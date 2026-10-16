import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CalendarClock, Play, Search, Sparkles } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, EmptyState, Input, PageHeader, Select, Spinner, Tabs, formatDate } from "../components/ui";
import BriefCard from "./research/BriefCard";
import { OccasionStrip, OccasionsTab } from "./research/Occasions";
import { RunLog, RunStatusIcon } from "./research/RunPanel";
import SettingsPanel, { ProviderNotice } from "./research/Settings";
import { fmtDay, type Brief, type Overview, type Run, type SettingsMeta } from "./research/types";

type Tab = "briefs" | "occasions" | "history" | "settings";
const TABS: Tab[] = ["briefs", "occasions", "history", "settings"];
const STATUS_FILTERS = [
  { id: "active", label: "To do" }, { id: "saved", label: "Saved" }, { id: "used", label: "Used" }, { id: "dismissed", label: "Dismissed" },
] as const;

function useOverview() {
  return useQuery({
    queryKey: ["research", "overview"],
    queryFn: () => api<Overview>("/research/overview"),
    refetchInterval: (q) => (q.state.data?.active_run ? 3000 : 60_000),
  });
}

function Briefing({ o, canRun }: { o: Overview; canRun: boolean }) {
  const run = o.active_run ?? o.latest_run;
  const tz = o.timezone;
  const day = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" })
    .format(new Date(`${o.today}T00:00:00Z`));
  const next = o.next_run_at ? new Intl.DateTimeFormat("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: tz }).format(new Date(o.next_run_at)) : null;
  const sources = run?.stats.pages_read;
  return (
    <section className="ai-mesh-dark relative mb-6 overflow-hidden rounded-2xl p-5 text-white shadow-[0_24px_80px_rgba(6,47,89,0.22)] sm:p-7">
      <div className="ai-grid-overlay pointer-events-none absolute inset-0 opacity-40" aria-hidden />
      <div className="relative grid gap-6 lg:grid-cols-[1fr_380px]">
        <div>
          <p className="flex items-center gap-1.5 text-sm text-secondary-300"><Sparkles size={15} /> Daily briefing</p>
          <h2 className="mt-1 text-[26px] font-bold leading-tight sm:text-[30px]">{day}</h2>
          <p className="mt-2 max-w-xl text-white/70">
            {o.counts.today
              ? `${o.counts.today} idea${o.counts.today === 1 ? "" : "s"} researched today${sources ? ` from ${sources} pages read across the web` : ""}. Pick the ones worth publishing.`
              : o.active_run ? "Research is running. New ideas appear here as soon as it finishes."
                : "No ideas yet today. They arrive every morning, or run research now."}
          </p>
          <dl className="mt-5 grid max-w-lg grid-cols-3 gap-3">
            {[["To review", o.counts.new], ["Saved", o.counts.saved], ["Used in content", o.counts.used]].map(([label, value]) => (
              <div key={label as string} className="rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2.5">
                <dt className="text-xs text-white/60">{label}</dt>
                <dd className="text-2xl font-bold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 flex items-center gap-1.5 text-sm text-white/60">
            <CalendarClock size={15} />
            {o.settings.enabled ? <>Next research {next ?? "—"} · {o.settings.depth} · {o.settings.ideas_per_day} ideas a day</> : "Daily research is off. Turn it on in Settings."}
          </p>
        </div>
        <div className="self-start">
          {run ? <RunLog key={run.id + run.status} run={run} tz={tz} /> : (
            <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4 text-sm text-white/70">
              The first research run reads the web for your themes and writes evidence-backed idea briefs.
              {canRun ? " Press “Run research now” to try it." : ""}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function BriefsTab({ o, meta, day, setDay }: { o: Overview; meta?: SettingsMeta; day: string; setDay: (d: string) => void }) {
  const [theme, setTheme] = useState("");
  const [fmt, setFmt] = useState("");
  const [status, setStatus] = useState<string>("active");
  const [q, setQ] = useState("");
  const days = useQuery({ queryKey: ["research", "days"], queryFn: () => api<{ date: string; count: number }[]>("/research/briefs/days") });
  const effectiveDay = day === "latest" ? (o.latest_brief_date ?? o.today) : day;
  const params = new URLSearchParams({ status });
  if (effectiveDay !== "all") params.set("date", effectiveDay);
  if (theme) params.set("theme", theme);
  if (fmt) params.set("format", fmt);
  if (q.trim()) params.set("q", q.trim());
  const briefs = useQuery({ queryKey: ["research", "briefs", params.toString()], queryFn: () => api<Brief[]>(`/research/briefs?${params}`) });
  const list = briefs.data ?? [];
  const occasionBriefs = list.filter((b) => b.occasion);
  const regular = list.filter((b) => !b.occasion);

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <Select aria-label="Day" value={day} onChange={(e) => setDay(e.target.value)} className="w-full sm:w-48">
          <option value="latest">Latest briefing</option>
          {(days.data ?? []).map((d) => (
            <option key={d.date} value={d.date}>{d.date === o.today ? "Today" : fmtDay(d.date, { weekday: "short", day: "numeric", month: "short" })} ({d.count})</option>
          ))}
          <option value="all">All days</option>
        </Select>
        <Select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value)} className="w-full sm:w-52">
          <option value="">All themes</option>
          {(meta?.themes ?? []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </Select>
        <Select aria-label="Format" value={fmt} onChange={(e) => setFmt(e.target.value)} className="w-full sm:w-48">
          <option value="">All formats</option>
          {(meta?.formats ?? []).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </Select>
        <div className="relative w-full sm:w-56">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <Input aria-label="Search ideas" placeholder="Search ideas" value={q} onChange={(e) => setQ(e.target.value)} className="w-full pl-9" />
        </div>
        <div role="tablist" aria-label="Status" className="flex rounded-lg border border-line bg-surface p-0.5 sm:ml-auto">
          {STATUS_FILTERS.map((s) => (
            <button key={s.id} role="tab" aria-selected={status === s.id} onClick={() => setStatus(s.id)}
              className={`rounded-md px-3 py-1 text-sm ${status === s.id ? "bg-primary-600 text-white" : "text-muted hover:text-ink"}`}>{s.label}</button>
          ))}
        </div>
      </div>

      {briefs.error && <Alert>{(briefs.error as Error).message}</Alert>}
      {briefs.isLoading && <Spinner label="Loading ideas…" />}
      {briefs.data && !list.length && (
        <EmptyState
          title={status === "active" ? "Nothing to review here" : "No ideas match"}
          body={status === "active" ? "New idea briefs arrive with each research run. Try another day or theme, or run research now." : "Try another filter."}
        />
      )}
      {occasionBriefs.length > 0 && (
        <section className="mb-7">
          <h3 className="mb-3 text-sm font-semibold text-muted">For upcoming occasions</h3>
          <div className="grid gap-4 md:grid-cols-2">{occasionBriefs.map((b) => <BriefCard key={b.id} b={b} today={o.today} />)}</div>
        </section>
      )}
      {regular.length > 0 && (
        <section>
          {occasionBriefs.length > 0 && <h3 className="mb-3 text-sm font-semibold text-muted">From today's research</h3>}
          <div className="grid gap-4 md:grid-cols-2">{regular.map((b) => <BriefCard key={b.id} b={b} today={o.today} />)}</div>
        </section>
      )}
    </div>
  );
}

function HistoryTab({ tz, openDay }: { tz: string; openDay: (d: string) => void }) {
  const runs = useQuery({ queryKey: ["research", "runs"], queryFn: () => api<Run[]>("/research/runs?limit=40") });
  if (runs.isLoading) return <Spinner label="Loading runs…" />;
  if (runs.error) return <Alert>{(runs.error as Error).message}</Alert>;
  if (!runs.data?.length) return <EmptyState title="No research runs yet" body="Runs appear here with what they searched and read." />;
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="bg-canvas text-left text-xs text-muted">
          <tr><th className="px-4 py-2 font-medium">Day</th><th className="px-4 py-2 font-medium">Run</th><th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Searches</th><th className="px-4 py-2 font-medium">Pages read</th><th className="px-4 py-2 font-medium">Ideas</th><th className="px-4 py-2" /></tr>
        </thead>
        <tbody>
          {runs.data.map((r) => (
            <tr key={r.id} className="border-t border-line">
              <td className="px-4 py-2.5 font-medium">{fmtDay(r.run_date, { weekday: "short", day: "numeric", month: "short" })}</td>
              <td className="px-4 py-2.5 text-muted">{r.trigger === "scheduled" ? "Daily" : "By hand"} · {r.depth}<div className="text-xs">{formatDate(r.created_at, tz)}</div></td>
              <td className="px-4 py-2.5">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white"><RunStatusIcon status={r.status} className="!h-3.5 !w-3.5" />{r.status}</span>
                {r.error && <div className="mt-1 max-w-xs text-xs text-danger">{r.error}</div>}
              </td>
              <td className="px-4 py-2.5 tabular-nums">{r.stats.searches ?? "—"}</td>
              <td className="px-4 py-2.5 tabular-nums">{r.stats.pages_read ?? "—"}</td>
              <td className="px-4 py-2.5 tabular-nums">{r.stats.briefs ?? "—"}</td>
              <td className="px-4 py-2.5 text-right">{!!r.stats.briefs && <Button variant="ghost" className="h-8 px-2" onClick={() => openDay(r.run_date)}>See ideas</Button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Research() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.find((t) => t === params.get("tab")) ?? "briefs";
  const setTab = (t: Tab) => setParams(t === "briefs" ? {} : { tab: t }, { replace: true });
  const [day, setDay] = useState("latest");
  const [depth, setDepth] = useState("");
  const overview = useOverview();
  const settings = useQuery({ queryKey: ["research", "settings"], queryFn: () => api<{ meta: SettingsMeta }>("/research/settings"), staleTime: 60_000 });
  const canRun = can("research.manage");
  const o = overview.data;

  const run = useMutation({
    mutationFn: () => api<{ run: Run }>("/research/runs", { method: "POST", json: depth ? { depth } : {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["research", "overview"] }),
  });

  // When a run finishes, refresh the feed and jump to the newest briefing.
  const activeId = o?.active_run?.id;
  const prev = useRef<string | undefined>();
  useEffect(() => {
    if (prev.current && !activeId) {
      qc.invalidateQueries({ queryKey: ["research"] });
      setDay("latest");
    }
    prev.current = activeId;
  }, [activeId, qc]);

  const action = useMemo(() => canRun && (
    <div className="flex items-center gap-2">
      <Select aria-label="Research depth" value={depth} onChange={(e) => setDepth(e.target.value)} className="w-36">
        <option value="">Usual depth</option><option value="quick">Quick</option><option value="standard">Standard</option><option value="deep">Deep</option>
      </Select>
      <Button onClick={() => run.mutate()} disabled={run.isPending || !!o?.active_run || o?.providers.ai === false}>
        <Play size={15} /> {o?.active_run ? "Researching…" : "Run research now"}
      </Button>
    </div>
  ), [canRun, depth, run, o?.active_run, o?.providers.ai]);

  return (
    <div>
      <PageHeader title="Research & ideas"
        description="Fresh topics, posts and articles every day, researched across the web and grounded in what your company does."
        action={action} />
      {overview.isLoading && <Spinner label="Loading…" />}
      {overview.error && <Alert>{(overview.error as Error).message}</Alert>}
      {run.error && <div className="mb-4"><Alert>{(run.error as Error).message}</Alert></div>}
      {o && (
        <>
          <Briefing o={o} canRun={canRun} />
          <div className="mb-6 space-y-3">
            <ProviderNotice p={o.providers} />
          </div>
          <section className="mb-7" aria-labelledby="coming-up">
            <div className="mb-2.5 flex items-baseline justify-between">
              <h2 id="coming-up" className="font-semibold">Coming up</h2>
              <button className="text-sm text-brand hover:underline" onClick={() => setTab("occasions")}>All occasions</button>
            </div>
            <OccasionStrip today={o.today} days={30} />
          </section>
          <Tabs value={tab} onChange={setTab} items={[
            { id: "briefs", label: "Idea briefs", count: o.counts.new || undefined }, { id: "occasions", label: "Occasions" },
            { id: "history", label: "Research history" }, { id: "settings", label: "Settings" },
          ]} />
          {tab === "briefs" && <BriefsTab o={o} meta={settings.data?.meta} day={day} setDay={setDay} />}
          {tab === "occasions" && <OccasionsTab today={o.today} />}
          {tab === "history" && <HistoryTab tz={o.timezone} openDay={(d) => { setDay(d); setTab("briefs"); }} />}
          {tab === "settings" && <SettingsPanel />}
        </>
      )}
    </div>
  );
}
