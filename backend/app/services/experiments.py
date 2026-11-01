"""A/B tests: 2–3 variants of a post, compared on one metric after their results are in.

Modes
- time:     one account, the variants go out at comparable times (same weekday and hour on consecutive weeks by
            default, or a few days apart)
- accounts: the same post on different accounts of one platform, at the same time

`evaluate(session, exp)` runs from a periodic task. Once every variant is published and `evaluate_after_days` have
passed since the last one went out, it compares the latest metrics and records the leader, the numbers and an honest
label for how big the gap is. One post per variant is a tiny sample, so the wording never claims statistical
significance: a gap is called "clear" only when it is large relative to the counts involved.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Experiment, ExperimentPost, Organization, Post, PostMetric, SocialAccount
from app.services import content as svc
from app.services.bulk_import import place
from app.services.llm import router as llm_router
from app.services.platforms import supported_types

log = logging.getLogger(__name__)

METRICS = {"engagement_rate": "Engagement rate", "clicks": "Link clicks", "impressions": "Impressions"}
MODES = {"time": "Same account, comparable times", "accounts": "Different accounts on the same platform"}
LABELS = ["A", "B", "C"]
DEFAULT_DAYS = 3
NO_DATA_GRACE_DAYS = 7  # after the evaluation date, how long to wait for metrics before giving up
PUBLISH_GIVE_UP_DAYS = 45  # a variant that still isn't published this long after the start ends the test
TERMINAL = {"rejected", "skipped", "removed"}
MIN_RELATIVE = 0.10  # gaps under 10% are "too close to call"
MIN_IMPRESSIONS = 200  # fewer impressions than this in total: never more than a hint
CLEAR_Z = 2.0


class ExperimentError(Exception):
    """Shown to the user."""


# ------------------------------------------------------------------ helpers
def eng(m: PostMetric | None) -> int:
    if m is None:
        return 0
    return int((m.likes or 0) + (m.comments or 0) + (m.shares or 0) + (m.saves or 0) + (m.clicks or 0))


async def latest_metrics(session: AsyncSession, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, PostMetric]:
    if not post_ids:
        return {}
    rows = (await session.scalars(
        select(PostMetric).where(PostMetric.post_id.in_(post_ids))
        .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc())
    )).all()
    return {m.post_id: m for m in rows}


def plan_times(first: datetime, n: int, spacing_days: int = 7) -> list[datetime]:
    """Comparable slots: the same time of day, `spacing_days` apart (7 = same weekday on consecutive weeks)."""
    return [first + timedelta(days=spacing_days * i) for i in range(n)]


def metric_value(metric: str, m: PostMetric | None) -> float | None:
    if m is None:
        return None
    if metric == "engagement_rate":
        return round(eng(m) / m.impressions, 6) if m.impressions else None
    if metric == "clicks":
        return float(m.clicks or 0)
    return float(m.impressions or 0)


def fmt_value(metric: str, v: float | None) -> str:
    if v is None:
        return "no data"
    if metric == "engagement_rate":
        return f"{v * 100:.2f}%"
    return f"{int(v):,}"


# ------------------------------------------------------------------ the comparison
@dataclass
class Verdict:
    verdict: str  # clear | slight | tie | no_data
    leader: int | None  # index into the variants list
    difference_pct: float | None
    gap_score: float | None  # how large the gap is relative to the counts (a z-like ratio, shown as a guide only)
    message: str


def compare(metric: str, rows: list[dict]) -> Verdict:
    """rows: [{label, value, impressions, engagements, clicks}] (value None = no data)."""
    have = [(i, r) for i, r in enumerate(rows) if r.get("value") is not None]
    if len(have) < 2:
        return Verdict("no_data", None, None, None, "Not enough results to compare.")
    have.sort(key=lambda x: x[1]["value"], reverse=True)
    (li, lead), (_, second) = have[0], have[1]
    lv, sv = float(lead["value"]), float(second["value"])
    if lv == sv:
        return Verdict("tie", None, 0.0, 0.0, "A tie: the variants scored exactly the same.")
    rel = (lv - sv) / sv if sv > 0 else math.inf
    diff_pct = None if math.isinf(rel) else round(rel * 100, 1)

    if metric == "engagement_rate":  # gap between two proportions relative to its spread
        n1, n2 = max(int(lead["impressions"]), 1), max(int(second["impressions"]), 1)
        p = (lead["engagements"] + second["engagements"]) / (n1 + n2)
        se = math.sqrt(max(p * (1 - p), 1e-12) * (1 / n1 + 1 / n2))
        z = (lv - sv) / se if se else 0.0
    else:  # counts: gap relative to the square root of the total
        z = (lv - sv) / math.sqrt(max(lv + sv, 1.0))
    z = round(z, 2)
    total_imp = sum(int(r.get("impressions") or 0) for _, r in have)
    gap = "more than double" if math.isinf(rel) or rel >= 1 else f"{diff_pct:g}% higher"
    name = METRICS[metric].lower()
    head = f"Variant {lead['label']} had the best {name}: {fmt_value(metric, lv)} vs {fmt_value(metric, sv)} for {second['label']} ({gap})."
    if rel < MIN_RELATIVE:
        return Verdict("tie", li, diff_pct, z, f"{head} That's too close to call: treat the variants as performing about the same.")
    if z >= CLEAR_Z and total_imp >= MIN_IMPRESSIONS:
        return Verdict("clear", li, diff_pct, z, f"{head} The gap is large compared with the numbers involved, so it's "
                       "probably not just chance, but it's still one post per variant: confirm it with another test before "
                       "treating it as a rule.")
    small = " Very few people saw these posts, so" if total_imp < MIN_IMPRESSIONS else " With numbers this small"
    return Verdict("slight", li, diff_pct, z, f"{head}{small} the gap could easily be chance. Treat it as a hint and test again.")


# ------------------------------------------------------------------ loading
async def variants_of(session: AsyncSession, exp_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[tuple[ExperimentPost, Post]]]:
    if not exp_ids:
        return {}
    rows = (await session.execute(
        select(ExperimentPost, Post).join(Post, Post.id == ExperimentPost.post_id)
        .where(ExperimentPost.experiment_id.in_(exp_ids)).order_by(ExperimentPost.label)
    )).all()
    out: dict[uuid.UUID, list] = {}
    for ep, p in rows:
        out.setdefault(ep.experiment_id, []).append((ep, p))
    return out


# ------------------------------------------------------------------ creating
@dataclass
class VariantIn:
    text: str
    hashtags: list[str]
    account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    post_id: uuid.UUID | None = None  # use this existing (unpublished) post as the variant
    kind: str = "manual"
    change: str | None = None


async def create(session: AsyncSession, org: Organization, user, *, name: str, metric: str, mode: str,
                 evaluate_after_days: int, variants: list[VariantIn], then: str, source_post_id: uuid.UUID | None = None,
                 spacing_days: int = 7, notes: str | None = None, now: datetime | None = None) -> Experiment:
    now = now or svc.now_utc()
    if metric not in METRICS:
        raise ExperimentError("Choose engagement rate, clicks or impressions.")
    if mode not in MODES:
        raise ExperimentError("Choose how the variants are compared.")
    if not 2 <= len(variants) <= 3:
        raise ExperimentError("An A/B test needs 2 or 3 variants.")
    if then not in ("draft", "review", "schedule"):
        raise ExperimentError("Choose draft, review or schedule.")
    source = await session.get(Post, source_post_id) if source_post_id else None
    if source_post_id and source is None:
        raise ExperimentError("The original post doesn't exist any more.")

    # resolve each variant's existing post and account
    existing: list[Post | None] = []
    accounts: list[SocialAccount] = []
    for v in variants:
        p = None
        if v.post_id:
            p = await session.get(Post, v.post_id)
            if p is None:
                raise ExperimentError("One of the posts doesn't exist any more.")
            if p.status in ("published", "publishing", "planned", "generating"):
                raise ExperimentError("A post that's already published (or still being written) can't be a variant. "
                                      "Use it as the starting point instead.")
            busy = await session.scalar(select(func.count()).select_from(ExperimentPost)
                                        .join(Experiment, Experiment.id == ExperimentPost.experiment_id)
                                        .where(ExperimentPost.post_id == p.id, Experiment.status == "running"))
            if busy:
                raise ExperimentError("That post is already part of a running A/B test.")
        aid = v.account_id or (p.social_account_id if p else None) or (source.social_account_id if source else None)
        a = await session.get(SocialAccount, aid) if aid else None
        if a is None:
            raise ExperimentError("Choose an account for every variant.")
        existing.append(p)
        accounts.append(a)

    platforms = {a.platform for a in accounts}
    if len(platforms) > 1:
        raise ExperimentError("All variants must be on the same platform, so the results can be compared.")
    if mode == "time" and len({a.id for a in accounts}) > 1:
        raise ExperimentError("In a same-account test every variant uses one account. Choose “different accounts” instead.")
    if mode == "accounts" and len({a.id for a in accounts}) != len(accounts):
        raise ExperimentError("In a different-accounts test each variant needs its own account.")

    # times
    tz = svc.org_tz(org)
    times = [v.scheduled_at or (p.scheduled_at if p else None) for v, p in zip(variants, existing)]
    if mode == "time" and times[0] and not all(times[1:]):
        planned = plan_times(times[0], len(variants), spacing_days)
        times = [t or planned[i] for i, t in enumerate(times)]
    if mode == "accounts" and times[0]:
        times = [t or times[0] for t in times]
    if then != "draft" or any(times):
        for t in times:
            if then != "draft" and t is None:
                raise ExperimentError("Give every variant a date and time, or save them as drafts.")
            if t is not None and t <= now:
                raise ExperimentError("Pick future dates and times for the variants.")
    if mode == "time" and all(times) and len(set(times)) != len(times):
        raise ExperimentError("In a same-account test the variants need different times.")

    plat = svc.platform_of(accounts[0].platform)
    texts = [(v.text or (p.text if p else "") or "").strip() for v, p in zip(variants, existing)]
    if len({t.lower() for t in texts}) != len(texts):
        raise ExperimentError("The variants must have different text.")
    for i, v in enumerate(variants):
        tags = v.hashtags if v.hashtags or existing[i] is None else list(existing[i].hashtags or [])
        cap = svc.full_caption(texts[i], tags)
        if not texts[i]:
            raise ExperimentError(f"Variant {LABELS[i]} has no text.")
        if len(cap) > plat.max_chars:
            raise ExperimentError(f"Variant {LABELS[i]} is too long for {plat.label}: {len(cap)} of {plat.max_chars} characters.")

    exp = Experiment(organization_id=org.id, name=(name or "").strip()[:200] or f"A/B test {now.astimezone(tz):%d %b}",
                     status="running", mode=mode, metric=metric, evaluate_after_days=max(1, min(int(evaluate_after_days or DEFAULT_DAYS), 30)),
                     source_post_id=source.id if source else None, started_at=now, created_by=getattr(user, "id", None),
                     result={"state": "waiting", "message": "Waiting for the variants to be published."}, notes=notes)
    session.add(exp)
    await session.flush()
    auto = svc.auto_approve_posts(org)
    for i, v in enumerate(variants):
        a, p = accounts[i], existing[i]
        if p is None:
            base = source
            supported = supported_types(a.platform)
            ptype = base.post_type if base and base.post_type in supported else ("text" if "text" in supported else supported[0])
            p = Post(
                organization_id=org.id, social_account_id=a.id, platform=a.platform, post_type=ptype,
                pillar_id=base.pillar_id if base else None, campaign_id=base.campaign_id if base else None,
                topic=base.topic if base else None, title=base.title if base else None, text=texts[i],
                hashtags=[svc._clean_tag(h) for h in v.hashtags if svc._clean_tag(h)][: plat.max_hashtags]
                if v.hashtags else list((base.hashtags if base else []) or [])[: plat.max_hashtags],
                link_url=base.link_url if base else None, first_comment=base.first_comment if base else None,
                media_ids=list(base.media_ids or []) if base and ptype != "text" else [], scheduled_at=times[i],
                status="draft", mode="manual", created_by=getattr(user, "id", None), qa={},
                meta={"source": "experiment", "keep_media": True,
                      **({"duplicated_from": str(base.id)} if base else {})},
            )
            session.add(p)
            await session.flush()
            await svc.recheck_post(session, p)
            place(p, then, auto, now)
            svc.add_activity(session, p, "created", f"Variant {LABELS[i]} of the A/B test “{exp.name}”", user=user)
        else:
            changed = False
            if v.text and v.text.strip() != (p.text or ""):
                p.text, changed = v.text.strip(), True
            if v.hashtags:
                p.hashtags, changed = [svc._clean_tag(h) for h in v.hashtags if svc._clean_tag(h)][: plat.max_hashtags], True
            if times[i] != p.scheduled_at:
                p.scheduled_at, changed = times[i], True
            if changed:
                await svc.recheck_post(session, p)
            if changed or p.status in ("draft", "rejected"):
                place(p, then, auto, now)  # every variant follows the same approval choice
            svc.add_activity(session, p, "edited", f"Variant {LABELS[i]} of the A/B test “{exp.name}”", user=user)
        svc.set_meta(p, experiment_id=str(exp.id), experiment_label=LABELS[i])
        session.add(ExperimentPost(organization_id=org.id, experiment_id=exp.id, post_id=p.id, label=LABELS[i],
                                   kind=v.kind if v.kind in ("original", "manual", "ai") else "manual",
                                   change=(v.change or "").strip()[:300] or None))
    await session.flush()
    return exp


# ------------------------------------------------------------------ AI alternatives
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_URL = re.compile(r"https?://\S+")
FOCUS = {
    "hook": "Write a different opening line (the hook). Keep the rest of the post close to the original.",
    "cta": "Change only the closing call to action. Keep the rest of the post as it is.",
    "both": "Write a different opening line (hook) and a different closing call to action. Keep the middle close to the original.",
}


def _new_details(original: str, candidate: str) -> list[str]:
    """Numbers or links in the candidate that aren't in the original (a sign of invented facts)."""
    orig_nums = set(_NUM.findall(original))
    orig_urls = set(_URL.findall(original))
    out = [n for n in _NUM.findall(candidate) if n not in orig_nums]
    out += [u for u in _URL.findall(candidate) if u not in orig_urls]
    return out


async def suggest_variants(session: AsyncSession, text: str, platform: str, *, focus: str = "hook", count: int = 2,
                           hashtags: list[str] | None = None) -> list[dict]:
    text = (text or "").strip()
    if len(text) < 10:
        raise ExperimentError("Write the original post first (at least a sentence).")
    focus = focus if focus in FOCUS else "hook"
    count = max(1, min(int(count or 2), 2))
    plat = svc.platform_of(platform)
    room = plat.max_chars - (len(" ".join(f"#{h}" for h in hashtags or [])) + 2 if hashtags else 0)
    system = (
        "You write alternative versions of a social media post for an A/B test. Rules: keep every fact, number, name, "
        "product, link and claim from the original exactly as it is. Do not add new facts, numbers, statistics, "
        "customers, prices, dates, quotes or promises. Do not use placeholders. Change only what you are asked to change, "
        "so the test shows what that change does. Reply with JSON only."
    )
    prompt = (
        f"Platform: {plat.label}. Style: {plat.style or 'clear and friendly'}\n"
        f"Maximum length: {room} characters (hashtags are added separately; do not include hashtags).\n"
        f"Task: {FOCUS[focus]}\nWrite {count} different version(s).\n\n"
        f"Original post:\n\"\"\"\n{text}\n\"\"\"\n\n"
        'Return {"variants": [{"text": "the full post", "change": "what you changed, in a few words"}]}'
    )
    try:
        data = await llm_router.complete_json(session, "write", prompt, system=system, temperature=0.8, max_tokens=1800)
    except (llm_router.NoProviderError, llm_router.AllProvidersFailed) as e:
        raise ExperimentError(str(e)) from None
    except ValueError:
        raise ExperimentError("The AI didn't return usable suggestions. Try again, or write the variant yourself.") from None
    items = data.get("variants") if isinstance(data, dict) else data if isinstance(data, list) else []
    out, dropped = [], 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        t = svc.plain(str(it.get("text") or "").strip()) or ""
        t = re.sub(r"(?:\s*#[\wऀ-ॿ]+)+\s*$", "", t).strip()  # hashtags are kept separately
        if not t or t.lower() == text.lower() or any(t.lower() == o["text"].lower() for o in out):
            continue
        if _new_details(text, t) or len(t) > room:
            dropped += 1
            continue
        out.append({"text": t, "change": str(it.get("change") or FOCUS[focus].split(".")[0]).strip()[:120], "kind": "ai"})
        if len(out) >= count:
            break
    if not out:
        if dropped:
            raise ExperimentError("The AI suggestions added details that aren't in the original (or were too long), so "
                                  "they were left out. Try again, or write the variant yourself.")
        raise ExperimentError("The AI didn't return usable suggestions. Try again, or write the variant yourself.")
    return out


# ------------------------------------------------------------------ evaluating
def _row(ep: ExperimentPost, p: Post, m: PostMetric | None, metric: str) -> dict:
    return {"post_id": str(p.id), "label": ep.label, "value": metric_value(metric, m) if p.status == "published" else None,
            "impressions": int(m.impressions or 0) if m else 0, "engagements": eng(m), "clicks": int(m.clicks or 0) if m else 0,
            "has_metrics": m is not None, "status": p.status,
            "published_at": p.published_at.isoformat() if p.published_at else None}


def _notes(exp: Experiment, posts: list[Post], tz) -> list[str]:
    notes = []
    if exp.mode == "time":
        slots = {(p.published_at.astimezone(tz).weekday(), p.published_at.astimezone(tz).hour) for p in posts if p.published_at}
        if len(slots) > 1:
            notes.append("The variants went out on different weekdays or hours, which can affect the results on its own.")
        else:
            notes.append("Each variant went out on the same weekday and hour, on different dates.")
    else:
        notes.append("Each account has its own audience, so part of any gap can come from the accounts, not the text.")
    if exp.metric == "engagement_rate":
        notes.append("Engagement rate = (likes + comments + shares + saves + clicks) ÷ impressions.")
    notes.append("Each variant is a single post, so this is a small test.")
    return notes


async def evaluate(session: AsyncSession, exp: Experiment, *, now: datetime | None = None, force: bool = False) -> bool:
    """Update the experiment's state. Returns True when it finished just now. Caller commits.
    force: compare as soon as every variant has results, without waiting `evaluate_after_days`."""
    if exp.status != "running":
        return False
    now = now or svc.now_utc()
    exp.checked_at = now
    pairs = (await variants_of(session, [exp.id])).get(exp.id, [])
    org = await session.get(Organization, exp.organization_id)
    tz = svc.org_tz(org)
    posts = [p for _, p in pairs]

    def finish(state: str, message: str, extra: dict | None = None) -> bool:
        exp.status, exp.ended_at = "done", now
        exp.result = {"state": state, "message": message, **(extra or {})}
        return True

    if len(pairs) < 2:
        return finish("incomplete", "A variant was deleted, so there's nothing to compare.")
    dead = [ep.label for ep, p in pairs if p.status in TERMINAL]
    if dead:
        return finish("incomplete", f"Variant {', '.join(dead)} wasn't published, so the test can't be compared.")
    unpublished = [ep.label for ep, p in pairs if p.status != "published" or p.published_at is None]
    if unpublished:
        started = exp.started_at or exp.created_at or now
        if now - started > timedelta(days=PUBLISH_GIVE_UP_DAYS):
            return finish("incomplete", f"Variant {', '.join(unpublished)} still wasn't published after "
                                        f"{PUBLISH_GIVE_UP_DAYS} days, so the test was closed.")
        exp.result = {"state": "waiting", "message": f"Waiting for variant {', '.join(unpublished)} to be published."}
        return False
    last = max(p.published_at for p in posts)
    due = last + timedelta(days=exp.evaluate_after_days or DEFAULT_DAYS)
    metrics = await latest_metrics(session, [p.id for p in posts])
    rows = [_row(ep, p, metrics.get(p.id), exp.metric) for ep, p in pairs]
    if now < due and not force:
        exp.result = {"state": "collecting", "due_at": due.isoformat(), "variants": rows,
                      "message": f"All variants are out. Results are compared on {due.astimezone(tz):%d %b, %H:%M}, "
                                 f"{exp.evaluate_after_days} day(s) after the last one was published."}
        return False
    missing = [r["label"] for r in rows if r["value"] is None]
    if missing:
        if now > due + timedelta(days=NO_DATA_GRACE_DAYS):
            return finish("no_data", f"No results came back from the platform for variant {', '.join(missing)}, so the "
                                     "test can't be compared.", {"variants": rows})
        what = "impressions" if exp.metric == "engagement_rate" and all(r["has_metrics"] for r in rows) else "results"
        exp.result = {"state": "collecting", "due_at": due.isoformat(), "variants": rows,
                      "message": f"Waiting for {what} from the platform for variant {', '.join(missing)}."}
        return False
    v = compare(exp.metric, rows)
    exp.winner_post_id = uuid.UUID(rows[v.leader]["post_id"]) if v.leader is not None and v.verdict in ("clear", "slight") else None
    return finish("done", v.message, {
        "verdict": v.verdict, "leader": rows[v.leader]["label"] if v.leader is not None else None,
        "difference_pct": v.difference_pct, "gap_score": v.gap_score, "variants": rows, "notes": _notes(exp, posts, tz),
        "metric_label": METRICS[exp.metric],
    })


async def evaluate_org(session: AsyncSession, *, now: datetime | None = None) -> list[Experiment]:
    """Evaluate every running experiment of the session's organization. Returns the ones that finished."""
    done = []
    for exp in (await session.scalars(select(Experiment).where(Experiment.status == "running"))).all():
        try:
            if await evaluate(session, exp, now=now):
                done.append(exp)
        except Exception:  # noqa: BLE001 - one broken test must not block the others
            log.exception("experiment %s evaluation failed", exp.id)
    await session.flush()
    return done


async def notify_done(exp: Experiment) -> None:
    from app.services.notify import notify_org

    try:
        await notify_org(exp.organization_id, "experiment_done", f"A/B test finished: {exp.name}",
                         (exp.result or {}).get("message"), link=f"/posts/experiments?id={exp.id}", permission="content.view")
    except Exception:  # noqa: BLE001
        log.exception("experiment notification failed")


# ------------------------------------------------------------------ reuse the winner
async def reuse_winner(session: AsyncSession, exp: Experiment, user, *, account_id: uuid.UUID | None = None,
                       scheduled_at: datetime | None = None) -> Post:
    if not exp.winner_post_id:
        raise ExperimentError("This test has no winner to reuse.")
    win = await session.get(Post, exp.winner_post_id)
    if win is None:
        raise ExperimentError("The winning post was deleted.")
    a = await session.get(SocialAccount, account_id or win.social_account_id) if (account_id or win.social_account_id) else None
    if a is None:
        raise ExperimentError("Choose an account for the new draft.")
    supported = supported_types(a.platform)
    ptype = win.post_type if win.post_type in supported else ("text" if "text" in supported else supported[0])
    plat = svc.platform_of(a.platform)
    p = Post(
        organization_id=exp.organization_id, social_account_id=a.id, platform=a.platform, post_type=ptype,
        pillar_id=win.pillar_id, campaign_id=win.campaign_id, topic=win.topic, title=win.title, text=win.text,
        hashtags=list(win.hashtags or [])[: plat.max_hashtags], link_url=win.link_url, first_comment=win.first_comment,
        media_ids=list(win.media_ids or []) if ptype != "text" else [], scheduled_at=scheduled_at, status="draft",
        mode="manual", created_by=getattr(user, "id", None), qa={},
        meta={"source": "experiment_winner", "experiment_id": str(exp.id), "duplicated_from": str(win.id), "keep_media": True},
    )
    session.add(p)
    await session.flush()
    await svc.recheck_post(session, p)
    svc.add_activity(session, p, "created", f"Reused the winner of the A/B test “{exp.name}”", user=user)
    return p


# ------------------------------------------------------------------ output
def out(exp: Experiment, pairs: list[tuple[ExperimentPost, Post]], metrics: dict, accounts: dict) -> dict[str, Any]:
    variants = []
    for ep, p in pairs:
        m = metrics.get(p.id)
        a = accounts.get(p.social_account_id)
        imp = int(m.impressions or 0) if m else 0
        variants.append({
            "label": ep.label, "kind": ep.kind, "change": ep.change, "post_id": str(p.id), "status": p.status,
            "status_label": svc.STATUSES.get(p.status, p.status), "text": p.text, "hashtags": p.hashtags or [],
            "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
            "published_at": p.published_at.isoformat() if p.published_at else None, "external_url": p.external_url,
            "account": {"id": str(a.id), "name": a.name, "platform": a.platform,
                        "platform_label": svc.platform_of(a.platform).label} if a else None,
            "metrics": {"impressions": imp, "engagements": eng(m), "clicks": int(m.clicks or 0) if m else 0,
                        "engagement_rate": round(eng(m) / imp, 6) if m and imp else None,
                        "fetched_at": m.fetched_at.isoformat() if m else None} if m else None,
            "is_winner": exp.winner_post_id == p.id,
        })
    return {
        "id": str(exp.id), "name": exp.name, "status": exp.status, "mode": exp.mode, "mode_label": MODES.get(exp.mode, exp.mode),
        "metric": exp.metric, "metric_label": METRICS.get(exp.metric, exp.metric), "evaluate_after_days": exp.evaluate_after_days,
        "source_post_id": str(exp.source_post_id) if exp.source_post_id else None,
        "winner_post_id": str(exp.winner_post_id) if exp.winner_post_id else None,
        "started_at": exp.started_at.isoformat() if exp.started_at else None,
        "ended_at": exp.ended_at.isoformat() if exp.ended_at else None,
        "checked_at": exp.checked_at.isoformat() if exp.checked_at else None,
        "result": exp.result or {}, "notes": exp.notes, "variants": variants,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }
