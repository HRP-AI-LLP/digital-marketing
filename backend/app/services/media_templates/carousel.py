"""Carousel / document slides: cover (hook), body slides (auto-varied by content), closing CTA card."""

from __future__ import annotations

import re

from app.services.media_templates.base import as_list, esc
from app.services.media_templates.kit import Kit, fit_size, icon, icon_for, rich
from app.services.media_templates.layouts import _s, count_span, headline, page, para, parse_stat


def _progress(k: Kit, index: int, total: int) -> str:
    total = max(1, min(total, 12))
    segs = "".join(f"<i style='flex:1;height:{k.px(6)};border-radius:9px;background:"
                   f"{'linear-gradient(90deg,var(--accent),var(--accent2))' if i < index else 'rgba(var(--line-rgb),.16)'}'></i>" for i in range(total))
    return f"<div style='display:flex;gap:{k.px(8)};flex:1;max-width:{k.px(360)}'>{segs}</div>"


def _swipe(k: Kit, label: str = "Swipe") -> str:
    return (f"<span style='display:inline-flex;align-items:center;gap:{k.px(14)};font-weight:700;font-size:{k.px(24)};color:var(--fg)'>{esc(label)}"
            f"<span style='width:{k.px(56)};height:{k.px(56)};border-radius:50%;display:grid;place-items:center;"
            f"background:linear-gradient(140deg,var(--primary-light),var(--primary));box-shadow:0 {k.px(8)} {k.px(20)} rgba(var(--primary-rgb),.35)'>"
            f"{icon('arrow', k.px(26), '#fff', 2.6)}</span></span>")


def carousel_cover(k: Kit, f: dict, photo: str | None) -> str:
    text = _s(f.get("headline"))
    sub = _s(f.get("body") or f.get("subtitle"))
    total = int(f.get("total") or 1)
    css = ".main{justify-content:" + ("flex-end" if photo else "center") + "}" + (".main>*{max-width:70%}" if k.wide else "")
    inner = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
             + headline(k, text, 120 if not k.wide else 96, soft=26, floor=56)
             + ("<div class='sp3'></div>" + para(k, sub, 34) if sub else ""))
    foot = (f"<div class='foot' data-anim='fade' data-order='8'><span class='rule'></span>{_swipe(k) if total > 1 else k.site()}</div>")
    head = k.header(f"<span class='counter num'>{total} slides</span>" if total > 2 else "")
    deco = "" if photo else (k.rings("100%", "100%", 420, 5, .12)
                             + f"<div style='position:absolute;right:calc(var(--pr) - {k.px(8)});top:calc(var(--pt) + {k.px(120)});font-family:var(--font-head);"
                               f"font-weight:800;font-size:{k.px(300)};line-height:.8;color:transparent;-webkit-text-stroke:{k.px(2)} rgba(var(--line-rgb),.12);letter-spacing:-.06em'"
                               f" data-anim='blur' data-order='2'>{total - 2 if total > 2 else ''}</div>")
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo), title=text, head=head, foot=foot, deco=deco)


def carousel_slide(k: Kit, f: dict, photo: str | None) -> str:
    heading = _s(f.get("heading") or f.get("headline"))
    body = _s(f.get("body"))
    bullets = as_list(f.get("bullets"), 5)
    index = int(f.get("index") or 2)
    total = int(f.get("total") or index)
    num = int(f.get("number") or max(1, index - 1))
    # automatic variety: a slide that opens with a number becomes a stat slide; sentences with ';' or lists become checklists
    m = re.match(r"^\s*([₹$€£]?\d[\d,.]*\s*(%|x|×|\+)?)\s+(.*)$", heading)
    variant = "stat" if m and len(m.group(1)) <= 8 and (m.group(2) or len(m.group(1)) <= 4) else "list" if bullets else "text"
    css = (f".main{{justify-content:safe center}}.big{{font-family:var(--font-head);font-weight:800;font-size:{k.px(150)};line-height:.85;letter-spacing:-.06em;"
           f"background:linear-gradient(120deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent;padding-bottom:.06em}}"
           f".pts{{list-style:none;display:flex;flex-direction:column;gap:{k.px(16)};margin-top:{k.px(34)}}}"
           f".pts li{{display:flex;gap:{k.px(16)};align-items:flex-start;font-weight:520;line-height:1.35;color:var(--fg)}}"
           f".pts .ic{{color:var(--accent);margin-top:{k.px(3)}}}"
           f".stat{{font-family:var(--font-head);font-weight:800;letter-spacing:-.055em;line-height:.9;white-space:nowrap;"
           f"background:linear-gradient(105deg,var(--fg) 10%,var(--accent) 70%);-webkit-background-clip:text;background-clip:text;color:transparent;padding:.04em 0 .08em}}")
    bs = fit_size(body, 34 if not k.wide else 30, soft=150, floor=24)
    if variant == "stat":
        stat, rest = m.group(1).strip(), m.group(3)
        inner = (f"<div class='stat' data-fit data-anim='rise' data-order='1' style='font-size:{k.px(200 if len(stat) <= 4 else 150)}'>{count_span(k, stat, 1)}</div><div class='sp2'></div>"
                 + headline(k, rest, 60, cls="h2", soft=40, floor=34, order=2, anim="rise", tag="h2")
                 + ("<div class='sp3'></div>" + para(k, body, bs, order=3, cls="txt") if body else ""))
    else:
        pts = "".join(f"<li data-anim='slide-l' data-order='{3 + i * .5}'>{icon('check', k.px(32), sw=2.6)}<span data-fit style='font-size:{k.px(bs)}'>{rich(b)}</span></li>"
                      for i, b in enumerate(bullets))
        inner = (f"<div style='display:flex;align-items:center;gap:{k.px(22)}'><div class='big num' data-anim='blur' data-order='0'>{num:02d}</div>"
                 f"<span data-anim='pop' data-order='1'>{k.badge(icon_for(heading + ' ' + body, num), 76)}</span></div><div class='sp4'></div>"
                 + headline(k, heading, 70 if not k.wide else 58, cls="h1", soft=34, floor=38, order=2, tag="h2")
                 + ("<div class='sp3'></div>" + para(k, body, bs, order=3, cls="txt", style="max-width:40ch") if body else "")
                 + (f"<ul class='pts'>{pts}</ul>" if pts else ""))
    head = k.header(f"<span class='counter num'>{index:02d} / {total:02d}</span>", name=False)
    foot = (f"<div class='foot' data-anim='fade' data-order='9'>{_progress(k, index, total)}"
            + (_swipe(k, "") if index < total else k.site()) + "</div>")
    _ = parse_stat
    return page(k, inner, css=css, photo=None, title=heading, head=head, foot=foot)


def carousel_cta(k: Kit, f: dict, photo: str | None) -> str:
    heading = _s(f.get("heading") or f.get("headline")) or "Found this useful?"
    body = _s(f.get("body")) or "Save it for later and share it with someone who needs it."
    cta = _s(f.get("cta"))
    index = int(f.get("index") or 1)
    total = int(f.get("total") or index)
    css = (f".main{{justify-content:center;align-items:center;text-align:center}}.main .lead{{margin:0 auto}}.main .btn{{align-self:center}}"
           f".acts{{display:flex;gap:{k.px(14)};justify-content:center;flex-wrap:wrap;margin-top:{k.px(40)}}}"
           f".act{{display:inline-flex;align-items:center;gap:{k.px(10)};font-weight:650;font-size:{k.px(23)};color:var(--fg);padding:{k.px(14)} {k.px(22)};border-radius:999px;"
           f"background:var(--chip-bg);border:1px solid var(--chip-line)}}")
    acts = "".join(f"<span class='act' data-anim='pop' data-order='{5 + i * .4}'>{icon(ic, k.px(26))}{esc(t)}</span>"
                   for i, (ic, t) in enumerate([("heart", "Like"), ("doc", "Save"), ("link", "Share"), ("users", f"Follow {k.brand.name}")]))
    inner = (f"<div data-anim='pop' data-order='1' style='display:flex;justify-content:center'>{k.logo(150)}</div><div class='sp5'></div>"
             + headline(k, heading, 84 if not k.wide else 72, cls="h1", soft=24, floor=44, order=2)
             + "<div class='sp3'></div>" + para(k, body, 32, order=3, style="max-width:30ch")
             + f"<div class='acts'>{acts}</div>"
             + ("<div class='sp5'></div>" + k.button(cta, order=7) if cta else ""))
    head = f"<div class='head'><span></span>{'<span class=\"counter num\">' + f'{index:02d} / {total:02d}' + '</span>' if total > 1 else ''}</div>"
    foot = f"<div class='foot' style='justify-content:center' data-anim='fade' data-order='9'>{k.site()}</div>"
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, variant="spot"), title=heading, head=head, foot=foot,
                deco=k.rings("50%", "50%", 300, 4, .14))
