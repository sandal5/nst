# gateway/ — 项目采集网关（Fetch Gateway）

一个带范围锁的抓取代理，部署在 Cloudflare Workers 上。

## 为什么需要它

助手运行环境按「目标域名」逐张弹网络访问许可卡：注册表有数千个官方域名，逐个点批准不可行。改走网关后，助手的所有抓取只连接网关这一个域名，用户对网关点一次「总是允许」即可；目标页面由网关在服务器端取回。

## 范围锁（不是开放代理）

1. **密钥**：请求必须带 `X-Gateway-Key` 头，值与 Worker 密钥 `GATEWAY_KEY` 一致，否则 401。
2. **域名白名单**：目标域名必须在项目注册表的域名清单内（`src/allowlist.js`，由 `scripts/gen_allowlist.py` 从注册表生成），清单外一律 403 拒绝。

robots 先行、单站限速、署名 UA 等礼貌规则仍由调用端执行，与直连时完全一致；网关只负责「取回这一页」。

## 接口

`GET /fetch?url=<urlencoded 目标 URL>`，返回 JSON：

- `status`、`final_url`（跳转后终点）、`content_type`、`etag`、`last_modified`
- `body_base64`：响应体（上限 8 MB，兼容 PDF 等二进制详情页）

## 部署

```bash
cd gateway
npx wrangler login          # 一次性，项目所有者本人登录
npx wrangler secret put GATEWAY_KEY
npx wrangler deploy
```

白名单更新：注册表域名清单变化后重跑 `python3 scripts/gen_allowlist.py` 并重新 deploy。
