import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive, AtSign, ChevronDown, CornerDownRight, Inbox as InboxIcon, MailOpen, MessagesSquare, RefreshCw, Search,
  SlidersHorizontal, Mail,
} from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, EmptyState, Input, PageHeader, Select, Spinner, Tabs } from "../../components/ui";
import { useInboxSummary } from "../../components/InboxBadge";
import Channels from "./Channels";
import Conversation from "./Conversation";
import ListeningSettings from "./ListeningSettings";
import {
  Avatar, KindChip, PlatformMark, SentimentChip, UrgentChip, ago, type InboxItem, type Person, type Status,
} from "./shared";

type Tab = "comments" | "mentions";
const PAGE = 50;

function Row({ it, active, checked, onCheck, onOpen }: {
  it: InboxItem; active: boolean; checked: boolean; onCheck: (v: boolean) => void; onOpen: () => void;
}) {
  const unread = it.status === "new";
  return (
    <li className={`group relative flex gap-3 border-b border-line px-3 py-3 transition-colors ${
      active ? "bg-primary-50" : unread ? "bg-surface hover:bg-slate-50" : "bg-canvas/40 hover:bg-slate-50"
    }`}>
      {active && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-gradient-to-b from-blue-600 to-cyan-500" />}
      <input type="checkbox" aria-label="Select" checked={checked} onChange={(e) => onCheck(e.target.checked)}
        className="mt-2.5 h-4 w-4 shrink-0 accent-[rgb(var(--brand))]" />
      <button onClick={onOpen} className="flex min-w-0 flex-1 gap-3 text-left" aria-current={active ? "true" : undefined}>
        <span className="relative shrink-0">
          <Avatar author={it.author} size={36} />
          <span className="absolute -bottom-1 -right-1 rounded-md ring-2 ring-surface"><PlatformMark platform={it.platform} label={it.platform_label} size="xs" /></span>
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className={`truncate text-[14px] ${unread ? "font-semibold text-ink" : "font-medium text-slate-700"}`}>
              {it.author.name || it.author.handle || it.platform_label}
            </span>
            {unread && <span aria-label="Unread" className="h-2 w-2 shrink-0 rounded-full bg-primary-500" />}
            <span className="ml-auto shrink-0 text-[11px] text-muted">{ago(it.published_at || it.created_at)}</span>
          </span>
          {it.title && <span className="block truncate text-[13px] font-medium text-slate-700">{it.title}</span>}
          <span className={`mt-0.5 line-clamp-2 text-[13px] leading-snug ${unread ? "text-slate-700" : "text-muted"}`}>
            {it.kind === "reply" && <CornerDownRight size={12} className="mr-1 inline text-muted" />}
            {it.text || it.title || "No text"}
          </span>
          <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {it.priority && <UrgentChip />}
            <SentimentChip s={it.sentiment} />
            <KindChip item={it} />
            {it.status === "replied" && <span className="text-[11px] font-medium text-primary-700">Replied</span>}
            {it.status === "archived" && <span className="text-[11px] text-muted">Archived</span>}
            {it.assigned_to && <span className="truncate text-[11px] text-muted">· {it.assigned_to.name}</span>}
          </span>
        </span>
      </button>
    </li>
  );
}

export default function Inbox() {
  const qc = useQueryClient();
  const { can, session } = useAuth();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "comments");
  const [f, setF] = useState({
    platform: "", account_id: "", status: "", sentiment: params.get("sentiment") ?? "", assigned: "", source: "", keyword: "", q: "",
  });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [openId, setOpenId] = useState<string | null>(params.get("item"));
  const [showChannels, setShowChannels] = useState(false);
  const [showListening, setShowListening] = useState(false);
  const [jobs, setJobs] = useState<{ comments?: string; mentions?: string }>({});
  const canAct = can("inbox.reply");

  const summary = useInboxSummary();
  const status = useQuery({ queryKey: ["inbox-status"], queryFn: () => api<Status>("/inbox/status"), staleTime: 30_000 });
  const people = useQuery({ queryKey: ["inbox-assignees"], queryFn: () => api<Person[]>("/inbox/assignees"), staleTime: 300_000 });

  const qs = new URLSearchParams({ tab, limit: String(PAGE), offset: String(page * PAGE) });
  for (const [k, v] of Object.entries(f)) if (v && !((k === "source" || k === "keyword") && tab === "comments")) qs.set(k, v);
  const list = useQuery({
    queryKey: ["inbox-items", qs.toString()],
    queryFn: () => api<{ items: InboxItem[]; total: number }>(`/inbox/items?${qs}`),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  // Opening an unread item marks it read.
  const markRead = useMutation({
    mutationFn: (ids: string[]) => api("/inbox/items/bulk", { method: "POST", json: { ids, action: "read" } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inbox-items"] }); qc.invalidateQueries({ queryKey: ["inbox-summary"] }); },
  });
  const open = (it: InboxItem) => {
    setOpenId(it.id);
    if (window.innerWidth < 1024) setTimeout(() => document.getElementById("inbox-panes")?.scrollIntoView({ block: "start" }), 0);
    if (it.status === "new" && canAct) markRead.mutate([it.id]);
  };
  useEffect(() => {
    if (openId && params.get("item") !== openId) { params.set("item", openId); setParams(params, { replace: true }); }
    if (!openId && params.get("item")) { params.delete("item"); setParams(params, { replace: true }); }
  }, [openId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setSelected([]); }, [tab, qs.toString()]); // eslint-disable-line react-hooks/exhaustive-deps

  const bulk = useMutation({
    mutationFn: (action: string) => api<{ updated: number }>("/inbox/items/bulk", { method: "POST", json: { ids: selected, action } }),
    onSuccess: () => {
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["inbox-items"] });
      qc.invalidateQueries({ queryKey: ["inbox-summary"] });
      qc.invalidateQueries({ queryKey: ["inbox-item"] });
    },
  });
  const refresh = useMutation({
    mutationFn: () => api<{ jobs: { comments?: string; mentions?: string } }>("/inbox/refresh", { method: "POST", json: { what: "all" } }),
    onSuccess: (r) => setJobs(r.jobs),
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["inbox-items"] });
    qc.invalidateQueries({ queryKey: ["inbox-summary"] });
    qc.invalidateQueries({ queryKey: ["inbox-status"] });
  };
  const j1 = useJob(jobs.comments, done);
  const j2 = useJob(jobs.mentions, done);
  const running = [j1, j2].some((j) => j && (j.status === "queued" || j.status === "running"));

  const st = status.data;
  const syncAccounts = st?.accounts.filter((a) => a.sync) ?? [];
  const problems = syncAccounts.filter((a) => a.missing_permission || a.ok === false);
  const platforms = useMemo(() => {
    const seen = new Map<string, string>();
    for (const a of st?.accounts ?? []) if (a.sync) seen.set(a.platform, a.platform_label);
    if (tab === "mentions") { seen.set("web", "Web"); seen.set("news", "News"); }
    return [...seen.entries()];
  }, [st, tab]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setPage(0); };
  const filtered = Object.entries(f).some(([k, v]) => v && !((k === "source" || k === "keyword") && tab === "comments"));
  const allChecked = items.length > 0 && items.every((i) => selected.includes(i.id));

  let empty: JSX.Element | null = null;
  if (!list.isLoading && items.length === 0) {
    if (filtered) empty = <EmptyState title="Nothing matches these filters" body="Try clearing a filter or searching for something else." />;
    else if (tab === "comments" && st && syncAccounts.length === 0)
      empty = <EmptyState title="Connect an account to see comments here"
        body="Comments and replies from Facebook, Instagram, Threads, YouTube, Bluesky and Mastodon land in this inbox. LinkedIn and Pinterest don't share comments with apps, so you answer those on the platform."
        action={<Button variant="secondary" onClick={() => setShowChannels(true)}>See how to connect</Button>} />;
    else if (tab === "mentions" && st && !st.keywords.length)
      empty = <EmptyState title="Tell us what to listen for"
        body="Add your brand and product names (for example HRP AI, hrpai, #hrpai) and we'll look for them on the web, in the news and on Bluesky, Mastodon and YouTube."
        action={can("inbox.manage") ? <Button onClick={() => setShowListening(true)}><SlidersHorizontal size={15} /> Add keywords</Button> : undefined} />;
    else empty = <EmptyState title={tab === "comments" ? "You're all caught up" : "No mentions yet"}
      body={tab === "comments" ? "New comments on your posts from the last 30 days show up here. We check every 10 minutes." : "We check the web and social networks every hour or so. New mentions show up here."} />;
  }

  return (
    <div>
      <PageHeader title="Inbox" description="Comments, replies and mentions from your accounts and the web, in one place."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setShowChannels((v) => !v)} aria-expanded={showChannels}>
              <MessagesSquare size={15} /> Channels
              {problems.length > 0 && <span className="grid h-5 min-w-5 place-items-center rounded-full bg-amber-100 px-1 text-[11px] font-semibold text-amber-800">{problems.length}</span>}
              <ChevronDown size={14} className={`transition-transform ${showChannels ? "rotate-180" : ""}`} />
            </Button>
            {canAct && (
              <Button onClick={() => refresh.mutate()} disabled={refresh.isPending || running}>
                <RefreshCw size={15} className={running ? "animate-spin" : ""} /> {running ? "Checking…" : "Refresh"}
              </Button>
            )}
          </div>
        } />

      {refresh.error && <div className="mb-4"><Alert>{(refresh.error as Error).message}</Alert></div>}
      {problems.length > 0 && !showChannels && (
        <div className="mb-4">
          <Alert tone="warn">
            {problems.length === 1 ? `${problems[0].name} needs attention` : `${problems.length} accounts need attention`}
            {problems[0].missing_permission ? ` (missing ${problems[0].missing_permission})` : ""}.{" "}
            <button className="font-medium underline" onClick={() => setShowChannels(true)}>See what to do</button>
          </Alert>
        </div>
      )}
      {showChannels && st && <Channels status={st} />}

      <Tabs<Tab> value={tab} onChange={(t) => { setTab(t); setPage(0); setOpenId(null); }} items={[
        { id: "comments", label: "Comments and replies", count: summary.data?.comments },
        { id: "mentions", label: "Mentions", count: summary.data?.mentions },
      ]} />

      {tab === "mentions" && (
        <div className="-mt-3 mb-4 flex flex-wrap items-center gap-2 text-sm">
          <AtSign size={15} className="text-muted" />
          <span className="text-muted">Listening for</span>
          {(st?.keywords ?? []).length ? st!.keywords.slice(0, 8).map((k) => (
            <button key={k} aria-pressed={f.keyword === k} title={f.keyword === k ? "Show all keywords" : `Only show ${k}`}
              onClick={() => { setF({ ...f, keyword: f.keyword === k ? "" : k }); setPage(0); }}
              className={`rounded-md px-2 py-0.5 ${f.keyword === k ? "bg-primary-700 text-white" : "bg-primary-50 text-primary-800 hover:bg-primary-100"}`}>{k}</button>
          )) : <span className="text-muted">nothing yet</span>}
          <Button variant="ghost" className="h-8 px-2.5" onClick={() => setShowListening(true)}><SlidersHorizontal size={14} /> Listening settings</Button>
        </div>
      )}

      <form className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-6" onSubmit={(e) => { e.preventDefault(); setF({ ...f, q: search.trim() }); setPage(0); }}>
        <div className="relative sm:col-span-2">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search" placeholder="Search text or name, then press Enter" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <Select aria-label="Platform" value={f.platform} onChange={set("platform")}>
          <option value="">All platforms</option>
          {platforms.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </Select>
        <Select aria-label="Status" value={f.status} onChange={set("status")}>
          <option value="">Open and replied</option>
          <option value="new">Unread</option>
          <option value="open">Not answered</option>
          <option value="replied">Replied</option>
          <option value="archived">Archived</option>
          <option value="all">Everything</option>
        </Select>
        <Select aria-label="Sentiment" value={f.sentiment} onChange={set("sentiment")}>
          <option value="">Any sentiment</option>
          <option value="urgent">Urgent or negative</option>
          <option value="question">Questions</option>
          <option value="positive">Positive</option>
          <option value="neutral">Neutral</option>
          <option value="negative">Negative</option>
        </Select>
        {tab === "mentions" ? (
          <Select aria-label="Source" value={f.source} onChange={set("source")}>
            <option value="">All mentions</option>
            <option value="account">Direct mentions</option>
            <option value="listening">From listening</option>
          </Select>
        ) : (
          <Select aria-label="Assigned to" value={f.assigned} onChange={set("assigned")}>
            <option value="">Anyone</option>
            <option value="me">Assigned to me</option>
            <option value="none">Unassigned</option>
            {(people.data ?? []).filter((p) => p.id !== session?.user.id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        )}
      </form>

      {list.error && <div className="mb-3"><Alert>{(list.error as Error).message}</Alert></div>}

      {list.isLoading ? <Spinner label="Loading the inbox…" /> : empty ?? (
        <div className="grid grid-cols-1 overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)] lg:h-[calc(100vh-7rem)] lg:min-h-[560px] lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]" id="inbox-panes">
          {/* list */}
          <div className={`flex min-h-0 flex-col border-line lg:border-r ${openId ? "hidden lg:flex" : "flex"}`}>
            <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-canvas/70 px-3">
              <input type="checkbox" aria-label="Select all on this page" checked={allChecked}
                onChange={(e) => setSelected(e.target.checked ? items.map((i) => i.id) : [])} className="h-4 w-4 accent-[rgb(var(--brand))]" />
              {selected.length > 0 && canAct ? (
                <div className="flex flex-1 items-center gap-1">
                  <span className="mr-1 text-xs font-medium">{selected.length} selected</span>
                  <Button variant="ghost" className="h-8 px-2 text-xs" onClick={() => bulk.mutate("read")}><MailOpen size={14} /> Read</Button>
                  <Button variant="ghost" className="h-8 px-2 text-xs" onClick={() => bulk.mutate("unread")}><Mail size={14} /> Unread</Button>
                  <Button variant="ghost" className="h-8 px-2 text-xs" onClick={() => bulk.mutate(f.status === "archived" ? "unarchive" : "archive")}>
                    <Archive size={14} /> {f.status === "archived" ? "Restore" : "Archive"}
                  </Button>
                </div>
              ) : (
                <span className="flex-1 text-xs text-muted">{total} {total === 1 ? "conversation" : "conversations"}{list.isFetching ? " · updating…" : ""}</span>
              )}
            </div>
            <ul className="min-h-0 flex-1 overflow-y-auto" aria-label="Inbox items">
              {items.map((it) => (
                <Row key={it.id} it={it} active={it.id === openId} checked={selected.includes(it.id)}
                  onCheck={(v) => setSelected(v ? [...selected, it.id] : selected.filter((x) => x !== it.id))} onOpen={() => open(it)} />
              ))}
            </ul>
            {total > PAGE && (
              <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
                <span>{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
                <div className="flex gap-1">
                  <Button variant="ghost" className="h-7 px-2 text-xs" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
                  <Button variant="ghost" className="h-7 px-2 text-xs" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</Button>
                </div>
              </div>
            )}
          </div>
          {/* conversation */}
          <div className={`min-h-0 min-w-0 ${openId ? "block h-[calc(100dvh-5rem)] lg:h-auto" : "hidden lg:block"}`}>
            {openId ? <Conversation id={openId} onBack={() => setOpenId(null)} people={people.data ?? []} /> : (
              <div className="grid h-full place-items-center p-10 text-center">
                <div>
                  <span className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-primary-50 to-secondary-50 text-primary-700 ring-1 ring-primary-100">
                    <InboxIcon size={22} />
                  </span>
                  <p className="font-semibold">Pick a conversation</p>
                  <p className="mx-auto mt-1 max-w-xs text-sm text-muted">
                    You'll see the original post, the whole thread and AI reply suggestions in your brand voice.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {showListening && <ListeningSettings onClose={() => setShowListening(false)} sources={st?.sources ?? []} />}
    </div>
  );
}
