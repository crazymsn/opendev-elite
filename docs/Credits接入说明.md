# OpenAI Next Credits 接入说明

核对日期：2026-09-07。源码版本：`057b78a1bca368dd9b77e56a3fb249f0918b1b98`。源码访问需要对应私有仓库权限。

## 源码证据

- [邀请控制器](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/api-v1/invitations-v1.controller.ts)：确认 PAT 可访问方法，以及邮件、claim-link、activate 的认证边界。
- [参与者控制器](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/api-v1/participants-v1.controller.ts)：列表、额度总值更新和活动归属判断。
- [外部 DTO](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/api-v1/external-api.dto.ts)：响应字段白名单、额度单位、激活和已结算数据。
- [AuthGuard](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/auth/auth.guard.ts)：PAT 格式、`@PatEnabled` 和禁止混用 Session/PAT。
- [邀请服务](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/invitations/invitations.service.ts)：创建邀请、重复邮箱约束、消费后更新限制与审计。
- [Prisma schema](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/packages/db/prisma/schema.prisma)：邀请、参与者和账户关系。
- [活动控制器](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/api-v1/events-v1.controller.ts)：活动 `endAt`、管理权限及缺少 PAT 开放标记。
- [外部 OpenAPI](https://github.com/Kadxy/openai-next-credits/blob/057b78a1bca368dd9b77e56a3fb249f0918b1b98/apps/api/src/openapi/external-openapi.ts)：`/developers`、`/openapi/v1.json`，仅公开 PAT 方法。

## 路径选择

| 操作 | 真实端点 | 当前适配行为 |
| --- | --- | --- |
| 获取契约 | `GET /openapi/v1.json` | 真正写入前核对所需端点是否存在 |
| 查询邀请 | `GET /api/v1/events/{slug}/invitations?q=...&page=...&pageSize=100` | 模糊查询后精确比对邮箱；处理分页 |
| 创建邀请 | `POST /api/v1/events/{slug}/invitations` | 显式 amount cents，唯一业务备注；不填不必要的个人信息 |
| 查询参与者 | `GET /api/v1/events/{slug}/participants?q=...&page=...&pageSize=100` | 比较邮箱、邀请关联、激活、封禁和额度 |
| 同步邀请 | `PUT /api/v1/events/{slug}/invitations/sync` | 现有 API 支持，但本适配器不调用，防止更新他人已建邀请 |
| 修改额度总值 | `PATCH /api/v1/events/{slug}/participants/{pid}` | 现有 API 支持，当前不用于自动增量发放 |

请求使用 `Authorization: Bearer <NEXT_CREDITS_PAT>`。配置只写域名/路径及活动信息，环境变量只在发送请求时读取；不附 Session Cookie，也不追随 HTTP 重定向发送凭证。

## 身份、金额和有效期

接收身份是作者确认的邮箱。不能直接把任意贡献者 email、仓库所有者账号、组织名称当成接收账户。审批中的 `identity_evidence` 记录访谈确认依据。

金额明确以正整数 `cents` 记录，避免浮点换算。源码 DTO 说明单位是 cents，适配器不猜测其它货币或 token 单位。超过数据库 Int 范围、非整数、非正数均拒绝。

审批需要 `event_slug` 和 `expires_at`。`expires_at` 是与活动后台确认的日期口径；该 API 没有独立的单笔到期字段，适配器不会假装将本地日期下发到远端。跨时区精确时刻以活动 `endAt` 为准，正式联调必须人工检查。读对账允许在到期后继续执行。

首次写入时将提供方基址与业务记录绑定。后续不能随意更换地址后，把另一套 Credits 系统的结果记到原赞助记录。

## 状态与恢复

```text
pending → submitting → awaiting_claim → issued → closed
                └→ unknown ──只读核对──→ awaiting_claim / issued
pending / failed → 撤销审批时 cancelled
failed → 重新预览与显式执行
```

`submitting` 在调用前落库，因此崩溃不会留下“尚未尝试”的假象。超时、409、鉴权/契约异常等都保守转成待核对；本地不会自动无限重试创建。网络只读异常保留之前发放状态。

关联标识为 `note = sponsorship:<grant_id>`，并结合活动与邮箱唯一性核对。发现相同邮箱的无关记录时停止，不抢占、不覆盖。对账要确认邀请已消费、参与者已激活且未封禁，并比较参与者实际额度；单纯收到 POST 201 不产生 issued。

邀请若在后台被人工修改、删除、转移或关联到不同账户，进入人工排查。`not_found` 不代表“确定没发过”，不能直接补建。v0.2 禁止把 `unknown`、`submitting`、`awaiting_claim` 手工重置为可重试状态；需要核对提供方审计。当前没有实现这类异常的人工恢复接口，不能通过直接改库绕开约束。

服务 API 的真实提交还要求服务端开启 `allow_credit_writes`、请求传 `execute: true`，并使用与审批人不同的发放身份。发生外部调用中断时，同一个幂等键返回 409；用新的幂等键执行只读对账。不要用新幂等键再次创建邀请来代替对账。

## 无真实写入的本地验证

```powershell
python -m unittest discover -s tests -v
python main.py --db path/to/empty-demo.db demo --out artifacts/credits-demo
```

模拟包括：创建后等待、作者激活后记到账、重复对账、服务端成功但响应丢失、已有邀请冲突。FakeCredits 显式使用 `example.invalid`，不调用真实系统。

## 生产联调清单

1. Credits 管理员建立测试活动，确认额度限制、到期时刻、身份绑定/签到条件和管理账号权限。
2. 将本地配置复制为 `config/credits.local.json`；替换基址、活动、到期日期及后台设置依据，在环境中配置 PAT。
3. 用实际的三轮业务记录建立测试赞助审批；不要把合成 demo 数据导入真实库后发放。
4. 执行 `credits-plan`，核对邮箱、金额和活动；`credits-submit` 默认仍是预览。
5. 对批准的测试账户显式执行 `credits-submit ... --execute`，确认只出现一条邀请。
6. 由 Credits 后台和测试作者完成现有领取流程；此过程不属于 PAT 适配器的消息发送能力。
7. 执行 `credits-reconcile`，验证激活、额度及复盘待办，再重复核对确认无重复发放。
8. 模拟断网、PAT 失效、已存在邮箱、到期/封禁，确认记录进入对应异常路径，检查两边审计。

截至本次交付，尚未获得已部署地址、活动与 PAT，因此未执行上述生产写入或实际发放。
