import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, ExternalLink, LogIn, Search } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Button, EmptyState, Field, Input, Select, Spinner } from "../../components/ui";
import { Markdown } from "../social/Markdown";

type Status = {
  configured: boolean; connected: boolean; status: string | null; status_message: string | null;
  display_name: string | null; redirect_uri: string; guide: string;
};
type Design = {
  id: string; title: string; thumbnail_url: string | null; width: number | null; height: number | null;
  page_count: number; edit_url: string | null; updated_at: number | string | null;
};
type DesignPage = { items: Design[]; continuation: string | null };

const FORMATS: { id: string; label: string }[] = [
  { id: "png", label: "PNG image" }, { id: "jpg", label: "JPG image" }, { id: "mp4", label: "MP4 video" },
  { id: "gif", label: "GIF loop" }, { id: "pdf", label: "PDF document" },
];
const MAX_PAGES = 20;

export function useCanvaStatus() {
  return useQuery({ queryKey: ["canva-status"], queryFn: () => api<Status>("/canva/status"), retry: false });
}

/** Browse / search Canva designs and import the chosen pages into the media library. */
export function CanvaPicker({ returnTo, actionLabel = "Import to library", onImported }: {
  returnTo: string; actionLabel?: string; onImported: (assetIds: string[]) => void;
}) {
  const qc = useQueryClient();
  const status = useCanvaStatus();
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [pages, setPages] = useState<Design[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [picked, setPicked] = useState<Design | null>(null);
  const connected = !!status.data?.connected;

  const list = useQuery({
    queryKey: ["canva-designs", search],
    queryFn: () => api<DesignPage>(`/canva/designs${search ? `?query=${encodeURIComponent(search)}` : ""}`),
    enabled: connected, retry: false,
  });
  useEffect(() => { if (list.data) { setPages(list.data.items); setNext(list.data.continuation); } }, [list.data]);
  const more = useMutation({
    mutationFn: () => api<DesignPage>(`/canva/designs?continuation=${encodeURIComponent(next ?? "")}${search ? `&query=${encodeURIComponent(search)}` : ""}`),
    onSuccess: (d) => { setPages((p) => [...p, ...d.items.filter((x) => !p.some((y) => y.id === x.id))]); setNext(d.continuation); },
  });
  const connect = useMutation({
    mutationFn: () => api<{ authorize_url: string }>("/canva/connect", { method: "POST", json: { return_to: returnTo } }),
    onSuccess: (d) => { window.location.href = d.authorize_url; },
  });
  const disconnect = useMutation({
    mutationFn: () => api("/canva/connection", { method: "DELETE" }),
    onSuccess: () => { setPages([]); setPicked(null); qc.invalidateQueries({ queryKey: ["canva-status"] }); },
  });

  if (status.isLoading) return <Spinner label="Checking Canva…" />;
  if (status.error) return <Alert>{(status.error as Error).message}</Alert>;
  const st = status.data!;

  if (!st.configured) {
    return (
      <div className="space-y-3">
        <Alert tone="info">
          Importing from Canva is optional and free. It needs a Canva integration of your own: create it once, then add its Client ID and
          secret under <Link to="/settings/integrations" className="font-medium underline">Settings → Integrations</Link> (Canva integration).
        </Alert>
        <div>
          <div className="mb-1 text-sm font-medium">Redirect URI to add to the Canva integration</div>
          <code className="block overflow-x-auto rounded-lg border border-line bg-canvas px-2.5 py-1.5 font-mono text-xs [overflow-wrap:anywhere]">{st.redirect_uri}</code>
        </div>
        {st.guide && (
          <details className="rounded-lg border border-line">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-primary-700">Setup guide</summary>
            <div className="max-h-[22rem] overflow-y-auto border-t border-line px-4 py-3"><Markdown text={st.guide} skipFirstHeading /></div>
          </details>
        )}
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="space-y-3">
        {st.status === "expired" && <Alert tone="warn">{st.status_message || "The Canva connection expired. Connect again."}</Alert>}
        <p className="text-sm text-muted">Sign in to Canva once to browse your designs here and bring them into the media library.</p>
        {connect.error && <Alert>{(connect.error as Error).message}</Alert>}
        <Button onClick={() => connect.mutate()} disabled={connect.isPending}><LogIn size={16} />{connect.isPending ? "Opening Canva…" : "Connect Canva"}</Button>
      </div>
    );
  }

  if (picked) {
    return <ImportPanel design={picked} actionLabel={actionLabel} onBack={() => setPicked(null)}
      onDone={(ids) => { qc.invalidateQueries({ queryKey: ["media"] }); onImported(ids); }} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
        <span>Signed in to Canva{st.display_name ? ` as ${st.display_name}` : ""}.</span>
        <button type="button" className="text-xs hover:text-primary-700 hover:underline" onClick={() => { if (confirm("Disconnect Canva?")) disconnect.mutate(); }}>
          Disconnect
        </button>
      </div>
      <form className="flex gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); setSearch(query.trim()); }}>
        <Input aria-label="Search your Canva designs" placeholder="Search your designs" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" variant="secondary"><Search size={15} />Search</Button>
      </form>
      {list.error && <Alert>{(list.error as Error).message}</Alert>}
      {list.isLoading ? <Spinner label="Loading your designs…" /> : pages.length === 0 && !list.error ? (
        <EmptyState title={search ? "No designs match" : "No designs yet"} body={search ? "Try another word." : "Designs you create in Canva show up here."} />
      ) : (
        <div className="grid max-h-[380px] grid-cols-2 gap-2.5 overflow-y-auto pr-1 sm:grid-cols-4">
          {pages.map((d) => (
            <button key={d.id} type="button" onClick={() => setPicked(d)}
              className="overflow-hidden rounded-lg border border-line text-left hover:border-primary-500 focus-visible:ring-2 focus-visible:ring-primary-500/40">
              <div className="aspect-square bg-slate-100">
                {d.thumbnail_url && <img src={d.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" />}
              </div>
              <div className="px-2 py-1.5">
                <div className="truncate text-sm font-medium" title={d.title}>{d.title}</div>
                <div className="text-xs text-muted">{d.page_count} {d.page_count === 1 ? "page" : "pages"}</div>
              </div>
            </button>
          ))}
        </div>
      )}
      {next && <Button variant="ghost" onClick={() => more.mutate()} disabled={more.isPending}>{more.isPending ? "Loading…" : "Load more designs"}</Button>}
    </div>
  );
}

function ImportPanel({ design, actionLabel, onBack, onDone }: {
  design: Design; actionLabel: string; onBack: () => void; onDone: (ids: string[]) => void;
}) {
  const total = Math.max(1, design.page_count || 1);
  const [format, setFormat] = useState("png");
  const [sel, setSel] = useState<number[]>(() => Array.from({ length: Math.min(total, MAX_PAGES) }, (_, i) => i + 1));
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const finish = (j: Job) => {
    setJobId(null);
    if (j.status === "failed") { setError((j.error || "The import didn't work. Try again.").replace(/^\w*Error: /, "")); return; }
    setDone(true);
    onDone(((j.result ?? {}) as { asset_ids?: string[] }).asset_ids ?? []);
  };
  const job = useJob(jobId, finish);
  const multiFile = format === "png" || format === "jpg";
  const start = () => {
    setError(null);
    api<{ job_id: string }>("/canva/import", { method: "POST", json: { design_id: design.id, format, pages: total > 1 ? sel : null } })
      .then((r) => setJobId(r.job_id)).catch((e) => setError((e as Error).message));
  };
  const toggle = (n: number) => setSel((s) => (s.includes(n) ? s.filter((x) => x !== n) : [...s, n].sort((a, b) => a - b).slice(0, MAX_PAGES)));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <div className="h-28 w-28 shrink-0 overflow-hidden rounded-lg border border-line bg-slate-100">
          {design.thumbnail_url && <img src={design.thumbnail_url} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold [overflow-wrap:anywhere]">{design.title}</div>
          <div className="text-sm text-muted">{total} {total === 1 ? "page" : "pages"}</div>
          {design.edit_url && (
            <a href={design.edit_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-sm text-primary-700 hover:underline">
              Open in Canva <ExternalLink size={13} />
            </a>
          )}
        </div>
      </div>
      <Field label="Format" hint={multiFile ? "Each page becomes its own image, ready for a carousel." : "The chosen pages are combined into one file."}>
        <Select value={format} onChange={(e) => setFormat(e.target.value)}>
          {FORMATS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </Select>
      </Field>
      {total > 1 && (
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Pages {total > MAX_PAGES && <span className="font-normal text-muted">(up to {MAX_PAGES} at a time)</span>}</legend>
          <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
            {Array.from({ length: Math.min(total, 100) }, (_, i) => i + 1).map((n) => (
              <button key={n} type="button" onClick={() => toggle(n)} aria-pressed={sel.includes(n)}
                className={`h-8 min-w-8 rounded-md border px-2 text-sm ${sel.includes(n) ? "border-primary-500 bg-primary-50 font-medium text-primary-700" : "border-line hover:border-ink/30"}`}>
                {n}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      {error && <Alert>{error}</Alert>}
      {jobId && <Alert tone="info"><Spinner label={job?.progress || "Exporting from Canva…"} /></Alert>}
      {done && <Alert tone="info">Imported into the media library.</Alert>}
      <div className="flex flex-wrap justify-between gap-2">
        <Button variant="ghost" onClick={onBack} disabled={!!jobId}>Back to designs</Button>
        <Button onClick={start} disabled={!!jobId || (total > 1 && sel.length === 0)}>
          {done ? <><Check size={16} />Import again</> : <><Download size={16} />{jobId ? "Importing…" : actionLabel}</>}
        </Button>
      </div>
    </div>
  );
}

/** Media studio tab. */
export default function CanvaTab() {
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <p className="max-w-3xl text-sm text-muted">Bring designs you made in Canva into the media library, then attach them to any post.</p>
      {note && <Alert tone="info">{note}</Alert>}
      <CanvaPicker returnTo="/media" onImported={(ids) => setNote(`${ids.length} ${ids.length === 1 ? "file was" : "files were"} added to the library.`)} />
    </div>
  );
}
