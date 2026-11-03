"""改图助手的内置执行器（ADR 0147，提议）：自己跑「模型 ↔ 工具」循环，不借 Codex / Claude Code CLI。

本包**只有核心循环**（第一阶段）：协议层 `wires`、工具层 `tools`、循环与预算 `loop`、进程入口 `__main__`。
还没有接进 `ai_bridge` / `ai_agents` / 设置页——那些在飞的改动合入后另起阶段。

边界（`docs/rules/backend/ai-agent-bridge.md`「内置执行器」一节）：

* 纯标准库、Python 3.10 可跑；**不 import 科学栈、不 import `ai_bridge` / `ai_agents` / `ai_tasks`**（本包跑在
  独立子进程里，只经 stdout 的事件行与父进程说话）；唯一的内部依赖是 `tlstrust`（出站 HTTPS 的信任来源）。
* 密钥只经环境变量 `TAVOTTO_AGENT_API_KEY`；提示词只经文件 / stdin；事件与 stderr 不带文件内容、提示词、密钥。
* 失败一律不抛：结局写进最后一条 `done` 事件。
"""
