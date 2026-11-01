import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FlaskConical, Plus, Sparkles, Trophy, X } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, PageHeader, Select, Spinner, Textarea } from "../../components/ui";
import { caption, fmt, fromLocalInput, parseTags, toLocalInput, tzLabel, useContentAccounts, usePlatform, useTz, type PostDetail } from "./shared";

type Metric = "engagement_rate" | "clicks" | "impressions";
type Mode = "time" | "accounts";
type VariantOut = {
  label: string; kind: string; change: string | null; post_id: string; status: string; status_label: string; text: string | null;
  hashtags: string[]; scheduled_at: string | null; published_at: string | null; external_url: string | null;
  account: { id: string; name: string; platform: string; platform_label: string } | null;
  metrics: { impressions: number; engagements: number; clicks: number; engagement_rate: number | null; fetched_at: string | null } | null;
  is_winner: boolean;
};
type ResultRow = { label: string; value: number | null };
type Experiment = {
  id: string; name: string; status: "running" | "done" | "cancelled"; mode: Mode; mode_label: string; metric: Metric; metric_label: string;
  evaluate_after_days: number; winner_post_id: string | null; started_at: string | null; ended_at: string | null; checked_at: string | null;
  result: { state?: string; message?: string; verdict?: "clear" | "slight" | "tie" | "no_data"; leader?: string | null; difference_pct?: number | null; notes?: string[]; variants?: ResultRow[]; due_at?: string };
  variants: VariantOut[]; created_at: string | null;
};

const METRICS: { id: Metric; label: string; hint: string }[] = [
  { id: "engagement_rate", label: "Engagement rate", hint: "Likes, comments, shares, saves and clicks ÷ impressions. Fair when reach differs." },
  { id: "clicks", label: "Link clicks", hint: "Best when the post has a link." },
  { id: "impressions", label: "Impressions", hint: "How many times it was seen." },
];
const VERDICT: Record<string, { label: string; tone: "brand" | "accent" | "neutral" }> = {
  clear: { label: "Clear lead", tone: "brand" }, slight: { label: "Slight lead — a hint", tone: "accent" },
  tie: { label: "Too close to call", tone: "neutral" }, no_data: { label: "No data", tone: "neutral" },
};
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const n = (v: number | null | undefined) => (v == null ? "—" : new Intl.NumberFormat("en-IN").format(v));

// ------------------------------------------------------------------ create
type Draft = { text: string; tags: string; account_id: string; change: string; kind: "original" | "manual" | "ai"; post_id?: string };

function CreateExperiment({ fromId, onDone, onCancel }: { fromId: string | null; onDone: (id: string) => void; onCancel: () => void }) {
  const tz = useTz();
  const accounts = useContentAccounts();
  const src = useQuery({ queryKey: ["post", fromId], queryFn: () => api<PostDetail>(`/content/posts/${fromId}`), enabled: !!fromId });
  const [name, setName] = useState("");
  const [metric, setMetric] = useState<Metric>("engagement_rate");
  const [mode, setMode] = useState<Mode>("time");
  const [days, setDays] = useState(3);
  const [spacing, setSpacing] = useState(7);
  const [then, setThen] = useState<"draft" | "review" | "schedule">("review");
  const [when, setWhen] = useState("");
  const [account, setAccount] = useState("");
  const [vs, setVs] = useState<Draft[]>([
    { text: "", tags: "", account_id: "", change: "", kind: "manual" }, { text: "", tags: "", account_id: "", change: "", kind: "manual" },
  ]);
  const [focus, setFocus] = useState<"hook" | "cta" | "both">("hook");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const p = src.data;
    if (!p || loaded) return;
    setLoaded(true);
    const editable = !["published", "publishing", "planned", "generating"].includes(p.status);
    const tags = p.hashtags.map((h) => `#${h}`).join(" ");
    setName(`${(p.title || p.text || "Post").slice(0, 50)} — A/B test`);
    setAccount(p.social_account_id ?? "");
    if (p.scheduled_at && new Date(p.scheduled_at).getTime() > Date.now()) setWhen(toLocalInput(p.scheduled_at, tz));
    setVs([
      { text: p.text ?? "", tags, account_id: p.social_account_id ?? "", change: "Original", kind: editable ? "original" : "manual", post_id: editable ? p.id : undefined },
      { text: "", tags, account_id: "", change: "", kind: "manual" },
    ]);
  }, [src.data, loaded, tz]);

  const acc = (accounts.data ?? []).find((a) => a.id === (mode === "time" ? account : vs[0].account_id));
  const plat = usePlatform(acc?.platform);
  const suggest = useMutation({
    mutationFn: () => api<{ variants: { text: string; change: string; kind: "ai" }[] }>("/content/experiments/suggest", {
      method: "POST", json: { text: vs[0].text, account_id: acc?.id ?? null, focus, count: Math.min(2, 3 - vs.filter((v) => v.text.trim()).length) || 1 },
    }),
    onSuccess: (r) => {
      setVs((cur) => {
        const next = [...cur];
        for (const s of r.variants) {
          const empty = next.findIndex((v, i) => i > 0 && !v.text.trim());
          const d: Draft = { text: s.text, tags: cur[0].tags, account_id: "", change: s.change, kind: "ai" };
          if (empty >= 0) next[empty] = { ...next[empty], ...d, account_id: next[empty].account_id };
          else if (next.length < 3) next.push(d);
        }
        return next;
      });
    },
  });
  const create = useMutation({
    mutationFn: () => api<Experiment>("/content/experiments", {
      method: "POST",
      json: {
        name, metric, mode, evaluate_after_days: days, spacing_days: spacing, then, source_post_id: fromId,
        variants: vs.map((v, i) => ({
          text: v.text, hashtags: parseTags(v.tags), kind: v.kind, change: v.change || null, post_id: v.post_id ?? null,
          account_id: mode === "time" ? (account || null) : (v.account_id || null),
          scheduled_at: i === 0 || mode === "accounts" ? fromLocalInput(when) : null,
        })),
      },
    }),
    onSuccess: (e) => onDone(e.id),
  });
  const set = (i: number, patch: Partial<Draft>) => setVs((cur) => cur.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const planned = useMemo(() => {
    if (!when) return [];
    const base = new Date(when);
    return vs.map((_, i) => {
      const d = new Date(base);
      if (mode === "time") d.setDate(d.getDate() + spacing * i);
      return d;
    });
  }, [when, vs, mode, spacing]);
  const sameAccountOk = mode === "time" ? !!account : vs.every((v) => v.account_id) && new Set(vs.map((v) => v.account_id)).size === vs.length;
  const ready = name.trim() && sameAccountOk && vs.every((v) => v.text.trim()) && (then === "draft" || !!when);

  if (fromId && src.isLoading) return <Spinner label="Loading the post…" />;
  return (
    <Card className="mb-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">New A/B test</h2>
        <button onClick={onCancel} aria-label="Close" className="text-muted hover:text-ink"><X size={18} /></button>
      </div>
      {src.data && ["published", "publishing"].includes(src.data.status) && (
        <div className="mb-3"><Alert tone="info">This post is already published, so its text is the starting point for the new variants.</Alert></div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Question hook vs statement" /></Field>
        <Field label="Measure by" hint={METRICS.find((m) => m.id === metric)?.hint}>
          <Select value={metric} onChange={(e) => setMetric(e.target.value as Metric)}>{METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</Select>
        </Field>
        <Field label="How to compare">
          <Select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="time">One account, posted at comparable times</option>
            <option value="accounts">Different accounts on the same platform, same time</option>
          </Select>
        </Field>
        {mode === "time" ? (
          <Field label="Account">
            <Select value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">Choose an account</option>
              {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name} · {a.platform_label}</option>)}
            </Select>
          </Field>
        ) : <div className="hidden md:block" />}
        <Field label={`First post goes out (${tzLabel(tz)})`} hint={then === "draft" ? "Optional for drafts." : undefined}>
          <Input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        </Field>
        {mode === "time" && (
          <Field label="The other variants go out">
            <Select value={spacing} onChange={(e) => setSpacing(Number(e.target.value))}>
              <option value={7}>Same weekday and time, one week apart</option>
              <option value={14}>Same weekday and time, two weeks apart</option>
              <option value={3}>Same time, 3 days apart</option>
              <option value={2}>Same time, 2 days apart</option>
            </Select>
          </Field>
        )}
        <Field label="Compare the results" hint="After the last variant is published. Results need time to come in.">
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 2, 3, 5, 7, 14].map((d) => <option key={d} value={d}>{d} day{d > 1 ? "s" : ""} later</option>)}
          </Select>
        </Field>
        <Field label="Then">
          <Select value={then} onChange={(e) => setThen(e.target.value as typeof then)}>
            <option value="draft">Save the variants as drafts</option>
            <option value="review">Send them for approval</option>
            <option value="schedule">Schedule them (they still need approval unless auto-approval is on)</option>
          </Select>
        </Field>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Variants</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="What the AI changes" value={focus} onChange={(e) => setFocus(e.target.value as typeof focus)} className="w-auto">
            <option value="hook">AI: new opening line</option>
            <option value="cta">AI: new call to action</option>
            <option value="both">AI: both</option>
          </Select>
          <Button variant="secondary" onClick={() => suggest.mutate()} disabled={suggest.isPending || vs[0].text.trim().length < 10 || !acc || vs.length >= 3 && vs.every((v) => v.text.trim())}
            title="Writes alternatives of variant A that change only the hook or call to action, without adding facts">
            <Sparkles size={15} /> {suggest.isPending ? "Writing…" : "Suggest with AI"}
          </Button>
          {vs.length < 3 && <Button variant="ghost" onClick={() => setVs([...vs, { text: "", tags: vs[0].tags, account_id: "", change: "", kind: "manual" }])}><Plus size={15} /> Variant</Button>}
        </div>
      </div>
      {suggest.error && <div className="mt-2"><Alert>{(suggest.error as Error).message}</Alert></div>}
      <div className={`mt-3 grid gap-4 ${vs.length === 3 ? "lg:grid-cols-3" : "md:grid-cols-2"}`}>
        {vs.map((v, i) => {
          const count = caption(v.text, parseTags(v.tags)).length;
          return (
            <div key={i} className="min-w-0 rounded-xl border border-line p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2"><span className="grid h-7 w-7 place-items-center rounded-full bg-primary-50 font-bold text-primary-800">{"ABC"[i]}</span>
                  {v.kind === "original" && <Badge>Original post</Badge>}{v.kind === "ai" && <Badge tone="accent">AI suggestion</Badge>}</div>
                {i >= 2 && <button className="text-sm text-muted hover:text-ink" onClick={() => setVs(vs.filter((_, j) => j !== i))}>Remove</button>}
              </div>
              <Textarea rows={7} value={v.text} onChange={(e) => set(i, { text: e.target.value, kind: v.kind === "ai" ? "manual" : v.kind })} aria-label={`Variant ${"ABC"[i]} text`} />
              <div className={`mt-1 text-right text-xs tabular-nums ${plat && count > plat.max_chars ? "text-danger" : "text-muted"}`}>{count}{plat ? ` / ${plat.max_chars}` : ""}</div>
              <div className="mt-2 space-y-2">
                <Input value={v.tags} onChange={(e) => set(i, { tags: e.target.value })} placeholder="#hashtags" aria-label="Hashtags" />
                <Input value={v.change} onChange={(e) => set(i, { change: e.target.value })} placeholder="What's different (for example: question hook)" aria-label="What's different" />
                {mode === "accounts" && (
                  <Select value={v.account_id} onChange={(e) => set(i, { account_id: e.target.value })} aria-label="Account">
                    <option value="">Choose an account</option>
                    {(accounts.data ?? []).filter((a) => !acc || i === 0 || a.platform === acc.platform).map((a) => <option key={a.id} value={a.id}>{a.name} · {a.platform_label}</option>)}
                  </Select>
                )}
                {planned[i] && <p className="text-xs text-muted">Goes out {fmt(planned[i].toISOString(), Intl.DateTimeFormat().resolvedOptions().timeZone, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</p>}
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-muted">Change one thing at a time (the opening line, or the call to action) so you know what made the difference. Images and links are copied from the original post.</p>
      {create.error && <div className="mt-3"><Alert>{(create.error as Error).message}</Alert></div>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button onClick={() => create.mutate()} disabled={!ready || create.isPending}><FlaskConical size={15} /> {create.isPending ? "Starting…" : "Start the test"}</Button>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ one test
function ExperimentCard({ e, highlight }: { e: Experiment; highlight: boolean }) {
  const tz = useTz();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const refresh = () => qc.invalidateQueries({ queryKey: ["experiments"] });
  const evaluate = useMutation({ mutationFn: () => api(`/content/experiments/${e.id}/evaluate`, { method: "POST", json: { force: true } }), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: () => api(`/content/experiments/${e.id}/cancel`, { method: "POST" }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api(`/content/experiments/${e.id}`, { method: "DELETE" }), onSuccess: refresh });
  const reuse = useMutation({
    mutationFn: () => api<{ post_id: string }>(`/content/experiments/${e.id}/reuse-winner`, { method: "POST", json: {} }),
    onSuccess: (r) => nav(`/posts/${r.post_id}`),
  });
  const err = evaluate.error || cancel.error || remove.error || reuse.error;
  const r = e.result ?? {};
  const v = r.verdict ? VERDICT[r.verdict] : null;
  const best = (x: VariantOut) => (e.metric === "engagement_rate" ? x.metrics?.engagement_rate : e.metric === "clicks" ? x.metrics?.clicks : x.metrics?.impressions);

  return (
    <Card className={highlight ? "ring-2 ring-primary-500/50" : ""}>
      <div id={`exp-${e.id}`} className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold [overflow-wrap:anywhere]">{e.name}</h2>
            <Badge tone={e.status === "running" ? "accent" : e.status === "done" ? "brand" : "neutral"}>{e.status === "running" ? "Running" : e.status === "done" ? "Finished" : "Stopped"}</Badge>
            {v && <Badge tone={v.tone}>{v.label}</Badge>}
          </div>
          <p className="text-sm text-muted">{e.mode_label} · measured by {e.metric_label.toLowerCase()} · compared {e.evaluate_after_days} day{e.evaluate_after_days > 1 ? "s" : ""} after the last post{e.started_at ? ` · started ${fmt(e.started_at, tz, { day: "numeric", month: "short", year: "numeric" })}` : ""}</p>
        </div>
        {can("content.create") && (
          <div className="flex flex-wrap gap-2">
            {e.winner_post_id && <Button onClick={() => reuse.mutate()} disabled={reuse.isPending}><Trophy size={15} /> Reuse winner</Button>}
            {e.status === "running" && <Button variant="secondary" onClick={() => evaluate.mutate()} disabled={evaluate.isPending} title="Compare now if every variant has results, without waiting">Check now</Button>}
            {e.status === "running" && <Button variant="ghost" onClick={() => { if (confirm("Stop this test? The posts are kept.")) cancel.mutate(); }}>Stop</Button>}
            {e.status !== "running" && <Button variant="ghost" onClick={() => { if (confirm("Delete this test? The posts are kept.")) remove.mutate(); }}>Delete</Button>}
          </div>
        )}
      </div>
      {r.message && <div className="mt-3"><Alert tone={e.status === "done" && e.winner_post_id ? "info" : e.status === "done" ? "warn" : "info"}>{r.message}</Alert></div>}
      {err && <div className="mt-3"><Alert>{(err as Error).message}</Alert></div>}
      <div className={`mt-4 grid gap-3 ${e.variants.length === 3 ? "lg:grid-cols-3" : "md:grid-cols-2"}`}>
        {e.variants.map((x) => (
          <div key={x.post_id} className={`min-w-0 rounded-xl border p-3 ${x.is_winner ? "border-primary-500 bg-brand-tint/40" : "border-line"}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-primary-50 font-bold text-primary-800">{x.label}</span>
                {x.is_winner && <Badge tone="brand"><Trophy size={12} className="mr-1" />Winner</Badge>}
                {x.kind === "ai" && <Badge tone="accent">AI</Badge>}
              </div>
              <Link to={`/posts/${x.post_id}`} className="text-xs text-primary-700 hover:underline">{x.status_label}</Link>
            </div>
            <div className="mt-1 text-xs text-muted">
              {x.account ? `${x.account.name} · ${x.account.platform_label}` : "No account"} · {x.published_at ? `published ${fmt(x.published_at, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}` : x.scheduled_at ? `planned ${fmt(x.scheduled_at, tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}` : "no time set"}
            </div>
            {x.change && <div className="mt-1 text-xs font-medium text-ink">{x.change}</div>}
            <p className="mt-2 line-clamp-6 whitespace-pre-line text-sm [overflow-wrap:anywhere]">{x.text}</p>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-line pt-2 text-sm">
              {([["Impressions", n(x.metrics?.impressions), "impressions"], ["Engagements", n(x.metrics?.engagements), ""], ["Clicks", n(x.metrics?.clicks), "clicks"], ["Engagement rate", pct(x.metrics?.engagement_rate), "engagement_rate"]] as const).map(([l, val, key]) => (
                <div key={l} className="contents">
                  <dt className="text-muted">{l}</dt>
                  <dd className={`text-right tabular-nums ${key === e.metric ? "font-bold text-ink" : ""}`}>{x.metrics ? val : "—"}</dd>
                </div>
              ))}
            </dl>
            {!x.metrics && x.published_at && <p className="mt-1 text-xs text-muted">Waiting for results from the platform.</p>}
            {best(x) == null && !x.published_at && <p className="mt-1 text-xs text-muted">Results appear after it's published.</p>}
          </div>
        ))}
      </div>
      {r.notes && r.notes.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-muted">{r.notes.map((t) => <li key={t}>{t}</li>)}</ul>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ page
export default function Experiments() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const fromId = params.get("from");
  const focusId = params.get("id");
  const [creating, setCreating] = useState(!!fromId);
  const [filter, setFilter] = useState<"" | "running" | "done">("");
  const q = useQuery({ queryKey: ["experiments", filter], queryFn: () => api<{ items: Experiment[] }>(`/content/experiments${filter ? `?status=${filter}` : ""}`) });
  const qc = useQueryClient();
  useEffect(() => {
    if (focusId && q.data) document.getElementById(`exp-${focusId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusId, q.data]);
  const items = q.data?.items ?? [];

  return (
    <div>
      <Link to="/posts" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={15} /> All posts</Link>
      <PageHeader title="A/B tests" description="Try two or three versions of a post and see which one people respond to. Results are compared once they've had time to come in."
        action={can("content.create") && !creating && <Button onClick={() => setCreating(true)}><Plus size={16} /> New A/B test</Button>} />
      {creating && can("content.create") && (
        <CreateExperiment fromId={fromId} onCancel={() => { setCreating(false); if (fromId) setParams({}, { replace: true }); }}
          onDone={(id) => { setCreating(false); setParams({ id }, { replace: true }); qc.invalidateQueries({ queryKey: ["experiments"] }); }} />
      )}
      <div className="mb-4">
        <Select aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} className="w-56">
          <option value="">All tests</option><option value="running">Running</option><option value="done">Finished</option>
        </Select>
      </div>
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading ? <Spinner label="Loading tests…" /> : items.length === 0 ? (
        <EmptyState title="No A/B tests yet" body="Open a post and press “A/B test”, or start one here. Change one thing (the opening line or the call to action) so you learn what works."
          action={can("content.create") && !creating && <Button onClick={() => setCreating(true)}><Plus size={16} /> New A/B test</Button>} />
      ) : (
        <div className="space-y-5">{items.map((e) => <ExperimentCard key={e.id} e={e} highlight={e.id === focusId} />)}</div>
      )}
      <p className="mt-6 text-xs text-muted">How winners are picked: once every variant is published and the waiting time has passed, the variants are compared on the chosen measure. A lead is called clear only when the gap is large compared with the numbers involved; with one post per variant it's still a small test, so confirm big decisions with another test.</p>
    </div>
  );
}
