import type { ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, ClipboardCheck, Minus, X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Spinner, formatDate } from "../../components/ui";
import { ErrorText, JobStatus, ScoreBar, SectionTitle, useIntelJobs } from "./shared";
import { IMPACT_TONE, scoreTone, type Audit, type AuditSummary, type JobStart, type Overview } from "./types";

const SECTION_LABELS: Record<string, string> = {
  technical: "Website SEO basics", performance: "Mobile page speed", social: "Social presence", content: "Content freshness",
};

function Check3({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2 py-1 text-sm">
      {ok === null ? <Minus size={15} className="mt-0.5 text-muted" /> : ok ? <Check size={15} className="mt-0.5 text-emerald-700" /> : <X size={15} className="mt-0.5 text-danger" />}
      <span>{children}</span>
    </li>
  );
}

function num(v: unknown): number {
  return typeof v === "number" ? v : 0;
}

function Technical({ t }: { t: NonNullable<Audit["sections"]["technical"]> }) {
  const pages = num(t.pages);
  return (
    <ul>
      <Check3 ok={!!t.https}>{t.https ? "Served over HTTPS" : "Not served over HTTPS"}</Check3>
      <Check3 ok={!!t.sitemap}>{t.sitemap ? "sitemap.xml found" : "No sitemap.xml"}</Check3>
      <Check3 ok={!!t.robots_txt}>{t.robots_txt ? "robots.txt found" : "No robots.txt"}</Check3>
      <Check3 ok={!num(t.missing_title) && !num(t.duplicate_titles)}>
        Page titles: {num(t.missing_title)} missing, {num(t.title_length_issues)} outside 30–60 characters, {num(t.duplicate_titles)} duplicated
      </Check3>
      <Check3 ok={!num(t.missing_description)}>
        Meta descriptions: {num(t.missing_description)} missing, {num(t.description_length_issues)} outside 70–160 characters
      </Check3>
      <Check3 ok={!num(t.pages_without_h1) && !num(t.pages_with_multiple_h1)}>
        H1 headings: {num(t.pages_without_h1)} pages without one, {num(t.pages_with_multiple_h1)} with several
      </Check3>
      <Check3 ok={num(t.pages_without_og) === 0}>Open Graph tags missing on {num(t.pages_without_og)} of {pages} pages</Check3>
      <Check3 ok={!!t.organization_schema}>
        {t.organization_schema ? "Organization structured data found" : `No Organization structured data (${num(t.pages_with_jsonld)} pages have any JSON-LD)`}
      </Check3>
      <Check3 ok={num(t.images_missing_alt) === 0}>{num(t.images_missing_alt)} of {num(t.images_total)} images have no alt text</Check3>
      <Check3 ok={num(t.broken_link_count) === 0}>{num(t.broken_link_count)} broken internal links</Check3>
      {!!t.broken_links?.length && (
        <li className="ml-6 text-xs text-muted">
          {t.broken_links.slice(0, 5).map((b) => <div key={b.url}>{b.status} · {b.url}{b.from ? ` (linked from ${b.from})` : ""}</div>)}
        </li>
      )}
    </ul>
  );
}

function Performance({ p }: { p: NonNullable<Audit["sections"]["performance"]> }) {
  if (p.error) return <p className="text-sm text-muted">{p.error}</p>;
  const rows: [string, string][] = [
    ["Performance", p.performance != null ? `${p.performance}/100` : "—"],
    ["Accessibility", p.accessibility != null ? `${p.accessibility}/100` : "—"],
    ["Best practices", p.best_practices != null ? `${p.best_practices}/100` : "—"],
    ["SEO", p.seo != null ? `${p.seo}/100` : "—"],
    ["Largest contentful paint", p.lcp_ms != null ? `${(p.lcp_ms / 1000).toFixed(1)} s` : "—"],
    ["Total blocking time", p.tbt_ms != null ? `${Math.round(p.tbt_ms)} ms` : "—"],
    ["Cumulative layout shift", p.cls != null ? p.cls.toFixed(2) : "—"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="tabular-nums">{v}</dd></div>)}
      <p className="col-span-2 mt-2 text-xs text-muted">Google PageSpeed Insights, mobile, for {p.url}</p>
    </dl>
  );
}

function Social({ s }: { s: NonNullable<Audit["sections"]["social"]> }) {
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-xs text-muted"><th className="py-1 font-medium">Platform</th><th className="font-medium">Linked from website</th><th className="font-medium">Connected here</th></tr></thead>
      <tbody>
        {s.platforms.map((r) => (
          <tr key={r.platform} className="border-t border-line">
            <td className="py-1.5">{r.label}</td>
            <td>{r.linked_from_website ? <a className="text-primary-700 hover:underline" href={r.linked_from_website} target="_blank" rel="noreferrer">Yes</a> : <span className="text-muted">No</span>}</td>
            <td>{r.connected ? (r.accounts.some((a) => a.status !== "connected") ? <Badge tone="danger">Needs attention</Badge> : "Yes") : <span className="text-muted">No</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Content({ c }: { c: NonNullable<Audit["sections"]["content"]> }) {
  return (
    <ul>
      <Check3 ok={c.blog_pages > 0}>{c.blog_pages ? `${c.blog_pages} blog or news pages found` : "No blog, news or insights section found"}</Check3>
      <Check3 ok={c.latest_date ? c.dated_items_last_90_days > 0 : null}>
        {c.latest_date ? `Newest dated content: ${c.latest_date} (${c.dated_items_last_90_days} items in the last 90 days)` : "No publication dates could be detected"}
      </Check3>
    </ul>
  );
}

export default function AuditTab({ overview }: { overview: Overview }) {
  const { can } = useAuth();
  const { track } = useIntelJobs();
  const latest = useQuery({ queryKey: ["intel", "audit"], queryFn: () => api<{ audit: Audit | null }>("/intel/audits/latest") });
  const history = useQuery({ queryKey: ["intel", "audits"], queryFn: () => api<AuditSummary[]>("/intel/audits") });
  const run = useMutation({
    mutationFn: () => api<JobStart>("/intel/audits/run", { method: "POST" }),
    onSuccess: (r) => track("audit", r.job_id),
  });
  const a = latest.data?.audit;
  const crawled = overview.website_source?.status === "ready";
  const sections = a ? (["technical", "performance", "social", "content"] as const) : [];

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          Checks your website's SEO basics, mobile speed, social profiles and how fresh your content is, then lists what to fix first.
          {can("siteaudit.view") && <> For a page-by-page technical check (150+ checks, Core Web Vitals, AI crawler access), open the{" "}
            <Link to="/site-audit" className="text-primary-700 hover:underline">Site audit</Link>.</>}
        </p>
        {can("intel.manage") && (
          <Button onClick={() => run.mutate()} disabled={run.isPending || !crawled} title={crawled ? undefined : "Crawl your website first"}>
            <ClipboardCheck size={15} /> {a ? "Run the audit again" : "Run audit"}
          </Button>
        )}
      </div>
      <div className="space-y-2">
        <ErrorText error={run.error} />
        <JobStatus jobKey="audit" label="Auditing your digital presence" />
      </div>
      {latest.isLoading && <Spinner label="Loading…" />}
      <ErrorText error={latest.error} />
      {latest.data && !a && (
        <EmptyState
          title="No audit yet"
          body={crawled ? "Select “Run audit” to check your website and social profiles." : "Crawl your website on the Sources tab first; the audit uses what the crawl finds."}
        />
      )}
      {a && (
        <>
          <section className="grid gap-4 lg:grid-cols-[260px_1fr]">
            <Card className="flex flex-col justify-center">
              <div className="text-sm text-muted">Presence score</div>
              <div className="flex items-baseline gap-1">
                <span className={`text-5xl font-bold tabular-nums ${scoreTone(a.score)}`}>{a.score}</span><span className="text-muted">/100</span>
              </div>
              <div className="mt-1 text-xs text-muted">{formatDate(a.created_at)} · {a.pages_analyzed} pages</div>
            </Card>
            <Card>
              <div className="grid gap-4 sm:grid-cols-2">
                {sections.map((k) => {
                  const v = a.section_scores[k];
                  return (
                    <div key={k}>
                      <div className="flex items-baseline justify-between text-sm">
                        <span>{SECTION_LABELS[k]}</span>
                        <span className={`font-semibold tabular-nums ${scoreTone(v)}`}>{v ?? "—"}</span>
                      </div>
                      <div className="mt-1"><ScoreBar value={v ?? 0} label={SECTION_LABELS[k]} /></div>
                    </div>
                  );
                })}
              </div>
            </Card>
          </section>

          <section>
            <SectionTitle title="What to fix first" description="Ordered by impact." />
            {a.recommendations.length ? (
              <Card className="py-1">
                <ol className="divide-y divide-line">
                  {a.recommendations.map((r) => (
                    <li key={r.title} className="flex items-start gap-3 py-3">
                      <Badge tone={IMPACT_TONE[r.impact]}>{r.impact} impact</Badge>
                      <div>
                        <div className="font-medium">{r.title}</div>
                        <p className="text-sm text-muted">{r.detail}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </Card>
            ) : (
              <EmptyState title="Nothing to fix" body="No issues were found. Run the audit again after big website changes." />
            )}
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            {a.sections.technical && <Card><h3 className="mb-2 font-semibold">{SECTION_LABELS.technical}</h3><Technical t={a.sections.technical} /></Card>}
            {a.sections.performance && <Card><h3 className="mb-2 font-semibold">{SECTION_LABELS.performance}</h3><Performance p={a.sections.performance} /></Card>}
            {a.sections.social && <Card><h3 className="mb-2 font-semibold">{SECTION_LABELS.social}</h3><Social s={a.sections.social} /></Card>}
            {a.sections.content && <Card><h3 className="mb-2 font-semibold">{SECTION_LABELS.content}</h3><Content c={a.sections.content} /></Card>}
          </section>

          {(history.data?.length ?? 0) > 1 && (
            <section>
              <SectionTitle title="Earlier audits" />
              <Card className="py-1">
                <ul className="divide-y divide-line text-sm">
                  {history.data!.slice(1, 8).map((h) => (
                    <li key={h.id} className="flex justify-between py-2">
                      <span className="text-muted">{formatDate(h.created_at)}</span>
                      <span className={`font-semibold tabular-nums ${scoreTone(h.score)}`}>{h.score}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </section>
          )}
        </>
      )}
    </div>
  );
}
