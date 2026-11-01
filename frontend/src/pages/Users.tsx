import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Field, Input, Modal, PageHeader, SecretReveal, Toggle, formatDate } from "../components/ui";
import type { Role } from "./Roles";

type Member = {
  user_id: string; email: string; full_name: string; is_active: boolean;
  roles: { id: string; name: string }[]; last_login_at: string | null; joined_at: string; two_factor_enabled?: boolean;
};

function RolePicker({ roles, value, onChange }: { roles: Role[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-sm font-medium mb-1.5">Roles</legend>
      {roles.map((r) => (
        <label key={r.id} className="flex items-start gap-2.5 rounded-md border border-line px-3 py-2 hover:border-brand/40">
          <input
            type="checkbox"
            className="mt-1 accent-[rgb(var(--brand))]"
            checked={value.includes(r.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, r.id] : value.filter((x) => x !== r.id))}
          />
          <span>
            <span className="block text-sm font-medium">{r.name}</span>
            {r.description && <span className="block text-xs text-muted">{r.description}</span>}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function AddUser({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [roleIds, setRoleIds] = useState<string[]>(roles.filter((r) => r.name === "Viewer").map((r) => r.id));
  const [sendEmail, setSendEmail] = useState(true);
  type Added = { temporary_password: string | null; member: Member; email_sent: boolean; email_note: string | null };
  const [result, setResult] = useState<Added | null>(null);
  const m = useMutation({
    mutationFn: () => api<Added>("/users", { method: "POST", json: { email, full_name: name, role_ids: roleIds, send_email: sendEmail } }),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["users"] }); },
  });

  if (result) {
    return (
      <Modal title="User added" onClose={onClose}>
        <div className="space-y-4">
          <p>{result.member.full_name} can now sign in as <strong>{result.member.email}</strong>.</p>
          {result.email_note && <Alert tone={result.email_sent ? "info" : "warn"}>{result.email_note}</Alert>}
          {result.temporary_password && result.email_sent && (
            <p className="text-sm text-muted">They'll be asked to choose their own password the first time they sign in. The temporary password is also below in case you need it.</p>
          )}
          {result.temporary_password
            ? <SecretReveal label="Temporary password" value={result.temporary_password} />
            : <Alert tone="info">They already have an account in another organization, so they'll use their existing password.</Alert>}
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Add user" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); m.mutate(); }}>
        {m.error && <Alert>{(m.error as Error).message}</Alert>}
        <Field label="Full name"><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Email"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <RolePicker roles={roles} value={roleIds} onChange={setRoleIds} />
        <Toggle checked={sendEmail} onChange={setSendEmail} label="Email them their sign-in details and a temporary password" />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={m.isPending || !roleIds.length}>{m.isPending ? "Adding…" : "Add user"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function EditUser({ member, roles, onClose }: { member: Member; roles: Role[]; onClose: () => void }) {
  const qc = useQueryClient();
  const { session } = useAuth();
  const [name, setName] = useState(member.full_name);
  const [roleIds, setRoleIds] = useState(member.roles.map((r) => r.id));
  const [temp, setTemp] = useState<string | null>(null);
  const [tempNote, setTempNote] = useState<{ sent: boolean; note: string | null } | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["users"] });
  const save = useMutation({
    mutationFn: (body: object) => api(`/users/${member.user_id}`, { method: "PATCH", json: body }),
    onSuccess: () => { done(); onClose(); },
  });
  const reset = useMutation({
    mutationFn: () => api<{ temporary_password: string; email_sent: boolean; email_note: string | null }>(`/users/${member.user_id}/reset-password`, { method: "POST" }),
    onSuccess: (r) => { setTemp(r.temporary_password); setTempNote({ sent: r.email_sent, note: r.email_note }); },
  });
  const [confirm2fa, setConfirm2fa] = useState(false);
  const [note2fa, setNote2fa] = useState<string | null>(null);
  const reset2fa = useMutation({
    mutationFn: () => api<Member>(`/users/${member.user_id}/reset-2fa`, { method: "POST" }),
    onSuccess: () => {
      setConfirm2fa(false); done();
      setNote2fa(`Two-step sign-in is off for ${member.full_name}. They can sign in with their password and set it up again.`);
    },
  });
  const isSelf = member.user_id === session?.user.id;
  const err = (save.error || reset.error || reset2fa.error) as Error | null;

  return (
    <Modal title={`Edit ${member.full_name}`} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ ...(name !== member.full_name ? { full_name: name } : {}), role_ids: roleIds });
        }}
      >
        {err && <Alert>{err.message}</Alert>}
        {tempNote?.note && <Alert tone={tempNote.sent ? "info" : "warn"}>{tempNote.note}</Alert>}
        {temp && <SecretReveal label="New temporary password" value={temp} />}
        {note2fa && <Alert tone="info">{note2fa}</Alert>}
        {member.two_factor_enabled && !note2fa && !isSelf && (
          <div className="rounded-lg border border-line px-3 py-2.5 text-sm">
            <div className="font-medium">Two-step sign-in is on</div>
            {confirm2fa ? (
              <div className="mt-1.5 space-y-2">
                <p className="text-muted">Only do this if {member.full_name} lost their phone and recovery codes, and you've checked it's really them.</p>
                <div className="flex gap-2">
                  <Button type="button" variant="danger" disabled={reset2fa.isPending} onClick={() => reset2fa.mutate()}>Reset two-step sign-in</Button>
                  <Button type="button" variant="ghost" onClick={() => setConfirm2fa(false)}>Cancel</Button>
                </div>
              </div>
            ) : (
              <button type="button" className="mt-0.5 text-brand underline" onClick={() => setConfirm2fa(true)}>Lost their phone? Reset it</button>
            )}
          </div>
        )}
        <Field label="Full name"><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="text-sm"><span className="text-muted">Email </span>{member.email}</div>
        <RolePicker roles={roles} value={roleIds} onChange={setRoleIds} />
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
          <div className="flex gap-2">
            {!isSelf && (
              <Button type="button" variant="danger" onClick={() => save.mutate({ is_active: !member.is_active })}>
                {member.is_active ? "Deactivate" : "Reactivate"}
              </Button>
            )}
            <Button type="button" variant="secondary" onClick={() => reset.mutate()} disabled={reset.isPending}>Reset password</Button>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={save.isPending || !roleIds.length}>Save changes</Button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export default function Users() {
  const { can, session } = useAuth();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<Member[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("/roles"), enabled: can("role.view") });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const manage = can("user.manage") && !!roles.data;

  return (
    <div>
      <PageHeader
        title="Users"
        description={`People who can sign in to ${session?.organization?.name}. What they can do depends on their roles.`}
        action={manage && <Button onClick={() => setAdding(true)}><UserPlus size={16} /> Add user</Button>}
      />
      {users.error && <Alert>{(users.error as Error).message}</Alert>}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Name</th>
              <th className="px-4 py-2.5 font-medium">Roles</th>
              <th className="px-4 py-2.5 font-medium">Two-step sign-in</th>
              <th className="px-4 py-2.5 font-medium">Last sign-in</th>
              <th className="px-4 py-2.5 font-medium"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {users.data?.map((u) => (
              <tr key={u.user_id} className={u.is_active ? "" : "text-muted"}>
                <td className="px-4 py-3">
                  <div className="font-medium text-ink">{u.full_name} {!u.is_active && <Badge tone="danger">Deactivated</Badge>}</div>
                  <div className="text-muted">{u.email}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">{u.roles.map((r) => <Badge key={r.id} tone={r.name === "Administrator" ? "brand" : "neutral"}>{r.name}</Badge>)}</div>
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  {u.two_factor_enabled ? <Badge tone="brand">On</Badge> : <Badge>Off</Badge>}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">{formatDate(u.last_login_at, session?.organization?.timezone)}</td>
                <td className="px-4 py-3 text-right">
                  {manage && <Button variant="ghost" onClick={() => setEditing(u)}>Edit</Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.isLoading && <p className="px-4 py-6 text-muted">Loading users…</p>}
      </div>
      {adding && roles.data && <AddUser roles={roles.data} onClose={() => setAdding(false)} />}
      {editing && roles.data && <EditUser member={editing} roles={roles.data} onClose={() => setEditing(null)} />}
    </div>
  );
}
