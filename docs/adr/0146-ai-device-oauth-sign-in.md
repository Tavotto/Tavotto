# ADR 0146：改图助手的「账号登录」——MiniMax 设备码 OAuth（流程按厂商可扩展）

日期：2026-10-22 · 状态：**提议**
相关：[0008 会话认证](0008-unified-local-session-auth.md)（新端点不得绕过）、[0015 编码 Agent 注册表与设置](0015-coding-agent-registry-and-settings.md)、
`docs/rules/backend/ai-agent-bridge.md`（第三方 API 接入）、`docs/legal/THIRD_PARTY_PORTS.md`。

## 背景

MiniMax 的会员用户拿到的是「账号登录」而不是 API Key：其官方客户端走设备码授权，令牌几十分钟过期、靠刷新令牌续。
在改图助手里接它目前只能让用户手填一个他们根本没有的 Key。**Kimi 不在本决定内**：维护者决定它改走官方 Kimi Code 命令行（独立执行器）。本决定让用户点一下、在浏览器里确认验证码，就得到一条可用的接口记录。

这引入两件此前没有的事：**Tavotto 存第三方的刷新令牌**，以及**新的出站请求**（不是遥测）。

## 决定

1. **只给 Tavotto 自己申请到 client_id 的厂商开放。** client_id 集中在 `ai_oauth.CLIENT_IDS`（出厂全为 `None`），
   联调期可用环境变量 `TAVOTTO_OAUTH_CLIENT_ID_MINIMAX` 覆盖。为 `None` 时 capabilities 报
   `oauth.<厂商>.oauth_available = false`，界面**不出现**该入口，也不放占位或「即将推出」。源码里不写别家的 client_id、不发冒充别家客户端的头
   （请求头只标 Tavotto 与版本）；用例钉死「源码里没有 UUID 形状的 client_id，也没有两份参照实现里的那两个」。
2. **协议**：规格表驱动，已实现两种风味。标准 RFC 8628（当前没有厂商使用，用例以合成厂商覆盖，供将来厂商直接加表项；设备授权 → 按间隔轮询，`authorization_pending` 继续、`slow_down` 加间隔、`expired_token` / `access_denied`
   终止，刷新 grant `refresh_token`）；MiniMax 为 `user_code` grant + PKCE(S256) + `state` 校验，国内 / 海外两个区域端点，过期时间有相对秒 / 绝对秒 / 绝对毫秒
   三种表示。刷新**只回到签发它的那个区域端点**，且该端点必须在代码里的已知表中（记录被改过的端点不发）。
3. **令牌存放**：写进**一条 `agent=claude` 的接口记录**（`auth: "oauth"`，`oauth: {access, refresh, expires_ms, token_endpoint, provider, region}`），
   与 `api_key` 同级——只在用户配置里，目录 0700、文件 0600。`ai_providers.public()` 只回 `auth` / `signed_in` / `oauth_expires_ms` / `oauth_provider` /
   `oauth_region`，**令牌一个字节都不回传**；`save()`（界面通道）剥掉请求体里的 `auth` / `oauth`，令牌只能由 `ai_oauth` 写；诊断包按字段挑选、不含令牌；日志不记令牌、
   设备码与验证码，传输失败只记异常类型。
4. **spawn 前刷新**：`ai_bridge.run` 在不兼容检查旁、快照之前调用 `ai_oauth.ensure_fresh`——剩余不足 10 分钟就刷新并写回；刷不动（被拒 / 记录残缺 / 已过期）以 409
   `ai_endpoint_needs_login` 拒绝，**不 spawn**。令牌尚未过期而刷新只遇到暂时性故障（断网 / 5xx / 429）时沿用现有令牌。
5. **注入**：Claude Code 侧只设 `ANTHROPIC_AUTH_TOKEN`（Bearer）；`ANTHROPIC_API_KEY` 设为空串——既不让它发 x-api-key（部分网关报告会 401），
   也盖掉用户 shell 里继承来的真 Anthropic 密钥，避免顺路发给第三方网关。
6. **出站**：新增第七处出站 HTTPS（`ai_oauth._opener`，`tlstrust` 平台原生证书校验、**不跟随重定向**、30 s 超时、响应体 8 KB（错误）/ 64 KB（成功）上限），
   登记在 `tests/test_outbound_https_trust.py::OUTBOUND` 与 `tlstrust` 模块头。它只在用户点「登录」后、以及已登录接口的 spawn 前刷新时发生，
   目的地只有代码表里写死的厂商授权域名；**不是遥测**，不经过遥测白名单，也不携带任何脚本 / 路径 / 图内文字。
7. **退出登录**只删本地令牌（记录与用户改过的模型保留，界面给「重新登录」）；**不**调用对方撤销接口——两份参照实现都没有，我们不发明未经确认的出站请求。
8. **不使用服务端下发的 `resource_url` 当 API 基址**：各区域基址写死，响应不能把访问令牌改投到别的主机。
9. 同一厂商同时只有一个登录流程（同区域重复发起拿回同一个；换区域则取消旧的）；取消时在途轮询的结果作废、令牌不落盘。

## 后果

* 界面：模型服务区在厂商可用时出现「用 X 账号登录」；点开小对话框（一句话 + 大号验证码 + 「重新打开网页」+「取消」），轮询到 `signed_in` 自动关闭；
  `expired / denied / failed` 换成一句话 + 「再试一次」。已登录的记录行显示「已登录」+「退出」。
* **未验证项**：MiniMax 的 Anthropic 兼容端点接受 OAuth 访问令牌的 Bearer，依据是 OpenClaw 的目录代码（OAuth 用 Bearer），尚未实测。拿到 client_id 联调时先验，不符则本 ADR 修订，而不是在客户端里冒充。
* 许可证：参照实现的出处与取舍记在 `docs/legal/THIRD_PARTY_PORTS.md`。
