"""Parse the single KKJ link-collection page already retrieved for directory research.
NOTE compliance: kkj.go.jp robots.txt = Disallow:/ for all agents.
This script does NOT fetch kkj again; it only parses the local copy obtained via browser research.
Daily collection must target official issuer sites directly, after checking each site's terms/robots.
"""
import csv, re, json
from pathlib import Path
from bs4 import BeautifulSoup

html = Path("data/kkj_cal1.html").read_text(errors="ignore")
soup = BeautifulSoup(html, "lxml")
# Find start: the div after link collection header
rows=[]
pref=None; cat=None; group=None
# Walk elements in document order inside body, tracking h1/h2/h3 and a[href^=http]
# But there are h1 for search sections too; start after "発注機関へのリンク集"
start=False
for el in soup.find_all(["h1","h2","h3","a"]):
    text=el.get_text(strip=True)
    if el.name=="h1" and text=="発注機関へのリンク集":
        start=True; continue
    if not start: continue
    if el.name=="h1":
        # prefecture name or next major section; prefectures are 47 names
        pref=text; cat=None; group=None
    elif el.name=="h2":
        cat=text
    elif el.name=="h3":
        group=text
    elif el.name=="a":
        href=el.get("href","")
        if href.startswith("http") and text:
            rows.append({"prefecture":pref,"category_kkj":cat,"group_ministry":group,"org_name":text,"url":href,"source":"kkj.go.jp/s/?cal=1"})
print(f"rows={len(rows)}")
from collections import Counter
print("by prefecture top", Counter(r["prefecture"] for r in rows).most_common(5))
print("by category", Counter(r["category_kkj"] for r in rows))
print("unique urls", len(set(r["url"] for r in rows)), "unique org names", len(set(r["org_name"] for r in rows)))
Path("data/kkj_rows.json").write_text(json.dumps(rows,ensure_ascii=False,indent=1))
with open("output/kkj_source_list.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=["prefecture","category_kkj","group_ministry","org_name","url","source"])
    w.writeheader(); w.writerows(rows)
print("written output/kkj_source_list.csv")
for r in rows[:5]: print(r)
