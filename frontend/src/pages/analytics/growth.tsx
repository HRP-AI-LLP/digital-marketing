import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Info, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Select, Spinner, formatDate } from "../../components/ui";
import { BarChart, HeatGrid, fmtNum, fmtPct } from "./charts";
import type { Growth, GrowthAccount } from "./types";
import { ColumnChart, Delta, PlatformBadge, SERIES, TrendChart, pctText, shortDay, signed } from "./vizkit";

/** Refresh button + job progress shared by the growth and competitor tabs. */
export function useCollect(scope: "all" | "accounts" | "competitors") {
  const qc = useQueryClient();
  const [job, setJob] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/analytics/growth/refresh?scope=${scope}`, { method: "POST" }),
    onSuccess: (r) => setJob(r.job_id),
  });
  const running = useJob(job, () => qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("analytics-") }));
  const busy = (!!job && running?.status !== "done" && running?.status !== "failed") || start.isPending;
  return { start, running, busy, job };
}

export function CollectStatus({ c }: { c: ReturnType<typeof useCollect> }) {
  if (c.start.error) return <Alert>{(c.start.error as Error).message}</Alert>;
  if (c.busy) return <Spinner label={c.running?.progress ?? "Waiting for the background worker…"} />;
  if (c.running?.status === "failed") return <Alert>{c.running.error}</Alert>;
  if (c.running?.status === "done") return <Alert tone="info">{String(c.running.result?.message ?? "Updated.")}</Alert>;
  return null;
}

type WinKey = "7" | "30" | "90";

function Kpi({ label, value, delta, hint }: { label: string; value: string; delta?: number | null; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-0.5 text-2xl font-bold tabular-nums text-ink">{value}</div>
      {delta !== undefined ? <Delta value={delta} suffix="vs previous" /> : hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Segmented<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { id: T; label: string }[]; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {items.map((i) => (
        <button key={i.id} role="radio" aria-checked={value === i.id} onClick={() => onChange(i.id)}
          className={`rounded-md px-3 py-1 text-sm ${value === i.id ? "bg-primary-50 font-semibold text-primary-800" : "text-muted hover:text-ink"}`}>
          {i.label}
        </button>
      ))}
    </div>
  );
}
export { Segmented };

function AccountRow({ a }: { a: GrowthAccount }) {
  const l = a.latest;
  const extra = l ? [
    l.reach != null && `Reach ${fmtNum(l.reach)}`, l.impressions != null && `Views ${fmtNum(l.impressions)}`,
    l.profile_views != null && `Profile visits ${fmtNum(l.profile_views)}`, l.engagement != null && `Engagement ${fmtNum(l.engagement)}`,
    l.posts_count != null && `${fmtNum(l.posts_count)} posts`, l.views != null && (a.platform === "pinterest" ? `${fmtNum(l.views)} monthly views` : `${fmtNum(l.views)} total views`),
  ].filter(Boolean).join(" · ") : "";
  return (
    <tr className="border-b border-line align-top last:border-0">
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-2.5">
          <PlatformBadge platform={a.platform} />
          <div className="min-w-0">
            <div className="truncate font-medium text-ink">{a.name}</div>
            <div className="truncate text-xs text-muted">{a.platform_label}{a.handle ? ` · ${a.handle}` : ""}</div>
          </div>
        </div>
      </td>
      {!a.supported ? (
        <td colSpan={5} className="px-3 py-2.5 text-sm text-muted"><span className="inline-flex items-start gap-1.5"><Info size={14} className="mt-0.5 shrink-0" />{a.unsupported_reason ?? "Numbers aren't available for this account."}</span></td>
      ) : (
        <>
          <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{l?.followers != null ? fmtNum(l.followers) : "—"}</td>
          {(["7", "30", "90"] as const).map((k) => (
            <td key={k} className="px-3 py-2.5 text-right tabular-nums">
              <div>{signed(a.deltas[k].net)}</div>
              {a.deltas[k].rate != null && <div className="text-xs text-muted">{pctText(a.deltas[k].rate)}</div>}
            </td>
          ))}
          <td className="px-3 py-2.5 text-sm text-muted">
            {extra && <div>{extra}</div>}
            {!l && <div>Not collected yet. Press Refresh now.</div>}
            {a.notes.map((n) => <div key={n} className="mt-0.5 text-xs">{n}</div>)}
          </td>
        </>
      )}
    </tr>
  );
}

export function GrowthPanel() {
  const { can } = useAuth();
  const [days, setDays] = useState(30);
  const [win, setWin] = useState<WinKey>("30");
  const [view, setView] = useState<"total" | "accounts">("total");
  const q = useQuery({ queryKey: ["analytics-growth", days], queryFn: () => api<Growth>(`/analytics/growth?days=${days}`) });
  const collect = useCollect("all");
  const d = q.data;

  const withData = d ? d.accounts.filter((a) => a.series.some((p) => p.followers != null)) : [];
  const labels = d ? d.followers_series.map((p) => shortDay(p.day)) : [];
  const shown = withData.slice(0, SERIES.length);
  const accountSeries = shown.map((a) => {
    const byDay = new Map(a.series.map((p) => [p.day, p.followers]));
    let base: number | null = null;
    let last: number | null = null;
    return {
      name: a.name,
      values: (d?.followers_series ?? []).map((p) => {
        const v = byDay.get(p.day);
        if (v != null) last = v;
        if (last != null && base == null) base = last;
        return last != null && base ? +(((last - base) / base) * 100).toFixed(2) : null;
      }),
    };
  });
  const w = d?.windows[win];

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {d?.last_collected_at ? <>Follower counts last read {formatDate(d.last_collected_at)}. They're saved once a day.</> : "Follower counts are saved once a day."}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="growth-days" className="text-sm text-muted">Charts show</label>
          <Select id="growth-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40">
            <option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={180}>Last 180 days</option>
          </Select>
          {can("analytics.manage") && (
            <Button variant="secondary" onClick={() => collect.start.mutate()} disabled={collect.busy}>
              <RefreshCw size={16} className={collect.busy ? "animate-spin" : ""} /> Refresh now
            </Button>
          )}
        </div>
      </div>
      <CollectStatus c={collect} />
      {q.isLoading && <Spinner label="Loading growth trends" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {d && d.accounts.length === 0 && (
        <EmptyState title="No social accounts connected" body="Connect your accounts and the app will start saving follower counts every day."
          action={<Link to="/settings/accounts"><Button variant="secondary">Connect accounts</Button></Link>} />
      )}
      {d && d.accounts.length > 0 && w && (
        <>
          <section aria-labelledby="pop-h">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 id="pop-h" className="text-base font-semibold">How the last {win} days compare</h2>
              <Segmented label="Comparison period" value={win} onChange={setWin}
                items={[{ id: "7", label: "7 days" }, { id: "30", label: "30 days" }, { id: "90", label: "90 days" }]} />
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
              <Kpi label="New followers" value={signed(w.current.followers_net)} delta={w.deltas.followers_net} />
              <Kpi label="Growth rate" value={pctText(w.current.growth_rate ?? null)} hint={d.totals.followers != null ? `${fmtNum(d.totals.followers)} followers now` : "Collecting history"} />
              <Kpi label="Posts published" value={fmtNum(w.current.posts)} delta={w.deltas.posts} />
              <Kpi label="Impressions" value={fmtNum(w.current.impressions)} delta={w.deltas.impressions} />
              <Kpi label="Engagements" value={fmtNum(w.current.engagements)} delta={w.deltas.engagements} />
              <Kpi label="Engagement rate" value={w.current.engagement_rate != null ? fmtPct(w.current.engagement_rate) : "—"} delta={w.deltas.engagement_rate} />
            </div>
          </section>

          <Card>
            <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
              <Segmented label="Followers chart" value={view} onChange={setView}
                items={[{ id: "total", label: "Total" }, { id: "accounts", label: "Growth by account" }]} />
            </div>
            {view === "total" ? (
              <TrendChart title="Followers over time" description="All connected accounts that share follower counts" labels={labels}
                series={[{ name: "Followers", values: d.followers_series.map((p) => p.followers) }]} fromZero={false}
                empty="The trend appears after the first daily reading. Press Refresh now to take one." />
            ) : (
              <TrendChart title="Follower growth by account" description="Change since the first day in the period, in %, so small and large accounts share one scale"
                labels={labels} series={accountSeries} format={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`}
                empty="Not enough history yet."
                footer={withData.length > SERIES.length ? <p className="mt-2 text-xs text-muted">Showing the first {SERIES.length} accounts; see the table for the rest.</p> : null} />
            )}
          </Card>

          <Card className="!p-0">
            <div className="px-5 pt-4"><h2 className="text-sm font-semibold">Accounts</h2></div>
            <div className="overflow-x-auto">
              <table className="mt-2 w-full min-w-[720px] text-sm">
                <caption className="sr-only">Followers and growth per account</caption>
                <thead className="border-b border-line text-left text-muted">
                  <tr>
                    <th className="px-3 py-2 font-medium">Account</th><th className="px-3 py-2 text-right font-medium">Followers</th>
                    <th className="px-3 py-2 text-right font-medium">7 days</th><th className="px-3 py-2 text-right font-medium">30 days</th>
                    <th className="px-3 py-2 text-right font-medium">90 days</th><th className="px-3 py-2 font-medium">Latest numbers</th>
                  </tr>
                </thead>
                <tbody>{d.accounts.map((a) => <AccountRow key={a.id} a={a} />)}</tbody>
              </table>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <ColumnChart title="Posts per week" description={`Published posts, weeks starting Monday (${d.timezone})`}
                labels={d.weeks.map((x) => shortDay(x.week))} values={d.weeks.map((x) => x.posts)}
                hint={(i) => `${d.weeks[i].posts} posts · ${fmtNum(d.weeks[i].engagements)} engagements`} />
            </Card>
            <Card>
              <TrendChart title="Engagement rate by week" description="Engagements ÷ impressions for posts published that week"
                labels={d.weeks.map((x) => shortDay(x.week))} series={[{ name: "Engagement rate", values: d.weeks.map((x) => x.engagement_rate) }]}
                format={(v) => fmtPct(v)} height={200} empty="Appears once posts have metrics." />
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <BarChart title="Followers by platform" data={d.platforms.filter((p) => p.followers != null).map((p) => ({
                label: p.label, value: p.followers ?? 0, hint: `${p.label}: ${fmtNum(p.followers ?? 0)} followers${p.net != null ? `, ${signed(p.net)} in the period` : ""}`,
              }))} />
            </Card>
            <Card>
              <BarChart title="Engagement rate by platform" format={(v) => fmtPct(v)} data={d.platforms.filter((p) => p.engagement_rate != null).map((p) => ({
                label: p.label, value: p.engagement_rate ?? 0, hint: `${p.label}: ${fmtPct(p.engagement_rate ?? 0)} over ${p.posts} posts`,
              }))} />
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <BarChart title="Engagement rate by format" format={(v) => fmtPct(v)} data={d.by_post_type.map((g) => ({
                label: g.label, value: g.engagement_rate, hint: `${g.label}: ${fmtPct(g.engagement_rate)} over ${g.posts} posts`,
              }))} />
            </Card>
            <Card>
              <BarChart title="Engagement rate by topic pillar" format={(v) => fmtPct(v)} data={d.by_pillar.map((g) => ({
                label: g.label, value: g.engagement_rate, hint: `${g.label}: ${fmtPct(g.engagement_rate)} over ${g.posts} posts`,
              }))} />
            </Card>
          </div>

          <Card>
            <HeatGrid title="Best days and hours" description={`Average engagement rate of posts by weekday and hour (${d.timezone})`}
              cells={d.heatmap.map((c) => ({ weekday: c.weekday, hour: c.hour, value: c.avg_engagement_rate, posts: c.posts, enough: c.enough }))} />
            <div className="mt-3 text-sm">
              {d.best_times.length > 0 ? (
                <p>Best slots: {d.best_times.map((b, i) => (
                  <span key={i} className="mr-2 inline-block"><Badge tone="brand">{b.day} {String(b.hour).padStart(2, "0")}:00</Badge> <span className="tabular-nums text-muted">{fmtPct(b.avg_engagement_rate)}</span></span>
                ))}</p>
              ) : <p className="text-muted">{d.best_times_message}</p>}
            </div>
          </Card>

          <Card className="!p-0">
            <div className="px-5 pt-4"><h2 className="text-sm font-semibold">Top posts</h2></div>
            {d.top_posts.length === 0 ? <p className="px-5 py-6 text-sm text-muted">No posts were published in this period.</p> : (
              <div className="overflow-x-auto">
                <table className="mt-2 w-full min-w-[640px] text-sm">
                  <caption className="sr-only">Top posts</caption>
                  <thead className="border-b border-line text-left text-muted">
                    <tr><th className="px-3 py-2 font-medium">Post</th><th className="px-3 py-2 font-medium">Platform</th>
                      <th className="px-3 py-2 text-right font-medium">Impressions</th><th className="px-3 py-2 text-right font-medium">Engagements</th>
                      <th className="px-3 py-2 text-right font-medium">Rate</th></tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {d.top_posts.map((p) => (
                      <tr key={p.id} className="border-b border-line last:border-0">
                        <td className="max-w-md truncate px-3 py-2"><Link to={`/posts/${p.id}`} className="hover:text-primary-700 hover:underline">{p.title}</Link></td>
                        <td className="px-3 py-2"><span className="inline-flex items-center gap-1.5 text-muted"><PlatformBadge platform={p.platform} size={18} />{p.post_type}</span></td>
                        <td className="px-3 py-2 text-right">{p.has_metrics ? fmtNum(p.impressions) : "—"}</td>
                        <td className="px-3 py-2 text-right">{p.has_metrics ? fmtNum(p.engagements) : "—"}</td>
                        <td className="px-3 py-2 text-right">{p.has_metrics ? fmtPct(p.engagement_rate) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
