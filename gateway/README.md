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

## 部署（推荐：GitHub 绑定，自动 CI/CD）

不依赖任何本地 Cloudflare 环境：在 Cloudflare 控制台把本仓库绑定到 Worker，push 到 main 即自动构建部署，构建记录在 Cloudflare 控制台与 GitHub checks 里都可见。

一次性设置（仓库所有者在 Cloudflare 控制台完成）：

1. Workers & Pages → Create / Import → 连接 GitHub，选择本仓库。
2. Root directory 填 `gateway`，部署命令用默认的 `npx wrangler deploy`。
3. Worker 的 Settings → Variables and Secrets 里加 Secret：`GATEWAY_KEY`（值自定，调用端本地配置持有，不进仓库）。

之后改 `gateway/` 或白名单后 push 即可自动上线。

本地部署（备选）：

```bash
cd gateway
npx wrangler login
npx wrangler secret put GATEWAY_KEY
npx wrangler deploy
```

白名单更新：注册表域名清单变化后重跑 `python3 scripts/gen_allowlist.py` 并 push（绑定部署会自动上线）。
