# ADR 0119：Import 来源解析——静态、零执行、只观测的证据层；「装可信包」与「确认包名」是两次不同的授权

日期：2026-10-11 · 状态：**Accepted**（PR1–PR6 已合入 main，本 ADR 随 PR7 补记**已经发生的决定**与它们的边界；每一条都指向代码或用例）
相关：[0114 环境建议 / 检查 / 采用](0114-environment-recommend-check-adopt.md)、[0115 授权绑定实际影响](0115-dependency-authorization-binds-impact.md)、
[0061 联合依赖准备](0061-joint-dependency-preparation.md)、[0019 受控依赖修复](0019-controlled-dependency-repair.md)、
[0057 首开的环境与工作目录](0057-first-open-environment-and-workdir.md)；规则全文 `docs/rules/backend/dependency-repair-and-packages.md`
「Import Origin Resolver」各条、`docs/rules/frontend/readiness-and-left-shell.md`；实施记录与每个 PR 的取舍
`docs/implementation/import-origin-resolver/00_BASELINE.md`（§1–§15），验收 `…/FINAL_ACCEPTANCE.md`；用户文档 `docs/user-guide/python-environment-preparation.md`

## 问题

升级之前，「脚本 import 的东西从哪来、缺了怎么办」靠一张名字表（`depresolve.CURATED`）加几条启发式（BASELINE §2 Q2–Q4）。在真实 Python 的导入语义面前，
有六类**已经运行复现**的误判（BASELINE §2 Q3 的 M1–M6）：项目里的 `json.py` 被当成标准库；同名的包与单文件模块、大小写不同的文件分不清；
包内相对导入不跟进；`cv2` 这类一个 import 名对应多个发行包的情形被静默挑了一个；动态导入的别名调用漏检。更根本的是两件事：

1. 全仓**没有任何地方**静态读已安装发行包的元数据（BASELINE §Q5）：「这个 import 在目标环境里是谁提供的」只能起一个子进程问 `importlib.metadata`，
   而那是在用户没有授权执行之前不该做的事；
2. 表外的名字（`import lifelines` 这类 curated 表里没有的）只有两个出路：猜 PyPI 同名包（#864 的方向，违背「不从 unknown import 猜 PyPI 包名」），
   或者等运行失败后走单包修复——此时用户已经执行过一次脚本。

同时底线不能动：默认扫描绝不执行用户代码；内置 runtime 永不是安装目标；授权绑定的是实际影响（0115）。

## 决策

### 一、升级的是**证据层**，不是第二个解析器，更不是新的安装器

既有的「唯一权威」一个都没换：`importscan` 仍是静态导入分析的唯一出处；`depresolve` 仍是包名可信解析（`INSTALLABLE_SOURCES` 仍是既有的三档 project_declared / curated / user_specified，没有第四种自动可信来源，
`tests/test_import_origin_user_specified.py` 钉着这个元组）；`depplan` 联合计划；`envadvice` 管建议 / 检查 / 采用；`deprepair` 管安装与验证；`managedenv` 管受管环境生命周期。
新增的只有 `engine/distmeta.py`——**纯文件系统**的发行包元数据读取器，是 `depplan._FACTS_SRC`（在目标解释器里读 `importlib.metadata` 的子进程）的**静态对偶**，
共用 PEP 503 规范化键。数据模型是在 `importscan.ImportClass` 上**追加可选字段**（旧的 bucket / context / needed 一字不变，`tests/test_import_origin_static.py::TestContract::test_the_old_payload_keys_are_unchanged_and_new_ones_are_appended`），
没有建第二个 AST 扫描器。

**为什么不重建安装器**：0019 / 0061 / 0115 已经有计划先行、影响摘要、认领幂等、采用互斥、作用域互斥、失败旧代可用、装后自检（BASELINE §7.2 的 13 条逐条映射到现有用例）。
这次缺的只有两样——装后「发行包与版本」核验（`deprepair._verify_distributions`）与用户确认包名这一档来源（见九）——都落在既有流程里。

### 二、默认扫描不调任意 `find_spec` / `import`（零执行），只用五样东西

`ast`、目录清单（`os.scandir`，不跟随链接）、`pyvenv.cfg`、`*.dist-info`（`METADATA` / `top_level.txt` / `RECORD` / `direct_url.json`）、项目声明文件。理由：
`importlib.util.find_spec("pkg.sub")` 会先**导入父包**，也就是执行用户包的 `__init__`；`.pth` 里的 `import` 行与 `sitecustomize` 在解释器启动时就会跑。
这些在用户没有授权执行之前都不该发生。看护有三层：

* 行为：恶意包 `SIDE_EFFECT` 哨兵——`from dangerous_package import submodule`、`.pth`、`sitecustomize`、editable finder 扫完 / 读完，哨兵文件不存在
  （`tests/test_import_origin_safety.py::TestScanRunsNothing`、`tests/test_import_origin_metadata.py` 的 `test_reading_and_scanning_execute_no_pth_sitecustomize_package_or_finder`）；
* 结构：`importscan.py` 对兄弟模块只用白名单属性，`distmeta.py` 源码里没有进程 / 网络 / 动态执行 / `importlib.metadata` 的名字（AST，
  `TestStructuralGate`、metadata 的 `test_no_process_network_dynamic_execution_or_importlib_metadata_name_appears`）；每个文件都经 `scanbudget` 读、不直接 `open`；
* 默认项目扫描的候选子进程数恒为 0（`tests/test_project_scan_zero_exec.py`；5 / 30 / 100 脚本的计数见 `tests/test_import_origin_perf.py`）。

### 三、默认扫描与「授权检查」的边界，不假装无副作用

| | 默认扫描 / 建议 / 依赖门 `cache_only` / 静态索引 | 授权检查（`POST /api/engine/environment/check`，`coverage: true`） |
| --- | --- | --- |
| 起候选解释器 | 否 | **是**（只起被点名的、最多 6 个） |
| import 脚本要的包 | 否 | **是**——执行它们的 `__init__`，解释器启动期的 `.pth` / `sitecustomize` 也会跑 |
| 如实标注 | — | 结果的 `executed`（起了几个候选、import 了哪些名字、`ran_user_code`、`may_run_package_init`）、候选覆盖度的 `executed_user_code`、`check.executes_user_code: true`、`side_effect_free` **恒为 False** |

「无副作用」这个说法从来不在任何一个返回值里出现为真（`tests/test_environment_coverage.py::test_the_default_scan_never_runs_user_code_and_the_authorized_check_does_and_says_so`
用真 venv 里会写文件的包与 `.pth` 证明了两边）。`execution` 账按**实际完成情况**填，不按「打算做什么」填（`projectenv.execution_note`，起不来 = 全空）。

### 四、状态与置信度模型：只观测，不下不该下的结论

* `ImportClass` 追加：`origin_kind`（9 种：built-in / frozen / stdlib / project-local / site-packages / editable / namespace / extension / unresolved）、
  `resolution_status`（resolved / ambiguous / unverified / unresolved / unsupported）、`evidence`（来自哪一档证据）、`shadowing`、`warnings`、
  `distribution_candidates` / `selected_distribution` / `observed_*` / `declared_*` / `distribution_status`（`distmeta.STATUSES` 9 种）/ `compatibility`。
  判不出就是 unknown / unverified / unsupported，**绝不猜**（BASELINE §Q3 的每条误判各有回归用例，O-01…O-20 见 FINAL_ACCEPTANCE 的映射表）。
* **联合计划只加可选的 `origins`，不新增 `BLOCK_REASONS`**（裁决 C3）：来源未定的 import（多发行包都装着 / editable / 本地 / VCS / Conda / 未确认 / 导入失败 / 表外名）
  离开 `missing`、不进安装集合，落在 `origins[]`，`reason` 是闭集 `depplan.ORIGIN_REASONS`（7 个）。`plan_version` 不升，老客户端忽略它。
  `BLOCK_REASONS` 仍是 5 个（用例钉着闭集，`tests/test_dependency_plan.py::TestClosedSetsMirrorTheFrontend`）。
* 判决顺序就是证据强弱（`depplan._verdict`）：名字级已装 > 已装的备选发行包 > 多个备选都装着（歧义，项目声明点名的胜出）> 别的已装发行包声称提供它 > 包在导入失败 > 缺。
* 前端的**五种展示态**（已就绪 / 发现更合适的环境 / 可以自动修复 / 需要确认 / 尚无法确定）只是**已有后端状态的命名**（`web/src/lib/importOrigin.ts`），
  前端没有解析、名单或映射（`importOrigin.structure.test.ts`）。

### 五、元数据证据：是证据，不是授权

读 `top_level.txt` / `RECORD`（前 N 行）/ `direct_url.json` / editable finder 的 `MAPPING` / Conda 的 `conda-meta`；site-packages 位置由 `pyvenv.cfg` 加约定布局推出，推不出就 `unverified`。
一个 import 名对应多个发行包（`cv2` 的四个）**全部保留、不挑**；声明与已装版本冲突只报告、两边都不覆盖。editable / 本地 wheel / VCS / 无元数据**不得**转成 pip 包名
（`distmeta.REPRODUCIBLE_PROVENANCES` 只有 index 一种）。已安装的元数据**从不**造安装名（用例：运行环境里 headless 提供 `cv2`、新一代装的仍是 `opencv-python`）。

### 六、与 0114 / 0115 的关系

* **0114（建议 / 检查 / 采用）**：不改它的三个动作与默认的「检测模式」（裁决 C2：默认仍是 `detect`，能跑就用；确认模式仍可选）。只让「检查」多答一件事——脚本要的 import 在这个候选里装齐了没有
  （覆盖度：`covered` / `partial` / `missing` / `unusable` / `not_checked`；`not_found` 与 `import_error` 分开，装它救不了的不当缺）。**覆盖度只有一份缓存**（`userenvs._probe_cache`，键含环境代，import 集合排序），
  检测、依赖门、`recommend` 读的是同一份。结构化 detail 不是发布的错误码（`envadvice.DETAIL_CODES`）。
* **0115（授权绑定实际影响）**：不改它的一个字节。新增的一类影响——「装的包名不是 Tavotto 解析出来的」——所以 `IMPACT_VERSION` 1 → 2、摘要含 `user_specified`，
  旧的同意不覆盖它（用例：去掉字段 / 版本 1 的形状摘要都对不上）。采用仍 ≠ 安装；安装目标仍只有受管环境与项目 `.venv`（显式授权），bundled 永不是目标。

### 七、缓存与失效：层指纹与提供者指纹

计划与「执行期的世界」不能错位。`prepare` 在重量事实（不走缓存）之后比两个指纹，不一致 = 同一条 `repair_plan_stale`：

* **层指纹** `depplan.layer_digest`：`site_roots` / `extra_roots` / `opaque_roots` / `path_order` 加每个 site 层里 `.pth` 的内容摘要（单文件 ≤ 256 KiB、每层 ≤ 200 个）。
  量事实的同一刻就算好（与静态索引同一刻读，计划与它描述同一个时间点）；
* **提供者指纹** `depplan.provider_digest`：只记**计划用到的 import 名**在静态索引各层的提供状态——不把整层名字并入指纹，因为无关包的安装不该让计划过期。
* 都不进 `impact` / `to_payload` / `identity` / `inputs_digest`：无关的重装不会因此发生；目标事实 `builtin` / `ext_suffixes` / `site_roots` 不进 `TargetFacts.digest()`。
  `static_index` 与 `target_facts` 同一刻读、同一个 `reset_cache` 清（否则装上但验证没过的包会一边说没装一边说装着，`test_foundation_dependencies.py::test_fo22_…`）。

### 八、不支持 / 保守的场景（如实不做，而不是猜）

zip / egg 里的名字（`opaque_roots > 0` 时名字层判不完整）；未建模的可执行 `.pth` 行（`uncovered_paths`，只有已知良性钩子——setuptools 的 distutils 垫片、virtualenv 的 `_virtualenv`、
`*-nspkg.pth` 的固定模板——按整行内容精确匹配才算已建模）；`sitecustomize` / `usercustomize` 里 `site.addsitedir` 的目录；动态导入（`dynamic`，不当必需依赖）；
命名空间包不强行选唯一提供者；多路径上额外层与 site 层不按 sys.path 交错排序之外的遮蔽顺序（#929 已修主要形状）；真 Windows 的 `sys.path` 布局 / NTFS / junction 真实判定（只有替身用例，见 FINAL_ACCEPTANCE）。
读不全就当「来源未定」，**宁可不完整当未定，也不漏层后去装**（`depplan._verdict` 的 `index_incomplete`）。

### 九、维护者裁决（BASELINE §8）

* **C1（#864 关闭）：预填 + 用户确认，不猜同名**。`depresolve.suggest_distribution` 只对「纯粹的表外名」给输入框初始值，**不是**可信来源、不进 `missing` / `requirements`、不自动装；
  用户确认（可改）后经 `POST /api/engine/dependencies/plan {user_specified}` → `depplan.plan(user_specified=)` 才进计划，`resolution_source=user_specified`，
  只能装到受管环境或项目 `.venv`。包名与约束过 `depresolve.from_user_input` 的严格语法并从解析结构重新序列化，原文不进 argv。
* **C2**：默认采用模式保持 `detect`（上面六）。
* **C3**：不新增 `blocked` 理由，用可选的 `origins`（上面四）。
* **C4**：PR3 起基于 main（onboarding 叠栈已合入后再开）。
* **C5**：`GET /api/engine/dependencies` 会起解释器，**不承担**新的静态来源结果；来源事实只走两条零执行路径——联合计划的 `origins`（静态读目标环境元数据）与准备会话 `checks_of` 的
  `dependencies` 项（`detail.origins`：计数与闭集原因码，不出名字 / 路径）。默认扫描（`projscan`）没有接线，候选环境子进程数仍为 0。

### 十、为什么「装可信包」与「确认包名」是**两次**授权

同一份脚本既缺可信包（curated 表里的 `python-docx`）又有表外名（`mystery_lib`）时：先走「可以自动修复」（可信来源，一句话 + 一个主按钮，影响摘要绑定），**装完会话重新检查后**才出现「需要确认」；
确认包名的框里一份计划、所有输入框一次授权（不逐包弹窗）。没有合成一次，理由：

1. **信任等级不同**。可信包的来源是 Tavotto 的表 / 项目声明；用户指定的包 `trust` 恒为 `user_specified_unverified`，确认页必须单独、明白地说「未经可信来源核实」。
   塞进同一个按钮下，用户会在一次点击里同时接受两种信任等级。
2. **摘要语义**。`impact_digest` 绑定的是一份计划的实际影响；把 `user_specified` 并进 `DependencyPrepareDialog` 的 `prepare` 会改它的摘要漂移判定（PR6 的评估），属于另一个 `risk:high` 的改动。
3. **失败隔离**。用户填错包名时（装上的包没有脚本要的库）装后核验会拦住（`distribution_does_not_provide_import`），受管环境保留旧代；可信包的安装不该被它拖累。

代价是同一个项目偶尔要点两次。若以后要合成一次，必须让 `prepare` 带 `user_specified` 并重做摘要漂移判定，同时保留确认页的独立标注——不是改文案能做到的。

## 不做 / 边界

* 不新增第四种自动可信来源；不从 unknown import 猜 PyPI 名；不静默安装；不写 bundled runtime。
* 不为「已装元数据」造 requirement；不在默认扫描里读候选环境（目标环境没有被授权前不读）。
* 不重做自动检测与推荐顺序（`rank()` / `best()` 未动）。
* 不新增已发布的错误码（装后核验失败复用 `dependency_import_still_failed`，闭集原因码 `deprepair.DIST_REASONS` 随终态进度走）。

## 后果 / 已知差距

* 默认扫描没有接来源事实：卡片上的「需要确认 / 尚无法确定」只在**准备会话已经评估过依赖**之后出现（第一次检查之前没有这一行）。
* **确认包名并装好之后，卡片仍说「来源还没法确定」**（`origins[].reason = unverified`，而 `distribution_status = installed_confirmed`）：`depplan._verdict` 对「表外名 + 已装提供者」一律判来源未定。
  运行没有问题；真浏览器用例 `web/e2e/import-origin.spec.ts` ④ 钉着现状。修法与取舍见 FINAL_ACCEPTANCE.md「已知缺陷」#1，需要维护者决定。
* `found` 改判（覆盖度说它能 import、发行包名却没装）与「依赖门展示覆盖度 detail」未做（BASELINE §12、§15）。
* 真 Windows 的路径 / 文件系统 / 冻结运行时形状没有真机证据（FINAL_ACCEPTANCE 的「需 Windows 真机 / CI 验证」）。

## 看护

`tests/test_import_origin_static.py`（O-01…O-20 的静态半边，含真实 Python 对拍）、`test_import_origin_metadata.py`、`test_import_origin_safety.py`、`test_import_origin_target_facts.py`、
`test_import_origin_plan_backlog.py`、`test_import_origin_user_specified.py`、`test_import_origin_ui_facts.py`、`test_environment_coverage.py`（环境 fixture A–J）、
`test_import_origin_cross_platform.py`、`test_import_origin_perf.py`、`test_import_origin_diagnostics_privacy.py`；前端 `importOrigin.test.ts` / `importOrigin.structure.test.ts` /
`ImportOriginDialog.test.tsx` / `PreparationCard.test.tsx`；真浏览器 `web/e2e/import-origin.spec.ts`。
