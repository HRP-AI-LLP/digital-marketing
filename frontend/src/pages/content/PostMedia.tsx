import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Box, Check, Image as ImageIcon, Library as LibraryIcon, Sparkles, Wand2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Button, EmptyState, Field, Modal, Select, Spinner, Tabs, Textarea, Toggle } from "../../components/ui";
import { CanvaPicker } from "../media/Canva";
import { TemplatePicker } from "../media/Gallery";
import { AssetPreview, PREVIEW_SIZE_LABEL, useCaps, useTemplates, type Asset, type AssetPage } from "../media/shared";
import { useMeta, type PostDetail as Post } from "./shared";

type Tab = "design" | "ai" | "3d" | "library" | "canva";
const KIND_NAMES: Record<string, string> = {
  image: "Image", carousel: "Carousel", gif: "GIF loop", animation: "Animation (MP4)", video: "Short video with voice-over", document: "PDF document",
};

/** Kinds that make sense for this platform: its own post types, plus a motion clip wherever GIFs or videos are allowed. */
function kindsFor(postTypes: string[]): string[] {
  const out = ["image", "carousel", "gif", "video", "document"].filter((k) => postTypes.includes(k));
  if (postTypes.includes("video") || postTypes.includes("gif")) out.splice(out.indexOf("video") >= 0 ? out.indexOf("video") : out.length, 0, "animation");
  return out.length ? out : ["image"];
}

/** "Create media" for one post: branded designs, AI images, 3D scenes or library picks, attached straight to the post. */
export function PostMediaDialog({ post, onClose, onAttached }: { post: Post; onClose: () => void; onAttached: (msg: string) => void }) {
  const meta = useMeta();
  const caps = useCaps();
  const plat = meta.data?.platforms.find((x) => x.id === post.platform);
  const kinds = kindsFor(plat?.post_types ?? []);
  const imageSize = plat?.image_size ?? "1080x1080";
  const videoSize = plat?.video_size ?? "1080x1920";
  const [tab, setTab] = useState<Tab>("design");
  const [replace, setReplace] = useState(post.media_ids.length > 0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [options, setOptions] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const finish = (j: Job) => {
    setJobId(null);
    if (j.status === "failed") { setError(j.error || "That didn't work. Try again or pick another option."); return; }
    const r = (j.result ?? {}) as { asset_ids?: string[]; variants?: boolean; post_id?: string | null; note?: string };
    const ids = r.asset_ids ?? [];
    if (r.variants) { setOptions(ids); return; }
    if (r.note) { onAttached(r.note); onClose(); return; }
    if (r.post_id) { onAttached(replace ? "New media is on the post." : "New media was added to the post."); onClose(); return; }
    // AI image and 3D renders land in the library first; attach them now.
    if (ids.length) attach.mutate(ids);
  };
  const job = useJob(jobId, finish);
  const busy = !!jobId;

  const attach = useMutation({
    mutationFn: (ids: string[]) => api(`/media/attach`, { method: "POST", json: { post_id: post.id, asset_ids: ids, replace } }),
    onSuccess: () => { onAttached(replace ? "The post now uses the new media." : "Media added to the post."); onClose(); },
    onError: (e) => setError((e as Error).message),
  });
  const start = (path: string, json: unknown) => {
    setError(null); setOptions(null);
    api<{ job_id: string }>(path, { method: "POST", json }).then((r) => setJobId(r.job_id)).catch((e) => setError((e as Error).message));
  };

  return (
    <Modal title="Create media for this post" onClose={onClose} wide>
      <Tabs value={tab} onChange={(t) => { setTab(t); setOptions(null); setError(null); }} items={[
        { id: "design", label: "Branded design" }, { id: "ai", label: "AI image" }, { id: "3d", label: "3D scene" }, { id: "library", label: "From library" }, { id: "canva", label: "From Canva" },
      ]} />
      {error && <div className="mb-3"><Alert>{error}</Alert></div>}
      {busy && <div className="mb-3"><Alert tone="info"><Spinner label={job?.progress || "Working on it… this can take a minute for videos."} /></Alert></div>}

      {options ? (
        <VariantPicker ids={options} busy={attach.isPending} onBack={() => setOptions(null)} onPick={(id) => attach.mutate([id])} />
      ) : (
        <>
          {tab === "design" && <DesignTab post={post} kinds={kinds} busy={busy} size={imageSize}
            onGo={(kind, template, variants) => start("/media/generate", {
              kind, post_id: post.id, platform: post.platform, replace, variants: variants ? 3 : 0,
              fields: template && template !== "auto" ? { template } : {},
            })} />}
          {tab === "ai" && <AiTab post={post} busy={busy} enabled={!!caps.data?.image} size={imageSize}
            onGo={(prompt, size) => start("/media/ai-image", { prompt, size, platform: post.platform, enhance: true })} />}
          {tab === "3d" && <ThreeDTab busy={busy} imageSize={imageSize} videoSize={videoSize} allowVideo={kinds.includes("video") || kinds.includes("animation")}
            allowGif={kinds.includes("gif")}
            onGo={(scene, output, size) => start("/media/3d", { scene, output, size, platform: post.platform, fields: { headline: post.title || post.topic || "" } })} />}
          {tab === "library" && <LibraryTab busy={attach.isPending} onPick={(ids) => attach.mutate(ids)} />}
          {tab === "canva" && <CanvaPicker returnTo={`/posts/${post.id}`} actionLabel="Import and use in this post"
            onImported={(ids) => { if (ids.length) attach.mutate(ids.slice(0, 10)); }} />}
          <div className="mt-5 border-t border-line pt-4">
            <Toggle checked={replace} onChange={setReplace} label={replace ? "Replace the post's current media" : "Add to the post's current media"} />
          </div>
        </>
      )}
    </Modal>
  );
}

function DesignTab({ post, kinds, busy, size, onGo }: { post: Post; kinds: string[]; busy: boolean; size: string; onGo: (kind: string, template: string, variants: boolean) => void }) {
  const [kind, setKind] = useState(kinds.includes(post.post_type) ? post.post_type : kinds[0]);
  const [template, setTemplate] = useState("auto");
  const [variants, setVariants] = useState(false);
  const templated = kind === "image" || kind === "gif" || kind === "animation";
  useEffect(() => { setTemplate("auto"); }, [kind]);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">Uses this post's text, the visuals brief and your brand kit. Sized for {post.platform_label} automatically.</p>
      <Field label="What to make">
        <Select value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => <option key={k} value={k}>{KIND_NAMES[k] ?? k}</option>)}
        </Select>
      </Field>
      {templated && (
        <Field label="Design">
          <TemplatePicker kind={kind === "animation" ? "gif" : kind} value={template} onChange={setTemplate} size={size} />
        </Field>
      )}
      {kind === "image" && template === "auto" && (
        <Toggle checked={variants} onChange={setVariants} label="Show me 3 different designs to choose from" />
      )}
      <div className="flex justify-end">
        <Button onClick={() => onGo(kind, templated ? template : "auto", kind === "image" && template === "auto" && variants)} disabled={busy}>
          <Wand2 size={16} />{busy ? "Creating…" : "Create"}
        </Button>
      </div>
    </div>
  );
}

function AiTab({ post, busy, enabled, size, onGo }: { post: Post; busy: boolean; enabled: boolean; size: string; onGo: (prompt: string, size: string) => void }) {
  const [prompt, setPrompt] = useState(post.brief.image_brief || post.title || post.topic || "");
  const [sz, setSz] = useState(size);
  if (!enabled) return <Alert tone="warn">No image model is set up yet. Add one in Settings → Integrations → Images, then come back.</Alert>;
  return (
    <div className="space-y-4">
      <Field label="Describe the image" hint="Prefilled from the visuals brief. Your brand colours and style are added automatically.">
        <Textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </Field>
      <Field label="Shape">
        <Select value={sz} onChange={(e) => setSz(e.target.value)}>
          {[...new Set([size, "1080x1080", "1080x1350", "1080x1920", "1920x1080"])].map((s) => <option key={s} value={s}>{PREVIEW_SIZE_LABEL[s] ?? s}{s === size ? " (best for this platform)" : ""}</option>)}
        </Select>
      </Field>
      <div className="flex justify-end">
        <Button onClick={() => onGo(prompt.trim(), sz)} disabled={busy || prompt.trim().length < 3}><Sparkles size={16} />{busy ? "Generating…" : "Generate image"}</Button>
      </div>
    </div>
  );
}

function ThreeDTab({ busy, imageSize, videoSize, allowVideo, allowGif, onGo }: {
  busy: boolean; imageSize: string; videoSize: string; allowVideo: boolean; allowGif: boolean; onGo: (scene: string, output: string, size: string) => void;
}) {
  const tq = useTemplates(imageSize);
  const scenes = useMemo(() => (tq.data?.templates ?? []).filter((t) => t.three_d && t.name !== "scene3d_model"), [tq.data]);
  const [scene, setScene] = useState("scene3d_neural");
  const outputs = ["image", ...(allowVideo ? ["video"] : []), ...(allowGif ? ["gif"] : [])];
  const [output, setOutput] = useState("image");
  if (tq.isLoading) return <Spinner label="Loading scenes…" />;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">A rendered 3D scene in your brand colours, with the post title on it.</p>
      <div className="grid max-h-[340px] grid-cols-3 gap-2.5 overflow-y-auto pr-1 sm:grid-cols-4">
        {scenes.map((t) => (
          <button key={t.name} type="button" onClick={() => setScene(t.name)}
            className={`overflow-hidden rounded-lg border text-left ${scene === t.name ? "border-primary-500 ring-2 ring-primary-500/30" : "border-line hover:border-ink/30"}`}>
            <img src={t.preview_url} alt="" className="aspect-square w-full object-cover" loading="lazy" />
            <div className="truncate px-2 py-1 text-xs">{t.label}</div>
          </button>
        ))}
      </div>
      <Field label="Output">
        <Select value={output} onChange={(e) => setOutput(e.target.value)}>
          {outputs.map((o) => <option key={o} value={o}>{{ image: "Still image", video: "Short clip (MP4)", gif: "GIF loop" }[o]}</option>)}
        </Select>
      </Field>
      <div className="flex justify-end">
        <Button onClick={() => onGo(scene.replace("scene3d_", ""), output, output === "video" ? videoSize : imageSize)} disabled={busy}><Box size={16} />{busy ? "Rendering…" : "Render 3D"}</Button>
      </div>
    </div>
  );
}

function LibraryTab({ busy, onPick }: { busy: boolean; onPick: (ids: string[]) => void }) {
  const [kind, setKind] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const q = useQuery({
    queryKey: ["media", "pick", kind],
    queryFn: () => api<AssetPage>(`/media?page_size=40${kind ? `&kind=${kind}` : ""}`),
  });
  const items = (q.data?.items ?? []).filter((a: Asset) => a.kind !== "audio" && a.kind !== "model3d");
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(0, 10)));
  return (
    <div className="space-y-3">
      <Select aria-label="Kind" value={kind} onChange={(e) => { setKind(e.target.value); setSel([]); }} className="max-w-[200px]">
        <option value="">All media</option><option value="image">Images</option><option value="gif">GIFs</option><option value="video">Videos</option><option value="document">PDFs</option>
      </Select>
      {q.isLoading ? <Spinner label="Loading library…" /> : items.length === 0 ? (
        <EmptyState title="Nothing in the library yet" body="Create something in the other tabs, or upload in Media studio." />
      ) : (
        <div className="grid max-h-[380px] grid-cols-3 gap-2.5 overflow-y-auto pr-1 sm:grid-cols-5">
          {items.map((a) => {
            const on = sel.includes(a.id);
            return (
              <button key={a.id} type="button" onClick={() => toggle(a.id)} aria-pressed={on}
                className={`relative overflow-hidden rounded-lg border ${on ? "border-primary-500 ring-2 ring-primary-500/30" : "border-line hover:border-ink/30"}`}>
                <div className="aspect-square bg-slate-100"><AssetPreview asset={a} /></div>
                {on && <span className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full bg-primary-600 text-white"><Check size={13} /></span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="flex justify-end">
        <Button onClick={() => onPick(sel)} disabled={busy || sel.length === 0}><LibraryIcon size={16} />Use {sel.length > 1 ? `${sel.length} items` : "selected"}</Button>
      </div>
    </div>
  );
}

function VariantPicker({ ids, busy, onPick, onBack }: { ids: string[]; busy: boolean; onPick: (id: string) => void; onBack: () => void }) {
  const q = useQuery({
    queryKey: ["media-assets", ids],
    queryFn: () => Promise.all(ids.map((id) => api<Asset>(`/media/${id}`))),
  });
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Pick the design you like. The others stay in your library.</p>
      {q.isLoading ? <Spinner label="Loading options…" /> : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {(q.data ?? []).map((a) => (
            <div key={a.id} className="overflow-hidden rounded-lg border border-line">
              <div className="aspect-square bg-slate-100"><AssetPreview asset={a} /></div>
              <div className="p-2"><Button className="w-full" onClick={() => onPick(a.id)} disabled={busy}><ImageIcon size={15} />Use this one</Button></div>
            </div>
          ))}
        </div>
      )}
      <Button variant="ghost" onClick={onBack}>Back</Button>
    </div>
  );
}
