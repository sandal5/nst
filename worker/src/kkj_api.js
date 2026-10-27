/**
 * KKJ (官公需情報ポータルサイト) official Search API client.
 *
 * This is the ONLY sanctioned automated channel for kkj.go.jp:
 * web scraping of kkj.go.jp is prohibited (robots.txt: Disallow: /),
 * while the API is provided expressly for automated retrieval.
 * Terms (利用規約 §3, paraphrased — see docs/01_compliance.md):
 *  - Credit the portal API and link back to the portal wherever its data is used.
 *  - Respect its concurrency limits; no sustained heavy access from one server.
 *    (Specific numbers are not published — confirm with the operator before
 *     production-scale use; this client defaults to 1 request / 2s, max a few
 *     pages per run.)
 *  - Violations may result in API access being blocked.
 * API guide: https://www.kkj.go.jp/doc/ja/api_guide.pdf (V1.1)
 * Endpoint returns XML; up to 1,000 results per call (Count).
 */

const API_BASE = "https://www.kkj.go.jp/api/";
const sleep = ms => new Promise(r => setTimeout(r, ms));

function tag(block, name) {
  const m = block.match(new RegExp(`<${name}>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${name}>`));
  return m ? m[1].trim() : "";
}

export function parseKkjXml(xml) {
  const hits = Number(tag(xml, "SearchHits") || 0);
  const items = [];
  for (const m of xml.matchAll(/<SearchResult>([\s\S]*?)<\/SearchResult>/g)) {
    const b = m[1];
    items.push({
      key: tag(b, "Key"),
      title: tag(b, "ProjectName"),
      issuer: tag(b, "OrganizationName"),
      prefecture: tag(b, "PrefectureName"),
      lg_code: tag(b, "LgCode"),
      city: tag(b, "CityName"),
      city_code: tag(b, "CityCode"),
      published_date: tag(b, "CftIssueDate"),
      tender_deadline: tag(b, "TenderSubmissionDeadline"),
      opening_date: tag(b, "OpeningTendersEvent"),
      category: tag(b, "Category"),
      procedure_type: tag(b, "ProcedureType"),
      source_url: tag(b, "ExternalDocumentURI"), // original notice URL on the issuer's official site
      description: tag(b, "ProjectDescription"),
      collected_via: "kkj-api",
      attribution: "出典：官公需情報ポータルサイト検索API (https://www.kkj.go.jp/s/)",
    });
  }
  return { hits, items };
}

/** Fetch notices issued on a date range (YYYY-MM-DD/YYYY-MM-DD), paging by Count<=1000. */
export async function fetchKkjByIssueDate(env, range, { maxPages = 3 } = {}) {
  const all = [];
  for (let page = 0; page < maxPages; page++) {
    const params = new URLSearchParams({ CFT_Issue_Date: range, Count: "1000" });
    // The V1.1 API has no offset parameter in the guide; if hits exceed one page,
    // narrow the range (per-prefecture LG_Code) instead of re-requesting the same page.
    const res = await fetch(`${API_BASE}?${params}`, { headers: { "User-Agent": env.COLLECTOR_UA || "nst-collector/0.1 (+https://github.com/sandal5/nst)" } });
    if (!res.ok) throw new Error(`kkj-api http-${res.status}`);
    const { hits, items } = parseKkjXml(await res.text());
    all.push(...items);
    if (items.length === 0 || all.length >= hits) break;
    await sleep(2000);
    break; // see note above: subdivide by LG_Code for >1000/day in production
  }
  return all;
}
