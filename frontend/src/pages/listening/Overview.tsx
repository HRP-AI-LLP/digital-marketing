import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Quote, RefreshCw, Sparkles } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { Alert, Button, Card, EmptyState, Select, Spinner } from "../../components/ui";
import { BarChart, Kpi, fmtNum } from "../analytics/charts";
import { SERIES, TrendChart, shortDay } from "../analytics/vizkit";
import { ago } from "../inbox/shared";
import type { Overview as Data, Query, Theme } from "./types";

const SENT_COLORS = { positive: "#1baf7a", neutral: "#94a3b8", negative: "#e34948" };

function TermCloud({ terms }: { terms: Data["top_terms"] }) {
  if (!terms.length) return <p className="py-6 text-center text-sm text-muted">Not enough mentions yet.</p>;
  const max = Math.max(...terms.map((t) => t.count));
  const min = Math.min(...terms.map((t) => t.count));
  return (
    <ul aria-label="Most used words" className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
      {terms.map((t) => {
        const w = max === min ? 0.5 : (t.count - min) / (max - min);
        return (
          <li key={t.term} title={`${t.term}: in ${t.count} mentions`}
            style={{ fontSize: `${0.8 + w * 0.9}rem`, opacity: 0.55 + w * 0.45 }}
            className={`leading-tight ${w > 0.6 ? "font-semibold text-brand-deep" : "text-ink"}`}>{t.term}</li>
        );
      })}
    </ul>
  );
}

function ThemeCard({ t }: { t: Theme }) {
  const dot = SENT_COLORS[t.sentiment] ?? SENT_COLORS.neutral;
  return (
    <li className="rounded-lg border border-line p-3">
      <p className="flex items-center gap-2 font-semibold"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: dot }} aria-hidden />{t.title}</p>
      {t.detail && <p className="mt-1 text-sm text-muted">{t.detail}</p>}
      {t.quotes.map((q, i) => (
        <blockquote key={i} className="mt-2 flex gap-2 text-sm [overflow-wrap:anywhere]">
          <Quote size={14} className="mt-0.5 shrink-0 text-muted" aria-hidden />
          <span>“{q.quote}” <span className="text-muted">— {q.author ?? q.source}</span>
            {q.url && <a href={q.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center text-brand hover:underline">source<ArrowUpRight size={12} /></a>}</span>
        </blockquote>
      ))}
      {t.links.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-sm">
          {t.links.slice(0, 3).map((l) => (
            <li key={l.id} className="truncate">
              <a href={l.url ?? "#"} target="_blank" rel="noreferrer" className="text-brand hover:underline">{l.title}</a>
              <span className="text-muted"> · {l.source_label}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function SummaryCard({ s }: { s: Data["summary"] }) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [days, setDays] = useState(String(s.days ?? 7));
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>("/listening/summary", { method: "POST", json: { days: Number(days) } }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const job = useJob(jobId, () => { setJobId(null); qc.invalidateQueries({ queryKey: ["listening-overview"] }); });
  const busy = start.isPending || (!!jobId && job?.status !== "failed");
  return (
    <Card className="lg:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><Sparkles size={16} className="text-secondary-600" /> What people are saying</h3>
          <p className="text-xs text-muted">
            {s.generated_at ? `Written ${ago(s.generated_at)} from ${s.count ?? 0} mentions in the last ${s.days} days.` : "A short summary with real quotes and links."}
            {s.method === "basic" && " (basic version, no AI model set up)"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select aria-label="Summary period" value={days} onChange={(e) => setDays(e.target.value)} className="w-32">
            <option value="1">Last day</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option>
          </Select>
          <Button variant="secondary" disabled={busy} onClick={() => start.mutate()}>
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} /> {busy ? "Writing…" : s.generated_at ? "Refresh" : "Write summary"}
          </Button>
        </div>
      </div>
      {(start.error || job?.status === "failed") && <div className="mt-3"><Alert>{(start.error as Error)?.message ?? job?.error ?? "Couldn't write the summary."}</Alert></div>}
      {s.summary && <p className="mt-3 text-[15px] leading-relaxed [overflow-wrap:anywhere]">{s.summary}</p>}
      {!!s.themes?.length && <ul className="mt-4 grid gap-3 md:grid-cols-2">{s.themes.map((t, i) => <ThemeCard key={i} t={t} />)}</ul>}
    </Card>
  );
}

export default function Overview({ queries }: { queries: Query[] }) {
  const [days, setDays] = useState("30");
  const [qid, setQid] = useState("");
  const q = useQuery({
    queryKey: ["listening-overview", days, qid],
    queryFn: () => api<Data>(`/listening/overview?days=${days}${qid ? `&query_id=${qid}` : ""}`),
  });
  const d = q.data;
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-2">
        <Select aria-label="Period" value={days} onChange={(e) => setDays(e.target.value)} className="w-40">
          <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option>
        </Select>
        <Select aria-label="Topic" value={qid} onChange={(e) => setQid(e.target.value)} className="w-56">
          <option value="">All topics</option>
          {queries.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </Select>
      </div>
      {q.isLoading ? <Spinner label="Loading…" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : !d ? null : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Kpi label="Mentions" value={fmtNum(d.totals.mentions)} delta={d.totals.change_pct} />
            <Kpi label="Positive" value={fmtNum(d.totals.positive)} hint={d.totals.mentions ? `${Math.round(d.totals.positive * 100 / d.totals.mentions)}% of mentions` : undefined} />
            <Kpi label="Negative" value={fmtNum(d.totals.negative)} hint={`${d.totals.negative_share}% of mentions`} />
            <Kpi label="Waiting for a reply" value={fmtNum(d.totals.needs_response)} hint="Questions, complaints, requests" />
            <Kpi label="About competitors" value={fmtNum(d.totals.competitor)} />
          </div>
          {!d.totals.mentions ? (
            <EmptyState title="No mentions in this period" body="Charts appear once your topics have been searched and mentions come in." />
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              <SummaryCard s={d.summary} />
              <Card>
                <TrendChart title="Mentions over time" description="By source, per day" labels={d.volume.map((v) => shortDay(v.date))}
                  series={d.volume_sources.slice(0, 8).map((s, i) => ({
                    name: d.sources.find((x) => x.id === s)?.label ?? s, values: d.volume.map((v) => Number(v[s] ?? 0)), color: SERIES[i],
                  }))} />
              </Card>
              <Card>
                <TrendChart title="Sentiment over time" description="Mentions per day" labels={d.sentiment_trend.map((v) => shortDay(v.date))}
                  series={(["positive", "neutral", "negative"] as const).map((k) => ({
                    name: k[0].toUpperCase() + k.slice(1), values: d.sentiment_trend.map((v) => v[k]), color: SENT_COLORS[k], dashed: k === "neutral",
                  }))} />
              </Card>
              <Card>
                <BarChart title="Share of voice" description="Your brand topics compared with competitor topics"
                  data={d.share_of_voice.map((r, i) => ({ label: r.is_us ? `${r.name} (you)` : r.name, value: r.share, color: r.is_us ? SERIES[0] : SERIES[(i % 7) + 1], hint: `${r.mentions} mentions` }))}
                  format={(v) => `${v}%`} max={100} />
                {!d.share_of_voice.length && <p className="text-xs text-muted">Add competitor topics to compare.</p>}
              </Card>
              <Card>
                <BarChart title="Where mentions come from" data={d.sources.map((s) => ({ label: s.label, value: s.mentions }))} />
              </Card>
              <Card>
                <BarChart title="Top news and web sites" data={d.top_domains.map((x) => ({ label: x.domain, value: x.mentions }))} />
              </Card>
              <Card>
                <h3 className="mb-2 text-sm font-semibold">Most active people</h3>
                {!d.top_authors.length ? <p className="py-6 text-center text-sm text-muted">No social posts yet.</p> : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[22rem] text-sm">
                      <thead><tr className="text-left text-xs text-muted"><th className="pb-1 font-medium">Person</th><th className="pb-1 font-medium">Where</th><th className="pb-1 text-right font-medium">Mentions</th><th className="pb-1 text-right font-medium">Engagement</th></tr></thead>
                      <tbody>
                        {d.top_authors.map((a) => (
                          <tr key={`${a.source}-${a.handle ?? a.name}`} className="border-t border-line">
                            <td className="py-1.5 pr-2 [overflow-wrap:anywhere]">{a.url ? <a className="hover:underline" href={a.url} target="_blank" rel="noreferrer">{a.name}</a> : a.name}</td>
                            <td className="py-1.5 pr-2 text-muted">{d.sources.find((s) => s.id === a.source)?.label ?? a.source}</td>
                            <td className="py-1.5 text-right tabular-nums">{a.mentions}</td>
                            <td className="py-1.5 text-right tabular-nums">{fmtNum(a.engagement)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
              <Card className="lg:col-span-2">
                <h3 className="text-sm font-semibold">Words people use</h3>
                <p className="mb-3 text-xs text-muted">The bigger the word, the more mentions it appears in. Your own tracked words are left out.</p>
                <TermCloud terms={d.top_terms} />
              </Card>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
