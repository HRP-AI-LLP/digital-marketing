import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Field, Input, PageHeader } from "../components/ui";

export default function ChangePassword({ forced = false }: { forced?: boolean }) {
  const { changePassword, logout, session } = useAuth();
  const nav = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(""); setDone(false);
    if (next.length < 10) return setError("Use at least 10 characters.");
    if (next !== confirm) return setError("The two new passwords don't match.");
    setBusy(true);
    try {
      await changePassword(current, next);
      setDone(true); setCurrent(""); setNext(""); setConfirm("");
      if (forced) nav("/");
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  const form = (
    <form onSubmit={submit} className="max-w-sm space-y-4">
      {error && <Alert>{error}</Alert>}
      {done && <Alert tone="info">Password changed. Other devices have been signed out.</Alert>}
      <Field label={forced ? "Temporary password" : "Current password"}>
        <Input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <Field label="New password" hint="At least 10 characters.">
        <Input type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
      </Field>
      <Field label="Confirm new password">
        <Input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>{busy ? "Saving…" : "Change password"}</Button>
        {forced && <Button type="button" variant="ghost" onClick={logout}>Sign out</Button>}
      </div>
    </form>
  );

  if (!forced) {
    return (
      <>
        <PageHeader title="Change password" description="Changing it signs you out on every other device." />
        {form}
      </>
    );
  }
  return (
    <div className="ai-page-surface flex min-h-full items-center justify-center px-6">
      <div className="ai-glass-panel w-full max-w-sm rounded-2xl p-8">
        <img src="/logo-mark.png" alt="HRP AI LLP" className="mb-4 h-12 w-12" />
        <h1 className="text-2xl font-bold">Choose your own password</h1>
        <p className="mt-1 mb-6 text-muted">
          Welcome, {session?.user.full_name.split(" ")[0]}. You signed in with a temporary password, so set a new one to continue.
        </p>
        {form}
      </div>
    </div>
  );
}
