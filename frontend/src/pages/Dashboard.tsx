import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight, X } from "lucide-react";
import { Gauge, Panel, TipRow } from "../components/viz";
import DashboardInsights from "./dashboard/Insights";
import SystemMap from "./dashboard/SystemMap";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert } from "../components/ui";

type Dash = {
  organization: { id: string; name: string; timezone: string } | null;
  counts: { in_review: number; scheduled_7d: number; published_7d: number; failed: number; manual_pending: number };
  upcoming: { id: string; platform: string; account: string | null; status: string; post_type: string; scheduled_at: string; topic: string | null; text: string }[];
  accounts: { id: string; platform: string; platform_label: string; name: string; status: string }[];
  setup: { key: string; title: string; body: string; done: boolean; to: string }[];
  audit_score: number | null;
  social_presence: { score: number | null; grade: string; change: number | null; at: string } | null;
  failing_jobs: number;
};

function Pill({ label, value, to, tone }: { label: string; value: number; to: string; tone?: "warn" }) {
  return (
    <Link to={to} className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors hover:border-primary-300 ${tone === "warn" && value > 0 ? "border-amber-300 bg-amber-50" : "border-line bg-surface"}`}>
      <span className="font-bold tabular-nums text-ink">{value}</span><span className="text-muted">{label}</span>
    </Link>
  );
}

type HealthSummary = { score: number | null; grade: string; weakest: { label: string; score: number }[]; top_tip: string | null };

function ScoresRow({ d, canAnalytics, canPresence }: { d: Dash; canAnalytics: boolean; canPresence: boolean }) {
  const q = useQuery({ queryKey: ["analytics-health-summary"], queryFn: () => api<HealthSummary>("/analytics/health/summary"), staleTime: 300000, enabled: canAnalytics });
  const h = q.data;
  return (
    <section className="grid gap-5 md:grid-cols-3">
      {canAnalytics && (
        <Panel title="Marketing health" sub={h?.grade ?? "Overall score across channels, content and website"}>
          <div className="flex items-center gap-4">
            <Gauge value={h?.score ?? null} label="Marketing health" sub="out of 100" size={160}
              detail={h?.weakest?.map((w) => <TipRow key={w.label} label={w.label} value={w.score} />)} />
            <div className="min-w-0 text-sm">
              {h?.weakest[0] && <p className="text-muted">Weakest: <span className="text-ink">{h.weakest[0].label} ({h.weakest[0].score})</span></p>}
              {h?.top_tip && <p className="mt-1">{h.top_tip}</p>}
              <Link className="mt-1 inline-block text-primary-700 hover:underline" to="/analytics?tab=health">How to improve</Link>
            </div>
          </div>
        </Panel>
      )}
      {canPresence && (
        <Panel title="Social presence" sub={d.social_presence?.grade ?? "How complete and active your profiles are"}>
          <div className="flex items-center gap-4">
            <Gauge value={d.social_presence?.score ?? null} label="Social presence" sub="out of 100" size={160}
              detail={d.social_presence?.change != null ? <TipRow label="Change since last check" value={`${d.social_presence.change > 0 ? "+" : ""}${d.social_presence.change}`} /> : undefined} />
            <div className="text-sm">
              {d.social_presence?.change != null && d.social_presence.change !== 0 && (
                <p className={`tabular-nums ${d.social_presence.change > 0 ? "text-emerald-700" : "text-danger"}`}>{d.social_presence.change > 0 ? "▲" : "▼"} {Math.abs(d.social_presence.change)} points since the previous check</p>
              )}
              <Link className="text-primary-700 hover:underline" to="/presence">{d.social_presence ? "See suggestions" : "Check your social profiles"}</Link>
            </div>
          </div>
        </Panel>
      )}
      <Panel title="Digital presence" sub="Website, search and listings audit">
        <div className="flex items-center gap-4">
          <Gauge value={d.audit_score} label="Digital presence" sub="out of 100" size={160} />
          <div className="text-sm">
            <p className="text-muted">Time zone: <span className="text-ink">{d.organization?.timezone}</span></p>
            <Link className="text-primary-700 hover:underline" to="/intelligence">{d.audit_score !== null ? "See recommendations" : "Run an audit"}</Link>
          </div>
        </div>
      </Panel>
    </section>
  );
}

export default function Dashboard() {
  const { session, can } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dash>("/dashboard"), refetchInterval: 60000 });
  const qc = useQueryClient();
  const dismiss = useMutation({
    mutationFn: () => api("/dashboard/alerts/dismiss", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dashboard"] }),
  });
  const org = session?.organization;

  if (!org) {
    return (
      <div>
        <h1 className="text-[28px] font-bold">No organization selected</h1>
        <p className="mt-1 text-muted">
          Open <Link className="text-primary-700 underline" to="/platform/organizations">All organizations</Link> to create one or switch into it.
        </p>
      </div>
    );
  }

  const d = q.data;
  const now = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: org.timezone }).format(new Date());
  const doneCount = d?.setup.filter((s) => s.done).length ?? 0;
  const allDone = d && doneCount === d.setup.length;

  return (
    <div>
      <p className="text-muted">{now}</p>
      <h1 className="mt-1 text-[34px] font-bold leading-tight">Hello, {session.user.full_name.split(" ")[0]}</h1>
      {q.error && <div className="mt-4"><Alert>{(q.error as Error).message}</Alert></div>}

      {d && (
        <>
          {(d.counts.failed > 0 || d.failing_jobs > 0 || d.accounts.some((a) => a.status === "expired")) && (
            <div className="mt-6 flex items-start gap-3 rounded-xl border border-danger/20 bg-danger-tint px-4 py-3 text-sm text-danger">
              <AlertTriangle size={18} className="mt-0.5 shrink-0" />
              <div>
                {d.counts.failed > 0 && <div>{d.counts.failed} post(s) failed to publish in the last two weeks. <Link className="underline" to="/posts?status=failed">Review them</Link>.</div>}
                {d.accounts.filter((a) => a.status === "expired").map((a) => <div key={a.id}>{a.name} needs reconnecting. <Link className="underline" to="/settings/accounts">Reconnect</Link>.</div>)}
                {d.failing_jobs > 0 && <div>{d.failing_jobs} background task(s) failed recently. Details are in the notifications.</div>}
              </div>
              {(d.counts.failed > 0 || d.failing_jobs > 0) && (
                <button type="button" onClick={() => dismiss.mutate()} disabled={dismiss.isPending}
                  title="Hide these failures (new ones will show again). Accounts that need reconnecting keep showing."
                  className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium hover:bg-danger/10 disabled:opacity-50">
                  <X size={14} /> Dismiss
                </button>
              )}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <Pill label="waiting for approval" value={d.counts.in_review} to="/approvals" />
            <Pill label="scheduled in the next 7 days" value={d.counts.scheduled_7d} to="/calendar" />
            <Pill label="published in the last 7 days" value={d.counts.published_7d} to="/posts?status=published" />
            <Pill label="to post by hand" value={d.counts.manual_pending} to="/posts?status=manual_pending" tone="warn" />
            <Pill label="connected accounts" value={d.accounts.filter((a) => a.status === "connected" || a.status === "manual").length} to="/settings/accounts" />
          </div>

          {!allDone && <div className="mt-8 max-w-4xl">
                <div>
                  <div className="flex items-baseline justify-between">
                    <h2 className="text-xl font-semibold">Get {org.name} ready to publish</h2>
                    <span className="text-sm text-muted">{doneCount} of {d.setup.length} done</span>
                  </div>
                  <div className="mt-2 h-1.5 rounded-full bg-line">
                    <div className="h-full rounded-full bg-gradient-to-r from-grad1 to-grad2 transition-all" style={{ width: `${(doneCount / d.setup.length) * 100}%` }} />
                  </div>
                  <ol className="mt-4 divide-y divide-line border-y border-line">
                    {d.setup.map((s, i) => {
                      const badge = (
                        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-sm font-semibold ${s.done ? "bg-gradient-to-br from-grad1 to-grad2 text-white" : "border border-primary-300 text-primary-700"}`}>
                          {s.done ? <Check size={15} /> : i + 1}
                        </span>
                      );
                      return (
                        <li key={s.key}>
                          {s.done ? (
                            <div className="flex gap-4 py-3.5">
                              {badge}
                              <div>
                                <span className="font-medium text-muted">{s.title}</span>
                                <p className="text-sm text-muted">{s.body}</p>
                              </div>
                            </div>
                          ) : (
                            <Link to={s.to} className="group -mx-3 flex items-center gap-4 rounded-lg px-3 py-3.5 transition-colors hover:bg-primary-50">
                              {badge}
                              <div className="min-w-0 flex-1">
                                <span className="font-medium text-ink group-hover:text-primary-700">{s.title}</span>
                                <p className="text-sm text-muted">{s.body}</p>
                              </div>
                              <span className="flex shrink-0 items-center gap-1 text-sm font-medium text-primary-700">
                                Start <ChevronRight size={16} className="transition-transform group-hover:translate-x-0.5" />
                              </span>
                            </Link>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </div>
          </div>}

          <div className="mt-8"><DashboardInsights /></div>

          <div className="mt-5">
            <Panel title="Your marketing system" sub="Every AI agent, the services it relies on and each connected channel, with live status. Updates every 15 seconds."
              action={<Link to="/system" className="text-sm text-primary-700 hover:underline">Open full screen</Link>}>
              <SystemMap height={620} />
            </Panel>
          </div>

          <div className="mt-5"><ScoresRow d={d} canAnalytics={can("analytics.view")} canPresence={can("presence.view")} /></div>
        </>
      )}
    </div>
  );
}
