// Thin fetch wrapper: bearer access token in memory, refresh token in localStorage,
// automatic one-time refresh on 401. Same contract the Android/iOS apps will use.

export const API = "/api/v1";
const REFRESH_KEY = "dm.refresh";

export type Organization = {
  id: string; name: string; slug: string; timezone: string; website: string | null;
  status: string; plan: string; created_at: string;
  legal_name?: string | null; tagline?: string | null; description?: string | null; industry?: string | null;
  company_size?: string | null; founded_year?: number | null; email?: string | null; phone?: string | null;
  country?: string | null; currency?: string; address?: Record<string, string>; registration?: Record<string, string>;
  social_links?: Record<string, string>; contacts?: OrgContact[];
  logo_asset_id?: string | null; logo_dark_asset_id?: string | null; logo_url?: string | null; logo_dark_url?: string | null;
};
export type OrgContact = { name: string; role?: string | null; email?: string | null; phone?: string | null };
export type Membership = { organization_id: string; organization_name: string; organization_slug: string; roles: string[] };
export type SessionUser = { id: string; email: string; full_name: string; is_platform_admin: boolean; must_change_password: boolean };
export type Session = { user: SessionUser; organization: Organization | null; permissions: string[]; memberships: Membership[] };
export type Tokens = Session & { access_token: string; refresh_token: string; expires_in: number };

let accessToken: string | null = null;
let onSessionChange: (s: Session | null) => void = () => {};

export function bindSession(cb: (s: Session | null) => void) { onSessionChange = cb; }

function readRefresh(): string | null {
  try { return localStorage.getItem(REFRESH_KEY); } catch { return null; }
}
function writeRefresh(v: string | null) {
  try { v ? localStorage.setItem(REFRESH_KEY, v) : localStorage.removeItem(REFRESH_KEY); } catch { /* storage unavailable */ }
}

export function applyTokens(t: Tokens): Session {
  accessToken = t.access_token;
  writeRefresh(t.refresh_token);
  const { access_token: _a, refresh_token: _r, expires_in: _e, ...session } = t;
  onSessionChange(session);
  return session;
}

export function clearTokens() {
  accessToken = null;
  writeRefresh(null);
  onSessionChange(null);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function parseError(res: Response): Promise<ApiError> {
  let msg = `Request failed (${res.status}).`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") msg = body.detail;
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      const d = body.detail[0];
      const field = d.loc?.[d.loc.length - 1];
      msg = field ? `${String(field).replace(/_/g, " ")}: ${d.msg}` : d.msg;
    }
  } catch { /* not JSON */ }
  return new ApiError(res.status, msg);
}

let refreshing: Promise<boolean> | null = null;

export async function refreshSession(): Promise<boolean> {
  const rt = readRefresh();
  if (!rt) return false;
  refreshing ??= (async () => {
    const res = await fetch(`${API}/auth/refresh`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) { clearTokens(); return false; }
    applyTokens(await res.json());
    return true;
  })().finally(() => { refreshing = null; });
  return refreshing;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API}${path}`, {
    ...init, headers, body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (res.status === 401 && retry && (await refreshSession())) return api<T>(path, init, false);
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const auth = {
  login: async (email: string, password: string) =>
    applyTokens(await api<Tokens>("/auth/login", { method: "POST", json: { email, password, device_name: "Web" } }, false)),
  logout: async () => {
    const rt = readRefresh();
    if (rt) await api("/auth/logout", { method: "POST", json: { refresh_token: rt } }, false).catch(() => {});
    clearTokens();
  },
  changePassword: async (current_password: string, new_password: string) =>
    applyTokens(await api<Tokens>("/auth/change-password", { method: "POST", json: { current_password, new_password } })),
  switchOrg: async (organization_id: string) =>
    applyTokens(await api<Tokens>("/auth/switch-organization", {
      method: "POST", json: { organization_id, refresh_token: readRefresh() },
    })),
};
