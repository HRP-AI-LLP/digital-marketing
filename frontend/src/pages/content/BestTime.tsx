import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, Sparkles } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Button, Spinner } from "../../components/ui";
import { DAY_NAMES, tzLabel } from "./shared";
import { SERIES, TipHead, TipRow, useTip } from "../../components/viz";

export type BestSlot = { at: string; local: string; input: string; score: number; strength: "Best" | "Great" | "Good" };
export type HeatCell = { weekday: number; day: string; hour: number; score: number; own: number | null; posts: number; in_window: boolean };
export type BestTimes = {
  timezone: string; platform: string | null; source: "account" | "platform" | "defaults"; sample: number;
  confidence: number; metric: string; message: string; slots: BestSlot[]; heatmap: HeatCell[];
  window: { start: number; end: number }; top: { weekday: number; day: string; hours: number[] }[];
};

export function useBestTimes(accountId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ["best-times", accountId],
    queryFn: () => api<BestTimes>(`/content/best-times?account_id=${accountId}`),
    enabled: !!accountId && enabled,
    staleTime: 5 * 60_000,
  });
}

const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

/** "Suggest best time" button for a scheduling field: shows the next 5 best slots to pick from. */
export function BestTimeSuggest({ accountId, onPick, disabled, label = "Suggest best time" }: {
  accountId: string | null | undefined; onPick: (localInput: string) => void; disabled?: boolean; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const q = useBestTimes(accountId, open);
  if (!accountId) return null;
  return (
    <div className="mt-1.5">
      <Button type="button" variant="ghost" className="!px-2 !py-1 text-sm" disabled={disabled} aria-expanded={open}
        onClick={() => setOpen(!open)}>
        <Sparkles size={14} /> {open ? "Hide suggestions" : label}
      </Button>
      {open && (
        <div className="mt-1.5 rounded-lg border border-line bg-surface p-2.5">
          {q.isLoading ? <Spinner label="Finding the best times…" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : (
            <>
              {q.data && q.data.slots.length === 0 && <p className="text-sm text-muted">No free good times in the next 7 days.</p>}
              <ul className="flex flex-wrap gap-1.5" aria-label="Suggested times">
                {q.data?.slots.map((s) => (
                  <li key={s.at}>
                    <button type="button" onClick={() => { onPick(s.input); setOpen(false); }}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-sm hover:border-primary-600 hover:bg-primary-50"
                      aria-label={`Use ${s.local} ${tzLabel(q.data!.timezone)} (${s.strength.toLowerCase()} time)`}>
                      <Clock size={13} className="text-muted" /> {s.local}
                      <span className={`text-xs ${s.strength === "Best" ? "font-semibold text-primary-700" : "text-muted"}`}>{s.strength}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {q.data && <p className="mt-2 text-xs text-muted [overflow-wrap:anywhere]">{q.data.message}</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Weekday × hour heatmap of the best-time scores (CSS grid; every cell has a text label). */
export function BestTimeHeatmap({ data }: { data: BestTimes }) {
  const hours = Array.from({ length: data.window.end - data.window.start + 1 }, (_, i) => data.window.start + i);
  const by = new Map(data.heatmap.map((c) => [`${c.weekday}-${c.hour}`, c]));
  const inWin = data.heatmap.filter((c) => c.in_window);
  const max = Math.max(0.0001, ...inWin.map((c) => c.score));
  const word = (v: number) => (v >= 0.85 ? "best" : v >= 0.65 ? "strong" : v >= 0.4 ? "fair" : "quiet");
  const t = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const clear = () => { setHover(null); t.hide(); };
  const tipFor = (d: string, h: number, c: HeatCell | undefined, v: number) => (
    <>
      <TipHead sub={tzLabel(data.timezone)}>{`${d} ${hh(h)}–${hh((h + 1) % 24)}`}</TipHead>
      <TipRow color={SERIES[0]} label="Score (vs the best slot)" value={`${Math.round(v * 100)} / 100`} />
      <TipRow label="Rating" value={word(v).replace(/^./, (x) => x.toUpperCase())} />
      {c?.own != null && <TipRow label="From your own results" value={`${Math.round(c.own * 100)} / 100`} />}
      <TipRow label="Posts published then" value={c?.posts ?? 0} />
      {data.source === "defaults" && <div className="mt-1 text-[11px] text-muted">Typical times for the platform, no results of yours yet.</div>}
    </>
  );
  return (
    <div ref={t.ref} className="relative">
      <div className="overflow-x-auto" onPointerLeave={clear}>
        <div role="grid" aria-label={`Best times to post by weekday and hour (${tzLabel(data.timezone)})`}
          className="grid min-w-[560px] gap-0.5 text-xs" style={{ gridTemplateColumns: `2.75rem repeat(${hours.length}, minmax(1.75rem, 1fr))` }}>
          <div role="row" className="contents">
            <span role="columnheader" className="sr-only">Day</span>
            {hours.map((h) => <span role="columnheader" key={h} className="text-center text-muted">{String(h).padStart(2, "0")}</span>)}
          </div>
          {DAY_NAMES.map((d, wd) => (
            <div role="row" key={d} className="contents">
              <span role="rowheader" className="self-center font-medium text-muted">{d}</span>
              {hours.map((h) => {
                const c = by.get(`${wd}-${h}`);
                const v = c ? c.score / max : 0;
                const label = `${d} ${hh(h)}: ${word(v)} (${Math.round(v * 100)} of 100)${c && c.posts ? `, ${c.posts} post${c.posts === 1 ? "" : "s"} published then` : ""}`;
                return (
                  <span role="gridcell" key={h} aria-label={label} tabIndex={0}
                    className={`relative h-7 rounded border outline-none ${hover === `${wd}-${h}` ? "border-ink ring-1 ring-ink" : "border-line/40"}`}
                    style={{ background: `rgba(8, 111, 184, ${(0.06 + 0.94 * v).toFixed(3)})` }}
                    onPointerMove={(e) => { setHover(`${wd}-${h}`); t.show(e, tipFor(d, h, c, v)); }}
                    onFocus={(e) => { setHover(`${wd}-${h}`); t.show(e.currentTarget, tipFor(d, h, c, v)); }} onBlur={clear}>
                    {c && c.posts > 0 && <span aria-hidden className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-white/90 ring-1 ring-primary-700" />}
                  </span>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: "rgba(8,111,184,0.12)" }} /> Quieter</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm" style={{ background: "rgba(8,111,184,1)" }} /> Best</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-white ring-1 ring-primary-700" /> You posted at this time</span>
        <span>Times in {tzLabel(data.timezone)}</span>
      </div>
      {t.tip}
    </div>
  );
}

export function confidenceText(d: BestTimes): string {
  if (d.source === "defaults") return "Typical times for this platform";
  return `${Math.round(d.confidence * 100)}% learned from ${d.source === "account" ? "this account" : "this platform"} (${d.sample} post${d.sample === 1 ? "" : "s"})`;
}
