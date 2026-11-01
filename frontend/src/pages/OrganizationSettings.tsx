import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Building2, ImagePlus, Plus, Trash2, Upload } from "lucide-react";
import { api, refreshSession, type OrgContact, type Organization } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Spinner, Textarea, Toggle } from "../components/ui";
import { useSystemStatus, type BlogAutopilot } from "../components/SystemBanner";
import { useEmailSetup } from "../components/EmailDialog";

export const TIMEZONES = [
  "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Europe/London", "Europe/Berlin",
  "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney", "UTC",
];
const SIZES = ["1", "2-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+"];
const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY"];
const INDUSTRIES = [
  "Software / SaaS", "IT services", "Artificial intelligence", "HR technology", "Financial services", "Banking", "Insurance",
  "Healthcare", "Pharmaceuticals", "Education", "E-commerce", "Retail", "Manufacturing", "Logistics", "Real estate",
  "Construction", "Hospitality", "Travel", "Media and entertainment", "Marketing and advertising", "Consulting",
  "Telecommunications", "Energy", "Automotive", "Agriculture", "Government", "Non-profit",
];
const STATES = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
  "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha",
  "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
  "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
  "Ladakh", "Lakshadweep", "Puducherry",
];
const SOCIAL: [string, string, string][] = [
  ["linkedin", "LinkedIn", "https://www.linkedin.com/company/…"], ["facebook", "Facebook", "https://www.facebook.com/…"],
  ["instagram", "Instagram", "https://www.instagram.com/…"], ["x", "X (Twitter)", "https://x.com/…"],
  ["youtube", "YouTube", "https://www.youtube.com/@…"], ["threads", "Threads", "https://www.threads.net/@…"],
  ["whatsapp", "WhatsApp channel", "https://whatsapp.com/channel/…"], ["telegram", "Telegram channel", "https://t.me/…"],
  ["pinterest", "Pinterest", "https://www.pinterest.com/…"], ["reddit", "Reddit", "https://www.reddit.com/r/…"],
  ["discord", "Discord invite", "https://discord.gg/…"], ["github", "GitHub", "https://github.com/…"],
  ["other", "Other profile (Bluesky, Mastodon, Medium…)", "https://bsky.app/profile/…"],
];

type Form = {
  name: string; legal_name: string; tagline: string; description: string; industry: string; company_size: string;
  founded_year: string; website: string; email: string; phone: string; timezone: string; currency: string; country: string;
  address: Record<string, string>; registration: Record<string, string>; social_links: Record<string, string>; contacts: OrgContact[];
};
const toForm = (o: Organization): Form => ({
  name: o.name, legal_name: o.legal_name ?? "", tagline: o.tagline ?? "", description: o.description ?? "", industry: o.industry ?? "",
  company_size: o.company_size ?? "", founded_year: o.founded_year ? String(o.founded_year) : "", website: o.website ?? "",
  email: o.email ?? "", phone: o.phone ?? "", timezone: o.timezone, currency: o.currency ?? "INR", country: o.country ?? "",
  address: { ...(o.address ?? {}) }, registration: { ...(o.registration ?? {}) }, social_links: { ...(o.social_links ?? {}) },
  contacts: (o.contacts ?? []).map((c) => ({ ...c })),
});

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card>
      <h2 className="font-semibold">{title}</h2>
      {description && <p className="mb-4 mt-0.5 text-sm text-muted">{description}</p>}
      <div className={description ? "" : "mt-4"}>{children}</div>
    </Card>
  );
}

function LogoSlot({ label, hint, url, dark, editable, busy, onUpload, onRemove }: {
  label: string; hint: string; url: string | null | undefined; dark?: boolean; editable: boolean; busy: boolean;
  onUpload: (f: File) => void; onRemove: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="flex flex-wrap items-center gap-4">
      <div className={`grid h-24 w-44 shrink-0 place-items-center overflow-hidden rounded-xl border border-line ${dark ? "bg-[#0b1f36]" : "bg-white"}`}>
        {busy ? <Spinner /> : url ? <img src={url} alt={label} className="max-h-20 max-w-40 object-contain" />
          : <span className={`flex flex-col items-center gap-1 text-xs ${dark ? "text-white/60" : "text-muted"}`}><Building2 size={20} />No logo yet</span>}
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-medium">{label}</div>
        <p className="text-sm text-muted">{hint}</p>
        {editable && (
          <div className="mt-2 flex flex-wrap gap-2">
            <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }} />
            <Button type="button" variant="secondary" onClick={() => ref.current?.click()} disabled={busy}>
              {url ? <><Upload size={15} />Replace</> : <><ImagePlus size={15} />Upload</>}
            </Button>
            {url && <Button type="button" variant="ghost" onClick={onRemove} disabled={busy}><Trash2 size={15} />Remove</Button>}
          </div>
        )}
      </div>
    </div>
  );
}

/** Who reports are sent from and to by default (each send can change both). */
function EmailSettings() {
  const { can } = useAuth();
  const setup = useEmailSetup();
  const [to, setTo] = useState<string | null>(null);
  const [cc, setCc] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (json: { default_sender_id?: string | null; report_recipients?: string; report_cc?: string }) =>
      api("/email/settings", { method: "PATCH", json }),
    onSuccess: () => { setup.refetch(); setTo(null); setCc(null); setMsg({ tone: "info", text: "Saved." }); },
    onError: (e) => setMsg({ tone: "danger", text: (e as Error).message }),
  });
  const [testTo, setTestTo] = useState<string | null>(null);
  const test = useMutation({
    mutationFn: (sender_id: string | null) => api<{ message: string }>("/email/test", { method: "POST", json: { sender_id, to: testTo ?? (d?.senders.find((x) => x.default)?.from_email || null) } }),
    onSuccess: (r) => setMsg({ tone: "info", text: r.message }),
    onError: (e) => setMsg({ tone: "danger", text: (e as Error).message }),
  });
  const d = setup.data;
  if (!d) return null;
  const editable = can("org.settings.manage");
  const def = d.senders.find((x) => x.default);
  const toVal = to ?? d.report_recipients.join(", ");
  const ccVal = cc ?? d.report_cc.join(", ");
  return (
    <Section title="Email" description="Reports, exported files, welcome emails and notifications are sent from your own mailbox. Every Export PDF menu and CSV download can email the file.">
      <div className="space-y-4">
        {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
        {!d.senders.length ? (
          <Alert tone="warn">No mailbox is connected yet. Add one in <a className="underline" href="/settings/integrations">Settings → Integrations → Email (SMTP)</a>:
            choose “GoDaddy Professional Email” in Quick setup and enter info@hrpai.in and its password.</Alert>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Send from">
              <Select value={def?.id ?? ""} disabled={!editable || save.isPending}
                onChange={(e) => save.mutate({ default_sender_id: e.target.value })}>
                {d.senders.filter((x) => x.enabled).map((x) => (
                  <option key={x.id} value={x.id}>{x.from_name ? `${x.from_name} <${x.from_email}>` : x.from_email || x.name}</option>
                ))}
              </Select>
            </Field>
            {can("integration.manage") && (
              <>
                <Field label="Send a test to">
                  <Input type="email" value={testTo ?? def?.from_email ?? ""} onChange={(e) => setTestTo(e.target.value)} placeholder="you@hrpai.in" />
                </Field>
                <Button type="button" variant="secondary" disabled={test.isPending} onClick={() => test.mutate(def?.id ?? null)}>
                  {test.isPending ? "Sending…" : "Send test email"}
                </Button>
              </>
            )}
          </div>
        )}
        <Field label="Send reports to (default)" hint="Filled in automatically when you email a report or file; you can still change it each time. Separate addresses with commas.">
          <Input value={toVal} disabled={!editable} onChange={(e) => setTo(e.target.value)} placeholder="ceo@hrpai.in, marketing@hrpai.in" />
        </Field>
        <Field label="Cc (default, optional)"><Input value={ccVal} disabled={!editable} onChange={(e) => setCc(e.target.value)} /></Field>
        {editable && (to !== null || cc !== null) && (
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => { setTo(null); setCc(null); }}>Discard</Button>
            <Button type="button" disabled={save.isPending} onClick={() => save.mutate({ report_recipients: toVal, report_cc: ccVal })}>
              {save.isPending ? "Saving…" : "Save recipients"}</Button>
          </div>
        )}
      </div>
    </Section>
  );
}

/** Auto-approval: posts are scheduled without waiting for a person; finished articles go to the website. */
function AutoApprovalSettings() {
  const { can } = useAuth();
  const st = useSystemStatus();
  const [msg, setMsg] = useState<string | null>(null);
  const waiting = useQuery({
    queryKey: ["publishing-waiting"],
    queryFn: () => api<{ waiting: number; would_approve: number }>("/organization/publishing/waiting"),
  });
  const save = useMutation({
    mutationFn: (json: { auto_approve_posts?: boolean; auto_publish_articles?: boolean; approve_waiting?: boolean;
                         publish_held_posts?: boolean; blog_autopilot?: BlogAutopilot }) =>
      api<{ approved: number; still_waiting: number }>("/organization/publishing", { method: "PATCH", json }),
    onSuccess: (r, v) => {
      if (v.approve_waiting) setMsg(`Approved ${r.approved} waiting post${r.approved === 1 ? "" : "s"}.`
        + (r.still_waiting ? ` ${r.still_waiting} still need a look (quality checks or similarity).` : ""));
      st.refetch(); waiting.refetch();
    },
  });
  const cfg = st.data?.settings;
  if (!cfg) return null;
  const busy = save.isPending;
  const canPosts = can("org.settings.manage") && can("content.approve");
  const canArticles = can("org.settings.manage") && can("seo.publish");
  const w = waiting.data;
  return (
    <Section title="Auto-approval"
      description="Let the studio publish without waiting for you. You still get notified about everything that goes out, and you can turn this off at any time.">
      <div className="space-y-5">
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        {msg && <Alert tone="info">{msg}</Alert>}
        <div>
          <Toggle checked={cfg.auto_approve_posts} disabled={!canPosts || busy}
            onChange={(v) => { setMsg(null); save.mutate({ auto_approve_posts: v }); }}
            label="Approve all social posts automatically" />
          <p className="mt-1 text-xs text-muted">
            Applies to every post the AI writes: schedules, campaigns, festival greetings and posts shared from articles.
            Each post is scheduled for its slot; posts without a time go out about 15 minutes after they're ready.
            Posts that break a platform rule (too long, too many hashtags) still wait for you, and so do posts that score
            low or are too similar to an earlier post unless you turn on the next option. Drafts you write by hand stay drafts.
          </p>
          {cfg.auto_approve_posts && (
            <div className="mt-3">
              <Toggle checked={cfg.publish_held_posts} disabled={!canPosts || busy}
                onChange={(v) => { setMsg(null); save.mutate({ publish_held_posts: v }); }}
                label="Also publish posts that are similar to an earlier one or score low" />
              <p className="mt-1 text-xs text-muted">
                Fully hands-off: these posts go out too, with a note on the post saying why they would have waited.
                The AI still tries to write a different version first.
              </p>
            </div>
          )}
          {cfg.auto_approve_posts && w && w.would_approve > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-primary-50/50 px-3 py-2 text-sm">
              <span>{w.would_approve} post{w.would_approve === 1 ? " is" : "s are"} already waiting and pass the checks.</span>
              <Button variant="ghost" disabled={busy} onClick={() => save.mutate({ approve_waiting: true })}>
                {busy ? "Approving…" : `Approve ${w.would_approve === 1 ? "it" : "them"} now`}
              </Button>
            </div>
          )}
        </div>
        <div>
          <Toggle checked={cfg.auto_publish_articles} disabled={!canArticles || busy}
            onChange={(v) => { setMsg(null); save.mutate({ auto_publish_articles: v }); }}
            label="Publish finished articles and blogs to the website automatically" />
          <p className="mt-1 text-xs text-muted">
            When an article is written and its SEO score is at least 60, it's published to your first website
            connection (Settings → Integrations → Website). Lower-scoring articles stay drafts for you to improve.
          </p>
        </div>
        <BlogAutopilotSettings cfg={cfg.blog_autopilot} disabled={!(can("org.settings.manage") && can("seo.manage")) || busy}
          onSave={(b) => { setMsg(null); save.mutate({ blog_autopilot: b }); }} />
      </div>
    </Section>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Write blog articles on chosen days without anyone starting them. Saves on change. */
function BlogAutopilotSettings({ cfg, disabled, onSave }: { cfg: BlogAutopilot; disabled: boolean; onSave: (b: BlogAutopilot) => void }) {
  const [time, setTime] = useState<string | null>(null);
  const toggleDay = (d: number) => {
    const days = cfg.days.includes(d) ? cfg.days.filter((x) => x !== d) : [...cfg.days, d].sort();
    if (days.length) onSave({ ...cfg, days });
  };
  return (
    <div>
      <Toggle checked={cfg.enabled} disabled={disabled} onChange={(v) => onSave({ ...cfg, enabled: v })}
        label="Write blog articles automatically (blog autopilot)" />
      <p className="mt-1 text-xs text-muted">
        On the days you pick, the AI chooses the next keyword from your SEO keyword ideas (finding new ideas when they run
        out), writes the article with a cover image and scores it. With the option above on, it's then published to your
        website.
      </p>
      {cfg.enabled && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          {WEEKDAYS.map((n, i) => (
            <button key={n} type="button" disabled={disabled} aria-pressed={cfg.days.includes(i)} onClick={() => toggleDay(i)}
              className={`rounded-full border px-3 py-1 ${cfg.days.includes(i) ? "border-primary-500 bg-primary-50 text-primary-700" : "border-line text-muted"}`}>
              {n}
            </button>
          ))}
          <span className="ml-2 text-muted">at</span>
          <Input type="time" className="w-32" disabled={disabled} value={time ?? cfg.time}
            onChange={(e) => setTime(e.target.value)}
            onBlur={() => { if (time && time !== cfg.time) onSave({ ...cfg, time }); setTime(null); }} />
        </div>
      )}
    </div>
  );
}

/** How the studio behaves when the laptop was off, asleep or offline at a post's time. Saves on change. */
function CatchUpSettings({ editable }: { editable: boolean }) {
  const st = useSystemStatus();
  const [gap, setGap] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (json: { catch_up_gap_minutes?: number; wake_for_posts?: boolean }) =>
      api("/organization/publishing", { method: "PATCH", json }),
    onSuccess: () => st.refetch(),
  });
  const cfg = st.data?.settings;
  if (!cfg) return null;
  const gapValue = gap ?? String(cfg.catch_up_gap_minutes);
  const commitGap = () => {
    const n = Math.max(0, Math.min(240, Number(gapValue) || 0));
    setGap(null);
    if (n !== cfg.catch_up_gap_minutes) save.mutate({ catch_up_gap_minutes: n });
  };
  const wakeAt = st.data?.wake_at;
  return (
    <Section title="When the laptop was off, asleep or offline"
      description="Nothing is dropped. Posts whose time passed while the app couldn't run go out as soon as it's back: oldest first, one at a time per account, and you get one summary of what's going out when. Festival greetings whose day has passed wait for you instead of posting late on their own.">
      <div className="space-y-4">
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Field label="Space late posts on the same account (minutes)" hint="0 posts them all at once. 20 is a gentle default.">
          <Input type="number" min={0} max={240} className="w-32" value={gapValue} disabled={!editable || save.isPending}
            onChange={(e) => setGap(e.target.value)} onBlur={commitGap} onKeyDown={(e) => e.key === "Enter" && commitGap()} />
        </Field>
        <div>
          <Toggle checked={cfg.wake_for_posts} disabled={!editable || save.isPending}
            onChange={(v) => save.mutate({ wake_for_posts: v })} label="Wake this laptop from sleep or hibernation for scheduled posts" />
          <p className="mt-1 text-xs text-muted">
            Windows only. The laptop wakes about 3 minutes before the next post, posts it, then can sleep again. It
            can't wake a laptop that is shut down. Keep it plugged in: most laptops ignore wake timers on battery.
            {cfg.wake_for_posts && wakeAt ? ` Next wake-up: ${new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(wakeAt))}.` : ""}
          </p>
        </div>
      </div>
    </Section>
  );
}

/** Require two-step sign-in for every member (Administrators only). */
function SignInSecurity() {
  const q = useQuery({
    queryKey: ["org-security"],
    queryFn: () => api<{ require_2fa: boolean; members: number; members_with_2fa: number; can_change: boolean }>("/organization/security"),
  });
  const save = useMutation({
    mutationFn: (require_2fa: boolean) => api("/organization/security", { method: "PATCH", json: { require_2fa } }),
    onSuccess: () => q.refetch(),
  });
  const d = q.data;
  if (!d) return null;
  return (
    <Section title="Sign-in security"
      description="Two-step sign-in asks for a code from an authenticator app after the password, so a leaked password alone can't open the account.">
      <div className="space-y-2">
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Toggle checked={d.require_2fa} disabled={!d.can_change || save.isPending} onChange={(v) => save.mutate(v)}
          label="Require two-step sign-in for everyone in this organization" />
        <p className="text-xs text-muted">
          {d.members_with_2fa} of {d.members} members use it now. When required, the others are asked to set it up the next time they sign in.
          {!d.can_change && " Only Administrators can change this."}
        </p>
      </div>
    </Section>
  );
}

export default function OrganizationSettings() {
  const { session, can } = useAuth();
  const org = session?.organization;
  const editable = can("org.settings.manage");
  const [f, setF] = useState<Form | null>(null);
  const [base, setBase] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!org) return;
    const fresh = toForm(org);
    setF((cur) => (!cur || JSON.stringify(cur) === base ? fresh : cur));
    setBase(JSON.stringify(fresh));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [org]);

  const save = useMutation({
    mutationFn: () => {
      const v = f!;
      return api<Organization>("/organization", { method: "PATCH", json: {
        ...v, founded_year: v.founded_year ? Number(v.founded_year) : null, company_size: v.company_size || null,
        email: v.email || null, website: v.website || null,
        contacts: v.contacts.filter((c) => c.name.trim()).map((c) => ({ ...c, email: c.email || null, phone: c.phone || null, role: c.role || null })),
      } });
    },
    onSuccess: async (o) => { const fr = toForm(o); setF(fr); setBase(JSON.stringify(fr)); setNotice("Changes saved."); await refreshSession(); },
  });
  const logo = useMutation({
    mutationFn: ({ variant, file }: { variant: "main" | "dark"; file: File | null }) => {
      if (!file) return api<Organization>(`/organization/logo?variant=${variant}`, { method: "DELETE" });
      const fd = new FormData();
      fd.append("files", file, file.name);
      return api<Organization>(`/organization/logo?variant=${variant}`, { method: "POST", body: fd });
    },
    onSuccess: async (_o, v) => { setNotice(v.file ? "Logo updated." : "Logo removed."); await refreshSession(); },
  });

  const dirty = !!f && JSON.stringify(f) !== base;
  const descLeft = useMemo(() => 5000 - (f?.description.length ?? 0), [f?.description]);
  if (!org || !f) return null;
  const tzs = TIMEZONES.includes(f.timezone) ? TIMEZONES : [f.timezone, ...TIMEZONES];
  const set = (k: keyof Form) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const setIn = (group: "address" | "registration" | "social_links", k: string) => (e: { target: { value: string } }) =>
    setF({ ...f, [group]: { ...f[group], [k]: e.target.value } });
  const setContact = (i: number, k: keyof OrgContact, v: string) => setF({ ...f, contacts: f.contacts.map((c, j) => (j === i ? { ...c, [k]: v } : c)) });
  const err = (save.error || logo.error) as Error | null;

  return (
    <div className="pb-20">
      <PageHeader title="Organization" description="Your company's details and logo. They appear in reports and emails, and the AI uses them when it writes." />
      <div className="max-w-4xl space-y-5">
        {err && <Alert>{err.message}</Alert>}
        {notice && !err && <Alert tone="info">{notice}</Alert>}

        <Section title="Logo" description="PNG, JPG or WebP, ideally with a transparent background. The main logo is also used on generated images if your brand kit has no logo yet.">
          <div className="space-y-5">
            <LogoSlot label="Main logo" hint="For light backgrounds: reports, emails and generated images." url={org.logo_url} editable={editable}
              busy={logo.isPending && logo.variables?.variant === "main"}
              onUpload={(file) => logo.mutate({ variant: "main", file })} onRemove={() => logo.mutate({ variant: "main", file: null })} />
            <LogoSlot label="Logo for dark backgrounds" hint="Optional. Shown in the sidebar. Falls back to the main logo." url={org.logo_dark_url} dark editable={editable}
              busy={logo.isPending && logo.variables?.variant === "dark"}
              onUpload={(file) => logo.mutate({ variant: "dark", file })} onRemove={() => logo.mutate({ variant: "dark", file: null })} />
          </div>
        </Section>

        {can("content.view") && <AutoApprovalSettings />}
        {can("content.view") && <EmailSettings />}
        {can("content.view") && <CatchUpSettings editable={editable} />}
        <SignInSecurity />

        <form className="space-y-5" onSubmit={(e: FormEvent) => { e.preventDefault(); setNotice(null); save.mutate(); }}>
          <fieldset disabled={!editable} className="space-y-5">
            <Section title="Company">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Name" hint="Shown across the app."><Input required minLength={2} value={f.name} onChange={set("name")} /></Field>
                <Field label="Legal name" hint="As registered, for invoices and reports."><Input value={f.legal_name} onChange={set("legal_name")} placeholder="HRP AI LLP" /></Field>
                <div className="md:col-span-2">
                  <Field label="Tagline"><Input maxLength={200} value={f.tagline} onChange={set("tagline")} placeholder="One line on what you do" /></Field>
                </div>
                <div className="md:col-span-2">
                  <Field label="About the company" hint={`What you do, for whom, and what makes you different. ${descLeft.toLocaleString()} characters left.`}>
                    <Textarea rows={5} maxLength={5000} value={f.description} onChange={set("description")} />
                  </Field>
                </div>
                <Field label="Industry">
                  <Input list="org-industries" value={f.industry} onChange={set("industry")} placeholder="Pick or type" />
                  <datalist id="org-industries">{INDUSTRIES.map((i) => <option key={i} value={i} />)}</datalist>
                </Field>
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Company size">
                    <Select value={f.company_size} onChange={set("company_size")}>
                      <option value="">Not set</option>
                      {SIZES.map((s) => <option key={s} value={s}>{s} {s === "1" ? "person" : "people"}</option>)}
                    </Select>
                  </Field>
                  <Field label="Founded"><Input type="number" min={1800} max={2100} value={f.founded_year} onChange={set("founded_year")} placeholder="2024" /></Field>
                </div>
              </div>
              <p className="mt-4 text-sm text-muted">Short name: <code className="text-ink">{org.slug}</code></p>
            </Section>

            <Section title="Contact">
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Website" hint="Company intelligence reads this site."><Input type="url" placeholder="https://example.com" value={f.website} onChange={set("website")} /></Field>
                <Field label="Email"><Input type="email" placeholder="hello@example.com" value={f.email} onChange={set("email")} /></Field>
                <Field label="Phone"><Input type="tel" placeholder="+91 98765 43210" value={f.phone} onChange={set("phone")} /></Field>
              </div>
            </Section>

            <Section title="Address and region" description="Time zone sets when posts go out and reports are sent.">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Address line 1"><Input value={f.address.line1 ?? ""} onChange={setIn("address", "line1")} /></Field>
                <Field label="Address line 2"><Input value={f.address.line2 ?? ""} onChange={setIn("address", "line2")} /></Field>
                <Field label="City"><Input value={f.address.city ?? ""} onChange={setIn("address", "city")} /></Field>
                <Field label="State">
                  <Input list="org-states" value={f.address.state ?? ""} onChange={setIn("address", "state")} />
                  <datalist id="org-states">{STATES.map((s) => <option key={s} value={s} />)}</datalist>
                </Field>
                <Field label="PIN / postal code"><Input inputMode="numeric" value={f.address.postal_code ?? ""} onChange={setIn("address", "postal_code")} /></Field>
                <Field label="Country"><Input value={f.country} onChange={set("country")} placeholder="India" /></Field>
                <Field label="Time zone">
                  <Select value={f.timezone} onChange={set("timezone")}>
                    {tzs.map((t) => <option key={t} value={t}>{t === "Asia/Kolkata" ? "Asia/Kolkata (IST)" : t}</option>)}
                  </Select>
                </Field>
                <Field label="Currency" hint="Used for budgets and costs in reports.">
                  <Select value={f.currency} onChange={set("currency")}>
                    {(CURRENCIES.includes(f.currency) ? CURRENCIES : [f.currency, ...CURRENCIES]).map((c) => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </Field>
              </div>
            </Section>

            <Section title="Registration" description="Optional. For invoices and official documents.">
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="GSTIN"><Input className="uppercase" maxLength={15} placeholder="29ABCDE1234F1Z5" value={f.registration.gstin ?? ""} onChange={setIn("registration", "gstin")} /></Field>
                <Field label="PAN"><Input className="uppercase" maxLength={10} placeholder="ABCDE1234F" value={f.registration.pan ?? ""} onChange={setIn("registration", "pan")} /></Field>
                <Field label="CIN / LLPIN"><Input className="uppercase" maxLength={30} value={f.registration.cin ?? ""} onChange={setIn("registration", "cin")} /></Field>
                <Field label="Udyam number"><Input className="uppercase" maxLength={30} placeholder="UDYAM-XX-00-0000000" value={f.registration.udyam ?? ""} onChange={setIn("registration", "udyam")} /></Field>
                <div className="md:col-span-2"><Field label="Other registration"><Input maxLength={60} value={f.registration.other ?? ""} onChange={setIn("registration", "other")} /></Field></div>
              </div>
            </Section>

            <Section title="Social profiles" description="Your public company pages. Connecting accounts for posting is done in Social accounts.">
              <div className="grid gap-4 md:grid-cols-2">
                {SOCIAL.map(([k, l, ph]) => (
                  <Field key={k} label={l}><Input type="text" inputMode="url" placeholder={ph} value={f.social_links[k] ?? ""} onChange={setIn("social_links", k)} /></Field>
                ))}
              </div>
            </Section>

            <Section title="Key contacts" description="People to reach about marketing, billing or approvals.">
              <div className="space-y-3">
                {f.contacts.length === 0 && <p className="text-sm text-muted">No contacts yet.</p>}
                {f.contacts.map((c, i) => (
                  <div key={i} className="grid items-end gap-3 rounded-lg border border-line p-3 md:grid-cols-[minmax(0,1fr)_1fr_1fr_1fr_auto]">
                    <Field label="Name"><Input required value={c.name} onChange={(e) => setContact(i, "name", e.target.value)} /></Field>
                    <Field label="Role"><Input value={c.role ?? ""} onChange={(e) => setContact(i, "role", e.target.value)} placeholder="Marketing lead" /></Field>
                    <Field label="Email"><Input type="email" value={c.email ?? ""} onChange={(e) => setContact(i, "email", e.target.value)} /></Field>
                    <Field label="Phone"><Input type="tel" value={c.phone ?? ""} onChange={(e) => setContact(i, "phone", e.target.value)} /></Field>
                    {editable && <Button type="button" variant="ghost" aria-label="Remove contact" onClick={() => setF({ ...f, contacts: f.contacts.filter((_, j) => j !== i) })}><Trash2 size={15} /></Button>}
                  </div>
                ))}
                {editable && f.contacts.length < 20 && (
                  <Button type="button" variant="secondary" onClick={() => setF({ ...f, contacts: [...f.contacts, { name: "", role: "", email: "", phone: "" }] })}><Plus size={15} />Add contact</Button>
                )}
              </div>
            </Section>
          </fieldset>

          {editable && (dirty || save.isPending) && (
            <div className="sticky bottom-4 z-10 flex items-center justify-end gap-3 rounded-xl border border-primary-500/40 bg-surface px-4 py-3 shadow-[0_8px_24px_rgba(6,47,89,0.12)]">
              <span className="mr-auto text-sm text-muted">You have unsaved changes.</span>
              <Button type="button" variant="ghost" onClick={() => setF(JSON.parse(base))}>Discard</Button>
              <Button type="submit" disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</Button>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
