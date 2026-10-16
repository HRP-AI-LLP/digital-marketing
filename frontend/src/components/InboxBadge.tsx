import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export type InboxSummary = { unread: number; comments: number; mentions: number; urgent: number; assigned_to_me: number };

export function useInboxSummary() {
  const { can } = useAuth();
  return useQuery({
    queryKey: ["inbox-summary"],
    queryFn: () => api<InboxSummary>("/inbox/summary"),
    enabled: can("inbox.view"),
    refetchInterval: 60_000,
  });
}

/** Unread count for the sidebar. Turns red-tinted when something urgent is waiting. */
export default function InboxBadge() {
  const q = useInboxSummary();
  const n = q.data?.unread ?? 0;
  if (!n) return null;
  const urgent = (q.data?.urgent ?? 0) > 0;
  return (
    <span
      aria-label={`${n} unread${urgent ? `, ${q.data?.urgent} urgent` : ""}`}
      className={`ml-auto grid h-5 min-w-5 place-items-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums text-white ${
        urgent ? "bg-danger" : "bg-gradient-to-r from-blue-600 to-cyan-500"
      }`}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}
