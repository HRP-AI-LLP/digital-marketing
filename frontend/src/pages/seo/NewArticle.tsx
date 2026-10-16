import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Lightbulb } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Badge, Button, Field, Input, Modal, Select, Spinner, Textarea } from "../../components/ui";
import type { ArticleSummary, KeywordIdea } from "./types";

const SOURCE_LABEL: Record<string, string> = {
  search_console: "Search Console", competitor: "Competitor gap", opportunity: "Opportunity", company: "Your offer", seed: "Your topic",
};

export function JobProgress({ job, label }: { job: Job | undefined; label: string }) {
  if (!job) return <Spinner label={label} />;
  if (job.status === "failed") return <Alert>{job.error ?? "The task failed."}</Alert>;
  if (job.status === "done") return null;
  return <Spinner label={job.progress || (job.status === "queued" ? "Waiting for the worker…" : label)} />;
}

export function NewArticleModal({ initialKeyword = "", onClose }: { initialKeyword?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState(initialKeyword);
  const [secondary, setSecondary] = useState("");
  const [audience, setAudience] = useState("");
  const [words, setWords] = useState("1500");
  const [intent, setIntent] = useState("");
  const [tone, setTone] = useState("");
  const [notes, setNotes] = useState("");
  const [ideasJob, setIdeasJob] = useState<string | null>(null);
  const [writeJob, setWriteJob] = useState<{ job: string; article: string } | null>(null);

  const suggest = useMutation({
    mutationFn: () => api<{ job_id: string }>("/seo/keywords", { method: "POST", json: { seed: keyword || null, background: true } }),
    onSuccess: (r) => setIdeasJob(r.job_id),
  });
  const ideas = useJob(ideasJob);
  const ideaList = (ideas?.status === "done" ? (ideas.result?.ideas as KeywordIdea[] | undefined) : undefined) ?? [];

  const create = useMutation({
    mutationFn: () => api<{ article: ArticleSummary; job_id: string }>("/seo/articles", {
      method: "POST",
      json: {
        keyword, secondary_keywords: secondary.split(",").map((s) => s.trim()).filter(Boolean), audience: audience || null,
        word_target: Number(words), search_intent: intent || null, tone: tone || null, notes: notes || null,
      },
    }),
    onSuccess: (r) => { setWriteJob({ job: r.job_id, article: r.article.id }); qc.invalidateQueries({ queryKey: ["seo-articles"] }); },
  });
  const writing = useJob(writeJob?.job, (j) => {
    qc.invalidateQueries({ queryKey: ["seo-articles"] });
    if (j.status === "done" && writeJob) { onClose(); navigate(`/seo/${writeJob.article}`); }
  });

  if (writeJob) {
    return (
      <Modal title="Writing your article" onClose={onClose}>
        <div className="space-y-4">
          <p className="text-sm text-muted">This takes a few minutes: brief, outline, each section, FAQ and meta tags. You can close this window; you'll get a notification when it's ready.</p>
          <JobProgress job={writing} label="Writing" />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => { onClose(); navigate(`/seo/${writeJob.article}`); }}>Open the article</Button>
            <Button onClick={onClose}>Close</Button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="New SEO article" onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        {create.error && <Alert>{(create.error as Error).message}</Alert>}
        <Field label="Main keyword" hint="The search phrase this article should rank for.">
          <div className="flex gap-2">
            <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} required minLength={2} maxLength={200} placeholder="e.g. HRMS for small business" />
            <Button type="button" variant="secondary" onClick={() => suggest.mutate()} disabled={suggest.isPending || (!!ideasJob && ideas?.status !== "done" && ideas?.status !== "failed")}>
              <Lightbulb size={16} /> Suggest keywords
            </Button>
          </div>
        </Field>
        {suggest.error && <Alert>{(suggest.error as Error).message}</Alert>}
        {ideasJob && ideas?.status !== "done" && <JobProgress job={ideas} label="Finding keyword ideas" />}
        {ideaList.length > 0 && (
          <div className="max-h-60 space-y-1.5 overflow-y-auto rounded-lg border border-line p-2" aria-label="Keyword ideas">
            {ideaList.map((i) => (
              <button key={i.keyword} type="button" onClick={() => setKeyword(i.keyword)}
                className={`block w-full rounded-md px-2.5 py-2 text-left text-sm hover:bg-primary-50 ${keyword === i.keyword ? "bg-primary-50 ring-1 ring-primary-300" : ""}`}>
                <span className="font-medium">{i.keyword}</span>{" "}
                <Badge tone="brand">{i.intent}</Badge>{" "}
                <Badge>{i.difficulty_guess} difficulty</Badge>{" "}
                <Badge tone="accent">{SOURCE_LABEL[i.source] ?? i.source}</Badge>
                {i.rationale && <span className="mt-0.5 block text-xs text-muted">{i.rationale}</span>}
              </button>
            ))}
          </div>
        )}
        <Field label="Secondary keywords" hint="Comma-separated variants and related phrases.">
          <Input value={secondary} onChange={(e) => setSecondary(e.target.value)} placeholder="hr software, employee management" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Audience">
            <Input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="HR managers at growing companies" />
          </Field>
          <Field label="Length">
            <Select value={words} onChange={(e) => setWords(e.target.value)}>
              {[800, 1200, 1500, 2000, 2500, 3000].map((w) => <option key={w} value={w}>About {w.toLocaleString()} words</option>)}
            </Select>
          </Field>
          <Field label="Search intent" hint="Leave on automatic unless you know.">
            <Select value={intent} onChange={(e) => setIntent(e.target.value)}>
              <option value="">Automatic</option>
              <option value="informational">Informational (learn)</option>
              <option value="commercial">Commercial (compare)</option>
              <option value="transactional">Transactional (buy)</option>
              <option value="navigational">Navigational (find us)</option>
            </Select>
          </Field>
          <Field label="Tone">
            <Input value={tone} onChange={(e) => setTone(e.target.value)} placeholder="Expert and friendly" />
          </Field>
        </div>
        <Field label="Notes for the writer">
          <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Points to cover, products to mention, things to avoid" />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={create.isPending || keyword.trim().length < 2}>Write article</Button>
        </div>
      </form>
    </Modal>
  );
}
