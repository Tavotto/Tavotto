# ADR 0070：ExecutionReceipt 完整化与 Manifest 关联——证据链的身份与公开投影

日期：2026-09-21 · 状态：**Accepted（U09；两条主线第一次联调；默认后端不切）**
相关：[0053 合同与准备](0053-foundation-contracts-and-preparation.md)、[0057 首开环境与工作目录](0057-first-open-environment-and-workdir.md)、
[0061 联合依赖准备](0061-joint-dependency-preparation.md)、[0063 私有 Python](0063-private-python-provisioning.md)、
[0067 候选切换开关与执行侧源](0067-render-backend-switch-and-execution-sources.md)、[0068 ArtifactInspector 与 Manifest](0068-artifact-inspector-and-manifest.md)、
[0071 Trace 与旧计划失效政策](0071-trace-and-stale-plan-policy.md)、[0021 native 进程归属](0021-tavotto-run-product-contract.md)；
实施包 `docs/implementation/tavotto-foundation/`（`phases/U09_join.md`、`01_SCOPE_AND_DECISIONS.md` D06 / D09 / D13、
`03_CI_POLICY.md` §8、`04_ARCHITECTURE.md` §2 / §3、`05_TEST_STRATEGY.md` §3 / §7；registry RC-075 ~ RC-082、FO-059 ~ FO-065、FO32）。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 回执只收谁的自报 | **跑了脚本的那个进程**：worker 的 `runtime_report()` 带 `report_origin=build` 与 `pid`；`receipt.from_worker` 核 origin，并把 pid 与控制面自己起的那个子进程对（`EngineWorker.child_pid` = `proc.pid`、`WorkerdWorker.child_pid` 来自 `open_session` 响应、`NativeSession.child_pid` = 握手帧的 `process_pid`）。体检（`projectenv.probe_environment`）/ 探针 / 手拼的字典**一律拒收**：`runtime=None`、`runtime_rejected ∈ {not_a_build_report, pid_mismatch}`、`completeness=partial`。控制面起的那个进程是个 **launcher**（Windows 上 venv 的 `python.exe`：CreateProcess 出真正的解释器再等它）时，自报的 `pid` 对不上、`ppid` 对得上——收，`pid_check=ok_via_launcher`（如实写是经 launcher 核的，不冒充直核；#498 第二轮 Windows 腿受管环境的回执整份 partial 就是这么来的）。控制面不知道 pid（老 workerd）时那一维不核、`pid_check=unavailable`，不冒充核过 | `engine/receipt.accept_runtime`；`tests/test_execution_receipt.py::TestReceiptOnlyAcceptsThisSessionsReport`（含 launcher 子进程 / 陌生 ppid 两支）、`tests/test_worker_runtime_report.py`（真 worker：直接是它或 launcher 的子进程） |
| 已观察的输入 | `figcapture.InputObserver`：三处 `open`（`builtins.open` / `io.open` / `Path.open`）+ numpy 的 `DataSource.open`（2026-09-24 修订，#545：`np.loadtxt` / `np.genfromtxt` 的打开器在 numpy 载入时就绑了原来的 `io.open`，前三处看不见；按返回文件对象的 `.name` 记）的只读包装，记**真正打开的那条路径**（先于只读回退装，回退换出来的路径经它记下）落在项目根内的文件（相对路径 + 大小 + sha256，去重、有界 256 条、超过 `truncated`、源码文件剔掉——它们是 `source_revision` / `local_modules`）；`observed_local_modules`：`sys.modules` 里 `__file__` 落在项目根内的模块。**`observation` 永远是 `partial`**：h5py / netCDF / C 扩展的 `H5Fopen` / `fopen`、`os.open`、网络、子进程一条都看不见（D13 / FO-061），`unobserved` 列出没看的通道 | `engine/figcapture.py`；`engine/worker.py`（build 那一刻定格）；`test_the_report_carries_origin_pid_and_observed_inputs_of_the_process_that_ran` |
| 数据身份进哪个身份 | 观察到的文件身份（相对路径 + sha256）**进公开语义身份**（`public_identity`）：同一脚本同一环境读到不同内容的同名数据就是另一次执行（同名干扰 / 预检后被改都在这一维分开）；机器路径仍然不进 | `test_observed_data_identity_enters_the_public_identity_but_paths_do_not` |
| 数据绑定 | `databinding.binding_for(script, root, mode)`：按 cwd 档记「脚本会读哪些文件、内容 sha256、修订摘要」，进 `PreparationPlan.binding`；回执带同一份，`binding_check()` 与观察到的输入逐条对：`matched ∈ {True, False, None}`——None = 一条都没观察到（原生读），**不冒充核过** | `test_binding_check_compares_expected_with_observed_and_never_pretends`、`test_the_binding_recorded_at_plan_time_is_matched_against_what_the_script_read` |
| 导出路的回执 | 与准备接口同一份账：`_execution_receipt` 带 `grant`（`workdir.grant_for`）与此刻的 `binding`；热态 Figure 是它跑那一刻的数据（04 §3），数据后来变了导出照常，回执 `binding_check.matched=False` 如实写着 | `app._execution_receipt`；`tests/test_foundation_join.py::test_fo30_*` |
| 四种身份（RC-075 ~ RC-078） | `rendercore/identity.py`：**semantic** = `plan_identity`（规范化请求 + 源的语义坐标）；**render** = semantic + 后端名与版本 + 栅格器与版本 + 字体政策版本（allowlist 的 sha256，与预览缓存键同一份）+ 像素参数；**artifact** = 封口文件字节 sha256（等于发布的字节，绝不再写进文件）；**run** = 作业 id（不进前三个）。不知道的一维写 None、不省略键 | `test_the_run_identity_never_enters_semantic_or_render`、`test_the_render_fingerprint_changes_with_every_known_influence`、`test_the_artifact_hash_is_not_written_into_the_artifact` |
| Manifest 的来源段 | `manifest["identity"]`（四身份）+ `manifest["provenance"]`：源产物公开身份、回执公开事实（`receipt.public_facts()`：身份 / 完备性 / generation / source revision / 解释器版本 / 关键包 / cwd 来源 / 观察完备性 / 绑定核对计数——没有路径、argv、stem）、节点表（RC-079：画布对象 id + 实例序号，同一份源放两次是两个实例，外来页 `internal=unknown`，有界 `NODES_LIMIT`）。执行侧源随附 `FrozenSource.receipt`；MCP 直出路（worker 自己写文件、没有 RenderPlan）经 `artifactinspect.execution_provenance()` 用同一份算法补齐（源是这次执行的 Figure，`kind=figure`，semantic 对格式不变） | `rendercore/job.plan_facts`、`inspector._identity_block / _provenance_block`；`test_a_real_export_carries_four_identities_and_a_node_table`、`test_a_real_mcp_export_carries_identities_receipt_facts_and_the_data_binding` |
| 公开投影（RC-081 / FO-062） | 只有一份可以离开本机：`inspector.public_projection(manifest, trace=)`——身份与结论（四身份、格式 / 字节数、政策与裁决、每项四值、载体 / 尺寸 / 像素 / 密度、字体名、源的 origin / kind / 字节 hash / 回执身份 / patch hash、回执公开事实、节点的种类与来源关系、可复现性口径、有界轨迹）。**不带** notes（可能引用期望文字行）、`plan.text`、对象框、`source_id` / 节点 id / 面板 id、`observed` 大块、任何路径 / argv / 环境变量。本轮不写 XMP；将来 XMP / 报告 / 遥测若要带产物身份，只许带这一份投影 | `test_the_public_projection_carries_identities_and_verdicts_but_no_needles`（九根针：data_dir / home / prefix / 解释器 / 临时目录 / 脚本正文 / 图内文字 / argv / 文件名） |
| PDF 元数据不是授权（RC-082） | 执行的来源只有注册表（扫描出的 script ↔ stem 关系）：/Info 里写着脚本名 / 命令的 PDF 不被当成有脚本，准备是 `static_source_available`，一个 worker 不起 | `test_pdf_metadata_naming_a_script_never_triggers_execution` |
| 可复现性口径（RC-078） | `identity.REPRODUCIBILITY`：semantic = 同一意图；visual = 同一渲染栈下语义 / 视觉可复现；byte = 字节逐位相同**不由任何身份保证**（跨平台 / 跨版本不承诺；同机同栈只作观察）。hash 相同不断言字节相同 | 公开投影的 `reproducibility` 段 |

## 1. 为什么要核 pid，而不只是 `report_origin`

`report_origin=build` 挡的是**形状**上的冒充（体检字典没有这个键）；一份**真的** build 回执被抄到另一条会话上——上一代会话的、别的项目同名脚本的、诊断包里翻出来的——形状完全对。控制面知道自己起的是哪个进程（`proc.pid`；workerd 在 `open_session` 里报；native 在握手帧里报），自报里的 `pid` 与它对不上就不是这次执行的事实。两条判据各自一条变异（M01 / M02）都红。

## 2. 为什么观察是 partial 而不是「尽量完整」

D13：简单图也要求所有原生 I/O 证据是错的方向。观察器只包 Python 的 `open`——那是 `csv` / `json` / `numpy.load` / `pandas.read_csv` / `PIL.Image.open` 走的路——外加 numpy 的 `DataSource.open`（`np.loadtxt` / `np.genfromtxt`，2026-09-24 修订）；h5py 经 HDF5 C 库、`np.memmap` 经 `os.open`、`urllib` 经 socket，包不住。与其装一个全系统跟踪器，不如把没看的通道写在回执上：`unobserved=[native_io, network, subprocess, os_open]`。FO32 里 h5py 读的那份 HDF5 因此在回执上是**未观察**（`binding_check.matched=None`），由静态证据（`databinding` 两处同名 sha256 不同 → 问一次）与图内值（[7, 13, 25]）守住——不是冒充核过。

## 3. 为什么 semantic 身份对格式不变、render 身份对格式变

同一次执行、同一份意图写成 PDF 与 PNG 是同一张图（semantic 相同），但画它的栈不同：PNG 多了栅格器（PDFium 版本）与像素参数。RC-076 的 must_fail（同名新字体仍命中旧缓存）落在 render 这一维：字体政策版本是 allowlist 文件的 sha256，加一张脸 / 换一个 hash 都会变，预览缓存键与 render 身份认的是同一个数（`identity.fonts_policy_version`）。

## 4. 反证（每条变异一次就红；scratchpad `u09/mut_a.json` / `mut_b.json` / `mut_c.json`）

| 变异 | 红在 |
|---|---|
| 自报不核 `report_origin` / 不核 pid / pid 报常数 | `test_a_probe_result_cannot_pose_as_the_worker_report` / `test_a_build_report_from_another_process_is_rejected` / 真 worker 用例 |
| 公开身份不含观察到的数据身份 | `test_observed_data_identity_enters_the_public_identity_but_paths_do_not` |
| `binding_check` 把没观察到当匹配 | `test_binding_check_compares_expected_with_observed_and_never_pretends` |
| 观察器不记 / 装在回退外层 / 源码混进数据 / 报成 complete | `test_the_report_carries_origin_pid_and_observed_inputs_of_the_process_that_ran` |
| render 不含字体政策 / run 进 semantic / artifact 不是文件字节 | `test_export_identity.py` 对应三条 |
| 公开投影带 notes / source_id / interpreter | `test_the_public_projection_carries_identities_and_verdicts_but_no_needles` |
| MCP 直出路丢回执 / 语义随格式变 | `test_a_real_mcp_export_carries_identities_receipt_facts_and_the_data_binding` |
| PDF 元数据里的脚本名被登记成脚本 | `test_pdf_metadata_naming_a_script_never_triggers_execution` |
| 导出路不附回执 / 同一份源放两次合成一个节点 / 外来页编造 internal | `test_fo32_existing_env_*`、`test_a_real_export_carries_four_identities_and_a_node_table` |

## 5. 没做 / 边界

* 不写 XMP、不改遥测白名单（遥测仍不带任何产物身份）；公开投影只是**定义**了可以离开本机的那一份，谁要带就带它。
* `scope=original` 的导出仍只有请求级计划半张（没有回执）——接线点在 `_export_produce_original`，归 U10。
* 严格 byte 模式（跨平台字节一致）不是本轮硬目标；`reproducibility.byte` 把这句话写在投影里。
* 观察器不装在 native 会话上（用户自己的进程在附着之前已经读过数据）：native 回执的 `inputs` 是 None，如实。
