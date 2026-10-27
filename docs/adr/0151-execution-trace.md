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
| 运行中写盘 | 开始 / 冻结各一次 + 结束 Span 时节流 `CHECKPOINT_S = 2 s`（热路径不逐条同步写） |
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
