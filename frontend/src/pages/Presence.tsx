import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, CircleHelp, ExternalLink, Play, RotateCcw, X as XIcon } from "lucide-react";
import { api } from "../api/client";
import { useJob } from "../api/hooks";
import { Alert, Badge, Button, Card, EmptyState, PageHeader, Select, Spinner, formatDate } from "../components/ui";
import { Meter, ScoreRing, TrendChart, shortDay } from "./analytics/vizkit";
import { Segmented } from "./analytics/growth";
import { fmtNum } from "./analytics/charts";

type Check = { id: string; group: string; label: string; status: "pass" | "fail" | "warn" | "unknown"; detail: string; weight: number };
type Profile = {
  key: string; platform: string; label: string; handle: string; url: string; display_name: string | null; avatar_url: string | null;
  has_avatar: boolean; bio: string | null; followers: number | null; posts_total: number | null; posts_30d: number | null;
  days_since_last_post: number | null; last_post_at: string | null; avg_engagement: number | null; engagement_rate: number | null;
  growth_pct?: number; growth_since?: string; formats: Record<string, number>;
  source: "connected" | "public" | "limited"; source_label: string; fields: string[]; notes: string[]; sources: string[];
  account: { id: string; platform: string; status: string } | null; linked_from_website: boolean | null; links_to_website: boolean | null;
  checks: Check[]; parts: Record<string, number | null>; score: number | null;
};
type Part = { key: string; label: string; weight: number; score: number | null; how: string; detail: string };
type Run = {
  id: string; created_at: string; trigger: string; score: number | null; grade: string; parts: Part[]; profiles: Profile[];
  coverage: { platform: string; label: string; weight: number; present: boolean }[];
  summary: { profiles: number; connected: number; public: number; limited: number; website: string | null; website_crawled: boolean;
    change: number | null; previous_score: number | null; resolved?: string[]; message?: string | null; unparsed_links?: string[] };
  narrative: string | null; narrative_source: "ai" | "rules" | null;
};
type Suggestion = {
  id: string; key: string; title: string; detail: string | null; priority: "high" | "medium" | "low"; category: string;
  platform: string | null; profile_key: string | null; action: { to?: string; href?: string; label: string } | null;
  source: "rules" | "ai"; status: "open" | "done"; reopened: boolean; status_changed_at: string | null;
};
type Data = {
  run: Run | null; suggestions: Suggestion[]; history: { id: string; at: string; score: number | null }[];
  running_job: { id: string; status: string; progress: string | null } | null;
  inputs: { organization_links: Record<string, string>; website: string | null; missing_org_links: string[]; optional_sources: Record<string, boolean> };
  can_manage: boolean;
};

const NAMES: Record<string, string> = {
  linkedin: "LinkedIn", facebook: "Facebook", instagram: "Instagram", x: "X (Twitter)", youtube: "YouTube", threads: "Threads",
  pinterest: "Pinterest", github: "GitHub", bluesky: "Bluesky", mastodon: "Mastodon", tiktok: "TikTok", medium: "Medium", other: "Other",
};
const BADGE: Record<string, { code: string; bg: string }> = {
  linkedin: { code: "in", bg: "#0A66C2" }, facebook: { code: "f", bg: "#1877F2" }, instagram: { code: "IG", bg: "#D62976" },
  x: { code: "X", bg: "#111827" }, youtube: { code: "YT", bg: "#FF0000" }, threads: { code: "@", bg: "#111827" },
  pinterest: { code: "P", bg: "#E60023" }, github: { code: "GH", bg: "#24292F" }, bluesky: { code: "B", bg: "#1185FE" },
  mastodon: { code: "M", bg: "#6364FF" }, tiktok: { code: "TT", bg: "#111827" }, medium: { code: "Me", bg: "#111827" },
};
const SOURCE: Record<Profile["source"], { label: string; tone: "brand" | "accent" | "neutral" }> = {
  connected: { label: "Connected", tone: "brand" }, public: { label: "Public data", tone: "accent" }, limited: { label: "Limited", tone: "neutral" },
};
const PRIORITY_TONE = { high: "danger", medium: "accent", low: "neutral" } as const;

function Avatar({ p }: { p: Profile }) {
  const [broken, setBroken] = useState(false);
  const b = BADGE[p.platform] ?? { code: p.platform.slice(0, 2).toUpperCase(), bg: "#64748B" };
  return (
    <span className="relative shrink-0">
      {p.avatar_url && !broken ? (
        <img src={p.avatar_url} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} className="h-11 w-11 rounded-full border border-line object-cover" />
      ) : (
        <span aria-hidden className="grid h-11 w-11 place-items-center rounded-full text-sm font-bold text-white" style={{ background: b.bg }}>{b.code}</span>
      )}
      <span aria-hidden className="absolute -bottom-1 -right-1 grid h-5 w-5 place-items-center rounded text-[9px] font-bold text-white ring-2 ring-white" style={{ background: b.bg }}>{b.code}</span>
    </span>
  );
}

function CheckIcon({ s }: { s: Check["status"] }) {
  if (s === "pass") return <Check size={15} className="mt-0.5 shrink-0 text-emerald-600" aria-label="Passed" />;
  if (s === "fail") return <XIcon size={15} className="mt-0.5 shrink-0 text-danger" aria-label="Needs work" />;
  if (s === "warn") return <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600" aria-label="Could be better" />;
  return <CircleHelp size={15} className="mt-0.5 shrink-0 text-muted" aria-label="Couldn't check" />;
}

function lastPost(days: number | null) {
  if (days == null) return "—";
  return days === 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

function ProfileCard({ p }: { p: Profile }) {
  const src = SOURCE[p.source];
  const fails = p.checks.filter((c) => c.status === "fail" || c.status === "warn").length;
  return (
    <Card className="flex min-w-0 flex-col">
      <div className="flex items-start gap-3">
        <Avatar p={p} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-ink">{p.label}</span>
            <Badge tone={src.tone}>{src.label}</Badge>
          </div>
          <a href={p.url} target="_blank" rel="noreferrer" className="inline-flex max-w-full items-center gap-1 text-sm text-primary-700 hover:underline [overflow-wrap:anywhere]">
            {p.handle} <ExternalLink size={12} className="shrink-0" />
          </a>
          {p.display_name && <div className="text-xs text-muted [overflow-wrap:anywhere]">{p.display_name}</div>}
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums text-ink">{p.score ?? "—"}</div>
          <div className="text-[11px] text-muted">{p.score == null ? "not enough data" : "out of 100"}</div>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-sm">
        <div className="rounded-lg bg-slate-50 p-2"><dt className="text-[11px] text-muted">Followers</dt><dd className="font-semibold tabular-nums">{p.followers != null ? fmtNum(p.followers) : "—"}</dd></div>
        <div className="rounded-lg bg-slate-50 p-2"><dt className="text-[11px] text-muted">Posts (30 days)</dt><dd className="font-semibold tabular-nums">{p.posts_30d ?? "—"}</dd></div>
        <div className="rounded-lg bg-slate-50 p-2"><dt className="text-[11px] text-muted">Last post</dt><dd className="font-semibold">{lastPost(p.days_since_last_post)}</dd></div>
      </dl>
      {p.growth_pct != null && (
        <div className={`mt-2 text-xs tabular-nums ${p.growth_pct >= 0 ? "text-emerald-700" : "text-danger"}`}>
          {p.growth_pct >= 0 ? "▲" : "▼"} {Math.abs(p.growth_pct)}% followers since {p.growth_since}
        </div>
      )}
      <div className="mt-2 text-xs text-muted [overflow-wrap:anywhere]">Data: {p.source_label}{p.fields.length ? ` · ${p.fields.slice(0, 6).join(", ")}` : ""}</div>
      {p.notes.filter((n) => n.startsWith("Limited data")).map((n) => (
        <div key={n} className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 [overflow-wrap:anywhere]">{n}</div>
      ))}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer select-none font-medium text-primary-700">
          Checklist{fails ? ` · ${fails} to improve` : " · all good"}
        </summary>
        <ul className="mt-2 space-y-1.5">
          {p.checks.map((c) => (
            <li key={c.id} className="flex gap-2">
              <CheckIcon s={c.status} />
              <div className="min-w-0"><span className="font-medium text-ink">{c.label}.</span> <span className="text-muted [overflow-wrap:anywhere]">{c.detail}</span></div>
            </li>
          ))}
        </ul>
        {p.notes.filter((n) => !n.startsWith("Limited data")).length > 0 && (
          <div className="mt-3 space-y-1 text-xs text-muted">
            <div className="font-medium text-ink">What couldn't be read</div>
            {p.notes.filter((n) => !n.startsWith("Limited data")).map((n) => <div key={n} className="[overflow-wrap:anywhere]">{n}</div>)}
          </div>
        )}
      </details>
    </Card>
  );
}

function SuggestionRow({ s, canManage, profiles }: { s: Suggestion; canManage: boolean; profiles: Record<string, Profile> }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (status: Suggestion["status"]) => api(`/presence/suggestions/${s.id}`, { method: "PATCH", json: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["presence"] }),
  });
  const prof = s.profile_key ? profiles[s.profile_key] : undefined;
  const done = s.status === "done";
  return (
    <li className={`rounded-xl border border-line bg-surface p-4 ${done ? "opacity-70" : ""}`}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <h3 className={`font-semibold text-ink [overflow-wrap:anywhere] ${done ? "line-through decoration-slate-400" : ""}`}>{s.title}</h3>
          {s.detail && <p className="mt-1 text-sm text-muted [overflow-wrap:anywhere]">{s.detail}</p>}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={PRIORITY_TONE[s.priority]}>{s.priority} priority</Badge>
          {(prof || s.platform) && <Badge>{prof?.label ?? NAMES[s.platform ?? ""] ?? s.platform}</Badge>}
          {s.source === "ai" && <Badge tone="accent">AI tip</Badge>}
        </div>
      </div>
      {s.reopened && !done && <p className="mt-2 text-xs text-amber-800">Marked done before, but the latest check still finds this.</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {s.action?.to && <Link to={s.action.to} className="text-sm font-medium text-primary-700 hover:underline">{s.action.label} →</Link>}
        {s.action?.href && (
          <a href={s.action.href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm font-medium text-primary-700 hover:underline">
            {s.action.label} <ExternalLink size={13} />
          </a>
        )}
        <span className="flex-1" />
        {canManage && (done ? (
          <Button variant="ghost" onClick={() => toggle.mutate("open")} disabled={toggle.isPending}><RotateCcw size={14} /> Not done</Button>
        ) : (
          <Button variant="secondary" onClick={() => toggle.mutate("done")} disabled={toggle.isPending}><Check size={15} /> Done</Button>
        ))}
        {done && s.status_changed_at && <span className="text-xs text-muted">Done {formatDate(s.status_changed_at)}</span>}
      </div>
      {toggle.error && <div className="mt-2"><Alert>{(toggle.error as Error).message}</Alert></div>}
    </li>
  );
}

export default function Presence() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["presence"], queryFn: () => api<Data>("/presence") });
  const [job, setJob] = useState<string | null>(null);
  const [filter, setFilter] = useState<"open" | "done">("open");
  const [platform, setPlatform] = useState("");
  const run = useMutation({ mutationFn: () => api<{ job_id: string }>("/presence/run", { method: "POST" }), onSuccess: (r) => setJob(r.job_id) });
  const jobId = job ?? q.data?.running_job?.id ?? null;
  const running = useJob(jobId, () => { setJob(null); qc.invalidateQueries({ queryKey: ["presence"] }); });
  const busy = (!!jobId && running?.status !== "done" && running?.status !== "failed") || run.isPending;

  const d = q.data;
  const r = d?.run;
  const byKey = Object.fromEntries((r?.profiles ?? []).map((p) => [p.key, p]));
  const sugs = (d?.suggestions ?? []).filter((s) => s.status === filter && (!platform || s.platform === platform));
  const counts = { open: 0, done: 0 };
  d?.suggestions.forEach((s) => { counts[s.status] += 1; });
  const platforms = Array.from(new Set((d?.suggestions ?? []).map((s) => s.platform).filter((x): x is string => !!x)));
  const missing = d?.inputs.missing_org_links ?? [];

  return (
    <>
      <PageHeader title="Social presence"
        description="Every social profile in your organization profile, website and connected accounts, checked and scored, with what to fix first."
        action={d?.can_manage && (
          <Button onClick={() => run.mutate()} disabled={busy}><Play size={15} className={busy ? "animate-pulse" : ""} /> {busy ? "Checking…" : "Run check now"}</Button>
        )} />
      {busy && <div className="mb-4"><Spinner label={running?.progress ?? "Waiting for the background worker…"} /></div>}
      {running?.status === "failed" && <div className="mb-4"><Alert>{running.error ?? "The check failed. Try again."}</Alert></div>}
      {run.error && <div className="mb-4"><Alert>{(run.error as Error).message}</Alert></div>}
      {q.isLoading && <Spinner label="Loading" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}

      {d && missing.length > 0 && (
        <div className="mb-5">
          <Alert tone="info">
            Not in your organization profile yet: {missing.map((k) => NAMES[k] ?? k).join(", ")}. If you have these pages, add them so they're checked too.{" "}
            <Link className="font-medium underline" to="/settings/organization">Add them in Settings → Organization</Link>
          </Alert>
        </div>
      )}

      {d && !r && !busy && (
        <EmptyState title="No check yet"
          body="The check reads your profiles' public details and connected accounts, scores them and suggests what to improve. It runs every week; you can also start it now."
          action={d.can_manage ? <Button onClick={() => run.mutate()}><Play size={15} /> Run the first check</Button> : undefined} />
      )}

      {r && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <Card>
              <div className="flex flex-wrap items-center gap-6">
                <ScoreRing score={r.score} size={148} />
                <div className="min-w-[12rem] flex-1">
                  <div className="text-sm text-muted">Social presence</div>
                  <div className="text-2xl font-bold text-ink">{r.grade}</div>
                  {r.summary.change != null && (
                    <div className={`text-sm tabular-nums ${r.summary.change > 0 ? "text-emerald-700" : r.summary.change < 0 ? "text-danger" : "text-muted"}`}>
                      {r.summary.change > 0 ? "▲" : r.summary.change < 0 ? "▼" : "•"} {Math.abs(r.summary.change)} points since the previous check
                    </div>
                  )}
                  <div className="mt-1 text-xs text-muted">
                    Checked {formatDate(r.created_at)} · {r.summary.profiles} profiles: {r.summary.connected} connected, {r.summary.public} public data, {r.summary.limited} limited
                  </div>
                </div>
              </div>
              {r.narrative && (
                <div className="mt-4 rounded-xl bg-slate-50 p-4 text-sm leading-relaxed text-ink [overflow-wrap:anywhere]">
                  {r.narrative}
                  <div className="mt-2 text-xs text-muted">{r.narrative_source === "ai" ? "Written by AI from the numbers above." : "Summary from built-in rules."}{r.summary.message ? ` ${r.summary.message}` : ""}</div>
                </div>
              )}
              {!!r.summary.resolved?.length && (
                <div className="mt-4 text-sm">
                  <div className="font-medium text-emerald-800">Fixed since the previous check</div>
                  <ul className="mt-1 list-inside list-disc text-muted">{r.summary.resolved.map((t) => <li key={t} className="[overflow-wrap:anywhere]">{t}</li>)}</ul>
                </div>
              )}
            </Card>
            <Card>
              <h2 className="mb-3 text-sm font-semibold">How the score is made</h2>
              <ul className="space-y-3.5">
                {r.parts.map((p) => (
                  <li key={p.key}>
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="font-medium text-ink">{p.label} <span className="font-normal text-muted">· weight {p.weight}</span></span>
                      <span className="font-semibold tabular-nums">{p.score ?? "—"}</span>
                    </div>
                    <div className="mt-1"><Meter value={p.score} label={`${p.label} score`} /></div>
                    <div className="mt-1 text-xs text-muted [overflow-wrap:anywhere]"><span className="text-ink">{p.detail}</span> {p.how}</div>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted">Parts without data are left out and the rest are scaled up. Limited data never lowers your score.</p>
            </Card>
          </div>

          <section>
            <h2 className="mb-3 text-lg font-semibold">Your profiles</h2>
            {r.profiles.length === 0 ? (
              <EmptyState title="No profiles found" body="Add your social profile links in Settings → Organization, or connect accounts in Settings → Social accounts."
                action={<Link to="/settings/organization" className="text-sm font-medium text-primary-700 underline">Add profile links</Link>} />
            ) : (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">{r.profiles.map((p) => <ProfileCard key={p.key} p={p} />)}</div>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
              {r.coverage.map((c) => (
                <span key={c.platform} className={`rounded-full border px-2.5 py-1 ${c.present ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-line"}`}>
                  {c.present ? "✓" : "✗"} {c.label}
                </span>
              ))}
            </div>
          </section>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
            <section className="min-w-0 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">What to do next</h2>
                <div className="flex flex-wrap items-center gap-2">
                  {platforms.length > 1 && (
                    <Select aria-label="Platform" value={platform} onChange={(e) => setPlatform(e.target.value)}>
                      <option value="">All platforms</option>
                      {platforms.map((p) => <option key={p} value={p}>{NAMES[p] ?? p}</option>)}
                    </Select>
                  )}
                  <Segmented label="Show suggestions" value={filter} onChange={setFilter}
                    items={[{ id: "open", label: `To do (${counts.open})` }, { id: "done", label: `Done (${counts.done})` }]} />
                </div>
              </div>
              {sugs.length === 0 ? (
                <EmptyState title={filter === "open" ? "Nothing to do here" : "Nothing marked done yet"} />
              ) : (
                <ul className="space-y-3">{sugs.map((s) => <SuggestionRow key={s.id} s={s} canManage={d.can_manage} profiles={byKey} />)}</ul>
              )}
            </section>
            <aside className="min-w-0 space-y-4">
              {d.history.filter((h) => h.score != null).length > 1 ? (
                <Card>
                  <TrendChart title="Score over time" labels={d.history.map((h) => shortDay(h.at.slice(0, 10)))}
                    series={[{ name: "Score", values: d.history.map((h) => h.score) }]} height={200} />
                </Card>
              ) : (
                <Card><div className="text-sm text-muted">The score trend appears after the next check. Checks run every week.</div></Card>
              )}
              <Card>
                <h2 className="text-sm font-semibold">Where the data comes from</h2>
                <ul className="mt-2 space-y-1.5 text-sm text-muted">
                  <li><Badge tone="brand">Connected</Badge> accounts you connected in Settings → Social accounts: the richest data.</li>
                  <li><Badge tone="accent">Public data</Badge> free public APIs (YouTube, Bluesky, Mastodon, GitHub, Instagram and Facebook lookups through your connected accounts).</li>
                  <li><Badge>Limited</Badge> only the public page's title, description and picture could be read.</li>
                </ul>
                <p className="mt-2 text-xs text-muted">
                  Optional sources: YouTube Data API key {d.inputs.optional_sources.youtube_data ? "added" : "not added (free)"} · X API token {d.inputs.optional_sources.x_api ? "added" : "not added (paid, optional)"}.{" "}
                  <Link to="/settings/integrations" className="underline">Integrations</Link>
                </p>
              </Card>
            </aside>
          </div>
        </div>
      )}
    </>
  );
}
