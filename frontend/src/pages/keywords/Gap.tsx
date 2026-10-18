import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, GitCompare, ListPlus, Plus, Sparkles, X } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Select, Spinner, Tabs, formatDate } from "../../components/ui";
import { SaveToList, useWriteArticle } from "./Research";
import { CostNote, DataNotice, IntentBadge, Kd, SortTh, downloadCsv, num, sortRows, usd, useOverview, type SortDir } from "./shared";
import type { GapRow, Report } from "./types";

type Cat = "missing" | "weak" | "strong" | "shared" | "all";
const CATS: { id: Cat; label: string; help: string }[] = [
  { id: "missing", label: "Missing", help: "Competitors rank for these, you don't. Your biggest content opportunities." },
  { id: "weak", label: "Weak", help: "You rank, but below at least one competitor (or you have a page that doesn't show up yet)." },
  { id: "strong", label: "Strong", help: "You rank better than every competitor here. Keep these pages fresh." },
  { id: "shared", label: "Shared", help: "Both you and competitors rank for these." },
  { id: "all", label: "All", help: "Everything found." },
];

function GapTable({ rows, comps, selected, setSelected, free }: { rows: GapRow[]; comps: string[]; selected: Set<string>; setSelected: (s: Set<string>) => void; free: boolean }) {
  const [sort, setSort] = useState<[string, SortDir]>([free ? "demand" : "volume", "desc"]);
  const sorted = useMemo(() => sortRows(rows, sort, (r, k) => k.startsWith("c:") ? r.competitors[k.slice(2)]?.position ?? null
    : (r as unknown as Record<string, number | string | null>)[k]), [rows, sort]);
  const toggle = (k: string) => { const s = new Set(selected); if (s.has(k)) s.delete(k); else s.add(k); setSelected(s); };
  const all = rows.length > 0 && rows.every((r) => selected.has(r.keyword));
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="w-10 px-3 py-2"><input type="checkbox" aria-label="Select all" checked={all} onChange={() => setSelected(all ? new Set() : new Set(rows.map((r) => r.keyword)))} className="h-4 w-4 accent-[rgb(var(--brand))]" /></th>
            <SortTh label={free ? "Topic" : "Keyword"} k="keyword" sort={sort} setSort={setSort} />
            {free ? <SortTh label="Competitors covering" k="demand" sort={sort} setSort={setSort} title="How many of the compared competitors have a page on this (a rough sign of demand)" />
              : <SortTh label="Volume / mo" k="volume" sort={sort} setSort={setSort} className="text-right" />}
            {!free && <SortTh label="Difficulty" k="kd" sort={sort} setSort={setSort} />}
            <SortTh label="Intent" k="intent" sort={sort} setSort={setSort} />
            <SortTh label="You" k="ours" sort={sort} setSort={setSort} title={free ? "Your average position in Search Console for a matching query" : "Your Google position"} />
            {comps.map((c) => <SortTh key={c} label={c} k={`c:${c}`} sort={sort} setSort={setSort} className="max-w-[9rem] truncate" title={c} />)}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.keyword} className={`border-b border-line/70 ${selected.has(r.keyword) ? "bg-brand-tint/40" : ""}`}>
              <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${r.keyword}`} checked={selected.has(r.keyword)} onChange={() => toggle(r.keyword)} className="h-4 w-4 accent-[rgb(var(--brand))]" /></td>
              <td className="max-w-[320px] px-3 py-2 [overflow-wrap:anywhere]">
                <span className="font-medium text-ink">{r.keyword}</span>
                {r.our_query && r.our_query.toLowerCase() !== r.keyword && <span className="block text-xs text-muted">Your query: “{r.our_query}”</span>}
                {r.note && <span className="block text-xs text-muted">{r.note}</span>}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{free ? r.demand : num(r.volume)}</td>
              {!free && <td className="px-3 py-2"><Kd v={r.kd} /></td>}
              <td className="px-3 py-2"><IntentBadge intent={r.intent} guessed={free} /></td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                {r.ours ? (r.our_url ? <a href={r.our_url} target="_blank" rel="noreferrer" className="text-primary-700 hover:underline">#{r.ours}</a> : `#${r.ours}`)
                  : r.our_url ? <a href={r.our_url} target="_blank" rel="noreferrer" className="text-xs text-primary-700 hover:underline">page, no ranking</a> : <span className="text-muted">—</span>}
              </td>
              {comps.map((c) => {
                const x = r.competitors[c];
                return (
                  <td key={c} className="whitespace-nowrap px-3 py-2 tabular-nums">
                    {!x ? <span className="text-muted">—</span>
                      : x.url ? <a href={x.url} target="_blank" rel="noreferrer" className="text-primary-700 hover:underline">{x.position ? `#${x.position}` : "has a page"}</a>
                        : x.position ? `#${x.position}` : "✓"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function GapTab() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const o = useOverview();
  const [ours, setOurs] = useState("");
  const [comps, setComps] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [reportId, setReportId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [cat, setCat] = useState<Cat>("missing");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const write = useWriteArticle();
  useEffect(() => { if (o.data && !ours) setOurs(o.data.our_domain); }, [o.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const known = useQuery({ queryKey: ["keywords", "gap-competitors"], queryFn: () => api<{ competitors: { domain: string; name: string; status: string }[] }>("/keywords/gap/competitors") });
  const suggest = useMutation({ mutationFn: () => api<{ domains: { domain: string }[]; note?: string; cost_usd: number }>("/keywords/gap/suggest", { method: "POST" }) });
  const reports = useQuery({ queryKey: ["keywords", "reports", "gap"], queryFn: () => api<Report<GapRow>[]>("/keywords/reports?kind=gap") });
  useEffect(() => { if (!reportId && reports.data?.length) setReportId(reports.data[0].id); }, [reports.data, reportId]);
  const report = useQuery({
    queryKey: ["keywords", "report", reportId], queryFn: () => api<Report<GapRow>>(`/keywords/reports/${reportId}`), enabled: !!reportId,
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2500 : false),
  });
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: ["keywords"] }));
  const add = (d: string) => {
    const v = d.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
    if (v && !comps.includes(v) && comps.length < 4 && v !== ours) setComps([...comps, v]);
    setTyped("");
  };
  const body = { our_domain: ours || undefined, competitors: comps };
  const start = useMutation({
    mutationFn: () => api<{ report: Report<GapRow>; job_id: string }>("/keywords/gap", { method: "POST", json: body }),
    onSuccess: (r) => { setReportId(r.report.id); setJobId(r.job_id); setSelected(new Set()); qc.invalidateQueries({ queryKey: ["keywords", "reports"] }); },
  });
  const rep = report.data;
  const rows = rep?.rows ?? [];
  const free = !!rep?.meta.approximate;
  const shown = rows.filter((r) => cat === "all" || (cat === "shared" ? r.shared : r.category === cat));
  const counts = rep?.meta.counts ?? {};
  const pool = [...new Set([...(known.data?.competitors ?? []).map((c) => c.domain), ...(suggest.data?.domains ?? []).map((d) => d.domain)])].filter((d) => !comps.includes(d) && d !== ours);
  const sel = rows.filter((r) => selected.has(r.keyword));
  const repComps = (rep?.params.competitors as string[] | undefined) ?? [];

  const exportCsv = () => downloadCsv(`keyword-gap-${cat}-${rep!.created_at.slice(0, 10)}.csv`, [
    ["keyword", "category", free ? "competitors_covering" : "volume", "kd", "intent", "our_position", "our_url", ...repComps.map((c) => `${c}_position`), ...repComps.map((c) => `${c}_url`)],
    ...shown.map((r) => [r.keyword, r.category, free ? r.demand ?? "" : r.volume, r.kd, r.intent, r.ours, r.our_url,
      ...repComps.map((c) => r.competitors[c]?.position ?? (r.competitors[c] ? "page" : "")), ...repComps.map((c) => r.competitors[c]?.url ?? "")]),
  ]);

  return (
    <div className="space-y-5">
      <DataNotice o={o.data}>{!o.data?.provider.configured && "The free gap compares the topics on competitors' pages with your Search Console queries, so it is approximate."}</DataNotice>
      {can("keywords.manage") && (
        <Card>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,240px)_1fr]">
            <Field label="Your website"><Input value={ours} onChange={(e) => setOurs(e.target.value)} placeholder="yourcompany.com" /></Field>
            <Field label={`Competitors (${comps.length}/4)`}>
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface p-1.5">
                {comps.map((c) => (
                  <span key={c} className="inline-flex items-center gap-1 rounded-md bg-brand-tint px-2 py-1 text-sm text-brand-deep">
                    {c}<button type="button" aria-label={`Remove ${c}`} onClick={() => setComps(comps.filter((x) => x !== c))}><X size={13} /></button>
                  </span>
                ))}
                {comps.length < 4 && (
                  <input className="h-8 min-w-[10rem] flex-1 bg-transparent px-1 text-[15px] focus:outline-none" placeholder="competitor.com, then Enter" value={typed}
                    onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(typed); } }} onBlur={() => typed && add(typed)} />
                )}
              </div>
            </Field>
          </div>
          {pool.length > 0 && comps.length < 4 && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5 text-sm">
              <span className="text-muted">Add:</span>
              {pool.slice(0, 10).map((d) => <button key={d} type="button" onClick={() => add(d)} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-0.5 hover:border-brand/50"><Plus size={12} />{d}</button>)}
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button disabled={!comps.length || start.isPending} onClick={() => start.mutate()}><GitCompare size={15} /> Compare keywords</Button>
            <Button variant="secondary" disabled={suggest.isPending} onClick={() => suggest.mutate()}><Sparkles size={15} /> Suggest competitors</Button>
            <CostNote body={{ action: "gap", gap: body }} enabled={comps.length > 0} />
          </div>
          {suggest.data?.note && <p className="mt-2 text-xs text-muted">{suggest.data.note}</p>}
          {(start.error || suggest.error) && <div className="mt-3"><Alert>{((start.error || suggest.error) as Error).message}</Alert></div>}
        </Card>
      )}
      {(reports.data?.length ?? 0) > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Earlier comparisons:</span>
          <Select aria-label="Earlier comparisons" className="w-auto min-w-[16rem] max-w-full" value={reportId ?? ""} onChange={(e) => { setReportId(e.target.value); setSelected(new Set()); }}>
            {(reports.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.title} · {formatDate(r.created_at)}</option>)}
          </Select>
        </div>
      )}
      {!reportId && !reports.isLoading && <EmptyState title="Compare your keywords with competitors" body="Add up to four competitor websites to see the keywords and topics they cover that you don't." />}
      {rep && ["queued", "running"].includes(rep.status) && <Card><Spinner label={job?.progress ?? "Comparing…"} /></Card>}
      {rep?.status === "failed" && <Alert>{rep.error}</Alert>}
      {rep?.status === "done" && (
        <Card>
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="font-semibold [overflow-wrap:anywhere]">{rep.title}</h2>
              <p className="text-xs text-muted">
                {free ? <Badge tone="accent">Approximate</Badge> : `Data from ${rep.meta.provider}`} · {rows.length} {free ? "topics" : "keywords"}
                {rep.meta.cost_usd ? ` · cost ${usd(rep.meta.cost_usd)}` : " · free"} · {formatDate(rep.created_at)}
              </p>
            </div>
            <Button variant="secondary" onClick={exportCsv}><Download size={15} /> CSV</Button>
          </div>
          {free && rep.meta.note && <p className="mb-2 text-xs text-muted">{rep.meta.note}</p>}
          {(rep.meta.notes ?? []).map((n) => <p key={n} className="mb-2 text-xs text-amber-800">{n}</p>)}
          <Tabs value={cat} onChange={(c) => { setCat(c); setSelected(new Set()); }} items={CATS.map((c) => ({ id: c.id, label: c.label, count: c.id === "all" ? rows.length : counts[c.id] ?? 0 }))} />
          <p className="-mt-3 mb-3 text-sm text-muted">{CATS.find((c) => c.id === cat)?.help}</p>
          {shown.length ? <GapTable rows={shown} comps={repComps} selected={selected} setSelected={setSelected} free={free} />
            : <p className="py-6 text-center text-sm text-muted">Nothing in this group.</p>}
          {sel.length > 0 && can("keywords.manage") && (
            <div className="sticky bottom-3 z-10 mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-3 shadow-[0_12px_40px_rgba(6,47,89,0.14)]">
              <span className="mr-auto text-sm"><strong>{sel.length}</strong> selected</span>
              {write.error && <span className="text-sm text-danger">{(write.error as Error).message}</span>}
              <Button variant="secondary" onClick={() => setSaving(true)}><ListPlus size={15} /> Save to list</Button>
              {o.data?.can_write_articles && (
                <Button disabled={write.isPending} onClick={() => write.mutate({ keyword: sel[0].keyword, intent: sel[0].intent, secondary_keywords: sel.slice(1, 16).map((r) => r.keyword) })}>
                  <FileText size={15} /> Write article
                </Button>
              )}
              <Button variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
            </div>
          )}
          {saving && <SaveToList items={sel} onClose={() => setSaving(false)} onSaved={() => setSelected(new Set())} />}
        </Card>
      )}
    </div>
  );
}
