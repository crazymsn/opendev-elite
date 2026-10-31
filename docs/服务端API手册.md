# OpenDev Elite 服务端 API 手册（v0.6.0）

## 账号密码与授权

默认网页登录使用 `/auth/password-login`，原 Token `/auth/login` 和 SSO 继续兼容。账号随数据库持久化，当前 schema 为 6。浏览器 POST 需精确同源 Origin，已登录的修改还需 X-CSRF-Token；专用 Bearer API 客户端按身份和接口权限调用。

v0.6 的入选标准、双语档案、分页历史、项目筛选、readiness 及 followup-schedule 详见 [档案与流程接口](v0.6档案与流程接口.md)；完整参数由当前 `/openapi.json` 提供。

| 接口 | 请求 / 权限 | 结果 |
|---|---|---|
| `GET /auth/options` | 公开 | 登录方式；公开注册与初始化入口恒为关闭 |
| `POST /api/accounts` | 管理员、CSRF、Idempotency-Key；username/display_name/password/roles | 创建已授权成员；返回资料不含密码 |
| `POST /auth/password-login` | 公开；username/password | 已授权账号建立 HttpOnly 会话 |
| `GET /api/accounts` | 管理员 | 分页成员列表，无密码摘要 |
| `POST /api/accounts/{id}` | 管理员、CSRF、Idempotency-Key；status/roles/auth_version，可选 password | 授权、停用或重置密码，旧会话失效 |
| `POST /auth/password` | 当前账号、CSRF；current_password/new_password | 改密并退出全部会话 |

账号 id 是稳定的服务端 actor。公开 `/auth/register` 和 `/auth/bootstrap` 已关闭（404）；角色由管理员分配。最后一位启用的管理员不能被停用或降权。默认管理员初始账号为 OpenDev / 123456；新成员及修改后的密码至少 6 个字符；账户变更使用乐观版本校验。账号配置与本机恢复步骤见 [账号注册与授权](账号注册与授权.md)。

## 原有 API 与集成方式

这是受控团队使用的 JSON API，提供浏览器审核页面、SQLite、本地 Bearer 身份、角色权限、会话与操作审计。默认仅依赖 Python 标准库，OIDC SSO 使用可选依赖，见 [SSO 接入说明](SSO接入说明.md)。未实现多租户隔离。CLI 仍是可信本机管理入口；能够直接修改数据库的人不受 API 角色限制。

浏览器入口 `/`；POST `/auth/login` 正文 `{ "token": "个人凭证" }` 换取 HttpOnly SameSite=Strict 会话，GET `/auth/me` 返回当前身份和 csrf_token，POST `/auth/logout` 撤销会话。浏览器 POST 需精确同源 Origin 与 `X-CSRF-Token`；业务 POST 还需要 Idempotency-Key。跨站请求拒绝，默认只信任本机配置的主机和端口，反向代理场景必须配置精确 public_origin。

## 配置与启动

复制 `config/service.example.json` 为 `config/service.local.json`。示例摘要是占位符，未经替换无法启动。为每位操作人和每个 Agent 分别生成凭证：

```powershell
python main.py auth-key --actor technical-reviewer --roles reviewer --secret-out "$env:LOCALAPPDATA/github-sponsorship/reviewer.token"
```

命令只把明文凭证写入指定新文件，标准输出返回身份配置和 SHA-256 摘要。把返回的身份对象放入配置 `users`；不要把明文凭证写入配置、源码或提交记录。Windows 上应限制凭证文件的 NTFS ACL。凭证轮换通过生成新文件、更新摘要并重启服务完成；停用身份可设置 `disabled: true`。不同身份不能共享摘要。

```powershell
python main.py serve --config config/service.local.json --host 127.0.0.1 --port 8765
```

默认仅监听本机。绑定非回环地址必须同时提供 `--tls-cert` 和 `--tls-key`；可使用已有 TLS 反向代理转发到回环地址。Python 服务的 TLS 最低为 1.2。不要把密钥放入仓库。`--credits-config config/credits.local.json` 可加载固定 Credits 服务配置；默认 `allow_credit_writes: false`。

服务最多同时处理 32 个连接，每个连接读取/握手超时 15 秒，请求体最多 1 MiB。服务支持自身同源页面，不提供跨域 CORS。反向代理与进程托管由部署环境负责。数据库应位于本机应用数据目录，不能放在同步盘或跨主机共享文件系统。

## 权限

| 角色 | 允许工作 |
| --- | --- |
| reviewer | 第一/二轮审核、创建体验、录入人类体验、维护者确认、查看候选与体验 |
| operator | 导入/调度/重试、分配负责人、维护者确认、联系草稿与状态、访谈、复盘 |
| approver | 读取访谈、做赞助决定、读取审批与台账 |
| disburser | Credits 计划、显式提交、只读对账、读取台账 |
| agent | 仅领取、续租、提交分配给自身的 Agent 任务，以及读取自身身份 |
| auditor | 读取业务概况、台账、事件、队列和覆盖信息 |
| admin | 人工管理权限；仍不能以人工身份调用 Agent 专用接口，仍不能执行本人批准的发放 |

Agent 角色不能与任何人工角色混用。团队应给审批人和执行人分配不同身份；服务和 Credits 适配器都拒绝审批人执行自己的赞助。角色判断不能代替团队对真实人员身份的管理。

## HTTP 协议

`GET /healthz` 是进程存活检查，不证明数据库或外部服务健康。`GET /openapi.json` 提供路由级 OpenAPI 3.1 描述；业务字段参见操作手册和示例文件。其他路由要求 `Authorization: Bearer <token>`。

所有 POST 要求 `Content-Type: application/json`、JSON 对象和 `Idempotency-Key`。幂等键为 8–128 位字母、数字或 `_.:-`。请求中不允许传 `actor`，服务端从认证身份确定操作人。禁止非有限 JSON 数字和分块请求体。

```powershell
$reviewToken = (Get-Content "$env:LOCALAPPDATA/github-sponsorship/reviewer.token" -Raw).Trim()
$reviewHeaders = @{ Authorization = "Bearer $reviewToken"; 'Idempotency-Key' = [guid]::NewGuid().ToString() }
Invoke-RestMethod http://127.0.0.1:8765/api/projects -Headers $reviewHeaders
Invoke-RestMethod http://127.0.0.1:8765/api/actions/review -Method Post -Headers $reviewHeaders -ContentType 'application/json' -Body (Get-Content examples/review-round1.json -Raw)
```

运行示例前必须把评审文件填写为真实项目证据。同一身份、同一路径和相同内容重试时复用原幂等键，返回原结果；同键不同内容返回 409。本地写入、事件与幂等结果在同一事务提交。外部调用前先持久化请求记录；崩溃后保留待核对状态，不能把不确定的外部执行当成未执行。

## 路由

| 方法与路径 | 内容 |
| --- | --- |
| GET /api/me | 当前身份与角色 |
| GET /api/status | 按权限过滤后的流程概况 |
| GET /api/projects | `q` 搜索，`page` / `page_size` 分页；每页 1–200 |
| GET /api/projects/{id} | 仓库与最新证据，附有权限查看的评审/维护者信息 |
| GET /api/records/{table} | 按权限分页查询 experiences、contacts、interviews、decisions、grants、followups、events、jobs、search_coverage |
| POST /api/actions/{action} | 操作手册中的业务动作；不开放通用 grant-reconcile |
| POST /api/import | `{"repos":["owner/repo"]}`，1–100 个仓库 |
| POST /api/schedule | 可传 `start` / `end`；发现查询仅使用服务端配置 |
| POST /api/jobs/{id}/retry | `{}`；仅重试 dead 任务 |
| POST /api/credits/{id}/plan | `{}`；预览已有批准赞助 |
| POST /api/credits/{id}/submit | `{"execute":false}` 预览；true 才执行，另需服务端开启和角色隔离 |
| POST /api/credits/{id}/reconcile | `{}`；只读提供方，核对后更新本地状态 |

401 表示凭证无效，403 表示权限不足，409 表示幂等冲突、租约失效或需对账；其他业务校验可能返回 400。不要仅根据 HTTP 状态码自动重试资金操作。服务不接受客户端指定 Credits 基址或服务器配置文件路径。

## 自有 Agent 协议

1. 专用 Agent 身份 POST `/api/agent/claim`，正文 `{}`。返回 `task`、`task_sha256`、`lease_token`、`lease_until`；暂无任务返回 `task: null`。一个身份同时只持有一个有效任务。新的轮询要使用新幂等键；网络重试保留旧键。
2. 执行器读取 task 中的 `id`、`sha`、`run_id`、spec 和证据快照，在自己的隔离环境运行真实产品。任务不自动准备完整源码、依赖或在线模型。
3. 到期前 POST `/api/agent/tasks/{id}/heartbeat`，正文含 `lease_token`，每次续租使用新幂等键。服务再次验证项目、SHA、完整快照和第一轮审核。
4. POST `/api/agent/tasks/{id}/result`，提交以下结构。原样回传服务端 task_sha256，不对重排后的 JSON 自行计算摘要。

```json
{
  "id": "EXPERIENCE_ID",
  "sha": "TASK_SHA",
  "run_id": "TASK_RUN_ID",
  "task_sha256": "SERVER_RETURNED_DIGEST",
  "lease_token": "SERVER_RETURNED_LEASE_TOKEN",
  "result": {
    "success": false,
    "failure_kind": "environment",
    "executor": "experience-agent",
    "environment": "填写实际执行节点与环境",
    "steps": ["填写真实尝试的执行步骤"],
    "actual_result": "填写真实执行情况和环境缺失原因",
    "artifacts": []
  }
}
```

实际结果字段以 task 的 `result_fields` 为准；上述为环境失败模板，须填写真实信息。成功要求 `failure_kind: none` 以及真实 artifacts；失败使用 product 或 environment。证据引用为相对 POSIX 路径，不能使用 URL、绝对路径或上跳路径。v0.3 提交结果前必须通过下述上传协议提交所有引用文件的真实内容；服务会核对当前 run 的完整文件，不能用路径字符串代替产物。

### 执行证据上传与下载

每个文件按 384 KiB 原始字节分块，再进行 base64 编码。POST `/api/agent/tasks/{id}/artifacts`，正文包含：

```json
{
  "lease_token": "CURRENT_LEASE_TOKEN",
  "run_id": "CURRENT_TASK_RUN_ID",
  "path": "evidence/output.txt",
  "sha256": "SHA256_OF_ENTIRE_FILE",
  "size": 123,
  "chunk_index": 0,
  "total_chunks": 1,
  "data_base64": "BASE64_OF_THIS_CHUNK"
}
```

chunk_index 从 0 开始；除最后一块外固定 384 KiB，空文件用一个空块。每个分块使用自己的稳定 Idempotency-Key，网络重试复用同一个键。只有完整文件大小与 SHA-256 核对通过才标记 completed；同一块不同内容会被拒绝。客户端须校验 ACK 的任务、run、路径、摘要、大小及分块标识。

配额按同一个 experience 跨 run 总计：最多 100 文件、64 MiB，包含未完成上传及历史 run 的保留文件。失效上传不会自动变为完整文件，也不会默默删除历史证据；配额用尽后由审核人创建新的体验任务。上传和结果提交都要求有效身份、租约、当前版本及审核。`agent-poll` 已实现分块、续租、ACK 校验和私有 outbox 恢复。

GET `/api/experiences/{id}/artifacts` 返回已完成文件列表，含 id、path、size、sha256、run_id、referenced 和 download_url。GET `/api/artifacts/{id}/download` 由有权限的人工审核/运营/审计身份读取；Agent 身份不能借此读取其他任务证据。

附件以 application/octet-stream、Content-Disposition: attachment、nosniff 返回，不能当作同源页面执行。数据库使用 BLOB 保存完整文件，写入和下载均按块处理；备份包含这些文件。管理后台在 Agent 体验“详情”中提供下载入口。下载并核对产物能支持审核，但不能证明第三方执行器诚实，第二轮仍需负责人复核。

过期或其他身份的租约、任务 SHA 变化、第一轮被重审、仓库归档、证据不完整都不能通过校验。新租约有新 run_id，旧结果不能冒用。executor 由服务端写为 Agent 身份。结果只完成体验记录，不能自动通过第二轮或触发联系。

## 运维和故障

另开受控进程运行 `run` 消费采集任务；API 不代替采集工作进程。`doctor` 做本地配置/数据库/队列检查，不访问外部服务，也不打印凭证。`tick --out` 输出 report.md 和 health.json，可由团队已有监控读取。

Credits 请求中断后，相同幂等键返回待对账冲突。用新的幂等键调用 reconcile 查询提供方。未知、提交中、等待领取状态不能手动改回可重试状态；详见 Credits 接入说明。升级前备份数据库，首次打开会将 v1/v2/v3 迁移至 v4；高于本程序支持版本的数据库拒绝打开。旧版本二进制不能用于已升级数据库。

## v0.4 待办与记录定位

`GET /api/inbox?kind=pending_experiences&page=1&page_size=15` 返回 `items, counts, kind, page, page_size, total`。分类由 `/api/ui-config` 的 `inbox_kinds` 给出；未传 kind 默认体验待办，运营身份应显式选择其授权分类。未知/空/重复参数返回 400，无权分类返回 403。page 为 1–100000，page_size 为 1–200。

分类：pending_experiences、running_experiences、stale_experiences（reviewer）；ready_for_contact、blocked_contacts（operator）；due_followups（operator/reviewer）；dead_jobs（operator/auditor）。admin 可读取所有分类。counts 只含授权类别，基于同一个数据库读取快照；接口不修改业务状态。

item 含 id、project_id（可空）、title、reason、href。href 是固定本地路由；具体记录由 `GET /api/records/{table}/{id}` 读取，沿用该表权限，未知 ID 返回 404；search_coverage 使用 job_id 作为路径中的记录标识。浏览器生成对应记录深链。`/api/status` 的待办字段使用相同角色权限。

`followup` 动作的 `result.next_review_at` 为可选的未来 ISO 日期。continue/adjust/pause 若省略则复用已有未来待办，没有时拒绝；返回 next_followup_id 和 next_review_at。end 返回两者为 null 并取消剩余待办。日期不会自动修改 Credits 远端额度或活动期限。
