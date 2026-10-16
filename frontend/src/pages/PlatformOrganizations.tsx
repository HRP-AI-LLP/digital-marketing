import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, type Organization } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Badge, Button, Field, Input, Modal, PageHeader, SecretReveal, Select, formatDate } from "../components/ui";
import { TIMEZONES } from "./OrganizationSettings";

type Row = Organization & { member_count: number };
type Created = { organization: Organization; admin_email: string; temporary_password: string | null };

function NewOrg({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", website: "", timezone: "Asia/Kolkata", admin_name: "", admin_email: "" });
  const [created, setCreated] = useState<Created | null>(null);
  const m = useMutation({
    mutationFn: () => api<Created>("/platform/organizations", { method: "POST", json: { ...f, website: f.website || null } }),
    onSuccess: (r) => { setCreated(r); qc.invalidateQueries({ queryKey: ["platform-orgs"] }); },
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  if (created) {
    return (
      <Modal title="Organization created" onClose={onClose}>
        <div className="space-y-4">
          <p><strong>{created.organization.name}</strong> is ready. {created.admin_email} is its Administrator.</p>
          {created.temporary_password
            ? <SecretReveal label={`Temporary password for ${created.admin_email}`} value={created.temporary_password} />
            : <Alert tone="info">{created.admin_email} already has an account and will use their existing password.</Alert>}
          <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="New organization" onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); m.mutate(); }}>
        {m.error && <Alert>{(m.error as Error).message}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Organization name"><Input required value={f.name} onChange={set("name")} /></Field>
          <Field label="Website"><Input type="url" placeholder="https://" value={f.website} onChange={set("website")} /></Field>
          <Field label="Time zone">
            <Select value={f.timezone} onChange={set("timezone")}>{TIMEZONES.map((t) => <option key={t}>{t}</option>)}</Select>
          </Field>
        </div>
        <div className="border-t border-line pt-4">
          <h3 className="font-semibold">First administrator</h3>
          <p className="text-sm text-muted mb-3">They can add the rest of their team.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name"><Input required value={f.admin_name} onChange={set("admin_name")} /></Field>
            <Field label="Email"><Input type="email" required value={f.admin_email} onChange={set("admin_email")} /></Field>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={m.isPending}>{m.isPending ? "Creating…" : "Create organization"}</Button>
        </div>
      </form>
    </Modal>
  );
}

export default function PlatformOrganizations() {
  const { session, switchOrg } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform-orgs"], queryFn: () => api<Row[]>("/platform/organizations") });
  const [creating, setCreating] = useState(false);
  const status = useMutation({
    mutationFn: (o: Row) => api(`/platform/organizations/${o.id}`, { method: "PATCH", json: { status: o.status === "active" ? "suspended" : "active" } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["platform-orgs"] }),
  });

  return (
    <div>
      <PageHeader
        title="All organizations"
        description="Every tenant on this installation. Each organization's data is isolated from the others."
        action={<Button onClick={() => setCreating(true)}><Plus size={16} /> New organization</Button>}
      />
      {(q.error || status.error) && <Alert>{((q.error || status.error) as Error).message}</Alert>}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Organization</th>
              <th className="px-4 py-2.5 font-medium">People</th>
              <th className="px-4 py-2.5 font-medium">Created</th>
              <th className="px-4 py-2.5 font-medium"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {q.data?.map((o) => (
              <tr key={o.id}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 font-medium">
                    {o.name}
                    {o.id === session?.organization?.id && <Badge tone="brand">Current</Badge>}
                    {o.status !== "active" && <Badge tone="danger">Suspended</Badge>}
                  </div>
                  <div className="text-muted">{o.website ?? o.slug}</div>
                </td>
                <td className="px-4 py-3 tabular-nums">{o.member_count}</td>
                <td className="px-4 py-3 whitespace-nowrap">{formatDate(o.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    {o.id !== session?.organization?.id && (
                      <Button variant="ghost" onClick={async () => { await switchOrg(o.id); nav("/"); }}>Open</Button>
                    )}
                    <Button variant="ghost" onClick={() => status.mutate(o)}>{o.status === "active" ? "Suspend" : "Reactivate"}</Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {creating && <NewOrg onClose={() => setCreating(false)} />}
    </div>
  );
}
