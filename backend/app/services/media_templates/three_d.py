"""3D scenes rendered with three.js (vendored, r180) in headless Chromium, frame-accurate via window.__seek(t).

Scenes: logo (extruded brand name + logo tile, studio lighting, reflections), cards (floating devices/cards
showing a screenshot, AI image or a generic product UI), neural (network + data pulses), globe (dotted
globe with arcs), particles (flowing data streams), model (turntable of an uploaded .glb/.gltf).

The canvas renders at `quality` x the output size (stills 1.0, clips ~0.6) and the browser upscales it,
while the HTML text overlay stays crisp at full resolution. If WebGL can't start, the page reports
window.__glError and the caller falls back to a 2D template.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache

from app.services.media_templates.base import ASSETS, esc, plain
from app.services.media_templates.kit import Kit, fit_size, rich, words

THREE_FILE = ASSETS / "vendor" / "three-0.180.0.bundle.min.js"
SCENES = {
    "logo": ("3D logo", "Your brand name extruded in 3D with studio lighting and reflections."),
    "cards": ("Floating devices", "Floating phone and cards showing your screenshot, an AI image or a product UI."),
    "neural": ("Neural network", "An abstract AI network with pulses of data in brand colours."),
    "globe": ("Data globe", "A dotted globe with glowing connection arcs."),
    "particles": ("Data streams", "Flowing particle streams, good as a video background."),
    "model": ("3D model turntable", "Your uploaded .glb/.gltf model on a turntable with studio lighting."),
}
ASSET_HOST = "http://assets.local"  # served by page.route() in media_render; nothing loads from the network


@lru_cache(maxsize=1)
def three_js() -> str:
    return THREE_FILE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def font_b64() -> str:
    p = ASSETS / "fonts" / "plus-jakarta-sans-latin-800-normal.woff"
    return base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def _js(v) -> str:
    return json.dumps(v, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


SCENE_JS = r"""
(function(){
const T=window.THREE, CFG=window.__CFG, W=CFG.w, H=CFG.h, Q=CFG.q;
let seed=CFG.seed>>>0; function rnd(){ seed=(seed*1664525+1013904223)>>>0; return seed/4294967296; }
const ease=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2, clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const C=CFG.colors, col=h=>new T.Color(h);
let renderer, scene, camera, update=()=>{};
function b64ToBuf(b){const s=atob(b),u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);return u.buffer;}
function loadImg(src){return new Promise((res,rej)=>{if(!src){res(null);return;} const i=new Image(); i.onload=()=>res(i); i.onerror=()=>rej(new Error('image failed')); i.src=src;});}
function texFrom(img){const t=new T.Texture(img); t.colorSpace=T.SRGBColorSpace; t.anisotropy=8; t.needsUpdate=true; return t;}
function glowTex(color){const c=document.createElement('canvas');c.width=c.height=128;const g=c.getContext('2d');const r=g.createRadialGradient(64,64,0,64,64,64);
  r.addColorStop(0,color);r.addColorStop(.25,color+'aa');r.addColorStop(1,color+'00');g.fillStyle=r;g.fillRect(0,0,128,128);const t=new T.CanvasTexture(c);t.colorSpace=T.SRGBColorSpace;return t;}
function shadowTex(){const c=document.createElement('canvas');c.width=c.height=256;const g=c.getContext('2d');const r=g.createRadialGradient(128,128,0,128,128,128);
  r.addColorStop(0,'rgba(0,0,0,.55)');r.addColorStop(1,'rgba(0,0,0,0)');g.fillStyle=r;g.fillRect(0,0,256,256);return new T.CanvasTexture(c);}
function roundedImageTex(img, w, h, radius, pad){const c=document.createElement('canvas');c.width=w;c.height=h;const g=c.getContext('2d');
  g.fillStyle='#ffffff'; g.beginPath(); g.roundRect(0,0,w,h,radius); g.fill(); if(img){g.save();g.beginPath();g.roundRect(pad,pad,w-2*pad,h-2*pad,radius*.7);g.clip();
  const s=Math.max((w-2*pad)/img.width,(h-2*pad)/img.height);g.drawImage(img,(w-img.width*s)/2,(h-img.height*s)/2,img.width*s,img.height*s);g.restore();}
  const t=new T.CanvasTexture(c); t.colorSpace=T.SRGBColorSpace; t.anisotropy=8; return t;}

function base(fov){
  const canvas=document.getElementById('gl');
  canvas.width=Math.round(W*Q); canvas.height=Math.round(H*Q);
  renderer=new T.WebGLRenderer({canvas,antialias:true,alpha:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
  if(!renderer.getContext()) throw new Error('WebGL unavailable');
  renderer.setPixelRatio(1); renderer.setSize(canvas.width,canvas.height,false);
  renderer.toneMapping=T.ACESFilmicToneMapping; renderer.toneMappingExposure=1.05; renderer.outputColorSpace=T.SRGBColorSpace;
  renderer.setClearColor(0x000000,0);
  scene=new T.Scene();
  const pm=new T.PMREMGenerator(renderer); scene.environment=pm.fromScene(new T.RoomEnvironment(),.04).texture;
  camera=new T.PerspectiveCamera(fov||32,W/H,.1,200);
  if(CFG.lift){ camera.setViewOffset(W,H,0,Math.round(H*CFG.lift),W,H); }  // subject sits above the text overlay
}
// distance at which a w x h box fills the frame (with margin), for any aspect ratio
function distFor(w,h,margin){ const v=Math.tan(camera.fov*Math.PI/360), hz=v*camera.aspect; return Math.max(w/2/hz,h/2/v)*(margin||1.2); }
function fitDist(d){ return distFor(d,d*.8,1.0); }

const SC={};
SC.logo=async function(){
  base(30);
  const group=new T.Group(); scene.add(group);
  const font=new T.TTFLoader().parse(b64ToBuf(CFG.font)); const f=new T.Font(font);
  const name=(CFG.text3d||'Brand').slice(0,22);
  const geo=new T.TextGeometry(name,{font:f,size:1,depth:.32,curveSegments:10,bevelEnabled:true,bevelThickness:.045,bevelSize:.028,bevelSegments:5});
  geo.computeBoundingBox(); const bb=geo.boundingBox; const tw=bb.max.x-bb.min.x, th=bb.max.y-bb.min.y;
  geo.translate(-bb.min.x,-th/2-bb.min.y,-.16);
  const face=new T.MeshPhysicalMaterial({color:col(CFG.dark3d?'#ffffff':C.dark),metalness:.15,roughness:.22,clearcoat:1,clearcoatRoughness:.08});
  const side=new T.MeshPhysicalMaterial({color:col(C.primary),metalness:.55,roughness:.28,clearcoat:.6});
  const text=new T.Mesh(geo,[face,side]); group.add(text);
  // logo tile
  const img=await loadImg(CFG.logo);
  const tile=new T.Mesh(new T.RoundedBoxGeometry(1.7,1.7,.34,8,.3),new T.MeshPhysicalMaterial({color:0xffffff,metalness:0,roughness:.35,clearcoat:1,clearcoatRoughness:.15}));
  let tileW=0;
  if(img){ const plane=new T.Mesh(new T.PlaneGeometry(1.42,1.42),new T.MeshBasicMaterial({map:texFrom(img),transparent:true,toneMapped:false}));
    plane.position.z=.175; tile.add(plane); group.add(tile); tileW=1.7+.45; }
  const total=tileW+tw; const x0=-total/2;
  tile.position.set(x0+.85,0,0); text.position.x=x0+tileW;
  const sh=new T.Mesh(new T.PlaneGeometry(total*1.3,4.2),new T.MeshBasicMaterial({map:shadowTex(),transparent:true,depthWrite:false}));
  sh.rotation.x=-Math.PI/2; sh.position.y=-1.15; scene.add(sh);
  const key=new T.DirectionalLight(0xffffff,2.2); key.position.set(3,4,6); scene.add(key);
  const rim1=new T.PointLight(col(C.accent),40,30); scene.add(rim1);
  const rim2=new T.PointLight(col(C.primary_light),30,30); rim2.position.set(-5,-2,3); scene.add(rim2);
  const span=Math.max(total,3.2), dist=distFor(span,2.6,1.22);
  camera.position.set(0,1.9,dist); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){
    const intro=ease(clamp(t/Math.min(2.4,D*.45),0,1));
    const loopy=Math.sin(2*Math.PI*t/D);
    group.rotation.y=-0.3+(-0.6*(1-intro))+0.16*loopy*(CFG.mode==='loop'?1:intro);
    group.rotation.x=.06*Math.sin(2*Math.PI*t/D+1);
    group.position.y=.08*Math.sin(2*Math.PI*t/D);
    group.scale.setScalar(.82+.18*intro);
    rim1.position.set(-6+12*((t/D)%1),2.5,4);   // light sweep across the faces
    camera.position.z=dist*(1.1-.1*intro);
  };
};

SC.cards=async function(){
  base(30);
  const imgs=await Promise.all((CFG.screens||[]).map(s=>loadImg(s).catch(()=>null)));
  const group=new T.Group(); scene.add(group);
  const mk=(w,h,img,bezel)=>{ const g=new T.Group();
    const body=new T.Mesh(new T.RoundedBoxGeometry(w,h,.12,8,Math.min(w,h)*.08),new T.MeshPhysicalMaterial({color:col(bezel?'#0b1220':'#ffffff'),metalness:bezel?.6:0,roughness:bezel?.3:.4,clearcoat:1,clearcoatRoughness:.1}));
    g.add(body);
    const sw=bezel?w*.92:w*.94, shh=bezel?h*.955:h*.94;
    const tex=roundedImageTex(img, 512, Math.round(512*shh/sw), bezel?40:28, bezel?0:6);
    const screen=new T.Mesh(new T.PlaneGeometry(sw,shh),new T.MeshBasicMaterial({map:tex,toneMapped:false}));
    screen.position.z=.065; g.add(screen); return g; };
  const phone=mk(1.55,3.2,imgs[0],true), cardL=mk(2.2,1.45,imgs[1]||imgs[0],false), cardR=mk(2.0,1.3,imgs[2]||imgs[0],false);
  phone.position.set(0,0,.4); cardL.position.set(-1.85,.85,-.5); cardR.position.set(1.85,-.7,-.3);
  cardL.rotation.set(.05,.38,-.04); cardR.rotation.set(-.04,-.36,.05);
  group.add(phone,cardL,cardR);
  const glow=new T.Sprite(new T.SpriteMaterial({map:glowTex(C.accent),transparent:true,opacity:.55,depthWrite:false,blending:T.AdditiveBlending}));
  glow.scale.set(9,9,1); glow.position.z=-2; scene.add(glow);
  const key=new T.DirectionalLight(0xffffff,1.8); key.position.set(2,3,5); scene.add(key);
  const cd=distFor(6.2,4.4,.92); camera.position.set(0,0,cd); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){ const a=2*Math.PI*t/D, intro=ease(clamp(t/Math.min(2,D*.4),0,1));
    group.rotation.y=-.25+.2*Math.sin(a)+(1-intro)*.5; group.rotation.x=.05*Math.sin(a*2);
    phone.position.y=.12*Math.sin(a); cardL.position.y=.85+.16*Math.sin(a+1.3); cardR.position.y=-.7+.14*Math.sin(a+2.4);
    group.position.y=(1-intro)*-1.2; group.scale.setScalar(.9+.1*intro); };
};

function nodesNet(){
  const group=new T.Group(); scene.add(group);
  const layers=[5,8,8,6,3], pos=[];
  layers.forEach((n,li)=>{ for(let i=0;i<n;i++){ const y=(i-(n-1)/2)*.62, z=(rnd()-.5)*1.2; pos.push({v:new T.Vector3((li-(layers.length-1)/2)*1.55,y,z),l:li}); } });
  const nodeGeo=new T.SphereGeometry(.085,24,16), nodeMat=new T.MeshPhysicalMaterial({color:col(C.accent),emissive:col(C.accent),emissiveIntensity:1.6,roughness:.3,metalness:.1});
  const inst=new T.InstancedMesh(nodeGeo,nodeMat,pos.length); const m=new T.Matrix4(); pos.forEach((p,i)=>{m.makeTranslation(p.v.x,p.v.y,p.v.z); inst.setMatrixAt(i,m);}); group.add(inst);
  const glowM=new T.SpriteMaterial({map:glowTex(C.accent),transparent:true,opacity:.7,depthWrite:false,blending:T.AdditiveBlending});
  pos.forEach(p=>{const s=new T.Sprite(glowM); s.position.copy(p.v); s.scale.set(.38,.38,1); group.add(s);});
  const edges=[]; for(let a=0;a<pos.length;a++) for(let b=0;b<pos.length;b++) if(pos[b].l===pos[a].l+1 && rnd()<.55) edges.push([pos[a].v,pos[b].v]);
  const lg=new T.BufferGeometry(); const arr=new Float32Array(edges.length*6); edges.forEach((e,i)=>{arr.set([e[0].x,e[0].y,e[0].z,e[1].x,e[1].y,e[1].z],i*6);});
  lg.setAttribute('position',new T.BufferAttribute(arr,3));
  group.add(new T.LineSegments(lg,new T.LineBasicMaterial({color:col(C.primary_light),transparent:true,opacity:.28,blending:T.AdditiveBlending,depthWrite:false})));
  const pulses=[], pm=new T.SpriteMaterial({map:glowTex('#ffffff'),transparent:true,depthWrite:false,blending:T.AdditiveBlending});
  for(let i=0;i<70;i++){ const s=new T.Sprite(pm); s.scale.set(.22,.22,1); group.add(s); pulses.push({s,e:edges[Math.floor(rnd()*edges.length)],o:rnd(),k:1+Math.floor(rnd()*2)}); }
  return {group,pulses};
}
function dust(n,spread,color,size){ const g=new T.BufferGeometry(), a=new Float32Array(n*3); for(let i=0;i<n;i++){a[i*3]=(rnd()-.5)*spread;a[i*3+1]=(rnd()-.5)*spread*.6;a[i*3+2]=(rnd()-.5)*spread*.5-2;}
  g.setAttribute('position',new T.BufferAttribute(a,3)); const p=new T.Points(g,new T.PointsMaterial({color:col(color),size:size||.03,transparent:true,opacity:.7,depthWrite:false,blending:T.AdditiveBlending}));
  scene.add(p); return p; }

SC.neural=async function(){
  base(34);
  const {group,pulses}=nodesNet(); const d=dust(900,18,C.accent2,.035);
  const nd=distFor(6.8,4.6,1.1); camera.position.set(0,0,nd); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){ const a=2*Math.PI*t/D;
    group.rotation.y=.55*Math.sin(a)+.2; group.rotation.x=.12*Math.sin(a+.7); d.rotation.y=a*.25/(2*Math.PI);
    pulses.forEach(p=>{ const u=((t/D)*p.k*3+p.o)%1; p.s.position.lerpVectors(p.e[0],p.e[1],u); p.s.material.opacity=Math.sin(Math.PI*u); });
    camera.position.z=nd-.5*Math.sin(a); };
};

SC.globe=async function(){
  base(32);
  const group=new T.Group(); scene.add(group);
  const N=2600, R=2, a=new Float32Array(N*3);
  for(let i=0;i<N;i++){ const y=1-(i/(N-1))*2, r=Math.sqrt(1-y*y), th=Math.PI*(3-Math.sqrt(5))*i; a[i*3]=Math.cos(th)*r*R; a[i*3+1]=y*R; a[i*3+2]=Math.sin(th)*r*R; }
  const g=new T.BufferGeometry(); g.setAttribute('position',new T.BufferAttribute(a,3));
  group.add(new T.Points(g,new T.PointsMaterial({color:col(C.accent),size:.035,transparent:true,opacity:.9,depthWrite:false})));
  group.add(new T.Mesh(new T.SphereGeometry(R*.985,64,48),new T.MeshPhysicalMaterial({color:col(C.dark),roughness:.6,metalness:.2,transparent:true,opacity:.85})));
  const atm=new T.Sprite(new T.SpriteMaterial({map:glowTex(C.primary_light),transparent:true,opacity:.65,depthWrite:false,blending:T.AdditiveBlending})); atm.scale.set(7.2,7.2,1); atm.position.z=-.5; scene.add(atm);
  const arcs=[]; const sp=()=>{const u=rnd()*2-1,th=rnd()*Math.PI*2,r=Math.sqrt(1-u*u);return new T.Vector3(Math.cos(th)*r*R,u*R,Math.sin(th)*r*R);};
  for(let i=0;i<14;i++){ const p0=sp(),p1=sp(); if(p0.distanceTo(p1)<1.2) continue; const mid=p0.clone().add(p1).multiplyScalar(.5).normalize().multiplyScalar(R*(1.25+p0.distanceTo(p1)*.18));
    const curve=new T.QuadraticBezierCurve3(p0,mid,p1); const pts=curve.getPoints(80); const geo=new T.BufferGeometry().setFromPoints(pts);
    const line=new T.Line(geo,new T.LineBasicMaterial({color:col(i%2?C.accent:'#ffffff'),transparent:true,opacity:.85,blending:T.AdditiveBlending,depthWrite:false}));
    group.add(line); const head=new T.Sprite(new T.SpriteMaterial({map:glowTex('#ffffff'),transparent:true,depthWrite:false,blending:T.AdditiveBlending})); head.scale.set(.28,.28,1); group.add(head);
    arcs.push({line,head,pts,o:rnd()}); }
  const d=dust(500,20,C.accent2,.03);
  camera.position.set(0,.4,distFor(5.2,5.2,1.12)); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){ group.rotation.y=2*Math.PI*t/D*.25+.6; group.rotation.x=.25; d.rotation.y=t/D;
    arcs.forEach(A=>{ const u=((t/D)*2+A.o)%1, n=Math.floor(clamp(u*1.4,0,1)*80), s=Math.floor(clamp(u*1.4-.4,0,1)*80);
      A.line.geometry.setDrawRange(s,Math.max(0,n-s)); A.head.position.copy(A.pts[Math.min(79,n)]); A.head.material.opacity=u<.72?1:Math.max(0,1-(u-.72)*4); }); };
};

SC.particles=async function(){
  base(40);
  const streams=[], n=11;
  for(let s=0;s<n;s++){ const pts=[]; const y0=(s-(n-1)/2)*.42; for(let i=0;i<=8;i++){ pts.push(new T.Vector3(-7+i*1.75,y0+Math.sin(i*.9+s)*.6,Math.cos(i*.7+s*1.3)*1.2)); }
    const curve=new T.CatmullRomCurve3(pts); const M=160; const geo=new T.BufferGeometry(); const arr=new Float32Array(M*3); geo.setAttribute('position',new T.BufferAttribute(arr,3));
    const mat=new T.PointsMaterial({color:col(s%2?C.accent:C.primary_light),size:.085,transparent:true,opacity:.95,depthWrite:false,blending:T.AdditiveBlending});
    const p=new T.Points(geo,mat); scene.add(p);
    const line=new T.Line(new T.BufferGeometry().setFromPoints(curve.getPoints(200)),new T.LineBasicMaterial({color:col(C.primary_light),transparent:true,opacity:.18,blending:T.AdditiveBlending}));
    scene.add(line); streams.push({curve,geo,M,o:rnd(),sp:.6+rnd()*.6}); }
  const d=dust(1200,22,C.accent2,.025);
  camera.position.set(0,0,distFor(9,5,.9)); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){ streams.forEach(S=>{ const a=S.geo.attributes.position.array; for(let i=0;i<S.M;i++){ const u=(i/S.M+(t/D)*Math.round(S.sp*2)/2+S.o)%1; const v=S.curve.getPointAt(u); a[i*3]=v.x;a[i*3+1]=v.y;a[i*3+2]=v.z; } S.geo.attributes.position.needsUpdate=true; });
    d.rotation.z=t/D*.4; camera.position.x=.6*Math.sin(2*Math.PI*t/D); camera.lookAt(0,0,0); };
};

SC.model=async function(){
  base(30);
  const loader=new T.GLTFLoader(); loader.setMeshoptDecoder(T.MeshoptDecoder);
  const gltf=await new Promise((res,rej)=>loader.load(CFG.model,res,undefined,e=>rej(new Error('model failed to load: '+(e&&e.message||e)))));
  const root=gltf.scene; const box=new T.Box3().setFromObject(root); const size=box.getSize(new T.Vector3()), ctr=box.getCenter(new T.Vector3());
  const k=2.6/Math.max(size.x,size.y,size.z||1); root.scale.setScalar(k); root.position.sub(ctr.multiplyScalar(k));
  const pivot=new T.Group(); pivot.add(root); scene.add(pivot);
  const h=size.y*k;
  const sh=new T.Mesh(new T.PlaneGeometry(4.2,4.2),new T.MeshBasicMaterial({map:shadowTex(),transparent:true,depthWrite:false}));
  const disc=new T.Mesh(new T.CylinderGeometry(1.9,1.95,.08,96),new T.MeshPhysicalMaterial({color:col(C.dark),metalness:.3,roughness:.35,clearcoat:1}));
  disc.position.y=-h/2-.045; scene.add(disc);
  sh.rotation.x=-Math.PI/2; sh.position.y=-h/2-.002; sh.renderOrder=2; scene.add(sh);
  const key=new T.DirectionalLight(0xffffff,2.4); key.position.set(3,5,4); scene.add(key);
  const rim=new T.PointLight(col(C.accent),35,20); rim.position.set(-3,2,-3); scene.add(rim);
  camera.position.set(0,h*.35+.9,distFor(4.2,h+1,1.15)); camera.lookAt(0,0,0);
  const D=CFG.duration;
  update=function(t){ pivot.rotation.y=2*Math.PI*t/D; disc.rotation.y=pivot.rotation.y; };
};

window.__before=async function(){
  const fn=SC[CFG.scene]||SC.neural; await fn();
  window.__onSeek=function(t){ update(t); renderer.render(scene,camera); };
  window.__alwaysActive=true;
  update(0); renderer.render(scene,camera);
  const px=new Uint8Array(4); const gl=renderer.getContext(); gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,px);
};
})();
"""


def scene3d(k: Kit, f: dict, photo: str | None, scene: str) -> str:
    c = k.brand.colors
    quality = float(f.get("quality") or 1.0)
    bare = bool(f.get("bare"))
    cfg = {
        "w": k.w, "h": k.h, "q": max(.3, min(1.0, quality)), "scene": scene, "duration": k.duration, "mode": k.motion,
        "seed": k.seed % 100000, "font": font_b64() if scene == "logo" else "",
        "colors": {"primary": c["primary"], "primary_light": c["primary_light"], "accent": c["secondary_light"], "accent2": c["primary_light"],
                   "dark": c["dark"], "secondary": c["secondary"]},
        "text3d": str(f.get("text3d") or k.brand.name)[:22], "logo": k.brand.logo_uri, "dark3d": True,
        "screens": [s for s in (f.get("screens") or []) if isinstance(s, str) and s.startswith("data:image/")][:3],
        "model": f.get("model_url") or "",
        "lift": 0.0,
    }
    head = str(f.get("headline") or "")
    body = str(f.get("body") or "")
    eyebrow = str(f.get("eyebrow") or "")
    is_motion = k.motion != "still"
    css = (".gl{position:absolute;inset:0;width:100%;height:100%;z-index:2}"
           ".safe{z-index:5}.main{justify-content:flex-end}.ov{max-width:100%}.ov .h1{max-width:16em}.ov .lead{max-width:34em}"
           f".gltop .main{{justify-content:flex-start}}")
    text_pos_top = scene in ("logo",)
    overlay = ""
    if not bare and head and not text_pos_top:
        cfg["lift"] = .14 if k.h >= k.w else .06
    if bare and k.h > k.w * 1.3:
        cfg["lift"] = -.2  # video background: subject sits low, captions get the upper half
    if not bare and (head or body or eyebrow):
        size = fit_size(head, 84 * k.boost, soft=28, floor=44)
        overlay = ((f"<span class='eyebrow' data-anim='rise' data-delay='.6'><span class='dot'></span>{esc(eyebrow)}</span><div class='sp3'></div>" if eyebrow else "")
                   + (f"<h1 class='h1' data-fit data-anim='words' data-delay='.8' style='font-size:{k.px(size)}'>{words(head) if is_motion else rich(head)}</h1>" if head else "")
                   + (f"<div class='sp2'></div><p class='lead' data-fit data-anim='rise' data-delay='1.2' style='font-size:{k.px(30)}'>{rich(body)}</p>" if body else ""))
    scrim = ("<div class='layer' style='z-index:3;background:linear-gradient(180deg,transparent 45%,rgba(var(--dark-rgb),.85) 100%)'></div>"
             if overlay and not text_pos_top else "")
    chrome = "" if bare else (f"<div class='head' data-anim='fade' data-delay='.3'>{k.lockup()}</div>")
    foot = "" if bare else f"<div class='foot' data-anim='fade' data-delay='1.4'><span class='rule'></span>{k.site()}</div>"
    if text_pos_top and overlay:
        css += ".main{justify-content:flex-end;text-align:center;align-items:center}.main .eyebrow{align-self:center}.ov{margin:0 auto}"
    body_html = (f"<div class='canvas'>{k.bg(None, variant='spot' if scene in ('logo', 'model', 'cards') else 'aurora')}"
                 f"<canvas id='gl' class='gl'></canvas>{scrim}"
                 f"<div class='safe'>{chrome}<div class='main' data-fitbox><div class='ov'>{overlay}</div></div>{foot}</div></div>")
    script = f"window.__CFG={_js(cfg)};</script><script>{three_js()}</script><script>{SCENE_JS}"
    return k.document(body_html, css, plain(head) or SCENES[scene][0], script=script)


def _fn(scene: str):
    def fn(k: Kit, f: dict, photo: str | None) -> str:
        return scene3d(k, f, photo, scene)
    fn.__name__ = f"scene3d_{scene}"
    return fn


def specs(T) -> list:
    fields = (("eyebrow", "Small label", "text"), ("headline", "Headline (optional)", "textarea"), ("body", "Supporting line", "textarea"))
    samples = {
        "logo": {"body": "AI for people teams"},
        "cards": {"eyebrow": "HRP HRMS", "headline": "Your whole HR stack, *one* app"},
        "neural": {"eyebrow": "AI agents", "headline": "Agents that *understand* your policies"},
        "globe": {"eyebrow": "Global payroll", "headline": "Pay teams in *40+* countries"},
        "particles": {"headline": "Data that *flows*"},
        "model": {"headline": "Built to last"},
    }
    return [T(f"scene3d_{key}", label, desc, ("image", "gif", "animation", "video"), fields + ((("text3d", "3D text", "text"),) if key == "logo" else ()),
              _fn(key), photo=False, animated=True, group="3D", themes=("dark",), sample=samples.get(key), three_d=True)
            for key, (label, desc) in SCENES.items()]


def screen_html(brand, width: int, height: int, variant: int = 0) -> str:
    """A generic product UI screen (for the floating devices when no screenshot is given)."""
    k = Kit(width, height, brand, theme="light")
    titles = ["Overview", "Payroll", "Team"]
    kpis = [[("Active employees", "1,284"), ("On leave today", "37"), ("Open roles", "12")],
            [("Payroll run", "₹1.2 Cr"), ("Payslips sent", "1,284"), ("Exceptions", "3")],
            [("Engagement", "86%"), ("Check-ins", "412"), ("New joiners", "18")]][variant % 3]
    return k.document(f"<div class='canvas'>{k.ui_mock(titles[variant % 3], kpis, compact=height > width)}</div>", "", "screen")


@lru_cache(maxsize=1)
def demo_glb() -> bytes:
    """A small procedural model (torus knot, brand blue) for previews and tests - generated, not downloaded."""
    import math
    import struct

    p_, q_, seg, rs, r = 2, 3, 160, 24, .32
    pos: list[float] = []
    nor: list[float] = []
    idx: list[int] = []

    def curve(t: float) -> tuple[float, float, float]:
        return ((1 + .4 * math.cos(q_ * t)) * math.cos(p_ * t), (1 + .4 * math.cos(q_ * t)) * math.sin(p_ * t), .4 * math.sin(q_ * t))

    for i in range(seg + 1):
        t = 2 * math.pi * i / seg
        a, b = curve(t), curve(t + .01)
        tv = [b[k] - a[k] for k in range(3)]
        ln = math.sqrt(sum(v * v for v in tv))
        tv = [v / ln for v in tv]
        nv = [a[k] + b[k] for k in range(3)]
        bv = [tv[1] * nv[2] - tv[2] * nv[1], tv[2] * nv[0] - tv[0] * nv[2], tv[0] * nv[1] - tv[1] * nv[0]]
        ln = math.sqrt(sum(v * v for v in bv))
        bv = [v / ln for v in bv]
        nv = [bv[1] * tv[2] - bv[2] * tv[1], bv[2] * tv[0] - bv[0] * tv[2], bv[0] * tv[1] - bv[1] * tv[0]]
        for j in range(rs + 1):
            v = 2 * math.pi * j / rs
            cx, cy = -r * math.cos(v), r * math.sin(v)
            n = [cx * nv[k] + cy * bv[k] for k in range(3)]
            ln = math.sqrt(sum(x * x for x in n))
            pos += [a[k] + n[k] for k in range(3)]
            nor += [x / ln for x in n]
    for i in range(seg):
        for j in range(rs):
            a0, b0 = i * (rs + 1) + j, (i + 1) * (rs + 1) + j
            idx += [a0, b0, a0 + 1, b0, b0 + 1, a0 + 1]
    pb = struct.pack(f"<{len(pos)}f", *pos)
    nb = struct.pack(f"<{len(nor)}f", *nor)
    ib = struct.pack(f"<{len(idx)}I", *idx)
    binb = pb + nb + ib
    n = len(pos) // 3
    doc = {"asset": {"version": "2.0", "generator": "hrp-dm"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.06, 0.55, 0.87, 1], "metallicFactor": 0.6, "roughnessFactor": 0.25}}],
           "buffers": [{"byteLength": len(binb)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pb), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pb), "byteLength": len(nb), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pb) + len(nb), "byteLength": len(ib), "target": 34963}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
                          "min": [min(pos[i::3]) for i in range(3)], "max": [max(pos[i::3]) for i in range(3)]},
                         {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]}
    js = json.dumps(doc).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    binb += b"\0" * ((4 - len(binb) % 4) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binb)) + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(binb), 0x004E4942) + binb)
