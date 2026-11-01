"""PDF reports for every part of the studio.

A report is built in two steps:
  1. a builder (reports/builders.py, registered with @report) gathers the data for one area and returns a `Doc`:
     a title plus a list of blocks (summary numbers, tables, charts, text, lists, key/value cards);
  2. render_html(doc) lays it out in the same branded A4 design as the marketing report, and
     analytics_reports.render_pdf prints it with the shared headless Chromium.

`generate(session, org, kind, params)` does both and returns (filename, pdf bytes). Builders run in the caller's
session, which is scoped to one organization (row-level security), so a report can only ever contain that
organization's data.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization
from app.services import analytics_reports as ar
from app.services import svg_charts as svg


class ReportInputError(ValueError):
    """Bad parameters (unknown report, missing id, bad dates): shown to the user as is."""


# ------------------------------------------------------------------ document model
@dataclass
class Doc:
    title: str
    subtitle: str = ""
    period: str = ""  # shown top right, e.g. "1 Oct – 19 Oct 2026"
    blocks: list[dict] = field(default_factory=list)

    # builders call these
    def kpis(self, items: list[tuple[str, Any] | tuple[str, Any, str]]) -> None:
        rows = [{"label": i[0], "value": i[1], "hint": i[2] if len(i) > 2 else ""} for i in items]
        if rows:
            self.blocks.append({"type": "kpis", "items": rows})

    def table(self, title: str, columns: list[str], rows: list[list[Any]], *, note: str = "", empty: str = "Nothing here yet.",
              numeric: tuple[int, ...] = (), limit: int = 500) -> None:
        extra = len(rows) - limit
        self.blocks.append({"type": "table", "title": title, "columns": columns, "rows": rows[:limit], "note": note,
                            "empty": empty, "numeric": set(numeric),
                            "more": f"…and {extra} more not shown." if extra > 0 else ""})

    def chart(self, title: str, svg_markup: str, note: str = "") -> None:
        self.blocks.append({"type": "chart", "title": title, "svg": svg_markup, "note": note})

    def bars(self, title: str, pairs: list[tuple[str, float]], *, note: str = "") -> None:
        if pairs:
            self.chart(title, svg.bar_chart([p[0][:14] for p in pairs], [float(p[1] or 0) for p in pairs], width=680,
                                            height=190), note)

    def line(self, title: str, labels: list[str], series: list[dict], *, pct: bool = False, note: str = "") -> None:
        if labels:
            self.chart(title, svg.line_chart(labels, series, width=680, height=190, pct=pct, empty="No data yet."), note)

    def text(self, title: str, body: str | None) -> None:
        if body and str(body).strip():
            self.blocks.append({"type": "text", "title": title, "body": str(body)})

    def bullets(self, title: str, items: list[str], *, empty: str = "") -> None:
        items = [str(i) for i in items if i and str(i).strip()]
        if items or empty:
            self.blocks.append({"type": "list", "title": title, "items": items, "empty": empty})

    def facts(self, title: str, pairs: list[tuple[str, Any]]) -> None:
        pairs = [(k, v) for k, v in pairs if v not in (None, "", [], {})]
        if pairs:
            self.blocks.append({"type": "facts", "title": title, "items": pairs})

    def heading(self, text: str, note: str = "") -> None:
        self.blocks.append({"type": "heading", "text": text, "note": note})

    def html(self, markup: str) -> None:
        """Trusted HTML made by the app (an article body rendered from Markdown)."""
        self.blocks.append({"type": "html", "html": markup})

    def score(self, title: str, value: int | None, label: str = "", parts: list[tuple[str, int | None, str]] = ()) -> None:
        self.blocks.append({"type": "score", "title": title, "value": value, "label": label, "parts": list(parts)})


@dataclass
class Ctx:
    session: AsyncSession
    org: Organization
    tz: ZoneInfo
    start: date
    end: date
    params: dict

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, time.min, self.tz).astimezone(UTC)

    @property
    def end_utc(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, self.tz).astimezone(UTC)

    def local(self, dt: datetime | None, fmt: str = "%d %b %Y, %H:%M") -> str:
        return dt.astimezone(self.tz).strftime(fmt) if dt else "—"

    def day(self, d: date | datetime | None) -> str:
        if d is None:
            return "—"
        if isinstance(d, datetime):
            d = d.astimezone(self.tz).date()
        return d.strftime("%d %b %Y")

    @property
    def period_label(self) -> str:
        return f"{self.start.strftime('%d %b')} – {self.end.strftime('%d %b %Y')}"


Builder = Callable[[Ctx], Awaitable[Doc]]


@dataclass
class ReportSpec:
    kind: str
    title: str
    description: str
    permission: str
    build: Builder
    period: bool = True   # takes a date range
    needs_id: bool = False  # a single item (one post, one article)
    group: str = "Content"


REPORTS: dict[str, ReportSpec] = {}


def catalog() -> dict[str, ReportSpec]:
    from app.services.reports import builders as _builders  # noqa: F401  (registers the builders)

    return REPORTS


def report(kind: str, title: str, description: str, permission: str, *, period: bool = True, needs_id: bool = False,
           group: str = "Content"):
    def deco(fn: Builder) -> Builder:
        REPORTS[kind] = ReportSpec(kind, title, description, permission, fn, period, needs_id, group)
        return fn
    return deco


# ------------------------------------------------------------------ helpers for builders
def num(v, pct: bool = False) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, str):
        return v
    return svg.fmt(v, pct)


def short(text: str | None, n: int = 120) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def label(v: str | None) -> str:
    return str(v or "—").replace("_", " ").strip().capitalize()


# ------------------------------------------------------------------ rendering
EXTRA_CSS = """
.blk { margin-top: 4px; }
.kpis { grid-template-columns: repeat(4, 1fr); }
.kpi .v { font-size: 17px; }
.kpi .d { color: #64748b; }
.facts { display: grid; grid-template-columns: 170px 1fr; gap: 4px 12px; }
.facts .k { color: #64748b; }
.facts .v { white-space: pre-wrap; word-break: break-word; }
.body { white-space: pre-wrap; word-break: break-word; }
td { word-break: break-word; }
.more { color: #64748b; font-size: 10px; margin-top: 4px; }
h3.sec { font-size: 16px; color: #062f59; margin: 22px 0 2px; padding-top: 6px; border-top: 2px solid #e2e8f0; }
.article h1 { font-size: 18px; } .article h2 { font-size: 14px; } .article h3 { font-size: 12.5px; }
.article p, .article li { font-size: 11px; } .article img { max-width: 100%; }
ul.plain { margin: 4px 0 0 16px; padding: 0; } ul.plain li { margin: 2px 0; }
.scorebox { display: flex; gap: 18px; align-items: center; }
"""


def _cell(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return num(v)
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d %b %Y")
    return escape(str(v))


def _block(b: dict) -> str:
    t = b["type"]
    title = f'<h2>{escape(b["title"])}</h2>' if b.get("title") else ""
    if t == "kpis":
        cards = "".join(f'<div class="kpi"><div class="l">{escape(k["label"])}</div><div class="v">{_cell(k["value"])}</div>'
                        f'<div class="d">{escape(k.get("hint") or "")}</div></div>' for k in b["items"])
        return f'<div class="kpis section">{cards}</div>'
    if t == "table":
        head = "".join(f'<th class="{"n" if i in b["numeric"] else ""}">{escape(c)}</th>' for i, c in enumerate(b["columns"]))
        rows = "".join("<tr>" + "".join(f'<td class="{"n" if i in b["numeric"] else ""}">{_cell(v)}</td>'
                                        for i, v in enumerate(r)) + "</tr>" for r in b["rows"])
        if not rows:
            rows = f'<tr><td colspan="{len(b["columns"])}" class="muted">{escape(b["empty"])}</td></tr>'
        note = f'<div class="more">{escape(b["note"])}</div>' if b.get("note") else ""
        more = f'<div class="more">{escape(b["more"])}</div>' if b.get("more") else ""
        return f'<div class="blk">{title}<div class="card"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>{more}{note}</div></div>'
    if t == "chart":
        note = f'<p class="note">{escape(b["note"])}</p>' if b.get("note") else ""
        return f'<div class="blk section">{title}<div class="card">{b["svg"]}{note}</div></div>'
    if t == "text":
        return f'<div class="blk">{title}<div class="card body">{escape(b["body"])}</div></div>'
    if t == "list":
        items = "".join(f"<li>{escape(i)}</li>" for i in b["items"]) or f'<li class="muted">{escape(b.get("empty") or "None.")}</li>'
        return f'<div class="blk">{title}<div class="card"><ul class="plain">{items}</ul></div></div>'
    if t == "facts":
        rows = "".join(f'<div class="k">{escape(str(k))}</div><div class="v">{_cell(v)}</div>' for k, v in b["items"])
        return f'<div class="blk section">{title}<div class="card facts">{rows}</div></div>'
    if t == "heading":
        note = f'<div class="note">{escape(b["note"])}</div>' if b.get("note") else ""
        return f'<h3 class="sec">{escape(b["text"])}</h3>{note}'
    if t == "html":
        return f'<div class="blk card article">{b["html"]}</div>'
    if t == "score":
        parts = "".join(
            f'<div class="part"><span>{escape(p[0])}</span>{svg.meter(p[1], 200)}'
            f'<b style="text-align:right">{p[1] if p[1] is not None else "—"}</b>'
            + (f'<span class="how">{escape(p[2])}</span>' if p[2] else "") + "</div>" for p in b["parts"])
        lab = f'<div class="grade">{escape(b["label"])}</div>' if b.get("label") else ""
        return (f'<div class="blk section">{title}<div class="card scorebox"><div style="text-align:center">'
                f'{svg.score_ring(b["value"])}{lab}</div><div class="parts">{parts}</div></div></div>')
    return ""


def render_html(doc: Doc, org: Organization, logo: str, generated: str) -> str:
    css = ar.CSS.replace("%FONT%", ar._b64("fonts/inter-latin-wght-normal.woff2")) + EXTRA_CSS
    body = "".join(_block(b) for b in doc.blocks) or '<p class="muted">Nothing to report yet.</p>'
    sub = escape(doc.subtitle or org.name)
    meta = f'<b>{escape(doc.period)}</b>' if doc.period else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(doc.title)}</title>
<style>{css}</style></head><body>
<div class="hero"><div class="logo"><img src="{logo}" alt=""></div>
<div><h1>{escape(doc.title)}</h1><div class="sub">{sub}</div></div><div class="meta">{meta}{escape(org.name)}</div></div>
{body}
<p class="note" style="margin-top:16px">Generated {escape(generated)} by HRP Digital Marketing Studio.</p>
</body></html>"""


def parse_period(params: dict, tz: ZoneInfo, default_days: int = 30) -> tuple[date, date]:
    today = datetime.now(tz).date()
    try:
        end = date.fromisoformat(params["end"]) if params.get("end") else today
        start = date.fromisoformat(params["start"]) if params.get("start") else end - timedelta(days=default_days - 1)
    except (TypeError, ValueError):
        raise ReportInputError("Dates must look like 2026-10-19.")
    if start > end:
        start, end = end, start
    if (end - start).days > 366 * 3:
        raise ReportInputError("Pick a period of three years or less.")
    return start, end


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "report"


async def build(session: AsyncSession, org: Organization, kind: str, params: dict) -> tuple[ReportSpec, Doc, Ctx]:
    from app.services.reports import builders as _builders  # noqa: F401  (registers the builders)

    spec = REPORTS.get(kind)
    if spec is None:
        raise ReportInputError("Unknown report.")
    if spec.needs_id and not params.get("id"):
        raise ReportInputError("Choose the item to report on.")
    tz = ZoneInfo(org.timezone or "Asia/Kolkata")
    start, end = parse_period(params, tz)
    ctx = Ctx(session, org, tz, start, end, params)
    doc = await spec.build(ctx)
    if spec.period and not doc.period:
        doc.period = ctx.period_label
    return spec, doc, ctx


async def generate(session: AsyncSession, org: Organization, kind: str, params: dict) -> tuple[str, bytes]:
    if kind == "analytics":  # the existing marketing report has its own richer layout
        tz = ZoneInfo(org.timezone or "Asia/Kolkata")
        start, end = parse_period(params, tz)
        data = await ar.build_data(session, org, start, end)
        pdf = await ar.render_pdf(ar.render_html(data, await ar.logo_data_uri(session)))
        return f"marketing-report-{start.isoformat()}-to-{end.isoformat()}.pdf", pdf
    spec, doc, ctx = await build(session, org, kind, params)
    html = render_html(doc, org, await ar.logo_data_uri(session), datetime.now(ctx.tz).strftime("%d %b %Y, %H:%M %Z"))
    pdf = await render_pdf(html, doc.title)
    stamp = f"-{ctx.start.isoformat()}-to-{ctx.end.isoformat()}" if spec.period else ""
    return f"{slug(doc.title)}{stamp}.pdf", pdf


async def render_pdf(html: str, title: str) -> bytes:
    from app.services import media_render

    try:
        browser = await media_render.get_browser()
    except media_render.RenderError as e:
        raise ar.ReportError(str(e)) from e
    ctx = await browser.new_context(offline=True, java_script_enabled=False)
    try:
        page = await ctx.new_page()
        await page.set_content(html, wait_until="load", timeout=ar.PDF_TIMEOUT_S * 1000)
        footer = ('<div style="font-family:Arial;font-size:8px;color:#94a3b8;width:100%;padding:0 14mm;display:flex;'
                  f'justify-content:space-between"><span>{escape(title)}</span>'
                  '<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
        return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                              display_header_footer=True, header_template="<div></div>", footer_template=footer)
    finally:
        await ctx.close()
