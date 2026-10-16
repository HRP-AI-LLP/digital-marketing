"""In-page JavaScript shared by every template: text fitting and the deterministic motion timeline.

Contract with media_render (every template implements it through this runtime):
  window.__ready      Promise that resolves once fonts are loaded, text is fitted and the timeline is built
                      (body[data-ready="1"] is set at the same moment).
  window.__seek(t)    puts the page in its exact state at t seconds (no real-time clocks anywhere, so frames are
                      reproducible and the output is smooth at any fps).
  window.__stillTime  the time at which the composition is complete (used for PNG stills and poster frames).
  window.__duration   total length of the timeline in seconds.
  window.__activeAt(t) false when nothing moves at t (the renderer then reuses the previous frame).

Elements opt in with attributes (written by the Python layouts):
  data-anim="rise|fade|scale|pop|wipe|wipe-up|slide-l|slide-r|blur|grow|grow-x|draw|count|words|none"
  data-order="n"      stagger slot (default: document order); data-delay="s" absolute start instead
  data-dur="s"        tween length;   data-drift="ax,ay,k"  periodic drift (px at 1080, cycles per timeline)
  data-kb="in|out|left|right"  slow Ken Burns on photos;  data-count="73.5" (+ data-prefix/-suffix/-decimals)
  data-exit="0"       keep on screen during the outro;  data-outro  only shown in the closing logo sting
Body attributes: data-dur (s), data-mode ("intro" = enter, hold, exit to the outro; "loop" = starts composed and
loops seamlessly; "still"), data-intro (s, when the first element starts), data-stagger (s), data-drift ("0" = off).
"""

FIT_JS = r"""
(function(){
  function over(b){return b.scrollHeight>b.clientHeight+1||b.scrollWidth>b.clientWidth+1;}
  function fitBox(box){
    var els=[].slice.call(box.querySelectorAll('[data-fit]'));
    if(!els.length) return;
    var sizes=els.map(function(e){return parseFloat(getComputedStyle(e).fontSize);});
    var mins=els.map(function(e,i){return parseFloat(e.dataset.min||sizes[i]*0.42);});
    var k=1,n=0;
    while(over(box)&&n<70){
      k*=0.955;n++;var any=false;
      els.forEach(function(e,i){var s=Math.max(mins[i],sizes[i]*k);if(s<parseFloat(e.style.fontSize||sizes[i])-0.01){any=true;}e.style.fontSize=s+'px';});
      if(!any)break;
    }
    els.forEach(function(e){var s=parseFloat(getComputedStyle(e).fontSize),m=0;while(e.scrollWidth>e.clientWidth+1&&m<30){s*=0.94;e.style.fontSize=s+'px';m++;}});
  }
  window.__fit=function(){document.querySelectorAll('[data-fitbox]').forEach(fitBox);};
})();
"""

RUNTIME_JS = r"""
(function(){
  var B=document.body, S=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--s'))||1;
  var E={
    linear:function(t){return t;},
    out:function(t){return 1-Math.pow(1-t,3);},
    outQuart:function(t){return 1-Math.pow(1-t,4);},
    expo:function(t){return t>=1?1:1-Math.pow(2,-10*t);},
    inOut:function(t){return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;},
    inCubic:function(t){return t*t*t;},
    back:function(t){var c1=1.45,c3=c1+1;return 1+c3*Math.pow(t-1,3)+c1*Math.pow(t-1,2);}
  };
  var PRESET={
    rise:{y:[48,0],o:[0,1],b:[6,0],e:'expo',d:.9},
    fade:{o:[0,1],e:'out',d:.8},
    scale:{s:[.9,1],o:[0,1],e:'expo',d:1},
    pop:{s:[.55,1],o:[0,1],e:'back',d:.7},
    wipe:{cx:[100,0],e:'expo',d:1},
    'wipe-up':{cy:[100,0],e:'expo',d:1},
    'slide-l':{x:[-70,0],o:[0,1],e:'expo',d:.9},
    'slide-r':{x:[70,0],o:[0,1],e:'expo',d:.9},
    blur:{b:[18,0],o:[0,1],s:[1.06,1],e:'out',d:1.1},
    grow:{sy:[0,1],e:'expo',d:1.1},
    'grow-x':{sx:[0,1],e:'expo',d:1.1},
    draw:{dash:[1,0],e:'inOut',d:1.4},
    count:{n:[0,1],e:'expo',d:1.6},
    word:{y:[105,0],o:[0,1],e:'expo',d:.85,pct:1}
  };
  function num(a,d){var v=parseFloat(a);return isNaN(v)?d:v;}
  var dur=num(B.dataset.dur,6), mode=B.dataset.mode||'still', intro=num(B.dataset.intro,.25), stag=num(B.dataset.stagger,.12);
  var driftOn=B.dataset.drift!=='0';
  var tweens=[], drifts=[], kbs=[], els=[];
  function state(el){ if(!el.__st){el.__st={x:0,y:0,s:1,sx:1,sy:1,o:1,b:0,cx:0,cy:0,dash:0,n:1,r:0}; els.push(el);} return el.__st; }
  function tween(el,start,p,d,e,rev){
    var t={el:el,start:start,d:d||p.d,e:E[e||p.e]||E.out,props:{},pct:p.pct};
    for(var k in p){ if(['e','d','pct'].indexOf(k)>=0) continue; t.props[k]=rev?[p[k][1],p[k][0]]:p[k]; }
    state(el); tweens.push(t); return t;
  }
  var anims=[].slice.call(document.querySelectorAll('[data-anim]')).filter(function(e){return e.dataset.anim!=='none';});
  var slot=0, ends=intro;
  anims.forEach(function(el){
    var kind=el.dataset.anim, order=el.dataset.order!==undefined?num(el.dataset.order,slot):slot;
    var start=el.dataset.delay!==undefined?num(el.dataset.delay,0):intro+order*stag;
    slot=Math.max(slot,order)+1;
    if(kind==='words'){
      var ws=[].slice.call(el.querySelectorAll('.wi')), wst=num(el.dataset.wstagger,.055);
      ws.forEach(function(w,i){tween(w,start+i*wst,PRESET.word); ends=Math.max(ends,start+i*wst+PRESET.word.d);});
      el.__words=ws; return;
    }
    var p=PRESET[kind]||PRESET.rise, d=num(el.dataset.dur,p.d);
    if(kind==='draw'){ el.querySelectorAll('[data-len]').forEach(function(path){ tween(path,start,p,d); }); }
    else tween(el,start,p,d);
    ends=Math.max(ends,start+d);
  });
  document.querySelectorAll('.canvas [data-drift]').forEach(function(el){
    var a=(el.dataset.drift||'').split(',').map(parseFloat); drifts.push({el:el,ax:(a[0]||0)*S,ay:(a[1]||0)*S,k:a[2]||1,ph:num(el.dataset.phase,0)}); state(el);
  });
  var twinkles=[]; document.querySelectorAll('[data-tw]').forEach(function(el){var a=(el.dataset.tw||'').split(',').map(parseFloat); twinkles.push({el:el,k:a[0]||2,ph:a[1]||0,lo:isNaN(a[2])?.35:a[2]}); state(el);});
  var spins=[]; document.querySelectorAll('[data-spin]').forEach(function(el){spins.push({el:el,turns:parseFloat(el.dataset.spin)||1}); state(el);});
  document.querySelectorAll('[data-kb]').forEach(function(el){kbs.push({el:el,dir:el.dataset.kb}); state(el);});
  if(mode==='loop' && ends>dur*.62){ var kf=(dur*.62)/ends; tweens.forEach(function(tw){tw.start*=kf; tw.d*=kf;}); ends*=kf; }
  var still=Math.min(dur, ends+.05);
  // outro: content exits, the logo sting (data-outro) comes in
  var outroEls=[].slice.call(document.querySelectorAll('[data-outro]'));
  var outroAt=num(B.dataset.outroAt, dur-1.8);
  if(mode==='intro' && outroEls.length && outroAt>still){
    document.querySelectorAll('[data-exitgroup]').forEach(function(el){ tween(el,outroAt-.15,{o:[1,0],y:[0,-30],b:[0,8]},.5,'inCubic'); });
    outroEls.forEach(function(el,i){ tween(el,outroAt+.3+i*.12,PRESET[el.dataset.outroAnim||'scale'],null); });
  } else { outroEls.forEach(function(el){ var s=state(el); s.o=0; el.__hidden=true; }); }
  if(mode==='loop'){
    // exit near the end so the last frame flows back into the composed first frame
    var exitAt=dur-.75;
    anims.forEach(function(el){ if(el.dataset.exit==='0') return; var targets=el.__words||[el];
      targets.forEach(function(t){ tween(t,exitAt,{o:[1,0],y:[0,-18*(t.__words?0:1)]},.45,'inCubic'); }); });
  }
  var phase=mode==='loop'?still:0, loopDur=mode==='loop'?dur+still:dur;
  function local(t){ if(mode!=='loop') return t; var u=(t+phase)%dur; return u<0?u+dur:u; }
  var dashLen=new Map();
  document.querySelectorAll('[data-len]').forEach(function(p){var L=p.getTotalLength?p.getTotalLength():num(p.dataset.len,1000); dashLen.set(p,L); p.style.strokeDasharray=L+' '+L;});
  function fmt(el,v){
    var dec=parseInt(el.dataset.decimals||'0',10), target=parseFloat(el.dataset.count);
    var s=(target*v).toFixed(dec);
    if(el.dataset.group){ var parts=s.split('.'); var x=parts[0];
      if(el.dataset.group==='in'){ var last=x.slice(-3), rest=x.slice(0,-3); if(rest) last=','+last; x=rest.replace(/\B(?=(\d{2})+(?!\d))/g,',')+last; }
      else x=x.replace(/\B(?=(\d{3})+(?!\d))/g,',');
      s=parts.length>1?x+'.'+parts[1]:x; }
    return (el.dataset.prefix||'')+s+(el.dataset.suffix||'');
  }
  function apply(){
    els.forEach(function(el){var s=el.__st, tr='';
      if(s.x||s.y) tr+='translate('+(s.pct?s.x+'%':(s.x*S)+'px')+','+(s.pct?s.y+'%':(s.y*S)+'px')+') ';
      if(s.r) tr+='rotate('+s.r+'deg) ';
      if(s.s!==1) tr+='scale('+s.s+') ';
      if(s.sx!==1||s.sy!==1) tr+='scale('+s.sx+','+s.sy+') ';
      el.style.transform=tr||'none';
      el.style.opacity=el.__hidden&&s.o===0?0:s.o;
      el.style.filter=s.b>0.05?'blur('+(s.b*S).toFixed(2)+'px)':'';
      if(s.cx||s.cy) el.style.clipPath='inset('+s.cy+'% '+s.cx+'% 0 0)'; else if(el.style.clipPath) el.style.clipPath='';
      if(dashLen.has(el)) el.style.strokeDashoffset=(s.dash*dashLen.get(el)).toFixed(2);
      if(el.dataset&&el.dataset.count!==undefined) el.textContent=fmt(el,s.n);
    });
  }
  function seek(t){
    var lt=local(t);
    els.forEach(function(el){var s=el.__st; s.x=0;s.y=0;s.s=1;s.sx=1;s.sy=1;s.o=el.__hidden?0:1;s.b=0;s.cx=0;s.cy=0;s.dash=0;s.n=1;s.r=0;s.pct=false;});
    var seen=new Map();
    tweens.forEach(function(tw){
      var s=tw.el.__st, key=tw.el; var first=!seen.has(key); if(first) seen.set(key,{});
      var done=seen.get(key);
      for(var k in tw.props){
        var r=tw.props[k];
        if(lt<tw.start){ if(!done[k]){ s[k]=r[0]; } continue; }
        var p=Math.min(1,(lt-tw.start)/tw.d), e=tw.e(p); s[k]=r[0]+(r[1]-r[0])*e; done[k]=1;
      }
      if(tw.pct) s.pct=true;
    });
    // tweens that start later must not override values set by an earlier finished tween: handled by order
    if(driftOn) drifts.forEach(function(d){var a=2*Math.PI*(t/dur*d.k+d.ph); d.el.__st.x+=d.ax*Math.sin(a)/S; d.el.__st.y+=d.ay*Math.cos(a)/S;});
    if(driftOn){ twinkles.forEach(function(w){ var v=.5+.5*Math.sin(2*Math.PI*(t/dur*w.k+w.ph)); w.el.__st.o*= w.lo+(1-w.lo)*v; });
      spins.forEach(function(sp){ sp.el.__st.r=mode==='loop'?360*sp.turns*Math.sin(2*Math.PI*t/dur)/Math.PI:360*sp.turns*t/dur; }); }
    kbs.forEach(function(k){var p=mode==='loop'?(1-Math.cos(2*Math.PI*t/dur))/2:Math.min(1,t/dur), z;
      if(k.dir==='drift'){var a=2*Math.PI*t/dur; k.el.__st.s=1.06+.025*Math.sin(a); k.el.__st.x=14*Math.sin(a); k.el.__st.y=10*Math.cos(a)-10; return;}
      if(k.dir==='out'){z=1.14-.12*p;} else {z=1.02+.12*p;} k.el.__st.s=z;
      if(k.dir==='left') k.el.__st.x=-30*p; if(k.dir==='right') k.el.__st.x=30*p; });
    apply();
    if(window.__onSeek) window.__onSeek(t);
  }
  function active(t){
    if((driftOn&&(drifts.length||twinkles.length||spins.length))||kbs.length||window.__alwaysActive) return true;
    var lt=local(t); for(var i=0;i<tweens.length;i++){var tw=tweens[i]; if(lt>=tw.start-.001&&lt<=tw.start+tw.d+.04) return true;}
    return false;
  }
  window.__seek=seek; window.__activeAt=window.__activeAtCustom||active; window.__duration=dur;
  window.__stillTime=mode==='loop'?0:Math.min(dur,still);
  window.__holdUntil=mode==='intro'&&outroEls.length?outroAt:dur;
})();
"""

BOOT_JS = r"""
(function(){
  var ready=new Promise(function(res){
    var go=function(){(document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(function(){
      requestAnimationFrame(function(){
        try{ window.__fit(); }catch(e){}
        Promise.resolve(window.__before ? window.__before() : null).then(function(){
          try{ (new Function(document.getElementById('__runtime').textContent))(); }catch(e){ window.__runtimeError=String(e); }
          if(window.__after) window.__after();
          try{ window.__seek(window.__stillTime||0); }catch(e){}
          document.body.setAttribute('data-ready','1'); res(true);
        }).catch(function(e){ window.__glError=String(e&&e.message||e); document.body.setAttribute('data-ready','1'); res(false); });
      });
    });};
    if(document.readyState==='complete') go(); else window.addEventListener('load',go);
  });
  window.__ready=ready;
})();
"""
