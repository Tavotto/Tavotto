# 非 Codex 宿主

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「非 Codex 宿主（2026-09-24，全文 `docs/implementation/multi-host-mcp/README.md`）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **同一份完整包、同一个启动器、同一份 Skill**：`integrations/configure.py`（纯标准库，进
  `pluginmanifest.STAGE_REQUIRED`——只卡新 staging；**不进** `REQUIRED`，否则体检会把没有它的旧版已装插件报成损坏）从自己的位置找 `mcp/server.py`，按 `--host` 打印那一家的配置片段，
  **不写任何文件**。宿主差异只在它的 `HOSTS` 表（顶层 key / 额外字段 / 超时单位 / 落点 / Skill 入口）；
  字段依据与查证日期在 `docs/implementation/multi-host-mcp/hosts.md`，文档证明不了的字段不加。
- 授权只来自 `--project-root`（写进 `TAVOTTO_MCP_ROOTS`）；根 / HOME / 包目录拒绝。启动器探针与
  `launcher_starts()` 同一把尺（跑 `--health` 要体检 JSON）；引擎只在当前 shell 环境里找得到时才钉
  `TAVOTTO_MCP_PYTHON`，钉完在最小环境里再验。超时唯一出处是 `.mcp.json` 的 `tool_timeout_sec`，按单位换算。
- 启动器 / roots 的恢复话术不再只说「新开 Codex 会话」（`RELOAD_HINT` / `RESTART_HOST`），恢复命令写
  真实绝对路径。`tavotto_health` 多了 `server`（实际包目录 / 版本）与分层 `checks`——
  `host_ui_rendered` 永远是 `unknown_to_server`，不从资源在推断画布已显示。
- 看护：`tests/test_mcp_configure.py`（解包到树外按生成配置真起 server）、`tests/test_mcp_host_profiles.py`
  （八个 profile 的独立期望）、`tests/test_plugin_candidate.py` 末条（真实候选）。
