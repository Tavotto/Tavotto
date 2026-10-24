# ADR 0147：改图助手的内置执行器——自己跑「模型 ↔ 工具」循环

日期：2026-10-23 · 状态：**第一阶段（核心循环）已落地；第二阶段（注册进改图执行路径）已接入改图**（2026-10-24，「连接 AI」改造；1.0 豁免已由维护者给出）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)（§三 默认只改表现形式、§四 执行面不扩大、§六 Harness² 只借思想）、
[0015 Agent 注册表](0015-coding-agent-registry-and-settings.md)、[0143 生成可编辑版本](0143-convert-to-editable.md)。

## 问题

改图助手今天只能借用户本机的 Codex / Claude Code CLI：用户没装、没登录、或只有一个第三方模型服务的密钥时用不了；
CLI 的写入面（任意 shell、网络、绝对路径写）也比「改一个脚本的外观」大得多（04-security-model §三 的 R-03 只能事后发现）。

## 决定

### 一、一个独立子进程，只有四样工具

`python -m tavotto.engine.agent_runner` 在候选目录里跑一个循环：系统提示 + 用户指令 → 模型 → 工具调用 → 结果回填 → 直到模型给出最终文字。
`figure_edit` 的工具集只有 `read_file` / `list_dir` / `edit_file` / `write_file` / `check`；**没有 shell、网络、MCP**。路径一律 `realpath` 判在
候选目录内，写只许目标脚本；`edit_file` 的原文必须恰好出现一次（照 Claude Code 的 Edit 语义）。执行面比 CLI 小，是这条路的主要理由。
`data_to_figure` 将来的工具集没有写文件工具（0138 §四）。

### 二、三种线上协议 + 一个确定性 Demo

`anthropic` / `openai-chat` / `openai-responses`，纯标准库 urllib，出站 HTTPS 只经 `tlstrust`，不跟随重定向，响应体有上限。
`demo` 协议按剧本回放，不联网，端到端测试用（借 Harness²，0138 §六）。密钥只经环境变量，提示词只经文件 / stdin，事件不带内容。

### 三、失败不抛，结局写进 `done`

`reason` ∈ `completed | max_turns | budget | tool_error_loop | auth | quota | refused | network | cancelled`（外加兜底 `internal`）。`refused` = 服务端过滤 / 拒答。
预算恒等式（每轮入账之和 == 合计）不成立时记 `ledger_mismatch`，不抛。

### 四、第二套工具集：`convert_to_editable`

同一个循环、第二套任务工具集（`--task convert_to_editable --target <原脚本> --output <原名>_matplotlib.py`）。权限边界逐条对应
[0143 §二](0143-convert-to-editable.md)：原脚本永远不可写；Agent 只能在候选目录顶层**新建**那一个文件（`create_file` / `edit_file` / 整份覆盖这个新文件，
别的路径一律工具错误）；`--output` 起跑即校验、不合格 `done internal bad_output`（0 次请求）；`check` 是注入回调（语义 = 0143 的 `run_validation`），
最多 3 次（0143 的「校验运行 ≤ 3」），本阶段入口没有注入方。系统提示的产品要求引用 `ai_prompt_pack.convert_requirements()` 的同一份文字，不复制。
没有 `write_file`、没有 shell / 网络。

### 五、第二阶段：已接入改图（2026-10-24）

`ai_agents.BuiltinAgent`（id `builtin`）以**内部**执行器的身份接进 `ai_bridge.run`：不在 `AGENT_REGISTRY`、不出现在 capabilities 的 `agents[]`，
界面上也没有「执行器」——用户只看到「连接」，`builtin` 只经连接的 `route` 体现。命令是 `tavotto agent-run`（冻结产物同目录 `tavotto-cli`，源码 `-m tavotto`，与 D3 的 `fix-mcp` 同一启动器出处），
复用候选工作区 / TAF 校验与修正 / 事件流 / 取消 / 历史库，与 Codex / Claude Code 同一套会话生命周期；密钥只经环境变量，提示词经 stdin。

**路由规则**（用户决策 2026-10-24，视图见 `engine/ai_connections.py`）：

1. 订阅类连接（ChatGPT / Claude 的官方登录）→ 对应 CLI；
2. Key 类连接：该连接家族的 CLI 装着且可用，并且 `ai.builtin_default` 不为 true → CLI + 注入 provider（现状）；否则 → 内置执行器。没装任何 CLI 的用户因此直接可用；
3. `ai.builtin_default` 默认 false，**FigureBench 跑分不差于 Claude Code 之后改 true**；运维开关 `TAVOTTO_AI_BUILTIN=0|1` 强制；
4. 账号登录（Bearer 令牌）类只走 CLI；内置循环不能用的记录（明文 http 网关、缺型号）退回可用的 CLI。

AI 修复（ADR 0149）的内置循环同样改为取连接视图里第一个可用的 Key 连接。

### 六、仍然不做

转换任务（`convert_to_editable`）经内置执行器——它的 `check` 回调还没有注入方（`convert` + `builtin` 直接拒绝）；流式回复；默认开启（`builtin_default` 先 false）。

## 后果

* 用户有了不依赖 CLI 的改图路径；代价是我们自己维护三种协议的字段细节，需要用真实服务做兼容性回归（单独、显式计费的命令，不进 CI）。
* 模型生成 / 影响的文件改动仍不当作可信：候选目录 + 指纹 + 提交前检查（0138 §四）一个不少。
* 规则全文：`docs/rules/backend/ai-agent-bridge.md`「内置执行器」与「连接」两节；看护 `tests/test_agent_runner.py`、`tests/test_ai_connections.py`。
