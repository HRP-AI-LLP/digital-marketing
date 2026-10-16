// Small dependency-free SVG charts. Brand palette: primary-600 main series, secondary-500 comparison
// (dashed, so it never relies on colour alone), slate grid lines. One y-axis per chart.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export const MAIN = "#086fb8";
export const COMPARE = "#06a6c7";
const GRID = "#e2e8f0";
const AXIS_TEXT = "#64748b";
const INK = "#0f172a";

export const fmtNum = (v: number) =>
  Math.abs(v) >= 10000 ? new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 }).format(v)
    : new Intl.NumberFormat("en-IN", { maximumFractionDigits: v % 1 ? 1 : 0 }).format(v);
export const fmtPct = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;
export const fmtDuration = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);

function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => { if (ref.current) setW(ref.current.clientWidth || fallback); }, [fallback]);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((e) => setW(Math.max(200, e[0].contentRect.width)));
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

export function ChartFrame({ title, description, legend, children }: { title: string; description?: string; legend?: ReactNode; children: ReactNode }) {
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2">
        <span className="text-sm font-semibold text-ink">{title}</span>
        {description && <span className="block text-xs text-muted">{description}</span>}
      </figcaption>
      {children}
      {legend && <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">{legend}</div>}
    </figure>
  );
}

export type LineSeries = { name: string; values: number[]; color?: string; dashed?: boolean };

export function LineChart({
  title, description, labels, series, height = 220, format = fmtNum,
}: { title: string; description?: string; labels: string[]; series: LineSeries[]; height?: number; format?: (v: number) => string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const max = Math.max(0, ...series.flatMap((s) => s.values));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const pad = { l: 44, r: 12, t: 10, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v: number) => pad.t + ih - (v / top) * ih;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
  const summary = series.map((s) => `${s.name}: total ${format(s.values.reduce((a, b) => a + b, 0))}`).join("; ");

  return (
    <ChartFrame title={title} description={description} legend={series.length > 1 && series.map((s, i) => (
      <span key={s.name} className="inline-flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={s.color ?? (i ? COMPARE : MAIN)} strokeWidth="2" strokeDasharray={s.dashed ?? i > 0 ? "4 3" : undefined} /></svg>
        {s.name}
      </span>
    ))}>
      <div ref={ref} className="relative">
        {n === 0 ? <p className="py-8 text-center text-sm text-muted">No data for this period.</p> : (
          <svg width={width} height={height} role="img" aria-label={`${title}. ${summary}`} className="block"
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
              const px = e.clientX - box.left - pad.l;
              setHover(Math.min(n - 1, Math.max(0, Math.round(n <= 1 ? 0 : (px / iw) * (n - 1)))));
            }}>
            <title>{title}</title>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke={GRID} />
                <text x={pad.l - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill={AXIS_TEXT} style={{ fontVariantNumeric: "tabular-nums" }}>{format(t)}</text>
              </g>
            ))}
            {labels.map((l, i) => (i % every === 0 || i === n - 1) && (
              <text key={i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize="11" fill={AXIS_TEXT}>{l}</text>
            ))}
            {series.map((s, si) => {
              const color = s.color ?? (si ? COMPARE : MAIN);
              const pts = s.values.slice(0, n).map((v, i) => `${x(i)},${y(v)}`).join(" ");
              return <polyline key={s.name} points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
                strokeDasharray={s.dashed ?? si > 0 ? "5 4" : undefined} />;
            })}
            {hover !== null && (
              <g>
                <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="#94a3b8" strokeDasharray="2 3" />
                {series.map((s, si) => s.values[hover] !== undefined && (
                  <circle key={s.name} cx={x(hover)} cy={y(s.values[hover])} r="4" fill={s.color ?? (si ? COMPARE : MAIN)} stroke="#fff" strokeWidth="2" />
                ))}
              </g>
            )}
          </svg>
        )}
        {hover !== null && (
          <div className="pointer-events-none absolute top-1 rounded-md border border-line bg-white px-2 py-1 text-xs shadow-sm"
            style={{ left: Math.min(Math.max(x(hover) - 60, 0), width - 140) }}>
            <div className="font-medium text-ink">{labels[hover]}</div>
            {series.map((s) => <div key={s.name} className="tabular-nums text-muted">{s.name}: <span className="text-ink">{format(s.values[hover] ?? 0)}</span></div>)}
          </div>
        )}
      </div>
    </ChartFrame>
  );
}

export type Bar = { label: string; value: number; hint?: string; color?: string };

/** Horizontal bars with labels on the left and values on the right (direct labels, no legend needed). */
export function BarChart({ title, description, data, format = fmtNum, max }: { title: string; description?: string; data: Bar[]; format?: (v: number) => string; max?: number }) {
  const top = max ?? Math.max(0, ...data.map((d) => d.value));
  return (
    <ChartFrame title={title} description={description}>
      {data.length === 0 ? <p className="py-6 text-center text-sm text-muted">No data yet.</p> : (
        <ul role="list" aria-label={title} className="space-y-1.5">
          {data.map((d) => (
            <li key={d.label} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-2 text-sm" title={d.hint ?? `${d.label}: ${format(d.value)}`}>
              <span className="truncate text-ink" title={d.label}>{d.label}</span>
              <svg height="12" width="100%" aria-hidden className="block">
                <rect x="0" y="1" height="10" rx="4" width={`${top ? Math.max((d.value / top) * 100, d.value > 0 ? 1.5 : 0) : 0}%`} fill={d.color ?? MAIN} />
              </svg>
              <span className="text-right tabular-nums text-ink">{format(d.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </ChartFrame>
  );
}

export type HeatCell = { weekday: number; hour: number; value: number; posts: number; enough: boolean };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Weekday × hour grid. Cells with too few posts are hatched instead of coloured. */
export function HeatGrid({ title, description, cells, format = fmtPct }: { title: string; description?: string; cells: HeatCell[]; format?: (v: number) => string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const by = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]));
  const max = Math.max(0, ...cells.filter((c) => c.enough).map((c) => c.value));
  const left = 36, topPad = 16;
  const cw = Math.max(8, (width - left) / 24);
  const ch = 20;
  const height = topPad + ch * 7 + 4;
  return (
    <ChartFrame title={title} description={description} legend={<>
      <span className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: MAIN, opacity: 0.25 }} />Lower</span>
      <span className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: MAIN }} />Higher engagement rate</span>
      <span className="inline-flex items-center gap-1.5"><svg width="12" height="12" aria-hidden><rect width="12" height="12" rx="2" fill="url(#hatch-legend)" stroke={GRID} /><defs><pattern id="hatch-legend" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="#94a3b8" strokeWidth="1.2" /></pattern></defs></svg>Fewer than 5 posts</span>
    </>}>
      <div ref={ref} className="overflow-x-auto">
        <svg width={Math.max(width, left + 24 * 8)} height={height} role="img" aria-label={`${title}. ${cells.filter((c) => c.enough).length} time slots have enough posts.`}>
          <title>{title}</title>
          <defs>
            <pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="4" stroke="#94a3b8" strokeWidth="1.2" />
            </pattern>
          </defs>
          {[0, 6, 12, 18, 23].map((h) => (
            <text key={h} x={left + h * cw + cw / 2} y={10} textAnchor="middle" fontSize="10" fill={AXIS_TEXT}>{String(h).padStart(2, "0")}:00</text>
          ))}
          {DAYS.map((d, wd) => (
            <g key={d}>
              <text x={0} y={topPad + wd * ch + ch / 2} dy="0.32em" fontSize="11" fill={AXIS_TEXT}>{d}</text>
              {Array.from({ length: 24 }, (_, h) => {
                const c = by.get(`${wd}-${h}`);
                const fill = !c ? "#f8fafc" : c.enough ? MAIN : "url(#hatch)";
                const opacity = c && c.enough ? 0.2 + 0.8 * (max ? c.value / max : 0) : 1;
                return (
                  <rect key={h} x={left + h * cw + 1} y={topPad + wd * ch + 1} width={cw - 2} height={ch - 2} rx="3" fill={fill} fillOpacity={opacity}
                    stroke={c ? "none" : GRID} strokeWidth="1">
                    <title>{`${d} ${String(h).padStart(2, "0")}:00 — ${c ? `${c.posts} post${c.posts === 1 ? "" : "s"}, ${format(c.value)} engagement rate${c.enough ? "" : " (not enough data)"}` : "no posts"}`}</title>
                  </rect>
                );
              })}
            </g>
          ))}
        </svg>
      </div>
    </ChartFrame>
  );
}

/** KPI tile with an optional change vs the previous period. `invert` when lower is better (search position). */
export function Kpi({ label, value, delta, invert, hint }: { label: string; value: string; delta?: number | null; invert?: boolean; hint?: string }) {
  const good = delta == null ? null : invert ? delta < 0 : delta > 0;
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-0.5 text-2xl font-bold tabular-nums" style={{ color: INK }}>{value}</div>
      {delta != null && (
        <div className={`text-xs tabular-nums ${delta === 0 ? "text-muted" : good ? "text-emerald-700" : "text-danger"}`}>
          {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} {Math.abs(delta).toFixed(1)}% vs previous period
        </div>
      )}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}
