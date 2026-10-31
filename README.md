# OpenDev Elite

把 GitHub 项目发现、维护证据、两轮筛选、联系/访谈记录、Credits 赞助与复盘连接起来的浏览器工作台、CLI 和团队 JSON API。Python 3.11+；默认运行时仅使用标准库，OIDC 单点登录使用可选依赖。

**当前版本 v0.8.0：五项质量门槛、证据评分与简洁登录；工作台布局全面优化。** Docker Compose 使用 `docker compose up -d` 启动。本地原爬虫持续复用；两轮评审、访谈、Credits 与复盘共用同一业务链。真实 IdP、Agent 镜像和 Credits 服务联调需配置对应环境。

## 管理员与团队登录

首次启动后打开登录区，使用默认管理员 **OpenDev / 123456** 登录。公开管理员创建页和成员注册页已移除。管理员在 **成员与权限 → 新增成员** 中填写账号、显示名称、初始密码并分配角色，成员即可登录，无需领取 Token。默认密码只在空账号库首次初始化时写入，改密后重启不会覆盖。已有账号的 v0.5 安装保留原账号。

账号支持停用、角色修改、管理员重置密码和本人修改密码。原有 Token 登录保留在折叠的兼容入口，Agent 继续使用专用 Token。新成员和修改后的密码至少 6 个字符；默认管理员的初始密码仍为 123456。完整说明见 [账号注册与授权](docs/账号注册与授权.md)。

开发从 [当前开发规格](docs/自动化开发计划.md) 和 [v0.6 工作台规格](docs/v0.6工作台优化规格.md) 开始；团队使用见 [操作指南](docs/团队操作指南.md)，部署见 [部署手册](docs/部署手册.md)。当前发布包及验证结果见 [v0.8.0 交付说明](docs/v0.8.0交付说明.md)；[v0.6 验收矩阵](docs/v0.6验收矩阵.md)及以前交付记录保留为历史基线。

## v0.8.0 质量筛选与界面

入选须总分至少 80/100、每项至少 3/4：需求 20%、工程 25%、维护 20%、差异化 20%、可评测性 15%。逐项记录依据，维护需一年内两个不同日期和两个对应来源；低分先暂缓，旧标准须重评。服务端重算分数，不能绕过两轮审核。完整定义见 [入选标准](docs/项目入选与排除标准.md)。

登录页只保留账号密码及必要入口；中英文一致。工作台重排导航、概览、筛选列表、质量规则与评估表单，支持手机和平板。默认账号和后台创建成员方式延续。

## v0.7.4 团队身份验收

上线检查合并统计启用的兼容 Token 与活跃团队账号，避免密码登录成员被漏计。停用账号不参与角色分工判断，相同 actor 不重复计数；只报告计数，不读取密码哈希。见 [交付说明](docs/v0.7.4交付说明.md)。

## v0.7.3 GitHub 采集身份

本地部署可显式复用已有 GitHub CLI 登录，令牌只保留在进程内存；环境变量和 Secret 文件继续可用。搜索及代码采集核对公开仓库范围。见 [身份配置](docs/GitHub采集身份配置.md)及 [交付说明](docs/v0.7.3交付说明.md)。

## v0.7.2 技术复核材料

项目仓库档案展示固定版本代码预览、提交、发布和议题线索，标明缺项、截断与历史证据局限。见 [技术证据规格](docs/v0.7.2技术证据规格.md)与 [交付说明](docs/v0.7.2交付说明.md)。

## v0.7.1 标准与采集一致性

默认发现含 Fork 的候选；按实现、测试、依赖和 CI 四类采样，并记录版本与采样局限。Fork 仍须评估新增贡献，样本存在不等于测试或审核通过。见 [交付说明](docs/v0.7.1交付说明.md)和 [真实候选技术复核准备](docs/真实候选技术复核准备-20260908.md)。

## v0.7.0 人才档案与前期筛选

按 GitHub ID 汇总跨项目贡献，区分个人与组织、候选与已确认维护关系。访谈画像保留原始记录、责任人与时间；联系、访谈、发放和复盘仍按角色授权。新增分配、补证据、入选评估及第一轮技术复核待办，暂缓/排除项目不会自动推回初筛。完整范围见 [持续优化验收](docs/核心SOP持续优化验收.md)与 [开发者档案接口](docs/开发者档案与前期待办接口.md)。

## v0.6.1 入选标准深化

支持真实用户 AI 工具及供 Agent 调用的应用；五项入选前提、六类暂缓、八类排除，按实际维护证据判断。标准页、评估表单和服务端共享条款，旧标准评估需重评。完整定义见 [项目入选与排除标准](docs/项目入选与排除标准.md)及 [v0.6.1 交付说明](docs/v0.6.1交付说明.md)。

## v0.6 有哪些变化

- 候选发现与正式入选分开；根据方向、持续维护和可核对来源评估，个人、小团队、早期及零 Stars 项目均可。
- 界面可切换中文 / English 并记住偏好；项目名、代码、用户输入和原始证据保持原样。
- 仓库档案提供中文简介、README 阅读摘要、译文和原文核对；翻译绑定源文摘要，源文更新使旧译文失效。
- 项目详情按档案、入选、两轮审核、体验、联系/访谈、赞助及复盘分区，历史分页可查。
- 当前下一步区分暂缓、拒绝、等待领取、结果不明、到账、复盘、关闭及续周期；旧证据失效不隐藏已经发生的财务义务。
- schema 5 自动迁移到 schema 6，保留账号和业务记录；升级前备份。接口及译文更新命令见 [档案与流程接口](docs/v0.6档案与流程接口.md)。

## v0.4 有哪些变化

- 历史版本的公开首页包含交互式跟进预览、多渠道记录说明、Agent 集成与本地 SOP 对话导航、支持方案对比及团队登录。该介绍与预览页已于 v0.8.0 简化为独立登录页。
- 团队待办按身份、当前证据和有效租约区分可执行、执行中、需补齐条件及到期复盘，可直接进入具体记录。
- 新赞助周期不再被历史结束状态遮蔽；实际入站回复可在证据过期后记录，继续推进仍受门槛约束。
- 复盘继续/调整/暂停必须保留下一次日期，避免跟进中断；结束取消剩余待办。
- 修正联系表单和异步退出竞态，增加表单联动、响应式布局、键盘焦点和更清晰的文字对比。

## Docker Compose 部署

在含 `compose.yaml` 的目录执行（Docker Engine / Docker Desktop Linux 容器模式，Compose v2.20+）：

```sh
docker compose up -d
docker compose ps -a
```

访问 `http://127.0.0.1:8780`。已配置自动从本地源码构建镜像，后续启动复用构建缓存，无需另加 `--build`。初始化容器创建六类身份后正常退出；API、采集进程和网关常驻。后台启动不等待全部健康检查完成，可用 `docker compose ps -a` 查看状态。数据库、凭证与日志保存在 `opendev-elite_state` 本地数据卷，重建容器保留数据。已有本机服务使用 8765，默认端口不会冲突。

可复制 `.env.example` 为 `.env` 设置 GitHub Token、端口与访问域名；配置 HTTPS 域名后经宿主机 TLS 反向代理访问。**凭证领取、Credits 配置、备份和升级步骤见 [Docker Compose 部署手册](docs/DockerCompose部署.md)。** 镜像包含 OIDC 可选依赖；Agent 执行节点独立部署。

## Windows 直接启动浏览器工作台

```powershell
python -m pip install -e .
$appRoot = Join-Path $env:LOCALAPPDATA 'github-sponsorship-production'
python main.py setup-local --root $appRoot
./scripts/start-local.ps1 -Root $appRoot -Python (Get-Command python).Source
```

访问 `http://127.0.0.1:8765/#team-login`，使用 OpenDev / 123456 登录，再从后台新增成员并分配角色。审核、审批及发放使用各自身份，原有 Token 登录作为兼容入口保留。可用 `./scripts/stop-local.ps1 -Root $appRoot` 停止，数据库保留。重复启动不会重复建立工作进程。

演示必须使用独立目录并显式传 `-Demo`，不能将演示结果用于真实验收。启用公司登录见 [SSO 接入说明](docs/SSO接入说明.md)。

## 运行

在 `GitHub_Worker` 目录执行：

```powershell
python main.py init
python main.py import owner/repo
python main.py work --max-jobs 1
python main.py status
python main.py report --out artifacts/report.md
```

公开仓库可匿名读取；实际批量采集应在本机设置 `GITHUB_TOKEN`。未配置令牌时，采集器会按 GitHub 匿名每小时配额自动放慢请求并共享退避状态，不会用并发请求反复撞击限流。正式 `Pipeline` 已调用重构后的 `src.py.fetcher` 和 `src.py.userinfo`，旧 `bootstrap` 入口也使用同一队列与数据库。程序不会加载旧 `API_KEYS` 或启动地域推断；C++ 编译不是采集前提。具体复用范围见 [原爬虫整合说明](docs/原爬虫整合说明.md)。

默认数据库在 Windows `%LOCALAPPDATA%/github-sponsorship/worker.db`，其他系统在 `~/.local/share/github-sponsorship/worker.db`；通过 `--db` 或 `SPONSORSHIP_DB` 指定其他位置。不要将运行中的 SQLite 放在 OneDrive、共享网盘或跨主机文件系统上。

## 发现与持续更新

```powershell
python main.py schedule --config config/discovery.json --start 2026-06-01 --end 2026-09-07
python main.py work --max-jobs 20 --workers 2
python main.py status
python main.py import-csv path/to/Projects_Today.csv
```

不传日期时按当前日期回看 7 天。`schedule` 仅写入队列，不注册系统定时任务；`work` 是一次有界运行，遇到限流保存重试时间后退出。可由已有任务调度系统重复调用；同一日期和配置的计划任务去重。失败终止的任务用 `retry-job ID` 显式重试。旧 CSV 只导入仓库地址并重新采集，不继承未经核验的旧画像。

GitHub 搜索按推送日期和 Stars 范围拆分。不能继续拆分或上游返回不完整结果时，报告 `partial`；不宣称搜索覆盖全部 GitHub。证据采样范围、分页和截断会一并记录。

持续执行与健康诊断：

```powershell
python main.py doctor
python main.py tick --config config/discovery.json --max-jobs 20 --out artifacts/operations
python main.py run --config config/discovery.json --interval 300 --max-jobs 20 --out artifacts/operations
```

`tick` 执行一轮调度、采集和报告；`run` 重复执行，Ctrl+C 停止，也可用 `--max-cycles` 限定轮数。这些命令不会注册系统服务、发送消息或自动发放 Credits。发现配置中的 `workers` 控制有界并发（1-8，默认 1；模板配置为 2）；每个 worker 使用独立 SQLite 连接，GitHub 令牌通过共享速率/并发闸门协调，不能用它绕过配额。

## 两轮审核

```powershell
python main.py project 123 --out artifacts/project-123.json
python main.py action assign --file examples/assign.json --actor reviewer-name
python main.py action qualify --file examples/project-qualify.json --actor reviewer-name
python main.py action review --file examples/review-round1.json --actor reviewer-name
python main.py action experience-create --file examples/experience-create.json --actor reviewer-name
python main.py action experience-result --file examples/experience-result.json --actor reviewer-name
python main.py action review --file examples/review-round2.json --actor reviewer-name
```

先复制并填写示例文件中的 `REPLACE`、项目 ID 和 Commit。示例不是评审结论，不能直接用于真实项目通过审核。审核人必须实际复核引用的证据，CLI 不验证人的判断是否真实。

先完成方向适配与持续维护的入选评估，再进行第一轮工程结构、维护及文档复核。第二轮必须引用第一轮通过后创建的真实体验结果；面向人和 Agent 的混合产品需要两类结果。版本/受众变化、重新评估入选或第一轮、评审过期（默认 30 天）都会阻止旧结论直接用于联系。

## 自有 Agent

```powershell
python main.py agent-export EXPERIENCE_ID --out artifacts/task.json
python main.py agent-run EXPERIENCE_ID --config config/agent.local.json --out artifacts/run-001
python main.py action experience-result --file artifacts/run-001/output/result.json --actor agent-reviewer
```

支持导出任务给已有 Agent 平台，也支持审核过的、固定 digest 的 Docker 执行镜像。`agent-run` 不构建或拉取陌生仓库镜像，不注入宿主凭证，断网运行，并要求结果人工签收；容器需预置所需依赖和任务数据。需要网络/模型访问的真实产品由团队的隔离执行环境消费相同协议，配置按任务限制的出口与测试凭证。

也可通过认证 API 领取任务、续租和提交结果。任务绑定仓库 SHA、第一轮审核、`run_id` 和任务摘要；过期租约、其他 Agent 身份或失效审核不能提交。第二轮通过仍需审核人确认，详见 API 手册。

v0.3 提供完整消费客户端，在专用 Agent 节点运行：

```powershell
python main.py agent-poll --server https://YOUR-WORKSPACE-HOST --token-file path/to/experience-agent.token --config config/agent.local.json --out "$env:LOCALAPPDATA/github-sponsorship-agent-outbox" --max-tasks 1
```

默认只处理一个任务，自动续租并回传校验结果。未知执行/提交状态会阻止继续领取；`--resume` 仅按持久化的原幂等键恢复允许重试的阶段，不重跑状态不明的容器。凭据和 outbox 必须放在受保护的专用目录，不能位于仓库或同步盘。当前本机没有真实 Docker/Agent 镜像。

## 联系、访谈与赞助

所有业务写入均通过 `action ACTION --file JSON --actor NAME`，有事务与历史事件。支持的动作和字段见 [操作手册](docs/操作手册.md)。联系模块保存草稿和人工已执行动作的证据，不发送任何消息。

Credits 适配针对 `Kadxy/openai-next-credits` 的 PAT 接口：

```powershell
python main.py credits-plan GRANT_ID --config config/credits.local.json
python main.py credits-submit GRANT_ID --config config/credits.local.json --actor operator
# 上一条默认仅预览；真实创建已批准邀请需显式加 --execute。
python main.py credits-reconcile GRANT_ID --config config/credits.local.json --actor operator
```

将 API 地址、活动 slug、后台核实的活动到期日期填写到本地配置，PAT 放在 `NEXT_CREDITS_PAT` 环境变量。`credits-submit --execute` 会创建邀请；可能触发 Credits 系统现有内部审计通知，不会调用邮件发送接口。邀请创建记为 `awaiting_claim`；激活、账户和额度核对通过后才记 `issued`，并创建复盘待办。未知结果不自动重发。实际领取和邮件操作走 Credits 后台现有流程。

## 验证与备份

```powershell
python -m unittest discover -s tests -v
python main.py --db "$env:LOCALAPPDATA/github-sponsorship/demo-new.db" demo --out artifacts/demo
python main.py backup path/to/new-backup.db
```

`demo` 必须使用空数据库，所有项目、体验、沟通及到账结果都是明确标注的合成数据；无真实网络请求、联系或发放。测试覆盖门槛、过期、去重、故障重试、搜索拆分、备份和 Credits 对账。

CLI 的 `--actor` 是本地可信操作员的责任标签。浏览器通过 HttpOnly 会话操作，API 使用 Bearer 身份与角色权限；服务端确定 actor，审批人与发放执行人分离，Agent 使用独立身份。启动配置、TLS 与幂等要求见 [服务端 API 手册](docs/服务端API手册.md)。

`acceptance --service-config ... --credits-config ... --agent-config ... --out path/to/new-report.json` 生成只读验收清单。该工具识别明确的合成数据并盘点记录，不把数据库中有记录等同于真实业务已经通过外部验收。详见 [上线验收清单](docs/上线验收清单.md)。
