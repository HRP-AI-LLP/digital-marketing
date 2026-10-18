import { useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Field, Modal, Spinner, Toggle } from "../../components/ui";
import { ago, type Listening, type SourceStatus } from "./shared";

export function TagInput({ id, values, onChange, placeholder, max }: { id: string; values: string[]; onChange: (v: string[]) => void; placeholder: string; max?: number }) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
    const next = [...values];
    for (const p of parts) if (!next.some((v) => v.toLowerCase() === p.toLowerCase())) next.push(p);
    onChange(max ? next.slice(0, max) : next);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === "Enter" || e.key === ",") && draft.trim()) { e.preventDefault(); add(draft); }
    if (e.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
  };
  return (
    <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-1.5 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500/20">
      {values.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-md bg-primary-50 py-0.5 pl-2 pr-1 text-sm text-primary-800">
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))} className="rounded p-0.5 hover:bg-primary-100"><X size={12} /></button>
        </span>
      ))}
      <input id={id} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} onBlur={() => draft.trim() && add(draft)}
        placeholder={values.length ? "" : placeholder} className="min-w-[8rem] flex-1 bg-transparent px-1 text-[15px] outline-none placeholder:text-muted/70" />
    </div>
  );
}

function every(min: number) {
  return min < 60 ? `every ${min} minutes` : min === 60 ? "every hour" : `every ${Math.round(min / 60)} hours`;
}

export default function ListeningSettings({ onClose, sources }: { onClose: () => void; sources: SourceStatus[] }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const editable = can("inbox.manage");
  const q = useQuery({ queryKey: ["inbox-listening"], queryFn: () => api<Listening>("/inbox/listening") });
  const [form, setForm] = useState<Listening | null>(null);
  const f = form ?? q.data ?? null;
  const save = useMutation({
    mutationFn: (body: Listening) => api<Listening>("/inbox/listening", { method: "PUT", json: {
      keywords: body.keywords, excluded: body.excluded, languages: body.languages, sources: body.sources, listening_enabled: body.listening_enabled } }),
    onSuccess: (d) => {
      qc.setQueryData(["inbox-listening"], d);
      qc.invalidateQueries({ queryKey: ["inbox-status"] });
      onClose();
    },
  });
  const set = (patch: Partial<Listening>) => f && setForm({ ...f, ...patch });

  return (
    <Modal title="Listening settings" onClose={onClose} wide>
      {!f ? <Spinner label="Loading…" /> : (
        <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); save.mutate(f); }}>
          <p className="text-sm text-muted">
            We search for these words with official APIs and add what we find to the Mentions tab. Use your brand and product
            names, hashtags you own, and (if you like) competitor names.
          </p>
          {save.error && <Alert>{(save.error as Error).message}</Alert>}
          <Toggle checked={f.listening_enabled} onChange={(v) => set({ listening_enabled: v })} label="Look for mentions" disabled={!editable} />
          <Field label="Keywords to track" hint={`Press Enter after each one. Up to ${f.max_keywords} are searched; hashtags like #hrpai also search Instagram and Mastodon tags.`}>
            <TagInput id="kw" values={f.keywords} onChange={(v) => set({ keywords: v })} placeholder="HRP AI, hrpai, #hrpai, HRP HRMS" max={f.max_keywords * 2} />
          </Field>
          <Field label="Leave out results containing" hint="For example casino, job offer, or a person who shares your brand name.">
            <TagInput id="ex" values={f.excluded} onChange={(v) => set({ excluded: v })} placeholder="Words to exclude" />
          </Field>
          <Field label="Languages" hint="Language codes such as en or hi. Leave empty for all languages.">
            <TagInput id="lang" values={f.languages} onChange={(v) => set({ languages: v.map((x) => x.toLowerCase()) })} placeholder="en, hi" max={10} />
          </Field>
          <fieldset>
            <legend className="mb-1.5 text-sm font-medium">Where to look</legend>
            <ul className="divide-y divide-line rounded-lg border border-line">
              {Object.entries(f.source_info).map(([id, info]) => {
                const st = sources.find((s) => s.id === id);
                const unavailable = st && !st.available;
                return (
                  <li key={id} className="flex items-start gap-3 px-3 py-2.5">
                    <input id={`src-${id}`} type="checkbox" className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]" disabled={!editable}
                      checked={!!f.sources[id]} onChange={(e) => set({ sources: { ...f.sources, [id]: e.target.checked } })} />
                    <label htmlFor={`src-${id}`} className="min-w-0 flex-1 text-sm">
                      <span className="font-medium">{info.label}</span>
                      <span className="text-muted"> · {every(info.interval_minutes)}</span>
                      {id === "youtube" && <span className="block text-xs text-muted">Uses 100 units of your daily YouTube quota per keyword (first 3 keywords).</span>}
                      {id === "instagram" && <span className="block text-xs text-muted">Hashtags only. Needs Meta's Instagram Public Content Access feature; skipped without it.</span>}
                      {unavailable && <span className="block text-xs text-amber-700">Connect {info.needs === "youtube" ? "Google (YouTube)" : info.needs} in Social accounts first.</span>}
                      {st?.last_run_at && (
                        <span className={`block text-xs ${st.ok === false ? "text-danger" : "text-muted"}`}>
                          Last checked {ago(st.last_run_at)}{st.ok === false && st.message ? `: ${st.message}` : st.found ? `, ${st.found} new` : ""}
                        </span>
                      )}
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
          <div className="flex justify-end gap-2 border-t border-line pt-4">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            {editable && <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>}
          </div>
        </form>
      )}
    </Modal>
  );
}

