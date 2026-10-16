import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Badge, Card, Spinner } from "../../components/ui";
import { fmtNum, fmtPct } from "./charts";
import type { Growth, Health, Recs } from "./types";
import { Delta, Meter, ScoreRing, TrendChart, pctText, shortDay, signed } from "./vizkit";

type SiteOverview = {
  ga4: { current: Record<string, number> | null; deltas: Record<string, number | null> | null } | null;
  gsc: { current: { clicks: number; impressions: number; position: number } | null; deltas: Record<string, number | null> | null } | null;
  pagespeed: { mobile: { scores: Record<string, number> | null } } | null;
};

function Tile({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-0.5 text-2xl font-bold tabular-nums text-ink">{value}</div>
      {children}
    </div>
  );
}

function TabLink({ tab, children }: { tab: string; children: React.ReactNode }) {
  return <Link to={`/analytics?tab=${tab}`} className="inline-flex items-center gap-0.5 text-sm font-medium text-primary-700 hover:underline">{children}<ChevronRight size={15} /></Link>;
}

export function OverviewPanel() {
  const g = useQuery({ queryKey: ["analytics-growth", 30], queryFn: () => api<Growth>("/analytics/growth?days=30") });
  const h = useQuery({ queryKey: ["analytics-health"], queryFn: () => api<Health>("/analytics/health") });
  const r = useQuery({ queryKey: ["analytics-recs"], queryFn: () => api<Recs>("/analytics/recommendations") });
  const site = useQuery({ queryKey: ["analytics-overview"], queryFn: () => api<SiteOverview>("/analytics/overview") });
  if (g.isLoading || h.isLoading) return <Spinner label="Loading the overview" />;
  const err = g.error || h.error;
  if (err) return <Alert>{(err as Error).message}</Alert>;
  const w = g.data!.windows["30"];
  const health = h.data!;
  const weakest = health.parts.filter((p) => p.score != null).sort((a, b) => (a.score ?? 0) - (b.score ?? 0)).slice(0, 5);
  const tips = (r.data?.items ?? []).filter((x) => x.status === "new").slice(0, 3);
  const s = site.data;

  return (
    <div className="min-w-0 space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card className="ai-mesh-dark !border-0 text-white">
          <div className="flex flex-wrap items-center gap-5">
            <div className="rounded-full bg-white p-1.5"><ScoreRing score={health.score} size={124} /></div>
            <div className="min-w-[10rem] flex-1">
              <div className="text-sm text-secondary-200">Marketing health</div>
              <div className="text-2xl font-bold">{health.grade}</div>
              {health.change_7d != null && <div className="text-sm tabular-nums text-secondary-100">{health.change_7d >= 0 ? "▲" : "▼"} {Math.abs(health.change_7d)} points this week</div>}
              <Link to="/analytics?tab=health" className="mt-2 inline-flex items-center gap-0.5 text-sm font-medium text-secondary-300 hover:underline">See how to improve<ChevronRight size={15} /></Link>
            </div>
          </div>
          {weakest.length > 0 && (
            <div className="mt-5 space-y-2.5">
              <div className="text-xs font-medium text-secondary-200">Weakest parts of your score first</div>
              {weakest.map((p) => (
                <div key={p.key}>
                  <div className="flex justify-between text-sm"><span>{p.label}</span><span className="tabular-nums font-semibold">{p.score}</span></div>
                  <div className="mt-1"><Meter value={p.score} label={`${p.label} score`} /></div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <div className="min-w-0 space-y-6">
          <div className="grid grid-cols-2 gap-3">
            <Tile label="Followers" value={g.data!.totals.followers != null ? fmtNum(g.data!.totals.followers) : "—"}>
              <div className="text-xs text-muted">{signed(w.current.followers_net)} in 30 days{w.current.growth_rate != null ? ` (${pctText(w.current.growth_rate)})` : ""}</div>
            </Tile>
            <Tile label="Posts in 30 days" value={fmtNum(w.current.posts)}><Delta value={w.deltas.posts} suffix="vs previous 30 days" /></Tile>
            <Tile label="Engagements" value={fmtNum(w.current.engagements)}><Delta value={w.deltas.engagements} suffix="vs previous 30 days" /></Tile>
            <Tile label="Engagement rate" value={w.current.engagement_rate != null ? fmtPct(w.current.engagement_rate) : "—"}><Delta value={w.deltas.engagement_rate} suffix="vs previous 30 days" /></Tile>
          </div>
          <Card className="relative">
            <div className="absolute right-5 top-4"><TabLink tab="growth">Growth details</TabLink></div>
            <TrendChart title="Followers, last 30 days" labels={g.data!.followers_series.map((p) => shortDay(p.day))}
              series={[{ name: "Followers", values: g.data!.followers_series.map((p) => p.followers) }]} fromZero={false} height={180}
              empty="Follower counts are saved once a day; the trend fills in from the first reading." />
          </Card>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-baseline justify-between"><h2 className="font-semibold">Top tips right now</h2><TabLink tab="health">All tips</TabLink></div>
          {tips.length === 0 ? <p className="text-sm text-muted">No open tips. Open Health &amp; tips to get fresh ones.</p> : (
            <ol className="space-y-3">
              {tips.map((t, i) => (
                <li key={t.id} className="flex gap-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary-50 text-xs font-semibold text-primary-800">{i + 1}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 font-medium">{t.title} <Badge tone={t.impact === "high" ? "brand" : "neutral"}>{t.impact} impact</Badge></div>
                    {t.why && <p className="text-sm text-muted">{t.why}</p>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
        <Card>
          <div className="mb-3 flex items-baseline justify-between"><h2 className="font-semibold">Website and search</h2><TabLink tab="website">Details</TabLink></div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div><div className="text-sm text-muted">Sessions (28 days)</div><div className="text-xl font-bold tabular-nums">{s?.ga4?.current ? fmtNum(s.ga4.current.sessions ?? 0) : "—"}</div>
              {s?.ga4?.deltas && <Delta value={s.ga4.deltas.sessions} suffix="" />}</div>
            <div><div className="text-sm text-muted">Search clicks</div><div className="text-xl font-bold tabular-nums">{s?.gsc?.current ? fmtNum(s.gsc.current.clicks) : "—"}</div>
              {s?.gsc?.deltas && <Delta value={s.gsc.deltas.clicks} suffix="" />}</div>
            <div><div className="text-sm text-muted">Mobile speed</div><div className="text-xl font-bold tabular-nums">{s?.pagespeed?.mobile?.scores?.performance ?? "—"}<span className="text-sm font-normal text-muted">{s?.pagespeed?.mobile?.scores?.performance != null ? " / 100" : ""}</span></div></div>
          </div>
          {!s?.ga4 && !s?.gsc && <p className="mt-3 text-sm text-muted">Connect Google and pick your property in the Settings tab to see website traffic and search data.</p>}
        </Card>
      </div>
    </div>
  );
}
