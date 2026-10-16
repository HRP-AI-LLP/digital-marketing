import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CheckCircle2, ExternalLink, MapPin, Plus, RefreshCw, Settings2, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  Alert, Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, Spinner, StatusBadge, Toggle, formatDate,
} from "../components/ui";
import { Markdown } from "./social/Markdown";

export type SocialAccount = {
  id: string; platform: string; platform_label: string; name: string; handle: string | null; avatar_url: string | null;
  profile_url: string | null; status: string; status_message: string | null; token_expires_at: string | null;
  settings: Record<string, any>; last_checked_at: string | null; created_at: string;
};
type Connector = {
  id: string; label: string; method: "oauth" | "app_password" | "token" | "manual"; platforms: string[]; blurb: string;
  app_provider: string | null; app_label: string | null; app_configured: boolean; redirect_uri: string | null;
  needs_public_media: boolean; guide: string; options: { name: string; label: string; default: boolean }[];
};
type PlatformsInfo = {
  connectors: Connector[]; media_host_configured: boolean; media_guide: string; intro: string; redirect_base: string;
};
type Location = { name: string; account_name: string | null; title: string | null; address: string; connected: boolean };
type Board = { id: string; name: string; privacy: string | null };

const KEY = ["social-accounts"];
const SOON_MS = 7 * 24 * 3600 * 1000;

function useAccounts() {
  return useQuery({ queryKey: KEY, queryFn: () => api<SocialAccount[]>("/social/accounts?all=true") });
}

function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-2.5 py-1.5">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" title={value}>{value}</code>
      <button
        type="button"
        className="shrink-0 text-xs font-medium text-primary-700 hover:underline"
        onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Avatar({ acc }: { acc: SocialAccount }) {
  const [broken, setBroken] = useState(false);
  if (acc.avatar_url && !broken) {
    return <img src={acc.avatar_url} alt="" onError={() => setBroken(true)} className="h-10 w-10 shrink-0 rounded-full border border-line object-cover" />;
  }
  const initials = acc.name.replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-50 text-sm font-semibold text-primary-700">{initials || "?"}</div>;
}

function Guide({ text, label = "Setup guide" }: { text: string; label?: string }) {
  if (!text) return null;
  return (
    <details className="group mt-3 rounded-lg border border-line">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium text-primary-700 hover:bg-primary-50/60">
        <BookOpen size={15} /> {label}
        <span className="ml-auto text-xs text-muted group-open:hidden">Show</span>
        <span className="ml-auto hidden text-xs text-muted group-open:inline">Hide</span>
      </summary>
      <div className="max-h-[28rem] overflow-y-auto border-t border-line px-4 py-3"><Markdown text={text} skipFirstHeading /></div>
    </details>
  );
}

// ------------------------------------------------------------------ account settings
function SettingsModal({ acc, onClose }: { acc: SocialAccount; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(acc.name);
  const [settings, setSettings] = useState<Record<string, any>>({ ...acc.settings });
  const boards = useQuery({
    queryKey: ["pinterest-boards", acc.id],
    queryFn: () => api<{ boards: Board[] }>(`/social/accounts/${acc.id}/boards`),
    enabled: acc.platform === "pinterest",
    retry: false,
  });
  const save = useMutation({
    mutationFn: () => api(`/social/accounts/${acc.id}`, { method: "PATCH", json: { name, settings } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  const set = (k: string, v: unknown) => setSettings({ ...settings, [k]: v });
  return (
    <Modal title={`Settings for ${acc.name}`} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <Field label="Display name" hint="Only used inside this app."><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        {acc.platform === "pinterest" && (
          <>
            <Field label="Default board" hint="New pins go to this board.">
              {boards.isLoading ? <Spinner label="Loading boards…" /> : (
                <Select value={settings.board_id ?? ""} onChange={(e) => {
                  const b = boards.data?.boards.find((x) => x.id === e.target.value);
                  setSettings({ ...settings, board_id: e.target.value || null, board_name: b?.name ?? null });
                }}>
                  <option value="">Choose a board</option>
                  {boards.data?.boards.map((b) => <option key={b.id} value={b.id}>{b.name}{b.privacy === "SECRET" ? " (secret)" : ""}</option>)}
                </Select>
              )}
            </Field>
            {boards.error && <Alert>{(boards.error as Error).message}</Alert>}
            <Toggle checked={!!settings.sandbox} onChange={(v) => set("sandbox", v)} label="Use the sandbox API (only if Pinterest says your Trial app must)" />
          </>
        )}
        {acc.platform === "youtube" && (
          <Field label="Visibility of new Shorts" hint="Uploads from unverified Google projects may stay private until Google audits the project.">
            <Select value={settings.privacy ?? "public"} onChange={(e) => set("privacy", e.target.value)}>
              <option value="public">Public</option>
              <option value="unlisted">Unlisted</option>
              <option value="private">Private</option>
            </Select>
          </Field>
        )}
        {acc.platform === "gbp" && (
          <Field label="Post language" hint="Language code for Business Profile posts, for example en or hi.">
            <Input value={settings.language ?? "en"} onChange={(e) => set("language", e.target.value)} maxLength={10} />
          </Field>
        )}
        {acc.platform === "mastodon" && (
          <Field label="Post visibility">
            <Select value={settings.visibility ?? "public"} onChange={(e) => set("visibility", e.target.value)}>
              <option value="public">Public</option>
              <option value="unlisted">Unlisted</option>
              <option value="private">Followers only</option>
            </Select>
          </Field>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>Save settings</Button>
        </div>
      </form>
    </Modal>
  );
}

function RemoveModal({ acc, onClose }: { acc: SocialAccount; onClose: () => void }) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api(`/social/accounts/${acc.id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  return (
    <Modal title={`Remove ${acc.name}?`} onClose={onClose}>
      <div className="space-y-3 text-sm">
        <p>The app deletes its stored access and stops its posting schedules. Posts already made stay in the app and on the platform.</p>
        {acc.platform === "google" && <Alert tone="warn">Business Profile, YouTube, Analytics and Search Console stop working until you connect Google again.</Alert>}
        <p className="text-muted">To fully revoke access, also remove this app in the platform's own settings.</p>
        {remove.error && <Alert>{(remove.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>Remove account</Button>
        </div>
      </div>
    </Modal>
  );
}

function AccountRow({ acc, canManage }: { acc: SocialAccount; canManage: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const check = useMutation({
    mutationFn: () => api<SocialAccount>(`/social/accounts/${acc.id}/refresh`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: KEY }),
  });
  const expires = acc.token_expires_at ? new Date(acc.token_expires_at).getTime() : null;
  const soon = expires !== null && expires - Date.now() < SOON_MS;
  const hasSettings = acc.platform !== "x" && acc.platform !== "manual";
  return (
    <li className="flex flex-wrap items-start gap-3 py-4">
      <Avatar acc={acc} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{acc.name}</span>
          <StatusBadge status={acc.status} />
          {acc.profile_url && (
            <a href={acc.profile_url} target="_blank" rel="noreferrer" className="text-muted hover:text-primary-700" aria-label={`Open ${acc.name} profile`}>
              <ExternalLink size={14} />
            </a>
          )}
        </div>
        <div className="mt-0.5 text-sm text-muted">
          {acc.handle && <>{acc.handle} · </>}{acc.platform_label}
          {acc.platform === "pinterest" && (acc.settings.board_name ? <> · Board: {acc.settings.board_name}</> : <> · <span className="text-danger">No board chosen</span></>)}
          {acc.platform === "youtube" && <> · New Shorts: {acc.settings.privacy ?? "public"}</>}
        </div>
        {acc.status_message && (
          <p className={`mt-1 text-sm ${acc.status === "expired" || acc.status === "error" ? "text-danger" : "text-muted"}`}>{acc.status_message}</p>
        )}
        <p className="mt-1 text-xs text-muted">
          {expires !== null && <span className={soon ? "font-medium text-danger" : ""}>Access until {formatDate(acc.token_expires_at)}{soon ? " (reconnect soon)" : ""} · </span>}
          {acc.status === "manual" ? "Publish by hand" : acc.last_checked_at ? `Checked ${formatDate(acc.last_checked_at)}` : `Connected ${formatDate(acc.created_at)}`}
        </p>
        {check.error && <p className="mt-1 text-sm text-danger">{(check.error as Error).message}</p>}
      </div>
      {canManage && (
        <div className="flex items-center gap-1">
          {acc.status !== "manual" && (
            <Button variant="secondary" onClick={() => check.mutate()} disabled={check.isPending} title="Refresh access and check the account works">
              <RefreshCw size={14} className={check.isPending ? "animate-spin" : ""} /> {check.isPending ? "Checking…" : "Check now"}
            </Button>
          )}
          {hasSettings && <Button variant="ghost" onClick={() => setEditing(true)} aria-label={`Settings for ${acc.name}`}><Settings2 size={15} /></Button>}
          <Button variant="ghost" onClick={() => setRemoving(true)} aria-label={`Remove ${acc.name}`}><Trash2 size={15} /></Button>
        </div>
      )}
      {editing && <SettingsModal acc={acc} onClose={() => setEditing(false)} />}
      {removing && <RemoveModal acc={acc} onClose={() => setRemoving(false)} />}
    </li>
  );
}

// ------------------------------------------------------------------ Google
function LocationsModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["gbp-locations"], queryFn: () => api<{ locations: Location[] }>("/social/google/locations"), retry: false });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const save = useMutation({
    mutationFn: () => api("/social/google/locations", { method: "POST", json: [...picked] }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  const toggle = (n: string) => { const s = new Set(picked); if (s.has(n)) s.delete(n); else s.add(n); setPicked(s); };
  return (
    <Modal title="Choose Business Profile locations" onClose={onClose} wide>
      <div className="space-y-4">
        {q.isLoading && <Spinner label="Loading your locations…" />}
        {q.error && <Alert>{(q.error as Error).message}</Alert>}
        {q.data && !q.data.locations.length && (
          <p className="text-sm text-muted">No locations found. Check that this Google account manages a Business Profile and that Google approved your Business Profile API access.</p>
        )}
        <ul className="max-h-[50vh] divide-y divide-line overflow-y-auto">
          {q.data?.locations.map((l) => (
            <li key={l.name} className="flex items-start gap-3 py-2.5">
              <input type="checkbox" className="mt-1 h-4 w-4 accent-primary-600" id={l.name} disabled={l.connected}
                checked={l.connected || picked.has(l.name)} onChange={() => toggle(l.name)} />
              <label htmlFor={l.name} className="min-w-0 flex-1 text-sm">
                <span className="font-medium">{l.title ?? l.name}</span>{l.connected && <span className="ml-2"><Badge tone="brand">Added</Badge></span>}
                <span className="block text-muted">{[l.address, l.account_name].filter(Boolean).join(" · ")}</span>
              </label>
            </li>
          ))}
        </ul>
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => save.mutate()} disabled={!picked.size || save.isPending}>Add {picked.size || ""} {picked.size === 1 ? "location" : "locations"}</Button>
        </div>
      </div>
    </Modal>
  );
}

function GoogleConnection({ holder, canManage }: { holder: SocialAccount; canManage: boolean }) {
  const [locations, setLocations] = useState(false);
  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">Google connection</h3>
          <p className="text-sm text-muted">
            Signed in as {holder.handle ?? holder.name}. Used for Business Profile posts, YouTube Shorts, Google Analytics and Search Console.
          </p>
        </div>
        {canManage && <Button variant="secondary" onClick={() => setLocations(true)}><MapPin size={15} /> Choose Business Profile locations</Button>}
      </div>
      <ul className="mt-2 divide-y divide-line"><AccountRow acc={holder} canManage={canManage} /></ul>
      {locations && <LocationsModal onClose={() => setLocations(false)} />}
    </Card>
  );
}

// ------------------------------------------------------------------ connect forms
function BlueskyModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [service, setService] = useState("");
  const add = useMutation({
    mutationFn: () => api("/social/accounts/bluesky", { method: "POST", json: { handle, app_password: password, service: service || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  return (
    <Modal title="Add a Bluesky account" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        {add.error && <Alert>{(add.error as Error).message}</Alert>}
        <Field label="Handle"><Input required value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="hrpai.bsky.social" /></Field>
        <Field label="App password" hint="Create one in Bluesky under Settings → Privacy and security → App passwords. Don't use your main password.">
          <Input required type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="xxxx-xxxx-xxxx-xxxx" />
        </Field>
        <Field label="Server (optional)" hint="Only if your account isn't hosted on bsky.social.">
          <Input value={service} onChange={(e) => setService(e.target.value)} placeholder="https://bsky.social" />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={add.isPending}>{add.isPending ? "Signing in…" : "Add account"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function MastodonModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [instance, setInstance] = useState("");
  const [token, setToken] = useState("");
  const add = useMutation({
    mutationFn: () => api("/social/accounts/mastodon", { method: "POST", json: { instance_url: instance, access_token: token } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  return (
    <Modal title="Add a Mastodon account" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        {add.error && <Alert>{(add.error as Error).message}</Alert>}
        <Field label="Server address"><Input required value={instance} onChange={(e) => setInstance(e.target.value)} placeholder="https://mastodon.social" /></Field>
        <Field label="Access token" hint="Preferences → Development → New application, with read:accounts, read:statuses, write:statuses and write:media.">
          <Input required type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={add.isPending}>{add.isPending ? "Checking…" : "Add account"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function ManualModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [platform, setPlatform] = useState("x");
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const add = useMutation({
    mutationFn: () => api("/social/accounts/manual", { method: "POST", json: { platform, name, handle: handle || null, profile_url: profileUrl || null } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: KEY }); onClose(); },
  });
  return (
    <Modal title="Add a manual account" onClose={onClose}>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
        {add.error && <Alert>{(add.error as Error).message}</Alert>}
        <p className="text-sm text-muted">Posts for this account are prepared as usual and wait in Manual publish, ready to copy and post by hand.</p>
        <Field label="Platform">
          <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="x">X (Twitter)</option>
            <option value="manual">Other</option>
          </Select>
        </Field>
        <Field label="Name"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder={platform === "x" ? "HRP AI on X" : "HRP AI on Quora"} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Handle (optional)"><Input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@hrpai" /></Field>
          <Field label="Profile link (optional)"><Input type="url" value={profileUrl} onChange={(e) => setProfileUrl(e.target.value)} placeholder="https://" /></Field>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={add.isPending}>Add account</Button>
        </div>
      </form>
    </Modal>
  );
}

const APP_FIELDS: Record<string, { id: string; secret: string; idLabel: string; secretLabel: string; where: string }> = {
  meta_app: { id: "client_id", secret: "client_secret", idLabel: "App ID", secretLabel: "App secret",
    where: "developers.facebook.com → My Apps → your app → App settings → Basic" },
  linkedin_app: { id: "client_id", secret: "client_secret", idLabel: "Client ID", secretLabel: "Client secret",
    where: "linkedin.com/developers/apps → your app → Auth" },
  pinterest_app: { id: "client_id", secret: "client_secret", idLabel: "App ID", secretLabel: "App secret key",
    where: "developers.pinterest.com/apps → your app" },
  google_app: { id: "client_id", secret: "client_secret", idLabel: "Client ID", secretLabel: "Client secret",
    where: "console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client IDs (type: Web application)" },
};

function AppSetupModal({ c, onClose }: { c: Connector; onClose: () => void }) {
  const qc = useQueryClient();
  const f = APP_FIELDS[c.app_provider ?? ""] ?? APP_FIELDS.meta_app;
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [pagesId, setPagesId] = useState("");
  const [pagesSecret, setPagesSecret] = useState("");
  const isLinkedIn = c.app_provider === "linkedin_app";
  const save = useMutation({
    mutationFn: async () => {
      const existing = (await api<{ id: string; provider: string }[]>("/integrations/providers"))
        .find((p) => p.provider === c.app_provider);
      const secrets: Record<string, string> = {};
      if (clientId.trim()) secrets[f.id] = clientId.trim();
      if (secret.trim()) secrets[f.secret] = secret.trim();
      if (pagesId.trim()) secrets.pages_client_id = pagesId.trim();
      if (pagesSecret.trim()) secrets.pages_client_secret = pagesSecret.trim();
      if (existing) return api(`/integrations/providers/${existing.id}`, { method: "PATCH", json: { secrets, enabled: true } });
      return api("/integrations/providers", { method: "POST", json: { provider: c.app_provider, secrets } });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["social-platforms"] });
      qc.invalidateQueries({ queryKey: ["int-providers"] });
      onClose();
    },
  });
  return (
    <Modal title={`Set up your ${c.app_label ?? "developer app"}`} onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
        <p className="text-sm text-muted">
          Signing in to {c.label} goes through a free developer app that you create once. Follow the setup guide on the card, then paste
          the app's credentials here. You'll find them at: {f.where}.
        </p>
        {c.redirect_uri && (
          <div>
            <div className="mb-1 text-sm font-medium">Redirect URI to add to the app</div>
            <CopyField value={c.redirect_uri} />
          </div>
        )}
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={f.idLabel}><Input required autoComplete="off" value={clientId} onChange={(e) => setClientId(e.target.value)} /></Field>
          <Field label={f.secretLabel}><Input required type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
        </div>
        {isLinkedIn && (
          <div className="rounded-lg border border-line p-4">
            <div className="font-medium">Company pages app (optional)</div>
            <p className="mt-1 text-sm text-muted">
              LinkedIn only allows the Community Management API on an app that has no other products. To post on your company page,
              create a second LinkedIn app with only that product, add the same redirect URI, request access, and paste its credentials
              here. Then switch on "Include company pages" before connecting.
            </p>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Field label="Client ID"><Input autoComplete="off" value={pagesId} onChange={(e) => setPagesId(e.target.value)} /></Field>
              <Field label="Client secret"><Input type="password" autoComplete="off" value={pagesSecret} onChange={(e) => setPagesSecret(e.target.value)} /></Field>
            </div>
          </div>
        )}
        <p className="text-xs text-muted">Stored encrypted on this machine. You can change it later in Integrations → Social apps and Google.</p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save and continue"}</Button>
        </div>
      </form>
    </Modal>
  );
}

// Platforms a connected card can still add, and the connect option that turns each one on.
const ADDABLE: Record<string, { option: string; label: string }> = {
  instagram: { option: "include_instagram", label: "Add Instagram" },
  linkedin_org: { option: "company_pages", label: "Add a LinkedIn company page" },
};

function ConnectedCard({ c, connected, onAddAnother, onAdd }: {
  c: Connector; connected: SocialAccount[]; onAddAnother?: () => void; onAdd?: (option: string) => void;
}) {
  const have = new Set(connected.map((a) => a.platform));
  const addable = c.platforms.filter((p) => !have.has(p) && ADDABLE[p] && c.options.some((o) => o.name === ADDABLE[p].option));
  return (
    <Card className="flex flex-col border-emerald-200 bg-emerald-50/40">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold">{c.label}</h3>
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800">
          <CheckCircle2 size={13} /> Connected
        </span>
      </div>
      <p className="mt-2 text-sm text-emerald-900">Successfully connected and verified.</p>
      <ul className="mt-3 space-y-1.5 text-sm">
        {connected.map((a) => (
          <li key={a.id} className="flex items-center gap-2">
            <CheckCircle2 size={14} className="shrink-0 text-emerald-600" />
            <span className="truncate font-medium">{a.platform === "google" ? (a.handle ?? a.name) : a.name}</span>
            <span className="shrink-0 text-muted">· {a.platform === "google" ? "Google sign-in" : a.platform_label}</span>
          </li>
        ))}
      </ul>
      {onAdd && addable.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {addable.map((p) => (
            <Button key={p} variant="secondary" onClick={() => onAdd(ADDABLE[p].option)}>
              <Plus size={15} /> {ADDABLE[p].label}
            </Button>
          ))}
        </div>
      )}
      {onAddAnother && (
        <div className="mt-auto pt-4">
          <button type="button" onClick={onAddAnother} className="text-xs text-muted hover:text-primary-700 hover:underline">
            Connect a different account
          </button>
        </div>
      )}
    </Card>
  );
}

function ConnectCard({ c, canManage, mediaReady, mediaGuide, connected }: {
  c: Connector; canManage: boolean; mediaReady: boolean; mediaGuide: string; connected: SocialAccount[];
}) {
  const [showConnect, setShowConnect] = useState(false);
  const [preset, setPreset] = useState<Record<string, boolean>>({});
  if (c.method !== "manual" && connected.length > 0 && !showConnect) {
    return <ConnectedCard c={c} connected={connected}
      onAddAnother={canManage ? () => { setPreset({}); setShowConnect(true); } : undefined}
      onAdd={canManage ? (option) => { setPreset({ [option]: true }); setShowConnect(true); } : undefined} />;
  }
  return <ConnectForm key={JSON.stringify(preset)} c={c} canManage={canManage} mediaReady={mediaReady} mediaGuide={mediaGuide}
    preset={preset} onBack={showConnect ? () => setShowConnect(false) : undefined} />;
}

function ConnectForm({ c, canManage, mediaReady, mediaGuide, onBack, preset = {} }: {
  c: Connector; canManage: boolean; mediaReady: boolean; mediaGuide: string; onBack?: () => void; preset?: Record<string, boolean>;
}) {
  const [opts, setOpts] = useState<Record<string, boolean>>({ ...Object.fromEntries(c.options.map((o) => [o.name, o.default])), ...preset });
  const [form, setForm] = useState(false);
  const [setup, setSetup] = useState(false);
  const start = useMutation({
    mutationFn: () => api<{ authorize_url: string }>(`/social/connect/${c.id}`, { method: "POST", json: opts }),
    onSuccess: (d) => { window.location.href = d.authorize_url; },
  });
  const needsApp = c.method === "oauth" && !c.app_configured;
  const action = () => {
    if (needsApp) setSetup(true);
    else if (c.method === "oauth") start.mutate();
    else setForm(true);
  };
  const buttonLabel = needsApp ? "Set up app" : c.method === "oauth" ? (c.id === "google" ? "Connect Google" : `Connect ${c.label}`) : c.method === "manual" ? "Add manual account" : `Add ${c.label}`;
  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold">{c.label}</h3>
        {c.id === "manual" && <Badge>Publish by hand</Badge>}
        {onBack && <button type="button" onClick={onBack} className="text-xs text-muted hover:text-primary-700 hover:underline">Cancel</button>}
      </div>
      <p className="mt-1 text-sm text-muted">{c.blurb}</p>
      {c.method === "oauth" && c.options.length > 0 && canManage && (
        <div className="mt-3 space-y-2">
          {c.options.map((o) => (
            <Toggle key={o.name} checked={!!opts[o.name]} onChange={(v) => setOpts({ ...opts, [o.name]: v })} label={o.label} />
          ))}
        </div>
      )}
      {c.redirect_uri && (
        <div className="mt-3">
          <div className="mb-1 text-xs text-muted">Redirect URI to register in the developer app</div>
          <CopyField value={c.redirect_uri} />
        </div>
      )}
      {c.method === "oauth" && !c.app_configured && (
        <p className="mt-3 text-sm text-amber-800">
          Connecting needs your {c.app_label ?? "developer app"} first. Press <span className="font-medium">Set up app</span> and paste its ID and secret (see the setup guide below).
        </p>
      )}
      {c.needs_public_media && !mediaReady && (
        <p className="mt-2 text-sm text-amber-800">
          Instagram, Threads and Business Profile fetch media from a public link. Set up Cloudinary or a public URL in{" "}
          <Link to="/settings/integrations" className="font-medium underline">Integrations</Link> before publishing.
        </p>
      )}
      {start.error && <div className="mt-3"><Alert>{(start.error as Error).message}</Alert></div>}
      <div className="mt-auto pt-4">
        {canManage && (
          <Button onClick={action} disabled={start.isPending} variant={c.method === "manual" || needsApp ? "secondary" : "primary"}>
            <Plus size={15} /> {start.isPending ? "Opening sign-in…" : buttonLabel}
          </Button>
        )}
        <Guide text={c.guide} />
        {c.needs_public_media && <Guide text={mediaGuide} label="Public media hosting guide" />}
      </div>
      {setup && <AppSetupModal c={c} onClose={() => setSetup(false)} />}
      {form && c.id === "bluesky" && <BlueskyModal onClose={() => setForm(false)} />}
      {form && c.id === "mastodon" && <MastodonModal onClose={() => setForm(false)} />}
      {form && c.id === "manual" && <ManualModal onClose={() => setForm(false)} />}
    </Card>
  );
}

// ------------------------------------------------------------------ page
export default function SocialAccounts() {
  const { can } = useAuth();
  const canManage = can("integration.manage");
  const accounts = useAccounts();
  const info = useQuery({ queryKey: ["social-platforms"], queryFn: () => api<PlatformsInfo>("/social/platforms") });
  const [params, setParams] = useSearchParams();
  const [banner, setBanner] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    const connected = params.get("connected");
    const error = params.get("error");
    if (!connected && !error) return;
    if (error) setBanner({ tone: "danger", text: error });
    else {
      const n = Number(params.get("count") ?? "0");
      const label = { meta: "Facebook and Instagram", threads: "Threads", linkedin: "LinkedIn", pinterest: "Pinterest", google: "Google" }[connected!] ?? connected;
      setBanner({
        tone: "info",
        text: connected === "google"
          ? `Google connected${n ? ` with ${n} YouTube channel${n === 1 ? "" : "s"}` : ""}. Choose your Business Profile locations below.`
          : `${label} connected: ${n} account${n === 1 ? "" : "s"} added or updated. Remove any you don't want to post to.`,
      });
      qc.invalidateQueries({ queryKey: KEY });
    }
    setParams({}, { replace: true });
  }, [params, setParams, qc]);

  const all = accounts.data ?? [];
  const holder = all.find((a) => a.platform === "google");
  const visible = all.filter((a) => a.platform !== "google");
  const groups = useMemo(() => {
    const g = new Map<string, SocialAccount[]>();
    for (const a of visible) g.set(a.platform_label, [...(g.get(a.platform_label) ?? []), a]);
    return [...g.entries()];
  }, [visible]);
  const issues = visible.filter((a) => a.status === "expired" || a.status === "error").length;

  return (
    <div>
      <PageHeader
        title="Social accounts"
        description="Connect the pages and profiles the app posts to. Access is stored encrypted and checked every 6 hours."
      />
      {banner && (
        <div className="mb-4 flex items-start gap-2">
          <div className="flex-1"><Alert tone={banner.tone}>{banner.text}</Alert></div>
          <Button variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>
        </div>
      )}
      {accounts.error && <Alert>{(accounts.error as Error).message}</Alert>}
      {issues > 0 && (
        <div className="mb-4"><Alert tone="warn">{issues === 1 ? "One account needs" : `${issues} accounts need`} reconnecting. Posts for {issues === 1 ? "it" : "them"} can't be published until then.</Alert></div>
      )}

      <section className="mb-10">
        <h2 className="mb-3 text-lg font-semibold">Connected accounts</h2>
        {holder && <GoogleConnection holder={holder} canManage={canManage} />}
        {accounts.isLoading && <Spinner label="Loading accounts…" />}
        {accounts.data && !visible.length && (
          <EmptyState title="No accounts yet" body="Connect a platform below. Each platform's setup guide explains the developer app it needs." />
        )}
        <div className="space-y-4">
          {groups.map(([label, list]) => (
            <Card key={label} className="py-1">
              <div className="flex items-center justify-between pt-3 text-sm font-semibold text-muted">
                <span>{label}</span><span className="text-xs font-normal">{list.length}</span>
              </div>
              <ul className="divide-y divide-line">{list.map((a) => <AccountRow key={a.id} acc={a} canManage={canManage} />)}</ul>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-1 text-lg font-semibold">Connect an account</h2>
        <p className="mb-4 max-w-3xl text-sm text-muted">
          Platforms with sign-in need a free developer app of your own. Create it using the setup guide, add the redirect URI shown on the card to it, press Set up app to paste its ID and secret, then connect. Platforms that are already connected show only their status.
        </p>
        {info.isLoading && <Spinner label="Loading platforms…" />}
        {info.error && <Alert>{(info.error as Error).message}</Alert>}
        {info.data && (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {info.data.connectors.map((c) => (
                <ConnectCard key={c.id} c={c} canManage={canManage} mediaReady={info.data.media_host_configured} mediaGuide={info.data.media_guide}
                  connected={all.filter((a) => a.status === "connected" && (c.platforms.includes(a.platform) || (c.id === "google" && a.platform === "google")))} />
              ))}
            </div>
            <Card className="mt-6">
              <h3 className="font-semibold">How connections work</h3>
              <Guide text={info.data.intro} label="Overview" />
            </Card>
          </>
        )}
      </section>
    </div>
  );
}
