import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Copy, ExternalLink, GripVertical, ImagePlus, Plus, Star, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { Alert, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Stat, Toggle } from "../components/ui";
import { AssetPreview, type AssetPage } from "./media/shared";

type LinkItem = {
  id: string; title: string; url: string; icon: string | null; thumbnail_asset_id: string | null; highlight: boolean; enabled: boolean;
};
type Theme = { mode?: "light" | "dark" | "brand"; button_style?: "filled" | "outline" | "soft"; accent?: string; background?: string; text?: string };
type PageData = {
  slug: string; published: boolean; title: string | null; tagline: string | null; show_logo: boolean; show_socials: boolean;
  show_posts: boolean; posts_count: number; links: LinkItem[]; theme: Theme;
};
type PageOut = PageData & {
  exists: boolean; public_url: string; local_url: string; has_public_address: boolean; thumbnail_urls: Record<string, string>;
  organization: { name: string; logo_url: string | null; socials: { key: string; label: string; url: string }[] };
};
type Stats = {
  days: number; views: number; clicks: number; click_rate: number | null;
  by_day: { day: string; views: number; clicks: number }[];
  by_target: { target: string; label: string; kind: string; clicks: number }[];
};

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`.replace(/\D/g, "").padEnd(32, "0")
  .replace(/^(.{8})(.{4})(.{4})(.{4})(.{12}).*/, "$1-$2-$3-$4-$5"));
const pick = (p: PageOut): PageData => ({
  slug: p.slug, published: p.published, title: p.title, tagline: p.tagline, show_logo: p.show_logo, show_socials: p.show_socials,
  show_posts: p.show_posts, posts_count: p.posts_count, links: p.links, theme: p.theme ?? {},
});

function CopyLink({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-2.5 py-1.5">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" title={value}>{value}</code>
      <button type="button" className="shrink-0 text-xs font-medium text-primary-700 hover:underline"
        onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
        {copied ? "Copied" : <span className="inline-flex items-center gap-1"><Copy size={12} />Copy</span>}
      </button>
    </div>
  );
}

function ThumbPicker({ onPick, onClose }: { onPick: (id: string, url: string | null) => void; onClose: () => void }) {
  const q = useQuery({ queryKey: ["media", "lib-thumb"], queryFn: () => api<AssetPage>("/media?page_size=40&kind=image,gif") });
  return (
    <Modal title="Choose a thumbnail" onClose={onClose} wide>
      {q.isLoading ? <Spinner label="Loading library…" /> : !(q.data?.items.length) ? (
        <EmptyState title="No images yet" body="Upload or create images in Media studio first." />
      ) : (
        <div className="grid max-h-[60vh] grid-cols-3 gap-2.5 overflow-y-auto sm:grid-cols-5">
          {q.data.items.map((a) => (
            <button key={a.id} type="button" onClick={() => onPick(a.id, a.thumb_url ?? a.url)}
              className="overflow-hidden rounded-lg border border-line hover:border-primary-500">
              <div className="aspect-square bg-slate-100"><AssetPreview asset={a} /></div>
            </button>
          ))}
        </div>
      )}
    </Modal>
  );
}

function LinkRow({ link, index, count, thumbUrl, onChange, onRemove, onMove, dragProps }: {
  link: LinkItem; index: number; count: number; thumbUrl: string | null;
  onChange: (l: LinkItem, thumbUrl?: string | null) => void; onRemove: () => void; onMove: (to: number) => void;
  dragProps: { draggable: boolean; onDragStart: (e: DragEvent) => void; onDragOver: (e: DragEvent) => void; onDrop: (e: DragEvent) => void; onDragEnd: () => void; dragging: boolean };
}) {
  const [thumb, setThumb] = useState(false);
  const { dragging, ...drag } = dragProps;
  return (
    <li {...drag} className={`rounded-xl border bg-surface p-3 ${dragging ? "border-primary-500 opacity-60" : "border-line"} ${link.enabled ? "" : "opacity-70"}`}>
      <div className="flex items-start gap-2">
        <span className="mt-2 cursor-grab text-muted" title="Drag to reorder" aria-hidden="true"><GripVertical size={18} /></span>
        <div className="min-w-0 flex-1 space-y-2">
          <div className="grid gap-2 sm:grid-cols-2">
            <Input aria-label="Button text" placeholder="Button text" maxLength={100} value={link.title} onChange={(e) => onChange({ ...link, title: e.target.value })} />
            <Input aria-label="Link" placeholder="https://" maxLength={1000} value={link.url} onChange={(e) => onChange({ ...link, url: e.target.value })} />
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Input aria-label="Icon (emoji)" placeholder="Icon 🙂" maxLength={4} className="w-24" value={link.icon ?? ""}
              onChange={(e) => onChange({ ...link, icon: e.target.value || null })} />
            {link.thumbnail_asset_id ? (
              <span className="inline-flex items-center gap-2">
                {thumbUrl && <img src={thumbUrl} alt="" className="h-8 w-8 rounded object-cover" />}
                <button type="button" className="text-xs text-muted hover:text-danger" onClick={() => onChange({ ...link, thumbnail_asset_id: null })}>
                  <X size={12} className="inline" /> Remove picture
                </button>
              </span>
            ) : (
              <button type="button" className="inline-flex items-center gap-1 text-sm text-primary-700 hover:underline" onClick={() => setThumb(true)}>
                <ImagePlus size={14} /> Picture
              </button>
            )}
            <Toggle checked={link.highlight} onChange={(v) => onChange({ ...link, highlight: v })} label="Highlight" />
            <Toggle checked={link.enabled} onChange={(v) => onChange({ ...link, enabled: v })} label="Show" />
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <Button variant="ghost" onClick={() => onMove(index - 1)} disabled={index === 0} aria-label="Move up"><ArrowUp size={14} /></Button>
          <Button variant="ghost" onClick={() => onMove(index + 1)} disabled={index === count - 1} aria-label="Move down"><ArrowDown size={14} /></Button>
          <Button variant="ghost" onClick={onRemove} aria-label={`Remove ${link.title || "button"}`}><Trash2 size={14} /></Button>
        </div>
      </div>
      {thumb && <ThumbPicker onClose={() => setThumb(false)} onPick={(id, url) => { onChange({ ...link, thumbnail_asset_id: id }, url); setThumb(false); }} />}
    </li>
  );
}

function StatsPanel({ enabled }: { enabled: boolean }) {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["linkinbio-stats", days], queryFn: () => api<Stats>(`/linkinbio/stats?days=${days}`), enabled });
  const s = q.data;
  const max = Math.max(1, ...(s?.by_day ?? []).map((d) => Math.max(d.views, d.clicks)));
  const topMax = Math.max(1, ...(s?.by_target ?? []).map((t) => t.clicks));
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Visits and clicks</h2>
        <Select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-auto">
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </Select>
      </div>
      {!enabled ? <p className="text-sm text-muted">Numbers appear once the page is saved and visited.</p> : q.isLoading ? <Spinner /> : s && (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Page visits" value={s.views} />
            <Stat label="Clicks" value={s.clicks} />
            <Stat label="Click rate" value={s.click_rate == null ? "–" : `${s.click_rate}%`} />
          </div>
          <div>
            <div className="flex h-24 items-end gap-[2px]" role="img" aria-label={`Daily visits and clicks over the last ${days} days`}>
              {s.by_day.map((d) => (
                <div key={d.day} className="flex h-full flex-1 items-end gap-px" title={`${d.day}: ${d.views} visits, ${d.clicks} clicks`}>
                  <div className="flex-1 rounded-t bg-primary-200" style={{ height: `${(d.views / max) * 100}%` }} />
                  <div className="flex-1 rounded-t bg-primary-600" style={{ height: `${(d.clicks / max) * 100}%` }} />
                </div>
              ))}
            </div>
            <div className="mt-1 flex gap-4 text-xs text-muted">
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-primary-200" />Visits</span>
              <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm bg-primary-600" />Clicks</span>
            </div>
          </div>
          {s.by_target.length > 0 ? (
            <ul className="space-y-2">
              {s.by_target.slice(0, 10).map((t) => (
                <li key={t.target} className="text-sm">
                  <div className="flex justify-between gap-2"><span className="min-w-0 truncate">{t.label}</span><span className="tabular-nums text-muted">{t.clicks}</span></div>
                  <div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-primary-600" style={{ width: `${(t.clicks / topMax) * 100}%` }} /></div>
                </li>
              ))}
            </ul>
          ) : <p className="text-sm text-muted">No clicks in this period yet.</p>}
          <p className="text-xs text-muted">Counted per button per day. No names, IP addresses or cookies are stored. Link-preview bots aren't counted.</p>
        </div>
      )}
    </Card>
  );
}

export default function LinkInBio() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["linkinbio"], queryFn: () => api<PageOut>("/linkinbio") });
  const [d, setD] = useState<PageData | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [html, setHtml] = useState("");
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => { if (q.data && !d) { setD(pick(q.data)); setThumbs(q.data.thumbnail_urls ?? {}); } }, [q.data, d]);

  const update = (patch: Partial<PageData>) => { setD((cur) => (cur ? { ...cur, ...patch } : cur)); setDirty(true); setNotice(null); };
  const save = useMutation({
    mutationFn: (body: PageData) => api<PageOut>("/linkinbio", { method: "PUT", json: body }),
    onSuccess: (p) => {
      qc.setQueryData(["linkinbio"], p); setD(pick(p)); setThumbs(p.thumbnail_urls ?? {}); setDirty(false);
      setNotice(p.published ? "Saved. Your page is live." : "Saved. The page stays hidden until you publish it.");
      qc.invalidateQueries({ queryKey: ["linkinbio-stats"] });
    },
  });

  // Live preview (debounced; links that aren't complete yet are left out so typing never errors).
  const previewBody = useMemo(() => d && ({ ...d, links: d.links.filter((l) => l.title.trim() && /^(https?:\/\/.+|mailto:.+|tel:.+)/i.test(l.url.trim())) }), [d]);
  useEffect(() => {
    if (!previewBody) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api<{ html: string }>("/linkinbio/preview", { method: "POST", json: previewBody })
        .then((r) => { setHtml(r.html); setPreviewError(null); })
        .catch((e) => setPreviewError((e as Error).message));
    }, 400);
    return () => window.clearTimeout(timer.current);
  }, [previewBody]);

  if (q.isLoading || (!d && !q.error)) return <Spinner label="Loading your link page…" />;
  if (q.error || !d || !q.data) return <Alert>{q.error ? (q.error as Error).message : "Couldn't load the page."}</Alert>;
  const info = q.data;
  const theme = d.theme ?? {};
  const setLinks = (links: LinkItem[]) => update({ links });
  const move = (from: number, to: number) => {
    if (to < 0 || to >= d.links.length || from === to) return;
    const next = [...d.links];
    const [it] = next.splice(from, 1);
    next.splice(to, 0, it);
    setLinks(next);
  };
  const addLink = () => setLinks([...d.links, { id: newId(), title: "", url: "https://", icon: null, thumbnail_asset_id: null, highlight: false, enabled: true }]);
  const incomplete = d.links.some((l) => !l.title.trim() || !/^(https?:\/\/.{3,}|mailto:.+|tel:.+)/i.test(l.url.trim()));

  return (
    <div>
      <PageHeader title="Link in bio"
        description="One mobile-friendly page with all your important links, for your Instagram, TikTok and X bios. Free, no extra services needed."
        action={
          <div className="flex flex-wrap gap-2">
            {info.exists && d.published && !dirty && (
              <a href={info.has_public_address ? info.public_url : `/l/${d.slug}`} target="_blank" rel="noreferrer">
                <Button variant="secondary"><ExternalLink size={15} /> Open page</Button>
              </a>
            )}
            <Button onClick={() => save.mutate(d)} disabled={save.isPending || (!dirty && info.exists) || incomplete}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        } />
      {save.error && <div className="mb-4"><Alert>{(save.error as Error).message}</Alert></div>}
      {notice && <div className="mb-4"><Alert tone="info">{notice}</Alert></div>}
      {incomplete && dirty && <div className="mb-4"><Alert tone="warn">Every button needs text and a link starting with https://, mailto: or tel: before you can save.</Alert></div>}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-5">
          <Card>
            <h2 className="mb-3 font-semibold">Page</h2>
            <div className="space-y-4">
              <Toggle checked={d.published} onChange={(v) => update({ published: v })} label={d.published ? "Published: anyone with the link can see it" : "Hidden: only you can preview it"} />
              <Field label="Page address" hint="Lowercase letters, numbers and dashes.">
                <div className="flex min-w-0 items-center gap-1">
                  <span className="shrink-0 text-sm text-muted">/l/</span>
                  <Input value={d.slug} maxLength={60} onChange={(e) => update({ slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} />
                </div>
              </Field>
              {info.exists && (
                <div className="space-y-2">
                  <div className="text-sm font-medium">Link to put in your bios</div>
                  {info.has_public_address ? <CopyLink value={info.public_url} /> : (
                    <Alert tone="warn">
                      Set PUBLIC_BASE_URL (the free Cloudflare Tunnel address, see the setup guide) so people outside this laptop can open the page.
                      For now it works only here: <span className="[overflow-wrap:anywhere]">{info.local_url}</span>
                    </Alert>
                  )}
                </div>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name"><Input value={d.title ?? ""} maxLength={120} placeholder={info.organization.name} onChange={(e) => update({ title: e.target.value })} /></Field>
                <Field label="Tagline"><Input value={d.tagline ?? ""} maxLength={300} onChange={(e) => update({ tagline: e.target.value })} /></Field>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                <Toggle checked={d.show_logo} onChange={(v) => update({ show_logo: v })} label={info.organization.logo_url ? "Show logo" : "Show logo (initials until you add one in Organization)"} />
                <Toggle checked={d.show_socials} onChange={(v) => update({ show_socials: v })}
                  label={`Show social icons (${info.organization.socials.length} from Organization settings)`} />
              </div>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                <Toggle checked={d.show_posts} onChange={(v) => update({ show_posts: v })} label="Show latest posts with pictures" />
                {d.show_posts && (
                  <Select aria-label="Number of posts" value={d.posts_count} onChange={(e) => update({ posts_count: Number(e.target.value) })} className="w-auto">
                    {[3, 6, 9, 12].map((n) => <option key={n} value={n}>{n} posts</option>)}
                  </Select>
                )}
              </div>
            </div>
          </Card>

          <Card>
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="font-semibold">Buttons</h2>
              <Button variant="secondary" onClick={addLink} disabled={d.links.length >= 40}><Plus size={15} /> Add button</Button>
            </div>
            {d.links.length === 0 ? (
              <EmptyState title="No buttons yet" body="Add links to your website, a demo booking page, your latest article or a contact email." />
            ) : (
              <ul className="space-y-2.5">
                {d.links.map((l, i) => (
                  <LinkRow key={l.id} link={l} index={i} count={d.links.length} thumbUrl={l.thumbnail_asset_id ? thumbs[l.thumbnail_asset_id] ?? null : null}
                    onChange={(nl, url) => { if (url && nl.thumbnail_asset_id) setThumbs((t) => ({ ...t, [nl.thumbnail_asset_id!]: url })); setLinks(d.links.map((x) => (x.id === l.id ? nl : x))); }}
                    onRemove={() => setLinks(d.links.filter((x) => x.id !== l.id))}
                    onMove={(to) => move(i, to)}
                    dragProps={{
                      draggable: true, dragging: dragIndex === i,
                      onDragStart: (e) => { setDragIndex(i); e.dataTransfer.effectAllowed = "move"; },
                      onDragOver: (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; },
                      onDrop: (e) => { e.preventDefault(); if (dragIndex !== null) move(dragIndex, i); setDragIndex(null); },
                      onDragEnd: () => setDragIndex(null),
                    }} />
                ))}
              </ul>
            )}
            <p className="mt-3 flex items-center gap-1 text-xs text-muted"><Star size={12} /> Highlighted buttons stand out in your accent colour. Drag the handle or use the arrows to reorder.</p>
          </Card>

          <Card>
            <h2 className="mb-3 font-semibold">Look</h2>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Background">
                <Select value={theme.mode ?? "light"} onChange={(e) => update({ theme: { ...theme, mode: e.target.value as Theme["mode"], background: undefined } })}>
                  <option value="light">Light</option><option value="dark">Dark</option><option value="brand">Brand colour</option>
                </Select>
              </Field>
              <Field label="Buttons">
                <Select value={theme.button_style ?? "filled"} onChange={(e) => update({ theme: { ...theme, button_style: e.target.value as Theme["button_style"] } })}>
                  <option value="filled">Filled</option><option value="soft">Soft</option><option value="outline">Outline</option>
                </Select>
              </Field>
              <Field label="Accent colour" hint="Empty uses your brand colour.">
                <div className="flex items-center gap-2">
                  <input type="color" aria-label="Accent colour" value={theme.accent ?? "#075492"} onChange={(e) => update({ theme: { ...theme, accent: e.target.value } })}
                    className="h-9 w-12 cursor-pointer rounded border border-line bg-surface" />
                  {theme.accent && <button type="button" className="text-xs text-muted hover:underline" onClick={() => update({ theme: { ...theme, accent: undefined } })}>Use brand colour</button>}
                </div>
              </Field>
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <h2 className="mb-3 font-semibold">Preview</h2>
            {previewError && <div className="mb-2"><Alert>{previewError}</Alert></div>}
            <div className="mx-auto w-full max-w-[360px] overflow-hidden rounded-[28px] border-[6px] border-slate-800 bg-slate-800 shadow-lg">
              {html ? <iframe title="Link page preview" srcDoc={html} sandbox="" className="h-[640px] w-full rounded-[22px] bg-white" />
                : <div className="flex h-[640px] items-center justify-center bg-white"><Spinner label="Building preview…" /></div>}
            </div>
            <p className="mt-2 text-center text-xs text-muted">Buttons in the preview don't open links.</p>
          </Card>
          <StatsPanel enabled={info.exists} />
        </div>
      </div>
    </div>
  );
}
