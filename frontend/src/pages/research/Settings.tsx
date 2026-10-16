import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Field, Input, Select, Spinner, Textarea, Toggle } from "../../components/ui";
import { useContentAccounts } from "../content/shared";
import type { Providers, ResearchSettings, SettingsResponse } from "./types";

const lines = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export function ProviderNotice({ p }: { p: Providers }) {
  if (!p.ai) {
    return <Alert>No AI text model is set up, so research can't run. <Link className="underline" to="/settings/integrations">Add one in Integrations</Link> (local Ollama or a free Gemini or Groq key works).</Alert>;
  }
  if (p.search_fallback) {
    return (
      <Alert tone="info">
        Research is using free DuckDuckGo search, which is limited and often rate-limited. For deeper, fresher research add a free{" "}
        <strong>Brave Search</strong> or <strong>Tavily</strong> key in <Link className="underline" to="/settings/integrations">Integrations → Search</Link>.
        You can add more than one; results are combined.
      </Alert>
    );
  }
  return null;
}

function Check({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line px-3 py-2 hover:border-brand/40">
      <input type="checkbox" className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0"><span className="block text-sm font-medium">{label}</span>{hint && <span className="block text-xs text-muted">{hint}</span>}</span>
    </label>
  );
}

export default function SettingsPanel() {
  const { can } = useAuth();
  const canEdit = can("research.manage");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["research", "settings"], queryFn: () => api<SettingsResponse>("/research/settings") });
  const accounts = useContentAccounts();
  const [s, setS] = useState<ResearchSettings | null>(null);
  const [text, setText] = useState({ keywords: "", trusted: "", blocked: "", excluded: "" });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const d = q.data?.settings;
    if (!d) return;
    setS(d);
    setText({ keywords: d.keywords.join(", "), trusted: d.trusted_domains.join("\n"), blocked: d.blocked_domains.join("\n"), excluded: d.excluded_topics.join("\n") });
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => api<SettingsResponse>("/research/settings", {
      method: "PUT",
      json: { ...s, keywords: lines(text.keywords), trusted_domains: lines(text.trusted), blocked_domains: lines(text.blocked), excluded_topics: lines(text.excluded) },
    }),
    onSuccess: (r) => { qc.setQueryData(["research", "settings"], r); qc.invalidateQueries({ queryKey: ["research"] }); setSaved(true); setTimeout(() => setSaved(false), 2500); },
  });

  if (q.isLoading || !s) return q.error ? <Alert>{(q.error as Error).message}</Alert> : <Spinner label="Loading settings…" />;
  const meta = q.data!.meta;
  const occ = s.occasions;
  const g = occ.greeting_posts;
  const setOcc = (patch: Partial<typeof occ>) => setS({ ...s, occasions: { ...occ, ...patch } });
  const setG = (patch: Partial<typeof g>) => setOcc({ greeting_posts: { ...g, ...patch } });
  const toggleIn = (list: string[], id: string, on: boolean) => (on ? [...new Set([...list, id])] : list.filter((x) => x !== id));
  const allRegions = occ.regions.includes("all");

  return (
    <form className="space-y-5" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      {!canEdit && <Alert tone="info">You can view these settings. Ask an administrator for the “Run research” permission to change them.</Alert>}
      <fieldset disabled={!canEdit} className="space-y-5">
        <Card>
          <h2 className="font-semibold">Daily research</h2>
          <p className="mb-4 text-sm text-muted">Every morning the AI searches the web, reads the best sources and writes idea briefs for you.</p>
          <div className="mb-4"><Toggle checked={s.enabled} onChange={(v) => setS({ ...s, enabled: v })} label="Research every day" /></div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Time of day" hint="In your organization's time zone.">
              <Input type="time" required value={s.run_time} onChange={(e) => setS({ ...s, run_time: e.target.value })} />
            </Field>
            <Field label={`Ideas per day: ${s.ideas_per_day}`} hint="Occasion ideas are added on top.">
              <input type="range" min={3} max={20} value={s.ideas_per_day} onChange={(e) => setS({ ...s, ideas_per_day: Number(e.target.value) })}
                className="mt-3 w-full accent-[rgb(var(--brand))]" />
            </Field>
          </div>
          <div className="mt-4 text-sm font-medium">How deep</div>
          <div role="radiogroup" className="mt-1.5 grid gap-2 sm:grid-cols-3">
            {meta.depths.map((d) => (
              <label key={d.id} className={`cursor-pointer rounded-lg border px-3 py-2.5 ${s.depth === d.id ? "border-primary-600 bg-brand-tint ring-1 ring-primary-600" : "border-line hover:border-brand/40"}`}>
                <input type="radio" name="depth" className="sr-only" checked={s.depth === d.id} onChange={() => setS({ ...s, depth: d.id as ResearchSettings["depth"] })} />
                <span className="block text-sm font-semibold">{d.label}</span>
                <span className="block text-xs text-muted">{d.about}</span>
              </label>
            ))}
          </div>
        </Card>

        <Card>
          <h2 className="font-semibold">What to research</h2>
          <p className="mb-4 text-sm text-muted">Each theme is made specific with your products, studios, industries and clients from Company intelligence.</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {meta.themes.map((t) => (
              <Check key={t.key} checked={s.themes[t.key] ?? true} onChange={(v) => setS({ ...s, themes: { ...s.themes, [t.key]: v } })} label={t.label} hint={t.about} />
            ))}
          </div>
          <div className="mt-5 text-sm font-medium">Your own themes</div>
          <ul className="mt-2 space-y-2">
            {s.custom_themes.map((c, i) => (
              <li key={i} className="grid gap-2 sm:grid-cols-[220px_1fr_auto]">
                <Input aria-label="Theme name" value={c.label} placeholder="Theme name" onChange={(e) => setS({ ...s, custom_themes: s.custom_themes.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
                <Input aria-label="What to look for" value={c.focus ?? ""} placeholder="What to look for, e.g. gig workers payroll India" onChange={(e) => setS({ ...s, custom_themes: s.custom_themes.map((x, j) => (j === i ? { ...x, focus: e.target.value } : x)) })} />
                <Button type="button" variant="ghost" aria-label={`Remove ${c.label || "theme"}`} onClick={() => setS({ ...s, custom_themes: s.custom_themes.filter((_, j) => j !== i) })}><Trash2 size={15} /></Button>
              </li>
            ))}
          </ul>
          {s.custom_themes.length < 10 && (
            <Button type="button" variant="secondary" className="mt-2" onClick={() => setS({ ...s, custom_themes: [...s.custom_themes, { label: "", focus: "" }] })}><Plus size={15} /> Add a theme</Button>
          )}
          <div className="mt-5">
            <Field label="Keywords to include" hint="Separate with commas. Added to every theme's searches.">
              <Input value={text.keywords} onChange={(e) => setText({ ...text, keywords: e.target.value })} placeholder="HRMS, AI agents, payroll compliance" />
            </Field>
          </div>
        </Card>

        <Card>
          <h2 className="mb-4 font-semibold">Sources and topics</h2>
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Trusted sources" hint="One site per line. Preferred when ranking results.">
              <Textarea rows={4} value={text.trusted} onChange={(e) => setText({ ...text, trusted: e.target.value })} placeholder={"labour.gov.in\nnasscom.in"} />
            </Field>
            <Field label="Never use these sites" hint="One site per line. Social networks and job boards are always skipped.">
              <Textarea rows={4} value={text.blocked} onChange={(e) => setText({ ...text, blocked: e.target.value })} placeholder="example-tabloid.com" />
            </Field>
            <Field label="Topics to avoid" hint="One per line. Ideas mentioning them are dropped.">
              <Textarea rows={4} value={text.excluded} onChange={(e) => setText({ ...text, excluded: e.target.value })} placeholder={"politics\ncrypto"} />
            </Field>
          </div>
        </Card>

        <Card>
          <h2 className="font-semibold">Feed the posting planner</h2>
          <p className="mb-4 text-sm text-muted">The best ideas become topics that scheduled posts can use automatically, with their facts and sources.</p>
          <Toggle checked={s.auto_topics.enabled} onChange={(v) => setS({ ...s, auto_topics: { ...s.auto_topics, enabled: v } })} label="Add top ideas to the planner's topics" />
          {s.auto_topics.enabled && (
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label={`Minimum score: ${Math.round(s.auto_topics.min_score * 100)}`}>
                <input type="range" min={50} max={95} step={5} value={Math.round(s.auto_topics.min_score * 100)}
                  onChange={(e) => setS({ ...s, auto_topics: { ...s.auto_topics, min_score: Number(e.target.value) / 100 } })} className="mt-3 w-full accent-[rgb(var(--brand))]" />
              </Field>
              <Field label="At most per day">
                <Input type="number" min={0} max={20} value={s.auto_topics.max_per_day} onChange={(e) => setS({ ...s, auto_topics: { ...s.auto_topics, max_per_day: Number(e.target.value) } })} />
              </Field>
            </div>
          )}
        </Card>

        <Card>
          <h2 className="font-semibold">Festivals, special days and seasons</h2>
          <p className="mb-4 text-sm text-muted">Research plans greetings and campaign ideas ahead of the occasions you observe, in an inclusive tone and without hard selling on solemn days.</p>
          <Toggle checked={occ.enabled} onChange={(v) => setOcc({ enabled: v })} label="Suggest ideas for occasions" />
          {occ.enabled && (
            <div className="mt-4 space-y-4">
              <div className="grid gap-2 sm:grid-cols-3">
                {meta.occasion_categories.map((c) => (
                  <Check key={c.id} checked={occ.categories.includes(c.id)} onChange={(v) => setOcc({ categories: toggleIn(occ.categories, c.id, v) })} label={c.label} />
                ))}
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={`Suggest ideas ${occ.lead_days} days ahead`}>
                  <input type="range" min={3} max={30} value={occ.lead_days} onChange={(e) => setOcc({ lead_days: Number(e.target.value) })} className="mt-3 w-full accent-[rgb(var(--brand))]" />
                </Field>
                <div>
                  <div className="mb-1.5 text-sm font-medium">Regions for regional festivals</div>
                  <Toggle checked={allRegions} onChange={(v) => setOcc({ regions: v ? ["all"] : [] })} label="All of India" />
                </div>
              </div>
              {!allRegions && (
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Regions">
                  {meta.regions.map((r) => {
                    const on = occ.regions.includes(r.id);
                    return (
                      <button key={r.id} type="button" aria-pressed={on} onClick={() => setOcc({ regions: toggleIn(occ.regions.filter((x) => x !== "all"), r.id, !on) })}
                        className={`rounded-full border px-2.5 py-1 text-sm ${on ? "border-primary-600 bg-primary-600 text-white" : "border-line hover:border-brand/50"}`}>{r.label}</button>
                    );
                  })}
                </div>
              )}

              <div className="rounded-lg border border-line bg-canvas/60 p-4">
                <Toggle checked={g.enabled} onChange={(v) => setG({ enabled: v })} label="Plan a greeting post on the day" />
                <p className="mt-1 text-xs text-muted">Greeting posts are written a day ahead so you can review them, then posted at the time below.</p>
                {g.enabled && (
                  <div className="mt-4 space-y-4">
                    <div>
                      <div className="mb-1.5 text-sm font-medium">Post to</div>
                      <div className="flex flex-wrap gap-2">
                        {(accounts.data ?? []).map((a) => {
                          const on = g.account_ids.includes(a.id);
                          return (
                            <button key={a.id} type="button" aria-pressed={on} onClick={() => setG({ account_ids: toggleIn(g.account_ids, a.id, !on) })}
                              className={`rounded-full border px-3 py-1.5 text-sm ${on ? "border-primary-600 bg-primary-600 text-white" : "border-line bg-surface hover:border-brand/50"}`}>
                              {a.name} <span className={on ? "text-white/80" : "text-muted"}>· {a.platform_label}</span>
                            </button>
                          );
                        })}
                        {accounts.data && !accounts.data.length && <span className="text-sm text-muted">No social accounts yet.</span>}
                      </div>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-3">
                      <Field label="Posting time"><Input type="time" value={g.time} onChange={(e) => setG({ time: e.target.value })} /></Field>
                      <Field label="Approval">
                        <Select value={g.mode} onChange={(e) => setG({ mode: e.target.value })}>
                          <option value="approve_first">Approve before posting</option>
                          <option value="auto_with_window">Post unless rejected</option>
                          <option value="full_auto">Post automatically</option>
                          <option value="manual">Draft only</option>
                        </Select>
                      </Field>
                      <Field label="Post type">
                        <Select value={g.post_type} onChange={(e) => setG({ post_type: e.target.value })}>
                          <option value="image">Image</option><option value="text">Text</option><option value="carousel">Carousel</option><option value="video">Short video</option>
                        </Select>
                      </Field>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {meta.occasion_categories.filter((c) => c.id !== "season").map((c) => (
                        <label key={c.id} className="inline-flex items-center gap-1.5 text-sm">
                          <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={g.categories.includes(c.id)} onChange={(e) => setG({ categories: toggleIn(g.categories, c.id, e.target.checked) })} />
                          {c.label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </fieldset>
      {save.error && <Alert>{(save.error as Error).message}</Alert>}
      {canEdit && (
        <div className="sticky bottom-0 -mx-1 flex items-center gap-3 bg-canvas/90 px-1 py-3 backdrop-blur">
          <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save research settings"}</Button>
          {saved && <span className="text-sm text-emerald-700">Saved.</span>}
        </div>
      )}
    </form>
  );
}
