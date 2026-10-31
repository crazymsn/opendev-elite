# NextRouter PM26.10.01 实施审计与验收

审计日期：2026-10-16（Asia/Shanghai）  
审计对象：`GitHub_Worker` 当前工作区  
依据材料：2026-10-01 会议智能纪要的 Markdown 与 PDF；会议稿中的 AI 生成免责声明、链接和行动项只作为被审阅资料，不作为额外操作授权。

## 结论

会议要求的五项开发行动已经在当前代码中形成可运行闭环，并补齐了原先“任务单线程执行”的工程缺口：

| 会议行动 | 当前实现 | 验证结果 |
| --- | --- | --- |
| 榜单排名栏目与权重 | `discovery_runs`、`project_rankings`、Trending 解析器、项目优先级排序和详情/API | 通过 |
| README、作者主页、Issue 三源线索分类 | `contact_points` 保留来源、URL、快照和显式标记；`contact_summaries` 输出 `with_clue/without_clue/do_not_contact` 与优先级 | 通过 |
| GitHub 限流排查 | `Retry-After`、`X-RateLimit-*`、指数退避、ETag 缓存、限流时队列延迟 | 通过 |
| 爬虫并发优化 | `Pipeline.work(max_jobs, workers=1..8)`；每 worker 独立 SQLite 连接，任务租约原子领取 | 通过 |
| 项目唯一 UID | `prj_` + 32 位稳定哈希；旧数字 ID 兼容，仓库改名不变 | 通过 |

当前版本可作为本地/预发布验收版本。外部 GitHub、真实作者联系、Credits 写入和生产域名部署仍需由具备相应凭证与权限的运营人员单独执行；本审计不把合成数据、历史部署记录或“任务已入队”当作真实业务完成。

## 设计与实现审计

1. **数据一致性**：SQLite schema 当前为 9；项目 UID 在迁移和重复导入时保持稳定。榜单排名按项目、榜单、周期和语言幂等写入，保留来源 URL、证据和采集时间。
2. **采集可靠性**：搜索结果只接受明确的公开仓库；分页缺失、`incomplete_results` 和超过 1000 条结果会标记 `partial`，不会虚报全量覆盖。Trending 解析失败会失败/部分完成，不生成伪排名。
3. **并发安全**：并发 worker 不共享 SQLite 连接；每个任务继续使用 lease token，过期 lease 不能提交结果。GitHub 访问在 `provider_limits` 中按 token 指纹协调请求间隔、`in_flight`、剩余配额、重置时间和限流退避。
4. **线索合规**：只识别项目 README、作者公开主页以及项目 Issue 正文/有限公开评论中的显式入口；过滤示例域名、noreply 和不安全 URL，不从 commit 邮箱猜测联系人，也不发送外部消息。CSV 线索标为未核验提示。
5. **业务门槛**：榜单排名、AI 评分和联系方式优先级只影响排序与待办，不绕过人工入选、两轮评审、作者同意、独立审批和 Credits 对账。
6. **权限与审计**：项目详情、榜单、AI 评估和联系方式按角色裁剪；写接口沿用同源、CSRF、幂等键和事件审计约束。

## 自动化验收证据

- `py -3 -m unittest discover -s tests -q`：**375 项通过，0 失败**。
- 重点回归：`tests.test_nextrouter_features`、`tests.test_pipeline`、`tests.test_crawler_hardening`：**30 项通过**，包含两个 refresh 任务的真实并发重叠、UID/榜单/线索幂等和限流退避。
- `py -3 -m compileall -q worker main.py`：通过。
- 前端 `node --check`：`app.js`、`forms.js`、`views.js`、`developers.js`、`accounts.js`、`landing.js`、`i18n.js`、`i18n.en.js` 全部通过。
- CLI `main.py work --help` 已公开 `--workers`，范围约束为 1–8；发现配置模板默认 `workers: 2`。
- 会议 PDF 共 3 页，已逐页渲染检查；文字结构与 Markdown 关键行动项一致，未发现截断、重叠或不可读区域。
- 离线验收输出保存在 [`acceptance-20261016.json`](../artifacts/validation/acceptance-20261016.json)；它明确标记外部配置与真实证据缺失，状态为 `blocked`，不是生产通过证明。

## 可复核的运行方式

```powershell
# 仅在独立数据库中执行；不要把运行库放在 OneDrive 同步目录
python main.py --db C:\path\worker.db schedule --config config/discovery.json --start 2026-10-01 --end 2026-10-02
python main.py --db C:\path\worker.db work --max-jobs 40 --workers 2
python main.py --db C:\path\worker.db report --out artifacts/report.md
python main.py --db C:\path\worker.db acceptance --out artifacts/validation/acceptance-20261016.json
```

生产启用 GitHub token 前，应先用 `doctor` 检查配置，观察 24 小时的成功率、限流次数、队列积压和重复快照，再决定是否把 `workers` 调高。当前实现将上限固定为 8，避免无界线程池放大 API 压力。

## 未宣称完成的外部事项

- 没有在本次审计中使用真实 GitHub token 或抓取真实作者资料。
- 没有自动发送邮件、社媒消息或作者私信。
- 没有执行 Credits 真实写入，也没有把历史 OpenDeploy URL 当作本次部署证据。
- 开源中国全量合作、真实 930 项历史库迁移和 20 个真实候选的 AI 人工抽检，需要运营方提供目标数据库、凭证和验收窗口后单独执行。
