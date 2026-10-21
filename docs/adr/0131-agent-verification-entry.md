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
- 确认信号要新增一个**经本机会话认证（ADR 0008）**的有限期查询，不开无认证端点，不为验证去执行用户脚本（实现见下文「实现」）。
  同源对：`locate.PROTOCOL_VERSION` ↔ 插件 `PROTOCOL`、`docs/handoff-protocol.md`、技能文本。

#### 实现（交接三态 PR）

**确认信号 = 经认证的 HTTP 查询**（`GET /api/handoff/render-status`）。曾考虑 0700/0600 回执文件（CLI 先写期望、实例写回结论），
维护者 2026-10-16 改为 HTTP 查询：文件通道要自带期望文件、写锁、清理，又是与会话认证并行的第二套信任面；HTTP 查询复用
`security.py` 的同一道 guard（Host / Origin / 凭据），first-wins 由时间序保证，不需要锁，CLI 不往磁盘写任何东西。
代价是桌面 sidecar 原本没有可供 CLI 使用的凭据——见下文「scoped token」，那是对 ADR 0008 的最小放宽。

- **实例侧**：`/api/engine/render` 收尾处（成功 / override 的 `WorkerError` / 取 worker 阶段的 `WorkerError` / 选中原件核对不上的
  `ArtifactContextError`——进渲染前核对、取 worker、执行中三处都算，可重试的 `source_changed` 除外——重排后那次渲染才是结论）把结论记进
  **进程内有界结论表**（`engine/handoffreceipt.py::LEDGER`，最多 128 条、每条保留 10 分钟，键 = 项目身份 + stem，线程安全，
  零 IO，没有交接在查时也记）。记录是旁路，任何异常不影响渲染响应。**只记录桌面端本来就会做的那次渲染，不为验证触发任何执行**；
  交接落图走 `openFastEdit → enterElementEdit`，有脚本的 Figure 会发起这次渲染。
- **端点**：`GET /api/handoff/render-status?project=<路径>&stem=<stem>&since=<epoch 秒>`，返回 `since` 之后（含）该项目 + stem 的
  **第一份**结论，没有则 `{"status":"pending"}`；响应只含 `status` / `code` / `stem` / `registry_match` / `manifest_elements`，
  不回显路径、不含 traceback；缺参数 / `since` 非有限数 → 400。
- **认证**（不开无认证端点）：走 guard，Host / Origin 校验照旧；接受 cookie、浏览器模式的完整 secret（`X-Tavotto-Auth`，
  `port-<端口>.json`），以及下面的 scoped token。比较用 `hmac.compare_digest`。
- **scoped token**：桌面 sidecar 在端口实绑后（`SidecarServer.announce_ready`）写 `<data_dir>/session/handoff-status-<端口>.json`
  （目录 0700、文件 0600、原子写；`{port, pid, token, scope: "handoff_render_status", created}`），退出时删除，启动时顺手清 pid 已死 /
  超过 30 天的同类文件。token 是 256 位随机，**与启动 nonce、会话 token、api secret 都无关**；guard 只在「`GET` 且路径恰为该端点」
  时认它，带去任何其它端点（含 `/api/session/relaunch`）都是 401/403/404。浏览器模式不另写这份：它本来就有完整 secret 文件，
  CLI 对已知端口直接用。写不出来只会让 CLI 落成 `unverified/channel_unavailable`，不影响 sidecar 启动。
- **为什么是对 ADR 0008 的最小放宽**：ADR 0008 划的边界是「桌面磁盘上没有任何能换会话的凭据」（nonce 走 stdin，不写文件，
  `relaunch` 回 404）。这里新增的是一份**只读、单端点**的凭据，不是会话凭据：换不到 nonce / cookie，调不了任何写端点。
- **威胁模型**：能读 `<data_dir>/session/` 的同用户本机进程本来就能读用户的任何文件（与 `session_client` 凭据文件、ADR 0021 §4 native
  descriptor 同一个论证）；网页（含 DNS rebinding 页面）读不到文件，且 Host / Origin 钉死。token 泄露的后果仅是看到「某项目某 stem
  最近渲染成功与否」（需要知道项目路径与 stem）。
- **CLI 侧**：唤起**之前**取 `since = time.time() - 2 s`（回拨余量：唤起前取时刻与实例渲染完成之间可能被调度打乱；代价是 2 秒内同一
  stem 的上一次渲染也会被算进来，`verified` 还要求 stem 一致 + 注册表有 + manifest 非空）。唤起后有界轮询，**每轮重新发现凭据**
  （冷启动时 sidecar 在唤起之后才发布）：扫 `handoff-status-*.json`（pid 存活的才算，可能多份、逐个查）+ 已知浏览器端口的
  `port-<端口>.json`；`http.client.HTTPConnection("127.0.0.1", 端口)` 直连（主机字面量固定，outbound 闸门只放行这一种 HTTPConnection 写法），单次超时 1 秒。**绝不经过代理**：请求带 `X-Tavotto-Auth`，`urlopen` 会按环境变量 / 系统设置走 HTTP 代理而把凭据发给代理；直连 socket 不读任何代理设置（用例：假代理收 0 连接）。
- **等待与超时**：`tavotto open --render-wait SECONDS`，CLI 默认 20 秒、0 = 不等；`open_target()` 自己默认 0（库调用不被拖慢）；
  `nan` / `inf` / 负数被 argparse 拒绝（退出 2），库调用按 0。**到点没结论是 `unverified/timeout`；一个实例都没连上（找不到凭据 / 连接失败 /
  被拒）是 `unverified/channel_unavailable`；都不是 failed。**
- **`launch.render`**（加性对象，`ok`、退出码、`protocol: 1` 不变）：
  `{"status": "ok|failed|unverified", "verified": bool, "waited_ms": int, …}`。
  `unverified` 带 `reason`（闭集 `not_waited` / `timeout` / `channel_unavailable` / `no_target_stem` / `not_renderable`）；
  `failed` 带 `code`（实例报出的 worker 稳定码，如 `engine_files_incomplete`、`missing_dependency`；
  不合规的串落成 `render_failed`）；`ok` 带 `stem` / `registry_match` / `manifest_elements`。
  `verified` 的**唯一判据**是 `handoffreceipt.summarize`：渲染成功 ∧ 结论 stem == 目标 stem ∧ 注册表里有该 stem ∧ manifest 非空。
- **插件顶层三字段**（`scripts/handoff.py`，只搬运不另判）：`launch_success`（true=唤起成功；null=`--no-launch`；false=没走到唤起）、
  `render_success`（true / false=明确失败 / **null=不知道**）、`verified_success`（仅 `render.verified === true` 才 true）；外加 `render` 细节。
  旧 CLI 没有 `launch.render`：插件补 `{"status":"unverified","reason":"cli_without_render_signal"}`，
  `render_success: null`、`verified_success: false`。旧插件读新 CLI：多出的键被忽略。退出码与 `ok` 一字未改。
- **没覆盖的**：旧版本实例没有这个端点 / 没发布凭据 → `channel_unavailable`；无脚本 Figure（`registry.parameterizable === false`）不发起渲染
  → CLI 不查询，直接 `unverified/not_renderable`；结论表是进程内的，实例重启后丢失（CLI 只关心刚发生的那一次）；
  真 Windows 桌面上的端到端未验。
- **MSIX 与数据目录（#961 现场，#1129，未在真机验证）**：CLI 现在只**读**桌面写在真实 `%LOCALAPPDATA%\Tavotto\session\` 下的 scoped
  凭据文件，不再写任何东西。MSIX 的文件系统虚拟化对读通常合并到真实路径，因此**预计可读到**；但仓库里没有处理 MSIX /
  LocalCache 的代码（`code-facts.md`），仍未在 Windows 真机（Codex Store 版拉起 CLI）验证。若读不到，结果是
  `unverified/channel_unavailable`（不误报 ok / failed，但这个场景下三态没有信号）。本 PR 不声称已解决。

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

## 修订（2026-10-18，第六节，#1154：重复交接已显示的图）

**问题**：桌面已开着且正在显示某张图时再交接同一张，前端纪律「重复交接同一张只选中，不叠第二份」不触发
`/api/engine/render`，结论表没有 `since` 之后的新结论，CLI 每次白等满 20 秒再报 `unverified/timeout`，
也拿不到「图其实已经好好显示着」的确认。

**决定**（加性，`ok`、退出码、`protocol: 1` 不变）：
- 新增**经会话认证的写入口** `POST /api/handoff/display-ack`，请求体 `{id, status, manifest_elements?, code?}`，
  `status` 闭集 `ok | failed | displayed`。键沿用 `_note_handoff_render` 的 (项目, stem)，stem 由 `_handoff_target_stem(id)` 解析；
  时间戳取**当前时间**，故 CLI 的 `since` 能查到；与渲染结论同一张有界 `LEDGER`（128 条 / 10 分钟），确认不绕过它。
- 结论形状与渲染收尾处写入的一致：`ok` 的 `registry_match` 由**后端**查注册表（前端的话不算），`verified` 仍只在
  `handoffreceipt.summarize`；`failed` 的 code 过 `_safe_code`。
- **闭集新增一条 `unverified.reason = already_displayed`**：前端命中了已显示面板，但拿不出这一版的精确渲染（过期 / 非精确变体）时用，
  语义是「实例应答了，但没有可凭的渲染结论」，与 `timeout`（没信号）区分；绝不 verified。正在渲染中（结论在路上）或还没渲染过的面板不发确认，
  交给它自己的渲染结论。
- **安全**：不改 `security.py`——guard 原本只在「GET 且路径恰为 render-status」时认 scoped token，POST 新入口带它是 401（用例钉着）；
  新入口只认会话 cookie / 完整 secret，Host / Origin 照旧。请求体校验失败 / 解析不出 stem 一律 `400 bad_handoff_ack`。
- **兼容**：老前端不发确认 = 行为与从前一致（等到超时 `unverified/timeout`）；插件三态映射不改（`unverified` → `render_success: null`）。
