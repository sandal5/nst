# Cloudflare Worker 部署

默认部署目标：Cloudflare Workers（免费层可跑通小规模，正式全量建议 Workers Paid + Queues + D1）。

## 架构与配额现实
- 全量源头约 3,900 个、唯一 URL 3,448 个。单个 Worker 调用有 subrequest 上限（免费层 50/次），**不能在一个调用里抓全量**。
- 因此：Cron 每日 08:00 JST 只负责**分片入队**（按都道府县 47 片），每片由一个 Queue consumer 调用慢慢抓（同域名 ≥2 秒）。
- robots.txt 结果缓存 KV 24h；公告存 D1（`worker/schema.sql`），KV 存当日分片结果作缓存。

## 步骤
```bash
cd worker
npm i -g wrangler   # 或 npx wrangler
wrangler login
wrangler kv namespace create KV
wrangler d1 create nst
wrangler d1 execute nst --file schema.sql
wrangler queues create nst-collect
# 把上面得到的 id 填入 wrangler.toml 的绑定段（文件里有模板）
# 导入源头清单到 KV（先在 CSV 中把已验证的行标 terms_status=verified 并填 verified_bidding_url）：
node import_sources.mjs ../data/master_source_list.csv > sources.json
wrangler kv key put --binding KV "sources:all" --path sources.json
wrangler secret put ADMIN_TOKEN
wrangler deploy
```

## 上线门禁（代码已内置）
- `COLLECT_ONLY_VERIFIED=true`：只有 `terms_status=verified` 的源头会被采集；未验证的一律跳过并在结果里注明原因。
- robots 禁止路径、P6 未定位列表页、P4 无公开 adapter 的系统，都会跳过并登记，不会硬抓。
- 手动触发：`POST /api/run`（需 `Authorization: Bearer <ADMIN_TOKEN>`），单次最多跑 10 个源头，用于调试。

## 本地开发
`npx wrangler dev` 后访问 `/`、`/api/sources`。无绑定时 /api/sources 返回空数组——先完成 KV 导入。
