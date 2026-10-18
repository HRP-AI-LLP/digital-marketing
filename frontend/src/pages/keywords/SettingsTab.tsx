import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Field, Input, Select, Spinner, Toggle } from "../../components/ui";
import { usd, useOverview } from "./shared";
import type { Settings } from "./types";

export function SettingsTab() {
  const { can } = useAuth();
  const edit = can("keywords.manage");
  const qc = useQueryClient();
  const o = useOverview();
  const [s, setS] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (o.data) setS(o.data.settings); }, [o.data]);
  const save = useMutation({
    mutationFn: () => api<Settings>("/keywords/settings", { method: "PUT", json: s }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["keywords"] }); setSaved(true); setTimeout(() => setSaved(false), 2500); },
  });
  if (!o.data || !s) return <Spinner label="Loading…" />;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const paid = o.data.provider.configured;
  const sp = o.data.spend;
  return (
    <form className="grid gap-5 lg:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Card className="space-y-4">
        <h2 className="font-semibold">Data sources</h2>
        <ul className="space-y-1.5 text-sm">
          <li>Google Autocomplete and web search: <strong>free</strong>, always on.</li>
          <li>Search Console: {o.data.gsc_connected ? <strong>connected ({o.data.gsc_site})</strong> : <>not connected. <Link className="text-primary-700 underline" to="/analytics?tab=settings">Connect it in Analytics</Link> for your real impressions and positions.</>}</li>
          <li>DataForSEO (optional, paid): {paid ? <strong>{o.data.provider.name}</strong> : <>not added. <Link className="text-primary-700 underline" to="/settings/integrations">Add it in Integrations</Link> for search volumes, difficulty, cost per click and exact Google positions.</>}</li>
        </ul>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Your website" hint="Used to find you in search results. Defaults to the organization website.">
            <Input value={s.our_domain} disabled={!edit} onChange={(e) => set("our_domain", e.target.value)} placeholder={o.data.our_domain || "yourcompany.com"} />
          </Field>
          <Field label="Country">
            <Select value={s.location_code} disabled={!edit} onChange={(e) => set("location_code", Number(e.target.value))}>{o.data.locations.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}</Select>
          </Field>
          <Field label="Language">
            <Select value={s.language_code} disabled={!edit} onChange={(e) => set("language_code", e.target.value)}>{o.data.languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}</Select>
          </Field>
          <Field label="Device for rank checks">
            <Select value={s.device} disabled={!edit} onChange={(e) => set("device", e.target.value as Settings["device"])}><option value="desktop">Desktop</option><option value="mobile">Mobile</option></Select>
          </Field>
        </div>
      </Card>

      <Card className="space-y-4">
        <h2 className="font-semibold">Rank tracking</h2>
        <Toggle checked={s.rank_tracking_enabled} disabled={!edit} onChange={(v) => set("rank_tracking_enabled", v)} label="Check positions every day" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Check after (local time)"><Input type="time" value={s.check_time} disabled={!edit} onChange={(e) => set("check_time", e.target.value)} /></Field>
          <Field label="Most keywords to track"><Input type="number" min={1} max={2000} value={s.max_tracked} disabled={!edit} onChange={(e) => set("max_tracked", Number(e.target.value))} /></Field>
        </div>
        <h3 className="text-sm font-semibold">Alerts</h3>
        <Toggle checked={s.alerts.enabled} disabled={!edit} onChange={(v) => set("alerts", { ...s.alerts, enabled: v })} label="Notify me about big ranking changes" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="A drop of at least"><Input type="number" min={1} max={50} value={s.alerts.drop} disabled={!edit || !s.alerts.enabled} onChange={(e) => set("alerts", { ...s.alerts, drop: Number(e.target.value) })} /></Field>
          <div className="pt-7"><Toggle checked={s.alerts.top3} disabled={!edit || !s.alerts.enabled} onChange={(v) => set("alerts", { ...s.alerts, top3: v })} label="Entering the top 3" /></div>
          <div className="pt-7"><Toggle checked={s.alerts.top10} disabled={!edit || !s.alerts.enabled} onChange={(v) => set("alerts", { ...s.alerts, top10: v })} label="Entering the top 10" /></div>
        </div>
      </Card>

      {paid && (
        <Card className="space-y-4 lg:col-span-2">
          <h2 className="font-semibold">DataForSEO spending</h2>
          <p className="text-sm text-muted">Today {usd(sp.today_usd)} · this month {usd(sp.month_usd)}{sp.by_purpose.length ? ` (${sp.by_purpose.map((p) => `${p.purpose} ${usd(p.cost_usd)}`).join(", ")})` : ""}. Repeated requests are served from the cache for free.</p>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="Daily budget (USD)" hint="0 = no limit. Rank checks stop for the day when it's reached."><Input type="number" min={0} step="0.01" value={s.daily_budget_usd} disabled={!edit} onChange={(e) => set("daily_budget_usd", Number(e.target.value))} /></Field>
            <Field label="Keywords per research"><Input type="number" min={10} max={1000} value={s.research_limit} disabled={!edit} onChange={(e) => set("research_limit", Number(e.target.value))} /></Field>
            <Field label="Keywords per competitor (gap)"><Input type="number" min={10} max={1000} value={s.gap_limit} disabled={!edit} onChange={(e) => set("gap_limit", Number(e.target.value))} /></Field>
            <Field label="Keep results for (days)"><Input type="number" min={1} max={90} value={s.cache_days} disabled={!edit} onChange={(e) => set("cache_days", Number(e.target.value))} /></Field>
          </div>
        </Card>
      )}
      {edit && (
        <div className="flex items-center gap-3 lg:col-span-2">
          <Button type="submit" disabled={save.isPending}>Save settings</Button>
          {saved && <span className="text-sm text-muted">Saved.</span>}
          {save.error && <Alert>{(save.error as Error).message}</Alert>}
        </div>
      )}
    </form>
  );
}
