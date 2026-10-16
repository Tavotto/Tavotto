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

### 六、交接三态（独立 PR 实现，见本节末「实现」）

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

#### 实现（交接三态 PR）

**确认信号用 0700/0600 的回执文件，不开 HTTP 端点。** 原因是事实而非偏好：桌面 sidecar 的端口由壳建议、实绑后
只写进壳的握手文件，磁盘上没有任何本机凭据（ADR 0008「桌面模式的差异」：不写凭据文件、`/api/session/relaunch` 回 404），
CLI 既找不到端口也没有凭据可带；要做成「经会话认证的查询」就得给桌面新造一个凭据发布面，那是放宽 ADR 0008。
回执通道的信任锚与 `session_client` 凭据文件、ADR 0021 §4 的 native descriptor 是同一个论证（`<data_dir>/session/`
本人可读写、网页碰不到），没有新端点就没有可被绕过的 guard。实现与约束全文在 `engine/handoffreceipt.py` 的模块文档。

- **流程**：CLI 唤起**之前**写 `<data_dir>/session/handoff/<32 位十六进制 ID>.json`（项目身份、stem、`expires_at`、
  `result: null`）→ 唤起 → 有界轮询；实例在 `/api/engine/render` 收尾处（成功 / `WorkerError`）把结论写进匹配的期望
  （项目 + stem 相同、未过期、尚无结论；第一份结论赢）→ CLI 读到后删掉自己的期望。**只记录桌面端本来就会做的那次渲染，
  不为验证触发任何执行**；交接落图走 `openFastEdit → enterElementEdit`，有脚本的 Figure 会发起这次渲染。
- **等待与超时**：`tavotto open --render-wait SECONDS`，CLI 默认 20 秒、0 = 不等；`open_target()` 自己默认 0（库调用不被拖慢）。
  期望有效期 = 等待上限 + 30 秒宽限，陈旧文件（1 小时）顺手清。**到点没结论是 `unverified/timeout`，绝不记 failed。**
- **`launch.render`**（加性对象，`ok`、`protocol: 1` 不变）：
  `{"status": "ok|failed|unverified", "verified": bool, "waited_ms": int, …}`。
  `unverified` 带 `reason`（闭集 `not_waited` / `timeout` / `channel_unavailable` / `no_target_stem`）；
  `failed` 带 `code`（实例报出的 worker 稳定码，如 `engine_files_incomplete`、`missing_dependency`；
  不合规的串落成 `render_failed`）；`ok` 带 `stem` / `registry_match` / `manifest_elements`。
  `verified` 的**唯一判据**是 `handoffreceipt.summarize`：渲染成功 ∧ 回执 stem == 目标 stem ∧ 注册表里有该 stem ∧ manifest 非空。
- **插件顶层三字段**（`scripts/handoff.py`，只搬运不另判）：`launch_success`（true=唤起成功；null=`--no-launch`；false=没走到唤起）、
  `render_success`（true / false=明确失败 / **null=不知道**）、`verified_success`（仅 `render.verified === true` 才 true）；外加 `render` 细节。
  旧 CLI 没有 `launch.render`：插件补 `{"status":"unverified","reason":"cli_without_render_signal"}`，
  `render_success: null`、`verified_success: false`。旧插件读新 CLI：多出的键被忽略。退出码与 `ok` 一字未改。
- **没覆盖的**：没有任何实例在写回执（旧版本实例 / 数据目录被隔离成不同位置）→ `timeout`；无脚本 Figure（不可参数化）不发起渲染 → `timeout`；
  真 Windows 桌面（MSIX 副本）上的端到端未验。
- **MSIX 与数据目录（#961 现场，未解决）**：回执路径是 `config.data_dir()` = `%LOCALAPPDATA%\Tavotto`（可被 `TAVOTTO_DATA_DIR` 覆盖）。
  仓库里没有任何处理 MSIX / LocalCache 的代码（`code-facts.md`），所以**不确定**：Codex 是 MSIX 打包应用，它拉起的 CLI 子进程
  若继承包的文件系统虚拟化，写 `%LOCALAPPDATA%` 会落到 `Packages\<pkg>\LocalCache\Local\Tavotto`，而非打包的桌面 sidecar
  读的是真实路径——两边错位，则永远 `unverified/timeout`（不会误报 ok / failed，但这个场景下三态等于没有信号）。
  同一错位也会影响 `session_client` 的凭据文件，但那条从未在 MSIX 下被验证过。需 Windows 真机验证；若证实，可选的出路
  （都未实现）：实例额外扫 `Packages\*\LocalCache\Local\Tavotto\session\handoff`，或把 ID 经 argv 传给壳（改同源对 `desktop_argv`）。

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
5. #1001 合入后登记一个 `diagnostic_only` 场景。
