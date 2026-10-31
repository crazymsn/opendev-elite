# v0.8.0 验证记录

| 记录 | 范围 |
| --- | --- |
| [tests.txt](tests.txt) | 362 项全量回归通过，166.071 秒 |
| [ui.json](ui.json) | 隔离浏览器的质量表单、登录、中英文及响应式检查 |
| [wheel.json](wheel.json) | 源码之外独立安装环境，20 个资源/API 路径 |
| [runtime.json](runtime.json) | API、采集器进程归属及最终环境 |
| [login.json](login.json) | 正式默认管理员登录、注册关闭、退出，零业务写入 |
| [upgrade.json](upgrade.json) | 51 个运行文件一致、备份与数据保留、真实采集推进 |

预览：[正式登录页](login-desktop.png)、[手机登录页（隔离环境）](login-mobile.png)、[正式标准页](criteria-desktop.png)。源截图在本地 `output/playwright/v080-*`，预览中的规则与样式来自最终安装文件。

回归：`python -m unittest discover -s tests -q`。`wheel-smoke.py` 必须由安装 wheel 的 Python 使用 `-I` 在源码目录外运行。正式只读核查使用 `audit-v080.py --context <私有发布上下文> --live`，上下文和真实凭据不进入发布包。

浏览器工作流需先启动 `serve-quality-fixture.py`，其数据库由临时目录创建；不要改指向正式数据库。通过 Playwright CLI 的 `run-code --filename` 顺序执行 `quality-ui-login.cjs`、`quality-ui-workflow.cjs`、`quality-ui-layout.cjs`。`quality-ui-final.cjs` 用于补验表单固定操作区，之后可再次执行布局检查。正式环境只执行 `quality-ui-production.cjs` 的登录和只读页面检查，不提交评估。

这些结果证明软件约束与界面行为，不证明真实项目质量、团队体验、作者沟通或 Credits 到账。浏览器的预期匿名 401、错误登录 401 与低分 400 单独记录，不称为零网络错误。Docker 实机部署未在本机验证。
