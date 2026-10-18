import { Card, EmptyState } from "../../components/ui";
import { BarChart } from "../analytics/charts";
import { ScoreRing, SERIES, TrendChart, shortDay } from "../analytics/vizkit";
import { CostBadge } from "./shared";
import { pct, type Overview as O, type Run } from "./types";

function change(now?: number | null, before?: number | null, invert = false) {
  if (now == null || before == null) return null;
  const d = Math.round((now - before) * 10) / 10;
  if (d === 0) return <span className="text-xs text-muted">• no change since last run</span>;
  const good = invert ? d < 0 : d > 0;
  return <span className={`text-xs tabular-nums ${good ? "text-emerald-700" : "text-danger"}`}>{d > 0 ? "▲" : "▼"} {Math.abs(d)}{invert ? "" : " pts"} since last run</span>;
}

function Tile({ label, value, hint, delta }: { label: string; value: string; hint?: string; delta?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-0.5 text-2xl font-bold tabular-nums text-ink">{value}</div>
      {delta}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

export default function OverviewTab({ o, onOpenPrompt }: { o: O; onOpenPrompt: (text: string) => void }) {
  const run: Run | null = o.latest;
  const m = run?.metrics;
  if (!run || !m) {
    return (
      <EmptyState title="No visibility check yet"
        body={o.prompts.enabled ? "Press “Run check now” to ask your prompts on the chosen engines. It takes a minute or two."
          : "Start on the Prompts tab: add the questions your buyers ask AI assistants, or let AI suggest them."} />
    );
  }
  const s = m.sentiment;
  const totalSent = s.positive + s.neutral + s.negative;
  const days = o.trend.map((t) => shortDay(t.at.slice(0, 10)));
  // Several checks on one day: add the time so the points can be told apart.
  const trendLabels = days.map((d, i) => (days.indexOf(d) !== days.lastIndexOf(d)
    ? `${d} ${new Date(o.trend[i].at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: o.timezone })}` : d));
  const sovMax = Math.max(...m.sov.map((r) => r.share), 1);

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card className="flex flex-col items-center justify-center text-center">
          <ScoreRing score={Math.round(m.visibility)} label="visibility" />
          <p className="mt-2 text-sm text-muted">Mentioned in <strong className="text-ink">{m.mentioned}</strong> of {m.answers} AI answers</p>
          {change(m.visibility, o.previous?.visibility)}
        </Card>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Tile label="Share of voice" value={pct(m.share_of_voice)} hint="Your mentions out of all brand mentions" delta={change(m.share_of_voice, o.previous?.share_of_voice)} />
          <Tile label="Average position" value={m.avg_position == null ? "—" : `#${m.avg_position}`} hint="Where you appear when listed" delta={change(m.avg_position, o.previous?.avg_position, true)} />
          <Tile label="Your site cited" value={`${m.own_cited}`} hint={`${pct(m.own_cited_rate)} of answers link to you`} />
          <Tile label="Missed prompts" value={`${m.missed_count}`} hint="Competitors named, you weren't" />
          <div className="col-span-2 rounded-xl border border-line bg-surface p-4 md:col-span-4">
            <div className="mb-2 text-sm text-muted">How answers talk about you</div>
            {totalSent ? (
              <>
                <div className="flex h-3 overflow-hidden rounded-full bg-slate-100" role="img"
                  aria-label={`Positive ${s.positive}, neutral ${s.neutral}, negative ${s.negative}`}>
                  <div className="bg-emerald-600" style={{ width: `${(s.positive / totalSent) * 100}%` }} />
                  <div className="bg-slate-400" style={{ width: `${(s.neutral / totalSent) * 100}%` }} />
                  <div className="bg-danger" style={{ width: `${(s.negative / totalSent) * 100}%` }} />
                </div>
                <div className="mt-1.5 flex flex-wrap gap-4 text-xs text-muted">
                  <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-600" />Positive {s.positive}</span>
                  <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-400" />Neutral {s.neutral}</span>
                  <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-danger" />Negative {s.negative}</span>
                </div>
              </>
            ) : <p className="text-sm text-muted">No mentions yet, so no tone to show.</p>}
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <BarChart title="Share of voice" description="How often each brand is named across all answers"
            data={m.sov.map((r, i) => ({ label: r.is_us ? `${r.name} (you)` : r.name, value: r.share, color: r.is_us ? SERIES[0] : i < 8 ? "#94a3b8" : "#cbd5e1",
              hint: `${r.name}: ${r.mentions} answers, ${pct(r.share)} share${r.avg_position ? `, average #${r.avg_position}` : ""}` }))}
            format={(v) => pct(v)} max={sovMax} />
        </Card>
        <Card>
          <TrendChart title="Visibility over time" description="Each point is one check" labels={trendLabels}
            series={[{ name: "Visibility %", values: o.trend.map((t) => t.visibility ?? null) },
              { name: "Share of voice %", values: o.trend.map((t) => t.share_of_voice ?? null), dashed: true }]}
            format={(v) => `${Math.round(v)}%`} empty="Trends appear after the second check." />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <h3 className="mb-2 text-sm font-semibold">By engine</h3>
          <ul className="space-y-2 text-sm">
            {m.engines.map((e) => {
              const ref = run.engines.find((x) => x.key === e.key);
              return (
                <li key={e.key}>
                  <div className="flex items-center justify-between gap-2"><span className="min-w-0 truncate" title={e.label}>{e.label}</span><span className="tabular-nums">{pct(e.visibility)}</span></div>
                  <div className="mt-1 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-primary-600" style={{ width: `${e.visibility}%` }} /></div>
                    <CostBadge cost={ref?.cost} />
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
        <Card>
          <h3 className="mb-2 text-sm font-semibold">Most cited sources</h3>
          {m.top_domains.length ? (
            <ol className="space-y-1 text-sm">
              {m.top_domains.slice(0, 10).map((d) => (
                <li key={d.domain} className="flex justify-between gap-2">
                  <span className="min-w-0 truncate">{d.domain}{d.own && <span className="ml-1.5 text-xs text-secondary-800">(yours)</span>}</span>
                  <span className="tabular-nums text-muted">{d.answers}</span>
                </li>
              ))}
            </ol>
          ) : <p className="text-sm text-muted">No sources were cited.</p>}
          <p className="mt-2 text-xs text-muted">Getting listed on these sites helps AI engines find you.</p>
        </Card>
        <Card>
          <h3 className="mb-2 text-sm font-semibold">Missed prompts</h3>
          {m.missed.length ? (
            <ul className="space-y-2 text-sm">
              {m.missed.slice(0, 6).map((p) => (
                <li key={p.text}>
                  <button className="text-left text-brand hover:underline [overflow-wrap:anywhere]" onClick={() => onOpenPrompt(p.text)}>{p.text}</button>
                  <div className="text-xs text-muted [overflow-wrap:anywhere]">Named instead: {p.competitors.slice(0, 4).join(", ")}</div>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">None. Whenever competitors were named, you were too.</p>}
        </Card>
      </div>

      {m.topics.length > 1 && (
        <Card>
          <BarChart title="Visibility by topic" data={m.topics.map((t) => ({ label: t.topic, value: t.visibility, hint: `${t.topic}: ${t.mentioned} of ${t.answers} answers` }))}
            format={(v) => pct(v)} max={100} />
        </Card>
      )}
    </div>
  );
}
