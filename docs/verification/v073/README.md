# v0.7.3 验证记录

这些文件是 2026-09-08 验证时的脱敏结果；运行日志、数据库备份、完整本地路径上下文与凭据留在本机，不放入发布包。

| 记录 | 含义与边界 |
| --- | --- |
| [tests.txt](tests.txt) | 353 项回归，75.870 秒；包含合成 API 与认证错误场景 |
| [wheel.json](wheel.json) | 源码之外运行的独立 wheel 环境，19 个资源/API 路径 |
| [github-auth-probe.json](github-auth-probe.json) | 已安装代码显式读取 gh 身份后核对 GitHub 配额，临时库 |
| [public-search.json](public-search.json) | 最终已安装代码的公开仓库真实搜索，1 个条目；未采集代码或修改正式库 |
| [queue-recovery.json](queue-recovery.json) | 370 个匿名限流任务的定向恢复，未清错误或重置尝试次数 |
| [runtime.json](runtime.json) | API 和采集器的进程归属、最终环境及认证启动记录 |
| [login.json](login.json) | 正式管理员登录、关闭公开注册及退出；没有业务写入 |
| [upgrade.json](upgrade.json) | 50 个运行文件一致、原候选/账号保留、数据库校验、公开元数据与实际快照推进 |

回归命令为 `python -m unittest discover -s tests -q`。安装检查使用独立 Python 的 `-I` 模式运行 `scripts/validation/wheel-smoke.py`。版本交付审计脚本 `scripts/validation/audit-v073.py` 读取私有发布上下文与只读数据库，不输出账号行或凭据。`default-login-smoke.py --expected-version 0.7.3` 仅适用于管理员尚未改密的已明确配置环境；不能为满足脚本而重置账号。

真实采集、合成测试和业务结论各自独立。记录不能证明已执行候选程序、完成真实审核、联系作者或发放 Credits。
