# ADR 0209：Sign in with ChatGPT 正式登录——取代 Codex CLI 的 ChatGPT 订阅登录

日期：2026-11-25 · 状态：**Accepted（分 PR 落地；切换默认值的那一个 PR 等真服务核对后另开）**
**1.0 收敛纪律：扩大 [0201](0201-siwc-readiness.md) 的豁免，维护者 2026-11-25 裁决（「先把这些缺的都补充好。我们目前就只是一个开源项目」），登记在 `docs/1.0-release-readiness.md` §1.8。**
修订：[0201](0201-siwc-readiness.md) §五「仍然不做」里的「真实 OAuth 与凭据落盘」「用真实账号测试」「改登录入口」三条**撤销**；其余（不碰 `chatgpt.com/backend-api`、不引入 DevKit 代码、
不读不迁移 Codex CLI 令牌、AI 修复 / 结构化调用 / 保存时探测不用 SIWC）照旧。相关：[0008 会话认证](0008-unified-local-session-auth.md)、[0146 设备码账号登录](0146-ai-device-oauth-sign-in.md)、[0147 内置执行器](0147-builtin-agent-runner.md)。

## 问题

0201 只备好了协议（`siwc-responses`）和令牌接口（`siwc_auth`），没有登录、凭据、连接与界面。今天 ChatGPT 订阅用户仍必须装 Codex CLI、在终端里 `codex login`，
这是新手卡住最多的一步。维护者要求把 SIWC 补齐，并用它取代 Codex CLI 那条订阅登录。

**资格**：官方开源文档（`/siwc/token-sharing-open-source`，2026-11-25 读取）：这套文档讲「open-source and locally hosted apps」的订阅额度；「paid or remotely hosted app」要填意向表。
Tavotto 是 AGPL 开源、本机运行；维护者 2026-11-25 判定按开源路径走（意向表 11-23 已另行提交）。付费版（0193）/ 闭源 Pro（0198）上线前须再核这一条。

## 决定

### 一、登录：授权码 + PKCE，系统浏览器，一次性本机回调（`engine/siwc_login.py`）

- 端点全部来自 `https://auth.openai.com/.well-known/openid-configuration`（authorize / token / revocation / jwks），写成常量、看护用例对拍文档值。
- 首次注册：`client_id=dynamic_agent_client` + `agent_name_hint=Tavotto` + `ext_agent_host_id`；回调带回 `oaiapp_…` 才算注册完成（**不把 `dynamic_agent_client` 存成已签发 id**）。
  再次登录同一账号：用存下的 `client_id`、不发 `agent_name_hint`、带 `id_token_hint`；回调的 `client_id` 不一致 → 拒绝、不替换。
- `scope` = `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`，`resource=https://api.openai.com/v1`；每次尝试新的 `state` / `nonce` / PKCE（S256）。
- **回调监听器不是 Flask 路由**：一次性的 `http.server`，只绑 `127.0.0.1`、只认 `GET /auth/callback`、只认本次 `state`（常量时间比较），收到一次（或 10 分钟、或取消）即关。
  它不提供任何 Tavotto 能力、不碰项目与文件，只把 `code` 交回本进程——所以**不是** ADR 0008 意义上的「新端点」，不过会话认证；
  发起 / 查询 / 取消登录的 `/api/ai/siwc/*` 是普通 Flask 端点，照常过会话认证。监听器先起、再开浏览器（官方要求）。
- 端口：先试 1455（官方示例），被占用（例如 Codex CLI 的登录正开着）就换系统分配的空闲端口；授权与换令牌发同一个 `redirect_uri`。
- 换令牌后**验 ID 令牌**：RS256 签名对 JWKS（成熟库验签，不手写；JWKS 缓存、`kid` 未知时重取一次）、`iss`、`aud` == 已签发 `client_id`、`exp`（允许 5 分钟时钟偏差）、`nonce`。
  身份 = 验过的 `sub`；`email` 只作显示。授权范围没有 `chatgpt.tokens.use.direct` = 已登录但不许用订阅额度（`PlanUsageDisabled`），**不进推理、不换计费方式**。
- `access_denied` → 不换令牌；`invalid_grant` → 丢掉这个 code、用同一 client_id 重来。

### 二、凭据（`engine/siwc_store.py`）

- `data_dir()/siwc/`（0700）下：`host.json`（`ext_agent_host_id` = `urn:uuid:<v4>`，首次登录前生成并持久，之后永不变）；
  `accounts/<client_id>.json`（0600，原子写）：`{issuer, subject, email, client_id, ext_agent_host_id, id_token, access_token, refresh_token, token_type, expires_at, scopes, saved_at}`。
  一个已签发 client_id + 一个 `sub` 一份，永不混用；**不进 `ai.providers[]`**（`first_key_connection` 等「挑第一个能用的连接」结构上看不见它）。
- 刷新：`siwc_auth.RefreshingProvider` 接上这份存储；刷新串行化 = 进程内锁 + **跨进程文件锁**（`refresh_token_reused` 是终态错误，两个 Tavotto 进程抢刷新会把用户踢下线；macOS/Linux `fcntl`、Windows `msvcrt`）。
  访问令牌、过期时刻、授权范围、轮换后的刷新令牌整体替换。终态刷新错误才清令牌（保留 client_id 与 host id），暂时失败保留。
- 退出登录：先停用，再向 revocation 端点撤销刷新令牌（空 200 = 成功）；撤销没确认也清本机令牌，并告诉用户「远端撤销未确认」。保留 client_id 与 host id。
- 令牌不进日志、事件、诊断包、遥测、`public()`、连接视图；对外只有 `status()`（登录态、额度许可、过期时刻、显示用 email）。

### 三、型号

登录成功后 `GET /v1/models`（`models[]`，取 `visibility == "list"` 的 `slug` / `display_name`），缓存进账号记录；连接视图只读缓存、**不联网**；
默认型号 = 服务端顺序第一个。用户可在连接管理里手动刷新清单。

### 四、连接与路由

- 连接 `siwc:chatgpt`：开关开 **且** 有账号记录时出现在 `connections()`（未登录不出现；`needs_auth` = 有记录但刷新终态失败）。只走 `builtin`；`TAVOTTO_AI_BUILTIN=0` → 走不通；永不 `cli:codex`。
- 内置执行器 argv 由 SIWC 专用的一处构造（`--wire siwc-responses --base-url https://api.openai.com/v1 --auth bearer --model …`，无 effort / fallback），
  看护用例把父进程产出的 argv 直接喂给 `siwc_wire.runner_args_ok`。令牌在 spawn 前（锁外）`access_token(min_valid_s=MIN_VALID_S)`，只进 `TAVOTTO_AGENT_API_KEY`；
  开关值由父进程显式写进子进程环境（runner 自己也查）。
- AI 修复、结构化调用、数据角色、保存时探测、型号探测：不用 SIWC（0201 §五不变）。

### 五、界面

- 「连接 AI」里 OpenAI 一家的订阅方式只有「Continue with ChatGPT」（官方推荐文案，产品名不翻译；中文界面同一串）。**老用户也直接切换**（维护者 2026-11-25：「老用户直接切换成新的登录办法」）：Codex CLI 登录方式与 `sub:codex` 连接不再列出，老用户点一次「Continue with ChatGPT」即可（Codex 的令牌我们不读、不迁移）；`resolve_connection("sub:codex")` 仍认，已发出的旧请求不 404。
- **失败时的选择题**（维护者 2026-11-25「用 1，失败时给选择题」）：登录没完成 / 超时 / 没允许订阅额度时，那一句后面给「改用 Codex 登录」（本机装了 Codex 才出现；重试就是原来的 Continue with ChatGPT）。点了先 `POST /api/ai/siwc/codex-fallback` 记下（`ai.siwc_codex_fallback`），之后这台机器一直列出 Codex 登录与 `sub:codex`，再走原来的 Codex 官方登录。
- 失败兜底（新手不卡住）：额度用完 → 主按钮「Manage usage」开 `https://chatgpt.com/settings/usage`；不具备资格 → 选择题（填 API Key / 用 Codex 登录），**不循环 OAuth、不自动换计费**；
  `invalid_user` / 终态刷新错误 → 「重新登录」。文案与截图先过 agy、再给维护者看。
- 官方 UX 指南里的「首次使用说明」「Using ChatGPT plan 指示」与本仓库「不加说明文字 / 卡片一句话」的界面规矩冲突：随截图交维护者定，不默认加。

### 六、开关与上线顺序

后端两个 PR（登录、凭据与连接 #2210；与 API 体验相同 #2212）合入时开关**仍默认关**（界面还不认 `siwc` 方式）；界面 PR（#2228）把默认改成**开**
（维护者 2026-11-25：「开关默认开」），`TAVOTTO_EXPERIMENTAL_SIWC=0` 整体关掉。开着时即完整新行为：OpenAI 一家只有「Continue with ChatGPT」+ API Key，
Codex 登录与 `sub:codex` 行不再列出（老用户也直接切换）。

两轮真服务核对（2026-11-25，维护者本人账号）已做：首次注册在 1455 被占时换端口成功、ID 令牌对真实 JWKS 验签通过、型号清单（曾因响应超过 256 KB 被当成空清单，已修）
与每型号的思考强度档位、low–xhigh 实发、刷新令牌轮换、早于 `earliest_refresh_at` 刷新照样成功（所以只记不拦）、https 流到一半取消、撤销。
**仍未真服务验证**：用量超限等错误码、`response.incomplete` / `error` 载荷、带 `encrypted_content` 的 reasoning 条目回放、`max` 档实发——上线后在真实使用里观察，不写成已验证。

### 七（前）、11-23 三条规矩在这里怎么变

维护者 2026-11-25 确认（原话）：「1.开关默认开。2.可以真联调，不用问我。3.要能够被其他功能调到」。下表是确认后的结论。

| 11-23 定的（0201） | 0209 之后 |
| --- | --- |
| 放在 `TAVOTTO_EXPERIMENTAL_SIWC` 开关后面 | **默认开**：随界面 PR 一起落地（后端先于界面合入时仍关，免得「连接 AI」里只剩一个界面不认的方式）；`TAVOTTO_EXPERIMENTAL_SIWC=0` 整体关掉 |
| 只做 Mock 验证 | 仓库里的用例**仍全是 Mock**（本机假服务器）。真服务联调**要做、不必每次再问**：维护者本人账号、一次性脚本、临时数据目录、跑完撤销授权；结论写回本 ADR / `siwc_wire` 模块说明，不写进用例 |
| SIWC 记录不进 `first_key_connection` / 探测 / 用户可声明的 `PROTOCOLS` | **改**：`first_key_connection` 在所有 Key / 账号登录记录之后给 SIWC，AI 修复、结构化调用、数据出图、改图方案都能用（与 API 体验相同，§七）。**不变**：保存时探测不用它；凭据不进 `ai.providers[]`；`siwc-responses` 不在 `ai_providers.PROTOCOLS`，用户记录不能声明它（看护 `tests/test_siwc_connection.py` / `tests/test_siwc_parity.py`） |

### 七、不随 SIWC 一起替换的能力

0201 §五 把 AI 修复、结构化调用、数据出图（数据角色）、保存时探测排除在 SIWC 之外（不让「挑第一个能用的连接」静默花订阅额度）。
维护者 2026-11-25 指出 SIWC 本质上就是用户自己的 API 连接（「理想中 SIWC 不是就等同于使用 API 吗」「我们的目标是 SIWC 与 API 的体验完全相同」）：
**已接上**前三项（#2212）——用户连了 ChatGPT 就算显式选择，`first_key_connection` 在所有 Key 之后给它；请求走 `siwc-responses`（令牌在 `fixsession.build_model` / `ai_structured.call_structured` 现取）；
订阅额度不按 API 价目估钱；保存时探测仍不用（SIWC 不需要探测）。**思考强度**同 Key 连接：每个型号的档位取自 `/v1/models` 的 `supported_reasoning_levels`（第二次真服务核对：low–xhigh 实发 200、`minimal` 被拒）。

## 后果

- 新增运行时依赖一个：pycryptodomex（RS256 验签，维护者 2026-11-25 同意；只在验签函数里延后 import，import 不了 = 验签失败 = 登录失败，不降级成不验）。
- 我们自己保存订阅令牌：凭据文件的权限、原子写、跨进程锁都是新的维护面；看护全部用本机假授权服务器，不连真实服务器。
- ChatGPT 订阅用户此后都走内置执行器；「用 Codex 当执行器」只剩「Codex + 第三方 Key 注入」这一条（ChatGPT 订阅不再经 Codex）。
