"""Site audit runs: settings, starting a run, executing it (crawl → PageSpeed → checks → score → save),
new/fixed issues against the previous run, comparison and CSV export."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, SiteAuditIssue, SiteAuditPage, SiteAuditRun, SiteAuditSettings
from app.services import crawler
from app.services.jobs import enqueue
from app.services.siteaudit import checks, pagespeed
from app.services.siteaudit.crawl import AuditConfig, CrawlData, crawl_site

log = logging.getLogger(__name__)

NO_WEBSITE = "Add your website in Organization settings (or a start address in Site audit settings), then run the audit."
KEEP_DETAILED_RUNS = 12  # older runs keep their summary (for the trend) but not page / issue rows

DEFAULTS: dict = {
    "start_url": None, "max_pages": 500, "max_depth": 10, "include": [], "exclude": [], "user_agent": "",
    "psi_pages": 10, "weekday": 0, "run_time": "06:00", "drop_threshold": 5, "check_external": True,
}
LIMITS = {"max_pages": (1, 2000), "max_depth": (1, 50), "psi_pages": (0, 25), "weekday": (0, 6), "drop_threshold": (1, 50)}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def now() -> datetime:
    return datetime.now(UTC)


def org_tz(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone if org and org.timezone else "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def hhmm(v: str) -> time:
    try:
        h, m = str(v).split(":")
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return time(6, 0)


# ------------------------------------------------------------------ settings
async def get_settings_row(s: AsyncSession) -> SiteAuditSettings | None:
    return await s.scalar(select(SiteAuditSettings).limit(1))


def effective_config(row: SiteAuditSettings | None) -> dict:
    cfg = {**DEFAULTS, **((row.config or {}) if row else {})}
    cfg["enabled"] = row.enabled if row else True
    for k, (lo, hi) in LIMITS.items():
        try:
            cfg[k] = max(lo, min(hi, int(cfg[k])))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    cfg["include"] = [str(x)[:200] for x in cfg.get("include") or [] if str(x).strip()][:30]
    cfg["exclude"] = [str(x)[:200] for x in cfg.get("exclude") or [] if str(x).strip()][:30]
    return cfg


# ------------------------------------------------------------------ starting
async def start_run(s: AsyncSession, org_id: uuid.UUID, *, trigger: str = "manual", run_key: str | None = None,
                    created_by: uuid.UUID | None = None) -> SiteAuditRun:
    """Create a run and its job in the caller's transaction (caller commits)."""
    run = SiteAuditRun(organization_id=org_id, trigger=trigger, run_key=run_key, status="queued", created_by=created_by,
                       config={}, summary={}, stats={}, site={}, performance={})
    s.add(run)
    await s.flush()
    job = await enqueue(s, "siteaudit.run", org_id, {"run_id": str(run.id)}, dedupe_key=f"siteaudit.run:{run.id}",
                        priority=7, max_attempts=1, created_by=created_by)
    run.job_id = job.id
    return run


# ------------------------------------------------------------------ executing
def _compact_page(rec: dict) -> dict:
    """What the pages explorer shows; drops the long link lists already summarised elsewhere."""
    out = {k: v for k, v in rec.items() if k != "facts"}
    f = dict(rec.get("facts") or {})
    for k in ("internal_links", "external_links"):
        f.pop(k, None)
    f["links"] = (f.get("links") or [])[:150]
    f["images"] = (f.get("images") or [])[:60]
    out["facts"] = f
    return out


def _psi_targets(d: CrawlData, info: dict[str, dict], n: int) -> list[str]:
    if n <= 0:
        return []
    ok = [u for u, r in d.pages.items() if checks.ok_html(r)]
    home = d.home if d.home in ok else (ok[0] if ok else None)
    rest = sorted((u for u in ok if u != home and info.get(u, {}).get("indexable")),
                  key=lambda u: (-info[u]["inlinks"], info[u]["depth"] if info[u]["depth"] is not None else 99, len(u)))
    return ([home] if home else []) + rest[: n - 1 if home else n]


async def execute(s: AsyncSession, run: SiteAuditRun, org: Organization | None, progress=None, psi_key: str | None = None) -> dict:
    cfg = effective_config(await get_settings_row(s))
    start = (cfg.get("start_url") or (org.website if org else None) or "").strip()
    run.status, run.started_at, run.error = "running", now(), None
    run.config = {k: v for k, v in cfg.items()}
    await s.commit()
    if not start:
        return await _fail(s, run, NO_WEBSITE)
    acfg = AuditConfig(start_url=start, max_pages=cfg["max_pages"], max_depth=cfg["max_depth"], include=cfg["include"],
                       exclude=cfg["exclude"], user_agent=(cfg.get("user_agent") or "").strip() or crawler.USER_AGENT,
                       check_external=bool(cfg.get("check_external", True)))
    try:
        d = await crawl_site(acfg, progress)
    except crawler.UnsafeURLError as e:
        return await _fail(s, run, str(e))
    run.start_url = acfg.start_url
    if d.fatal:
        return await _fail(s, run, d.fatal)

    issues, info = checks.evaluate(d, None)
    perf: dict = {}
    targets = _psi_targets(d, info, cfg["psi_pages"])
    if targets:
        if progress:
            await progress(f"Measuring page speed for {len(targets)} pages (PageSpeed Insights)")
        try:
            perf = await pagespeed.measure(targets, psi_key, progress)
        except Exception as e:  # noqa: BLE001 — speed data is optional and never blocks the audit
            log.warning("PageSpeed step failed: %s", e)
            perf = {"pages": [], "summary": {}, "error": "Page speed couldn't be measured this time."}
        issues, info = checks.evaluate(d, perf)

    if progress:
        await progress("Scoring and saving the results")
    return await _save(s, run, d, issues, info, perf, cfg)


async def _fail(s: AsyncSession, run: SiteAuditRun, msg: str) -> dict:
    run.status, run.error, run.finished_at = "failed", msg, now()
    await s.commit()
    return {"run_id": str(run.id), "status": "failed", "error": msg}


async def previous_done(s: AsyncSession, run: SiteAuditRun) -> SiteAuditRun | None:
    return (await s.scalars(select(SiteAuditRun).where(
        SiteAuditRun.status == "done", SiteAuditRun.id != run.id, SiteAuditRun.created_at < run.created_at)
        .order_by(SiteAuditRun.created_at.desc()).limit(1))).first()


async def issue_keys(s: AsyncSession, run_id: uuid.UUID) -> set[tuple[str, str]]:
    rows = (await s.execute(select(SiteAuditIssue.check_id, SiteAuditIssue.url).where(SiteAuditIssue.run_id == run_id))).all()
    return {(c, u) for c, u in rows}


async def _save(s: AsyncSession, run: SiteAuditRun, d: CrawlData, issues: list[dict], info: dict, perf: dict, cfg: dict) -> dict:
    org_id = run.organization_id
    prev = await previous_done(s, run)
    prev_keys = await issue_keys(s, prev.id) if prev else set()
    prev_has_detail = bool(prev_keys) or bool(prev and not (prev.summary or {}))

    # one row per (check, url): merge duplicates
    merged: dict[tuple[str, str], dict] = {}
    for i in issues:
        merged.setdefault((i["check"], i["url"]), i)
    keys = set(merged)
    per_page: dict[str, Counter] = defaultdict(Counter)
    summary: dict[str, dict] = {}
    for (cid, url), i in merged.items():
        sev = checks.CHECKS[cid].severity
        per_page[url][sev] += 1
        row = summary.setdefault(cid, {"count": 0, "new": 0, "fixed": 0})
        row["count"] += 1
        is_new = bool(prev) and prev_has_detail and (cid, url) not in prev_keys
        if is_new:
            row["new"] += 1
        s.add(SiteAuditIssue(organization_id=org_id, run_id=run.id, check_id=cid, severity=sev, url=url[:4000],
                             detail=_jsonable(i["detail"]), is_new=is_new))
    if prev and prev_has_detail:
        for cid, _url in prev_keys - keys:
            if cid in checks.CHECKS:
                summary.setdefault(cid, {"count": 0, "new": 0, "fixed": 0})["fixed"] += 1

    for url in d.order:
        rec = d.pages[url]
        f = rec.get("facts") or {}
        pi = info.get(url, {})
        c = per_page.get(url, Counter())
        s.add(SiteAuditPage(
            organization_id=org_id, run_id=run.id, url=url[:4000], final_url=rec.get("final_url"), status=rec.get("status") or 0,
            content_type=(rec.get("content_type") or "")[:120] or None, depth=pi.get("depth"), response_ms=rec.get("ttfb_ms"),
            size_bytes=rec.get("html_bytes") or rec.get("size"), word_count=f.get("word_count"), title=(f.get("title") or None),
            indexable=bool(pi.get("indexable")), in_sitemap=bool(pi.get("in_sitemap")), inlinks=pi.get("inlinks", 0),
            errors=c["error"], warnings=c["warning"], notices=c["notice"], text_ratio=f.get("text_ratio"),
            data=_jsonable({**_compact_page(rec), "inlinks_from": pi.get("inlinks_from", [])}),
        ))

    counts = {cid: v["count"] for cid, v in summary.items()}
    sev_totals = Counter()
    for cid, n in counts.items():
        sev_totals[checks.CHECKS[cid].severity] += n
    total_pages = len(d.pages)
    run.score = checks.health_score(counts, total_pages)
    run.pages_crawled = total_pages
    run.errors, run.warnings, run.notices = sev_totals["error"], sev_totals["warning"], sev_totals["notice"]
    run.summary = summary
    html_pages = sum(1 for r in d.pages.values() if checks.ok_html(r))
    statuses = Counter(_status_bucket(r) for r in d.pages.values())
    run.stats = {
        "pages": total_pages, "html_pages": html_pages, "indexable": sum(1 for v in info.values() if v.get("indexable")),
        "statuses": dict(statuses), "external_checked": len(d.external), "images_checked": len(d.images),
        "sitemap_urls": len(d.sitemap_urls), "blocked_by_robots": len(d.blocked), "truncated": d.truncated,
        "new_issues": sum(v["new"] for v in summary.values()), "fixed_issues": sum(v["fixed"] for v in summary.values()),
        "previous_run_id": str(prev.id) if prev else None, "previous_score": prev.score if prev else None,
        "avg_response_ms": _avg([r.get("ttfb_ms") for r in d.pages.values() if checks.ok_html(r)]),
        "max_depth": max((v["depth"] for v in info.values() if v.get("depth") is not None), default=None),
    }
    robots = dict(d.robots or {})
    run.site = _jsonable({
        "home": d.home, "origin": d.origin, "https": d.home.startswith("https://"), "robots": robots,
        "sitemaps": d.sitemaps[:30], "llms_txt": d.llms, "ai_access": d.ai_access, "http_variant": d.http_variant,
        "host_variant": d.host_variant, "favicon": d.favicon,
    })
    run.performance = _jsonable(perf or {})
    run.status, run.finished_at = "done", now()
    await s.flush()
    await _prune(s)
    await s.commit()
    return {"run_id": str(run.id), "status": "done", "score": run.score, "pages": total_pages, "errors": run.errors,
            "warnings": run.warnings, "notices": run.notices, "new_issues": run.stats["new_issues"],
            "fixed_issues": run.stats["fixed_issues"], "previous_score": run.stats["previous_score"]}


def _status_bucket(r: dict) -> str:
    s = r.get("status") or 0
    if r.get("chain"):
        return "3xx"
    if s == 0:
        return "failed"
    return f"{s // 100}xx"


def _avg(vals: list) -> int | None:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals)) if vals else None


def _jsonable(v):
    return json.loads(json.dumps(v, default=str))


async def _prune(s: AsyncSession) -> None:
    keep = (await s.scalars(select(SiteAuditRun.id).where(SiteAuditRun.status == "done")
                            .order_by(SiteAuditRun.created_at.desc()).limit(KEEP_DETAILED_RUNS))).all()
    if len(keep) < KEEP_DETAILED_RUNS:
        return
    old = select(SiteAuditRun.id).where(SiteAuditRun.id.not_in(keep), SiteAuditRun.status.in_(("done", "failed")))
    await s.execute(delete(SiteAuditIssue).where(SiteAuditIssue.run_id.in_(old)))
    await s.execute(delete(SiteAuditPage).where(SiteAuditPage.run_id.in_(old)))


# ------------------------------------------------------------------ reading
def issue_groups(run: SiteAuditRun) -> list[dict]:
    """Checks with occurrences (or fixed since the last run), with catalog text, sorted by severity then count."""
    order = {"error": 0, "warning": 1, "notice": 2}
    out = []
    for cid, v in (run.summary or {}).items():
        chk = checks.CHECKS.get(cid)
        if chk is None:
            continue
        out.append({**chk.out(), "count": v.get("count", 0), "new": v.get("new", 0), "fixed": v.get("fixed", 0)})
    out.sort(key=lambda x: (x["count"] == 0, order[x["severity"]], -x["count"], x["title"]))
    return out


async def compare(s: AsyncSession, a: SiteAuditRun, b: SiteAuditRun, limit: int = 500) -> dict:
    """a = older run, b = newer run."""
    ka, kb = await issue_keys(s, a.id), await issue_keys(s, b.id)
    detailed = bool(ka or not a.summary) and bool(kb or not b.summary)
    rows = []
    for cid in sorted(set(a.summary or {}) | set(b.summary or {})):
        chk = checks.CHECKS.get(cid)
        if chk is None:
            continue
        ca = (a.summary or {}).get(cid, {}).get("count", 0)
        cb = (b.summary or {}).get(cid, {}).get("count", 0)
        if ca == 0 and cb == 0:
            continue
        rows.append({"id": cid, "title": chk.title, "severity": chk.severity, "category": chk.category,
                     "category_label": checks.CATEGORIES[chk.category], "before": ca, "after": cb, "change": cb - ca})
    order = {"error": 0, "warning": 1, "notice": 2}
    rows.sort(key=lambda r: (order[r["severity"]], -abs(r["change"]), r["title"]))

    def lst(keys: set[tuple[str, str]]) -> list[dict]:
        items = sorted(keys, key=lambda k: (order[checks.CHECKS[k[0]].severity] if k[0] in checks.CHECKS else 3, k[0], k[1]))
        return [{"check": c, "title": checks.CHECKS[c].title, "severity": checks.CHECKS[c].severity, "url": u}
                for c, u in items[:limit] if c in checks.CHECKS]

    return {
        "before": run_brief(a), "after": run_brief(b), "checks": rows, "detailed": detailed,
        "new": lst(kb - ka) if detailed else [], "fixed": lst(ka - kb) if detailed else [],
        "new_count": len(kb - ka) if detailed else None, "fixed_count": len(ka - kb) if detailed else None,
    }


def run_brief(r: SiteAuditRun) -> dict:
    return {"id": r.id, "status": r.status, "trigger": r.trigger, "score": r.score, "pages": r.pages_crawled,
            "errors": r.errors, "warnings": r.warnings, "notices": r.notices, "created_at": r.created_at,
            "started_at": r.started_at, "finished_at": r.finished_at, "start_url": r.start_url, "error": r.error,
            "new_issues": (r.stats or {}).get("new_issues"), "fixed_issues": (r.stats or {}).get("fixed_issues"),
            "job_id": r.job_id}


# ------------------------------------------------------------------ CSV
_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _cell(v) -> str:
    """CSV cell safe against spreadsheet formula injection."""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    v = str(v)
    return "'" + v if _FORMULA.match(v) else v


async def export_csv(s: AsyncSession, run: SiteAuditRun, kind: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "pages":
        w.writerow(["URL", "Status", "Final URL", "Title", "Words", "Click depth", "Internal links in", "Response ms",
                    "Size bytes", "Indexable", "In sitemap", "Errors", "Warnings", "Notices"])
        rows = (await s.scalars(select(SiteAuditPage).where(SiteAuditPage.run_id == run.id).order_by(SiteAuditPage.url))).all()
        for p in rows:
            w.writerow([_cell(x) for x in (p.url, p.status, p.final_url if p.final_url != p.url else "", p.title, p.word_count,
                                            p.depth, p.inlinks, p.response_ms, p.size_bytes, "yes" if p.indexable else "no",
                                            "yes" if p.in_sitemap else "no", p.errors, p.warnings, p.notices)])
    else:
        w.writerow(["Severity", "Category", "Issue", "URL", "New since last audit", "Details", "How to fix"])
        rows = (await s.scalars(select(SiteAuditIssue).where(SiteAuditIssue.run_id == run.id)
                                .order_by(SiteAuditIssue.check_id, SiteAuditIssue.url))).all()
        order = {"error": 0, "warning": 1, "notice": 2}
        rows = sorted(rows, key=lambda i: (order.get(i.severity, 3), i.check_id, i.url))
        for i in rows:
            chk = checks.CHECKS.get(i.check_id)
            if chk is None:
                continue
            w.writerow([_cell(x) for x in (chk.severity, checks.CATEGORIES[chk.category], chk.title, i.url,
                                            "yes" if i.is_new else "no", i.detail or "", chk.fix)])
    return buf.getvalue()


async def count_pages(s: AsyncSession, run_id: uuid.UUID) -> int:
    return await s.scalar(select(func.count()).select_from(SiteAuditPage).where(SiteAuditPage.run_id == run_id)) or 0
