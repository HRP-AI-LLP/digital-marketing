"""Single-image templates: photo headline, quote, tips, stat, announcement."""

from __future__ import annotations

from app.services.media_templates.base import Page, as_list, esc, plain, rich, size_for


def photo_headline(p: Page, f: dict, photo: str | None) -> str:
    headline = f.get("headline") or ""
    hs = size_for(headline, 9.2 if not p.wide else 7.4, soft=38, floor=4.6)
    body = f.get("body") or ""
    eyebrow = f.get("eyebrow") or ""
    cta = f.get("cta") or ""
    css = ".content{justify-content:%s}" % ("flex-end" if photo else "center")
    inner = (
        (f"<span class='eyebrow'>{esc(eyebrow)}</span><div style='height:calc(var(--u)*2.6)'></div>" if eyebrow else "")
        + f"<h1 class='headline' data-fit style='font-size:calc(var(--u)*{hs:.2f})'>{rich(headline)}</h1>"
        + (f"<p class='body' data-fit style='font-size:calc(var(--u)*3.3);margin-top:calc(var(--u)*2.6);max-width:92%'>{rich(body)}</p>"
           if body else "")
        + (f"<div style='margin-top:calc(var(--u)*3.6)'><span class='pill'>{esc(cta)} →</span></div>" if cta else "")
    )
    return p.document(
        f"<div class='canvas'>{p.background(photo, headline)}"
        f"<div class='frame'><div class='top'>{p.brandmark(show_name=True)}</div>"
        f"<div class='content' data-fitbox>{inner}</div>{_rule_footer(p)}</div></div>", css, plain(headline))


def _rule_footer(p: Page) -> str:
    site = p.brand.website or p.brand.handle
    return ("<div class='footer'><span class='rule'></span>"
            + (f"<span class='site'>{esc(site)}</span>" if site else "") + "</div>")


def quote_card(p: Page, f: dict, photo: str | None) -> str:
    quote = (f.get("quote") or f.get("headline") or "").strip().strip('"“”')
    qs = size_for(quote, 6.6 if not p.wide else 5.4, soft=70, floor=3.4)
    author = f.get("author") or ""
    role = f.get("role") or ""
    css = """
.content{justify-content:center}
.qmark{font-family:Georgia,'Times New Roman',serif;font-weight:700;line-height:.8;font-size:calc(var(--u)*22);
  background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent;height:calc(var(--u)*12)}
.quote{font-weight:650;letter-spacing:-.018em;line-height:1.22;color:var(--fg)}
.who{display:flex;align-items:center;gap:calc(var(--u)*2);margin-top:calc(var(--u)*4.5)}
.who b{display:block;font-size:calc(var(--u)*3);font-weight:700;color:var(--fg)}
.who span{display:block;font-size:calc(var(--u)*2.5);color:var(--fg-soft);margin-top:calc(var(--u)*.4)}
"""
    who = ""
    if author or role:
        who = (f"<div class='who'><span class='rule' style='width:calc(var(--u)*7)'></span><div>"
               f"{f'<b>{esc(author)}</b>' if author else ''}{f'<span>{esc(role)}</span>' if role else ''}</div></div>")
    return p.document(
        f"<div class='canvas'>{p.background(photo, quote)}<div class='frame'>"
        f"<div class='content' data-fitbox><div class='qmark'>“</div>"
        f"<blockquote class='quote' data-fit style='font-size:calc(var(--u)*{qs:.2f})'>{rich(quote)}</blockquote>{who}</div>"
        f"{p.footer()}</div></div>", css, plain(quote))


def tip_card(p: Page, f: dict, photo: str | None) -> str:
    title = f.get("headline") or f.get("title") or ""
    bullets = as_list(f.get("bullets"), 5)
    eyebrow = f.get("eyebrow") or ""
    ts = size_for(title, 6.4 if not p.wide else 5.2, soft=40, floor=3.6)
    bs = 3.25 if len(bullets) <= 3 else 2.95
    css = """
.content{justify-content:center}
ol.tips{list-style:none;display:flex;flex-direction:column;gap:calc(var(--u)*2.2);margin-top:calc(var(--u)*4.2)}
ol.tips li{display:flex;align-items:flex-start;gap:calc(var(--u)*2.4)}
.num{flex:none;display:grid;place-items:center;width:calc(var(--u)*6);height:calc(var(--u)*6);border-radius:calc(var(--u)*1.6);
  font-weight:800;font-size:calc(var(--u)*2.7);color:#fff;background:linear-gradient(135deg,var(--primary),var(--secondary-deep));
  box-shadow:0 calc(var(--u)*.4) calc(var(--u)*1.4) rgba(var(--primary-rgb),.3)}
.tip{font-weight:520;line-height:1.35;color:var(--fg);padding-top:calc(var(--u)*.8)}
"""
    items = "".join(
        f"<li><span class='num'>{i}</span><span class='tip' data-fit style='font-size:calc(var(--u)*{bs})'>{rich(b)}</span></li>"
        for i, b in enumerate(bullets, 1)
    )
    return p.document(
        f"<div class='canvas'>{p.background(photo, title)}<div class='frame'>"
        f"<div class='content' data-fitbox>"
        + (f"<span class='eyebrow'>{esc(eyebrow)}</span><div style='height:calc(var(--u)*2.6)'></div>" if eyebrow else "")
        + f"<h1 class='headline' data-fit style='font-size:calc(var(--u)*{ts:.2f})'>{rich(title)}</h1>"
        f"<ol class='tips'>{items}</ol></div>{p.footer()}</div></div>", css, plain(title))


def stat_card(p: Page, f: dict, photo: str | None) -> str:
    stat = str(f.get("stat") or "").strip()
    label = f.get("stat_label") or f.get("headline") or ""
    context = f.get("body") or ""
    source = f.get("source") or ""
    ss = 24 if len(stat) <= 4 else max(11, 24 * (4 / len(stat)) ** 0.7)
    if p.wide:
        ss *= 0.8
    css = """
.content{justify-content:center}
.stat{font-weight:850;letter-spacing:-.05em;line-height:.95;white-space:nowrap;
  background:linear-gradient(110deg,var(--accent) 0%,var(--accent2) 70%);-webkit-background-clip:text;background-clip:text;color:transparent;
  padding-bottom:calc(var(--u)*1)}
.label{font-weight:750;letter-spacing:-.02em;line-height:1.15;color:var(--fg);margin-top:calc(var(--u)*2.4)}
.src{font-size:calc(var(--u)*2.1);color:var(--fg-soft);margin-top:calc(var(--u)*3)}
"""
    return p.document(
        f"<div class='canvas'>{p.background(photo, stat + label)}<div class='frame'>"
        f"<div class='content' data-fitbox>"
        + (f"<span class='eyebrow'>{esc(f.get('eyebrow'))}</span><div style='height:calc(var(--u)*3)'></div>" if f.get("eyebrow") else "")
        + f"<div class='stat' data-fit style='font-size:calc(var(--u)*{ss:.2f})'>{esc(stat)}</div>"
        f"<div class='label' data-fit style='font-size:calc(var(--u)*{size_for(label, 5.4, soft=40, floor=3.2):.2f})'>{rich(label)}</div>"
        + (f"<p class='body' data-fit style='font-size:calc(var(--u)*3);margin-top:calc(var(--u)*2.4);max-width:90%'>{rich(context)}</p>" if context else "")
        + (f"<div class='src'>Source: {esc(source)}</div>" if source else "")
        + f"</div>{p.footer()}</div></div>", css, plain(stat + " " + label))


def announcement(p: Page, f: dict, photo: str | None) -> str:
    headline = f.get("headline") or ""
    body = f.get("body") or ""
    cta = f.get("cta") or ""
    eyebrow = f.get("eyebrow") or "Announcement"
    date = f.get("date") or ""
    hs = size_for(headline, 8.4 if not p.wide else 6.6, soft=34, floor=4.2)
    css = """
.content{justify-content:center;align-items:flex-start}
.date{font-weight:600;font-size:calc(var(--u)*2.6);color:var(--accent-text);margin-top:calc(var(--u)*3)}
.burst{position:absolute;right:calc(var(--u)*-12);bottom:calc(var(--u)*-12);width:calc(var(--u)*55);height:calc(var(--u)*55);border-radius:50%;
  border:calc(var(--u)*.35) solid rgba(var(--grid-rgb),.10);box-shadow:0 0 0 calc(var(--u)*6) rgba(var(--grid-rgb),.035),0 0 0 calc(var(--u)*12) rgba(var(--grid-rgb),.025)}
"""
    return p.document(
        f"<div class='canvas'>{p.background(photo, headline)}<div class='burst'></div><div class='frame'>"
        f"<div class='top'>{p.brandmark()}</div>"
        f"<div class='content' data-fitbox><span class='eyebrow'>{esc(eyebrow)}</span>"
        f"<div style='height:calc(var(--u)*3)'></div>"
        f"<h1 class='headline' data-fit style='font-size:calc(var(--u)*{hs:.2f})'>{rich(headline)}</h1>"
        + (f"<p class='body' data-fit style='font-size:calc(var(--u)*3.3);margin-top:calc(var(--u)*2.8);max-width:90%'>{rich(body)}</p>" if body else "")
        + (f"<div class='date'>{esc(date)}</div>" if date else "")
        + (f"<div style='margin-top:calc(var(--u)*4)'><span class='pill'>{esc(cta)} →</span></div>" if cta else "")
        + f"</div>{_rule_footer(p)}</div></div>", css, plain(headline))
