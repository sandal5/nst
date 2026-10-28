#!/usr/bin/env python3
"""Build pilot_table.csv from OCR-parsed entries of the pilot pages.
Columns: page, seq, org_name, prefecture, parent_org, url_text, depth, y.
org_name/url_text are OCR-derived (cleaned name, raw OCR URL). Depth from
the name line's x coordinate (top-level ~122px, subordinate ~204px at
1440px viewport width); parent = most recent preceding top-level entry."""
import csv, json, os, sys
from evaluate import parse_entries, clean_name, pref_of

HERE = os.path.dirname(os.path.abspath(__file__))

def page_entries(page):
    lines = json.load(open(os.path.join(HERE, "ocr_lines", f"{page}.json"), encoding="utf-8"))
    by_y = {}
    for l in lines:
        by_y.setdefault(round(l["y"]), l["x"])
    entries = parse_entries(lines)
    out = []
    for e in entries:
        x = by_y.get(round(e["y"]), 0)
        name = clean_name(e["name_raw"])
        out.append({"name": name, "prefecture": pref_of(name),
                    "url": e["url_raw"] or "", "depth": 2 if x >= 160 else 1,
                    "y": round(e["y"])})
    return out

def main():
    pages = sys.argv[1:] or ["dir2_p001", "dir2_p002", "dir2_p160"]
    rows = []
    for page in pages:
        num = int(page.split("_p")[1])
        parent = ""
        for i, e in enumerate(page_entries(page), 1):
            if e["depth"] == 1:
                parent = e["name"]
                p = ""
            else:
                p = parent
            rows.append({"page": num, "seq": i, "org_name": e["name"],
                         "prefecture": e["prefecture"], "parent_org": p,
                         "url_text": e["url"], "depth": e["depth"], "y": e["y"]})
    with open(os.path.join(HERE, "pilot_table.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["page", "seq", "org_name", "prefecture",
                                          "parent_org", "url_text", "depth", "y"])
        w.writeheader()
        w.writerows(rows)
    print(f"pilot_table.csv: {len(rows)} rows")

if __name__ == "__main__":
    main()
