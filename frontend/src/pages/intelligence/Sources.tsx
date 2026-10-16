import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Globe, RefreshCw, Upload } from "lucide-react";
import { api } from "../../api/client";
import { upload } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Spinner, StatusBadge, formatDate } from "../../components/ui";
import { DeleteButton, ErrorText, JobStatus, SectionTitle, useIntelJobs } from "./shared";
import type { JobStart, Overview, Source } from "./types";

const ACCEPT = ".pdf,.docx,.pptx,.txt,.md,.markdown,.html,.htm,.csv";

function formatSize(bytes: number | null) {
  if (!bytes) return "";
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function CrawlForm({ defaultUrl }: { defaultUrl: string }) {
  const { track } = useIntelJobs();
  const qc = useQueryClient();
  const [url, setUrl] = useState(defaultUrl);
  const [maxPages, setMaxPages] = useState("150");
  const crawl = useMutation({
    mutationFn: () => api<JobStart & { source: Source }>("/intel/sources/website", {
      method: "POST", json: { url, max_pages: Number(maxPages) || 150 },
    }),
    onSuccess: (r) => { track(`crawl:${r.source.id}`, r.job_id); qc.invalidateQueries({ queryKey: ["intel"] }); },
  });
  return (
    <Card>
      <div className="flex items-center gap-2"><Globe size={17} className="text-primary-700" /><h3 className="font-semibold">Read a website</h3></div>
      <p className="mt-1 text-sm text-muted">Follows the sitemap and links on the same site and respects robots.txt. Your own website then updates the company profile.</p>
      <form className="mt-4 grid gap-3 sm:grid-cols-[1fr_120px_auto] sm:items-end" onSubmit={(e: FormEvent) => { e.preventDefault(); crawl.mutate(); }}>
        <Field label="Website address"><Input required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.example.com" /></Field>
        <Field label="Page limit"><Input inputMode="numeric" value={maxPages} onChange={(e) => setMaxPages(e.target.value.replace(/\D/g, ""))} /></Field>
        <Button type="submit" disabled={crawl.isPending || !url.trim()}>{crawl.isPending ? "Starting…" : "Crawl website"}</Button>
      </form>
      <div className="mt-3"><ErrorText error={crawl.error} /></div>
    </Card>
  );
}

function UploadForm() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const send = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file!);
      return upload<Source>("/intel/sources/upload", form);
    },
    onSuccess: () => {
      setFile(null);
      if (input.current) input.current.value = "";
      qc.invalidateQueries({ queryKey: ["intel"] });
    },
  });
  return (
    <Card>
      <div className="flex items-center gap-2"><FileText size={17} className="text-primary-700" /><h3 className="font-semibold">Upload a document</h3></div>
      <p className="mt-1 text-sm text-muted">Brochures, decks, proposals, product docs or case studies. PDF, Word (.docx), PowerPoint (.pptx), text, Markdown or HTML, up to 25 MB.</p>
      <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (file) send.mutate(); }}>
        <label className="min-w-0 flex-1">
          <span className="mb-1.5 block text-sm font-medium">File</span>
          <input
            ref={input} type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-2 file:text-sm file:font-medium hover:file:border-brand/50"
          />
        </label>
        <Button type="submit" disabled={!file || send.isPending}><Upload size={15} /> {send.isPending ? "Reading the file…" : "Upload document"}</Button>
      </form>
      <div className="mt-3 space-y-2">
        <ErrorText error={send.error} />
        {send.isSuccess && <Alert tone="info">Added “{send.data.title}”. Re-run the analysis to include it in the company profile.</Alert>}
      </div>
    </Card>
  );
}

function SourceRow({ s, canManage }: { s: Source; canManage: boolean }) {
  const qc = useQueryClient();
  const { track } = useIntelJobs();
  const refresh = () => qc.invalidateQueries({ queryKey: ["intel"] });
  const recrawl = useMutation({
    mutationFn: () => api<JobStart>(`/intel/sources/${s.id}/recrawl`, { method: "POST" }),
    onSuccess: (r) => { track(`crawl:${s.id}`, r.job_id); refresh(); },
  });
  const remove = useMutation({ mutationFn: () => api(`/intel/sources/${s.id}`, { method: "DELETE" }), onSuccess: refresh });
  const isWeb = s.kind === "website";
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {isWeb ? <Globe size={15} className="text-muted" /> : <FileText size={15} className="text-muted" />}
            <span className="font-semibold">{s.title}</span>
            <StatusBadge status={s.status} />
            <Badge>{isWeb ? "Website" : "Document"}</Badge>
          </div>
          <div className="mt-0.5 text-sm text-muted">
            {isWeb ? <a href={s.url ?? "#"} target="_blank" rel="noreferrer" className="hover:underline">{s.url}</a> : <>{s.filename} {formatSize(s.size_bytes)}</>}
          </div>
          <div className={`mt-1 text-sm ${s.status === "error" ? "text-danger" : "text-muted"}`}>
            {s.status_message}
            {isWeb && s.last_crawled_at && <> · last read {formatDate(s.last_crawled_at)}</>}
            {!isWeb && <> · added {formatDate(s.created_at)}</>}
          </div>
          {isWeb && s.crawl && (
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
              <Badge tone={s.crawl.sitemap_found ? "brand" : "neutral"}>{s.crawl.sitemap_found ? "Sitemap found" : "No sitemap"}</Badge>
              <Badge tone={s.crawl.robots_found ? "brand" : "neutral"}>{s.crawl.robots_found ? "robots.txt found" : "No robots.txt"}</Badge>
              {!!s.crawl.broken_link_count && <Badge tone="danger">{s.crawl.broken_link_count} broken links</Badge>}
              {!!s.crawl.blocked_by_robots && <Badge>{s.crawl.blocked_by_robots} pages skipped by robots.txt</Badge>}
              {s.crawl.truncated && <Badge tone="accent">Page limit reached</Badge>}
            </div>
          )}
          {(recrawl.error || remove.error) && <p className="mt-1 text-sm text-danger">{((recrawl.error || remove.error) as Error).message}</p>}
        </div>
        {canManage && (
          <div className="flex items-center gap-1">
            {isWeb && (
              <Button variant="secondary" className="h-8 px-3" onClick={() => recrawl.mutate()} disabled={recrawl.isPending || s.status === "running"}>
                <RefreshCw size={14} /> Re-crawl
              </Button>
            )}
            <DeleteButton label={`Delete ${s.title}`} onConfirm={() => remove.mutate()} busy={remove.isPending} />
          </div>
        )}
      </div>
      <div className="mt-2"><JobStatus jobKey={`crawl:${s.id}`} label={`Reading ${s.title}`} /></div>
    </li>
  );
}

export default function SourcesTab({ overview }: { overview: Overview }) {
  const { can } = useAuth();
  const canManage = can("intel.manage");
  const sources = useQuery({
    queryKey: ["intel", "sources"],
    queryFn: () => api<Source[]>("/intel/sources"),
    refetchInterval: (q) => (q.state.data?.some((s) => s.status === "running" || s.status === "pending") ? 4000 : false),
  });
  const defaultUrl = overview.website_source?.url ?? overview.organization_website ?? "";

  return (
    <div className="space-y-8">
      {canManage && (
        <div className="grid gap-4 lg:grid-cols-2">
          <CrawlForm defaultUrl={defaultUrl} />
          <UploadForm />
        </div>
      )}
      <JobStatus jobKey="extract" label="Updating the company profile from new pages" />
      <section>
        <SectionTitle title="Your knowledge sources" description="Posts and articles quote these pages and documents. Competitor websites are on the Competitors tab." />
        {sources.isLoading && <Spinner label="Loading…" />}
        <ErrorText error={sources.error} />
        {sources.data && !sources.data.length && (
          <EmptyState
            title="Nothing here yet"
            body={canManage
              ? "Crawl your website above, or upload a brochure or product document, so posts have real facts to draw on."
              : "Ask an administrator to crawl the company website or upload documents."}
          />
        )}
        {!!sources.data?.length && (
          <Card className="py-1"><ul className="divide-y divide-line">{sources.data.map((s) => <SourceRow key={s.id} s={s} canManage={canManage} />)}</ul></Card>
        )}
      </section>
    </div>
  );
}
