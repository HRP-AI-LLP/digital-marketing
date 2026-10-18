import { AlertTriangle, AtSign, Globe, HelpCircle, Newspaper, Smile, Frown, Minus } from "lucide-react";
import type { ReactNode } from "react";

export type Author = { name: string | null; handle: string | null; avatar_url: string | null; url: string | null };
export type AccountRef = {
  id: string; platform: string; platform_label: string; name: string; handle: string | null;
  avatar_url: string | null; status: string; profile_url: string | null;
};
export type Sentiment = "positive" | "neutral" | "negative" | "question";
export type InboxItem = {
  id: string; source: "account" | "listening"; platform: string; platform_label: string; kind: "comment" | "reply" | "mention";
  account: AccountRef | null; post_id: string | null; external_post_url: string | null; post_snippet: string | null;
  external_id: string | null; parent_id: string | null; thread_key: string | null; url: string | null; author: Author;
  text: string | null; title: string | null; published_at: string | null; created_at: string; language: string | null;
  is_own: boolean; status: "new" | "read" | "replied" | "archived"; assigned_to: { id: string; name: string } | null;
  sentiment: Sentiment | null; sentiment_source: string | null; priority: boolean; keyword: string | null;
  engagement: number | null; ai_suggestions: string[]; reply_text: string | null; replied_at: string | null;
  reply_error: string | null; liked_at: string | null; can_reply: boolean; can_like: boolean;
};
export type Detail = {
  item: InboxItem; thread: InboxItem[];
  post: { id: string; text: string | null; title: string | null; external_url: string | null; published_at: string | null; platform: string } | null;
  can_reply: boolean; reply_blocked_reason: string | null; reply_limit: number | null; can_like: boolean;
  permission_issue: { permission: string; message: string | null } | null;
};
export type AccountStatus = AccountRef & {
  sync: boolean; reply: boolean; like: boolean; note: string; last_synced_at: string | null; ok: boolean | null;
  message: string | null; missing_permission: string | null; permission_help: string | null; reconnect: boolean;
  read_permissions: string[]; reply_permissions: string[]; notes: string[];
};
export type SourceStatus = {
  id: string; label: string; enabled: boolean; available: boolean; needs: string | null; interval_minutes: number;
  can_reply: boolean; last_run_at: string | null; ok: boolean | null; message: string | null; found: number | null;
};
export type OpenPost = { post_id: string; platform: string; platform_label: string; account: string; text: string; url: string; published_at: string | null };
export type Status = { accounts: AccountStatus[]; open_posts: OpenPost[]; sources: SourceStatus[]; keywords: string[]; sync_platforms: string[] };
export type Listening = {
  keywords: string[]; excluded: string[]; languages: string[]; sources: Record<string, boolean>; listening_enabled: boolean;
  max_keywords: number; source_info: Record<string, { label: string; interval_minutes: number; needs: string | null }>;
};
export type Person = { id: string; name: string; email: string };

/** Short platform marks (no third-party logos): two letters on a neutral chip. */
const MARKS: Record<string, string> = {
  facebook_page: "Fb", instagram: "Ig", threads: "Th", youtube: "Yt", bluesky: "Bs", mastodon: "Ma",
  linkedin_org: "in", linkedin_person: "in", pinterest: "Pi", gbp: "G", x: "X", tiktok: "Tt", web: "", news: "",
};

export function PlatformMark({ platform, label, size = "sm" }: { platform: string; label: string; size?: "sm" | "xs" }) {
  const cls = size === "xs" ? "h-4 min-w-4 text-[9px]" : "h-5 min-w-5 text-[10px]";
  const icon = platform === "web" ? <Globe size={size === "xs" ? 10 : 12} /> : platform === "news" ? <Newspaper size={size === "xs" ? 10 : 12} /> : MARKS[platform] ?? platform.slice(0, 2);
  return (
    <span title={label} aria-label={label} className={`inline-grid place-items-center rounded-md bg-primary-900 px-1 font-bold leading-none text-secondary-300 ${cls}`}>
      {icon}
    </span>
  );
}

export function Avatar({ author, size = 36, own = false }: { author: Author; size?: number; own?: boolean }) {
  const name = author.name || author.handle || "?";
  const initials = name.replace(/^@/, "").split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
  if (author.avatar_url) {
    return <img src={author.avatar_url} alt="" referrerPolicy="no-referrer" className="shrink-0 rounded-full bg-slate-100 object-cover" style={{ width: size, height: size }} />;
  }
  return (
    <span aria-hidden className={`grid shrink-0 place-items-center rounded-full font-semibold text-white ${own ? "bg-gradient-to-br from-blue-700 to-cyan-500" : "bg-gradient-to-br from-slate-500 to-slate-400"}`}
      style={{ width: size, height: size, fontSize: Math.max(11, size / 2.8) }}>
      {initials}
    </span>
  );
}

const SENTIMENT: Record<Sentiment, { label: string; cls: string; icon: ReactNode }> = {
  positive: { label: "Positive", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/15", icon: <Smile size={12} /> },
  neutral: { label: "Neutral", cls: "bg-slate-100 text-slate-600 ring-slate-500/10", icon: <Minus size={12} /> },
  negative: { label: "Negative", cls: "bg-danger-tint text-danger ring-danger/15", icon: <Frown size={12} /> },
  question: { label: "Question", cls: "bg-accent-tint text-secondary-800 ring-secondary-600/15", icon: <HelpCircle size={12} /> },
};

export function SentimentChip({ s }: { s: Sentiment | null }) {
  if (!s) return null;
  const v = SENTIMENT[s];
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${v.cls}`}>{v.icon}{v.label}</span>;
}

export function UrgentChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-danger px-2 py-0.5 text-[11px] font-semibold text-white">
      <AlertTriangle size={11} /> Urgent
    </span>
  );
}

export function KindChip({ item }: { item: InboxItem }) {
  if (item.kind !== "mention") return null;
  return (
    <span className="inline-flex items-center gap-0.5 rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-800 ring-1 ring-inset ring-primary-600/10">
      <AtSign size={11} />{item.source === "listening" ? (item.keyword ? item.keyword : "Listening") : "Mention"}
    </span>
  );
}

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d} d ago`;
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(iso));
}

export function openLabel(platform: string, label: string) {
  if (platform === "web" || platform === "news") return "Open the page";
  return `Open on ${label.replace(/ page$| profile$| company page$/i, "")}`;
}
