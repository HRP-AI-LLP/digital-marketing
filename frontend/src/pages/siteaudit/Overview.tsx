import { useState } from "react";
import { Bot, CheckCircle2, CircleAlert, FileText, Gauge, ShieldAlert } from "lucide-react";
import { Card, formatDate } from "../../components/ui";
import { ScoreRing, TrendChart, shortDay } from "../analytics/vizkit";
import { Rating, SevBadge } from "./shared";
import { SEV_DOT, SEV_LABEL, fmtMs, scoreTone, type Overview, type RunFull, type Severity, type StrategySummary } from "./types";

type Go = (tab: "issues" | "pages", opts?: { severity?: Severity; category?: string; check?: string }) => void;

export default function OverviewTab({ o, run, go }: { o: Overview; run: RunFull; go: Go }) {
  const prev = run.stats.previous_score;
  const delta = prev != null && run.score != null ? run.score - prev : null;
  const sev: Severity[] = ["error", "warning", "notice"];
  const top = run.issues.filter((i) => i.count > 0).slice(0, 8);
  const trend = o.trend.filter((t) => t.score != null);

  return (
    <div className="space-y-6">
      <section className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card className="flex flex-col items-center text-center">
          <div className="text-sm font-medium text-muted">Site health</div>
          <ScoreRing score={run.score} size={150} />
          <div className="mt-1 text-sm">
            {delta == null ? <span className="text-muted">First audit — future audits show the change.</span> : (
              <span className={`tabular-nums ${delta > 0 ? "text-emerald-700" : delta < 0 ? "text-danger" : "text-muted"}`}>
                {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} {Math.abs(delta)} {Math.abs(delta) === 1 ? "point" : "points"} since the last audit
              </span>
            )}
          </div>
          <div className="mt-2 text-xs text-muted [overflow-wrap:anywhere]">
            {run.pages} URLs checked · {formatDate(run.finished_at, o.timezone)}
          </div>
        </Card>
        <Card>
          <div className="grid gap-3 sm:grid-cols-3">
            {sev.map((s) => {
              const n = s === "error" ? run.errors : s === "warning" ? run.warnings : run.notices;
              return (
                <button key={s} onClick={() => go("issues", { severity: s })}
                  className="rounded-xl border border-line p-4 text-left transition-colors hover:border-brand/50">
                  <div className="flex items-center gap-2 text-sm text-muted"><span className={`h-2.5 w-2.5 rounded-full ${SEV_DOT[s]}`} />{SEV_LABEL[s]}</div>
                  <div className="mt-1 text-3xl font-bold tabular-nums">{n}</div>
                  <div className="text-xs text-muted">
                    {s === "error" ? "Fix these first" : s === "warning" ? "Worth fixing soon" : "Good to know"}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {run.new_issues != null && run.stats.previous_run_id ? (
              <>
                <span><strong className="tabular-nums">{run.new_issues}</strong> <span className="text-muted">new issues since the last audit</span></span>
                <span><strong className="tabular-nums text-emerald-700">{run.fixed_issues}</strong> <span className="text-muted">fixed</span></span>
              </>
            ) : <span className="text-muted">New and fixed issues appear after the next audit.</span>}
            <span><strong className="tabular-nums">{String(run.stats.indexable ?? "—")}</strong> <span className="text-muted">pages can appear in search</span></span>
          </div>
        </Card>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <TrendChart title="Health score over time" description="One point per finished audit."
            labels={trend.map((t) => shortDay((t.finished_at ?? t.created_at).slice(0, 10)))}
            series={[{ name: "Health score", values: trend.map((t) => t.score) }]}
            empty="The trend appears after the first audit." height={220} />
        </Card>
        <Card>
          <h3 className="mb-3 font-semibold">Top issues</h3>
          {top.length ? (
            <ul className="divide-y divide-line">
              {top.map((i) => (
                <li key={i.id}>
                  <button onClick={() => go("issues", { check: i.id })} className="flex w-full items-center gap-3 py-2 text-left hover:text-primary-700">
                    <SevBadge s={i.severity} />
                    <span className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">{i.title}</span>
                    <span className="tabular-nums text-sm font-semibold">{i.count}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">No issues found. Nice work.</p>}
        </Card>
      </section>

      <Vitals run={run} />

      <section className="grid gap-4 lg:grid-cols-2">
        <AiCard run={run} />
        <Card>
          <h3 className="mb-3 font-semibold">Issues by area</h3>
          <ul className="space-y-2">
            {run.categories.filter((c) => c.checks > 0).map((c) => {
              const total = c.error + c.warning + c.notice;
              return (
                <li key={c.id}>
                  <button onClick={() => go("issues", { category: c.id })} className="w-full text-left">
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="hover:text-primary-700">{c.label}</span>
                      <span className="text-xs tabular-nums text-muted">{c.error} errors · {c.warning} warnings · {c.notice} notices</span>
                    </div>
                    <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                      {(["error", "warning", "notice"] as Severity[]).map((s) => (
                        <div key={s} className={SEV_DOT[s]} style={{ width: `${(c[s] / Math.max(1, total)) * 100}%` }} />
                      ))}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>
      </section>

      <SiteFiles run={run} />
    </div>
  );
}

const METRICS: { key: "lcp_ms" | "inp_ms" | "cls" | "tbt_ms" | "fcp_ms"; label: string; hint: string }[] = [
  { key: "lcp_ms", label: "Largest Contentful Paint", hint: "When the main content appears. Good: under 2.5 s" },
  { key: "inp_ms", label: "Interaction to Next Paint", hint: "How fast the page reacts to taps (real users). Good: under 200 ms" },
  { key: "cls", label: "Cumulative Layout Shift", hint: "How much the layout jumps. Good: under 0.1" },
  { key: "tbt_ms", label: "Total Blocking Time", hint: "Time scripts block the page (lab). Good: under 200 ms" },
  { key: "fcp_ms", label: "First Contentful Paint", hint: "When anything first appears. Good: under 1.8 s" },
];

function Vitals({ run }: { run: RunFull }) {
  const [strategy, setStrategy] = useState<"mobile" | "desktop">("mobile");
  const perf = run.performance;
  const s: StrategySummary | undefined = perf.summary?.[strategy];
  const field = s?.field;
  const show = (k: string, v: number | null | undefined) => (v == null ? "—" : k === "cls" ? v.toFixed(2) : fmtMs(v));
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><Gauge size={17} /> Core Web Vitals</h3>
          <p className="text-sm text-muted">From Google PageSpeed Insights (free) for your most important pages{s?.measured ? ` · ${s.measured} measured` : ""}.</p>
        </div>
        <div role="tablist" aria-label="Device" className="flex rounded-lg border border-line bg-surface p-0.5">
          {(["mobile", "desktop"] as const).map((k) => (
            <button key={k} role="tab" aria-selected={strategy === k} onClick={() => setStrategy(k)}
              className={`rounded-md px-3 py-1 text-sm capitalize ${strategy === k ? "bg-primary-600 text-white" : "text-muted hover:text-ink"}`}>{k}</button>
          ))}
        </div>
      </div>
      {!perf.pages?.length ? (
        <p className="text-sm text-muted">{perf.error ?? "Page speed wasn't measured in this audit (turned off in settings)."}</p>
      ) : !s?.measured ? (
        <p className="text-sm text-muted">{s?.error ?? "No page speed results this time."}</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {METRICS.map((m) => {
              const fv = field?.[m.key] as number | undefined;
              const lv = s.lab?.[m.key];
              const rating = (field?.[`${m.key}_rating`] as string | undefined) ?? s.lab_ratings?.[m.key];
              return (
                <div key={m.key} className="rounded-xl border border-line p-3">
                  <div className="text-xs text-muted">{m.label}</div>
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <span className="text-xl font-bold tabular-nums">{show(m.key, fv ?? lv)}</span>
                    <Rating r={rating} />
                  </div>
                  <div className="mt-1 text-[11px] text-muted">{fv != null ? "Real users (Chrome data)" : lv != null ? "Lab test" : "Not available"} · {m.hint}</div>
                </div>
              );
            })}
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {([["Performance", s.scores?.performance], ["Accessibility", s.scores?.accessibility], ["Best practices", s.scores?.best_practices], ["SEO", s.scores?.seo]] as const).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-muted">{k}</dt>
                <dd className={`text-lg font-bold tabular-nums ${scoreTone(v ?? null)}`}>{v ?? "—"}<span className="text-xs font-normal text-muted">/100</span></dd>
              </div>
            ))}
          </dl>
          {!field && <p className="mt-3 text-xs text-muted">Google has no real-user data for these pages yet (it needs enough Chrome visitors), so lab results are shown.</p>}
        </>
      )}
      {perf.quota_reached && (
        <p className="mt-3 text-xs text-amber-800">PageSpeed Insights' free limit was reached, so some pages were skipped.{perf.with_key ? "" : " An optional free API key in Settings → Integrations → Google raises the limit."}</p>
      )}
    </Card>
  );
}

function AiCard({ run }: { run: RunFull }) {
  const bots = run.ai.bots ?? [];
  const icon = (st: string) => st === "allowed" ? <CheckCircle2 size={15} className="text-emerald-700" aria-hidden />
    : st === "blocked" ? <ShieldAlert size={15} className="text-danger" aria-hidden /> : <CircleAlert size={15} className="text-amber-600" aria-hidden />;
  const label = { allowed: "Allowed", blocked: "Blocked", partial: "Partly blocked" } as const;
  return (
    <Card>
      <h3 className="flex items-center gap-2 font-semibold"><Bot size={17} /> AI crawler access</h3>
      <p className="mb-3 text-sm text-muted">
        Whether AI assistants (ChatGPT, Claude, Perplexity, Gemini…) may read your site, according to robots.txt.
        “Search” crawlers fetch pages to cite in answers; “training” crawlers collect content for future models.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-sm">
          <thead><tr className="text-left text-xs text-muted"><th className="py-1 font-medium">Crawler</th><th className="font-medium">Used for</th><th className="font-medium">Access</th></tr></thead>
          <tbody>
            {bots.map((b) => (
              <tr key={b.bot} className="border-t border-line">
                <td className="py-1.5"><span className="font-medium">{b.bot}</span> <span className="text-xs text-muted">{b.owner}</span></td>
                <td className="text-muted">{b.purpose === "search" ? "AI search answers" : "Model training"}</td>
                <td><span className="inline-flex items-center gap-1.5" title={b.rule}>{icon(b.status)}{label[b.status]}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-sm">
        <FileText size={15} className="text-muted" aria-hidden />
        {run.ai.llms_txt?.found ? <>llms.txt found{run.ai.llms_txt.has_title ? "" : " (but it doesn't start with a “# Title” line)"}</> : "No llms.txt (optional guide for AI assistants)"}
      </p>
    </Card>
  );
}

function SiteFiles({ run }: { run: RunFull }) {
  const rb = run.site.robots ?? {};
  const maps = run.site.sitemaps ?? [];
  return (
    <Card>
      <h3 className="mb-3 font-semibold">robots.txt and sitemaps</h3>
      <div className="grid gap-4 sm:grid-cols-2 text-sm">
        <div>
          <div className="font-medium">robots.txt</div>
          <p className="text-muted">
            {rb.found ? `Found · ${rb.sitemaps?.length ?? 0} sitemap line(s)${rb.invalid_lines?.length ? ` · ${rb.invalid_lines.length} line(s) not understood` : ""}`
              : `Not found${rb.status ? ` (HTTP ${rb.status})` : ""}`}
          </p>
        </div>
        <div>
          <div className="font-medium">Sitemaps</div>
          {maps.length ? (
            <ul className="text-muted">
              {maps.slice(0, 6).map((m) => (
                <li key={m.url} className="[overflow-wrap:anywhere]">{m.url} · {m.error ? <span className="text-danger">{m.error}</span> : `${m.urls} URLs`}</li>
              ))}
            </ul>
          ) : <p className="text-muted">No sitemap found</p>}
        </div>
      </div>
    </Card>
  );
}
