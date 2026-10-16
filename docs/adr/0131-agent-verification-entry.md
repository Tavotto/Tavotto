# ADR 0131：开发 Agent 的统一验证入口——白名单场景、诚实结果、可审查证据

日期：2026-10-15 · 状态：**Proposed**（P0：一个 Draft PR，交维护者审）
相关：[0075 拖动性能探针](0075-drag-performance-probe.md)（探针在产品内、判据在产品外）、
[0097 功能登记表](0097-feature-registry.md)（登记表 + 校验器的风格）、
`docs/rules/ci/agent-verification.md`（规则全文）、`docs/rules/ci/verification-chain.md`、
`docs/rules/ci/runner-trust-zones.md`、代码事实表 `docs/implementation/agent-verification/code-facts.md`

## 问题

Claude / Codex 这类开发 Agent 改完代码要回答三件事：验证了什么、结果是什么、哪些没验证。
仓库里的验证器各自都对，但合在一起会让 Agent 说错话（2026-10-15 在 6457447d6 上实测）：

| 验证器 | 会让 Agent 说错的地方 |
| --- | --- |
| `scripts/perf_report.py` | 严重卡顿也退 0；`--json` 无 schema / status；文件不存在是 traceback |
| `scripts/bench_render.py` | worktree 缺字体时 `/api/export` 返回 500，是一个 traceback，看不出是环境问题 |
| `scripts/ci/benchmark.py` | 无基线、门禁未开时回归也退 0；会写 `/srv/tavotto-ci` |
| pytest | skip 混在绿里，退出码 0 |
| `perf_manifest_report.py` | `recorded` 只说明数据完整，**不**说明性能达标 |

仓库里没有「白名单场景 ID + 统一报告」的等价物（审计 B：全仓库无 `agent_verify`，
`compat_matrix` / `feature_registry` / `_common.write_report` 只是零件，各自 schema 不同）。

## 决定

### 一、一个 CLI，不是服务

`scripts/ci/agent_verify.py`：`list` / `run <id>... [--out DIR] [--json] [--python PY]`。
不常驻、不开端口、不进 Codex 插件的 MCP（那是面向用户的作图工具，随插件发布；带着仓库根 / git /
pytest 的假设进去会扩大攻击面）。纯标准库，复用 `_common.run_metadata()`。
放 `scripts/ci/`：已在 ruff `src` 里，`from _common import …` 排序正确；`scripts/dev/` 不在。

### 二、白名单场景

场景登记在模块常量 `SCENARIOS`（argv 模板与前置条件探针是代码逻辑，写成 JSON 要再造解释器）。
每个场景写明 `claim`（passed 到底证明了什么）、`unverified`（明确没覆盖的东西）、
`owner_doc`。argv 只有占位符 `{python}` `{evidence}` `{private}` `{work}`，**不接受外部命令或参数**。
首轮三个：

- `perf.manifest_timing`：`bench_render.py --plane python --repeat 9` → `perf_manifest_report.py`。
  原始 bench JSON 含项目路径与文件名，放在私有目录、跑完删除，只有匿名摘要进证据（`docs/perf-manifest-agent.md`）。
- `perf.manifest_analyzer.selftest`：分析器自己的反向用例，离线、确定性。
- `regression.961.engine_files_incomplete`：#961 的本机可证部分（见第五节）。

### 三、结果闭集与聚合

`passed | failed | error | environment_unavailable | not_run | skipped | not_reproduced`。
聚合取最差（error > failed > environment_unavailable > not_run > skipped > not_reproduced > passed），
空集合是 `not_run`。**只有全部 passed 才是 passed。** 退出码 0 / 1（failed、error）/ 2（用法）/
3（环境）/ 4（不完整）；CI 的 pwsh 会把 3、4 折成 1，所以权威是 `report.json` 的 `overall`。

- 前置条件（字体、Python 包、测试文件）在执行前判。缺环境是 `environment_unavailable`；
  登记的测试文件不存在是 `not_run`。产品失败不会伪装成环境问题，反过来也一样。
- pytest 结果从 junit 读计数：有 skip 就整场景 `skipped`；没有 junit 是 `error`，不按退出码判绿；
  没有收集到用例是 `not_run`。
- 超时会连同子进程树一起杀掉，结果记 `error` 并标 `timed_out`。

### 四、隔离与证据

- 每个场景在 `--out/<id>/` 下独立运行，`--out` 必须为空。
- 设置 `TAVOTTO_DATA_DIR`、`MPLCONFIGDIR`、`TAVOTTO_CI_STATE_ROOT`，都指向场景私有目录，所以绝不碰 lab 真基线。
- 固定 `TAVOTTO_NO_TELEMETRY=1`，`PYTHONPATH` 用绝对路径。
- 环境变量走白名单：调用方 shell 里的 `GITHUB_*`、`TAVOTTO_*` 开关不进场景，只放行 `TAVOTTO_FONTS_DIR`。
- `report.json` 的内容：
  - schema 1；
  - commit：sha、branch、dirty、改动文件数；
  - 环境事实：没有绝对路径，记的是目标 Python 版本、是否仓库 venv、字体在不在；
  - 每个场景：前置条件、步骤（argv 用占位符）、退出码、耗时、facts、基线、证据（相对路径 + sha256）、未验证项。
- 原始日志可能含本机路径，只留在本机，不贴进公开 PR。
- **不更新任何基线**。性能场景的基线状态原样透传 `not_checked_without_pinned_same_machine_baseline`。
  P0 不做任何基线对照；将来接入时，机器指纹（OS 版本 / 架构 / Python）不同就拒绝对照、只出
  `incomparable`，借 #1001 `perf_macos_window.py compare` 的判法。

### 五、#961：本机能证明什么、不能证明什么

#961：Windows 上经 Codex 桌面交接后，sidecar 实际从 `...\Packages\<pkg>\LocalCache\...` 副本启动，
副本缺 `figcapture.py`。渲染时 safe worker 的 `bridgeboot.load_engine_modules` 抛
`ModuleNotFoundError`，之后在三处被**误判成用户缺包**：worker `_missing_module_fact`、父进程
`pool._MISSING_RE`、`app.py` 的缺依赖提示与一键安装 offer。与此同时交接早已返回 `ok: true`。

- **本 PR（本机可证）**：把「Tavotto 自家引擎模块缺失」分诊成引擎文件不完整，不再引导装包；
  真缺第三方包的分诊不回归。复现方式是把 engine 目录复制出来删掉 `figcapture.py`。修复前后用同一场景
  `regression.961.engine_files_incomplete` 各跑一次，对照写进 PR。
- **仅 Windows 真机可证**（写进场景的 `unverified`，PR 不声称）：
  - MSIX 重定向副本是否真不完整、为何缺件；
  - NSIS 安装 → 升级后的真实文件集；
  - 真 Codex（Store 版）交接。

### 六、交接三态（后续独立 PR，本 PR 不实现）

交接现在的 `ok` 有两层含义：
- CLI 层（`engine/handoff.py` `open_target`）：解析、登记、唤起都成功了，桌面模式下进程还活着；
- 插件层（`scripts/handoff.py`）：`parameterizable === true`。

两层都不含「图已渲染」。**不改 `ok` 的公开语义**，protocol 仍为 1，只做加性字段：

- CLI `launch` 下加 `render`：`unverified | ok | failed`，以及失败时的稳定码。
- 插件 payload 加三个顶层字段：
  - `launch_success`：等于现有 CLI `ok` 的语义；
  - `render_success`：桌面 sidecar 对目标 stem 的一次渲染结果；
  - `verified_success`：`render_success` 为真，且渲染结果确认是目标 Figure（stem 与 registry 一致、manifest 非空）。
- 确认信号要新增一个**经本机会话认证（ADR 0008）**的有限期查询，不开无认证端点，不为验证去执行用户脚本。
  同源对：`locate.PROTOCOL_VERSION` ↔ 插件 `PROTOCOL`、`docs/handoff-protocol.md`、技能文本。

## 不做

- 不建第二套 CI，不接 workflow（P0 只给本地 Agent 用）；
- 不在 self-hosted runner 上跑任何东西；
- 不重写性能 / 诊断系统；
- 不新增服务；
- 不上传诊断、脚本、路径；
- 不改基线、不放宽断言、不把失败转成 skip。

## 后续切分

1. 交接三态（第六节），risk:high；
2. `perf.probe.analyze`：入口这一层给 `perf_report.py` 补 schema 预检，并把 findings 级别映射成结果；输入必须是真机录的探针报告；
3. sidecar 启动期 / `tavotto doctor` 的引擎文件清单自检（#961 修复 B）；
4. 如需 CI 消费：hosted runner、事件闭集内、只上传 `report.json`；
5. #1001 合入后登记一个 `diagnostic_only` 场景。**已落地（2026-10-16）**，见文末「追加说明」。

## 追加说明（2026-10-16，后续切分第 5 条落地）

登记了三个场景，**结果闭集与聚合顺序未改**：

- `perf.agent.symbol_ui`（measurement）：`perf_agent.py run`（chromium，16 轮，无 `--baseline` / `--include-drag`）。
  新增 parser `perf_agent`，以摘要 `status` 为准：`ok`→passed（且摘要自洽），`incomplete` / `failed` /
  `regression`→failed，`incomparable` / `no_baseline`→not_run，`pending` / 未知 / 摘要缺失→error；
  基线状态原样透传，passed 不等于性能达标。前置条件新增 `tool:`、`any_file:`、`playwright_browser:`，
  缺 node / pnpm / 依赖 / 前端构建 / 浏览器一律 environment_unavailable。
- `perf.agent.selftest`（selftest）：`tests/test_perf_agent.py`。
- `perf.macos_window.selftest`（selftest）：`tests/test_perf_macos_window.py`。
  `perf_macos_window.py record / compare` 需要真实 macOS 窗口与人工拖动，Agent 无法无人值守，**不登记**；
  `diagnostic_only` 用场景上新增的 `semantics` 标注（不是结果状态）表达。
