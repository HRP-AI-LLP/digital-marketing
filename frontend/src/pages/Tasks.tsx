// Background tasks: what the studio did on its own (writing, publishing, syncing…), with failed ones first in mind.
// The dashboard warning links here with ?status=failed&since=…; a failure notification adds &job=<id> to open it.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ExternalLink, Loader2, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, EmptyState, PageHeader } from "../components/ui";

type Job = {
  id: string; kind: string; label: string; link: string | null; status: string; error: string | null;
  attempts: number; created_at: string; finished_at: string | null; run_after: string | null;
};

const STATUSES: { id: string; label: string }[] = [
  { id: "failed", label: "Failed" }, { id: "running", label: "Running" }, { id: "queued", label: "Waiting" },
  { id: "done", label: "Done" }, { id: "", label: "All" },
];

function fmt(iso: string | null, tz?: string) {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: tz }).format(new Date(iso));
  } catch { return iso; }
}

/** The useful part of an error: drop the exception class and the generic advice tail. */
function summary(err: string | null): string {
  if (!err) return "";
  const first = err.replace(/^[A-Za-z]+Error:\s*/, "").split("\n")[0];
  return first.length > 220 ? first.slice(0, 220) + "…" : first;
}

function StatusChip({ s }: { s: string }) {
  const map: Record<string, string> = {
    failed: "bg-danger-tint text-danger", done: "bg-emerald-50 text-emerald-700", running: "bg-blue-50 text-blue-700",
    queued: "bg-slate-100 text-slate-700", cancelled: "bg-slate-100 text-slate-500",
  };
  const label = STATUSES.find((x) => x.id === s)?.label || s;
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[s] || map.queued}`}>{label}</span>;
}

export default function Tasks() {
  const { session } = useAuth();
  const tz = session?.organization?.timezone;
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "failed";
  const since = params.get("since");
  const focus = params.get("job");
  const [open, setOpen] = useState<Record<string, boolean>>(focus ? { [focus]: true } : {});
  const qc = useQueryClient();

  const qs = useMemo(() => {
    const p = new URLSearchParams({ limit: "200" });
    if (status) p.set("status", status);
    if (since) p.set("since", since);
    return p.toString();
  }, [status, since]);
  const q = useQuery({ queryKey: ["jobs", qs], queryFn: () => api<Job[]>(`/jobs?${qs}`), refetchInterval: 15000 });

  const retry = useMutation({
    mutationFn: (id: string) => api<Job>(`/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  useEffect(() => {
    if (focus && q.data?.some((j) => j.id === focus)) {
      document.getElementById(`job-${focus}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [focus, q.data]);

  const setStatus = (s: string) => {
    const p = new URLSearchParams(params);
    if (s) p.set("status", s); else p.set("status", "");
    p.delete("job");
    setParams(p, { replace: true });
  };
  const clearSince = () => { const p = new URLSearchParams(params); p.delete("since"); setParams(p, { replace: true }); };

  const rows = q.data || [];
  return (
    <div className="max-w-5xl">
      <PageHeader title="Background tasks"
        description="What the studio does on its own: writing and publishing posts, fetching comments, research and more. Open a failed task to see what went wrong and retry it." />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
          {STATUSES.map((s) => (
            <button key={s.id || "all"} type="button" onClick={() => setStatus(s.id)}
              className={`rounded-md px-3 py-1.5 text-sm ${status === s.id ? "bg-brand/10 font-semibold text-ink" : "text-muted hover:text-ink"}`}>
              {s.label}
            </button>
          ))}
        </div>
        {since && (
          <span className="inline-flex items-center gap-2 rounded-full bg-ink/5 px-3 py-1 text-xs text-muted">
            Since {fmt(since, tz)}
            <button type="button" onClick={clearSince} className="font-semibold text-ink hover:underline">Show older too</button>
          </span>
        )}
      </div>

      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading && <p className="text-sm text-muted">Loading…</p>}
      {!q.isLoading && rows.length === 0 && (
        <EmptyState title={status === "failed" ? "No failed tasks" : "Nothing here"}
          body={status === "failed" ? "Everything the studio ran in this period worked." : "No tasks match this filter."} />
      )}

      {rows.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {rows.map((j) => {
            const isOpen = !!open[j.id];
            return (
              <li key={j.id} id={`job-${j.id}`} className={focus === j.id ? "bg-brand/5" : ""}>
                <button type="button" onClick={() => setOpen((o) => ({ ...o, [j.id]: !isOpen }))}
                  className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-ink/[0.03]">
                  <span className="mt-0.5 text-muted">{isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
                  <span className="mt-0.5">
                    {j.status === "failed" ? <AlertTriangle size={16} className="text-danger" />
                      : j.status === "done" ? <CheckCircle2 size={16} className="text-emerald-600" />
                      : <Loader2 size={16} className={j.status === "running" ? "animate-spin text-blue-600" : "text-muted"} />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink">{j.label}</span>
                      <StatusChip s={j.status} />
                    </span>
                    {j.status === "failed" && j.error && <span className="mt-1 block text-sm text-danger">{summary(j.error)}</span>}
                  </span>
                  <span className="shrink-0 text-right text-xs text-muted">{fmt(j.finished_at || j.created_at, tz)}</span>
                </button>
                {isOpen && (
                  <div className="space-y-3 border-t border-line bg-ink/[0.02] px-4 py-3 pl-14 text-sm">
                    <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1">
                      <dt className="text-muted">Task</dt><dd><code className="text-xs">{j.kind}</code></dd>
                      <dt className="text-muted">Started</dt><dd>{fmt(j.created_at, tz)}</dd>
                      {j.finished_at && <><dt className="text-muted">Finished</dt><dd>{fmt(j.finished_at, tz)}</dd></>}
                      <dt className="text-muted">Attempts</dt><dd>{j.attempts}</dd>
                    </dl>
                    {j.error && (
                      <div>
                        <p className="mb-1 text-muted">What went wrong</p>
                        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface p-3 text-xs">{j.error}</pre>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {j.link && (
                        <Link to={j.link}><Button variant="secondary"><ExternalLink size={14} /> Open what it was about</Button></Link>
                      )}
                      {(j.status === "failed" || j.status === "cancelled") && (
                        <Button variant="secondary" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>
                          <RotateCcw size={14} /> Retry
                        </Button>
                      )}
                    </div>
                    {retry.error && retry.variables === j.id && <Alert>{(retry.error as Error).message}</Alert>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
