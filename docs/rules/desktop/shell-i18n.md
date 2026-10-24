# 壳内多语言

> 原文出自 `src-tauri/AGENTS.md`「壳内多语言」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 90
改到: `src-tauri/src/i18n.rs`、`build.rs`、`connect.html`、splash / error 页
主题: 桌面壳自带文案 → `shell-i18n.md`
必守要点: 改菜单文案要改两处，菜单项 id 与加速键一个字节不动；壳内文案不手写产品名，由 `build.rs` 编译期注入；`connect.html` 经 `?product=` / `?dist=` 带名字
-->

**桌面壳自带一份文案**（`src-tauri/src/i18n.rs`）：原生菜单在 webview
起来之前就要建。改菜单文案要**改两处**；切语言只换显示文案，菜单项 id 与
加速键一个字节不动。splash/error 页在 `tauri://` 源下，两份文案内联、
语言由壳经 `?lang=` 带过去。首启（还没有 `menu-locale` 文件）菜单是默认档，
前端起来后重建——已知限制，见 docs/i18n.md。
**壳内文案不手写产品名**：`build.rs` 从 `brand.ts` / `engine/brand.py` 编译期注入
`TAVOTTO_PRODUCT_NAME` / `TAVOTTO_DIST_NAME`，`ShellText` 里写 `concat!(env!(…), …)`，
`connect.html` 由壳经 `?product=` / `?dist=` 带名字（`tests/test_desktop_i18n.py` 看护；
splash / error 页与窗口标题是尚未收口的旧写法）。
