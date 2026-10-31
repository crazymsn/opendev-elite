# NextRouter-PM26.10.01：开源项目赞助系统开发方案

版本：v1.0（方案基线）  
依据：2026-10-01 会议智能纪要、当前 `GitHub_Worker` 源码与已有验收约束  
目标：把会议决定拆成可排期、可开发、可验收的系统增量，不把 AI 判断、榜单排名或采集数量直接当作赞助决定。

## 1. 会议结论与工程解释

会议确定了六项产品方向：

1. 早期优先发现高质量、尚未形成知名度的项目，阶段一以 GitHub 官方 Trending 等榜单为主要数据源，并支持自定义起止日期。
2. 联系作者时采用“同道中人交流”路径，先技术、产业和认知交流，再讨论赞助；作者评估和信任建立不能被自动化流程省略。
3. 联系线索只从项目 README、作者 GitHub 公开主页、项目 Issue 问答三个高权重位置采集；没有公开线索的项目后置，高价值例外必须走人工审批。
4. 为项目增加稳定 UID、榜单排名和权重；AI 增加标签、分维度评分、项目全景分析、优缺点和商业价值结论。
5. 已赞助作者获得受限的内推和社区协作能力，后续建设激励、归属感和生态裂变。
6. 解决 GitHub 偶发限流和单任务串行瓶颈，并由 AlexX 对接 Ethan 团队、甄诚和 OP 协同完成爬虫迭代。

会议稿的 AI 免责声明属于资料可信度提示，不是开发指令。任何 AI 结论都必须保留来源、版本、观察时间和人工复核状态。

## 2. 当前实现基线与差距

当前代码已经提供以下可复用能力：

- `worker/db.py` 使用 SQLite schema 9，项目主键仍是 GitHub 数字仓库 ID，同时有稳定的业务 UID；已有快照、来源、作者候选、评审、体验、联系、事件和持久化任务队列。
- `worker/pipeline.py` 已有 `schedule/search/refresh/work`，支持日期范围、搜索拆分、分页、公开仓库校验、快照和失败重试。
- `worker/contact_points.py` 已从 GitHub 作者公开主页、README、Issue 正文/有限评论和导入 CSV 生成带来源的联系方式，并写入 `with_clue/without_clue/do_not_contact` 分类与优先级。
- `worker/report.py` 已统计已采集、已筛选、入选、淘汰、暂缓、待筛选、联系方式、队列和覆盖情况；统计是当前快照，不代表历史转化率。
- `worker/service.py` 已提供 `/api/status`、`/api/projects`、`/api/schedule`、联系方式发现和角色权限；`worker/web/app.js` 已有工作概览、项目列表、任务和联系方式页面。
- 现有业务硬门槛仍有效：五项质量维度总分至少 80、每项至少 3/4；两轮评审和体验证据通过后才能联系；系统只保存联系草稿和人工实际动作，不自动发送消息。

主要缺口：

| 缺口 | 影响 | 方案归属 |
| --- | --- | --- |
| 没有业务 UID | 改名、跨来源和外部协作时只能依赖 GitHub 数字 ID | 已在 M1 完成 |
| `sources` 只有文本来源 | 无法保存榜单名次、周期、排名证据及历史变化 | M1 |
| 发现配置只有 GitHub 搜索 | 不能把 Trending 作为可审计的一等数据源 | M1 |
| 联系抽取没有 Issue 问答 | 会议要求的第三个高权重渠道未覆盖 | 已在 M1 完成 |
| 没有线索分类与优先级 | 运营无法快速区分先联系和后置项目 | 已在 M1 完成 |
| 没有 AI 评估实体 | AI 结论无法与快照、提示词版本和人工结论绑定 | M2 |
| 队列有租约但没有共享速率闸门 | 多进程扩展后仍可能触发 GitHub 限流 | 已在 M1 完成 |
| 作者、推荐人和激励没有受限模型 | 无法安全开放内推和子管理员协作 | M3 |

## 3. 目标业务流程

```text
创建采集运行
  -> Trending/API 搜索与来源记录
  -> 项目去重、生成 UID、自动分配负责人
  -> GitHub 快照（README、代码采样、维护证据、Issue 问答、作者主页）
  -> 联系线索抽取与分类（有线索 / 无线索 / 禁止联系）
  -> AI 标签、评分、全景分析（只作建议）
  -> 人工入选评估（现有 80 分门槛）
  -> 第一轮技术复核 -> 真实体验 -> 第二轮复核
  -> 维护者确认 -> 同道交流草稿 -> 人工记录联系和回复
  -> 访谈、赞助审批、独立发放、复盘
```

排序只影响待办顺序，不改变入选门槛。联系方式有线索只影响联络优先级，不代表作者同意联系。AI 的“高价值”只允许把无线索项目放入人工例外队列，不能自动触达作者。

## 4. 数据模型和迁移

### 4.1 项目 UID

在 `projects` 增加：

```sql
uid TEXT NOT NULL UNIQUE;
uid_version INTEGER NOT NULL DEFAULT 1;
```

- 新项目生成 `prj_` 加 32 位小写随机值；UID 永不因仓库改名、迁移或刷新而变化。
- 既有项目迁移时使用 `prj_` 加 `sha256("github:" || projects.id)` 的前 32 位，保证重复迁移结果稳定。
- GitHub 数字 ID、`full_name` 和 UID 都保留；内部兼容接口继续接受数字 ID，新增接口优先返回 UID。
- 增加 `GET /api/projects/{uid}` 的兼容路由；旧 `/api/projects/{id}` 不删除。

### 4.2 采集运行与来源

新增：

```sql
CREATE TABLE discovery_runs(
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,                 -- github_search / github_trending / manual
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  config TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued', -- queued/running/partial/done/failed
  requested_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  counts TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
```

现有 `sources` 继续保存项目来源字符串；新增 `project_rankings` 保存可审计排名：

```sql
CREATE TABLE project_rankings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  run_id TEXT NOT NULL REFERENCES discovery_runs(id),
  board TEXT NOT NULL,                  -- github_trending 等
  period TEXT NOT NULL,                 -- 日/周/月或具体日期
  language TEXT,
  rank INTEGER NOT NULL CHECK(rank > 0),
  rank_delta INTEGER,
  weight REAL NOT NULL DEFAULT 0,
  source_url TEXT NOT NULL,
  evidence TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  UNIQUE(project_id, board, period, language)
);
CREATE INDEX project_rankings_latest
  ON project_rankings(project_id, board, captured_at DESC);
```

权重只用于排序建议。建议初始计算：第一名 15 分、第二至五名 12 分、六至十名 9 分、其余有效上榜 5 分；同一项目多个榜单取最高值并记录来源。不得用 Stars、国籍、地域或榜单权重单独淘汰项目。

### 4.3 联系线索分类

在现有 `contact_points` 结构上新增来源细节字段，避免把线索值塞入自由文本：

```sql
ALTER TABLE contact_points ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'other';
ALTER TABLE contact_points ADD COLUMN source_ref TEXT;
ALTER TABLE contact_points ADD COLUMN explicit INTEGER NOT NULL DEFAULT 1;
```

新增项目级扫描结果：

```sql
CREATE TABLE contact_summaries(
  project_id INTEGER NOT NULL REFERENCES projects(id),
  snapshot_id INTEGER REFERENCES snapshots(id),
  class TEXT NOT NULL,                   -- with_clue / without_clue / do_not_contact
  source_mask TEXT NOT NULL,             -- readme/profile/issues/csv
  point_count INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,
  rationale TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(project_id, snapshot_id)
);
```

分类规则：

- `with_clue`：至少一条有效、来源明确的 README、作者公开主页或 Issue 问答线索。
- `without_clue`：三个高权重渠道均无有效线索，或只有不能核验的模糊文本。
- `do_not_contact`：维护者明确拒绝、项目设置禁止联系或运营标记限制；此类永远不能被优先级覆盖。
- CSV 线索标记为 `csv` 和 `unverified`，只能作为待核验提示，不自动确认作者归属。

### 4.4 AI 评估

新增不可变评估表：

```sql
CREATE TABLE ai_assessments(
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,                  -- queued/running/completed/failed/stale
  tags TEXT NOT NULL DEFAULT '[]',
  dimensions TEXT NOT NULL DEFAULT '{}',
  total_score REAL,
  tier TEXT,                             -- A/B/C/unknown
  strengths TEXT NOT NULL DEFAULT '[]',
  weaknesses TEXT NOT NULL DEFAULT '[]',
  business_value TEXT,
  contactability TEXT,
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  uncertainty TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT
);
```

AI 输出必须是 JSON Schema 校验后的结构；每个分数和结论至少绑定一个快照证据引用。快照 SHA 变化时旧评估标记 `stale`，禁止继续作为当前项目结论。模型请求、原始提示词和作者联系方式应分离，默认只向模型发送项目公开资料摘要及证据定位，不发送 CSV 原始邮箱。

### 4.5 内推、子管理员与激励（M3）

新增 `referral_links`、`referrals`、`community_memberships`、`incentive_ledger` 四表。链接数据库只存哈希，访问令牌只在创建时显示一次；推荐归因以首次有效提交为准，防重复和自荐循环。子管理员权限必须绑定 `member_id + scope`，只允许查看和处理被授权项目，不能读取全库联系方式、审批赞助或发放 Credits。激励先记账、人工审核后结算，任何外部支付不在本次系统自动化范围内。

## 5. 采集与并发限流架构

### 5.1 任务类型

扩展现有 `jobs`：

| 类型 | 责任 | 幂等键 |
| --- | --- | --- |
| `discovery` | 执行一次来源查询或榜单抓取 | `discovery:{run_id}:{page}` |
| `refresh` | 获取仓库快照 | `refresh:{project_id}:{sha_or_run}` |
| `contact_extract` | 解析 README、主页、Issue 问答 | `contact:{project_id}:{snapshot_id}` |
| `ai_assess` | 调用 AI 并保存结构化评估 | `ai:{project_id}:{snapshot_id}:{prompt_version}` |
| `assignment` | 按规则分配负责人 | `assign:{project_id}:{run_id}` |

单个任务仍由数据库租约保护；多个 worker 可并行消费，但同一项目的 `refresh -> contact_extract -> ai_assess` 必须按依赖顺序执行。

### 5.2 共享速率闸门

新增 `provider_limits` 表或等价的 SQLite 原子记录，按 `provider + token_fingerprint` 保存：剩余请求数、重置时间、最近退避时间、并发上限。每次 GitHub 请求前在事务内领取一个许可；响应后用 `X-RateLimit-Remaining`、`X-RateLimit-Reset` 和 `Retry-After` 校正。令牌明文不写库，只记录不可逆指纹。

重试策略：

- 401、权限配置错误：立即失败并进入待处理，不重试。
- 403 且明确 rate limit、429、502、503、504：指数退避 `30s * 2^(attempt-1)`，上限 1 小时，并尊重 `Retry-After`。
- 404、422：记录组件级缺失；只在上游明确暂时不可用时重试。
- 搜索 `incomplete_results`、分页链接缺失或结果超过 1000：继续拆分并标记 `partial`，禁止宣称全量覆盖。

初始并发建议：GitHub API 每个 token 2 个并发，单个项目最多 1 个 refresh；`contact_extract` 和 `ai_assess` 各 2 个并发。连续 24 小时观测后再按剩余配额和错误率调参。worker 启动时生成 `worker_id`，任务和日志带上它，方便定位 Ethan 团队并发优化造成的重复或饥饿。

当前实现：`Pipeline.work(max_jobs, workers=1..8)` 使用线程池和每 worker 独立 SQLite 连接，任务租约仍由数据库原子领取；生产模板 `config/discovery.json` 默认 `workers=2`。`GitHub` 在 `provider_limits` 中原子维护 `in_flight`、`next_allowed_at`、剩余配额和重置时间，跨进程共享每令牌并发上限和限流退避。单项目任务仍按 `refresh -> contact_extract -> ai_assess` 入队依赖顺序执行。worker 数量不改变人工评审、联系同意或赞助审批门槛。

### 5.3 Trending 适配器

实现 `TrendingProvider` 接口，首版只支持 GitHub 官方 Trending 的公开页面/官方允许接口：

- 语言、周期（日/周/月）、日期范围均为配置项。
- 保存抓取 URL、响应摘要/哈希、解析器版本、页面时间和名次；解析失败为 `partial`，不生成伪排名。
- 使用 ETag/Last-Modified 和本地 `http_cache`，低频抓取，遵守 robots、服务条款和速率限制；不绕过登录、验证码或访问控制。
- 若官方来源在部署环境不可稳定读取，保留 `github_search` 作为明确标注的降级来源，页面显示“搜索近似，不等同 Trending”。

### 5.4 自定义采集任务

`POST /api/schedule` 向后兼容 `start/end`，新增可选：

```json
{
  "source": "github_trending",
  "start": "2026-10-01",
  "end": "2026-10-02",
  "period": "daily",
  "languages": ["Python", "TypeScript"],
  "max_projects": 200,
  "auto_assign": true
}
```

接口先创建 `discovery_runs`，返回 `run_id`、任务数、来源和预计范围；任务完成后自动将项目置于待分配/待筛选池，不自动入选或联系作者。`GET /api/discovery-runs/{id}` 返回运行状态、成功/部分/失败数量、限流等待和覆盖说明。

## 6. 联系方式抽取与作者接洽

### 6.1 三个高权重来源

1. **README**：只识别显式 `mailto:`、公开邮箱、明确标注的官网、社区、Discord、Discussions、Matrix 等链接；忽略示例域名和普通代码文本。
2. **作者 GitHub 公开主页**：公开邮箱、个人主页、公开社媒用户名和 GitHub 主页；保留抓取时的公开状态和 URL。
3. **Issue 问答**：只采集项目 Issue 正文和有限数量的公开评论；识别作者主动留下的联系方式或明确的社区入口，记录 Issue 编号、评论 URL、作者登录名和快照 SHA。

明确禁止：从 commit author 邮箱推断联系方式、用户名猜邮箱、购买数据、访问登录后页面、绕过 robots/验证码、批量抓取非公开个人信息。高价值无线索例外必须经过人工批准，并只允许合规公开来源，记录理由、来源和停止条件。

### 6.2 线索优先级

建议 `priority`：

- 有作者明确邮箱或作者主页入口：100。
- 有 README/Issue 社区入口但无直接邮箱：80。
- 只有项目官网或组织入口：60。
- 无线索但 AI 评估为高价值：人工例外队列 40，不自动联系。
- `do_not_contact`：0，且从所有待办中排除。

优先级只决定列表默认顺序。运营人员仍需确认维护关系、联系人身份、是否愿意交流，并使用“技术/产业/认知交流 -> 了解需求 -> 说明赞助”的手工接洽 SOP。系统只生成草稿、记录实际执行和回复，不发送邮件或社媒消息。

### 6.3 接洽记录字段

在现有 `contacts` 和 `interviews` 上增加结构化字段：接触目标、交流阶段、作者自述、匹配度、信任风险、作者明确需求、同意状态、下一步、证据 URL。每次状态变更写入 `events`，禁止用一条自由文本覆盖历史。

## 7. AI 标签、评分和全景分析

### 7.1 输出契约

首版标签建议：`agent-infra`、`ai-coding`、`rag`、`routing`、`inference`、`developer-tool`、`application`、`early-stage`、`maintained`、`needs-evidence`。标签必须带置信度和证据引用。

AI 评估维度沿用现有五项标准，避免出现两套互相冲突的门槛：

| 维度 | 权重 | AI 输出 | 人工最终结论 |
| --- | ---: | --- | --- |
| 真实需求与任务价值 | 20% | 场景、用户、收益、未知项 | 现有入选评估 |
| 工程成熟度 | 25% | 模块、测试、失败处理证据 | 第一轮技术复核 |
| 维护连续性 | 20% | 两个不同日期和来源 | 现有维护证据核验 |
| 差异化与新增贡献 | 20% | 替代方案、优势、限制 | 第二轮体验与人工判断 |
| 可评测性与边界 | 15% | 安装、输入输出、成本、权限 | 体验计划与审核 |

项目全景分析固定包含：一句话摘要、目标用户、核心流程、优点、缺点、风险、替代方案、商业/生态价值、赞助可能解除的瓶颈、联系建议、缺失证据和“不能据此判断”的限制。

### 7.2 运行和人工校验

- 快照完成后异步入队，AI 服务超时、限额或 JSON 校验失败进入 `failed`，可人工重试；不阻塞项目采集。
- 输出必须通过 JSON Schema、数值范围、标签枚举和证据引用校验；不合格结果只保存错误，不显示为评估结论。
- 列表展示“AI 建议”徽标、模型、提示词版本、生成时间和过期状态；人工评估表单保留现有权限与门槛。
- AI 评分不得自动把项目标为入选、淘汰、可联系或可赞助；仅可生成待办、排序建议和待核查事项。

## 8. Web、API 和统计改造

### 8.1 项目列表

在 `worker/web/app.js` 项目表增加：UID、来源/榜单、当前名次、排名权重、AI 层级、联系方式分类、联络优先级、负责人。增加筛选：来源、榜单周期、`with_clue/without_clue/do_not_contact`、AI 标签、AI 评估状态；保留按名称、更新时间和 Stars 排序，并新增“运营优先级”。

### 8.2 概览统计

现有统计保留并新增：

- 已采集项目总数（唯一项目 UID 数）。
- 已筛选、入选、淘汰、暂缓、待筛选数量。
- 有联系线索、无线索、禁止联系数量。
- 上榜项目数、各榜单覆盖和部分覆盖数。
- AI 已完成、失败、过期数量。
- 当前可领取、限流等待、运行中、死信任务数量。

每个数字都附统计口径和生成时间；“已淘汰”只计当前最新正式评估为 `exclude` 的项目，不把未评估或无线索计为淘汰。

### 8.3 API

新增或扩展：

| 接口 | 用途 | 权限 |
| --- | --- | --- |
| `GET /api/discovery-runs` | 采集运行与覆盖 | operator/auditor |
| `GET /api/discovery-runs/{id}` | 单次运行详情 | operator/auditor |
| `POST /api/discovery-runs` | 创建 Trending/API 运行 | operator |
| `GET /api/projects/{uid}/rankings` | 榜单历史与证据 | 现有项目读取者 |
| `GET /api/projects/{uid}/contacts/summary` | 联系分类与优先级 | operator/reviewer |
| `POST /api/projects/{uid}/contacts/rescan` | 重新抽取三类来源 | operator |
| `GET /api/projects/{uid}/ai-assessments` | AI 历史和当前状态 | reviewer/operator/auditor |
| `POST /api/projects/{uid}/ai-assess` | 明确触发 AI 重评 | reviewer/operator |
| `GET /api/metrics` | 面向监控的只读指标 | auditor/admin |
| `POST /api/referrals` | 创建内推链接/记录推荐 | 受限 operator/sub_admin |

所有新写接口沿用同源、CSRF、角色、幂等键和事件审计规则；不增加跨域 CORS，不把作者原始线索返回给无权角色。

## 9. 权限、隐私与安全

- 继续保留 reviewer、operator、approver、disburser、agent、auditor、admin 的职责分离；新增 `sub_admin` 必须是项目范围权限，不能调用账户、Credits 或全库导出接口。
- 联系线索按最小权限返回；AI 服务不默认接收邮箱和社媒值，只接收“有/无线索”和证据定位。
- 公开页面不展示真实候选和作者资料；演示数据库、生产数据库、备份和凭证目录继续分离。
- 记录每次线索采集的来源、时间、版本、公开性和删除/拒绝联系状态；作者拒绝后全局屏蔽同一维护者的主动联系。
- 任何“特殊信息挖掘”先通过合规检查清单：来源公开、服务条款允许、无登录绕过、无数据购买、无身份猜测、可提供停止/删除路径；不满足即不执行。

## 10. 分阶段实施与交付物

按 1 名后端、1 名采集、1 名前端、0.5 名质控并行估算；实际日期由团队排班决定。

### M0：规格冻结与迁移演练（2 人日）

- 冻结 UID、榜单来源、线索分类、AI JSON Schema 和合规边界。
- 在独立复制库执行 schema 8 迁移、回滚和备份恢复演练。
- 为 930 个既有项目生成 UID，核对数量、项目 ID、快照和联系记录无损。
- 交付：迁移脚本、字段字典、OpenAPI 更新、验收矩阵。

### M1：会议五项 action item MVP（8-12 人日）

- `project_uid` 与历史回填。
- `discovery_runs`、`project_rankings` 和 Trending 适配器。
- `/api/schedule` 扩展 source/period/language/max_projects，运行详情和 UI 状态。
- README、作者主页、Issue 问答三源抽取；线索分类、优先级和项目详情展示。
- 共享 GitHub 速率闸门、Retry-After、指数退避、worker_id、限流指标。
- 自动分配到待处理池；分配失败进入待分配待办，不静默丢失。
- 交付：M1 生产迁移、API、页面、单元/集成/浏览器测试。

### M2：AI 评估和全景分析（7-10 人日）

- AI provider 抽象、配置、预算、超时和脱敏。
- `ai_assessments`、异步任务、JSON Schema 校验、证据引用和过期机制。
- 标签、五维建议分、优缺点、商业价值和缺失证据卡片。
- AI 评估状态、重试和人工复核入口；不改变现有入选门槛。
- 交付：提示词版本库、模型回归样本、成本报表、人工抽检结果。

### M3：社区内推与受限协作（8-12 人日）

- 推荐链接、首次归因、防重复、撤销和审计。
- 已赞助作者的项目级 `sub_admin`，最小权限和到期机制。
- 推荐项目、有效线索、有效接洽、落地结果的激励台账与人工审批。
- 社区身份、项目里程碑、贡献历史展示；不做自动付款或自动发信。
- 交付：权限矩阵、内推 SOP、激励规则版本、隐私和删除流程。

### M4：真实试运行与 OpenDeploy 发布（5-7 人日）

- 以 20 个候选、至少 3 个方向、含人用和 Agent 项目进行灰度。
- 采集 24 小时限流/队列观测，验证并发没有重复快照、任务饥饿或错误放大。
- 完成数据库迁移、健康检查、备份恢复、日志和指标。
- 执行真实浏览器验收：创建采集任务、查看运行、项目排序、线索分类、AI 抽检、权限隔离。
- OpenDeploy 先部署到预览服务，健康检查和回滚包通过后再切生产；生产 URL、版本、部署 ID 和验收记录落库。

## 11. 测试与验收标准

### 单元与迁移

- UID 生成幂等、仓库改名不变、重复导入不重复。
- 榜单重复周期、语言、排名变更和部分抓取正确写入；权重不覆盖排除条件。
- README、主页、Issue 的邮箱/链接提取过滤示例、noreply、重复和恶意 URL。
- AI JSON Schema、证据引用、快照过期、超时重试和成本上限。
- schema 7 -> 8（或实际版本）升级、回滚、备份恢复和既有事件数量一致。

### 采集与并发

- 模拟 403/429/Retry-After/502/分页缺失，验证退避、死信、人工重试和 `partial` 标记。
- 两个 worker 同时领取同类任务，验证唯一快照、唯一联系方式和幂等事件。
- GitHub 配额耗尽时新任务进入 delayed，不忙等、不清空队列、不误报“安排失败”。
- 24 小时压力样本：成功率、平均延迟、限流次数、重复请求率、队列积压和每项目请求数均有指标。

### 业务与浏览器

- “安排发现任务”创建运行并显示真实状态；项目自动进入待分配池。
- 930 个历史项目可按 UID、榜单、AI 标签、线索分类和优先级查询。
- 无线索项目不能自动生成外部消息；特殊例外必须人工理由和审计事件。
- 未完成两轮审核的项目仍无法联系；AI 高分和 Trending 排名不能绕过门槛。
- 已拒绝联系的作者在同项目和跨项目均不可再次生成联系草稿。
- reviewer/operator/sub_admin/auditor 只能看到授权数据；Agent 不得读取作者线索或社区后台。

### 上线门槛

1. 全量 Python 单测和新模块集成测试通过。
2. 关键流程浏览器回归通过，含桌面、平板和键盘操作。
3. 独立生产数据库迁移和备份恢复成功；无 demo、fixture 或真实凭证泄漏。
4. GitHub 采集至少完成一轮真实 Trending/API 运行，保存仓库 ID、UID、SHA、来源、排名和覆盖状态。
5. AI 输出至少人工抽检 20 个项目，错误/不确定结论全部能追溯并可标记过期。
6. 生产部署后 `/healthz`、`/openapi.json`、登录、队列、任务状态和回滚均实测；不把“任务已入队”当作“采集已完成”。

## 12. 风险、取舍与待决策项

| 项目 | 风险 | 默认决策 |
| --- | --- | --- |
| GitHub Trending 来源 | 页面结构或服务条款变化，可能无法稳定解析 | 接口隔离、低频缓存、失败标记 partial，不伪造排名 |
| AI 成本 | 对 930+ 项目全量分析成本不可控 | 先对入选候选/高优先级项目异步评估，配置日预算和并发上限 |
| 多 worker | SQLite 写竞争与重复请求 | 先共享租约/闸门小并发，观测后再扩容；不直接上无界线程池 |
| 特殊信息挖掘 | 侵犯隐私或违反平台条款 | 仅公开合规来源，人工批准；不做猜测、购买或绕过访问控制 |
| 子管理员 | 误读全库作者信息或越权审批 | 项目范围、到期、审计；默认只读，逐项授权写操作 |
| 激励 | 诱发刷推荐、垃圾线索或不当接触 | 首次有效归因、人工核验、版本化规则、只记账不自动结算 |
| 开源中国合作 | 外部合作尚未确定 | 预留 `source=opencn` 适配器，不提前依赖或伪造全量能力 |

在 M0 评审并确认：AI 服务提供方和预算、Trending 允许的抓取方式、标签词典、子管理员范围、激励规则、Issue 评论抓取上限，以及真实生产数据库和 OpenDeploy 发布窗口。

## 13. 责任分工

- **AlexX（徐长波）**：总体架构、迁移、API、OpenDeploy 发布、与 Ethan 团队对接。
- **Ethan（赵毅飞）团队**：GitHub provider、限流闸门、并发 worker、Trending 适配器和采集压测。
- **甄诚、OP**：爬虫模块联调、来源解析、异常样本和运行监控。
- **OpenLab 核心团队**：负责人分配、AI 结果抽检、作者接洽、访谈、赞助和社区 SOP。
- **质控/审计角色**：权限、隐私、迁移、备份、浏览器回归和生产验收签署。

## 14. 方案完成定义

当 M1 完成时，系统应能对指定日期范围创建有来源的采集运行，给每个项目生成稳定 UID，记录榜单名次，完成三源联系方式分类，按优先级展示并在限流下可靠执行；当 M2 完成时，AI 评估可追溯、可过期、可人工复核；当 M3 完成时，内推和受限协作可审计；M4 完成后才允许把该版本作为 OpenDeploy 生产版本。任何阶段都不能用采集量、AI 分数或榜单排名替代人工评估、作者同意和独立赞助审批。
