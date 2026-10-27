/**
 * nst polling engine — polls ENDPOINTS, not objects, with EXPONENTIAL
 * BACKOFF on silence (指数回避轮询).
 *
 * Why: most issuers publish bid notices rarely — some only once in several
 * months. Polling every endpoint daily would mean ~3,500 requests/day,
 * almost all of them finding nothing, and would put pointless pressure on
 * small municipal servers. Instead each endpoint carries its own interval:
 *
 *   - interval starts at the tier base (A: 12h, B: 1d, C: 2d);
 *   - every poll that finds NO change doubles the interval (×2), up to the
 *     tier cap (A: 2d, B: 8d, C: 32d), with ±10% jitter so endpoints do not
 *     synchronize into thundering herds;
 *   - any change resets the interval to base immediately.
 *
 * Example, a C endpoint that publishes quarterly, after its last notice:
 * polls on day 0, 2, 6, 14, 30, 62, 94… ≈ 12 polls/year instead of 365 —
 * ~97% fewer requests — while an active A endpoint is still checked twice
 * a day at its most frequent. Worst-case detection delay is bounded by the
 * tier cap; tiers are assigned when the endpoint is classified (framework
 * + observed publication frequency) and can be overridden per endpoint.
 *
 * Other rules (unchanged):
 *  - Cheap change detection first: conditional GET (ETag/Last-Modified)
 *    + SHA-256 body hash; unchanged => heartbeat only, no parsing.
 *  - Per-host politeness: a host appears at most once per shard; consumers
 *    keep >=2s between requests to the same host.
 *  - Failures (404/5xx/network): exponential backoff 1d, 2d, 4d, then the
 *    endpoint moves to the re-verify queue (find its new page on the
 *    official site). 403/429: stop that host for the rest of the day.
 *  - Gates: robots-disallowed, terms-unverified and P6-unresolved
 *    endpoints are never fetched; they are reported as skips.
 */

export const DAY = 86400_000;
export const TIER_PARAMS = {
  A: { base: 0.5, cap: 2 },   // active systems / large issuers
  B: { base: 1, cap: 8 },     // default
  C: { base: 2, cap: 32 },    // rare publishers (a few notices a year)
};
const FACTOR = 2;

export function params(ep) { return TIER_PARAMS[ep.poll_tier] || TIER_PARAMS.B; }

export function currentIntervalMs(ep) {
  const p = params(ep);
  const days = ep.interval_days ?? p.base;
  return Math.min(days, p.cap) * DAY;
}

export function isDue(ep, now = Date.now()) {
  if (ep.status === "reverify" || ep.status === "disabled") return false;
  if (ep.backoff_until && now < ep.backoff_until) return false;
  if (ep.stop_host_until && now < ep.stop_host_until) return false;
  return now >= (ep.last_polled_at || 0) + currentIntervalMs(ep);
}

/** Plan one cron tick: due endpoints, sharded so no host repeats in a shard. */
export function planShards(endpoints, { shardSize = 25 } = {}) {
  const due = endpoints.filter(ep => isDue(ep)).sort((a, b) => (a.last_polled_at || 0) - (b.last_polled_at || 0));
  const shards = [], hosts = [];
  for (const ep of due) {
    let placed = false;
    for (let i = 0; i < shards.length; i++) {
      if (shards[i].length < shardSize && !hosts[i].has(ep.host)) { shards[i].push(ep); hosts[i].add(ep.host); placed = true; break; }
    }
    if (!placed) { shards.push([ep]); hosts.push(new Set([ep.host])); }
  }
  return shards;
}

function jitter(ms) { return ms * (0.9 + Math.random() * 0.2); }

/** Silence: grow the interval exponentially (called after an unchanged poll). */
export function growInterval(ep) {
  const p = params(ep);
  ep.silent_streak = (ep.silent_streak || 0) + 1;
  ep.interval_days = Math.min(p.cap, p.base * FACTOR ** ep.silent_streak);
  ep.next_interval_ms = jitter(currentIntervalMs(ep));
}

/** Change detected: reset to base cadence. */
export function resetInterval(ep) {
  const p = params(ep);
  ep.silent_streak = 0;
  ep.interval_days = p.base;
  ep.next_interval_ms = jitter(currentIntervalMs(ep));
}

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Poll one endpoint. `io` injects storage & robots:
 *   io.robotsAllow(url) -> bool, io.saveNotice(n), io.saveEndpointState(ep)
 * parseListing(html, url) -> notices[] (from index.js / framework adapters)
 */
export async function pollEndpoint(env, ep, io, parseListing) {
  const now = Date.now();
  const base = { endpoint_id: ep.endpoint_id, url: ep.url, at: new Date(now).toISOString() };
  if (ep.terms_status !== "verified") return { ...base, skipped: "terms-not-verified" };
  if (ep.framework && ep.framework.startsWith("P6")) return { ...base, skipped: "P6-listing-page-unresolved" };
  if (io.robotsAllow && !(await io.robotsAllow(ep.url))) return { ...base, skipped: "robots-disallow" };

  const headers = { "User-Agent": env.COLLECTOR_UA || "nst-collector/0.1 (+https://github.com/sandal5/nst)", "Accept-Language": "ja" };
  if (ep.etag) headers["If-None-Match"] = ep.etag;
  if (ep.last_modified) headers["If-Modified-Since"] = ep.last_modified;

  let res;
  try { res = await fetch(ep.url, { headers }); }
  catch (e) { return failure(ep, io, base, `network:${e.message}`); }

  if (res.status === 304) {
    ep.last_polled_at = now; growInterval(ep); await io.saveEndpointState(ep);
    return { ...base, unchanged: "304", next_in_days: (currentIntervalMs(ep) / DAY).toFixed(1) };
  }
  if (res.status === 403 || res.status === 429) {
    ep.stop_host_until = now + DAY; ep.last_polled_at = now; await io.saveEndpointState(ep);
    return { ...base, stop_host: res.status };
  }
  if (res.status >= 400) return failure(ep, io, base, `http-${res.status}`);

  const body = await res.text();
  const hash = await sha256(body);
  ep.etag = res.headers.get("etag") || ep.etag;
  ep.last_modified = res.headers.get("last-modified") || ep.last_modified;
  ep.last_polled_at = now;
  if (hash === ep.body_hash) {
    growInterval(ep); await io.saveEndpointState(ep);
    return { ...base, unchanged: "hash", next_in_days: (currentIntervalMs(ep) / DAY).toFixed(1) };
  }

  ep.body_hash = hash; ep.last_changed_at = now; ep.failures = 0; ep.backoff_until = 0;
  resetInterval(ep);
  const notices = parseListing ? await parseListing(body, ep.url) : [];
  const attributed = notices.map(n => ({
    ...n, endpoint_id: ep.endpoint_id,
    objects: (ep.object_names || []).filter(name => !n.issuer || n.issuer.includes(name) || name.includes(n.issuer)),
  }));
  for (const n of attributed) await io.saveNotice(n);
  await io.saveEndpointState(ep);
  return { ...base, changed: true, notices: attributed.length };
}

async function failure(ep, io, base, why) {
  const now = Date.now();
  ep.failures = (ep.failures || 0) + 1;
  ep.last_error = why; ep.last_polled_at = now;
  if (ep.failures > 3) ep.status = "reverify"; // 1d, 2d, 4d backoff exhausted -> find the new page
  else ep.backoff_until = now + (2 ** (ep.failures - 1)) * DAY;
  await io.saveEndpointState(ep);
  return { ...base, error: why, failures: ep.failures, status: ep.status || "backoff" };
}
