# U09 · 从正确执行到正确产物的一条证据链 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U09.join，两个独立 milestone：**`U09.existing_env_join`**（只要 U03 + U08：用户已有的项目 venv +
新 RenderCore 终点）与 **`U09.managed_env_join`**（另要 U04 + U05：产品自己准备私有 Python + 联合安装 + 同一终点）。
架构决策：[ADR 0070](../../../adr/0070-execution-receipt-and-manifest-identities.md)（回执完整化 / 四身份 / 公开投影）与
[ADR 0071](../../../adr/0071-trace-and-stale-plan-policy.md)（Trace / 旧计划失效政策）。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 = 两条链的汇合点 `bf87e511`（`git merge 4973a28f`：链 2 U03 → U04 → U05 的
本地 tip 并进链 1 U02 → U06 → U07 → U08 的本地 tip `464f7872`；这个 merge 提交本身是**两主线第一次联调**的第一份证据，见下）；
结束 = 两条链全部合入 main（`3147bd76`，2026-09-22 06:18）之后，把本分支的 A / B / C / D 与后续修正以 main 为底线性重放成
**一笔提交**（期间对上游各 tip 的六次 `git merge` 只是同步，内容全部已在 main，重放时丢弃；重放用逐文件三方：base 取本分支并入过的
那个上游 tip，我的提交没碰过的文件一律取 main），PR 就开在它上面（合入后以 `git log origin/main` 里的 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u09` 里做，用户主工作区一个字节没碰；主仓库 `.venv` 零改动；候选包只在 session
scratchpad 的 `u09/rc-venv`（`pip install -e '<worktree>[rendercore,dev,worker]' pdfminer.six pypdf pymupdf h5py`）；
「用户本来就有的项目环境」是 scratchpad 里的 `host311`（Homebrew 3.11.14 + matplotlib 3.11.2 / numpy 2.4.6 / h5py 3.16.0，
经 `TAVOTTO_FOUNDATION_PROJECT_PYTHON` 点名）；真 pbs 归档复用 U05 的 scratchpad 缓存、wheelhouse 复用 U05 的 + `pip download h5py`。

**两主线第一次联调（merge）**：12 处冲突全是「两边各加一段」——`enrollment.json`（链 2 为底并入 U07-R1 / U08-R1）、
`U00_FACADE_LEDGER.json`（U03 的 `symbol` 格式为底并入 U08 的 `migration_evidence` / `candidate_parity` / 备注 / bridge 调用点，
合并后 app.py 行号漂出符号范围的 23 处按 AST 填回）、`PACKAGE_CONTENTS.json`（并集重算）、README（七段按阶段排序）、
`make_plugin_manifest.py`（三段注释并列；`BRIDGE_IMPORTS_AT_MIN` 自动并集）、两份 AGENTS 速查行（各取超集那一行）、import baseline
（`extra_edges` 并集 34 条）、`test_foundation_facade_ledger.py`（U08 的 migration_evidence 用例 + U03 的 symbol 判据）、
`test_foundation_harness.py`（计数 35 条）。merge 后：ruff 两条 0 / 0；链 2 关键套件 512 通过 / 7 skip（联网 / 真 pbs /
ALT_PYTHON，预期）；链 1 关键套件主 `.venv` 401 通过 / 178 skip（候选包未装，预期），rc-venv 里 243 通过 / 0 skip；
ledger / harness / importgraph / plan integrity / 文档索引 / 插件清单用例全过——**零回归**。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径（PyMuPDF 终点、解释器链、写回、会话认证）不变；本阶段改的是**两条路
都走的账**：回执（自报只收这一条会话的、输入观察、数据绑定）、准备接口的过期判定（多两个理由）、manifest 的 `identity` /
`provenance`、作业 / 准备结果的 `trace`——都是加字段 / 加判据，旧键一个不动；MCP 导出的 manifest 多来源段。拟启用能力**无**
（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、`codex-plugin/AGENTS.md`；`docs/rules/backend/`
的 preparation-and-receipts / execution-entries / figure-capture-and-execution / worker-protocol-and-lifecycle / process-boundaries /
override-application-and-replay / export-pipeline / writeback-transaction / dependency-repair-and-packages / private-python / rendercore /
telemetry / diagnostics / session-auth / ai-agent-bridge；`docs/rules/repo/same-origin-pairs.md`、`predicate-subject.md`；ADR 0008 /
0016 / 0021 / 0044 / 0047 / 0053 / 0057 / 0059 / 0060 / 0061 / 0063 / 0064 / 0065 / 0066 / 0067 / 0068；实施包 00 §3 / §6、01（D06 /
D09 / D13）、03 §8、04 §1–§3、05 §3 / §7、07、`phases/U09_join.md`、`generated/FIRST_OPEN_SCHEDULE.md`（FO30 / FO32）、registry
（R03 / R11 / R12 / R13 / CP06 / CP07 / CP08 / CP08-D → U09 的 19 条）、`archive/firstopen_cases.json` 的 FO30 / FO32 原文；U01–U08
全部交接。复用（权威不动）：`pool.build_owned / peek / invalidate`、`workdir.grant_for / decision_for`、`databinding.evidence`、
`exportjob.run`（生命周期一字不改）、`exportreq.render_plan_ref`（语义身份算法）、`inspector.inspect`（观测 / 政策不动）、
`artifactinspect.inspect_produced`（HTTP 与 MCP 同一份接线）、`figcapture.source_artifact_from_file`、`/api/engine/invalidate`
（用户明确重建的既有端点）、U03 的 `foundation_app.running_app` / U05 的进程内形态 / U04 的 `/api/engine/dependencies/*`。

**实际代码与 API / 数据结构变更**：

| 层 | 变更 |
|---|---|
| `engine/figsession.py` | `runtime_report(origin=, inputs=)`：加 `report_origin=build` / `pid` / `inputs`（加字段，`RUNTIME_REPORT_VERSION` 不升） |
| `engine/figcapture.py` | `InputObserver`（三处 `open` 的只读观察包装：记实际打开的项目内文件，去重、有界 256、源码后缀剔掉、`observation=partial` + `unobserved`）、`observed_local_modules()`；常量 `INPUT_OBSERVER_*` / `CODE_SUFFIXES` / `OBSERVATION_PARTIAL` |
| `engine/worker.py` | build 时**先**装观察器再装只读回退（回退换出来的路径经观察器记下），脚本跑完那一刻定格 `_inputs_report`，build 响应的 `runtime` 带它 |
| `engine/pool.py` / `nativesession.py` | `EngineWorker.child_pid`（= `proc.pid`）、`WorkerdWorker.child_pid`（`open_session` 响应的 `pid`）、`NativeSession.child_pid`（握手帧 `process_pid`） |
| `engine/receipt.py` | `accept_runtime()`（origin + pid 核对 → `runtime_rejected ∈ {not_a_build_report, pid_mismatch}` / `pid_check ∈ {ok, unavailable}`）；字段 `runtime_rejected` / `pid_check` / `binding`；`inputs` / `observed_files()` / `binding_check()` / `public_facts()`；公开语义身份含观察到的数据身份；`from_worker(..., binding=)`；`worker_pid()`；native 同样核 |
| `engine/databinding.py` | `binding_for(script, root, mode)`：按 cwd 档记「会读哪些文件、内容 sha256、修订摘要」（`BINDING_VERSION`） |
| `engine/preparation.py` | `PreparationPlan.binding`；`PreparationResult.trace`；`_stale_reason()`（授权 / 解释器决策 / 数据绑定三比 → `preparation_plan_stale` + `reason ∈ STALE_REASONS` + `executed`）、`_binding_mismatch()`（build 后按观察到的输入核；复用热态会话不算错，note 点明旧快照）；轨迹 plan → check → spawn → execute → receipt |
| `engine/trace.py`（新，纯标准库） | `Trace`：阶段名闭集 `PHASES`、有界 64 条（丢中间留头尾）、`facts` 只收标量、第一次失败是根因、`to_payload()` |
| `engine/exportjob.py` | `ExportJob.trace`（prepare → source → … → publish → report，随 `to_payload()` 走）；失败落到当前阶段（生产者记过就不补） |
| `rendercore/identity.py`（新） | `render_identity()` / `identities()` / `fonts_policy_version()`（与预览缓存键同一份）/ `REPRODUCIBILITY` |
| `rendercore/job.py` | `plan_facts()` 多 `receipts`（`FrozenSource.receipt`）与 `nodes`（对象 id + 实例序号，`NODES_LIMIT`，外来页 `internal=unknown`）；每个格式的 `plan.render_identity`（矢量 / 栅格各自）；轨迹 compile / compose / raster |
| `rendercore/sources.py` | `FrozenSource.receipt`（执行侧源随附的回执公开事实） |
| `rendercore/inspector.py` | manifest 多 `identity`（四身份）/ `provenance`；`inspect(..., run_id=)` / `uninspected(..., run_id=)`；`summary()` 带两段；**`public_projection(manifest, trace=)`**（唯一可以离开本机的那份） |
| `rendercore/preview.py` | `fonts_policy_version` 改为引用 `identity` 的那一份 |
| `engine/artifactinspect.py` | `inspect_produced` 传 `run_id=job.id`；`plan_half` 接受生产者给的 `manifest["provenance"]` 补丁；**`execution_provenance()`**（worker 直出路：回执 + `kind=figure` 的源产物 + `render_plan_ref` 语义身份 + render 身份） |
| `app.py` | `_execution_receipt` 带 `grant` 与此刻的 `binding`（与准备接口同一份账）；`_execution_source` 把 `rcpt.public_facts()` 挂到 `FrozenSource.receipt` |
| `codex-plugin/mcp/tavotto_mcp/bridge.py` | `export._produce` 每个格式经 `execution_provenance()` 补来源段（`Produced.manifest={"provenance": …}`），装配失败进 `warnings`；桥不新增 import |
| `web/src/lib/api.ts` | `ArtifactIdentity` / `ArtifactProvenance` / `ArtifactReceiptFacts` / `ExportTrace` 类型；`ArtifactManifestSummary.identity / provenance`、`ExportJob.trace` 可选键（界面不画） |
| `tests/fixtures/foundation/join_h5/`（新，夹具 ⑩） | `scripts/figure_h5.py`（h5py 读相对路径 `data/measure.h5`）、`data/measure.h5`（[2,4,8]）、`scripts/data/measure.h5`（干扰 [200,400,800]）、`make_h5.py`（字节确定）、`truth.json` |
| `tests/test_foundation_join.py`（新） | FO32 `existing_env_join`（+ 错误同名数据负例）、`TestManagedEnvJoin`（FO32 `managed_env_join`）、FO30（真实入口） |
| `tests/test_export_identity.py`（新） | 四身份（RC-075 ~ RC-078）、公开投影九根针（RC-081 / FO-062）、PDF 元数据不触发执行（RC-082）、真导出四身份 + 节点表（RC-079）、节点表有界（RC-080）、写入器炸 → compose 且不问依赖门（FO-066） |
| `tests/test_trace.py`（新） | 闭集 / 有界 / 标量 / 根因 / 两条链同形 / 导出失败落阶段 |
| `tests/test_execution_receipt.py` / `test_preparation_api.py` / `test_worker_runtime_report.py` / `test_mcp_export_inspection.py` / `test_rendercore_sources.py` / `test_foundation_fixtures.py` | 自报核对 / 输入观察 / 绑定核对 / 三条停止合同 + 复用旧快照 / 真 worker 的 pid 与观察 / 真 worker 的 MCP 四身份 / 夹具真值（独立读 HDF5 字节） |
| `codex-plugin/mcp/server.py` | 合并态发现：链 1（U08 C）给 bridge 加了 `artifactinspect` import 却没同步 launcher 的 `_BRIDGE_IMPORT` 探测串（`tests/test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge` 在链 1 tip 上就红；resolver 交棒后老引擎上 bridge 会 ImportError）——补上一个词 |
| `tests/test_dependency_transaction.py` | `TestGate` 手拼计划的解释器钉成产品此刻的决策（U09 起会话前比解释器决策，这条用例测的是门的投影，不是环境变了） |
| `engine/preparation.py` `reset_for_tests()` | 清登记表之前先置取消位并 join 还在跑的执行线程（有界）：用例结束 monkeypatch 已还原，没跑完的线程会在还原后的世界里拿真 pool 起一条真 worker 泄到下一个用例——U09 的起会话前三比拉长了那个窗口，全量里 `test_workerd_pool::test_control_plane_reports…` 被这样诬告（LEAKWATCH 插件定位到 `test_preparation_api::test_the_plan_reads_the_grant…` 的线程） |
| `.github/workflows/ci.yml` | harness 步加 `tests/test_foundation_join.py`（FO30 enforced，pr lane） |
| `.github/workflows/foundation-u06-rendercore.yml` | 第二个 setup-python 3.12 + matplotlib / numpy / h5py（用户环境）→ `TAVOTTO_FOUNDATION_PROJECT_PYTHON`；「U09 联调」步（FO32 existing_env_join + identity + trace；FO32 两条 skip 即红）；paths / 工件 |
| `.github/workflows/private-python-targets.yml` | wheelhouse 加 h5py；应用侧装 `.[rendercore]` + 批准字体；「U09 联调：managed_env_join」步（TestRealChain 供应好的 runtime 零下载复用；skip 即红）；paths |
| 台账 / 文档 | `enrollment.json`：FO30 → enforced（pr），FO32 → observing（release，两条具名任务）；registry：FO30 / FO32 enrollment 同步 + FO30 promotion 合同；`plan.json` U09 done（两个 milestone 各 done、产品资格仍 not_run）；ADR 0070 / 0071；`preparation-and-receipts.md` / `rendercore.md` / 前端 `export-pipeline.md` 细则；三份 AGENTS 速查行；夹具 README；`evidence/u09/`；本文件；README U09 段；PACKAGE_CONTENTS 重算；派生视图重生成 |

**关联旧要求 ID / 场景 ID**（registry 映射到 U09 的 19 条，逐条处置）：

| ID | 处置 | 证据 |
|---|---|---|
| RC-075 四身份分离 | 做了（run 不进 semantic / render；`identities()` 四格并列） | `test_the_run_identity_never_enters_semantic_or_render`；变异 M13 红 |
| RC-076 render fingerprint 含全部已知影响 | 做了（后端 build / 栅格器版本 / 字体政策版本 / ppi / px / 透明；字体政策版本与预览缓存键同一份） | `test_the_render_fingerprint_changes_with_every_known_influence`；M12 红 |
| RC-077 artifact hash == 发布字节 | 做了（发布只是 `os.replace`；hash 不进文件——自引用互斥） | `test_the_artifact_hash_is_not_written_into_the_artifact`、FO32 用例对每个格式核；M17 红 |
| RC-078 可复现性分 semantic / visual / byte | 做了口径（`REPRODUCIBILITY` 进公开投影；byte 不承诺）；实际重放 = FO32 同机同栈（semantic 相同、render 按格式分）；跨 OS byte 模式 → later（registry follow_up X01） | 公开投影的 `reproducibility` |
| RC-079 节点 id 区分同资产实例、稳定关系 | 做了（对象 id + 实例序号；插入别的对象不影响；不用 PDF object number） | `test_a_real_export_carries_four_identities_and_a_node_table`；M25 红 |
| RC-080 Trace planned / observed 分开且有上限 | 做了（Trace 有界 64、节点表 `NODES_LIMIT`；plan / observed 仍在 manifest 两半张） | `test_the_trace_is_bounded_and_keeps_head_and_tail`、`test_the_node_table_is_bounded`；M22 / M28 红 |
| RC-081 公开 Manifest / Trace / 诊断不泄露路径或科研内容 | 做了（`public_projection` 唯一出口；九根针含中文用户名 / 空格路径 / 图内文字 / 脚本正文 / argv） | `test_the_public_projection_carries_identities_and_verdicts_but_no_needles`；M14 / M15 / M16 / M21 红 |
| RC-082 PDF metadata 不成为执行授权 | 做了（执行来源只有注册表；/Info 里写脚本名的 PDF 一个 worker 不起） | `test_pdf_metadata_naming_a_script_never_triggers_execution`；M20 红 |
| RC-097 关键故障注入 / 变异确实使资格失败 | 做了本阶段的定点反证：30 条变异全红（`evidence/u09/mutations.json`），含「报告假绿」（M30：unknown 报 verified）、「删观察」、「不核 pid」、「不比绑定」 | `evidence/u09/mutations.json` |
| FO-059 回执来自实际 worker 而非预检猜测 | 做了（`report_origin` + pid 核对；体检形状 / 别的进程的回执一律拒收） | `TestReceiptOnlyAcceptsThisSessionsReport`；M01 / M02 / M10 红 |
| FO-060 热态 / 重放 / 写回 / 导出上下文与 generation 一致 | 做了（导出路的回执与准备接口同一份账：grant + binding；manifest 的 `receipts` == 准备回执的公开事实；FO30 里 generation 随重建递增、复用时不变） | FO32 用例（`facts == rt`）、FO30 用例 |
| FO-061 有限数据观察诚实标 partial / unknown | 做了（`observation` 永远 partial；h5py 的读在回执上是未观察、`binding_check.matched=None`，不冒充） | FO32 用例、`test_the_report_carries_origin_pid_and_observed_inputs…`；M23 红 |
| FO-062 公共 manifest 不含敏感路径 / argv / env / 凭据 | 做了（同 RC-081；回执默认投影本来就不带路径，`public_facts()` 再收窄） | 同上 |
| FO-063 科学环境不需要新 PDF 依赖 | 做了（FO32 两个出口都核项目 venv / 受管环境里没有 pikepdf / pypdfium2 / uharfbuzz，渲染是应用的 RenderCore） | FO32 用例的 `_importable` 断言 |
| FO-064 RenderCore 能消费不同科学 Python 生成的冻结源 | 做了（3.11.14 的项目 venv 与 3.13.15 的私有 Python 都向同一个应用渲染器（3.13.11 的 rc-venv）交图） | FO32 两条（`facts.python_version` ≠ 应用） |
| FO-065 静态无 override 导出不强迫执行脚本 | 做了（RC-082 用例：静态 PDF 准备 = `static_source_available`；U08 的 `StaticSourceResolver` 语义不变） | `test_pdf_metadata_naming_a_script_never_triggers_execution` |
| FO-066 故障阶段准确，PDF 错误不触发装包 | 做了（写入器炸 → `failed_phase=compose`，依赖门一次不问） | `test_a_writer_failure_is_pinned_to_compose_and_never_reaches_the_dependency_door`；M27 红 |
| FO30 预检后输入或环境改变 | **enforced**（contractual 拆成三条 safe_stop + 一条 guided；见 enrollment notes） | `tests/test_preparation_api.py` 三条 + `test_fo30_*`（真实入口） |
| FO32 真实打开—编辑—重放—导出 | **observing**（两个出口本机 macOS 都真跑过；具名任务两条；不提升的理由在 enrollment notes） | `test_fo32_existing_env_*`、`TestManagedEnvJoin`；`evidence/u09/fo32-result-local-*.json` |

`U00_FACADE_LEDGER` 本阶段不动（没碰 facade 19 项）。registry 220 条 `execution_status` 一条没动（产品资格 not_run）。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest tests/test_execution_receipt.py tests/test_preparation_api.py tests/test_worker_runtime_report.py tests/test_trace.py tests/test_export_identity.py tests/test_export_pipeline.py tests/test_export_inspection.py tests/test_mcp_export_inspection.py tests/test_rendercore_sources.py tests/test_rendercore_job.py tests/test_rendercore_inspector.py tests/test_import_architecture.py tests/test_error_codes.py tests/test_databinding.py tests/test_first_open_workdir.py` | 主 `.venv`（没装候选包） | 0 | 408 通过 / 19 skip（候选包） |
| `PYTHONPATH=$WT/src <rc-venv>/python -m pytest tests/test_export_identity.py tests/test_trace.py tests/test_rendercore_app.py tests/test_rendercore_job.py tests/test_rendercore_inspector.py tests/test_export_inspection.py tests/test_mcp_export_inspection.py tests/test_rendercore_preview.py tests/test_rendercore_facade.py tests/test_rendercore_sources.py` | rc-venv（候选包 + 字体 + 真 worker） | 0 | 0 skip（identity 22 条含真导出四身份 / 节点表有界 / compose 故障） |
| `TAVOTTO_FOUNDATION_PROJECT_PYTHON=<host311> TAVOTTO_PRIVATE_PYTHON_REAL=1 TAVOTTO_PRIVATE_PYTHON_CACHE=<u05 cache> TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE=<wheelhouse313> PYTHONPATH=$WT/src <rc-venv>/python -m pytest tests/test_foundation_join.py` | rc-venv 应用（3.13.11）；项目 venv = Homebrew 3.11.14 + h5py 3.16.0；私有 Python = 真 pbs 3.13.15（缓存，零请求）；wheelhouse cp313 macOS arm64（matplotlib 3.11.1 / numpy 2.5.2 / h5py 3.16.0 / 依赖） | **0** | **4 通过 / 0 skip，66 s**（树静止后重跑的那次；之前一次与变异反证撞了时间窗，作废不计）：FO32 existing_env_join（guided：ambiguous_data 问一次 → project_root → 回执 3.11.14 / h5py / pid ok / 观察 partial / 绑定未观察 → ylim == [7,13,25]±5% → 标题 patch → RenderCore PDF / PNG / TIFF → PDFium 抽到标题与画布文字、`text_layer` / `fonts_embedded` verified、PNG 591×414、TIFF 同尺寸、artifact == sha256、semantic 三格式一致、run == job_id、`receipts[0]` == 准备回执事实）；错误同名数据负例（选脚本目录 → [601,1201,2401]、cwd_origin / 绑定修订 / semantic 身份都不同）；FO32 managed_env_join（工作目录门 → 依赖门带 private_python → 一次授权：供应 + 建代 + 装 h5py → 回执 3.13.15、prefix 在受管环境、base_prefix == runtime → 真值 → patch → 三格式）；FO30（A → 同大小同 mtime 改成 B → 复用 ready + matched=False + 旧快照、render 仍 A、原图导出照常 → invalidate → 新代 matched=True、值 = B、public_identity 变 → 改脚本重建 → source_revision 随文件） |
| 变异反证 30 条（scratchpad `u09/mut_a.json` / `mut_b.json` / `mut_c.json`，结果 `evidence/u09/mutations.json`） | 主 `.venv` 23 条 / rc-venv 7 条 | 每条非零；还原后 0 | 全红。M27 第一版绿——落在 `exportjob` 的守卫后面（冗余保证，变异不可杀），给 Trace 补单测后红 |
| harness 三步（pr lane，主 `.venv`）：`expected --lane pr` → 8 个用例文件 → `validate` | macOS arm64 | 0 / 0 / 0 | 预期 16 · 提交 16 · 有效通过 16 · 问题 0（117 通过 / 5 skip 是 observing 的那几条）；见文末 |
| `actionlint` 两份 workflow；`pytest tests/test_merge_queue_workflows.py tests/test_source_hygiene.py tests/test_e2e_leg_topology.py tests/test_foundation_harness.py tests/test_foundation_plan_integrity.py tests/test_foundation_fixtures.py tests/test_agents_rules_index.py tests/test_docs_references.py` | 同上 | 0 / 0 | workflow 合同 / 源码卫生（subprocess 钉编码）/ 台账 ↔ registry / 计划校验 / 夹具真值 / 文档索引全过 |
| `cd web && pnpm install && pnpm build` | 同上（worktree 里真 `pnpm install`） | 0 | tsc + vite 绿（只加了类型） |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest`（全量，主 `.venv`，后台 + 日志） | macOS arm64 | 1 | **5837 通过 / 7 红 / 232 skip / 2 deselect，34 分钟**。7 红逐条归因：`tests/native/test_run_cli_integration.py` 两条 = 本机 #452 噪音（本机 worker 解释器路径里含 "Tavotto" 字样、ctrl-c 90 s 超时）；`test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge` = 链 1 的遗漏（上表，已修）；`TestGate::test_probe_and_preparation_project_the_door_as_needs_input` = 本阶段新判据把手拼计划判成环境变了（用例已钉，已修）；`test_mcp_normalize.py::test_zero_edit_roundtrip…[two] / [tight]`（像素 3.2% / 11%）与 `test_workerd_pool.py::test_control_plane_reports_both_the_choice…`（池里多一条 python 会话）在合并态 bf87e511 与 HEAD 上单跑都绿——顺序相关；只跑后半套（test_m*–test_z*，1 失败 / 2866 通过 / 198 skip，12 分钟）像素那两条绿（与并行的 pnpm test 抢 CPU 有关的噪音），`test_workerd_pool` 那条仍红 → 用 LEAKWATCH 插件按用例逐条打印 `pool._workers`，定位到 `test_preparation_api::test_the_plan_reads_the_grant…` 的准备线程在用例结束、monkeypatch 还原之后才跑到 runner，拿真 pool 起了一条真 worker（我加的起会话前三比拉长了窗口）——修在 `reset_for_tests()`（上表），修后 m + p + workerd_pool 组合见下一行 |
| `pytest $(ls tests/test_m*.py tests/test_p*.py) tests/test_workerd_pool.py`（修后复跑，带 LEAKWATCH） | 同上 | 0 | 0 失败；`test_the_plan_reads_the_grant` 的 worker 不再泄到后面 |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest`（全量第二遍，修后，单独跑） | 同上 | 1 | **5840 通过 / 4 红 / 232 skip / 2 deselect，33 分钟**：native 两条 = #452 噪音（同上）；`test_mcp_normalize.py::test_zero_edit_roundtrip…[two] / [tight]` 像素 3.2% / 11% 只在**全量**里红——单跑、合并态单跑、后半套（m–z）单跑都绿，是前半套里某个用例留下的字体缓存 / 渲染状态（与 ADR 0067 §2 记的 `[two]` 校准项同族，U08 也遇到过本机字体缓存残留），**不是** U09 的判据或产品行为；没有继续二分（每轮 20 分钟以上），记在这里给 U10 |
| `cd web && pnpm test` / `pnpm i18n:check` | 同上 | 0 / 0 | 283 文件 / 4186 用例；翻译资源通过 |
| Linux / Windows / 3.10 上的 FO32 两个出口 | `foundation-u06-rendercore.yml`「U09 联调」步、`private-python-targets.yml`「U09 联调：managed_env_join」步 | — | **not_run**（PR 上随文件变动触发；结论按 run 号填进 PR 正文） |

**本切片的正例、负例、旧行为回归**：正例 = FO32 两个出口 + FO30 + 真 worker 的观察 / pid + MCP 真 worker 四身份；负例 = 错 receipt
（体检冒充 / 别的进程的 build 回执 / pid 缺失）、过期 generation（复用旧会话时数据变了 → matched=False 如实；spawn 与读之间变了 →
作废 `executed=true`）、错误同名数据（选错目录 → 干扰值 + 不同身份）、数据预检后变化（同大小同 mtime → 作废点名）、raw 秘密泄漏
（九根针）、报告假绿（M30：unknown 报 verified → 红）、PDF 元数据触发执行（M20）、写入器炸触发装包（FO-066）；旧行为回归 =
默认路径零改动、旧键一个不动（`test_export_pipeline` / `test_mcp_server` / U03 / U04 / U05 套件全过），老 worker 没自报仍是
`partial` 且 `runtime_rejected=None`。

**本次是否改变 case enrollment**：**是**——FO30 planned → enforced（pr，`tests/test_foundation_join.py::test_fo30_*`；registry 补
promotion 合同）；FO32 planned → observing（release；两条具名任务；理由与缺什么写在 notes）。计数 planned 8 / observing 10 / later 1 /
enforced 16（35 条）。旧后端早期 FO 记录与新终点分开：FO32 / FO30 的记录 `backend` 字段分别是 `rendercore` / `pymupdf`。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：Linux / Windows / 3.10 的 FO32 两个出口（两条 observing 腿 PR 上首跑）；真浏览器里的 UI（本阶段前端只加类型，
  界面不画身份 / 轨迹）；经 Codex 宿主的端到端 MCP（用例是工具级 + 真 worker）；跨 OS 的 byte 复现（非硬目标）；registry 220 条产品实例。
* 基础设施问题：本机 `find_worker_python()` 指向别的会话的 3.11 venv（#452）——FO30 / 真 worker 用例的 worker 是它，与结论无关；
  一次 `tests/test_foundation_join.py` 全量跑与变异反证撞了时间窗（树被动过），那一遍作废、树静止后重跑 4/4；
  `fontTools` 是 matplotlib 的依赖，「科学环境没有 PDF 候选包」的判据只点 pikepdf / pypdfium2 / uharfbuzz。
* 真正产品失败：无新增。顺带发现的事实：① `os.open` / h5py 的读在沙盒默认下没有只读回退（本来就没有，U03 的边界），
  FO32 靠工作目录决定 + 静态证据；② 画布上同一份源放两次的两个面板 id 相同，节点表按实例序号区分——之前 manifest 的 `object_boxes`
  也是同 id 两条；③ 检查器的 `text_layer` 只对画布文字（计划里的期望行）判，面板内部的文字层由独立读取器另核（FO32 用例两者都核）。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动。`LICENSE`、ruleset、`aggregate_gate.py`、required job、默认后端、
`security._PUBLIC_PATHS`、worker 守卫、写回事务、遥测白名单一个都没动；没有新增运行时依赖（h5py 只在测试环境）。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改回执 / 投影 / 端点载荷）→ `full-ci` + `@codex review`；ruff 两条 0；
针对性 pytest 0；变异 30/30 红；FO32 两个出口本机真跑 4/4；文档门禁 0；前端 build 绿。

**仍缺哪些默认启用 / 精确安装物资格**：全部。候选后端未切默认（U10）；FO32 只在本机 macOS 真跑过、其它平台等两条 observing 腿；
无系统 Python 的目标资格在 U11（`enabled` 全 false）；跨 OS byte 复现不承诺。

**两个出口的结论**：

* **`U09.existing_env_join`：通过（本机 macOS arm64，候选后端）**——用户已有的项目 venv（3.11.14 + h5py）经真实入口首开 → 问一次 →
  真值 → patch → 重放 → RenderCore 三格式 → 独立核文件 → 回执 / 身份 / 来源逐项对得上。**可以推进 U10**（切默认）：U10 拿走的输入
  见下。
* **`U09.managed_env_join`：本机通过（macOS arm64，真 pbs 3.13.15 + 离线 wheelhouse）**——产品自己准备环境的整链到同一终点。
  **目标资格留 U11**：其它目标只有 `private-python-targets.yml` 那条 observing 腿的证据，五个目标 `enabled` 仍全 false。

**下一个无阻塞阶段 / 子切片**：U10（切默认）。给 U10 的输入：① `scope=original` 的计划半张没有回执——接线点 `_export_produce_original`；
② 旧后端下 `identity.semantic / render` 是 None（没有 RenderPlan），切默认后所有导出都有；③ `public_projection()` 是唯一出口，
XMP / 报告 / 遥测要带产物身份只许带它（遥测白名单本阶段没动）；④ FO32 提升 enforced 要 required 矩阵里有第二解释器 + h5py + 候选包
+ 字体——建议随 U10 把候选包进必需矩阵时一起考虑；⑤ `MIN_TAVOTTO_VERSION` 仍要在下次发版抬（U08 的 artifactinspect）——本阶段桥没有
新 import。给 U11 的输入：FO32 managed 出口的目标资格 = `private-python-targets.yml` 三腿 + 冻结产物 + 正常入口（ADR 0064 第三档）。

**回退方式、不能假装可回滚的外部副作用**：revert 本 PR 即回退（新模块 / 用例 / 台账状态 / 类型；加的都是可选字段，旧客户端读得懂、
忽略）。外部副作用只有本机 scratchpad 里的 rc-venv / host311 / wheelhouse / 归档缓存 / 受管环境临时目录，删掉即可；没有设置写入、
没有发布。

## 发行归属：v0.17.0 待发（用户 2026-09-22 拍板范围 A）

v0.16.0 = 已落地的 20 个 PR（#496 版本号 PR 绿了就发）；**U09–U11 归 v0.17.0**。所以 #498 照常走评审，但**入队等 v0.16.0 发版链跑完**
（发行 SHA 定了 main 不能动 src/）。用户可见的行为变更（导出作业的 `trace`、准备结果的完整回执与 `preparation_plan_stale` 的具名理由）
已按 RELEASING.md 写进 `docs/release-notes/UNRELEASED.md`（英文、按症状与触发条件），发 v0.17.0 那天搬进 v0.17.0.md。

## PR #498 第一轮 CI（2026-09-22）两条 observing 具名任务第一次在 CI 上真跑就红——都是我的假设，不是产品

1. `foundation-u06-rendercore.yml` 四腿全红：第二个 `setup-python`（用户的 3.12 项目环境）把 PATH 上的 `python` 也换成了 3.12，后面 `python -m venv` 建出来的**应用** venv 就是 3.12 → 「项目 Python ≠ 应用 Python」的夹具前提当场不成立（ubuntu 3.10 / 3.13、macOS、Windows 四腿同一句断言）。修：`update-environment: false`，只经 `outputs.python-path` 点名。
2. `private-python-targets.yml` 三腿全红：同一 workflow 前一步（TestRealChain）已把私有 Python 供应进同一个数据目录，门上的载荷按 U05-B（Codex #475 P1）是**已就位态** `required=false`、计划上不挂供应段——我的用例把 `required is True` 写死了，只见过「还没有」那一种就位态（本机每次都是新目录）。修：按 `privatepython.python_of(source)` 分两支，两支都断言来源被说出口、字节数 / 联网与 cached 一致；本机把两种就位态各真跑一次（同一数据目录跑两遍）+ 两条变异（present_payload 谎报 required=true / offer_payload 谎报 required=false）各红。

Codex 第一轮（7867f970）：P1 = 上面第 1 条（同一根因）；P2 = 导出作业图全好、只有报告坏了时 `failed` 只从 outputs 算 →
轨迹末尾 `report ok`、`failed_phase=null`，而作业是 partial——修在 `exportjob` 的 partial 分支：没有坏图但报告坏了就
`trace.fail("report", report.error_code)`；两条报告用例各加断言，变异（那一行改 pass）红。

第二轮（ecb65c99）：rendercore 三腿绿、private-python 两腿绿；**Windows 两腿仍红，同一根因**——受管环境 / 项目 venv 的
`python.exe` 在 Windows 上是 launcher，控制面的 `child_pid` 是它，真正跑脚本的解释器是它的子进程，自报的 pid 对不上 →
`pid_mismatch` → runtime 连同 `inputs` 一起被拒 → 回执 partial、`binding_check.matched=None`（FO30 的 True 也是这么变 None 的）。
修：worker 自报加 `ppid`，`accept_runtime` 按 ppid 认 launcher 的子进程、如实记 `pid_check=ok_via_launcher`（ADR 0070 补一格）；
单测三支（launcher 子进程收 / 陌生 ppid 拒 / pid 对上仍是 ok）、变异（ppid 那一支改 False）红；真 worker 用例按「直接 / 经 launcher」
两种认。另按 Codex P1 的建议：夹具前提不成立时 `pytest.skip("not_run: …")`（`test_foundation_join._build_project_venv`）、
workflow 把两个解释器的版本打进日志、合同用例 `tests/test_foundation_workflow_python_setup.py` 钉「每个 job 只有第一次
setup-python 可以动 PATH」（变异去掉 `update-environment: false` 红）。

第三轮（28e879e6）：launcher 修法在 Windows 实测成立（private-python 三腿全绿、rendercore Windows 腿的 existing_env 绿）；剩下三条全是
**我的用例按 POSIX 形态写的**：① 清单 `line` 没随我改过行数的文件重生成（六腿同一条，第三次同形——推前清单加 `test_foundation_facade_ledger`）；
② FO30 的 `_rewrite_same_size` 按 LF 算「同样的字节数」，Windows checkout 把夹具 CSV 转成 CRLF（28 ≠ 33）→ 改成沿用原文件换行形态、按字节写，
本机 LF / CRLF 两种夹具各跑一次；③ 真 worker 用例的 `notes.txt` 用 `write_text` 落盘，Windows 上 6 字节变 7 → 按字节写、断言同时对磁盘 st_size。

第四轮（c9f91ef4）：只剩 Windows 一条真红——真 worker 的 `inputs.local_modules` 恒空：`observed_local_modules` 用 `os.path.commonpath`
判「在项目内 / 在引擎目录内」，Windows runner 的临时目录在 C:、checkout 在 D:，跨盘的 commonpath 抛 ValueError，`except … continue`
把 `labhelp` 吞了（文件观察那一半只比项目根、同盘，所以 files 对而 local_modules 空）。修：一份 `figcapture._within`（与
`projectenv.contained_path` 同形：realpath 后按前缀、`+ sep`、`normcase`），文件与模块共用；单测用 ntpath 钉 Windows 布局（跨盘 / 大小写 /
同前缀）+ 把 commonpath 换成一律抛的替身跑 `observed_local_modules` 与 `_note`；变异（退回 commonpath）红。

## harness 数字（pr lane，本机主 `.venv`，2026-09-21）

1. `foundation_harness.py expected --lane pr` → **预期实例 16**（planned 19）；退出 0。
2. `pytest -rs tests/test_foundation_harness.py tests/test_foundation_first_open.py tests/test_foundation_dependencies.py
   tests/test_foundation_private_python.py tests/test_foundation_join.py tests/test_execution_receipt.py tests/test_preparation_api.py
   tests/test_worker_runtime_report.py` → **117 通过 / 5 skip**（skip = FO18 / FO05 联网、FO32 两个出口的三条——它们是 observing，
   不在 pr lane 的预期集合里）；322 s；退出 0。
3. `foundation_harness.py validate` → **提交 16 · 有效通过 16 · 问题 0**；按 verdict `{"pass": 16}`；按产品结果
   `{"automatic": 6, "guided": 4, "safe_stop": 6}`（自动兼容成功 automatic + guided = 10；safe_stop 的通过不计入）；FO30 在通过的实例里
   （`FO30@4ea699fed11a0819`，guided）；退出 0。
