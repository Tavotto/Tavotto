# 进程关系、桌面感知点与 Tauri ACL

> 原文出自 `src-tauri/AGENTS.md`「进程关系与安全边界（2026-08-17，与浏览器模式并行）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 10
改到: `src-tauri/src/main.rs`、`build.rs`、`capabilities/`、`src/tavotto/desktop.py`、`web/src/lib/desktop.ts`、`sidecar/port_memory.rs`
主题: 壳 → sidecar 进程关系、外链、`#[tauri::command]` 的 ACL 三处同步 → `process-and-acl.md`
必守要点: 前端仍由 sidecar Flask 提供、不走 frontendDist；组件不得 import `@tauri-apps/*`；新增 command 必须 build.rs / capabilities / generate_handler 三处同步，漏前两处被静默拒绝；opener 必须 `open_js_links_on_click(false)`
-->

- **进程关系**：Tauri 壳（`src-tauri/`）→ spawn `tavotto --desktop-sidecar`
  （PyInstaller onedir，无 matplotlib）→ 现有 worker 协议。前端仍由 sidecar 的
  Flask 提供，**不走 Tauri frontendDist**——桌面与浏览器跑同一份界面。
- 会话认证与桌面/浏览器共用一道边界（ADR 0008），细节见
  `docs/rules/backend/session-auth.md`。
- **桌面模式差异收在 `src/tavotto/desktop.py`**：端口优先壳记住的那个
  （`app_config_dir()/desktop-port`，stdin 首行 `preferred_port`；占不到限时重试后
  退回 `127.0.0.1:0`；连续 3 次落空才改记，策略是 `sidecar/port_memory.rs` 的纯函数；
  字段与 Python 侧严格同源，ADR 0108——端口一变 localStorage 整个换一份）、
  werkzeug 线程 server（可优雅 shutdown）、nonce 走 **stdin 首行**
  （环境变量对同用户进程可见；桌面**不写**完整磁盘凭据，只写 scoped 只读的 handoff-status 文件（ADR 0008 修订），实例复用由壳的
  单实例 argv 转发负责）、握手文件（无密钥、原子写、退出清理）、
  stdin EOF + 父 PID 双路「壳没了就自杀」（`test_desktop_sidecar.py` 看护）。
- **外链（`<a target=_blank>` / `window.open`）全由壳在 Rust 侧交给系统浏览器**：`navigation_allowed` → `OpenExternal`
  与 `on_new_window` 两条原生路径，失败经 `open_external` 记日志。opener 插件必须 `open_js_links_on_click(false)`——
  它默认注入的 click 监听会先 `preventDefault` 再走 ACL 拒绝的 IPC，两条原生路径永远等不到（beta 0.19.0b2「支持 Tavotto」
  点了没反应的真根因，#1288 只修了 `window.open`）。capability 不给页面 `opener:*`；Rust 单测
  `the_opener_plugin_never_swallows_link_clicks` 看护。
- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- **Tauri 2 的 ACL 对应用自定义命令同样生效**：新增 `#[tauri::command]` 必须
  三处同步——`build.rs` 的 `AppManifest::commands`、`capabilities/` 里**该拿它的
  那个窗口**加 `allow-<命令名连字符化>`（主窗口 `main.json`，远程实例窗口
  `remote-*.json`）、`main.rs` 的 `generate_handler`。漏掉前两处
  invoke 会被**静默拒绝**（reveal_export「点了没反应」就是这么坏的）；
  失败路径不许吞——回退时把完整文件路径告诉用户。三处齐全的看护读结构不读子串
  （`tests/support/rustsrc.py`，枚举在 `tests/test_desktop_codex_button.py`）。
