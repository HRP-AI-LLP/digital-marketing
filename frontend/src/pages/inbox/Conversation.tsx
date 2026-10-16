import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive, ArchiveRestore, ArrowLeft, ExternalLink, Flag, Heart, Mail, Send, Sparkles,
} from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Select, Spinner, Textarea } from "../../components/ui";
import {
  Avatar, KindChip, PlatformMark, SentimentChip, UrgentChip, ago, openLabel, type Detail, type InboxItem, type Person,
  type Sentiment,
} from "./shared";

function Message({ m, highlight }: { m: InboxItem; highlight: boolean }) {
  const own = m.is_own;
  return (
    <li className={`flex gap-2.5 ${own ? "flex-row-reverse" : ""} ${m.parent_id && !own ? "pl-6" : ""}`}>
      <Avatar author={m.author} size={30} own={own} />
      <div className={`min-w-0 max-w-[85%] ${own ? "items-end text-right" : ""} flex flex-col`}>
        <div className={`flex flex-wrap items-baseline gap-x-2 text-xs text-muted ${own ? "justify-end" : ""}`}>
          <span className="font-semibold text-ink">{own ? "You" : m.author.name || m.author.handle}</span>
          {!own && m.author.handle && m.author.handle !== m.author.name && <span>{m.author.handle.startsWith("@") ? m.author.handle : `@${m.author.handle}`}</span>}
          <span>{ago(m.published_at || m.created_at)}</span>
        </div>
        <div className={`mt-1 whitespace-pre-line break-words rounded-2xl px-3.5 py-2 text-left text-[14px] leading-relaxed ${
          own ? "rounded-tr-md bg-gradient-to-br from-blue-700 to-cyan-600 text-white"
            : highlight ? "rounded-tl-md bg-primary-50 ring-2 ring-primary-500/30" : "rounded-tl-md bg-slate-100"
        }`}>
          {m.title && <div className="mb-1 font-semibold">{m.title}</div>}
          {m.text || <span className="italic text-muted">No text</span>}
        </div>
      </div>
    </li>
  );
}

function IconBtn({ label, onClick, disabled, pressed, children }: { label: string; onClick: () => void; disabled?: boolean; pressed?: boolean; children: ReactNode }) {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={pressed} disabled={disabled} onClick={onClick}
      className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-ink/5 hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent">
      {children}
    </button>
  );
}

export default function Conversation({ id, onBack, people }: { id: string; onBack: () => void; people: Person[] }) {
  const qc = useQueryClient();
  const { can, session } = useAuth();
  const canAct = can("inbox.reply");
  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["inbox-item", id], queryFn: () => api<Detail>(`/inbox/items/${id}`) });
  useEffect(() => { setText(""); setNotice(null); }, [id]);

  const refresh = (d?: Detail) => {
    if (d) qc.setQueryData(["inbox-item", id], d);
    qc.invalidateQueries({ queryKey: ["inbox-items"] });
    qc.invalidateQueries({ queryKey: ["inbox-summary"] });
  };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Detail>(`/inbox/items/${id}`, { method: "PATCH", json: body }),
    onSuccess: (d) => refresh(d),
  });
  const reply = useMutation({
    mutationFn: () => api<Detail>(`/inbox/items/${id}/reply`, { method: "POST", json: { text } }),
    onSuccess: (d) => { setText(""); setNotice("Reply sent."); refresh(d); },
    onError: () => qc.invalidateQueries({ queryKey: ["inbox-item", id] }),
  });
  const suggest = useMutation({
    mutationFn: () => api<{ suggestions: string[] }>(`/inbox/items/${id}/suggest`, { method: "POST" }),
    onSuccess: (r) => qc.setQueryData<Detail>(["inbox-item", id], (d) => d && { ...d, item: { ...d.item, ai_suggestions: r.suggestions } }),
  });
  const like = useMutation({
    mutationFn: () => api<{ liked_at: string }>(`/inbox/items/${id}/like`, { method: "POST" }),
    onSuccess: (r) => qc.setQueryData<Detail>(["inbox-item", id], (d) => d && { ...d, item: { ...d.item, liked_at: r.liked_at } }),
  });

  if (q.isLoading) return <div className="grid h-64 place-items-center"><Spinner label="Loading conversation…" /></div>;
  if (q.error || !q.data) return <div className="p-5"><Alert>{(q.error as Error)?.message ?? "Couldn't load this item."}</Alert></div>;
  const d = q.data;
  const it = d.item;
  const limit = d.reply_limit ?? 0;
  const over = limit > 0 && text.length > limit;
  const postText = d.post?.text || it.post_snippet;
  const postUrl = d.post?.external_url || it.external_post_url;
  const mutErr = (patch.error || like.error) as Error | null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* header */}
      <div className="flex items-start gap-3 border-b border-line px-4 py-3.5 sm:px-5">
        <button onClick={onBack} aria-label="Back to the list" className="-ml-1 mt-1 rounded-md p-1 text-muted hover:bg-ink/5 lg:hidden"><ArrowLeft size={18} /></button>
        <Avatar author={it.author} size={40} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate font-semibold">{it.author.name || it.author.handle || "Someone"}</span>
            {it.priority && <UrgentChip />}
            <SentimentChip s={it.sentiment} />
            <KindChip item={it} />
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <PlatformMark platform={it.platform} label={it.platform_label} size="xs" />
            <span>{it.platform_label}</span>
            {it.account && <><span aria-hidden>·</span><span className="truncate">{it.account.name}</span></>}
            <span aria-hidden>·</span><span>{ago(it.published_at || it.created_at)}</span>
            {it.engagement ? <><span aria-hidden>·</span><span>{it.engagement} engagements</span></> : null}
          </div>
        </div>
        {it.url && (
          <a href={it.url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-ink hover:border-brand/50">
            <ExternalLink size={13} /> <span className="hidden sm:inline">{openLabel(it.platform, it.platform_label)}</span>
          </a>
        )}
      </div>

      {/* toolbar */}
      {canAct && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-canvas/60 px-4 py-2">
          <Select aria-label="Assign to" title="Assign to" className="h-8 w-36 text-sm" value={it.assigned_to?.id ?? ""}
            onChange={(e) => patch.mutate({ assigned_to: e.target.value || null })}>
            <option value="">Unassigned</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.id === session?.user.id ? `${p.name} (you)` : p.name}</option>)}
          </Select>
          <Select aria-label="Sentiment" title="Sentiment" className="h-8 w-28 text-sm" value={it.sentiment ?? ""}
            onChange={(e) => e.target.value && patch.mutate({ sentiment: e.target.value as Sentiment })}>
            <option value="" disabled>Sentiment</option>
            <option value="positive">Positive</option><option value="neutral">Neutral</option>
            <option value="question">Question</option><option value="negative">Negative</option>
          </Select>
          <div className="ml-auto flex items-center">
            <IconBtn label={it.priority ? "Remove the urgent flag" : "Flag as urgent"} pressed={it.priority} onClick={() => patch.mutate({ priority: !it.priority })}>
              <Flag size={16} className={it.priority ? "fill-danger text-danger" : ""} />
            </IconBtn>
            {d.can_like && (
              <IconBtn label={it.liked_at ? "Liked" : "Like"} disabled={!!it.liked_at || like.isPending} onClick={() => like.mutate()}>
                <Heart size={16} className={it.liked_at ? "fill-danger text-danger" : ""} />
              </IconBtn>
            )}
            <IconBtn label="Mark unread" disabled={it.status === "new"} onClick={() => patch.mutate({ status: "new" })}><Mail size={16} /></IconBtn>
            {it.status === "archived"
              ? <IconBtn label="Restore from archive" onClick={() => patch.mutate({ status: "read" })}><ArchiveRestore size={16} /></IconBtn>
              : <IconBtn label="Archive" onClick={() => patch.mutate({ status: "archived" })}><Archive size={16} /></IconBtn>}
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {mutErr && <div className="mb-3"><Alert>{mutErr.message}</Alert></div>}
        {d.permission_issue && (
          <details className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <summary className="cursor-pointer">
              <strong className="font-semibold">Replies need the {d.permission_issue.permission} permission.</strong> <span className="underline">How to fix</span>
            </summary>
            <p className="mt-1.5">
              {d.permission_issue.message?.replace(/^.*?needs the \S+ permission\.\s*/i, "")}{" "}
              {can("integration.view") && <Link to="/settings/accounts" className="font-medium underline">Go to Social accounts</Link>}
            </p>
          </details>
        )}
        {/* original post */}
        {(postText || postUrl) && (
          <figure className="mb-5 rounded-xl border border-line bg-surface p-3.5">
            <figcaption className="mb-1.5 flex items-center justify-between gap-2 text-xs text-muted">
              <span>{it.source === "listening" ? "Found on" : it.kind === "mention" ? "Their post" : "Your post"}</span>
              {postUrl && <a href={postUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary-700 hover:underline">View post <ExternalLink size={11} /></a>}
            </figcaption>
            {postText && <p className="line-clamp-4 whitespace-pre-line border-l-2 border-secondary-400 pl-3 text-sm text-slate-700">{postText}</p>}
          </figure>
        )}
        <ol className="space-y-4" aria-label="Conversation">
          {d.thread.map((m) => <Message key={m.id} m={m} highlight={m.id === it.id && d.thread.length > 1} />)}
        </ol>
        {it.reply_error && (
          <div className="mt-4"><Alert>Last reply failed: {it.reply_error}</Alert></div>
        )}
        {it.status === "replied" && it.replied_at && !d.thread.some((m) => m.is_own) && (
          <p className="mt-4 text-xs text-muted">Answered {ago(it.replied_at)}.</p>
        )}
      </div>

      {/* composer */}
      <div className="border-t border-line bg-surface px-5 py-4">
        {!d.can_reply ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-canvas px-3.5 py-3 text-sm text-muted">
            <span>{d.reply_blocked_reason}</span>
            {it.url && <a href={it.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-medium text-primary-700 hover:underline">{openLabel(it.platform, it.platform_label)} <ExternalLink size={13} /></a>}
          </div>
        ) : !canAct ? (
          <p className="text-sm text-muted">You can read the inbox. Ask an administrator for the inbox reply permission to answer.</p>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim() && !over) reply.mutate(); }}>
            {it.ai_suggestions.length > 0 && (
              <div className="mb-3">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted"><Sparkles size={13} className="text-secondary-600" /> Suggested replies — pick one to edit</div>
                <div className="flex flex-col gap-1.5">
                  {it.ai_suggestions.map((s, i) => (
                    <button key={i} type="button" onClick={() => { setText(s); document.getElementById("inbox-reply")?.focus(); }}
                      className="rounded-lg border border-secondary-200 bg-accent-tint/60 px-3 py-2 text-left text-sm text-slate-700 hover:border-secondary-400 hover:bg-accent-tint">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {reply.error && <div className="mb-2"><Alert>{(reply.error as Error).message}</Alert></div>}
            {suggest.error && <div className="mb-2"><Alert tone="warn">{(suggest.error as Error).message}</Alert></div>}
            {notice && !reply.error && <div className="mb-2"><Alert tone="info">{notice}</Alert></div>}
            <label htmlFor="inbox-reply" className="sr-only">Your reply</label>
            <Textarea id="inbox-reply" rows={3} value={text} placeholder={`Reply to ${it.author.name || "them"} on ${it.platform_label}…`}
              onChange={(e) => { setText(e.target.value); setNotice(null); }}
              onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && text.trim() && !over) reply.mutate(); }} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button type="button" variant="secondary" disabled={suggest.isPending} onClick={() => suggest.mutate()}>
                <Sparkles size={15} className="text-secondary-600" />
                {suggest.isPending ? "Writing suggestions…" : it.ai_suggestions.length ? "New suggestions" : "Suggest replies"}
              </Button>
              <span className={`ml-auto text-xs tabular-nums ${over ? "font-semibold text-danger" : "text-muted"}`}>{text.length} / {limit}</span>
              <Button type="submit" disabled={!text.trim() || over || reply.isPending}>
                <Send size={15} /> {reply.isPending ? "Sending…" : "Send reply"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
