import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, ExternalLink, Link2Off } from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { Card } from "../../components/ui";
import { PlatformMark, ago, type Status } from "./shared";

const SETUP: { platform: string; label: string; how: string }[] = [
  { platform: "facebook_page", label: "Facebook page", how: "Connect Facebook and Instagram. Replies need pages_manage_engagement." },
  { platform: "instagram", label: "Instagram", how: "Connect with Instagram ticked. Needs instagram_manage_comments." },
  { platform: "threads", label: "Threads", how: "Connect Threads. Needs threads_read_replies and threads_manage_replies." },
  { platform: "youtube", label: "YouTube", how: "Connect Google. Needs the youtube.force-ssl scope." },
  { platform: "bluesky", label: "Bluesky", how: "Add Bluesky with an app password." },
  { platform: "mastodon", label: "Mastodon", how: "Add Mastodon with a token that has read:notifications and write:statuses." },
];

/** What each connected account can do in the inbox, with permission problems spelled out. */
export default function Channels({ status }: { status: Status }) {
  const { can } = useAuth();
  const synced = status.accounts.filter((a) => a.sync);
  const openOnly = status.accounts.filter((a) => !a.sync && ["linkedin_org", "linkedin_person", "pinterest"].includes(a.platform));
  const missing = SETUP.filter((s) => !status.accounts.some((a) => a.platform === s.platform));
  return (
    <Card className="mb-6 p-0">
      <div className="grid gap-0 md:grid-cols-2">
        <section className="border-b border-line p-5 md:border-b-0 md:border-r">
          <h2 className="mb-3 font-semibold">Accounts in the inbox</h2>
          {synced.length === 0 && <p className="text-sm text-muted">No account that supports comments is connected yet.</p>}
          <ul className="space-y-3">
            {synced.map((a) => {
              const bad = a.ok === false || !!a.missing_permission;
              return (
                <li key={a.id} className="flex gap-3">
                  <PlatformMark platform={a.platform} label={a.platform_label} />
                  <div className="min-w-0 flex-1 text-sm">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-medium">{a.name}</span>
                      {bad ? <AlertTriangle size={14} className="shrink-0 text-amber-600" aria-label="Needs attention" />
                        : a.last_synced_at ? <CheckCircle2 size={14} className="shrink-0 text-emerald-600" aria-label="Working" /> : null}
                    </div>
                    <div className="text-xs text-muted">
                      {a.platform_label} · {a.last_synced_at ? `checked ${ago(a.last_synced_at)}` : "not checked yet"}
                      {a.like ? " · replies and likes" : " · replies"}
                    </div>
                    {a.missing_permission ? (
                      <div className="mt-1.5 rounded-md bg-amber-50 px-2.5 py-2 text-xs text-amber-900">
                        <strong className="font-semibold">Needs {a.missing_permission}.</strong> {a.permission_help}
                      </div>
                    ) : a.ok === false && a.message ? (
                      <div className="mt-1.5 rounded-md bg-amber-50 px-2.5 py-2 text-xs text-amber-900">{a.message}</div>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
          {missing.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer font-medium text-primary-700">How to add more platforms</summary>
              <ul className="mt-2 space-y-1.5 text-xs text-muted">
                {missing.map((m) => <li key={m.platform}><span className="font-medium text-ink">{m.label}:</span> {m.how}</li>)}
              </ul>
              {can("integration.view") && <Link to="/settings/accounts" className="mt-2 inline-block text-xs font-medium text-primary-700 hover:underline">Go to Social accounts</Link>}
            </details>
          )}
        </section>
        <section className="p-5">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><Link2Off size={16} className="text-muted" /> Answer on the platform</h2>
          <p className="mb-3 text-xs text-muted">
            LinkedIn and Pinterest don't let apps like this read comments, so open your recent posts there to reply.
            {openOnly.length === 0 && " Connect LinkedIn or Pinterest to list your posts here."}
          </p>
          {status.open_posts.length === 0 ? (
            <p className="text-sm text-muted">{openOnly.length ? "No posts published there in the last 30 days." : ""}</p>
          ) : (
            <ul className="max-h-56 space-y-1 overflow-y-auto pr-1">
              {status.open_posts.map((p) => (
                <li key={p.post_id}>
                  <a href={p.url} target="_blank" rel="noreferrer" className="group flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-primary-50">
                    <PlatformMark platform={p.platform} label={p.platform_label} size="xs" />
                    <span className="min-w-0 flex-1 truncate">{p.text || "Untitled post"}</span>
                    <span className="shrink-0 text-xs text-muted">{ago(p.published_at)}</span>
                    <ExternalLink size={13} className="shrink-0 text-muted group-hover:text-primary-700" />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Card>
  );
}
