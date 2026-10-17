import { useEffect, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { X } from "lucide-react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-gradient-to-r from-blue-700 to-cyan-600 text-white shadow-sm hover:from-blue-800 hover:to-cyan-700 disabled:opacity-50",
  secondary: "bg-surface text-ink border border-line hover:border-brand/50 disabled:opacity-50",
  ghost: "text-muted hover:text-ink hover:bg-ink/5",
  danger: "bg-surface text-danger border border-danger/30 hover:bg-danger-tint",
};

export function Button({ variant = "primary", className = "", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...p}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 h-9 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    />
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium mb-1.5">{label}</span>
      {children}
      {hint && !error && <span className="block text-xs text-muted mt-1">{hint}</span>}
      {error && <span className="block text-xs text-danger mt-1">{error}</span>}
    </label>
  );
}

/** Full width unless the caller passes its own width class (w-*, flex-*, min-w-*). */
function widthOf(cls?: string) {
  return cls && /(^|\s)(sm:|md:|lg:)?(w-|flex-1|basis-)/.test(cls) ? "" : "w-full";
}

export function Input(p: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...p}
      className={`${widthOf(p.className)} h-10 rounded-lg border border-line bg-surface px-3 text-[15px] placeholder:text-muted/70 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 ${p.className ?? ""}`}
    />
  );
}

export function Select(p: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...p}
      className={`${widthOf(p.className)} h-10 rounded-lg border border-line bg-surface px-2.5 text-[15px] focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 ${p.className ?? ""}`}
    />
  );
}

export function Alert({ tone = "danger", children }: { tone?: "danger" | "info" | "warn"; children: ReactNode }) {
  const t = {
    danger: "bg-danger-tint text-danger border-danger/20",
    info: "bg-brand-tint text-brand-deep border-brand/20",
    warn: "bg-amber-50 text-amber-900 border-amber-200",
  }[tone];
  return <div role={tone === "danger" ? "alert" : "status"} className={`rounded-md border px-3 py-2 text-sm ${t}`}>{children}</div>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "brand" | "accent" | "danger" }) {
  const t = {
    neutral: "bg-ink/5 text-muted",
    brand: "bg-brand-tint text-brand-deep",
    accent: "bg-accent-tint text-secondary-800",
    danger: "bg-danger-tint text-danger",
  }[tone];
  return <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${t}`}>{children}</span>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input,select,button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 backdrop-blur-sm overflow-y-auto p-4 pt-[6vh] pb-[6vh]" onMouseDown={onClose}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} rounded-xl bg-surface shadow-[0_24px_80px_rgba(6,47,89,0.18)]`}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className="text-[28px] leading-tight font-bold text-ink">{title}</h1>
        {description && <p className="text-muted mt-1">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function SecretReveal({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-secondary-300 bg-accent-tint px-3 py-2.5">
      <div className="text-sm">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <code className="font-mono text-base font-semibold tracking-wide">{value}</code>
        <button className="text-sm text-brand underline" onClick={() => navigator.clipboard?.writeText(value)}>Copy</button>
      </div>
      <div className="text-xs text-muted mt-1">Shown once. They'll be asked to choose a new password when they first sign in.</div>
    </div>
  );
}

export function formatDate(iso: string | null, tz?: string) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: tz }).format(new Date(iso));
}

export function Textarea(p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...p}
      className={`w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px] placeholder:text-muted/70 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 ${p.className ?? ""}`}
    />
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className={`inline-flex items-center gap-2.5 text-sm ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 rounded-full transition-colors ${checked ? "bg-primary-600" : "bg-slate-300"}`}
      >
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
      </button>
      {label}
    </label>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; count?: number }[] }) {
  return (
    <div role="tablist" className="no-scrollbar mb-6 flex gap-1 overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_rgb(var(--line))]">
      {items.map((i) => (
        <button
          key={i.id}
          role="tab"
          aria-selected={value === i.id}
          onClick={() => onChange(i.id)}
          className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${value === i.id ? "border-primary-600 font-semibold text-ink" : "border-transparent text-muted hover:text-ink"}`}
        >
          {i.label}{i.count !== undefined && <span className="ml-1.5 text-xs text-muted">{i.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-surface p-5 ${className}`}>{children}</div>;
}

export function EmptyState({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-10 text-center">
      <p className="font-semibold">{title}</p>
      {body && <p className="mx-auto mt-1 max-w-md text-sm text-muted">{body}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600" />
      {label}
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div>
      <div className="text-sm text-muted">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, "neutral" | "brand" | "accent" | "danger"> = {
    published: "brand", approved: "brand", connected: "brand", ready: "brand", confirmed: "brand", done: "brand", ok: "brand",
    in_review: "accent", scheduled: "accent", generating: "accent", running: "accent", queued: "accent", writing: "accent", suggested: "accent", publishing: "accent",
    failed: "danger", error: "danger", rejected: "danger", expired: "danger", missed: "danger",
  };
  return <Badge tone={tone[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}
