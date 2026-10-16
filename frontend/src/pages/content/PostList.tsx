import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, EmptyState, Input, PageHeader, Select, Spinner } from "../../components/ui";
import { PostStatus, STATUS_LABELS, fmt, tzLabel, useContentAccounts, useMeta, usePillars, useTz, type PostSummary } from "./shared";

const PAGE = 50;

export default function PostList() {
  const tz = useTz();
  const nav = useNavigate();
  const { can } = useAuth();
  const meta = useMeta();
  const accounts = useContentAccounts();
  const pillars = usePillars();
  const [urlParams] = useSearchParams();
  const [f, setF] = useState({ status: urlParams.get("status") ?? "", platform: "", account_id: "", pillar_id: "", q: "", start: "", end: "" });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  for (const [k, v] of Object.entries(f)) {
    if (!v) continue;
    params.set(k, k === "start" || k === "end" ? `${v}T00:00:00` : v);
  }
  const q = useQuery({
    queryKey: ["posts", params.toString()],
    queryFn: () => api<{ items: PostSummary[]; total: number }>(`/content/posts?${params}`),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setPage(0); };
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const platforms = [...new Set((accounts.data ?? []).map((a) => a.platform))];

  return (
    <div>
      <PageHeader title="All posts" description={`Every post across your accounts. Times are ${tzLabel(tz)}.`}
        action={can("content.create") && <Button onClick={() => nav("/posts/new")}><Plus size={16} /> New post</Button>} />

      <form className="mb-4 grid gap-2 md:grid-cols-4 xl:grid-cols-7" onSubmit={(e) => { e.preventDefault(); setF({ ...f, q: search.trim() }); setPage(0); }}>
        <div className="relative md:col-span-2">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search posts" placeholder="Search text or topic, then press Enter" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <Select aria-label="Status" value={f.status} onChange={set("status")}>
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).filter(([k]) => k !== "skipped").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
        <Select aria-label="Account" value={f.account_id} onChange={set("account_id")}>
          <option value="">All accounts</option>
          {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
        <Select aria-label="Platform" value={f.platform} onChange={set("platform")}>
          <option value="">All platforms</option>
          {platforms.map((p) => <option key={p} value={p}>{meta.data?.platforms.find((x) => x.id === p)?.label ?? p}</option>)}
        </Select>
        <Select aria-label="Pillar" value={f.pillar_id} onChange={set("pillar_id")}>
          <option value="">All pillars</option>
          {(pillars.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
        <div className="flex gap-1">
          <Input type="date" aria-label="From" title="From" value={f.start} onChange={set("start")} />
          <Input type="date" aria-label="To (not included)" title="To (not included)" value={f.end} onChange={set("end")} />
        </div>
      </form>

      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading ? <Spinner label="Loading posts…" /> : items.length === 0 ? (
        <EmptyState title="No posts match" body={Object.values(f).some(Boolean) ? "Try removing a filter." : "Posts appear here once a schedule runs or you write one."}
          action={can("content.create") && <Button onClick={() => nav("/posts/new")}><Plus size={16} /> New post</Button>} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b border-line bg-canvas text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">Account</th>
                <th className="px-3 py-2 font-medium">Post</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((p) => (
                <tr key={p.id} className="cursor-pointer hover:bg-canvas" onClick={() => nav(`/posts/${p.id}`)}>
                  <td className="whitespace-nowrap px-3 py-2.5 tabular-nums text-muted">{fmt(p.published_at ?? p.scheduled_at, tz, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</td>
                  <td className="px-3 py-2.5">
                    <div className="font-medium">{p.account?.name ?? "—"}</div>
                    <div className="text-xs text-muted">{p.platform_label}</div>
                  </td>
                  <td className="max-w-[460px] px-3 py-2.5">
                    <Link to={`/posts/${p.id}`} onClick={(e) => e.stopPropagation()} className="line-clamp-2 hover:underline">
                      {p.title || p.text || <span className="text-muted">{p.topic ?? "Not written yet"}</span>}
                    </Link>
                    {p.pillar && <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-muted"><span className="h-2 w-2 rounded-full" style={{ background: p.pillar.color }} />{p.pillar.name}</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-muted">{meta.data?.post_types[p.post_type] ?? p.post_type}</td>
                  <td className="px-3 py-2.5"><PostStatus status={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > PAGE && (
        <div className="mt-3 flex items-center justify-between text-sm text-muted">
          <span>{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button variant="secondary" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      )}
    </div>
  );
}
