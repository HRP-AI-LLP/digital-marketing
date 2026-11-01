// Shared, dependency-free interactive charts. Every plotted mark has a hover / focus popup with the exact
// figures. Rules (see the dataviz method): categorical colours in a fixed, validated order (never cycled; extra
// series fold into "Other"), one y-axis per chart, thin marks with rounded data ends, recessive grid, text in ink
// colours (never the series colour), a legend for 2+ series, and identity never by colour alone.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2, ZoomIn, ZoomOut } from "lucide-react";

// ------------------------------------------------------------------ palette & formatting
/** Brand blue first; the rest is the validated categorical order (worst adjacent CVD ΔE ≥ 9). */
export const SERIES = ["#086fb8", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
export const OTHER = "#94a3b8";
export const GRID = "#e2e8f0";
export const AXIS = "#64748b";
export const INK = "#0f172a";
export const SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
export const STATUS = { good: "#047857", warn: "#b45309", bad: "#b91c1c", idle: "#64748b", info: "#086fb8" };

export const fmtNum = (v: number) =>
  Math.abs(v) >= 10000 ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v)
    : new Intl.NumberFormat("en-IN", { maximumFractionDigits: v % 1 ? 1 : 0 }).format(v);
export const fmtFull = (v: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(v);
export const fmtPct = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;
export const fmtDuration = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
export const fmtDay = (iso: string) =>
  new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(iso.length === 10 ? `${iso}T00:00:00` : iso));
export const fmtDayLong = (iso: string) =>
  new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" })
    .format(new Date(iso.length === 10 ? `${iso}T00:00:00` : iso));

/** Mix a hex colour with white (amt > 0) or black (amt < 0). Used for 3D faces and hover lifts. */
export function shade(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) =>
    Math.round(amt >= 0 ? c + (255 - c) * amt : c * (1 + amt)));
  return `#${ch.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** Fixed colour per entity (platform, status...) from a stable key order, so filters never repaint survivors. */
export function colorMap(keys: string[], max = 8): Record<string, string> {
  const out: Record<string, string> = {};
  keys.forEach((k, i) => { out[k] = i < max ? SERIES[i] : OTHER; });
  return out;
}

export function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => { if (ref.current) setW(ref.current.clientWidth || fallback); }, [fallback]);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((e) => setW(Math.max(160, e[0].contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(+v.toFixed(6));
  if (ticks[ticks.length - 1] < max) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(6));
  return ticks;
}

// ------------------------------------------------------------------ hover popup
type TipState = { x: number; y: number; content: ReactNode } | null;

/**
 * One popup per chart. `show` takes a pointer event (or an element, for keyboard focus) and the content.
 * Put `ref` on a `relative` wrapper and render `tip` inside it.
 */
export function useTip<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T>(null);
  const [st, setSt] = useState<TipState>(null);
  const show = (e: { clientX: number; clientY: number } | Element, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    let cx: number, cy: number;
    if (e instanceof Element) {
      const r = e.getBoundingClientRect();
      cx = r.left + r.width / 2; cy = r.top;
    } else { cx = e.clientX; cy = e.clientY; }
    setSt({ x: cx, y: cy, content });
  };
  const hide = () => setSt(null);
  const tip = st ? <TipBox x={st.x} y={st.y}>{st.content}</TipBox> : null;
  return { ref, show, hide, tip, active: st !== null };
}

/** The popup is fixed to the viewport (in a portal), so a zoomed, scrolling or clipped chart never cuts it off. */
function TipBox({ x, y, children }: { x: number; y: number; wrap?: React.RefObject<HTMLElement>; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<CSSProperties>({ left: x + 14, top: y - 12, opacity: 0 });
  const [gone, setGone] = useState(false);
  useLayoutEffect(() => {
    setGone(false);
    const w = window.innerWidth, h = window.innerHeight;
    const bw = box.current?.offsetWidth ?? 180;
    const bh = box.current?.offsetHeight ?? 60;
    const left = x + 14 + bw > w - 8 ? Math.max(8, x - 14 - bw) : x + 14;
    const top = y - bh - 10 < 8 ? Math.min(h - bh - 8, y + 16) : y - bh - 10;
    setPos({ left, top, opacity: 1 });
  }, [x, y, children]);
  useEffect(() => {
    const off = () => setGone(true);
    window.addEventListener("scroll", off, true);
    return () => window.removeEventListener("scroll", off, true);
  }, []);
  if (gone || typeof document === "undefined") return null;
  return createPortal(
    <div ref={box} role="tooltip"
      className="pointer-events-none fixed z-[1000] min-w-[9rem] max-w-[17rem] rounded-lg border border-line bg-white/95 px-3 py-2 text-xs shadow-xl backdrop-blur-sm transition-opacity duration-75"
      style={pos}>
      {children}
    </div>,
    document.fullscreenElement ?? document.body,
  );
}

/**
 * Wrap any small data mark (a score ring, an inline bar, a badge) so hovering or focusing it shows the popup.
 * Renders an inline-flex span by default; pass `block` for a block-level wrapper.
 */
export function HoverTip({ tip, children, className = "", label, block = false }: {
  tip: ReactNode; children: ReactNode; className?: string; label?: string; block?: boolean;
}) {
  const t = useTip<HTMLSpanElement>();
  return (
    <span ref={t.ref} tabIndex={0} aria-label={label}
      className={`relative ${block ? "block" : "inline-flex"} cursor-default rounded outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 ${className}`}
      onPointerMove={(e) => t.show(e, tip)} onPointerLeave={t.hide}
      onFocus={(e) => t.show(e.currentTarget, tip)} onBlur={t.hide}>
      {children}
      {t.tip}
    </span>
  );
}

export function TipHead({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-1.5 border-b border-line pb-1">
      <div className="font-semibold text-ink">{children}</div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

/** A tooltip row: short line key in the series colour, label, and the value as the strong element. */
export function TipRow({ color, label, value, dashed, strong }: { color?: string; label: ReactNode; value: ReactNode; dashed?: boolean; strong?: boolean }) {
  return (
    <div className={`flex items-center gap-2 py-0.5 tabular-nums ${strong ? "mt-0.5 border-t border-line pt-1" : ""}`}>
      {color ? (
        <svg width="12" height="6" aria-hidden className="shrink-0"><line x1="0" y1="3" x2="12" y2="3" stroke={color} strokeWidth="3" strokeLinecap="round" strokeDasharray={dashed ? "3 2" : undefined} /></svg>
      ) : <span className="w-3 shrink-0" />}
      <span className="min-w-0 flex-1 truncate text-muted">{label}</span>
      <span className="pl-2 font-semibold text-ink">{value}</span>
    </div>
  );
}

// ------------------------------------------------------------------ frame, legend, empty
export function Legend({ items }: { items: { label: string; color: string; shape?: "line" | "rect" | "dot"; dashed?: boolean; value?: ReactNode }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          {it.shape === "line" ? (
            <svg width="16" height="6" aria-hidden><line x1="0" y1="3" x2="16" y2="3" stroke={it.color} strokeWidth="2.5" strokeDasharray={it.dashed ? "4 3" : undefined} /></svg>
          ) : <span className={`h-2.5 w-2.5 ${it.shape === "dot" ? "rounded-full" : "rounded-sm"}`} style={{ background: it.color }} />}
          <span className="text-ink/80">{it.label}</span>
          {it.value != null && <span className="tabular-nums text-muted">{it.value}</span>}
        </span>
      ))}
    </div>
  );
}

export function Empty({ children = "No data for this period yet.", h = 160 }: { children?: ReactNode; h?: number }) {
  return <div className="grid place-items-center rounded-lg bg-slate-50 text-center text-sm text-muted" style={{ minHeight: h }}>{children}</div>;
}

/** Card wrapper used on dashboards: title, optional subtitle and action, then the chart. */
export function Panel({ title, sub, action, children, className = "" }: { title: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-line bg-surface p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}>
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
          {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}


// ------------------------------------------------------------------ zoom (every chart)
/**
 * Zoom for charts: the − / % / + / full-screen buttons (top right, on hover), Ctrl/⌘ + scroll or a trackpad pinch
 * around the pointer, drag to pan when zoomed in, and + / − / 0 keys. Charts are redrawn at the new size (not
 * blown up as a picture), so lines stay sharp, the axes gain detail and hover popups keep working.
 * "stretch" charts get a wider canvas and scale their height by `useChartZoom()`; "self" charts (3D scenes, the
 * system map) read the factor and move their own camera.
 */
type ZoomState = { k: number; fit: number; mode: "stretch" | "self" };
const ZoomCtx = createContext<ZoomState | null>(null);
const ZMIN = 0.5, ZMAX = 6;
const STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, 6];

/** The size factor to draw at (zoom × full-screen fit). 1 outside a zoom frame. */
export function useChartZoom(): number {
  const z = useContext(ZoomCtx);
  return z ? (z.mode === "self" ? z.k : z.k * z.fit) : 1;
}
/** Full-screen height factor, for "self" charts that size their own box. */
export function useChartFit(): number {
  return useContext(ZoomCtx)?.fit ?? 1;
}

export function ZoomFrame({ children, mode = "stretch", pan = true, wheel = true, className = "", dark = false, corner = "top" }: {
  children: ReactNode | ((k: number) => ReactNode); mode?: "stretch" | "self"; pan?: boolean; className?: string; dark?: boolean;
  /** false when the chart already zooms on the wheel itself (3D scenes with orbit controls) */
  wheel?: boolean;
  /** where the zoom buttons sit (bottom when the chart keeps its own controls at the top) */
  corner?: "top" | "bottom";
}) {
  const parent = useContext(ZoomCtx);
  const outer = useRef<HTMLDivElement>(null);
  const vp = useRef<HTMLDivElement>(null);
  const [k, setK] = useState(1);
  const [fs, setFs] = useState(false);
  const [fit, setFit] = useState(1);
  const [baseH, setBaseH] = useState(0);
  const anchor = useRef<{ fx: number; fy: number; mx: number; my: number } | null>(null);
  const dragging = useRef<{ x: number; y: number; sl: number; st: number; moved: boolean } | null>(null);
  const justPanned = useRef(false);
  const kRef = useRef(k);
  kRef.current = k;

  // remember the natural height at 100% so zooming in scrolls inside the same box instead of growing the page
  useEffect(() => {
    const el = vp.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (kRef.current === 1 && !document.fullscreenElement) setBaseH(el.offsetHeight); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const zoomTo = useCallback((next: number, at?: { x: number; y: number }) => {
    const el = vp.current;
    const nk = Math.min(ZMAX, Math.max(ZMIN, +next.toFixed(3)));
    if (el) {
      const r = el.getBoundingClientRect();
      const mx = at ? at.x - r.left : r.width / 2, my = at ? at.y - r.top : r.height / 2;
      anchor.current = { fx: (el.scrollLeft + mx) / Math.max(1, el.scrollWidth), fy: (el.scrollTop + my) / Math.max(1, el.scrollHeight), mx, my };
    }
    setK(nk);
  }, []);
  const step = (dir: 1 | -1) => {
    const cur = kRef.current;
    const nx = dir > 0 ? STEPS.find((s) => s > cur + 0.01) ?? ZMAX : [...STEPS].reverse().find((s) => s < cur - 0.01) ?? ZMIN;
    zoomTo(nx);
  };
  useLayoutEffect(() => {
    const el = vp.current, a = anchor.current;
    if (!el || !a) return;
    // wait a frame so width-driven charts have re-measured and redrawn
    const id = requestAnimationFrame(() => {
      el.scrollLeft = a.fx * el.scrollWidth - a.mx;
      el.scrollTop = a.fy * el.scrollHeight - a.my;
      anchor.current = null;
    });
    return () => cancelAnimationFrame(id);
  }, [k]);

  // Ctrl/⌘ + wheel (and trackpad pinch) zooms around the pointer; a plain wheel still scrolls the page
  useEffect(() => {
    const el = vp.current;
    if (!el || parent || !wheel) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      zoomTo(kRef.current * Math.exp(-e.deltaY * 0.0025), { x: e.clientX, y: e.clientY });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [parent, zoomTo, wheel]);

  useEffect(() => {
    const on = () => {
      const isMe = document.fullscreenElement === outer.current;
      setFs(isMe);
      setFit(isMe && baseH ? Math.max(1, Math.min(3, (window.innerHeight - 96) / baseH)) : 1);
    };
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, [baseH]);
  const toggleFs = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void outer.current?.requestFullscreen?.().catch(() => undefined);
  };

  const value = useMemo<ZoomState>(() => ({ k, fit, mode }), [k, fit, mode]);
  const draw = (z: number) => (typeof children === "function" ? children(z) : children);
  if (parent) return <>{draw(parent.mode === "self" ? parent.k : parent.k * parent.fit)}</>; // already inside a zoom frame

  const zoomed = k !== 1;
  const canPan = pan && mode === "stretch" && k > 1;
  const btn = `grid h-7 w-7 place-items-center rounded-md outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary-500/40 disabled:opacity-35 ${dark ? "text-slate-200 hover:bg-white/10" : "text-slate-600 hover:bg-slate-100 hover:text-ink"}`;
  return (
    <div ref={outer} className={`zoom-frame group/zoom relative min-w-0 ${fs ? "overflow-auto bg-surface p-6" : ""} ${className}`}
      onKeyDown={(e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.key === "+" || e.key === "=") { e.preventDefault(); step(1); }
        else if (e.key === "-" || e.key === "_") { e.preventDefault(); step(-1); }
        else if (e.key === "0" && zoomed) { e.preventDefault(); zoomTo(1); }
      }}>
      <div role="toolbar" aria-label="Chart zoom"
        className={`absolute right-1 ${corner === "bottom" ? "bottom-1" : "top-1"} z-20 flex items-center gap-0.5 rounded-lg border p-0.5 shadow-sm backdrop-blur transition-opacity ${dark ? "border-white/15 bg-slate-900/70" : "border-line bg-white/90"} ${zoomed || fs ? "opacity-100" : "opacity-0 focus-within:opacity-100 group-hover/zoom:opacity-100 [@media(hover:none)]:opacity-100"} ${fs ? (corner === "bottom" ? "bottom-4 right-4" : "right-4 top-4") : ""}`}>
        <button type="button" className={btn} onClick={() => step(-1)} disabled={k <= ZMIN} title="Zoom out (−, or Ctrl + scroll)" aria-label="Zoom out"><ZoomOut size={15} /></button>
        <button type="button" onClick={() => zoomTo(1)} disabled={!zoomed} title="Back to 100% (0)" aria-label={`Zoom ${Math.round(k * 100)}%, reset`}
          className={`h-7 min-w-[3rem] rounded-md px-1 text-[11px] font-semibold tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 disabled:cursor-default ${dark ? "text-slate-100 hover:bg-white/10" : "text-ink hover:bg-slate-100"}`}>{Math.round(k * 100)}%</button>
        <button type="button" className={btn} onClick={() => step(1)} disabled={k >= ZMAX} title="Zoom in (+, or Ctrl + scroll)" aria-label="Zoom in"><ZoomIn size={15} /></button>
        <span className={`mx-0.5 h-4 w-px ${dark ? "bg-white/15" : "bg-line"}`} />
        <button type="button" className={btn} onClick={toggleFs} title={fs ? "Exit full screen (Esc)" : "Full screen"} aria-label={fs ? "Exit full screen" : "Full screen"}>{fs ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
      </div>
      <div ref={vp}
        className={`relative ${mode === "stretch" && (zoomed || fs) ? "overflow-auto overscroll-contain" : ""} ${canPan ? "cursor-grab active:cursor-grabbing" : ""}`}
        style={mode === "stretch" && k > 1 && baseH && !fs ? { height: baseH } : undefined}
        onPointerDown={(e) => {
          if (!canPan || e.button !== 0 || (e.target as HTMLElement).closest("button, a, input, select, textarea")) return;
          dragging.current = { x: e.clientX, y: e.clientY, sl: vp.current!.scrollLeft, st: vp.current!.scrollTop, moved: false };
        }}
        onPointerMove={(e) => {
          const d = dragging.current;
          if (!d) return;
          const dx = e.clientX - d.x, dy = e.clientY - d.y;
          if (!d.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
          if (!d.moved) { d.moved = true; vp.current!.setPointerCapture?.(e.pointerId); }
          vp.current!.scrollLeft = d.sl - dx;
          vp.current!.scrollTop = d.st - dy;
        }}
        onPointerUp={() => { if (dragging.current?.moved) { justPanned.current = true; setTimeout(() => { justPanned.current = false; }, 0); } dragging.current = null; }}
        onPointerCancel={() => { dragging.current = null; }}
        onClickCapture={(e) => { if (justPanned.current) { e.stopPropagation(); e.preventDefault(); } }}>
        <div style={mode === "stretch" && k !== 1 ? { width: `${k * 100}%`, marginInline: k < 1 ? "auto" : undefined } : undefined}>
          <ZoomCtx.Provider value={value}>{draw(mode === "self" ? k : k * fit)}</ZoomCtx.Provider>
        </div>
      </div>
    </div>
  );
}

/** Render-prop access to the enclosing frame's zoom and full-screen fit (for charts that size themselves). */
export function ZoomValues({ children }: { children: (z: { k: number; fit: number }) => ReactNode }) {
  const z = useContext(ZoomCtx);
  return <>{children({ k: z?.k ?? 1, fit: z?.fit ?? 1 })}</>;
}

/**
 * Dolly a three.js camera toward / away from its orbit target when the frame's zoom changes (for "self" frames).
 * Call with the current zoom; `cam` holds the camera and controls once the scene exists.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useCameraZoom(k: number, cam: React.MutableRefObject<any>) {
  const prev = useRef(k);
  useEffect(() => {
    const c = cam.current;
    const ratio = prev.current / k;
    prev.current = k;
    if (!c || ratio === 1) return;
    const { camera, controls } = c;
    const off = camera.position.clone().sub(controls.target);
    const dist = Math.min(controls.maxDistance, Math.max(controls.minDistance, off.length() * ratio));
    off.setLength(dist);
    camera.position.copy(controls.target).add(off);
    controls.update();
  }, [k, cam]);
}

/** Wrap a chart component so it always sits in a zoom frame (no-op when it's already inside one). */
export function withZoom<P extends object>(Inner: ComponentType<P>, opts: { pan?: boolean; mode?: "stretch" | "self" } = {}) {
  const Z = (props: P) => <ZoomFrame pan={opts.pan} mode={opts.mode}><Inner {...props} /></ZoomFrame>;
  Z.displayName = `Zoom(${Inner.displayName ?? Inner.name ?? "Chart"})`;
  return Z;
}


/**
 * Time-axis zoom for line and column charts: at zoom k the chart shows 1/k of the points (the y-axis rescales to
 * what's visible, the axes stay put). Drag the plot or the strip underneath to move along, or scroll sideways.
 */
export function useTimeWindow(n: number, k: number) {
  const win = Math.max(Math.min(n, 2), Math.min(n, Math.round(n / Math.max(1, k))));
  // the window's centre (in points); null keeps the latest points in view. Zooming keeps the centre where it is.
  const [mid, setMid] = useState<number | null>(null);
  const maxStart = Math.max(0, n - win);
  const a = mid == null ? maxStart : Math.min(maxStart, Math.max(0, Math.round(mid - win / 2)));
  const zoomed = win < n;
  const drag = useRef<{ x: number; a: number; w: number; moved: boolean } | null>(null);
  const move = (to: number) => { const st = Math.min(maxStart, Math.max(0, to)); setMid(st >= maxStart - 0.5 ? null : st + win / 2); };
  /** spread on the plot wrapper: drag sideways to pan (only while zoomed) */
  const pan = zoomed ? {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => { if (e.button === 0) drag.current = { x: e.clientX, a, w: e.currentTarget.clientWidth || 1, moved: false }; },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const d = drag.current;
      if (!d) return;
      const dx = e.clientX - d.x;
      if (!d.moved && Math.abs(dx) < 5) return;
      if (!d.moved) { d.moved = true; e.currentTarget.setPointerCapture?.(e.pointerId); }
      move(d.a - (dx / d.w) * win);
    },
    onPointerUp: () => { drag.current = null; },
    onPointerCancel: () => { drag.current = null; },
    onWheel: (e: React.WheelEvent<HTMLElement>) => {
      if (e.ctrlKey || e.metaKey || Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return;
      move(a + (e.deltaX / (e.currentTarget.clientWidth || 1)) * win);
    },
    onClickCapture: (e: React.MouseEvent) => { if (drag.current?.moved) { e.stopPropagation(); e.preventDefault(); } },
    style: { cursor: "grab" } as CSSProperties,
  } : {};
  const navigator = (labels?: string[]) => zoomed && n > 0 ? (
    <div className="mt-2 flex items-center gap-2 text-[11px] text-muted">
      <div className="relative h-2 flex-1 cursor-pointer rounded-full bg-slate-100"
        onPointerDown={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const at = ((e.clientX - r.left) / r.width) * n;
          const inThumb = at >= a && at <= a + win;
          const startA = inThumb ? a : at - win / 2;
          if (!inThumb) move(startA);
          const x0 = e.clientX, w = r.width;
          const el = e.currentTarget;
          el.setPointerCapture?.(e.pointerId);
          const mv = (ev: PointerEvent) => move(startA + ((ev.clientX - x0) / w) * n);
          const up = () => { el.removeEventListener("pointermove", mv); el.removeEventListener("pointerup", up); };
          el.addEventListener("pointermove", mv); el.addEventListener("pointerup", up);
        }}>
        <div className="absolute inset-y-0 rounded-full bg-primary-400/70 hover:bg-primary-500" style={{ left: `${(a / n) * 100}%`, width: `${(win / n) * 100}%` }} />
      </div>
      <span className="shrink-0 tabular-nums">{labels ? `${labels[a]} – ${labels[Math.min(n - 1, a + win - 1)]}` : `${a + 1}–${a + win} of ${n}`}</span>
    </div>
  ) : null;
  return { a, b: a + win, win, zoomed, pan, navigator };
}

// ------------------------------------------------------------------ axis helpers
function yAxis(ticks: number[], y: (v: number) => number, x0: number, x1: number, format: (v: number) => string) {
  return ticks.map((t) => (
    <g key={t}>
      <line x1={x0} x2={x1} y1={y(t)} y2={y(t)} stroke={GRID} strokeDasharray={t === 0 ? undefined : "2 4"} />
      <text x={x0 - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill={AXIS} style={{ fontVariantNumeric: "tabular-nums" }}>{format(t)}</text>
    </g>
  ));
}

function xLabels(labels: string[], x: (i: number) => number, y: number, minGap: number, width: number) {
  const n = labels.length;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(width / minGap))));
  return labels.map((l, i) => (i % every === 0 || i === n - 1) && (i === n - 1 || n - 1 - i >= every) && (
    <text key={i} x={x(i)} y={y} textAnchor={n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle"} fontSize="11" fill={AXIS}>{l}</text>
  ));
}

// ------------------------------------------------------------------ time series: lines / areas / stacked areas
export type Series = { key: string; name: string; values: (number | null)[]; color?: string; dashed?: boolean };

function TimeChartBase({
  labels, tipLabels, series, height = 240, format = fmtNum, area = false, stacked = false, empty, showLegend = true, fromZero = true,
}: {
  labels: string[]; tipLabels?: string[]; series: Series[]; height?: number; format?: (v: number) => string;
  area?: boolean; stacked?: boolean; empty?: ReactNode; showLegend?: boolean; fromZero?: boolean;
}) {
  height = Math.round(height * useChartFit());
  const allLabels = labels;
  const W = useTimeWindow(labels.length, useChartZoom());
  if (W.zoomed) {
    labels = labels.slice(W.a, W.b); tipLabels = tipLabels?.slice(W.a, W.b);
    series = series.map((s) => ({ ...s, values: s.values.slice(W.a, W.b) }));
  }
  const [wref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const col = (i: number, s: Series) => s.color ?? SERIES[i] ?? OTHER;
  const stacks = useMemo(() => {
    if (!stacked) return null;
    const acc = Array(n).fill(0);
    return series.map((s) => s.values.map((v, i) => { const lo = acc[i]; acc[i] += v ?? 0; return [lo, acc[i]] as [number, number]; }));
  }, [series, stacked, n]);
  const vals = stacked && stacks ? stacks.flatMap((s) => s.map((p) => p[1])) : series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const has = n > 0 && vals.length > 0 && (vals.some((v) => v > 0) || !fromZero);
  const hiRaw = Math.max(0, ...vals);
  let base = 0;
  let ticks = niceTicks(hiRaw);
  if (!fromZero && !stacked && vals.length) {
    const loRaw = Math.max(0, Math.min(...vals) - (hiRaw - Math.min(...vals)) * 0.1 - 1);
    const raw = niceTicks(Math.max(1e-6, hiRaw - loRaw));
    const step = raw[1] - raw[0] || 1;
    base = Math.floor(loRaw / step) * step;
    ticks = raw.map((tv) => base + tv).filter((tv) => tv <= hiRaw + step);
  }
  const top = ticks[ticks.length - 1] || 1;
  const pad = { l: 52, r: 12, t: 12, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v: number) => pad.t + ih - ((v - base) / ((top - base) || 1)) * ih;
  const gid = useMemo(() => `g${Math.random().toString(36).slice(2, 8)}`, []);

  const tipFor = (i: number) => {
    const total = series.reduce((a, s) => a + (s.values[i] ?? 0), 0);
    return (
      <>
        <TipHead>{(tipLabels ?? labels)[i]}</TipHead>
        {series.map((s, si) => <TipRow key={s.key} color={col(si, s)} dashed={s.dashed} label={s.name} value={s.values[i] == null ? "—" : format(s.values[i] as number)} />)}
        {series.length > 1 && stacked && <TipRow label="Total" value={format(total)} strong />}
      </>
    );
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.min(n - 1, Math.max(0, Math.round(n <= 1 ? 0 : ((e.clientX - box.left - pad.l) / iw) * (n - 1))));
    setHover(i);
    t.show(e, tipFor(i));
  };
  const line = (pts: [number, number][]) => pts.map(([px, py], k) => `${k ? "L" : "M"}${px},${py}`).join(" ");

  return (
    <div className="min-w-0">
      <div ref={(el) => { (wref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }} className="relative" {...W.pan}>
        {!has ? <Empty h={height}>{empty}</Empty> : (
          <svg width={width} height={height} role="img" className="block touch-none select-none rounded outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40" tabIndex={0}
            aria-label={series.map((s) => `${s.name}: total ${format(s.values.reduce<number>((a, b) => a + (b ?? 0), 0))}`).join("; ")}
            onPointerMove={onMove} onPointerLeave={() => { setHover(null); t.hide(); }}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              const i = Math.min(n - 1, Math.max(0, (hover ?? -1) + (e.key === "ArrowRight" ? 1 : -1)));
              setHover(i);
              const box = e.currentTarget.getBoundingClientRect();
              t.show({ clientX: box.left + x(i), clientY: box.top + pad.t + 20 }, tipFor(i));
            }}
            onBlur={() => { setHover(null); t.hide(); }}>
            <defs>
              {series.map((s, si) => (
                <linearGradient key={s.key} id={`${gid}-${si}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor={col(si, s)} stopOpacity={stacked ? 0.55 : 0.28} />
                  <stop offset="1" stopColor={col(si, s)} stopOpacity={stacked ? 0.35 : 0.02} />
                </linearGradient>
              ))}
            </defs>
            {yAxis(ticks, y, pad.l, width - pad.r, format)}
            {xLabels(labels, x, height - 7, 74, iw)}
            {series.map((s, si) => {
              if (stacked && stacks) {
                const upper = stacks[si].map(([, hi], i) => [x(i), y(hi)] as [number, number]);
                const lower = stacks[si].map(([lo], i) => [x(i), y(lo)] as [number, number]).reverse();
                return (
                  <g key={s.key}>
                    <path d={`${line(upper)} ${lower.map(([px, py]) => `L${px},${py}`).join(" ")} Z`} fill={`url(#${gid}-${si})`} />
                    <path d={line(upper)} fill="none" stroke={col(si, s)} strokeWidth="2" strokeLinejoin="round" />
                  </g>
                );
              }
              const segs: [number, number][][] = [];
              let cur: [number, number][] = [];
              s.values.slice(0, n).forEach((v, i) => { if (v == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([x(i), y(v)]); });
              if (cur.length) segs.push(cur);
              return (
                <g key={s.key}>
                  {segs.map((seg, k) => (
                    <g key={k}>
                      {area && seg.length > 1 && <path d={`${line(seg)} L${seg[seg.length - 1][0]},${y(base)} L${seg[0][0]},${y(base)} Z`} fill={`url(#${gid}-${si})`} />}
                      {seg.length === 1 ? <circle cx={seg[0][0]} cy={seg[0][1]} r="3.5" fill={col(si, s)} />
                        : <path d={line(seg)} fill="none" stroke={col(si, s)} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "5 4" : undefined} />}
                    </g>
                  ))}
                </g>
              );
            })}
            {hover !== null && (
              <g pointerEvents="none">
                <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="#94a3b8" strokeDasharray="3 3" />
                {series.map((s, si) => {
                  const v = stacked && stacks ? stacks[si][hover][1] : s.values[hover];
                  return v != null && <circle key={s.key} cx={x(hover)} cy={y(v)} r="4.5" fill={col(si, s)} stroke="#fff" strokeWidth="2" />;
                })}
              </g>
            )}
          </svg>
        )}
        {t.tip}
      </div>
      {W.navigator(allLabels)}
      {showLegend && series.length > 1 && has && (
        <div className="mt-3"><Legend items={series.map((s, si) => ({ label: s.name, color: col(si, s), shape: stacked || area ? "rect" : "line", dashed: s.dashed }))} /></div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ columns: single, stacked or grouped
function ColumnsBase({
  labels, tipLabels, series, height = 240, format = fmtNum, mode = "stacked", empty, showLegend = true, onSelect,
}: {
  labels: string[]; tipLabels?: string[]; series: Series[]; height?: number; format?: (v: number) => string;
  mode?: "stacked" | "grouped"; empty?: ReactNode; showLegend?: boolean; onSelect?: (i: number) => void;
}) {
  height = Math.round(height * useChartFit());
  const allLabels = labels;
  const W = useTimeWindow(labels.length, useChartZoom());
  if (W.zoomed) {
    labels = labels.slice(W.a, W.b); tipLabels = tipLabels?.slice(W.a, W.b);
    series = series.map((s) => ({ ...s, values: s.values.slice(W.a, W.b) }));
  }
  const [wref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const col = (i: number, s: Series) => s.color ?? SERIES[i] ?? OTHER;
  const totals = labels.map((_, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const peak = mode === "stacked" ? Math.max(0, ...totals) : Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0)));
  const ticks = niceTicks(peak);
  const top = ticks[ticks.length - 1] || 1;
  const pad = { l: 44, r: 8, t: 12, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const slot = iw / Math.max(1, n);
  const groupW = Math.max(3, Math.min(44, slot * 0.68));
  const y = (v: number) => pad.t + ih - (v / top) * ih;
  const cx = (i: number) => pad.l + slot * i + slot / 2;
  const has = peak > 0;

  const bar = (x0: number, w: number, y0: number, y1: number, color: string, roundTop: boolean, key: string, dim: boolean) => {
    const h = Math.max(0, y0 - y1);
    if (h <= 0) return null;
    const r = roundTop ? Math.min(4, w / 2, h) : 0;
    const d = `M${x0},${y0} V${y1 + r} Q${x0},${y1} ${x0 + r},${y1} H${x0 + w - r} Q${x0 + w},${y1} ${x0 + w},${y1 + r} V${y0} Z`;
    return <path key={key} d={d} fill={color} fillOpacity={dim ? 0.45 : 1} />;
  };
  const tipFor = (i: number) => (
    <>
      <TipHead>{(tipLabels ?? labels)[i]}</TipHead>
      {series.map((s, si) => <TipRow key={s.key} color={col(si, s)} label={s.name} value={format(s.values[i] ?? 0)} />)}
      {series.length > 1 && <TipRow label="Total" value={format(totals[i])} strong />}
    </>
  );

  return (
    <div className="min-w-0">
      <div ref={(el) => { (wref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }} className="relative" {...W.pan}>
        {!has ? <Empty h={height}>{empty}</Empty> : (
          <svg width={width} height={height} role="img" className="block select-none"
            aria-label={labels.map((l, i) => `${l}: ${format(totals[i])}`).join(", ")}
            onPointerLeave={() => { setHover(null); t.hide(); }}>
            {yAxis(ticks, y, pad.l, width - pad.r, format)}
            {xLabels(labels, cx, height - 7, 56, iw)}
            {labels.map((_, i) => {
              const dim = hover !== null && hover !== i;
              const x0 = cx(i) - groupW / 2;
              if (mode === "stacked") {
                let acc = 0;
                const visible = series.map((s) => (s.values[i] ?? 0) > 0);
                const lastIdx = visible.lastIndexOf(true);
                return (
                  <g key={i}>
                    {series.map((s, si) => {
                      const v = s.values[i] ?? 0;
                      const y0 = y(acc), y1 = y(acc + v);
                      acc += v;
                      // 2px surface gap between stacked segments
                      return bar(x0, groupW, y0 - (si > 0 && v > 0 && acc - v > 0 ? 1 : 0), y1 + (si !== lastIdx && v > 0 ? 1 : 0), col(si, s), si === lastIdx, `${i}-${s.key}`, dim);
                    })}
                  </g>
                );
              }
              const bw = Math.max(2, (groupW - (series.length - 1) * 2) / series.length);
              return <g key={i}>{series.map((s, si) => bar(x0 + si * (bw + 2), bw, y(0), y(s.values[i] ?? 0), col(si, s), true, `${i}-${s.key}`, dim))}</g>;
            })}
            {labels.map((l, i) => (
              <rect key={`hit-${i}`} x={pad.l + slot * i} y={pad.t} width={slot} height={ih} fill="transparent" tabIndex={0}
                aria-label={`${l}: ${format(totals[i])}`} style={{ cursor: onSelect ? "pointer" : "default", outline: "none" }}
                onPointerMove={(e) => { setHover(i); t.show(e, tipFor(i)); }}
                onFocus={(e) => { setHover(i); t.show(e.currentTarget, tipFor(i)); }}
                onBlur={() => { setHover(null); t.hide(); }}
                onClick={() => onSelect?.(i + (W.zoomed ? W.a : 0))} />
            ))}
          </svg>
        )}
        {t.tip}
      </div>
      {W.navigator(allLabels)}
      {showLegend && series.length > 1 && has && <div className="mt-3"><Legend items={series.map((s, si) => ({ label: s.name, color: col(si, s) }))} /></div>}
    </div>
  );
}

// ------------------------------------------------------------------ horizontal bars
export type HBar = { key: string; label: ReactNode; text?: string; value: number; color?: string; detail?: ReactNode; suffix?: ReactNode };

function HBarsBase({ data, format = fmtNum, max, empty }: { data: HBar[]; format?: (v: number) => string; max?: number; empty?: ReactNode }) {
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const top = max ?? Math.max(0, ...data.map((d) => d.value));
  if (!data.length) return <Empty h={120}>{empty}</Empty>;
  return (
    <div ref={t.ref} className="relative">
      <ul role="list" className="space-y-2">
        {data.map((d, i) => {
          const tip = (
            <>
              <TipHead>{d.text ?? d.label}</TipHead>
              <TipRow color={d.color ?? SERIES[0]} label="Value" value={format(d.value)} />
              {top > 0 && <TipRow label="Share of the largest" value={`${Math.round((d.value / top) * 100)}%`} />}
              {d.detail}
            </>
          );
          return (
            <li key={d.key} tabIndex={0}
              className={`grid cursor-default grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 rounded-md px-1 py-0.5 text-sm outline-none transition-colors ${hover === d.key ? "bg-slate-50" : ""}`}
              onPointerMove={(e) => { setHover(d.key); t.show(e, tip); }} onPointerLeave={() => { setHover(null); t.hide(); }}
              onFocus={(e) => { setHover(d.key); t.show(e.currentTarget, tip); }} onBlur={() => { setHover(null); t.hide(); }}>
              <span className="truncate text-ink">{d.label}</span>
              <span className="relative block h-2.5 overflow-hidden rounded-full bg-slate-100">
                <span className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500"
                  style={{ width: `${top ? Math.max((d.value / top) * 100, d.value > 0 ? 2 : 0) : 0}%`, background: d.color ?? SERIES[i < 1 ? 0 : 0], opacity: hover && hover !== d.key ? 0.55 : 1 }} />
              </span>
              <span className="text-right tabular-nums text-ink">{format(d.value)}{d.suffix}</span>
            </li>
          );
        })}
      </ul>
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ donut
export type Slice = { key: string; label: string; value: number; color?: string; detail?: ReactNode };

function DonutBase({
  data, size = 180, thickness = 26, format = fmtNum, centerLabel = "Total", centerValue, legend = true, onSelect,
}: {
  data: Slice[]; size?: number; thickness?: number; format?: (v: number) => string; centerLabel?: string; centerValue?: string;
  legend?: boolean; onSelect?: (key: string) => void;
}) {
  const zk = useChartZoom();
  size = Math.round(size * zk); thickness = Math.round(thickness * zk);
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const total = data.reduce((a, d) => a + d.value, 0);
  const r = size / 2 - 6, ri = r - thickness;
  const c = size / 2;
  const col = (d: Slice, i: number) => d.color ?? SERIES[i] ?? OTHER;
  let ang = -Math.PI / 2;
  const gap = data.filter((d) => d.value > 0).length > 1 ? 0.012 : 0;
  const arcs = data.map((d, i) => {
    const a = total ? (d.value / total) * Math.PI * 2 : 0;
    const a0 = ang + gap, a1 = ang + a - gap;
    ang += a;
    return { d, i, a0, a1, mid: (a0 + a1) / 2, frac: total ? d.value / total : 0 };
  });
  const path = (a0: number, a1: number, ro: number, rin: number) => {
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rr: number, a: number) => `${c + rr * Math.cos(a)},${c + rr * Math.sin(a)}`;
    return `M${p(ro, a0)} A${ro},${ro} 0 ${large} 1 ${p(ro, a1)} L${p(rin, a1)} A${rin},${rin} 0 ${large} 0 ${p(rin, a0)} Z`;
  };
  const tipFor = (d: Slice, i: number) => (
    <>
      <TipHead>{d.label}</TipHead>
      <TipRow color={col(d, i)} label="Value" value={format(d.value)} />
      <TipRow label="Share" value={total ? `${((d.value / total) * 100).toFixed(1)}%` : "—"} />
      {d.detail}
    </>
  );
  const hv = data.find((d) => d.key === hover);
  if (!total) return <Empty h={size}>Nothing to show for this period.</Empty>;
  return (
    <div ref={t.ref} className="relative flex flex-wrap items-center gap-5">
      <svg width={size} height={size} role="img" aria-label={data.map((d) => `${d.label}: ${format(d.value)}`).join(", ")} className="shrink-0"
        onPointerLeave={() => { setHover(null); t.hide(); }}>
        {arcs.map(({ d, i, a0, a1 }) => d.value > 0 && (
          <path key={d.key} d={path(a0, Math.max(a0 + 0.001, a1), hover === d.key ? r + 4 : r, ri)} fill={col(d, i)}
            fillOpacity={hover && hover !== d.key ? 0.4 : 1} tabIndex={0} style={{ transition: "all .15s", cursor: onSelect ? "pointer" : "default", outline: "none" }}
            onPointerMove={(e) => { setHover(d.key); t.show(e, tipFor(d, i)); }}
            onFocus={(e) => { setHover(d.key); t.show(e.currentTarget, tipFor(d, i)); }} onBlur={() => { setHover(null); t.hide(); }}
            onClick={() => onSelect?.(d.key)} />
        ))}
        <text x={c} y={c - 4} textAnchor="middle" fontSize={size * 0.15} fontWeight="700" fill={INK} style={{ fontVariantNumeric: "tabular-nums" }}>
          {hv ? format(hv.value) : (centerValue ?? format(total))}
        </text>
        <text x={c} y={c + 14} textAnchor="middle" fontSize="11" fill={AXIS}>{hv ? hv.label.slice(0, 18) : centerLabel}</text>
      </svg>
      {legend && (
        <ul className="min-w-[9rem] flex-1 space-y-1.5 text-xs">
          {arcs.map(({ d, i, frac }) => (
            <li key={d.key} className={`flex items-center gap-2 rounded px-1 ${hover === d.key ? "bg-slate-50" : ""}`}
              onPointerEnter={() => setHover(d.key)} onPointerLeave={() => setHover(null)}>
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: col(d, i) }} />
              <span className="min-w-0 flex-1 truncate text-ink/80">{d.label}</span>
              <span className="tabular-nums text-ink">{format(d.value)}</span>
              <span className="w-10 text-right tabular-nums text-muted">{(frac * 100).toFixed(0)}%</span>
            </li>
          ))}
        </ul>
      )}
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ sparkline
export function Sparkline({
  values, labels, format = fmtNum, color = SERIES[0], width = 120, height = 34, area = true, title,
}: { values: (number | null)[]; labels?: string[]; format?: (v: number) => string; color?: string; width?: number; height?: number; area?: boolean; title?: string }) {
  const t = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const pts = values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v != null);
  const gid = useMemo(() => `s${Math.random().toString(36).slice(2, 8)}`, []);
  if (pts.length < 2) return <svg width={width} height={height} aria-hidden><line x1="0" x2={width} y1={height - 4} y2={height - 4} stroke={GRID} strokeDasharray="3 3" /></svg>;
  const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
  const n = values.length;
  const x = (i: number) => 2 + (i * (width - 4)) / Math.max(1, n - 1);
  const y = (v: number) => 3 + (height - 6) - ((v - lo) / ((hi - lo) || 1)) * (height - 6);
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i)},${y(p.v)}`).join(" ");
  return (
    <div ref={t.ref} className="relative inline-block">
      <svg width={width} height={height} role="img" aria-label={`${title ?? "Trend"}: from ${format(pts[0].v)} to ${format(pts[pts.length - 1].v)}`}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const i = Math.min(n - 1, Math.max(0, Math.round(((e.clientX - box.left - 2) / (width - 4)) * (n - 1))));
          setHover(i);
          t.show(e, <><TipHead>{labels?.[i] ?? `Point ${i + 1}`}</TipHead><TipRow color={color} label={title ?? "Value"} value={values[i] == null ? "—" : format(values[i] as number)} /></>);
        }}
        onPointerLeave={() => { setHover(null); t.hide(); }}>
        <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={color} stopOpacity="0.25" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
        {area && <path d={`${d} L${x(pts[pts.length - 1].i)},${height} L${x(pts[0].i)},${height} Z`} fill={`url(#${gid})`} />}
        <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && values[hover] != null && <circle cx={x(hover)} cy={y(values[hover] as number)} r="3.5" fill={color} stroke="#fff" strokeWidth="1.5" />}
      </svg>
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ funnel
function FunnelBase({ steps, format = fmtNum }: { steps: { key: string; label: string; value: number; hint?: string }[]; format?: (v: number) => string }) {
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const top = Math.max(1, ...steps.map((s) => s.value));
  const first = steps[0]?.value || 0;
  return (
    <div ref={t.ref} className="relative space-y-1.5">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : null;
        const conv = prev ? s.value / prev : null;
        const w = Math.max(6, (s.value / top) * 100);
        const color = SEQ[Math.min(SEQ.length - 1, 2 + i)];
        const tip = (
          <>
            <TipHead sub={s.hint}>{s.label}</TipHead>
            <TipRow color={color} label="Posts" value={format(s.value)} />
            {conv != null && <TipRow label={`From "${steps[i - 1].label}"`} value={`${(conv * 100).toFixed(0)}%`} />}
            {first > 0 && i > 0 && <TipRow label="Of all planned" value={`${((s.value / first) * 100).toFixed(0)}%`} />}
          </>
        );
        return (
          <div key={s.key} tabIndex={0} className="group grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-3 text-sm outline-none"
            onPointerMove={(e) => { setHover(s.key); t.show(e, tip); }} onPointerLeave={() => { setHover(null); t.hide(); }}
            onFocus={(e) => { setHover(s.key); t.show(e.currentTarget, tip); }} onBlur={() => { setHover(null); t.hide(); }}>
            <span className="truncate text-ink/80">{s.label}</span>
            <div className="flex h-8 justify-center">
              <div className="flex items-center justify-center rounded-md text-xs font-semibold text-white transition-all duration-500"
                style={{ width: `${w}%`, background: color, opacity: hover && hover !== s.key ? 0.55 : 1 }}>
                {w > 18 ? format(s.value) : ""}
              </div>
            </div>
            <span className="text-right text-xs tabular-nums text-muted">{conv == null ? "" : `${(conv * 100).toFixed(0)}%`}</span>
          </div>
        );
      })}
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ heatmap (weekday × hour)
export type Cell = { weekday: number; hour: number; value: number; posts: number; enough?: boolean; detail?: ReactNode };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function HeatmapBase({ cells, format = fmtPct, valueLabel = "Engagement rate", minPosts = 1 }: { cells: Cell[]; format?: (v: number) => string; valueLabel?: string; minPosts?: number }) {
  const zk = useChartZoom();
  const [wref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const by = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]));
  const okCell = (c: Cell) => c.enough ?? c.posts >= minPosts;
  const max = Math.max(0, ...cells.filter(okCell).map((c) => c.value));
  const left = 34, topPad = 18;
  const cw = Math.max(9, (width - left) / 24), ch = Math.round(22 * zk);
  const height = topPad + ch * 7 + 2;
  const step = (v: number) => SEQ[Math.min(SEQ.length - 1, Math.floor((max ? v / max : 0) * (SEQ.length - 0.001)))];
  return (
    <div className="min-w-0">
      <div ref={(el) => { (wref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }} className="relative overflow-x-auto">
        <svg width={Math.max(width, left + 24 * 9)} height={height} role="img" aria-label={`${valueLabel} by weekday and hour`} onPointerLeave={() => { setHover(null); t.hide(); }}>
          <defs><pattern id="hm-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="#cbd5e1" strokeWidth="1.2" /></pattern></defs>
          {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => <text key={h} x={left + h * cw + cw / 2} y={11} textAnchor="middle" fontSize="10" fill={AXIS}>{String(h).padStart(2, "0")}</text>)}
          {DAYS.map((d, wd) => (
            <g key={d}>
              <text x={0} y={topPad + wd * ch + ch / 2} dy="0.32em" fontSize="11" fill={AXIS}>{d}</text>
              {Array.from({ length: 24 }, (_, h) => {
                const c = by.get(`${wd}-${h}`);
                const k = `${wd}-${h}`;
                const ok = c && okCell(c);
                const tip = (
                  <>
                    <TipHead>{`${d} ${String(h).padStart(2, "0")}:00–${String((h + 1) % 24).padStart(2, "0")}:00`}</TipHead>
                    {c ? <>
                      <TipRow color={ok ? step(c.value) : "#cbd5e1"} label={valueLabel} value={format(c.value)} />
                      <TipRow label="Posts" value={c.posts} />
                      {c.detail}
                      {!ok && <div className="mt-1 text-[11px] text-muted">Too few posts to be reliable.</div>}
                    </> : <div className="text-muted">No posts at this time.</div>}
                  </>
                );
                return (
                  <rect key={h} x={left + h * cw + 1} y={topPad + wd * ch + 1} width={cw - 2} height={ch - 2} rx="3"
                    fill={!c ? "#f8fafc" : ok ? step(c.value) : "url(#hm-hatch)"} stroke={hover === k ? INK : c ? "none" : GRID} strokeWidth={hover === k ? 1.5 : 1}
                    onPointerMove={(e) => { setHover(k); t.show(e, tip); }} />
                );
              })}
            </g>
          ))}
        </svg>
        {t.tip}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px] text-muted">
        <span>Lower</span>
        {SEQ.map((c) => <span key={c} className="h-2.5 w-5 rounded-sm" style={{ background: c }} />)}
        <span>Higher</span>
        <svg width="14" height="10" aria-hidden className="ml-3"><rect width="14" height="10" rx="2" fill="url(#hm-hatch)" stroke={GRID} /></svg>
        <span>Too few posts</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ scatter / bubble
export type Point = { key: string; x: number; y: number; size?: number; label: string; color?: string; group?: string; detail?: ReactNode; onClick?: () => void };

function ScatterBase({
  points, height = 260, xLabel, yLabel, formatX = fmtNum, formatY = fmtNum, sizeLabel, formatSize = fmtNum, legend,
}: {
  points: Point[]; height?: number; xLabel: string; yLabel: string; formatX?: (v: number) => string; formatY?: (v: number) => string;
  sizeLabel?: string; formatSize?: (v: number) => string; legend?: { label: string; color: string }[];
}) {
  height = Math.round(height * useChartZoom());
  const [wref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  if (!points.length) return <Empty h={height}>No published posts with metrics yet.</Empty>;
  const xt = niceTicks(Math.max(0, ...points.map((p) => p.x)));
  const yt = niceTicks(Math.max(0, ...points.map((p) => p.y)));
  const smax = Math.max(1, ...points.map((p) => p.size ?? 1));
  const pad = { l: 68, r: 16, t: 12, b: 40 };
  const iw = Math.max(10, width - pad.l - pad.r), ih = height - pad.t - pad.b;
  const X = (v: number) => pad.l + (v / (xt[xt.length - 1] || 1)) * iw;
  const Y = (v: number) => pad.t + ih - (v / (yt[yt.length - 1] || 1)) * ih;
  const R = (s?: number) => 5 + Math.sqrt((s ?? 1) / smax) * 14;
  const tipFor = (p: Point) => (
    <>
      <TipHead sub={p.group}>{p.label}</TipHead>
      <TipRow color={p.color ?? SERIES[0]} label={xLabel} value={formatX(p.x)} />
      <TipRow label={yLabel} value={formatY(p.y)} />
      {sizeLabel && p.size != null && <TipRow label={sizeLabel} value={formatSize(p.size)} />}
      {p.detail}
    </>
  );
  return (
    <div className="min-w-0">
      <div ref={(el) => { (wref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }} className="relative">
        <svg width={width} height={height} role="img" aria-label={`${yLabel} against ${xLabel} for ${points.length} items`} onPointerLeave={() => { setHover(null); t.hide(); }}>
          {yAxis(yt, Y, pad.l, width - pad.r, formatY)}
          {xt.map((v) => <text key={v} x={X(v)} y={pad.t + ih + 16} textAnchor="middle" fontSize="11" fill={AXIS}>{formatX(v)}</text>)}
          <text x={pad.l + iw / 2} y={height - 4} textAnchor="middle" fontSize="11" fill={AXIS}>{xLabel} →</text>
          <text transform={`translate(12 ${pad.t + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill={AXIS}>{yLabel} →</text>
          {[...points].sort((a, b) => (b.size ?? 1) - (a.size ?? 1)).map((p) => (
            <g key={p.key} tabIndex={0} style={{ cursor: p.onClick ? "pointer" : "default", outline: "none" }}
              onPointerMove={(e) => { setHover(p.key); t.show(e, tipFor(p)); }}
              onFocus={(e) => { setHover(p.key); t.show(e.currentTarget, tipFor(p)); }} onBlur={() => { setHover(null); t.hide(); }}
              onClick={p.onClick}>
              <circle cx={X(p.x)} cy={Y(p.y)} r={Math.max(12, R(p.size))} fill="transparent" />
              <circle cx={X(p.x)} cy={Y(p.y)} r={R(p.size) + (hover === p.key ? 2 : 0)} fill={p.color ?? SERIES[0]}
                fillOpacity={hover && hover !== p.key ? 0.25 : 0.7} stroke="#fff" strokeWidth="2" />
            </g>
          ))}
        </svg>
        {t.tip}
      </div>
      {legend && legend.length > 1 && <div className="mt-2"><Legend items={legend.map((l) => ({ ...l, shape: "dot" as const }))} /></div>}
    </div>
  );
}

// ------------------------------------------------------------------ radar (normalised comparison)
function RadarBase({
  axes, series, size = 260,
}: {
  axes: { key: string; label: string; format: (v: number) => string }[];
  series: { key: string; name: string; color?: string; values: Record<string, number> }[];
  size?: number;
}) {
  size = Math.round(size * useChartZoom());
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const padX = 56;
  const c = size / 2, r = size / 2 - 34;
  const max: Record<string, number> = {};
  axes.forEach((a) => { max[a.key] = Math.max(1e-9, ...series.map((s) => s.values[a.key] ?? 0)); });
  const pt = (ai: number, f: number) => {
    const a = -Math.PI / 2 + (ai * 2 * Math.PI) / axes.length;
    return [padX + c + r * f * Math.cos(a), c + r * f * Math.sin(a)] as const;
  };
  if (!series.length || axes.length < 3) return <Empty h={size}>Not enough data to compare yet.</Empty>;
  return (
    <div ref={t.ref} className="relative flex flex-col items-center">
      <svg width={size + padX * 2} height={size} role="img" aria-label="Comparison across measures" onPointerLeave={() => { setHover(null); t.hide(); }}>
        {[0.25, 0.5, 0.75, 1].map((f) => <polygon key={f} points={axes.map((_, i) => pt(i, f).join(",")).join(" ")} fill="none" stroke={GRID} />)}
        {axes.map((a, i) => {
          const [lx, ly] = pt(i, 1.14);
          return (
            <g key={a.key}>
              <line x1={padX + c} y1={c} x2={pt(i, 1)[0]} y2={pt(i, 1)[1]} stroke={GRID} />
              <text x={lx} y={ly} dy="0.32em" textAnchor={Math.abs(lx - padX - c) < 8 ? "middle" : lx > padX + c ? "start" : "end"} fontSize="11" fill={AXIS}>{a.label}</text>
            </g>
          );
        })}
        {series.map((s, si) => {
          const color = s.color ?? SERIES[si] ?? OTHER;
          const tip = (
            <>
              <TipHead>{s.name}</TipHead>
              {axes.map((a) => <TipRow key={a.key} color={color} label={a.label} value={a.format(s.values[a.key] ?? 0)} />)}
            </>
          );
          return (
            <polygon key={s.key} points={axes.map((a, i) => pt(i, (s.values[a.key] ?? 0) / max[a.key]).join(",")).join(" ")}
              fill={color} fillOpacity={hover === s.key ? 0.35 : hover ? 0.04 : 0.12} stroke={color} strokeWidth={hover === s.key ? 2.5 : 1.8}
              strokeOpacity={hover && hover !== s.key ? 0.35 : 1} strokeLinejoin="round"
              onPointerMove={(e) => { setHover(s.key); t.show(e, tip); }} />
          );
        })}
      </svg>
      <Legend items={series.map((s, si) => ({ label: s.name, color: s.color ?? SERIES[si] ?? OTHER }))} />
      <p className="mt-1 text-[11px] text-muted">Each measure is scaled to the best channel (outer ring = best).</p>
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ gauge
function GaugeBase({ value, max = 100, label, sub, size = 180, format = (v: number) => String(Math.round(v)), detail }: {
  value: number | null; max?: number; label: string; sub?: string; size?: number; format?: (v: number) => string; detail?: ReactNode;
}) {
  size = Math.round(size * useChartZoom());
  const t = useTip();
  const r = size / 2 - 14, cx = size / 2, cy = size / 2 + 4;
  const frac = Math.max(0, Math.min(1, (value ?? 0) / max));
  const a = (f: number) => Math.PI + f * Math.PI;
  const p = (f: number) => `${cx + r * Math.cos(a(f))},${cy + r * Math.sin(a(f))}`;
  const gid = useMemo(() => `ga${Math.random().toString(36).slice(2, 8)}`, []);
  const tip = <><TipHead>{label}</TipHead><TipRow color={SERIES[0]} label="Score" value={value == null ? "—" : `${format(value)} / ${format(max)}`} />{detail}</>;
  return (
    <div ref={t.ref} className="relative inline-block" onPointerMove={(e) => t.show(e, tip)} onPointerLeave={t.hide}>
      <svg width={size} height={size / 2 + 30} role="img" aria-label={`${label}: ${value == null ? "no score" : format(value)}`}>
        <defs><linearGradient id={gid} x1="0" x2="1"><stop offset="0" stopColor="#075492" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
        <path d={`M${p(0)} A${r},${r} 0 0 1 ${p(1)}`} fill="none" stroke={GRID} strokeWidth="14" strokeLinecap="round" />
        {value != null && frac > 0 && <path d={`M${p(0)} A${r},${r} 0 0 1 ${p(frac)}`} fill="none" stroke={`url(#${gid})`} strokeWidth="14" strokeLinecap="round" />}
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize={size * 0.2} fontWeight="700" fill={INK}>{value == null ? "—" : format(value)}</text>
        <text x={cx} y={cy + 12} textAnchor="middle" fontSize="11" fill={AXIS}>{sub ?? label}</text>
      </svg>
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ 3D: isometric skyline (rows × columns of bars)
/**
 * Isometric 3D column grid, e.g. channels × weeks. Height = value. Drag or use the arrows to turn it. The flat
 * charts beside it carry the exact reading; this one shows the overall shape, and every bar still has its popup.
 */
function Skyline3DBase({
  rows, cols, values, colors, format = fmtNum, height = 340, valueLabel = "Value",
}: {
  rows: { key: string; label: string }[]; cols: string[]; values: number[][]; colors?: string[]; format?: (v: number) => string;
  height?: number; valueLabel?: string;
}) {
  height = Math.round(height * useChartZoom());
  const [wref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const [turn, setTurn] = useState(0); // 0..3 quarter turns
  const drag = useRef<{ x: number; t: number } | null>(null);
  const R = rows.length, C = cols.length;
  const peak = Math.max(1, ...values.flat());
  if (!R || !C || peak <= 0) return <Empty h={height}>Nothing published in this period yet.</Empty>;
  // rotate the grid by quarter turns
  const at = (r: number, c: number) => {
    switch (turn % 4) {
      case 1: return { gx: R - 1 - r, gy: c };
      case 2: return { gx: C - 1 - c, gy: R - 1 - r };
      case 3: return { gx: r, gy: C - 1 - c };
      default: return { gx: c, gy: r };
    }
  };
  const GX = turn % 2 ? R : C, GY = turn % 2 ? C : R;
  const maxH = height * 0.42;
  const tile = Math.max(8, Math.min(46, (width - 40) / ((GX + GY) * 0.866 * 1.05), (height - maxH - 44) / ((GX + GY) * 0.5)));
  const hx = tile * 0.866, hy = tile * 0.5;
  const ox = width / 2 - ((GX - GY) * hx) / 2;
  const oy = Math.max(maxH + 14, (height - (GX + GY) * hy + maxH) / 2);
  const iso = (gx: number, gy: number, z = 0) => [ox + (gx - gy) * hx, oy + (gx + gy) * hy - z] as const;
  const bars: { r: number; c: number; gx: number; gy: number }[] = [];
  for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) bars.push({ r, c, ...at(r, c) });
  bars.sort((a, b) => (a.gx + a.gy) - (b.gx + b.gy));
  const poly = (pts: (readonly [number, number])[]) => pts.map((p) => p.join(",")).join(" ");
  const inset = 0.16;
  return (
    <div className="min-w-0">
      <div ref={(el) => { (wref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }}
        className="relative cursor-grab select-none rounded-xl bg-gradient-to-b from-slate-50 to-white active:cursor-grabbing"
        onPointerDown={(e) => { drag.current = { x: e.clientX, t: turn }; }}
        onPointerUp={() => { drag.current = null; }}
        onPointerMove={(e) => { if (drag.current) { const d = Math.round((e.clientX - drag.current.x) / 90); setTurn(((drag.current.t + d) % 4 + 4) % 4); } }}>
        <svg width={width} height={height} role="img" aria-label={`${valueLabel} by ${rows.length} rows and ${cols.length} columns, shown in 3D`}
          onPointerLeave={() => { setHover(null); t.hide(); }}>
          {/* floor */}
          <polygon points={poly([iso(0, 0), iso(GX, 0), iso(GX, GY), iso(0, GY)])} fill="#f1f5f9" stroke={GRID} />
          {Array.from({ length: GX + 1 }, (_, i) => <line key={`gx${i}`} x1={iso(i, 0)[0]} y1={iso(i, 0)[1]} x2={iso(i, GY)[0]} y2={iso(i, GY)[1]} stroke={GRID} />)}
          {Array.from({ length: GY + 1 }, (_, i) => <line key={`gy${i}`} x1={iso(0, i)[0]} y1={iso(0, i)[1]} x2={iso(GX, i)[0]} y2={iso(GX, i)[1]} stroke={GRID} />)}
          {bars.map(({ r, c, gx, gy }) => {
            const v = values[r]?.[c] ?? 0;
            const h = (v / peak) * maxH;
            const color = colors?.[r] ?? SERIES[r] ?? OTHER;
            const k = `${r}-${c}`;
            const dim = hover && hover !== k && !hover.startsWith(`${r}-`) ;
            const x0 = gx + inset, x1 = gx + 1 - inset, y0 = gy + inset, y1 = gy + 1 - inset;
            const topF = [iso(x0, y0, h), iso(x1, y0, h), iso(x1, y1, h), iso(x0, y1, h)];
            const left = [iso(x0, y1, 0), iso(x1, y1, 0), iso(x1, y1, h), iso(x0, y1, h)];
            const right = [iso(x1, y0, 0), iso(x1, y1, 0), iso(x1, y1, h), iso(x1, y0, h)];
            const tip = (
              <>
                <TipHead sub={cols[c]}>{rows[r].label}</TipHead>
                <TipRow color={color} label={valueLabel} value={format(v)} />
                <TipRow label={`Share of ${cols[c]}`} value={(() => { const tot = rows.reduce((a, _, ri) => a + (values[ri]?.[c] ?? 0), 0); return tot ? `${Math.round((v / tot) * 100)}%` : "—"; })()} />
              </>
            );
            return (
              <g key={k} opacity={dim ? 0.35 : 1} onPointerMove={(e) => { if (!drag.current) { setHover(k); t.show(e, tip); } }}>
                {h > 0.5 ? <>
                  <polygon points={poly(left)} fill={shade(color, -0.28)} />
                  <polygon points={poly(right)} fill={shade(color, -0.12)} />
                  <polygon points={poly(topF)} fill={hover === k ? shade(color, 0.25) : color} stroke={shade(color, 0.35)} strokeWidth="0.6" />
                </> : <polygon points={poly(topF)} fill={shade(color, 0.7)} opacity="0.6" />}
              </g>
            );
          })}
        </svg>
        <div className="absolute right-2 top-2 flex gap-1">
          <button type="button" aria-label="Turn left" className="rounded-md border border-line bg-white px-2 py-0.5 text-xs text-muted hover:text-ink" onClick={() => setTurn((x) => (x + 3) % 4)}>⟲</button>
          <button type="button" aria-label="Turn right" className="rounded-md border border-line bg-white px-2 py-0.5 text-xs text-muted hover:text-ink" onClick={() => setTurn((x) => (x + 1) % 4)}>⟳</button>
        </div>
        {t.tip}
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <Legend items={rows.map((r, i) => ({ label: r.label, color: colors?.[i] ?? SERIES[i] ?? OTHER }))} />
        <span className="text-[11px] text-muted">Drag sideways or use ⟲ ⟳ to turn</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 3D: tilted pie with depth
function Pie3DBase({ data, size = 300, depth = 22, format = fmtNum }: { data: Slice[]; size?: number; depth?: number; format?: (v: number) => string }) {
  const zk = useChartZoom();
  size = Math.round(size * zk); depth = Math.round(depth * zk);
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const total = data.reduce((a, d) => a + d.value, 0);
  if (!total) return <Empty h={size * 0.6}>Nothing to show for this period.</Empty>;
  const rx = size / 2 - 10, ry = rx * 0.5, cx = size / 2, cy = ry + 14;
  const H = ry * 2 + depth + 30;
  const col = (d: Slice, i: number) => d.color ?? SERIES[i] ?? OTHER;
  let a = -Math.PI / 2;
  const slices = data.filter((d) => d.value > 0).map((d) => {
    const i = data.indexOf(d);
    const span = (d.value / total) * Math.PI * 2;
    const s = { d, i, a0: a, a1: a + span, mid: a + span / 2 };
    a += span;
    return s;
  });
  const P = (ang: number, off = 0, lift = 0) => [cx + rx * Math.cos(ang) + off * Math.cos(ang), cy + ry * Math.sin(ang) + off * 0.5 * Math.sin(ang) - lift] as const;
  const topPath = (a0: number, a1: number, o: number) => {
    const [x0, y0] = P(a0, 0), [x1, y1] = P(a1, 0);
    const dx = o * Math.cos((a0 + a1) / 2), dy = o * 0.5 * Math.sin((a0 + a1) / 2);
    return `M${cx + dx},${cy + dy} L${x0 + dx},${y0 + dy} A${rx},${ry} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1 + dx},${y1 + dy} Z`;
  };
  const sidePath = (a0: number, a1: number, o: number) => {
    // only the front half of the rim (angles 0..π, where sin > 0) is visible
    const pts: string[] = [];
    const segs: [number, number][] = [];
    for (let k = -1; k <= 1; k++) {
      const f0 = k * 2 * Math.PI, f1 = f0 + Math.PI; // visible window [0, π] + 2πk
      const s = Math.max(a0, f0), e = Math.min(a1, f1);
      if (e > s) segs.push([s, e]);
    }
    const dx = o * Math.cos((a0 + a1) / 2), dy = o * 0.5 * Math.sin((a0 + a1) / 2);
    for (const [s, e] of segs) {
      const [x0, y0] = P(s), [x1, y1] = P(e);
      pts.push(`M${x0 + dx},${y0 + dy} A${rx},${ry} 0 0 1 ${x1 + dx},${y1 + dy} L${x1 + dx},${y1 + dy + depth} A${rx},${ry} 0 0 0 ${x0 + dx},${y0 + dy + depth} Z`);
    }
    return pts.join(" ");
  };
  const tipFor = (d: Slice, i: number) => (
    <>
      <TipHead>{d.label}</TipHead>
      <TipRow color={col(d, i)} label="Value" value={format(d.value)} />
      <TipRow label="Share" value={`${((d.value / total) * 100).toFixed(1)}%`} />
      {d.detail}
    </>
  );
  return (
    <div ref={t.ref} className="relative flex flex-wrap items-center gap-5">
      <svg width={size} height={H} role="img" aria-label={data.map((d) => `${d.label}: ${format(d.value)}`).join(", ")} onPointerLeave={() => { setHover(null); t.hide(); }}>
        <ellipse cx={cx} cy={cy + depth + 8} rx={rx * 0.95} ry={ry * 0.9} fill="#0f172a" opacity="0.06" />
        {slices.map(({ d, i, a0, a1 }) => (
          <path key={`s-${d.key}`} d={sidePath(a0, a1, hover === d.key ? 10 : 0)} fill={shade(col(d, i), -0.3)}
            onPointerMove={(e) => { setHover(d.key); t.show(e, tipFor(d, i)); }} />
        ))}
        {slices.map(({ d, i, a0, a1 }) => (
          <path key={`t-${d.key}`} d={topPath(a0, a1, hover === d.key ? 10 : 0)} fill={col(d, i)} stroke="#fff" strokeWidth="1.5"
            opacity={hover && hover !== d.key ? 0.6 : 1} style={{ transition: "all .15s" }} tabIndex={0}
            onPointerMove={(e) => { setHover(d.key); t.show(e, tipFor(d, i)); }}
            onFocus={(e) => { setHover(d.key); t.show(e.currentTarget, tipFor(d, i)); }} onBlur={() => { setHover(null); t.hide(); }} />
        ))}
      </svg>
      <ul className="min-w-[9rem] flex-1 space-y-1.5 text-xs">
        {data.map((d, i) => (
          <li key={d.key} className={`flex items-center gap-2 rounded px-1 ${hover === d.key ? "bg-slate-50" : ""}`} onPointerEnter={() => setHover(d.key)} onPointerLeave={() => setHover(null)}>
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: col(d, i) }} />
            <span className="min-w-0 flex-1 truncate text-ink/80">{d.label}</span>
            <span className="tabular-nums text-ink">{format(d.value)}</span>
            <span className="w-10 text-right tabular-nums text-muted">{((d.value / total) * 100).toFixed(0)}%</span>
          </li>
        ))}
      </ul>
      {t.tip}
    </div>
  );
}

// ------------------------------------------------------------------ table view (every chart's numbers without hovering)
export function DataTable({ columns, rows }: { columns: { key: string; label: string; align?: "right" }[]; rows: Record<string, ReactNode>[] }) {
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-line">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-50 text-muted">
          <tr>{columns.map((c) => <th key={c.key} className={`px-3 py-2 font-medium ${c.align === "right" ? "text-right" : "text-left"}`}>{c.label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c.key} className={`px-3 py-1.5 tabular-nums ${c.align === "right" ? "text-right" : ""}`}>{r[c.key]}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}
export const TimeChart = withZoom(TimeChartBase, { mode: "self" });
export const Columns = withZoom(ColumnsBase, { mode: "self" });
export const HBars = withZoom(HBarsBase);
export const Donut = withZoom(DonutBase);
export const Funnel = withZoom(FunnelBase);
export const Heatmap = withZoom(HeatmapBase);
export const Scatter = withZoom(ScatterBase);
export const Radar = withZoom(RadarBase);
export const Gauge = withZoom(GaugeBase);
export const Skyline3D = withZoom(Skyline3DBase, { pan: false });
export const Pie3D = withZoom(Pie3DBase);
