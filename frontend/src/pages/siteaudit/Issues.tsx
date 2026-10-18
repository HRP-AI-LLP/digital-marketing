import { useMemo, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { api } from "../../api/client";
import { Button, EmptyState, Input, Select, Spinner, Toggle } from "../../components/ui";
import { Detail, SevBadge, UrlLink } from "./shared";
import { SEV_LABEL, type IssueGroup, type IssueUrls, type RunFull, type Severity } from "./types";

export type IssueFilter = { severity?: Severity | ""; category?: string; check?: string };

export default function IssuesTab({ run, filter, setFilter, openPage }: {
  run: RunFull; filter: IssueFilter; setFilter: (f: IssueFilter) => void; openPage: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [showFixed, setShowFixed] = useState(false);
  const severity = filter.severity ?? "";
  const category = filter.category ?? "";
  const list = run.issues.filter((i) => (showFixed ? true : i.count > 0)
    && (!severity || i.severity === severity) && (!category || i.category === category)
    && (!q.trim() || `${i.title} ${i.why}`.toLowerCase().includes(q.trim().toLowerCase())));
  const groups = useMemo(() => {
    const m = new Map<string, { label: string; items: IssueGroup[] }>();
    for (const i of list) {
      if (!m.has(i.category)) m.set(i.category, { label: i.category_label, items: [] });
      m.get(i.category)!.items.push(i);
    }
    return [...m.values()];
  }, [list]);
  const cats = run.categories;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Severity" className="flex rounded-lg border border-line bg-surface p-0.5">
          {(["", "error", "warning", "notice"] as const).map((s) => (
            <button key={s || "all"} role="tab" aria-selected={severity === s} onClick={() => setFilter({ ...filter, severity: s, check: undefined })}
              className={`rounded-md px-3 py-1 text-sm ${severity === s ? "bg-primary-600 text-white" : "text-muted hover:text-ink"}`}>
              {s ? `${SEV_LABEL[s]} ${s === "error" ? run.errors : s === "warning" ? run.warnings : run.notices}` : "All"}
            </button>
          ))}
        </div>
        <Select aria-label="Area" value={category} onChange={(e) => setFilter({ ...filter, category: e.target.value, check: undefined })} className="w-full sm:w-64">
          <option value="">All areas</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </Select>
        <div className="relative w-full sm:w-64">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <Input aria-label="Search issues" placeholder="Search issues" value={q} onChange={(e) => setQ(e.target.value)} className="w-full pl-9" />
        </div>
        <div className="sm:ml-auto"><Toggle checked={showFixed} onChange={setShowFixed} label="Show fixed" /></div>
      </div>
      {!groups.length ? (
        <EmptyState title="No issues match" body="Try another filter, or celebrate: nothing to fix here." />
      ) : (
        <div className="space-y-6">
          {groups.map((g) => (
            <section key={g.label}>
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted">{g.label}</h3>
              <div className="divide-y divide-line rounded-xl border border-line bg-surface">
                {g.items.map((i) => (
                  <IssueRow key={i.id} runId={run.id} issue={i} initiallyOpen={filter.check === i.id} openPage={openPage} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function IssueRow({ runId, issue, initiallyOpen, openPage }: { runId: string; issue: IssueGroup; initiallyOpen: boolean; openPage: (id: string) => void }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-ink/[0.02]">
        {open ? <ChevronDown size={17} className="mt-0.5 shrink-0 text-muted" /> : <ChevronRight size={17} className="mt-0.5 shrink-0 text-muted" />}
        <SevBadge s={issue.severity} />
        <span className="min-w-0 flex-1">
          <span className="font-medium [overflow-wrap:anywhere]">{issue.title}</span>
          <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
            {issue.site_wide ? <span>Whole site</span> : <span>{issue.count} {issue.count === 1 ? "URL" : "URLs"}</span>}
            {issue.new > 0 && <span className="text-danger">{issue.new} new</span>}
            {issue.fixed > 0 && <span className="text-emerald-700">{issue.fixed} fixed</span>}
          </span>
        </span>
        <span className="text-lg font-semibold tabular-nums">{issue.count}</span>
      </button>
      {open && <IssueBody runId={runId} issue={issue} openPage={openPage} />}
    </div>
  );
}

function IssueBody({ runId, issue, openPage }: { runId: string; issue: IssueGroup; openPage: (id: string) => void }) {
  const [onlyNew, setOnlyNew] = useState(false);
  const q = useInfiniteQuery({
    queryKey: ["siteaudit", "issue", runId, issue.id, onlyNew],
    queryFn: ({ pageParam }) => api<IssueUrls>(`/site-audit/runs/${runId}/issues/${issue.id}?offset=${pageParam}&limit=50${onlyNew ? "&only_new=true" : ""}`),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const n = all.reduce((a, p) => a + p.items.length, 0);
      return n < last.total ? n : undefined;
    },
    enabled: issue.count > 0 || issue.fixed > 0,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const fixed = q.data?.pages[0]?.fixed_urls ?? [];
  return (
    <div className="border-t border-line bg-slate-50/60 px-4 py-4 sm:pl-12">
      <div className="grid gap-3 md:grid-cols-2">
        <div><div className="text-xs font-semibold uppercase tracking-wide text-muted">Why it matters</div><p className="mt-1 text-sm">{issue.why}</p></div>
        <div><div className="text-xs font-semibold uppercase tracking-wide text-muted">How to fix</div><p className="mt-1 text-sm">{issue.fix}</p></div>
      </div>
      {issue.count > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">{issue.site_wide ? "Details" : `Affected URLs (${q.data?.pages[0]?.total ?? issue.count})`}</div>
            {issue.new > 0 && <Toggle checked={onlyNew} onChange={setOnlyNew} label="Only new" />}
          </div>
          {q.isLoading && <Spinner label="Loading…" />}
          <ul className="space-y-2">
            {items.map((it) => (
              <li key={it.id} className="rounded-lg border border-line bg-surface px-3 py-2">
                <div className="flex flex-wrap items-start gap-2">
                  <UrlLink url={it.url} className="min-w-0 flex-1 text-sm" />
                  {it.is_new && <span className="rounded bg-danger-tint px-1.5 py-0.5 text-xs text-danger">New</span>}
                  {it.page_id && <button className="text-xs text-primary-700 hover:underline" onClick={() => openPage(it.page_id!)}>Page details</button>}
                </div>
                <Detail detail={it.detail} />
              </li>
            ))}
          </ul>
          {q.hasNextPage && (
            <Button variant="secondary" className="mt-3" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
              {q.isFetchingNextPage ? "Loading…" : "Show more"}
            </Button>
          )}
        </div>
      )}
      {fixed.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-emerald-700">Fixed since the last audit</div>
          <ul className="space-y-0.5 text-sm">{fixed.slice(0, 20).map((u) => <li key={u}><UrlLink url={u} /></li>)}</ul>
        </div>
      )}
    </div>
  );
}
