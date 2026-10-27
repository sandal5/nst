"""White-hat link checker: 1 request to robots.txt per domain, then (only if allowed) 1 GET to target.
Rate: >=1.5s between requests to same host, descriptive UA, no retries, no parallel hammering.
Usage: check_links.py input.csv output.csv [limit]
"""
import csv, sys, time, re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx

UA="JPProcurementResearch/1.0 (white-hat link verification; human-directed research)"
inp, outp = sys.argv[1], sys.argv[2]
limit=int(sys.argv[3]) if len(sys.argv)>3 else 9999
rows=list(csv.DictReader(open(inp,encoding="utf-8-sig")))[:limit]
robots_cache={}
_client=httpx.Client(headers={"User-Agent":UA}, timeout=10, trust_env=False, proxy=__import__("os").environ.get("https_proxy"))
def allowed(url):
    p=urlparse(url); base=f"{p.scheme}://{p.netloc}"
    if base not in robots_cache:
        rp=RobotFileParser(); rp.set_url(base+"/robots.txt")
        try:
            rr=_client.get(base+"/robots.txt")
            if rr.status_code==200: rp.parse(rr.text.splitlines())
            elif rr.status_code in (401,403): rp.parse(["User-agent: *","Disallow: /"])
            else: rp.parse([])  # 404等は制限なしとみなす
        except Exception: rp.parse([])
        robots_cache[base]=rp
        time.sleep(0.5)
    rp=robots_cache[base]
    if rp is None: return True, "robots取得不可(不存在等)"
    ok=rp.can_fetch(UA, url)
    return ok, "robots許可" if ok else "robots禁止→手動確認対象"
out=[]
with httpx.Client(headers={"User-Agent":UA}, timeout=10, follow_redirects=True, trust_env=False, proxy=__import__("os").environ.get("https_proxy")) as c:
    for i,r in enumerate(rows):
        url=r["url"]; ok,note=allowed(url)
        rec={**r,"robots":note,"http_status":"","final_url":"","page_title":"","checked_note":""}
        if not ok:
            rec["checked_note"]="skip自動取得"
            out.append(rec); print(i+1, "ROBOTS-DENY", url); continue
        try:
            resp=c.get(url)
            rec["http_status"]=resp.status_code; rec["final_url"]=str(resp.url)
            m=re.search(r"<title[^>]*>([^<]{1,120})</title>", resp.text, re.I|re.S)
            rec["page_title"]=re.sub(r"\s+"," ",m.group(1)).strip() if m else ""
            if resp.status_code>=400: rec["checked_note"]="要・新ページ探索"
            elif str(resp.url)!=url: rec["checked_note"]="リダイレクトあり(転送先を正とする候補)"
        except Exception as e:
            rec["checked_note"]=f"接続エラー: {type(e).__name__}"
            print(i+1,"ERR",url,e)
        out.append(rec); print(i+1, rec["http_status"], rec["checked_note"], url, "->", rec["final_url"][:90])
        time.sleep(1.5)
with open(outp,"w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
print("done", len(out))
