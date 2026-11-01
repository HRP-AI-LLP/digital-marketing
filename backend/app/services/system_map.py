"""System map: the studio's agents (background workers), the services they rely on and the connected accounts,
with each one's live state and how they connect. Feeds the spatial view on the dashboard."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import platform_session
from app.models import Job, Organization, Post, ProviderConfig, SocialAccount, SystemState
from app.services import uptime
from app.services.platforms import PLATFORMS

STALE_SECONDS = 120

# id, name, role, job kinds, periodic task names, layer (0 = thinking, 1 = making, 2 = sending, 3 = learning)
AGENTS = [
    ("planner", "Content planner", "Plans each schedule's slots, approves and dispatches posts on time",
     [], ["content_plan_and_dispatch"], 1),
    ("writer", "AI writer", "Writes every post in your brand voice, checks quality and avoids repeats",
     ["post.generate"], [], 1),
    ("designer", "Media designer", "Creates images, carousels, videos and 3D scenes for each post",
     ["media.generate"], ["media.tmp_cleanup"], 1),
    ("publisher", "Publisher", "Posts to each network at the scheduled time, retries and catches up after downtime",
     ["post.publish"], [], 2),
    ("blog", "Blog autopilot", "Picks a keyword, writes and scores an SEO article, publishes it to the website",
     ["seo.write", "seo.site_publish", "seo.keywords"], ["blog_autopilot"], 2),
    ("tracker", "Performance tracker", "Collects likes, reach, clicks and followers from every channel",
     ["post.metrics", "analytics.collect", "account.refresh"], ["content_metrics_refresh", "analytics_daily_refresh",
                                                                 "analytics_growth_daily", "account_refresh"], 3),
    ("listening", "Social listening", "Finds mentions of HRP AI and competitors across the web",
     ["listening.run", "listening.digest", "listening.summary"], ["listening_schedule", "listening_digest"], 3),
    ("inbox", "Inbox", "Brings comments and replies from your channels into one inbox",
     ["inbox.sync", "inbox.listen"], ["inbox_sync", "inbox_listen"], 3),
    ("research", "Research & ideas", "Reads your site, competitors and trends and suggests topics",
     ["research.run", "intel.run_all", "intel.crawl", "intel.ideas", "intel.competitors", "keywords.research"],
     ["research_daily", "intel_weekly_recrawl"], 0),
    ("evergreen", "Evergreen recycler", "Reshares fresh versions of your best timeless posts",
     [], ["evergreen_recycle"], 0),
    ("occasions", "Festival greetings", "Prepares posts for festivals and special days",
     [], ["occasion_greetings"], 0),
    ("advisor", "Marketing advisor", "Weekly recommendations and scheduled reports",
     ["analytics.advisor", "analytics.report"], ["analytics_advisor_weekly", "analytics_report_schedules"], 3),
]

# who feeds whom (agent → agent)
FLOWS = [
    ("research", "planner", "topic ideas"), ("occasions", "planner", "festival posts"), ("evergreen", "planner", "reshares"),
    ("planner", "writer", "posts to write"), ("writer", "designer", "image briefs"), ("designer", "publisher", "ready posts"),
    ("writer", "publisher", "approved posts"), ("tracker", "advisor", "results"), ("tracker", "research", "what works"),
    ("listening", "research", "conversations"),
]


def _parse(v) -> datetime | None:
    return uptime._parse(v)


async def build(session: AsyncSession, org: Organization, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    day = now - timedelta(hours=24)
    st = await uptime.get_state()
    last_seen = _parse(st.get("last_seen"))
    worker_up = bool(last_seen and (now - last_seen).total_seconds() < uptime.stale_after())
    online = bool(st.get("online", True)) if worker_up else False
    async with platform_session() as ps:
        periodic = {r.value.get("name") or r.key[2:]: r.value for r in (await ps.scalars(
            select(SystemState).where(SystemState.key.like("p:%")))).all()}

    # job counts per kind in the last 24h, plus anything running or waiting now
    rows = (await session.execute(select(Job.kind, Job.status, func.count(), func.max(Job.finished_at)).where(
        Job.organization_id == org.id, (Job.created_at >= day) | Job.status.in_(("queued", "running")))
        .group_by(Job.kind, Job.status))).all()
    jobs: dict[str, dict] = defaultdict(lambda: {"done": 0, "failed": 0, "queued": 0, "running": 0, "last": None})
    for kind, status, n, last in rows:
        j = jobs[kind]
        if status in j:
            j[status] += int(n)
        if last and (j["last"] is None or last > j["last"]):
            j["last"] = last
    last_err = {k: e for k, e in (await session.execute(select(Job.kind, Job.error).where(
        Job.organization_id == org.id, Job.status == "failed", Job.finished_at >= day)
        .order_by(Job.finished_at.desc()))).all() if e}

    llms = (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == "llm"))).all()
    smtp = (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind.in_(("smtp", "email"))))).all()
    websites = (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == "website"))).all()
    pub = org.publishing or {}

    agents = []
    for aid, name, role, kinds, tasks, layer in AGENTS:
        done = sum(jobs[k]["done"] for k in kinds)
        failed = sum(jobs[k]["failed"] for k in kinds)
        running = sum(jobs[k]["running"] for k in kinds)
        queued = sum(jobs[k]["queued"] for k in kinds)
        lasts = [jobs[k]["last"] for k in kinds if jobs[k]["last"]]
        task_runs = [periodic[t] for t in tasks if t in periodic]
        lasts += [_parse(r.get("last_run")) for r in task_runs if _parse(r.get("last_run"))]
        last = max(lasts) if lasts else None
        task_fail = [r for r in task_runs if r.get("ok") is False]
        enabled = True
        note = None
        if aid == "blog" and not (pub.get("blog_autopilot") or {}).get("enabled"):
            enabled, note = False, "Blog autopilot is off (Settings → Organization)."
        if aid == "blog" and enabled and not websites:
            note = "No website connected, so articles stay drafts."
        if not worker_up:
            state, note = "stopped", "The background worker isn't running. Start the studio."
        elif not online:
            state, note = "paused", "No internet: waiting to reconnect."
        elif not enabled:
            state = "off"
        elif running:
            state = "working"
        elif (failed and failed >= done) or task_fail:
            state = "error"
            note = note or (last_err.get(next((k for k in kinds if k in last_err), ""), None)
                            or (task_fail[0].get("error") if task_fail else None))
        elif failed:
            state = "warning"
            note = note or f"{failed} of {done + failed} tasks failed in the last 24 hours."
        elif last and (now - last) < timedelta(hours=24):
            state = "ok"
        else:
            state = "idle"
        agents.append({"id": aid, "name": name, "role": role, "layer": layer, "state": state, "note": note,
                       "done_24h": done, "failed_24h": failed, "running": running, "queued": queued,
                       "last_activity": last.isoformat() if last else None})

    # accounts with their recent activity
    accounts = (await session.scalars(select(SocialAccount).where(
        SocialAccount.status != "removed", SocialAccount.platform != "google"))).all()
    week = now - timedelta(days=7)
    posted = dict((await session.execute(select(Post.social_account_id, func.count()).where(
        Post.status == "published", Post.published_at >= week).group_by(Post.social_account_id))).all())
    last_pub = dict((await session.execute(select(Post.social_account_id, func.max(Post.published_at)).where(
        Post.status == "published").group_by(Post.social_account_id))).all())
    nxt = dict((await session.execute(select(Post.social_account_id, func.min(Post.scheduled_at)).where(
        Post.status.in_(("scheduled", "approved", "in_review", "planned")), Post.scheduled_at >= now)
        .group_by(Post.social_account_id))).all())
    failed_acc = dict((await session.execute(select(Post.social_account_id, func.count()).where(
        Post.status == "failed", Post.updated_at >= week).group_by(Post.social_account_id))).all())
    manual_acc = dict((await session.execute(select(Post.social_account_id, func.count()).where(
        Post.status == "manual_pending").group_by(Post.social_account_id))).all())
    publishing_now = set((await session.scalars(select(Post.social_account_id).where(Post.status == "publishing"))).all())
    acc_out = []
    for a in accounts:
        spec = PLATFORMS.get(a.platform)
        if a.status in ("expired", "error"):
            state = "error"
        elif a.status == "manual":
            state = "manual"
        elif a.id in publishing_now:
            state = "working"
        elif failed_acc.get(a.id):
            state = "warning"
        else:
            state = "ok"
        acc_out.append({
            "id": str(a.id), "name": a.name, "handle": a.handle, "platform": a.platform,
            "platform_label": spec.label if spec else a.platform, "status": a.status, "state": state,
            "note": a.status_message, "posts_7d": int(posted.get(a.id, 0)), "failed_7d": int(failed_acc.get(a.id, 0)),
            "waiting_by_hand": int(manual_acc.get(a.id, 0)),
            "last_published_at": last_pub[a.id].isoformat() if last_pub.get(a.id) else None,
            "next_post_at": nxt[a.id].isoformat() if nxt.get(a.id) else None,
            "needs_public_link": bool(spec and getattr(spec, "needs_public_media", False)) or a.platform in ("instagram", "threads"),
        })

    llm_ok = [p for p in llms if p.enabled]
    services = [
        {"id": "ai", "name": "AI models", "state": "ok" if llm_ok else "error",
         "detail": f"{len(llm_ok)} model{'s' if len(llm_ok) != 1 else ''} switched on" if llm_ok else "No AI model switched on",
         "feeds": ["writer", "designer", "blog", "research", "listening", "advisor"]},
        {"id": "tunnel", "name": "Public link", "state": ("ok" if st.get("public_ok") else "error") if worker_up and st.get("public_base") else "off",
         "detail": st.get("public_base") or "Not set up (only needed for Instagram and Threads media)",
         "feeds": ["publisher"]},
        {"id": "internet", "name": "Internet", "state": "ok" if online else ("stopped" if not worker_up else "error"),
         "detail": "Connected" if online else "Offline", "feeds": ["publisher", "tracker", "listening"]},
        {"id": "email", "name": "Email (SMTP)", "state": "ok" if any(p.enabled for p in smtp) else "off",
         "detail": "Sends reports and alerts" if smtp else "Not set up", "feeds": ["advisor"]},
        {"id": "website", "name": "Website", "state": "ok" if any(w.enabled for w in websites) else "off",
         "detail": websites[0].name if websites else "Not connected (blog articles stay drafts)", "feeds": [], "from": ["blog"]},
    ]
    summary = {
        "agents_working": sum(a["state"] == "working" for a in agents),
        "agents_ok": sum(a["state"] in ("ok", "working", "idle") for a in agents),
        "agents_attention": sum(a["state"] in ("error", "warning", "stopped") for a in agents),
        "accounts_ok": sum(a["state"] in ("ok", "working") for a in acc_out),
        "accounts_attention": sum(a["state"] in ("error", "warning") for a in acc_out),
        "tasks_24h": sum(a["done_24h"] for a in agents), "failed_24h": sum(a["failed_24h"] for a in agents),
        "worker_running": worker_up, "online": online, "last_seen": st.get("last_seen"),
    }
    return {"generated_at": now.isoformat(), "summary": summary, "agents": agents, "flows": [
        {"from": a, "to": b, "label": lbl} for a, b, lbl in FLOWS], "services": services, "accounts": acc_out}
