import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, PageHeader, formatDate } from "../components/ui";

type Entry = { id: string; actor_email: string | null; action: string; summary: string | null; ip_address: string | null; created_at: string };
const PAGE = 50;

export default function Activity() {
  const { session } = useAuth();
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => api<{ items: Entry[]; total: number }>(`/audit?limit=${PAGE}&offset=${offset}`),
  });
  const tz = session?.organization?.timezone;

  return (
    <div>
      <PageHeader title="Activity log" description="Who did what in this organization. Entries can't be edited or removed." />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      <ol className="divide-y divide-line rounded-lg border border-line bg-surface">
        {q.data?.items.map((e) => (
          <li key={e.id} className="grid gap-1 px-4 py-3 sm:grid-cols-[170px_1fr]">
            <time className="text-sm text-muted tabular-nums">{formatDate(e.created_at, tz)}</time>
            <div>
              <div>{e.summary ?? e.action}</div>
              <div className="text-xs text-muted">{e.actor_email ?? "System"}{e.ip_address ? ` from ${e.ip_address}` : ""}</div>
            </div>
          </li>
        ))}
        {q.data?.items.length === 0 && <li className="px-4 py-6 text-muted">Nothing has happened here yet.</li>}
      </ol>
      {q.data && q.data.total > PAGE && (
        <div className="mt-4 flex items-center gap-3 text-sm">
          <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Button>
          <Button variant="secondary" disabled={offset + PAGE >= q.data.total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
          <span className="text-muted">{offset + 1}–{Math.min(offset + PAGE, q.data.total)} of {q.data.total}</span>
        </div>
      )}
    </div>
  );
}
