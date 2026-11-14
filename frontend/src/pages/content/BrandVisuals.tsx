// Brand → Visuals: what generated images and videos may show (people), real product demos for posts about a
// product, and how often posts cover current tech / AI news.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Plus, Trash2, UserX, Users } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Button, Card, Field, Input } from "../../components/ui";

type Demo = {
  product: string; keywords: string[]; urls: string[]; asset_ids: string[];
  screens?: number; recordings?: number; captured_at?: string | null;
};
type Visuals = { people: "none" | "team" | "allow"; news_share: number; demos: Demo[]; team_photos?: number };
type Row = { product: string; keywords: string; urls: string; asset_ids: string[]; screens?: number; recordings?: number; captured_at?: string | null };

const PEOPLE: { id: Visuals["people"]; title: string; body: string; icon: typeof UserX }[] = [
  { id: "none", title: "No people", icon: UserX,
    body: "Images and videos never show people or faces. AI images are told not to draw them, stock searches leave them out, and every photo and clip is checked for faces before it's used." },
  { id: "team", title: "Only our team photos", icon: Users,
    body: "Still no stock or AI people. When a scene needs people, one of your own team photos is used (mark them “Team photo” in the Media library; use them only with each person's consent)." },
  { id: "allow", title: "Allow people", icon: Users,
    body: "Stock photos and clips may show people. AI images never copy a real person's likeness." },
];

const toRow = (d: Demo): Row => ({ ...d, keywords: d.keywords.join(", "), urls: d.urls.join("\n") });
const split = (s: string, by: RegExp) => s.split(by).map((x) => x.trim()).filter(Boolean);

export default function BrandVisuals({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brand-visuals"], queryFn: () => api<Visuals>("/content/brand/visuals") });
  const [people, setPeople] = useState<Visuals["people"]>("none");
  const [share, setShare] = useState(20);
  const [rows, setRows] = useState<Row[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!q.data) return;
    setPeople(q.data.people); setShare(q.data.news_share); setRows(q.data.demos.map(toRow));
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => api<Visuals>("/content/brand/visuals", {
      method: "PUT",
      json: {
        people, news_share: share,
        demos: rows.filter((r) => r.product.trim()).map((r) => ({
          product: r.product.trim(), keywords: split(r.keywords, /[,\n]/), urls: split(r.urls, /[\s,]+/), asset_ids: r.asset_ids,
        })),
      },
    }),
    onSuccess: (d) => {
      qc.setQueryData(["brand-visuals"], d);
      setNotice("Saved. New or changed demo pages are being captured now (a minute or two each).");
    },
  });
  const capture = useMutation({
    mutationFn: (product: string) => api<{ message: string }>("/content/brand/demos/capture", { method: "POST", json: { product } }),
    onSuccess: (r) => { setNotice(r.message); setTimeout(() => qc.invalidateQueries({ queryKey: ["brand-visuals"] }), 90_000); },
  });
  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const err = (save.error || capture.error || q.error) as Error | null;

  return (
    <div className="max-w-4xl space-y-6">
      {notice && <Alert tone="info">{notice}</Alert>}
      {err && <Alert>{err.message}</Alert>}

      <Card>
        <h3 className="font-semibold">People in images and videos</h3>
        <p className="mt-1 text-sm text-muted">Stock photos and AI images of people can raise questions about consent and likeness. Choose what posts may show.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {PEOPLE.map((p) => (
            <button key={p.id} type="button" disabled={!canEdit} onClick={() => setPeople(p.id)}
              className={`rounded-xl border p-4 text-left transition ${people === p.id ? "border-brand bg-brand/5 ring-1 ring-brand" : "border-line hover:border-ink/30"}`}>
              <div className="flex items-center gap-2 font-semibold"><p.icon size={16} /> {p.title}</div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{p.body}</p>
            </button>
          ))}
        </div>
        {people === "team" && (
          <p className="mt-3 text-sm">
            {q.data?.team_photos ? `${q.data.team_photos} team photo${q.data.team_photos === 1 ? "" : "s"} marked.` : "No team photos marked yet."}{" "}
            Upload them in the <Link to="/media" className="text-brand underline">Media library</Link>, open each photo and tick “Team photo”.
          </p>
        )}
      </Card>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold">Product demos</h3>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              Posts about one of your products show it working: Shorts and Reels use a scroll-through recording and screenshots of
              its pages, and image posts show a screenshot in a laptop or phone frame. Add the public pages that show the product
              (product page, live demo). Your own screen recordings work too: upload them to the Media library.
            </p>
          </div>
          {canEdit && <Button variant="secondary" onClick={() => setRows((r) => [...r, { product: "", keywords: "", urls: "", asset_ids: [] }])}><Plus size={15} /> Add a product</Button>}
        </div>
        {rows.length === 0 && <p className="mt-4 text-sm text-muted">No products yet. Example: “HRP HRMS” with words “HRMS, payroll, attendance” and https://hrpai.in/products/hrms.</p>}
        <div className="mt-4 space-y-4">
          {rows.map((r, i) => (
            <div key={i} className="rounded-xl border border-line p-4">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Product name">
                  <Input value={r.product} disabled={!canEdit} placeholder="HRP HRMS" onChange={(e) => set(i, { product: e.target.value })} />
                </Field>
                <Field label="Words that mean a post is about it" hint="Comma separated, e.g. HRMS, payroll, attendance">
                  <Input value={r.keywords} disabled={!canEdit} placeholder="HRMS, payroll, attendance" onChange={(e) => set(i, { keywords: e.target.value })} />
                </Field>
              </div>
              <div className="mt-3">
                <Field label="Pages that show it (one per line, up to 6)" hint="Public pages only: the studio opens them like a visitor would.">
                  <textarea rows={2} value={r.urls} disabled={!canEdit} placeholder="https://hrpai.in/products/hrms"
                    onChange={(e) => set(i, { urls: e.target.value })}
                    className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm focus:border-brand focus:outline-none" />
                </Field>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">
                <span>{r.screens ?? 0} screenshots · {r.recordings ?? 0} recordings{r.captured_at ? ` · captured ${new Date(r.captured_at).toLocaleString()}` : ""}</span>
                <span className="ml-auto flex gap-2">
                  {canEdit && q.data?.demos.some((d) => d.product === r.product && d.urls.length) && (
                    <Button variant="secondary" onClick={() => capture.mutate(r.product)} disabled={capture.isPending}><Camera size={14} /> Capture again</Button>
                  )}
                  {canEdit && <Button variant="ghost" onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}><Trash2 size={14} /> Remove</Button>}
                </span>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="font-semibold">Tech and AI news</h3>
        <p className="mt-1 text-sm text-muted">
          Some posts share a current technology or AI industry update from the daily research (with its source), plus your take.
          Their images and videos show the technology itself.
        </p>
        <div className="mt-4 flex items-center gap-4">
          <input type="range" min={0} max={60} step={5} value={share} disabled={!canEdit} onChange={(e) => setShare(Number(e.target.value))}
            className="w-64 accent-[rgb(var(--brand))]" aria-label="Share of posts about tech news" />
          <span className="text-sm font-medium">{share === 0 ? "Off" : `About ${share} in 100 posts`}</span>
        </div>
      </Card>

      {canEdit && <Button onClick={() => { setNotice(null); save.mutate(); }} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save visuals"}</Button>}
    </div>
  );
}
