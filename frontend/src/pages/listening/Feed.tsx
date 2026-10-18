import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowUpRight, Check, Copy, EyeOff, Eye, Lightbulb, MessageCircleReply, MessageSquare, RotateCcw, Search, ThumbsUp, Users,
} from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, EmptyState, Input, Modal, Select, Spinner } from "../../components/ui";
import { ago } from "../inbox/shared";
import { KIND_LABEL, REASON_LABEL, type Mention, type MentionPage, type Query, type Sentiment, type SourceInfo } from "./types";

export type Filters = {
  query_id: string; source: string; sentiment: string; days: string; status: string; needs_response: boolean; q: string;
  sort: "newest" | "engagement";
};
export const EMPTY_FILTERS: Filters = { query_id: "", source: "", sentiment: "", days: "30", status: "open", needs_response: false, q: "", sort: "newest" };
const PAGE = 30;

const SENT: Record<Sentiment, { label: string; cls: string }> = {
  positive: { label: "Positive", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/15" },
  neutral: { label: "Neutral", cls: "bg-slate-100 text-slate-600 ring-slate-500/10" },
  negative: { label: "Negative", cls: "bg-danger-tint text-danger ring-danger/15" },
};

function Chip({ cls, children }: { cls: string; children: React.ReactNode }) {
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}>{children}</span>;
}

function params(f: Filters, offset: number) {
  const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset), sort: f.sort });
  if (f.query_id) p.set("query_id", f.query_id);
  if (f.source) p.set("source", f.source);
  if (f.sentiment) p.set("sentiment", f.sentiment);
  if (f.days) p.set("days", f.days);
  if (f.status === "filtered") { p.set("filtered", "true"); p.set("status", "all"); } else p.set("status", f.status);
  if (f.needs_response) p.set("needs_response", "true");
  if (f.q.trim()) p.set("q", f.q.trim());
  return p.toString();
}

function DraftModal({ m, onClose }: { m: Mention; onClose: () => void }) {
  const [copied, setCopied] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["listening-draft", m.id],
    queryFn: () => api<{ drafts: string[]; limit: number; url: string | null; where: string }>(`/listening/mentions/${m.id}/draft`, { method: "POST" }),
    retry: false, staleTime: Infinity,
  });
  const copy = (t: string, i: number) => { navigator.clipboard?.writeText(t); setCopied(i); setTimeout(() => setCopied(null), 1800); };
  return (
    <Modal title="Draft a reply" onClose={onClose} wide>
      <p className="mb-3 text-sm text-muted">
        Written in your brand voice. Copy one, change it if you like, and post it yourself on {m.source_label}.
        Read it first: replies go out under your name.
      </p>
      <blockquote className="mb-4 rounded-lg border-l-4 border-line bg-ink/[0.03] px-3 py-2 text-sm [overflow-wrap:anywhere]">
        <span className="font-medium">{m.title}</span>
        {m.snippet && <span className="mt-1 block line-clamp-3 text-muted">{m.snippet}</span>}
      </blockquote>
      {q.isLoading && <Spinner label="Writing drafts…" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      <ul className="space-y-3">
        {q.data?.drafts.map((d, i) => (
          <li key={i} className="rounded-lg border border-line p-3">
            <p className="whitespace-pre-line text-[15px] [overflow-wrap:anywhere]">{d}</p>
            <div className="mt-2 flex items-center justify-between text-xs text-muted">
              <span>{d.length} / {q.data.limit} characters</span>
              <Button variant="secondary" className="h-8 px-3" onClick={() => copy(d, i)}>
                {copied === i ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" onClick={onClose}>Close</Button>
        {m.url && <a href={m.url} target="_blank" rel="noreferrer"><Button>Open on {m.source_label} <ArrowUpRight size={15} /></Button></a>}
      </div>
    </Modal>
  );
}

function MentionCard({ m, canManage, canIdea, onDraft }: { m: Mention; canManage: boolean; canIdea: boolean; onDraft: () => void }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Mention>(`/listening/mentions/${m.id}`, { method: "PATCH", json: body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["listening-mentions"] }); qc.invalidateQueries({ queryKey: ["listening-status"] }); },
    onError: (e) => setErr((e as Error).message),
  });
  const idea = useMutation({
    mutationFn: () => api<{ idea_id: string; title: string; link: string }>(`/listening/mentions/${m.id}/idea`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["listening-mentions"] }),
    onError: (e) => setErr((e as Error).message),
  });
  const when = m.published_at ?? m.found_at;
  const s = m.sentiment ? SENT[m.sentiment] : null;
  const faded = m.status !== "new";
  return (
    <li className={`rounded-xl border bg-surface p-4 ${m.needs_response && m.status === "new" ? "border-amber-300" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <Badge tone="brand">{m.source_label}</Badge>
        {(m.publisher || m.domain) && <span className="[overflow-wrap:anywhere]">{m.publisher ?? m.domain}</span>}
        {m.author.name && m.author.name !== m.publisher && (
          <span>· {m.author.url ? <a className="hover:underline" href={m.author.url} target="_blank" rel="noreferrer">{m.author.name}</a> : m.author.name}
            {m.author.handle && m.author.handle !== m.author.name && <span className="text-muted/80"> {m.author.handle}</span>}</span>
        )}
        <span title={when}>· {ago(when)}</span>
        {m.status === "reviewed" && <span className="inline-flex items-center gap-1 text-emerald-700"><Check size={12} /> Reviewed</span>}
        {m.status === "hidden" && <span className="inline-flex items-center gap-1"><EyeOff size={12} /> Hidden</span>}
      </div>
      <h3 className={`mt-1.5 font-semibold leading-snug [overflow-wrap:anywhere] ${faded ? "text-ink/80" : "text-ink"}`}>
        {m.url ? <a href={m.url} target="_blank" rel="noreferrer" className="hover:underline">{m.title || (m.snippet ?? "").slice(0, 140)}</a> : m.title}
      </h3>
      {m.snippet && m.title && <p className="mt-1 line-clamp-3 text-sm text-muted [overflow-wrap:anywhere]">{m.snippet}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {s && <Chip cls={s.cls}>{s.label}</Chip>}
        {m.needs_response && m.response_reason && <Chip cls="bg-amber-50 text-amber-800 ring-amber-600/20"><MessageCircleReply size={12} />{REASON_LABEL[m.response_reason]}</Chip>}
        {m.needs_response && !m.response_reason && <Chip cls="bg-amber-50 text-amber-800 ring-amber-600/20">Needs a reply</Chip>}
        {m.is_competitor && <Chip cls="bg-violet-50 text-violet-700 ring-violet-600/15">Competitor{m.competitor?.name ? `: ${m.competitor.name}` : ""}</Chip>}
        {m.queries.map((q) => <Chip key={q.id} cls="bg-ink/5 text-muted ring-transparent">{q.name}</Chip>)}
        {m.tags.map((t) => <span key={t} className="text-[11px] text-muted">#{t.replace(/\s+/g, "")}</span>)}
        {!m.relevant && <Chip cls="bg-ink/5 text-muted ring-transparent">Filtered out as not about you</Chip>}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3 text-xs tabular-nums text-muted">
          {m.score != null && <span className="inline-flex items-center gap-1" title="Upvotes, points or likes"><ThumbsUp size={13} />{m.score}</span>}
          {m.comments != null && <span className="inline-flex items-center gap-1" title="Comments"><MessageSquare size={13} />{m.comments}</span>}
          {(m.reach ?? m.author.followers) != null && <span className="inline-flex items-center gap-1" title={m.reach != null ? "Views" : "Author's followers"}><Users size={13} />{(m.reach ?? m.author.followers ?? 0).toLocaleString("en-IN")}</span>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {m.url && <a href={m.url} target="_blank" rel="noreferrer"><Button variant="ghost" className="h-8 px-2.5">Open <ArrowUpRight size={14} /></Button></a>}
          <Button variant="secondary" className="h-8 px-2.5" onClick={onDraft}><MessageCircleReply size={14} /> Draft a reply</Button>
          {canIdea && (m.idea_id
            ? <Link to="/research?status=saved"><Button variant="ghost" className="h-8 px-2.5"><Lightbulb size={14} /> Idea saved</Button></Link>
            : <Button variant="ghost" className="h-8 px-2.5" disabled={idea.isPending} onClick={() => idea.mutate()}><Lightbulb size={14} /> {idea.isPending ? "Saving…" : "Post idea"}</Button>)}
          {canManage && !m.relevant && <Button variant="ghost" className="h-8 px-2.5" onClick={() => patch.mutate({ relevant: true })}><RotateCcw size={14} /> It's about us</Button>}
          {canManage && m.relevant && m.status === "new" && <Button variant="ghost" className="h-8 px-2.5" onClick={() => patch.mutate({ status: "reviewed" })}><Check size={14} /> Reviewed</Button>}
          {canManage && m.status !== "new" && <Button variant="ghost" className="h-8 px-2.5" onClick={() => patch.mutate({ status: "new" })}><Eye size={14} /> Mark new</Button>}
          {canManage && m.status !== "hidden" && <Button variant="ghost" className="h-8 px-2.5" title="Hide: not useful or not about you" onClick={() => patch.mutate({ status: "hidden" })}><EyeOff size={14} /> Hide</Button>}
        </div>
      </div>
      {idea.data && <p className="mt-2 text-sm text-emerald-700">Saved “{idea.data.title}” to <Link className="underline" to="/research?status=saved">Research & ideas</Link>.</p>}
      {err && <div className="mt-2"><Alert>{err}</Alert></div>}
    </li>
  );
}

export default function Feed({ queries, sources, filters, setFilters }: {
  queries: Query[]; sources: SourceInfo[]; filters: Filters; setFilters: (f: Filters) => void;
}) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [pages, setPages] = useState(1);
  const [draft, setDraft] = useState<Mention | null>(null);
  const [search, setSearch] = useState(filters.q);
  const f = filters;
  const set = (patch: Partial<Filters>) => { setPages(1); setFilters({ ...f, ...patch }); };
  const list = useQuery({
    queryKey: ["listening-mentions", f, pages],
    queryFn: () => api<MentionPage>(`/listening/mentions?${params(f, 0).replace(`limit=${PAGE}`, `limit=${PAGE * pages}`)}`),
    placeholderData: (prev) => prev,
  });
  const bulk = useMutation({
    mutationFn: (ids: string[]) => api("/listening/mentions/bulk", { method: "POST", json: { ids, status: "reviewed" } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["listening-mentions"] }); qc.invalidateQueries({ queryKey: ["listening-status"] }); },
  });
  const items = list.data?.items ?? [];
  const newOnes = items.filter((m) => m.status === "new" && m.relevant).map((m) => m.id);
  const active = JSON.stringify({ ...f, sort: "newest" }) !== JSON.stringify({ ...EMPTY_FILTERS, sort: "newest" });

  return (
    <div>
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-[1.4fr_repeat(5,minmax(0,1fr))]">
        <form className="relative" onSubmit={(e) => { e.preventDefault(); set({ q: search }); }}>
          <Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" />
          <Input aria-label="Search mentions" placeholder="Search mentions" value={search} onChange={(e) => setSearch(e.target.value)} onBlur={() => search !== f.q && set({ q: search })} className="pl-9" />
        </form>
        <Select aria-label="Topic" value={f.query_id} onChange={(e) => set({ query_id: e.target.value })}>
          <option value="">All topics</option>
          {queries.map((q) => <option key={q.id} value={q.id}>{q.name} ({KIND_LABEL[q.kind]})</option>)}
        </Select>
        <Select aria-label="Source" value={f.source} onChange={(e) => set({ source: e.target.value })}>
          <option value="">All sources</option>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </Select>
        <Select aria-label="Sentiment" value={f.sentiment} onChange={(e) => set({ sentiment: e.target.value })}>
          <option value="">Any sentiment</option><option value="positive">Positive</option><option value="neutral">Neutral</option><option value="negative">Negative</option>
        </Select>
        <Select aria-label="Date" value={f.days} onChange={(e) => set({ days: e.target.value })}>
          <option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="">Any time</option>
        </Select>
        <Select aria-label="Status" value={f.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="open">To look at</option><option value="new">New only</option><option value="reviewed">Reviewed</option>
          <option value="hidden">Hidden</option><option value="filtered">Filtered out as not about you</option>
        </Select>
      </div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button onClick={() => set({ needs_response: !f.needs_response })}
            className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 ${f.needs_response ? "border-amber-400 bg-amber-50 text-amber-900" : "border-line text-muted hover:text-ink"}`}>
            <MessageCircleReply size={14} /> Needs a reply
          </button>
          <button onClick={() => set({ sort: f.sort === "newest" ? "engagement" : "newest" })}
            className="inline-flex h-8 items-center rounded-full border border-line px-3 text-muted hover:text-ink">
            Sort: {f.sort === "newest" ? "newest first" : "most engagement"}
          </button>
          {active && <button className="text-sm text-brand hover:underline" onClick={() => { setSearch(""); set(EMPTY_FILTERS); }}>Clear filters</button>}
        </div>
        <div className="flex items-center gap-3 text-sm text-muted">
          {list.data && <span>{list.data.total.toLocaleString("en-IN")} mention{list.data.total === 1 ? "" : "s"}</span>}
          {can("listening.manage") && newOnes.length > 1 && (
            <Button variant="secondary" className="h-8 px-3" disabled={bulk.isPending} onClick={() => bulk.mutate(newOnes)}>
              <Check size={14} /> Mark {newOnes.length} shown as reviewed
            </Button>
          )}
        </div>
      </div>
      {list.isLoading ? <Spinner label="Loading mentions…" /> : list.error ? <Alert>{(list.error as Error).message}</Alert> : !items.length ? (
        <EmptyState title={active ? "Nothing matches these filters" : "No mentions yet"}
          body={active ? "Try a longer date range or clear the filters." : "Once your topics are searched, posts, articles and comments that mention them show up here."} />
      ) : (
        <>
          <ul className="space-y-3">
            {items.map((m) => <MentionCard key={m.id} m={m} canManage={can("listening.manage")} canIdea={can("listening.manage") && can("research.view")} onDraft={() => setDraft(m)} />)}
          </ul>
          {list.data && list.data.total > items.length && (
            <div className="mt-4 flex justify-center">
              <Button variant="secondary" disabled={list.isFetching} onClick={() => setPages(pages + 1)}>{list.isFetching ? "Loading…" : "Show more"}</Button>
            </div>
          )}
        </>
      )}
      {draft && <DraftModal m={draft} onClose={() => setDraft(null)} />}
    </div>
  );
}
