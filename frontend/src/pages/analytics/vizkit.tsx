// Charts and small visual pieces for the growth, competitor and health tabs.
// Rules: one y-axis per chart, 2px lines, rounded bar ends, recessive grid, values in ink colours,
// categorical colours in a fixed order (validated for colour-vision deficiency), legend for 2+ series,
// and a hover tooltip on every plotted mark.
import { useState, type ReactNode } from "react";
import { ChartFrame, fmtNum, niceTicks, useWidth } from "./charts";

// Brand blue first; the rest is a validated categorical order (worst adjacent CVD ΔE 9.1). Never cycled past 8.
export const SERIES = ["#086fb8", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const GRID = "#e2e8f0";
const AXIS = "#64748b";

export type TrendSeries = { name: string; values: (number | null)[]; color?: string; dashed?: boolean };

/** Line chart that keeps gaps (null) as breaks; y-axis can start above zero for slow-moving totals like followers. */
export function TrendChart({
  title, description, labels, series, height = 240, format = fmtNum, fromZero = true, empty = "No data for this period yet.", footer,
}: {
  title: string; description?: string; labels: string[]; series: TrendSeries[]; height?: number; format?: (v: number) => string;
  fromZero?: boolean; empty?: string; footer?: ReactNode;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const vals = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const hasData = n > 0 && vals.length > 0;
  let lo = fromZero ? 0 : Math.min(...vals);
  const hiRaw = Math.max(0, ...vals);
  if (!fromZero) lo = Math.max(0, lo - (hiRaw - lo) * 0.1 - 1);
  const ticksRaw = niceTicks(hiRaw - lo > 0 ? hiRaw - lo : Math.max(1e-6, Math.abs(hiRaw)) || 1);
  const step = ticksRaw[1] - ticksRaw[0] || 1;
  const base = fromZero ? 0 : Math.floor(lo / step) * step;
  const ticks = ticksRaw.map((t) => base + t).filter((t) => t <= hiRaw + step);
  const top = ticks[ticks.length - 1] || 1;
  const pad = { l: 52, r: 12, t: 10, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v: number) => pad.t + ih - ((v - base) / ((top - base) || 1)) * ih;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 72))));
  const color = (i: number, s: TrendSeries) => s.color ?? SERIES[i % SERIES.length];

  const segments = (s: TrendSeries) => {
    const out: string[][] = [];
    let cur: string[] = [];
    s.values.slice(0, n).forEach((v, i) => {
      if (v === null) { if (cur.length) out.push(cur); cur = []; } else cur.push(`${x(i)},${y(v)}`);
    });
    if (cur.length) out.push(cur);
    return out;
  };

  return (
    <ChartFrame title={title} description={description} legend={series.length > 1 && series.map((s, i) => (
      <span key={s.name} className="inline-flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={color(i, s)} strokeWidth="2.5" strokeDasharray={s.dashed ? "4 3" : undefined} /></svg>
        {s.name}
      </span>
    ))}>
      <div ref={ref} className="relative">
        {!hasData ? <p className="rounded-lg bg-slate-50 py-10 text-center text-sm text-muted">{empty}</p> : (
          <svg width={width} height={height} role="img" className="block" aria-label={`${title}. ${series.map((s) => {
            const last = [...s.values].reverse().find((v) => v !== null);
            return `${s.name}: latest ${last == null ? "no data" : format(last)}`;
          }).join("; ")}`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
              const px = e.clientX - box.left - pad.l;
              setHover(Math.min(n - 1, Math.max(0, Math.round(n <= 1 ? 0 : (px / iw) * (n - 1)))));
            }}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke={GRID} />
                <text x={pad.l - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill={AXIS} style={{ fontVariantNumeric: "tabular-nums" }}>{format(t)}</text>
              </g>
            ))}
            {labels.map((l, i) => (i % every === 0 || i === n - 1) && (i === n - 1 || n - 1 - i >= every / 2) && (
              <text key={i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize="11" fill={AXIS}>{l}</text>
            ))}
            {series.map((s, si) => segments(s).map((seg, k) => seg.length === 1 ? (
              <circle key={`${s.name}-${k}`} cx={seg[0].split(",")[0]} cy={seg[0].split(",")[1]} r="4" fill={color(si, s)} stroke="#fff" strokeWidth="2" />
            ) : (
              <polyline key={`${s.name}-${k}`} points={seg.join(" ")} fill="none" stroke={color(si, s)} strokeWidth="2"
                strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "5 4" : undefined} />
            )))}
            {hover !== null && (
              <g>
                <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="#94a3b8" strokeDasharray="2 3" />
                {series.map((s, si) => s.values[hover] != null && (
                  <circle key={s.name} cx={x(hover)} cy={y(s.values[hover] as number)} r="4.5" fill={color(si, s)} stroke="#fff" strokeWidth="2" />
                ))}
              </g>
            )}
          </svg>
        )}
        {hasData && hover !== null && (
          <div className="pointer-events-none absolute top-1 z-10 min-w-[8rem] rounded-md border border-line bg-white px-2.5 py-1.5 text-xs shadow-md"
            style={{ left: Math.min(Math.max(x(hover) + 10, 0), Math.max(0, width - 190)) }}>
            <div className="mb-0.5 font-medium text-ink">{labels[hover]}</div>
            {series.map((s, si) => (
              <div key={s.name} className="flex items-center gap-1.5 tabular-nums text-muted">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(si, s) }} />
                <span className="truncate">{s.name}</span>
                <span className="ml-auto pl-2 text-ink">{s.values[hover] == null ? "—" : format(s.values[hover] as number)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {footer}
    </ChartFrame>
  );
}

/** Vertical bars (e.g. posts per week) with a hover tooltip and value labels when there's room. */
export function ColumnChart({
  title, description, labels, values, height = 200, format = fmtNum, hint,
}: { title: string; description?: string; labels: string[]; values: number[]; height?: number; format?: (v: number) => string; hint?: (i: number) => string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const ticks = niceTicks(Math.max(0, ...values));
  const top = ticks[ticks.length - 1] || 1;
  const pad = { l: 40, r: 8, t: 16, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r);
  const ih = height - pad.t - pad.b;
  const slot = iw / Math.max(1, n);
  const bw = Math.max(4, Math.min(40, slot - 6));
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 64))));
  const showVals = slot > 26;
  return (
    <ChartFrame title={title} description={description}>
      <div ref={ref} className="relative">
        {n === 0 || !values.some((v) => v > 0) ? <p className="rounded-lg bg-slate-50 py-10 text-center text-sm text-muted">Nothing in this period yet.</p> : (
          <svg width={width} height={height} role="img" aria-label={`${title}. ${labels.map((l, i) => `${l}: ${format(values[i])}`).join(", ")}`} className="block"
            onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={pad.t + ih - (t / top) * ih} y2={pad.t + ih - (t / top) * ih} stroke={GRID} />
                <text x={pad.l - 6} y={pad.t + ih - (t / top) * ih} dy="0.32em" textAnchor="end" fontSize="11" fill={AXIS}>{format(t)}</text>
              </g>
            ))}
            {values.map((v, i) => {
              const cx = pad.l + slot * i + slot / 2;
              const h = (v / top) * ih;
              const r = Math.min(4, bw / 2, h);
              const top_ = pad.t + ih - h;
              return (
                <g key={i} onMouseEnter={() => setHover(i)}>
                  <rect x={pad.l + slot * i} y={pad.t} width={slot} height={ih} fill="transparent" />
                  {h > 0 && <path d={`M${cx - bw / 2},${pad.t + ih} V${top_ + r} Q${cx - bw / 2},${top_} ${cx - bw / 2 + r},${top_} H${cx + bw / 2 - r} Q${cx + bw / 2},${top_} ${cx + bw / 2},${top_ + r} V${pad.t + ih} Z`}
                    fill={SERIES[0]} fillOpacity={hover === null || hover === i ? 1 : 0.55} />}
                  {showVals && v > 0 && <text x={cx} y={top_ - 4} textAnchor="middle" fontSize="11" fill="#0f172a" style={{ fontVariantNumeric: "tabular-nums" }}>{format(v)}</text>}
                  {(i % every === 0) && <text x={cx} y={height - 8} textAnchor="middle" fontSize="11" fill={AXIS}>{labels[i]}</text>}
                </g>
              );
            })}
          </svg>
        )}
        {hover !== null && (
          <div className="pointer-events-none absolute top-1 z-10 rounded-md border border-line bg-white px-2.5 py-1.5 text-xs shadow-md"
            style={{ left: Math.min(Math.max(pad.l + slot * hover + slot / 2 + 8, 0), Math.max(0, width - 170)) }}>
            <div className="font-medium text-ink">{labels[hover]}</div>
            <div className="tabular-nums text-muted">{hint ? hint(hover) : format(values[hover])}</div>
          </div>
        )}
      </div>
    </ChartFrame>
  );
}

/** 0–100 score ring. */
export function ScoreRing({ score, size = 140, label = "out of 100" }: { score: number | null; size?: number; label?: string }) {
  const r = size / 2 - 11;
  const c = 2 * Math.PI * r;
  const frac = (score ?? 0) / 100;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={score == null ? "No score yet" : `Score ${score} out of 100`}>
      <defs>
        <linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#075492" /><stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={GRID} strokeWidth="11" />
      {score != null && score > 0 && (
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="url(#ring-grad)" strokeWidth="11" strokeLinecap="round"
          strokeDasharray={`${c * frac} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      )}
      <text x="50%" y="48%" textAnchor="middle" fontSize={size * 0.27} fontWeight="700" fill="#0f172a">{score ?? "—"}</text>
      <text x="50%" y="63%" textAnchor="middle" fontSize="11" fill={AXIS}>{label}</text>
    </svg>
  );
}

/** Thin 0–100 meter used next to each sub-score. */
export function Meter({ value, label }: { value: number | null; label: string }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}>
      {value != null && <div className="h-full rounded-full bg-gradient-to-r from-primary-700 to-secondary-400" style={{ width: `${Math.max(2, value)}%` }} />}
    </div>
  );
}

/** "▲ 12.4% vs previous 30 days" with colour + arrow (never colour alone). */
export function Delta({ value, suffix = "vs previous period", invert }: { value: number | null | undefined; suffix?: string; invert?: boolean }) {
  if (value == null) return <span className="text-xs text-muted">No earlier data to compare</span>;
  const good = invert ? value < 0 : value > 0;
  return (
    <span className={`text-xs tabular-nums ${value === 0 ? "text-muted" : good ? "text-emerald-700" : "text-danger"}`}>
      {value > 0 ? "▲" : value < 0 ? "▼" : "•"} {Math.abs(value).toFixed(1)}% <span className="text-muted">{suffix}</span>
    </span>
  );
}

export const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${fmtNum(v)}`);
export const pctText = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
export const shortDay = (iso: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(iso + (iso.length === 10 ? "T00:00:00" : "")));

// Platform badge: brand colour + short code, so identity is never colour alone.
const PLATFORM_BADGE: Record<string, { code: string; bg: string }> = {
  facebook_page: { code: "f", bg: "#1877F2" }, instagram: { code: "IG", bg: "#D62976" }, threads: { code: "@", bg: "#111827" },
  linkedin_person: { code: "in", bg: "#0A66C2" }, linkedin_org: { code: "in", bg: "#0A66C2" }, youtube: { code: "YT", bg: "#FF0000" },
  gbp: { code: "G", bg: "#34A853" }, pinterest: { code: "P", bg: "#E60023" }, bluesky: { code: "B", bg: "#1185FE" },
  mastodon: { code: "M", bg: "#6364FF" }, x: { code: "X", bg: "#111827" }, website: { code: "W", bg: "#475569" },
};
export function PlatformBadge({ platform, size = 24 }: { platform: string; size?: number }) {
  const b = PLATFORM_BADGE[platform] ?? { code: platform.slice(0, 2).toUpperCase(), bg: "#64748B" };
  return (
    <span aria-hidden className="grid shrink-0 place-items-center rounded-md font-bold text-white" style={{ background: b.bg, width: size, height: size, fontSize: size * 0.4 }}>
      {b.code}
    </span>
  );
}
