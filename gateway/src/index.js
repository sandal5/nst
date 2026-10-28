// nst fetch gateway — a scoped, key-protected fetch proxy for the project.
//
// Why this exists: the assistant runtime asks the user to approve network
// access per destination host. The project registry holds thousands of
// official hosts, so per-host approval does not scale. All project fetches
// go through this single gateway host instead: the user approves the
// gateway once, and the gateway fetches target pages server-side.
//
// Scope locks (this is NOT an open proxy):
//  1. Requests must carry the shared secret in the X-Gateway-Key header
//     (Worker secret GATEWAY_KEY).
//  2. The target host must be in the project allowlist (allowlist.js,
//     generated from the registry domain list). Anything else is refused.
// Politeness rules (robots-first, per-host pacing) stay enforced by the
// calling client, exactly as with direct fetches. Outbound requests use a
// rotating pool of common browser User-Agents (project decision 2026-10-27);
// a 403/429 from a target still stops that host — rotation is the uniform
// default identity, never a per-block evasion retry.

import { ALLOWED_HOSTS } from "./allowlist.js";
import { STATUS_PAGE_HTML } from "./status_page.js";
import { TEMPLATES_PAGE_HTML } from "./templates_page.js";

// Front-door posture (project decision 2026-10-28): the gateway host must
// look like a freshly installed stock web server. The root serves the
// classic default page, robots.txt disallows everything, and every path
// other than the API and the owner's unlisted status path redirects to
// the root. Nothing on the public surface links to or names the status
// path; it is reachable only by typing it directly.
const NGINX_PAGE_HTML = `<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
html { color-scheme: light dark; }
body { width: 35em; margin: 0 auto;
font-family: Tahoma, Verdana, Arial, sans-serif; }
</style>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>

<p><em>Thank you for using nginx.</em></p>
</body>
</html>`;

const ROBOTS_TXT = "User-agent: *\nDisallow: /\n";

function htmlPage(html, extraHeaders = {}) {
  return new Response(html, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      ...extraHeaders,
    },
  });
}

const MAX_BODY = 8 * 1024 * 1024;

// Common desktop browser User-Agents, rotated per request.
const UA_POOL = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
];

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function hostAllowed(hostname) {
  if (ALLOWED_HOSTS.has(hostname)) return true;
  // tolerate www.<->bare-domain differences between registry and live URLs
  const alt = hostname.startsWith("www.") ? hostname.slice(4) : "www." + hostname;
  return ALLOWED_HOSTS.has(alt);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/robots.txt") {
      return new Response(ROBOTS_TXT, {
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return htmlPage(NGINX_PAGE_HTML);
    }
    // Owner's task-status page. Unlisted: never linked, never named in any
    // other response, disallowed by robots.txt, and served with noindex.
    if (url.pathname === "/sandal") {
      return htmlPage(STATUS_PAGE_HTML, {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    // Sub-page of the status page (template classes x dimensions). Same
    // unlisted area: linked only from the status page itself.
    if (url.pathname === "/sandal/templates") {
      return htmlPage(TEMPLATES_PAGE_HTML, {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    if (url.pathname !== "/fetch") {
      return new Response(null, {
        status: 301,
        headers: { location: "/" },
      });
    }
    if (!env.GATEWAY_KEY || request.headers.get("X-Gateway-Key") !== env.GATEWAY_KEY) {
      return json({ error: "unauthorized" }, 401);
    }
    const target = url.searchParams.get("url");
    if (!target) return json({ error: "missing url parameter" }, 400);
    let t;
    try {
      t = new URL(target);
    } catch {
      return json({ error: "bad url" }, 400);
    }
    if (t.protocol !== "http:" && t.protocol !== "https:") {
      return json({ error: "only http/https targets" }, 400);
    }
    if (!hostAllowed(t.hostname)) {
      return json({ error: "host not in project allowlist", host: t.hostname }, 403);
    }
    let resp;
    try {
      resp = await fetch(t.toString(), {
        method: "GET",
        redirect: "follow",
        headers: {
          "User-Agent": UA_POOL[Math.floor(Math.random() * UA_POOL.length)],
          Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        },
      });
    } catch (e) {
      return json({ error: "fetch failed", detail: String(e).slice(0, 200) }, 502);
    }
    const buf = await resp.arrayBuffer();
    if (buf.byteLength > MAX_BODY) return json({ error: "body too large" }, 502);
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return json({
      status: resp.status,
      final_url: resp.url,
      content_type: resp.headers.get("content-type"),
      etag: resp.headers.get("etag"),
      last_modified: resp.headers.get("last-modified"),
      body_base64: btoa(bin),
    });
  },
};
