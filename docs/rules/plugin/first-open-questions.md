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
  `structuredContent`，`recovery` 告诉 Codex 再调一次 `tavotto_open_figure` 并带 `prepare_dependencies=` 与 `prepare_impact_digest=`（原样回显 `dependency_preparation.impact_digest`；缺 → `dependency_impact_required`，不符 → `dependency_impact_changed`）
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
  采用 ≠ 安装。**采用必须候选 id 与环境代**一起**给（缺一个在 server 边界拒、桥里空环境代也拒——否则 `adopt_candidate` 跳过比对，会采用用户没看过的那一代）。`argv` 里任何指到已授权根之外的路径（绝对 / `~` / 盘符 / UNC、含 `..`；整项、`--opt=值`、`-o值` 三种形状）以 `argv_path_out_of_scope` 拒，脚本不跑、不登记配置——桥没有「用户绑定」的凭据可核，范围就是边界；项目外的路径请用户在 Tavotto 窗口里自己运行（`run_config` 引用来自用户在界面登记的配置，不查）。**先问再发**：引擎没宣告 `script-argv` / `environment-adoption`（`bridge.engine_features()`）就以　经桥执行的**已登记配置**（`run_config` 引用、脚本默认、会话恢复）每次执行前按当下真实 cwd 重查范围（`_recheck_run`；登记记录的 `source` 不可信，一律复核），登记后符号链接改指根外也拒。**响应文件（argparse `fromfile_prefix_chars`，前缀可以是任意字符）**：整项、`--opt=值` 的值、`-oVALUE` 的值，首字符命中前缀一律拒（`argv_path_out_of_scope`）。前缀集合来自 `engine/scriptargs.response_file_prefixes`（静态 `ast`、不执行）；`@` 恒拒。**可证明**（argparse 脚本；每一处 `ArgumentParser` 构造都按主分析器同一套名字解析认出、无 `**kwargs`/位置参数/`parents=`、`fromfile_prefix_chars` 缺省或字符串字面量；子类、`P = AP` 再赋值、`from argparse import *`、别处带该关键字的调用任一出现即整体说不准）-> 只拒集合内的首字符；**说不准**（读不了 / 语法错 / 无 argparse 证据 / 前缀非字面量 / 引擎没有 `scriptargs`）-> 保守口径：首字符不是字母、数字、`-` `.` `/` `_` 的一律拒。取舍：说不准的脚本在 MCP 里传不了以符号开头的值（用户在 Tavotto 窗口里自己运行）；不做递归展开校验（文件可在校验与执行之间被改）。残余：解析器在别的模块里造、而本脚本又有另一个字面量 argparse 时，静态分析看不见那个前缀（要看见得跨模块，未做）。
  **进程创建口（r4221289223，四轮 argv 校验都漏在入口上，这次按入口列全）**：桥里**只有 `bridge._spawn_worker(session, one_shot=)` 一个函数**能调 `engine_pool.get` / `.one_shot`，它在创建 / 取回进程前先 `_recheck_run`（按当下真实 cwd + canonical 重查冻结的 argv）；`tests/test_mcp_compat.py::test_bridge_has_a_single_worker_spawn_point` 用 AST 钉住——新入口直接调池会红。
  | 入口 | 怎么到 `_spawn_worker` | 另有的前置复核 |
  | --- | --- | --- |
  | open / 沿用 / 重渲染（`_render`） | `Session.acquire()` | `_choose_run` 对 `argv` / `run_config` / 脚本默认各查一次 |
  | apply / 刷新预览 | `Session.acquire()` | 无 |
  | export / 导出前重渲染 | `Session.acquire()` | 无 |
  | rerender / normalize / preflight 重取 worker | `Session.acquire()` | 无 |
  | worker 被池淘汰或死亡后下一次操作 | `Session.acquire()` | 无（r4221289223 的缺口） |
  | `verify_replay` 一次性 worker | `_spawn_worker(one_shot=True)` | 无（同上） |
  | 会话恢复（`_restore_session`） | 先 `_recheck_run`，再 `_render` -> `acquire()` | `_restore_session` 里的 `_recheck_run` |
  代价：存活会话在符号链接事后改指根外时，下一次操作也会被拒（不论 worker 是否还活着）——宁可多拒也不让「还活着」成为绕开口。
  **前端同源**：任何非 null 的 `run_config` 与任何非空 `argv` 发给引擎前都先过 `script-argv` 能力协商（`probeScript`、`createPreparationSession`、`fetchRuntimeStatus` 的 `source.run_config`、`updateScriptAnswer` / `forgetScriptAnswer` 的 `runConfig`）；旧引擎会静默忽略 `run_config` 去无参数运行，所以缺能力就以 `engine_capability_missing` 停下、一次请求都不发（`run_config: null` = 明确无参数，不问）。新增任何带运行配置的请求都要走 `requireEngineFeature`。
  `engine_capability_missing` 拒绝，绝不先无参数试一次。**采用写不进项目设置**（`projectenv.remember()` 回 False：只读 / 满）= 采用失败 `environment_save_failed`，不重置池、不开图（HTTP 手填路径同样 500）。**首问的 getpass 也是口令**：`script_needs_input` 载荷带 `input_kind`，`getpass` 一律 `input.secret=true` 并带不转交口令的提示（`secret_required` 只是重放的 reason）。失败载荷**追加**（原字段不动）`requirements[]`（`{kind, answer_with, where}`：
  再调时带哪个参数答；`None` 时 `where=tavotto_app` / `user` 说明这里答不了）、`input`（`reason` 闭集 + `secret`）、`arguments`
  （T07 只读 schema 摘要，有界、无 help 原文）、`environment`（`envadvice.recommend()` 纯读投影）；口令那一问明确要求不经 Agent。
  这些新模块比最低引擎版本新，**只经 `bridge._optional_engine()`** 取（`OPTIONAL_ENGINE_MODULES`），不进 `_BRIDGE_IMPORT` /
  `BRIDGE_IMPORTS_AT_MIN`。看护 `tests/test_mcp_compat.py`（含模拟旧引擎）、`tests/test_mcp_compat_e2e.py`（真 GUI × 真 stdio 同一份
  配置与数据）、`tests/test_engine_capabilities.py`。
