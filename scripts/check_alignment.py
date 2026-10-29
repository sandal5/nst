#!/usr/bin/env python3
"""check_alignment.py — nst 对象清单「机关名 <-> URL」对齐预检。

背景: 2026-10-29 全量对齐审计 (output/alignment_audit/) 的常设化脚本。
和歌山県警察本部行挂兵庫県警察页、福島地本行挂札幌地本页两次取り違え
的教训: 名字与 URL 拿错本体只靠逐行复核才发现。今后**每次落库前必跑**
本脚本的结构预检, 输出的跨主机行/共用组/都道府县信号行须逐项定性后
才可并入。

结构预检 (离线, 不取页):
  (a) daily_collection_target 主机与 official_domain 不一致 (非其子域)
      的行全量列出;
  (b) 同一 target URL 被多个不同机关共用的组全量列出 (共用可能正当,
      须按组定性: 委任/共通系统/上级机关页等);
  (c) 都道府县信号粗筛: official_domain 与 target 的主机/路径 token 中
      出现与本行 prefecture 不同的都道府县 romaji 名, 或 org_name 以
      其他都道府县名开头。

--fetch 模式: 对 http target 行经网关 (/fetch) 取页, 以 <title>/h1 与
org_name 的归一化比对做实证 (剥法人格前缀后核心名须出现在标题层);
取不到页与名字缺失的行单列。网关密钥读 ~/workspace/jp-procurement/
.gateway.env; >=1.5s/主机节奏; 403/429 即停该主机。

用法:
  python3 scripts/check_alignment.py [--csv data/master_source_list.csv]
          [--out report.csv] [--fetch] [--limit N]
退出码: 结构预检发现 (a) 或 (c) 行时为 1, 否则 0 (--fetch 时名字
缺失行亦计入)。
"""
import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
         "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
         "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
         "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
         "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
         "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
         "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
PREF_ROMAJI = {
    "北海道": "hokkaido", "青森県": "aomori", "岩手県": "iwate", "宮城県": "miyagi",
    "秋田県": "akita", "山形県": "yamagata", "福島県": "fukushima",
    "茨城県": "ibaraki", "栃木県": "tochigi", "群馬県": "gunma", "埼玉県": "saitama",
    "千葉県": "chiba", "東京都": "tokyo", "神奈川県": "kanagawa",
    "新潟県": "niigata", "富山県": "toyama", "石川県": "ishikawa", "福井県": "fukui",
    "山梨県": "yamanashi", "長野県": "nagano", "岐阜県": "gifu",
    "静岡県": "shizuoka", "愛知県": "aichi", "三重県": "mie", "滋賀県": "shiga",
    "京都府": "kyoto", "大阪府": "osaka", "兵庫県": "hyogo", "奈良県": "nara",
    "和歌山県": "wakayama", "鳥取県": "tottori", "島根県": "shimane",
    "岡山県": "okayama", "広島県": "hiroshima", "山口県": "yamaguchi",
    "徳島県": "tokushima", "香川県": "kagawa", "愛媛県": "ehime", "高知県": "kochi",
    "福岡県": "fukuoka", "佐賀県": "saga", "長崎県": "nagasaki", "熊本県": "kumamoto",
    "大分県": "oita", "宮崎県": "miyazaki", "鹿児島県": "kagoshima", "沖縄県": "okinawa",
}
ROMAJI2PREF = {v: k for k, v in PREF_ROMAJI.items()}

LEGAL_PREFIXES = [
    "国立大学法人", "公立大学法人", "独立行政法人", "地方独立行政法人",
    "国立研究開発法人", "大学共同利用機関法人", "社会福祉法人",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "学校法人", "医療法人", "宗教法人", "特定非営利活動法人",
]
SEP_RE = re.compile(r"[\s・、,，．.·\-‐―_/／\\|｜:：;；()（）\[\]【】「」『』\"'“”<>＜＞~〜…☆★●○※]")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return SEP_RE.sub("", s.lower())


BRACKET_RE = re.compile(r"（[^）]*）|\([^)]*\)")


def strip_legal(name):
    n = unicodedata.normalize("NFKC", name or "")
    n = BRACKET_RE.sub("", n)  # （独）（国研）（特）等の注記は本体名でない
    changed = True
    while changed:
        changed = False
        for p in LEGAL_PREFIXES:
            if n.startswith(p):
                n = n[len(p):]
                changed = True
    return n


def host_of(u):
    try:
        return urlsplit(u).netloc.lower()
    except Exception:
        return ""


def tokens_of(url):
    sp = urlsplit(url)
    toks = re.split(r"[.\-/]", sp.netloc.lower()) + re.split(r"[.\-/]", sp.path.lower())
    return [t for t in toks if t]


def structural(rows):
    """Return (cross_host, shared_groups, pref_flags, placeholder_count)."""
    http_rows = [r for r in rows if r["daily_collection_target"].startswith("http")]
    placeholder = len(rows) - len(http_rows)
    cross = []
    for r in http_rows:
        h = host_of(r["daily_collection_target"])
        d = (r["official_domain"] or "").lower().strip()
        if not d or (h != d and not h.endswith("." + d)):
            cross.append(r)
    byurl = defaultdict(list)
    for r in http_rows:
        byurl[r["daily_collection_target"]].append(r)
    shared = {u: rs for u, rs in byurl.items()
              if len({x["org_name"] for x in rs}) > 1}
    flags = []
    for r in http_rows:
        own = PREF_ROMAJI.get(r["prefecture"])
        seen = set()
        for src, is_domain in ((r["official_domain"], True), (r["daily_collection_target"], False)):
            u = ("https://" + src) if is_domain else src
            for t in tokens_of(u):
                if t in ROMAJI2PREF and t != own:
                    seen.add(ROMAJI2PREF[t])
        for p in PREFS:
            if p != r["prefecture"] and r["org_name"].startswith(p):
                seen.add(p + "(org_name)")
        if seen:
            flags.append((r, sorted(seen)))
    return cross, shared, flags, placeholder


def fetch_mode(rows, args):
    """Gateway page-evidence check: org core name must appear in title/h1."""
    import base64
    import threading
    import time
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        print("--fetch requires httpx + beautifulsoup4 (use the jp-procurement .venv)")
        return 0
    base = os.path.expanduser("~/workspace/jp-procurement")
    env = {}
    for line in open(os.path.join(base, ".gateway.env")):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            env[k.strip()] = v.strip()
    gw, key = env["GATEWAY_URL"].rstrip("/"), env["GATEWAY_KEY"]
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    client = httpx.Client(proxy=proxy, trust_env=False, timeout=60)
    last = {}
    lock = threading.Lock()

    def pace(host):
        with lock:
            now = time.time()
            start = max(now, last.get(host, 0) + 1.55)
            last[host] = start
            wait = start - now
        if wait > 0:
            time.sleep(wait)

    def fetch(url):
        pace(urlsplit(url).netloc)
        r = client.get(gw + "/fetch", params={"url": url}, headers={"X-Gateway-Key": key})
        j = r.json()
        if "error" in j:
            return None, f"gateway: {j.get('error')}"
        body = base64.b64decode(j["body_base64"]) if j.get("body_base64") else b""
        return (j.get("status"), body), ""

    http_rows = [r for r in rows if r["daily_collection_target"].startswith("http")]
    if args.limit:
        http_rows = http_rows[:args.limit]
    byurl = defaultdict(list)
    for r in http_rows:
        byurl[r["daily_collection_target"]].append(r)
    missing = 0
    stopped = set()
    for u, rs in byurl.items():
        host = urlsplit(u).netloc.lower()
        if host in stopped:
            print(f"    UNFETCHABLE(host stopped) {u} rows={[x['_oid'] for x in rs]}")
            missing += len(rs)
            continue
        got, err = fetch(u)
        if got is None:
            print(f"    UNFETCHABLE {u}: {err}")
            missing += len(rs)
            continue
        status, body = got
        if status in (403, 429):
            stopped.add(host)
            print(f"    HOST STOP {host} http {status}; rows={[x['_oid'] for x in rs]}")
            missing += len(rs)
            continue
        if not status or not (200 <= status < 400):
            print(f"    UNFETCHABLE {u}: http {status} rows={[x['_oid'] for x in rs]}")
            missing += len(rs)
            continue
        soup = BeautifulSoup(body, "lxml")
        title = soup.title.string if soup.title and soup.title.string else ""
        h1 = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
        head = norm(title + " " + h1)
        for r in rs:
            k = norm(strip_legal(r["org_name"]))
            if k and k not in head:
                print(f"    NAME-ABSENT {r['_oid']} {r['org_name']} | {u} | title={title}")
                missing += 1
    print(f"--fetch done: problem rows={missing}")
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.expanduser(
        "~/workspace/jp-procurement/data/master_source_list.csv"))
    ap.add_argument("--out", default="")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = list(csv.DictReader(io.StringIO(open(args.csv, encoding="utf-8-sig").read())))
    for i, r in enumerate(rows, 1):
        r["_oid"] = f"OBJ-K{i:04d}"
    cross, shared, flags, placeholder = structural(rows)
    http_n = sum(1 for r in rows if r["daily_collection_target"].startswith("http"))
    print(f"rows={len(rows)} http_targets={http_n} placeholder={placeholder}")
    print(f"(a) cross-host rows: {len(cross)}")
    for r in cross:
        print(f"    {r['_oid']} {r['org_name']} | domain={r['official_domain']} | target={r['daily_collection_target']}")
    print(f"(b) shared-url groups: {len(shared)} (rows {sum(len(v) for v in shared.values())})")
    for u, rs in sorted(shared.items()):
        print(f"    {u} -> {[x['org_name'] for x in rs]}")
    print(f"(c) prefecture-signal flags: {len(flags)}")
    for r, seen in flags:
        print(f"    {r['_oid']} {r['org_name']} ({r['prefecture']}) signals={seen} domain={r['official_domain']} target={r['daily_collection_target']}")

    bad = 0
    if args.fetch:
        bad = fetch_mode(rows, args)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["check", "oid", "org_name", "prefecture", "official_domain", "target", "detail"])
            for r in cross:
                w.writerow(["cross_host", r["_oid"], r["org_name"], r["prefecture"], r["official_domain"], r["daily_collection_target"], ""])
            for u, rs in shared.items():
                for r in rs:
                    w.writerow(["shared_url", r["_oid"], r["org_name"], r["prefecture"], r["official_domain"], u, f"group of {len(rs)}"])
            for r, seen in flags:
                w.writerow(["pref_signal", r["_oid"], r["org_name"], r["prefecture"], r["official_domain"], r["daily_collection_target"], ";".join(seen)])
        print(f"wrote {args.out}")
    return 1 if (cross or flags or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
