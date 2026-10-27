# Execution Trace：层级执行追踪（ADR 0151）

> 2026-10-27 随 PR1（契约、生命周期与有界存储）新增；速查行在 `src/tavotto/AGENTS.md`「按改动路径找细则」表里（由下面的
> `agents-route` 块生成）。这里是这一主题规则的**唯一全文**。
> 看护用例：`tests/test_exectrace.py`、`tests/test_exectrace_pipelines.py`、`tests/test_exectrace_api.py`、`tests/test_exectrace_diag.py`、`tests/test_exectrace_matrix.py`、`tests/test_exectrace_otel.py`、`tests/test_exectrace_analysis.py`、`tests/test_exectrace_bench.py`（旧阶段轨迹仍由 `tests/test_trace.py` 看护，见 `preparation-and-receipts.md`）。

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 325
改到: `engine/exectrace.py`、`engine/exectrace_diag.py`、`engine/exectrace_otel.py`、`engine/ai_pricing.py`、`data_dir()/cache/exec-trace/`、任何 `start_span` / `end_span` / `event` 调用点
主题: Execution Trace（层级执行追踪，ADR 0151） → `execution-trace.md`
必守要点: 只观察不判定；自由字符串没有入口；采集失败不影响业务；时间不伪造
-->

- **观察层，不是状态权威。** 任务状态归 `ai_tasks` / `ai_history`，工具执行归 `fixtools.run_tool`，诊断快照归 `taskdiag`，阶段事实归
  旧 `engine/trace.py`。Trace 只记这些模块已经产生的事实；**不从 Trace 读回任何东西去驱动业务**（不重试、不改状态、不授权）。
  审批只观察（`approved_by` 词表），不能授予权限。
- **旧阶段轨迹一行不动。** `observe_stage_trace()` 只读 phase / outcome（不读 `facts`），映射成 `stage` 观察点，不造区间。
- **白名单投影，自由字符串没有入口。** 名字 / kind / 状态 / Event 名闭集；`error_code` 只收 `[a-z][a-z0-9_]{0,63}`；
  `task_ref` / `session_ref` 只收 id 形状；attributes 只收 `ATTRIBUTES` 登记的键，值按类型校验。加键 / 加名字 / 加种类先改闭集并同步
  `tests/test_exectrace.py`。词表只能用**代码里的静态注册表**扩充（`register_vocabulary`）——绝不把用户文件名、工具参数、Prompt、
  模型输出、路径、环境变量、命令行塞进词表。
- **一次 Span 只结束一次**；冻结时开着的子 Span 随根（取消 / 超时 / 中断）或记 `unknown` + `unclosed`，永不记 ok；冻结后的写入只计
  `late_count`；`first_failure` 是第一次失败，不被后来的失败、重试覆盖。重试是 `attempt` 递增的兄弟 Span。
- **时间不伪造。** Span 偏移只用本进程单调钟；墙钟只给 Trace 起止。别的进程的时长走 `reported_duration_ms`（`timing="reported"`），
  只知道时刻的用 `point()`；不把两个相邻观察点之间的时间算给前一个阶段。第三方 CLI 内部看不见就只记外层区间 + `opaque=true`。
- **Token / 费用**：没有可信用量就是 `None`，不是 0；`source` 必须是 `reported` / `estimated`；费用只在带币种与定价版本时收，
  永远 `estimated=True`。不为统计额外发模型请求。
- **采集失败不影响业务。** 公开方法吞异常计 `internal_errors`；存储不可写 / 损坏只计数；`TAVOTTO_NO_EXEC_TRACE=1` 拿到 `NullTrace`。
  调用点不得自己 `try` 掉业务异常来「保护」采集，也不得在业务锁里等采集。
- **存储**：`data_dir()/cache/exec-trace/`，每 Trace 一个 JSON，`atomicio` 原子写，**只在后台写线程里写**（业务线程只 `submit`，绝不在业务锁里等磁盘）；上限 / 保留 / 节流 / 恢复的数值是
  `exectrace.py` 顶部常量（唯一出处，ADR 0151 §六列了当时的值）。只逐出冻结的；启动 `recover()` 把属主已死的运行中记录收成
  `interrupted`。读一律带 scope（项目的不透明键），对不上当不存在。
- **读取端点（PR3）**：`GET /api/exec-trace` 只读零执行、按当前项目认领（两把 scope 键），找不到一律 404 + `available:false`；前端规则在 `docs/rules/frontend/execution-trace-ui.md`。
- **出门（PR4）**：离开本机的只有 `exectrace_diag.export_projection` / `document` / `bundle_section` 的产物（诊断包 `exec-traces.json`，schema 9；
  用户导出 `/api/exec-trace/diagnostic`）。它们是独立的第二道白名单，不复制快照；归因只说证据说得出的话（说不出 = `unknown`，最慢 ≠ 根因）。
  OTel 只有转换函数 `exectrace_otel.to_otlp_json`（估算用量不进 `gen_ai.*`）；Guardian 分析 `scripts/ci/exectrace_analysis.py` 不给兼容结论。
  性能预算脚本 `scripts/dev/exectrace_bench.py`。
- **不进遥测、不自动上传。** 诊断包要带 Trace 是 PR4 的事：另做导出投影、升 `BUNDLE_SCHEMA_VERSION`（`diagnostics.md`）。
  这份本地存储是诊断规则「不写盘」之外经维护者裁决的登记例外（2026-10-27），仅限本目录、仅限白名单字段。
- **进程边界**：只在 Flask 父进程 import；worker / bridge / agent_runner 侧不 import（它们报来的事实由父进程收到时记）。
- **接入点（PR2）**：每条链路一个根、一个冻结点，表在 ADR 0151「修订：PR2」。要点：AI 任务在账本预留**成功之后**开、`_finish_session`
  冻结；工具 Span **只在 `fixtools.run_tool`**；Task Core 阶段只经 `ai_tasks._trace_phase`；准备 / 导出在各自的 `_record_terminal`。
  新链路接入照这张表加一行，不在业务代码里另起第二个冻结点。
- **费用**：`engine/ai_pricing.py` 是定价的唯一出处（带版本与出处）；改价 = 改表 + 升 `PRICING_VERSION` + 核出处。表外的厂商 / 模型永远 `null`。
