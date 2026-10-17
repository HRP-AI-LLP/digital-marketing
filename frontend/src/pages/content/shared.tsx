import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ExternalLink, FileText, XCircle } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Badge } from "../../components/ui";

// ------------------------------------------------------------------ types
export type PlatformMeta = {
  id: string; label: string; max_chars: number; max_hashtags: number; title_chars: number;
  post_types: string[]; max_images: number; notes: string; manual: boolean;
  image_size?: string; video_size?: string;
};
export type Meta = {
  platforms: PlatformMeta[]; post_types: Record<string, string>; needs_media: string[];
  statuses: Record<string, string>; modes: Record<string, string>; timezone: string;
};
export type AccountBrief = {
  id: string; platform: string; name: string; handle: string | null; status: string; avatar_url: string | null;
  platform_label?: string;
};
export type Pillar = { id: string; name: string; description: string | null; weight: number; color: string; active: boolean };
export type Campaign = { id: string; name: string; brief: string | null; starts_on: string | null; ends_on: string | null; boost: number; active: boolean };
export type MediaItem = { id: string; kind: string; mime: string; width: number | null; height: number | null; url: string | null; alt_text?: string | null };
export type QaCheck = { name: string; ok: boolean; detail: string; hard?: boolean };

export type PostSummary = {
  id: string; platform: string; platform_label: string; post_type: string; status: string; mode: string;
  topic: string | null; title: string | null; text: string | null; hashtags: string[];
  scheduled_at: string | null; approval_deadline: string | null; published_at: string | null;
  account: AccountBrief | null; social_account_id: string | null; schedule_id: string | null;
  pillar: { id: string; name: string; color: string } | null; external_url: string | null; error: string | null;
  qa_score: number | null; qa_passed: boolean | null; similarity: number | null; char_count: number; max_chars: number;
  media: MediaItem[]; warnings: string[];
};
export type Activity = { id: string; action: string; note: string | null; actor: string | null; created_at: string };
export type Metric = { fetched_at: string; impressions: number; reach: number; likes: number; comments: number; shares: number; clicks: number; saves: number; video_views: number };
export type PostDetail = PostSummary & {
  link_url: string | null; first_comment: string | null; media_ids: string[];
  qa: { checks?: QaCheck[]; score?: number | null; issues?: string[]; passed?: boolean; rewritten?: boolean; edited?: boolean; similarity_method?: string };
  rejection_reason: string | null; approved_at: string | null; external_id: string | null; attempts: number;
  created_at: string; updated_at: string; caption: string; max_hashtags: number; title_chars: number;
  brief: { image_brief?: string | null; alt_text?: string | null; angle?: string | null; brief?: string | null; slides?: { heading: string; body: string }[] | null; video_script?: { caption: string; voiceover: string }[] | null };
  activities: Activity[]; metrics: Metric[];
};

// ------------------------------------------------------------------ data hooks
export function useMeta() {
  return useQuery({ queryKey: ["content-meta"], queryFn: () => api<Meta>("/content/meta"), staleTime: Infinity });
}
export function usePlatform(id: string | undefined): PlatformMeta | undefined {
  const meta = useMeta();
  return meta.data?.platforms.find((p) => p.id === id);
}
/** Accounts that can hold posts (content.view permission is enough). */
export function useContentAccounts() {
  return useQuery({ queryKey: ["content-accounts"], queryFn: () => api<AccountBrief[]>("/content/accounts"), staleTime: 60_000 });
}
export function usePillars() {
  return useQuery({ queryKey: ["pillars"], queryFn: () => api<Pillar[]>("/content/pillars") });
}
export function useTz(): string {
  const { session } = useAuth();
  return session?.organization?.timezone ?? "Asia/Kolkata";
}

// ------------------------------------------------------------------ time helpers (always in the org time zone)
export function fmt(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", { ...opts, timeZone: tz }).format(new Date(iso));
}
export function fmtTime(iso: string | null | undefined, tz: string) {
  return fmt(iso, tz, { hour: "2-digit", minute: "2-digit", hour12: false });
}
export function tzLabel(tz: string): string {
  if (tz === "Asia/Kolkata" || tz === "Asia/Calcutta") return "IST";
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" }).formatToParts(new Date())
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? tz;
  } catch {
    return tz;
  }
}
/** Calendar parts of an instant in a time zone. */
export function partsIn(d: Date, tz: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
      .formatToParts(d).map((p) => [p.type, p.value]),
  );
  const hour = parts.hour === "24" ? "00" : parts.hour;
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${hour}:${parts.minute}` };
}
/** ISO instant → value for <input type="datetime-local"> in the org time zone. */
export function toLocalInput(iso: string | null | undefined, tz: string): string {
  if (!iso) return "";
  const p = partsIn(new Date(iso), tz);
  return `${p.date}T${p.time}`;
}
/** The API reads a time without an offset as org-local time, so the input value is sent as-is. */
export function fromLocalInput(v: string): string | null {
  return v ? (v.length === 16 ? `${v}:00` : v) : null;
}
export function until(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - now;
  const abs = Math.abs(ms);
  const m = Math.round(abs / 60000);
  const text = m < 60 ? `${m} min` : m < 60 * 48 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.round(m / 1440)} days`;
  return ms >= 0 ? `in ${text}` : `${text} ago`;
}
export function useNow(intervalMs = 30_000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function caption(text: string | null | undefined, hashtags: string[] | undefined) {
  const tags = (hashtags ?? []).filter(Boolean).map((h) => `#${h}`).join(" ");
  const body = (text ?? "").trimEnd();
  return tags && body ? `${body}\n\n${tags}` : body || tags;
}
export function parseTags(v: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of v.split(/[\s,]+/)) {
    const t = raw.replace(/^#+/, "").replace(/[^\p{L}\p{N}_]/gu, "");
    if (t && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); out.push(t); }
  }
  return out;
}

export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const ACTION_LABELS: Record<string, string> = {
  planned: "Planned", generated: "Written by AI", approved: "Approved", rejected: "Rejected",
  regenerate_requested: "New version requested", edited: "Edited", created: "Created", published: "Published",
  publish_failed: "Publishing failed", publish_retry: "Publishing will retry", publish_now: "Publish now pressed",
  manual_pending: "Ready to post by hand", marked_published: "Marked as published", missed: "Missed its slot",
  auto_approved: "Approved automatically", generation_failed: "Writing failed", deleted: "Deleted",
};

// ------------------------------------------------------------------ small components
export function PlatformTag({ label }: { label: string }) {
  return <span className="inline-flex items-center rounded border border-line px-1.5 py-0.5 text-xs text-muted">{label}</span>;
}

export const STATUS_LABELS: Record<string, string> = {
  planned: "Planned", generating: "Writing", draft: "Draft", in_review: "Waiting for approval", approved: "Approved",
  scheduled: "Scheduled", publishing: "Publishing", published: "Published", manual_pending: "Post by hand",
  failed: "Failed", rejected: "Rejected", missed: "Missed", skipped: "Skipped",
};
const STATUS_TONE: Record<string, "neutral" | "brand" | "accent" | "danger"> = {
  published: "brand", scheduled: "brand", approved: "brand", in_review: "accent", generating: "accent", planned: "neutral",
  publishing: "accent", manual_pending: "accent", failed: "danger", rejected: "danger", missed: "danger",
};
export function PostStatus({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_LABELS[status] ?? status.replace(/_/g, " ")}</Badge>;
}

function Media({ items }: { items: MediaItem[] }) {
  if (!items.length) return null;
  const grid = items.length === 1 ? "grid-cols-1" : "grid-cols-2";
  return (
    <div className={`mt-3 grid gap-1 ${grid}`}>
      {items.slice(0, 4).map((m) =>
        !m.url ? (
          <div key={m.id} className="grid aspect-square place-items-center rounded bg-ink/5 text-xs text-muted">{m.kind}</div>
        ) : m.kind === "video" || m.mime.startsWith("video/") ? (
          <video key={m.id} src={m.url} controls preload="metadata" className="w-full rounded bg-black" />
        ) : m.kind === "document" || m.mime === "application/pdf" ? (
          <a key={m.id} href={m.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded border border-line p-3 text-sm text-brand">
            <FileText size={16} /> Open document
          </a>
        ) : (
          <img key={m.id} src={m.url} alt={m.alt_text ?? ""} loading="lazy" className="w-full rounded object-cover" />
        ),
      )}
      {items.length > 4 && <div className="text-xs text-muted">+{items.length - 4} more</div>}
    </div>
  );
}

/** A post as it will look on the platform (simplified). */
export function PostPreview({ post, clamp }: { post: Pick<PostSummary, "account" | "platform_label" | "title" | "text" | "hashtags" | "media"> & { link_url?: string | null }; clamp?: boolean }) {
  const a = post.account;
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-2 flex items-center gap-2">
        {a?.avatar_url ? (
          <img src={a.avatar_url} alt="" className="h-8 w-8 rounded-full object-cover" />
        ) : (
          <div className="grid h-8 w-8 place-items-center rounded-full bg-brand-tint text-sm font-semibold text-brand-deep">
            {(a?.name ?? "?").slice(0, 1)}
          </div>
        )}
        <div className="min-w-0 leading-tight">
          <div className="truncate text-sm font-semibold">{a?.name ?? "Account removed"}</div>
          <div className="text-xs text-muted">{post.platform_label}</div>
        </div>
      </div>
      {post.title && <div className="mb-1 font-semibold">{post.title}</div>}
      <div className={`whitespace-pre-line break-words text-[15px] ${clamp ? "line-clamp-[10]" : ""}`}>{post.text}</div>
      {post.hashtags.length > 0 && (
        <div className="mt-2 text-[15px] text-primary-700">{post.hashtags.map((h) => `#${h}`).join(" ")}</div>
      )}
      {post.link_url && !post.text?.includes(post.link_url) && (
        <a href={post.link_url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-sm text-brand underline">
          {post.link_url} <ExternalLink size={12} />
        </a>
      )}
      <Media items={post.media} />
    </div>
  );
}

const CHECK_LABELS: Record<string, string> = {
  length: "Length", hashtags: "Hashtags", title: "Title", banned_words: "Banned words", placeholders: "Placeholder text",
  link: "Link", emoji: "Emojis", disclaimer: "Disclaimer",
};

export function QaSummary({ score, passed, similarity, warnings }: { score: number | null; passed: boolean | null; similarity: number | null; warnings?: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {passed === true && <Badge tone="brand">Checks passed</Badge>}
      {passed === false && <Badge tone="danger">Checks need attention</Badge>}
      {score !== null && score !== undefined && <Badge tone={score >= 7 ? "brand" : score >= 5 ? "neutral" : "danger"}>Quality {score}/10</Badge>}
      {similarity !== null && similarity !== undefined && (
        <Badge tone={similarity >= 0.6 ? "danger" : "neutral"}>{Math.round(similarity * 100)}% like an earlier post</Badge>
      )}
      {(warnings ?? []).length > 0 && <Badge tone="danger">{warnings!.length} warning{warnings!.length > 1 ? "s" : ""}</Badge>}
    </div>
  );
}

export function QaPanel({ post }: { post: PostDetail }) {
  const checks = post.qa.checks ?? [];
  return (
    <div className="space-y-3">
      <QaSummary score={post.qa.score ?? null} passed={post.qa.passed ?? null} similarity={post.similarity} />
      {checks.length > 0 && (
        <ul className="space-y-1 text-sm">
          {checks.map((c) => (
            <li key={c.name} className="flex items-start gap-2">
              {c.ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" /> : <XCircle size={16} className="mt-0.5 shrink-0 text-danger" />}
              <span><span className="font-medium">{CHECK_LABELS[c.name] ?? c.name}</span> <span className="text-muted">· {c.detail}</span></span>
            </li>
          ))}
        </ul>
      )}
      {(post.qa.issues ?? []).length > 0 && (
        <div>
          <div className="text-sm font-medium">Reviewer notes</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-muted">{post.qa.issues!.map((i, n) => <li key={n}>{i}</li>)}</ul>
        </div>
      )}
      {post.warnings.length > 0 && <Warnings items={post.warnings} />}
      {post.qa.rewritten && <p className="text-xs text-muted">The AI rewrote the first draft once to fix problems.</p>}
      {post.qa.edited && <p className="text-xs text-muted">Checks were re-run after the last edit.</p>}
    </div>
  );
}

export function Warnings({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1">
      {items.map((w, i) => (
        <li key={i} className="flex items-start gap-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-sm text-amber-900">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" /> {w}
        </li>
      ))}
    </ul>
  );
}

export function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function CharCounter({ count, max }: { count: number; max: number }) {
  const over = count > max;
  return (
    <span className={`text-xs tabular-nums ${over ? "font-semibold text-danger" : count > max * 0.9 ? "text-amber-700" : "text-muted"}`}>
      {count.toLocaleString()} / {max.toLocaleString()}{over && " — too long"}
    </span>
  );
}
