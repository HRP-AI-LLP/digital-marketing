"""Social presence audit: find every profile, collect data, score it, write suggestions, store the run.

    run = await audit(session, org_id, trigger="manual", progress=ctx.progress)

Pieces: presence_profiles (inputs and URL normalisation), presence_collect (data per profile), presence_score (scores
and rule-based suggestions). An optional LLM pass adds a short narrative and a few extra tips grounded in the
collected facts; any number it mentions that isn't in the facts gets the text dropped.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompanyEntity, CompanyProfile, MediaAsset, Organization, PresenceRun, PresenceSuggestion
from app.services import presence_collect as collect
from app.services import presence_profiles as profiles
from app.services import presence_score as scoring
from app.services.social import net

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]] | None
NOTIFY_CHANGE = 5  # points
HISTORY_MIN_DAYS = 20  # compare followers with a run at least this old
MAX_AVATARS = 12
MAX_IMAGE_BYTES = 3_000_000
SOCIAL_KEYS = ("linkedin", "facebook", "instagram", "x", "youtube", "threads", "pinterest", "github")
SOURCE_RANK = {"connected": 0, "public": 1, "limited": 2}

SYSTEM = """You are a social media strategist for a B2B technology company in India. You receive facts collected from the
company's social profiles and the rule-based suggestions already shown to the user.
Rules: use ONLY the facts given. Never invent numbers, follower counts, growth figures, customers, prices or results;
if a number isn't in the facts, don't write one. Plain, friendly English for non-technical marketers. No markdown.
Return ONLY JSON:
{"narrative": "3 to 5 sentences: where the presence stands, the biggest strength, the biggest gap, what to do first",
 "tips": [{"title": "specific action, max 90 characters", "detail": "one or two sentences grounded in the facts",
           "platform": "linkedin|facebook|instagram|x|youtube|threads|pinterest|github|bluesky|mastodon|", "priority": "high|medium|low"}]}
At most 5 tips. Tips must add something new (content ideas, timing, collaborations, profile wording), not repeat the
rule-based suggestions."""


async def _keywords(session: AsyncSession, org: Organization) -> list[str]:
    names = [n for (n,) in (await session.execute(select(CompanyEntity.name).where(
        CompanyEntity.type.in_(("product", "service", "studio", "use_case"))).limit(40))).all()]
    prof = await session.scalar(select(CompanyProfile).limit(1))
    names += list((prof.industries if prof else None) or [])
    names += [org.tagline or "", org.industry or ""]
    return scoring.offering_keywords([str(n) for n in names if n])


async def _previous_followers(session: AsyncSession, now: datetime) -> dict[str, dict]:
    old = await session.scalar(select(PresenceRun).where(PresenceRun.created_at <= now - timedelta(days=HISTORY_MIN_DAYS))
                               .order_by(PresenceRun.created_at.desc()).limit(1))
    if old is None:
        return {}
    return {p["key"]: {"followers": p.get("followers"), "at": old.created_at.isoformat()}
            for p in old.profiles or [] if isinstance(p, dict) and p.get("key")}


def _merge_duplicates(rows: list[tuple[profiles.ProfileRef, dict]], website_links: set[str]) -> list[tuple[profiles.ProfileRef, dict]]:
    """Two links can be the same profile (a YouTube @handle and its channel id). Merge by the platform's own id."""
    out: list[tuple[profiles.ProfileRef, dict]] = []
    seen: dict[tuple[str, str], int] = {}
    for ref, data in rows:
        pid = data.get("id")
        k = (ref.platform, str(pid)) if pid else None
        if k and k in seen:
            i = seen[k]
            keep, kd = out[i]
            if SOURCE_RANK.get(data.get("source"), 3) < SOURCE_RANK.get(kd.get("source"), 3):
                keep, kd, other = ref, data, keep
            else:
                other = ref
            for s in other.sources:
                keep.add_source(s)
            if other.account_id and not keep.account_id:
                keep.account_id, keep.account_platform, keep.account_status = other.account_id, other.account_platform, other.account_status
            if other.key in website_links or keep.key in website_links:
                website_links.add(keep.key)
            out[i] = (keep, kd)
            continue
        if k:
            seen[k] = len(out)
        out.append((ref, data))
    return out


# ------------------------------------------------------------------ profile pictures (logo consistency)
def ahash(data: bytes) -> int | None:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img = img.convert("L").resize((8, 8))
        px = list(img.getdata())
    except Exception:  # noqa: BLE001 - not an image
        return None
    avg = sum(px) / len(px)
    return sum(1 << i for i, v in enumerate(px) if v >= avg)


async def _image(c: httpx.AsyncClient, url: str) -> bytes | None:
    from app.services import crawler

    current = url
    for _ in range(4):
        try:
            await crawler.check_public_url(current)
            r = await c.get(current)
        except (crawler.UnsafeURLError, httpx.HTTPError, ValueError):
            return None
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            current = str(httpx.URL(current).join(r.headers["location"]))
            continue
        if r.status_code != 200 or not r.headers.get("content-type", "image/").startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
            return None
        return r.content
    return None


async def avatar_groups(session: AsyncSession, org: Organization, c: httpx.AsyncClient,
                        rows: list[dict]) -> tuple[dict[str, int], int | None]:
    """Cluster profile pictures by a perceptual hash. Returns ({profile key: cluster}, cluster of the org logo)."""
    hashes: dict[str, int] = {}
    for p in [r for r in rows if r.get("avatar_url")][:MAX_AVATARS]:
        data = await _image(c, p["avatar_url"])
        h = ahash(data) if data else None
        if h is not None:
            hashes[p["key"]] = h
    logo_hash = None
    if org.logo_asset_id:
        asset = await session.get(MediaAsset, org.logo_asset_id)
        if asset is not None:
            from app.services.social.common import PublishError, read_bytes

            try:
                logo_hash = ahash(read_bytes(asset))
            except PublishError:
                logo_hash = None
    centers: list[int] = []
    if logo_hash is not None:
        centers.append(logo_hash)
    groups: dict[str, int] = {}
    for k, h in hashes.items():
        for i, cen in enumerate(centers):
            if bin(h ^ cen).count("1") <= 12:
                groups[k] = i
                break
        else:
            centers.append(h)
            groups[k] = len(centers) - 1
    return groups, (0 if logo_hash is not None else None)


# ------------------------------------------------------------------ LLM pass
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


def grounded(text: str, allowed: set[str]) -> bool:
    """True when every number in the text appears in the facts (small counts like '3 times a week' are fine)."""
    for n in _numbers(text):
        try:
            if float(n) <= 10:
                continue
        except ValueError:
            pass
        if n not in allowed and n.split(".")[0] not in allowed:
            return False
    return True


def facts(org: Organization, rows: list[dict], result: dict, rules: list[dict], ctx: scoring.Context) -> dict:
    return {
        "company": org.name, "website": ctx.domain,
        "overall": {"score": result["score"], "grade": result["grade"],
                    "parts": [{"part": p["label"], "score": p["score"], "detail": p["detail"]} for p in result["parts"]]},
        "profiles": [{
            "platform": p["label"], "handle": p["handle"], "data_source": p["source_label"], "score": p["score"],
            "followers": p["followers"], "posts_last_30_days": p["posts_30d"], "days_since_last_post": p["days_since_last_post"],
            "average_engagement_per_post": p["avg_engagement"],
            "engagement_rate_percent": round(p["engagement_rate"] * 100, 2) if p.get("engagement_rate") is not None else None,
            "formats_used": p["formats"], "bio": (p.get("bio") or "")[:300],
            "issues": [f"{c['label']}: {c['detail']}" for c in p["checks"] if c["status"] in ("fail", "warn")],
        } for p in rows],
        "missing_platforms": [c["label"] for c in result["coverage"] if not c["present"]],
        "rule_based_suggestions": [s["title"] for s in rules[:15]],
    }


def rules_narrative(org: Organization, rows: list[dict], result: dict, rules: list[dict]) -> str:
    if not rows:
        return (f"We couldn't find any social profiles for {org.name} yet. Add your profile links in Settings → Organization "
                "or connect your accounts, then run the check again.")
    known = [p for p in result["parts"] if p["score"] is not None]
    best = max(known, key=lambda p: p["score"]) if known else None
    worst = min(known, key=lambda p: p["score"]) if known else None
    src = {k: sum(1 for p in rows if p["source"] == k) for k in ("connected", "public", "limited")}
    bits = [f"{org.name}'s social presence scores {result['score']} out of 100 ({result['grade'].lower()}), "
            f"based on {len(rows)} profile{'s' if len(rows) != 1 else ''} ({src['connected']} connected, {src['public']} from "
            f"public data, {src['limited']} with limited data)."]
    if best and worst and best is not worst:
        bits.append(f"The strongest part is {best['label'].lower()} ({best['score']}); the weakest is {worst['label'].lower()} ({worst['score']}).")
    if rules:
        bits.append(f"Start with: {rules[0]['title']}.")
    return " ".join(bits)


async def llm_pass(session: AsyncSession, org: Organization, rows: list[dict], result: dict, rules: list[dict],
                   ctx: scoring.Context) -> tuple[str, str, list[dict], str | None]:
    """(narrative, source ai|rules, extra tips, message)."""
    from app.services.llm import router as llm

    fallback = rules_narrative(org, rows, result, rules)
    if not rows:
        return fallback, "rules", [], None
    f = facts(org, rows, result, rules, ctx)
    blob = json.dumps(f, default=str, ensure_ascii=False)
    allowed = _numbers(blob)
    try:
        out = await llm.complete_json(session, "plan", "Facts about this company's social media presence as JSON. Write the "
                                      "narrative and extra tips.\n\n" + blob[:14000], system=SYSTEM, temperature=0.4,
                                      max_tokens=1500)
    except (llm.NoProviderError, llm.AllProvidersFailed, ValueError) as e:
        log.info("presence narrative fallback: %s", e)
        msg = ("No AI model is set up, so the summary comes from built-in rules." if isinstance(e, llm.NoProviderError)
               else "The AI model didn't answer, so the summary comes from built-in rules.")
        return fallback, "rules", [], msg
    if not isinstance(out, dict):
        return fallback, "rules", [], None
    narrative = re.sub(r"[*_#`]+", "", str(out.get("narrative") or "")).strip()[:1500]
    source = "ai"
    if not narrative or not grounded(narrative, allowed):
        narrative, source = fallback, "rules"
    tips: list[dict] = []
    existing = {scoring.compact(s["title"]) for s in rules}
    for t in out.get("tips") or []:
        if not isinstance(t, dict):
            continue
        title = re.sub(r"[*_#`]+", "", str(t.get("title") or "")).strip()[:200]
        detail = re.sub(r"[*_#`]+", "", str(t.get("detail") or "")).strip()[:800]
        if not title or scoring.compact(title) in existing or not grounded(title + " " + detail, allowed):
            continue
        plat = str(t.get("platform") or "").lower().strip()
        prio = str(t.get("priority") or "medium").lower()
        prio = prio if prio in ("high", "medium", "low") else "medium"
        tips.append({"key": "ai:" + scoring.compact(title)[:120], "title": title, "detail": detail or None, "priority": prio,
                     "points": {"high": 2.4, "medium": 1.6, "low": 0.8}[prio], "category": "tip",
                     "platform": plat if plat in profiles.LABELS else None, "profile_key": None, "action": None, "source": "ai"})
        if len(tips) >= 5:
            break
    return narrative, source, tips, None


# ------------------------------------------------------------------ the audit
async def audit(session: AsyncSession, org_id: uuid.UUID, *, trigger: str = "manual", created_by: uuid.UUID | None = None,
                progress: Progress = None) -> PresenceRun:
    async def step(msg: str) -> None:
        if progress:
            await progress(msg)

    org = await session.get(Organization, org_id)
    now = datetime.now(UTC)
    await step("Finding your social profiles")
    inputs = await profiles.gather(session, org)
    ctx = scoring.Context(org_name=org.name, brand=scoring.brand_stems(org.name, inputs.website_domain),
                          domain=inputs.website_domain, keywords=await _keywords(session, org),
                          website_links=inputs.website_links, website_crawled=inputs.website_crawled, now=now,
                          previous=await _previous_followers(session, now))
    collected: list[tuple[profiles.ProfileRef, dict]] = []
    groups: dict[str, int] = {}
    main = None
    async with net.client(timeout=30.0) as c:
        x = collect.Collect(session, c)
        for ref in inputs.profiles:
            await step(f"Reading {ref.label} ({ref.handle})")
            collected.append((ref, await collect.collect(x, ref)))
        collected = _merge_duplicates(collected, ctx.website_links)
        rows = [scoring.score_profile(ref, data, ctx) for ref, data in collected]
        if len(rows) >= 2:
            await step("Comparing profile pictures")
            try:
                groups, main = await avatar_groups(session, org, c, rows)
            except Exception as e:  # noqa: BLE001 - optional check
                log.info("avatar comparison skipped: %s", e)
    await step("Scoring your presence")
    result = scoring.overall(rows, ctx, groups, main)
    rules = scoring.suggestions(rows, result, ctx)
    await step("Writing suggestions")
    narrative, nsource, tips, message = await llm_pass(session, org, rows, result, rules, ctx)

    prev = await session.scalar(select(PresenceRun).order_by(PresenceRun.created_at.desc()).limit(1))
    prev_sugs = {}
    if prev is not None:
        prev_sugs = {s.key: s for s in (await session.scalars(select(PresenceSuggestion).where(PresenceSuggestion.run_id == prev.id))).all()}
    org_links = inputs.org_links
    run = PresenceRun(
        organization_id=org_id, trigger=trigger, score=result["score"], grade=result["grade"], parts=result["parts"],
        profiles=rows, coverage=result["coverage"], narrative=narrative, narrative_source=nsource, created_by=created_by,
        created_at=now,
        summary={
            "profiles": len(rows), **{k: sum(1 for p in rows if p["source"] == k) for k in ("connected", "public", "limited")},
            "website": inputs.website, "website_domain": inputs.website_domain, "website_crawled": inputs.website_crawled,
            "missing_org_links": [k for k in SOCIAL_KEYS if not org_links.get(k)],
            "unparsed_links": inputs.unparsed, "message": message,
            "previous_score": prev.score if prev else None,
            "change": (result["score"] - prev.score) if prev and prev.score is not None and result["score"] is not None else None,
        },
    )
    session.add(run)
    await session.flush()
    new_keys = set()
    for rank, s in enumerate(rules + tips):
        if s["key"] in new_keys:
            continue
        new_keys.add(s["key"])
        old = prev_sugs.get(s["key"])
        status, reopened, changed_at, changed_by = "open", False, None, None
        if old is not None and old.status == "done":
            if s["source"] == "ai":
                status, changed_at, changed_by = "done", old.status_changed_at, old.status_changed_by
            else:
                reopened = True  # marked done, but the check still fails
        session.add(PresenceSuggestion(
            organization_id=org_id, run_id=run.id, key=s["key"], rank=rank, profile_key=s.get("profile_key"),
            platform=s.get("platform"), title=s["title"][:500], detail=s.get("detail"), priority=s["priority"],
            category=s["category"], points=s["points"], action=s.get("action"), source=s["source"], status=status,
            reopened=reopened, status_changed_at=changed_at, status_changed_by=changed_by))
    run.summary = {**run.summary, "resolved": [s.title for k, s in prev_sugs.items() if k not in new_keys and s.source == "rules"][:10]}
    await session.flush()
    return run


# ------------------------------------------------------------------ output
def suggestion_out(s: PresenceSuggestion) -> dict:
    return {"id": str(s.id), "key": s.key, "title": s.title, "detail": s.detail, "priority": s.priority, "category": s.category,
            "platform": s.platform, "profile_key": s.profile_key, "action": s.action, "source": s.source, "status": s.status,
            "reopened": s.reopened, "points": s.points,
            "status_changed_at": s.status_changed_at.isoformat() if s.status_changed_at else None}


def run_out(r: PresenceRun) -> dict:
    return {"id": str(r.id), "created_at": r.created_at.isoformat(), "trigger": r.trigger, "score": r.score, "grade": r.grade,
            "parts": r.parts, "profiles": r.profiles, "coverage": r.coverage, "summary": r.summary, "narrative": r.narrative,
            "narrative_source": r.narrative_source}


async def latest(session: AsyncSession) -> PresenceRun | None:
    return await session.scalar(select(PresenceRun).order_by(PresenceRun.created_at.desc()).limit(1))


async def history(session: AsyncSession, limit: int = 52) -> list[dict]:
    rows = (await session.execute(select(PresenceRun.id, PresenceRun.created_at, PresenceRun.score, PresenceRun.profiles)
                                  .order_by(PresenceRun.created_at.desc()).limit(limit))).all()
    return [{"id": str(i), "at": at.isoformat(), "score": sc,
             "profiles": {p["key"]: p.get("score") for p in prof or [] if isinstance(p, dict) and p.get("key")}}
            for i, at, sc, prof in reversed(rows)]
