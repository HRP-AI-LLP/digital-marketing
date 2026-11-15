"""Academy lesson slides (16:9) and code cards for Shorts.

Each slide of a lesson_script is drawn in "steps": bullets, steps or diagram nodes appear one by one, a quiz shows
its answer in the last step. The lesson video shows the steps in turn while the teacher speaks. Code is highlighted
with Pygments; Hindi text uses Noto Sans Devanagari (installed in the image), technical words stay in Latin letters.
"""

from __future__ import annotations

import html

from app.services.media_templates.kit import Kit, icon, icon_for

FONT = "'Plus Jakarta Sans','Inter','Noto Sans Devanagari','Noto Sans',system-ui,sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono','Noto Sans Mono',ui-monospace,monospace"
KIND_LABEL = {"en": {"analogy": "Think of it like this", "code": "Let's code", "diagram": "How it fits together",
                     "steps": "Step by step", "quiz": "Quick quiz", "recap": "Recap", "outro": "Up next"},
              "hi": {"analogy": "ऐसे समझिए", "code": "चलिए code करें", "diagram": "पूरी तस्वीर",
                     "steps": "Step by step", "quiz": "Quick quiz", "recap": "Recap", "outro": "अगला lesson"}}
MAX_STEPS = 6


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def steps_of(slide: dict) -> int:
    """How many reveal steps a slide has."""
    kind = slide.get("kind")
    if kind == "quiz" and slide.get("quiz"):
        return 2
    if slide.get("diagram"):
        return min(MAX_STEPS, len(slide["diagram"]["nodes"]))
    if kind in ("title", "outro", "code"):
        return 1
    return max(1, min(MAX_STEPS, len(slide.get("bullets") or [])))


def highlight(code: str, language: str) -> str:
    try:
        from pygments import highlight as _hl
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import TextLexer, get_lexer_by_name

        try:
            lexer = get_lexer_by_name({"c#": "csharp", "dotnet": "csharp", "c++": "cpp", "shell": "bash",
                                       "golang": "go"}.get(language, language))
        except Exception:  # noqa: BLE001
            lexer = TextLexer()
        return _hl(code, lexer, HtmlFormatter(nowrap=True, noclasses=True, style="monokai"))
    except Exception:  # noqa: BLE001
        return esc(code)


CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:var(--w);height:var(--h);overflow:hidden;font-family:{FONT};color:#f4f7fb}}
.bg{{position:absolute;inset:0;background:
  radial-gradient(1200px 600px at 85% -10%,rgba(var(--primary-rgb),.55),transparent 60%),
  radial-gradient(900px 500px at -10% 110%,rgba(var(--secondary-rgb),.35),transparent 60%),
  var(--dark);}}
.grid{{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),
  linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:48px 48px}}
.top{{position:absolute;left:56px;right:56px;top:30px;display:flex;align-items:center;justify-content:space-between;
  font-size:19px;color:rgba(255,255,255,.75)}}
.chip{{display:inline-flex;align-items:center;gap:10px;padding:7px 16px;border-radius:999px;background:rgba(255,255,255,.08);
  border:1px solid rgba(255,255,255,.14)}}
.top .logo{{width:44px;height:44px;border-radius:11px;overflow:hidden;display:inline-flex;align-items:center;justify-content:center;
  background:#fff}}
.top .logo img{{width:100%;height:100%;object-fit:contain}}
.top .logo .mono{{color:var(--dark);font-weight:800}}
.main{{position:absolute;left:72px;right:72px;top:104px;bottom:64px;display:flex;flex-direction:column}}
.eyebrow{{font-size:20px;letter-spacing:.08em;text-transform:uppercase;color:var(--secondary-light);font-weight:700;
  display:flex;align-items:center;gap:10px;margin-bottom:10px}}
h1{{font-size:50px;line-height:1.12;font-weight:800;text-wrap:balance;letter-spacing:-.01em}}
.body{{flex:1;display:flex;gap:40px;margin-top:26px;min-height:0}}
.col{{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center}}
ul.pts{{list-style:none;display:flex;flex-direction:column;gap:18px}}
ul.pts li{{font-size:31px;line-height:1.3;display:flex;gap:18px;align-items:flex-start}}
ul.pts li .n{{flex:none;width:44px;height:44px;border-radius:12px;background:rgba(var(--primary-rgb),.9);display:flex;
  align-items:center;justify-content:center;font-size:22px;font-weight:800}}
ul.pts li .d{{flex:none;width:14px;height:14px;border-radius:50%;background:var(--secondary-light);margin-top:13px}}
ul.pts li.off{{visibility:hidden}}
ul.pts li.now{{color:#fff}} ul.pts li.was{{color:rgba(255,255,255,.62)}}
.code{{background:#1e1f1c;border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:22px 26px;overflow:hidden;
  box-shadow:0 20px 60px rgba(0,0,0,.45)}}
.code .bar{{display:flex;gap:8px;margin-bottom:14px;align-items:center;color:rgba(255,255,255,.5);font-size:15px}}
.code .bar i{{width:12px;height:12px;border-radius:50%;display:inline-block}}
.code pre{{font-family:{MONO};font-size:27px;line-height:1.45;white-space:pre;color:#f8f8f2}}
.flow{{display:flex;align-items:center;justify-content:center;gap:18px;flex-wrap:wrap}}
.node{{padding:22px 26px;border-radius:18px;background:rgba(255,255,255,.08);border:2px solid rgba(var(--secondary-rgb),.8);
  font-size:27px;font-weight:700;text-align:center;max-width:300px}}
.node.off,.arrow.off{{visibility:hidden}}
.node.now{{background:rgba(var(--primary-rgb),.85);border-color:#fff}}
.arrow{{font-size:40px;color:var(--secondary-light)}}
.quiz .q{{font-size:34px;font-weight:700;margin-bottom:26px;line-height:1.25}}
.quiz .opt{{font-size:28px;padding:16px 22px;border-radius:16px;border:2px solid rgba(255,255,255,.18);margin-bottom:14px;
  display:flex;gap:16px;align-items:center}}
.quiz .opt b{{flex:none;width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.12);display:flex;align-items:center;
  justify-content:center}}
.quiz .opt.ok{{border-color:#34d399;background:rgba(52,211,153,.16)}}
.quiz .opt.ok b{{background:#34d399;color:#05261a}}
.analogy{{display:flex;align-items:center;justify-content:center}}
.analogy .ic{{color:var(--secondary-light)}}
.center{{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:flex-start}}
.title h1{{font-size:68px;max-width:22ch}}
.title .sub{{font-size:28px;color:rgba(255,255,255,.8);margin-top:20px;max-width:40ch}}
.panel{{background:rgba(6,18,38,.62);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.14);border-radius:28px;
  padding:44px 52px}}
.prog{{position:absolute;left:0;bottom:0;height:6px;background:var(--secondary-light)}}
.foot{{position:absolute;left:72px;right:72px;bottom:20px;font-size:17px;color:rgba(255,255,255,.55);display:flex;
  justify-content:space-between}}
"""


def _top(k: Kit, ctx: dict) -> str:
    left = f"{esc(ctx.get('course'))} · {esc(ctx.get('lesson_label'))}"
    return f"<div class='top'><span class='chip'>{icon('layers', '18px')} {left}</span>{k.logo(44)}</div>"


def _bullets(items: list[str], shown: int, numbered: bool) -> str:
    lis = []
    for i, b in enumerate(items):
        cls = "off" if i >= shown else ("now" if i == shown - 1 else "was")
        mark = f"<span class='n'>{i + 1}</span>" if numbered else "<span class='d'></span>"
        lis.append(f"<li class='{cls}'>{mark}<span>{esc(b)}</span></li>")
    return f"<ul class='pts'>{''.join(lis)}</ul>"


def _diagram(d: dict, shown: int) -> str:
    out = []
    for i, n in enumerate(d["nodes"]):
        if i:
            out.append(f"<span class='arrow{' off' if i >= shown else ''}'>→</span>")
        cls = "off" if i >= shown else ("now" if i == shown - 1 else "")
        out.append(f"<div class='node {cls}'>{esc(n)}</div>")
    if d.get("flow") == "cycle" and shown >= len(d["nodes"]):
        out.append("<span class='arrow'>↺</span>")
    return f"<div class='flow'>{''.join(out)}</div>"


def _code(c: dict, big: bool) -> str:
    size = "" if big else " style='font-size:21px'"
    return (f"<div class='code'><div class='bar'><i style='background:#ff5f57'></i><i style='background:#febc2e'></i>"
            f"<i style='background:#28c840'></i>&nbsp; {esc(c.get('language'))}</div>"
            f"<pre{size}>{highlight(c['text'], c.get('language') or 'text')}</pre></div>")


def slide_html(k: Kit, slide: dict, step: int, ctx: dict) -> str:
    """One step (1-based) of a lesson slide. ctx: course, lesson_label, lang, module, index, total, next_title.
    Title and outro slides are transparent (drawn over a 3D scene) when ctx["over_3d"]."""
    kind = slide.get("kind") or "explain"
    lang = ctx.get("lang") if ctx.get("lang") in KIND_LABEL else "en"
    labels = KIND_LABEL[lang]
    over = bool(ctx.get("over_3d")) and kind in ("title", "outro")
    bg = "" if over else "<div class='bg'></div><div class='grid'></div>"
    head = esc(slide.get("heading"))
    bullets = slide.get("bullets") or []
    if kind == "title":
        inner = (f"<div class='center title'><div class='panel'><div class='eyebrow'>{esc(ctx.get('lesson_label'))}"
                 f" · {esc(ctx.get('module'))}</div><h1>{head}</h1>"
                 + (f"<div class='sub'>{esc(bullets[0])}</div>" if bullets else "") + "</div></div>")
    elif kind == "outro":
        nxt = ctx.get("next_title")
        inner = (f"<div class='center title'><div class='panel'><div class='eyebrow'>{labels['outro']}</div>"
                 f"<h1>{esc(nxt) if nxt else head}</h1><div class='sub'>"
                 + esc(ctx.get("follow") or "") + "</div></div></div>")
    else:
        eyebrow = labels.get(kind) or esc(ctx.get("module"))
        body = ""
        if kind == "quiz" and slide.get("quiz"):
            q = slide["quiz"]
            opts = "".join(f"<div class='opt{' ok' if step > 1 and i == q['answer'] else ''}'><b>{'ABCD'[i]}</b>{esc(o)}</div>"
                           for i, o in enumerate(q["options"]))
            body = f"<div class='col quiz'><div class='q'>{esc(q['question'])}</div>{opts}</div>"
        elif slide.get("diagram"):
            body = f"<div class='col'>{_diagram(slide['diagram'], step)}</div>"
            if bullets:
                body += f"<div class='col' style='flex:.7'>{_bullets(bullets[:3], len(bullets[:3]), False)}</div>"
        elif slide.get("code"):
            side = f"<div class='col' style='flex:.62'>{_bullets(bullets[:3], len(bullets[:3]), False)}</div>" if bullets else ""
            body = f"<div class='col'>{_code(slide['code'], not bullets)}</div>{side}"
        elif kind == "analogy":
            ic = icon_for(f"{slide.get('heading')} {' '.join(bullets)}", ctx.get("index", 0))
            body = (f"<div class='col analogy' style='flex:.45'>{icon(ic, '220px', sw=1.2)}</div>"
                    f"<div class='col'>{_bullets(bullets, step, False)}</div>")
        else:
            body = f"<div class='col'>{_bullets(bullets, step, kind == 'steps')}</div>" if bullets else ""
        inner = (f"<div class='eyebrow'>{icon(icon_for(kind, 0), '22px')} {eyebrow}</div><h1>{head}</h1>"
                 f"<div class='body'>{body}</div>")
    pct = 100 * (ctx.get("index", 0) + 1) / max(1, ctx.get("total", 1))
    foot = "" if over else (f"<div class='foot'><span>{esc(k.brand.website or k.brand.name)}</span>"
                            f"<span>{ctx.get('index', 0) + 1}/{ctx.get('total', 1)}</span></div><div class='prog' style='width:{pct:.1f}%'></div>")
    body = f"{bg}{_top(k, ctx)}<div class='main'>{inner}</div>{foot}"
    return k.document(body, CSS, title="lesson")


def code_card_html(k: Kit, code: dict) -> str:
    """A vertical code card for Shorts (the captions sit over its lower part)."""
    css = CSS + (".wrap{position:absolute;left:6%;right:6%;top:22%;}.code pre{font-size:30px}")
    lines = code["text"].splitlines()[:10]
    body = (f"<div class='bg'></div><div class='grid'></div><div class='wrap'>"
            f"{_code({'language': code.get('language'), 'text': chr(10).join(lines)}, True)}</div>")
    return k.document(body, css, title="code")
