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
// Politeness rules (robots-first, per-host pacing, identifiable UA) stay
// enforced by the calling client, exactly as with direct fetches.

import { ALLOWED_HOSTS } from "./allowlist.js";

const MAX_BODY = 8 * 1024 * 1024;

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
    if (url.pathname !== "/fetch") return json({ error: "not found" }, 404);
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
          "User-Agent": "nst-research/0.1 (+https://github.com/sandal5/nst)",
          Accept: "*/*",
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
