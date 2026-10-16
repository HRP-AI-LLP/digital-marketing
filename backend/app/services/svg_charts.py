"""Tiny server-side SVG charts for PDF reports and emails (no JavaScript, no CDN).

Same visual rules as the web app's charts: thin 2px lines, recessive slate grid, one y-axis, rounded bar ends,
values and labels in ink colours (never in the series colour). Categorical colours follow a fixed order.
"""

from __future__ import annotations

import math
from html import escape

INK, MUTED, GRID, SURFACE = "#0f172a", "#64748b", "#e2e8f0", "#ffffff"
# Brand blue first, then a validated categorical order (worst adjacent CVD ΔE 9.1).
SERIES = ["#086fb8", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{v / 1000:.1f}k"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi + step * 0.999:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _empty(width: int, height: int, text: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" rx="8" fill="#f8fafc"/>'
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" font-size="12" fill="{MUTED}">{escape(text)}</text></svg>')


def line_chart(labels: list[str], series: list[dict], width: int = 680, height: int = 220, *, pct: bool = False,
               zero: bool = False, empty: str = "No data for this period yet.") -> str:
    """series: [{"name", "values": [float|None]}]. Gaps (None) break the line."""
    vals = [v for s in series for v in s["values"] if v is not None]
    if not labels or not vals:
        return _empty(width, height, empty)
    lo, hi = (0 if zero else min(vals)), max(vals)
    if not zero and hi == lo:
        lo, hi = lo * 0.98 if lo else 0, hi * 1.02 if hi else 1
    ticks = nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    pl, pr, pt, pb = 48, 14, 10, 26
    iw, ih = width - pl - pr, height - pt - pb
    n = len(labels)

    def x(i):
        return pl + (iw / 2 if n == 1 else i * iw / (n - 1))

    def y(v):
        return pt + ih - (v - lo) / ((hi - lo) or 1) * ih

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="Inter, Arial, sans-serif">']
    for t in ticks:
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="{GRID}"/>'
                   f'<text x="{pl - 6}" y="{y(t):.1f}" dy="0.32em" text-anchor="end" font-size="10" fill="{MUTED}">{fmt(t, pct)}</text>')
    every = max(1, math.ceil(n / 7))
    for i, lab in enumerate(labels):
        if i % every == 0 or i == n - 1:
            anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
            out.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="{anchor}" font-size="10" fill="{MUTED}">{escape(lab)}</text>')
    for si, s in enumerate(series):
        color = s.get("color") or SERIES[si % len(SERIES)]
        seg: list[str] = []
        segs: list[list[str]] = []
        for i, v in enumerate(s["values"][:n]):
            if v is None:
                if seg:
                    segs.append(seg)
                seg = []
            else:
                seg.append(f"{x(i):.1f},{y(v):.1f}")
        if seg:
            segs.append(seg)
        dash = ' stroke-dasharray="5 4"' if s.get("dashed") else ""
        for sg in segs:
            if len(sg) == 1:
                cx, cy = sg[0].split(",")
                out.append(f'<circle cx="{cx}" cy="{cy}" r="4" fill="{color}" stroke="{SURFACE}" stroke-width="2"/>')
            else:
                out.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{color}" stroke-width="2" '
                           f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(labels: list[str], values: list[float], width: int = 680, height: int = 200, *, color: str = SERIES[0],
              pct: bool = False, empty: str = "No posts in this period.") -> str:
    """Vertical bars with value labels above each bar."""
    if not labels or not any(values):
        return _empty(width, height, empty)
    ticks = nice_ticks(0, max(values))
    hi = ticks[-1] or 1
    pl, pr, pt, pb = 40, 10, 16, 26
    iw, ih = width - pl - pr, height - pt - pb
    n = len(labels)
    slot = iw / n
    bw = max(4, min(42, slot - 6))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="Inter, Arial, sans-serif">']
    for t in ticks:
        yy = pt + ih - t / hi * ih
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}"/>'
                   f'<text x="{pl - 6}" y="{yy:.1f}" dy="0.32em" text-anchor="end" font-size="10" fill="{MUTED}">{fmt(t, pct)}</text>')
    every = max(1, math.ceil(n / 10))
    for i, (lab, v) in enumerate(zip(labels, values)):
        cx = pl + slot * i + slot / 2
        h = (v or 0) / hi * ih
        top = pt + ih - h
        if h > 0:
            r = min(4, bw / 2, h)
            out.append(f'<path d="M{cx - bw / 2:.1f},{pt + ih:.1f} V{top + r:.1f} Q{cx - bw / 2:.1f},{top:.1f} {cx - bw / 2 + r:.1f},{top:.1f} '
                       f'H{cx + bw / 2 - r:.1f} Q{cx + bw / 2:.1f},{top:.1f} {cx + bw / 2:.1f},{top + r:.1f} V{pt + ih:.1f} Z" fill="{color}"/>')
            if n <= 16:
                out.append(f'<text x="{cx:.1f}" y="{top - 4:.1f}" text-anchor="middle" font-size="10" fill="{INK}">{fmt(v, pct)}</text>')
        if i % every == 0:
            out.append(f'<text x="{cx:.1f}" y="{height - 8}" text-anchor="middle" font-size="10" fill="{MUTED}">{escape(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


def score_ring(score: int | None, size: int = 132) -> str:
    r = size / 2 - 10
    c = 2 * math.pi * r
    frac = (score or 0) / 100
    color = "#0f8dde"
    label = "—" if score is None else str(score)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'font-family="Inter, Arial, sans-serif">'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="{GRID}" stroke-width="10"/>'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="10" '
            f'stroke-linecap="round" stroke-dasharray="{c * frac:.1f} {c:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/>'
            f'<text x="{size / 2}" y="{size / 2 - 2}" text-anchor="middle" font-size="34" font-weight="700" fill="{INK}">{label}</text>'
            f'<text x="{size / 2}" y="{size / 2 + 18}" text-anchor="middle" font-size="11" fill="{MUTED}">out of 100</text></svg>')


def meter(score: int | None, width: int = 160) -> str:
    """A thin horizontal 0–100 meter."""
    w = 0 if score is None else max(0, min(100, score)) / 100 * width
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="8" viewBox="0 0 {width} 8">'
            f'<rect width="{width}" height="8" rx="4" fill="{GRID}"/>'
            + (f'<rect width="{w:.1f}" height="8" rx="4" fill="{SERIES[0]}"/>' if w else "") + "</svg>")
