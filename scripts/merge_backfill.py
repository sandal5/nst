"""Merge backfill passes into the final deliverables.

Sources:
- output/backfill/pref_muni_backfill.csv : pass 1 results for all 487 original rows (base)
- output/backfill/pass3_results.csv      : re-run of pass-1 non-resolved rows (wins when resolved/robots_blocked)
- output/backfill/enrich_results.csv     : latest-notice enrichment for pass-1 resolved rows
- output/backfill/addendum_results.csv   : 65 addendum rows (batch tag addendum_65)

Writes final output/backfill/pref_muni_backfill.csv (all 552 rows, new columns)
and output/backfill/SUMMARY.md.
"""
import csv, os, re, datetime

BASE = "output/backfill"
FN = ["org_name", "domain", "original_url", "status", "resolved_url", "page_title",
      "how_found", "note", "prefecture", "latest_detail_url", "latest_notice_title",
      "latest_notice_date"]

def load(name):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        return []
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))

def stale_flag(date_iso):
    if not date_iso:
        return ""
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
        if (datetime.date(2026, 10, 27) - datetime.date(y, m, d)).days > 548:
            return f" possibly_stale: newest notice {date_iso}"
    except Exception:
        pass
    return ""

pass1 = load("pref_muni_backfill.csv")
pass3 = {(r["org_name"], r["original_url"]): r for r in load("pass3_results.csv")}
enrich = {(r["org_name"], r["original_url"]): r for r in load("enrich_results.csv")}
addendum = load("addendum_results.csv")

final = []
for r in pass1:
    key = (r["org_name"], r["original_url"])
    p3 = pass3.get(key)
    if p3:
        # pass 3 re-ran every non-resolved pass-1 row with gentler settings;
        # its verdict is the fresher one and always wins for covered rows.
        row = {k: p3.get(k, "") for k in FN}
    else:
        row = {k: r.get(k, "") for k in FN}
    if row["status"] == "resolved":
        e = enrich.get(key)
        if e and not row.get("latest_detail_url"):
            row["latest_detail_url"] = e["latest_detail_url"]
            row["latest_notice_title"] = e["latest_notice_title"]
            row["latest_notice_date"] = e["latest_notice_date"]
            if e["enrich_note"]:
                row["note"] = (row["note"] + " | enrich: " + e["enrich_note"]).strip()
        sf = stale_flag(row.get("latest_notice_date", ""))
        if sf and "possibly_stale" not in row["note"]:
            row["note"] += sf
    final.append(row)

n_add = 0
for r in addendum:
    row = {k: r.get(k, "") for k in FN}
    row["note"] = ("batch=addendum_65; " + row["note"]).strip("; ")
    sf = stale_flag(row.get("latest_notice_date", ""))
    if row["status"] == "resolved" and sf and "possibly_stale" not in row["note"]:
        row["note"] += sf
    final.append(row)
    n_add += 1

with open(os.path.join(BASE, "pref_muni_backfill_final.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FN)
    w.writeheader()
    w.writerows(final)

# ---- stats ----
from collections import Counter
def stats(rows):
    c = Counter(r["status"] for r in rows)
    return c
orig = final[:len(pass1)]
add = final[len(pass1):]
co, ca = stats(orig), stats(add)
how = Counter(r["how_found"] for r in final if r["status"] == "resolved")
with_latest = sum(1 for r in final if r["status"] == "resolved" and r["latest_detail_url"])
with_date = sum(1 for r in final if r["status"] == "resolved" and r["latest_notice_date"])
stale = [r for r in final if "possibly_stale" in r["note"]]
appr = [r for r in final if r["status"] == "approval_pending"]
unres = [r for r in final if r["status"] == "unresolved_manual"]
robots = [r for r in final if r["status"] == "robots_blocked"]

def pct(n, d):
    return f"{(100.0*n/d):.1f}%" if d else "-"

lines = []
lines.append("# Backfill summary: prefecture/municipality dead (404) links")
lines.append("")
lines.append("Method: on-site first (official homepage -> site navigation / site structure, keywords 入札公告・調達・入札・契約, up to 3 levels, plus the site's own sitemap as fallback), every resolved page verified live (HTTP 200) in this run. Robots.txt checked first per host; >=1.5s per host; no retry on 403/429. The resolved URL is the announcement list page (the polling endpoint); the newest announcement detail page on it is recorded as freshness evidence (latest_detail_url / latest_notice_title / latest_notice_date).")
lines.append("")
lines.append("## Totals")
lines.append("")
lines.append(f"- Original batch: {len(orig)} rows -> resolved {co.get('resolved',0)} ({pct(co.get('resolved',0), len(orig))}), unresolved_manual {co.get('unresolved_manual',0)}, robots_blocked {co.get('robots_blocked',0)}, approval_pending {co.get('approval_pending',0)}")
lines.append(f"- Addendum batch (addendum_65): {len(add)} rows -> resolved {ca.get('resolved',0)} ({pct(ca.get('resolved',0), len(add))}), unresolved_manual {ca.get('unresolved_manual',0)}, robots_blocked {ca.get('robots_blocked',0)}, approval_pending {ca.get('approval_pending',0)}")
lines.append(f"- Combined: {len(final)} rows -> resolved {co.get('resolved',0)+ca.get('resolved',0)} ({pct(co.get('resolved',0)+ca.get('resolved',0), len(final))})")
lines.append(f"- Resolved via: " + ", ".join(f"{k} {v}" for k, v in how.items()))
lines.append(f"- Resolved rows with a latest-announcement detail recorded: {with_latest}; with a parsed notice date: {with_date}")
lines.append(f"- Lists flagged possibly_stale (newest notice older than ~18 months): {len(stale)}")
lines.append("")
lines.append("## approval_pending (host could not be fetched from this environment; retry after approval cards are cleared)")
lines.append("")
lines.append(f"Count: {len(appr)}")
lines.append("")
for r in appr:
    lines.append(f"- {r['org_name']} ({r['domain']})")
lines.append("")
lines.append("## robots_blocked (robots.txt disallows; manual confirmation needed)")
lines.append("")
lines.append(f"Count: {len(robots)}")
lines.append("")
for r in robots:
    lines.append(f"- {r['org_name']} ({r['domain']})")
lines.append("")
lines.append("## Unresolved (needs human work)")
lines.append("")
lines.append(f"Count: {len(unres)}")
lines.append("")
for r in unres:
    lines.append(f"- {r['org_name']} ({r['domain']}) - {r['note']}")
lines.append("")
lines.append("## Possibly stale lists")
lines.append("")
for r in stale:
    lines.append(f"- {r['org_name']} ({r['domain']}) - newest notice {r['latest_notice_date']} - {r['resolved_url']}")
lines.append("")
with open(os.path.join(BASE, "SUMMARY.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("final rows:", len(final), "| orig", dict(co), "| addendum", dict(ca))
print("approval_pending:", len(appr), "| unresolved:", len(unres), "| robots:", len(robots), "| stale:", len(stale))
