import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Alert, Button, Card, Field, Input, Select, Spinner, Textarea, Toggle } from "../../components/ui";
import type { Settings } from "./types";

type Meta = { settings: Settings; website: string | null; weekdays: string[]; default_user_agent: string };

export default function SettingsTab({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["siteaudit", "settings"], queryFn: () => api<Meta>("/site-audit/settings") });
  const [s, setS] = useState<Settings | null>(null);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (q.data && !s) {
      setS(q.data.settings);
      setInclude(q.data.settings.include.join("\n"));
      setExclude(q.data.settings.exclude.join("\n"));
    }
  }, [q.data, s]);
  const save = useMutation({
    mutationFn: () => api<{ settings: Settings }>("/site-audit/settings", {
      method: "PUT",
      json: { ...s, start_url: s?.start_url || null, include: include.split("\n"), exclude: exclude.split("\n") },
    }),
    onSuccess: (r) => { setS(r.settings); setSaved(true); qc.invalidateQueries({ queryKey: ["siteaudit"] }); },
  });
  if (!s || !q.data) return <Spinner label="Loading…" />;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => { setSaved(false); setS({ ...s, [k]: v }); };
  const dis = !canManage;
  const num = (k: "max_pages" | "max_depth" | "psi_pages" | "drop_threshold") => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value));

  return (
    <form className="max-w-3xl space-y-5" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Card className="space-y-4">
        <h3 className="font-semibold">What to check</h3>
        <Field label="Start address" hint={`Leave empty to use your website from Organization settings${q.data.website ? ` (${q.data.website})` : ""}.`}>
          <Input value={s.start_url ?? ""} onChange={(e) => set("start_url", e.target.value)} placeholder="https://www.example.com" disabled={dis} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Page limit" hint="How many URLs to check (1–2,000).">
            <Input type="number" min={1} max={2000} value={s.max_pages} onChange={num("max_pages")} disabled={dis} />
          </Field>
          <Field label="Maximum clicks from the home page" hint="Pages deeper than this aren't followed.">
            <Input type="number" min={1} max={50} value={s.max_depth} onChange={num("max_depth")} disabled={dis} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Only check these sections" hint="One per line, e.g. /blog/* or /products. Empty = everything.">
            <Textarea rows={3} value={include} onChange={(e) => { setSaved(false); setInclude(e.target.value); }} disabled={dis} />
          </Field>
          <Field label="Skip these sections" hint="One per line, e.g. /tag/* or ?sort=">
            <Textarea rows={3} value={exclude} onChange={(e) => { setSaved(false); setExclude(e.target.value); }} disabled={dis} />
          </Field>
        </div>
        <Field label="Crawler name (user agent)" hint={`Leave empty for “${q.data.default_user_agent}”. robots.txt rules for this name are obeyed.`}>
          <Input value={s.user_agent} onChange={(e) => set("user_agent", e.target.value)} disabled={dis} />
        </Field>
        <Toggle checked={s.check_external} onChange={(v) => set("check_external", v)} label="Check links to other websites" disabled={dis} />
      </Card>
      <Card className="space-y-4">
        <h3 className="font-semibold">Page speed</h3>
        <Field label="Pages to measure with PageSpeed Insights" hint="Your most-linked pages, on mobile and desktop. Free; 0 turns it off. An optional free Google API key (Settings → Integrations) raises the daily limit.">
          <Input type="number" min={0} max={25} value={s.psi_pages} onChange={num("psi_pages")} disabled={dis} />
        </Field>
      </Card>
      <Card className="space-y-4">
        <h3 className="font-semibold">Weekly audit and alerts</h3>
        <Toggle checked={s.enabled} onChange={(v) => set("enabled", v)} label="Run the audit automatically every week" disabled={dis} />
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Day">
            <Select value={s.weekday} onChange={(e) => set("weekday", Number(e.target.value))} disabled={dis || !s.enabled}>
              {q.data.weekdays.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </Select>
          </Field>
          <Field label="Time">
            <Input type="time" value={s.run_time} onChange={(e) => set("run_time", e.target.value)} disabled={dis || !s.enabled} />
          </Field>
          <Field label="Alert when health drops by" hint="Points.">
            <Input type="number" min={1} max={50} value={s.drop_threshold} onChange={num("drop_threshold")} disabled={dis} />
          </Field>
        </div>
        <p className="text-xs text-muted">You get a notification when each audit finishes, and an urgent one (email too, if set up in Notifications) when the health score drops.</p>
      </Card>
      {save.error && <Alert>{(save.error as Error).message}</Alert>}
      {saved && <Alert tone="info">Settings saved.</Alert>}
      {canManage && <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>}
    </form>
  );
}
