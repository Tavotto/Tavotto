# ADR 0147：改图助手的内置执行器——自己跑「模型 ↔ 工具」循环

日期：2026-10-23 · 状态：**提议**（1.0 豁免已由维护者给出；方案页已批准；第一阶段只落核心循环模块）
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

### 五、本阶段不做

接进 `ai_bridge` / `ai_agents` / 设置页（等在飞的 PR 合入后另起阶段）；`check` 回调的真实实现（复用 TAF 的候选检查）；
流式回复；打包进冻结产物（`python -m` 在冻结包里怎么起，另一阶段决定）；默认开启。

## 后果

* 用户有了不依赖 CLI 的改图路径；代价是我们自己维护三种协议的字段细节，需要用真实服务做兼容性回归（单独、显式计费的命令，不进 CI）。
* 模型生成 / 影响的文件改动仍不当作可信：候选目录 + 指纹 + 提交前检查（0138 §四）一个不少。
* 规则全文：`docs/rules/backend/ai-agent-bridge.md`「内置执行器」一节；看护 `tests/test_agent_runner.py`。
