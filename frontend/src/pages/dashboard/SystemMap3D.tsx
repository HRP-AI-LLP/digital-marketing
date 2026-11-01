// Real 3D (WebGL) view of the system map: the orchestrator in the middle, AI agents floating on an upper ring,
// the services they rely on on a middle ring, and every connected channel as a pillar on the outer ring (the
// pillar's height is how many posts went out in the last 7 days). Curved links carry moving light where work is
// flowing; broken links are red and dashed. Drag to orbit, scroll to zoom, hover for details, click for the card.
import { useEffect, useRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { LucideIcon } from "lucide-react";
import { useCameraZoom, useChartZoom } from "../../components/viz";

export type Node3D = {
  id: string; kind: "hub" | "agent" | "service" | "account"; x: number; y: number; label: string; sub?: string;
  ring: string; stateLabel: string; working: boolean; broken: boolean; icon?: LucideIcon; badge?: { code: string; bg: string };
  height?: number; // accounts: 0..1, pillar height
};
export type Edge3D = { a: string; b: string; kind: "spoke" | "flow" | "publish" | "feed" | "collect"; live: boolean; broken: boolean };

const SCALE = 1 / 28; // plane units (0..1000, centre 500) → world units
const TIER = { hub: 1.2, agent: 3.4, service: 0.9, account: -1.6 } as const;
const EDGE_COLOR = { spoke: 0x64748b, flow: 0x7dd3fc, publish: 0x93c5fd, collect: 0x6ee7b7, feed: 0xc4b5fd } as const;

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch { return false; }
}

function glowTexture(color: string) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, color);
  grd.addColorStop(0.35, `${color}88`);
  grd.addColorStop(1, `${color}00`);
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function floorTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d")!;
  const grd = g.createRadialGradient(256, 256, 0, 256, 256, 256);
  grd.addColorStop(0, "rgba(34,211,238,0.22)");
  grd.addColorStop(0.45, "rgba(30,64,120,0.18)");
  grd.addColorStop(1, "rgba(8,26,48,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 512, 512);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function labelEl(n: Node3D) {
  const el = document.createElement("div");
  el.className = "sm3-label";
  const icon = n.icon ? renderToStaticMarkup(<n.icon size={13} strokeWidth={2.2} />) : "";
  const badge = n.badge ? `<span class="sm3-badge" style="background:${n.badge.bg}">${n.badge.code}</span>` : "";
  el.innerHTML = `<div class="sm3-pill ${n.kind}">${badge}${icon ? `<span class="sm3-ic">${icon}</span>` : ""}<span class="sm3-name"></span>`
    + `<span class="sm3-dot" style="background:${n.ring}"></span></div>${n.sub ? '<div class="sm3-sub"></div>' : ""}`;
  (el.querySelector(".sm3-name") as HTMLElement).textContent = n.label;
  if (n.sub) (el.querySelector(".sm3-sub") as HTMLElement).textContent = n.sub;
  el.setAttribute("translate", n.kind === "account" ? "no" : "yes");
  return el;
}

export default function SystemMap3D({ nodes, edges, height, onHover, onPick, logo = "/logo-mark.png" }: {
  nodes: Node3D[]; edges: Edge3D[]; height: number;
  onHover: (id: string | null, e?: { clientX: number; clientY: number }) => void; onPick: (id: string) => void; logo?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<{ setGraph: (n: Node3D[], e: Edge3D[]) => void; dispose: () => void } | null>(null);
  const last = useRef("");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cam = useRef<any>(null);
  useCameraZoom(useChartZoom(), cam);
  const cb = useRef({ onHover, onPick });
  cb.current = { onHover, onPick };

  // one renderer / camera / controls for the component's life
  useEffect(() => {
    const el = host.current!;
    const w = el.clientWidth, h = el.clientHeight;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(w, h);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.setSize(w, h);
    Object.assign(labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x061426, 30, 70);
    const camera = new THREE.PerspectiveCamera(42, w / h, 0.1, 200);
    camera.position.set(0, 19, 33);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 6;
    controls.maxDistance = 80;
    cam.current = { camera, controls };
    controls.maxPolarAngle = 1.38;
    controls.target.set(0, 0.6, 0);
    const reduce = document.documentElement.dataset.motion === "reduce" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    controls.autoRotate = !reduce;
    controls.autoRotateSpeed = 0.45;
    let idleTimer = 0;
    controls.addEventListener("start", () => { controls.autoRotate = false; window.clearTimeout(idleTimer); });
    controls.addEventListener("end", () => { idleTimer = window.setTimeout(() => { controls.autoRotate = !reduce; }, 6000); });

    scene.add(new THREE.AmbientLight(0x9fb8d8, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(8, 20, 12);
    scene.add(key);
    const core = new THREE.PointLight(0x22d3ee, 40, 30, 1.6);
    core.position.set(0, 3, 0);
    scene.add(core);

    // floor: glow disc, polar grid, tier rings
    const floor = new THREE.Mesh(new THREE.CircleGeometry(22, 96), new THREE.MeshBasicMaterial({ map: floorTexture(), transparent: true, depthWrite: false }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -1.62;
    scene.add(floor);
    const grid = new THREE.PolarGridHelper(19, 24, 6, 96, 0x1e4a7a, 0x12304f);
    grid.position.y = -1.6;
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.55;
    scene.add(grid);
    // star dust
    const stars = new THREE.BufferGeometry();
    const sp: number[] = [];
    for (let i = 0; i < 500; i++) { const r = 30 + Math.random() * 40, a = Math.random() * Math.PI * 2, y = Math.random() * 30 - 4; sp.push(r * Math.cos(a), y, r * Math.sin(a)); }
    stars.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
    scene.add(new THREE.Points(stars, new THREE.PointsMaterial({ color: 0x9fc6ff, size: 0.12, transparent: true, opacity: 0.7 })));

    let graph = new THREE.Group();
    scene.add(graph);
    let pickables: THREE.Object3D[] = [];
    let anims: ((t: number) => void)[] = [];
    let edgeObjs: { a: string; b: string; mat: THREE.LineBasicMaterial | THREE.LineDashedMaterial; base: number }[] = [];
    let hovered: string | null = null;
    const loader = new THREE.TextureLoader();
    const glows = new Map<string, THREE.Texture>();
    const glow = (c: string) => { if (!glows.has(c)) glows.set(c, glowTexture(c)); return glows.get(c)!; };

    const disposeGroup = (g: THREE.Object3D) => g.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose?.();
      const mat = m.material as THREE.Material | THREE.Material[] | undefined;
      (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
      if (o instanceof CSS2DObject) o.element.remove();
    });

    const highlight = () => {
      const rel = new Set<string>();
      if (hovered) { rel.add(hovered); edgeObjs.forEach((e) => { if (e.a === hovered) rel.add(e.b); if (e.b === hovered) rel.add(e.a); }); }
      edgeObjs.forEach((e) => {
        const on = !hovered || ((e.a === hovered || e.b === hovered));
        e.mat.opacity = hovered ? (on ? Math.min(1, e.base * 2.4) : 0.05) : e.base;
      });
      graph.children.forEach((o) => {
        const id = o.userData.nodeId as string | undefined;
        if (!id) return;
        const dim = hovered && !rel.has(id);
        o.traverse((x) => {
          const m = (x as THREE.Mesh).material as THREE.Material & { opacity?: number } | undefined;
          if (m && "opacity" in m && x.userData.baseOpacity != null) { m.transparent = true; m.opacity = dim ? x.userData.baseOpacity * 0.25 : x.userData.baseOpacity; }
          if (x instanceof CSS2DObject) x.element.style.opacity = dim ? "0.25" : "1";
        });
      });
    };

    const setGraph = (ns: Node3D[], es: Edge3D[]) => {
      scene.remove(graph);
      disposeGroup(graph);
      graph = new THREE.Group();
      scene.add(graph);
      pickables = []; anims = []; edgeObjs = [];
      const pos = new Map<string, THREE.Vector3>();
      ns.forEach((n) => {
        const x = (n.x - 500) * SCALE, z = (n.y - 500) * SCALE;
        const top = n.kind === "account" ? TIER.account + 0.8 + (n.height ?? 0) * 4.2 : TIER[n.kind];
        pos.set(n.id, new THREE.Vector3(x, top, z));
        const g = new THREE.Group();
        g.userData.nodeId = n.id;
        g.position.set(x, 0, z);
        const ringCol = new THREE.Color(n.ring);
        const mark = (m: THREE.Object3D, o = 1) => { m.userData.baseOpacity = o; m.userData.nodeId = n.id; return m; };
        if (n.kind === "hub") {
          const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1.5, 2), new THREE.MeshStandardMaterial({ color: 0x0b5fa5, emissive: 0x0e7490, emissiveIntensity: 0.55, metalness: 0.35, roughness: 0.3, flatShading: true }));
          s.position.y = top; g.add(mark(s)); pickables.push(s);
          const ring = new THREE.Mesh(new THREE.TorusGeometry(2.3, 0.05, 12, 96), new THREE.MeshBasicMaterial({ color: 0x67e8f9, transparent: true, opacity: 0.85 }));
          ring.position.y = top; ring.rotation.x = Math.PI / 2; g.add(mark(ring, 0.85));
          const ring2 = ring.clone(); ring2.scale.setScalar(1.25); ring2.material = new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.4 }); g.add(mark(ring2, 0.4));
          anims.push((t) => { s.rotation.y = t * 0.25; ring.rotation.z = t * 0.6; ring2.rotation.z = -t * 0.35; ring2.rotation.x = Math.PI / 2 + Math.sin(t * 0.5) * 0.25; });
          const tex = loader.load(logo);
          tex.colorSpace = THREE.SRGBColorSpace;
          const logoS = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
          logoS.scale.set(1.4, 1.4, 1); logoS.position.y = top + 0.05; logoS.renderOrder = 5; g.add(mark(logoS));
          const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow("#22d3ee"), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.8 }));
          halo.scale.set(9, 9, 1); halo.position.y = top; g.add(mark(halo, 0.8));
          // light beam to the floor
          const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.4, top - TIER.account, 16, 1, true), new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.18, side: THREE.DoubleSide }));
          beam.position.y = (top + TIER.account) / 2; g.add(mark(beam, 0.18));
        } else if (n.kind === "agent" || n.kind === "service") {
          const r = n.kind === "agent" ? 0.72 : 0.5;
          const geo = n.kind === "agent" ? new THREE.SphereGeometry(r, 32, 24) : new THREE.OctahedronGeometry(r * 1.15, 0);
          const body = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x14365c, emissive: ringCol, emissiveIntensity: n.working ? 0.55 : 0.22, metalness: 0.4, roughness: 0.35, flatShading: n.kind === "service", transparent: true }));
          body.position.y = top; g.add(mark(body)); pickables.push(body);
          const torus = new THREE.Mesh(new THREE.TorusGeometry(r + 0.22, 0.05, 10, 64), new THREE.MeshBasicMaterial({ color: ringCol, transparent: true, opacity: 0.95 }));
          torus.position.y = top; torus.rotation.x = Math.PI / 2; g.add(mark(torus, 0.95));
          const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow(n.ring), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: n.working ? 0.9 : 0.45 }));
          halo.scale.set(3.6, 3.6, 1); halo.position.y = top; g.add(mark(halo, n.working ? 0.9 : 0.45));
          // stem down to the floor, so height reads as depth
          const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, top - TIER.account, 6), new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.18 }));
          stem.position.y = (top + TIER.account) / 2; g.add(mark(stem, 0.18));
          const foot = new THREE.Mesh(new THREE.RingGeometry(0.25, 0.32, 24), new THREE.MeshBasicMaterial({ color: ringCol, transparent: true, opacity: 0.45, side: THREE.DoubleSide }));
          foot.rotation.x = -Math.PI / 2; foot.position.y = TIER.account + 0.02; g.add(mark(foot, 0.45));
          const phase = Math.random() * 6;
          anims.push((t) => {
            const bob = Math.sin(t * 1.2 + phase) * 0.12;
            body.position.y = top + bob; torus.position.y = top + bob; halo.position.y = top + bob;
            if (n.kind === "service") body.rotation.y = t * 0.6;
            if (n.working) { const k = 1 + ((t * 0.8) % 1) * 0.6; torus.scale.setScalar(k); (torus.material as THREE.MeshBasicMaterial).opacity = 1 - ((t * 0.8) % 1); }
          });
        } else {
          const hgt = 0.8 + (n.height ?? 0) * 4.2;
          const col = new THREE.Color(n.badge?.bg ?? "#475569");
          const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.7, hgt, 32), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.25, metalness: 0.3, roughness: 0.45, transparent: true, opacity: n.broken ? 0.55 : 0.95 }));
          pillar.position.y = TIER.account + hgt / 2; g.add(mark(pillar, n.broken ? 0.55 : 0.95)); pickables.push(pillar);
          const cap = new THREE.Mesh(new THREE.TorusGeometry(0.78, 0.07, 10, 48), new THREE.MeshBasicMaterial({ color: ringCol, transparent: true, opacity: 1 }));
          cap.position.y = TIER.account + hgt + 0.02; cap.rotation.x = Math.PI / 2; g.add(mark(cap));
          const base = new THREE.Mesh(new THREE.RingGeometry(0.85, 1.05, 40), new THREE.MeshBasicMaterial({ color: ringCol, transparent: true, opacity: 0.5, side: THREE.DoubleSide }));
          base.rotation.x = -Math.PI / 2; base.position.y = TIER.account + 0.02; g.add(mark(base, 0.5));
          const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow(n.ring), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.5 }));
          halo.scale.set(3, 3, 1); halo.position.y = TIER.account + hgt; g.add(mark(halo, 0.5));
        }
        const lab = new CSS2DObject(labelEl(n));
        lab.position.set(0, (n.kind === "account" ? TIER.account + 0.8 + (n.height ?? 0) * 4.2 : top) + (n.kind === "hub" ? 2.3 : n.kind === "agent" ? 1.25 : n.kind === "service" ? 0.95 : 0.85), 0);
        lab.center.set(0.5, 1);
        g.add(lab);
        graph.add(g);
      });

      es.forEach((e) => {
        const a = pos.get(e.a), b = pos.get(e.b);
        if (!a || !b) return;
        const mid = a.clone().add(b).multiplyScalar(0.5);
        const lift = e.kind === "spoke" ? 0.6 : e.kind === "flow" ? 2.2 : e.kind === "publish" ? 1.6 : 1.0;
        mid.y = Math.max(a.y, b.y) + lift;
        const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
        const geo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(48));
        const base = e.broken ? 0.9 : e.kind === "flow" ? 0.75 : e.kind === "publish" ? 0.55 : e.kind === "spoke" ? 0.35 : 0.3;
        const mat = e.broken || e.kind === "collect" || e.kind === "feed"
          ? new THREE.LineDashedMaterial({ color: e.broken ? 0xf87171 : EDGE_COLOR[e.kind], dashSize: 0.35, gapSize: 0.25, transparent: true, opacity: base })
          : new THREE.LineBasicMaterial({ color: EDGE_COLOR[e.kind], transparent: true, opacity: base });
        const line = new THREE.Line(geo, mat);
        if (mat instanceof THREE.LineDashedMaterial) line.computeLineDistances();
        graph.add(line);
        edgeObjs.push({ a: e.a, b: e.b, mat, base });
        if ((e.live || e.kind === "flow" || e.kind === "publish") && !e.broken) {
          const count = e.live ? 3 : 1;
          for (let i = 0; i < count; i++) {
            const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow(e.live ? "#e0f2fe" : "#7dd3fc"), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
            dot.scale.setScalar(e.live ? 0.7 : 0.45);
            graph.add(dot);
            const off = i / count, speed = e.live ? 0.45 : 0.18;
            anims.push((t) => { if (reduce) { dot.position.copy(curve.getPoint(0.5)); return; } dot.position.copy(curve.getPoint((t * speed + off) % 1)); });
          }
        }
      });
      highlight();
    };

    // hover + click
    const ray = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let downAt: { x: number; y: number } | null = null;
    const pick = (ev: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      mouse.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, camera);
      const hit = ray.intersectObjects(pickables, false)[0];
      return (hit?.object.userData.nodeId as string | undefined) ?? null;
    };
    const onMove = (ev: PointerEvent) => {
      const id = pick(ev);
      if (id !== hovered) { hovered = id; highlight(); }
      renderer.domElement.style.cursor = id ? "pointer" : "grab";
      cb.current.onHover(id, ev);
    };
    const onLeave = () => { hovered = null; highlight(); cb.current.onHover(null); };
    const onDown = (ev: PointerEvent) => { downAt = { x: ev.clientX, y: ev.clientY }; };
    const onUp = (ev: PointerEvent) => {
      if (downAt && Math.abs(ev.clientX - downAt.x) + Math.abs(ev.clientY - downAt.y) < 5) { const id = pick(ev); if (id) cb.current.onPick(id); }
      downAt = null;
    };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const ro = new ResizeObserver(() => {
      const W = el.clientWidth, H = el.clientHeight;
      renderer.setSize(W, H); labels.setSize(W, H);
      camera.aspect = W / H; camera.updateProjectionMatrix();
    });
    ro.observe(el);

    const clock = new THREE.Clock();
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const t = clock.getElapsedTime();
      anims.forEach((f) => f(t));
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
    };
    loop();

    api.current = {
      setGraph,
      dispose: () => {
        cancelAnimationFrame(raf); ro.disconnect(); window.clearTimeout(idleTimer); controls.dispose();
        renderer.domElement.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("pointerleave", onLeave);
        renderer.domElement.removeEventListener("pointerdown", onDown);
        renderer.domElement.removeEventListener("pointerup", onUp);
        disposeGroup(scene); glows.forEach((t) => t.dispose()); renderer.dispose();
        el.removeChild(renderer.domElement); el.removeChild(labels.domElement);
      },
    };
    last.current = ""; // a fresh scene needs the graph again
    return () => { api.current?.dispose(); api.current = null; cam.current = null; };
  }, [logo]);

  // rebuild the graph only when something visible changed (data refreshes every 15 s)
  const sig = JSON.stringify([nodes.map((n) => [n.id, n.ring, n.working, n.broken, n.height, n.label]), edges.map((e) => [e.a, e.b, e.live, e.broken])]);
  useEffect(() => {
    if (!api.current || sig === last.current) return;
    last.current = sig;
    api.current.setGraph(nodes, edges);
  });

  return (
    <div ref={host} className="absolute inset-0" style={{ height }}>
      <style>{`
        .sm3-label { pointer-events: none; display: flex; flex-direction: column; align-items: center; transition: opacity .2s; }
        .sm3-pill { display: flex; align-items: center; gap: 5px; padding: 3px 9px 3px 6px; border-radius: 9999px; background: rgba(8,26,48,.82);
          border: 1px solid rgba(125,211,252,.25); color: #fff; font: 600 12px/16px var(--font-sans); white-space: nowrap; box-shadow: 0 4px 14px rgba(0,0,0,.35); max-width: 190px; }
        .sm3-pill.hub { font-size: 14px; padding: 4px 12px; border-color: rgba(34,211,238,.55); }
        .sm3-pill.account { font-size: 12.5px; }
        .sm3-name { overflow: hidden; text-overflow: ellipsis; }
        .sm3-ic { display: grid; place-items: center; color: #bae6fd; }
        .sm3-badge { display: grid; place-items: center; min-width: 18px; height: 18px; padding: 0 3px; border-radius: 5px; font-size: 9.5px; font-weight: 800; color: #fff; }
        .sm3-dot { width: 7px; height: 7px; border-radius: 9999px; box-shadow: 0 0 6px currentColor; flex: none; }
        .sm3-sub { margin-top: 2px; font: 500 10.5px/14px var(--font-sans); color: rgba(186,230,253,.85); text-shadow: 0 1px 3px rgba(0,0,0,.7); white-space: nowrap; }
      `}</style>
    </div>
  );
}
