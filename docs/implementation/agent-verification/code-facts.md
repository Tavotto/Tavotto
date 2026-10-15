# Agent Verification P0：代码事实表

基线：origin/main `6457447d6`（2026-10-15）。来源：四路只读审计（性能、CI、可靠性、Agent 接口），
并在本机复核过关键几条（标 ✅ 的是本机实际运行得到的结论）。决策见 `docs/adr/0131-agent-verification-entry.md`。

## 性能

| 位置 | 事实 |
| --- | --- |
| `docs/adr/0075-drag-performance-probe.md` | 探针在产品内录数字和枚举，判定在产品外（`scripts/perf_report.py`）。没有基线，只有同一份报告内的 idle 与 m1/m3 对照；开发机数字只是模拟 |
| `scripts/perf_report.py` | schema `tavotto-perf-probe/1`。CLI `reports... [--html] [--json]`，无条件 `return 0`。`--json` 是裸列表，没有 schema / status。schema 不符时以 `SystemExit(str)` 退 1；✅ 文件不存在时抛未捕获 `FileNotFoundError` |
| PR #1000（已合入） | 探针报告加 `actions` 字段，记录符号和预设插入的同步分段与双 rAF 代理（明确不等于上屏延迟），`perf_report --json` 输出 `actions[].*_p95_ms` |
| PR #1001（Draft） | `scripts/perf_macos_window.py compare` 只输出 `diagnostic_only` 或 `incomparable`，绝无 pass。OS 版本或架构不同、样本少于 5 个时拒绝对照。只能在 macOS 真窗口运行 |
| `scripts/perf_manifest_report.py` | schema `tavotto-perf-manifest/1`。`recorded` 退 0，`incomplete` 或坏文件退 2。`comparison_verdict` 恒为 `not_checked_without_pinned_same_machine_baseline`。只输出数字和场景序号 |
| `scripts/bench_render.py` | 自己拉起后端，走真实 HTTP。每轮用全新数据目录。原始 `--json` 含项目路径和文件名 |
| ✅ `bench_render.py` 在新 worktree 里 | 没有字体（`src/tavotto/resources/fonts` 不进 git）时，`/api/export` 返回 HTTP 500，脚本 rc=1，输出 traceback。设置 `TAVOTTO_FONTS_DIR` 后 6.5 秒跑完三张示例图，三个面板全部 9/9 recorded |
| `scripts/ci/benchmark.py` | 基线在 `<state_root>/baselines/perf/rolling.json`（lab 机器，不在 git 里）。`REGRESSION_PCT=25`。只有 `LAB_PERF_GATE=true` 才阻断；无基线时退 0 |
| `tests/test_perf_probe.py` | 模块级 `import flask`，没有 Flask 的环境里整个文件收集失败 |

## CI 边界

| 位置 | 事实 |
| --- | --- |
| `.github/workflows/ci.yml` | 触发事件是 push main、`pull_request`（branches main）、merge_group。全部跑在 hosted runner 上，顶层 `contents: read`。重型腿只在 merge_group 或带 `full-ci` 标签时跑 |
| `.github/workflows/lab-ci.yml` | 只有 push main、schedule、dispatch。`trust-check` 要求 SHA 是 main 的祖先或有 tag 指向，然后用 PAT 派发到私有仓库 `Tavotto/ci-infra` |
| `_lab-qualification.yml` | 唯一使用 `[self-hosted, linux, x64, tavotto-lab]` 的地方。看护：`tests/test_merge_queue_workflows.py::TestRunnerTrustZones` |
| `docs/rules/ci/runner-trust-zones.md` | 自述缺口：PR 自带的 workflow 在 PR 事件上会先执行一次，这一半靠 runner 不注册在公开仓库来挡 |
| `scripts/ci/_common.py` | `run_metadata()`（环境事实）、`write_report()`、`state_root()`（默认 `/srv/tavotto-ci`，可由 `TAVOTTO_CI_STATE_ROOT` 覆盖）。纯标准库 |
| 全仓库 | 没有 `agent_verify` 或「白名单场景 + 统一报告」的等价物。`compat_matrix`、`feature_registry`、`aggregate_gate` 各自 schema 不同 |
| 退出码约定 | 0 通过 / 1 失败 / 2 用法错误（feature_registry、aggregate_gate、normalise）。pwsh 步骤会把非 0/1 折成 1 |

## 可靠性 / #961

| 位置 | 事实 |
| --- | --- |
| `packaging/tavotto.spec` | 冻结产物把 engine 源文件（含 `figcapture.py`）作为 datas 复制到 `_internal/tavotto/engine` |
| `src/tavotto/engine/worker.py` `_ENGINE_MODULES` | safe worker 用 `bridgeboot.load_engine_modules(HERE, …)` 装载引擎模块，`figcapture` 排第一 |
| `src/tavotto/engine/worker.py` `_missing_module_fact` | 只看是不是 `ModuleNotFoundError`，不区分用户包和 Tavotto 自家模块，结果是 `missing_dependency` |
| `src/tavotto/engine/pool.py` `_MISSING_RE` | 父进程按 `No module named` 识别缺依赖 |
| `src/tavotto/app.py` | 对 `missing_dependency` 给出「缺包」提示，挂一键安装 offer，并记入诊断 |
| `src/tavotto/engine/handoff.py` `open_target` | 返回 `ok: True`。桌面模式的 `ready` 只有 `process_alive`、`forwarder_exited`、`unverified` 三种取值，不含渲染结果 |
| `codex-plugin/skills/tavotto-figure/scripts/handoff.py` | 插件层的 `ok` 由 `parameterizable is True` 决定 |
| 同源对 | `engine/locate.py` 的 `PROTOCOL_VERSION=1` ↔ 插件 `PROTOCOL=1`。看护：`tests/test_install_locate.py`、`tests/test_codex_plugin.py`。文档：`docs/handoff-protocol.md` |
| 全仓库 | 没有任何处理 MSIX、LocalCache、WindowsApps 的代码 |

## Agent 接口

| 位置 | 事实 |
| --- | --- |
| `pyproject.toml` `[tool.ruff] src` | 含 `.`、`scripts`、`scripts/ci`，不含 `scripts/dev`，所以新入口放 `scripts/ci/` |
| `tests/test_import_architecture.py` | 只覆盖 `src/tavotto`，`scripts/` 不在其中 |
| `tests/test_agents_rules_index.py` | 每份细则都要被速查表点名、头部写明出处；反引号里的路径、用例、ADR 必须存在；Codex 拼接 32 KiB 硬线 |
| `scripts/dev/agents_budget.py`（基线） | cwd=`src/tavotto` 拼接 32739 B、cwd=`web` 32702 B，余量都不到 100 B，所以根 `AGENTS.md` 一个字也不加 |
| `src/tavotto/engine/config.py` | `data_dir()` 读 `TAVOTTO_DATA_DIR` |
| `codex-plugin/mcp/` | 面向用户的作图工具（`tavotto_*`），不承载开发验证 |
