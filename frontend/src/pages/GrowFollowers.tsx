// Grow followers: share links, QR codes, barcodes and "Follow us" images for every social profile, one at a time
// or all together (one public page with every channel, or a zip of everything).
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Barcode, Check, ChevronDown, Copy, Download, ExternalLink, Eye, Facebook, Image as ImageIcon, Linkedin, Mail, MessageCircle,
  Package, Palette, Pencil, QrCode, RotateCcw, Send, Share2, Twitter, Users, X as XIcon,
} from "lucide-react";
import { api, apiBlob, downloadFile } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Modal, PageHeader } from "../components/ui";
import { PlatformBadge } from "./analytics/vizkit";

type Item = {
  key: string; platform: string; platform_label: string; name: string; handle: string | null; url: string | null; auto_url: string | null;
  custom_url: boolean; hint: string | null; source: "account" | "profile"; include: boolean; code: string | null; short_url: string | null; clicks: number;
};
type Kit = {
  settings: { title: string; message: string; slug: string; theme: "dark" | "light" };
  items: Item[];
  all: { key: "all"; page_url: string; short_url: string; code: string; clicks: number; count: number };
  public_ready: boolean;
};
type Design = { fg: string; bg: string; style: "square" | "dots"; logo: boolean; shape: "square" | "portrait" | "story" | "landscape"; theme: "dark" | "light" };
type Target = { key: string; short_url: string | null; label: string; text: string; platform_label: string; handle: string | null; name: string; stem: string };
type ItemT = Item & Target;
const stemOf = (i: { platform_label: string; handle: string | null; name: string }) =>
  `${i.platform_label}-${i.handle || i.name}`.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "profile";

const DEFAULT_DESIGN: Design = { fg: "#0b1d33", bg: "#ffffff", style: "square", logo: true, shape: "square", theme: "dark" };
const SHAPES: { id: Design["shape"]; label: string; hint: string }[] = [
  { id: "square", label: "Square", hint: "1080 × 1080 · feeds" }, { id: "portrait", label: "Portrait", hint: "1080 × 1350 · Instagram, LinkedIn" },
  { id: "story", label: "Story", hint: "1080 × 1920 · Stories, Status" }, { id: "landscape", label: "Landscape", hint: "1200 × 630 · X, Facebook, email" },
];
const VERB: Record<string, string> = { youtube: "Subscribe on", telegram: "Join us on", discord: "Join us on", whatsapp: "Follow our channel on", linkedin_person: "Connect on" };

function useDesign() {
  const [d, setD] = useState<Design>(() => {
    try { return { ...DEFAULT_DESIGN, ...JSON.parse(localStorage.getItem("dm.followDesign") || "{}") }; } catch { return DEFAULT_DESIGN; }
  });
  useEffect(() => { try { localStorage.setItem("dm.followDesign", JSON.stringify(d)); } catch { /* storage unavailable */ } }, [d]);
  return [d, (p: Partial<Design>) => setD((x) => ({ ...x, ...p }))] as const;
}

const qs = (o: Record<string, string | number | boolean>) => new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)])).toString();
const enc = encodeURIComponent;
const qrPath = (key: string, d: Design, fmt: "png" | "svg", extra: Record<string, string | number | boolean> = {}) =>
  `/follow/${enc(key)}/qr.${fmt}?${qs({ fg: d.fg, bg: d.bg, style: d.style, logo: d.logo, ...extra })}`;
const barPath = (key: string, fmt: "png" | "svg", extra: Record<string, string | number | boolean> = {}) => `/follow/${enc(key)}/barcode.${fmt}?${qs(extra)}`;
const cardPath = (key: string, d: Design, extra: Record<string, string | number | boolean> = {}) => `/follow/${enc(key)}/card.png?${qs({ shape: d.shape, theme: d.theme, ...extra })}`;

/** An authenticated image as an object URL (revoked when it changes). */
function useBlobUrl(path: string | null) {
  const [st, setSt] = useState<{ url: string | null; loading: boolean; error: string | null }>({ url: null, loading: false, error: null });
  useEffect(() => {
    if (!path) { setSt({ url: null, loading: false, error: null }); return; }
    let url: string | null = null; let live = true;
    setSt((s) => ({ ...s, loading: true, error: null }));
    apiBlob(path).then((b) => { if (!live) return; url = URL.createObjectURL(b); setSt({ url, loading: false, error: null }); })
      .catch((e) => live && setSt({ url: null, loading: false, error: (e as Error).message }));
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [path]);
  return st;
}

function Img({ path, alt, className = "" }: { path: string | null; alt: string; className?: string }) {
  const b = useBlobUrl(path);
  if (b.error) return <div className={`grid place-items-center rounded-lg bg-red-50 p-3 text-center text-xs text-danger ${className}`}>{b.error}</div>;
  if (!b.url) return <div className={`animate-pulse rounded-lg bg-slate-100 ${className}`} />;
  return <img src={b.url} alt={alt} className={className} />;
}

function useCopy() {
  const [done, setDone] = useState<string | null>(null);
  const copy = (text: string, id: string) => { void navigator.clipboard?.writeText(text); setDone(id); setTimeout(() => setDone((x) => (x === id ? null : x)), 1500); };
  return { copy, done };
}

/** Small dropdown menu. */
function Menu({ label, icon, children, align = "left", primary = false }: { label: string; icon: ReactNode; children: (close: () => void) => ReactNode; align?: "left" | "right"; primary?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu"
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${primary ? "border-primary-600 bg-primary-600 text-white hover:bg-primary-700" : "border-line bg-surface text-ink hover:border-primary-300"}`}>
        {icon}{label}<ChevronDown size={12} className="opacity-70" />
      </button>
      {open && (
        <div role="menu" className={`absolute z-40 mt-1 w-60 overflow-hidden rounded-xl border border-line bg-surface py-1 text-sm shadow-xl ${align === "right" ? "right-0" : "left-0"}`}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
function MenuItem({ icon, children, onClick, href, hint }: { icon: ReactNode; children: ReactNode; onClick?: () => void; href?: string; hint?: string }) {
  const cls = "flex w-full items-center gap-2.5 px-3 py-2 text-left text-ink hover:bg-slate-50";
  const inner = <><span className="grid w-4 place-items-center text-muted">{icon}</span><span className="flex-1">{children}{hint && <span className="block text-[11px] text-muted">{hint}</span>}</span></>;
  return href ? <a role="menuitem" href={href} target="_blank" rel="noreferrer" className={cls} onClick={onClick}>{inner}</a>
    : <button role="menuitem" type="button" className={cls} onClick={onClick}>{inner}</button>;
}
const MenuSep = ({ label }: { label: string }) => <div className="border-t border-line px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted first:border-0">{label}</div>;

function ShareMenu({ t, d, primary }: { t: Target; d: Design; primary?: boolean }) {
  const { copy, done } = useCopy();
  const url = t.short_url ?? "";
  const msg = `${t.text}: ${url}`;
  const native = async () => {
    try {
      const blob = await apiBlob(cardPath(t.key, d));
      const file = new File([blob], `follow-${t.stem}.png`, { type: "image/png" });
      const data: ShareData = { title: t.label, text: t.text, url };
      if (navigator.canShare?.({ ...data, files: [file] })) await navigator.share({ ...data, files: [file] });
      else await navigator.share(data);
    } catch { /* cancelled */ }
  };
  return (
    <Menu label="Share" icon={<Share2 size={13} />} primary={primary}>
      {(close) => <>
        <MenuSep label="Copy" />
        <MenuItem icon={done === "l" ? <Check size={14} /> : <Copy size={14} />} onClick={() => copy(url, "l")}>{done === "l" ? "Copied" : "Copy link"}</MenuItem>
        <MenuItem icon={done === "m" ? <Check size={14} /> : <Copy size={14} />} onClick={() => copy(msg, "m")} hint={msg.length > 46 ? `${msg.slice(0, 46)}…` : msg}>{done === "m" ? "Copied" : "Copy message with link"}</MenuItem>
        <MenuSep label="Send to" />
        <MenuItem icon={<MessageCircle size={14} />} href={`https://wa.me/?text=${enc(msg)}`} onClick={close}>WhatsApp</MenuItem>
        <MenuItem icon={<Linkedin size={14} />} href={`https://www.linkedin.com/sharing/share-offsite/?url=${enc(url)}`} onClick={close}>LinkedIn</MenuItem>
        <MenuItem icon={<Twitter size={14} />} href={`https://x.com/intent/post?text=${enc(t.text)}&url=${enc(url)}`} onClick={close}>X (Twitter)</MenuItem>
        <MenuItem icon={<Facebook size={14} />} href={`https://www.facebook.com/sharer/sharer.php?u=${enc(url)}`} onClick={close}>Facebook</MenuItem>
        <MenuItem icon={<Send size={14} />} href={`https://t.me/share/url?url=${enc(url)}&text=${enc(t.text)}`} onClick={close}>Telegram</MenuItem>
        <MenuItem icon={<Mail size={14} />} href={`mailto:?subject=${enc(t.label)}&body=${enc(msg)}`} onClick={close}>Email</MenuItem>
        {typeof navigator !== "undefined" && "share" in navigator && <MenuItem icon={<Share2 size={14} />} onClick={() => { close(); void native(); }} hint="With the image, on phones">More apps…</MenuItem>}
      </>}
    </Menu>
  );
}

function DownloadMenu({ t, d, onPreview }: { t: Target; d: Design; onPreview: () => void }) {
  const stem = t.stem;
  const dl = (path: string, name: string) => { void downloadFile(path, name); };
  return (
    <Menu label="Download" icon={<Download size={13} />}>
      {(close) => <>
        <MenuItem icon={<Eye size={14} />} onClick={() => { close(); onPreview(); }}>Preview everything…</MenuItem>
        <MenuSep label="QR code" />
        <MenuItem icon={<QrCode size={14} />} onClick={() => { close(); dl(qrPath(t.key, d, "png", { size: 2048, download: true }), `qr-${stem}.png`); }} hint="2048 px, for screens and print">PNG</MenuItem>
        <MenuItem icon={<QrCode size={14} />} onClick={() => { close(); dl(qrPath(t.key, d, "svg", { download: true }), `qr-${stem}.svg`); }} hint="Vector, any size, for designers">SVG</MenuItem>
        <MenuSep label="Barcode (Code 128)" />
        <MenuItem icon={<Barcode size={14} />} onClick={() => { close(); dl(barPath(t.key, "png", { download: true }), `barcode-${stem}.png`); }}>PNG</MenuItem>
        <MenuItem icon={<Barcode size={14} />} onClick={() => { close(); dl(barPath(t.key, "svg", { download: true }), `barcode-${stem}.svg`); }}>SVG</MenuItem>
        <MenuSep label={`"Follow us" image · ${SHAPES.find((s) => s.id === d.shape)?.label}`} />
        <MenuItem icon={<ImageIcon size={14} />} onClick={() => { close(); dl(cardPath(t.key, d, { download: true }), `follow-${stem}-${d.shape}.png`); }} hint={SHAPES.find((s) => s.id === d.shape)?.hint}>PNG image</MenuItem>
      </>}
    </Menu>
  );
}

// ------------------------------------------------------------------ page
export default function GrowFollowers() {
  const { session } = useAuth();
  const can = (p: string) => !!session?.permissions.includes(p);
  const qc = useQueryClient();
  const kit = useQuery({ queryKey: ["follow-kit"], queryFn: () => api<Kit>("/follow") });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Kit>("/follow", { method: "PUT", json: body }),
    onSuccess: (k) => qc.setQueryData(["follow-kit"], k),
  });
  const [d, setD] = useDesign();
  const [preview, setPreview] = useState<Target | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [packing, setPacking] = useState(false);
  const [editPage, setEditPage] = useState(false);
  const k = kit.data;
  const manage = can("links.manage");

  const targets = useMemo(() => (k?.items ?? []).map<ItemT>((i) => ({ ...i, label: `${i.platform_label}: ${i.handle || i.name}`, text: `${VERB[i.platform] ?? "Follow us on"} ${i.platform_label}`, stem: stemOf(i) })), [k]);
  const allTarget: Target | null = k ? { key: "all", short_url: k.all.short_url, label: k.settings.title, text: `${k.settings.title}: all our channels in one place`,
    platform_label: "All channels", handle: null, name: k.settings.title, stem: "all-channels" } : null;
  const ready = targets.filter((t) => t.short_url);
  const toggleSel = (key: string) => setSelected((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const pack = async (keys: string[], images: boolean) => {
    setPacking(true);
    try { await downloadFile(`/follow/pack.zip?${qs({ keys: keys.join(","), images, shape: d.shape, theme: d.theme, fg: d.fg, bg: d.bg, style: d.style, logo: d.logo })}`, "follow-kit.zip"); }
    finally { setPacking(false); }
  };

  return (
    <div>
      <PageHeader title="Grow followers" description="Share a link, QR code, barcode or ready-made image that takes people straight to your profiles to follow you. One profile at a time, or every channel at once." />
      {kit.error && <Alert>{(kit.error as Error).message}</Alert>}
      {save.error && <Alert>{(save.error as Error).message}</Alert>}
      {k && !k.public_ready && (
        <Alert tone="warn">Links and QR codes need your public HTTPS address (Cloudflare Tunnel) so people outside your computer can open them. Set <code>PUBLIC_BASE_URL</code> as described in Settings → Integrations, then restart.</Alert>
      )}
      {!k && kit.isLoading && <div className="h-64 animate-pulse rounded-2xl bg-slate-100" />}

      {k && allTarget && (
        <>
          {/* everything in one go */}
          <section className="mb-5 rounded-2xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
            <div className="grid gap-5 p-5 lg:grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-primary-700"><Users size={14} />All channels in one go</div>
                <h2 className="mt-1 text-xl font-bold text-ink">{k.settings.title}</h2>
                <p className="mt-1 text-sm text-muted">{k.settings.message}</p>
                <p className="mt-2 text-sm text-ink/80">One link opens a page with a follow button for each of the <b>{k.all.count}</b> channel{k.all.count === 1 ? "" : "s"} switched on below.</p>
                <div className="mt-3 space-y-2">
                  <LinkRow label="Share link" value={k.all.short_url} sub={`${k.all.clicks} open${k.all.clicks === 1 ? "" : "s"} · counted`} />
                  <LinkRow label="Page" value={k.all.page_url} open />
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <ShareMenu t={allTarget} d={d} primary />
                  <DownloadMenu t={allTarget} d={d} onPreview={() => setPreview(allTarget)} />
                  <Button variant="secondary" onClick={() => void pack([], true)} disabled={packing}>
                    <span className="inline-flex items-center gap-1.5 text-xs"><Package size={13} />{packing ? "Preparing zip…" : "Download whole kit (.zip)"}</span>
                  </Button>
                  {manage && <button type="button" onClick={() => setEditPage(true)} className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline"><Pencil size={12} />Edit page</button>}
                </div>
              </div>
              <button type="button" onClick={() => setPreview(allTarget)} className="group relative mx-auto block w-44 shrink-0 rounded-2xl border border-line bg-white p-2 shadow-sm hover:border-primary-300" title="Preview and download">
                <Img path={qrPath("all", d, "png", { size: 512 })} alt="QR code for all channels" className="aspect-square w-full" />
                <span className="mt-1 block text-center text-[11px] text-muted group-hover:text-primary-700">Scan to see every channel</span>
              </button>
            </div>
            <DesignBar d={d} setD={setD} />
          </section>

          {/* bulk bar */}
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <label className="inline-flex items-center gap-2 text-muted">
              <input type="checkbox" className="h-4 w-4 accent-primary-600" checked={ready.length > 0 && ready.every((t) => selected.has(t.key))}
                onChange={(e) => setSelected(e.target.checked ? new Set(ready.map((t) => t.key)) : new Set())} />
              Select all profiles
            </label>
            {selected.size > 0 && <>
              <span className="text-muted">{selected.size} selected</span>
              <Button variant="secondary" disabled={packing} onClick={() => void pack([...selected], true)}><span className="inline-flex items-center gap-1.5 text-xs"><Package size={13} />{packing ? "Preparing…" : "Download selected (.zip)"}</span></Button>
              <Button variant="secondary" disabled={packing} onClick={() => void pack([...selected], false)}><span className="text-xs">Codes only (faster)</span></Button>
              <button type="button" className="text-xs text-muted hover:text-ink" onClick={() => setSelected(new Set())}>Clear</button>
            </>}
            <span className="ml-auto text-xs text-muted">Every code points to a permanent short link: change a profile address later and printed codes keep working.</span>
          </div>

          {/* one card per profile */}
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {targets.map((t) => (
              <ProfileCard key={t.key} t={t} d={d} manage={manage} selected={selected.has(t.key)} onSelect={() => toggleSel(t.key)}
                onPreview={() => setPreview(t)} saving={save.isPending}
                onSave={(patch) => save.mutate({ items: [{ key: t.key, ...patch }] })} />
            ))}
          </div>
          {targets.length === 0 && <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">Connect a social account (Settings → Social accounts) or add profile links in Organization settings.</div>}
        </>
      )}

      {preview && <PreviewModal t={preview} d={d} setD={setD} onClose={() => setPreview(null)} />}
      {editPage && k && <EditPage k={k} saving={save.isPending} error={save.error ? (save.error as Error).message : null}
        onClose={() => setEditPage(false)} onSave={(b) => save.mutate(b, { onSuccess: () => setEditPage(false) })} />}
    </div>
  );
}

function LinkRow({ label, value, sub, open }: { label: string; value: string; sub?: string; open?: boolean }) {
  const { copy, done } = useCopy();
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-20 shrink-0 text-xs text-muted">{label}</span>
      <code className="min-w-0 max-w-full truncate rounded-md bg-slate-50 px-2 py-1 text-[13px] text-ink ring-1 ring-line">{value}</code>
      <button type="button" onClick={() => copy(value, "x")} className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted hover:bg-slate-100 hover:text-ink" title="Copy">
        {done === "x" ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}{done === "x" ? "Copied" : "Copy"}
      </button>
      {open && <a href={value} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted hover:bg-slate-100 hover:text-ink"><ExternalLink size={13} />Open</a>}
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </div>
  );
}

function DesignBar({ d, setD }: { d: Design; setD: (p: Partial<Design>) => void }) {
  const chip = (on: boolean) => `rounded-md px-2 py-1 text-xs ${on ? "bg-primary-600 font-semibold text-white" : "text-muted hover:bg-slate-100 hover:text-ink"}`;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-b-2xl border-t border-line bg-slate-50/70 px-5 py-3 text-xs">
      <span className="inline-flex items-center gap-1.5 font-semibold text-ink"><Palette size={14} />Design</span>
      <label className="inline-flex items-center gap-1.5 text-muted">QR colour <input type="color" value={d.fg} onChange={(e) => setD({ fg: e.target.value })} className="h-6 w-8 cursor-pointer rounded border border-line" /></label>
      <label className="inline-flex items-center gap-1.5 text-muted">Background <input type="color" value={d.bg} onChange={(e) => setD({ bg: e.target.value })} className="h-6 w-8 cursor-pointer rounded border border-line" /></label>
      <span className="inline-flex items-center gap-1 text-muted">Style
        {(["square", "dots"] as const).map((s) => <button key={s} type="button" className={chip(d.style === s)} onClick={() => setD({ style: s })}>{s === "square" ? "Classic" : "Dots"}</button>)}
      </span>
      <label className="inline-flex items-center gap-1.5 text-muted"><input type="checkbox" checked={d.logo} onChange={(e) => setD({ logo: e.target.checked })} className="h-3.5 w-3.5 accent-primary-600" />Logo in the middle</label>
      <span className="inline-flex flex-wrap items-center gap-1 text-muted">Image
        {SHAPES.map((s) => <button key={s.id} type="button" title={s.hint} className={chip(d.shape === s.id)} onClick={() => setD({ shape: s.id })}>{s.label}</button>)}
        <span className="mx-1 h-4 w-px bg-line" />
        {(["dark", "light"] as const).map((s) => <button key={s} type="button" className={chip(d.theme === s)} onClick={() => setD({ theme: s })}>{s === "dark" ? "Dark" : "Light"}</button>)}
      </span>
      {(d.fg !== DEFAULT_DESIGN.fg || d.bg !== DEFAULT_DESIGN.bg || d.style !== "square" || !d.logo) &&
        <button type="button" onClick={() => setD({ fg: DEFAULT_DESIGN.fg, bg: DEFAULT_DESIGN.bg, style: "square", logo: true })} className="inline-flex items-center gap-1 text-muted hover:text-ink"><RotateCcw size={12} />Reset</button>}
    </div>
  );
}

function ProfileCard({ t, d, manage, selected, onSelect, onPreview, onSave, saving }: {
  t: ItemT; d: Design; manage: boolean; selected: boolean; onSelect: () => void; onPreview: () => void;
  onSave: (patch: { url?: string | null; include?: boolean }) => void; saving: boolean;
}) {
  const [editing, setEditing] = useState(!t.url);
  const [val, setVal] = useState(t.custom_url ? t.url ?? "" : t.url ?? "");
  useEffect(() => { setVal(t.url ?? ""); setEditing(!t.url); }, [t.url]);
  const commit = () => { const v = val.trim(); if (v !== (t.url ?? "")) onSave({ url: v || null }); else if (t.url) setEditing(false); };
  return (
    <article className={`flex flex-col rounded-2xl border bg-surface p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors ${selected ? "border-primary-400 ring-2 ring-primary-500/15" : "border-line"}`}>
      <header className="flex items-start gap-3">
        {t.short_url && <input type="checkbox" checked={selected} onChange={onSelect} aria-label={`Select ${t.label}`} className="mt-1 h-4 w-4 accent-primary-600" />}
        <PlatformBadge platform={t.platform} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-semibold text-ink">{t.platform_label}</h3>
            {t.source === "profile" && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-muted" title="From the profile links in Organization settings">Profile link</span>}
          </div>
          <div className="truncate text-xs text-muted">{t.name}{t.handle && t.handle !== t.name ? ` · ${t.handle}` : ""}</div>
        </div>
        {manage && t.url && (
          <label className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] text-muted" title="Show on the all-channels page and image">
            <input type="checkbox" checked={t.include} disabled={saving} onChange={(e) => onSave({ include: e.target.checked })} className="h-3.5 w-3.5 accent-primary-600" />On page
          </label>
        )}
      </header>

      <div className="mt-3 flex gap-3">
        <button type="button" onClick={onPreview} disabled={!t.short_url} className="shrink-0 rounded-xl border border-line bg-white p-1.5 hover:border-primary-300 disabled:cursor-default disabled:opacity-60" title="Preview and download">
          {t.short_url ? <Img path={qrPath(t.key, d, "png", { size: 320 })} alt={`QR code for ${t.label}`} className="h-24 w-24" />
            : <div className="grid h-24 w-24 place-items-center text-center text-[10px] text-muted"><QrCode size={22} className="mb-1 opacity-40" /></div>}
        </button>
        <div className="min-w-0 flex-1 space-y-1.5 text-xs">
          {editing && manage ? (
            <div>
              <label className="text-muted">Profile address people should open</label>
              <div className="mt-1 flex gap-1">
                <input value={val} onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape" && t.url) { setVal(t.url); setEditing(false); } }}
                  placeholder="https://…" className="h-8 min-w-0 flex-1 rounded-md border border-line bg-surface px-2 text-xs outline-none focus:border-primary-400" />
                <button type="button" onClick={commit} disabled={saving} className="rounded-md bg-primary-600 px-2 text-xs font-medium text-white disabled:opacity-50">Save</button>
              </div>
              {t.hint && <p className="mt-1 text-[11px] text-amber-700">{t.hint}</p>}
              {t.custom_url && t.auto_url && <button type="button" onClick={() => onSave({ url: null })} className="mt-1 text-[11px] text-muted hover:text-ink">Use the connected account's address</button>}
            </div>
          ) : t.url ? (
            <div className="flex items-center gap-1">
              <a href={t.url} target="_blank" rel="noreferrer" className="truncate text-primary-700 hover:underline" title={t.url}>{t.url.replace(/^https?:\/\/(www\.)?/, "")}</a>
              {manage && <button type="button" onClick={() => setEditing(true)} className="shrink-0 rounded p-0.5 text-muted hover:text-ink" title="Change the address"><Pencil size={12} /></button>}
            </div>
          ) : <p className="text-amber-700">{t.hint ?? "No public address yet."}</p>}
          {t.short_url && (
            <>
              <div className="flex items-center gap-1 text-muted"><span className="truncate font-mono text-ink/80">{t.short_url.replace(/^https?:\/\//, "")}</span></div>
              <div className="text-muted"><b className="font-semibold text-ink tabular-nums">{t.clicks}</b> open{t.clicks === 1 ? "" : "s"} from your links and codes</div>
            </>
          )}
        </div>
      </div>
      {t.short_url && (
        <footer className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <ShareMenu t={t} d={d} />
          <DownloadMenu t={t} d={d} onPreview={onPreview} />
          <button type="button" onClick={onPreview} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:underline"><Eye size={13} />Preview</button>
        </footer>
      )}
    </article>
  );
}

function PreviewModal({ t, d, setD, onClose }: { t: Target; d: Design; setD: (p: Partial<Design>) => void; onClose: () => void }) {
  const [tab, setTab] = useState<"qr" | "image" | "barcode">("qr");
  const title = t.key === "all" ? "All channels" : `${t.platform_label}${t.handle ? ` · ${t.handle}` : ""}`;
  const tabs = [{ id: "qr", label: "QR code", icon: <QrCode size={14} /> }, { id: "image", label: "Follow us image", icon: <ImageIcon size={14} /> }, { id: "barcode", label: "Barcode", icon: <Barcode size={14} /> }] as const;
  return (
    <Modal title={title} onClose={onClose} wide>
      <div className="-mt-1 mb-4 flex gap-1 rounded-lg bg-slate-100 p-1">
        {tabs.map((x) => (
          <button key={x.id} type="button" onClick={() => setTab(x.id)} className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-sm ${tab === x.id ? "bg-surface font-semibold text-ink shadow-sm" : "text-muted hover:text-ink"}`}>{x.icon}{x.label}</button>
        ))}
      </div>
      {tab === "qr" && (
        <div className="grid items-center gap-5 sm:grid-cols-[16rem_1fr]">
          <div className="rounded-2xl border border-line bg-white p-3"><Img path={qrPath(t.key, d, "png", { size: 768 })} alt="QR code" className="aspect-square w-full" /></div>
          <div className="space-y-3 text-sm">
            <p className="text-ink/80">Any phone camera opens this straight away. Use it on posters, business cards, slides, stalls and packaging.</p>
            <p className="font-mono text-xs text-muted">{t.short_url}</p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void downloadFile(qrPath(t.key, d, "png", { size: 2048, download: true }), `qr-${t.stem}.png`)}><span className="inline-flex items-center gap-1.5"><Download size={14} />PNG (2048 px)</span></Button>
              <Button variant="secondary" onClick={() => void downloadFile(qrPath(t.key, d, "svg", { download: true }), `qr-${t.stem}.svg`)}><span className="inline-flex items-center gap-1.5"><Download size={14} />SVG (vector)</span></Button>
            </div>
            <p className="text-xs text-muted">Colours, dots and the logo come from the Design bar. Dark codes on a light background scan best.</p>
          </div>
        </div>
      )}
      {tab === "image" && (
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-1 text-xs">
            {SHAPES.map((s) => <button key={s.id} type="button" onClick={() => setD({ shape: s.id })} title={s.hint} className={`rounded-md border px-2 py-1 ${d.shape === s.id ? "border-primary-500 bg-primary-50 font-semibold text-primary-800" : "border-line text-muted hover:text-ink"}`}>{s.label}</button>)}
            <span className="mx-1 h-4 w-px bg-line" />
            {(["dark", "light"] as const).map((s) => <button key={s} type="button" onClick={() => setD({ theme: s })} className={`rounded-md border px-2 py-1 ${d.theme === s ? "border-primary-500 bg-primary-50 font-semibold text-primary-800" : "border-line text-muted hover:text-ink"}`}>{s === "dark" ? "Dark" : "Light"}</button>)}
            <span className="ml-auto text-muted">{SHAPES.find((s) => s.id === d.shape)?.hint}</span>
          </div>
          <div className="grid place-items-center rounded-xl bg-slate-100 p-3">
            <Img path={cardPath(t.key, d)} alt="Follow us image" className={`max-h-[52vh] w-auto rounded-lg shadow-lg ${d.shape === "landscape" ? "aspect-[1200/630] w-full" : d.shape === "story" ? "aspect-[9/16] h-[52vh]" : d.shape === "portrait" ? "aspect-[4/5] h-[52vh]" : "aspect-square h-[52vh]"}`} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button onClick={() => void downloadFile(cardPath(t.key, d, { download: true }), `follow-${t.stem}-${d.shape}.png`)}><span className="inline-flex items-center gap-1.5"><Download size={14} />Download PNG</span></Button>
            <ShareMenu t={t} d={d} />
            <span className="text-xs text-muted">Post it, put it in a Story or Status, or add it to an email. It uses your brand colours and logo.</span>
          </div>
        </div>
      )}
      {tab === "barcode" && (
        <div className="space-y-3 text-sm">
          <div className="rounded-xl border border-line bg-white p-4"><Img path={barPath(t.key, "png")} alt="Barcode" className="mx-auto max-h-40 w-auto" /></div>
          <p className="text-ink/80">A Code 128 barcode of the same short link, for print layouts and barcode scanners. Most phone cameras don't open barcodes, so use the QR code when you want people to scan with their phone.</p>
          <div className="flex gap-2">
            <Button onClick={() => void downloadFile(barPath(t.key, "png", { download: true }), `barcode-${t.stem}.png`)}><span className="inline-flex items-center gap-1.5"><Download size={14} />PNG</span></Button>
            <Button variant="secondary" onClick={() => void downloadFile(barPath(t.key, "svg", { download: true }), `barcode-${t.stem}.svg`)}><span className="inline-flex items-center gap-1.5"><Download size={14} />SVG</span></Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function EditPage({ k, onClose, onSave, saving, error }: { k: Kit; onClose: () => void; onSave: (b: Record<string, unknown>) => void; saving: boolean; error: string | null }) {
  const [f, setF] = useState(k.settings);
  const base = k.all.page_url.replace(/\/f\/[^/]+$/, "/f/");
  const inp = "mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-500/15";
  return (
    <Modal title="Edit the all-channels page" onClose={onClose} wide>
      <div className="space-y-3 text-sm">
        {error && <Alert>{error}</Alert>}
        <label className="block"><span className="text-muted">Title</span><input className={inp} maxLength={80} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        <label className="block"><span className="text-muted">Message</span><textarea className={inp} rows={2} maxLength={280} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></label>
        <label className="block"><span className="text-muted">Page address</span>
          <div className="mt-1 flex items-center rounded-lg border border-line bg-slate-50 pl-3 text-xs text-muted focus-within:border-primary-400">
            <span className="truncate">{base}</span>
            <input className="min-w-0 flex-1 rounded-r-lg border-0 bg-surface px-2 py-2 text-sm text-ink outline-none" maxLength={42} value={f.slug}
              onChange={(e) => setF({ ...f, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
          </div>
          <span className="mt-1 block text-[11px] text-muted">The share link and QR code keep working if you change this.</span>
        </label>
        <div><span className="text-muted">Page look</span>
          <div className="mt-1 flex gap-2">{(["dark", "light"] as const).map((x) => (
            <button key={x} type="button" onClick={() => setF({ ...f, theme: x })} className={`rounded-lg border px-3 py-1.5 ${f.theme === x ? "border-primary-500 bg-primary-50 font-semibold text-primary-800" : "border-line text-muted"}`}>{x === "dark" ? "Dark" : "Light"}</button>
          ))}</div>
        </div>
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button variant="secondary" onClick={onClose}><span className="inline-flex items-center gap-1"><XIcon size={14} />Cancel</span></Button>
          <Button disabled={saving} onClick={() => onSave(f)}>{saving ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </Modal>
  );
}
