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
import { TPL_PAGES } from "./tpl_pages.js";
import { OBJECTS_PAGE_HTML } from "./objects_page.js";

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

// ---------------------------------------------------------------------------
// Manual-check state sync (owner's objects-page checking workflow).
//
// Marks live in a Cloudflare KV namespace (binding CHECK_KV) as ONE JSON
// object under the key "marks_v1": { "<org|pref|domain>": mark }, where a
// mark is { s: "ok" | "re" | "ret", note, at, v, round, ack?, result?,
// new_target?, agent_note?, processed_at? }. Alignment is by (at, v):
// every mutation bumps v and stamps at; the assistant pulls with the
// gateway key, acks what it takes into processing (acked "re" marks lock),
// and writes results back as "ret" marks for the owner's final judgement.
//
// If the CHECK_KV binding is absent the API answers 501 and the objects
// page falls back to browser-local marks (CSV export) — nothing breaks.
//
// The write token below is NOT a secret (this repo is public); it only
// stops casual drive-by bots. Real protection is procedural: marks are
// leads, and every mark is verified before it can touch the registry.
// ---------------------------------------------------------------------------
const CHECK_TOKEN = "nst-check-2026";
const MARKS_KEY = "marks_v1";

async function loadMarks(env) {
  try {
    const raw = await env.CHECK_KV.get(MARKS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

async function saveMarks(env, marks) {
  await env.CHECK_KV.put(MARKS_KEY, JSON.stringify(marks));
}

function keyGuard(request, env) {
  return env.GATEWAY_KEY && request.headers.get("X-Gateway-Key") === env.GATEWAY_KEY;
}

async function checkApi(request, env, action) {
  if (!env.CHECK_KV) return json({ error: "kv_pending" }, 501);
  const marks = await loadMarks(env);
  if (action === "state") return json({ ok: true, marks });
  if (action === "pull") {
    if (!keyGuard(request, env)) return json({ error: "unauthorized" }, 401);
    return json({ ok: true, marks });
  }
  let body = {};
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad json" }, 400);
  }
  // Assistant-side operations (gateway key).
  if (action === "pageput") {
    if (!keyGuard(request, env)) return json({ error: "unauthorized" }, 401);
    const name = body.name || "";
    if (!/^(status|templates|objects|p[1-7])$/.test(name)) {
      return json({ error: "bad page name" }, 400);
    }
    if (typeof body.html !== "string" || !body.html) {
      return json({ error: "missing html" }, 400);
    }
    await env.CHECK_KV.put("page:" + name, body.html);
    return json({ ok: true, bytes: body.html.length });
  }
  if (action === "ack") {
    if (!keyGuard(request, env)) return json({ error: "unauthorized" }, 401);
    const now = new Date().toISOString();
    for (const k of body.keys || []) {
      const m = marks[k];
      if (m && m.s === "re" && !m.ack) {
        m.ack = true;
        m.ack_at = now;
        m.v = (m.v || 1) + 1;
      }
    }
    await saveMarks(env, marks);
    return json({ ok: true, marks });
  }
  if (action === "return") {
    if (!keyGuard(request, env)) return json({ error: "unauthorized" }, 401);
    const m = marks[body.key];
    if (!m) return json({ error: "no such mark" }, 404);
    marks[body.key] = {
      s: "ret",
      note: m.note || "",
      round: m.round || 1,
      v: (m.v || 1) + 1,
      at: m.at,
      result: body.result || "",
      new_target: body.new_target || "",
      agent_note: body.agent_note || "",
      processed_at: new Date().toISOString(),
    };
    await saveMarks(env, marks);
    return json({ ok: true, marks });
  }
  // Owner-side operations (write token from the checking page).
  if (request.headers.get("X-Check-Token") !== CHECK_TOKEN) {
    return json({ error: "unauthorized" }, 401);
  }
  if (action === "import") {
    const incoming = body.marks || {};
    for (const [k, m] of Object.entries(incoming)) {
      const cur = marks[k];
      if (!cur || (m.at || "") > (cur.at || "")) {
        marks[k] = {
          s: m.s === "re" ? "re" : "ok",
          note: m.note || "",
          at: m.at || new Date().toISOString(),
          v: m.v || 1,
          round: m.round || 1,
        };
      }
    }
    await saveMarks(env, marks);
    return json({ ok: true, marks });
  }
  if (action === "mark") {
    const now = new Date().toISOString();
    // Bulk "ok" (batch buttons): apply the ok transition to every key
    // where it is allowed (unmarked -> ok, returned -> closed).
    if (Array.isArray(body.keys)) {
      for (const k of body.keys) {
        const cur = marks[k];
        if (!cur) {
          marks[k] = { s: "ok", note: "", at: now, v: 1, round: 1 };
        } else if (cur.s === "ret") {
          delete marks[k];
        }
      }
      await saveMarks(env, marks);
      return json({ ok: true, marks });
    }
    const k = body.key;
    if (!k) return json({ error: "missing key" }, 400);
    const cur = marks[k];
    if (body.action === "undo") {
      if (!cur) return json({ ok: true, marks });
      if (cur.s === "ok" || (cur.s === "re" && !cur.ack)) {
        delete marks[k];
      } else {
        return json({ error: "locked", marks }, 409);
      }
    } else if (body.action === "ok" || body.action === "re") {
      if (!cur) {
        marks[k] = {
          s: body.action,
          note: body.note || "",
          at: now,
          v: 1,
          round: body.round || 1,
        };
      } else if (cur.s === "ret" && body.action === "ok") {
        delete marks[k]; // returned item judged correct: closed
      } else if (cur.s === "ret" && body.action === "re") {
        marks[k] = {
          s: "re",
          note: body.note || "",
          at: now,
          v: (cur.v || 1) + 1,
          round: (cur.round || 1) + 1,
        };
      } else {
        return json({ error: "already_marked", marks }, 409);
      }
    } else {
      return json({ error: "bad action" }, 400);
    }
    await saveMarks(env, marks);
    return json({ ok: true, marks });
  }
  return json({ error: "unknown action" }, 404);
}

async function kvPage(env, name, bundled) {
  // Generated pages are published to KV by the page generator (see
  // /sandal/api/pageput): the KV copy is the live one, so hourly page
  // updates do not depend on a Worker redeploy. The bundled copy is the
  // fallback for a cold KV.
  if (env && env.CHECK_KV) {
    try {
      const h = await env.CHECK_KV.get("page:" + name);
      if (h) return h;
    } catch {}
  }
  return bundled;
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
      return htmlPage(await kvPage(env, "status", STATUS_PAGE_HTML), {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    // Sub-page of the status page (template classes x dimensions). Same
    // unlisted area: linked only from the status page itself.
    if (url.pathname === "/sandal/templates") {
      return htmlPage(await kvPage(env, "templates", TEMPLATES_PAGE_HTML), {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    // Complete object list for the owner's manual checking (sibling of
    // the templates page, same unlisted area).
    if (url.pathname === "/sandal/objects") {
      return htmlPage(await kvPage(env, "objects", OBJECTS_PAGE_HTML), {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    // Per-class explanation pages under the templates page.
    const tplMatch = url.pathname.match(/^\/sandal\/templates\/(p[1-7])$/);
    if (tplMatch && TPL_PAGES[tplMatch[1]]) {
      return htmlPage(await kvPage(env, tplMatch[1], TPL_PAGES[tplMatch[1]]), {
        "x-robots-tag": "noindex, nofollow, noarchive",
      });
    }
    // Manual-check state API (see checkApi). Must precede the /sandal/
    // catch-all redirect below.
    const apiMatch = url.pathname.match(/^\/sandal\/api\/(state|mark|ack|return|pull|import|pageput)$/);
    if (apiMatch) {
      return checkApi(request, env, apiMatch[1]);
    }
    // Screenshot uploads (assistant-side, gateway key): raw image body,
    // object key in ?k=. Stored in the R2 bucket bound as SHOTS.
    if (url.pathname === "/sandal/api/shot" && request.method === "POST") {
      if (!env.SHOTS) return json({ error: "r2_pending" }, 501);
      if (!keyGuard(request, env)) return json({ error: "unauthorized" }, 401);
      const k = url.searchParams.get("k");
      if (!k) return json({ error: "missing k" }, 400);
      const buf = await request.arrayBuffer();
      await env.SHOTS.put(k, buf, {
        httpMetadata: { contentType: request.headers.get("content-type") || "image/jpeg" },
      });
      return json({ ok: true, bytes: buf.byteLength });
    }
    // Screenshot serving for the checking pages. The bucket stays
    // private; images are only reachable through this unlisted path.
    const shotMatch = url.pathname.match(/^\/sandal\/shots\/(.+)$/);
    if (shotMatch && request.method === "GET") {
      if (!env.SHOTS) return new Response("not found", { status: 404 });
      const obj = await env.SHOTS.get(decodeURIComponent(shotMatch[1]));
      if (!obj) return new Response("not found", { status: 404 });
      return new Response(obj.body, {
        status: 200,
        headers: {
          "content-type": (obj.httpMetadata && obj.httpMetadata.contentType) || "image/jpeg",
          "cache-control": "public, max-age=86400",
          "x-robots-tag": "noindex, nofollow, noarchive",
        },
      });
    }
    if (url.pathname.startsWith("/sandal/")) {
      return new Response(null, { status: 301, headers: { location: "/sandal" } });
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
