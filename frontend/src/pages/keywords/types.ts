export type Intent = "informational" | "commercial" | "transactional" | "navigational";

export type KwRow = {
  keyword: string; volume: number | null; kd: number | null; cpc: number | null; competition: number | null;
  intent: Intent | null; intent_guessed?: boolean; trend: number[]; trend_months: string[]; serp_features: string[];
  source: string | null; estimated: boolean; demand?: number | null; seed?: boolean; question?: boolean;
  position?: number | null; url?: string | null; impressions?: number | null; clicks?: number | null;
  gsc?: { clicks: number | null; impressions: number | null; position: number | null; ctr: number | null } | null;
};

export type GapRow = KwRow & {
  category: "missing" | "weak" | "strong"; shared: boolean; ours: number | null; our_url: string | null;
  our_query?: string | null; best_competitor: number | null; note?: string;
  competitors: Record<string, { position: number | null; url: string | null }>;
};

export type ReportMeta = {
  source?: string; provider?: string | null; estimated?: boolean; approximate?: boolean; note?: string; notes?: string[];
  cost_usd?: number; cached?: number; calls?: number; count?: number; gsc_connected?: boolean;
  counts?: Record<string, number>;
};

export type Report<R = KwRow> = {
  id: string; kind: "research" | "gap"; title: string; status: "queued" | "running" | "done" | "failed";
  params: Record<string, unknown> & { competitors?: string[]; seeds?: string[]; our_domain?: string; mode?: string };
  meta: ReportMeta; error: string | null; job_id: string | null; created_at: string; count: number; rows?: R[];
};

export type Settings = {
  location_code: number; language_code: string; device: "desktop" | "mobile"; daily_budget_usd: number; max_tracked: number;
  check_time: string; rank_tracking_enabled: boolean; alerts: { enabled: boolean; drop: number; top3: boolean; top10: boolean };
  research_limit: number; gap_limit: number; cache_days: number; serp_depth: number; cluster_overlap: number; our_domain: string;
};

export type Spend = {
  today_usd: number; month_usd: number; daily_budget_usd: number;
  by_purpose: { purpose: string; calls: number; cost_usd: number }[]; daily: { day: string; cost_usd: number }[];
};

export type Overview = {
  provider: { configured: boolean; name: string | null; id: string | null };
  gsc_connected: boolean; gsc_site: string | null; our_domain: string; settings: Settings; spend: Spend;
  locations: { code: number; name: string }[]; languages: { code: string; name: string }[]; devices: string[];
  counts: { lists: number; tracked: number; reports: number }; recent_seeds: string[]; can_write_articles: boolean;
};

export type Estimate = { cost_usd: number; cached?: boolean; provider?: string | null; note?: string; keywords?: number; method?: string };

export type Cluster = { id: string; name: string; pillar: string; keywords: string[]; volume: number | null; kd: number | null; intent: Intent | null; method: string };

export type KwList = {
  id: string; name: string; description: string | null; count: number; volume: number | null; clusters_count: number;
  cluster_meta: { method?: string; at?: string; cost_usd?: number; note?: string; approximate?: boolean; serp_source?: string };
  created_at: string; updated_at: string; items?: (KwRow & { added_at?: string })[]; clusters?: Cluster[];
};

export type RankRow = {
  id: string; keyword: string; target_url: string | null; location_code: number; language_code: string; device: string;
  active: boolean; tags: string[]; volume: number | null; position: number | null; previous_position: number | null;
  change: number | null; change_7d: number | null; best_position: number | null; url: string | null; gsc_position: number | null;
  source: string | null; last_checked_at: string | null; target_ranking: boolean | null; spark: (number | null)[]; lost: boolean; new: boolean;
};

export type RankOverview = {
  today: string; rows: RankRow[];
  summary: { tracked: number; active: number; top3: number; top10: number; top100: number; not_ranking: number;
    avg_position: number | null; visibility: number | null; improved: number; declined: number };
  series: { date: string; visibility: number | null; avg_position: number | null; avg_gsc_position: number | null; top10: number; checked: number }[];
  best_movers: RankRow[]; worst_movers: RankRow[];
  source: "dataforseo" | "search"; approximate: boolean; depth: number; gsc_connected: boolean; our_domain: string;
  max_tracked: number; check_time: string; enabled: boolean; per_keyword_usd: number;
};

export type History = {
  id: string; keyword: string; points: { date: string; position: number | null; gsc_position: number | null; url: string | null; source: string }[];
  serp_features: string[]; top_urls: string[];
};
