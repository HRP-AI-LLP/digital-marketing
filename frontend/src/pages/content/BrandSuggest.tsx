import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarRange, Check, Pencil, RefreshCw, Sparkles, X } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Badge, Button, Modal, Spinner } from "../../components/ui";
import type { Campaign, Pillar } from "./shared";

// ------------------------------------------------------------------ shared: start a suggestion job and wait
function useSuggestion<T>(kind: "voice" | "pillars" | "campaigns") {
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

// ------------------------------------------------------------------ campaigns
export type CampaignIdea = {
  name: string; brief: string | null; starts_on: string | null; ends_on: string | null; boost: number; goal: string;
  occasion: string; channels: string[]; post_ideas: string[]; reason: string;
};
type CampaignResult = { suggested: CampaignIdea[]; notes: string; window: { start: string; end: string } };
const GOAL: Record<string, string> = {
  awareness: "Awareness", leads: "Leads", launch: "Launch", engagement: "Engagement", hiring: "Hiring", community: "Community",
};
const day = (d: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${d}T00:00:00`));

/** "Suggest campaigns": the AI plans the next ~4 months from your offerings, research ideas and the festivals and
 * special days calendar. Each idea can be added as is, edited first, or dismissed. Nothing is saved until you add it. */
export function CampaignSuggest({ onEdit }: { onEdit: (draft: Partial<Campaign>) => void }) {
  const qc = useQueryClient();
  const s = useSuggestion<CampaignResult>("campaigns");
  const [done, setDone] = useState<Record<string, "added" | "dismissed" | "edited">>({});
  const add = useMutation({
    mutationFn: (c: CampaignIdea) => api("/content/campaigns", { method: "POST", json: {
      name: c.name, brief: c.brief, starts_on: c.starts_on, ends_on: c.ends_on, boost: c.boost, active: true } }),
    onSuccess: (_r, c) => { setDone((d) => ({ ...d, [c.name]: "added" })); qc.invalidateQueries({ queryKey: ["campaigns"] }); },
  });
  const ideas = s.result?.suggested ?? [];
  const open = ideas.filter((c) => !done[c.name]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SuggestButton onClick={() => { setDone({}); s.start(); }} busy={s.busy} label={s.result ? "Suggest again" : "Suggest campaigns with AI"} />
        {s.busy && s.progress && <span className="text-sm text-muted">{s.progress}</span>}
        {!s.busy && !s.result && <span className="text-sm text-muted">Plans the next 4 months from your offerings, market research and upcoming festivals and special days.</span>}
      </div>
      {s.error && <Alert>{s.error}</Alert>}
      {add.error && <Alert>{(add.error as Error).message}</Alert>}
      {s.result && (
        <div className="rounded-xl border border-primary-500/30 bg-brand-tint/40 p-4">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div>
              <h3 className="font-semibold">AI campaign plan · {day(s.result.window.start)} – {day(s.result.window.end)}</h3>
              {s.result.notes && <p className="text-sm text-muted">{s.result.notes}</p>}
            </div>
            <div className="flex gap-2">
              {open.length > 1 && <Button onClick={() => open.forEach((c) => add.mutate(c))} disabled={add.isPending}><Check size={15} />Add all {open.length}</Button>}
              <Button variant="ghost" onClick={s.clear}>Close</Button>
            </div>
          </div>
          <ul className="grid gap-3 lg:grid-cols-2">
            {ideas.map((c) => {
              const state = done[c.name];
              return (
                <li key={c.name} className={`flex min-w-0 flex-col rounded-lg border border-line bg-surface p-4 ${state ? "opacity-60" : ""}`}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold">{c.name}</span>
                    {c.goal && <Badge tone="brand">{GOAL[c.goal] ?? c.goal}</Badge>}
                    {c.occasion && <Badge>{c.occasion}</Badge>}
                  </div>
                  <div className="mt-1 flex items-center gap-1 text-sm text-muted">
                    <CalendarRange size={14} />
                    {c.starts_on ? `${day(c.starts_on)}${c.ends_on ? ` – ${day(c.ends_on)}` : ""}` : "No dates"} · {c.boost}% of posts
                    {c.channels.length > 0 && <> · {c.channels.join(", ")}</>}
                  </div>
                  {c.reason && <p className="mt-2 text-sm text-ink/80 [overflow-wrap:anywhere]"><span className="font-medium">Why now:</span> {c.reason}</p>}
                  {c.brief && <p className="mt-1 line-clamp-4 whitespace-pre-line text-sm text-muted [overflow-wrap:anywhere]">{c.brief.split("\nPost ideas:")[0]}</p>}
                  {c.post_ideas.length > 0 && (
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm [overflow-wrap:anywhere]">{c.post_ideas.map((i) => <li key={i}>{i}</li>)}</ul>
                  )}
                  <div className="mt-auto flex flex-wrap gap-2 pt-3">
                    {state === "added" ? <span className="inline-flex items-center gap-1 text-sm font-medium text-emerald-700"><Check size={15} />Added</span>
                      : state === "dismissed" ? <span className="text-sm text-muted">Dismissed</span>
                      : state === "edited" ? <span className="text-sm text-muted">Opened for editing</span> : (
                      <>
                        <Button onClick={() => add.mutate(c)} disabled={add.isPending}><Check size={15} />Add</Button>
                        <Button variant="secondary" onClick={() => { setDone((d) => ({ ...d, [c.name]: "edited" })); onEdit(c); }}><Pencil size={14} />Edit first</Button>
                        <Button variant="ghost" onClick={() => setDone((d) => ({ ...d, [c.name]: "dismissed" }))}><X size={15} />Dismiss</Button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
