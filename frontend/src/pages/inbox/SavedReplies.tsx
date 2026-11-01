import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, MessageSquareText, Pencil, Plus, Search, Settings2, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Button, EmptyState, Field, Input, Modal, Spinner, Textarea } from "../../components/ui";

export type SavedReply = {
  id: string; title: string; text: string; tags: string[]; platforms: string[]; usage_count: number;
  last_used_at: string | null;
};
type SavedReplies = { items: SavedReply[]; variables: Record<string, string>; platforms: { id: string; label: string }[]; can_manage: boolean };
type UseResult = { text: string; missing: string[]; missing_labels: string[] };

const KEY = ["saved-replies"];

function useSavedReplies(enabled = true) {
  return useQuery({ queryKey: KEY, queryFn: () => api<SavedReplies>("/inbox/saved-replies"), enabled, staleTime: 60_000 });
}

/** "Saved replies" button + dropdown for the reply box. Picking one fills in the variables and inserts the text. */
export function SavedRepliesPicker({ itemId, platform, onInsert }: { itemId: string; platform: string; onInsert: (text: string, note: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [manage, setManage] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const list = useSavedReplies(open || manage);
  const use = useMutation({
    mutationFn: (id: string) => api<UseResult>(`/inbox/saved-replies/${id}/use`, { method: "POST", json: { item_id: itemId } }),
    onSuccess: (r) => {
      setOpen(false);
      onInsert(r.text, r.missing.length ? `Some details were left out because they aren't filled in yet: ${r.missing_labels.join("; ")}. Check the text before sending.` : null);
    },
  });
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const items = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (list.data?.items ?? []).filter((r) => (!r.platforms.length || r.platforms.includes(platform))
      && (!needle || r.title.toLowerCase().includes(needle) || r.text.toLowerCase().includes(needle) || r.tags.some((t) => t.toLowerCase().includes(needle))));
  }, [list.data, q, platform]);

  return (
    <div className="relative" ref={box}>
      <Button type="button" variant="secondary" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
        <MessageSquareText size={15} /> Saved replies
      </Button>
      {open && (
        <div className="absolute bottom-full left-0 z-30 mb-2 w-[min(92vw,380px)] rounded-xl border border-line bg-surface shadow-[0_16px_48px_rgba(6,47,89,0.18)]">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <Search size={14} className="shrink-0 text-muted" aria-hidden />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search saved replies…" aria-label="Search saved replies"
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted/70" />
          </div>
          <div className="max-h-72 overflow-y-auto p-1.5">
            {list.isLoading ? <div className="p-3"><Spinner label="Loading…" /></div>
              : list.error ? <div className="p-2"><Alert>{(list.error as Error).message}</Alert></div>
                : items.length === 0 ? <p className="px-3 py-4 text-center text-sm text-muted">{q ? "No saved reply matches." : "No saved replies for this platform yet."}</p>
                  : (
                    <ul role="listbox" aria-label="Saved replies" className="space-y-0.5">
                      {items.map((r) => (
                        <li key={r.id}>
                          <button type="button" role="option" aria-selected={false} disabled={use.isPending} onClick={() => use.mutate(r.id)}
                            className="w-full min-w-0 rounded-lg px-3 py-2 text-left hover:bg-canvas disabled:opacity-60">
                            <span className="block text-sm font-medium">{r.title}</span>
                            <span className="line-clamp-2 block text-xs text-muted [overflow-wrap:anywhere]">{r.text}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
            {use.error && <div className="p-2"><Alert>{(use.error as Error).message}</Alert></div>}
          </div>
          {list.data?.can_manage && (
            <div className="border-t border-line px-3 py-2">
              <button type="button" className="inline-flex items-center gap-1.5 text-xs font-medium text-primary-700 hover:underline"
                onClick={() => { setOpen(false); setManage(true); }}>
                <Settings2 size={13} /> Manage saved replies
              </button>
            </div>
          )}
        </div>
      )}
      {manage && <ManageSavedReplies onClose={() => setManage(false)} />}
    </div>
  );
}

type Form = { id?: string; title: string; text: string; tags: string; platforms: string[] };
const empty: Form = { title: "", text: "", tags: "", platforms: [] };

/** List, add, edit and delete the organization's saved replies (needs inbox.manage). */
export function ManageSavedReplies({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const list = useSavedReplies();
  const [form, setForm] = useState<Form | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);
  const done = () => { qc.invalidateQueries({ queryKey: KEY }); setForm(null); };
  const save = useMutation({
    mutationFn: (f: Form) => {
      const json = { title: f.title, text: f.text, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean), platforms: f.platforms };
      return f.id ? api(`/inbox/saved-replies/${f.id}`, { method: "PATCH", json }) : api("/inbox/saved-replies", { method: "POST", json });
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`/inbox/saved-replies/${id}`, { method: "DELETE" }), onSuccess: done });
  const d = list.data;
  const platformLabel = (id: string) => d?.platforms.find((p) => p.id === id)?.label ?? id;
  const addVar = (v: string) => {
    if (!form) return;
    const el = textRef.current;
    const at = el ? el.selectionStart : form.text.length;
    const next = form.text.slice(0, at) + `{${v}}` + form.text.slice(el ? el.selectionEnd : at);
    setForm({ ...form, text: next });
  };

  return (
    <Modal title="Saved replies" onClose={onClose} wide>
      {list.isLoading ? <Spinner label="Loading…" /> : list.error ? <Alert>{(list.error as Error).message}</Alert> : d && (form ? (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(form); }}>
          {save.error && <Alert>{(save.error as Error).message}</Alert>}
          <Field label="Name"><Input required maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="For example: Thank you" /></Field>
          <Field label="Reply text" hint="Add details that change each time with the buttons below. They are filled in when you use the reply.">
            <Textarea required rows={5} maxLength={5000} value={form.text}
              onFocus={(e) => { textRef.current = e.currentTarget; }} onSelect={(e) => { textRef.current = e.currentTarget; }}
              onChange={(e) => setForm({ ...form, text: e.target.value })} />
          </Field>
          <div className="-mt-2 flex flex-wrap gap-1.5">
            {Object.entries(d.variables).map(([k, label]) => (
              <button key={k} type="button" title={label} onClick={() => addVar(k)}
                className="rounded-md border border-line px-2 py-0.5 font-mono text-xs text-ink hover:border-primary-400 hover:bg-brand-tint">{`{${k}}`}</button>
            ))}
          </div>
          <Field label="Tags (optional)" hint="Separate with commas. Used when searching."><Input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="support, pricing" /></Field>
          <fieldset>
            <legend className="mb-1 text-sm font-medium">Show for</legend>
            <p className="mb-2 text-xs text-muted">Leave all unticked to offer it on every platform.</p>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {d.platforms.map((p) => (
                <label key={p.id} className="inline-flex items-center gap-2 text-sm">
                  <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={form.platforms.includes(p.id)}
                    onChange={(e) => setForm({ ...form, platforms: e.target.checked ? [...form.platforms, p.id] : form.platforms.filter((x) => x !== p.id) })} />
                  {p.label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setForm(null)}>Cancel</Button>
            <Button type="submit" disabled={!form.title.trim() || !form.text.trim() || save.isPending}>{save.isPending ? "Saving…" : form.id ? "Save changes" : "Add reply"}</Button>
          </div>
        </form>
      ) : (
        <div className="space-y-3">
          {remove.error && <Alert>{(remove.error as Error).message}</Alert>}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted">Ready-made answers your team can drop into any reply.</p>
            {d.can_manage && <Button onClick={() => setForm({ ...empty })}><Plus size={15} /> Add a reply</Button>}
          </div>
          {d.items.length === 0 ? (
            <EmptyState title="No saved replies yet" body="Add answers you give often, like thanks, support contacts or pricing questions."
              action={d.can_manage ? <Button onClick={() => setForm({ ...empty })}><BookmarkPlus size={15} /> Add your first reply</Button> : undefined} />
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line">
              {d.items.map((r) => (
                <li key={r.id} className="flex min-w-0 items-start gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium">{r.title}</span>
                      <span className="text-xs text-muted">used {r.usage_count} time{r.usage_count === 1 ? "" : "s"}</span>
                    </div>
                    <p className="mt-0.5 line-clamp-3 whitespace-pre-line text-sm text-slate-700 [overflow-wrap:anywhere]">{r.text}</p>
                    {(r.tags.length > 0 || r.platforms.length > 0) && (
                      <p className="mt-1 text-xs text-muted">{[...r.tags.map((t) => `#${t}`), ...(r.platforms.length ? [`Only on ${r.platforms.map(platformLabel).join(", ")}`] : [])].join(" · ")}</p>
                    )}
                  </div>
                  {d.can_manage && (
                    <div className="flex shrink-0 gap-1">
                      <button type="button" aria-label={`Edit ${r.title}`} title="Edit" className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-ink/5 hover:text-ink"
                        onClick={() => setForm({ id: r.id, title: r.title, text: r.text, tags: r.tags.join(", "), platforms: r.platforms })}><Pencil size={15} /></button>
                      <button type="button" aria-label={`Delete ${r.title}`} title="Delete" className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-danger-tint hover:text-danger"
                        disabled={remove.isPending} onClick={() => { if (confirm(`Delete the saved reply “${r.title}”?`)) remove.mutate(r.id); }}><Trash2 size={15} /></button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </Modal>
  );
}
