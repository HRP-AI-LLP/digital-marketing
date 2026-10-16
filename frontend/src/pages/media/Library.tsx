import { useEffect, useRef, useState, type DragEvent } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Search, Trash2, Upload } from "lucide-react";
import { api } from "../../api/client";
import { upload } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Badge, Button, EmptyState, Field, Input, Modal, Select, Spinner, Textarea, formatDate } from "../../components/ui";
import { AssetPreview, KIND_LABEL, SOURCE_LABEL, formatBytes, type Asset, type AssetPage } from "./shared";

const PAGE = 40;

function useDebounced<T>(value: T, ms = 350) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function UploadZone({ onDone }: { onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState<{ tone: "danger" | "info"; text: string } | null>(null);
  const send = useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f, f.name));
      return upload<{ items: Asset[]; errors: { filename: string; error: string }[] }>("/media/upload", form);
    },
    onSuccess: (r) => {
      const ok = `Uploaded ${r.items.length} file${r.items.length === 1 ? "" : "s"}.`;
      setMsg(r.errors.length
        ? { tone: "danger", text: `${ok} Not uploaded: ${r.errors.map((e) => `${e.filename} (${e.error})`).join("; ")}` }
        : { tone: "info", text: ok });
      onDone();
    },
    onError: (e: Error) => setMsg({ tone: "danger", text: e.message }),
  });
  const pick = (list: FileList | null) => { if (list?.length) { setMsg(null); send.mutate(Array.from(list)); } };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); };

  return (
    <div className="mb-5">
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border-2 border-dashed px-5 py-4 transition-colors ${over ? "border-primary-500 bg-brand-tint" : "border-line bg-surface"}`}
      >
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand-tint text-brand"><Upload size={18} /></span>
          <div>
            <p className="text-sm font-medium">Drop files here to add them to the library</p>
            <p className="text-xs text-muted">PNG, JPG, WebP, GIF, MP4, MOV or PDF. Images up to 20 MB, videos up to 300 MB.</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {send.isPending && <Spinner label="Uploading…" />}
          <Button variant="secondary" onClick={() => input.current?.click()} disabled={send.isPending}>Choose files</Button>
        </div>
        <input ref={input} type="file" multiple hidden accept=".png,.jpg,.jpeg,.webp,.gif,.mp4,.mov,.pdf"
          onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
      </div>
      {msg && <div className="mt-2"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
    </div>
  );
}

export function AssetDetail({ asset, onClose, onChanged }: { asset: Asset; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const [alt, setAlt] = useState(asset.alt_text ?? "");
  const [savedAlt, setSavedAlt] = useState(asset.alt_text ?? "");
  const [confirm, setConfirm] = useState(false);
  const save = useMutation({
    mutationFn: () => api<Asset>(`/media/${asset.id}`, { method: "PATCH", json: { alt_text: alt } }),
    onSuccess: (a) => { setSavedAlt(a.alt_text ?? ""); onChanged(); },
  });
  const del = useMutation({
    mutationFn: () => api(`/media/${asset.id}`, { method: "DELETE" }),
    onSuccess: () => { onChanged(); onClose(); },
  });
  const m = asset.meta ?? {};
  return (
    <Modal title={`${KIND_LABEL[asset.kind] ?? asset.kind} details`} onClose={onClose} wide>
      <div className="grid gap-5 md:grid-cols-[1.2fr_1fr]">
        <div className="flex max-h-[60vh] min-h-[240px] items-center justify-center overflow-hidden rounded-lg bg-slate-100">
          <AssetPreview asset={asset} controls />
        </div>
        <div className="space-y-4 text-sm">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
            <dt className="text-muted">Source</dt><dd>{SOURCE_LABEL(asset.source)}</dd>
            {asset.width && <><dt className="text-muted">Size</dt><dd>{asset.width} × {asset.height} px</dd></>}
            {asset.duration_s && <><dt className="text-muted">Length</dt><dd>{asset.duration_s.toFixed(1)} s</dd></>}
            <dt className="text-muted">File</dt><dd>{formatBytes(asset.size_bytes)} · {asset.mime}</dd>
            <dt className="text-muted">Added</dt><dd>{formatDate(asset.created_at)}</dd>
            {m.pages && <><dt className="text-muted">Pages</dt><dd>{m.pages}</dd></>}
            {m.attribution && <><dt className="text-muted">Credit</dt><dd>{m.page_url
              ? <a href={m.page_url} target="_blank" rel="noreferrer" className="text-brand underline">{m.attribution}</a>
              : m.attribution}</dd></>}
          </dl>
          {asset.prompt && <p className="rounded-lg bg-ink/5 p-2.5 text-xs text-muted"><span className="font-medium text-ink">Prompt: </span>{asset.prompt}</p>}
          {m.voice_note && <Alert tone="warn">{m.voice_note}</Alert>}
          {m.background_note && <Alert tone="warn">{m.background_note}</Alert>}
          <Field label="Alt text" hint="Describes the image for screen readers. Platforms that support it get this text.">
            <Textarea rows={3} value={alt} onChange={(e) => setAlt(e.target.value)} disabled={!can("media.manage")} />
          </Field>
          {save.error && <Alert>{(save.error as Error).message}</Alert>}
          <div className="flex flex-wrap gap-2">
            {can("media.manage") && (
              <Button onClick={() => save.mutate()} disabled={save.isPending || alt.trim() === savedAlt}>
                {save.isSuccess && alt.trim() === savedAlt ? "Saved" : "Save alt text"}
              </Button>
            )}
            <a href={asset.url} target="_blank" rel="noreferrer"><Button variant="secondary" type="button"><ExternalLink size={15} />Open</Button></a>
            <a href={`${asset.url}&download=1`}><Button variant="secondary" type="button"><Download size={15} />Download</Button></a>
            {asset.mp4_url && <a href={asset.mp4_url} target="_blank" rel="noreferrer"><Button variant="ghost" type="button">MP4 version</Button></a>}
          </div>
          {can("media.manage") && (
            <div className="border-t border-line pt-3">
              {del.error && <div className="mb-2"><Alert>{(del.error as Error).message}</Alert></div>}
              {confirm ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm">Delete this file for good?</span>
                  <Button variant="danger" onClick={() => del.mutate()} disabled={del.isPending}>Delete</Button>
                  <Button variant="ghost" onClick={() => setConfirm(false)}>Keep it</Button>
                </div>
              ) : (
                <Button variant="danger" onClick={() => setConfirm(true)}><Trash2 size={15} />Delete</Button>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

export function AssetGrid({ items, onOpen }: { items: Asset[]; onOpen: (a: Asset) => void }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {items.map((a) => (
        <button key={a.id} onClick={() => onOpen(a)}
          className="group overflow-hidden rounded-xl border border-line bg-surface text-left transition-shadow hover:shadow-[0_8px_24px_rgba(6,47,89,0.12)] focus:outline-none focus:ring-2 focus:ring-primary-500/40">
          <div className="aspect-square overflow-hidden bg-slate-100">
            <AssetPreview asset={a} className="transition-transform duration-300 group-hover:scale-[1.03]" />
          </div>
          <div className="flex items-center justify-between gap-2 px-2.5 py-2">
            <span className="truncate text-xs text-muted" title={a.alt_text ?? ""}>{a.alt_text || SOURCE_LABEL(a.source)}</span>
            <Badge tone={a.kind === "image" ? "neutral" : "brand"}>{KIND_LABEL[a.kind] ?? a.kind}</Badge>
          </div>
        </button>
      ))}
    </div>
  );
}

export default function Library() {
  const qc = useQueryClient();
  const [kind, setKind] = useState("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [open, setOpen] = useState<Asset | null>(null);

  const list = useInfiniteQuery({
    queryKey: ["media", kind, source, q],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ page: String(pageParam), page_size: String(PAGE) });
      if (kind) p.set("kind", kind);
      if (source) p.set("source", source);
      if (q.trim()) p.set("search", q.trim());
      return api<AssetPage>(`/media?${p}`);
    },
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
  });
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const total = list.data?.pages[0]?.total ?? 0;
  const refresh = () => qc.invalidateQueries({ queryKey: ["media"] });

  return (
    <div>
      <UploadZone onDone={refresh} />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <Input aria-label="Search the library" placeholder="Search alt text, prompts, file names" value={search}
            onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <Select aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value)} className="!w-40">
          <option value="">All types</option>
          <option value="image">Images</option>
          <option value="gif">GIFs</option>
          <option value="video">Videos</option>
          <option value="document">PDFs</option>
        </Select>
        <Select aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)} className="!w-48">
          <option value="">All sources</option>
          <option value="template">Branded templates</option>
          <option value="ai">AI images</option>
          <option value="stock">Stock media</option>
          <option value="upload">Uploads</option>
        </Select>
        <span className="pb-2 text-sm text-muted">{total} file{total === 1 ? "" : "s"}</span>
      </div>

      {list.isLoading ? <Spinner label="Loading the library…" />
        : list.error ? <Alert>{(list.error as Error).message}</Alert>
        : items.length === 0 ? (
          <EmptyState title={q || kind || source ? "Nothing matches these filters" : "The library is empty"}
            body={q || kind || source ? "Clear the search or pick another type." : "Upload files above, or create branded images, carousels, GIFs and videos in the Create tab."} />
        ) : (
          <>
            <AssetGrid items={items} onOpen={setOpen} />
            {list.hasNextPage && (
              <div className="mt-5 flex justify-center">
                <Button variant="secondary" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
                  {list.isFetchingNextPage ? "Loading…" : "Show more"}
                </Button>
              </div>
            )}
          </>
        )}
      {open && <AssetDetail asset={open} onClose={() => setOpen(null)} onChanged={refresh} />}
    </div>
  );
}
