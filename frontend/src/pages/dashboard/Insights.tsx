// The analytics half of the home dashboard: one filter bar scoping every KPI, chart and table below it.
import { useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Heatmap3D, { type Cube } from "./Heatmap3D";
import { ArrowDownRight, ArrowUpRight, Box, CalendarRange, Filter, Layers, Minus, RotateCcw, Table2, X } from "lucide-react";
import { api } from "../../api/client";
import {
  Columns, DataTable, Donut, Empty, Funnel, Gauge, HBars, OTHER, Panel, Pie3D, Radar, SERIES, Scatter, Skyline3D,
  Sparkline, TimeChart, TipRow, fmtDay, fmtDayLong, fmtFull, fmtNum, fmtPct, type Series,
} from "../../components/viz";
import { PlatformBadge } from "../analytics/vizkit";

type Kpi = { key: string; label: string; value: number; previous: number; delta: number | null; spark: number[]; format: "num" | "pct"; invert: boolean };
type Day = { date: string; posts: number; impressions: number; reach: number; engagements: number; likes: number; comments: number; shares: number; saves: number; video_views: number; clicks: number; engagement_rate: number; by_platform: Record<string, number> };
type Row = { key: string; label: string; posts: number; impressions: number; engagements: number; clicks: number; engagement_rate: number; color?: string | null };
type Channel = { id: string; platform: string; platform_label: string; name: string; status: string; followers: number | null; followers_change: number | null; followers_series: (number | null)[]; posts: number; engagements: number; impressions: number; engagement_rate: number; last_published_at: string | null };
type TopPost = { id: string; platform: string; platform_label: string; post_type: string; title: string; published_at: string; external_url: string | null; impressions: number; engagements: number; clicks: number; engagement_rate: number; has_metrics: boolean };
export type Insights = {
  period: { start: string; end: string; days: number; previous_start: string; previous_end: string; timezone: string };
  kpis: Kpi[]; daily: Day[];
  followers: { total: (number | null)[]; by_platform: { platform: string; label: string; values: (number | null)[] }[]; now: number | null; change: number | null };
  by_platform: Row[]; by_post_type: Row[]; by_pillar: Row[];
  engagement_mix: { key: string; label: string; value: number }[];
  funnel: { key: string; label: string; value: number; hint: string }[];
  status_mix: { key: string; label: string; value: number }[];
  heatmap: { weekday: number; day: string; hour: number; posts: number; engagements: number; engagement_rate: number }[];
  heat_cube: Cube;
  top_posts: TopPost[];
  upcoming: { date: string; total: number; by_platform: Record<string, number> }[];
  mentions: { daily: { date: string; positive: number; neutral: number; negative: number }[]; totals: { positive: number; neutral: number; negative: number } };
  channels: Channel[];
  filters: { platforms: string[]; platform_labels: Record<string, string>; accounts: { id: string; name: string; platform: string; platform_label: string }[];
    post_types: { key: string; label: string }[]; pillars: { id: string; name: string; color: string }[] };
};

// Stable colour per channel: a fixed priority order, so a filter never repaints the channels that remain.
const PLATFORM_ORDER = ["linkedin_org", "linkedin_person", "facebook_page", "instagram", "youtube", "pinterest", "x", "telegram",
  "bluesky", "mastodon", "discord", "threads", "gbp", "reddit", "whatsapp", "tiktok"];
const MAX_COLOURED = 7;

function channelColours(platforms: string[]) {
  const ordered = [...new Set(platforms)].sort((a, b) => (PLATFORM_ORDER.indexOf(a) + 1 || 99) - (PLATFORM_ORDER.indexOf(b) + 1 || 99));
  const map: Record<string, string> = {};
  ordered.forEach((p, i) => { map[p] = i < MAX_COLOURED ? SERIES[i] : OTHER; });
  return { map, ordered, coloured: ordered.slice(0, MAX_COLOURED), rest: ordered.slice(MAX_COLOURED) };
}

const PERIODS = [
  { id: "7", label: "7 days" }, { id: "30", label: "30 days" }, { id: "90", label: "90 days" }, { id: "180", label: "6 months" }, { id: "365", label: "12 months" },
];
const METRICS: { key: keyof Day; label: string; format: (v: number) => string }[] = [
  { key: "impressions", label: "Impressions", format: fmtNum }, { key: "engagements", label: "Engagements", format: fmtNum },
  { key: "engagement_rate", label: "Engagement rate", format: (v) => fmtPct(v) }, { key: "clicks", label: "Link clicks", format: fmtNum },
  { key: "reach", label: "Reach", format: fmtNum }, { key: "video_views", label: "Video views", format: fmtNum }, { key: "posts", label: "Posts", format: fmtNum },
];
// The chart can show every KPI card's measure; "followers" isn't a daily column, so it comes from its KPI.
type MetricKey = (typeof METRICS)[number]["key"] | "followers";
const CHART_METRICS: { key: MetricKey; label: string; format: (v: number) => string }[] = [...METRICS, { key: "followers", label: "Followers", format: fmtNum }];
const kpiMetric = (k: string): MetricKey | null => (k === "published" ? "posts" : CHART_METRICS.some((x) => x.key === k) ? (k as MetricKey) : null);

function useFilters() {
  const [sp, setSp] = useSearchParams();
  const f = {
    days: sp.get("days") ?? "30", start: sp.get("start") ?? "", end: sp.get("end") ?? "",
    platforms: (sp.get("platforms") ?? "").split(",").filter(Boolean), account: sp.get("account") ?? "",
    type: sp.get("type") ?? "", pillar: sp.get("pillar") ?? "",
  };
  const set = (patch: Partial<Record<keyof typeof f, string | string[]>>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      const val = Array.isArray(v) ? v.join(",") : v;
      if (val) next.set(k, val); else next.delete(k);
    }
    setSp(next, { replace: true });
  };
  const active = (f.platforms.length ? 1 : 0) + (f.account ? 1 : 0) + (f.type ? 1 : 0) + (f.pillar ? 1 : 0);
  const reset = () => setSp(new URLSearchParams(f.days !== "30" ? { days: f.days } : {}), { replace: true });
  return { f, set, active, reset };
}

export default function DashboardInsights() {
  const { f, set, active, reset } = useFilters();
  const qs = new URLSearchParams();
  if (f.start && f.end) { qs.set("start", f.start); qs.set("end", f.end); } else qs.set("days", f.days);
  if (f.platforms.length) qs.set("platforms", f.platforms.join(","));
  if (f.account) qs.set("account_id", f.account);
  if (f.type) qs.set("post_type", f.type);
  if (f.pillar) qs.set("pillar_id", f.pillar);
  const q = useQuery({
    queryKey: ["dashboard-insights", qs.toString()], queryFn: () => api<Insights>(`/dashboard/insights?${qs}`),
    placeholderData: keepPreviousData, refetchInterval: 120000,
  });
  const d = q.data;
  return (
    <div className="space-y-5">
      <FilterBar f={f} set={set} active={active} reset={reset} d={d} loading={q.isFetching} />
      {q.error && <div className="rounded-xl border border-danger/20 bg-danger-tint px-4 py-3 text-sm text-danger">{(q.error as Error).message}</div>}
      {!d ? <div className="grid h-64 place-items-center text-sm text-muted">Loading your numbers…</div> : (
        <div className={`space-y-5 transition-opacity ${q.isFetching ? "opacity-70" : ""}`}>
          <Body d={d} f={f} set={set} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ filters
function FilterBar({ f, set, active, reset, d, loading }: {
  f: ReturnType<typeof useFilters>["f"]; set: ReturnType<typeof useFilters>["set"]; active: number; reset: () => void; d?: Insights; loading: boolean;
}) {
  const [custom, setCustom] = useState(Boolean(f.start && f.end));
  const opts = d?.filters;
  const accounts = (opts?.accounts ?? []).filter((a) => !f.platforms.length || f.platforms.includes(a.platform));
  const sel = "h-9 max-w-[13rem] rounded-lg border border-line bg-white px-2.5 text-sm text-ink focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20";
  return (
    <div className="sticky top-0 z-20 -mx-1 rounded-2xl border border-line bg-white/95 px-4 py-2.5 shadow-sm backdrop-blur">
      <div className="flex flex-wrap items-center gap-2">
        <CalendarRange size={16} className="text-muted" />
        <div className="flex rounded-lg border border-line bg-slate-50 p-0.5" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.id} type="button" aria-pressed={!custom && !f.start && f.days === p.id}
              className={`rounded-md px-2.5 py-1 text-sm transition-colors ${!custom && !f.start && f.days === p.id ? "bg-white font-semibold text-ink shadow-sm" : "text-muted hover:text-ink"}`}
              onClick={() => { setCustom(false); set({ days: p.id, start: "", end: "" }); }}>{p.label}</button>
          ))}
          <button type="button" aria-pressed={custom}
            className={`rounded-md px-2.5 py-1 text-sm ${custom ? "bg-white font-semibold text-ink shadow-sm" : "text-muted hover:text-ink"}`}
            onClick={() => setCustom(true)}>Custom</button>
        </div>
        {custom && (
          <span className="flex items-center gap-1.5 text-sm">
            <input type="date" aria-label="From" className={sel} value={f.start} max={f.end || undefined} onChange={(e) => set({ start: e.target.value })} />
            <span className="text-muted">to</span>
            <input type="date" aria-label="To" className={sel} value={f.end} min={f.start || undefined} onChange={(e) => set({ end: e.target.value })} />
          </span>
        )}
        <span className="mx-1 hidden h-6 w-px bg-line sm:block" />
        <Filter size={15} className="text-muted" />
        <select aria-label="Account" className={sel} value={f.account} onChange={(e) => set({ account: e.target.value })}>
          <option value="">All accounts</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.platform_label}</option>)}
        </select>
        <select aria-label="Post type" className={sel} value={f.type} onChange={(e) => set({ type: e.target.value })}>
          <option value="">All post types</option>
          {(opts?.post_types ?? []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <select aria-label="Topic pillar" className={sel} value={f.pillar} onChange={(e) => set({ pillar: e.target.value })}>
          <option value="">All topics</option>
          {(opts?.pillars ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {active > 0 && (
          <button type="button" onClick={reset} className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-primary-700 hover:bg-primary-50">
            <RotateCcw size={14} /> Clear {active} filter{active === 1 ? "" : "s"}
          </button>
        )}
        <span className="ml-auto text-xs text-muted">
          {loading ? "Updating…" : d ? `${fmtDay(d.period.start)} – ${fmtDay(d.period.end)} vs the ${d.period.days} days before` : ""}
        </span>
      </div>
      {opts && opts.platforms.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 shrink-0 text-xs font-medium uppercase tracking-wide text-muted">Channels</span>
          {opts.platforms.map((p) => {
            const on = f.platforms.includes(p);
            return (
              <button key={p} type="button" aria-pressed={on}
                onClick={() => set({ platforms: on ? f.platforms.filter((x) => x !== p) : [...f.platforms, p], account: "" })}
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border py-0.5 pl-0.5 pr-2.5 text-xs transition-colors ${on ? "border-primary-500 bg-primary-50 font-semibold text-primary-800" : "border-line bg-white text-ink/80 hover:border-primary-300"}`}>
                <PlatformBadge platform={p} size={20} />{opts.platform_labels[p] ?? p}{on && <X size={12} />}
              </button>
            );
          })}
          {f.platforms.length === 0 && <span className="shrink-0 text-xs text-muted">All channels · tap to filter</span>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ body
function Body({ d, f, set }: { d: Insights; f: ReturnType<typeof useFilters>["f"]; set: ReturnType<typeof useFilters>["set"] }) {
  const nav = useNavigate();
  const colours = useMemo(() => channelColours([...d.filters.platforms, ...d.by_platform.map((r) => r.key)]), [d]);
  const labelOf = (p: string) => d.filters.platform_labels[p] ?? d.by_platform.find((r) => r.key === p)?.label ?? p;
  const dates = d.daily.map((x) => fmtDay(x.date));
  const longDates = d.daily.map((x, i) => fmtDayLong(x.date) + (i === d.daily.length - 1 && x.date === new Date().toLocaleDateString("en-CA") ? " (today, so far)" : ""));
  const [metric, setMetric] = useState<MetricKey>("impressions");
  const m = CHART_METRICS.find((x) => x.key === metric)!;
  const chartRef = useRef<HTMLDivElement>(null);
  // a KPI card's own daily series when there is one (it includes e.g. short-link clicks), else the daily column
  const metricValues = (key: MetricKey): number[] => {
    const k = d.kpis.find((x) => kpiMetric(x.key) === key);
    if (k && k.spark.length === d.daily.length) return k.spark.map((v) => Number(v) || 0);
    return key === "followers" ? d.followers.total.map((v) => v ?? 0) : d.daily.map((x) => Number(x[key as keyof Day]) || 0);
  };
  const values = metricValues(metric);
  const pickKpi = (key: string) => {
    const mk = kpiMetric(key);
    if (!mk) return;
    setMetric(mk);
    chartRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  // posts per day per channel (top channels coloured, the rest folded into "Other channels")
  const present = colours.ordered.filter((p) => d.daily.some((x) => x.by_platform[p]));
  const shown = present.filter((p) => colours.coloured.includes(p));
  const folded = present.filter((p) => !colours.coloured.includes(p));
  const postSeries: Series[] = [
    ...shown.map((p) => ({ key: p, name: labelOf(p), color: colours.map[p], values: d.daily.map((x) => x.by_platform[p] ?? 0) })),
    ...(folded.length ? [{ key: "other", name: `Other channels (${folded.map(labelOf).join(", ")})`, color: OTHER, values: d.daily.map((x) => folded.reduce((a, p) => a + (x.by_platform[p] ?? 0), 0)) }] : []),
  ];

  // 3D skyline: channels × days (≤ 21 days) or weeks
  const sky = useMemo(() => {
    const byWeek = d.daily.length > 21;
    const buckets: { label: string; days: Day[] }[] = [];
    d.daily.forEach((x, i) => {
      if (!byWeek) buckets.push({ label: fmtDay(x.date), days: [x] });
      else if (i % 7 === 0) buckets.push({ label: `Wk of ${fmtDay(x.date)}`, days: [x] });
      else buckets[buckets.length - 1].days.push(x);
    });
    const cols = buckets.slice(-14);
    const rows = [...shown, ...(folded.length ? ["other"] : [])];
    return {
      cols: cols.map((b) => b.label),
      rows: rows.map((p) => ({ key: p, label: p === "other" ? "Other channels" : labelOf(p) })),
      colors: rows.map((p) => (p === "other" ? OTHER : colours.map[p])),
      values: rows.map((p) => cols.map((b) => b.days.reduce((a, x) => a + (p === "other" ? folded.reduce((s, fp) => s + (x.by_platform[fp] ?? 0), 0) : x.by_platform[p] ?? 0), 0))),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d]);

  const kpis = d.kpis;
  const fp = d.followers.by_platform;
  const fShown = colours.ordered.filter((p) => colours.coloured.includes(p) && fp.some((x) => x.platform === p));
  const fRest = fp.filter((x) => !fShown.includes(x.platform));
  const followerSeries: Series[] = [
    ...fShown.map((p) => { const x = fp.find((y) => y.platform === p)!; return { key: p, name: x.label, color: colours.map[p], values: x.values }; }),
    ...(fRest.length ? [{ key: "other", name: `Other channels (${fRest.map((x) => x.label).join(", ")})`, color: OTHER,
      values: d.daily.map((_, i) => fRest.reduce((a, x) => a + (x.values[i] ?? 0), 0)) }] : []),
  ];
  const prevLabel = `${d.period.days}d`;
  const radarSeries = d.by_platform.slice(0, 3).map((r) => ({
    key: r.key, name: r.label, color: colours.map[r.key],
    values: { posts: r.posts, impressions: r.impressions, engagements: r.engagements, rate: r.engagement_rate,
      followers: d.channels.filter((c) => c.platform === r.key).reduce((a, c) => a + (c.followers ?? 0), 0) },
  }));
  const mixColours = ["#086fb8", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
  const totalMentions = d.mentions.totals.positive + d.mentions.totals.neutral + d.mentions.totals.negative;
  const sentimentScore = totalMentions ? Math.round(((d.mentions.totals.positive - d.mentions.totals.negative) / totalMentions) * 50 + 50) : null;
  const upcomingPlatforms = colours.ordered.filter((p) => d.upcoming.some((u) => u.by_platform[p]));

  return (
    <>
      {/* KPI row */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {kpis.map((k, i) => <KpiCard key={k.key} k={k} labels={longDates} prevLabel={prevLabel} color={SERIES[i % 2 === 0 ? 0 : 0]}
          active={kpiMetric(k.key) === metric} onClick={kpiMetric(k.key) ? () => pickKpi(k.key) : undefined} />)}
      </section>

      {/* performance over time + posts per channel */}
      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div ref={chartRef} className="min-w-0 scroll-mt-4">
        <Panel title="Performance over time" sub={metric === "followers" ? "Total followers each day, across the filtered channels" : `${m.label} per day, across the filtered channels`}
          action={<div className="flex flex-wrap justify-end gap-1">
            {CHART_METRICS.map((x) => (
              <button key={x.key} type="button" onClick={() => setMetric(x.key)} aria-pressed={metric === x.key}
                className={`rounded-md px-2 py-1 text-xs ${metric === x.key ? "bg-primary-50 font-semibold text-primary-800" : "text-muted hover:bg-slate-50 hover:text-ink"}`}>{x.label}</button>
            ))}
          </div>}>
          <WithTable table={<DataTable columns={[{ key: "d", label: "Day" }, { key: "v", label: m.label, align: "right" }]}
            rows={d.daily.map((x, i) => ({ d: fmtDayLong(x.date), v: m.format(values[i] ?? 0) }))} />}>
            <TimeChart labels={dates} tipLabels={longDates} height={280} area format={m.format}
              series={[{ key: metric, name: m.label, color: SERIES[0], values }]}
              empty="No metrics for this period yet. They appear a few hours after posts go out." />
          </WithTable>
        </Panel>
        </div>
        <Panel title="Posts published per day" sub="Stacked by channel · click a day to see its posts">
          <WithTable table={<DataTable columns={[{ key: "d", label: "Day" }, ...postSeries.map((s) => ({ key: s.key, label: s.name.split(" (")[0], align: "right" as const }))]}
            rows={d.daily.map((x, i) => ({ d: fmtDay(x.date), ...Object.fromEntries(postSeries.map((s) => [s.key, s.values[i] ?? 0])) }))} />}>
            <Columns labels={dates} tipLabels={longDates} series={postSeries} height={280} empty="Nothing published in this period."
              onSelect={(i) => { const day = d.daily[i].date; const next = new Date(`${day}T00:00:00`); next.setDate(next.getDate() + 1); nav(`/posts?status=published&start=${day}&end=${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`); }} />
          </WithTable>
        </Panel>
      </section>

      {/* mix & pipeline */}
      <section className="grid gap-5 lg:grid-cols-2 xl:grid-cols-3">
        <Panel title="Posts by channel" sub="Share of everything published · click a slice to filter">
          <Donut centerLabel="posts" onSelect={(k) => set({ platforms: [k], account: "" })}
            data={d.by_platform.map((r) => ({ key: r.key, label: r.label, value: r.posts, color: colours.map[r.key],
              detail: <><TipRow label="Impressions" value={fmtNum(r.impressions)} /><TipRow label="Engagements" value={fmtNum(r.engagements)} /><TipRow label="Engagement rate" value={fmtPct(r.engagement_rate)} /></> }))} />
        </Panel>
        <Panel title="How people engaged" sub="Likes, comments, shares, saves and clicks · 3D view" action={<Box size={16} className="text-muted" />}>
          <Pie3D data={d.engagement_mix.map((x, i) => ({ key: x.key, label: x.label, value: x.value, color: mixColours[i] }))} size={260} />
        </Panel>
        <Panel title="Content pipeline" sub="Posts due in this period, from plan to engagement">
          <Funnel steps={d.funnel} />
          {d.status_mix.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-1.5 text-xs">
              {d.status_mix.map((s) => (
                <Link key={s.key} to={`/posts?status=${s.key}`} className="rounded-full border border-line px-2 py-0.5 text-ink/80 hover:border-primary-300">
                  {s.label} <span className="font-semibold tabular-nums text-ink">{s.value}</span>
                </Link>
              ))}
            </div>
          )}
        </Panel>
      </section>

      {/* 3D activity + comparison */}
      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Panel title="Publishing activity in 3D" sub={`Posts per channel per ${d.daily.length > 21 ? "week" : "day"} · drag to turn, hover a bar`} action={<Layers size={16} className="text-muted" />}>
          <Skyline3D rows={sky.rows} cols={sky.cols} values={sky.values} colors={sky.colors} valueLabel="Posts" height={400} />
        </Panel>
        <Panel title="Channel comparison" sub="Your three busiest channels on five measures">
          <Radar size={300} series={radarSeries} axes={[
            { key: "posts", label: "Posts", format: fmtNum }, { key: "impressions", label: "Impressions", format: fmtNum },
            { key: "engagements", label: "Engagements", format: fmtNum }, { key: "rate", label: "Eng. rate", format: (v) => fmtPct(v) },
            { key: "followers", label: "Followers", format: fmtNum },
          ]} />
        </Panel>
      </section>

      {/* when & what works */}
      <section className="grid gap-5">
        <Panel title="When your posts perform best · 3D heatmap"
          sub={`Weekday × hour (${d.period.timezone}) · pick any measure for height and colour, split by channel, or play the weeks`}>
          <Heatmap3D cube={d.heat_cube ?? { weeks: [], cells: [] }} colorOf={(p) => colours.map[p] ?? OTHER} labelOf={labelOf} height={520} />
        </Panel>
        <Panel title="Reach against engagement" sub="Each bubble is a post · size = engagements · click to open">
          <Scatter xLabel="Impressions" yLabel="Engagement rate" formatY={(v) => fmtPct(v)} sizeLabel="Engagements" height={280}
            legend={[...new Set(d.top_posts.map((p) => p.platform))].map((p) => ({ label: labelOf(p), color: colours.map[p] ?? OTHER }))}
            points={d.top_posts.filter((p) => p.has_metrics).map((p) => ({
              key: p.id, x: p.impressions, y: p.engagement_rate, size: Math.max(1, p.engagements), label: p.title.slice(0, 70),
              group: `${p.platform_label} · ${fmtDayLong(p.published_at)}`, color: colours.map[p.platform] ?? OTHER,
              detail: <TipRow label="Clicks" value={fmtNum(p.clicks)} />, onClick: () => nav(`/posts/${p.id}`),
            }))} />
        </Panel>
      </section>

      <section className="grid gap-5 lg:grid-cols-2 xl:grid-cols-3">
        <Panel title="Engagement by post type" sub="Average engagement rate · posts in brackets">
          <HBars format={(v) => fmtPct(v)} empty="Nothing published yet."
            data={d.by_post_type.map((r, i) => ({ key: r.key, label: r.label, value: r.engagement_rate, color: SERIES[i] ?? OTHER, suffix: <span className="ml-1 text-muted">({r.posts})</span>,
              detail: <><TipRow label="Posts" value={r.posts} /><TipRow label="Impressions" value={fmtNum(r.impressions)} /><TipRow label="Engagements" value={fmtNum(r.engagements)} /></> }))} />
          <TypeSelect d={d} f={f} set={set} />
        </Panel>
        <Panel title="Topic pillars" sub="Engagements per topic · click to filter">
          <HBars empty="Posts aren't tagged with topic pillars yet."
            data={d.by_pillar.map((r) => ({ key: r.key, label: <button type="button" className="truncate text-left hover:underline" onClick={() => r.key !== "none" && set({ pillar: r.key })}>{r.label}</button>, text: r.label,
              value: r.engagements, color: r.color ?? SERIES[0],
              detail: <><TipRow label="Posts" value={r.posts} /><TipRow label="Engagement rate" value={fmtPct(r.engagement_rate)} /></> }))} />
        </Panel>
        <Panel title="Audience growth" sub={d.followers.change != null ? `${d.followers.change >= 0 ? "+" : ""}${fmtFull(d.followers.change)} followers in this period` : "Total followers across channels"}>
          <TimeChart labels={dates} tipLabels={longDates} height={230} stacked
            series={followerSeries} empty="Follower counts appear after the first daily collection." />
        </Panel>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title="Coming up: next 14 days" sub="Posts planned or scheduled per day, by channel">
          <Columns labels={d.upcoming.map((u) => fmtDay(u.date))} tipLabels={d.upcoming.map((u) => fmtDayLong(u.date))} height={220}
            series={[
              ...upcomingPlatforms.filter((p) => colours.coloured.includes(p)).map((p) => ({ key: p, name: labelOf(p), color: colours.map[p], values: d.upcoming.map((u) => u.by_platform[p] ?? 0) })),
              ...(upcomingPlatforms.some((p) => !colours.coloured.includes(p)) ? [{ key: "other", name: "Other channels", color: OTHER,
                values: d.upcoming.map((u) => upcomingPlatforms.filter((p) => !colours.coloured.includes(p)).reduce((a, p) => a + (u.by_platform[p] ?? 0), 0)) }] : []),
            ]} empty="Nothing planned yet. Schedules plan posts a day or two ahead." />
        </Panel>
        <Panel title="What people say about you" sub="Mentions found by social listening">
          <div className="flex flex-wrap items-center gap-4">
            <Gauge value={sentimentScore} label="Sentiment" sub={totalMentions ? `${totalMentions} mentions` : "No mentions"} size={170}
              detail={<><TipRow color="#1baf7a" label="Positive" value={d.mentions.totals.positive} /><TipRow color="#94a3b8" label="Neutral" value={d.mentions.totals.neutral} /><TipRow color="#e34948" label="Negative" value={d.mentions.totals.negative} /></>} />
            <div className="min-w-[12rem] flex-1">
              <Columns labels={dates} tipLabels={longDates} height={150} showLegend={false} empty="No mentions in this period."
                series={[{ key: "positive", name: "Positive", color: "#1baf7a", values: d.mentions.daily.map((x) => x.positive) },
                  { key: "neutral", name: "Neutral", color: "#94a3b8", values: d.mentions.daily.map((x) => x.neutral) },
                  { key: "negative", name: "Negative", color: "#e34948", values: d.mentions.daily.map((x) => x.negative) }]} />
            </div>
          </div>
        </Panel>
      </section>

      {/* channels table */}
      <Panel title="Channels" sub="Every connected account in the current filter">
        {d.channels.length === 0 ? <Empty h={100}>No connected accounts match these filters.</Empty> : (
          <div className="-mx-2 overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted">
                <tr><th className="px-2 py-2 font-medium">Account</th><th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 text-right font-medium">Followers</th><th className="px-2 py-2 font-medium">Trend</th>
                  <th className="px-2 py-2 text-right font-medium">Posts</th><th className="px-2 py-2 text-right font-medium">Impressions</th>
                  <th className="px-2 py-2 text-right font-medium">Engagements</th><th className="px-2 py-2 text-right font-medium">Eng. rate</th>
                  <th className="px-2 py-2 font-medium">Last post</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {d.channels.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50/70">
                    <td className="px-2 py-2.5">
                      <button type="button" className="flex items-center gap-2.5 text-left" onClick={() => set({ account: c.id })}>
                        <PlatformBadge platform={c.platform} size={26} />
                        <span className="min-w-0 leading-tight"><span className="block truncate font-medium hover:underline">{c.name}</span><span className="block text-xs text-muted">{c.platform_label}</span></span>
                      </button>
                    </td>
                    <td className="px-2 py-2.5"><StatePill status={c.status} /></td>
                    <td className="px-2 py-2.5 text-right tabular-nums">
                      {c.followers == null ? <span className="text-muted">—</span> : fmtNum(c.followers)}
                      {c.followers_change != null && c.followers_change !== 0 && <span className={`ml-1 text-xs ${c.followers_change > 0 ? "text-emerald-700" : "text-danger"}`}>{c.followers_change > 0 ? "+" : ""}{fmtNum(c.followers_change)}</span>}
                    </td>
                    <td className="px-2 py-2.5"><Sparkline values={c.followers_series} labels={longDates} title="Followers" color={colours.map[c.platform] ?? OTHER} width={96} height={28} /></td>
                    <td className="px-2 py-2.5 text-right tabular-nums">{c.posts}</td>
                    <td className="px-2 py-2.5 text-right tabular-nums">{fmtNum(c.impressions)}</td>
                    <td className="px-2 py-2.5 text-right tabular-nums">{fmtNum(c.engagements)}</td>
                    <td className="px-2 py-2.5 text-right tabular-nums">{c.impressions ? fmtPct(c.engagement_rate) : "—"}</td>
                    <td className="px-2 py-2.5 text-xs text-muted">{c.last_published_at ? fmtDayLong(c.last_published_at) : "Never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* top posts */}
      <Panel title="Top posts" sub="Ranked by engagements in this period" action={<Link to="/posts?status=published" className="text-sm text-primary-700 hover:underline">All posts</Link>}>
        {d.top_posts.length === 0 ? <Empty h={100}>Nothing published in this period.</Empty> : (
          <ol className="divide-y divide-line">
            {d.top_posts.slice(0, 6).map((p, i) => {
              const best = d.top_posts[0].engagements || 1;
              return (
                <li key={p.id} className="flex items-center gap-3 py-2.5">
                  <span className="w-5 text-center text-sm font-semibold tabular-nums text-muted">{i + 1}</span>
                  <PlatformBadge platform={p.platform} size={26} />
                  <Link to={`/posts/${p.id}`} className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium hover:underline">{p.title}</span>
                    <span className="block text-xs text-muted">{p.platform_label} · {p.post_type} · {fmtDayLong(p.published_at)}</span>
                  </Link>
                  <div className="hidden w-40 sm:block">
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${(p.engagements / best) * 100}%`, background: colours.map[p.platform] ?? OTHER }} /></div>
                  </div>
                  <div className="w-36 text-right text-xs tabular-nums text-muted">
                    <span className="font-semibold text-ink">{fmtNum(p.engagements)}</span> eng · {fmtNum(p.impressions)} impr
                    {p.has_metrics && p.impressions > 0 && <span className="block">{fmtPct(p.engagement_rate)} rate</span>}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </>
  );
}

function TypeSelect({ d, f, set }: { d: Insights; f: ReturnType<typeof useFilters>["f"]; set: ReturnType<typeof useFilters>["set"] }) {
  if (!d.by_post_type.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {d.by_post_type.map((r) => (
        <button key={r.key} type="button" onClick={() => set({ type: f.type === r.key ? "" : r.key })} aria-pressed={f.type === r.key}
          className={`rounded-full border px-2 py-0.5 text-xs ${f.type === r.key ? "border-primary-500 bg-primary-50 text-primary-800" : "border-line text-muted hover:text-ink"}`}>
          {f.type === r.key ? "Showing only " : "Only "}{r.label.toLowerCase()}
        </button>
      ))}
    </div>
  );
}

function KpiCard({ k, labels, prevLabel, color, active, onClick }: { k: Kpi; labels: string[]; prevLabel: string; color: string; active: boolean; onClick?: () => void }) {
  const fmt = k.format === "pct" ? (v: number) => fmtPct(v) : fmtNum;
  const good = k.delta == null ? null : k.invert ? k.delta < 0 : k.delta > 0;
  const Icon = k.delta == null || k.delta === 0 ? Minus : k.delta > 0 ? ArrowUpRight : ArrowDownRight;
  const Wrap = onClick ? "button" : "div";
  return (
    <Wrap type={onClick ? "button" : undefined} onClick={onClick} aria-pressed={onClick ? active : undefined}
      title={onClick ? `Show ${k.label.toLowerCase()} on the Performance over time chart` : undefined}
      className={`group relative overflow-hidden rounded-2xl border bg-surface p-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all ${onClick ? "cursor-pointer hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40" : ""} ${active ? "border-primary-500 ring-2 ring-primary-500/15" : "border-line hover:border-primary-300"}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{k.label}</div>
      <div className="mt-1 text-[26px] font-bold leading-tight tabular-nums text-ink">{fmt(k.value)}</div>
      <div className="mt-0.5 flex min-h-[22px] flex-wrap items-center gap-x-1 text-xs">
        {k.delta == null ? <span className="text-muted" title={`Nothing to compare with in the ${prevLabel} before this period`}>No data in prior {prevLabel}</span> : (<>
          <span className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-semibold tabular-nums ${k.delta === 0 ? "bg-slate-100 text-muted" : good ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-danger"}`}>
            <Icon size={12} />{Math.abs(k.delta).toFixed(1)}%
          </span>
        <span className="whitespace-nowrap text-muted" title={`Compared with the ${prevLabel} before this period`}>vs prior {prevLabel}</span></>)}
      </div>
      <div className="mt-2 -mx-1"><Sparkline values={k.spark} labels={labels} format={fmt} color={color} width={150} height={34} title={k.label} /></div>
      <div className="mt-1 text-[11px] text-muted">Before: <span className="tabular-nums text-ink/70">{fmt(k.previous)}</span></div>
    </Wrap>
  );
}

function StatePill({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    connected: ["bg-emerald-50 text-emerald-700", "● Connected"], manual: ["bg-violet-50 text-violet-700", "✋ Post by hand"],
    expired: ["bg-red-50 text-danger", "✕ Reconnect"], error: ["bg-red-50 text-danger", "✕ Error"],
  };
  const [cls, label] = map[status] ?? ["bg-slate-100 text-muted", status];
  return <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{label}</span>;
}

/** Chart with a "Table" switch, so every number is reachable without hovering. */
function WithTable({ children, table }: { children: ReactNode; table: ReactNode }) {
  const [tab, setTab] = useState(false);
  return (
    <div>
      {tab ? table : children}
      <div className="mt-2 flex justify-end">
        <button type="button" onClick={() => setTab(!tab)} className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
          <Table2 size={13} />{tab ? "Show chart" : "Show as table"}
        </button>
      </div>
    </div>
  );
}
