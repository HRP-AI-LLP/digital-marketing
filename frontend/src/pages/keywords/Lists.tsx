import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, LineChart, Network, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Spinner, Toggle, formatDate } from "../../components/ui";
import { useTrack, useWriteArticle } from "./Research";
import { CostNote, Demand, IntentBadge, Kd, downloadCsv, num, sourceLabel, useOverview } from "./shared";
import type { KwList } from "./types";

const METHOD: Record<string, string> = {
  serp: "shared search results", embeddings: "meaning (embeddings)", ai: "AI grouping", words: "shared words",
};

function ListDetail({ id }: { id: string }) {
  const { can } = useAuth();
  const manage = can("keywords.manage");
  const qc = useQueryClient();
  const o = useOverview();
  const [useSerp, setUseSerp] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [view, setView] = useState<"clusters" | "keywords">("clusters");
  const l = useQuery({ queryKey: ["keywords", "list", id], queryFn: () => api<KwList>(`/keywords/lists/${id}`) });
  const job = useJob(jobId, () => { qc.invalidateQueries({ queryKey: ["keywords", "list", id] }); qc.invalidateQueries({ queryKey: ["keywords", "lists"] }); });
  const cluster = useMutation({ mutationFn: () => api<{ job_id: string }>(`/keywords/lists/${id}/cluster`, { method: "POST", json: { use_serp: useSerp } }), onSuccess: (r) => setJobId(r.job_id) });
  const remove = useMutation({ mutationFn: (k: string) => api<KwList>(`/keywords/lists/${id}/items/remove`, { method: "POST", json: { keywords: [k] } }), onSuccess: (x) => qc.setQueryData(["keywords", "list", id], x) });
  const write = useWriteArticle();
  const track = useTrack();
  useEffect(() => { if (l.data && !(l.data.clusters ?? []).length) setView("keywords"); }, [l.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!l.data) return <Spinner label="Loading…" />;
  const kl = l.data;
  const items = kl.items ?? [];
  const clusters = kl.clusters ?? [];
  const byKw = new Map(items.map((i) => [i.keyword, i]));
  const running = job && ["queued", "running"].includes(job.status);
  const hasVolume = items.some((i) => i.volume !== null);

  return (
    <Card className="min-w-0">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold [overflow-wrap:anywhere]">{kl.name}</h2>
          <p className="text-xs text-muted">{kl.count} keywords{kl.volume ? ` · ${num(kl.volume)} searches/mo` : ""} · updated {formatDate(kl.updated_at)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {manage && <Button variant="secondary" disabled={track.isPending} onClick={() => track.mutate(items)}><LineChart size={15} /> Track all</Button>}
          <Button variant="ghost" onClick={() => {
            const of = new Map(clusters.flatMap((c) => c.keywords.map((k) => [k, c.name] as const)));
            downloadCsv(`${kl.name.replace(/\W+/g, "-").toLowerCase() || "keywords"}.csv`, [["keyword", "cluster", "volume", "estimated_demand", "kd", "cpc_usd", "intent", "source"],
              ...items.map((i) => [i.keyword, of.get(i.keyword) ?? "", i.volume, i.demand ?? "", i.kd, i.cpc, i.intent, sourceLabel(i.source)])]);
          }}><Download size={15} /> CSV</Button>
        </div>
      </div>
      {track.data && <div className="mb-3"><Alert tone="info">{track.data.message}</Alert></div>}
      {(write.error || cluster.error) && <div className="mb-3"><Alert>{((write.error || cluster.error) as Error).message}</Alert></div>}

      {manage && items.length >= 2 && (
        <div className="mb-4 rounded-lg border border-line p-3">
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={!!running || cluster.isPending} onClick={() => cluster.mutate()}><Network size={15} /> {clusters.length ? "Group again" : "Group into topics"}</Button>
            <Toggle checked={useSerp} onChange={setUseSerp} label={o.data?.provider.configured ? "Use Google results (most accurate)" : "Use web search results (free, approximate)"} />
            <CostNote body={{ action: "cluster", list_id: id, use_serp: useSerp }} />
          </div>
          <p className="mt-2 text-xs text-muted">Keywords that bring up the same pages in search belong in one article. Without search results, they're grouped by meaning.</p>
          {running && <div className="mt-2"><Spinner label={job?.progress ?? "Grouping…"} /></div>}
        </div>
      )}

      {clusters.length > 0 && (
        <div role="tablist" className="mb-3 inline-flex rounded-lg border border-line p-0.5 text-sm">
          {(["clusters", "keywords"] as const).map((v) => (
            <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)} className={`rounded-md px-3 py-1 ${view === v ? "bg-brand-tint font-medium text-brand-deep" : "text-muted"}`}>
              {v === "clusters" ? `Topic clusters (${clusters.length})` : `All keywords (${items.length})`}
            </button>
          ))}
        </div>
      )}

      {view === "clusters" && clusters.length > 0 ? (
        <>
          <p className="mb-3 text-xs text-muted">
            Grouped by {METHOD[kl.cluster_meta.method ?? ""] ?? kl.cluster_meta.method}{kl.cluster_meta.approximate ? " (approximate)" : ""}
            {kl.cluster_meta.cost_usd ? ` · cost $${kl.cluster_meta.cost_usd.toFixed(3)}` : ""}. Each cluster is one article: the pillar keyword is its main topic.
            {kl.cluster_meta.note ? ` ${kl.cluster_meta.note}` : ""}
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {clusters.map((c) => (
              <div key={c.id} className="flex min-w-0 flex-col rounded-lg border border-line p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="font-semibold [overflow-wrap:anywhere]">{c.name}</h3>
                    <p className="text-xs text-muted">Pillar: <span className="text-ink">{c.pillar}</span> · {c.keywords.length} keyword{c.keywords.length === 1 ? "" : "s"}
                      {c.volume ? ` · ${num(c.volume)}/mo` : ""}{c.kd !== null ? ` · difficulty ${c.kd}` : ""}</p>
                  </div>
                  <IntentBadge intent={c.intent} />
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {c.keywords.map((k) => <span key={k} className={`rounded px-1.5 py-0.5 text-xs [overflow-wrap:anywhere] ${k === c.pillar ? "bg-brand-tint text-brand-deep" : "bg-ink/5 text-muted"}`}>{k}{byKw.get(k)?.volume ? ` · ${num(byKw.get(k)!.volume)}` : ""}</span>)}
                </div>
                {manage && o.data?.can_write_articles && (
                  <div className="mt-3 flex justify-end">
                    <Button variant="secondary" disabled={write.isPending} onClick={() => write.mutate({ list_id: id, cluster_id: c.id })}><FileText size={15} /> Write article</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="-mx-5 overflow-x-auto">
          <table className="w-full min-w-[620px] text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2 font-medium">Keyword</th>
                <th className="px-3 py-2 text-right font-medium">{hasVolume ? "Volume / mo" : "Demand (est.)"}</th>
                <th className="px-3 py-2 font-medium">Difficulty</th>
                <th className="px-3 py-2 font-medium">Intent</th>
                <th className="px-3 py-2 font-medium">Source</th>
                <th className="px-3 py-2" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.keyword} className="border-b border-line/70">
                  <td className="max-w-[320px] px-3 py-2 font-medium [overflow-wrap:anywhere]">{i.keyword}{i.estimated && <Badge>est.</Badge>}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{hasVolume ? num(i.volume) : <Demand v={i.demand} />}</td>
                  <td className="px-3 py-2"><Kd v={i.kd} /></td>
                  <td className="px-3 py-2"><IntentBadge intent={i.intent} guessed={i.intent_guessed} /></td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{sourceLabel(i.source)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    {manage && o.data?.can_write_articles && <button type="button" className="mr-3 text-xs text-primary-700 hover:underline" onClick={() => write.mutate({ keyword: i.keyword, intent: i.intent })}>Write article</button>}
                    {manage && <button type="button" className="text-muted hover:text-danger" aria-label={`Remove ${i.keyword}`} onClick={() => remove.mutate(i.keyword)}><Trash2 size={14} /></button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function ListsTab() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["keywords", "lists"], queryFn: () => api<KwList[]>("/keywords/lists") });
  const [sel, setSel] = useState<string | null>(null);
  const [name, setName] = useState("");
  useEffect(() => { if (!sel && lists.data?.length) setSel(lists.data[0].id); }, [lists.data, sel]);
  const create = useMutation({ mutationFn: () => api<KwList>("/keywords/lists", { method: "POST", json: { name } }), onSuccess: (l) => { setName(""); setSel(l.id); qc.invalidateQueries({ queryKey: ["keywords"] }); } });
  const del = useMutation({ mutationFn: (id: string) => api(`/keywords/lists/${id}`, { method: "DELETE" }), onSuccess: () => { setSel(null); qc.invalidateQueries({ queryKey: ["keywords"] }); } });
  if (lists.isLoading) return <Spinner label="Loading lists…" />;
  return (
    <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
      <div className="space-y-3">
        <ul className="space-y-1">
          {(lists.data ?? []).map((l) => (
            <li key={l.id} className={`group flex items-center gap-1 rounded-lg ${sel === l.id ? "bg-brand-tint" : "hover:bg-ink/5"}`}>
              <button type="button" onClick={() => setSel(l.id)} className="min-w-0 flex-1 px-3 py-2 text-left">
                <span className="block truncate text-sm font-medium">{l.name}</span>
                <span className="block text-xs text-muted">{l.count} keywords{l.clusters_count ? ` · ${l.clusters_count} clusters` : ""}</span>
              </button>
              {can("keywords.manage") && <button type="button" aria-label={`Delete ${l.name}`} className="px-2 text-muted opacity-0 hover:text-danger group-hover:opacity-100 focus:opacity-100" onClick={() => { if (confirm(`Delete the list “${l.name}”?`)) del.mutate(l.id); }}><Trash2 size={14} /></button>}
            </li>
          ))}
        </ul>
        {can("keywords.manage") && (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
            <input className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-sm" placeholder="New list name" value={name} onChange={(e) => setName(e.target.value)} />
            <Button type="submit" variant="secondary" disabled={!name.trim()}>Add</Button>
          </form>
        )}
      </div>
      {sel ? <ListDetail key={sel} id={sel} /> : (
        <EmptyState title="No keyword lists yet" body="Select keywords in Research or Gap and save them to a list. Then group them into topic clusters and write an article for each." />
      )}
    </div>
  );
}
