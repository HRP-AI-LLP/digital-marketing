import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, Download, ExternalLink, RefreshCw, Send, Trash2, X } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import { RejectDialog } from "../Approvals";
import {
  ACTION_LABELS, CharCounter, PostPreview, PostStatus, QaPanel, Section, Warnings, caption, fmt, fromLocalInput,
  parseTags, toLocalInput, tzLabel, until, useContentAccounts, useMeta, useTz, type PostDetail as Post,
} from "./shared";

type Draft = { text: string; title: string; tags: string; link_url: string; first_comment: string; when: string; media_ids: string[] };

const toDraft = (p: Post, tz: string): Draft => ({
  text: p.text ?? "", title: p.title ?? "", tags: p.hashtags.map((h) => `#${h}`).join(" "), link_url: p.link_url ?? "",
  first_comment: p.first_comment ?? "", when: toLocalInput(p.scheduled_at, tz), media_ids: p.media_ids,
});

function DuplicateDialog({ post, onClose }: { post: Post; onClose: () => void }) {
  const nav = useNavigate();
  const accounts = useContentAccounts();
  const [account, setAccount] = useState("");
  const dup = useMutation({
    mutationFn: () => api<Post>(`/content/posts/${post.id}/duplicate`, { method: "POST", json: { account_id: account } }),
    onSuccess: (p) => { onClose(); nav(`/posts/${p.id}`); },
  });
  return (
    <Modal title="Copy to another account" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); dup.mutate(); }}>
        {dup.error && <Alert>{(dup.error as Error).message}</Alert>}
        <p className="text-sm text-muted">Creates a draft with the same text and media. Check the length and hashtags for the new platform before scheduling it.</p>
        <Field label="Account">
          <Select required value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="" disabled>Choose an account</option>
            {(accounts.data ?? []).filter((a) => a.id !== post.social_account_id).map((a) => <option key={a.id} value={a.id}>{a.name} · {a.platform_label}</option>)}
          </Select>
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={!account || dup.isPending}>Create copy</Button>
        </div>
      </form>
    </Modal>
  );
}

function MarkPublishedDialog({ post, onClose }: { post: Post; onClose: () => void }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const mark = useMutation({
    mutationFn: () => api(`/content/posts/${post.id}/mark-published`, { method: "POST", json: { external_url: url || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["post", post.id] }); onClose(); },
  });
  return (
    <Modal title="Mark as published" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); mark.mutate(); }}>
        {mark.error && <Alert>{(mark.error as Error).message}</Alert>}
        <Field label="Link to the post (optional)" hint="Paste it so the team can find the post later.">
          <Input type="url" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={mark.isPending}>Mark as published</Button>
        </div>
      </form>
    </Modal>
  );
}

function RegenerateDialog({ onClose, onGo, busy }: { onClose: () => void; onGo: (note: string) => void; busy: boolean }) {
  const [note, setNote] = useState("");
  return (
    <Modal title="Write a new version" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onGo(note); }}>
        <Field label="What should change? (optional)" hint="For example: shorter, more practical, mention the free trial.">
          <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <p className="text-sm text-muted">The current text and media are replaced when the new version is ready.</p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={busy}><RefreshCw size={15} /> Write new version</Button>
        </div>
      </form>
    </Modal>
  );
}

export default function PostDetail() {
  const { id = "" } = useParams();
  const tz = useTz();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const meta = useMeta();
  const q = useQuery({
    queryKey: ["post", id],
    queryFn: () => api<Post>(`/content/posts/${id}`),
    refetchInterval: (query) => (["planned", "generating", "publishing"].includes(query.state.data?.status ?? "") ? 3000 : false),
  });
  const p = q.data;
  const [d, setD] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState<string>("");
  const [dialog, setDialog] = useState<"reject" | "dup" | "mark" | "regen" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    if (!p) return;
    const fresh = toDraft(p, tz);
    const key = JSON.stringify(fresh);
    // Only reset the editor when the server copy changed and there are no unsaved edits.
    setD((cur) => (!cur || JSON.stringify(cur) === baseline ? fresh : cur));
    setBaseline(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p?.updated_at, p?.status, tz]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["post", id] });
    qc.invalidateQueries({ queryKey: ["approvals"] });
    qc.invalidateQueries({ queryKey: ["posts"] });
    qc.invalidateQueries({ queryKey: ["calendar"] });
  };
  useJob(jobId, () => { setJobId(null); refresh(); });

  const dirty = !!d && JSON.stringify(d) !== baseline;
  const save = useMutation({
    mutationFn: () => {
      const base = p ? toDraft(p, tz) : null;
      const json: Record<string, unknown> = {};
      if (!d || !base) return Promise.resolve(p as Post);
      if (d.text !== base.text) json.text = d.text;
      if (d.title !== base.title) json.title = d.title;
      if (d.tags !== base.tags) json.hashtags = parseTags(d.tags);
      if (d.link_url !== base.link_url) json.link_url = d.link_url;
      if (d.first_comment !== base.first_comment) json.first_comment = d.first_comment;
      if (d.when !== base.when) json.scheduled_at = fromLocalInput(d.when);
      if (JSON.stringify(d.media_ids) !== JSON.stringify(base.media_ids)) json.media_ids = d.media_ids;
      return api<Post>(`/content/posts/${id}`, { method: "PATCH", json });
    },
    onSuccess: (np) => { qc.setQueryData(["post", id], np); const f = toDraft(np, tz); setD(f); setBaseline(JSON.stringify(f)); setNotice("Saved."); refresh(); },
  });
  const act = (path: string, json?: unknown) => api<{ message?: string; job_id?: string | null }>(`/content/posts/${id}/${path}`, { method: "POST", json: json ?? {} });
  const onAct = (r: { message?: string; job_id?: string | null }) => { setNotice(r.message ?? null); if (r.job_id) setJobId(r.job_id); refresh(); };
  const approve = useMutation({ mutationFn: () => act("approve"), onSuccess: onAct });
  const reject = useMutation({ mutationFn: (reason: string) => act("reject", { reason }), onSuccess: (r) => { setDialog(null); onAct(r); } });
  const regen = useMutation({ mutationFn: (note: string) => act("regenerate", { note }), onSuccess: (r) => { setDialog(null); onAct(r); } });
  const publishNow = useMutation({ mutationFn: () => act("publish-now"), onSuccess: onAct });
  const remove = useMutation({ mutationFn: () => api(`/content/posts/${id}`, { method: "DELETE" }), onSuccess: () => { refresh(); nav("/posts"); } });

  if (q.isLoading) return <Spinner label="Loading post…" />;
  if (q.error || !p || !d) return <Alert>{q.error ? (q.error as Error).message : "Post not found."}</Alert>;

  const writing = p.status === "planned" || p.status === "generating";
  const locked = writing || p.status === "published" || p.status === "publishing";
  const canEdit = can("content.create") && !locked;
  const tags = parseTags(d.tags);
  const count = caption(d.text, tags).length;
  const preview = { ...p, text: d.text, title: d.title || null, hashtags: tags, link_url: d.link_url || null, media: p.media.filter((m) => d.media_ids.includes(m.id)) };
  const approvable = ["in_review", "draft", "missed", "rejected"].includes(p.status);
  const err = save.error || approve.error || regen.error || publishNow.error || remove.error;
  const copyText = async () => {
    const full = [d.title, caption(d.text, tags), d.link_url && !d.text.includes(d.link_url) ? d.link_url : ""].filter(Boolean).join("\n\n");
    try { await navigator.clipboard.writeText(full); setNotice("Text copied."); } catch { setNotice("Couldn't copy. Select the text and copy it yourself."); }
  };

  return (
    <div>
      <Link to="/posts" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={15} /> All posts</Link>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <PostStatus status={p.status} />
            <Badge>{meta.data?.post_types[p.post_type] ?? p.post_type}</Badge>
            {p.pillar && <span className="inline-flex items-center gap-1 text-sm text-muted"><span className="h-2 w-2 rounded-full" style={{ background: p.pillar.color }} />{p.pillar.name}</span>}
          </div>
          <h1 className="mt-1 text-2xl font-bold">{p.account?.name ?? "Post"} <span className="font-normal text-muted">· {p.platform_label}</span></h1>
          <p className="text-sm text-muted">
            {p.published_at ? <>Published {fmt(p.published_at, tz)} {tzLabel(tz)}</> : p.scheduled_at ? <>Scheduled for {fmt(p.scheduled_at, tz)} {tzLabel(tz)} ({until(p.scheduled_at)})</> : "No time set"}
            {p.status === "in_review" && p.mode === "auto_with_window" && p.approval_deadline && <> · publishes automatically unless rejected by {fmt(p.approval_deadline, tz, { hour: "2-digit", minute: "2-digit", hour12: false })}</>}
          </p>
          {p.topic && <p className="mt-1 text-sm">Topic: {p.topic}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {p.external_url && <a href={p.external_url} target="_blank" rel="noreferrer"><Button variant="secondary"><ExternalLink size={15} /> View on {p.platform_label}</Button></a>}
          {approvable && can("content.approve") && <Button onClick={() => approve.mutate()} disabled={approve.isPending || dirty}><Check size={16} /> Approve</Button>}
          {["in_review", "draft", "scheduled", "approved", "missed"].includes(p.status) && can("content.approve") && (
            <Button variant="danger" onClick={() => setDialog("reject")}><X size={15} /> Reject</Button>
          )}
          {can("content.publish") && !locked && p.status !== "manual_pending" && (
            <Button variant="secondary" onClick={() => { if (confirm("Publish this post now?")) publishNow.mutate(); }} disabled={publishNow.isPending || dirty}><Send size={15} /> Publish now</Button>
          )}
        </div>
      </header>

      {writing && <div className="mb-4"><Alert tone="info"><Spinner label="The AI is writing this post. This page updates when it's ready." /></Alert></div>}
      {jobId && <div className="mb-4"><Alert tone="info"><Spinner label="Working on it…" /></Alert></div>}
      {p.error && <div className="mb-4"><Alert>{p.error}</Alert></div>}
      {p.rejection_reason && p.status === "rejected" && <div className="mb-4"><Alert tone="warn">Rejected: {p.rejection_reason}</Alert></div>}
      {err && <div className="mb-4"><Alert>{(err as Error).message}</Alert></div>}
      {notice && !err && <div className="mb-4"><Alert tone="info">{notice}</Alert></div>}
      {dirty && approvable && <div className="mb-4"><Alert tone="warn">Save your changes before approving or publishing.</Alert></div>}

      {p.status === "manual_pending" && (
        <div className="mb-5 rounded-xl border border-violet-200 bg-violet-50 p-4">
          <h2 className="font-semibold text-violet-900">Time to post this by hand</h2>
          <p className="mt-1 text-sm text-violet-900/80">Copy the text, download the media, post it on {p.platform_label}, then mark it as published.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={copyText}><Copy size={15} /> Copy text</Button>
            {p.media.filter((m) => m.url).map((m, i) => (
              <a key={m.id} href={m.url!} download><Button variant="secondary"><Download size={15} /> Download {m.kind} {p.media.length > 1 ? i + 1 : ""}</Button></a>
            ))}
            {can("content.publish") && <Button onClick={() => setDialog("mark")}><Check size={16} /> Mark as published</Button>}
          </div>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-5">
          <Section title="Post" action={canEdit && (
            <div className="flex items-center gap-2">
              {dirty && <Button variant="ghost" onClick={() => setD(toDraft(p, tz))}>Discard</Button>}
              <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</Button>
            </div>
          )}>
            <fieldset disabled={!canEdit} className="space-y-4">
              {p.title_chars > 0 && (
                <Field label="Title">
                  <Input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} />
                  <div className="mt-1 text-right"><CharCounter count={d.title.length} max={p.title_chars} /></div>
                </Field>
              )}
              <Field label="Text">
                <Textarea rows={12} value={d.text} onChange={(e) => setD({ ...d, text: e.target.value })} />
              </Field>
              <div className="-mt-2 flex justify-between text-xs text-muted">
                <span>Counted with hashtags, as it will be posted.</span>
                <CharCounter count={count} max={p.max_chars} />
              </div>
              <Field label={`Hashtags (up to ${p.max_hashtags})`} error={tags.length > p.max_hashtags ? `${p.platform_label} allows ${p.max_hashtags}. Remove ${tags.length - p.max_hashtags}.` : undefined}>
                <Input value={d.tags} onChange={(e) => setD({ ...d, tags: e.target.value })} placeholder="#HRTech #AI" />
              </Field>
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Link"><Input type="url" value={d.link_url} onChange={(e) => setD({ ...d, link_url: e.target.value })} placeholder="https://" /></Field>
                <Field label={`Scheduled time (${tzLabel(tz)})`}>
                  <Input type="datetime-local" value={d.when} onChange={(e) => setD({ ...d, when: e.target.value })} />
                </Field>
              </div>
              <Field label="First comment (optional)" hint="Posted as the first comment where the platform allows it.">
                <Textarea rows={2} value={d.first_comment} onChange={(e) => setD({ ...d, first_comment: e.target.value })} />
              </Field>
            </fieldset>
            {p.media.length > 0 && (
              <div className="mt-4">
                <div className="mb-2 text-sm font-medium">Media</div>
                <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {p.media.map((m) => {
                    const on = d.media_ids.includes(m.id);
                    return (
                      <li key={m.id} className={`relative overflow-hidden rounded-lg border border-line ${on ? "" : "opacity-40"}`}>
                        {m.url && (m.kind === "video" ? <video src={m.url} className="aspect-square w-full bg-black object-cover" muted /> : m.kind === "document" ? <div className="grid aspect-square place-items-center text-xs">PDF</div> : <img src={m.url} alt={m.alt_text ?? ""} className="aspect-square w-full object-cover" />)}
                        <div className="flex items-center justify-between px-2 py-1 text-xs">
                          <span>{m.kind}{m.width ? ` · ${m.width}×${m.height}` : ""}</span>
                          {canEdit && (
                            <button className="text-brand hover:underline" onClick={() => setD({ ...d, media_ids: on ? d.media_ids.filter((x) => x !== m.id) : [...d.media_ids, m.id] })}>
                              {on ? "Remove" : "Add back"}
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </Section>

          <Section title="Preview">
            <div className="max-w-xl"><PostPreview post={preview} /></div>
            {p.first_comment && <p className="mt-2 max-w-xl text-sm text-muted">First comment: {p.first_comment}</p>}
          </Section>

          {(p.brief.image_brief || p.brief.slides?.length || p.brief.video_script?.length || p.brief.brief) && (
            <Section title="Brief for the visuals">
              {p.brief.brief && <p className="mb-2 text-sm"><span className="font-medium">Your brief:</span> {p.brief.brief}</p>}
              {p.brief.image_brief && <p className="text-sm"><span className="font-medium">Image:</span> {p.brief.image_brief}</p>}
              {p.brief.alt_text && <p className="mt-1 text-sm text-muted">Alt text: {p.brief.alt_text}</p>}
              {!!p.brief.slides?.length && (
                <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">{p.brief.slides.map((s, i) => <li key={i}><span className="font-medium">{s.heading}</span> {s.body}</li>)}</ol>
              )}
              {!!p.brief.video_script?.length && (
                <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">{p.brief.video_script.map((s, i) => <li key={i}><span className="font-medium">{s.caption}</span> <span className="text-muted">— {s.voiceover}</span></li>)}</ol>
              )}
            </Section>
          )}
        </div>

        <div className="space-y-5">
          <Section title="Quality checks"><QaPanel post={p} /></Section>
          {p.metrics.length > 0 && (
            <Section title="Performance">
              <dl className="grid grid-cols-3 gap-3 text-sm">
                {(["impressions", "reach", "likes", "comments", "shares", "clicks", "saves", "video_views"] as const).map((k) => (
                  <div key={k}><dt className="text-xs text-muted">{k.replace("_", " ")}</dt><dd className="text-lg font-semibold tabular-nums">{p.metrics[0][k].toLocaleString()}</dd></div>
                ))}
              </dl>
              <p className="mt-2 text-xs text-muted">Updated {fmt(p.metrics[0].fetched_at, tz)}</p>
            </Section>
          )}
          <Section title="More actions">
            <div className="flex flex-wrap gap-2">
              {can("content.create") && !locked && <Button variant="secondary" onClick={() => setDialog("regen")}><RefreshCw size={15} /> Write a new version</Button>}
              {can("content.create") && !writing && <Button variant="secondary" onClick={() => setDialog("dup")}><Copy size={15} /> Copy to another account</Button>}
              {p.status !== "manual_pending" && <Button variant="ghost" onClick={copyText}><Copy size={15} /> Copy text</Button>}
              {can("content.create") && !["published", "publishing"].includes(p.status) && (
                <Button variant="ghost" onClick={() => { if (confirm("Delete this post? This can't be undone.")) remove.mutate(); }}><Trash2 size={15} /> Delete</Button>
              )}
            </div>
            {p.warnings.length > 0 && <div className="mt-3"><Warnings items={p.warnings} /></div>}
          </Section>
          <Section title="History">
            {p.activities.length === 0 ? <p className="text-sm text-muted">Nothing yet.</p> : (
              <ol className="space-y-3 border-l border-line pl-4">
                {p.activities.map((a) => (
                  <li key={a.id} className="relative text-sm">
                    <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-primary-500" />
                    <div className="font-medium">{ACTION_LABELS[a.action] ?? a.action.replace(/_/g, " ")}</div>
                    {a.note && <div className="text-muted">{a.note}</div>}
                    <div className="text-xs text-muted">{a.actor ?? "system"} · {fmt(a.created_at, tz)}</div>
                  </li>
                ))}
              </ol>
            )}
          </Section>
        </div>
      </div>

      {dialog === "reject" && <RejectDialog count={1} onClose={() => setDialog(null)} onReject={(r) => reject.mutate(r)} busy={reject.isPending} error={reject.error as Error | null} />}
      {dialog === "dup" && <DuplicateDialog post={p} onClose={() => setDialog(null)} />}
      {dialog === "mark" && <MarkPublishedDialog post={p} onClose={() => setDialog(null)} />}
      {dialog === "regen" && <RegenerateDialog onClose={() => setDialog(null)} onGo={(n) => regen.mutate(n)} busy={regen.isPending} />}
    </div>
  );
}
