# Contributing

## 认领一个都道府县（验证工作流）
1. 在 Issues 里认领都道府县，避免重复劳动。
2. 对该县 `data/master_source_list.csv` 的每一行：
   - 打开机关**官方网站**（以清单中的官方域名为准），沿「トップ → 入札・契約 → 入札公告」导航找到现行公告列表页；
   - 回填 `verified_bidding_url`、真实框架类型（P1–P5）、`verification_status=verified (YYYY-MM-DD)`；
   - 查该站 robots.txt 与利用規約，在行内或 PR 描述里注明规约类型（公共データ利用規約系 / 单独规约 / robots 禁止路径）。
3. 旧链接失效时，不要直接改掉原值：把「旧 URL → 新 URL + 官方依据链接」追加到 `data/corrections_verified.csv`，再更新主清单。
4. 一个 PR 只做一个都道府县（或一个共同利用系统），附依据链接便于复核。

## 红线
- 不提交针对 kkj.go.jp / nsearch.jp 的自动化抓取代码，不批量复制其站内数据（依据见 docs/01_compliance.md）。
- 采集/验证脚本必须 robots 优先、同域名低频（≥1.5 秒）、遇 403/429 即停，不绕过登录、验证码与访问限制。
- 只采集无需登录的公开公告部分；电子入札系统的投标功能不在范围内。

## 代码
- Worker：`worker/`（Cloudflare Workers, ESM）。本地 `npx wrangler dev`。
- Python 参考实现：`scripts/`，依赖见 `requirements.txt`。
