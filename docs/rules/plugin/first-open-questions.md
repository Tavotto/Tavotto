# 首开的「需要输入」与跑前依赖准备（U03 / U04）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

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
- **运行参数、环境采用与「没有界面时」的待办（T10，ADR 0117）**：`tavotto_open_figure` 的 `argv`（一项一个 token）/
  `run_config`（`rc_…`）二选一、批量不接受；非空 argv 走 GUI 的同一个 `runconfig.selection_for`（同一 (脚本, argv) 同一个引用，
  `source="mcp"`），`argv=[]` = 明确无参数（旧行为），都不给 = 沿用这个脚本最近一次在 Tavotto 里**明确运行**的配置（`default_selection`，
  与 GUI 磁盘面板同一判据），Agent 的 token 不改那份默认、不能标敏感。运行配置是会话身份的一部分（`_live_session_for` 按引用分开），
  会话冻结它（`acquire` / `verify_replay`），落盘只存引用（带引用的记录 `v=2`，旧插件当作不存在）。`adopt_environment=<候选 id>`
  （+ `expected_environment_generation`）委派 `envadvice.adopt_candidate`——与 HTTP `PATCH {candidate}` **同一个服务**，先采用再开图，
  采用 ≠ 安装。**先问再发**：引擎没宣告 `script-argv` / `environment-adoption`（`bridge.engine_features()`）就以
  `engine_capability_missing` 拒绝，绝不先无参数试一次。失败载荷**追加**（原字段不动）`requirements[]`（`{kind, answer_with, where}`：
  再调时带哪个参数答；`None` 时 `where=tavotto_app` / `user` 说明这里答不了）、`input`（`reason` 闭集 + `secret`）、`arguments`
  （T07 只读 schema 摘要，有界、无 help 原文）、`environment`（`envadvice.recommend()` 纯读投影）；口令那一问明确要求不经 Agent。
  这些新模块比最低引擎版本新，**只经 `bridge._optional_engine()`** 取（`OPTIONAL_ENGINE_MODULES`），不进 `_BRIDGE_IMPORT` /
  `BRIDGE_IMPORTS_AT_MIN`。看护 `tests/test_mcp_compat.py`（含模拟旧引擎）、`tests/test_mcp_compat_e2e.py`（真 GUI × 真 stdio 同一份
  配置与数据）、`tests/test_engine_capabilities.py`。
