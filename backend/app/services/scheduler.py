"""Slot planning, approval-window promotion, publish dispatch and catch-up.

All functions take a session already scoped to one organization (RLS). `run_org(org_id)` runs
everything for one tenant in its own transaction and then sends the collected notifications;
the worker's periodic task calls it for every active organization.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, set_tenant
from app.models import BrandProfile, Organization, Post, Schedule, SocialAccount
from app.services.content import (
    MANUAL_PLATFORMS,
    add_activity,
    now_utc,
    org_tz,
    platform_of,
)
from app.services.jobs import enqueue

log = logging.getLogger(__name__)


@dataclass
class Pending:
    """Notifications to send after the transaction commits."""

    items: list[tuple[str, str, str | None, str | None, str | None, list[dict] | None]] = field(default_factory=list)

    def add(self, event: str, title: str, body: str | None = None, *, link: str | None = None,
            permission: str | None = "content.view", actions: list[dict] | None = None) -> None:
        self.items.append((event, title, body, link, permission, actions))

    async def send(self, org_id: uuid.UUID) -> None:
        from app.services.notify import notify_org

        for event, title, body, link, perm, actions in self.items:
            try:
                await notify_org(org_id, event, title, body, link=link, permission=perm, actions=actions)
            except Exception:
                log.exception("notification %s failed", event)
        self.items.clear()


# ------------------------------------------------------------------ slot maths
def parse_hhmm(v: str) -> time | None:
    try:
        h, m = str(v).strip().split(":")
        hh, mm = int(h), int(m)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hh, mm)
    except (ValueError, AttributeError):
        pass
    return None


def slot_times(schedule: Schedule, tz: ZoneInfo, start: datetime, end: datetime,
               blackout: set[str] | None = None) -> list[datetime]:
    """Slot datetimes (UTC) with start < t <= end, in the schedule's days/times in `tz`."""
    days = {int(d) for d in (schedule.days_of_week or []) if str(d).lstrip("-").isdigit()}
    times = sorted({t for t in (parse_hhmm(x) for x in (schedule.times or [])) if t})
    if not days or not times or end <= start:
        return []
    blackout = blackout or set()
    every = max(1, int(getattr(schedule, "interval_days", 1) or 1))
    anchor: date | None = None
    if every > 1:
        try:
            anchor = date.fromisoformat(schedule.interval_start) if schedule.interval_start else None
        except ValueError:
            anchor = None
        if anchor is None:
            anchor = (schedule.created_at or start).astimezone(tz).date()
    out: list[datetime] = []
    d: date = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    while d <= last:
        on_cycle = anchor is None or (d - anchor).days % every == 0
        if on_cycle and d.weekday() in days and d.isoformat() not in blackout:
            for t in times:
                local = datetime.combine(d, t, tzinfo=tz)
                utc = local.astimezone(UTC)
                if start < utc <= end:
                    out.append(utc)
        d += timedelta(days=1)
    return sorted(set(out))


def account_ready(account: SocialAccount | None) -> bool:
    if account is None or account.status == "removed":
        return False
    return account.status in ("connected", "manual") or account.platform in MANUAL_PLATFORMS


async def blackout_dates(session: AsyncSession) -> set[str]:
    brand = await session.scalar(select(BrandProfile))
    return {str(d) for d in ((brand.blackout_dates if brand else None) or [])}


async def _lock_schedule(session: AsyncSession, schedule_id: uuid.UUID) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"sched:{schedule_id}"})


def slot_key(at: datetime) -> str:
    """Stable identifier of a schedule slot; kept in post.meta even if the post is moved."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def new_slot_post(schedule: Schedule, account: SocialAccount, at: datetime) -> Post:
    return Post(
        id=uuid.uuid4(), organization_id=schedule.organization_id, schedule_id=schedule.id,
        social_account_id=account.id, platform=account.platform, status="planned", mode=schedule.mode,
        scheduled_at=at, post_type="text", hashtags=[], media_ids=[], qa={}, meta={"slot_at": slot_key(at)},
        link_url=schedule.link_url if schedule.include_link else None,
    )


def monthly_limit(account: SocialAccount) -> int:
    try:
        return max(0, int((account.settings or {}).get("monthly_post_limit") or 0))
    except (TypeError, ValueError):
        return 0


def month_bounds(at: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    local = at.astimezone(tz)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.astimezone(UTC), end.astimezone(UTC)


async def posts_in_month(session: AsyncSession, account: SocialAccount, at: datetime, tz: ZoneInfo,
                         published_only: bool = False) -> int:
    """Posts for this account in the calendar month of `at` (org time): published, or planned to go out."""
    from sqlalchemy import func, or_

    start, end = month_bounds(at, tz)
    q = select(func.count()).select_from(Post).where(Post.social_account_id == account.id)
    if published_only:
        q = q.where(Post.status == "published", Post.published_at >= start, Post.published_at < end)
    else:
        q = q.where(Post.status.notin_(("skipped", "rejected", "failed", "missed", "removed")),
                    or_(Post.published_at.is_(None) & (Post.scheduled_at >= start) & (Post.scheduled_at < end),
                        (Post.published_at >= start) & (Post.published_at < end)))
    return int(await session.scalar(q) or 0)


async def plan_schedule(session: AsyncSession, schedule: Schedule, account: SocialAccount, tz: ZoneInfo,
                        now: datetime, blackout: set[str]) -> list[Post]:
    """Create planned posts for the schedule's slots within its lead time (idempotent)."""
    slots = slot_times(schedule, tz, now, now + timedelta(hours=max(1, schedule.lead_hours or 24)), blackout)
    if not slots:
        return []
    await _lock_schedule(session, schedule.id)
    keys = [slot_key(at) for at in slots]
    taken_times = set((await session.scalars(
        select(Post.scheduled_at).where(Post.schedule_id == schedule.id, Post.scheduled_at.in_(slots))
    )).all())
    # A post moved to another time still owns its original slot.
    taken_keys = set((await session.scalars(
        select(Post.meta["slot_at"].as_string()).where(Post.schedule_id == schedule.id, Post.meta["slot_at"].as_string().in_(keys))
    )).all())
    created = []
    limit = monthly_limit(account)
    for at in slots:
        if at in taken_times or slot_key(at) in taken_keys:
            continue
        if limit and await posts_in_month(session, account, at, tz) >= limit:
            continue  # the account's monthly post limit (e.g. to cap X API spend) is already used up
        post = new_slot_post(schedule, account, at)
        session.add(post)
        await session.flush()
        add_activity(session, post, "planned", f"Slot from schedule “{schedule.name}”")
        await enqueue(session, "post.generate", schedule.organization_id, {"post_id": str(post.id)},
                      dedupe_key=f"gen:{post.id}")
        created.append(post)
    return created


async def plan_all(session: AsyncSession, org: Organization, now: datetime | None = None) -> list[Post]:
    now = now or now_utc()
    tz = org_tz(org)
    blackout = await blackout_dates(session)
    rows = (await session.execute(
        select(Schedule, SocialAccount).join(SocialAccount, SocialAccount.id == Schedule.social_account_id)
        .where(Schedule.active.is_(True))
    )).all()
    created: list[Post] = []
    for schedule, account in rows:
        if not account_ready(account):
            continue
        created += await plan_schedule(session, schedule, account, tz, now, blackout)
    return created


async def next_free_slot(session: AsyncSession, schedule: Schedule, tz: ZoneInfo, now: datetime,
                         days: int = 60) -> datetime | None:
    blackout = await blackout_dates(session)
    slots = slot_times(schedule, tz, now, now + timedelta(days=days), blackout)
    if not slots:
        return None
    taken = set((await session.scalars(
        select(Post.scheduled_at).where(Post.schedule_id == schedule.id, Post.scheduled_at.in_(slots))
    )).all())
    return next((s for s in slots if s not in taken), None)


# ------------------------------------------------------------------ promotion, dispatch, catch-up
async def promote_windows(session: AsyncSession, now: datetime | None = None) -> list[Post]:
    """auto_with_window posts whose review window ended without a rejection become scheduled."""
    now = now or now_utc()
    rows = (await session.scalars(select(Post).where(
        Post.status == "in_review", Post.mode == "auto_with_window",
        Post.approval_deadline.is_not(None), Post.approval_deadline <= now,
    ))).all()
    for p in rows:
        p.status = "scheduled"
        add_activity(session, p, "auto_approved", "Review window ended without a rejection")
    return list(rows)


CATCH_UP_AFTER = timedelta(minutes=10)  # a post this late (or later) is "catching up"


async def _queued_until(session: AsyncSession, now: datetime) -> dict:
    """account id -> the latest time a publish job is already queued for (posts waiting their turn)."""
    from app.models import Job

    rows = (await session.execute(select(Job.payload, Job.run_after).where(
        Job.kind == "post.publish", Job.status == "queued", Job.run_after > now))).all()
    ids = {str((pl or {}).get("post_id")): at for pl, at in rows if (pl or {}).get("post_id")}
    if not ids:
        return {}
    out: dict = {}
    for pid, acc in (await session.execute(select(Post.id, Post.social_account_id).where(
            Post.id.in_([uuid.UUID(i) for i in ids])))).all():
        at = ids.get(str(pid))
        if acc is not None and at is not None and (acc not in out or at > out[acc]):
            out[acc] = at
    return out


async def _active_publish(session: AsyncSession) -> set[str]:
    from app.models import Job

    keys = (await session.scalars(select(Job.dedupe_key).where(
        Job.kind == "post.publish", Job.status.in_(["queued", "running"]), Job.dedupe_key.is_not(None)))).all()
    return {k[4:] for k in keys if k and k.startswith("pub:")}


async def dispatch_and_catch_up(session: AsyncSession, tz: ZoneInfo, pending: Pending,
                                now: datetime | None = None, org: Organization | None = None) -> dict:
    """Queue publishing for every due post. Nothing is ever dropped: posts that fell due while the laptop was
    off, asleep or offline go out now, one at a time per account (`catch_up_gap_minutes` apart, default 20), and
    one friendly summary says what is going out when. Greetings for a day that has already passed go back to
    review instead of posting late on their own."""
    from app.services import uptime

    now = now or now_utc()
    gap = timedelta(minutes=uptime.publishing_settings(org)["catch_up_gap_minutes"])
    queued = held = 0
    late: list[tuple[Post, datetime]] = []
    kept: list[Post] = []

    due = (await session.scalars(select(Post).where(
        Post.status.in_(["scheduled", "approved"]), Post.scheduled_at.is_not(None), Post.scheduled_at <= now,
    ).order_by(Post.scheduled_at))).all()
    if due:
        active = await _active_publish(session)
        busy = await _queued_until(session, now)
        next_free: dict = {}
        for p in due:
            if str(p.id) in active:
                continue  # already queued (perhaps waiting its turn)
            meta = dict(p.meta or {})
            if meta.get("occasion_id") and p.scheduled_at.astimezone(tz).date() < now.astimezone(tz).date():
                p.status, p.error = "in_review", None
                p.meta = {**meta, "held_reason": "occasion_passed"}
                add_activity(session, p, "held_for_review",
                             f"Its day ({_local(p.scheduled_at, tz)}) passed while the app was off, so it waits for you "
                             "instead of posting late on its own.")
                kept.append(p)
                held += 1
                continue
            acc = p.social_account_id
            if acc not in next_free:
                next_free[acc] = (busy[acc] + gap) if acc in busy else now
            at = max(now, next_free[acc])
            next_free[acc] = at + gap
            await enqueue(session, "post.publish", p.organization_id, {"post_id": str(p.id)},
                          dedupe_key=f"pub:{p.id}", priority=2, run_after=at)
            queued += 1
            if at > now or now - p.scheduled_at >= CATCH_UP_AFTER:
                p.meta = {**meta, "catch_up_at": at.isoformat()}
                add_activity(session, p, "catching_up",
                             f"Was due {_local(p.scheduled_at, tz)}; posting {'now' if at <= now else 'at ' + _local(at, tz)}.")
                late.append((p, at))

    # approve-first posts still waiting for approval after their time: they simply wait (one gentle reminder)
    waiting = (await session.scalars(select(Post).where(
        Post.status == "in_review", Post.mode == "approve_first", Post.scheduled_at.is_not(None),
        Post.scheduled_at <= now,
    ))).all()
    for p in waiting:
        meta = dict(p.meta or {})
        if meta.get("overdue_reminded") or meta.get("held_reason"):
            continue
        p.meta = {**meta, "overdue_reminded": True}
        pending.add("post_review", f"A {platform_of(p.platform).label} post is waiting for your approval",
                    f"“{_snippet(p)}” was planned for {_local(p.scheduled_at, tz)}. Approve it and it goes out "
                    "straight away, or pick a new time.",
                    link=f"/posts/{p.id}", permission="content.approve",
                    actions=[{"label": "Approve", "data": f"ap:{p.id}"}, {"label": "Reject", "data": f"rj:{p.id}"}])

    if late or kept:
        await _catch_up_summary(pending, tz, now, late, kept)
    return {"queued": queued, "catching_up": len(late), "held": held}


async def _catch_up_summary(pending: Pending, tz: ZoneInfo, now: datetime, late: list[tuple[Post, datetime]],
                            kept: list[Post]) -> None:
    from app.services import uptime

    since = min([p.scheduled_at for p, _ in late] + [p.scheduled_at for p in kept])
    try:
        periods = await uptime.recent_downtime(since - timedelta(hours=1))
    except Exception:  # noqa: BLE001
        periods = []
    lines = []
    if periods:
        lines.append("The app was " + uptime.describe(periods, lambda d: _local(d, tz)) + ".")
    if late:
        last = max(at for _, at in late)
        lines.append(f"Posting {len(late)} late post{'s' if len(late) != 1 else ''} gently, one at a time per account"
                     + (f" (all out by {_local(last, tz)})" if last > now else "") + ":")
        for p, at in late[:15]:
            lines.append(f"• {platform_of(p.platform).label}: “{_snippet(p, 70)}” "
                         f"{'now' if at <= now else 'at ' + _local(at, tz)}")
        if len(late) > 15:
            lines.append(f"• …and {len(late) - 15} more")
    if kept:
        lines.append("Waiting for you (their day has passed, so they won't post on their own):")
        for p in kept[:10]:
            lines.append(f"• {platform_of(p.platform).label}: “{_snippet(p, 70)}”. Approve to post it anyway, or edit it.")
    title = "Welcome back: catching up" if periods else "Catching up on late posts"
    pending.add("catch_up", title, "\n".join(lines), link="/posts")


def _snippet(p: Post, n: int = 90) -> str:
    s = (p.title or p.text or p.topic or "Untitled post").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _local(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%d %b %H:%M") if dt else "—"


async def run_org(org_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Planning + promotion + dispatch + catch-up for one organization."""
    now = now or now_utc()
    pending = Pending()
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return {}
        tz = org_tz(org)
        created = await plan_all(s, org, now)
        promoted = await promote_windows(s, now)
        result = await dispatch_and_catch_up(s, tz, pending, now, org)
        await s.commit()
    await pending.send(org_id)
    return {"planned": len(created), "promoted": len(promoted), **result}
