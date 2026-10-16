import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, Pencil, Plus } from "lucide-react";
import { api } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, Spinner, Textarea, Toggle, formatDate,
} from "../../components/ui";
import { DeleteButton, ErrorText, JobStatus, ListEditor, SectionTitle, SetupHint } from "./shared";
import { ENTITY_TYPES, hostOf, type Entity, type EntityType, type Overview, type Profile } from "./types";

type Draft = {
  summary: string; positioning: string; geography: string; industries: string[]; audiences: string[]; differentiators: string[];
};

const toDraft = (p: Profile | null): Draft => ({
  summary: p?.summary ?? "", positioning: p?.positioning ?? "", geography: p?.geography ?? "",
  industries: p?.industries ?? [], audiences: p?.audiences ?? [], differentiators: p?.differentiators ?? [],
});

function changedFields(d: Draft, p: Profile | null): Partial<Draft> {
  const base = toDraft(p);
  const out: Partial<Draft> = {};
  (Object.keys(d) as (keyof Draft)[]).forEach((k) => {
    if (JSON.stringify(d[k]) !== JSON.stringify(base[k])) (out as Record<string, unknown>)[k] = d[k];
  });
  return out;
}

function ProfileForm({ profile, canManage }: { profile: Profile | null; canManage: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => toDraft(profile));
  // Reset only when the saved profile really changes (the overview refetches while tasks run).
  const version = `${profile?.id ?? ""}:${profile?.updated_at ?? ""}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setDraft(toDraft(profile)), [version]);
  const changes = useMemo(() => changedFields(draft, profile), [draft, profile]);
  const dirty = Object.keys(changes).length > 0;
  const refresh = () => qc.invalidateQueries({ queryKey: ["intel"] });
  const save = useMutation({
    mutationFn: () => api<{ profile: Profile }>("/intel/profile", { method: "PATCH", json: changes }),
    onSuccess: refresh,
  });
  const confirm = useMutation({
    mutationFn: async () => {
      if (dirty) await api("/intel/profile", { method: "PATCH", json: changes });
      return api<{ entities_confirmed: number }>("/intel/profile/confirm", { method: "POST", json: { confirm_entities: true } });
    },
    onSuccess: refresh,
  });
  const set = <K extends keyof Draft>(k: K) => (v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const edited = new Set(profile?.edited_fields ?? []);
  const editedNote = (k: string) => (edited.has(k) ? " (edited by your team; re-analysis keeps it)" : "");

  return (
    <Card>
      <form className="space-y-5" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Company profile</h2>
            <p className="text-sm text-muted">
              {profile?.extracted_at ? `Built from your website on ${formatDate(profile.extracted_at)}.` : "Fill this in by hand or run the website analysis."}
              {" "}Posts and articles use these facts.
            </p>
          </div>
          {profile?.confirmed ? (
            <Badge tone="brand"><CheckCircle2 size={13} className="mr-1" /> Confirmed {profile.confirmed_at && formatDate(profile.confirmed_at)}</Badge>
          ) : profile ? <Badge tone="accent">Not confirmed yet</Badge> : null}
        </div>
        <ErrorText error={save.error || confirm.error} />
        <Field label="What the company does" hint={`Two to four sentences.${editedNote("summary")}`}>
          <Textarea rows={4} value={draft.summary} onChange={(e) => set("summary")(e.target.value)} disabled={!canManage} />
        </Field>
        <Field label="Positioning" hint={`How you want to be seen, in one sentence.${editedNote("positioning")}`}>
          <Textarea rows={2} value={draft.positioning} onChange={(e) => set("positioning")(e.target.value)} disabled={!canManage} />
        </Field>
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <div className="mb-1.5 text-sm font-medium">Industries</div>
            <ListEditor label="industries" value={draft.industries} onChange={set("industries")} placeholder="e.g. Manufacturing" disabled={!canManage} />
          </div>
          <div>
            <div className="mb-1.5 text-sm font-medium">Audiences</div>
            <ListEditor label="audiences" value={draft.audiences} onChange={set("audiences")} placeholder="e.g. HR heads at mid-size companies" disabled={!canManage} />
          </div>
          <div>
            <div className="mb-1.5 text-sm font-medium">Differentiators</div>
            <ListEditor label="differentiators" value={draft.differentiators} onChange={set("differentiators")} placeholder="What sets you apart" disabled={!canManage} />
          </div>
          <Field label="Geography" hint={`Where you operate.${editedNote("geography")}`}>
            <Input value={draft.geography} onChange={(e) => set("geography")(e.target.value)} disabled={!canManage} placeholder="e.g. India and the Middle East" />
          </Field>
        </div>
        {profile && Object.keys(profile.social_links).length > 0 && (
          <div>
            <div className="mb-1.5 text-sm font-medium">Social profiles linked from your website</div>
            <div className="flex flex-wrap gap-3 text-sm">
              {Object.entries(profile.social_links).map(([k, v]) => (
                <a key={k} href={v} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary-700 hover:underline">
                  {k === "x" ? "X" : k.charAt(0).toUpperCase() + k.slice(1)} <ExternalLink size={12} />
                </a>
              ))}
            </div>
          </div>
        )}
        {canManage && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
            {confirm.isSuccess && <span className="mr-auto text-sm text-emerald-700">Profile confirmed. {confirm.data.entities_confirmed ? `${confirm.data.entities_confirmed} facts confirmed too.` : ""}</span>}
            {save.isSuccess && !dirty && !confirm.isSuccess && <span className="mr-auto text-sm text-emerald-700">Saved.</span>}
            <Button type="submit" variant="secondary" disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</Button>
            <Button type="button" onClick={() => confirm.mutate()} disabled={confirm.isPending}
              title="Marks the profile and every fact below as correct. Delete anything that's wrong first.">
              {confirm.isPending ? "Confirming…" : profile?.confirmed ? "Confirm again with all facts" : "Confirm profile and facts"}
            </Button>
          </div>
        )}
        {canManage && !profile?.confirmed && (
          <p className="text-right text-xs text-muted">Confirming marks every fact below as correct. Delete or edit anything that's wrong first.</p>
        )}
      </form>
    </Card>
  );
}

function EntityForm({ type, existing, onClose }: { type: EntityType; existing?: Entity; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [url, setUrl] = useState(existing?.url ?? "");
  const [publicOk, setPublicOk] = useState(existing?.public_mention_ok ?? false);
  const meta = ENTITY_TYPES.find((t) => t.id === type)!;
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description: description || null, url: url || null, ...(type === "client" ? { public_mention_ok: publicOk } : {}) };
      return existing
        ? api(`/intel/entities/${existing.id}`, { method: "PATCH", json: body })
        : api("/intel/entities", { method: "POST", json: { ...body, type, confirmed: true } });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["intel"] }); onClose(); },
  });
  return (
    <Modal title={existing ? `Edit ${existing.name}` : `Add a ${meta.singular}`} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <ErrorText error={save.error} />
        <Field label="Name"><Input required maxLength={300} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description" hint="One or two sentences. Posts may quote this.">
          <Textarea rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Link (optional)"><Input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
        {type === "client" && (
          <Toggle checked={publicOk} onChange={setPublicOk} label="Can be named in posts" />
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending || !name.trim()}>{existing ? "Save changes" : `Add ${meta.singular}`}</Button>
        </div>
      </form>
    </Modal>
  );
}

function EntityRow({ e, canManage }: { e: Entity; canManage: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["intel"] });
  const patch = useMutation({
    mutationFn: (body: Partial<Pick<Entity, "confirmed" | "public_mention_ok">>) => api(`/intel/entities/${e.id}`, { method: "PATCH", json: body }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => api(`/intel/entities/${e.id}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{e.name}</span>
          {e.url && <a href={e.url} target="_blank" rel="noreferrer" className="text-xs text-primary-700 hover:underline">{hostOf(e.url)}</a>}
          {e.source !== "website" && <Badge>{e.source === "manual" ? "Added by hand" : e.source === "upload" ? "From a document" : e.source}</Badge>}
          {e.edited && e.source !== "manual" && <Badge>Edited</Badge>}
        </div>
        {e.description && <p className="text-sm text-muted">{e.description}</p>}
        {(patch.error || remove.error) && <p className="text-sm text-danger">{((patch.error || remove.error) as Error).message}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {e.type === "client" && (
          <Toggle checked={e.public_mention_ok} onChange={(v) => patch.mutate({ public_mention_ok: v })} label="Can be named in posts" disabled={!canManage || patch.isPending} />
        )}
        <Toggle checked={e.confirmed} onChange={(v) => patch.mutate({ confirmed: v })} label="Confirmed" disabled={!canManage || patch.isPending} />
        {canManage && (
          <>
            <Button variant="ghost" className="h-8 px-2" aria-label={`Edit ${e.name}`} onClick={() => setEditing(true)}><Pencil size={15} /></Button>
            <DeleteButton label={`Delete ${e.name}`} onConfirm={() => remove.mutate()} busy={remove.isPending} />
          </>
        )}
      </div>
      {editing && <EntityForm type={e.type} existing={e} onClose={() => setEditing(false)} />}
    </li>
  );
}

export default function ProfileTab({ overview }: { overview: Overview }) {
  const { can } = useAuth();
  const canManage = can("intel.manage");
  const entities = useQuery({ queryKey: ["intel", "entities"], queryFn: () => api<Entity[]>("/intel/entities") });
  const [adding, setAdding] = useState<EntityType | null>(null);
  const byType = useMemo(() => {
    const m = new Map<EntityType, Entity[]>();
    for (const e of entities.data ?? []) m.set(e.type, [...(m.get(e.type) ?? []), e]);
    return m;
  }, [entities.data]);
  const website = overview.website_source?.url ?? overview.organization_website;
  const anyConfirmed = (entities.data ?? []).some((e) => e.confirmed);

  return (
    <div className="space-y-8">
      <JobStatus jobKey="extract" label="Building the company profile" />
      {!overview.profile && <SetupHint aiReady={overview.ai_ready} hasWebsite={!!website} />}
      <ProfileForm profile={overview.profile} canManage={canManage} />

      <section>
        <SectionTitle
          title="Products, clients and other facts"
          description={anyConfirmed
            ? "Only confirmed facts are used when writing. Clients are named only when “can be named in posts” is on."
            : "Nothing is confirmed yet, so every fact below is used when writing. Confirm the ones that are right."}
        />
        {entities.isLoading && <Spinner label="Loading…" />}
        <ErrorText error={entities.error} />
        {entities.data && !entities.data.length && !canManage && (
          <EmptyState title="No facts yet" body="Someone with the “Run crawls and confirm the company profile” permission can analyze the website." />
        )}
        {entities.data && (
          <div className="grid gap-4 xl:grid-cols-2">
            {ENTITY_TYPES.map((t) => {
              const list = byType.get(t.id) ?? [];
              if (!list.length && !canManage) return null;
              return (
                <Card key={t.id}>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h3 className="font-semibold">{t.label} <span className="text-sm font-normal text-muted">{list.length || ""}</span></h3>
                      <p className="text-xs text-muted">{t.hint}</p>
                    </div>
                    {canManage && (
                      <Button variant="secondary" className="h-8 px-3" onClick={() => setAdding(t.id)}><Plus size={14} /> Add</Button>
                    )}
                  </div>
                  {list.length ? (
                    <ul className="mt-2 divide-y divide-line">{list.map((e) => <EntityRow key={e.id} e={e} canManage={canManage} />)}</ul>
                  ) : (
                    <p className="mt-3 text-sm text-muted">None found. Add one, or re-analyze the website after updating it.</p>
                  )}
                </Card>
              );
            })}
          </div>
        )}
        {canManage && byType.get("client")?.some((c) => !c.public_mention_ok) && (
          <div className="mt-4"><Alert tone="info">Clients found only in uploaded documents start as private. Turn on “Can be named in posts” once you have permission.</Alert></div>
        )}
      </section>
      {adding && <EntityForm type={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}
