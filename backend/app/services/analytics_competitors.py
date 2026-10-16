"""Competitor benchmarks from public numbers, collected only through official means:

- Instagram: Business Discovery, read through the organization's own connected Instagram professional account
  (`/{ig-user-id}?fields=business_discovery.username(NAME){...}`). Works for business/creator accounts only.
- YouTube: channels.list / playlistItems.list / videos.list with the organization's Google connection.
- Bluesky: the public AppView (app.bsky.actor.getProfile, app.bsky.feed.getAuthorFeed).
- Mastodon: the instance's public API (accounts/lookup, accounts/:id/statuses).
- Website: PageSpeed Insights (mobile performance and SEO scores), at most once a week.

Handles live in Competitor.data["social"] = {"instagram": "...", "youtube": "...", "bluesky": "...", "mastodon": "..."}.
"""

from __future__ import annotations

import logging
import re
import statistics
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competitor, CompetitorMetric, Organization, SocialAccount
from app.services import analytics_growth as growth
from app.services.social import net
from app.services.social.common import PublishError, account_tokens, ok_json

log = logging.getLogger(__name__)

CHANNELS = ("instagram", "youtube", "bluesky", "mastodon")
CHANNEL_LABELS = {"instagram": "Instagram", "youtube": "YouTube", "bluesky": "Bluesky", "mastodon": "Mastodon",
                  "website": "Website"}
OUR_PLATFORM = {"instagram": "instagram", "youtube": "youtube", "bluesky": "bluesky", "mastodon": "mastodon"}
SAMPLE = 25
WEBSITE_EVERY_DAYS = 7

_IG_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_YT_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_YT_HANDLE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")
_BSKY_RE = re.compile(r"^(did:[a-z]+:[A-Za-z0-9._:%-]+|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$")
_MASTO_RE = re.compile(r"^@?([A-Za-z0-9_]{1,30})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


class HandleError(ValueError):
    pass


def clean_handle(channel: str, value: str | None) -> str | None:
    """Normalize what people paste (profile links, @names) into the handle each API expects."""
    v = (value or "").strip()
    if not v:
        return None
    v = re.sub(r"^https?://", "", v).rstrip("/")
    if channel == "instagram":
        v = re.sub(r"^(www\.)?instagram\.com/", "", v).split("/")[0].split("?")[0].lstrip("@")
        if not _IG_RE.match(v):
            raise HandleError("Enter an Instagram username like hrpai (letters, numbers, dots and underscores).")
        return v
    if channel == "youtube":
        v = re.sub(r"^(www\.|m\.)?youtube\.com/", "", v)
        if v.startswith("channel/"):
            v = v.split("/")[1]
        v = v.split("/")[0].split("?")[0]
        if _YT_ID.match(v):
            return v
        if _YT_HANDLE.match(v):
            return "@" + v.lstrip("@")
        raise HandleError("Enter a YouTube handle like @hrpai or a channel ID starting with UC.")
    if channel == "bluesky":
        v = re.sub(r"^bsky\.app/profile/", "", v).split("/")[0].lstrip("@")
        if not _BSKY_RE.match(v):
            raise HandleError("Enter a Bluesky handle like hrpai.bsky.social.")
        return v.lower()
    if channel == "mastodon":
        m = re.match(r"^([A-Za-z0-9.-]+)/@([A-Za-z0-9_]+)$", v)  # instance.social/@user
        if m:
            v = f"@{m.group(2)}@{m.group(1)}"
        if not _MASTO_RE.match(v):
            raise HandleError("Enter a Mastodon address like @hrpai@mastodon.social.")
        return "@" + v.lstrip("@")
    raise HandleError(f"Unknown channel {channel}.")


def handles(c: Competitor) -> dict[str, str]:
    s = (c.data or {}).get("social") or {}
    return {k: v for k, v in s.items() if k in CHANNELS and v}


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(v, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def post_stats(items: list[tuple[datetime | None, int]], now: datetime | None = None) -> dict:
    """posts_30d and average engagement per post from a recent sample [(published_at, engagement)]."""
    now = now or datetime.now(UTC)
    recent = [t for t, _ in items if t and t >= now - timedelta(days=30)]
    engs = [e for _, e in items]
    return {"posts_30d": len(recent), "avg_engagement": round(sum(engs) / len(engs), 1) if engs else None,
            "sample": len(items), "capped": len(items) >= SAMPLE and len(recent) >= len(items)}


# ------------------------------------------------------------------ collectors (one per channel)
async def our_instagram(session: AsyncSession) -> SocialAccount | None:
    return await session.scalar(select(SocialAccount).where(SocialAccount.platform == "instagram",
                                                            SocialAccount.status == "connected"))


async def ig_discovery(session: AsyncSession, c: httpx.AsyncClient, username: str) -> dict:
    from app.services.social.meta import GRAPH, check

    ours = await our_instagram(session)
    if ours is None:
        raise PublishError("Connect your Instagram professional account to compare Instagram competitors.")
    tok = account_tokens(ours).get("access_token")
    fields = (f"business_discovery.username({username}){{followers_count,media_count,"
              f"media.limit({SAMPLE}){{like_count,comments_count,timestamp,media_type}}}}")
    d = check(await c.get(f"{GRAPH}/{ours.external_id}", params={"fields": fields, "access_token": tok}),
              "Instagram business discovery")
    bd = d.get("business_discovery") or {}
    media = (bd.get("media") or {}).get("data") or []
    stats = post_stats([(_ts(m.get("timestamp")), int(m.get("like_count") or 0) + int(m.get("comments_count") or 0))
                        for m in media])
    types: dict[str, int] = {}
    for m in media:
        types[m.get("media_type") or "OTHER"] = types.get(m.get("media_type") or "OTHER", 0) + 1
    return {"followers": growth._int(bd.get("followers_count")), "posts_count": growth._int(bd.get("media_count")),
            **stats, "raw": {"media_types": types, "sample": stats["sample"], "capped": stats["capped"]}}


async def yt_channel(session: AsyncSession, c: httpx.AsyncClient, handle: str) -> dict:
    from app.services.social.google import YOUTUBE_API, google_access_token

    tok = await google_access_token(session)
    if not tok:
        raise PublishError("Connect Google to compare YouTube competitors.")
    h = {"Authorization": f"Bearer {tok}"}
    params = {"part": "statistics,contentDetails"}
    params.update({"id": handle} if handle.startswith("UC") else {"forHandle": handle})
    d = ok_json(await c.get(f"{YOUTUBE_API}/channels", params=params, headers=h), "YouTube channel")
    items = d.get("items") or []
    if not items:
        raise PublishError(f"YouTube has no channel {handle}.")
    ch = items[0]
    st = ch.get("statistics") or {}
    uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    sample: list[tuple[datetime | None, int]] = []
    if uploads:
        pl = ok_json(await c.get(f"{YOUTUBE_API}/playlistItems", params={
            "part": "contentDetails", "playlistId": uploads, "maxResults": SAMPLE}, headers=h), "YouTube uploads")
        vids = [(i.get("contentDetails") or {}) for i in pl.get("items") or []]
        ids = [v.get("videoId") for v in vids if v.get("videoId")]
        published = {v.get("videoId"): _ts(v.get("videoPublishedAt")) for v in vids}
        if ids:
            vs = ok_json(await c.get(f"{YOUTUBE_API}/videos", params={"part": "statistics", "id": ",".join(ids)}, headers=h),
                         "YouTube videos")
            for v in vs.get("items") or []:
                s = v.get("statistics") or {}
                sample.append((published.get(v.get("id")), int(s.get("likeCount") or 0) + int(s.get("commentCount") or 0)))
    stats = post_stats(sample)
    return {"followers": None if st.get("hiddenSubscriberCount") else growth._int(st.get("subscriberCount")),
            "posts_count": growth._int(st.get("videoCount")), "views": growth._int(st.get("viewCount")), **stats,
            "raw": {"channel_id": ch.get("id"), "sample": stats["sample"], "capped": stats["capped"]}}


async def bsky_actor(session: AsyncSession, c: httpx.AsyncClient, handle: str) -> dict:
    prof = await growth.bluesky_profile(c, handle)
    feed = ok_json(await c.get(f"{growth.BLUESKY_PUBLIC}/xrpc/app.bsky.feed.getAuthorFeed", params={
        "actor": handle, "limit": SAMPLE, "filter": "posts_no_replies"}), "Bluesky posts")
    sample = []
    for it in feed.get("feed") or []:
        if it.get("reason"):  # a repost of someone else
            continue
        p = it.get("post") or {}
        rec = p.get("record") or {}
        e = sum(int(p.get(k) or 0) for k in ("likeCount", "replyCount", "repostCount", "quoteCount"))
        sample.append((_ts(rec.get("createdAt") or p.get("indexedAt")), e))
    stats = post_stats(sample)
    return {"followers": growth._int(prof.get("followersCount")), "posts_count": growth._int(prof.get("postsCount")), **stats,
            "raw": {"did": prof.get("did"), "sample": stats["sample"], "capped": stats["capped"]}}


async def _instance(host: str) -> str:
    from app.services.crawler import UnsafeURLError, check_public_url

    try:
        return (await check_public_url(f"https://{host}")).rstrip("/")
    except UnsafeURLError as e:
        raise PublishError(f"That Mastodon server isn't allowed: {e}") from e


async def masto_account(session: AsyncSession, c: httpx.AsyncClient, handle: str) -> dict:
    m = _MASTO_RE.match(handle)
    if not m:
        raise PublishError("Mastodon handles look like @name@instance.social.")
    user, host = m.group(1), m.group(2)
    inst = await _instance(host)
    acc = ok_json(await c.get(f"{inst}/api/v1/accounts/lookup", params={"acct": user}), "Mastodon profile")
    sts = await c.get(f"{inst}/api/v1/accounts/{acc['id']}/statuses",
                      params={"limit": SAMPLE, "exclude_reblogs": "true", "exclude_replies": "true"})
    rows = sts.json() if sts.is_success else []
    sample = [(_ts(s.get("created_at")),
               int(s.get("favourites_count") or 0) + int(s.get("replies_count") or 0) + int(s.get("reblogs_count") or 0))
              for s in rows if isinstance(s, dict)]
    stats = post_stats(sample)
    return {"followers": growth._int(acc.get("followers_count")), "posts_count": growth._int(acc.get("statuses_count")),
            **stats, "raw": {"id": acc.get("id"), "sample": stats["sample"], "capped": stats["capped"]}}


FETCHERS = {"instagram": ig_discovery, "youtube": yt_channel, "bluesky": bsky_actor, "mastodon": masto_account}


async def website_scores(session: AsyncSession, url: str) -> dict:
    """PageSpeed mobile performance and SEO scores for a public site."""
    from app.services import analytics as base

    params = [("url", url), ("strategy", "mobile"), ("category", "performance"), ("category", "seo")]
    key = await base.pagespeed_key(session)
    if key:
        params.append(("key", key))
    async with base.http_client(timeout=120.0) as http:
        r = await http.get(base.PAGESPEED_RUN, params=params)
    if r.status_code >= 400:
        raise PublishError(f"PageSpeed: {r.text[:200]}")
    parsed = base.parse_pagespeed(r.json())
    return {"score": parsed["scores"].get("performance"), "raw": {"scores": parsed["scores"], "url": url}}


async def _upsert(session: AsyncSession, org_id: uuid.UUID, comp: Competitor, channel: str, handle: str | None, day: date,
                  values: dict | None, error: str | None) -> CompetitorMetric:
    row = await session.scalar(select(CompetitorMetric).where(
        CompetitorMetric.competitor_id == comp.id, CompetitorMetric.channel == channel, CompetitorMetric.day == day))
    if row is None:
        row = CompetitorMetric(organization_id=org_id, competitor_id=comp.id, channel=channel, day=day)
        session.add(row)
    values = values or {}
    row.handle = handle
    for f in ("followers", "posts_count", "posts_30d", "avg_engagement", "views", "score"):
        setattr(row, f, values.get(f))
    row.raw, row.error, row.collected_at = values.get("raw") or {}, error, datetime.now(UTC)
    return row


async def collect_competitors(session: AsyncSession, org_id: uuid.UUID, *, websites: bool | None = None) -> dict:
    org = await session.get(Organization, org_id)
    day = growth.local_today(org)
    comps = (await session.scalars(select(Competitor).where(Competitor.status == "confirmed"))).all()
    done, failed = 0, []
    async with net.client() as c:
        for comp in comps:
            for ch, h in handles(comp).items():
                try:
                    vals = await FETCHERS[ch](session, c, h)
                    await _upsert(session, org_id, comp, ch, h, day, vals, None)
                    done += 1
                except (PublishError, httpx.HTTPError, KeyError, ValueError) as e:
                    msg = e.message if isinstance(e, PublishError) else f"{type(e).__name__}: {e}"
                    await _upsert(session, org_id, comp, ch, h, day, None, msg[:500])
                    failed.append({"competitor": comp.name, "channel": ch, "error": msg[:300]})
            if comp.website:
                last = await session.scalar(select(CompetitorMetric.day).where(
                    CompetitorMetric.competitor_id == comp.id, CompetitorMetric.channel == "website",
                    CompetitorMetric.error.is_(None)).order_by(CompetitorMetric.day.desc()))
                due = websites if websites is not None else (last is None or last <= day - timedelta(days=WEBSITE_EVERY_DAYS))
                if due:
                    try:
                        vals = await website_scores(session, comp.website)
                        await _upsert(session, org_id, comp, "website", comp.website, day, vals, None)
                    except (PublishError, httpx.HTTPError, ValueError) as e:
                        failed.append({"competitor": comp.name, "channel": "website", "error": str(e)[:300]})
    await session.flush()
    return {"competitors": len(comps), "collected": done, "failed": failed}


# ------------------------------------------------------------------ benchmark view
async def benchmark(session: AsyncSession, org: Organization) -> dict:
    today = growth.local_today(org)
    now = datetime.now(UTC)
    comps = (await session.scalars(select(Competitor).where(Competitor.status == "confirmed").order_by(Competitor.name))).all()
    mets = (await session.scalars(select(CompetitorMetric).where(CompetitorMetric.day >= today - timedelta(days=120))
                                  .order_by(CompetitorMetric.day))).all()
    by_comp: dict[uuid.UUID, dict[str, list[CompetitorMetric]]] = {}
    for m in mets:
        by_comp.setdefault(m.competitor_id, {}).setdefault(m.channel, []).append(m)

    def channel_row(rows: list[CompetitorMetric]) -> dict:
        ok = [r for r in rows if r.error is None]
        last_any = rows[-1] if rows else None
        last = ok[-1] if ok else None
        before = growth._value_on([(r.day, r.followers) for r in ok], today - timedelta(days=30))
        return {
            "followers": last.followers if last else None, "posts_30d": last.posts_30d if last else None,
            "avg_engagement": last.avg_engagement if last else None, "posts_count": last.posts_count if last else None,
            "score": last.score if last else None, "views": last.views if last else None,
            "net_30d": (last.followers - before) if last and last.followers is not None and before is not None else None,
            "engagement_rate": round(last.avg_engagement / last.followers, 4) if last and last.avg_engagement is not None and last.followers else None,
            "capped": bool((last.raw or {}).get("capped")) if last else False,
            "updated": last.day.isoformat() if last else None,
            "error": last_any.error if last_any and last_any.error else None,
        }

    # our own numbers per channel
    accounts = await growth.tracked_accounts(session)
    series = await growth.account_series(session, today - timedelta(days=60))
    post_rows = await growth.post_rows(session, now - timedelta(days=30), now)
    ours: dict[str, dict] = {}
    for ch in (*CHANNELS, "all"):
        accs = [a for a in accounts if ch == "all" or a.platform == OUR_PLATFORM.get(ch)]
        fol, net, n = 0, 0, 0
        has_net = False
        for a in accs:
            pts = [(r.day, r.followers) for r in series.get(a.id, [])]
            cur = growth._value_on(pts, today)
            if cur is not None:
                fol += cur
                n += 1
                b = growth._value_on(pts, today - timedelta(days=30))
                if b is not None:
                    net += cur - b
                    has_net = True
        plats = {a.platform for a in accs}
        rows = [(p, m) for p, m in post_rows if ch == "all" or p.platform in plats]
        with_m = [(p, m) for p, m in rows if m]
        avg = round(sum(growth._eng(m) for _, m in with_m) / len(with_m), 1) if with_m else None
        ours[ch] = {"followers": fol if n else None, "posts_30d": len(rows) if accs or ch == "all" else None,
                    "avg_engagement": avg, "net_30d": net if has_net else None,
                    "engagement_rate": round(avg / fol, 4) if avg is not None and n and fol else None,
                    "connected": bool(accs)}
    own_site = await _own_website_score(session)
    ours["website"] = {"score": own_site}

    entities = [{"id": "us", "name": org.name if org else "You", "is_us": True, "channels": ours,
                 "website": org.website if org else None}]
    for comp in comps:
        chs = {ch: channel_row(rows) for ch, rows in by_comp.get(comp.id, {}).items()}
        tracked = [ch for ch in CHANNELS if ch in handles(comp)]
        agg_ch = [chs[ch] for ch in tracked if ch in chs]
        fol = [x["followers"] for x in agg_ch if x["followers"] is not None]
        p30 = [x["posts_30d"] for x in agg_ch if x["posts_30d"] is not None]
        avgs = [x["avg_engagement"] for x in agg_ch if x["avg_engagement"] is not None]
        nets = [x["net_30d"] for x in agg_ch if x["net_30d"] is not None]
        chs["all"] = {"followers": sum(fol) if fol else None, "posts_30d": sum(p30) if p30 else None,
                      "avg_engagement": round(statistics.mean(avgs), 1) if avgs else None,
                      "net_30d": sum(nets) if nets else None,
                      "engagement_rate": round(statistics.mean(avgs) / sum(fol), 4) if avgs and fol and sum(fol) else None}
        entities.append({"id": str(comp.id), "name": comp.name, "is_us": False, "channels": chs,
                         "website": comp.website, "handles": handles(comp)})

    by_channel = {}
    for ch in (*CHANNELS, "all"):
        rows = []
        for e in entities:
            r = e["channels"].get(ch)
            if not r or (not e["is_us"] and ch != "all" and ch not in (e.get("handles") or {})):
                continue
            if e["is_us"] and ch != "all" and not r.get("connected"):
                continue
            rows.append({"id": e["id"], "name": e["name"], "is_us": e["is_us"], **r})
        by_channel[ch] = {"rows": rows, "share_of_voice": share_of_voice(rows), "insights": lead_lag(rows)}
    website_rows = [{"id": e["id"], "name": e["name"], "is_us": e["is_us"],
                     "score": (e["channels"].get("website") or {}).get("score")} for e in entities]
    return {
        "competitors": [{"id": str(c.id), "name": c.name, "website": c.website, "handles": handles(c),
                         "overlap_score": c.overlap_score} for c in comps],
        "channels": by_channel, "website": website_rows,
        "can_instagram": await our_instagram(session) is not None,
        "updated": max((m.collected_at for m in mets), default=None),
    }


async def _own_website_score(session: AsyncSession) -> int | None:
    from app.services import analytics as base

    snap = await base.latest(session, "pagespeed")
    if snap is None:
        return None
    return (((snap.data or {}).get("mobile") or {}).get("scores") or {}).get("performance")


def share_of_voice(rows: list[dict]) -> list[dict]:
    """Each brand's share of the group's engagement (avg engagement × posts in 30 days), followers and posts."""
    def tot(key):
        return sum(r.get(key) or 0 for r in rows)

    eng = {r["id"]: (r.get("avg_engagement") or 0) * (r.get("posts_30d") or 0) for r in rows}
    te, tf, tp = sum(eng.values()), tot("followers"), tot("posts_30d")
    return [{"id": r["id"], "name": r["name"], "is_us": r["is_us"],
             "engagement_share": round(eng[r["id"]] / te, 4) if te else None,
             "followers_share": round((r.get("followers") or 0) / tf, 4) if tf else None,
             "posts_share": round((r.get("posts_30d") or 0) / tp, 4) if tp else None} for r in rows]


METRICS = [
    ("followers", "Audience size", "followers"),
    ("posts_30d", "Posting frequency", "posts in the last 30 days"),
    ("avg_engagement", "Engagement per post", "average likes, comments and shares per post"),
    ("net_30d", "Audience growth", "followers gained in 30 days"),
]


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}"
    return f"{int(v):,}"


def lead_lag(rows: list[dict]) -> dict:
    """Plain-language 'where you lead / where you lag' statements for one channel."""
    us = next((r for r in rows if r["is_us"]), None)
    others = [r for r in rows if not r["is_us"]]
    lead, lag = [], []
    if us is None or not others:
        return {"lead": lead, "lag": lag}
    for key, label, unit in METRICS:
        mine = us.get(key)
        vals = [(r[key], r["name"]) for r in others if r.get(key) is not None]
        if mine is None or not vals:
            continue
        best_v, best_n = max(vals)
        med = statistics.median(v for v, _ in vals)
        if mine >= best_v:
            lead.append({"metric": key, "label": label,
                         "text": f"{label}: you're ahead with {_fmt(mine)} {unit} (best competitor: {best_n}, {_fmt(best_v)})."})
        elif mine < med:
            lag.append({"metric": key, "label": label,
                        "text": f"{label}: you have {_fmt(mine)} {unit}; {best_n} leads with {_fmt(best_v)} "
                                f"and the typical competitor has {_fmt(med)}."})
    return {"lead": lead, "lag": lag}

