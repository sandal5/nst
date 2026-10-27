"""Round-2 on-site backfill crawler for the nst project.

Based on round 1's backfill_pass3.py (the gentle pass that worked best):
on-site first (official homepage -> navigation/sections, keywords
入札公告・調達・入札・契約, at most 3 levels, plus the site's own sitemap as
fallback), list page + newest announcement detail recorded, robots first,
>=1.5s per host, 15s timeout, 6 workers, no retry on page-level 403/429.

Round-2 changes:
- input columns: queue, org_name, url, prefecture; output gains a queue column.
- robots.txt re-verification: a robots fetch returning 403 (a known false-block
  source on shared-CMS clusters in this environment) is re-fetched gently up
  to 3 times with pauses before the host is treated as disallow-all; the note
  records when a robots_blocked verdict rests on a re-verified 403.
- the original URL itself is fetched once as an extra frontier seed: if the
  site redirected it to a moved page, that page is scored like any other
  on-site page.
- homepage unreachable after retry -> approval_pending (never unresolved).

Usage: backfill2_crawl.py input.csv output.csv START END
"""
import csv, os, re, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin, urldefrag
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup

UA = "JPProcurementResearch/1.0 (white-hat source verification; human-directed research)"
PROXY = os.environ.get("https_proxy")
inp, outp = sys.argv[1], sys.argv[2]
START, END = int(sys.argv[3]), int(sys.argv[4])

rows = list(csv.DictReader(open(inp, encoding="utf-8-sig")))[START:END]

# ---------------- per-host rate limiting + robots ----------------
_host_lock = threading.Lock()
_host_last = {}
_robots = {}
_robots_lock = threading.Lock()

def host_wait(host):
    with _host_lock:
        now = time.time()
        wait = 1.5 - (now - _host_last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()

def _fetch_robots_once(client, base):
    try:
        r = client.get(base + "/robots.txt")
        return r.status_code, r.text
    except Exception:
        return None, ""

def robots_for(client, base):
    """Returns (RobotFileParser, flag) where flag is '403' if the disallow-all
    verdict rests on a re-verified 403 robots fetch, else ''."""
    with _robots_lock:
        if base in _robots:
            return _robots[base]
        host = urlparse(base).netloc
        rp = RobotFileParser()
        flag = ""
        status, text = None, ""
        for attempt in range(3):
            if attempt:
                time.sleep(4 * attempt)  # gentle re-verification pauses: 4s, 8s
            host_wait(host)
            status, text = _fetch_robots_once(client, base)
            if status != 403:
                break
        if status == 200:
            rp.parse(text.splitlines())
        elif status in (401, 403):
            rp.parse(["User-agent: *", "Disallow: /"])
            if status == 403:
                flag = "403"
        else:
            rp.parse([])
        _robots[base] = (rp, flag)
        return _robots[base]

def allowed(client, url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp, _ = robots_for(client, base)
    return rp.can_fetch(UA, url)

def robots_flag(client, url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    _, flag = robots_for(client, base)
    return flag

def fetch(client, url):
    p = urlparse(url)
    host_wait(p.netloc)
    try:
        r = client.get(url)
        return r.status_code, str(r.url), r.content, r.headers.get("content-type", "")
    except Exception:
        return None, url, b"", ""

# ---------------- link / page scoring (from pass3, unchanged) ----------------
ANCHOR_STRONG = ["入札", "調達", "契約", "公募", "プロポーザル"]
ANCHOR_OK = ["公告", "競争", "見積", "発注", "事業者"]
PATH_KW = ["nyusatsu", "nyuusatsu", "chotatsu", "keiyaku", "bid", "koukoku", "koubo", "procure"]
BAD_PATH = ["login", "signin", "member", "mypage", "shinsei/system", "cgi-bin/search"]
LOGIN_HINT = ["ログイン", "利用者登録", "ICカード", "パスワードを入力"]

def link_score(anchor, href):
    s = 0
    a = anchor or ""
    h = href.lower()
    for k in ANCHOR_STRONG:
        if k in a: s += 5
    for k in ANCHOR_OK:
        if k in a: s += 2
    for k in PATH_KW:
        if k in h: s += 3
    if href.endswith(".pdf"): s -= 4
    for b in BAD_PATH:
        if b in h: s -= 8
    return s

def page_score(title, text, links, depth):
    s = 0
    t = title or ""
    if "入札" in t: s += 6
    if "調達" in t: s += 4
    if "公告" in t or "公募" in t: s += 3
    if "契約" in t: s += 2
    if "一覧" in t: s += 6
    if "情報" in t and ("入札" in t or "調達" in t or "契約" in t): s += 4
    if len(t) > 50: s -= 6
    if len(t) > 32: s -= 4
    if "について" in t: s -= 5
    body = text[:20000]
    for kw, w in [("入札公告", 5), ("公告一覧", 5), ("入札情報", 4), ("調達情報", 4),
                  ("入札・契約", 3), ("公募型", 2), ("一般競争入札", 3), ("開札", 2)]:
        if kw in body: s += w
    dates = len(re.findall(r"20\d{2}[年/]\s?\d{1,2}[月/]", body))
    if dates >= 3: s += 3
    elif dates >= 1: s += 1
    notice_links = 0
    for anchor, href in links:
        a = anchor or ""
        if any(k in a for k in ("入札", "公告", "調達", "公募")):
            notice_links += 1
    s += 2 * min(notice_links, 8)
    for h in LOGIN_HINT:
        if h in body[:3000]: s -= 3
    s -= depth
    return s, notice_links

def parse_date(s):
    m = re.search(r"(20\d{2})[年/](\d{1,2})[月/](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"令和\s?(\d{1,2}|元)\s?年\s?(\d{1,2})\s?月\s?(\d{1,2})\s?日", s)
    if m:
        y = 2019 if m.group(1) == "元" else 2018 + int(m.group(1))
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"平成\s?(\d{1,2})\s?年\s?(\d{1,2})\s?月\s?(\d{1,2})\s?日", s)
    if m:
        return f"{1988 + int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""

def extract_latest(content, base_url, domain):
    soup = BeautifulSoup(content, "lxml")
    cands = []
    for a in soup.find_all("a", href=True):
        absu = urldefrag(urljoin(base_url, a["href"]))[0]
        ap = urlparse(absu)
        if ap.netloc != domain or absu == urldefrag(base_url)[0]:
            continue
        if absu.endswith(".pdf"):
            continue
        anchor = a.get_text(" ", strip=True)
        ctx = anchor
        node = a
        for _ in range(2):
            if node.parent:
                node = node.parent
                t = node.get_text(" ", strip=True)
                if len(t) < 300:
                    ctx = t
        d = parse_date(ctx) or parse_date(anchor)
        GENERIC = {"入札情報", "入札・契約", "入札・契約情報", "公告", "公募", "調達情報",
                   "入札公告", "プロポーザル公告", "入札結果", "契約情報", "入札案内"}
        if anchor in GENERIC and not d:
            continue
        notice_like = (len(anchor) >= 6 and any(k in anchor for k in
                       ("入札", "公告", "公募", "調達", "契約", "プロポーザル"))) or bool(d and len(anchor) >= 4)
        if notice_like:
            cands.append((d, anchor, absu))
    if not cands:
        return "", "", ""
    dated = [c for c in cands if c[0]]
    if dated:
        best = max(dated, key=lambda c: c[0])
        return best[2], best[1], best[0]
    long_first = [c for c in cands if len(c[1]) >= 10]
    c = long_first[0] if long_first else cands[0]
    return c[2], c[1], ""

STALE_DAYS = 548  # ~18 months

def stale_flag(date_iso):
    if not date_iso:
        return ""
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
        import datetime
        age = (datetime.date(2026, 10, 27) - datetime.date(y, m, d)).days
        if age > STALE_DAYS:
            return f" possibly_stale: newest notice {date_iso}"
    except Exception:
        pass
    return ""

def parse_page(content):
    soup = BeautifulSoup(content, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", soup.title.string).strip()
    text = soup.get_text(" ", strip=True)
    links = []
    for a in soup.find_all("a", href=True):
        links.append((a.get_text(" ", strip=True), a["href"]))
    return title, text, links

def score_candidate(fin, title, text, links, depth):
    sc, notice_links = page_score(title, text, links, depth)
    fp = urlparse(fin)
    fpath = fp.path.lower()
    fname = fpath.rsplit("/", 1)[-1]
    stem = fname.rsplit(".", 1)[0]
    if fpath.endswith("/"): sc += 4
    if any(k in fpath for k in ("category", "index", "list")): sc += 5
    if any(k in fname for k in ("nyusatsu", "nyuusatsu", "chotatsu", "keiyaku", "koukoku", "koubo", "bid")): sc += 5
    if stem.isdigit(): sc -= 8
    sc -= depth
    return sc, notice_links

def qualifies(sc, notice_links, title):
    is_listing = notice_links >= 2 or "一覧" in title or ("情報" in title and "入札" in title)
    return (sc >= 12 and is_listing) or (sc >= 11 and notice_links >= 3)

# ---------------- shared-host candidate pool ----------------
# Listings verified live on a host are pooled per domain: later rows for other
# issuers on the same host re-verify pooled candidates live (same scoring,
# same qualification bar) before falling back to a full crawl. Every pooled
# candidate that qualifies during any crawl is added, so the pool accumulates
# unit-specific listings, not just the ministry-wide one.
_pool = {}
_pool_lock = threading.Lock()

def pool_add(domain, url, title):
    with _pool_lock:
        _pool.setdefault(domain, {})[url] = title

def pool_get(domain):
    with _pool_lock:
        return list(_pool.get(domain, {}).keys())

_seed = os.environ.get("SEED_CSV")
if _seed and os.path.exists(_seed):
    for r in csv.DictReader(open(_seed, encoding="utf-8-sig")):
        if r.get("status") == "resolved" and r.get("resolved_url"):
            pool_add(urlparse(r["resolved_url"]).netloc, r["resolved_url"], r.get("page_title", ""))

# ---------------- per-site resolution ----------------
MAX_FETCH = 55
MAX_DEPTH = 3

def resolve(client, org, orig_url):
    p = urlparse(orig_url)
    domain = p.netloc
    schemes = [p.scheme or "https", "https" if p.scheme == "http" else "http"]
    home = None; home_data = None
    for sch in dict.fromkeys(schemes):
        cand = f"{sch}://{domain}/"
        if not allowed(client, cand):
            note = "robots.txt disallows homepage"
            if robots_flag(client, cand) == "403":
                note += " (robots.txt fetch returned 403, re-verified 3x at low speed)"
            return ("robots_blocked", "", "", "", note, "", "", "")
        st, fin, content, ctype = fetch(client, cand)
        if st is None:  # network error/timeout: one retry after a pause
            time.sleep(3)
            st, fin, content, ctype = fetch(client, cand)
        if st == 200 and "html" in ctype:
            home, home_data = fin, content
            break
        if st in (403, 429):
            return ("unresolved_manual", "", "", "", f"homepage HTTP {st}, stopped per rules", "", "", "")
    if home is None:
        return ("approval_pending", "", "", "",
                "host could not be fetched from this environment (network or approval block); retry after clearing approval cards",
                "", "", "")

    seen = set()
    frontier = [(home, 0, home_data)]
    # extra seed: the original URL itself (site may redirect it to the moved page)
    if urldefrag(orig_url)[0] != urldefrag(home)[0] and allowed(client, orig_url):
        st, fin, content, ctype = fetch(client, orig_url)
        if st == 200 and "html" in ctype and content:
            frontier.append((fin, 1, content))
    best = None  # (score, url, title, content)
    pool_used = False
    # shared-host pool: re-verify pooled candidates live before a full crawl
    for cand_url in pool_get(domain)[:6]:
        if cand_url == urldefrag(home)[0] or not allowed(client, cand_url):
            continue
        st, fin, content, ctype = fetch(client, cand_url)
        if st != 200 or "html" not in ctype or not content:
            continue
        try:
            title, text, links = parse_page(content)
        except Exception:
            continue
        sc, notice_links = score_candidate(fin, title, text, links, 1)
        if qualifies(sc, notice_links, title):
            if best is None or sc > best[0]:
                best = (sc, fin, title, content)
                pool_used = True
    fetches = 0
    while best is None and frontier and fetches < MAX_FETCH:
        url, depth, cached = frontier.pop(0)
        url = urldefrag(url)[0]
        if url in seen: continue
        seen.add(url)
        if cached is not None:
            content = cached; st = 200; fin = url
        else:
            if not allowed(client, url):
                continue
            st, fin, content, ctype = fetch(client, url)
            fetches += 1
            if st in (403, 429):
                continue
            if st != 200 or "html" not in ctype or not content:
                continue
        try:
            title, text, links = parse_page(content)
        except Exception:
            continue
        sc, notice_links = score_candidate(fin, title, text, links, depth)
        if url != home and qualifies(sc, notice_links, title):
            pool_add(domain, fin, title)
            if best is None or sc > best[0]:
                best = (sc, fin, title, content)
            if sc >= 26 and notice_links >= 4:
                break
        if depth < MAX_DEPTH:
            scored = []
            for anchor, href in links:
                absu = urljoin(fin, href)
                ap = urlparse(absu)
                if ap.netloc != domain: continue
                if absu in seen: continue
                ls = link_score(anchor, absu)
                if ls >= (2 if depth == 0 else 3):
                    scored.append((-ls, absu))
            scored.sort()
            for _, u in scored[:18]:
                frontier.append((u, depth + 1, None))
    # sitemap assist (the site's own published structure; still on-site)
    if best is None:
        for sch in ("https", "http"):
            sm_url = f"{sch}://{domain}/sitemap.xml"
            if not allowed(client, sm_url):
                continue
            st, fin, content, ctype = fetch(client, sm_url)
            if st != 200 or not content:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", content.decode("utf-8", "ignore"))
            cand = []
            for u in locs:
                up = urlparse(u)
                if up.netloc != domain:
                    continue
                ls = link_score("", u)
                if ls >= 3:
                    cand.append((-ls, u))
            cand.sort()
            for _, u in cand[:12]:
                if u in seen:
                    continue
                seen.add(u)
                if not allowed(client, u):
                    continue
                st, fin, content, ctype = fetch(client, u)
                if st != 200 or "html" not in ctype or not content:
                    continue
                try:
                    title, text, links = parse_page(content)
                except Exception:
                    continue
                sc, notice_links = score_candidate(fin, title, text, links, 2)
                if qualifies(sc, notice_links, title):
                    pool_add(domain, fin, title)
                    if best is None or sc > best[0]:
                        best = (sc, fin, title, content)
            if best:
                break
    if best:
        pool_add(domain, best[1], best[2])
        lu, lt, ld = extract_latest(best[3], best[1], domain)
        note = f"page_score={best[0]}" + stale_flag(ld)
        if pool_used:
            note += "; via shared-host candidate pool (verified live for this row)"
        return ("resolved", best[1], best[2], "onsite_nav", note, lu, lt, ld)
    return ("unresolved_manual", "", "", "", "no listing page found within 3 levels of homepage navigation", "", "", "")

# ---------------- main ----------------
FIELDNAMES = ["queue", "org_name", "domain", "original_url", "status", "resolved_url",
              "page_title", "how_found", "note", "prefecture",
              "latest_detail_url", "latest_notice_title", "latest_notice_date"]
new_file = not os.path.exists(outp)
out_f = open(outp, "a", newline="", encoding="utf-8-sig")
w = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
if new_file: w.writeheader()

_client_kwargs = dict(headers={"User-Agent": UA}, timeout=15, follow_redirects=True,
                      trust_env=False, proxy=PROXY)

def work(r):
    url = r["url"]
    domain = urlparse(url).netloc
    with httpx.Client(**_client_kwargs) as client:
        try:
            status, resolved, title, how, note, lu, lt, ld = resolve(client, r["org_name"], url)
        except Exception as e:
            status, resolved, title, how, note, lu, lt, ld = "unresolved_manual", "", "", "", f"crawler error: {type(e).__name__}", "", "", ""
    rec = {"queue": r.get("queue", ""), "org_name": r["org_name"], "domain": domain, "original_url": url,
           "status": status, "resolved_url": resolved, "page_title": title,
           "how_found": how, "note": note, "prefecture": r.get("prefecture", ""),
           "latest_detail_url": lu, "latest_notice_title": lt, "latest_notice_date": ld}
    with _host_lock:
        w.writerow(rec); out_f.flush()
    print(f"[{r.get('queue','')}:{r['org_name']}] {status} {resolved[:80]}", flush=True)
    return rec

with ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(work, rows))
out_f.close()
print("BATCH DONE", START, END)
