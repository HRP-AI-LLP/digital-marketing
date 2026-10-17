"""Content generation pipeline: Plan → Write → QA → Uniqueness → Media → Route.

`generate_post(session, post)` turns a `planned` Post into a finished draft and routes it by mode:

- full_auto         → scheduled (publishes at its slot)
- auto_with_window  → in_review with an approval deadline; promoted to scheduled when it passes
- approve_first     → in_review (missed if nobody approves before the slot)
- manual            → draft (never published automatically)

Posts that fail QA or uniqueness after the automatic fixes are always held for a human
(`in_review` with mode approve_first) instead of being published automatically.

Dependencies built by other modules (knowledge, media) are imported lazily so the pipeline
degrades gracefully when they are missing.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BrandProfile,
    Campaign,
    ContentPillar,
    Organization,
    Post,
    PostActivity,
    Schedule,
    SocialAccount,
)
from app.services.llm import router as llm_router
from app.services.platforms import PLATFORMS, POST_TYPES, Platform, supported_types

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ constants
MODES: dict[str, str] = {
    "manual": "Generate drafts only. Nothing is published unless someone schedules it.",
    "approve_first": "Each post waits for approval. If nobody approves it before its time, it is skipped.",
    "auto_with_window": "Each post publishes on time unless someone rejects it within the review window.",
    "full_auto": "Posts publish automatically after the quality checks pass.",
}
STATUSES: dict[str, str] = {
    "planned": "Planned", "generating": "Writing", "draft": "Draft", "in_review": "Waiting for approval",
    "approved": "Approved", "scheduled": "Scheduled", "publishing": "Publishing", "published": "Published",
    "manual_pending": "Ready to post by hand", "failed": "Failed", "rejected": "Rejected", "missed": "Missed",
    "skipped": "Skipped",
}
NEEDS_MEDIA = {"image", "carousel", "gif", "video", "animation", "document"}
SLIDE_TYPES = {"carousel", "document"}
SCRIPT_TYPES = {"video", "animation", "gif"}
VISUAL_TYPES = {"image", "gif", "animation"}  # the writer also suggests a layout + the words that go on the graphic
VISUAL_LAYOUTS = ("photo_headline", "hook_bold", "quote_card", "stat_card", "data_chart", "feature_spotlight", "tip_card",
                  "steps_card", "myth_fact", "announcement", "event_card", "testimonial", "hiring", "animated_text")
_VISUAL_STR = ("layout", "headline", "eyebrow", "stat", "stat_label", "quote", "author", "role", "left", "right", "left_label",
               "right_label", "cta", "date", "time", "place", "source")


def _visual(raw: Any) -> dict | None:
    """Sanitised layout hints from the writer (only known keys, short strings, short lists)."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for k in _VISUAL_STR:
        v = raw.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            out[k] = str(v).strip()[:220]
    if out.get("layout") not in VISUAL_LAYOUTS:
        out.pop("layout", None)
    b = raw.get("bullets")
    if isinstance(b, list):
        items = [str(x).strip()[:140] for x in b if isinstance(x, (str, int, float)) and str(x).strip()][:5]
        if items:
            out["bullets"] = items
    return out or None
MANUAL_PLATFORMS = {"manual", "x"}

SIMILARITY_THRESHOLD = 0.88  # cosine similarity on embeddings
JACCARD_THRESHOLD = 0.6  # word 3-shingle Jaccard (fallback without embeddings; stricter measure)
MAX_UNIQUENESS_RETRIES = 2
AUTO_MIN_SCORE = 5.0  # LLM judge score below this holds an automatic post for review
DEFAULT_GRACE_HOURS = 3

DEFAULT_PILLARS: list[dict[str, Any]] = [
    {"name": "Product & features", "weight": 30, "color": "#0f8dde",
     "description": "What our products do, new features, and the problems they solve."},
    {"name": "Thought leadership (AI & HR tech)", "weight": 25, "color": "#075492",
     "description": "Opinions and trends in AI, HR technology and the future of work."},
    {"name": "Tips & how-tos", "weight": 20, "color": "#06a6c7",
     "description": "Practical advice readers can use today."},
    {"name": "Customer stories & use cases", "weight": 15, "color": "#16a34a",
     "description": "How organisations use our solutions and the results they get."},
    {"name": "Company & culture", "weight": 10, "color": "#f59e0b",
     "description": "Team, milestones, events and life at the company."},
]


class GenerationError(Exception):
    """A generation failure that should not be retried; message is shown to the user."""


# ------------------------------------------------------------------ small helpers
def now_utc() -> datetime:
    return datetime.now(UTC)


def platform_of(name: str) -> Platform:
    return PLATFORMS.get(name) or PLATFORMS["manual"]


def org_tz(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo((org.timezone if org else None) or "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def full_caption(text: str | None, hashtags: list[str] | None) -> str:
    """The text as it is published: body, blank line, hashtags."""
    body = (text or "").rstrip()
    tags = " ".join(f"#{h}" for h in (hashtags or []) if h)
    return f"{body}\n\n{tags}" if tags and body else (body or tags)


def as_uuid(v: Any) -> uuid.UUID | None:
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def add_activity(session: AsyncSession, post: Post, action: str, note: str | None = None, *, user=None,
                 actor: str | None = None) -> None:
    session.add(PostActivity(
        organization_id=post.organization_id, post_id=post.id, user_id=getattr(user, "id", None),
        actor=actor or getattr(user, "email", None) or "system", action=action, note=(note or None) and note[:2000],
    ))


def add_warning(post: Post, msg: str) -> None:
    meta = dict(post.meta or {})
    warnings = list(meta.get("warnings") or [])
    if msg not in warnings:
        warnings.append(msg)
    meta["warnings"] = warnings[-10:]
    post.meta = meta


def set_meta(post: Post, **kw) -> None:
    post.meta = {**(post.meta or {}), **kw}


async def get_brand(session: AsyncSession, org_id: uuid.UUID) -> BrandProfile:
    brand = await session.scalar(select(BrandProfile))
    if brand is None:
        brand = BrandProfile(organization_id=org_id, do_list=[], dont_list=[], hashtags=[], ctas=[], banned_words=[],
                             colors={}, fonts={}, blackout_dates=[], settings={})
        session.add(brand)
        await session.flush()
    return brand


# ------------------------------------------------------------------ generation settings
@dataclass
class GenSettings:
    mode: str = "approve_first"
    include_link: bool = False
    link_url: str | None = None
    instructions: str | None = None
    approval_window_hours: int = 4
    grace_hours: int = DEFAULT_GRACE_HOURS
    post_types: dict[str, float] = field(default_factory=lambda: {"text": 1})
    pillar_ids: list[str] = field(default_factory=list)


def settings_for(post: Post, schedule: Schedule | None) -> GenSettings:
    if schedule is not None:
        return GenSettings(
            mode=post.mode or schedule.mode, include_link=bool(schedule.include_link and schedule.link_url),
            link_url=schedule.link_url if schedule.include_link else None, instructions=schedule.instructions,
            approval_window_hours=schedule.approval_window_hours or 4,
            grace_hours=schedule.catch_up_grace_hours if schedule.catch_up_grace_hours is not None else DEFAULT_GRACE_HOURS,
            post_types=dict(schedule.post_types or {"text": 1}), pillar_ids=[str(p) for p in (schedule.pillar_ids or [])],
        )
    return GenSettings(mode=post.mode or "manual", include_link=bool(post.link_url), link_url=post.link_url,
                       instructions=(post.meta or {}).get("brief"))


# ------------------------------------------------------------------ 1. plan
async def choose_pillar(session: AsyncSession, account_id: uuid.UUID | None, restrict: list[str],
                        now: datetime | None = None) -> ContentPillar | None:
    """Active pillar with the largest deficit between its weight share and its share of this
    account's posts over the last 30 days."""
    pillars = list((await session.scalars(select(ContentPillar).where(ContentPillar.active.is_(True)))).all())
    if restrict:
        allowed = {str(r) for r in restrict}
        subset = [p for p in pillars if str(p.id) in allowed]
        pillars = subset or pillars
    pillars = [p for p in pillars if (p.weight or 0) > 0] or pillars
    if not pillars:
        return None
    since = (now or now_utc()) - timedelta(days=30)
    q = select(Post.pillar_id, func.count()).where(
        Post.created_at >= since, Post.pillar_id.is_not(None),
        Post.status.notin_(["failed", "rejected", "skipped"]),
    ).group_by(Post.pillar_id)
    if account_id:
        q = q.where(Post.social_account_id == account_id)
    counts = {pid: n for pid, n in (await session.execute(q)).all()}
    total_w = sum(max(p.weight, 0) for p in pillars) or len(pillars)
    total_n = sum(counts.get(p.id, 0) for p in pillars)

    def deficit(p: ContentPillar) -> tuple[float, int, str]:
        target = (max(p.weight, 0) or 1) / total_w * (total_n + 1)
        return (target - counts.get(p.id, 0), p.weight, p.name)

    return max(pillars, key=deficit)


async def choose_post_type(session: AsyncSession, platform: str, weights: dict[str, float],
                           account_id: uuid.UUID | None, now: datetime | None = None) -> str:
    supported = supported_types(platform)
    mix: dict[str, float] = {}
    for t, w in (weights or {}).items():
        try:
            wf = float(w)
        except (TypeError, ValueError):
            continue
        if t in supported and wf > 0:
            mix[t] = wf
    if not mix:
        return "text" if "text" in supported else supported[0]
    if len(mix) == 1:
        return next(iter(mix))
    since = (now or now_utc()) - timedelta(days=30)
    q = select(Post.post_type, func.count()).where(
        Post.created_at >= since, Post.status.notin_(["failed", "rejected", "skipped"])
    ).group_by(Post.post_type)
    if account_id:
        q = q.where(Post.social_account_id == account_id)
    counts = {t: n for t, n in (await session.execute(q)).all()}
    total_w = sum(mix.values())
    total_n = sum(counts.get(t, 0) for t in mix)
    return max(mix, key=lambda t: (mix[t] / total_w * (total_n + 1) - counts.get(t, 0), mix[t], t))


async def choose_campaign(session: AsyncSession, slot_day: date, seed: str) -> Campaign | None:
    day = slot_day.isoformat()
    rows = (await session.scalars(select(Campaign).where(Campaign.active.is_(True)))).all()
    live = [c for c in rows if (not c.starts_on or c.starts_on <= day) and (not c.ends_on or c.ends_on >= day)]
    if not live:
        return None
    rng = random.Random(seed)
    for c in sorted(live, key=lambda c: (-(c.boost or 0), c.name)):
        if rng.random() * 100 < max(0, min(100, c.boost or 0)):
            return c
    return None


async def recent_topics(session: AsyncSession, limit: int = 40) -> list[str]:
    rows = (await session.scalars(
        select(Post.topic).where(Post.topic.is_not(None)).order_by(Post.created_at.desc()).limit(limit)
    )).all()
    return [t for t in rows if t]


async def recent_rejections(session: AsyncSession, limit: int = 6) -> list[str]:
    rows = (await session.scalars(
        select(Post.rejection_reason).where(Post.rejection_reason.is_not(None), Post.status == "rejected")
        .order_by(Post.updated_at.desc()).limit(limit)
    )).all()
    return [r for r in rows if r and r.strip()]


async def _knowledge():
    try:
        from app.services import knowledge
        return knowledge
    except ImportError:
        return None


async def company_context(session: AsyncSession, max_chars: int = 3500) -> str:
    k = await _knowledge()
    if k is None or not hasattr(k, "company_context"):
        return ""
    try:
        return (await k.company_context(session, max_chars=max_chars)) or ""
    except Exception:
        log.exception("company_context failed")
        return ""


async def retrieve(session: AsyncSession, query: str, k: int = 6) -> list[dict]:
    mod = await _knowledge()
    if mod is None or not hasattr(mod, "retrieve") or not query:
        return []
    try:
        rows = await mod.retrieve(session, query, k=k)
        return [r for r in (rows or []) if isinstance(r, dict) and r.get("text")]
    except Exception:
        log.exception("knowledge retrieve failed")
        return []


async def next_opportunity(session: AsyncSession, pillar_id: uuid.UUID | None):
    mod = await _knowledge()
    if mod is None or not hasattr(mod, "next_opportunity"):
        return None
    try:
        return await mod.next_opportunity(session, pillar_id)
    except Exception:
        log.exception("next_opportunity failed")
        return None


async def plan_post(session: AsyncSession, post: Post, schedule: Schedule | None, cfg: GenSettings,
                    tz: ZoneInfo) -> None:
    """Fill pillar, post type, campaign and topic (only the ones not already set)."""
    if post.pillar_id is None and (schedule is not None or cfg.pillar_ids):
        pillar = await choose_pillar(session, post.social_account_id, cfg.pillar_ids)
        post.pillar_id = pillar.id if pillar else None
    if schedule is not None and not (post.meta or {}).get("post_type_fixed"):
        post.post_type = await choose_post_type(session, post.platform, cfg.post_types, post.social_account_id)
    if post.post_type not in supported_types(post.platform):
        types = supported_types(post.platform)
        post.post_type = "text" if "text" in types else types[0]

    slot_day = (post.scheduled_at or now_utc()).astimezone(tz).date()
    campaign = None
    if post.campaign_id:
        campaign = await session.get(Campaign, post.campaign_id)
    elif schedule is not None:
        campaign = await choose_campaign(session, slot_day, str(post.id))
        post.campaign_id = campaign.id if campaign else None

    if post.topic:
        return
    pillar = await session.get(ContentPillar, post.pillar_id) if post.pillar_id else None
    if campaign is None:
        opp = await next_opportunity(session, post.pillar_id)
        if opp is not None and getattr(opp, "title", None):
            post.opportunity_id = opp.id
            post.topic = opp.title
            if getattr(opp, "angle", None):
                set_meta(post, angle=opp.angle)
            # Researched topics carry a grounded brief (facts + sources) and, for occasions, the occasion
            # (used by festive media templates).
            odata = getattr(opp, "data", None) or {}
            for k in ("brief", "occasion", "research_brief_id"):
                if odata.get(k) and not (post.meta or {}).get(k):
                    set_meta(post, **{k: odata[k]})
            return

    avoid = await recent_topics(session)
    ctx = await company_context(session, max_chars=1500)
    lines = [
        f"Suggest ONE fresh social media post topic for {platform_of(post.platform).label} "
        f"(post format: {POST_TYPES.get(post.post_type, post.post_type)}).",
    ]
    if pillar:
        lines.append(f"Content pillar: {pillar.name}. {pillar.description or ''}")
    if campaign:
        lines.append(f"Campaign: {campaign.name}. Brief: {campaign.brief or ''}")
    if cfg.instructions:
        lines.append(f"Extra instructions: {cfg.instructions}")
    if ctx:
        lines.append(f"Company facts:\n{ctx}")
    if avoid:
        lines.append("Do NOT repeat or closely paraphrase these recent topics:\n- " + "\n- ".join(a[:160] for a in avoid))
    lines.append('Return JSON: {"topic": "<one line, under 120 characters>", "angle": "<the specific angle or hook>"}')
    data = await llm_router.complete_json(session, "plan", "\n\n".join(lines),
                                          system="You are a B2B content strategist. Reply with JSON only.",
                                          temperature=0.9, max_tokens=400)
    topic = angle = None
    if isinstance(data, dict):
        topic = str(data.get("topic") or "").strip() or None
        angle = str(data.get("angle") or "").strip() or None
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        topic = str(data[0].get("topic") or "").strip() or None
    if not topic:
        topic = (campaign.name if campaign else pillar.name if pillar else "Company update")
    post.topic = topic[:300]
    if angle:
        set_meta(post, angle=angle[:500])


# ------------------------------------------------------------------ 2. write
_TAG_RE = re.compile(r"#([\w\u0900-\u097F]+)", re.UNICODE)


def _clean_tag(t: Any) -> str:
    s = re.sub(r"[^\w\u0900-\u097F]", "", str(t).strip().lstrip("#"), flags=re.UNICODE)
    return s[:60]


def _str(v: Any, limit: int = 20000) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return None
    s = str(v).strip()
    return s[:limit] or None


_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
_MD_ITALIC = re.compile(r"(?<![\w*])([*_])(?=\S)([^*_\n]+?)(?<=\S)\1(?![\w*])")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?=\S)", re.M)
_MD_CODE = re.compile(r"`([^`\n]+)`")


def plain(text: str | None, accent: bool = False) -> str | None:
    """Social networks show text as typed, so remove markdown the model adds (**bold**, *italic*, [a](url),
    headings, `code`). Hashtags and emojis are kept. With accent=True (slide and video text), bold becomes the
    templates' *accent* highlight instead of being dropped."""
    if not text:
        return text

    def link(m: re.Match) -> str:
        label, url = m.group(1).strip(), m.group(2)
        bare = lambda u: re.sub(r"^https?://(www\.)?", "", u).rstrip("/")  # noqa: E731
        return url if bare(label) == bare(url) else f"{label} ({url})"

    t = _MD_LINK.sub(link, text)
    if accent:
        return _MD_HEADING.sub("", _MD_CODE.sub(r"\1", _MD_BOLD.sub(r"*\2*", t)))
    t = _MD_BOLD.sub(r"\2", t)
    t = _MD_ITALIC.sub(r"\2", t)
    t = _MD_CODE.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    return t


def normalize_draft(raw: Any, platform: str, post_type: str) -> dict:
    """Coerce model output into the draft shape. Raises ValueError when there is no usable text."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = raw[0]
    if not isinstance(raw, dict):
        raise ValueError("The AI model did not return a post in the expected format.")
    p = platform_of(platform)
    text = plain(_str(raw.get("text") or raw.get("body") or raw.get("caption") or raw.get("post"))) or ""
    tags_raw = raw.get("hashtags") or []
    if isinstance(tags_raw, str):
        tags_raw = re.split(r"[\s,]+", tags_raw)
    tags: list[str] = []
    if isinstance(tags_raw, list):
        tags = [_clean_tag(t) for t in tags_raw if isinstance(t, (str, int))]
    # Hashtag-only lines at the end of the text belong in the hashtag list.
    lines = text.rstrip().split("\n")
    while lines and lines[-1].strip() and all(w.startswith("#") for w in lines[-1].split()):
        tags = [_clean_tag(w) for w in lines[-1].split()] + tags
        lines.pop()
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("The AI model returned an empty post.")
    seen: set[str] = set()
    hashtags = []
    for t in tags:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            hashtags.append(t)

    draft: dict[str, Any] = {
        "text": text, "hashtags": hashtags,
        "title": plain(_str(raw.get("title"), 300)) if p.title_chars else None,
        "first_comment": plain(_str(raw.get("first_comment"), 2000)),
        "image_brief": _str(raw.get("image_brief"), 2000),
        "alt_text": _str(raw.get("alt_text"), 1000),
    }
    if post_type in SLIDE_TYPES:
        slides = []
        for s in raw.get("slides") or []:
            if isinstance(s, dict):
                h, b = plain(_str(s.get("heading"), 200), True), plain(_str(s.get("body"), 600), True)
                if h or b:
                    slides.append({"heading": h or "", "body": b or ""})
            elif isinstance(s, str) and s.strip():
                slides.append({"heading": plain(s.strip()[:200], True), "body": ""})
        draft["slides"] = slides[:10]
    if post_type in VISUAL_TYPES:
        vis = _visual(raw.get("visual"))
        if vis:
            draft["visual"] = vis
    if post_type in SCRIPT_TYPES:
        script = []
        for s in raw.get("video_script") or raw.get("scenes") or []:
            if isinstance(s, dict):
                c, v = plain(_str(s.get("caption"), 200), True), plain(_str(s.get("voiceover"), 600))
                if c or v:
                    script.append({"caption": c or "", "voiceover": v or ""})
        draft["video_script"] = script[:12]
    return draft


def build_system_prompt(brand: BrandProfile, platform: str, post_type: str, cfg: GenSettings, context: str,
                        knowledge: list[dict], avoid: list[str]) -> str:
    p = platform_of(platform)
    parts = [
        "You write social media posts for a company. Write in the company's own voice and only state facts "
        "supported by the company facts and knowledge below. Never invent statistics, quotes, prices or client names: "
        "mention a client only if its name appears in the company facts. No percentages or numbers unless they "
        "appear in the facts. Write plain text: no markdown (no **bold**, *italics*, # headings or [text](link)); "
        "write links as plain URLs.",
        f"Platform: {p.label}. Style: {p.style or 'clear and professional'}",
        f"Format: {POST_TYPES.get(post_type, post_type)}.",
        f"Hard limits: the text plus hashtags must fit in {p.max_chars} characters; at most {p.max_hashtags} hashtags."
        + (f" The title must be under {p.title_chars} characters." if p.title_chars else ""),
    ]
    if brand.voice:
        parts.append(f"Brand voice: {brand.voice}")
    if brand.audience:
        parts.append(f"Audience: {brand.audience}")
    if brand.do_list:
        parts.append("Do:\n- " + "\n- ".join(map(str, brand.do_list)))
    if brand.dont_list:
        parts.append("Don't:\n- " + "\n- ".join(map(str, brand.dont_list)))
    if brand.banned_words:
        parts.append("Never use these words or phrases: " + ", ".join(map(str, brand.banned_words)))
    emoji = {"none": "Do not use any emojis.", "light": "Use at most 2 emojis, only where they add meaning.",
             "rich": "Emojis are welcome where they fit."}.get(brand.emoji_policy or "light", "")
    if emoji:
        parts.append(emoji)
    lang = brand.language or "en"
    if lang != "en":
        parts.append(f"Write in this language: {lang}.")
    if brand.ctas:
        parts.append("Preferred calls to action (pick one that fits): " + "; ".join(map(str, brand.ctas)))
    if brand.hashtags and p.max_hashtags:
        parts.append("Brand hashtags to prefer when relevant: " + " ".join(f"#{_clean_tag(h)}" for h in brand.hashtags))
    if brand.disclaimer:
        parts.append(f"End the text with this disclaimer exactly: {brand.disclaimer}")
    if cfg.include_link and cfg.link_url:
        parts.append(f"Include this link in the text: {cfg.link_url}")
    if cfg.instructions:
        parts.append(f"Extra instructions: {cfg.instructions}")
    if context:
        parts.append(f"Company facts (the only source for names and claims):\n{context}")
    if knowledge:
        snippets = "\n\n".join(f"[{k.get('title') or k.get('url') or 'source'}]\n{str(k['text'])[:900]}" for k in knowledge[:6])
        parts.append(f"Relevant knowledge:\n{snippets}")
    if avoid:
        parts.append("Avoid these (feedback on earlier posts):\n- " + "\n- ".join(a[:300] for a in avoid))

    shape = ['"text": "<post body without hashtags>"', '"hashtags": ["NoHashSign", ...]',
             '"first_comment": "<optional first comment or null>"',
             '"image_brief": "<one paragraph describing the visual; no text inside AI images>"',
             '"alt_text": "<image description for screen readers>"']
    if p.title_chars:
        shape.insert(0, f'"title": "<under {p.title_chars} characters>"')
    if post_type in SLIDE_TYPES:
        shape.append('"slides": [{"heading": "...", "body": "..."}]  (5 to 8 slides)')
    if post_type in VISUAL_TYPES:
        shape.append('"visual": {"layout": "<one of: ' + ", ".join(VISUAL_LAYOUTS) + '>", "headline": "<max 8 words for the graphic; wrap 1-2 key words in *stars*>", '
                     '"eyebrow": "<2-3 word label>", optional only when true and supported by the facts: "stat", "stat_label", "quote", "author", '
                     '"bullets": ["<max 6 words each>"], "left"/"right" (myth vs fact), "date"/"time"/"place" (events), "cta"}')
    if post_type in SCRIPT_TYPES:
        shape.append('"video_script": [{"caption": "<on-screen text>", "voiceover": "<spoken line>"}]  (4 to 8 scenes, under 45 seconds total)')
    parts.append("Reply with ONLY a JSON object: {" + ", ".join(shape) + "}")
    return "\n\n".join(parts)


async def write_draft(session: AsyncSession, post: Post, brand: BrandProfile, cfg: GenSettings, *,
                      extra_avoid: list[str] | None = None, fix_issues: list[str] | None = None,
                      previous: dict | None = None) -> dict:
    meta = post.meta or {}
    ctx = await company_context(session)
    knowledge = await retrieve(session, post.topic or "", k=6)
    avoid = list(meta.get("avoid") or []) + await recent_rejections(session) + list(extra_avoid or [])
    system = build_system_prompt(brand, post.platform, post.post_type, cfg, ctx, knowledge, avoid)
    pillar = await session.get(ContentPillar, post.pillar_id) if post.pillar_id else None
    campaign = await session.get(Campaign, post.campaign_id) if post.campaign_id else None
    lines = [f"Topic: {post.topic or 'Company update'}"]
    if meta.get("angle"):
        lines.append(f"Angle: {meta['angle']}")
    if pillar:
        lines.append(f"Content pillar: {pillar.name}")
    if campaign:
        lines.append(f"Campaign: {campaign.name}. {campaign.brief or ''}")
    if meta.get("brief"):
        lines.append(f"Brief from the user: {meta['brief']}")
    if meta.get("regenerate_note"):
        lines.append(f"This is a new version. Make it clearly different from the previous one. {meta['regenerate_note']}")
        if meta.get("previous_text"):
            lines.append("Previous version (do not reuse its wording):\n" + str(meta["previous_text"])[:1500])
    if fix_issues and previous:
        lines.append("Rewrite this draft to fix these problems:\n- " + "\n- ".join(fix_issues))
        lines.append("Draft:\n" + full_caption(previous.get("text"), previous.get("hashtags")))
    raw = await llm_router.complete_json(session, "write", "\n\n".join(lines), system=system,
                                         temperature=0.6 if fix_issues else 0.8, max_tokens=2500)
    return normalize_draft(raw, post.platform, post.post_type)


# ------------------------------------------------------------------ 3. QA
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u27BF\U0001F1E6-\U0001F1FF\u2B50\u2B06\u2194-\u21AA]"
)
_PLACEHOLDER_RE = re.compile(r"[\[\]{}]|\bTODO\b|\bTBD\b|lorem ipsum|\blorem\b|<insert|insert (?:name|link|here)", re.IGNORECASE)
LIGHT_EMOJI_MAX = 3


def count_emojis(s: str) -> int:
    return len(_EMOJI_RE.findall(s or ""))


def _strip_emojis(s: str, keep: int = 0) -> str:
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        return m.group(0) if n <= keep else ""

    out = _EMOJI_RE.sub(repl, s)
    return re.sub(r"[ \t]{2,}", " ", out).replace("\ufe0f", "" if keep == 0 else "\ufe0f")


def _banned_hits(text: str, banned: list[str]) -> list[str]:
    hits = []
    for w in banned or []:
        w = str(w).strip()
        if w and re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.IGNORECASE):
            hits.append(w)
    return hits


def run_checks(draft: dict, platform: str, brand: BrandProfile | None, *, link_url: str | None,
               post_type: str = "text") -> list[dict]:
    p = platform_of(platform)
    text = draft.get("text") or ""
    tags = draft.get("hashtags") or []
    caption = full_caption(text, tags)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, hard: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "hard": hard})

    add("length", len(caption) <= p.max_chars, f"{len(caption)} of {p.max_chars} characters")
    add("hashtags", len(tags) <= p.max_hashtags, f"{len(tags)} of at most {p.max_hashtags} hashtags")
    if p.title_chars:
        title = draft.get("title") or ""
        add("title", 0 < len(title) <= p.title_chars,
            f"{len(title)} of {p.title_chars} characters" if title else "A title is required on this platform")
    hits = _banned_hits(caption + " " + (draft.get("title") or ""), list((brand.banned_words if brand else None) or []))
    add("banned_words", not hits, ("Uses: " + ", ".join(hits)) if hits else "None used")
    ph = _PLACEHOLDER_RE.findall(caption + " " + (draft.get("title") or ""))
    add("placeholders", not ph, ("Found: " + ", ".join(sorted(set(ph))[:5])) if ph else "None found")
    if link_url:
        ok = link_url in text or post_type == "link"
        add("link", ok, "Link included" if ok else "The link is missing")
    policy = (brand.emoji_policy if brand else None) or "light"
    n = count_emojis(caption)
    if policy == "none":
        add("emoji", n == 0, f"{n} emojis (policy: none)")
    elif policy == "light":
        add("emoji", n <= LIGHT_EMOJI_MAX, f"{n} emojis (policy: at most {LIGHT_EMOJI_MAX})")
    if brand and brand.disclaimer:
        ok = brand.disclaimer.strip() in text
        add("disclaimer", ok, "Disclaimer included" if ok else "The disclaimer is missing")
    return checks


def _trim_to(text: str, limit: int) -> str:
    """Cut text to fit `limit`, preferring a sentence or paragraph end."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n\n", ". ", "! ", "? ", "\n"):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[: i + (1 if sep.strip() else 0)].rstrip()
    i = cut.rfind(" ")
    return (cut[:i] if i > limit * 0.5 else cut).rstrip() + "…"


def deterministic_fixes(draft: dict, platform: str, brand: BrandProfile | None, *, link_url: str | None,
                        post_type: str, force_length: bool = False) -> dict:
    p = platform_of(platform)
    d = dict(draft)
    d["hashtags"] = list(d.get("hashtags") or [])[: p.max_hashtags]
    policy = (brand.emoji_policy if brand else None) or "light"
    if policy == "none":
        d["text"] = _strip_emojis(d["text"], 0).strip()
    elif policy == "light" and count_emojis(d["text"]) > LIGHT_EMOJI_MAX:
        d["text"] = _strip_emojis(d["text"], LIGHT_EMOJI_MAX).strip()
    if p.title_chars and d.get("title") and len(d["title"]) > p.title_chars:
        d["title"] = _trim_to(d["title"], p.title_chars - 1).rstrip("…")[: p.title_chars]
    if p.title_chars and not d.get("title"):
        first = (d.get("text") or "").split("\n", 1)[0]
        d["title"] = _trim_to(first, p.title_chars - 1).rstrip("…")[: p.title_chars] or None
    suffix = []
    if link_url and post_type != "link" and link_url not in d["text"]:
        suffix.append(link_url)
    if brand and brand.disclaimer and brand.disclaimer.strip() not in d["text"]:
        suffix.append(brand.disclaimer.strip())
    if suffix:
        d["text"] = d["text"].rstrip() + "\n\n" + "\n\n".join(suffix)
    if force_length:
        tags_len = len(full_caption("x", d["hashtags"])) - 1 if d["hashtags"] else 0
        tail = ("\n\n" + "\n\n".join(suffix)) if suffix else ""
        body = d["text"][: len(d["text"]) - len(tail)] if tail and d["text"].endswith(tail) else d["text"]
        room = p.max_chars - tags_len - len(tail)
        if len(body) > room:
            if room < 40 and d["hashtags"]:
                d["hashtags"] = []
                room = p.max_chars - len(tail)
            d["text"] = _trim_to(body, max(room - 1, 10)) + tail
    return d


def hard_failures(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c.get("hard") and not c.get("ok")]


def _issue_text(c: dict, platform: str) -> str:
    p = platform_of(platform)
    msgs = {
        "length": f"Too long ({c['detail']}). Shorten it to fit {p.max_chars} characters including hashtags.",
        "banned_words": f"Remove banned words ({c['detail']}).",
        "placeholders": f"Remove placeholder text ({c['detail']}); write real content.",
        "emoji": f"Fix the emoji use ({c['detail']}).",
        "title": f"Fix the title ({c['detail']}).",
    }
    return msgs.get(c["name"], f"{c['name']}: {c['detail']}")


async def quality_pass(session: AsyncSession, post: Post, brand: BrandProfile, cfg: GenSettings,
                       draft: dict) -> tuple[dict, list[dict], bool]:
    """Deterministic fixes and checks, one LLM rewrite when hard checks still fail, then a forced
    length trim. Returns (draft, checks, rewritten)."""
    kw = {"link_url": cfg.link_url if cfg.include_link else None, "post_type": post.post_type}
    draft = deterministic_fixes(draft, post.platform, brand, **kw)
    checks = run_checks(draft, post.platform, brand, **kw)
    rewritten = False
    failed = hard_failures(checks)
    if failed:
        rewritten = True
        try:
            new = await write_draft(session, post, brand, cfg, fix_issues=[_issue_text(c, post.platform) for c in failed],
                                    previous=draft)
            draft = deterministic_fixes(new, post.platform, brand, **kw)
        except (ValueError, llm_router.AllProvidersFailed) as e:
            log.warning("rewrite failed: %s", e)
        checks = run_checks(draft, post.platform, brand, **kw)
        if any(c["name"] == "length" for c in hard_failures(checks)):
            draft = deterministic_fixes(draft, post.platform, brand, force_length=True, **kw)
            checks = run_checks(draft, post.platform, brand, **kw)
    return draft, checks, rewritten


async def judge(session: AsyncSession, post: Post, brand: BrandProfile, draft: dict) -> tuple[float | None, list[str]]:
    """Optional LLM-as-judge. Returns (score 0–10 or None, issues)."""
    ctx = await company_context(session, max_chars=1500)
    prompt = (
        f"Review this {platform_of(post.platform).label} post for a company.\n"
        f"Brand voice: {brand.voice or 'professional'}. Audience: {brand.audience or 'business decision makers'}.\n"
        f"Company facts (names not listed here must not appear as clients):\n{ctx or '(none)'}\n\n"
        f"Post:\n{full_caption(draft.get('text'), draft.get('hashtags'))}\n\n"
        "Score 0-10 for clarity, accuracy, brand fit and engagement. List concrete issues (invented facts or client "
        'names, vague claims, weak hook). Return JSON: {"score": <number>, "issues": ["..."]}'
    )
    try:
        data = await llm_router.complete_json(session, "qa", prompt, system="You are a strict social media editor. JSON only.",
                                              temperature=0.1, max_tokens=500)
    except llm_router.NoProviderError:
        return None, []
    except Exception as e:  # noqa: BLE001
        log.info("QA judge skipped: %s", e)
        return None, []
    if not isinstance(data, dict):
        return None, []
    score: float | None
    try:
        score = max(0.0, min(10.0, float(data.get("score"))))
    except (TypeError, ValueError):
        score = None
    issues = [str(i)[:300] for i in (data.get("issues") or []) if isinstance(i, (str, int, float))][:8]
    return score, issues


# ------------------------------------------------------------------ 4. uniqueness
def shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"\w+", (text or "").lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Similarity:
    score: float
    method: str  # embedding | jaccard
    similar_post_id: uuid.UUID | None = None
    similar_text: str | None = None
    vector: list[float] | None = None
    model: str | None = None

    @property
    def too_similar(self) -> bool:
        return self.score >= (SIMILARITY_THRESHOLD if self.method == "embedding" else JACCARD_THRESHOLD)


async def check_similarity(session: AsyncSession, post: Post, draft: dict) -> Similarity:
    text = "\n".join(filter(None, [draft.get("title"), draft.get("text")]))
    vecs, model = None, None
    try:
        vecs, model = await llm_router.embed_texts(session, [text])
    except Exception:
        log.exception("embedding failed; using word overlap instead")
    if vecs and model and vecs[0]:
        vec = vecs[0]
        since = now_utc() - timedelta(days=180)
        dist = Post.embedding.cosine_distance(vec)
        try:
            async with session.begin_nested():  # a failed vector query must not abort the transaction
                row = (await session.execute(
                    select(Post.id, Post.text, dist.label("d")).where(
                        Post.id != post.id, Post.embedding.is_not(None), Post.embedding_model == model,
                        Post.created_at >= since, Post.status.notin_(["skipped"]),
                    ).order_by(dist).limit(1)
                )).first()
        except Exception:
            log.exception("vector comparison failed")
            row = None
        if row is None:
            return Similarity(0.0, "embedding", vector=vec, model=model)
        return Similarity(max(0.0, 1.0 - float(row.d)), "embedding", row.id, row.text, vec, model)

    mine = shingles(text)
    rows = (await session.execute(
        select(Post.id, Post.title, Post.text).where(Post.id != post.id, Post.text.is_not(None),
                                                     Post.status.notin_(["skipped"]))
        .order_by(Post.created_at.desc()).limit(200)
    )).all()
    best = Similarity(0.0, "jaccard")
    for pid, title, other in rows:
        s = jaccard(mine, shingles("\n".join(filter(None, [title, other]))))
        if s > best.score:
            best = Similarity(s, "jaccard", pid, other)
    return best


# ------------------------------------------------------------------ 5. media
async def build_media(session: AsyncSession, post: Post, brand: BrandProfile) -> None:
    """Create media for posts that need it; on failure fall back to text or fail clearly."""
    if post.post_type not in NEEDS_MEDIA:
        return
    if (post.meta or {}).get("keep_media") and post.media_ids:
        return
    err: str | None = None
    try:
        from app.services import media

        assets = await media.generate_for_post(session, post, brand)
        ids = [str(a.id) for a in (assets or []) if getattr(a, "id", None)]
        if ids:
            post.media_ids = ids
            return
        err = "no media was produced"
    except ImportError:
        err = "the media studio isn't available yet"
    except Exception as e:
        log.exception("media generation failed for post %s", post.id)
        err = str(e)[:300] or type(e).__name__
    label = POST_TYPES.get(post.post_type, post.post_type).lower()
    if "text" in supported_types(post.platform):
        add_warning(post, f"Couldn't create the {label} ({err}), so this became a text post.")
        set_meta(post, original_post_type=post.post_type)
        post.post_type = "text"
        post.media_ids = []
        return
    raise GenerationError(
        f"Couldn't create the {label} for this {platform_of(post.platform).label} post: {err}. "
        "Check the image and video providers in Settings → Integrations, then press Regenerate."
    )


# ------------------------------------------------------------------ 6. route
def route(post: Post, cfg: GenSettings, *, hold_reason: str | None, now: datetime | None = None) -> None:
    now = now or now_utc()
    mode = post.mode or cfg.mode
    post.approval_deadline = None
    if mode == "manual":
        post.status = "draft"
        return
    if hold_reason and mode in ("full_auto", "auto_with_window"):
        add_warning(post, f"Held for approval: {hold_reason}")
        set_meta(post, held_from_mode=mode)
        post.mode = mode = "approve_first"
    if mode == "full_auto":
        post.status = "scheduled" if post.scheduled_at else "in_review"
        return
    post.status = "in_review"
    if mode == "auto_with_window" and post.scheduled_at:
        deadline = min(now + timedelta(hours=cfg.approval_window_hours or 4), post.scheduled_at - timedelta(minutes=5))
        post.approval_deadline = max(deadline, now)
    elif mode == "approve_first":
        post.approval_deadline = post.scheduled_at


# ------------------------------------------------------------------ the pipeline
@dataclass
class GenerateResult:
    post_id: uuid.UUID
    status: str
    notify_review: bool = False
    warnings: list[str] = field(default_factory=list)


async def generate_post(session: AsyncSession, post: Post, *, now: datetime | None = None) -> GenerateResult:
    """Run the whole pipeline on `post` (already loaded in `session`). Caller commits.

    Raises llm_router.NoProviderError when no text model exists, GenerationError for clear
    non-retryable failures, and other exceptions for retryable ones."""
    now = now or now_utc()
    schedule = await session.get(Schedule, post.schedule_id) if post.schedule_id else None
    org = await session.get(Organization, post.organization_id)
    tz = org_tz(org)
    brand = await get_brand(session, post.organization_id)
    cfg = settings_for(post, schedule)
    meta = dict(post.meta or {})
    meta.pop("warnings", None)
    post.meta = meta
    post.error = None

    # 1. plan
    await plan_post(session, post, schedule, cfg, tz)

    # 2–4. write, QA, uniqueness (regenerate when too similar)
    extra_avoid: list[str] = []
    best: tuple[dict, list[dict], bool, Similarity] | None = None
    for attempt in range(MAX_UNIQUENESS_RETRIES + 1):
        draft = await write_draft(session, post, brand, cfg, extra_avoid=extra_avoid)
        draft, checks, rewritten = await quality_pass(session, post, brand, cfg, draft)
        sim = await check_similarity(session, post, draft)
        if best is None or sim.score < best[3].score:
            best = (draft, checks, rewritten, sim)
        if not sim.too_similar:
            break
        log.info("post %s too similar (%.2f via %s), attempt %d", post.id, sim.score, sim.method, attempt + 1)
        snippet = (sim.similar_text or "")[:400].replace("\n", " ")
        extra_avoid.append(f"Do not repeat this earlier post; choose a different hook, structure and examples: \"{snippet}\"")
    assert best is not None
    draft, checks, rewritten, sim = best

    score, issues = await judge(session, post, brand, draft)
    hard_ok = not hard_failures(checks)
    post.qa = {
        "checks": [{k: c[k] for k in ("name", "ok", "detail", "hard")} for c in checks],
        "score": score, "issues": issues, "passed": hard_ok, "rewritten": rewritten,
        "similarity_method": sim.method, "checked_at": now.isoformat(),
    }
    post.text = draft["text"]
    post.hashtags = draft.get("hashtags") or []
    post.title = draft.get("title")
    post.first_comment = draft.get("first_comment")
    if cfg.include_link and cfg.link_url:
        post.link_url = cfg.link_url
    post.similarity = round(sim.score, 4)
    if sim.vector is not None and sim.model:
        post.embedding = sim.vector
        post.embedding_model = sim.model
    set_meta(post, **{k: draft.get(k) for k in ("image_brief", "alt_text", "slides", "video_script", "visual") if draft.get(k) is not None},
             similar_post_id=str(sim.similar_post_id) if sim.too_similar and sim.similar_post_id else None,
             regenerate_note=None, previous_text=None)

    hold: list[str] = []
    if not hard_ok:
        hold.append("some quality checks still fail (" + ", ".join(c["name"].replace("_", " ") for c in hard_failures(checks)) + ")")
    if sim.too_similar:
        hold.append(f"it is {round(sim.score * 100)}% similar to an earlier post")
    if score is not None and score < AUTO_MIN_SCORE:
        hold.append(f"the reviewer model scored it {score:g}/10")

    # 5. media
    await build_media(session, post, brand)

    # 6. route
    route(post, cfg, hold_reason="; ".join(hold) or None, now=now)
    add_activity(session, post, "generated",
                 f"{POST_TYPES.get(post.post_type, post.post_type)} on “{(post.topic or '')[:120]}” → {STATUSES.get(post.status, post.status)}")
    return GenerateResult(post.id, post.status, notify_review=post.status == "in_review",
                          warnings=list((post.meta or {}).get("warnings") or []))


async def recheck_post(session: AsyncSession, post: Post) -> None:
    """Re-run the deterministic checks after a human edit (keeps the judge score)."""
    brand = await get_brand(session, post.organization_id)
    draft = {"text": post.text or "", "hashtags": post.hashtags or [], "title": post.title}
    checks = run_checks(draft, post.platform, brand, link_url=None, post_type=post.post_type)
    qa = dict(post.qa or {})
    qa.update({"checks": checks, "passed": not hard_failures(checks), "edited": True,
               "checked_at": now_utc().isoformat()})
    post.qa = qa


async def account_for(session: AsyncSession, post: Post) -> SocialAccount | None:
    return await session.get(SocialAccount, post.social_account_id) if post.social_account_id else None


def review_actions(post_id: uuid.UUID | str) -> list[dict]:
    return [{"label": "Approve", "data": f"ap:{post_id}"}, {"label": "Reject", "data": f"rj:{post_id}"},
            {"label": "Regenerate", "data": f"rg:{post_id}"}]


async def notify_review(post: Post, tz: ZoneInfo | None = None) -> None:
    """Tell reviewers a post is waiting (in-app, email, Telegram with buttons). Call after commit."""
    from app.services.notify import notify_org

    tz = tz or ZoneInfo("Asia/Kolkata")
    when = post.scheduled_at.astimezone(tz).strftime("%a %d %b, %H:%M") if post.scheduled_at else "no time set"
    deadline = ""
    if post.mode == "auto_with_window" and post.approval_deadline:
        deadline = f"\nIt publishes automatically unless rejected by {post.approval_deadline.astimezone(tz).strftime('%d %b %H:%M')}."
    body = (f"{platform_of(post.platform).label} · {POST_TYPES.get(post.post_type, post.post_type)} · {when}{deadline}\n\n"
            f"{full_caption(post.text, post.hashtags)[:700]}")
    try:
        await notify_org(post.organization_id, "post_review", "A post is waiting for approval", body,
                         link=f"/approvals?post={post.id}", permission="content.approve", actions=review_actions(post.id))
    except Exception:
        log.exception("review notification failed")
