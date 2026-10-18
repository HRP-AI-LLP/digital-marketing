import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Field, Input, Select, Spinner, Textarea, Toggle } from "../../components/ui";
import { CostBadge, PaidHints } from "./shared";
import type { AvailableEngine, EngineChoice, Settings } from "./types";

const lines = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

function EngineRow({ a, choice, onChange, disabled }: { a: AvailableEngine; choice?: EngineChoice; onChange: (c: EngineChoice | null) => void; disabled: boolean }) {
  const on = !!choice;
  return (
    <li className="rounded-lg border border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5">
          <input type="checkbox" className="h-4 w-4" checked={on} disabled={disabled}
            onChange={(e) => onChange(e.target.checked ? { provider_id: a.provider_id, model: a.suggested_model, web: a.web } : null)} />
          <span className="min-w-0">
            <span className="block text-sm font-medium [overflow-wrap:anywhere]">{a.brand}{a.brand !== a.name ? ` (${a.name})` : ""}</span>
            <span className="block text-xs text-muted">
              {a.provider === "websearch" ? "Searches the web for free (DuckDuckGo or your search keys); any free AI model then answers from the results."
                : a.always_web ? "Always searches the web and lists its sources." : a.web ? "Can search the web before answering." : "Answers from memory (no web search)."}
              {a.local ? " Runs on this computer." : ""}
            </span>
          </span>
        </label>
        <CostBadge cost={a.cost} />
      </div>
      {on && a.provider !== "websearch" && (
        <div className="mt-2 flex flex-wrap items-center gap-3 pl-6">
          <Select aria-label={`${a.brand} model`} value={choice!.model} disabled={disabled} className="w-full sm:w-64"
            onChange={(e) => onChange({ ...choice!, model: e.target.value })}>
            {[...new Set([choice!.model, ...a.models])].map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
          {a.web && !a.always_web && <Toggle checked={choice!.web} disabled={disabled} onChange={(v) => onChange({ ...choice!, web: v })} label="Use web search" />}
        </div>
      )}
    </li>
  );
}

export default function SettingsTab() {
  const { can } = useAuth();
  const canEdit = can("aivis.manage");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["aivis", "settings"], queryFn: () => api<Settings>("/aivis/settings") });
  const [s, setS] = useState<Settings | null>(null);
  const [aliases, setAliases] = useState("");
  const [domains, setDomains] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!q.data) return;
    setS(q.data);
    setAliases(q.data.aliases.join("\n"));
    setDomains(q.data.own_domains.join("\n"));
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => api<Settings>("/aivis/settings", {
      method: "PUT",
      json: {
        enabled: s!.enabled, weekday: s!.weekday, run_time: s!.run_time, drop_alert_points: s!.drop_alert_points, llm_analysis: s!.llm_analysis,
        engines: s!.engines_auto ? [] : s!.engines.map((e) => ({ provider_id: e.provider_id, model: e.model, web: e.web })),
        aliases: lines(aliases), own_domains: lines(domains),
      },
    }),
    onSuccess: (r) => { qc.setQueryData(["aivis", "settings"], r); qc.invalidateQueries({ queryKey: ["aivis", "overview"] }); setSaved(true); setTimeout(() => setSaved(false), 2500); },
  });

  if (q.isLoading || !s) return q.error ? <Alert>{(q.error as Error).message}</Alert> : <Spinner label="Loading settings…" />;
  const set = (patch: Partial<Settings>) => setS({ ...s, ...patch });
  const choose = (a: AvailableEngine, c: EngineChoice | null) => {
    const rest = s.engines.filter((e) => e.provider_id !== a.provider_id);
    set({ engines: c ? [...rest, c] : rest });
  };
  const free = s.available_engines.filter((a) => a.cost === "free");
  const paid = s.available_engines.filter((a) => a.cost === "paid");

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <h3 className="mb-1 font-semibold">Answer engines</h3>
        <p className="mb-3 text-sm text-muted">Each check asks every prompt once on each engine with a short answer, so costs stay low. Free engines are enough; paid ones are optional extras.</p>
        <Toggle checked={s.engines_auto} disabled={!canEdit} label="Choose automatically (free engines only)"
          onChange={(v) => set({ engines_auto: v, engines: v ? s.engines.filter((e) => e.cost !== "paid") : s.engines })} />
        {s.engines_auto ? (
          <ul className="mt-3 space-y-1 text-sm">
            {s.engines.map((e) => <li key={e.key} className="flex items-center justify-between gap-2"><span className="min-w-0 [overflow-wrap:anywhere]">{e.label}</span><CostBadge cost={e.cost} /></li>)}
          </ul>
        ) : (
          <div className="mt-3 space-y-4">
            <div>
              <div className="mb-1.5 text-xs font-semibold text-muted">Free</div>
              <ul className="space-y-2">{free.map((a) => <EngineRow key={a.provider_id} a={a} choice={s.engines.find((e) => e.provider_id === a.provider_id)} onChange={(c) => choose(a, c)} disabled={!canEdit} />)}</ul>
            </div>
            {paid.length > 0 && (
              <div>
                <div className="mb-1.5 text-xs font-semibold text-muted">Paid, optional (uses your own key and credits)</div>
                <ul className="space-y-2">{paid.map((a) => <EngineRow key={a.provider_id} a={a} choice={s.engines.find((e) => e.provider_id === a.provider_id)} onChange={(c) => choose(a, c)} disabled={!canEdit} />)}</ul>
              </div>
            )}
            {s.engines.length > 6 && <Alert tone="warn">Pick up to 6 engines.</Alert>}
          </div>
        )}
        <div className="mt-4"><PaidHints hints={s.paid_hints} /></div>
        <p className="mt-2 text-xs text-muted">Engines come from your <Link className="underline" to="/settings/integrations">Integrations</Link>. A free Gemini key adds Google-search-grounded answers at no cost.</p>
      </Card>

      <div className="space-y-5">
        <Card>
          <h3 className="mb-3 font-semibold">Schedule</h3>
          <Toggle checked={s.enabled} disabled={!canEdit} onChange={(v) => set({ enabled: v })} label="Check every week" />
          <div className="mt-3 grid grid-cols-2 gap-3">
            <Field label="Day">
              <Select value={s.weekday} disabled={!canEdit || !s.enabled} onChange={(e) => set({ weekday: Number(e.target.value) })}>
                {s.weekdays.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </Select>
            </Field>
            <Field label="Time"><Input type="time" value={s.run_time} disabled={!canEdit || !s.enabled} onChange={(e) => set({ run_time: e.target.value })} /></Field>
          </div>
          <div className="mt-3">
            <Field label="Alert me when visibility drops by" hint="Percentage points compared with the previous check. 0 turns alerts off.">
              <div className="flex items-center gap-2">
                <Input type="number" min={0} max={100} step={1} className="w-24" value={s.drop_alert_points} disabled={!canEdit}
                  onChange={(e) => set({ drop_alert_points: Number(e.target.value) })} />
                <span className="text-sm text-muted">points</span>
              </div>
            </Field>
          </div>
        </Card>
        <Card>
          <h3 className="mb-1 font-semibold">How we recognise you</h3>
          <p className="mb-3 text-sm text-muted">An answer counts as mentioning you when it names <strong>{s.brand.name}</strong>, one of these other names, or your website.</p>
          <Field label="Other names (one per line)" hint="Product names, short names or common spellings, e.g. your product brand.">
            <Textarea rows={3} value={aliases} disabled={!canEdit} onChange={(e) => setAliases(e.target.value)} />
          </Field>
          <div className="mt-3">
            <Field label="Extra website domains (one per line)" hint={`Already included: ${s.brand.domains.join(", ") || "none (add your website in Organization settings)"}`}>
              <Textarea rows={2} value={domains} disabled={!canEdit} onChange={(e) => setDomains(e.target.value)} />
            </Field>
          </div>
          <p className="mt-3 text-xs text-muted">Competitors tracked by name: {s.competitors.length ? s.competitors.join(", ") : "none yet"}. Manage them on <Link className="underline" to="/intelligence">Company intelligence</Link>.</p>
          <div className="mt-3"><Toggle checked={s.llm_analysis} disabled={!canEdit} onChange={(v) => set({ llm_analysis: v })} label="Use AI to spot other brands and tone (one short call per prompt)" /></div>
        </Card>
        {canEdit && (
          <div className="flex items-center gap-3">
            <Button onClick={() => save.mutate()} disabled={save.isPending || (!s.engines_auto && (!s.engines.length || s.engines.length > 6))}>Save settings</Button>
            {saved && <span className="text-sm text-emerald-700">Saved</span>}
            {save.error && <Alert>{(save.error as Error).message}</Alert>}
          </div>
        )}
      </div>
    </div>
  );
}
