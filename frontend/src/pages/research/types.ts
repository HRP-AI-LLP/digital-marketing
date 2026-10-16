export type Fact = { fact: string; source_url: string | null; publisher: string | null; date: string | null; title?: string | null };
export type BriefFormat = { format: string; label: string; note: string };
export type OccasionRef = {
  key: string; id: string; name: string; date: string; category: string; tone: string; regions: string[]; confidence: string;
};
export type Scores = { relevance: number; timeliness: number; novelty: number; business_value: number; evidence: number };
export type Brief = {
  id: string; run_id: string | null; run_date: string; title: string; angle: string | null; why_now: string | null;
  theme: string; theme_label: string; audience: string | null; facts: Fact[]; formats: BriefFormat[];
  article: { keyword?: string; secondary_keywords?: string[]; outline?: string[] }; cta: { text?: string; url?: string };
  hashtags: string[]; scores: Partial<Scores>; score: number; status: "new" | "saved" | "used" | "dismissed";
  occasion: OccasionRef | null; pillar_id: string | null; opportunity_id: string | null;
  used: { kind: string; id: string | null; at: string }[]; reason: string | null; greeting: string | null;
  followed_up: boolean; created_at: string;
};
export type Step = { at: string; stage: string; message: string; level: "info" | "warn" | "error" };
export type Run = {
  id: string; run_date: string; trigger: "scheduled" | "manual"; depth: string; status: string; job_id: string | null;
  started_at: string | null; finished_at: string | null; error: string | null; created_at: string;
  stats: { llm_calls?: number; searches?: number; results?: number; pages_read?: number; facts?: number; briefs?: number;
    occasion_briefs?: number; topics_added?: number; duration_s?: number };
  last_step: Step | null; steps?: Step[];
};
export type Providers = { ai: boolean; embeddings: boolean; search: { name: string; provider: string }[]; search_fallback: boolean };
export type Overview = {
  today: string; timezone: string; next_run_at: string | null;
  settings: { enabled: boolean; run_time: string; depth: string; ideas_per_day: number };
  latest_run: Run | null; active_run: Run | null;
  counts: { today: number; new: number; saved: number; used: number; dismissed: number };
  latest_brief_date: string | null; providers: Providers;
};
export type Occasion = {
  id: string; key: string; name: string; category: string; category_label: string; date: string; end_date: string | null;
  tone: string; tone_guidance: string; regions: string[]; confidence: string; source: string; hint: string; note: string;
  observed: boolean; has_brief: boolean; greeting_planned: boolean;
};
export type OccasionList = { from: string; to: string; lead_days: number; items: Occasion[]; data_years: number[] };

export type GreetingSettings = {
  enabled: boolean; account_ids: string[]; time: string; mode: string; post_type: string; categories: string[];
};
export type ResearchSettings = {
  enabled: boolean; run_time: string; depth: "quick" | "standard" | "deep"; ideas_per_day: number;
  themes: Record<string, boolean>; custom_themes: { label: string; focus: string | null }[]; keywords: string[];
  trusted_domains: string[]; blocked_domains: string[]; excluded_topics: string[];
  auto_topics: { enabled: boolean; min_score: number; max_per_day: number };
  occasions: { enabled: boolean; categories: string[]; regions: string[]; lead_days: number; greeting_posts: GreetingSettings };
};
export type SettingsMeta = {
  themes: { key: string; label: string; about: string }[];
  depths: { id: string; label: string; about: string }[];
  formats: { id: string; label: string }[];
  occasion_categories: { id: string; label: string }[];
  regions: { id: string; label: string }[];
};
export type SettingsResponse = { settings: ResearchSettings; meta: SettingsMeta; providers: Providers };

export const THEME_TONE: Record<string, string> = {
  company: "bg-primary-50 text-primary-800 ring-primary-200",
  tech_ai: "bg-secondary-50 text-secondary-800 ring-secondary-200",
  studios: "bg-sky-50 text-sky-800 ring-sky-200",
  industries: "bg-indigo-50 text-indigo-800 ring-indigo-200",
  challenges: "bg-amber-50 text-amber-900 ring-amber-200",
  market_news: "bg-rose-50 text-rose-800 ring-rose-200",
  policy: "bg-slate-100 text-slate-800 ring-slate-300",
  events: "bg-violet-50 text-violet-800 ring-violet-200",
  thought_leadership: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  fixed: "Fixed date", rule: "Fixed rule", official: "Official calendar", estimated: "Estimated (moon sighting)",
  derived: "Calculated", verified: "Checked online", holidays: "Holiday calendar", season: "Season",
};

export const fmtDay = (iso: string, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) =>
  new Intl.DateTimeFormat("en-IN", { ...opts, timeZone: "UTC" }).format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));
export const daysBetween = (from: string, to: string) =>
  Math.round((new Date(`${to}T00:00:00Z`).getTime() - new Date(`${from}T00:00:00Z`).getTime()) / 86400000);
