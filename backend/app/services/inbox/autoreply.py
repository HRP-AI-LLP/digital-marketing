"""AI replies to comments: drafts for a person to approve, or fully automatic replies, with guard rails.

Settings live in Organization.publishing["inbox_autoreply"]:
  {enabled, mode: "draft" | "auto", platforms: [..] (empty = every platform that can reply), emoji: "none" | "light"
   | "rich" | "brand", sentiments: ["positive", "neutral", "question"], delay_minutes, max_per_day, instructions,
   started_at}

Guard rails (always on):
  - negative or urgent comments are never answered automatically: they stay for a person (and notify as before);
  - only comments that arrived after auto-replies were switched on, and not older than 3 days;
  - never our own items, never twice to the same comment, at most 2 automatic replies to one person per thread;
  - waits `delay_minutes` after the comment (so it doesn't look instant) and stops at `max_per_day` per day.
The result of every decision is kept on the item: raw["auto_reply"] = {state: drafted|sent|skipped|failed, ...}.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Organization, Post
from app.models.inbox import InboxItem
from app.services.inbox.base import CAPABILITIES

log = logging.getLogger("inbox.autoreply")

DEFAULTS = {"enabled": False, "mode": "draft", "platforms": [], "emoji": "rich",
            "sentiments": ["positive", "neutral", "question"], "delay_minutes": 5, "max_per_day": 30,
            "instructions": "", "started_at": None}
MAX_AGE = timedelta(days=3)
PER_PERSON_PER_THREAD = 2
BATCH = 15


def settings_of(org: Organization | None) -> dict:
    cfg = dict(((org.publishing if org is not None else None) or {}).get("inbox_autoreply") or {})
    out = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
    out["mode"] = out["mode"] if out["mode"] in ("draft", "auto") else "draft"
    out["emoji"] = out["emoji"] if out["emoji"] in ("none", "light", "rich", "brand") else "rich"
    out["delay_minutes"] = max(0, min(720, int(out.get("delay_minutes") or 0)))
    out["max_per_day"] = max(1, min(500, int(out.get("max_per_day") or 30)))
    out["sentiments"] = [s for s in out.get("sentiments") or [] if s in ("positive", "neutral", "question")]
    return out


def save_settings(org: Organization, patch: dict) -> dict:
    cur = settings_of(org)
    was_on = cur["enabled"]
    cur.update({k: v for k, v in patch.items() if k in DEFAULTS and k != "started_at"})
    if cur["enabled"] and (not was_on or not cur.get("started_at")):
        cur["started_at"] = datetime.now(UTC).isoformat()  # never reply to the backlog from before
    pub = dict(org.publishing or {})
    pub["inbox_autoreply"] = cur
    org.publishing = pub
    flag_modified(org, "publishing")
    return settings_of(org)


def replyable_platforms() -> list[str]:
    from app.services.inbox.sync import REPLIERS

    return [p for p, c in CAPABILITIES.items() if c.reply and p in REPLIERS]


def _mark(item: InboxItem, **info) -> None:
    raw = dict(item.raw or {})
    raw["auto_reply"] = {**info, "at": datetime.now(UTC).isoformat()}
    item.raw = raw
    flag_modified(item, "raw")


def state_of(item: InboxItem) -> dict | None:
    return (item.raw or {}).get("auto_reply")


async def _sent_today(session: AsyncSession) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    rows = (await session.scalars(select(InboxItem.raw).where(
        InboxItem.is_own.is_(False), InboxItem.replied_at >= since))).all()
    return sum(1 for r in rows if ((r or {}).get("auto_reply") or {}).get("state") == "sent")


async def _replies_to_person(session: AsyncSession, item: InboxItem) -> int:
    if not item.thread_key or not item.author_external_id and not item.author_handle:
        return 0
    others = (await session.scalars(select(InboxItem).where(
        InboxItem.social_account_id == item.social_account_id, InboxItem.thread_key == item.thread_key,
        InboxItem.is_own.is_(False), InboxItem.id != item.id))).all()
    same = [o for o in others if (o.author_external_id and o.author_external_id == item.author_external_id)
            or (o.author_handle and o.author_handle == item.author_handle)]
    return sum(1 for o in same if (state_of(o) or {}).get("state") == "sent")


def _why_not(item: InboxItem, cfg: dict, now: datetime, started: datetime | None) -> str | None:
    if item.is_own or item.source != "account":
        return "own"
    if item.reply_text or item.status in ("replied", "archived"):
        return "answered"
    if item.platform not in replyable_platforms():
        return "platform can't reply"
    if cfg["platforms"] and item.platform not in cfg["platforms"]:
        return "platform off"
    when = item.published_at or item.created_at
    if started and item.created_at and item.created_at < started:
        return "before auto-replies were on"
    if when and now - when > MAX_AGE:
        return "too old"
    if item.priority or item.sentiment == "negative":
        return "needs a person"
    if item.sentiment and item.sentiment not in cfg["sentiments"]:
        return "sentiment off"
    if not (item.text or "").strip():
        return "empty"
    return None


def _pick(options: list[str], item: InboxItem) -> str | None:
    if not options:
        return None
    # questions get the option that answers; everything else the short, friendly one
    return options[1] if item.sentiment == "question" and len(options) > 1 else options[0]


async def process(session: AsyncSession, org_id: uuid.UUID) -> dict:
    """Draft or send AI replies to new comments. Commits as it goes. Returns counts."""
    org = await session.get(Organization, org_id)
    cfg = settings_of(org)
    out = {"drafted": 0, "sent": 0, "skipped": 0, "failed": 0}
    if not cfg["enabled"]:
        return out
    from app.services.inbox.suggest import suggest_replies
    from app.services.inbox.sync import InboxActionError, send_reply, thread_of

    now = datetime.now(UTC)
    started = datetime.fromisoformat(cfg["started_at"]) if cfg.get("started_at") else None
    ready = now - timedelta(minutes=cfg["delay_minutes"])
    allowed = [p for p in replyable_platforms() if not cfg["platforms"] or p in cfg["platforms"]]
    if not allowed:
        return out
    since = max(now - MAX_AGE, started) if started else now - MAX_AGE
    state = func.coalesce(InboxItem.raw["auto_reply"]["state"].as_string(), "")
    tries = func.coalesce(InboxItem.raw["auto_reply"]["tries"].as_string(), "0")
    # only candidates in SQL (platform, age, not yet decided), so comments that can never be answered here don't
    # fill the batch and starve new ones
    rows = (await session.scalars(select(InboxItem).where(
        InboxItem.source == "account", InboxItem.is_own.is_(False), InboxItem.reply_text.is_(None),
        InboxItem.status.in_(["new", "read"]), InboxItem.kind.in_(["comment", "reply", "mention"]),
        InboxItem.platform.in_(allowed), InboxItem.created_at >= since,
        func.coalesce(InboxItem.published_at, InboxItem.created_at) <= ready,
        InboxItem.organization_id == org_id, state.notin_(["drafted", "sent", "skipped"]),
        ~((state == "failed") & (tries.in_(["2", "3", "4", "5"]))),
    ).order_by(InboxItem.created_at.asc()).limit(BATCH * 3))).all()
    budget = cfg["max_per_day"] - await _sent_today(session) if cfg["mode"] == "auto" else BATCH
    done = 0
    for item in rows:
        st = state_of(item) or {}
        if st.get("state") in ("drafted", "sent", "skipped") or (st.get("state") == "failed" and st.get("tries", 0) >= 2):
            continue
        why = _why_not(item, cfg, now, started)
        if why:
            if why in ("needs a person", "too old", "before auto-replies were on", "sentiment off", "empty"):
                _mark(item, state="skipped", reason=why)
                out["skipped"] += 1
            continue
        if done >= BATCH or (cfg["mode"] == "auto" and budget <= 0):
            break
        if await _replies_to_person(session, item) >= PER_PERSON_PER_THREAD:
            _mark(item, state="skipped", reason="already answered this person twice here")
            out["skipped"] += 1
            continue
        post = await session.get(Post, item.post_id) if item.post_id else None
        emoji = None if cfg["emoji"] == "brand" else cfg["emoji"]
        extra = ("Write it as the company, sounding like a real person. Keep it short (one or two sentences). "
                 + (cfg["instructions"] or ""))
        try:
            options = await suggest_replies(session, item, await thread_of(session, item), post.text if post else None,
                                            emoji=emoji, extra=extra)
        except Exception as e:  # noqa: BLE001 - no AI model: try again next run
            log.info("auto-reply suggestion failed for %s: %s", item.id, e)
            _mark(item, state="failed", reason=f"AI writer unavailable: {str(e)[:160]}", tries=st.get("tries", 0) + 1)
            out["failed"] += 1
            await session.commit()
            continue
        text = _pick(options, item)
        if not text:
            _mark(item, state="failed", reason="The AI didn't write a usable reply.", tries=st.get("tries", 0) + 1)
            out["failed"] += 1
            continue
        item.ai_suggestions = options
        done += 1
        if cfg["mode"] == "draft":
            _mark(item, state="drafted", text=text)
            out["drafted"] += 1
        else:
            try:
                await send_reply(session, org_id, item, text, None)
                _mark(item, state="sent", text=text)
                from app.services import audit

                label = CAPABILITIES[item.platform].label if item.platform in CAPABILITIES else item.platform
                audit.record(session, action="inbox.auto_reply", organization_id=org_id, entity_type="inbox_item",
                             entity_id=item.id, summary=f"AI replied on {label} to {item.author_name or 'a comment'}",
                             details={"platform": item.platform, "comment": (item.text or "")[:300], "reply": text})
                out["sent"] += 1
                budget -= 1
            except InboxActionError as e:
                _mark(item, state="failed", reason=e.message[:300], text=text, tries=st.get("tries", 0) + 1)
                out["failed"] += 1
        await session.commit()
    await session.commit()
    if out["drafted"]:
        try:
            from app.services.notify import notify_org

            n = out["drafted"]
            await notify_org(org_id, "inbox_new", f"{n} AI repl{'y' if n == 1 else 'ies'} ready to approve",
                             "Open the inbox, check the drafts and press Send (or edit them first).",
                             link="/inbox?drafts=1", permission="inbox.view")
        except Exception:  # noqa: BLE001
            log.exception("auto-reply notification failed")
    return out
