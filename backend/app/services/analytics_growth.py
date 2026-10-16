"""Growth trends: a daily snapshot of every connected social account (followers, posts, reach, ...) and the
views built on top of it (followers over time, net growth, period-over-period deltas, posts per week, engagement trend).

Collectors use only official, documented endpoints and the tokens the adapters already store, so no new sign-in
scopes are needed. All HTTP goes through app.services.social.net.client (patched in tests). A collector returns
None for a number the platform doesn't share; the UI shows those as "not available" with the reason in `notes`.

Endpoint versions live in the adapters (meta.GRAPH_VERSION, threads.THREADS_VERSION, linkedin.LINKEDIN_VERSION).
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountMetric, Organization, Post, PostMetric, SocialAccount
from app.services.platforms import PLATFORMS
from app.services.social import net
from app.services.social.common import PublishError, account_tokens, ok_json

log = logging.getLogger(__name__)

BLUESKY_PUBLIC = "https://public.api.bsky.app"  # Bluesky's public AppView (no sign-in needed for profiles)

# Meta renames insights metrics from time to time; keep the names in one place.
FB_PAGE_INSIGHTS = {"page_impressions_unique": "reach", "page_post_engagements": "engagement", "page_views_total": "profile_views"}
IG_ACCOUNT_INSIGHTS = {"reach": "reach", "profile_views": "profile_views", "accounts_engaged": "engagement", "views": "impressions"}
THREADS_INSIGHTS = "views,likes,replies,reposts,quotes,followers_count"

METRIC_FIELDS = ("followers", "following", "posts_count", "reach", "impressions", "profile_views", "engagement", "views")

# Platforms whose account numbers the official APIs don't expose to us, with the reason shown in the app.
UNSUPPORTED: dict[str, str] = {
    "linkedin_person": "LinkedIn doesn't share follower counts for personal profiles through its API.",
    "gbp": "Google Business Profile has no followers; profile views aren't collected yet.",
    "x": "X (Twitter) is on hold, so its numbers aren't collected.",
    "manual": "Accounts you post to by hand don't report numbers.",
}
SKIP_PLATFORMS = {"google"}  # the hidden Google connection holder


@dataclass
class Snapshot:
    values: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _int(v) -> int | None:
    try:
        return None if v is None or v == "" else int(float(v))
    except (TypeError, ValueError):
        return None


def insight_value(d: dict) -> int | None:
    """Last value of a Graph insights response: data[0].total_value.value or data[0].values[-1].value."""
    items = d.get("data") or []
    if not items:
        return None
    it = items[0]
    tv = it.get("total_value")
    if isinstance(tv, dict) and tv.get("value") is not None:
        return _int(tv.get("value"))
    vals = it.get("values") or []
    for v in reversed(vals):
        if isinstance(v, dict) and v.get("value") is not None and not isinstance(v.get("value"), dict):
            return _int(v.get("value"))
    return None


# ------------------------------------------------------------------ per-platform collectors
async def _facebook(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.meta import GRAPH, check

    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    page = account.external_id
    d = check(await c.get(f"{GRAPH}/{page}", params={"fields": "followers_count,fan_count", "access_token": tok}),
              "Facebook page")
    snap = Snapshot(values={"followers": _int(d.get("followers_count") if d.get("followers_count") is not None else d.get("fan_count"))},
                    raw={"page": d, "insights": {}})
    missing = []
    for metric, key in FB_PAGE_INSIGHTS.items():
        try:
            r = check(await c.get(f"{GRAPH}/{page}/insights", params={"metric": metric, "period": "day", "access_token": tok}),
                      "Facebook insights")
            snap.raw["insights"][metric] = r.get("data")
            snap.values[key] = insight_value(r)
        except PublishError:
            missing.append(metric)
    if missing:
        snap.notes.append("Facebook didn't return page insights (" + ", ".join(missing) + "). Reach and engagement need the "
                          "read_insights permission and a page with at least 100 likes.")
    return snap


async def _instagram(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.meta import GRAPH, check

    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    ig = account.external_id
    d = check(await c.get(f"{GRAPH}/{ig}", params={"fields": "followers_count,follows_count,media_count", "access_token": tok}),
              "Instagram profile")
    snap = Snapshot(values={"followers": _int(d.get("followers_count")), "following": _int(d.get("follows_count")),
                            "posts_count": _int(d.get("media_count"))}, raw={"profile": d, "insights": {}})
    missing = []
    for metric, key in IG_ACCOUNT_INSIGHTS.items():
        try:
            r = check(await c.get(f"{GRAPH}/{ig}/insights", params={
                "metric": metric, "period": "day", "metric_type": "total_value", "access_token": tok}), "Instagram insights")
            snap.raw["insights"][metric] = r.get("data")
            snap.values[key] = insight_value(r)
        except PublishError:
            missing.append(metric)
    if missing:
        snap.notes.append("Instagram didn't return " + ", ".join(missing) + ". Account insights need the "
                          "instagram_manage_insights permission (reconnect Facebook with Instagram ticked).")
    return snap


async def _threads(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.meta import check
    from app.services.social.threads import API

    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    until = datetime.now(UTC)
    since = until - timedelta(days=1)
    d = check(await c.get(f"{API}/{account.external_id}/threads_insights", params={
        "metric": THREADS_INSIGHTS, "since": int(since.timestamp()), "until": int(until.timestamp()), "access_token": tok}),
        "Threads insights")
    vals: dict[str, int] = {}
    for m in d.get("data") or []:
        tv = (m.get("total_value") or {}).get("value")
        if tv is not None:
            vals[m.get("name")] = _int(tv) or 0
        else:
            vals[m.get("name")] = sum(_int(v.get("value")) or 0 for v in m.get("values") or [] if isinstance(v, dict))
    eng = sum(vals.get(k, 0) for k in ("likes", "replies", "reposts", "quotes"))
    return Snapshot(values={"followers": vals.get("followers_count"), "impressions": vals.get("views"),
                            "engagement": eng if any(k in vals for k in ("likes", "replies", "reposts", "quotes")) else None},
                    raw=d)


async def _youtube(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.google import YOUTUBE_API, google_access_token

    tok = await google_access_token(session)
    if not tok:
        raise PublishError("Google isn't connected. Connect Google in Settings → Social accounts.", reconnect=True)
    d = ok_json(await c.get(f"{YOUTUBE_API}/channels", params={"part": "statistics", "id": account.external_id},
                            headers={"Authorization": f"Bearer {tok}"}), "YouTube channel statistics")
    st = ((d.get("items") or [{}])[0]).get("statistics") or {}
    snap = Snapshot(values={"followers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
                            "posts_count": _int(st.get("videoCount")), "views": _int(st.get("viewCount"))}, raw=d)
    if st.get("hiddenSubscriberCount"):
        snap.notes.append("This channel hides its subscriber count.")
    return snap


async def _pinterest(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.pinterest import api_base

    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    d = ok_json(await c.get(f"{api_base(account)}/user_account", headers={"Authorization": f"Bearer {tok}"}),
                "Pinterest profile")
    snap = Snapshot(values={"followers": _int(d.get("follower_count")), "following": _int(d.get("following_count")),
                            "posts_count": _int(d.get("pin_count")), "views": _int(d.get("monthly_views"))}, raw=d)
    if d.get("monthly_views") is None:
        snap.notes.append("Pinterest shares monthly views only for business accounts.")
    return snap


async def bluesky_profile(c: httpx.AsyncClient, actor: str) -> dict:
    return ok_json(await c.get(f"{BLUESKY_PUBLIC}/xrpc/app.bsky.actor.getProfile", params={"actor": actor}),
                   "Bluesky profile")


async def _bluesky(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    d = await bluesky_profile(c, account.external_id or account.handle or "")
    return Snapshot(values={"followers": _int(d.get("followersCount")), "following": _int(d.get("followsCount")),
                            "posts_count": _int(d.get("postsCount"))},
                    raw={k: d.get(k) for k in ("did", "handle", "followersCount", "followsCount", "postsCount")})


async def _mastodon(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.mastodon import _auth, _ctx

    inst, tok = _ctx(account)
    d = ok_json(await c.get(f"{inst}/api/v1/accounts/verify_credentials", headers=_auth(tok)), "Mastodon profile")
    return Snapshot(values={"followers": _int(d.get("followers_count")), "following": _int(d.get("following_count")),
                            "posts_count": _int(d.get("statuses_count"))},
                    raw={k: d.get(k) for k in ("id", "acct", "followers_count", "following_count", "statuses_count")})


async def _linkedin_org(session, account: SocialAccount, c: httpx.AsyncClient) -> Snapshot:
    from app.services.social.linkedin import REST, author_urn, headers

    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    urn = author_urn(account)
    r = await c.get(f"{REST}/networkSizes/{quote(urn, safe='')}", params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
                    headers=headers(tok, False))
    if r.status_code == 403:
        return Snapshot(notes=["LinkedIn didn't allow reading this page's follower count. It needs the Community "
                               "Management API with the r_organization_admin permission."])
    d = ok_json(r, "LinkedIn followers")
    return Snapshot(values={"followers": _int(d.get("firstDegreeSize"))}, raw=d,
                    notes=["LinkedIn page reach and engagement come from your posts' statistics."])


COLLECTORS = {
    "facebook_page": _facebook, "instagram": _instagram, "threads": _threads, "youtube": _youtube,
    "pinterest": _pinterest, "bluesky": _bluesky, "mastodon": _mastodon, "linkedin_org": _linkedin_org,
}


async def collect_account(session: AsyncSession, account: SocialAccount) -> Snapshot | None:
    """Numbers for one account, or None when the platform isn't covered. Raises PublishError on API failure."""
    fn = COLLECTORS.get(account.platform)
    if fn is None:
        return None
    async with net.client() as c:
        return await fn(session, account, c)


# ------------------------------------------------------------------ storing
def org_zone(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo((org.timezone if org else None) or "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def local_today(org: Organization | None) -> date:
    return datetime.now(org_zone(org)).date()


async def tracked_accounts(session: AsyncSession) -> list[SocialAccount]:
    rows = (await session.scalars(select(SocialAccount).where(SocialAccount.status != "removed")
                                  .order_by(SocialAccount.platform, SocialAccount.name))).all()
    return [a for a in rows if a.platform not in SKIP_PLATFORMS]


async def upsert_metric(session: AsyncSession, org_id: uuid.UUID, account: SocialAccount, day: date, snap: Snapshot) -> AccountMetric:
    row = await session.scalar(select(AccountMetric).where(AccountMetric.social_account_id == account.id,
                                                           AccountMetric.day == day))
    if row is None:
        row = AccountMetric(organization_id=org_id, social_account_id=account.id, platform=account.platform, day=day)
        session.add(row)
    for f in METRIC_FIELDS:
        setattr(row, f, snap.values.get(f))
    row.raw, row.notes, row.collected_at = snap.raw or {}, snap.notes, datetime.now(UTC)
    return row


async def collect_accounts(session: AsyncSession, org_id: uuid.UUID) -> dict:
    """Snapshot every connected account for today's local date. Returns a summary for the job result."""
    org = await session.get(Organization, org_id)
    day = local_today(org)
    done, skipped, failed = [], [], []
    for acc in await tracked_accounts(session):
        if acc.status in ("manual",) or acc.platform in UNSUPPORTED:
            skipped.append({"account": acc.name, "reason": UNSUPPORTED.get(acc.platform, "Not connected to an API.")})
            continue
        try:
            snap = await collect_account(session, acc)
        except PublishError as e:
            failed.append({"account": acc.name, "error": e.message})
            continue
        except httpx.HTTPError as e:
            failed.append({"account": acc.name, "error": f"Network error: {e}"})
            continue
        if snap is None:
            skipped.append({"account": acc.name, "reason": "This platform isn't covered yet."})
            continue
        await upsert_metric(session, org_id, acc, day, snap)
        done.append(acc.name)
    await session.flush()
    return {"day": day.isoformat(), "collected": done, "skipped": skipped, "failed": failed}


# ------------------------------------------------------------------ views
def _value_on(series: list[tuple[date, int | None]], day: date) -> int | None:
    """Latest known value on or before `day` (series sorted by day)."""
    best = None
    for d, v in series:
        if d > day:
            break
        if v is not None:
            best = v
    return best


def growth_rate(now: int | None, before: int | None) -> float | None:
    if now is None or before is None or before <= 0:
        return None
    return round((now - before) * 100 / before, 2)


def _eng(m: PostMetric | None) -> int:
    return 0 if m is None else int(m.likes + m.comments + m.shares + m.saves + m.clicks)


async def post_rows(session: AsyncSession, start: datetime, end: datetime) -> list[tuple[Post, PostMetric | None]]:
    posts = (await session.scalars(select(Post).where(
        Post.status == "published", Post.published_at.is_not(None), Post.published_at >= start, Post.published_at < end))).all()
    ids = [p.id for p in posts]
    mets: dict[uuid.UUID, PostMetric] = {}
    if ids:
        rows = (await session.scalars(select(PostMetric).where(PostMetric.post_id.in_(ids))
                                      .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc()))).all()
        mets = {m.post_id: m for m in rows}
    return [(p, mets.get(p.id)) for p in posts]


def window_totals(rows: list[tuple[Post, PostMetric | None]]) -> dict:
    imp = sum(int(m.impressions or 0) for _, m in rows if m)
    eng = sum(_eng(m) for _, m in rows)
    return {"posts": len(rows), "impressions": imp, "engagements": eng,
            "engagement_rate": round(eng / imp, 4) if imp else None,
            "with_metrics": sum(1 for _, m in rows if m)}


def pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or not prev:
        return None
    return round((cur - prev) * 100 / prev, 1)


async def account_series(session: AsyncSession, since: date) -> dict[uuid.UUID, list[AccountMetric]]:
    rows = (await session.scalars(select(AccountMetric).where(AccountMetric.day >= since)
                                  .order_by(AccountMetric.social_account_id, AccountMetric.day))).all()
    out: dict[uuid.UUID, list[AccountMetric]] = defaultdict(list)
    for r in rows:
        out[r.social_account_id].append(r)
    return out


def followers_at(series: dict[uuid.UUID, list[AccountMetric]], day: date) -> tuple[int | None, int]:
    """Total followers on `day` across accounts that have a value then, and how many accounts contributed."""
    total, n = 0, 0
    for rows in series.values():
        v = _value_on([(r.day, r.followers) for r in rows], day)
        if v is not None:
            total += v
            n += 1
    return (total if n else None), n


def net_followers(series: dict[uuid.UUID, list[AccountMetric]], start: date, end: date) -> int | None:
    """Followers gained between two days, counting only accounts with a value on both days."""
    gained, n = 0, 0
    for rows in series.values():
        pts = [(r.day, r.followers) for r in rows]
        a, b = _value_on(pts, start), _value_on(pts, end)
        if a is not None and b is not None:
            gained += b - a
            n += 1
    return gained if n else None


async def growth(session: AsyncSession, org: Organization, days: int = 30) -> dict:
    zone = org_zone(org)
    today = local_today(org)
    start_day = today - timedelta(days=days)
    hist_from = today - timedelta(days=max(days, 90) * 2 + 1)
    series = await account_series(session, hist_from)
    accounts = await tracked_accounts(session)
    now = datetime.now(UTC)

    acc_out = []
    for a in accounts:
        rows = series.get(a.id, [])
        pts = [(r.day, r.followers) for r in rows]
        latest = rows[-1] if rows else None
        deltas = {}
        for n in (7, 30, 90):
            before = _value_on(pts, today - timedelta(days=n))
            cur = _value_on(pts, today)
            deltas[str(n)] = {"net": (cur - before) if cur is not None and before is not None else None,
                              "rate": growth_rate(cur, before)}
        acc_out.append({
            "id": str(a.id), "name": a.name, "platform": a.platform,
            "platform_label": PLATFORMS[a.platform].label if a.platform in PLATFORMS else a.platform,
            "handle": a.handle, "status": a.status,
            "supported": a.platform in COLLECTORS and a.status != "manual",
            "unsupported_reason": UNSUPPORTED.get(a.platform),
            "latest": {f: getattr(latest, f) for f in METRIC_FIELDS} if latest else None,
            "latest_day": latest.day.isoformat() if latest else None,
            "notes": (latest.notes if latest else []) or [],
            "deltas": deltas,
            "series": [{"day": r.day.isoformat(), "followers": r.followers, "reach": r.reach, "impressions": r.impressions,
                        "engagement": r.engagement, "posts_count": r.posts_count}
                       for r in rows if r.day >= start_day],
        })

    # total followers by day (carry forward each account's last known value)
    labels = [start_day + timedelta(days=i) for i in range(days + 1)]
    total_series = []
    for d in labels:
        v, n = followers_at(series, d)
        total_series.append({"day": d.isoformat(), "followers": v, "accounts": n})

    windows = {}
    for n in (7, 30, 90):
        cur_rows = await post_rows(session, now - timedelta(days=n), now)
        prev_rows = await post_rows(session, now - timedelta(days=2 * n), now - timedelta(days=n))
        cur, prev = window_totals(cur_rows), window_totals(prev_rows)
        fol_cur = net_followers(series, today - timedelta(days=n), today)
        fol_prev = net_followers(series, today - timedelta(days=2 * n), today - timedelta(days=n))
        f_now, _ = followers_at(series, today)
        f_then, _ = followers_at(series, today - timedelta(days=n))
        windows[str(n)] = {
            "current": {**cur, "followers_net": fol_cur, "growth_rate": growth_rate(f_now, f_then)},
            "previous": {**prev, "followers_net": fol_prev},
            "deltas": {k: pct(cur.get(k), prev.get(k)) for k in ("posts", "impressions", "engagements", "engagement_rate")}
            | {"followers_net": pct(fol_cur, fol_prev)},
        }

    # posts per week and weekly engagement rate over the chosen period
    rows = await post_rows(session, now - timedelta(days=days), now)
    weeks: dict[date, list] = defaultdict(list)
    for p, m in rows:
        local = p.published_at.astimezone(zone).date()
        weeks[local - timedelta(days=local.weekday())].append((p, m))
    first_week = start_day - timedelta(days=start_day.weekday())
    week_out = []
    w = first_week
    while w <= today:
        t = window_totals(weeks.get(w, []))
        by_plat: dict[str, int] = defaultdict(int)
        for p, _ in weeks.get(w, []):
            by_plat[p.platform] += 1
        week_out.append({"week": w.isoformat(), **t, "by_platform": dict(by_plat)})
        w += timedelta(days=7)

    # platform comparison
    plat: dict[str, dict] = {}
    for a in acc_out:
        p = plat.setdefault(a["platform"], {"platform": a["platform"], "label": a["platform_label"], "accounts": 0,
                                            "followers": None, "net": None, "posts": 0, "impressions": 0, "engagements": 0})
        p["accounts"] += 1
        f = (a["latest"] or {}).get("followers")
        if f is not None:
            p["followers"] = (p["followers"] or 0) + f
        net = a["deltas"][str(min(days, 90))]["net"] if str(min(days, 90)) in a["deltas"] else None
        if net is not None:
            p["net"] = (p["net"] or 0) + net
    for pst, m in rows:
        p = plat.setdefault(pst.platform, {"platform": pst.platform, "label": PLATFORMS[pst.platform].label if pst.platform in PLATFORMS else pst.platform,
                                           "accounts": 0, "followers": None, "net": None, "posts": 0, "impressions": 0, "engagements": 0})
        p["posts"] += 1
        p["impressions"] += int(m.impressions or 0) if m else 0
        p["engagements"] += _eng(m)
    for p in plat.values():
        p["engagement_rate"] = round(p["engagements"] / p["impressions"], 4) if p["impressions"] else None
        p["growth_rate"] = growth_rate(p["followers"], (p["followers"] - p["net"]) if p["followers"] is not None and p["net"] is not None else None)

    from app.services import analytics as base

    social = await base.social_performance(session, days, org.timezone if org else "Asia/Kolkata")
    last = max((r.collected_at for rs in series.values() for r in rs), default=None)
    f_now, n_now = followers_at(series, today)
    return {
        "days": days, "timezone": str(zone), "today": today.isoformat(),
        "last_collected_at": last.isoformat() if last else None,
        "totals": {"followers": f_now, "accounts_with_followers": n_now,
                   "net": windows[str(days)]["current"]["followers_net"] if str(days) in windows else net_followers(series, start_day, today),
                   "growth_rate": windows[str(days)]["current"]["growth_rate"] if str(days) in windows else None},
        "accounts": acc_out, "followers_series": total_series, "windows": windows, "weeks": week_out,
        "platforms": sorted(plat.values(), key=lambda p: -(p["followers"] or 0)),
        "heatmap": social["heatmap"], "best_times": social["best_times"], "best_times_message": social["best_times_message"],
        "top_posts": social["top_posts"], "by_post_type": social["by_post_type"], "by_pillar": social["by_pillar"],
        "social_totals": social["totals"],
    }
