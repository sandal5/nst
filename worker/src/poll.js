/**
 * nst polling engine — polls ENDPOINTS, not objects.
 *
 * The registry holds 13,528 issuer objects (one-time enumeration from the two
 * directories; the directories themselves are never polled). Each object
 * resolves to one polling endpoint on an official site. Many objects share an
 * endpoint (ministry procurement page, prefectural joint e-procurement system,
 * parent-organization page), so the poll unit is the endpoint and notices are
 * attributed back to objects by issuer-name matching.
 *
 * Effectiveness rules:
 *  - Tiered cadence: A = daily, B = every 3 days, C = weekly. Tiers adapt:
 *    an endpoint that produced changes in the last 30 days is promoted,
 *    one silent for 90 days is demoted (never below C, never above A).
 *  - Cheap change detection first: conditional GET (ETag / Last-Modified)
 *    and a SHA-256 body hash. Unchanged => heartbeat only, no parsing.
 *  - Per-host politeness: >= MIN_INTERVAL between requests to one host,
 *    one in-flight request per host (enforced by the shard planner sending
 *    a host at most once per consumer batch).
 *  - Failure backoff: 404/5xx => 1d, 3d, 7d, then the endpoint moves to the
 *    re-verify queue (find its new page) instead of being hammered.
 *    403/429 => stop that host for the rest of the day.
 *  - Gates: robots-disallowed paths and terms-unverified endpoints are
 *    never fetched; they surface in the result as skips for human review.
 */

export const TIERS = { A: 1, B: 3, C: 7 }; // days between polls

export function nextDue(ep, now = Date.now()) {
  const days = TIERS[ep.poll_tier] || TIERS.B;
  return (ep.last_polled_at || 0) + days * 86400_000 + (ep.backoff_until ? ep.backoff_until - now > 0 ? ep.backoff_until - (ep.last_polled_at || 0) - days * 86400_000 : 0 : 0);
}

export function isDue(ep, now = Date.now()) {
  if (ep.status === "reverify" || ep.status === "disabled") return false;
  if (ep.backoff_until && now < ep.backoff_until) return false;
  return now >= (ep.last_polled_at || 0) + (TIERS[ep.poll_tier] || TIERS.B) * 86400_000;
}

/** Plan one cron tick: pick due endpoints, shard them so no host repeats in a shard. */
export function planShards(endpoints, { shardSize = 25 } = {}) {
  const due = endpoints.filter(ep => isDue(ep));
  const shards = [];
  const hostInShard = [];
  for (const ep of due.sort((a, b) => (a.last_polled_at || 0) - (b.last_polled_at || 0))) {
    let placed = false;
    for (let i = 0; i < shards.length; i++) {
      if (shards[i].length < shardSize && !hostInShard[i].has(ep.host)) {
        shards[i].push(ep); hostInShard[i].add(ep.host); placed = true; break;
      }
    }
    if (!placed) { shards.push([ep]); hostInShard.push(new Set([ep.host])); }
  }
  return shards;
}

export function adaptTier(ep, now = Date.now()) {
  const changed30 = ep.last_changed_at && now - ep.last_changed_at < 30 * 86400_000;
  const silent90 = !ep.last_changed_at || now - ep.last_changed_at > 90 * 86400_000;
  if (changed30 && ep.poll_tier !== "A") return "A";
  if (silent90 && ep.poll_tier === "A") return "B";
  if (silent90 && ep.poll_tier === "B" && ep.silent_polls >= 10) return "C";
  return ep.poll_tier;
}

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/**
 * Poll one endpoint. `io` injects storage so this stays testable:
 *   io.getRobots(origin) -> rules[]   ( robotsAllow() lives in index.js logic;
 *                                       here we accept io.robotsAllow(url) )
 *   io.saveNotice(notice), io.saveEndpointState(ep)
 * Returns a result record; never throws for HTTP-level failures.
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
  catch (e) { return backoff(ep, base, `network:${e.message}`); }

  if (res.status === 304) { ep.last_polled_at = now; ep.silent_polls = (ep.silent_polls || 0) + 1; ep.poll_tier = adaptTier(ep, now); await io.saveEndpointState(ep); return { ...base, unchanged: "304" }; }
  if (res.status === 403 || res.status === 429) { ep.stop_host_until = now + 86400_000; await io.saveEndpointState(ep); return { ...base, stop_host: res.status }; }
  if (res.status >= 400) return backoff(ep, base, `http-${res.status}`);

  const body = await res.text();
  const hash = await sha256(body);
  ep.etag = res.headers.get("etag") || ep.etag;
  ep.last_modified = res.headers.get("last-modified") || ep.last_modified;
  ep.last_polled_at = now;
  if (hash === ep.body_hash) { ep.silent_polls = (ep.silent_polls || 0) + 1; ep.poll_tier = adaptTier(ep, now); await io.saveEndpointState(ep); return { ...base, unchanged: "hash" }; }

  ep.body_hash = hash; ep.last_changed_at = now; ep.silent_polls = 0; ep.failures = 0; ep.backoff_until = 0;
  ep.poll_tier = adaptTier(ep, now);
  const notices = parseListing ? await parseListing(body, ep.url) : [];
  // Attribute each notice to the objects registered on this endpoint by issuer name.
  const attributed = notices.map(n => ({ ...n, endpoint_id: ep.endpoint_id, objects: (ep.object_names || []).filter(name => !n.issuer || n.issuer.includes(name) || name.includes(n.issuer)) }));
  for (const n of attributed) await io.saveNotice(n);
  await io.saveEndpointState(ep);
  return { ...base, changed: true, notices: attributed.length };
}

async function backoff(ep, base, why) {
  const now = Date.now();
  ep.failures = (ep.failures || 0) + 1;
  const steps = [1, 3, 7];
  if (ep.failures > steps.length) { ep.status = "reverify"; }
  else ep.backoff_until = now + steps[ep.failures - 1] * 86400_000;
  ep.last_error = why; ep.last_polled_at = now;
  return { ...base, error: why, failures: ep.failures, status: ep.status || "backoff" };
}
