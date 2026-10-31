# v0.6.0 验证资料

2026-09-07 实际运行结果的发布副本。原始报告保存在工作区 `artifacts/validation/`；迁移与安装报告省略本机绝对部署路径，其余结果字段保留。

- `tests.txt`：最终 306 项完整回归输出。
- `browser-accounts.json` / `browser-session.json`：合成环境账号管理与会话检查。
- `browser-roles.json`：五种角色、两种语言、四种宽度。
- `browser-sop.json`：两种语言完整 SOP，离线 Credits 传输；真实消息和额度请求均为 0。
- `browser-dossiers.json`：真实资料的独立工作副本，145 条简介、当时已采集的 14 份 README 摘要；不是最终正式库数量。
- `browser-forms.json`：补齐复盘、可读计划及主要表单的响应式、键盘检查。
- `browser-production.json`：升级后的真实本机服务，只登录、读取、退出；验证补采第 15 份 README 摘要。
- `default-login.json` / `wheel.json`：实际默认账号与安装包隔离导入。
- `migration.json`：schema 5→6 的原有行保留及 145+15 条译文导入。
- `compose.json`：配置检查结果，明确容器实机未运行。

开发阶段脚本的 `0.6-development` 标签保留，最终版本以独立安装及正式服务报告中的 `0.6.0` 为准。以上资料不是外部 Credits、实际产品体验或真实赞助验收；数量反映各报告生成时刻。
