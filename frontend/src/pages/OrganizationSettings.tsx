import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { Building2, ImagePlus, Plus, Trash2, Upload } from "lucide-react";
import { api, refreshSession, type OrgContact, type Organization } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Spinner, Textarea } from "../components/ui";

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
  ["youtube", "YouTube", "https://www.youtube.com/@…"], ["tiktok", "TikTok", "https://www.tiktok.com/@…"], ["threads", "Threads", "https://www.threads.net/@…"],
  ["pinterest", "Pinterest", "https://www.pinterest.com/…"], ["github", "GitHub", "https://github.com/…"],
  ["other", "Other profile (Bluesky, Mastodon, TikTok…)", "https://bsky.app/profile/…"],
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
                  <div key={i} className="grid items-end gap-3 rounded-lg border border-line p-3 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
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
