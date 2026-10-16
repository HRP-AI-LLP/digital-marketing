import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { api } from "../../api/client";
import { useJob, type Job } from "../../api/hooks";
import { Alert, Button, Card, EmptyState, Field, Select, Spinner, Textarea, Toggle } from "../../components/ui";
import { AssetGrid, AssetDetail } from "./Library";
import { ProviderHint, useCaps, useTemplates, type Asset } from "./shared";

const AI_SIZES = [
  ["1024x1024", "Square 1:1"], ["1080x1350", "Portrait 4:5"], ["1080x1920", "Story 9:16"],
  ["1200x675", "Landscape 16:9"], ["1000x1500", "Pinterest 2:3"],
];

export default function AiImage() {
  const qc = useQueryClient();
  const caps = useCaps();
  const templates = useTemplates();
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState("1080x1350");
  const [enhance, setEnhance] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [results, setResults] = useState<Asset[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Asset | null>(null);

  const onDone = async (j: Job) => {
    setJobId(null);
    if (j.status !== "done") { setErr(j.error ?? "The image couldn't be generated."); return; }
    const ids = (j.result?.asset_ids as string[]) ?? [];
    const got = await Promise.all(ids.map((id) => api<Asset>(`/media/${id}`)));
    setResults((r) => [...got, ...r]);
    qc.invalidateQueries({ queryKey: ["media"] });
  };
  const job = useJob(jobId, onDone);
  const start = useMutation({
    mutationFn: () => api<{ job_id: string }>("/media/ai-image", { method: "POST", json: { prompt, size, enhance } }),
    onMutate: () => setErr(null),
    onSuccess: (r) => setJobId(r.job_id),
  });

  if (caps.isLoading || templates.isLoading) return <Spinner label="Loading…" />;
  if (caps.data && !caps.data.image) return <ProviderHint what="image" />;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <Card className="h-fit space-y-4">
        <Field label="Describe the image" hint="Subject, setting, light and mood. Leave out words and logos: headlines are added with templates.">
          <Textarea rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)}
            placeholder="A diverse HR team reviewing candidate profiles on a large screen, warm morning light, modern Indian office" />
        </Field>
        <Field label="Shape">
          <Select value={size} onChange={(e) => setSize(e.target.value)}>
            {AI_SIZES.map(([v, l]) => <option key={v} value={v}>{l} ({v.replace("x", " × ")})</option>)}
          </Select>
        </Field>
        <Toggle checked={enhance} onChange={setEnhance} label="Improve my description with AI first" />
        {(start.error || err) && <Alert>{(start.error as Error | null)?.message ?? err}</Alert>}
        <Button onClick={() => start.mutate()} disabled={prompt.trim().length < 3 || start.isPending || !!jobId}>
          <Sparkles size={16} />Generate image
        </Button>
        <p className="text-xs text-muted">Uses your image provider from Settings → Integrations. Local models (ComfyUI, Stable Diffusion) run one at a time on the GPU.</p>
      </Card>
      <div>
        {jobId && (
          <Card className="mb-4 text-center"><Spinner label={job?.progress ?? "Generating…"} /></Card>
        )}
        {results.length ? <AssetGrid items={results} onOpen={setOpen} />
          : !jobId && <EmptyState title="Your images appear here" body="Every image is saved to the library, ready to use as a background in templates." />}
      </div>
      {open && <AssetDetail asset={open} onClose={() => setOpen(null)} onChanged={() => qc.invalidateQueries({ queryKey: ["media"] })} />}
    </div>
  );
}
