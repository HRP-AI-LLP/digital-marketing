// Multi-dimensional 3D heatmap: weekday × hour of day on the floor, any measure as bar height, a second measure as
// colour (or the bars split into channels), and time as a week slider you can play. WebGL with instanced meshes, so
// it stays smooth however much data there is. Hover a bar for every number in that slot; switch to 2D any time.
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { Pause, Play, RotateCcw } from "lucide-react";
import { Heatmap, OTHER, TipHead, TipRow, ZoomFrame, fmtNum, fmtPct, useCameraZoom, useChartFit, useChartZoom, useTip } from "../../components/viz";
import { webglAvailable } from "./SystemMap3D";

export type CubeCell = { w: number; d: number; h: number; posts: number; engagements: number; impressions: number; clicks: number;
  engagement_rate: number; platforms: Record<string, number> };
export type Cube = { weeks: string[]; cells: CubeCell[] };
type Measure = "posts" | "engagements" | "impressions" | "clicks" | "engagement_rate";

const MEASURES: { key: Measure; name: string; fmt: (v: number) => string }[] = [
  { key: "posts", name: "Posts", fmt: fmtNum }, { key: "engagements", name: "Engagements", fmt: fmtNum },
  { key: "impressions", name: "Impressions", fmt: fmtNum }, { key: "clicks", name: "Clicks", fmt: fmtNum },
  { key: "engagement_rate", name: "Engagement rate", fmt: (v) => fmtPct(v) },
];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// one-hue sequential ramp (light → dark blue): magnitude never uses a rainbow
const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"].map((c) => new THREE.Color(c));
const rampAt = (t: number) => {
  const x = Math.max(0, Math.min(0.9999, t)) * (RAMP.length - 1);
  const i = Math.floor(x);
  return RAMP[i].clone().lerp(RAMP[i + 1], x - i);
};
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
const shortWeek = (iso: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(`${iso}T00:00:00`));

type Slot = { d: number; h: number; posts: number; engagements: number; impressions: number; clicks: number; engagement_rate: number; platforms: Record<string, number> };

function aggregate(cells: CubeCell[], week: number | null): Map<string, Slot> {
  const out = new Map<string, Slot>();
  for (const c of cells) {
    if (week != null && c.w !== week) continue;
    const k = `${c.d}-${c.h}`;
    const s = out.get(k) ?? { d: c.d, h: c.h, posts: 0, engagements: 0, impressions: 0, clicks: 0, engagement_rate: 0, platforms: {} };
    s.posts += c.posts; s.engagements += c.engagements; s.impressions += c.impressions; s.clicks += c.clicks;
    for (const [p, n] of Object.entries(c.platforms)) s.platforms[p] = (s.platforms[p] ?? 0) + n;
    out.set(k, s);
  }
  out.forEach((s) => { s.engagement_rate = s.impressions ? s.engagements / s.impressions : 0; });
  return out;
}

export default function Heatmap3D({ cube, colorOf, labelOf, height = 460 }: {
  cube: Cube; colorOf: (platform: string) => string; labelOf: (platform: string) => string; height?: number;
}) {
  const [hMeasure, setH] = useState<Measure>("engagements");
  const [cMeasure, setC] = useState<Measure | "channel">("engagement_rate");
  const [week, setWeek] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [view3d, setView3d] = useState(() => webglAvailable());
  const weeks = cube.weeks.length;

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setWeek((w) => (w == null ? 0 : w + 1 >= weeks ? null : w + 1)), 1400);
    return () => window.clearInterval(id);
  }, [playing, weeks]);
  useEffect(() => { if (playing && week == null) setPlaying(false); }, [week]); // eslint-disable-line react-hooks/exhaustive-deps

  const slots = useMemo(() => aggregate(cube.cells, week), [cube, week]);
  // scale against the whole period so bars stay comparable while the weeks play
  const scale = useMemo(() => {
    const all = aggregate(cube.cells, null);
    const perWeek = Array.from({ length: weeks }, (_, w) => aggregate(cube.cells, w));
    const max = (m: Measure, src: Map<string, Slot>[]) => Math.max(1e-9, ...src.flatMap((x) => [...x.values()].map((s) => s[m])));
    return (m: Measure) => (week == null ? max(m, [all]) : max(m, perWeek));
  }, [cube, weeks, week]);
  const platforms = useMemo(() => [...new Set(cube.cells.flatMap((c) => Object.keys(c.platforms)))], [cube]);
  const best = useMemo(() => [...slots.values()].filter((s) => s.posts > 0).sort((a, b) => b[hMeasure] - a[hMeasure])[0], [slots, hMeasure]);
  const hm = MEASURES.find((m) => m.key === hMeasure)!;
  const cm = cMeasure === "channel" ? null : MEASURES.find((m) => m.key === cMeasure)!;

  const tipFor = (s: Slot) => (
    <>
      <TipHead sub={week == null ? "All weeks in this period" : `Week of ${shortWeek(cube.weeks[week])}`}>{DAYS[s.d]} · {hh(s.h)}–{hh((s.h + 1) % 24)}</TipHead>
      {MEASURES.map((m) => <TipRow key={m.key} label={m.name} value={m.fmt(s[m.key])} />)}
      {Object.keys(s.platforms).length > 0 && (
        <div className="mt-1 border-t border-slate-100 pt-1">
          {Object.entries(s.platforms).sort((a, b) => b[1] - a[1]).map(([p, n]) => <TipRow key={p} color={colorOf(p)} label={labelOf(p)} value={`${n} post${n === 1 ? "" : "s"}`} />)}
        </div>
      )}
    </>
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Pick label="Height" value={hMeasure} onChange={(v) => setH(v as Measure)} options={MEASURES.map((m) => ({ key: m.key, name: m.name }))} />
        <Pick label="Colour" value={cMeasure} onChange={(v) => setC(v as Measure | "channel")}
          options={[...MEASURES.map((m) => ({ key: m.key, name: m.name })), { key: "channel", name: "Split by channel" }]} />
        <div className="ml-auto inline-flex rounded-lg border border-line bg-surface p-0.5">
          {[{ k: true, n: "3D" }, { k: false, n: "2D" }].map((o) => (
            <button key={o.n} type="button" disabled={o.k && !webglAvailable()} onClick={() => setView3d(o.k)}
              className={`rounded-md px-2.5 py-1 font-medium ${view3d === o.k ? "bg-primary-600 text-white" : "text-muted hover:text-ink"}`}>{o.n}</button>
          ))}
        </div>
      </div>
      {weeks > 1 && (
        <div className="mb-3 flex items-center gap-3 text-xs">
          <button type="button" onClick={() => { if (!playing && week == null) setWeek(0); setPlaying(!playing); }}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 font-medium text-ink hover:border-primary-300">
            {playing ? <Pause size={13} /> : <Play size={13} />}{playing ? "Pause" : "Play weeks"}
          </button>
          <input type="range" min={-1} max={weeks - 1} value={week ?? -1} aria-label="Week"
            onChange={(e) => { setPlaying(false); const v = Number(e.target.value); setWeek(v < 0 ? null : v); }} className="h-1.5 flex-1 accent-primary-600" />
          <span className="w-36 text-right tabular-nums text-muted">{week == null ? "All weeks" : `Week of ${shortWeek(cube.weeks[week])}`}</span>
          {week != null && <button type="button" onClick={() => { setPlaying(false); setWeek(null); }} className="text-muted hover:text-ink" title="All weeks"><RotateCcw size={13} /></button>}
        </div>
      )}
      {!cube.cells.length ? (
        <p className="rounded-xl bg-slate-50 py-14 text-center text-sm text-muted">No published posts in this period yet.</p>
      ) : view3d ? (
        <ZoomFrame mode="self" wheel={false}>
          <Scene3D slots={slots} hMeasure={hMeasure} cMeasure={cMeasure} scale={scale} colorOf={colorOf} platforms={platforms} best={best}
            bestText={best ? `${DAYS[best.d]} ${hh(best.h)} · ${hm.fmt(best[hMeasure])}` : ""} tipFor={tipFor} height={height} />
        </ZoomFrame>
      ) : (
        <Heatmap minPosts={1} format={hm.fmt} valueLabel={hm.name}
          cells={[...slots.values()].map((s) => ({ weekday: s.d, hour: s.h, value: s[hMeasure], posts: s.posts, detail: tipFor(s) }))} />
      )}
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
        <span>Bar height: <b className="text-ink">{hm.name}</b></span>
        {cm ? (
          <span className="inline-flex items-center gap-2">Colour: <b className="text-ink">{cm.name}</b>
            <span className="h-2.5 w-28 rounded-full" style={{ background: `linear-gradient(90deg, ${RAMP.map((c) => `#${c.getHexString()}`).join(",")})` }} />
            <span>low → high</span>
          </span>
        ) : (
          <span className="inline-flex flex-wrap items-center gap-3">Colour: channel
            {platforms.slice(0, 8).map((p) => <span key={p} className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: colorOf(p) }} />{labelOf(p)}</span>)}
          </span>
        )}
        {best && <span>Peak: <b className="text-ink">{DAYS[best.d]} {hh(best.h)}</b> ({hm.fmt(best[hMeasure])})</span>}
        {view3d && <span className="ml-auto">Drag to orbit · scroll to zoom · hover a bar</span>}
      </div>
    </div>
  );
}

function Pick({ label, value, options, onChange }: { label: string; value: string; options: { key: string; name: string }[]; onChange: (v: string) => void }) {
  return (
    <label className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface py-0.5 pl-2.5 pr-1">
      <span className="text-muted">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="rounded-md bg-transparent py-1 pr-1 font-medium text-ink focus:outline-none">
        {options.map((o) => <option key={o.key} value={o.key}>{o.name}</option>)}
      </select>
    </label>
  );
}

// ------------------------------------------------------------------ the WebGL scene
const GAP = 0.18, MAXH = 5.2;
const X = (h: number) => (h - 11.5) * 1;
const Z = (d: number) => (d - 3) * 1.25;

function Scene3D({ slots, hMeasure, cMeasure, scale, colorOf, platforms, best, bestText, tipFor, height }: {
  slots: Map<string, Slot>; hMeasure: Measure; cMeasure: Measure | "channel"; scale: (m: Measure) => number; colorOf: (p: string) => string;
  platforms: string[]; best?: Slot; bestText: string; tipFor: (s: Slot) => React.ReactNode; height: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const t = useTip();
  const ctl = useRef<{ update: () => void } | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cam = useRef<any>(null);
  useCameraZoom(useChartZoom(), cam);
  height = Math.round(height * useChartFit());
  const live = useRef({ slots, hMeasure, cMeasure, scale, colorOf, platforms, best, bestText, tipFor, t });
  live.current = { slots, hMeasure, cMeasure, scale, colorOf, platforms, best, bestText, tipFor, t };

  useEffect(() => {
    const el = host.current!;
    const W = el.clientWidth, H = el.clientHeight;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(W, H);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.setSize(W, H);
    Object.assign(labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, W / H, 0.1, 300);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.minDistance = 4; controls.maxDistance = 90; controls.maxPolarAngle = 1.45;
    cam.current = { camera, controls };
    controls.target.set(0, 0.3, 1.2);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(-10, 18, 12);
    scene.add(sun);

    // floor plate + grid
    const plate = new THREE.Mesh(new RoundedBoxGeometry(24 + 1.2, 0.25, 7 * 1.25 + 1.0, 3, 0.12),
      new THREE.MeshStandardMaterial({ color: document.documentElement.dataset.mode === "dark" ? 0x1e293b : 0xe2e8f0, roughness: 0.9, metalness: 0 }));
    plate.position.y = -0.14;
    scene.add(plate);
    const gridMat = new THREE.LineBasicMaterial({ color: document.documentElement.dataset.mode === "dark" ? 0x334155 : 0xcbd5e1 });
    const pts: THREE.Vector3[] = [];
    for (let h = 0; h <= 24; h += 3) { pts.push(new THREE.Vector3(X(h) - 0.5, 0.01, Z(0) - 0.65), new THREE.Vector3(X(h) - 0.5, 0.01, Z(6) + 0.65)); }
    scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
    const mkLabel = (text: string, cls: string) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; return new CSS2DObject(d); };
    DAYS.forEach((d, i) => { const l = mkLabel(d, "hm3-ax"); l.position.set(X(0) - 1.4, 0.05, Z(i)); scene.add(l); });
    for (let h = 0; h < 24; h += 3) { const l = mkLabel(hh(h), "hm3-ax"); l.position.set(X(h), 0.05, Z(6) + 1.25); scene.add(l); }
    const peak = mkLabel("", "hm3-peak");
    scene.add(peak);

    // instanced bars: one instance per slot (or per slot × channel when split)
    const cap = 168 * Math.max(1, Math.min(9, live.current.platforms.length + 1));
    const geo = new RoundedBoxGeometry(1 - GAP, 1, 1.25 - GAP, 2, 0.08);
    geo.translate(0, 0.5, 0); // grow from the floor
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.45, metalness: 0.1 });
    const bars = new THREE.InstancedMesh(geo, mat, cap);
    bars.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    scene.add(bars);
    const dummy = new THREE.Object3D();
    let owners: (Slot | null)[] = [];
    let target: { x: number; z: number; y0: number; h: number; col: THREE.Color }[] = [];
    let current: { y0: number; h: number }[] = [];
    let hovered = -1;

    const rebuild = () => {
      const L = live.current;
      const hmax = L.scale(L.hMeasure);
      const cmax = L.cMeasure === "channel" ? 1 : L.scale(L.cMeasure);
      const next: typeof target = [];
      owners = [];
      for (const s of L.slots.values()) {
        if (s.posts <= 0) continue;
        const total = Math.max(0.06, (s[L.hMeasure] / hmax) * MAXH);
        if (L.cMeasure === "channel") {
          let y0 = 0;
          const entries = Object.entries(s.platforms).sort((a, b) => a[0].localeCompare(b[0]));
          const sum = entries.reduce((a, [, n]) => a + n, 0) || 1;
          for (const [p, n] of entries) {
            const hgt = total * (n / sum);
            next.push({ x: X(s.h), z: Z(s.d), y0, h: hgt, col: new THREE.Color(L.colorOf(p) || OTHER) });
            owners.push(s); y0 += hgt;
          }
        } else {
          next.push({ x: X(s.h), z: Z(s.d), y0: 0, h: total, col: rampAt(0.12 + 0.88 * (s[L.cMeasure] / cmax)) });
          owners.push(s);
        }
      }
      // carry over current heights where possible so bars grow/shrink smoothly
      current = next.map((n, i) => current[i] ?? { y0: n.y0, h: 0.001 });
      target = next.slice(0, cap);
      bars.count = target.length;
      target.forEach((b, i) => bars.setColorAt(i, b.col));
      if (bars.instanceColor) bars.instanceColor.needsUpdate = true;
      const B = L.best;
      if (B) {
        const top = Math.max(0.06, (B[L.hMeasure] / hmax) * MAXH);
        peak.position.set(X(B.h), top + 0.5, Z(B.d));
        peak.element.textContent = `★ ${L.bestText}`;
        peak.element.style.display = "";
      } else peak.element.style.display = "none";
    };
    ctl.current = { update: rebuild };
    rebuild();

    const ray = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    const onMove = (ev: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      mouse.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, camera);
      const hit = ray.intersectObject(bars, false)[0];
      const id = hit?.instanceId ?? -1;
      if (id !== hovered) {
        if (hovered >= 0 && target[hovered]) bars.setColorAt(hovered, target[hovered].col);
        hovered = id;
        if (id >= 0 && target[id]) bars.setColorAt(id, target[id].col.clone().lerp(new THREE.Color(0xf59e0b), 0.55));
        if (bars.instanceColor) bars.instanceColor.needsUpdate = true;
      }
      const s = id >= 0 ? owners[id] : null;
      renderer.domElement.style.cursor = s ? "pointer" : "grab";
      if (s) live.current.t.show(ev, live.current.tipFor(s)); else live.current.t.hide();
    };
    const onLeave = () => { if (hovered >= 0 && target[hovered]) { bars.setColorAt(hovered, target[hovered].col); if (bars.instanceColor) bars.instanceColor.needsUpdate = true; } hovered = -1; live.current.t.hide(); };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    // frame the whole floor whatever the panel's shape
    const fit = (w: number, h: number) => {
      const dir = new THREE.Vector3(-0.32, 0.62, 0.72).normalize();
      const dist = Math.min(52, Math.max(20, 16 + 20 / Math.max(0.6, w / h)));
      camera.position.copy(controls.target).addScaledVector(dir, dist);
    };
    fit(W, H);
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h); labels.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
    });
    ro.observe(el);

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      for (let i = 0; i < target.length; i++) {
        const c = current[i], g = target[i];
        c.h += (g.h - c.h) * 0.16; c.y0 += (g.y0 - c.y0) * 0.16;
        dummy.position.set(g.x, c.y0, g.z);
        dummy.scale.set(1, Math.max(0.001, c.h), 1);
        dummy.updateMatrix();
        bars.setMatrixAt(i, dummy.matrix);
      }
      bars.instanceMatrix.needsUpdate = true;
      bars.computeBoundingSphere();
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); controls.dispose();
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      scene.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); const mt = m.material as THREE.Material | undefined; mt?.dispose?.(); if (o instanceof CSS2DObject) o.element.remove(); });
      renderer.dispose();
      el.removeChild(renderer.domElement); el.removeChild(labels.domElement);
      ctl.current = null; cam.current = null;
    };
  }, []);

  useEffect(() => { ctl.current?.update(); }, [slots, hMeasure, cMeasure, scale, best, bestText]);

  return (
    <div ref={t.ref} className="hm3-bg relative overflow-hidden rounded-xl" style={{ height }}>
      <div ref={host} className="absolute inset-0" />
      {t.tip}
      <style>{`
        .hm3-bg { background: radial-gradient(ellipse at 50% 20%, #ffffff 0%, #f1f5f9 70%); }
        [data-mode="dark"] .hm3-bg { background: radial-gradient(ellipse at 50% 20%, rgb(var(--surface)) 0%, rgb(var(--canvas)) 75%); }
        .hm3-ax { font: 600 11px/14px var(--font-sans); color: #475569; background: rgba(255,255,255,.85); padding: 1px 5px; border-radius: 4px; }
        .hm3-peak { font: 700 11.5px/16px var(--font-sans); color: #fff; background: #0d366b; padding: 2px 8px; border-radius: 9999px;
          box-shadow: 0 4px 12px rgba(13,54,107,.35); white-space: nowrap; }
        [data-mode="dark"] .hm3-ax { background: rgba(15,23,42,.8); color: #cbd5e1; }
      `}</style>
    </div>
  );
}
