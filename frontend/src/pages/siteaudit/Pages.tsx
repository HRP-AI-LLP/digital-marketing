import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { api } from "../../api/client";
import { Button, EmptyState, Input, Modal, Select, Spinner } from "../../components/ui";
import { Detail, Rating, SevBadge, UrlLink } from "./shared";
import { fmtBytes, fmtMs, pathOf, type PageDetail, type PageRow, type RunFull } from "./types";

const PAGE = 50;
const SORTS = [
  { id: "issues", label: "Most issues" }, { id: "url", label: "Address" }, { id: "status", label: "Status code" },
  { id: "depth", label: "Clicks from home" }, { id: "inlinks", label: "Internal links in" }, { id: "words", label: "Word count" },
  { id: "response", label: "Response time" }, { id: "size", label: "Page size" },
];

export default function PagesTab({ run, openPage, check, clearCheck }: { run: RunFull; openPage: (id: string) => void; check?: string; clearCheck: () => void }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [has, setHas] = useState("any");
  const [indexable, setIndexable] = useState("");
  const [sort, setSort] = useState("issues");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ status, has, sort, order, offset: String(offset), limit: String(PAGE) });
  if (q.trim()) params.set("q", q.trim());
  if (indexable) params.set("indexable", indexable);
  if (check) params.set("check", check);
  const res = useQuery({ queryKey: ["siteaudit", "pages", run.id, params.toString()], queryFn: () => api<{ total: number; items: PageRow[] }>(`/site-audit/runs/${run.id}/pages?${params}`) });
  const reset = <T,>(fn: (v: T) => void) => (v: T) => { setOffset(0); fn(v); };
  const checkTitle = check ? run.issues.find((i) => i.id === check)?.title : null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <Input aria-label="Search pages" placeholder="Search address or title" value={q} onChange={(e) => reset(setQ)(e.target.value)} className="w-full pl-9" />
        </div>
        <Select aria-label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value)} className="w-full sm:w-40">
          <option value="all">All statuses</option><option value="2xx">OK (2xx)</option><option value="3xx">Redirects</option>
          <option value="4xx">Not found (4xx)</option><option value="5xx">Server errors</option><option value="failed">Failed</option>
        </Select>
        <Select aria-label="Issues" value={has} onChange={(e) => reset(setHas)(e.target.value)} className="w-full sm:w-44">
          <option value="any">Any issues</option><option value="errors">With errors</option><option value="warnings">With warnings</option>
          <option value="notices">With notices</option><option value="none">No issues</option>
        </Select>
        <Select aria-label="Indexable" value={indexable} onChange={(e) => reset(setIndexable)(e.target.value)} className="w-full sm:w-44">
          <option value="">Indexable or not</option><option value="true">Indexable</option><option value="false">Not indexable</option>
        </Select>
        <Select aria-label="Sort" value={sort} onChange={(e) => reset(setSort)(e.target.value)} className="w-full sm:w-48">
          {SORTS.map((s) => <option key={s.id} value={s.id}>Sort: {s.label}</option>)}
        </Select>
        <Button variant="secondary" onClick={() => reset(setOrder)(order === "asc" ? "desc" : "asc")} aria-label="Change sort direction">
          {order === "asc" ? "Ascending" : "Descending"}
        </Button>
      </div>
      {checkTitle && (
        <div className="mb-3 flex items-center gap-2 text-sm">
          <span className="text-muted">Only pages with:</span> <strong>{checkTitle}</strong>
          <button className="text-primary-700 hover:underline" onClick={clearCheck}>Clear</button>
        </div>
      )}
      {res.isLoading && <Spinner label="Loading pages…" />}
      {res.data && !res.data.items.length && <EmptyState title="No pages match" body="Change the filters to see more pages." />}
      {!!res.data?.items.length && (
        <>
          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full min-w-[860px] text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th className="px-3 py-2 font-medium">Page</th><th className="font-medium">Status</th><th className="font-medium">Issues</th>
                  <th className="font-medium">Clicks</th><th className="font-medium">Links in</th><th className="font-medium">Words</th>
                  <th className="font-medium">Response</th><th className="pr-3 font-medium">Size</th>
                </tr>
              </thead>
              <tbody>
                {res.data.items.map((p) => (
                  <tr key={p.id} className="cursor-pointer border-t border-line hover:bg-ink/[0.02]" onClick={() => openPage(p.id)}>
                    <td className="max-w-[360px] px-3 py-2">
                      <div className="font-medium text-primary-700 [overflow-wrap:anywhere]">{pathOf(p.url)}</div>
                      <div className="truncate text-xs text-muted">{p.redirect ? `→ ${p.final_url}` : p.title || "—"}</div>
                    </td>
                    <td><StatusCode p={p} /></td>
                    <td className="whitespace-nowrap tabular-nums">
                      {p.errors > 0 && <span className="mr-1.5 text-danger">{p.errors}E</span>}
                      {p.warnings > 0 && <span className="mr-1.5 text-amber-700">{p.warnings}W</span>}
                      {p.notices > 0 && <span className="text-primary-700">{p.notices}N</span>}
                      {!p.errors && !p.warnings && !p.notices && <span className="text-muted">—</span>}
                    </td>
                    <td className="tabular-nums">{p.depth ?? "—"}</td>
                    <td className="tabular-nums">{p.inlinks}</td>
                    <td className="tabular-nums">{p.words ?? "—"}</td>
                    <td className="tabular-nums">{fmtMs(p.response_ms)}</td>
                    <td className="pr-3 tabular-nums">{fmtBytes(p.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between text-sm text-muted">
            <span>{offset + 1}–{Math.min(offset + PAGE, res.data.total)} of {res.data.total}</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button variant="secondary" disabled={offset + PAGE >= res.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatusCode({ p }: { p: PageRow }) {
  const s = p.redirect ? `${p.status} redirect` : p.status === 0 ? "Failed" : String(p.status);
  const tone = p.status === 0 || p.status >= 400 ? "text-danger" : p.redirect ? "text-amber-700" : "text-emerald-700";
  return <span className={`whitespace-nowrap tabular-nums ${tone}`}>{s}{!p.indexable && p.status === 200 && !p.redirect ? <span className="ml-1 text-xs text-muted">noindex/canonical</span> : null}</span>;
}

export function PageModal({ runId, pageId, onClose }: { runId: string; pageId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["siteaudit", "page", runId, pageId], queryFn: () => api<PageDetail>(`/site-audit/runs/${runId}/pages/${pageId}`) });
  const p = q.data;
  const f = p?.data.facts ?? {};
  const row = (k: string, v: React.ReactNode) => (
    <div className="contents"><dt className="text-muted">{k}</dt><dd className="min-w-0 [overflow-wrap:anywhere]">{v ?? "—"}</dd></div>
  );
  return (
    <Modal title="Page details" onClose={onClose} wide>
      {q.isLoading && <Spinner label="Loading…" />}
      {p && (
        <div className="space-y-5 text-sm">
          <div><UrlLink url={p.url} className="font-medium" /></div>
          <dl className="grid grid-cols-[8.5rem_1fr] gap-x-3 gap-y-1">
            {row("Status", <StatusCode p={p} />)}
            {!!p.data.chain?.length && row("Redirects", <ol className="space-y-0.5">{p.data.chain.map((h, i) => <li key={i}>{h.status} · {h.url}</li>)}<li>→ {p.final_url}</li></ol>)}
            {p.data.error && row("Problem", p.data.error)}
            {row("Title", f.title)}
            {row("Description", f.description)}
            {row("H1", f.headings?.h1?.join(" · ") || null)}
            {row("Canonical", f.canonical)}
            {row("Robots", f.meta_robots || p.data.headers?.["x-robots-tag"] || "index, follow (default)")}
            {row("Language", f.lang)}
            {row("Words", f.word_count)}
            {row("Clicks from home", p.depth)}
            {row("Internal links in", p.inlinks)}
            {row("Links out", f.internal_link_count != null ? `${f.internal_link_count} internal · ${f.external_link_count} external` : null)}
            {row("Response", `${fmtMs(p.response_ms)} · ${fmtBytes(p.size_bytes)}`)}
            {row("Structured data", f.jsonld_types?.join(", ") || null)}
            {row("Open Graph", f.og && Object.keys(f.og).length ? Object.keys(f.og).join(", ") : null)}
            {row("In sitemap", p.in_sitemap ? "Yes" : "No")}
          </dl>
          {p.pagespeed && (
            <div>
              <h3 className="mb-1 font-semibold">Page speed</h3>
              {(["mobile", "desktop"] as const).map((k) => {
                const r = p.pagespeed?.[k];
                if (!r) return null;
                return (
                  <div key={k} className="mb-2">
                    <span className="capitalize text-muted">{k}: </span>
                    {r.error ? <span className="text-muted">{r.error}</span> : (
                      <span className="tabular-nums">
                        Performance {r.scores?.performance ?? "—"} · LCP {fmtMs(r.lab?.lcp_ms)} <Rating r={r.lab_ratings?.lcp_ms} /> · CLS {r.lab?.cls?.toFixed(2) ?? "—"}
                      </span>
                    )}
                    {!!r.opportunities?.length && (
                      <ul className="ml-4 list-disc text-xs text-muted">{r.opportunities.slice(0, 4).map((o) => <li key={o.id}>{o.title}{o.savings_ms ? ` (save about ${fmtMs(o.savings_ms)})` : ""}</li>)}</ul>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <div>
            <h3 className="mb-2 font-semibold">Issues on this page ({p.issues.length})</h3>
            {p.issues.length ? (
              <ul className="space-y-2">
                {p.issues.map((i) => (
                  <li key={i.id} className="rounded-lg border border-line px-3 py-2">
                    <div className="flex items-start gap-2"><SevBadge s={i.severity} /><span className="font-medium">{i.title}</span>{i.is_new && <span className="text-xs text-danger">New</span>}</div>
                    <p className="mt-1 text-xs text-muted">{i.fix}</p>
                    <Detail detail={i.detail} />
                  </li>
                ))}
              </ul>
            ) : <p className="text-muted">No issues on this page.</p>}
          </div>
          {!!p.data.inlinks_from?.length && (
            <div>
              <h3 className="mb-1 font-semibold">Linked from</h3>
              <ul className="space-y-0.5">{p.data.inlinks_from.slice(0, 15).map((u) => <li key={u}><UrlLink url={u} /></li>)}</ul>
            </div>
          )}
          {!!f.links?.length && (
            <details>
              <summary className="cursor-pointer font-semibold">Links on this page ({f.links.length})</summary>
              <ul className="mt-2 space-y-0.5 text-xs">
                {f.links.slice(0, 100).map((l) => (
                  <li key={l.href} className="[overflow-wrap:anywhere]">
                    <UrlLink url={l.href} /> <span className="text-muted">“{l.anchor || "no text"}”{l.nofollow ? " · nofollow" : ""}{l.internal ? "" : " · external"}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Modal>
  );
}
