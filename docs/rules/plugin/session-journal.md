# 会话跨进程恢复（ADR 0078）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「会话跨进程恢复（2026-09-23，ADR 0078）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **会话不再只活在 server 进程内存里**：提交点（open 结束 / apply 结束 / 规范化收尾，含回退）
  经 `tavotto_mcp/sessionjournal.py` 落 `data_dir()/mcp-sessions/<id>.json`；内存未命中时
  `get_session` → `_restore_session` 按记录在本进程重建。起因是宿主会换进程（WorkBuddy 一轮后
  回收 CLI、Codex 改配置重启 server）。`_render` 本身不写盘，规范化中途的候选不落盘。
- **记录不能自证权限**：恢复先用**当前连接**的 `RootAuthority` 校验项目，越界 `workspace_root_changed`
  且**拒在渲染之前**；脚本 / 入口不存、从注册表重读，stem 不在了 `session_restore_failed`。
- **明确释放的不复活**：`close_session` 与 `_evict_if_needed` 连记录一起删。结果里的
  `restored: true` 只报一次（`_take_restored`），文字带 `server.RESTORED_NOTE`。
- 落盘失败不让工具调用失败，但写 stderr。`sessionjournal` 只用标准库——不动 `_BRIDGE_IMPORT` 三处同源。
- 看护：`tests/test_mcp_session_journal.py`、`tests/test_mcp_roundtrip.py` 的两进程接力。
