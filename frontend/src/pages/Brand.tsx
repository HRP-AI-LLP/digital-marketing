import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Sparkles, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea, Toggle,
} from "../components/ui";
import { usePillars, type Campaign, type Pillar } from "./content/shared";
import BrandLook from "./content/BrandLook";

type Brand = {
  voice: string | null; audience: string | null; do_list: string[]; dont_list: string[]; hashtags: string[];
  ctas: string[]; banned_words: string[]; disclaimer: string | null; language: string; emoji_policy: "none" | "light" | "rich";
  blackout_dates: string[];
};

const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);
const commas = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

const LANGUAGES: [string, string][] = [
  ["en", "English"], ["en-IN", "English (India)"], ["hi", "Hindi"], ["hinglish", "Hinglish"], ["mr", "Marathi"],
  ["ta", "Tamil"], ["te", "Telugu"], ["bn", "Bengali"], ["gu", "Gujarati"], ["kn", "Kannada"],
];

// ------------------------------------------------------------------ voice and rules
function VoiceForm({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const brand = useQuery({ queryKey: ["brand"], queryFn: () => api<Brand>("/content/brand") });
  const [f, setF] = useState<Record<string, string>>({});
  const [blackout, setBlackout] = useState<string[]>([]);
  const [newDate, setNewDate] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const b = brand.data;
    if (!b) return;
    setF({
      voice: b.voice ?? "", audience: b.audience ?? "", do_list: b.do_list.join("\n"), dont_list: b.dont_list.join("\n"),
      banned_words: b.banned_words.join(", "), hashtags: b.hashtags.map((h) => `#${h}`).join(" "), ctas: b.ctas.join("\n"),
      disclaimer: b.disclaimer ?? "", language: b.language, emoji_policy: b.emoji_policy,
    });
    setBlackout(b.blackout_dates);
  }, [brand.data]);

  const save = useMutation({
    mutationFn: () => api<Brand>("/content/brand", {
      method: "PUT",
      json: {
        voice: f.voice, audience: f.audience, do_list: lines(f.do_list), dont_list: lines(f.dont_list),
        banned_words: commas(f.banned_words), hashtags: f.hashtags.split(/[\s,]+/).filter(Boolean), ctas: lines(f.ctas),
        disclaimer: f.disclaimer, language: f.language || "en", emoji_policy: f.emoji_policy, blackout_dates: blackout,
      },
    }),
    onSuccess: (b) => { qc.setQueryData(["brand"], b); setSaved(true); setTimeout(() => setSaved(false), 2500); },
  });

  if (brand.isLoading) return <Spinner label="Loading brand kit…" />;
  if (brand.error) return <Alert>{(brand.error as Error).message}</Alert>;
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  return (
    <form className="space-y-6" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <fieldset disabled={!canEdit} className="space-y-6">
        <Card>
          <h2 className="mb-1 font-semibold">Voice and audience</h2>
          <p className="mb-4 text-sm text-muted">Every post the AI writes follows this. Be specific: describe how you sound and who you're talking to.</p>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="How we sound" hint="For example: confident, practical, warm; explains AI without jargon.">
              <Textarea rows={4} value={f.voice ?? ""} onChange={set("voice")} />
            </Field>
            <Field label="Who we write for" hint="For example: HR heads and founders at Indian companies with 50–2,000 staff.">
              <Textarea rows={4} value={f.audience ?? ""} onChange={set("audience")} />
            </Field>
            <Field label="Always do" hint="One rule per line.">
              <Textarea rows={5} value={f.do_list ?? ""} onChange={set("do_list")} placeholder={"Open with a clear hook\nUse Indian examples"} />
            </Field>
            <Field label="Never do" hint="One rule per line.">
              <Textarea rows={5} value={f.dont_list ?? ""} onChange={set("dont_list")} placeholder={"Make promises about results\nMention competitors by name"} />
            </Field>
          </div>
        </Card>

        <Card>
          <h2 className="mb-4 font-semibold">Words, hashtags and calls to action</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Banned words and phrases" hint="Separate with commas. Posts using them are rewritten automatically.">
              <Textarea rows={3} value={f.banned_words ?? ""} onChange={set("banned_words")} placeholder="synergy, game-changer, revolutionary" />
            </Field>
            <Field label="Brand hashtags" hint="Used when they fit the post and the platform allows hashtags.">
              <Textarea rows={3} value={f.hashtags ?? ""} onChange={set("hashtags")} placeholder="#HRTech #AIforHR" />
            </Field>
            <Field label="Calls to action" hint="One per line. The AI picks one that fits.">
              <Textarea rows={3} value={f.ctas ?? ""} onChange={set("ctas")} placeholder={"Book a free demo at hrpai.in\nComment with your view"} />
            </Field>
            <Field label="Disclaimer" hint="Added to the end of every post when set.">
              <Textarea rows={3} value={f.disclaimer ?? ""} onChange={set("disclaimer")} />
            </Field>
            <Field label="Emojis">
              <Select value={f.emoji_policy ?? "light"} onChange={set("emoji_policy")}>
                <option value="none">None</option>
                <option value="light">A few (up to 3)</option>
                <option value="rich">Use freely</option>
              </Select>
            </Field>
            <Field label="Language">
              <Select value={f.language ?? "en"} onChange={set("language")}>
                {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                {f.language && !LANGUAGES.some(([v]) => v === f.language) && <option value={f.language}>{f.language}</option>}
              </Select>
            </Field>
          </div>
        </Card>

        <Card>
          <h2 className="mb-1 font-semibold">Days without posts</h2>
          <p className="mb-4 text-sm text-muted">No posts are planned on these dates, for example national holidays or company events.</p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Date"><Input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} /></Field>
            <Button type="button" variant="secondary" disabled={!newDate || blackout.includes(newDate)}
              onClick={() => { setBlackout([...blackout, newDate].sort()); setNewDate(""); }}>
              <Plus size={15} /> Add date
            </Button>
          </div>
          {blackout.length > 0 ? (
            <ul className="mt-3 flex flex-wrap gap-2">
              {blackout.map((d) => (
                <li key={d} className="inline-flex items-center gap-1 rounded-full border border-line px-2.5 py-1 text-sm">
                  {new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date(`${d}T00:00:00`))}
                  {canEdit && (
                    <button type="button" aria-label={`Remove ${d}`} onClick={() => setBlackout(blackout.filter((x) => x !== d))} className="text-muted hover:text-danger">
                      <X size={14} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : <p className="mt-3 text-sm text-muted">No dates added.</p>}
        </Card>
      </fieldset>
      {save.error && <Alert>{(save.error as Error).message}</Alert>}
      {canEdit && (
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save brand kit"}</Button>
          {saved && <span className="text-sm text-emerald-700">Saved.</span>}
        </div>
      )}
    </form>
  );
}

// ------------------------------------------------------------------ pillars
function PillarRow({ p, share, canEdit }: { p: Pillar; share: number; canEdit: boolean }) {
  const qc = useQueryClient();
  const [d, setD] = useState(p);
  useEffect(() => setD(p), [p]);
  const dirty = d.name !== p.name || d.description !== p.description || d.weight !== p.weight || d.color !== p.color || d.active !== p.active;
  const refresh = () => qc.invalidateQueries({ queryKey: ["pillars"] });
  const save = useMutation({
    mutationFn: () => api(`/content/pillars/${p.id}`, { method: "PATCH", json: { name: d.name, description: d.description, weight: d.weight, color: d.color, active: d.active } }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => api(`/content/pillars/${p.id}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <li className="grid gap-3 py-4 md:grid-cols-[auto_1fr_220px_auto] md:items-start">
      <input type="color" aria-label={`Colour for ${p.name}`} disabled={!canEdit} value={d.color}
        onChange={(e) => setD({ ...d, color: e.target.value })} className="h-9 w-9 cursor-pointer rounded border border-line bg-surface" />
      <div className="space-y-2">
        <Input aria-label="Pillar name" disabled={!canEdit} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
        <Input aria-label="What it covers" disabled={!canEdit} placeholder="What this pillar covers" value={d.description ?? ""} onChange={(e) => setD({ ...d, description: e.target.value })} />
      </div>
      <div>
        <label className="flex items-center justify-between text-sm">
          <span>Weight {d.weight}</span>
          <span className="text-muted">{d.active ? `${Math.round(share * 100)}% of posts` : "Off"}</span>
        </label>
        <input type="range" min={0} max={100} step={5} disabled={!canEdit} value={d.weight}
          onChange={(e) => setD({ ...d, weight: Number(e.target.value) })} className="mt-2 w-full accent-[rgb(var(--brand))]" />
        <div className="mt-2"><Toggle checked={d.active} disabled={!canEdit} onChange={(v) => setD({ ...d, active: v })} label="Use this pillar" /></div>
      </div>
      {canEdit && (
        <div className="flex gap-1">
          {dirty && <Button onClick={() => save.mutate()} disabled={save.isPending || !d.name.trim()}>Save</Button>}
          <Button variant="ghost" aria-label={`Delete ${p.name}`} onClick={() => { if (confirm(`Delete the pillar “${p.name}”? Existing posts keep their text.`)) remove.mutate(); }}>
            <Trash2 size={15} />
          </Button>
        </div>
      )}
      {(save.error || remove.error) && <div className="md:col-span-4"><Alert>{((save.error || remove.error) as Error).message}</Alert></div>}
    </li>
  );
}

function Pillars({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const pillars = usePillars();
  const [adding, setAdding] = useState(false);
  const [n, setN] = useState({ name: "", description: "", weight: 20, color: "#0f8dde" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["pillars"] });
  const seed = useMutation({ mutationFn: () => api<Pillar[]>("/content/pillars/seed-defaults", { method: "POST" }), onSuccess: refresh });
  const add = useMutation({
    mutationFn: () => api("/content/pillars", { method: "POST", json: n }),
    onSuccess: () => { refresh(); setAdding(false); setN({ name: "", description: "", weight: 20, color: "#0f8dde" }); },
  });
  const list = pillars.data ?? [];
  const total = list.filter((p) => p.active).reduce((s, p) => s + p.weight, 0) || 1;

  return (
    <div className="space-y-4">
      <p className="max-w-2xl text-sm text-muted">
        Pillars are the themes you post about. The planner keeps each account's posts in line with these weights over the last 30 days,
        so a pillar with weight 30 gets about twice as many posts as one with weight 15.
      </p>
      {pillars.error && <Alert>{(pillars.error as Error).message}</Alert>}
      {seed.error && <Alert>{(seed.error as Error).message}</Alert>}
      {pillars.isLoading ? <Spinner label="Loading pillars…" /> : list.length === 0 ? (
        <EmptyState title="No topic pillars yet" body="Start with five common B2B pillars and adjust them, or add your own."
          action={canEdit && <Button onClick={() => seed.mutate()} disabled={seed.isPending}><Sparkles size={15} /> Add suggested pillars</Button>} />
      ) : (
        <Card className="py-1">
          <ul className="divide-y divide-line">
            {list.map((p) => <PillarRow key={p.id} p={p} share={p.weight / total} canEdit={canEdit} />)}
          </ul>
        </Card>
      )}
      {canEdit && list.length > 0 && (
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setAdding(true)}><Plus size={15} /> Add pillar</Button>
          <Button variant="ghost" onClick={() => seed.mutate()} disabled={seed.isPending}>Add any missing suggested pillars</Button>
        </div>
      )}
      {adding && (
        <Modal title="Add topic pillar" onClose={() => setAdding(false)}>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            {add.error && <Alert>{(add.error as Error).message}</Alert>}
            <Field label="Name"><Input required value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} placeholder="Product & features" /></Field>
            <Field label="What it covers"><Textarea rows={2} value={n.description} onChange={(e) => setN({ ...n, description: e.target.value })} /></Field>
            <div className="grid grid-cols-[1fr_auto] gap-4">
              <Field label={`Weight ${n.weight}`}>
                <input type="range" min={0} max={100} step={5} value={n.weight} onChange={(e) => setN({ ...n, weight: Number(e.target.value) })} className="w-full accent-[rgb(var(--brand))]" />
              </Field>
              <Field label="Colour"><input type="color" value={n.color} onChange={(e) => setN({ ...n, color: e.target.value })} className="h-10 w-12 rounded border border-line" /></Field>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
              <Button type="submit" disabled={add.isPending}>Add pillar</Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ campaigns
function CampaignForm({ existing, onClose }: { existing?: Campaign; onClose: () => void }) {
  const qc = useQueryClient();
  const [c, setC] = useState({
    name: existing?.name ?? "", brief: existing?.brief ?? "", starts_on: existing?.starts_on ?? "", ends_on: existing?.ends_on ?? "",
    boost: existing?.boost ?? 50, active: existing?.active ?? true,
  });
  const save = useMutation({
    mutationFn: () => {
      const json = { ...c, starts_on: c.starts_on || null, ends_on: c.ends_on || null };
      return existing ? api(`/content/campaigns/${existing.id}`, { method: "PATCH", json }) : api("/content/campaigns", { method: "POST", json });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["campaigns"] }); onClose(); },
  });
  return (
    <Modal title={existing ? `Edit ${existing.name}` : "Add campaign"} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Field label="Name"><Input required value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} placeholder="Diwali offer, Product launch, HR Summit 2026" /></Field>
        <Field label="Brief" hint="What to say, the offer or event details, and any link. The AI uses this for campaign posts.">
          <Textarea rows={4} value={c.brief} onChange={(e) => setC({ ...c, brief: e.target.value })} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Starts on"><Input type="date" value={c.starts_on} onChange={(e) => setC({ ...c, starts_on: e.target.value })} /></Field>
          <Field label="Ends on"><Input type="date" value={c.ends_on} min={c.starts_on || undefined} onChange={(e) => setC({ ...c, ends_on: e.target.value })} /></Field>
        </div>
        <Field label={`Share of posts during the campaign: ${c.boost}%`} hint="The rest of the slots follow your normal pillars.">
          <input type="range" min={0} max={100} step={5} value={c.boost} onChange={(e) => setC({ ...c, boost: Number(e.target.value) })} className="w-full accent-[rgb(var(--brand))]" />
        </Field>
        <Toggle checked={c.active} onChange={(v) => setC({ ...c, active: v })} label="Campaign is on" />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>{existing ? "Save changes" : "Add campaign"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function Campaigns({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => api<Campaign[]>("/content/campaigns") });
  const [editing, setEditing] = useState<Campaign | "new" | null>(null);
  const remove = useMutation({ mutationFn: (id: string) => api(`/content/campaigns/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }) });
  const today = new Date().toISOString().slice(0, 10);
  const list = campaigns.data ?? [];
  const range = (c: Campaign) => {
    const f = (d: string) => new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date(`${d}T00:00:00`));
    if (c.starts_on && c.ends_on) return `${f(c.starts_on)} – ${f(c.ends_on)}`;
    if (c.starts_on) return `From ${f(c.starts_on)}`;
    if (c.ends_on) return `Until ${f(c.ends_on)}`;
    return "No end date";
  };
  return (
    <div className="space-y-4">
      <p className="max-w-2xl text-sm text-muted">Campaigns take over a share of posting slots between their dates, for launches, festivals and events.</p>
      {campaigns.error && <Alert>{(campaigns.error as Error).message}</Alert>}
      {campaigns.isLoading ? <Spinner /> : list.length === 0 ? (
        <EmptyState title="No campaigns" body="Add one when you have a launch, offer or event coming up."
          action={canEdit && <Button onClick={() => setEditing("new")}><Plus size={15} /> Add campaign</Button>} />
      ) : (
        <Card className="py-1">
          <ul className="divide-y divide-line">
            {list.map((c) => {
              const live = c.active && (!c.starts_on || c.starts_on <= today) && (!c.ends_on || c.ends_on >= today);
              return (
                <li key={c.id} className="flex flex-wrap items-start justify-between gap-3 py-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{c.name}</span>
                      {live ? <Badge tone="brand">Running</Badge> : !c.active ? <Badge>Off</Badge> : null}
                    </div>
                    <div className="text-sm text-muted">{range(c)} · {c.boost}% of posts</div>
                    {c.brief && <p className="mt-1 line-clamp-2 max-w-2xl text-sm">{c.brief}</p>}
                  </div>
                  {canEdit && (
                    <div className="flex gap-1">
                      <Button variant="ghost" onClick={() => setEditing(c)}>Edit</Button>
                      <Button variant="ghost" aria-label={`Delete ${c.name}`} onClick={() => { if (confirm(`Delete the campaign “${c.name}”?`)) remove.mutate(c.id); }}><Trash2 size={15} /></Button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      {canEdit && list.length > 0 && <Button variant="secondary" onClick={() => setEditing("new")}><Plus size={15} /> Add campaign</Button>}
      {editing && <CampaignForm existing={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

export default function Brand() {
  const { can } = useAuth();
  const canEdit = can("content.create");
  const [tab, setTab] = useState<"voice" | "look" | "pillars" | "campaigns">("voice");
  return (
    <div>
      <PageHeader title="Brand and topics" description="How your posts sound, what they talk about, and when to stay quiet." />
      {!canEdit && <div className="mb-4"><Alert tone="info">You can view these settings. Ask an administrator for the “Create, edit and regenerate posts” permission to change them.</Alert></div>}
      <Tabs value={tab} onChange={setTab} items={[
        { id: "voice", label: "Voice and rules" }, { id: "look", label: "Look" }, { id: "pillars", label: "Topic pillars" }, { id: "campaigns", label: "Campaigns" },
      ]} />
      {tab === "voice" && <VoiceForm canEdit={canEdit} />}
      {tab === "look" && <BrandLook canEdit={canEdit} />}
      {tab === "pillars" && <Pillars canEdit={canEdit} />}
      {tab === "campaigns" && <Campaigns canEdit={canEdit} />}
    </div>
  );
}
