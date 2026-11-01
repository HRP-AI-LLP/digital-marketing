import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Recycle } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Select, Spinner, Toggle } from "../components/ui";
import { BestTimeHeatmap, confidenceText, useBestTimes } from "./content/BestTime";
import { DAY_NAMES, PlatformTag, PostStatus, fmt, tzLabel, useContentAccounts, useTz, type PostSummary } from "./content/shared";

const MODE_TITLES: Record<string, string> = {
  approve_first: "Approve first", auto_with_window: "Publish unless rejected", full_auto: "Fully automatic", manual: "Drafts only",
};

// ================================================================== best times
export function BestTimesPanel() {
  const accounts = useContentAccounts();
  const list = (accounts.data ?? []).filter((a) => a.status !== "removed");
  const [accountId, setAccountId] = useState("");
  useEffect(() => { if (!accountId && list.length) setAccountId(list[0].id); }, [accountId, list]);
  const q = useBestTimes(accountId || null);
  const tz = useTz();

  if (accounts.isLoading) return <Spinner label="Loading accounts…" />;
  if (!list.length) return <EmptyState title="No accounts yet" body="Connect a social account to see when it's best to post." />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <Field label="Account">
            <Select value={accountId} onChange={(e) => setAccountId(e.target.value)} className="w-72 max-w-full">
              {list.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.platform_label ?? a.platform}</option>)}
            </Select>
          </Field>
          {d && <Badge tone={d.source === "defaults" ? "neutral" : "brand"}>{confidenceText(d)}</Badge>}
        </div>
        {q.isLoading && <div className="mt-4"><Spinner label="Working out the best times…" /></div>}
        {q.error && <div className="mt-4"><Alert>{(q.error as Error).message}</Alert></div>}
        {d && (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-muted [overflow-wrap:anywhere]">{d.message}</p>
            <BestTimeHeatmap data={d} />
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="mb-1.5 text-sm font-semibold">Next best times</h3>
                <ul className="space-y-1 text-sm">
                  {d.slots.map((s) => (
                    <li key={s.at} className="flex items-center justify-between gap-2 rounded-md border border-line px-2.5 py-1.5">
                      <span>{s.local} <span className="text-muted">{tzLabel(tz)}</span></span>
                      <Badge tone={s.strength === "Best" ? "brand" : "neutral"}>{s.strength}</Badge>
                    </li>
                  ))}
                  {!d.slots.length && <li className="text-muted">No free good times in the next 7 days.</li>}
                </ul>
              </div>
              <div>
                <h3 className="mb-1.5 text-sm font-semibold">Top hours each day</h3>
                <ul className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
                  {d.top.map((t) => (
                    <li key={t.weekday}><span className="inline-block w-10 font-medium">{DAY_NAMES[t.weekday]}</span>{t.hours.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ")}</li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="text-xs text-muted">
              Schedules set to <span className="font-medium">Best time</span> post at these hours automatically. You can also press
              “Suggest best time” when picking a time for a single post.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}

// ================================================================== evergreen
type EvergreenSettings = { enabled: boolean; min_days_between: number; per_week: number; accounts: string[]; mode: string };
type QueueItem = PostSummary & { eligible: boolean; reason: string | null; last_shared: string | null; engagements: number | null; impressions: number | null };
type HistoryItem = PostSummary & { created_at: string; original: { id: string; title: string } | null };
type Evergreen = {
  settings: EvergreenSettings; timezone: string; due: boolean; due_reason: string | null; queue: QueueItem[];
  eligible: number; total: number; history: HistoryItem[];
};

const title = (p: PostSummary) => (p.title || p.text || p.topic || "Untitled post").slice(0, 120);

export function EvergreenPanel() {
  const { can } = useAuth();
  const canManage = can("schedule.manage");
  const canRun = canManage && can("content.create");
  const tz = useTz();
  const nav = useNavigate();
  const qc = useQueryClient();
  const accounts = useContentAccounts();
  const q = useQuery({ queryKey: ["evergreen"], queryFn: () => api<Evergreen>("/content/evergreen") });
  const [f, setF] = useState<EvergreenSettings | null>(null);
  useEffect(() => { if (q.data && !f) setF(q.data.settings); }, [q.data, f]);
  const save = useMutation({
    mutationFn: (json: Partial<EvergreenSettings>) => api<Evergreen>("/content/evergreen", { method: "PUT", json }),
    onSuccess: (d) => { qc.setQueryData(["evergreen"], d); setF(d.settings); },
  });
  const run = useMutation({
    mutationFn: () => api<{ post_id: string }>("/content/evergreen/run-now", { method: "POST" }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["evergreen"] }); nav(`/posts/${r.post_id}`); },
  });

  if (q.isLoading || !f) return q.error ? <Alert>{(q.error as Error).message}</Alert> : <Spinner label="Loading evergreen posts…" />;
  const d = q.data!;
  const accList = (accounts.data ?? []).filter((a) => a.status !== "removed");
  const dirty = JSON.stringify(f) !== JSON.stringify(d.settings);
  const err = save.error || run.error;

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-semibold">Evergreen recycling</h2>
            <p className="mt-0.5 max-w-2xl text-sm text-muted">
              Your best timeless posts are shared again as a fresh version: the AI keeps the facts and links and writes a new
              opening. Reshares follow the account's approval rules and go into its next free posting slot.
            </p>
          </div>
          <Toggle checked={f.enabled} disabled={!canManage || save.isPending} label={f.enabled ? "On" : "Off"}
            onChange={(v) => { setF({ ...f, enabled: v }); save.mutate({ ...f, enabled: v }); }} />
        </div>
        {err && <div className="mt-3"><Alert>{(err as Error).message}</Alert></div>}
        <fieldset disabled={!canManage} className="mt-4 grid gap-4 sm:grid-cols-3">
          <Field label="Reshares per week">
            <Select value={String(f.per_week)} onChange={(e) => setF({ ...f, per_week: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5, 7].map((n) => <option key={n} value={n}>{n} a week</option>)}
            </Select>
          </Field>
          <Field label="Wait before resharing a post (days)" hint="A post isn't reshared again within this many days.">
            <Input type="number" min={7} max={365} value={f.min_days_between}
              onChange={(e) => setF({ ...f, min_days_between: Math.max(7, Math.min(365, Number(e.target.value) || 60)) })} />
          </Field>
          <Field label="Accounts without a schedule" hint="How reshares on those accounts go out.">
            <Select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}>
              {Object.entries(MODE_TITLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
        </fieldset>
        {accList.length > 0 && (
          <fieldset disabled={!canManage} className="mt-4">
            <legend className="mb-1.5 text-sm font-medium">Accounts</legend>
            <p className="mb-2 text-xs text-muted">Leave all unticked to recycle on every account.</p>
            <div className="flex flex-wrap gap-2">
              {accList.map((a) => {
                const on = f.accounts.includes(a.id);
                return (
                  <label key={a.id} className={`inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1 text-sm ${on ? "border-primary-600 bg-primary-50" : "border-line"}`}>
                    <input type="checkbox" className="sr-only" checked={on}
                      onChange={() => setF({ ...f, accounts: on ? f.accounts.filter((x) => x !== a.id) : [...f.accounts, a.id] })} />
                    {a.name} <span className="text-xs text-muted">{a.platform_label ?? a.platform}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        )}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted">
            {d.settings.enabled ? (d.due ? "The next reshare goes out within the hour." : d.due_reason) : "Recycling is off."}{" "}
            {d.eligible} of {d.total} evergreen post{d.total === 1 ? "" : "s"} can be reshared now.
          </p>
          <div className="flex gap-2">
            {canRun && <Button variant="secondary" disabled={run.isPending || !d.eligible} onClick={() => run.mutate()}><Play size={15} /> {run.isPending ? "Writing…" : "Reshare one now"}</Button>}
            {canManage && <Button disabled={!dirty || save.isPending} onClick={() => save.mutate(f)}>{save.isPending ? "Saving…" : "Save settings"}</Button>}
          </div>
        </div>
      </Card>

      <Card>
        <h2 className="mb-2 font-semibold">Evergreen posts</h2>
        {!d.queue.length ? (
          <EmptyState title="No evergreen posts yet" body="Open a published post you'd happily share again and turn on its Evergreen switch." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs text-muted">
                <tr><th className="py-1.5 pr-3 font-medium">Post</th><th className="pr-3 font-medium">Account</th><th className="pr-3 font-medium">Engagement</th><th className="pr-3 font-medium">Last shared</th><th className="font-medium">Next reshare</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {d.queue.map((p) => (
                  <tr key={p.id}>
                    <td className="max-w-xs py-2 pr-3"><Link to={`/posts/${p.id}`} className="hover:underline [overflow-wrap:anywhere]">{title(p)}</Link></td>
                    <td className="pr-3">{p.account?.name ?? "—"} <PlatformTag label={p.platform_label} /></td>
                    <td className="pr-3 tabular-nums">{p.engagements ?? "—"}{p.impressions ? <span className="text-xs text-muted"> / {p.impressions.toLocaleString()} seen</span> : null}</td>
                    <td className="pr-3">{p.last_shared ? fmt(p.last_shared, tz, { dateStyle: "medium" }) : "—"}</td>
                    <td>{p.eligible ? <Badge tone="brand">Ready</Badge> : <span className="text-xs text-muted">{p.reason}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h2 className="mb-2 flex items-center gap-2 font-semibold"><Recycle size={16} /> Reshared</h2>
        {!d.history.length ? <p className="text-sm text-muted">Nothing reshared yet.</p> : (
          <ul className="divide-y divide-line">
            {d.history.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <div className="min-w-0">
                  <Link to={`/posts/${h.id}`} className="font-medium hover:underline [overflow-wrap:anywhere]">{title(h)}</Link>
                  <div className="text-xs text-muted">
                    {h.account?.name ?? h.platform_label} · created {fmt(h.created_at, tz)}
                    {h.original && <> · from <Link to={`/posts/${h.original.id}`} className="underline">“{h.original.title.slice(0, 60)}”</Link></>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {h.scheduled_at && <span className="text-xs text-muted">{fmt(h.scheduled_at, tz)}</span>}
                  <PostStatus status={h.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
