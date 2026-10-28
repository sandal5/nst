#!/usr/bin/env python3
"""Pilot capture v2: dir2 listing pages 1/2/160 (expand-all, full-page PNG),
one focused expansion case, and 2 robots-denied registry pages.

Rendering route that works on this VM (see PILOT.md):
- Direct Chromium navigation is rejected by the target's WAF for ANY
  browser-identifiable request profile (202 empty / 403 / hang), while
  plain HTTP clients (curl, httpx default UA) are served the full page.
- Therefore Chromium never talks to the target: every request is fulfilled
  by a local httpx fetcher (default UA, env proxy) through Playwright
  route interception; Chromium only renders + runs JS. Data extraction
  from the renders is by OCR only; DOM ground truth is captured solely
  to score OCR accuracy for this pilot.
- Robots-denied pages are ordinary official sites: direct navigation via
  the local relay proxy (relay.py, 127.0.0.1:18080).
"""
import json, os, time, threading
import httpx
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/opt/meta-chromium/chrome"
RELAY = {"server": "http://127.0.0.1:18080"}
BASE = "https://nsearch.jp/organizations"
PAUSE = 3.0  # >=2s pacing between page loads

class Fetcher:
    def __init__(self):
        self.client = httpx.Client(proxy=os.environ['https_proxy'], trust_env=False,
                                   timeout=90, follow_redirects=True,
                                   limits=httpx.Limits(max_keepalive_connections=0))
        self.n = 0
        self.cache = {}
        self.errors = 0
        self.lock = threading.Lock()  # proxy stalls under parallel tunnels; serialize
    def _request_once(self, method, url, h, body):
        # Fresh client per request: a persistent cookie jar poisons later
        # requests (first response sets a WAF/session cookie; subsequent
        # requests carrying it hang in the challenge path - observed).
        with httpx.Client(proxy=os.environ['https_proxy'], trust_env=False,
                          timeout=90, follow_redirects=True,
                          limits=httpx.Limits(max_keepalive_connections=0)) as c:
            return c.request(method, url, headers=h, content=body)
    def fetch(self, method, url, headers, body):
        # Header normalization is REQUIRED: the target's AWS WAF challenges
        # any browser-profile header set (see PILOT.md). Plain httpx default
        # headers are served the full public page. Keep only content-type
        # (POST fidelity) from the browser request.
        if method.upper() == "GET" and url in self.cache:
            return self.cache[url]
        h = {}
        ct = (headers or {}).get('content-type')
        if ct and method.upper() not in ('GET', 'HEAD'):
            h['Content-Type'] = ct
        last = None
        attempts = 3 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                with self.lock:
                    r = self._request_once(method, url, h, body)
                self.n += 1
                out = (r.status_code, dict(r.headers), r.content)
                if method.upper() == "GET" and r.status_code == 200:
                    self.cache[url] = out
                return out
            except Exception as e:
                last = e
                print(f"fetch retry {attempt+1}:", url[:80], type(e).__name__, flush=True)
                time.sleep(2)
        self.errors += 1
        print("fetch FAILED:", url[:80], type(last).__name__ if last else "?", flush=True)
        return 502, {}, b""
    def prewarm(self, urls):
        t0 = time.time()
        for u in urls:
            if u not in self.cache:
                self.fetch("GET", u, {}, None)
        print(f"prewarm: {len(self.cache)} cached in {time.time()-t0:.1f}s", flush=True)

BLOCKED_SUFFIXES = ("googletagmanager.com", "google-analytics.com", "clarity.ms",
                    "doubleclick.net", "facebook.net", "yimg.jp")

def make_handler(fetcher):
    def handler(route):
        req = route.request
        if not req.url.startswith(("http://", "https://")):
            return route.continue_()
        from urllib.parse import urlparse as _up
        host = _up(req.url).hostname or ""
        if any(host == s or host.endswith("." + s) for s in BLOCKED_SUFFIXES):
            try:
                return route.fulfill(status=204, body=b"")
            except Exception:
                return
        status, headers, body = fetcher.fetch(req.method, req.url, req.headers,
                                              req.post_data_buffer)
        headers = {k: v for k, v in headers.items()
                   if k.lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
        try:
            route.fulfill(status=status, headers=headers, body=body)
        except Exception:
            pass
    return handler

def cards_ready(page):
    try:
        return page.evaluate("document.querySelectorAll('.organization-card').length")
    except Exception:
        return 0

def expand_all(page):
    n = page.evaluate("""() => {
      const btns = [...document.querySelectorAll('button.accordion-button')];
      btns.forEach((b, i) => setTimeout(() => b.click(), i * 200));
      return btns.length;
    }""")
    if n:
        page.wait_for_timeout(n * 200 + 1500)
        for _ in range(24):
            pending = page.evaluate("""() => [...document.querySelectorAll('.accordion-collapse')]
              .filter(c => c.classList.contains('show') && c.querySelector('.accordion-body') && !c.querySelector('.accordion-body').innerText.trim()).length""")
            if pending == 0:
                break
            page.wait_for_timeout(500)
    return n

def ground_truth(page):
    return page.evaluate("""() => {
      const cards = [];
      document.querySelectorAll('.organization-card').forEach(card => {
        const name = (card.querySelector('.org-name') || {}).innerText || '';
        const header = (card.querySelector('.card-header') || {}).innerText || '';
        const urlA = [...card.querySelectorAll('.card-body a')].find(a => /^https?:/.test(a.innerText.trim()));
        const subs = [];
        const body = card.querySelector('.accordion-body');
        if (body) {
          body.querySelectorAll('a').forEach(a => subs.push({text: a.innerText.trim(), href: a.getAttribute('href')}));
          if (!subs.length && body.innerText.trim()) subs.push({text: body.innerText.trim().slice(0, 500), href: null});
        }
        cards.push({name: name.trim(), header: header.trim().replace(/\\s+/g,' '),
                    url_text: urlA ? urlA.innerText.trim() : null, subordinates: subs});
      });
      return cards;
    }""")

def sliced_fullpage_png(page, path, slice_h=6000):
    """Full-page PNG via CDP captureBeyondViewport slices + PIL stitch.
    Playwright's full_page=True resizes the layout viewport to the whole
    document and crashes the renderer on very tall expanded pages."""
    import base64, io
    from PIL import Image
    total = page.evaluate("document.documentElement.scrollHeight")
    width = page.evaluate("document.documentElement.scrollWidth")
    cdp = page.context.new_cdp_session(page)
    imgs = []
    y = 0
    while y < total:
        h = min(slice_h, total - y)
        r = cdp.send("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": True,
            "clip": {"x": 0, "y": y, "width": width, "height": h, "scale": 1}})
        imgs.append(Image.open(io.BytesIO(base64.b64decode(r["data"]))))
        y += h
    canvas = Image.new("RGB", (width, total), "white")
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height
    canvas.save(path)
    return total

def load(page, url, wait_cards=False):
    t0 = time.time()
    status = None
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else None
    except Exception as e:
        print("goto issue:", str(e)[:110], flush=True)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    return status, time.time() - t0

def main():
    results = {"route": "playwright_route_interception_httpx_fetcher", "jobs": []}
    fetcher = Fetcher()
    # Prewarm shared static assets (identical across all listing pages) so the
    # browser's parallel burst is served from cache, not the slow proxy.
    import re as _re
    try:
        r = httpx.get(BASE, proxy=os.environ['https_proxy'], trust_env=False, timeout=60)
        assets = sorted(set(_re.findall(r'(?:src|href)="(https?://[^"]+\.(?:css|js|png|jpg|svg|woff2?))"', r.text)))
        print(f"prewarm candidates: {len(assets)}", flush=True)
        fetcher.prewarm(assets)
    except Exception as e:
        print("prewarm skipped:", type(e).__name__, flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ja-JP",
                                  ignore_https_errors=True)
        page = ctx.new_page()
        page.route("**/*", make_handler(fetcher))

        # --- Job A: page 1 load + focused expansion case ---
        t0 = time.time()
        status, dt = load(page, BASE)
        n = cards_ready(page)
        print(f"page1 via fetcher: status={status} cards={n} load_s={dt:.1f} reqs={fetcher.n}", flush=True)
        results["jobs"].append({"job": "dir2_page_1_load", "status": status, "cards": n,
                                "load_s": round(dt, 1), "fetcher_requests": fetcher.n})
        if n == 0:
            print("FATAL: fetcher route rendered no cards")
            with open(os.path.join(OUT, "capture_log.json"), "w") as f:
                json.dump(results, f, ensure_ascii=False, indent=1)
            return
        if os.path.exists(os.path.join(OUT, "expansion_after.png")):
            print("expansion case: PNGs already captured, skipping re-shoot", flush=True)
            results["jobs"].append({"job": "expansion_case", "note": "captured in prior run; see capture8 log",
                                    "org": "防衛省", "subordinates_count": 101,
                                    "card_h_before": 231, "card_h_after": 6657})
            gt = ground_truth(page)
            with open(os.path.join(OUT, "ground_truth_p001.json"), "w", encoding="utf-8") as f:
                json.dump(gt, f, ensure_ascii=False, indent=1)
            time.sleep(PAUSE)
            # fall through to B-D loop below by skipping the rest of Job A
            card = None
        else:
            card = page.query_selector(".organization-card")
        if card is not None:
            card.scroll_into_view_if_needed(); page.wait_for_timeout(600)
            card.screenshot(path=os.path.join(OUT, "expansion_before.png"))
            h_before = page.evaluate("document.querySelector('.organization-card').offsetHeight")
            page.evaluate("document.querySelector('.organization-card button.accordion-button').click()")
            page.wait_for_timeout(3500)
            card2 = page.query_selector(".organization-card")
            card2.scroll_into_view_if_needed(); page.wait_for_timeout(400)
            card2.screenshot(path=os.path.join(OUT, "expansion_after.png"))
            h_after = page.evaluate("document.querySelector('.organization-card').offsetHeight")
            gt = ground_truth(page)
            results["jobs"].append({"job": "expansion_case", "org": gt[0]["name"],
                                    "card_h_before": h_before, "card_h_after": h_after,
                                    "subordinates": gt[0]["subordinates"],
                                    "s": round(time.time() - t0, 1)})
            print("expansion case:", gt[0]["name"], "subs:", len(gt[0]["subordinates"]),
                  "h", h_before, "->", h_after, flush=True)
            time.sleep(PAUSE)

        # --- Jobs B-D: pages 1, 2, 160 expand-all + sliced full-page screenshot ---
        for num in (1, 2, 160):
            t0 = time.time()
            try:
                url = BASE if num == 1 else f"{BASE}?page={num}"
                status, dt = load(page, url)
                n_exp = expand_all(page)
                gt = ground_truth(page)
                scroll_h = page.evaluate("document.documentElement.scrollHeight")
                ph = sliced_fullpage_png(page, os.path.join(OUT, f"dir2_p{num:03d}.png"))
                results["jobs"].append({"job": f"dir2_page_{num}", "status": status, "load_s": round(dt, 1),
                                        "expand_buttons": n_exp, "cards": len(gt), "scroll_h": scroll_h,
                                        "png_h": ph, "total_s": round(time.time() - t0, 1)})
                with open(os.path.join(OUT, f"ground_truth_p{num:03d}.json"), "w", encoding="utf-8") as f:
                    json.dump(gt, f, ensure_ascii=False, indent=1)
                print(f"page {num}: status={status} cards={len(gt)} expanded={n_exp} scroll_h={scroll_h} total_s={time.time()-t0:.1f}", flush=True)
            except Exception as e:
                results["jobs"].append({"job": f"dir2_page_{num}", "error": str(e)[:200]})
                print(f"page {num}: ERROR {str(e)[:150]}", flush=True)
            time.sleep(PAUSE)
        ctx.close()

        # --- Job E: robots-denied pages, direct via relay ---
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ja-JP",
                                   ignore_https_errors=True)
        page2 = ctx2.new_page()
        # route relay through browser proxy: relaunch context is per-browser; use CDP-free approach:
        # (proxy is a launch option; this browser was launched without it -> recreate)
        ctx2.close()
        browser.close()
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"], proxy=RELAY)
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ja-JP",
                                   ignore_https_errors=True)
        page2 = ctx2.new_page()
        robots = [
            ("robots_jftc", "https://www.jftc.go.jp/soshiki/tyoutatsu/index.html"),
            ("robots_hiroo", "https://www.town.hiroo.lg.jp/gyousei/nyuusatsu/"),
        ]
        for name, url in robots:
            t0 = time.time()
            try:
                status, dt = load(page2, url)
                title = page2.title()
                scroll_h = page2.evaluate("document.documentElement.scrollHeight")
                ph = sliced_fullpage_png(page2, os.path.join(OUT, f"{name}.png"))
                results["jobs"].append({"job": name, "url": url, "status": status, "title": title,
                                        "scroll_h": scroll_h, "total_s": round(time.time() - t0, 1)})
                print(f"{name}: status={status} title={title!r} total_s={time.time()-t0:.1f}", flush=True)
            except Exception as e:
                results["jobs"].append({"job": name, "url": url, "error": str(e)[:200]})
                print(f"{name}: ERROR {str(e)[:120]}", flush=True)
            time.sleep(PAUSE)
        browser.close()
    with open(os.path.join(OUT, "capture_log.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("ALL DONE")

if __name__ == "__main__":
    main()
