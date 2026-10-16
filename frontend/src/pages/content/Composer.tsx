import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Lightbulb, Sparkles, XCircle } from "lucide-react";
import { api } from "../../api/client";
import { useJob } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea } from "../../components/ui";
import {
  CharCounter, caption, fromLocalInput, parseTags, tzLabel, useContentAccounts, useMeta, usePillars, useTz,
  type AccountBrief, type PostDetail,
} from "./shared";

type Made = { post_id: string; job_id: string; account_id: string };
type BriefPrefill = { brief_id: string; title: string; brief: string; link_url: string | null; post_type: string; pillar_id: string | null };

function Progress({ item, account }: { item: Made; account?: AccountBrief }) {
  const job = useJob(item.job_id);
  const status = job?.status;
  return (
    <li className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm"><span className="font-medium">{account?.name ?? "Account"}</span> <span className="text-muted">· {account?.platform_label}</span></span>
      {status === "done" ? (
        <Link to={`/posts/${item.post_id}`} className="inline-flex items-center gap-1 text-sm text-brand hover:underline"><CheckCircle2 size={15} className="text-emerald-600" /> Ready — open it</Link>
      ) : status === "failed" ? (
        <span className="inline-flex items-center gap-1 text-sm text-danger"><XCircle size={15} /> {job?.error?.replace(/^\w+Error: /, "") ?? "Failed"}</span>
      ) : <Spinner label={job?.progress ?? "Writing…"} />}
    </li>
  );
}

export default function Composer() {
  const tz = useTz();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const meta = useMeta();
  const accounts = useContentAccounts();
  const pillars = usePillars();
  const [tab, setTab] = useState<"ai" | "manual">("ai");
  const [picked, setPicked] = useState<string[]>([]);
  const [f, setF] = useState({ brief: "", post_type: "", pillar_id: "", when: "", then: "draft", link_url: "" });
  const [m, setM] = useState({ text: "", title: "", tags: "", link_url: "", first_comment: "", post_type: "text", when: "", status: "draft" });
  const [made, setMade] = useState<Made[] | null>(null);
  // Opened from a research idea brief (/posts/new?brief=<id>): prefill it and write a grounded post from it.
  const [params] = useSearchParams();
  const briefId = params.get("brief");
  const prefill = useQuery({
    queryKey: ["research", "prefill", briefId], enabled: !!briefId,
    queryFn: () => api<BriefPrefill>(`/research/briefs/${briefId}/composer`),
  });

  const list = accounts.data ?? [];
  const chosen = list.filter((a) => picked.includes(a.id));
  const platforms = meta.data?.platforms ?? [];
  const typesFor = (a: AccountBrief) => platforms.find((p) => p.id === a.platform)?.post_types ?? ["text"];
  const commonTypes = useMemo(() => {
    if (!chosen.length) return [];
    return chosen.map(typesFor).reduce((acc, t) => acc.filter((x) => t.includes(x)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picked.join(","), platforms.length]);
  useEffect(() => {
    const want = prefill.data?.post_type;
    if (want && !f.post_type && commonTypes.includes(want)) setF((cur) => ({ ...cur, post_type: want }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill.data, commonTypes.join(",")]);
  useEffect(() => {
    const p = prefill.data;
    if (p) setF((cur) => ({ ...cur, brief: p.brief, link_url: p.link_url ?? "", pillar_id: p.pillar_id ?? "" }));
  }, [prefill.data]);
  const strictest = chosen.length ? Math.min(...chosen.map((a) => platforms.find((p) => p.id === a.platform)?.max_chars ?? 3000)) : 3000;
  const needsTitle = chosen.some((a) => (platforms.find((p) => p.id === a.platform)?.title_chars ?? 0) > 0);
  const manualTypes = commonTypes.filter((t) => t === "text" || t === "link");

  const compose = useMutation({
    mutationFn: () => api<{ posts: Made[] }>(briefId ? `/research/briefs/${briefId}/compose` : "/content/posts/compose", {
      method: "POST",
      json: {
        account_ids: picked, brief: f.brief, post_type: f.post_type || null, pillar_id: f.pillar_id || null,
        scheduled_at: fromLocalInput(f.when), then: f.then, link_url: f.link_url || null,
      },
    }),
    onSuccess: (r) => { setMade(r.posts); qc.invalidateQueries({ queryKey: ["posts"] }); if (briefId) qc.invalidateQueries({ queryKey: ["research"] }); },
  });
  const create = useMutation({
    mutationFn: async () => {
      const out: PostDetail[] = [];
      for (const id of picked) {
        out.push(await api<PostDetail>("/content/posts", {
          method: "POST",
          json: {
            account_id: id, post_type: m.post_type, text: m.text, title: m.title || null, hashtags: parseTags(m.tags),
            link_url: m.link_url || null, first_comment: m.first_comment || null, scheduled_at: fromLocalInput(m.when), status: m.status,
          },
        }));
      }
      return out;
    },
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["posts"] });
      nav(out.length === 1 ? `/posts/${out[0].id}` : "/posts");
    },
  });

  const togglePick = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  const count = caption(m.text, parseTags(m.tags)).length;

  return (
    <div className="max-w-3xl">
      <Link to="/posts" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={15} /> All posts</Link>
      <PageHeader title="New post" description="Write one post for one or several accounts. Each account gets its own version that fits the platform." />

      <Card className="mb-5">
        <div className="mb-2 text-sm font-medium">Post to</div>
        {accounts.error && <Alert>Couldn't load accounts: {(accounts.error as Error).message}</Alert>}
        {accounts.isLoading ? <Spinner /> : list.length === 0 ? (
          <Alert tone="info">No social accounts yet. <Link to="/settings/accounts" className="underline">Connect one</Link> or add an account you post to by hand.</Alert>
        ) : (
          <div className="flex flex-wrap gap-2">
            {list.map((a) => {
              const on = picked.includes(a.id);
              return (
                <button key={a.id} type="button" aria-pressed={on} onClick={() => togglePick(a.id)}
                  className={`rounded-full border px-3 py-1.5 text-sm ${on ? "border-primary-600 bg-primary-600 text-white" : "border-line hover:border-brand/50"}`}>
                  {a.name} <span className={on ? "text-white/80" : "text-muted"}>· {a.platform_label}</span>
                </button>
              );
            })}
          </div>
        )}
      </Card>

      {made ? (
        <Card>
          <h2 className="font-semibold">Writing your posts</h2>
          <p className="mb-2 text-sm text-muted">This usually takes under a minute. You can leave this page; the drafts will be in All posts.</p>
          <ul className="divide-y divide-line">{made.map((x) => <Progress key={x.post_id} item={x} account={list.find((a) => a.id === x.account_id)} />)}</ul>
          <div className="mt-4 flex gap-2">
            <Button variant="secondary" onClick={() => { setMade(null); setF({ ...f, brief: "" }); }}>Write another</Button>
            <Button variant="ghost" onClick={() => nav("/posts")}>Go to all posts</Button>
          </div>
        </Card>
      ) : (
        <>
          <Tabs value={tab} onChange={setTab} items={[{ id: "ai", label: "Write with AI" }, { id: "manual", label: "Write it myself" }]} />
          {tab === "ai" ? (
            <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); compose.mutate(); }}>
              {compose.error && <Alert>{(compose.error as Error).message}</Alert>}
              {prefill.error && <Alert>{(prefill.error as Error).message}</Alert>}
              {prefill.data && (
                <div className="flex gap-2.5 rounded-lg border border-secondary-200 bg-accent-tint px-3 py-2.5 text-sm">
                  <Lightbulb size={16} className="mt-0.5 shrink-0 text-secondary-700" />
                  <p>Writing from the idea <span className="font-medium">“{prefill.data.title}”</span>. Its facts and sources are below, so the post stays grounded. <Link to="/research" className="text-brand underline">Back to ideas</Link></p>
                </div>
              )}
              <Field label="What should the post be about?" hint="A topic, an announcement, or a few bullet points. The AI adds facts from your company knowledge.">
                <Textarea required minLength={3} rows={prefill.data ? 12 : 5} value={f.brief} onChange={(e) => setF({ ...f, brief: e.target.value })}
                  placeholder="We launched leave management in HRP HRMS: employees apply from their phone, managers approve in one tap." />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Post type" hint={chosen.length > 1 ? "Only types every chosen account supports." : undefined}>
                  <Select value={f.post_type} onChange={(e) => setF({ ...f, post_type: e.target.value })} disabled={!chosen.length}>
                    <option value="">{chosen.length ? "Best fit for each account" : "Choose accounts first"}</option>
                    {commonTypes.map((t) => <option key={t} value={t}>{meta.data?.post_types[t] ?? t}</option>)}
                  </Select>
                </Field>
                <Field label="Topic pillar (optional)">
                  <Select value={f.pillar_id} onChange={(e) => setF({ ...f, pillar_id: e.target.value })}>
                    <option value="">None</option>
                    {(pillars.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                </Field>
                <Field label={`When (${tzLabel(tz)}, optional)`}>
                  <Input type="datetime-local" value={f.when} onChange={(e) => setF({ ...f, when: e.target.value })} />
                </Field>
                <Field label="Link (optional)"><Input type="url" placeholder="https://" value={f.link_url} onChange={(e) => setF({ ...f, link_url: e.target.value })} /></Field>
              </div>
              <Field label="When it's written">
                <Select value={f.then} onChange={(e) => setF({ ...f, then: e.target.value })}>
                  <option value="draft">Keep it as a draft for me to edit</option>
                  <option value="review">Send it for approval</option>
                </Select>
              </Field>
              <Button type="submit" disabled={!picked.length || compose.isPending || f.brief.trim().length < 3}>
                <Sparkles size={16} /> {compose.isPending ? "Starting…" : picked.length > 1 ? `Write ${picked.length} posts` : "Write post"}
              </Button>
            </form>
          ) : (
            <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
              {create.error && <Alert>{(create.error as Error).message}</Alert>}
              {needsTitle && (
                <Field label="Title" hint="Pinterest and YouTube show a separate title (up to 100 characters).">
                  <Input value={m.title} maxLength={100} onChange={(e) => setM({ ...m, title: e.target.value })} />
                </Field>
              )}
              <Field label="Text"><Textarea required rows={8} value={m.text} onChange={(e) => setM({ ...m, text: e.target.value })} /></Field>
              <div className="-mt-2 flex justify-end"><CharCounter count={count} max={strictest} /></div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Hashtags"><Input value={m.tags} placeholder="#HRTech #AI" onChange={(e) => setM({ ...m, tags: e.target.value })} /></Field>
                <Field label="Link"><Input type="url" placeholder="https://" value={m.link_url} onChange={(e) => setM({ ...m, link_url: e.target.value })} /></Field>
                <Field label="Post type" hint="Add images or video from the post page after saving.">
                  <Select value={m.post_type} onChange={(e) => setM({ ...m, post_type: e.target.value })}>
                    {(manualTypes.length ? manualTypes : ["text"]).map((t) => <option key={t} value={t}>{meta.data?.post_types[t] ?? t}</option>)}
                  </Select>
                </Field>
                <Field label={`When (${tzLabel(tz)})`}><Input type="datetime-local" value={m.when} onChange={(e) => setM({ ...m, when: e.target.value })} /></Field>
              </div>
              <Field label="First comment (optional)"><Textarea rows={2} value={m.first_comment} onChange={(e) => setM({ ...m, first_comment: e.target.value })} /></Field>
              <Field label="Then">
                <Select value={m.status} onChange={(e) => setM({ ...m, status: e.target.value })}>
                  <option value="draft">Save as draft</option>
                  <option value="in_review">Send for approval</option>
                  {can("content.approve") && <option value="scheduled">Schedule it (no approval needed)</option>}
                </Select>
              </Field>
              {chosen.some((a) => !typesFor(a).includes(m.post_type)) && (
                <Alert tone="warn">{chosen.filter((a) => !typesFor(a).includes(m.post_type)).map((a) => a.name).join(", ")} can't take text-only posts. Use Write with AI to create an image post instead.</Alert>
              )}
              <Button type="submit" disabled={!picked.length || create.isPending || !m.text.trim()}>
                {create.isPending ? "Saving…" : m.status === "scheduled" ? "Schedule" : m.status === "in_review" ? "Send for approval" : "Save draft"}
              </Button>
            </form>
          )}
        </>
      )}
    </div>
  );
}
