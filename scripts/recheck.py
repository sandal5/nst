"""Mechanical re-checker for verify_all.csv subsets (nst project).

Modes:
  redirect  - rows whose original check ended in a redirect with status 200;
              re-request the recorded final_url once.
  connerror - rows whose original check was a connection error;
              re-request the original url once.

Safeguards (same as check_links.py, do not weaken):
  robots.txt first per host (disallow = skip and record),
  >=1.5s between request starts to the same host,
  10s timeout, no retries, identifiable UA.
Parallelism is across different hosts only (worker pool); per-host
spacing is enforced with a shared throttle.

Usage: recheck.py <redirect|connerror> input.csv output.csv
"""
import csv, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx, os

UA = "JPProcurementResearch/1.0 (white-hat link verification; human-directed research)"
MODE, INP, OUTP = sys.argv[1], sys.argv[2], sys.argv[3]
PROXY = os.environ.get("https_proxy")
WORKERS = 6
MIN_INTERVAL = 1.5

rows = list(csv.DictReader(open(INP, encoding="utf-8-sig")))
if MODE == "redirect":
    sel = [r for r in rows if r["final_url"] and r["final_url"] != r["url"] and r["http_status"] == "200"]
    target_of = lambda r: r["final_url"]
elif MODE == "connerror":
    sel = [r for r in rows if r["checked_note"].startswith("接続エラー")]
    target_of = lambda r: r["url"]
else:
    sys.exit("unknown mode")

# ---- per-host throttle -------------------------------------------------
_last = {}
_last_lock = threading.Lock()

def throttle(host):
    while True:
        with _last_lock:
            now = time.monotonic()
            wait = _last.get(host, 0.0) + MIN_INTERVAL - now
            if wait <= 0:
                _last[host] = now
                return
        time.sleep(min(wait, 0.5))

# ---- robots ------------------------------------------------------------
_robots = {}
_robots_locks = {}
_robots_guard = threading.Lock()
_robots_client = httpx.Client(headers={"User-Agent": UA}, timeout=10, trust_env=False, proxy=PROXY)

def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    with _robots_guard:
        lk = _robots_locks.setdefault(base, threading.Lock())
    with lk:
        if base not in _robots:
            rp = RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                throttle(p.netloc)
                rr = _robots_client.get(base + "/robots.txt")
                if rr.status_code == 200:
                    rp.parse(rr.text.splitlines())
                elif rr.status_code in (401, 403):
                    rp.parse(["User-agent: *", "Disallow: /"])
                else:
                    rp.parse([])
            except Exception:
                rp.parse([])
            _robots[base] = rp
    return _robots[base].can_fetch(UA, url)

# ---- one recheck -------------------------------------------------------
_page_client = httpx.Client(headers={"User-Agent": UA}, timeout=10, follow_redirects=True,
                            trust_env=False, proxy=PROXY)

def recheck(idx, r):
    target = target_of(r)
    rec = {**r, "recheck_status": "", "recheck_final_url": "", "recheck_http_status": "", "recheck_note": ""}
    if not allowed(target):
        rec["recheck_status"] = "skipped_robots"
        rec["recheck_note"] = "robots禁止→手動確認対象"
        return idx, rec
    host = urlparse(target).netloc
    try:
        throttle(host)
        resp = _page_client.get(target)
        final = str(resp.url)
        rec["recheck_http_status"] = resp.status_code
        rec["recheck_final_url"] = final
        if MODE == "redirect":
            if resp.status_code == 200 and final == target:
                rec["recheck_status"] = "confirmed"
            elif resp.status_code == 200:
                rec["recheck_status"] = "redirected_again"
                rec["recheck_note"] = "再リダイレクトあり"
            else:
                rec["recheck_status"] = "failed"
                rec["recheck_note"] = f"status {resp.status_code}"
        else:
            if resp.status_code == 200 and final == target:
                rec["recheck_status"] = "recovered_200"
            elif resp.status_code == 200:
                rec["recheck_status"] = "recovered_redirect"
                rec["recheck_note"] = "リダイレクトあり"
            elif resp.status_code == 404:
                rec["recheck_status"] = "dead_404"
            else:
                rec["recheck_status"] = "other_status"
                rec["recheck_note"] = f"status {resp.status_code}"
    except Exception as e:
        name = type(e).__name__
        if MODE == "redirect":
            rec["recheck_status"] = "failed"
            rec["recheck_note"] = f"接続エラー: {name}"
        else:
            rec["recheck_status"] = "still_error"
            rec["recheck_note"] = f"接続エラー: {name}"
    return idx, rec

results = [None] * len(sel)
done = 0
done_lock = threading.Lock()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    for idx, rec in ex.map(lambda t: recheck(*t), enumerate(sel)):
        results[idx] = rec
        with done_lock:
            done += 1
            if done % 50 == 0 or done == len(sel):
                print(f"{MODE}: {done}/{len(sel)}", flush=True)

fields = list(rows[0].keys()) + ["recheck_status", "recheck_final_url", "recheck_http_status", "recheck_note"]
with open(OUTP, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(results)
print("done", MODE, len(results), "->", OUTP)
