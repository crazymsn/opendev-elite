# NextRouter PM26.10.01 第一周开发验收

验收日期：2026-10-27（Asia/Shanghai）  
验收对象：`GitHub_Worker` 当前代码与 `https://ee06e589.opendeploy.site/` 部署公开契约  
对应周次：第 1 周（UID 和数据模型，原计划 2026-10-19–2026-10-25）

## 验收结论

第一周开发任务的代码能力已完成，专项验收 **7/7 通过**。本地完整回归 **380 项测试通过，0 失败**；Python 源码编译通过。线上公开服务健康检查、认证配置和 OpenAPI 契约均通过。

第一周完成项：

- 新项目自动生成稳定 UID，格式为 `prj_` 加 32 位小写十六进制哈希。
- UID 由 GitHub 数字项目 ID 稳定派生，重复导入和仓库改名场景不会改变 UID。
- 旧 schema 数据库迁移时自动补齐 UID，schema 当前迁移到 9。
- 项目默认状态为 `discovered`，现有项目阶段字段可继续承载后续流程状态。
- 重复项目导入通过 GitHub 数字 ID 幂等更新，避免生成重复项目和重复来源记录。
- 榜单排名保存项目、榜单、周期、语言、排名、权重、来源 URL、证据和采集时间。
- 榜单权重规则已验证：第 1 名 15 分，第 2–5 名 12 分，第 6–10 名 9 分，其余有效上榜 5 分。
- 同一项目、榜单、周期和语言重复写入时更新既有记录，不生成重复排名。
- 项目列表已返回 UID、状态、当前榜单排名、榜单权重和运营优先级分数。

## 自动化验收证据

机器可读报告：[`week1-acceptance.json`](../artifacts/validation/week1-acceptance.json)

运行命令：

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/validation/week1_acceptance.py --out artifacts/validation/week1-acceptance.json
```

报告结果：

```json
{"passed": 7, "failed": 0, "blocked": 0}
```

专项检查：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| UID 格式与稳定派生 | 通过 | `prj_de71e247475d7d9e09413c4d6a7cb647` |
| 旧库 UID 回填 | 通过 | legacy schema 1 → schema 9 |
| 重复导入幂等 | 通过 | 项目 1 条、来源 1 条、UID 保持不变 |
| 默认项目状态 | 通过 | `discovered` |
| 榜单权重 | 通过 | 1/3/7/11 名分别为 15/12/9/5 |
| 榜单周期幂等 | 通过 | 更新后保持单条记录，排名 4、权重 12 |
| 项目列表字段 | 通过 | UID、stage、ranking_rank、ranking_weight、contact_class、contact_priority、priority_score 均存在；优先级 115 |

## 回归验证

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m unittest discover -s tests -q
python -m compileall -q worker main.py
```

结果：

- Python 单元测试：**380 项通过，0 失败**。
- Python 编译检查：**通过**。
- 专项验收测试：**5 项通过，0 失败**。
- 专项机器报告：**7 项通过，0 失败**；标准模式与 `python -O` 优化模式结果一致。

## 部署公开契约验证

验证地址：`https://ee06e589.opendeploy.site/`

| 接口 | 结果 | 说明 |
| --- | --- | --- |
| `/healthz` | 通过 | 返回 `{"ok":true}`，HTTP 200 |
| `/auth/options` | 通过 | 密码登录开启；公开注册关闭；无需 bootstrap |
| `/openapi.json` | 通过 | 包含项目列表、项目详情、项目榜单、发现运行和状态接口 |

已确认的公开 API 契约包括：

- `/api/projects`
- `/api/projects/{id}`
- `/api/projects/{id}/rankings`
- `/api/discovery-runs`
- `/api/status`

## 线上项目数据验收

机器可读证据：[`week1-online-acceptance-20261027.json`](../artifacts/validation/week1-online-acceptance-20261027.json)

已在认证后的线上工作台分页读取 `/api/projects?page_size=200`，共 30 页：

- 报告总数：**5,849**；实际拉取：**5,849**，数量一致。
- UID 缺失：**0**；UID 格式错误：**0**；UID 完整率：**100%**。
- 唯一 UID：**5,849**；重复 UID：**0**。
- 唯一 GitHub 数字项目 ID：**5,849**；重复项目 ID：**0**。
- 项目状态：`technical_review` **1,131** 条；`discovered` **4,718** 条。
- 榜单排名：有排名 **0** 条；无排名 **5,849** 条。

这次线上核对证明当前部署中的项目记录已完成 UID 覆盖且没有重复 UID/项目 ID。线上当前没有榜单排名数据，属于需要进入后续采集/榜单运行验证的业务数据缺口，不影响 UID 和数据模型验收。

## 证据边界

本地自动化验收使用独立临时 SQLite 数据库，未使用真实 GitHub Token，未抓取真实作者资料，未发送联系消息，也未执行 Credits 写入。线上验收使用认证后的部署工作台，未将账号密码写入证据文件。

本次线上数据核对以当前部署 API 返回的 5,849 条记录为准；它证明当前部署数据状态，不等同于外部历史源中的原始约 930 条项目数量。当前线上实际项目量为 5,849 条。

## 下一周交接条件

第一周已经可以进入第 2 周的爬虫问题排查。线上登录核对已完成，当前部署项目数据为 5,849 条，UID 完整且无重复；榜单排名当前为空，应作为第 2 周采集/榜单运行的首项验证。
