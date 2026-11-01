// Charts and small visual pieces for the growth, competitor and health tabs.
// Rules: one y-axis per chart, 2px lines, rounded bar ends, recessive grid, values in ink colours,
// categorical colours in a fixed order (validated for colour-vision deficiency), legend for 2+ series,
// and a hover tooltip on every plotted mark.
import { type ReactNode } from "react";
import { ChartFrame, fmtNum } from "./charts";
import { Columns, TimeChart, useTip, TipHead, TipRow } from "../../components/viz";

// Brand blue first; the rest is a validated categorical order (worst adjacent CVD ΔE 9.1). Never cycled past 8.
export { SERIES } from "../../components/viz";
import { SERIES } from "../../components/viz";
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
  return (
    <ChartFrame title={title} description={description}>
      <TimeChart labels={labels} height={height} format={format} fromZero={fromZero} empty={empty} area={series.length === 1}
        series={series.map((s, i) => ({ key: `${s.name}-${i}`, name: s.name, values: s.values, color: s.color ?? SERIES[i] ?? "#94a3b8", dashed: s.dashed }))} />
      {footer}
    </ChartFrame>
  );
}

/** Vertical bars (e.g. posts per week) with a hover popup on every bar. */
export function ColumnChart({
  title, description, labels, values, height = 200, format = fmtNum, hint,
}: { title: string; description?: string; labels: string[]; values: number[]; height?: number; format?: (v: number) => string; hint?: (i: number) => string }) {
  return (
    <ChartFrame title={title} description={description}>
      <Columns labels={labels} height={height} format={hint ? (v) => format(v) : format} empty="Nothing in this period yet."
        tipLabels={hint ? labels.map((l, i) => `${l} · ${hint(i)}`) : undefined}
        series={[{ key: "v", name: title, values, color: SERIES[0] }]} />
    </ChartFrame>
  );
}

/** 0–100 score ring. */
export function ScoreRing({ score, size = 140, label = "out of 100" }: { score: number | null; size?: number; label?: string }) {
  const t = useTip();
  return (
    <div ref={t.ref} className="relative inline-block" onPointerMove={(e) => t.show(e, <><TipHead>Score</TipHead><TipRow color={SERIES[0]} label={label} value={score == null ? "Not scored yet" : `${score} / 100`} /></>)} onPointerLeave={t.hide}>
      <Ring score={score} size={size} label={label} />
      {t.tip}
    </div>
  );
}

function Ring({ score, size, label }: { score: number | null; size: number; label: string }) {
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
  const t = useTip();
  return (
    <div ref={t.ref} className="relative" onPointerMove={(e) => t.show(e, <><TipHead>{label}</TipHead><TipRow color={SERIES[0]} label="Score" value={value == null ? "Not scored yet" : `${value} / 100`} /></>)} onPointerLeave={t.hide}>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}>
        {value != null && <div className="h-full rounded-full bg-gradient-to-r from-primary-700 to-secondary-400 transition-[width] duration-500" style={{ width: `${Math.max(2, value)}%` }} />}
      </div>
      {t.tip}
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
export const PLATFORM_BADGE: Record<string, { code: string; bg: string }> = {
  facebook_page: { code: "f", bg: "#1877F2" }, instagram: { code: "IG", bg: "#D62976" }, threads: { code: "@", bg: "#111827" },
  linkedin_person: { code: "in", bg: "#0A66C2" }, linkedin_org: { code: "in", bg: "#0A66C2" }, youtube: { code: "YT", bg: "#FF0000" },
  gbp: { code: "G", bg: "#34A853" }, pinterest: { code: "P", bg: "#E60023" }, bluesky: { code: "B", bg: "#1185FE" },
  mastodon: { code: "M", bg: "#6364FF" }, x: { code: "X", bg: "#111827" }, tiktok: { code: "TT", bg: "#010101" }, website: { code: "W", bg: "#475569" },
  telegram: { code: "TG", bg: "#229ED9" }, discord: { code: "DC", bg: "#5865F2" },
  reddit: { code: "r/", bg: "#FF4500" }, whatsapp: { code: "WA", bg: "#128C7E" },
};
export function PlatformBadge({ platform, size = 24 }: { platform: string; size?: number }) {
  const b = PLATFORM_BADGE[platform] ?? { code: platform.slice(0, 2).toUpperCase(), bg: "#64748B" };
  return (
    <span aria-hidden className="grid shrink-0 place-items-center rounded-md font-bold text-white" style={{ background: b.bg, width: size, height: size, fontSize: size * 0.4 }}>
      {b.code}
    </span>
  );
}
