import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Badge, Modal, Spinner } from "../../components/ui";
import type { Answer, PaidHint, PromptResult } from "./types";

export function CostBadge({ cost }: { cost?: "free" | "paid" }) {
  return cost === "paid" ? <Badge tone="accent">Paid, optional</Badge> : <Badge tone="brand">Free</Badge>;
}

/** One engine's result for one prompt: filled dot = mentioned (with rank), ring = not mentioned, cross = failed. */
export function MentionDot({ r, onOpen }: { r?: PromptResult; onOpen?: (id: string) => void }) {
  if (!r) return <span className="text-xs text-muted" title="Not checked in this run">—</span>;
  const label = r.status !== "ok" ? `${r.engine_label}: failed` : r.mentioned
    ? `${r.engine_label}: mentioned${r.position ? `, #${r.position}` : ""}${r.sentiment ? `, ${r.sentiment}` : ""}`
    : `${r.engine_label}: not mentioned${r.competitors.length ? ` (names ${r.competitors.slice(0, 3).join(", ")})` : ""}`;
  const tone = r.sentiment === "negative" ? "bg-danger text-white" : "bg-primary-600 text-white";
  return (
    <button type="button" onClick={() => onOpen?.(r.answer_id)} title={label} aria-label={label}
      className="inline-flex h-7 min-w-7 items-center justify-center rounded-full px-1 text-[11px] font-semibold tabular-nums focus:outline-none focus:ring-2 focus:ring-primary-500/40">
      {r.status !== "ok" ? <span className="text-danger">×</span>
        : r.mentioned ? <span className={`inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1 ${tone}`}>{r.position ?? "✓"}</span>
          : <span className="inline-block h-4 w-4 rounded-full border-2 border-slate-300" />}
    </button>
  );
}

export function DotLegend() {
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
      <span className="inline-flex items-center gap-1.5"><span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-[10px] font-semibold text-white">2</span>Mentioned (number = rank in the answer)</span>
      <span className="inline-flex items-center gap-1.5"><span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-danger text-[10px] font-semibold text-white">1</span>Mentioned negatively</span>
      <span className="inline-flex items-center gap-1.5"><span className="inline-block h-4 w-4 rounded-full border-2 border-slate-300" />Not mentioned</span>
      <span className="inline-flex items-center gap-1.5"><span className="text-danger">×</span>Engine failed</span>
    </p>
  );
}

export function PaidHints({ hints, compact }: { hints: PaidHint[]; compact?: boolean }) {
  if (!hints.length) return null;
  return (
    <Alert tone="info">
      Everything here works with free options. For richer data you can add {compact ? "" : "paid, optional "}engines in{" "}
      <Link className="underline" to="/settings/integrations">Integrations</Link>:{" "}
      {hints.map((h, i) => <span key={h.provider}>{i ? ", " : ""}<strong>{h.label}</strong>{compact ? "" : ` (${h.note.replace(/\.$/, "").toLowerCase()})`}</span>)}.
    </Alert>
  );
}

function highlight(text: string, terms: string[]) {
  const t = terms.filter((x) => x.length >= 3).map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!t.length) return text;
  const re = new RegExp(`(${t.join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i} className="rounded bg-amber-100 px-0.5 text-ink">{part}</mark> : part));
}

export function AnswerModal({ id, onClose, brandTerms = [] }: { id: string; onClose: () => void; brandTerms?: string[] }) {
  const q = useQuery({ queryKey: ["aivis", "answer", id], queryFn: () => api<Answer>(`/aivis/answers/${id}`) });
  const a = q.data;
  return (
    <Modal title={a ? a.engine_label : "Answer"} onClose={onClose} wide>
      {q.isLoading && <Spinner label="Loading answer…" />}
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {a && (
        <div className="space-y-4">
          <div>
            <div className="text-xs text-muted">Prompt</div>
            <p className="font-medium [overflow-wrap:anywhere]">{a.prompt_text}</p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {a.status !== "ok" ? <Badge tone="danger">Failed</Badge> : a.mentioned
              ? <Badge tone="brand">Mentioned{a.position ? ` · #${a.position}` : ""}</Badge> : <Badge>Not mentioned</Badge>}
            {a.sentiment && <Badge tone={a.sentiment === "negative" ? "danger" : a.sentiment === "positive" ? "brand" : "neutral"}>{a.sentiment}</Badge>}
            {a.own_cited && <Badge tone="accent">Cites your site</Badge>}
            <Badge>{a.web ? "Web search" : "From memory (no web search)"}</Badge>
          </div>
          {a.error && <Alert>{a.error}</Alert>}
          {a.analysis?.sentiment_reason && <p className="text-sm text-muted">Tone: {a.analysis.sentiment_reason}</p>}
          {a.answer && (
            <div>
              <div className="mb-1 text-xs text-muted">Full answer</div>
              <div className="max-h-[45vh] overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-canvas p-3 text-sm leading-relaxed [overflow-wrap:anywhere]">
                {highlight(a.answer.replace(/\*\*(.+?)\*\*/g, "$1").replace(/^#{1,6}\s+/gm, ""), brandTerms)}
              </div>
            </div>
          )}
          {!!a.brands?.length && (
            <div>
              <div className="mb-1 text-xs text-muted">Brands in order</div>
              <ol className="flex flex-wrap gap-1.5 text-sm">{a.brands.map((b, i) => <li key={b} className="rounded bg-ink/5 px-1.5 py-0.5">{i + 1}. {b}</li>)}</ol>
            </div>
          )}
          <div>
            <div className="mb-1 text-xs text-muted">Sources cited {a.analysis?.citations_from === "text" ? "(links found in the answer)" : ""}</div>
            {a.citations?.length ? (
              <ul className="space-y-1 text-sm">
                {a.citations.map((c) => (
                  <li key={c.url} className="flex items-start gap-2">
                    <ExternalLink size={14} className="mt-0.5 shrink-0 text-muted" aria-hidden />
                    <a href={c.url} target="_blank" rel="noreferrer noopener" className="min-w-0 text-brand hover:underline [overflow-wrap:anywhere]">{c.title || c.domain}</a>
                    <span className="shrink-0 text-xs text-muted">{c.domain}</span>
                    {c.own && <Badge tone="accent">yours</Badge>}
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">No sources listed.</p>}
          </div>
          <p className="text-xs text-muted">{a.provider === "websearch" ? "Free web search" : `${a.provider} · ${a.model}`} · {new Date(a.created_at).toLocaleString("en-IN")}</p>
        </div>
      )}
    </Modal>
  );
}
