# ADR 0071：Trace 与旧计划失效政策——坏在哪一步、什么时候旧计划不算数

日期：2026-09-21 · 状态：**Accepted（U09；两条主线第一次联调）**
相关：[0070 ExecutionReceipt 完整化与 Manifest 关联](0070-execution-receipt-and-manifest-identities.md)、
[0053 合同与准备](0053-foundation-contracts-and-preparation.md)（§四 准备接口、FO-007 过期授权）、
[0057 首开环境与工作目录](0057-first-open-environment-and-workdir.md)、[0031 导出](0031-unified-export-pipeline.md)、
[0021 native 进程归属](0021-tavotto-run-product-contract.md)、[0016 诊断 V2](0016-diagnostics-v2-frontend-state-tracing.md)；
实施包 `00_MASTER_PROMPT.md` §2（Trace 是八项基础设施之一）、`04_ARCHITECTURE.md` §3（旧热 Figure 代表当时的数据）、
`phases/U09_join.md`；registry RC-080、FO-060、FO-066、FO30。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| Trace 是什么 | **有界的阶段轨迹**，只答「现在卡在哪一步、哪一步坏的」——不是全系统追踪平台。`engine/trace.py`：阶段名闭集（准备接口 plan → check → spawn → execute → receipt；导出作业 prepare → source → compile → compose → raster → inspect → publish → report）、每步一条（阶段 / 毫秒 / 结果 / 错误码 / 标量事实）、上限 64 条，超过丢**中间**的（头尾留着）记 `truncated`；**第一次失败是根因**，后面的连带失败不盖它 | `tests/test_trace.py`（闭集 / 有界 / 根因 / 标量） |
| Trace 记什么、不记什么 | `facts` 只收标量与短串（`_scalar()` 把对象换成类型名、长串截断）——没有路径、脚本正文、图内文字、argv，公开投影可以原样带它（RC-081）。节点级的来源关系不在 Trace 里，在 manifest 的 `provenance.nodes`（ADR 0070）：只记真实知道的 id 与来源，外来页 `internal=unknown` 不编造 | `test_facts_only_carry_scalars_never_content`；`test_a_real_export_carries_four_identities_and_a_node_table` |
| 故障阶段准确（FO-066） | 生产者在哪一步炸就记哪一步：RenderCore 的 `compile` / `compose`（写入器 `WriterError`）/ `raster`（child `RenderChildError`）各自 `fail()`；`exportjob.run` 只在生产者没记过时补一条。**PDF 错误不触发装包**：依赖那道门只在准备接口的 `spawn` / `execute` 之前问（`deprepair.gate`），导出作业从不碰它 | `test_a_writer_failure_is_pinned_to_compose_and_never_reaches_the_dependency_door`、`test_an_export_that_fails_while_producing_names_the_phase` |
| 旧计划什么时候作废（FO30 拆开的合同） | 执行线程**起会话之前**把计划记下的三样与此刻各比一次，第一条不一致的就是理由：授权（`workdir.grant_for`，FO-007 原有）→ `grant_changed`；项目的解释器决策（`pool.resolve_worker_python`）→ `environment_changed`；数据绑定（`databinding.binding_for` 的修订，判**内容** sha256——同大小同 mtime 也算）→ `data_binding_changed` 并点名变了的文件。三种都是 `preparation_plan_stale` + `reason` + `executed=False`，一行脚本不跑；调用方重新准备。build 之后按**观察到的输入**再核一次（spawn 与读取之间被改）→ 同一个 code、`executed=True`（脚本跑过一次），重新准备会复用这条会话（新计划的绑定就是它读到的那份） | `preparation._stale_reason / _binding_mismatch`；`tests/test_preparation_api.py`（三条 + 复用） |
| 什么时候旧计划**不**作废 | 复用热态会话时数据绑定对不上**不是错误**（04 §3：旧热 Figure 代表当时的数据结果）：`ready` + 回执 `binding_check.matched=False` + note 点明「旧快照」；render / export 仍是明示的旧快照，不自动清空编辑重新计算；用户明确「重新构建」（`/api/engine/invalidate`，既有端点）后新一代读到新数据、回执 `matched=True`、公开身份变；改了脚本再重建则回执的 `source_revision` 随文件 | `test_reusing_a_hot_session_after_the_data_changed_is_ready_but_says_it_is_a_snapshot`、`tests/test_foundation_join.py::test_fo30_*`（真实入口） |
| native | native 会话的重放不靠自动杀进程模拟（ADR 0021）：`/api/engine/invalidate` 对 native 回 `invalidated=false`；旧计划失效政策只作用于准备接口起的会话，native 的回执按握手帧的 pid 核 | 既有 `api_engine_invalidate`；`receipt.from_native_session` |
| 用户的 live 编辑 | 计划作废 / 会话作废都不碰前端文档里的 override（它们随下一次 render 原样重放），`preparation_plan_stale` 的载荷里没有「清编辑」这种东西 | FO30 用例：patch 之后重建，patch 仍在文档里由调用方重放 |

## 1. 为什么 Trace 是「阶段序列」而不是「节点树」

00 §2 列的 Trace 与 `04 §1` 的数据方向一样是一条**流水线**：准备 → 执行 → 捕获 → 编译 → 合成 → 检查 → 发布。用户 / 评审要的问题是「卡在哪一步、哪一步坏的」，这一条序列就答得出；把每个 IR 节点的处理都记进去（RC-080 must_fail：无限记录全文文字 / 路径）既无界又把科研正文带进公开投影。节点级的来源关系有 manifest 的 `provenance.nodes`，它只记 id 与来源（谁来自哪份源、哪份回执），不记内容。

## 2. 为什么「数据变了」要分三个时刻

同一个事实（脚本要读的数据在预检之后变了）在三个时刻的正确处置不同：

* **起会话之前**发现——按旧计划跑等于拿旧数据的判断执行（预检说「两处同名」是按旧内容判的），所以作废、一行不跑；
* **spawn 与读取之间**发现（观察到的 sha 与计划不符）——脚本已经跑过一次，结果如实作废（`executed=True`），但那条会话读到的正是新数据，重新准备就复用它；
* **execute 之后**发现（复用热态会话）——热 Figure 代表它跑那一刻的数据，这是**明示的旧快照**：普通导出照常、回执如实说 `matched=False`，要不要重算是用户的事（`invalidate`），不自动清空编辑重新计算。

把三个时刻压成一个「作废」会让用户每改一次数据就丢掉编辑；压成一个「照常」会把旧计划的回执冒充实际执行。

## 3. 反证（scratchpad `u09/mut_a.json` / `mut_b.json` / `mut_c.json`）

| 变异 | 红在 |
|---|---|
| 起会话前不比数据绑定 / 不比解释器 / build 后不核绑定 | `tests/test_preparation_api.py` 三条 |
| 轨迹无界 / facts 原样带对象 / 后来的失败盖掉根因 | `tests/test_trace.py` 三条 |
| PDF 元数据里的脚本名被登记成脚本（执行来源不止注册表） | `test_pdf_metadata_naming_a_script_never_triggers_execution` |

## 4. 没做 / 边界

* 不建全系统追踪平台、不记进程级 I/O；Trace 不进遥测（遥测白名单不动）。
* 脚本文件变了仍由项目 watcher 作废会话（U03 之前就有），本 ADR 不改它的轮询语义；`source_revision` 随重建更新。
* `environment_changed` 只报「变了」，不报路径（ADR 0053 §二：公开投影不带机器路径）。
