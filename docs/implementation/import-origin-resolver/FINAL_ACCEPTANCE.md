# Import Origin Resolver — 最终验收（PR7，P7 / P8）

> 范围：把 PR1–PR6 合入 main 的工作按维护者计划（2026-10-08）逐条验收，并回答三个问题。**证据分四级**，沿用 `00_BASELINE.md` 的口径：
> `[运行]` 本次在本机真跑过（命令与 rc 在 §4 / §6 / §8）；`[测试]` 有现成用例钉着（给出 `文件::类::用例`，本文里每个引用都由脚本核过它存在）；
> `[CI]` 合入前 PR 页面的 status check 汇总（`gh pr view --json statusCheckRollup`，2026-10-11 读取）；`[代码]` 读代码得出、未运行。没有证据的不写成事实。
>
> 本文基线：`origin/main` `3db9c9cc5`（PR1–PR6 全部已合入）；本 PR 分支 `docs/import-origin-pr7-acceptance`（提交列表见 §2）。本机：Darwin 27.0 arm64、Python 3.13.11、Playwright chromium。
> **本分支未推送，PR7 新增的内容尚无 CI 结果**（§5 如实标出）。

## 0. 三个问题的最终回答

**A. 现在 Tavotto 能否在运行前准确识别用户项目里的本地模块与第三方包？— 能；确定结果与静态推断分得开，判不出的一律标「未确定」而不猜。**

* **确定结果**（有静态证据、且只有一个答案，`resolution_status = resolved`）：标准库 / built-in / frozen（`origin_kind`）、项目里的本地模块与包（含按真实 Python 的优先级：目录包 > 扩展模块 > 单文件 > 命名空间目录；本地 `json.py` 遮蔽标准库；包内相对导入；中文模块名；符号链接不越界）、已安装发行包经 `top_level.txt` / `RECORD` / editable finder 确认的唯一提供者（`installed_confirmed`）。证据：O-01…O-20 全部有用例（§4.1），并对 `python script.py` / `python -m` 做了**真实解释器对拍**（`tests/test_import_origin_static.py::TestRealPythonParity`）。
* **静态推断**（有候选、缺确认，`unverified` / `ambiguous`）：映射表（curated）给出的候选、命名空间目录、目标解释器的内建名单没量到、搜索被截断或预算用尽（`search_complete=False`）、`.pth` 里未建模的 `import` 钩子、静态索引读不全。这些**不进 `missing`、不进安装集合**，如实出现在联合计划的 `origins[]`。
* **仍是推断、没有解决的**：动态导入只当候选（`dynamic`）；zip / egg 里的名字静态读不到；真 Windows 的路径与冻结运行时形状没有真机证据（§9、§10）。

**B. Tavotto 能否优先复用用户现有 Python 环境，避免不必要的安装？— 能，有可复现的真浏览器证据。**

`web/e2e/import-origin.spec.ts` ①：项目自带 `.venv` 已装齐 → 扫描（零执行，候选只是建议、未检查、`consent: none`）→ 明确的「检查候选」（真起候选、标注 `executes_user_code: true` / `side_effect_free: false`，覆盖度 `covered`，docx / matplotlib / numpy 均 `found`）→ 经卡片运行，脚本自己写的执行日志里 `sys.prefix` 就是项目 `.venv`、`cwd` 是项目根、数据是 `data/results.csv`、`docx` 来自 `.venv` 自己的 site-packages；**没有建受管环境、没有任何 `.dist-info` 被装进去**（§6）。复现命令见 §6。后端侧：采用 ≠ 安装、同一份覆盖度缓存、环境 fixture A–J（§4.3）。

**C. 当确实需要依赖安装时，能否通过一次明确的用户授权，在合法隔离环境中完成安装、验证并运行？— 能，有真实端到端证据。**

`web/e2e/import-origin.spec.ts` ②：缺 `python-docx` → 卡片一句话 + 一个主按钮，一行说明「python-docx · 装到 Tavotto 目录」（后端的实际影响：`creates_environment: true`、`modifies_user_environment: false`，`utils` 不在安装集合里）→ 一次点击授权 → **真 pip 从本地离线 wheelhouse** 装进 Tavotto 新建的受管环境 → worker 验证 → 点「运行」→ 捕获 Figure；磁盘上受管环境里有 `python_docx-1.1.2.dist-info`、执行日志里解释器在受管环境内、**宿主解释器仍然 import 不到 docx**、项目里没有被写进任何东西。④ 是「表外的包」那一档：预填 → 用户确认包名 → 确认页标「用户指定，未经核实」→ 装 → 装后核验（先填错包名被核验拦下、不激活，改对后装好并运行）。**限定**：e2e 里受管环境靠离线补丁建出（基础栈换空表、宿主 site-packages 用 `.pth` 挂进去、pip 只取本地 wheel），生产上的隔离与最小性由 `tests/test_dependency_repair.py::test_managed_venv_creation_is_isolated_and_minimal` 另行钉死；联网下载 matplotlib 等的冷机路径不在本 PR 的证据里（属 T11）。

## 1. 最终架构

```text
  脚本 / 项目文件
        │  ast + os.scandir（不跟随链接）+ pyvenv.cfg + *.dist-info + 项目声明 —— 零执行、零子进程、不联网
        ▼
  importscan.scan(root, script, entry=, builtin=, ext_suffixes=, dists=)  ──►  ImportClass{bucket, context, needed（旧，不变）
        ▲                                                                       + origin_kind, resolution_status, evidence,
        │ distmeta.Index（环境的静态发行包索引：层顺序 / .pth / editable finder /             shadowing, distribution_candidates,
        │   top_level.txt / RECORD / direct_url.json / conda-meta）                           observed_*, declared_*, …）
        ▼
  depplan.plan(facts=TargetFacts, dists=Index, coverage=只读缓存, user_specified=)  ──►  JointPlan{missing, satisfied, origins[],
        │                                                                                         user_specified[]}   plan_version 不升
        ▼
  deprepair.create_joint_plan ─► impact（impact_digest，IMPACT_VERSION=2）─► prepare(confirmed_impact)
        │                                 └─ 执行期重量事实后：层指纹 / 提供者指纹重比 → repair_plan_stale
        ▼
  managedenv 受管环境换代 ｜ 项目 .venv 就地（显式授权、披露不可完整回滚）
        └─► _verify_imports ─► _verify_distributions（装后「发行包与版本」核验）─► worker 自检 ─► 激活

  授权路径（会执行用户包的 __init__）：envadvice.check(modules=) ─► projectenv.probe_environment（起候选、真 import）
        └─► userenvs._probe_cache（环境覆盖度**唯一一份**；键含环境代）

  展示：prepsession.checks_of（detail.origins：计数 + 闭集原因码）─► web/src/lib/importOrigin.ts（五种展示态，零解析）
```

每个关口的「唯一权威」没有换：`importscan`（静态导入分析）、`depresolve`（包名可信解析）、`depplan`（联合计划）、`envadvice`（建议 / 检查 / 采用）、`pool`（实际 worker 解释器）、
`deprepair`（安装与验证）、`managedenv`（受管环境生命周期）、`prepsession`（准备会话）；前端只展示后端事实。新增的只有 `engine/distmeta.py`。决策与理由：[ADR 0119](../../adr/0119-import-origin-static-evidence.md)。

## 2. 修改文件与 PR 列表

| PR | 提交（main 上） | 范围 | risk | 主要文件 |
| --- | --- | --- | --- | --- |
| #880（PR0 + PR1） | `d7577b5f6` | P0 审计基线；静态来源解析核心：按真实 Python 的导入优先级、`ImportClass` 追加字段、`entry`（script / `-m`）、`scanbudget` | high | `engine/importscan.py`、`scanbudget.py`；`tests/test_import_origin_static.py`、`…_safety.py`、`…_namespace_and_links.py`、`…_no_follow_audit.py`、`…_path_confinement.py`、`…_windows_links.py`、`…_wrapper_preloaded.py` |
| #882（PR2） | `4d11c7b8d` | 发行包元数据映射（`distmeta`，纯文件系统，只观测） | normal | `engine/distmeta.py`、`depresolve.py`、`importscan.py`；`tests/test_import_origin_metadata.py` |
| #906（PR3） | `b63248303` | 环境覆盖度（`not_found` / `import_error` 分开；授权检查如实标注已执行）；#889 三条 | high | `projectenv.py`、`envadvice.py`、`userenvs.py`、`deprepair.py`、`distmeta.py`；`tests/test_environment_coverage.py` |
| #920（PR4） | `59a90e27e` | 依赖计划消费来源与覆盖度（`origins`）、目标解释器事实（#888）、#908 | high | `depplan.py`、`prepsession.py`、`importscan.py`；`tests/test_dependency_plan.py`、`test_import_origin_target_facts.py` |
| #930（PR5a） | `51d233cdd` | PR4 遗留 #922 #924 #925 #929：层指纹绑定执行期过期检查、歧义先于映射、未建模钩子、精确扩展后缀、保留 sys.path 顺序 | high | `depplan.py`、`distmeta.py`、`deprepair.py`；`tests/test_import_origin_plan_backlog.py` |
| #933（PR5b） | `e090923b4` | 预填 + 用户确认（`user_specified`）、`IMPACT_VERSION` 1→2、装后发行包与版本核验、#931 #932 | high | `depplan.py`、`deprepair.py`、`depresolve.py`；`tests/test_import_origin_user_specified.py` |
| #934（PR6） | `3db9c9cc5` | 前端：五种展示态、确认包名框、来源与核验原因的人话（零解析） | normal | `web/src/lib/importOrigin.ts`、`components/{ImportOriginDialog,OriginDetails,PreparationCard,DependencyPrepareDialog}.tsx`、`store/depRepairStore.ts` |
| **PR7（本分支）** | 见下 | E2E 四条、测试矩阵映射与缺口、性能 fixture、诊断隐私、跨平台、ADR 0119、规则与速查、用户文档、本文 | normal | 见下 |

相关但不属于本工程提交的：#858（lxml 等报告 / 文档类常用包登记进 curated 表）、#859（缺依赖分三类引导）已合入；#864（表外缺包按同名从 PyPI 安装）**已关闭未合入**（裁决 C1，由 PR5b 的「预填 + 用户确认」取代）。

**PR7 提交**（本分支，基于 `origin/main` `3db9c9cc5`）：

* `e10b5e482` test(support): 离线世界零件拆出 pytest 依赖（造本地 wheel、离线建受管环境），供真浏览器 e2e 的被测进程复用
* `15a197ee5` fix(importscan): 非 ASCII 的本地模块名（数据.py）按真实 Python 当本地模块，并补跨平台形状测试（路径拼写 / 源码编码 / Windows 文本形状）
* `b94958006` test(diagnostics): 来源 / 覆盖度 / 核验字段不出名字——四个诊断出口的金丝雀、键闭集与 AST 结构钉住
* `b5e1aa445` test(perf): 5 / 30 / 100 脚本的性能 fixture——默认扫描零子进程、不读数据与 .venv、读取线性有界、增量零解析
* `bd15396fc` test(e2e): Import Origin 验收四条——复用已有环境 / 受管环境安装 / 来源无法验证 / 需要确认（真后端、真 worker、真浏览器）
* `45faad47d` style(test): 诊断隐私用例沿用兄弟文件的 F811 文件级豁免写法
* `fe01f2f51` docs(import-origin): ADR 0119、规则收口、用户文档与 FINAL_ACCEPTANCE（A / B / C 三问、矩阵映射、性能、隐私、已知缺陷与 issue 现状）
* `90f863580` docs(agents): 依赖速查行加 ADR 0119 / 来源解析默认零执行 / 三个新看护文件；诊断隐私用例钉住 impact.digest 是唯一由包名派生的值（#918）

（列表截至写下它的那一刻；其后只有一个「填入运行结果」的文档提交。作者 `erwanjun`，无 `Co-Authored-By`。）

## 3. 新旧识别能力比较

| 场景（BASELINE §2 Q3） | 升级前（`[运行]` 复现） | 现在 | 看护 |
| --- | --- | --- | --- |
| M1 本地 `json.py` 与标准库同名 | 归 `stdlib`，不跟进 | `project-local` + `shadowing=stdlib`，跟进它的 import | `tests/test_import_origin_static.py::TestPriority::test_o06_a_local_json_py_shadows_the_stdlib_and_is_followed`、`::TestRealPythonParity::test_json_shadowing_package_priority_and_case`（对拍真 Python） |
| M2 `dup.py` 与 `dup/__init__.py` 同在 | 选了 `dup.py` | 目录包胜出（与真实 Python 一致） | `::TestPriority::test_o18_a_package_directory_wins_over_a_same_named_module` |
| M3 `import Utils` 而磁盘是 `utils.py` | 判本地（大小写不敏感的文件系统） | 逐字匹配目录清单，不匹配 | `::TestPriority::test_o18_import_names_match_the_directory_listing_exactly` |
| M4 包内相对导入 / 子模块里的第三方 | 相对导入被跳过，子模块不跟进 | 按包上下文解析并传播上下文 | `::TestLocalPackages::test_o05_relative_imports_resolve_against_the_package_context`、`::test_o08_from_pkg_import_x_follows_only_names_that_are_submodules` |
| M5 `cv2`：已装 headless，仍判缺 `opencv-python` | `ready` + `missing=['opencv-python']` | 已装的备选发行包算满足；都装着 = 歧义；一个都没装仍按 curated 缺 | `tests/test_dependency_plan.py::TestPlanOrigins::test_m5_cv2_with_the_headless_build_installed_is_not_missing`、`tests/test_import_origin_metadata.py::TestSeveralProviders::test_o11_two_installed_cv2_distributions_are_both_kept_and_none_is_picked` |
| M6 别名 `il.import_module(...)` | 不产生 import，不进 `dynamic` | 记为动态候选，不当必需依赖 | `静态::TestDynamicImports::test_o12_alias_calls_are_dynamic_candidates_not_unconditional_dependencies` |
| 已安装的 editable / 本地 / VCS / Conda 包 | 无证据（只有名字表） | 状态词（`editable_dependency_not_reproducible` 等），**不转成 pip 名** | `tests/test_import_origin_metadata.py::TestEditableAndNonReproducibleSources` |
| 表外的名字（curated 里没有） | 只有「运行后单包修复」或（#864）猜同名 | 预填 + 用户确认包名 + 装后核验，不猜 | `tests/test_import_origin_user_specified.py` |
| 非 ASCII 的本地模块名（`数据.py`） | 判 `unknown`（`no_provider_found`）；**PR7 发现并修复** | 按 `str.isidentifier()` 当本地模块 | `tests/test_import_origin_cross_platform.py::TestPathSpelling::test_a_chinese_module_name_is_a_local_module_exactly_as_real_python_sees_it` |

升级前 `curated` 之外**从不读**已安装元数据；现在：`top_level.txt` / `RECORD` / `direct_url.json` / editable finder / `conda-meta`（`distmeta`），但**只是证据，不是授权**（`TestInstalledMetadataIsNotAnInstallAuthorization`）。

## 4. 确定性测试矩阵

### 4.1 O-01 … O-20（计划 P7.1）

下表每个用例都存在（本文引用由脚本核过）；`静态` = `tests/test_import_origin_static.py`，`元数据` = `tests/test_import_origin_metadata.py`。

| 编号 | 内容 | 用例 |
| --- | --- | --- |
| O-01 | `import numpy` 第三方候选 | `静态::TestContract::test_o01_o02_a_mapped_third_party_is_a_candidate_not_a_verified_origin` |
| O-02 | `import docx` 可映射 `python-docx`（候选，非已验证来源） | 同上；e2e ② 的影响摘要里 `python-docx` |
| O-03 | 同目录 `utils.py` 是本地模块 | `静态::TestAlreadyCoveredElsewhere::test_o03_a_same_directory_utils_py_is_local` |
| O-04 | 项目内 package 是本地 package，永不装 | `静态::TestLocalPackages::test_o04_a_project_package_is_local_and_never_installed` |
| O-05 | package 内相对导入正确解析 | `静态::TestLocalPackages::test_o05_relative_imports_resolve_against_the_package_context`、`::test_o05_a_relative_import_in_the_entry_script_has_no_package_and_is_not_guessed`、`::test_o05_a_relative_import_climbing_above_the_top_package_is_reported` |
| O-06 | 本地 `json.py` 不被简单归为 stdlib | `静态::TestPriority::test_o06_a_local_json_py_shadows_the_stdlib_and_is_followed`、`::test_o06_a_stdlib_name_without_a_local_file_stays_stdlib`；真 Python 对拍 `静态::TestRealPythonParity::test_json_shadowing_package_priority_and_case` |
| O-07 | built-in `sys` 内建 | `静态::TestPriority::test_o07_builtin_and_preloaded_names_are_not_shadowed_by_local_files`、`::test_frozen_importlib_is_frozen`；目标解释器名单 `tests/test_import_origin_target_facts.py::TestBuiltinNamesComeFromTheTarget` |
| O-08 | `from pkg import x` 不把全部 x 当子模块 | `静态::TestLocalPackages::test_o08_from_pkg_import_x_follows_only_names_that_are_submodules`、`::test_o08_a_name_that_is_both_attribute_and_submodule_follows_the_submodule` |
| O-09 | namespace package 不强行选唯一提供者 | `静态::TestNamespacePackages::test_o09_a_directory_without_init_is_never_the_only_provider`、`::test_o09_the_namespace_directory_keeps_the_mapping_candidate_visible`、`::test_o09_a_submodule_of_a_namespace_directory_is_followed_when_imported` |
| O-10 | editable install 不当普通 PyPI 依赖 | `元数据::TestEditableAndNonReproducibleSources::test_o10_direct_url_editable`、`::test_o10_setuptools_editable_finder_pth_maps_modules_without_running_anything` |
| O-11 | `cv2` 多发行包不静默猜 | `元数据::TestSeveralProviders::test_o11_two_installed_cv2_distributions_are_both_kept_and_none_is_picked`、`::test_o11_declared_name_matching_one_candidate_does_not_pick_it`、`::test_o11_nothing_installed_keeps_all_four_alternatives_and_is_ambiguous_even_without_an_index` |
| O-12 | 动态导入变量是 unknown / 动态候选 | `静态::TestDynamicImports::test_o12_alias_calls_are_dynamic_candidates_not_unconditional_dependencies`、`::test_o12_the_dynamic_payload_shape_is_unchanged`、`::test_o12_a_non_importlib_object_with_the_same_method_name_is_not_an_import` |
| O-13 / O-14 | try/except 可选依赖、`TYPE_CHECKING` 不当运行期必需 | `静态::TestAlreadyCoveredElsewhere::test_o13_o14_try_and_type_checking_imports_are_never_needed` |
| O-15 | 本地模块递归依赖正确传播 needed | `静态::TestLocalPackages::test_o15_a_package_submodule_third_party_import_is_needed`、`::test_o15_the_weaker_context_propagates_through_the_chain`、`::test_o15_a_later_unconditional_import_strengthens_the_whole_chain`、`::test_o15_following_stops_at_the_depth_limit_and_says_so`、`::test_o15_following_stops_at_the_file_limit` |
| O-16 | 软链接越界不越界扫描 | `静态::TestLinks::test_o16_links_pointing_outside_the_project_are_never_read`、`::test_o16_no_follow_refuses_even_links_that_stay_inside_the_project`；`tests/test_import_origin_no_follow_audit.py::TestNoFollowingStatOfOutsideTargets` |
| O-17 | `python -m` 尊重包上下文 | `静态::TestEntryProfiles::test_o17_python_m_puts_cwd_first_and_runs_the_package_init`、`::test_o17_python_m_runs_the_parent_package_init_before_the_module`、`::test_o17_python_m_package_runs_its_dunder_main` |
| O-18 | 同名冲突返回真实优先级或歧义 | `静态::TestPriority::test_o18_a_package_directory_wins_over_a_same_named_module`、`::test_o18_an_extension_module_wins_over_a_same_named_py_in_one_directory`、`::test_o18_import_names_match_the_directory_listing_exactly` |
| O-19 | 不完整元数据 `unverified` | `元数据::TestIncompleteMetadataAndNothingInstalled::test_o19_a_dist_with_neither_top_level_nor_record_is_unverified_not_a_mapping`、`::test_o19_a_module_with_no_claiming_distribution_is_an_orphan_not_a_guess`、`::test_o19_a_dist_info_without_metadata_falls_back_to_its_directory_name_and_says_so` |
| O-20 | 未安装模块不猜 PyPI 名 | `静态::TestContract::test_o20_an_uninstalled_unmapped_module_is_unknown_and_never_guessed`、`元数据::TestIncompleteMetadataAndNothingInstalled::test_o20_an_uninstalled_unknown_module_is_never_given_a_pypi_name` |

**缺口**：无。O-01…O-20 在 PR7 之前就全部有用例（BASELINE §7.1 的「不满足」项由 PR1 / PR2 补齐）；PR7 额外补了计划里没有列、但中文用户一定会碰到的两类（中文模块名与源码编码，§5.2）。

### 4.2 恶意包副作用（计划 P7.2）

| 形状 | 用例 | 结论 |
| --- | --- | --- |
| `dangerous_package/__init__.py` 与子模块里写 `SIDE_EFFECT`；`from dangerous_package import submodule` | `tests/test_import_origin_safety.py::TestScanRunsNothing::test_from_dangerous_package_import_submodule_executes_neither_file`、`::test_no_follow_mode_runs_nothing_either`、`::test_a_python_m_entry_runs_nothing_either` | 哨兵文件不存在；起进程 / 联网 / 动态执行的入口换成调用即失败的桩，没被碰；`::test_the_armed_traps_are_real` 证明桩真会响 |
| `.pth` / `sitecustomize` / editable finder | `tests/test_import_origin_safety.py::TestScanRunsNothing::test_pth_and_sitecustomize_in_a_fake_site_packages_are_never_read_or_run`；`tests/test_import_origin_metadata.py::TestReadingRunsNothing::test_reading_and_scanning_execute_no_pth_sitecustomize_package_or_finder` | 读取与扫描都不执行；痕迹文件不出现 |
| 默认路径整体（项目扫描 / 建议含覆盖度 / 静态索引 / 依赖门 `cache_only`） | `tests/test_environment_coverage.py::test_the_default_scan_never_runs_user_code_and_the_authorized_check_does_and_says_so` | 零子进程、零执行；**授权检查真执行**（`SIDE_EFFECT` 与启动期 `.pth` 都出现）且结果如实标 `side_effect_free: false` |
| 导入即扫描三类哨兵（脚本 / 项目解释器 / 登录 Shell） | `tests/test_project_scan_zero_exec.py::test_the_scan_runs_nothing_with_every_sentinel_armed`、`::test_open_scan_refresh_and_readiness_over_http_start_nothing` | 三类哨兵计数 0 |
| 结构 | `tests/test_import_origin_safety.py::TestStructuralGate`、`tests/test_import_origin_metadata.py::TestStructuralGate`、`tests/test_import_origin_no_follow_audit.py::TestFollowingCallsAreAudited` | 模块只 import 白名单；源码里没有进程 / 网络 / 动态执行 / `importlib.metadata` 的名字；每个会碰文件系统的调用都是不跟随的或在白名单里（带理由） |
| 真浏览器 | `web/e2e/import-origin.spec.ts` ①：「检查」之前与之后、运行之前，脚本的执行日志都是空 | 准备阶段不执行用户脚本；检查只起被点名的候选 |

### 4.3 环境 fixture A–J（计划 P7.3）

均在 `tests/test_environment_coverage.py`（真 venv，复用 `tests/support/envworld.py` / `venvfixture.py`）：

| fixture | 用例 |
| --- | --- |
| A 无第三方 | `test_A_a_script_without_third_party_imports_probes_exactly_as_before` |
| B `.venv` 装齐 | `test_B_a_venv_with_everything_installed_is_covered_and_says_user_code_ran` |
| C `.venv` 缺一个（缺 vs 装坏分开） | `test_C_one_missing_and_one_broken_package_are_told_apart` |
| D bundled 缺而用户环境有 | `test_D_the_bundled_runtime_lacks_it_while_the_user_environment_has_it` |
| E 版本冲突 | `test_E_a_declared_version_conflict_is_not_hidden_by_coverage` |
| F 同 base 两个 venv（不因 realpath 合并） | `test_F_two_venvs_on_one_base_keep_their_own_coverage` |
| G 被重建（旧覆盖度对不上） | `test_G_a_rebuilt_environment_never_serves_the_old_coverage`、`test_G_a_venv_rebuilt_while_its_check_runs_does_not_get_the_old_coverage` |
| H 不支持的 Python / I matplotlib 在但 worker import 失败（`unusable` 与 `missing` 分对） | `test_H_I_an_environment_that_cannot_run_tavotto_is_not_reported_as_missing_packages` |
| J 用户全局解释器设置（显式选择不被覆盖、角色按全局锁） | `test_J_a_global_interpreter_setting_still_allows_coverage_but_recommends_nothing`、`test_J_a_project_candidate_overridden_by_the_global_setting_is_a_user_environment`、`test_J_the_global_interpreter_itself_is_the_target_role_when_it_is_a_candidate` |

计划里的「Windows 旧 3.7 不误认」按支持档（`support`）判，与平台无关；在 fixture H / I 里是 `unsupported` 而非 `missing`。**缺口**：无。

### 4.4 安装与并发 13 条（计划 P7.4）

BASELINE §7.2 已把 13 条逐条映射到现有用例，**13 条全部已有对应用例**，PR5a / PR5b / PR6 没有碰安装方案本身（`impact_of` 只在 PR5b 加了 `user_specified` 并升 `IMPACT_VERSION`）。PR7 重新核对了这些用例仍存在：

| # | 条 | 用例 |
| --- | --- | --- |
| 1 | 未授权不装 | `tests/test_dependency_impact.py::TestEveryExecutionEntryRequiresTheDigest`、`tests/test_dependency_impact.py::TestTaskDiagnostic::test_the_prepare_endpoint_requires_the_displayed_digest` |
| 2 | bundled 不可改 | `tests/test_dependency_repair.py::test_the_bundled_runtime_is_never_a_mutation_target`；`tests/test_import_origin_user_specified.py`（bundled 不可作目标） |
| 3 | 同 digest 只一个安装 | `tests/test_dependency_impact.py::TestClaim::test_two_confirmations_of_one_impact_run_one_job_and_both_hear_the_end` |
| 4 | 确认后集合变化阻止旧计划 | `tests/test_dependency_impact.py::TestClaim::test_a_world_that_changed_after_confirming_is_refused_and_the_plan_is_dropped`；PR5a：`tests/test_import_origin_plan_backlog.py::TestExecutionTimeLayerCheck` |
| 5 | 环境选择变则旧计划失效 | `tests/test_dependency_impact.py::TestAdoptionFence::test_a_plan_made_before_the_user_adopted_another_environment_never_runs` |
| 6 | 安装中不能交错采用 | `tests/test_dependency_impact.py::TestAdoptionFence::test_changing_the_project_environment_is_refused_while_an_install_runs`、`::test_the_adoption_endpoint_waits_for_the_install_instead_of_overwriting_it` |
| 7 | 取消 / 超时 | `tests/test_dependency_transaction.py::test_cancel_during_install_leaves_no_active_environment`、`::test_cancel_accepted_during_the_selftest_is_honored`；`tests/test_pypi_slow_fallback.py` |
| 8 | 断网重试 | `tests/test_dependency_repair.py::test_a_failed_pip_run_leaves_the_requirement_retryable` |
| 9 | 受管失败旧代可用 | `tests/test_dependency_transaction.py::test_selftest_or_consistency_failure_keeps_the_old_generation`、`::test_resolver_conflict_leaves_the_previous_generation_active` |
| 10 | 原地改 venv 披露不可完整回滚 | `tests/test_dependency_impact.py::TestBoundaries::test_a_user_venv_install_that_imports_badly_is_not_rolled_back_and_says_so` |
| 11 | 装成功 import 失败不 ready | `tests/test_dependency_transaction.py::test_key_import_failure_after_install_is_not_activated`；**PR5b 加严**：`tests/test_import_origin_user_specified.py`（import 成功但发行包核验不过也不放行）；真浏览器 ④ |
| 12 | 项目隔离 | `tests/test_dependency_transaction.py::test_two_projects_prepare_concurrently_and_independently` |
| 13 | 作用域冲突守 ADR 0115 | `tests/test_dependency_scope.py` 全文件 |

（表里每个 `文件::类::用例` 都由核对脚本验证过它存在。）**缺口**：无新增。

### 4.5 变异反证（每条新判据先提交、再变异；rc 取自真实退出码）

| 判据 | 变异（改的位置） | 变异后 | 还原后 |
| --- | --- | --- | --- |
| 中文模块名按标识符规则 | `importscan._is_module_name` 退回 `depresolve.valid_import_name` | `test_import_origin_cross_platform.py` rc 1（`…test_a_chinese_module_name_…`） | rc 0 |
| 源码编码按 PEP 263 / BOM 读 | `importscan._read` 里 `python_source=True` → `False` | rc 1（`tests/test_import_origin_cross_platform.py::TestSourceEncodings::test_a_utf8_bom_script_is_read`） | rc 0 |
| `RECORD` 反斜杠路径 | `distmeta` 去掉 `row[0].replace("\\", "/")` | rc 1（`tests/test_import_origin_cross_platform.py::TestWindowsShapesOnAnyHost::test_a_record_with_backslash_paths_still_maps_the_import_name`） | rc 0 |
| 依赖任务投影不读新字段 | `deprepair.diagnostic_projection` 加 `"origins": rec.get("origins")` | `test_import_origin_diagnostics_privacy.py` rc 1（金丝雀） | rc 0 |
| 准备任务投影不读新字段 | `preparation.diagnostic_projection` 加 `origin_names`（读 `dep["plan"]["origins"]`） | rc 1（会话 + 全局包金丝雀） | rc 0 |
| 缺依赖现场不记计划 | `deprepair.note_missing_dependency` 记 `repair.get("plan")` | rc 1 | rc 0 |
| 检查项只回计数与闭集原因码 | `prepsession.origin_facts` 不再按 `ORIGIN_REASONS` 过滤 | rc 1 | rc 0 |
| 默认扫描不读数据文件 | `projscan.scan` 里 `read_bytes()` 一次 `data/results.csv` | `test_import_origin_perf.py` rc 1（`test_data_files_and_the_venv_are_listed_but_never_opened`） | rc 0 |
| 默认扫描零子进程 | `projscan.scan` 里 `subprocess.run(["true"])` | rc 1（fixture 报错：性能脚本在额外子进程 > 0 时退出码非 0） | rc 0 |
| 增量扫描命中缓存 | `projscan._script_item` 忽略缓存命中 | rc 1（`test_the_second_pass_with_the_same_cache_parses_nothing`） | rc 0 |
| e2e ④ 装后发行包核验 | `deprepair._verify_distributions` 开头 `return` | `import-origin.spec.ts` ④ 失败（核验错误从不出现） | ④ 通过 |
| e2e ③ 来源无法验证 | `depplan._installed_claims` 不认「在而无元数据」 | ③ 失败（`distribution_unresolved` ≠ `unverified`） | ③ 通过 |
| e2e ① 授权检查如实标注 | `envadvice.recommend` 的 `side_effect_free` 改 `True` | ① 失败 | ① 通过 |
| e2e ② 一句话 + 安装 | `depplan.plan` 里不再把缺的包放进 `missing` | ② 失败（卡片是 `ready` 而不是 `deps`） | ② 通过 |

没有做变异的：`tests/test_import_origin_perf.py` 的「静态解析读到的恰好是导入闭包」（`importscan` 的闭包读取由 PR1 的 `TestCostScalesWithTheImportGraph` 另有变异）；
一次对 `depplan._verdict` 的无效变异（改了之后 `version` 仍为空、结果等价）被如实丢弃，换成上表第 14 行。

### 4.6 本次运行结果（本机，Darwin arm64，Python 3.13.11）

| 命令（`PYTHONPATH=$PWD/src:$PWD/tests`） | 结果 |
| --- | --- |
| `pytest tests/test_import_origin_*.py tests/test_environment_coverage.py tests/test_dependency_plan.py tests/test_preparation_session_dependencies.py tests/test_dependency_impact.py tests/test_dependency_transaction.py tests/test_dependency_scope.py tests/test_dependency_repair.py tests/test_project_scan.py tests/test_project_scan_zero_exec.py tests/test_project_scan_declarations.py tests/test_diagnostics_bundle.py tests/test_diagnostics_log_privacy.py tests/test_task_diagnostics.py tests/test_e2e_leg_topology.py tests/test_import_architecture.py tests/test_environment_autodetect.py tests/test_environment_adoption.py tests/test_environment_session.py tests/test_user_environments.py tests/test_foundation_dependencies.py tests/test_dependency_repair_e2e.py -q` | **rc 0**；1586 passed、3 skipped、0 failed（按进度点计数；其中 2 条 skip 已核实是联网用例，`TAVOTTO_FOUNDATION_ONLINE=1` 才跑，nightly foundation-observing；另 1 条未逐条追查）。这一轮在补写最后一条诊断隐私用例之前跑，该文件随后单独重跑 `12 passed` |
| `pytest tests/test_agents_rules_index.py tests/test_docs_references.py tests/test_source_hygiene.py` | rc 0，`52 passed` |
| `ruff check .`；`ruff format --check src tests` | rc 0；rc 0（641 个文件已格式化） |
| `tsc -b`（web）；`oxlint`；`i18next-cli types --ci && node scripts/i18n-check.mjs && i18next-cli lint` | rc 0；rc 0（既有 warning，非本 PR）；通过。本 PR 没有改 `web/src`，只改了 `web/e2e` |
| `python scripts/ci/feature_registry.py check` | 54 个 active 功能、65 条 e2e 引用都在且不带跳过；无孤儿标签 |

## 5. Windows / macOS / Linux 验证

### 5.1 已有证据

| 平台 | 证据 | 级别 |
| --- | --- | --- |
| macOS（arm64，本机） | §4 / §6 / §8 全部在本机运行 | `[运行]` |
| macOS / Windows / Ubuntu | #880 #882 #906 #920 #930 #933 #934 合入前的 status check 汇总里：`backend-platforms (macos-latest, 1–3)`、`backend-platforms (windows-latest, 1–3)`、`backend-fast (3.10 / 3.13 / 3.14, …)`、`windows-exe-smoke (1)(2)`、`posix-e2e`、`private python (windows-latest)`、`frontend` 均 SUCCESS | `[CI]`（含这些 PR 各自新增的用例；**不含本 PR7 新增的用例**） |
| Windows 形状（在 POSIX 上模拟） | `tests/test_import_origin_windows_links.py`（reparse point / junction / 扩展长度前缀 / UNC）、`tests/test_import_origin_cross_platform.py::TestWindowsShapesOnAnyHost` | `[测试]` |

### 5.2 P7.5 跨平台矩阵

| 项 | 覆盖 | 状态 |
| --- | --- | --- |
| 中文路径 | `tests/test_import_origin_cross_platform.py::TestPathSpelling`（同一棵树放在 ASCII / 中文 + 空格 + 括号 / 带重音字符的目录里，事实逐字相同；载荷里没有绝对路径与目录拼写；环境前缀在中文 + 空格路径下照样读出发行包元数据）；真浏览器 ①–④ 的项目在 `我的 论文/paper` | 已覆盖（macOS 本机；Linux 由 posix-e2e 腿执行 e2e） |
| 空格 | 同上；`tests/test_import_origin_namespace_and_links.py` | 已覆盖 |
| 中文模块名（`数据.py`） | `tests/test_import_origin_cross_platform.py::TestPathSpelling::test_a_chinese_module_name_is_a_local_module_exactly_as_real_python_sees_it`（对拍真 Python）。**这条在 PR7 里发现缺口并修复**（`importscan._is_module_name`，此前 ASCII 判据把它判成 unknown） | 已修复并覆盖 |
| 源码编码（GBK 声明 / UTF-8 BOM / latin-1 / 未知编码 / 非法字节） | `tests/test_import_origin_cross_platform.py::TestSourceEncodings`（读不懂是一条问题，不崩溃、问题里没有文件内容、不猜；非法 UTF-8 对拍真 Python 同样拒收） | 已覆盖 |
| 盘符 / Windows 路径形态 | `tests/test_import_origin_cross_platform.py::TestWindowsShapesOnAnyHost`：`RECORD` 反斜杠、`pyvenv.cfg` 的盘符 + 中文用户名、`.pth` 里的盘符 / 反斜杠行在 POSIX 上不被当成 site 之外的真路径 | 文本形状已模拟；**真 Windows 路径解析需 Windows 真机 / CI 验证** |
| 大小写 | `静态::TestPriority::test_o18_import_names_match_the_directory_listing_exactly`（逐字匹配）、`tests/test_import_origin_cross_platform.py::TestWindowsShapesOnAnyHost::test_dist_info_directory_case_and_separators_normalise_to_one_distribution_key`（PEP 503 键；声明与已装对得上）、`tests/test_dependency_plan.py` 的 `Utils` / `utils` 用例 | 已覆盖；**NTFS 真实的大小写不敏感与 8.3 短名需 Windows 真机 / CI 验证** |
| wheel 平台 / arm64 与 x86_64 | 目标事实 `platform_machine` / `sys_platform` / marker 环境来自**目标解释器**（`TargetFacts`）；本机 arm64 跑 e2e，CI posix-e2e 是 ubuntu x86_64 | 已覆盖（两种架构各跑过）；**Windows arm64 没有证据** |
| `.pyd` / 扩展后缀 / DLL | 后缀表取目标解释器的 `EXTENSION_SUFFIXES`：`tests/test_import_origin_target_facts.py::TestExtensionSuffixesComeFromTheTarget`、`tests/test_import_origin_plan_backlog.py::TestExactExtensionSuffixes`、`tests/test_import_origin_cross_platform.py::TestWindowsShapesOnAnyHost::test_the_host_platform_never_decides_which_extension_suffix_counts` | 判据已覆盖；**`.pyd` 的真实加载与 DLL 依赖需 Windows 真机 / CI 验证** |
| PyInstaller 冻结运行时 | `tests/test_import_origin_target_facts.py::test_target_facts_measure_the_interpreters_own_builtin_names_and_suffixes`（名单与后缀由目标解释器量，不拿宿主冒充）；wrapper 预加载 `tests/test_import_origin_wrapper_preloaded.py` | 判据已覆盖；**冻结产物里的真实 `sys.path` 布局需 Windows exe / CI 验证**（`windows-exe-smoke` 只验渲染走内置 runtime，不验来源解析） |
| worker 与主进程不同解释器 | e2e ①（worker 跑在项目 `.venv`）、②（跑在受管环境）；`tests/test_import_origin_target_facts.py::TestBuiltinNamesComeFromTheTarget::test_depplan_passes_the_target_interpreters_builtin_names` | 已覆盖 |

## 6. 一次真实项目的完整运行证据

### 6.1 命令与结果

前置（worktree 里，本机）：前端产物 `pnpm vite build` 拷进 `src/tavotto/web/`；批准字体 `scripts/fetch_fonts.py`（不进 git）；`web/node_modules` 软链主仓库（完事删）。

```text
cd web
PYTHONPATH=<worktree>/src TAVOTTO_PYTHON=<repo>/.venv/bin/python \
  npx playwright test e2e/import-origin.spec.ts --project=chromium
```

结果：命令 `npx playwright test e2e/import-origin.spec.ts --project=chromium` → **rc 0，`4 passed (1.5m)`**：① 22.4 s、② 22.3 s、③ 18.2 s、④ 25.3 s。同一份代码在机器负载均值约 30 时的另一次完整运行 `4 passed (2.8m)`（30.8 / 41.3 / 28.0 / 66 s）。
同一台机器上相邻既有 e2e `environment-advice.spec.ts` 仍 `2 passed`（`fixtures.ts` 的 `launchCommand` 只加了一个默认关闭的 `offline` 参数）。
`tsc -p tsconfig.e2e.json --noEmit` rc 0、`oxlint` rc 0。

e2e 只在 POSIX 腿执行：`test.skip(win32)` 的理由点出 `posix-e2e`（`tests/test_e2e_leg_topology.py::TestPlatformSkipsHaveALegThatRunsThem` 钉着）。

### 6.2 夹具与判据

项目 = 计划的最终验收场景（`tests/support/e2e_origin_world.py`）：`我的 论文/paper/{scripts/{figure1.py,figure2.py,utils.py}, data/results.csv, requirements.txt[, .venv/]}`，`figure1.py` 是 `import numpy as np; import matplotlib.pyplot as plt; import docx; from utils import load_results`。
判据的主语是脚本自己写在项目**之外**的执行日志（`sys.executable` / `sys.prefix` / `cwd` / `docx.__file__` / 画出的 y）与磁盘真实状态，不是界面文案。

### 6.3 验收场景逐条对照（计划「最终产品验收场景」）

| 期望 | 实际行为（证据） |
| --- | --- |
| `utils` 识别为本地，不提 PyPI | `utils` 不在任何 `needed` / `installs` 里：`tests/test_import_origin_static.py::TestAlreadyCoveredElsewhere::test_o03_a_same_directory_utils_py_is_local`；e2e ②：影响里 `installs` 不含 `utils`；① 的覆盖度 `coverage.modules` 只有 `docx / matplotlib / numpy` |
| numpy / matplotlib / docx 识别为第三方并给映射 | `docx → python-docx`（e2e ② 的 `installs`）；numpy / matplotlib 已在运行环境里，满足、不进安装集合 |
| 发现 `.venv` 只建议不切换 | e2e ①：检查前候选 `.venv` 为 `unchecked`、`decision.mode = detect`、`needs_decision = false`、`consent = none`、`source ≠ project_venv`。**口径说明（裁决 C2）**：默认「检测模式」在**运行时**自动检测并采用能跑的那一套；「只建议」指扫描 / 打开项目时不切换、不起它 |
| 检查 `.venv` 用真实数据判断 | e2e ①：`POST …/environment/check {coverage:true, script:"scripts/figure1.py"}` → `executed.candidate_interpreters = 1`、覆盖度 `covered`、`docx / matplotlib / numpy` 均 `found`、`executed_user_code = true`、`side_effect_free = false`；只起被点名的那一个；检查后 `consent` 仍是 `none`、脚本仍未执行 |
| 满足则采用直接跑，不装 | e2e ①：执行日志 `prefix` == 项目 `.venv`、`docx` 来自 `.venv` 自己的 site-packages；`managed.exists = false`、磁盘上没有任何受管环境的 `.dist-info` |
| 缺 `python-docx` 则给「用其他已有环境」或「受管环境准备」 | e2e ②（受管环境准备）：卡片 `auto_fix`，一句话 + 一个主按钮。「用其他已有环境」这一支：后端 `deprepair.user_environment_offer` / `tests/test_user_environments.py`，前端 `DependencyPrepareDialog` 的「改用这个环境」（`DependencyPrepareDialog.test.tsx`）有组件级用例，**没有真浏览器 e2e**（§10） |
| 确认影响后装好 | e2e ②：授权前没有受管环境、没有执行；影响 `creates_environment=true`、`modifies_user_environment=false`；点一次后装好，磁盘上有 `python_docx-1.1.2.dist-info`，宿主解释器仍 import 不到 `docx` |
| 执行前检查 cwd 与数据路径授权 | e2e：卡片先问「在哪个目录运行？」（`workdir`，推荐项目根，因为 `data/results.csv` 只在项目根找得到）；日志 `cwd` == 项目根；`data/results.csv` 读到的 y = `[2, 3.5, 1.25, 4]` |
| 现有 worker 运行捕获 Figure | e2e ①②：`画好了 1 张图`，详情里 `execution_finished` / `figure_captured` 均 `true`；执行日志恰好一条 |
| 进入图内编辑 | e2e ①②：点「进入编辑」→ 卡片收起，`[data-element-svg] svg` 可见，不重跑（执行日志仍一条） |

## 7. 安全测试结果

| 项 | 结果 |
| --- | --- |
| 默认扫描零执行、零子进程、不联网 | §4.2；§8 的计数门禁（5 / 30 / 100 脚本：额外子进程 0） |
| 已安装元数据不是授权 | `tests/test_import_origin_metadata.py::TestInstalledMetadataIsNotAnInstallAuthorization`；`INSTALLABLE_SOURCES` 仍是既有三档（`tests/test_import_origin_user_specified.py`） |
| 用户指定的包是 `user_specified_unverified`，确认页单独标注，换约束摘要就变 | `tests/test_import_origin_user_specified.py`；e2e ④（确认页「未经可信来源核实」+ 徽标 + 影响披露） |
| 装后核验拦住「装上了但不提供脚本要的库」 | `tests/test_import_origin_user_specified.py`（传递依赖 / 填错包名 / 版本不符）；e2e ④：先填 `other-lib`（它把 `mystery-lib` 当传递依赖拉进来，`import mystery_lib` 能成功，但用户确认的包自己不提供它）→ 框里「装完后核对发现…」+ 详情「包名可能填错了」，不激活、输入保留、脚本未执行 |
| 路径约束 | `tests/test_import_origin_path_confinement.py`、`…no_follow_audit.py`、`…windows_links.py`（越界 / 链接 / UNC / junction 一律不读不探） |
| 诊断隐私 | §7.1 |
| 不新增已发布错误码、不新增 `BLOCK_REASONS` | `tests/test_dependency_plan.py::TestClosedSetsMirrorTheFrontend` |

### 7.1 诊断与隐私核查结论

核查四个出口：依赖任务投影 `deprepair.diagnostic_projection`、准备任务投影 `preparation.diagnostic_projection`、缺依赖现场 `deprepair.note_missing_dependency`、全局诊断包。**结论：来源 / 覆盖度 / 核验的新字段（`origins`、`user_specified`、`suggested_distribution`、`candidates`、`coverage`、`dist_reasons`）不以任何形式进入这四个出口——今天它们连 reason code 与计数都没有进诊断**；诊断里能看到的只有既有的闭集 / 计数 / 布尔 / 不透明摘要。会话检查项的 `detail.origins` 只有计数与闭集原因码（`prepsession.origin_facts`，闭集外的原因码丢弃、不回显），它是给卡片用的会话报告，不进诊断。

* 结构性测试（PR7 新增）：`tests/test_import_origin_diagnostics_privacy.py`——金丝雀（带标记的 import 名 / 包名 / 路径塞进每个新字段的位置，出口全文搜索原样、小写、hex、base64、url 编码与常见摘要）、投影键的写死闭集、AST（`diagnostic_projection` 只读白名单键；`preparation.diagnostic_projection` 对 `dependency_preparation` 只读 `blocked`）、真走一遍会话再导出任务诊断与全局诊断包。
* **一个已知差距**：依赖任务投影里的 `impact.digest` 是影响摘要的**无密钥** sha256，`IMPACT_VERSION` 2 起摘要含用户指定的包名——名字不以明文出门，但对常见包名集合可离线复算。这与 #918 第 1 条同形（**该 issue 仍开着**，修法是诊断导出里改带本机密钥的摘要或不导出）。本 PR **没有**改它（改的是 #918 的范围），只在 `::TestDependencyTaskProjection::test_the_one_value_derived_from_package_names_is_the_impact_digest_and_that_is_issue_918` 里钉住「它是投影里唯一一个由名字派生的值」，需要维护者决定是否在 PR7 里顺手修。
* 另：`dist_reasons`（装后核验的闭集原因码）今天**不**进任务诊断；若想让维护者在诊断里看到「为什么核验没过」，只能加这个闭集的码，不能带名字（需要维护者决定）。

## 8. 性能测量

`scripts/dev/import_origin_perf.py`（可重复跑：`PYTHONPATH=src python scripts/dev/import_origin_perf.py [--json] [--repeats N]`）：5 / 30 / 100 个脚本的项目（脚本 + 共享模块 + 包 + 40 个无关模块 + 1.6 MB 数据文件 + 600 个文件的 `.venv`）。量法用 `sys.addaudithook` 的 `open` / `scandir` / `compile` / `subprocess.Popen` / `os.system` / `os.exec` / `os.posix_spawn` / `os.fork` / `socket.connect` 事件，**独立于扫描自己的预算账**；时间取 5 次中位数。
`tests/test_import_origin_perf.py` 在子进程里跑它并**只断言确定性计数**（不断言耗时）。

2026-10-11 · Python 3.13.11 · Darwin 27.0.0 arm64 · 12 核

| 脚本数 | 默认扫描冷（s） | 打开文件 / 字节 | 解析次数 | 峰值 MiB | 增量（s） | 缓存命中 | 静态解析全部（s） | 读到文件 | 额外子进程 | 误开数据 / .venv |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 0.0119 | 53 / 18611 | 48 | 0.17 | 0.0028 | 100% | 0.0088 | 8 | 0 | 0 |
| 30 | 0.0164 | 78 / 24431 | 73 | 0.24 | 0.0041 | 100% | 0.0502 | 33 | 0 | 0 |
| 100 | 0.0295 | 148 / 40741 | 143 | 0.44 | 0.006 | 100% | 0.1794 | 103 | 0 | 0 |

读法：「打开文件 / 字节」是审计事件里 `open` 的次数与文件当时大小之和（读了多少的上界）；「解析次数」是 `compile` 事件数；缓存命中 = 1 − 增量解析次数 / 冷解析次数。结论：
默认扫描的**额外子进程恒为 0**；数据文件与 `.venv` 里的文件**一个都没被打开**；打开的文件数随脚本数线性增长（每个多出来的脚本多一次打开）；总字节不超过项目源码字节加一个常数（依赖声明等）；
增量扫描一次解析都没有。静态来源解析把 100 个脚本各解一遍读到 103 个文件（100 个脚本 + `shared.py` + `lab/__init__.py` + `lab/util.py`），40 个无关模块一个都没读。内存峰值是 `tracemalloc` 对一次冷扫描的值。
本机负载较高时耗时会抖（量的时候机器负载均值约 30），**不要把耗时当门禁**。e2e 端到端耗时见 §6.1。

## 9. 未支持的 Python 机制（如实不做，而不是猜）

zip / egg 里的模块名（`opaque_roots > 0` 时名字层判不完整）；未建模的可执行 `.pth` 行（只有已知良性钩子——setuptools 的 distutils 垫片、virtualenv 的 `_virtualenv`、`*-nspkg.pth` 固定模板——按**整行内容**精确匹配才算已建模，`distmeta._KNOWN_BENIGN_PTH_RE` / `_is_nspkg_line`）；`sitecustomize` / `usercustomize` 里 `site.addsitedir` 的目录（当作不处理 `.pth`）；动态导入（只当候选，不当必需依赖）；命名空间包不强行选唯一提供者；导入钩子（`sys.meta_path`）与运行时改 `sys.path`（`sys.path` 修改让搜索不完整，`tests/test_import_origin_static.py::TestDynamicImports::test_sys_path_edits_make_the_search_incomplete`）；Conda 专有包不转 pip 名；editable / 本地 wheel / VCS / 无元数据不转 pip 名。读不全就当「来源未定」。

## 10. 已知限制与已知缺陷

**已知缺陷（需要维护者决定）**

1. **确认包名并装好之后，卡片仍说「有 1 个库的来源还没法确定」、主按钮是「仍然运行」，而不是「可以运行了」。** 真浏览器 ④ 复现并钉着现状。根因：联合计划对「表外名 + 已装提供者」判 `origins[].reason = unverified`（条目 `distribution_status = installed_confirmed`、`provenance = index`、`resolution_status = resolved`，候选里就有刚装的包），`depplan._verdict` 里只有「名字级已装 / 备选 / 表 / 声明的名字」能判满足，`_installed_claims` 把任何已装提供者都当成「环境里已经有它、装 curated 的名字救不了」而判 `_OPEN`。单元层可复现：给一个表外名 + 静态索引里有唯一的 index 来源提供者 + `facts.installed` 里有该发行包，`depplan.plan` 仍给 `origins`（见 `docs/implementation/import-origin-resolver/00_BASELINE.md` §12 的判决顺序）。
   影响：运行本身没有问题；用户确认并装好之后看到的状态措辞不对；**任何**在用户环境里已用 PyPI 装好的表外库（`curated` 里没有的）也会被卡片说「来源待定」。建议修法（未做）：在 `_verdict` 里对 `bucket=unknown` 且唯一未被遮蔽的已装候选是 `installed_confirmed` + `provenance=index` + `reproducible` 时判满足（不进 `missing`、也不进 `origins`），仍不产生安装名；改动面是 `depplan._verdict` 与 `TestPlanOrigins`，属 `risk:high` 的判据改动，所以没有放进 PR7。
2. #918 第 1 条（§7.1）：依赖任务诊断里的 `impact.digest` 可离线复算（含用户指定包名）。

**已知限制（继承自各 PR 的「仍未覆盖」，逐条核对过仍然成立）**

* 默认扫描没有接来源事实：卡片上的「需要确认 / 尚无法确定」只在**准备会话已经评估过依赖**之后出现（BASELINE §15）。
* 自动修复与需要确认是**两次授权**（先装可信的，装完会话重新检查后才出现「需要确认」）：同一脚本既缺可信包又有预填项时授权框不带输入框（ADR 0119 §十给出理由；e2e ③④ 各自单独验证，没有把两者放在同一脚本里）。
* 「用其他已有环境」这一支只有后端与组件级用例，没有真浏览器 e2e。
* `found` 改判（覆盖度说它能 import、发行包名却没装）与「依赖门展示覆盖度 detail」未做（BASELINE §12）。
* 确认包名的框在运行中切走项目再回来不恢复（BASELINE §15）。
* 非 ASCII 宿主路径下的夹具仍写 import 钩子（Windows 中文用户名的开发机上，用 `real_venv` 的准备类端到端用例会判「来源未定」；CI 与 ASCII 路径不受影响，BASELINE §13）。
* 真 Windows 的路径 / 文件系统 / 冻结运行时形状没有真机证据（§5.2）。
* e2e 的受管环境是离线补丁建出来的（§0 C 的限定）。

## 11. 上线与回滚建议

事实：新增的联合计划字段 `origins` / `user_specified`、会话检查项 `detail.origins`、`ImportClass` 的追加字段都是**可选字段**，`plan_version` 不升、旧键不变，老客户端忽略它们；没有新增已发布的错误码、没有新增 `BLOCK_REASONS`；默认扫描没有接线来源事实；`IMPACT_VERSION` 1 → 2 只在用户指定包名的计划里才会出现非空的 `user_specified`（没有用户指定包时字段是空表）。

建议（**建议，不是已验证的流程**）：

1. 现有的灰度开关够用，不新增：环境采用模式 `TAVOTTO_ENV_ADOPTION`（默认 `detect`）、`TAVOTTO_USER_ENV_DISCOVERY=0` 整个关掉用户环境发现。来源事实本身没有开关——它们只读、不执行、不改变任何既有判决之外的东西（改判的是此前误判的条目，BASELINE §12）。
2. 观测阶段看：联合计划里 `origins` 的 `reason` 分布与会话 `detail.origins.by_reason`（只有计数与原因码，可从会话报告读，不进诊断）。
3. 回滚按层，从上往下互不依赖：先回 #934（前端，纯展示，后端字段无人读也不影响）→ 再回 #933（`user_specified` / 装后核验 / `IMPACT_VERSION=2`；回滚后用户指定包名这一档消失，表外名回到「只能运行后单包修复」）→ #930 → #920（`origins` 与 `JointPlan` 的来源判决；回滚后 M5 这类误判回来）→ #906 → #882 → #880。PR7 的内容（测试 / 文档 / `importscan._is_module_name`）可以单独回滚。
4. 合入 PR7 之前：本分支的新增用例要先过一次完整 CI（尤其 posix-e2e 腿首次执行 `import-origin.spec.ts`，以及 backend-platforms 的 Windows 腿执行 `test_import_origin_cross_platform.py`）。

## 12. 仍未覆盖与相关 issue 现状（`gh issue view` 核对，2026-10-11）

「代码已修」指有合入的 PR 与用例；issue 状态以 GitHub 为准，**仍是 OPEN 的由维护者关闭**（它们的 PR 正文没有用能自动关闭的关键字）。

| issue | 现状（GitHub） | 实际 |
| --- | --- | --- |
| #888 builtin 名单、扩展后缀取目标解释器；截断标不完整 | OPEN | **代码已修**（#920）：`tests/test_import_origin_target_facts.py::TestBuiltinNamesComeFromTheTarget`、`::TestExtensionSuffixesComeFromTheTarget`、`::TestTruncatedScansAreNotComplete` |
| #889 distmeta 剩余三条 | OPEN | **代码已修**（#906 的 `6627b05d5`）：`tests/test_import_origin_metadata.py::TestIssue889` |
| #908 被全局解释器覆盖的项目环境不应标 target 角色 | OPEN | **代码已修**（#920）：`tests/test_environment_coverage.py::test_J_a_project_candidate_overridden_by_the_global_setting_is_a_user_environment` |
| #922 执行期过期检查未绑定 import 层 | **CLOSED**（#930） | 已修：`tests/test_import_origin_plan_backlog.py::TestExecutionTimeLayerCheck` |
| #924 映射发行包与备选同时装着应判歧义；内置候选角色尊重全局锁 | OPEN | **代码已修**（#930）：`tests/test_import_origin_plan_backlog.py::TestMappedAndAlternativeBothInstalled`、`::TestBuiltinCoverageRoleFollowsTheGlobalLock` |
| #925 未建模 `.pth` 钩子应让名字层判不完整；静态索引用目标扩展后缀 | OPEN | **代码已修**（#930）：`::TestUnmodelledPthHooks`、`::TestExactExtensionSuffixes`、`::TestNspkgPthHooks` |
| #929 静态索引应保留 sys.path 交错顺序 | OPEN | **代码已修**（#930）：`::TestSysPathInterleaving` |
| #931 Python 不处理 `.pth` 的层里的符号链接 `.pth` | **CLOSED**（#933） | 已修：`::TestPthDerivedModelsOnlyForProcessedLayers`、`::TestSymlinkedPthInLayerDigest` |
| #932 包初始化文件按目标扩展后缀判；层指纹覆盖已有层内的提供者名字 | **CLOSED**（#933） | 已修 |
| #918 补审跟进：诊断里的安装影响摘要可离线猜解；自动采用路径未与安装互斥 | OPEN | **未修**；第 1 条与本工程直接相关（§7.1）；第 2 条（自动采用路径未接 `envlease.inspecting`）与 Import Origin 无关 |
| #935 AGENTS 拼接上限测试只在 Windows 见红 | OPEN | 与本工程无关；本 PR 的 AGENTS 增量按 CRLF 最坏情形计，backend 一对余量 222 字节、web 一对 133 字节（§13） |
| #837–#842 | OPEN | #811 的 P2 跟进（projscan / prepsession / pool），不属于 Import Origin 本身，仍开 |
| #864（PR，非 issue） | **CLOSED 未合入** | 被 PR5b 的「预填 + 用户确认」取代（裁决 C1） |

## 13. 文档与速查

* [ADR 0119](../../adr/0119-import-origin-static-evidence.md)；规则全文 `docs/rules/backend/dependency-repair-and-packages.md`「Import Origin Resolver 收口」一节；
* `src/tavotto/AGENTS.md` 速查表只改了依赖那一行（加 ADR 0119 / 来源解析默认零执行 / 三个看护文件）；AGENTS.md + 子目录 AGENTS.md 拼接按 CRLF 最坏情形：backend 一对 32546 / 32768 字节（余 222），web 一对不变（32635，余 133）（#935 的口径）；
* 用户文档 `docs/user-guide/python-environment-preparation.md`；
* 实施记录 `docs/implementation/import-origin-resolver/00_BASELINE.md` §16（PR7）。

## 14. 未经 Codex 安全评审的 PR

**#880、#882、#920（部分）、#930、#933、#934 的 PR 正文都注明「未经 Codex 安全评审」**（#880 / #882 / #930 / #933 / #934 写的是额度用尽；#920 写的是 `b0ccc37` 起额度用尽、此前评审过）；**#906 正文写「Codex 安全评审已于 4b458e62a 自动完成，未发现安全问题」**。这批 PR 的补评跟踪正文都指向 #846，但 **#846 已于 GitHub 上关闭（COMPLETED）**，它的清单只含 onboarding 叠栈 #812–#820（全部已勾选）——**Import Origin 这批目前没有开放的补评跟踪项**。需要维护者决定：重开 #846 并追加这批，或新开一个 issue。优先级建议（`risk:high` 且触及安装边界）：#933（用户指定包名进入安装集合、`IMPACT_VERSION` 1→2、装后核验）> #930 > #920 > #880 / #882（静态读取与元数据读取，零执行边界由结构门禁 + 桩 + 变异守住）> #934。
本 PR7 新增了一个 `src/` 改动（`importscan._is_module_name`，§3）与测试 / 文档，同样**没有**安全评审。
