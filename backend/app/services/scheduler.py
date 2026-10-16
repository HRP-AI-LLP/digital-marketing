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
    DEFAULT_GRACE_HOURS,
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
    out: list[datetime] = []
    d: date = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    while d <= last:
        if d.weekday() in days and d.isoformat() not in blackout:
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
    for at in slots:
        if at in taken_times or slot_key(at) in taken_keys:
            continue
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
async def _grace_hours(session: AsyncSession, post: Post, cache: dict) -> int:
    if not post.schedule_id:
        return DEFAULT_GRACE_HOURS
    if post.schedule_id not in cache:
        s = await session.get(Schedule, post.schedule_id)
        cache[post.schedule_id] = s.catch_up_grace_hours if s and s.catch_up_grace_hours is not None else DEFAULT_GRACE_HOURS
    return cache[post.schedule_id]


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


async def dispatch_and_catch_up(session: AsyncSession, tz: ZoneInfo, pending: Pending,
                                now: datetime | None = None) -> dict:
    """Queue publishing for due posts; mark posts that are too late as missed."""
    now = now or now_utc()
    cache: dict = {}
    queued = missed = 0

    due = (await session.scalars(select(Post).where(
        Post.status.in_(["scheduled", "approved"]), Post.scheduled_at.is_not(None), Post.scheduled_at <= now,
    ))).all()
    for p in due:
        grace = await _grace_hours(session, p, cache)
        if now - p.scheduled_at <= timedelta(hours=grace):
            await enqueue(session, "post.publish", p.organization_id, {"post_id": str(p.id)}, dedupe_key=f"pub:{p.id}",
                          priority=2)
            queued += 1
        else:
            _mark_missed(session, p, f"The app wasn't running within {grace} h of the posting time.")
            pending.add("post_failed", f"Missed a {platform_of(p.platform).label} post",
                        f"“{_snippet(p)}” was due {_local(p.scheduled_at, tz)} and is more than {grace} h late. "
                        "Pick a new time and approve it to post it.", link=f"/posts/{p.id}")
            missed += 1

    # approve-first posts nobody approved before the slot
    waiting = (await session.scalars(select(Post).where(
        Post.status == "in_review", Post.mode == "approve_first", Post.scheduled_at.is_not(None),
        Post.scheduled_at <= now,
    ))).all()
    for p in waiting:
        _mark_missed(session, p, "Nobody approved it before its posting time.")
        grace = await _grace_hours(session, p, cache)
        pending.add("post_review", f"A {platform_of(p.platform).label} post missed its slot",
                    f"“{_snippet(p)}” wasn't approved before {_local(p.scheduled_at, tz)}. "
                    f"Approve it within {grace} h to post it now, or pick a new time.",
                    link=f"/posts/{p.id}", permission="content.approve",
                    actions=[{"label": "Approve", "data": f"ap:{p.id}"}, {"label": "Reject", "data": f"rj:{p.id}"}])
        missed += 1

    # posts that never finished generating
    stuck = (await session.scalars(select(Post).where(
        Post.status.in_(["planned", "generating"]), Post.scheduled_at.is_not(None), Post.scheduled_at <= now,
    ))).all()
    for p in stuck:
        grace = await _grace_hours(session, p, cache)
        if now - p.scheduled_at > timedelta(hours=grace):
            _mark_missed(session, p, "The post wasn't ready before its posting time.")
            missed += 1
    return {"queued": queued, "missed": missed}


def _mark_missed(session: AsyncSession, p: Post, why: str) -> None:
    p.status = "missed"
    p.error = why
    add_activity(session, p, "missed", why)


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
        result = await dispatch_and_catch_up(s, tz, pending, now)
        await s.commit()
    await pending.send(org_id)
    return {"planned": len(created), "promoted": len(promoted), **result}
