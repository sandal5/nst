"""Merge round-2 queue A + B results and generate SUMMARY.md."""
import csv, os
from collections import Counter

BASE = "output/backfill2"
WORK = os.path.join(BASE, "work")
FN = ["queue", "org_name", "domain", "original_url", "status", "resolved_url",
      "page_title", "how_found", "note", "prefecture",
      "latest_detail_url", "latest_notice_title", "latest_notice_date"]

def load(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))

rows = load(os.path.join(WORK, "queueA_results.csv")) + load(os.path.join(WORK, "queueB_results.csv"))
rows = [{k: (r.get(k) or "") for k in FN} for r in rows]

out = os.path.join(BASE, "backfill2_results.csv")
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FN)
    w.writeheader()
    w.writerows(rows)

def block(title, rs):
    lines = [f"- {r['org_name']} ({r['domain']}) — {r['note']}" for r in rs]
    return [f"## {title}", "", f"Count: {len(rs)}", ""] + (lines or ["- (none)"]) + [""]

lines = ["# Backfill round 2 summary", ""]
lines.append("Method: on-site first (official homepage -> site navigation / site structure / on-site search, keywords 入札公告・調達・入札・契約, up to 3 levels, plus the site's own sitemap as fallback); only where on-site resolution failed was a site:-restricted search used. Every resolved page verified live (HTTP 200) in this run. Robots.txt checked first per host; a robots fetch returning 403 was re-verified three times at low speed before a host was concluded robots_blocked. >=1.5s per host; no retry on page-level 403/429. The resolved URL is the announcement list page (the polling endpoint); the newest announcement detail page on it is recorded as freshness evidence (latest_detail_url / latest_notice_title / latest_notice_date); lists whose newest notice is older than ~18 months are flagged possibly_stale in the note.")
lines.append("")
lines.append("Robots re-verification: all 29 rows first concluded robots_blocked were re-run in a separate, later gentle pass. 8 resolved, 9 moved to unresolved_manual with live crawl evidence (robots fetch succeeded on re-check; no listing found or homepage 403), and 12 were confirmed robots_blocked (repeated verdicts, concentrated on shared ministry hosts). The figures below are post-re-verification.")
lines.append("")
for q in ("A", "B"):
    qs = [r for r in rows if r["queue"] == q]
    c = Counter(r["status"] for r in qs)
    res = c.get("resolved", 0)
    rate = f"{res/len(qs)*100:.1f}%" if qs else "-"
    lines += [f"## Queue {q} totals", "",
              f"- Rows: {len(qs)} -> resolved {res} ({rate}), unresolved_manual {c.get('unresolved_manual',0)}, robots_blocked {c.get('robots_blocked',0)}, approval_pending {c.get('approval_pending',0)}"]
    det = sum(1 for r in qs if r["status"] == "resolved" and r["latest_detail_url"])
    dated = sum(1 for r in qs if r["status"] == "resolved" and r["latest_notice_date"])
    stale = sum(1 for r in qs if "possibly_stale" in r["note"])
    lines.append(f"- Resolved rows with a latest-announcement detail recorded: {det}; with a parsed notice date: {dated}; flagged possibly_stale: {stale}")
    lines.append("")
tot = Counter(r["status"] for r in rows)
lines += ["## Combined totals", "",
          f"- Rows: {len(rows)} -> resolved {tot.get('resolved',0)}, unresolved_manual {tot.get('unresolved_manual',0)}, robots_blocked {tot.get('robots_blocked',0)}, approval_pending {tot.get('approval_pending',0)}", ""]
lines += block("approval_pending (host could not be fetched from this environment; retry after approval cards are cleared)",
               [r for r in rows if r["status"] == "approval_pending"])
lines += block("robots_blocked (robots.txt disallows; manual confirmation needed)",
               [r for r in rows if r["status"] == "robots_blocked"])
lines += block("unresolved_manual (with per-row reasons)",
               [r for r in rows if r["status"] == "unresolved_manual"])

with open(os.path.join(BASE, "SUMMARY.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("merged rows:", len(rows), dict(tot))
for q in ("A", "B"):
    qs = [r for r in rows if r["queue"] == q]
    c = Counter(r["status"] for r in qs)
    print(f"queue {q}: {len(qs)} rows", dict(c))
