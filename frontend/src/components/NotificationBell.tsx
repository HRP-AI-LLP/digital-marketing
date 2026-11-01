import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { api } from "../api/client";
import { formatDate } from "./ui";

type Item = { id: string; title: string; body: string | null; link: string | null; read: boolean; created_at: string };

/** Links inside the studio open in place; links to other sites (a published post on YouTube) open in a new tab. */
function isExternal(link: string) {
  return /^https?:\/\//i.test(link);
}

export default function NotificationBell({ dark = false }: { dark?: boolean }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const openLink = (link?: string | null) => {
    if (!link) return;
    if (isExternal(link)) window.open(link, "_blank", "noopener,noreferrer");
    else nav(link);
  };
  const box = useRef<HTMLDivElement>(null);
  // Close on a click or tap anywhere outside the panel, or on Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const q = useQuery({ queryKey: ["notifications"], queryFn: () => api<{ unread: number; items: Item[] }>("/notifications?limit=20"), refetchInterval: 30000 });
  const read = useMutation({
    mutationFn: (ids?: string[]) => api("/notifications/read", { method: "POST", json: { ids: ids ?? null } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const unread = q.data?.unread ?? 0;
  return (
    <div className="relative" ref={box}>
      <button
        aria-expanded={open}
        aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
        onClick={() => setOpen((o) => !o)}
        className={`relative rounded-lg p-1.5 ${dark ? "text-white/80 hover:bg-white/10" : "text-muted hover:bg-ink/5"}`}
      >
        <Bell size={18} />
        {unread > 0 && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-gradient-to-r from-grad1 to-grad2 px-1 text-[10px] font-bold text-white">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-2 w-80 rounded-xl border border-line bg-surface text-ink shadow-xl">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="font-semibold">Notifications</span>
            {unread > 0 && <button className="text-xs text-primary-700 hover:underline" onClick={() => read.mutate(undefined)}>Mark all read</button>}
          </div>
          <ul className="max-h-96 divide-y divide-line overflow-y-auto">
            {q.data?.items.map((n) => (
              <li key={n.id}>
                <button
                  className={`block w-full px-4 py-3 text-left hover:bg-primary-50 ${n.read ? "" : "bg-primary-50/50"}`}
                  onClick={() => { read.mutate([n.id]); setOpen(false); openLink(n.link); }}
                >
                  <div className={`text-sm ${n.read ? "" : "font-semibold"}`}>{n.title}</div>
                  {n.body && <div className="line-clamp-2 text-xs text-muted">{n.body}</div>}
                  <div className="mt-0.5 text-[11px] text-muted">{formatDate(n.created_at)}</div>
                </button>
              </li>
            ))}
            {q.data && !q.data.items.length && <li className="px-4 py-6 text-sm text-muted">You're all caught up.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
