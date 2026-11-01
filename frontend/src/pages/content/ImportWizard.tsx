import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, FileUp, XCircle } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Modal, Spinner } from "../../components/ui";

type PreviewRow = {
  row: number; ok: boolean; errors: string[]; warnings: string[];
  account: { id: string; name: string; platform: string; platform_label: string } | null; account_input: string;
  scheduled_at: string | null; local_time: string | null; status: "draft" | "review" | "schedule"; post_type: string; post_type_label: string;
  text: string; hashtags: string[]; link: string | null; images: number; char_count: number; max_chars: number | null; outcome: string;
};
type Preview = { rows: PreviewRow[]; total: number; ok: number; errors: number; warnings: number; columns: string[]; auto_approve: boolean };
type CommitResult = { created: number; post_ids: string[]; skipped_rows: number[]; waiting: number; scheduled: number; drafts: number; message: string };
type Template = { filename: string; csv: string; columns: string[]; accounts: { name: string; platform: string; handle: string | null }[]; max_rows: number };

const STATUS: Record<PreviewRow["status"], string> = { draft: "Draft", review: "For review", schedule: "Schedule" };
const COLUMN_HELP: [string, string][] = [
  ["account", "Account name, handle, or platform:name (required)"],
  ["date / time", "YYYY-MM-DD and 24-hour HH:MM, in your organization's time zone"],
  ["text", "The post (required unless there's media)"],
  ["hashtags", "Separated by spaces or commas; # is optional"],
  ["link", "A web address to include"],
  ["image_url / media_id", "A public image address (downloaded into the media library) or a media library id; separate several with |"],
  ["post_type", "text, link, image, carousel… (worked out from the media if empty)"],
  ["pillar", "Content pillar name (optional)"],
  ["status", "draft, review or schedule"],
];

function form(file: File) {
  const f = new FormData();
  f.append("file", file, file.name);
  return f;
}

function saveText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** Upload a CSV → check every row → import the posts. */
export function ImportWizard({ onClose }: { onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [only, setOnly] = useState<"all" | "problems">("all");
  const [tplErr, setTplErr] = useState<string | null>(null);

  const preview = useMutation({ mutationFn: (f: File) => api<Preview>("/content/import/preview", { method: "POST", body: form(f) }) });
  const commit = useMutation({
    mutationFn: ({ f, skip }: { f: File; skip: boolean }) =>
      api<CommitResult>(`/content/import/commit${skip ? "?skip_invalid=true" : ""}`, { method: "POST", body: form(f) }),
    onSuccess: () => { for (const k of ["posts", "approvals", "calendar", "dashboard"]) qc.invalidateQueries({ queryKey: [k] }); },
  });
  const template = async () => {
    setTplErr(null);
    try {
      const t = await api<Template>("/content/import/template");
      saveText(t.filename, t.csv);
    } catch (e) {
      setTplErr((e as Error).message);
    }
  };
  const pick = (f: File | null) => {
    setFile(f);
    commit.reset();
    if (f) preview.mutate(f); else preview.reset();
  };

  const p = preview.data;
  const done = commit.data;
  const rows = (p?.rows ?? []).filter((r) => only === "all" || r.errors.length || r.warnings.length);
  const needsMedia = p?.rows.some((r) => r.ok && r.images > 0) && !can("media.manage");

  return (
    <Modal title="Import posts from a spreadsheet" onClose={onClose} wide>
      {done ? (
        <div className="space-y-4">
          <Alert tone="info"><span className="inline-flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 shrink-0" />{done.message}</span></Alert>
          <div className="grid grid-cols-3 gap-3 text-center">
            {[["Drafts", done.drafts], ["Waiting for approval", done.waiting], ["Scheduled", done.scheduled]].map(([l, v]) => (
              <div key={l} className="rounded-lg border border-line p-3"><div className="text-2xl font-bold tabular-nums">{v}</div><div className="text-xs text-muted">{l}</div></div>
            ))}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {done.waiting > 0 && can("content.approve") && <Link to="/approvals"><Button variant="secondary">Go to approvals</Button></Link>}
            <Button onClick={onClose}>Done</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            {["Upload", "Check the rows", "Import"].map((s, i) => {
              const step = !p ? 0 : 1;
              return <li key={s} className={i === step ? "font-semibold text-ink" : ""}>{i + 1}. {s}</li>;
            })}
          </ol>

          {!p && (
            <>
              <p className="text-sm text-muted">
                Save your spreadsheet as a CSV file (in Excel: File → Save As → CSV UTF-8) with one post per row. Nothing is
                created until you've checked the rows and pressed Import.
              </p>
              <div className="rounded-xl border border-dashed border-line bg-canvas p-5 text-center"
                onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files?.[0] ?? null); }}>
                <FileUp className="mx-auto text-muted" size={26} />
                <p className="mt-2 text-sm">Drop the CSV file here, or</p>
                <Button className="mt-2" onClick={() => input.current?.click()} disabled={preview.isPending}>Choose a file</Button>
                <input ref={input} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
                {preview.isPending && <div className="mt-3"><Spinner label={`Checking ${file?.name ?? "the file"}…`} /></div>}
              </div>
              {preview.error && <Alert>{(preview.error as Error).message}</Alert>}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Button variant="secondary" onClick={template}><Download size={15} /> Download the template</Button>
                {tplErr && <span className="text-sm text-danger">{tplErr}</span>}
              </div>
              <details className="rounded-lg border border-line p-3 text-sm">
                <summary className="cursor-pointer font-medium">Columns</summary>
                <dl className="mt-2 grid gap-x-3 gap-y-1 sm:grid-cols-[10rem_1fr]">
                  {COLUMN_HELP.map(([k, v]) => <div key={k} className="contents"><dt className="font-mono text-xs text-ink">{k}</dt><dd className="text-muted">{v}</dd></div>)}
                </dl>
                <p className="mt-2 text-xs text-muted">“schedule” rows wait for approval unless auto-approval is on (Settings → Organization). Rows that fail the quality checks always wait for a person.</p>
              </details>
            </>
          )}

          {p && (
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium [overflow-wrap:anywhere]">{file?.name}</span>
                <Badge tone="brand">{p.ok} ready</Badge>
                {p.errors > 0 && <Badge tone="danger">{p.errors} with problems</Badge>}
                {p.warnings > 0 && <Badge tone="accent">{p.warnings} with notes</Badge>}
                <span className="text-muted">· Auto-approval is {p.auto_approve ? "on" : "off"}</span>
                <button className="ml-auto text-sm text-primary-700 hover:underline" onClick={() => pick(null)}>Choose another file</button>
              </div>
              {(p.errors > 0 || p.warnings > 0) && (
                <div role="radiogroup" aria-label="Rows to show" className="inline-flex rounded-lg border border-line bg-surface p-0.5 text-sm">
                  {(["all", "problems"] as const).map((k) => (
                    <button key={k} role="radio" aria-checked={only === k} onClick={() => setOnly(k)}
                      className={`rounded-md px-3 py-1 ${only === k ? "bg-primary-50 font-semibold text-primary-800" : "text-muted hover:text-ink"}`}>
                      {k === "all" ? `All rows (${p.total})` : `Problems and notes (${p.errors + p.warnings})`}
                    </button>
                  ))}
                </div>
              )}
              <div className="max-h-[50vh] overflow-auto rounded-xl border border-line">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className="sticky top-0 border-b border-line bg-canvas text-left text-xs text-muted">
                    <tr>
                      <th className="px-2 py-2 font-medium">Row</th>
                      <th className="px-2 py-2 font-medium">Account</th>
                      <th className="px-2 py-2 font-medium">When</th>
                      <th className="px-2 py-2 font-medium">Post</th>
                      <th className="px-2 py-2 font-medium">What happens</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {rows.map((r) => (
                      <tr key={r.row} className={`align-top ${r.errors.length ? "bg-danger-tint/40" : ""}`}>
                        <td className="px-2 py-2 tabular-nums text-muted">{r.row}</td>
                        <td className="px-2 py-2">
                          {r.account ? <><div className="font-medium">{r.account.name}</div><div className="text-xs text-muted">{r.account.platform_label}</div></>
                            : <span className="text-muted [overflow-wrap:anywhere]">{r.account_input || "—"}</span>}
                        </td>
                        <td className="whitespace-nowrap px-2 py-2 tabular-nums text-muted">{r.local_time ?? "No time"}</td>
                        <td className="max-w-[320px] px-2 py-2">
                          <div className="line-clamp-2 [overflow-wrap:anywhere]">{r.text || <span className="text-muted">No text</span>}</div>
                          <div className="mt-0.5 text-xs text-muted">
                            {r.post_type_label}{r.images ? ` · ${r.images} image${r.images > 1 ? "s" : ""}` : ""}
                            {r.max_chars ? ` · ${r.char_count}/${r.max_chars}` : ""}
                            {r.hashtags.length ? ` · ${r.hashtags.map((h) => `#${h}`).join(" ")}` : ""}
                          </div>
                          {r.errors.map((e) => <div key={e} className="mt-1 flex items-start gap-1 text-xs text-danger"><XCircle size={13} className="mt-0.5 shrink-0" /><span className="[overflow-wrap:anywhere]">{e}</span></div>)}
                          {r.warnings.map((w) => <div key={w} className="mt-1 flex items-start gap-1 text-xs text-amber-800"><AlertTriangle size={13} className="mt-0.5 shrink-0" /><span className="[overflow-wrap:anywhere]">{w}</span></div>)}
                        </td>
                        <td className="px-2 py-2 text-xs">
                          {r.errors.length ? <span className="text-danger">Not imported</span> : <><Badge>{STATUS[r.status]}</Badge><div className="mt-1 text-muted">{r.outcome}</div></>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {needsMedia && <Alert tone="warn">Rows with image URLs add files to the media library, which needs the “media.manage” permission.</Alert>}
              {commit.error && <Alert>{(commit.error as Error).message}</Alert>}
              <div className="flex flex-wrap items-center justify-end gap-2">
                {commit.isPending && <Spinner label="Importing (downloading images can take a moment)…" />}
                <Button variant="ghost" onClick={onClose}>Cancel</Button>
                {p.errors > 0 ? (
                  <Button disabled={!p.ok || commit.isPending} onClick={() => file && commit.mutate({ f: file, skip: true })}>
                    Import {p.ok} row{p.ok === 1 ? "" : "s"} without problems
                  </Button>
                ) : (
                  <Button disabled={!p.ok || commit.isPending} onClick={() => file && commit.mutate({ f: file, skip: false })}>
                    Import {p.ok} post{p.ok === 1 ? "" : "s"}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
