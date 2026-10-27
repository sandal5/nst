import json, csv
from urllib.parse import urlparse
rows=json.load(open("data/kkj_classified.json"))
out=[]
for r in rows:
    level = "国・独立行政法人等" if r["category_kkj"]=="国・独立行政法人等" else ("都道府県" if r["category_kkj"]=="都道府県" else "市区町村")
    out.append({
        "prefecture": r["prefecture"], "level": level, "org_name": r["org_name"],
        "group_ministry": r["group_ministry"] or "",
        "source_url_kkj": r["url"],
        "official_domain": urlparse(r["url"]).netloc,
        "framework_guess_by_url": r["framework_guess"],
        "in_kkj": "yes", "in_nsearch_top": "",
        "verification_status": "未検証 (v1はKKJ掲載URLをそのまま収録)",
        "daily_collection_target": "このURL自体ではなく、検証後に確定する公式の入札公告一覧/電子調達システム",
        "source_note": "KKJ 発注機関へのリンク集 (手動閲覧で参照; KKJ自体はrobots Disallow:/ のため自動収集対象外)",
    })
# nsearch top orgs (manually read from the public /organizations page in browser, top by case count) - cross-check supplement
nsearch_top=[
 ("防衛省","東京都","国","https://www.mod.go.jp/"),("国土交通省","東京都","国","https://www.mlit.go.jp/"),
 ("秋田県","秋田県","都道府県","https://www.pref.akita.lg.jp/"),("厚生労働省","東京都","国","https://www.mhlw.go.jp/"),
 ("茨城県","茨城県","都道府県","https://www.pref.ibaraki.jp/"),("文部科学省","東京都","国","https://www.mext.go.jp/"),
 ("熊本市","熊本県","市区町村","https://www.city.kumamoto.jp/"),("農林水産省","東京都","国","https://www.maff.go.jp/"),
 ("東京都","東京都","都道府県","https://www.metro.tokyo.lg.jp/"),("愛知県","愛知県","都道府県","https://www.pref.aichi.jp/"),
 ("岐阜県","岐阜県","都道府県","https://www.pref.gifu.lg.jp/"),("大阪市","大阪府","市区町村","https://www.city.osaka.lg.jp/"),
 ("三重県","三重県","都道府県","https://www.pref.mie.lg.jp/"),("法務省","東京都","国","https://www.moj.go.jp/"),
 ("神奈川県","神奈川県","都道府県","https://www.pref.kanagawa.jp/"),("埼玉県","埼玉県","都道府県","https://www.pref.saitama.lg.jp/"),
 ("京都市","京都府","市区町村","https://www.city.kyoto.lg.jp/"),("財務省","東京都","国","https://www.mof.go.jp/"),
 ("経済産業省","東京都","国","https://www.meti.go.jp/"),("大阪府","大阪府","都道府県","https://www.pref.osaka.lg.jp/"),
 ("香川県","香川県","都道府県","https://www.pref.kagawa.lg.jp/"),("横浜市","神奈川県","市区町村","https://www.city.yokohama.lg.jp/"),
 ("熊本県","熊本県","都道府県","https://www.pref.kumamoto.jp/"),("福島県","福島県","都道府県","https://www.pref.fukushima.lg.jp/"),
]
kkj_doms=set(o["official_domain"] for o in out)
added=0
for name,pref,lv,url in nsearch_top:
    d=urlparse(url).netloc
    # mark existing
    for o in out:
        if o["official_domain"]==d: o["in_nsearch_top"]="yes"
    if d not in kkj_doms:
        out.append({"prefecture":pref,"level":lv,"org_name":name,"group_ministry":"","source_url_kkj":"",
          "official_domain":d,"framework_guess_by_url":"P6 機関トップ (nsearch掲載の公式トップ)","in_kkj":"no (同一ドメインなし)","in_nsearch_top":"yes",
          "verification_status":"未検証","daily_collection_target":"公式トップから入札・契約カテゴリを辿って確定する",
          "source_note": "nsearch 発注機関一覧 (ブラウザで手動閲覧した上位機関のみ; nsearch規約で自動収集禁止のため一括取得はしない). URLは機関公式トップ"})
        added+=1
print("master rows", len(out), "nsearch-only added", added)
# central government systems not as single-org rows in KKJ directory style
extra=[
 {"prefecture":"全国","level":"国 (共通システム)","org_name":"調達ポータル / 政府電子調達システム (GEPS)","group_ministry":"デジタル庁・各府省共通","source_url_kkj":"","official_domain":"www.p-portal.go.jp","framework_guess_by_url":"P4 電子入札/電子調達システム型","in_kkj":"","in_nsearch_top":"","verification_status":"公式公告文中で多数参照されることを確認 (検索結果経由)","daily_collection_target":"調達ポータルの調達案件検索 (利用規約を個別確認後に方法決定)","source_note":"各府省の入札公告PDFが一貫して案内する中央政府の公式入口. KKJの機関別リンクとは別枠の共通基盤として追加"},
 {"prefecture":"全国","level":"国 (共通システム)","org_name":"政府電子調達システム ポータル (GEPS)","group_ministry":"各府省共通","source_url_kkj":"","official_domain":"www.geps.go.jp","framework_guess_by_url":"P4 電子入札/電子調達システム型","in_kkj":"","in_nsearch_top":"","verification_status":"未検証","daily_collection_target":"同上","source_note":"GEPS案内サイト. 実案件検索は調達ポータル側"},
]
out.extend(extra)
with open("output/master_source_list.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
print("written output/master_source_list.csv rows=",len(out))
