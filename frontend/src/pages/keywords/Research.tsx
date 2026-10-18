import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, ListPlus, LineChart, Search } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, EmptyState, Field, Input, Modal, Select, Spinner, formatDate } from "../../components/ui";
import {
  CostNote, DataNotice, Demand, IntentBadge, Kd, Sparkline, SortTh, downloadCsv, kwCsv, num, sortRows, sourceLabel, usd, useOverview,
  type SortDir,
} from "./shared";
import type { KwList, KwRow, Report } from "./types";

const MODES = [
  { id: "suggestions", label: "Keywords that contain my words" },
  { id: "related", label: "Related searches" },
  { id: "ideas", label: "Broader ideas on the topic" },
  { id: "domain", label: "What a website ranks for" },
];

/** Save selected keywords to a new or existing keyword list. */
export function SaveToList({ items, onClose, onSaved }: { items: KwRow[]; onClose: () => void; onSaved?: (l: KwList) => void }) {
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["keywords", "lists"], queryFn: () => api<KwList[]>("/keywords/lists") });
  const [target, setTarget] = useState<string>("new");
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () => target === "new"
      ? api<KwList>("/keywords/lists", { method: "POST", json: { name: name.trim() || "My keywords", items } })
      : api<KwList>(`/keywords/lists/${target}/items`, { method: "POST", json: { items } }),
    onSuccess: (l) => { qc.invalidateQueries({ queryKey: ["keywords"] }); onSaved?.(l); onClose(); },
  });
  return (
    <Modal title={`Save ${items.length} keyword${items.length === 1 ? "" : "s"} to a list`} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Field label="List">
          <Select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">New list…</option>
            {(lists.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name} ({l.count})</option>)}
          </Select>
        </Field>
        {target === "new" && <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Payroll articles" autoFocus /></Field>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

export function useWriteArticle() {
  const nav = useNavigate();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<{ article: { id: string } }>("/keywords/write-article", { method: "POST", json: body }),
    onSuccess: (r) => nav(`/seo/${r.article.id}`),
  });
}

export function useTrack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rows: KwRow[]) => api<{ message: string }>("/keywords/ranks", { method: "POST", json: { keywords: rows.map((r) => ({ keyword: r.keyword, volume: r.volume })) } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keywords"] }),
  });
}

export function KeywordTable({ rows, selected, setSelected, showPosition }: {
  rows: KwRow[]; selected: Set<string>; setSelected: (s: Set<string>) => void; showPosition?: boolean;
}) {
  const [sort, setSort] = useState<[string, SortDir]>(["volume", "desc"]);
  const hasVolume = rows.some((r) => r.volume !== null);
  const hasGsc = rows.some((r) => r.gsc || r.impressions);
  const hasKd = rows.some((r) => r.kd !== null), hasCpc = rows.some((r) => r.cpc !== null), hasTrend = rows.some((r) => r.trend.length > 1);
  const sorted = useMemo(() => sortRows(rows, sort[0] === "volume" && !hasVolume ? ["demand", sort[1]] : sort, (r, k) => {
    if (k === "gsc") return r.gsc?.impressions ?? r.impressions ?? null;
    if (k === "position") return r.position ?? r.gsc?.position ?? null;
    return (r as unknown as Record<string, number | string | null>)[k];
  }), [rows, sort, hasVolume]);
  const all = rows.length > 0 && rows.every((r) => selected.has(r.keyword));
  const toggle = (k: string) => { const s = new Set(selected); if (s.has(k)) s.delete(k); else s.add(k); setSelected(s); };
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="w-10 px-3 py-2"><input type="checkbox" aria-label="Select all" checked={all} onChange={() => setSelected(all ? new Set() : new Set(rows.map((r) => r.keyword)))} className="h-4 w-4 accent-[rgb(var(--brand))]" /></th>
            <SortTh label="Keyword" k="keyword" sort={sort} setSort={setSort} />
            {hasVolume
              ? <SortTh label="Volume / mo" k="volume" sort={sort} setSort={setSort} className="text-right" title="Average monthly Google searches" />
              : <SortTh label="Demand (est.)" k="demand" sort={sort} setSort={setSort} title="Estimated relative demand 0–100, from Google Autocomplete and Search Console. Not a search volume." />}
            {hasKd && <SortTh label="Difficulty" k="kd" sort={sort} setSort={setSort} title="Keyword difficulty 0–100" />}
            {hasCpc && <SortTh label="CPC" k="cpc" sort={sort} setSort={setSort} className="text-right" title="Cost per click in Google Ads, USD" />}
            <SortTh label="Intent" k="intent" sort={sort} setSort={setSort} />
            {hasTrend && <th className="px-3 py-2 font-medium">Trend</th>}
            {(hasGsc || showPosition) && <SortTh label={showPosition ? "Position" : "Search Console"} k={showPosition ? "position" : "gsc"} sort={sort} setSort={setSort} title="Your impressions and average position in Search Console" />}
            <th className="px-3 py-2 font-medium">Source</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.keyword} className={`border-b border-line/70 ${selected.has(r.keyword) ? "bg-brand-tint/40" : ""}`}>
              <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${r.keyword}`} checked={selected.has(r.keyword)} onChange={() => toggle(r.keyword)} className="h-4 w-4 accent-[rgb(var(--brand))]" /></td>
              <td className="max-w-[320px] px-3 py-2 [overflow-wrap:anywhere]">
                <span className="font-medium text-ink">{r.keyword}</span>
                {r.seed && <span className="ml-1.5 text-xs text-muted">(your seed)</span>}
                {r.serp_features.length > 0 && <span className="block text-xs text-muted">{r.serp_features.slice(0, 3).map((f) => f.replace(/_/g, " ")).join(" · ")}</span>}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{hasVolume ? num(r.volume) : <Demand v={r.demand} />}</td>
              {hasKd && <td className="px-3 py-2"><Kd v={r.kd} /></td>}
              {hasCpc && <td className="px-3 py-2 text-right tabular-nums">{r.cpc !== null ? usd(r.cpc) : "—"}</td>}
              <td className="px-3 py-2"><IntentBadge intent={r.intent} guessed={r.intent_guessed} /></td>
              {hasTrend && <td className="px-3 py-2"><Sparkline values={r.trend} label={`Monthly searches: ${r.trend.join(", ")}`} /></td>}
              {(hasGsc || showPosition) && (
                <td className="whitespace-nowrap px-3 py-2 text-xs tabular-nums text-muted">
                  {showPosition && r.position ? <span className="text-sm text-ink">#{r.position}</span>
                    : r.gsc || r.impressions ? <>{num(r.gsc?.impressions ?? r.impressions ?? null)} impr. · #{Math.round(r.gsc?.position ?? r.position ?? 0)}</> : "—"}
                </td>
              )}
              <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{sourceLabel(r.source)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SelectionBar({ rows, clear }: { rows: KwRow[]; clear: () => void }) {
  const { can } = useAuth();
  const o = useOverview();
  const [saving, setSaving] = useState(false);
  const track = useTrack();
  const write = useWriteArticle();
  const [note, setNote] = useState<string | null>(null);
  if (!rows.length || !can("keywords.manage")) return null;
  const main = [...rows].sort((a, b) => (b.volume ?? b.demand ?? 0) - (a.volume ?? a.demand ?? 0))[0];
  return (
    <div className="sticky bottom-3 z-10 mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface p-3 shadow-[0_12px_40px_rgba(6,47,89,0.14)]">
      <span className="mr-auto text-sm"><strong>{rows.length}</strong> selected</span>
      {note && <span className="text-sm text-muted">{note}</span>}
      {(track.error || write.error) && <span className="text-sm text-danger">{((track.error || write.error) as Error).message}</span>}
      <Button variant="secondary" onClick={() => setSaving(true)}><ListPlus size={15} /> Save to list</Button>
      <Button variant="secondary" disabled={track.isPending} onClick={() => track.mutate(rows, { onSuccess: (r) => setNote(r.message) })}><LineChart size={15} /> Track rankings</Button>
      {o.data?.can_write_articles && (
        <Button disabled={write.isPending} title={`Main keyword: ${main.keyword}`}
          onClick={() => write.mutate({ keyword: main.keyword, intent: main.intent, secondary_keywords: rows.filter((r) => r !== main).map((r) => r.keyword).slice(0, 15) })}>
          <FileText size={15} /> Write article
        </Button>
      )}
      <Button variant="ghost" onClick={clear}>Clear</Button>
      {saving && <SaveToList items={rows} onClose={() => setSaving(false)} onSaved={(l) => { setNote(`Saved to “${l.name}”.`); clear(); }} />}
    </div>
  );
}

export function ResearchTab() {
  const { can } = useAuth();
  const o = useOverview();
  const qc = useQueryClient();
  const s = o.data?.settings;
  const [seeds, setSeeds] = useState("");
  const [mode, setMode] = useState("suggestions");
  const [loc, setLoc] = useState<number | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState({ q: "", intent: "", minVol: "", maxKd: "" });
  const reports = useQuery({ queryKey: ["keywords", "reports", "research"], queryFn: () => api<Report[]>("/keywords/reports?kind=research") });
  useEffect(() => { if (!reportId && reports.data?.length) setReportId(reports.data[0].id); }, [reports.data, reportId]);
  const report = useQuery({
    queryKey: ["keywords", "report", reportId], queryFn: () => api<Report>(`/keywords/reports/${reportId}`), enabled: !!reportId,
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2500 : false),
  });
  const job = useJob(jobId, () => { qc.invalidateQueries({ queryKey: ["keywords"] }); });
  const seedList = seeds.split(/[\n,]/).map((x) => x.trim()).filter(Boolean).slice(0, 5);
  const body = { seeds: seedList, mode, location_code: loc ?? s?.location_code };
  const start = useMutation({
    mutationFn: () => api<{ report: Report; job_id: string }>("/keywords/research", { method: "POST", json: body }),
    onSuccess: (r) => { setReportId(r.report.id); setJobId(r.job_id); setSelected(new Set()); qc.invalidateQueries({ queryKey: ["keywords", "reports"] }); },
  });
  const rows = report.data?.rows ?? [];
  const shown = rows.filter((r) => (!filter.q || r.keyword.toLowerCase().includes(filter.q.toLowerCase()))
    && (!filter.intent || r.intent === filter.intent)
    && (!filter.minVol || (r.volume ?? r.demand ?? 0) >= Number(filter.minVol))
    && (!filter.maxKd || r.kd === null || r.kd <= Number(filter.maxKd)));
  const running = report.data && ["queued", "running"].includes(report.data.status);
  const hasVolume = rows.some((r) => r.volume !== null);

  return (
    <div className="space-y-5">
      <DataNotice o={o.data} />
      {can("keywords.manage") && (
        <Card>
          <form className="grid gap-4 lg:grid-cols-[1fr_250px_170px_auto] lg:items-start" onSubmit={(e) => { e.preventDefault(); if (seedList.length) start.mutate(); }}>
            <Field label="Seed keywords or a website" hint="Up to 5, separated by commas. Type a domain (like example.com) to see what it ranks for.">
              <Input value={seeds} onChange={(e) => setSeeds(e.target.value)} placeholder="hrms software, payroll software" list="kw-recent" />
            </Field>
            <datalist id="kw-recent">{(o.data?.recent_seeds ?? []).map((x) => <option key={x} value={x} />)}</datalist>
            <Field label="Find">
              <Select value={mode} onChange={(e) => setMode(e.target.value)}>{MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</Select>
            </Field>
            <Field label="Country">
              <Select value={loc ?? s?.location_code ?? 2356} onChange={(e) => setLoc(Number(e.target.value))}>
                {(o.data?.locations ?? []).map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
              </Select>
            </Field>
            <Button type="submit" className="lg:mt-7 lg:h-10" disabled={!seedList.length || start.isPending}><Search size={15} /> Find keywords</Button>
          </form>
          <div className="mt-2 min-h-[1rem]"><CostNote body={{ action: "research", research: body }} enabled={seedList.length > 0} /></div>
          {start.error && <div className="mt-2"><Alert>{(start.error as Error).message}</Alert></div>}
        </Card>
      )}

      {(reports.data?.length ?? 0) > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Earlier searches:</span>
          <Select aria-label="Earlier searches" className="w-auto min-w-[16rem] max-w-full" value={reportId ?? ""} onChange={(e) => { setReportId(e.target.value); setSelected(new Set()); }}>
            {(reports.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.title} · {formatDate(r.created_at)}</option>)}
          </Select>
        </div>
      )}

      {!reportId && !reports.isLoading && <EmptyState title="No keyword research yet" body="Type a topic your customers search for, like “payroll software”, and see related keywords, questions and how much demand each has." />}
      {running && <Card><Spinner label={job?.progress ?? "Finding keywords…"} /></Card>}
      {report.data?.status === "failed" && <Alert>{report.data.error}</Alert>}
      {report.data?.status === "done" && (
        <Card>
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="font-semibold [overflow-wrap:anywhere]">{report.data.title}</h2>
              <p className="text-xs text-muted">
                {rows.length} keywords · {report.data.meta.estimated ? "estimated (no volume data)" : `data from ${report.data.meta.provider}`}
                {report.data.meta.cost_usd ? ` · cost ${usd(report.data.meta.cost_usd)}` : " · free"}
                {report.data.meta.cached ? " · from cache" : ""} · {formatDate(report.data.created_at)}
              </p>
            </div>
            <Button variant="secondary" onClick={() => downloadCsv(`keywords-${report.data!.created_at.slice(0, 10)}.csv`, kwCsv(shown))}><Download size={15} /> CSV</Button>
          </div>
          {report.data.meta.estimated && report.data.meta.note && <p className="mb-3 text-xs text-muted">{report.data.meta.note}</p>}
          <div className="mb-3 grid gap-2 sm:grid-cols-4">
            <Input aria-label="Filter keywords" placeholder="Filter…" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
            <Select aria-label="Intent" value={filter.intent} onChange={(e) => setFilter({ ...filter, intent: e.target.value })}>
              <option value="">Any intent</option><option value="informational">Learn (informational)</option><option value="commercial">Compare (commercial)</option>
              <option value="transactional">Buy (transactional)</option><option value="navigational">Brand (navigational)</option>
            </Select>
            <Input aria-label={hasVolume ? "Minimum volume" : "Minimum demand"} inputMode="numeric" placeholder={hasVolume ? "Min. volume" : "Min. demand (0–100)"} value={filter.minVol} onChange={(e) => setFilter({ ...filter, minVol: e.target.value })} />
            <Input aria-label="Maximum difficulty" inputMode="numeric" placeholder="Max. difficulty" disabled={!rows.some((r) => r.kd !== null)} value={filter.maxKd} onChange={(e) => setFilter({ ...filter, maxKd: e.target.value })} />
          </div>
          {shown.length ? <KeywordTable rows={shown} selected={selected} setSelected={setSelected} showPosition={report.data.params.mode === "domain"} />
            : <p className="py-6 text-center text-sm text-muted">No keywords match these filters.</p>}
          <p className="mt-2 text-xs text-muted">* intent estimated from the wording or by AI.</p>
          <SelectionBar rows={rows.filter((r) => selected.has(r.keyword))} clear={() => setSelected(new Set())} />
        </Card>
      )}
    </div>
  );
}
