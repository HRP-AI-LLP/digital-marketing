import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Play, Plus, Trash2, Wand2 } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Spinner, Toggle } from "../../components/ui";
import { TagInput } from "../inbox/ListeningSettings";
import { ago } from "../inbox/shared";
import { KIND_LABEL, type ListenSettings, type ListenStatus, type Query, type QueryKind, type SourceInfo, type Suggestion } from "./types";

type Draft = Omit<Query, "id" | "last_run_at" | "found_last_run" | "source_status" | "mentions_30d">;
const BLANK: Draft = { name: "", kind: "brand", terms: [], context_terms: [], exclude: [], languages: [], countries: [], sources: {}, competitor_id: null, enabled: true };
type Competitor = { id: string; name: string; status: string };

function body(d: Draft) {
  const { name, kind, terms, context_terms, exclude, languages, countries, sources, competitor_id, enabled } = d;
  return { name, kind, terms, context_terms, exclude, languages, countries, sources, competitor_id, enabled };
}

function invalidate(qc: ReturnType<typeof useQueryClient>) {
  for (const k of ["listening-queries", "listening-status", "listening-suggestions", "listening-mentions", "listening-overview"]) qc.invalidateQueries({ queryKey: [k] });
}

function TopicEditor({ initial, id, sources, onClose }: { initial: Draft; id?: string; sources: SourceInfo[]; onClose: () => void }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [d, setD] = useState<Draft>(initial);
  const set = (p: Partial<Draft>) => setD({ ...d, ...p });
  const comps = useQuery({ queryKey: ["intel-competitors"], queryFn: () => api<Competitor[]>("/intel/competitors"), enabled: can("intel.view") });
  const save = useMutation({
    mutationFn: () => api<Query>(id ? `/listening/queries/${id}` : "/listening/queries", { method: id ? "PUT" : "POST", json: body(d) }),
    onSuccess: () => { invalidate(qc); onClose(); },
  });
  return (
    <Modal title={id ? "Edit topic" : "New topic"} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name"><Input value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="HRP AI" maxLength={120} required /></Field>
          <Field label="What is it?">
            <Select value={d.kind} onChange={(e) => set({ kind: e.target.value as QueryKind })}>
              {(Object.keys(KIND_LABEL) as QueryKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </Select>
          </Field>
        </div>
        {d.kind === "competitor" && comps.data && (
          <Field label="Competitor" hint="Links the topic to your competitor list for share of voice.">
            <Select value={d.competitor_id ?? ""} onChange={(e) => set({ competitor_id: e.target.value || null })}>
              <option value="">Not linked</option>
              {comps.data.filter((c) => c.status !== "rejected").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          </Field>
        )}
        <Field label="Listen for any of these" hint="Press Enter after each one. Names, products, website, handles or #hashtags. Whole words only, so “HRP” won't match “sharp”.">
          <TagInput id="terms" values={d.terms} onChange={(v) => set({ terms: v })} placeholder="HRP AI, hrpai.in, #hrpai" max={20} />
        </Field>
        <Field label="…that also mention one of these (optional)" hint="Use this for short or common names. For example HRP plus HRMS, payroll or software.">
          <TagInput id="ctx" values={d.context_terms} onChange={(v) => set({ context_terms: v })} placeholder="HRMS, payroll, software" max={20} />
        </Field>
        <Field label="…and never these" hint="Leave out results that contain any of these words.">
          <TagInput id="ex" values={d.exclude} onChange={(v) => set({ exclude: v })} placeholder="hospital, casino" max={40} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Languages (optional)" hint="Two-letter codes such as en or hi.">
            <TagInput id="lang" values={d.languages} onChange={(v) => set({ languages: v.map((x) => x.toLowerCase()) })} placeholder="en" max={5} />
          </Field>
          <Field label="Countries for news (optional)" hint="Two-letter codes such as IN or US. Default: your country.">
            <TagInput id="cty" values={d.countries} onChange={(v) => set({ countries: v.map((x) => x.toUpperCase()) })} placeholder="IN" max={3} />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Search this topic on</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {sources.filter((s) => s.enabled).map((s) => (
              <label key={s.id} className={`inline-flex items-center gap-2 text-sm ${s.available ? "" : "opacity-50"}`}>
                <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={d.sources[s.id] !== false}
                  onChange={(e) => set({ sources: { ...d.sources, [s.id]: e.target.checked } })} />
                {s.label}
              </label>
            ))}
          </div>
        </fieldset>
        <Toggle checked={d.enabled} onChange={(v) => set({ enabled: v })} label="Listen for this topic" />
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending || !d.name.trim() || !d.terms.length}>{save.isPending ? "Saving…" : "Save topic"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function Suggestions({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["listening-suggestions"], queryFn: () => api<Suggestion[]>("/listening/suggestions") });
  const add = useMutation({
    mutationFn: (names?: string[]) => api<Query[]>("/listening/queries/starter", { method: "POST", json: { names: names ?? null } }),
    onSuccess: () => invalidate(qc),
  });
  const open = (q.data ?? []).filter((s) => !s.exists);
  if (!open.length) return null;
  return (
    <Card className="mb-4 border-secondary-300 bg-accent-tint/40">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><Wand2 size={16} /> Suggested topics</h3>
          <p className="text-sm text-muted">From your organization profile, products and competitor list. Check the words before adding.</p>
        </div>
        {canManage && open.length > 1 && <Button disabled={add.isPending} onClick={() => add.mutate(undefined)}><Plus size={15} /> Add all {open.length}</Button>}
      </div>
      {add.error && <div className="mt-3"><Alert>{(add.error as Error).message}</Alert></div>}
      <ul className="mt-3 grid gap-2 md:grid-cols-2">
        {open.map((s) => (
          <li key={s.name} className="rounded-lg border border-line bg-surface p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">{s.name} <Badge>{KIND_LABEL[s.kind]}</Badge></p>
                <p className="mt-1 text-sm [overflow-wrap:anywhere]">{s.terms.join(" · ")}</p>
                <p className="mt-1 text-xs text-muted">{s.why}</p>
              </div>
              {canManage && <Button variant="secondary" className="h-8 shrink-0 px-3" disabled={add.isPending} onClick={() => add.mutate([s.name])}>Add</Button>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function TopicRow({ q, canManage, onEdit, onRun }: { q: Query; canManage: boolean; onEdit: () => void; onRun: () => void }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const del = useMutation({
    mutationFn: (purge: boolean) => api(`/listening/queries/${q.id}?purge=${purge}`, { method: "DELETE" }),
    onSuccess: () => invalidate(qc),
  });
  const problems = Object.entries(q.source_status).filter(([, v]) => v.ok === false);
  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{q.name} <Badge tone={q.kind === "brand" ? "brand" : q.kind === "competitor" ? "accent" : "neutral"}>{KIND_LABEL[q.kind]}</Badge>
            {!q.enabled && <span className="ml-2 text-xs text-muted">Paused</span>}</p>
          <p className="mt-1 text-sm [overflow-wrap:anywhere]">{q.terms.join(" · ")}</p>
          {q.context_terms.length > 0 && <p className="text-xs text-muted [overflow-wrap:anywhere]">Must also mention: {q.context_terms.join(", ")}</p>}
          {q.exclude.length > 0 && <p className="text-xs text-muted [overflow-wrap:anywhere]">Leaves out: {q.exclude.join(", ")}</p>}
          <p className="mt-1 text-xs text-muted">
            {q.mentions_30d} mentions in 30 days{q.last_run_at ? ` · searched ${ago(q.last_run_at)}` : " · not searched yet"}
            {q.found_last_run ? ` · ${q.found_last_run} new last time` : ""}
          </p>
          {problems.length > 0 && <p className="mt-1 text-xs text-amber-800 [overflow-wrap:anywhere]">{problems.map(([k, v]) => `${k.replace("_", " ")}: ${v.message}`).join(" · ")}</p>}
        </div>
        {canManage && (
          <div className="flex gap-1.5">
            <Button variant="ghost" className="h-8 px-2.5" onClick={onRun} title="Search this topic now"><Play size={14} /> Run</Button>
            <Button variant="ghost" className="h-8 px-2.5" onClick={onEdit}><Pencil size={14} /> Edit</Button>
            <Button variant="ghost" className="h-8 px-2.5 text-danger" onClick={() => setConfirm(true)}><Trash2 size={14} /></Button>
          </div>
        )}
      </div>
      {confirm && (
        <Modal title={`Delete “${q.name}”?`} onClose={() => setConfirm(false)}>
          <p className="text-sm text-muted">We'll stop listening for this topic. You can keep the mentions it found, or remove the ones no other topic found.</p>
          {del.error && <div className="mt-3"><Alert>{(del.error as Error).message}</Alert></div>}
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="secondary" disabled={del.isPending} onClick={() => del.mutate(false)}>Delete, keep mentions</Button>
            <Button variant="danger" disabled={del.isPending} onClick={() => del.mutate(true)}>Delete with mentions</Button>
          </div>
        </Modal>
      )}
    </li>
  );
}

function SettingsCard({ status, canManage }: { status: ListenStatus; canManage: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ListenSettings | null>(null);
  const f = form ?? status.settings;
  const set = (p: Partial<ListenSettings>) => setForm({ ...f, ...p });
  const save = useMutation({
    mutationFn: () => api<ListenStatus>("/listening/settings", { method: "PUT", json: f }),
    onSuccess: (d) => { qc.setQueryData(["listening-status"], d); setForm(null); },
  });
  const digest = useMutation({ mutationFn: () => api<{ job_id: string }>("/listening/digest", { method: "POST" }) });
  return (
    <Card>
      <h3 className="font-semibold">Settings</h3>
      <form className="mt-3 space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Toggle checked={f.enabled} onChange={(v) => set({ enabled: v })} label="Search automatically" disabled={!canManage} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="How often">
            <Select value={f.interval_hours} disabled={!canManage} onChange={(e) => set({ interval_hours: Number(e.target.value) })}>
              {[2, 3, 4, 6, 12, 24].map((h) => <option key={h} value={h}>Every {h} hours</option>)}
            </Select>
          </Field>
          <Field label="Summary by email or Telegram" hint="Sent in the app, and by email or Telegram to people who turned those on in Notifications.">
            <Select value={f.digest} disabled={!canManage} onChange={(e) => set({ digest: e.target.value as ListenSettings["digest"] })}>
              <option value="off">Off</option><option value="daily">Every morning</option><option value="weekly">Monday mornings</option>
            </Select>
          </Field>
        </div>
        <Toggle checked={f.spike_alerts} onChange={(v) => set({ spike_alerts: v })} label="Alert me when mentions or negative mentions jump" disabled={!canManage} />
        <Toggle checked={f.use_ai} onChange={(v) => set({ use_ai: v })} label="Use AI to check relevance and sentiment (falls back to keywords)" disabled={!canManage} />
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Sources</legend>
          <p className="mb-2 text-xs text-muted">The free sources work without any key. Paid ones are optional and only used once you add their key.</p>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {status.sources.map((s) => (
              <li key={s.id} className="flex items-start gap-3 px-3 py-2.5">
                <input id={`ls-${s.id}`} type="checkbox" className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]" disabled={!canManage}
                  checked={!!f.sources[s.id]} onChange={(e) => set({ sources: { ...f.sources, [s.id]: e.target.checked } })} />
                <label htmlFor={`ls-${s.id}`} className="min-w-0 flex-1 text-sm">
                  <span className="font-medium">{s.label}</span>
                  {s.paid ? <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800">paid, optional</span>
                    : <span className="ml-2 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700">free</span>}
                  <span className="block text-xs text-muted">{s.note}</span>
                  {!s.available && s.missing && <span className="block text-xs text-amber-800">{s.missing}</span>}
                  {s.last && <span className={`block text-xs ${s.last.ok === false ? "text-danger" : "text-muted"}`}>
                    Last checked {ago(s.last.last_run_at)}{s.last.ok === false && s.last.message ? `: ${s.last.message}` : `, ${s.last.found} new`}</span>}
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
        <Field label="Public Mastodon server (optional)" hint="Used for hashtag timelines when no Mastodon account is connected, for example mastodon.social.">
          <Input value={f.mastodon_instance ?? ""} disabled={!canManage} onChange={(e) => set({ mastodon_instance: e.target.value || null })} placeholder="mastodon.social" />
        </Field>
        {canManage && (
          <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-4">
            <Button type="button" variant="ghost" disabled={digest.isPending} onClick={() => digest.mutate()}>{digest.isSuccess ? "Summary sent" : "Send the summary now"}</Button>
            <Button type="submit" disabled={save.isPending || !form}>{save.isPending ? "Saving…" : "Save settings"}</Button>
          </div>
        )}
      </form>
    </Card>
  );
}

export default function Topics({ status, queries, loading, onRun }: { status: ListenStatus; queries: Query[]; loading: boolean; onRun: (qid?: string) => void }) {
  const { can } = useAuth();
  const canManage = can("listening.manage");
  const [edit, setEdit] = useState<{ d: Draft; id?: string } | null>(null);
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
      <div>
        <Suggestions canManage={canManage} />
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Your topics <span className="text-sm font-normal text-muted">{status.queries} of {status.max_queries}</span></h3>
          {canManage && <Button variant="secondary" onClick={() => setEdit({ d: BLANK })}><Plus size={15} /> New topic</Button>}
        </div>
        {loading ? <Spinner label="Loading…" /> : !queries.length ? (
          <EmptyState title="No topics yet" body="Add the suggested topics above, or create your own: your brand, your products, competitors and the words your customers use." />
        ) : (
          <ul className="space-y-3">
            {queries.map((q) => <TopicRow key={q.id} q={q} canManage={canManage} onRun={() => onRun(q.id)} onEdit={() => setEdit({ d: body(q), id: q.id })} />)}
          </ul>
        )}
      </div>
      <SettingsCard key={JSON.stringify(status.settings)} status={status} canManage={canManage} />
      {edit && <TopicEditor initial={edit.d} id={edit.id} sources={status.sources} onClose={() => setEdit(null)} />}
    </div>
  );
}
