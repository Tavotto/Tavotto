# U01 · 共同合同与增量测试骨架 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U01.contracts + U01.harness（两个 milestone，一个 PR）。架构决策：
[`docs/adr/0053-foundation-contracts-and-preparation.md`](../../../adr/0053-foundation-contracts-and-preparation.md)。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `4a68dac7`（`foundation/u00-baseline` 的 head，
即 U00 的 PR #446；`origin/main` 当时 `4cf5b8c6`）；结束 = 本 PR 的 head（合并后以 `git log origin/main`
里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u01` 里做，用户主工作区一个字节没碰。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 后端、safe worker、旧依赖解析、
解释器链）；候选路径**无**；拟启用能力**无**（`plan.json` `new_default_capabilities_enabled: []`）。
新增的全部是**合同与只读投影**：LaunchContext 从 spec 派生、DependencyIntent 只读、回执只报告、
准备接口只走既有 `pool.build`。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`.github/AGENTS.md`；
`docs/rules/backend/` 的 execution-entries / figure-capture-and-execution / worker-protocol-and-lifecycle /
process-boundaries / export-pipeline / pdf-backend-boundary / project-system / dependency-repair-and-packages /
profiles-and-preflight / session-auth / tavotto-run-control-plane / writeback-transaction；
`docs/rules/repo/same-origin-pairs.md`、`predicate-subject.md`；`docs/rules/ci/verification-chain.md`、
`ci-lanes.md`、`gate-discipline.md`；ADR 0003 / 0008 / 0014 / 0019 / 0021 / 0031 / 0044 / 0047 / 0051。
复用（权威不动）：`execspec.safe_spec / worker_argv`（golden 未变）、`pool.build / resolve_worker_python /
force_cancel`、`workdir.mode_for`、`depresolve.parse_requirement`（窄语法安全边界未变）、
`exportreq.normalize`、`exportjob`、`security` 的全局 guard、`scripts/smoke_app.adopt_session_credentials`
（凭据装载唯一实现）。

**实际代码与 API / 数据结构变更**（细节见 ADR 0053 §一–§五）：

| 层 | 变更 |
|---|---|
| `engine/execspec.py` | `CWD_ORIGINS` / `WRITE_MODES` / `launch_context(spec, grant=)`——派生视图；`ExecutionSpec`、`STABLE_FIELDS`、`worker_argv` 一字未动 |
| `engine/workdir.py` | `set_mode(project)` 记 `granted_at`（再设不刷新，切回沙盒即撤销）；`grant_for()`；`state()` 多 `grant` 段（前端类型无需改：加字段） |
| `engine/depresolve.py` | `DependencyIntent` / `parse_intent` / `parse_intents_text` / `declared_intents` / `conflicts`（读 `constraints.txt` 与 pyproject 分组）；旧安装路径解析器未动 |
| `engine/figsession.py` | `runtime_report()` + `RECEIPT_PACKAGES`（闭集）；worker / bridge 的 v1 `build` 响应新增 `runtime`（加字段不升版，legacy 形状不变） |
| `engine/figcapture.py` | `SourceArtifact` / `hash_file` / `source_artifact_from_file`（static 来源不许带执行身份；零字节不是产物） |
| `engine/receipt.py`（新） | `ExecutionReceipt`（`public_identity` / `private_invalidation_key` / `receipt_id` / `completeness`）、`from_worker`、`source_artifact_for` |
| `engine/exportreq.py` | `render_plan_ref(req, resources)`：只做引用与身份 |
| `engine/preparation.py`（新） | `PreparationPlan` / `PreparationResult` / `PreparationService`（状态闭集、取消边界、按项目认领） |
| `engine/pool.py` | `peek()`、`acquire()`（`get()` + 池锁里的 `created`）、`build_owned()`、`control_plane_of()`、`last_build_runtime`（两种 worker）；**`WorkerdWorker.spec` 补 `cwd_mode`**（此前属性与真实 spawn 不一致，见「发现的产品事实」） |
| `engine/nativesession.py` / `enginesession.py` | `last_build_runtime` 只读投影；`WORKER_LIKE` 多一个成员 |
| `app.py` | `POST /api/engine/preparation`、`GET /api/engine/preparation/<id>`、`POST …/cancel`（会话认证之内）；错误码 `preparation_not_found`（两种语言文案 + `resources.d.ts` 重生成） |
| `tests/support/foundation_harness.py`（新） | 台账校验 / 预期实例集合 / `ResultRecord` / 闭集校验 / JSON + JUnit + 摘要 / CLI |
| `docs/implementation/tavotto-foundation/enrollment.json` + `ENROLLMENT.md` + `tools/generate_enrollment.py` | case enrollment 台账（真值 + 派生） |
| `.github/workflows/ci.yml` | `invariants` job 三步：预期集合 → enforced 用例 → 闭集校验（`if: always()`，退出码即结论）+ 证据上传 |
| 文档 | ADR 0053、`docs/rules/backend/preparation-and-receipts.md`、`src/tavotto/AGENTS.md` 一行、本文件、`evidence/u01/` |

**关联旧要求 ID / 场景 ID**：CP01（FO-005…011 的合同层：只读清点不执行脚本 = `plan_for` 不起子进程；
两个项目异步状态不串用 = 按 `project_id` 认领；取消不破坏别的消费者 = 取消边界；静态源不被阻断 =
`static_source_available`）、CP07 / CP08 / CP08-A（harness、enrollment、闭集校验）、R13（RC-091 /
092 / 098：既有三个 Gate 未动；空 case / 全 skip / 错 SHA / 旧报告不能绿——校验器负例）。
FO-006（信任范围）、FO-007（过期 plan / 撤销 grant 不执行）、FO-011（probe 预算可取消）**本阶段没有
产品实现**，仍 `planned`；registry 220 条 `execution_status` 一条没动。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `PYTHONPATH=$WT/src pytest tests/test_execution_receipt.py tests/test_preparation_api.py tests/test_worker_runtime_report.py tests/test_foundation_harness.py tests/bridge/test_bridge_e2e.py` | 同上；应用 `.venv` 3.13.11；服务子进程选中 `system`（Homebrew 3.13.11 / mpl 3.10.8） | 0 | 45 + 10 + 2 + 14 + 5 通过；`U01-S1` 28 s |
| `TAVOTTO_FOUNDATION_RESULTS=<dir> pytest -k u01_s1` → `foundation_harness.py expected --lane pr` → `validate` | 同上 | 0 / 0 / 0 | 预期 1 · 提交 1 · 有效 1；planned 32 单列；摘要与记录见 [`../evidence/u01/`](../evidence/u01/) |
| 既有针对性：`test_execspec` / `test_workdir_mode` / `test_error_codes` / `test_import_architecture` / `native/test_native_api` / `test_browser_auth` / `test_dependency_repair` / `test_worker_protocol` / `test_merge_queue_workflows` / `test_source_hygiene` / `test_agents_rules_index` / `test_docs_references` | 同上 | 0 | 344 + 99 + … 通过（`test_browser_auth` 的 url_map 枚举自动覆盖三个新端点） |
| `cd web && pnpm i18n:check` | 同上 | 0 | 重生成 `resources.d.ts` 后通过 |
| `actionlint .github/workflows/ci.yml` | 同上 | 0 | — |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文（提交前跑，退出码与红项逐条写在 PR 里） |
| 变异反证（20 条） | 同上 | 每条非零 | 见 PR 正文「反证」；`bool(want)` 那一半是有意冗余，源码注明 |
| 内置 runtime、e2e、lab、Windows / Linux | — | — | **not_run**（本机只有 macOS；CI 上 `invariants` job 是 ubuntu，合并后第一个 run 才有 Linux 证据） |

**本切片的正例、负例、旧行为回归**：正例 = `U01-S1`（401 默认拒绝 → 凭据交接 → 准备 `ready` + 完整回执
→ 二开报 `existing_runtime` 不新起 → 渲染 ylim 由 truth 推出 → 带 override 的旧后端导出 PDF/PNG，PDF 文字层
含改过的字、PNG 尺寸按 IHDR）；负例 = 校验器的错项目 / 错 generation / 空报告 / 重复 ID / 错 SHA / 非 pass
verdict / 空预期集合各自红，台账漂移 / 缺场景 / 重复 / 指向不存在的用例各自红，准备接口的项目串用 404、
取消只碰自己起的会话、静态素材不起子进程；旧行为回归 = `ExecutionSpec` golden、legacy 信封形状、安装路径
窄语法、默认后端与解释器链全部未动。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**新建台账**。32 个 FO
场景逐一登记（31 `planned` + FO14 `later`），与 registry 一致（门禁钉着）；新增 **`U01-S1` enforced**
（lane `pr`，落点 `invariants` job）——它是 U01 切片自己的真链路，**不是** FO01 / FO32 的资格
（那两条各自在 U03 / U09 取得）。没有 `observing` 的 case；没有新建需要完整安装 VM 的 job。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：内置 runtime；Playwright e2e；lab / Windows / Linux 任何腿；registry 220 条产品实例；
  FO-006 / FO-007 / FO-011 的产品实现（本阶段只有合同层）。
* 基础设施问题：**issue #452**（area:ci-harness / severity:P2）——本机全量三条红不归本 PR：
  `test_mcp_normalize` 的两条 zero-edit 像素对比（模块级 `WORKER_PY` 在 conftest 隔离前读到真实
  配置里的 venv，`test_bootstrap` 等 `reset_worker_python()` 之后产品在隔离配置下重选中另一档
  matplotlib，两档像素差 11% / 3%；干净 `origin/main` 同进程复现、数字逐位相同）与
  `test_run_messages_only_stderr`（判据被解释器路径里的 `Tavotto` 子串咬到）。不改 aggregate_gate、
  不加豁免；出口在 issue 里。
* 真正产品失败：无新增。顺带发现的**产品事实**（不是本阶段修的缺陷，已在代码里改正或记录）：
  1. `WorkerdWorker.spec` 属性此前**没带 `cwd_mode`**，而真实 spawn（`_spawn_spec`）带——project 模式下
     从属性读 LaunchContext 会报成沙盒。已补（一行），`test_workerd_pool` 的 argv 对拍未变。
  2. `single_file_csv` 夹具（figsize 3.2×2.4）的 **xlabel 落在图幅之外**（manifest bbox y ≈ 1.012），原生
     `figure.pdf` 的文字层里也没有它——夹具性质，预检 `element-outside-figure` 会说；U01-S1 因此改 ylabel。
     U03 若拿这份夹具做 FO01，别把「xlabel 不在文字层」误判成产品缺陷。
  3. 本机用户配置里 `worker_python` 指向另一个会话的 scratchpad venv（`configured`），pytest 进程内
     `pool.find_worker_python()` 在 conftest 隔离 `TAVOTTO_CONFIG_DIR` 之前就读到它；而 U01-S1 起的服务
     子进程用隔离配置，选中 `system`。两者可以是不同的解释器，回执记的是**服务真的用了哪个**。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动；导出终点仍是 PyMuPDF。`git diff --stat`
只含上表的文件；`LICENSE`、ruleset、`aggregate_gate.py`、默认后端、生产依赖、`security._PUBLIC_PATHS`
一个都没动。

**评审轮次与处置**：Codex 第一轮 2×P1 + 2×P2（`69657e2b`）——所有权改由池原子给出、已 build 的会话
不再碰 runner（真 worker 用脚本副作用计数证明不重跑）、语义身份改吃回执公开身份 `receipt_identity`、
`conflicts()` 纳入 constraints；四条线程修 + 先红后绿 + 变异红 + resolve。CodeQL 三条路径告警：改为传
`safe_resolve` 校验过的绝对路径，处置结果以 PR 上的最终状态为准。
第二轮（Codex 评 #455 的旧 diff 落在本 PR 文件上，转办，`1d5b21fe`）：P1 harness 用例存在性改按 AST 判
（注释 / 嵌套函数 / 类名不算）；P2 Poetry `^` / `~` / 表值 → unknown + raw 原文；P2 计划公开投影去掉
机器绝对路径（项目外解释器只给来源标签与版本；错误分支 `project_env` 只留四个公开键）。CodeQL #140–#142
在新分析里 fixed（改传 `safe_resolve` 校验过的路径即可，未 dismiss）。Windows 片两条红（空 env 下
`Path.home()`、工具打中文到 cp1252 管道）已修并加看护 `test_foundation_pack_tools_reconfigure_stdout_to_utf8`。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改产品源码、协议加字段、新端点、CI 拓扑）
→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；变异反证逐条红；合同测试覆盖新 CI 步骤；
新端点在 url_map 枚举的认证门禁之内。

**仍缺哪些默认启用 / 精确安装物资格**：全部。本阶段不产生任何产品资格；`U01-S1` 的 pass 是
「源码树 + 旧后端 + 一个平台」的切片证据。

**下一个无阻塞阶段 / 子切片**：U02（两个 spike，依赖 U01.contracts）与 U03（依赖 U01.contracts +
U01.harness）可并行起步。U03 的输入：

* 准备接口的 `plan.environment` / `dependency_intents` / `launch_context` 就是 FO01–FO19 要核对的事实面；
  `existing_runtime` / `created_runtime` 是 FO31 的形状；
* 新 enforced case 的加法：台账加一条 `enforced` + 指向真实用例 + 用例用 `fh.ResultRecord` 写记录；
  lane 不是 `pr` 的要在对应 job 里加同样的三步（预期集合 → 用例 → 校验），别复用 `invariants` 那一份；
* `project.root` 这一档 cwd 来源今天没有生产者（用例钉着），U03 加「在项目根运行」时先改那条用例；
* FO19（用户 `manifest.py` 被引擎遮蔽）U00 已开 issue，归 U03；
* U04 的 DependencyIntent：marker 求值、extras / constraints 怎样进联合安装——`declared_intents` 已把原样
  给全，安装语法不放宽。

**回退方式、不能假装可回滚的外部副作用**：revert 本 PR 即回退：合同是派生视图与新模块，协议只加字段，
端点删掉即无；`workdir` 设置里多出的 `granted_at` 键旧代码读得懂（`mode_for` 只看 `mode`）；台账与
`invariants` 的三步随 revert 消失。没有发布、没有设置写入、没有外部副作用。
