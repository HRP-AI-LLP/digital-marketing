// Academy: free IT courses (C, Python, ML, networking …) for people outside IT, made lesson by lesson, by hand.
// A course gets an AI-written outline (modules → lessons). "Make lesson" writes that lesson's Shorts, full lesson
// videos and carousels in English and Hindi as drafts; you review and publish them from Posts.
import { useMemo, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, GraduationCap, Pencil, Plus, RefreshCw, Sparkles, Trash2, Wand2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, StatusBadge, Textarea } from "../components/ui";

type CatalogItem = { id: string; title: string; topic: string };
type Account = { id: string; platform: string; name: string; status: string; formats: string[] };
type Catalog = {
  groups: { name: string; items: CatalogItem[] }[];
  languages: { id: string; name: string; voice: string }[];
  formats: { id: string; label: string; youtube_only: boolean }[];
  levels: string[];
  accounts: Account[];
};
type Lesson = { n: number; module_no: number; module: string; title: string; summary: string; made_at: string | null; posts: Record<string, number> };
type Course = {
  id: string; title: string; catalog_id: string | null; topic: string; audience: string; level: string; status: string;
  languages: string[]; formats: Record<string, boolean>; account_ids: Record<string, string[]>; next_lesson: number;
  lessons_total: number; modules_total: number; lessons_made: number; lessons_published: number;
  outline_status: string | null; outline_error: string | null; created_at: string | null;
  outline?: { modules: { title: string; lessons: { title: string; summary: string }[] }[] }; lessons?: Lesson[];
};
type LessonPost = { id: string; platform: string; status: string; post_type: string; format: string; lang: string; title: string | null; error: string | null };

const FORMAT_HINT: Record<string, string> = {
  short: "Under 60 s, vertical: one idea, an everyday example, a few lines of code.",
  lesson: "5-10 minutes, 16:9: slides, code, diagrams, a quiz and 3D scenes. YouTube only.",
  carousel: "Swipeable slides: a summary / cheat sheet of the lesson.",
};
const LANG_NAME: Record<string, string> = { en: "English", hi: "Hindi" };

const useCatalog = () => useQuery({ queryKey: ["academy", "catalog"], queryFn: () => api<Catalog>("/academy/catalog"), staleTime: 60_000 });

// ------------------------------------------------------------------ course form (create / edit)
type FormValue = { title: string; catalog_id: string | null; topic: string; audience: string; level: string; languages: string[];
  formats: Record<string, boolean>; account_ids: Record<string, string[]> };

function CourseForm({ initial, onSubmit, pending, error, submitLabel, showCatalog }: {
  initial: FormValue; onSubmit: (v: FormValue) => void; pending: boolean; error?: string; submitLabel: string; showCatalog: boolean;
}) {
  const cat = useCatalog();
  const [v, setV] = useState<FormValue>(initial);
  const set = (p: Partial<FormValue>) => setV((x) => ({ ...x, ...p }));
  const all = useMemo(() => (cat.data?.groups ?? []).flatMap((g) => g.items), [cat.data]);
  if (!cat.data) return <Spinner />;
  const accountsFor = (f: string) => cat.data.accounts.filter((a) => a.formats.includes(f));
  const toggleAcc = (f: string, id: string) => {
    const cur = v.account_ids[f] ?? [];
    set({ account_ids: { ...v.account_ids, [f]: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] } });
  };
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onSubmit(v); }}>
      {showCatalog && (
        <Field label="Subject" hint="Pick a ready course or choose “My own topic” and describe it.">
          <Select value={v.catalog_id ?? ""} onChange={(e) => {
            const it = all.find((x) => x.id === e.target.value);
            set(it ? { catalog_id: it.id, title: it.title, topic: it.topic } : { catalog_id: null });
          }}>
            <option value="">My own topic</option>
            {cat.data.groups.map((g) => (
              <optgroup key={g.name} label={g.name}>{g.items.map((i) => <option key={i.id} value={i.id}>{i.title}</option>)}</optgroup>
            ))}
          </Select>
        </Field>
      )}
      <Field label="Course name"><Input value={v.title} onChange={(e) => set({ title: e.target.value })} required minLength={2} /></Field>
      <Field label="What it covers" hint="The AI plans the lessons from this, from the very basics to advanced.">
        <Textarea rows={3} value={v.topic} onChange={(e) => set({ topic: e.target.value })} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Who it's for" hint="Leave empty for people with no IT background.">
          <Input value={v.audience} onChange={(e) => set({ audience: e.target.value })} placeholder="Students, working adults, career changers" />
        </Field>
        <Field label="Starts at">
          <Select value={v.level} onChange={(e) => set({ level: e.target.value })}>
            {cat.data.levels.map((l) => <option key={l} value={l}>{l[0].toUpperCase() + l.slice(1)}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Languages" hint="Each lesson is made once per language, as its own post.">
        <div className="flex flex-wrap gap-4">
          {cat.data.languages.map((l) => (
            <label key={l.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={v.languages.includes(l.id)}
                onChange={() => set({ languages: v.languages.includes(l.id) ? v.languages.filter((x) => x !== l.id) : [...v.languages, l.id] })} />
              {l.name}
            </label>
          ))}
        </div>
      </Field>
      <div className="space-y-3">
        <div className="text-sm font-medium">Formats and where each goes</div>
        {cat.data.formats.map((f) => (
          <Card key={f.id} className="p-3">
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={!!v.formats[f.id]} onChange={(e) => set({ formats: { ...v.formats, [f.id]: e.target.checked } })} />
              <span><span className="font-medium">{f.label}</span><span className="block text-muted">{FORMAT_HINT[f.id]}</span></span>
            </label>
            {v.formats[f.id] && (
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-6">
                {accountsFor(f.id).length === 0 && <span className="text-sm text-muted">No connected account can take this format{f.youtube_only ? " (connect YouTube)" : ""}.</span>}
                {accountsFor(f.id).map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={(v.account_ids[f.id] ?? []).includes(a.id)} onChange={() => toggleAcc(f.id, a.id)} />
                    {a.name} <span className="text-muted">({a.platform})</span>
                  </label>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="flex justify-end"><Button disabled={pending || !v.title.trim() || !v.languages.length}>{pending ? "Saving…" : submitLabel}</Button></div>
    </form>
  );
}

// ------------------------------------------------------------------ list
function CourseList() {
  const { can } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const courses = useQuery({ queryKey: ["academy", "courses"], queryFn: () => api<{ items: Course[] }>("/academy/courses") });
  const create = useMutation({
    mutationFn: (v: FormValue) => api<Course>("/academy/courses", { method: "POST", json: v }),
    onSuccess: (c) => { void qc.invalidateQueries({ queryKey: ["academy"] }); nav(`/academy/${c.id}`); },
  });
  return (
    <div className="space-y-6">
      <PageHeader title="Academy" description="Free IT courses for everyone, in simple English and Hindi. Pick a lesson, and its Shorts, full lesson videos and carousels are written for you to review and publish."
        action={can("content.create") && <Button onClick={() => setCreating(true)}><Plus size={16} /> New course</Button>} />
      {courses.isLoading && <Spinner />}
      {courses.data && courses.data.items.length === 0 && (
        <EmptyState title="No courses yet" body="Start with Python, C, networking, machine learning or any topic. The AI plans the full course; you make the lessons one by one."
          action={can("content.create") && <Button onClick={() => setCreating(true)}><GraduationCap size={16} /> Create your first course</Button>} />
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {courses.data?.items.map((c) => (
          <Link key={c.id} to={`/academy/${c.id}`} className="block">
            <Card className="h-full p-4 transition hover:border-brand">
              <div className="flex items-start justify-between gap-2">
                <div className="font-semibold">{c.title}</div>
                <StatusBadge status={c.outline_status === "writing" ? "writing" : c.status} />
              </div>
              <div className="mt-1 line-clamp-2 text-sm text-muted">{c.topic}</div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
                <div className="h-full bg-brand" style={{ width: `${c.lessons_total ? (100 * c.lessons_made) / c.lessons_total : 0}%` }} />
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted">
                <span>{c.lessons_made}/{c.lessons_total || "…"} lessons made</span>
                <span>· {c.languages.map((l) => LANG_NAME[l] ?? l).join(" + ")}</span>
                <span>· {Object.entries(c.formats).filter(([, on]) => on).length} formats</span>
              </div>
            </Card>
          </Link>
        ))}
      </div>
      {creating && (
        <Modal title="New course" onClose={() => setCreating(false)} wide>
          <CourseForm showCatalog submitLabel="Create and plan the lessons" pending={create.isPending} error={(create.error as Error | null)?.message}
            initial={{ title: "", catalog_id: null, topic: "", audience: "", level: "beginner", languages: ["en", "hi"],
              formats: { short: true, lesson: true, carousel: true }, account_ids: {} }}
            onSubmit={(v) => create.mutate(v)} />
        </Modal>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ outline editor (plain text)
const outlineToText = (o: Course["outline"]) =>
  (o?.modules ?? []).map((m) => `# ${m.title}\n` + m.lessons.map((l) => `- ${l.title}${l.summary ? ` | ${l.summary}` : ""}`).join("\n")).join("\n\n");

function textToOutline(t: string) {
  const modules: { title: string; lessons: { title: string; summary: string }[] }[] = [];
  for (const raw of t.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("#")) modules.push({ title: line.replace(/^#+\s*/, ""), lessons: [] });
    else {
      if (!modules.length) modules.push({ title: "Module 1", lessons: [] });
      const [title, ...rest] = line.replace(/^[-*\d.)\s]+/, "").split("|");
      if (title.trim()) modules[modules.length - 1].lessons.push({ title: title.trim(), summary: rest.join("|").trim() });
    }
  }
  return { modules };
}

// ------------------------------------------------------------------ make a lesson
function MakeLesson({ course, lesson, onClose }: { course: Course; lesson: Lesson; onClose: () => void }) {
  const qc = useQueryClient();
  const [formats, setFormats] = useState(Object.entries(course.formats).filter(([, on]) => on).map(([f]) => f));
  const [langs, setLangs] = useState(course.languages);
  const count = formats.reduce((s, f) => s + (course.account_ids[f]?.length ?? 0), 0) * langs.length;
  const make = useMutation({
    mutationFn: () => api<{ count: number }>(`/academy/courses/${course.id}/lessons/${lesson.n}`, { method: "POST", json: { formats, languages: langs } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["academy"] }); },
  });
  return (
    <Modal title={`Lesson ${lesson.n}: ${lesson.title}`} onClose={onClose}>
      {make.isSuccess ? (
        <div className="space-y-3">
          <Alert tone="info">{make.data.count} drafts are being written and their videos and slides made. A full lesson video takes several minutes.
            Nothing is published until you do it: open each post, check it, then schedule or publish.</Alert>
          <div className="flex justify-end gap-2"><Link to="/posts?status=draft"><Button variant="secondary">Open drafts</Button></Link><Button onClick={onClose}>Done</Button></div>
        </div>
      ) : (
        <div className="space-y-4">
          {lesson.summary && <p className="text-sm text-muted">{lesson.summary}</p>}
          {lesson.made_at && <Alert tone="warn">This lesson was already made on {new Date(lesson.made_at).toLocaleString()}. Making it again writes new drafts.</Alert>}
          <Field label="Formats">
            <div className="flex flex-wrap gap-4">
              {Object.entries(course.formats).filter(([, on]) => on).map(([f]) => (
                <label key={f} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={formats.includes(f)} onChange={() => setFormats(formats.includes(f) ? formats.filter((x) => x !== f) : [...formats, f])} />
                  {f === "short" ? "Shorts / Reels" : f === "lesson" ? "Full lesson video" : "Carousel"} ({course.account_ids[f]?.length ?? 0} accounts)
                </label>
              ))}
            </div>
          </Field>
          <Field label="Languages">
            <div className="flex gap-4">
              {course.languages.map((l) => (
                <label key={l} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={langs.includes(l)} onChange={() => setLangs(langs.includes(l) ? langs.filter((x) => x !== l) : [...langs, l])} />
                  {LANG_NAME[l] ?? l}
                </label>
              ))}
            </div>
          </Field>
          {make.error && <Alert>{(make.error as Error).message}</Alert>}
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted">{count} draft{count === 1 ? "" : "s"} will be written.</span>
            <Button onClick={() => make.mutate()} disabled={make.isPending || !count}><Wand2 size={16} /> {make.isPending ? "Starting…" : "Make this lesson"}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function LessonPosts({ courseId, n }: { courseId: string; n: number }) {
  const q = useQuery({ queryKey: ["academy", "lesson", courseId, n], queryFn: () => api<{ posts: LessonPost[] }>(`/academy/courses/${courseId}/lessons/${n}`),
    refetchInterval: (x) => (x.state.data?.posts.some((p) => ["planned", "generating"].includes(p.status)) ? 10_000 : false) });
  if (!q.data) return <Spinner />;
  if (!q.data.posts.length) return <div className="text-sm text-muted">No posts yet.</div>;
  return (
    <ul className="space-y-1 text-sm">
      {q.data.posts.map((p) => (
        <li key={p.id} className="flex flex-wrap items-center gap-2">
          <StatusBadge status={p.status} />
          <Link to={`/posts/${p.id}`} className="hover:underline">
            {p.format === "lesson" ? "Full lesson" : p.format === "short" ? "Short" : "Carousel"} · {p.platform} · {LANG_NAME[p.lang] ?? p.lang}
          </Link>
          {p.error && <span className="text-xs text-danger [overflow-wrap:anywhere]">{p.error.slice(0, 160)}</span>}
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ course page
function CourseView() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [editOutline, setEditOutline] = useState<string | null>(null);
  const [making, setMaking] = useState<Lesson | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const course = useQuery({ queryKey: ["academy", "course", id], queryFn: () => api<Course>(`/academy/courses/${id}`),
    refetchInterval: (q) => (q.state.data?.outline_status === "writing" ? 5000 : false) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["academy"] });
  const patch = useMutation({ mutationFn: (body: object) => api<Course>(`/academy/courses/${id}`, { method: "PATCH", json: body }),
    onSuccess: () => { setEditing(false); setEditOutline(null); void refresh(); } });
  const replan = useMutation({ mutationFn: () => api(`/academy/courses/${id}/outline`, { method: "POST" }), onSuccess: () => void refresh() });
  const del = useMutation({ mutationFn: () => api(`/academy/courses/${id}`, { method: "DELETE" }), onSuccess: () => { void refresh(); nav("/academy"); } });
  const c = course.data;
  if (course.isLoading) return <Spinner />;
  if (!c) return <Alert>{(course.error as Error | null)?.message ?? "Course not found."}</Alert>;
  const lessons = c.lessons ?? [];
  const byModule = lessons.reduce<Record<number, Lesson[]>>((acc, l) => ((acc[l.module_no] ||= []).push(l), acc), {});
  const edit = can("content.create");
  return (
    <div className="space-y-6">
      <Link to="/academy" className="inline-flex items-center gap-1 text-sm text-muted hover:underline"><ArrowLeft size={14} /> All courses</Link>
      <PageHeader title={c.title} description={c.topic}
        action={edit && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Settings</Button>
            <Button variant="ghost" onClick={() => { if (confirm(`Delete “${c.title}”? Posts already made stay in Posts.`)) del.mutate(); }}><Trash2 size={14} /></Button>
          </div>
        )} />
      <div className="flex flex-wrap gap-2 text-sm">
        <Badge tone="brand">{c.lessons_made}/{c.lessons_total} lessons made</Badge>
        <Badge>{c.level}</Badge>
        {c.languages.map((l) => <Badge key={l}>{LANG_NAME[l] ?? l}</Badge>)}
        {Object.entries(c.formats).filter(([, on]) => on).map(([f]) => <Badge key={f} tone="accent">{f === "short" ? "Shorts" : f === "lesson" ? "Full lessons" : "Carousels"} · {c.account_ids[f]?.length ?? 0} accounts</Badge>)}
      </div>

      {c.outline_status === "writing" && <Alert tone="info"><span className="inline-flex items-center gap-2"><Sparkles size={14} /> The AI is planning the lessons. This takes a minute or two.</span></Alert>}
      {c.outline_status === "error" && (
        <Alert>Couldn't plan the course: {c.outline_error} {edit && <Button variant="secondary" className="ml-2" onClick={() => replan.mutate()}>Try again</Button>}</Alert>
      )}

      {lessons.length > 0 && (
        <Card className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold"><BookOpen size={16} className="mr-1 inline" /> Lessons ({c.modules_total} modules)</div>
            {edit && (
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setEditOutline(outlineToText(c.outline))}><Pencil size={14} /> Edit outline</Button>
                <Button variant="ghost" disabled={replan.isPending} onClick={() => { if (confirm("Plan the whole course again? The current outline is replaced.")) replan.mutate(); }}><RefreshCw size={14} /> Plan again</Button>
              </div>
            )}
          </div>
          <div className="space-y-5">
            {Object.entries(byModule).map(([mno, ls]) => (
              <div key={mno}>
                <div className="mb-2 text-sm font-semibold text-muted">Module {mno}: {ls[0].module}</div>
                <ul className="divide-y divide-black/5 dark:divide-white/10">
                  {ls.map((l) => {
                    const made = Object.values(l.posts).reduce((a, b) => a + b, 0);
                    return (
                      <li key={l.n} className="py-2">
                        <div className="flex flex-wrap items-center gap-3">
                          <span className={`w-8 shrink-0 text-right text-sm tabular-nums ${l.n === c.next_lesson ? "font-bold text-brand" : "text-muted"}`}>{l.n}</span>
                          <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setOpen(open === l.n ? null : l.n)}>
                            <div className="font-medium">{l.title}</div>
                            {l.summary && <div className="text-sm text-muted">{l.summary}</div>}
                          </button>
                          {made > 0 && (
                            <span className="flex flex-wrap gap-1">
                              {Object.entries(l.posts).map(([st, k]) => (
                                <Badge key={st} tone={st === "published" ? "brand" : st === "failed" ? "danger" : ["planned", "generating"].includes(st) ? "accent" : "neutral"}>
                                  {k} {st === "planned" || st === "generating" ? "writing" : st.replace(/_/g, " ")}</Badge>
                              ))}
                            </span>
                          )}
                          {edit && <Button variant={l.n === c.next_lesson ? "primary" : "secondary"} className="h-8 px-3 text-sm" onClick={() => setMaking(l)}>
                            <Wand2 size={14} /> {l.made_at ? "Make again" : "Make lesson"}</Button>}
                        </div>
                        {open === l.n && <div className="mt-2 pl-11"><LessonPosts courseId={c.id} n={l.n} /></div>}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      {editing && (
        <Modal title="Course settings" onClose={() => setEditing(false)} wide>
          <CourseForm showCatalog={false} submitLabel="Save" pending={patch.isPending} error={(patch.error as Error | null)?.message}
            initial={{ title: c.title, catalog_id: c.catalog_id, topic: c.topic, audience: c.audience, level: c.level, languages: c.languages,
              formats: c.formats, account_ids: c.account_ids }}
            onSubmit={(v) => patch.mutate({ title: v.title, topic: v.topic, audience: v.audience, level: v.level, languages: v.languages,
              formats: v.formats, account_ids: v.account_ids })} />
        </Modal>
      )}
      {editOutline !== null && (
        <Modal title="Edit outline" onClose={() => setEditOutline(null)} wide>
          <div className="space-y-3">
            <p className="text-sm text-muted">One module per “# ” line, one lesson per “- ” line. Add “| summary” after a lesson title if you like.</p>
            <Textarea rows={20} className="font-mono text-sm" value={editOutline} onChange={(e) => setEditOutline(e.target.value)} />
            {patch.error && <Alert>{(patch.error as Error).message}</Alert>}
            <div className="flex justify-end"><Button disabled={patch.isPending} onClick={() => patch.mutate({ outline: textToOutline(editOutline) })}>Save outline</Button></div>
          </div>
        </Modal>
      )}
      {making && <MakeLesson course={c} lesson={making} onClose={() => setMaking(null)} />}
    </div>
  );
}

export default function Academy() {
  return (
    <Routes>
      <Route index element={<CourseList />} />
      <Route path=":id" element={<CourseView />} />
    </Routes>
  );
}
