export type Severity = "error" | "warning" | "notice";

export type RunBrief = {
  id: string; status: "queued" | "running" | "done" | "failed"; trigger: string; score: number | null; pages: number;
  errors: number; warnings: number; notices: number; created_at: string; started_at: string | null; finished_at: string | null;
  start_url: string | null; error: string | null; new_issues: number | null; fixed_issues: number | null; job_id: string | null;
  progress?: string | null;
};

export type IssueGroup = {
  id: string; severity: Severity; category: string; category_label: string; title: string; why: string; fix: string;
  site_wide: boolean; count: number; new: number; fixed: number;
};

export type Category = { id: string; label: string; error: number; warning: number; notice: number; checks: number };

export type AiBot = { bot: string; owner: string; purpose: "search" | "training"; status: "allowed" | "blocked" | "partial"; named: boolean; rule: string };

export type Lab = { lcp_ms?: number | null; cls?: number | null; tbt_ms?: number | null; fcp_ms?: number | null; inp_ms?: number | null; ttfb_ms?: number | null };
export type Field = Lab & { overall?: string | null; origin_fallback?: boolean; [k: string]: unknown };
export type Scores = { performance: number | null; accessibility: number | null; best_practices: number | null; seo: number | null };
export type StrategySummary = { measured: number; error?: string | null; lab?: Lab; lab_ratings?: Record<string, string>; scores?: Scores; field?: Field | null };
export type PsiResult = { error?: string; scores?: Scores; lab?: Lab; lab_ratings?: Record<string, string>; field?: Field | null; origin_field?: Field | null; opportunities?: { id: string; title: string; savings_ms: number; display?: string }[] };
export type PsiPage = { url: string; mobile?: PsiResult; desktop?: PsiResult };

export type RunFull = RunBrief & {
  stats: Record<string, unknown> & { statuses?: Record<string, number>; previous_score?: number | null; indexable?: number; html_pages?: number; truncated?: boolean };
  config: Record<string, unknown>;
  issues: IssueGroup[];
  categories: Category[];
  site: {
    home?: string; https?: boolean; favicon?: boolean;
    robots?: { found?: boolean; status?: number; sitemaps?: string[]; invalid_lines?: string[]; crawl_delay?: number | null };
    sitemaps?: { url: string; status: number; urls: number; error: string | null; declared: boolean }[];
    llms_txt?: { found?: boolean; url?: string; status?: number; has_title?: boolean };
  };
  ai: { bots: AiBot[]; blocked: number; allowed: number; partial: number; llms_txt: { found?: boolean; url?: string; has_title?: boolean }; robots_found: boolean };
  performance: { summary: { mobile?: StrategySummary; desktop?: StrategySummary }; pages: PsiPage[]; quota_reached?: boolean; with_key?: boolean; error?: string | null };
};

export type Settings = {
  enabled: boolean; start_url: string | null; max_pages: number; max_depth: number; include: string[]; exclude: string[];
  user_agent: string; psi_pages: number; weekday: number; run_time: string; drop_threshold: number; check_external: boolean;
};

export type Overview = {
  website: string | null; settings: Settings; next_run_at: string | null; latest: RunFull | null; active: RunBrief | null;
  last_failed: RunBrief | null; trend: RunBrief[]; timezone: string;
};

export type IssueItem = { id: string; url: string; detail: Record<string, unknown>; is_new: boolean; page_id: string | null };
export type IssueUrls = { check: IssueGroup; total: number; fixed_urls: string[]; items: IssueItem[] };

export type PageRow = {
  id: string; url: string; final_url: string | null; status: number; redirect: boolean; title: string | null; depth: number | null;
  inlinks: number; words: number | null; response_ms: number | null; size_bytes: number | null; indexable: boolean; in_sitemap: boolean;
  errors: number; warnings: number; notices: number; content_type: string | null;
};

export type PageDetail = PageRow & {
  data: Record<string, unknown> & {
    chain?: { url: string; status: number }[]; headers?: Record<string, string>; inlinks_from?: string[]; error?: string | null;
    facts?: Record<string, unknown> & {
      title?: string; description?: string; canonical?: string | null; meta_robots?: string; lang?: string | null;
      viewport?: string | null; headings?: Record<string, string[]>; og?: Record<string, string>; twitter?: Record<string, string>;
      jsonld_types?: string[]; hreflang?: { lang: string; href: string | null }[]; word_count?: number; text_ratio?: number;
      links?: { href: string; anchor: string; nofollow: boolean; internal: boolean }[]; images?: { src: string; alt: string | null }[];
      internal_link_count?: number; external_link_count?: number; excerpt?: string;
    };
  };
  issues: (IssueGroup & { detail: Record<string, unknown>; is_new: boolean })[];
  pagespeed: PsiPage | null;
};

export type Compare = {
  before: RunBrief; after: RunBrief; detailed: boolean; new_count: number | null; fixed_count: number | null;
  checks: { id: string; title: string; severity: Severity; category_label: string; before: number; after: number; change: number }[];
  new: { check: string; title: string; severity: Severity; url: string }[];
  fixed: { check: string; title: string; severity: Severity; url: string }[];
};

export const SEV_LABEL: Record<Severity, string> = { error: "Errors", warning: "Warnings", notice: "Notices" };
export const SEV_ONE: Record<Severity, string> = { error: "Error", warning: "Warning", notice: "Notice" };
export const SEV_STYLE: Record<Severity, string> = {
  error: "bg-danger-tint text-danger border-danger/20",
  warning: "bg-amber-50 text-amber-900 border-amber-200",
  notice: "bg-brand-tint text-brand-deep border-brand/20",
};
export const SEV_DOT: Record<Severity, string> = { error: "bg-danger", warning: "bg-amber-500", notice: "bg-primary-500" };

export function scoreTone(v: number | null | undefined) {
  if (v == null) return "text-muted";
  return v >= 80 ? "text-emerald-700" : v >= 60 ? "text-amber-700" : "text-danger";
}

export const fmtMs = (v: number | null | undefined) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);
export const fmtBytes = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1024 * 1024 ? `${(v / 1024 / 1024).toFixed(1)} MB` : v >= 1024 ? `${Math.round(v / 1024)} KB` : `${v} B`;

export function pathOf(url: string) {
  try {
    const u = new URL(url);
    return (u.pathname + u.search) || "/";
  } catch {
    return url;
  }
}
