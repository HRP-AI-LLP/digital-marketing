// Small dependency-free SVG charts. Brand palette: primary-600 main series, secondary-500 comparison
// (dashed, so it never relies on colour alone), slate grid lines. One y-axis per chart.
import { type ReactNode } from "react";

export const MAIN = "#086fb8";
export const COMPARE = "#06a6c7";
const INK = "#0f172a";

export { fmtNum, fmtPct, fmtDuration, useWidth, niceTicks } from "../../components/viz";
import { Heatmap, HBars, TimeChart, fmtNum, fmtPct } from "../../components/viz";

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
  return (
    <ChartFrame title={title} description={description}>
      <TimeChart labels={labels} height={height} format={format} area={series.length === 1}
        series={series.map((s, i) => ({ key: s.name, name: s.name, values: s.values, color: s.color ?? (i ? COMPARE : MAIN), dashed: s.dashed ?? i > 0 }))} />
    </ChartFrame>
  );
}

export type Bar = { label: string; value: number; hint?: string; color?: string };

/** Horizontal bars with labels on the left and values on the right; hover a bar for the details. */
export function BarChart({ title, description, data, format = fmtNum, max }: { title: string; description?: string; data: Bar[]; format?: (v: number) => string; max?: number }) {
  return (
    <ChartFrame title={title} description={description}>
      <HBars format={format} max={max} empty="No data yet."
        data={data.map((d) => ({ key: d.label, label: d.label, value: d.value, color: d.color ?? MAIN,
          detail: d.hint ? <div className="mt-1 text-[11px] text-muted">{d.hint}</div> : undefined }))} />
    </ChartFrame>
  );
}

export type HeatCell = { weekday: number; hour: number; value: number; posts: number; enough: boolean };

/** Weekday × hour grid. Cells with too few posts are hatched instead of coloured; hover for the figures. */
export function HeatGrid({ title, description, cells, format = fmtPct }: { title: string; description?: string; cells: HeatCell[]; format?: (v: number) => string }) {
  return (
    <ChartFrame title={title} description={description}>
      <Heatmap format={format} cells={cells.map((c) => ({ weekday: c.weekday, hour: c.hour, value: c.value, posts: c.posts, enough: c.enough }))} />
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
