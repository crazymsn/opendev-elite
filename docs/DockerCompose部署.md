# OpenDev Elite · Docker Compose 部署

适用于 v0.6.1，SQLite schema 6。源码目录仍为 `GitHub_Worker`，Python 分发名仍为 `github-sponsorship-worker`，既有 Windows / systemd 部署兼容。Compose 部署是一个新的独立工作空间，不会自动搬迁现有本机数据。

## 一、直接启动

前提：可用的 Docker Engine 或 Docker Desktop（Linux 容器模式）、Docker Compose v2.20+；首次构建能够访问容器镜像仓库和 Python 包源。Windows 不需要在宿主安装 Python，Docker Desktop 的 Linux 后端必须正常运行。

在包含 `compose.yaml` 的项目目录运行：

```sh
docker compose version
docker info
docker compose config --quiet
docker compose up -d
docker compose ps -a
```

打开 **http://127.0.0.1:8780**。已为应用和网关设置 `pull_policy: build`，普通 `up -d` 即从本地源码构建镜像，后续使用构建缓存，不必另外添加 `--build`，也不会尝试拉取尚未发布的应用镜像。基础镜像与依赖在首次构建时仍需下载。

`up -d` 在后台启动，不保证返回时所有健康检查都已完成。`init` 显示 `Exited (0)` 是正常结果，`api`、`worker`、`gateway` 随后应显示运行与健康状态。需要命令等待全部服务就绪时，使用 `docker compose up -d --wait --wait-timeout 180`；首次构建时间不计入健康等待时间。已有 Windows 本机部署默认使用 8765，两个部署的数据库和端口相互独立。

默认即启动真实 GitHub 采集，不会自动通过评审、联系作者或发放 Credits。仅需先验收界面时可运行 `docker compose up -d api gateway`，随后以 `docker compose up -d worker` 启动采集。

## 二、服务和数据

| 服务 | 作用 | 持久化 / 网络 |
|---|---|---|
| `init` | 创建数据库、配置和六类身份；重复执行校验绑定 | 非 root，无网络，成功后退出 |
| `api` | 工作台、角色权限、SOP、Credits 与 Agent API | UID 10001，只监听回环 8765 |
| `gateway` | Nginx 转发网页与 API，保留原始 Host | 与 API 共享网络命名空间，监听 8080，无数据卷 |
| `worker` | 原 GitHub 爬虫、任务队列、持续更新和报告 | UID 10001，按需访问 GitHub |
| `maintenance` | 手动健康诊断、备份、配置导入 | `tools` profile，按命令启动，无网络 |

宿主 `127.0.0.1:8780` 映射到 API 网络命名空间的网关端口 8080。Python API 仍只监听内部 `127.0.0.1:8765`，没有放宽非回环 HTTP 绑定限制。网关不信任客户端 Forwarded 头；身份、Host、Origin 和 Credits 权限仍由 API 校验。

数据卷默认名为 **`opendev-elite_state`**，容器内路径 `/var/lib/opendev-elite`，包括 `worker.db`、六个凭证文件、`service.local.json`、`discovery.local.json`、`logs/`、`reports/` 和 `backups/`。卷使用本机 Docker 的本地存储；不接 NFS、SMB、OneDrive 或跨主机共享 SQLite。API 和 Worker 每种只部署一个实例。

所有常驻服务使用非 root 用户、只读镜像文件系统、有限临时目录、移除 Linux capabilities、内存和进程数限制。Docker 日志和应用日志均轮转。健康检查区分数据库可用性与采集队列告警，失败任务不会阻断用于修复任务的工作台。

## 三、默认管理员、成员登录与授权

首次启动后直接打开 `http://127.0.0.1:8780/#team-login`，使用 **OpenDev / 123456** 登录。没有公开管理员创建页和成员注册页，不需要复制服务器 Token。

管理员登录后在“成员与权限 → 新增成员”填写账号、显示名称、至少 6 个字符的初始密码并选择角色，创建成功即可登录。支持停用、改角色和重置密码；个人密码在“账号设置”修改。旧版已有待审批成员仍可审批。

默认管理员只在空账号库首次启动时生成；已有账号的安装保留原有管理员，重建容器不会恢复初始密码或启用已停用账号。账户保存在同一个数据卷和 SQLite 备份中；角色或密码变更使旧会话失效。

详细使用和忘记密码恢复见 [账号注册与授权](账号注册与授权.md)。

### 兼容入口：原有 Token 与 Agent 凭证

六个身份为 `technical-reviewer`、`relationship-operator`、`funding-approver`、`credits-operator`、`experience-agent`、`quality-auditor`。初始化日志只显示文件路径。每个团队成员使用对应身份，不共用审批与发放凭证。

Linux / macOS，示例提取运营凭证到个人私有目录：

```sh
mkdir -p "$HOME/.opendev-elite"
chmod 700 "$HOME/.opendev-elite"
docker compose cp api:/var/lib/opendev-elite/credentials/relationship-operator.token "$HOME/.opendev-elite/relationship-operator.token"
chmod 600 "$HOME/.opendev-elite/relationship-operator.token"
```

Windows PowerShell：

```powershell
$privateDir = Join-Path $env:LOCALAPPDATA 'opendev-elite-credentials'
New-Item -ItemType Directory -Force -Path $privateDir | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls $privateDir /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F'
docker compose cp api:/var/lib/opendev-elite/credentials/relationship-operator.token "$privateDir/relationship-operator.token"
notepad "$privateDir/relationship-operator.token"
```

兼容 Token 登录时，展开“使用原有访问凭证登录”，把文件内容填入输入框。替换文件名即可提取其他身份。`experience-agent` 凭证用于 Agent API，不用于浏览器登录。重建容器保留原有凭证；重复初始化不会重新发放。

## 四、环境变量与 HTTPS

可选：复制 `.env.example` 为 `.env`，填写配置后再启动。无需配置即可按默认端口运行。

| 变量 | 默认 | 用途 |
|---|---|---|
| `OPENDEV_PORT` | `8780` | 仅绑定宿主回环的访问端口 |
| `OPENDEV_PUBLIC_ORIGIN` | `http://127.0.0.1:${OPENDEV_PORT}` | 浏览器使用的精确 origin，无结尾 `/` |
| `GITHUB_TOKEN` | 空 | 只注入采集 Worker；空值使用匿名限额 |
| `NEXT_CREDITS_PAT` | 空 | 只注入 API；不会因此开启真实发放 |
| `OPENDEV_IMAGE` | `opendev-elite:0.6.1` | 本地构建的应用镜像名称 |
| `OPENDEV_GATEWAY_IMAGE` | `opendev-elite-gateway:0.6.1` | 本地构建的网关镜像名称 |
| `PYTHON_IMAGE` / `NGINX_IMAGE` | 见示例 | 可替换成审核过的基础镜像 digest |

`.env` 不进入 Git、镜像构建上下文或发布包。`docker compose config` 和 `docker inspect` 可能展开环境变量，包含密钥时不要把输出作为工单附件。

域名部署将 `OPENDEV_PUBLIC_ORIGIN` 设为真实 `https://` origin，例如 `https://opendev.example.com`。在**同一宿主机**配置 TLS Nginx，将已有 [TLS 模板](../deploy/nginx-tls.conf.example) 的上游端口从 8765 改为 8780、Host 头改为 `$http_host`，替换域名和证书路径；执行 `nginx -t` 后再加载。用带端口的 HTTPS origin 时必须保留该端口。TLS 在宿主代理终止，Compose 端口仍保持回环绑定。

首次初始化后修改 origin，必须同时修改数据卷的 `service.local.json`：先导出该文件、修改 `public_origin`，然后按下一节导入。单独改 `.env` 会得到明确的不一致错误，不会静默覆盖身份配置。新端口或环境变量通过 `docker compose up -d --force-recreate --wait` 应用；`restart` 不会应用新的环境变量。

如改用文件 Secret，可在本地 `compose.override.yaml` 对相应服务挂载 Compose secret，并设置 `GITHUB_TOKEN_FILE` 或 `NEXT_CREDITS_PAT_FILE`；同时清空同名普通环境变量。例如：

```yaml
services:
  worker:
    environment:
      GITHUB_TOKEN: ""
      GITHUB_TOKEN_FILE: /run/secrets/github_token
    secrets: [github_token]
secrets:
  github_token:
    file: ./secrets/github_token.txt
```

Secret 文件需对容器 UID 10001 可读，放在忽略的 `secrets/` 目录，避免把配置文件导入命令用于明文 PAT。Credits secret 用同样方式挂到 `api`。应用限制读取大小，拒绝多行、空文件及同时提供两种来源。

## 五、修改配置与接入 Credits

维护工具将完整 JSON 对象写入数据卷，通过临时文件和原子替换完成，不留下半写入配置。导入不会输出配置内容。先保留旧配置副本。

```sh
docker compose cp api:/var/lib/opendev-elite/service.local.json ./service.local.json
docker compose cp api:/var/lib/opendev-elite/discovery.local.json ./discovery.local.json
# 编辑副本后导入。以下重定向语法用于 Linux/macOS shell。
docker compose run --rm -T maintenance configure --name service < service.local.json
docker compose run --rm -T maintenance configure --name discovery < discovery.local.json
docker compose up -d --force-recreate --wait
```

PowerShell 导入方式：

```powershell
Get-Content -Raw -Encoding utf8 service.local.json | docker compose run --rm -T maintenance configure --name service
```

Credits 接入使用 [Credits 配置示例](../config/credits.example.json)，填写真实服务 URL、活动和核验依据，保存为 `config/credits.local.json` 后：

```sh
docker compose run --rm -T maintenance configure --name credits < config/credits.local.json
```

然后在 `.env` 填写 `NEXT_CREDITS_PAT`，依照 [Credits 接入说明](Credits接入说明.md) 完成真实联调。只有管理员明确将 `service.local.json` 的 `allow_credit_writes` 设为 `true` 并导入、重建后才允许写入；每次业务发放仍受独立审批、执行身份和幂等规则约束。Credits 服务本身是独立上游服务，本 Compose 不创建虚假的 Credits 后端。

## 六、诊断、备份和升级

```sh
docker compose logs --tail 100 api worker gateway
docker compose run --rm maintenance health
docker compose run --rm maintenance backup
docker compose cp api:/var/lib/opendev-elite/backups ./opendev-backups
```

`backup` 使用 SQLite 在线备份 API，文件名含时间和随机后缀，不覆盖旧备份；该备份仅含数据库。配置、原始凭证需要单独保管。将备份复制到宿主的受保护目录，再纳入现有异机备份流程。

完整停止后的卷备份示例（Linux shell；`>` 输出二进制，不用于旧版 Windows PowerShell）：

```sh
docker compose stop gateway api worker
docker compose run --rm -T --entrypoint tar maintenance -C /var/lib/opendev-elite -czf - . > opendev-state-backup.tar.gz
docker compose up -d --wait
```

Windows 可在服务停止后用 `docker compose cp api:/var/lib/opendev-elite ./opendev-state-backup` 复制整个数据目录，然后再次 `up`。备份中包含凭证，应放到专用私有目录。

升级前在线备份数据库，并保留完整配置与凭证。获取新源码、按需更新 `.env` 的镜像版本后运行：

```sh
docker compose build --pull
# schema 升级前停止旧 API / Worker，避免新旧版本同时操作数据库。
docker compose stop gateway api worker
docker compose up -d --force-recreate --wait --wait-timeout 180
docker compose ps -a
```

API 和网关共享网络命名空间，升级时一起重建。保留旧镜像标签；回退代码时使用旧镜像与兼容 schema 的备份，不对较新 schema 强行降级。需要固定基础镜像时，在 `.env` 设置已核验的 `python@sha256:...` 和 `nginx@sha256:...`，并保存 `docker image inspect` 的镜像 ID / digest。默认标签用于方便构建，不能当作不可变供应链锁定。

若 API 异常重启后网关持续不健康，执行 `docker compose up -d --force-recreate --wait api gateway` 一起重建网络关系。Docker 的 restart policy 不会因为 `unhealthy` 状态自动重建服务，应由已有主机监控关注 `docker compose ps` 和队列报告。

`docker compose down` 删除容器与网络但保留数据卷。**不要对生产执行 `down -v`，它会删除数据库和凭证。** 更改 Compose 项目名（`-p`）会使用另一数据卷，因此日常操作保持同一个项目名。

恢复完整归档时停止三个常驻服务，在专用恢复项目的数据卷导入后再启动，避免覆盖仍在运行的部署：

```sh
# 示例恢复项目名 opendev-recovery，访问端口改为 8781。
docker compose -p opendev-recovery run --rm -T --entrypoint tar maintenance -C /var/lib/opendev-elite -xzf - < opendev-state-backup.tar.gz
```

之后导入与恢复环境 origin 一致的 `service.local.json`，所有后续命令都带 `-p opendev-recovery`，并设置恢复端口与 origin。先启动 `api gateway` 验证身份、数据和备份完整性，再启用 Worker。已有 Windows 数据迁移也遵循“停止写入、复制完整目录、恢复到新卷、调整 origin、验收后切换”的步骤。

## 七、Agent 和验收

本 Compose 运行赞助平台和采集程序。产品体验 Agent 继续运行在独立节点，通过认证 API 领取任务和上传结果，见 [团队操作指南](团队操作指南.md)。API 容器不挂载 Docker socket；不在掌握凭证和数据库的容器内运行被评测项目。

项目带有真实 Docker 冒烟脚本：

```sh
python scripts/validation/compose-smoke.py
```

脚本使用随机项目名和独立卷，仅启动 API / 网关，验证构建、初始化、页面品牌、认证、Host / Origin 拒绝、在线备份、重建后记录与凭证保留、旧会话失效；最终只删除它自己创建的测试卷。不会启动真实 GitHub 采集，不联系作者、不发放 Credits。CI 已新增 Ubuntu Compose 验收任务。

当前开发机没有可用 Docker Engine，WSL 注册的 Ubuntu 缺少磁盘文件，因此本次交付的容器实机运行结果仍需在可用 Docker 主机或 CI 中产生；不能把普通 Python 回归或 YAML 校验写成容器运行成功。

Compose 依赖启动和健康条件按照 [Docker 官方服务规范](https://docs.docker.com/reference/compose-file/services/) 与 [启动顺序说明](https://docs.docker.com/compose/how-tos/startup-order/) 配置。

自动构建策略见 [Docker pull_policy 说明](https://docs.docker.com/reference/compose-file/services/#pull_policy)。CI 保留 `--wait` 作为验收等待条件，默认构建不再依赖命令行 `--build`。
