# 调试符号与小型转储

> 原文出自 `src-tauri/AGENTS.md`「构建、验收与安装界面」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 80
改到: `src-tauri/Cargo.toml` 的 `[profile.release]`、`crash_dump.rs`、`desktop-tauri.yml`、`workerd/Cargo.toml`
主题: 调试符号与 Windows 小型转储 → `debug-symbols-and-minidump.md`
必守要点: 符号另存 `debug-symbols-*` artifact、不进安装包与更新清单；MSVC 腿用 `CARGO_PROFILE_RELEASE_STRIP=none`；`main()` 第一句装过滤器、过滤器内不分配堆不加锁；不写 WER LocalDumps 注册表
-->

- **调试符号与小型转储**：`[profile.release]` 的 `debug = "line-tables-only"` + `split-debuginfo = "packed"` 让 macOS 产出 .dSYM、
  二进制仍剥光；MSVC 上 `strip` 会吞 PDB，Windows 腿由 `desktop-tauri.yml` 用 `CARGO_PROFILE_RELEASE_STRIP=none` 覆盖
  （`workerd/Cargo.toml` 同）。符号另存为 `debug-symbols-*` artifact，**不进安装包、不进更新清单**（`docs/RELEASING.md`「调试符号」）。
  Windows 小型转储由壳自写（`crash_dump.rs`：`main()` 第一句装未处理异常过滤器，过滤器里不分配堆、不加锁；
  落到诊断包 `crash_dirs()` 的 `%LOCALAPPDATA%\CrashDumps\Tavotto.exe.<pid>.dmp`，启动时轮转留 5 份，崩溃后短暂可有 6 份）。
  **不写 WER LocalDumps 注册表**（HKCU 是否生效无权威说法，HKLM 要管理员）。看护：模块内单测 +
  `main_installs_the_filter_as_its_first_statement`；「真崩一次」只在 `nightly.yml` windows-install 跑（PR CI 没有 Windows cargo test）。
