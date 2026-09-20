# U00 · 首开主线的入口与能力清单（execspec / projectenv / deprepair / workdir / envlease / managedenv）

采样 SHA `319a506d`（2026-09-20）。每条都指到 `文件:行`（`def` 所在行）；「现状」只写读码 / 实测
得到的事实，「差距」写它与 [`04_ARCHITECTURE.md`](04_ARCHITECTURE.md) §2 合同的距离，供 U01 起草
PreparationPlan / LaunchContext / DependencyIntent 时逐条对照。**没有一条是新实现**。

## 1. 「跑一个脚本」的唯一描述：`engine/execspec.py`

| 项 | 位置 | 现状 |
|---|---|---|
| `ExecutionSpec`（frozen dataclass） | `execspec.py:95` | `profile`（safe / native）、`interpreter`、`target_kind`（script / module）、`target`（项目相对 POSIX 路径）、`entry`、`argv`、`cwd`、`env`（只存注入增量）、`project_root`、`passthrough_savefig`、`raw_target`（native）、`cwd_mode`（sandbox / project）、`sandbox` |
| safe 档默认值唯一出处 | `safe_spec()` `execspec.py:208` | cwd = 会话沙盒（`cwd_mode=project` 时 cwd = **`script.parent`**，写入边界仍是 `--sandbox`）；argv 只有脚本自身；savefig 吞掉捕获；相对路径只读回退 |
| native 档 | `native_spec()` `execspec.py:252` | 用户 invocation 原样：解释器 / cwd / argv / env；`passthrough_savefig=True` |
| worker argv 唯一出处 | `worker_argv()` `execspec.py:347`；bridge 的 `bridge_argv()` `execspec.py:302` | Python 池 `EngineWorker.__init__`（`pool.py:875-890`）、workerd `_spawn_spec()`（`pool.py:1379-1395`）、依赖修复自检 `deprepair.worker_self_test`（`deprepair.py:1100-1103`）、`probe.py` 都是消费者 |
| 序列化两档 | `to_payload()` / `stable_payload()` `execspec.py:161/169` | `STABLE_FIELDS` = profile / target_kind / target / entry / argv / passthrough_savefig / cwd_mode；机器相关字段不进 fingerprint |
| 差距（→ U01 LaunchContext） | | 04 §2 要 `interpreter, target, 原 argv, cwd 来源, 授权根 / 绑定版本, 写入模式`。现状缺：**cwd 来源三分**（`script.parent` / `project.root` / `invocation.cwd`，FO-041——今天 `project` 档就是 `script.parent`）、**授权根 / grant**（FO-047）、**写入模式**（只有「沙盒 / 脚本目录」的 cwd 维度，没有独立的写入模式维度） |

## 2. 四类入口各自怎么到达 `ExecutionSpec`

```text
HTTP   /api/engine/render … ─┐
MCP    tavotto_mcp.bridge.Session.worker ─┤─▶ enginesession.resolve() ─▶ pool.get() ─▶ EngineWorker.__init__ ─▶ execspec.safe_spec() + worker_argv()
桌面   Tauri 壳 → tavotto --desktop-sidecar → 同一个 Flask app（同上）      │                              └─▶ workerd: pool._spawn_spec() ─▶ 同一份 safe_spec / worker_argv
CLI    tavotto run -- <python> <script> ─▶ runcli._run() ─▶ execspec.native_spec() + bridge_argv() ─▶ 用户解释器跑 bridge_runner ─▶ NativeRelay ─▶ 桌面 /api/native/pending/<id>/approve 接手
```

| 入口 | 路径（file:line） | 到 spec 的形态 | 备注 |
|---|---|---|---|
| **HTTP**（浏览器 / 桌面 WebView 都走它） | `app.py:3384 api_engine_render` → `app.py:3340 _engine_worker()` / `app.py:3322 _safe_worker()` → `enginesession.resolve()` (`enginesession.py:87`) → `pool.get()` (`pool.py:2031`) | safe：`EngineWorker.__init__` 里 `execspec.safe_spec(...)`；`cwd_mode` 由 `workdir.mode_for(figures_dir)` 取（三条 spawn 路径同一处：Python 池 / `_spawn_spec` / `one_shot`） | 「谁来渲染」只在 `enginesession.resolve()` 分支（profile 决定，不由「谁碰巧可用」决定）；native 面板没有 live route 时报 `native_session_offline`，绝不退回 safe |
| **MCP**（Codex 插件，进程内） | `codex-plugin/mcp/tavotto_mcp/bridge.py:272`（`Session.worker` → `engine_pool.get(script, project, entry)`）；写回重放 `codex-plugin/mcp/tavotto_mcp/bridge.py:2156` → `engine_pool.one_shot()` | 与 HTTP 同一条 `pool.get()`；**不经 `enginesession.resolve()`**（MCP 没有 native 面板概念，全部 safe） | `refresh_project` 可选经 HTTP 找已开着的桌面实例（`codex-plugin/mcp/tavotto_mcp/bridge.py:855-944`），渲染本身不走 HTTP |
| **CLI**（`tavotto run`，native） | `engine/cli.py:23 COMMANDS` → `runcli.cli()` `runcli.py:102` → `_run()` `:142` → `_spawn_user_python()` `:236`：`execspec.native_spec()` + `execspec.bridge_argv()` | 用户的解释器、cwd、argv、env 原样；只注入一次性 `TAVOTTO_BRIDGE_TOKEN`；`creationflags=INHERIT_CONSOLE` | 需要桌面在线（`handoff.find_desktop_app()`，否则 `NATIVE_DESKTOP_REQUIRED`；`--x-no-desktop` 是测试旁路）；确认前一行用户代码不跑（`nativehandoff.create` → `/api/native/pending/<id>` → `approve`）；权限记忆 `nativeperm.py:52` / `:62` / `:90` |
| **桌面**（Tauri） | `src-tauri` 起 `tavotto --desktop-sidecar`（`desktop.py:230`）→ `localserver.LocalWSGIServer` → 同一个 Flask app | 与 HTTP 完全相同（sidecar 只换了认证握手：stdin nonce → HttpOnly cookie） | `tavotto open <路径>`（`handoff.desktop_argv()` ↔ `src-tauri parse_open_args()` 严格同源对）只负责把项目送进桌面壳，不建 spec |

差距（→ U01 PreparationPlan）：四类入口在 spec 之前**没有共同的「准备」层**——环境证据（谁来跑）、
依赖意图、cwd/数据绑定、授权分别散在 `pool.resolve_worker_python` / `depresolve` / `workdir` /
`nativeperm` + 前端确认文案里。04 §1 的 ProjectPreparation 要在 `pool.get()` 之前收拢它们，
但 **消费现有 ExecutionSpec，不放进 RenderCore**。

## 3. 「用哪个 Python」：`engine/pool.py` + `engine/projectenv.py` + `engine/runtime.py`

| 能力 | 位置 | 现状 |
|---|---|---|
| 解释器优先级链（唯一出处） | `pool._prioritized_candidates()` `pool.py:483` | ① `TAVOTTO_WORKER_PYTHON` ② 设置里指定的（`config.worker_python()`）③ 内置 runtime（`runtime.bundled_python()`）④ 自身（非 frozen）⑤ 系统 Python / Conda 的固定路径 + `shutil.which` |
| 项目级决策（唯一出处） | `pool.resolve_worker_python(figures_dir)` `pool.py:668` | ①② 显式来源各自判（不短路）→ ③ **项目记住的解释器**（`projectenv.remembered`，每进程复检一次 `_has_matplotlib`）→ ④ 老链条。来源标签 `env_override / configured / project_venv / managed_venv / system_interpreter / bundled / current_process / system` |
| 「谁有 matplotlib」探测 | `pool._has_matplotlib()`（`runtime.child_env()` / `child_args()` 同一套 env/args） | 本机实测选中 `/opt/homebrew/opt/python@3.13/libexec/bin/python3`（source=system） |
| 项目内 venv 发现 + 体检 + 记住 | `projectenv.discover()` `:233`（只认 `.venv` / `venv` / `env` 三个目录名）、`probe_environment()` `:338`（不带 `-I`、env 原样、cwd 换空临时目录；报 Python 版本 / matplotlib 版本 / `support_status`）、`remember()` `:627`（项目设置，项目相对路径）、`forget()` / `reset_cache()` | 硬约束：**以整个解释器为单位切换，绝不混装 site-packages**（`test_project_env.py::test_never_mixes_site_packages`） |
| 系统解释器体检（ADR 0044） | `projectenv.probe_system_candidates()` `:450`、`healthy_system_candidate()` / `rejected_system_candidates()` `:511/:520` | 候选来自 `pool.system_python_candidates()` `pool.py:539`；健康的作为 `system_interpreter` 目标（采用不装）；不合格的单列 `system_rejected` + 原因 |
| 缺包时自动接手 | `pool.should_try_project_env()` `pool.py:2081`（只认 `missing_dependency`）→ `pool.try_project_env()` `:2095` → `projectenv.resolve_for_missing_dependency()` `:706`；app 侧 `_switched_to_project_env()` `app.py:3276` | 一次 build 最多自动切一次（`mark_attempted`）；切成后 `g.environment_switched` 让前端 toast；**不无感切换系统解释器** |
| 内置 runtime | `engine/runtime.py`（`bundled_python()`、`manifest schema 2` 平台 / 架构校验、`child_env()` 摘 PYTHONHOME/PYTHONPATH…、`-B`、`MPLCONFIGDIR` 改道） | 桌面版才有；pip / Linux 形态 `ships_bundled_runtime()` 为 False 时 `bundled_runtime_missing/invalid` 两个 code 都不给 |
| bootstrap（没有任何 Python 有 matplotlib 时） | `engine/bootstrap.py`：`status()` `:117`、`install()` `:176`（在 `data_dir/worker-env` 建 venv 装 matplotlib；`find_base_python()`）；HTTP `POST /api/engine/environment/install` `app.py:4619` | 桌面版（runtime expected）时拒绝现场建 venv（那是安装文件不完整） |
| 状态 API | `GET /api/engine/environment` `app.py:4527`（`?probe=1` 真 import 一遍）；`_project_environment_state()` `app.py:4552`（不体检，只报记住的决策 + `managed` + `workdir`） | 诊断包同一份 |
| 手动指定 | `PATCH /api/engine/environment` `app.py:4643`（`scope=global` 写设置；`scope=project` + `module` 走 `_set_project_environment()` `app.py:4681`，记 `trigger=missing_dependency / user_selected`） | 用户显式选择永远压过自动决策 |
| 差距（→ U01/U03） | | 「原选择与候选理由」（04 §2 PreparationPlan）现状只有来源标签 + `projectenv.state()` 的 `automatic / trigger / module`；被拒候选有原因（`system_rejected`）但项目内 venv 被拒的原因只在体检结构里；**Python 要求**（脚本要哪个 minor）没有任何地方声明 |

## 4. 工作目录：`engine/workdir.py`（ADR 0047）

| 能力 | 位置 | 现状 |
|---|---|---|
| 模式 | `MODES = (sandbox, project)` `workdir.py:32`；`mode_for()` `:41`、`set_mode()` `:50`、`state()` `:61` | 项目级设置 `project_settings["workdir"]`，不写全局；不认识的值当默认（沙盒） |
| 生效点 | 三条 spawn 路径从 `mode_for()` 取（Python 池 / `_spawn_spec` / `one_shot`）；`worker.py:160 os.chdir(self.workdir or self.sandbox)` | 默认模式 argv 逐字节不变，project 模式只多 `--cwd`；写回重放与热态同一个 cwd |
| HTTP | `PATCH /api/engine/workdir` `app.py:4590` | 改了就 `shutdown_all(root)` |
| 相对路径只读回退（沙盒模式） | `figcapture.py`（`builtins.open` / `io.open` / 3.10 `Path.open` 三处 patch；四条同时成立才改指） | `exists` / `glob` / C++ 读取器在盲区 → `no_figures_captured`（`pool._explain_empty_capture` `pool.py:417`） |
| 差距（→ U03） | | FO-041 三分（见 §1）；FO-045「歧义数据需确认」——今天沙盒模式下同名文件只读回退到脚本目录，**没有「歧义 → 请求选择」这一步**（夹具 `same_name_data` 就是给这条用的）；FO-047 grant 只在前端 |

## 5. 环境占用：`engine/envlease.py`

| 能力 | 位置 | 现状 |
|---|---|---|
| 一张表 | `env_key_of()` `:87`、`is_mutating()` `:95`、`native_sessions_on()` `:107`、`state_of()` `:112`、`snapshot()` `:123` | 键按解释器路径字符串（不 realpath：venv 与其 base 是两个环境） |
| native 租约 | `acquire_native()` / `release_native()` / `native_lease()` `:135-182` | `NativeSession` 起时占、`_release_lease()` `nativesession.py:399` 放 |
| 安装期锁 | `mutating()` `:182`（`pool.mutating_environment()` `pool.py:2013` 是它的消费者） | 锁粒度 = 一个环境；`pool.get()` 撞上 `is_mutating` 抛 `environment_mutating`（`pool.py:2041`） |
| 差距（→ U04 FO-039） | | 「复用 envlease 且不杀活跃 native」：`mutating()` 与 `native_sessions_on()` 都在，但「安装前如果该解释器上有活跃 native 会话怎么办」的裁决要在 U04 写成用例（现状读码：`deprepair` 只对 safe worker 做 `shutdown_all`，native 会话不在池里） |

## 6. 依赖：`engine/depresolve.py` + `engine/deprepair.py` + `engine/managedenv.py`（ADR 0019 / 0038 / 0044）

| 能力 | 位置 | 现状（含 U00 实测） |
|---|---|---|
| import 名 → distribution | `depresolve.resolve()`；两档高置信 `project_declared`（`REQUIREMENTS_GLOBS` `:324` + `pyproject.toml`）/ `curated`（`curated_distribution()` `:180`）+ `user_specified` | **没有「同名试试看」**；`labtools_local` / 未知名 → `None`（实测） |
| 声明解析 | `parse_requirements_text()` `:363`、`parse_requirement()` `:226`、`_pyproject_dependency_strings()` `:385` | **实测**：marker 整段丢、extras 剥掉、同名冲突第一条静默胜出、`constraints.txt` 不读、`parse_requirement` 对 extras / marker 回 `None`（第一版刻意窄，docstring 写明）——见 [`U00_BASELINE.md`](U00_BASELINE.md) §4.3 |
| 包名语法 = 安全边界 | `parse_requirement` 白名单 + `_pip_install` 装前再验 | 只有 `name` + 运算符版本段；`-r` / `--index-url` / URL / 路径 / 空格一律拒 |
| 修复报价 | `deprepair.offer()` `:327` | 只读；目标顺序：`system_interpreter`（采用不装，不进 `TARGETS`）→ `project_venv`（要确认）→ `tavotto_managed`；解析不出 distribution 时 `targets=[]` |
| 计划绑定 | `create_plan()` `:436` → `install()` / `install_async()` `:565/:549` → `cancel()` `:539`；HTTP `/api/engine/dependency/{plan,install,cancel,state}` `app.py:4783-4848` | 执行端一个字节不从请求体读；执行前重算环境指纹（`repair_plan_stale`）；`rounds_remaining` 每 (项目, 脚本) 最多 3 轮 |
| 三层验证 | `worker_self_test()` `:1083`（argv 走 `execspec.worker_argv`） | import 包 / import matplotlib / 真起 worker 跑 build |
| 受管环境 | `managedenv`：`env_dir()` `:93` = `<data_dir>/environments/<项目指纹>/`、`venv/`、`environment.json`（schema 1，`state ready/incomplete`，`installed[]` 记账，`record_snapshot` freeze 快照）；`rebuild_managed()` `deprepair.py:761`；HTTP `POST /api/engine/environment/managed/rebuild` `app.py:4856` | `BASE_PACKAGES = ("matplotlib",)`；**内置 runtime 永远不是安装目标**；用户 venv 只进不退、取消不假装 rollback |
| 包管理（ADR 0038） | `inventory()` `:1355`、`protected_distributions()` `:1371`、`create_package_job()` `:1781` → `run_package_job()` `:1891`；`lookup_package()` `:1727`（`pip index versions`，`--retries 1`）；HTTP `/api/engine/packages*` `app.py:4881-5018` | 目标只有受管环境；`package_protected` 拒卸内置 |
| 差距（→ U04 DependencyIntent） | | 04 §2 要 `name/version/extras/marker/selected group/source/constraints；unknown 不是空依赖`。现状：extras / marker / constraints / group 四个维度**都不存在**，unknown 在 `parse_requirements_text` 里是「安静跳过」（= 空依赖）。FO-036 的「最终路径 + active 指针」是新目标（现状是状态标记）。**内置 runtime 不是安装目标**这条边界与 04 一致，保留 |

## 7. 相关但不属于首开主线、U01 合同要引用的现状

| 项 | 位置 | 与合同的关系 |
|---|---|---|
| 捕获描述符 | `figcapture.CapturedFigureDescriptor`；build 响应 `descriptors`（worker v1 / browser 同形，`test_compat_capture_parity.py`） | SourceArtifact 的起点：`runtime:<script>#<stem>` 不透明 id、`source_fingerprint` 只是 stale hint、writeback 能力只能派生；**savefig kwargs 一个都没记**（`docs/rules/backend/figure-capture-and-execution.md`） |
| 就绪判据 | `engine/readiness.py`（六态 `editable / auto_linkable / needs_probe / conflict / source_missing / layout_only` + 十个 reason_code；`GET /api/project/readiness`） | FO-054 的 `ready_editable` = `editable`；只报告不动手 |
| 写回基线的文件身份 | `engine/bakedbaseline.py:baseline_matches_file`（size → mtime_ns → sha1 三步） | SourceArtifact「实际产物 bytes hash」可复用 |
| 导出作业 | `engine/exportjob.py`（临时目录 → `atomicio.publish_file` 原子 replace；`partial` 独立一档；终局字段先于 `status`） | ArtifactManifest 的 plan / observed / policy 分开要接的现状 |
| 执行计时 | worker `timings` + `pool` 的 `queue_wait_ms / total_ms` + `app` 的 `worker_get_ms`（冷启动） | ExecutionReceipt 的「实际 Python / prefix / 关键包版本」今天**只有** `pool` 的来源标签 + `?probe=1` 的 imports，worker 不自报 `sys.prefix`（U01 要加，加字段不升协议版） |
