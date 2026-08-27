# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-27
- 当前 branch：`compat/bridge-session07-project-env`（worktree
  `.claude/worktrees/compat-bridge-session01`），已 rebase 到 `origin/main`
  的 `8e5a67f` 并推送 → **PR #177**
- 本 branch 上现在有**两个 Session 的工作**：Session 7（项目 Python 环境
  自动发现与无感切换，ADR 0018）与 **Session 7B（受控依赖修复，ADR 0019）**。
  7B 建在 7 上面，**没有改动 7 的任何行为**（见下方「Session 7 一个字节没改」）。
- 前一轮：PR 1（#127）Session 1–6 已合入 main（squash `6aeca9e`，2026-08-26），
  交接收尾 `9f48357`（#135）
- 受管产物 `canvas.html` 指纹 **`2c5f737bd8d7903c`**（在 `8e5a67f` 上重建，
  `--check` 通过）。**它会再次过期**：`tavotto-a7` 手上六条 PR
  （#157/#160/#161/#164/#168/#171）都重建这份产物且排在前面，入队前要按
  合并态再重建一次。rebase 时它撞了 3 次，每次都是重跑 `build_mcp_widget.py`
  然后 `git add`——**managed artifact 的冲突不用手工解**，10 秒一次。

## Session 7B 做了什么

Session 7 解决的是「项目自己带着一个能跑通的 `.venv`」。真实用户里还有一半
不是这样：项目有 `.venv` 但它也缺这个包、或者项目根本没有 venv。那时 Tavotto
给出的仍然只有一句 `ModuleNotFoundError` 与「去设置里手填一条解释器路径」。

本轮给这类局面一条产品化的路：

```text
missing_dependency（唯一触发器）
    ↓  import 名 → 可信的 distribution（解析不到就停在这儿）
    ↓  选目标：项目 .venv（改用户环境）/ Tavotto 受管环境（改我们自己的）
    ↓  用户明确点一次（改用户环境时文案说清「这会改你的环境」）
    ↓  pip install（wheels 优先、shell=False、不 --upgrade）
    ↓  验证三层：import 那个包 / import matplotlib / 真起一次 worker
    ↓  作废旧 worker → 重跑脚本 → Figure 出来
```

## Session 7 一个字节没改

下面这些是 Session 7 的实现面，本轮**只读、只复用，没有修改语义**：

- `engine/projectenv.py`：发现、体检、记住/忘记、项目级缓存与重试上限——
  整个文件本轮**零改动**。
- `pool.resolve_worker_python()` 的优先级链（env > 设置 > 项目记住的 >
  内置/自身/系统）与「两条显式来源各自判、不短路」。
- `pool.should_try_project_env()`（只有 `missing_dependency` 触发）与
  `pool.build()` 的「一次 build 最多自动切一次」。
- `pool.get()` 的身份守卫（入口已变 / 渲染解释器已变）。
- `app._switched_to_project_env()` 与 `_engine_attempt()` 的端点侧重试。
- `GET/PATCH /api/engine/environment` 的既有字段与 `scope="project"` 语义。
- 诊断包的 `project.environment_resolution`（本轮是**新增**了平级的
  `project.dependency_repair`，没动它）。

pool 侧本轮的增量都是**加**出来的，不改既有判据：`SOURCE_MANAGED_PROJECT`
（受管环境的来源标签）、`remembered_source()`、`note_project_python_ok()`
（把 `try_project_env` 里那两行重复的登记收成一处）、
`mutating_environment()` / `is_mutating()` / `shutdown_workers_using()`。

## 已完成

- [x] **`engine/depresolve.py`**（新）：import 名 → distribution 的可信解析。
  三档来源（project_declared / curated / user_specified），**没有第四档**；
  curated 分「名字不同要查表」与「同名但显式登记过」两张表；包名语法作为
  安全边界（白名单，URL/VCS/路径/extras/marker/带空格一律拒）。
  依赖声明只读（requirements*.txt / pyproject 的 project + optional +
  poetry），解析失败只让这一档不可用、不连坐。
- [x] **`engine/managedenv.py`**（新）：`<data_dir>/environments/<项目指纹>/`。
  一个项目一个、绝不建在用户项目里、不带 `--system-site-packages`、只装
  matplotlib（numpy 由它带）。`environment.json` 记 schema / 是不是我们建的 /
  Python 版本 / 基础解释器指纹（**不是路径**）/ 装过什么。
- [x] **`engine/deprepair.py`**（新）：错误码表、`RepairPlan`（绑项目 + 环境
  指纹 + 需求 + 有效期）、`offer()`（只读的修复建议）、`create_plan()`、
  `install()`（环境锁 → pip 可用性 → pip → 三层验证 → 记账 → 作废 worker）、
  `cancel()`、`rebuild_managed()`、`worker_self_test()`、日志脱敏、
  `diagnostics_state()`。
- [x] **`pool` 侧的 worker 生命周期**：安装期间该环境上的会话全停 +
  `get()` 拒起新会话（`environment_mutating`）；锁按环境不按全局。
- [x] **端点**：`POST /api/engine/dependency/plan | install | cancel`、
  `GET /api/engine/dependency/state`、
  `POST /api/engine/environment/managed/rebuild`。进度经 SSE
  `engine.dependency`。
- [x] **修复建议挂在两条入口上**：渲染端点的 `_worker_error_payload` 与
  素材库那条 `registry/probe`（`probe._error_from_worker`）。只给一条的话
  「素材库里打不开、面板里能修」又是一次两个入口两个答案。
- [x] **前端**：`DependencyRepairCard`（起点 → 确认 → 进度 → 结果）、
  `depRepairStore`、SSE 路由、`ManagedEnvironmentRow`（设置页里的「装了
  什么 + 重建」）、中英各一份文案、按钮进 overflow 字数预算表。
- [x] **诊断**：`project.dependency_repair`（修过几轮、受管环境状态与
  包名+版本）。**没有路径、没有 pip 配置、没有 index 地址。**
- [x] **ADR 0019** 与 `docs/compatibility/legacy-projects.md`（兼容层从四层
  变五层，新的第 3 层就是本轮）。

## 刻意没做

- **没有加遥测事件**。`EVENTS` 扩容意味着采集范围变化，按既有纪律要升
  `CONSENT_VERSION` 并让所有人重新同意一次。为了几个计数让全体用户重新表态，
  这笔账本轮不划算。要数据时单独一轮做，连同代理侧那份对拍表一起。
- **没有实现 `tavotto run`**（ADR 0014 仍是 Proposed）。
- **没有自动 `pip uninstall`**、没有自动改 requirements/pyproject、没有自动
  建 `project/.venv`、没有静态扫描后批量安装、没有 sdist 编译。

## 负向反证

**后端十五条**（变异脚本在仓库外，**从内存还原不用 `git checkout`**——
工作区有未提交改动时它会一起吃掉，Session 7 就吃掉过一次）：

| # | 抽掉什么 | 哪条用例变红 |
|---|---|---|
| 1 | 内置 runtime 可以当安装目标 | `test_the_bundled_runtime_is_never_a_mutation_target` |
| 2 | 没有计划也能调安装接口 | `test_install_endpoint_refuses_without_a_plan` |
| 3 | 未知 import 按同名装 | `test_an_unknown_import_is_never_installable` |
| 4 | 包名语法放行（option injection） | `test_package_option_injection_is_rejected` |
| 5 | pip 成功后不做 import 探测 | `test_pip_success_alone_is_not_success` |
| 6 | 装完不作废 worker | `test_the_old_worker_is_gone_and_the_new_one_uses_the_new_interpreter` |
| 7 | 安装期间旧 worker 照常工作 | `test_workers_on_the_mutating_environment_are_stopped` |
| 8 | 安装期间还能起新会话 | `test_a_new_session_is_refused_while_the_environment_is_mutating` |
| 9 | 修复轮次没有上限 | `test_repair_rounds_are_capped` |
| 10 | 受管环境被所有项目共用 | `test_managed_environments_are_project_scoped` |
| 11 | 计划不绑环境指纹（TOCTOU） | `test_a_changed_environment_makes_the_plan_stale` |
| 12 | 受管环境不隔离（`--system-site-packages`） | `test_managed_venv_creation_is_isolated_and_minimal` |
| 13 | pip 默认 `--upgrade` / 允许 sdist | `test_pip_argv_is_a_list_wheels_only_and_never_upgrades` |
| 14 | 装完不做 worker 自检 | `test_imports_can_pass_while_the_worker_still_cannot_run` |
| 15 | 没有 pip 就静默 ensurepip | `test_an_environment_without_pip_is_reported_not_silently_fixed` |

**前端八条**：不提示「这会改你的环境」/ 解析不出包名也给一键安装 / 安装请求
带前端拼的包名 / 轮次用完仍给入口 / 不可用目标也列出来 / pip 日志糊在主文案
上 / 失败甩后端中文原文 / 取消后对用户环境也说「已回滚」——全部变红。

### 两条第一轮抽掉不红（都是**缺维度**，不是死代码）

- **#14（worker 自检）**：原本挂在 golden path 上，而那条用例里环境本来就
  是好的，跳过自检什么都不变。补了「前两层都放行、worker 仍然起不来」
  （字体缓存不可写 / `.so` 只在子进程里崩的同形状）之后才真正看护到它。
- **前端 #2（解析不出包名也给一键安装）**：用例里 `targets` 恰好是空的，
  guard 抽掉照样没按钮。补了「后端给了目标但没有可信包名」那一维——一键
  安装的前提是「知道要装什么」，不是「有地方可以装」。

## 真安装 E2E 怎么做到不联网

临时目录里**手工拼一个纯 Python wheel**（wheel 就是约定好目录结构的 zip：
模块 + `dist-info/{METADATA,WHEEL,RECORD}`），再用 pip 自己的
`PIP_FIND_LINKS` + `PIP_NO_INDEX` 指过去。后者顺带验证了「index 用那个环境
自己的配置」这条决策：**安装命令一个字节都不用为测试改动**。

断言必须证明**包真的进了那个环境**：site-packages 里有那个文件、在那个解释器
里 import 得到、`sys.prefix` / `sys.executable` 是它。

受管环境那组另有一个**离线 fixture**（基础栈换空表 + venv 带
`--system-site-packages`，因为 CI 装不了 matplotlib）。被放宽的那两条**另有
单元用例逐字节钉住**——否则「离线 fixture 好使」会掩盖「生产上建出来的环境
根本不隔离」。

## 实际运行的测试

```sh
ruff check .                                               # 全绿
PYTHONPATH=src .venv/bin/python -m pytest -q -o faulthandler_timeout=600
                                                           # 全绿（无 F）
PYTHONPATH=src .venv/bin/python -m pytest -q \
    tests/test_dependency_repair.py tests/test_dependency_repair_e2e.py
                                                           # 80 passed
cd web && pnpm build && pnpm test && pnpm lint && pnpm i18n:check
                                                           # 1201 passed / 全绿
PYTHONPATH=src .venv/bin/python scripts/ci/compat_matrix.py --smoke   # 通过
python scripts/build_mcp_widget.py --check                 # 一致
```

## 已知失败与限制

| 问题 | 严重度 | 后续 |
|---|---|---|
| 没有 wheel 的包走不了一键路径（如实报 `dependency_requires_build`） | 中（如实记账） | 「允许源码构建」是高级功能，等数据 |
| 机器上没有任何可建 venv 的 Python 时受管环境这条路不存在 | 中 | 如实报 `managed_env_unavailable`，退回「选择其他 Python」 |
| 私有 index 上的包：pip 用那个环境自己的配置，能装；但**诊断里只记有没有自定义 index**，排障信息比公网少 | 低 | 刻意如此（凭据） |
| Conda 专属的包（只在 conda-forge 上）装不了 | 中 | 走「选择其他 Python」 |
| 受管环境按**项目路径指纹**寻址：项目整个挪走 = 换了一个环境（旧的留在数据目录里） | 低 | 用户可以重建；自动搬迁要另想身份模型 |
| 受管环境**不声称 lockfile 级复现**：重建时某个版本从 index 撤了会如实报错 | 低 | 刻意如此 |
| 取消对用户 `.venv` 没有回滚 | 中（**已在 UI 与 ADR 里说明**） | 不修，pip 层面做不到 |
| 真机（WebView2 / WKWebView 壳内）尚未走过这条路 | 中 | 与 Session 7 同一条待办 |
| Session 7 遗留：只认 `.venv`/`venv`/`env`；体检跑在 `-I` 下与 worker 的环境条件差异 | 见上一轮记录 | 未变 |

## 不得被下一 Session 破坏的约束

- Session 2–7 的全部约束仍然有效（见 git 历史里的上一版本文件，逐条未变）。
- **内置 runtime 永远不是安装目标**。缺包时它只是触发器。
- **未知 import 绝不按同名安装**。一键安装只允许 `project_declared` /
  `curated` 两档高置信解析。
- **包名与版本必须过 `depresolve.parse_requirement`**，且安装前再验一次。
  `shell=False` 不是免死金牌——pip 自己会把参数解析成选项。
- **没有计划就不许改任何环境**；计划绑项目 + 环境指纹 + 需求，执行端一个
  字节都不从请求体里读。
- **改用户 `.venv` 必须是用户明确点击的结果**，且**不假装能回滚**。
- **pip exit 0 不等于成功**：三层验证缺一不可。
- **安装期间那个环境上不许有 worker 在跑，也不许起新的**；锁按环境不按全局。
- **绝不自动 `pip uninstall`**、绝不自动改 requirements/pyproject、绝不
  自动建 `project/.venv`、打开项目绝不联网。
- **诊断与遥测里绝不出现 index 地址、pip 配置、绝对路径、凭据**。
- **merge main 之后必须重跑 e2e**（asset-library / golden-paths 至少各一遍）：
  windows-exe-smoke 的 Playwright 套件只在 merge_group 跑。大改动入队前
  先打 `full-ci` 标签在 PR SHA 上取证。

## 合并前必做

- [x] `git rebase origin/main`（到 `8e5a67f`），rebase 后**本地重跑**整套：
      ruff 全绿 / pytest 2449 passed / web 1208 passed / CompatBench smoke 通过 /
      `build_mcp_widget.py --check` 一致。
- [ ] **入队前再重建一次 `canvas.html`**：a7 那六条排在前面，先到先得，
      每次被顶掉都按「合并态重建 + `--check`」处理，**不手工解冲突**。
- [ ] 打 `full-ci` 标签在 PR SHA 上取证（大改动入队规矩）。
- [ ] **merge main 之后重跑 e2e**（asset-library / golden-paths 各一遍）：
      windows-exe-smoke 的 Playwright 套件只在 merge_group 跑。

### 与在途 PR 的两处交代（已同步给 `tavotto-d2`）

- **Ruff formatter 迁移（#159 / #175 / #176）**：本 PR **刻意不含格式化提交**。
  `ruff format src/tavotto/app.py` 会重排整个 3800 行文件——那几千行与本轮
  无关的 churn 会淹掉评审，还会和每一条动 app.py / pool.py 的 PR 撞车，
  而那正是 #175 存在的理由。实测本分支改过的 16 个 `.py` **全部**会被重排。
  #175 落地后本分支按全仓 `ruff format .` + rebase 收敛；#176 落地后提交前
  跑 `ruff check . && ruff format --check .`（与 CI 逐字相同）。
- **CodeQL 门禁红着，需要维护者出手**（`CodeQL gate` 是 required check，
  见 ruleset 21121430）。`CodeQL` 报 1 critical + 7 high，逐条定性写在
  PR #177 的 issuecomment-5438197651。

  **我开 PR 时的预判是错的，值得记下来**：我以为红灯来自「本 PR 改写了
  `pool.py::EngineWorker.__init__` 里的一行，把那条 critical dismissal 的
  指纹打散了」。实情不是——那 8 条**全部是新代码第一次被扫到**
  （`projectenv.py` / `depresolve.py` 是本 PR 新增的文件，main 从没扫过），
  与 dismissal 打散无关。`EngineWorker.__init__` 那条**要等本 PR 合进 main
  之后**才会落空。

  处置：**一条都没 dismiss。** main 上那 15 条的先例摆着——维护者
  2026-08-26 逐条手写的理由，那不是 PR 作者该代劳的动作，更不该为了让门禁
  变绿而做。也没有为了消 CodeQL 去改代码：#119 的守卫（正则白名单 +
  `shell=False` + list argv）已经是正确形状。

  其中 **#120（`app.py:3111`）要单独看**：它不在已 dismiss 的那三条所在的
  函数里（那三条在 `api_registry_probe`），而在本 PR 新增的
  `_set_project_environment`，且**故意接受项目外的绝对路径**（ADR 0018
  写明「用户显式挑的项目外解释器（conda 环境）才存绝对路径」）。要不要收紧
  是产品决定。

  **两条方法学**（都来自这次，值得下一轮直接用）：
  - 判「新增 high 告警是不是我引入的」，`state=open` 那个过滤器**会把
    dismissed 排除掉**，只看它会得出相反结论。
  - 判「我碰到那条 dismissal 了吗」，比 hunk 位置可靠的是**比锚点函数的
    AST dump**——排版、行号、rebase 都不影响它，只有实质改动会变。

## 待办（本 PR 之外）

- [ ] **真机验证这条路**：Windows/macOS 壳内用一个带 `.venv` 且缺包的真实
      项目走一遍完整修复（用户的 `2d 处理` 是现成样本）。CI 侧覆盖不了
      WebView2 / WKWebView 壳内交互。
- [ ] **网站 playground re-sync**（PR 1 起就挂着）：合并后 main 上
      `python scripts/build_browser_playground.py` → 网站仓库
      `pnpm sync-playground`。
- [ ] **curated 表的第一次扩充等真实数据**：哪些包最常缺、哪些解析不出来。
      `dependency_unresolved` 出现的频率就是这张表该不该长的依据。
- [x] 受管产物重建（本轮改了 `web/src`）：`canvas.html` 指纹
      d1aec7a5393dcb20 → 10f822e0302251de；playground 指纹 670ecdf05bede0f3
      （`web/dist-playground` 不进仓库）。

## 下一 Session：Session 8 — Matplotlib Bridge Technical Spike

**不要把 `tavotto run` 塞进 Session 8。** ADR 0014 的决策门没有变：只有当
真实数据表明剩余失败仍大量集中在 cwd / argv / shell env / `python -m` /
自定义启动语义上时，才恢复它。

本轮之后要回答的数据问题多了一条：

1. 之前失败的旧项目里，有多少因为项目 `.venv` 自动接手而成功了？（Session 7）
2. **有多少因为一键装依赖而成功了？**（Session 7B）
3. 剩余失败按原因分类各占多少？

分类口径不变：`environment_missing` / `unsupported_python` / `cwd_semantics` /
`argv_semantics` / `env_var_semantics` / `module_invocation` /
`package_layout` / `custom_launcher` / `artist_support` / `actual_script_bug`。

本轮的决策输出仍是：**DEFER_TAVOTTO_RUN**。

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md
src/tavotto/AGENTS.md 的「受控依赖修复」一节
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/compatibility/legacy-projects.md（兼容层五层）
docs/adr/0018-project-python-environment-resolution.md（Session 7）
docs/adr/0019-controlled-dependency-repair.md（Session 7B）
docs/adr/0014-safe-native-execution-profiles.md（仍是 Proposed）
src/tavotto/engine/{projectenv,depresolve,managedenv,deprepair}.py
src/tavotto/engine/pool.py 的 resolve_worker_python / build /
    try_project_env / mutating_environment
```

## 建议启动命令

```bash
git status --short && git log -10 --oneline
ruff check .
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_project_env.py tests/test_dependency_repair.py
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_dependency_repair_e2e.py        # 真装包，约 1–2 分钟
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
