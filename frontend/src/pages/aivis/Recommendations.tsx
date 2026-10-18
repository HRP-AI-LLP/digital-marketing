import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Spinner } from "../../components/ui";
import type { Run } from "./types";

const TYPE: Record<string, string> = { content: "Content", listing: "Get listed", comparison: "Comparison page", technical: "Technical", reputation: "Reputation" };

export default function RecommendationsTab({ run }: { run: Run | null }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const refresh = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/aivis/runs/${run?.id}/recommendations`, { method: "POST" }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: ["aivis"] }));
  const busy = !!jobId && job?.status !== "done" && job?.status !== "failed";
  if (!run) return <EmptyState title="No recommendations yet" body="They're written from the gaps found in each check." />;
  const recs = run.recommendations ?? [];
  const order = { high: 0, medium: 1, low: 2 };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">Based on the latest check: prompts you missed, competitors that won, and the sites AI engines cite.</p>
        {can("aivis.manage") && <Button variant="secondary" onClick={() => refresh.mutate()} disabled={busy}><RefreshCw size={15} /> {busy ? "Writing…" : "Rewrite recommendations"}</Button>}
      </div>
      {busy && <Spinner label={job?.progress || "Writing recommendations…"} />}
      {job?.status === "failed" && <Alert>{job.error}</Alert>}
      {refresh.error && <Alert>{(refresh.error as Error).message}</Alert>}
      {!recs.length && <EmptyState title="Nothing to fix right now" body="AI engines already name you where it matters. Keep checking weekly." />}
      <div className="grid gap-4 md:grid-cols-2">
        {[...recs].sort((a, b) => order[a.priority] - order[b.priority]).map((r, i) => (
          <Card key={i}>
            <div className="mb-1.5 flex flex-wrap gap-1.5">
              <Badge tone={r.priority === "high" ? "danger" : r.priority === "medium" ? "accent" : "neutral"}>{r.priority} priority</Badge>
              <Badge>{TYPE[r.type] ?? r.type}</Badge>
              {r.source === "rules" && <Badge>From your data</Badge>}
            </div>
            <h3 className="font-semibold [overflow-wrap:anywhere]">{r.title}</h3>
            {r.why && <p className="mt-1 text-sm text-muted [overflow-wrap:anywhere]">{r.why}</p>}
            {!!r.actions.length && <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{r.actions.map((a) => <li key={a} className="[overflow-wrap:anywhere]">{a}</li>)}</ul>}
            {!!r.prompts.length && <p className="mt-2 text-xs text-muted [overflow-wrap:anywhere]">Prompts: {r.prompts.join(" · ")}</p>}
            {!!r.domains.length && <p className="mt-1 text-xs text-muted [overflow-wrap:anywhere]">Sites: {r.domains.join(", ")}</p>}
          </Card>
        ))}
      </div>
    </div>
  );
}
