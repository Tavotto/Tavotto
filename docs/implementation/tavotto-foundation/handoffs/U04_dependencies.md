# U04 · 一次准备多个依赖，不等私有 Python 下载器 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U04.managed_dependencies。三个叠栈 PR：**A** 无损解析 + import 分类 + 联合计划（纯逻辑，不装）；
**B** 受管环境按代的事务（建 / 装 / 验 / 切 active / 取消 / 租约）；**C** 跑前的门、准备接口与 HTTP / MCP / 前端的一次
授权、场景与台账。架构决策：[`docs/adr/0061-joint-dependency-preparation.md`](../../../adr/0061-joint-dependency-preparation.md)。
本文件随 A 落地，B / C 各追加一段。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `52edd08a`（`foundation/u03-first-open-b` 派工时的 head，
下面是 U03 A → U01 #451 → main）；期间 U03-B rebase 两次（→ `6d1e70e7` → `afa6eb0f`），本分支各 `rebase --onto` 一次；
结束 = PR A 的 head（合并后以 `git log origin/main` 里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u04` 里做，
用户主工作区一个字节没碰；主仓库 `.venv` 没装任何东西。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 终点、safe worker、解释器链、单包修复的窄语法与
pip 路径、包管理、会话认证）。**PR A 默认路径上变了的行为**：`depresolve.declared_intents` 的读法（成熟解析器、
有界 include、四档 kind、组名带来源路径）——它的消费者今天只有准备计划的 `dependency_intents` / `dependency_conflicts`
两个只读字段；`conflicts()` 由「specifier 串不一致」改成「`SpecifierSet` 确定矛盾」（少报不确定的）。拟启用能力：无
（`plan.json` `new_default_capabilities_enabled: []`）；本阶段不产生任何产品资格。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、`codex-plugin/AGENTS.md`、
`packaging/AGENTS.md`；`docs/rules/backend/` 的 dependency-repair-and-packages / preparation-and-receipts /
execution-entries / project-system / profiles-and-preflight / worker-protocol-and-lifecycle / process-boundaries /
session-auth / tavotto-run-control-plane / runtime-figure-assets；`docs/rules/repo/` same-origin-pairs / predicate-subject；
ADR 0008 / 0019 / 0021 / 0038 / 0044 / 0047 / 0053 / 0056（U02 分支）/ 0057；实施包 00 §3、01（D05 / D10 / D11 / D14）、
03、04 §2–3、05 §2 / §4、07、`phases/U04_dependencies.md`、registry 的 CP04 / CP06 / CP08 / CP08-C 条目与
`archive/firstopen_cases.json` 的 FO05 / 13 / 18 / 20 / 21 / 22 / 27 / 28 / 29 / 31 原文；U00 基线 §1.3 / §4.3、U01 / U02 / U03
交接。复用（权威不动）：`depresolve.parse_requirement` / `resolve` / `curated_distribution` / `normalize_distribution`
（旧安装路径与 curated 表）、`projectenv._probe_scratch_dir`（体检子进程的 cwd 纪律）、`config.project_settings`、
`packaging`（PEP 508 / 440 / 685 参考实现）。

## PR A：无损解析、import 分类、联合计划（纯逻辑）

**实际代码与 API / 数据结构变更**（细节见 ADR 0061 §一–§三）：

| 层 | 变更 |
|---|---|
| `pyproject.toml` | 运行时依赖加 `packaging>=24,<27`（理由 / 边界见 ADR §一；`process-boundaries.md` 同步一句） |
| `engine/depresolve.py` | intent 段重写：`packaging.requirements.Requirement` 解析；kind 加 `unsupported` + 闭集 `UNSUPPORTED_REASONS`（14 条）；`DependencyIntent` 多 `hashes` / `reason` / `declared`；`-r` / `-c` 在项目根内有界跟进（`_Walk` / `_read_declaration_file`：缺失 / 越界（含软链接）/ 环（含自包含）/ 超限各一条 unsupported 留在原位；被 include 的条目归引用它的组；同一次 walk 同一文件只读一次）；`pep723_intents`（PEP 723）；`pyproject_intents`（PEP 621 主依赖 / optional、PEP 735 `dependency-groups` 含 `include-group` 展开到外层组、Poetry 表只认 PEP 440 形态）；`_toml_loads`（tomllib → tomli → `toml_parser_unavailable`）；`MANAGER_LOCK_NAMES` 只登记存在；组名 = 来源相对路径 / `pyproject.toml:<段>` / `pep723:<脚本>`；`default_group`；`requirement_string`（从结构重新序列化，extras PEP 685）；`conflicts()` 用 `SpecifierSet` 判确定矛盾并带 `reasons`。旧安装路径（`parse_requirement` / `parse_requirements_text` / `project_declared` / `resolve` / `from_user_input`）一字未动 |
| `engine/importscan.py`（新，纯标准库） | 脚本 + 本地模块（有界：`MAX_LOCAL_MODULES` / `MAX_DEPTH`）的 import 分四桶 × 六上下文；`ImportClass.needed` = 第三方且无条件；`map_distribution`（项目声明经 curated / 同名 > curated > 未知，不猜）；stdlib 表可由目标解释器给 |
| `engine/depplan.py`（新） | `target_facts(python)`（目标解释器里量 marker 环境 / stdlib / 已装；启动条件与 `probe_environment` 对齐；按解释器缓存 + `reset_cache`）；`select()`（默认组 + 点名组、marker 按目标求值、约束不分组、unsupported 只在范围内计）；`plan()` → `JointPlan`（`needed` / `missing` / `satisfied` / `unknown` / `possible` / `requirements` / `constraints` / `hashes` / `require_hashes` / `adapter` / `blocked` / `identity`）；`ADAPTER_REQUIREMENTS` ↔ pyproject `worker` extra；`SETTINGS_KEY = "dependency_groups"` + `selected_groups_setting` |
| `tests/test_execution_receipt.py` | U01 的 intent 用例按新语义改 5 处（specifier / marker 规范串、`tabulate[]` 合法、`-r 缺失` 是 unsupported/include_missing、pyproject 组名带 `pyproject.toml:`、3.10 分支允许 unsupported） |
| `tests/test_dependency_plan.py`（新） | 74 条：语法 / unsupported 闭集 / include 边界 / PEP 723 / 735 / Poetry / 3.10 无 tomllib（模拟 + 真 3.10）/ 冲突判据 / 上下文 × 桶 / 本地跟进 / 目标事实（真子进程）/ 选择 / 计划的每一条「不装」/ 身份不含路径 / adapter 同源对 |
| 文档 | ADR 0061；`dependency-repair-and-packages.md` 新一节；`process-boundaries.md` 一句；`src/tavotto/AGENTS.md` 一行；`COMMERCIALIZATION_DEPENDENCY_AUDIT.md` packaging 一行；本文件；`evidence/u04/` |

**关联旧要求 ID / 场景 ID**（PR A 只做到「计划」这一层，场景的资格在 PR C 取）：FO-029（marker / extras / constraints /
hash 原样，Poetry `^` 显式 unsupported——不丢约束继续）、FO-030（未选 optional / dev 组默认不选、不装）、FO-031（本地模块
桶永不装）、FO-032（联合求解的输入集合：requirements + constraints + adapter 一次给全）、FO-033（adapter 与项目约束进
同一次求解——受管环境；用户 venv 不并入，只报告）、FO-034（unknown 不猜）、FO-035（URL / VCS / `-e` / 本地路径显式
unsupported，不进安装集合）、FO-038（版本不满足声明只报告不改）；FO20 / FO21 的判据面（选择、blocked/conflict）。
FO-036 / 037 / 039 / 040、FO13 / 18 / 22 / 27 / 28 / 29 / 31 归 PR B / C。registry 220 条 `execution_status` 一条没动。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿（442 files） |
| `PYTHONPATH=$WT/src pytest tests/test_dependency_plan.py tests/test_execution_receipt.py tests/test_dependency_repair.py tests/test_import_architecture.py tests/test_preparation_api.py` | 同上；应用 `.venv` 3.13.11 | 0 | 74 + 45 + … 通过（`test_target_facts` 真起子进程量 `sys.executable`） |
| 变异反证 15 条（`evidence/u04/mutations_pr_a.md`） | 同上 | 每条非零；还原后 0 | M15（stdlib 表用宿主）第一次绿——用例只在 `importscan` 层钉了参数、没在 `plan` 层钉传递，补 `test_plan_classifies_stdlib_by_the_target_interpreter_not_the_host` 后红 |
| `pytest tests/test_docs_references.py tests/test_agents_rules_index.py` | 同上 | 0 | ADR 0056 在 U02 分支上，本分支只写文字引用不写相对链接 |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文（红项逐条归因） |
| 内置 runtime、e2e、lab、Windows / Linux、3.10 真实分支、`pnpm test/build`（PR A 不碰 web） | — | — | **not_run**（本机只有 macOS 3.13；3.10 分支由 backend-fast 的 3.10 腿跑——那条腿装了 pytest 的 tomli，走 tomli 路；无 tomli 的路由模拟用例覆盖） |

**本切片的正例、负例、旧行为回归**：正例 = 夹具 ⑤ 逐文件对得上 truth.json；嵌套 `-r` / `-c` 归引用组；PEP 735 include
展开；PEP 723 是脚本自己的组；六种上下文各得其所；本地 `lab_utils` 里的 `h5py` 经 via 记成 needed；`target_facts` 在真
解释器里量到 `packaging`；ready 计划只装缺的、其余当约束、adapter 只给受管。负例 = 17 种 unsupported 形状各自命名；
include 缺失 / 越界 / 软链接跳出 / 环 / 自包含 / 超限；坏 TOML / 多个 PEP 723 块 / 没有 TOML 解析器都不是空；Poetry `^`
/ `~` / 表不翻译；marker 为假不装也不算缺；未选组不装、未选组里的 unsupported 不 blocked；unknown 不进 requirements；
hash 模式缺一条就 blocked；冲突 blocked 且冲突可见；目标事实拿不到 blocked；版本不满足只报告。旧行为回归 =
`parse_requirement` 窄语法逐字不变（U01 用例仍在）、`resolve()` / 单包 `create_plan` 路径的 15 条负向反证全绿、
`project_declared()` 仍是「第一条静默胜出」（用例钉着，它只服务旧单包路径）。

**本次是否改变 case enrollment**：PR A **不改**（没有经真实入口的场景用例）。PR C 提升 FO18 / 20 / 21 / 27 / 31 等，
届时按 03 §4 逐条写正例 + 负例 + 预算 + lane。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：
* 未运行：见上表最后一行。
* 基础设施问题：本机 `worker_python` 指向别的会话的 venv（#452）——全量里 `test_run_messages_only_stderr` /
  `test_mcp_normalize` 两条像素 / `tests/native` 两条如实红，与本阶段改动无关。
* 真正产品失败：无新增。顺带发现的**事实**：① `packaging` 认 `tabulate[]==1`（空 extras 合法）、`1.2.3` 是合法包名——U01
  把前者当 unknown 是手写正则的产物，改从成熟解析器；② `packaging` 把 marker 的引号统一成双引号、specifier 按运算符排序
  ——intent 的 `specifier` / `marker` 是规范串，`raw` 才是原文；③ CI 3.10 腿装着 pytest 的 `tomli`，所以「3.10 没有 TOML
  解析器」那条分支在 CI 上不会自然走到，只能模拟。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动。`LICENSE`、ruleset、`aggregate_gate.py`、默认后端、
`security._PUBLIC_PATHS`、worker 守卫、写回事务一个都没动；生产依赖**加了一条**（`packaging`，ADR §一 记理由与许可）。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改产品源码、加运行时依赖、改准备计划的只读字段语义）
→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；变异反证 15/15 红；文档门禁 0。

**仍缺哪些默认启用 / 精确安装物资格**：全部。PR A 只是计划层；U04 的场景资格在 PR C，且仍是「源码树 + 旧后端 +
一个平台」的切片证据。

## PR B：受管环境按代的事务、联合安装、取消与租约

**实际代码与 API / 数据结构变更**（细节见 ADR 0061 §四–§六）：

| 层 | 变更 |
|---|---|
| `engine/managedenv.py` | 按代：`generation_dir / generation_python`（`envs/g<身份>/`，代号只许 `[a-z0-9-]`）、`generations()`（旧布局 `venv/` 是隐式 `legacy` 一代）、`active_generation()`、`register_generation`（先记 `incomplete`；第一次按代时把 legacy 登记进 `generations` 并保持 active）、`mark_generation`、`activate`（manifest `active` 字段原子写 = **唯一**指针）、`create_generation_venv`（最终目录里建，残留目录先删）、`is_managed_python`（任一代）、`retire_unused(in_use=)`（active 永不删、在用的不删）；`venv_dir / venv_python(generation=None)` 默认跟 active；`python_of` 按 active 那一代的状态；`mark_ready / mark_incomplete` 落在 active 那一代；`state()` 多 `active_generation` / `generations`（只出代号与状态）。schema 仍是 1（加可选字段不升） |
| `engine/deprepair.py` | `JointRepairPlan` + `create_joint_plan`（不是 `ready` 的计划拒绝绑定 `dependency_plan_blocked` 带 `joint`；用户 venv 目标必须就是此刻选中的；建代前查磁盘）/ `get_joint_plan` / `prepare` / `prepare_async` / `cancel_status`（提交点后 `committed`）；`_GenerationJob` + `_run_generation(_locked)`——**四条路共用**（联合准备 / 单包修复到受管环境 / `rebuild_managed` / 包管理首装）：建 → 一次 pip → `pip check` → `probe_imports` → `worker_self_test` → `activate` → 快照 → 记账 → remember → 作废会话 → `retire_unused`；`generation_requirements`（adapter + 账 + delta）；`write_plan_files`（从解析结构生成、每行过形状关）；`pip_install_joint_argv` / `pip_check_argv`（唯一出处）；`probe_imports`；`_run_joint_in_place`（用户 venv 原地）；新码 `dependency_consistency_failed` / `dependency_hash_mismatch` / `dependency_plan_blocked`；`classify_pip_failure` 多 hash 一档；`_fingerprint_managed` 含 active 代号；`_emit` 认 `joint` 与 `committed`；`list_managed_packages.busy` 同时看合成 key；`_create_managed` 删除（被代事务取代） |
| `engine/pool.py` | `mutating_environment(key, python, *, shutdown=True)`：换代传 `shutdown=False`（不收旧代 worker）；`remembered_source` 用 `managedenv.is_managed_python`（任一代都算受管） |
| `tests/support/dependency_repair.py` | `build_wheel` 多 `requires` / `provides_extras` / `body`；`offline_managed_env` 同时替换 `create_generation_venv`（建 + 挂宿主 site-packages，建不了目录回 `(False, 原因)`）并把 `depplan.ADAPTER_REQUIREMENTS` 换成空表（离线 CI 里 matplotlib 来自宿主；生产值有单元用例钉着） |
| 既有用例 | `test_dependency_repair::test_rebuild_and_install_are_mutually_exclusive` 探针改放 `base_python`（重建不再有 `_create_managed`），并多断言合成 key 也放掉；`test_package_management::test_a_missing_package_is_reported_with_its_own_code` 改成新语义：首装失败 = 第一代 `incomplete`、没有 active（不假装有环境）、下一次装得上的照常成代 |
| `tests/test_dependency_transaction.py`（新） | 30 条（见 evidence/u04/mutations_pr_b.md 的用例名） |
| 文档 | ADR 0061 §五 按落地形状修订（manifest `active` 字段是唯一指针，不设 `active.json`）；`dependency-repair-and-packages.md` 事务一节；本文件；`evidence/u04/mutations_pr_b.md` |

**关联旧要求 ID / 场景 ID**：FO-036（最终目录建、原子切 active、不移动已建环境）、FO-037（安装失败旧环境仍可用、未完成不激活）、
FO-039（复用 envlease、不杀 native）、FO-032 / FO-033（联合求解一次 pip、约束进同一次求解）；FO18 / FO20 / FO21 / FO22 / FO27 /
FO28 / FO29 / FO31 的**机制面**各有一条真事务用例（经真实公共入口的场景资格在 PR C 取）。FO-040（真实二进制 wheel）与 FO13
未做（纯 Python wheel 装成功不算 ABI 资格，台账仍 planned）。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `PYTHONPATH=$WT/src pytest tests/test_dependency_transaction.py` | 同上；真 venv（`python -m venv`）、真 pip（离线 wheelhouse：七个手工 wheel，含 extra 拉进第四个包）、真 worker 自检（本机 worker 解释器 3.11.14 / mpl 3.11.1） | 0 | 30 passed（约 6 分钟） |
| `pytest tests/test_dependency_repair.py tests/test_dependency_repair_e2e.py tests/test_package_management.py tests/test_package_lookup.py tests/test_project_env.py tests/test_first_open_environment.py tests/test_preparation_api.py tests/test_import_architecture.py tests/test_error_codes.py tests/test_dependency_plan.py` | 同上 | 0 | 通过（旧单包路径的 e2e 十条全绿——受管目标已在走代事务） |
| 变异反证 17 条（`evidence/u04/mutations_pr_b.md`） | 同上 | 每条非零；还原后 0 | B17 第一次绿是脚本 `-k` 指错用例，用例本身早就钉着 |
| `PYTHONPATH=$WT/src pytest`（全量） | 同上 | 见 PR 正文 | 见 PR 正文 |
| Windows（`envs/` 目录退役时文件占用）、Linux、内置 runtime、e2e | — | — | **not_run**（本机只有 macOS；退役删不掉的目录留到下次是为 Windows 占用写的，没有本机证据） |

**本切片的正例、负例、旧行为回归**：正例 = 三包一次成代（extra 拉进的第四个也在、账三笔、prefix 在代目录、脚本会话作废、
项目从此用它）；二开 `nothing_needed`；重建成新一代（旧代退役）；单包修复走同一事务；用户 venv 原地只装缺的。负例 =
声明冲突停在建代之前、求解器冲突 / 无 wheel / 坏 hash / 取消 / 自检不过 / `pip check` 不过 / 关键 import 失败 / 只读目录 /
磁盘不足各**不切 active**；native 租约拒绝开始且租约原样；旧代在用不删；提交点后拒取消；过期计划拒执行；blocked 计划
拒绑定；用户 venv 目标不是此刻选中的拒绝。旧行为回归 = 单包路径的 `pip_install_argv` 逐字不变；包管理原地路径不变；
`test_dependency_repair` 的十五条负向反证全绿；envlease 两个方向的拒绝不变。

**未运行 / 基础设施问题 / 真正产品失败**：未运行见上表；基础设施 = 本机 `worker_python` 指向 3.11 的 venv（#452），所以真事务
用例的 worker 是 3.11——与应用 `.venv`（3.13）不同 minor，反而顺带证明了「建代的 base 不必是应用自己的解释器」；真正产品
失败无新增。顺带发现：① `_prepare_guarded` 把夹具里没接住的 `PermissionError` 收成 `dependency_install_failed`——真实
`create_generation_venv` 自己接 `OSError` 回 `(False, 原因)`，夹具第一版没照做（已改）；② `monkeypatch.undo()` 会连同夹具的
环境变量一起撤掉，用例内改判据要用 `MonkeyPatch.context()`。

**当前可合并依据**：中高风险档（改产品行为：受管环境布局与安装事务）→ `full-ci` + `@codex review`；ruff 两条 0；针对性
pytest 0；变异 17/17；旧 e2e 全绿。**回退**：revert PR B 即回到原地改写的单份 `venv/`；已经按代建出来的环境 manifest
里多出 `generations` / `active`，旧代码 `read_manifest` 读得懂（schema 未变）但 `venv_python()` 会指回 `venv/`——若那时
`venv/` 不存在，`python_of` 回 None，用户看到「环境不存在」，重装即可；没有别的外部副作用。

**PR B 第二轮（Codex #461 评审，3 P1 + 2 P2，全部修在 B）**：

| 评审 | 处置 | 用例 |
|---|---|---|
| P1 重建两次同一份账 → 同一个身份 → `register_generation` 把 active 改成 incomplete、`create_generation_venv` 把 active 目录 rmtree | `managedenv.fresh_generation(project, identity)`：目录名撞**在册**的代（active / 旧代还有人用——`retire_unused` 之后只剩这两种）就 `g<身份>-2`、`-3`……身份字段照记；`register_generation` 拒绝重新登记 active / `ready` 的代（结构性：撞上就抛，不静默覆盖） | `test_fresh_generation_never_reuses_a_registered_name`（单元）、`test_rebuild_twice_never_touches_the_active_directory`（真事务：建代那一刻 active 目录在、状态 ready） |
| P1 hash 模式 + 受管目标：adapter 没 hash 混进 `--require-hashes` 的需求文件，整次必败（离线夹具把 adapter 换空所以从没量到） | 计划期校验 `depplan._adapter_against_lock`：锁必须把 matplotlib / numpy 用 `==` 钉在 adapter 范围内（没钉 → `dependency_hashes_incomplete` 带 `adapter`；钉在范围外 → `dependency_conflict`）；`generation_requirements(hash_mode=True)` 只给锁本身（adapter / 账上那些不进文件） | `test_hash_mode_on_the_managed_target_requires_the_lock_to_pin_the_adapter`（三个分支）、`test_generation_requirements_in_hash_mode_are_the_lock_only`、`test_hash_locked_managed_generation_installs_only_the_lock`（真 pip `--require-hashes` 成代，文件只有两行锁；锁没钉 adapter 计划期 409） |
| P1 选中项目 venv、显式选受管目标：事实按项目 venv 量，代却从 base 建 → 新代漏装、marker 按另一个 minor | `deprepair._facts_for(kind, python, root)` 回两份：缺什么按**此刻会跑脚本的**解释器（门的主语）、装什么 / marker / stdlib 按**目标**（active 那一代；没有就 `depplan.fresh_venv_facts(base, provided=adapter_distributions())`——marker 与 stdlib 是 base 的、已装只有 adapter 必然带上的）；`depplan.plan(install_facts=)` 集合按它量 | `test_install_facts_measure_the_set_against_the_target_not_the_current_interpreter`、`test_fresh_venv_facts_provide_the_adapter_and_nothing_else`、`test_managed_target_from_a_project_venv_installs_the_full_needed_set`（真 venv 里有 alpha → 新代 alpha + beta 都装，项目 venv 一个字节不动） |
| P2 执行前只重算解释器指纹，目标里的包变了不算 stale | `prepare()` 执行前 `_facts_for(..., use_cache=False)` 重量两份 digest，任一不同 → `repair_plan_stale`；`JointRepairPlan` 多 `facts_python` / `install_facts_digest` | `test_stale_plan_is_refused_when_the_target_packages_changed` |
| P2 自检期间接受的取消照常提交 | 受管与用户 venv 两条路都在 `worker_self_test` 之后、提交点之前再看一次事件；用户 venv 那条如实报 cancelled + 体检（包已在里面） | `test_cancel_accepted_during_the_selftest_is_honored`（受管）/ `…_in_place`（用户 venv） |

变异 B18–B27 见 `evidence/u04/mutations_pr_b.md` 第二轮。顺带：`_within` 三处合一（A 分支，team-lead 指示：`projectenv.within` 是唯一判据）。

**下一个无阻塞阶段 / 子切片**：PR C（门 / 端点 / 前端 / MCP / 场景）。PR B 给它的输入：`deprepair.joint_plan_for`（只读算计划）、`create_joint_plan` / `prepare_async` / `progress` / `cancel_status`、`JointRepairPlan.to_payload()`（不含路径）。PR A 给 PR B 的输入曾是：`JointPlan.requirements / constraints / hashes / require_hashes /
adapter / identity`；`depplan.reset_cache(python)` 在事务结束时调；`ADAPTER_REQUIREMENTS` 是受管环境每一代的基座。
U05 的输入：ADR §四（安装器接入规则）。U06 并行：本 PR 只碰 `pyproject.toml` 的 `dependencies` 三行（U06 加可选 extra，
相邻不重叠）。

**回退方式、不能假装可回滚的外部副作用**：revert PR A 即回退（新模块删掉、intent 段回到 U01 的正则读法、`packaging`
从依赖里去掉——但装过新版 wheel 的环境里它仍在，无害）。没有设置写入、没有发布、没有外部副作用。
