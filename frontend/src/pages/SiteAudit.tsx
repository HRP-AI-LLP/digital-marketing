import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CalendarClock, Download, Play } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, EmptyState, PageHeader, Spinner, Tabs, formatDate } from "../components/ui";
import CompareTab from "./siteaudit/Compare";
import IssuesTab, { type IssueFilter } from "./siteaudit/Issues";
import OverviewTab from "./siteaudit/Overview";
import PagesTab, { PageModal } from "./siteaudit/Pages";
import SettingsTab from "./siteaudit/Settings";
import { downloadCsv } from "./siteaudit/shared";
import type { Overview, RunBrief, Severity } from "./siteaudit/types";

type Tab = "overview" | "issues" | "pages" | "compare" | "settings";
const TABS: Tab[] = ["overview", "issues", "pages", "compare", "settings"];

export default function SiteAudit() {
  const { can } = useAuth();
  const canManage = can("siteaudit.manage");
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.includes(params.get("tab") as Tab) ? params.get("tab") : "overview") as Tab;
  const setTab = (t: Tab) => setParams((p) => { p.set("tab", t); return p; }, { replace: true });
  const [filter, setFilter] = useState<IssueFilter>({});
  const [pageCheck, setPageCheck] = useState<string | undefined>();
  const [pageId, setPageId] = useState<string | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);

  const ov = useQuery({
    queryKey: ["siteaudit", "overview"], queryFn: () => api<Overview>("/site-audit/overview"),
    refetchInterval: (q) => (q.state.data?.active ? 3000 : 120_000),
  });
  const o = ov.data;
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && o && !o.active) qc.invalidateQueries({ queryKey: ["siteaudit"] });
    wasActive.current = !!o?.active;
  }, [o, qc]);

  const run = useMutation({
    mutationFn: () => api<{ run: RunBrief; already_running: boolean }>("/site-audit/runs", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["siteaudit", "overview"] }),
  });
  const latest = o?.latest ?? null;
  const go = (t: "issues" | "pages", opts: { severity?: Severity; category?: string; check?: string } = {}) => {
    if (t === "issues") setFilter({ severity: opts.severity ?? "", category: opts.category ?? "", check: opts.check });
    else setPageCheck(opts.check);
    setTab(t);
  };
  const dl = (kind: "issues" | "pages") => latest && downloadCsv(latest.id, kind).catch((e) => setDlError((e as Error).message));

  return (
    <div>
      <PageHeader
        title="Site audit"
        description="A deep technical check of your website: broken links, redirects, titles, indexing, structured data, speed, security and AI crawler access, with what to fix first."
        action={
          <div className="flex flex-wrap gap-2">
            {latest && (
              <>
                <Button variant="secondary" onClick={() => dl("issues")}><Download size={15} /> Issues CSV</Button>
                <Button variant="secondary" onClick={() => dl("pages")}><Download size={15} /> Pages CSV</Button>
              </>
            )}
            {canManage && (
              <Button onClick={() => run.mutate()} disabled={run.isPending || !!o?.active || !o?.website}
                title={o?.website ? undefined : "Add your website in Organization settings first"}>
                <Play size={15} /> {o?.active ? "Audit running…" : "Run audit now"}
              </Button>
            )}
          </div>
        }
      />
      <div className="mb-4 space-y-2">
        {run.error && <Alert>{(run.error as Error).message}</Alert>}
        {dlError && <Alert>{dlError}</Alert>}
        {o?.active && <RunProgress r={o.active} />}
        {o?.last_failed && !o.active && <Alert>The last audit didn't finish: {o.last_failed.error}</Alert>}
        {o && (
          <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-muted">
            <CalendarClock size={15} aria-hidden />
            {o.settings.enabled ? <>Next weekly audit {o.next_run_at ? formatDate(o.next_run_at, o.timezone) : "—"}</> : "Weekly audit is off (turn it on in Settings)."}
            {o.website && <span className="[overflow-wrap:anywhere]"> · {o.website}</span>}
          </p>
        )}
      </div>

      <Tabs value={tab} onChange={setTab} items={[
        { id: "overview", label: "Overview" },
        { id: "issues", label: "Issues", count: latest ? latest.errors + latest.warnings + latest.notices : undefined },
        { id: "pages", label: "Pages", count: latest?.pages },
        { id: "compare", label: "Compare audits" },
        { id: "settings", label: "Settings" },
      ]} />

      {ov.isLoading && <Spinner label="Loading…" />}
      {ov.error && <Alert>{(ov.error as Error).message}</Alert>}
      {o && tab === "settings" && <SettingsTab canManage={canManage} />}
      {o && tab === "compare" && <CompareTab tz={o.timezone} />}
      {o && !latest && tab !== "settings" && tab !== "compare" && (
        <EmptyState
          title={o.active ? "Your first audit is running" : "No audit yet"}
          body={!o.website ? "Add your website address in Organization settings (or a start address in the Settings tab), then run the audit."
            : o.active ? "This takes a few minutes for a typical site. You can leave this page; you'll get a notification when it's done."
              : "Run the audit to check every page of your website. It's free and takes a few minutes."}
          action={canManage && o.website && !o.active ? <Button onClick={() => run.mutate()} disabled={run.isPending}><Play size={15} /> Run audit now</Button> : undefined}
        />
      )}
      {o && latest && tab === "overview" && <OverviewTab o={o} run={latest} go={go} />}
      {o && latest && tab === "issues" && <IssuesTab key={filter.check ?? "all"} run={latest} filter={filter} setFilter={setFilter} openPage={setPageId} />}
      {o && latest && tab === "pages" && <PagesTab run={latest} openPage={setPageId} check={pageCheck} clearCheck={() => setPageCheck(undefined)} />}
      {pageId && latest && <PageModal runId={latest.id} pageId={pageId} onClose={() => setPageId(null)} />}
    </div>
  );
}

function RunProgress({ r }: { r: RunBrief }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-brand/20 bg-brand-tint px-3 py-2 text-sm text-brand-deep" role="status">
      <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600" />
      <span className="[overflow-wrap:anywhere]">{r.status === "queued" ? "Audit queued — starting shortly…" : r.progress || "Audit running…"}</span>
    </div>
  );
}
