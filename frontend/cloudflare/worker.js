// Cloudflare Worker for HRP Digital Marketing Studio (UAT: uat-dm.hrpai.in, production: dm.hrpai.in).
//
// Serves the React app (static assets, SPA fallback) and forwards the API and the public pages to Cloud Run:
//   /api/*   the REST API (sign-in, studio, OAuth callbacks, signed media links)
//   /r/*     tracked short links            /l/*  link-in-bio pages            /f/*  "follow us" pages
// Every forwarded request carries X-Origin-Verify (Worker secret ORIGIN_VERIFY_SECRET); the API refuses requests
// without it, so its Cloud Run URL can't be used directly.

const API_PREFIXES = ["/api/", "/r/", "/l/", "/f/"];

const SECURITY_HEADERS = {
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

function withHeaders(res, extra) {
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries({ ...SECURITY_HEADERS, ...extra })) if (!out.headers.has(k)) out.headers.set(k, v);
  return out;
}

async function forward(request, env, url) {
  const origin = (env.API_ORIGIN || "").replace(/\/+$/, "");
  if (!origin || !env.ORIGIN_VERIFY_SECRET) {
    return new Response("The API isn't configured for this environment yet.", { status: 503 });
  }
  const target = origin + url.pathname + url.search;
  const headers = new Headers(request.headers);
  headers.set("X-Origin-Verify", env.ORIGIN_VERIFY_SECRET);
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", "https");
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) headers.set("X-Forwarded-For", ip);
  headers.delete("Host");
  const init = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
  try {
    const res = await fetch(target, init);
    return withHeaders(res, url.pathname.startsWith("/api/") ? { "X-Robots-Tag": "noindex" } : {});
  } catch (e) {
    return new Response("The studio's API didn't answer. Please try again in a moment.", { status: 502 });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (API_PREFIXES.some((p) => url.pathname.startsWith(p))) return forward(request, env, url);
    // Static app: hashed assets are cached for a year, index.html never (so a deploy shows up at once).
    const res = await env.ASSETS.fetch(request);
    const cache = url.pathname.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache";
    return withHeaders(res, { "Cache-Control": cache });
  },
};
