import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FileText, RefreshCw, ShieldAlert, Wand2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, Card, Spinner, Tabs, Textarea, formatDate } from "../../components/ui";
import type { Llms, SiteCheck } from "./types";

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function SiteStatus({ c, canRun, onRun, running }: { c: SiteCheck | null; canRun: boolean; onRun: () => void; running: boolean }) {
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold">Your live website</h3>
          <p className="text-xs text-muted">{c?.checked_at ? `Checked ${formatDate(c.checked_at)}` : "Not checked yet"} · free, no key needed</p>
        </div>
        {canRun && <Button variant="secondary" onClick={onRun} disabled={running}><RefreshCw size={15} /> {running ? "Checking…" : "Check now"}</Button>}
      </div>
      {c?.error && <Alert tone="warn">{c.error}</Alert>}
      {c?.llms_txt && (
        <ul className="space-y-1.5 text-sm">
          <li className="flex items-center gap-2">
            {c.llms_txt.found ? <CheckCircle2 size={16} className="text-emerald-700" /> : <ShieldAlert size={16} className="text-amber-600" />}
            {c.llms_txt.found ? "Your site serves /llms.txt" : c.llms_txt.served_but_invalid ? "/llms.txt exists but doesn't look like an llms.txt file" : "No /llms.txt on your site yet"}
          </li>
          <li className="flex items-center gap-2">
            {c.llms_full_txt?.found ? <CheckCircle2 size={16} className="text-emerald-700" /> : <span className="inline-block h-4 w-4 rounded-full border-2 border-slate-300" />}
            {c.llms_full_txt?.found ? "Your site serves /llms-full.txt" : "No /llms-full.txt (optional)"}
          </li>
          <li className="flex items-center gap-2 text-muted">{c.robots?.found ? "robots.txt found" : "No robots.txt: every crawler is allowed"}</li>
        </ul>
      )}
      {c?.bots && (
        <div className="mt-3">
          <div className="mb-1.5 text-xs text-muted">AI crawlers in robots.txt</div>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {c.bots.map((b) => (
              <li key={b.bot} className="flex items-center justify-between gap-2 rounded-lg border border-line px-3 py-1.5 text-sm">
                <span className="min-w-0"><span className="font-medium">{b.bot}</span> <span className="text-xs text-muted">{b.who}</span></span>
                <Badge tone={b.status === "blocked" ? "danger" : "brand"}>{b.status === "blocked" ? "Blocked" : "Allowed"}</Badge>
              </li>
            ))}
          </ul>
          {c.bots.some((b) => b.status === "blocked") && (
            <p className="mt-2 text-xs text-muted">Blocked crawlers can't read your pages, so those AI tools are less likely to describe or cite you. If that isn't on purpose, ask your web developer to allow them in robots.txt.</p>
          )}
        </div>
      )}
    </Card>
  );
}

export default function LlmsTab() {
  const { can } = useAuth();
  const canEdit = can("aivis.manage");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["aivis", "llms"], queryFn: () => api<Llms>("/aivis/llms") });
  const [which, setWhich] = useState<"llms" | "full">("llms");
  const [text, setText] = useState("");
  const [full, setFull] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (q.data) { setText(q.data.text); setFull(q.data.full_text); } }, [q.data]);

  const gen = useMutation({ mutationFn: () => api<{ job_id: string }>("/aivis/llms/generate", { method: "POST" }), onSuccess: (r) => setJobId(r.job_id) });
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: ["aivis", "llms"] }));
  const genBusy = !!jobId && job?.status !== "done" && job?.status !== "failed";
  const save = useMutation({
    mutationFn: () => api<Llms>("/aivis/llms", { method: "PUT", json: { text, full_text: full } }),
    onSuccess: (r) => { qc.setQueryData(["aivis", "llms"], r); setSaved(true); setTimeout(() => setSaved(false), 2500); },
  });
  const check = useMutation({ mutationFn: () => api<SiteCheck>("/aivis/site-check", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["aivis", "llms"] }) });

  if (q.isLoading) return <Spinner label="Loading…" />;
  if (q.error) return <Alert>{(q.error as Error).message}</Alert>;
  const d = q.data!;
  const dirty = text !== d.text || full !== d.full_text;
  const current = which === "llms" ? text : full;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
      <div className="min-w-0 space-y-4">
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-xl">
              <h3 className="flex items-center gap-2 font-semibold"><FileText size={17} /> llms.txt</h3>
              <p className="mt-1 text-sm text-muted">A short guide to your website for AI assistants: your name, a one-line summary and links to your key pages. Built from your company profile and the {d.crawled_pages} pages read on Company intelligence.</p>
            </div>
            {canEdit && <Button onClick={() => gen.mutate()} disabled={genBusy}><Wand2 size={15} /> {d.text ? "Generate again" : "Generate"}</Button>}
          </div>
          {d.crawled_pages === 0 && <div className="mt-3"><Alert tone="info">No website pages have been read yet. <Link className="underline" to="/intelligence">Analyze your website</Link> first for a complete file; until then it only has your company summary.</Alert></div>}
          {genBusy && <div className="mt-3"><Spinner label={job?.progress || "Writing llms.txt…"} /></div>}
          {job?.status === "failed" && <div className="mt-3"><Alert>{job.error}</Alert></div>}
          {d.generated_at && <p className="mt-2 text-xs text-muted">Generated {formatDate(d.generated_at)}{d.edited_at ? ` · edited ${formatDate(d.edited_at)}` : ""}</p>}
        </Card>

        {(d.text || d.full_text) && (
          <div>
            <Tabs value={which} onChange={setWhich} items={[{ id: "llms", label: "llms.txt" }, { id: "full", label: "llms-full.txt (optional)" }]} />
            <Textarea aria-label={which === "llms" ? "llms.txt" : "llms-full.txt"} value={current} readOnly={!canEdit} spellCheck={false}
              onChange={(e) => (which === "llms" ? setText(e.target.value) : setFull(e.target.value))}
              className="min-h-[420px] font-mono text-[13px] leading-relaxed" />
            {which === "llms" && d.issues.length > 0 && !dirty && (
              <div className="mt-2"><Alert tone="warn"><ul className="list-disc pl-4">{d.issues.map((i) => <li key={i}>{i}</li>)}</ul></Alert></div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {canEdit && <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>Save changes</Button>}
              <Button variant="secondary" onClick={() => download(which === "llms" ? "llms.txt" : "llms-full.txt", current)} disabled={!current}><Download size={15} /> Download {which === "llms" ? "llms.txt" : "llms-full.txt"}</Button>
              {saved && <span className="text-sm text-emerald-700">Saved</span>}
              {save.error && <Alert>{(save.error as Error).message}</Alert>}
            </div>
          </div>
        )}
      </div>

      <div className="space-y-4">
        <SiteStatus c={d.site_check} canRun={canEdit} onRun={() => check.mutate()} running={check.isPending} />
        {check.error && <Alert>{(check.error as Error).message}</Alert>}
        <Card>
          <h3 className="mb-2 font-semibold">How to publish it</h3>
          <ol className="list-decimal space-y-1.5 pl-5 text-sm">
            <li>Download <strong>llms.txt</strong> (and llms-full.txt if you want).</li>
            <li>Upload it to the top folder of your website, so it opens at <code className="rounded bg-ink/5 px-1 [overflow-wrap:anywhere]">{d.site_root ?? "https://your-site"}/llms.txt</code>. On WordPress use a file manager or an llms.txt plugin; on other builders ask your web developer.</li>
            <li>Come back and press “Check now” to confirm it's live.</li>
            <li>Generate it again after big website changes.</li>
          </ol>
        </Card>
      </div>
    </div>
  );
}
