# Tavotto 跨领域科研脚本兼容性实施方案

> 状态：**Proposed，实施规划，不代表产品能力已通过验收**。
> 编写日期：2026-10-22。代码基线：`d07bfc425b393f8cb20625bc94aa796e2143e433`。
> 本次完成代码、既有用例入口和 PR 状态核对；没有重新执行科研项目、平台验收或覆盖率实验。
> 本文承接已有脚本兼容性工作，不重启 P00–P09，不覆盖既有 ADR、规则和验收判据。
> S0 实施与冻结后的真实观测单列在 [S0_BASELINE.md](S0_BASELINE.md)；本文仍保留立项时的代码审计快照。

## 1. 目标与实施边界

目标是让更多**原本能在用户环境中正确产生 Matplotlib Figure 的任务**，在 Tavotto 中完成
打开、常用编辑、导出及所需的持久化。适配单位收敛为：

1. 有证据的执行上下文：解释器、启动机制、工作目录、参数、环境代和授权来源。
2. 原进程中的 Figure：保持科研库的对象、计算与原生绘制语义。
3. 有限的图元操作契约：明确哪些对象上的哪些操作能修改、撤销、重绘和重放。

领域库的名字用来描述样本分布，不作为默认的实现分支。普通领域计算最终生成标准 Artist 时，
应复用通用编辑层；只有新增图形语义才考虑局部适配器。

本轮不新建渲染器、第二棵 Figure 树、第二套 override 系统、第二个环境数据库或第二个兼容性 runner。
保留现有 RenderCore / PDF 契约层与零 PyMuPDF 闭包。原生已选择的图像与既有 vector/hybrid/raster
预览策略照常存在；不能靠全图栅格化、关闭字体/TeX、降低导出分辨率来制造兼容成功。

Notebook 内存接入、任意 shell/Makefile/专业软件启动器注入、远程/HPC、非 Matplotlib 图的语义重建，
均不计为当前已支持。它们需要单独原型与 ADR/1.0 范围判定，不阻挡本轮已有入口的兼容修复。
AI 生成可编辑版本属于候选重建，不作为原图语义等价或基础兼容的证据。

## 2. 当前能力：已有实现与实际边界

“已有”指在上述 SHA 找到实现和相应看护入口，不等于本次重跑通过；“待验证”不能直接写成缺陷。

| 能力 | 当前实现依据 | 当前边界与本轮动作 |
| --- | --- | --- |
| 统一执行描述与三种 safe cwd | `engine/execspec.py` 的 `safe_spec`、`worker_argv`；`workdir.py`、`databinding.py`；`tests/test_execspec.py`、`tests/test_workdir_mode.py` | 已有 `sandbox / project / project_root`。补跨读取机制的产品验收，不继续逐库模拟文件系统。 |
| 解释器发现、来源解析、受控依赖准备 | `pool.py`、`userenvs.py`、`importscan.py`、`depresolve.py`、`deprepair.py`；`tests/test_import_origin_static.py`、`tests/test_dependency_plan.py` | 已能区分声明、本地模块和用户环境。不能把静态 import 扫描当作完整依赖证明，也不能为了兼容升级用户科研环境。 |
| Conda 激活与缓存失效 | `launcher.py` 的 `launcher_for_interpreter`、`activated_delta`、`context_key`；`tests/test_conda_launch_context.py` | Windows/POSIX 已实现。只覆盖受信任 Conda 安装范围和环境变量增量，不能恢复任意激活脚本的服务、shell 函数或进程内状态。 |
| ROOT 环境叠加 | `rootenv.py`、`projectenv.py`、`pool.py`、`worker.py`；`tests/test_root_env_overlay.py` | 已合入 #1282；含 Windows DLL 与 `libcppyy` 接入。保留现有修复，核验它与探针、热会话、成功记录的身份是否一致；不照此为每个领域增建 `xxxenv.py`。 |
| 成功启动记录及再次打开复用 | `launchctx.py` 的 `record_success`、`verify`、`reusable_run_config`；`runconfig.commit_success`；`tests/test_reuse_last_success.py`、`tests/test_launchctx_native_atomic.py` | 已实现，不能列为从零新增。当前按脚本一份成功记录；只在明确请求复用且依据有效时采用，敏感配置不自动复用。记录是事实，不是执行命令或授权。 |
| 桌面运行路由与有界回退 | `runroute.py`、`execpolicy.py`、`prepsession.py`；`tests/test_runroute_wiring.py`、`tests/test_execution_fallback_e2e.py` | 已有静态高置信路由、沿用成功 profile、blocked 后一次 safe→native 回退。CLI/MCP 与桌面的授权合同不同，见下文。 |
| 终端原上下文接入 | `runspec.py`、`runcli.py`、`bridge_runner.py`；`tests/native/test_run_cli_integration.py`、`tests/bridge/` | `python file.py` 和 `python -m module` 已支持；继承终端 cwd/env/stdin。任意 shell、管道、`py -3.x`、任意 Python flags 等不能据此宣称支持。 |
| 桌面拥有的 native 进程与输入 | `desktopproc.py`、`nativesession.py`、`inputbroker.py`；`tests/native/`、`tests/test_native_launch_basis.py` | 已有，不等于附着任意 IDE/kernel。`execspec.ENV_SOURCES` 当前只认 `sidecar_inherited`，不能冒充恢复终端或 IDE 全部状态。 |
| 原进程 Figure 与统一编辑会话 | `figsession.LiveFigureSession`、`wireproto.py`、`enginesession.resolve` | safe/native 共用编辑语义；已有拥有线程断言、串行请求、原子预览发布、SVG/manifest 同次响应。补组合场景，不另造会话层。 |
| 捕获与逐图失败隔离 | `figcapture.py`、`LiveFigureSession.instrument_all`、`runtimeasset.py`；`tests/test_compat_capture_parity.py` | 已有 savefig/pyplot 捕获、无原文件的 runtime 资产、失败图事实。逐图隔离不等于任意未知 Artist 都可独立隔离；后者先做反例实验。 |
| 标准图元与部分特殊对象能力 | `manifest.py`、`overrides.collection_caps`、`axestraversal.py`、`legendmodel.py`、`tickmodel.py`、`colorbarmodel.py`；`tests/test_artist_families.py`、`tests/test_invariants_engine.py` | 已有按真实行为判断能力、特殊 axes、3D 样式等。未知子类不能仅凭同名 getter/setter 获得完整编辑资格。 |
| override 身份、撤销与重放 | `FigState.identity`、`overrides.py`、`patchspec.py`；`tests/test_override_identity.py`、`tests/test_restore_failure_retry.py`、`tests/test_equivalence_matrix.py` | 已有身份匹配、全量列表、还原欠账和冷热一致性。新增契约必须复用这些实现，不改 gid 来“修好”新案例而破坏旧文档。 |
| native 屏障与不一致阻断 | `bridge_runner.release_barrier`、`nativesession.py`；`tests/native/test_native_barrier_semantics.py`、`tests/native/test_native_inconsistent.py` | 已有离开屏障前恢复脚本原态、下个屏障重采基准并重放；offline 不回落 safe，不一致不假装恢复。 |
| 写回原始文件 | `app.py` 与 `pool.one_shot`；`tests/test_write_back.py`、`tests/test_worker_roundtrip.py` | eligible safe 资产走 prepare→verify→commit。native 面板拒绝原件写回；无原始产物的 runtime 资产也不具备原件写回资格。编辑/导出成功不能冒充写回成功。 |
| 兼容性 runner、对照与探索 | `scripts/ci/compat_matrix.py`、`compat_driver.py`、`compat_guardian.py`、`compat_explore_oracles.py`；`tests/test_compat_guardian.py`、`tests/test_compat_explore_oracles.py` | 已有九级漏斗、产品路由维、原生对照、执行见证、保型变换、聚类/最小化。本轮增加真实项目分组与固定队列前后比较，复用现有判据。 |

表中仅写文件名的后端模块位于 `src/tavotto/engine/`，`app.py` 位于 `src/tavotto/`；其余按显式路径定位。主要契约入口：
[执行入口](../../rules/backend/execution-entries.md)、
[Figure 捕获与执行](../../rules/backend/figure-capture-and-execution.md)、
[native 控制面](../../rules/backend/tavotto-run-control-plane.md)、
[图元能力](../../rules/backend/axes-and-artist-families.md)、
[写回事务](../../rules/backend/writeback-transaction.md)。

### 2.1 不能遗漏的现行决策

- 按 [ADR 0127](../../adr/0127-execution-policy-and-unified-retry-guard.md) 与
  [ADR 0133](../../adr/0133-run-routing-compatibility-first.md)，桌面用户主动运行 + `compatible`
  策略可直达 native，或在 blocked 后回退一次；已有裁决接受回退可能重复先前副作用。
  本计划不额外加确认、不取消这一政策、不宣称全局“恰执行一次”。`safe_only`、开关、来源闸门原样保持。
- CLI 继续 `nativeperm` 合同，MCP 不获得 native 权限。只读检查、扫描、打开已保存预览、读取能力都不能
  变成新执行入口；正常热编辑不得额外执行科学脚本入口。写回验证的一次性重放仍须执行。
- 成功记录中的数据观察依然有盲区：C 层读取、网络等不能因 `basis_observed` 为真就声称全部输入已知。
  它也不证明科学计算可复现。不能序列化整个 `os.environ`、任意 argv 或 Figure pickle 来补这个洞。
- 既有文档含历史阶段措辞：捕获细则 P09 段仍写成功记录“消费端不在本期”，但当前
  `launchctx.reusable_run_config` 已在；`execspec` 开头也保留早期 native 占位叙述。
  实施以当前代码、后续修订与用例为准；S0 更新相关历史提示时保留原审计日期，不改旧证据。

### 2.2 在途工作及重复开发边界

2026-10-22 在线核对：#1275、#1276、#1282、#1255 已合并；下列 PR 尚未合并，不纳入本地基线的已交付能力。

| PR | 当前工作 | 本计划的关系 |
| --- | --- | --- |
| [#1250](https://github.com/Tavotto/Tavotto/pull/1250) | 同 stem 多 Figure、文件归属、存后变化提示与新增夹具 | 后续优先消费其结果，不重复修同一段；文件→图映射仍跟踪 #1251。 |
| [#1299](https://github.com/Tavotto/Tavotto/pull/1299) | glob/目录探路/C++ ENOENT 的 cwd 引导 | 验收“看到引导→选择正确目录→实际出正确图”；提示出现本身不算恢复覆盖。 |
| [#1277](https://github.com/Tavotto/Tavotto/pull/1277) | 非 Matplotlib 图生成可编辑候选 | 单独产品能力，不混进本计划的原生 Matplotlib 兼容率。 |

每个实施 PR 开始前重新读取主干与上述 PR 状态。不得将计划写作时的开放/合并状态当永久事实。

## 3. 收敛后的执行顺序（2026-10-22）

原 S0–S5 六步安排不再作为执行承诺。保留第 2 节的代码盘点，按真实失败决定下一项工作。

1. 测量修复已独立合入 main：#1307（SVG、runner_error）、#1321（原生对照沿用 savefig 实际裁切框）、
   #1322（素材清单不列虚构占位）。
2. 研究语料只登记上游提交、文件哈希、运行配方和任务；显式下载到仓库外，经哈希校验后交给现有 Guardian。
   普通 CompatBench 保持 149 项。科研 16 项不进入默认测试，不在主仓库保存上游源码、数据或完整运行日志。
   产品腿按登记的 argv 与工作目录启动，与原生腿同源。
3. Leaf（argv）与 Qudit（cwd）的 S0 失败来自研究产品腿没传登记的启动上下文，真实入口复测产品通过，
   用新测量重跑即可。Celerite 的数组一致，剩零编辑布局漂移；Gala 的 PDF 失败是测量环境把两套依赖装进同一 venv 的 ABI 冲突，现已拆成 Tavotto / 科研两个 venv。
4. 共性修复先在开发项目验证，再用未参与调参的项目检查泛化。现有 8 个留出承诺保留，取消代理保管与
   “到 S5 解封”的流程。需要时直接按固定任务做验证，记录有没有参与过开发。

## 4. 完成判据

- 本轮只有首轮探索基线，尚未完成用户路径验收；不能把 engine 成功率写成用户总体覆盖率。
- 原生与 Tavotto 使用同一解释器、依赖、输入和声明的启动条件。不同条件必须显示在结果里。
- 数据一致性、绘图样式、页面布局分别取证；尺寸不等不能替代数组比较，也不能直接归因 RNG。
- 每次修复保留原始失败、改动后的结果及已有通过项目的回归结果。环境准备失败仍单列，不能缩分母。
- 对用户入口的承诺需覆盖打开、编辑、导出、保存重开，以及目标平台；写回仍遵守原事务验证，不降低门槛。

具体观测与复核记录见 [S0_BASELINE.md](S0_BASELINE.md)。
