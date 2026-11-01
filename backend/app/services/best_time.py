"""Best time to post, learned from the organization's own results.

For every social account we score each weekday × hour (in the organization's time zone) from the posts it
published in the last 90 days:

* engagement of a post = likes + comments + shares + saves + clicks from its latest metrics (the same numbers the
  analytics pages use, see analytics.engagements), divided by impressions when the platform reports impressions
  for most of the posts (an engagement rate), otherwise the raw engagement count;
* a bucket's score is a weighted mean of the posts in it and around it: same hour ×1, ±1 hour ×0.5, the same
  hour on other weekdays ×0.25 and ±1 hour on other weekdays ×0.125, so a handful of posts still says something
  (shrunk towards the overall mean by one pseudo-post, so buckets with more evidence win);
* scores are scaled to 0..1 and blended with conservative platform defaults (below). The blend weight is
  0.85 × confidence, where confidence = posts with metrics / 10 (capped at 1). With no data the defaults decide;
  even with plenty of data the defaults keep a small say, which breaks ties between hours never tried.

When an account has fewer than 10 posts with metrics but other accounts on the same platform have more, the
platform's pooled posts are used instead (source "platform").

Platform defaults are deliberately cautious, widely reported patterns (local time): business networks on weekday
mornings and lunchtime, consumer networks at lunchtime and in the evening, weekends quieter for B2B. They are a
starting point only and fade out as the organization's own results come in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

WINDOW_DAYS = 90
MIN_POSTS = 10            # posts with metrics before the organization's own data is fully trusted
OWN_WEIGHT = 0.85         # the most the organization's data can weigh against the defaults
DAY_START, DAY_END = 8, 21  # best-time slots are picked between 08:00 and 21:00 local time
MIN_GAP_HOURS = 3         # best-time slots on the same day are at least this far apart
MAX_PER_DAY = 5           # 08, 11, 14, 17, 20 is the most that fits
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# (weekday weights Mon..Sun, hour weights; hours not listed get BASE_HOUR)
BASE_HOUR = 0.15
_B2B_DAYS = [0.85, 1.0, 1.0, 0.95, 0.75, 0.3, 0.25]
_CONSUMER_DAYS = [0.85, 0.9, 0.95, 0.95, 0.9, 0.8, 0.75]
DEFAULTS: dict[str, tuple[list[float], dict[int, float]]] = {
    "linkedin": (_B2B_DAYS, {8: 0.85, 9: 1.0, 10: 0.95, 11: 0.75, 12: 0.8, 13: 0.6, 14: 0.45, 15: 0.45, 16: 0.45,
                             17: 0.6, 18: 0.4}),
    "facebook": (_CONSUMER_DAYS, {9: 0.7, 10: 0.8, 11: 0.85, 12: 0.9, 13: 0.85, 14: 0.6, 15: 0.5, 16: 0.5, 17: 0.55,
                                  18: 0.6, 19: 0.75, 20: 0.7, 21: 0.5}),
    "instagram": (_CONSUMER_DAYS, {8: 0.4, 9: 0.5, 10: 0.6, 11: 0.8, 12: 0.9, 13: 0.85, 14: 0.55, 15: 0.5, 16: 0.5,
                                   17: 0.6, 18: 0.75, 19: 0.95, 20: 1.0, 21: 0.8}),
    "x": (_B2B_DAYS[:5] + [0.5, 0.45], {8: 0.7, 9: 0.95, 10: 1.0, 11: 0.9, 12: 0.85, 13: 0.7, 14: 0.5, 15: 0.5,
                                        16: 0.55, 17: 0.7, 18: 0.55, 19: 0.4}),
    "youtube": ([0.75, 0.75, 0.8, 0.85, 0.95, 1.0, 1.0], {12: 0.5, 13: 0.55, 14: 0.7, 15: 0.8, 16: 0.85, 17: 0.9,
                                                          18: 1.0, 19: 0.95, 20: 0.85, 21: 0.6}),
    "pinterest": ([0.7, 0.7, 0.75, 0.75, 0.85, 1.0, 1.0], {12: 0.45, 14: 0.45, 17: 0.55, 18: 0.65, 19: 0.8, 20: 1.0,
                                                           21: 0.95}),
    "tiktok": (_CONSUMER_DAYS, {9: 0.4, 12: 0.6, 13: 0.55, 16: 0.6, 17: 0.7, 18: 0.85, 19: 1.0, 20: 1.0, 21: 0.85}),
    # Messaging and community channels in India: commute mornings, lunch and late evenings.
    "telegram": (_CONSUMER_DAYS, {8: 0.7, 9: 0.85, 10: 0.8, 12: 0.7, 13: 0.75, 18: 0.8, 19: 0.9, 20: 1.0, 21: 0.95,
                                  22: 0.7}),
    "whatsapp": (_CONSUMER_DAYS, {8: 0.75, 9: 0.9, 10: 0.85, 11: 0.7, 12: 0.7, 13: 0.75, 18: 0.75, 19: 0.9, 20: 1.0,
                                  21: 0.9}),
    "discord": ([0.8, 0.85, 0.85, 0.9, 0.95, 1.0, 0.95], {12: 0.5, 13: 0.55, 17: 0.6, 18: 0.75, 19: 0.9, 20: 1.0,
                                                          21: 1.0, 22: 0.85, 23: 0.6}),
    # Reddit's busiest hours overlap India's evening and the US morning.
    "reddit": ([0.9, 1.0, 1.0, 0.95, 0.85, 0.75, 0.8], {7: 0.6, 8: 0.7, 9: 0.75, 18: 0.7, 19: 0.85, 20: 0.95, 21: 1.0,
                                                        22: 0.95, 23: 0.8}),
    "gbp": ([1.0, 1.0, 1.0, 1.0, 0.95, 0.7, 0.5], {8: 0.7, 9: 0.9, 10: 1.0, 11: 0.95, 12: 0.8, 13: 0.6, 14: 0.5,
                                                   15: 0.5, 16: 0.45, 17: 0.4}),
    "generic": ([0.9, 1.0, 1.0, 1.0, 0.9, 0.6, 0.5], {9: 0.85, 10: 1.0, 11: 0.9, 12: 0.8, 13: 0.75, 14: 0.5, 15: 0.5,
                                                      16: 0.5, 17: 0.65, 18: 0.7, 19: 0.6}),
}
FAMILY = {
    "linkedin_org": "linkedin", "linkedin_person": "linkedin", "facebook_page": "facebook", "instagram": "instagram",
    "threads": "instagram", "x": "x", "bluesky": "x", "mastodon": "x", "youtube": "youtube", "pinterest": "pinterest",
    "tiktok": "tiktok", "gbp": "gbp", "telegram": "telegram", "whatsapp": "whatsapp", "discord": "discord",
    "reddit": "reddit",
}


def default_grid(platform: str | None) -> list[list[float]]:
    days, hours = DEFAULTS[FAMILY.get(platform or "", "generic")]
    return [[round(days[wd] * hours.get(h, BASE_HOUR), 4) for h in range(24)] for wd in range(7)]


# ------------------------------------------------------------------ bucket maths (pure, unit-tested)
@dataclass
class Sample:
    weekday: int
    hour: int
    value: float


@dataclass
class BestTimes:
    platform: str | None
    source: str = "defaults"          # account | platform | defaults
    sample: int = 0                   # posts with metrics behind the scores
    confidence: float = 0.0           # 0..1 (sample / MIN_POSTS)
    metric: str = "engagement_rate"   # engagement_rate | engagements
    grid: list[list[float]] = field(default_factory=list)    # final 7×24 scores 0..1
    own: list[list[float | None]] = field(default_factory=list)  # the organization's own 7×24 scores 0..1 (None = no data)
    counts: list[list[int]] = field(default_factory=list)    # posts in each bucket
    defaults: list[list[float]] = field(default_factory=list)

    def ranked_hours(self, weekday: int) -> list[int]:
        """Hours between 08:00 and 21:00, best first (earlier hour wins a tie)."""
        row = self.grid[weekday]
        return sorted(range(DAY_START, DAY_END + 1), key=lambda h: (-row[h], h))

    def pick_hours(self, weekday: int, n: int, *, exclude: set[int] | None = None) -> list[int]:
        """The top `n` hours of a weekday, at least MIN_GAP_HOURS apart, in time order."""
        n = max(1, min(MAX_PER_DAY, int(n or 1)))
        exclude = exclude or set()
        chosen: list[int] = []
        for h in self.ranked_hours(weekday):
            if h in exclude or any(abs(h - c) < MIN_GAP_HOURS for c in chosen):
                continue
            chosen.append(h)
            if len(chosen) >= n:
                break
        return sorted(chosen)

    def heatmap(self) -> list[dict]:
        out = []
        for wd in range(7):
            for h in range(24):
                own = self.own[wd][h] if self.own else None
                out.append({"weekday": wd, "day": WEEKDAYS[wd], "hour": h, "score": round(self.grid[wd][h], 3),
                            "own": None if own is None else round(own, 3), "posts": self.counts[wd][h] if self.counts else 0,
                            "in_window": DAY_START <= h <= DAY_END})
        return out

    def summary(self) -> dict:
        return {"platform": self.platform, "source": self.source, "sample": self.sample,
                "confidence": round(self.confidence, 2), "metric": self.metric, "message": self.message()}

    def message(self) -> str:
        if self.source == "defaults":
            return ("No results yet, so these are typical good times for this platform. They adjust as your posts "
                    "collect likes, comments and clicks.")
        whose = "this account's" if self.source == "account" else "this platform's"
        if self.confidence >= 1:
            return f"Based on {self.sample} of {whose} posts from the last {WINDOW_DAYS} days."
        return (f"Based on {self.sample} of {whose} posts so far, mixed with typical times for the platform. "
                f"It gets more personal after {MIN_POSTS} posts with results.")


def _kernel(samples: list[Sample]) -> list[list[float | None]]:
    num = [[0.0] * 24 for _ in range(7)]
    den = [[0.0] * 24 for _ in range(7)]
    for s in samples:
        for wd in range(7):
            same_day = wd == s.weekday
            for dh, w_h in ((0, 1.0), (-1, 0.5), (1, 0.5)):
                h = s.hour + dh
                if not 0 <= h <= 23:
                    continue
                w = w_h * (1.0 if same_day else 0.25)
                num[wd][h] += w * s.value
                den[wd][h] += w
    # Shrink towards the overall mean (one pseudo-post) so a bucket backed by more posts beats a neighbour
    # that only borrows from it.
    prior = sum(s.value for s in samples) / len(samples) if samples else 0.0
    return [[((num[wd][h] + prior) / (den[wd][h] + 1)) if den[wd][h] else None for h in range(24)] for wd in range(7)]


def build(samples: list[Sample], platform: str | None, *, source: str = "account",
          metric: str = "engagement_rate") -> BestTimes:
    """Scores from samples blended with the platform defaults."""
    defaults = default_grid(platform)
    counts = [[0] * 24 for _ in range(7)]
    for s in samples:
        counts[s.weekday][s.hour] += 1
    n = len(samples)
    if not n:
        return BestTimes(platform=platform, source="defaults", sample=0, confidence=0.0, metric=metric,
                         grid=[row[:] for row in defaults], own=[[None] * 24 for _ in range(7)], counts=counts,
                         defaults=defaults)
    raw = _kernel(samples)
    top = max((v for row in raw for v in row if v is not None), default=0.0)
    own: list[list[float | None]] = [[(v / top if top > 0 else 0.0) if v is not None else None for v in row] for row in raw]
    confidence = min(1.0, n / MIN_POSTS)
    w = OWN_WEIGHT * confidence
    grid = [[w * (own[wd][h] if own[wd][h] is not None else defaults[wd][h] * 0.5) + (1 - w) * defaults[wd][h]
             for h in range(24)] for wd in range(7)]
    return BestTimes(platform=platform, source=source, sample=n, confidence=confidence, metric=metric, grid=grid,
                     own=own, counts=counts, defaults=defaults)


def samples_from(rows: list, zone: ZoneInfo) -> tuple[list[Sample], str]:
    """(post, latest metric) rows → samples. Uses engagement rate when most posts report impressions."""
    from app.services.analytics import engagements

    with_m = [(p, m) for p, m in rows if m is not None and p.published_at is not None]
    if not with_m:
        return [], "engagement_rate"
    with_imp = [(p, m) for p, m in with_m if int(m.impressions or 0) > 0]
    use_rate = len(with_imp) * 2 >= len(with_m)
    out = []
    for p, m in (with_imp if use_rate else with_m):
        local = p.published_at.astimezone(zone)
        eng = engagements(m)
        out.append(Sample(local.weekday(), local.hour, eng / int(m.impressions) if use_rate else float(eng)))
    return out, ("engagement_rate" if use_rate else "engagements")


# ------------------------------------------------------------------ from the database
async def _rows(session: AsyncSession, now: datetime) -> list:
    from app.services.analytics_growth import post_rows

    return await post_rows(session, now - timedelta(days=WINDOW_DAYS), now + timedelta(minutes=1))


async def for_account(session: AsyncSession, account, zone: ZoneInfo, now: datetime | None = None,
                      rows: list | None = None) -> BestTimes:
    """Best times for one account (falls back to the platform's pooled posts, then to the defaults)."""
    now = now or datetime.now(UTC)
    rows = rows if rows is not None else await _rows(session, now)
    platform = getattr(account, "platform", None)
    acc_id = getattr(account, "id", None)
    mine = [(p, m) for p, m in rows if m is not None and p.social_account_id == acc_id]
    same = [(p, m) for p, m in rows if m is not None and p.platform == platform]
    use, source = mine, "account"
    if len(mine) < MIN_POSTS and len(same) > len(mine):
        use, source = same, "platform"
    samples, metric = samples_from(use, zone)
    return build(samples, platform, source=source, metric=metric)


async def for_platform(session: AsyncSession, platform: str | None, zone: ZoneInfo, now: datetime | None = None,
                       rows: list | None = None) -> BestTimes:
    now = now or datetime.now(UTC)
    rows = rows if rows is not None else await _rows(session, now)
    same = [(p, m) for p, m in rows if m is not None and (platform is None or p.platform == platform)]
    samples, metric = samples_from(same, zone)
    return build(samples, platform, source="platform", metric=metric)


async def next_slots(session: AsyncSession, account, zone: ZoneInfo, *, now: datetime | None = None, count: int = 5,
                     days: int = 7, best: BestTimes | None = None, blackout: set[str] | None = None) -> list[dict]:
    """The `count` best upcoming hours over the next `days` days (time order), skipping hours that already have a
    post for this account within 90 minutes and keeping slots on the same day 3 hours apart."""
    from sqlalchemy import select

    from app.models import Post

    now = now or datetime.now(UTC)
    best = best or await for_account(session, account, zone, now)
    earliest = now + timedelta(minutes=30)
    end = now + timedelta(days=days)
    busy: list[datetime] = []
    if getattr(account, "id", None) is not None:
        busy = [t for t in (await session.scalars(select(Post.scheduled_at).where(
            Post.social_account_id == account.id, Post.scheduled_at.is_not(None),
            Post.scheduled_at >= now - timedelta(hours=2), Post.scheduled_at <= end + timedelta(hours=2),
            Post.status.notin_(("skipped", "rejected", "failed", "removed")),
        ))).all() if t is not None]
    blackout = blackout or set()
    cands: list[tuple[float, datetime]] = []
    d: date = now.astimezone(zone).date()
    last = end.astimezone(zone).date()
    while d <= last:
        if d.isoformat() not in blackout:
            for h in range(DAY_START, DAY_END + 1):
                at = datetime.combine(d, time(h, 0), tzinfo=zone).astimezone(UTC)
                if earliest <= at <= end and not any(abs((at - b).total_seconds()) < 90 * 60 for b in busy):
                    cands.append((best.grid[d.weekday()][h], at))
        d += timedelta(days=1)
    cands.sort(key=lambda c: (-c[0], c[1]))
    chosen: list[tuple[float, datetime]] = []
    for score, at in cands:
        if any(abs((at - c).total_seconds()) < MIN_GAP_HOURS * 3600 for _, c in chosen):
            continue
        chosen.append((score, at))
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda c: c[1])
    top = max((s for s, _ in chosen), default=1.0) or 1.0
    out = []
    for score, at in chosen:
        local = at.astimezone(zone)
        out.append({"at": at.isoformat(), "local": local.strftime("%a %d %b, %H:%M"),
                    "input": local.strftime("%Y-%m-%dT%H:%M"), "score": round(score, 3),
                    "strength": "Best" if score >= top * 0.95 else "Great" if score >= top * 0.8 else "Good"})
    return out

