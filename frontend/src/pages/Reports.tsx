import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileDown, Loader2, Mail } from "lucide-react";
import { api } from "../api/client";
import { downloadReport, PRESETS, presetRange } from "../components/ExportPdf";
import { EmailDialog } from "../components/EmailDialog";
import { Alert, Card, PageHeader, Select, Spinner } from "../components/ui";

type Item = { kind: string; title: string; description: string; group: string; period: boolean; needs_id: boolean };

/** Every PDF report in one place. Single posts and articles are exported from their own page. */
export default function Reports() {
  const q = useQuery({ queryKey: ["report-catalog"], queryFn: () => api<{ items: Item[] }>("/reports/catalog") });
  const [preset, setPreset] = useState("30");
  const [custom, setCustom] = useState(presetRange("30"));
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [emailing, setEmailing] = useState<Item | null>(null);
  const range = preset === "custom" ? custom : presetRange(preset);

  const run = async (it: Item) => {
    setBusy(it.kind); setErr(null);
    try {
      await downloadReport(it.kind, it.period ? range : {});
    } catch (e) {
      setErr(`${it.title}: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const items = (q.data?.items ?? []).filter((i) => !i.needs_id);
  const groups = [...new Set(items.map((i) => i.group))];
  return (
    <div>
      <PageHeader title="Reports"
        description="Download any part of the studio as a branded PDF. Every page also has an Export PDF button at the top right; single posts and articles can be exported from their own page." />
      <Card className="mb-6 flex flex-wrap items-end gap-3 p-4">
        <label className="text-sm">
          <span className="mb-1 block text-muted">Period (for reports that cover a period)</span>
          <Select value={preset} onChange={(e) => setPreset(e.target.value)}>
            {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            <option value="custom">Custom…</option>
          </Select>
        </label>
        {preset === "custom" && (
          <>
            <label className="text-sm"><span className="mb-1 block text-muted">From</span>
              <input type="date" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })}
                className="h-10 rounded-lg border border-line bg-surface px-2.5" /></label>
            <label className="text-sm"><span className="mb-1 block text-muted">To</span>
              <input type="date" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })}
                className="h-10 rounded-lg border border-line bg-surface px-2.5" /></label>
          </>
        )}
        <span className="pb-2 text-sm text-muted">{range.start} to {range.end}</span>
      </Card>
      {err && <div className="mb-4"><Alert>{err}</Alert></div>}
      {q.isLoading && <Spinner />}
      {groups.map((g) => (
        <section key={g} className="mb-8">
          <h2 className="mb-3 text-lg font-semibold">{g}</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.filter((i) => i.group === g).map((it) => (
              <div key={it.kind} className="flex flex-col rounded-xl border border-line bg-surface p-4 shadow-sm">
                <span className="flex items-center gap-2 font-semibold">
                  {busy === it.kind ? <Loader2 size={16} className="animate-spin" /> : <FileDown size={16} className="text-primary-700" />}
                  {it.title}
                </span>
                <span className="mt-1 text-sm text-muted">{it.description}</span>
                <span className="mt-auto flex items-center gap-2 pt-3">
                  <button type="button" disabled={!!busy} onClick={() => run(it)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-sm hover:bg-primary-50 disabled:opacity-60">
                    <FileDown size={14} />{busy === it.kind ? "Making…" : "Download"}</button>
                  <button type="button" disabled={!!busy} onClick={() => setEmailing(it)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-sm hover:bg-primary-50 disabled:opacity-60">
                    <Mail size={14} />Email</button>
                  <span className="ml-auto text-xs text-muted">{it.period ? "Period above" : "Current state"}</span>
                </span>
              </div>
            ))}
          </div>
        </section>
      ))}
      {emailing && (
        <EmailDialog onClose={() => setEmailing(null)}
          what={{ type: "report", kind: emailing.kind, title: emailing.title, params: emailing.period ? range : {} }} />
      )}
    </div>
  );
}
