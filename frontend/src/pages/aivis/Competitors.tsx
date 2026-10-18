import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { Alert, Badge, Card, EmptyState, Spinner } from "../../components/ui";
import { pct, type Competitors } from "./types";

export default function CompetitorsTab() {
  const q = useQuery({ queryKey: ["aivis", "competitors"], queryFn: () => api<Competitors>("/aivis/competitors") });
  if (q.isLoading) return <Spinner label="Loading…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const d = q.data;
  if (!d?.run_id) return <EmptyState title="No comparison yet" body="After the first check you'll see which brands AI engines name instead of you." />;
  return (
    <div className="space-y-6">
      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-canvas text-left text-xs text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Brand</th><th className="px-4 py-2 text-right font-medium">Answers naming it</th>
              <th className="px-4 py-2 text-right font-medium">Visibility</th><th className="px-4 py-2 text-right font-medium">Share of voice</th>
              <th className="px-4 py-2 text-right font-medium">Average position</th>
            </tr>
          </thead>
          <tbody>
            {d.brands.map((b) => (
              <tr key={b.name} className={`border-t border-line ${b.is_us ? "bg-brand-tint/50 font-medium" : ""}`}>
                <td className="px-4 py-2.5 [overflow-wrap:anywhere]">
                  {b.name} {b.is_us ? <Badge tone="brand">You</Badge> : b.tracked ? <Badge>Tracked competitor</Badge> : <Badge tone="accent">Also named</Badge>}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">{b.mentions}</td>
                <td className="px-4 py-2.5 text-right tabular-nums">{pct(b.visibility)}</td>
                <td className="px-4 py-2.5 text-right tabular-nums">{pct(b.share)}</td>
                <td className="px-4 py-2.5 text-right tabular-nums">{b.avg_position ? `#${b.avg_position}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-muted">
        “Also named” brands were found in the answers but aren't on your competitor list. Add the important ones on{" "}
        <Link className="text-brand underline" to="/intelligence">Company intelligence → Competitors</Link> to track them by name and website.
      </p>

      {d.head_to_head.length > 0 && (
        <section>
          <h3 className="mb-2 font-semibold">Head to head</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {d.head_to_head.map((h) => (
              <Card key={h.name} className="!p-4">
                <div className="mb-2 font-medium [overflow-wrap:anywhere]">You vs {h.name}</div>
                <dl className="grid grid-cols-3 gap-2 text-center text-xs text-muted">
                  <div><dd className="text-lg font-bold tabular-nums text-ink">{h.only_us}</dd><dt>only you</dt></div>
                  <div><dd className="text-lg font-bold tabular-nums text-ink">{h.both}</dd><dt>both named</dt></div>
                  <div><dd className="text-lg font-bold tabular-nums text-danger">{h.only_them}</dd><dt>only them</dt></div>
                </dl>
                {h.both > 0 && <p className="mt-2 text-xs text-muted">When both are named, you're listed first in {h.we_rank_higher} of {h.both}.</p>}
              </Card>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="mb-2 font-semibold">By engine</h3>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {d.engines.map((e) => (
            <Card key={e.key} className="!p-4">
              <div className="mb-2 text-sm font-medium [overflow-wrap:anywhere]">{e.label} <span className="text-xs text-muted">· {e.answers} answers</span></div>
              <ol className="space-y-1 text-sm">
                {e.brands.map((b) => (
                  <li key={b.name} className="flex justify-between gap-2"><span className="min-w-0 truncate">{b.name}</span><span className="tabular-nums text-muted">{pct(b.visibility)}</span></li>
                ))}
                {!e.brands.length && <li className="text-muted">No brands named.</li>}
              </ol>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
