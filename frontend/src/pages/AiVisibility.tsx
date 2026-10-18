import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarClock, Loader2, Play } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, EmptyState, PageHeader, Spinner, StatusBadge, Tabs, formatDate } from "../components/ui";
import CompetitorsTab from "./aivis/Competitors";
import LlmsTab from "./aivis/LlmsTxt";
import OverviewTab from "./aivis/Overview";
import PromptsTab from "./aivis/Prompts";
import RecommendationsTab from "./aivis/Recommendations";
import SettingsTab from "./aivis/Settings";
import { CostBadge, PaidHints } from "./aivis/shared";
import { pct, type Overview, type Run, type Settings } from "./aivis/types";

type Tab = "overview" | "prompts" | "competitors" | "recommendations" | "llms" | "history" | "settings";
const TABS: Tab[] = ["overview", "prompts", "competitors", "recommendations", "llms", "history", "settings"];

function HistoryTab({ tz }: { tz: string }) {
  const q = useQuery({ queryKey: ["aivis", "runs"], queryFn: () => api<Run[]>("/aivis/runs?limit=52") });
  if (q.isLoading) return <Spinner label="Loading checks…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  if (!q.data?.length) return <EmptyState title="No checks yet" body="Every check is kept here so you can see the trend." />;
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="bg-canvas text-left text-xs text-muted">
          <tr><th className="px-4 py-2 font-medium">When</th><th className="px-4 py-2 font-medium">Status</th><th className="px-4 py-2 text-right font-medium">Visibility</th>
            <th className="px-4 py-2 text-right font-medium">Share of voice</th><th className="px-4 py-2 text-right font-medium">Avg. position</th>
            <th className="px-4 py-2 text-right font-medium">Answers</th><th className="px-4 py-2 font-medium">Engines</th></tr>
        </thead>
        <tbody>
          {q.data.map((r) => (
            <tr key={r.id} className="border-t border-line align-top">
              <td className="px-4 py-2.5">{formatDate(r.created_at, tz)}<div className="text-xs text-muted">{r.trigger === "scheduled" ? "Weekly" : "By hand"}</div></td>
              <td className="px-4 py-2.5"><StatusBadge status={r.status} />{r.error && <div className="mt-1 max-w-xs text-xs text-danger [overflow-wrap:anywhere]">{r.error}</div>}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{pct(r.summary.visibility)}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{pct(r.summary.share_of_voice)}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{r.summary.avg_position ? `#${r.summary.avg_position}` : "—"}</td>
              <td className="px-4 py-2.5 text-right tabular-nums">{r.summary.answers ?? "—"}{r.summary.failed ? <div className="text-xs text-danger">{r.summary.failed} failed</div> : null}</td>
              <td className="px-4 py-2.5 text-xs text-muted">{r.engines.map((e) => e.label).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AiVisibility() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.find((t) => t === params.get("tab")) ?? "overview";
  const setTab = (t: Tab) => setParams(t === "overview" ? {} : { tab: t }, { replace: true });
  const [promptFilter, setPromptFilter] = useState("");
  const canRun = can("aivis.manage");

  const ov = useQuery({
    queryKey: ["aivis", "overview"], queryFn: () => api<Overview>("/aivis/overview"),
    refetchInterval: (q) => (q.state.data?.active_run ? 3000 : 60_000),
  });
  const settings = useQuery({ queryKey: ["aivis", "settings"], queryFn: () => api<Settings>("/aivis/settings"), staleTime: 60_000 });
  const run = useMutation({
    mutationFn: () => api<{ run: Run }>("/aivis/runs", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["aivis", "overview"] }),
  });

  const activeId = ov.data?.active_run?.id;
  const prev = useRef<string | undefined>();
  useEffect(() => {
    if (prev.current && !activeId) qc.invalidateQueries({ queryKey: ["aivis"] });
    prev.current = activeId;
  }, [activeId, qc]);

  const o = ov.data;
  const brandTerms = settings.data?.brand.terms ?? [];
  const next = o?.next_run_at ? new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: o.timezone }).format(new Date(o.next_run_at)) : null;

  return (
    <div>
      <PageHeader title="AI visibility"
        description="See whether ChatGPT, Gemini, Perplexity and other AI assistants mention you when buyers ask about what you sell, and what to do about it."
        action={canRun && (
          <Button onClick={() => run.mutate()} disabled={run.isPending || !!o?.active_run || !o?.prompts.enabled}>
            {o?.active_run ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} {o?.active_run ? "Checking…" : "Run check now"}
          </Button>
        )} />
      {ov.isLoading && <Spinner label="Loading…" />}
      {ov.error && <Alert>{(ov.error as Error).message}</Alert>}
      {run.error && <div className="mb-4"><Alert>{(run.error as Error).message}</Alert></div>}
      {o && (
        <>
          <section className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-line bg-surface px-4 py-3 text-sm">
            <span className="flex items-center gap-1.5 text-muted"><CalendarClock size={15} />
              {o.settings.enabled ? <>Next weekly check {next ?? "—"}</> : "Weekly checks are off"}
            </span>
            <span className="text-muted">{o.prompts.enabled} active prompt{o.prompts.enabled === 1 ? "" : "s"}</span>
            <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-muted">
              Engines{o.engines_auto ? " (automatic)" : ""}:
              {o.engines.map((e) => <span key={e.key} className="inline-flex items-center gap-1 rounded-full bg-ink/5 px-2 py-0.5 text-xs text-ink">{e.label}<CostBadge cost={e.cost} /></span>)}
            </span>
          </section>
          {o.active_run && (
            <div className="mb-5"><Alert tone="info">
              <span className="inline-flex items-center gap-2"><Loader2 size={14} className="animate-spin" />
                {o.active_run.last_step?.message ?? "Starting the check…"}</span>
            </Alert></div>
          )}
          {!o.active_run && o.last_run?.status === "failed" && (
            <div className="mb-5"><Alert>The last check didn't finish: {o.last_run.error}</Alert></div>
          )}
          {!o.has_ai && (
            <div className="mb-5"><Alert tone="info">
              Checks are using free web search only. Add a free AI model in <Link className="underline" to="/settings/integrations">Integrations</Link> (a free Gemini or Groq key, or Ollama on this computer) to also see how AI models answer, and to get AI-suggested prompts.
            </Alert></div>
          )}
          {tab === "overview" && o.paid_hints.length > 0 && o.latest && <div className="mb-5"><PaidHints hints={o.paid_hints} compact /></div>}
          <Tabs value={tab} onChange={setTab} items={[
            { id: "overview", label: "Overview" }, { id: "prompts", label: "Prompts", count: o.prompts.total },
            { id: "competitors", label: "Competitors" }, { id: "recommendations", label: "Recommendations", count: o.latest?.recommendations?.length || undefined },
            { id: "llms", label: "llms.txt" }, { id: "history", label: "History" }, { id: "settings", label: "Settings" },
          ]} />
          {tab === "overview" && <OverviewTab o={o} onOpenPrompt={(t) => { setPromptFilter(t); setTab("prompts"); }} />}
          {tab === "prompts" && <PromptsTab key={promptFilter} brandTerms={brandTerms} initialQuery={promptFilter} />}
          {tab === "competitors" && <CompetitorsTab />}
          {tab === "recommendations" && <RecommendationsTab run={o.latest} />}
          {tab === "llms" && <LlmsTab />}
          {tab === "history" && <HistoryTab tz={o.timezone} />}
          {tab === "settings" && <SettingsTab />}
        </>
      )}
    </div>
  );
}
