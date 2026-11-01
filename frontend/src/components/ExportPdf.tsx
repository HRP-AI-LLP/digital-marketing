import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link, useLocation } from "react-router-dom";
import { FileDown, Loader2, Mail } from "lucide-react";
import { apiFile } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EmailDialog } from "./EmailDialog";

// ------------------------------------------------------------------ which report belongs to which page
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
type PageReport = { kind: string; perm: string; period: boolean; params?: Record<string, string> };
const ROUTES: [RegExp, Omit<PageReport, "params">, string?][] = [
  [/^\/$/, { kind: "dashboard", perm: "content.view", period: true }],
  [new RegExp(`^/posts/(${UUID})$`), { kind: "post", perm: "content.view", period: false }, "id"],
  [/^\/posts\/experiments\/?$/, { kind: "experiments", perm: "content.view", period: true }],
  [/^\/posts\/?$/, { kind: "posts", perm: "content.view", period: true }],
  [/^\/calendar/, { kind: "calendar", perm: "content.view", period: true }],
  [/^\/approvals/, { kind: "approvals", perm: "content.view", period: false }],
  [/^\/media/, { kind: "media", perm: "content.view", period: true }],
  [/^\/link-in-bio/, { kind: "linkinbio", perm: "content.view", period: true }],
  [new RegExp(`^/seo/(${UUID})$`), { kind: "article", perm: "seo.view", period: false }, "id"],
  [/^\/seo/, { kind: "articles", perm: "seo.view", period: true }],
  [/^\/inbox/, { kind: "inbox", perm: "inbox.view", period: true }],
  [/^\/listening/, { kind: "listening", perm: "listening.view", period: true }],
  [/^\/intelligence/, { kind: "intelligence", perm: "intel.view", period: false }],
  [/^\/research/, { kind: "research", perm: "research.view", period: true }],
  [/^\/keywords/, { kind: "keywords", perm: "keywords.view", period: false }],
  [/^\/site-audit/, { kind: "siteaudit", perm: "siteaudit.view", period: false }],
  [/^\/ai-visibility/, { kind: "aivis", perm: "aivis.view", period: false }],
  [/^\/analytics/, { kind: "analytics", perm: "analytics.view", period: true }],
  [/^\/links/, { kind: "links", perm: "links.view", period: true }],
  [/^\/presence/, { kind: "presence", perm: "presence.view", period: false }],
  [/^\/settings\/organization/, { kind: "organization", perm: "org.settings.view", period: false }],
  [/^\/settings\/brand/, { kind: "brand", perm: "content.view", period: false }],
  [/^\/settings\/schedules\/best-times/, { kind: "best_times", perm: "content.view", period: false }],
  [/^\/settings\/schedules/, { kind: "schedules", perm: "schedule.view", period: false }],
  [/^\/settings\/accounts/, { kind: "accounts", perm: "content.view", period: true }],
  [/^\/settings\/integrations/, { kind: "integrations", perm: "integration.view", period: false }],
  [/^\/settings\/connections/, { kind: "connections", perm: "connections.view", period: true }],
  [/^\/settings\/ai-usage/, { kind: "ai_usage", perm: "integration.view", period: true }],
  [/^\/settings\/activity/, { kind: "activity", perm: "audit.view", period: true }],
];

// Pages whose tabs have their own report (?tab=…).
const TAB_REPORTS: Record<string, Record<string, Omit<PageReport, "params">>> = {
  "/analytics": { hashtags: { kind: "hashtags", perm: "analytics.view", period: true } },
};

export function reportForPath(path: string, search = ""): PageReport | null {
  const tab = new URLSearchParams(search).get("tab");
  const byTab = tab ? TAB_REPORTS[path.replace(/\/$/, "")]?.[tab] : undefined;
  if (byTab) return { ...byTab, params: {} };
  for (const [rx, r, idKey] of ROUTES) {
    const m = rx.exec(path);
    if (m) return { ...r, params: idKey ? { [idKey]: m[1] } : {} };
  }
  return null;
}

// ------------------------------------------------------------------ pages can share their filters with the export
let pageParams: Record<string, string> = {};
const listeners = new Set<() => void>();
export function setReportParams(p: Record<string, string>) {
  pageParams = Object.fromEntries(Object.entries(p).filter(([, v]) => v));
  listeners.forEach((l) => l());
}
function usePageParams() {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => pageParams);
}

// ------------------------------------------------------------------ download
export async function downloadReport(kind: string, params: Record<string, string>) {
  const { blob, filename } = await apiFile("/reports/pdf", { kind, params });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

import { PRESETS, presetRange } from "./periods";
export { PRESETS, presetRange };

/** "Export PDF" for the page you're on. Lives in the layout, so every page gets it. */
export default function ExportPdf() {
  const { can } = useAuth();
  const loc = useLocation();
  const shared = usePageParams();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [custom, setCustom] = useState(presetRange("30"));
  const [emailing, setEmailing] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const r = reportForPath(loc.pathname, loc.search);

  useEffect(() => { setReportParams({}); setOpen(false); setErr(null); }, [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (!r || !can(r.perm)) return null;
  const run = async (extra: Record<string, string> = {}) => {
    setBusy(true); setErr(null); setOpen(false);
    try {
      await downloadReport(r.kind, { ...shared, ...r.params, ...extra });
    } catch (e) {
      setErr((e as Error).message || "Couldn't make the PDF.");
    } finally {
      setBusy(false);
    }
  };
  // The page already chose a period (its own date filter): export exactly that.
  const pagePeriod = !!(shared.start || shared.end);

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        aria-expanded={open}
        disabled={busy}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink shadow-sm hover:bg-primary-50 disabled:opacity-60"
        title="Download this page as a PDF report"
      >
        {busy ? <Loader2 size={15} className="animate-spin" /> : <FileDown size={15} />}
        {busy ? "Making PDF…" : "Export PDF"}
      </button>
      {err && <div role="alert" className="absolute right-0 z-30 mt-1 w-72 rounded-lg border border-danger/20 bg-danger-tint px-3 py-2 text-xs text-danger">{err}</div>}
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-xl border border-line bg-surface p-2 text-sm shadow-xl">
          {!(r.period && !pagePeriod) && (
            <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-primary-50"
              onClick={() => run()}><FileDown size={15} />Download PDF</button>
          )}
          {r.period && !pagePeriod && <>
          <div className="px-2 pb-1 pt-0.5 text-xs font-medium text-muted">Download PDF for</div>
          {PRESETS.map((p) => (
            <button key={p.id} type="button" className="block w-full rounded-lg px-2 py-1.5 text-left hover:bg-primary-50"
              onClick={() => run(presetRange(p.id))}>{p.label}</button>
          ))}
          <div className="mt-1 border-t border-line px-2 pt-2">
            <div className="mb-1 text-xs font-medium text-muted">Custom</div>
            <div className="flex items-center gap-1">
              <input type="date" aria-label="From" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })}
                className="h-8 min-w-0 flex-1 rounded border border-line bg-surface px-1 text-xs" />
              <input type="date" aria-label="To" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })}
                className="h-8 min-w-0 flex-1 rounded border border-line bg-surface px-1 text-xs" />
            </div>
            <button type="button" className="mt-2 w-full rounded-lg bg-primary-600 px-2 py-1.5 text-white hover:bg-primary-700"
              onClick={() => run(custom)}>Download</button>
          </div>
          </>}
          <button type="button" className="mt-1 flex w-full items-center gap-2 rounded-lg border-t border-line px-2 py-2 text-left hover:bg-primary-50"
            onClick={() => { setOpen(false); setEmailing(true); }}><Mail size={15} />Email this report…</button>
          <Link to="/reports" className="mt-2 block border-t border-line px-2 pt-2 text-xs text-primary-700 hover:underline"
            onClick={() => setOpen(false)}>All reports…</Link>
        </div>
      )}
      {emailing && (
        <EmailDialog onClose={() => setEmailing(false)} what={{
          type: "report", kind: r.kind, title: REPORT_TITLES[r.kind] ?? "Report", params: { ...shared, ...(r.params ?? {}) },
          period: r.period && !pagePeriod }} />
      )}
    </div>
  );
}

const REPORT_TITLES: Record<string, string> = {
  dashboard: "Overview report", posts: "Posts report", post: "Post details", calendar: "Content calendar", approvals: "Waiting for approval",
  media: "Media library", linkinbio: "Link-in-bio", article: "Article", articles: "SEO articles", inbox: "Inbox report",
  listening: "Listening report", intelligence: "Company intelligence", research: "Research and ideas", keywords: "Keyword rankings",
  siteaudit: "Website health", aivis: "AI visibility", analytics: "Marketing report", links: "Link clicks", presence: "Social presence",
  organization: "Organization profile", brand: "Brand kit and voice", schedules: "Posting schedules", best_times: "Best time to post", accounts: "Social accounts",
  integrations: "Integrations", connections: "Connections (webhooks)", ai_usage: "AI usage and cost", activity: "Activity log",
  hashtags: "Hashtag performance", experiments: "A/B tests",
};
