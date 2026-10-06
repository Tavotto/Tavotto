# 准备计划、执行回执与源图产物（统一实施包 U01，ADR 0053）

> 2026-09-20 随 U01 新增，同日随 U03（ADR 0057）扩到「需要输入」/ 过期计划 / 环境证据；2026-09-21 随 U09
> （ADR 0070 / 0071）扩到「自报只收这一条会话的」/ 输入观察 / 数据绑定 / 旧计划失效政策 / 阶段轨迹；速查行在
> `src/tavotto/AGENTS.md`「按改动路径找细则」表里（`engine/preparation.py` / `receipt.py` 那一行）。
> 这里是这一主题规则的**唯一全文**；速查表只留一行。改规则改这里，并同步那一行。

- **计划与观测分开**：`preparation.PreparationPlan` 是「打算怎么跑」（环境证据 / 依赖意图 /
  LaunchContext / grant / 预算），`PreparationResult` 是「实际发生了什么」（状态 / 现有
  runtime 的事实 / 回执 / 错误 / 取消时刻）。计划**只读**产品自己的决定
  （`pool.resolve_worker_python` / `projectenv.state` / `workdir` / `depresolve`），不替产品
  选环境、不装包、不改 cwd。终局字段先于终局 `status` 写（与 `exportjob` 同一条纪律）。
- **执行只有一条路**：`pool.build_owned()`（`build()` + 池锁里给出的 `created`）。准备接口不另写
  `get + ensure_built`——自动 fallback 只有一份实现，漏掉一个入口就是「素材库里能打开、准备
  接口打不开」。已 build 且解释器决策没变的会话直接用它记下的 build 响应装配回执，不发请求。
- **取消只碰自己起的会话**（D11 / FO-009）：接受时刻只有「还没碰 pool」与「build 返回」
  两个；`created_runtime` **由池原子给出**（不从 `peek()` 快照推断——两份计划同时起步、池只建
  一条时主人只能是一个），为真才 `pool.force_cancel`，别的消费者的会话不碰；native 会话不在
  池里，永远碰不到；不承诺撤销外部副作用，`note` 如实说。
- **按项目认领**（FO-008）：`SERVICE.get / cancel` 都带 `project_id`，对不上就是没有
  （404 `preparation_not_found`），绝不返回别的项目的状态；执行线程用 `bound_project`
  钉项目。三个端点在会话认证之内，不进 `security._PUBLIC_PATHS`。
- **回执两半缺一半就是半张**：worker 自报（`figsession.runtime_report()`，随 v1 `build`
  的 `runtime` 回来，加字段不升版，safe / native 同一份）+ 控制面账本（generation /
  `script_sha1` / 来源标签 / spec 稳定字段 / LaunchContext）。老 worker 没自报是
  `partial`，不补不猜；`RECEIPT_PACKAGES` 是闭集，回执不是环境普查。
- **身份三分不许混**：私有失效键**含**机器路径（区分两个 venv 靠它）；公开语义身份不含
  任何路径；最终文件 hash 只在 `SourceArtifact.bytes_sha256`，不回写进语义身份；`receipt_id` /
  `generation` 是实例元数据，语义身份（`semantic_identity` / `plan_identity`）只吃回执的公开身份
  `receipt_identity`。默认 `to_payload()` 不带机器路径，诊断包用 `include_private=True`；
  `PreparationPlan.to_payload()` 同样：项目外的解释器只给来源标签（路径存私有字段），错误分支的
  `project_env` 只留 `ok / code / module / reason`。
- **LaunchContext 是派生视图**：`execspec.launch_context(spec)` 从 spec 算，`ExecutionSpec`
  与 `worker_argv` 的 golden 一个字节不动。四个 `cwd_origin` 各有且只有一个生产者
  （`sandbox` / `project`=脚本目录 / `project_root`=项目根 / native；ADR 0057 §二）；
  grant 只由 `workdir.set_mode / grant_for` 记账，只记时刻不记人，多 `mode` 与 `decided`。
- **环境选择前移的落点就是 `plan_for`**（U03，ADR 0057 §一；**ADR 0114 起默认的确认模式下不再替用户发现 / 体检 / 记住
  项目 venv**，那一整段只在兼容开关 `TAVOTTO_ENV_ADOPTION=auto` 下发生，默认由 `envadvice.recommend()` 给纯读建议、用户采用后
  才有项目级记录）：它调 `pool.resolve_worker_python(root,
  script=…)`——项目 venv 的发现 + 体检 + 记住在这里已经发生（每进程每项目一次），计划里
  `environment.python_version / matplotlib_version / support / discovery / invalidated / error.explicit`
  如实写下选了谁、凭什么（体检量到的事实，ADR 0053 的公开投影：项目外的路径一律 None）、发现了什么
  没采用、上一条自动决策是不是刚作废、显式选择为什么用不了。计划仍然只读产品的决定，不替它选。
- **首开要问的事是终局 `needs_input`**（U03，ADR 0057 §三）：`workdir.decision_for` 说要问
  （数据只在项目根找得到 / 两处同名不同值）时 `register()` 直接落 `needs_input`，
  `result.required_input` = `workdir.confirmation_payload`（选项 / 证据 / 怎么回答），不起线程、
  不碰 pool。runner 抛 `workdir_confirmation_required`（计划后决定被清掉）落同一终局。答完
  （`PATCH /api/engine/workdir`）重新准备。
- **`ready` 要求这张面板真的被捕获**（QA 2026-09-24 PATH-B1）：build（或复用的会话）的捕获表里没有 `plan.stem` 时落
  `error`，code 与渲染入口**同一个**（`no_figures_captured` / `no_figures_captured_silent` / `unknown_stem`，
  `pool.missing_stem_error` 走 `_explain_empty_capture` 同一条换码路），回执照样留着（脚本确实跑过）。
- **门也管复用**（QA 2026-09-24 PATH-B3）：池里活着、**上一次跑完的 build 失败了**的会话（`build_failed`；不是
  `not built`——正在 build 的会话只是让后来者排队复用，不过门，Codex #599 P2）再被取用就是
  在它起会话时的 cwd 里重跑整个脚本——`pool.acquire` 复用它之前过 `_workdir_gate`（与 `_new_worker` 同一处），
  证据变成要问就抛同一个 `workdir_confirmation_required`；会话本身不动（答完 `PATCH /api/engine/workdir` 会收掉）。
  已 build 的热态会话不再跑脚本，不过门。
- **过期计划不执行**（FO-007）：执行线程在起会话之前把 `workdir.grant_for(root)` 与计划记下的
  `grant` 比一次，不一致就 `preparation_plan_stale`，一行脚本不跑。缺包后自动接手换了解释器（ADR 0107：
  用户自己的 Python 默认在脚本目录跑）时，`pool.build_owned(before_retry=…)` 在**第二次执行之前**把授权 / 解释器 /
  数据绑定按同一份 `_stale_reason` 再比一次，不一致同样作废（`executed=True`：第一次按计划跑过），不在计划没写过的
  解释器或 cwd 里重跑。
- **自报只收这一条会话的（U09，ADR 0070）**：worker 的 `runtime_report()` 带 `report_origin=build` 与 `pid`；
  `receipt.from_worker` 核 origin，并把 pid 与控制面自己起的那个子进程对（`EngineWorker.child_pid` /
  `WorkerdWorker.child_pid` / `NativeSession.child_pid`）。体检 / 探针 / 手拼的字典**一律拒收**：`runtime=None`、
  `runtime_rejected ∈ {not_a_build_report, pid_mismatch}`、`completeness=partial`；控制面起的是 launcher（Windows 上 venv 的
  `python.exe`）而解释器是它的子进程时按自报的 `ppid` 认、`pid_check=ok_via_launcher`；不知道 pid 就 `pid_check=unavailable`，
  不冒充核过。拿预检结果冒充回执是 must_fail。
- **输入观察永远是 partial（D13 / FO-061）**：`figcapture.InputObserver` 只包 Python 的三处 `open` 与 numpy 的 `DataSource.open`（2026-09-24，#545：
  `np.loadtxt` / `np.genfromtxt` 的打开器在 numpy 载入时就绑了原来的 `io.open`，前三处看不见；按返回文件对象的 `.name` 记；
  `inputs.channels` 如实列出装上了的通道 `python_open` / `numpy_datasource`）（先于只读回退装，
  记实际打开的那条路径；源码文件剔掉、去重、有界），h5py / `os.open` / 网络 / 子进程看不见——`inputs.observation=partial`
  + `unobserved` 如实列出；观察到的文件身份（相对路径 + sha256）**进公开语义身份**，机器路径不进；`local_modules` 是
  `sys.modules` 里落在项目根内的模块。**解释器自己的目录不算**（QA 2026-09-24 PATH-B2）：项目根里的 `.venv`（ADR 0057 首开第 4 条）
  的 prefix / site-packages 严格落在项目根内时，底下的库文件既不记成数据输入、也不占预算、也不算本地模块
  （`figcapture.interpreter_dirs_within`，主语是跑脚本的那个进程的 `sys.prefix` 等）；前缀等于或包含项目根时不排除。build 那一刻定格，之后进程里再读什么都不是它的输入。
- **数据绑定与旧计划失效（U09，ADR 0071 / FO30）**：`databinding.binding_for(script, root, mode)` 按 cwd 档记「会读哪些
  文件、内容 sha256、修订」进 `PreparationPlan.binding`；执行线程起会话之前把授权 / 解释器决策 / 数据绑定三样与此刻各比
  一次，不一致就 `preparation_plan_stale` + `reason ∈ {grant_changed, environment_changed, data_binding_changed}` +
  `executed=False`，一行不跑；build 之后按观察到的输入再核（`receipt.binding_check()`），不一致就作废且 `executed=True`。
  **复用热态会话时不一致不是错误**：`ready` + `binding_check.matched=False` + note「旧快照」——不自动重算、不清编辑，
  用户要重算走既有 `/api/engine/invalidate`。`matched=None` = 一条都没观察到（原生读），不冒充核过。导出路的回执与准备
  接口同一份账（`_execution_receipt` 带 grant 与 binding）。
- **观察器的路径判据只有一份**：`figcapture._within`（realpath 之后按前缀判、`+ sep`、`normcase`，与 `projectenv.contained_path`
  同一形状），文件观察与本地模块共用；不用 `os.path.commonpath`——Windows 上跨盘抛 ValueError，一 `except … continue` 就把
  该记的模块吞了（`tests/test_input_observer_paths.py` 钉着 Windows 布局与跨盘）。
- **阶段轨迹（U09，ADR 0071）**：`engine/trace.py` 的 `Trace` 有界（64 条，丢中间留头尾）、阶段名闭集、`facts` 只收标量、
  第一次失败是根因；`PreparationResult.trace`（plan → check → spawn → execute → receipt）与 `ExportJob.trace`（prepare →
  source → compile → compose → raster → inspect → publish → report）同一个形状，随 `to_payload()` 走。作业进 `partial` / `failed`
  时 `failed_phase` 必须非空：图全好、只有报告坏了也要记 `report` 失败（`tests/test_export_pipeline.py` 的两条报告用例钉着）。
  不是全系统追踪平台。
- **DependencyIntent 是第二个读法，不是第二个安装器**：extras / marker / constraints / 冲突
  原样可见，看不懂的行 `kind=unknown` 保留原文（Poetry 的 `^` / `~` / 表值也是 unknown，不剥成
  任意版本）；安装路径的窄语法（ADR 0019 安全边界）一字不动。
- **case enrollment 台账**（`docs/implementation/tavotto-foundation/enrollment.json` +
  派生 md）：32 个 FO 场景逐一在台账里、与 registry 一致；enforced 的 case 必须指向真实
  pytest 用例（按 AST 找，不按子串）并写结果记录；预期实例集合在执行前生成；**空集合永远不是通过**。校验器在
  `tests/support/foundation_harness.py`，落点是 `invariants` job 的一步。
- **台账的 enforced 集合**：U01-S1 + U03 的 FO01 / FO02 / FO03 / FO07 / FO15 / FO19
  （`tests/test_foundation_first_open.py`，经真实 HTTP 入口，装置在 `tests/support/foundation_app.py`）；
  safe_stop 的 case 记录 `product_outcome=safe_stop` + `test_verdict=pass`，校验器按台账预期的结果
  对拍（`outcome_mismatch`）并分开计数，**不进自动兼容成功的分子**。
- **台账里 U09 的两条**：FO30 → enforced（registry 的 contractual 拆成三条 safe_stop 合同在 `tests/test_preparation_api.py`
  + 一条 guided 合同经真实入口 `tests/test_foundation_join.py::test_fo30_*`）；FO32 → observing（两个出口
  `existing_env_join` / `managed_env_join` 各挂一条具名任务：`foundation-u06-rendercore.yml` 的「U09 联调」步、
  `private-python-targets.yml` 的「U09 联调：managed_env_join」步；候选后端未切默认，所以不进 required）。
- 看护：`tests/test_execution_receipt.py`、`tests/test_preparation_api.py`、
  `tests/test_worker_runtime_report.py`、`tests/bridge/test_bridge_e2e.py`、
  `tests/test_foundation_harness.py`、`tests/test_foundation_first_open.py`、`tests/test_trace.py`、
  `tests/test_foundation_join.py`、`tests/test_export_identity.py`。

## 准备会话：script-first 的共同生命周期（T01，onboarding 收敛）

`engine/prepsession.py` 是 `preparation.py` 之上的**用户操作层**，不是新的任务系统：一个项目里一个目标（一份只有脚本的
`script` 目标，或一张已知素材）对应一个会话，会话里有多轮不可变计划与尝试。检查复用 `plan_for`，执行复用
`PreparationService.start` → `pool.build_owned`，登记复用 `probe.register_probed`；没有第二个 resolver / 安装器 / worker /
input 协议，前端也没有 readiness 计算器。端点 `POST /api/engine/preparation-sessions`（创建 / 复用检查会话）·
`GET …/<session_id>`（补拉，不重新执行）· `POST …/<session_id>/actions`（只认 `action_id` 与 `expected_config_revision`）。

**会话与导入即扫描（T02）的关系**：会话的「检查」要走 `plan_for`（T05 起默认不再有候选解释器体检与采用写配置：环境只给纯读建议，
但依赖门仍会为 `ready` 的计划体检候选环境，见 `dependency-repair-and-packages.md`），是用户选定目标之后的
明确动作；项目被认领 / 恢复时自动发生的是**只读零执行**的结构扫描（`engine/projscan.py`，规则全文在
`registry-discovery-and-probe.md`「导入即扫描」），它列出候选目标并给每个目标一份 `session_target`（= 本端点的创建请求体）。
扫描**不得**调用 `plan_for` / `decide_environment` / `gate` / `resolve_worker_python`；两者共用 `probe.inventory_entry` 的脚本分类，
不是两套关系判定器。

- **身份层次**：`session_id`（可恢复的用户体验）· `config_revision`（语义修订：目标 / 解释器 / 工作目录档 / 授权 / 数据绑定 /
  门的结论变了才 +1，判据是私有指纹，不对外）· `observation_seq`（读报告时可观察状态变了才推进；进度变化只动它，所以用户
  正在填的配置、手里的动作 id 不会每秒过期）· `attempt_id`（= 这次执行的 `PreparationPlan.plan_id`，旧 `plan_id` 语义不变）。
  `PreparationPlan.target ∈ {asset, script}`（缺省 asset）：`script` 目标没有 `asset_id` / `stem`，「成功」= 至少捕获到一张图。
- **phase 是纯派生**（`prepsession.derive(facts)`，黄金向量 `tests/golden/preparation_session_vectors.json`）：只吃检查 / 尝试 /
  失效 / 是否在等 `input()` 这几件已观察的事实。**`unknown` 不当通过**（有一项判不出就不是 `ready_to_run`，给 `recheck`）；
  provider 状态保留原权威，旧终局 `needs_input` 投影成 `awaiting_configuration` / `awaiting_confirmation`；失败事实永远单列在
  `outcome{kind,code,reason}`。「脚本跑完了但没有图」是 `partial` + `execution_finished_no_figure`，不算首图成功。
  `scanning`（T02）与 `preparing_environment`（T06）词汇已进闭集，本阶段不产生。前端只存这份投影，不另算。
- **检查不执行用户代码、不写用户项目**（`plan_for` 不跑脚本；e2e 用项目外的计数文件与目录树比对钉着）；执行只在认领后端生成的 `run`
  动作之后发生。注意 `plan_for` **不是**零副作用：它仍会做解释器体检与「记住」（`decide_environment` / `resolve_worker_python`，
  写的是 Tavotto 自己的数据目录配置）——这是 T00 登记的既有行为，T01 没有新增，由 T02（只读扫描）/ T05（推荐与采用分离）拆开；
  会话层不得借「检查」之名再加新的执行或对用户项目的写。动作是不透明 id，
  绑定会话 / `config_revision` / 影响摘要（`executes_user_script` / `writes_to_project` …），kind 闭集 `run` / `cancel` /
  `recheck` / `prepare_dependencies`（T06，授权一次依赖准备，绑定 `deprepair.impact_digest`，规则在
  `dependency-repair-and-packages.md`「授权影响摘要」），请求体除 `action_id` / `expected_config_revision` 外只多一个可选的
  `impact_digest`（回显用户看到的影响摘要），多别的字段就 400。已知素材的 provenance / frame / selected-source 拒绝逻辑在原路径里，原样生效。
- **check-use 窗口**：「比对修订 → `preparation.stale_reason`（授权 / 解释器 / 数据绑定）→ 认领 → 提交 provider」整段在会话锁内；
  失效就 409 `preparation_plan_stale`、一行不跑、会话标失效等 `recheck`；同一个动作重复认领（重复点击 / 两个标签页）回当初那次
  尝试（`claimed=false`，200），不重复起 worker；新的 `run` 动作（终局之后才有）才是用户明确的重跑，产生新的 attempt。
  池层 `build_owned` 的原子 `created` 仍是 worker 去重与取消所有权的唯一判据。
- **脚本目标执行成功之后**由 app 注入的 `finalize` 在执行线程、项目绑定里做 `probe.register_probed` + 物化 + 刷新（与
  `/api/registry/probe` 成功之后同一串）；登记没做完时会话仍是 `running`，所以读到 `completed` 就意味着编辑请求（
  `/api/engine/render`）已经找得到这张图。登记写 `tavotto_registry.json` 是执行**之后**、用户确认过的动作，列在 `run` 的
  `impact.writes_to_project`；扫描 / 检查永远不写。
- **入口循环**：`probe.entry_retry_allowed` 是「值不值得换入口」的唯一判据——缺参 / 要输入 / 读不到数据 / 缺包 / 超时 / 取消 /
  起会话前的两道门都**不**换（换了只是把顶层代码再跑一遍）；`script` 目标只选一个入口（显式 `entry` > 注册表 > 静态候选第一个）。
- **内存会话，如实失忆**：应用重启后旧 `session_id` 得 404 `preparation_session_not_found`（`params.reason=unknown_or_restarted`），
  客户端重新创建检查会话由当时的真实状态重判，**不自动重跑**。尝试的 `PreparationService` 记录过期 → `action_required` +
  `outcome.unknown/attempt_expired`。登记表有界（64 会话，闲置 30 min 回收）：有尝试在跑或登记没完成的会话**永不**被 TTL 回收，
  满了且全部活跃就 429 `preparation_sessions_full`，不驱逐。
- **取消**只经 `PreparationService.cancel`：本会话新起的会话才关，别人的不碰；关闭面板 / 切项目是展示层的事，不取消。
  依赖作业的取消同样只退役**自己拥有**的（认领了别人先起的同一份作业的会话没有 `cancel`）。**当场生效（T09，ADR 0116 §三）**：
  `pool.build_owned(on_acquired=…)` 在取到会话、执行之前把 `(worker, created)` 报给 `PreparationService.note_owner`（app 的
  `_preparation_runner` 接上）；运行中取消 → 本计划新建的会话 `force_cancel(expected_worker=…)` 只关这一条（build 以 WorkerError 回来、
  按取消收场，不等长计算 / input 自己结束）；共享会话的等待者 → 不碰会话，本计划直接以取消收场，执行线程迟到的结局不改写终局
  （`_finish` 对已终局的计划不写）；取消比「取到会话」还早到时，取到那一刻按同一规则处理。取消落地的尝试不再算「活跃」，会话可以马上重新检查。
  没报所有权的 runner（测试替身）仍走旧的「build 返回之后再收」。看护 `tests/test_preparation_session_lifecycle.py`、e2e `preparation-panel.spec.ts`。
- **成功的报告带 `captured`（T09）**：这次尝试捕获到的公开描述符（与 `/api/registry/probe` 响应里同一份：项目相对路径、运行配置只是不透明引用），
  只在 outcome 为 `succeeded` 时非空。界面的「进入编辑」直接用它，不按图名再找一遍、不为换界面再跑一次脚本。
- **`unlinked_stems`（T09b，T03 已知缺口的可恢复提示）**：无参数的执行按脚本整条替换注册表里的 stems（`discover.register` 的旧语义，
  注册表格式不动）；`probe.register_probed` 把替换之前登记在这个脚本名下、这次没产出的图名如实带回（带运行配置的执行是并入，不替换，
  没有这个键）。`/api/registry/probe` 响应与会话报告（outcome `succeeded` 时）同一口径：只是图名（与 `captured[].stem` 同口径），不含参数。
  界面据此说「此前带其他参数生成的 … 已不再关联，用原参数再运行一次即可恢复」。看护 `tests/test_probe_owner_and_unlinked_stems.py`、
  e2e `registry-center-preparation.spec.ts`。
- **依赖准备并入同一个会话（T06）**：phase `preparing_environment` 由依赖作业事实派生；装好后同一会话按新环境重新检查（只重算差额，
  报告多 `dependency_delta`），失败 / 取消保留原代并重新给新的授权动作；脚本跑到一半才发现缺包 = 新的一次尝试
  （outcome `needs_dependencies`，`rerun_required`），不叫"从异常点继续"。认领与失效检查在会话锁内。
- **SSE**：`preparation.session`（`pj` / `session_id` / `target` / `config_revision`）只是「重新读报告」的提示，不带 phase 与序号。
- 看护：`tests/test_preparation_session.py`（假 pool：合同、并发认领、修订、失效、取消所有权、回收、项目绑定）、
  `tests/test_preparation_session_dependencies.py`（T06：授权 / 认领 / 差额 / 真安装到首图）、
  `tests/test_preparation_session_e2e.py`（真 worker、真服务：只有脚本的项目 → 一次执行 → 进编辑请求不重跑）、
  `tests/test_script_probe.py::TestEntryLoopStopsOnNonEntryFailures`。**T09 / T09b 起它是 GUI 首跑的默认入口**（检查条「准备并运行」、
  素材库脚本行 ▶、接入中心逐行「试运行并连接」，前端规则在 `docs/rules/frontend/readiness-and-left-shell.md`「准备面板」，ADR 0116）；
  `/api/registry/probe` 留给本地开关关闭时的旧路径与 MCP / CLI（T10），是暂存的薄兼容 wrapper。它的取消（`/api/registry/probe/cancel`）
  **按 owner（T09b）**：试运行每次取到会话（`pool.build` 的 `before_build`）就把 `(worker, owned)` 记进 `app._PROBE_OWNERS`
  （`owned` = `pool.acquired_here`：本线程最近一次 `acquire()` 取到的就是它时那一次的 `created`），取消端点**先置标志、再读所有权**，
  只 `force_cancel(expected_worker=那一条)`（已不在池里就只杀那一条，绝不碰同键的替换者）；取到的是别人正在用的同键会话（`owned is False`）
  只停自己的等待意图、不杀；还没取到会话时只置标志，取到那一刻由 `probe` 按同一规则处理。看护 `tests/test_probe_owner_and_unlinked_stems.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 执行只走 `pool.build`
- 回执两半缺一半就是 `partial`
- 身份三分不混（私有键含路径、公开身份不含、文件 hash 单列）
- LaunchContext 是派生视图、四个来源各一个生产者
- 环境选择前移的落点是 `plan_for`（证据 / 作废 / 显式失效如实写）；ADR 0114 起解析解释器不再发现 / 体检 / 采用项目 venv，计划里多
  `environment.{generation, consent, recommendation}`（纯读建议），`discovery` 只在兼容开关 `TAVOTTO_ENV_ADOPTION=auto` 下有值
- 首开要问的是终局 `needs_input`
- 过期计划 `preparation_plan_stale` 不执行
- DependencyIntent 只读不装
- enrollment 台账空集合不是通过、safe_stop 的通过不进兼容成功分子
- 自报只收这一条会话的（`report_origin=build` + pid 对 `child_pid`，或 launcher 的子进程按 ppid 对 → `ok_via_launcher`；体检 / 探针冒充 = `runtime_rejected` + partial）
- 输入观察永远 partial、观察到的数据身份进公开身份
- `binding_for` 进计划，起会话前授权 / 解释器 / 绑定三比不一致即 `preparation_plan_stale` + reason
- 复用热态会话时不一致是明示旧快照
- `Trace` 有界、第一次失败是根因

## 验证（2026-09-25 迁自 `src/tavotto/AGENTS.md`「验证」，#608）

速查表那一节只留一行指向这里；下面是原文，一字未改。

- 改了回执 / 准备 / 数据绑定 / 轨迹 / manifest 身份（U09）：跑 `tests/test_execution_receipt.py test_preparation_api.py
  test_worker_runtime_report.py test_trace.py test_export_identity.py test_foundation_join.py`；FO32 两个出口在候选 venv 里带
  `TAVOTTO_FOUNDATION_PROJECT_PYTHON`（另一 minor + matplotlib + h5py）/ `TAVOTTO_PRIVATE_PYTHON_REAL=1` + `_CACHE` + `_WHEELHOUSE` 真跑；skip 不是绿。

## 运行参数在计划 / 会话里（T03）

`PreparationPlan.run`（`execspec.RunSelection`）是**私有**字段，`to_payload()` 只多 `run_config`（本机不透明引用）与 `argv_count`；
`launch_context` 同样只带个数与引用。会话的 `target_key` 含引用（同脚本不同 argv = 不同会话），`_fingerprint` 含引用（换任一 token
= 新 `config_revision`），`impact.script_arguments` 只给个数。请求体：`{script, entry?, argv?, argv_sensitive?}`，已知素材（`id`）
不接受另给 argv（它的配置冻结在资产 id 里）。执行线程取消 / 复用 / 回执都带 `run`，所以取消 A 配置不会杀 B 配置的会话。

## 参数、数据与输出在同一个会话里（T07）

- **参数**：脚本有 argparse 字面量证据（`scriptargs.analyze_file`，只读源码、按 mtime 缓存）时才多一项检查 `arguments`
  （**永远 `ok`**，`detail` 只有计数：`schema` / `arguments` / `required` / `output_files` / `form_enabled`）和一项
  `requirements[kind=script_arguments, blocking=False]`（`payload.schema` + 本次配置的 `argv_count` / `run_config`）。它是表单建议，
  不改 phase、不撤 `run`：静态看缺必填照样能跑（真 parser 说缺参仍是既有的 `script_needs_arguments`），识别不全标 `partial`。
  没有 argparse 证据的脚本报告形状与 T06 一字不差；已知素材目标（`asset`）不提议表单。
- **数据缺失**：执行线程把 `WorkerError.missing_input`（ADR 0106 的指认载荷）存进 `PreparationResult.missing_input`（**不进**
  `to_payload()`、回执与诊断快照）；当前修订的尝试以错误收场且带它时，报告多 `requirements[kind=input_location, origin=last_attempt,
  blocking=False]`。回答走既有 `POST /api/engine/input-remap`（用户亲手指认，同名不同内容不就近猜），之后 `recheck`：
  `_fingerprint` 含 `inputremap.generation`，改指表变了 = 新 `config_revision`，旧失败不再是当前的。
- **输出参数**（P03）：schema 里 `role=output_file`（只来自 `FileType('w'|'a'|'x'|…)`）的个数进 `run` 动作的
  `impact.script_writes = {declared_output_arguments, cwd_mode}`（不含参数名与路径）。Tavotto 从不替用户加 overwrite / force 一类 token。
- 看护：`tests/test_script_args_session.py`（假 pool）、`tests/test_script_args_e2e.py`（真 worker：A01 表单路径 = 原始 token 路径、
  A05 输出参数只执行一次不覆盖、数据指认后同一会话出图）。

## 终局诊断快照（T04）

`PreparationService._finish` 在每个终局（`ready` / `error` / `cancelled`；`needs_input` 在执行线程里同走 `_finish`）之后调
`preparation._record_terminal`，把 `diagnostic_projection(plan, result)` 冻结进 `taskdiag.STORE`（`kind=preparation`，
id = `plan_id` = 会话报告的 `provider.attempt_id`）。白名单：目标类别、argv 个数与 `rc_…` 引用、计划那一刻的环境来源 / 版本、
阶段轨迹（不含 `facts`）、回执的控制面 / 来源 / 完整度、`error.code` + 闭集 `reason`、取消事实、起止时间；**不含**脚本路径、
入口名、解释器与项目路径、`error.message`、`note`、`required_input` 内容。完整规则见 `diagnostics.md`「任务绑定诊断」。
