# Import Origin Resolver — P0 仓库审计基线

> 范围：只读审计，不改任何非文档代码。结论只写有证据的东西；证据分四级，文中用方括号标注：
>
> * `[运行]` 审计时在 `origin/main` 的代码上真跑过（复现片段见文末「复现」）；
> * `[测试]` 有现成用例钉着（给出用例名）；
> * `[代码]` 读代码得出，未运行；
> * `[不确定]` 没验证到，不得当事实用。
>
> 凡写「不足」的条目都带 `[运行]` 或 `[代码]` 证据；没有证据的猜测不写成缺陷。

## 0. 基线

| 项 | 值 |
| --- | --- |
| 审计基线 `origin/main` | `82b4aa7a9aeabf05b8631d48e4674fb80eacfe95`（`feat: 环境建议 / 检查 / 采用分开，依赖安装授权绑定实际影响（ADR 0114 / 0115）(#814)`） |
| 叠栈顶 `origin/onboarding/pr10-guide-card`（#820） | 审计时 `b5fc0742eaf2adac567b164798bc7ffdbf904604`（merge-base 即上面的 main；比 main 多 201 个提交（含多次合并提交）、165 个文件、+21620/-1483）。**这个 SHA 会随 #815–#820 的评审轮次继续移动**，PR 开工前以 `git rev-parse` 重取 |
| 审计分支 | `feat/import-origin-resolver-pr1`（worktree `/Volumes/Projects/tavotto-wt/import-origin-pr1`，从 `origin/main` 切出） |
| 备注 | 主工作区本地 `main` 落后于 `origin/main`（gitStatus 里最新是 `c0021425d`），不要在主工作区的旧树上读 |

已读：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`docs/rules/backend/dependency-repair-and-packages.md`、`docs/rules/repo/same-origin-pairs.md`、ADR 0019 / 0057 / 0061 / 0114 / 0115 的规则摘要（经上面的细则）、叠栈上的 ADR 0114 §六，以及 P0 清单里的全部后端模块与前端文件（前端只读到 store / 组件的职责与叠栈 diff，没有逐行读）。`docs/rules/backend/preparation-and-receipts.md` 与 `docs/rules/frontend/readiness-and-left-shell.md` 只读了与环境 / 依赖相关的部分 `[不确定：未逐字通读]`。

## 1. 当前架构与关键调用链

### 1.1 两条主链

```text
链 A  依赖（回答「脚本要什么、目标里缺什么、怎么装」）
importscan.scan(root, script, declared=, stdlib=)           engine/importscan.py:396
   │  数据：ScanResult{classes: tuple[ImportClass], uses, dynamic, problems, truncated, files}
   │        ImportClass{module, bucket∈stdlib|local|third_party|unknown, context(6 档), distribution,
   │                    resolution_source∈project_declared|curated, local_path, lines, via, unused}
   ▼  （唯一调用点：depplan.plan，depplan.py:432）
depplan.plan(root, script, facts=TargetFacts, target_kind, install_facts=)   engine/depplan.py:406
   │  facts 来自 depplan.target_facts(python)（depplan.py:173）：起**一个子进程**在目标解释器里跑 _FACTS_SRC
   │        （depplan.py:95，m.distributions() 读已装发行包名→版本，另量 marker 环境与 sys.stdlib_module_names）
   │  数据：JointPlan{status∈nothing_needed|ready|blocked, needed, missing, satisfied, unknown, possible,
   │                  unused, requirements, constraints, hashes, adapter, blocked, selection, scan, facts, identity, inputs_digest}
   ▼
deprepair.joint_plan_for → _facts_for → create_joint_plan → prepare / start_confirmed
   │  joint_plan_for  deprepair.py:3777（只读算计划）；_facts_for  :3812（缺什么按此刻解释器、装到哪按目标）
   │  create_joint_plan :3841 绑定 JointRepairPlan（含 impact / impact_digest，ADR 0115）；prepare :4281
   ▼
deprepair._run_generation (:4528) → managedenv.register_generation (managedenv.py:683) → pip → pip check
   → probe_imports (deprepair.py:5053) → worker_self_test (:2251) → managedenv.activate (managedenv.py:750，唯一指针)
```

```text
链 B  环境（回答「用谁的解释器、有哪些候选、能不能跑」）
projscan.environment_evidence(root, script, private=)        engine/projscan.py:415   只读磁盘线索，零进程
   ├─ projectenv.discover (projectenv.py:271)               项目 venv：.venv → venv → env，向上不出项目根
   ├─ projectenv.remembered_record                          项目记住的决定
   └─ userenvs.discover(ask_login_shell=None)  (userenvs.py:450)  .vscode / .python-version / environment.yml /
                                                            shebang / Conda / pyenv 落盘记录（登录 shell 只读缓存）
   ▼
envadvice.recommend (envadvice.py:173)   纯读：标签闭集 selected>remembered_legacy>project_hint>checked_compatible>machine_hint>bundled
envadvice.check (:317 → _check :355)     唯一「用户点了才起候选解释器」的入口：projectenv.probe_environment(python, timeout=) ——**不带 modules**
   ▼
projectenv.probe_environment (projectenv.py:424)  在候选解释器里执行 _PROBE_SRC（:349）：import matplotlib、
   │                                       按文件执行 worker.py、`__import__(module)` / `__import__(name for name in modules)`
   ▼
采用：PATCH /api/engine/environment {candidate, expected_generation} → projectenv.remember(automatic=False)
   ▼
pool.resolve_worker_python (pool.py:1259)  项目级决定的唯一出处；SPAWN_GATES / ENVIRONMENT_DECIDERS (pool.py:2736 / 2748)
   → pool._new_worker (pool.py:2769) 起 worker；sys.path 在 worker.py:776–781 组装
```

### 1.2 两条链的交汇点（也是新解析器要接的地方）

* **「装齐」的判据 = 在候选解释器里真的 `__import__`**：`userenvs.evaluate`（userenvs.py:583）把 `deprepair._plan_imports(plan)` 取出的 `missing + satisfied` 的 import 名与 `unknown` 名交给 `projectenv.probe_environment(modules=…)`，看 `modules_ok`。门（`deprepair.user_environment_offer`，deprepair.py:5310）、依赖弹窗、采用前复核（`recheck_user_environment`，deprepair.py:5246）、叠栈的「自动检测」（见 §5）共用它 `[代码]`。
* **`envadvice.check` 与上面的覆盖度检查是两套缓存**：`envadvice._verdicts` 键为 (解释器路径, 环境代)，只含健康体检；`userenvs._probe_cache` 键为 (路径键, 模块元组, bundled)，不含环境代 `[代码]`（envadvice.py:96，userenvs.py:546–560）。
* **目前没有任何一处把「发行包名」与「import 名」在**用户环境**里对上**：映射只有静态表（项目声明 > `CURATED`/`SAME_NAME`，`importscan.map_distribution`，importscan.py:653）。

### 1.3 默认项目扫描（导入即扫描，T02）在哪里

`projscan.scan`（projscan.py:598）→ `discover.iter_all_scripts` / `project_refresh.iter_assets` / `environment_evidence` / `dependency_evidence`（projscan.py:372，只读声明文件、`evaluated: False`）。**它不调用 `importscan`**，没有 import 来源分类；脚本只经 `discover.inspect_script(target_python=None)` 做语法与绘图证据。服务层 `ScanService` 单飞 / 取消 / 迟到作废（projscan.py:729）。

## 2. P0 必答 10 问

### Q1 如何提取脚本与本地模块的 import

* 解析：`ast.parse`（importscan.py:386）+ `_Visitor`（:168）。`Import` 取每个 alias 的顶级名；`ImportFrom` 取 `node.module` 顶级名，**相对导入（`level>0`）直接跳过**（:261–263）；字面量 `importlib.import_module("x")` / `import_module("x")` / `__import__("x")` 当作 import，非字面量记入 `dynamic`（:271–283、:325–344）。
* 上下文六档（:45–59）：`unconditional / conditional / deferred / optional / type_checking / dynamic`；`if __name__ == "__main__":` 体当无条件（:225–230）；`try` 体 = optional，`with suppress(ImportError)` = optional（:244）；经本地模块的 import 取两处较弱的上下文（:514–519）。只有「第三方 ∧ 无条件 ∧ 非 unused」才是 `needed`（`ImportClass.needed`，:111–118）。
* 本地模块跟进：搜索目录 = `[脚本目录, 项目根]`（:419–421；与 safe worker 的 `sys.path.insert(0, 脚本目录)` + 项目根一致，`worker.py:776–781`，**与 cwd 档无关**），上限 `MAX_LOCAL_MODULES=24`、`MAX_DEPTH=3`、单文件 1 MiB（:63–65）；本地判定 `_local_module_path`（:350–374）。
* 调用方只有 `depplan.plan`（`grep` 全仓 `importscan.` 的非自身、非注释引用：`depplan.py:432` 与 `deprepair.py:3772` 的 `HOST_STDLIB` 常量）。

### Q2 如何分 stdlib / 本地 / 第三方 / unknown

顺序（:466 与 :561–589）：名字在 stdlib 名字表 → `stdlib`；否则在 `local_paths` → `local`；否则 `map_distribution`：项目声明（经 curated 表或同名对上）> `CURATED`/`SAME_NAME`（`depresolve.curated_distribution`，depresolve.py:186）→ `third_party`，映射不到 → `unknown`（**不猜同名**，FO-034）。stdlib 名字表由 `depplan.plan` 传入**目标解释器**的 `sys.stdlib_module_names`（`_FACTS_SRC` 量，depplan.py:95–135）；目标量不出时用宿主的 `HOST_STDLIB`（importscan.py:74）。没有 built-in / frozen 的单独概念。 `[代码]`

### Q3 何时误判（逐条有证据）

| # | 现象 | 证据 |
| --- | --- | --- |
| M1 | 项目里 `json.py`（或任意 stdlib 同名文件）与脚本同目录：被归 `stdlib`，不跟进该文件的 import。真实 Python 里脚本目录在 `sys.path[0]`，`json.py` 遮蔽标准库 | `[运行]` 扫描结果 `json stdlib`；真 CPython 3.13 `import json` 打印的是本地 `json.py`。根因 importscan.py:466 `name in stdlib_names → continue` 先于本地判定 |
| M2 | 同目录同时有 `dup.py` 与 `dup/__init__.py`：选了 `dup.py`；真实 Python 目录包优先于同名模块 | `[运行]` 扫描 `dup local dup.py`，并跟进 `dup.py`；真 CPython `import dup` 打印 `pkg`。根因 `_local_module_path` 的候选顺序（:355）`.py` 在前 |
| M3 | 大小写：`import Utils` 而磁盘是 `utils.py`（macOS / Windows 不区分大小写的文件系统）：判成本地；真 Python 抛 `ModuleNotFoundError` | `[运行]` 扫描 `Utils local Utils.py`；真 CPython 报 `No module named 'Utils'`（macOS APFS）。Linux 上不复现 `[代码]` |
| M4 | 带 `__init__.py` 的本地包，子模块里的第三方 import 不被发现：`from pkg import a`、`import pkg.sub.b` 后 `pkg/a.py` 的 `import scipy` 不进 `needed` | `[运行]` 三个 fixture 都只报出 `pkg local`。根因 `_module_files`（:641–647）对 `__init__.py` 文件只返回自身；命名空间目录（无 `__init__.py`）才会 glob 顶层 `*.py`，但也不递归 |
| M5 | `cv2`：项目声明 / 已装的是 `opencv-python-headless`（或 contrib），仍映射到 `opencv-python`；计划把它当「缺」并要装 | `[运行]` `map_distribution("cv2", {"opencv-python-headless": ""}) == ("opencv-python","curated")`；`depplan.plan` 在 `installed={"opencv-python-headless":"4.9.0",…}` 下得 `ready`、`missing=['opencv-python']`、`requirements=('opencv-python',)` |
| M6 | 别名调用 `importlib`：`import importlib as il; il.import_module("scipy")` 不产生 import，`im(input())` 也不进 `dynamic` | `[运行]` 只有 `importlib stdlib`，`dynamic: []`。根因 `_dynamic_import_target`（:325–334）只认名字 `importlib` / `import_module` / `__import__` |
| M7 | 本地依赖链超过 3 层：第 4 层的第三方 import 丢失，`truncated=True` | `[运行]`（设计内上限，不是 bug；列在这里是因为它影响 PR1 的验收口径） |
| M8 | 符号链接指出项目根：**没有**越界（`outside.py` → `unknown`，不跟进） | `[运行]` 正确；`projectenv.within` 兜住（O-16 现状已满足） |

### Q4 哪些靠 curated 而非已安装证据

全部映射都是静态表：`CURATED`（depresolve.py:71，21 项 import≠包名）与 `SAME_NAME`（:100，74 项同名确认），再加项目声明（`project_declared`，requirements / pyproject / PEP 723）。**已安装元数据从不参与映射**。已装信息只出现在「缺不缺」：`installed.get(distribution)`（depplan.py:497、`_FACTS_SRC` 的发行包名→版本表），即「按**发行包名**问目标里有没有」，不是「按 import 名问谁提供」。多发行包提供同一个 import（`cv2`）、改名包（`PIL`↔Pillow 之外的长尾）、本地 wheel / VCS / editable / Conda 专有包，在现状下要么被表误映射（M5），要么落 `unknown`。`[运行][代码]`

### Q5 是否已有发行包元数据读取

* **有，但只读「发行包名 + 版本 + requires」，且只在目标解释器子进程里、用 `importlib.metadata`**：`depplan._FACTS_SRC`（depplan.py:122）、`deprepair._INVENTORY_SCRIPT`/`inventory()`（deprepair.py:2780–2820，包管理用，带 `-I`）、`installed_version()`（:2219）、`_versions_of`（:4955）、`figsession` 回执（figsession.py:352–365，worker 内）。
* **没有**任何代码读 `top_level.txt`、`RECORD`、`direct_url.json`、`INSTALLER`、`packages_distributions()`、`*.dist-info` 目录（全仓 grep 无命中）；没有「不起解释器直接读 site-packages 元数据文件」的实现。
* 叠栈外的在飞 PR #864 的 `_verify_same_name` 计划读 `top_level.txt` / RECORD 首段（PR 正文自述），尚未合入，且它在**装后核验**，不是扫描期映射。`[代码]`（来自 PR 正文与 diff，未运行）

### Q6 如何发现与检查 .venv / Conda / pyenv / 系统 Python

* 发现（只读磁盘、零进程）：项目 venv `projectenv.discover`（`VENV_DIRNAMES = (".venv","venv","env")`，projectenv.py:100；从脚本目录逐级向上、不出项目根、不跟软链接出根）；`userenvs.discover`（userenvs.py:450）：`.vscode/settings.json`、`.python-version`（含 pyenv 版本目录）、`environment.yml`（名字对 Conda 环境）、脚本 shebang、登录 shell（`ask_login_shell` 三态：True 现问 / None 只读缓存 / False 不问）、Conda 全部环境（`environments.txt` + 常见安装根）、pyenv versions；系统解释器 `pool.system_python_candidates`（pool.py:1009，老链条第四、五级）。候选总表 `projscan.environment_evidence`（projscan.py:415）上限 24（`MAX_ENV_CANDIDATES`）。
* 检查（起解释器）：`projectenv.probe_environment`（projectenv.py:424）：不带 `-I`、env 继承、cwd 换空目录、带 `-B`；检查内容 = Python 版本档（`PYTHON_MIN` … `PYTHON_MAX_EXCLUSIVE`）+ import matplotlib + 按文件执行 `worker.py` + 可选 `__import__(module)`。结论分五种：`ok` / `project_env_unsupported_python` / `project_env_no_matplotlib` / `project_env_worker_import_failed` / `project_env_module_missing`（再加 `project_env_unusable`）。
* 入口分两条：**明确检查动作** `envadvice.check`（预算：≤6 候选、总时限 180 s、单探测 120 s、单飞、可取消；不带 `modules`，只验健康）；**依赖门里的覆盖度评估** `userenvs.evaluate`（≤12 候选、4 并发，带 `modules`，确认模式下 `cache_only=True` 一个都不起）。
* 环境代（`projectenv.environment_generation`，projectenv.py:726）：解释器 `lstat` + `pyvenv.cfg` 的 (inode, mtime_ns, size)；重建换代，装包不换。
* 同路径不 realpath（`_executable_key`，projectenv.py:538 附近）：同 base 的两个 venv 分得开 `[测试]`（`test_environment_adoption.py::test_two_projects_on_one_base_keep_their_own_confirmed_environment`、`::test_a_compatible_environment_the_user_chose_over_the_recommended_one_is_the_one_that_runs`）。

### Q7 默认项目扫描是否启动解释器、登录 Shell 或用户代码（调用链证据）

**结论：默认扫描（`projscan.scan`，导入即扫描 / 刷新 / 就绪度）不启动任何子进程，不问登录 shell，不执行 / 导入用户代码，不 `find_spec`，不读 `*.dist-info`。** 不是约定，有三层证据：

1. **调用链**：`projscan.scan`（projscan.py:598）只调用 `discover.iter_all_scripts`、`project_refresh.iter_assets`、`environment_evidence`、`dependency_evidence`。`environment_evidence` 里：`projectenv.discover(..., no_follow=True)`（只 `lstat`/文件存在）、`projectenv.remembered_record(no_follow=True)`（读项目设置）、`userenvs.discover(ask_login_shell=None, no_follow=True)`（登录 shell 只并入**已被明确问过**的缓存答案，userenvs.py:493–496）。`dependency_evidence` → `depresolve.declared_intents`，只读文本，结论恒 `evaluated: False`（projscan.py:372–412）。文件头明确：解析永远不给目标解释器（`inspect_script(target_python=None)`），宿主判语法错误 = `parser=None`（未核验）。
2. **AST 门禁**：`tests/test_project_scan_zero_exec.py::test_the_scan_module_references_no_process_or_adoption_entry` 禁止 `projscan.py` 引用 `subprocess / pool / preparation / prepsession / deprepair / managedenv / socket / urllib / requests …` 与 `resolve_worker_python / decide_environment / plan_for / gate / probe_environment / login_shell_pythons / Popen / run / system …`。
3. **行为证明**：同文件里有哨兵脚本、哨兵假 `.venv` 解释器、哨兵假 `$SHELL`，项目甚至已记住该 venv：`test_the_scan_runs_nothing_with_every_sentinel_armed`、`test_reading_environment_clues_does_not_ask_the_login_shell`、`test_the_scan_reaches_no_process_or_network_entry_on_any_platform`（扫描期间 `Popen / os.system / os.exec* / posix_spawn* / socket.connect` 换桩，调用即失败）、`test_open_scan_refresh_and_readiness_over_http_start_nothing`（真 HTTP）。另 `tests/test_environment_adoption.py::test_the_environment_report_starts_no_interpreter_even_when_one_is_remembered`、`::test_recommending_starts_nothing_with_every_entry_armed`、`::test_the_first_resolution_runs_no_candidate_probe` 覆盖 GET `/api/engine/environment` 与 `envadvice.recommend`。

**必须区分的几条会起解释器的路径（它们不是「默认扫描」，但新解析器的结果若要经它们出去就会继承这个属性）**：

| 路径 | 起什么 | 谁触发 |
| --- | --- | --- |
| `depplan.target_facts` → 子进程跑 `_FACTS_SRC` | 当前选中的解释器一次（`m.distributions()` 读元数据，**不 import 用户包**），按解释器路径缓存 | `joint_plan_for` ← `preparation.plan_for`（准备会话「检查」）、`pool` 起会话前的 `deprepair.gate`、`GET /api/engine/dependencies`（app.py:7958，**这是个 GET，会起选中的解释器**）`[代码]` |
| `projectenv.probe_environment` | 候选解释器；`__import__` 执行**用户包的 `__init__`**（`modules` / `module`），且解释器启动本身会执行 `sitecustomize` / `.pth` | `envadvice.check`（明确动作）、`userenvs.evaluate` 非 `cache_only`（门 / 采用复核 / 叠栈自动检测）、`pool.try_project_env`（运行后缺包） |
| `userenvs.login_shell_pythons` | 用户登录 shell（读 rc 文件） | 只在 `envadvice.check(include_login_shell=True)` 或 legacy 模式的门 |
| worker 内 `importlib.util.find_spec` | 仅在 worker 子进程、用户脚本运行中（figcapture.py:2369，缺包占位判据） | 脚本运行 |

→ **新解析器的「零执行」边界**：静态来源解析（P1/P2）可以并入默认扫描而不破坏 `test_project_scan_zero_exec`，前提是只用 `ast` + 文件系统 + `pyvenv.cfg` + `*.dist-info` 文本；一旦经 `depplan.target_facts` / `probe_environment` 取结果，就必须挂在「用户发起的检查」之后，并在界面与诊断里如实标「已执行」，不能宣称无副作用。`importscan` 当前**没有** `scanbudget` 接线（读文件用 `Path.read_text`，无占位文件 / no_follow / 字节预算，importscan.py:377–383），并入默认扫描前要补（见 PR1）。 `[代码]`

### Q8 环境选择 / 准备 / 安装 / 重试各由哪个唯一模块负责

| 事 | 唯一出处 | 证据 |
| --- | --- | --- |
| 静态导入分析 | `importscan`（`ImportClass`），消费者 `depplan` | ADR 0061；`src/tavotto/AGENTS.md` |
| import 名 → 发行包的可信解析、包名 / 约束语法 | `depresolve`（`resolve`、`parse_requirement`、`declared_intents`） | `dependency-repair-and-packages.md`「四个模块各是自己那件事的唯一出处」 |
| 联合依赖计划 | `depplan.plan` | 同上 |
| 环境建议 / 检查 / 采用 | `envadvice`（`recommend` / `check` / PATCH 采用） | ADR 0114；`envadvice.py` 文件头 |
| 「此刻用哪个解释器」 | `pool.resolve_worker_python`（项目级决定的唯一出处）；换不换只在 `deprepair.decide_environment` | pool.py:1259；deprepair.py:5479 |
| 起 worker / 门 | `pool._new_worker`；门判据 `deprepair.gate`（经 `SPAWN_GATES`） | pool.py:2769、2736；deprepair.py:5509 |
| 安装与验证、记账 | `deprepair`（plan→install 两步、`impact_of` / `impact_digest`、代事务 `_run_generation`、三层验证） | deprepair.py:477 / 4528 / 5053 / 2251 |
| 受管环境生命周期 | `managedenv`（代、`active` 唯一指针、`retire_unused`） | managedenv.py:683–845 |
| 环境占用（互斥） | `envlease`（一张表） | envlease.py:182 `mutating` |
| 准备会话 | `prepsession`（`checks_of`、`SessionService`），计划快照 `preparation.plan_for` | prepsession.py:174、664；preparation.py:220 |
| 默认只读扫描 | `projscan` | projscan.py 文件头 |
| 重试 | 单包：`_attempted` 只在 pip 退出码 0 后登记，另有 `retryable` 终态字段；联合 / 代事务同纪律 | 规则细则；`tests/test_dependency_repair.py::test_a_failed_pip_run_leaves_the_requirement_retryable`（:1289）、`::test_a_failed_managed_pip_run_leaves_the_requirement_retryable`（:1455）、`::test_an_async_failure_after_pip_ran_is_not_offered_as_retryable`（:1562） |

### Q9 哪些逻辑与新解析器重叠

* **重叠（应扩展而不是重写）**：`importscan`（分类 / 上下文 / 本地跟进）、`depresolve.curated_distribution` / `resolve`（映射优先级）、`depplan.plan` 的 `needed/missing/satisfied/unknown/possible`、`userenvs.evaluate`（覆盖度判据）、`projscan.environment_evidence`（候选证据入口）、`envadvice`（建议 / 检查 / 采用）、`deprepair.impact_of`（授权影响摘要）、`depresolve.UNSUPPORTED_EDITABLE / DIRECT_URL / LOCAL_PATH`（声明侧已能识别 editable / 直链 / 本地路径并 `blocked`，depresolve.py:580–582）。
* **不重叠、不要碰**：`projectenv.probe_environment` 的体检内容（worker 启动导入链，#435）、`pool.resolve_worker_python`、`envlease`、代事务、`managedenv` manifest、`taskdiag` 脱敏投影、`privatepython`。
* **正在被在飞 PR 改的重叠区**：`depresolve.resolve`（#864 / #859 / #858）、`importscan.map_distribution` 注释（#864）、`deprepair.impact_of` 的 `IMPACT_VERSION`（#864 升到 2）、`pool.WorkerError.install_route`（#859）。见 §5。

### Q10 稳定错误码 / 协议契约 / 隐私约束 / 回归测试

* **稳定错误码**（不改已发布码，新状态走结构化 detail，计划原话）：`deprepair.ERROR_*`（deprepair.py:73–124、450–452、1556、3601–3603、5124–5127，共约 40 个）；`depplan.BLOCK_*`（depplan.py:60–68）；`projectenv.ERROR_*`（projectenv.py:84–97）；准备会话 `environment_choice_required` / `preparation_impact_unconfirmed` / `dependency_*`。用户可见码必须在两份 `errors.json` 有文案（`tests/test_error_codes.py`；`deprepair` 的 `ERROR_*` 由 `test_every_repair_code_has_text_in_both_languages` 钉住）。
* **协议字段**（新增只许加可选字段）：`JointPlan.to_payload`（`plan_version=1`，depplan.py:52、379）↔ 前端 `web/src/lib/api.ts` 的 `JointDependencyPlan`（api.ts:4093–4125，`needed` 条目目前只声明 4 个字段，而后端实发 `ImportClass.to_payload` 的 10 个字段 `[代码]`——前端类型已落后于后端，新增字段要顺手补）；`RepairPlan.impact` / `impact_digest` + `IMPACT_VERSION=1`（deprepair.py:441，新增一类影响必须升版，否则旧同意会被继承）；`envadvice.REC_VERSION=1`；`projscan.SCAN_VERSION=1`；`preparation` 会话的 `PLAN` / 修订号。
* **严格同源对**（`docs/rules/repo/same-origin-pairs.md`）里与本工程相关：`deprepair.PIP_OPTIONS_PROBE` 等 ↔ `codex-plugin/mcp/server.py`（若改 pip 选项探测）；`projectenv.PYTHON_MIN/PYTHON_MAX_EXCLUSIVE` ↔ `codex-plugin/mcp/server.py` 同名常量（若改 Python 支持区间）；`REBUILD_PROGRESS_ID_RE` ↔ `depRepairStore.newRebuildProgressId`（若动重建）；`depplan.ADAPTER_REQUIREMENTS` ↔ pyproject `worker` extra（`test_adapter_requirements_mirror_the_worker_extra`）。表里**没有** `importscan` / `depplan` 的前端镜像，但存在**事实上的**镜像：`api.ts` 的 `JointDependencyPlan.blocked` code 联合 ↔ `depplan.BLOCK_REASONS`，`DependencyPrepareDialog` 的 blocked 文案；ADR 0061 §三「下一步」表。**新增 blocked 理由 / 来源 / 影响类要同步这三处**，并建议在 PR4 把这一对登记进同源对表（现在是口头约定）。
* **隐私**：公开载荷与 SSE 不带机器路径，只带 `userenvs.env_id` / 项目相对路径（ADR 0053 §二）；安装日志两道脱敏（`deprepair._sanitize` + `diagnostics.redact_text`）；诊断只记 `custom_package_index: true/false`；终局进 `taskdiag.KIND_DEPENDENCY` 经 `deprepair.diagnostic_projection` 白名单（计数 / 闭集 / 稳定码 / 不透明摘要，包名也不进）；遥测 `EVENTS` 不扩容（要升 `CONSENT_VERSION`）。→ 计划里的 `local_path` / `evidence` 字段若进公开载荷，必须是项目相对路径或不透明 id，**绝不能带 site-packages 绝对路径或 dist-info 文件内容**。
* **回归测试（本工程相关、必须保持绿）**：`tests/test_dependency_plan.py`（`TestImportContexts` / `TestImportBuckets` / `TestPlan`）、`tests/test_unused_missing_import.py`、`tests/test_project_scan_zero_exec.py`、`tests/test_project_scan.py`、`tests/test_environment_adoption.py`、`tests/test_user_environments.py`、`tests/test_environment_health_parity.py`、`tests/test_dependency_impact.py`、`tests/test_dependency_transaction.py`、`tests/test_dependency_scope.py`、`tests/test_dependency_repair.py`、`tests/test_dependency_repair_e2e.py`、`tests/test_probe_leaves_no_trace.py`、`tests/test_preparation_session_dependencies.py`、`tests/test_foundation_dependencies.py`、`tests/test_import_architecture.py`（新增 engine 模块 / 新 import 边要过它）、`tests/test_agents_rules_index.py` / `tests/test_docs_references.py`。

## 3. 已有能力与不足

### 3.1 已有（不要重复实现）

| 能力 | 位置 | 备注 |
| --- | --- | --- |
| AST 导入提取 + 6 档上下文 + TYPE_CHECKING / try / suppress / `__main__` | importscan.py:168–283 | O-12 / O-13 / O-14 现状已满足 `[运行]`（见 §7 表） |
| 本地模块有界跟进 + 越界软链接不跟进 | importscan.py:350–374、:463–520 | O-03 / O-15（≤3 层）/ O-16 现状满足 `[运行][测试]`（`TestImportBuckets`） |
| 未用 import 不算 needed（AST 证明，副作用名单） | `figcapture.unused_imports` | `tests/test_unused_missing_import.py` |
| 声明读取：requirements / pyproject / PEP 723 / `-r` `-c` 有界跟进；editable / 直链 / 本地路径 / 锁文件 → `unsupported` 且 `blocked` | depresolve.py:568–1300 | 「editable 声明不转成 pip 包名」现状已满足 `[测试]`（`tests/test_dependency_plan.py::TestIntentGrammar::test_recognised_but_unsupported_constructs_are_named_not_dropped`） |
| 目标解释器事实（marker 环境、stdlib、已装发行包名→版本） | depplan.target_facts | 一个子进程 |
| 零执行的候选证据入口 + 建议 / 检查 / 采用三分 | projscan.environment_evidence / envadvice | ADR 0114 |
| 覆盖度判据（真 import） | userenvs.evaluate | 叠栈「自动检测」也用它 |
| 授权影响摘要 + 摘要绑定 + 认领幂等 + 采用互斥 + 多作用域互斥 | deprepair.impact_of / start_confirmed / unless_installing / _with_scope_check | ADR 0115 |
| 代事务（建代 → pip → pip check → 关键 import → worker 自检 → 切 active） | deprepair._run_generation | 失败旧代原样可用 |
| 环境占用互斥 | envlease | |
| 任务诊断白名单投影 | taskdiag + deprepair.diagnostic_projection | |
| 隐私 / 零写回 / 不写字节码 | `runtime.probe_args()`（`-B`）、`owned_env` | `tests/test_probe_leaves_no_trace.py` |

### 3.2 不足（有证据；未证实的写「不确定」）

1. 导入优先级不是 Python 的（M1 / M2 / M3）；没有 built-in / frozen 概念 `[运行][代码]`。
2. 本地包子模块不跟进（M4）→ `needed` 漏报，后果是落到「运行后缺包 → 有界重计划」的兜底，不是静默错图 `[代码]`（兜底机制见 ADR 0061 §四）。
3. 单一映射表无法表达多发行包（M5）；表外落 `unknown`，而在飞 #864 想在运行时按同名补（冲突，见 §5.2）`[运行]`。
4. 没有「已装元数据 → import 提供者」的静态证据（Q5）；所以也无从区分 editable / 本地 wheel / VCS / Conda 专有的已装包，现状下它们都只是「发行包名在 `installed` 表里 = satisfied」`[代码]`。
5. `probe_environment` 的 `__import__` 吞掉所有异常，`modules_ok=False` 不能区分「找不到」与「找到了但 import 炸了」（projectenv.py:~398–405）；计划里的 `module_not_found_in_user_environment` vs `environment_unusable` 现无法由现有数据区分 `[代码]`。
6. `envadvice.check` 不带 `modules`：检查只回答「环境健康」，不回答「对这个脚本够不够」；够不够只在叠栈的依赖门 / 自动检测路径里（`userenvs.evaluate`）才量，且两套缓存互不相通（§1.2）`[代码]`。
7. `userenvs._probe_cache` 键不含环境代（userenvs.py:546），而 `envadvice._verdicts` 含；同一路径被重建后，覆盖度缓存只靠 `RESET_HOOKS`（`reset_cache()`）清 `[代码]`；是否出过实际错误 `[不确定]`。
8. `importscan` 无预算 / 无 no_follow / 无占位文件处理（并入默认扫描前必须补）`[代码]`。
9. `GET /api/engine/dependencies` 会起选中的解释器（Q7 表）——与 `GET /api/engine/environment`「GET 不起解释器」的规则不一致；不是缺陷（属用户发起的依赖面板），但新解析器的静态结果**不应**走这个 GET `[代码]`。
10. 前端类型落后后端（`needed` 条目 4 vs 10 个字段）`[代码]`，属债务不是 bug。
11. `python -m` / native 的导入优先级未建模：`importscan.scan` 只有 `script` 参数；`TARGET_MODULE` 只在 native / bridge（`execspec.py:58`，`bridge_runner.run_module` 把 **cwd** 放 `sys.path[0]`），safe worker 永远是 `[脚本目录, 项目根]` `[代码]`。

## 4. 真正需要修改的位置 / 不需要重复实现的

**要改**（按 PR 归属，见 §7）：

* `importscan.py`：优先级与遮蔽（M1–M3）、包子模块跟进（M4）、`ImportClass` 增加可选字段、预算接线、`entry` 种类参数（脚本 / 模块）。
* `depresolve.py`：多发行包候选表（`cv2` 等）、`curated` 映射结构化（`distribution_candidates`）、`resolve` 与 `map_distribution` 的口径统一（在 #864 / #859 / #858 落地后再改）。
* 新增一个**纯文件系统**的发行包元数据读取器（建议新模块，不进 `projscan` 的禁用名单；从 `pyvenv.cfg` + 约定布局推 site-packages，只解析 `METADATA / top_level.txt / direct_url.json / RECORD` 文本，不执行、不 import、不读 `.pth` 内容以外的东西，有文件数 / 字节预算）。
* `projectenv.py` `_PROBE_SRC`：失败原因细分（找不到 vs 导入异常），只加字段，不改既有 `ok/code` 语义。
* `envadvice.check`：可选带 `modules`（脚本所需），结论带「覆盖度」；缓存键对齐环境代。
* `depplan.py`：`needed/missing/satisfied` 条目增加 `origin` 相关可选字段；多候选 / editable / 无元数据 → `unresolved` 状态而不是 `missing`（不新增 `status` 取值；用 `blocked` 的新理由或 `unknown` 的细化，二选一由 PR4 定，见 §8）。
* 前端：`api.ts` 类型、`preparationText.ts`、`PreparationCard`（叠栈）与 `projectPreparationStore`（叠栈）。

**不要重复实现**：

* dist-info 里发行包名 / 版本的读取（`_FACTS_SRC` 已有）——新读取器只补 `top_level / RECORD / direct_url`，且要与 `_FACTS_SRC` 的归一化键（PEP 503）一致；
* 环境体检（`probe_environment`）、worker 导入链判据；
* 环境代、`impact_digest`、认领幂等、`unless_installing`、`envlease`；
* 代事务与三层验证；
* 声明解析（`declared_intents`）；
* 零执行扫描的预算 / 单飞 / 取消 / 迟到作废（`scanbudget`、`ScanService`）。

## 5. 叠栈与在飞 PR

### 5.1 叠栈 #815–#820 对 P0 清单模块的改动（`git diff origin/main origin/onboarding/pr10-guide-card --stat -- <file>`）

| 文件 | 叠栈改动 | 对本工程的含义 |
| --- | --- | --- |
| `importscan.py` / `depresolve.py` / `depplan.py` / `managedenv.py` / `workdir.py` / `runtime.py` / `envlease.py` / `taskdiag.py` | **无** | PR1 / PR2 的核心文件在叠栈里不动 → **可直接基于 `main`**，叠栈合入时不冲突 |
| `deprepair.py` | +148/-12 | **自动检测模式**：`decide_environment_pinned` / `_detect_environment_unlocked`（用 `userenvs.evaluate` + `_plan_imports` 判「能跑」）、`user_environment_candidates` 随 `legacy_adoption_enabled()` 分流 |
| `pool.py` | +115/-18 | `EnvironmentDecision`、`_stops_when_unusable`、检测模式下 `resolve_worker_python` 失效记录一律作废 |
| `projectenv.py` | +47/-8 | **`adoption_mode()`：默认从 `confirm` 改为 `detect`**（ADR 0114 §六，用户 2026-10-06 裁决），`TRIGGER_AUTO_DETECTED`、`CONSENT_AUTO_DETECTED` |
| `envadvice.py` | +205/-2 | `adopt_candidate`、`adoption_fact`（报告里的 `environment` 事实） |
| `userenvs.py` | +14/-2 | `PROJECT_SOURCES` |
| `projscan.py` | +32/-5 | `linked` 目标判据（`probe.linked_scripts`） |
| `preparation.py` / `prepsession.py` / `execspec.py` | +124 / +121 / +7 | 会话化、`run_kwargs` |
| `web/src/components/ProjectScanBar.tsx` | **整文件删除**（由 `PreparationCard.tsx` 1237 行 + `projectPreparationStore.ts` 508 行取代） | **P6 UI 不能基于 main** |
| `web/src/store/{envStore,scriptRunStore,projectReadinessStore,depRepairStore}.ts`、`EngineEnvironmentCard.tsx`、`web/src/lib/api.ts`（+325/-12） | 均有改动 | 同上 |
| `DependencyPrepareDialog.tsx` | 无 | |

**含义**：

* **PR1、PR2 可以、也应该基于 `main`**（核心文件叠栈零改动；只在 `api.ts` 的类型处会有机械冲突，可后置）。
* **PR3（环境覆盖度）基于 main 会与叠栈的自动检测打架**：叠栈已经把「覆盖度 = `userenvs.evaluate`」接进检测模式，PR3 如果在 main 上另加一套覆盖度，等于做第二套。建议 **PR3 等叠栈合入 main 再开**（或基于 pr10，base 随栈改）。
* **PR4（`depplan` + 准备会话集成）依赖 `prepsession` 的叠栈版本** → 等叠栈。
* **PR6（UI）必须等叠栈**：`ProjectScanBar` 在叠栈中不存在，展示载体是 `PreparationCard`。
* **与计划口径的一处张力（需要维护者确认，见 §8-C2）**：计划验收场景写的是「发现 `.venv` **只建议不切换**」，这是 `main` 上 ADR 0114 的 `confirm` 模式；叠栈把默认改成 `detect`（用户点了准备 / 运行之后，自动检测并**直接采用**能跑的环境）。两者都满足「采用 ≠ 安装」和「默认扫描零进程」，但「只建议」在叠栈默认下不再成立。

### 5.2 在飞 PR 与本工程的关系

| PR | 基线 / 风险 | 内容 | 与本工程的关系 |
| --- | --- | --- | --- |
| #864 同名候选安装 | base=`pr10-guide-card`，`risk:high`，`full-ci` | `depresolve.resolve` 对表外名返回新来源 `same_name_unverified`（`INSTALLABLE_SOURCES` 增为 4 个），`NO_SAME_NAME` 禁区表，`IMPACT_VERSION` 1→2，装后 `_verify_same_name`（读 `top_level.txt` / RECORD 核验）；跑前 `map_distribution` 有意不猜 | **与 P5.4「禁止从 unknown 猜 PyPI 包名」及硬约束「不新增第四种自动可信来源」直接冲突**（详见 §5.3）；同时它的「装后读 top_level/RECORD」与 P2 重叠，应复用 P2 的读取器而不是各写一份 |
| #859 缺依赖三分类 | base=pr10，`risk:normal` | `pool.WorkerError.install_route ∈ installable/unresolvable/stdlib_missing`；`depresolve.resolve` 对标准库名返回 None | 与 PR1 一致（stdlib 不可装）；`INSTALL_ROUTE_*` 就是计划里「distribution_unresolved」一类的**现成载体**，P5 的错误状态应映射到它而非另起一套；`stdlib_install_route` 用宿主 `HOST_STDLIB`，与 PR1 的 built-in/frozen 区分有口径重叠 |
| #858 补 lxml 等 SAME_NAME | base=main，`risk:normal` | `SAME_NAME` +9 项、`CURATED` +`odf` | 纯数据，与 PR2 的表结构化（`distribution_candidates`）会有文本冲突；建议先合 |
| #855 诊断 recent_runs | base=main，`risk:normal` | `diagnostics` 记 recent_runs 结果分类 + 缺依赖现场 | 与 PR7 的诊断字段重叠：新增诊断字段要沿用其白名单口径（`deprepair.note_missing_dependency`，deprepair.py:2622 的 `ev()` 闭集校验） |
| #868 worker 日志结构摘要 | base=main，`risk:high` | 脱敏后保留异常类型 / 已知模块名 / 帧分类 | 与 P7 隐私约束同向；「已知模块名」闭集要与新解析器的模块名白名单对齐，避免用户私有模块名进诊断 |
| #856 位图提示 / #857 弹窗预检 | base=pr10，`risk:normal` | `rasterhint.py`（改 `importscan.py` 一处）、`dialogscan.py` | 与 PR1 在 `importscan.py` 有**行级**接触（#856 改动该文件）；PR1 开工时先 `git fetch` 看合并状态再 rebase |

### 5.3 #864 与 P5.4 的冲突点与建议

* **冲突点**：计划 P5.4 与硬约束：「禁止从 unknown import 猜 PyPI 包名；不新增第四种自动可信来源；表外的包需用户在确认界面接受包名与版本后走现有 `user_specified` 路径」。#864 把「表外名 → 按同名当 PyPI 包名」做成 `resolve` 的默认结果，来源 `same_name_unverified` 进入 `INSTALLABLE_SOURCES`，`DependencyRequirement.installable` 因而为真（depresolve.py diff：`confidence in INSTALLABLE_CONFIDENCES`）。它的缓解（只在运行时真的 import 失败之后、只进受管环境、wheel-only、用户点击授权、装后核验、禁区表）是真实的，但：①「猜」仍发生在后端解析层并进了「可一键安装」的来源集合；②核验靠 `top_level.txt`/RECORD 推断，无元数据的发行包被判不提供（回滚）——安全侧失败，但授权前用户看不到「这是猜的」之外的证据；③`NO_SAME_NAME` 人工小表是第二张需要维护的静态表。
* **建议（供维护者裁决，不是已决定）**：
  1. 若坚持 #864 的产品方向：把 `same_name_unverified` **不放进 `INSTALLABLE_SOURCES`**，而作为「候选名预填 + `user_specified` 路径」——后端只给 `suggested_distribution`，用户在确认界面接受包名（并能改）与版本后，作为 `user_specified` 提交，核验沿用 #864 的 `_verify_same_name`。这样不新增自动可信来源，P5.4 的「用户确认包名与版本」成立；
  2. `_verify_same_name` 与 P2 的元数据读取器合并为同一个读取模块（避免两份 `top_level.txt` 解析）；
  3. `IMPACT_VERSION` 只升一次：#864 若先合就升到 2，P5 再加字段升到 3，并在 PR 正文里互相引用；
  4. #864 保持「跑前扫描不猜」（它自己已钉 `test_pre_run_scan_still_does_not_guess`）——这与 PR1 的 O-20 一致，可作为共同的回归用例。

## 6. 兼容风险

| 风险 | 说明 / 对策 |
| --- | --- |
| `needed` 集合变化改变安装集合 | M1–M4 的修复会让一部分脚本的 `needed` 增减（如 `json.py` 不再算 stdlib、包子模块的第三方被发现）。**这是行为变更不是纯加字段**：`inputs_digest`、`identity`（受管环境代目录名）会变，已有受管环境的代名与「第二次打开不重装」需回归（`test_dependency_transaction.py::test_second_open_does_not_install_again`）。PR1 把「分类修复」与「新增字段」分成两个提交，便于评审与回滚 |
| 协议 | 只加可选字段；`plan_version` 不升（加字段不升，改语义才升，`execspec` 同约定）；前端类型同步；老前端忽略未知字段 |
| 错误码 | 不新增 / 不改名已发布码；新增的 `blocked` 理由要同步 `BLOCK_REASONS` ↔ `api.ts` ↔ i18n ↔ ADR 0061 §三 |
| 同源对 | 见 Q10；建议 PR4 把「`BLOCK_REASONS` ↔ 前端」登记进 `same-origin-pairs.md` 并给看护用例 |
| 隐私 | 新字段中的路径一律项目相对 / 不透明 id；诊断白名单 `deprepair.diagnostic_projection` 与 `taskdiag` 同步；测试里加「载荷不含绝对路径 / dist-info 内容」断言 |
| 零执行 | 并入默认扫描的任何新代码须过 `test_project_scan_zero_exec` 全部 7 条，并新增「dangerous_package 不被初始化」哨兵 |
| 叠栈冲突 | PR1 / PR2 基于 main；`api.ts`、`deprepair.py`（`IMPACT_VERSION`、`_plan_imports`）、`userenvs.py` 是叠栈 / 在飞 PR 的热区，改到这些文件的 PR 要晚于或显式叠在它们之上 |
| 跨平台 | 大小写（M3）、`.pyd`/`.dll`/`.so` 后缀（`_EXT_SUFFIXES` 现仅 `.so/.pyd/.dylib`，`.dll` 不在内 `[代码]`）、Windows 旧 Python 3.7 不误认（`PYTHON_MIN` 判据在 `probe_environment`）、符号链接 / junction（`scanbudget.redirected_component`）、中文路径 / 空格（`test_project_scan_zero_exec.World` 已用 `项目 空格`） |
| 性能 | 并入默认扫描后总预算仍受 `scanbudget.MAX_SECONDS=20` / `MAX_SOURCE_BYTES=32 MiB` / `MAX_SCRIPTS=400` 约束；`importscan` 的 24 文件 / 3 层上限要在多脚本（100）下按脚本累计，否则 100 脚本 × 24 文件会超预算 `[不确定：未做过测量]` |

## 7. PR 拆分与测试计划

> 原则：能复用的复用；每个 PR 能独立合入、独立回滚；新增判据先提交再做反证（变异→退出码 1→还原→0），按 `docs/ci/pr-review-tiers.md` 档位调用 Codex。

### 7.1 O-01…O-20 现状（`[运行]` 对 main 实测，是否已满足）

| 编号 | 现状 | 归属 |
| --- | --- | --- |
| O-01 numpy 第三方 | 满足 | 回归 |
| O-02 docx → python-docx | 满足（`CURATED`） | 回归（并加 #858 之后的复测） |
| O-03 同目录 utils.py 本地 | 满足 | 回归 |
| O-04 项目内 package 为本地 | 满足（分类对，子模块见 O-05） | PR1 |
| O-05 包内相对导入解析 | **不满足**（M4：相对导入被跳过，子模块不跟进） | PR1 |
| O-06 本地 json.py | **不满足**（M1） | PR1 |
| O-07 built-in sys | 满足但仅因 `sys` 在 stdlib 名单；无 built-in/frozen 概念 | PR1（补语义） |
| O-08 `from pkg import x` 不把全部 x 当子模块 | **部分**：不会误认，但真子模块也不跟进（M4） | PR1 |
| O-09 namespace package | 部分：判本地（无 `__init__.py` 目录且含 .py）；跨多路径的命名空间包未建模 | PR1（保守）+ PR2 |
| O-10 editable 不当 PyPI 依赖 | 声明侧满足；**已装**的 editable 无证据 | PR2 |
| O-11 cv2 多发行包不猜 | **不满足**（M5） | PR2 |
| O-12 动态导入变量 unknown | 满足（`dynamic`）；别名调用漏检（M6） | PR1 |
| O-13 try/except 可选 | 满足 | 回归 |
| O-14 TYPE_CHECKING | 满足 | 回归 |
| O-15 本地模块递归传播 | 满足至 3 层，之后 `truncated` | PR1（固化口径） |
| O-16 软链接越界 | 满足 | 回归 |
| O-17 `python -m` 尊重包上下文 | **未建模**（仅 native / bridge 有 module 目标） | PR1（加 `entry` 参数，默认不变） |
| O-18 同名冲突返回真实优先级 / 歧义 | **不满足**（M2、M3） | PR1 |
| O-19 不完整元数据 unverified | 无元数据概念 | PR2 |
| O-20 未安装模块不猜 PyPI 名 | 满足（`map_distribution`，`test_pre_run_scan_still_does_not_guess` 在 #864 里新增） | 回归；与 #864 联动 |

### 7.2 PR1–PR7

**PR1 静态来源解析核心（P1）** —— 基线 **main**；建议 `risk:normal`（改分类行为但边界清楚；因 `needed`/`identity` 会变，PR 正文要写清并附回归）
* 范围：修 M1/M2/M3/M4/M6；`ImportClass` 增加可选字段（`origin_kind`、`resolution_status`、`evidence`、`shadowing`、`warnings`、`source_line`），`to_payload` 追加字段而不改旧键；`entry` 参数（script / module）；built-in 名单仅在能确认目标版本时使用，否则 `unverified`；`importscan.scan` 接 `scanbudget`（no_follow / 占位文件 / 字节预算）；不起进程、不 `find_spec`、不读 dist-info。
* 文件：`src/tavotto/engine/importscan.py`（主）、`src/tavotto/engine/depplan.py`（`to_payload` 透传，不改判据）、`web/src/lib/api.ts`（可选字段类型，机械改动）、`tests/test_dependency_plan.py`（更新 `TestImportBuckets` 里与 M1 相关的预期）、新增 `tests/test_import_origin_static.py`。
* 测试：O-04 / 05 / 06 / 07 / 08 / 09 / 12 / 15 / 16 / 17 / 18 / 20 + O-01 / 03 / 13 / 14 回归；P7.2 静态半边（`dangerous_package/__init__.py` 写 `SIDE_EFFECT`，扫描 `from dangerous_package import submodule` 后断言文件不存在；复用 `test_project_scan_zero_exec.no_process_no_network` 断言零进程 / 零网络）；性能 fixture 5/30/100 脚本的静态半边（读文件量、耗时）。
* 合入门槛：本地 `ruff check . && ruff format --check .`、针对性 pytest（上列 + `test_unused_missing_import.py`、`test_dependency_transaction.py::test_second_open_does_not_install_again`、`test_project_scan*.py`、`test_import_architecture.py`）退出码 0；每条新判据做一次变异；Codex 一次完整评审。
* 与在飞 PR：#856 改 `importscan.py`、#864/#859 改其注释 → 开工与提交前各 `git fetch` 一次。

**PR2 发行包元数据映射（P2）** —— 基线 **main**（核心文件叠栈零改动）；`risk:normal`；建议在 #858 合入后开工（表结构冲突）
* 范围：新增**纯文件系统**元数据读取器（建议 `src/tavotto/engine/distmeta.py`，纯标准库，有文件数 / 字节预算，只读 `*.dist-info/{METADATA,top_level.txt,direct_url.json,RECORD(前 N 行)}`，site-packages 由 `pyvenv.cfg` 的 `home`/`include-system-site-packages` 加约定布局推出，推不出就 `unverified`，**不执行 `.pth`、不 import、不起解释器、不调 `packages_distributions()`**）；`depresolve` 增加多发行包候选表（`cv2` → `opencv-python / -headless / -contrib / -contrib-headless`，保留全部候选、不挑）；输出 `observed_distribution / observed_version / declared_requirement / provenance / compatibility`；editable / 本地 wheel / VCS / 无元数据 → 状态而非 pip 名。冲突不静默覆盖。
* 文件：`distmeta.py`（新）、`depresolve.py`、`importscan.py`（填 `distribution_candidates`）、`tests/test_import_origin_metadata.py`（新）；新增 engine 模块要过 `tests/test_import_architecture.py` 并更新 `src/tavotto/AGENTS.md` 速查行与 `docs/rules/backend/dependency-repair-and-packages.md`。
* 测试：O-02 / O-10 / O-11 / O-19；P7.2 `.pth` / `sitecustomize` 哨兵（site-packages 里放会写文件的 `.pth` 与 `sitecustomize.py`，读取后断言未执行）；多发行包 + 版本冲突报告不静默覆盖；metadata 损坏 / 超大 / 软链接 dist-info。
* 合入门槛：同 PR1；另附「文件系统读取」变异（去掉预算 / 去掉 no_follow 各一次）。
* 若 #864 先合：`_verify_same_name` 改为调用本读取器（或在本 PR 里替换），并把 `same_name_unverified` 的处置（§5.3）同时收口。

**PR3 环境覆盖度（P3）** —— 基线：**叠栈合入 main 之后**（或基于 `pr10-guide-card`，base 随栈改）；`risk:high`（Python 环境 / 起解释器）
* 范围：`envadvice.check` 可选带脚本所需 `modules` → 覆盖度（`modules_ok` + 失败原因细分：`not_found` vs `import_error`，`_PROBE_SRC` 只加字段）；覆盖度缓存键与环境代对齐、与 `userenvs._probe_cache` 合一；明确标注「已执行用户包的 `__init__`」（授权环境检查不得宣称无副作用）；`module_not_found_in_user_environment / module_missing_in_target_environment / environment_not_checked / environment_unusable` 作为结构化 detail（不新增发布码）；**不重做**自动检测，只让它读同一份覆盖度。
* 文件：`projectenv.py`（`_PROBE_SRC` 字段）、`envadvice.py`、`userenvs.py`、`deprepair.py`（`_plan_imports` 的下游）、测试。
* 测试：环境 fixture A–J（A 无第三方 / B `.venv` 装齐 / C 缺一个 / D bundled 缺而用户环境有 / E 版本冲突 / F 同 base 两个 venv / G 被重建 / H 不支持的 Python / I matplotlib 在但 worker import 失败 / J 用户全局解释器设置），显式选择不被覆盖、不因 realpath 合并、unsupported 与 missing 分对；现有 `test_environment_adoption.py`、`test_user_environments.py`、`test_environment_health_parity.py` 保持绿并复用其夹具；P7.2 候选环境检查的「有副作用」声明测试。
* 合入门槛：`full-ci`（Windows 重腿）全绿、变异清单逐条退出码、未验证范围写全；Codex 一次 + 一次定向复核。

**PR4 依赖计划与准备会话集成（P4）** —— 基线：叠栈合入后；`risk:high`（改 `depplan` 判据与门）
* 范围：`depplan.plan` 消费 PR1/PR2 的来源与候选：多候选 / editable / 无元数据 → `unresolved`/新 `blocked` 理由（二选一，见 §8-C3），**不再把 M5 这类情形算成 `missing`**；`prepsession.checks_of` 的 `dependencies` 项带来源事实；`GET /api/engine/dependencies` 不承担静态来源结果（另走零执行端点或并入 `projscan` 报告）；登记 `BLOCK_REASONS ↔ api.ts` 同源对。
* 测试：`tests/test_dependency_plan.py` 新增 `TestPlan` 用例（M5 的 `plan` 级回归即 §2-Q3 的复现）；`tests/test_preparation_session_dependencies.py` 黄金向量 `tests/golden/preparation_session_vectors.json` 新增事实条目；`tests/test_foundation_dependencies.py` 回归。
* 门槛：同 PR3。

**PR5 安装方案与验证（P5）** —— 基线：叠栈合入后，且 #864 已决（§5.3）；`risk:high`（往磁盘装第三方代码的边界）；**范围可缩小，甚至并入 PR4**
* 现状已满足（有用例，不要重做）：计划先行 + 影响摘要（目标 / 集合 / 约束 / 联网 / 是否新建 / 是否改用户环境 / 写入范围 / 回滚性质，`impact_of`）；摘要必填与校验；认领幂等；采用互斥；作用域互斥；失败旧代可用；原地改 venv 披露不可完整回滚；装后关键 import + worker 自检；内置 runtime 不可改。
* 真正要做的：①装后「发行包与版本」核验对照 PR2 的 `observed_*`（现有 `_versions_of` / `installed_version` 已有读数，只缺对照）；②确认界面接受包名与版本的 `user_specified` 路径（若采纳 §5.3 建议 1）；③`IMPACT_VERSION` 升版并加 `origin` 相关披露（需新增一类影响才升）；④把 13 条里「未逐条核实」的补齐（见下表）。
* 安装与并发 13 条映射（`[测试]` 为现有用例）：

| # | 条 | 现状 |
| --- | --- | --- |
| 1 | 未授权不装 | `test_dependency_impact.py::TestEveryExecutionEntryRequiresTheDigest`、`...::test_the_prepare_endpoint_requires_the_displayed_digest` |
| 2 | bundled 不可改 | `test_dependency_repair.py::test_the_bundled_runtime_is_never_a_mutation_target` |
| 3 | 同 digest 只一个安装 | `test_dependency_impact.py::...test_two_confirmations_of_one_impact_run_one_job_and_both_hear_the_end`、`...test_a_single_package_plan_is_claimed_once` |
| 4 | 确认后集合变化阻止旧计划 | `...test_a_world_that_changed_after_confirming_is_refused_and_the_plan_is_dropped`、`...test_inputs_that_differ_from_the_disclosure_are_never_installed` |
| 5 | 环境选择变则旧计划失效 | `...test_a_plan_made_before_the_user_adopted_another_environment_never_runs` 等三条 |
| 6 | 安装中不能交错采用 | `...test_changing_the_project_environment_is_refused_while_an_install_runs`、`...test_the_adoption_endpoint_waits_for_the_install_instead_of_overwriting_it` |
| 7 | 取消 / 超时 | `test_dependency_transaction.py::test_cancel_during_install_leaves_no_active_environment`、`::test_cancel_accepted_during_the_selftest_is_honored`；超时 `tests/test_pypi_slow_fallback.py` |
| 8 | 断网重试 | `test_dependency_repair.py::test_a_failed_pip_run_leaves_the_requirement_retryable` 等 |
| 9 | 受管失败旧代可用 | `test_dependency_transaction.py::test_selftest_or_consistency_failure_keeps_the_old_generation`、`::test_resolver_conflict_leaves_the_previous_generation_active` |
| 10 | 原地改 venv 披露不可完整回滚 | `test_dependency_impact.py::...test_a_user_venv_install_that_imports_badly_is_not_rolled_back_and_says_so` |
| 11 | 装成功 import 失败不 ready | `test_dependency_transaction.py::test_key_import_failure_after_install_is_not_activated` |
| 12 | 项目隔离 | `::test_two_projects_prepare_concurrently_and_independently`、`test_dependency_impact.py::...test_a_tool_script_s_missing_package_never_enters_an_independent_plot_s_plan` |
| 13 | 作用域冲突守 ADR 0115 | `tests/test_dependency_scope.py` 全文件 |

→ 13 条**全部已有对应用例**，PR5 不需要新增这 13 条本身，只需增加「针对新增披露字段 / `user_specified` 路径」的同类用例，并确认这些用例仍绿。（用例名里的 `...` 表示所在类，名字以文件为准。）

**PR6 UI（P6）** —— 基线：**叠栈合入后**（或 pr10）；`risk:normal`
* 范围：五种展示态（已就绪 / 发现更合适的环境 / 可以自动修复 / 需要确认 / 尚无法确定）全部来自后端事实，落在 `PreparationCard` / `projectPreparationStore`（叠栈）与 `EngineEnvironmentCard`、`DependencyPrepareDialog`；一句话 + 一个主按钮其余折叠（用户硬性要求）；项目代际与请求序号规则沿用 `projectPreparationStore`/`envStore` 现有写法，不新增并行判据；i18n 两语种 + `pnpm i18n:types`。
* 测试：`PreparationCard.test.tsx` 的「每状态 × 每语种一句话 + 至多一个主按钮」矩阵新增 5 态；`envStore` / `depRepairStore` 的项目切换用例；前端不得出现解析逻辑的结构性断言（沿用 `envAdvice.test.ts` 的口径）。
* 门槛：`pnpm test`、`pnpm build`、`pnpm i18n:check` 退出码 0（别用 `tsc --noEmit`）。

**PR7 E2E + 跨平台 + 诊断 + 文档 + FINAL_ACCEPTANCE（P7/P8）** —— 基线：叠栈合入后；`risk:low`~`normal`（测试 / 文档为主）
* 范围：E2E 三条（①复用现有环境：导入→扫描→建议→检查候选→选现有 Python→验证→运行→捕获 Figure→编辑；②缺第三方→修复方案→确认影响→建受管环境→安装→worker 验证→重跑→捕获；③来源无法验证→不装→解释→后续选项）；P7.5 跨平台矩阵；P7.7 性能 fixture 5/30/100 脚本（记录扫描总耗时、解析耗时、文件读取量、缓存命中率、内存峰值、额外子进程数——**默认扫描候选环境子进程 = 0**）；诊断字段（沿用 #855 / #868 的白名单口径）；P8：ADR（新号取当时 `docs/adr/` 最大号 + 1，派工时预分配防撞号）、rules 与 `AGENTS.md` 速查行、`docs/user-guide/python-environment-preparation.md`、`FINAL_ACCEPTANCE.md`（回答 A / B / C 三问）。
* 门槛：E2E 走现有 `posix-e2e` / `windows-exe-smoke`；`tests/test_agents_rules_index.py`、`tests/test_docs_references.py` 绿。

### 7.3 合并 / 缩小建议

* PR1 与 PR2 可并行（文件不重叠，`importscan.py` 仅在 PR2 填一个字段，建议 PR2 在 PR1 之后）。
* PR5 的绝大部分目标现有实现已满足（上表），建议**缩小为 PR4 的一个提交**或一个很小的独立 PR，不要为它单独走一轮 `risk:high` 评审，除非 §8-C1 决定改动 #864 的来源模型。
* 叠栈迟迟不合入时，PR3 / PR4 / PR6 可以基于 `onboarding/pr10-guide-card` 叠开，但 base 要随栈改，并预期 `api.ts`、`deprepair.py`、`preparation.py`、`prepsession.py` 的机械冲突。

## 8. 需要维护者裁决

* **C1（冲突）#864 的同名候选 vs 计划 P5.4「禁止猜 PyPI 包名 / 不新增第四种自动可信来源」**：保留 #864 原样 / 改为「预填 + `user_specified`」（§5.3 建议 1）/ 撤回？这决定 PR2 / PR5 的范围。
* **C2（口径）计划验收场景写「发现 `.venv` 只建议不切换」，叠栈默认已是 `detect`（能跑就直接用）**：以哪个为本工程的验收口径？两者都满足「采用 ≠ 安装」「默认扫描零进程」。
* **C3（设计）多发行包 / editable / 无元数据在联合计划里的落点**：新增 `blocked` 理由（要改 `BLOCK_REASONS` ↔ 前端 ↔ ADR 0061 §三），还是只在 `unknown`/`possible` 条目里加状态字段（不改 `blocked` 闭集）？前者语义更强但同源面更大。
* **C4（顺序）PR3 / PR4 / PR6 是否等叠栈合入再开**，还是基于 `pr10-guide-card` 叠开。
* **C5（范围）`GET /api/engine/dependencies` 会起解释器（§3.2-9）**：新静态结果是否要求走零执行端点 / 并入 `projscan` 报告（本文建议是），还是允许复用这个 GET。

## 9. 复现（审计时跑过的片段，均在 `origin/main` 代码上，Python 3.13.11）

* M1–M4、M6、O-系列：对 `importscan.scan(tmpdir, "s.py")` 构造 fixture（`json.py`、`dup.py`+`dup/__init__.py`、`Utils` vs `utils.py`、`pkg/__init__.py`+`pkg/a.py`、`il.import_module`）并打印 `classes`；并用真 `python s.py` 对拍 `json` 遮蔽、大小写、包优先。
* M5：`importscan.map_distribution("cv2", {"opencv-python-headless": ""})`；`depplan.plan(root,"s.py",facts=TargetFacts(installed={"opencv-python-headless":"4.9.0",…}),target_kind=TARGET_PROJECT_VENV)` → `status=ready, missing=['opencv-python']`。
* 脚本保存在审计会话草稿目录，未入库（它们是一次性探针，不是门禁；PR1 的正式用例要重新写成带变异反证的测试）。
