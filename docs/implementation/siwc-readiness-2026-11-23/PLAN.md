# Sign in with ChatGPT（SIWC）架构兼容性：审计与最小改造方案

日期：2026-11-23 · 状态：**方案，待维护者拍板（1.0 豁免 + ADR 0147 §六修订）后才动代码**
基线：`origin/main` e9d70b094；相关在途 PR：#2103（ADR 0200 Tavotto Agent，草稿）。

官方文档（2026-11-23 读取。全文：overview、`models-and-inference`、`preview-limitations`、`errors-and-recovery`、`token-reference`、
`profiles-and-sessions`、`ui-ux-guidelines`、`quickstart`、`request-client-id`、`codex-app-server`；`sign-in` 分两次读、第 1 步开头未逐字读完；
通用 Responses 文档只读了 function-calling 指南前半与 streaming 指南；**未读**：self-hosted-vms、website（按钮样式）、Responses 参考页的事件一节）：

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference

---

## 一、审计：现在的认证、路由、执行器、线上协议怎么耦合

### 1. 四层，三个已经解耦，一个没有

| 层 | 现在的唯一出处 | 与 SIWC 的关系 |
| --- | --- | --- |
| 连接视图（界面看到的「连了哪些 AI」） | `ai_connections.connections()` / `vendors()` | SIWC 需要一条**新的、独立 id** 的连接 |
| 认证方式 | 三种：①CLI 官方登录（`SUBSCRIPTIONS`：`sub:codex` / `sub:claude`，凭据在 CLI 自己手里）；②API Key（`ai.providers[]` 的 `api_key`）；③账号登录 OAuth（`ai_oauth`，设备码，`auth:"oauth"` 记录，MiniMax，`CLIENT_IDS` 为 None 时整个入口不出现） | **②③ 已经与执行器解耦**：凭据由我们保存、`ensure_fresh` 在 spawn 前刷新、只经环境变量 `TAVOTTO_AGENT_API_KEY` 进 runner。**①是耦合点**：`SUBSCRIPTIONS` 把「ChatGPT 订阅」这个认证方式直接钉死到 `cli:codex` 执行器 |
| 路由 | `ai_connections.route_for` + 唯一判据 `ai_providers.builtin_only` / `builtin_conn` | 「只能走内置」已有现成判据（chat 记录）；#2103 又加了「虚拟记录、只走内置」的第二个先例（`hosted_ai.is_hosted`） |
| 执行器 | `cli:codex` / `cli:claude` / `builtin`（`agent_runner`，ADR 0147） | 内置执行器的循环、工具集、候选工作区、TAF 校验、回滚**完全不需要动** |
| 线上协议 | `agent_runner.wires.WIRES`：`anthropic` / `openai-chat` / `openai-responses`；传输 `post_json`；循环 `loop.HttpModel.complete` | **SIWC 与现有 `openai-responses` 不兼容**，见下 |

结论：要「认证方式与执行器路由解耦」，**不需要重构**。ChatGPT 订阅现在只能经 Codex CLI，是因为它是第①种认证；
SIWC 是第③种（我们自己保存的 OAuth 令牌）的又一个厂商，天然走内置执行器。`sub:codex` 一个字节不改。

### 2. 现有 `openai-responses` wire 与 SIWC 要求的逐条差异

| 官方要求 | 现在 `OpenAIResponsesWire` / `HttpModel` | 必须改 |
| --- | --- | --- |
| 每个请求 `stream: true`，只有 `response.completed` 才算成功，流中断是错误 | 不发 `stream`；`HttpModel.complete` 对 2xx 一律 `json.loads`；ADR 0147 §六「仍然不做：流式回复」 | 新 wire 发 `stream:true`；循环给 wire 一个 `decode` 钩子把 SSE 折叠成最终响应对象（**缓冲读完再解析，不做流式界面**） |
| `store: false` | 已经是 | — |
| 禁发 `max_output_tokens` / `temperature` / `metadata` / `truncation` / `user` / `background` / `conversation` / `max_tool_calls` / `moderation` / `multi_agent` / `prompt` / `prompt_cache_retention` / `safety_identifier` / `top_logprobs` / `top_p` | **总是发 `max_output_tokens`** | 新 wire 的请求体走**白名单**构造，不是在旧 body 上删字段 |
| 不用 `previous_response_id`，`input` 为数组、全量上下文 | 已是无状态全量回放 | — |
| 不接受 `role:"system"` 消息条目，用 `instructions` | 已用 `instructions` | 看护用例钉住 |
| 函数工具须放进 namespace 或 `additional_tools` 输入条目 | 顶层扁平 `{"type":"function"}` | SIWC 页无示例；通用 function-calling 指南给了 `{"type":"namespace","name","description","tools":[{"type":"function",…}]}`。新 wire 按这个包一层 namespace；**SIWC 路由上是否接受未经真服务验证** |
| 错误码：`subscription_sharing_usage_limit_exceeded`(429) / `…_usage_unavailable`(503) / `…_user_not_eligible`(403) / `…_unsupported_capability`(400) / `…_route_not_supported`(403) / `…_invalid_user`(401) / `chatpass_v2_*`(403) / `…_user_unavailable`(503)；用量超限也可能在流开始后以 `response.failed` 出现 | `classify_status`：429 只匹配 `quota/billing/credit` 子串 → 用量超限会被**重试 4 次后报 `rate_limited`**；403 一律 `auth` → 「不具备资格」会被当成要重新登录（**官方明确禁止循环 OAuth**）；`response.failed` 一律 `bad_response` | 新 wire 带自己的闭集分类（复用 #2103 新增的 `_error_code` 精确比对闭集码，原文不外传） |
| 不得自动换计费方式 / 不得静默切换 | 有 `--fallback` 换协议、`FieldAdjuster` 字段降级 | SIWC 连接**不给** fallback，wire 不参与字段降级 |
| `reasoning` / `include` / `parallel_tool_calls` / `tool_choice` | 给 effort 时发 `reasoning` + `include` | SIWC 页**未提及**。通用指南要求推理型号的 reasoning 条目随工具结果回传，但 `store:false` 下没有 `encrypted_content` 的条目会被拒——本阶段不发 effort / include，回放剥掉 `reasoning` 条目（复用现有剥 include 的逻辑），标「未经真服务验证」 |

### 3. 其他会碰到 SIWC 记录的调用点（必须逐个关）

`builtin_conn` 是共享判据，凡是「第一个能用的内置连接」都会把 SIWC 选中——会**静默花用户的 ChatGPT 额度**：

- `ai_connections.first_key_connection`（AI 修复 `fixsession`、`ai_structured`、`ai_data_roles` 三处在用）→ 本阶段 SIWC 一律不进；以后要进也必须显式选择。
- `ai_probe`（保存时探测）发 `max_tokens ≤ 16` = 禁用字段 → SIWC 不走探测。
- `live_models` 期待 `/v1/models` 的 OpenAI 形状；SIWC 的是 `models[]` + `visibility == "list"` + `slug` → 本阶段不接型号清单。
- `add_connection` 的 `wire` / `protocol` 由请求体给 → 必须拒绝 SIWC 协议名（不让用户拿一个 Key 记录伪装成 SIWC）。
- `fixsession.build_model` 直接 new `HttpModel` → SIWC 不进（同 first_key_connection）。

### 4. 按协议名分支的地方（新 wire 名会落进默认分支，逐个核）

`loop.HttpModel`（`truncated_continuation`、anthropic 专属分支、`_succeeded_wire`）、`fallback.py`（`_DROP_CANDIDATES`、`store_rejected`、`FieldAdjuster`）、
`runner_cli._honours_options` / `--fallback` 解析 / `--wire` 的 `choices=WIRES`、`ai_probe.PROTOCOLS` 与 `WIRES[protocol]`、`ai_providers.PROTOCOLS` / `_BUILTIN_WIRE` / `apply_runtime_report`（`used not in PROTOCOLS`）、
`fixsession.build_model`（`WIRES[name]`）、`ai_structured`（`model.wire.name` 进回报）、`ai_pricing`、`done` 事件 `wire_used` 闭集、`tests/test_error_codes.py`。
原则：**SIWC wire 不进 `ai_providers.PROTOCOLS`**（用户记录永远不能声明它）；进 `WIRES` 但 argparse 只在 flag 开时接受；每个分支加一条用例钉住它对 `siwc-responses` 的行为。

### 5. 另一条可能的路：Codex app-server 注入（记下，不做）

官方 `codex-app-server` 页给了把 SIWC 令牌经 `-c model_providers.openai_chatgpt_plan.env_key=ACCESS_TOKEN`、`requires_openai_auth=false`、`supports_websockets=false`
交给 Codex 的做法，与我们现有给 Codex 注入第三方 Key 的 `spawn_overrides` 同形。但它走 app-server RPC（不是 `codex exec`）、令牌续期要重启 app-server、
`tool_search` 配置会失败，而且会让 SIWC 依赖用户装了 Codex——与「不依赖 `codex login`」的目标相反。本方案只走内置执行器，CLI 注入留作以后评估。

### 6. 前端

`web/src/components/settings/connectAi/`：方式与连接全由后端 `vendors[]` / `connections[]` 驱动；`VendorRow.tsx:67` 用 `id.startsWith('sub:')` 判「订阅类不可管理」。
SIWC 连接 id 定为 **`siwc:chatgpt`**（不以 `sub:` 开头，与 `sub:codex` 的登录态、管理按钮判据都不混）。**flag 关时后端不产出这一行，前端零改动**；本阶段不加任何按钮。

---

## 二、最小改造（四个小 PR，全部在 flag 后，默认关）

Feature flag：`TAVOTTO_EXPERIMENTAL_SIWC=1`（只认环境变量，不进用户配置，不进设置页）。关 = 下面所有新代码不可达：
不产出连接 / 方式、`resolve_connection("siwc:chatgpt")` 404、runner `--wire siwc-responses` 拒绝起跑。

### PR-1 runner：SIWC Responses 适配器（纯新增 + 两个默认不变的钩子）

- `agent_runner/wires.py`：`Wire` 协议加两个**可选**方法 `decode(status, headers, data) -> dict` 与 `classify(status, data) -> RunnerFailure | None`；
  `HttpModel.complete` 有就用，没有就走原来的 `json.loads` / `classify_status`——三条旧 wire 行为逐字节不变。
- 新文件 `agent_runner/siwc_wire.py`：`SiwcResponsesWire`（`name = "siwc-responses"`）
  - `build`：白名单 `{model, instructions, input, tools, store:false, stream:true}`；忽略传入的 `max_output_tokens` / `effort`；回放剥 `reasoning` 条目；`Accept: text/event-stream`。
  - `decode`：增量解析 SSE（`event:` / `data:` / 空行分帧、`[DONE]`、注释行），有 `response.completed` → 取 `response`；
    补 `response.output_item.done` 累积（防 completed 不带 output）；`response.failed` → 按 `error.code` 闭集分类；`response.incomplete` → 交给现有 `parse` 的 incomplete 分支；
    无终态事件 = `network/stream_incomplete`（可重试：工具在客户端执行，重发无副作用）。
    **体积**：SSE 会把每段参数增量再发一遍，又不能发 `max_output_tokens`，长 `write_file` 很容易超过 `post_json` 的 8 MiB 上限。
    改为边读边解析、只留终态与 `output_item.done`，丢掉已消费的 delta；仍设硬上限（总字节 + 单条目），超了 `network/response_too_large`。
    单轮输出无上限 → 只能靠现有累计 token 预算与 900 s 时限兜住，截断续写路径（`max_tokens`）对该 wire 不可达（服务端不会因我们的上限截断）。终态后再解析出旧 `OpenAIResponsesWire.parse` 认的形状，**复用 parse**。
  - `classify`：上表闭集码 → `quota`（不重试）/ `network`（可重试，503 两种）/ `refused` + 闭集 detail `plan_not_eligible`（不重试、**不是 auth**；界面文案以后另定，别复用「模型拒答」那句）/ `internal`（unsupported_capability、route_not_supported、chatpass_v2_*：配置错，不重试）/ `auth`（invalid_user、无码 401）。
  - 不参与 `--fallback` 与 `FieldAdjuster`（runner 起跑时对该 wire 给了 fallback 直接 `bad_args`）。
- `runner_cli.py`：`--wire siwc-responses` 强制 `--auth bearer`、不收 `--fallback`。flag 由**父进程**判定（不派发就不会起跑）；runner 自己也拒绝，但不依赖环境变量是否穿过 `spawn_env()`。
- 测试（全 Mock，经注入的 `post` 缝断言，**在 `_adjuster().apply` 之后、包括重试与截断续写的每一个请求**）：单轮、多轮工具调用、工具错误回填、流中断、`response.failed` 用量超限、预流 429 / 503 / 403 not_eligible / 400 unsupported_capability、取消（流读到一半 cancel）、禁用参数（遍历官方清单）不出现、`role:system` 不出现、事件 / 日志 / `done` 里无令牌串（Redactor）。

### PR-2 Token Provider 接口（无真实 OAuth、无凭据落盘）

- 新文件 `engine/siwc_auth.py`（纯标准库，Flask 父进程可 import）：
  ```python
  class TokenProvider(Protocol):
      def access_token(self, *, min_valid_s: int) -> str: ...   # 不够新就刷新；刷不动 → NeedsLogin
      def invalidate(self, reason: str) -> None: ...           # 确认被拒（invalid_user / 终态刷新错误）才调
      def status(self) -> dict: ...                            # 只给 {signed_in, plan_usage_enabled, expires_at}，无令牌
  ```
  `min_valid_s` 默认 = runner 总时限（900 s）+ 余量，**不复用** `ai_oauth.REFRESH_MARGIN_MS`（10 分钟 < 一次任务最长 15 分钟，令牌会在任务中途过期）。
  刷新串行化（进程内锁 + 未来的文件锁，见官方「serialize refreshes」）。
  本阶段只有 `DisabledProvider`（恒 `NeedsLogin`）与测试用 `StaticProvider`。**不读取、不迁移 Codex CLI 的 `~/.codex/auth.json`**（看护用例：模块源码不出现该路径、不 import ai_agents 的 codex 登录读取）。
- 存储形状写进文档（每个 client_id + 已验证 `sub` 一份记录、0600、原子写），**不实现**。

### PR-3 连接与路由接入（flag 关 = 与 main 逐字节相同）

- `ai_connections`：照 #2103 `hosted_ai.virtual_record()` 的先例加 `siwc` 虚拟连接 `siwc:chatgpt`；只在 flag 开 **且** provider `status().signed_in` 时出现（本阶段 provider 恒未登录 → 永不出现）。
- `route_for` / `resolve_connection`：SIWC 只走 `builtin`，`TAVOTTO_AI_BUILTIN=0` → 走不通；**永不** `cli:codex`（CLI 也拿不到这个令牌）。
- `first_key_connection` / `ai_probe` / `live_models` / `add_connection` 四处显式排除 SIWC（见一.3）。
- `ai_bridge.run`：spawn 前 `provider.access_token(min_valid_s=…)`，令牌只进 `TAVOTTO_AGENT_API_KEY`；`NeedsLogin` → 现有 `ai_endpoint_needs_login`。
- 看护：flag 关时 `vendors()` / `connections()` / capabilities 三份 JSON 与 main **完全相等**（快照比对，不只是套件绿）；flag 开 + StaticProvider 时只多一行且 route = builtin。

### PR-4 文档

ADR 0147 修订（§六「流式回复不做」改为「SIWC wire 消费 SSE、缓冲解析、无流式界面」）+ 新 ADR（SIWC 预备：独立连接 id、flag、闭集错误、不自动换计费、Token Provider 契约）+ `ai-agent-bridge.md` 一节 + 隐私说明草稿（届时发版前定）。

---

## 三、严格不做（本阶段）

不加用户可见按钮 / 文案；不实现 OAuth 登录、不保存真实凭据；不读 / 不迁移 Codex CLI 令牌；不访问 `backend-api` 等未授权接口；不引入 DevKit 代码（我们自己写，文档里无 DevKit 内容）；
不改默认模型、登录入口、执行器路由、已有配置；不改 `anthropic` / `openai-chat` / `openai-responses` 三条 wire 的请求与解析；不重写 Agent Runner。

---

## 四、风险

1. **工具形状只有通用指南的示例**：SIWC 页只说「放进 namespace 或 `additional_tools`」，namespace 的 JSON 取自通用 function-calling 指南；SIWC 路由上接不接受、扁平工具会不会被拒，都没验证。工具打包集中在 `build` 里一个函数，真服务验证时只改这一处。
2. **`response.completed` 是否带完整 `output`、`response.output_item.done` 与 `response.incomplete` 的载荷**：SIWC 页与 streaming 指南都没写字段 → 两种都实现并测，标「未经真服务验证」。
3. **`reasoning` / `include` 未提及**：本阶段不发思考强度；以后要发需真服务确认。
4. **与 #2103 冲突**：两者都改 `loop.py` 的错误分类、`ai_connections` 的路由与 `vendors` 排序。拆开：PR-1 / PR-2 只需要十几行的 `_error_code`，可以先独立做（谁后合谁解冲突，`_error_code` 合并成一份）；
   PR-3 需要它的虚拟记录写法，**等 #2103 合入后再叠**，避免两套「虚拟连接」。
5. **令牌寿命**：访问令牌 1 小时、任务最长 900 s；margin 不足会在任务中途 401。PR-2 契约里钉死。
6. **额度被静默消耗**：任何「挑第一个能用的连接」都会选中 SIWC —— PR-3 逐个关，并加反向用例。
7. **1.0 纪律**：这是新产品能力，需维护者豁免；SSE 与 ADR 0147 §六 字面冲突，需要修订。

## 五、验收标准

| # | 标准 | 怎么验 | Mock / 真服务 |
| --- | --- | --- | --- |
| A1 | 现有 ChatGPT(Codex CLI) / Claude 登录 / API Key / MiniMax 账号登录行为不变 | 现有 `test_ai_connections` / `test_ai_agents` / `test_ai_login` / `test_ai_bridge` / `test_agent_runner*` / `test_ai_oauth` / `test_ai_probe` 全绿 + flag 关快照相等 | 本地 |
| A2 | 适配器单轮 / 多轮 / 工具调用 / 失败 / 取消 / 用量超限 | `tests/test_agent_runner_siwc.py`，Mock SSE | **Mock** |
| A3 | 不发官方禁止参数 | 在 `post` 缝对**每一个**发出的请求遍历禁用清单 | **Mock** |
| A4 | 改图 / 转换 / 回滚 / 数据完整性不退化 | 现有 `test_ai_figure_edit` / `test_ai_convert_app` / `test_ai_revert_stale` / `test_agent_runner_convert` 全绿 | 本地 |
| A5 | 凭据不进日志 / 前端状态 / 遥测 / 诊断包 | runner 事件 + `done` + `ai_providers.public` + capabilities + 遥测白名单里搜令牌串为 0 | **Mock** |
| A6 | flag 默认关、关了完全不可达 | flag 关：三份 JSON 快照相等、`siwc:chatgpt` 404、`--wire siwc-responses` 拒绝 | 本地 |
| A7 | 真实服务 | **本阶段不做**（不用真实 ChatGPT 账号）；以后按官方动态注册（`client_id=dynamic_agent_client` 首次换出 `oaiapp_…`）做，前提是第 0 条资格判过 | — |

## 六、待维护者拍板

0. **资格**：官方把这条免 client secret 的动态注册路径限定给「开源、本机运行的应用」；「付费或远程托管的应用」要填意向表。
   Tavotto 是 AGPL 开源、本机运行，但有付费版（ADR 0193）与闭源 Pro 扩展（ADR 0198）。文档对「开源应用带付费档」没表态。
   这一条要你先判（或先问 OpenAI）；本阶段的代码不联网、不注册，不受影响，但决定了以后能不能上线。
1. 给不给 1.0 豁免（新能力，默认关、无界面）？
2. 同不同意修订 ADR 0147 §六：「SIWC wire 消费 SSE、缓冲解析、不做流式界面」？
3. SIWC 的 PR-1 / PR-3 等 #2103 合入后再叠（推荐），还是先独立做、谁后合谁解冲突？
4. flag 名 `TAVOTTO_EXPERIMENTAL_SIWC`、连接 id `siwc:chatgpt` 可以吗？
