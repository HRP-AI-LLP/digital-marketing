import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Card, EmptyState, Select, Spinner, formatDate } from "../../components/ui";
import { SevBadge, UrlLink } from "./shared";
import { scoreTone, type Compare, type RunBrief } from "./types";

export default function CompareTab({ tz }: { tz: string }) {
  const runs = useQuery({ queryKey: ["siteaudit", "runs"], queryFn: () => api<RunBrief[]>("/site-audit/runs?limit=60") });
  const done = (runs.data ?? []).filter((r) => r.status === "done");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  useEffect(() => {
    if (done.length >= 2 && !a && !b) { setB(done[0].id); setA(done[1].id); }
  }, [done, a, b]);
  const cmp = useQuery({
    queryKey: ["siteaudit", "compare", a, b], enabled: !!a && !!b && a !== b,
    queryFn: () => api<Compare>(`/site-audit/compare?a=${a}&b=${b}`),
  });
  if (runs.isLoading) return <Spinner label="Loading…" />;
  if (done.length < 2) return <EmptyState title="Nothing to compare yet" body="Comparisons appear once two audits have finished." />;
  const label = (r: RunBrief) => `${formatDate(r.finished_at ?? r.created_at, tz)} · health ${r.score}`;
  const c = cmp.data;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Earlier audit" value={a} onChange={(e) => setA(e.target.value)} className="w-full sm:w-72">
          {done.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </Select>
        <span className="text-muted">compared with</span>
        <Select aria-label="Later audit" value={b} onChange={(e) => setB(e.target.value)} className="w-full sm:w-72">
          {done.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </Select>
      </div>
      {a === b && <p className="text-sm text-muted">Pick two different audits.</p>}
      {cmp.isLoading && <Spinner label="Comparing…" />}
      {c && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {([["Health", c.before.score, c.after.score, false], ["Errors", c.before.errors, c.after.errors, true],
               ["Warnings", c.before.warnings, c.after.warnings, true], ["Notices", c.before.notices, c.after.notices, true],
               ["URLs checked", c.before.pages, c.after.pages, null]] as const).map(([k, x, y, lowerBetter]) => {
              const d = (y ?? 0) - (x ?? 0);
              const good = lowerBetter == null ? null : lowerBetter ? d < 0 : d > 0;
              return (
                <Card key={k} className="p-4">
                  <div className="text-xs text-muted">{k}</div>
                  <div className={`text-2xl font-bold tabular-nums ${k === "Health" ? scoreTone(y) : ""}`}>{y ?? "—"}</div>
                  <div className={`text-xs tabular-nums ${d === 0 || good == null ? "text-muted" : good ? "text-emerald-700" : "text-danger"}`}>
                    {d > 0 ? "▲" : d < 0 ? "▼" : "•"} {Math.abs(d)} (was {x ?? "—"})
                  </div>
                </Card>
              );
            })}
          </div>
          <Card className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead><tr className="text-left text-xs text-muted"><th className="px-4 py-2 font-medium">Issue</th><th className="font-medium">Before</th><th className="font-medium">After</th><th className="pr-4 font-medium">Change</th></tr></thead>
                <tbody>
                  {c.checks.map((r) => (
                    <tr key={r.id} className="border-t border-line">
                      <td className="px-4 py-2"><div className="flex items-start gap-2"><SevBadge s={r.severity} /><span className="[overflow-wrap:anywhere]">{r.title}</span></div></td>
                      <td className="tabular-nums">{r.before}</td>
                      <td className="tabular-nums">{r.after}</td>
                      <td className={`pr-4 tabular-nums ${r.change > 0 ? "text-danger" : r.change < 0 ? "text-emerald-700" : "text-muted"}`}>{r.change > 0 ? `+${r.change}` : r.change}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          {c.detailed ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <UrlList title={`New issues (${c.new_count})`} items={c.new} tone="text-danger" />
              <UrlList title={`Fixed issues (${c.fixed_count})`} items={c.fixed} tone="text-emerald-700" />
            </div>
          ) : <p className="text-sm text-muted">Page-level details of older audits are no longer kept, so only the counts are compared.</p>}
        </>
      )}
    </div>
  );
}

function UrlList({ title, items, tone }: { title: string; items: Compare["new"]; tone: string }) {
  return (
    <Card>
      <h3 className={`mb-2 font-semibold ${tone}`}>{title}</h3>
      {items.length ? (
        <ul className="max-h-[420px] space-y-1.5 overflow-y-auto text-sm">
          {items.slice(0, 200).map((i) => (
            <li key={`${i.check}|${i.url}`}>
              <div className="flex items-start gap-2"><SevBadge s={i.severity} /><span>{i.title}</span></div>
              <UrlLink url={i.url} className="ml-1 text-xs" />
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted">None.</p>}
    </Card>
  );
}
