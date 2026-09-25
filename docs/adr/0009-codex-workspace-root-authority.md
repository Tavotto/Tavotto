# ADR 0009：Codex 工作区根的可信传递与连接内确认

状态：已实施；真实 Codex Desktop 视觉证据按
[`../acceptance/codex-desktop-canvas.md`](../acceptance/codex-desktop-canvas.md) 收集
（2026-08-24）

相关：[0006 Codex MCP App](0006-codex-mcp-app-and-publication-profile.md)

## 背景

安装后的 MCP server 从插件缓存目录启动。这个 cwd 不是用户工作区，不能拿来授权
读取任意图表；而 `tavotto_open_figure.project_path` 来自模型，它只能是候选目标，
不能反过来成为自己的授权证明。

真实 Codex 0.149.1 的 initialize 握手使用 MCP `2025-06-18`，声明
`elicitation`，但不声明 `roots`。因此只实现 `roots/list` 仍会让零配置打开失败。
同时，MCP Roots 已于协议版本 `2026-07-28` 弃用：兼容旧 host 是必要的，把它当
唯一长期入口则不是。

## 决策

### 1. 一个 `RootAuthority` 负责全部路径授权

授权来源按下面顺序选择，后面的来源不能扩宽前面的边界：

1. `TAVOTTO_MCP_ROOTS`：服务器所有者的显式配置；即使配置无效也 fail-closed；
2. host 在 initialize 中声明 `roots` 后，由关联的 `roots/list` 返回的本地目录；
3. host 声明 `elicitation` 时，由用户确认的一个精确目录；
4. 白名单里的 host workspace 环境变量；
5. 安全 cwd（不得是插件目录或文件系统根）。

所有入口都转成 realpath，只接受已存在的本地目录，去重，并拒绝文件系统根与
插件缓存目录。相对路径只有在恰好一个可信根存在时才解析；多根时回
`ambiguous_workspace_root`。

`tavotto_health.root_authority` 暴露来源、规范化根、generation、警告、client 名称/
版本/协议、initialize 实际声明的 capability 名称、Roots/工作区确认状态，以及当前
这次失败属于哪一档（`authorization`）。它是诊断记录，不从版本号猜 capability。

### 2. 模型给候选，用户给权限

当没有更高优先级的根、host 不支持 Roots 但声明 `elicitation`，且第一次
`tavotto_open_figure` 传入一个绝对、已存在的路径时：

1. server 把目标规范化；目录就确认自身，文件就确认其父目录；
2. 拒绝文件系统根、插件缓存、相对路径、不存在的路径；
3. 在这次 `tools/call` 尚未结束时发送关联的 `elicitation/create`；
4. 确认框显示完整规范路径，boolean 缺省为 `false`；
5. 只有 response 同时满足 `action == "accept"` 与 `content.approve == true` 才绑定；
6. 授权只活在当前 MCP 连接内；重新 initialize/重启 server 自动清掉。

拒绝、取消、超时、协议错误全部 fail-closed，并返回不同的
`workspace_confirmation_*` code。错误明确要求代理不要自动循环重试。显式
`TAVOTTO_MCP_ROOTS` 或已声明的 Roots 边界不能被 elicitation 扩宽。

### 2b. 授权失败分档（2026-09-03，issue #173）

真实用户报告：宿主声明了 `elicitation`，Codex UI 里却从没出现任何提示，而 server
把这次「没回应」报成了用户拒绝。用户于是被要求「再点一次」——根本没有框可以点。
**「宿主没回应」是独立一档**，不能并进「用户拒绝」这类相邻取值：两者的处置正好
相反。

`roots.WORKSPACE_FAILURES` 是「授权失败长什么样」的唯一出处：一个稳定 `code` ↔ 一个
`disposition`（谁该动手，闭集）↔ 一句说得出下一步的措辞。工具错误里同时带
`code` / `disposition` / `recovery`，**`code` 只作机器标识，不当文案**。

| `disposition` | 何时 | `code` |
| --- | --- | --- |
| `ask_user_again` | 用户看着确认框作了选择，或批准的目录在授权落地前变了 | `workspace_confirmation_declined` / `_cancelled` / `_stale` |
| `fix_host_wiring` | 宿主声明了 `elicitation`/`roots`，却超时、断开、回错误，或不弹框就替用户回了拒绝（§2c） | `workspace_confirmation_no_response` / `_error` / `_auto_declined`、`workspace_roots_no_response` / `workspace_roots_error` |
| `narrow_the_path` | 路径不在允许的根之内（错误里列出允许的根） | `path_out_of_scope` |
| `configure_roots` | 宿主既没给工作区目录，也不支持确认 | `no_workspace_root` |
| `send_absolute_path` | 还没有可以展示给用户的绝对路径，或多根下传了相对路径 | `workspace_confirmation_required` / `ambiguous_workspace_root` |

传输层失败（超时、EOF、没有可等待的传输）与宿主的错误响应在内部是两个状态
（`no_response` / `error`），因为「一声不吭」和「明确回了错」查的地方不一样。

已有 session 每次使用前重新规范化项目路径并检查当前根；host 换根、连接重新授权，
或项目目录被替换成指向范围外的 symlink/junction 后，旧项目不再在范围内就删除该
session，并回 `workspace_root_changed`。重新规范化失败或项目已不再是目录也按越界
fail-closed，不能让保存下来的词法路径继续授权 worker 重启。

### 2c. 宿主代答的拒绝不是用户拒绝（2026-09-25）

用户反馈（与 #38 的 2026-08-25 跟进同一现场）：宿主声明了 `elicitation`，框没出现，
server **立刻**收到 `decline`，于是报 `workspace_confirmation_declined`、叫人再点一次。
这是 §2b 同一个缺陷的另一种形态：宿主没回应改成了宿主**替用户**回应。

根因在宿主的审批设置（codex-rs `core/src/session/mcp.rs`）：`approval_policy = never`
（桌面版权限下拉里的「完全访问」）或 granular 策略没开 `mcp_elicitations` 时，Codex 不把
elicitation 交给界面，直接回 `decline`。本机实测 codex-cli 0.155.1：`never` 与 `codex exec`
（无论 `on-request` 与否）都在 **0 ms** 内回 `{"action": "decline"}`，不带 `_meta`；新版源码
在代答时带 `_meta.approvals_reviewer = "auto_review"`。

server 看不见宿主的审批设置，只能看**作答的那一方**：`decline` / `cancel` 快于
`HUMAN_RESPONSE_FLOOR_S`（1 s，真人要先看见框、读完整路径），或带 `auto_review` 标记，就归
`workspace_confirmation_auto_declined`（`fix_host_wiring`），恢复话术点名「完全访问 → 请求批准」
与 `TAVOTTO_MCP_ROOTS` 两条路。误判两头不对称：真人 0.9 s 内的拒绝被报成宿主代答，只是多一句
关于权限设置的话；反过来就是 #173 的原样。授权本身不放宽：`accept` 分支一字未改，代答的
拒绝照样 fail-closed。

同一轮核过「宿主自动传项目根」这条路：Codex 0.155.1 的 `tools/call._meta.x-codex-turn-metadata`
只有 session / sandbox / 模型等字段、没有工作区路径，也不给 MCP 子进程设任何 `CODEX_*`
环境变量——§1 第 4 档那几个变量名在这一版上不会命中，这条路今天不存在。

### 3. server→client 请求必须留在原始请求窗口里

stdio 改成一个 reader pump：读取线程持续收帧，处理线程在活跃 `tools/call` 内有界
等待 `roots/list` 或 `elicitation/create` 的对应 response，其余已经读到的消息按原序
放进 deferred queue。这样既不死锁，也不在 notification 后发送没有 originating
request 的野请求。

Roots 探针超时 2 秒；真人确认超时 300 秒。EOF、错误 response 与迟到/不匹配 id
都不能改变授权状态。

## 验证边界

自动化分三层：

- `tests/test_mcp_roots.py`：来源优先级、路径校验、连接生命周期、capability probe、
  失败分档（每个确认状态各选中自己那一档）；
- `tests/test_mcp_server.py`：完整双向 JSON-RPC，分别覆盖 Roots 与 elicitation，
  断言 open 的 MCP App metadata，并在真 stdio 帧上跑四档失败（拒绝 / 没弹框 /
  越界 / 没配），要求四个 code、四种处置两两不同；
- `tests/test_mcp_stdio.py`：真 server 子进程、真 stdio 双向请求，防止直接函数调用假绿。

真实 Codex CLI 探针必须另记：它证明 host 的 initialize 能力与非交互取消行为，不能
证明 Desktop iframe。真实 Desktop 通过的唯一判据是：新任务中用户看见并批准精确路径，
随后 `tavotto_open_figure` 的工具卡实际渲染 `ui://tavotto/canvas/v1.html`，且截图与
工具结果可对应。详细步骤和证据字段见验收文档。

## 代价

- 首次访问一个新目录多一次明确确认；安全边界因此可见，而不是藏在模型参数里。
- 非交互 `codex exec` 会取消 elicitation，零配置 open 按设计失败；自动化应使用
  fake host 验协议，或显式配置 `TAVOTTO_MCP_ROOTS`，不能伪造用户批准。
- Roots 兼容代码仍保留，但在 health 中标成 `compatibility_only` 与弃用日期，便于未来
  删除而不把它误认为长期主路径。

## 协议依据

- [MCP 2025-06-18 Elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation)
- [SEP-2260：server request 必须关联 originating request](https://modelcontextprotocol.io/seps/2260-Require-Server-requests-to-be-associated-with-Client-requests)
- [SEP-2577：弃用 Roots、Sampling 与 Logging](https://modelcontextprotocol.io/seps/2577-deprecate-roots-sampling-and-logging)
- [OpenAI：MCP Apps UI resource/tool metadata](https://developers.openai.com/plugins/build/chatgpt-ui)
