// Activity log: a sortable, filterable grid of everything that happened in the organization. Every row has a
// "Details" button (double-click or Enter does the same) that opens the full entry, including the stored details.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Copy, Download, ExternalLink, Eye,
  Filter, RefreshCw, Search, X,
} from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Modal, PageHeader } from "../components/ui";

type Entry = {
  id: string; actor_email: string | null; actor_user_id: string | null; action: string; entity_type: string | null; entity_id: string | null;
  summary: string | null; ip_address: string | null; created_at: string; details: Record<string, unknown> | null;
};
type Facets = {
  categories: { key: string; count: number }[]; actions: { key: string; count: number }[];
  actors: { key: string; email: string | null; count: number }[]; entity_types: { key: string; type: string | null; count: number }[];
  first_at: string | null; last_at: string | null;
};
type SortKey = "created_at" | "actor_email" | "action" | "summary" | "entity_type" | "ip_address";

const CATEGORY: Record<string, string> = {
  auth: "Sign-in & security", user: "Users", role: "Roles", org: "Organization", post: "Posts", schedule: "Schedules",
  pillar: "Content pillars", campaign: "Campaigns", media: "Media", integration: "Integrations", social: "Social accounts",
  webhook: "Webhooks", api_token: "API tokens", analytics: "Analytics", report: "Reports", intel: "Company intelligence",
  research: "Research", keywords: "Keywords", seo: "SEO", siteaudit: "Site audit", aivis: "AI visibility", listening: "Listening",
  inbox: "Inbox", saved_reply: "Saved replies", links: "Links", experiment: "Experiments", brand: "Brand", platform: "Platform",
  presence: "Presence", evergreen: "Evergreen", canva: "Canva", file: "Files",
};
const TONE: Record<string, string> = {
  auth: "bg-violet-50 text-violet-700 ring-violet-200", user: "bg-sky-50 text-sky-700 ring-sky-200", role: "bg-sky-50 text-sky-700 ring-sky-200",
  org: "bg-slate-100 text-slate-700 ring-slate-200", post: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  schedule: "bg-teal-50 text-teal-700 ring-teal-200", integration: "bg-amber-50 text-amber-800 ring-amber-200",
  social: "bg-amber-50 text-amber-800 ring-amber-200", platform: "bg-rose-50 text-rose-700 ring-rose-200",
};
const catOf = (action: string) => action.split(".")[0];
const catLabel = (c: string) => CATEGORY[c] ?? c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
const humanize = (s: string) => s.replace(/[._]/g, " ").replace(/\s+/g, " ").trim().replace(/^\w/, (m) => m.toUpperCase());
const typeLabel = (t: string | null) => (t ? humanize(t) : "—");
const ENTITY_LINK: Record<string, (id: string) => string> = {
  post: (id) => `/posts/${id}`, schedule: () => "/settings/schedules", integration: () => "/settings/integrations",
  organization: () => "/settings/organization", user: () => "/settings/users", role: () => "/settings/roles",
  social_account: () => "/settings/accounts", media_asset: () => "/media", report: () => "/reports", tracked_keyword: () => "/keywords",
  competitor: () => "/intelligence", knowledge_source: () => "/intelligence", idea_brief: () => "/research", saved_reply: () => "/inbox",
};
const PAGE_SIZES = [25, 50, 100, 200];

function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

function fmt(iso: string, tz?: string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "medium" }) {
  try { return new Intl.DateTimeFormat("en-IN", { ...opts, timeZone: tz }).format(new Date(iso)); } catch { return iso; }
}
function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60); if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60); if (h < 48) return `${h} h ago`;
  const d = Math.round(h / 24); if (d < 60) return `${d} days ago`;
  return `${Math.round(d / 30)} months ago`;
}
function ymd(d: Date) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }

export default function Activity() {
  const { session } = useAuth();
  const tz = session?.organization?.timezone;
  const [sp, setSp] = useSearchParams();
  const f = {
    q: sp.get("q") ?? "", cat: sp.get("cat") ?? "", action: sp.get("action") ?? "", actor: sp.get("actor") ?? "",
    type: sp.get("type") ?? "", start: sp.get("start") ?? "", end: sp.get("end") ?? "",
    sort: (sp.get("sort") as SortKey) || "created_at", dir: (sp.get("dir") as "asc" | "desc") || "desc",
    size: Number(sp.get("size")) || 50, page: Math.max(0, Number(sp.get("page")) || 0),
  };
  const set = (patch: Partial<Record<keyof typeof f, string | number>>, keepPage = false) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => { if (v === "" || v == null) next.delete(k); else next.set(k, String(v)); });
    if (!keepPage) next.delete("page");
    setSp(next, { replace: true });
  };
  const [search, setSearch] = useState(f.q);
  const dq = useDebounced(search);
  useEffect(() => { if (dq !== f.q) set({ q: dq }); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [dq]);

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(f.size), offset: String(f.page * f.size), sort: f.sort, dir: f.dir });
    if (f.q) p.set("q", f.q);
    if (f.action || f.cat) p.set("action", f.action || `${f.cat}.`);
    if (f.actor) p.set("actor", f.actor);
    if (f.type) p.set("entity_type", f.type);
    if (f.start) p.set("start", f.start);
    if (f.end) p.set("end", f.end);
    return p;
  }, [f.size, f.page, f.sort, f.dir, f.q, f.action, f.cat, f.actor, f.type, f.start, f.end]);

  const q = useQuery({ queryKey: ["audit", params.toString()], queryFn: () => api<{ items: Entry[]; total: number }>(`/audit?${params}`), placeholderData: keepPreviousData });
  const facets = useQuery({ queryKey: ["audit-facets"], queryFn: () => api<Facets>("/audit/facets"), staleTime: 60_000 });
  const rows = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / f.size));
  const [open, setOpen] = useState<number | null>(null);
  const [focusIdx, setFocusIdx] = useState(0);
  const body = useRef<HTMLTableSectionElement>(null);

  const actionsInCat = (facets.data?.actions ?? []).filter((a) => !f.cat || catOf(a.key) === f.cat);
  const active: { key: keyof typeof f; label: string }[] = [
    f.q && { key: "q" as const, label: `Search: “${f.q}”` },
    f.cat && { key: "cat" as const, label: `Category: ${catLabel(f.cat)}` },
    f.action && { key: "action" as const, label: `Action: ${f.action}` },
    f.actor && { key: "actor" as const, label: `User: ${f.actor === "__system__" ? "System" : f.actor}` },
    f.type && { key: "type" as const, label: `Item: ${f.type === "__none__" ? "None" : typeLabel(f.type)}` },
    (f.start || f.end) && { key: "start" as const, label: `Dates: ${f.start || "…"} → ${f.end || "…"}` },
  ].filter(Boolean) as { key: keyof typeof f; label: string }[];
  const clearOne = (k: keyof typeof f) => { if (k === "q") setSearch(""); set(k === "start" ? { start: "", end: "" } : k === "cat" ? { cat: "", action: "" } : { [k]: "" }); };
  const clearAll = () => { setSearch(""); set({ q: "", cat: "", action: "", actor: "", type: "", start: "", end: "" }); };
  const preset = (days: number) => { const e = new Date(); const s = new Date(); s.setDate(s.getDate() - (days - 1)); set({ start: ymd(s), end: ymd(e) }); };

  const sortBy = (k: SortKey) => set({ sort: k, dir: f.sort === k ? (f.dir === "asc" ? "desc" : "asc") : k === "created_at" ? "desc" : "asc" }, false);
  const go = (p: number) => set({ page: Math.min(pages - 1, Math.max(0, p)) || "" }, true);

  const [exporting, setExporting] = useState(false);
  const exportCsv = async () => {
    setExporting(true);
    try {
      const all: Entry[] = [];
      for (let off = 0; off < Math.min(total, 10000); off += 500) {
        const p = new URLSearchParams(params); p.set("limit", "500"); p.set("offset", String(off));
        all.push(...(await api<{ items: Entry[] }>(`/audit?${p}`)).items);
      }
      const esc = (v: unknown) => { const s = v == null ? "" : typeof v === "string" ? v : JSON.stringify(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
      const head = ["Time", "User", "Category", "Action", "Summary", "Item type", "Item ID", "IP address", "Details"];
      const lines = all.map((e) => [fmt(e.created_at, tz), e.actor_email ?? "System", catLabel(catOf(e.action)), e.action, e.summary ?? "", e.entity_type ?? "", e.entity_id ?? "", e.ip_address ?? "", e.details ?? ""].map(esc).join(","));
      const blob = new Blob(["﻿" + [head.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `activity-log-${ymd(new Date())}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } finally { setExporting(false); }
  };

  const onKeyRow = (e: React.KeyboardEvent, i: number) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(i); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)));
      setFocusIdx(n);
      body.current?.querySelectorAll<HTMLTableRowElement>("tr[data-row]")[n]?.focus();
    }
  };

  const Th = ({ k, children, className = "" }: { k?: SortKey; children: ReactNode; className?: string }) => (
    <th scope="col" aria-sort={k && f.sort === k ? (f.dir === "asc" ? "ascending" : "descending") : undefined}
      className={`sticky top-0 z-10 whitespace-nowrap border-b border-line bg-slate-50/95 px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted backdrop-blur ${className}`}>
      {k ? (
        <button type="button" onClick={() => sortBy(k)} className={`group inline-flex items-center gap-1 rounded outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary-500/40 ${f.sort === k ? "text-ink" : ""}`}
          title={`Sort by ${String(children).toLowerCase()}`}>
          {children}
          {f.sort === k ? (f.dir === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ArrowUpDown size={12} className="opacity-0 group-hover:opacity-60" />}
        </button>
      ) : children}
    </th>
  );
  const sel = "h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-ink outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-500/15";

  return (
    <div>
      <PageHeader title="Activity log" description="Who did what in this organization. Entries can't be edited or removed."
        action={<div className="flex gap-2">
          <Button variant="secondary" onClick={() => { void q.refetch(); void facets.refetch(); }} title="Refresh"><span className="inline-flex items-center gap-1.5"><RefreshCw size={14} className={q.isFetching ? "animate-spin" : ""} />Refresh</span></Button>
          <Button variant="secondary" onClick={exportCsv} disabled={!total || exporting} title="Download every entry that matches the filters (up to 10,000)">
            <span className="inline-flex items-center gap-1.5"><Download size={14} />{exporting ? "Preparing…" : "Export CSV"}</span>
          </Button>
        </div>} />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}

      {/* filters */}
      <section className="mb-3 rounded-xl border border-line bg-surface p-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-[16rem] flex-1">
            <Search size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search summary, action, user, item or IP…" aria-label="Search the activity log"
              className={`${sel} w-full pl-8`} />
          </label>
          <select aria-label="Category" value={f.cat} onChange={(e) => set({ cat: e.target.value, action: "" })} className={sel}>
            <option value="">All categories</option>
            {facets.data?.categories.map((c) => <option key={c.key} value={c.key}>{catLabel(c.key)} ({c.count})</option>)}
          </select>
          <select aria-label="Action" value={f.action} onChange={(e) => set({ action: e.target.value })} className={`${sel} max-w-[14rem]`}>
            <option value="">All actions</option>
            {actionsInCat.map((a) => <option key={a.key} value={a.key}>{a.key} ({a.count})</option>)}
          </select>
          <select aria-label="User" value={f.actor} onChange={(e) => set({ actor: e.target.value })} className={`${sel} max-w-[14rem]`}>
            <option value="">All users</option>
            {facets.data?.actors.map((a) => <option key={a.key} value={a.key}>{a.email ?? "System"} ({a.count})</option>)}
          </select>
          <select aria-label="Item type" value={f.type} onChange={(e) => set({ type: e.target.value })} className={sel}>
            <option value="">All item types</option>
            {facets.data?.entity_types.map((t) => <option key={t.key} value={t.key}>{t.type ? typeLabel(t.type) : "No item"} ({t.count})</option>)}
          </select>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <span className="inline-flex items-center gap-1 text-xs font-medium text-muted"><Filter size={13} />Dates</span>
          <input type="date" aria-label="From" value={f.start} max={f.end || undefined} onChange={(e) => set({ start: e.target.value })} className={sel} />
          <span className="text-muted">to</span>
          <input type="date" aria-label="To" value={f.end} min={f.start || undefined} onChange={(e) => set({ end: e.target.value })} className={sel} />
          {[["Today", 1], ["7 days", 7], ["30 days", 30], ["90 days", 90]].map(([l, d]) => (
            <button key={l} type="button" onClick={() => preset(d as number)} className="rounded-full border border-line px-2.5 py-1 text-xs text-ink/80 hover:border-primary-300 hover:text-ink">{l}</button>
          ))}
          {active.length > 0 && <button type="button" onClick={clearAll} className="ml-auto text-xs font-medium text-primary-700 hover:underline">Clear all filters</button>}
        </div>
        {active.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {active.map((a) => (
              <span key={a.key} className="inline-flex items-center gap-1 rounded-full bg-primary-50 py-0.5 pl-2.5 pr-1 text-xs text-primary-800 ring-1 ring-primary-200">
                {a.label}
                <button type="button" onClick={() => clearOne(a.key)} aria-label={`Remove ${a.label}`} className="grid h-4 w-4 place-items-center rounded-full hover:bg-primary-100"><X size={11} /></button>
              </span>
            ))}
          </div>
        )}
      </section>

      {/* grid */}
      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <div className="max-h-[65vh] overflow-auto">
          <table className="w-full min-w-[1040px] table-fixed border-separate border-spacing-0 text-sm">
            <thead>
              <tr>
                <Th k="created_at" className="w-[10rem]">Time</Th>
                <Th k="actor_email" className="w-[11rem]">User</Th>
                <Th k="action" className="w-[11.5rem]">Action</Th>
                <Th k="summary" className="min-w-[16rem]">Summary</Th>
                <Th k="entity_type" className="w-[7.5rem]">Item</Th>
                <Th k="ip_address" className="w-[7.5rem]">IP address</Th>
                <Th className="w-[6.5rem] text-right"><span className="sr-only">Details</span></Th>
              </tr>
            </thead>
            <tbody ref={body} className={q.isFetching && q.isPlaceholderData ? "opacity-60 transition-opacity" : ""}>
              {rows.map((e, i) => {
                const c = catOf(e.action);
                return (
                  <tr key={e.id} data-row tabIndex={i === focusIdx ? 0 : -1} onFocus={() => setFocusIdx(i)}
                    onDoubleClick={() => setOpen(i)} onKeyDown={(ev) => onKeyRow(ev, i)} title="Double-click for full details"
                    className={`group cursor-default select-none outline-none even:bg-slate-50/50 hover:bg-primary-50/50 focus-visible:bg-primary-50 focus-visible:shadow-[inset_3px_0_0_rgb(var(--p-500))] ${open === i ? "bg-primary-50" : ""}`}>
                    <td className="whitespace-nowrap border-b border-line/70 px-3 py-2.5 align-top">
                      <div className="tabular-nums text-ink">{fmt(e.created_at, tz, { dateStyle: "medium", timeStyle: "short" })}</div>
                      <div className="text-[11px] text-muted">{ago(e.created_at)}</div>
                    </td>
                    <td className="border-b border-line/70 px-3 py-2.5 align-top">
                      <div className="truncate text-ink" title={e.actor_email ?? "System"}>{e.actor_email ?? <span className="italic text-muted">System</span>}</div>
                    </td>
                    <td className="border-b border-line/70 px-3 py-2.5 align-top">
                      <span className={`inline-block rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONE[c] ?? "bg-slate-50 text-slate-700 ring-slate-200"}`}>{catLabel(c)}</span>
                      <div className="mt-0.5 font-mono text-[11px] text-muted">{e.action}</div>
                    </td>
                    <td className="border-b border-line/70 px-3 py-2.5 align-top">
                      <div className="line-clamp-2 text-ink" title={e.summary ?? undefined}>{e.summary ?? humanize(e.action)}</div>
                      {e.details && Object.keys(e.details).length > 0 && <div className="mt-0.5 text-[11px] text-muted">{Object.keys(e.details).length} detail{Object.keys(e.details).length === 1 ? "" : "s"} recorded</div>}
                    </td>
                    <td className="border-b border-line/70 px-3 py-2.5 align-top text-ink/80">
                      {e.entity_type ? <><div>{typeLabel(e.entity_type)}</div>{e.entity_id && <div className="font-mono text-[11px] text-muted" title={e.entity_id}>{e.entity_id.slice(0, 8)}…</div>}</> : <span className="text-muted">—</span>}
                    </td>
                    <td className="whitespace-nowrap border-b border-line/70 px-3 py-2.5 align-top font-mono text-xs text-ink/80">{e.ip_address ?? <span className="font-sans text-muted">—</span>}</td>
                    <td className="border-b border-line/70 px-3 py-2 text-right align-top">
                      <button type="button" onClick={() => setOpen(i)} tabIndex={-1}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-xs font-medium text-ink hover:border-primary-300 hover:text-primary-700">
                        <Eye size={13} />Details
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!q.isLoading && rows.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-12 text-center text-muted">
                  {active.length ? <>No entries match these filters. <button type="button" onClick={clearAll} className="text-primary-700 hover:underline">Clear filters</button></> : "Nothing has happened here yet."}
                </td></tr>
              )}
              {q.isLoading && Array.from({ length: 8 }, (_, i) => (
                <tr key={i}><td colSpan={7} className="border-b border-line/70 px-3 py-3"><div className="h-4 animate-pulse rounded bg-slate-100" style={{ width: `${60 + ((i * 37) % 35)}%` }} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* pagination */}
        <div className="flex flex-wrap items-center gap-3 border-t border-line bg-slate-50/60 px-3 py-2 text-sm">
          <span className="text-muted">{total ? <><b className="font-semibold text-ink tabular-nums">{(f.page * f.size + 1).toLocaleString("en-IN")}–{Math.min(total, (f.page + 1) * f.size).toLocaleString("en-IN")}</b> of <b className="font-semibold text-ink tabular-nums">{total.toLocaleString("en-IN")}</b> entries</> : "0 entries"}</span>
          <label className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted">Rows per page
            <select value={f.size} onChange={(e) => set({ size: e.target.value === "50" ? "" : e.target.value })} className="h-8 rounded-md border border-line bg-surface px-1.5 text-sm text-ink">
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="flex items-center gap-1">
            {[
              { l: "First page", i: <ChevronsLeft size={15} />, to: 0, off: f.page === 0 },
              { l: "Previous page", i: <ChevronLeft size={15} />, to: f.page - 1, off: f.page === 0 },
            ].map((b) => <button key={b.l} type="button" aria-label={b.l} title={b.l} disabled={b.off} onClick={() => go(b.to)} className="grid h-8 w-8 place-items-center rounded-md border border-line bg-surface text-ink hover:border-primary-300 disabled:opacity-40">{b.i}</button>)}
            <span className="px-2 text-xs tabular-nums text-muted">Page {f.page + 1} of {pages}</span>
            {[
              { l: "Next page", i: <ChevronRight size={15} />, to: f.page + 1, off: f.page >= pages - 1 },
              { l: "Last page", i: <ChevronsRight size={15} />, to: pages - 1, off: f.page >= pages - 1 },
            ].map((b) => <button key={b.l} type="button" aria-label={b.l} title={b.l} disabled={b.off} onClick={() => go(b.to)} className="grid h-8 w-8 place-items-center rounded-md border border-line bg-surface text-ink hover:border-primary-300 disabled:opacity-40">{b.i}</button>)}
          </div>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted">Tip: double-click a row (or select it and press Enter) to see everything recorded for it. Use ↑ ↓ to move between rows.</p>

      {open != null && rows[open] && (
        <EntryDetail e={rows[open]} tz={tz} index={open} count={rows.length}
          onNav={(d) => setOpen((o) => (o == null ? o : Math.min(rows.length - 1, Math.max(0, o + d))))}
          onClose={() => { const i = open; setOpen(null); setTimeout(() => body.current?.querySelectorAll<HTMLTableRowElement>("tr[data-row]")[i]?.focus(), 0); }}
          onFilter={(patch) => { setOpen(null); if (patch.q) setSearch(String(patch.q)); set(patch); }} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ details popup
function flatten(v: unknown, prefix = ""): [string, unknown][] {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const ent = Object.entries(v as Record<string, unknown>);
    if (!ent.length) return [[prefix || "(empty)", "{}"]];
    return ent.flatMap(([k, x]) => flatten(x, prefix ? `${prefix} › ${k}` : k));
  }
  return [[prefix, v]];
}
function Val({ v }: { v: unknown }) {
  if (v == null || v === "") return <span className="text-muted">—</span>;
  if (typeof v === "boolean") return <span className={v ? "text-emerald-700" : "text-muted"}>{v ? "Yes" : "No"}</span>;
  if (Array.isArray(v)) return v.length ? <span className="flex flex-wrap gap-1">{v.map((x, i) => <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-ink">{typeof x === "object" ? JSON.stringify(x) : String(x)}</span>)}</span> : <span className="text-muted">none</span>;
  const s = String(v);
  if (/^https?:\/\//.test(s)) return <a href={s} target="_blank" rel="noreferrer" className="break-all text-primary-700 hover:underline">{s}</a>;
  return <span className="whitespace-pre-wrap break-words">{s}</span>;
}

function EntryDetail({ e, tz, index, count, onNav, onClose, onFilter }: {
  e: Entry; tz?: string; index: number; count: number; onNav: (d: 1 | -1) => void; onClose: () => void;
  onFilter: (patch: Record<string, string>) => void;
}) {
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const k = (ev: KeyboardEvent) => {
      if ((ev.target as HTMLElement)?.closest("input,textarea,select")) return;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") { ev.preventDefault(); onNav(1); }
      if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") { ev.preventDefault(); onNav(-1); }
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onNav]);
  const c = catOf(e.action);
  const details = flatten(e.details ?? {}).filter(([k]) => k !== "(empty)");
  const link = e.entity_type && e.entity_id && ENTITY_LINK[e.entity_type]?.(e.entity_id);
  const copy = () => { void navigator.clipboard?.writeText(JSON.stringify(e, null, 2)); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  const Row = ({ label, children }: { label: string; children: ReactNode }) => (
    <div className="grid grid-cols-[9rem_1fr] gap-3 border-b border-line/70 py-2 text-sm last:border-0">
      <dt className="text-muted">{label}</dt><dd className="min-w-0 text-ink">{children}</dd>
    </div>
  );
  const chip = (label: string, patch: Record<string, string>) => (
    <button type="button" onClick={() => onFilter(patch)} className="ml-2 rounded-full border border-line px-2 py-0.5 text-[11px] text-muted hover:border-primary-300 hover:text-primary-700">{label}</button>
  );
  return (
    <Modal title="Activity details" onClose={onClose} wide>
      <div className="-mt-1 mb-3 flex items-start gap-3">
        <span className={`mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONE[c] ?? "bg-slate-50 text-slate-700 ring-slate-200"}`}>{catLabel(c)}</span>
        <p className="text-[15px] font-medium leading-snug text-ink">{e.summary ?? humanize(e.action)}</p>
      </div>
      <dl className="rounded-lg border border-line px-3">
        <Row label="When">
          <span className="tabular-nums">{fmt(e.created_at, tz, { dateStyle: "full", timeStyle: "long" })}</span>
          <span className="block text-xs text-muted">{ago(e.created_at)} · UTC {new Date(e.created_at).toISOString().replace("T", " ").slice(0, 19)}</span>
        </Row>
        <Row label="Who">{e.actor_email ?? <span className="italic text-muted">System (automatic)</span>}{chip("Only this user", { actor: e.actor_email ?? "__system__" })}</Row>
        <Row label="Action"><code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{e.action}</code> <span className="text-muted">· {humanize(e.action)}</span>{chip("Same action", { action: e.action, cat: c })}</Row>
        <Row label="Item">
          {e.entity_type ? <>{typeLabel(e.entity_type)}{e.entity_id && <code className="ml-2 break-all rounded bg-slate-100 px-1.5 py-0.5 text-xs">{e.entity_id}</code>}
            {link && <Link to={link} onClick={onClose} className="ml-2 inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline">Open<ExternalLink size={11} /></Link>}
            {e.entity_id && chip("History of this item", { q: e.entity_id })}</> : <span className="text-muted">—</span>}
        </Row>
        <Row label="IP address">{e.ip_address ? <code className="text-xs">{e.ip_address}</code> : <span className="text-muted">—</span>}</Row>
        <Row label="Entry ID"><code className="break-all text-xs text-muted">{e.id}</code></Row>
      </dl>

      <div className="mt-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">Recorded details</h3>
        <div className="flex items-center gap-2 text-xs">
          {details.length > 0 && <button type="button" onClick={() => setRaw(!raw)} className="rounded-md border border-line px-2 py-1 text-muted hover:text-ink">{raw ? "Show as table" : "Show raw JSON"}</button>}
          <button type="button" onClick={copy} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-muted hover:text-ink"><Copy size={12} />{copied ? "Copied" : "Copy entry"}</button>
        </div>
      </div>
      {details.length === 0 ? (
        <p className="mt-2 rounded-lg bg-slate-50 px-3 py-3 text-sm text-muted">No extra details were stored for this entry.</p>
      ) : raw ? (
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">{JSON.stringify(e.details, null, 2)}</pre>
      ) : (
        <div className="mt-2 max-h-72 overflow-auto rounded-lg border border-line">
          <table className="w-full text-sm">
            <tbody>
              {details.map(([k, v]) => (
                <tr key={k} className="even:bg-slate-50/60">
                  <th scope="row" className="w-[40%] border-b border-line/60 px-3 py-1.5 text-left align-top font-medium text-muted">{humanize(k)}</th>
                  <td className="border-b border-line/60 px-3 py-1.5 align-top text-ink"><Val v={v} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-line pt-3 text-sm">
        <span className="text-xs text-muted">Entry {index + 1} of {count} on this page · ← → to move</span>
        <div className="flex gap-2">
          <Button variant="secondary" disabled={index === 0} onClick={() => onNav(-1)}><span className="inline-flex items-center gap-1"><ChevronLeft size={14} />Previous</span></Button>
          <Button variant="secondary" disabled={index >= count - 1} onClick={() => onNav(1)}><span className="inline-flex items-center gap-1">Next<ChevronRight size={14} /></span></Button>
        </div>
      </div>
    </Modal>
  );
}
