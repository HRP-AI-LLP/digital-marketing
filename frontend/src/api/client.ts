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
export type SessionUser = {
  id: string; email: string; full_name: string; is_platform_admin: boolean; must_change_password: boolean; totp_enabled?: boolean;
};
export type Session = {
  user: SessionUser; organization: Organization | null; permissions: string[]; memberships: Membership[];
  /** The organization requires two-step sign-in and this person hasn't set it up yet. */
  two_factor_setup_required?: boolean;
};
export type Tokens = Session & { access_token: string; refresh_token: string; expires_in: number };
/** Returned by /auth/login instead of tokens when two-step sign-in is on. */
export type TwoFactorChallenge = { two_factor_required: true; challenge: string; expires_in: number };

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

/** POST that returns a file (PDF reports). Resolves with the blob and the server's file name. */
export async function apiFile(path: string, json: unknown, retry = true): Promise<{ blob: Blob; filename: string }> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API}${path}`, { method: "POST", headers, body: JSON.stringify(json) });
  if (res.status === 401 && retry && (await refreshSession())) return apiFile(path, json, false);
  if (!res.ok) throw await parseError(res);
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd) || /filename="([^"]+)"/.exec(cd);
  return { blob: await res.blob(), filename: m ? decodeURIComponent(m[1]) : "report.pdf" };
}

/** "Chrome on Windows": the name shown in Account → Security and in new-sign-in emails. */
export function deviceName(): string {
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone/.test(ua) ? "iPhone"
    : /iPad/.test(ua) ? "iPad" : /Mac OS/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

/** Re-read the session (after turning on two-step sign-in, for example). */
export async function reloadSession(): Promise<Session> {
  const s = await api<Session>("/auth/me");
  onSessionChange(s);
  return s;
}

/** GET a file (image, zip…) with the signed-in user's token, as a Blob. */
export async function apiBlob(path: string, retry = true): Promise<Blob> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API}${path}`, { headers });
  if (res.status === 401 && retry && (await refreshSession())) return apiBlob(path, false);
  if (!res.ok) throw await parseError(res);
  return res.blob();
}

/** GET a file with the signed-in user's token and hand it to the browser as a download. */
export async function downloadFile(path: string, filename: string, retry = true): Promise<void> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${API}${path}`, { headers });
  if (res.status === 401 && retry && (await refreshSession())) return downloadFile(path, filename, false);
  if (!res.ok) throw await parseError(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const auth = {
  /** Signs in, or returns the challenge for the second step when two-step sign-in is on. */
  login: async (email: string, password: string): Promise<Session | TwoFactorChallenge> => {
    const r = await api<Tokens | TwoFactorChallenge>("/auth/login", { method: "POST", json: { email, password, device_name: deviceName() } }, false);
    return "two_factor_required" in r ? r : applyTokens(r);
  },
  loginSecondStep: async (challenge: string, answer: { code?: string; recovery_code?: string }) =>
    applyTokens(await api<Tokens>("/auth/login/2fa", { method: "POST", json: { challenge, ...answer } }, false)),
  signOutOthers: async () => {
    const r = await api<{ signed_out: number; tokens: Tokens }>("/account/sessions/sign-out-others", { method: "POST" });
    applyTokens(r.tokens);
    return r.signed_out;
  },
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
