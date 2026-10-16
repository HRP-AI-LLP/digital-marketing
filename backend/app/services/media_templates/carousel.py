"""Carousel / document slides: cover, content slide, call-to-action."""

from __future__ import annotations

from app.services.media_templates.base import Page, as_list, esc, plain, rich, size_for

_COUNTER_CSS = """
.count{font-weight:650;font-size:calc(var(--u)*2.3);color:var(--fg-soft);font-variant-numeric:tabular-nums;
  border:1px solid var(--chip-line);background:var(--chip-bg);border-radius:999px;padding:calc(var(--u)*.7) calc(var(--u)*1.8)}
.progress{display:flex;gap:calc(var(--u)*.8);flex:1;max-width:calc(var(--u)*40)}
.progress i{flex:1;height:calc(var(--u)*.7);border-radius:999px;background:rgba(var(--grid-rgb),.16)}
.progress i.on{background:linear-gradient(90deg,var(--accent),var(--accent2))}
.swipe{display:inline-flex;align-items:center;gap:calc(var(--u)*1.2);font-weight:700;font-size:calc(var(--u)*2.6);color:var(--fg)}
.swipe .arrow{display:grid;place-items:center;width:calc(var(--u)*6);height:calc(var(--u)*6);border-radius:50%;color:#fff;
  background:linear-gradient(135deg,var(--primary),var(--secondary-deep));font-size:calc(var(--u)*3)}
"""


def _progress(index: int, total: int) -> str:
    total = max(1, min(total, 12))
    return "<div class='progress'>" + "".join(f"<i class='{'on' if i < index else ''}'></i>" for i in range(total)) + "</div>"


def _counter(index: int, total: int) -> str:
    return f"<span class='count'>{index:02d} / {total:02d}</span>" if total > 1 else ""


def carousel_cover(p: Page, f: dict, photo: str | None) -> str:
    headline = f.get("headline") or ""
    sub = f.get("body") or f.get("subtitle") or ""
    eyebrow = f.get("eyebrow") or ""
    total = int(f.get("total") or 1)
    hs = size_for(headline, 10 if not p.wide else 7.6, soft=30, floor=5)
    css = _COUNTER_CSS + ".content{justify-content:center}"
    return p.document(
        f"<div class='canvas'>{p.background(photo, headline)}<div class='frame'>"
        f"<div class='top'>{p.brandmark()}{_counter(1, total)}</div>"
        f"<div class='content' data-fitbox>"
        + (f"<span class='eyebrow'>{esc(eyebrow)}</span><div style='height:calc(var(--u)*3)'></div>" if eyebrow else "")
        + f"<h1 class='headline' data-fit style='font-size:calc(var(--u)*{hs:.2f})'>{rich(headline)}</h1>"
        + (f"<p class='body' data-fit style='font-size:calc(var(--u)*3.5);margin-top:calc(var(--u)*3);max-width:88%'>{rich(sub)}</p>" if sub else "")
        + "</div><div class='footer'><span class='rule'></span>"
        + ("<span class='swipe'>Swipe <span class='arrow'>→</span></span>" if total > 1 else "")
        + "</div></div></div>", css, plain(headline))


def carousel_slide(p: Page, f: dict, photo: str | None) -> str:
    heading = f.get("heading") or f.get("headline") or ""
    body = f.get("body") or ""
    bullets = as_list(f.get("bullets"), 5)
    index = int(f.get("index") or 2)
    total = int(f.get("total") or index)
    num = int(f.get("number") or max(1, index - 1))
    hs = size_for(heading, 6.6 if not p.wide else 5.2, soft=36, floor=3.8)
    bs = size_for(body, 3.6 if not p.wide else 3.0, soft=160, floor=2.3)
    css = _COUNTER_CSS + """
.content{justify-content:center}
.big{font-weight:850;font-size:calc(var(--u)*15);line-height:.9;letter-spacing:-.06em;
  -webkit-text-stroke:calc(var(--u)*.28) var(--accent);color:transparent;opacity:.9;margin-bottom:calc(var(--u)*3)}
ul.pts{list-style:none;margin-top:calc(var(--u)*3);display:flex;flex-direction:column;gap:calc(var(--u)*1.8)}
ul.pts li{display:flex;gap:calc(var(--u)*1.8);align-items:flex-start;font-weight:500;line-height:1.35;color:var(--fg)}
ul.pts li::before{content:"";flex:none;width:calc(var(--u)*1.5);height:calc(var(--u)*1.5);border-radius:50%;margin-top:calc(var(--u)*1.2);
  background:linear-gradient(135deg,var(--accent),var(--accent2))}
"""
    pts = "".join(f"<li><span data-fit style='font-size:calc(var(--u)*{bs:.2f})'>{rich(b)}</span></li>" for b in bullets)
    return p.document(
        f"<div class='canvas'>{p.background(photo, heading)}<div class='frame'>"
        f"<div class='top'>{p.brandmark(show_name=False)}{_counter(index, total)}</div>"
        f"<div class='content' data-fitbox><div class='big'>{num:02d}</div>"
        f"<h2 class='headline' data-fit style='font-size:calc(var(--u)*{hs:.2f})'>{rich(heading)}</h2>"
        + (f"<p class='body' data-fit style='font-size:calc(var(--u)*{bs:.2f});margin-top:calc(var(--u)*2.6)'>{rich(body)}</p>" if body else "")
        + (f"<ul class='pts'>{pts}</ul>" if pts else "")
        + f"</div><div class='footer'>{_progress(index, total)}"
        + ("<span class='swipe'><span class='arrow'>→</span></span>" if index < total else "")
        + "</div></div></div>", css, plain(heading))


def carousel_cta(p: Page, f: dict, photo: str | None) -> str:
    heading = f.get("heading") or f.get("headline") or "Found this useful?"
    body = f.get("body") or "Save it for later and share it with someone who needs it."
    cta = f.get("cta") or ""
    index = int(f.get("index") or 1)
    total = int(f.get("total") or index)
    site = p.brand.website or p.brand.handle or ""
    css = _COUNTER_CSS + """
.content{justify-content:center;align-items:center;text-align:center}
.biglogo .chip{width:calc(var(--u)*20);height:calc(var(--u)*20);border-radius:calc(var(--u)*5)}
.biglogo .chip .mono{font-size:calc(var(--u)*8)}
.actions{display:flex;gap:calc(var(--u)*1.6);justify-content:center;flex-wrap:wrap;margin-top:calc(var(--u)*3.4)}
.act{font-weight:600;font-size:calc(var(--u)*2.4);color:var(--fg);border:1px solid var(--chip-line);background:var(--chip-bg);
  border-radius:999px;padding:calc(var(--u)*1) calc(var(--u)*2.2)}
"""
    logo = p.brandmark(show_name=False).replace("class='brandmark'", "class='brandmark biglogo'")
    return p.document(
        f"<div class='canvas'>{p.background(photo, heading + 'cta')}<div class='frame'>"
        f"<div class='top'><span></span>{_counter(index, total)}</div>"
        f"<div class='content' data-fitbox>{logo}<div style='height:calc(var(--u)*4)'></div>"
        f"<h2 class='headline' data-fit style='font-size:calc(var(--u)*{size_for(heading, 7, soft=26, floor=4):.2f})'>{rich(heading)}</h2>"
        f"<p class='body' data-fit style='font-size:calc(var(--u)*3.3);margin-top:calc(var(--u)*2.4);max-width:86%'>{rich(body)}</p>"
        "<div class='actions'><span class='act'>Like</span><span class='act'>Save</span><span class='act'>Share</span>"
        f"<span class='act'>Follow {esc(p.brand.name)}</span></div>"
        + (f"<div style='margin-top:calc(var(--u)*4)'><span class='pill'>{esc(cta)} →</span></div>" if cta else "")
        + f"</div><div class='footer' style='justify-content:center'><span class='site'>{esc(site)}</span></div></div></div>",
        css, plain(heading))
