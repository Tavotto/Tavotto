# ADR 0012：统一的 Codex 集成安装 CLI（`tavotto codex …`）

日期：2026-08-25 · 状态：**Proposed**（接口已定，实现待排期；本 ADR 随
「Codex 首次使用体验」PR 提交，评估目标是 v1.0 前）

## 背景

首次使用体验重构（README 首用章节 + SKILL 会话入口状态机）把普通用户的安装
收敛到了「两条 Codex 命令 + 一条引擎命令 + 新开会话」。但这仍是四个手工步骤，
且失败分诊靠用户读输出。桌面应用的设置页也想要一个「安装 Codex 集成」按钮
——如果按钮另写一套安装器，就会与 README 的命令漂移（本仓库最忌讳的第二权威）。

## 决定

新增三个子命令，挂在既有的 `engine/cli.py` 分派点上（与 `open` / `doctor`
同层，**import Flask 之前**分派，全程纯标准库）：

```sh
tavotto codex install     # 安装/修复 Codex 集成
tavotto codex doctor      # 只诊断不改动
tavotto codex uninstall   # 移除插件与 marketplace 项（不碰引擎）
```

### `install` 的步骤（幂等，缺什么补什么）

1. **定位 Codex CLI**：PATH → npm 全局 → 常见安装位置（复用 AI 桥
   `_search_dirs()` 的探测思路，但独立实现在纯标准库层）。找不到报
   `codex_cli_missing` + 安装指引，**不代装 Codex**。
2. **marketplace add**：`codex plugin marketplace add Tavotto/Tavotto
   --sparse .agents/plugins --sparse codex-plugin`（已存在则跳过；
   源与 sparse 路径从 `engine/brand.py` 派生，不在两处手写）。
3. **plugin add**：`codex plugin add tavotto@tavotto`（已装则按
   `marketplace upgrade` 语义提示，不强制升级）。
4. **准备匹配版本的引擎**：当前进程能 `import tavotto.engine` 即已满足
   （pip/pipx 形态天然满足）；frozen 桌面形态复用插件的 `--provision`
   逻辑（同一份实现，从插件包调，不抄第二份）。
5. **健康检查**：等价于 `python3 <插件>/mcp/server.py --health`，输出逐项
   结论。
6. 收尾**只输出一句**：「新开一个 Codex 会话」。不试图在旧会话里验证工具。

### 输出契约

与 `tavotto open --json` 同族：`--json` 时一行 JSON，失败带稳定
`error_code`（`codex_cli_missing` / `marketplace_add_failed` /
`plugin_add_failed` / `provision_failed` / `health_failed`…），每步带
`skipped: true/false`——幂等重跑必须能看出「什么都没做」。

### 桌面设置页按钮

「安装 Codex 集成」按钮 = spawn `tavotto-cli codex install --json` 并渲染
结果。**不写第二套安装器**；按钮与终端命令永远走同一条实现。

## 不做什么

- 不自动安装/升级 Codex CLI 本身。
- 不在健康状态下重装任何组件（与 SKILL 会话入口同一契约）。
- 不把 `codex` 子命令做成交互向导——一次跑完、如实报告。

## 验收（实现时）

- `tests/test_codex_plugin.py` 的真实 CLI 冒烟扩展：fresh `CODEX_HOME` 下
  `tavotto codex install` 走到 plugin list 可见（无 codex CLI 的机器 skip）。
- `install` 幂等：连跑两次，第二次全部 `skipped`，零网络写操作（marketplace
  查询除外）。
- 三个子命令在没装 Flask/PyMuPDF 的解释器里也能跑
  （与 `test_subcommands_run_without_flask_or_pymupdf` 同一纪律）。

## 关联

- 追踪 issue：<https://github.com/Tavotto/Tavotto/issues/117>（实现排期到 v1.0 前评估）。
- 前置：README「在 Codex 中第一次使用 Tavotto」、SKILL 会话入口状态机
  （本 PR）；`docs/adr/0005`（交接与发现链）、`docs/adr/0006`（MCP server）。
