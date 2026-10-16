import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Globe, Sparkles } from "lucide-react";
import { api } from "../../api/client";
import type { Job } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Stat, formatDate } from "../../components/ui";
import { ErrorText, JobStatus, SetupHint, useIntelJobs } from "./shared";
import { IMPACT_TONE, SOURCE_LABEL, hostOf, scoreTone, type IntelTab, type JobStart, type Overview } from "./types";

const STEPS = [
  "Read every page of your website",
  "Build the company profile: products, studios, services, use cases, clients",
  "Audit your digital presence",
  "Find competitors that sell to the same buyers",
  "Suggest topics for posts and articles",
];

function runSummary(j: Job): string {
  const steps = (j.result?.steps ?? {}) as Record<string, { ok: boolean; error?: string }>;
  const failed = Object.entries(steps).filter(([, v]) => !v.ok);
  if (!failed.length) return "Analysis finished. Review the company profile and the suggested competitors next.";
  return `Analysis finished with ${failed.length} step${failed.length > 1 ? "s" : ""} skipped: ${failed.map(([, v]) => v.error).join(" ")}`;
}

export default function OverviewTab({ data, goTo }: { data: Overview; goTo: (t: IntelTab) => void }) {
  const { can } = useAuth();
  const canManage = can("intel.manage");
  const { track } = useIntelJobs();
  const website = data.website_source?.url ?? data.organization_website;
  const run = useMutation({
    mutationFn: () => api<JobStart>("/intel/run-all", { method: "POST", json: {} }),
    onSuccess: (r) => track("run_all", r.job_id),
  });
  const p = data.profile;
  const c = data.counts;

  return (
    <div className="space-y-8">
      <section className="ai-mesh-dark relative overflow-hidden rounded-2xl p-6 text-white sm:p-8">
        <div className="relative grid gap-6 lg:grid-cols-[1fr_auto] lg:items-end">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 text-sm text-cyan-200">
              <Globe size={15} />
              {website ? hostOf(website) : "No website yet"}
              {data.website_source?.last_crawled_at && <span className="text-slate-300">· read {formatDate(data.website_source.last_crawled_at)}</span>}
            </div>
            <h2 className="mt-2 text-2xl font-bold leading-tight">Know your company before you post</h2>
            <ol className="mt-3 space-y-1 text-sm text-slate-200">
              {STEPS.map((s, i) => <li key={s}><span className="mr-2 tabular-nums text-cyan-300">{i + 1}.</span>{s}</li>)}
            </ol>
          </div>
          {canManage && (
            <div className="flex flex-col items-start gap-2 lg:items-end">
              <Button onClick={() => run.mutate()} disabled={run.isPending || !website} className="h-10 px-5">
                <Sparkles size={16} /> {data.profile ? "Analyze everything again" : "Analyze everything"}
              </Button>
              <span className="text-xs text-slate-300">Takes a few minutes. You can leave this page.</span>
            </div>
          )}
        </div>
      </section>

      <div className="space-y-2">
        <ErrorText error={run.error} />
        <JobStatus jobKey="run_all" label="Analyzing your company" doneText={runSummary} />
        <SetupHint aiReady={data.ai_ready} hasWebsite={!!website} />
      </div>

      <section className="grid grid-cols-2 gap-6 rounded-xl border border-line bg-surface p-5 sm:grid-cols-5">
        <Stat label="Pages read" value={c.pages} hint={c.uploads ? `incl. ${c.uploads} documents` : undefined} />
        <Stat label="Facts found" value={c.entities_total} hint={c.entities_total ? `${c.entities_confirmed} confirmed` : undefined} />
        <Stat label="Competitors" value={c.competitors.confirmed ?? 0} hint={c.competitors.suggested ? `${c.competitors.suggested} to review` : undefined} />
        <Stat label="Presence score" value={<span className={scoreTone(data.latest_audit?.score)}>{data.latest_audit ? data.latest_audit.score : "—"}</span>} hint={data.latest_audit ? "out of 100" : undefined} />
        <Stat label="Topic ideas" value={c.ideas.new ?? 0} hint="ready to use" />
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">Company profile</h2>
            {p && (p.confirmed ? <Badge tone="brand">Confirmed</Badge> : <Badge tone="accent">Needs review</Badge>)}
          </div>
          {p?.summary ? (
            <>
              <p>{p.summary}</p>
              {p.positioning && <p className="mt-2 text-sm text-muted">{p.positioning}</p>}
              {!!p.industries.length && <p className="mt-3 text-sm"><span className="text-muted">Industries: </span>{p.industries.join(", ")}</p>}
              <Button variant="secondary" className="mt-4" onClick={() => goTo("profile")}>
                {p.confirmed ? "Open the profile" : "Review and confirm the profile"} <ArrowRight size={15} />
              </Button>
            </>
          ) : (
            <EmptyState
              title="No profile yet"
              body={website ? "Run “Analyze everything” to read your website and build the profile." : "Add your website first, then run the analysis."}
            />
          )}
        </Card>

        <Card>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">Digital presence</h2>
            {data.latest_audit && <span className="text-sm text-muted">{formatDate(data.latest_audit.created_at)}</span>}
          </div>
          {data.latest_audit ? (
            <>
              <div className="flex items-baseline gap-1">
                <span className={`text-4xl font-bold tabular-nums ${scoreTone(data.latest_audit.score)}`}>{data.latest_audit.score}</span>
                <span className="text-muted">/100</span>
              </div>
              <ul className="mt-3 space-y-2">
                {data.latest_audit.top_recommendations.map((r) => (
                  <li key={r.title} className="flex items-start gap-2 text-sm">
                    <Badge tone={IMPACT_TONE[r.impact]}>{r.impact}</Badge><span>{r.title}</span>
                  </li>
                ))}
              </ul>
              <Button variant="secondary" className="mt-4" onClick={() => goTo("audit")}>See the full audit <ArrowRight size={15} /></Button>
            </>
          ) : (
            <EmptyState title="No audit yet" body="The audit runs after your website has been read. It checks SEO basics, page speed, social profiles and content freshness." />
          )}
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Latest topic ideas</h2>
          {!!data.latest_ideas.length && <Button variant="ghost" onClick={() => goTo("ideas")}>All ideas <ArrowRight size={15} /></Button>}
        </div>
        {data.latest_ideas.length ? (
          <ul className="divide-y divide-line">
            {data.latest_ideas.map((i) => (
              <li key={i.id} className="flex flex-wrap items-start justify-between gap-2 py-2.5">
                <div className="min-w-0">
                  <div className="font-medium">{i.title}</div>
                  {i.angle && <div className="text-sm text-muted">{i.angle}</div>}
                </div>
                <div className="flex items-center gap-2">
                  <Badge>{SOURCE_LABEL[i.source] ?? i.source}</Badge>
                  <span className="text-sm tabular-nums text-muted">{Math.round(i.score * 100)}%</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No ideas yet" body="Ideas are generated from your profile, competitor content and Search Console queries once the profile exists." />
        )}
      </Card>
    </div>
  );
}
