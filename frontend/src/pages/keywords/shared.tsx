import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Alert, Badge } from "../../components/ui";
import { ChartFrame, useWidth } from "../analytics/charts";
import { HoverTip, TipHead, TipRow, useTip, useChartZoom, ZoomFrame } from "../../components/viz";
import type { Estimate, Intent, KwRow, Overview } from "./types";
import { offerEmailFile } from "../../components/EmailDialog";

export const MAIN = "#086fb8";
export const COMPARE = "#eb6834";

export function useOverview() {
  return useQuery({ queryKey: ["keywords", "overview"], queryFn: () => api<Overview>("/keywords/overview") });
}

export const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : new Intl.NumberFormat("en-IN").format(v));
export const usd = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
export const pos = (v: number | null | undefined, depth = 100) => (v === null || v === undefined ? `>${depth}` : `#${v}`);

const INTENT_TONE: Record<Intent, "brand" | "accent" | "neutral" | "danger"> = {
  informational: "neutral", commercial: "accent", transactional: "brand", navigational: "neutral",
};
const INTENT_SHORT: Record<Intent, string> = { informational: "Learn", commercial: "Compare", transactional: "Buy", navigational: "Brand" };

export function IntentBadge({ intent, guessed }: { intent: Intent | null; guessed?: boolean }) {
  if (!intent) return <span className="text-muted">—</span>;
  return (
    <span title={`${intent}${guessed ? " (estimated)" : ""}`}>
      <Badge tone={INTENT_TONE[intent]}>{INTENT_SHORT[intent]}{guessed ? "*" : ""}</Badge>
    </span>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  dataforseo: "DataForSEO", search_console: "Search Console", autocomplete: "Google Autocomplete", web_search: "Web search",
  ai: "AI idea", competitor_pages: "Competitor pages", gsc: "Search Console", search: "Web search (approx.)",
};
export const sourceLabel = (s: string | null | undefined) => (s ? SOURCE_LABEL[s] ?? s : "—");

const KD_COLOR = (v: number) => (v < 30 ? "#10b981" : v < 60 ? "#f59e0b" : "#f43f5e");

export function Kd({ v }: { v: number | null }) {
  if (v === null || v === undefined) return <span className="text-muted">—</span>;
  const label = v < 30 ? "Easy" : v < 60 ? "Medium" : "Hard";
  const color = v < 30 ? "bg-emerald-500" : v < 60 ? "bg-amber-500" : "bg-rose-500";
  return (
    <HoverTip label={`Keyword difficulty ${v}/100 (${label})`} className="items-center gap-1.5 tabular-nums" tip={
      <>
        <TipHead sub="How hard it is to reach page one">Keyword difficulty</TipHead>
        <TipRow color={KD_COLOR(v)} label="Difficulty" value={`${v} / 100`} />
        <TipRow label="Rating" value={`${label} (${v < 30 ? "0–29" : v < 60 ? "30–59" : "60–100"})`} />
      </>
    }>
      <span className={`h-2 w-2 rounded-full ${color}`} aria-hidden />{v}
    </HoverTip>
  );
}

/** Estimated relative demand 0–100 (no volume data): a small bar. */
export function Demand({ v }: { v: number | null | undefined }) {
  if (!v) return <span className="text-muted">—</span>;
  return (
    <HoverTip label={`Estimated relative demand ${v}/100 (not a search volume)`} className="items-center gap-1.5" tip={
      <>
        <TipHead sub="Estimated, not a search volume">Relative demand</TipHead>
        <TipRow color={MAIN} label="Demand" value={`${v} / 100`} />
        <div className="mt-1 text-[11px] text-muted">Compared with the other keywords found. Add DataForSEO for real volumes.</div>
      </>
    }>
      <svg width="40" height="8" aria-hidden><rect width="40" height="8" rx="4" fill="#e2e8f0" /><rect width={Math.max(3, (v / 100) * 40)} height="8" rx="4" fill={MAIN} /></svg>
      <span className="text-xs tabular-nums text-muted">{v}</span>
    </HoverTip>
  );
}

/**
 * Small trend line for a table cell. Hover or focus shows the point under the pointer (arrow keys step through).
 * `invert` puts low numbers at the top (positions); `format` and `labels` describe each point in the popup.
 */
export function Sparkline({ values, invert = false, label, labels, title = "Value", format = (v: number) => num(v), width = 72, height = 22 }: {
  values: (number | null)[]; invert?: boolean; label: string; labels?: string[]; title?: string; format?: (v: number) => string; width?: number; height?: number;
}) {
  const t = useTip<HTMLSpanElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pts = values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v !== null && p.v !== undefined);
  if (pts.length < 2) return <span className="text-xs text-muted">—</span>;
  const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
  const n = values.length;
  const x = (i: number) => 2 + (i * (width - 4)) / Math.max(1, n - 1);
  const y = (v: number) => {
    const f = hi === lo ? 0.5 : (v - lo) / (hi - lo);
    return 2 + (invert ? f : 1 - f) * (height - 4);
  };
  const last = pts[pts.length - 1];
  const nameOf = (i: number) => labels?.[i] ?? `${i + 1} of ${n}`;
  const tipFor = (i: number) => {
    const v = values[i];
    const prev = [...values.slice(0, i)].reverse().find((p) => p !== null && p !== undefined);
    const d = v != null && prev != null ? v - prev : null;
    const better = d == null || d === 0 ? null : invert ? d < 0 : d > 0;
    return (
      <>
        <TipHead>{nameOf(i)}</TipHead>
        <TipRow color={MAIN} label={title} value={v == null ? "—" : format(v)} />
        {d != null && <TipRow label="vs previous" value={<span className={better == null ? "" : better ? "text-emerald-700" : "text-danger"}>{d > 0 ? "+" : ""}{num(d)}</span>} />}
        <TipRow label={invert ? "Best / worst" : "Lowest / highest"} value={`${format(lo)} / ${format(hi)}`} />
      </>
    );
  };
  const at = (i: number, e: { clientX: number; clientY: number } | Element) => { setHover(i); t.show(e, tipFor(i)); };
  const clear = () => { setHover(null); t.hide(); };
  return (
    <span ref={t.ref} className="relative inline-block align-middle">
      <svg width={width} height={height} role="img" aria-label={label} tabIndex={0} className="block rounded outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
        onPointerMove={(e) => {
          const b = e.currentTarget.getBoundingClientRect();
          at(Math.min(n - 1, Math.max(0, Math.round(((e.clientX - b.left - 2) / (width - 4)) * (n - 1)))), e);
        }}
        onPointerLeave={clear} onFocus={(e) => at(last.i, e.currentTarget)} onBlur={clear}
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const i = Math.min(n - 1, Math.max(0, (hover ?? last.i) + (e.key === "ArrowRight" ? 1 : -1)));
          const b = e.currentTarget.getBoundingClientRect();
          at(i, { clientX: b.left + x(i), clientY: b.top });
        }}>
        <polyline points={pts.map((p) => `${x(p.i)},${y(p.v)}`).join(" ")} fill="none" stroke={MAIN} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && values[hover] != null
          ? <circle cx={x(hover)} cy={y(values[hover] as number)} r="3" fill={MAIN} stroke="#fff" strokeWidth="1.5" />
          : <circle cx={x(last.i)} cy={y(last.v)} r="2.2" fill={MAIN} />}
      </svg>
      {t.tip}
    </span>
  );
}

type PositionProps = {
  title: string; description?: string; labels: string[]; series: { name: string; values: (number | null)[]; color?: string; dashed?: boolean }[];
  maxPos?: number; height?: number;
};
const posCol = (s: { color?: string }, i: number) => s.color ?? (i ? COMPARE : MAIN);

/** Position chart: #1 at the top, gaps where a keyword wasn't found. Hover / arrow keys show every series at that date. */
export function PositionChart(props: PositionProps) {
  const { title, description, series } = props;
  return (
    <ChartFrame title={title} description={description} legend={series.length > 1 && series.map((s, i) => (
      <span key={s.name} className="inline-flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={posCol(s, i)} strokeWidth="2.5" strokeDasharray={s.dashed ? "4 3" : undefined} /></svg>{s.name}
      </span>
    ))}>
      <ZoomFrame><PositionPlot {...props} /></ZoomFrame>
    </ChartFrame>
  );
}

function PositionPlot({ title, labels, series, maxPos = 100, height = 220 }: PositionProps) {
  height = Math.round(height * useChartZoom());
  const [ref, width] = useWidth<HTMLDivElement>();
  const t = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const col = (s: { color?: string }, i: number) => s.color ?? (i ? COMPARE : MAIN);
  const vals = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const top = Math.min(maxPos, Math.max(10, Math.ceil((Math.max(1, ...vals) + 2) / 10) * 10));
  const ticks = [1, ...[0.25, 0.5, 0.75, 1].map((f) => Math.round(top * f))].filter((v, i, a) => a.indexOf(v) === i);
  const pad = { l: 36, r: 12, t: 10, b: 26 };
  const iw = Math.max(10, width - pad.l - pad.r), ih = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v: number) => pad.t + ((Math.min(v, top) - 1) / Math.max(1, top - 1)) * ih;
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 72))));
  const segs = (vs: (number | null)[]) => {
    const out: string[][] = []; let cur: string[] = [];
    vs.forEach((v, i) => { if (v === null) { if (cur.length) out.push(cur); cur = []; } else cur.push(`${x(i)},${y(v)}`); });
    if (cur.length) out.push(cur);
    return out;
  };
  const tipFor = (i: number) => (
    <>
      <TipHead sub="Lower is better">{labels[i]}</TipHead>
      {series.map((s, si) => {
        const v = s.values[i] ?? null;
        const prev = i > 0 ? s.values[i - 1] ?? null : null;
        const d = v !== null && prev !== null ? prev - v : null; // positive = moved up
        return (
          <TipRow key={s.name} color={col(s, si)} dashed={s.dashed} label={s.name} value={
            <>{pos(v, maxPos)}{d ? <span className={`ml-1 text-[11px] font-normal ${d > 0 ? "text-emerald-700" : "text-danger"}`}>{d > 0 ? "▲" : "▼"}{Math.abs(Math.round(d * 10) / 10)}</span> : null}</>
          } />
        );
      })}
    </>
  );
  const at = (i: number, e: { clientX: number; clientY: number }) => { setHover(i); t.show(e, tipFor(i)); };
  const clear = () => { setHover(null); t.hide(); };
  return (
      <div ref={(el) => { (ref as React.MutableRefObject<HTMLDivElement | null>).current = el; (t.ref as React.MutableRefObject<HTMLDivElement | null>).current = el; }} className="relative">
        {!vals.length ? <p className="py-8 text-center text-sm text-muted">No positions yet. They appear after the first check.</p> : (
          <svg width={width} height={height} role="img" aria-label={`${title}.`} tabIndex={0} className="block touch-none select-none outline-none"
            onPointerLeave={clear} onBlur={clear}
            onPointerMove={(e) => {
              const b = e.currentTarget.getBoundingClientRect();
              at(Math.min(n - 1, Math.max(0, Math.round(n <= 1 ? 0 : ((e.clientX - b.left - pad.l) / iw) * (n - 1)))), e);
            }}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              e.preventDefault();
              const i = Math.min(n - 1, Math.max(0, (hover ?? (e.key === "ArrowRight" ? -1 : n)) + (e.key === "ArrowRight" ? 1 : -1)));
              const b = e.currentTarget.getBoundingClientRect();
              at(i, { clientX: b.left + x(i), clientY: b.top + pad.t + 20 });
            }}>
            {ticks.map((tk) => (
              <g key={tk}>
                <line x1={pad.l} x2={width - pad.r} y1={y(tk)} y2={y(tk)} stroke="#e2e8f0" />
                <text x={pad.l - 6} y={y(tk)} dy="0.32em" textAnchor="end" fontSize="11" fill="#64748b">#{tk}</text>
              </g>
            ))}
            {labels.map((l, i) => (i % every === 0 || i === n - 1) && (
              <text key={i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize="11" fill="#64748b">{l}</text>
            ))}
            {series.map((s, si) => segs(s.values).map((seg, k) => seg.length === 1
              ? <circle key={`${s.name}${k}`} cx={seg[0].split(",")[0]} cy={seg[0].split(",")[1]} r="3" fill={col(s, si)} />
              : <polyline key={`${s.name}${k}`} points={seg.join(" ")} fill="none" stroke={col(s, si)} strokeWidth="2"
                  strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinejoin="round" strokeLinecap="round" />))}
            {hover !== null && (
              <g pointerEvents="none">
                <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="#94a3b8" strokeDasharray="2 3" />
                {series.map((s, si) => s.values[hover] != null && (
                  <circle key={s.name} cx={x(hover)} cy={y(s.values[hover] as number)} r="4.5" fill={col(s, si)} stroke="#fff" strokeWidth="2" />
                ))}
              </g>
            )}
          </svg>
        )}
        {t.tip}
      </div>
  );
}

/** Banner that says where the numbers come from (free/estimated vs DataForSEO). */
export function DataNotice({ o, children }: { o?: Overview; children?: ReactNode }) {
  if (!o) return null;
  if (o.provider.configured) {
    return <p className="mb-4 text-sm text-muted">Keyword data from <strong>{o.provider.name}</strong> (search volume, difficulty and cost per click). Results are cached to save money. {children}</p>;
  }
  return (
    <Alert tone="info">
      Free mode: suggestions from Google Autocomplete and web search, your Search Console data{o.gsc_connected ? "" : " (not connected yet)"} and AI.
      There are no search volumes, so you see an <strong>estimated relative demand</strong> instead. For volumes, difficulty and
      exact Google rankings you can add DataForSEO (optional, pay as you go) in <Link className="underline" to="/settings/integrations">Integrations</Link>. {children}
    </Alert>
  );
}

/** Live cost estimate for an action (debounced). */
export function CostNote({ body, enabled = true }: { body: Record<string, unknown>; enabled?: boolean }) {
  const key = JSON.stringify(body);
  const [debounced, setDebounced] = useState(key);
  useEffect(() => { const t = setTimeout(() => setDebounced(key), 500); return () => clearTimeout(t); }, [key]);
  const q = useQuery({ queryKey: ["keywords", "estimate", debounced], queryFn: () => api<Estimate>("/keywords/estimate", { method: "POST", json: JSON.parse(debounced) }), enabled, retry: false });
  if (!enabled || !q.data) return null;
  const e = q.data;
  return (
    <span className="text-xs text-muted">
      {e.cost_usd > 0 ? <>Estimated cost <strong className="text-ink">{usd(e.cost_usd)}</strong>. {e.note}</>
        : e.cached ? "Free (already fetched)." : e.note && /^free/i.test(e.note) ? e.note : `Free. ${e.note ?? ""}`}
    </span>
  );
}

export function downloadCsv(filename: string, rows: (string | number | null | undefined | boolean)[][]) {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const content = "﻿" + rows.map((r) => r.map(esc).join(",")).join("\n");
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  offerEmailFile({ filename, mime: "text/csv", content });
}

export function kwCsv(rows: KwRow[]) {
  return [["keyword", "volume", "estimated_demand", "kd", "cpc_usd", "intent", "search_console_impressions", "search_console_position", "source"],
    ...rows.map((r) => [r.keyword, r.volume, r.demand ?? "", r.kd, r.cpc, r.intent, r.gsc?.impressions ?? r.impressions ?? "",
      r.gsc?.position ?? r.position ?? "", sourceLabel(r.source)])];
}

export type SortDir = "asc" | "desc";
export function SortTh({ label, k, sort, setSort, className = "", title }: { label: string; k: string; sort: [string, SortDir]; setSort: (s: [string, SortDir]) => void; className?: string; title?: string }) {
  const active = sort[0] === k;
  return (
    <th scope="col" className={`whitespace-nowrap px-3 py-2 font-medium ${className}`} title={title} aria-sort={active ? (sort[1] === "asc" ? "ascending" : "descending") : undefined}>
      <button type="button" className={`hover:text-ink ${active ? "text-ink" : ""}`} onClick={() => setSort([k, active && sort[1] === "desc" ? "asc" : "desc"])}>
        {label}{active ? (sort[1] === "desc" ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );
}

export function sortRows<T>(rows: T[], [k, dir]: [string, SortDir], get: (r: T, k: string) => number | string | null | undefined): T[] {
  return [...rows].sort((a, b) => {
    const va = get(a, k), vb = get(b, k);
    if (va === vb) return 0;
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
    return dir === "asc" ? c : -c;
  });
}
