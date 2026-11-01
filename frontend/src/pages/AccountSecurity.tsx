import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Laptop, ShieldCheck, Smartphone } from "lucide-react";
import { api, auth } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Spinner, formatDate } from "../components/ui";

type Device = { id: string; device_name: string; signed_in_at: string; last_used_at: string; ip_address: string | null; current: boolean };
type Security = {
  totp_enabled: boolean; enabled_at: string | null; recovery_codes_left: number; required_by_org: boolean;
  setup_pending: boolean; sessions: Device[];
};
type Setup = { secret: string; otpauth_uri: string; qr_svg: string | null; issuer: string; account: string };

function CodeInput({ value, onChange, autoFocus }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  return (
    <Input inputMode="numeric" autoComplete="one-time-code" autoFocus={autoFocus} required maxLength={7} placeholder="123 456"
      className="w-40 text-center text-lg tracking-[0.25em]" value={value} onChange={(e) => onChange(e.target.value.replace(/[^0-9 ]/g, ""))} />
  );
}

function RecoveryCodes({ codes, onDone, doneLabel = "I've saved them" }: { codes: string[]; onDone: () => void; doneLabel?: string }) {
  const [copied, setCopied] = useState(false);
  const text = `HRP Digital Marketing Studio - recovery codes\nEach code works once, if you can't use your authenticator app.\n\n${codes.join("\n")}\n`;
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url; a.download = "hrp-studio-recovery-codes.txt"; a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="space-y-4">
      <Alert tone="warn">
        Save these recovery codes somewhere safe (a password manager, or print them). If you lose your phone, each code
        lets you sign in once. They're shown only now.
      </Alert>
      <ul className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-ink/[0.03] p-3 font-mono text-[15px] sm:grid-cols-2">
        {codes.map((c) => <li key={c} className="tracking-wide">{c}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" onClick={() => { navigator.clipboard?.writeText(codes.join("\n")); setCopied(true); }}>
          {copied ? "Copied" : "Copy all"}
        </Button>
        <Button type="button" variant="secondary" onClick={download}>Download as text file</Button>
        <Button type="button" onClick={onDone}>{doneLabel}</Button>
      </div>
    </div>
  );
}

/** Set up two-step sign-in: QR code (or the key typed by hand), confirm with a code, then the recovery codes. */
function SetupFlow({ onFinished, onCancel }: { onFinished: () => void; onCancel?: () => void }) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const setup = useQuery({ queryKey: ["2fa-setup"], queryFn: () => api<Setup>("/account/2fa/setup", { method: "POST" }), staleTime: Infinity, gcTime: 0 });
  const confirm = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/account/2fa/enable", { method: "POST", json: { code: code.replace(/\D/g, "") } }),
    onSuccess: (r) => { setCodes(r.recovery_codes); qc.invalidateQueries({ queryKey: ["account-security"] }); },
  });

  if (codes) return <RecoveryCodes codes={codes} onDone={onFinished} />;
  if (setup.isLoading) return <Spinner label="Preparing…" />;
  if (setup.error) return <Alert>{(setup.error as Error).message}</Alert>;
  const s = setup.data!;
  return (
    <form className="space-y-5" onSubmit={(e: FormEvent) => { e.preventDefault(); confirm.mutate(); }}>
      <ol className="space-y-5">
        <li>
          <p className="font-medium">1. Install an authenticator app on your phone</p>
          <p className="text-sm text-muted">Google Authenticator, Microsoft Authenticator, Authy or 1Password all work.</p>
        </li>
        <li>
          <p className="font-medium">2. Scan this code with the app</p>
          <div className="mt-2 flex flex-wrap items-start gap-5">
            {s.qr_svg ? (
              <img src={`data:image/svg+xml;utf8,${encodeURIComponent(s.qr_svg)}`} alt="QR code to scan with your authenticator app"
                className="h-44 w-44 rounded-lg border border-line bg-white p-1" />
            ) : null}
            <div className="min-w-0 flex-1 space-y-2 text-sm">
              <p className="text-muted">Can't scan it? In the app choose “Enter a setup key” and type:</p>
              <div className="rounded-md bg-ink/[0.04] px-3 py-2">
                <div className="text-xs text-muted">Account</div>
                <div className="[overflow-wrap:anywhere]">{s.account}</div>
                <div className="mt-1.5 text-xs text-muted">Key (time based)</div>
                <code className="font-mono text-[15px] font-semibold tracking-wide [overflow-wrap:anywhere]">{s.secret}</code>
                <button type="button" className="ml-2 text-brand underline" onClick={() => navigator.clipboard?.writeText(s.secret.replace(/ /g, ""))}>Copy</button>
              </div>
              <p className="text-muted">On this phone? <a className="text-brand underline" href={s.otpauth_uri}>Open in the authenticator app</a></p>
            </div>
          </div>
        </li>
        <li>
          <p className="font-medium">3. Type the 6-digit code the app shows</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CodeInput value={code} onChange={setCode} />
            <Button type="submit" disabled={confirm.isPending || code.replace(/\D/g, "").length !== 6}>
              {confirm.isPending ? "Checking…" : "Turn on"}
            </Button>
            {onCancel && <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>}
          </div>
        </li>
      </ol>
      {confirm.error && <Alert>{(confirm.error as Error).message}</Alert>}
    </form>
  );
}

function CodeModal({ title, intro, action, danger, onClose, onDone }: {
  title: string; intro: string; action: string; danger?: boolean;
  onClose: () => void; onDone: (body: { code?: string; recovery_code?: string }) => Promise<unknown>;
}) {
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const m = useMutation({ mutationFn: () => onDone(recovery ? { recovery_code: code.trim() } : { code: code.replace(/\D/g, "") }) });
  return (
    <Modal title={title} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <p className="text-sm text-muted">{intro}</p>
        {m.error && <Alert>{(m.error as Error).message}</Alert>}
        <Field label={recovery ? "Recovery code" : "6-digit code from your authenticator app"}>
          {recovery
            ? <Input autoFocus required value={code} onChange={(e) => setCode(e.target.value)} placeholder="abcde-fghjk" />
            : <CodeInput autoFocus value={code} onChange={setCode} />}
        </Field>
        <button type="button" className="text-sm text-brand underline" onClick={() => { setRecovery((v) => !v); setCode(""); }}>
          {recovery ? "Use the authenticator app instead" : "Use a recovery code instead"}
        </button>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={danger ? "danger" : "primary"} disabled={m.isPending || !code.trim()}>{m.isPending ? "Checking…" : action}</Button>
        </div>
      </form>
    </Modal>
  );
}

function TwoStepCard({ data }: { data: Security }) {
  const qc = useQueryClient();
  const { reload, session } = useAuth();
  const [settingUp, setSettingUp] = useState(false);
  const [modal, setModal] = useState<"disable" | "codes" | null>(null);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["account-security"] });

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ShieldCheck className={data.totp_enabled ? "text-primary-600" : "text-muted"} size={22} />
          <div className="min-w-0">
            <h2 className="font-semibold">Two-step sign-in {data.totp_enabled ? <Badge tone="brand">On</Badge> : <Badge>Off</Badge>}</h2>
            <p className="text-sm text-muted">
              {data.totp_enabled
                ? `After your password, you type a code from your authenticator app. On since ${formatDate(data.enabled_at, session?.organization?.timezone)}.`
                : "Protect your account even if someone learns your password: signing in also asks for a code from an app on your phone."}
            </p>
            {data.required_by_org && !data.totp_enabled && (
              <p className="mt-1 text-sm text-amber-800">Your organization requires two-step sign-in.</p>
            )}
            {data.totp_enabled && (
              <p className={`mt-1 text-sm ${data.recovery_codes_left <= 2 ? "text-amber-800" : "text-muted"}`}>
                {data.recovery_codes_left} of 10 recovery codes left.
              </p>
            )}
          </div>
        </div>
        {!data.totp_enabled && !settingUp && <Button onClick={() => setSettingUp(true)}>Set up</Button>}
        {data.totp_enabled && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setModal("codes")}>New recovery codes</Button>
            <Button variant="danger" onClick={() => setModal("disable")}>Turn off</Button>
          </div>
        )}
      </div>
      {settingUp && !data.totp_enabled && (
        <div className="mt-5 border-t border-line pt-5">
          <SetupFlow onCancel={() => setSettingUp(false)} onFinished={() => { setSettingUp(false); refresh(); reload(); }} />
        </div>
      )}
      {newCodes && (
        <div className="mt-5 border-t border-line pt-5">
          <RecoveryCodes codes={newCodes} doneLabel="Done" onDone={() => setNewCodes(null)} />
        </div>
      )}
      {modal === "disable" && (
        <CodeModal title="Turn off two-step sign-in" danger action="Turn off"
          intro={data.required_by_org
            ? "Your organization requires two-step sign-in, so you'll be asked to set it up again straight away (for example on a new phone)."
            : "Signing in will only need your password again."}
          onClose={() => setModal(null)}
          onDone={async (body) => { await api("/account/2fa/disable", { method: "POST", json: body }); setModal(null); refresh(); await reload(); }} />
      )}
      {modal === "codes" && (
        <CodeModal title="Make new recovery codes" action="Make new codes"
          intro="Your old recovery codes stop working. Confirm with a code from your authenticator app."
          onClose={() => setModal(null)}
          onDone={async (body) => {
            const r = await api<{ recovery_codes: string[] }>("/account/2fa/recovery-codes", { method: "POST", json: body });
            setModal(null); setNewCodes(r.recovery_codes); refresh();
          }} />
      )}
    </Card>
  );
}

function DevicesCard({ devices }: { devices: Device[] }) {
  const qc = useQueryClient();
  const { session } = useAuth();
  const [note, setNote] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["account-security"] });
  const others = useMutation({
    mutationFn: () => auth.signOutOthers(),
    onSuccess: (n) => { setNote(n ? `Signed out ${n} other device${n === 1 ? "" : "s"}.` : "No other devices were signed in."); refresh(); },
  });
  const one = useMutation({
    mutationFn: (id: string) => api(`/account/sessions/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const tz = session?.organization?.timezone;
  const err = (others.error || one.error) as Error | null;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">Signed-in devices</h2>
          <p className="text-sm text-muted">Browsers and phones where you're signed in. Don't recognise one? Sign it out and change your password.</p>
        </div>
        <Button variant="secondary" disabled={others.isPending || devices.length < 2} onClick={() => others.mutate()}>
          {others.isPending ? "Signing out…" : "Sign out other devices"}
        </Button>
      </div>
      {note && <div className="mt-3"><Alert tone="info">{note}</Alert></div>}
      {err && <div className="mt-3"><Alert>{err.message}</Alert></div>}
      <ul className="mt-4 divide-y divide-line">
        {devices.map((d) => {
          const Icon = /android|iphone|ipad|phone/i.test(d.device_name) ? Smartphone : Laptop;
          return (
            <li key={d.id} className="flex flex-wrap items-center gap-3 py-3">
              <Icon size={18} className="shrink-0 text-muted" />
              <div className="min-w-0 flex-1">
                <div className="font-medium [overflow-wrap:anywhere]">{d.device_name} {d.current && <Badge tone="brand">This device</Badge>}</div>
                <div className="text-xs text-muted">
                  Signed in {formatDate(d.signed_in_at, tz)} · last active {formatDate(d.last_used_at, tz)}
                  {d.ip_address ? ` · ${d.ip_address}` : ""}
                </div>
              </div>
              {!d.current && <Button variant="ghost" disabled={one.isPending} onClick={() => one.mutate(d.id)}>Sign out</Button>}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export default function AccountSecurity({ forced = false }: { forced?: boolean }) {
  const { session, logout, reload } = useAuth();
  const q = useQuery({ queryKey: ["account-security"], queryFn: () => api<Security>("/account/security"), enabled: !forced });

  if (forced) {
    return (
      <div className="ai-page-surface flex min-h-full items-center justify-center px-4 py-10">
        <div className="ai-glass-panel w-full max-w-xl rounded-2xl p-6 sm:p-8">
          <img src="/logo-mark.png" alt="HRP AI LLP" className="mb-4 h-12 w-12" />
          <h1 className="text-2xl font-bold">Set up two-step sign-in</h1>
          <p className="mt-1 mb-6 text-muted">
            {session?.organization?.name} asks everyone to use a code from an authenticator app when signing in. It takes about a minute.
          </p>
          <SetupFlow onFinished={() => { reload(); }} />
          <div className="mt-6 border-t border-line pt-4">
            <Button variant="ghost" onClick={logout}>Sign out</Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Security" description="How you sign in, and where you're signed in." />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading && <Spinner label="Loading…" />}
      {q.data && (
        <div className="space-y-5">
          <TwoStepCard data={q.data} />
          <DevicesCard devices={q.data.sessions} />
        </div>
      )}
    </>
  );
}
