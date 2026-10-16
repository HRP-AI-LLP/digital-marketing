import { createContext, useCallback, useContext, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CheckCircle2, Trash2, X } from "lucide-react";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Badge, Button, Input, Spinner } from "../../components/ui";

// ------------------------------------------------------------------ background tasks shared across tabs
type Tracked = Record<string, string>; // key → job id
type JobsApi = { tracked: Tracked; track: (key: string, jobId: string) => void; clear: (key: string) => void };
const JobsContext = createContext<JobsApi>({ tracked: {}, track: () => {}, clear: () => {} });

export function JobsProvider({ children }: { children: ReactNode }) {
  const [tracked, setTracked] = useState<Tracked>({});
  const track = useCallback((key: string, jobId: string) => setTracked((t) => (t[key] === jobId ? t : { ...t, [key]: jobId })), []);
  const clear = useCallback((key: string) => setTracked((t) => {
    const { [key]: _removed, ...rest } = t;
    return rest;
  }), []);
  const value = useMemo(() => ({ tracked, track, clear }), [tracked, track, clear]);
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

export function useIntelJobs() {
  return useContext(JobsContext);
}

/** Live status of the background task tracked under `jobKey`. Refreshes intelligence data when it finishes. */
export function JobStatus({ jobKey, label, doneText }: { jobKey: string; label: string; doneText?: (j: Job) => string }) {
  const { tracked, clear } = useIntelJobs();
  const qc = useQueryClient();
  const id = tracked[jobKey];
  const onDone = useCallback(() => { qc.invalidateQueries({ queryKey: ["intel"] }); }, [qc]);
  const job = useJob(id, onDone);
  if (!id) return null;
  if (!job || job.status === "queued" || job.status === "running") {
    const waiting = !job || job.status === "queued";
    return (
      <div className="rounded-lg border border-primary-200 bg-brand-tint px-3 py-2.5" role="status" aria-live="polite">
        <Spinner label={`${label}${job?.progress ? ` — ${job.progress}` : waiting ? " — waiting for the background worker to pick it up…" : "…"}`} />
      </div>
    );
  }
  const dismiss = (
    <button type="button" onClick={() => clear(jobKey)} aria-label="Dismiss" className="ml-auto text-muted hover:text-ink"><X size={15} /></button>
  );
  if (job.status === "failed") {
    return (
      <Alert>
        <div className="flex items-start gap-2"><span><strong>{label} didn't finish.</strong> {cleanError(job.error)}</span>{dismiss}</div>
      </Alert>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900" role="status">
      <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
      <span>{doneText ? doneText(job) : `${label} finished.`}</span>
      {dismiss}
    </div>
  );
}

export function cleanError(err: string | null | undefined): string {
  if (!err) return "Something went wrong. Try again.";
  return err.replace(/^[A-Za-z]+Error: /, "");
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return <Alert>{error instanceof Error ? error.message : String(error)}</Alert>;
}

// ------------------------------------------------------------------ small building blocks
export function ScoreBar({ value, max = 100, label }: { value: number; max?: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}>
      <div className="h-full rounded-full bg-gradient-to-r from-primary-600 to-secondary-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Chips({ items, max = 12, empty }: { items: string[]; max?: number; empty?: string }) {
  if (!items.length) return empty ? <span className="text-sm text-muted">{empty}</span> : null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.slice(0, max).map((x) => <Badge key={x}>{x}</Badge>)}
      {items.length > max && <span className="text-xs text-muted">+{items.length - max} more</span>}
    </div>
  );
}

/** Editable list of short strings shown as removable chips. */
export function ListEditor({ value, onChange, placeholder, disabled, label }: {
  value: string[]; onChange: (v: string[]) => void; placeholder: string; disabled?: boolean; label: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const parts = draft.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
    const lower = new Set(value.map((v) => v.toLowerCase()));
    const next = [...value, ...parts.filter((p) => !lower.has(p.toLowerCase()))];
    if (next.length !== value.length) onChange(next);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
  };
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {value.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-md bg-brand-tint px-2 py-1 text-sm text-brand-deep">
            {v}
            {!disabled && (
              <button type="button" aria-label={`Remove ${v} from ${label}`} onClick={() => onChange(value.filter((x) => x !== v))} className="text-brand-deep/60 hover:text-danger">
                <X size={13} />
              </button>
            )}
          </span>
        ))}
        {!value.length && <span className="text-sm text-muted">None yet.</span>}
      </div>
      {!disabled && (
        <div className="flex gap-2">
          <Input aria-label={`Add to ${label}`} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} onBlur={() => draft.trim() && add()} placeholder={placeholder} />
          <Button type="button" variant="secondary" onClick={add} disabled={!draft.trim()}>Add</Button>
        </div>
      )}
    </div>
  );
}

/** Two-step delete: first click asks, second click deletes. */
export function DeleteButton({ onConfirm, label, busy }: { onConfirm: () => void; label: string; busy?: boolean }) {
  const [asking, setAsking] = useState(false);
  if (asking) {
    return (
      <span className="inline-flex items-center gap-1">
        <Button variant="danger" className="h-8 px-3" onClick={onConfirm} disabled={busy}>{busy ? "Deleting…" : "Delete"}</Button>
        <Button variant="ghost" className="h-8 px-2" onClick={() => setAsking(false)}>Keep</Button>
      </span>
    );
  }
  return (
    <Button variant="ghost" className="h-8 px-2" aria-label={label} title={label} onClick={() => setAsking(true)}><Trash2 size={15} /></Button>
  );
}

export function SetupHint({ aiReady, hasWebsite }: { aiReady: boolean; hasWebsite: boolean }) {
  if (aiReady && hasWebsite) return null;
  return (
    <div className="space-y-2">
      {!hasWebsite && (
        <Alert tone="info">
          Add your website address in <Link className="font-medium underline" to="/settings/organization">Organization settings</Link> or
          on the Sources tab so it can be read.
        </Alert>
      )}
      {!aiReady && (
        <Alert tone="warn">
          Reading pages and building the profile needs an AI text model. Add one in{" "}
          <Link className="font-medium underline" to="/settings/integrations">Settings → Integrations</Link> (a local Ollama model or a free Gemini or Groq key works).
        </Alert>
      )}
    </div>
  );
}

export function SectionTitle({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {description && <p className="text-sm text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}
