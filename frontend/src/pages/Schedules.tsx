import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Play, Plus, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Textarea, Toggle,
} from "../components/ui";
import { DAY_NAMES, PlatformTag, PostStatus, tzLabel, useMeta, usePillars, useTz, type AccountBrief } from "./content/shared";

type Schedule = {
  id: string; social_account_id: string; name: string; active: boolean; days_of_week: number[]; times: string[];
  post_types: Record<string, number>; pillar_ids: string[]; mode: string; approval_window_hours: number;
  lead_hours: number; catch_up_grace_hours: number; include_link: boolean; link_url: string | null;
  instructions: string | null; account: (AccountBrief & { platform_label: string }) | null; ready: boolean;
  interval_days: number; interval_start: string | null;
};
type Slot = { at: string; local: string; date: string; blackout: boolean; within_lead: boolean; post_id: string | null; status: string | null };

const MODE_TITLES: Record<string, string> = {
  approve_first: "Approve first", auto_with_window: "Publish unless rejected", full_auto: "Fully automatic", manual: "Drafts only",
};
const MODE_ORDER = ["approve_first", "auto_with_window", "full_auto", "manual"];

type Form = Omit<Schedule, "id" | "account" | "ready">;
const blank = (accountId = ""): Form => ({
  social_account_id: accountId, name: "", active: true, days_of_week: [0, 1, 2, 3, 4], times: ["10:00"],
  post_types: { text: 1, image: 2 }, pillar_ids: [], mode: "approve_first", approval_window_hours: 4, lead_hours: 24,
  catch_up_grace_hours: 3, include_link: false, link_url: "", instructions: "", interval_days: 1, interval_start: null,
});

function Preview({ id }: { id: string }) {
  const tz = useTz();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["schedule-preview", id], queryFn: () => api<{ slots: Slot[] }>(`/content/schedules/${id}/preview?days=7`) });
  if (q.isLoading) return <Spinner label="Working out the next 7 days…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const slots = q.data?.slots ?? [];
  if (!slots.length) return <p className="text-sm text-muted">No posting times in the next 7 days.</p>;
  return (
    <ul className="grid gap-1 sm:grid-cols-2">
      {slots.map((s) => (
        <li key={s.at} className="flex items-center justify-between gap-2 rounded-md border border-line px-2.5 py-1.5 text-sm">
          <span className={s.blackout ? "text-muted line-through" : ""}>{s.local} <span className="text-muted">{tzLabel(tz)}</span></span>
          {s.blackout ? <Badge>No-post day</Badge> : s.post_id ? (
            <button onClick={() => nav(`/posts/${s.post_id}`)} className="hover:opacity-80"><PostStatus status={s.status ?? "planned"} /></button>
          ) : <span className="text-xs text-muted">{s.within_lead ? "Planned soon" : "Not planned yet"}</span>}
        </li>
      ))}
    </ul>
  );
}

function ScheduleForm({ existing, accounts, onClose }: { existing?: Schedule; accounts: AccountBrief[]; onClose: () => void }) {
  const qc = useQueryClient();
  const tz = useTz();
  const meta = useMeta();
  const pillars = usePillars();
  const [f, setF] = useState<Form>(() => existing ? { ...existing, link_url: existing.link_url ?? "", instructions: existing.instructions ?? "" } : blank(accounts[0]?.id));
  const account = accounts.find((a) => a.id === f.social_account_id);
  const platform = meta.data?.platforms.find((p) => p.id === account?.platform);
  const supported = platform?.post_types ?? ["text"];
  const typeLabels = meta.data?.post_types ?? {};
  const totalWeight = supported.reduce((s, t) => s + (f.post_types[t] ?? 0), 0);

  const save = useMutation({
    mutationFn: () => {
      const json = {
        ...f, link_url: f.include_link ? f.link_url : (f.link_url || null), instructions: f.instructions || null,
        post_types: Object.fromEntries(Object.entries(f.post_types).filter(([t, w]) => supported.includes(t) && w > 0)),
      };
      return existing ? api<Schedule>(`/content/schedules/${existing.id}`, { method: "PATCH", json }) : api<Schedule>("/content/schedules", { method: "POST", json });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["schedules"] }); qc.invalidateQueries({ queryKey: ["schedule-preview"] }); onClose(); },
  });

  const pickAccount = (id: string) => {
    const a = accounts.find((x) => x.id === id);
    const p = meta.data?.platforms.find((x) => x.id === a?.platform);
    const types = Object.fromEntries(Object.entries(f.post_types).filter(([t]) => p?.post_types.includes(t)));
    if (!Object.keys(types).length && p) types[p.post_types[0]] = 1;
    setF({ ...f, social_account_id: id, post_types: types, name: f.name || `${a?.name ?? ""}` });
  };
  const toggleDay = (d: number) => setF({ ...f, days_of_week: f.days_of_week.includes(d) ? f.days_of_week.filter((x) => x !== d) : [...f.days_of_week, d].sort() });
  const setTime = (i: number, v: string) => setF({ ...f, times: f.times.map((t, n) => (n === i ? v : t)) });

  return (
    <Modal title={existing ? `Edit ${existing.name}` : "New posting schedule"} onClose={onClose} wide>
      <form className="max-h-[70vh] space-y-5 overflow-y-auto pr-1" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Social account">
            <Select required value={f.social_account_id} onChange={(e) => pickAccount(e.target.value)}>
              <option value="" disabled>Choose an account</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name} · {a.platform_label ?? a.platform}{a.status !== "connected" && a.status !== "manual" ? ` (${a.status})` : ""}</option>
              ))}
            </Select>
          </Field>
          <Field label="Schedule name"><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="LinkedIn weekdays" /></Field>
        </div>

        <Field label="Repeat" hint={f.interval_days > 1 ? `Posts on every ${f.interval_days === 2 ? "second" : `${f.interval_days}th`} day, counted from ${f.interval_start || "today"}, on the days ticked below.` : "Posts on every day ticked below."}>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={String(f.interval_days || 1)} className="w-48"
              onChange={(e) => { const n = Number(e.target.value); setF({ ...f, interval_days: n, interval_start: n > 1 ? (f.interval_start || new Date().toISOString().slice(0, 10)) : null, days_of_week: n > 1 ? [0, 1, 2, 3, 4, 5, 6] : f.days_of_week }); }}>
              <option value="1">On the days below</option>
              <option value="2">Every 2 days</option>
              <option value="3">Every 3 days</option>
              <option value="7">Once a week</option>
            </Select>
            {f.interval_days > 1 && <Input type="date" aria-label="Starting from" className="w-44" value={f.interval_start ?? ""} onChange={(e) => setF({ ...f, interval_start: e.target.value || null })} />}
          </div>
        </Field>

        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Days</legend>
          <div className="flex flex-wrap gap-1.5">
            {DAY_NAMES.map((d, i) => (
              <button type="button" key={d} aria-pressed={f.days_of_week.includes(i)} onClick={() => toggleDay(i)}
                className={`h-9 w-12 rounded-lg border text-sm ${f.days_of_week.includes(i) ? "border-primary-600 bg-primary-600 text-white" : "border-line bg-surface hover:border-brand/50"}`}>
                {d}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Posting times ({tzLabel(tz)})</legend>
          <div className="flex flex-wrap items-center gap-2">
            {f.times.map((t, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                <Input type="time" aria-label={`Time ${i + 1}`} required value={t} onChange={(e) => setTime(i, e.target.value)} className="w-32" />
                {f.times.length > 1 && (
                  <button type="button" aria-label="Remove time" onClick={() => setF({ ...f, times: f.times.filter((_, n) => n !== i) })} className="text-muted hover:text-danger"><X size={16} /></button>
                )}
              </span>
            ))}
            <Button type="button" variant="secondary" onClick={() => setF({ ...f, times: [...f.times, "17:30"] })}><Plus size={15} /> Add time</Button>
          </div>
          <p className="mt-1 text-xs text-muted">{f.interval_days > 1 ? `About ${Math.round(f.times.length * f.days_of_week.length / f.interval_days * 30 / 7)} posts a month.` : `${f.times.length * f.days_of_week.length} posts a week.`}</p>
        </fieldset>

        <fieldset>
          <legend className="mb-1 text-sm font-medium">Post types and mix</legend>
          <p className="mb-2 text-xs text-muted">{account ? `Types ${platform?.label ?? account.platform} accepts. A higher number means more of that type; 0 turns it off.` : "Choose an account first."}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {supported.map((t) => {
              const w = f.post_types[t] ?? 0;
              return (
                <label key={t} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
                  <span className="text-sm">{typeLabels[t] ?? t}{w > 0 && totalWeight > 0 && <span className="ml-1 text-xs text-muted">{Math.round((w / totalWeight) * 100)}%</span>}</span>
                  <Input type="number" min={0} max={10} value={w} className="w-20"
                    onChange={(e) => setF({ ...f, post_types: { ...f.post_types, [t]: Math.max(0, Math.min(10, Number(e.target.value) || 0)) } })} />
                </label>
              );
            })}
          </div>
        </fieldset>

        <fieldset>
          <legend className="mb-1 text-sm font-medium">Topic pillars</legend>
          <p className="mb-2 text-xs text-muted">Leave all unticked to use every active pillar.</p>
          {pillars.data?.length ? (
            <div className="flex flex-wrap gap-2">
              {pillars.data.filter((p) => p.active).map((p) => {
                const on = f.pillar_ids.includes(p.id);
                return (
                  <label key={p.id} className={`inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1 text-sm ${on ? "border-primary-600 bg-primary-50" : "border-line"}`}>
                    <input type="checkbox" className="sr-only" checked={on}
                      onChange={() => setF({ ...f, pillar_ids: on ? f.pillar_ids.filter((x) => x !== p.id) : [...f.pillar_ids, p.id] })} />
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.color }} /> {p.name}
                  </label>
                );
              })}
            </div>
          ) : <p className="text-sm text-muted">No pillars yet. <Link className="text-brand underline" to="/settings/brand">Add topic pillars</Link>.</p>}
        </fieldset>

        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">How posts go out</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {MODE_ORDER.map((m) => (
              <label key={m} className={`cursor-pointer rounded-lg border px-3 py-2.5 ${f.mode === m ? "border-primary-600 bg-primary-50" : "border-line hover:border-brand/40"}`}>
                <input type="radio" name="mode" className="sr-only" checked={f.mode === m} onChange={() => setF({ ...f, mode: m })} />
                <span className="block text-sm font-medium">{MODE_TITLES[m]}</span>
                <span className="block text-xs text-muted">{meta.data?.modes[m]}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-3">
          {f.mode === "auto_with_window" && (
            <Field label="Review window (hours)" hint="Time reviewers get to reject a post.">
              <Input type="number" min={1} max={168} value={f.approval_window_hours} onChange={(e) => setF({ ...f, approval_window_hours: Number(e.target.value) })} />
            </Field>
          )}
          <Field label="Write posts ahead (hours)" hint="How early each post is written.">
            <Input type="number" min={1} max={168} value={f.lead_hours} onChange={(e) => setF({ ...f, lead_hours: Number(e.target.value) })} />
          </Field>
          <Field label="Late posting limit (hours)" hint="If the laptop was off, still post this late. After that the post is marked missed.">
            <Input type="number" min={0} max={48} value={f.catch_up_grace_hours} onChange={(e) => setF({ ...f, catch_up_grace_hours: Number(e.target.value) })} />
          </Field>
        </div>

        <div className="space-y-2">
          <Toggle checked={f.include_link} onChange={(v) => setF({ ...f, include_link: v })} label="Include a link in every post" />
          {f.include_link && (
            <Input type="url" required placeholder="https://hrpai.in/products/hrms" value={f.link_url ?? ""} onChange={(e) => setF({ ...f, link_url: e.target.value })} />
          )}
        </div>
        <Field label="Extra instructions for the AI (optional)" hint="For example: focus on small businesses; end with a question.">
          <Textarea rows={3} value={f.instructions ?? ""} onChange={(e) => setF({ ...f, instructions: e.target.value })} />
        </Field>
        <Toggle checked={f.active} onChange={(v) => setF({ ...f, active: v })} label="Schedule is on" />
        {existing && (
          <div>
            <div className="mb-2 text-sm font-medium">Next 7 days (saved settings)</div>
            <Preview id={existing.id} />
          </div>
        )}
        <div className="sticky bottom-0 flex justify-end gap-2 bg-surface pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending || !f.social_account_id}>{existing ? "Save changes" : "Create schedule"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function ScheduleCard({ s, canManage, canRun, onEdit }: { s: Schedule; canManage: boolean; canRun: boolean; onEdit: () => void }) {
  const qc = useQueryClient();
  const tz = useTz();
  const nav = useNavigate();
  const meta = useMeta();
  const [showPreview, setShowPreview] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["schedules"] });
  const toggle = useMutation({ mutationFn: (active: boolean) => api(`/content/schedules/${s.id}`, { method: "PATCH", json: { active } }), onSuccess: refresh });
  const run = useMutation({
    mutationFn: () => api<{ post_id: string }>(`/content/schedules/${s.id}/run-now`, { method: "POST" }),
    onSuccess: (r) => nav(`/posts/${r.post_id}`),
  });
  const remove = useMutation({ mutationFn: () => api(`/content/schedules/${s.id}`, { method: "DELETE" }), onSuccess: refresh });
  const days = (s.interval_days ?? 1) > 1 ? `Every ${s.interval_days} days` : s.days_of_week.length === 7 ? "Every day" : s.days_of_week.length === 5 && !s.days_of_week.includes(5) && !s.days_of_week.includes(6) ? "Weekdays" : s.days_of_week.map((d) => DAY_NAMES[d]).join(", ");
  const types = Object.entries(s.post_types).filter(([, w]) => w > 0).map(([t]) => meta.data?.post_types[t] ?? t).join(", ");
  const err = toggle.error || run.error || remove.error;
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{s.name}</span>
            <Badge tone={s.mode === "full_auto" ? "accent" : "neutral"}>{MODE_TITLES[s.mode] ?? s.mode}</Badge>
            {!s.active && <Badge>Off</Badge>}
            {s.active && !s.ready && <Badge tone="danger">Account not connected</Badge>}
          </div>
          <div className="mt-0.5 text-sm text-muted">
            {days} at {s.times.join(", ")} {tzLabel(tz)} · {types}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {canManage && <Toggle checked={s.active} onChange={(v) => toggle.mutate(v)} label="" />}
          <Button variant="ghost" onClick={() => setShowPreview(!showPreview)}><CalendarClock size={15} /> {showPreview ? "Hide" : "Next 7 days"}</Button>
          {canRun && <Button variant="secondary" disabled={run.isPending || !s.ready} onClick={() => run.mutate()} title="Write the post for the next free slot now"><Play size={15} /> {run.isPending ? "Starting…" : "Run now"}</Button>}
          {canManage && <Button variant="ghost" onClick={onEdit}>Edit</Button>}
          {canManage && (
            <Button variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => { if (confirm(`Delete “${s.name}”? Posts already written are kept.`)) remove.mutate(); }}><Trash2 size={15} /></Button>
          )}
        </div>
      </div>
      {err && <div className="mt-2"><Alert>{(err as Error).message}</Alert></div>}
      {showPreview && <div className="mt-3"><Preview id={s.id} /></div>}
    </li>
  );
}

export default function Schedules() {
  const { can } = useAuth();
  const canManage = can("schedule.manage");
  const canRun = canManage && can("content.create");
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: () => api<Schedule[]>("/content/schedules") });
  const accounts = useQuery({ queryKey: ["social-accounts"], queryFn: () => api<AccountBrief[]>("/social/accounts"), retry: false });
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);

  const groups = useMemo(() => {
    const m = new Map<string, { account: Schedule["account"]; items: Schedule[] }>();
    for (const s of schedules.data ?? []) {
      const key = s.social_account_id;
      if (!m.has(key)) m.set(key, { account: s.account, items: [] });
      m.get(key)!.items.push(s);
    }
    return [...m.values()];
  }, [schedules.data]);

  const accountList = accounts.data ?? [];
  return (
    <div>
      <PageHeader
        title="Posting schedules"
        description="When each account posts, what kinds of posts, and whether they need approval."
        action={canManage && <Button onClick={() => setEditing("new")} disabled={!accountList.length}><Plus size={16} /> New schedule</Button>}
      />
      {accounts.error && (
        <div className="mb-4"><Alert>Couldn't load social accounts: {(accounts.error as Error).message}</Alert></div>
      )}
      {!accounts.isLoading && !accounts.error && !accountList.length && (
        <div className="mb-4"><Alert tone="info">Connect a social account first in <Link className="underline" to="/settings/accounts">Social accounts</Link>. You can also add an account you post to by hand.</Alert></div>
      )}
      {schedules.error && <Alert>{(schedules.error as Error).message}</Alert>}
      {schedules.isLoading ? <Spinner label="Loading schedules…" /> : groups.length === 0 ? (
        <EmptyState title="No schedules yet" body="Create a schedule to have posts written and queued automatically, for example LinkedIn at 10:00 and 17:30 on weekdays."
          action={canManage && accountList.length > 0 && <Button onClick={() => setEditing("new")}><Plus size={16} /> New schedule</Button>} />
      ) : (
        <div className="space-y-4">
          {groups.map((g) => (
            <Card key={g.account?.id ?? g.items[0].id} className="py-2">
              <div className="flex items-center gap-2 border-b border-line py-2">
                <span className="font-semibold">{g.account?.name ?? "Removed account"}</span>
                {g.account && <PlatformTag label={g.account.platform_label} />}
                {g.account && g.account.status !== "connected" && <Badge tone={g.account.status === "manual" ? "neutral" : "danger"}>{g.account.status === "manual" ? "Posted by hand" : g.account.status}</Badge>}
              </div>
              <ul className="divide-y divide-line">
                {g.items.map((s) => <ScheduleCard key={s.id} s={s} canManage={canManage} canRun={canRun} onEdit={() => setEditing(s)} />)}
              </ul>
            </Card>
          ))}
        </div>
      )}
      {editing && (
        <ScheduleForm existing={editing === "new" ? undefined : editing}
          accounts={accountList.length ? accountList : (editing !== "new" && editing.account ? [editing.account] : [])}
          onClose={() => setEditing(null)} />
      )}
    </div>
  );
}
