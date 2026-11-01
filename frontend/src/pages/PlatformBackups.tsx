import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { DatabaseBackup, Download } from "lucide-react";
import { api, downloadFile } from "../api/client";
import { Alert, Badge, Button, Card, Field, Input, PageHeader, Spinner, Stat, StatusBadge, Toggle, formatDate } from "../components/ui";

type Run = {
  id: string; kind: "daily" | "manual"; status: "running" | "ok" | "failed"; method: string | null; file_name: string | null;
  size_bytes: number | null; media_file: string | null; media_size_bytes: number | null; copied_to: string | null;
  warning: string | null; error: string | null; started_at: string; finished_at: string | null; removed: boolean;
  triggered_by: string | null;
};
type Settings = { enabled: boolean; time: string; keep_daily: number; keep_weekly: number; copy_to: string; include_media: boolean };
type Data = { runs: Run[]; settings: Settings; disk: { folder: string; files: number; total_bytes: number; free_bytes: number | null } };

function size(n: number | null | undefined) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function SettingsCard({ settings, onSaved }: { settings: Settings; onSaved: () => void }) {
  const [f, setF] = useState(settings);
  const [saved, setSaved] = useState(false);
  useEffect(() => setF(settings), [settings]);
  const save = useMutation({
    mutationFn: (json: Partial<Settings>) => api<Settings>("/platform/backups/settings", { method: "PATCH", json }),
    onSuccess: (s) => { setF(s); setSaved(true); onSaved(); },
  });
  const dirty = JSON.stringify(f) !== JSON.stringify(settings);
  return (
    <Card>
      <h2 className="font-semibold">Settings</h2>
      <p className="mt-0.5 mb-4 text-sm text-muted">
        The backup runs every day at this time on the laptop's clock. If the laptop is off then, it runs as soon as the studio starts again.
      </p>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setSaved(false); save.mutate(f); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        {saved && !dirty && <Alert tone="info">Backup settings saved.</Alert>}
        <Toggle checked={f.enabled} onChange={(v) => setF({ ...f, enabled: v })} label="Back up the database every day" />
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Time (24-hour)"><Input value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} placeholder="02:30" /></Field>
          <Field label="Keep daily backups for" hint="days">
            <Input type="number" min={1} max={90} value={f.keep_daily} onChange={(e) => setF({ ...f, keep_daily: Number(e.target.value) })} />
          </Field>
          <Field label="Also keep one a week for" hint="weeks">
            <Input type="number" min={0} max={52} value={f.keep_weekly} onChange={(e) => setF({ ...f, keep_weekly: Number(e.target.value) })} />
          </Field>
        </div>
        <Field label="Also copy each backup to this folder (optional)"
          hint="A folder that OneDrive or Google Drive keeps in sync, so a copy survives if the laptop is lost. Leave empty to keep backups on this laptop only.">
          <Input value={f.copy_to} onChange={(e) => setF({ ...f, copy_to: e.target.value })} placeholder="C:\Users\you\OneDrive\HRP Backups" />
        </Field>
        <div>
          <Toggle checked={f.include_media} onChange={(v) => setF({ ...f, include_media: v })} label="Also back up media files (images, videos, uploads)" />
          <p className="mt-1 text-xs text-muted">Zips storage\orgs next to each backup. It can be large: check the free space below.</p>
        </div>
        <Button type="submit" disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>
      </form>
    </Card>
  );
}

export default function PlatformBackups() {
  const q = useQuery({
    queryKey: ["platform-backups"],
    queryFn: () => api<Data>("/platform/backups"),
    refetchInterval: (query) => (query.state.data?.runs.some((r) => r.status === "running") ? 3000 : false),
  });
  const [dlError, setDlError] = useState<string | null>(null);
  const now = useMutation({ mutationFn: () => api<Run>("/platform/backups/run", { method: "POST" }), onSuccess: () => q.refetch() });
  const running = q.data?.runs.some((r) => r.status === "running");
  const last = q.data?.runs.find((r) => r.status === "ok");
  const lastFailed = q.data?.runs[0]?.status === "failed" ? q.data.runs[0] : null;

  async function download(r: Run, media = false) {
    setDlError(null);
    try { await downloadFile(`/platform/backups/${r.id}/download${media ? "?media=true" : ""}`, (media ? r.media_file : r.file_name) || "backup"); }
    catch (e) { setDlError((e as Error).message); }
  }

  return (
    <div>
      <PageHeader
        title="Backups"
        description="Automatic daily copies of the database, so nothing is lost if the laptop breaks. Restore one with restore.ps1 (see the README)."
        action={<Button onClick={() => now.mutate()} disabled={now.isPending || running}><DatabaseBackup size={16} /> {running ? "Backing up…" : "Back up now"}</Button>}
      />
      {(q.error || now.error) && <Alert>{((q.error || now.error) as Error).message}</Alert>}
      {dlError && <Alert>{dlError}</Alert>}
      {q.isLoading && <Spinner label="Loading…" />}
      {q.data && (
        <div className="space-y-5">
          {lastFailed && <Alert>The last backup failed: {lastFailed.error}</Alert>}
          {!q.data.settings.enabled && <Alert tone="warn">Daily backups are turned off.</Alert>}
          <Card>
            <div className="grid gap-4 sm:grid-cols-4">
              <Stat label="Last good backup" value={last ? formatDate(last.finished_at) : "None yet"} />
              <Stat label="Backups kept" value={q.data.disk.files} />
              <Stat label="Space used" value={size(q.data.disk.total_bytes)} />
              <Stat label="Free on this drive" value={size(q.data.disk.free_bytes)} />
            </div>
            <p className="mt-3 text-xs text-muted [overflow-wrap:anywhere]">Folder: {q.data.disk.folder}</p>
          </Card>

          <div className="overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full text-sm">
              <thead className="border-b border-line text-left text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-medium">When</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium">Size</th>
                  <th className="px-4 py-2.5 font-medium">Details</th>
                  <th className="px-4 py-2.5 font-medium"><span className="sr-only">Download</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {q.data.runs.map((r) => (
                  <tr key={r.id} className={r.removed ? "text-muted" : ""}>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {formatDate(r.started_at)}
                      <div className="text-xs text-muted">{r.kind === "manual" ? `Back up now${r.triggered_by ? ` · ${r.triggered_by}` : ""}` : "Daily"}</div>
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={r.status} /> {r.removed && <Badge>Rotated out</Badge>}</td>
                    <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                      {size(r.size_bytes)}{r.media_size_bytes != null && <div className="text-xs text-muted">media {size(r.media_size_bytes)}</div>}
                    </td>
                    <td className="max-w-md px-4 py-3 text-xs [overflow-wrap:anywhere]">
                      {r.error && <div className="text-danger">{r.error}</div>}
                      {r.warning && <div className="text-amber-800">{r.warning}</div>}
                      {r.file_name && <div className="text-muted">{r.file_name}{r.method ? ` · via ${r.method}` : ""}</div>}
                      {r.copied_to && <div className="text-muted">Copied to {r.copied_to}</div>}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      {r.status === "ok" && !r.removed && (
                        <>
                          <Button variant="ghost" onClick={() => download(r)}><Download size={14} /> Database</Button>
                          {r.media_file && <Button variant="ghost" onClick={() => download(r, true)}><Download size={14} /> Media</Button>}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!q.data.runs.length && <p className="px-4 py-6 text-muted">No backups yet. Press “Back up now” to make the first one.</p>}
          </div>

          <SettingsCard settings={q.data.settings} onSaved={() => q.refetch()} />
        </div>
      )}
    </div>
  );
}
