import { useEffect, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, refreshSession, type Organization } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Field, Input, PageHeader, Select } from "../components/ui";

export const TIMEZONES = [
  "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Europe/London", "Europe/Berlin",
  "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney", "UTC",
];

export default function OrganizationSettings() {
  const { session, can } = useAuth();
  const org = session?.organization;
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [timezone, setTimezone] = useState("Asia/Kolkata");
  const [saved, setSaved] = useState(false);
  const editable = can("org.settings.manage");

  useEffect(() => {
    if (org) { setName(org.name); setWebsite(org.website ?? ""); setTimezone(org.timezone); }
  }, [org]);

  const save = useMutation({
    mutationFn: () => api<Organization>("/organization", { method: "PATCH", json: { name, website: website || null, timezone } }),
    onSuccess: async () => { setSaved(true); await refreshSession(); },
  });

  if (!org) return null;
  const tzs = TIMEZONES.includes(org.timezone) ? TIMEZONES : [org.timezone, ...TIMEZONES];

  return (
    <div>
      <PageHeader title="Organization" description="Basic details. Schedules, reports and notifications use this time zone." />
      <form className="max-w-lg space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); setSaved(false); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        {saved && <Alert tone="info">Changes saved.</Alert>}
        <Field label="Organization name"><Input required disabled={!editable} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Website" hint="Company intelligence reads this site to learn about your products and clients.">
          <Input type="url" placeholder="https://example.com" disabled={!editable} value={website} onChange={(e) => setWebsite(e.target.value)} />
        </Field>
        <Field label="Time zone">
          <Select disabled={!editable} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {tzs.map((t) => <option key={t} value={t}>{t === "Asia/Kolkata" ? "Asia/Kolkata (IST)" : t}</option>)}
          </Select>
        </Field>
        <div className="text-sm text-muted">Short name: <code className="text-ink">{org.slug}</code></div>
        {editable && <Button type="submit" disabled={save.isPending}>Save changes</Button>}
      </form>
    </div>
  );
}
