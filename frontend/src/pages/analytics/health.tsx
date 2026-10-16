import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, Sparkles, X, Zap } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { Alert, Badge, Button, Card, EmptyState, Spinner, formatDate } from "../../components/ui";
import type { Health, Rec, Recs } from "./types";
import { Meter, ScoreRing, TrendChart, shortDay } from "./vizkit";
import { Segmented } from "./growth";

export function ScoreCard({ h, compact }: { h: Health; compact?: boolean }) {
  return (
    <div className={`flex flex-wrap items-center gap-6 ${compact ? "" : ""}`}>
      <ScoreRing score={h.score} size={compact ? 120 : 148} />
      <div className="min-w-[12rem] flex-1">
        <div className="text-sm text-muted">Marketing health</div>
        <div className="text-2xl font-bold text-ink">{h.grade}</div>
        {h.change_7d != null && (
          <div className={`text-sm tabular-nums ${h.change_7d > 0 ? "text-emerald-700" : h.change_7d < 0 ? "text-danger" : "text-muted"}`}>
            {h.change_7d > 0 ? "▲" : h.change_7d < 0 ? "▼" : "•"} {Math.abs(h.change_7d)} points in the last 7 days
          </div>
        )}
        <p className="mt-1 max-w-md text-sm text-muted">
          A weighted average of the parts below. Parts without enough data are left out until the numbers come in.
        </p>
      </div>
    </div>
  );
}

const IMPACT_TONE = { high: "brand", medium: "accent", low: "neutral" } as const;

function TipCard({ r, canManage }: { r: Rec; canManage: boolean }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("analytics-") });
  const [msg, setMsg] = useState<{ text: string; link: string | null } | null>(null);
  const apply = useMutation({
    mutationFn: () => api<{ message: string; link: string | null }>(`/analytics/recommendations/${r.id}/apply`, { method: "POST" }),
    onSuccess: (x) => { setMsg({ text: x.message, link: x.link }); refresh(); },
  });
  const setStatus = useMutation({
    mutationFn: (status: Rec["status"]) => api(`/analytics/recommendations/${r.id}`, { method: "PATCH", json: { status } }),
    onSuccess: refresh,
  });
  const done = r.status !== "new";
  return (
    <li className={`rounded-xl border border-line bg-surface p-4 ${done ? "opacity-75" : ""}`}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <h3 className="min-w-0 flex-1 font-semibold text-ink">{r.title}</h3>
        <div className="flex gap-1.5">
          <Badge tone={IMPACT_TONE[r.impact]}>{r.impact} impact</Badge>
          <Badge>{r.effort} effort</Badge>
        </div>
      </div>
      {r.why && <p className="mt-1 text-sm text-muted"><span className="font-medium text-ink">Why: </span>{r.why}</p>}
      {r.action && <p className="mt-1 text-sm"><span className="font-medium">What to do: </span>{r.action}</p>}
      {(msg || r.result) && (
        <div className="mt-2"><Alert tone="info">{msg?.text ?? r.result}{msg?.link && <> <Link className="underline" to={msg.link}>Open</Link></>}</Alert></div>
      )}
      {apply.error && <div className="mt-2"><Alert>{(apply.error as Error).message}</Alert></div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {r.status === "new" && canManage && r.one_click && (
          <Button onClick={() => apply.mutate()} disabled={apply.isPending}><Zap size={15} /> {r.one_click.label}</Button>
        )}
        {r.status === "new" && canManage && (
          <>
            <Button variant="secondary" onClick={() => setStatus.mutate("done")} disabled={setStatus.isPending}><Check size={15} /> Mark as done</Button>
            <Button variant="ghost" onClick={() => setStatus.mutate("dismissed")} disabled={setStatus.isPending}><X size={15} /> Dismiss</Button>
          </>
        )}
        {done && (
          <>
            <span className="text-xs text-muted">{r.status === "done" ? "Done" : "Dismissed"}{r.status_changed_at ? ` ${formatDate(r.status_changed_at)}` : ""}</span>
            {canManage && <Button variant="ghost" onClick={() => setStatus.mutate("new")}>Move back to open</Button>}
          </>
        )}
      </div>
    </li>
  );
}

export function HealthPanel() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"new" | "done" | "dismissed">("new");
  const h = useQuery({ queryKey: ["analytics-health"], queryFn: () => api<Health>("/analytics/health") });
  const recs = useQuery({ queryKey: ["analytics-recs"], queryFn: () => api<Recs>("/analytics/recommendations") });
  const [job, setJob] = useState<string | null>(null);
  const gen = useMutation({ mutationFn: () => api<{ job_id: string }>("/analytics/recommendations/generate", { method: "POST" }), onSuccess: (r) => setJob(r.job_id) });
  const running = useJob(job, () => qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("analytics-") }));
  const busy = (!!job && running?.status !== "done" && running?.status !== "failed") || gen.isPending;

  const items = recs.data?.items.filter((r) => r.status === filter) ?? [];
  const counts = { new: 0, done: 0, dismissed: 0 };
  recs.data?.items.forEach((r) => { counts[r.status] += 1; });
  const last = recs.data?.last_run;

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="min-w-0 space-y-6">
        <Card>
          {h.isLoading && <Spinner label="Working out your score" />}
          {h.error && <Alert>{(h.error as Error).message}</Alert>}
          {h.data && <ScoreCard h={h.data} />}
        </Card>
        {h.data && (
          <Card>
            <h2 className="mb-3 text-sm font-semibold">How the score is made</h2>
            <ul className="space-y-4">
              {h.data.parts.map((p) => (
                <li key={p.key}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="font-medium text-ink">{p.label} <span className="font-normal text-muted">· weight {p.weight}</span></span>
                    <span className="tabular-nums font-semibold">{p.score ?? "—"}</span>
                  </div>
                  <div className="mt-1"><Meter value={p.score} label={`${p.label} score`} /></div>
                  <div className="mt-1 text-xs"><span className="text-ink">{p.value}.</span> <span className="text-muted">{p.how}</span></div>
                  {p.detail && <div className="text-xs text-muted">{p.detail}</div>}
                </li>
              ))}
            </ul>
          </Card>
        )}
        {h.data && h.data.history.length > 1 && (
          <Card>
            <TrendChart title="Score over time" labels={h.data.history.map((x) => shortDay(x.day))}
              series={[{ name: "Score", values: h.data.history.map((x) => x.score) }]} height={180} />
          </Card>
        )}
      </div>

      <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">How to improve your score</h2>
            <p className="text-sm text-muted">
              {last ? <>Tips from {formatDate(last.at)}{last.source === "ai" ? ", written by AI from your numbers" : ", from built-in rules"}. New tips every week.</> : "Tips are written from your numbers every week."}
            </p>
          </div>
          {recs.data?.can_manage && (
            <Button onClick={() => gen.mutate()} disabled={busy}><Sparkles size={16} className={busy ? "animate-pulse" : ""} /> {busy ? "Writing tips…" : "Get fresh tips"}</Button>
          )}
        </div>
        {busy && <Spinner label={running?.progress ?? "Waiting for the background worker…"} />}
        {running?.status === "failed" && <Alert>{running.error}</Alert>}
        {gen.error && <Alert>{(gen.error as Error).message}</Alert>}
        {last?.message && <Alert tone="warn">{last.message}</Alert>}
        <Segmented label="Show tips" value={filter} onChange={setFilter}
          items={[{ id: "new", label: `Open (${counts.new})` }, { id: "done", label: `Done (${counts.done})` }, { id: "dismissed", label: `Dismissed (${counts.dismissed})` }]} />
        {recs.isLoading && <Spinner label="Loading tips" />}
        {recs.error && <Alert>{(recs.error as Error).message}</Alert>}
        {recs.data && items.length === 0 && (
          <EmptyState title={filter === "new" ? "No open tips" : `Nothing ${filter} yet`}
            body={filter === "new" ? "Press Get fresh tips to have your numbers reviewed." : undefined} />
        )}
        <ul className="space-y-3">{items.map((r) => <TipCard key={r.id} r={r} canManage={!!recs.data?.can_manage} />)}</ul>
      </div>
    </div>
  );
}
