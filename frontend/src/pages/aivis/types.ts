export type EngineRef = { key: string; label: string; web: boolean; cost?: "free" | "paid" };
export type SovRow = { name: string; mentions: number; share: number; visibility: number; avg_position: number | null; is_us: boolean; tracked: boolean };
export type Missed = { prompt_id: string | null; text: string; topic: string; answers: number; competitors: string[] };
export type Metrics = {
  visibility: number; answers: number; failed: number; mentioned: number; prompts: number; share_of_voice: number;
  avg_position: number | null; sentiment: { positive: number; neutral: number; negative: number };
  own_cited: number; own_cited_rate: number;
  engines: { key: string; label: string; answers: number; mentioned: number; visibility: number }[];
  topics: { topic: string; answers: number; mentioned: number; visibility: number }[];
  sov: SovRow[]; top_domains: { domain: string; answers: number; own: boolean }[]; missed: Missed[]; missed_count: number;
};
export type Recommendation = {
  title: string; type: string; priority: "high" | "medium" | "low"; why: string; actions: string[];
  prompts: string[]; domains: string[]; source: "ai" | "rules";
};
export type Step = { at: string; message: string; level: string };
export type Run = {
  id: string; trigger: string; status: string; job_id: string | null; created_at: string; started_at: string | null;
  finished_at: string | null; error: string | null; engines: EngineRef[];
  summary: { visibility?: number; share_of_voice?: number; avg_position?: number | null; answers?: number; failed?: number; mentioned?: number; prompts?: number; missed_count?: number };
  last_step: Step | null; metrics?: Metrics; recommendations?: Recommendation[]; steps?: Step[];
};
export type PaidHint = { provider: string; label: string; note: string };
export type Overview = {
  settings: { enabled: boolean; weekday: number; run_time: string }; weekday_label: string; timezone: string; next_run_at: string | null;
  latest: Run | null; previous: { visibility: number; share_of_voice: number; avg_position: number | null } | null;
  active_run: Run | null; last_run: Run | null;
  trend: { run_id: string; at: string; visibility: number; share_of_voice: number; avg_position: number | null; mentioned: number; answers: number }[];
  engines: EngineRef[]; engines_auto: boolean; has_ai: boolean; paid_hints: PaidHint[];
  prompts: { total: number; enabled: number; max: number };
};
export type PromptResult = { answer_id: string; engine_key: string; engine_label: string; status: string; mentioned: boolean; position: number | null; sentiment: string | null; competitors: string[] };
export type Prompt = { id: string; text: string; topic: string; source: string; enabled: boolean; reason: string | null; created_at: string; results: PromptResult[] };
export type PromptsResponse = { run_id: string | null; engines: EngineRef[]; max: number; prompts: Prompt[] };
export type Citation = { url: string; title: string | null; domain: string; own: boolean };
export type Answer = {
  id: string; run_id: string; prompt_id: string | null; prompt_text: string; topic: string; engine_key: string; engine_label: string;
  provider: string; model: string; web: boolean; status: string; mentioned: boolean; position: number | null; sentiment: string | null;
  competitors: string[]; own_cited: boolean; error: string | null; created_at: string;
  answer?: string; citations?: Citation[]; brands?: string[];
  analysis?: { method?: string; snippet?: string; sentiment_reason?: string | null; citations_from?: string; searches?: number };
};
export type AvailableEngine = { provider_id: string; provider: string; name: string; brand: string; models: string[]; suggested_model: string; web: boolean; always_web: boolean; local: boolean; cost: "free" | "paid" };
export type EngineChoice = { key?: string; provider_id: string; provider?: string; model: string; web: boolean; label?: string; cost?: "free" | "paid" };
export type Settings = {
  enabled: boolean; weekday: number; run_time: string; engines: EngineChoice[]; engines_auto: boolean; aliases: string[]; own_domains: string[];
  drop_alert_points: number; llm_analysis: boolean; available_engines: AvailableEngine[]; paid_hints: PaidHint[];
  brand: { name: string; terms: string[]; domains: string[] }; competitors: string[]; weekdays: string[];
};
export type Bot = { bot: string; who: string; status: "allowed" | "blocked"; named: boolean };
export type FileCheck = { found: boolean; status: number; error: string | null; served_but_invalid: boolean; preview: string; issues: string[] };
export type SiteCheck = { checked_at: string; site?: string; error?: string; llms_txt?: FileCheck; llms_full_txt?: FileCheck; robots?: { found: boolean; status: number; error: string | null }; bots?: Bot[] };
export type Llms = {
  text: string; full_text: string; generated_at: string | null; edited_at: string | null; pages: number | null; issues: string[];
  site_check: SiteCheck | null; website: string | null; site_root: string | null; crawled_pages: number;
};
export type Competitors = {
  run_id: string | null; answers?: number; brands: SovRow[];
  engines: { key: string; label: string; answers: number; brands: { name: string; answers: number; visibility: number }[] }[];
  head_to_head: { name: string; tracked: boolean; them: number; both: number; only_them: number; only_us: number; we_rank_higher: number }[];
};

export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 10) / 10}%`);
