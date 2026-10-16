import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowDown, ArrowUp, ExternalLink, Plus, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea, Toggle, formatDate,
} from "../components/ui";

export type Spec = {
  id: string; kind: string; label: string; style: string; base_url: string; key: boolean; free: string; docs: string;
  examples: string[]; local: boolean; fields: { name: string; label: string; secret: boolean }[];
};
export type ProviderCfg = {
  id: string; kind: string; provider: string; name: string; label: string; base_url: string;
  secrets: Record<string, string>; fields: Record<string, string>; settings: Record<string, any>; enabled: boolean;
  monthly_budget_usd: number | null; month_spend_usd: number | null; last_test_ok: boolean | null;
  last_test_at: string | null; last_test_message: string | null;
};
type RouteTask = { label: string; kind: string; model_type: string };
type Catalog = { providers: Spec[]; tasks: Record<string, string>; media_tasks: Record<string, string>; route_tasks: Record<string, RouteTask> };
type ModelList = { models: string[]; types: Record<string, string>; source?: string | null; synced_at?: string | null; error?: string | null; missing?: string[] };
type Status = { provider_id: string; model: string; state: string; label: string; reason: string | null; until: string | null; last_error: string | null };
type Step = { provider_id: string; model: string };

const AI_KINDS = ["llm", "image", "tts", "video"];
const GROUPS: { id: string; label: string; kinds: string[]; blurb: string }[] = [
  { id: "ai", label: "Text models", kinds: ["llm"], blurb: "Models that plan, write and review posts and articles, and make embeddings. Add a local model, a free cloud tier, or a paid API. Model lists refresh automatically every 6 hours; Task routing decides which model does what and what to fall back to." },
  { id: "media", label: "Images, video, voice", kinds: ["image", "video", "tts", "stock"], blurb: "Where images, AI video clips, voice-overs and stock photos come from. Add several and order them in Task routing; if one runs out of credits or hits a limit, the next is used." },
  { id: "web", label: "Search, hosting", kinds: ["search", "media_host"], blurb: "Web search finds competitors. Instagram, Threads and Google Business Profile fetch images from a public URL, so a media host is needed for them." },
  { id: "apps", label: "Social apps", kinds: ["social_app", "google"], blurb: "Your developer app credentials. They let you connect accounts on the Social accounts page." },
  { id: "notify", label: "Email, Telegram", kinds: ["smtp", "telegram"], blurb: "How notifications leave the app. Each person chooses what they receive in Notifications." },
];
const KIND_LABEL: Record<string, string> = {
  llm: "Text model", image: "Images", video: "AI video", tts: "Voice-over", stock: "Stock media", search: "Web search",
  media_host: "Media hosting", social_app: "Social app", google: "Google", smtp: "Email", telegram: "Telegram",
};
const TYPE_LABEL: Record<string, string> = {
  chat: "Text", embed: "Embeddings", image: "Image", voice: "Voice", tts_model: "Speech model", video: "Video", other: "Other",
};

function ago(iso?: string | null) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function until(iso: string | null) {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }).format(new Date(iso));
}
const fmtNum = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}k` : n.toLocaleString();

function useCatalog() {
  return useQuery({ queryKey: ["int-catalog"], queryFn: () => api<Catalog>("/integrations/catalog"), staleTime: Infinity });
}
function useStatuses() {
  return useQuery({ queryKey: ["int-status"], queryFn: () => api<{ statuses: Status[] }>("/integrations/model-status"), refetchInterval: 60000 });
}

// ------------------------------------------------------------------ model picker
function ModelPicker({ value, onChange, list, want, placeholder, className = "", sourceLabel }: {
  value: string; onChange: (v: string) => void; list: ModelList | undefined; want?: string[]; placeholder?: string;
  className?: string; sourceLabel?: string;
}) {
  const all = list?.models ?? [];
  const filtered = want ? all.filter((m) => want.includes(list?.types?.[m] ?? "chat")) : all;
  const options = filtered.length ? filtered : all;
  const notListed = !!value && options.length > 0 && !options.includes(value);
  const wrongType = notListed && all.includes(value) ? (list?.types?.[value] ?? "chat") : null;
  const wantLabel = want?.length === 1 ? (TYPE_LABEL[want[0]] ?? want[0]).toLowerCase() : "the right kind of";
  const [typing, setTyping] = useState(false);
  useEffect(() => { if (notListed) setTyping(true); }, [notListed]);

  if (!options.length || typing) {
    return (
      <div className={className}>
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder ?? "model name"} />
        {options.length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs">
            {notListed && (
              <span className="inline-flex items-center gap-1 text-amber-700">
                <AlertTriangle size={12} />
                {wrongType ? `This is a ${(TYPE_LABEL[wrongType] ?? wrongType).toLowerCase()} model, not ${wantLabel === "embeddings" ? "an embedding" : `a ${wantLabel}`} model` : `Not in ${sourceLabel ?? "the provider"}'s current list`}
              </span>
            )}
            <button type="button" className="text-primary-700 hover:underline" onClick={() => { setTyping(false); if (notListed) onChange(""); }}>
              Choose from {options.length} available
            </button>
          </div>
        )}
      </div>
    );
  }
  return (
    <Select className={className} value={value} onChange={(e) => e.target.value === "__type" ? setTyping(true) : onChange(e.target.value)}>
      <option value="">Choose…</option>
      {options.map((m) => <option key={m} value={m}>{m}{!want && list?.types?.[m] && list.types[m] !== "chat" ? ` (${TYPE_LABEL[list.types[m]] ?? list.types[m]})` : ""}</option>)}
      <option value="__type">Type another name…</option>
    </Select>
  );
}

// ------------------------------------------------------------------ add / edit
function ProviderForm({ spec, existing, onClose }: { spec: Spec; existing?: ProviderCfg; onClose: () => void }) {
  const qc = useQueryClient();
  const st = existing?.settings ?? {};
  const [name, setName] = useState(existing?.name ?? spec.label);
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? spec.base_url);
  const [secrets, setSecrets] = useState<Record<string, string>>({ ...(existing?.fields ?? {}) });
  const [defaultModel, setDefaultModel] = useState<string>(st.default_model ?? "");
  const [embedModel, setEmbedModel] = useState<string>(st.embed_model ?? "");
  const [voice, setVoice] = useState<string>(st.voice ?? "");
  const [budget, setBudget] = useState(existing?.monthly_budget_usd?.toString() ?? "");
  const [priceIn, setPriceIn] = useState<string>(st.prices?.["*"]?.[0]?.toString() ?? "");
  const [priceOut, setPriceOut] = useState<string>(st.prices?.["*"]?.[1]?.toString() ?? "");
  const unitKey = spec.kind === "image" ? "image" : spec.kind === "tts" ? "minute" : spec.kind === "video" ? "video" : "";
  const [unitPrice, setUnitPrice] = useState<string>(unitKey ? (st.prices?.[unitKey]?.toString() ?? "") : "");
  const isAI = AI_KINDS.includes(spec.kind);

  const stored = useQuery({
    queryKey: ["models", existing?.id],
    queryFn: () => api<ModelList>(`/integrations/providers/${existing!.id}/models`),
    enabled: !!existing && isAI, retry: false,
  });
  const live = useMutation({
    mutationFn: () => existing && !Object.values(secrets).some((v) => v && !v.startsWith("••")) && baseUrl === existing.base_url
      ? api<ModelList>(`/integrations/providers/${existing.id}/models?refresh=true`)
      : api<ModelList>("/integrations/discover", { method: "POST", json: { provider: spec.id, base_url: baseUrl || null, secrets, provider_id: existing?.id ?? null } }),
  });
  // Local and key-less providers can list models straight away.
  useEffect(() => { if (!existing && isAI && !spec.key) live.mutate(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const list: ModelList | undefined = live.data ?? stored.data ?? (spec.examples.length ? { models: spec.examples, types: {}, source: "suggested" } : undefined);

  const save = useMutation({
    mutationFn: () => {
      const settings: Record<string, unknown> = {};
      if (spec.kind === "llm") {
        settings.default_model = defaultModel || null;
        settings.embed_model = embedModel || null;
        if (priceIn || priceOut) settings.prices = { ...(st.prices ?? {}), "*": [Number(priceIn || 0), Number(priceOut || 0)] };
      } else if (isAI) {
        settings.default_model = defaultModel || null;
        if (spec.id === "openai_tts") settings.voice = voice || null;
        if (unitKey) settings.prices = { ...(st.prices ?? {}), [unitKey]: unitPrice ? Number(unitPrice) : 0 };
      } else if (defaultModel) settings.default_model = defaultModel;
      const body = {
        name, base_url: spec.base_url || baseUrl ? baseUrl : null, secrets, settings,
        monthly_budget_usd: budget ? Number(budget) : null,
      };
      return existing
        ? api(`/integrations/providers/${existing.id}`, { method: "PATCH", json: body })
        : api("/integrations/providers", { method: "POST", json: { ...body, provider: spec.id } });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["int-providers"] });
      qc.invalidateQueries({ queryKey: ["models"] });
      qc.invalidateQueries({ queryKey: ["int-status"] });
      onClose();
    },
  });
  const set = (k: string) => (e: { target: { value: string } }) => setSecrets({ ...secrets, [k]: e.target.value });
  const mainWant = spec.kind === "llm" ? ["chat"] : spec.kind === "image" ? ["image"] : spec.id === "openai_tts" ? ["tts_model"] : spec.kind === "tts" ? ["voice"] : undefined;
  const mainLabel = spec.kind === "llm" ? "Default model for writing" : spec.id === "openai_tts" ? "Speech model" : spec.kind === "tts" ? "Voice" : "Model";
  const unitLabel = spec.kind === "image" ? "Price per image (USD)" : spec.kind === "tts" ? "Price per minute of audio (USD)" : "Price per clip (USD)";

  return (
    <Modal title={existing ? `Edit ${existing.name}` : `Add ${spec.label}`} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <p className="text-sm text-muted">
          {spec.free}{spec.docs && <> · <a className="text-primary-700 underline" href={spec.docs} target="_blank" rel="noreferrer">Get credentials <ExternalLink size={12} className="inline" /></a></>}
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name"><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
          {(spec.base_url || spec.id === "custom_openai") && (
            <Field label="Address (base URL)"><Input required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></Field>
          )}
          {spec.key && (
            <Field label="API key" hint={existing?.secrets.api_key ? `Saved (${existing.secrets.api_key}). Leave empty to keep it.` : undefined}>
              <Input type="password" autoComplete="off" required={!existing} value={secrets.api_key ?? ""} onChange={set("api_key")} />
            </Field>
          )}
          {spec.fields.map((f) => f.name === "workflow_json" ? null : (
            <Field key={f.name} label={f.label} hint={f.secret && existing?.secrets[f.name] ? `Saved (${existing.secrets[f.name]}). Leave empty to keep it.` : undefined}>
              <Input type={f.secret ? "password" : "text"} autoComplete="off" value={secrets[f.name] ?? ""} onChange={set(f.name)} />
            </Field>
          ))}
        </div>
        {spec.fields.some((f) => f.name === "workflow_json") && (
          <Field label="ComfyUI workflow (API format JSON)" hint="Use {{prompt}}, {{negative}}, {{width}}, {{height}} and {{seed}} where values should go.">
            <Textarea rows={6} className="font-mono text-xs" value={secrets.workflow_json ?? ""} onChange={set("workflow_json")} />
          </Field>
        )}

        {(isAI || spec.examples.length > 0) && (
          <div className="space-y-3 border-t border-line pt-4">
            {isAI && (
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-muted">
                  {live.isPending || stored.isFetching ? "Loading the provider's models…"
                    : list?.source === "live" ? `${list.models.length} models available${list.synced_at ? ` · updated ${ago(list.synced_at)}` : " now"}`
                    : list?.source === "suggested" ? "This provider has no model list; showing suggestions."
                    : spec.key && !existing ? "Enter the key, then load the models it can use." : ""}
                </span>
                <Button type="button" variant="secondary" onClick={() => live.mutate()} disabled={live.isPending || (spec.key && !existing && !secrets.api_key)}>
                  <RefreshCw size={14} className={live.isPending ? "animate-spin" : ""} /> {list?.models.length ? "Refresh list" : "Load models"}
                </Button>
              </div>
            )}
            {(live.error || list?.error) && <Alert tone="warn">Couldn't load the model list: {(live.error as Error)?.message ?? list?.error}</Alert>}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={mainLabel}>
                <ModelPicker value={defaultModel} onChange={setDefaultModel} list={list} want={mainWant} placeholder={spec.examples[0] ?? ""} sourceLabel={spec.label} />
              </Field>
              {spec.kind === "llm" && (
                <Field label="Embedding model (optional)" hint="Catches duplicate posts and searches your company knowledge.">
                  <ModelPicker value={embedModel} onChange={setEmbedModel} list={list} want={["embed"]} sourceLabel={spec.label} />
                </Field>
              )}
              {spec.id === "openai_tts" && (
                <Field label="Voice"><ModelPicker value={voice} onChange={setVoice} list={list} want={["voice"]} placeholder="alloy" /></Field>
              )}
            </div>
          </div>
        )}
        {isAI && !spec.local && (
          <div className="grid gap-4 sm:grid-cols-3">
            {spec.kind === "llm" ? (
              <>
                <Field label="Price per 1M input tokens (USD)"><Input inputMode="decimal" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="0" /></Field>
                <Field label="Price per 1M output tokens (USD)"><Input inputMode="decimal" value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="0" /></Field>
              </>
            ) : (
              <Field label={unitLabel}><Input inputMode="decimal" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} placeholder="0" /></Field>
            )}
            <Field label="Monthly budget (USD)" hint="Skipped for the rest of the month when reached."><Input inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="No limit" /></Field>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>{existing ? "Save changes" : "Add integration"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function StatusBadge({ s, onReset }: { s?: Status; onReset?: () => void }) {
  if (!s || s.state === "ok") return null;
  const tone = s.state === "cooldown" ? "accent" : "danger";
  return (
    <span className="inline-flex items-center gap-1.5" title={s.last_error ?? undefined}>
      <Badge tone={tone}>{s.reason ?? s.label}{s.state === "cooldown" && s.until ? ` · back ${until(s.until)}` : ""}</Badge>
      {onReset && <button type="button" onClick={onReset} className="text-xs text-primary-700 hover:underline" title="Try this model again now"><RotateCcw size={12} className="inline" /> Retry now</button>}
    </span>
  );
}

function ProviderRow({ cfg, spec, canManage, statuses }: { cfg: ProviderCfg; spec?: Spec; canManage: boolean; statuses: Status[] }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["int-providers"] }); qc.invalidateQueries({ queryKey: ["int-status"] }); };
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; message: string }>(`/integrations/providers/${cfg.id}/test`, { method: "POST" }), onSettled: refresh });
  const toggle = useMutation({ mutationFn: (enabled: boolean) => api(`/integrations/providers/${cfg.id}`, { method: "PATCH", json: { enabled } }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api(`/integrations/providers/${cfg.id}`, { method: "DELETE" }), onSuccess: refresh });
  const reset = useMutation({ mutationFn: (model: string) => api("/integrations/model-status/reset", { method: "POST", json: { provider_id: cfg.id, model } }), onSuccess: refresh });
  const result = test.data ?? (cfg.last_test_message ? { ok: !!cfg.last_test_ok, message: cfg.last_test_message } : null);
  const st = cfg.settings;
  const mine = statuses.filter((s) => s.provider_id === cfg.id && s.state !== "ok");
  const missing: string[] = st.missing_models ?? [];
  const isAI = AI_KINDS.includes(cfg.kind);
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{cfg.name}</span>
            <Badge>{KIND_LABEL[cfg.kind] ?? cfg.kind}</Badge>
            {!cfg.enabled && <Badge tone="danger">Off</Badge>}
            {spec?.local && <Badge tone="accent">On this laptop</Badge>}
          </div>
          <div className="mt-0.5 text-sm text-muted">
            {cfg.label}
            {st.default_model && <> · {st.default_model}</>}
            {st.embed_model && <> · embeddings: {st.embed_model}</>}
            {st.voice && <> · voice: {st.voice}</>}
            {cfg.month_spend_usd !== null && cfg.month_spend_usd > 0 && <> · ${cfg.month_spend_usd.toFixed(2)} this month{cfg.monthly_budget_usd ? ` of $${cfg.monthly_budget_usd}` : ""}</>}
          </div>
          {isAI && (st.models_synced_at || st.models_error) && (
            <p className="mt-0.5 text-xs text-muted">
              {st.models_source === "suggested" ? "Suggested models (no list from the provider)" : `${(st.models ?? []).length} models available`}
              {st.models_synced_at && <> · list updated {ago(st.models_synced_at)}</>}
              {st.models_error && <span className="text-amber-700"> · last refresh failed: {String(st.models_error).slice(0, 120)}</span>}
            </p>
          )}
          {missing.length > 0 && (
            <p className="mt-1 flex items-start gap-1.5 text-sm text-amber-800">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>No longer offered: {missing.join(", ")}. Tasks fall back to the next model; pick a replacement in Edit or Task routing.</span>
            </p>
          )}
          {mine.length > 0 && (
            <ul className="mt-1.5 space-y-1 text-sm">
              {mine.map((s) => (
                <li key={s.model} className="flex flex-wrap items-center gap-2">
                  <code className="text-xs">{s.model}</code>
                  <StatusBadge s={s} onReset={canManage ? () => reset.mutate(s.model) : undefined} />
                </li>
              ))}
            </ul>
          )}
          {result && (
            <p className={`mt-1 text-sm ${result.ok ? "text-emerald-700" : "text-danger"}`}>
              {result.message}{cfg.last_test_at && !test.data && <span className="text-muted"> ({formatDate(cfg.last_test_at)})</span>}
            </p>
          )}
        </div>
        {canManage && (
          <div className="flex items-center gap-1">
            <Toggle checked={cfg.enabled} onChange={(v) => toggle.mutate(v)} label="" />
            <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>{test.isPending ? "Testing…" : "Test"}</Button>
            <Button variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
            <Button variant="ghost" aria-label={`Remove ${cfg.name}`} onClick={() => remove.mutate()}><Trash2 size={15} /></Button>
          </div>
        )}
      </div>
      {editing && spec && <ProviderForm spec={spec} existing={cfg} onClose={() => setEditing(false)} />}
    </li>
  );
}

function AddPicker({ specs, onPick, onClose }: { specs: Spec[]; onPick: (s: Spec) => void; onClose: () => void }) {
  const kinds = [...new Set(specs.map((s) => s.kind))];
  return (
    <Modal title="Add integration" onClose={onClose} wide>
      <div className="max-h-[60vh] space-y-5 overflow-y-auto">
        {kinds.map((k) => (
          <div key={k}>
            <div className="mb-2 text-sm font-semibold text-muted">{KIND_LABEL[k] ?? k}</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {specs.filter((s) => s.kind === k).map((s) => (
                <button key={s.id} onClick={() => onPick(s)} className="rounded-lg border border-line px-3 py-2.5 text-left hover:border-primary-400 hover:bg-primary-50">
                  <div className="font-medium">{s.label}</div>
                  <div className="text-xs text-muted">{s.free}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ routing
const DEFAULT_FOR: Record<string, (p: ProviderCfg) => string> = {
  embed: (p) => p.settings.embed_model ?? "",
  tts: (p) => p.provider === "openai_tts" ? (p.settings.voice ?? "alloy") : (p.settings.default_model ?? ""),
};

function Routing({ providers, catalog, statuses }: { providers: ProviderCfg[]; catalog: Catalog; statuses: Status[] }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const canManage = can("integration.manage");
  const q = useQuery({ queryKey: ["int-routes"], queryFn: () => api<{ routes: Record<string, Step[]> }>("/integrations/routes") });
  const [draft, setDraft] = useState<Record<string, Step[]> | null>(null);
  const routes = draft ?? q.data?.routes ?? {};
  const save = useMutation({
    mutationFn: () => api("/integrations/routes", { method: "PUT", json: { routes } }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["int-routes"] }); qc.invalidateQueries({ queryKey: ["int-providers"] }); },
  });
  const refreshAll = useMutation({
    mutationFn: () => api("/integrations/models/refresh", { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["int-providers"] }); qc.invalidateQueries({ queryKey: ["int-status"] }); },
  });
  const reset = useMutation({
    mutationFn: (s: Step) => api("/integrations/model-status/reset", { method: "POST", json: s }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["int-status"] }),
  });
  const update = (task: string, steps: Step[]) => setDraft({ ...routes, [task]: steps });
  const status = (s: Step) => statuses.find((x) => x.provider_id === s.provider_id && x.model === s.model);
  const listOf = (p?: ProviderCfg): ModelList | undefined => p ? { models: p.settings.models ?? [], types: p.settings.model_types ?? {}, source: p.settings.models_source } : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-muted">
          For each task, list the models to try in order. When a model fails, runs out of credits or hits a rate or daily limit,
          the next one is used straight away, and the limited model rests until its limit resets. Model lists come from each
          provider and refresh every 6 hours.
        </p>
        {canManage && (
          <Button variant="secondary" onClick={() => refreshAll.mutate()} disabled={refreshAll.isPending}>
            <RefreshCw size={14} className={refreshAll.isPending ? "animate-spin" : ""} /> {refreshAll.isPending ? "Refreshing…" : "Refresh model lists"}
          </Button>
        )}
      </div>
      {save.error && <Alert>{(save.error as Error).message}</Alert>}
      {refreshAll.error && <Alert>{(refreshAll.error as Error).message}</Alert>}
      {Object.entries(catalog.route_tasks).map(([task, rt]) => {
        const steps = routes[task] ?? [];
        const pool = providers.filter((p) => p.kind === rt.kind && p.enabled);
        const want = rt.model_type === "voice" ? ["voice"] : [rt.model_type];
        return (
          <Card key={task}>
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <span className="font-semibold">{rt.label}</span>
              <span className="text-xs text-muted">{KIND_LABEL[rt.kind]}</span>
            </div>
            {!pool.length ? (
              <p className="text-sm text-muted">Add a {KIND_LABEL[rt.kind].toLowerCase()} integration first.{rt.kind === "tts" ? " Free Edge voices are used until then." : ""}</p>
            ) : (
              <>
                {steps.length === 0 && (
                  <p className="text-sm text-muted">
                    {rt.kind === "llm" ? "Uses each text model's default, cloud models first." : rt.kind === "tts" ? "Uses your voice integrations in the order added, then free Edge voices." : "Uses your integrations in the order added."}
                  </p>
                )}
                <ol className="space-y-2">
                  {steps.map((s, i) => {
                    const p = providers.find((x) => x.id === s.provider_id);
                    return (
                      <li key={i} className="flex flex-wrap items-start gap-2">
                        <span className="mt-2.5 w-6 text-sm text-muted">{i + 1}.</span>
                        <Select className="w-56" value={s.provider_id} disabled={!canManage} onChange={(e) => {
                          const np = providers.find((x) => x.id === e.target.value);
                          const n = [...steps]; n[i] = { provider_id: e.target.value, model: np ? (DEFAULT_FOR[task]?.(np) ?? np.settings.default_model ?? "") : "" }; update(task, n);
                        }}>
                          {pool.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                        </Select>
                        <ModelPicker className="w-72" value={s.model} list={listOf(p)} want={want} sourceLabel={p?.name}
                          onChange={(v) => { const n = [...steps]; n[i] = { ...s, model: v }; update(task, n); }} />
                        {canManage && (
                          <div className="ml-auto flex">
                            <Button variant="ghost" aria-label="Move up" disabled={i === 0} onClick={() => { const n = [...steps]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; update(task, n); }}><ArrowUp size={14} /></Button>
                            <Button variant="ghost" aria-label="Move down" disabled={i === steps.length - 1} onClick={() => { const n = [...steps]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; update(task, n); }}><ArrowDown size={14} /></Button>
                            <Button variant="ghost" aria-label="Remove" onClick={() => update(task, steps.filter((_, j) => j !== i))}><Trash2 size={14} /></Button>
                          </div>
                        )}
                        {status(s) && status(s)!.state !== "ok" && (
                          <div className="basis-full pl-8"><StatusBadge s={status(s)} onReset={canManage ? () => reset.mutate(s) : undefined} /></div>
                        )}
                      </li>
                    );
                  })}
                </ol>
                {canManage && (
                  <button className="mt-2 text-sm text-primary-700 hover:underline" onClick={() => {
                    const used = new Set(steps.map((x) => x.provider_id));
                    const p = pool.find((x) => !used.has(x.id)) ?? pool[0];
                    update(task, [...steps, { provider_id: p.id, model: DEFAULT_FOR[task]?.(p) ?? p.settings.default_model ?? "" }]);
                  }}>{steps.length ? "Add a fallback model" : "Set the order"}</button>
                )}
              </>
            )}
          </Card>
        );
      })}
      {draft && canManage && (
        <div className="sticky bottom-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDraft(null)}>Discard</Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>Save routing</Button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ usage
type UsageRow = {
  provider_id: string | null; integration: string | null; provider: string; model: string; kind: string; calls: number;
  failures: number; input_tokens: number; output_tokens: number; units: number; cost_usd: number; last_used: string; tasks: string[];
};
type UsageData = {
  totals: { calls: number; failures: number; input_tokens: number; output_tokens: number; images: number; audio_seconds: number; videos: number; cost_usd: number };
  models: UsageRow[];
};
const TASK_LABEL: Record<string, string> = {
  write: "writing", plan: "planning", qa: "reviews", extract: "reading", longform: "articles", image_prompt: "image prompts",
  embed: "embeddings", image: "images", tts: "voice-over", video: "video", carousel: "carousels",
};
const KIND_SHORT: Record<string, string> = { chat: "Text", embed: "Embeddings", image: "Image", tts: "Voice", video: "Video" };

function output(r: UsageRow) {
  if (r.kind === "image") return `${r.units} image${r.units === 1 ? "" : "s"}`;
  if (r.kind === "tts") return `${Math.floor(r.units / 60)}m ${r.units % 60}s audio`;
  if (r.kind === "video") return `${r.units} clip${r.units === 1 ? "" : "s"}`;
  return `${fmtNum(r.input_tokens)} in / ${fmtNum(r.output_tokens)} out`;
}

function Usage({ statuses }: { statuses: Status[] }) {
  const [days, setDays] = useState("30");
  const q = useQuery({ queryKey: ["int-usage", days], queryFn: () => api<UsageData>(`/integrations/usage?days=${days}`) });
  const [prompt, setPrompt] = useState("Write a two-line LinkedIn hook about AI-powered HR software.");
  const run = useMutation({ mutationFn: () => api<{ text: string; provider: string; model: string }>("/integrations/playground", { method: "POST", json: { task: "write", prompt } }) });
  const t = q.data?.totals;
  const periodLabel = days === "0" ? "since the start" : `last ${days} days`;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">Every AI call is counted per model: text tokens, embeddings, images, seconds of voice-over and video clips.</p>
        <Select className="w-44" value={days} onChange={(e) => setDays(e.target.value)}>
          <option value="1">Today</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option><option value="0">All time</option>
        </Select>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card><div className="text-sm text-muted">AI calls, {periodLabel}</div><div className="text-2xl font-bold tabular-nums">{fmtNum(t?.calls ?? 0)}</div>
          {!!t?.failures && <div className="text-xs text-danger">{t.failures} failed (fell back)</div>}</Card>
        <Card><div className="text-sm text-muted">Text tokens</div><div className="text-2xl font-bold tabular-nums">{fmtNum((t?.input_tokens ?? 0) + (t?.output_tokens ?? 0))}</div>
          <div className="text-xs text-muted">{fmtNum(t?.input_tokens ?? 0)} in · {fmtNum(t?.output_tokens ?? 0)} out</div></Card>
        <Card><div className="text-sm text-muted">Media made</div><div className="text-2xl font-bold tabular-nums">{t?.images ?? 0} <span className="text-base font-normal text-muted">images</span></div>
          <div className="text-xs text-muted">{Math.round((t?.audio_seconds ?? 0) / 60)} min voice-over · {t?.videos ?? 0} AI clips</div></Card>
        <Card><div className="text-sm text-muted">Spend</div><div className="text-2xl font-bold tabular-nums">${(t?.cost_usd ?? 0).toFixed(2)}</div>
          <div className="text-xs text-muted">From the prices set on each integration</div></Card>
      </div>
      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-muted"><tr>
            <th className="min-w-[200px] px-4 py-2.5 font-medium">Model</th><th className="px-4 py-2.5 font-medium">Used for</th>
            <th className="px-4 py-2.5 text-right font-medium">Calls</th><th className="px-4 py-2.5 text-right font-medium">Usage</th>
            <th className="px-4 py-2.5 text-right font-medium">Cost</th><th className="px-4 py-2.5 font-medium">Last used</th><th className="px-4 py-2.5 font-medium">Status</th></tr></thead>
          <tbody className="divide-y divide-line">
            {q.isLoading && <tr><td colSpan={7} className="px-4 py-6"><Spinner label="Loading usage…" /></td></tr>}
            {q.data?.models.map((r, i) => {
              const s = statuses.find((x) => x.provider_id === r.provider_id && x.model === r.model);
              return (
                <tr key={i} className="align-top">
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{r.model}</div>
                    <div className="text-xs text-muted">{r.integration ?? r.provider} · {KIND_SHORT[r.kind] ?? r.kind}</div>
                  </td>
                  <td className="px-4 py-2.5 text-muted">{r.tasks.map((x) => TASK_LABEL[x] ?? x).join(", ")}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{fmtNum(r.calls)}{r.failures ? <div className="text-xs text-danger">{r.failures} failed</div> : null}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{output(r)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">${r.cost_usd.toFixed(4)}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap text-muted">{ago(r.last_used)}</td>
                  <td className="px-4 py-2.5">{s && s.state !== "ok" ? <StatusBadge s={s} /> : <span className="text-xs text-emerald-700">Ready</span>}</td>
                </tr>
              );
            })}
            {q.data && !q.data.models.length && <tr><td colSpan={7} className="px-4 py-6 text-muted">No AI calls in this period.</td></tr>}
          </tbody>
        </table>
      </div>
      <Card>
        <h3 className="font-semibold">Try the writing model</h3>
        <p className="text-sm text-muted">Runs the 'Write social posts' route with its fallbacks and shows which model answered.</p>
        <div className="mt-3 space-y-3">
          <Textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          <Button onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? "Writing…" : "Run"}</Button>
          {run.error && <Alert>{(run.error as Error).message}</Alert>}
          {run.data && <div className="rounded-lg bg-primary-50 p-3 text-sm"><div className="mb-1 text-xs text-muted">{run.data.provider} · {run.data.model}</div><p className="whitespace-pre-wrap">{run.data.text}</p></div>}
        </div>
      </Card>
    </div>
  );
}

export default function Integrations() {
  const { can } = useAuth();
  const catalog = useCatalog();
  const providers = useQuery({ queryKey: ["int-providers"], queryFn: () => api<ProviderCfg[]>("/integrations/providers") });
  const statusQ = useStatuses();
  const statuses = statusQ.data?.statuses ?? [];
  const [tab, setTab] = useState("ai");
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState<Spec | null>(null);
  const specs = useMemo(() => Object.fromEntries((catalog.data?.providers ?? []).map((s) => [s.id, s])), [catalog.data]);
  const group = GROUPS.find((g) => g.id === tab);
  const visible = (providers.data ?? []).filter((p) => group?.kinds.includes(p.kind));
  const canManage = can("integration.manage");
  const attention = statuses.filter((s) => ["auth", "billing", "unavailable"].includes(s.state)).length;

  return (
    <div>
      <PageHeader
        title="Integrations"
        description="Connect AI models, media services, developer apps and notification channels. Keys are encrypted on this machine."
        action={canManage && group && <Button onClick={() => setPicking(true)}><Plus size={16} /> Add integration</Button>}
      />
      <Tabs value={tab} onChange={setTab} items={[
        ...GROUPS.map((g) => ({ id: g.id, label: g.label, count: (providers.data ?? []).filter((p) => g.kinds.includes(p.kind)).length })),
        { id: "routing", label: "Task routing" }, { id: "usage", label: "Usage" },
      ]} />
      {providers.error && <Alert>{(providers.error as Error).message}</Alert>}
      {attention > 0 && tab !== "usage" && (
        <div className="mb-4"><Alert tone="warn">{attention === 1 ? "One AI model needs" : `${attention} AI models need`} attention (credits, key or a removed model). Their tasks are using the next model in Task routing until it's fixed.</Alert></div>
      )}
      {group && (
        <>
          <p className="mb-4 max-w-3xl text-sm text-muted">{group.blurb}</p>
          {visible.length ? (
            <ul className="divide-y divide-line rounded-xl border border-line bg-surface px-5">
              {visible.map((p) => <ProviderRow key={p.id} cfg={p} spec={specs[p.provider]} canManage={canManage} statuses={statuses} />)}
            </ul>
          ) : (
            <EmptyState
              title="Nothing connected here yet"
              body={tab === "ai" ? "Start with Ollama on this laptop (free) or a free Gemini, Groq or Mistral key." : undefined}
              action={canManage && <Button onClick={() => setPicking(true)}><Plus size={16} /> Add integration</Button>}
            />
          )}
        </>
      )}
      {tab === "routing" && catalog.data && <Routing providers={providers.data ?? []} catalog={catalog.data} statuses={statuses} />}
      {tab === "usage" && <Usage statuses={statuses} />}
      {picking && group && catalog.data && (
        <AddPicker specs={catalog.data.providers.filter((s) => group.kinds.includes(s.kind))} onClose={() => setPicking(false)} onPick={(s) => { setPicking(false); setAdding(s); }} />
      )}
      {adding && <ProviderForm spec={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}
