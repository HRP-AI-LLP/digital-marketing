"""Branded PDF marketing reports, report schedules and email delivery.

- build_data(...) gathers the numbers for a period (KPIs vs the previous period, audience growth, posts per week,
  platform breakdown, top posts, competitor benchmark, health score and open tips).
- render_html(...) turns them into a print-ready A4 page with inline SVG charts and embedded fonts/logo
  (no network access is needed: Chromium renders with offline=True).
- render_pdf(...) prints it with the shared headless Chromium (app.services.media_render.get_browser).
- produce(...) is the job body: build → render → store under storage/orgs/<org>/reports/ → email the recipients.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time as _time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import get_settings
from app.models import (
    AnalyticsReport, BrandProfile, MediaAsset, Organization, ProviderConfig, Recommendation, ReportSchedule,
)
from app.services import analytics_competitors as comp
from app.services import analytics_growth as growth
from app.services import analytics_health as health
from app.services import svg_charts as svg
from app.services.platforms import PLATFORMS

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "media_assets"
PDF_TIMEOUT_S = 60


class ReportError(Exception):
    pass


# ------------------------------------------------------------------ periods & schedules
def default_period(frequency: str, today: date) -> tuple[date, date]:
    """weekly → the 7 days up to yesterday; monthly → the previous calendar month."""
    if frequency == "monthly":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    end = today - timedelta(days=1)
    return end - timedelta(days=6), end


def next_run(s: ReportSchedule, tz: ZoneInfo, after: datetime) -> datetime:
    """Next time (UTC) strictly after `after` for a weekly/monthly schedule at s.time in the org's time zone."""
    hh, mm = (int(x) for x in (s.time or "09:00").split(":"))
    local = after.astimezone(tz)
    d = local.date()
    for _ in range(400):
        if s.frequency == "monthly":
            ok = d.day == min(max(1, s.month_day or 1), 28)
        else:
            ok = d.weekday() == (s.weekday or 0)
        if ok:
            at = datetime.combine(d, time(hh, mm), tzinfo=tz)
            if at > local:
                return at.astimezone(UTC)
        d += timedelta(days=1)
    raise ReportError("Couldn't work out the next send time.")


# ------------------------------------------------------------------ signed download links
def _sig(report_id: str, org_id: str, exp: int) -> str:
    msg = f"report:{report_id}:{org_id}:{exp}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def file_url(report_id, org_id, ttl: int = 3600) -> str:
    exp = int(_time.time()) + ttl
    return f"/api/v1/analytics/reports/{report_id}/file?o={org_id}&e={exp}&s={_sig(str(report_id), str(org_id), exp)}"


def verify(report_id: str, org_id: str, exp: int, sig: str) -> bool:
    return exp >= _time.time() and hmac.compare_digest(_sig(report_id, org_id, exp), sig)


# ------------------------------------------------------------------ data
def _utc_bounds(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (datetime.combine(start, time(0), tzinfo=tz).astimezone(UTC),
            datetime.combine(end + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC))


def _plat_label(p: str) -> str:
    return PLATFORMS[p].label if p in PLATFORMS else p.replace("_", " ").capitalize()


async def build_data(session: AsyncSession, org: Organization, start: date, end: date) -> dict:
    tz = growth.org_zone(org)
    length = (end - start).days + 1
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=length - 1)
    cur_rows = await growth.post_rows(session, *_utc_bounds(start, end, tz))
    prev_rows = await growth.post_rows(session, *_utc_bounds(p_start, p_end, tz))
    cur, prev = growth.window_totals(cur_rows), growth.window_totals(prev_rows)
    series = await growth.account_series(session, p_start - timedelta(days=1))
    f_end, _ = growth.followers_at(series, end)
    net = growth.net_followers(series, start - timedelta(days=1), end)
    net_prev = growth.net_followers(series, p_start - timedelta(days=1), p_end)

    kpis = [
        {"label": "Followers", "value": f_end, "delta": None, "hint": f"{net:+,} in this period" if net is not None else "Collecting history"},
        {"label": "New followers", "value": net, "delta": growth.pct(net, net_prev)},
        {"label": "Posts published", "value": cur["posts"], "delta": growth.pct(cur["posts"], prev["posts"])},
        {"label": "Impressions", "value": cur["impressions"], "delta": growth.pct(cur["impressions"], prev["impressions"])},
        {"label": "Engagements", "value": cur["engagements"], "delta": growth.pct(cur["engagements"], prev["engagements"])},
        {"label": "Engagement rate", "value": cur["engagement_rate"], "pct": True,
         "delta": growth.pct(cur["engagement_rate"], prev["engagement_rate"])},
    ]

    days = [start + timedelta(days=i) for i in range(length)]
    fol_series = [growth.followers_at(series, d)[0] for d in days]

    weeks: dict[date, int] = defaultdict(int)
    eng_week: dict[date, list] = defaultdict(list)
    for p, m in cur_rows:
        d = p.published_at.astimezone(tz).date()
        wk = d - timedelta(days=d.weekday())
        weeks[wk] += 1
        eng_week[wk].append((p, m))
    wk0 = start - timedelta(days=start.weekday())
    week_keys = []
    w = wk0
    while w <= end:
        week_keys.append(w)
        w += timedelta(days=7)

    plat: dict[str, dict] = {}
    for p, m in cur_rows:
        r = plat.setdefault(p.platform, {"platform": p.platform, "label": _plat_label(p.platform), "posts": 0,
                                         "impressions": 0, "engagements": 0, "followers": None, "net": None})
        r["posts"] += 1
        r["impressions"] += int(m.impressions or 0) if m else 0
        r["engagements"] += growth._eng(m)
    for acc_rows in series.values():
        if not acc_rows:
            continue
        pf = acc_rows[0].platform
        pts = [(r.day, r.followers) for r in acc_rows]
        a, b = growth._value_on(pts, start - timedelta(days=1)), growth._value_on(pts, end)
        r = plat.setdefault(pf, {"platform": pf, "label": _plat_label(pf), "posts": 0, "impressions": 0, "engagements": 0,
                                 "followers": None, "net": None})
        if b is not None:
            r["followers"] = (r["followers"] or 0) + b
            if a is not None:
                r["net"] = (r["net"] or 0) + (b - a)
    for r in plat.values():
        r["engagement_rate"] = round(r["engagements"] / r["impressions"], 4) if r["impressions"] else None

    top = sorted(cur_rows, key=lambda pm: (growth._eng(pm[1]), (pm[1].impressions if pm[1] else 0)), reverse=True)[:6]
    top_posts = [{"title": (p.title or p.topic or (p.text or "")[:90] or "Untitled").strip(), "platform": _plat_label(p.platform),
                  "date": p.published_at.astimezone(tz).strftime("%d %b"), "impressions": m.impressions if m else None,
                  "engagements": growth._eng(m) if m else None,
                  "rate": round(growth._eng(m) / m.impressions, 4) if m and m.impressions else None} for p, m in top]

    bench = await comp.benchmark(session, org)
    score = await health.compute(session, org)
    recs = (await session.scalars(select(Recommendation).where(Recommendation.status == "new")
                                  .order_by(Recommendation.rank).limit(6))).all()
    return {
        "org": org.name, "website": org.website, "timezone": str(tz),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": length,
                   "label": f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
                   "previous": f"{p_start.strftime('%d %b')} – {p_end.strftime('%d %b %Y')}"},
        "kpis": kpis,
        "followers": {"labels": [d.strftime("%d %b") for d in days], "values": fol_series},
        "weeks": {"labels": [w.strftime("%d %b") for w in week_keys], "posts": [weeks.get(w, 0) for w in week_keys],
                  "rates": [growth.window_totals(eng_week.get(w, []))["engagement_rate"] for w in week_keys]},
        "platforms": sorted(plat.values(), key=lambda r: (-(r["followers"] or 0), -r["posts"])),
        "top_posts": top_posts,
        "benchmark": {"rows": bench["channels"]["all"]["rows"], "insights": bench["channels"]["all"]["insights"],
                      "share": bench["channels"]["all"]["share_of_voice"]},
        "health": {"score": score["score"], "grade": score["grade"], "parts": score["parts"]},
        "recommendations": [{"title": r.title, "why": r.why, "action": r.action_text, "impact": r.impact, "effort": r.effort}
                            for r in recs],
        "generated_at": datetime.now(tz).strftime("%d %b %Y, %H:%M"),
    }


# ------------------------------------------------------------------ HTML
@lru_cache
def _b64(name: str) -> str:
    return base64.b64encode((ASSETS / name).read_bytes()).decode()


async def logo_data_uri(session: AsyncSession) -> str:
    """The brand kit logo when one is set, else the HRP AI mark."""
    brand = await session.scalar(select(BrandProfile))
    if brand and brand.logo_asset_id:
        asset = await session.get(MediaAsset, brand.logo_asset_id)
        if asset is not None and (asset.mime or "").startswith("image/"):
            try:
                data = storage.abs_path(asset.path).read_bytes()
                return f"data:{asset.mime};base64,{base64.b64encode(data).decode()}"
            except (OSError, ValueError):
                log.warning("brand logo %s unreadable", asset.id)
    return f"data:image/png;base64,{_b64('logo-mark.png')}"


def _num(v, pct: bool = False) -> str:
    return svg.fmt(v, pct)


def _delta(d: float | None) -> str:
    if d is None:
        return '<span class="muted">No earlier data</span>'
    cls = "up" if d > 0 else "down" if d < 0 else "muted"
    arrow = "▲" if d > 0 else "▼" if d < 0 else "•"
    return f'<span class="{cls}">{arrow} {abs(d):.1f}%</span> <span class="muted">vs previous</span>'


CSS = """
@font-face { font-family: 'Inter'; src: url(data:font/woff2;base64,%FONT%) format('woff2'); font-weight: 100 900; }
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, Arial, sans-serif; color: #0f172a; font-size: 11px; line-height: 1.45;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.hero { background: #062f59; background-image: radial-gradient(circle at 85% 0%, rgba(34,211,238,.35), transparent 45%),
        radial-gradient(circle at 0% 100%, rgba(15,141,222,.45), transparent 50%);
        color: #fff; border-radius: 14px; padding: 22px 24px; display: flex; align-items: center; gap: 18px; }
.hero .logo { width: 58px; height: 58px; border-radius: 14px; background: #fff; display: grid; place-items: center; flex: none; }
.hero .logo img { width: 50px; height: 50px; object-fit: contain; }
.hero h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
.hero .sub { color: #bae6fd; font-size: 12px; margin-top: 2px; }
.hero .meta { margin-left: auto; text-align: right; font-size: 11px; color: #cbd5e1; }
.hero .meta b { color: #fff; font-size: 13px; display: block; }
h2 { font-size: 14px; margin: 18px 0 7px; color: #062f59; font-weight: 700; }
h2 small { font-weight: 400; color: #64748b; font-size: 11px; margin-left: 6px; }
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px; }
.kpi { border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }
.kpi .l { color: #64748b; font-size: 10.5px; }
.kpi .v { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 1px 0; }
.kpi .d { font-size: 10px; }
.up { color: #047857; } .down { color: #b91c1c; } .muted { color: #64748b; }
.card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th { text-align: left; color: #64748b; font-weight: 500; font-size: 10px; border-bottom: 1px solid #e2e8f0; padding: 5px 6px; }
td { padding: 6px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
td.n, th.n { text-align: right; }
tr.us td { background: #f0f7ff; font-weight: 600; }
.health { display: flex; gap: 18px; align-items: center; }
.parts { flex: 1; }
.part { display: grid; grid-template-columns: 130px 1fr 34px; gap: 8px; align-items: center; padding: 3px 0; }
.part .how { grid-column: 1 / -1; color: #64748b; font-size: 9.5px; margin-top: -2px; }
.grade { display: inline-block; background: #e0f2fe; color: #075492; border-radius: 6px; padding: 2px 8px; font-weight: 600; font-size: 11px; }
.rec { border-left: 3px solid #0f8dde; padding: 6px 10px; margin: 6px 0; background: #f8fafc; border-radius: 0 8px 8px 0;
       break-inside: avoid; }
.rec b { font-size: 11.5px; }
.tag { display: inline-block; font-size: 9.5px; border-radius: 4px; padding: 1px 5px; margin-left: 4px; background: #e2e8f0; color: #334155; }
.tag.high { background: #dbeafe; color: #1e3a8a; }
.lead li { margin: 2px 0; }
.section { break-inside: avoid; }
svg { max-width: 100%; height: auto; display: block; }
.note { color: #64748b; font-size: 10px; }
"""


def render_html(d: dict, logo: str) -> str:
    css = CSS.replace("%FONT%", _b64("fonts/inter-latin-wght-normal.woff2"))
    kpis = "".join(
        f'<div class="kpi"><div class="l">{escape(k["label"])}</div>'
        f'<div class="v">{_num(k["value"], k.get("pct", False))}</div>'
        f'<div class="d">{escape(k["hint"]) if k.get("hint") else _delta(k.get("delta"))}</div></div>' for k in d["kpis"])

    has_followers = any(v is not None for v in d["followers"]["values"])
    fol_chart = svg.line_chart(d["followers"]["labels"], [{"name": "Followers", "values": d["followers"]["values"]}],
                               width=680, height=165, empty="Follower history starts with the first daily collection.")
    wk_chart = svg.bar_chart(d["weeks"]["labels"], d["weeks"]["posts"], width=330, height=150)
    er_chart = svg.line_chart(d["weeks"]["labels"], [{"name": "Engagement rate", "values": d["weeks"]["rates"]}],
                              width=330, height=150, pct=True, zero=True, empty="No post metrics yet.")

    plat_rows = "".join(
        f'<tr><td>{escape(r["label"])}</td><td class="n">{_num(r["followers"])}</td>'
        f'<td class="n">{("+" if (r["net"] or 0) > 0 else "") + _num(r["net"]) if r["net"] is not None else "—"}</td>'
        f'<td class="n">{r["posts"]}</td><td class="n">{_num(r["impressions"])}</td><td class="n">{_num(r["engagements"])}</td>'
        f'<td class="n">{_num(r["engagement_rate"], True)}</td></tr>' for r in d["platforms"]) or \
        '<tr><td colspan="7" class="muted">No connected accounts or posts in this period.</td></tr>'

    top_rows = "".join(
        f'<tr><td>{escape(p["title"][:90])}</td><td>{escape(p["platform"])}</td><td>{p["date"]}</td>'
        f'<td class="n">{_num(p["impressions"])}</td><td class="n">{_num(p["engagements"])}</td><td class="n">{_num(p["rate"], True)}</td></tr>'
        for p in d["top_posts"]) or '<tr><td colspan="6" class="muted">No posts were published in this period.</td></tr>'

    h = d["health"]
    parts = "".join(
        f'<div class="part"><span>{escape(p["label"])}</span>{svg.meter(p["score"], 200)}'
        f'<b style="text-align:right">{p["score"] if p["score"] is not None else "—"}</b>'
        f'<span class="how">{escape(p["value"])} · {escape(p["how"])}</span></div>' for p in h["parts"])

    b = d["benchmark"]
    share = {s["id"]: s for s in b["share"]}
    bench_rows = "".join(
        f'<tr class="{"us" if r["is_us"] else ""}"><td>{escape(r["name"])}{" (you)" if r["is_us"] else ""}</td>'
        f'<td class="n">{_num(r.get("followers"))}</td><td class="n">{_num(r.get("net_30d"))}</td>'
        f'<td class="n">{_num(r.get("posts_30d"))}</td><td class="n">{_num(r.get("avg_engagement"))}</td>'
        f'<td class="n">{_num((share.get(r["id"]) or {}).get("engagement_share"), True)}</td></tr>' for r in b["rows"])
    lead = "".join(f"<li>{escape(x['text'])}</li>" for x in b["insights"].get("lead", []))
    lag = "".join(f"<li>{escape(x['text'])}</li>" for x in b["insights"].get("lag", []))
    bench_html = (
        f'<table><thead><tr><th>Brand</th><th class="n">Followers</th><th class="n">Gained (30 days)</th>'
        f'<th class="n">Posts (30 days)</th><th class="n">Engagement per post</th><th class="n">Share of voice</th></tr></thead>'
        f'<tbody>{bench_rows}</tbody></table>'
        + (f'<div class="grid2" style="margin-top:8px"><div><b>Where you lead</b><ul class="lead">{lead or "<li class=muted>Nothing yet.</li>"}</ul></div>'
           f'<div><b>Where you lag</b><ul class="lead">{lag or "<li class=muted>Nothing yet.</li>"}</ul></div></div>')
        if len(b["rows"]) > 1 else
        '<p class="muted">Add competitors\' social handles on the Analytics → Competitors tab to compare.</p>')

    recs = "".join(
        f'<div class="rec"><b>{i + 1}. {escape(r["title"])}</b><span class="tag {r["impact"]}">{r["impact"]} impact</span>'
        f'<span class="tag">{r["effort"]} effort</span>'
        + (f'<div class="muted">{escape(r["why"])}</div>' if r.get("why") else "")
        + (f'<div>{escape(r["action"])}</div>' if r.get("action") else "") + "</div>"
        for i, r in enumerate(d["recommendations"])) or '<p class="muted">No open tips. Generate fresh ones on the Health &amp; tips tab.</p>'

    p = d["period"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Marketing report</title><style>{css}</style></head>
<body>
<div class="hero"><div class="logo"><img src="{logo}" alt=""></div>
<div><h1>Marketing report</h1><div class="sub">{escape(d['org'])}{' · ' + escape(d['website']) if d.get('website') else ''}</div></div>
<div class="meta"><b>{escape(p['label'])}</b>{p['days']} days · compared with {escape(p['previous'])}</div></div>

<div class="kpis">{kpis}</div>

<div class="section"><h2>Audience growth <small>total followers across connected accounts</small></h2>
<div class="card">{fol_chart}{'' if has_followers else '<p class="note">The app records follower counts once a day; the trend fills in over time.</p>'}</div></div>

<div class="grid2 section">
<div><h2>Posts per week</h2><div class="card">{wk_chart}</div></div>
<div><h2>Engagement rate by week</h2><div class="card">{er_chart}</div></div>
</div>

<div class="section"><h2>Platform breakdown</h2><div class="card"><table><thead><tr><th>Platform</th><th class="n">Followers</th>
<th class="n">Gained</th><th class="n">Posts</th><th class="n">Impressions</th><th class="n">Engagements</th><th class="n">Rate</th></tr></thead>
<tbody>{plat_rows}</tbody></table></div></div>

<div class="section"><h2>Top posts</h2><div class="card"><table><thead><tr><th>Post</th><th>Platform</th><th>Date</th>
<th class="n">Impressions</th><th class="n">Engagements</th><th class="n">Rate</th></tr></thead><tbody>{top_rows}</tbody></table></div></div>

<div class="section"><h2>Marketing health</h2><div class="card health">
<div style="text-align:center">{svg.score_ring(h['score'])}<div class="grade">{escape(h['grade'])}</div></div>
<div class="parts">{parts}</div></div></div>

<div class="section"><h2>Competitor benchmark <small>public numbers, last 30 days</small></h2><div class="card">{bench_html}</div></div>

<h2>How to improve</h2>{recs}
<p class="note" style="margin-top:14px">Generated {escape(d['generated_at'])} ({escape(d['timezone'])}) by HRP Digital Marketing Studio.
Numbers come from each platform's official API; “—” means the platform doesn't share that number.</p>
</body></html>"""


async def render_pdf(html: str) -> bytes:
    from app.services import media_render

    try:
        browser = await media_render.get_browser()
    except media_render.RenderError as e:
        raise ReportError(str(e)) from e
    ctx = await browser.new_context(offline=True, java_script_enabled=False)
    try:
        page = await ctx.new_page()
        await page.set_content(html, wait_until="load", timeout=PDF_TIMEOUT_S * 1000)
        footer = ('<div style="font-family:Arial;font-size:8px;color:#94a3b8;width:100%;padding:0 14mm;display:flex;'
                  'justify-content:space-between"><span>Marketing report</span>'
                  '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
        return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                              display_header_footer=True, header_template="<div></div>", footer_template=footer)
    finally:
        await ctx.close()


# ------------------------------------------------------------------ email
async def smtp_config(session: AsyncSession) -> ProviderConfig | None:
    return await session.scalar(select(ProviderConfig).where(ProviderConfig.kind == "smtp", ProviderConfig.enabled.is_(True)))


def email_html(d: dict) -> str:
    cells = "".join(
        f'<td style="padding:8px 10px;border:1px solid #e2e8f0;border-radius:8px"><div style="color:#64748b;font-size:12px">'
        f'{escape(k["label"])}</div><div style="font-size:20px;font-weight:700">{_num(k["value"], k.get("pct", False))}</div></td>'
        for k in d["kpis"][:3])
    cells2 = "".join(
        f'<td style="padding:8px 10px;border:1px solid #e2e8f0"><div style="color:#64748b;font-size:12px">'
        f'{escape(k["label"])}</div><div style="font-size:20px;font-weight:700">{_num(k["value"], k.get("pct", False))}</div></td>'
        for k in d["kpis"][3:])
    h = d["health"]
    return (f'<p style="color:#334155">Here is the marketing report for <b>{escape(d["org"])}</b>, '
            f'{escape(d["period"]["label"])}. The full PDF is attached.</p>'
            f'<table style="border-collapse:separate;border-spacing:6px;width:100%"><tr>{cells}</tr><tr>{cells2}</tr></table>'
            f'<p style="color:#334155">Marketing health: <b>{h["score"] if h["score"] is not None else "—"}/100</b> '
            f'({escape(h["grade"])}).</p>')


async def email_report(session: AsyncSession, report: AnalyticsReport, data: dict, pdf: bytes) -> str | None:
    """Send to every recipient. Returns an error message for the UI, or None when all went out."""
    from app.services import notify

    if not report.recipients:
        return None
    cfg = await smtp_config(session)
    if cfg is None:
        return "Email isn't set up. Add your SMTP server in Settings → Integrations, then press Send now."
    fname = f"marketing-report-{report.period_start.isoformat()}-to-{report.period_end.isoformat()}.pdf"
    failed = []
    for to in report.recipients:
        try:
            await notify.send_email(cfg, to, report.title, f"The marketing report for {data['period']['label']} is attached.",
                                    "/analytics?tab=reports", attachments=[(fname, pdf, "application/pdf")],
                                    html=email_html(data))
        except Exception as e:  # noqa: BLE001 - report one line per failed recipient
            log.warning("report email to %s failed: %s", to, e)
            failed.append(f"{to}: {str(e)[:160]}")
    if failed:
        return "Couldn't email " + "; ".join(failed)
    return None


# ------------------------------------------------------------------ the job body
def report_title(org: Organization, start: date, end: date) -> str:
    return f"{org.name} marketing report, {start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"


async def produce(session: AsyncSession, report_id: uuid.UUID) -> dict:
    report = await session.get(AnalyticsReport, report_id)
    if report is None:
        raise ReportError("The report was deleted.")
    org = await session.get(Organization, report.organization_id)
    try:
        data = await build_data(session, org, report.period_start, report.period_end)
        html = render_html(data, await logo_data_uri(session))
        pdf = await render_pdf(html)
    except Exception as e:  # noqa: BLE001 - keep the failure visible on the report row
        log.exception("report %s failed", report_id)
        report.status, report.error = "failed", (str(e) or type(e).__name__)[:1000]
        await session.flush()
        return {"status": "failed", "error": report.error}
    rel, _ = storage.save_bytes(org.id, "reports", pdf, "pdf")
    report.path, report.size_bytes, report.status, report.error = rel, len(pdf), "ready", None
    report.summary = {"kpis": data["kpis"], "health": data["health"]["score"], "grade": data["health"]["grade"]}
    report.email_error = await email_report(session, report, data, pdf)
    if report.recipients and not report.email_error:
        report.sent_at = datetime.now(UTC)
    if report.schedule_id:
        sched = await session.get(ReportSchedule, report.schedule_id)
        if sched is not None:
            sched.last_sent_at = report.sent_at or sched.last_sent_at
            sched.last_status = report.email_error or f"Sent to {len(report.recipients)} recipient(s)."
    await session.flush()
    return {"status": "ready", "report_id": str(report.id), "emailed": bool(report.sent_at), "email_error": report.email_error}


def report_out(r: AnalyticsReport) -> dict:
    return {"id": str(r.id), "title": r.title, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
            "status": r.status, "trigger": r.trigger, "schedule_id": str(r.schedule_id) if r.schedule_id else None,
            "size_bytes": r.size_bytes, "recipients": r.recipients or [], "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "email_error": r.email_error, "error": r.error, "summary": r.summary or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": file_url(r.id, r.organization_id) if r.status == "ready" and r.path else None}


def schedule_out(s: ReportSchedule) -> dict:
    return {"id": str(s.id), "name": s.name, "frequency": s.frequency, "weekday": s.weekday, "month_day": s.month_day,
            "time": s.time, "recipients": s.recipients or [], "active": s.active,
            "next_run_at": s.next_run_at.isoformat() if s.next_run_at and s.active else None,
            "last_sent_at": s.last_sent_at.isoformat() if s.last_sent_at else None, "last_status": s.last_status}

