# 窗框：macOS 全屏让位、Windows 无标题栏与主页拖放

> 原文出自 `src-tauri/AGENTS.md`「进程关系与安全边界（2026-08-17，与浏览器模式并行）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 40
改到: `window_chrome.rs`、`fullscreen_observer.rs`、`native_drop.rs`、`drop_paths::classify`、`tests/golden/drop_script_rule.json`
主题: 窗框与拖放（ADR 0092） → `window-chrome-and-file-drop.md`
必守要点: `disable_drag_drop_handler()` 必须留着；路径分派只在 `drop_paths::classify`；全屏事件以 NSWindow 通知为主信号；Windows 主窗口 `decorations(false)`、窗口命令只在 `main.json`
-->

- **macOS 全屏收起顶栏让位**（`window_chrome.rs`）：主信号是 `fullscreen_observer.rs` 听的 NSWindow will/did 全屏通知（will-exit 立刻发 false、did-enter 才发 true：红绿灯在屏上时绝不收回占位；`Resized` 只作兜底且观察者在位时只许报 false）；`FullscreenTracker` 去重，真变了才向主窗口发
  `tavotto:fullscreen`（`{fullscreen}`）；`current_fullscreen` 回读防事件早于监听（仅主窗口，ACL 三处同步）。
  前端 `useWindowFullscreen`（非 `overlay` 窗框恒 false）让 TopBar 撤掉 `TRAFFIC_LIGHTS_INSET`。
  事件名 / 命令 / ACL 由 `tests/test_desktop_window_chrome.py` 看护。
- **Windows 主窗口无系统标题栏**（`window_chrome.rs`，窗框 `menu-button`，2026-10-21）：非 macOS 的主窗口
  `decorations(false)`，与 `chrome()` 同一个 cfg；最小化 / 最大化 / 关闭由页面 `WindowControls`（`main.tsx` 单独一个根，
  崩溃页 / 会话失败页也在）画，壳自带的 splash / error 页各内联一组；关闭走 `close()` 照旧进关窗询问闸。窗口命令只在
  `main.json`，远程实例窗口维持系统标题栏。看护同上。
- **主页拖放拿真实路径**（ADR 0092）：`disable_drag_drop_handler()` 必须留着（装上 Tauri 的处理器
  页面 HTML5 拖放整片失效）；macOS 上 `native_drop.rs` 只旁听 `performDragOperation:` 读路径、发
  `tavotto:file-drop`、再调原实现。路径分派只在 `drop_paths::classify`（绝对 + canonicalize，Rust 单测；`.py` > 外语脚本 `.R`/`.r`/`.m` > 文件夹，ADR 0143 修订二），
  视图分派在前端（只有主页订阅）；两侧同源由 `tests/test_desktop_file_drop.py` 看护，「什么算脚本」与页面
  `scriptImport.ts` 同源由 `tests/golden/drop_script_rule.json`（两侧各读）看护。
