# 关窗询问闸、引擎监视与退出标记

> 原文出自 `src-tauri/AGENTS.md`「进程关系与安全边界（2026-08-17，与浏览器模式并行）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 20
改到: `CloseGate`、`CloseAsk`、`hold_window()`、`Sidecar::poll_exit()`、`boot_engine`、`session-state.json`、`exit_guard_tests.rs`
主题: 关窗询问闸（issue #223）与引擎意外退出标记（ADR 0129） → `close-gate-and-engine-exit.md`
必守要点: 关窗闸默认不拦、必须留看门狗、`CloseAsk` 四档；绕过 `RunEvent::Exit` 的退出必须先写 clean（`e2e_exit` / `updater_builder()`）；`restart_engine` 仅主窗口
-->

- **关窗询问闸**（issue #223，ADR 0002 的「关窗询问闸」一节）：
  `WindowEvent::CloseRequested` → `CloseGate` → 事件 `tavotto:close-requested`
  → 前端答 `hold`/`close`/`cancel`。三条别改坏：**默认不拦**（前端 arm 之后才拦，
  splash/error 页没有监听器）、**超时只针对「前端有没有接手」**——而「接手」有
  两种结局，`CloseAsk` 的四档存在正是为了让看门狗分得出「没人接手 / 说继续关 /
  **说取消**」（压成一个 bool 会让点了取消的窗口两秒后自己关掉）、
  **必须留看门狗**（没有它 = 一个关不掉的窗口；拦的唯一入口是 `hold_window()`，
  行为有 Rust 单测钉住，别退回「在源码里搜 token」那种空门禁）。
  ⌘Q 与系统注销不走这条路。两侧同源由 `tests/test_desktop_close_guard.py` 看护。
- **引擎监视、重启与退出标记**（ADR 0129）：握手后监视线程约 1 s `Sidecar::poll_exit()`（shutdown 取走 child 后回 `None` = 正常关闭）；
  意外退出写标记、发 `tavotto:engine-exited`、`current_engine_exit` 回读；`restart_engine`（仅主窗口，远程窗口**无**）与首启共用 `boot_engine`
  （新 nonce，`AppState.port` 可变）。`app_config_dir()/session-state.json` 只含版本 / 时间 / running|clean / 退出类型闭集，不上传；
  `take_previous_exit` 取走即清空。**绕过 `RunEvent::Exit` 的退出路径必须先写 clean**：`std::process::exit` 只许经 `e2e_exit`，
  `UpdaterBuilder` 只许经 `updater_builder()`（`on_before_exit`）——`exit_guard_tests.rs` 用 syn 扫 `src/` 全部生产 `.rs` 的 AST（不做 `use` 别名解析：`use` 里出现 std 的 process 段或导入 exit 即 hit，任何末段是 exit 的路径也是 hit；宏体遍历 TokenTree 只看 Ident；`use` 按叶子逐个 hit；允许名单列到具体叶子,与实际命中按多重集精确相等），`main.rs` 的结构测试钉出口函数内部先写 clean 的顺序；新增退出路径先加看护。
