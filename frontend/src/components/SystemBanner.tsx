import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert } from "./ui";

export type BlogAutopilot = { enabled: boolean; days: number[]; time: string };
export type SystemStatus = {
  worker_running: boolean;
  online: boolean | null;
  offline_since: string | null;
  last_seen: string | null;
  clock_skew_seconds: number | null;
  wake_at: string | null;
  public_base: string | null;
  public_ok: boolean | null;
  recent_downtime: { reason: string; started_at: string; ended_at: string }[];
  catching_up: { id: string; platform_label: string; title: string; due_at: string; posting_at: string; status: string }[];
  settings: { catch_up_gap_minutes: number; wake_for_posts: boolean; auto_approve_posts: boolean; auto_publish_articles: boolean;
    publish_held_posts: boolean; blog_autopilot: BlogAutopilot };
};

export function useSystemStatus() {
  const { can } = useAuth();
  return useQuery({
    queryKey: ["system-status"],
    queryFn: () => api<SystemStatus>("/system/status"),
    enabled: can("content.view"),
    refetchInterval: 30_000,
  });
}

const time = (iso: string) => new Intl.DateTimeFormat("en-IN", { timeStyle: "short" }).format(new Date(iso));

/** Calm, one-line notices: no internet (posting paused), catching up after downtime, or a wrong laptop clock. */
export default function SystemBanner() {
  const q = useSystemStatus();
  const d = q.data;
  if (!d) return null;
  const notes: JSX.Element[] = [];
  if (!d.worker_running) {
    notes.push(
      <Alert key="worker" tone="warn">
        The background worker isn't running, so nothing is being posted right now. Run <code>.\start.ps1</code> and
        everything that fell due will go out gently, one post at a time.
      </Alert>,
    );
  } else if (d.online === false) {
    notes.push(
      <Alert key="offline" tone="warn">
        No internet connection{d.offline_since ? ` since ${time(d.offline_since)}` : ""}. Posting is paused and nothing
        will fail: it picks up by itself as soon as the connection is back.
      </Alert>,
    );
  }
  if (d.worker_running && d.online !== false && d.public_ok === false) {
    notes.push(
      <Alert key="public" tone="warn">
        Your public address ({d.public_base}) isn't answering, so Instagram, Threads and Google can't fetch images and
        Facebook sign-in won't work. Posts that need it wait and go out by themselves once it's back. The studio
        restarts the Cloudflare Tunnel by itself (every 10 minutes while it's down); if this stays, check
        <code>storage\logs\tunnel.err.log</code>.
      </Alert>,
    );
  }
  if (d.catching_up.length) {
    const last = d.catching_up.reduce((m, p) => (p.posting_at > m ? p.posting_at : m), d.catching_up[0].posting_at);
    notes.push(
      <Alert key="catch" tone="info">
        Catching up: {d.catching_up.length} late post{d.catching_up.length === 1 ? "" : "s"} going out one at a time
        {new Date(last) > new Date() ? `, the last at about ${time(last)}` : ""}.{" "}
        <Link className="underline" to="/posts?status=scheduled">See them</Link>
      </Alert>,
    );
  }
  if (d.clock_skew_seconds != null && Math.abs(d.clock_skew_seconds) > 120) {
    notes.push(
      <Alert key="clock" tone="warn">
        This laptop's clock is about {Math.round(Math.abs(d.clock_skew_seconds) / 60)} minutes{" "}
        {d.clock_skew_seconds > 0 ? "fast" : "slow"}, so posts would go out at the wrong time. In Windows, open
        Settings → Time &amp; language → Date &amp; time and press "Sync now".
      </Alert>,
    );
  }
  if (!notes.length) return null;
  return <div className="mb-5 space-y-2">{notes}</div>;
}
