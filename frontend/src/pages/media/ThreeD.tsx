import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Film, Image as ImageIcon, Repeat, Upload } from "lucide-react";
import { api } from "../../api/client";
import { upload, useJob, type Job } from "../../api/hooks";
import { Alert, Button, Card, EmptyState, Field, Input, Select, Spinner, Textarea } from "../../components/ui";
import { TemplateCard } from "./Gallery";
import { AssetPreview, useCaps, useTemplates, type Asset, type AssetPage } from "./shared";
import UseInPost from "./UseInPost";

type Output = "image" | "video" | "gif";
const OUTPUTS: { id: Output; label: string; hint: string; icon: typeof ImageIcon }[] = [
  { id: "image", label: "Still image", hint: "PNG, ready in seconds", icon: ImageIcon },
  { id: "video", label: "Video clip", hint: "MP4 loop, about a minute", icon: Film },
  { id: "gif", label: "GIF loop", hint: "Seamless, under 8 MB", icon: Repeat },
];
const SIZES = [["1080x1080", "Square 1:1"], ["1080x1350", "Portrait 4:5"], ["1080x1920", "Story / Reel 9:16"], ["1920x1080", "Landscape 16:9"]];

export default function ThreeD({ initialScene }: { initialScene?: string }) {
  const qc = useQueryClient();
  const caps = useCaps();
  const [size, setSize] = useState("1080x1350");
  const tq = useTemplates(size);
  const scenes = useMemo(() => (tq.data?.templates ?? []).filter((t) => t.three_d), [tq.data]);
  const [scene, setScene] = useState(initialScene ?? "scene3d_neural");
  useEffect(() => { if (initialScene) setScene(initialScene); }, [initialScene]);
  const [output, setOutput] = useState<Output>("image");
  const [f, setF] = useState({ eyebrow: "", headline: "", body: "", text3d: "" });
  const [model, setModel] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<Asset | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [attach, setAttach] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const models = useQuery({ queryKey: ["media", "model3d"], queryFn: () => api<AssetPage>("/media?kind=model3d&page_size=50"),
    enabled: scene === "scene3d_model" });
  useEffect(() => { if (!model && models.data?.items.length) setModel(models.data.items[0].id); }, [models.data, model]);
  const up = useMutation({
    mutationFn: (file: File) => { const form = new FormData(); form.append("files", file, file.name);
      return upload<{ items: Asset[]; errors: { filename: string; error: string }[] }>("/media/upload", form); },
    onSuccess: (r) => {
      if (r.items[0]) { setModel(r.items[0].id); qc.invalidateQueries({ queryKey: ["media"] }); setErr(null); }
      else setErr(r.errors[0]?.error ?? "Upload failed.");
    },
    onError: (e: Error) => setErr(e.message),
  });

  const onDone = (j: Job) => {
    if (j.status === "done") {
      const id = (j.result?.asset_ids as string[] | undefined)?.[0];
      if (id) api<Asset>(`/media/${id}`).then(setResult);
      qc.invalidateQueries({ queryKey: ["media"] });
    } else setErr(j.error ?? "Something went wrong.");
    setJobId(null);
  };
  const job = useJob(jobId, onDone);
  const go = useMutation({
    mutationFn: () => api<{ job_id: string }>("/media/3d", { method: "POST", json: {
      scene: scene.replace("scene3d_", ""), output, size: output === "gif" ? size : size,
      fields: Object.fromEntries(Object.entries(f).filter(([, v]) => v)), model_asset_id: scene === "scene3d_model" ? model || null : null } }),
    onMutate: () => { setResult(null); setErr(null); },
    onSuccess: (r) => setJobId(r.job_id),
    onError: (e: Error) => setErr(e.message),
  });
  const busy = go.isPending || !!jobId;
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-5">
        {caps.data?.webgl === false && (
          <Alert tone="warn">3D rendering isn't available on this server (WebGL could not start), so 3D requests fall back to a 2D layout.</Alert>
        )}
        <Card>
          <h2 className="mb-3 font-semibold">Scene</h2>
          {tq.isLoading ? <Spinner label="Loading scenes…" /> : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {scenes.map((t) => <TemplateCard key={t.name} t={t} size={size} compact selected={scene === t.name} onPick={() => setScene(t.name)} />)}
            </div>
          )}
        </Card>
        <Card>
          <h2 className="mb-3 font-semibold">Output</h2>
          <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Output">
            {OUTPUTS.map((o) => { const Icon = o.icon; const on = output === o.id; return (
              <button key={o.id} type="button" role="radio" aria-checked={on} onClick={() => setOutput(o.id)}
                className={`rounded-lg border px-3 py-2.5 text-left ${on ? "border-primary-500 bg-brand-tint" : "border-line hover:border-brand/40"}`}>
                <span className="flex items-center gap-2 text-sm font-semibold"><Icon size={16} className={on ? "text-brand" : "text-muted"} />{o.label}</span>
                <span className="mt-0.5 block text-xs text-muted">{o.hint}</span>
              </button>); })}
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Format"><Select value={size} onChange={(e) => setSize(e.target.value)}>
              {SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
            {scene === "scene3d_logo" && <Field label="3D text" hint="Defaults to your brand name."><Input value={f.text3d} onChange={set("text3d")} maxLength={22} /></Field>}
          </div>
          {scene === "scene3d_model" && (
            <div className="mt-4 rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-end gap-3">
                <div className="min-w-[220px] flex-1">
                  <Field label="Model" hint="Self-contained .glb (or .gltf with embedded data), up to 50 MB. Draco/KTX2 compression isn't supported.">
                    <Select value={model} onChange={(e) => setModel(e.target.value)} disabled={!models.data?.items.length}>
                      {!models.data?.items.length && <option value="">No models yet</option>}
                      {models.data?.items.map((m) => <option key={m.id} value={m.id}>{(m.meta?.filename as string) || m.id.slice(0, 8)}</option>)}
                    </Select>
                  </Field>
                </div>
                <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={up.isPending}><Upload size={15} />{up.isPending ? "Uploading…" : "Upload .glb"}</Button>
                <input ref={fileRef} type="file" hidden accept=".glb,.gltf" onChange={(e) => { const x = e.target.files?.[0]; if (x) up.mutate(x); e.target.value = ""; }} />
              </div>
            </div>
          )}
        </Card>
        <Card>
          <h2 className="mb-3 font-semibold">Text on top (optional)</h2>
          <div className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Small label"><Input value={f.eyebrow} onChange={set("eyebrow")} /></Field>
              <Field label="Headline" hint="Wrap a word in *stars* to highlight it."><Input value={f.headline} onChange={set("headline")} /></Field>
            </div>
            <Field label="Supporting line"><Textarea rows={2} value={f.body} onChange={set("body")} /></Field>
          </div>
        </Card>
        {err && <Alert>{err}</Alert>}
        <Button onClick={() => go.mutate()} disabled={busy || (scene === "scene3d_model" && !model)}><Box size={16} />Render 3D</Button>
      </div>
      <div>
        <Card className="xl:sticky xl:top-4">
          <h2 className="mb-3 font-semibold">Result</h2>
          {jobId ? (
            <div className="py-10 text-center"><Spinner label={job?.progress ?? "Waiting for the worker…"} />
              {output !== "image" && <p className="mt-2 text-xs text-muted">3D clips render frame by frame and take about a minute.</p>}</div>
          ) : result ? (
            <div>
              <div className="flex max-h-[560px] items-center justify-center overflow-hidden rounded-xl bg-slate-100"><AssetPreview asset={result} controls /></div>
              {result.meta?.fallback_from && <p className="mt-2 text-xs text-muted">3D wasn't available, so a 2D layout was used instead.</p>}
              <div className="mt-3 flex gap-2"><Button variant="secondary" onClick={() => setAttach(true)}>Use in a post</Button>
                <a className="inline-flex h-9 items-center rounded-lg px-3 text-sm font-medium text-brand hover:underline" href={`${result.url}&download=1`}>Download</a></div>
            </div>
          ) : <EmptyState title="Nothing rendered yet" body="Pick a scene and an output, then choose Render 3D. Results are saved to the library." />}
        </Card>
      </div>
      {attach && result && <UseInPost assetIds={[result.id]} onClose={() => setAttach(false)} />}
    </div>
  );
}
