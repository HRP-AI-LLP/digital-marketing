import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, Copy, Download, ExternalLink, Globe, RefreshCw, Search, Send, TriangleAlert, Undo2, X } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, Field, Input, Modal, Select, Spinner, formatDate } from "../../components/ui";
import type { Article } from "./types";

type Connection = {
  id: string; name: string; provider: string; target: "wordpress" | "webhook"; site: string; enabled: boolean;
  last_test_ok: boolean | null; last_test_message: string | null; indexnow_key: string | null;
  plugins: { yoast?: boolean; rankmath?: boolean; redirection?: boolean };
};
type Warning = { code: string; message: string; help?: string; snippet?: string };
type PageCheck = {
  ok: boolean; status_code: number | null; title: string | null; canonical: string | null; title_ok: boolean | null;
  canonical_ok: boolean | null; noindex: boolean; message: string; checked_at: string; final_url: string;
};
type Publication = {
  id: string; provider_id: string; target: string; status: string; update_available: boolean; remote_id: string | null;
  remote_url: string | null; published_slug: string | null; scheduled_at: string | null; published_at: string | null;
  updated_at: string; error: string | null; warnings: Warning[]; seo_plugin: string | null;
  options: { status?: string; categories?: number[]; tags?: string[]; author?: number | null; indexnow?: boolean };
  check: PageCheck | null; indexnow: { ok: boolean; message: string; at: string } | null;
  search_console_url: string | null; can_unpublish: boolean;
};
type Item = { connection: Connection; publication: Publication | null; job: { id: string; status: string; progress: string | null } | null };
type Redirect = { id: string; from_path: string; to_path: string; status: string; error: string | null; provider_id: string | null };
type WebsiteState = { items: Item[]; redirects: Redirect[]; can_publish: boolean };
type Options = { categories: { id: number; name: string }[]; tags: { id: number; name: string }[]; authors: { id: number; name: string }[]; errors: string[] };
type IndexNowSetup = { key: string; file_name: string; file_url: string; file_content: string; steps: string[] };

const STATUS: Record<string, { label: string; tone: "neutral" | "brand" | "accent" | "danger" }> = {
  none: { label: "Not published", tone: "neutral" }, draft: { label: "Draft on website", tone: "neutral" },
  published: { label: "Published", tone: "brand" }, scheduled: { label: "Scheduled", tone: "accent" },
  unpublished: { label: "Removed from website", tone: "neutral" }, error: { label: "Not published", tone: "danger" },
};
const REDIRECT_STATUS: Record<string, string> = {
  pending: "Waiting for next update", pushed: "Added in WordPress", sent: "Sent to your site", manual: "Add it yourself",
  done: "Added", failed: "Couldn't add",
};
const FORMATS = [
  { id: "htaccess", label: ".htaccess (Apache)" }, { id: "nginx", label: "nginx" }, { id: "netlify", label: "_redirects (Netlify)" },
  { id: "vercel", label: "vercel.json" }, { id: "csv", label: "CSV" },
] as const;

function host(url: string) {
  try { return new URL(url).host; } catch { return url; }
}

function saveBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function downloadBundle(id: string) {
  const r = await api<{ filename: string; content_b64: string }>(`/seo/articles/${id}/bundle?as_json=true`);
  const bytes = Uint8Array.from(atob(r.content_b64), (c) => c.charCodeAt(0));
  saveBlob(r.filename, new Blob([bytes], { type: "application/zip" }));
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button type="button" variant="ghost" className="h-7 px-2"
      onClick={() => navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}>
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? "Copied" : label}
    </Button>
  );
}

function WarningItem({ w }: { w: Warning }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-sm text-amber-900">
      <div className="flex gap-2"><TriangleAlert size={15} className="mt-0.5 shrink-0" /><span className="[overflow-wrap:anywhere]">{w.message}</span></div>
      {(w.help || w.snippet) && (
        <button type="button" className="mt-1 text-xs font-medium underline" onClick={() => setOpen(!open)}>{open ? "Hide how to fix" : "How to fix"}</button>
      )}
      {open && (
        <div className="mt-2 space-y-2">
          {w.help && <p className="text-xs [overflow-wrap:anywhere]">{w.help}</p>}
          {w.snippet && (
            <div>
              <div className="flex justify-end"><CopyButton text={w.snippet} label="Copy code" /></div>
              <pre className="max-h-48 overflow-auto rounded-md bg-slate-900 p-2 text-[11px] leading-4 text-slate-100">{w.snippet}</pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function CheckResult({ c }: { c: PageCheck }) {
  const row = (ok: boolean | null, text: string) => ok === null ? null : (
    <li className="flex gap-1.5">{ok ? <Check size={14} className="mt-0.5 shrink-0 text-emerald-600" aria-label="OK" /> : <X size={14} className="mt-0.5 shrink-0 text-danger" aria-label="Problem" />}<span className="[overflow-wrap:anywhere]">{text}</span></li>
  );
  return (
    <div className="rounded-lg border border-line p-2.5 text-sm">
      <ul className="space-y-1">
        {row(c.status_code === 200, c.status_code === 200 ? "Page opens (200)" : c.status_code ? `Page answered ${c.status_code}` : "Page didn't open")}
        {row(c.title_ok, c.title_ok ? "Title is right" : `Title: ${c.title ?? "missing"}`)}
        {row(c.canonical_ok, c.canonical_ok ? "Canonical link is right" : `Canonical: ${c.canonical ?? "missing"}`)}
        {c.noindex && row(false, "Page is set to noindex")}
      </ul>
      {!c.ok && <p className="mt-1.5 text-xs text-muted [overflow-wrap:anywhere]">{c.message}</p>}
      <p className="mt-1 text-xs text-muted">Checked {formatDate(c.checked_at)}</p>
    </div>
  );
}

function toLocalInput(iso?: string | null) {
  const d = iso ? new Date(iso) : new Date(Date.now() + 24 * 3600 * 1000);
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function PublishModal({ article, item, onClose, onStarted }: { article: Article; item: Item; onClose: () => void; onStarted: (jobId: string) => void }) {
  const c = item.connection;
  const prev = item.publication?.options ?? {};
  const wp = c.target === "wordpress";
  const live = ["published", "scheduled"].includes(item.publication?.status ?? "");
  const [when, setWhen] = useState<"publish" | "future" | "draft">((prev.status as "publish" | "future" | "draft") ?? "publish");
  const [at, setAt] = useState(toLocalInput(item.publication?.scheduled_at));
  const [cats, setCats] = useState<number[]>(prev.categories ?? []);
  const [tags, setTags] = useState((prev.tags ?? article.secondary_keywords.slice(0, 3)).join(", "));
  const [author, setAuthor] = useState(prev.author ? String(prev.author) : "");
  const [indexnow, setIndexnow] = useState(prev.indexnow ?? !!c.indexnow_key);
  const opts = useQuery({ queryKey: ["wp-options", c.id], queryFn: () => api<Options>(`/seo/website/connections/${c.id}/options`), enabled: wp, retry: false });
  const go = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/seo/articles/${article.id}/website/publish`, {
      method: "POST",
      json: {
        provider_id: c.id, status: when, publish_at: when === "future" ? new Date(at).toISOString() : null,
        categories: cats, tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        author: author ? Number(author) : null, indexnow: indexnow && !!c.indexnow_key,
      },
    }),
    onSuccess: (r) => { onStarted(r.job_id); onClose(); },
  });
  return (
    <Modal title={item.publication?.remote_id ? `Update on ${c.name}` : `Publish to ${c.name}`} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); go.mutate(); }}>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">When</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {([["publish", "Publish now"], ["future", "Schedule"], ["draft", live ? "Move to draft" : "Save as draft"]] as const).map(([v, label]) => (
              <label key={v} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${when === v ? "border-primary-500 bg-primary-50" : "border-line"}`}>
                <input type="radio" name="when" className="accent-primary-600" checked={when === v} onChange={() => setWhen(v)} /> {label}
              </label>
            ))}
          </div>
        </fieldset>
        {when === "future" && (
          <Field label="Go live at" hint="Your local time.">
            <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} required />
          </Field>
        )}
        {wp && (
          <>
            {opts.isLoading && <Spinner label="Loading categories and authors from WordPress" />}
            {opts.error && <Alert tone="warn">{(opts.error as Error).message}</Alert>}
            {opts.data?.errors.map((e) => <Alert key={e} tone="warn">{e}</Alert>)}
            {opts.data && opts.data.categories.length > 0 && (
              <fieldset>
                <legend className="mb-1.5 text-sm font-medium">Categories</legend>
                <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                  {opts.data.categories.map((cat) => (
                    <label key={cat.id} className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm ${cats.includes(cat.id) ? "border-primary-500 bg-primary-50" : "border-line"}`}>
                      <input type="checkbox" className="accent-primary-600" checked={cats.includes(cat.id)}
                        onChange={(e) => setCats(e.target.checked ? [...cats, cat.id] : cats.filter((x) => x !== cat.id))} />
                      {cat.name}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Tags" hint="Separate with commas. New tags are created.">
                <Input value={tags} onChange={(e) => setTags(e.target.value)} list={`wp-tags-${c.id}`} />
              </Field>
              <datalist id={`wp-tags-${c.id}`}>{opts.data?.tags.map((t) => <option key={t.id} value={t.name} />)}</datalist>
              {opts.data && opts.data.authors.length > 0 && (
                <Field label="Author">
                  <Select value={author} onChange={(e) => setAuthor(e.target.value)}>
                    <option value="">The connected user</option>
                    {opts.data.authors.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </Select>
                </Field>
              )}
            </div>
          </>
        )}
        {!wp && (
          <Field label="Tags (optional)" hint="Sent with the article for your site to use.">
            <Input value={tags} onChange={(e) => setTags(e.target.value)} />
          </Field>
        )}
        {c.indexnow_key && when === "publish" && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-primary-600" checked={indexnow} onChange={(e) => setIndexnow(e.target.checked)} />
            Tell Bing and other search engines right away (IndexNow)
          </label>
        )}
        <p className="text-xs text-muted">
          Sends the title, text, cover image, meta title and description, URL slug{wp ? ", excerpt and focus keyphrase" : ", schema and social tags"}.
          {item.publication?.remote_id ? " The existing post is updated; no second copy is made." : ""}
        </p>
        {go.error && <Alert>{(go.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={go.isPending}><Send size={15} /> {when === "draft" ? "Send as draft" : when === "future" ? "Schedule" : item.publication?.remote_id ? "Update on website" : "Publish"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function IndexNowModal({ connection, onClose, onDone }: { connection: Connection; onClose: () => void; onDone: () => void }) {
  const setup = useQuery({
    queryKey: ["indexnow", connection.id],
    queryFn: () => api<IndexNowSetup>(`/seo/website/connections/${connection.id}/indexnow-key`, { method: "POST" }),
  });
  return (
    <Modal title="Set up IndexNow" onClose={() => { onDone(); onClose(); }}>
      {setup.isLoading && <Spinner label="Creating your key" />}
      {setup.error && <Alert>{(setup.error as Error).message}</Alert>}
      {setup.data && (
        <div className="space-y-3 text-sm">
          <p className="text-muted">IndexNow tells Bing, Yandex and other search engines about new pages straight away. It needs a small key file on your website once.</p>
          <ol className="list-decimal space-y-1.5 pl-5">{setup.data.steps.map((s) => <li key={s} className="[overflow-wrap:anywhere]">{s}</li>)}</ol>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2.5">
            <code className="min-w-0 flex-1 text-xs [overflow-wrap:anywhere]">{setup.data.file_name}</code>
            <CopyButton text={setup.data.file_content} label="Copy key" />
            <Button type="button" variant="ghost" className="h-7 px-2" onClick={() => saveBlob(setup.data!.file_name, new Blob([setup.data!.file_content], { type: "text/plain" }))}>
              <Download size={14} /> Download file
            </Button>
          </div>
          <p className="text-xs text-muted">Check it opens: <a className="text-primary-700 underline [overflow-wrap:anywhere]" href={setup.data.file_url} target="_blank" rel="noreferrer">{setup.data.file_url}</a>. Google doesn't use IndexNow; use “Request indexing” for Google.</p>
          <div className="flex justify-end"><Button onClick={() => { onDone(); onClose(); }}>Done</Button></div>
        </div>
      )}
    </Modal>
  );
}

function Redirects({ article, redirects, items, canPublish, refetch }: { article: Article; redirects: Redirect[]; items: Item[]; canPublish: boolean; refetch: () => void }) {
  const [fmt, setFmt] = useState<string>("htaccess");
  const [text, setText] = useState<string | null>(null);
  const exportQ = useMutation({
    mutationFn: (f: string) => api<{ filename: string; media_type: string; content: string }>(`/seo/redirects?format=${f}&article_id=${article.id}`),
    onSuccess: (r) => setText(r.content),
  });
  const mark = useMutation({ mutationFn: (id: string) => api(`/seo/redirects/${id}`, { method: "PATCH", json: { status: "done" } }), onSuccess: refetch });
  const push = useMutation({ mutationFn: (pid: string) => api(`/seo/redirects/push`, { method: "POST", json: { provider_id: pid } }), onSuccess: refetch });
  if (!redirects.length) return null;
  const wpWithPlugin = items.find((i) => i.connection.target === "wordpress" && i.connection.plugins.redirection);
  const needsWork = redirects.some((r) => ["manual", "failed"].includes(r.status));
  return (
    <div className="space-y-2 border-t border-line pt-3">
      <h3 className="text-sm font-semibold">Redirects</h3>
      <p className="text-xs text-muted">The address changed after publishing, so visitors and search engines are sent from the old address to the new one.</p>
      <ul className="space-y-1.5 text-sm">
        {redirects.map((r) => (
          <li key={r.id} className="rounded-lg border border-line p-2">
            <div className="text-xs [overflow-wrap:anywhere]"><code>{r.from_path}</code> → <code>{r.to_path}</code></div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge tone={r.status === "failed" ? "danger" : ["pushed", "done", "sent"].includes(r.status) ? "brand" : "neutral"}>{REDIRECT_STATUS[r.status] ?? r.status}</Badge>
              {canPublish && r.status === "manual" && <button type="button" className="text-xs text-primary-700 hover:underline" onClick={() => mark.mutate(r.id)}>Mark as added</button>}
            </div>
            {r.error && <p className="mt-1 text-xs text-danger [overflow-wrap:anywhere]">{r.error}</p>}
          </li>
        ))}
      </ul>
      {needsWork && !wpWithPlugin && items.some((i) => i.connection.target === "wordpress") && (
        <p className="text-xs text-muted">WordPress already forwards old post addresses by itself. To be safe, add these with your host or the free Redirection plugin (then press Update on website and they're added automatically).</p>
      )}
      {canPublish && needsWork && wpWithPlugin && (
        <Button variant="secondary" className="w-full" onClick={() => push.mutate(wpWithPlugin.connection.id)} disabled={push.isPending}>
          <RefreshCw size={14} className={push.isPending ? "animate-spin" : ""} /> Add them in WordPress (Redirection)
        </Button>
      )}
      {push.error && <Alert>{(push.error as Error).message}</Alert>}
      <div className="flex gap-2">
        <Select aria-label="Redirect format" value={fmt} onChange={(e) => { setFmt(e.target.value); setText(null); }} className="flex-1">
          {FORMATS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </Select>
        <Button variant="secondary" onClick={() => exportQ.mutate(fmt)} disabled={exportQ.isPending}>Show</Button>
      </div>
      {text !== null && (
        <div>
          <div className="flex justify-end gap-1">
            <CopyButton text={text} />
            <Button variant="ghost" className="h-7 px-2" onClick={() => saveBlob(exportQ.data!.filename, new Blob([text], { type: exportQ.data!.media_type }))}><Download size={14} /> Download</Button>
          </div>
          <pre className="max-h-40 overflow-auto rounded-md bg-slate-900 p-2 text-[11px] leading-4 text-slate-100">{text}</pre>
        </div>
      )}
    </div>
  );
}

function ConnectionBlock({ article, item, dirty, canPublish, refetch }: { article: Article; item: Item; dirty: boolean; canPublish: boolean; refetch: () => void }) {
  const c = item.connection;
  const p = item.publication;
  const [jobId, setJobId] = useState<string | null>(null);
  const [modal, setModal] = useState<"publish" | "indexnow" | null>(null);
  const activeJob = jobId ?? item.job?.id ?? null;
  const job = useJob(activeJob, () => { setJobId(null); refetch(); });
  const running = !!activeJob && job?.status !== "done" && job?.status !== "failed";
  const unpublish = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/seo/articles/${article.id}/website/unpublish`, { method: "POST", json: { provider_id: c.id } }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const update = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/seo/articles/${article.id}/website/publish`, {
      method: "POST", json: { provider_id: c.id, ...(p?.options ?? {}), status: p?.status === "draft" ? "draft" : p?.status === "scheduled" ? "future" : "publish", publish_at: p?.status === "scheduled" ? p.scheduled_at : null },
    }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const check = useMutation({ mutationFn: () => api<PageCheck>(`/seo/articles/${article.id}/website/check`, { method: "POST", json: { provider_id: c.id } }), onSuccess: refetch });
  const ping = useMutation({ mutationFn: () => api<{ ok: boolean; message: string }>(`/seo/articles/${article.id}/website/indexnow`, { method: "POST", json: { provider_id: c.id } }), onSuccess: refetch });

  const status = p?.status ?? "none";
  const st = STATUS[status] ?? { label: status, tone: "neutral" as const };
  const live = status === "published" || status === "scheduled";
  const onSite = live || status === "draft";
  const where = c.site ? host(c.site) : c.target === "wordpress" ? "WordPress" : "your website";
  const busy = running || update.isPending || unpublish.isPending;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium [overflow-wrap:anywhere]">{c.name}</div>
          <div className="text-xs text-muted [overflow-wrap:anywhere]">{c.target === "wordpress" ? "WordPress" : "Webhook"} · {where}{!c.enabled && " · turned off"}</div>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge tone={st.tone}>{st.label}{status === "scheduled" && p?.scheduled_at ? ` · ${formatDate(p.scheduled_at)}` : ""}</Badge>
          {p?.update_available && <Badge tone="accent">Update available</Badge>}
        </div>
      </div>
      {p?.remote_url && onSite && (
        <a href={p.remote_url} target="_blank" rel="noreferrer" className="flex items-start gap-1 text-sm text-primary-700 hover:underline">
          <ExternalLink size={13} className="mt-1 shrink-0" /><span className="[overflow-wrap:anywhere]">{p.remote_url}</span>
        </a>
      )}
      {p?.published_at && <p className="text-xs text-muted">First published {formatDate(p.published_at)} · last sent {formatDate(p.updated_at)}</p>}
      {running && <Spinner label={job?.progress || (job?.status === "queued" ? "Waiting for the worker…" : "Sending to the website…")} />}
      {!running && p?.error && <Alert>{p.error}</Alert>}
      {!running && p?.warnings && p.warnings.length > 0 && <ul className="space-y-1.5">{p.warnings.map((w) => <WarningItem key={w.code + w.message} w={w} />)}</ul>}
      {(update.error || unpublish.error) && <Alert>{((update.error ?? unpublish.error) as Error).message}</Alert>}

      {canPublish && (
        <div className="flex flex-wrap gap-2">
          {!onSite && <Button className="flex-1" onClick={() => setModal("publish")} disabled={busy || dirty || !c.enabled}><Globe size={15} /> Publish to website</Button>}
          {onSite && (
            <Button className="flex-1" onClick={() => update.mutate()} disabled={busy || dirty || !p?.update_available || !c.enabled}
              title={p?.update_available ? undefined : "The website already has the latest version."}>
              <RefreshCw size={15} /> {p?.update_available ? "Update on website" : "Up to date"}
            </Button>
          )}
          {onSite && <Button variant="secondary" onClick={() => setModal("publish")} disabled={busy || dirty || !c.enabled}>Options…</Button>}
          {live && p?.can_unpublish && (
            <Button variant="ghost" onClick={() => { if (window.confirm(c.target === "wordpress" ? "Move this post back to draft in WordPress? It will disappear from the website." : "Ask your website to take this article down?")) unpublish.mutate(); }} disabled={busy}>
              <Undo2 size={15} /> Unpublish
            </Button>
          )}
        </div>
      )}
      {canPublish && dirty && <p className="text-xs text-muted">Save your changes first.</p>}

      {status === "published" && p?.remote_url && (
        <div className="space-y-2 border-t border-line pt-2.5">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Live page</h3>
            {canPublish && <Button variant="ghost" className="h-7 px-2" onClick={() => check.mutate()} disabled={check.isPending}><RefreshCw size={13} className={check.isPending ? "animate-spin" : ""} /> Check again</Button>}
          </div>
          {p.check ? <CheckResult c={p.check} /> : <p className="text-xs text-muted">Not checked yet.</p>}
          <div className="flex flex-wrap gap-2">
            {p.search_console_url && (
              <a href={p.search_console_url} target="_blank" rel="noreferrer"><Button variant="secondary" className="h-8"><Search size={14} /> Request indexing in Google</Button></a>
            )}
            {canPublish && (c.indexnow_key
              ? <Button variant="secondary" className="h-8" onClick={() => ping.mutate()} disabled={ping.isPending}><Send size={14} /> Notify search engines</Button>
              : <Button variant="ghost" className="h-8" onClick={() => setModal("indexnow")}>Set up IndexNow</Button>)}
          </div>
          {p.search_console_url && <p className="text-xs text-muted">Opens Search Console's URL inspection. Press “Request indexing” there.</p>}
          {(ping.data ?? p.indexnow) && <p className={`text-xs ${(ping.data ?? p.indexnow)!.ok ? "text-emerald-700" : "text-danger"}`}>{(ping.data ?? p.indexnow)!.message}</p>}
          {ping.error && <Alert>{(ping.error as Error).message}</Alert>}
        </div>
      )}
      {modal === "publish" && <PublishModal article={article} item={item} onClose={() => setModal(null)} onStarted={setJobId} />}
      {modal === "indexnow" && <IndexNowModal connection={c} onClose={() => setModal(null)} onDone={refetch} />}
    </div>
  );
}

export function WebsitePanel({ article, dirty }: { article: Article; dirty: boolean }) {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["seo-website", article.id, article.updated_at], queryFn: () => api<WebsiteState>(`/seo/articles/${article.id}/website`) });
  const [picked, setPicked] = useState<string | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const canBundle = can("seo.manage") || can("seo.publish");
  const items = q.data?.items ?? [];
  const current = items.find((i) => i.connection.id === picked) ?? items.find((i) => i.publication) ?? items[0];

  return (
    <Card>
      <h2 className="mb-3 flex items-center gap-2 font-semibold"><Globe size={17} className="text-primary-700" /> Publish to website</h2>
      {q.isLoading && <Spinner label="Loading" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.data && items.length === 0 && (
        <p className="text-sm text-muted">
          Connect your WordPress site or your own website to publish with one click.{" "}
          {can("integration.manage") ? <Link to="/settings/integrations" className="text-primary-700 underline">Connect a website</Link> : "Ask an administrator to connect it in Settings → Integrations."}
        </p>
      )}
      {items.length > 1 && (
        <Field label="Website">
          <Select value={current?.connection.id} onChange={(e) => setPicked(e.target.value)}>
            {items.map((i) => <option key={i.connection.id} value={i.connection.id}>{i.connection.name}{i.publication && i.publication.status !== "none" ? ` (${STATUS[i.publication.status]?.label ?? i.publication.status})` : ""}</option>)}
          </Select>
        </Field>
      )}
      {current && q.data && (
        <div className={items.length > 1 ? "mt-3" : ""}>
          <ConnectionBlock key={current.connection.id} article={article} item={current} dirty={dirty} canPublish={q.data.can_publish} refetch={() => q.refetch()} />
        </div>
      )}
      {q.data && (
        <div className="mt-3">
          <Redirects article={article} redirects={q.data.redirects} items={items} canPublish={q.data.can_publish} refetch={() => q.refetch()} />
        </div>
      )}
      {canBundle && (
        <div className="mt-3 border-t border-line pt-3">
          <Button variant="secondary" className="w-full" disabled={dirty || downloading}
            onClick={() => { setDlError(null); setDownloading(true); downloadBundle(article.id).catch((e) => setDlError((e as Error).message)).finally(() => setDownloading(false)); }}>
            <Download size={15} /> {downloading ? "Preparing…" : "Download for website (.zip)"}
          </Button>
          <p className="mt-1 text-xs text-muted">For static or Git-based sites: a ready page with all search and social tags, the cover image, MDX and the schema.</p>
          {dlError && <div className="mt-2"><Alert>{dlError}</Alert></div>}
        </div>
      )}
    </Card>
  );
}
