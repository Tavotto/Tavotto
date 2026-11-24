# ADR 0201：Sign in with ChatGPT（SIWC）预备——独立连接、专用 Responses 协议、Token Provider 契约

日期：2026-11-23 · 状态：**Accepted（只做预备：默认关、无界面、不实现 OAuth、不联网验证）**
**1.0 收敛纪律：新增产品能力，维护者 2026-11-23 给豁免（「先做吧……这些我都同意」），登记在 `docs/1.0-release-readiness.md` §1.8。**
相关：[0147 内置执行器](0147-builtin-agent-runner.md)（循环、三种协议、路由；本 ADR 修订其 §六）、[0015 Agent 注册表](0015-coding-agent-registry-and-settings.md)、
ADR 0200 Tavotto Agent（草稿 #2086 / #2103，虚拟连接的先例）；审计与方案全文 `docs/implementation/siwc-readiness-2026-11-23/PLAN.md`。

## 问题

OpenAI 的 SIWC 计划允许开源、本机运行的应用让用户用自己的 ChatGPT 订阅额度调 `POST https://api.openai.com/v1/responses`（OAuth 授权码 + PKCE，
令牌由应用自己保存）。Tavotto 今天的「ChatGPT 订阅」只能经 Codex CLI 的官方登录（连接 `sub:codex` → `cli:codex`），认证方式和执行器绑在一起；
内置执行器的 `openai-responses` 协议又不满足 SIWC 的硬性要求（必须 `stream: true`、禁发 `max_output_tokens` 等、专用错误码、函数工具须放进 namespace）。
维护者已向 OpenAI 提交意向表；本阶段只准备基础设施，不影响现有用户。

## 决定

### 一、SIWC 是「我们自己保存的 OAuth 令牌」这一类，走内置执行器

不拆 `SUBSCRIPTIONS`、不改 `sub:codex`：SIWC 是新的连接 **`siwc:chatgpt`**（不以 `sub:` 开头，与 Codex 登录态、前端「订阅类不可管理」的判据都不混），
**只走 `builtin`**，永不注入 Codex（`codex-app-server` 的 env_key 注入法记为以后评估项，不做）。连接视图与路由的接入（PR-3）照 0200 的虚拟连接写法，**等 #2103 合入后再叠**。

### 二、专用协议 `siwc-responses`（runner 层，PR-1）

- 请求体**白名单**构造：`model`、`instructions`、`input`（数组，全量无状态回放）、`tools`（函数工具包进一个 `namespace`）、`store: false`、`stream: true`；
  调用方传入的 `max_output_tokens` / effort 一律不发；官方禁用清单（`background`、`conversation`、`max_output_tokens`、`max_tool_calls`、`metadata`、`moderation`、
  `multi_agent`、`prompt`、`prompt_cache_retention`、`safety_identifier`、`temperature`、`top_logprobs`、`top_p`、`truncation`、`user`、`previous_response_id`）与 `role: "system"` 条目不出现；
  回放剥掉 `reasoning` 条目（不发 `include`，没有 `encrypted_content` 的条目在 `store: false` 下会被拒）。
- SSE 边读边解析，只保留终态事件与 `response.output_item.done` 条目，增量随读随丢；总字节与单条目各有硬上限。`response.completed` 才算成功；
  completed 不带 `output` 时用累积的条目。`response.failed` / 预流错误按闭集错误码归类；没有终态事件 = 可重试的 `network/stream_incomplete`（工具在客户端执行，重发无副作用）。
- 错误码闭集：`subscription_sharing_usage_limit_exceeded` → `quota`（不重试）；`subscription_sharing_usage_unavailable` / `subscription_sharing_user_unavailable` → 可重试 `network`；
  `subscription_sharing_user_not_eligible` → `refused/plan_not_eligible`（**不是 `auth`**，官方禁止循环 OAuth）；`subscription_sharing_unsupported_capability` / `subscription_sharing_route_not_supported` /
  `chatpass_v2_*` → `internal`（配置错，不重试）；`subscription_sharing_invalid_user` 与无码 401 → `auth`。服务端原文不进事件、日志、诊断包。
- 不参与 `--fallback` 换协议与字段降级（官方：不得静默切换计费方式）；强制 `--auth bearer`。**不进 `ai_providers.PROTOCOLS`**：用户记录永远不能声明它。
- 原有 `anthropic` / `openai-chat` / `openai-responses` 三条协议的请求与解析不变：`HttpModel` 只多两个可选钩子（`decode` / `classify`），旧协议不实现它们。

### 三、Token Provider 契约（PR-2，不实现登录）

`engine/siwc_auth.py`：`access_token(min_valid_s)`（不够新就刷新，刷不动 → `NeedsLogin`）、`mark_rejected(reason)`（服务端拒了当前令牌：下次先刷新，**不清凭据**——官方要求只在确认撤销或终态刷新错误后才让用户重新登录）、`status()`（只给登录态与过期时刻，无令牌）。
`min_valid_s` 至少覆盖一次任务的最长时限（900 s）加余量，**不复用** `ai_oauth` 的 10 分钟余量；刷新串行化。本阶段只有恒未登录的 `DisabledProvider` 与测试用 `StaticProvider`。
**不读取、不迁移 Codex CLI 的令牌**；凭据存储（每个 client_id + 已验证 `sub` 一份、0600、原子写）只写进契约，不实现。

### 四、开关

`TAVOTTO_EXPERIMENTAL_SIWC=1`，只认环境变量，由父进程判定；关 = 新代码不可达（不派发、不出现连接、runner 拒绝该协议）。默认关。

### 五、仍然不做

用户可见的按钮或文案；真实 OAuth 与凭据落盘；用真实 ChatGPT 账号测试；任何 `chatgpt.com/backend-api` 接口；引入 OpenAI DevKit 的代码；
改默认模型 / 登录入口 / 路由 / 已存配置；AI 修复、结构化调用、保存时探测、型号清单使用 SIWC。

## 后果

- 用例全部是 Mock（runner 测试的合成 SSE）。**2026-11-24 真服务核对**（维护者本人账号、一次性脚本、令牌只在内存且跑完撤销）：动态注册、授权码 + PKCE、
  型号清单、单轮 / namespace 工具调用 / 多轮回放都通过，`response.completed` 带完整 `output`，函数调用回来是裸名 + `namespace` 字段；细节见 `siwc_wire` 模块说明。
  **仍未真服务验证**：错误码（含用量超限）、`response.incomplete` / `error` 载荷、刷新令牌轮换、https 下流到一半取消、`reasoning` 回放。上线前逐条确认，不得写成已验证。
- 我们自己多维护一条协议的字段细节；官方 preview 限制变化时只改 `siwc-responses` 一处。
