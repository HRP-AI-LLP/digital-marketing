import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Alert, Badge } from "../../components/ui";
import { ChartFrame, useWidth } from "../analytics/charts";
import type { Estimate, Intent, KwRow, Overview } from "./types";

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

export function Kd({ v }: { v: number | null }) {
  if (v === null || v === undefined) return <span className="text-muted">—</span>;
  const label = v < 30 ? "Easy" : v < 60 ? "Medium" : "Hard";
  const color = v < 30 ? "bg-emerald-500" : v < 60 ? "bg-amber-500" : "bg-rose-500";
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums" title={`Keyword difficulty ${v}/100 (${label})`}>
      <span className={`h-2 w-2 rounded-full ${color}`} aria-hidden />{v}
    </span>
  );
}

/** Estimated relative demand 0–100 (no volume data): a small bar. */
export function Demand({ v }: { v: number | null | undefined }) {
  if (!v) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5" title={`Estimated relative demand ${v}/100 (not a search volume)`}>
      <svg width="40" height="8" aria-hidden><rect width="40" height="8" rx="4" fill="#e2e8f0" /><rect width={Math.max(3, (v / 100) * 40)} height="8" rx="4" fill={MAIN} /></svg>
      <span className="text-xs tabular-nums text-muted">{v}</span>
    </span>
  );
}

export function Sparkline({ values, invert = false, label, width = 72, height = 22 }: { values: (number | null)[]; invert?: boolean; label: string; width?: number; height?: number }) {
  const pts = values.map((v, i) => ({ v, i })).filter((p): p is { v: number; i: number } => p.v !== null && p.v !== undefined);
  if (pts.length < 2) return <span className="text-xs text-muted">—</span>;
  const lo = Math.min(...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v));
  const n = values.length;
  const x = (i: number) => 2 + (i * (width - 4)) / Math.max(1, n - 1);
  const y = (v: number) => {
    const t = hi === lo ? 0.5 : (v - lo) / (hi - lo);
    return 2 + (invert ? t : 1 - t) * (height - 4);
  };
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="block">
      <title>{label}</title>
      <polyline points={pts.map((p) => `${x(p.i)},${y(p.v)}`).join(" ")} fill="none" stroke={MAIN} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.i)} cy={y(last.v)} r="2.2" fill={MAIN} />
    </svg>
  );
}

/** Position chart: #1 at the top, gaps where a keyword wasn't found. */
export function PositionChart({ title, description, labels, series, maxPos = 100, height = 220 }: {
  title: string; description?: string; labels: string[]; series: { name: string; values: (number | null)[]; color?: string; dashed?: boolean }[];
  maxPos?: number; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
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
  return (
    <ChartFrame title={title} description={description} legend={series.length > 1 && series.map((s, i) => (
      <span key={s.name} className="inline-flex items-center gap-1.5">
        <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={s.color ?? (i ? COMPARE : MAIN)} strokeWidth="2.5" strokeDasharray={s.dashed ? "4 3" : undefined} /></svg>{s.name}
      </span>
    ))}>
      <div ref={ref} className="relative">
        {!vals.length ? <p className="py-8 text-center text-sm text-muted">No positions yet. They appear after the first check.</p> : (
          <svg width={width} height={height} role="img" aria-label={`${title}.`} className="block" onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const b = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
              setHover(Math.min(n - 1, Math.max(0, Math.round(n <= 1 ? 0 : ((e.clientX - b.left - pad.l) / iw) * (n - 1)))));
            }}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="#e2e8f0" />
                <text x={pad.l - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill="#64748b">#{t}</text>
              </g>
            ))}
            {labels.map((l, i) => (i % every === 0 || i === n - 1) && (
              <text key={i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize="11" fill="#64748b">{l}</text>
            ))}
            {series.map((s, si) => segs(s.values).map((seg, k) => seg.length === 1
              ? <circle key={`${s.name}${k}`} cx={seg[0].split(",")[0]} cy={seg[0].split(",")[1]} r="3" fill={s.color ?? (si ? COMPARE : MAIN)} />
              : <polyline key={`${s.name}${k}`} points={seg.join(" ")} fill="none" stroke={s.color ?? (si ? COMPARE : MAIN)} strokeWidth="2"
                  strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinejoin="round" strokeLinecap="round" />))}
            {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} stroke="#94a3b8" strokeDasharray="2 3" />}
          </svg>
        )}
        {hover !== null && vals.length > 0 && (
          <div className="pointer-events-none absolute top-1 rounded-md border border-line bg-white px-2 py-1 text-xs shadow-sm" style={{ left: Math.min(Math.max(x(hover) - 60, 0), width - 150) }}>
            <div className="font-medium text-ink">{labels[hover]}</div>
            {series.map((s) => <div key={s.name} className="tabular-nums text-muted">{s.name}: <span className="text-ink">{pos(s.values[hover] ?? null, maxPos)}</span></div>)}
          </div>
        )}
      </div>
    </ChartFrame>
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
  const blob = new Blob(["﻿" + rows.map((r) => r.map(esc).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
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
