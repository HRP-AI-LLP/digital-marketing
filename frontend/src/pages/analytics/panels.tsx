import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ExternalLink, PenLine, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Select, Spinner, formatDate } from "../../components/ui";
import { BarChart, COMPARE, HeatGrid, Kpi, LineChart, MAIN, fmtDuration, fmtNum, fmtPct } from "./charts";

// ------------------------------------------------------------------ types
type Group = { key: string; label: string; posts: number; impressions: number; engagements: number; clicks: number; engagement_rate: number; color?: string | null };
type Social = {
  days: number; timezone: string; totals: Group & { reach: number };
  by_platform: Group[]; by_account: Group[]; by_pillar: Group[]; by_post_type: Group[];
  by_day: (Group & { date: string })[];
  heatmap: { weekday: number; day: string; hour: number; posts: number; avg_engagement_rate: number; enough: boolean }[];
  best_times: { day: string; hour: number; posts: number; avg_engagement_rate: number }[];
  best_times_message: string | null; min_bucket_posts: number;
  top_posts: { id: string; platform: string; post_type: string; title: string; published_at: string; external_url: string | null; impressions: number; engagements: number; clicks: number; engagement_rate: number; has_metrics: boolean }[];
};
type Connection = { connected: boolean; account: { name: string; status: string } | null; ga4_property: string | null; gsc_site: string | null; website: string | null; pagespeed_key: boolean };
type Snap<T> = { snapshot: { key: string; period_start: string | null; period_end: string | null; created_at: string; data: T } | null; connection: Connection };
type Ga4 = {
  metrics: string[];
  totals: { current: Record<string, number>; previous: Record<string, number>; deltas: Record<string, number | null>; conversion_metric: string | null };
  by_day: { date: string; sessions: number; activeUsers: number }[]; previous_by_day: number[];
  pages: { path: string; views: number; sessions: number }[]; channels: { name: string; sessions: number }[];
  sources: { name: string; sessions: number }[]; countries: { name: string; users: number }[]; devices: { name: string; sessions: number }[];
};
type GscRow = { clicks: number; impressions: number; ctr: number; position: number; query?: string; page?: string; date?: string; opportunity?: boolean };
type Gsc = { site: string; totals: { current: GscRow; previous: GscRow; deltas: Record<string, number | null> }; by_date: GscRow[]; queries: GscRow[]; pages: GscRow[] };
type Speed = Record<"mobile" | "desktop", {
  error?: string; scores?: Record<string, number>; field?: Record<string, { p75: number | null; category: string | null; unit: string }>;
  lab?: Record<string, { value: number | null; display: string | null; score: number | null }>; field_overall?: string | null;
}>;

const shortDate = (iso: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(iso));

function Updated({ at, period }: { at: string; period?: [string | null, string | null] }) {
  return <p className="mb-4 text-xs text-muted">Updated {formatDate(at)}{period?.[0] && ` · ${period[0]} to ${period[1]}`}</p>;
}

function NotSetUp({ title, body }: { title: string; body: string }) {
  return <EmptyState title={title} body={body} action={<Link to="/analytics?tab=settings"><Button variant="secondary">Open analytics settings</Button></Link>} />;
}

function Table({ head, children, caption }: { head: string[]; children: React.ReactNode; caption: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="border-b border-line text-left text-muted">
          <tr>{head.map((h, i) => <th key={h} className={`px-3 py-2 font-medium ${i ? "text-right" : ""}`}>{h}</th>)}</tr>
        </thead>
        <tbody className="tabular-nums">{children}</tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ social
export function SocialPanel() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["analytics-social", days], queryFn: () => api<Social>(`/analytics/social?days=${days}`) });
  const d = q.data;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end gap-2">
        <label htmlFor="days" className="text-sm text-muted">Period</label>
        <Select id="days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40">
          <option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </Select>
      </div>
      {q.isLoading && <Spinner label="Loading social performance" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {d && d.totals.posts === 0 && <EmptyState title="No published posts in this period" body="Numbers appear here after posts go out and their metrics are collected." />}
      {d && d.totals.posts > 0 && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Kpi label="Posts published" value={fmtNum(d.totals.posts)} />
            <Kpi label="Impressions" value={fmtNum(d.totals.impressions)} />
            <Kpi label="Engagements" value={fmtNum(d.totals.engagements)} hint="Likes, comments, shares, saves, clicks" />
            <Kpi label="Engagement rate" value={fmtPct(d.totals.engagement_rate)} />
            <Kpi label="Link clicks" value={fmtNum(d.totals.clicks)} />
          </div>
          <Card>
            <LineChart title="Engagements per day" labels={d.by_day.map((r) => shortDate(r.date))}
              series={[{ name: "Engagements", values: d.by_day.map((r) => r.engagements) }]} />
          </Card>
          <div className="grid gap-6 lg:grid-cols-3">
            <Card><BarChart title="Posts per platform" data={d.by_platform.map((g) => ({ label: g.label, value: g.posts }))} /></Card>
            <Card><BarChart title="Engagement rate by pillar" format={(v) => fmtPct(v)} data={d.by_pillar.map((g) => ({ label: g.label, value: g.engagement_rate, hint: `${g.label}: ${fmtPct(g.engagement_rate)} over ${g.posts} posts` }))} /></Card>
            <Card><BarChart title="Engagement rate by post type" format={(v) => fmtPct(v)} data={d.by_post_type.map((g) => ({ label: g.label, value: g.engagement_rate, hint: `${g.posts} posts` }))} /></Card>
          </div>
          <Card>
            <HeatGrid title="Best times to post" description={`Average engagement rate by weekday and hour (${d.timezone}).`}
              cells={d.heatmap.map((c) => ({ weekday: c.weekday, hour: c.hour, value: c.avg_engagement_rate, posts: c.posts, enough: c.enough }))} />
            <div className="mt-3 text-sm">
              {d.best_times.length > 0 ? (
                <p>Best slots: {d.best_times.map((b, i) => (
                  <span key={i} className="mr-2 inline-block"><Badge tone="brand">{b.day} {String(b.hour).padStart(2, "0")}:00</Badge> <span className="tabular-nums text-muted">{fmtPct(b.avg_engagement_rate)}</span></span>
                ))}</p>
              ) : <p className="text-muted">{d.best_times_message}</p>}
            </div>
          </Card>
          <Card>
            <h2 className="mb-2 text-sm font-semibold">Top posts</h2>
            <Table caption="Top posts" head={["Post", "Platform", "Impressions", "Engagements", "Rate"]}>
              {d.top_posts.map((p) => (
                <tr key={p.id} className="border-b border-line last:border-0">
                  <td className="max-w-md truncate px-3 py-2 font-sans"><Link to={`/posts/${p.id}`} className="hover:text-primary-700 hover:underline">{p.title}</Link></td>
                  <td className="px-3 py-2 text-right font-sans text-muted">{p.platform.replace(/_/g, " ")}</td>
                  <td className="px-3 py-2 text-right">{p.has_metrics ? fmtNum(p.impressions) : "—"}</td>
                  <td className="px-3 py-2 text-right">{p.has_metrics ? fmtNum(p.engagements) : "—"}</td>
                  <td className="px-3 py-2 text-right">{p.has_metrics ? fmtPct(p.engagement_rate) : "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ website (GA4)
const GA_LABELS: Record<string, [string, (v: number) => string]> = {
  sessions: ["Sessions", fmtNum], activeUsers: ["Active users", fmtNum], newUsers: ["New users", fmtNum],
  engagementRate: ["Engagement rate", (v) => fmtPct(v)], averageSessionDuration: ["Avg. session", fmtDuration],
  screenPageViews: ["Page views", fmtNum], keyEvents: ["Key events", fmtNum], conversions: ["Conversions", fmtNum],
};

export function WebsitePanel() {
  const q = useQuery({ queryKey: ["analytics-website"], queryFn: () => api<Snap<Ga4>>("/analytics/website") });
  if (q.isLoading) return <Spinner label="Loading website analytics" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const s = q.data?.snapshot;
  if (!s) return <NotSetUp title="No Google Analytics data yet" body="Connect Google, pick your GA4 property in Settings and refresh." />;
  const t = s.data.totals;
  const prev = s.data.previous_by_day;
  return (
    <div>
      <Updated at={s.created_at} period={[s.period_start, s.period_end]} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {s.data.metrics.map((m) => {
          const [label, f] = GA_LABELS[m] ?? [m, fmtNum];
          return <Kpi key={m} label={label} value={f(t.current[m] ?? 0)} delta={t.deltas[m]} />;
        })}
      </div>
      <Card className="mt-6">
        <LineChart title="Sessions by day" description="Last 28 days, with the previous 28 days for comparison"
          labels={s.data.by_day.map((r) => shortDate(r.date))}
          series={[{ name: "This period", values: s.data.by_day.map((r) => r.sessions), color: MAIN },
            ...(prev?.length ? [{ name: "Previous period", values: prev.slice(-s.data.by_day.length), color: COMPARE, dashed: true }] : [])]} />
      </Card>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-2 text-sm font-semibold">Top pages</h2>
          <Table caption="Top pages" head={["Page", "Views", "Sessions"]}>
            {s.data.pages.map((p) => (
              <tr key={p.path} className="border-b border-line last:border-0">
                <td className="max-w-xs truncate px-3 py-2 font-sans" title={p.path}>{p.path}</td>
                <td className="px-3 py-2 text-right">{fmtNum(p.views)}</td>
                <td className="px-3 py-2 text-right">{fmtNum(p.sessions)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <div className="space-y-6">
          <Card><BarChart title="Sessions by channel" data={s.data.channels.map((c) => ({ label: c.name, value: c.sessions }))} /></Card>
          <Card><BarChart title="Sessions by source" data={s.data.sources.map((c) => ({ label: c.name, value: c.sessions }))} /></Card>
          <div className="grid gap-6 sm:grid-cols-2">
            <Card><BarChart title="Devices" data={s.data.devices.map((c) => ({ label: c.name, value: c.sessions }))} /></Card>
            <Card><BarChart title="Countries (users)" data={s.data.countries.map((c) => ({ label: c.name, value: c.users }))} /></Card>
          </div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ search (GSC)
export function SearchPanel() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [onlyOpp, setOnlyOpp] = useState(false);
  const q = useQuery({ queryKey: ["analytics-search"], queryFn: () => api<Snap<Gsc>>("/analytics/search") });
  if (q.isLoading) return <Spinner label="Loading Search Console data" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const s = q.data?.snapshot;
  if (!s) return <NotSetUp title="No Search Console data yet" body="Connect Google, pick your Search Console site in Settings and refresh." />;
  const { current: c, deltas } = s.data.totals;
  const rows = s.data.queries.filter((r) => !onlyOpp || r.opportunity).slice(0, 50);
  return (
    <div>
      <Updated at={s.created_at} period={[s.period_start, s.period_end]} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Clicks" value={fmtNum(c.clicks)} delta={deltas.clicks} />
        <Kpi label="Impressions" value={fmtNum(c.impressions)} delta={deltas.impressions} />
        <Kpi label="Click-through rate" value={fmtPct(c.ctr)} delta={deltas.ctr} />
        <Kpi label="Average position" value={c.position.toFixed(1)} delta={deltas.position} invert hint="Lower is better" />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card><LineChart title="Clicks by day" labels={s.data.by_date.map((r) => shortDate(r.date!))} series={[{ name: "Clicks", values: s.data.by_date.map((r) => r.clicks) }]} /></Card>
        <Card><LineChart title="Impressions by day" labels={s.data.by_date.map((r) => shortDate(r.date!))} series={[{ name: "Impressions", values: s.data.by_date.map((r) => r.impressions) }]} /></Card>
      </div>
      <Card className="mt-6">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Top queries</h2>
            <p className="text-xs text-muted">Opportunities rank in positions 5–20 or get many impressions with few clicks. A focused article can move them up.</p>
          </div>
          <label className="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-primary-600" checked={onlyOpp} onChange={(e) => setOnlyOpp(e.target.checked)} />
            Only opportunities
          </label>
        </div>
        <Table caption="Top search queries" head={["Query", "Clicks", "Impressions", "CTR", "Position", ""]}>
          {rows.map((r) => (
            <tr key={r.query} className="border-b border-line last:border-0">
              <td className="px-3 py-2 font-sans">{r.query} {r.opportunity && <Badge tone="accent">Opportunity</Badge>}</td>
              <td className="px-3 py-2 text-right">{fmtNum(r.clicks)}</td>
              <td className="px-3 py-2 text-right">{fmtNum(r.impressions)}</td>
              <td className="px-3 py-2 text-right">{fmtPct(r.ctr)}</td>
              <td className="px-3 py-2 text-right">{r.position.toFixed(1)}</td>
              <td className="px-3 py-2 text-right">
                {r.opportunity && can("seo.manage") && (
                  <Button variant="ghost" className="h-7 px-2" onClick={() => navigate(`/seo?keyword=${encodeURIComponent(r.query ?? "")}`)}>
                    <PenLine size={14} /> Write an article
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </Table>
        {rows.length === 0 && <p className="py-4 text-center text-sm text-muted">No queries to show.</p>}
      </Card>
      <Card className="mt-6">
        <h2 className="mb-2 text-sm font-semibold">Top pages in search</h2>
        <Table caption="Top pages in search" head={["Page", "Clicks", "Impressions", "CTR", "Position"]}>
          {s.data.pages.slice(0, 25).map((r) => (
            <tr key={r.page} className="border-b border-line last:border-0">
              <td className="max-w-sm truncate px-3 py-2 font-sans" title={r.page}>{r.page}</td>
              <td className="px-3 py-2 text-right">{fmtNum(r.clicks)}</td>
              <td className="px-3 py-2 text-right">{fmtNum(r.impressions)}</td>
              <td className="px-3 py-2 text-right">{fmtPct(r.ctr)}</td>
              <td className="px-3 py-2 text-right">{r.position.toFixed(1)}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ speed
const SCORE_LABEL: Record<string, string> = { performance: "Performance", accessibility: "Accessibility", "best-practices": "Best practices", seo: "SEO" };
const VITALS: [string, string, string][] = [
  ["lcp", "Largest contentful paint", "How fast the main content shows"], ["inp", "Interaction to next paint", "How quickly the page responds"],
  ["cls", "Cumulative layout shift", "How much the layout jumps"], ["fcp", "First contentful paint", "When something first appears"],
  ["ttfb", "Time to first byte", "Server response time"],
];
const LAB: [string, string][] = [["lcp", "Largest contentful paint"], ["fcp", "First contentful paint"], ["tbt", "Total blocking time"], ["cls", "Cumulative layout shift"], ["si", "Speed index"]];

function ScoreDial({ label, score }: { label: string; score: number }) {
  const tone = score >= 90 ? "#047857" : score >= 50 ? "#b45309" : "#b91c1c";
  const r = 26, circ = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center gap-1 text-center">
      <svg width="68" height="68" role="img" aria-label={`${label} ${score} out of 100`}>
        <circle cx="34" cy="34" r={r} fill="none" stroke="#e2e8f0" strokeWidth="6" />
        <circle cx="34" cy="34" r={r} fill="none" stroke={tone} strokeWidth="6" strokeLinecap="round" strokeDasharray={`${(score / 100) * circ} ${circ}`} transform="rotate(-90 34 34)" />
        <text x="34" y="34" dy="0.35em" textAnchor="middle" fontSize="17" fontWeight="700" fill={tone} style={{ fontVariantNumeric: "tabular-nums" }}>{score}</text>
      </svg>
      <span className="text-xs text-muted">{label}</span>
    </div>
  );
}

const CATEGORY: Record<string, ["brand" | "accent" | "danger" | "neutral", string]> = { FAST: ["brand", "Good"], AVERAGE: ["accent", "Needs improvement"], SLOW: ["danger", "Poor"] };

export function SpeedPanel() {
  const q = useQuery({ queryKey: ["analytics-pagespeed"], queryFn: () => api<Snap<Speed>>("/analytics/pagespeed") });
  if (q.isLoading) return <Spinner label="Loading PageSpeed results" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const s = q.data?.snapshot;
  if (!s) return <NotSetUp title="No PageSpeed report yet" body={q.data?.connection.website ? "Run a refresh to test your website." : "Add your website in Settings → Organization, then refresh."} />;
  return (
    <div>
      <Updated at={s.created_at} />
      <p className="-mt-3 mb-4 text-sm text-muted">Tested <a className="text-primary-700 underline" href={s.key} target="_blank" rel="noreferrer">{s.key}</a></p>
      <div className="grid gap-6 lg:grid-cols-2">
        {(["mobile", "desktop"] as const).map((k) => {
          const r = s.data[k];
          return (
            <Card key={k}>
              <h2 className="mb-3 font-semibold">{k === "mobile" ? "Mobile" : "Desktop"}</h2>
              {!r || r.error ? <Alert tone="warn">{r?.error ?? "No result."}</Alert> : (
                <>
                  <div className="flex flex-wrap justify-around gap-3">
                    {Object.entries(r.scores ?? {}).map(([cat, v]) => <ScoreDial key={cat} label={SCORE_LABEL[cat] ?? cat} score={v} />)}
                  </div>
                  <h3 className="mb-2 mt-5 text-sm font-semibold">Core Web Vitals (real users, 75th percentile)</h3>
                  {Object.keys(r.field ?? {}).length === 0 ? <p className="text-sm text-muted">Not enough real-user data from Chrome for this site yet.</p> : (
                    <ul className="space-y-1.5 text-sm">
                      {VITALS.filter(([key]) => r.field?.[key]).map(([key, label, help]) => {
                        const f = r.field![key];
                        const [tone, word] = CATEGORY[f.category ?? ""] ?? ["neutral", f.category ?? "—"];
                        const val = f.p75 == null ? "—" : key === "cls" ? f.p75.toFixed(2) : f.p75 >= 1000 ? `${(f.p75 / 1000).toFixed(1)} s` : `${f.p75} ms`;
                        return (
                          <li key={key} className="flex items-center justify-between gap-2">
                            <span>{label}<span className="block text-xs text-muted">{help}</span></span>
                            <span className="flex items-center gap-2"><span className="font-semibold tabular-nums">{val}</span><Badge tone={tone}>{word}</Badge></span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <h3 className="mb-2 mt-5 text-sm font-semibold">Lab test</h3>
                  <ul className="space-y-1 text-sm">
                    {LAB.filter(([key]) => r.lab?.[key]).map(([key, label]) => (
                      <li key={key} className="flex justify-between"><span>{label}</span><span className="tabular-nums">{r.lab![key].display ?? "—"}</span></li>
                    ))}
                  </ul>
                </>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ settings
type SettingsData = {
  connection: Connection; ga4_property: string | null; gsc_site: string | null;
  ga4_options: { id: string; label: string }[]; gsc_options: { id: string; label: string }[]; errors: string[]; can_manage: boolean;
};
type Overview = { last_refresh: { at: string; message?: string } | null };

export function SettingsPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["analytics-settings"], queryFn: () => api<SettingsData>("/analytics/settings") });
  const ov = useQuery({ queryKey: ["analytics-overview"], queryFn: () => api<Overview>("/analytics/overview") });
  const [ga4, setGa4] = useState("");
  const [gsc, setGsc] = useState("");
  const [job, setJob] = useState<string | null>(null);
  useEffect(() => { if (q.data) { setGa4(q.data.ga4_property ?? ""); setGsc(q.data.gsc_site ?? ""); } }, [q.data]);
  const save = useMutation({
    mutationFn: () => api("/analytics/settings", { method: "PUT", json: { ga4_property: ga4 || null, gsc_site: gsc || null } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["analytics-settings"] }),
  });
  const refresh = useMutation({ mutationFn: () => api<{ job_id: string }>("/analytics/refresh", { method: "POST" }), onSuccess: (r) => setJob(r.job_id) });
  const running = useJob(job, () => { qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("analytics-") }); });

  if (q.isLoading) return <Spinner label="Loading settings" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const d = q.data!;
  const c = d.connection;
  const busy = !!job && running?.status !== "done" && running?.status !== "failed";
  const withCurrent = (opts: { id: string; label: string }[], cur: string) => (cur && !opts.some((o) => o.id === cur) ? [{ id: cur, label: cur }, ...opts] : opts);

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <h2 className="mb-1 font-semibold">Google connection</h2>
        {c.connected ? (
          <p className="text-sm text-muted">Connected as <span className="font-medium text-ink">{c.account?.name}</span>.</p>
        ) : (
          <div className="space-y-3">
            <Alert tone="warn">Google isn't connected. Analytics and Search Console need it; PageSpeed works with just your website address.</Alert>
            <Link to="/settings/accounts"><Button variant="secondary"><ExternalLink size={16} /> Connect Google in Social accounts</Button></Link>
          </div>
        )}
        {d.errors.map((e) => <div key={e} className="mt-2"><Alert tone="warn">{e}</Alert></div>)}
        <div className="mt-4 space-y-4">
          <Field label="Google Analytics 4 property">
            <Select value={ga4} onChange={(e) => setGa4(e.target.value)} disabled={!c.connected || !d.can_manage}>
              <option value="">Not selected</option>
              {withCurrent(d.ga4_options, ga4).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </Select>
          </Field>
          <Field label="Search Console site">
            <Select value={gsc} onChange={(e) => setGsc(e.target.value)} disabled={!c.connected || !d.can_manage}>
              <option value="">Not selected</option>
              {withCurrent(d.gsc_options, gsc).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </Select>
          </Field>
          {save.error && <Alert>{(save.error as Error).message}</Alert>}
          {save.isSuccess && <Alert tone="info">Saved. Refresh to load the data.</Alert>}
          {d.can_manage ? (
            <Button onClick={() => save.mutate()} disabled={!c.connected || save.isPending}>Save selection</Button>
          ) : <p className="text-xs text-muted">You need the integration.manage permission to change these.</p>}
        </div>
      </Card>
      <Card>
        <h2 className="mb-1 font-semibold">Refresh</h2>
        <p className="text-sm text-muted">Data refreshes every morning at about 06:00. Website: <span className="text-ink">{c.website ?? "not set"}</span>{c.pagespeed_key ? " · PageSpeed key set" : " · PageSpeed without a key (lower limits)"}.</p>
        {ov.data?.last_refresh && (
          <p className="mt-3 text-sm"><span className="text-muted">Last run {formatDate(ov.data.last_refresh.at)}:</span> {ov.data.last_refresh.message}</p>
        )}
        <div className="mt-4 space-y-3">
          <Button onClick={() => refresh.mutate()} disabled={busy || refresh.isPending}><RefreshCw size={16} className={busy ? "animate-spin" : ""} /> Refresh now</Button>
          {refresh.error && <Alert>{(refresh.error as Error).message}</Alert>}
          {job && running?.status === "failed" && <Alert>{running.error}</Alert>}
          {busy && <Spinner label={running?.progress ?? "Waiting for the worker…"} />}
          {running?.status === "done" && <Alert tone="info">{String(running.result?.message ?? "Done.")}</Alert>}
        </div>
      </Card>
    </div>
  );
}
