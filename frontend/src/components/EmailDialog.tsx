import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Mail, X } from "lucide-react";
import { api } from "../api/client";
import { Alert, Button, Field, Input, Modal, Select, Textarea } from "./ui";
import { PRESETS, presetRange } from "./periods";

export type Sender = { id: string; name: string; from_email: string | null; from_name: string | null; enabled: boolean; default: boolean };
export type EmailSetup = { senders: Sender[]; report_recipients: string[]; report_cc: string[]; ready: boolean };

export function useEmailSetup(enabled = true) {
  return useQuery({ queryKey: ["email-senders"], queryFn: () => api<EmailSetup>("/email/senders"), enabled, staleTime: 30_000 });
}

/** What to send: a report (made on the server) or a file the page already built (CSV). */
export type EmailWhat =
  | { type: "report"; kind: string; title: string; params: Record<string, string>; period?: boolean }
  | { type: "file"; filename: string; mime: string; content: string };

/** Choose sender and recipients, add a note, send. Defaults come from Settings → Organization → Email. */
export function EmailDialog({ what, onClose }: { what: EmailWhat; onClose: () => void }) {
  const setup = useEmailSetup();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(what.type === "report" ? what.title : what.filename);
  const [message, setMessage] = useState("");
  const [preset, setPreset] = useState("30");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    const d = setup.data;
    if (!d) return;
    setFrom((f) => f || d.senders.find((s) => s.default)?.id || d.senders.find((s) => s.enabled)?.id || "");
    setTo((t) => t || d.report_recipients.join(", "));
    setCc((c) => c || d.report_cc.join(", "));
  }, [setup.data]);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const base = { to, cc: cc || null, sender_id: from || null, subject, message };
      const r = what.type === "report"
        ? await api<{ message: string }>("/email/report", { method: "POST", json: {
            ...base, kind: what.kind, params: what.period ? { ...what.params, ...presetRange(preset) } : what.params } })
        : await api<{ message: string }>("/email/file", { method: "POST", json: {
            ...base, filename: what.filename, mime: what.mime, content: what.content } });
      setDone(r.message);
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const title = what.type === "report" ? `Email “${what.title}”` : `Email ${what.filename}`;
  if (done) {
    return (
      <Modal title={title} onClose={onClose}>
        <div className="space-y-4"><Alert tone="info">{done}</Alert>
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div></div>
      </Modal>
    );
  }
  const d = setup.data;
  return (
    <Modal title={title} onClose={onClose}>
      {d && !d.ready ? (
        <div className="space-y-4">
          <Alert tone="warn">Email isn't set up yet. Add your mailbox (for example info@hrpai.in from GoDaddy) under
            Settings → Integrations → Email (SMTP), then come back.</Alert>
          <div className="flex justify-end gap-2">
            <Link to="/settings/integrations" onClick={onClose}><Button>Set up email</Button></Link>
          </div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={send}>
          {err && <Alert>{err}</Alert>}
          <Field label="From">
            <Select value={from} onChange={(e) => setFrom(e.target.value)}>
              {(d?.senders ?? []).filter((s) => s.enabled).map((s) => (
                <option key={s.id} value={s.id}>{s.from_name ? `${s.from_name} <${s.from_email}>` : s.from_email || s.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="To" hint="Separate addresses with commas.">
            <Input required value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com" />
          </Field>
          <Field label="Cc (optional)"><Input value={cc} onChange={(e) => setCc(e.target.value)} /></Field>
          {what.type === "report" && what.period && (
            <Field label="Period">
              <Select value={preset} onChange={(e) => setPreset(e.target.value)}>
                {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </Select>
            </Field>
          )}
          <Field label="Subject"><Input required value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} /></Field>
          <Field label="Message (optional)"><Textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} /></Field>
          <p className="text-xs text-muted">
            {what.type === "report" ? "The report is attached as a PDF." : `${what.filename} is attached.`} Default sender and
            recipients are set in <Link className="underline" to="/settings/organization" onClick={onClose}>Settings → Organization</Link>.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy || !d}>{busy ? "Sending…" : "Send"}</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ "Email it" after any CSV download
type Offered = { filename: string; mime: string; content: string } | null;
let offered: Offered = null;
const subs = new Set<() => void>();
/** Call after a file is downloaded: a small card offers to email the same file. */
export function offerEmailFile(f: { filename: string; mime: string; content: string }) {
  offered = f;
  subs.forEach((s) => s());
}
function useOffered() {
  return useSyncExternalStore((l) => { subs.add(l); return () => subs.delete(l); }, () => offered);
}

export function FileEmailOffer() {
  const f = useOffered();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!f || open) return;
    const t = setTimeout(() => offerEmailFileClear(), 20_000);
    return () => clearTimeout(t);
  }, [f, open]);
  if (!f) return null;
  return (
    <>
      <div role="status" className="fixed bottom-4 right-4 z-40 flex max-w-sm items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-xl">
        <span className="min-w-0 flex-1 truncate">Downloaded <b>{f.filename}</b></span>
        <Button variant="secondary" onClick={() => setOpen(true)}><Mail size={15} />Email it</Button>
        <button aria-label="Dismiss" className="text-muted hover:text-ink" onClick={offerEmailFileClear}><X size={16} /></button>
      </div>
      {open && <EmailDialog what={{ type: "file", ...f }} onClose={() => { setOpen(false); offerEmailFileClear(); }} />}
    </>
  );
}
function offerEmailFileClear() {
  offered = null;
  subs.forEach((s) => s());
}
