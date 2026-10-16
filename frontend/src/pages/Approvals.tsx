import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clock, Pencil, RefreshCw, X } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, EmptyState, Field, Modal, PageHeader, Spinner, Textarea } from "../components/ui";
import {
  PostPreview, QaSummary, Warnings, fmt, tzLabel, until, useMeta, useNow, useTz, type PostSummary,
} from "./content/shared";

type Item = PostSummary & { link_url: string | null; qa: { issues?: string[]; checks?: { name: string; ok: boolean; detail: string }[] } };

export function RejectDialog({ count, onClose, onReject, busy, error }: {
  count: number; onClose: () => void; onReject: (reason: string) => void; busy: boolean; error?: Error | null;
}) {
  const [reason, setReason] = useState("");
  return (
    <Modal title={count > 1 ? `Reject ${count} posts` : "Reject this post"} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onReject(reason); }}>
        {error && <Alert>{error.message}</Alert>}
        <Field label="What was wrong?" hint="The AI avoids this in future posts. For example: too salesy, wrong product name, don't mention pricing.">
          <Textarea rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="danger" disabled={busy}>{busy ? "Rejecting…" : "Reject"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function Card({ p, selected, onSelect, highlight }: { p: Item; selected: boolean; onSelect: (v: boolean) => void; highlight: boolean }) {
  const qc = useQueryClient();
  const tz = useTz();
  const now = useNow();
  const meta = useMeta();
  const { can } = useAuth();
  const [rejecting, setRejecting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["approvals"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); };
  const approve = useMutation({ mutationFn: () => api<{ message: string }>(`/content/posts/${p.id}/approve`, { method: "POST" }), onSuccess: (r) => { setMsg(r.message); refresh(); } });
  const reject = useMutation({
    mutationFn: (reason: string) => api<{ message: string }>(`/content/posts/${p.id}/reject`, { method: "POST", json: { reason } }),
    onSuccess: () => { setRejecting(false); refresh(); },
  });
  const regen = useMutation({ mutationFn: () => api<{ message: string }>(`/content/posts/${p.id}/regenerate`, { method: "POST", json: {} }), onSuccess: (r) => { setMsg(r.message); refresh(); } });
  const err = approve.error || regen.error;
  const deadline = p.mode === "auto_with_window" ? p.approval_deadline : p.scheduled_at;
  const overdue = deadline ? new Date(deadline).getTime() < now : false;

  return (
    <li className={`rounded-xl border bg-surface p-4 ${highlight ? "border-primary-500 ring-2 ring-primary-500/20" : "border-line"}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <input type="checkbox" aria-label="Select for bulk action" checked={selected} onChange={(e) => onSelect(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--brand))]" />
        <Badge tone="brand">{meta.data?.post_types[p.post_type] ?? p.post_type}</Badge>
        {p.pillar && <span className="inline-flex items-center gap-1 text-muted"><span className="h-2 w-2 rounded-full" style={{ background: p.pillar.color }} />{p.pillar.name}</span>}
        <span className="ml-auto inline-flex items-center gap-1 text-muted">
          <Clock size={14} /> Posts {fmt(p.scheduled_at, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })} {tzLabel(tz)}
        </span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px]">
        <PostPreview post={p} clamp />
        <div className="space-y-3 text-sm">
          {deadline && (
            <div className={`rounded-lg px-3 py-2 ${overdue ? "bg-danger-tint text-danger" : p.mode === "auto_with_window" ? "bg-amber-50 text-amber-900" : "bg-brand-tint text-brand-deep"}`}>
              {p.mode === "auto_with_window"
                ? overdue ? "Review window ended. It will publish shortly." : `Publishes automatically ${until(deadline, now)} unless rejected.`
                : overdue ? "Its time has passed. Approve to post it now." : `Approve within ${until(deadline, now).replace("in ", "")}.`}
            </div>
          )}
          <QaSummary score={p.qa_score} passed={p.qa_passed} similarity={p.similarity} />
          <div className={`text-xs ${p.char_count > p.max_chars ? "text-danger" : "text-muted"}`}>{p.char_count} of {p.max_chars} characters</div>
          {p.topic && <div className="text-xs text-muted">Topic: {p.topic}</div>}
          {(p.qa.issues ?? []).length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{p.qa.issues!.slice(0, 4).map((i, n) => <li key={n}>{i}</li>)}</ul>
          )}
          {p.warnings.length > 0 && <Warnings items={p.warnings} />}
        </div>
      </div>
      {err && <div className="mt-3"><Alert>{(err as Error).message}</Alert></div>}
      {msg && <div className="mt-3"><Alert tone="info">{msg}</Alert></div>}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={() => approve.mutate()} disabled={approve.isPending}><Check size={16} /> {approve.isPending ? "Approving…" : "Approve"}</Button>
        <Link to={`/posts/${p.id}`}><Button variant="secondary"><Pencil size={15} /> Edit</Button></Link>
        {can("content.create") && <Button variant="secondary" onClick={() => regen.mutate()} disabled={regen.isPending}><RefreshCw size={15} /> Write a new version</Button>}
        <Button variant="danger" onClick={() => setRejecting(true)}><X size={15} /> Reject</Button>
      </div>
      {rejecting && <RejectDialog count={1} onClose={() => setRejecting(false)} onReject={(r) => reject.mutate(r)} busy={reject.isPending} error={reject.error as Error | null} />}
    </li>
  );
}

export default function Approvals() {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const focus = params.get("post");
  const q = useQuery({ queryKey: ["approvals"], queryFn: () => api<Item[]>("/content/approvals"), refetchInterval: 30_000 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkReject, setBulkReject] = useState(false);
  const bulk = useMutation({
    mutationFn: (b: { action: "approve" | "reject"; reason?: string }) =>
      api<{ ok: number; failed: number; results: { id: string; ok: boolean; message: string }[] }>("/content/approvals/bulk", { method: "POST", json: { ids: [...selected], ...b } }),
    onSuccess: () => { setSelected(new Set()); setBulkReject(false); qc.invalidateQueries({ queryKey: ["approvals"] }); },
  });
  const items = q.data ?? [];
  const toggle = (id: string, v: boolean) => { const s = new Set(selected); v ? s.add(id) : s.delete(id); setSelected(s); };
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.id));

  return (
    <div>
      <PageHeader title="Approvals" description="Posts waiting for a decision, soonest first. Approved posts go out at their scheduled time." />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading ? <Spinner label="Loading the queue…" /> : items.length === 0 ? (
        <EmptyState title="Nothing to review" body="New posts appear here when a schedule writes them. You'll also get a notification." />
      ) : (
        <>
          <div className="sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface/95 px-3 py-2 backdrop-blur">
            <label className="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" checked={allSelected} onChange={(e) => setSelected(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())} className="h-4 w-4 accent-[rgb(var(--brand))]" />
              Select all ({items.length})
            </label>
            {selected.size > 0 && (
              <>
                <Button onClick={() => bulk.mutate({ action: "approve" })} disabled={bulk.isPending}><Check size={16} /> Approve {selected.size}</Button>
                <Button variant="danger" onClick={() => setBulkReject(true)} disabled={bulk.isPending}>Reject {selected.size}</Button>
              </>
            )}
            {bulk.data && bulk.data.failed > 0 && (
              <span className="text-sm text-danger">{bulk.data.failed} couldn't be changed: {bulk.data.results.find((r) => !r.ok)?.message}</span>
            )}
            {bulk.error && <span className="text-sm text-danger">{(bulk.error as Error).message}</span>}
          </div>
          <ul className="space-y-4">
            {items.map((p) => <Card key={p.id} p={p} highlight={p.id === focus} selected={selected.has(p.id)} onSelect={(v) => toggle(p.id, v)} />)}
          </ul>
        </>
      )}
      {bulkReject && <RejectDialog count={selected.size} onClose={() => setBulkReject(false)} onReject={(reason) => bulk.mutate({ action: "reject", reason })} busy={bulk.isPending} error={bulk.error as Error | null} />}
    </div>
  );
}
