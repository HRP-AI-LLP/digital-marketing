"""Video templates: caption overlay (transparent PNG over a scene), the full-length short-video overlay
(kinetic captions + word-by-word subtitles synced to the voice-over, progress bar, logo), and the end card.
"""

from __future__ import annotations

import json

from app.services.media_templates.base import esc, plain
from app.services.media_templates.kit import Kit, fit_size, rich, words


def video_caption_overlay(k: Kit, f: dict, photo: str | None = None) -> str:
    caption = str(f.get("caption") or "")
    title = str(f.get("title") or "")
    index = int(f.get("index") or 0)
    total = int(f.get("total") or 0)
    cs = fit_size(caption, 58, soft=50, floor=36)
    css = (f".cap{{position:absolute;left:var(--pl);right:calc(var(--pr) + {k.px(40) if k.tall else '0px'});bottom:calc(var(--pb) + {k.px(40)});display:flex;justify-content:safe center}}"
           f".capbox{{max-height:calc(var(--h)*0.3);overflow:hidden;background:rgba(var(--dark-rgb),.78);border:1px solid rgba(255,255,255,.14);"
           f"-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-radius:{k.px(28)};padding:{k.px(24)} {k.px(34)};"
           f"box-shadow:0 {k.px(16)} {k.px(40)} rgba(0,0,0,.35)}}"
           f".captxt{{font-family:var(--font-head);font-weight:800;line-height:1.18;letter-spacing:-.015em;color:#fff;text-align:center;text-wrap:balance}}"
           f".captxt .hl{{background:none;color:var(--secondary-light);-webkit-text-fill-color:var(--secondary-light)}}"
           f".hd{{position:absolute;left:var(--pl);right:var(--pr);top:var(--pt);display:flex;align-items:center;gap:{k.px(18)}}}"
           f".ttl{{font-weight:700;font-size:{k.px(28)};color:#fff;background:rgba(var(--dark-rgb),.66);border-radius:999px;padding:{k.px(10)} {k.px(22)};"
           f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}"
           f".pbar{{position:absolute;left:var(--pl);right:var(--pr);top:calc(var(--pt) - {k.px(34)});display:flex;gap:{k.px(8)}}}"
           f".pbar i{{flex:1;height:{k.px(6)};border-radius:9px;background:rgba(255,255,255,.32)}}.pbar i.on{{background:#fff}}")
    bar = ""
    if total > 1:
        bar = "<div class='pbar'>" + "".join(f"<i class='{'on' if i < index else ''}'></i>" for i in range(min(total, 12))) + "</div>"
    head = f"<div class='hd'>{k.logo(72)}" + (f"<span class='ttl'>{esc(title)}</span>" if title else "") + "</div>"
    cap = (f"<div class='cap'><div class='capbox' data-fitbox><div class='captxt' data-fit style='font-size:{k.px(cs)}'>{rich(caption)}</div></div></div>"
           if caption else "")
    return k.document(f"<div class='canvas'>{bar}{head}{cap}</div>", css, plain(caption))


def _js(v) -> str:
    return json.dumps(v, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def short_overlay(k: Kit, f: dict, photo: str | None = None) -> str:
    """Transparent overlay for a whole short. fields:
      scenes: [{start, end, caption, words: [[start, end, text], ...], kind: "hook"|"scene"}]
      total: seconds;  style: "kinetic" (no footage: big type) | "caption" (footage: captions low in the frame)
    Word timings are absolute (seconds from the start of the video)."""
    scenes = f.get("scenes") or []
    total = float(f.get("total") or k.duration)
    styles = [s.get("style") or "caption" for s in scenes]
    blocks = []
    for i, s in enumerate(scenes):
        cap = str(s.get("caption") or "")
        big = s.get("style") == "kinetic" or s.get("kind") == "hook"
        size = fit_size(cap, (132 if s.get("kind") == "hook" else 118) if big else 72, soft=22 if big else 36, floor=64 if big else 48)
        cls = "kin" if big else "lowcap"
        blocks.append(f"<div class='blk {cls}' id='b{i}' data-fitbox><div class='{'display' if big else 'h2'} ctext' data-fit style='font-size:{k.px(size)}'>"
                      f"{words(cap)}</div></div>")
    data = [{"start": float(s.get("start") or 0), "end": float(s.get("end") or 0),
             "words": [[float(a), float(b), str(t)] for a, b, t in (s.get("words") or [])][:400]} for s in scenes]
    css = (f".blk{{position:absolute;left:var(--pl);right:calc(var(--pr) + {k.px(60)});display:flex;flex-direction:column;opacity:0}}"
           f".kin{{top:28%;bottom:44%;justify-content:safe center}}"
           f".kin .ctext{{color:#fff;text-shadow:0 {k.px(4)} {k.px(30)} rgba(2,12,27,.45)}}"
           f".lowcap{{top:30%;bottom:46%;justify-content:safe center}}"
           f".lowcap .ctext{{color:#fff;font-weight:800;text-shadow:0 {k.px(3)} {k.px(18)} rgba(0,0,0,.55),0 0 {k.px(2)} rgba(0,0,0,.6)}}"
           f".ctext .hl{{background:none;-webkit-text-fill-color:var(--secondary-light);color:var(--secondary-light)}}"
           f".subs{{position:absolute;left:var(--pl);right:calc(var(--pr) + {k.px(70)});top:61%;height:{k.px(170)};display:flex;align-items:center;justify-content:center;text-align:center}}"
           f".sub{{font-family:var(--font-head);font-weight:800;font-size:{k.px(50)};line-height:1.18;letter-spacing:-.01em;color:#fff;"
           f"text-shadow:0 {k.px(3)} {k.px(14)} rgba(0,0,0,.6),0 0 {k.px(3)} rgba(0,0,0,.7);text-wrap:balance}}"
           f".sub span{{display:inline-block;padding:0 {k.px(6)};border-radius:{k.px(10)};margin:0 {k.px(1)};transition:none}}"
           f".sub span.on{{color:var(--dark);background:var(--secondary-light);text-shadow:none;transform:scale(1.06)}}"
           f".sub span.past{{color:#fff}}.sub span.future{{color:rgba(255,255,255,.62)}}"
           f".brand{{position:absolute;left:var(--pl);top:calc(var(--pt) - {k.px(20)});display:flex;align-items:center;gap:{k.px(14)}}}"
           f".brand .bname{{color:#fff;text-shadow:0 2px 10px rgba(0,0,0,.4)}}"
           f".ptrack{{position:absolute;left:0;right:0;top:0;height:{k.px(8)};background:rgba(255,255,255,.18)}}"
           f".pfill{{height:100%;width:0;background:linear-gradient(90deg,var(--secondary-light),var(--primary-light))}}")
    script = (
        "var SC=" + _js(data) + ",TOTAL=" + f"{total:.3f}" + ";"
        r"""
var E=function(t){return t>=1?1:1-Math.pow(2,-10*t);};
var subs=document.getElementById('subs'), cur=-1, lastKey='', changed=true;
function group(ws,t){ // chunks of up to 4 words / 26 chars that stay on screen together
  var g=[],c=[],len=0; ws.forEach(function(w){ if(c.length&&(c.length>=4||len+w[2].length>26)){g.push(c);c=[];len=0;} c.push(w); len+=w[2].length+1; });
  if(c.length) g.push(c);
  for(var i=0;i<g.length;i++){ var a=g[i][0][0], b=(i+1<g.length?g[i+1][0][0]:g[i][g[i].length-1][1]+.5); if(t>=a-.05&&t<b) return g[i]; }
  return null;
}
window.__onSeek=function(t){
  SC.forEach(function(s,i){
    var el=document.getElementById('b'+i); if(!el) return;
    var inT=Math.min(1,Math.max(0,(t-s.start-.05)/.55)), outT=Math.min(1,Math.max(0,(t-(s.end-.35))/.3));
    var vis=t>=s.start-.01&&t<s.end; el.style.opacity=vis?(1-outT):0;
    el.style.transform='translateY('+((1-E(inT))*30-outT*20)+'px)'; el.style.filter=outT>0?'blur('+(outT*6).toFixed(1)+'px)':'';
    var wis=el.querySelectorAll('.wi'); wis.forEach(function(w,j){ var p=Math.min(1,Math.max(0,(t-s.start-.08-j*.06)/.7)); var e=E(p);
      w.style.transform='translateY('+((1-e)*105).toFixed(2)+'%)'; w.style.opacity=e; });
  });
  var s=null; for(var i=0;i<SC.length;i++){ if(t>=SC[i].start&&t<SC[i].end){s=SC[i];break;} }
  var g=s&&s.words.length?group(s.words,t):null, key=g?g[0][0]+':':'';
  if(g){ var html=g.map(function(w){var st=t>=w[1]?'past':(t>=w[0]?'on':'future');key+=st[0];
      return '<span class="'+st+'">'+w[2].replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];})+'</span>';}).join(' ');
    if(key!==lastKey){subs.innerHTML='<div class="sub">'+html+'</div>';} } else if(lastKey){subs.innerHTML='';}
  changed=(key!==lastKey); lastKey=key;
};
// only frames where something moves are captured; the renderer repeats the previous frame otherwise
window.__activeAtCustom=function(t){ if(changed) return true;
  for(var i=0;i<SC.length;i++){ var s=SC[i]; if((t>=s.start-.05&&t<=s.start+1.6)||(t>=s.end-.45&&t<=s.end+.05)) return true; }
  return false; };
""")
    body = (f"<div class='canvas'>"
            f"<div class='brand'>{k.logo(60)}<span class='bname'>{esc(k.brand.name)}</span></div>"
            + "".join(blocks) + "<div class='subs' id='subs'></div></div>")
    _ = styles
    return k.document(body, css, "short", script=script)


def end_card(k: Kit, f: dict, photo: str | None = None) -> str:
    """Closing card of a short: logo sting, CTA, website (opaque, animated with the shared runtime)."""
    heading = str(f.get("heading") or k.brand.name)
    body = str(f.get("body") or (k.brand.website or ""))
    cta = str(f.get("cta") or "")
    css = (".main{justify-content:safe center;align-items:center;text-align:center}.main .lead{margin:0 auto}.main .btn{align-self:center}"
           f".ring{{position:absolute;left:50%;top:50%;border-radius:50%;border:{k.px(2)} solid rgba(255,255,255,.18)}}")
    rings = "".join(f"<div class='ring' data-anim='scale' data-delay='{.1 + i * .12:.2f}' style='width:{k.px(r)};height:{k.px(r)};margin:{k.px(-r / 2)} 0 0 {k.px(-r / 2)}'></div>"
                    for i, r in enumerate((420, 640, 880)))
    inner = (f"<div data-anim='pop' data-delay='.2'>{k.logo(200)}</div><div class='sp5'></div>"
             f"<div class='h1' data-anim='rise' data-delay='.5' data-fit style='font-size:{k.px(fit_size(heading, 84, soft=22, floor=48))}'>{rich(heading)}</div>"
             + (f"<div class='sp3'></div><p class='lead' data-anim='rise' data-delay='.7' style='font-size:{k.px(36)}'>{rich(body)}</p>" if body else "")
             + (f"<div class='sp5'></div><span class='btn' data-anim='pop' data-delay='.95' style='font-size:{k.px(36)}'>{esc(cta)}</span>" if cta else ""))
    return k.document(f"<div class='canvas'>{k.bg(None, variant='spot')}{rings}<div class='safe'><div class='main' data-fitbox>{inner}</div></div></div>",
                      css, plain(heading))
