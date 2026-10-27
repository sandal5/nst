"""Merge the 2026-10-27 night gateway-run results into data/master_source_list.csv.

Sources (applied in this order; a 'resolved' verdict from any source wins and
is never downgraded by another source; among resolved verdicts the later
source supplies the target URL):
  1. output/slowpass/conn_error_pass3.csv   key (org_name, source_url)
  2. output/slowpass/browser_pass.csv       key (org_name, source_url)
  3. output/approval_retry/results.csv      key (org_name, original_url)
  4. output/phaseb/robots_check_result.csv  key (org_name, source_url)
  5. output/phaseb/manual_confirm_check.csv key (org_name, source_url)
  6. output/phaseb/res_*.csv                key (org_name, original_url)

Status-string conventions follow rounds 1-2 exactly (see master history):
  crawl-resolved tonight  -> 検証済 現行ページ回填 (2026-10-27 网关轮), target = resolved_url
  conn-error recovered 200 -> 検証済 200 (復核 2026-10-27)
  conn-error recovered redirect -> 検証済 跳转 (復核 2026-10-27), target = final URL
  confirmed dead 404 / backfill-unresolved -> 404 現行ページ未確定 (要手動)
  connection error        -> 接続エラー 未確認 (再検証队列)
  robots denied           -> robots 禁止 (手動確認対象)
  forbidden 403           -> 要手動確認 (403)
The 7 allowlist-blocked unverified rows are deliberately NOT touched
(their gateway_host_not_in_allowlist residual reason stays accurate until
the regenerated allowlist is deployed).

Writes data/master_source_list.csv in place (backup: output/master_before_gateway_merge.csv),
output/merge_gateway_changes.csv (one row per master row changed) and
output/merge_gateway_log.md (per-source tallies + anomalies).
"""
import csv, os, shutil
from collections import Counter, defaultdict

RES_BACKFILL = "検証済 現行ページ回填 (2026-10-27 网关轮)"
RES_200 = "検証済 200 (復核 2026-10-27)"
RES_REDIR = "検証済 跳转 (復核 2026-10-27)"
DEAD_MANUAL = "404 現行ページ未確定 (要手動)"
CONN_ERR = "接続エラー 未確認 (再検証队列)"
ROBOTS = "robots 禁止 (手動確認対象)"
MANUAL_403 = "要手動確認 (403)"

MASTER = "data/master_source_list.csv"


def load(p):
    if not os.path.exists(p):
        return []
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def is_http(u):
    return bool(u) and u.startswith("http")


# ---------- verdict extractors: row -> (kind, new_status, target) ----------
# kind in {"resolved", "status", None}; None = leave master row unchanged.

def v_conn_error(r):
    s = r["result_status"]
    if s == "recovered_200":
        return ("resolved", RES_200, r["resolved_url"] if is_http(r["resolved_url"]) else r["source_url"])
    if s == "recovered_redirect":
        return ("resolved", RES_REDIR, r["resolved_url"])
    if s == "dead_404":
        return ("status", DEAD_MANUAL, None)
    if s == "robots_disallowed":
        return ("status", ROBOTS, None)
    if s == "forbidden_403":
        return ("status", MANUAL_403, None)
    return (None, None, None)  # still_error


def v_browser(r):
    if r["result_status"] == "resolved":
        return ("resolved", RES_BACKFILL, r["resolved_url"])
    return (None, None, None)  # still_manual


def v_approval(r):
    s = r["status"]
    if s == "resolved":
        return ("resolved", RES_BACKFILL, r["resolved_url"])
    if s == "unresolved_manual":
        return ("status", DEAD_MANUAL, None)
    if s == "still_error":
        return ("status", CONN_ERR, None)
    if s == "robots_blocked":
        return ("status", ROBOTS, None)
    return (None, None, None)


def v_robots_check_result(r):
    if r["verdict"] == "robots_denied":
        return ("status", ROBOTS, None)
    return (None, None, None)  # 'crawl' rows are covered by res_robots_check


def v_manual_confirm_check(r):
    s = r["result_status"]
    if s == "robots_disallowed":
        return ("status", ROBOTS, None)
    if s == "dead_404":
        return ("status", DEAD_MANUAL, None)
    if s == "still_error":
        return ("status", CONN_ERR, None)
    return (None, None, None)  # recovered_redirect covered by res_manual_confirm


def v_phaseb_res(queue_file):
    def f(r):
        s = r["status"]
        if s == "resolved":
            return ("resolved", RES_BACKFILL, r["resolved_url"])
        if s == "robots_blocked":
            return ("status", ROBOTS, None)
        if s == "still_error":
            if queue_file == "res_verify.csv":
                return (None, None, None)  # allowlist-blocked unverified: untouched by design
            return ("status", CONN_ERR, None)
        if s == "unresolved_manual":
            if queue_file == "res_robots_check.csv":
                return ("status", DEAD_MANUAL, None)  # robots allowed, no listing found
            return (None, None, None)
        return (None, None, None)
    return f


SOURCES = [
    ("conn_error_pass3", "output/slowpass/conn_error_pass3.csv", "source_url", v_conn_error),
    ("browser_pass", "output/slowpass/browser_pass.csv", "source_url", v_browser),
    ("approval_retry", "output/approval_retry/results.csv", "original_url", v_approval),
    ("phaseb_robots_check_result", "output/phaseb/robots_check_result.csv", "source_url", v_robots_check_result),
    ("phaseb_manual_confirm_check", "output/phaseb/manual_confirm_check.csv", "source_url", v_manual_confirm_check),
    ("phaseb_res_manual_unresolved", "output/phaseb/res_manual_unresolved.csv", "original_url", v_phaseb_res("res_manual_unresolved.csv")),
    ("phaseb_res_robots_check", "output/phaseb/res_robots_check.csv", "original_url", v_phaseb_res("res_robots_check.csv")),
    ("phaseb_res_manual_confirm", "output/phaseb/res_manual_confirm.csv", "original_url", v_phaseb_res("res_manual_confirm.csv")),
    ("phaseb_res_verify", "output/phaseb/res_verify.csv", "original_url", v_phaseb_res("res_verify.csv")),
]


def census(rows):
    return sum(1 for r in rows if r["verification_status"].startswith("検証済"))


def main():
    master = load(MASTER)
    shutil.copy(MASTER, "output/master_before_gateway_merge.csv")
    before = census(master)
    idx = defaultdict(list)
    for i, r in enumerate(master):
        idx[(r["org_name"], r["source_url_kkj"])].append(i)

    resolved_rows = set()       # master indices resolved during this merge
    changes = []                # traceability rows
    per_source = {}
    anomalies = []

    for name, path, urlcol, verdict in SOURCES:
        rows = load(path)
        tally = Counter()
        unmatched = []
        for r in rows:
            key = (r["org_name"], r[urlcol])
            hits = idx.get(key, [])
            if not hits:
                unmatched.append(key)
                tally["unmatched"] += 1
                continue
            kind, new_status, target = verdict(r)
            if kind is None:
                tally["no_change"] += 1
                continue
            for i in hits:
                m = master[i]
                if m["verification_status"].startswith("検証済") and i not in resolved_rows:
                    anomalies.append(f"{name}: {key} already resolved before tonight "
                                     f"({m['verification_status']}); verdict {new_status} skipped")
                    tally["already_resolved_guard"] += 1
                    continue
                if kind == "status" and i in resolved_rows:
                    tally["downgrade_skipped"] += 1
                    continue
                old_status, old_target = m["verification_status"], m["daily_collection_target"]
                m["verification_status"] = new_status
                if kind == "resolved":
                    if is_http(target):
                        m["daily_collection_target"] = target
                    else:
                        anomalies.append(f"{name}: {key} resolved without http target ({target!r})")
                    resolved_rows.add(i)
                if old_status != new_status or (kind == "resolved" and old_target != m["daily_collection_target"]):
                    changes.append({
                        "source": name, "org_name": m["org_name"],
                        "official_domain": m["official_domain"],
                        "source_url_kkj": m["source_url_kkj"],
                        "old_status": old_status, "new_status": new_status,
                        "old_target": old_target,
                        "new_target": m["daily_collection_target"],
                    })
                    tally["changed"] += 1
                else:
                    tally["same_status"] += 1
        per_source[name] = (len(rows), tally, unmatched)

    fn = list(master[0].keys())
    with open(MASTER, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        w.writerows(master)
    with open("output/merge_gateway_changes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["source", "org_name", "official_domain",
                                          "source_url_kkj", "old_status", "new_status",
                                          "old_target", "new_target"])
        w.writeheader()
        w.writerows(changes)

    after = census(master)
    lines = ["# Gateway-run merge log (2026-10-27 night)", "",
             f"Master rows: {len(master)}; census (rows with a confirmed current page, "
             f"verification_status 検証済*): before {before} -> after {after} "
             f"(net new resolved {after - before}).",
             f"Master rows changed in total: {len(changes)} (detail: output/merge_gateway_changes.csv).",
             ""]
    for name, (n, tally, unmatched) in per_source.items():
        lines.append(f"## {name} ({n} result rows)")
        lines.append(f"- tallies: {dict(tally)}")
        if unmatched:
            lines.append(f"- unmatched keys ({len(unmatched)}):")
            for k in unmatched:
                lines.append(f"  - {k[0]} | {k[1]}")
        lines.append("")
    if anomalies:
        lines.append("## Anomalies / guards")
        lines += [f"- {a}" for a in anomalies]
        lines.append("")
    lines.append("## Status strings applied tonight")
    lines.append(f"- crawl-resolved (approval retry / browser pass / Phase B): `{RES_BACKFILL}` + daily_collection_target = resolved list-page URL")
    lines.append(f"- conn-error recovered 200 / redirect: `{RES_200}` / `{RES_REDIR}` + target = final URL")
    lines.append(f"- confirmed dead 404 & backfill-unresolved: `{DEAD_MANUAL}`")
    lines.append(f"- connection error: `{CONN_ERR}`; robots denied: `{ROBOTS}`; forbidden 403: `{MANUAL_403}`")
    lines.append("- The 7 allowlist-blocked unverified rows were left untouched by design "
                 "(residual reason gateway_host_not_in_allowlist; they unblock once the "
                 "regenerated allowlist — 2,645 hosts — is deployed).")
    with open("output/merge_gateway_log.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"census: {before} -> {after} (net +{after - before}); rows changed {len(changes)}")
    for name, (n, tally, unmatched) in per_source.items():
        print(f"{name}: {n} rows {dict(tally)} unmatched={len(unmatched)}")
    if anomalies:
        print("ANOMALIES:")
        for a in anomalies:
            print(" -", a)


if __name__ == "__main__":
    main()
