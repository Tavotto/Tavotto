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
