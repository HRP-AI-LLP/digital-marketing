export type Sentiment = "positive" | "neutral" | "negative";
export type QueryKind = "brand" | "competitor" | "industry" | "custom";

export type SourceInfo = {
  id: string; label: string; note: string; enabled: boolean; available: boolean; missing: string | null;
  min_minutes: number; paid: boolean;
  last: { last_run_at: string; ok: boolean; found: number; message: string | null } | null;
};
export type ListenSettings = {
  enabled: boolean; interval_hours: number; digest: "off" | "daily" | "weekly"; spike_alerts: boolean; use_ai: boolean;
  mastodon_instance: string | null; sources: Record<string, boolean>;
};
export type ListenStatus = {
  settings: ListenSettings; sources: SourceInfo[]; last_run_at: string | null; next_run_at: string | null;
  last_found: number | null; queries: number; max_queries: number; new: number; needs_response: number; kinds: QueryKind[];
};
export type SourceRun = { ok: boolean | null; message: string | null; found: number | null; last_run_at: string | null };
export type Query = {
  id: string; name: string; kind: QueryKind; terms: string[]; context_terms: string[]; exclude: string[];
  languages: string[]; countries: string[]; sources: Record<string, boolean>; competitor_id: string | null; enabled: boolean;
  last_run_at: string | null; found_last_run: number | null; source_status: Record<string, SourceRun>; mentions_30d: number;
};
export type Suggestion = Omit<Query, "id" | "sources" | "enabled" | "last_run_at" | "found_last_run" | "source_status" | "mentions_30d"> & {
  why: string; exists: boolean;
};
export type QueryRef = { id: string; name: string; kind: QueryKind };
export type Mention = {
  id: string; source: string; source_label: string; url: string | null; domain: string | null; title: string | null;
  snippet: string | null; author: { name: string | null; handle: string | null; url: string | null; followers: number | null };
  published_at: string | null; found_at: string; language: string | null; score: number | null; comments: number | null;
  reach: number | null; engagement: number; query: QueryRef | null; queries: QueryRef[]; matched_terms: string[];
  relevant: boolean; relevance: number | null; sentiment: Sentiment | null; sentiment_source: string | null; tags: string[];
  needs_response: boolean; response_reason: "question" | "complaint" | "recommendation_request" | null;
  is_competitor: boolean; competitor: { id: string; name: string | null } | null; status: "new" | "reviewed" | "hidden";
  idea_id: string | null; publisher: string | null; link: string | null;
};
export type MentionPage = { items: Mention[]; total: number };
export type Brief = { id: string; title: string; url: string | null; source: string; source_label: string; author: string | null; sentiment: Sentiment | null; published_at: string; engagement: number };
export type Theme = {
  title: string; detail: string; sentiment: Sentiment; links: Brief[];
  quotes: { quote: string; mention_id: string; url: string | null; source: string; author: string | null }[];
};
export type Summary = { generated_at?: string; days?: number; count?: number; summary?: string; themes?: Theme[]; method?: "ai" | "basic" };
export type Overview = {
  days: number;
  totals: { mentions: number; previous: number; change_pct: number | null; positive: number; neutral: number; negative: number;
    negative_share: number; needs_response: number; competitor: number; reach: number };
  sources: { id: string; label: string; mentions: number }[];
  volume: ({ date: string; total: number } & Record<string, number | string>)[];
  volume_sources: string[];
  sentiment_trend: { date: string; positive: number; neutral: number; negative: number }[];
  share_of_voice: { key: string; name: string; is_us: boolean; mentions: number; share: number }[];
  top_domains: { domain: string; mentions: number }[];
  top_authors: { name: string; handle: string | null; source: string; url: string | null; mentions: number; engagement: number; followers: number | null }[];
  top_terms: { term: string; count: number }[];
  top_mentions: Brief[];
  summary: Summary;
};

export const KIND_LABEL: Record<QueryKind, string> = {
  brand: "Your brand", competitor: "Competitor", industry: "Industry", custom: "Other",
};
export const REASON_LABEL: Record<string, string> = {
  question: "Question", complaint: "Complaint", recommendation_request: "Asking for recommendations",
};
