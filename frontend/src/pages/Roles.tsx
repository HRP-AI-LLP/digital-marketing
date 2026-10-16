import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Field, Input, PageHeader } from "../components/ui";

export type Role = { id: string; name: string; description: string | null; is_system: boolean; permissions: string[]; member_count: number };
type Permission = { code: string; group: string; description: string };

const NEW = "__new__";

export default function Roles() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("/roles") });
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => api<Permission[]>("/permissions") });
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [saved, setSaved] = useState("");

  const role = roles.data?.find((r) => r.id === selected);
  const isNew = selected === NEW;
  const editable = can("role.manage") && (isNew || (role && !role.is_system));

  useEffect(() => {
    if (!selected && roles.data?.length) setSelected(roles.data[0].id);
  }, [roles.data, selected]);
  useEffect(() => {
    setSaved("");
    if (isNew) { setName(""); setDescription(""); setCodes([]); }
    else if (role) { setName(role.name); setDescription(role.description ?? ""); setCodes(role.permissions); }
  }, [selected, role, isNew]);

  const groups = useMemo(() => {
    const g = new Map<string, Permission[]>();
    perms.data?.forEach((p) => g.set(p.group, [...(g.get(p.group) ?? []), p]));
    return [...g.entries()];
  }, [perms.data]);

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api<Role>("/roles", { method: "POST", json: { name, description: description || null, permissions: codes } })
        : api<Role>(`/roles/${selected}`, { method: "PATCH", json: { name, description, permissions: codes } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["roles"] }); setSelected(r.id); setSaved(isNew ? "Role created." : "Changes saved."); },
  });
  const remove = useMutation({
    mutationFn: () => api(`/roles/${selected}`, { method: "DELETE" }),
    onSuccess: () => { setSelected(null); qc.invalidateQueries({ queryKey: ["roles"] }); },
  });

  const toggle = (c: string, on: boolean) => setCodes((cur) => (on ? [...cur, c] : cur.filter((x) => x !== c)));
  const err = (save.error || remove.error) as Error | null;

  return (
    <div>
      <PageHeader
        title="Roles and access"
        description="A role is a named set of permissions. Give people one or more roles on the Users page."
        action={can("role.manage") && <Button variant="secondary" onClick={() => setSelected(NEW)}><Plus size={16} /> New role</Button>}
      />
      <div className="grid gap-6 md:grid-cols-[240px_1fr]">
        <ul className="space-y-0.5" aria-label="Roles">
          {roles.data?.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => setSelected(r.id)}
                aria-current={selected === r.id}
                className={`w-full rounded-md px-3 py-2 text-left ${selected === r.id ? "bg-surface border border-line shadow-sm" : "hover:bg-ink/5"}`}
              >
                <span className="flex items-center gap-1.5 font-medium">{r.name}{r.is_system && <Lock size={12} className="text-muted" />}</span>
                <span className="text-xs text-muted">{r.member_count} {r.member_count === 1 ? "person" : "people"} · {r.permissions.length} permissions</span>
              </button>
            </li>
          ))}
          {isNew && <li className="rounded-md border border-dashed border-brand/50 px-3 py-2 text-sm text-brand">New role</li>}
        </ul>

        {(role || isNew) && (
          <form
            className="rounded-lg border border-line bg-surface p-5"
            onSubmit={(e) => { e.preventDefault(); save.mutate(); }}
          >
            {role?.is_system && (
              <Alert tone="info">Administrators always have every permission, including ones added in future updates. This role can't be changed.</Alert>
            )}
            {err && <div className="mb-4"><Alert>{err.message}</Alert></div>}
            {saved && <div className="mb-4"><Alert tone="info">{saved}</Alert></div>}
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Role name"><Input required disabled={!editable} value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <Field label="Description"><Input disabled={!editable} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
            </div>

            <div className="mt-6 space-y-6">
              {groups.map(([group, items]) => {
                const all = items.every((p) => codes.includes(p.code));
                return (
                  <fieldset key={group}>
                    <div className="flex items-center justify-between border-b border-line pb-1.5">
                      <legend className="font-display font-semibold">{group}</legend>
                      {editable && (
                        <button type="button" className="text-xs text-brand hover:underline"
                          onClick={() => items.forEach((p) => toggle(p.code, !all))}>
                          {all ? "Clear all" : "Select all"}
                        </button>
                      )}
                    </div>
                    <div className="mt-2 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                      {items.map((p) => (
                        <label key={p.code} className={`flex items-start gap-2.5 text-sm ${editable ? "cursor-pointer" : ""}`}>
                          <input
                            type="checkbox"
                            className="mt-1 accent-[rgb(var(--brand))]"
                            disabled={!editable}
                            checked={codes.includes(p.code)}
                            onChange={(e) => toggle(p.code, e.target.checked)}
                          />
                          <span>{p.description}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                );
              })}
            </div>

            {editable && (
              <div className="mt-6 flex items-center justify-between border-t border-line pt-4">
                {!isNew && role ? (
                  <Button type="button" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete role</Button>
                ) : <span />}
                <div className="flex gap-2">
                  {isNew && <Button type="button" variant="ghost" onClick={() => setSelected(null)}>Cancel</Button>}
                  <Button type="submit" disabled={save.isPending}>{isNew ? "Create role" : "Save changes"}</Button>
                </div>
              </div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
