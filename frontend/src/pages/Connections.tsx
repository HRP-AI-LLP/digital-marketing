import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, RotateCcw, Send, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useJob } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Toggle, formatDate,
} from "../components/ui";

type EventInfo = { event: string; label: string };
type Hook = {
  id: string; name: string; url: string; events: string[]; active: boolean; last_status: "ok" | "failed" | null;
  last_status_code: number | null; last_delivery_at: string | null; failure_count: number; disabled_reason: string | null;
  created_at: string; deliveries_30d: number; success_rate: number | null; secret?: string;
};
type Delivery = {
  id: string; event: string; event_id: string; status_code: number | null; ok: boolean; attempt: number;
  duration_ms: number | null; error: string | null; redelivery: boolean; created_at: string;
  payload?: Record<string, unknown>; response_body?: string | null;
};
type Token = {
  id: string; name: string; prefix: string; hint: string; scopes: string[]; user_id: string; created_by: string | null;
  created_at: string; last_used_at: string | null; expires_at: string | null; revoked_at: string | null;
  state: "active" | "expired" | "revoked"; token?: string;
};
type Scope = { code: string; group: string; description: string };
type Tab = "webhooks" | "tokens" | "api";

function errMsg(e: unknown) { return e ? (e as Error).message : null; }

function OneTimeSecret({ label, value, note }: { label: string; value: string; note: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-lg border border-secondary-300 bg-accent-tint px-3 py-2.5">
      <div className="text-sm font-medium">{label}</div>
      <div className="mt-1 flex items-start gap-2">
        <code className="min-w-0 flex-1 font-mono text-sm [overflow-wrap:anywhere]">{value}</code>
        <button type="button" className="inline-flex shrink-0 items-center gap-1 text-sm text-brand underline"
          onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); }}>
          <Copy size={14} /> {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="mt-1 text-xs text-muted">{note}</div>
    </div>
  );
}

// ====================================================================== webhooks
function EventPicker({ events, value, onChange }: { events: EventInfo[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium">Send a message when…</legend>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {events.map((e) => (
          <label key={e.event} className="flex items-start gap-2 rounded-md border border-line px-3 py-2 hover:border-brand/40">
            <input type="checkbox" className="mt-1 accent-[rgb(var(--brand))]" checked={value.includes(e.event)}
              onChange={(ev) => onChange(ev.target.checked ? [...value, e.event] : value.filter((x) => x !== e.event))} />
            <span className="min-w-0">
              <span className="block text-sm">{e.label}</span>
              <code className="block text-xs text-muted">{e.event}</code>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function HookForm({ hook, events, onClose }: { hook?: Hook; events: EventInfo[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(hook?.name ?? "");
  const [url, setUrl] = useState(hook?.url ?? "");
  const [chosen, setChosen] = useState<string[]>(hook?.events ?? ["post.published"]);
  const [active, setActive] = useState(hook?.active ?? true);
  const [secret, setSecret] = useState<string | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["webhooks"] });
  const save = useMutation({
    mutationFn: () => hook
      ? api<Hook>(`/connections/webhooks/${hook.id}`, { method: "PATCH", json: { name, url, events: chosen, active } })
      : api<Hook>("/connections/webhooks", { method: "POST", json: { name, url, events: chosen, active } }),
    onSuccess: (h) => { done(); if (h.secret) setSecret(h.secret); else onClose(); },
  });
  const rotate = useMutation({
    mutationFn: () => api<{ secret: string }>(`/connections/webhooks/${hook!.id}/rotate-secret`, { method: "POST" }),
    onSuccess: (r) => setSecret(r.secret),
  });

  if (secret) {
    return (
      <Modal title={hook ? "New signing secret" : "Webhook added"} onClose={onClose}>
        <div className="space-y-4">
          <p className="text-sm">Every message is signed with this secret, so the receiving tool can check it really came from the studio.
            Paste it into your Zapier, Make or n8n step if it checks signatures.</p>
          <OneTimeSecret label="Signing secret" value={secret}
            note="Shown once. If you lose it, make a new one from Edit → Replace secret." />
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title={hook ? `Edit ${hook.name}` : "Add webhook"} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {(save.error || rotate.error) && <Alert>{errMsg(save.error || rotate.error)}</Alert>}
        {hook?.disabled_reason && !hook.active && <Alert tone="warn">{hook.disabled_reason}</Alert>}
        <Field label="Name" hint="Something to recognise it by, e.g. “Zapier: new posts to Slack”.">
          <Input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Address (URL)" hint="Copy it from your Zapier “Catch Hook”, Make “Custom webhook” or n8n “Webhook” step. It must start with https://">
          <Input required type="url" placeholder="https://hooks.zapier.com/hooks/catch/…" value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <EventPicker events={events} value={chosen} onChange={setChosen} />
        <Toggle checked={active} onChange={setActive} label="Turned on" />
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
          <div>
            {hook && (
              <Button type="button" variant="secondary" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
                <RotateCcw size={15} /> Replace secret
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={save.isPending || !chosen.length}>{save.isPending ? "Saving…" : hook ? "Save" : "Add webhook"}</Button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

function DeliveryRow({ d, hookId, canManage }: { d: Delivery; hookId: string; canManage: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const again = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/connections/webhooks/${hookId}/deliveries/${d.id}/redeliver`, { method: "POST" }),
    onSuccess: (r) => setJobId(r.job_id),
  });
  useJob(jobId, () => { setJobId(null); qc.invalidateQueries({ queryKey: ["deliveries", hookId] }); qc.invalidateQueries({ queryKey: ["webhooks"] }); });
  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={d.ok ? "brand" : "danger"}>{d.ok ? `OK ${d.status_code ?? ""}` : d.status_code ? `Failed ${d.status_code}` : "Failed"}</Badge>
        <code className="text-sm">{d.event}</code>
        <span className="text-xs text-muted">
          {formatDate(d.created_at)} · try {d.attempt}{d.redelivery ? " · sent again" : ""}{d.duration_ms != null ? ` · ${d.duration_ms} ms` : ""}
        </span>
        <span className="ml-auto flex gap-1">
          <Button variant="ghost" className="h-7 px-2" onClick={() => setOpen(!open)}>{open ? "Hide" : "Details"}</Button>
          {canManage && (
            <Button variant="ghost" className="h-7 px-2" disabled={again.isPending || !!jobId} onClick={() => again.mutate()}>
              {jobId ? <Spinner /> : <><Send size={14} /> Send again</>}
            </Button>
          )}
        </span>
      </div>
      {d.error && <p className="mt-1 text-sm text-danger [overflow-wrap:anywhere]">{d.error}</p>}
      {again.error && <p className="mt-1 text-sm text-danger">{errMsg(again.error)}</p>}
      {open && (
        <div className="mt-2 space-y-2">
          <div className="text-xs font-medium text-muted">What was sent</div>
          <pre className="max-h-72 overflow-auto rounded-md bg-ink/5 p-2 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
            {JSON.stringify(d.payload, null, 2)}
          </pre>
          {d.response_body && (
            <>
              <div className="text-xs font-medium text-muted">What the other side answered</div>
              <pre className="max-h-40 overflow-auto rounded-md bg-ink/5 p-2 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">{d.response_body}</pre>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function Deliveries({ hook, onClose, canManage }: { hook: Hook; onClose: () => void; canManage: boolean }) {
  const q = useQuery({
    queryKey: ["deliveries", hook.id],
    queryFn: () => api<{ items: Delivery[] }>(`/connections/webhooks/${hook.id}/deliveries?limit=50`),
    refetchInterval: 10000,
  });
  return (
    <Modal title={`Recent deliveries: ${hook.name}`} onClose={onClose} wide>
      {q.isLoading ? <Spinner label="Loading…" /> : q.error ? <Alert>{errMsg(q.error)}</Alert> : !q.data?.items.length ? (
        <EmptyState title="Nothing sent yet" body="Press “Send test”, or wait for one of the chosen events to happen." />
      ) : (
        <>
          <p className="text-sm text-muted">The last 50 attempts (kept for 30 days). Failed messages are tried again up to 5 times, waiting longer each time.</p>
          <ul className="divide-y divide-line">{q.data.items.map((d) => <DeliveryRow key={d.id} d={d} hookId={hook.id} canManage={canManage} />)}</ul>
        </>
      )}
    </Modal>
  );
}

function HookCard({ hook, events, canManage }: { hook: Hook; events: EventInfo[]; canManage: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const labels = Object.fromEntries(events.map((e) => [e.event, e.label]));
  const test = useMutation({
    mutationFn: () => api<{ job_id: string }>(`/connections/webhooks/${hook.id}/test`, { method: "POST" }),
    onSuccess: (r) => { setTestResult(null); setJobId(r.job_id); },
  });
  useJob(jobId, (j) => {
    setJobId(null);
    const r = (j.result ?? {}) as { ok?: boolean; status_code?: number; error?: string };
    setTestResult(j.status === "done" && r.ok
      ? { ok: true, text: `It works: the other side answered ${r.status_code}.` }
      : { ok: false, text: r.error || j.error || "The test didn't go through." });
    qc.invalidateQueries({ queryKey: ["webhooks"] });
  });
  const remove = useMutation({
    mutationFn: () => api(`/connections/webhooks/${hook.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }),
  });

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{hook.name}</h3>
            {hook.active ? <Badge tone="brand">On</Badge> : <Badge tone="danger">Off</Badge>}
            {hook.last_status === "failed" && hook.active && <Badge tone="danger">Last try failed</Badge>}
          </div>
          <div className="mt-0.5 text-sm text-muted [overflow-wrap:anywhere]">{hook.url}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canManage && (
            <Button variant="secondary" disabled={test.isPending || !!jobId} onClick={() => test.mutate()}>
              {jobId ? <Spinner label="Sending…" /> : <><Send size={15} /> Send test</>}
            </Button>
          )}
          <Button variant="secondary" onClick={() => setShowLog(true)}>Deliveries</Button>
          {canManage && <Button variant="secondary" onClick={() => setEditing(true)}>Edit</Button>}
          {canManage && (
            <Button variant="danger" aria-label="Remove webhook" disabled={remove.isPending}
              onClick={() => confirm(`Remove “${hook.name}”? Tools using it will stop receiving messages.`) && remove.mutate()}>
              <Trash2 size={15} />
            </Button>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {hook.events.map((e) => <Badge key={e}>{labels[e] ?? e}</Badge>)}
      </div>
      <div className="mt-3 text-sm text-muted">
        {hook.last_delivery_at
          ? <>Last message {formatDate(hook.last_delivery_at)}{hook.last_status_code ? ` (answer ${hook.last_status_code})` : ""} · </>
          : <>Nothing sent yet · </>}
        {hook.deliveries_30d} sent in 30 days{hook.success_rate != null && <> · {hook.success_rate}% delivered</>}
        {hook.failure_count > 0 && hook.active && <> · {hook.failure_count} failed in a row (turns off at 20)</>}
      </div>
      {!hook.active && hook.disabled_reason && <div className="mt-3"><Alert tone="warn">{hook.disabled_reason}</Alert></div>}
      {testResult && <div className="mt-3"><Alert tone={testResult.ok ? "info" : "danger"}>{testResult.text}</Alert></div>}
      {(test.error || remove.error) && <div className="mt-3"><Alert>{errMsg(test.error || remove.error)}</Alert></div>}
      {editing && <HookForm hook={hook} events={events} onClose={() => setEditing(false)} />}
      {showLog && <Deliveries hook={hook} canManage={canManage} onClose={() => setShowLog(false)} />}
    </Card>
  );
}

function WebhooksTab() {
  const { can } = useAuth();
  const canManage = can("connections.manage");
  const [adding, setAdding] = useState(false);
  const events = useQuery({ queryKey: ["webhook-events"], queryFn: () => api<{ items: EventInfo[] }>("/connections/events"), staleTime: Infinity });
  const hooks = useQuery({ queryKey: ["webhooks"], queryFn: () => api<{ items: Hook[] }>("/connections/webhooks"), refetchInterval: 30000 });
  const ev = events.data?.items ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          A webhook is an address the studio calls the moment something happens, for example when a post is published or a
          comment needs a reply. Use it to start a Zap, a Make scenario or an n8n workflow.
        </p>
        {canManage && <Button onClick={() => setAdding(true)}><Plus size={16} /> Add webhook</Button>}
      </div>
      {hooks.error && <Alert>{errMsg(hooks.error)}</Alert>}
      {hooks.isLoading ? <Spinner label="Loading…" /> : !hooks.data?.items.length ? (
        <EmptyState title="No webhooks yet" body="Add the address from your automation tool and choose which events it should hear about."
          action={canManage ? <Button onClick={() => setAdding(true)}><Plus size={16} /> Add webhook</Button> : undefined} />
      ) : hooks.data.items.map((h) => <HookCard key={h.id} hook={h} events={ev} canManage={canManage} />)}
      {adding && ev.length > 0 && <HookForm events={ev} onClose={() => setAdding(false)} />}
    </div>
  );
}

// ====================================================================== API tokens
function TokenForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const scopes = useQuery({ queryKey: ["token-scopes"], queryFn: () => api<{ items: Scope[] }>("/connections/scopes") });
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [chosen, setChosen] = useState<string[]>(["content.view"]);
  const [created, setCreated] = useState<Token | null>(null);
  const create = useMutation({
    mutationFn: () => api<Token>("/connections/tokens", {
      method: "POST", json: { name, scopes: chosen, expires_in_days: days === "never" ? null : Number(days) },
    }),
    onSuccess: (t) => { setCreated(t); qc.invalidateQueries({ queryKey: ["api-tokens"] }); },
  });
  if (created?.token) {
    return (
      <Modal title="API token created" onClose={onClose}>
        <div className="space-y-4">
          <p className="text-sm">Paste this into your automation tool as a “Bearer token” (header <code>Authorization: Bearer …</code>).</p>
          <OneTimeSecret label={created.name} value={created.token}
            note="Shown once. Keep it like a password: anyone with it can do what you allowed. Revoke it here if it leaks." />
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      </Modal>
    );
  }
  const groups: Record<string, Scope[]> = {};
  for (const s of scopes.data?.items ?? []) (groups[s.group] ??= []).push(s);
  const all = (scopes.data?.items ?? []).map((s) => s.code);
  return (
    <Modal title="Create API token" onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        {(create.error || scopes.error) && <Alert>{errMsg(create.error || scopes.error)}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" hint="Where it's used, e.g. “n8n on the office PC”.">
            <Input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Stops working after">
            <Select value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option>
              <option value="180">180 days</option><option value="365">1 year</option><option value="never">Never</option>
            </Select>
          </Field>
        </div>
        <fieldset>
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <legend className="text-sm font-medium">What it may do</legend>
            <span className="flex gap-3 text-sm">
              <button type="button" className="text-brand underline" onClick={() => setChosen(all.filter((c) => c.endsWith(".view")))}>Only viewing</button>
              <button type="button" className="text-brand underline" onClick={() => setChosen(all)}>Everything I can do</button>
            </span>
          </div>
          <p className="mb-2 text-xs text-muted">You can only give what you're allowed to do yourself. Tokens can never sign in, or change users, roles or tokens.</p>
          {scopes.isLoading ? <Spinner /> : (
            <div className="max-h-[45vh] space-y-3 overflow-y-auto pr-1">
              {Object.entries(groups).map(([g, items]) => (
                <div key={g}>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{g}</div>
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {items.map((s) => (
                      <label key={s.code} className="flex items-start gap-2 rounded-md border border-line px-3 py-1.5 hover:border-brand/40">
                        <input type="checkbox" className="mt-1 accent-[rgb(var(--brand))]" checked={chosen.includes(s.code)}
                          onChange={(e) => setChosen(e.target.checked ? [...chosen, s.code] : chosen.filter((x) => x !== s.code))} />
                        <span className="text-sm">{s.description}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </fieldset>
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={create.isPending || !chosen.length}>{create.isPending ? "Creating…" : "Create token"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function TokensTab() {
  const { can, session } = useAuth();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const tokens = useQuery({ queryKey: ["api-tokens"], queryFn: () => api<{ items: Token[] }>("/connections/tokens") });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/connections/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-tokens"] }),
  });
  const canCreate = can("apitoken.create");
  const canManage = can("connections.manage");
  const tone = { active: "brand", expired: "neutral", revoked: "danger" } as const;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          An API token lets an automation tool act as you, for example to create a post, approve it or publish it. It can only do
          what you allow when you create it. {canManage ? "You can see and revoke everyone's tokens." : "You see your own tokens."}
        </p>
        {canCreate && <Button onClick={() => setAdding(true)}><KeyRound size={16} /> Create API token</Button>}
      </div>
      {(tokens.error || revoke.error) && <Alert>{errMsg(tokens.error || revoke.error)}</Alert>}
      {tokens.isLoading ? <Spinner label="Loading…" /> : !tokens.data?.items.length ? (
        <EmptyState title="No API tokens" body="Create one when a tool like Zapier, Make or n8n needs to do things in the studio." />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="border-b border-line text-left text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th><th className="px-4 py-2 font-medium">Token</th>
                <th className="px-4 py-2 font-medium">Permissions</th><th className="px-4 py-2 font-medium">Created by</th>
                <th className="px-4 py-2 font-medium">Last used</th><th className="px-4 py-2 font-medium">Expires</th>
                <th className="px-4 py-2 font-medium">State</th><th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {tokens.data.items.map((t) => (
                <tr key={t.id}>
                  <td className="px-4 py-2 font-medium [overflow-wrap:anywhere]">{t.name}</td>
                  <td className="px-4 py-2"><code className="text-xs">{t.hint}</code></td>
                  <td className="px-4 py-2" title={t.scopes.join(", ")}>{t.scopes.length}</td>
                  <td className="px-4 py-2 [overflow-wrap:anywhere]">{t.created_by ?? "—"}</td>
                  <td className="px-4 py-2">{t.last_used_at ? formatDate(t.last_used_at) : "Never"}</td>
                  <td className="px-4 py-2">{t.expires_at ? formatDate(t.expires_at) : "Never"}</td>
                  <td className="px-4 py-2"><Badge tone={tone[t.state]}>{t.state}</Badge></td>
                  <td className="px-4 py-2 text-right">
                    {t.state === "active" && (canManage || t.user_id === session?.user.id) && (
                      <Button variant="danger" className="h-8" disabled={revoke.isPending}
                        onClick={() => confirm(`Revoke “${t.name}”? Anything using it stops working straight away.`) && revoke.mutate(t.id)}>
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <TokenForm onClose={() => setAdding(false)} />}
    </div>
  );
}

// ====================================================================== API help
function Example({ title, body, code }: { title: string; body: string; code: string }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-medium">{title}</h4>
        <button type="button" className="inline-flex items-center gap-1 text-sm text-brand underline" onClick={() => navigator.clipboard?.writeText(code)}>
          <Copy size={14} /> Copy
        </button>
      </div>
      <p className="text-sm text-muted">{body}</p>
      <pre className="mt-1.5 overflow-x-auto rounded-md bg-ink/5 p-2.5 text-xs">{code}</pre>
    </div>
  );
}

function ApiHelp() {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const base = `${origin}/api/v1`;
  const h = `-H "Authorization: Bearer $HRP_TOKEN"`;
  const examples = [
    { title: "Find your social accounts", body: "You need an account id to create a post. Permission: Integrations → view.",
      code: `curl ${h} ${base}/social/accounts` },
    { title: "Create a post (sent for approval)", body: "status can be draft, in_review or scheduled (scheduled needs the approve permission).",
      code: `curl -X POST ${h} -H "Content-Type: application/json" \\\n  -d '{"account_id":"<account id>","text":"Hello from n8n","status":"in_review","scheduled_at":"2026-11-02T10:00:00+05:30"}' \\\n  ${base}/content/posts` },
    { title: "List posts", body: "Filter with ?status=in_review, ?status=scheduled and so on.", code: `curl ${h} "${base}/content/posts?status=in_review"` },
    { title: "Approve a post", body: "Optionally send a new time: {\"scheduled_at\": \"…\"}.", code: `curl -X POST ${h} ${base}/content/posts/<post id>/approve` },
    { title: "Publish a post now", body: "Needs the publish permission. Publishing runs in the background; you'll get post.published or post.failed.",
      code: `curl -X POST ${h} ${base}/content/posts/<post id>/publish-now` },
    { title: "List marketing reports (PDF)", body: "Each ready report has a download_url for its PDF.", code: `curl ${h} ${base}/analytics/reports` },
  ];
  const verify = `# Python: check a webhook really came from the studio
import hmac, hashlib
expected = "sha256=" + hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
ok = hmac.compare_digest(expected, request.headers["X-HRP-Signature"])`;
  return (
    <div className="space-y-4">
      <Card>
        <h3 className="font-semibold">Using the API</h3>
        <p className="mt-1 text-sm text-muted">
          Send your API token in the <code>Authorization: Bearer hrp_…</code> header. Every endpoint, with its fields, is listed in
          the <a className="text-brand underline" href={`${origin}/api/docs`} target="_blank" rel="noreferrer">API reference</a>.
          Each token may make 120 requests a minute. Tokens work from this computer's network; ask your administrator if a cloud tool
          (Zapier, Make) needs to reach the studio from the internet.
        </p>
        <div className="mt-4 space-y-5">{examples.map((e) => <Example key={e.title} {...e} />)}</div>
      </Card>
      <Card>
        <h3 className="font-semibold">What a webhook message looks like</h3>
        <p className="mt-1 text-sm text-muted">A JSON POST with the headers <code>X-HRP-Event</code>, <code>X-HRP-Delivery</code> and
          <code> X-HRP-Signature</code>. The same <code>id</code> is kept when a message is tried again, so you can skip duplicates.</p>
        <pre className="mt-2 overflow-x-auto rounded-md bg-ink/5 p-2.5 text-xs">{`{
  "id": "6f1c…", "event": "post.published", "created_at": "2026-10-19T10:00:00+00:00",
  "organization": {"id": "…", "name": "HRP AI LLP"},
  "data": {"id": "…", "platform": "linkedin_org", "text": "…", "external_url": "https://…"}
}`}</pre>
        <div className="mt-4"><Example title="Check the signature" body="Use the signing secret you got when you added the webhook." code={verify} /></div>
      </Card>
    </div>
  );
}

export default function Connections() {
  const [tab, setTab] = useState<Tab>("webhooks");
  return (
    <div>
      <PageHeader title="Connections"
        description="Connect the studio to Zapier, Make, n8n and other tools: webhooks tell them what happened, API tokens let them do things here." />
      <Tabs value={tab} onChange={setTab} items={[
        { id: "webhooks", label: "Webhooks" }, { id: "tokens", label: "API tokens" }, { id: "api", label: "API help" },
      ]} />
      {tab === "webhooks" && <WebhooksTab />}
      {tab === "tokens" && <TokensTab />}
      {tab === "api" && <ApiHelp />}
    </div>
  );
}
