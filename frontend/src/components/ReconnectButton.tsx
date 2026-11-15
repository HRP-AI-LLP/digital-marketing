// "Reconnect" for one social account, shown wherever an account card says it needs it (next to "Check now").
// Sign-in platforms go straight to the platform's sign-in page (keeping Instagram, LinkedIn pages and reply permission
// as they were). Bluesky, Mastodon, Telegram and Discord ask for their password / token / webhook again: on the Social
// accounts page that form opens in place (onForm); elsewhere the button takes you there and opens it.
import { useMutation } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Button } from "./ui";

type Result = { authorize_url?: string; form?: string };

export function ReconnectButton({ accountId, name, returnTo, onForm, variant = "primary", compact = false }: {
  accountId: string; name?: string; returnTo?: string; onForm?: (form: string) => void;
  variant?: "primary" | "secondary" | "ghost"; compact?: boolean;
}) {
  const nav = useNavigate();
  const go = useMutation({
    mutationFn: () => api<Result>(`/social/accounts/${accountId}/reconnect`, { method: "POST", json: returnTo ? { return_to: returnTo } : {} }),
    onSuccess: (d) => {
      if (d.authorize_url) window.location.href = d.authorize_url;
      else if (d.form) {
        if (onForm) onForm(d.form);
        else nav(`/settings/accounts?reconnect=${accountId}`);
      }
    },
  });
  return (
    <span className="inline-flex flex-col items-start">
      <Button type="button" variant={variant} onClick={() => go.mutate()} disabled={go.isPending}
        className={compact ? "h-7 px-2.5 text-xs" : ""} title={name ? `Sign in again so ${name} works` : "Sign in again"}>
        <PlugZap size={compact ? 13 : 14} /> {go.isPending ? "Opening…" : "Reconnect"}
      </Button>
      {go.error && <span className="mt-1 max-w-xs text-xs text-danger">{(go.error as Error).message}</span>}
    </span>
  );
}

/** Account states that need a new sign-in (or soon will). */
export function needsReconnect(status: string, tokenExpiresAt?: string | null, soonMs = 7 * 24 * 3600 * 1000,
  autoRenews = false): boolean {
  if (status === "expired" || status === "error") return true;
  if (status !== "connected" || !tokenExpiresAt || autoRenews) return false;
  return new Date(tokenExpiresAt).getTime() - Date.now() < soonMs;
}
