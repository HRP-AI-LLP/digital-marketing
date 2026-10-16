import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, PageHeader, Select, Spinner } from "../components/ui";
import { DAY_NAMES, STATUS_LABELS, fmtTime, partsIn, tzLabel, useContentAccounts, useMeta, useTz } from "./content/shared";

type CalItem = {
  id: string; at: string; status: string; platform: string; platform_label: string; post_type: string;
  account_name: string | null; account_id: string | null; title: string; pillar_name: string | null; color: string;
};

// Calendar maths on plain YYYY-MM-DD strings (no time zone involved).
const toKey = (d: Date) => d.toISOString().slice(0, 10);
const fromKey = (k: string) => new Date(`${k}T00:00:00Z`);
const addDays = (k: string, n: number) => { const d = fromKey(k); d.setUTCDate(d.getUTCDate() + n); return toKey(d); };
const weekday = (k: string) => (fromKey(k).getUTCDay() + 6) % 7; // Mon=0
const startOfWeek = (k: string) => addDays(k, -weekday(k));
const startOfMonth = (k: string) => `${k.slice(0, 8)}01`;
const addMonths = (k: string, n: number) => {
  const d = fromKey(startOfMonth(k));
  d.setUTCMonth(d.getUTCMonth() + n);
  return toKey(d);
};

const STATUS_DOT: Record<string, string> = {
  published: "bg-emerald-500", scheduled: "bg-primary-500", approved: "bg-primary-500", in_review: "bg-amber-500",
  draft: "bg-slate-400", planned: "bg-slate-300", generating: "bg-slate-300", failed: "bg-red-500", missed: "bg-red-500",
  rejected: "bg-red-300", manual_pending: "bg-violet-500", publishing: "bg-primary-300",
};

function Chip({ item, tz, onOpen }: { item: CalItem; tz: string; onOpen: () => void }) {
  const faded = ["rejected", "missed", "failed"].includes(item.status);
  return (
    <button onClick={onOpen} title={`${item.title}\n${item.account_name ?? ""} · ${STATUS_LABELS[item.status] ?? item.status}${item.pillar_name ? ` · ${item.pillar_name}` : ""}`}
      className={`flex w-full items-center gap-1.5 truncate rounded-md border-l-[3px] bg-surface px-1.5 py-1 text-left text-xs shadow-sm ring-1 ring-line hover:ring-brand/50 ${faded ? "opacity-60" : ""}`}
      style={{ borderLeftColor: item.color }}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[item.status] ?? "bg-slate-300"}`} aria-hidden />
      <span className="shrink-0 tabular-nums text-muted">{fmtTime(item.at, tz)}</span>
      <span className="shrink-0 font-medium">{item.platform_label.split(" ")[0]}</span>
      <span className="truncate">{item.title}</span>
    </button>
  );
}

export default function Calendar() {
  const tz = useTz();
  const nav = useNavigate();
  const { can } = useAuth();
  const meta = useMeta();
  const accounts = useContentAccounts();
  const today = partsIn(new Date(), tz).date;
  const [view, setView] = useState<"month" | "week">("month");
  const [anchor, setAnchor] = useState(today);
  const [account, setAccount] = useState("");
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");

  const { start, end, days } = useMemo(() => {
    if (view === "week") {
      const s = startOfWeek(anchor);
      return { start: s, end: addDays(s, 7), days: Array.from({ length: 7 }, (_, i) => addDays(s, i)) };
    }
    const first = startOfMonth(anchor);
    const s = startOfWeek(first);
    const lastOfMonth = addDays(addMonths(first, 1), -1);
    const e = addDays(startOfWeek(lastOfMonth), 7);
    const n = Math.round((fromKey(e).getTime() - fromKey(s).getTime()) / 86400000);
    return { start: s, end: e, days: Array.from({ length: n }, (_, i) => addDays(s, i)) };
  }, [view, anchor]);

  const params = new URLSearchParams({ start: `${start}T00:00:00`, end: `${end}T00:00:00` });
  if (account) params.set("account_id", account);
  if (platform) params.set("platform", platform);
  if (status) params.set("status", status);
  const items = useQuery({ queryKey: ["calendar", params.toString()], queryFn: () => api<CalItem[]>(`/content/calendar?${params}`), refetchInterval: 60_000 });

  const byDay = useMemo(() => {
    const m = new Map<string, CalItem[]>();
    for (const it of items.data ?? []) {
      const k = partsIn(new Date(it.at), tz).date;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(it);
    }
    return m;
  }, [items.data, tz]);

  const month = anchor.slice(0, 7);
  const title = view === "month"
    ? new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" }).format(fromKey(startOfMonth(anchor)))
    : `${new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" }).format(fromKey(start))} – ${new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(fromKey(addDays(end, -1)))}`;
  const move = (n: number) => setAnchor(view === "month" ? addMonths(anchor, n) : addDays(anchor, 7 * n));
  const platforms = [...new Set((accounts.data ?? []).map((a) => a.platform))];

  return (
    <div>
      <PageHeader title="Content calendar" description={`Everything planned, waiting and published. Times are ${tzLabel(tz)}.`}
        action={can("content.create") && <Button onClick={() => nav("/posts/new")}><Plus size={16} /> New post</Button>} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <Button variant="secondary" aria-label="Previous" onClick={() => move(-1)}><ChevronLeft size={16} /></Button>
          <Button variant="secondary" onClick={() => setAnchor(today)}>Today</Button>
          <Button variant="secondary" aria-label="Next" onClick={() => move(1)}><ChevronRight size={16} /></Button>
        </div>
        <h2 className="mx-2 text-lg font-semibold">{title}</h2>
        {items.isFetching && <Spinner />}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select aria-label="Account" value={account} onChange={(e) => setAccount(e.target.value)} className="w-44">
            <option value="">All accounts</option>
            {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
          <Select aria-label="Platform" value={platform} onChange={(e) => setPlatform(e.target.value)} className="w-44">
            <option value="">All platforms</option>
            {platforms.map((p) => <option key={p} value={p}>{meta.data?.platforms.find((x) => x.id === p)?.label ?? p}</option>)}
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).filter(([k]) => k !== "skipped").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </Select>
          <div role="tablist" className="flex rounded-lg border border-line p-0.5">
            {(["month", "week"] as const).map((v) => (
              <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
                className={`rounded-md px-3 py-1 text-sm ${view === v ? "bg-primary-600 text-white" : "text-muted hover:text-ink"}`}>
                {v === "month" ? "Month" : "Week"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {items.error && <div className="mb-3"><Alert>{(items.error as Error).message}</Alert></div>}

      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <div className="grid min-w-[760px] grid-cols-7 border-b border-line bg-canvas text-xs font-medium text-muted">
          {DAY_NAMES.map((d) => <div key={d} className="px-2 py-1.5">{d}</div>)}
        </div>
        <div className="grid min-w-[760px] grid-cols-7">
          {days.map((d) => {
            const list = byDay.get(d) ?? [];
            const outside = view === "month" && d.slice(0, 7) !== month;
            const isToday = d === today;
            const limit = view === "month" ? 4 : 50;
            return (
              <div key={d} className={`border-b border-r border-line p-1.5 ${view === "week" ? "min-h-[420px]" : "min-h-[120px]"} ${outside ? "bg-canvas/60" : ""}`}>
                <div className={`mb-1 flex items-center justify-between text-xs ${outside ? "text-muted/60" : "text-muted"}`}>
                  <span className={isToday ? "grid h-6 w-6 place-items-center rounded-full bg-primary-600 font-semibold text-white" : "px-1"}>
                    {Number(d.slice(8))}
                  </span>
                  {list.length > 0 && <span>{list.length}</span>}
                </div>
                <div className="space-y-1">
                  {list.slice(0, limit).map((it) => <Chip key={it.id} item={it} tz={tz} onOpen={() => nav(`/posts/${it.id}`)} />)}
                  {list.length > limit && (
                    <button className="w-full text-left text-xs text-brand hover:underline" onClick={() => { setAnchor(d); setView("week"); }}>
                      +{list.length - limit} more
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted">
        {["scheduled", "in_review", "published", "manual_pending", "failed", "draft"].map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-full ${STATUS_DOT[s]}`} />{STATUS_LABELS[s]}</span>
        ))}
        <span>· The coloured edge shows the topic pillar.</span>
      </div>
    </div>
  );
}
