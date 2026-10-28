#!/usr/bin/env python3
"""Generate the gateway status pages (overview + multi-dimension detail).

Reads the project data in ~/workspace/jp-procurement and writes:
  gateway/src/status_page.js    — overview snapshot (tasks, queues)
  gateway/src/templates_page.js — template classes x level x affiliation
                                  x region breakdown of the object universe
Narrative fragments (running tasks / today's completions) come from
~/workspace/jp-procurement/output/status_fragments.json when present.
Run by the hourly status cron and by hand after merges. Deterministic:
same inputs produce the same files apart from the timestamp argument.

Usage: gen_status_pages.py [timestamp-string]
"""
import csv
import html
import json
import os
import sys
from collections import Counter

JP = os.path.expanduser("~/workspace/jp-procurement")
REPO = os.path.expanduser("~/workspace/nst-repo")
TS = sys.argv[1] if len(sys.argv) > 1 else ""

P_NAMES = {
    "P1": ("入札情報一覧型", "入札・nyusatsu 系路径的标准公告列表，标题+日期排列"),
    "P2": ("調達情報型", "chotatsu 系路径，国家机关・大学・医院较多"),
    "P3": ("公告・公募掲示板型", "公告板型，公告本文常在 PDF 内"),
    "P4": ("電子調達システム型", "电子调達系统入口 / 检索式公开侧，需系统级适配"),
    "P5": ("契約情報型", "公告与结果同页，解析时需区分公告中与结果公表"),
    "P6": ("機関トップ/汎用ページ型", "登记的是机构トップ或汎用页，真实列表在更深处或新闻流里"),
    "P7": ("PDF直接リンク型", "公告页本身即 PDF 直链"),
}
STRUCT_LABELS = {
    "ul_list": "列表（ul）", "link_list": "链接列表", "pdf_index": "PDF 索引",
    "search_form": "检索表单", "table_list": "表格列表", "calendar": "日历型",
    "dl_list": "定义列表", "insufficient_links": "链接不足（待深挖）",
    "evidence_only": "仅记录字段（未实采）",
}
REASON_LABELS = {
    "conn_error": ("连接错误（源站不可达）", "按指数退避周期复验"),
    "manual_unresolved": ("人工未确定", "待深挖轮（指数退避节奏）"),
    "robots_denied": ("robots 禁止", "截图核验中"),
    "manual_confirm": ("人工确认", "待处理"),
    "unverified": ("未验证", "待处理"),
    "reverify": ("待复核", "待处理"),
}
CSS = """
  body { font-family: -apple-system, "Hiragino Sans", "Noto Sans CJK SC", sans-serif; margin: 0; background: #f6f7f9; color: #1c2330; }
  .wrap { max-width: 920px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 22px; margin: 8px 0 2px; }
  .upd { color: #66707e; font-size: 13px; margin-bottom: 20px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #fff; border: 1px solid #e2e6ec; border-radius: 10px; padding: 14px 16px; }
  .card .n { font-size: 26px; font-weight: 700; }
  .card .l { color: #66707e; font-size: 13px; margin-top: 2px; }
  h2 { font-size: 16px; margin: 26px 0 10px; border-left: 4px solid #2f6fed; padding-left: 8px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e6ec; border-radius: 10px; overflow: hidden; margin-bottom: 8px; }
  th, td { text-align: left; padding: 8px 12px; font-size: 14px; border-bottom: 1px solid #eef1f5; vertical-align: top; }
  th { background: #fafbfc; color: #44505f; font-weight: 600; }
  tr:last-child td { border-bottom: none; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .run { color: #b25e09; font-weight: 600; }
  .done { color: #1d7a3a; font-weight: 600; }
  .wait { color: #66707e; font-weight: 600; }
  ul { background: #fff; border: 1px solid #e2e6ec; border-radius: 10px; padding: 12px 16px 12px 34px; margin: 0; }
  li { font-size: 14px; padding: 4px 0; }
  .note { color: #66707e; font-size: 12.5px; margin-top: 18px; line-height: 1.6; }
  .bar { background: #edf0f4; border-radius: 4px; height: 8px; min-width: 90px; }
  .bar span { display: block; background: #2f6fed; height: 8px; border-radius: 4px; }
  a.nav { display: block; background: #2f6fed; color: #fff; text-decoration: none; border-radius: 10px; padding: 14px 16px; font-size: 15px; font-weight: 600; margin: 4px 0 8px; }
  a.back { color: #2f6fed; text-decoration: none; font-size: 13.5px; }
  .sub { color: #66707e; font-size: 12.5px; }
"""


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def bar(share):
    return f'<div class="bar"><span style="width:{share:.1f}%"></span></div>'


def main():
    master = read_csv(os.path.join(JP, "data/master_source_list.csv"))
    open_items = read_csv(os.path.join(JP, "data/open_items.csv"))
    endpoints = read_csv(os.path.join(JP, "data/endpoints.csv"))
    templates = read_csv(os.path.join(JP, "data/site_templates.csv"))
    cand_path = os.path.join(REPO, "data/candidates_joint_bodies.csv")
    n_candidates = len(read_csv(cand_path)) if os.path.exists(cand_path) else 0

    total = len(master)
    confirmed = sum(1 for r in master if (r.get("verification_status") or "").startswith("検証済"))
    n_endpoints = len(endpoints)
    n_templates = len(templates)
    n_sampled = sum(1 for r in templates if "gateway_sample" in (r.get("evidence") or ""))

    frag = {"running": [], "done_today": []}
    frag_path = os.path.join(JP, "output/status_fragments.json")
    if os.path.exists(frag_path):
        with open(frag_path, encoding="utf-8") as f:
            frag = json.load(f)

    # ---------- overview page ----------
    running_rows = "".join(
        f'<tr><td>{html.escape(t["name"])}</td><td class="run">{html.escape(t.get("status", "进行中"))}</td>'
        f'<td>{html.escape(t.get("note", ""))}</td></tr>' for t in frag.get("running", []))
    if not running_rows:
        running_rows = '<tr><td colspan="3">当前没有进行中的批次</td></tr>'
    reason_counts = Counter(r["bucket"] for r in open_items)
    queue_rows = ""
    for reason, n in reason_counts.most_common():
        label, action = REASON_LABELS.get(reason, (reason, ""))
        cls = "run" if reason == "robots_denied" and any("robots" in t["name"] for t in frag.get("running", [])) else "wait"
        queue_rows += (f'<tr><td>{label}</td><td class="num">{n}</td>'
                       f'<td class="{cls}">{action}</td></tr>')
    if n_candidates:
        queue_rows += (f'<tr><td>候选册：组合类团体</td><td class="num">{n_candidates:,}</td>'
                       f'<td class="wait">挂账，逐个核实是否为发注机关</td></tr>')
    done_items = "".join(
        f'<li><span class="done">完成</span>　{html.escape(x)}</li>' for x in frag.get("done_today", []))
    date_label = frag.get("date", "")
    overview = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="120">
<title>nst 任务状态</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>nst 任务状态</h1>
  <div class="upd">快照更新于 {TS}（每小时自动更新；本页每 2 分钟自动刷新）</div>

  <div class="cards">
    <div class="card"><div class="n">{confirmed:,} / {total:,}</div><div class="l">主清单现行公告页已确定（{pct(confirmed, total)}）</div></div>
    <div class="card"><div class="n">{n_endpoints:,}</div><div class="l">候选轮询端点</div></div>
    <div class="card"><div class="n">{n_templates:,}</div><div class="l">模板已归类对象（实采覆盖 {pct(n_sampled, n_templates)}）</div></div>
    <div class="card"><div class="n">{len(open_items):,}</div><div class="l">未决行（全部带原因码在册）</div></div>
  </div>

  <a class="nav" href="/sandal/templates">模板分类与对象全景（分类 × 分层 × 机构归属 × 地区）→</a>

  <h2>正在运行</h2>
  <table>
    <tr><th>任务</th><th>状态</th><th>说明</th></tr>
    {running_rows}
  </table>

  <h2>未决队列（{len(open_items):,} 行）</h2>
  <table>
    <tr><th>类别</th><th class="num">行数</th><th>处置</th></tr>
    {queue_rows}
  </table>

  <h2>今日完成{f"（{date_label}）" if date_label else ""}</h2>
  <ul>
    {done_items}
  </ul>

  <div class="note">说明：本页数字由主清单数据文件每小时重算；许可等待（approval_pending）当前为 0。目录源件数以 2026-10-28 实测 9,669 件为准。</div>
</div>
</body>
</html>"""

    # ---------- templates / universe page ----------
    p_counts = Counter(r["p_class"] for r in templates)
    p_rows = ""
    for p in ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]:
        n = p_counts.get(p, 0)
        name, desc = P_NAMES[p]
        p_rows += (f'<tr><td><b>{p}</b> {name}</td><td class="num">{n:,}</td>'
                   f'<td class="num">{pct(n, n_templates)}</td><td>{bar(n / n_templates * 100 if n_templates else 0)}</td>'
                   f'<td class="sub">{desc}</td></tr>')
    struct_counts = Counter(r["list_structure"] for r in templates)
    struct_rows = "".join(
        f'<tr><td>{STRUCT_LABELS.get(k, k)}</td><td class="num">{n:,}</td><td class="num">{pct(n, n_templates)}</td></tr>'
        for k, n in struct_counts.most_common())

    level_counts = Counter(r["level"] for r in master)
    level_conf = Counter(r["level"] for r in master if (r.get("verification_status") or "").startswith("検証済"))
    level_rows = ""
    for lv, n in level_counts.most_common():
        c = level_conf.get(lv, 0)
        level_rows += (f'<tr><td>{html.escape(lv)}</td><td class="num">{n:,}</td>'
                       f'<td class="num">{c:,}</td><td class="num">{pct(c, n)}</td><td>{bar(c / n * 100 if n else 0)}</td></tr>')

    nat = [r for r in master if (r["level"] or "").startswith("国")]
    gm_counts = Counter((r["group_ministry"] or "(未分类)") for r in nat)
    gm_rows = "".join(
        f'<tr><td>{html.escape(k)}</td><td class="num">{n:,}</td><td class="num">{pct(n, len(nat))}</td></tr>'
        for k, n in gm_counts.most_common(14))

    pref_counts = Counter(r["prefecture"] for r in master)
    pref_conf = Counter(r["prefecture"] for r in master if (r.get("verification_status") or "").startswith("検証済"))
    pref_rows = ""
    for pref, n in sorted(pref_counts.items(), key=lambda kv: -kv[1]):
        c = pref_conf.get(pref, 0)
        pref_rows += (f'<tr><td>{html.escape(pref)}</td><td class="num">{n:,}</td>'
                      f'<td class="num">{c:,}</td><td class="num">{pct(c, n)}</td><td>{bar(c / n * 100 if n else 0)}</td></tr>')

    n_kkj = sum(1 for r in master if (r.get("in_kkj") or "") == "yes")
    n_dir2top = sum(1 for r in master if (r.get("in_dir2_top") or "") == "yes")
    dir2_final = os.path.exists(os.path.join(JP, "output/dir2_screenshot_full/FINAL.md"))
    dir2_state = "已落表" if dir2_final else "截图枚举进行中"

    detail = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="300">
<title>nst 模板分类与对象全景</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/sandal">← 返回任务状态</a>
  <h1>模板分类与对象全景</h1>
  <div class="upd">快照更新于 {TS}（每小时自动更新）</div>

  <div class="cards">
    <div class="card"><div class="n">{total:,}</div><div class="l">主清单对象（已确定 {confirmed:,}，{pct(confirmed, total)}）</div></div>
    <div class="card"><div class="n">9,669</div><div class="l">目录源对象（{dir2_state}，与主清单重合待去重）</div></div>
    <div class="card"><div class="n">{n_candidates:,}</div><div class="l">组合类候选（官方名录差集，挂账核实中）</div></div>
    <div class="card"><div class="n">{n_endpoints:,}</div><div class="l">候选轮询端点（多对象共用一页只算一个端点）</div></div>
  </div>

  <h2>按模板分类（P1–P7，已归类 {n_templates:,} 个对象）</h2>
  <table>
    <tr><th>分类</th><th class="num">对象数</th><th class="num">占比</th><th></th><th>说明</th></tr>
    {p_rows}
  </table>
  <div class="sub">分类依据：实采样本 {n_sampled:,} 个（{pct(n_sampled, n_templates)}），其余按登记字段归类。P6 占大头，意味着多数小机关把公告放在新闻流或栏目深处，采集器需要按关键词过滤。</div>

  <h2>列表页结构（实采与记录合计）</h2>
  <table>
    <tr><th>结构</th><th class="num">对象数</th><th class="num">占比</th></tr>
    {struct_rows}
  </table>

  <h2>按分层（对象层级）</h2>
  <table>
    <tr><th>层级</th><th class="num">对象数</th><th class="num">已确定</th><th class="num">确定率</th><th></th></tr>
    {level_rows}
  </table>
  <div class="sub">轮询层：{n_endpoints:,} 个端点目前全部登记为 B 层（默认），轮询引擎按指数退避自动调整单端点间隔（静默越久间隔越长，上限封顶）。来源层：主清单中出自官方门户目录的 {n_kkj:,} 个、同时被目录源收录的 {n_dir2top:,} 个。</div>

  <h2>按机构归属（国家层按省庁・機関）</h2>
  <table>
    <tr><th>省庁・機関</th><th class="num">对象数</th><th class="num">占国家层</th></tr>
    {gm_rows}
  </table>
  <div class="sub">国家层共 {len(nat):,} 个对象；地方层见上「按分层」表（市区町村 {level_counts.get("市区町村", 0):,}、都道府县 {level_counts.get("都道府県", 0):,}）。</div>

  <h2>按地区（都道府县，登记值）</h2>
  <table>
    <tr><th>都道府县</th><th class="num">对象数</th><th class="num">已确定</th><th class="num">确定率</th><th></th></tr>
    {pref_rows}
  </table>

  <div class="note">口径：主清单 = 已立行的发注机关；目录源 9,669 件为一次性截图枚举的对象名录，与主清单按「名称+都道府县」去重后并入；组合类候选来自总务省官方名录差集，逐个核实是否为发注机关后再立行。</div>
</div>
</body>
</html>"""

    def js_module(html_text, extra_export=""):
        body = html_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        return ("// Generated by scripts/gen_status_pages.py — do not edit by hand.\n"
                + extra_export + "export const PAGE_HTML = `" + body + "`;\n")

    with open(os.path.join(REPO, "gateway/src/status_page.js"), "w", encoding="utf-8") as f:
        f.write("// Generated by scripts/gen_status_pages.py — do not edit by hand.\n"
                f"export const STATUS_UPDATED = \"{TS}\";\n"
                "export const STATUS_PAGE_HTML = `" + overview.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${") + "`;\n")
    with open(os.path.join(REPO, "gateway/src/templates_page.js"), "w", encoding="utf-8") as f:
        f.write(js_module(detail).replace("PAGE_HTML", "TEMPLATES_PAGE_HTML"))
    print(f"generated: confirmed {confirmed}/{total}, templates {n_templates}, endpoints {n_endpoints}, open {len(open_items)}")


if __name__ == "__main__":
    main()
