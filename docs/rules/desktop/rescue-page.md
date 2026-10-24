# 原生救援页（ADR 0130）

> 原文出自 `src-tauri/AGENTS.md`「进程关系与安全边界（2026-08-17，与浏览器模式并行）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `src-tauri/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：只在「从未进入编辑态」时可用；只许主窗口壳自带 `error.html` 调用，`main.json` / `remote-*.json` 不得出现 `allow-rescue-*`；重新尝试 = 重启整个应用；只有 `rescue_diag_send` 联网

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 30
改到: `src-tauri/src/rescue.rs`、`src-tauri/src/shell_report.rs`、`shell/error.html`、`capabilities/rescue-page.json`、`tests/golden/diag_shell_report.json`
主题: sidecar 起不来时的原生救援页 → `rescue-page.md`
必守要点: 只在「从未进入编辑态」时可用；只许主窗口壳自带 `error.html` 调用，`remote-*.json` 不得出现 `allow-rescue-*`
-->

- **原生救援页**（ADR 0130，`src-tauri/src/rescue.rs` + `src-tauri/src/shell_report.rs` + `shell/error.html`）：sidecar 起不来时由壳自己检查修复版本、重新尝试、发送最小启动诊断。
  四条别改坏：① **只在「从未进入编辑态」时可用**——`RescueState.entered` 在 `boot_engine` 导航到应用页**之前**置位、永不复位，置位后七条 `rescue_*` 命令一律拒绝
  （`editing_state_unknown`），不靠超时推断干净；② **只许主窗口的壳自带 `error.html` 调用**（`rescue::decide` 查 label + 当前 URL，ACL 之外再查一道）；
  capability 是独立的 `rescue-page.json`（`local:true`、无 remote、`windows:["main"]`），`main.json` 与 `remote-*.json` 不得出现 `allow-rescue-*`；
  ③ **更新走同一个协调器**（同一个 `UpdateState`，安装带 `expected_version`，对不上 `candidate_changed`）；
  ④ **重新尝试 = 重启整个应用**（收 sidecar → 写 clean → `restart()`），不做同进程重试。shell 报告只含闭集字段（`STAGES` / `CODES`），不收 `msg` / 日志路径 / 环境变量；
  **只有 `rescue_diag_send` 联网**，由用户单独点击触发；端点是编译期常量，环境变量只能关。协议常量、失败码、上传地址向量、包内容以
  `tests/golden/diag_shell_report.json` 为唯一出处（Rust 与 `tests/test_diag_shell_contract.py` 各读）。发行边界：只能救援已带此功能且壳能运行的客户端。
