"""引擎对外宣告的能力标记（T10）：新客户端在发送新字段**之前**先问引擎会不会，不会就明确拒绝。

为什么需要：旧端点不认识的 JSON 字段会被静默忽略。新前端把 `argv` 发给不认识它的旧引擎，旧引擎照样
无参数运行，回包看起来一切正常——用户以为跑的是带参数那一版。插件（MCP）也一样：它可能配着一份更早
安装的 Tavotto（插件版本 ≠ 已装引擎版本，见 `codex-plugin/AGENTS.md`）。所以协商只有一条规矩：

* **先问再发**。客户端只在本表里有对应标记时才发送新字段；没有就当场以 `engine_capability_missing`
  拒绝，**一次都不先「试着无参数跑」**。
* 标记只增不改名：旧客户端不读这张表，行为不变（空 argv / 旧终局逐字节照旧）。
* 标记只说「这一版会什么」，不带项目、机器或用户信息——`/api/version` 是公开端点。

出口：HTTP `GET /api/version` 的 `features`（与桌面壳连远程实例用的 `desktop-remote-window` 同一个列表）；
MCP `tavotto_health` 的 `engine_features`，桥里按需 import 本模块（旧引擎没有本模块 = 一个标记都没有）。
"""

from __future__ import annotations

#: 运行入口接受精确 argv（一项一个 token）与不透明运行配置引用 `rc_…`，并在回包 / 回执里用
#: `run_config` 明确承认这次用的是哪份配置（T03）。没有它：argv 会被静默丢掉，按无参数运行。
SCRIPT_ARGV = "script-argv"
#: 准备会话（`/api/engine/preparation-sessions`，T01 / T09）：检查与运行是两个动作，动作由后端生成。
PREPARATION_SESSIONS = "preparation-sessions"
#: 环境建议上的显式采用：候选 id + 用户看到的环境代（T05，ADR 0114）。没有它：旧引擎在首开时静默采用。
ENVIRONMENT_ADOPTION = "environment-adoption"

#: 本引擎会的全部标记（顺序固定，只增不改名）。
FEATURES: tuple[str, ...] = (SCRIPT_ARGV, PREPARATION_SESSIONS, ENVIRONMENT_ADOPTION)

#: 客户端没拿到所需标记时回的稳定码（HTTP 前端 / MCP 桥同一个）。
ERROR_CAPABILITY_MISSING = "engine_capability_missing"


def features() -> list[str]:
    """公开投影：一份新列表（调用方改它不影响本表）。"""
    return list(FEATURES)
