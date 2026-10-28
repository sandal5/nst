# 06 — Web テンプレート総覧（2026-10-27 网关轮 Phase D；2026-10-28 重采样更新）

> 出典・前提：本ファイルは 2026-10-27 夜間网关轮（gateway run）Phase D の成果物 `output/web_template_summary.md` を docs/ に移したもの。配套データは `data/site_templates.csv`（3,054 行）。対象集合は「現行公告ページ確定済み」のオブジェクトで、(機関名, 一覧ページ URL) で重複を除いた 3,054 件を基準とする——主清单の行レベル census は現在 3,308 件で、差 254 件 = 全て同一機関・同一一覧ページを共有する兄弟行（集合凍結後に新規確定した 49 鍵——2026-10-28 復活 45 + 補充 4——は同日中に分類完了し本集合に編入済み）。対账の詳細は merge ログ（output/day2/merge_log.md）参照。カバレッジ上の注意：当夜サンプル失敗の 1,170 件は 2026-10-28 白天に再サンプル済み——1,144 件が実サンプル分類に更新され、26 件が再び失敗し記録フィールド分類を日付注記付きで維持している。

# Web template summary — resolved source set (2026-10-27 night run)

Companion data file: `data/site_templates.csv` (3,054 rows, one per resolved
object). Classes follow `docs/02_classification.md` (P1–P7).

## Resolved set and coverage

The classified set is every registry object with a confirmed current
announcement page: 3,054 objects (2,804 already confirmed in the registry,
201 confirmed during this night's runs: approval queue 50 new keys, browser
pass 41, connection-error recovery 36, Phase B crawls 71, verification
crawls 3).

Template features were determined per unique list URL (2,630 URLs), in this
order: (1) one gateway sample fetch of the list page (robots.txt first,
≥1.5 s per target host), (2) tonight's crawl records for hosts already
sampled tonight, (3) recorded fields only (URLs, titles, recorded detail
links).

| classification basis | objects | share |
|---|---|---|
| gateway sample (live page parsed) | 2,465 | 80.7% |
| tonight's crawl evidence | 300 | 10.0% |
| sample fetch failed (incl. 2026-10-28 re-sample) → recorded fields | 26 | 0.9% |
| recorded fields only (not sampled) | 240 | 8.0% |
| sample skipped (robots) | 22 | 0.7% |

**2026-10-28 update:** the 1,170 "sample failed" objects were re-sampled in daytime through the gateway (1,016 unique list URLs; `scripts/gw_day2_resample.py`). 990 URLs sampled successfully, 14 were skipped (robots), 12 failed again; 1,144 objects moved to live-sample classification and only 26 remain on recorded-fields classification, with a dated failure note in the evidence column. Live-sample coverage is now 2,417 of 3,005 objects (80.4%).

**2026-10-28 append:** the 49 keys confirmed after the set froze (45 connection-error recoveries + 4 supplementary) were classified the same day via gateway sampling (`output/day2/templates49.csv`): 48 live-sampled (P1 12, P2 1, P4 3, P5 7, P6 24, P7 1), and 1 （富山市） fell back to recorded fields (P1, evidence_only) because its redirect-target host was missing from the gateway allowlist; the allowlist has since been regenerated to include recorded redirect-target hosts and that row will be re-sampled after the gateway redeploys. Totals now: 3,054 objects, live-sample coverage 2,465 (80.7%). Detail-pattern fields are empty for rows whose registry record carries no detail URL and whose sample showed no qualifying detail link — they were left blank rather than guessed.

## P-class distribution (docs/02)

| class | objects | structural signature | collector implication |
|---|---|---|---|
| P1 入札情報一覧型 | 792 | nyusatsu/bid/procurement paths; mostly ul/link lists of title+date with HTML or PDF details | standard list parser; date-ordered incremental fetch |
| P2 調達情報型 | 207 | chotatsu paths on national agencies, customs, universities, hospitals | same parser as P1; filter shared ministry pages by issuer name |
| P3 公告・公募掲示板型 | 30 | koukoku/koubo bulletin pages; notice text often inside PDFs | list parser + PDF text extraction (notice no., title, deadline) |
| P4 電子調達システム型 | 112 | dedicated e-procurement entries and non-root search-form driven public systems | use only the public search/disclosure side; per-system adapter; never the login/IC-card area |
| P5 契約情報型 | 282 | keiyaku paths; announcements and award results mixed on one list | parser must separate 公告中 from 結果公表 entries |
| P6 機関トップ／汎用ページ型 | 1,630 | recorded target is a top/generic/CMS category page; the real listing sits deeper or inside the news flow | needs the nav-located listing (tonight's crawls supplied it for 74 objects) or news-list + keyword （入札・公募・プロポーザル) filtering at low frequency |
| P7 PDF直接リンク型 | 1 | one PDF-direct target (2026-10-28 append) | PDF link monitoring |

Representative sampled sites — P1: 北海道農政事務所 (ul_list, static_html
details), 国立大学法人北海道大学 (search form on the list page, PDF details).
P2: 函館税関 (ul_list), 北海道厚生局 (ul_list). P3: （独）日本高速道路保有・
債務返済機構 (table_list, numbered pagination, mixed HTML/PDF details).
P4: 北海道地方環境事務所 /procure/ (search_form system page). P5: 北海道開発局
各開発建設部 (keiyaku pages, link/ul lists). P6: 人事院北海道事務局 (top page
with a 調達 link in the news flow).

## List-page structures (sampled objects only, n=2,465)

| structure | objects | note |
|---|---|---|
| ul_list | 1,219 | dominant pattern: CMS news-style lists; parse li > a + date text |
| link_list | 489 | loose anchor collections without list markup |
| search_form | 217 | few/no static notice links; results require the public search UI (P4-style handling when the path is a system page) |
| pdf_index | 226 | ≥60% of notice links are PDFs; the list is effectively a PDF index — fetch list HTML, then PDFs |
| table_list | 80 | classic table layouts (hospitals, older ministry pages) |
| calendar | 53 | month-navigated posting calendars （整備局 offices) |
| dl_list | 13 | definition-list layouts |
| insufficient_links | 120 | page fetched but <1 qualifying notice link (stale or hub pages) — re-check before collector build |

## Detail pages

| detail_pattern | objects | | detail_format | objects |
|---|---|---|---|---|
| static_html | 1,624 | | html | 1,884 |
| numeric_id | 435 | | pdf | 203 |
| pdf_direct | 214 | | mixed | 215 |
| query_id | 24 | | (unknown, evidence-only) | 703 |
| date_path | 5 | | | |
| (unknown, evidence-only) | 703 | | | |

Pagination: `next_or_numbered` detected on 253 sampled lists;
`none_detected` on 2,164 (single-page lists or pagination below the fold of
the sampled HTML); `unknown` for the 588 rows without a live sample. Collectors
should treat none_detected as "verify on first deep crawl", not as proof of
a single page.

## Subclass signals worth carrying into collector design

- **P4-public-search**: search_form structures on system paths (217 sampled)
  split into true e-procurement public search (build a query adapter) and
  ordinary site search pasted on a list page (parse the static list
  underneath). The CSV keeps them separable via p_class + list_structure.
- **P6-news-flow**: the largest single group (P6, 1,630). For small
  municipalities the announcement "list" is the site's news list; the
  working collector is an RSS/news watcher with keyword filtering, at the
  lowest polling tier — tonight's crawl evidence confirms most of these
  sites publish bid notices only a few times a year.
- **Shared-host pooling**: hundreds of objects share ministry/prefecture
  hosts; per-endpoint polling (docs/05) collapses them — the template of
  the shared listing, not the object, is the unit that matters.
