import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, History, Lock, MessageSquare, Pencil, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Spinner } from "../../components/ui";
import { fmt, useTz } from "./shared";

// ====================================================================== types
export type Person = { id: string; name: string; email: string };
export type Comment = {
  id: string; post_id: string; body: string; author: Person | null; mentions: Person[]; resolved: boolean;
  resolved_by: Person | null; resolved_at: string | null; edited_at: string | null; created_at: string; mine: boolean;
};
type Comments = { items: Comment[]; open: number; total: number; can_comment: boolean };
type Fields = { title: string | null; text: string; hashtags: string[]; link_url: string | null; first_comment: string | null; media_ids: string[] };
export type Version = {
  version: number; fields: Fields; reason: string | null; changed_by: Person | null; changed_by_name: string;
  created_at: string; changed: (keyof Fields)[]; current: boolean;
};
type Versions = { items: Version[]; can_restore: boolean; current_fields: Fields };

const FIELD_NAMES: Record<keyof Fields, string> = {
  title: "Title", text: "Text", hashtags: "Hashtags", link_url: "Link", first_comment: "First comment", media_ids: "Media",
};

export function useMembers() {
  return useQuery({ queryKey: ["content-members"], queryFn: () => api<Person[]>("/content/members"), staleTime: 5 * 60_000 });
}

// ====================================================================== @mention input
/** Finds "@partial" right before the caret. */
function mentionQuery(text: string, caret: number): { start: number; q: string } | null {
  const before = text.slice(0, caret);
  const m = /(^|\s)@([^\s@]{0,30}(?: [^\s@]{0,30})?)$/.exec(before);
  if (!m) return null;
  return { start: before.length - m[2].length - 1, q: m[2].toLowerCase() };
}

/** Ids of people whose "@Full Name" is still in the text. */
function mentionedIds(text: string, people: Person[], picked: Set<string>): string[] {
  return people.filter((p) => picked.has(p.id) && text.includes(`@${p.name}`)).map((p) => p.id);
}

function MentionBox({ value, onChange, picked, onPick, people, placeholder, autoFocus, onSubmit }: {
  value: string; onChange: (v: string) => void; picked: Set<string>; onPick: (id: string) => void; people: Person[];
  placeholder?: string; autoFocus?: boolean; onSubmit?: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = useState<{ start: number; q: string } | null>(null);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => {
    if (!query) return [];
    return people.filter((p) => p.name.toLowerCase().includes(query.q) || p.email.toLowerCase().startsWith(query.q)).slice(0, 6);
  }, [query, people]);
  useEffect(() => setActive(0), [query?.q]);

  const update = (v: string, caret: number) => { onChange(v); setQuery(mentionQuery(v, caret)); };
  const choose = (p: Person) => {
    if (!query) return;
    const caret = ref.current?.selectionStart ?? value.length;
    const insert = `@${p.name} `;
    const next = value.slice(0, query.start) + insert + value.slice(caret);
    onChange(next);
    onPick(p.id);
    setQuery(null);
    requestAnimationFrame(() => {
      const pos = query.start + insert.length;
      ref.current?.focus();
      ref.current?.setSelectionRange(pos, pos);
    });
  };
  return (
    <div className="relative">
      <textarea ref={ref} rows={3}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px] placeholder:text-muted/70 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20" value={value} placeholder={placeholder} autoFocus={autoFocus}
        aria-label="Comment" aria-autocomplete="list" aria-expanded={matches.length > 0}
        onChange={(e) => update(e.target.value, e.target.selectionStart)}
        onClick={(e) => setQuery(mentionQuery(value, e.currentTarget.selectionStart))}
        onBlur={() => setTimeout(() => setQuery(null), 150)}
        onKeyDown={(e) => {
          if (matches.length) {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % matches.length); return; }
            if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + matches.length) % matches.length); return; }
            if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); choose(matches[active]); return; }
            if (e.key === "Escape") { setQuery(null); return; }
          }
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && onSubmit) onSubmit();
        }} />
      {matches.length > 0 && (
        <ul role="listbox" className="absolute left-0 right-0 top-full z-20 mt-1 max-h-56 overflow-y-auto rounded-lg border border-line bg-surface py-1 shadow-lg">
          {matches.map((p, i) => (
            <li key={p.id} role="option" aria-selected={i === active}>
              <button type="button" onMouseDown={(e) => { e.preventDefault(); choose(p); }}
                className={`flex w-full min-w-0 flex-col px-3 py-1.5 text-left text-sm ${i === active ? "bg-brand-tint" : "hover:bg-canvas"}`}>
                <span className="font-medium">{p.name}{picked.has(p.id) && <span className="ml-1 text-xs text-muted">(mentioned)</span>}</span>
                <span className="truncate text-xs text-muted">{p.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Comment text with @mentions highlighted. */
function Body({ text, mentions }: { text: string; mentions: Person[] }) {
  if (!mentions.length) return <>{text}</>;
  const names = mentions.map((m) => m.name).sort((a, b) => b.length - a.length).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(@(?:${names.join("|")}))`, "g"));
  return <>{parts.map((p, i) => (i % 2 ? <span key={i} className="rounded bg-brand-tint px-0.5 font-medium text-brand-deep">{p}</span> : p))}</>;
}

// ====================================================================== team comments
function CommentRow({ c, postId, onData, canComment, people }: {
  c: Comment; postId: string; onData: (d: Comments) => void; canComment: boolean; people: Person[];
}) {
  const tz = useTz();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(c.body);
  const [picked, setPicked] = useState<Set<string>>(new Set(c.mentions.map((m) => m.id)));
  const base = `/content/posts/${postId}/comments/${c.id}`;
  const save = useMutation({
    mutationFn: () => api<Comments>(base, { method: "PATCH", json: { body: text, mentions: mentionedIds(text, people, picked) } }),
    onSuccess: (d) => { setEditing(false); onData(d); },
  });
  const resolve = useMutation({ mutationFn: (v: boolean) => api<Comments>(`${base}/resolve`, { method: "POST", json: { resolved: v } }), onSuccess: onData });
  const remove = useMutation({ mutationFn: () => api<Comments>(base, { method: "DELETE" }), onSuccess: onData });
  const err = (save.error || resolve.error || remove.error) as Error | null;
  return (
    <li className={`min-w-0 rounded-lg border px-3 py-2.5 ${c.resolved ? "border-line bg-canvas/60" : "border-line bg-surface"}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
        <span className="font-semibold text-ink">{c.author?.name ?? "Former member"}{c.mine && " (you)"}</span>
        <span>{fmt(c.created_at, tz)}</span>
        {c.edited_at && <span>· edited</span>}
        {c.resolved && <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 size={12} /> Resolved{c.resolved_by ? ` by ${c.resolved_by.name}` : ""}</span>}
      </div>
      {editing ? (
        <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) save.mutate(); }}>
          <MentionBox value={text} onChange={setText} picked={picked} onPick={(id) => setPicked(new Set([...picked, id]))} people={people} autoFocus onSubmit={() => text.trim() && save.mutate()} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => { setEditing(false); setText(c.body); }}>Cancel</Button>
            <Button type="submit" disabled={!text.trim() || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
          </div>
        </form>
      ) : (
        <p className={`mt-1 whitespace-pre-line text-sm [overflow-wrap:anywhere] ${c.resolved ? "text-muted" : ""}`}><Body text={c.body} mentions={c.mentions} /></p>
      )}
      {err && <div className="mt-2"><Alert>{err.message}</Alert></div>}
      {!editing && (canComment || c.mine) && (
        <div className="mt-1.5 flex flex-wrap gap-3 text-xs">
          {canComment && (
            <button type="button" className="font-medium text-primary-700 hover:underline disabled:opacity-50" disabled={resolve.isPending} onClick={() => resolve.mutate(!c.resolved)}>
              {c.resolved ? "Reopen" : "Resolve"}
            </button>
          )}
          {c.mine && <button type="button" className="inline-flex items-center gap-1 text-muted hover:text-ink" onClick={() => setEditing(true)}><Pencil size={12} /> Edit</button>}
          {c.mine && (
            <button type="button" className="inline-flex items-center gap-1 text-muted hover:text-danger" disabled={remove.isPending}
              onClick={() => { if (confirm("Delete this comment?")) remove.mutate(); }}><Trash2 size={12} /> Delete</button>
          )}
        </div>
      )}
    </li>
  );
}

export function TeamComments({ postId }: { postId: string }) {
  const qc = useQueryClient();
  const members = useMembers();
  const people = members.data ?? [];
  const key = ["post-comments", postId];
  const q = useQuery({ queryKey: key, queryFn: () => api<Comments>(`/content/posts/${postId}/comments`), refetchInterval: 60_000 });
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showResolved, setShowResolved] = useState(false);
  const onData = (d: Comments) => {
    qc.setQueryData(key, d);
    qc.invalidateQueries({ queryKey: ["posts"] });
    qc.invalidateQueries({ queryKey: ["approvals"] });
  };
  const add = useMutation({
    mutationFn: () => api<Comments>(`/content/posts/${postId}/comments`, { method: "POST", json: { body: text, mentions: mentionedIds(text, people, picked) } }),
    onSuccess: (d) => { setText(""); setPicked(new Set()); onData(d); },
  });
  useEffect(() => {
    if (window.location.hash === "#comments" && q.data) document.getElementById("comments")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [q.data]);

  const d = q.data;
  const open = d?.items.filter((c) => !c.resolved) ?? [];
  const resolved = d?.items.filter((c) => c.resolved) ?? [];
  return (
    <section id="comments" className="scroll-mt-4 rounded-xl border border-line bg-surface p-5">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="inline-flex items-center gap-2 font-semibold"><MessageSquare size={17} className="text-secondary-600" /> Team comments</h2>
        {d && d.total > 0 && <Badge tone={d.open ? "accent" : "neutral"}>{d.open ? `${d.open} open` : "All resolved"}</Badge>}
      </div>
      <p className="mb-3 inline-flex items-center gap-1 text-xs text-muted"><Lock size={12} aria-hidden /> Only your team sees these. They are never published.</p>
      {q.isLoading ? <Spinner label="Loading comments…" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : d && (
        <>
          {open.length === 0 && resolved.length === 0 && <p className="mb-3 text-sm text-muted">No comments yet. Ask a question or leave a note for the team — type @ to mention someone.</p>}
          {open.length > 0 && (
            <ul className="mb-3 space-y-2">{open.map((c) => <CommentRow key={c.id} c={c} postId={postId} onData={onData} canComment={d.can_comment} people={people} />)}</ul>
          )}
          {resolved.length > 0 && (
            <div className="mb-3">
              <button type="button" className="text-xs font-medium text-primary-700 hover:underline" onClick={() => setShowResolved(!showResolved)}>
                {showResolved ? "Hide" : "Show"} {resolved.length} resolved comment{resolved.length === 1 ? "" : "s"}
              </button>
              {showResolved && <ul className="mt-2 space-y-2">{resolved.map((c) => <CommentRow key={c.id} c={c} postId={postId} onData={onData} canComment={d.can_comment} people={people} />)}</ul>}
            </div>
          )}
          {d.can_comment ? (
            <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) add.mutate(); }} className="space-y-2">
              {add.error && <Alert>{(add.error as Error).message}</Alert>}
              <MentionBox value={text} onChange={setText} picked={picked} onPick={(id) => setPicked(new Set([...picked, id]))} people={people}
                placeholder="Write a note for the team… Type @ to mention someone." onSubmit={() => text.trim() && add.mutate()} />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-muted">People you mention get a notification.</span>
                <Button type="submit" disabled={!text.trim() || add.isPending}>{add.isPending ? "Posting…" : "Comment"}</Button>
              </div>
            </form>
          ) : <p className="text-xs text-muted">You can read comments. People who write or approve posts can add them.</p>}
        </>
      )}
    </section>
  );
}

// ====================================================================== word diff
type Piece = { t: string; k: "same" | "add" | "del" };

/** Word-level diff (LCS over words and spaces). Falls back to "all removed / all added" for very long texts. */
export function wordDiff(a: string, b: string): Piece[] {
  const A = a.split(/(\s+)/).filter(Boolean);
  const B = b.split(/(\s+)/).filter(Boolean);
  if (a === b) return A.map((t) => ({ t, k: "same" }));
  if (A.length * B.length > 400_000) return [...(a ? [{ t: a, k: "del" as const }] : []), ...(b ? [{ t: b, k: "add" as const }] : [])];
  const m = A.length, n = B.length;
  const dp: Uint32Array[] = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: Piece[] = [];
  const push = (t: string, k: Piece["k"]) => { const last = out[out.length - 1]; if (last && last.k === k) last.t += t; else out.push({ t, k }); };
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (A[i] === B[j]) { push(A[i], "same"); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) { push(A[i], "del"); i++; } else { push(B[j], "add"); j++; }
  }
  while (i < m) push(A[i++], "del");
  while (j < n) push(B[j++], "add");
  return out;
}

function Diff({ before, after }: { before: string; after: string }) {
  const parts = wordDiff(before, after);
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed [overflow-wrap:anywhere]">
      {parts.map((p, i) => p.k === "same" ? <span key={i}>{p.t}</span>
        : p.k === "add" ? <ins key={i} className="rounded-sm bg-emerald-100 text-emerald-900 no-underline">{p.t}</ins>
          : <del key={i} className="rounded-sm bg-rose-100 text-rose-900">{p.t}</del>)}
    </p>
  );
}

const asText = (f: Fields | undefined, k: keyof Fields): string => {
  if (!f) return "";
  const v = f[k];
  if (k === "hashtags") return (v as string[] | undefined ?? []).map((h) => `#${h}`).join(" ");
  if (k === "media_ids") { const n = (v as string[] | undefined ?? []).length; return n ? `${n} file${n === 1 ? "" : "s"}` : "none"; }
  return (v as string | null) ?? "";
};

function FieldDiff({ label, before, after, children }: { label: string; before?: string; after?: string; children?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-0.5 text-xs font-medium text-muted">{label}</div>
      {children ?? <Diff before={before ?? ""} after={after ?? ""} />}
    </div>
  );
}

function VersionCompare({ from, to, fromLabel, toLabel }: { from: Fields | undefined; to: Fields; fromLabel: string; toLabel: string }) {
  const keys = (Object.keys(FIELD_NAMES) as (keyof Fields)[]).filter((k) => asText(from, k) !== asText(to, k) || (k === "media_ids" && JSON.stringify(from?.media_ids ?? []) !== JSON.stringify(to.media_ids)));
  if (!from) {
    return (
      <div className="space-y-2">
        {to.title && <FieldDiff label="Title" before="" after={to.title}><p className="text-sm [overflow-wrap:anywhere]">{to.title}</p></FieldDiff>}
        <FieldDiff label="Text"><p className="whitespace-pre-wrap text-sm [overflow-wrap:anywhere]">{to.text || <span className="text-muted">No text</span>}</p></FieldDiff>
        {to.hashtags.length > 0 && <FieldDiff label="Hashtags"><p className="text-sm">{asText(to, "hashtags")}</p></FieldDiff>}
        <p className="text-xs text-muted">The first saved version.</p>
      </div>
    );
  }
  if (!keys.length) return <p className="text-sm text-muted">No differences between {fromLabel.toLowerCase()} and {toLabel.toLowerCase()}.</p>;
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        <del className="rounded-sm bg-rose-100 px-1 text-rose-900">Removed</del> and <ins className="rounded-sm bg-emerald-100 px-1 text-emerald-900 no-underline">added</ins> going from {fromLabel.toLowerCase()} to {toLabel.toLowerCase()}.
      </p>
      {keys.map((k) => k === "media_ids"
        ? <FieldDiff key={k} label="Media"><p className="text-sm">Changed from {asText(from, k)} to {asText(to, k)}.</p></FieldDiff>
        : <FieldDiff key={k} label={FIELD_NAMES[k]} before={asText(from, k)} after={asText(to, k)} />)}
    </div>
  );
}

// ====================================================================== versions
export function VersionHistory({ postId, onRestored }: { postId: string; onRestored: (msg: string) => void }) {
  const tz = useTz();
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["post-versions", postId], queryFn: () => api<Versions>(`/content/posts/${postId}/versions`) });
  const [open, setOpen] = useState<number | null>(null);
  const [against, setAgainst] = useState<"previous" | "current">("previous");
  const restore = useMutation({
    mutationFn: (n: number) => api<{ message: string; restored: boolean }>(`/content/posts/${postId}/versions/${n}/restore`, { method: "POST" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["post-versions", postId] });
      qc.invalidateQueries({ queryKey: ["post", postId] });
      qc.invalidateQueries({ queryKey: ["posts"] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
      setOpen(null);
      onRestored(r.message);
    },
  });
  if (q.isLoading) return <Spinner label="Loading versions…" />;
  if (q.error || !q.data) return <Alert>{(q.error as Error)?.message ?? "Couldn't load the versions."}</Alert>;
  const { items, can_restore, current_fields } = q.data;
  if (!items.length) return <p className="text-sm text-muted">No saved versions yet. A version is saved every time the text, hashtags, title, link or media change.</p>;
  return (
    <div>
      {restore.error && <div className="mb-2"><Alert>{(restore.error as Error).message}</Alert></div>}
      <ol className="space-y-2">
        {items.map((v, idx) => {
          const prev = items[idx + 1];
          const isOpen = open === v.version;
          return (
            <li key={v.version} className={`min-w-0 rounded-lg border ${isOpen ? "border-primary-300" : "border-line"}`}>
              <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : v.version)}
                className="flex w-full min-w-0 items-start gap-2 px-3 py-2 text-left hover:bg-canvas/60">
                <span className="mt-0.5 shrink-0 rounded bg-ink/5 px-1.5 text-xs font-semibold tabular-nums text-muted">v{v.version}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium [overflow-wrap:anywhere]">
                    {v.reason ?? "Changed"}{v.current && <Badge tone="brand">Current</Badge>}
                  </span>
                  <span className="block text-xs text-muted">{v.changed_by_name} · {fmt(v.created_at, tz)}{v.changed.length > 0 && prev ? ` · ${v.changed.map((c) => FIELD_NAMES[c].toLowerCase()).join(", ")}` : ""}</span>
                </span>
              </button>
              {isOpen && (
                <div className="space-y-3 border-t border-line px-3 py-3">
                  {!v.current && (
                    <div className="flex flex-wrap gap-1 text-xs" role="group" aria-label="Compare with">
                      <span className="mr-1 self-center text-muted">Compare with:</span>
                      {(["previous", "current"] as const).map((k) => (
                        <button key={k} type="button" aria-pressed={against === k} onClick={() => setAgainst(k)}
                          className={`rounded-md border px-2 py-0.5 ${against === k ? "border-primary-500 bg-brand-tint font-medium" : "border-line hover:bg-canvas"}`}>
                          {k === "previous" ? "Version before it" : "The post now"}
                        </button>
                      ))}
                    </div>
                  )}
                  {against === "current" && !v.current
                    ? <VersionCompare from={v.fields} to={current_fields} fromLabel={`Version ${v.version}`} toLabel="The post now" />
                    : <VersionCompare from={prev?.fields} to={v.fields} fromLabel={prev ? `Version ${prev.version}` : ""} toLabel={`Version ${v.version}`} />}
                  {can_restore && can("content.create") && !v.current && (
                    <div className="flex justify-end">
                      <Button variant="secondary" disabled={restore.isPending}
                        onClick={() => { if (confirm(`Put version ${v.version} back? The current text is kept as a version, so you can undo this.`)) restore.mutate(v.version); }}>
                        <RotateCcw size={15} /> {restore.isPending ? "Restoring…" : `Restore version ${v.version}`}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {!can_restore && <p className="mt-2 inline-flex items-center gap-1 text-xs text-muted"><History size={12} /> Versions can be restored until the post is published.</p>}
    </div>
  );
}
