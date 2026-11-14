// Brand → Visuals: what generated images and videos may show (people), real product demos for posts about a
// product, and how often posts cover current tech / AI news.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Images, Plus, ShieldCheck, Sparkles, Trash2, UserX, Users } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Button, Card, Field, Input } from "../../components/ui";

type Demo = {
  product: string; keywords: string[]; urls: string[]; asset_ids: string[];
  login_url?: string; username?: string; has_password?: boolean; explore?: boolean;
  screens?: number; recordings?: number; captured_at?: string | null;
};
type Visuals = { people: "none" | "team" | "allow"; sources: "team_stock" | "mixed"; news_share: number; demos: Demo[]; team_photos?: number };
type Row = {
  product: string; keywords: string; urls: string; asset_ids: string[]; screens?: number; recordings?: number; captured_at?: string | null;
  signin: boolean; login_url: string; username: string; password: string; has_password?: boolean; explore: boolean;
};

const PEOPLE: { id: Visuals["people"]; title: string; body: string; icon: typeof UserX }[] = [
  { id: "none", title: "No people", icon: UserX,
    body: "Images and videos never show people or faces. AI images are told not to draw them, stock searches leave them out, and every photo and clip is checked for faces before it's used." },
  { id: "team", title: "Only our team", icon: Users,
    body: "No stock or invented people. When a scene needs people, a new AI photo of one of your team members is created from their team photo: same face, new pose, clothes and setting. Their uploaded photo is never posted itself." },
  { id: "allow", title: "Allow people", icon: Users,
    body: "Stock photos and clips may show people. AI images never copy a real person's likeness." },
];

const SOURCES: { id: Visuals["sources"]; title: string; body: string; icon: typeof Images }[] = [
  { id: "team_stock", title: "Team photos and stock only", icon: ShieldCheck,
    body: "Images and video scenes come only from your team (new photos made from their team photos), stock photos and clips (checked: no people), your product demos and brand graphics. Nothing is invented by AI, so no realistic person other than your team can appear." },
  { id: "mixed", title: "Also AI-generated images", icon: Sparkles,
    body: "AI may also create images and clips for scenes (they're told not to show people and checked for faces). More variety, but they're invented pictures." },
];

const toRow = (d: Demo): Row => ({
  ...d, keywords: d.keywords.join(", "), urls: d.urls.join("\n"), signin: !!d.login_url, login_url: d.login_url ?? "",
  username: d.username ?? "", password: "", explore: d.explore ?? true,
});
const split = (s: string, by: RegExp) => s.split(by).map((x) => x.trim()).filter(Boolean);

export default function BrandVisuals({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brand-visuals"], queryFn: () => api<Visuals>("/content/brand/visuals") });
  const [people, setPeople] = useState<Visuals["people"]>("none");
  const [sources, setSources] = useState<Visuals["sources"]>("mixed");
  const [share, setShare] = useState(20);
  const [rows, setRows] = useState<Row[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!q.data) return;
    setPeople(q.data.people); setSources(q.data.sources ?? "mixed"); setShare(q.data.news_share); setRows(q.data.demos.map(toRow));
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => api<Visuals>("/content/brand/visuals", {
      method: "PUT",
      json: {
        people, sources, news_share: share,
        demos: rows.filter((r) => r.product.trim()).map((r) => ({
          product: r.product.trim(), keywords: split(r.keywords, /[,\n]/), urls: split(r.urls, /[\s,]+/), asset_ids: r.asset_ids,
          login_url: r.signin ? r.login_url.trim() || null : null, username: r.signin ? r.username.trim() || null : null,
          password: r.signin && r.password ? r.password : null, clear_login: !r.signin, explore: r.explore,
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
        <h3 className="flex items-center gap-2 font-semibold"><Images size={17} /> Where images and video scenes come from</h3>
        <p className="mt-1 text-sm text-muted">The default for every post the studio makes.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {SOURCES.map((o) => (
            <button key={o.id} type="button" disabled={!canEdit}
              onClick={() => { setSources(o.id); if (o.id === "team_stock" && people === "allow") setPeople("team"); }}
              className={`rounded-xl border p-4 text-left transition ${sources === o.id ? "border-brand bg-brand/5 ring-1 ring-brand" : "border-line hover:border-ink/30"}`}>
              <div className="flex items-center gap-2 font-semibold"><o.icon size={16} /> {o.title}{o.id === "team_stock" && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">Recommended</span>}</div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{o.body}</p>
            </button>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="font-semibold">People in images and videos</h3>
        <p className="mt-1 text-sm text-muted">Stock photos and AI images of people can raise questions about consent and likeness. Choose what posts may show.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {PEOPLE.map((p) => (
            <button key={p.id} type="button" disabled={!canEdit || (sources === "team_stock" && p.id === "allow")}
              title={sources === "team_stock" && p.id === "allow" ? "“Team photos and stock only” never shows anyone but your team" : undefined}
              onClick={() => setPeople(p.id)}
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
            <span className="mt-1 block text-muted">
              Needs an image service that works from a reference photo (Google Gemini image or OpenAI GPT Image in Integrations);
              without one, those scenes show no people. Get each person's written consent to create images of them, and
              label posts with these photos as AI-generated where the platform asks (YouTube, Instagram and Facebook do).
            </span>
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
          {canEdit && <Button variant="secondary" onClick={() => setRows((r) => [...r, { product: "", keywords: "", urls: "", asset_ids: [], signin: false, login_url: "", username: "", password: "", explore: true }])}><Plus size={15} /> Add a product</Button>}
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
                <Field label="Pages that show it (one per line, up to 6)"
                  hint={r.signin ? "Optional with a sign-in: pages inside the app to capture. Leave empty to start from where the sign-in lands." : "Public pages: the studio opens them like a visitor would."}>
                  <textarea rows={2} value={r.urls} disabled={!canEdit} placeholder="https://hrpai.in/products/hrms"
                    onChange={(e) => set(i, { urls: e.target.value })}
                    className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm focus:border-brand focus:outline-none" />
                </Field>
              </div>
              <label className="mt-3 flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={r.signin} disabled={!canEdit}
                  onChange={(e) => set(i, { signin: e.target.checked })} />
                <span className="font-medium">The demo needs a sign-in</span>
              </label>
              {r.signin && (
                <div className="mt-2 space-y-3 rounded-lg bg-ink/[0.03] p-3">
                  <div className="grid gap-3 md:grid-cols-3">
                    <Field label="Sign-in page">
                      <Input value={r.login_url} disabled={!canEdit} placeholder="https://demo.hrpai.in/login" onChange={(e) => set(i, { login_url: e.target.value })} />
                    </Field>
                    <Field label="Demo username or email">
                      <Input value={r.username} disabled={!canEdit} autoComplete="off" onChange={(e) => set(i, { username: e.target.value })} />
                    </Field>
                    <Field label="Demo password" hint={r.has_password ? "Saved. Type a new one to change it." : undefined}>
                      <Input type="password" value={r.password} disabled={!canEdit} autoComplete="new-password"
                        placeholder={r.has_password ? "••••••••" : ""} onChange={(e) => set(i, { password: e.target.value })} />
                    </Field>
                  </div>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-[rgb(var(--brand))]" checked={r.explore} disabled={!canEdit}
                      onChange={(e) => set(i, { explore: e.target.checked })} />
                    Also capture the main pages in the app's menu (up to 6 pages)
                  </label>
                  <p className="text-xs text-muted">
                    Use a demo account with sample data only: these screens are posted publicly. The password is stored encrypted and never shown again.
                    The studio only signs in and opens pages; it never clicks buttons or changes anything. Sign-ins that need a one-time code or a CAPTCHA can't be automated.
                  </p>
                </div>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">
                <span>{r.screens ?? 0} screenshots · {r.recordings ?? 0} recordings{r.captured_at ? ` · captured ${new Date(r.captured_at).toLocaleString()}` : ""}</span>
                <span className="ml-auto flex gap-2">
                  {canEdit && q.data?.demos.some((d) => d.product === r.product && (d.urls.length || d.login_url)) && (
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
