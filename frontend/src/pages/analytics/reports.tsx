import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CalendarClock, Download, FileText, Mail, Pencil, Plus, Send, Trash2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Spinner, StatusBadge, Textarea, Toggle, formatDate } from "../../components/ui";
import type { Report, ReportSchedule, Reports } from "./types";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const splitEmails = (s: string) => s.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
const dayLabel = (iso: string) => new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso + "T00:00:00"));
const refreshAll = (qc: ReturnType<typeof useQueryClient>) => qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("analytics-") });

function NewReport({ emailReady }: { emailReady: boolean }) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState("30");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [to, setTo] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const make = useMutation({
    mutationFn: () => api<{ job_id: string }>("/analytics/reports", { method: "POST", json: {
      period, start: period === "custom" ? start || null : null, end: period === "custom" ? end || null : null, recipients: splitEmails(to),
    } }),
    onSuccess: (r) => { setJob(r.job_id); refreshAll(qc); },
  });
  const running = useJob(job, () => refreshAll(qc));
  const busy = (!!job && running?.status !== "done" && running?.status !== "failed") || make.isPending;
  return (
    <Card>
      <h2 className="mb-1 flex items-center gap-2 font-semibold"><FileText size={18} className="text-primary-700" /> Create a report</h2>
      <p className="mb-4 text-sm text-muted">A branded PDF with your key numbers, growth charts, top posts, competitors, health score and tips.</p>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); make.mutate(); }}>
        <Field label="Period">
          <Select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option>
            <option value="last_month">Last calendar month</option><option value="custom">Custom dates</option>
          </Select>
        </Field>
        {period === "custom" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="From"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} required /></Field>
            <Field label="To"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required /></Field>
          </div>
        )}
        <Field label="Email it to (optional)" hint={emailReady ? "Separate addresses with commas." : "Email isn't set up yet, so the report will only be available to download."}>
          <Input value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com, team@company.com" disabled={!emailReady} />
        </Field>
        {make.error && <Alert>{(make.error as Error).message}</Alert>}
        {running?.status === "failed" && <Alert>{running.error}</Alert>}
        {running?.status === "done" && (running.result?.status === "failed"
          ? <Alert>{String(running.result?.error ?? "The report couldn't be built.")}</Alert>
          : <Alert tone="info">{running.result?.email_error ? String(running.result.email_error) : running.result?.emailed ? "Report ready and emailed." : "Report ready. Download it from the list."}</Alert>)}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy}>{busy ? "Building the report…" : "Create report"}</Button>
          {busy && <Spinner label={running?.progress ?? "Waiting for the background worker…"} />}
        </div>
      </form>
    </Card>
  );
}

function SendModal({ r, onClose }: { r: Report; onClose: () => void }) {
  const qc = useQueryClient();
  const [to, setTo] = useState(r.recipients.join(", "));
  const send = useMutation({
    mutationFn: () => api(`/analytics/reports/${r.id}/send`, { method: "POST", json: { recipients: splitEmails(to) } }),
    onSuccess: () => { refreshAll(qc); onClose(); },
  });
  return (
    <Modal title="Email this report" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
        <p className="text-sm text-muted">The report for {dayLabel(r.period_start)} – {dayLabel(r.period_end)} is rebuilt with the latest numbers and sent as a PDF attachment.</p>
        <Field label="Send to" hint="Separate addresses with commas."><Input value={to} onChange={(e) => setTo(e.target.value)} required /></Field>
        {send.error && <Alert>{(send.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={send.isPending}><Send size={15} /> Send now</Button>
        </div>
      </form>
    </Modal>
  );
}

type SchedForm = { name: string; frequency: "weekly" | "monthly"; weekday: number; month_day: number; time: string; recipients: string; active: boolean };

function ScheduleModal({ s, onClose }: { s: ReportSchedule | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<SchedForm>(s ? { ...s, recipients: s.recipients.join(", ") } :
    { name: "Weekly marketing report", frequency: "weekly", weekday: 0, month_day: 1, time: "09:00", recipients: "", active: true });
  const save = useMutation({
    mutationFn: () => {
      const json = { ...f, recipients: splitEmails(f.recipients) };
      return s ? api(`/analytics/report-schedules/${s.id}`, { method: "PATCH", json }) : api("/analytics/report-schedules", { method: "POST", json });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["analytics-report-schedules"] }); onClose(); },
  });
  return (
    <Modal title={s ? "Edit report schedule" : "Schedule a report"} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="How often">
            <Select value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value as SchedForm["frequency"] })}>
              <option value="weekly">Every week</option><option value="monthly">Every month</option>
            </Select>
          </Field>
          {f.frequency === "weekly" ? (
            <Field label="On">
              <Select value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </Select>
            </Field>
          ) : (
            <Field label="On day">
              <Select value={f.month_day} onChange={(e) => setF({ ...f, month_day: Number(e.target.value) })}>
                {Array.from({ length: 28 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}
              </Select>
            </Field>
          )}
        </div>
        <Field label="At" hint="In your organization's time zone."><Input type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} required /></Field>
        <Field label="Send to" hint={f.frequency === "weekly" ? "Covers the 7 days up to the day before sending." : "Covers the previous calendar month."}>
          <Textarea rows={2} value={f.recipients} onChange={(e) => setF({ ...f, recipients: e.target.value })} placeholder="name@company.com, team@company.com" required />
        </Field>
        <Toggle checked={f.active} onChange={(v) => setF({ ...f, active: v })} label="Send on schedule" />
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>{s ? "Save changes" : "Create schedule"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function Schedules({ canManage, emailReady }: { canManage: boolean; emailReady: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["analytics-report-schedules"], queryFn: () => api<ReportSchedule[]>("/analytics/report-schedules") });
  const [edit, setEdit] = useState<ReportSchedule | null | "new">(null);
  const [notice, setNotice] = useState<string | null>(null);
  const sendNow = useMutation({
    mutationFn: (id: string) => api(`/analytics/report-schedules/${id}/send-now`, { method: "POST" }),
    onSuccess: () => { setNotice("Building the report now. It will be emailed in a minute and appear in the list below."); refreshAll(qc); },
  });
  const del = useMutation({ mutationFn: (id: string) => api(`/analytics/report-schedules/${id}`, { method: "DELETE" }), onSuccess: () => refreshAll(qc) });
  const desc = (s: ReportSchedule) => `${s.frequency === "weekly" ? `Every ${WEEKDAYS[s.weekday]}` : `On day ${s.month_day} of each month`} at ${s.time}`;
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><CalendarClock size={18} className="text-primary-700" /> Email schedules</h2>
        {canManage && <Button variant="secondary" onClick={() => setEdit("new")} disabled={!emailReady}><Plus size={15} /> Schedule a report</Button>}
      </div>
      {!emailReady && (
        <Alert tone="warn">To email reports, set up your mail server first in <Link className="underline" to="/settings/integrations">Integrations</Link> (SMTP).</Alert>
      )}
      {notice && <div className="mb-3"><Alert tone="info">{notice}</Alert></div>}
      {(sendNow.error || del.error) && <Alert>{((sendNow.error || del.error) as Error).message}</Alert>}
      {q.isLoading && <Spinner label="Loading schedules" />}
      {q.data && q.data.length === 0 && <p className="mt-3 text-sm text-muted">No scheduled reports yet. Schedule one to get a PDF in your inbox every week or month.</p>}
      <ul className="divide-y divide-line">
        {q.data?.map((s) => (
          <li key={s.id} className="flex flex-wrap items-start gap-3 py-3">
            <div className="min-w-[14rem] flex-1">
              <div className="flex items-center gap-2 font-medium">{s.name} {!s.active && <Badge>Paused</Badge>}</div>
              <div className="text-sm text-muted">{desc(s)} · to {s.recipients.join(", ")}</div>
              <div className="text-xs text-muted">
                {s.next_run_at ? `Next: ${formatDate(s.next_run_at)}` : "Not scheduled"}{s.last_status ? ` · Last: ${s.last_status}` : ""}
              </div>
            </div>
            {canManage && (
              <div className="flex flex-wrap gap-1">
                <Button variant="ghost" onClick={() => sendNow.mutate(s.id)} disabled={sendNow.isPending}><Send size={14} /> Send now</Button>
                <Button variant="ghost" onClick={() => setEdit(s)} aria-label={`Edit ${s.name}`}><Pencil size={14} /> Edit</Button>
                <Button variant="ghost" onClick={() => { if (confirm(`Delete the schedule “${s.name}”?`)) del.mutate(s.id); }} aria-label={`Delete ${s.name}`}><Trash2 size={14} /></Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {edit && <ScheduleModal s={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

export function ReportsPanel() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["analytics-reports"], queryFn: () => api<Reports>("/analytics/reports"),
    refetchInterval: (query) => (query.state.data?.items.some((r) => r.status === "queued") ? 4000 : false),
  });
  const [sending, setSending] = useState<Report | null>(null);
  const del = useMutation({ mutationFn: (id: string) => api(`/analytics/reports/${id}`, { method: "DELETE" }), onSuccess: () => refreshAll(qc) });
  if (q.isLoading) return <Spinner label="Loading reports" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const d = q.data!;
  return (
    <div className="min-w-0 space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {d.can_manage ? <NewReport emailReady={d.email_ready} /> : <Card><p className="text-sm text-muted">You can download reports. Creating and emailing them needs the analytics.manage permission.</p></Card>}
        <Schedules canManage={d.can_manage} emailReady={d.email_ready} />
      </div>
      <Card className="!p-0">
        <div className="px-5 pt-4"><h2 className="font-semibold">Report history</h2></div>
        {d.items.length === 0 ? <div className="p-5"><EmptyState title="No reports yet" body="Create your first report above." /></div> : (
          <ul className="mt-2 divide-y divide-line">
            {d.items.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <div className="min-w-[16rem] flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{dayLabel(r.period_start)} – {dayLabel(r.period_end)}</span>
                    <StatusBadge status={r.status} />
                    {r.trigger !== "manual" && <Badge>{r.trigger === "schedule" ? "Scheduled" : "Sent now"}</Badge>}
                    {r.summary.health != null && <span className="text-xs text-muted">Health {r.summary.health}/100</span>}
                  </div>
                  <div className="text-xs text-muted">
                    Created {r.created_at ? formatDate(r.created_at) : "—"}
                    {r.sent_at && <> · <Mail size={12} className="inline" /> emailed to {r.recipients.join(", ")}</>}
                    {r.size_bytes > 0 && <> · {(r.size_bytes / 1024).toFixed(0)} KB</>}
                  </div>
                  {r.email_error && <div className="mt-1 text-xs text-danger">{r.email_error}</div>}
                  {r.error && <div className="mt-1 text-xs text-danger">{r.error}</div>}
                </div>
                <div className="flex flex-wrap gap-1">
                  {r.download_url && <a href={r.download_url} download><Button variant="secondary"><Download size={15} /> Download PDF</Button></a>}
                  {d.can_manage && r.status === "ready" && d.email_ready && <Button variant="ghost" onClick={() => setSending(r)}><Send size={14} /> Email</Button>}
                  {d.can_manage && <Button variant="ghost" aria-label="Delete report" onClick={() => { if (confirm("Delete this report?")) del.mutate(r.id); }}><Trash2 size={14} /></Button>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {sending && <SendModal r={sending} onClose={() => setSending(null)} />}
    </div>
  );
}
