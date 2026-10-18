import type { ReactNode } from "react";
import { api } from "../../api/client";
import { SEV_ONE, SEV_STYLE, type Severity } from "./types";

export function SevBadge({ s, children }: { s: Severity; children?: ReactNode }) {
  return (
    <span className={`inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-xs font-medium ${SEV_STYLE[s]}`}>
      {children ?? SEV_ONE[s]}
    </span>
  );
}

export function UrlLink({ url, className = "" }: { url: string; className?: string }) {
  return (
    <a href={url} target="_blank" rel="noreferrer" className={`text-primary-700 hover:underline [overflow-wrap:anywhere] ${className}`}>
      {url}
    </a>
  );
}

const LABELS: Record<string, string> = {
  status: "Status", linked_from: "Linked from", links: "Links", count: "How many", to: "Goes to", hops: "Redirect steps",
  title: "Title", length: "Characters", others: "Same as", canonical: "Canonical", images: "Images", resources: "Insecure files",
  actions: "Form sends to", words: "Words", depth: "Clicks from home", ms: "Time", kb: "Size (KB)", bots: "Crawlers",
  sitemap: "Sitemap", error: "Problem", lines: "Lines", href: "Points to", hreflang: "hreflang", lang: "Page language",
  missing: "Missing", errors: "Errors", viewport: "Viewport", headings: "Headings", where: "Where", mobile: "Mobile score",
  desktop: "Desktop score", score: "Score", lab_ms: "Lab (ms)", field_ms: "Real users (ms)", lab: "Lab", field: "Real users",
  urls: "URLs", source: "Set by", description: "Description", h1: "H1", query: "Parameters", tags: "Tags", elements: "Elements",
  header: "HTTP header", html: "Page tag", codes: "Codes", image: "Image", og_url: "og:url", ratio: "Text ratio", content: "Content",
  redirects_to: "Redirects to", url: "Address", crawl_delay: "Crawl-delay", size: "Size (bytes)", scripts: "Scripts",
  stylesheets: "Stylesheets", chain: "Redirect steps", origin: "Origin data", content_type: "Content type", rule: "Rule",
};

function Value({ v }: { v: unknown }): JSX.Element {
  if (v == null || v === "") return <span className="text-muted">—</span>;
  if (typeof v === "string") return /^https?:\/\//.test(v) ? <UrlLink url={v} /> : <span className="[overflow-wrap:anywhere]">{v}</span>;
  if (typeof v === "number" || typeof v === "boolean") return <span className="tabular-nums">{String(v)}</span>;
  if (Array.isArray(v)) {
    return (
      <ul className="space-y-0.5">
        {v.slice(0, 20).map((x, i) => (
          <li key={i} className="[overflow-wrap:anywhere]">
            {typeof x === "object" && x !== null ? (
              <span>
                {"url" in x && typeof (x as { url: unknown }).url === "string" ? <UrlLink url={(x as { url: string }).url} /> : null}
                {Object.entries(x as Record<string, unknown>).filter(([k]) => k !== "url").map(([k, val]) => val != null && (
                  <span key={k} className="ml-1.5 text-muted">{LABELS[k] ?? k}: {String(val)}</span>
                ))}
              </span>
            ) : <Value v={x} />}
          </li>
        ))}
        {v.length > 20 && <li className="text-muted">…and {v.length - 20} more</li>}
      </ul>
    );
  }
  return <span className="text-muted">{JSON.stringify(v)}</span>;
}

/** Plain rendering of an issue's detail object (key → value). */
export function Detail({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail).filter(([, v]) => v != null && !(Array.isArray(v) && !v.length));
  if (!entries.length) return null;
  return (
    <dl className="mt-1 grid gap-x-3 gap-y-0.5 text-xs sm:grid-cols-[9rem_1fr]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{LABELS[k] ?? k.replace(/_/g, " ")}</dt>
          <dd className="min-w-0"><Value v={v} /></dd>
        </div>
      ))}
    </dl>
  );
}

export async function downloadCsv(runId: string, kind: "issues" | "pages") {
  const r = await api<{ filename: string; media_type: string; content: string }>(`/site-audit/runs/${runId}/export?kind=${kind}&as_json=true`);
  const url = URL.createObjectURL(new Blob([r.content], { type: r.media_type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = r.filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Rating({ r }: { r?: string | null }) {
  if (!r) return null;
  const map: Record<string, [string, string]> = {
    good: ["Good", "bg-emerald-50 text-emerald-800 border-emerald-200"],
    needs_improvement: ["Needs work", "bg-amber-50 text-amber-900 border-amber-200"],
    poor: ["Poor", "bg-danger-tint text-danger border-danger/20"],
  };
  const [label, cls] = map[r] ?? [r, "bg-ink/5 text-muted border-line"];
  return <span className={`inline-flex rounded border px-1.5 py-0.5 text-xs font-medium ${cls}`}>{label}</span>;
}
