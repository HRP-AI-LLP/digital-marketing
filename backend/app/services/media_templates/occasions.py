"""Festive seasons, national days and awareness days: respectful, brand-aware greeting templates.

All artwork is original and procedural (inline SVG/CSS), never copied imagery. National days use abstract
tricolour ribbons only - no depiction of the National Flag or State Emblem (Flag Code of India / Emblem Act) and
no deities. Each occasion has a palette for its art; the brand's logo, name and website stay on every card.

`match_occasion(meta_occasion, text)` -> occasion key, used for automatic template selection.
"""

from __future__ import annotations

import math
import re

from app.services.media_templates.base import esc, mix, plain
from app.services.media_templates.kit import Kit, fit_size, icon, rich, words

# key: (label, regex on the occasion name / post text, palette)
#   palette: bg0, bg1 (background gradient), a1, a2 (accents), theme (dark|light)
OCCASIONS: dict[str, dict] = {
    "diwali": {"label": "Diwali", "re": r"diwali|deepavali|deepawali|festival of lights", "default": "Happy Diwali",
               "bg0": "#14051f", "bg1": "#3b0d2e", "a1": "#ffb547", "a2": "#ff7a1a", "theme": "dark",
               "msg": "May the festival of lights bring warmth, prosperity and new beginnings."},
    "holi": {"label": "Holi", "re": r"\bholi\b|festival of colou?rs", "default": "Happy Holi",
             "bg0": "#fffaf3", "bg1": "#fff1f7", "a1": "#e11d74", "a2": "#7c3aed", "theme": "light",
             "msg": "Wishing you a season full of colour, laughter and fresh starts."},
    "eid": {"label": "Eid", "re": r"\beid\b|eid[- ]ul[- ]fitr|eid[- ]al[- ]adha|bakri?d|ramzan|ramadan", "default": "Eid Mubarak",
            "bg0": "#03191f", "bg1": "#0b3b45", "a1": "#f2c46d", "a2": "#7fe0d2", "theme": "dark",
            "msg": "May this Eid bring peace, joy and blessings to you and your loved ones."},
    "christmas": {"label": "Christmas", "re": r"christmas|xmas|x-mas", "default": "Merry Christmas",
                  "bg0": "#06221a", "bg1": "#0d3b2c", "a1": "#f5d07a", "a2": "#e2484d", "theme": "dark",
                  "msg": "Wishing you a joyful season and a peaceful year ahead."},
    "new_year": {"label": "New Year", "re": r"new year|\bnye\b|happy 20\d\d", "default": "Happy New Year",
                 "bg0": "#05081a", "bg1": "#12123a", "a1": "#f7d27a", "a2": "#8ab4ff", "theme": "dark",
                 "msg": "Here's to new goals, bold ideas and a great year together."},
    "independence_day": {"label": "Independence Day", "re": r"independence day|15(th)? august|august 15", "default": "Happy Independence Day",
                         "bg0": "#fbfaf7", "bg1": "#f1f5f2", "a1": "#ff9933", "a2": "#138808", "theme": "light", "tricolour": True,
                         "msg": "Celebrating the spirit of freedom and the people building India's future."},
    "republic_day": {"label": "Republic Day", "re": r"republic day|26(th)? january|january 26", "default": "Happy Republic Day",
                     "bg0": "#fbfaf7", "bg1": "#f1f5f2", "a1": "#ff9933", "a2": "#138808", "theme": "light", "tricolour": True,
                     "msg": "Honouring the Constitution and the values that unite us."},
    "ganesh_chaturthi": {"label": "Ganesh Chaturthi", "re": r"ganesh|ganesha|vinayaka|chaturthi", "default": "Happy Ganesh Chaturthi",
                         "bg0": "#2a0a06", "bg1": "#5a1a0a", "a1": "#ffb000", "a2": "#ff6a00", "theme": "dark",
                         "msg": "May new beginnings be smooth and every obstacle be cleared."},
    "navratri": {"label": "Navratri / Dussehra", "re": r"navratri|navaratri|dussehra|dasara|dussera|vijayadashami|durga puja|garba", "default": "Happy Navratri",
                 "bg0": "#1b0630", "bg1": "#43105a", "a1": "#ffcc33", "a2": "#ff4f8b", "theme": "dark",
                 "msg": "Nine nights of colour, rhythm and the triumph of good."},
    "onam": {"label": "Onam", "re": r"\bonam\b|thiruvonam", "default": "Happy Onam",
             "bg0": "#fffbea", "bg1": "#f6f9e8", "a1": "#f59e0b", "a2": "#15803d", "theme": "light",
             "msg": "Wishing you a harvest of happiness and a home full of flowers."},
    "pongal": {"label": "Pongal / Sankranti", "re": r"pongal|sankranti|sankranthi|lohri|bihu|uttarayan", "default": "Happy Pongal",
               "bg0": "#fff7e6", "bg1": "#ffe9c7", "a1": "#f97316", "a2": "#16a34a", "theme": "light",
               "msg": "May the harvest season bring abundance, warmth and gratitude."},
    "womens_day": {"label": "Women's Day", "re": r"women'?s day|womens day|international women", "default": "Happy Women's Day",
                   "bg0": "#1d0b33", "bg1": "#3d1466", "a1": "#f0abfc", "a2": "#a78bfa", "theme": "dark",
                   "msg": "To the women who lead, build and inspire, today and every day."},
    "teachers_day": {"label": "Teachers' Day", "re": r"teachers'? day|teacher'?s day|guru purnima", "default": "Happy Teachers' Day",
                     "bg0": "#f7fbff", "bg1": "#eef6ff", "a1": "#0f8dde", "a2": "#f59e0b", "theme": "light",
                     "msg": "Thank you to every teacher and mentor who helped us grow."},
    "tech_day": {"label": "Engineers' / Technology Day", "re": r"engineers'? day|engineer'?s day|technology day|tech day|national science day",
                 "default": "Happy Engineers' Day", "bg0": "", "bg1": "", "a1": "", "a2": "", "theme": "dark", "brand": True,
                 "msg": "Celebrating the builders who turn ideas into things that work."},
    "festive": {"label": "Festive (any occasion)", "re": r"festival|festive|celebrat|season'?s greetings|greetings", "default": "Season's Greetings",
                "bg0": "", "bg1": "", "a1": "", "a2": "", "theme": "dark", "brand": True,
                "msg": "Warm wishes to you and your family from all of us."},
    "awareness": {"label": "Awareness day (any)", "re": r"\bworld\b.*\bday\b|international .*\bday\b|awareness|\bday of\b", "default": "",
                  "bg0": "", "bg1": "", "a1": "", "a2": "", "theme": "light", "brand": True,
                  "msg": "Small actions, taken together, make a real difference."},
}
CATEGORY_FALLBACK = {"festival": "festive", "national": "independence_day", "global_day": "awareness", "industry_day": "tech_day"}


def match_occasion(occ: dict | str | None = None, text: str | None = None) -> str | None:
    """Occasion key from post.meta['occasion'] ({name, category, date} or a string), else from the post text."""
    name, cat = "", ""
    if isinstance(occ, dict):
        name, cat = str(occ.get("name") or ""), str(occ.get("category") or "")
    elif isinstance(occ, str):
        name = occ
    for key, o in OCCASIONS.items():
        if key in ("festive", "awareness"):
            continue
        if name and re.search(o["re"], name, re.I):
            return key
    if name or cat:
        if cat in CATEGORY_FALLBACK:
            return CATEGORY_FALLBACK[cat] if cat != "national" else ("republic_day" if re.search(r"republic", name, re.I) else
                                                                  "independence_day" if re.search(r"independence", name, re.I) else "festive")
        return "awareness" if re.search(OCCASIONS["awareness"]["re"], name, re.I) else "festive"
    t = plain(text or "")[:600]
    if t:
        for key, o in OCCASIONS.items():
            if key in ("festive", "awareness"):
                continue
            if re.search(r"(happy|wish|greet|celebrat|mubarak|merry|warm)", t, re.I) and re.search(o["re"], t, re.I):
                return key
    return None


# ----------------------------------------------------------------------------- art helpers
def _pal(k: Kit, key: str) -> dict:
    o = OCCASIONS[key]
    c = k.brand.colors
    if o.get("brand"):
        if o["theme"] == "light":
            return {"bg0": "#ffffff", "bg1": mix(c["light"], c["secondary_light"], .18), "a1": c["primary_light"], "a2": c["secondary"], "theme": "light"}
        return {"bg0": mix(c["dark"], "#000000", .35), "bg1": mix(c["dark"], c["primary"], .45), "a1": c["secondary_light"], "a2": c["primary_light"], "theme": "dark"}
    return {k2: o[k2] for k2 in ("bg0", "bg1", "a1", "a2", "theme")}


def _bokeh(k: Kit, n: int, colors: list[str], *, top: float = 0, bottom: float = 100, rmin: float = 6, rmax: float = 26, op: float = .5, seed: int = 0) -> str:
    out = []
    for i in range(n):
        x, y = k.rnd(seed + i * 3, 0, 100), k.rnd(seed + i * 3 + 1, top, bottom)
        r = k.rnd(seed + i * 3 + 2, rmin, rmax) * k.s
        col = colors[i % len(colors)]
        out.append(f"<i data-tw='{1 + i % 3},{k.rnd(seed + i, 0, 1):.2f},.3' style='position:absolute;left:{x:.1f}%;top:{y:.1f}%;width:{2 * r:.0f}px;height:{2 * r:.0f}px;"
                   f"margin:{-r:.0f}px 0 0 {-r:.0f}px;border-radius:50%;background:radial-gradient(closest-side,{col} 0%,{col}88 40%,transparent 100%);opacity:{op}'></i>")
    return "<div class='layer' style='pointer-events:none'>" + "".join(out) + "</div>"


def _sparkles(k: Kit, n: int, color: str, *, seed: int = 50, top: float = 0, bottom: float = 100, size: float = 22) -> str:
    out = []
    for i in range(n):
        x, y = k.rnd(seed + i * 5, 2, 98), k.rnd(seed + i * 5 + 1, top, bottom)
        sz = k.rnd(seed + i * 5 + 2, .5, 1.3) * size * k.s
        out.append(f"<svg data-tw='{2 + i % 3},{k.rnd(seed + i * 7, 0, 1):.2f},.15' viewBox='-10 -10 20 20' style='position:absolute;left:{x:.1f}%;top:{y:.1f}%;"
                   f"width:{sz:.0f}px;height:{sz:.0f}px;overflow:visible'><path d='M0-10C1 -2 2 -1 10 0C2 1 1 2 0 10C-1 2 -2 1 -10 0C-2 -1 -1 -2 0-10Z' fill='{color}'/></svg>")
    return "<div class='layer' style='pointer-events:none'>" + "".join(out) + "</div>"


def _mandala(cx: float, cy: float, r: float, colors: list[str], *, rings: int = 4, petals: int = 12, stroke: str = "none", op: float = 1) -> str:
    """Concentric petal rings (rangoli / pookalam style), drawn procedurally."""
    g = [f"<g transform='translate({cx},{cy})' opacity='{op}'>"]
    for ring in range(rings, 0, -1):
        rr = r * ring / rings
        n = petals + (ring - 1) * 4
        col = colors[ring % len(colors)]
        pw = rr * math.pi / n * .95
        for i in range(n):
            a = 360 * i / n + (180 / n if ring % 2 else 0)
            g.append(f"<ellipse cx='0' cy='{-rr + pw * .9:.2f}' rx='{pw * .62:.2f}' ry='{pw * 1.05:.2f}' fill='{col}' stroke='{stroke}' "
                     f"stroke-width='1.2' transform='rotate({a:.2f})'/>")
        g.append(f"<circle r='{rr - pw * 1.9:.2f}' fill='{colors[(ring + 1) % len(colors)]}'/>")
    g.append(f"<circle r='{r * .08:.2f}' fill='{colors[0]}'/></g>")
    return "".join(g)


def _diya(x: float, y: float, w: float, gold: str, flame_id: str) -> str:
    """Clay lamp with a flame and glow (SVG units)."""
    h = w * .42
    return (f"<g transform='translate({x},{y})'>"
            f"<ellipse cx='0' cy='{-h * 1.9:.1f}' rx='{w * .55:.1f}' ry='{w * .7:.1f}' fill='url(#{flame_id}g)' opacity='.85'/>"
            f"<path d='M{-w / 2:.1f},{-h * .2:.1f} Q0,{h * 1.25:.1f} {w / 2:.1f},{-h * .2:.1f} Q{w * .2:.1f},{-h * .5:.1f} {w * .1:.1f},{-h * .55:.1f} L{-w * .1:.1f},{-h * .55:.1f} Q{-w * .2:.1f},{-h * .5:.1f} {-w / 2:.1f},{-h * .2:.1f}Z' fill='url(#{flame_id}c)'/>"
            f"<path d='M{-w * .42:.1f},{-h * .12:.1f} Q0,{h * .35:.1f} {w * .42:.1f},{-h * .12:.1f}' fill='none' stroke='{gold}' stroke-width='{w * .035:.1f}' opacity='.9'/>"
            f"<g data-tw='5,{(x % 7) / 7:.2f},.75'><path d='M0,{-h * .55:.1f} C{w * .14:.1f},{-h * 1.1:.1f} {w * .05:.1f},{-h * 1.6:.1f} 0,{-h * 2.1:.1f} C{-w * .05:.1f},{-h * 1.6:.1f} {-w * .14:.1f},{-h * 1.1:.1f} 0,{-h * .55:.1f}Z' fill='url(#{flame_id}f)'/>"
            f"<path d='M0,{-h * .6:.1f} C{w * .06:.1f},{-h * .95:.1f} {w * .02:.1f},{-h * 1.25:.1f} 0,{-h * 1.5:.1f} C{-w * .02:.1f},{-h * 1.25:.1f} {-w * .06:.1f},{-h * .95:.1f} 0,{-h * .6:.1f}Z' fill='#fff7d6'/></g></g>")


def _defs_diya(fid: str) -> str:
    return (f"<radialGradient id='{fid}g'><stop offset='0' stop-color='#ffcf6b' stop-opacity='.75'/><stop offset='1' stop-color='#ff8a00' stop-opacity='0'/></radialGradient>"
            f"<linearGradient id='{fid}c' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#d9732b'/><stop offset='1' stop-color='#7a2f12'/></linearGradient>"
            f"<linearGradient id='{fid}f' x1='0' y1='1' x2='0' y2='0'><stop offset='0' stop-color='#ff7a00'/><stop offset='.6' stop-color='#ffc23a'/><stop offset='1' stop-color='#fff2b0'/></linearGradient>")


def _svg(k: Kit, inner: str, *, defs: str = "", style: str = "") -> str:
    return (f"<svg class='layer' viewBox='0 0 {k.w} {k.h}' preserveAspectRatio='xMidYMid slice' style='pointer-events:none;{style}'>"
            f"<defs>{defs}</defs>{inner}</svg>")


# ----------------------------------------------------------------------------- per-occasion art
def art_diwali(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    y = H - (k.pads()[2] * .5)
    w = 140 * s
    xs = [.1, .24, .76, .9] if not k.wide else [.08, .18, .28, .72, .82, .92]
    lamps = "".join(_diya(W * x, y + (14 * s if i % 2 else 0), w * (.85 if i % 2 else 1), p["a1"], "dy") for i, x in enumerate(xs))
    rangoli = _mandala(0, 0, 300 * s,
                       ["#ffb547", "#ff4f8b", "#ff7a1a", "#ffe08a", "#c2185b"], rings=5, petals=12, op=.9)
    rangoli2 = _mandala(W * -.04, H * .5 if k.tall else H * .62, 190 * s, ["#ffe08a", "#ff7a1a", "#ff4f8b", "#ffb547"], rings=4, petals=10, op=.55)
    art = (_svg(k, f"<g transform='translate({W * (1.02 if not k.wide else .95):.0f},{H * (.02 if not k.wide else .05):.0f})'><g data-spin='.15'>{rangoli}</g></g>{rangoli2}", style="opacity:.95")
           + _bokeh(k, 22, ["#ffb547", "#ff7a1a", "#ffe08a"], top=0, bottom=70, op=.42)
           + _sparkles(k, 14, "#ffe7a3", top=5, bottom=75)
           + _svg(k, lamps, defs=_defs_diya("dy")))
    return art, ""


def art_holi(k: Kit, p: dict) -> tuple[str, str]:
    cols = ["#ff2e88", "#ffc400", "#00c2ff", "#7c3aed", "#22c55e", "#ff6a00"]
    W, H, s = k.w, k.h, k.s
    blobs = []
    spots = [(.05, .08), (.95, .1), (.92, .9), (.08, .92), (.55, -.02), (.5, 1.02), (1.0, .5), (0, .45)]
    for i, (x, y) in enumerate(spots):
        col = cols[i % len(cols)]
        r = (260 + 120 * k.rnd(i)) * s
        blobs.append(f"<circle cx='{W * x:.0f}' cy='{H * y:.0f}' r='{r:.0f}' fill='url(#h{i % 6})' data-drift='{20 + i * 3},{15},1' data-phase='{i * .13:.2f}'/>")
        for j in range(14):  # powder specks
            a = k.rnd(i * 31 + j, 0, 6.283)
            d = r * k.rnd(i * 17 + j, .7, 1.35)
            blobs.append(f"<circle cx='{W * x + math.cos(a) * d:.0f}' cy='{H * y + math.sin(a) * d:.0f}' r='{k.rnd(i * 13 + j, 4, 16) * s:.1f}' fill='{col}' opacity='{k.rnd(j, .35, .8):.2f}'/>")
    defs = "".join(f"<radialGradient id='h{i}'><stop offset='0' stop-color='{c}' stop-opacity='.95'/><stop offset='.55' stop-color='{c}' stop-opacity='.55'/>"
                   f"<stop offset='1' stop-color='{c}' stop-opacity='0'/></radialGradient>" for i, c in enumerate(cols))
    return _svg(k, "".join(blobs), defs=defs, style="filter:saturate(1.05)"), ""


def art_eid(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    gold = p["a1"]
    mx, my, mr = W * (.8 if not k.wide else .82), H * (.2 if k.tall else .24), 120 * s
    moon = (f"<mask id='mm'><rect x='0' y='0' width='{W}' height='{H}' fill='#fff'/><circle cx='{mx + mr * .42:.0f}' cy='{my - mr * .22:.0f}' r='{mr * .86:.0f}' fill='#000'/></mask>"
            f"<circle cx='{mx:.0f}' cy='{my:.0f}' r='{mr * 2.2:.0f}' fill='url(#mglow)'/>"
            f"<circle cx='{mx:.0f}' cy='{my:.0f}' r='{mr:.0f}' fill='url(#mgold)' mask='url(#mm)'/>")
    lanterns = []
    xs = [.12, .27] if not k.wide else [.08, .2, .32]
    for i, fx in enumerate(xs):
        x = W * fx
        top = 0
        ln = (H * (.16 + .09 * (i % 2))) if not k.tall else H * (.12 + .06 * (i % 2))
        lw = (58 + 14 * (i % 2)) * s
        lanterns.append(
            f"<g data-drift='0,{6 + 3 * i},1' data-phase='{i * .3:.2f}'><line x1='{x:.0f}' y1='{top}' x2='{x:.0f}' y2='{ln:.0f}' stroke='{gold}' stroke-width='{2 * s:.1f}' opacity='.8'/>"
            f"<g transform='translate({x:.0f},{ln:.0f})'>"
            f"<path d='M{-lw * .25:.1f},0 L{lw * .25:.1f},0 L{lw * .32:.1f},{lw * .2:.1f} L{-lw * .32:.1f},{lw * .2:.1f}Z' fill='{gold}'/>"
            f"<ellipse cx='0' cy='{lw * .95:.1f}' rx='{lw * .9:.1f}' ry='{lw * 1.1:.1f}' fill='url(#lglow)'/>"
            f"<path d='M{-lw * .32:.1f},{lw * .2:.1f} C{-lw * .62:.1f},{lw * .6:.1f} {-lw * .62:.1f},{lw * 1.3:.1f} {-lw * .3:.1f},{lw * 1.7:.1f} L{lw * .3:.1f},{lw * 1.7:.1f} "
            f"C{lw * .62:.1f},{lw * 1.3:.1f} {lw * .62:.1f},{lw * .6:.1f} {lw * .32:.1f},{lw * .2:.1f}Z' fill='rgba(255,214,120,.22)' stroke='{gold}' stroke-width='{2.2 * s:.1f}'/>"
            f"<path d='M0,{lw * .2:.1f} V{lw * 1.7:.1f} M{-lw * .45:.1f},{lw * .95:.1f} H{lw * .45:.1f}' stroke='{gold}' stroke-width='{1.4 * s:.1f}' opacity='.7'/>"
            f"<circle cx='0' cy='{lw * .95:.1f}' r='{lw * .16:.1f}' fill='#fff3c4' data-tw='4,{i * .2:.2f},.6'/>"
            f"<path d='M{-lw * .3:.1f},{lw * 1.7:.1f} L{lw * .3:.1f},{lw * 1.7:.1f} L{lw * .14:.1f},{lw * 1.9:.1f} L{-lw * .14:.1f},{lw * 1.9:.1f}Z' fill='{gold}'/>"
            f"<line x1='0' y1='{lw * 1.9:.1f}' x2='0' y2='{lw * 2.2:.1f}' stroke='{gold}' stroke-width='{2 * s:.1f}'/></g></g>")
    # arch pattern along the bottom
    arches = []
    aw = 120 * s
    for i in range(int(W / aw) + 2):
        x0 = i * aw - aw / 2
        arches.append(f"<path d='M{x0:.0f},{H:.0f} V{H - 60 * s:.0f} Q{x0:.0f},{H - 120 * s:.0f} {x0 + aw / 2:.0f},{H - 150 * s:.0f} Q{x0 + aw:.0f},{H - 120 * s:.0f} {x0 + aw:.0f},{H - 60 * s:.0f} V{H:.0f}' "
                      f"fill='none' stroke='{gold}' stroke-width='{1.5 * s:.1f}' opacity='.28'/>")
    defs = ("<radialGradient id='mglow'><stop offset='0' stop-color='#fff1c2' stop-opacity='.35'/><stop offset='1' stop-color='#fff1c2' stop-opacity='0'/></radialGradient>"
            "<linearGradient id='mgold' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#fff2c6'/><stop offset='1' stop-color='#e2a93b'/></linearGradient>"
            "<radialGradient id='lglow'><stop offset='0' stop-color='#ffd98a' stop-opacity='.55'/><stop offset='1' stop-color='#ffd98a' stop-opacity='0'/></radialGradient>")
    return (_sparkles(k, 26, "#fff4cf", top=0, bottom=60, size=16) + _svg(k, moon + "".join(lanterns) + "".join(arches), defs=defs)), ""


def art_christmas(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    flakes = []
    for i in range(34):
        x, y = k.rnd(i * 3, 0, W), k.rnd(i * 3 + 1, 0, H)
        if .14 * W < x < .86 * W and .26 * H < y < .74 * H:
            continue
        r = k.rnd(i * 3 + 2, 8, 26) * s
        arms = "".join(f"<g transform='rotate({a})'><line x1='0' y1='0' x2='0' y2='{-r:.1f}'/><line x1='0' y1='{-r * .55:.1f}' x2='{r * .25:.1f}' y2='{-r * .78:.1f}'/>"
                       f"<line x1='0' y1='{-r * .55:.1f}' x2='{-r * .25:.1f}' y2='{-r * .78:.1f}'/></g>" for a in range(0, 360, 60))
        flakes.append(f"<g data-drift='{k.rnd(i, -10, 10):.0f},{30 + i % 3 * 10},1' data-phase='{k.rnd(i + 9):.2f}'><g transform='translate({x:.0f},{y:.0f})' "
                      f"stroke='#ffffff' stroke-width='{max(1.2, r * .09):.1f}' stroke-linecap='round' opacity='{k.rnd(i + 2, .25, .7):.2f}'>{arms}</g></g>")
    orn = []
    for i, (fx, ln, col) in enumerate([(.72, .2, p["a2"]), (.84, .3, p["a1"]), (.93, .17, "#7fd1ff")]):
        x, y, r = W * fx, H * ln * (.7 if k.tall else 1), (46 + 10 * (i % 2)) * s
        orn.append(f"<g data-drift='0,8,1' data-phase='{i * .25:.2f}'><line x1='{x:.0f}' y1='0' x2='{x:.0f}' y2='{y - r:.0f}' stroke='{p['a1']}' stroke-width='{2 * s:.1f}' opacity='.7'/>"
                   f"<rect x='{x - r * .25:.0f}' y='{y - r * 1.15:.0f}' width='{r * .5:.0f}' height='{r * .3:.0f}' rx='{r * .06:.0f}' fill='{p['a1']}'/>"
                   f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{r:.0f}' fill='{col}'/>"
                   f"<circle cx='{x - r * .35:.0f}' cy='{y - r * .35:.0f}' r='{r * .3:.0f}' fill='#fff' opacity='.35'/>"
                   f"<path d='M{x - r:.0f},{y:.0f} Q{x:.0f},{y + r * .35:.0f} {x + r:.0f},{y:.0f}' stroke='#fff' stroke-opacity='.45' stroke-width='{3 * s:.1f}' fill='none'/></g>")
    return _bokeh(k, 14, ["#f5d07a", "#ffffff"], op=.25) + _svg(k, "".join(flakes) + "".join(orn)), ""


def art_new_year(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    bursts = []
    for i, (fx, fy, r, col) in enumerate([(.86, .12, 190, p["a1"]), (.12, .1, 140, p["a2"]), (.9, .9, 120, "#ff8fb1"), (.1, .88, 110, p["a1"])]):
        cx, cy, rr = W * fx, H * fy, r * s
        rays = "".join(f"<line x1='{cx + math.cos(a) * rr * .25:.0f}' y1='{cy + math.sin(a) * rr * .25:.0f}' x2='{cx + math.cos(a) * rr:.0f}' y2='{cy + math.sin(a) * rr:.0f}'/>"
                       f"<circle cx='{cx + math.cos(a) * rr * 1.08:.0f}' cy='{cy + math.sin(a) * rr * 1.08:.0f}' r='{3.5 * s:.1f}' fill='{col}' stroke='none'/>"
                       for a in [2 * math.pi * j / 24 for j in range(24)])
        bursts.append(f"<g data-anim='scale' data-delay='{.2 + i * .35:.2f}' data-dur='1.4' style='transform-origin:{cx:.0f}px {cy:.0f}px'>"
                      f"<g stroke='{col}' stroke-width='{2.4 * s:.1f}' stroke-linecap='round' opacity='.85' data-tw='2,{i * .2:.2f},.5'>{rays}</g></g>")
    conf = []
    cols = [p["a1"], p["a2"], "#ff8fb1", "#ffffff"]
    for i in range(46):
        x, y = k.rnd(i * 4, 0, W), k.rnd(i * 4 + 1, 0, H)
        if .12 * W < x < .88 * W and .24 * H < y < .76 * H:
            continue
        w, h = k.rnd(i * 4 + 2, 6, 14) * s, k.rnd(i * 4 + 3, 14, 28) * s
        conf.append(f"<g data-drift='{k.rnd(i, -12, 12):.0f},{24},1' data-phase='{k.rnd(i + 3):.2f}'><rect x='{x:.0f}' y='{y:.0f}' width='{w:.1f}' height='{h:.1f}' rx='{w * .3:.1f}' "
                    f"fill='{cols[i % 4]}' opacity='{k.rnd(i + 1, .35, .85):.2f}' transform='rotate({k.rnd(i + 5, 0, 180):.0f} {x:.0f} {y:.0f})'/></g>")
    return _svg(k, "".join(bursts) + "".join(conf)), ""


def art_tricolour(k: Kit, p: dict) -> tuple[str, str]:
    """Abstract flowing ribbons in saffron, white and India green - not a flag, no emblem."""
    W, H, s = k.w, k.h, k.s
    sa, gr = "#ff9933", "#138808"
    def wave(y0: float, amp: float, ph: float) -> str:
        pts = [f"M{-W * .05:.0f},{y0:.0f}"]
        for i in range(1, 5):
            x1 = W * (i - .5) / 4
            x2 = W * i / 4 + W * .02
            pts.append(f"Q{x1:.0f},{y0 + amp * math.sin(i * 1.7 + ph):.0f} {x2:.0f},{y0 + amp * .3 * math.cos(i + ph):.0f}")
        return " ".join(pts)
    band = 46 * s
    top_y = H * .085
    bot_y = H * .955
    def ribbons(y: float, flip: int) -> str:
        out = []
        for j, col in enumerate([sa, "#ffffff", gr]):
            d = wave(y + j * band * .9 * flip, 70 * s, .6 + j * .25)
            out.append(f"<path d='{d}' fill='none' stroke='{col}' stroke-width='{band:.0f}' stroke-linecap='round' opacity='{.92 if col != '#ffffff' else 1}' "
                       f"style='filter:drop-shadow(0 {6 * s:.0f}px {14 * s:.0f}px rgba(0,0,0,.08))'/>")
        return "".join(out)
    sweep = (f"<g data-anim='wipe' data-delay='.2' data-dur='1.6'>{ribbons(top_y - band * 2.4, 1)}</g>"
             f"<g data-anim='wipe' data-delay='.5' data-dur='1.6'>{ribbons(bot_y, 1)}</g>")
    glow = (f"<circle cx='{W * .5:.0f}' cy='{H * .5:.0f}' r='{min(W, H) * .55:.0f}' fill='url(#tglow)'/>")
    defs = ("<radialGradient id='tglow'><stop offset='0' stop-color='#ffffff' stop-opacity='.9'/><stop offset='1' stop-color='#ffffff' stop-opacity='0'/></radialGradient>")
    return _svg(k, glow + sweep, defs=defs) + _bokeh(k, 10, [sa, gr], op=.12, rmin=10, rmax=30), ""


def art_ganesh(k: Kit, p: dict) -> tuple[str, str]:
    """Marigold toran across the top, lotus mandala glow - no depiction of the deity."""
    W, H, s = k.w, k.h, k.s
    flowers = []
    n = int(W / (46 * s)) + 2
    for i in range(n):
        x = i * W / (n - 1)
        y = 30 * s + 40 * s * math.sin(math.pi * (i % ((n - 1) // 3 or 1)) / ((n - 1) // 3 or 1))
        col = ["#ffb000", "#ff7a00", "#ffd23f"][i % 3]
        flowers.append(f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{24 * s:.0f}' fill='{col}'/><circle cx='{x:.0f}' cy='{y:.0f}' r='{12 * s:.0f}' fill='{mix(col, '#a33d00', .35)}' opacity='.6'/>")
        if i % 3 == 1:
            for j in range(4):
                flowers.append(f"<circle cx='{x:.0f}' cy='{y + (34 + j * 30) * s:.0f}' r='{(13 - j * 1.5) * s:.0f}' fill='{['#ff7a00', '#ffb000'][j % 2]}'/>")
            flowers.append(f"<path d='M{x:.0f},{y + 150 * s:.0f} l{-9 * s:.0f},{-14 * s:.0f} l{9 * s:.0f},{-26 * s:.0f} l{9 * s:.0f},{26 * s:.0f}z' fill='#3fa34d'/>")
    lotus = []
    cx, cy, r = W / 2, H * (.5 if not k.wide else .5), min(W, H) * .44
    for i in range(16):
        a = 360 * i / 16
        lotus.append(f"<ellipse cx='0' cy='{-r * .55:.0f}' rx='{r * .14:.0f}' ry='{r * .42:.0f}' transform='rotate({a})' fill='none' stroke='#ffcf6b' stroke-width='{1.6 * s:.1f}' opacity='.2'/>")
    art = (_svg(k, f"<circle cx='{cx:.0f}' cy='{cy:.0f}' r='{r * 1.1:.0f}' fill='url(#gglow)'/><g transform='translate({cx:.0f},{cy:.0f})'><g data-spin='.08'>"
                  f"{''.join(lotus)}</g></g>",
                  defs="<radialGradient id='gglow'><stop offset='0' stop-color='#ff9a1f' stop-opacity='.35'/><stop offset='1' stop-color='#ff9a1f' stop-opacity='0'/></radialGradient>")
           + _bokeh(k, 14, ["#ffb000", "#ff7a00"], top=20, bottom=100, op=.3) + _svg(k, "".join(flowers)))
    return art, ""


def art_navratri(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    md = _mandala(0, 0, min(W, H) * .62, ["#ff4f8b", "#ffcc33", "#7b2ff7", "#ff8a00", "#22c1c3"], rings=6, petals=14, op=.09)
    sticks = []
    for i, (fx, fy, rot) in enumerate([(.86, .16, 35), (.9, .2, -35), (.1, .82, 35), (.14, .86, -35)]):
        x, y, L = W * fx, H * fy, 300 * s
        sticks.append(f"<g transform='translate({x:.0f},{y:.0f}) rotate({rot})'><rect x='{-9 * s:.0f}' y='{-L / 2:.0f}' width='{18 * s:.0f}' height='{L:.0f}' rx='{9 * s:.0f}' fill='url(#dstick)'/>"
                      + "".join(f"<rect x='{-10 * s:.0f}' y='{-L / 2 + L * (j + .5) / 7:.0f}' width='{20 * s:.0f}' height='{8 * s:.0f}' rx='{3 * s:.0f}' fill='{['#ffcc33', '#22c1c3', '#ff4f8b'][j % 3]}'/>" for j in range(7))
                      + f"<circle cx='0' cy='{-L / 2:.0f}' r='{12 * s:.0f}' fill='#ffcc33'/><circle cx='0' cy='{L / 2:.0f}' r='{12 * s:.0f}' fill='#ffcc33'/></g>")
    bunting = []
    n = 13
    for i in range(n):
        x0 = W * i / n
        col = ["#ff4f8b", "#ffcc33", "#22c1c3", "#ff8a00", "#7b2ff7"][i % 5]
        sag = 34 * s * math.sin(math.pi * (i + .5) / n)
        bunting.append(f"<path d='M{x0:.0f},{sag:.0f} L{x0 + W / n:.0f},{sag:.0f} L{x0 + W / n / 2:.0f},{sag + 70 * s:.0f}Z' fill='{col}' opacity='.95'/>"
                       f"<circle cx='{x0 + W / n / 2:.0f}' cy='{sag + 22 * s:.0f}' r='{6 * s:.0f}' fill='#fff' opacity='.85'/>")
    defs = "<linearGradient id='dstick' x1='0' x2='1'><stop offset='0' stop-color='#b5651d'/><stop offset='1' stop-color='#e6a15a'/></linearGradient>"
    return _svg(k, f"<g transform='translate({W * .5:.0f},{H * .5:.0f})'><g data-spin='.1'>{md}</g></g>" + "".join(sticks) + "".join(bunting), defs=defs) + _sparkles(k, 16, "#ffe28a", top=10, bottom=95), ""


def art_onam(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    pk = _mandala(0, 0, min(W, H) * (.3 if not k.wide else .36), ["#f59e0b", "#dc2626", "#facc15", "#ea580c", "#16a34a", "#fde68a"], rings=6, petals=16)
    cx, cy = (W * .98, H * .98) if not k.wide else (W * .93, H * .9)
    pk2 = _mandala(0, 0, min(W, H) * .15, ["#facc15", "#ea580c", "#dc2626", "#16a34a"], rings=4, petals=12)
    leaves = "".join(f"<path transform='translate({W * fx:.0f},{H * fy:.0f}) rotate({rot})' d='M0,0 C{40 * s:.0f},{-60 * s:.0f} {120 * s:.0f},{-60 * s:.0f} {170 * s:.0f},0 C{120 * s:.0f},{60 * s:.0f} {40 * s:.0f},{60 * s:.0f} 0,0Z' fill='#16a34a' opacity='.85'/>"
                     for fx, fy, rot in [(-.02, .1, 20), (.0, .18, -10), (.94, .02, 150), (1.0, .1, 175)])
    return _svg(k, leaves + f"<g transform='translate({cx:.0f},{cy:.0f})'><g data-spin='.12'>{pk}</g></g>"
                + f"<g transform='translate({W * .02:.0f},{H * .96:.0f})' opacity='.9'><g data-spin='-.12'>{pk2}</g></g>"), ""


def art_pongal(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    sx, sy, sr = W * .5, H * 1.12, min(W, H) * .3
    rays = "".join(f"<path d='M0,0 L{math.cos(a - .045) * sr * 3:.0f},{math.sin(a - .045) * sr * 3:.0f} L{math.cos(a + .045) * sr * 3:.0f},{math.sin(a + .045) * sr * 3:.0f}Z' fill='#fbbf24' opacity='.13'/>"
                   for a in [math.pi + math.pi * (i + .5) / 14 for i in range(14)])
    sun = f"<g transform='translate({sx:.0f},{sy:.0f})'><g data-spin='.05'>{rays}</g></g><circle cx='{sx:.0f}' cy='{sy:.0f}' r='{sr:.0f}' fill='url(#sun)'/>"
    kites = []
    for i, (fx, fy, sc, col1, col2) in enumerate([(.86, .13, .9, "#f97316", "#ec4899"), (.12, .16, .7, "#16a34a", "#facc15"), (.9, .62, .5, "#0ea5e9", "#f43f5e")]):
        x, y, z = W * fx, H * fy, 90 * s * sc
        kites.append(f"<g data-drift='{14},{18},1' data-phase='{i * .3:.2f}'><g transform='translate({x:.0f},{y:.0f}) rotate({-12 + i * 10})'>"
                     f"<path d='M0,{-z:.0f} L{z * .72:.0f},0 L0,{z * 1.15:.0f} L{-z * .72:.0f},0Z' fill='{col1}'/><path d='M0,{-z:.0f} L{z * .72:.0f},0 L0,0Z' fill='{col2}'/>"
                     f"<path d='M0,{-z:.0f} V{z * 1.15:.0f} M{-z * .72:.0f},0 H{z * .72:.0f}' stroke='#7c2d12' stroke-width='{2 * s:.1f}' opacity='.5'/>"
                     f"<path d='M0,{z * 1.15:.0f} q{z * .3:.0f},{z * .5:.0f} 0,{z:.0f} q{-z * .3:.0f},{z * .5:.0f} 0,{z:.0f}' stroke='#7c2d12' stroke-width='{1.6 * s:.1f}' fill='none' opacity='.6'/>"
                     f"<path d='M{-6 * s:.0f},{z * 1.8:.0f} l{12 * s:.0f},{8 * s:.0f} l{-12 * s:.0f},{8 * s:.0f}z' fill='{col2}'/></g></g>")
    cane = "".join(f"<g transform='translate({W * fx:.0f},{H:.0f}) rotate({rot})'><rect x='{-9 * s:.0f}' y='{-H * .36:.0f}' width='{18 * s:.0f}' height='{H * .4:.0f}' rx='{9 * s:.0f}' fill='#65a30d'/>"
                   + "".join(f"<rect x='{-10 * s:.0f}' y='{-H * .36 + j * 60 * s:.0f}' width='{20 * s:.0f}' height='{5 * s:.0f}' fill='#3f6212'/>" for j in range(int(H * .36 / (60 * s))))
                   + f"<path d='M0,{-H * .36:.0f} c{30 * s:.0f},{-70 * s:.0f} {90 * s:.0f},{-90 * s:.0f} {130 * s:.0f},{-80 * s:.0f} c{-50 * s:.0f},{10 * s:.0f} {-100 * s:.0f},{40 * s:.0f} {-130 * s:.0f},{80 * s:.0f}z' fill='#4d7c0f'/></g>"
                   for fx, rot in [(.04, 6), (.96, -6)])
    defs = "<radialGradient id='sun'><stop offset='0' stop-color='#fde68a'/><stop offset='.7' stop-color='#fbbf24'/><stop offset='1' stop-color='#f97316'/></radialGradient>"
    return _svg(k, sun + cane + "".join(kites), defs=defs), ""


def art_womens_day(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    blooms = []
    for i, (fx, fy, r) in enumerate([(.9, .12, 120), (.08, .88, 140), (.95, .78, 80), (.12, .1, 70)]):
        cx, cy, rr = W * fx, H * fy, r * s
        pet = "".join(f"<ellipse cx='0' cy='{-rr * .55:.0f}' rx='{rr * .32:.0f}' ry='{rr * .58:.0f}' transform='rotate({a})' fill='url(#wb{i % 2})' opacity='.85'/>" for a in range(0, 360, 45))
        blooms.append(f"<g transform='translate({cx:.0f},{cy:.0f})'><g data-spin='{.06 if i % 2 else -.06}'>{pet}<circle r='{rr * .2:.0f}' fill='#fde68a'/></g></g>")
    waves = "".join(f"<path d='M{-W * .1:.0f},{H * y:.0f} C{W * .3:.0f},{H * (y - .1):.0f} {W * .6:.0f},{H * (y + .12):.0f} {W * 1.1:.0f},{H * (y - .04):.0f}' fill='none' stroke='url(#wl)' stroke-width='{w * s:.0f}' stroke-linecap='round' opacity='{o}'/>"
                    for y, w, o in [(.3, 3, .35), (.34, 1.5, .25), (.72, 2, .3)])
    defs = ("<linearGradient id='wb0' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#f0abfc'/><stop offset='1' stop-color='#a855f7'/></linearGradient>"
            "<linearGradient id='wb1' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#fbcfe8'/><stop offset='1' stop-color='#ec4899'/></linearGradient>"
            "<linearGradient id='wl' x1='0' x2='1'><stop offset='0' stop-color='#f0abfc'/><stop offset='1' stop-color='#a78bfa'/></linearGradient>")
    return _bokeh(k, 12, ["#f0abfc", "#a78bfa"], op=.28) + _svg(k, waves + "".join(blooms), defs=defs), ""


def art_teachers_day(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    col = p["a1"]
    items = [("bulb", .86, .14, 120, 12), ("doc", .1, .16, 90, -10), ("spark", .9, .82, 70, 0), ("star", .08, .84, 80, 8), ("target", .76, .9, 60, 0)]
    out = []
    for i, (ic, fx, fy, sz, rot) in enumerate(items):
        out.append(f"<div data-drift='0,10,1' data-phase='{i * .2:.2f}' style='position:absolute;left:{fx * 100:.1f}%;top:{fy * 100:.1f}%;transform:translate(-50%,-50%) rotate({rot}deg);"
                   f"color:{[col, p['a2']][i % 2]};opacity:.55'>{icon(ic, k.px(sz), sw=1.3)}</div>")
    # ruled-notebook lines + margin
    lines = (f"<div class='layer' style='background-image:linear-gradient(rgba(15,141,222,.07) 1px,transparent 1px);background-size:100% {k.px(56)}'></div>"
             f"<div style='position:absolute;top:0;bottom:0;left:{k.px(60)};width:{k.px(2)};background:rgba(244,63,94,.2)'></div>")
    return lines + "".join(out), ""


def art_tech_day(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    a = p["a1"]
    def gear(cx: float, cy: float, r: float, teeth: int) -> str:
        pts = []
        for i in range(teeth * 2):
            ang = math.pi * i / teeth
            rr = r if i % 2 == 0 else r * .82
            for d in (-.18, .18):
                pts.append(f"{cx + math.cos(ang + d * math.pi / teeth) * rr:.1f},{cy + math.sin(ang + d * math.pi / teeth) * rr:.1f}")
        return (f"<polygon points='{' '.join(pts)}' fill='none' stroke='{a}' stroke-width='{2 * s:.1f}'/>"
                f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{r * .35:.1f}' fill='none' stroke='{a}' stroke-width='{2 * s:.1f}'/>")
    g1 = (W * .92, H * .06, 160 * s)
    g2 = (W * .92 - 250 * s, H * .06 + 110 * s, 100 * s)
    gears = (f"<g opacity='.55' data-spin='.25' style='transform-origin:{g1[0]:.0f}px {g1[1]:.0f}px'>{gear(*g1, 14)}</g>"
             f"<g opacity='.4' data-spin='-.35' style='transform-origin:{g2[0]:.0f}px {g2[1]:.0f}px'>{gear(*g2, 10)}</g>")
    circuit = []
    for i in range(7):
        y = H * (.62 + i * .06)
        x0 = k.rnd(i, -40, 80) * s
        x1 = W * k.rnd(i + 3, .3, .55)
        circuit.append(f"<path d='M{x0:.0f},{y:.0f} H{x1:.0f} l{40 * s:.0f},{-40 * s:.0f} H{x1 + W * .2:.0f}' fill='none' stroke='{a}' stroke-width='{2 * s:.1f}' opacity='.35'/>"
                       f"<circle cx='{x1 + W * .2:.0f}' cy='{y - 40 * s:.0f}' r='{6 * s:.1f}' fill='{a}' data-tw='3,{i * .14:.2f},.3'/>")
    blueprint = (f"<div class='layer' style='background-image:linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px),"
                 f"linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);"
                 f"background-size:{k.px(120)} {k.px(120)},{k.px(120)} {k.px(120)},{k.px(24)} {k.px(24)},{k.px(24)} {k.px(24)}'></div>")
    return blueprint + _svg(k, gears + "".join(circuit)), ""


def art_festive(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    cols = [p["a1"], p["a2"], "#ffffff", "#ffd27a"]
    conf = []
    for i in range(40):
        x, y = k.rnd(i * 4, 0, W), k.rnd(i * 4 + 1, 0, H * .45) if i % 2 else k.rnd(i * 4 + 1, H * .7, H)
        sz = k.rnd(i * 4 + 2, 6, 14) * s
        shape = (f"<circle cx='{x:.0f}' cy='{y:.0f}' r='{sz / 1.6:.1f}' fill='{cols[i % 4]}'/>" if i % 3 == 0 else
                 f"<rect x='{x:.0f}' y='{y:.0f}' width='{sz:.1f}' height='{sz * 2:.1f}' rx='{sz * .3:.1f}' fill='{cols[i % 4]}' transform='rotate({k.rnd(i, 0, 180):.0f} {x:.0f} {y:.0f})'/>")
        conf.append(f"<g data-drift='{k.rnd(i, -10, 10):.0f},20,1' data-phase='{k.rnd(i + 7):.2f}' opacity='{k.rnd(i + 1, .4, .9):.2f}'>{shape}</g>")
    # string lights across the top
    n = 11
    bulbs = []
    for i in range(n + 1):
        x = W * i / n
        y = 40 * s + 50 * s * math.sin(math.pi * i / n)
        bulbs.append(f"<circle cx='{x:.0f}' cy='{y + 16 * s:.0f}' r='{30 * s:.0f}' fill='url(#bl)' data-tw='3,{i * .09:.2f},.4'/><circle cx='{x:.0f}' cy='{y + 16 * s:.0f}' r='{9 * s:.0f}' fill='#fff2c4'/>")
    wire = f"<path d='M0,{40 * s:.0f} Q{W / 2:.0f},{140 * s:.0f} {W:.0f},{40 * s:.0f}' stroke='rgba(255,255,255,.3)' stroke-width='{2 * s:.1f}' fill='none'/>"
    defs = "<radialGradient id='bl'><stop offset='0' stop-color='#ffe08a' stop-opacity='.9'/><stop offset='1' stop-color='#ffe08a' stop-opacity='0'/></radialGradient>"
    return _bokeh(k, 16, [p["a1"], p["a2"]], op=.3) + _svg(k, wire + "".join(bulbs) + "".join(conf), defs=defs), ""


def art_awareness(k: Kit, p: dict) -> tuple[str, str]:
    W, H, s = k.w, k.h, k.s
    a = p["a1"]
    cx, cy, r = W * (.88 if not k.wide else .9), H * (.13 if not k.tall else .1), 115 * s
    ribbon = (f"<g data-drift='0,8,1'><g transform='translate({cx:.0f},{cy:.0f})'>"
              f"<path d='M0,{r * .2:.0f} C{-r * .9:.0f},{-r * .6:.0f} {-r * .15:.0f},{-r * 1.1:.0f} 0,{-r * .45:.0f} C{r * .15:.0f},{-r * 1.1:.0f} {r * .9:.0f},{-r * .6:.0f} 0,{r * .2:.0f}"
              f" L{r * .55:.0f},{r * 1.05:.0f} L{r * .32:.0f},{r * 1.15:.0f} L0,{r * .55:.0f} L{-r * .32:.0f},{r * 1.15:.0f} L{-r * .55:.0f},{r * 1.05:.0f}Z' "
              f"fill='none' stroke='url(#aw)' stroke-width='{r * .2:.0f}' stroke-linejoin='round'/></g></g>")
    rings = "".join(f"<circle cx='{cx:.0f}' cy='{cy:.0f}' r='{r * (1.6 + i * .5):.0f}' fill='none' stroke='{a}' stroke-width='{1.5 * s:.1f}' opacity='{.18 - i * .04:.2f}'/>" for i in range(3))
    defs = f"<linearGradient id='aw' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{a}'/><stop offset='1' stop-color='{p['a2']}'/></linearGradient>"
    return _svg(k, rings + ribbon, defs=defs) + _bokeh(k, 8, [a, p["a2"]], op=.15), ""


ART = {"diwali": art_diwali, "holi": art_holi, "eid": art_eid, "christmas": art_christmas, "new_year": art_new_year,
       "independence_day": art_tricolour, "republic_day": art_tricolour, "ganesh_chaturthi": art_ganesh, "navratri": art_navratri,
       "onam": art_onam, "pongal": art_pongal, "womens_day": art_womens_day, "teachers_day": art_teachers_day, "tech_day": art_tech_day,
       "festive": art_festive, "awareness": art_awareness}


# ----------------------------------------------------------------------------- the greeting layout
def greeting(k: Kit, f: dict, photo: str | None, key: str) -> str:
    o = OCCASIONS[key]
    p = _pal(k, key)
    k.drift = True  # flames flicker and sparkles twinkle even in GIF loops (all motions are periodic)
    k.theme = p["theme"]
    name = str(f.get("occasion") or (f.get("occasion_name")) or o["label"].split(" / ")[0].split(" (")[0])
    head = str(f.get("headline") or o["default"] or name)
    body = str(f.get("body") or o["msg"])
    eyebrow = str(f.get("eyebrow") or f.get("date") or "")
    dark = p["theme"] == "dark"
    fg = "#ffffff" if dark else "#14141f"
    soft = "rgba(255,255,255,.82)" if dark else "rgba(20,20,31,.72)"
    a1, a2 = p["a1"], p["a2"]
    if key in ("independence_day", "republic_day"):
        hl = "linear-gradient(100deg,#e57a12 0%,#c2410c 30%,#1f7a2e 75%,#138808 100%)"
    elif dark:
        hl = f"linear-gradient(100deg,{mix(a1, '#ffffff', .25)} 0%,{a1} 50%,{a2} 100%)"
    else:
        hl = f"linear-gradient(100deg,{a1} 0%,{a2} 100%)"
    vars_ = (f"--fg:{fg};--fg-soft:{soft};--fg-mute:{soft};--accent:{a1};--accent2:{a2};--accent-text:{a1 if dark else mix(a1, '#000000', .25)};"
             f"--chip-bg:{'rgba(255,255,255,.10)' if dark else 'rgba(255,255,255,.7)'};--chip-line:{'rgba(255,255,255,.2)' if dark else 'rgba(0,0,0,.08)'}")
    bg = (f"<!--bgl:drift--><div class='bgl layer'><div class='layer' style='background:radial-gradient(120% 90% at 50% 35%,{p['bg1']} 0%,{p['bg0']} 100%)'></div>"
          + ("<div class='vignette'></div>" if dark else "") + "<div class='grain'></div></div><!--/bgl-->")
    art, _ = ART[key](k, p)
    size = fit_size(head, 112 * k.boost if not k.wide else 96, soft=18, floor=56)
    is_motion = k.motion != "still"
    head_html = words(head) if is_motion else rich(head)
    year = str(f.get("year") or "")
    css = (f".main{{justify-content:safe center;align-items:center;text-align:center}}.main .eyebrow{{align-self:center}}"
           f".gh{{font-family:var(--font-head);font-weight:800;letter-spacing:-.035em;line-height:1;color:var(--fg);text-wrap:balance}}"
           f".gh .hl{{background:{hl};-webkit-background-clip:text;background-clip:text;color:transparent}}"
           f".gmsg{{font-weight:500;line-height:1.45;color:var(--fg-soft);max-width:30ch;margin:0 auto;text-wrap:balance}}"
           f".from{{display:flex;align-items:center;gap:{k.px(16)};justify-content:center}}"
           f".from .bname{{color:var(--fg)}}.from .bsub{{color:var(--fg-soft)}}"
           f".yr{{font-family:var(--font-head);font-weight:800;font-size:{k.px(220)};line-height:.85;letter-spacing:-.05em;color:transparent;"
           f"-webkit-text-stroke:{k.px(2)} {a1};opacity:.55;margin-bottom:{k.px(26)}}}"
           f".textpanel{{padding:{k.px(10)}}}" + (f".gh,.gmsg{{text-shadow:0 {k.px(2)} {k.px(24)} rgba(0,0,0,.35)}}" if dark else
                                                    f".textpanel{{background:radial-gradient(closest-side,rgba(255,255,255,.85),rgba(255,255,255,0));padding:{k.px(40)}}}"))
    # Holi's bright splashes need a calm centre for the text
    if key == "holi":
        css += f".textpanel{{background:rgba(255,255,255,.78);-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);border-radius:{k.px(36)};padding:{k.px(56)} {k.px(48)};box-shadow:0 {k.px(20)} {k.px(60)} rgba(0,0,0,.08)}}"
    hl_head = head if "*" in head else _auto_highlight(head, name)
    inner = ("<div class='textpanel'>"
             + (f"<span class='eyebrow' data-anim='rise' data-order='1'><span class='dot'></span>{esc(eyebrow)}</span><div class='sp4'></div>" if eyebrow else "")
             + (f"<div class='yr num' data-anim='blur' data-order='1'>{esc(year)}</div>" if year else "")
             + f"<h1 class='gh' data-fit data-min='{40 * k.s:.0f}' data-anim='words' data-order='2' style='font-size:{k.px(size)}'>"
             + (words(hl_head) if is_motion else rich(hl_head)) + "</h1>"
             + f"<div class='sp4'></div><p class='gmsg' data-fit data-anim='rise' data-order='5' style='font-size:{k.px(32 * (1 + (k.boost - 1) * .6))}'>{rich(body)}</p></div>")
    _ = head_html
    foot = (f"<div class='foot' data-anim='fade' data-order='8' style='justify-content:center'><div class='from'>{k.logo(64)}"
            f"<div style='text-align:left'><div class='bsub'>Warm wishes from</div><div class='bname'>{esc(k.brand.name)}</div></div></div></div>")
    return k.document(
        f"<div class='canvas' style='{vars_}'>{bg}<div class='layer' data-anim='fade' data-delay='0' data-dur='1.2'>{art}</div>"
        f"<div class='safe' data-exitgroup><div class='head' style='min-height:0'></div><div class='main' data-fitbox>{inner}</div>{foot}</div></div>",
        css, plain(head))


def _auto_highlight(head: str, name: str) -> str:
    """Highlight the occasion's name inside the greeting ("Happy *Diwali*")."""
    for cand in [name] + name.split():
        if len(cand) > 2 and re.search(re.escape(cand), head, re.I):
            return re.sub(re.escape(cand), lambda m: f"*{m.group(0)}*", head, count=1, flags=re.I)
    parts = head.split()
    return " ".join(parts[:-1] + [f"*{parts[-1]}*"]) if len(parts) > 1 else head


def _fn(key: str):
    def fn(k: Kit, f: dict, photo: str | None) -> str:
        return greeting(k, f, photo, key)
    fn.__name__ = f"greeting_{key}"
    return fn


SAMPLE_DATES = {"diwali": "1 November 2026", "holi": "4 March 2026", "eid": "20 March 2026", "christmas": "25 December",
                "new_year": "1 January 2027", "independence_day": "15 August", "republic_day": "26 January",
                "ganesh_chaturthi": "14 September 2026", "navratri": "11 October 2026", "onam": "26 August 2026",
                "pongal": "14 January", "womens_day": "8 March", "teachers_day": "5 September", "tech_day": "15 September"}


def specs(T) -> list:
    fields = (("headline", "Greeting", "textarea"), ("body", "Message", "textarea"), ("eyebrow", "Small label (date)", "text"))
    out = []
    for key, o in OCCASIONS.items():
        sample = {"eyebrow": SAMPLE_DATES.get(key, "")}
        if key == "awareness":
            sample = {"headline": "World *Mental Health* Day", "eyebrow": "10 October",
                      "body": "Check in on your team today. A five-minute conversation can change someone's week."}
        if key == "new_year":
            sample["year"] = "2027"
        out.append(T(f"greeting_{key}", o["label"], f"Greeting for {o['label']} with original artwork and your logo.",
                     ("image", "gif", "animation"), fields, _fn(key), theme=o["theme"] if not o.get("brand") else "dark",
                     photo=False, group="Occasions", themes=(o["theme"],), sample=sample))
    return out
