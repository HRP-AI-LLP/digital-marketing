import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Search, Send, Trash2, X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, EmptyState, Input, PageHeader, Select, Spinner } from "../../components/ui";
import { RejectDialog } from "../Approvals";
import { PostStatus, STATUS_LABELS, fmt, tzLabel, useContentAccounts, useMeta, usePillars, useTz, type PostSummary } from "./shared";

const PAGE = 50;

type Action = "approve" | "reject" | "publish" | "delete";
type BulkResult = { results: { id: string; ok: boolean; message: string }[]; ok: number; failed: number };
const DONE: Record<Action, string> = { approve: "approved", reject: "rejected", publish: "sent for publishing", delete: "deleted" };
// Which statuses each action can apply to (the server re-checks every post).
const ELIGIBLE: Record<Action, string[]> = {
  approve: ["in_review", "draft", "missed", "rejected"],
  reject: ["in_review", "draft", "scheduled", "approved", "missed"],
  publish: ["in_review", "draft", "scheduled", "approved", "missed", "rejected", "failed"],
  delete: ["planned", "in_review", "draft", "scheduled", "approved", "missed", "rejected", "failed", "manual_pending", "cancelled"],
};

export default function PostList() {
  const tz = useTz();
  const nav = useNavigate();
  const { can } = useAuth();
  const meta = useMeta();
  const accounts = useContentAccounts();
  const pillars = usePillars();
  const [urlParams] = useSearchParams();
  const [f, setF] = useState({ status: urlParams.get("status") ?? "", platform: "", account_id: "", pillar_id: "", q: "", start: "", end: "" });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const qc = useQueryClient();
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [rejecting, setRejecting] = useState(false);
  const [summary, setSummary] = useState<{ action: Action; r: BulkResult } | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  for (const [k, v] of Object.entries(f)) {
    if (!v) continue;
    params.set(k, k === "start" || k === "end" ? `${v}T00:00:00` : v);
  }
  const q = useQuery({
    queryKey: ["posts", params.toString()],
    queryFn: () => api<{ items: PostSummary[]; total: number }>(`/content/posts?${params}`),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setPage(0); };
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const platforms = [...new Set((accounts.data ?? []).map((a) => a.platform))];
  useEffect(() => { setSel(new Set()); }, [params.toString()]);  // a new page or filter starts a fresh selection
  const chosen = items.filter((p) => sel.has(p.id));
  const allOnPage = items.length > 0 && items.every((p) => sel.has(p.id));
  const toggle = (id: string, on: boolean) => setSel((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n; });
  const count = (a: Action) => chosen.filter((p) => ELIGIBLE[a].includes(p.status)).length;
  const bulk = useMutation({
    mutationFn: ({ action, reason }: { action: Action; reason?: string }) =>
      api<BulkResult>("/content/posts/bulk", { method: "POST", json: { ids: [...sel], action, reason: reason || null } }),
    onSuccess: (r, v) => {
      setSummary({ action: v.action, r }); setRejecting(false);
      setSel(new Set(r.results.filter((x) => !x.ok).map((x) => x.id)));  // keep the ones that need attention selected
      for (const k of ["posts", "approvals", "calendar", "post", "dashboard"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });
  const run = (action: Action) => {
    const n = sel.size;
    if (action === "reject") { setRejecting(true); return; }
    if (action === "publish" && !confirm(`Publish ${n} post${n > 1 ? "s" : ""} now?`)) return;
    if (action === "delete" && !confirm(`Delete ${n} post${n > 1 ? "s" : ""}? This can't be undone. Published posts are kept.`)) return;
    bulk.mutate({ action });
  };
  const label = (id: string) => { const p = items.find((x) => x.id === id); return p ? (p.title || p.text || p.topic || "Post").slice(0, 70) : "Post"; };

  return (
    <div>
      <PageHeader title="All posts" description={`Every post across your accounts. Times are ${tzLabel(tz)}.`}
        action={can("content.create") && <Button onClick={() => nav("/posts/new")}><Plus size={16} /> New post</Button>} />

      <form className="mb-4 grid gap-2 md:grid-cols-4 xl:grid-cols-7" onSubmit={(e) => { e.preventDefault(); setF({ ...f, q: search.trim() }); setPage(0); }}>
        <div className="relative md:col-span-2">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search posts" placeholder="Search text or topic, then press Enter" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <Select aria-label="Status" value={f.status} onChange={set("status")}>
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).filter(([k]) => k !== "skipped").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
        <Select aria-label="Account" value={f.account_id} onChange={set("account_id")}>
          <option value="">All accounts</option>
          {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
        <Select aria-label="Platform" value={f.platform} onChange={set("platform")}>
          <option value="">All platforms</option>
          {platforms.map((p) => <option key={p} value={p}>{meta.data?.platforms.find((x) => x.id === p)?.label ?? p}</option>)}
        </Select>
        <Select aria-label="Pillar" value={f.pillar_id} onChange={set("pillar_id")}>
          <option value="">All pillars</option>
          {(pillars.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
        <div className="flex gap-1">
          <Input type="date" aria-label="From" title="From" value={f.start} onChange={set("start")} />
          <Input type="date" aria-label="To (not included)" title="To (not included)" value={f.end} onChange={set("end")} />
        </div>
      </form>

      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {bulk.error && <div className="mb-3"><Alert>{(bulk.error as Error).message}</Alert></div>}
      {summary && (
        <div className="mb-3">
          <Alert tone={summary.r.failed ? "warn" : "info"}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div>{summary.r.ok} post{summary.r.ok === 1 ? "" : "s"} {DONE[summary.action]}.{summary.r.failed ? ` ${summary.r.failed} couldn't be:` : ""}</div>
                {summary.r.failed > 0 && (
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                    {summary.r.results.filter((x) => !x.ok).slice(0, 8).map((x) => (
                      <li key={x.id}><Link to={`/posts/${x.id}`} className="underline">{label(x.id)}</Link> — {x.message}</li>
                    ))}
                  </ul>
                )}
              </div>
              <button aria-label="Dismiss" onClick={() => setSummary(null)} className="text-muted hover:text-ink"><X size={15} /></button>
            </div>
          </Alert>
        </div>
      )}
      {sel.size > 0 && (
        <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-primary-500/40 bg-surface px-3 py-2 shadow-[0_8px_24px_rgba(6,47,89,0.12)]">
          <span className="mr-1 text-sm font-medium">{sel.size} selected</span>
          {can("content.approve") && <Button onClick={() => run("approve")} disabled={bulk.isPending || !count("approve")} title="Posts need a scheduled time to be approved"><Check size={15} />Approve{count("approve") !== sel.size ? ` (${count("approve")})` : ""}</Button>}
          {can("content.approve") && <Button variant="danger" onClick={() => run("reject")} disabled={bulk.isPending || !count("reject")}><X size={15} />Reject{count("reject") !== sel.size ? ` (${count("reject")})` : ""}</Button>}
          {can("content.publish") && <Button variant="secondary" onClick={() => run("publish")} disabled={bulk.isPending || !count("publish")}><Send size={15} />Publish now{count("publish") !== sel.size ? ` (${count("publish")})` : ""}</Button>}
          {can("content.create") && <Button variant="ghost" onClick={() => run("delete")} disabled={bulk.isPending || !count("delete")}><Trash2 size={15} />Delete{count("delete") !== sel.size ? ` (${count("delete")})` : ""}</Button>}
          {bulk.isPending && <Spinner label="Working…" />}
          <button className="ml-auto text-sm text-muted hover:text-ink" onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}
      {q.isLoading ? <Spinner label="Loading posts…" /> : items.length === 0 ? (
        <EmptyState title="No posts match" body={Object.values(f).some(Boolean) ? "Try removing a filter." : "Posts appear here once a schedule runs or you write one."}
          action={can("content.create") && <Button onClick={() => nav("/posts/new")}><Plus size={16} /> New post</Button>} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b border-line bg-canvas text-left text-xs text-muted">
              <tr>
                <th className="w-10 px-3 py-2">
                  <input type="checkbox" aria-label="Select all on this page" checked={allOnPage}
                    onChange={(e) => setSel(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())} className="h-4 w-4 accent-[rgb(var(--brand))]" />
                </th>
                <th className="px-3 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">Account</th>
                <th className="px-3 py-2 font-medium">Post</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((p) => (
                <tr key={p.id} className={`cursor-pointer hover:bg-canvas ${sel.has(p.id) ? "bg-brand-tint" : ""}`} onClick={() => nav(`/posts/${p.id}`)}>
                  <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label="Select post" checked={sel.has(p.id)} onChange={(e) => toggle(p.id, e.target.checked)} className="h-4 w-4 accent-[rgb(var(--brand))]" />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 tabular-nums text-muted">{fmt(p.published_at ?? p.scheduled_at, tz, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</td>
                  <td className="px-3 py-2.5">
                    <div className="font-medium">{p.account?.name ?? "—"}</div>
                    <div className="text-xs text-muted">{p.platform_label}</div>
                  </td>
                  <td className="max-w-[460px] px-3 py-2.5">
                    <Link to={`/posts/${p.id}`} onClick={(e) => e.stopPropagation()} className="line-clamp-2 hover:underline">
                      {p.title || p.text || <span className="text-muted">{p.topic ?? "Not written yet"}</span>}
                    </Link>
                    {p.pillar && <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-muted"><span className="h-2 w-2 rounded-full" style={{ background: p.pillar.color }} />{p.pillar.name}</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-muted">{meta.data?.post_types[p.post_type] ?? p.post_type}</td>
                  <td className="px-3 py-2.5"><PostStatus status={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > PAGE && (
        <div className="mt-3 flex items-center justify-between text-sm text-muted">
          <span>{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button variant="secondary" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      )}
      {rejecting && <RejectDialog count={sel.size} onClose={() => setRejecting(false)} onReject={(reason) => bulk.mutate({ action: "reject", reason })}
        busy={bulk.isPending} error={bulk.error as Error | null} />}
    </div>
  );
}
