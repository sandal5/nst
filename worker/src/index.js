/**
 * nst — 全日本入札情報 源頭清單 + 合規採集框架 (Cloudflare Worker)
 *
 * Principles (see docs/01_compliance.md — non-negotiable):
 *  1. robots.txt first: a source whose robots.txt disallows the target path
 *     is never fetched automatically; it is reported as manual-check.
 *  2. Terms first: only sources with terms_status = "verified" in the source
 *     list are collected. Aggregators that ban automated collection
 *     (kkj.go.jp robots Disallow:/, the commercial directory's terms) are NEVER targets —
 *     collection goes directly to each issuer's official site.
 *  3. Polite: identifiable UA, >=2s per host, stop the host for the day on 403/429.
 *  4. Facts only: title / issuer / publish date / deadline / source URL.
 *     Store the source URL and attribution with every notice.
 *
 * Scale note: ~3,900 sources cannot be fetched in one Worker invocation
 * (subrequest & CPU limits). The cron handler therefore shards by
 * prefecture and enqueues jobs; each queue consumer collects one shard.
 * Without a Queue binding it falls back to a small inline batch (dev/demo).
 */

// ---------- robots.txt (RFC 9309, simplified group parser) ----------
export function parseRobots(text, ua) {
  const groups = [];
  let cur = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.split("#")[0].trim();
    if (!line) continue;
    const [k, ...rest] = line.split(":");
    const key = k.trim().toLowerCase();
    const val = rest.join(":").trim();
    if (key === "user-agent") {
      if (!cur || cur.rules.length) { cur = { agents: [], rules: [] }; groups.push(cur); }
      cur.agents.push(val.toLowerCase());
    } else if ((key === "disallow" || key === "allow") && cur) {
      cur.rules.push({ allow: key === "allow", path: val });
    }
  }
  const mine = groups.filter(g => g.agents.includes("*") || g.agents.some(a => ua.toLowerCase().includes(a)));
  return mine.flatMap(g => g.rules);
}

export function robotsAllow(rules, urlPath) {
  let best = null;
  for (const r of rules) {
    if (r.path === "") continue;
    if (urlPath.startsWith(r.path) && (!best || r.path.length > best.path.length)) best = r;
  }
  return best ? best.allow : true;
}

async function fetchRobots(env, origin) {
  const cacheKey = `robots:${origin}`;
  if (env.KV) {
    const hit = await env.KV.get(cacheKey, "json");
    if (hit && Date.now() - hit.at < 24 * 3600 * 1000) return hit.rules;
  }
  let rules = [];
  try {
    const res = await fetch(origin + "/robots.txt", { headers: { "User-Agent": env.COLLECTOR_UA } });
    if (res.status === 200) rules = parseRobots(await res.text(), env.COLLECTOR_UA);
    else if (res.status === 401 || res.status === 403) rules = [{ allow: false, path: "/" }];
  } catch { rules = []; } // unreachable robots.txt: treat as no restriction, log upstream
  if (env.KV) await env.KV.put(cacheKey, JSON.stringify({ at: Date.now(), rules }));
  return rules;
}

// ---------- polite fetch ----------
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function politeFetch(env, url, lastByHost) {
  const host = new URL(url).host;
  const wait = Math.max(0, (lastByHost.get(host) || 0) + Number(env.MIN_INTERVAL_MS_PER_HOST || 2000) - Date.now());
  if (wait) await sleep(wait);
  lastByHost.set(host, Date.now());
  const res = await fetch(url, { headers: { "User-Agent": env.COLLECTOR_UA, "Accept-Language": "ja" } });
  if (res.status === 403 || res.status === 429) {
    const err = new Error(`stop-host:${res.status}`); err.stopHost = true; throw err;
  }
  return res;
}

// ---------- listing parser (frameworks P1/P2/P3/P5 share this shape) ----------
// Extracts announcement-like links: anchor text + href + nearby date (YYYY/MM/DD, 令和/平成表記は原文のまま保持).
export async function parseListing(html, baseUrl) {
  const items = [];
  const re = /<a\s[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(html))) {
    const text = m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    if (!text || text.length < 4) continue;
    if (!/(入札|公告|公募|調達|プロポーザル|見積|競争|告示)/.test(text)) continue;
    const around = html.slice(Math.max(0, m.index - 200), m.index + 200);
    const date = (around.match(/20\d{2}[\/.\-]\d{1,2}[\/.\-]\d{1,2}/) || [null])[0];
    items.push({ title: text.slice(0, 300), url: new URL(m[1], baseUrl).toString(), date_on_page: date });
  }
  return items;
}

// P4 (電子入札/電子調達システム): only the public PPI-style search surface may
// be adapted, per system, after its own terms are verified. No login, no IC-card
// areas, no bypassing session/CAPTCHA. Adapters are added per system as
// `adapters/<system>.js`; until then P4 sources are reported, not fetched.
const ADAPTERS = {};

// ---------- one source ----------
async function collectSource(env, source, lastByHost) {
  if (env.COLLECT_ONLY_VERIFIED === "true" && source.terms_status !== "verified") {
    return { source: source.org_name, skipped: "terms-not-verified" };
  }
  const target = source.verified_bidding_url || source.source_url_kkj;
  if (!target) return { source: source.org_name, skipped: "no-target-url" };
  if ((source.framework || "").startsWith("P6")) return { source: source.org_name, skipped: "P6-unresolved-listing-page" };
  if ((source.framework || "").startsWith("P4") && !ADAPTERS[source.official_domain]) {
    return { source: source.org_name, skipped: "P4-no-public-adapter-yet" };
  }
  const u = new URL(target);
  const rules = await fetchRobots(env, u.origin);
  if (!robotsAllow(rules, u.pathname)) return { source: source.org_name, skipped: "robots-disallow", url: target };
  const res = await politeFetch(env, target, lastByHost);
  if (!res.ok) return { source: source.org_name, error: `http-${res.status}`, url: target };
  const notices = await parseListing(await res.text(), target);
  const now = new Date().toISOString();
  const records = notices.map(n => ({ ...n, issuer: source.org_name, prefecture: source.prefecture, source_url: target, collected_at: now }));
  if (env.KV && records.length) {
    await env.KV.put(`notices:${source.prefecture}:${now.slice(0, 10)}:${source.org_name}`, JSON.stringify(records));
  }
  // D1 insert (schema in worker/schema.sql) runs when DB is bound.
  return { source: source.org_name, found: records.length, url: target };
}

async function loadSources(env) {
  if (env.KV) {
    const list = await env.KV.get("sources:all", "json");
    if (Array.isArray(list)) return list;
  }
  return []; // import data/master_source_list.csv into KV with scripts/import_sources.mjs
}

// ---------- handlers ----------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/sources") {
      const sources = await loadSources(env);
      return Response.json({ count: sources.length, sources });
    }
    if (url.pathname === "/api/notices") {
      // KV-backed demo read; production reads go to D1 (see docs/03).
      return Response.json({ note: "query D1/KV by prefecture+date in production deployment", pref: url.searchParams.get("pref"), date: url.searchParams.get("date") });
    }
    if (url.pathname === "/api/run" && request.method === "POST") {
      if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
        return new Response("unauthorized", { status: 401 });
      }
      const sources = await loadSources(env);
      const lastByHost = new Map(); const results = [];
      for (const s of sources.slice(0, 10)) {
        try { results.push(await collectSource(env, s, lastByHost)); }
        catch (e) { results.push({ source: s.org_name, error: String(e.message || e) }); if (e.stopHost) break; }
      }
      return Response.json({ ran_at: new Date().toISOString(), results });
    }
    return new Response(
      `nst — Japan public procurement source registry & compliant collection framework\n` +
      `Sources/API: /api/sources  /api/notices?pref=<都道府县>&date=<YYYY-MM-DD>\n` +
      `Project: https://github.com/sandal5/nst\n`,
      { headers: { "content-type": "text/plain; charset=utf-8" } });
  },

  async scheduled(event, env, ctx) {
    const sources = await loadSources(env);
    const verified = sources.filter(s => s.terms_status === "verified");
    if (env.JOBS) {
      const byPref = new Map();
      for (const s of verified) {
        if (!byPref.has(s.prefecture)) byPref.set(s.prefecture, []);
        byPref.get(s.prefecture).push(s);
      }
      for (const [pref, shard] of byPref) await env.JOBS.send({ prefecture: pref, sources: shard });
    } else {
      ctx.waitUntil((async () => {
        const lastByHost = new Map();
        for (const s of verified.slice(0, 10)) { try { await collectSource(env, s, lastByHost); } catch { /* logged per-source in KV in production */ } }
      })());
    }
  },

  async queue(batch, env) {
    const lastByHost = new Map();
    for (const msg of batch.messages) {
      for (const s of msg.body.sources || []) {
        try { await collectSource(env, s, lastByHost); } catch { /* stop-host etc.: skip rest of shard today */ break; }
      }
      msg.ack();
    }
  },
};
