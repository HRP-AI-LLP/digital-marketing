import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Card, StatusBadge, formatDate } from "../components/ui";

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

// Platform badge: brand colour plus a short code (no logos), so each account's network is clear at a glance.
const PLATFORM_BADGE: Record<string, { code: string; bg: string }> = {
  facebook_page: { code: "f", bg: "#1877F2" }, instagram: { code: "IG", bg: "#D62976" }, threads: { code: "@", bg: "#111827" },
  linkedin_person: { code: "in", bg: "#0A66C2" }, linkedin_org: { code: "in", bg: "#0A66C2" }, youtube: { code: "YT", bg: "#FF0000" },
  gbp: { code: "G", bg: "#34A853" }, google: { code: "G", bg: "#4285F4" }, pinterest: { code: "P", bg: "#E60023" },
  bluesky: { code: "B", bg: "#1185FE" }, mastodon: { code: "M", bg: "#6364FF" }, x: { code: "X", bg: "#111827" }, tiktok: { code: "TT", bg: "#010101" },
};

function PlatformBadge({ platform }: { platform: string }) {
  const b = PLATFORM_BADGE[platform] ?? { code: platform.slice(0, 2).toUpperCase(), bg: "#64748B" };
  return (
    <span aria-hidden className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-[11px] font-bold text-white" style={{ background: b.bg }}>
      {b.code}
    </span>
  );
}

function Tile({ label, value, to, tone }: { label: string; value: number; to: string; tone?: "warn" }) {
  return (
    <Link to={to} className="rounded-xl border border-line bg-surface p-4 transition-colors hover:border-primary-300">
      <div className="text-sm text-muted">{label}</div>
      <div className={`mt-1 text-3xl font-bold tabular-nums ${tone === "warn" && value > 0 ? "text-danger" : ""}`}>{value}</div>
    </Link>
  );
}

type HealthSummary = { score: number | null; grade: string; weakest: { label: string; score: number }[]; top_tip: string | null };

function HealthCard() {
  const q = useQuery({ queryKey: ["analytics-health-summary"], queryFn: () => api<HealthSummary>("/analytics/health/summary"), staleTime: 300000 });
  const h = q.data;
  return (
    <Card>
      <div className="text-sm text-muted">Marketing health</div>
      <div className="mt-1 text-3xl font-bold tabular-nums">{h?.score ?? "—"}<span className="text-base font-normal text-muted">{h?.score != null ? " / 100" : ""}</span></div>
      {h && <div className="text-sm font-medium text-primary-800">{h.grade}</div>}
      {h?.weakest[0] && <p className="mt-1 text-sm text-muted">Weakest area: {h.weakest[0].label} ({h.weakest[0].score})</p>}
      {h?.top_tip && <p className="mt-1 text-sm">Next step: {h.top_tip}</p>}
      <Link className="text-sm text-primary-700 hover:underline" to="/analytics?tab=health">How to improve</Link>
    </Card>
  );
}

export default function Dashboard() {
  const { session, can } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dash>("/dashboard"), refetchInterval: 60000 });
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
            </div>
          )}

          <section className="mt-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile label="Waiting for approval" value={d.counts.in_review} to="/approvals" />
            <Tile label="Scheduled, next 7 days" value={d.counts.scheduled_7d} to="/calendar" />
            <Tile label="Published, last 7 days" value={d.counts.published_7d} to="/posts?status=published" />
            <Tile label="To post by hand" value={d.counts.manual_pending} to="/posts?status=manual_pending" tone="warn" />
          </section>

          <section className="mt-10 grid gap-10 lg:grid-cols-[1fr_320px]">
            <div>
              {!allDone && (
                <div className="mb-10">
                  <div className="flex items-baseline justify-between">
                    <h2 className="text-xl font-semibold">Get {org.name} ready to publish</h2>
                    <span className="text-sm text-muted">{doneCount} of {d.setup.length} done</span>
                  </div>
                  <div className="mt-2 h-1.5 rounded-full bg-line">
                    <div className="h-full rounded-full bg-gradient-to-r from-blue-700 to-cyan-500 transition-all" style={{ width: `${(doneCount / d.setup.length) * 100}%` }} />
                  </div>
                  <ol className="mt-4 divide-y divide-line border-y border-line">
                    {d.setup.map((s, i) => {
                      const badge = (
                        <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-sm font-semibold ${s.done ? "bg-gradient-to-br from-blue-700 to-cyan-500 text-white" : "border border-primary-300 text-primary-700"}`}>
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
              )}

              <div className="flex items-baseline justify-between">
                <h2 className="text-xl font-semibold">Coming up</h2>
                <Link to="/calendar" className="text-sm text-primary-700 hover:underline">Open calendar</Link>
              </div>
              {d.upcoming.length ? (
                <ul className="mt-3 divide-y divide-line rounded-xl border border-line bg-surface">
                  {d.upcoming.map((p) => (
                    <li key={p.id}>
                      <Link to={`/posts/${p.id}`} className="flex gap-4 px-4 py-3 hover:bg-primary-50/60">
                        <div className="w-28 shrink-0 text-sm tabular-nums text-muted">{formatDate(p.scheduled_at, org.timezone)}</div>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-medium">{p.account ?? p.platform}</span>
                            <StatusBadge status={p.status} />
                            <Badge>{p.post_type}</Badge>
                          </div>
                          <p className="truncate text-sm text-muted">{p.text || p.topic || "Draft will be written before posting time."}</p>
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-sm text-muted">Nothing scheduled yet. Create a posting schedule, or write a post from the calendar.</p>
              )}
            </div>

            <aside className="space-y-6">
              <Card>
                <div className="text-sm text-muted">Connected accounts</div>
                {d.accounts.length ? (
                  <ul className="mt-3 space-y-2.5 text-sm">
                    {d.accounts.map((a) => (
                      <li key={a.id} className="flex items-center gap-2.5">
                        <PlatformBadge platform={a.platform} />
                        <div className="min-w-0 flex-1 leading-tight">
                          <div className="truncate font-medium">{a.name}</div>
                          <div className="truncate text-xs text-muted">{a.platform_label}</div>
                        </div>
                        <StatusBadge status={a.status} />
                      </li>
                    ))}
                  </ul>
                ) : <p className="mt-1 text-sm">None yet. <Link className="text-primary-700 underline" to="/settings/accounts">Connect one</Link>.</p>}
              </Card>
              {can("analytics.view") && <HealthCard />}
              {can("presence.view") && (
                <Card>
                  <div className="text-sm text-muted">Social presence score</div>
                  <div className="mt-1 text-3xl font-bold tabular-nums">{d.social_presence?.score ?? "—"}<span className="text-base font-normal text-muted">{d.social_presence?.score != null ? " / 100" : ""}</span></div>
                  {d.social_presence?.change != null && d.social_presence.change !== 0 && (
                    <div className={`text-xs tabular-nums ${d.social_presence.change > 0 ? "text-emerald-700" : "text-danger"}`}>
                      {d.social_presence.change > 0 ? "▲" : "▼"} {Math.abs(d.social_presence.change)} points since the previous check
                    </div>
                  )}
                  <Link className="text-sm text-primary-700 hover:underline" to="/presence">{d.social_presence ? "See suggestions" : "Check your social profiles"}</Link>
                </Card>
              )}
              <Card>
                <div className="text-sm text-muted">Digital presence score</div>
                <div className="mt-1 text-3xl font-bold tabular-nums">{d.audit_score ?? "—"}<span className="text-base font-normal text-muted">{d.audit_score !== null ? " / 100" : ""}</span></div>
                <Link className="text-sm text-primary-700 hover:underline" to="/intelligence">{d.audit_score !== null ? "See recommendations" : "Run an audit"}</Link>
              </Card>
              <Card>
                <div className="text-sm text-muted">Time zone</div>
                <div className="font-medium">{org.timezone}</div>
              </Card>
            </aside>
          </section>
        </>
      )}
    </div>
  );
}
