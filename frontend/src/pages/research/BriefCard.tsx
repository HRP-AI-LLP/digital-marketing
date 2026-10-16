import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Bookmark, BookmarkCheck, CalendarHeart, ChevronDown, ExternalLink, FileText, ListPlus, PenLine, RotateCcw, Sparkles, X,
} from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Button } from "../../components/ui";
import { CONFIDENCE_LABEL, THEME_TONE, fmtDay, type Brief } from "./types";

const SCORE_LABELS: [keyof Brief["scores"], string][] = [
  ["relevance", "Fit with our offerings"], ["timeliness", "Timeliness"], ["novelty", "Fresh for us"],
  ["business_value", "Business value"], ["evidence", "Evidence"],
];

function ScoreRing({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const r = 17;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative h-11 w-11 shrink-0" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={`Score ${pct} out of 100`}>
      <svg viewBox="0 0 40 40" className="h-11 w-11 -rotate-90">
        <circle cx="20" cy="20" r={r} fill="none" stroke="rgb(226 232 240)" strokeWidth="4" />
        <circle cx="20" cy="20" r={r} fill="none" stroke="url(#ring)" strokeWidth="4" strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`} />
        <defs><linearGradient id="ring" x1="0" x2="1"><stop offset="0" stopColor="#075492" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
      </svg>
      <span className="absolute inset-0 grid place-items-center text-[13px] font-semibold tabular-nums">{pct}</span>
    </div>
  );
}

export function ThemeChip({ theme, label }: { theme: string; label: string }) {
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${THEME_TONE[theme] ?? "bg-ink/5 text-muted ring-line"}`}>{label}</span>;
}

export default function BriefCard({ b, today }: { b: Brief; today: string }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["research"] });
  const patch = useMutation({
    mutationFn: (status: Brief["status"]) => api<Brief>(`/research/briefs/${b.id}`, { method: "PATCH", json: { status } }),
    onSuccess: refresh,
  });
  const topic = useMutation({ mutationFn: () => api(`/research/briefs/${b.id}/topic`, { method: "POST" }), onSuccess: refresh });
  const article = useMutation({
    mutationFn: () => api<{ article: { id: string } }>(`/research/briefs/${b.id}/article`, { method: "POST", json: {} }),
    onSuccess: (r) => { refresh(); nav(`/seo/${r.article.id}`); },
  });
  const err = (patch.error || topic.error || article.error) as Error | null;
  const canManage = can("research.manage");
  const publishers = [...new Set(b.facts.map((f) => f.publisher).filter(Boolean))] as string[];
  const inTopics = !!b.opportunity_id;
  const dim = b.status === "dismissed";

  return (
    <article className={`group relative flex flex-col rounded-xl border border-line bg-surface p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:shadow-[0_12px_40px_rgba(6,47,89,0.08)] ${dim ? "opacity-60" : ""}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <ThemeChip theme={b.theme} label={b.theme_label} />
            {b.occasion && (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-600 px-2 py-0.5 text-xs font-medium text-white">
                <CalendarHeart size={12} /> {b.occasion.name} · {fmtDay(b.occasion.date)}
              </span>
            )}
            {b.status === "saved" && <span className="inline-flex items-center gap-1 text-xs font-medium text-brand"><BookmarkCheck size={13} /> Saved</span>}
            {b.status === "used" && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-200">Used</span>}
            {inTopics && b.status !== "used" && <span className="text-xs text-muted">In planner topics</span>}
            {b.run_date !== today && <span className="text-xs text-muted">{fmtDay(b.run_date)}</span>}
          </div>
          <h3 className="text-[17px] font-semibold leading-snug text-ink">{b.title}</h3>
        </div>
        <ScoreRing value={b.score} />
      </div>

      {b.angle && <p className="mt-2 text-[15px] text-ink/80">{b.angle}</p>}
      {b.why_now && (
        <div className="mt-3 rounded-lg border-l-[3px] border-secondary-400 bg-accent-tint/70 px-3 py-2 text-sm">
          <span className="font-medium text-secondary-800">Why now: </span>{b.why_now}
        </div>
      )}
      {b.greeting && <p className="mt-2 text-sm italic text-muted">“{b.greeting}”</p>}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {b.formats.map((f) => (
          <span key={f.format} title={f.note || undefined} className="rounded-md bg-ink/[0.04] px-2 py-0.5 text-xs text-ink/80 ring-1 ring-inset ring-line">{f.label}</span>
        ))}
        {b.article.keyword && b.formats.some((f) => f.format === "seo_article") && (
          <span className="rounded-md px-2 py-0.5 text-xs text-muted">Keyword: <span className="text-ink/80">{b.article.keyword}</span></span>
        )}
      </div>

      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="mt-3 inline-flex items-center gap-1 self-start text-sm text-brand hover:underline">
        <ChevronDown size={15} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        {b.facts.length ? `${b.facts.length} fact${b.facts.length > 1 ? "s" : ""}${publishers.length ? ` from ${publishers.slice(0, 2).join(", ")}${publishers.length > 2 ? ` +${publishers.length - 2}` : ""}` : ""}` : "Details"}
      </button>

      {open && (
        <div className="mt-3 space-y-4 border-t border-line pt-3">
          {b.facts.length > 0 && (
            <ol className="space-y-2.5">
              {b.facts.map((f, i) => (
                <li key={i} className="flex gap-2.5 text-sm">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-primary-50 text-[11px] font-semibold text-primary-700">{i + 1}</span>
                  <div className="min-w-0">
                    <p>{f.fact}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                      {f.publisher && <span>{f.publisher}</span>}
                      {f.date && <span>· {fmtDay(f.date, { day: "numeric", month: "short", year: "numeric" })}</span>}
                      {f.source_url && (
                        <a href={f.source_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-brand hover:underline">
                          · Open source <ExternalLink size={11} />
                        </a>
                      )}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
          {b.occasion && (
            <p className="text-xs text-muted">Date source: {CONFIDENCE_LABEL[b.occasion.confidence] ?? b.occasion.confidence}{b.occasion.regions.length ? ` · Regions: ${b.occasion.regions.join(", ")}` : ""}</p>
          )}
          <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {SCORE_LABELS.map(([k, label]) => {
              const v = Math.round((b.scores[k] ?? 0) * 100);
              return (
                <div key={k}>
                  <div className="flex justify-between text-xs"><span className="text-muted">{label}</span><span className="tabular-nums">{v}</span></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-gradient-to-r from-primary-600 to-secondary-500" style={{ width: `${v}%` }} /></div>
                </div>
              );
            })}
          </div>
          {b.reason && <p className="text-sm text-muted"><Sparkles size={13} className="mr-1 inline text-secondary-600" />{b.reason}</p>}
          {(b.audience || b.cta.text || b.hashtags.length > 0) && (
            <dl className="grid gap-2 text-sm sm:grid-cols-[110px_1fr]">
              {b.audience && <><dt className="text-muted">Audience</dt><dd>{b.audience}</dd></>}
              {b.cta.text && <><dt className="text-muted">Call to action</dt><dd>{b.cta.text}{b.cta.url && <> · <a className="text-brand hover:underline" href={b.cta.url} target="_blank" rel="noreferrer noopener">{b.cta.url.replace(/^https?:\/\//, "")}</a></>}</dd></>}
              {b.hashtags.length > 0 && <><dt className="text-muted">Hashtags</dt><dd className="text-brand-deep">{b.hashtags.join(" ")}</dd></>}
              {b.article.outline && b.article.outline.length > 0 && <><dt className="text-muted">Article outline</dt><dd><ul className="list-disc pl-4">{b.article.outline.map((h) => <li key={h}>{h}</li>)}</ul></dd></>}
            </dl>
          )}
          {b.formats.some((f) => f.note) && (
            <ul className="space-y-1 text-sm">
              {b.formats.filter((f) => f.note).map((f) => <li key={f.format}><span className="font-medium">{f.label}:</span> <span className="text-ink/80">{f.note}</span></li>)}
            </ul>
          )}
        </div>
      )}

      {err && <p className="mt-3 text-sm text-danger" role="alert">{err.message}</p>}

      <div className="mt-auto flex flex-wrap items-center gap-1 pt-4">
        {can("content.create") && !dim && (
          <Button className="h-8 !px-2.5" onClick={() => nav(`/posts/new?brief=${b.id}`)}><PenLine size={14} /> Create post</Button>
        )}
        {can("seo.manage") && !dim && (!b.occasion || b.formats.some((f) => f.format === "seo_article")) && (
          <Button variant="secondary" className="h-8 !px-2.5" disabled={article.isPending} onClick={() => article.mutate()}>
            <FileText size={14} /> {article.isPending ? "Starting…" : "Write article"}
          </Button>
        )}
        {can("content.create") && !dim && !inTopics && (
          <Button variant="ghost" className="h-8 !gap-1.5 !px-2" disabled={topic.isPending} onClick={() => topic.mutate()} title="Let scheduled posts use this idea">
            <ListPlus size={14} /> Add to topics
          </Button>
        )}
        {canManage && (
          <div className="ml-auto flex gap-0.5">
            {b.status === "new" && (
              <Button variant="ghost" className="h-8 w-8 !px-0" title="Save for later" aria-label={`Save ${b.title}`} onClick={() => patch.mutate("saved")}><Bookmark size={15} /></Button>
            )}
            {b.status === "saved" && (
              <Button variant="ghost" className="h-8 w-8 !px-0 !text-brand" title="Saved. Click to unsave" aria-label={`Unsave ${b.title}`} onClick={() => patch.mutate("new")}><BookmarkCheck size={15} /></Button>
            )}
            {dim ? (
              <Button variant="ghost" className="h-8 px-2" onClick={() => patch.mutate("new")}><RotateCcw size={14} /> Restore</Button>
            ) : b.status !== "used" && (
              <Button variant="ghost" className="h-8 w-8 !px-0" title="Dismiss" aria-label={`Dismiss ${b.title}`} onClick={() => patch.mutate("dismissed")}><X size={15} /></Button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

export function sourceCount(b: Brief) {
  return new Set(b.facts.map((f) => f.source_url).filter(Boolean)).size;
}
