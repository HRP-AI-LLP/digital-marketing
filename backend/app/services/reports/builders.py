"""One report builder per area of the studio. Each returns a `Doc` (see reports/__init__.py).

Builders only read data. They run in a session scoped to the organization, so every query is automatically
limited to that organization's rows.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.models import (
    AccountMetric, AivisPrompt, AivisRun, AuditLog, BrandProfile, Campaign, CompanyEntity, CompanyProfile, Competitor,
    CompetitorMetric, ContentPillar, IdeaBrief, InboxItem, LinkPage, LinkPageStat, ListeningMention, ListeningQuery,
    LinkClick, LlmUsage, MediaAsset, Post, PostActivity, PostMetric, PresenceRun, PresenceSuggestion, ProviderConfig,
    ProviderSpend, ResearchRun, Schedule, SeoArticle, SiteAuditIssue, SiteAuditPage, SiteAuditRun, SocialAccount,
    TrackedKeyword, TrackedLink,
)
from app.services.content import STATUSES
from app.services.platforms import PLATFORMS, POST_TYPES
from app.services.reports import Ctx, Doc, ReportInputError, label, num, report, short

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def plat(p: str | None) -> str:
    spec = PLATFORMS.get(p or "")
    return spec.label if spec else label(p)


def status_label(s: str | None) -> str:
    return STATUSES.get(s or "", label(s))


def ptype(t: str | None) -> str:
    return POST_TYPES.get(t or "", label(t))


def _uuid(v) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        raise ReportInputError("That item doesn't exist.")


def _post_title(p: Post) -> str:
    return short(p.title or p.text or p.topic or "Untitled post", 110)


async def _latest_metrics(c: Ctx, post_ids: list) -> dict:
    if not post_ids:
        return {}
    sub = (select(PostMetric.post_id, func.max(PostMetric.fetched_at).label("at"))
           .where(PostMetric.post_id.in_(post_ids)).group_by(PostMetric.post_id).subquery())
    rows = (await c.session.scalars(select(PostMetric).join(
        sub, (PostMetric.post_id == sub.c.post_id) & (PostMetric.fetched_at == sub.c.at)))).all()
    return {m.post_id: m for m in rows}


def _eng(m: PostMetric | None) -> int | None:
    if m is None:
        return None
    return sum(int(getattr(m, k) or 0) for k in ("likes", "comments", "shares", "saves", "clicks"))


async def _accounts(c: Ctx) -> dict:
    return {a.id: a for a in (await c.session.scalars(select(SocialAccount))).all()}


async def _posts_in(c: Ctx, *, field=Post.scheduled_at, extra=()) -> list[Post]:
    q = select(Post).where(field >= c.start_utc, field < c.end_utc, *extra).order_by(field)
    return list((await c.session.scalars(q)).all())


# ====================================================================== overview
@report("dashboard", "Overview report", "Everything at a glance: posts by status, publishing by platform, accounts, "
        "what's coming up and what needs attention.", "content.view", group="Overview")
async def dashboard(c: Ctx) -> Doc:
    d = Doc("Overview report")
    posts = await _posts_in(c)
    published = [p for p in (await _posts_in(c, field=Post.published_at)) if p.status == "published"]
    by_status = Counter(p.status for p in posts)
    metrics = await _latest_metrics(c, [p.id for p in published])
    impressions = sum(int(m.impressions or 0) for m in metrics.values())
    eng = sum(_eng(m) or 0 for m in metrics.values())
    waiting = await c.session.scalar(select(func.count()).select_from(Post).where(Post.status == "in_review"))
    accounts = await _accounts(c)
    d.kpis([("Posts published", len(published)), ("Impressions", impressions), ("Engagements", eng),
            ("Waiting for approval", waiting or 0), ("Planned in period", len(posts)),
            ("Failed", by_status.get("failed", 0)), ("Connected accounts", sum(a.status == "connected" for a in accounts.values())),
            ("Platforms", len({a.platform for a in accounts.values()}))])
    per_plat = Counter(plat(p.platform) for p in published)
    d.bars("Published posts by platform", sorted(per_plat.items(), key=lambda x: -x[1]))
    per_day: dict[str, int] = defaultdict(int)
    for p in published:
        per_day[c.day(p.published_at)] += 1
    days = [(c.start + timedelta(days=i)) for i in range((c.end - c.start).days + 1)]
    if len(days) <= 92:
        d.line("Posts published per day", [x.strftime("%d %b") for x in days],
               [{"name": "Posts", "values": [per_day.get(x.strftime("%d %b %Y"), 0) for x in days]}])
    d.table("Posts by status", ["Status", "Posts"], [[status_label(k), v] for k, v in by_status.most_common()], numeric=(1,))
    d.table("Accounts", ["Account", "Platform", "Status"],
            [[a.name, plat(a.platform), label(a.status)] for a in accounts.values()], empty="No accounts connected.")
    upcoming = (await c.session.scalars(select(Post).where(
        Post.status.in_(["scheduled", "approved", "in_review"]), Post.scheduled_at >= func.now())
        .order_by(Post.scheduled_at).limit(25))).all()
    d.table("Coming up", ["When", "Platform", "Status", "Post"],
            [[c.local(p.scheduled_at), plat(p.platform), status_label(p.status), _post_title(p)] for p in upcoming],
            empty="Nothing scheduled.")
    failed = [p for p in posts if p.status == "failed"]
    d.table("Needs attention", ["When", "Platform", "Post", "Problem"],
            [[c.local(p.scheduled_at), plat(p.platform), _post_title(p), short(p.error, 160)] for p in failed],
            empty="No failed posts in this period.")
    return d


# ====================================================================== posts
@report("posts", "Posts report", "Every post in the period with its status, platform and results. Follows the filters "
        "on the All posts page.", "content.view")
async def posts_report(c: Ctx) -> Doc:
    d = Doc("Posts report")
    extra = []
    if c.params.get("status"):
        extra.append(Post.status.in_([s for s in str(c.params["status"]).split(",") if s]))
    if c.params.get("platform"):
        extra.append(Post.platform == c.params["platform"])
    if c.params.get("account_id"):
        extra.append(Post.social_account_id == _uuid(c.params["account_id"]))
    if c.params.get("pillar_id"):
        extra.append(Post.pillar_id == _uuid(c.params["pillar_id"]))
    posts = await _posts_in(c, extra=extra)
    metrics = await _latest_metrics(c, [p.id for p in posts])
    pub = [p for p in posts if p.status == "published"]
    imp = sum(int((metrics.get(p.id).impressions if metrics.get(p.id) else 0) or 0) for p in pub)
    eng = sum(_eng(metrics.get(p.id)) or 0 for p in pub)
    d.kpis([("Posts", len(posts)), ("Published", len(pub)), ("Impressions", imp), ("Engagements", eng)])
    if any(c.params.get(k) for k in ("status", "platform", "account_id", "pillar_id")):
        d.facts("Filters", [("Status", c.params.get("status")), ("Platform", plat(c.params.get("platform")) if c.params.get("platform") else None)])
    d.bars("Posts by platform", Counter(plat(p.platform) for p in posts).most_common())
    d.table("Posts", ["Date", "Platform", "Type", "Status", "Post", "Impr.", "Eng."],
            [[c.local(p.published_at or p.scheduled_at, "%d %b %H:%M"), plat(p.platform), ptype(p.post_type),
              status_label(p.status), _post_title(p),
              metrics[p.id].impressions if p.id in metrics else None, _eng(metrics.get(p.id))] for p in posts],
            numeric=(5, 6), empty="No posts in this period.")
    return d


@report("post", "Post details", "One post: text, hashtags, media, schedule, quality checks, results and history.",
        "content.view", period=False, needs_id=True)
async def post_report(c: Ctx) -> Doc:
    p = await c.session.get(Post, _uuid(c.params.get("id")))
    if p is None:
        raise ReportInputError("That post doesn't exist.")
    acc = await c.session.get(SocialAccount, p.social_account_id) if p.social_account_id else None
    pillar = await c.session.get(ContentPillar, p.pillar_id) if p.pillar_id else None
    d = Doc("Post details", subtitle=f"{plat(p.platform)}{' · ' + acc.name if acc else ''}")
    d.facts("Post", [("Status", status_label(p.status)), ("Type", ptype(p.post_type)), ("Platform", plat(p.platform)),
                     ("Account", acc.name if acc else None), ("Topic", p.topic), ("Content pillar", pillar.name if pillar else None),
                     ("Scheduled", c.local(p.scheduled_at) if p.scheduled_at else None),
                     ("Published", c.local(p.published_at) if p.published_at else None),
                     ("Link on the platform", p.external_url), ("Link in post", p.link_url), ("Problem", p.error)])
    d.text("Title", p.title)
    d.text("Text", p.text)
    d.text("Hashtags", " ".join(h if str(h).startswith("#") else f"#{h}" for h in (p.hashtags or [])))
    d.text("First comment", p.first_comment)
    images = []
    for mid in (p.media_ids or [])[:10]:
        try:
            a = await c.session.get(MediaAsset, uuid.UUID(str(mid)))
        except ValueError:
            a = None
        if a is not None:
            images.append(a)
    if images:
        d.html(_thumbs(images))
    qa = p.qa or {}
    if qa.get("checks"):
        d.table("Quality checks", ["Check", "Result", "Detail"],
                [[label(x.get("name")), "Pass" if x.get("ok") else "Fail", x.get("detail")] for x in qa["checks"]])
    if qa.get("issues"):
        d.bullets("Reviewer notes", list(qa["issues"]))
    ms = (await c.session.scalars(select(PostMetric).where(PostMetric.post_id == p.id).order_by(PostMetric.fetched_at))).all()
    if ms:
        m = ms[-1]
        d.kpis([("Impressions", m.impressions), ("Reach", m.reach), ("Likes", m.likes), ("Comments", m.comments),
                ("Shares", m.shares), ("Clicks", m.clicks), ("Saves", m.saves), ("Video views", m.video_views)])
    acts = (await c.session.scalars(select(PostActivity).where(PostActivity.post_id == p.id)
                                    .order_by(PostActivity.created_at))).all()
    d.table("History", ["When", "What", "Who", "Note"],
            [[c.local(a.created_at), label(a.action), a.actor or "system", short(a.note, 200)] for a in acts])
    await _post_collab(c, d, p)
    return d


async def _post_collab(c: Ctx, d: Doc, p: Post) -> None:
    """Internal team comments (never published) and the content versions of one post."""
    from app.models import PostComment, PostVersion, User
    from app.services.collab import FIELD_LABELS, changed_fields

    comments = (await c.session.scalars(select(PostComment).where(PostComment.post_id == p.id)
                                        .order_by(PostComment.created_at))).all()
    versions = (await c.session.scalars(select(PostVersion).where(PostVersion.post_id == p.id)
                                        .order_by(PostVersion.version))).all()
    ids = {x.user_id for x in comments} | {x.changed_by for x in versions}
    ids |= {uuid.UUID(str(m)) for x in comments for m in (x.mentions or []) if _safe_uuid(m)}
    ids.discard(None)
    names = {u.id: u.full_name for u in (await c.session.scalars(select(User).where(User.id.in_(ids)))).all()} if ids else {}

    def mentioned(x) -> str:
        return ", ".join(names[u] for u in (_safe_uuid(m) for m in (x.mentions or [])) if u in names)

    d.table("Team comments", ["When", "Who", "Comment", "Mentioned", "Status"],
            [[c.local(x.created_at), names.get(x.user_id, "Former member"), short(x.body, 600), mentioned(x),
              "Resolved" if x.resolved else "Open"] for x in comments],
            note="Internal notes from the team. They are never published.", empty="No team comments.")
    rows, prev = [], None
    for v in versions:
        changed = ", ".join(FIELD_LABELS.get(f, f) for f in changed_fields(prev.fields if prev else None, v.fields))
        who = names.get(v.changed_by) or ("AI" if (v.reason or "").startswith("AI") else "System")
        rows.append([str(v.version), c.local(v.created_at), who, v.reason or "", changed or "—",
                     short((v.fields or {}).get("text") or "", 160)])
        prev = v
    d.table("Versions", ["#", "When", "Who", "What changed", "Fields", "Text"], list(reversed(rows)),
            empty="No saved versions yet.")


def _thumbs(assets: list[MediaAsset]) -> str:
    import base64

    from app.core import storage

    cells = []
    for a in assets:
        if (a.mime or "").startswith("image/"):
            try:
                data = storage.abs_path(a.path).read_bytes()
                if len(data) < 4_000_000:
                    cells.append(f'<img src="data:{a.mime};base64,{base64.b64encode(data).decode()}" '
                                 'style="width:31%;margin:1%;border-radius:8px;border:1px solid #e2e8f0">')
                    continue
            except (OSError, ValueError):
                pass
        cells.append(f'<span class="tag">{a.kind} · {a.width or "?"}×{a.height or "?"}</span>')
    return "<h2>Media</h2><div>" + "".join(cells) + "</div>"


@report("calendar", "Content calendar", "Posts planned for each day of the period, by platform.", "content.view")
async def calendar(c: Ctx) -> Doc:
    d = Doc("Content calendar")
    posts = [p for p in await _posts_in(c) if p.status not in ("rejected", "removed")]
    by_day: dict = defaultdict(list)
    for p in posts:
        by_day[p.scheduled_at.astimezone(c.tz).date()].append(p)
    d.kpis([("Posts planned", len(posts)), ("Days with posts", len(by_day)),
            ("Platforms", len({p.platform for p in posts})), ("Published", sum(p.status == "published" for p in posts))])
    rows = []
    for day in sorted(by_day):
        for p in by_day[day]:
            rows.append([f"{DAYS[day.weekday()]} {day.strftime('%d %b')}", c.local(p.scheduled_at, "%H:%M"), plat(p.platform),
                         ptype(p.post_type), status_label(p.status), _post_title(p)])
    d.table("Calendar", ["Day", "Time", "Platform", "Type", "Status", "Post"], rows, empty="Nothing planned in this period.")
    return d


@report("approvals", "Approvals report", "Posts waiting for approval, with their quality checks.", "content.view",
        period=False)
async def approvals(c: Ctx) -> Doc:
    d = Doc("Waiting for approval")
    rows = (await c.session.scalars(select(Post).where(Post.status == "in_review").order_by(Post.scheduled_at.nulls_last()))).all()
    from app.services.content import hold_reasons

    held = [p for p in rows if hold_reasons(p)]
    d.kpis([("Waiting", len(rows)), ("Pass the checks", len(rows) - len(held)), ("Need a closer look", len(held))])
    d.table("Posts", ["Planned for", "Platform", "Post", "Checks", "Similarity"],
            [[c.local(p.scheduled_at) if p.scheduled_at else "No time yet", plat(p.platform), _post_title(p),
              ", ".join(hold_reasons(p)) or "Pass", f"{round((p.similarity or 0) * 100)}%"] for p in rows],
            empty="Nothing is waiting for approval.")
    return d


@report("schedules", "Posting schedules", "Each schedule: account, days, times, mix and how posts are approved.",
        "schedule.view", period=False)
async def schedules(c: Ctx) -> Doc:
    d = Doc("Posting schedules")
    accounts = await _accounts(c)
    pillars = {p.id: p.name for p in (await c.session.scalars(select(ContentPillar))).all()}
    rows = (await c.session.scalars(select(Schedule).order_by(Schedule.name))).all()
    d.kpis([("Schedules", len(rows)), ("Active", sum(s.active for s in rows))])
    for s in rows:
        acc = accounts.get(s.social_account_id)
        every = "Every day" if (s.interval_days or 1) == 1 else f"Every {s.interval_days} days"
        d.facts(s.name, [("Account", f"{acc.name} ({plat(acc.platform)})" if acc else "—"), ("Active", s.active),
                         ("Repeats", every if (s.interval_days or 1) > 1 else ", ".join(DAYS[i] for i in sorted(s.days_of_week or []))),
                         ("Times", f"Best time, {s.posts_per_day or 1} a day" if s.time_mode == "best" else ", ".join(s.times or [])),
                         ("Post types", ", ".join(f"{ptype(k)} ×{v}" for k, v in (s.post_types or {}).items())),
                         ("Topics", ", ".join(pillars.get(_safe_uuid(x), "?") for x in (s.pillar_ids or [])) or "All"),
                         ("Approval", label(s.mode)), ("Written ahead", f"{s.lead_hours} h"),
                         ("Link", s.link_url if s.include_link else None), ("Instructions", s.instructions)])
    if not rows:
        d.text("Schedules", "No posting schedules yet.")
    return d


@report("best_times", "Best time to post", "For each account: the best days and hours to post, learned from your "
        "results over the last 90 days and blended with typical times for the platform.", "content.view", period=False)
async def best_times(c: Ctx) -> Doc:
    from app.services import best_time

    d = Doc("Best time to post")
    accounts = [a for a in (await _accounts(c)).values() if a.status != "removed" and a.platform in PLATFORMS
                and a.platform != "google"]
    rows = await best_time._rows(c.session, datetime.now(UTC))
    d.text("How this works", "Each post's likes, comments, shares, saves and clicks (per impression where the platform "
           f"reports impressions) are grouped by weekday and hour in {c.tz}. Until an account has "
           f"{best_time.MIN_POSTS} posts with results, typical good times for its platform fill the gaps. "
           f"Times are picked between {best_time.DAY_START:02d}:00 and {best_time.DAY_END:02d}:00.")
    if not accounts:
        d.text("Accounts", "No social accounts connected yet.")
        return d
    hours = list(range(best_time.DAY_START, best_time.DAY_END + 1))
    found = [(a, await best_time.for_account(c.session, a, c.tz, rows=rows)) for a in accounts]
    summary = []
    for a, bt in found:
        top = sorted(((bt.grid[wd][h], wd, h) for wd in range(7) for h in hours), key=lambda t: (-t[0], t[1], t[2]))[:3]
        summary.append([a.name, plat(a.platform), ", ".join(f"{DAYS[wd]} {h:02d}:00" for _, wd, h in top),
                        {"account": "This account", "platform": "Same platform", "defaults": "Typical times"}[bt.source],
                        bt.sample, f"{round(bt.confidence * 100)}%"])
    d.table("Best times by account", ["Account", "Platform", "Top times", "Based on", "Posts with results", "Confidence"],
            summary, numeric=(4,))
    for a, bt in found:
        peak = max((bt.grid[wd][h] for wd in range(7) for h in hours), default=1.0) or 1.0
        d.table(f"{a.name} ({plat(a.platform)}): score by weekday and hour", ["Day"] + [f"{h:02d}" for h in hours],
                [[DAYS[wd]] + [round(bt.grid[wd][h] / peak * 100) for h in hours] for wd in range(7)],
                numeric=tuple(range(1, len(hours) + 1)), note=bt.message() + " 100 = the strongest time.")
    return d


def _safe_uuid(v):
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None


@report("accounts", "Social accounts", "Connected accounts with their status, followers and posts in the period.",
        "content.view", group="Overview")
async def accounts_report(c: Ctx) -> Doc:
    d = Doc("Social accounts")
    accounts = list((await _accounts(c)).values())
    posted = Counter(p.social_account_id for p in await _posts_in(c, field=Post.published_at) if p.status == "published")
    latest = {}
    for a in accounts:
        latest[a.id] = await c.session.scalar(select(AccountMetric).where(AccountMetric.social_account_id == a.id)
                                              .order_by(AccountMetric.day.desc()).limit(1))
    d.kpis([("Accounts", len(accounts)), ("Connected", sum(a.status == "connected" for a in accounts)),
            ("Followers (total)", sum(int((latest[a.id].followers if latest[a.id] else 0) or 0) for a in accounts)),
            ("Posts published", sum(posted.values()))])
    d.table("Accounts", ["Account", "Platform", "Status", "Followers", "Posts in period", "Note"],
            [[a.name, plat(a.platform), label(a.status), latest[a.id].followers if latest[a.id] else None, posted.get(a.id, 0),
              short(a.status_message, 120)] for a in accounts], numeric=(3, 4), empty="No accounts connected.")
    return d


# ====================================================================== AI usage and cost
@report("ai_usage", "AI usage and cost", "Tokens, calls and cost by provider, model and task, plus paid data services.",
        "integration.view", group="Settings")
async def ai_usage(c: Ctx) -> Doc:
    d = Doc("AI usage and cost")
    rows = (await c.session.scalars(select(LlmUsage).where(LlmUsage.created_at >= c.start_utc,
                                                           LlmUsage.created_at < c.end_utc))).all()
    cost = sum(float(r.cost_usd or 0) for r in rows)
    tin, tout = sum(r.input_tokens or 0 for r in rows), sum(r.output_tokens or 0 for r in rows)
    d.kpis([("Calls", len(rows)), ("Input tokens", tin), ("Output tokens", tout), ("Cost (USD)", f"${cost:,.2f}"),
            ("Failed calls", sum(not r.ok for r in rows))])
    agg: dict = defaultdict(lambda: [0, 0, 0, 0.0])
    for r in rows:
        a = agg[(r.provider, r.model, r.kind)]
        a[0] += 1
        a[1] += r.input_tokens or 0
        a[2] += r.output_tokens or 0
        a[3] += float(r.cost_usd or 0)
    d.table("By model", ["Provider", "Model", "Kind", "Calls", "Input tokens", "Output tokens", "Cost (USD)"],
            [[k[0], k[1], label(k[2]), v[0], v[1], v[2], f"${v[3]:,.4f}"] for k, v in sorted(agg.items(), key=lambda x: -x[1][3])],
            numeric=(3, 4, 5, 6), empty="No AI calls in this period.")
    tasks: dict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        tasks[r.task or "other"][0] += 1
        tasks[r.task or "other"][1] += float(r.cost_usd or 0)
    d.table("By task", ["Task", "Calls", "Cost (USD)"], [[label(k), v[0], f"${v[1]:,.4f}"] for k, v in
                                                         sorted(tasks.items(), key=lambda x: -x[1][0])], numeric=(1, 2))
    by_day: dict = defaultdict(float)
    for r in rows:
        by_day[c.day(r.created_at)] += float(r.cost_usd or 0)
    days = [(c.start + timedelta(days=i)) for i in range((c.end - c.start).days + 1)]
    if len(days) <= 92 and cost:
        d.line("Cost per day (USD)", [x.strftime("%d %b") for x in days],
               [{"name": "Cost", "values": [round(by_day.get(x.strftime("%d %b %Y"), 0), 4) for x in days]}])
    spend = (await c.session.scalars(select(ProviderSpend).where(ProviderSpend.created_at >= c.start_utc,
                                                                 ProviderSpend.created_at < c.end_utc))).all()
    sp: dict = defaultdict(lambda: [0, 0.0])
    for s in spend:
        sp[(s.provider, s.purpose)][0] += 1
        sp[(s.provider, s.purpose)][1] += float(s.cost_usd or 0)
    d.table("Paid data services", ["Service", "Used for", "Calls", "Cost (USD)"],
            [[k[0], label(k[1]), v[0], f"${v[1]:,.4f}"] for k, v in sp.items()], numeric=(2, 3),
            empty="No paid data services used in this period.")
    return d


@report("integrations", "Integrations", "Every connected service, whether it works, and its monthly budget.",
        "integration.view", period=False, group="Settings")
async def integrations(c: Ctx) -> Doc:
    d = Doc("Integrations")
    rows = (await c.session.scalars(select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.name))).all()
    d.kpis([("Integrations", len(rows)), ("Turned on", sum(r.enabled for r in rows)),
            ("Last test failed", sum(r.last_test_ok is False for r in rows))])
    d.table("Services", ["Kind", "Name", "Provider", "On", "Last test", "Budget / month"],
            [[label(r.kind), r.name, r.provider, r.enabled,
              ("OK" if r.last_test_ok else "Failed: " + short(r.last_test_message, 80)) if r.last_test_ok is not None else "Not tested",
              f"${float(r.monthly_budget_usd):,.2f}" if r.monthly_budget_usd else "—"] for r in rows])
    d.text("Note", "Keys and passwords are never included in reports.")
    return d


@report("connections", "Connections (webhooks)", "Webhooks that tell other tools about events, how often their "
        "deliveries succeed, and the API tokens in use.", "connections.view", group="Settings")
async def connections(c: Ctx) -> Doc:
    from app.models import ApiToken, User, Webhook, WebhookDelivery
    from urllib.parse import urlsplit

    from app.services.webhooks import EVENTS, success_rate

    d = Doc("Connections")
    hooks = (await c.session.scalars(select(Webhook).order_by(Webhook.name))).all()
    rows = (await c.session.execute(
        select(WebhookDelivery.webhook_id, WebhookDelivery.ok, func.count())
        .where(WebhookDelivery.created_at >= c.start_utc, WebhookDelivery.created_at < c.end_utc)
        .group_by(WebhookDelivery.webhook_id, WebhookDelivery.ok))).all()
    stats: dict = defaultdict(lambda: [0, 0])  # webhook -> [ok, total]
    for wid, ok, n in rows:
        stats[wid][1] += n
        if ok:
            stats[wid][0] += n
    ok_all, total_all = sum(v[0] for v in stats.values()), sum(v[1] for v in stats.values())
    rate = success_rate(ok_all, total_all)
    d.kpis([("Webhooks", len(hooks)), ("Turned on", sum(h.active for h in hooks)), ("Deliveries", total_all),
            ("Success rate", f"{rate:g}%" if rate is not None else "—")])

    def _rate(h) -> str:
        r = success_rate(*stats[h.id]) if h.id in stats else None
        return f"{r:g}%" if r is not None else "—"

    d.table("Webhooks", ["Name", "Server", "Events", "On", "Deliveries", "Success", "Last result"],
            [[h.name, urlsplit(h.url).hostname or "—", ", ".join(EVENTS.get(e, e) for e in (h.events or [])), h.active,
              stats[h.id][1] if h.id in stats else 0, _rate(h),
              ("OK" if h.last_status == "ok" else f"Failed ({h.last_status_code or 'no answer'})" if h.last_status else "Not called yet")
              + (f" · {c.local(h.last_delivery_at)}" if h.last_delivery_at else "")] for h in hooks],
            numeric=(4,), empty="No webhooks yet.")
    events = (await c.session.execute(
        select(WebhookDelivery.event, func.count()).where(WebhookDelivery.created_at >= c.start_utc,
                                                          WebhookDelivery.created_at < c.end_utc)
        .group_by(WebhookDelivery.event))).all()
    d.table("Deliveries by event", ["Event", "Deliveries"], [[EVENTS.get(e, e), n] for e, n in sorted(events, key=lambda x: -x[1])],
            numeric=(1,), empty="No deliveries in this period.")
    toks = (await c.session.scalars(select(ApiToken).where(ApiToken.revoked_at.is_(None)).order_by(ApiToken.created_at))).all()
    names = {u.id: u.email for u in (await c.session.scalars(select(User).where(User.id.in_({t.user_id for t in toks})))).all()} if toks else {}
    d.table("API tokens in use", ["Name", "Created by", "Permissions", "Last used", "Expires"],
            [[t.name, names.get(t.user_id, "—"), len(t.scopes or []), c.local(t.last_used_at) if t.last_used_at else "Never",
              c.local(t.expires_at) if t.expires_at else "Never"] for t in toks], numeric=(2,), empty="No API tokens.")
    d.text("Note", "Signing secrets, API tokens and full webhook addresses are never included in reports.")
    return d


# ====================================================================== growth and research
@report("presence", "Social presence", "Your social presence score, each profile's score and the suggestions to improve it.",
        "presence.view", period=False, group="Growth")
async def presence(c: Ctx) -> Doc:
    d = Doc("Social presence")
    run = await c.session.scalar(select(PresenceRun).order_by(PresenceRun.created_at.desc()).limit(1))
    if run is None:
        d.text("No check yet", "Run a social presence check on the Social presence page first.")
        return d
    d.period = c.local(run.created_at, "%d %b %Y")
    d.score("Overall score", run.score, run.grade or "",
            [(p.get("label", ""), p.get("score"), p.get("how", "")) for p in (run.parts or [])])
    d.text("Summary", run.narrative or (run.summary or {}).get("text") if isinstance(run.summary, dict) else run.narrative)
    d.table("Profiles", ["Profile", "Platform", "Followers", "Score", "Notes"],
            [[p.get("display_name") or p.get("handle") or p.get("label"), plat(p.get("platform")), p.get("followers"),
              p.get("score"), short("; ".join(p.get("notes") or []), 140)] for p in (run.profiles or [])], numeric=(2, 3))
    sugg = (await c.session.scalars(select(PresenceSuggestion).where(PresenceSuggestion.run_id == run.id)
                                    .order_by(PresenceSuggestion.rank))).all()
    d.table("Suggestions", ["#", "Suggestion", "Priority", "Status", "Detail"],
            [[s.rank, s.title, label(s.priority), label(s.status), short(s.detail, 220)] for s in sugg])
    return d


@report("listening", "Listening report", "Mentions of your brand and topics: volume, sentiment, sources and the "
        "mentions that need a reply.", "listening.view", group="Growth")
async def listening(c: Ctx) -> Doc:
    d = Doc("Listening report")
    ms = (await c.session.scalars(select(ListeningMention).where(ListeningMention.published_at >= c.start_utc,
                                                                 ListeningMention.published_at < c.end_utc)
                                  .order_by(ListeningMention.published_at.desc()))).all()
    rel = [m for m in ms if m.relevant is not False]
    sent = Counter(m.sentiment or "unknown" for m in rel)
    d.kpis([("Mentions", len(rel)), ("Positive", sent.get("positive", 0)), ("Negative", sent.get("negative", 0)),
            ("Need a reply", sum(bool(m.needs_response) for m in rel))])
    d.bars("Mentions by source", Counter(label(m.source) for m in rel).most_common(12))
    d.table("Sentiment", ["Sentiment", "Mentions"], [[label(k), v] for k, v in sent.most_common()], numeric=(1,))
    qs = (await c.session.scalars(select(ListeningQuery))).all()
    d.table("Topics tracked", ["Topic", "Kind", "Terms", "On"],
            [[q.name, label(q.kind), short(", ".join(q.terms or []), 100), q.enabled] for q in qs])
    d.table("Mentions", ["Date", "Source", "Sentiment", "Title / text", "Link"],
            [[c.local(m.published_at, "%d %b"), label(m.source), label(m.sentiment), short(m.title or m.snippet, 140),
              short(m.url, 60)] for m in rel], limit=150, empty="No mentions in this period.")
    return d


@report("aivis", "AI visibility", "How often AI assistants mention you, your share of voice against competitors and "
        "what to do about it.", "aivis.view", period=False, group="Growth")
async def aivis(c: Ctx) -> Doc:
    d = Doc("AI visibility")
    run = await c.session.scalar(select(AivisRun).where(AivisRun.status == "done").order_by(AivisRun.created_at.desc()).limit(1))
    prompts = (await c.session.scalars(select(AivisPrompt))).all()
    if run is None:
        d.text("No check yet", "Run an AI visibility check first.")
        d.table("Prompts", ["Prompt", "Topic", "On"], [[p.text, p.topic, p.enabled] for p in prompts])
        return d
    m = run.metrics or {}
    d.period = c.local(run.finished_at or run.created_at, "%d %b %Y")
    d.kpis([("Visibility", num(m.get("visibility"), True) if m.get("visibility") is not None else None),
            ("Share of voice", num(m.get("share_of_voice"), True) if m.get("share_of_voice") is not None else None),
            ("Average position", m.get("avg_position")), ("Answers checked", m.get("answers"))])
    d.table("Share of voice", ["Brand", "Mentions", "Share", "Average position"],
            [[r.get("name"), r.get("mentions"), num(r.get("share"), True), r.get("avg_position")] for r in m.get("sov", [])],
            numeric=(1, 2, 3))
    d.table("Prompts where competitors beat you", ["Prompt", "Topic", "Competitors named"],
            [[x.get("text"), x.get("topic"), ", ".join(x.get("competitors") or [])] for x in m.get("missed", [])])
    d.table("Most cited websites", ["Domain", "Answers", "Yours"],
            [[x.get("domain"), x.get("answers"), x.get("own")] for x in m.get("top_domains", [])], numeric=(1,))
    recs = run.recommendations or []
    if isinstance(recs, dict):
        recs = recs.get("items") or []
    d.bullets("Recommendations", [f"{r.get('title')}: {r.get('detail') or r.get('why') or ''}" if isinstance(r, dict) else str(r)
                                  for r in recs])
    return d


@report("keywords", "Keyword rankings", "Tracked keywords with their Google position, change and ranking page.",
        "keywords.view", period=False, group="Growth")
async def keywords(c: Ctx) -> Doc:
    d = Doc("Keyword rankings")
    rows = (await c.session.scalars(select(TrackedKeyword).order_by(TrackedKeyword.position.nulls_last()))).all()
    ranked = [k for k in rows if k.position]
    d.kpis([("Keywords tracked", len(rows)), ("In top 3", sum(k.position <= 3 for k in ranked)),
            ("In top 10", sum(k.position <= 10 for k in ranked)), ("Not ranking", len(rows) - len(ranked))])
    d.table("Keywords", ["Keyword", "Position", "Change", "Best", "Volume", "Page"],
            [[k.keyword, k.position, (k.previous_position - k.position) if (k.position and k.previous_position) else None,
              k.best_position, k.volume, short(k.url, 60)] for k in rows], numeric=(1, 2, 3, 4), empty="No keywords tracked yet.")
    return d


@report("siteaudit", "Website health (site audit)", "The latest crawl of your website: score, issues by severity and "
        "the pages with problems.", "siteaudit.view", period=False, group="Growth")
async def siteaudit(c: Ctx) -> Doc:
    from app.services.siteaudit.checks import CHECKS

    d = Doc("Website health")
    run = await c.session.scalar(select(SiteAuditRun).where(SiteAuditRun.status == "done")
                                 .order_by(SiteAuditRun.created_at.desc()).limit(1))
    if run is None:
        d.text("No audit yet", "Run a site audit on the Site audit page first.")
        return d
    d.period = c.local(run.finished_at or run.created_at, "%d %b %Y")
    d.subtitle = run.start_url or c.org.website or ""
    d.kpis([("Health score", run.score), ("Pages crawled", run.pages_crawled), ("Errors", run.errors),
            ("Warnings", run.warnings), ("Notices", run.notices)])
    issues = (await c.session.scalars(select(SiteAuditIssue).where(SiteAuditIssue.run_id == run.id))).all()
    by_check: dict = defaultdict(list)
    for i in issues:
        by_check[(i.severity, i.check_id)].append(i)
    order = {"error": 0, "warning": 1, "notice": 2}
    rows = []
    for (sev, cid), items in sorted(by_check.items(), key=lambda x: (order.get(x[0][0], 9), -len(x[1]))):
        chk = CHECKS.get(cid)
        rows.append([label(sev), chk.title if chk else label(cid), len(items), short(chk.fix if chk else "", 160)])
    d.table("Issues", ["Severity", "Issue", "Pages", "How to fix"], rows, numeric=(2,), empty="No issues found.")
    pages = (await c.session.scalars(select(SiteAuditPage).where(SiteAuditPage.run_id == run.id, SiteAuditPage.errors > 0)
                                     .order_by(SiteAuditPage.errors.desc()).limit(100))).all()
    d.table("Pages with errors", ["Page", "Status", "Errors", "Warnings"],
            [[short(p.url, 80), p.status, p.errors, p.warnings] for p in pages], numeric=(1, 2, 3), empty="No pages with errors.")
    return d


@report("articles", "SEO articles", "Every article with its keyword, status, length and SEO score.", "seo.view",
        group="Growth")
async def articles(c: Ctx) -> Doc:
    d = Doc("SEO articles")
    rows = (await c.session.scalars(select(SeoArticle).where(SeoArticle.created_at >= c.start_utc,
                                                             SeoArticle.created_at < c.end_utc)
                                    .order_by(SeoArticle.created_at.desc()))).all()
    scores = [a.seo_score for a in rows if a.seo_score is not None]
    d.kpis([("Articles", len(rows)), ("Average SEO score", round(sum(scores) / len(scores)) if scores else None),
            ("Words written", sum(a.word_count or 0 for a in rows))])
    d.table("Articles", ["Created", "Title", "Keyword", "Status", "Words", "SEO score"],
            [[c.day(a.created_at), a.title, a.keyword, label(a.status), a.word_count, a.seo_score] for a in rows],
            numeric=(4, 5), empty="No articles in this period.")
    return d


@report("article", "Article", "One article, ready to read or share: the full text with its SEO details.", "seo.view",
        period=False, needs_id=True, group="Growth")
async def article(c: Ctx) -> Doc:
    from app.services.seo import md_to_html

    a = await c.session.get(SeoArticle, _uuid(c.params.get("id")))
    if a is None:
        raise ReportInputError("That article doesn't exist.")
    d = Doc(short(a.title, 90), subtitle=f"Keyword: {a.keyword}" if a.keyword else "")
    d.facts("SEO details", [("Meta title", a.meta_title), ("Meta description", a.meta_description), ("Slug", a.slug),
                            ("Words", a.word_count), ("SEO score", a.seo_score),
                            ("Other keywords", ", ".join(a.secondary_keywords or []))])
    if a.body_md:
        d.html(md_to_html(a.body_md))
    if a.faq:
        d.facts("FAQ", [(q.get("q"), q.get("a")) for q in a.faq if isinstance(q, dict)])
    checks = a.seo_checks or []
    if isinstance(checks, list) and checks:
        d.table("SEO checks", ["Check", "Result"],
                [[x.get("label") or label(x.get("id")), "Pass" if x.get("ok") else "Fix"] for x in checks if isinstance(x, dict)])
    return d


@report("research", "Research and ideas", "The daily research briefs: ideas, why now, angles and their scores.",
        "research.view", group="Growth")
async def research(c: Ctx) -> Doc:
    d = Doc("Research and ideas")
    briefs = (await c.session.scalars(select(IdeaBrief).where(IdeaBrief.run_date >= c.start.isoformat(), IdeaBrief.run_date <= c.end.isoformat())
                                      .order_by(IdeaBrief.run_date.desc(), IdeaBrief.score.desc()))).all()
    runs = (await c.session.scalars(select(ResearchRun).where(ResearchRun.run_date >= c.start.isoformat(),
                                                              ResearchRun.run_date <= c.end.isoformat()))).all()
    d.kpis([("Ideas", len(briefs)), ("Used", sum(bool(b.used) or b.status == "used" for b in briefs)),
            ("Saved", sum(b.status == "saved" for b in briefs)), ("Research runs", len(runs))])
    d.table("Ideas", ["Date", "Idea", "Why now", "Score", "Status"],
            [[b.run_date, b.title, short(b.why_now, 160), b.score, label(b.status)] for b in briefs],
            numeric=(3,), empty="No ideas in this period.")
    return d


@report("intelligence", "Company intelligence", "What the studio knows about your company: profile, products, "
        "services, audiences and competitors.", "intel.view", period=False, group="Growth")
async def intelligence(c: Ctx) -> Doc:
    d = Doc("Company intelligence")
    prof = await c.session.scalar(select(CompanyProfile))
    if prof is not None:
        d.text("Summary", prof.summary)
        d.text("Positioning", prof.positioning)
        d.bullets("Differentiators", list(prof.differentiators or []))
        d.facts("Profile", [("Industries", ", ".join(prof.industries or [])), ("Audiences", ", ".join(prof.audiences or [])),
                            ("Geography", ", ".join(prof.geography or []) if isinstance(prof.geography, list) else prof.geography),
                            ("Confirmed", prof.confirmed)])
    ents = (await c.session.scalars(select(CompanyEntity).order_by(CompanyEntity.type, CompanyEntity.name))).all()
    by_type: dict = defaultdict(list)
    for e in ents:
        by_type[e.type].append(e)
    for t, items in by_type.items():
        d.table(label(t) + "s", ["Name", "Description", "Link"], [[e.name, short(e.description, 200), short(e.url, 60)] for e in items])
    comps = (await c.session.scalars(select(Competitor).order_by(Competitor.overlap_score.desc().nulls_last()))).all()
    d.table("Competitors", ["Competitor", "Website", "Overlap", "Why"],
            [[x.name, x.domain or x.website, x.overlap_score, short(x.reason, 160)] for x in comps], numeric=(2,))
    cm = (await c.session.scalars(select(CompetitorMetric).where(CompetitorMetric.day >= c.start - timedelta(days=30))
                                  .order_by(CompetitorMetric.day.desc()))).all()
    seen, rows = set(), []
    names = {x.id: x.name for x in comps}
    for m in cm:
        if (m.competitor_id, m.channel) in seen:
            continue
        seen.add((m.competitor_id, m.channel))
        rows.append([names.get(m.competitor_id, "?"), label(m.channel), m.followers, m.posts_30d, m.avg_engagement])
    d.table("Competitors on social media", ["Competitor", "Channel", "Followers", "Posts (30 days)", "Engagement per post"],
            rows, numeric=(2, 3, 4), empty="No competitor numbers collected yet.")
    return d


@report("inbox", "Inbox report", "Comments, mentions and messages: volume, sentiment, status and what's still open.",
        "inbox.view", group="Overview")
async def inbox(c: Ctx) -> Doc:
    d = Doc("Inbox report")
    items = (await c.session.scalars(select(InboxItem).where(InboxItem.published_at >= c.start_utc,
                                                             InboxItem.published_at < c.end_utc, InboxItem.is_own.is_(False))
                                     .order_by(InboxItem.published_at.desc()))).all()
    st = Counter(i.status or "new" for i in items)
    sent = Counter(i.sentiment or "unknown" for i in items)
    d.kpis([("Items", len(items)), ("Open", sum(v for k, v in st.items() if k not in ("done", "archived", "replied"))),
            ("Negative", sent.get("negative", 0)), ("Urgent", sum((i.priority or "") == "urgent" for i in items))])
    d.bars("By platform", Counter(plat(i.platform) for i in items).most_common())
    d.table("By status", ["Status", "Items"], [[label(k), v] for k, v in st.most_common()], numeric=(1,))
    d.table("Items", ["Date", "Platform", "From", "Text", "Sentiment", "Status"],
            [[c.local(i.published_at, "%d %b %H:%M"), plat(i.platform), i.author_name or i.author_handle, short(i.text, 150),
              label(i.sentiment), label(i.status)] for i in items], limit=200, empty="Nothing in the inbox for this period.")
    return d


# ====================================================================== brand and settings
@report("brand", "Brand kit and voice", "Brand voice, audience, do's and don'ts, hashtags, calls to action, colours, "
        "topics and campaigns.", "content.view", period=False, group="Settings")
async def brand(c: Ctx) -> Doc:
    d = Doc("Brand kit and voice")
    b = await c.session.scalar(select(BrandProfile))
    if b is not None:
        d.text("Voice", b.voice)
        d.text("Audience", b.audience)
        d.bullets("Do", list(b.do_list or []))
        d.bullets("Don't", list(b.dont_list or []))
        d.facts("Details", [("Hashtags", " ".join(b.hashtags or [])), ("Banned words", ", ".join(b.banned_words or [])),
                            ("Calls to action", "\n".join(f"{x.get('text', '')} {x.get('url', '')}" if isinstance(x, dict) else str(x)
                                                          for x in (b.ctas or []))),
                            ("Colours", ", ".join(f"{k}: {v}" for k, v in (b.colors or {}).items()) if isinstance(b.colors, dict) else b.colors),
                            ("Language", b.language), ("Emojis", b.emoji_policy), ("Disclaimer", b.disclaimer)])
    pillars = (await c.session.scalars(select(ContentPillar).order_by(ContentPillar.weight.desc()))).all()
    d.table("Content topics", ["Topic", "Description", "Weight", "Active"],
            [[p.name, short(p.description, 200), p.weight, p.active] for p in pillars], numeric=(2,))
    camps = (await c.session.scalars(select(Campaign).order_by(Campaign.starts_on.desc().nulls_last()))).all()
    d.table("Campaigns", ["Campaign", "From", "To", "Active", "Brief"],
            [[x.name, x.starts_on, x.ends_on, x.active, short(x.brief, 160)] for x in camps], empty="No campaigns.")
    return d


@report("linkinbio", "Link-in-bio", "Your link-in-bio page: views and clicks per link over the period.",
        "content.view", group="Growth")
async def linkinbio(c: Ctx) -> Doc:
    d = Doc("Link-in-bio")
    pages = (await c.session.scalars(select(LinkPage))).all()
    stats = (await c.session.scalars(select(LinkPageStat).where(LinkPageStat.day >= c.start, LinkPageStat.day <= c.end))).all()
    views = sum(s.count for s in stats if s.target in ("view", "page", "_view"))
    clicks = Counter()
    for s in stats:
        if s.target not in ("view", "page", "_view"):
            clicks[s.target] += s.count
    d.kpis([("Pages", len(pages)), ("Views", views), ("Clicks", sum(clicks.values()))])
    for p in pages:
        d.facts(p.title or p.slug, [("Address", f"/l/{p.slug}"), ("Published", p.published), ("Tagline", p.tagline),
                                    ("Links", "\n".join(f"{x.get('label')}: {x.get('url')}" for x in (p.links or []) if isinstance(x, dict)))])
    d.table("Clicks per link", ["Link", "Clicks"], [[k, v] for k, v in clicks.most_common()], numeric=(1,),
            empty="No clicks in this period.")
    return d


@report("links", "Link clicks", "Tracked short links: clicks over time, by platform and site, and the posts and links "
        "that got the most clicks.", "links.view", group="Growth")
async def links(c: Ctx) -> Doc:
    from app.services import links as lsvc

    d = Doc("Link clicks")
    q = select(TrackedLink)
    if c.params.get("platform"):
        q = q.where(TrackedLink.platform == c.params["platform"])
    if c.params.get("campaign_id"):
        q = q.where(TrackedLink.campaign_id == _uuid(c.params["campaign_id"]))
    rows = list((await c.session.scalars(q)).all())
    filtered = bool(c.params.get("platform") or c.params.get("campaign_id"))
    ids = [r.id for r in rows] if filtered else None
    if filtered and not ids:
        ids = [uuid.uuid4()]
    per = await lsvc.clicks_by_link(c.session, c.start, c.end, ids)
    total = sum(per.values())
    days = (c.end - c.start).days + 1
    d.kpis([("Clicks", total), ("Tracked links", len(rows)), ("Links clicked", sum(1 for r in rows if per.get(r.id))),
            ("Clicks per day", f"{total / days:,.1f}" if days else "0")])
    ser = await lsvc.series(c.session, c.start, c.end, ids)
    d.line("Clicks per day", [date.fromisoformat(x["day"]).strftime("%d %b") for x in ser], [{"name": "Clicks", "values": [x["clicks"] for x in ser]}])
    by_plat = Counter()
    for r in rows:
        if per.get(r.id):
            by_plat[plat(r.platform) if r.platform in PLATFORMS else label(r.platform or "other")] += per[r.id]
    d.bars("Clicks by platform", by_plat.most_common(10))
    refs = await lsvc.breakdown(c.session, c.start, c.end, LinkClick.referrer, ids)
    d.table("Where clicks came from", ["Site", "Clicks"], [[x["key"], x["clicks"]] for x in refs], numeric=(1,),
            empty="No clicks in this period.")
    post_ids = {r.post_id for r in rows if r.post_id and per.get(r.id)}
    posts = {p.id: p for p in (await c.session.scalars(select(Post).where(Post.id.in_(post_ids))))} if post_ids else {}
    by_post = Counter()
    for r in rows:
        if r.post_id in posts:
            by_post[r.post_id] += per.get(r.id, 0)
    d.table("Top posts", ["Post", "Platform", "Clicks"],
            [[_post_title(posts[pid]), plat(posts[pid].platform), n] for pid, n in by_post.most_common(20)], numeric=(2,),
            empty="No post links were clicked in this period.")
    top = sorted(rows, key=lambda r: -per.get(r.id, 0))
    d.table("Links", ["Short link", "Opens", "Label", "Clicks", "All time"],
            [[f"/r/{r.code}", short(r.target_url, 70), r.label or "—", per.get(r.id, 0), r.clicks_total or 0] for r in top],
            numeric=(3, 4), limit=200, empty="No tracked links yet.")
    return d


@report("media", "Media library", "Images, videos and documents in the library, by kind and where they came from.",
        "content.view", group="Content")
async def media(c: Ctx) -> Doc:
    d = Doc("Media library")
    rows = (await c.session.scalars(select(MediaAsset).where(MediaAsset.created_at >= c.start_utc,
                                                             MediaAsset.created_at < c.end_utc)
                                    .order_by(MediaAsset.created_at.desc()))).all()
    d.kpis([("Files", len(rows)), ("Images", sum(r.kind == "image" for r in rows)), ("Videos", sum(r.kind == "video" for r in rows)),
            ("Size", f"{sum(r.size_bytes or 0 for r in rows) / 1048576:,.1f} MB")])
    d.table("By source", ["Source", "Files"], [[label(k), v] for k, v in Counter((r.source or "other").split(":")[0] for r in rows).most_common()],
            numeric=(1,))
    d.table("Files", ["Added", "Kind", "Size", "Source", "Description"],
            [[c.day(r.created_at), label(r.kind), f"{r.width or '?'}×{r.height or '?'}", label((r.source or "").split(':')[0]),
              short(r.alt_text or r.prompt, 120)] for r in rows], limit=300, empty="No files added in this period.")
    return d


@report("organization", "Organization profile", "Company details, contacts, registration and social profiles.",
        "org.settings.view", period=False, group="Settings")
async def organization(c: Ctx) -> Doc:
    o = c.org
    d = Doc("Organization profile", subtitle=o.legal_name or o.name)
    addr = o.address or {}
    reg = o.registration or {}
    d.facts("Company", [("Name", o.name), ("Legal name", o.legal_name), ("Tagline", o.tagline), ("Industry", o.industry),
                        ("Size", o.company_size), ("Founded", o.founded_year), ("Website", o.website), ("Email", o.email),
                        ("Phone", o.phone), ("Country", o.country), ("Currency", o.currency), ("Time zone", o.timezone),
                        ("Address", ", ".join(str(addr.get(k)) for k in ("line1", "line2", "city", "state", "postal_code") if addr.get(k)))])
    d.text("About", o.description)
    d.facts("Registration", [(k.upper(), v) for k, v in reg.items()])
    d.facts("Social profiles", [(label(k), v) for k, v in (o.social_links or {}).items()])
    d.table("Key contacts", ["Name", "Role", "Email", "Phone"],
            [[x.get("name"), x.get("role"), x.get("email"), x.get("phone")] for x in (o.contacts or []) if isinstance(x, dict)],
            empty="No contacts added.")
    return d


@report("activity", "Activity log", "Who did what and when: approvals, publishing, settings and sign-ins.",
        "audit.view", group="Settings")
async def activity(c: Ctx) -> Doc:
    d = Doc("Activity log")
    rows = (await c.session.scalars(select(AuditLog).where(AuditLog.created_at >= c.start_utc, AuditLog.created_at < c.end_utc)
                                    .order_by(AuditLog.created_at.desc()))).all()
    d.kpis([("Events", len(rows)), ("People", len({r.actor_email for r in rows if r.actor_email}))])
    d.table("By action", ["Action", "Events"], [[label(k.replace(".", " ")), v] for k, v in Counter(r.action for r in rows).most_common(25)],
            numeric=(1,))
    d.table("Events", ["When", "Who", "What"], [[c.local(r.created_at), r.actor_email or "system", short(r.summary or r.action, 200)]
                                               for r in rows], limit=400, empty="No activity in this period.")
    return d


# ====================================================================== everything
FULL = ["dashboard", "accounts", "posts", "approvals", "schedules", "inbox", "listening", "presence", "aivis", "keywords",
        "siteaudit", "articles", "research", "linkinbio", "links", "ai_usage"]


@report("full", "Complete report", "Every area in one PDF: overview, accounts, posts, approvals, schedules, inbox, "
        "listening, presence, AI visibility, keywords, website health, articles, research, link-in-bio, link clicks and AI cost.",
        "content.view", group="Overview")
async def full(c: Ctx) -> Doc:
    from app.services.reports import REPORTS

    perms = set(c.params.get("_permissions") or [])
    d = Doc("Complete report")
    for kind in FULL:
        spec = REPORTS[kind]
        if perms and spec.permission not in perms:
            continue
        part = await spec.build(c)
        d.heading(spec.title, part.period if part.period and part.period != c.period_label else "")
        d.blocks.extend(part.blocks)
    return d


@report("analytics", "Marketing report", "Audience growth, engagement, platform breakdown, top posts, competitor "
        "benchmark, marketing health and tips (the same report you can email on a schedule).", "analytics.view",
        group="Overview")
async def analytics(c: Ctx) -> Doc:  # rendered by reports.generate with its own layout
    raise ReportInputError("The marketing report is generated separately.")


# ====================================================================== hashtags & A/B tests
SUGGESTION_LABELS = {"keep": "Keep", "drop": "Drop", "try": "Try more", "neutral": "No clear effect", "wait": "Not enough data"}


def _signed_pct(v) -> str:
    return "—" if v is None else f"{'+' if v > 0 else ''}{v * 100:.0f}%"


@report("hashtags", "Hashtag performance", "Each hashtag used in the period: posts, average results and how they compare "
        "with the account's usual engagement, plus what to keep, drop or try.", "analytics.view", group="Growth")
async def hashtags_report(c: Ctx) -> Doc:
    from app.services import hashtag_stats

    platform = c.params.get("platform") or None
    if platform and platform not in PLATFORMS:
        raise ReportInputError("Unknown platform.")
    data = await hashtag_stats.hashtag_stats(c.session, start=c.start, end=c.end, tz=c.tz, platform=platform)
    d = Doc("Hashtag performance")
    t = data["totals"]
    d.kpis([("Published posts", t["posts"]), ("With results", t["posts_with_metrics"]),
            ("With hashtags", t["posts_with_hashtags"]), ("Hashtags used", t["hashtags"])])
    if platform:
        d.facts("Filters", [("Platform", plat(platform))])
    if data["note"]:
        d.text("Note", data["note"])
    d.text("How it's measured", data["method"])
    cols = ["Hashtag", "Posts", "With results", "Avg. impr.", "Avg. eng.", "Eng. rate", "vs account", "Trend", "Suggestion"]

    def row(r):
        return [f"#{r['tag']}", r["posts"], r["posts_with_metrics"], r["avg_impressions"], r["avg_engagements"],
                num(r["engagement_rate"], pct=True), _signed_pct(r["lift"]), label(r["trend"]) if r["trend"] else "—",
                SUGGESTION_LABELS.get(r["suggestion"], label(r["suggestion"]))]

    d.table("Top hashtags", cols, [row(r) for r in data["top"]], numeric=(1, 2, 3, 4),
            empty=f"No hashtag did better than usual on at least {data['min_posts']} posts with results.")
    d.table("Bottom hashtags", cols, [row(r) for r in data["bottom"]], numeric=(1, 2, 3, 4),
            empty=f"No hashtag did worse than usual on at least {data['min_posts']} posts with results.")
    s = data["suggestions"]
    d.bullets("Keep", [f"#{r['tag']}: {r['reason']}" for r in s["keep"]], empty="Nothing yet.")
    d.bullets("Drop", [f"#{r['tag']}: {r['reason']}" for r in s["drop"]], empty="Nothing yet.")
    d.bullets("Try", [f"#{r['tag']}: {r['reason']}" for r in s["try"]], empty="Nothing yet.")
    d.table("All hashtags", cols, [row(r) for r in data["hashtags"][:200]], numeric=(1, 2, 3, 4),
            empty="No hashtags were used on published posts in this period.")
    d.table("Account averages", ["Account", "Platform", "Posts with results", "Avg. impr.", "Avg. eng.", "Eng. rate"],
            [[a["name"], plat(a["platform"]), a["posts"], a["avg_impressions"], a["avg_engagements"],
              num(a["engagement_rate"], pct=True)] for a in data["accounts"]], numeric=(2, 3, 4),
            empty="No results yet.")
    return d


@report("experiments", "A/B tests", "Every A/B test that started in the period: variants side by side with their "
        "results, the winner and how big the gap was.", "content.view")
async def experiments_report(c: Ctx) -> Doc:
    from app.models import Experiment
    from app.services import experiments as ex

    exps = list((await c.session.scalars(select(Experiment).where(
        Experiment.created_at >= c.start_utc, Experiment.created_at < c.end_utc).order_by(Experiment.created_at.desc()))).all())
    pairs = await ex.variants_of(c.session, [e.id for e in exps])
    metrics = await ex.latest_metrics(c.session, [p.id for v in pairs.values() for _, p in v])
    accounts = await _accounts(c)
    d = Doc("A/B tests")
    d.kpis([("Tests", len(exps)), ("Running", sum(e.status == "running" for e in exps)),
            ("Finished", sum(e.status == "done" for e in exps)), ("With a winner", sum(bool(e.winner_post_id) for e in exps))])
    d.table("Tests", ["Started", "Test", "Measured by", "Status", "Result"],
            [[c.local(e.started_at, "%d %b %Y"), e.name, ex.METRICS.get(e.metric, e.metric), label(e.status),
              short((e.result or {}).get("message"), 200)] for e in exps], empty="No A/B tests in this period.")
    verdicts = {"clear": "Clear lead", "slight": "Slight lead (a hint)", "tie": "Too close to call", "no_data": "No data"}
    for e in exps[:30]:
        o = ex.out(e, pairs.get(e.id, []), metrics, accounts)
        res = o["result"]
        d.heading(e.name, f"{o['mode_label']} · {o['metric_label']}")
        d.table("Variants", ["Variant", "Account", "Published", "Impr.", "Eng.", "Clicks", "Eng. rate", "Text"],
                [[f"{v['label']}{' (winner)' if v['is_winner'] else ''}", (v["account"] or {}).get("name", "—"),
                  c.local(datetime.fromisoformat(v["published_at"]), "%d %b %H:%M") if v["published_at"] else v["status_label"],
                  (v["metrics"] or {}).get("impressions"), (v["metrics"] or {}).get("engagements"),
                  (v["metrics"] or {}).get("clicks"), num((v["metrics"] or {}).get("engagement_rate"), pct=True),
                  short(v["text"], 160)] for v in o["variants"]], numeric=(3, 4, 5))
        facts = [("Result", verdicts.get(res.get("verdict"), label(res.get("state")))), ("Summary", res.get("message"))]
        if res.get("difference_pct") is not None:
            facts.append(("Difference", f"{res['difference_pct']:g}%"))
        d.facts("Outcome", facts)
        if res.get("notes"):
            d.bullets("Keep in mind", res["notes"])
    return d
