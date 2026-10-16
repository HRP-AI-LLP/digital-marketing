import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, Download, FileOutput, Plus, Share2, Trash2, TriangleAlert, Wand2, X } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import {
  Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Spinner, StatusBadge, Textarea, formatDate,
} from "../../components/ui";
import { Markdown } from "./Markdown";
import { JobProgress } from "./NewArticle";
import { scoreTone, type Article, type Faq, type SocialAccount } from "./types";

type Draft = { title: string; body_md: string; meta_title: string; meta_description: string; slug: string; canonical_url: string; faq: Faq[] };

const toDraft = (a: Article): Draft => ({
  title: a.title, body_md: a.body_md, meta_title: a.meta_title ?? "", meta_description: a.meta_description ?? "",
  slug: a.slug, canonical_url: a.canonical_url, faq: a.faq.map((f) => ({ ...f })),
});

function Counter({ value, min, max }: { value: string; min: number; max: number }) {
  const n = value.length;
  const ok = n >= min && n <= max;
  return <span className={`text-xs tabular-nums ${ok ? "text-emerald-700" : n > max ? "text-danger" : "text-amber-700"}`}>{n} / {min}–{max}</span>;
}

function ScorePanel({ article, dirty }: { article: Article; dirty: boolean }) {
  const failing = article.seo_checks.filter((c) => !c.ok);
  const passing = article.seo_checks.filter((c) => c.ok);
  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">SEO score</h2>
        <span className={`text-3xl font-bold tabular-nums ${scoreTone(article.seo_score)}`} aria-label={`SEO score ${article.seo_score} out of 100`}>
          {article.seo_score}<span className="text-base font-medium text-muted">/100</span>
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
        <div className="h-full rounded-full bg-gradient-to-r from-primary-600 to-secondary-500" style={{ width: `${article.seo_score}%` }} />
      </div>
      {dirty && <p className="mt-2 text-xs text-muted">Save to update the score.</p>}
      <ul className="mt-3 space-y-2 text-sm">
        {[...failing, ...passing].map((c) => (
          <li key={c.id} className="flex gap-2">
            {c.ok ? <Check size={16} className="mt-0.5 shrink-0 text-emerald-600" aria-label="Passed" />
              : c.severity === "warn" ? <TriangleAlert size={16} className="mt-0.5 shrink-0 text-amber-600" aria-label="Suggestion" />
                : <X size={16} className="mt-0.5 shrink-0 text-danger" aria-label="Needs work" />}
            <span><span className="font-medium">{c.name}</span><span className="block text-xs text-muted">{c.detail}</span></span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function GooglePreview({ title, description, url }: { title: string; description: string; url: string }) {
  let crumb = url;
  try { const u = new URL(url); crumb = `${u.host} › ${u.pathname.split("/").filter(Boolean).join(" › ")}`; } catch { /* relative */ }
  return (
    <div className="rounded-lg border border-line bg-white p-3" aria-label="Google result preview">
      <div className="truncate text-xs text-slate-600">{crumb}</div>
      <div className="mt-0.5 line-clamp-1 text-[17px] leading-snug text-[#1a0dab]">{title || "Meta title"}</div>
      <div className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-slate-600">{description || "Meta description"}</div>
    </div>
  );
}

function ShareModal({ article, onClose }: { article: Article; onClose: () => void }) {
  const accounts = useQuery({ queryKey: ["social-accounts"], queryFn: () => api<SocialAccount[]>("/social/accounts") });
  const [picked, setPicked] = useState<string[]>([]);
  const share = useMutation({
    mutationFn: () => api<{ count: number }>(`/seo/articles/${article.id}/repurpose`, { method: "POST", json: { account_ids: picked } }),
  });
  const usable = (accounts.data ?? []).filter((a) => a.platform !== "google" && !["removed", "error", "expired"].includes(a.status));
  return (
    <Modal title="Share as posts" onClose={onClose}>
      {share.isSuccess ? (
        <div className="space-y-4">
          <Alert tone="info">{share.data.count} post(s) created and sent for review. Reviewers choose when they go out.</Alert>
          <div className="flex justify-end gap-2">
            <Link to="/approvals"><Button variant="secondary">Open approvals</Button></Link>
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-muted">One post per account, written for each platform and linking to <span className="break-all font-medium text-ink">{article.canonical_url}</span>.</p>
          {accounts.isLoading && <Spinner label="Loading accounts" />}
          {accounts.error && <Alert>{(accounts.error as Error).message}</Alert>}
          {accounts.data && usable.length === 0 && <Alert tone="warn">No connected accounts. Connect one in <Link className="underline" to="/settings/accounts">Social accounts</Link>.</Alert>}
          <fieldset className="space-y-1.5">
            <legend className="sr-only">Accounts</legend>
            {usable.map((a) => (
              <label key={a.id} className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-sm hover:border-primary-300">
                <input type="checkbox" checked={picked.includes(a.id)} className="h-4 w-4 accent-primary-600"
                  onChange={(e) => setPicked(e.target.checked ? [...picked, a.id] : picked.filter((x) => x !== a.id))} />
                <span className="font-medium">{a.name}</span><Badge>{a.platform.replace(/_/g, " ")}</Badge>
              </label>
            ))}
          </fieldset>
          {share.error && <Alert>{(share.error as Error).message}</Alert>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={() => share.mutate()} disabled={!picked.length || share.isPending}>
              {share.isPending ? "Writing posts…" : `Create ${picked.length || ""} post${picked.length === 1 ? "" : "s"}`}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

async function downloadFile(id: string, format: "mdx" | "html" | "json") {
  const r = await api<{ filename: string; media_type: string; content: string }>(`/seo/articles/${id}/download?format=${format}&as_json=true`);
  const url = URL.createObjectURL(new Blob([r.content], { type: r.media_type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = r.filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ArticleEditor() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const manage = can("seo.manage");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["seo-article", id],
    queryFn: () => api<Article>(`/seo/articles/${id}`),
    refetchInterval: (query) => (["queued", "writing"].includes(query.state.data?.status ?? "") ? 4000 : false),
  });
  const article = q.data;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [view, setView] = useState<"split" | "write" | "preview">("split");
  const [rewriteHeading, setRewriteHeading] = useState("");
  const [instruction, setInstruction] = useState("");
  const [exportJob, setExportJob] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [dlError, setDlError] = useState<string | null>(null);

  useEffect(() => { if (article) setDraft(toDraft(article)); }, [article?.id, article?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => !!article && !!draft && JSON.stringify(toDraft(article)) !== JSON.stringify(draft), [article, draft]);
  const setArticle = (a: Article) => { qc.setQueryData(["seo-article", id], a); qc.invalidateQueries({ queryKey: ["seo-articles"] }); };

  const save = useMutation({
    mutationFn: () => {
      const d = draft!;
      return api<Article>(`/seo/articles/${id}`, {
        method: "PATCH",
        json: { ...d, faq: d.faq.filter((f) => f.q.trim()), canonical_url: d.canonical_url === article!.canonical_url ? undefined : d.canonical_url },
      });
    },
    onSuccess: setArticle,
  });
  const rescore = useMutation({ mutationFn: () => api(`/seo/articles/${id}/score`, { method: "POST" }), onSuccess: () => q.refetch() });
  const rewrite = useMutation({
    mutationFn: () => api<Article>(`/seo/articles/${id}/rewrite-section`, { method: "POST", json: { heading: rewriteHeading, instruction } }),
    onSuccess: (a) => { setArticle(a); setInstruction(""); },
  });
  const exp = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/seo/articles/${id}/export`, { method: "POST" }),
    onSuccess: (r) => setExportJob(r.job_id),
  });
  const exportStatus = useJob(exportJob, () => q.refetch());
  const del = useMutation({
    mutationFn: () => api(`/seo/articles/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["seo-articles"] }); navigate("/seo"); },
  });

  if (q.isLoading) return <Spinner label="Loading article" />;
  if (q.error || !article) return <Alert>{(q.error as Error)?.message ?? "Article not found."}</Alert>;

  const back = <Link to="/seo" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={14} /> All articles</Link>;

  if (article.status === "queued" || article.status === "writing") {
    return (
      <>
        {back}
        <PageHeader title={article.title} description={`Keyword: ${article.keyword}`} />
        <Card><Spinner label="The article is being written. This page updates when it's ready." /></Card>
      </>
    );
  }
  if (!draft) return null;
  const set = <K extends keyof Draft>(key: K, v: Draft[K]) => setDraft({ ...draft, [key]: v });
  const headingOptions = article.headings.filter((h) => h.level >= 2 && h.level <= 3);
  const exporting = !!exportJob && exportStatus?.status !== "done" && exportStatus?.status !== "failed";

  return (
    <>
      {back}
      <PageHeader
        title={article.title}
        description={`Keyword: ${article.keyword} · ${article.word_count.toLocaleString()} words · updated ${formatDate(article.updated_at)}`}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={article.status} />
            {manage && <Button variant="secondary" onClick={() => setSharing(true)} disabled={dirty}><Share2 size={16} /> Share as posts</Button>}
            {manage && <Button variant="secondary" onClick={() => exp.mutate()} disabled={dirty || exp.isPending || exporting}><FileOutput size={16} /> Export</Button>}
            {manage && <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}</Button>}
          </div>
        }
      />
      <div className="mb-4 space-y-2">
        {article.status === "error" && article.error && <Alert>{article.error}</Alert>}
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        {exp.error && <Alert>{(exp.error as Error).message}</Alert>}
        {exportJob && <JobProgress job={exportStatus} label="Exporting" />}
        {dlError && <Alert>{dlError}</Alert>}
        {(article.exported || article.status === "exported") && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-sm">
            <span className="text-muted">Exported to <code className="text-ink">storage/{article.export_path}</code>. Download:</span>
            {(["mdx", "html", "json"] as const).map((f) => (
              <Button key={f} variant="ghost" className="h-7 px-2" onClick={() => downloadFile(id, f).catch((e) => setDlError((e as Error).message))}>
                <Download size={14} /> {f === "json" ? "meta.json" : f.toUpperCase()}
              </Button>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <Card className="p-0">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
              <div role="tablist" aria-label="Editor view" className="flex gap-1">
                {(["split", "write", "preview"] as const).map((v) => (
                  <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
                    className={`rounded-md px-2.5 py-1 text-sm ${view === v ? "bg-primary-50 font-semibold text-primary-800" : "text-muted hover:text-ink"}`}>
                    {v === "split" ? "Split" : v === "write" ? "Markdown" : "Preview"}
                  </button>
                ))}
              </div>
              {dirty && <span className="text-xs text-amber-700">Unsaved changes</span>}
            </div>
            <div className={`grid ${view === "split" ? "lg:grid-cols-2" : ""}`}>
              {view !== "preview" && (
                <label className="block">
                  <span className="sr-only">Article Markdown</span>
                  <textarea value={draft.body_md} onChange={(e) => set("body_md", e.target.value)} readOnly={!manage} spellCheck
                    className="h-[70vh] w-full resize-none border-0 bg-transparent p-4 font-mono text-[13px] leading-6 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary-500/20" />
                </label>
              )}
              {view !== "write" && (
                <div className={`h-[70vh] overflow-y-auto p-5 ${view === "split" ? "border-t border-line lg:border-l lg:border-t-0" : ""}`}>
                  {article.cover?.url && <img src={article.cover.url} alt={article.cover.alt_text ?? article.title} className="mb-4 w-full rounded-lg" />}
                  <Markdown source={draft.body_md} />
                </div>
              )}
            </div>
          </Card>

          {manage && (
            <Card>
              <h2 className="mb-3 font-semibold">Rewrite a section</h2>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] sm:items-end">
                <Field label="Section">
                  <select value={rewriteHeading} onChange={(e) => setRewriteHeading(e.target.value)}
                    className="h-10 w-full rounded-lg border border-line bg-surface px-2.5 text-[15px]">
                    <option value="">Choose a heading</option>
                    {headingOptions.map((h, i) => <option key={`${h.text}-${i}`} value={h.text}>{h.level === 3 ? "— " : ""}{h.text}</option>)}
                  </select>
                </Field>
                <Field label="Instruction">
                  <Input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Add a practical example, make it shorter…" />
                </Field>
                <Button onClick={() => rewrite.mutate()} disabled={!rewriteHeading || dirty || rewrite.isPending}>
                  <Wand2 size={16} /> {rewrite.isPending ? "Rewriting…" : "Rewrite"}
                </Button>
              </div>
              {dirty && <p className="mt-2 text-xs text-muted">Save your changes before rewriting a section.</p>}
              {rewrite.error && <div className="mt-2"><Alert>{(rewrite.error as Error).message}</Alert></div>}
            </Card>
          )}
        </div>

        <aside className="space-y-4">
          <ScorePanel article={article} dirty={dirty} />
          {manage && !dirty && <Button variant="ghost" className="w-full" onClick={() => rescore.mutate()} disabled={rescore.isPending}>Check score again</Button>}

          <Card>
            <h2 className="mb-3 font-semibold">Search appearance</h2>
            <div className="space-y-3">
              <Field label="Title (H1)"><Input value={draft.title} onChange={(e) => set("title", e.target.value)} readOnly={!manage} /></Field>
              <div>
                <div className="mb-1.5 flex items-center justify-between"><span className="text-sm font-medium">Meta title</span><Counter value={draft.meta_title} min={30} max={60} /></div>
                <Input aria-label="Meta title" value={draft.meta_title} onChange={(e) => set("meta_title", e.target.value)} readOnly={!manage} maxLength={200} />
              </div>
              <div>
                <div className="mb-1.5 flex items-center justify-between"><span className="text-sm font-medium">Meta description</span><Counter value={draft.meta_description} min={150} max={160} /></div>
                <Textarea aria-label="Meta description" rows={3} value={draft.meta_description} onChange={(e) => set("meta_description", e.target.value)} readOnly={!manage} maxLength={400} />
              </div>
              <Field label="URL slug"><Input value={draft.slug} onChange={(e) => set("slug", e.target.value)} readOnly={!manage} /></Field>
              <Field label="Canonical URL" hint="Where the article will live on your website."><Input value={draft.canonical_url} onChange={(e) => set("canonical_url", e.target.value)} readOnly={!manage} /></Field>
              <GooglePreview title={draft.meta_title} description={draft.meta_description} url={draft.canonical_url} />
            </div>
          </Card>

          <Card>
            <h2 className="mb-3 font-semibold">Cover image</h2>
            {article.cover?.url
              ? <img src={article.cover.url} alt={article.cover.alt_text ?? article.title} className="w-full rounded-lg border border-line" />
              : <p className="text-sm text-muted">No cover yet. One is created with the media studio after writing when an image route is set up.</p>}
          </Card>

          <Card>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">FAQ</h2>
              {manage && <Button variant="ghost" className="h-7 px-2" onClick={() => set("faq", [...draft.faq, { q: "", a: "" }])}><Plus size={14} /> Add</Button>}
            </div>
            {draft.faq.length === 0 && <p className="text-sm text-muted">No questions yet. 4–6 help with search features.</p>}
            <div className="space-y-3">
              {draft.faq.map((f, i) => (
                <div key={i} className="space-y-1.5 rounded-lg border border-line p-2.5">
                  <div className="flex gap-1.5">
                    <Input aria-label={`Question ${i + 1}`} value={f.q} placeholder="Question" readOnly={!manage}
                      onChange={(e) => set("faq", draft.faq.map((x, j) => (j === i ? { ...x, q: e.target.value } : x)))} />
                    {manage && <button aria-label={`Remove question ${i + 1}`} className="px-1 text-muted hover:text-danger"
                      onClick={() => set("faq", draft.faq.filter((_, j) => j !== i))}><Trash2 size={15} /></button>}
                  </div>
                  <Textarea aria-label={`Answer ${i + 1}`} rows={3} value={f.a} placeholder="Answer" readOnly={!manage}
                    onChange={(e) => set("faq", draft.faq.map((x, j) => (j === i ? { ...x, a: e.target.value } : x)))} />
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">These feed the FAQPage schema. Update the FAQ section in the article text too.</p>
          </Card>

          {manage && (
            <Button variant="danger" className="w-full" disabled={del.isPending}
              onClick={() => { if (window.confirm("Delete this article and its exported files?")) del.mutate(); }}>
              <Trash2 size={16} /> Delete article
            </Button>
          )}
        </aside>
      </div>
      {sharing && <ShareModal article={article} onClose={() => setSharing(false)} />}
    </>
  );
}
