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
import base64
import csv
import html
import io
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
  a.plink { color: #2f6fed; text-decoration: none; white-space: nowrap; font-size: 13.5px; }
  .ts { color: #66707e; font-size: 12.5px; font-variant-numeric: tabular-nums; margin-left: 6px; white-space: nowrap; }
  figure { margin: 0 0 20px; background: #fff; border: 1px solid #e2e6ec; border-radius: 10px; padding: 12px; }
  figure img { width: 100%; border: 1px solid #dfe4ea; border-radius: 6px; display: block; }
  figcaption { font-size: 13px; color: #44505f; padding-top: 10px; line-height: 1.7; }
  figcaption .org { font-weight: 700; color: #1c2330; font-size: 14px; }
  .obs { margin: 6px 0 2px; padding-left: 20px; }
  .obs li { font-size: 13.5px; padding: 2px 0; border: none; }
  a.nav.alt { background: #fff; color: #2f6fed; border: 1.5px solid #2f6fed; }
  details { background: #fff; border: 1px solid #e2e6ec; border-radius: 10px; margin-bottom: 10px; }
  details.lvl2 { margin: 8px 10px; background: #fafbfc; }
  summary { cursor: pointer; padding: 11px 14px; font-size: 15px; font-weight: 600; list-style: none; }
  summary::-webkit-details-marker { display: none; }
  summary::before { content: "▸ "; color: #2f6fed; }
  details[open] > summary::before { content: "▾ "; }
  summary .cnt { color: #66707e; font-weight: 400; font-size: 13px; margin-left: 8px; }
  .row { display: flex; align-items: center; gap: 8px; padding: 6px 14px; border-top: 1px solid #f0f2f5; font-size: 13.5px; }
  .row .org { flex: 1; min-width: 0; }
  .row a { color: #2f6fed; text-decoration: none; white-space: nowrap; }
  .row input[type=checkbox] { width: 16px; height: 16px; flex: none; }
  .bd { flex: none; font-size: 11.5px; border-radius: 4px; padding: 1px 6px; }
  .bd.ok { background: #e5f4ea; color: #1d7a3a; }
  .bd.ng { background: #fdeeee; color: #a33; }
  .toolbar { position: sticky; top: 0; z-index: 5; background: #f6f7f9; padding: 10px 0; display: flex; gap: 10px; align-items: center; }
  .toolbar input[type=search] { flex: 1; padding: 8px 12px; font-size: 14px; border: 1px solid #cfd6df; border-radius: 8px; }
  .toolbar button { padding: 8px 12px; font-size: 13px; border: 1px solid #cfd6df; background: #fff; border-radius: 8px; cursor: pointer; }
  .prog { font-size: 13.5px; color: #44505f; white-space: nowrap; }
  .acts { flex: none; display: inline-flex; gap: 6px; }
  .acts[hidden] { display: none; }
  .chip[hidden] { display: none; }
  .bok, .bre, .bnote { font-size: 12.5px; padding: 3px 10px; border-radius: 6px; cursor: pointer; border: 1px solid #cfd6df; background: #fff; }
  .bok { border-color: #1d7a3a; color: #1d7a3a; font-weight: 600; }
  .bre { border-color: #b3541e; color: #b3541e; font-weight: 600; }
  .chip { flex: none; font-size: 12px; border-radius: 4px; padding: 2px 8px; }
  .chip.ok { background: #e5f4ea; color: #1d7a3a; }
  .chip.re { background: #fdf0e2; color: #b3541e; }
  .notebox { flex: none; display: inline-flex; gap: 6px; }
  .notebox[hidden] { display: none; }
  .notein { padding: 4px 8px; font-size: 13px; border: 1px solid #cfd6df; border-radius: 6px; width: 220px; }
  .bbatch { margin-left: 10px; font-size: 12px; padding: 2px 9px; border: 1px solid #1d7a3a; color: #1d7a3a; background: #fff; border-radius: 6px; cursor: pointer; font-weight: 600; }
  details.panel { border-left: 4px solid #b3541e; }
  details.panel.ret { border-left-color: #2f6fed; }
  .qrow { padding: 6px 14px; border-top: 1px solid #f0f2f5; font-size: 13.5px; }
  .qrow .org { font-weight: 600; }
  .qrow a { color: #2f6fed; text-decoration: none; }
  .qfoot { padding: 10px 14px; display: flex; gap: 10px; align-items: center; border-top: 1px solid #f0f2f5; }
  .qfoot button { padding: 6px 12px; font-size: 13px; border: 1px solid #b3541e; color: #b3541e; background: #fff; border-radius: 8px; cursor: pointer; font-weight: 600; }
  .retinfo { flex: none; font-size: 12px; color: #2f6fed; }
  .retinfo a { color: #2f6fed; }
  .sub { color: #66707e; font-size: 12.5px; }
"""


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def bar(share):
    return f'<div class="bar"><span style="width:{share:.1f}%"></span></div>'


def img_data_uri(path):
    """Re-encode a screenshot to <=1000px wide JPEG and return a data URI."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if im.width > 1000:
            im = im.resize((1000, int(im.height * 1000 / im.width)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=62)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        with open(path, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()


def build_tpl_pages(p_counts, n_templates, n_sampled):
    """Per-class explanation pages with representative screenshots.

    Source: output/template_shots/manifest.json (+ its JPEGs), produced by
    the screenshot-capture pass. Returns {p1: html, ..., p7: html}; classes
    without manifest entries get a page without screenshots.
    """
    shots_dir = os.path.join(JP, "output/template_shots")
    manifest = {}
    mpath = os.path.join(shots_dir, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = {c["class"]: c for c in json.load(f).get("classes", [])}
    pages = {}
    for p in ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]:
        name, desc = P_NAMES[p]
        n = p_counts.get(p, 0)
        entry = manifest.get(p, {})
        figs = ""
        for ex in entry.get("examples", []):
            img_path = os.path.join(shots_dir, ex.get("image", ""))
            if not os.path.exists(img_path):
                continue
            obs = "".join(f"<li>{html.escape(o)}</li>" for o in ex.get("observed", []))
            figs += f"""<figure>
  <img src="{img_data_uri(img_path)}" alt="{html.escape(ex.get('org_name', ''))} 公告页截图">
  <figcaption><span class="org">{html.escape(ex.get('org_name', ''))}</span>　{html.escape(ex.get('page_title', ''))}<br>
  <span class="sub">{html.escape(ex.get('list_url', ''))}｜截图 {html.escape(ex.get('captured_at', ''))}</span>
  <ul class="obs">{obs}</ul></figcaption>
</figure>"""
        notes = "".join(f"<li>{html.escape(x)}</li>" for x in entry.get("class_notes", []))
        failed = entry.get("failed_attempts", [])
        fail_note = ""
        if failed:
            names = "、".join(html.escape(str(x.get("org_name", x)) if isinstance(x, dict) else str(x)) for x in failed[:5])
            fail_note = f'<div class="note">本次采集失败留痕：{names}（超时/拒绝/崩溃，见采集记录）。</div>'
        pages[p.lower()] = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="300">
<title>{p} {name}｜nst 模板说明</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/sandal/templates">← 返回模板分类总览</a>
  <h1>{p}　{name}</h1>
  <div class="upd">快照更新于 {TS}（每小时自动更新）</div>

  <div class="cards">
    <div class="card"><div class="n">{n:,}</div><div class="l">该类对象数（占已归类 {pct(n, n_templates)}）</div></div>
    <div class="card"><div class="n">{len(entry.get('examples', []))}</div><div class="l">本页代表截图（逐张实测说明）</div></div>
  </div>

  <h2>这类页面长什么样</h2>
  <ul>
    <li>{desc}</li>
    {notes}
  </ul>

  <h2>代表截图</h2>
  {figs if figs else '<div class="note">暂无代表截图。</div>'}
  {fail_note}

  <div class="note">口径：截图为代表站公告页顶部（1280px 宽视口），说明逐条来自截图实测；分类依据为全库 {n_templates:,} 个对象（实采 {n_sampled:,} 个）。</div>
</div>
</body>
</html>"""
    return pages


OBJ_SCRIPT = """
<script>
(function () {
  var KEY = "nst-obj-check-v2", OLDKEY = "nst-obj-check-v1";
  var st = {};
  try { st = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { st = {}; }
  function persist() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }
  function now() { return new Date().toISOString(); }
  function fmt(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
  var rows = Array.prototype.slice.call(document.querySelectorAll(".row[data-k]"));
  var byKey = {};
  rows.forEach(function (r) { (byKey[r.getAttribute("data-k")] = byKey[r.getAttribute("data-k")] || []).push(r); });
  // Rows returned from re-verification (panel B) supersede a stale re-flag.
  document.querySelectorAll(".row[data-ret]").forEach(function (r) {
    var k = r.getAttribute("data-k");
    if (st[k] && st[k].s === "re") { delete st[k]; }
  });
  // Migrate v1 plain checkmarks to v2 "ok".
  try {
    var old = JSON.parse(localStorage.getItem(OLDKEY) || "{}");
    rows.forEach(function (r) {
      var k1 = r.getAttribute("data-k1"), k = r.getAttribute("data-k");
      if (k1 && old[k1] && !st[k]) st[k] = { s: "ok", at: now(), round: 1 };
    });
  } catch (e) {}
  function renderRow(r) {
    var k = r.getAttribute("data-k"), e = st[k];
    var acts = r.querySelector(".acts"), chip = r.querySelector(".chip"), nb = r.querySelector(".notebox");
    if (nb) nb.hidden = true;
    if (!e) { if (acts) acts.hidden = false; if (chip) { chip.hidden = true; chip.textContent = ""; } return; }
    if (acts) acts.hidden = true;
    if (chip) {
      chip.hidden = false;
      chip.className = "chip " + (e.s === "ok" ? "ok" : "re");
      chip.textContent = e.s === "ok"
        ? "\\u2713 正确 " + fmt(e.at)
        : "\\u21bb 再确认 第" + (e.round || 1) + "轮 " + fmt(e.at) + (e.note ? "（" + e.note + "）" : "");
    }
  }
  function renderAll() { rows.forEach(renderRow); renderQueue(); refreshCounts(); }
  function refreshCounts() {
    var ok = 0, re = 0;
    Object.keys(st).forEach(function (k) { if (st[k].s === "ok") ok++; else if (st[k].s === "re") re++; });
    var t = document.getElementById("chkTotal"); if (t) t.textContent = ok;
    var q = document.getElementById("qaCount"); if (q) q.textContent = re;
    document.querySelectorAll("details[data-sec]").forEach(function (d) {
      var n = 0;
      d.querySelectorAll(".row[data-k]").forEach(function (r) { var e = st[r.getAttribute("data-k")]; if (e && e.s === "ok") n++; });
      var el = d.querySelector(":scope > summary .chkd"); if (el) el.textContent = n;
    });
  }
  function renderQueue() {
    var box = document.getElementById("queueA"); if (!box) return;
    var items = Object.keys(st).filter(function (k) { return st[k].s === "re"; });
    if (!items.length) { box.innerHTML = '<div class="note">暂无。点某行「再确认」后会进入这里。</div>'; return; }
    box.innerHTML = items.map(function (k) {
      var e = st[k], r = (byKey[k] || [])[0];
      var name = r ? r.getAttribute("data-name") : k;
      var url = r ? r.getAttribute("data-target") : "";
      return '<div class="qrow"><span class="org">' + esc(name) + "</span> " +
        (url ? '<a href="' + url + '" target="_blank" rel="noopener">现公告页 \\u2197</a>' : "") +
        ' <span class="bd ng">第' + (e.round || 1) + "轮</span>" +
        (e.note ? ' <span class="sub">备注：' + esc(e.note) + "</span>" : ' <span class="sub">未备注（默认按 skill 方式）</span>') +
        ' <span class="sub">' + fmt(e.at) + "</span></div>";
    }).join("");
  }
  rows.forEach(function (r) {
    var k = r.getAttribute("data-k");
    var bok = r.querySelector(".bok"), bre = r.querySelector(".bre"),
        nb = r.querySelector(".notebox"), bin = r.querySelector(".notein"), bc = r.querySelector(".bnote");
    if (bok) bok.addEventListener("click", function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      if (st[k]) return;
      st[k] = { s: "ok", at: now(), round: parseInt(r.getAttribute("data-round") || "1", 10) };
      persist(); renderAll();
    });
    if (bre) bre.addEventListener("click", function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      if (st[k]) return;
      if (nb) { nb.hidden = !nb.hidden; if (!nb.hidden && bin) bin.focus(); }
    });
    if (bc) bc.addEventListener("click", function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      if (st[k]) return;
      st[k] = { s: "re", at: now(), note: bin ? bin.value.trim() : "", round: parseInt(r.getAttribute("data-round") || "1", 10) };
      persist(); renderAll();
    });
  });
  document.querySelectorAll(".bbatch").forEach(function (b) {
    b.addEventListener("click", function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var d = b.closest("details"); if (!d) return;
      var targets = Array.prototype.slice.call(d.querySelectorAll(".row[data-k]")).filter(function (r) { return !st[r.getAttribute("data-k")]; });
      if (!targets.length) { alert("本节没有未标记的行了。"); return; }
      if (!confirm("把本节 " + targets.length + " 行未标记的全部标为「正确」？标记后不可撤销。")) return;
      var t = now();
      targets.forEach(function (r) { st[r.getAttribute("data-k")] = { s: "ok", at: t, round: parseInt(r.getAttribute("data-round") || "1", 10) }; });
      persist(); renderAll();
    });
  });
  var exp = document.getElementById("expA");
  if (exp) exp.addEventListener("click", function () {
    var lines = ["org_name,prefecture,official_domain,current_target,note,round,flagged_at"];
    Object.keys(st).forEach(function (k) {
      if (st[k].s !== "re") return;
      var r = (byKey[k] || [])[0]; if (!r) return;
      function q(v) { return '"' + String(v || "").replace(/"/g, '""') + '"'; }
      lines.push([q(r.getAttribute("data-name")), q(r.getAttribute("data-pref")), q(r.getAttribute("data-domain")), q(r.getAttribute("data-target")), q(st[k].note || ""), st[k].round || 1, q(st[k].at)].join(","));
    });
    var blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], { type: "text/csv" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "nst-recheck-queue.csv";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
  });
  var q = document.getElementById("flt");
  if (q) q.addEventListener("input", function () {
    var v = q.value.trim();
    rows.forEach(function (r) { r.style.display = (!v || r.textContent.indexOf(v) >= 0) ? "" : "none"; });
    if (v) document.querySelectorAll("details").forEach(function (d) {
      var any = Array.prototype.some.call(d.querySelectorAll(".row"), function (r) { return r.style.display !== "none"; });
      if (any) d.open = true;
    });
  });
  renderAll();
})();
</script>"""

DOKU_GROUPS = {"独立行政法人等", "地方共同法人", "国土交通省系 独立行政法人"}

DEFAULT_RECHECK_METHOD = "站内搜索 → Google site: 搜索 → Google 关键词搜索（排除商业网站）"


def load_returned():
    path = os.path.join(JP, "output/checking/returned.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_objects_page(master, open_items):
    """Complete object list for manual checking, KKJ-style hierarchy with
    accordion sections. Each row: link to the verified announcement page
    (or official homepage when unresolved) + 正确/再确认 buttons.
    Marks are one-way (no un-marking), persist in the reader's browser
    (localStorage v2), flagged rows form the re-check queue (exportable
    CSV), and processed rows come back in a separate 'returned' panel."""
    bucket_of = {}
    for r in open_items:
        bucket_of[(r["org_name"], r["prefecture"])] = r["bucket"]

    def row_html(r, round_no=1, ret_info=None):
        name = html.escape(r["org_name"])
        status = r.get("verification_status") or ""
        target = (r.get("daily_collection_target") or "").strip()
        dom = (r.get("official_domain") or "").strip()
        key = html.escape(f"{r['org_name']}|{r['prefecture']}|{dom}", quote=True)
        key1 = html.escape(f"{r['org_name']}|{dom}|{target}", quote=True)
        if status.startswith("検証済") and target.startswith("http"):
            link = f'<a href="{html.escape(target, quote=True)}" target="_blank" rel="noopener">公告页 ↗</a>'
            badge = '<span class="bd ok">確定</span>'
        else:
            link = (f'<a href="https://{html.escape(dom, quote=True)}/" target="_blank" rel="noopener">官网 ↗</a>'
                    if dom else "")
            reason = REASON_LABELS.get(bucket_of.get((r["org_name"], r["prefecture"]), ""), ("未决", ""))[0]
            badge = f'<span class="bd ng">{html.escape(reason)}</span>'
        ret = ""
        ret_attr = ""
        if ret_info:
            ret_attr = ' data-ret="1"'
            newt = (ret_info.get("new_target") or "").strip()
            res = html.escape(ret_info.get("result") or "")
            note = html.escape(ret_info.get("agent_note") or "")
            changed = ""
            if newt and newt != target:
                changed = f' → <a href="{html.escape(newt, quote=True)}" target="_blank" rel="noopener">新公告页 ↗</a>'
            ret = (f'<span class="retinfo">{html.escape(ret_info.get("processed_at") or "")} 处理：{res}{changed}'
                   f'{"｜" + note if note else ""}</span>')
        return (f'<div class="row" data-k="{key}" data-k1="{key1}" data-round="{round_no}"{ret_attr} '
                f'data-name="{html.escape(r["org_name"], quote=True)}" data-pref="{html.escape(r["prefecture"], quote=True)}" '
                f'data-domain="{html.escape(dom, quote=True)}" data-target="{html.escape(target, quote=True)}">'
                f'<span class="org">{name}</span>{link}{badge}{ret}'
                f'<span class="acts"><button type="button" class="bok">正确</button>'
                f'<button type="button" class="bre">再确认</button></span>'
                f'<span class="chip" hidden></span>'
                f'<span class="notebox" hidden><input class="notein" placeholder="备注核对方向（可空）">'
                f'<button type="button" class="bnote">确认入队</button></span></div>')

    def counts(rows):
        c = sum(1 for r in rows if (r.get("verification_status") or "").startswith("検証済"))
        return (f'<span class="cnt">{len(rows):,} 件・確定 {c:,}・已标正确 <span class="chkd">0</span></span>'
                f'<button type="button" class="bbatch">本节全部正确</button>')

    def section(title, inner, rows):
        return (f'<details data-sec><summary>{html.escape(title)}{counts(rows)}</summary>'
                f'{inner}</details>')

    def flat(rows):
        return "".join(row_html(r) for r in sorted(rows, key=lambda r: r["org_name"]))

    def grouped(rows, keyfn):
        groups = {}
        for r in rows:
            groups.setdefault(keyfn(r), []).append(r)
        out = ""
        for gname in sorted(groups, key=lambda g: -len(groups[g])):
            grows = sorted(groups[gname], key=lambda r: r["org_name"])
            inner = "".join(row_html(r) for r in grows)
            out += (f'<details class="lvl2" data-sec><summary>{html.escape(gname)}{counts(grows)}</summary>'
                    f'{inner}</details>')
        return out

    nat = [r for r in master if (r["level"] or "") == "国・独立行政法人等"]
    doku = [r for r in nat if (r.get("group_ministry") or "") in DOKU_GROUPS]
    ministries = [r for r in nat if (r.get("group_ministry") or "") not in DOKU_GROUPS]
    prefs = [r for r in master if r["level"] == "都道府県"]
    munis = [r for r in master if r["level"] == "市区町村"]
    common = [r for r in master if r["level"] == "国 (共通システム)"]

    total = len(master)
    confirmed = sum(1 for r in master if (r.get("verification_status") or "").startswith("検証済"))

    # Returned (re-verified) items panel: server-rendered from
    # output/checking/returned.csv, maintained by the re-check processing.
    master_by_key = {}
    for r in master:
        master_by_key[(r["org_name"], r["prefecture"], (r.get("official_domain") or "").strip())] = r
    ret_rows = ""
    n_ret = 0
    for rr in load_returned():
        m = master_by_key.get((rr.get("org_name", ""), rr.get("prefecture", ""), (rr.get("official_domain") or "").strip()))
        if not m:
            continue
        n_ret += 1
        ret_rows += row_html(m, round_no=int(rr.get("round") or 1) + 1, ret_info=rr)
    panel_b = (f'<details class="panel ret" data-sec><summary>再核对（已重新核对完返回，集中在这里确认）'
               f'<span class="cnt">{n_ret} 件</span>'
               f'<button type="button" class="bbatch">本批全部正确</button></summary>'
               f'{ret_rows if ret_rows else "<div class=\"note\">暂无返回项。你导出的再确认队列被处理后，结果会出现在这里。</div>"}'
               f'</details>')

    body = ""
    body += (f'<details class="panel" open><summary>再确认队列（待重新核对）'
             f'<span class="cnt"><span id="qaCount">0</span> 件</span></summary>'
             f'<div id="queueA"></div>'
             f'<div class="qfoot"><button type="button" id="expA">导出再确认队列（CSV）</button>'
             f'<span class="sub">导出后把文件发给我即可。未备注的默认按 skill 方式核对：{DEFAULT_RECHECK_METHOD}。</span></div>'
             f'</details>')
    body += panel_b
    body += section("省庁・国の機関", grouped(ministries, lambda r: r.get("group_ministry") or "(未分类)"), ministries)
    body += section("都道府県", flat(prefs), prefs)
    body += section("市区町村（按都道府县展开）", grouped(munis, lambda r: r["prefecture"]), munis)
    body += section("独立行政法人等", flat(doku), doku)
    if common:
        body += section("共通システム", flat(common), common)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>nst 对象清单・公告地址（人工核对）</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/sandal">← 返回任务状态</a>
  <h1>对象清单・公告地址（人工核对）</h1>
  <div class="upd">快照更新于 {TS}（每小时自动更新）｜全 {total:,} 件・已确定公告页 {confirmed:,} 件・未决 {total - confirmed:,} 件</div>
  <div class="toolbar">
    <input type="search" id="flt" placeholder="按机关名筛选（输入即过滤并展开）">
    <span class="prog">已标正确 <b id="chkTotal">0</b> / {total:,}</span>
  </div>
  {body}
  <div class="note">用法：逐行点「公告页」打开核对 → 对了点「正确」，存疑点「再确认」（可备注核对方向，不备注则默认：{DEFAULT_RECHECK_METHOD}）。两种标记点下即记录、不可反选。每层标题旁有「本节全部正确」可批量标记（只作用于未标记行）。标记都存在本浏览器里，每小时数据刷新不清；「再确认」的行汇总在页面顶部队列里，导出 CSV 发我处理，处理完的回到「再核对」区等你集中确认。目录源对象待去重核实后并入本清单。</div>
</div>
{OBJ_SCRIPT}
</body>
</html>"""


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
    def done_li(x):
        if isinstance(x, dict):
            ts = f'<span class="ts">{html.escape(x.get("time", ""))}</span>' if x.get("time") else ""
            return f'<li><span class="done">完成</span>{ts}　{html.escape(x.get("text", ""))}</li>'
        return f'<li><span class="done">完成</span>　{html.escape(str(x))}</li>'

    done_items = "".join(done_li(x) for x in frag.get("done_today", []))
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
  <a class="nav alt" href="/sandal/objects">对象清单・公告地址（人工核对用，手风琴层级）→</a>

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
                   f'<td class="sub">{desc}</td><td><a class="plink" href="/sandal/templates/{p.lower()}">截图说明 →</a></td></tr>')
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

    nat = [r for r in master if (r["level"] or "") == "国・独立行政法人等"]
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
    <tr><th>分类</th><th class="num">对象数</th><th class="num">占比</th><th></th><th>说明</th><th>详情</th></tr>
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
    tpl_pages = build_tpl_pages(p_counts, n_templates, n_sampled)
    with open(os.path.join(REPO, "gateway/src/tpl_pages.js"), "w", encoding="utf-8") as f:
        f.write("// Generated by scripts/gen_status_pages.py — do not edit by hand.\n")
        f.write("export const TPL_PAGES = {\n")
        for key, page_html in tpl_pages.items():
            body = page_html.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
            f.write(f'  "{key}": `{body}`,\n')
        f.write("};\n")
    print(f"tpl pages: {len(tpl_pages)}")
    objects_html = build_objects_page(master, open_items)
    with open(os.path.join(REPO, "gateway/src/objects_page.js"), "w", encoding="utf-8") as f:
        body = objects_html.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        f.write("// Generated by scripts/gen_status_pages.py — do not edit by hand.\n")
        f.write("export const OBJECTS_PAGE_HTML = `" + body + "`;\n")
    print(f"objects page rows: {objects_html.count('class=' + chr(34) + 'row' + chr(34))}")
    print(f"generated: confirmed {confirmed}/{total}, templates {n_templates}, endpoints {n_endpoints}, open {len(open_items)}")


if __name__ == "__main__":
    main()
