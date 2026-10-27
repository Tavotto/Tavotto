# ADR 0151：Execution Trace——跨任务的层级执行追踪（观察层，不是第二个状态权威）

日期：2026-10-27 · 状态：**Accepted（PR1：契约、生命周期与有界存储）**；PR2 接执行链路、PR3 接 Waterfall、PR4 接诊断 / Guardian 各自修订本 ADR
相关：[0071 阶段轨迹与旧计划失效](0071-trace-and-stale-plan-policy.md)（本 ADR 修订其「不建全系统追踪平台」一条的边界）、
[0138 TAF](0138-tavotto-ai-foundation.md)（任务状态的权威）、[0132 AI 请求幂等](0132-ai-submit-idempotency-and-draft-cache.md)、
[0149 修复工具箱](0149-fix-toolbox.md)（`run_tool` 唯一执行入口、本地审计）、[0016 诊断 V2](0016-diagnostics-v2-frontend-state-tracing.md)、
[0118 诊断发送](0118-diagnostics-send-report.md)；规则全文 `docs/rules/backend/execution-trace.md`；看护 `tests/test_exectrace.py`。

## 维护者裁决（2026-10-27）

1. **登记 1.0 豁免，默认开**（`docs/1.0-release-readiness.md` §1.8）；逃生口 `TAVOTTO_NO_EXEC_TRACE=1`。界面只在既有「详情」折叠里展开
   完整 Waterfall，不加顶级导航、不增加用户操作步骤。
2. **有界落盘**：`data_dir()/cache/exec-trace/`，用于刷新 / 重启后恢复保留期内的 Trace、识别崩溃中断。
3. **费用按带版本的定价表估算**（PR2 落地），永远带币种、定价版本与 `estimated=True`；拿不到可信用量（第三方 CLI）就是 `null`，不是 0。

## 问题

ADR 0071 的阶段轨迹回答「卡在哪一步、哪一步坏的」，它是一条**扁平序列**、只活在一次准备 / 导出作业里、不落盘。
AI 改图与智能修复的执行是**树**：一个任务里有 Agent 运行、多次模型请求、并行的工具调用、等用户审批、装依赖、再跑脚本、验证、提交；
还有重试与取消。今天这些事实分散在 `ai_tasks`（阶段 / 纠错轮次）、`ai_bridge`（会话终局）、`agent_runner`（Usage 账本，父进程丢掉了）、
`fixtools`（审计行，不记耗时）里，没有一处能回答「先坏在哪、花了多久、重试过没有、记录全不全」。

## 决定

### 一、三个逻辑实体，一个模块

`engine/exectrace.py`（纯标准库，只在 Flask 父进程）：

- **Trace**：`trace_id`（`secrets` 128 位）、`schema_version`、`operation_kind`（闭集）、`started_at` / `ended_at`（墙钟）、`status`、
  `root_span_id`、`task_ref` / `session_ref`（不透明 id 形状）、`truncated` / `dropped_count`，另加 `first_failure`、`complete`、
  `rejected_count` / `late_count` / `clock_anomalies` / `internal_errors` 几个诚实计数。
- **Span**：`span_id`、`parent_span_id`、`name`（**闭集阶段名**）、`kind`（闭集）、`start_offset_ms` / `end_offset_ms` / `duration_ms`、
  `status`、`error_code`（`[a-z][a-z0-9_]` 形状）、`attempt`、`timing`（`measured` / `reported` / `point` / `unmeasured`）、
  `attributes`（白名单）、`usage`（可选）。支持嵌套与并行。
- **Event**：时间点事实（闭集名），带单调递增 `seq` 与可选去重键；不从 Event 推导任何状态。

一次真实用户任务一个根 Trace；重试是同一 Trace 里 `attempt` 不同的兄弟 Span。trace 与任务的绑定在 PR2 经已有的
`task_id` / `client_request_id`（0132 幂等账本）——重复请求不会生出第二份执行，也就不会生出第二份 Trace。

### 二、与旧阶段轨迹的关系：只读适配，不共享判定

评估过三条路：

| 路线 | 结论 |
| --- | --- |
| 在 `Trace` 上兼容扩展父子关系 | 否：`to_payload()` 是公开投影（`inspector.public_projection`、导出 / 准备响应、`taskdiag.stages`、导出遥测），换形要动五个消费者；`facts` 收任意短串，与本层的白名单相悖 |
| 两套底层共享记录模型 | 否：旧轨迹的「丢中间」「第一次失败是根因」是它自己的契约（`tests/test_trace.py`），共享模型等于让两边的上限 / 截断语义耦合 |
| **独立层级契约 + 旧轨迹只读适配**（采用） | `observe_stage_trace()` 把旧事件映射成 `stage` 观察点：只读 phase / outcome，**不读 facts**、不造区间；阶段事实的判定权威仍是旧轨迹 |

### 三、时间语义

墙钟只用于 Trace 的 `started_at` / `ended_at`（跨进程关联与显示）；Span 偏移一律用本进程单调钟（可注入）。单调钟倒退时钉在上次的值、
记 `clock_anomalies`，永不出负时长。别的进程报来的只有**时长**（`reported_duration_ms`，例如 agent_runner 账本的 `ms`）：起点由结束时刻倒推、
`timing="reported"`，比观察到的窗口还长就不信它。只知道「这一刻发生了」的是零宽 `point`。第三方 CLI 的内部步骤看不见：PR2 只记外层区间，
标 `opaque=true`，不虚构内部 Span。

### 四、生命周期纪律

一个 Span 只能合法结束一次（第二次返回 False、不改字段）；父的终局不改写子；冻结时还开着的子 Span：根是取消 / 超时 / 中断就随根，
其余一律 `unknown` + `error_code="unclosed"` + `timing="unmeasured"`——**异常退出不会留下被解释成成功的 Span**。冻结后的写入只计
`late_count`（取消后迟到的事件不污染）。`first_failure` 记第一次失败（与 0071 同一条：后来的连带失败、重试的失败不盖掉它）。
所有公开方法吞异常、计 `internal_errors`：**采集故障不让业务失败**。

### 五、隐私：生产者白名单投影

不先存后删。Span 名 / kind / 状态 / Event 名是闭集；`error_code` 只收 code 形状；`task_ref` / `session_ref` 只收 id 形状；
attributes 只收 `ATTRIBUTES` 登记的键，值按类型（计数 / 毫秒 / 布尔 / 闭集词表）校验。词表由生产者用**代码里的静态注册表**扩充
（`register_vocabulary`，值还要过 id 形状），用户数据不是词表——自由字符串**没有入口**。隐私 Canary（密钥、邮箱、两种路径、Prompt、
样本编号、请求头）注入所有入口，快照与落盘字节都不出现。Trace 不进遥测、不上传；对外导出（PR4）另有自己的读取边界与投影。

### 六、有界本地存储

不进 `ai_history`（那是用户可删的会话历史 + 幂等账本的权威，按事件写会打到它跨进程的 `BEGIN IMMEDIATE` 热路径），不进 `taskdiag`
（终局冻结快照、只在内存）。最小实现是目录 + 每 Trace 一个 JSON，原子替换写（`atomicio.write_bytes`）：

| 项 | 值 |
| --- | --- |
| 位置 | `data_dir()/cache/exec-trace/<trace_id>.open.json`（运行中）→ `<trace_id>.json`（冻结） |
| 运行中写盘 | 开始 / 冻结各一次 + 结束 Span 时节流 `CHECKPOINT_S = 2 s`；**全部在后台写线程**（`TraceStore(background=True)`，待写表按 Trace 合并、写那一刻取最新快照）——业务线程（持着导出全局锁 / AI 请求锁 / 脚本锁）永不做磁盘 I/O（Codex #1453 P1）；正常退出 `atexit` 最多等 1 s 写完 |
| 单条 | `MAX_RECORD_BYTES = 256 KiB`，超了先砍事件中段、再砍 Span 尾，记 `truncated`、`complete=false` |
| 总量 / 保留 | `MAX_TOTAL_BYTES = 8 MiB`、`RETENTION_S = 7 天`；只逐出冻结的、按 mtime 从旧到新；运行中的永不逐出 |
| 每条内存上限 | `MAX_SPANS = 512`、`MAX_EVENTS = 1024`（丢中间）、去重键 4096 |
| 崩溃恢复 | `recover()`：属主 pid 不在了（`procalive.pid_alive`）或进程令牌不是本进程的 `.open` 收成 `interrupted`，开着的 Span 记 `unknown` |
| 项目隔离 | 记录带 `scope`（项目的不透明键）；`load` / `Recorder.get` scope 对不上当不存在；id 只收 32 位 hex，路径穿越进不来 |
| 故障 | 不可写 / 损坏只计 `write_errors` / `corrupt`，业务照常 |

### 七、四层数据分开

Execution Trace（本地执行观察）≠ Task Diagnostic（`taskdiag` 终局冻结快照，仍是任务诊断的权威）≠ Telemetry（单独同意的匿名统计，
白名单不动）≠ Agent History（`ai_history`，用户可见的会话历史）。它们可以按 id 关联，不互相复制、不互相判定。

## PR1 不做

不接任何业务模块（PR2）；不加端点、不改 SSE、不改前端（PR3）；不改诊断包结构（PR4，届时升 `BUNDLE_SCHEMA_VERSION`）；
定价表在 PR2 随 usage 接入落地。

## 反证（`tests/test_exectrace.py`；脚本 scratchpad `xt_mut.py`）

| 变异 | 红在 |
| --- | --- |
| 后来的失败覆盖 `first_failure` | `test_parallel_spans_overlap_and_keep_their_own_timing` |
| 冻结时开着的 Span 记成 ok | `test_spans_left_open_at_freeze_are_never_read_as_success` |
| 词表校验放行任意字符串 | `test_canary_strings_never_reach_the_public_projection_or_the_disk` |
| 逐出不区分运行中 | `test_store_eviction_only_touches_frozen_records_oldest_first` |
| Span 可以结束两次 | `test_a_span_ends_once_and_the_second_end_changes_nothing` |
| 去重失效 | `test_duplicate_delivery_is_recorded_once` |
| 读盘不核 scope | `test_a_trace_from_another_scope_reads_as_absent` |
| 恢复时记成 ok | `test_a_running_record_whose_owner_died_is_recovered_as_interrupted` |
| 采集异常外抛 | `test_internal_faults_are_swallowed_and_counted` |

## 修订：PR2——真实执行边界接入（2026-10-27）

每个 Span 都挂在**已有**的执行边界上；没有新的编排器、没有复制业务日志、执行顺序一行没变。

| 链路 | 根 Trace 在哪开 / 冻结 | Span 落点（唯一生产者） |
| --- | --- | --- |
| AI 改图 / 转换（`ai_bridge.run`） | 账本原子预留**成功之后**开（重复请求在此之前被 0132 挡下，没有第二份执行也没有第二份 Trace）；终态唯一写入点 `_finish_session` 冻结 | `accept`（point）；每个 CLI 进程一个 `agent_run`（修正轮 attempt 递增；第三方 CLI `opaque=true`，内部不虚构）；内置执行器 stdout 的 `usage` 行 → `model_request`（`timing=reported`，时长取子进程这一轮的 `ms`，起点不早于父）；`tool` 行 → `tool_call` point（只有结束时刻） |
| Task Core 阶段（`ai_tasks._drive`） | 跟随桥的 Trace（`TaskRun.trace`）；`run_task`（确定性任务）自己开、自己冻结 | `phase()` 推进即 `plan → execute → validate → (correct → validate)* → commit → verify`；提交前检查没过而进入修正 = 那次 `validate` 记 `failed/precommit_not_verified`；编排结束时开着的那段按真实结局（取消 / 超时 / 错误码）收尾 |
| 智能修复（`fixsession` + `fixtools.run_tool`） | `FixSessions.start` 开；`_settle` 冻结（`fixed`/`convert` → ok，`not_fixed` → failed） | `tool_call` **只在 `run_tool` 记**（内置执行器与 CLI 经 MCP 同一入口，父层不重复计）；卡片等待 `user_wait`（挂在发起它的工具下，答复 / 拒绝 / 超时 / 取消如实）；内置循环同进程的 `usage` → `model_request` |
| 准备（`preparation._execute`） | 执行线程开；`_record_terminal` 冻结 | `script_run`（真实执行区间，本进程单调钟）；旧阶段轨迹经 `observe_stage_trace` 映射成观察点 |
| 导出（`exportjob.prepare`） | 有项目上下文才开；`_record_terminal` 冻结 | 阶段轨迹观察点；`partial` 记失败、`conflict`（等用户裁决撞名）记 ok + outcome |

**四件事分开记**（根 Span 的 attributes）：`changed`（文件变没变）、`refresh`（统一刷新结局）、`outcome`（任务结局）各一条，与根的 `status`
（会话终态）互不合并；Agent 退出码在 `agent_run.exit_ok`。验证结论仍在 TaskResult 里，Trace 不重判。

**Token 与费用**（裁决 3）：`ai_pricing.py` 带版本（`PRICING_VERSION`）的定价表，只收从官方定价页逐条核过的条目（Anthropic Claude 5 系列、
OpenAI gpt-5 / gpt-5-mini，出处见 `SOURCES`）；厂商只认连接的 `base_url` 与目录预设**逐字相同**，自定义网关不冒充官方计价。只有
服务端报了用量（`reported=true`）才算费用；字符数估算的用量 Token 照记、标 `estimated`、费用 `null`。Anthropic 缓存**写入**按基础输入价算
（用量里没拆出写入量），所以永远 `estimated=True`。第三方 CLI 一律没有 Token / 费用。

**agent_runner 协议的加法**：`usage` 事件多带 `ms`（这一轮的时长）、`reported`，失败轮带 `ok:false`；父进程旧的 `classify_event` 不读它们，
加法兼容。`agent_runner/tools.TOOL_NAMES` 是内置执行器工具名的闭集（`tool` 词表只从它注册，AST 用例核对每个 `Tool("…")`）。

**跨进程**：子进程只报时长，父进程在收到那一刻打时间；agent_runner / CLI / MCP 子进程都不 import 本模块。SSE 不承担完整性（满了会丢），
PR3 的读取走 REST 权威快照。

### PR2 反证（`tests/test_exectrace_pipelines.py`；脚本 scratchpad `xt_mut2.py`，8/8 红）

| 变异 | 红在 |
| --- | --- |
| 提交前检查没过也记 ok | `test_a_correction_is_a_second_agent_attempt_and_the_failed_check_stays_the_first_failure` |
| 取消记成 ok | `test_cancel_freezes_the_trace_and_late_writes_do_not_pollute_it` |
| 第三方 CLI 不标 opaque | `test_a_normal_edit_records_accept_agent_phases_and_keeps_four_facts_apart` |
| 估算用量也算费用 | `test_an_estimated_usage_or_an_unknown_endpoint_never_gets_a_cost` |
| 准备失败记 ok（真 worker） | `test_a_real_preparation_leaves_a_trace_on_disk_with_the_stage_facts_mapped[script-fails]` |
| 卡片超时记成 ok | `test_an_unanswered_card_is_a_timed_out_wait_not_a_slow_model` |
| 工具被第二处重复计 | `test_an_approved_fix_has_one_tool_span_per_call_and_the_wait_is_its_own_span` |
| 拒绝记成批准 | `test_a_refused_install_is_recorded_as_the_users_decision` |

### 没做 / 边界

* 第三方 CLI（Codex / Claude Code）内部的模型请求、工具调用、Token 看不见：只有外层 `agent_run` 区间（`opaque`）。经 MCP 修复时工具调用仍如实（走 `run_tool`）。
* 内置执行器在改图链路里的工具调用只有结束时刻（point），没有区间；模型请求在**失败且无用量**时（网络错误）没有单独的 Span，结局在 `agent_run` 与根上。
* `ready_to_commit` 不成区间；`run_command` / `install_packages` 等工具的子进程内部不再细分。
* 端点、SSE 提示与 Waterfall 是 PR3；诊断包与 Guardian 是 PR4。

## 修订：PR3——把已复刻的 Waterfall 接到真实数据（2026-10-27）

* **读取端点** `GET /api/exec-trace?kind=&ref=`（或 `?id=`）：只读零执行，按**当前项目**认领（AI 会话按项目根、其余按项目 id 记 scope，两把键都属本项目）；
  找不到 / 过期 / 别的项目 / 追踪关闭一律 404 + `available:false`（原因只分 `not_found` / `disabled`），`Cache-Control: no-store`，会话认证照常。
  `Recorder.find` 先看内存（运行中的最新），再扫盘（总量有上限，扫一遍有界）。
* **实时**：评估过新增 SSE 事件——现有 `/api/events` 是满了就丢、无序号、无回放的进程级队列，承担不了完整性；所以不为 Waterfall 另建实时通道：
  运行中且展开时每秒取一次权威快照，`ai.done` 已有的终局通知触发最后一次。
* **前端**：原型搬进 `web/src/components/ai/traceWaterfall/`（视觉门禁、i18n、图标集按仓库规则改写，交互与版式保留）；`lib/execTrace.ts` 是唯一转换层；
  转录的「执行记录」与任务历史的「技术详情」共用 `TraceDetails`。规则全文 `docs/rules/frontend/execution-trace-ui.md`。
* **性能**（`e2e/exec-trace-perf.spec.ts`，489 Span + 200 观察点、运行中、Chromium，三次）：展开到首行 524–741 ms；含首渲在内的 5 s 窗口长任务 1 个、140–156 ms（那一个就是首渲）。
  改之前（时间轴随此刻连续伸缩、行不 memo）同口径是 24 个 / 累计 1.56 s。用例后来改成首渲之后静置 1 s 再开 5 s 窗口（只量实时刷新本身）：本机 0 个长任务。
  这个用例不进黄金路径，经 `playwright.perf.config.ts` 在 performance-agent workflow 里跑：运行中时间轴改取阶梯值、行只在含未结束 Span 时随 `now` 重画。没有改用 Canvas、没有虚拟化。

## 修订：PR4——诊断、归因、Guardian 对比与回归体系（2026-10-27）

* **出门投影与归因**（`engine/exectrace_diag.py`）：离开本机前再走一遍**独立**的白名单投影（逐字段按形状 / 闭集 / 词表重校验，不认识的丢，
  不导出 `task_ref` / `session_ref` / scope，冗余事件不出门）。归因分开给「最早失败的阶段」（`origin`，Trace 记下的第一次失败；任务失败却没记过 =
  `unknown`）、「连带失败」（`cascade`，不含根）、「最长耗时」（`slowest.is_cause = false`）、「最终结局」；另有修复尝试、重试、重复工具调用、预算、
  用户拒绝、超时、是否经真实证据验证（AI 任务只认 Task Core 结局 `completed`、修复会话只认结局 `fixed`、准备只认真实执行区间 `script_run`；阶段 Span 的 ok 不算，说不出就 `unknown`）、完整 / 截断 / 中断。Task Diagnostic 仍是任务诊断的权威。
* **诊断包 schema 9**：新增 `exec-traces.json`（本项目最近 8 条，失败在前，整节 ≤192 KiB，超了从尾部丢并记 `truncated`）。同源对：
  `diagnostics.BUNDLE_SCHEMA_VERSION` ↔ `web/src/diagnostics/types.ts` ↔ 壳 `shell_report.rs` 的 `BUNDLE_SCHEMA` ↔ `tests/golden/diag_shell_report.json`。
  追踪关闭 / 没有记录时不放这个文件（不放空壳）。上传仍只有用户逐次确认的「发送问题反馈」一条路（ADR 0118），遥测同意语义不变。
* **用户导出单份诊断**：`GET /api/exec-trace/diagnostic`（与 `/api/exec-trace` 同一套项目认领），文档 `tavotto.exec-trace-diagnostic` v1 = 投影 + 归因 +
  不完整说明；界面在「执行记录」里，任务结束后才给（运行中的半份不交出去），只下载到本机。
* **Guardian / FigureBench 只读分析**（`scripts/ci/exectrace_analysis.py`，`tavotto.exectrace-analysis/1`）：Trace 不给兼容结论——`verdict` /
  `verification` 只来自 Guardian / bench 的结果文件，否则 `null`；用例与 Trace 的关联只认结果文件里的 `trace_ids` / `task_refs`。对比统计每个均值 /
  分位数带 `n`，样本 < 5 记 `null` + `insufficient_samples`，不声称显著性；同一错误类只按证据分组，并写明「同码 ≠ 同根因」。`taf_bench` 每行记 `task_refs`（AI 会话 id），
  `--exec-trace-out FILE` 跑完后对自己数据目录的 Trace 出这份分析（holdout 拒绝：分析里有逐用例 id）；Guardian 的关联见下文「Guardian 关联」。
* **OpenTelemetry**（`engine/exectrace_otel.py`）：只做转换函数，不导出到网络、不加依赖。OTLP/JSON `ExportTraceServiceRequest`（opentelemetry-proto
  v1.x，枚举写整数、int64 写字符串）；只有 `ok` → OK，`failed` / `timeout` → ERROR，`cancelled` / `unknown` / `interrupted` / 运行中 → UNSET +
  `tavotto.status`（不为外部标准改 Tavotto 的状态语义）；**只有服务端报的用量**进 `gen_ai.usage.{input,output}_tokens`（GenAI semconv，Development
  稳定度），估算的放 `tavotto.usage.estimated_*`；其余一律 `tavotto.*`。有损 / 不支持的字段列在模块文档里（`timing`、Trace 级计数、截断丢掉的中段、
  links / traceState、跨进程单调钟不对齐）。
* **性能预算**（`scripts/dev/exectrace_bench.py`，`tavotto.exectrace-bench/1`，临时数据目录，数字随环境变）：本机（Python 3.13 / Darwin arm64）一次：
  开 / 关 每对 start+end 8.3 µs / 0.06 µs（含后台写线程的 GIL 竞争）、每个 event 0.75 µs / 0.03 µs；200 Span 的合成任务（每 Span 100 µs 自旋）
  23.6 ms / 20.9 ms（+13%，真实工具调用是毫秒到秒级，占比远低于此）；上限处内存峰值 ≈927 KiB；典型一条 ≈18 KB、上限处 ≈252 KB 且 `truncated`；
  10 万事件洪水 0.15 s、保留 1024 条。关掉时零文件。这是单机测量，不是跨平台结论。

### 故障注入矩阵（§4.6）与看护用例

| Case | 情景 | 证据（用例） | 真实链路 |
| --- | --- | --- | --- |
| T01 | 正常 Matplotlib 脚本 | `test_exectrace_pipelines::test_a_real_preparation…[ready]`、`…normal_edit…` | 真 worker / 真桥 |
| T02 | 缺少 Python 依赖 | `test_exectrace_matrix::test_t02_…`（失败落在 `script_run`、稳定码、包名不进 Trace） | 真 worker |
| T03 | 依赖修复成功 | `test_exectrace_matrix::test_t03_…`（装包工具 ok；没执行证据就 `unknown`） | 真 `run_tool`，装包器替身 |
| T04 | 安装失败 | `test_exectrace_matrix::test_t04_…`（`pip_failed` 是根因，不冒充成功） | 真 `run_tool`，装包器替身 |
| T05 | Pillow 像素图 | `test_exectrace_t05::test_t05_a_pillow_only_script…`（产品报 `no_figures_captured`，Trace 同码失败于 `script_run`，不升格；无文件名 / 路径）、`…::test_t05_the_convert_handoff…`（`convert` → ok + outcome，`convert_script` 工具 Span） | 真 worker；假模型修复会话 |
| T06 | 用户拒绝修改环境 | `…pipelines::test_a_refused_install…` | 假模型修复会话 |
| T07 | 模型 API 超时 | `test_exectrace::test_a_retry_is_a_separate_attempt…`、`…pipelines::…estimated…`（runner `ok:false`） | 构造事件 |
| T08 | 工具连续失败 | `test_exectrace_diag::test_repair_refusal_budget…`（重复调用、预算、超时） | 真 ExecTrace |
| T09 | Safe worker 失败 | `…pipelines::test_a_real_preparation…[script-fails]` | 真 worker |
| T10 | 取消正在执行的任务 | `…pipelines::test_cancel_freezes…`、`…cancelling_a_fix…` | 真桥 / 修复会话 |
| T11 | 同请求重复提交 | `…pipelines::test_an_idempotent_resend…` | 真桥 + 账本 |
| T12 | 后端异常终止并恢复 | `test_exectrace::test_a_running_record_whose_owner_died…`、`…diag::test_interrupted…` | 死进程 pid |
| T13 | 跨项目并发 | `test_exectrace_api::test_another_projects…`、`…diag::test_the_bundle…`、`…fix_sessions_in_two_projects…` | 真端点 |
| T14 | 部分成功 / 验证未知 | `…normal_edit…`（outcome partial）、`…diag::test_origin_cascade…`（verified unknown） | 真桥 |
| T15 | 采集器故障 | `…pipelines::test_a_broken_collector…`、`test_exectrace::test_internal_faults…` | 真桥 |
| T16 | 敏感信息注入 | `test_exectrace::test_canary…`、`…diag::test_the_export_projection…`、`…otel` canary | 真存储 / 出门投影 |

需要联网或真实付费模型的实验：本 PR 不跑；`scripts/dev/taf_bench.py` 的真模型模式仍需单独授权，CI 不消耗模型费用。

### PR4 反证（6/6 红）

| 变异 | 红在 |
| --- | --- |
| Guardian 分析拿 Trace 状态当结论 | `tests/test_exectrace_analysis.py` |
| 「最慢」标成根因 | `test_exectrace_diag::test_origin_cascade_and_slowest…` |
| 任务失败却没记失败时不给 `unknown` | `test_exectrace_diag::test_a_failed_task_without…` |
| 出门投影放过 Span 名 | `test_exectrace_diag::test_the_export_projection…` |
| 所有事件都出门 | 同上 |
| 诊断包不按项目取 | `test_exectrace_diag::test_the_bundle_carries_this_projects…` |

### 真实模型验证（2026-10-27，维护者授权付费模型）

用真实 Codex CLI（ChatGPT 订阅登录；本机没有 API Key，所以内置执行器的 Token / 费用路径这次没有真模型数据）跑了 3 个场景、共 4 次 Codex 运行（没有为好看的结果重试），
应用在隔离的配置 / 数据目录里（用户自己的配置与历史一字未碰）：① AI 改图成功（`agent_run` 32.9 s、opaque、`changed=true`、`refresh=ok`）；② 脚本缺依赖
→ 智能修复经 MCP 修好（18 个工具调用、3 次批准、`change_python` 在隔离数据目录的受管环境里、第二次 `try_run` 通过，结局 `fixed`）；③ 依赖在 PyPI 上不存在
→ `dependency_not_found` + 回滚 + 用户拒绝发送诊断，结局 `not_fixed`。所有 Trace / 诊断文档对项目路径、脚本名、包名、提示词、报错文字的检索**零命中**。

验证发现并已修（`tests/test_exectrace_realfindings.py`，反证 4/4 红）：

| 发现 | 修法 |
| --- | --- |
| 被策略挡下的越界读取（Agent 随后改正）被记成「最早失败」，修好的会话也被归因到它 | 挡下的调用没执行：`run_tool` 记 `cancelled`（码照留），不进 `first_failure`；归因只在任务真的失败时给 `origin` / `cascade`，任务成功时途中失败列 `recovered`，另计 `blocked_calls` |
| 修复会话 `repair_attempts` 恒 0、`verified_by_execution` 恒 unknown | 修复次数 = 动了环境的工具调用（tavotto / listed / user_env 档）；结局 `fixed` = 产品「交 fixed 必须有最后一次改动之后的应用执行证据」那道门过了 → verified |
| 准备的阶段观察点全落在收尾那一毫秒 | `observe_stage_trace(…, legacy_t0=)`：旧轨迹的 `perf_counter` 时刻按同进程单调钟换算到 Trace 偏移，点落在真实发生的时刻 |
| 等用户点卡片的时间算进工具耗时，「最慢」可能怪到用户头上；工具与它的 user_wait 的 `approved_by` 说法不一 | 「最慢」按自身耗时（扣掉子 `user_wait`）；有卡片决定时工具的 `approved_by` 与 user_wait 一致（审计的 `approved_by` 不动） |

遗留：修复会话里 `try_run` 发起的准备运行是**另一条** Trace（按计划 id 找得到），与修复会话的 Trace 之间还没有父子关联；Codex / Claude Code 的 Token 与费用看不见（opaque）。
