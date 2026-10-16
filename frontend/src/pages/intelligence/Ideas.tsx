import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lightbulb, Plus, RotateCcw, X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Spinner, StatusBadge, Textarea } from "../../components/ui";
import { ErrorText, JobStatus, ScoreBar, useIntelJobs } from "./shared";
import { SOURCE_LABEL, type Idea, type JobStart, type Overview, type Pillar } from "./types";

type Filters = { status: string; source: string; pillar: string; q: string };

function AddIdea({ pillars, onClose }: { pillars: Pillar[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [angle, setAngle] = useState("");
  const [pillar, setPillar] = useState("");
  const add = useMutation({
    mutationFn: () => api<Idea>("/intel/opportunities", { method: "POST", json: { title, angle: angle || null, pillar_id: pillar || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["intel"] }); onClose(); },
  });
  return (
    <Modal title="Add a topic idea" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        <ErrorText error={add.error} />
        <Field label="Topic"><Input required minLength={3} maxLength={300} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Angle (optional)" hint="The hook or point of view."><Textarea rows={2} value={angle} onChange={(e) => setAngle(e.target.value)} /></Field>
        <Field label="Content pillar (optional)">
          <Select value={pillar} onChange={(e) => setPillar(e.target.value)}>
            <option value="">Any pillar</option>
            {pillars.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={add.isPending || title.trim().length < 3}>Add idea</Button>
        </div>
      </form>
    </Modal>
  );
}

function IdeaRow({ i, pillarName, canManage }: { i: Idea; pillarName?: string; canManage: boolean }) {
  const qc = useQueryClient();
  const patch = useMutation({
    mutationFn: (status: Idea["status"]) => api<Idea>(`/intel/opportunities/${i.id}`, { method: "PATCH", json: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["intel"] }),
  });
  const pct = Math.round(i.score * 100);
  return (
    <tr className="border-t border-line align-top">
      <td className="py-3 pr-4">
        <div className="font-medium">{i.title}</div>
        {i.angle && <div className="text-sm text-muted">{i.angle}</div>}
        {(i.keywords.length > 0 || i.formats.length > 0) && (
          <div className="mt-1 flex flex-wrap gap-1 text-xs text-muted">
            {i.keywords.map((k) => <span key={k} className="rounded bg-ink/5 px-1.5 py-0.5">{k}</span>)}
            {i.formats.length > 0 && <span className="px-1">· {i.formats.join(", ")}</span>}
          </div>
        )}
        {patch.error && <p className="text-sm text-danger">{(patch.error as Error).message}</p>}
      </td>
      <td className="w-28 py-3 pr-4">
        <div className="text-sm tabular-nums">{pct}%</div>
        <ScoreBar value={pct} label={`Score for ${i.title}`} />
      </td>
      <td className="py-3 pr-4"><Badge tone={i.source === "gap" ? "accent" : i.source === "gsc" ? "brand" : "neutral"}>{SOURCE_LABEL[i.source] ?? i.source}</Badge></td>
      <td className="py-3 pr-4 text-sm">{pillarName ?? <span className="text-muted">Any</span>}</td>
      <td className="py-3 pr-4"><StatusBadge status={i.status === "new" ? "ready" : i.status} /></td>
      <td className="py-3 text-right">
        {canManage && i.status !== "dismissed" && (
          <Button variant="ghost" className="h-8 px-2" onClick={() => patch.mutate("dismissed")} disabled={patch.isPending} aria-label={`Dismiss ${i.title}`}>
            <X size={14} /> Dismiss
          </Button>
        )}
        {canManage && i.status !== "new" && (
          <Button variant="ghost" className="h-8 px-2" onClick={() => patch.mutate("new")} disabled={patch.isPending} aria-label={`Restore ${i.title}`}>
            <RotateCcw size={14} /> Restore
          </Button>
        )}
      </td>
    </tr>
  );
}

export default function IdeasTab({ overview }: { overview: Overview }) {
  const { can } = useAuth();
  const canManage = can("intel.manage");
  const { track } = useIntelJobs();
  const [filters, setFilters] = useState<Filters>({ status: "new", source: "", pillar: "", q: "" });
  const [adding, setAdding] = useState(false);
  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (filters.status) p.set("status", filters.status);
    if (filters.source) p.set("source", filters.source);
    if (filters.pillar) p.set("pillar_id", filters.pillar);
    if (filters.q.trim()) p.set("q", filters.q.trim());
    return p.toString();
  }, [filters]);
  const ideas = useQuery({ queryKey: ["intel", "opportunities", params], queryFn: () => api<Idea[]>(`/intel/opportunities?${params}`) });
  const pillars = useQuery({ queryKey: ["intel", "pillars"], queryFn: () => api<Pillar[]>("/intel/pillars") });
  const pillarNames = useMemo(() => new Map((pillars.data ?? []).map((p) => [p.id, p.name])), [pillars.data]);
  const generate = useMutation({
    mutationFn: () => api<JobStart>("/intel/opportunities/generate", { method: "POST" }),
    onSuccess: (r) => track("ideas", r.job_id),
  });
  const set = (k: keyof Filters) => (v: string) => setFilters((f) => ({ ...f, [k]: v }));
  const hasProfile = !!overview.profile;
  const filtered = filters.source || filters.pillar || filters.q || filters.status !== "new";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          Ranked by relevance, search demand and gaps against competitors. The content planner takes the best unused idea for each post.
        </p>
        {canManage && (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setAdding(true)}><Plus size={15} /> Add idea</Button>
            <Button onClick={() => generate.mutate()} disabled={generate.isPending || !hasProfile} title={hasProfile ? undefined : "Analyze your website first"}>
              <Lightbulb size={15} /> Generate ideas
            </Button>
          </div>
        )}
      </div>
      <div className="space-y-2">
        <ErrorText error={generate.error} />
        <JobStatus jobKey="ideas" label="Generating topic ideas" doneText={(j) => `Added ${Number(j.result?.created ?? 0)} new ideas.`} />
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Status">
          <Select value={filters.status} onChange={(e) => set("status")(e.target.value)}>
            <option value="new">Ready to use</option><option value="used">Used</option><option value="dismissed">Dismissed</option><option value="">All</option>
          </Select>
        </Field>
        <Field label="Source">
          <Select value={filters.source} onChange={(e) => set("source")(e.target.value)}>
            <option value="">All sources</option>
            {Object.entries(SOURCE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Pillar">
          <Select value={filters.pillar} onChange={(e) => set("pillar")(e.target.value)}>
            <option value="">All pillars</option>
            {(pillars.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label="Search"><Input value={filters.q} onChange={(e) => set("q")(e.target.value)} placeholder="Words in the topic" /></Field>
      </div>

      {ideas.isLoading && <Spinner label="Loading…" />}
      <ErrorText error={ideas.error} />
      {ideas.data && !ideas.data.length && (
        <EmptyState
          title={filtered ? "No ideas match these filters" : "No topic ideas yet"}
          body={filtered ? "Change the filters to see more."
            : hasProfile ? "Select “Generate ideas” to get 20–40 topics based on your profile, competitors and search data."
              : "Analyze your website first; ideas are built from your company profile."}
        />
      )}
      {!!ideas.data?.length && (
        <Card className="overflow-x-auto py-1">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-2 font-medium">Topic</th><th className="font-medium">Score</th><th className="font-medium">Source</th>
                <th className="font-medium">Pillar</th><th className="font-medium">Status</th><th className="sr-only">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ideas.data.map((i) => (
                <IdeaRow key={i.id} i={i} pillarName={i.pillar_id ? pillarNames.get(i.pillar_id) : undefined} canManage={canManage} />
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {adding && <AddIdea pillars={pillars.data ?? []} onClose={() => setAdding(false)} />}
    </div>
  );
}
