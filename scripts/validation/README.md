# 浏览器与发布验收脚本

## v0.7.2 技术证据阅读

`serve-technical-fixture.py --port 8884` 创建独立合成库；`browser-v072-technical.js` 检查技术证据预览、来源、截断、历史和缺失状态、双语、键盘、响应式及被动显示外部文本。只登录/退出，不写业务。

`browser-v072-production.js` 在 8765 读取三个指定真实项目。当前版本断言为 0.7.2；业务已开始或账号已改密后不能修改数据来满足历史断言。`wheel-smoke.py` 当前验证 0.7.2 的 19 个资源/API 路径，需使用安装环境的 `-I`、源码目录之外的工作目录。

## v0.7.1 标准与采集一致性

`live-crawler-smoke.py --db <全新数据库> --repo <owner/repo> [--use-gh-auth]` 只读取一至五个公开 GitHub 仓库，验证采样版本与 blob SHA；禁止复用已有库，不运行第三方代码。可选复用现有 gh 凭证仅保存在进程内存，输出不包含凭证。

`browser-v071-production.js` 读取正式 8765 服务、标准和未提交表单，只登录/退出；当前发布快照要求原候选保留且无正式评估。真实业务开始后不能改数据迎合旧断言。`wheel-smoke.py` 当前验证 0.7.1，使用安装环境的 Python `-I` 从源码目录之外运行。

## v0.7.0 开发者档案与前期待办

`serve-developers-fixture.py --port 8882` 创建独立合成库；`browser-developers.js` 验证跨仓库聚合、筛选分页、原始记录跳转、角色权限、响应式和退出竞态，只执行登录/退出，无业务写入。

`serve-sop-fixture.py --port 8883` 搭配 `browser-v070-sop.js`，验证完整中英文离线 SOP；不连接真实 Credits。每次完整复测新建 fixture，禁止清空正式数据。

`browser-v070-production.js` 只在 8765 登录/退出、读取真实目录、待办、标准及打开未提交表单。数量断言是本次发布基线，后续不要更改业务数据来满足历史断言。`wheel-smoke.py` 在独立安装的 0.7.0 运行环境核对 18 个资源和 API 路径。

## v0.6.1 入选标准 v2

启动专用离线环境：`python scripts/validation/serve-sop-fixture.py --port 8881`。先运行 `browser-selection-sop.js`，再运行 `browser-selection-policy.js`；后者会对同一组合成项目追加暂缓/入选/排除评估。重跑完整链路需重启专用 fixture 创建新库，不能重置正式数据。

`browser-selection-production.js` 仅读取正式 8765 工作台、登录/退出及打开未提交的评估表单。默认账号已改密或真实评估开始后，需要调整其发布快照断言，不要更改真实数据以适应旧脚本。当前程序版本验证脚本为 0.6.1。

## 当前 v0.6.0 验收

- `browser-v06-workspace.js`、`browser-v06-session.js`、`browser-v06-accounts.js` 使用 8877 的独立合成环境；先检查对应样本状态。账号脚本使用唯一临时账号，结束后停用，写入前核实 demo_mode。
- `browser-v06-roles.js` 在 8877 验证 reviewer/operator/approver/disburser/auditor 的双语项目历史和财务/复盘可见性，覆盖 375/768/1024/1440 宽度，结束停用新增测试账号。
- `browser-v06-dossiers.js` 只读取 8878 的中文资料工作副本。此脚本固定验证本次 145 个实际候选和 14 份已采集 README；用于本次翻译验收，不是未来不断增长的数据库通用数量断言。不可直接改端口指向正式库。
- `serve-sop-fixture.py` 每次创建全新临时库，仅监听 8879。使用真实服务端业务门槛和内存模拟的 Credits 传输，完全不连接外部服务。`browser-v06-sop.js` 用中英两种语言完成两个独立赞助周期。

```powershell
python scripts/validation/serve-sop-fixture.py
# 在另一个终端运行；浏览器会使用上述专用服务。
npx --yes --package @playwright/cli playwright-cli -s=sop open http://127.0.0.1:8879
npx --yes --package @playwright/cli playwright-cli -s=sop run-code --filename=scripts/validation/browser-v06-sop.js
```

整链测试会结束项目 401/402 的模拟赞助。重跑先 Ctrl+C 关闭此专用 fixture，再重新启动以获得新库；不要删除或重置正式数据库。`example.invalid` 邮箱、fixture token 与测试记录都仅适用于这些隔离环境。

验收输出在 `artifacts/validation/browser-v06*.json`，截图在 `output/playwright/v06*`。详细验证边界见 `docs/v0.6交付说明.md`。

- `browser-v060-final-forms.js` 在 8877 检查双语表单及键盘，要求合成项目 499/498 为已到账但尚未安排复盘；脚本会新增复盘。它还在完成 SOP 的 8879 中检查项目 401 的 Credits 计划。运行前准备对应样本，不能在已消耗的样本上盲目重跑。
- `browser-v060-production.js` 在 8765 使用独立浏览器上下文验证 v0.6.0、默认账号、双语和四种宽度，只登录、读取和退出。本次固定检查原有 145 个候选、一个管理员及零评审的发布快照；实际业务开始或改密后需调整验收条件，不能修改真实数据来满足旧断言。
- `default-login-smoke.py` 及 `wheel-smoke.py` 已升级为 0.6.0；后者从源码之外、安装环境中运行，结果保存为 `wheel-v060.json`。

## 历史脚本与版本

v0.7.4 使用 `audit-v074.py` 完成安装/数据核查，`readiness-v074.py --root <运行目录>` 在只读连接中运行实际身份和业务证据审计。配置参数和结果都不证明外部环境已连通。当前 `wheel-smoke.py` 与 `default-login-smoke.py` 默认版本为 0.7.4；历史脚本保留各自版本范围。

v0.7.3 的安装/数据核查使用 `audit-v073.py --context <本地私有上下文.json>`；加 `--live` 时只读正式数据库并核对备份、业务记录、公开元数据及最终重启后的采集进展。上下文包含本机路径，不加入发布包。`default-login-smoke.py` 默认期望 0.7.3，可通过 `--expected-version` 指定目标；它仍只适用于明确保留初始管理员密码的环境，不创建或重置账号。当前 `wheel-smoke.py` 期望 0.7.3，历史下述脚本仍保留各自版本断言。

历史账号版本 v0.5.1 使用 `browser-v051-accounts.js`，在独立、全新的演示库与 `http://127.0.0.1:8876` 运行；检查默认管理员登录与管理员后台创建成员。旧 `browser-v05-accounts.js` 仅对应 v0.5.0 的公开注册流程，不适用于当前版本。`default-login-smoke.py` 可验证明确指定的初始管理员并退出，不创建成员或修改业务数据；已有账号改密后不再适用。

这些脚本只用于单独的合成演示服务 `http://127.0.0.1:8874`。严禁把固定测试身份添加到正式配置。页面脚本中的身份是已知合成测试数据，不是正式凭证。

准备独立 demo 数据库后，用 `main.py --db <demo.db> serve --config <demo-config> --port 8874` 启动。配置须有 `demo_mode:true`、匹配的 public_origin、禁用真实 Credits。合成数据须有项目 101（完整 demo）、项目 301/302（第一轮通过并有待完成人类体验），以及 synthetic-browser-admin/reviewer/operator/disburser 对应测试身份。各脚本 token 文本的 SHA-256 仅写入该独立配置。

按顺序在同一个 Playwright CLI 会话中运行；不要并发操作同一页面：

```powershell
npx --yes --package @playwright/cli playwright-cli -s=v04 open http://127.0.0.1:8874 --headed
npx --yes --package @playwright/cli playwright-cli -s=v04 run-code --filename scripts/validation/browser-v04.js
npx --yes --package @playwright/cli playwright-cli -s=v04 run-code --filename scripts/validation/browser-v04-races.js
npx --yes --package @playwright/cli playwright-cli -s=v04 run-code --filename scripts/validation/browser-v04-submit-race.js
npx --yes --package @playwright/cli playwright-cli -s=v04 run-code --filename scripts/validation/browser-v04-a11y.js
```

第一组检查四种宽度、预览、登录、待办和表单；第二组会保存一条合成人类体验结果，耗用一个 pending 任务。重跑前检查合成样本。竞态检查延迟审核数据、计划或提交结果，验证不会覆盖已退出或新打开的 UI。对比度检查是当前可见文字的计算检查，不等同于全站 WCAG 认证。

`wheel-smoke.py` 必须由安装新 wheel 的独立 Python 使用 `-I` 执行，且 cwd 在源码之外。它创建临时合成库和临时 HTTP 服务，验证包内资源、版本及待办；不会连接外部平台。
