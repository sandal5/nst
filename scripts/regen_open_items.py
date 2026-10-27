"""Regenerate data/open_items.csv from the current master state.

One row per master row without a confirmed current page
(verification_status not 検証済*), bucket + next_action mapped from the
status string with the same wording used when the file was first built.
Preserves master row order.
"""
import csv
from collections import Counter

MAP = {
    "接続エラー 未確認 (再検証队列)": ("conn_error", "纳入端点重新验证队列，慢速长间隔重试"),
    "404 現行ページ未確定 (要手動)": ("manual_unresolved", "人工沿官网栏目/站内搜索找公告页"),
    "robots 禁止 (手動確認対象)": ("robots_denied", "人工确认对象，不自动采集"),
    "要手動確認 (403)": ("manual_confirm", "人工确认（403/跳转先 robots 等）"),
    "要手動確認 (401)": ("manual_confirm", "人工确认（403/跳转先 robots 等）"),
    "要手動確認 (跳转先 robots 禁止)": ("manual_confirm", "人工确认（403/跳转先 robots 等）"),
    "404 (robots 要確認・手動対象)": ("robots_check", "人工打开 robots.txt 与目标页确认是否真禁止"),
    "404 (许可待ち・再試行队列)": ("approval_pending", "等用户在线时整表批准后新批次重试"),
    "未検証": ("unverified", "待首次验证"),
    "公式公告文中で多数参照されることを確認 (検索結果経由)": ("unverified", "待首次验证"),
    "要再検証 (跳转复核失敗)": ("reverify", "择日再验证"),
}

rows = list(csv.DictReader(open("data/master_source_list.csv", encoding="utf-8-sig")))
out = []
for r in rows:
    st = r["verification_status"]
    if st.startswith("検証済"):
        continue
    bucket, action = MAP[st]
    out.append({
        "org_name": r["org_name"], "prefecture": r["prefecture"], "level": r["level"],
        "official_domain": r["official_domain"], "source_url": r["source_url_kkj"],
        "bucket": bucket, "status": st, "next_action": action,
    })
with open("data/open_items.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["org_name", "prefecture", "level", "official_domain",
                                      "source_url", "bucket", "status", "next_action"])
    w.writeheader()
    w.writerows(out)
print("open_items rows:", len(out), dict(Counter(r["bucket"] for r in out)))
