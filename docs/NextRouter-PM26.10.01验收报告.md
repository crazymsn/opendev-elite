# NextRouter-PM26.10.01 历史开发、审核与验收报告

> 本文件记录 2026-10-02 的部署快照，不代表 2026-10-16 的当前工作区状态。当前代码、测试和限制以 [实施审计与验收-20261016](NextRouter-PM26.10.01实施审计与验收-20261016.md) 为准。

验收日期：2026-10-02（Asia/Shanghai）

## 交付结论

- 生产部署：通过，OpenDeploy deployment `06a49f17-e91a-474a-8614-97e15c830274`，版本 `1.0.11`，状态 `success / active`。
- 访问地址：https://ee06e589.opendeploy.site
- 服务：`github-worker`，端口 `8080`，单副本，运行时 `OPENDEV_PUBLIC_ORIGIN` 与公开域名一致。
- 本轮修复：Trending 运行与 GitHub Search 来源互斥排队，避免重复采集；工作概览补齐 AI 已完成、失败、过期统计。

## 自动化验证

- `py -3 -m unittest discover -s tests -q`：373 项通过，0 失败（191.058 秒）。
- `tests.test_nextrouter_features`、`tests.test_service`、`tests.test_browser_auth`：34 项通过。
- Python `compileall`：通过。
- 前端 `node --check`（app/forms/views/developers/accounts/landing/i18n.en）：通过。

## 生产烟测

- `GET /healthz`：HTTP 200，`{"ok":true}`。
- 最终版本线上复测：`GET /`、`GET /openapi.json`、`GET /auth/options`、`GET /api/ui-config`、`GET /api/status`、`GET /api/projects?page=1&page_size=5`、`GET /api/metrics` 均返回 HTTP 200。
- `GET /`、静态资源、`GET /openapi.json`：HTTP 200。
- 同源密码登录：HTTP 200，HTTPS `__Host-` 会话 Cookie，`/auth/me` 返回 admin 会话。
- 跨源登录：HTTP 403，返回 `Exact same-origin request required`；此前“Cross-origin browser requests are not supported”问题已按同源策略复测通过。
- `/api/ui-config`、`/api/status`、`/api/projects`、`/api/metrics`：已登录请求均 HTTP 200。
- OpenAPI 中确认存在 discovery runs、榜单、AI assessment、联系方式摘要相关路径，并确认 `/api/projects/{id}/rankings` UID 兼容路由。
- 生产前端资源确认包含 UID、榜单/线索字段及 AI 三类统计文案。
- 生产会话 Cookie 确认使用 `__Host-sponsorship_session`、`Secure`、`HttpOnly`；不存在项目时 UID 详情和榜单路由均按设计返回 HTTP 404。
- 离线 `acceptance` 审计不会把空数据库、演示数据或 HTTP 回归测试冒充真实 GitHub/体验/Credits 证据；当前生产未配置 GitHub token、Agent 执行器或 Credits 写入，因此这些外部证据保持未认证状态，符合系统的 fail-closed 规则。

## 业务边界

系统会采集公开仓库证据、三源公开联系方式线索和可追溯规则 AI 建议；不会猜邮箱、读取非公开资料或自动给作者发送消息。榜单、AI 分数和采集数量都不能绕过人工入选、两轮复核、作者同意和独立赞助审批。

## 工具审计备注

OpenDeploy CLI 账号绑定、项目和服务均已核验。CLI `preflight` 报告当前 Codex 主机未检测到 Claude 版 `opendeploy` skill manifest，因此给出 skill 版本策略警告；本次部署使用已登录且受绑定项目约束的 OpenDeploy CLI 上传、创建部署并完成生产烟测，未绕过服务端权限或环境确认。
