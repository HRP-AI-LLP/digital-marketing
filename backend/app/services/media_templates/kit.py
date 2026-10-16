"""Design system for the branded templates (agency-grade look, rendered by headless Chromium).

- Type: display face Plus Jakarta Sans (OFL, bundled) for headlines, Inter for text; modular scale (1.25) scaled
  to the canvas (`--s` = shorter side / 1080); tight leading for display sizes; `text-wrap: balance` on headings,
  `pretty` on paragraphs; measure capped in `ch`; every text block auto-fits its box (runtime.FIT_JS).
- Space: 8-pt grid (`--g` = 8px at 1080), safe areas per format (feed, 4:5, Story/Reel, 16:9, Pinterest 2:3).
- Depth: layered aurora/mesh gradients, dot grids, glass cards, soft multi-layer shadows, film grain.
- Brand: logo, colours and fonts from the BrandProfile (hrpai.in palette by default) with contrast enforced.
- Motion: elements carry data-anim attributes that the shared runtime turns into a deterministic timeline.
"""

from __future__ import annotations

import base64
import math
import re
from functools import lru_cache

from app.services.media_templates.base import (
    ASSETS,
    BrandStyle,
    _font_face,
    contrast,
    ensure_contrast,
    esc,
    mix,
    plain,
    rgb_str,
    seed,
)
from app.services.media_templates.runtime import BOOT_JS, FIT_JS, RUNTIME_JS

DISPLAY_FONT = "Plus Jakarta Sans"


@lru_cache(maxsize=1)
def font_faces() -> str:
    css = _font_face()
    p = ASSETS / "fonts" / "plus-jakarta-sans-latin-wght-normal.woff2"
    if p.exists():
        b64 = base64.b64encode(p.read_bytes()).decode()
        css += ("@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-display:block;font-weight:200 800;"
                f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}")
    return css


@lru_cache(maxsize=1)
def grain_uri() -> str:
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='240' height='240'><filter id='n'>"
           "<feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/>"
           "<feColorMatrix values='0 0 0 0 .5  0 0 0 0 .5  0 0 0 0 .5  0 0 0 .55 0'/></filter>"
           "<rect width='100%' height='100%' filter='url(#n)'/></svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


# ----------------------------------------------------------------------------- icons (original, 24x24 strokes)
_ICONS = {
    "arrow": "<path d='M5 12h14M13 6l6 6-6 6'/>",
    "check": "<path d='M5 12.5l4.2 4.2L19 7'/>",
    "x": "<path d='M6 6l12 12M18 6L6 18'/>",
    "spark": "<path d='M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5'/>",
    "bolt": "<path d='M13 2L4 14h7l-1 8 9-12h-7l1-8z'/>",
    "calendar": "<rect x='3.5' y='5' width='17' height='15.5' rx='2.5'/><path d='M3.5 10h17M8 3v4M16 3v4'/>",
    "clock": "<circle cx='12' cy='12' r='8.5'/><path d='M12 7.5V12l3 2'/>",
    "pin": "<path d='M12 21s-6.5-6-6.5-11a6.5 6.5 0 0113 0c0 5-6.5 11-6.5 11z'/><circle cx='12' cy='10' r='2.3'/>",
    "users": "<circle cx='9' cy='8.5' r='3.3'/><path d='M3 19.5c.6-3.3 3-5.2 6-5.2s5.4 1.9 6 5.2'/><path d='M15.5 5.6a3.2 3.2 0 010 6M17.5 14.6c1.9.6 3.1 2.2 3.5 4.9'/>",
    "briefcase": "<rect x='3' y='7.5' width='18' height='12.5' rx='2.5'/><path d='M9 7.5V5.8A1.8 1.8 0 0110.8 4h2.4A1.8 1.8 0 0115 5.8v1.7M3 13h18'/>",
    "chart": "<path d='M4 20V4M4 20h16'/><path d='M8 16v-4M12 16V8M16 16v-6'/>",
    "trend": "<path d='M3.5 17l5.5-5.5 4 4L20.5 8'/><path d='M15 8h5.5v5.5'/>",
    "shield": "<path d='M12 3l7.5 3v5.5c0 4.6-3.2 8-7.5 9.5-4.3-1.5-7.5-4.9-7.5-9.5V6L12 3z'/><path d='M8.8 12.2l2.2 2.2 4.3-4.3'/>",
    "chip": "<rect x='6.5' y='6.5' width='11' height='11' rx='2'/><path d='M9.5 2.5v4M14.5 2.5v4M9.5 17.5v4M14.5 17.5v4M2.5 9.5h4M2.5 14.5h4M17.5 9.5h4M17.5 14.5h4'/>",
    "quote": "<path d='M9.5 7C6.5 7.6 5 9.8 5 13v4h5v-5H7.4c0-1.8.9-2.8 2.1-3.2zM18.5 7c-3 .6-4.5 2.8-4.5 6v4h5v-5h-2.6c0-1.8.9-2.8 2.1-3.2z'/>",
    "star": "<path d='M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z'/>",
    "play": "<path d='M8 5.5v13l10.5-6.5z'/>",
    "rocket": "<path d='M14.5 4.5c2.6-1 4.6-1 5-.6.4.4.4 2.4-.6 5-1.1 2.8-3.7 5.6-7.4 7.6l-4-4c2-3.7 4.8-6.3 7-8z'/><circle cx='15' cy='9' r='1.6'/><path d='M7.5 12.5L4 12l2.5-3.5h3.5M11.5 16.5l.5 3.5 3.5-2.5V14M6 18c-1.2.2-2 .8-2.2 2.2 1.4-.2 2-1 2.2-2.2z'/>",
    "target": "<circle cx='12' cy='12' r='8.5'/><circle cx='12' cy='12' r='4.8'/><circle cx='12' cy='12' r='1.2'/>",
    "globe": "<circle cx='12' cy='12' r='8.5'/><path d='M3.5 12h17M12 3.5c2.4 2.4 3.4 5.4 3.4 8.5s-1 6.1-3.4 8.5c-2.4-2.4-3.4-5.4-3.4-8.5s1-6.1 3.4-8.5z'/>",
    "mic": "<rect x='9' y='3' width='6' height='11' rx='3'/><path d='M5.5 11a6.5 6.5 0 0013 0M12 17.5V21'/>",
    "heart": "<path d='M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0112 7.4 4.3 4.3 0 0119.5 10c0 5.4-7.5 10-7.5 10z'/>",
    "bulb": "<path d='M9 17.5h6M10 21h4M12 3a6 6 0 00-3.6 10.8c.7.6 1.1 1.4 1.1 2.2v1.5h5V16c0-.8.4-1.6 1.1-2.2A6 6 0 0012 3z'/>",
    "layers": "<path d='M12 3.5l8.5 4.5L12 12.5 3.5 8 12 3.5z'/><path d='M3.5 12L12 16.5l8.5-4.5M3.5 16L12 20.5l8.5-4.5'/>",
    "doc": "<path d='M6.5 3h7.5l4.5 4.5V21h-12z'/><path d='M14 3v4.5h4.5M9 12h6M9 15.5h6'/>",
    "bot": "<rect x='4.5' y='8' width='15' height='11' rx='3'/><path d='M12 4v4M9 13h.01M15 13h.01M9.5 16.2h5'/><circle cx='12' cy='3.5' r='1'/>",
    "link": "<path d='M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1'/><path d='M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1'/>",
    "mail": "<rect x='3' y='5.5' width='18' height='13' rx='2.5'/><path d='M3.8 7l8.2 6 8.2-6'/>",
    "gift": "<rect x='4' y='9' width='16' height='11.5' rx='1.8'/><path d='M3 9h18M12 9v11.5M12 9c-1.6-3.2-5.6-4-5.6-1.6C6.4 9 12 9 12 9zm0 0c1.6-3.2 5.6-4 5.6-1.6C17.6 9 12 9 12 9z'/>",
}
ICON_WORDS = [
    (r"hir|recruit|talent|candidate|cv|resume|interview", "users"), (r"payroll|salary|pay|cost|save|saving|₹|\$|budget", "trend"),
    (r"secur|privacy|complian|gdpr|safe", "shield"), (r"\bai\b|model|llm|agent|automat|bot|chat", "bot"),
    (r"fast|speed|quick|instant|minutes|seconds", "bolt"), (r"data|report|analytic|insight|metric|dashboard", "chart"),
    (r"calendar|schedule|date|leave|attendance|shift", "calendar"), (r"time|hour|deadline", "clock"),
    (r"goal|target|focus|okr|performance", "target"), (r"global|world|countr|remote", "globe"),
    (r"idea|tip|learn|insight", "bulb"), (r"document|policy|contract|form|paper", "doc"),
    (r"launch|grow|scale|start", "rocket"), (r"integrat|connect|api|link", "link"), (r"people|team|employee|staff", "users"),
    (r"email|inbox|mail", "mail"), (r"care|wellbeing|well-being|culture|love", "heart"),
]


def icon(name: str, size: str = "1em", stroke: str = "currentColor", sw: float = 1.8, fill: str = "none", cls: str = "") -> str:
    body = _ICONS.get(name) or _ICONS["spark"]
    filled = name in ("star", "play", "bolt") and fill != "none"
    return (f"<svg class='ic {cls}' style='width:{size};height:{size}' viewBox='0 0 24 24' fill='{fill if filled else 'none'}' "
            f"stroke='{stroke}' stroke-width='{sw}' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{body}</svg>")


def icon_for(text: str, i: int = 0) -> str:
    t = plain(text).lower()
    for pat, name in ICON_WORDS:
        if re.search(pat, t):
            return name
    return ("spark", "bolt", "target", "chart", "layers", "bulb", "shield", "rocket")[i % 8]


# ----------------------------------------------------------------------------- text
_HL = re.compile(r"\*([^*\n]{1,80})\*")


def words(text: object, cls: str = "") -> str:
    """Kinetic typography markup: every word in its own mask (`.w`) with an animatable inner span (`.wi`);
    *highlighted* words get the accent gradient. Line breaks are kept; punctuation right after a highlight stays
    attached ("*hand*?" -> "hand?")."""
    out = []
    for li, line in enumerate(str(text or "").replace("\r", "").split("\n")):
        if li:
            out.append("<br>")
        toks: list[tuple[str, bool, bool]] = []  # (word, highlighted, glued to the previous word)
        pos = 0
        for m in list(_HL.finditer(line)) + [None]:
            seg = line[pos:m.start()] if m else line[pos:]
            for j, w in enumerate(seg.split()):
                toks.append((w, False, j == 0 and bool(seg) and not seg[0].isspace() and pos > 0))
            if m:
                for j, w in enumerate(m.group(1).split()):
                    toks.append((w, True, j == 0 and m.start() > 0 and not line[m.start() - 1].isspace()))
                pos = m.end()
        merged: list[list] = []
        for w, hl, glue in toks:
            if glue and merged:
                merged[-1][0] += w  # punctuation stays on the previous word
            else:
                merged.append([w, hl])
        for i, (w, hl) in enumerate(merged):
            if i:
                out.append(" ")
            out.append(f"<span class='w'><span class='wi {'hl ' if hl else ''}{cls}'>{esc(w)}</span></span>")
    return "".join(out)


def rich(s: object) -> str:
    t = esc(s).replace("\r", "")
    t = _HL.sub(r'<em class="hl">\1</em>', t)
    return t.replace("\n", "<br>")


def fit_size(text: str, base: float, *, soft: int, floor: float) -> float:
    n = len(plain(text))
    return base if n <= soft else max(floor, base * (soft / n) ** 0.55)


# ----------------------------------------------------------------------------- formats
def fmt_name(w: int, h: int) -> str:
    r = h / w
    if r >= 1.6:
        return "story"  # 9:16
    if r >= 1.4:
        return "pin"  # 2:3
    if r >= 1.15:
        return "portrait"  # 4:5
    if r <= 0.7:
        return "wide"  # 16:9 / 1.91:1
    if r < 0.9:
        return "landscape"
    return "square"


CSS = r"""
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:var(--w);height:var(--h);overflow:hidden}
body{font-family:var(--font-body);color:var(--fg);background:var(--bg0);-webkit-font-smoothing:antialiased;
  text-rendering:geometricPrecision;font-feature-settings:"cv11","ss01";font-optical-sizing:auto}
.canvas{position:relative;width:var(--w);height:var(--h);overflow:hidden;isolation:isolate}
.layer{position:absolute;inset:0}
.grain{position:absolute;inset:0;background-image:var(--grain);background-size:calc(var(--s)*240px);opacity:var(--grain-o);mix-blend-mode:overlay;pointer-events:none;z-index:50}
.vignette{position:absolute;inset:0;background:radial-gradient(120% 90% at 50% 40%,transparent 55%,rgba(var(--dark-rgb),var(--vig)) 100%);pointer-events:none}
.blob{position:absolute;border-radius:50%;will-change:transform}
.dots{position:absolute;inset:0;background-image:radial-gradient(rgba(var(--line-rgb),var(--dot-o)) calc(var(--s)*1.6px),transparent calc(var(--s)*1.8px));
  background-size:calc(var(--g)*4) calc(var(--g)*4);-webkit-mask-image:radial-gradient(80% 70% at 85% 10%,#000 0%,transparent 70%);mask-image:radial-gradient(80% 70% at 85% 10%,#000 0%,transparent 70%)}
.gridlines{position:absolute;inset:0;background-image:linear-gradient(rgba(var(--line-rgb),.06) 1px,transparent 1px),linear-gradient(90deg,rgba(var(--line-rgb),.06) 1px,transparent 1px);
  background-size:calc(var(--g)*12) calc(var(--g)*12);-webkit-mask-image:linear-gradient(180deg,#000,transparent 75%);mask-image:linear-gradient(180deg,#000,transparent 75%)}
.photo{position:absolute;inset:-3%;background-size:cover;background-position:center}
.safe{position:absolute;inset:0;padding:var(--pt) var(--pr) var(--pb) var(--pl);display:flex;flex-direction:column;z-index:5}
.head{display:flex;align-items:center;justify-content:space-between;gap:calc(var(--g)*2);min-height:calc(var(--g)*7);flex:none}
.main{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;overflow:hidden;padding:calc(var(--g)*1) 0}
.foot{display:flex;align-items:center;justify-content:space-between;gap:calc(var(--g)*2);flex:none;min-height:calc(var(--g)*6)}
.sp1{height:calc(var(--g)*1);flex:none}.sp2{height:calc(var(--g)*2);flex:none}.sp3{height:calc(var(--g)*3);flex:none}.sp4{height:calc(var(--g)*4);flex:none}.sp5{height:calc(var(--g)*5);flex:none}.sp6{height:calc(var(--g)*6);flex:none}
.display{font-family:var(--font-head);font-weight:800;letter-spacing:-.035em;line-height:.98;color:var(--fg);text-wrap:balance;overflow-wrap:break-word;hyphens:manual}
.h1{font-family:var(--font-head);font-weight:800;letter-spacing:-.03em;line-height:1.04;color:var(--fg);text-wrap:balance;overflow-wrap:break-word}
.h2{font-family:var(--font-head);font-weight:750;letter-spacing:-.022em;line-height:1.1;color:var(--fg);text-wrap:balance;overflow-wrap:break-word}
.h3{font-family:var(--font-head);font-weight:700;letter-spacing:-.015em;line-height:1.2;color:var(--fg);text-wrap:balance}
.lead{font-weight:450;line-height:1.42;color:var(--fg-soft);text-wrap:pretty;max-width:36ch}
.txt{font-weight:450;line-height:1.45;color:var(--fg-soft);text-wrap:pretty}
.muted{color:var(--fg-mute)}
.hl{font-style:normal;background:linear-gradient(100deg,var(--accent) 0%,var(--accent2) 100%);-webkit-background-clip:text;background-clip:text;color:transparent;
  -webkit-box-decoration-break:clone;box-decoration-break:clone;padding-right:.04em}
.w{display:inline-block;overflow:hidden;vertical-align:top;padding:0 .02em .14em;margin-bottom:-.14em}
.wi{display:inline-block}
.eyebrow{display:inline-flex;align-self:flex-start;align-items:center;gap:calc(var(--g)*1.25);font-family:var(--font-body);font-weight:650;
  font-size:calc(var(--s)*24px);letter-spacing:.02em;color:var(--accent-text);background:var(--chip-bg);border:1px solid var(--chip-line);
  padding:calc(var(--g)*1.1) calc(var(--g)*2.2);border-radius:999px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;
  -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
.eyebrow .dot{width:calc(var(--s)*10px);height:calc(var(--s)*10px);border-radius:50%;background:var(--accent);box-shadow:0 0 0 calc(var(--s)*5px) rgba(var(--accent-rgb),.18);flex:none}
.kicker{font-weight:700;font-size:calc(var(--s)*22px);letter-spacing:.14em;text-transform:uppercase;color:var(--accent-text)}
.lockup{display:flex;align-items:center;gap:calc(var(--g)*1.5);min-width:0}
.logo{flex:none;width:calc(var(--s)*64px);height:calc(var(--s)*64px);border-radius:calc(var(--s)*16px);background:#fff;display:grid;place-items:center;
  box-shadow:0 1px 0 rgba(255,255,255,.6) inset,0 calc(var(--s)*6px) calc(var(--s)*20px) rgba(2,12,27,.22);overflow:hidden}
.logo img{width:84%;height:84%;object-fit:contain}
.logo .mono{font-family:var(--font-head);font-weight:800;font-size:calc(var(--s)*26px);color:var(--primary);letter-spacing:-.04em}
.bname{font-family:var(--font-head);font-weight:750;font-size:calc(var(--s)*25px);line-height:1.1;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:-.01em}
.bsub{font-weight:500;font-size:calc(var(--s)*19px);color:var(--fg-mute);white-space:nowrap;margin-top:calc(var(--s)*2px)}
.site{font-weight:600;font-size:calc(var(--s)*21px);color:var(--fg-soft);white-space:nowrap;letter-spacing:.005em;display:inline-flex;align-items:center;gap:calc(var(--g)*1)}
.btn{display:inline-flex;align-items:center;gap:calc(var(--g)*1.25);align-self:flex-start;font-family:var(--font-head);font-weight:700;font-size:calc(var(--s)*27px);color:#fff;
  background:linear-gradient(100deg,var(--primary) 0%,var(--primary-light) 55%,var(--secondary) 100%);padding:calc(var(--g)*2) calc(var(--g)*3.5);border-radius:999px;
  box-shadow:0 1px 0 rgba(255,255,255,.35) inset,0 calc(var(--s)*10px) calc(var(--s)*30px) rgba(var(--primary-rgb),.42);white-space:nowrap;letter-spacing:-.005em}
.btn.ghost{background:var(--chip-bg);color:var(--fg);border:1px solid var(--chip-line);box-shadow:none}
.btn .ic{width:1.05em;height:1.05em}
.glass{position:relative;background:var(--glass);border:1px solid var(--glass-line);border-radius:calc(var(--s)*28px);
  -webkit-backdrop-filter:blur(calc(var(--s)*22px)) saturate(140%);backdrop-filter:blur(calc(var(--s)*22px)) saturate(140%);
  box-shadow:0 1px 0 rgba(255,255,255,var(--glass-hi)) inset,0 calc(var(--s)*24px) calc(var(--s)*60px) rgba(2,12,27,var(--shadow-o))}
.card{position:relative;background:var(--card);border:1px solid var(--card-line);border-radius:calc(var(--s)*26px);
  box-shadow:0 1px 2px rgba(2,12,27,.05),0 calc(var(--s)*14px) calc(var(--s)*40px) rgba(2,12,27,var(--shadow-o))}
.badge{display:grid;place-items:center;flex:none;border-radius:calc(var(--s)*18px);color:#fff;
  background:linear-gradient(140deg,var(--primary-light),var(--primary) 60%,var(--dark));box-shadow:0 1px 0 rgba(255,255,255,.35) inset,0 calc(var(--s)*8px) calc(var(--s)*22px) rgba(var(--primary-rgb),.35)}
.rule{height:calc(var(--s)*6px);width:calc(var(--g)*10);border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2));transform-origin:left center;flex:none}
.counter{font-weight:650;font-size:calc(var(--s)*20px);color:var(--fg-soft);font-variant-numeric:tabular-nums;border:1px solid var(--chip-line);background:var(--chip-bg);
  border-radius:999px;padding:calc(var(--g)*.9) calc(var(--g)*1.8);letter-spacing:.04em}
.num{font-variant-numeric:tabular-nums lining-nums}
.ic{flex:none;display:block}
[data-outro]{opacity:0}
"""


class Kit:
    """One canvas: tokens + building blocks. `theme`: dark | light | brand | photo."""

    def __init__(self, width: int, height: int, brand: BrandStyle, theme: str = "dark", *, transparent: bool = False,
                 key: str = "", motion: str = "still", duration: float = 6.0, drift: bool = True):
        self.w, self.h, self.brand, self.theme = width, height, brand, theme
        self.transparent = transparent
        self.key = key
        self.motion = motion
        self.duration = duration
        self.drift = drift
        self.fmt = fmt_name(width, height)
        # type scales with the short side, plus up to 15% more on taller/wider canvases (more room for text)
        self.s = min(min(width, height) / 1080 * 1.15, (width * height) ** 0.5 / 1080, max(min(width, height) / 1080, 1e-3) * 1.15)
        self.s = max(self.s, min(width, height) / 1080)
        self.seed = seed(key, width, height, theme)
        self.boost = {"story": 1.22, "pin": 1.08}.get(self.fmt, 1.0)  # display text on tall canvases

    # ---- tokens
    @property
    def tall(self) -> bool:
        return self.fmt in ("story", "pin")

    @property
    def wide(self) -> bool:
        return self.fmt in ("wide", "landscape")

    def pads(self) -> tuple[float, float, float, float]:
        s, f = self.s, self.fmt
        if f == "story":  # Stories/Reels UI: profile row on top, reply bar + actions at the bottom
            return self.h * 0.105, 88 * s, self.h * 0.15, 88 * s
        if f == "pin":
            return 104 * s, 84 * s, 110 * s, 84 * s
        if f == "portrait":
            return 96 * s, 88 * s, 96 * s, 88 * s
        if f in ("wide", "landscape"):
            return 80 * s, 104 * s, 80 * s, 104 * s
        return 84 * s, 84 * s, 84 * s, 84 * s

    def colors(self) -> dict[str, str]:
        c = self.brand.colors
        dark = c["dark"]
        th = self.theme
        if th == "light":
            bg = mix(c["light"], "#ffffff", 0.4)
            fg = ensure_contrast(mix(c["dark"], c["ink"], 0.35), bg, 9)
            soft = ensure_contrast(mix(c["ink"], "#64748b", 0.55), bg, 5.5)
            mute = ensure_contrast("#64748b", bg, 4.5)
            accent = ensure_contrast(c["primary_light"], bg, 3.0)
            accent2 = ensure_contrast(c["secondary"], bg, 3.0)
            accent_text = ensure_contrast(c["primary"], mix(bg, c["primary"], .08), 4.6)
            line = rgb_str(c["primary"])
            chip_bg, chip_line = f"rgba({rgb_str(c['primary'])},.07)", f"rgba({rgb_str(c['primary'])},.16)"
            glass, glass_line, glass_hi = "rgba(255,255,255,.72)", "rgba(255,255,255,.9)", ".9"
            card, card_line, shadow = "#ffffff", f"rgba({rgb_str(c['dark'])},.08)", ".10"
        elif th == "brand":
            bg = c["primary"]
            fg = "#ffffff" if contrast("#ffffff", bg) >= 4.5 else c["ink"]
            soft = ensure_contrast(mix(fg, bg, .14), bg, 4.5)
            mute = ensure_contrast(mix(fg, bg, .3), bg, 3.2)
            accent = ensure_contrast(mix(c["secondary_light"], "#ffffff", .2), bg, 2.6)
            accent2 = "#ffffff"
            accent_text = fg
            line = "255,255,255"
            chip_bg, chip_line = "rgba(255,255,255,.14)", "rgba(255,255,255,.28)"
            glass, glass_line, glass_hi = "rgba(255,255,255,.12)", "rgba(255,255,255,.26)", ".35"
            card, card_line, shadow = "rgba(255,255,255,.12)", "rgba(255,255,255,.25)", ".25"
        else:  # dark / photo
            bg = dark
            fg = "#ffffff" if contrast("#ffffff", dark) >= 4.5 else c["ink"]
            soft = ensure_contrast(mix(fg, dark, .2), dark, 6)
            mute = ensure_contrast(mix(fg, dark, .42), dark, 4.5)
            accent = ensure_contrast(c["secondary_light"], dark, 3.2)
            accent2 = ensure_contrast(mix(c["primary_light"], "#ffffff", .38), dark, 3.2)
            accent_text = ensure_contrast(c["secondary_light"], mix(dark, "#ffffff", .08), 4.6)
            line = "255,255,255"
            chip_bg, chip_line = "rgba(255,255,255,.08)", "rgba(255,255,255,.16)"
            glass, glass_line, glass_hi = "rgba(255,255,255,.075)", "rgba(255,255,255,.16)", ".22"
            card, card_line, shadow = "rgba(255,255,255,.06)", "rgba(255,255,255,.12)", ".35"
        return {"bg": bg, "fg": fg, "soft": soft, "mute": mute, "accent": accent, "accent2": accent2, "accent_text": accent_text,
                "line": line, "chip_bg": chip_bg, "chip_line": chip_line, "glass": glass, "glass_line": glass_line,
                "glass_hi": glass_hi, "card": card, "card_line": card_line, "shadow": shadow}

    def vars(self) -> dict[str, str]:
        c = self.brand.colors
        k = self.colors()
        pt, pr, pb, pl = self.pads()
        light = self.theme == "light"
        return {
            "--w": f"{self.w}px", "--h": f"{self.h}px", "--s": f"{self.s:.4f}", "--g": f"{8 * self.s:.3f}px",
            "--u": f"{min(self.w, self.h) / 100:.3f}px",
            "--pt": f"{pt:.1f}px", "--pr": f"{pr:.1f}px", "--pb": f"{pb:.1f}px", "--pl": f"{pl:.1f}px",
            "--bg0": "transparent" if self.transparent else k["bg"], "--fg": k["fg"], "--fg-soft": k["soft"], "--fg-mute": k["mute"],
            "--accent": k["accent"], "--accent2": k["accent2"], "--accent-text": k["accent_text"], "--accent-rgb": rgb_str(k["accent"]),
            "--chip-bg": k["chip_bg"], "--chip-line": k["chip_line"], "--line-rgb": k["line"],
            "--glass": k["glass"], "--glass-line": k["glass_line"], "--glass-hi": k["glass_hi"],
            "--card": k["card"], "--card-line": k["card_line"], "--shadow-o": k["shadow"],
            "--primary": c["primary"], "--primary-rgb": rgb_str(c["primary"]), "--primary-light": c["primary_light"],
            "--secondary": c["secondary"], "--secondary-light": c["secondary_light"], "--secondary-rgb": rgb_str(c["secondary"]),
            "--dark": c["dark"], "--dark-rgb": rgb_str(c["dark"]), "--light": c["light"],
            "--grain": f"url('{grain_uri()}')", "--grain-o": ".10" if light else ".16", "--vig": ".0" if light else ".55",
            "--dot-o": ".16" if light else ".14",
            "--font-head": f"'{self.brand.heading_font}','{DISPLAY_FONT}','Inter',system-ui,sans-serif",
            "--font-body": f"'{self.brand.body_font}','Inter',system-ui,sans-serif",
        }

    def document(self, body: str, css: str = "", title: str = "media", *, script: str = "", head: str = "") -> str:
        root = ";".join(f"{k}:{v}" for k, v in self.vars().items())
        bgcss = "html,body{background:transparent!important}" if self.transparent else ""
        attrs = (f" data-mode='{self.motion}' data-dur='{self.duration:.3f}' data-drift='{1 if self.drift else 0}'"
                 f" data-fmt='{self.fmt}' data-theme='{self.theme}'")
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{esc(title)}</title><style>{font_faces()}:root{{{root}}}{CSS}{bgcss}{css}</style>{head}</head>"
            f"<body{attrs}>{body}<script>{FIT_JS}</script><script>{script}</script>"
            f"<script type='text/plain' id='__runtime'>{RUNTIME_JS}</script><script>{BOOT_JS}</script></body></html>"
        )

    # ---- helpers
    def px(self, v: float) -> str:
        return f"calc(var(--s)*{v:.2f}px)"

    def rnd(self, i: int, lo: float = 0, hi: float = 1) -> float:
        x = math.sin(self.seed * 0.0001 + i * 12.9898) * 43758.5453
        return lo + (x - math.floor(x)) * (hi - lo)

    # ---- backgrounds
    def bg(self, photo: str | None = None, *, variant: str = "aurora", scrim: str = "bottom", kb: str = "in") -> str:
        c = self.brand.colors
        drift = self.drift and self.motion != "still"
        if photo:
            scr = {
                "bottom": f"linear-gradient(180deg,rgba(var(--dark-rgb),.28) 0%,rgba(var(--dark-rgb),.12) 30%,rgba(var(--dark-rgb),.72) 62%,rgba(var(--dark-rgb),.96) 100%)",
                "left": f"linear-gradient(90deg,rgba(var(--dark-rgb),.95) 0%,rgba(var(--dark-rgb),.82) 45%,rgba(var(--dark-rgb),.15) 100%)",
                "full": "linear-gradient(180deg,rgba(var(--dark-rgb),.72),rgba(var(--dark-rgb),.86))",
            }.get(scrim, "")
            tint = f"linear-gradient(140deg,rgba({rgb_str(c['primary'])},.32),rgba({rgb_str(c['secondary'])},.10))"
            kb_attr = f" data-kb='{kb}'" if self.motion != "still" else ""
            return ("<!--bgl:" + kb + "--><div class='bgl layer'>"
                    f"<div class='layer' style='overflow:hidden'><div class='photo'{kb_attr} style=\"background-image:url('{photo}')\"></div></div>"
                    f"<div class='layer' style='background:{tint};mix-blend-mode:multiply'></div>"
                    f"<div class='layer' style='background:{scr}'></div><div class='grain'></div></div><!--/bgl-->")
        if self.theme == "light":
            base = f"linear-gradient(165deg,#ffffff 0%,{mix(c['light'], c['secondary_light'], .10)} 55%,{mix(c['light'], c['primary_light'], .14)} 100%)"
            blobs = [(c["secondary_light"], .30, 78, 6, 62), (c["primary_light"], .20, 8, 92, 70), (c["primary"], .08, 50, 50, 90)]
        elif self.theme == "brand":
            base = f"linear-gradient(140deg,{mix(c['primary'], c['dark'], .25)} 0%,{c['primary']} 45%,{mix(c['primary_light'], c['secondary'], .4)} 100%)"
            blobs = [(c["secondary_light"], .55, 85, 10, 55), ("#ffffff", .16, 10, 85, 60), (c["dark"], .45, 15, 15, 70)]
        else:
            base = f"linear-gradient(155deg,{mix(c['dark'], '#000000', .25)} 0%,{c['dark']} 45%,{mix(c['dark'], c['primary'], .5)} 100%)"
            blobs = [(c["primary_light"], .55, 82, 8, 60), (c["secondary_light"], .32, 96, 46, 42), (c["primary"], .65, 6, 96, 75),
                     (c["secondary"], .22, 30, 40, 50)]
        if variant == "spot":  # one big glow behind the centre (stats, greetings)
            blobs = [(c["primary_light"] if self.theme != "light" else c["secondary_light"], .5, 50, 46, 70)] + blobs[1:2]
        parts = ["<!--bgl:drift--><div class='bgl layer'>", f"<div class='layer' style='background:{base}'></div>"]
        for i, (col, a, x, y, r) in enumerate(blobs):
            x += self.rnd(i, -8, 8)
            y += self.rnd(i + 7, -6, 6)
            size = r * max(self.w, self.h) / 100 * 1.6
            dr = f" data-drift='{40 + 20 * i},{30 + 15 * i},1' data-phase='{i * .27:.2f}'" if drift else ""
            parts.append(f"<div class='blob'{dr} style='width:{size:.0f}px;height:{size:.0f}px;left:calc({x:.1f}% - {size / 2:.0f}px);"
                         f"top:calc({y:.1f}% - {size / 2:.0f}px);background:radial-gradient(closest-side,rgba({rgb_str(col)},{a}) 0%,"
                         f"rgba({rgb_str(col)},{a * .45:.3f}) 45%,transparent 100%)'></div>")
        if variant != "plain":
            parts.append("<div class='dots'></div>")
        parts.append("<div class='vignette'></div><div class='grain'></div></div><!--/bgl-->")
        return "".join(parts)

    def rings(self, x: str = "100%", y: str = "100%", r: float = 520, n: int = 4, op: float = .10) -> str:
        """Concentric hairline rings (depth accent)."""
        out = []
        for i in range(n):
            rr = r * (1 + i * .32) * self.s
            out.append(f"<div style='position:absolute;left:calc({x} - {rr:.0f}px);top:calc({y} - {rr:.0f}px);width:{2 * rr:.0f}px;height:{2 * rr:.0f}px;"
                       f"border-radius:50%;border:1px solid rgba(var(--line-rgb),{op * (1 - i * .2):.3f})'></div>")
        return "<div class='layer' style='pointer-events:none'>" + "".join(out) + "</div>"

    # ---- brand
    def logo(self, size: float = 64) -> str:
        b = self.brand
        st = f"width:{self.px(size)};height:{self.px(size)};border-radius:{self.px(size * .25)}"
        if b.logo_uri:
            return f"<span class='logo' style='{st}'><img alt='' src='{b.logo_uri}'></span>"
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", b.name)[:2]).upper() or "•"
        return f"<span class='logo' style='{st}'><span class='mono' style='font-size:{self.px(size * .42)}'>{esc(initials)}</span></span>"

    def lockup(self, sub: str | None = None, size: float = 64, name: bool = True) -> str:
        b = self.brand
        txt = ""
        if name:
            s2 = f"<div class='bsub'>{esc(sub)}</div>" if sub else ""
            txt = f"<div style='min-width:0'><div class='bname'>{esc(b.name)}</div>{s2}</div>"
        return f"<div class='lockup'>{self.logo(size)}{txt}</div>"

    def site(self) -> str:
        s = self.brand.website or self.brand.handle or ""
        return f"<span class='site'>{esc(s)}</span>" if s else "<span></span>"

    def footer(self, left: str | None = None, right: str | None = None, anim: bool = True) -> str:
        a = " data-anim='fade' data-order='12'" if anim else ""
        left = left if left is not None else f"<span class='rule' style='width:{self.px(64)}'></span>"
        return f"<div class='foot'{a}>{left}{right if right is not None else self.site()}</div>"

    def header(self, right: str = "", sub: str | None = None, name: bool = True) -> str:
        return f"<div class='head' data-anim='fade' data-order='0'>{self.lockup(sub, name=name)}{right}</div>"

    def eyebrow(self, text: str, order: int = 1) -> str:
        if not text:
            return ""
        return f"<span class='eyebrow' data-anim='rise' data-order='{order}'><span class='dot'></span>{esc(text)}</span>"

    def button(self, text: str, order: int = 8, ghost: bool = False, ic: str = "arrow") -> str:
        if not text:
            return ""
        return (f"<span class='btn{' ghost' if ghost else ''}' data-anim='pop' data-order='{order}'>{esc(text)}"
                f"{icon(ic, '1em', sw=2.4)}</span>")

    def badge(self, ic: str, size: float = 72, ratio: float = .5) -> str:
        return (f"<span class='badge' style='width:{self.px(size)};height:{self.px(size)};border-radius:{self.px(size * .28)}'>"
                f"{icon(ic, self.px(size * ratio), '#fff', 2)}</span>")

    # ---- charts (SVG; animate with data-anim='draw'/'grow')
    def donut(self, pct: float, size: float = 360, stroke: float = 34, label: str = "") -> str:
        pct = max(0.0, min(100.0, pct))
        r = 50 - stroke / size * 50
        circ = 2 * math.pi * r
        sw = stroke / size * 100
        gid = f"dg{self.seed % 9999}"
        c = self.colors()
        return (f"<svg viewBox='0 0 100 100' style='width:{self.px(size)};height:{self.px(size)};transform:rotate(-90deg);overflow:visible;display:block'>"
                f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{c['accent']}'/>"
                f"<stop offset='1' stop-color='{c['accent2']}'/></linearGradient></defs>"
                f"<circle cx='50' cy='50' r='{r:.2f}' fill='none' stroke='rgba(var(--line-rgb),.12)' stroke-width='{sw:.2f}'/>"
                f"<g data-anim='draw' data-order='3' data-dur='1.6'><path data-len='1' d='M {50 + r:.2f} 50 A {r:.2f} {r:.2f} 0 {1 if pct > 50 else 0} 1 "
                f"{50 + r * math.cos(2 * math.pi * pct / 100):.3f} {50 + r * math.sin(2 * math.pi * pct / 100):.3f}' fill='none' "
                f"stroke='url(#{gid})' stroke-width='{sw:.2f}' stroke-linecap='round'/></g></svg>") if pct < 99.9 else (
                f"<svg viewBox='0 0 100 100' style='width:{self.px(size)};height:{self.px(size)};display:block'><circle cx='50' cy='50' r='{r:.2f}' fill='none' "
                f"stroke='{c['accent']}' stroke-width='{sw:.2f}'/></svg>")

    def bars(self, values: list[float], labels: list[str], *, height: float = 380, highlight: int | None = None,
             show_values: list[str] | None = None, order: int = 3) -> str:
        n = len(values)
        mx = max(values) or 1
        hi = highlight if highlight is not None else values.index(max(values))
        cols = []
        for i, (v, lab) in enumerate(zip(values, labels)):
            hpct = max(4, v / mx * 100)
            on = i == hi
            fill = ("linear-gradient(180deg,var(--accent) 0%,var(--primary-light) 100%)" if on
                    else "linear-gradient(180deg,rgba(var(--line-rgb),.22),rgba(var(--line-rgb),.10))")
            val = (show_values[i] if show_values and i < len(show_values) else "")
            cols.append(
                f"<div class='bcol'><div class='bval num' data-anim='fade' data-order='{order + 2 + i * .5}' style='color:{'var(--fg)' if on else 'var(--fg-soft)'}'>{esc(val)}</div>"
                f"<div class='btrack'><div class='bar' data-anim='grow' data-order='{order + i * .6}' style='height:{hpct:.1f}%;background:{fill};"
                f"{'box-shadow:0 0 calc(var(--s)*40px) rgba(var(--accent-rgb),.35)' if on else ''}'></div></div>"
                f"<div class='blab' style='color:{'var(--fg)' if on else 'var(--fg-mute)'}'>{esc(lab)}</div></div>")
        css = (f"<style>.bars{{display:flex;align-items:stretch;gap:{self.px(28 if n < 5 else 18)};height:{self.px(height)}}}"
               ".bcol{flex:1;display:flex;flex-direction:column;align-items:stretch;min-width:0}"
               f".btrack{{flex:1;display:flex;align-items:flex-end;border-bottom:1px solid rgba(var(--line-rgb),.18)}}"
               f".bar{{width:100%;border-radius:{self.px(16)} {self.px(16)} {self.px(4)} {self.px(4)};transform-origin:bottom center}}"
               f".bval{{font-family:var(--font-head);font-weight:800;font-size:{self.px(34 if n < 5 else 26)};text-align:center;margin-bottom:{self.px(10)};letter-spacing:-.02em}}"
               f".blab{{font-weight:600;font-size:{self.px(21 if n < 5 else 18)};text-align:center;margin-top:{self.px(14)};white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}</style>")
        return css + "<div class='bars'>" + "".join(cols) + "</div>"

    def line_chart(self, values: list[float], *, width: float = 820, height: float = 300, order: int = 3) -> str:
        n = len(values)
        lo, hi = min(values), max(values)
        rng = (hi - lo) or 1
        pts = [(i / (n - 1) * 100, 90 - (v - lo) / rng * 78) for i, v in enumerate(values)]
        d = f"M{pts[0][0]:.2f},{pts[0][1]:.2f}"
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            cx = (x0 + x1) / 2
            d += f" C{cx:.2f},{y0:.2f} {cx:.2f},{y1:.2f} {x1:.2f},{y1:.2f}"
        area = d + " L100,100 L0,100 Z"
        gid = f"lg{self.seed % 9999}"
        c = self.colors()
        lx, ly = pts[-1]
        return (f"<svg viewBox='0 0 100 100' preserveAspectRatio='none' style='width:{self.px(width)};height:{self.px(height)};overflow:visible;display:block'>"
                f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{c['accent']}' stop-opacity='.38'/>"
                f"<stop offset='1' stop-color='{c['accent']}' stop-opacity='0'/></linearGradient>"
                f"<linearGradient id='{gid}s' x1='0' y1='0' x2='1' y2='0'><stop offset='0' stop-color='{c['accent2']}'/><stop offset='1' stop-color='{c['accent']}'/></linearGradient></defs>"
                + "".join(f"<line x1='0' x2='100' y1='{y}' y2='{y}' stroke='rgba(var(--line-rgb),.10)' stroke-width='.4' vector-effect='non-scaling-stroke'/>" for y in (22, 46, 70, 94))
                + f"<path d='{area}' fill='url(#{gid})' data-anim='fade' data-order='{order + 3}'/>"
                f"<g data-anim='draw' data-order='{order}' data-dur='1.6'><path data-len='1' d='{d}' fill='none' stroke='url(#{gid}s)' stroke-width='{max(3, 5 * self.s):.1f}' "
                f"vector-effect='non-scaling-stroke' stroke-linecap='round' stroke-linejoin='round'/></g></svg>"
                f"<div data-anim='pop' data-order='{order + 5}' style='position:absolute;left:calc({lx:.2f}% - {self.px(14)});top:calc({ly:.2f}% - {self.px(14)});"
                f"width:{self.px(28)};height:{self.px(28)};border-radius:50%;background:{c['accent']};box-shadow:0 0 0 {self.px(10)} rgba(var(--accent-rgb),.22),0 0 {self.px(40)} rgba(var(--accent-rgb),.6)'></div>")

    # ---- devices
    def phone(self, inner: str, width: float = 380) -> str:
        h = width * 2.05
        return (f"<div class='phone' style='position:relative;width:{self.px(width)};height:{self.px(h)};border-radius:{self.px(width * .16)};"
                f"background:linear-gradient(150deg,#1d2939,#0b1220);padding:{self.px(width * .035)};"
                f"box-shadow:0 0 0 {self.px(2)} rgba(255,255,255,.14) inset,0 {self.px(40)} {self.px(90)} rgba(2,12,27,.5),0 {self.px(10)} {self.px(24)} rgba(2,12,27,.3)'>"
                f"<div style='position:relative;width:100%;height:100%;border-radius:{self.px(width * .13)};overflow:hidden;background:#fff'>{inner}"
                f"<div style='position:absolute;top:{self.px(width * .035)};left:50%;transform:translateX(-50%);width:{self.px(width * .3)};height:{self.px(width * .085)};"
                f"border-radius:999px;background:#0b1220'></div></div></div>")

    def browser(self, inner: str, width: float = 860, ratio: float = .62, url: str | None = None) -> str:
        h = width * ratio
        url = url or (self.brand.website or "app")
        bar = self.px(46)
        dots = "".join(f"<i style='width:{self.px(12)};height:{self.px(12)};border-radius:50%;background:{c};display:block'></i>"
                       for c in ("#ff5f57", "#febc2e", "#28c840"))
        return (f"<div style='position:relative;width:{self.px(width)};height:{self.px(h)};border-radius:{self.px(20)};overflow:hidden;background:#fff;"
                f"box-shadow:0 0 0 1px rgba(255,255,255,.12),0 {self.px(40)} {self.px(100)} rgba(2,12,27,.45),0 {self.px(8)} {self.px(24)} rgba(2,12,27,.25)'>"
                f"<div style='height:{bar};display:flex;align-items:center;gap:{self.px(8)};padding:0 {self.px(18)};background:#f1f5f9;border-bottom:1px solid #e2e8f0'>{dots}"
                f"<span style='margin-left:{self.px(18)};flex:1;max-width:60%;height:{self.px(26)};border-radius:999px;background:#fff;border:1px solid #e2e8f0;"
                f"font:500 {self.px(14)}/{self.px(26)} Inter,sans-serif;color:#64748b;padding:0 {self.px(14)};white-space:nowrap;overflow:hidden'>{esc(url)}</span></div>"
                f"<div style='position:absolute;top:{bar};left:0;right:0;bottom:0;overflow:hidden'>{inner}</div></div>")

    def ui_mock(self, title: str = "Overview", kpis: list[tuple[str, str]] | None = None, compact: bool = False) -> str:
        """An original, generic product UI (sidebar, KPI cards, chart, list) in brand colours. Not a real screen."""
        c = self.brand.colors
        kpis = (kpis or [("Active employees", "1,284"), ("On leave today", "37"), ("Open roles", "12")])[:3]
        p = self.px
        bars = "".join(f"<i style='flex:1;border-radius:{p(6)} {p(6)} 0 0;height:{h}%;background:{c['primary_light'] if i in (5, 9) else mix(c['primary_light'], '#ffffff', .65)}'></i>"
                       for i, h in enumerate([42, 55, 48, 66, 58, 80, 62, 70, 64, 92, 74, 68]))
        rows = "".join(
            f"<div style='display:flex;align-items:center;gap:{p(12)};padding:{p(10)} 0;border-top:1px solid #eef2f7'>"
            f"<span style='width:{p(30)};height:{p(30)};border-radius:50%;background:{col}'></span>"
            f"<span style='flex:1'><i style='display:block;height:{p(9)};width:{w}%;border-radius:9px;background:#cbd5e1'></i>"
            f"<i style='display:block;height:{p(7)};width:{w - 18}%;border-radius:9px;background:#e2e8f0;margin-top:{p(6)}'></i></span>"
            f"<span style='font:600 {p(12)} Inter;color:{tc};background:{bg};padding:{p(4)} {p(10)};border-radius:999px'>{lab}</span></div>"
            for col, w, lab, tc, bg in [(mix(c["primary"], "#ffffff", .5), 70, "Approved", "#047857", "#d1fae5"),
                                        (mix(c["secondary"], "#ffffff", .4), 58, "Pending", "#b45309", "#fef3c7"),
                                        (mix(c["dark"], "#ffffff", .55), 64, "Approved", "#047857", "#d1fae5")])
        side = "" if compact else (
            f"<div style='width:18%;background:{c['dark']};padding:{p(18)} {p(14)};display:flex;flex-direction:column;gap:{p(12)}'>"
            f"<span style='width:{p(34)};height:{p(34)};border-radius:{p(9)};background:linear-gradient(135deg,{c['primary_light']},{c['secondary_light']})'></span>"
            + "".join(f"<i style='display:block;height:{p(9)};border-radius:9px;background:rgba(255,255,255,{o});width:{w}%;margin-top:{p(10)}'></i>"
                      for o, w in [(.55, 80), (.25, 64), (.25, 72), (.25, 56), (.25, 68)]) + "</div>")
        kp = "".join(f"<div style='flex:1;background:#fff;border:1px solid #e9eef5;border-radius:{p(14)};padding:{p(14)}'>"
                     f"<div style='font:500 {p(12)} Inter;color:#64748b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{esc(k)}</div>"
                     f"<div style='font:800 {p(26)} \"Plus Jakarta Sans\",Inter;color:#0f172a;margin-top:{p(4)};letter-spacing:-.02em'>{esc(v)}</div></div>"
                     for k, v in kpis)
        return (f"<div style='display:flex;width:100%;height:100%;background:#f6f8fb;font-family:Inter'>{side}"
                f"<div style='flex:1;padding:{p(20)};display:flex;flex-direction:column;gap:{p(14)};min-width:0'>"
                f"<div style='display:flex;align-items:center;justify-content:space-between'><b style='font:800 {p(20)} \"Plus Jakarta Sans\",Inter;color:#0f172a'>{esc(title)}</b>"
                f"<span style='height:{p(28)};width:{p(110)};border-radius:999px;background:linear-gradient(90deg,{c['primary']},{c['primary_light']})'></span></div>"
                f"<div style='display:flex;gap:{p(12)}'>{kp}</div>"
                f"<div style='background:#fff;border:1px solid #e9eef5;border-radius:{p(14)};padding:{p(14)};flex:1;display:flex;flex-direction:column;min-height:0'>"
                f"<i style='display:block;height:{p(9)};width:30%;border-radius:9px;background:#cbd5e1'></i>"
                f"<div style='flex:1;display:flex;align-items:flex-end;gap:{p(8)};margin-top:{p(12)}'>{bars}</div></div>"
                f"<div style='background:#fff;border:1px solid #e9eef5;border-radius:{p(14)};padding:{p(4)} {p(14)}'>{rows}</div>"
                "</div></div>")
