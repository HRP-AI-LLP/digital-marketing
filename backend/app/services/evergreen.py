"""Evergreen recycling: give good, timeless posts a second life.

Posts marked evergreen (Post.evergreen) are reshared on the same account when the organization turns recycling on
(Organization.publishing["evergreen"]). Each run picks the best-performing published evergreen post that hasn't
been reshared for `min_days_between` days, asks the AI (task "write") for a fresh version that keeps every fact
and link but opens with a new hook, carries the media over and creates a NEW post. It goes through the normal
approval rules (content.route, including organization auto-approval) into the account's next free schedule slot,
or the next best time when the account has no schedule. The new post records meta {"recycled_from": <original id>}.

At most `per_week` reshares go out in any 7 days, spread out (one every 7/per_week days).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, Post, PostMetric, Schedule, SocialAccount
from app.services import content as svc
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)

DEFAULTS = {"enabled": False, "min_days_between": 60, "per_week": 2, "accounts": [], "mode": "approve_first"}
MODES = ("approve_first", "auto_with_window", "full_auto", "manual")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def settings(org: Organization | None) -> dict:
    raw = dict(((org.publishing if org is not None else None) or {}).get("evergreen") or {})
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    try:
        out["min_days_between"] = max(7, min(365, int(out["min_days_between"])))
    except (TypeError, ValueError):
        out["min_days_between"] = DEFAULTS["min_days_between"]
    try:
        out["per_week"] = max(1, min(14, int(out["per_week"])))
    except (TypeError, ValueError):
        out["per_week"] = DEFAULTS["per_week"]
    out["accounts"] = [str(a) for a in (out.get("accounts") or []) if svc.as_uuid(a)]
    out["enabled"] = bool(out.get("enabled"))
    if out.get("mode") not in MODES:
        out["mode"] = DEFAULTS["mode"]
    return out


def save_settings(org: Organization, patch: dict) -> dict:
    cur = settings(org)
    cur.update({k: v for k, v in patch.items() if k in DEFAULTS and v is not None})
    pub = dict(org.publishing or {})
    pub["evergreen"] = cur
    org.publishing = pub
    return settings(org)


def root_id(p: Post) -> str:
    return str((p.meta or {}).get("recycled_from") or p.id)


def perf(m: PostMetric | None) -> tuple[int, float, int]:
    """Sort key: posts with results first, then engagement rate (when impressions are known), then engagements."""
    if m is None:
        return (0, 0.0, 0)
    eng = int(m.likes + m.comments + m.shares + m.saves + m.clicks)
    imp = int(m.impressions or 0)
    return (1, eng / imp if imp else 0.0, eng)


async def _latest_metrics(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, PostMetric]:
    if not ids:
        return {}
    rows = (await session.scalars(select(PostMetric).where(PostMetric.post_id.in_(ids))
                                  .distinct(PostMetric.post_id)
                                  .order_by(PostMetric.post_id, PostMetric.fetched_at.desc()))).all()
    return {m.post_id: m for m in rows}


async def recycled_posts(session: AsyncSession, since: datetime | None = None) -> list[Post]:
    q = select(Post).where(Post.meta["recycled_from"].as_string().is_not(None))
    if since is not None:
        q = q.where(Post.created_at >= since)
    return list((await session.scalars(q.order_by(Post.created_at.desc()))).all())


@dataclass
class Candidate:
    post: Post
    account: SocialAccount
    metric: PostMetric | None
    last_shared: datetime | None
    eligible: bool
    reason: str | None = None


async def candidates(session: AsyncSession, org: Organization, now: datetime | None = None) -> list[Candidate]:
    """Every evergreen post, best first, with whether it can be reshared now (and why not)."""
    from app.services.scheduler import account_ready

    now = now or svc.now_utc()
    cfg = settings(org)
    gap = timedelta(days=cfg["min_days_between"])
    posts = list((await session.scalars(select(Post).where(Post.evergreen.is_(True), Post.status == "published"))).all())
    if not posts:
        return []
    accounts = {a.id: a for a in (await session.scalars(select(SocialAccount))).all()}
    mets = await _latest_metrics(session, [p.id for p in posts])
    last: dict[str, datetime] = {}
    for r in await recycled_posts(session):
        k = str((r.meta or {}).get("recycled_from"))
        when = r.created_at
        if when and (k not in last or when > last[k]):
            last[k] = when
    allowed = set(cfg["accounts"])
    out = []
    for p in posts:
        acc = accounts.get(p.social_account_id)
        if acc is None:
            continue
        shared = max([t for t in (p.published_at, last.get(root_id(p))) if t is not None], default=None)
        reason = None
        if allowed and str(acc.id) not in allowed:
            reason = "Recycling is off for this account."
        elif not account_ready(acc):
            reason = f"{acc.name} isn't connected."
        elif shared is not None and now - shared < gap:
            reason = f"Shared {max(0, (now - shared).days)} days ago; waits {cfg['min_days_between']} days."
        out.append(Candidate(p, acc, mets.get(p.id), shared, reason is None, reason))
    out.sort(key=lambda c: (c.eligible, perf(c.metric)), reverse=True)
    return out


async def due(session: AsyncSession, org: Organization, now: datetime | None = None) -> tuple[bool, str]:
    now = now or svc.now_utc()
    cfg = settings(org)
    if not cfg["enabled"]:
        return False, "Evergreen recycling is off."
    recent = await recycled_posts(session, now - timedelta(days=7))
    if len(recent) >= cfg["per_week"]:
        return False, f"Already reshared {len(recent)} posts this week."
    spacing = timedelta(hours=168 / cfg["per_week"] * 0.9)
    if recent and recent[0].created_at and now - recent[0].created_at < spacing:
        return False, "The last reshare was recent; the next one waits to spread them out."
    return True, ""


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM_RE.findall(text or "")}


def check_facts(original: str, new: str) -> list[str]:
    """Problems that mean a person should look before the reshare goes out."""
    issues = []
    extra = _numbers(new) - _numbers(original)
    if extra:
        issues.append("the new version mentions numbers that aren't in the original (" + ", ".join(sorted(extra)[:5]) + ")")
    missing = [u for u in _URL_RE.findall(original or "") if u.rstrip(".,!?") not in (new or "")]
    if missing:
        issues.append("a link from the original is missing")
    return issues


async def rewrite(session: AsyncSession, src: Post, brand) -> dict:
    cfg = svc.GenSettings(mode="approve_first")
    ctx = await svc.company_context(session)
    system = svc.build_system_prompt(brand, src.platform, src.post_type, cfg, ctx, [], [])
    system += ("\n\nYou are refreshing one of the company's earlier posts so it can be shared again. Keep every fact, "
               "name, number and link exactly as in the original; do not add new facts and never invent numbers. "
               "Change the opening hook and the wording so it reads as new, and keep a similar length.")
    original = svc.full_caption(src.text, src.hashtags)
    prompt = (f"Original post ({svc.platform_of(src.platform).label}):\n{original[:4000]}"
              + (f"\n\nTitle: {src.title}" if src.title else "")
              + (f"\n\nLink: {src.link_url}" if src.link_url else "")
              + "\n\nWrite the refreshed version.")
    raw = await llm_router.complete_json(session, "write", prompt, system=system, temperature=0.7, max_tokens=2000)
    return svc.normalize_draft(raw, src.platform, src.post_type)


async def _slot(session: AsyncSession, org: Organization, account: SocialAccount, now: datetime):
    """(schedule, time) for the reshare: the account's next free schedule slot, else its next best time."""
    from app.services import best_time, scheduler

    tz = svc.org_tz(org)
    schedules = (await session.scalars(select(Schedule).where(
        Schedule.social_account_id == account.id, Schedule.active.is_(True)).order_by(Schedule.created_at))).all()
    soonest = None
    for s in schedules:
        at = await scheduler.next_free_slot(session, s, tz, now + timedelta(minutes=30))
        if at is not None and (soonest is None or at < soonest[1]):
            soonest = (s, at)
    if soonest:
        return soonest
    slots = await best_time.next_slots(session, account, tz, now=now, count=1, days=7,
                                       blackout=await scheduler.blackout_dates(session))
    return (None, datetime.fromisoformat(slots[0]["at"])) if slots else (None, None)


async def recycle(session: AsyncSession, org: Organization, cand: Candidate, now: datetime | None = None) -> Post:
    """Create the fresh copy of `cand.post`. Caller commits (and notifies reviewers when it is in review)."""
    from app.services import scheduler

    now = now or svc.now_utc()
    src, account = cand.post, cand.account
    brand = await svc.get_brand(session, org.id)
    draft = await rewrite(session, src, brand)
    draft = svc.deterministic_fixes(draft, account.platform, brand, link_url=None, post_type=src.post_type)
    checks = svc.run_checks(draft, account.platform, brand, link_url=None, post_type=src.post_type)
    hold = [f"some quality checks fail ({', '.join(c['name'].replace('_', ' ') for c in svc.hard_failures(checks))})"] \
        if svc.hard_failures(checks) else []
    hold += check_facts(svc.full_caption(src.text, src.hashtags) + " " + (src.link_url or ""),
                        svc.full_caption(draft["text"], draft.get("hashtags")) + " " + (src.link_url or ""))
    schedule, at = await _slot(session, org, account, now)
    if schedule is not None:
        await scheduler._lock_schedule(session, schedule.id)
    cfg = settings(org)
    keep = {k: (src.meta or {}).get(k) for k in ("image_brief", "alt_text", "slides", "video_script", "visual")
            if (src.meta or {}).get(k)}
    post = Post(
        id=uuid.uuid4(), organization_id=org.id, social_account_id=account.id, platform=account.platform,
        schedule_id=schedule.id if schedule else None, pillar_id=src.pillar_id, topic=src.topic,
        post_type=src.post_type, status="planned", mode=schedule.mode if schedule else cfg["mode"],
        scheduled_at=at, title=draft.get("title") or src.title, text=draft["text"],
        hashtags=draft.get("hashtags") or list(src.hashtags or []), link_url=src.link_url,
        first_comment=src.first_comment, media_ids=list(src.media_ids or []),
        qa={"checks": [{k: c[k] for k in ("name", "ok", "detail", "hard")} for c in checks],
            "passed": not svc.hard_failures(checks), "score": None, "checked_at": now.isoformat()},
        meta={**keep, "source": "evergreen", "recycled_from": root_id(src), "keep_media": True,
              **({"slot_at": scheduler.slot_key(at)} if schedule and at else {})},
    )
    session.add(post)
    await session.flush()
    if hold:
        svc.add_warning(post, "Check before it goes out: " + "; ".join(hold))
    svc.route(post, svc.settings_for(post, schedule), hold_reason="; ".join(hold) or None, now=now,
              auto_approve=svc.auto_approve_posts(org))
    when = src.published_at.astimezone(svc.org_tz(org)).strftime("%d %b %Y") if src.published_at else "earlier"
    svc.add_activity(session, post, "recycled", f"Fresh version of an evergreen post first shared {when}"
                     + ("" if at else "; pick a time for it"))
    svc.add_activity(session, src, "reshared", "A fresh version was created for resharing")
    return post


async def run(session: AsyncSession, org: Organization, now: datetime | None = None, *, force: bool = False) -> dict:
    """One recycling step for an organization: reshare the best eligible post when one is due."""
    now = now or svc.now_utc()
    if not force:
        ok, why = await due(session, org, now)
        if not ok:
            return {"created": None, "reason": why}
    pick = next((c for c in await candidates(session, org, now) if c.eligible), None)
    if pick is None:
        return {"created": None, "reason": "No evergreen post is ready to reshare yet."}
    post = await recycle(session, org, pick, now)
    return {"created": post, "reason": None}


async def run_org(org_id: uuid.UUID, now: datetime | None = None) -> dict:
    from app.core.db import SessionLocal, set_tenant

    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active" or not settings(org)["enabled"]:
            return {"created": None}
        try:
            res = await run(s, org, now)
        except llm_router.NoProviderError:
            await s.rollback()
            return {"created": None, "reason": "Add an AI text model in Settings → Integrations."}
        except ValueError as e:  # the model returned nothing usable
            await s.rollback()
            log.warning("evergreen rewrite failed for org %s: %s", org_id, e)
            return {"created": None, "reason": str(e)}
        post = res["created"]
        await s.commit()
        if post is not None and post.status == "in_review":
            await svc.notify_review(post, svc.org_tz(org))
        return {"created": str(post.id) if post else None, "reason": res["reason"]}
