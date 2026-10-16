import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Sparkles } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Button, Modal, Spinner } from "../../components/ui";
import type { Pillar } from "./shared";

// ------------------------------------------------------------------ shared: start a suggestion job and wait
function useSuggestion<T>(kind: "voice" | "pillars") {
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>("/brand-ai/suggest", { method: "POST", json: { kind } }),
    onMutate: () => { setError(null); setResult(null); },
    onSuccess: (r) => setJobId(r.job_id),
    onError: (e) => setError((e as Error).message),
  });
  const job = useJob(jobId, (j: Job) => {
    if (j.status === "done") setResult(j.result as T);
    else setError((j.error ?? "The AI couldn't make suggestions.").replace(/^\w+Error: /, ""));
    setJobId(null);
  });
  const busy = start.isPending || !!jobId;
  return { start: () => start.mutate(), busy, progress: job?.progress, result, error, clear: () => { setResult(null); setError(null); } };
}

export function SuggestButton({ onClick, busy, label = "Suggest with AI" }: { onClick: () => void; busy: boolean; label?: string }) {
  return (
    <Button type="button" variant="secondary" onClick={onClick} disabled={busy}>
      {busy ? <Spinner /> : <Sparkles size={15} className="text-secondary-600" />} {busy ? "Thinking…" : label}
    </Button>
  );
}

// ------------------------------------------------------------------ brand voice
export type VoiceValues = {
  voice: string | null; audience: string | null; do_list: string[]; dont_list: string[]; banned_words: string[];
  hashtags: string[]; ctas: string[]; emoji_policy: string;
};
type VoiceResult = { current: VoiceValues; suggested: Partial<VoiceValues>; notes: Record<string, string>; samples: string[] };
const VOICE_FIELDS: [keyof VoiceValues, string][] = [
  ["voice", "How we sound"], ["audience", "Who we write for"], ["do_list", "Always do"], ["dont_list", "Never do"],
  ["banned_words", "Banned words"], ["hashtags", "Brand hashtags"], ["ctas", "Calls to action"], ["emoji_policy", "Emojis"],
];
const EMOJI: Record<string, string> = { none: "None", light: "A few (up to 3)", rich: "Use freely" };

function show(k: keyof VoiceValues, v: unknown): string {
  if (v == null || v === "") return "";
  if (k === "hashtags") return (v as string[]).map((h) => `#${h}`).join(" ");
  if (k === "emoji_policy") return EMOJI[v as string] ?? String(v);
  if (Array.isArray(v)) return k === "banned_words" ? v.join(", ") : v.map((x) => `• ${x}`).join("\n");
  return String(v);
}

/** "Suggest with AI" for the voice and rules form. `current` is what the form shows now (unsaved edits included);
 * `onApply` puts the accepted fields into the form — nothing is saved until the person presses Save. */
export function VoiceSuggest({ current, onApply }: { current: VoiceValues; onApply: (v: Partial<VoiceValues>) => void }) {
  const s = useSuggestion<VoiceResult>("voice");
  const [pick, setPick] = useState<Record<string, boolean>>({});
  const r = s.result;
  const fields = r ? VOICE_FIELDS.filter(([k]) => r.suggested[k] !== undefined && show(k, r.suggested[k]) !== show(k, current[k])) : [];
  const chosen = (k: string) => pick[k] ?? true;
  const apply = (all: boolean) => {
    if (!r) return;
    const out: Partial<VoiceValues> = {};
    for (const [k] of fields) if (all || chosen(k)) (out as Record<string, unknown>)[k] = r.suggested[k];
    onApply(out);
    s.clear();
    setPick({});
  };
  return (
    <>
      <SuggestButton onClick={s.start} busy={s.busy} />
      {s.busy && s.progress && <span className="text-sm text-muted">{s.progress}</span>}
      {s.error && <div className="w-full"><Alert>{s.error}</Alert></div>}
      {r && (
        <Modal title="Suggested brand voice" onClose={() => s.clear()} wide>
          <p className="mb-4 text-sm text-muted">Based on your company profile, knowledge base, competitors and recent research. Pick what to use; you can still edit everything before saving.</p>
          {fields.length === 0 ? <Alert tone="info">Your current settings already match the suggestions.</Alert> : (
            <ul className="max-h-[55vh] space-y-3 overflow-y-auto pr-1">
              {fields.map(([k, label]) => (
                <li key={k} className={`rounded-lg border p-3 ${chosen(k) ? "border-primary-300 bg-brand-tint/40" : "border-line"}`}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm font-semibold">
                    <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={chosen(k)} onChange={(e) => setPick({ ...pick, [k]: e.target.checked })} />
                    {label}
                  </label>
                  <div className="mt-2 grid gap-3 text-sm sm:grid-cols-2">
                    <div><div className="mb-1 text-xs text-muted">Now</div><p className="whitespace-pre-line text-ink/70">{show(k, current[k]) || <span className="italic text-muted">Empty</span>}</p></div>
                    <div><div className="mb-1 text-xs font-medium text-secondary-700">Suggested</div><p className="whitespace-pre-line">{show(k, r.suggested[k])}</p></div>
                  </div>
                  {r.notes[k] && <p className="mt-2 text-xs text-muted">Why: {r.notes[k]}</p>}
                </li>
              ))}
            </ul>
          )}
          {r.samples.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer text-brand">See sample posts in this voice</summary>
              <div className="mt-2 space-y-2">{r.samples.map((p, i) => <p key={i} className="whitespace-pre-line rounded-lg bg-canvas p-3">{p}</p>)}</div>
            </details>
          )}
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => { s.clear(); s.start(); }}><RefreshCw size={14} /> Regenerate</Button>
            {fields.length > 0 && <Button type="button" variant="secondary" onClick={() => apply(true)}>Use all</Button>}
            {fields.length > 0 && <Button type="button" onClick={() => apply(false)} disabled={!fields.some(([k]) => chosen(k))}>Use selected</Button>}
          </div>
        </Modal>
      )}
    </>
  );
}

// ------------------------------------------------------------------ pillars
type SuggestedPillar = { name: string; description: string | null; weight: number; reason: string; existing_id: string | null; color: string };
type PillarResult = { current: Pillar[]; suggested: SuggestedPillar[]; notes: string };

export function PillarSuggest({ current }: { current: Pillar[] }) {
  const qc = useQueryClient();
  const s = useSuggestion<PillarResult>("pillars");
  const [pick, setPick] = useState<Record<string, boolean>>({});
  const r = s.result;
  const chosen = (n: string) => pick[n] ?? true;
  const apply = useMutation({
    mutationFn: async (all: boolean) => {
      for (const p of r?.suggested ?? []) {
        if (!all && !chosen(p.name)) continue;
        if (p.existing_id) await api(`/content/pillars/${p.existing_id}`, { method: "PATCH", json: { description: p.description, weight: p.weight, active: true } });
        else await api("/content/pillars", { method: "POST", json: { name: p.name, description: p.description, weight: p.weight, color: p.color, active: true } });
      }
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pillars"] }); s.clear(); setPick({}); },
  });
  const byId = new Map(current.map((p) => [p.id, p]));
  const untouched = r ? current.filter((p) => !r.suggested.some((x) => x.existing_id === p.id)) : [];
  return (
    <>
      <SuggestButton onClick={s.start} busy={s.busy} label="Suggest topic mix with AI" />
      {s.busy && s.progress && <span className="text-sm text-muted">{s.progress}</span>}
      {s.error && <div className="w-full"><Alert>{s.error}</Alert></div>}
      {r && (
        <Modal title="Suggested topic pillars" onClose={() => s.clear()} wide>
          {r.notes && <p className="mb-3 text-sm text-muted">{r.notes}</p>}
          {apply.error && <div className="mb-3"><Alert>{(apply.error as Error).message}</Alert></div>}
          <ul className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {r.suggested.map((p) => {
              const ex = p.existing_id ? byId.get(p.existing_id) : undefined;
              return (
                <li key={p.name} className={`rounded-lg border p-3 ${chosen(p.name) ? "border-primary-300 bg-brand-tint/40" : "border-line"}`}>
                  <label className="flex cursor-pointer items-start gap-2.5">
                    <input type="checkbox" className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]" checked={chosen(p.name)} onChange={(e) => setPick({ ...pick, [p.name]: e.target.checked })} />
                    <span className="h-3 w-3 shrink-0 translate-y-1.5 rounded-full" style={{ background: p.color }} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">{p.name}</span>
                        <span className="rounded bg-ink/5 px-1.5 text-xs text-muted">{ex ? "Update existing" : "New"}</span>
                        <span className="ml-auto text-sm tabular-nums">
                          {ex && ex.weight !== p.weight && <span className="text-muted line-through">{ex.weight}</span>} <span className="font-semibold">{p.weight}%</span>
                        </span>
                      </span>
                      {p.description && <span className="block text-sm">{p.description}</span>}
                      {ex?.description && ex.description !== p.description && <span className="block text-xs text-muted">Now: {ex.description}</span>}
                      {p.reason && <span className="block text-xs text-muted">Why: {p.reason}</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {untouched.length > 0 && <p className="mt-3 text-xs text-muted">Kept as they are: {untouched.map((p) => p.name).join(", ")}. Turn them off on this page if you no longer want them.</p>}
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => { s.clear(); s.start(); }}><RefreshCw size={14} /> Regenerate</Button>
            <Button type="button" variant="secondary" disabled={apply.isPending} onClick={() => apply.mutate(true)}>Accept all</Button>
            <Button type="button" disabled={apply.isPending || !r.suggested.some((p) => chosen(p.name))} onClick={() => apply.mutate(false)}>
              {apply.isPending ? "Saving…" : "Accept selected"}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
