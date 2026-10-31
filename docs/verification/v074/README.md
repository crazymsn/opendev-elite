# v0.7.4 验证记录

| 记录 | 覆盖范围 |
| --- | --- |
| [tests.txt](tests.txt) | 356 项回归，85.324 秒；隔离样本覆盖混合身份和现有 SOP |
| [wheel.json](wheel.json) | 在源码之外的独立安装环境验证 19 个资源/API 路径 |
| [runtime.json](runtime.json) | 正式 API、采集器均运行最终环境，进程归属已核对 |
| [login.json](login.json) | 默认管理员登录、公开注册关闭和退出，零业务写入 |
| [upgrade.json](upgrade.json) | 50 个运行文件一致；备份、数据保留、完整性与真实采集推进 |
| [readiness.json](readiness.json) | 最终安装版本只读核查真实库：身份配置通过，Agent/Credits 配置及实际业务证据仍缺失 |

运行 `python -m unittest discover -s tests -q` 执行回归。`wheel-smoke.py` 必须由已安装 wheel 的 Python 使用 `-I`、在源码目录之外运行。`audit-v074.py --context <私有发布上下文.json> --live` 核查安装和数据库；上下文不打包。`readiness-v074.py --root <运行目录>` 使用只读连接，不运行数据库初始化、网络请求或子进程。

这些工具只输出脱敏记录。`readiness.json` 的 GitHub 环境字段属于临时审计进程；常驻采集的实际认证证据见 `upgrade.json` 的 GitHub 响应。`production_verified:false` 是刻意保留的证据边界，任何软件回归均不能代替实际体验、访谈或到账。
