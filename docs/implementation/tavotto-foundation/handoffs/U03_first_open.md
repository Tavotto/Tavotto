# U03 · 已有环境与数据上下文的首开闭环 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U03.existing_open。两个叠栈 PR：**A** 后端编排 + 场景用例 + 台账（本文件随 A 落地）；
**B** 确认交互（前端对话框 + i18n + MCP 投影 + 设置里的三档控件）。架构决策：
[`docs/adr/0057-first-open-environment-and-workdir.md`](../../../adr/0057-first-open-environment-and-workdir.md)。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `5b5812bf`（`foundation/u01-contracts` 评审后的
head，即 U01 的 PR #451；`origin/main` 当时 `89a83726`）；结束 = PR A 的 head（合并后以
`git log origin/main` 里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u03` 里做，用户主工作区
一个字节没碰。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认后端不变（PyMuPDF 终点）、safe worker 不变、守卫不变、
会话认证不变。**默认路径上变了的行为**（都是 ADR 0057 记的决定，不是候选）：项目 venv 在首次科学执行前
被发现 + 体检 + 采用；失效的显式解释器选择停下来说原因而不是静默换；没决定过 cwd 的项目在静态证据
要求时先问一次；safe worker 的引擎模块进私有包。拟启用的**新能力**：无（`plan.json`
`new_default_capabilities_enabled: []` 不变）；新包下载不实现、不作卡点。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、
`codex-plugin/AGENTS.md`；`docs/rules/backend/` 的 execution-entries / project-system /
registry-discovery-and-probe / profiles-and-preflight / dependency-repair-and-packages /
worker-protocol-and-lifecycle / figure-capture-and-execution / process-boundaries / session-auth /
tavotto-run-control-plane / preparation-and-receipts / ai-agent-bridge；`docs/rules/repo/`
same-origin-pairs / predicate-subject；ADR 0008 / 0018 / 0020 / 0021 / 0044 / 0047 / 0053。
复用（权威不动）：`pool.build / build_owned / acquire`（执行只有一条路）、`projectenv.discover /
probe_environment / remember`（发现与体检的实现）、`workdir.set_mode / grant_for`（记账）、
`bridgeboot.load_engine_modules`（私有包装载器，safe worker 直接用 native 那一份）、
`execspec.safe_spec / worker_argv`（第三档只多 `--cwd`）、`discover._Analyzer`（stem / entry 算法
在目标解释器里跑的还是它）、`scripts/smoke_app.adopt_session_credentials`。

**实际代码与 API / 数据结构变更**（细节见 ADR 0057）：

| 层 | 变更 |
|---|---|
| `engine/worker.py` | 摘掉 `sys.path[0]` 的 engine 目录、按文件路径装 `bridgeboot`、`_ENGINE_MODULES` 一次装进 `tavotto_bridge_runtime.*`；`figcapture / figsession / wireproto` 从私有包取（#447 / FO19） |
| `engine/pool.py` | `resolve_worker_python(figures_dir, *, script=None, discover=True)`：第 4 条「项目 venv 首开发现」；`EXPLICIT_UNUSABLE_CODE` / `PROJECT_PYTHON_UNUSABLE_CODE`（异常挂 `explicit`）；自动记住的失效 → `_invalidate_remembered` + `invalidated_decision()`；`first_open_outcome()`；`_new_worker()` 起会话前过 `workdir.resolve_mode` 那道门（异常挂 `confirmation`） |
| `engine/projectenv.py` | `remembered_record()`（文件不在也回记录）、`first_open_candidate()`（按项目缓存）、`TRIGGER_FIRST_OPEN`、`remember_default()` / `uses_default_chain()`（`mode == "default"`：用户明确选默认链条，首开不再盖回去） |
| `engine/execspec.py` | `CWD_PROJECT_ROOT = "project_root"`（第三档；`project` 仍是脚本目录）；`safe_spec` / `launch_context` / `worker_argv` 各多一分支；四个 `CWD_ORIGINS` 各有一个生产者 |
| `engine/workdir.py` | 三档；「没决定」/「决定沙盒」/「授权」三个答案（`decided_at` / `granted_at`）；`grant_for` 多 `cwd_write.mode` + `decided`；`decision_for` / `resolve_mode` / `confirmation_payload` / `ConfirmationRequired`；`ERROR_CONFIRMATION_REQUIRED` 进 `ERROR_CODES`；`forget()` |
| `engine/databinding.py`（新） | 相对数据路径字面量 × {脚本目录, 项目根} 的静态证据；五档结论；`SAVE_FUNCS` 镜像 `discover` 的（用例钉相等） |
| `engine/preparation.py` | 状态 `needs_input`（终局）+ `required_input`；`ERROR_PLAN_STALE`；计划多 `workdir_decision` / `required_input`；`environment` 多 `evidence` / `discovery` / `invalidated` / `error.explicit`；执行前比对 grant；runner 抛确认落 `needs_input` |
| `engine/discover.py` | `read_source` / `parse_source` / `inspect_script` / `analyze_in_interpreter` / `reset_target_cache`；`discover()` 多 `problems`；`discover / build_draft / merge / analyze_script` 多 `target_python=` |
| `engine/probe.py` | `script_inventory` 每条多 `problem` / `parser`（`reason` 闭集不变），目标解析器取 `resolve_worker_python(discover=False)` |
| `engine/project_refresh.py` | 静态 merge 带目标解析器（`_target_parser`） |
| `app.py` | `_worker_error_payload` 多 `confirmation` / `explicit`（状态码仍 500，三条门禁钉着字面量）；`/api/engine/environment` 的 `project` 多 `resolution_error`，读决策用 `discover=False`；清掉项目环境 = `remember_default` + 关会话；`PATCH /api/engine/workdir` 自然接受第三档 |
| `tests/support/foundation_harness.py` | 预期实例带 `expected_product_outcome`；记录的 `product_outcome` 必须等于它（`outcome_mismatch`）；`passed_by_outcome` / `compatibility_successes`（safe_stop 的通过不计入）；`ResultRecord` 允许 `pass + safe_stop` |
| `tests/support/foundation_app.py`（新） | 真实 HTTP 服务装置（摘掉 `TAVOTTO_WORKER_PYTHON` / `MM_WORKER_PYTHON`、401 先验、凭据交接） |
| `tests/fixtures/foundation/shadowed_engine_modules/`（新） | 夹具 ⑦：用户的 `manifest.py` / `overrides.py` + `lab_utils/` |
| 台账 / registry | FO01 / FO02 / FO03 / FO07 / FO15 / FO19 → `enforced`（lane pr，registry 各带 `promotion` 合同）；FO11 / FO12 / FO16 / FO17 → `observing`；`capability_version: u03`；FO02 lane 由 integration 改 pr |
| 守卫 | `tests/test_runtime_build.py` 的 spec 闭包从两个根的装载清单反推；`tests/test_import_architecture.py` 要求 `_ENGINE_MODULES` 逐条登记成 worker 的 `extra_edges`（baseline 加 17 条边） |
| 文档 | ADR 0057；`docs/rules/backend/` 的 preparation-and-receipts / execution-entries / figure-capture-and-execution / registry-discovery-and-probe / process-boundaries；`src/tavotto/AGENTS.md` 五行；本文件；`evidence/u03/` |

**关联旧要求 ID / 场景 ID**：CP02（FO-012 跨 minor 选择的**机制**在、真实不同 minor 的证据 observing；
FO-013 显式选择不被覆盖；FO-014 声明冲突不静默放宽——`depresolve.conflicts` 原样可见、不装（U04）；
FO-015 同 base 不同 venv；FO-016 / FO-017 静态库存不丢合法语法、非 UTF-8 与语法错误分开；FO-018 目标
解析器只分析；FO-020 不支持的 Python 不谎报（`support_status` 既有 + 显式失效停止）；FO-021 venv 重建
使记住的决策失效并作废）、CP05（FO-041 三分各有生产者；FO-042 旧 project 语义不变；FO-043 `__file__` /
相对 import 不被复制改变——FO03 / FO19；FO-044 绝对路径不重映射；FO-045 歧义数据需确认且值等于选的
——FO07；FO-046 h5py **未做**（U04 后）；FO-047 真实 cwd 写入许可执行前授予；FO-048 拆分：默认沙盒的
既有守卫 + 授权后的相对写入落项目目录（既有用例）；FO-049 只扫描项目根内、可取消——**probe 预算未加**；
FO-050 数据变更不造成旧计划假成功——过期计划不执行覆盖了授权那一维，数据修订那一维仍归 FO30）、
CP06（FO-051 已授权齐备的项目不问不闪红——FO01 / FO03；FO-052 准备授权与改环境动作分开——确认框只
选 cwd，装包是别的动作；FO-053 数据未知时按默认走并保留既有失败出口；FO-054 `ready_editable` 需实际图
与一次真实编辑——U01-S1 已是带 override 的导出，U03 场景核图内值；FO-055 artifact-only 不冒充本次脚本
成功——`static_source_available` 既有；FO-056 native 不改——一字未动；FO-057 MCP / 桌面准备状态与
权限同源——门在 `pool._new_worker`，MCP 的 `session.acquire()` 走同一处，**结构化投影在 PR B**；
FO-058 断线 / 切项目 / 中英文——PR B）、CP08 / CP08-B（enforced 六条 + 校验器按预期结果对拍）、
R12（旧后端终点、backend 标 `pymupdf`）。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿（439 files） |
| `PYTHONPATH=$WT/src pytest tests/support/foundation_harness.py expected --lane pr` → `pytest tests/test_foundation_harness.py tests/test_foundation_first_open.py tests/test_execution_receipt.py tests/test_preparation_api.py tests/test_worker_runtime_report.py` → `validate` | 同上；应用 `.venv` 3.13.11；服务子进程选中 `system`（Homebrew 3.13.11 / mpl 3.10.8） | 0 / 0 / 0 | 预期 7 · 提交 7 · 有效 7；`passed_by_outcome {automatic: 4, guided: 2, safe_stop: 1}`，`compatibility_successes 6`；见 [`../evidence/u03/`](../evidence/u03/) |
| `pytest tests/test_first_open_environment.py` | 同上；真实 venv（`support.venvfixture`）与真实 bare venv | 0 | 10 通过 + 1 skip（FO11 需 `TAVOTTO_FOUNDATION_ALT_PYTHON`） |
| `pytest tests/test_first_open_workdir.py tests/test_databinding.py tests/test_discover_problems.py tests/test_preparation_api.py` | 同上；`test_discover_problems` 的 FO12 机制用例用本机 `python3.14`（t-string） | 0 | 14 + 32 + 16 + 17 通过 |
| 既有针对性：`test_worker_roundtrip` / `test_workdir_mode` / `test_worker_runtime_report` / `test_compat_capture_parity` / `test_script_probe` / `test_project_env` / `test_dependency_repair` / `test_dependency_repair_e2e` / `test_bundled_runtime` / `test_discover` / `test_project_readiness` / `test_project_refresh` / `test_registry` / `test_import_architecture` / `test_runtime_build` / `tests/bridge/test_bridge_namespace` / `test_error_codes` / `test_i18n_dead_keys` / `test_agents_rules_index` / `test_docs_references` / `test_foundation_fixtures` / `test_foundation_plan_integrity` | 同上 | 0 | 通过（改法见「旧行为回归」） |
| 变异反证（18 条） | 同上 | 每条非零 | 见 PR 正文「反证」；`decode_as_syntax` 第一次落在没被用例走到的分支上（两条解码路各加一条用例后两条都红） |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文（红项逐条归因） |
| 内置 runtime、e2e、lab、Windows / Linux、`pnpm test/build`（PR A 不碰 web） | — | — | **not_run**（本机只有 macOS；CI 上 `invariants` job 是 ubuntu） |

**本切片的正例、负例、旧行为回归**：正例 = 六条场景（FO01 沙盒默认 + 只读回退 → 值 = 真值；FO02 问一次
→ 项目根 → 值 = 真值、二开不问；FO03 `__file__` + 本地包不问不装；FO07 问一次、选脚本目录得 [7,13,25]、
选项目根得干扰 [601,1201,2401]；FO15 显式 bare venv → `explicit_python_unusable` 三处一致、零副作用；
FO19 三个 sentinel 齐全）+ 机制用例（首开采用 venv 且 generation 1；FO17 两 venv prefix / 私有键不同；
中文空格路径的项目根运行 + 守卫仍在）。负例 = 校验器的 `outcome_mismatch`（跑成别的场景不算通过）、
`explicit_python_unusable` 的三种（env / configured 存在但坏、configured 不存在）、`project_python_unusable`
两种、自动作废有记录、默认链条不被盖回、门在 spawn 前且零进程、过期计划零执行、`needs_input` 不起线程、
databinding 的「不搜同名」「绝对路径不碰」「目录不算文件」「出了项目根不碰（`../../` 逃逸 / 软链接出界）」、目标解析器不执行用户脚本、同解释器不问两次。
旧行为回归 = `ExecutionSpec` golden 与默认 argv 逐字节不变；`project` 档 = 脚本目录；守卫 / savefig /
写回 / 会话认证 / 默认后端一字未动；六条既有用例按新事实改（见下）。

**改了哪些既有用例、为什么**（都是实现细节的断言，用户合同不变）：`test_project_env` 六条（首开就选
venv，`trigger` 由 `missing_dependency` 变 `first_open`；「切回默认」改走 `remember_default`；识别旧会话的
用例先起会话再建 venv）；`test_dependency_repair_e2e::test_the_old_worker_is_gone…`（旧会话与安装目标是同
一个解释器）；`test_execution_receipt::test_project_root_origin_has_no_producer_today` → 每个来源各一个
生产者；`TestGrant` 两条与 `test_workdir_mode` 一条（grant 多键、沙盒记决定）；`test_foundation_harness`
的 enforced 集合 / 计数 / 漂移用例（FO01 → FO04）；三个 `resolve_worker_python` / `merge` 的替身补 `**kw`。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：FO01 / FO02 / FO03 /
FO07 / FO15 / FO19 **planned → enforced**（lane pr；每条：真实正例 + 校验器负例 + 至少一条变异指名；
预算每条 6–15 s；registry 各带 `promotion` 合同）。FO11 / FO12 / FO16 / FO17 **planned → observing**
（机制已实现且有用例，真实资格缺的是「另一个 minor 且装了 matplotlib」/「支持矩阵外的 Python」这类
本机与 invariants job 都没有的解释器——FO11 的用例读 `TAVOTTO_FOUNDATION_ALT_PYTHON`，没有就 skip 并说明；
skip 不是绿，所以不 enforced）。FO04 / 05 / 06 / 08 / 09 / 10 仍 planned（各自的 notes 写了 U03 做到哪）。
FO02 的 lane 由 integration 改 pr：整条链 ~10 s，走 `invariants` job；`03 §5` 的「常驻 6–8 条」现在是 7 条。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行：内置 runtime；Playwright e2e；lab / Windows / Linux 腿；FO11 / FO16 的真实解释器场景；
  FO05（h5py）；FO32（RenderCore 终点，U09）。
* 基础设施问题：本机用户配置里 `worker_python` 指向别的会话的 scratchpad venv，路径串含 `Tavotto`——
  `tests/native/test_run_cli_integration.py::test_run_messages_only_stderr` 的 `"Tavotto" not in out` 在这台
  机器上必红（`nativekit.USER_PYTHON` 从进程内 `find_worker_python()` 取，隔离前读到真实配置）、
  `test_ctrl_c_reaches_the_script…` 90 s 超时；与本阶段改动无关，U01 handoff 已记同源事实。
  `tests/test_asset_library.py::TestCancelSemantics::test_cancelled_worker_failure_is_not_misreported` 在
  U01 head `5b5812bf` 上已红（替身只 patch 了 `pool.get`，而 #451 评审后 `build_owned` 走 `acquire`），
  已告知 U01 修；它留下的活 worker 还会让 `test_open_script_route::test_cli_probe_executes_once…` 的
  「池为空」假红。
* 真正产品失败：无新增。顺带发现的**产品事实**：
  1. `scripts/` 目录里的 PDF 不算素材（`project_refresh.EXCLUDE_DIRS` 既有规则）——FO03 变体的原生参考要
     站在项目根跑，产物才在库存里；
  2. 夹具 `split_scripts_data/scripts/entry.py` 的输出名来自 `--out` 参数 → 静态扫描 `dynamic_names`
     → 面板 `needs_probe`，不是 `editable`；FO03 用常量输出名的变体脚本；
  3. `databinding` 只认单个字符串常量：`os.path.join("data", "x.csv")` 不算（verdict `unknown` → 默认）——
     ADR 0057「不做的事」记了；
  4. 变异反证遇到同秒同长度改动时旧 `.pyc` 会掩盖变异（`decode_as_syntax_head` 脚本里 rc=0、手工 rc=1），
     mutate 脚本要清 `__pycache__`。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动；导出终点仍是 PyMuPDF。`LICENSE`、
ruleset、`aggregate_gate.py`、默认后端、生产依赖、`security._PUBLIC_PATHS`、worker 守卫、`Path.unlink`
守卫、写回事务一个都没动。用户脚本一个字节没改（FO02 用例钉了 sha）。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改产品源码、准备协议加状态、解释器链、worker
装载形态、CI 校验器判据）→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；harness 三步 0；
变异反证逐条红；新 code 都有后端回退文案；三条状态码门禁仍钉 500。

**仍缺哪些默认启用 / 精确安装物资格**：全部。六条 enforced 的 pass 是「源码树 + 旧后端 + 一个平台」的
切片证据（`product_validation_status` 仍 `not_run`：registry 220 条产品实例一条没动）。

**叠到 U01 ac217a27 之后**：environment 投影按 ADR 0053 摊平（`python_version / matplotlib_version / support`
顶层，项目外解释器路径一律 None），三支首开投影（needs_input / 显式失效 / 发现与作废）各有无机器路径
用例；ci.yml harness 步补上 `tests/test_foundation_first_open.py` 并加守卫「enforced（pr）用例文件必须在
那一步」（U04 同门指出的漏——之前 CI 上六条 FO 根本没跑）。Codex 评 U04 #459 时报在本 PR 文件上的 P1
（databinding 读 / hash 项目外文件）修法见 ADR 0057「不做的事」第二条。

**facade 清单锚点改为符号，行号只作信息**：`U00_FACADE_LEDGER.json` 的每个调用点 / 用例引用加 `symbol`
（AST 限定名，模块级 `<module>`；替换掉手写的 `function`——37 条里 11 条写错了函数名，按行号反查一次性
填对），门禁 `test_every_cited_caller_symbol_still_mentions_the_export` / `test_every_cited_test_symbol_still_contains_its_snippet`
按符号体（含装饰器）找导出名 / 片段，`line` 只作信息（另一条用例只守它落在符号范围内）。之前按绝对行号钉，
一天内让三个 PR 红（#462 / #469 / #475），而 `probe_asset` 在 `_declared_density` 里的引用实际指着
`_original_page_pt` 那一行，按行号的判据看不见。

**下一个无阻塞阶段 / 子切片**：PR B（本阶段）：前端确认对话框（`workdir_confirmation_required` →
三档选择 → `PATCH /api/engine/workdir` → 重排渲染）、设置里 `WorkdirRow` 改三档、i18n 四个新 code
（`workdir_confirmation_required` / `explicit_python_unusable` / `project_python_unusable` /
`preparation_plan_stale`）、`resources.d.ts` 重生成、MCP `open_figure` 的结构化投影（BridgeError 带
`confirmation`）。U04 的输入：`plan.environment.discovery.rejected` 是「项目里有 venv 但缺什么」的
事实面；`DependencyIntent` 原样可见、`conflicts` 不裁决；安装目标仍只有项目 venv / 受管环境；首开采用
的项目 venv（`trigger=first_open`）就是联合依赖的安装目标候选。U09 的输入：六条场景的 `receipt.backend`
都是 `pymupdf`，RenderCore 终点要另取资格，不能混称。

**回退方式、不能假装可回滚的外部副作用**：revert PR A 即回退：worker 装载形态回到平铺 import（#447 重新
打开）、解释器链回到「缺包后接手」、`project_root` 模式与 `needs_input` 消失。项目设置里多出的
`workdir.decided_at` / `environment.mode=default` / `trigger=first_open` 旧代码读得懂（`mode_for` 只看
`mode`，`remembered()` 对 `mode=default` 回 None）。没有发布、没有外部副作用。
