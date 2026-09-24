# codex-plugin/ — Codex 插件、技能与 MCP server 规则

仓库级路由与不变量在根 `AGENTS.md`。完整版 ADR：
`docs/adr/0005-external-handoff-and-codex-plugin.md`、
`docs/adr/0006-codex-mcp-app-and-publication-profile.md`、
`docs/adr/0009-codex-workspace-root-authority.md`、
`docs/adr/0069-canvas-payload-under-host-event-cap.md`、
`docs/adr/0078-mcp-session-survives-server-process.md`。改动前先读。
交接的引擎侧（`engine/locate.py` / `engine/handoff.py` / `engine/cli.py`）在
`docs/rules/backend/external-handoff.md`。

## 首次使用契约（2026-08-25，勿破坏）

- **普通用户安装绝不需要 clone 仓库**，也不需要 pnpm/npm/cargo/Tauri/前端构建/
  `run.sh`/测试套件/editable install。README 的「在 Codex 中第一次使用
  Tavotto」是普通用户的唯一入口；源码安装只留给贡献者。
- **技能的会话入口是「先检查，不安装」**（SKILL.md 最前部的状态机）：
  `tavotto_health` 健康时本会话零安装、零联网；缺什么修什么（缺插件才装
  插件、缺引擎才 provision/装引擎）；`desktop_only` 不说「没装 Tavotto」；
  插件更新只在收尾提醒一次；工具缺失 = 给两条安装命令 + 要求新开会话 + 停止。
  **不许把「每会话自动 marketplace add」加回来**——同一会话工具不重载，
  重装只有网络开销（tests/test_codex_plugin.py 看护）。
- 插件安装/升级/引擎装好之后**必须新开 Codex 会话**；`codex plugin list` 的
  enabled ≠ 当前会话拿得到工具。
- 安装命令两条分开写（不用 `&&`）；GitHub 源只需 `--sparse .agents/plugins`：市场清单
  的插件来源是 `git-subdir → plugin-stable`（ADR 0043），插件本体来自发行分支，不从源码
  checkout 里取。唯一出处 `brand.CODEX_SPARSE_PATHS`，README / 恢复文档由它派生。
- SKILL.md 收敛为「触发条件 + 会话入口状态机 + 核心图文件契约 + MCP 工具
  顺序 + 完成判据」，细节按需读 `skills/tavotto-figure/references/`：
  first-run-and-recovery（安装/provision/错误码/新会话）、figure-contract
  （同目录/静态产物名/main()/模板）、publication-style（尺寸/字号/克制/组图）、
  desktop-handoff（交接与退出码）、issue-reporting（脱敏草稿 + 用户同意）、
  compatibility（能改什么）。**SKILL.md 里必须写清什么情况读哪份**。
- **`.mcp.json` 的 `command` 是插件自带的 `./mcp/launch.cmd`**（#172 → #266，2026-09-24）。
  Codex 的 `.mcp.json` 没有按平台分支的字段、没有候选链，`command` 也**不过 shell**
  （实测：`command` 与 `args` 分开传，相对路径按 `cwd` 解析），一个裸名字盖不住 POSIX 与
  Windows（`python3` 在 Windows 上常是商店别名：命令存在、9009、零输出，连降级 server
  都起不来）。所以 command 是一份 **sh / cmd 双语**启动器：POSIX 上就是
  `exec python3 "$@"`（与改动前逐字同义），Windows 上按 显式 `TAVOTTO_MCP_PYTHON` →
  插件自管环境 → `py -3` → PATH 上 `python`/`python3` → `%LOCALAPPDATA%\Programs\Python`
  的顺序**真跑**一句判版本的探测（≥ 3.8；Python 2 也能 `import sys`，却解析不了 server.py），第一个过得了的接过全部参数；引擎定位仍只归
  `server.py` 的 resolver。**形态约束**（`test_the_dual_launcher_keeps_its_platform_contract`
  看护）：第一行 shebang（Rust 起不认无 shebang 的脚本，实测 Exec format error）、git 模式
  100755（Codex 缓存副本保留执行位，0.156.1 实测）、全文 LF、批处理段纯 ASCII、不用
  goto / call :label。cmd 会把第一行回显进 stdout **一次**：rmcp 3.2+ 跳过非 JSON 行，
  2.x 回一条 parse error 后继续，≤1.x 会断连——`test_codex_style_spawn_…` 钉住「最多这一行」。
  `args` 仍是 `["./mcp/server.py"]`：`tavotto codex install` 的 interpreter 步照旧按执行
  判（按**插件根**解析相对 command，不按本进程 cwd），起不来才把**已装副本**的 command
  钉成解释器绝对路径，**`.mcp.json` 与 `openai.yaml` 两侧一起换**（stdio 依赖按 command
  匹配）。发行件里只许裸名字或这种 `./` 相对、真在插件里的启动器
  （`pluginmanifest._is_plugin_relative`），机器相关的绝对路径只属于已装副本。插件升级会把
  钉过的绝对路径换回启动器——它自己找 Python，多数机器上不用再做什么。
  真 Windows + Codex Desktop 上的一次实跑是 #266 的关闭条件，CI 的 windows 腿只替它跑了
  cmd.exe 那半边（`test_windows_launcher_skips_a_python_that_does_not_run`）。
- `agents/openai.yaml` 的 `dependencies.tools` 声明本插件的 MCP server 依赖：
  `type: mcp` + `value` == `.mcp.json` 的 server key（`tavotto`）+
  `transport: stdio` + `command` == `.mcp.json` 的 `command`。schema 来自
  codex-rs 的 `SkillToolDependency`（type/value/description/transport/
  command/url），改 `.mcp.json` 必须同步这里（pytest 看护）。

## 插件本体与市场

- **Codex 插件在 `codex-plugin/`**，市场清单在仓库根 `.agents/plugins/marketplace.json`
  （仓库即市场根）。**已不再是 skills-only**：2026-08-18 起同时带一个本地 stdio
  MCP server 与内嵌画布（2026-09-02 起七个工具，含 `tavotto_refresh_project`；2026-09-13 起八个，加 `tavotto_normalize_figure`；2026-09-21 起加 `tavotto_session_state`，画布的取件通道）；交接这条路一字未改。**仍不做 `.app.json`**（需要
  OpenAI 侧注册的托管 App id）。pyproject 的 `exclude` 显式挡住 `codex-plugin/`
  进 wheel/sdist。插件版本 == `tavotto.__version__`（`tests/test_codex_plugin.py` 看护）。
- **插件里那份路径规则是 `engine/locate.py` 的镜像**（插件 import 不到 tavotto，
  这份重复无法避免）。能避免的是两边悄悄漂开：
  `tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在
  Windows/macOS/Linux × 有无环境变量 × 空格与中文的矩阵上逐条比对两侧输出，
  改一边必须同步另一边。两侧都**一个 pathlib 都不用**（`Path()` 按 `os.name`
  分派，在 macOS 上连构造一条 Windows 路径都做不到）。
- **插件自己的更新检查在 `codex-plugin/.../scripts/update_check.py`**：
  每 24 小时一次（失败 1 小时后可重试）、1.5 秒超时、缓存落
  `config_dir()/codex-plugin-update.json`（**绝不往插件目录写**——那儿归 Codex
  管、可能只读、升级时整个被换掉）。四条底线：不阻塞出图、**不污染 stdout**
  （调用方读的是最后一行 JSON）、不自动下载执行、**插件版本 ≠ Tavotto 版本**
  （当前版本只从 plugin.json 读，`min_tavotto_version` 比的是 `tavotto open`
  回报的那个版本）。清单由 `scripts/make_plugin_manifest.py` 在 **release.yml**
  生成——**不能挪进 desktop-tauri.yml 的 updater-manifest**，那个 job 没配
  minisign 私钥就整个跳过，插件的更新通道会跟着悄悄停而且全绿。
- **技能的第一条硬约定：脚本与产物同目录、且必须先落成文件**（禁 `python -c` 出图）
  ——「stem ↔ 产出它的脚本」是图能不能双击进去改的全部依据。自检不靠祈祷：
  `scripts/handoff.py` 读 `tavotto open --json` 的 `registry.parameterizable`，
  为 false 时**退出码 4**。图出来了但只是死图，那不是成功。

## 改过脚本之后的显式刷新（2026-09-02，ADR 0041）

- **`tavotto_refresh_project` 是第七个工具**，也是模型改完 .py 之后该调的那一步；技能与
  README 里不再写「重开会话 / 手动刷新」让 Tavotto 跟上。实现全在 `bridge.refresh_project()`：
  先探 `127.0.0.1:5089/api/version`，可达就委托运行中的 Tavotto（`/api/projects/open
  default=false` → `/api/project/refresh?pj= reason=codex` → `/api/project/readiness`，带
  `session_client` 的本机凭据，前端当场收到 SSE）；不可达就在本进程调**同一份**
  `engine.project_refresh.refresh_project_index()` + `readiness.compute()`。**两条路都不复制
  discover、不 probe、不跑脚本**；可达但刷新失败原样带回它的 code，不退回本地再试。
- **项目只来自授权**：`session_id`（`get_session` 重新校验范围）→ `project_path`（与
  `tavotto_open_figure` 同一套 `check_scope → resolve_target → check_scope`）→ 唯一有会话的项目；
  零个 `no_project`、多个 `ambiguous_project`（错误里列会话 id，不列路径）。`reason` 固定
  `codex`，模型传什么都不透传。结果里**没有绝对路径**（项目短 id 与 `app._project_id` 同一把尺）。
- **本进程那份刷新状态 `_REFRESH_CTX` 按项目缓存**：第一次如实报 `assets.baseline: true`，
  第二次起才是跨轮 diff。测试的 autouse fixture 要清它，并把 `engine_handoff.http_json_status`
  打成不可达——**用例里绝不真的探 5089**，开发机上很可能真开着一个 Tavotto。
- **桌面版的诚实限制**：sidecar 端口不落盘，这条路对桌面用户总是 `delivered: local`，Tavotto
  里的更新靠它自己的 watcher；工具文字里如实说，不许写成「界面已同步」。
- 降级 server 的 `NORMAL_TOOLS` 与 `_BRIDGE_IMPORT` 探测语句都要跟着 bridge 的 import 走
  （`test_bridge_import_probe_matches_the_bridge` / `test_degraded_refresh_tool_is_a_structured_error_too`）。
- 看护：`tests/test_mcp_server.py` 末节十六条（schema / 授权 / 越界 / 空 diff / 新脚本 / readiness /
  不 probe 不跑脚本 / 不可达 → local / 可达委托 / 可达失败 / no_project / 多项目隔离 / 无绝对路径 /
  reason 固定 / 无注册表）+ `test_mcp_resolver.py` 的降级用例。

## 已有图的保留式规范化（2026-09-13，ADR 0051）

- **`tavotto_normalize_figure` 是第八个工具**，也是用户「已有一张图、只要改宽度 /
  字体 / 字号下限」时该走的那条路——技能里明写不许用 `tavotto_apply_overrides` 手拼。
  实现全在 `bridge.normalize_figure()`，逻辑全在 `engine/normalize.py`（纯标准库、只算
  不画）：B0 → 约定 → 计划 → `_render` → `compare` → 最多 3 轮局部修复 →
  `export(acceptance=…)` → 提交或 `_render(B0 patches)` 回退。**任何异常都回退**。
- **桥只翻译**：干涉检测在 `engine/interference.py`、产物验收在 `engine/artifactcheck.py`、
  逐元素裁切判据是 `preflight.element_overflow()`——三处都进了 `_BRIDGE_IMPORT` /
  `BRIDGE_IMPORTS_AT_MIN`，`MIN_TAVOTTO_VERSION` 因此在 v0.15.0 抬到 0.15.0。
- **提交之后会话挂合同**（`Session.contract` / `Session.normalized`）：`apply_overrides`
  只放行与已提交列表逐条相同的重发；增删改要 `user_authorized=True`（合同解除、验收
  作废）。这一道既挡修复循环 / 模型扩权，也挡画布账本不带规范化 patch 时的静默还原
  （画布 seed 的是 `overrides: []`，见 `_live_session_for` 的说明）。`export` 回执的
  `normalized.verified` 说这次导的是不是通过验收的那一版；`acceptance` 逐格式给核验结果。
- **由用户目标直接决定的规范规则不挡事务**（`normalize.TARGET_RULES`：要 120 mm 时
  `page-width` 报出来但按用户要求执行，进 `profile_conflicts` 与留档）。其余新增 /
  加重的 error 级与**确定性**干涉才挡；原图已有且未加重的保留并报告，不顺手修。
- **图例候选只收「自己干干净净」的**：换到一个还在压别的东西的位置不叫修好。
  外边距重排只在有裁切 / 压到别的子图的那个方向上做，且相对 B0 有预算。
- 看护：`tests/test_normalize.py`（逻辑）、`tests/test_mcp_normalize.py`（真链路，含一条
  真 stdio server 的工具级集成——**不是**经 Codex 宿主的端到端）、`tests/test_codex_plugin.py`
  末节（技能文字：路由、禁止的绕路、按退出码说话）。

## 工具结果的体积预算与画布取件（2026-09-21，ADR 0069，issue #457）

- **Codex 把 MCP 工具结果送给桌面 UI 的事件副本封顶在 1 MiB**，超过就把 `structuredContent`
  / `_meta` 置空——模型那份与画布自己发的 `tools/call` 不受影响，症状是「模型正常、画布永远
  等待」（422 元素 ≈ 1.3 MB）。**顺带**：`structuredContent` 非空时 codex 只把它给模型，
  `content` 文本整段丢弃。全文在 ADR 0069。
- **只有单图 open 守预算**（`CANVAS_INLINE_BUDGET_BYTES` 768 KiB，量整个 `CallToolResult`
  的紧凑 UTF-8 字节，别用默认 ensure_ascii），按 `INLINE_ELISION_STEPS` 省 svg → manifest →
  位图 → 预检清单，写 `structuredContent.elided`；说明加完再量一次，还超先退到只剩把手
  （`HANDLE_ONLY_KEYS`）再截 `content` 文字；只在单图 + 有画布时跑（批量 / 无画布没有
  iframe）；**apply 不守**（画布靠它拿新 manifest）；`_meta` 不再复制 `widgetData`。
- **`tavotto_session_state` 是画布的取件通道**：只读、不重渲染，全部来自 `Session` 上最近一次
  `_render` 留下的字段（加字段先加到 `Session`），预检复用 `Session.preflight_cache`（`_render` 必清）。降级
  `NORMAL_TOOLS` 由 `test_degraded_normal_tool_names_mirror_the_real_server` 钉成镜像。
- **画布启动三路**（`web/src/mcp/boot.ts`）：完整结果直接种（`elided` 在就不算完整，只省 svg
  会种出空画布；矢量图必须带 svg 字符串）；只有把手就取件、回来的
  `patches` 原样种进账本；空壳当场报形状（`data-boot-state` / `data-boot-detail`），30 秒没
  结果也说出口但继续收。都不自己发起 open。真宿主验收加大图一条（acceptance 文档 D 节）。
- 看护：`tests/test_mcp_server.py` 末节、`tests/test_mcp_resolver.py`、`web/src/mcp/boot.test.ts`、
  `web/e2e/mcp-canvas.spec.ts`。**跑变异一律 `-B` 并清 `__pycache__`**：等长改动一秒内还原，
  pyc 头不变，跑的是变异版。

## 会话跨进程恢复（2026-09-23，ADR 0078）

- **会话不再只活在 server 进程内存里**：提交点（open 结束 / apply 结束 / 规范化收尾，含回退）
  经 `tavotto_mcp/sessionjournal.py` 落 `data_dir()/mcp-sessions/<id>.json`；内存未命中时
  `get_session` → `_restore_session` 按记录在本进程重建。起因是宿主会换进程（WorkBuddy 一轮后
  回收 CLI、Codex 改配置重启 server）。`_render` 本身不写盘，规范化中途的候选不落盘。
- **记录不能自证权限**：恢复先用**当前连接**的 `RootAuthority` 校验项目，越界 `workspace_root_changed`
  且**拒在渲染之前**；脚本 / 入口不存、从注册表重读，stem 不在了 `session_restore_failed`。
- **明确释放的不复活**：`close_session` 与 `_evict_if_needed` 连记录一起删。结果里的
  `restored: true` 只报一次（`_take_restored`），文字带 `server.RESTORED_NOTE`。
- 落盘失败不让工具调用失败，但写 stderr。`sessionjournal` 只用标准库——不动 `_BRIDGE_IMPORT` 三处同源。
- 看护：`tests/test_mcp_session_journal.py`、`tests/test_mcp_roundtrip.py` 的两进程接力。

## 导出产物核验（2026-09-21，统一实施包 U08，ADR 0068）

- **`bridge.export` 与 HTTP 导出接的是同一份检查器接线** `engine/artifactinspect.inspect_produced`
  （`engine_exportjob.run(..., inspect=_inspect)`，`backend="worker"`）：每个封口的临时文件在提交点之前
  重新打开量事实，不合格的那一项以 `artifact_rejected` 进 `partial`、**不发布**；合格的 `files[].manifest`
  原样带出（`verdict / checks 四值 / notes / sha256 / px / size_pt…`）。旧键（`path / bytes / vector / dpi /
  status / error`）一个不动。位图 `Produced` 带期望像素（图幅 × dpi，与 `artifactcheck` 同一换算），
  `size` 那一维才量得到。契约层 `probe_asset` 按需 import（`_probe_asset`），不进桥的常驻 import 闭包。
- **来源与四身份随 manifest 走（U09，ADR 0070）**：这条路是 worker 直接序列化、没有 RenderPlan，`_produce` 每个格式经
  `engine_artifactinspect.execution_provenance()` 用与 HTTP 候选路同一份算法补齐——回执从**这条会话**的账本装配
  （`report_origin=build` + pid 核过，体检结果冒充不了）、源是这次执行的 Figure（`kind=figure`，semantic 身份对格式
  不变）、`identity{semantic, render, artifact, run}` 与 `provenance{sources, receipts, nodes}` 进 `files[].manifest`；
  装配失败不影响导出，但要进 `warnings` 说「产物身份未核验」。桥**不**新增 import（回执 / 绑定都在 `artifactinspect` 里算）。
- **给模型看的文字里「未核验」永远不是「已核验」**：`server._inspection_summary` 三组各自点名
  （未通过 / 已核验 / 未核验），一组都不省；没有 manifest = 整份未核验。这条入口只有 standard 政策
  （必需 = 完整性 + 核心尺寸）；严格政策走 HTTP 的 `inspection` 段。
- 桥新增 import `artifactinspect` → **三处同源**一起改：`scripts/make_plugin_manifest.BRIDGE_IMPORTS_AT_MIN`、
  `codex-plugin/mcp/server.py` 的 `_BRIDGE_IMPORT` 探测串（resolver 用它判老引擎够不够用，漏了它 = 交棒后桥
  ImportError 崩死；`test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge` 对拍）、桥本身；它晚于
  v0.15.0，所以 `MIN_TAVOTTO_VERSION` 在 v0.16.0 发版时抬到 0.16.0（发那一版的 PR 里才能写这个号）。
- 替身 worker 写出的文件也要过得了检查：`tests/support/artifactbytes.py`（stdlib 最小合法 PDF / PNG）。
  看护：`tests/test_mcp_export_inspection.py`（独立读取器 + 坏文件负例 + unknown 不说已核验 + 同一份接线）。

## MCP server 与内嵌画布（2026-08-18）

ADR 0005 的「skills-only / 不做 MCP server」这一条**已被 ADR 0006 推翻**
（交接那条路不变）。

- 插件清单加 `"mcpServers": "./.mcp.json"`；`.mcp.json` 是**本地 stdio**
  （`command: python3` + `args: ["./mcp/server.py"]` + `cwd/env_vars/tool_timeout_sec`）。
  字段形状取自 Codex 官方插件装出来的清单，**不要猜**。
- **`codex-plugin/mcp/tavotto_mcp/` 只翻译不实现**：会话、manifest、override、patch 规范化、
  导出全部落回 `tavotto.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。
  发给 worker 的 patches 与 Flask `/api/engine/render` 走同一条路径，所以 ADR 0003 的
  等价性不变式原样成立（`tests/test_mcp_roundtrip.py` 用真 matplotlib + 真 stdio 逐条验：
  热态 == 全新 worker 重放、figure 尺寸变、axes 几何变、关掉重开）。
- **stdout 归协议独占**：`rpc.hijack_stdout()` 把 `sys.stdout` 改道到 stderr，**必须先
  存下真正的 stdout 句柄**（`_REAL_STDOUT`）。顺序反了协议帧全写到 stderr 上，症状是
  「initialize 永远等不到响应」且零报错（开发期真撞到过）。
- **路径范围只有一个权威 `RootAuthority`**：显式 `TAVOTTO_MCP_ROOTS` → host 明确
  声明后的 `roots/list`（Roots 已弃用，只作兼容）→ 用户经 `elicitation/create`
  批准、只活在本连接内的精确 realpath → 宿主工作区变量 → 安全 cwd。模型传来的
  `project_path` 只是候选，不能自证权限；相对路径只有恰好一个可信根时才解析。
  确认框默认 false，拒绝/取消/超时一律 fail-closed，重新 initialize 清掉授权；
  **授权失败要分档**（issue #173）：唯一出处 `roots.WORKSPACE_FAILURES`，一个稳定
  `code` ↔ 一个 `disposition` ↔ 一句下一步；「宿主声明了能力却没弹框」是独立一档
  （`fix_host_wiring`），**绝不能报成用户拒绝**——两者的处置正好相反。code 只作机器
  标识，不许当文案念给用户；
  root 改变后旧 session 必须回 `workspace_root_changed`。server→client 请求只能在
  活跃 `tools/call` 内发，reader pump 必须保序且有界等待。越界一律拒，**绝不
  「就近找一个能用的」**。看护 `tests/test_mcp_roots.py`、双向协议用例与
  `tests/test_mcp_stdio.py`。**没装 Tavotto 时降级而不是退出**（降级 server 握手正常、每个工具说人话）
  ——静默退出在 Codex 里表现为「插件没有工具」。
- **启动器 `mcp/server.py` 是运行时解析器（2026-08-20 重做）**：候选链
  当前解释器 → `TAVOTTO_MCP_PYTHON`（显式，失败要指名道姓报
  `engine_unavailable`）→ `TAVOTTO_WORKER_PYTHON`/设置里的 worker.python →
  **插件自管 venv**（`<配置目录>/mcp-runtime/venv`，`--provision` 建、
  钉插件版本、绝不碰用户全局环境）→ 从 CLI 反推 shebang → PATH。**每个候选
  都要真的验证 `import tavotto.engine`**；frozen `tavotto-cli` 永远出不了
  候选。降级 server 的 tools/list **只列 `tavotto_health`**（不把七个不可用
  工具伪装成可用），`serverInfo.version` 固定 "0"，七个工具名的调用回结构化
  错误 + 恢复步骤，不声明任何资源。`--health` 输出一行 JSON 体检（引擎/
  画布/桌面版/每个候选的结论与耗时）。真 server 也有 `tavotto_health` 工具
  （出图前的能力门槛）；widget 缺失时 open/apply 在 structuredContent 里带
  `canvas_ui: {available: false, code: "widget_missing"}` 并在文字里说出口，
  `resources/read` 对缺失产物报「缺失 + 修法」而不是回空 HTML。
  看护 `tests/test_mcp_resolver.py` + `tests/test_mcp_stdio.py`。
- **`--provision` 建 venv 之前先验基础解释器的版本**（2026-09-20）：启动器允许在很老的
  `python3` 上跑（纯标准库），但 venv 继承它的版本——macOS 上 `python3` 常是 Xcode CLT
  的 3.9，而引擎的 `requires-python` 是 `>=3.10,<3.15`，区间外的解释器上 pip 只会说一句
  "No matching distribution found"（3.9 自带的 pip 21 连被 Requires-Python 忽略的版本都
  不列），Codex 把它读成「这一版还没发」。`find_venv_base()` 按 当前解释器 → PATH 上的
  `python3.14…3.10` → Homebrew / python.org / `py` 启动器的常见位置 → 裸 `python3` 的顺序
  **真的跑一遍**每个候选问版本（判据是执行不是文件名），第一个在区间内的当 base；上次
  在区间外建出来的 venv 用 `venv --clear` 重建；一个都没有就以 `no_supported_python`
  失败并逐个说出版本，**不在区间外的解释器上起 pip**；`--python` 显式指定时只认那一个——
  先验它、已有的 venv 也换到它上面（已有环境在区间内不是跳过它的理由，#453 评审 P2）。
  区间常量 `PYTHON_MIN` / `PYTHON_MAX_EXCLUSIVE` 是 `engine/projectenv.py` 的镜像
  （`test_provision_python_range_mirrors_the_engine` 对拍），改 `requires-python` 要一起改。
  **装完插件/引擎必须新开 Codex 会话**——已开的会话不重载工具，
  `codex plugin list` 的 enabled 不代表 server 健康（README 里写明了）。
- **自管环境落后于插件时启动器自己重装**（#487，2026-09-24）：插件升级会换掉插件目录，
  配置目录里的 `mcp-runtime/venv` 却原样留着上一版引擎，import 不过新桥就落到降级。
  这一格单独报 `managed_runtime_stale`（不是 `tavotto_missing`——恢复步骤不许把人支去
  另装 pipx），并且 `main()` 在降级前 **spawn 一个脱离本进程的 `--provision`**（不在
  启动路径上同步跑 pip：`startup_timeout_sec` 只有 30 s）。互斥用 `mcp-runtime/provision.lock`
  上的**内核文件锁**（POSIX `fcntl.flock` / Windows `msvcrt.locking`），**不许**退回「锁文件
  + mtime + 令牌」：那套在纯文件语义下「核对所有权再删 / 续 / 接管」永远不原子，#548 的
  Codex 评审一轮轮挖出新的竞态；内核锁随持有进程退出（含崩溃、被杀）自动释放，没有
  过期锁可言。**改环境的一方拿锁**：`--provision`（后台的与手动 / `tavotto codex install`
  跑的同一条路）动 venv 之前非阻塞地拿，拿不到就不动、报 `provision_in_progress`；
  启动器只探一下锁（拿到即放）省掉明显多余的 spawn，多起一个子进程也只会有一个真跑 pip。
  `TAVOTTO_MCP_NO_AUTO_PROVISION=1` 关掉。本次会话仍是降级、payload 带 `auto_provision`，
  文案说「后台在装、装完新开会话」。**只管「在、却 import 不过」**：能 import 但版本旧的
  自管环境不在这里重装（它此刻正被本会话用着）。看护 `tests/test_mcp_resolver.py` 末节。
- **导出先预检**：有 error **或 `not_verifiable`** 且没有 `explicit_confirm` 时
  一张图都不出（`needs_confirm`，与导出对话框同一判据；`blocking` 仍只表示
  error）。PNG 的 dpi 与 profile 的 `min_raster_dpi` 比一次，复用同一个
  `raster-dpi` id 与同一张 severity 表。默认格式取**这次调用**的 profile，
  默认导出目录也要过 `check_scope`。强制导出与确认项都记进 proof。
- **首开的「需要输入」与它的回答（U03，ADR 0057）**：`session.acquire()` 走的 `pool.get()` 在起第一个
  worker 之前可能抛 `workdir_confirmation_required`（数据只在项目根找得到 / 两处同名数据不同）——
  `_bridge_error_from_worker` 把结构化 `confirmation`（三档选项 + 各档找得到的文件 + `recommended`）
  放进 `structuredContent`，`recovery` 告诉 Codex 再调一次 `tavotto_open_figure` 并带 `workdir=`
  （`sandbox` / `project` / `project_root`，与桌面确认框、HTTP 的 `PATCH /api/engine/workdir` 是
  **同一份**决定：`engine/workdir.set_mode`，按项目记住、只问一次）。**不替用户猜**——`recommended`
  为空时必须由用户选。显式解释器失效（`explicit_python_unusable` / `project_python_unusable`）同样
  结构化投影（`explicit` 只带 source / reason，不带路径）。`workdir` 也在 `_BRIDGE_IMPORT`
  与 `BRIDGE_IMPORTS_AT_MIN` 里（v0.14.0 起就有的模块，桥只用那时就有的名字，最低版本不抬）。
- **跑前的「需要先准备依赖」与它的回答（U04，ADR 0061）**：同一处门还可能抛 `dependency_preparation_required`
  （脚本开跑要的第三方包目标环境里没有、且能一次装全）——`_bridge_error_from_worker` 把整份联合计划
  （`dependency_preparation.plan`：装什么 / 约束什么 / 认不出的 import；`targets`：装到哪）放进
  `structuredContent`，`recovery` 告诉 Codex 再调一次 `tavotto_open_figure` 并带 `prepare_dependencies=`
  （`tavotto_managed` / `project_venv` / `skip`；与桌面授权框、HTTP 的 `/api/engine/dependencies/plan` +
  `/prepare` 是**同一份**决定：`deprepair.create_joint_plan` + `prepare`，同步执行、装完接着开图；
  `skip` = 用户明确不准备直接跑，这道门一直问到有答案）。**不替用户授权**；批量 open 不接受这个参数。
  `deprepair` 进 `_BRIDGE_IMPORT` 与 `BRIDGE_IMPORTS_AT_MIN`（v0.9.x 起就有的模块；`create_joint_plan` 是
  新名字，`getattr` 守着、缺就 `engine_too_old`，最低版本不抬）。
  看护 `tests/test_mcp_server.py` 的四条 U03 用例。
- **批量打开（issue #174）**：`tavotto_open_figure` 的 `stems` / `discover_stems`
  一次开 N 张独立图，每张仍走 `open_figure` 那条路（`_resolve_project` 是单图与
  批量共用的那一段解析，范围校验顺序只有一份）。四条不许破坏：**一张失败不回滚
  整批**（失败那张带稳定 code + 自己的 stem 名）；结局是 `done`/`partial`/`failed`
  三档（词汇同 `engine/exportjob.py`），**「没尝试」是第三个桶**，会话预算在开
  之前问而不是靠 `_evict_if_needed()` 事后淘汰（同一批里先开的正好最久没用，
  事后淘汰的表现是「返回了 N 个 session_id，前几个已被自己这批挤掉」）；
  `discover_stems` 只认注册表里已登记**且产物在磁盘上**的 stem，不 probe 不猜；
  **批量结果不挂内嵌画布并把这件事说出口**——一次 `tools/call` 只带得出一块
  iframe，而画布只认完整的单图 open 结果（`web/src/mcp/main.tsx` 的
  `isOpenResult`），挂上去的表现是 iframe 永远停在「等待 tavotto_open_figure」。
  预检按 #102 第 4 条只回合计 + 阻断项点名，「没跑出结论」不并进「通过」。
- **open 之后的每一步都不许把已经登记的会话带走**（#271 评审）：预检经
  `server._safe_preflight()`，`BridgeError`（预期内、有稳定 code）与其余异常
  （`preflight_crashed`，没人诊断过）**分两档**，两条路都照常回 session_id
  ——异常逃出去时 `tools/call` 回的是一条错误结果，里头没有 id，用户手上就是
  开着却关不掉的会话（批量那一路会连同**同一次调用里已开好的其余几个**一起丢）。
- **同一张图不开第二个会话**：`_live_session_for()` 沿用**还没改过**的会话
  （`patches` 为空），因为画布 seed 的是 `overrides: []`（`web/src/mcp/session.ts`），
  沿用带 patch 的会话会让画布账本与引擎状态对不上。这条同时堵掉「批量填满预算 →
  照指引再单独开一张看画布 → 静默挤掉这批里先开的那个」。真发生淘汰时
  `_evict_if_needed()` 返回被淘汰的 id，open 的文字里**必须说出口**。
- **会话不抱 worker 引用**：池的 `MAX_ALIVE` 与桥的 `MAX_SESSIONS` 是两个数，
  必然打架——每次操作前 `pool.get()` 重新取（`Session.acquire()`）。
  会话**渲染成功之后**才登记，否则失败的 open 会堆满账本并挤掉在用的会话。
- **内嵌画布 = Tavotto 前端那一份代码**（`CanvasStage`/`OverlaySvg`/`interactions.ts`/
  `ElementInspector`/既有 stores），拖拽、命中、吸附、undo、patch 状态**没有第二份实现**。
  唯一改动是 `web/src/lib/engineTransport.ts`：一个**可选覆盖**（HTTP ↔ `tools/call`）。
  它**不 import `lib/api`**——搬默认实现进去会与 api 绕成环（TDZ），而且既有单测大量
  `vi.mock('@/lib/api')` 打桩 `engineRender`，实测会炸 7 个文件。
- UI 只挂在 `tavotto_open_figure` / `tavotto_apply_overrides` 上（其余工具的产出是文字与
  文件，挂 UI 只会让画布不停重建）；CSP 的 `connectDomains` **是空的**（sidecar 端口动态，
  写不进白名单，这也是必须走 `tools/call` 的原因）；**绝不用「开浏览器」冒充内嵌画布**；
  iframe 的 `localStorage`/`widgetState` **不存业务数据**。
- 画布产物 `codex-plugin/mcp/widget/canvas.html` 是**构建物，不进 git**（ADR 0043）：本地
  `python scripts/build_mcp_widget.py` 构建到原位置试用（`--check` 三档只给本地用）；CI 从
  本次 checkout 现建并验证完整插件（`scripts/plugin_stage.py`，`plugin-candidate` job），
  用户装到的来自发行分支 `plugin-stable`（release.yml 在固定发行 SHA 上构建、验证、发布）。
  **不许把它加回索引**（`scripts/ci/check_generated_untracked.py` 在 PR 与 main 落地审计上看着）。
  三个路径分清：源码 `codex-plugin/`（无画布）/ staging / 已装副本，见 `docs/ci/plugin-stable-channel.md`。
  **装工作副本**：`codex plugin marketplace add <仓库>/codex-plugin` + `codex plugin add tavotto@tavotto-dev`
  （`codex-plugin/.agents/plugins/marketplace.json` 是开发用的本地市场，staging 不带它）。
- **协议绿灯不能冒充 Codex Desktop iframe 证据**。真实验收必须按
  `docs/acceptance/codex-desktop-canvas.md`：新任务、真实 capability JSON、先取消
  证明 fail-closed、再人工批准精确路径、同一任务里出现并实际交互画布，且保留截图与
  工具 metadata；缺一项就继续写“未验证”。
- **内嵌 Codex 画布不发遥测**（widget 打包同一份前端代码，但没人调
  `setTelemetryEnabled`）——这是决定，不是疏漏。

## 验证

```sh
.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_roundtrip.py \
  tests/test_codex_plugin.py tests/test_preflight.py tests/test_install_locate.py \
  tests/test_normalize.py tests/test_mcp_normalize.py
python scripts/build_mcp_widget.py --check     # 改了 web/src 就得重建
python codex-plugin/mcp/server.py --self-check # MCP 手动冒烟
```
