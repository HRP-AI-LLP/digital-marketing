import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Download, LineChart, Pause, Play, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Spinner, Stat, Textarea, formatDate } from "../../components/ui";
import { TrendChart } from "../analytics/vizkit";
import { CostNote, PositionChart, Sparkline, downloadCsv, num, pos, useOverview } from "./shared";
import type { History, RankOverview, RankRow } from "./types";

const short = (d: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${d}T00:00:00Z`));

function Change({ r }: { r: RankRow }) {
  if (r.lost) return <Badge tone="danger">Lost</Badge>;
  if (r.new) return <Badge tone="brand">New</Badge>;
  if (r.change === null || r.change === 0) return <span className="text-muted">–</span>;
  const up = r.change > 0;
  return (
    <span className={`inline-flex items-center gap-0.5 tabular-nums ${up ? "text-emerald-700" : "text-danger"}`} title={up ? `Up ${r.change} places` : `Down ${-r.change} places`}>
      {up ? <ArrowUp size={13} /> : <ArrowDown size={13} />}{Math.abs(r.change)}
    </span>
  );
}

function HistoryModal({ row, depth, onClose }: { row: RankRow; depth: number; onClose: () => void }) {
  const h = useQuery({ queryKey: ["keywords", "history", row.id], queryFn: () => api<History>(`/keywords/ranks/${row.id}/history?days=90`) });
  const pts = h.data?.points ?? [];
  return (
    <Modal title={`“${row.keyword}”`} onClose={onClose} wide>
      {h.isLoading ? <Spinner label="Loading…" /> : (
        <div className="space-y-4">
          <PositionChart title="Position over time" description={`Lower is better. Gaps mean not in the top ${depth}.`} maxPos={depth}
            labels={pts.map((p) => short(p.date))}
            series={[{ name: "Position", values: pts.map((p) => p.position) }, { name: "Search Console average", values: pts.map((p) => p.gsc_position), dashed: true }]} />
          {row.url && <p className="text-sm">Ranking page: <a className="text-primary-700 underline [overflow-wrap:anywhere]" href={row.url} target="_blank" rel="noreferrer">{row.url}</a></p>}
          {row.target_url && <p className="text-sm text-muted [overflow-wrap:anywhere]">Target page: {row.target_url} {row.target_ranking === false && row.position ? <Badge tone="accent">a different page ranks</Badge> : null}</p>}
          {(h.data?.serp_features.length ?? 0) > 0 && <p className="text-sm text-muted">On the results page: {h.data!.serp_features.map((f) => f.replace(/_/g, " ")).join(", ")}</p>}
          {(h.data?.top_urls.length ?? 0) > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Top results at the last check</h3>
              <ol className="list-decimal space-y-0.5 pl-5 text-sm">
                {h.data!.top_urls.map((u) => <li key={u} className="[overflow-wrap:anywhere]"><a className="text-primary-700 hover:underline" href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
              </ol>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function AddKeywords({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const o = useOverview();
  const [text, setText] = useState("");
  const [target, setTarget] = useState("");
  const [device, setDevice] = useState<string>(o.data?.settings.device ?? "desktop");
  const [loc, setLoc] = useState<number>(o.data?.settings.location_code ?? 2356);
  const add = useMutation({
    mutationFn: () => api<{ message: string }>("/keywords/ranks", { method: "POST", json: {
      keywords: text.split(/\n|,/).map((x) => x.trim()).filter(Boolean), target_url: target || null, device, location_code: loc } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["keywords"] }); },
  });
  return (
    <Modal title="Track keywords" onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        {add.error && <Alert>{(add.error as Error).message}</Alert>}
        {add.data && <Alert tone="info">{add.data.message}</Alert>}
        <Field label="Keywords" hint="One per line."><Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={"hrms software india\npayroll software"} /></Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Page you want to rank (optional)"><Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="https://…/pricing" /></Field>
          <Field label="Country">
            <Select value={loc} onChange={(e) => setLoc(Number(e.target.value))}>{(o.data?.locations ?? []).map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}</Select>
          </Field>
          <Field label="Device"><Select value={device} onChange={(e) => setDevice(e.target.value)}><option value="desktop">Desktop</option><option value="mobile">Mobile</option></Select></Field>
        </div>
        {!o.data?.provider.configured && <p className="text-xs text-muted">Free checks use web search, which doesn't change by country or device; those settings apply once DataForSEO is added.</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>{add.data ? "Done" : "Cancel"}</Button>
          <Button type="submit" disabled={add.isPending || !text.trim()}>Track</Button>
        </div>
      </form>
    </Modal>
  );
}

function Movers({ title, rows, depth }: { title: string; rows: RankRow[]; depth: number }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {!rows.length ? <p className="text-sm text-muted">None this week.</p> : (
        <ul className="space-y-1.5 text-sm">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate" title={r.keyword}>{r.keyword}</span>
              <span className={`shrink-0 tabular-nums ${(r.change_7d ?? 0) > 0 ? "text-emerald-700" : "text-danger"}`}>
                {(r.change_7d ?? 0) > 0 ? "+" : ""}{r.change_7d} · {pos(r.position, depth)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RankingsTab() {
  const { can } = useAuth();
  const manage = can("keywords.manage");
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const [adding, setAdding] = useState(false);
  const [hist, setHist] = useState<RankRow | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const r = useQuery({ queryKey: ["keywords", "ranks", days], queryFn: () => api<RankOverview>(`/keywords/ranks?days=${days}`) });
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: ["keywords"] }));
  const check = useMutation({ mutationFn: () => api<{ job_id: string }>("/keywords/ranks/check", { method: "POST", json: {} }), onSuccess: (x) => setJobId(x.job_id) });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: string; body: object }) => api(`/keywords/ranks/${id}`, { method: "PATCH", json: body }), onSuccess: () => qc.invalidateQueries({ queryKey: ["keywords", "ranks"] }) });
  const del = useMutation({ mutationFn: (id: string) => api(`/keywords/ranks/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["keywords"] }) });
  const d = r.data;
  if (r.isLoading || !d) return <Spinner label="Loading rankings…" />;
  const depth = d.depth;
  const running = job && ["queued", "running"].includes(job.status);
  const rows = d.rows.filter((x) => !q || x.keyword.toLowerCase().includes(q.toLowerCase()));
  const s = d.summary;
  const labels = d.series.map((p) => short(p.date));

  return (
    <div className="space-y-5">
      {d.approximate ? (
        <Alert tone="info">
          Free rank tracking: positions are <strong>approximate</strong>, read from web search results (top {depth}), with your
          Search Console average position shown beside them{d.gsc_connected ? "" : " once Search Console is connected in Analytics"}.
          For exact Google positions by country and device, add DataForSEO (optional, about $0.002 per keyword per day) in <Link className="underline" to="/settings/integrations">Integrations</Link>.
        </Alert>
      ) : <p className="text-sm text-muted">Google positions from DataForSEO, checked daily at {d.check_time} (top {depth}). Search Console's average position is shown beside them.</p>}
      {!d.our_domain && <Alert tone="warn">Add your website address in <Link className="underline" to="/settings/organization">Organization settings</Link> so we can find it in search results.</Alert>}

      <div className="flex flex-wrap items-center gap-2">
        {manage && <Button onClick={() => setAdding(true)}><Plus size={15} /> Track keywords</Button>}
        {manage && d.rows.length > 0 && <Button variant="secondary" disabled={!!running || check.isPending} onClick={() => check.mutate()}><RefreshCw size={15} className={running ? "animate-spin" : ""} /> Check now</Button>}
        {d.rows.length > 0 && <Button variant="ghost" onClick={() => downloadCsv(`rankings-${d.today}.csv`, [
          ["keyword", "position", "previous", "change", "best", "search_console_position", "url", "target_url", "volume", "device", "source", "last_checked"],
          ...d.rows.map((x) => [x.keyword, x.position, x.previous_position, x.change, x.best_position, x.gsc_position, x.url, x.target_url, x.volume, x.device, x.source, x.last_checked_at])])}>
          <Download size={15} /> CSV</Button>}
        {manage && d.rows.length > 0 && <CostNote body={{ action: "rank_check" }} />}
        <span className="ml-auto text-xs text-muted">{s.tracked} of {d.max_tracked} keywords{d.enabled ? ` · checked daily after ${d.check_time}` : " · daily checks are off"}</span>
      </div>
      {running && <Spinner label={job?.progress ?? "Checking positions…"} />}
      {job?.status === "done" && typeof job.result?.message === "string" && <Alert tone="info">{job.result.message}</Alert>}
      {check.error && <Alert>{(check.error as Error).message}</Alert>}

      {!d.rows.length ? (
        <EmptyState title="No keywords tracked yet" body="Track the searches that matter to your business and see every day where your website appears, what moved and why." action={manage && <Button onClick={() => setAdding(true)}><LineChart size={15} /> Track keywords</Button>} />
      ) : (
        <>
          <Card>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Visibility" value={s.visibility !== null ? `${s.visibility}%` : "—"} hint="Share of possible clicks" />
              <Stat label="Average position" value={s.avg_position ?? "—"} hint={`of ${s.top100} found`} />
              <Stat label="Top 3" value={s.top3} />
              <Stat label="Top 10" value={s.top10} />
              <Stat label="Improved" value={s.improved} hint="since the last check" />
              <Stat label="Declined" value={s.declined} />
            </div>
          </Card>
          <div className="grid gap-5 lg:grid-cols-2">
            <Card className="min-w-0">
              <div className="mb-2 flex justify-end">
                <Select aria-label="Period" className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
                  <option value={30}>Last 30 days</option><option value={90}>Last 90 days</option><option value={180}>Last 6 months</option>
                </Select>
              </div>
              <TrendChart title="Visibility index" description="0–100%: the clicks you'd get if every tracked keyword ranked #1, weighted by demand." labels={labels}
                series={[{ name: "Visibility", values: d.series.map((p) => p.visibility) }]} format={(v) => `${Math.round(v)}%`} />
            </Card>
            <Card className="min-w-0">
              <PositionChart title="Average position" description="Your tracked keywords, against Search Console's average." labels={labels} maxPos={depth}
                series={[{ name: d.approximate ? "Web search (approx.)" : "Google", values: d.series.map((p) => p.avg_position) },
                  { name: "Search Console", values: d.series.map((p) => p.avg_gsc_position), dashed: true }]} />
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Movers title="Best movers (7 days)" rows={d.best_movers} depth={depth} />
                <Movers title="Biggest drops (7 days)" rows={d.worst_movers} depth={depth} />
              </div>
            </Card>
          </div>
          <Card>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">Tracked keywords</h2>
              <Input aria-label="Filter" className="w-56" placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="-mx-5 overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-3 py-2 font-medium">Keyword</th>
                    <th className="px-3 py-2 font-medium">Position</th>
                    <th className="px-3 py-2 font-medium">Change</th>
                    <th className="px-3 py-2 font-medium">Best</th>
                    <th className="px-3 py-2 font-medium" title="Average position in Search Console">Search Console</th>
                    <th className="px-3 py-2 font-medium">Last {Math.min(30, days)} checks</th>
                    <th className="px-3 py-2 font-medium">Ranking page</th>
                    <th className="px-3 py-2 font-medium" aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((x) => (
                    <tr key={x.id} className={`border-b border-line/70 ${x.active ? "" : "opacity-60"}`}>
                      <td className="max-w-[260px] px-3 py-2 [overflow-wrap:anywhere]">
                        <button type="button" className="text-left font-medium text-ink hover:text-primary-700" onClick={() => setHist(x)}>{x.keyword}</button>
                        <span className="block text-xs text-muted">{x.device}{x.volume ? ` · ${num(x.volume)}/mo` : ""}{x.active ? "" : " · paused"}</span>
                      </td>
                      <td className="px-3 py-2 font-semibold tabular-nums">{x.last_checked_at ? pos(x.position, depth) : <span className="font-normal text-muted">not checked</span>}{x.source === "gsc" && x.position ? <span className="ml-1 text-xs font-normal text-muted">(SC)</span> : null}</td>
                      <td className="px-3 py-2"><Change r={x} /></td>
                      <td className="px-3 py-2 tabular-nums">{x.best_position ? `#${x.best_position}` : "—"}</td>
                      <td className="px-3 py-2 tabular-nums">{x.gsc_position ?? "—"}</td>
                      <td className="px-3 py-2"><Sparkline values={x.spark} invert label={`Positions: ${x.spark.map((v) => v ?? "–").join(", ")}`} /></td>
                      <td className="max-w-[220px] px-3 py-2 text-xs [overflow-wrap:anywhere]">
                        {x.url ? <a className="text-primary-700 hover:underline" href={x.url} target="_blank" rel="noreferrer">{x.url.replace(/^https?:\/\/(www\.)?/, "")}</a> : <span className="text-muted">—</span>}
                        {x.target_url && x.target_ranking === false && x.url && <span className="block text-amber-800">Not your target page</span>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right">
                        {manage && (
                          <>
                            <button type="button" className="mr-2 text-muted hover:text-ink" title={x.active ? "Pause" : "Resume"} aria-label={x.active ? "Pause" : "Resume"} onClick={() => patch.mutate({ id: x.id, body: { active: !x.active } })}>{x.active ? <Pause size={15} /> : <Play size={15} />}</button>
                            <button type="button" className="text-muted hover:text-danger" title="Stop tracking" aria-label="Stop tracking" onClick={() => { if (confirm(`Stop tracking “${x.keyword}”? Its history is deleted.`)) del.mutate(x.id); }}><Trash2 size={15} /></button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">Last checked {formatDate(d.rows.map((x) => x.last_checked_at).filter(Boolean).sort().at(-1) ?? null)}.</p>
          </Card>
        </>
      )}
      {adding && <AddKeywords onClose={() => setAdding(false)} />}
      {hist && <HistoryModal row={hist} depth={depth} onClose={() => setHist(null)} />}
    </div>
  );
}
