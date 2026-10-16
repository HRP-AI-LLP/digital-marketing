import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Plus, RefreshCw, Search, Undo2, X } from "lucide-react";
import { api } from "../../api/client";
import type { Job } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Spinner, formatDate } from "../../components/ui";
import { Chips, DeleteButton, ErrorText, JobStatus, ScoreBar, SectionTitle, SetupHint, useIntelJobs } from "./shared";
import { type Competitor, type JobStart, type Overview } from "./types";

type PatchResult = { competitor: Competitor; job_id: string | null };

function useCompetitorActions(c: Competitor) {
  const qc = useQueryClient();
  const { track } = useIntelJobs();
  const refresh = () => qc.invalidateQueries({ queryKey: ["intel"] });
  const setStatus = useMutation({
    mutationFn: (status: Competitor["status"]) => api<PatchResult>(`/intel/competitors/${c.id}`, { method: "PATCH", json: { status } }),
    onSuccess: (r) => { if (r.job_id) track(`competitor:${c.id}`, r.job_id); refresh(); },
  });
  const crawl = useMutation({
    mutationFn: () => api<JobStart>(`/intel/competitors/${c.id}/crawl`, { method: "POST" }),
    onSuccess: (r) => track(`competitor:${c.id}`, r.job_id),
  });
  const remove = useMutation({ mutationFn: () => api(`/intel/competitors/${c.id}`, { method: "DELETE" }), onSuccess: refresh });
  const error = setStatus.error || crawl.error || remove.error;
  return { setStatus, crawl, remove, error };
}

function SiteLink({ c }: { c: Competitor }) {
  return (
    <a href={c.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-primary-700 hover:underline">
      {c.domain} <ExternalLink size={12} />
    </a>
  );
}

function SuggestedCard({ c, canManage }: { c: Competitor; canManage: boolean }) {
  const a = useCompetitorActions(c);
  const pct = Math.round(c.overlap_score * 100);
  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-semibold">{c.name}</h3>
          <SiteLink c={c} />
        </div>
        <span className="text-sm font-semibold tabular-nums text-brand-deep" title="How much they overlap with your offerings">{pct}% overlap</span>
      </div>
      <div className="mt-2"><ScoreBar value={pct} label={`${c.name} overlap`} /></div>
      {c.reason && <p className="mt-3 text-sm">{c.reason}</p>}
      {!!c.offerings.length && <div className="mt-3"><Chips items={c.offerings} max={6} /></div>}
      {a.error && <p className="mt-2 text-sm text-danger">{(a.error as Error).message}</p>}
      {canManage && (
        <div className="mt-auto flex gap-2 pt-4">
          <Button className="h-8 px-3" onClick={() => a.setStatus.mutate("confirmed")} disabled={a.setStatus.isPending}><Check size={14} /> Confirm</Button>
          <Button variant="secondary" className="h-8 px-3" onClick={() => a.setStatus.mutate("rejected")} disabled={a.setStatus.isPending}><X size={14} /> Not a competitor</Button>
        </div>
      )}
    </Card>
  );
}

function Facts({ label, items, empty }: { label: string; items: string[]; empty?: string }) {
  if (!items.length && !empty) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-muted">{label}</div>
      <Chips items={items} max={10} empty={empty} />
    </div>
  );
}

function crawlDone(j: Job) {
  return j.result?.changed ? "Crawl finished. Something changed since last time; see “Recent changes”." : "Crawl finished. Their offerings and content themes are up to date.";
}

function ConfirmedCard({ c, canManage }: { c: Competitor; canManage: boolean }) {
  const a = useCompetitorActions(c);
  const analysed = c.offerings.length || c.content_themes.length || c.positioning;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-lg font-semibold">{c.name}</h3>
          <div className="flex flex-wrap items-center gap-2">
            <SiteLink c={c} />
            <span className="text-xs text-muted">
              {c.last_crawled_at ? `Read ${formatDate(c.last_crawled_at)}${c.page_count ? ` · ${c.page_count} pages` : ""}` : "Not read yet"}
            </span>
          </div>
        </div>
        {canManage && (
          <div className="flex items-center gap-1">
            <Button variant="secondary" className="h-8 px-3" onClick={() => a.crawl.mutate()} disabled={a.crawl.isPending}><RefreshCw size={14} /> Crawl now</Button>
            <DeleteButton label={`Remove ${c.name}`} onConfirm={() => a.remove.mutate()} busy={a.remove.isPending} />
          </div>
        )}
      </div>
      <div className="mt-3"><JobStatus jobKey={`competitor:${c.id}`} label={`Reading ${c.name}`} doneText={crawlDone} /></div>
      {a.error && <p className="mt-2 text-sm text-danger">{(a.error as Error).message}</p>}
      {c.positioning && <p className="mt-3">{c.positioning}</p>}
      {analysed ? (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Facts label="Offerings" items={c.offerings} />
          <Facts label="Pricing signals" items={c.pricing_signals} empty="None published" />
          <Facts label="Content themes" items={c.content_themes} />
          <Facts label="Blog topics" items={c.blog_topics} />
          <Facts label="Keywords they target" items={c.keywords.length ? c.keywords : c.title_keywords} />
          <Facts label="Who they sell to" items={[...c.audiences, ...c.industries]} />
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted">Their website hasn't been analysed yet. Select “Crawl now”.</p>
      )}
      {!!c.changes.length && (
        <div className="mt-4 rounded-lg border border-secondary-300 bg-accent-tint px-3 py-2.5 text-sm">
          <div className="font-medium">Recent changes</div>
          <ul className="mt-1 space-y-1">
            {c.changes.slice(0, 3).map((ch) => (
              <li key={ch.at}>
                <span className="text-muted">{formatDate(ch.at)}: </span>
                {ch.new_offerings.length > 0 && <>New offerings: {ch.new_offerings.join(", ")}. </>}
                {ch.new_pages.length > 0 && <>{ch.new_pages.length} new page{ch.new_pages.length > 1 ? "s" : ""}, e.g. <a className="text-primary-700 hover:underline" href={ch.new_pages[0]} target="_blank" rel="noreferrer">{ch.new_pages[0]}</a></>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function RejectedRow({ c, canManage }: { c: Competitor; canManage: boolean }) {
  const a = useCompetitorActions(c);
  return (
    <li className="flex items-center justify-between gap-2 py-2">
      <span className="min-w-0 truncate"><span className="font-medium">{c.name}</span> <span className="text-sm text-muted">{c.domain}</span></span>
      {canManage && (
        <Button variant="ghost" className="h-8 px-2" onClick={() => a.setStatus.mutate("suggested")} disabled={a.setStatus.isPending}>
          <Undo2 size={14} /> Move back to suggestions
        </Button>
      )}
    </li>
  );
}

function AddCompetitor({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { track } = useIntelJobs();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: () => api<{ competitor: Competitor; job_id: string }>("/intel/competitors", { method: "POST", json: { url, name: name || null } }),
    onSuccess: (r) => { track(`competitor:${r.competitor.id}`, r.job_id); qc.invalidateQueries({ queryKey: ["intel"] }); onClose(); },
  });
  return (
    <Modal title="Add a competitor" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        <ErrorText error={add.error} />
        <Field label="Website"><Input required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://competitor.com" /></Field>
        <Field label="Name (optional)" hint="Taken from the website address when empty."><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <p className="text-sm text-muted">Their public website is read right away (up to 40 pages), politely and within robots.txt.</p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={add.isPending || !url.trim()}>Add and read their website</Button>
        </div>
      </form>
    </Modal>
  );
}

export default function CompetitorsTab({ overview }: { overview: Overview }) {
  const { can } = useAuth();
  const canManage = can("intel.manage");
  const { track } = useIntelJobs();
  const [adding, setAdding] = useState(false);
  const [showRejected, setShowRejected] = useState(false);
  const list = useQuery({ queryKey: ["intel", "competitors"], queryFn: () => api<Competitor[]>("/intel/competitors") });
  const discover = useMutation({
    mutationFn: () => api<JobStart>("/intel/competitors/discover", { method: "POST" }),
    onSuccess: (r) => track("discover", r.job_id),
  });
  const all = list.data ?? [];
  const suggested = all.filter((c) => c.status === "suggested");
  const confirmed = all.filter((c) => c.status === "confirmed");
  const rejected = all.filter((c) => c.status === "rejected");
  const hasProfile = !!overview.profile;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          Competitors are found by searching the web for your offerings, industries and region
          ({overview.search_provider === "configured" ? "using your search integration" : "using DuckDuckGo; add another search provider in Integrations for better results"}).
          Confirmed competitors are re-read every week and you're told when something changes.
        </p>
        {canManage && (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setAdding(true)}><Plus size={15} /> Add competitor</Button>
            <Button onClick={() => discover.mutate()} disabled={discover.isPending || !hasProfile} title={hasProfile ? undefined : "Analyze your website first"}>
              <Search size={15} /> Find competitors
            </Button>
          </div>
        )}
      </div>
      <div className="space-y-2">
        <ErrorText error={discover.error} />
        <JobStatus jobKey="discover" label="Finding competitors"
          doneText={(j) => `Search finished. ${Number(j.result?.suggested ?? 0)} new competitors suggested.`} />
        {!hasProfile && <SetupHint aiReady={overview.ai_ready} hasWebsite={!!(overview.website_source || overview.organization_website)} />}
      </div>
      {list.isLoading && <Spinner label="Loading…" />}
      <ErrorText error={list.error} />

      {list.data && (
        <>
          <section>
            <SectionTitle title="Suggested" description="Confirm the ones that really compete with you. Rejected suggestions won't come back." />
            {suggested.length ? (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{suggested.map((c) => <SuggestedCard key={c.id} c={c} canManage={canManage} />)}</div>
            ) : (
              <EmptyState
                title="No suggestions waiting"
                body={hasProfile ? "Select “Find competitors” to search the web, or add one you already know." : "Analyze your website first so competitors can be compared with your offerings."}
              />
            )}
          </section>

          <section>
            <SectionTitle title="Confirmed competitors" description="What they sell, how they position themselves and what they write about." />
            {confirmed.length ? (
              <div className="space-y-4">{confirmed.map((c) => <ConfirmedCard key={c.id} c={c} canManage={canManage} />)}</div>
            ) : (
              <EmptyState title="No confirmed competitors yet" body="Confirm a suggestion or add a competitor you know. Their content themes feed your topic ideas." />
            )}
          </section>

          {rejected.length > 0 && (
            <section>
              <button className="text-sm text-muted hover:text-ink" onClick={() => setShowRejected((v) => !v)}>
                {showRejected ? "Hide" : "Show"} {rejected.length} rejected suggestion{rejected.length > 1 ? "s" : ""}
              </button>
              {showRejected && (
                <Card className="mt-2 py-2"><ul className="divide-y divide-line">{rejected.map((c) => <RejectedRow key={c.id} c={c} canManage={canManage} />)}</ul></Card>
              )}
            </section>
          )}
        </>
      )}
      {adding && <AddCompetitor onClose={() => setAdding(false)} />}
      {suggested.some((c) => c.origin === "auto") && <Badge>Overlap scores are AI estimates</Badge>}
    </div>
  );
}
