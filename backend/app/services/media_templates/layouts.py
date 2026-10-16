"""Agency-grade single-image layouts (also animatable: every element carries a data-anim role).

Every function: (k: Kit, f: dict, photo: str | None) -> HTML document.
"""

from __future__ import annotations

import re

from app.services.media_templates.base import as_list, esc, plain
from app.services.media_templates.kit import Kit, fit_size, icon, icon_for, rich, words


# ----------------------------------------------------------------------------- shared
def _s(v) -> str:
    return str(v or "").strip()


def page(k: Kit, inner: str, *, css: str = "", photo: str | None = None, bg: str | None = None, title: str = "",
         head: str | None = None, foot: str | None = None, deco: str = "", main_cls: str = "", main_style: str = "") -> str:
    background = bg if bg is not None else k.bg(photo)
    head = k.header() if head is None else head
    foot = k.footer() if foot is None else foot
    return k.document(
        f"<div class='canvas'>{background}{deco}<div class='safe' data-exitgroup>{head}"
        f"<div class='main {main_cls}' data-fitbox style='{main_style}'>{inner}</div>{foot}</div>{outro(k)}</div>",
        css, plain(title)[:120])


def outro(k: Kit) -> str:
    """Closing logo sting shown only in intro-mode animations (data-outro)."""
    site = k.brand.website or k.brand.handle or ""
    return (f"<div class='layer' style='display:flex;flex-direction:column;align-items:center;justify-content:safe center;gap:calc(var(--g)*3);z-index:9;pointer-events:none'>"
            f"<div data-outro data-outro-anim='pop'>{k.logo(170)}</div>"
            f"<div data-outro data-outro-anim='rise' class='h2' style='font-size:{k.px(52)}'>{esc(k.brand.name)}</div>"
            + (f"<div data-outro data-outro-anim='fade' class='site' style='font-size:{k.px(28)}'>{esc(site)}</div>" if site else "")
            + "</div>")


def headline(k: Kit, text: str, base: float, *, cls: str = "display", soft: int = 34, floor: float = 44, order: int = 2,
             anim: str = "words", style: str = "", tag: str = "h1") -> str:
    size = fit_size(text, base * k.boost, soft=soft, floor=floor)
    content = words(text) if anim == "words" and k.motion != "still" else rich(text)
    return (f"<{tag} class='{cls}' data-fit data-min='{floor * k.s * .8:.1f}' data-anim='{anim}' data-order='{order}' "
            f"style='font-size:{k.px(size)};{style}'>{content}</{tag}>")


def para(k: Kit, text: str, size: float = 32, *, order: int = 4, cls: str = "lead", style: str = "") -> str:
    if not _s(text):
        return ""
    size *= (1 + (k.boost - 1) * .6)
    return (f"<p class='{cls}' data-fit data-min='{size * k.s * .62:.1f}' data-anim='rise' data-order='{order}' "
            f"style='font-size:{k.px(size)};{style}'>{rich(text)}</p>")


def counter(index: int, total: int) -> str:
    return f"<span class='counter num'>{index:02d} / {total:02d}</span>" if total > 1 else ""


def initials(name: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name)[:2]).upper() or "•"


def parse_stat(stat: str) -> dict:
    """'73%' -> pct; '3x' -> multiple; '₹2.4 Cr' / '10,000+' -> number with prefix/suffix."""
    s = _s(stat)
    m = re.match(r"^\s*([^\d\-+.]*?)\s*([+\-]?\d[\d,]*(?:\.\d+)?)\s*(.*)$", s)
    if not m:
        return {"raw": s, "kind": "text"}
    pre, num, suf = m.group(1), m.group(2), m.group(3).strip()
    group = "in" if re.search(r"\d,\d\d,\d{3}", num) else ("en" if "," in num else "")
    value = float(num.replace(",", ""))
    dec = len(num.split(".")[1]) if "." in num else 0
    kind = "number"
    if suf.startswith("%"):
        kind = "pct"
    elif re.match(r"^(x|×)\b|^(x|×)$", suf, re.I):
        kind = "multiple"
    return {"raw": s, "kind": kind, "prefix": pre, "value": value, "decimals": dec, "suffix": suf if not suf or suf[0] in "%+x×" else " " + suf,
            "group": group}


def count_span(k: Kit, stat: str, order: int = 2, cls: str = "") -> str:
    st = parse_stat(stat)
    if st["kind"] == "text":
        return f"<span class='{cls}' data-anim='scale' data-order='{order}'>{esc(stat)}</span>"
    return (f"<span class='num {cls}' data-anim='count' data-order='{order}' data-count='{st['value']}' data-decimals='{st['decimals']}' "
            f"data-prefix='{esc(st['prefix'])}' data-suffix='{esc(st['suffix'])}' data-group='{st['group']}'>{esc(stat)}</span>")


def kv_list(items: list[str]) -> list[tuple[str, float, str]]:
    """'Label: 42%' lines -> (label, value, shown)."""
    out = []
    for it in items:
        m = re.match(r"^(.*?)[\s:–—-]+\s*([^\d\-]*?)(-?\d[\d,]*(?:\.\d+)?)\s*([%x×A-Za-z+]*)\s*$", it)
        if m and m.group(1).strip():
            v = float(m.group(3).replace(",", ""))
            out.append((m.group(1).strip(), v, f"{m.group(2)}{m.group(3)}{m.group(4)}".strip()))
    return out


# ============================================================================ 1. hook on photo
def photo_headline(k: Kit, f: dict, photo: str | None) -> str:
    text = _s(f.get("headline"))
    body = _s(f.get("body"))
    wide = k.wide
    base = 112 if not wide else 96
    if k.fmt == "story":
        base = 124
    css = (".main{justify-content:flex-end}" if photo else ".main{justify-content:safe center}") + (
        ".main>*{max-width:62%}" if wide else "")
    inner = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
             + headline(k, text, base, soft=30, floor=52)
             + ("<div class='sp3'></div>" + para(k, body, 34) if body else "")
             + ("<div class='sp5'></div>" + k.button(_s(f.get("cta"))) if f.get("cta") else ""))
    deco = "" if photo else k.rings("96%", "8%", 300, 4, .12)
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, scrim="left" if wide else "bottom"), title=text, deco=deco)


# ============================================================================ 2. bold statement
def hook_bold(k: Kit, f: dict, photo: str | None) -> str:
    text = _s(f.get("headline"))
    body = _s(f.get("body"))
    base = 128 if not k.wide else 104
    css = (f".mark{{position:absolute;right:calc(var(--pr) - {k.px(10)});bottom:calc(var(--pb) + {k.px(70)});font-family:var(--font-head);font-weight:800;"
           f"font-size:{k.px(420)};line-height:.8;letter-spacing:-.06em;color:transparent;-webkit-text-stroke:{k.px(2)} rgba(var(--line-rgb),.13);z-index:1}}"
           f".main{{justify-content:safe center}}.bar{{width:{k.px(10)};border-radius:999px;background:linear-gradient(180deg,var(--accent),var(--accent2));flex:none}}"
           f".row{{display:flex;gap:{k.px(40)};align-items:stretch}}" + (".main>*{max-width:78%}" if k.wide else ""))
    eyebrow = _s(f.get("eyebrow"))
    inner = ((f"<div class='kicker' data-anim='rise' data-order='1'>{esc(eyebrow)}</div><div class='sp3'></div>" if eyebrow else "")
             + headline(k, text, base, soft=26, floor=56)
             + (f"<div class='sp4'></div><div class='row'><span class='bar' data-anim='grow' data-order='4'></span>{para(k, body, 34, order=5)}</div>" if body else "")
             + ("<div class='sp5'></div>" + k.button(_s(f.get("cta"))) if f.get("cta") else ""))
    deco = f"<div class='mark' data-anim='blur' data-order='3'>{'→' if not f.get('mark') else esc(f.get('mark'))}</div>"
    return page(k, inner, css=css, photo=photo, title=text, deco=deco)


# ============================================================================ 3. quote
def quote_card(k: Kit, f: dict, photo: str | None) -> str:
    quote = _s(f.get("quote") or f.get("headline")).strip('"“”')
    author, role = _s(f.get("author")), _s(f.get("role") or f.get("company"))
    base = 70 if not k.wide else 58
    if k.fmt == "story":
        base = 76
    css = (f".main{{justify-content:safe center}}.qwrap{{padding:{k.px(64)} {k.px(60)} {k.px(56)};display:flex;flex-direction:column;min-height:0}}"
           f".q{{font-family:var(--font-head);font-weight:650;letter-spacing:-.02em;line-height:1.16;color:var(--fg);text-wrap:balance}}"
           f".who{{display:flex;align-items:center;gap:{k.px(22)};margin-top:{k.px(44)}}}"
           f".who .av{{width:{k.px(76)};height:{k.px(76)};border-radius:50%;display:grid;place-items:center;font-family:var(--font-head);font-weight:800;font-size:{k.px(28)};color:#fff;"
           f"background:linear-gradient(140deg,var(--primary-light),var(--primary));box-shadow:0 0 0 {k.px(5)} rgba(var(--line-rgb),.10);flex:none}}"
           f".who b{{display:block;font-family:var(--font-head);font-weight:750;font-size:{k.px(28)};color:var(--fg)}}"
           f".who span{{display:block;font-size:{k.px(22)};color:var(--fg-mute);margin-top:{k.px(4)}}}"
           f".qb{{position:absolute;top:{k.px(-44)};left:{k.px(56)}}}")
    who = ""
    if author or role:
        who = (f"<div class='who' data-anim='rise' data-order='5'><span class='av'>{esc(initials(author or role))}</span><div>"
               + (f"<b>{esc(author)}</b>" if author else "") + (f"<span>{esc(role)}</span>" if role else "") + "</div></div>")
    qs = fit_size(quote, base, soft=90, floor=36)
    inner = (f"<div class='glass qwrap' data-anim='scale' data-order='1' data-fitbox>"
             f"<div class='qb' data-anim='pop' data-order='2'>{k.badge('quote', 88, .5)}</div>"
             f"<blockquote class='q' data-fit data-min='{30 * k.s:.1f}' data-anim='words' data-order='3' data-wstagger='.03' style='font-size:{k.px(qs)}'>{words(quote)}</blockquote>"
             f"{who}</div>")
    return page(k, inner, css=css, photo=photo, title=quote)


# ============================================================================ 4. big stat
def stat_card(k: Kit, f: dict, photo: str | None) -> str:
    stat = _s(f.get("stat")) or "—"
    label = _s(f.get("stat_label") or f.get("headline"))
    context = _s(f.get("body"))
    source = _s(f.get("source"))
    st = parse_stat(stat)
    chart = _s(f.get("chart")).lower() or {"pct": "donut", "multiple": "bars"}.get(st["kind"], "line" if st["kind"] == "number" else "none")
    n = len(stat)
    big = 230 if n <= 4 else max(120, 230 * (4 / n) ** .75)
    if k.wide:
        big *= .85
    css = (f".main{{justify-content:safe center}}.stat{{font-family:var(--font-head);font-weight:800;letter-spacing:-.055em;line-height:.9;white-space:nowrap;"
           f"background:linear-gradient(105deg,var(--fg) 10%,var(--accent) 60%,var(--accent2) 100%);-webkit-background-clip:text;background-clip:text;color:transparent;padding:.04em .04em .08em 0}}"
           f".row{{display:flex;align-items:center;gap:{k.px(48)};justify-content:space-between}}"
           f".donutwrap{{position:relative;flex:none;display:grid;place-items:center}}"
           f".donutwrap .ctr{{position:absolute;inset:0;display:grid;place-items:center}}"
           f".src{{font-size:{k.px(19)};color:var(--fg-mute);font-weight:500}}"
           f".chartbox{{position:relative;margin-top:{k.px(44)}}}")
    stat_el = (f"<div class='stat' data-fit data-anim='rise' data-order='2' data-min='{90 * k.s:.0f}' style='font-size:{k.px(big)}'>{count_span(k, stat, 2)}</div>")
    lab = headline(k, label, 58 if not k.wide else 50, cls="h2", soft=40, floor=34, order=4, anim="rise", tag="div")
    ctx = ("<div class='sp3'></div>" + para(k, context, 30, order=5)) if context else ""
    chart_html = ""
    if chart == "donut" and st["kind"] == "pct":
        size = 330 if not k.tall else 380
        donut = (f"<div class='donutwrap' data-anim='scale' data-order='1'>{k.donut(st['value'], size, 38)}"
                 f"<div class='ctr'>{k.badge(icon_for(label), 92, .48)}</div></div>")
        if k.fmt in ("square", "wide", "landscape"):
            body = (f"{k.eyebrow(_s(f.get('eyebrow')))}{'<div class=sp3></div>' if f.get('eyebrow') else ''}"
                    f"<div class='row'><div style='min-width:0;flex:1'>{stat_el}<div class='sp2'></div>{lab}{ctx}</div>{donut}</div>")
        else:
            body = (f"<div style='display:flex;justify-content:space-between;align-items:flex-start'>{k.eyebrow(_s(f.get('eyebrow')))}<span></span></div>"
                    f"<div class='sp3'></div>{donut}<div class='sp5'></div>{stat_el}<div class='sp2'></div>{lab}{ctx}")
    else:
        if chart == "bars" and st["kind"] == "multiple":
            v = max(1.2, st["value"])
            chart_html = (f"<div class='chartbox'>{k.bars([1, v], [_s(f.get('before_label')) or 'Before', _s(f.get('after_label')) or 'With ' + k.brand.name], height=300 if k.fmt == 'square' else 360, show_values=['1×', stat])}</div>")
        elif chart == "line":
            vals = [3, 4, 3.6, 5, 4.8, 6.4, 7.1, 6.8, 8.6, 10]
            chart_html = f"<div class='chartbox' style='height:{k.px(240)}'>{k.line_chart(vals, width=(k.w - 200 * k.s) / k.s, height=240)}</div>"
        body = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
                + stat_el + "<div class='sp2'></div>" + lab + ctx + chart_html)
    foot = k.footer(left=f"<span class='src'>{'Source: ' + esc(source) if source else ''}</span>" if source else None)
    return page(k, body, css=css, photo=photo, bg=k.bg(photo, variant="spot"), title=f"{stat} {label}", foot=foot)


# ============================================================================ 5. data highlight (bar chart)
def data_chart(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline"))
    items = kv_list(as_list(f.get("bullets") or f.get("chart_data"), 6))
    if len(items) < 2:
        items = [("2022", 34, "34"), ("2023", 52, "52"), ("2024", 71, "71"), ("2025", 88, "88")]
    take = _s(f.get("body"))
    source = _s(f.get("source"))
    h = {"square": 330, "portrait": 500, "story": 640, "pin": 600}.get(k.fmt, 330)
    css = ".main{justify-content:safe center}"
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "Data") + "<div class='sp3'></div>"
             + headline(k, title, 76 if not k.wide else 64, cls="h1", soft=40, floor=40)
             + ("<div class='sp2'></div>" + para(k, take, 28, order=4) if take else "")
             + f"<div class='sp5'></div><div class='card' style='padding:{k.px(40)} {k.px(40)} {k.px(30)}' data-anim='scale' data-order='3'>"
             + k.bars([v for _, v, _ in items], [lab for lab, _, _ in items], height=h, show_values=[s for _, _, s in items], order=4) + "</div>")
    foot = k.footer(left=f"<span class='site' style='font-weight:500;color:var(--fg-mute)'>{'Source: ' + esc(source) if source else ''}</span>")
    return page(k, inner, css=css, photo=None, title=title, foot=foot)


# ============================================================================ 6. product / feature spotlight
def feature_spotlight(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline"))
    feats = as_list(f.get("bullets"), 3)
    body = _s(f.get("body"))
    device = _s(f.get("device")).lower() or ("phone" if k.tall else "browser")
    shot = (f"<div style=\"position:absolute;inset:0;background:url('{photo}') center/cover\"></div>" if photo
            else k.ui_mock(_s(f.get("screen_title")) or "Overview", compact=device == "phone"))
    fl = "".join(f"<li data-anim='slide-l' data-order='{4 + i}'><span class='ck'>{icon('check', k.px(22), '#fff', 3)}</span><span>{rich(x)}</span></li>"
                 for i, x in enumerate(feats))
    css = (f".feats{{list-style:none;display:flex;flex-direction:column;gap:{k.px(18)};margin-top:{k.px(36)}}}"
           f".feats li{{display:flex;align-items:center;gap:{k.px(18)};font-weight:550;font-size:{k.px(28)};line-height:1.3;color:var(--fg)}}"
           f".ck{{width:{k.px(40)};height:{k.px(40)};border-radius:50%;display:grid;place-items:center;flex:none;background:linear-gradient(140deg,var(--accent),var(--primary-light))}}"
           f".dev{{position:absolute;z-index:2;filter:drop-shadow(0 {k.px(30)} {k.px(50)} rgba(2,12,27,.35))}}"
           ".main{justify-content:flex-start}")
    if k.wide:
        dev = k.browser(shot, 900, .64) if device != "phone" else k.phone(shot, 380)
        pos = (f"right:{k.px(-120)};top:50%;transform:translateY(-50%) perspective({k.px(2200)}) rotateY(-14deg) rotateX(4deg)")
        css += ".main{justify-content:safe center}.main>*{max-width:46%}"
    elif k.fmt == "square" or k.fmt == "landscape":
        dev = k.browser(shot, 760, .64) if device != "phone" else k.phone(shot, 330)
        pos = (f"right:{k.px(-150)};bottom:{k.px(-60)};transform:perspective({k.px(2200)}) rotateY(-16deg) rotateX(6deg)" if device != "phone"
               else f"right:{k.px(70)};bottom:{k.px(-260)};transform:rotate(-6deg)")
        css += ".main>*{max-width:" + ("100%" if device == "phone" else "100%") + "}" + (".main{justify-content:flex-start;padding-top:calc(var(--g)*2)}")
        if device != "phone":
            fl = ""  # no room for both the list and a browser on a square: the device carries the story
        else:
            css += ".main>*{max-width:56%}"
    else:  # portrait / story / pin: text on top, device rising from the bottom
        dev = k.browser(shot, 960, .7) if device != "phone" else k.phone(shot, 440 if k.fmt != "story" else 500)
        pos = (f"left:50%;bottom:{k.px(-40 if device != 'phone' else -300)};transform:translateX(-50%) perspective({k.px(2400)}) rotateX(8deg)")
        css += ".main{justify-content:flex-start}"
        if device != "phone" and k.fmt == "portrait":
            fl = "".join(f"<li data-anim='slide-l' data-order='{4 + i}'><span class='ck'>{icon('check', k.px(22), '#fff', 3)}</span><span>{rich(x)}</span></li>"
                         for i, x in enumerate(feats[:3]))
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "Product") + "<div class='sp3'></div>"
             + headline(k, title, 84 if not k.wide else 72, cls="h1", soft=32, floor=44)
             + ("<div class='sp2'></div>" + para(k, body, 30, order=3) if body and not fl else "")
             + (f"<ul class='feats'>{fl}</ul>" if fl else "")
             + ("<div class='sp4'></div>" + k.button(_s(f.get("cta"))) if f.get("cta") and (k.wide or device == "phone") else ""))
    deco = (f"<div class='dev' style='{pos}'><div data-anim='rise' data-order='2' data-dur='1.3'>{dev}</div></div>")
    foot = k.footer(left="" if not k.wide else None, right="" if not k.wide else None)
    if k.fmt in ("portrait", "story", "pin", "square"):
        foot = "<div class='foot'></div>"
    return page(k, inner, css=css, photo=None, title=title, deco=deco, foot=foot)


# ============================================================================ 7. tips / listicle
def tip_card(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline") or f.get("title"))
    tips = as_list(f.get("bullets"), 5)
    n = max(1, len(tips))
    fs = 30 if n <= 3 else 27 if n == 4 else 25
    fs *= 1 + (k.boost - 1) * .7
    css = (f".main{{justify-content:safe center}}.tips{{list-style:none;display:flex;flex-direction:column;gap:{k.px(16 if n > 3 else 20)};margin-top:{k.px(40)};min-height:0}}"
           f".tips li{{display:flex;align-items:center;gap:{k.px(24)};padding:{k.px(22)} {k.px(28)};min-height:0}}"
           f".tips .n{{font-family:var(--font-head);font-weight:800;font-size:{k.px(30)};width:{k.px(64)};height:{k.px(64)};flex:none;display:grid;place-items:center;"
           f"border-radius:{k.px(18)};color:#fff;background:linear-gradient(140deg,var(--primary-light),var(--primary));box-shadow:0 {k.px(8)} {k.px(20)} rgba(var(--primary-rgb),.3)}}"
           f".tips .t{{font-weight:560;line-height:1.3;color:var(--fg);text-wrap:pretty}}"
           f".tips .ic2{{margin-left:auto;color:var(--accent);opacity:.9}}")
    items = "".join(
        f"<li class='card' data-anim='slide-l' data-order='{3 + i}'><span class='n num'>{i:02d}</span>"
        f"<span class='t' data-fit style='font-size:{k.px(fs)}'>{rich(t)}</span>{icon(icon_for(t, i), k.px(34), sw=1.8, cls='ic2')}</li>"
        for i, t in enumerate(tips, 1))
    inner = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
             + headline(k, title, 76 if not k.wide else 62, cls="h1", soft=34, floor=42)
             + f"<ol class='tips'>{items}</ol>")
    return page(k, inner, css=css, photo=None, title=title)


# ============================================================================ 8. step-by-step
def steps_card(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline"))
    raw = f.get("steps") or f.get("bullets")
    steps = []
    for s in (raw if isinstance(raw, list) else as_list(raw, 5))[:5]:
        if isinstance(s, dict):
            steps.append((_s(s.get("heading") or s.get("title")), _s(s.get("body") or s.get("text"))))
        else:
            a, _, b = str(s).partition(":")
            steps.append((a.strip(), b.strip()) if b.strip() else (str(s).strip(), ""))
    n = max(1, len(steps))
    horiz = k.wide and n <= 4
    ts = (40 if n <= 3 else 34) if k.tall or k.fmt == "portrait" else (34 if n <= 3 else 29)
    css = (f".main{{justify-content:safe center}}.steps{{position:relative;display:flex;flex-direction:{'row' if horiz else 'column'};gap:{k.px((52 if k.tall or k.fmt == 'portrait' else 34) if n <= 3 else 26)};margin-top:{k.px(44)}}}"
           f".step{{position:relative;display:flex;{'flex-direction:column;flex:1' if horiz else ''};gap:{k.px(26)};align-items:flex-start}}"
           f".dot{{position:relative;z-index:2;width:{k.px(76)};height:{k.px(76)};border-radius:50%;flex:none;display:grid;place-items:center;font-family:var(--font-head);"
           f"font-weight:800;font-size:{k.px(30)};color:#fff;background:linear-gradient(140deg,var(--primary-light),var(--primary));"
           f"box-shadow:0 0 0 {k.px(8)} rgba(var(--accent-rgb),.14),0 {k.px(10)} {k.px(24)} rgba(var(--primary-rgb),.35)}}"
           f".rail{{position:absolute;{'top:' + k.px(36) + ';left:' + k.px(36) + ';right:' + k.px(36) + ';height:' + k.px(4) if horiz else 'left:' + k.px(34) + ';top:' + k.px(36) + ';bottom:' + k.px(36) + ';width:' + k.px(4)};"
           f"border-radius:9px;background:linear-gradient({'90deg' if horiz else '180deg'},var(--accent),rgba(var(--accent-rgb),.15));transform-origin:{'left' if horiz else 'top'} center}}"
           f".st b{{display:block;font-family:var(--font-head);font-weight:750;line-height:1.2;color:var(--fg);letter-spacing:-.01em}}"
           f".st span{{display:block;line-height:1.4;color:var(--fg-soft);margin-top:{k.px(6)}}}"
           f".st{{padding-top:{k.px(10) if not horiz else 0};min-width:0}}")
    items = "".join(
        f"<div class='step' data-anim='rise' data-order='{3 + i}'><span class='dot num'>{i}</span><div class='st'>"
        f"<b data-fit style='font-size:{k.px(ts)}'>{rich(h)}</b>" + (f"<span data-fit style='font-size:{k.px(ts * .78)}'>{rich(b)}</span>" if b else "")
        + "</div></div>" for i, (h, b) in enumerate(steps, 1))
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "How it works") + "<div class='sp3'></div>"
             + headline(k, title, 84 if not k.wide else 64, cls="h1", soft=30, floor=42)
             + f"<div class='steps'><span class='rail' data-anim='{'grow-x' if horiz else 'grow'}' data-order='2' data-dur='1.8'></span>{items}</div>")
    return page(k, inner, css=css, photo=None, title=title)


# ============================================================================ 9. myth vs fact / before vs after
def myth_fact(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline"))
    ll, rl = _s(f.get("left_label")) or "Myth", _s(f.get("right_label")) or "Fact"
    left, right = _s(f.get("left") or f.get("myth")), _s(f.get("right") or f.get("fact"))
    side = k.wide or k.fmt == "square"
    fs = 34 if side else 46
    css = (f".main{{justify-content:safe center}}.pair{{display:flex;flex-direction:{'row' if side else 'column'};gap:{k.px(24)};margin-top:{k.px(40)};min-height:0}}"
           f".side{{flex:1;padding:{k.px(40 if side else 48)};display:flex;flex-direction:column;gap:{k.px(20)};min-height:0;min-width:0}}"
           f".lab{{display:flex;align-items:center;gap:{k.px(14)};font-weight:750;font-size:{k.px(24)};letter-spacing:.08em;text-transform:uppercase}}"
           f".ib{{width:{k.px(48)};height:{k.px(48)};border-radius:50%;display:grid;place-items:center;flex:none}}"
           f".myth .lab{{color:var(--fg-mute)}}.myth .ib{{background:rgba(var(--line-rgb),.12)}}"
           f".myth .say{{color:var(--fg-soft);text-decoration:line-through;text-decoration-color:rgba(var(--line-rgb),.35);text-decoration-thickness:{k.px(3)}}}"
           f".fact{{background:linear-gradient(150deg,rgba(var(--primary-rgb),.95),rgba(var(--secondary-rgb),.85));border:0;color:#fff}}"
           f".fact .lab{{color:#fff}}.fact .ib{{background:rgba(255,255,255,.2)}}.fact .say{{color:#fff}}"
           f".say{{font-family:var(--font-head);font-weight:700;line-height:1.22;letter-spacing:-.012em;text-wrap:balance}}")
    strike = ll.lower() in ("myth", "false", "fiction")
    inner = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
             + headline(k, title, 70 if not k.wide else 60, cls="h1", soft=30, floor=40)
             + f"<div class='pair'><div class='side card myth' data-anim='slide-l' data-order='3'><div class='lab'><span class='ib'>{icon('x', k.px(24), 'currentColor', 2.6)}</span>{esc(ll)}</div>"
             f"<div class='say' data-fit style='font-size:{k.px(fs)};{'' if strike else 'text-decoration:none'}'>{rich(left)}</div></div>"
             f"<div class='side card fact' data-anim='slide-r' data-order='4'><div class='lab'><span class='ib'>{icon('check', k.px(26), '#fff', 2.8)}</span>{esc(rl)}</div>"
             f"<div class='say' data-fit style='font-size:{k.px(fs)}'>{rich(right)}</div></div></div>")
    return page(k, inner, css=css, photo=None, title=title)


# ============================================================================ 10. announcement / launch
def announcement(k: Kit, f: dict, photo: str | None) -> str:
    text = _s(f.get("headline"))
    body = _s(f.get("body"))
    date = _s(f.get("date"))
    eyebrow = _s(f.get("eyebrow")) or "Announcement"
    css = (f".main{{justify-content:safe center;align-items:center;text-align:center}}.main .eyebrow{{align-self:center}}.main .btn{{align-self:center}}"
           f".main .lead{{margin-left:auto;margin-right:auto}}"
           f".burst{{position:absolute;left:50%;top:50%;width:{k.px(980)};height:{k.px(980)};margin:{k.px(-490)} 0 0 {k.px(-490)};border-radius:50%;"
           f"background:conic-gradient(from 0deg,transparent 0deg,rgba(var(--accent-rgb),.16) 40deg,transparent 80deg,transparent 180deg,rgba(var(--accent-rgb),.12) 220deg,transparent 260deg);"
           f"-webkit-mask:radial-gradient(closest-side,transparent 30%,#000 31%,#000 70%,transparent 100%);mask:radial-gradient(closest-side,transparent 30%,#000 31%,#000 70%,transparent 100%)}}"
           f".datechip{{display:inline-flex;align-items:center;gap:{k.px(12)};align-self:center;font-weight:650;font-size:{k.px(24)};color:var(--fg);margin-top:{k.px(30)}}}")
    inner = (k.eyebrow(eyebrow) + "<div class='sp4'></div>"
             + headline(k, text, 112 if not k.wide else 92, soft=28, floor=52)
             + ("<div class='sp3'></div>" + para(k, body, 32, style="max-width:30ch") if body else "")
             + (f"<div class='datechip' data-anim='fade' data-order='6'>{icon('calendar', k.px(28), 'var(--accent)')}{esc(date)}</div>" if date else "")
             + ("<div class='sp5'></div>" + k.button(_s(f.get("cta"))) if f.get("cta") else ""))
    deco = f"<div class='burst' data-anim='blur' data-order='0' data-drift='0,0,1'></div>" + k.rings("50%", "50%", 380, 3, .10)
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, variant="spot", scrim="full"), title=text, deco=deco)


# ============================================================================ 11. event / webinar
def event_card(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline"))
    date = _s(f.get("date"))
    m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3,})|([A-Za-z]{3,})\s+(\d{1,2})", date)
    day, mon = ("", "")
    if m:
        day, mon = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    time_ = _s(f.get("time"))
    place = _s(f.get("place")) or "Online"
    speaker = _s(f.get("speaker") or f.get("author"))
    css = (f".main{{justify-content:safe center}}.top{{display:flex;gap:{k.px(36)};align-items:center}}"
           f".cal{{flex:none;width:{k.px(170)};text-align:center;overflow:hidden;padding:0}}"
           f".cal .m{{background:linear-gradient(120deg,var(--primary),var(--primary-light));color:#fff;font-weight:750;font-size:{k.px(24)};letter-spacing:.14em;text-transform:uppercase;padding:{k.px(12)} 0}}"
           f".cal .d{{font-family:var(--font-head);font-weight:800;font-size:{k.px(92)};line-height:1.15;color:var(--fg);letter-spacing:-.04em}}"
           f".meta{{display:flex;flex-direction:column;gap:{k.px(16)};margin-top:{k.px(40)}}}"
           f".meta div{{display:flex;align-items:center;gap:{k.px(16)};font-weight:550;font-size:{k.px(27 * (1 + (k.boost - 1) * .6))};color:var(--fg-soft)}}"
           f".meta .ic{{color:var(--accent)}}")
    rows = "".join(f"<div data-anim='slide-l' data-order='{5 + i}'>{icon(ic, k.px(32))}<span>{esc(v)}</span></div>"
                   for i, (ic, v) in enumerate([("calendar", date if not day else date), ("clock", time_), ("pin", place), ("mic", speaker)]) if v)
    cal = (f"<div class='cal glass' data-anim='pop' data-order='2'><div class='m'>{esc(mon[:3])}</div><div class='d num'>{esc(day)}</div></div>" if day else "")
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "Live webinar") + "<div class='sp4'></div>"
             + (f"<div class='top'>{cal}<div style='min-width:0;flex:1'>{headline(k, title, 76 if not k.wide else 64, cls='h1', soft=30, floor=40)}</div></div>"
                if not k.tall else f"{cal}<div class='sp4'></div>{headline(k, title, 76, cls='h1', soft=30, floor=40)}")
             + f"<div class='meta'>{rows}</div>"
             + ("<div class='sp5'></div>" + k.button(_s(f.get("cta")) or "Register free") if True else ""))
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, scrim="full"), title=title, deco=k.rings("100%", "0%", 360, 4, .12))


# ============================================================================ 12. testimonial
def testimonial(k: Kit, f: dict, photo: str | None) -> str:
    quote = _s(f.get("quote") or f.get("headline")).strip('"“”')
    author, role, company = _s(f.get("author")), _s(f.get("role")), _s(f.get("company"))
    stars = "".join(icon("star", k.px(36), "none", 0, fill="#fbbf24") for _ in range(5))
    css = (f".main{{justify-content:safe center}}.t{{padding:{k.px(56)}}}.stars{{display:flex;gap:{k.px(6)}}}"
           f".tq{{font-family:var(--font-head);font-weight:650;line-height:1.24;letter-spacing:-.015em;color:var(--fg);margin-top:{k.px(30)};text-wrap:pretty}}"
           f".who{{display:flex;align-items:center;gap:{k.px(20)};margin-top:{k.px(40)};padding-top:{k.px(32)};border-top:1px solid var(--card-line)}}"
           f".who .av{{width:{k.px(80)};height:{k.px(80)};border-radius:50%;display:grid;place-items:center;font-family:var(--font-head);font-weight:800;font-size:{k.px(30)};color:#fff;"
           f"background:linear-gradient(140deg,var(--secondary),var(--primary));flex:none}}"
           f".who b{{display:block;font-family:var(--font-head);font-size:{k.px(28)};color:var(--fg)}}.who span{{display:block;font-size:{k.px(22)};color:var(--fg-mute);margin-top:{k.px(2)}}}"
           f".co{{margin-left:auto;font-family:var(--font-head);font-weight:800;font-size:{k.px(26)};color:var(--fg-mute);letter-spacing:-.02em;text-align:right}}")
    qs = fit_size(quote, 46 if not k.wide else 40, soft=120, floor=28)
    who = (f"<div class='who' data-anim='rise' data-order='5'><span class='av'>{esc(initials(author or company))}</span>"
           f"<div style='min-width:0'><b>{esc(author)}</b><span>{esc(role)}</span></div>"
           + (f"<span class='co'>{esc(company)}</span>" if company else "") + "</div>")
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "Client story") + "<div class='sp4'></div>"
             + f"<div class='card t' data-anim='scale' data-order='2' data-fitbox style='min-height:0;display:flex;flex-direction:column'>"
             f"<div class='stars' data-anim='fade' data-order='3'>{stars}</div>"
             f"<p class='tq' data-fit data-anim='rise' data-order='4' style='font-size:{k.px(qs)}'>“{rich(quote)}”</p>{who}</div>")
    return page(k, inner, css=css, photo=None, title=quote)


# ============================================================================ 13. client / logo wall
def logo_wall(k: Kit, f: dict, photo: str | None) -> str:
    title = _s(f.get("headline")) or "Trusted by growing teams"
    names = as_list(f.get("names") or f.get("bullets"), 9)
    n = max(1, len(names))
    cols = 3 if n > 4 or k.wide else 2
    if k.wide and n > 6:
        cols = 4
    css = (f".main{{justify-content:safe center}}.wall{{display:grid;grid-template-columns:repeat({cols},1fr);gap:{k.px(18)};margin-top:{k.px(48)}}}"
           f".tile{{display:flex;align-items:center;justify-content:safe center;gap:{k.px(12)};padding:{k.px(34)} {k.px(18)};text-align:center;min-width:0}}"
           f".tile b{{font-family:var(--font-head);font-weight:800;letter-spacing:-.025em;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
           f".tile i{{width:{k.px(30)};height:{k.px(30)};border-radius:{k.px(9)};flex:none;background:linear-gradient(140deg,var(--accent),var(--primary-light));display:block}}")
    tiles = "".join(f"<div class='tile card' data-anim='scale' data-order='{3 + i * .5}'><i style='border-radius:{k.px(9 if i % 3 else 30)}'></i>"
                    f"<b data-fit style='font-size:{k.px(30 if len(nm) < 12 else 25)}'>{esc(nm)}</b></div>" for i, nm in enumerate(names))
    inner = (k.eyebrow(_s(f.get("eyebrow")) or "Our clients") + "<div class='sp3'></div>"
             + headline(k, title, 76 if not k.wide else 64, cls="h1", soft=30, floor=40)
             + para(k, _s(f.get("body")), 28, order=3, style=f"margin-top:{k.px(18)}")
             + f"<div class='wall'>{tiles}</div>")
    return page(k, inner, css=css, photo=None, title=title)


# ============================================================================ 14. hiring
def hiring(k: Kit, f: dict, photo: str | None) -> str:
    role = _s(f.get("role_title") or f.get("headline")) or "We're hiring"
    chips = [x for x in [_s(f.get("location")), _s(f.get("job_type")), _s(f.get("experience"))] if x]
    perks = as_list(f.get("perks") or f.get("bullets"), 4)
    css = (f".main{{justify-content:safe center}}.chips{{display:flex;flex-wrap:wrap;gap:{k.px(12)};margin-top:{k.px(30)}}}"
           f".chip{{display:inline-flex;align-items:center;gap:{k.px(10)};font-weight:600;font-size:{k.px(23)};color:var(--fg);padding:{k.px(12)} {k.px(20)};border-radius:999px;"
           f"background:var(--chip-bg);border:1px solid var(--chip-line)}}.chip .ic{{color:var(--accent)}}"
           f".perks{{list-style:none;display:grid;grid-template-columns:{'1fr 1fr' if not k.tall else '1fr'};gap:{k.px(14)} {k.px(28)};margin-top:{k.px(40)}}}"
           f".perks li{{display:flex;gap:{k.px(14)};align-items:center;font-weight:520;font-size:{k.px(25)};color:var(--fg-soft);line-height:1.3}}"
           f".perks .ic{{color:var(--accent)}}"
           f".pulse{{width:{k.px(14)};height:{k.px(14)};border-radius:50%;background:#34d399;box-shadow:0 0 0 {k.px(7)} rgba(52,211,153,.2)}}")
    ic_for_chip = ["pin", "briefcase", "clock"]
    ch = "".join(f"<span class='chip' data-anim='pop' data-order='{4 + i * .5}'>{icon(ic_for_chip[i], k.px(26))}{esc(c)}</span>" for i, c in enumerate(chips))
    pk = "".join(f"<li data-anim='slide-l' data-order='{6 + i * .5}'>{icon('check', k.px(28), sw=2.6)}{rich(p)}</li>" for i, p in enumerate(perks))
    eyebrow = _s(f.get("eyebrow")) or "We're hiring"
    inner = (f"<span class='eyebrow' data-anim='rise' data-order='1'><span class='pulse'></span>{esc(eyebrow)}</span><div class='sp4'></div>"
             + headline(k, role, 104 if not k.wide else 86, soft=22, floor=48)
             + (f"<div class='chips'>{ch}</div>" if ch else "")
             + (f"<ul class='perks'>{pk}</ul>" if pk else "")
             + "<div class='sp5'></div>" + k.button(_s(f.get("cta")) or "Apply now"))
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, scrim="full"), title=role, deco=k.rings("100%", "100%", 380, 4, .12))


# ============================================================================ kinetic text (GIF / animation default)
def animated_text(k: Kit, f: dict, photo: str | None) -> str:
    lines = as_list(f.get("lines"), 5) or [x for x in [_s(f.get("headline")), _s(f.get("body"))] if x] or [k.brand.name]
    first = lines[0]
    rest = lines[1:]
    base = 116 if not k.wide else 96
    if k.fmt == "story":
        base = 124
    css = ".main{justify-content:safe center}" + (".main>*{max-width:80%}" if k.wide else "")
    rest_html = "".join(para(k, ln, 38 if len(rest) == 1 else 34, order=4 + i, cls="lead", style="max-width:24ch;margin-top:" + k.px(22))
                        for i, ln in enumerate(rest))
    inner = (k.eyebrow(_s(f.get("eyebrow"))) + ("<div class='sp3'></div>" if f.get("eyebrow") else "")
             + headline(k, first, base, soft=24, floor=56)
             + f"<div class='sp4'></div><span class='rule' data-anim='grow-x' data-order='3'></span>" + rest_html
             + ("<div class='sp5'></div>" + k.button(_s(f.get("cta"))) if f.get("cta") else ""))
    return page(k, inner, css=css, photo=photo, bg=k.bg(photo, scrim="full"), title=first, deco=k.rings("100%", "0%", 320, 4, .12))
