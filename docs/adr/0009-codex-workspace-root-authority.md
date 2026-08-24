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
版本/协议、initialize 实际声明的 capability 名称，以及 Roots/工作区确认状态。
它是诊断记录，不从版本号猜 capability。

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

已有 session 每次使用前重新规范化项目路径并检查当前根；host 换根、连接重新授权，
或项目目录被替换成指向范围外的 symlink/junction 后，旧项目不再在范围内就删除该
session，并回 `workspace_root_changed`。重新规范化失败或项目已不再是目录也按越界
fail-closed，不能让保存下来的词法路径继续授权 worker 重启。

### 3. server→client 请求必须留在原始请求窗口里

stdio 改成一个 reader pump：读取线程持续收帧，处理线程在活跃 `tools/call` 内有界
等待 `roots/list` 或 `elicitation/create` 的对应 response，其余已经读到的消息按原序
放进 deferred queue。这样既不死锁，也不在 notification 后发送没有 originating
request 的野请求。

Roots 探针超时 2 秒；真人确认超时 300 秒。EOF、错误 response 与迟到/不匹配 id
都不能改变授权状态。

## 验证边界

自动化分三层：

- `tests/test_mcp_roots.py`：来源优先级、路径校验、连接生命周期、capability probe；
- `tests/test_mcp_server.py`：完整双向 JSON-RPC，分别覆盖 Roots 与 elicitation，
  并断言 open 的 MCP App metadata；
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
