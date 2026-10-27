import json, re, csv
from collections import Counter
from urllib.parse import urlparse
rows=json.load(open("data/kkj_rows.json"))
# domain suffix classification
def dom_type(u):
    h=urlparse(u).netloc.lower()
    if h.endswith(".go.jp"): return "go.jp 政府機関"
    if h.endswith(".lg.jp"): return "lg.jp 自治体"
    if h.endswith(".ac.jp"): return "ac.jp 大学等"
    if h.endswith(".or.jp"): return "or.jp 団体"
    if h.endswith(".co.jp"): return "co.jp 会社/公社"
    if h.endswith(".jp"): return "jp その他"
    return "other/com等"
print("domain type", Counter(dom_type(r["url"]) for r in rows))
print("scheme", Counter(urlparse(r["url"]).scheme for r in rows))
# path keyword classification - page framework guess from URL alone (to be verified by sampling)
kw=[
 ("nyusatsu/nyuusatsu/nyusatu 入札", r"nyu+sa[ct]u"),
 ("chotatsu 調達", r"chotatsu|tyotatu|tyoutatsu"),
 ("kokuji/koukoku 公告", r"kokuji|koukoku|kokoku"),
 ("koubo 公募", r"koubo|kobo"),
 ("bid/procurement 英語", r"bid|procurement|tender"),
 ("keiyaku 契約", r"keiyaku"),
 ("ppi/電子入札システム系", r"ppi|ebid|denshi|electronic"),
 ("pdf直接", r"\.pdf($|\?)"),
 ("top/汎用 (入札キーワードなし)", r""),
]
def classify(u):
    low=u.lower()
    if low.split("?")[0].endswith(".pdf"): return "P7 PDF直接リンク型"
    if re.search(r"ppi|ebid|e-bid|denshi-nyusatsu|nyusatsu.*system", low): return "P4 電子入札/電子調達システム型"
    if re.search(r"nyu+sa[ct]u", low): return "P1 入札情報一覧型 (nyusatsuパス)"
    if re.search(r"chotatsu|tyotatu|tyoutatsu", low): return "P2 調達情報型 (chotatsuパス)"
    if re.search(r"kokuji|koukoku|koubo", low): return "P3 公告・公募型"
    if re.search(r"keiyaku", low): return "P5 契約情報型"
    if re.search(r"bid|procurement|tender|supplier", low): return "P1 入札情報一覧型 (英語パス)"
    # homepage-like: path is / or /index.html
    path=urlparse(u).path
    if path in ("","/") or re.search(r"/index\.(html?|php)$", path): return "P6 機関トップ/汎用ページ型 (要・サイト内探索)"
    return "P6 機関トップ/汎用ページ型 (要・サイト内探索)"
for r in rows: r["framework_guess"]=classify(r["url"])
print(Counter(r["framework_guess"] for r in rows))
# prefectures list
prefs=sorted(set(r["prefecture"] for r in rows))
print(len(prefs), prefs)
# municipalities per prefecture
muni=[r for r in rows if "市町村" in (r["category_kkj"] or "")]
print("muni rows", len(muni), "unique names", len(set(r["org_name"] for r in muni)))
# save classified
with open("output/kkj_classified.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
json.dump(rows, open("data/kkj_classified.json","w"), ensure_ascii=False, indent=1)
