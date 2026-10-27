"""Merge the 2026-10-28 day-2 batch results into the registry.

Sources:
  1. output/day2/conn_error_rerun.csv      -> data/master_source_list.csv
     (key (org_name, recorded_url) against (org_name, source_url_kkj);
     conventions copied from scripts/merge_gateway_run.py, dated 2026-10-28)
  2. output/day2/site_templates_updated.csv -> data/site_templates.csv
     (full-file replacement after sanity checks)
  3. data/remaining_sites.csv refreshed in place from source 1 verdicts.

Status strings applied (same shapes as the 2026-10-27 merges):
  recovered_200      -> 検証済 200 (復核 2026-10-28), target = final_url
  recovered_redirect -> 検証済 跳转 (復核 2026-10-28), target = final_url
  dead_404           -> 404 現行ページ未確定 (要手動)
  forbidden_403      -> 要手動確認 (403)
  robots_disallowed  -> robots 禁止 (手動確認対象)
  still_error        -> master row unchanged
Latest-detail evidence (URL/title/date) is carried in
output/day2/merge_changes.csv — the master schema has no detail columns and
merge_gateway_run.py likewise wrote only status + target to the master.

Writes output/day2/master_before_day2_merge.csv (backup),
output/day2/remaining_before_day2_merge.csv (backup),
output/day2/merge_changes.csv and output/day2/merge_log.md.
"""
import csv, os, shutil
from collections import Counter, defaultdict

RES_200 = "検証済 200 (復核 2026-10-28)"
RES_REDIR = "検証済 跳转 (復核 2026-10-28)"
DEAD_MANUAL = "404 現行ページ未確定 (要手動)"
ROBOTS = "robots 禁止 (手動確認対象)"
MANUAL_403 = "要手動確認 (403)"
CONN_ERR = "接続エラー 未確認 (再検証队列)"

MASTER = "data/master_source_list.csv"
REMAINING = "data/remaining_sites.csv"
TEMPLATES = "data/site_templates.csv"
RERUN = "output/day2/conn_error_rerun.csv"
TEMPLATES_NEW = "output/day2/site_templates_updated.csv"


def load(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def is_http(u):
    return bool(u) and u.startswith("http")


def verdict(r):
    s = r["result"]
    if s == "recovered_200":
        return ("resolved", RES_200, r["final_url"])
    if s == "recovered_redirect":
        return ("resolved", RES_REDIR, r["final_url"])
    if s == "dead_404":
        return ("status", DEAD_MANUAL, None)
    if s == "forbidden_403":
        return ("status", MANUAL_403, None)
    if s == "robots_disallowed":
        return ("status", ROBOTS, None)
    return (None, None, None)  # still_error


def census(rows):
    return sum(1 for r in rows if r["verification_status"].startswith("検証済"))


def main():
    log = ["# Day-2 merge log (2026-10-28)", ""]
    anomalies = []

    # ---------------- 1. master write-back ----------------
    master = load(MASTER)
    shutil.copy(MASTER, "output/day2/master_before_day2_merge.csv")
    before = census(master)
    idx = defaultdict(list)
    for i, r in enumerate(master):
        idx[(r["org_name"], r["source_url_kkj"])].append(i)

    rerun = load(RERUN)
    tally = Counter()
    changes = []
    matched_master = {}  # rerun key -> rerun row (for remaining refresh)
    for r in rerun:
        key = (r["org_name"], r["recorded_url"])
        matched_master[key] = r
        hits = idx.get(key, [])
        if not hits:
            tally["unmatched"] += 1
            anomalies.append(f"rerun row unmatched in master: {key}")
            continue
        kind, new_status, target = verdict(r)
        if kind is None:
            tally["no_change_still_error"] += 1
            continue
        for i in hits:
            m = master[i]
            if m["verification_status"].startswith("検証済"):
                anomalies.append(f"{key}: already resolved ({m['verification_status']}); skipped")
                tally["already_resolved_guard"] += 1
                continue
            if m["verification_status"] != CONN_ERR:
                anomalies.append(f"{key}: unexpected pre-merge status {m['verification_status']!r}")
            old_status, old_target = m["verification_status"], m["daily_collection_target"]
            m["verification_status"] = new_status
            if kind == "resolved":
                if is_http(target):
                    m["daily_collection_target"] = target
                else:
                    anomalies.append(f"{key}: resolved without http target ({target!r})")
            changes.append({
                "source": "conn_error_rerun", "org_name": m["org_name"],
                "official_domain": m["official_domain"],
                "source_url_kkj": m["source_url_kkj"],
                "old_status": old_status, "new_status": new_status,
                "old_target": old_target, "new_target": m["daily_collection_target"],
                "latest_detail_url": r["latest_detail_url"],
                "latest_notice_title": r["latest_notice_title"],
                "latest_notice_date": r["latest_notice_date"],
            })
            tally[f"applied_{r['result']}"] += 1

    fn = list(master[0].keys())
    with open(MASTER, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        w.writerows(master)
    with open("output/day2/merge_changes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["source", "org_name", "official_domain",
                                          "source_url_kkj", "old_status", "new_status",
                                          "old_target", "new_target", "latest_detail_url",
                                          "latest_notice_title", "latest_notice_date"])
        w.writeheader()
        w.writerows(changes)
    after = census(master)
    rec_applied = tally["applied_recovered_200"] + tally["applied_recovered_redirect"]
    log += ["## 1. Master write-back (conn_error_rerun, 298 rows)",
            f"- Master rows: {len(master)}; census: {before} -> {after} "
            f"(expected 3263 -> 3308, net +45 recovered).",
            f"- Recovered verdicts applied to master rows: {rec_applied} "
            f"(recovered_200 {tally['applied_recovered_200']}, "
            f"recovered_redirect {tally['applied_recovered_redirect']}).",
            f"- Status verdicts applied: dead_404 {tally['applied_dead_404']}, "
            f"forbidden_403 {tally['applied_forbidden_403']}, "
            f"robots_disallowed {tally['applied_robots_disallowed']}.",
            f"- still_error rows left unchanged: {tally['no_change_still_error']}; "
            f"unmatched rerun rows: {tally['unmatched']}.",
            f"- Census check: {'OK — exactly +45' if after - before == 45 else 'MISMATCH — see anomalies'}.",
            "- Latest-detail evidence among the 45 recovered: "
            f"{sum(1 for c in changes if c['new_status'].startswith('検証済') and c['latest_detail_url'])} rows carry a detail URL, "
            f"{sum(1 for c in changes if c['latest_detail_url'] and c['latest_notice_date'])} of them a parsed date "
            "(recorded in output/day2/merge_changes.csv; master schema has no detail columns, "
            "same as merge_gateway_run.py).",
            ""]

    # ---------------- 2. templates replacement ----------------
    old_t = load(TEMPLATES)
    new_t = load(TEMPLATES_NEW)
    assert len(new_t) == 3005, len(new_t)
    day2_sample = sum(1 for r in new_t if r["evidence"].startswith("gateway_sample (2026-10-28)"))
    live_sample = sum(1 for r in new_t if r["evidence"].startswith("gateway_sample"))
    key_old = [(r["org_name"], r["list_url"]) for r in old_t]
    key_new = [(r["org_name"], r["list_url"]) for r in new_t]
    assert key_old == key_new, "template key order/set changed"
    rows_changed = sum(1 for a, b in zip(old_t, new_t) if a != b)
    shutil.copy(TEMPLATES_NEW, TEMPLATES)
    p_dist = Counter(r["p_class"] for r in new_t)
    p_old = Counter(r["p_class"] for r in old_t)
    log += ["## 2. Templates replacement (site_templates_updated.csv)",
            f"- Rows: {len(new_t)} (keys identical to previous file, same order).",
            f"- Rows changed vs previous file: {rows_changed} (expected 1170).",
            f"- evidence starting 'gateway_sample (2026-10-28)': {day2_sample} (expected 1144).",
            f"- Objects with live-sample evidence (evidence starts 'gateway_sample', any date): {live_sample}.",
            f"- P distribution now: {dict(sorted(p_dist.items()))}; "
            f"delta vs before: { {k: p_dist[k] - p_old[k] for k in sorted(set(p_dist) | set(p_old)) if p_dist[k] != p_old[k]} }.",
            ""]

    # ---------------- 3. remaining_sites refresh ----------------
    rem = load(REMAINING)
    shutil.copy(REMAINING, "output/day2/remaining_before_day2_merge.csv")
    rem_before = len(rem)
    by_key = {}
    for r in rem:
        by_key.setdefault((r["org_name"], r["original_url"]), []).append(r)
    UPD = {
        "dead_404": ("manual_unresolved", "dead_404",
                     "recorded URL answered HTTP 404 in the 2026-10-28 daytime re-run via gateway; "
                     "current announcement page not determined — manual on-site resolution"),
        "forbidden_403": ("manual_confirm", "forbidden_403",
                          "recorded URL answered HTTP 403 in the 2026-10-28 daytime re-run via gateway; "
                          "host stopped per rules — manual confirmation"),
        "robots_disallowed": ("robots_denied", "robots_denied",
                              "robots.txt disallows the recorded path (re-confirmed in the 2026-10-28 "
                              "daytime re-run via gateway); not fetched — manual confirmation queue"),
    }
    removed, updated, noted = [], Counter(), 0
    out_rem = []
    for r in rem:
        rr = matched_master.get((r["org_name"], r["original_url"]))
        if rr is not None:
            res = rr["result"]
            if res in ("recovered_200", "recovered_redirect"):
                removed.append(r)
                continue
            if res in UPD:
                q, code, detail = UPD[res]
                r = dict(r, queue=q, reason_code=code, reason_detail=detail)
                updated[res] += 1
            elif res == "still_error":
                d = rr["detail"].strip()
                note = (f"still in error after the 2026-10-28 daytime re-run via gateway; "
                        f"last note: {d}") if d else (
                        "still in error after the 2026-10-28 daytime re-run via gateway "
                        "(dominated by edge 530/522 origin errors)")
                r = dict(r, reason_detail=note)
                noted += 1
        out_rem.append(r)
    with open(REMAINING, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["org_name", "prefecture", "official_domain",
                                          "original_url", "queue", "reason_code", "reason_detail"])
        w.writeheader()
        w.writerows(out_rem)
    log += ["## 3. remaining_sites.csv refresh",
            f"- Rows: {rem_before} -> {len(out_rem)} (expected 714 -> 669).",
            f"- Removed (recovered): {len(removed)} (expected 45).",
            f"- Reclassified: dead_404 {updated['dead_404']}, forbidden_403 {updated['forbidden_403']}, "
            f"robots_disallowed {updated['robots_disallowed']} (queue -> manual_unresolved / manual_confirm / robots_denied).",
            f"- still_error rows kept with refreshed 2026-10-28 note (per-row gateway HTTP status from the rerun file): {noted} (expected 233).",
            ""]

    if anomalies:
        log += ["## Anomalies"] + [f"- {a}" for a in anomalies] + [""]
    with open("output/day2/merge_log.md", "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    print(f"census {before} -> {after}; master changes {len(changes)}; "
          f"templates changed rows {rows_changed}, day2 samples {day2_sample}; "
          f"remaining {rem_before} -> {len(out_rem)} (removed {len(removed)}, noted {noted})")
    if anomalies:
        print("ANOMALIES:")
        for a in anomalies:
            print(" -", a)


if __name__ == "__main__":
    main()
