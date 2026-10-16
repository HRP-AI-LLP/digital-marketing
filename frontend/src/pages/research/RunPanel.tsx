import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDashed, Loader2, XCircle } from "lucide-react";
import { formatDate } from "../../components/ui";
import type { Run, Step } from "./types";

const STATUS_TEXT: Record<string, string> = {
  queued: "Waiting to start", running: "Researching", done: "Finished", partial: "Finished with warnings", failed: "Failed",
};

export function RunStatusIcon({ status, className = "" }: { status: string; className?: string }) {
  if (status === "running") return <Loader2 size={16} className={`animate-spin text-secondary-300 ${className}`} />;
  if (status === "queued") return <CircleDashed size={16} className={`text-secondary-300 ${className}`} />;
  if (status === "done") return <CheckCircle2 size={16} className={`text-emerald-400 ${className}`} />;
  if (status === "partial") return <AlertTriangle size={16} className={`text-amber-300 ${className}`} />;
  return <XCircle size={16} className={`text-red-400 ${className}`} />;
}

function StepRow({ s, tz, last, live }: { s: Step; tz: string; last: boolean; live: boolean }) {
  const dot = s.level === "error" ? "bg-red-400" : s.level === "warn" ? "bg-amber-300" : last && live ? "bg-secondary-300 animate-pulse" : "bg-white/40";
  return (
    <li className="relative flex gap-3 pb-3 pl-1 last:pb-0">
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className={`text-sm ${s.level === "warn" ? "text-amber-100" : s.level === "error" ? "text-red-200" : "text-white/85"}`}>{s.message}</p>
        <p className="text-[11px] text-white/40">{new Intl.DateTimeFormat("en-IN", { timeStyle: "medium", timeZone: tz }).format(new Date(s.at))}</p>
      </div>
    </li>
  );
}

/** Live step log of a run, shown inside the dark briefing panel. */
export function RunLog({ run, tz }: { run: Run; tz: string }) {
  const [open, setOpen] = useState(run.status === "running" || run.status === "queued");
  const live = run.status === "running" || run.status === "queued";
  const steps = run.steps ?? [];
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.04]">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left">
        <RunStatusIcon status={run.status} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-white">
            {STATUS_TEXT[run.status] ?? run.status} · {run.depth} research {run.trigger === "scheduled" ? "(daily)" : "(started by hand)"}
          </span>
          <span className="block truncate text-xs text-white/55">
            {live ? run.last_step?.message ?? "Getting ready…" : run.finished_at ? `Finished ${formatDate(run.finished_at, tz)}` : run.error ?? ""}
          </span>
        </span>
        <ChevronDown size={16} className={`text-white/60 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="border-t border-white/10 px-3 py-3">
          {run.stats && Object.keys(run.stats).length > 0 && (
            <dl className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-white/60">
              {run.stats.searches != null && <div><dt className="inline">Searches </dt><dd className="inline font-semibold text-white">{run.stats.searches}</dd></div>}
              {run.stats.pages_read != null && <div><dt className="inline">Pages read </dt><dd className="inline font-semibold text-white">{run.stats.pages_read}</dd></div>}
              {run.stats.facts != null && <div><dt className="inline">Facts </dt><dd className="inline font-semibold text-white">{run.stats.facts}</dd></div>}
              {run.stats.briefs != null && <div><dt className="inline">Briefs </dt><dd className="inline font-semibold text-white">{run.stats.briefs}</dd></div>}
              {run.stats.llm_calls != null && <div><dt className="inline">AI calls </dt><dd className="inline font-semibold text-white">{run.stats.llm_calls}</dd></div>}
            </dl>
          )}
          <ol className="scroll-dark max-h-64 overflow-y-auto pr-1" aria-live="polite">
            {steps.map((s, i) => <StepRow key={`${s.at}-${i}`} s={s} tz={tz} last={i === steps.length - 1} live={live} />)}
            {!steps.length && <li className="text-sm text-white/60">Waiting for the background worker…</li>}
          </ol>
        </div>
      )}
    </div>
  );
}
