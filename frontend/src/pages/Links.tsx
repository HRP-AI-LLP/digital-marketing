import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { setReportParams } from "../components/ExportPdf";
import { presetRange } from "../components/periods";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Toggle } from "../components/ui";
import { BarChart, Kpi, LineChart, fmtNum } from "./analytics/charts";
import { PlatformBadge, shortDay } from "./analytics/vizkit";

export type TrackedLink = {
  id: string; code: string; short_url: string; target_url: string; tracked_url: string; utm: Record<string, string>;
  post_id: string | null; campaign_id: string | null; platform: string | null; label: string | null; source: "auto" | "manual";
  clicks_total: number; clicks: number; last_click_at: string | null; created_at: string | null;
  post: { id: string; title: string; platform: string; status: string; published_at: string | null; external_url: string | null } | null;
};
type Bucket = { key: string; clicks: number };
type Settings = {
  enabled: boolean; base_url: string | null; ready: boolean;
  channels: { id: string; label: string }[]; platforms: { id: string; label: string }[];
};
type Overview = {
  period: { start: string; end: string; days: number };
  settings: Settings;
  kpis: { clicks: number; clicks_previous: number; links: number; links_clicked: number; clicks_all_time: number; per_day: number };
  series: { day: string; clicks: number }[];
  by_platform: Bucket[]; by_referrer: Bucket[]; by_app: Bucket[]; by_device: Bucket[];
  top_posts: { id: string; title: string; platform: string; clicks: number; links: number; external_url: string | null }[];
  items: TrackedLink[];
};
type Detail = TrackedLink & { series: { day: string; clicks: number }[]; by_referrer: Bucket[]; by_app: Bucket[]; by_device: Bucket[] };
type Campaign = { id: string; name: string };

const PERIODS = [{ id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }, { id: "90", label: "Last 90 days" }];
const APP_LABELS: Record<string, string> = {
  browser: "Web browser", linkedin: "LinkedIn app", facebook: "Facebook app", instagram: "Instagram app", threads: "Threads app",
  x: "X app", pinterest: "Pinterest app", tiktok: "TikTok app", snapchat: "Snapchat", whatsapp: "WhatsApp",
};
const REF_LABELS: Record<string, string> = { direct: "Direct or app (no website shown)" };

export function CopyField({ value, label = "Short link" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-canvas px-2.5 py-1.5">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" title={value}>{value}</code>
      <button type="button" aria-label={`Copy ${label.toLowerCase()}`} className="shrink-0 text-xs font-medium text-primary-700 hover:underline"
        onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
        {copied ? "Copied" : <span className="inline-flex items-center gap-1"><Copy size={12} />Copy</span>}
      </button>
    </div>
  );
}

function pct(cur: number, prev: number): number | null {
  if (!prev) return cur ? null : 0;
  return ((cur - prev) / prev) * 100;
}

function useNames(settings?: Settings) {
  return useMemo(() => {
    const m: Record<string, string> = { other: "Other" };
    settings?.platforms.forEach((p) => { m[p.id] = p.label; });
    settings?.channels.forEach((c) => { m[c.id] = c.label; });
    return m;
  }, [settings]);
}

function SettingsCard({ s }: { s: Settings }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (enabled: boolean) => api<Settings>("/links/settings", { method: "PUT", json: { enabled } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["links"] }),
  });
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 max-w-2xl">
          <h2 className="font-semibold">Track links in posts automatically</h2>
          <p className="mt-1 text-sm text-muted">
            When this is on, web links in your posts are swapped for short links like <span className="font-mono">{(s.base_url ?? "https://your-address")}/r/Ab3dE9x</span> just
            before they go out. Each click is counted, and the page that opens gets tags (UTM) so Google Analytics shows which post sent the visitor.
            Your post text stays as you wrote it. X posts keep links off unless links are allowed for that account; Instagram captions can't hold clickable links.
          </p>
        </div>
        <Toggle checked={s.enabled} disabled={!can("links.manage") || save.isPending || !s.ready} onChange={(v) => save.mutate(v)}
          label={s.enabled ? "On" : "Off"} />
      </div>
      {!s.ready && (
        <div className="mt-3"><Alert tone="warn">Short links need the studio's public web address (the Cloudflare Tunnel address, PUBLIC_BASE_URL). Ask whoever installed the studio to set it, then come back here.</Alert></div>
      )}
      {save.error && <div className="mt-3"><Alert>{(save.error as Error).message}</Alert></div>}
    </Card>
  );
}

function NewLinkDialog({ settings, campaigns, onClose }: { settings: Settings; campaigns: Campaign[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ url: "", label: "", platform: "bio", campaign_id: "", utm_campaign: "", utm_content: "", utm_term: "" });
  const [made, setMade] = useState<TrackedLink | null>(null);
  const create = useMutation({
    mutationFn: () => api<TrackedLink>("/links", { method: "POST", json: {
      url: f.url.trim(), label: f.label.trim() || null, platform: f.platform || null, campaign_id: f.campaign_id || null,
      utm: { utm_campaign: f.utm_campaign, utm_content: f.utm_content, utm_term: f.utm_term },
    } }),
    onSuccess: (l) => { setMade(l); qc.invalidateQueries({ queryKey: ["links"] }); },
  });
  return (
    <Modal title={made ? "Your tracked link is ready" : "New tracked link"} onClose={onClose}>
      {made ? (
        <div className="space-y-4">
          <CopyField value={made.short_url} />
          <p className="text-sm text-muted [overflow-wrap:anywhere]">Opens <span className="text-ink">{made.tracked_url}</span></p>
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <Field label="Web address to open" hint="The page people should land on, e.g. a product page or sign-up form.">
            <Input type="url" required placeholder="https://" maxLength={2000} value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />
          </Field>
          <Field label="Name (only you see this)">
            <Input maxLength={200} placeholder="e.g. Newsletter – October" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Where you'll share it">
              <Select value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value })}>
                <optgroup label="Other places">{settings.channels.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</optgroup>
                <optgroup label="Social networks">{settings.platforms.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</optgroup>
              </Select>
            </Field>
            <Field label="Campaign (optional)">
              <Select value={f.campaign_id} onChange={(e) => setF({ ...f, campaign_id: e.target.value })}>
                <option value="">No campaign</option>
                {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
          </div>
          <details className="rounded-lg border border-line px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Google Analytics tags (optional)</summary>
            <p className="mt-2 text-xs text-muted">Filled in for you. Change them only if your team uses its own naming.</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              <Field label="Campaign tag"><Input maxLength={100} placeholder="from the campaign" value={f.utm_campaign} onChange={(e) => setF({ ...f, utm_campaign: e.target.value })} /></Field>
              <Field label="Content tag"><Input maxLength={100} value={f.utm_content} onChange={(e) => setF({ ...f, utm_content: e.target.value })} /></Field>
              <Field label="Keyword tag"><Input maxLength={100} value={f.utm_term} onChange={(e) => setF({ ...f, utm_term: e.target.value })} /></Field>
            </div>
          </details>
          {!settings.ready && <Alert tone="warn">The studio has no public web address yet, so this short link only works inside your network until it's set.</Alert>}
          {create.error && <Alert>{(create.error as Error).message}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={create.isPending || !f.url.trim()}>{create.isPending ? "Creating…" : "Create link"}</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function LinkDialog({ id, range, names, onClose }: { id: string; range: { start: string; end: string }; names: Record<string, string>; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["links", "one", id, range], queryFn: () => api<Detail>(`/links/${id}?start=${range.start}&end=${range.end}`) });
  const remove = useMutation({
    mutationFn: () => api(`/links/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["links"] }); onClose(); },
  });
  const d = q.data;
  return (
    <Modal title={d?.label || (d ? `/r/${d.code}` : "Link")} onClose={onClose} wide>
      {q.isLoading ? <Spinner label="Loading clicks" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : d && (
        <div className="space-y-5">
          <CopyField value={d.short_url} />
          <p className="text-sm text-muted [overflow-wrap:anywhere]">Opens <a className="text-primary-700 hover:underline" href={d.tracked_url} target="_blank" rel="noreferrer">{d.tracked_url}</a></p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Kpi label="Clicks in period" value={fmtNum(d.clicks)} />
            <Kpi label="All-time clicks" value={fmtNum(d.clicks_total)} />
            <Kpi label="Shared on" value={names[d.platform ?? "other"] ?? d.platform ?? "—"} />
          </div>
          <LineChart title="Clicks per day" labels={d.series.map((x) => shortDay(x.day))} series={[{ name: "Clicks", values: d.series.map((x) => x.clicks) }]} height={180} />
          <div className="grid gap-5 sm:grid-cols-2">
            <BarChart title="Where clicks came from" data={d.by_referrer.map((b) => ({ label: REF_LABELS[b.key] ?? b.key, value: b.clicks }))} />
            <BarChart title="App or browser" data={d.by_app.map((b) => ({ label: APP_LABELS[b.key] ?? b.key, value: b.clicks }))} />
          </div>
          {d.post && <p className="text-sm">In the post <Link className="text-primary-700 hover:underline" to={`/posts/${d.post.id}`}>{d.post.title}</Link></p>}
          {d.source === "manual" && can("links.manage") && (
            <div className="flex justify-end border-t border-line pt-3">
              <Button variant="ghost" disabled={remove.isPending} onClick={() => { if (confirm("Delete this link? Anyone who has it will see a 'link doesn't work' page.")) remove.mutate(); }}>
                <Trash2 size={15} /> Delete link
              </Button>
            </div>
          )}
          {remove.error && <Alert>{(remove.error as Error).message}</Alert>}
        </div>
      )}
    </Modal>
  );
}

export default function Links() {
  const { can } = useAuth();
  const [period, setPeriod] = useState("30");
  const [platform, setPlatform] = useState("");
  const [campaign, setCampaign] = useState("");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const range = useMemo(() => presetRange(period), [period]);

  useEffect(() => { const t = setTimeout(() => setTerm(search.trim()), 300); return () => clearTimeout(t); }, [search]);
  useEffect(() => { setReportParams({ ...range, platform, campaign_id: campaign }); }, [range, platform, campaign]);

  const params = new URLSearchParams({ start: range.start, end: range.end });
  if (platform) params.set("platform", platform);
  if (campaign) params.set("campaign_id", campaign);
  if (term) params.set("q", term);
  const q = useQuery({ queryKey: ["links", "list", params.toString()], queryFn: () => api<Overview>(`/links?${params}`) });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => api<Campaign[]>("/content/campaigns"), enabled: can("content.view") });
  const names = useNames(q.data?.settings);
  const d = q.data;

  return (
    <div className="min-w-0 space-y-6">
      <PageHeader title="Link clicks"
        description="Short links that count clicks from your posts, link-in-bio, emails and anywhere else you share them."
        action={can("links.manage") && d && <Button onClick={() => setCreating(true)}><Plus size={16} /> New tracked link</Button>} />

      {q.isLoading ? <Spinner label="Loading link clicks" /> : q.error ? <Alert>{(q.error as Error).message}</Alert> : d && <>
        <SettingsCard s={d.settings} />

        <div className="flex flex-wrap items-end gap-3">
          <Field label="Period">
            <Select value={period} onChange={(e) => setPeriod(e.target.value)}>{PERIODS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</Select>
          </Field>
          <Field label="Shared on">
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="">Everywhere</option>
              {[...d.settings.platforms, ...d.settings.channels].map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </Select>
          </Field>
          {(campaigns.data?.length ?? 0) > 0 && (
            <Field label="Campaign">
              <Select value={campaign} onChange={(e) => setCampaign(e.target.value)}>
                <option value="">All campaigns</option>
                {campaigns.data!.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
          )}
          <Field label="Find a link">
            <div className="relative">
              <Search size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
              <Input className="pl-8" placeholder="Address or name" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi label="Clicks" value={fmtNum(d.kpis.clicks)} delta={pct(d.kpis.clicks, d.kpis.clicks_previous)} />
          <Kpi label="Clicks per day" value={fmtNum(d.kpis.per_day)} />
          <Kpi label="Links clicked" value={`${fmtNum(d.kpis.links_clicked)} of ${fmtNum(d.kpis.links)}`} />
          <Kpi label="All-time clicks" value={fmtNum(d.kpis.clicks_all_time)} hint="Every click since each link was made" />
        </div>

        <Card>
          <LineChart title="Clicks per day" description="People who opened a tracked link. Link previews and bots aren't counted."
            labels={d.series.map((x) => shortDay(x.day))} series={[{ name: "Clicks", values: d.series.map((x) => x.clicks) }]} />
        </Card>

        <div className="grid gap-6 lg:grid-cols-3">
          <Card><BarChart title="By platform" description="Where the link was shared"
            data={d.by_platform.map((b) => ({ label: names[b.key] ?? b.key, value: b.clicks }))} /></Card>
          <Card><BarChart title="Where clicks came from" description="The website the visitor was on"
            data={d.by_referrer.map((b) => ({ label: REF_LABELS[b.key] ?? b.key, value: b.clicks }))} /></Card>
          <Card><BarChart title="App or browser" description={d.by_device.length ? d.by_device.map((b) => `${b.key === "mobile" ? "Phone or tablet" : "Computer"} ${fmtNum(b.clicks)}`).join(" · ") : undefined}
            data={d.by_app.map((b) => ({ label: APP_LABELS[b.key] ?? b.key, value: b.clicks }))} /></Card>
        </div>

        {d.top_posts.length > 0 && (
          <Card>
            <h2 className="font-semibold">Posts that got the most clicks</h2>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <caption className="sr-only">Posts by clicks</caption>
                <thead className="border-b border-line text-left text-muted">
                  <tr><th className="px-3 py-2 font-medium">Post</th><th className="px-3 py-2 font-medium">Platform</th>
                    <th className="px-3 py-2 text-right font-medium">Links</th><th className="px-3 py-2 text-right font-medium">Clicks</th></tr>
                </thead>
                <tbody>
                  {d.top_posts.slice(0, 10).map((p) => (
                    <tr key={p.id} className="border-b border-line last:border-0">
                      <td className="max-w-md px-3 py-2"><Link to={`/posts/${p.id}`} className="text-primary-700 hover:underline [overflow-wrap:anywhere]">{p.title}</Link></td>
                      <td className="px-3 py-2"><span className="inline-flex items-center gap-2"><PlatformBadge platform={p.platform} size={20} />{names[p.platform] ?? p.platform}</span></td>
                      <td className="px-3 py-2 text-right tabular-nums">{p.links}</td>
                      <td className="px-3 py-2 text-right font-semibold tabular-nums">{fmtNum(p.clicks)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Card>
          <h2 className="font-semibold">All tracked links</h2>
          {d.items.length === 0 ? (
            <div className="mt-3"><EmptyState title="No tracked links yet"
              body={d.settings.enabled ? "Links in your posts are tracked from the next post that goes out." : "Turn on automatic tracking above, or make a tracked link for your bio, emails or print."}
              action={can("links.manage") ? <Button onClick={() => setCreating(true)}><Plus size={16} /> New tracked link</Button> : undefined} /></div>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <caption className="sr-only">Tracked links and their clicks</caption>
                <thead className="border-b border-line text-left text-muted">
                  <tr><th className="px-3 py-2 font-medium">Short link</th><th className="px-3 py-2 font-medium">Opens</th>
                    <th className="px-3 py-2 font-medium">Shared on</th><th className="px-3 py-2 text-right font-medium">Clicks</th>
                    <th className="px-3 py-2 text-right font-medium">All time</th></tr>
                </thead>
                <tbody>
                  {d.items.map((l) => (
                    <tr key={l.id} className="border-b border-line align-top last:border-0">
                      <td className="w-64 px-3 py-2">
                        <CopyField value={l.short_url} />
                        <button type="button" className="mt-1 text-xs font-medium text-primary-700 hover:underline" onClick={() => setOpen(l.id)}>
                          {l.label ? `${l.label} · ` : ""}See clicks
                        </button>
                      </td>
                      <td className="max-w-xs px-3 py-2">
                        <a href={l.target_url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 text-ink hover:underline [overflow-wrap:anywhere]">
                          {l.target_url}<ExternalLink size={12} className="mt-1 shrink-0 text-muted" aria-hidden />
                        </a>
                        {l.post && <div className="mt-0.5 text-xs text-muted [overflow-wrap:anywhere]">In <Link className="text-primary-700 hover:underline" to={`/posts/${l.post.id}`}>{l.post.title.slice(0, 80)}</Link></div>}
                      </td>
                      <td className="px-3 py-2">
                        <span className="inline-flex items-center gap-2">{l.platform && <PlatformBadge platform={l.platform} size={20} />}{names[l.platform ?? "other"] ?? l.platform}</span>
                        <div className="mt-1"><Badge tone={l.source === "auto" ? "brand" : "neutral"}>{l.source === "auto" ? "From a post" : "Made by hand"}</Badge></div>
                      </td>
                      <td className="px-3 py-2 text-right font-semibold tabular-nums">{fmtNum(l.clicks)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtNum(l.clicks_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {creating && <NewLinkDialog settings={d.settings} campaigns={campaigns.data ?? []} onClose={() => setCreating(false)} />}
        {open && <LinkDialog id={open} range={range} names={names} onClose={() => setOpen(null)} />}
      </>}
    </div>
  );
}

/** Clicks on the tracked links of one post (post detail page). Renders nothing when the post has none. */
export function PostLinkClicks({ postId }: { postId: string }) {
  const q = useQuery({ queryKey: ["links", "post", postId], queryFn: () => api<Overview>(`/links?post_id=${postId}&days=90`) });
  const d = q.data;
  if (!d || d.items.length === 0) return null;
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold">Link clicks</h2>
        <Link to="/links" className="text-sm text-primary-700 hover:underline">All links</Link>
      </div>
      <div className="text-2xl font-bold tabular-nums">{fmtNum(d.kpis.clicks_all_time)}</div>
      <div className="text-xs text-muted">clicks so far · {fmtNum(d.kpis.clicks)} in the last 90 days</div>
      <ul className="mt-3 space-y-3">
        {d.items.map((l) => (
          <li key={l.id} className="min-w-0 text-sm">
            <CopyField value={l.short_url} />
            <div className="mt-1 flex items-start justify-between gap-2">
              <span className="min-w-0 text-xs text-muted [overflow-wrap:anywhere]">{l.target_url}</span>
              <span className="shrink-0 font-semibold tabular-nums">{fmtNum(l.clicks_total)}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
