# src-tauri/ — Tauri 桌面壳规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`；打包与内置 runtime 在 `packaging/AGENTS.md`，sidecar / 引擎侧在
`src/tavotto/AGENTS.md`。架构与安全模型的完整版在 `docs/adr/0002-tauri-desktop-shell.md`，改动前先读。
本层规则的**全文按主题**在 `docs/rules/desktop/`：先在下表按改动路径找到主题，读那一份细则
（各 1–4 KB）与它点名的 ADR，再动手。下表是生成的：改细则头部的
`agents-route` 块，再 `python scripts/dev/gen_agents_sheets.py --write`，别手改；这里不放第二份全文。

## 本层不可破坏

- **前端不走 Tauri frontendDist**：由 sidecar 的 Flask 提供；组件不得直接 import `@tauri-apps/*`，唯一桌面感知点是 `web/src/lib/desktop.ts`。
- **新增 `#[tauri::command]` 三处同步**：`build.rs`、该拿它的窗口的 capability、`generate_handler`；漏前两处会被静默拒绝。
- **权限是闭集**：远程窗口 `remote-*.json` 不许加本机文件类权限；`main.json` / `remote-*.json` 不得出现 `allow-rescue-*`；页面不给 `opener:*`。
- **退出路径必须先写 clean**：绕过 `RunEvent::Exit` 的出口只许经 `e2e_exit` / `updater_builder()`；关窗询问闸默认不拦、必须留看门狗。
- **救援页只在「从未进入编辑态」时可用**，只有 `rescue_diag_send` 联网且由用户单独点击。
- **更新归壳**：桌面关掉 `/api/update/*`；webview 不拿 `updater:*`；验签永远用内置公钥；macOS 更新包签名公证后重做。
- **壳内文案不手写产品名**；改菜单文案要改两处。

<!-- BEGIN GENERATED: agents-routes -->
| 改到 | 主题（细则在 `docs/rules/desktop/`） | 必守要点 |
| --- | --- | --- |
| `src-tauri/src/main.rs`、`build.rs`、`capabilities/`、`src/tavotto/desktop.py`、`web/src/lib/desktop.ts`、`sidecar/port_memory.rs` | 壳 → sidecar 进程关系、外链、`#[tauri::command]` 的 ACL 三处同步 → `process-and-acl.md` | 前端仍由 sidecar Flask 提供；组件不得 import `@tauri-apps/*`；新增 command 必须 build.rs / capabilities / generate_handler 同步 |
| `CloseGate`、`CloseAsk`、`hold_window()`、`Sidecar::poll_exit()`、`boot_engine`、`session-state.json`、`exit_guard_tests.rs` | 关窗询问闸（issue #223）与引擎意外退出标记（ADR 0129） → `close-gate-and-engine-exit.md` | 关窗闸默认不拦、必须留看门狗；绕过 `RunEvent::Exit` 的退出必须先写 clean（`e2e_exit` / `updater_builder()`） |
| `src-tauri/src/rescue.rs`、`src-tauri/src/shell_report.rs`、`shell/error.html`、`capabilities/rescue-page.json`、`tests/golden/diag_shell_report.json` | sidecar 起不来时的原生救援页 → `rescue-page.md` | 只在「从未进入编辑态」时可用；只许主窗口壳自带 `error.html` 调用，`remote-*.json` 不得出现 `allow-rescue-*` |
| `window_chrome.rs`、`fullscreen_observer.rs`、`native_drop.rs`、`drop_paths::classify`、`tests/golden/drop_script_rule.json` | 窗框与拖放（ADR 0092） → `window-chrome-and-file-drop.md` | `disable_drag_drop_handler()` 必须留着；路径分派只在 `drop_paths::classify`；Windows 主窗口 `decorations(false)` |
| `capabilities/remote-*.json`、`REMOTE_WINDOW_MARKER`、`main.rs::parse_open_args()`、`handoff.desktop_argv()` | 远程实例窗口（ADR 0105）与 `--open` 交接契约 → `remote-window-and-handoff.md` | 远程窗口权限是闭集，不许加本机文件类权限、不装 native_drop；`--open <目录> [--stem]` 改一边同步另一边 |
| `src-tauri/src/app_update.rs`、`tauri.conf.json`、`capabilities/main.json`、`scripts/make_updater_manifest.py`、`MIRROR_MANIFEST_URL` | 桌面更新：tauri-plugin-updater 与来源编排（ADR 0121） → `update-channel.md` | 桌面关掉 `/api/update/*`；webview 不拿 `updater:*`；验签永远用内置公钥；首个来源说「无更新」即终局 |
| `scripts/build_desktop.py`、`scripts/smoke_desktop.py`、`desktop-tauri.yml`、`src-tauri/windows/installer.nsi`、`assets/brand/dmg-background.png` | 桌面构建 / 验收、dmg 与 NSIS 安装界面 → `build-acceptance-and-installer.md` | `desktop-tauri.yml` 是唯一桌面发行链；NSIS 覆盖安装 / `/UPDATE` / `/P` 跳过目录页；`@tauri-apps/cli` 钉死 2.11.4 |
| `src-tauri/Cargo.toml` 的 `[profile.release]`、`crash_dump.rs`、`desktop-tauri.yml`、`workerd/Cargo.toml` | 调试符号与 Windows 小型转储 → `debug-symbols-and-minidump.md` | 符号另存 `debug-symbols-*` artifact、不进安装包与更新清单；`main()` 第一句装过滤器、过滤器内不分配堆不加锁 |
| `src-tauri/src/i18n.rs`、`build.rs`、`connect.html`、splash / error 页 | 桌面壳自带文案 → `shell-i18n.md` | 改菜单文案要改两处，菜单项 id 与加速键一个字节不动；壳内文案不手写产品名，由 `build.rs` 编译期注入 |
<!-- END GENERATED: agents-routes -->

## 验证

- 针对性：`.venv/bin/python -m pytest tests/test_desktop_*.py`（看护用例清单见细则）。
- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py --sidecar dist/Tavotto/Tavotto`。
- 完整验证链见 `.github/AGENTS.md`。
