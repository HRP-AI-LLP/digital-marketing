import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarHeart, CheckCircle2, Info, Megaphone } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Spinner, Toggle } from "../../components/ui";
import { CONFIDENCE_LABEL, daysBetween, fmtDay, type Occasion, type OccasionList } from "./types";

const CAT_DOT: Record<string, string> = {
  national: "bg-orange-500", festival: "bg-violet-500", regional: "bg-fuchsia-400", global: "bg-secondary-500",
  industry: "bg-primary-600", season: "bg-slate-400",
};

export function useOccasions(days: number, all = false) {
  return useQuery({
    queryKey: ["research", "occasions", days, all],
    queryFn: () => api<OccasionList>(`/research/occasions?days=${days}${all ? "&all=true" : ""}`),
    staleTime: 5 * 60_000,
  });
}

function when(o: Occasion, today: string) {
  const d = daysBetween(today, o.date);
  return d === 0 ? "Today" : d === 1 ? "Tomorrow" : `In ${d} days`;
}

function Uncertain({ o }: { o: Occasion }) {
  if (!["estimated", "derived", "holidays"].includes(o.confidence)) return null;
  return <span className="text-[11px] text-amber-700" title={CONFIDENCE_LABEL[o.confidence]}>Date may shift</span>;
}

/** Horizontal strip for the top of the Research page (and the Calendar). */
export function OccasionStrip({ today, days = 30, compact = false }: { today: string; days?: number; compact?: boolean }) {
  const q = useOccasions(days);
  const items = (q.data?.items ?? []).filter((o) => o.category !== "season" || o.date >= today);
  if (q.isLoading) return <Spinner label="Loading occasions…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  if (!items.length) return <p className="text-sm text-muted">No observed occasions in the next {days} days.</p>;
  return (
    <ul className="no-scrollbar -mx-1 flex snap-x gap-2.5 overflow-x-auto px-1 pb-1" aria-label="Upcoming occasions">
      {items.map((o) => (
        <li key={o.id} className={`snap-start shrink-0 rounded-xl border border-line bg-surface ${compact ? "w-44 p-2.5" : "w-52 p-3"}`}>
          <div className="flex items-start gap-2.5">
            <div className="grid w-11 shrink-0 place-items-center rounded-lg bg-gradient-to-b from-primary-700 to-primary-900 py-1 text-white">
              <span className="text-[10px] uppercase leading-none tracking-wide text-secondary-200">{fmtDay(o.date, { month: "short" })}</span>
              <span className="text-lg font-bold leading-tight tabular-nums">{Number(o.date.slice(8, 10))}</span>
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold" title={o.name}>{o.name}</p>
              <p className="flex items-center gap-1 text-xs text-muted">
                <span className={`h-1.5 w-1.5 rounded-full ${CAT_DOT[o.category] ?? "bg-slate-400"}`} aria-hidden />{o.category_label}
              </p>
              <p className="text-xs text-muted">{when(o, today)} <Uncertain o={o} /></p>
            </div>
          </div>
          {!compact && (o.has_brief || o.greeting_planned) && (
            <div className="mt-2 flex flex-wrap gap-x-2 text-[11px]">
              {o.has_brief && <span className="inline-flex items-center gap-0.5 text-emerald-700"><CheckCircle2 size={11} /> Idea ready</span>}
              {o.greeting_planned && <span className="inline-flex items-center gap-0.5 text-brand"><Megaphone size={11} /> Greeting planned</span>}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Full list for the Occasions tab. */
export function OccasionsTab({ today }: { today: string }) {
  const [all, setAll] = useState(false);
  const q = useOccasions(120, all);
  const items = q.data?.items ?? [];
  const months = new Map<string, Occasion[]>();
  for (const o of items) {
    const k = o.date.slice(0, 7);
    if (!months.has(k)) months.set(k, []);
    months.get(k)!.push(o);
  }
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          Festivals, national days, awareness days, industry dates and seasons for the next four months. Research suggests ideas{" "}
          {q.data ? `${q.data.lead_days} days` : ""} ahead for the ones you observe. Change what you observe in Settings.
        </p>
        <Toggle checked={all} onChange={setAll} label="Show occasions we don't observe" />
      </div>
      {q.isLoading && <Spinner label="Loading occasions…" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {[...months.entries()].map(([m, list]) => (
        <section key={m}>
          <h3 className="mb-2 text-sm font-semibold text-muted">{fmtDay(`${m}-01`, { month: "long", year: "numeric" })}</h3>
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
            {list.map((o) => (
              <li key={o.id} className={`grid gap-2 px-4 py-3 sm:grid-cols-[88px_1fr_auto] sm:items-center ${o.observed ? "" : "opacity-55"}`}>
                <div className="text-sm tabular-nums">
                  <span className="font-semibold">{fmtDay(o.date, { day: "numeric", month: "short" })}</span>
                  {o.end_date && <span className="text-muted"> – {fmtDay(o.end_date)}</span>}
                  {o.date >= today && <span className="block text-xs text-muted">{when(o, today)}</span>}
                </div>
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 font-medium">
                    <span className={`h-2 w-2 rounded-full ${CAT_DOT[o.category] ?? "bg-slate-400"}`} aria-hidden />{o.name}
                    <span className="text-xs font-normal text-muted">{o.category_label}{o.regions.length ? ` · ${o.regions.join(", ")}` : ""}</span>
                  </p>
                  {o.hint && <p className="text-sm text-muted">{o.hint}</p>}
                  {o.note && <p className="text-xs text-amber-800"><Info size={12} className="mr-1 inline" />{o.note}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs sm:justify-end">
                  <span className={`rounded-full px-2 py-0.5 ring-1 ring-inset ${["estimated", "derived", "holidays"].includes(o.confidence) ? "bg-amber-50 text-amber-800 ring-amber-200" : "bg-ink/[0.03] text-muted ring-line"}`}
                    title={o.source}>{CONFIDENCE_LABEL[o.confidence] ?? o.confidence}</span>
                  {o.has_brief && <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 size={12} /> Idea ready</span>}
                  {o.greeting_planned && <span className="inline-flex items-center gap-1 text-brand"><CalendarHeart size={12} /> Greeting planned</span>}
                  {!o.observed && <span className="text-muted">Not observed</span>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {q.data && !items.length && <p className="text-sm text-muted">Nothing in this period.</p>}
      {q.data && (
        <p className="text-xs text-muted">
          Lunar festival dates come from the Government of India holiday lists (via the python-holidays calendar) for{" "}
          {q.data.data_years.join(", ")}. Dates marked “Date may shift” depend on moon sighting; research checks them online as they come close.
        </p>
      )}
    </div>
  );
}
