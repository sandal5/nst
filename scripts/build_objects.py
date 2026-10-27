"""Build the object registry (13,528 polling objects) + deduplicated endpoint list.

Objects = issuing organizations (from the ONE-TIME enumeration of the two
directories). Endpoints = the actual URLs to poll. Many objects share one
endpoint (a ministry procurement page, a prefectural joint e-procurement
system, a parent org page) — polling is per-endpoint, never per-object.
Sources: data/master_source_list.csv (KKJ-derived, 3,966 rows incl. 2 central
systems) and data/dir2_objects.csv (when the one-time dir2 enumeration
lands: columns org_name,prefecture,official_url).
"""
import csv, json, os
from urllib.parse import urlparse
from collections import defaultdict

objects=[]; 
rows=list(csv.DictReader(open("data/master_source_list.csv",encoding="utf-8-sig")))
for i,r in enumerate(rows,1):
    objects.append({"object_id":f"OBJ-K{i:04d}","org_name":r["org_name"],"prefecture":r["prefecture"],
      "level":r["level"],"official_domain":r["official_domain"],
      "candidate_endpoint_url":(r["daily_collection_target"] if r["daily_collection_target"].startswith("http") else (r["source_url_kkj"] or (("https://"+r["official_domain"]+"/") if r["official_domain"] else ""))),
      "framework":r["framework_guess_by_url"],"source":("kkj (one-time enumeration)" if not r["in_kkj"].startswith("no") else "supplement (one-time cross-check)")})
dir2_path="data/dir2_objects.csv"
n_dir2=0
if os.path.exists(dir2_path):
    for j,r in enumerate(csv.DictReader(open(dir2_path,encoding="utf-8-sig")),1):
        n_dir2+=1
        objects.append({"object_id":f"OBJ-D{j:05d}","org_name":r.get("org_name",""),"prefecture":r.get("prefecture",""),
          "level":"","official_domain":urlparse(r.get("official_url","")).netloc if r.get("official_url") else "",
          "candidate_endpoint_url":r.get("official_url",""),"framework":"","source":"dir2 (one-time enumeration)"})
print("objects:", len(objects), "(dir2 included:", n_dir2, ")")
# endpoints: group by candidate URL
by_url=defaultdict(list)
for o in objects:
    if o["candidate_endpoint_url"]: by_url[o["candidate_endpoint_url"]].append(o["object_id"])
endpoints=[{"endpoint_id":f"EP-{i:04d}","url":u,"host":urlparse(u).netloc,"object_count":len(ids),"object_ids":" ".join(ids[:20]),
            "poll_tier":"B (default; auto-adjusts)","terms_status":"unverified","status":"candidate"}
           for i,(u,ids) in enumerate(sorted(by_url.items(), key=lambda kv:-len(kv[1])),1)]
print("endpoints (unique candidate URLs):", len(endpoints))
print("objects covered by a candidate endpoint:", sum(e["object_count"] for e in endpoints))
with open("data/objects_registry.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(objects[0].keys())); w.writeheader(); w.writerows(objects)
with open("data/endpoints.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(endpoints[0].keys())); w.writeheader(); w.writerows(endpoints)
# how many objects share endpoints vs unique
shared=sum(e["object_count"] for e in endpoints if e["object_count"]>1)
print("objects sharing an endpoint with >=1 other object:", shared, "| endpoints serving >1 object:", sum(1 for e in endpoints if e["object_count"]>1))
