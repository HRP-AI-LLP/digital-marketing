import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Search, Sparkles, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, EmptyState, Field, Input, Modal, Spinner, Toggle } from "../../components/ui";
import { AnswerModal, DotLegend, MentionDot } from "./shared";
import type { Prompt, PromptsResponse } from "./types";

type Suggestion = { text: string; topic: string; reason: string };

function SuggestModal({ onClose, room }: { onClose: () => void; room: number }) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>("/aivis/prompts/suggest", { method: "POST", json: { count: 15 } }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const job = useJob(jobId, (j) => {
    const list = ((j.result?.prompts as Suggestion[]) ?? []);
    setPicked(new Set(list.map((_, i) => i).slice(0, room)));
  });
  const items = (job?.result?.prompts as Suggestion[] | undefined) ?? [];
  const add = useMutation({
    mutationFn: () => api("/aivis/prompts/bulk", { method: "POST", json: { prompts: items.filter((_, i) => picked.has(i)).map((s) => ({ ...s, source: "ai" })) } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["aivis"] }); onClose(); },
  });
  const running = !!jobId && (!job || job.status === "queued" || job.status === "running");
  return (
    <Modal title="Suggest prompts with AI" onClose={onClose} wide>
      {!jobId && (
        <div className="space-y-3">
          <p className="text-sm text-muted">AI reads your company profile, offerings and competitors and suggests questions buyers ask assistants like ChatGPT, Gemini or Perplexity. You choose which ones to track.</p>
          {start.error && <Alert>{(start.error as Error).message}</Alert>}
          <Button onClick={() => start.mutate()} disabled={start.isPending}><Sparkles size={15} /> Suggest prompts</Button>
        </div>
      )}
      {running && <Spinner label={job?.progress || "Thinking of the questions your buyers ask…"} />}
      {job?.status === "failed" && <Alert>{job.error}</Alert>}
      {job?.status === "done" && (
        items.length ? (
          <div className="space-y-3">
            <ul className="max-h-[50vh] space-y-1.5 overflow-y-auto">
              {items.map((s, i) => (
                <li key={s.text}>
                  <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line px-3 py-2 hover:border-brand/40">
                    <input type="checkbox" className="mt-1 h-4 w-4" checked={picked.has(i)}
                      onChange={(e) => { const n = new Set(picked); e.target.checked ? n.add(i) : n.delete(i); setPicked(n); }} />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium [overflow-wrap:anywhere]">{s.text}</span>
                      <span className="block text-xs text-muted">{s.topic}{s.reason ? ` · ${s.reason}` : ""}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            {picked.size > room && <Alert tone="warn">You can add {room} more prompt{room === 1 ? "" : "s"}. Untick some.</Alert>}
            {add.error && <Alert>{(add.error as Error).message}</Alert>}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button onClick={() => add.mutate()} disabled={!picked.size || picked.size > room || add.isPending}>Track {picked.size} prompt{picked.size === 1 ? "" : "s"}</Button>
            </div>
          </div>
        ) : <p className="text-sm text-muted">No new suggestions this time. Try again, or add your own.</p>
      )}
    </Modal>
  );
}

function EditModal({ p, onClose }: { p: Prompt; onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState(p.text);
  const [topic, setTopic] = useState(p.topic);
  const save = useMutation({
    mutationFn: () => api(`/aivis/prompts/${p.id}`, { method: "PATCH", json: { text, topic } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["aivis"] }); onClose(); },
  });
  return (
    <Modal title="Edit prompt" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Question"><Input value={text} onChange={(e) => setText(e.target.value)} maxLength={300} /></Field>
        <Field label="Topic"><Input value={topic} onChange={(e) => setTopic(e.target.value)} maxLength={60} /></Field>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={save.isPending}>Save</Button></div>
      </form>
    </Modal>
  );
}

export default function PromptsTab({ brandTerms, initialQuery = "" }: { brandTerms: string[]; initialQuery?: string }) {
  const { can } = useAuth();
  const canEdit = can("aivis.manage");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["aivis", "prompts"], queryFn: () => api<PromptsResponse>("/aivis/prompts") });
  const [text, setText] = useState("");
  const [topic, setTopic] = useState("");
  const [filter, setFilter] = useState(initialQuery);
  const [onlyMissed, setOnlyMissed] = useState(false);
  const [suggest, setSuggest] = useState(false);
  const [editing, setEditing] = useState<Prompt | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => api("/aivis/prompts", { method: "POST", json: { text, topic: topic || undefined } }),
    onSuccess: () => { setText(""); qc.invalidateQueries({ queryKey: ["aivis"] }); },
  });
  const patch = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api(`/aivis/prompts/${id}`, { method: "PATCH", json: { enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["aivis"] }),
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`/aivis/prompts/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["aivis"] }),
  });

  const data = q.data;
  const groups = useMemo(() => {
    const out = new Map<string, Prompt[]>();
    for (const p of data?.prompts ?? []) {
      if (filter && !p.text.toLowerCase().includes(filter.toLowerCase()) && !p.topic.toLowerCase().includes(filter.toLowerCase())) continue;
      if (onlyMissed && (!p.results.length || p.results.some((r) => r.mentioned) || !p.results.some((r) => r.competitors.length))) continue;
      out.set(p.topic, [...(out.get(p.topic) ?? []), p]);
    }
    return [...out.entries()];
  }, [data, filter, onlyMissed]);

  if (q.isLoading) return <Spinner label="Loading prompts…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  if (!data) return null;
  const room = data.max - data.prompts.length;
  const topics = [...new Set(data.prompts.map((p) => p.topic))];

  const onAdd = (e: FormEvent) => { e.preventDefault(); if (text.trim()) add.mutate(); };

  return (
    <div className="space-y-5">
      {canEdit && (
        <form onSubmit={onAdd} className="rounded-xl border border-line bg-surface p-4">
          <div className="grid gap-2 md:grid-cols-[1fr_200px_auto_auto]">
            <Input aria-label="New prompt" placeholder="A question your buyers ask, e.g. “best HRMS software for mid-size companies in India”"
              value={text} onChange={(e) => setText(e.target.value)} maxLength={300} />
            <Input aria-label="Topic" placeholder="Topic (optional)" list="aivis-topics" value={topic} onChange={(e) => setTopic(e.target.value)} maxLength={60} />
            <datalist id="aivis-topics">{topics.map((t) => <option key={t} value={t} />)}</datalist>
            <Button disabled={!text.trim() || add.isPending || room <= 0}><Plus size={15} /> Add</Button>
            <Button type="button" variant="secondary" onClick={() => setSuggest(true)} disabled={room <= 0}><Sparkles size={15} /> Suggest with AI</Button>
          </div>
          <p className="mt-2 text-xs text-muted">{data.prompts.length} of {data.max} prompts. Each check asks every active prompt once on each engine.</p>
          {add.error && <div className="mt-2"><Alert>{(add.error as Error).message}</Alert></div>}
        </form>
      )}

      {data.prompts.length === 0 ? (
        <EmptyState title="No prompts yet" body="Add the questions buyers type into AI assistants when they look for what you sell, or let AI suggest them from your company profile." />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative w-full sm:w-64">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
              <Input aria-label="Filter prompts" placeholder="Filter prompts" value={filter} onChange={(e) => setFilter(e.target.value)} className="w-full pl-9" />
            </div>
            <Toggle checked={onlyMissed} onChange={setOnlyMissed} label="Only prompts where competitors beat you" />
            <div className="sm:ml-auto"><DotLegend /></div>
          </div>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-canvas text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">Prompt</th>
                  {data.engines.map((e) => <th key={e.key} className="px-2 py-2 text-center font-medium" title={e.label}><span className="line-clamp-2 max-w-[110px]">{e.label}</span></th>)}
                  {canEdit && <th className="px-4 py-2" />}
                </tr>
              </thead>
              {groups.map(([t, list]) => (
                <tbody key={t}>
                  <tr className="border-t border-line bg-canvas/60"><td colSpan={data.engines.length + 2} className="px-4 py-1.5 text-xs font-semibold text-muted">{t} · {list.length}</td></tr>
                  {list.map((p) => (
                    <tr key={p.id} className={`border-t border-line ${p.enabled ? "" : "opacity-60"}`}>
                      <td className="max-w-[420px] px-4 py-2.5">
                        <div className="[overflow-wrap:anywhere]">{p.text}</div>
                        <div className="mt-0.5 flex flex-wrap gap-1.5">
                          {p.source === "ai" && <Badge>AI suggested</Badge>}
                          {!p.enabled && <Badge>Paused</Badge>}
                        </div>
                      </td>
                      {data.engines.map((e) => (
                        <td key={e.key} className="px-2 py-2.5 text-center">
                          <MentionDot r={p.results.find((r) => r.engine_key === e.key)} onOpen={setAnswer} />
                        </td>
                      ))}
                      {canEdit && (
                        <td className="whitespace-nowrap px-4 py-2.5 text-right">
                          <button className="mr-2 text-xs text-muted hover:text-ink" onClick={() => patch.mutate({ id: p.id, enabled: !p.enabled })}>{p.enabled ? "Pause" : "Resume"}</button>
                          <button aria-label="Edit prompt" className="mr-1 rounded p-1 text-muted hover:text-ink" onClick={() => setEditing(p)}><Pencil size={15} /></button>
                          <button aria-label="Delete prompt" className="rounded p-1 text-muted hover:text-danger"
                            onClick={() => { if (confirm("Stop tracking this prompt? Past answers stay in earlier checks.")) del.mutate(p.id); }}><Trash2 size={15} /></button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
            {!groups.length && <p className="px-4 py-6 text-center text-sm text-muted">No prompts match.</p>}
          </div>
          {!data.run_id && <p className="text-sm text-muted">Results appear here after the first check.</p>}
        </>
      )}
      {suggest && <SuggestModal onClose={() => setSuggest(false)} room={room} />}
      {editing && <EditModal p={editing} onClose={() => setEditing(null)} />}
      {answer && <AnswerModal id={answer} onClose={() => setAnswer(null)} brandTerms={brandTerms} />}
    </div>
  );
}
