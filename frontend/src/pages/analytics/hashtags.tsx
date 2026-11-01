import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Info } from "lucide-react";
import { api } from "../../api/client";
import { setReportParams } from "../../components/ExportPdf";
import { PRESETS, presetRange } from "../../components/periods";
import { Alert, Badge, Card, Select, Spinner } from "../../components/ui";
import { useContentAccounts, useMeta } from "../content/shared";
import { fmtNum, fmtPct } from "./charts";
import { SERIES, TipHead, TipRow, fmtDay, useTip } from "../../components/viz";

type Tag = {
  tag: string; posts: number; posts_with_metrics: number; avg_impressions: number | null; avg_engagements: number | null;
  engagement_rate: number | null; clicks: number; lift: number | null; impressions_lift: number | null;
  trend: "up" | "down" | "flat" | null; platforms: string[]; accounts: string[]; last_used: string | null;
  suggestion: "keep" | "drop" | "try" | "neutral" | "wait"; reason: string;
};
type Hashtags = {
  start: string; end: string; platform: string | null; totals: { posts: number; posts_with_metrics: number; posts_with_hashtags: number; hashtags: number };
  accounts: { id: string; name: string; platform: string | null; posts: number; avg_impressions: number; avg_engagements: number; engagement_rate: number | null }[];
  hashtags: Tag[]; top: Tag[]; bottom: Tag[]; suggestions: { keep: Tag[]; drop: Tag[]; try: (Tag | { tag: string; suggestion: "try"; reason: string })[] };
  min_posts: number; note: string | null; method: string;
};

const SUGGESTION: Record<Tag["suggestion"], { label: string; tone: "brand" | "danger" | "accent" | "neutral" }> = {
  keep: { label: "Keep", tone: "brand" }, drop: { label: "Drop", tone: "danger" }, try: { label: "Try more", tone: "accent" },
  neutral: { label: "No clear effect", tone: "neutral" }, wait: { label: "Not enough data", tone: "neutral" },
};
const signedPct = (v: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`);

function Trend({ t }: { t: Tag["trend"] }) {
  if (!t) return <span className="text-muted">—</span>;
  const [Icon, label, cls] = t === "up" ? [ArrowUpRight, "Rising", "text-emerald-700"] : t === "down" ? [ArrowDownRight, "Falling", "text-orange-700"] : [ArrowRight, "Steady", "text-muted"];
  return <span className={`inline-flex items-center gap-0.5 ${cls}`}><Icon size={14} aria-hidden />{label}</span>;
}

/** Engagement compared with the account's usual, as a bar either side of zero (the signed number carries the meaning). */
function LiftBars({ title, rows, empty }: { title: string; rows: Tag[]; empty: string }) {
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const max = Math.max(0.25, ...rows.map((r) => Math.abs(r.lift ?? 0)));
  const tipFor = (r: Tag) => (
    <>
      <TipHead sub={r.platforms.join(", ") || undefined}>#{r.tag}</TipHead>
      <TipRow color={(r.lift ?? 0) >= 0 ? SERIES[0] : SERIES[1]} label="vs account's usual" value={signedPct(r.lift)} />
      <TipRow label="Engagement rate" value={r.engagement_rate == null ? "—" : fmtPct(r.engagement_rate, 2)} />
      {r.impressions_lift != null && <TipRow label="Impressions vs usual" value={signedPct(r.impressions_lift)} />}
      <TipRow label="Avg. impressions" value={r.avg_impressions == null ? "—" : fmtNum(r.avg_impressions)} />
      <TipRow label="Avg. engagements" value={r.avg_engagements == null ? "—" : fmtNum(r.avg_engagements)} />
      {r.clicks > 0 && <TipRow label="Clicks" value={fmtNum(r.clicks)} />}
      <TipRow label="Posts (with results)" value={`${r.posts} (${r.posts_with_metrics})`} />
      {r.last_used && <TipRow label="Last used" value={fmtDay(r.last_used.slice(0, 10))} />}
      <div className="mt-1 text-[11px] text-muted">{SUGGESTION[r.suggestion].label}: {r.reason}</div>
    </>
  );
  const clear = () => { setHover(null); t.hide(); };
  return (
    <Card className="min-w-0">
      <h3 className="font-semibold">{title}</h3>
      {rows.length === 0 ? <p className="mt-2 text-sm text-muted">{empty}</p> : (
        <div ref={t.ref} className="relative">
          <ul className="mt-3 space-y-2">
            {rows.map((r) => {
              const w = Math.min(50, (Math.abs(r.lift ?? 0) / max) * 50);
              const pos = (r.lift ?? 0) >= 0;
              return (
                <li key={r.tag} tabIndex={0} aria-label={`#${r.tag}: ${signedPct(r.lift)} vs the account's usual`}
                  className={`grid cursor-default grid-cols-[minmax(0,8rem)_1fr_3.5rem] items-center gap-2 rounded-md px-1 text-sm outline-none transition-colors ${hover === r.tag ? "bg-slate-50" : ""}`}
                  onPointerMove={(e) => { setHover(r.tag); t.show(e, tipFor(r)); }} onPointerLeave={clear}
                  onFocus={(e) => { setHover(r.tag); t.show(e.currentTarget, tipFor(r)); }} onBlur={clear}>
                  <span className="truncate font-medium">#{r.tag}</span>
                  <span className="relative h-3 rounded bg-slate-100" aria-hidden>
                    <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
                    <span className={`absolute inset-y-0 rounded transition-opacity ${pos ? "bg-[#086fb8]" : "bg-[#eb6834]"}`}
                      style={{ ...(pos ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }), opacity: hover && hover !== r.tag ? 0.45 : 1 }} />
                  </span>
                  <span className={`text-right tabular-nums ${pos ? "text-ink" : "text-orange-800"}`}>{signedPct(r.lift)}</span>
                </li>
              );
            })}
          </ul>
          {t.tip}
        </div>
      )}
    </Card>
  );
}

export function HashtagsPanel() {
  const meta = useMeta();
  const accounts = useContentAccounts();
  const [preset, setPreset] = useState("90");
  const [platform, setPlatform] = useState("");
  const range = presetRange(preset);
  const qs = new URLSearchParams({ start: range.start, end: range.end, ...(platform ? { platform } : {}) });
  const q = useQuery({ queryKey: ["analytics-hashtags", qs.toString()], queryFn: () => api<Hashtags>(`/analytics/hashtags?${qs}`) });
  useEffect(() => { setReportParams({ start: range.start, end: range.end, platform }); }, [range.start, range.end, platform]);
  useEffect(() => () => setReportParams({}), []);  // other tabs export their own report
  const platforms = [...new Set((accounts.data ?? []).map((a) => a.platform))];
  const d = q.data;

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Period" value={preset} onChange={(e) => setPreset(e.target.value)} className="w-44">
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </Select>
        <Select aria-label="Platform" value={platform} onChange={(e) => setPlatform(e.target.value)} className="w-56">
          <option value="">All platforms</option>
          {platforms.map((p) => <option key={p} value={p}>{meta.data?.platforms.find((x) => x.id === p)?.label ?? p}</option>)}
        </Select>
      </div>
      {q.isLoading ? <Spinner label="Reading your hashtags" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : d && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {([["Published posts", d.totals.posts], ["With results", d.totals.posts_with_metrics], ["With hashtags", d.totals.posts_with_hashtags], ["Hashtags used", d.totals.hashtags]] as const).map(([l, v]) => (
              <div key={l} className="rounded-xl border border-line bg-surface p-4"><div className="text-sm text-muted">{l}</div><div className="mt-0.5 text-2xl font-bold tabular-nums">{fmtNum(v)}</div></div>
            ))}
          </div>
          {d.note && <Alert tone="info">{d.note}</Alert>}

          <div className="grid gap-5 lg:grid-cols-2">
            <LiftBars title="Top hashtags" rows={d.top} empty={`No hashtag did better than usual on at least ${d.min_posts} posts with results yet.`} />
            <LiftBars title="Bottom hashtags" rows={d.bottom} empty={`No hashtag did worse than usual on at least ${d.min_posts} posts with results.`} />
          </div>
          <p className="-mt-3 text-xs text-muted">Bars show engagement rate compared with the usual for the same account in this period (0 = the account's average).</p>

          <div className="grid gap-5 md:grid-cols-3">
            {(["keep", "drop", "try"] as const).map((k) => (
              <Card key={k} className="min-w-0">
                <div className="flex items-center gap-2"><Badge tone={SUGGESTION[k].tone}>{SUGGESTION[k].label}</Badge><span className="text-xs text-muted">{d.suggestions[k].length}</span></div>
                {d.suggestions[k].length === 0 ? <p className="mt-2 text-sm text-muted">{k === "try" ? "Nothing to suggest yet." : `Needs a hashtag on at least ${d.min_posts} posts with results.`}</p> : (
                  <ul className="mt-2 space-y-1.5 text-sm">
                    {d.suggestions[k].slice(0, 8).map((t) => <li key={t.tag} className="[overflow-wrap:anywhere]"><span className="font-medium">#{t.tag}</span> <span className="text-muted">{t.reason}</span></li>)}
                  </ul>
                )}
              </Card>
            ))}
          </div>

          <Card className="!p-0">
            <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4">
              <h3 className="font-semibold">All hashtags</h3>
              <span className="text-xs text-muted">{d.hashtags.length} hashtag{d.hashtags.length === 1 ? "" : "s"}</span>
            </div>
            {d.hashtags.length === 0 ? <p className="px-5 pb-5 pt-2 text-sm text-muted">No hashtags on published posts in this period.</p> : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[820px] text-sm">
                  <thead className="border-y border-line bg-canvas text-left text-xs text-muted">
                    <tr>
                      <th className="px-3 py-2 font-medium">Hashtag</th>
                      <th className="px-3 py-2 text-right font-medium">Posts</th>
                      <th className="px-3 py-2 text-right font-medium">Avg. impressions</th>
                      <th className="px-3 py-2 text-right font-medium">Avg. engagements</th>
                      <th className="px-3 py-2 text-right font-medium">Engagement rate</th>
                      <th className="px-3 py-2 text-right font-medium">vs account</th>
                      <th className="px-3 py-2 font-medium">Trend</th>
                      <th className="px-3 py-2 font-medium">Suggestion</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {d.hashtags.map((t) => (
                      <tr key={t.tag} className="align-top">
                        <td className="px-3 py-2"><div className="font-medium [overflow-wrap:anywhere]">#{t.tag}</div><div className="text-xs text-muted">{t.accounts.join(", ")}</div></td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.posts}{t.posts_with_metrics !== t.posts && <div className="text-xs text-muted">{t.posts_with_metrics} with results</div>}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.avg_impressions == null ? "—" : fmtNum(t.avg_impressions)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.avg_engagements == null ? "—" : fmtNum(t.avg_engagements)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.engagement_rate == null ? "—" : fmtPct(t.engagement_rate, 2)}</td>
                        <td className={`px-3 py-2 text-right tabular-nums ${(t.lift ?? 0) < 0 ? "text-orange-800" : ""}`}>{signedPct(t.lift)}</td>
                        <td className="px-3 py-2"><Trend t={t.trend} /></td>
                        <td className="px-3 py-2"><Badge tone={SUGGESTION[t.suggestion].tone}>{SUGGESTION[t.suggestion].label}</Badge><div className="mt-0.5 max-w-[16rem] text-xs text-muted">{t.reason}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <p className="flex items-start gap-1.5 text-xs text-muted"><Info size={14} className="mt-0.5 shrink-0" />{d.method} Suggestions come only from these numbers: keep and drop need at least {d.min_posts} posts with results.</p>
        </>
      )}
    </div>
  );
}
