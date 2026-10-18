import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, PageHeader, Spinner, Tabs } from "../../components/ui";
import { ago } from "../inbox/shared";
import Feed, { EMPTY_FILTERS, type Filters } from "./Feed";
import Overview from "./Overview";
import Topics from "./Topics";
import type { ListenStatus, Query } from "./types";

type Tab = "feed" | "overview" | "topics";
const TABS: Tab[] = ["feed", "overview", "topics"];

export default function Listening() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.includes(params.get("tab") as Tab) ? params.get("tab") : "feed") as Tab;
  const setTab = (t: Tab) => { const p = new URLSearchParams(params); p.set("tab", t); setParams(p, { replace: true }); };
  const [filters, setFilters] = useState<Filters>(() => ({
    ...EMPTY_FILTERS,
    sentiment: ["positive", "neutral", "negative"].includes(params.get("sentiment") ?? "") ? params.get("sentiment")! : "",
    needs_response: params.get("needs_response") === "true",
  }));
  const status = useQuery({ queryKey: ["listening-status"], queryFn: () => api<ListenStatus>("/listening/status"), refetchInterval: 60_000 });
  const queries = useQuery({ queryKey: ["listening-queries"], queryFn: () => api<Query[]>("/listening/queries") });
  const [jobId, setJobId] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (qid?: string) => api<{ job_id: string }>("/listening/run", { method: "POST", json: { query_id: qid ?? null } }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const job = useJob(jobId, () => {
    setJobId(null);
    for (const k of ["listening-status", "listening-queries", "listening-mentions", "listening-overview"]) qc.invalidateQueries({ queryKey: [k] });
  });
  const running = run.isPending || (!!jobId && job?.status !== "failed" && job?.status !== "done");
  const s = status.data;
  const result = job?.status === "done" ? (job.result as { new?: number } | null) : null;

  return (
    <div>
      <PageHeader
        title="Listening"
        description="What people say about you, your competitors and your industry on news sites, blogs, Reddit, Hacker News, Bluesky, Mastodon and more."
        action={can("listening.manage") && s && s.queries > 0 ? (
          <Button onClick={() => run.mutate(undefined)} disabled={running}>
            <RefreshCw size={16} className={running ? "animate-spin" : ""} /> {running ? job?.progress ?? "Searching…" : "Run now"}
          </Button>
        ) : undefined}
      />
      {s && (
        <p className="-mt-3 mb-4 text-sm text-muted">
          {s.last_run_at ? <>Last searched {ago(s.last_run_at)}{s.last_found != null ? `, ${s.last_found} new` : ""}. </> : s.queries ? "Not searched yet. " : ""}
          {s.settings.enabled && s.next_run_at ? <>Next search {ago(s.next_run_at).replace(" ago", "") === "just now" ? "soon" : `around ${new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit" }).format(new Date(s.next_run_at))}`}. </> : null}
          {s.needs_response > 0 && <button className="font-medium text-amber-800 hover:underline" onClick={() => { setFilters({ ...EMPTY_FILTERS, needs_response: true }); setTab("feed"); }}>{s.needs_response} waiting for a reply.</button>}
        </p>
      )}
      {run.error && <div className="mb-4"><Alert>{(run.error as Error).message}</Alert></div>}
      {job?.status === "failed" && <div className="mb-4"><Alert>{job.error ?? "The search didn't finish. Try again in a few minutes."}</Alert></div>}
      {result && <div className="mb-4"><Alert tone="info">Search finished: {result.new ?? 0} new mention{result.new === 1 ? "" : "s"}.</Alert></div>}
      <Tabs value={tab} onChange={setTab} items={[
        { id: "feed", label: "Mentions", count: s?.new },
        { id: "overview", label: "Overview" },
        { id: "topics", label: "Topics & settings", count: s?.queries },
      ]} />
      {!s || queries.isLoading ? <Spinner label="Loading…" /> : status.error ? <Alert>{(status.error as Error).message}</Alert> : (
        <>
          {tab === "feed" && (s.queries === 0 ? (
            <Alert tone="info">
              Start by choosing what to listen for. <button className="font-medium underline" onClick={() => setTab("topics")}>Add topics</button> — we suggest a starter set from your company profile and competitors.
            </Alert>
          ) : <Feed queries={queries.data ?? []} sources={s.sources} filters={filters} setFilters={setFilters} />)}
          {tab === "overview" && <Overview queries={queries.data ?? []} />}
          {tab === "topics" && <Topics status={s} queries={queries.data ?? []} loading={queries.isLoading} onRun={(qid) => run.mutate(qid)} />}
        </>
      )}
    </div>
  );
}
