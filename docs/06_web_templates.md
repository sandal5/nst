# 06 — Web テンプレート総覧（2026-10-27 网关轮 Phase D）

> 出典・前提：本ファイルは 2026-10-27 夜間网关轮（gateway run）Phase D の成果物 `output/web_template_summary.md` を docs/ に移したもの。配套データは `data/site_templates.csv`（3,005 行）。対象集合は「現行公告ページ確定済み」のオブジェクトで、(機関名, 一覧ページ URL) で重複を除いた 3,005 件を基準とする——主清单の行レベル census 3,259 件との差 254 件は、同一機関・同一一覧ページを共有する兄弟行であり、対账の詳細は merge ログ（output/merge_gateway_log.md）参照。カバレッジ上の注意：うち 1,170 件は当夜の网关遅延劣化でサンプル取得に失敗し記録フィールドのみで分類されている（evidence 列で識別可能）ため、優先的に再サンプルすること。

# Web template summary — resolved source set (2026-10-27 night run)

Companion data file: `data/site_templates.csv` (3,005 rows, one per resolved
object). Classes follow `docs/02_classification.md` (P1–P7).

## Resolved set and coverage

The classified set is every registry object with a confirmed current
announcement page: 3,005 objects (2,804 already confirmed in the registry,
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
| gateway sample (live page parsed) | 1,273 | 42.4% |
| tonight's crawl evidence | 300 | 10.0% |
| sample fetch failed tonight → recorded fields | 1,170 | 38.9% |
| recorded fields only (not sampled) | 240 | 8.0% |
| sample skipped (robots) | 22 | 0.7% |

**Caveat that matters:** the gateway path degraded sharply during the
evening (a robots.txt fetch that took ~3 s in the afternoon took 20–35 s at
night), and 1,016 of 2,460 attempted sample fetches failed (timeouts and
edge errors). The 1,170 "sample failed" objects are classified from URLs
and recorded evidence only; their `list_structure` is `evidence_only` and
`pagination` is `unknown`. They are exactly identifiable in
`site_templates.csv` via the evidence column and should be re-sampled
first when the path is healthy. Detail-pattern fields are empty for
evidence-only rows whose registry record carries no detail URL — they were
left blank rather than guessed.

## P-class distribution (docs/02)

| class | objects | structural signature | collector implication |
|---|---|---|---|
| P1 入札情報一覧型 | 779 | nyusatsu/bid/procurement paths; mostly ul/link lists of title+date with HTML or PDF details | standard list parser; date-ordered incremental fetch |
| P2 調達情報型 | 206 | chotatsu paths on national agencies, customs, universities, hospitals | same parser as P1; filter shared ministry pages by issuer name |
| P3 公告・公募掲示板型 | 30 | koukoku/koubo bulletin pages; notice text often inside PDFs | list parser + PDF text extraction (notice no., title, deadline) |
| P4 電子調達システム型 | 57 | dedicated e-procurement entries and non-root search-form driven public systems | use only the public search/disclosure side; per-system adapter; never the login/IC-card area |
| P5 契約情報型 | 275 | keiyaku paths; announcements and award results mixed on one list | parser must separate 公告中 from 結果公表 entries |
| P6 機関トップ／汎用ページ型 | 1,658 | recorded target is a top/generic/CMS category page; the real listing sits deeper or inside the news flow | needs the nav-located listing (tonight's crawls supplied it for 74 objects) or news-list + keyword （入札・公募・プロポーザル) filtering at low frequency |
| P7 PDF直接リンク型 | 0 | none in the confirmed set (the single KKJ-era candidate never confirmed) | — |

Representative sampled sites — P1: 北海道農政事務所 (ul_list, static_html
details), 国立大学法人北海道大学 (search form on the list page, PDF details).
P2: 函館税関 (ul_list), 北海道厚生局 (ul_list). P3: （独）日本高速道路保有・
債務返済機構 (table_list, numbered pagination, mixed HTML/PDF details).
P4: 北海道地方環境事務所 /procure/ (search_form system page). P5: 北海道開発局
各開発建設部 (keiyaku pages, link/ul lists). P6: 人事院北海道事務局 (top page
with a 調達 link in the news flow).

## List-page structures (sampled objects only, n=1,273)

| structure | objects | note |
|---|---|---|
| ul_list | 715 | dominant pattern: CMS news-style lists; parse li > a + date text |
| link_list | 224 | loose anchor collections without list markup |
| search_form | 109 | few/no static notice links; results require the public search UI (P4-style handling when the path is a system page) |
| pdf_index | 105 | ≥60% of notice links are PDFs; the list is effectively a PDF index — fetch list HTML, then PDFs |
| table_list | 37 | classic table layouts (hospitals, older ministry pages) |
| calendar | 21 | month-navigated posting calendars （整備局 offices) |
| dl_list | 6 | definition-list layouts |
| insufficient_links | 56 | page fetched but <1 qualifying notice link (stale or hub pages) — re-check before collector build |

## Detail pages

| detail_pattern | objects | | detail_format | objects |
|---|---|---|---|---|
| static_html | 879 | | html | 1,082 |
| numeric_id | 242 | | pdf | 96 |
| pdf_direct | 137 | | mixed | 94 |
| query_id | 11 | | (unknown, evidence-only) | 1,733 |
| date_path | 3 | | | |
| (unknown, evidence-only) | 1,733 | | | |

Pagination: `next_or_numbered` detected on 140 sampled lists;
`none_detected` on 1,133 (single-page lists or pagination below the fold of
the sampled HTML); `unknown` for the 1,732 evidence-only rows. Collectors
should treat none_detected as "verify on first deep crawl", not as proof of
a single page.

## Subclass signals worth carrying into collector design

- **P4-public-search**: search_form structures on system paths (109 sampled)
  split into true e-procurement public search (build a query adapter) and
  ordinary site search pasted on a list page (parse the static list
  underneath). The CSV keeps them separable via p_class + list_structure.
- **P6-news-flow**: the largest single group (P6, 1,658). For small
  municipalities the announcement "list" is the site's news list; the
  working collector is an RSS/news watcher with keyword filtering, at the
  lowest polling tier — tonight's crawl evidence confirms most of these
  sites publish bid notices only a few times a year.
- **Shared-host pooling**: hundreds of objects share ministry/prefecture
  hosts; per-endpoint polling (docs/05) collapses them — the template of
  the shared listing, not the object, is the unit that matters.
