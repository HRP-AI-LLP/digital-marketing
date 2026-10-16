import type { Job } from "../../api/hooks";

export type IntelTab = "overview" | "profile" | "sources" | "competitors" | "audit" | "ideas";

export type EntityType = "product" | "studio" | "service" | "use_case" | "client" | "industry" | "case_study" | "team";

export const ENTITY_TYPES: { id: EntityType; label: string; singular: string; hint: string }[] = [
  { id: "product", label: "Products", singular: "product", hint: "Software or platforms you sell." },
  { id: "studio", label: "Studios", singular: "studio", hint: "Named studios or labs, such as AI Studio." },
  { id: "service", label: "Services", singular: "service", hint: "Services you deliver." },
  { id: "use_case", label: "Use cases", singular: "use case", hint: "Specific problems you solve." },
  { id: "industry", label: "Industries", singular: "industry", hint: "Industries you serve." },
  { id: "case_study", label: "Case studies", singular: "case study", hint: "Named success stories." },
  { id: "client", label: "Clients", singular: "client", hint: "Posts only name clients marked “can be named in posts”." },
  { id: "team", label: "Team", singular: "team member", hint: "Leaders and team members." },
];

export type Profile = {
  id: string; summary: string | null; positioning: string | null; industries: string[]; audiences: string[];
  differentiators: string[]; geography: string | null; social_links: Record<string, string>; confirmed: boolean;
  confirmed_at: string | null; edited_fields: string[]; extracted_at: string | null; updated_at: string;
};

export type Entity = {
  id: string; type: EntityType; name: string; description: string | null; url: string | null; source: string;
  confirmed: boolean; public_mention_ok: boolean; edited: boolean; created_at: string;
};

export type Source = {
  id: string; kind: "website" | "upload" | "competitor" | "note"; title: string; url: string | null; status: string;
  status_message: string | null; page_count: number; last_crawled_at: string | null; max_pages: number | null;
  filename: string | null; size_bytes: number | null; created_at: string;
  crawl: null | {
    pages: number | null; sitemap_found: boolean | null; robots_found: boolean | null; https: boolean | null; truncated: boolean | null;
    blocked_by_robots: number | null; finished_at: string | null; error_count: number; broken_link_count: number;
  };
};

export type CompetitorChange = { at: string; new_offerings: string[]; new_pages: string[] };
export type Competitor = {
  id: string; name: string; website: string; domain: string; status: "suggested" | "confirmed" | "rejected";
  overlap_score: number; reason: string | null; last_crawled_at: string | null; offerings: string[]; positioning: string;
  pricing_signals: string[]; content_themes: string[]; blog_topics: string[]; keywords: string[]; title_keywords: string[];
  audiences: string[]; industries: string[]; page_count: number | null; changes: CompetitorChange[]; origin: string;
};

export type Recommendation = { title: string; detail: string; impact: "high" | "medium" | "low" };
export type AuditSummary = {
  id: string; score: number; created_at: string; website: string | null; pages_analyzed: number | null;
  section_scores: Record<string, number | null | undefined>;
};
export type SocialRow = {
  platform: string; label: string; linked_from_website: string | null; connected: boolean;
  accounts: { name: string; status: string; profile_url: string | null }[];
};
export type Audit = AuditSummary & {
  recommendations: Recommendation[];
  sections: {
    technical?: Record<string, unknown> & { score: number; broken_links?: { url: string; status: number; from: string | null }[] };
    performance?: { score?: number; error?: string; performance?: number | null; accessibility?: number | null; best_practices?: number | null;
      seo?: number | null; lcp_ms?: number | null; cls?: number | null; tbt_ms?: number | null; fcp_ms?: number | null; url?: string };
    social?: { score: number; platforms: SocialRow[]; linked_count: number; connected_count: number };
    content?: { score: number; blog_pages: number; latest_date: string | null; dated_items_last_90_days: number; blog_examples: string[] };
  };
};

export type Idea = {
  id: string; title: string; angle: string | null; pillar_id: string | null; source: string; score: number;
  status: "new" | "used" | "dismissed"; used_count: number; keywords: string[]; formats: string[]; created_at: string;
};
export type Pillar = { id: string; name: string; color: string; active: boolean };

export type Overview = {
  organization_website: string | null;
  website_source: Source | null;
  profile: Profile | null;
  counts: {
    pages: number; uploads: number; entities: Partial<Record<EntityType, number>>; entities_total: number; entities_confirmed: number;
    competitors: Partial<Record<Competitor["status"], number>>; ideas: Partial<Record<Idea["status"], number>>;
  };
  latest_audit: (AuditSummary & { top_recommendations: Recommendation[] }) | null;
  latest_ideas: Idea[];
  running_jobs: (Job & { payload: Record<string, unknown> })[];
  last_run: Job | null;
  ai_ready: boolean;
  search_provider: "configured" | "duckduckgo";
};

export type JobStart = { job_id: string };

export const SOURCE_LABEL: Record<string, string> = {
  idea: "Idea", gap: "Content gap", gsc: "Search Console", competitor: "Competitor", trend: "Trend", manual: "Added by hand",
};

export const IMPACT_TONE: Record<Recommendation["impact"], "danger" | "accent" | "neutral"> = { high: "danger", medium: "accent", low: "neutral" };

export function scoreTone(score: number | null | undefined): string {
  if (score === null || score === undefined) return "text-muted";
  return score >= 80 ? "text-emerald-700" : score >= 50 ? "text-amber-700" : "text-danger";
}

/** Job kind (+ payload) → the key a page section uses to show its progress. */
export function jobKey(kind: string, payload?: Record<string, unknown> | null): string {
  switch (kind) {
    case "intel.run_all": return "run_all";
    case "intel.crawl": return `crawl:${String(payload?.source_id ?? "")}`;
    case "intel.extract": return "extract";
    case "intel.competitors": return "discover";
    case "intel.competitor_crawl": return `competitor:${String(payload?.competitor_id ?? "")}`;
    case "intel.audit": return "audit";
    case "intel.ideas": return "ideas";
    default: return kind;
  }
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try { return new URL(url).host.replace(/^www\./, ""); } catch { return url; }
}
