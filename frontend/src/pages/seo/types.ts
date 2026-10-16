export type SeoCheck = { id: string; name: string; ok: boolean; detail: string; weight: number; severity: "ok" | "warn" | "error" };
export type Faq = { q: string; a: string };

export type ArticleSummary = {
  id: string; title: string; slug: string; keyword: string; secondary_keywords: string[]; audience: string | null;
  status: string; word_count: number; seo_score: number; meta_title: string | null; meta_description: string | null;
  error: string | null; export_path: string | null; exported: boolean; cover_media_id: string | null;
  created_at: string; updated_at: string; search_intent: string | null; word_target: number | null; canonical_url: string;
};

export type Article = ArticleSummary & {
  body_md: string; faq: Faq[]; seo_checks: SeoCheck[]; schema_json: Record<string, unknown>;
  outline: { heading: string; intent: string; target_words: number; subsections: { heading: string }[] }[];
  settings: Record<string, unknown>; headings: { level: number; text: string }[];
  cover: { id: string; url: string | null; alt_text?: string | null } | null;
};

export type KeywordIdea = { keyword: string; intent: string; difficulty_guess: string; rationale: string; source: string };

export type SocialAccount = { id: string; platform: string; name: string; handle?: string | null; status: string };

export function scoreTone(score: number) {
  return score >= 80 ? "text-emerald-700" : score >= 50 ? "text-amber-700" : "text-danger";
}
