import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Input, PageHeader, Select, Spinner } from "../components/ui";

// ------------------------------------------------------------------ types
type Price = { key: string; input_per_million?: number; output_per_million?: number; per_image?: number; per_second?: number; per_million_chars?: number };
export type UsageRow = {
  provider_id: string | null; integration: string | null; provider: string; provider_label: string; model: string; kind: string;
  calls: number; failures: number; input_tokens: number; output_tokens: number; units: number; cost_usd: number; list_value_usd: number;
  price_source: "custom" | "list" | "free" | "local" | "free_model" | "unknown"; billing: "free" | "paid"; price: Price | null;
  first_used: string; last_used: string; tasks: string[];
};
type Group = { key: string; label: string; calls: number; failures: number; input_tokens: number; output_tokens: number; units: number;
  cost_usd: number; list_value_usd: number; models: number; billing?: string; provider_id?: string | null };
type Day = { day: string; calls: number; tokens: number; cost_usd: number; list_value_usd: number };
type UsageData = {
  totals: { calls: number; failures: number; input_tokens: number; output_tokens: number; images: number; audio_seconds: number;
    videos: number; cost_usd: number; list_value_usd: number; unpriced_models: number };
  models: UsageRow[]; by_provider: Group[]; by_kind: Group[]; daily: Day[];
  price_list: { source: string; updated_at: number | null };
};
type Status = { provider_id: string; model: string; state: string; label: string; reason: string | null };

// ------------------------------------------------------------------ formatting
export const KIND_SHORT: Record<string, string> = { chat: "Text", embed: "Embeddings", image: "Images", tts: "Voice", video: "Video" };
const TASK_LABEL: Record<string, string> = {
  write: "writing", plan: "planning", qa: "reviews", extract: "reading", longform: "articles", image_prompt: "image prompts",
  embed: "embeddings", image: "images", tts: "voice-over", video: "video", carousel: "carousels", research: "research", aivis: "AI visibility",
};
const SOURCE: Record<string, { label: string; hint: string }> = {
  custom: { label: "Your price", hint: "Uses the price you set on this integration." },
  list: { label: "List price", hint: "Public list price for this model." },
  free: { label: "Free tier", hint: "This integration is marked as free tier, so you pay nothing. 'List value' shows what it would cost on a paid plan." },
  local: { label: "Local", hint: "Runs on your own computer: no charge." },
  free_model: { label: "Free model", hint: "A free model (':free' on OpenRouter)." },
  unknown: { label: "No price", hint: "This model isn't on the public price list. Set your own price on the integration to count its cost." },
};
const num = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}k` : n.toLocaleString("en-IN"));
export const usd = (n: number) => (n === 0 ? "$0" : n < 0.0001 ? "< $0.0001" : n < 0.01 ? `$${n.toFixed(4)}` : n < 100 ? `$${n.toFixed(2)}` : `$${Math.round(n).toLocaleString("en-US")}`);
const ago = (iso?: string | null) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
function amount(r: { kind?: string; input_tokens: number; output_tokens: number; units: number }) {
  if (r.kind === "image") return `${r.units.toLocaleString()} image${r.units === 1 ? "" : "s"}`;
  if (r.kind === "tts") return `${Math.floor(r.units / 60)}m ${r.units % 60}s audio`;
  if (r.kind === "video") return `${r.units} clip${r.units === 1 ? "" : "s"}`;
  if (r.kind === "embed") return `${num(r.input_tokens)} tokens`;
  return `${num(r.input_tokens + r.output_tokens)} tokens`;
}
function priceText(p: Price | null, kind: string) {
  if (!p) return null;
  if (kind === "image" && p.per_image) return `${usd(p.per_image)} per image`;
  if (kind === "tts" && (p.per_second || p.per_million_chars)) return p.per_second ? `${usd(p.per_second * 60)} per minute` : `$${p.per_million_chars} per 1M characters`;
  if (kind === "video" && p.per_second) return `${usd(p.per_second)} per second`;
  if (p.input_per_million != null) return `$${p.input_per_million} in / $${p.output_per_million ?? 0} out per 1M tokens`;
  return null;
}

// ------------------------------------------------------------------ period
const PERIODS = [
  { id: "1", label: "Today" }, { id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }, { id: "month", label: "This month" },
  { id: "90", label: "Last 90 days" }, { id: "365", label: "Last 12 months" }, { id: "0", label: "All time" }, { id: "custom", label: "Custom dates" },
];
function query(period: string, from: string, to: string) {
  if (period === "month") {
    const d = new Date();
    return `start=${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
  }
  if (period === "custom") return from ? `start=${from}${to ? `&end=${to}` : ""}` : "days=30";
  return `days=${period}`;
}

// ------------------------------------------------------------------ daily chart (one series, one axis)
function DailyChart({ days, metric }: { days: Day[]; metric: "tokens" | "cost_usd" | "calls" }) {
  const [hover, setHover] = useState<number | null>(null);
  if (days.length === 0) return <p className="py-8 text-center text-sm text-muted">No AI calls in this period.</p>;
  const vals = days.map((d) => d[metric]);
  const max = Math.max(...vals, metric === "cost_usd" ? 0.01 : 1);
  const fmt = (v: number) => (metric === "cost_usd" ? usd(v) : num(v));
  const label = (d: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(`${d}T00:00:00`));
  const h = days[hover ?? -1];
  return (
    <div className="relative">
      <div className="mb-1 flex justify-between text-xs text-muted"><span>{fmt(max)}</span>
        {h && <span className="font-medium text-ink">{label(h.day)}: {fmt(h[metric])}{metric !== "calls" ? ` · ${h.calls} calls` : ""}</span>}</div>
      <div className="flex h-40 items-end gap-[2px] border-b border-line" role="img"
        aria-label={`Daily ${metric === "cost_usd" ? "cost" : metric}, ${days.length} days`} onMouseLeave={() => setHover(null)}>
        {days.map((d, i) => (
          <div key={d.day} className="flex h-full min-w-0 max-w-[36px] flex-1 items-end" onMouseEnter={() => setHover(i)}>
            <div className={`w-full rounded-t-[4px] ${hover === i ? "bg-primary-800" : "bg-primary-600"}`}
              style={{ height: `${Math.max(d[metric] > 0 ? 2 : 0, (d[metric] / max) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted"><span>{label(days[0].day)}</span><span>{label(days[days.length - 1].day)}</span></div>
    </div>
  );
}

function csv(rows: UsageRow[]) {
  const head = ["Provider", "Integration", "Model", "Type", "Calls", "Failed", "Input tokens", "Output tokens", "Images / seconds / clips",
    "Cost (USD)", "List value (USD)", "Price basis", "Used for", "First used", "Last used"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = rows.map((r) => [r.provider_label, r.integration, r.model, KIND_SHORT[r.kind] ?? r.kind, r.calls, r.failures, r.input_tokens,
    r.output_tokens, r.units, r.cost_usd, r.list_value_usd, SOURCE[r.price_source]?.label, r.tasks.join("; "), r.first_used, r.last_used].map(esc).join(","));
  const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `ai-usage-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ------------------------------------------------------------------ the report
export function UsageReport() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [period, setPeriod] = useState("30");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kind, setKind] = useState("");
  const [provider, setProvider] = useState("");
  const [search, setSearch] = useState("");
  const [metric, setMetric] = useState<"tokens" | "cost_usd" | "calls">("tokens");
  const qs = query(period, from, to);
  const q = useQuery({ queryKey: ["int-usage", qs], queryFn: () => api<UsageData>(`/integrations/usage?${qs}`) });
  const status = useQuery({ queryKey: ["int-status"], queryFn: () => api<{ statuses: Status[] }>("/integrations/model-status"), refetchInterval: 60000 });
  const refreshPrices = useMutation({
    mutationFn: () => api<{ message: string }>("/integrations/prices/refresh", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["int-usage"] }),
  });
  const d = q.data;
  const rows = useMemo(() => (d?.models ?? []).filter((r) =>
    (!kind || r.kind === kind) && (!provider || String(r.provider_id ?? r.provider) === provider)
    && (!search || `${r.model} ${r.integration} ${r.provider_label}`.toLowerCase().includes(search.toLowerCase()))
  ).sort((a, b) => b.cost_usd - a.cost_usd || b.list_value_usd - a.list_value_usd || (b.input_tokens + b.output_tokens) - (a.input_tokens + a.output_tokens)), [d, kind, provider, search]);
  const t = d?.totals;
  const sum = rows.reduce((s, r) => ({ cost: s.cost + r.cost_usd, list: s.list + r.list_value_usd, tin: s.tin + (r.kind === "chat" || r.kind === "embed" ? r.input_tokens : 0),
    tout: s.tout + (r.kind === "chat" ? r.output_tokens : 0), calls: s.calls + r.calls }), { cost: 0, list: 0, tin: 0, tout: 0, calls: 0 });
  const filtered = !!(kind || provider || search);
  const saved = (t?.list_value_usd ?? 0) - (t?.cost_usd ?? 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Period" className="w-44" value={period} onChange={(e) => setPeriod(e.target.value)}>
          {PERIODS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </Select>
        {period === "custom" && <>
          <Input type="date" aria-label="From" className="w-40" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Input type="date" aria-label="To" className="w-40" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </>}
        <Select aria-label="Type" className="w-40" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All types</option>
          {Object.entries(KIND_SHORT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </Select>
        <Select aria-label="Provider" className="w-52" value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="">All providers</option>
          {(d?.by_provider ?? []).map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
        </Select>
        <Input aria-label="Search models" placeholder="Search models" className="w-48" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={() => csv(rows)} disabled={!rows.length}><Download size={15} />Export CSV</Button>
          {can("integration.manage") && <Button variant="ghost" onClick={() => refreshPrices.mutate()} disabled={refreshPrices.isPending}
            title="Download the latest public AI price list (it also updates every day)"><RefreshCw size={15} />{refreshPrices.isPending ? "Updating…" : "Update prices"}</Button>}
        </div>
      </div>
      {refreshPrices.data && <Alert tone="info">{refreshPrices.data.message}</Alert>}
      {(q.error || refreshPrices.error) && <Alert>{((q.error || refreshPrices.error) as Error).message}</Alert>}
      {q.isLoading ? <Spinner label="Loading usage…" /> : !d || d.models.length === 0 ? (
        <EmptyState title="No AI usage in this period" body="Every AI call is counted here: text, embeddings, images, voice-over and video." />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Card>
              <div className="text-sm text-muted">Tokens used{filtered ? " (filtered)" : ""}</div>
              <div className="text-2xl font-bold tabular-nums">{num(filtered ? sum.tin + sum.tout : t!.input_tokens + t!.output_tokens)}</div>
              <div className="text-xs text-muted">{num(filtered ? sum.tin : t!.input_tokens)} in · {num(filtered ? sum.tout : t!.output_tokens)} out</div>
            </Card>
            <Card>
              <div className="text-sm text-muted">AI calls</div>
              <div className="text-2xl font-bold tabular-nums">{num(filtered ? sum.calls : t!.calls)}</div>
              {!!t!.failures && !filtered && <div className="text-xs text-danger">{t!.failures} failed and fell back</div>}
            </Card>
            <Card>
              <div className="text-sm text-muted">Cost{filtered ? " (filtered)" : ""}</div>
              <div className="text-2xl font-bold tabular-nums">{usd(filtered ? sum.cost : t!.cost_usd)}</div>
              <div className="text-xs text-muted">What you pay, after free tiers and local models</div>
            </Card>
            <Card>
              <div className="text-sm text-muted">Value at list price</div>
              <div className="text-2xl font-bold tabular-nums">{usd(filtered ? sum.list : t!.list_value_usd)}</div>
              <div className="text-xs text-muted">{!filtered && saved > 0.005 ? `${usd(saved)} saved by free tiers and local models` : "Same models on a paid plan"}</div>
            </Card>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
            {Object.keys(KIND_SHORT).map((k) => {
              const g = d.by_kind.find((x) => x.key === k);
              return (
                <button key={k} type="button" onClick={() => setKind(kind === k ? "" : k)} aria-pressed={kind === k}
                  className={`rounded-xl border bg-surface p-3 text-left transition-colors ${kind === k ? "border-primary-500 ring-2 ring-primary-500/20" : "border-line hover:border-ink/25"}`}>
                  <div className="text-xs font-medium text-muted">{KIND_SHORT[k]}</div>
                  <div className="text-lg font-semibold tabular-nums">{g ? amount({ kind: k, ...g }) : "—"}</div>
                  <div className="text-xs text-muted">{g ? `${usd(g.cost_usd)} · ${g.models} model${g.models === 1 ? "" : "s"}` : "Not used"}</div>
                </button>
              );
            })}
          </div>

          <Card>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold">Per day</h3>
              <div className="flex gap-1" role="tablist" aria-label="Chart measure">
                {([["tokens", "Tokens"], ["cost_usd", "Cost"], ["calls", "Calls"]] as const).map(([m, l]) => (
                  <button key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium ${metric === m ? "border-ink/20 bg-ink/5 text-ink" : "border-line text-muted hover:text-ink"}`}>{l}</button>
                ))}
              </div>
            </div>
            <DailyChart days={d.daily} metric={metric} />
          </Card>

          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="border-b border-line bg-canvas text-left text-xs text-muted"><tr>
                <th className="px-4 py-2.5 font-medium">Provider</th><th className="px-4 py-2.5 font-medium">Billing</th>
                <th className="px-4 py-2.5 text-right font-medium">Models</th><th className="px-4 py-2.5 text-right font-medium">Calls</th>
                <th className="px-4 py-2.5 text-right font-medium">Tokens</th><th className="px-4 py-2.5 text-right font-medium">Media</th>
                <th className="px-4 py-2.5 text-right font-medium">Cost</th><th className="px-4 py-2.5 text-right font-medium">List value</th></tr></thead>
              <tbody className="divide-y divide-line">
                {d.by_provider.map((g) => {
                  const mine = d.models.filter((r) => String(r.provider_id ?? r.provider) === g.key);
                  const media = [
                    ["image", "images"], ["tts", "s voice"], ["video", "clips"],
                  ].map(([k, l]) => [mine.filter((r) => r.kind === k).reduce((s, r) => s + r.units, 0), l] as const).filter(([n]) => n > 0)
                    .map(([n, l]) => `${n}${l.startsWith("s") ? "" : " "}${l}`).join(", ");
                  return (
                    <tr key={g.key} className={`cursor-pointer hover:bg-canvas ${provider === g.key ? "bg-brand-tint" : ""}`}
                      onClick={() => setProvider(provider === g.key ? "" : g.key)}>
                      <td className="px-4 py-2.5 font-medium">{g.label}</td>
                      <td className="px-4 py-2.5">{mine.some((r) => r.price_source === "local") ? <Badge>Local</Badge> : g.billing === "free" ? <Badge tone="accent">Free tier</Badge> : <Badge tone="brand">Paid</Badge>}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{g.models}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{num(g.calls)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{num(g.input_tokens + g.output_tokens)}</td>
                      <td className="px-4 py-2.5 text-right text-muted">{media || "—"}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-right font-medium tabular-nums">{usd(g.cost_usd)}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-muted">{usd(g.list_value_usd)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="overflow-x-auto rounded-xl border border-line bg-surface">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="border-b border-line bg-canvas text-left text-xs text-muted"><tr>
                <th className="px-4 py-2.5 font-medium">Model</th><th className="px-4 py-2.5 font-medium">Used for</th>
                <th className="px-4 py-2.5 text-right font-medium">Calls</th><th className="px-4 py-2.5 text-right font-medium">Input</th>
                <th className="px-4 py-2.5 text-right font-medium">Output</th><th className="px-4 py-2.5 text-right font-medium">Cost</th>
                <th className="px-4 py-2.5 font-medium">Price</th><th className="px-4 py-2.5 font-medium">Last used</th></tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => {
                  const s = status.data?.statuses.find((x) => x.provider_id === r.provider_id && x.model === r.model && x.state !== "ok");
                  const tokens = r.kind === "chat" || r.kind === "embed";
                  return (
                    <tr key={`${r.provider_id}-${r.model}-${r.kind}`} className="align-top">
                      <td className="max-w-[280px] px-4 py-2.5">
                        <div className="font-medium [overflow-wrap:anywhere]">{r.model}</div>
                        <div className="text-xs text-muted">{r.integration ?? r.provider_label} · {KIND_SHORT[r.kind] ?? r.kind}</div>
                        {s && <div className="mt-0.5"><Badge tone={s.state === "cooldown" ? "accent" : "danger"}>{s.reason ?? s.label}</Badge></div>}
                      </td>
                      <td className="max-w-[180px] px-4 py-2.5 text-muted">{r.tasks.map((x) => TASK_LABEL[x] ?? x).join(", ")}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{num(r.calls)}{r.failures ? <div className="text-xs text-danger">{r.failures} failed</div> : null}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{tokens ? num(r.input_tokens) : "—"}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{r.kind === "chat" ? num(r.output_tokens) : tokens ? "—" : amount(r)}</td>
                      <td className="px-4 py-2.5 whitespace-nowrap text-right tabular-nums">
                        <div className="font-medium">{usd(r.cost_usd)}</div>
                        {r.list_value_usd > r.cost_usd && <div className="text-xs text-muted">{usd(r.list_value_usd)} list</div>}
                      </td>
                      <td className="px-4 py-2.5 text-xs">
                        <span title={SOURCE[r.price_source]?.hint}><Badge tone={r.price_source === "unknown" ? "danger" : r.price_source === "list" || r.price_source === "custom" ? "brand" : "neutral"}>{SOURCE[r.price_source]?.label}</Badge></span>
                        {priceText(r.price, r.kind) && <div className="mt-0.5 text-muted">{priceText(r.price, r.kind)}</div>}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-muted">{ago(r.last_used)}</td>
                    </tr>
                  );
                })}
                {rows.length === 0 && <tr><td colSpan={8} className="px-4 py-6 text-muted">No models match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted">
            Costs are worked out from the tokens, images, seconds of voice and clips recorded for each call, at today's prices: your own price on
            the integration first, then free tier or local (no charge), then the public list price
            {d.price_list.updated_at ? ` (updated ${new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date(d.price_list.updated_at * 1000))})` : ""}.
            Mark an integration as free tier or paid, or set your own prices, in Settings → Integrations → Edit.
            {t!.unpriced_models > 0 && ` ${t!.unpriced_models} model${t!.unpriced_models === 1 ? " has" : "s have"} no public price.`}
          </p>
        </>
      )}
    </div>
  );
}

export default function AiUsage() {
  return (
    <div>
      <PageHeader title="AI usage and costs" description="Tokens, images, voice-over and video used by every model and provider, and what they cost." />
      <UsageReport />
    </div>
  );
}
