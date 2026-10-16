import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Film, Image as ImageIcon, Images, Plus, Sparkles, Trash2, Wand2 } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Button, Card, EmptyState, Field, Input, Select, Spinner, Textarea } from "../../components/ui";
import { AssetDetail } from "./Library";
import {
  AssetPreview, textToList, useCaps, useTemplates, type Asset, type Template, type Voice,
} from "./shared";

type Kind = "image" | "carousel" | "gif" | "animation" | "video" | "document";
type Slide = { heading: string; body: string };
type Scene = { caption: string; voiceover: string; query: string };

const KINDS: { id: Kind; label: string; hint: string; icon: typeof ImageIcon }[] = [
  { id: "image", label: "Image", hint: "One branded graphic", icon: ImageIcon },
  { id: "carousel", label: "Carousel", hint: "3–8 swipeable slides", icon: Images },
  { id: "document", label: "Document", hint: "Slides as a PDF (LinkedIn)", icon: FileText },
  { id: "gif", label: "GIF", hint: "Short animated card", icon: Sparkles },
  { id: "animation", label: "Animation", hint: "Animated card as MP4", icon: Wand2 },
  { id: "video", label: "Short video", hint: "15–45 s with captions and voice", icon: Film },
];

const BACKGROUNDS = [
  ["auto", "Best available (AI image, then stock, then brand gradient)"],
  ["gradient", "Brand gradient"],
  ["ai", "AI image"],
  ["stock", "Stock photo"],
];

function SlidesEditor({ slides, onChange }: { slides: Slide[]; onChange: (s: Slide[]) => void }) {
  const set = (i: number, k: keyof Slide, v: string) => onChange(slides.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  return (
    <div className="space-y-3">
      {slides.map((s, i) => (
        <div key={i} className="rounded-lg border border-line p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">Slide {i + 2}</span>
            {slides.length > 1 && (
              <button type="button" onClick={() => onChange(slides.filter((_, j) => j !== i))} aria-label={`Remove slide ${i + 2}`}
                className="text-muted hover:text-danger"><Trash2 size={15} /></button>
            )}
          </div>
          <div className="grid gap-2">
            <Input placeholder="Heading" value={s.heading} onChange={(e) => set(i, "heading", e.target.value)} />
            <Textarea rows={2} placeholder="Text (a sentence or two)" value={s.body} onChange={(e) => set(i, "body", e.target.value)} />
          </div>
        </div>
      ))}
      {slides.length < 6 && (
        <Button type="button" variant="secondary" onClick={() => onChange([...slides, { heading: "", body: "" }])}><Plus size={15} />Add a slide</Button>
      )}
    </div>
  );
}

function ScenesEditor({ scenes, onChange }: { scenes: Scene[]; onChange: (s: Scene[]) => void }) {
  const set = (i: number, k: keyof Scene, v: string) => onChange(scenes.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  return (
    <div className="space-y-3">
      {scenes.map((s, i) => (
        <div key={i} className="rounded-lg border border-line p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">Scene {i + 1}</span>
            {scenes.length > 1 && (
              <button type="button" onClick={() => onChange(scenes.filter((_, j) => j !== i))} aria-label={`Remove scene ${i + 1}`}
                className="text-muted hover:text-danger"><Trash2 size={15} /></button>
            )}
          </div>
          <div className="grid gap-2">
            <Input placeholder="Caption on screen (short; wrap a word in *stars* to highlight it)" value={s.caption}
              onChange={(e) => set(i, "caption", e.target.value)} />
            <Textarea rows={2} placeholder="Voice-over (what the narrator says)" value={s.voiceover}
              onChange={(e) => set(i, "voiceover", e.target.value)} />
            <Input placeholder="Footage to look for (optional, e.g. “team meeting office”)" value={s.query}
              onChange={(e) => set(i, "query", e.target.value)} />
          </div>
        </div>
      ))}
      {scenes.length < 8 && (
        <Button type="button" variant="secondary" onClick={() => onChange([...scenes, { caption: "", voiceover: "", query: "" }])}>
          <Plus size={15} />Add a scene
        </Button>
      )}
    </div>
  );
}

function TemplateFields({ tpl, values, onChange }: { tpl: Template; values: Record<string, string>; onChange: (k: string, v: string) => void }) {
  return (
    <div className="grid gap-4">
      {tpl.fields.map((f) => (
        <Field key={f.name} label={f.label} hint={f.type === "list" ? "One per line." : f.name === "headline" ? "Wrap a word in *stars* to highlight it." : undefined}>
          {f.type === "textarea" || f.type === "list"
            ? <Textarea rows={f.type === "list" ? 4 : 2} value={values[f.name] ?? ""} onChange={(e) => onChange(f.name, e.target.value)} />
            : <Input value={values[f.name] ?? ""} onChange={(e) => onChange(f.name, e.target.value)} />}
        </Field>
      ))}
    </div>
  );
}

function Results({ ids }: { ids: string[] }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<Asset | null>(null);
  const assets = useQuery({
    queryKey: ["media-results", ids],
    queryFn: () => Promise.all(ids.map((id) => api<Asset>(`/media/${id}`))),
    enabled: ids.length > 0,
  });
  if (assets.isLoading) return <Spinner label="Loading results…" />;
  if (assets.error) return <Alert>{(assets.error as Error).message}</Alert>;
  const items = assets.data ?? [];
  return (
    <>
      <div className={`grid gap-3 ${items.length > 1 ? "grid-cols-2 lg:grid-cols-3" : "grid-cols-1"}`}>
        {items.map((a) => (
          <div key={a.id} className="overflow-hidden rounded-xl border border-line bg-surface">
            <div className={`${items.length > 1 ? "aspect-square" : "max-h-[560px]"} flex items-center justify-center overflow-hidden bg-slate-100`}>
              <AssetPreview asset={a} controls={items.length === 1 || a.kind === "video"} />
            </div>
            <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs text-muted">
              <span>{a.width && `${a.width} × ${a.height}`}{a.duration_s ? ` · ${a.duration_s.toFixed(1)} s` : ""}</span>
              <button className="font-medium text-brand hover:underline" onClick={() => setOpen(a)}>Details</button>
            </div>
          </div>
        ))}
      </div>
      {open && <AssetDetail asset={open} onClose={() => setOpen(null)}
        onChanged={() => { qc.invalidateQueries({ queryKey: ["media"] }); qc.invalidateQueries({ queryKey: ["media-results"] }); }} />}
    </>
  );
}

export default function Create() {
  const qc = useQueryClient();
  const templates = useTemplates();
  const caps = useCaps();
  const [kind, setKind] = useState<Kind>("image");
  const [platform, setPlatform] = useState("instagram");
  const [size, setSize] = useState("");
  const [tplName, setTplName] = useState("photo_headline");
  const [values, setValues] = useState<Record<string, string>>({ headline: "", eyebrow: "", body: "", cta: "" });
  const [theme, setTheme] = useState("");
  const [background, setBackground] = useState("auto");
  const [brief, setBrief] = useState("");
  const [slides, setSlides] = useState<Slide[]>([{ heading: "", body: "" }, { heading: "", body: "" }, { heading: "", body: "" }]);
  const [closing, setClosing] = useState({ heading: "Found this useful?", body: "Save it for later and share it with your team.", cta: "" });
  const [lines, setLines] = useState("");
  const [scenes, setScenes] = useState<Scene[]>([{ caption: "", voiceover: "", query: "" }, { caption: "", voiceover: "", query: "" }]);
  const [voice, setVoice] = useState("");
  const [visuals, setVisuals] = useState("auto");
  const [music, setMusic] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [resultIds, setResultIds] = useState<string[]>([]);
  const [jobError, setJobError] = useState<string | null>(null);

  const voices = useQuery({
    queryKey: ["media-voices"], queryFn: () => api<{ default: string; voices: Voice[] }>("/media/voices"),
    enabled: kind === "video", staleTime: 3600_000,
  });
  useEffect(() => { if (!voice && voices.data) setVoice(voices.data.default); }, [voices.data, voice]);

  const data = templates.data;
  const imageTemplates = useMemo(() => (data?.templates ?? []).filter((t) => t.kinds.includes("image")), [data]);
  const tpl = imageTemplates.find((t) => t.name === tplName) ?? imageTemplates[0];
  const platforms = useMemo(() => (data?.platforms ?? []).filter((p) => p.post_types.includes(kind)), [data, kind]);
  useEffect(() => {
    if (platforms.length && !platforms.some((p) => p.id === platform)) setPlatform(platforms[0].id);
  }, [platforms, platform]);
  const plat = platforms.find((p) => p.id === platform);
  const defaultSize = kind === "video" ? plat?.video_size : kind === "document" ? [1080, 1350] : plat?.image_size;

  const onDone = (j: Job) => {
    if (j.status === "done") {
      setResultIds(((j.result?.asset_ids as string[]) ?? []));
      qc.invalidateQueries({ queryKey: ["media"] });
    } else setJobError(j.error ?? "Something went wrong.");
    setJobId(null);
  };
  const job = useJob(jobId, onDone);

  const fields = (): Record<string, unknown> => {
    const f: Record<string, unknown> = {};
    if (kind === "image") {
      for (const fd of tpl?.fields ?? []) {
        const v = values[fd.name];
        if (v) f[fd.name] = fd.type === "list" ? textToList(v) : v;
      }
      f.template = tpl?.name;
      if (theme) f.theme = theme;
      if (tpl?.photo) { f.background = background; if (brief) f.image_brief = brief; }
    } else if (kind === "carousel" || kind === "document") {
      Object.assign(f, { headline: values.headline, body: values.body, eyebrow: values.eyebrow, cta: closing.cta,
        cta_heading: closing.heading, cta_body: closing.body,
        slides: slides.filter((s) => s.heading || s.body) });
      if (brief && kind === "carousel") { f.image_brief = brief; f.background = background; }
    } else if (kind === "gif" || kind === "animation") {
      Object.assign(f, { lines: textToList(lines), eyebrow: values.eyebrow, cta: values.cta });
    } else if (kind === "video") {
      Object.assign(f, { headline: values.headline, video_script: scenes.filter((s) => s.caption || s.voiceover),
        voice, visuals, cta: values.cta });
      if (music) f.music = music;
      if (brief) f.image_brief = brief;
    }
    return f;
  };

  const generate = useMutation({
    mutationFn: () => api<{ job_id: string }>("/media/generate", {
      method: "POST", json: { kind, platform, size: size || null, fields: fields() },
    }),
    onMutate: () => { setResultIds([]); setJobError(null); },
    onSuccess: (r) => setJobId(r.job_id),
  });
  const preview = useMutation({
    mutationFn: () => api<Asset>("/media/render", {
      method: "POST", json: { template: tpl?.name, fields: fields(), platform, size: size || null, theme: theme || null,
        background: tpl?.photo && background !== "auto" ? background : "gradient" },
    }),
    onMutate: () => { setResultIds([]); setJobError(null); },
    onSuccess: (a) => { setResultIds([a.id]); qc.invalidateQueries({ queryKey: ["media"] }); },
  });

  if (templates.isLoading) return <Spinner label="Loading templates…" />;
  if (templates.error) return <Alert>{(templates.error as Error).message}</Alert>;
  const busy = generate.isPending || !!jobId || preview.isPending;
  const set = (k: string) => (v: string) => setValues((s) => ({ ...s, [k]: v }));

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-5">
        <Card>
          <h2 className="mb-3 font-semibold">What to make</h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Output type">
            {KINDS.map((k) => {
              const Icon = k.icon;
              const on = kind === k.id;
              return (
                <button key={k.id} type="button" role="radio" aria-checked={on} onClick={() => setKind(k.id)}
                  className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${on ? "border-primary-500 bg-brand-tint" : "border-line hover:border-brand/40"}`}>
                  <span className="flex items-center gap-2 text-sm font-semibold"><Icon size={16} className={on ? "text-brand" : "text-muted"} />{k.label}</span>
                  <span className="mt-0.5 block text-xs text-muted">{k.hint}</span>
                </button>
              );
            })}
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Platform format" hint={defaultSize ? `${defaultSize[0]} × ${defaultSize[1]} px` : undefined}>
              <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                {platforms.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </Select>
            </Field>
            {kind !== "video" && kind !== "document" && (
              <Field label="Size">
                <Select value={size} onChange={(e) => setSize(e.target.value)}>
                  <option value="">Platform default</option>
                  {data?.sizes.map((s) => <option key={s.id} value={s.id}>{s.label} ({s.id.replace("x", " × ")})</option>)}
                </Select>
              </Field>
            )}
          </div>
        </Card>

        <Card>
          <h2 className="mb-3 font-semibold">Content</h2>
          {kind === "image" && tpl && (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Template" hint={tpl.description}>
                  <Select value={tpl.name} onChange={(e) => setTplName(e.target.value)}>
                    {imageTemplates.map((t) => <option key={t.name} value={t.name}>{t.label}</option>)}
                  </Select>
                </Field>
                <Field label="Look">
                  <Select value={theme} onChange={(e) => setTheme(e.target.value)}>
                    <option value="">Template default</option>
                    <option value="dark">Dark (navy)</option>
                    <option value="light">Light</option>
                  </Select>
                </Field>
              </div>
              <TemplateFields tpl={tpl} values={values} onChange={(k, v) => set(k)(v)} />
              {tpl.photo && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Background">
                    <Select value={background} onChange={(e) => setBackground(e.target.value)}>
                      {BACKGROUNDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </Select>
                  </Field>
                  <Field label="Describe the background photo" hint="Used for AI images and stock search. Text is never put inside the photo.">
                    <Input value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="Recruiter reviewing CVs in a bright office" />
                  </Field>
                </div>
              )}
            </div>
          )}

          {(kind === "carousel" || kind === "document") && (
            <div className="space-y-4">
              <Field label="Cover headline" hint="Slide 1. Wrap a word in *stars* to highlight it.">
                <Textarea rows={2} value={values.headline ?? ""} onChange={(e) => set("headline")(e.target.value)} />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Cover subtitle"><Input value={values.body ?? ""} onChange={(e) => set("body")(e.target.value)} /></Field>
                <Field label="Small label"><Input value={values.eyebrow ?? ""} onChange={(e) => set("eyebrow")(e.target.value)} placeholder="Guide" /></Field>
              </div>
              <SlidesEditor slides={slides} onChange={setSlides} />
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Closing heading"><Input value={closing.heading} onChange={(e) => setClosing({ ...closing, heading: e.target.value })} /></Field>
                <Field label="Closing text"><Input value={closing.body} onChange={(e) => setClosing({ ...closing, body: e.target.value })} /></Field>
                <Field label="Button text"><Input value={closing.cta} onChange={(e) => setClosing({ ...closing, cta: e.target.value })} placeholder="Book a demo" /></Field>
              </div>
              {kind === "carousel" && (
                <Field label="Cover photo (optional)" hint="Describe it to put an AI or stock photo behind the cover.">
                  <Input value={brief} onChange={(e) => setBrief(e.target.value)} />
                </Field>
              )}
            </div>
          )}

          {(kind === "gif" || kind === "animation") && (
            <div className="space-y-4">
              <Field label="Lines" hint="2–4 short lines revealed one after another, then your logo. Wrap a word in *stars* to highlight it.">
                <Textarea rows={4} value={lines} onChange={(e) => setLines(e.target.value)}
                  placeholder={"Your HR team, *amplified*\nAI that drafts, screens and schedules\nSo people can focus on people"} />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Small label"><Input value={values.eyebrow ?? ""} onChange={(e) => set("eyebrow")(e.target.value)} /></Field>
                <Field label="Button text"><Input value={values.cta ?? ""} onChange={(e) => set("cta")(e.target.value)} /></Field>
              </div>
              <p className="text-xs text-muted">{kind === "gif" ? "GIFs last 5 seconds and stay under 8 MB; an MP4 copy is saved for platforms that prefer video." : "Animations are 7-second MP4 files."}</p>
            </div>
          )}

          {kind === "video" && (
            <div className="space-y-4">
              <Field label="Title" hint="Shown on the first scene and the closing card.">
                <Input value={values.headline ?? ""} onChange={(e) => set("headline")(e.target.value)} />
              </Field>
              <ScenesEditor scenes={scenes} onChange={setScenes} />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Voice">
                  <Select value={voice} onChange={(e) => setVoice(e.target.value)} disabled={voices.isLoading}>
                    {(voices.data?.voices ?? []).map((v) => <option key={`${v.provider}-${v.id}`} value={v.id}>{v.label}</option>)}
                  </Select>
                </Field>
                <Field label="Footage">
                  <Select value={visuals} onChange={(e) => setVisuals(e.target.value)}>
                    <option value="auto">Best available (stock clips, AI images, stock photos)</option>
                    <option value="stock">Stock clips and photos</option>
                    <option value="ai">AI images</option>
                    <option value="gradient">Brand gradient only</option>
                  </Select>
                </Field>
                <Field label="Background music" hint={caps.data?.music.length ? undefined : "Add royalty-free tracks to storage/music/ to use music."}>
                  <Select value={music} onChange={(e) => setMusic(e.target.value)} disabled={!caps.data?.music.length}>
                    <option value="">{caps.data?.music.length ? "Pick one for me" : "No music files found"}</option>
                    {caps.data?.music.length ? <option value="none">No music</option> : null}
                    {caps.data?.music.map((m) => <option key={m} value={m}>{m}</option>)}
                  </Select>
                </Field>
                <Field label="Button text on the closing card"><Input value={values.cta ?? ""} onChange={(e) => set("cta")(e.target.value)} /></Field>
              </div>
            </div>
          )}
        </Card>

        {generate.error && <Alert>{(generate.error as Error).message}</Alert>}
        {preview.error && <Alert>{(preview.error as Error).message}</Alert>}
        <div className="flex flex-wrap items-center gap-2">
          {kind === "image" && (
            <Button variant="secondary" onClick={() => preview.mutate()} disabled={busy}>
              {preview.isPending ? "Rendering…" : "Render preview"}
            </Button>
          )}
          <Button onClick={() => generate.mutate()} disabled={busy}>
            <Sparkles size={16} />{kind === "video" ? "Generate video" : "Generate"}
          </Button>
          {kind === "image" && <span className="text-xs text-muted">Preview uses the brand gradient and is ready in seconds. Generate also fetches the background photo.</span>}
        </div>
      </div>

      <div>
        <Card className="xl:sticky xl:top-4">
          <h2 className="mb-3 font-semibold">Result</h2>
          {jobId ? (
            <div className="py-10 text-center">
              <Spinner label={job?.progress ?? (job?.status === "queued" ? "Waiting for the worker…" : "Working…")} />
              {kind === "video" && <p className="mt-2 text-xs text-muted">Videos take a minute or two.</p>}
            </div>
          ) : preview.isPending ? (
            <div className="py-10 text-center"><Spinner label="Rendering…" /></div>
          ) : jobError ? (
            <Alert>{jobError}</Alert>
          ) : resultIds.length ? (
            <Results ids={resultIds} />
          ) : (
            <EmptyState title="Nothing generated yet" body="Fill in the content and choose Generate. Results are saved to the library." />
          )}
        </Card>
      </div>
    </div>
  );
}

