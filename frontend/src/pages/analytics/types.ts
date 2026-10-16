// API shapes for the growth, competitor, health and report endpoints (backend: app/api/analytics_growth.py).

export type Window = {
  current: { posts: number; impressions: number; engagements: number; engagement_rate: number | null; followers_net: number | null; growth_rate?: number | null };
  previous: { posts: number; impressions: number; engagements: number; engagement_rate: number | null; followers_net: number | null };
  deltas: Record<"posts" | "impressions" | "engagements" | "engagement_rate" | "followers_net", number | null>;
};

export type MetricFields = { followers: number | null; following: number | null; posts_count: number | null; reach: number | null; impressions: number | null; profile_views: number | null; engagement: number | null; views: number | null };

export type GrowthAccount = {
  id: string; name: string; platform: string; platform_label: string; handle: string | null; status: string;
  supported: boolean; unsupported_reason: string | null; latest: MetricFields | null; latest_day: string | null; notes: string[];
  deltas: Record<"7" | "30" | "90", { net: number | null; rate: number | null }>;
  series: { day: string; followers: number | null; reach: number | null; impressions: number | null; engagement: number | null; posts_count: number | null }[];
};

export type Growth = {
  days: number; timezone: string; today: string; last_collected_at: string | null;
  totals: { followers: number | null; accounts_with_followers: number; net: number | null; growth_rate: number | null };
  accounts: GrowthAccount[];
  followers_series: { day: string; followers: number | null; accounts: number }[];
  windows: Record<"7" | "30" | "90", Window>;
  weeks: { week: string; posts: number; impressions: number; engagements: number; engagement_rate: number | null; by_platform: Record<string, number> }[];
  platforms: { platform: string; label: string; accounts: number; followers: number | null; net: number | null; posts: number; impressions: number; engagements: number; engagement_rate: number | null; growth_rate: number | null }[];
  heatmap: { weekday: number; day: string; hour: number; posts: number; avg_engagement_rate: number; enough: boolean }[];
  best_times: { day: string; hour: number; posts: number; avg_engagement_rate: number }[];
  best_times_message: string | null;
  top_posts: { id: string; platform: string; post_type: string; title: string; published_at: string; external_url: string | null; impressions: number; engagements: number; engagement_rate: number; has_metrics: boolean }[];
  by_post_type: { key: string; label: string; posts: number; engagement_rate: number }[];
  by_pillar: { key: string; label: string; posts: number; engagement_rate: number; color: string | null }[];
};

export type BenchRow = {
  id: string; name: string; is_us: boolean; followers: number | null; posts_30d: number | null; avg_engagement: number | null;
  net_30d: number | null; engagement_rate: number | null; capped?: boolean; updated?: string | null; error?: string | null; connected?: boolean;
};
export type Insight = { metric: string; label: string; text: string };
export type ChannelBench = {
  rows: BenchRow[];
  share_of_voice: { id: string; name: string; is_us: boolean; engagement_share: number | null; followers_share: number | null; posts_share: number | null }[];
  insights: { lead: Insight[]; lag: Insight[] };
};
export type Channel = "all" | "instagram" | "youtube" | "bluesky" | "mastodon";
export type Handles = Partial<Record<Exclude<Channel, "all">, string>>;
export type Bench = {
  competitors: { id: string; name: string; website: string; handles: Handles; overlap_score: number }[];
  channels: Record<Channel, ChannelBench>;
  website: { id: string; name: string; is_us: boolean; score: number | null }[];
  can_instagram: boolean; updated: string | null;
};

export type HealthPart = { key: string; label: string; weight: number; score: number | null; value: string; how: string; detail: string | null };
export type Health = {
  score: number | null; grade: string; parts: HealthPart[]; computed_at: string; day: string; window_days: number;
  history: { day: string; score: number | null }[]; change_7d: number | null;
};

export type OneClick = { type: string; params: Record<string, unknown>; label: string };
export type Rec = {
  id: string; title: string; why: string | null; action: string | null; impact: "high" | "medium" | "low"; effort: "low" | "medium" | "high";
  category: string; one_click: OneClick | null; source: "ai" | "rules"; status: "new" | "done" | "dismissed"; rank: number;
  result: string | null; created_at: string | null; status_changed_at: string | null;
};
export type Recs = { items: Rec[]; last_run: { at: string; source: string; message: string | null; count: number } | null; can_manage: boolean };

export type Report = {
  id: string; title: string; period_start: string; period_end: string; status: "queued" | "ready" | "failed"; trigger: string;
  schedule_id: string | null; size_bytes: number; recipients: string[]; sent_at: string | null; email_error: string | null;
  error: string | null; summary: { health?: number | null; grade?: string }; created_at: string | null; download_url: string | null;
};
export type Reports = { items: Report[]; email_ready: boolean; can_manage: boolean };
export type ReportSchedule = {
  id: string; name: string; frequency: "weekly" | "monthly"; weekday: number; month_day: number; time: string; recipients: string[];
  active: boolean; next_run_at: string | null; last_sent_at: string | null; last_status: string | null;
};
