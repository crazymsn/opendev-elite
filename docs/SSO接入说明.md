# OIDC 单点登录接入

v0.3 支持可选的 OpenID Connect Authorization Code + PKCE S256。默认不启用；普通团队凭证和 Agent Bearer API 不需要第三方依赖。启用前安装：

```powershell
python -m pip install ".[oidc]"
```

安装包同样可使用 `github_sponsorship_worker-0.3.0-py3-none-any.whl[oidc]`。使用运行服务的同一个 Python 环境安装，不能切换到未安装可选依赖的系统解释器。

## 配置

在实际 `service.local.json` 中保留 users 和其他字段，加入以下内容，替换全部示例值：

```json
{
  "public_origin": "https://sponsorship.your-domain.example",
  "oidc": {
    "issuer": "https://identity.your-domain.example/realms/team",
    "client_id": "developer-sponsorship",
    "client_secret_env": "SPONSORSHIP_OIDC_CLIENT_SECRET",
    "token_endpoint_auth_method": "client_secret_basic",
    "subjects": {
      "IDENTITY_PROVIDER_SUBJECT_FOR_REVIEWER": "technical-reviewer",
      "IDENTITY_PROVIDER_SUBJECT_FOR_APPROVER": "funding-approver"
    }
  }
}
```

在身份提供方登记精确回调地址：`https://sponsorship.your-domain.example/auth/sso/callback`。把客户端密钥设到上述命名环境变量。不要把密钥放入 JSON 或命令行参数。

subjects 的键必须是身份提供方的稳定 sub，值必须是已启用的本地人工 actor。不同成员应映射到不同 actor。不根据 email、显示名或 IdP roles 自动授予权限，不自动创建管理员，也不把人类登录映射为 Agent 身份。

公开客户端可显式使用 `token_endpoint_auth_method: "none"`，同时删除 client_secret_env，仍强制 PKCE S256。默认机密客户端使用 client_secret_basic。本机受控调试可使用明确的 loopback HTTP public_origin；远端 public_origin 必须 HTTPS。

## 安全边界与兼容范围

- 仅接受 RS256 签名；PyJWT[crypto] >=2.13.0,<3 负责密码学验证。校验 issuer、audience、expiry、nonce、azp，不相信 token 指定的任意 jku/x5u。
- discovery、authorization、token、JWKS 必须 HTTPS 且与 issuer 同主机及端口；请求禁止跳转。不支持把 token/JWKS 拆到不同域的配置，遇到该 IdP 架构应先实现并评审显式信任名单。
- state、nonce 和 PKCE 属于 300 秒一次性事务，绑定浏览器短期 Cookie。事务 Cookie 使用 SameSite=Lax 以允许身份提供方顶层回调；应用登录会话仍使用 HttpOnly、SameSite=Strict，HTTPS 时使用 Secure。
- token 和授权码不保存到业务数据库或前端存储，不回传给前端。应用会话和登录事务保存在内存，单进程部署，重启失效。
- 登出只撤销本应用会话，不实现身份提供方的全局登出，也不实现跨节点共享会话。

## 验证

测试使用假身份提供方和真实 RSA 签名，覆盖 code+PKCE、参数绑定、签名/issuer/audience/nonce 拒绝、会话建立与重放拒绝，并通过真实 localhost HTTP 走回调。

这些测试没有登录用户公司的真实身份提供方。上线需管理员登记客户端、提供稳定 sub 映射，分别验证授权角色、未知成员拒绝、审批与执行身份隔离、会话过期和退出。

实现依据：[OIDC Core 的 ID Token 校验](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)、[RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)、[PyJWT API](https://pyjwt.readthedocs.io/en/stable/api.html)和 [PyJWT 更新记录](https://pyjwt.readthedocs.io/en/stable/changelog.html)。
