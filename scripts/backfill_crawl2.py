"""On-site backfill crawler for dead prefecture/municipality bid links (nst project).

Method (Gordon's canon): start from the issuer's official homepage, follow the
site's own navigation (anchor text / path keywords for bidding & procurement),
at most 3 levels deep, verify candidate pages live (200) and score them as
announcement listing pages. Robots first, >=1.5s per host, 10s timeout,
no retry on 403/429, identifiable UA.

Usage: backfill_crawl.py input.csv output.csv START END   (row range, 0-based)
Appends results to output.csv (header written if new).
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

def host_wait(host):
    with _host_lock:
        now = time.time()
        wait = 1.5 - (now - _host_last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()

def robots_for(client, base):
    if base in _robots:
        return _robots[base]
    rp = RobotFileParser()
    try:
        r = client.get(base + "/robots.txt")
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        elif r.status_code in (401, 403):
            rp.parse(["User-agent: *", "Disallow: /"])
        else:
            rp.parse([])
    except Exception:
        rp.parse([])
    _robots[base] = rp
    return rp

def allowed(client, url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    return robots_for(client, base).can_fetch(UA, url)

def fetch(client, url):
    """One polite GET. Returns (status, final_url, content_bytes, content_type) or (None,...) on error."""
    p = urlparse(url)
    host_wait(p.netloc)
    try:
        r = client.get(url)
        return r.status_code, str(r.url), r.content, r.headers.get("content-type", "")
    except Exception:
        return None, url, b"", ""

# ---------------- link / page scoring ----------------
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
    """Score a fetched page as an announcement LISTING page (not a single notice)."""
    s = 0
    t = title or ""
    if "入札" in t: s += 6
    if "調達" in t: s += 4
    if "公告" in t or "公募" in t: s += 3
    if "契約" in t: s += 2
    if "一覧" in t: s += 6
    if "情報" in t and ("入札" in t or "調達" in t or "契約" in t): s += 4
    if len(t) > 50: s -= 6  # long specific titles are single-notice pages
    if len(t) > 32: s -= 4
    if "について" in t: s -= 5
    body = text[:20000]
    for kw, w in [("入札公告", 5), ("公告一覧", 5), ("入札情報", 4), ("調達情報", 4),
                  ("入札・契約", 3), ("公募型", 2), ("一般競争入札", 3), ("開札", 2)]:
        if kw in body: s += w
    dates = len(re.findall(r"20\d{2}[年/]\s?\d{1,2}[月/]", body))
    if dates >= 3: s += 3
    elif dates >= 1: s += 1
    # listing-ness: many outgoing links to notices / sub-listings
    notice_links = 0
    for anchor, href in links:
        a = anchor or ""
        if any(k in a for k in ("入札", "公告", "調達", "公募")):
            notice_links += 1
    s += 2 * min(notice_links, 8)
    for h in LOGIN_HINT:
        if h in body[:3000]: s -= 3
    s -= depth  # shallower is more likely the section listing
    return s, notice_links

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

# ---------------- per-site resolution ----------------
MAX_FETCH = 70
MAX_DEPTH = 3

def resolve(client, org, orig_url):
    p = urlparse(orig_url)
    domain = p.netloc
    schemes = [p.scheme or "https", "https" if p.scheme == "http" else "http"]
    home = None; home_data = None
    for sch in dict.fromkeys(schemes):
        cand = f"{sch}://{domain}/"
        if not allowed(client, cand):
            return ("robots_blocked", "", "", "", "robots.txt disallows homepage")
        st, fin, content, ctype = fetch(client, cand)
        if st is None:  # network error/timeout: one retry after a pause (not an HTTP block)
            time.sleep(3)
            st, fin, content, ctype = fetch(client, cand)
        if st == 200 and "html" in ctype:
            home, home_data = fin, content
            break
        if st in (403, 429):
            return ("unresolved_manual", "", "", "", f"homepage HTTP {st}, stopped per rules")
    if home is None:
        return ("unresolved_manual", "", "", "", "homepage unreachable or non-HTML")

    seen = set()
    frontier = [(home, 0, home_data)]
    best = None  # (score, url, title)
    fetches = 0
    while frontier and fetches < MAX_FETCH:
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
        sc, notice_links = page_score(title, text, links, depth)
        # URL shape: listing/category/index pages vs numeric article pages
        fp = urlparse(fin)
        fpath = fp.path.lower()
        fname = fpath.rsplit("/", 1)[-1]
        stem = fname.rsplit(".", 1)[0]
        if fpath.endswith("/"): sc += 4
        if any(k in fpath for k in ("category", "index", "list")): sc += 5
        if any(k in fname for k in ("nyusatsu", "nyuusatsu", "chotatsu", "keiyaku", "koukoku", "koubo", "bid")): sc += 5
        if stem.isdigit(): sc -= 8
        sc -= depth  # extra depth penalty in final comparison
        is_listing = notice_links >= 2 or "一覧" in title or ("情報" in title and "入札" in title)
        if url != home and ((sc >= 12 and is_listing) or (sc >= 11 and notice_links >= 3)):
            if best is None or sc > best[0]:
                best = (sc, fin, title)
            if sc >= 26 and notice_links >= 4:
                break  # strong listing page found
        if depth < MAX_DEPTH:
            scored = []
            for anchor, href in links:
                absu = urljoin(fin, href)
                ap = urlparse(absu)
                if ap.netloc != domain: continue
                if absu in seen: continue
                ls = link_score(anchor, absu)
                if ls >= 2:
                    scored.append((-ls, absu))
            scored.sort()
            for _, u in scored[:18]:
                frontier.append((u, depth + 1, None))
    # sitemap assist: the site's own published structure (still on-site)
    if best is None:
        for sch in ("https", "http"):
            sm_url = f"{sch}://{domain}/sitemap.xml"
            if not allowed(client, sm_url):
                continue
            st, fin, content, ctype = fetch(client, sm_url)
            if st != 200 or not content:
                continue
            try:
                locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", content.decode("utf-8", "ignore"))
            except Exception:
                locs = []
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
                sc, notice_links = page_score(title, text, links, 2)
                fp = urlparse(fin); fpath = fp.path.lower()
                fname = fpath.rsplit("/", 1)[-1]; stem = fname.rsplit(".", 1)[0]
                if fpath.endswith("/"): sc += 4
                if any(k in fpath for k in ("category", "index", "list")): sc += 5
                if any(k in fname for k in ("nyusatsu", "nyuusatsu", "chotatsu", "keiyaku", "koukoku", "koubo", "bid")): sc += 5
                if stem.isdigit(): sc -= 8
                is_listing = notice_links >= 2 or "一覧" in title or ("情報" in title and "入札" in title)
                if (sc >= 12 and is_listing) or (sc >= 11 and notice_links >= 3):
                    if best is None or sc > best[0]:
                        best = (sc, fin, title)
            if best:
                break
    if best:
        return ("resolved", best[1], best[2], "onsite_nav", f"page_score={best[0]}")
    return ("unresolved_manual", "", "", "", "no listing page found within 3 levels of homepage navigation")

# ---------------- main ----------------
FIELDNAMES = ["org_name", "domain", "original_url", "status", "resolved_url",
              "page_title", "how_found", "note", "prefecture"]
new_file = not os.path.exists(outp)
out_f = open(outp, "a", newline="", encoding="utf-8-sig")
w = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
if new_file: w.writeheader()

_client_kwargs = dict(headers={"User-Agent": UA}, timeout=20, follow_redirects=True,
                      trust_env=False, proxy=PROXY)

def work(r):
    url = r["url"]
    domain = urlparse(url).netloc
    with httpx.Client(**_client_kwargs) as client:
        try:
            status, resolved, title, how, note = resolve(client, r["org_name"], url)
        except Exception as e:
            status, resolved, title, how, note = "unresolved_manual", "", "", "", f"crawler error: {type(e).__name__}"
    rec = {"org_name": r["org_name"], "domain": domain, "original_url": url,
           "status": status, "resolved_url": resolved, "page_title": title,
           "how_found": how, "note": note, "prefecture": r.get("prefecture", "")}
    with _host_lock:
        w.writerow(rec); out_f.flush()
    print(f"[{r['org_name']}] {status} {resolved[:80]}", flush=True)
    return rec

with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(work, rows))
out_f.close()
print("BATCH DONE", START, END)
