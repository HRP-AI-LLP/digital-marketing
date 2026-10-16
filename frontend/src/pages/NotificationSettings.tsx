import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Alert, Button, Card, Field, Input, PageHeader, Toggle } from "../components/ui";

type Pref = { enabled: boolean; events: string[]; target: string | null; verified: boolean; quiet_start: string | null; quiet_end: string | null };
type Prefs = { events: Record<string, string>; channels: Record<"inapp" | "email" | "telegram", Pref>; available: { email: boolean; telegram: boolean; telegram_bot: string | null } };

const CH: { id: "inapp" | "email" | "telegram"; label: string; blurb: string }[] = [
  { id: "inapp", label: "In the app", blurb: "The bell at the top of every page." },
  { id: "email", label: "Email", blurb: "Sent through the email account set up in Integrations." },
  { id: "telegram", label: "Telegram", blurb: "Approve or reject posts from your phone with one tap." },
];

function Channel({ id, label, blurb, data }: { id: "inapp" | "email" | "telegram"; label: string; blurb: string; data: Prefs }) {
  const qc = useQueryClient();
  const p = data.channels[id];
  const [draft, setDraft] = useState<Pref>(p);
  useEffect(() => setDraft(p), [p]);
  const save = useMutation({
    mutationFn: () => api(`/notifications/prefs/${id}`, { method: "PUT", json: { enabled: draft.enabled, events: draft.events, target: draft.target, quiet_start: draft.quiet_start || null, quiet_end: draft.quiet_end || null } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notif-prefs"] }),
  });
  const test = useMutation({ mutationFn: () => api(`/notifications/test`, { method: "POST", json: { channel: id } }) });
  const link = useMutation({ mutationFn: () => api<{ url: string }>("/notifications/telegram/link", { method: "POST" }), onSuccess: (r) => window.open(r.url, "_blank") });
  const unavailable = (id === "email" && !data.available.email) || (id === "telegram" && !data.available.telegram);
  const dirty = JSON.stringify(draft) !== JSON.stringify(p);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{label}</h2>
          <p className="text-sm text-muted">{blurb}</p>
        </div>
        <Toggle checked={draft.enabled} disabled={unavailable} onChange={(v) => setDraft({ ...draft, enabled: v })} label={draft.enabled ? "On" : "Off"} />
      </div>
      {unavailable && <div className="mt-3"><Alert tone="warn">An administrator needs to add {id === "email" ? "an email (SMTP) account" : "a Telegram bot"} in Settings → Integrations first.</Alert></div>}
      {!unavailable && (
        <div className="mt-4 space-y-4">
          {id === "email" && (
            <Field label="Send to"><Input type="email" value={draft.target ?? ""} onChange={(e) => setDraft({ ...draft, target: e.target.value })} /></Field>
          )}
          {id === "telegram" && (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {p.verified ? <span className="text-emerald-700">Linked to your Telegram.</span> : <span className="text-muted">Not linked yet.</span>}
              <Button variant="secondary" onClick={() => link.mutate()}>{p.verified ? "Link again" : "Link Telegram"}</Button>
              {link.data && <span className="text-muted">In Telegram, press Start. Then refresh this page.</span>}
            </div>
          )}
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Tell me when</legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {Object.entries(data.events).map(([ev, lbl]) => (
                <label key={ev} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="accent-[rgb(var(--brand))]" checked={draft.events.includes(ev)}
                    onChange={(e) => setDraft({ ...draft, events: e.target.checked ? [...draft.events, ev] : draft.events.filter((x) => x !== ev) })} />
                  {lbl}
                </label>
              ))}
            </div>
          </fieldset>
          {id !== "inapp" && (
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Quiet from"><Input type="time" className="w-32" value={draft.quiet_start ?? ""} onChange={(e) => setDraft({ ...draft, quiet_start: e.target.value })} /></Field>
              <Field label="until"><Input type="time" className="w-32" value={draft.quiet_end ?? ""} onChange={(e) => setDraft({ ...draft, quiet_end: e.target.value })} /></Field>
              <p className="pb-2 text-xs text-muted">Failures and account problems still come through.</p>
            </div>
          )}
          {(save.error || test.error || link.error) && <Alert>{((save.error || test.error || link.error) as Error).message}</Alert>}
          {test.isSuccess && <Alert tone="info">Test sent.</Alert>}
          <div className="flex gap-2">
            <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save</Button>
            <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>Send a test</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function NotificationSettings() {
  const q = useQuery({ queryKey: ["notif-prefs"], queryFn: () => api<Prefs>("/notifications/prefs") });
  return (
    <div>
      <PageHeader title="Notifications" description="Choose what reaches you, and where. These settings are yours; everyone sets their own." />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      <div className="space-y-5">{q.data && CH.map((c) => <Channel key={c.id} {...c} data={q.data!} />)}</div>
    </div>
  );
}
