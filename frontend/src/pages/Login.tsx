import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Field, Input } from "../components/ui";

// A day of planned slots, drawn as a broadcast timeline: the product's idea in one picture.
const SLOTS = [
  { h: 9, ch: "Google Business" },
  { h: 10, ch: "LinkedIn" },
  { h: 12.5, ch: "Instagram" },
  { h: 14, ch: "Pinterest" },
  { h: 17.5, ch: "LinkedIn" },
  { h: 19, ch: "Facebook" },
  { h: 20.5, ch: "Pinterest" },
];
const fmt = (h: number) => `${String(Math.floor(h)).padStart(2, "0")}:${h % 1 ? "30" : "00"}`;

function DayStrip() {
  const start = 8, end = 22;
  const pct = (h: number) => ((h - start) / (end - start)) * 100;
  return (
    <div aria-hidden className="mt-12">
      <div className="relative h-36">
        <div className="absolute inset-x-0 top-[78px] h-px bg-white/25" />
        {SLOTS.map((s, i) => (
          <div key={i} className="absolute top-0 flex flex-col items-center" style={{ left: `${pct(s.h)}%`, transform: "translateX(-50%)" }}>
            <span className={`whitespace-nowrap text-[11px] text-white/70 ${i % 2 ? "mt-0" : "mt-6"}`}>{s.ch}</span>
            <span className={`mt-1 w-px bg-white/30 ${i % 2 ? "h-12" : "h-6"}`} />
            <span className={`h-3 w-3 rounded-full ${i === 1 ? "bg-accent onair" : "bg-white/70"}`} />
            <span className="mt-2 text-[11px] tabular-nums text-white/55">{fmt(s.h)}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-white/50">A sample day, in IST</p>
    </div>
  );
}

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await login(email, password); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-[1.15fr_1fr]">
      <section className="ai-mesh-dark relative hidden flex-col justify-between overflow-hidden px-14 py-12 text-white lg:flex">
        <div aria-hidden className="ai-grid-overlay absolute inset-0" />
        <div className="relative flex items-center gap-3">
          <img src="/logo-mark.png" alt="HRP AI LLP" className="h-11 w-11 rounded-lg bg-white p-0.5" />
          <div className="leading-tight">
            <div className="font-semibold">HRP AI LLP</div>
            <div className="text-sm text-secondary-300">Digital Marketing Studio</div>
          </div>
        </div>
        <div className="relative">
          <h1 className="max-w-[14ch] text-[60px] font-bold leading-[1.02] tracking-[-0.035em]">
            Every channel, on schedule.
          </h1>
          <p className="mt-5 max-w-md text-white/75">
            Plan, generate, approve and publish posts for every organization you manage, from one place.
          </p>
          <DayStrip />
        </div>
        <div className="relative text-xs text-white/45">Where humans and machines think together.</div>
      </section>

      <section className="ai-page-surface flex items-center justify-center px-6 py-12">
        <form onSubmit={submit} className="ai-glass-panel w-full max-w-sm space-y-5 rounded-2xl p-8">
          <img src="/logo-full.png" alt="HRP AI LLP" className="mx-auto h-24 w-24 lg:hidden" />
          <div>
            <h2 className="text-2xl font-bold">Sign in</h2>
            <p className="mt-1 text-muted">Use the email your administrator added you with.</p>
          </div>
          {error && <Alert>{error}</Alert>}
          <Field label="Email">
            <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password">
            <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" disabled={busy} className="w-full h-10">{busy ? "Signing in…" : "Sign in"}</Button>
          <p className="text-xs text-muted">Forgot your password? Ask an administrator of your organization to reset it.</p>
        </form>
      </section>
    </div>
  );
}
