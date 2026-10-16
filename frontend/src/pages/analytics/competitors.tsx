import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, Pencil, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, EmptyState, Field, Input, Modal, Spinner, formatDate } from "../../components/ui";
import { BarChart, fmtNum, fmtPct } from "./charts";
import { CollectStatus, Segmented, useCollect } from "./growth";
import type { Bench, BenchRow, Channel, Handles } from "./types";
import { PlatformBadge, signed } from "./vizkit";

const CHANNELS: { id: Channel; label: string }[] = [
  { id: "all", label: "All channels" }, { id: "instagram", label: "Instagram" }, { id: "youtube", label: "YouTube" },
  { id: "bluesky", label: "Bluesky" }, { id: "mastodon", label: "Mastodon" },
];
const HANDLE_FIELDS: { id: keyof Handles; label: string; placeholder: string; hint: string }[] = [
  { id: "instagram", label: "Instagram username", placeholder: "keka_hr", hint: "Business or creator accounts only. Read through your connected Instagram." },
  { id: "youtube", label: "YouTube channel", placeholder: "@kekahr or a channel link", hint: "Read through your Google connection." },
  { id: "bluesky", label: "Bluesky handle", placeholder: "brand.bsky.social", hint: "Public profile, no sign-in needed." },
  { id: "mastodon", label: "Mastodon address", placeholder: "@brand@mastodon.social", hint: "Public profile on their server." },
];

function HandlesModal({ c, onClose }: { c: Bench["competitors"][number]; onClose: () => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState<Handles>({ ...c.handles });
  const save = useMutation({
    mutationFn: () => api(`/analytics/competitors/${c.id}/handles`, { method: "PUT", json: v }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["analytics-competitors"] }); onClose(); },
  });
  return (
    <Modal title={`${c.name}: social profiles`} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="text-sm text-muted">Paste a profile link or handle. Only public numbers are read, through each platform's official API.</p>
        {HANDLE_FIELDS.map((f) => (
          <Field key={f.id} label={f.label} hint={f.hint}>
            <Input value={v[f.id] ?? ""} placeholder={f.placeholder} onChange={(e) => setV({ ...v, [f.id]: e.target.value })} />
          </Field>
        ))}
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>Save profiles</Button>
        </div>
      </form>
    </Modal>
  );
}

function Cell({ v, fmt = fmtNum }: { v: number | null | undefined; fmt?: (n: number) => string }) {
  return <>{v == null ? <span className="text-muted">—</span> : fmt(v)}</>;
}

function BenchTable({ rows, sov }: { rows: BenchRow[]; sov: Bench["channels"]["all"]["share_of_voice"] }) {
  const share = new Map(sov.map((s) => [s.id, s]));
  const best = (k: keyof BenchRow) => Math.max(...rows.map((r) => (typeof r[k] === "number" ? (r[k] as number) : -Infinity)));
  const cls = (r: BenchRow, k: keyof BenchRow) => (typeof r[k] === "number" && r[k] === best(k) && rows.length > 1 ? "font-semibold text-ink" : "");
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <caption className="sr-only">Competitor benchmark</caption>
        <thead className="border-b border-line text-left text-muted">
          <tr>
            <th className="px-3 py-2 font-medium">Brand</th>
            <th className="px-3 py-2 text-right font-medium">Followers</th>
            <th className="px-3 py-2 text-right font-medium">Gained in 30 days</th>
            <th className="px-3 py-2 text-right font-medium">Posts in 30 days</th>
            <th className="px-3 py-2 text-right font-medium">Engagement per post</th>
            <th className="px-3 py-2 text-right font-medium">Engagement rate</th>
            <th className="px-3 py-2 text-right font-medium">Share of voice</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((r) => (
            <tr key={r.id} className={`border-b border-line last:border-0 ${r.is_us ? "bg-primary-50/60" : ""}`}>
              <td className="px-3 py-2.5">
                <span className={r.is_us ? "font-semibold text-primary-800" : "text-ink"}>{r.name}</span>
                {r.is_us && <span className="ml-1.5 text-xs text-muted">(you)</span>}
                {r.error && <div className="text-xs text-danger">{r.error}</div>}
              </td>
              <td className={`px-3 py-2.5 text-right ${cls(r, "followers")}`}><Cell v={r.followers} /></td>
              <td className={`px-3 py-2.5 text-right ${cls(r, "net_30d")}`}>{r.net_30d == null ? <span className="text-muted">—</span> : signed(r.net_30d)}</td>
              <td className={`px-3 py-2.5 text-right ${cls(r, "posts_30d")}`}><Cell v={r.posts_30d} />{r.capped ? "+" : ""}</td>
              <td className={`px-3 py-2.5 text-right ${cls(r, "avg_engagement")}`}><Cell v={r.avg_engagement} /></td>
              <td className="px-3 py-2.5 text-right"><Cell v={r.engagement_rate} fmt={(n) => fmtPct(n, 2)} /></td>
              <td className="px-3 py-2.5 text-right"><Cell v={share.get(r.id)?.engagement_share} fmt={(n) => fmtPct(n, 0)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CompetitorsPanel() {
  const { can } = useAuth();
  const [ch, setCh] = useState<Channel>("all");
  const [editing, setEditing] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["analytics-competitors"], queryFn: () => api<Bench>("/analytics/competitors") });
  const collect = useCollect("competitors");
  if (q.isLoading) return <Spinner label="Loading competitor benchmarks" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const d = q.data!;
  if (d.competitors.length === 0) {
    return <EmptyState title="No confirmed competitors yet" body="Confirm competitors in Company intelligence, then add their social profiles here to compare."
      action={<Link to="/intelligence"><Button variant="secondary">Open Company intelligence</Button></Link>} />;
  }
  const c = d.channels[ch];
  const noHandles = d.competitors.every((x) => Object.keys(x.handles).length === 0);
  const editingComp = d.competitors.find((x) => x.id === editing);

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">{d.updated ? <>Public numbers last read {formatDate(d.updated)}.</> : "Add profiles, then refresh to read public numbers."}</p>
        {can("analytics.manage") && (
          <Button variant="secondary" onClick={() => collect.start.mutate()} disabled={collect.busy}>
            <RefreshCw size={16} className={collect.busy ? "animate-spin" : ""} /> Refresh competitors
          </Button>
        )}
      </div>
      <CollectStatus c={collect} />
      {noHandles && <Alert tone="info">Add each competitor's Instagram, YouTube, Bluesky or Mastodon profile below to start comparing.</Alert>}
      {!d.can_instagram && <Alert tone="warn">Instagram comparisons need your own Instagram professional account connected (Settings → Social accounts).</Alert>}

      <Card>
        <h2 className="mb-3 text-sm font-semibold">Competitor profiles</h2>
        <ul className="divide-y divide-line">
          {d.competitors.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <div className="min-w-[10rem] flex-1">
                <div className="font-medium">{x.name}</div>
                <a href={x.website} target="_blank" rel="noreferrer" className="text-xs text-muted hover:underline">{x.website.replace(/^https?:\/\//, "")}</a>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(x.handles).length === 0 && <span className="text-sm text-muted">No profiles added</span>}
                {Object.entries(x.handles).map(([k, h]) => (
                  <span key={k} className="inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-0.5 text-xs"><PlatformBadge platform={k} size={16} />{h}</span>
                ))}
              </div>
              {can("analytics.manage") && <Button variant="ghost" onClick={() => setEditing(x.id)}><Pencil size={14} /> Edit profiles</Button>}
            </li>
          ))}
        </ul>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Benchmark</h2>
        <Segmented label="Channel" value={ch} onChange={setCh} items={CHANNELS} />
      </div>
      {ch === "all" && <p className="-mt-3 text-xs text-muted">All channels adds up each brand's tracked profiles; your column uses all your connected accounts and posts. Pick a channel for a like-for-like comparison.</p>}
      <Card className="!p-0">
        {c.rows.length <= 1 ? (
          <p className="px-5 py-8 text-center text-sm text-muted">
            {c.rows.length === 0 ? "Connect your own account on this channel to compare." : "No competitor has a profile on this channel yet."}
          </p>
        ) : <BenchTable rows={c.rows} sov={c.share_of_voice} />}
      </Card>
      <p className="-mt-3 text-xs text-muted">Share of voice = each brand's share of all engagement in the group over 30 days (engagement per post × posts). “+” means at least that many: only the latest 25 posts are sampled.</p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ArrowUpRight size={16} className="text-emerald-700" /> Where you lead</h2>
          {c.insights.lead.length ? <ul className="space-y-2 text-sm">{c.insights.lead.map((i) => <li key={i.metric}>{i.text}</li>)}</ul>
            : <p className="text-sm text-muted">Nothing yet on this channel.</p>}
        </Card>
        <Card>
          <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ArrowDownRight size={16} className="text-danger" /> Where you lag</h2>
          {c.insights.lag.length ? <ul className="space-y-2 text-sm">{c.insights.lag.map((i) => <li key={i.metric}>{i.text}</li>)}</ul>
            : <p className="text-sm text-muted">You're at or above the typical competitor on everything measured here.</p>}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card>
          <BarChart title="Followers" data={c.rows.filter((r) => r.followers != null).map((r) => ({ label: r.is_us ? `${r.name} (you)` : r.name, value: r.followers ?? 0, color: r.is_us ? "#086fb8" : "#94a3b8" }))} />
        </Card>
        <Card>
          <BarChart title="Posts in 30 days" data={c.rows.filter((r) => r.posts_30d != null).map((r) => ({ label: r.is_us ? `${r.name} (you)` : r.name, value: r.posts_30d ?? 0, color: r.is_us ? "#086fb8" : "#94a3b8" }))} />
        </Card>
        <Card>
          <BarChart title="Share of voice" format={(v) => fmtPct(v, 0)} max={1}
            data={c.share_of_voice.filter((s) => s.engagement_share != null).map((s) => ({ label: s.is_us ? `${s.name} (you)` : s.name, value: s.engagement_share ?? 0, color: s.is_us ? "#086fb8" : "#94a3b8" }))} />
        </Card>
      </div>

      <Card>
        <BarChart title="Website speed (mobile)" description="PageSpeed performance score out of 100, checked weekly" max={100}
          data={d.website.filter((w) => w.score != null).map((w) => ({ label: w.is_us ? `${w.name} (you)` : w.name, value: w.score ?? 0, color: w.is_us ? "#086fb8" : "#94a3b8" }))} />
      </Card>
      {editingComp && <HandlesModal c={editingComp} onClose={() => setEditing(null)} />}
    </div>
  );
}
