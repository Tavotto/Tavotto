# 远程实例窗口、桌面交接 argv 与打包排除

> 原文出自 `src-tauri/AGENTS.md`「进程关系与安全边界（2026-08-17，与浏览器模式并行）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `src-tauri/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：远程窗口权限是闭集，不许加本机文件类权限、不装 native_drop；`--open <目录> [--stem]` 生产者与消费者唯一、改一边同步另一边；wheel/sdist 不含 `src-tauri/`

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 50
改到: `capabilities/remote-*.json`、`REMOTE_WINDOW_MARKER`、`main.rs::parse_open_args()`、`handoff.desktop_argv()`
主题: 远程实例窗口（ADR 0105）与 `--open` 交接契约 → `remote-window-and-handoff.md`
必守要点: 远程窗口权限是闭集，不许加本机文件类权限、不装 native_drop；`--open <目录> [--stem]` 改一边同步另一边
-->

- **远程实例窗口**（ADR 0105）：label `remote`，加载 `ssh -L` 转发过来的服务器引擎。
  `incognito(true)`（cookie 不按端口隔离，共用存储会顶掉本机会话）+ 注入
  `REMOTE_WINDOW_MARKER`（前端 `isDesktop()` 据它为假，本机文件类能力走浏览器回退）+
  不装 native_drop。权限是闭集（`capabilities/remote-*.json`：只听事件不发、关窗询问、
  壳自带页面的 `connect_remote`），**不许往里加本机文件类权限**；关窗闸按窗口各一道，
  菜单发给聚焦窗口。看护 `tests/test_desktop_remote.py`。
- 桌面交接契约 argv `--open <目录> [--stem <stem>]`：生产者唯一
  `handoff.desktop_argv()`，消费者唯一 `src-tauri/src/main.rs::parse_open_args()`，
  两侧各有单测，改一边必须同步另一边（完整交接语义见
  `docs/rules/backend/external-handoff.md`）。
- wheel/sdist 不含 `src-tauri/`（hatchling 白名单）；`src-tauri/target/`、
  `src-tauri/gen/` 进 .gitignore。
