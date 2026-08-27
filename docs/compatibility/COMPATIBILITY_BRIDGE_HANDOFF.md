# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-28
- 当前 branch：`compat/bridge-session08-native-spike`（worktree
  `.claude/worktrees/compat-bridge-session08`，**基于 `origin/main` `7952ceb`**，
  不 stacked 在任何未合并分支上）
- 本 Session Prompt：Session 8 —— Matplotlib Bridge Technical Spike
- 交付：**技术验证**（ADR 0020 定稿 + 可运行实现 + 62 条用例 + 8 条负向反证）。
  **不是产品**：`tavotto run` 不存在，spike 入口没有稳定契约。
- **Session 7（PR #177，项目 .venv 自动接手 / ADR 0018+0019）仍 open**。
  本 Session **刻意不 stacked 在它上面**：spike 与它没有代码依赖，独立分支
  两边都能先合（ADR 编号取 0020，与 #177 的 0018/0019 无冲突）。

## Session 8 结论（一句话）

> **GO / BRIDGE_RUNNER_SELECTED。** 在不改用户源码、不往用户环境装 Tavotto、
> 不重建用户 shell 环境的前提下，用户自己的 Python 跑用户的原脚本、Figure
> 留在那个进程里、Tavotto 经 loopback + worker 协议 v1 做 manifest / override
> / render / export——**全链已在真进程里跑通**（含只有 matplotlib 的干净 venv）。

完整裁决与证据在 **[ADR 0020](../adr/0020-native-matplotlib-bridge.md)**。

## 本轮交付

- [x] **共用编辑语义**：`engine/figsession.py`（`LiveFigureSession`）——捕获表 /
  FigState / manifest / render / render_png / preview_png / export / snapshot。
  `worker.Worker` 与 native bridge 都是它的消费者。**safe worker 语义逐条保留**
  （全量 pytest 绿）。
- [x] **共用信封语义**：`engine/wireproto.py`（v1 解析 / 校验 / 分派 / 回显 /
  错误信封）。native 只多一个命令 `continue`。**没有第二套 protocol semantics。**
- [x] **调用侧信封唯一出处**：`pool.build_envelope()`，`EngineWorker` 与
  bridge 客户端都吃它。
- [x] **`engine/bridgeboot.py`**：私有包命名空间装载器（`tavotto_bridge_runtime.*`）
  + `sys.meta_path` 后置 import 钩子（自己不 import 任何东西）。
- [x] **`engine/bridge_runner.py`**：交给用户 Python 的那份代码。收回
  `sys.path[0]`、装钩子、按 CPython 自己的做法组装 `__main__`（script）或
  `runpy.run_module(alter_sys=True)` + `sys.path[0]=cwd`（module）、屏障、
  loopback 控制循环。
- [x] **`engine/bridge.py`**：父进程侧（listen / spawn / 认证 / v1 请求）。
- [x] **`engine/bridge_spike.py`**：验证 CLI，**刻意没有接进 `tavotto` CLI**。
- [x] **`execspec`**：`native_spec()` / `bridge_argv()` + `raw_target` 字段
  （argv[0] 的对拍口径；native 的三条硬约束在构造时就拦）。
- [x] **`overrides._sibling()`**：两处 late import 不再走裸名（理由见 ADR 0020 §3.1）。
- [x] **打包**：`figsession` / `wireproto` / `bridge_runner` / `bridgeboot`
  进 `packaging/tavotto.spec`；`test_runtime_build` 的 import 闭包门禁从
  一个根扩到两个根。
- [x] **用例 61 条**（`tests/bridge/`，全部真起子进程）+ 助手
  `tests/support/bridgekit.py`。
- [x] **ADR 0020**（Accepted，technical spike）+ `src/tavotto/AGENTS.md`
  新增「两条执行入口」一节。

## 用例分布（tests/bridge/，62 passed + 1 slow）

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `test_bridge_namespace.py` | 6 | 装载器不变量、两阶段不重复装、用户项目 12 个同名模块全赢、late import、结构性守卫 |
| `test_bridge_invocation.py` | 6 | script/module 与真实 python 逐 13 字段对拍、绝对路径 argv[0]、不加解释器标志、env 原样继承、token 不进用户脚本 |
| `test_bridge_capture.py` | 12 | prompt §十三的 12 条形态 |
| `test_bridge_backend_and_show.py` | 8 | 不提前 import pyplot、三个后端、无 matplotlib 脚本、show 阻塞语义 |
| `test_bridge_transport.py` | 10 | loopback-only、错 token、token 随机、信封同源、stdout 噪声、断开、shutdown、不往用户 home 写、spike 未接进产品 CLI |
| `test_bridge_thread_model.py` | 8 | WrongThread、族判据、无后台线程、运行时线程 id 相等、零 pickle、通道只跑 JSON |
| `test_bridge_e2e.py` | 3 + 1 slow | 完整链（manifest→改字号→导出 PDF→撤销）、用户环境无 Tavotto、module 形态、**真 venv（`-m slow`）** |
| `test_bridge_injection_models.py` | 9 | A/B 实测对比（§17 的裁决依据） |

## 负向反证（本轮七条，全部先红后还原）

| # | 变异 | 判据 | 结果 |
|---|---|---|---|
| 1 | 摘掉 `plt.show` 钩子 | `test_show_blocks_by_default_and_returns_after_continue` | **红** |
| 2 | runner 顶上 `import matplotlib.pyplot` | `test_user_code_is_the_first_to_import_pyplot` + `..._never_touches_matplotlib...` | **红**（2 条） |
| 3 | 控制通道改回 stdin/stdout | `test_a_noisy_script_never_desyncs_the_control_channel` + E2E | **红**（2 条） |
| 4 | `figsession` 里 `import pickle` | `test_no_engine_module_ever_pickles_a_figure` | **红** |
| 5 | 捕获不去重（savefig 之后 Gcf 再收） | `test_savefig_then_still_in_gcf_is_not_captured_twice` | **红** |
| 6 | 两处 late import 改回裸 `import manifest` | `test_the_late_manifest_import_resolves...` + 结构性守卫 | **红**（2 条） |
| 7 | `_own()` 不再断言 / 摘掉 `do_render` 的那一处 | `..._refuses_to_be_touched_from_another_thread` / `..._every_mutating_entry...` | **各红一条** |
| 8 | engine 目录留在 `sys.path` 上 | `test_user_modules_win_over_the_engine_siblings` | **红** |

**反证 1 的诚实修正**：prompt 预期「去掉 show hook → show-only case 失败」。
实测**不失败**——脚本结束时的 Gcf 兜底照样把图捕获到。show 钩子的独有价值
是**中途屏障**（脚本还没跑完就能编辑），红的正是那一条。这是设计比预期更
稳健，不是判据不成立。

**反证 6 的第一版是空的**：最初拿「翻转色条方向」当判据，变异跑完全绿——
`_refresh_axes_follow` 外面包着 `except Exception: pass`，裸 import 在那里是
**静默**失败的。判据换成不吞异常的 `FigState.resolve` 那条路径（先把刻度
定位改 fixed + 给 15 个值，再改第 13 条刻度的文字，那条 gid 不在 index 里），
另加一条结构性守卫盖住整族。

## 本轮踩到并留了注释的三个坑

1. **`V1Handler` 的分派方法不能叫 `handle`**——safe worker 的 legacy 扁平信封
   入口历来就叫 `handle`，子类同名会静默顶掉，表现是每条 v1 请求都被答成
   「未知指令: render」。
2. **每个屏障都必须被应答**——一次运行里屏障出现多次（每个 `plt.show()` 一次
   + 脚本跑完一次）。只应答第一个然后去等 `exit`，两边各等各的，本机挂死。
3. **装载窗口内要把用户已 import 的同名模块挪开**——`importlib.import_module`
   先查 `sys.modules`，用户项目里的 `figsession.py` 会被当成我们的，报出
   `AttributeError: ... has no attribute 'LiveFigureSession'`。这条是用例
   抓出来的，不是设计时想到的。

## 未完成 / 进 Session 9 的入场券

- [ ] **Windows 真机执行**：`tests/bridge/` 全部平台无关，但**从没在 Windows
  上跑过**——「从没跑过的门禁不会保持正确」。入队前用 `full-ci` 标签取证。
- [ ] **native 会话是否进池复用**（ADR 0014 §7 第 4 问）。
- [ ] **产品面**：`tavotto run` 的稳定 CLI 契约与错误码表、桌面交接、UI 一次性
  确认（必须写明解释器路径 / cwd / 「拥有你当前用户的全部权限」）、每项目
  记住选择、SSE 进度。
- [ ] **CompatBench 的 `native_run` 路由**从 `not_implemented` 升级。
- [ ] **`_refresh_axes_follow` 的静默 except**：要不要收窄是独立一笔。
- [ ] **target 不存在时报的是 `script_error`**（带一段 FileNotFoundError
  traceback），分类不准——那是 invocation 层的错，不是脚本的错。归到
  Session 9 的「invocation parser 错误分类与稳定错误码表」一起做。
- [ ] **CI 时长**：`tests/bridge` 真起 ~70 个子进程（每个都要 import
  matplotlib），会加进 `backend-fast`（PR 每次跑，Linux 3.10+3.13）与
  `backend-platforms`（merge_group，mac+Windows）。**这正是 Windows 覆盖
  缺口的解药**（用例本身平台无关），但入队前请先看一眼这一格的时长有没有
  超出可接受范围；真超了再考虑把最重的几条挪到 `-m slow`。
- [ ] **网站 playground re-sync**：本轮动了 `overrides.py`（`_sibling`），
  playground 指纹变成 `1a7aefda8bbe880f`（canvas.html `9fe4aad080b18fa4`
  不受影响——它不嵌 Python）。`web/dist-playground/` 是 gitignored 的可再生
  产物，所以本分支里没有可提交的差异；合并后在新 main 上重跑
  `python scripts/build_browser_playground.py`，再去网站仓库
  `pnpm sync-playground`。
- [ ] 上一轮遗留：真机最终产物证据（§六）。

## 下一 Session 首先阅读

```text
docs/adr/0020-native-matplotlib-bridge.md   ← 本轮的全部裁决与证据
docs/adr/0014-safe-native-execution-profiles.md（§3/§7 已由 0020 裁决）
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
src/tavotto/AGENTS.md 的「两条执行入口」一节
src/tavotto/engine/{bridgeboot,bridge_runner,bridge,figsession,wireproto}.py
tests/bridge/（尤其 test_bridge_injection_models.py —— §17 的裁决依据）
```

## 建议启动命令

```bash
git status --short && git log -8 --oneline
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/bridge -q
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/bridge -q -m slow  # 真 venv，要联网
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m tavotto.engine.bridge_spike run \
    -- <你的python> <你的脚本.py>
```

---


---

# 历史（Session 7 / 7B —— PR #177，已合入 main `8118ba2`）

> 下面是上一轮的交接原文。Session 6 及更早的历史由 #177 收走（本轮沿用它的处置，不再重新展开）；需要时查 git 历史与 PR #127。

- 日期：2026-08-27
- 当前 branch：`compat/bridge-session07-project-env`（worktree
  `.claude/worktrees/compat-bridge-session01`），已 rebase 到 `origin/main`
  的 `e025612` 并推送 → **PR #177**，**三个 required gate 全绿**
  （`CI fast gate` / `CI integration gate` / `CodeQL gate`）
- 本 branch 上现在有**两个 Session 的工作**：Session 7（项目 Python 环境
  自动发现与无感切换，ADR 0018）与 **Session 7B（受控依赖修复，ADR 0019）**。
  7B 建在 7 上面，**没有改动 7 的任何行为**（见下方「Session 7 一个字节没改」）。
- 前一轮：PR 1（#127）Session 1–6 已合入 main（squash `6aeca9e`，2026-08-26），
  交接收尾 `9f48357`（#135）
- 受管产物 `canvas.html` 指纹 **`5ede3212c4d0db9e`**（在 `e025612` 上重建，
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

## 路径净化：两个出口，第二个是踩出来的

用户裁决 8 条 code-scanning 告警**走改码、不 dismiss**（`# nosec` /
`# codeql[...]` 这类抑制注释等于换个地方 dismiss，不算数）。8 条里 7 条是
**同一条污点流**：项目根 → 派生路径 → `open()` / 子进程 argv。所以收成两个
出口，不是打 7 个补丁：

| 函数 | 用于 | 判据 |
| --- | --- | --- |
| `projectenv.contained_path(root, cand)` | **目录** | 先 `realpath`（软链接 / `..` / `.` 全在这步落地），再按 `real_root + os.sep` 前缀判 |
| `projectenv.contained_file(root, cand)` | **文件** | 判**父目录**，回拼好的路径，**绝不 realpath 文件本身** |

两条容易写错的地方，各配了一条负向反证：

* **前缀判必须带 `os.sep`**。裸 `startswith(real_root)` 会把 `/a/paper-evil`
  判成「在 `/a/paper` 里面」。
* **顺序不能反**，也不能用 `normpath` 代替 `realpath`：`<项目>/.venv -> /etc`
  这种软链接在字符串上看着在项目内。

**为什么需要第二个函数**（这是踩出来的，不是设计出来的）：只有
`contained_path` 时，`venv/bin/python` 会被 realpath 解析成
`/opt/homebrew/.../python3.13`——**每一个 venv 都被判成「在项目外」**，
`test_project_env` 与 `test_dependency_repair` 当场全红。目录不是软链接，
判目录既挡得住逃逸又不误伤。**这个坑在 `project_relative` 的注释里已经记过
一次**，改 `interpreter_of` 时又踩了——所以这次收成函数，第三个调用点直接用。

顺带堵上一个**真缺陷**（不只是静态分析的抱怨）：`PATCH /api/engine/environment`
的 `scope="project"` 分支把相对路径拼到项目根上却没验逃逸，`../../../x`
拼完就出了项目，**而那条路径下游是要被当解释器 spawn 的**。绝对路径仍然
允许（ADR 0018 写明用户可以挑项目外的 conda 环境），堵住的只有「假装是
相对路径」那条。

> **写跨平台逃逸用例时**：硬写 `..\..\x` 在 POSIX 上根本不是逃逸（反斜杠
> 不是分隔符，那只是个怪文件名），断言会红在 `interpreter_not_found` 上而不是
> 逃逸判据上——用例在一半平台上量的是另一件事。用 `os.sep.join([...])`。

## ⚠️ PR 有冲突时 CI **根本不会跑**（不是「还没跑」）

`#177` 有过两次「推上去 46 分钟一个 run 都没有」。当时的判断是「push 事件
丢了，重推一次」——**重推没用，因为根因不是派发**：

```
gh pr view 177 --json mergeable   →  CONFLICTING
```

**PR 有合并冲突时 GitHub 算不出 merge commit，`pull_request` 触发的
workflow 就不派发。** 三点对照（同一条分支、同一段时间窗，唯一变的是
`mergeable`）：

| head | mergeable | runs |
| --- | --- | --- |
| `82d893d` | CONFLICTING | 0（等了 46 分钟） |
| `c4f6af0` | CONFLICTING | 0（重推换了 SHA，仍然 0） |
| `9b6007c` | **MERGEABLE** | **3（rebase 之后立刻派发）** |

**查它之前先查 `mergeable`**，它是 O(1) 的，而「计时 + 看别人有没有 run」
要 30 分钟且答不了这个问题：

```text
CONFLICTING                    → 根因是冲突，rebase 才会有 run
MERGEABLE 且别人有 run 它没有  → 被单独跳过
MERGEABLE 且大家都没有         → 平台滞后
```

看板上 `checks=UNREPORTED` 与「永远不会报告」长得一样，这类状态要单独一档
（`BLOCKED(conflicting)`）。

**这里还藏着一个更一般的教训**：当时的推理是「排除了平台整体故障 → 所以是
事件丢了」。**排除一个解释不等于证明另一个**——中间少了「还有别的可能吗」
那一步，而第三种可能用一个字段就能查出来。

## ⚠️ 脚本报「完成」不等于它做过事

`rebase_loop.sh` 有一轮报「rebase 已完成」，而 **HEAD 一动没动**：它只会
「继续」一个进行中的 rebase，没有 rebase 时 `git rebase --continue` 报
"no rebase in progress"，脚本把那当成了完成。

修法是**在结束时验证目标状态**，而不是信一句话：

```sh
git merge-base --is-ancestor origin/main HEAD || { echo "什么都没做"; exit 4; }
```

这与本轮别处踩的几次同源（`CodeQL` 与 `CodeQL gate` 当成同一个 check、
`CONFLICT` 行读 stderr、`--name-only` 分节、按分支 ref 查 code-scanning
alerts 永远是 0）：**输出看着像结论，其实回答的是另一个问题**。
每条判据先造一个已知答案的样本验一遍再用。

## ⚠️ linked worktree 共享主仓库的 `.git/config`

另一个会话在临时 worktree 里跑 `git config user.email q@l`，以为只作用于
那个 worktree。实际上 **`extensions.worktreeConfig` 没启用时，`git config`
不带 `--worktree` 一律写进共享的 `.git/config`**——于是本仓库（含所有
linked worktree）此后每一次提交的作者都变成了 `q <q@l>`，本分支有两个提交
中招。

排查与修复：

```sh
git config --show-origin user.email     # 看是哪一层盖住了全局身份
git log --format='%an <%ae>' origin/main..HEAD | sort -u   # 应当只有一行
# 改写时**显式带身份**，别依赖配置（那条配置可能还生效着）：
git -c user.name=… -c user.email=… \
    rebase --exec 'git commit --amend --reset-author --no-edit' <base>
```

`git -c` 设的参数经 `GIT_CONFIG_PARAMETERS` 传给子进程，所以 `--exec` 里的
`git commit` 拿到的是你指定的身份——这一点验过再用，别想当然。

后果不只是「名字不好看」：权利溯源审计要求 main 可达的提交出自同一权利人，
而 `q@l` 反解不出 GitHub 账号，CLA 判定器会判 `unresolved` 并阻断。

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

- [x] `git rebase origin/main`（到 `e025612`），rebase 后**本地重跑**整套：
      ruff 全绿 / pytest 2454 passed / web 1215 passed / CompatBench smoke 通过 /
      `build_mcp_widget.py --check` 一致。CI 上三个 required gate 也全绿。

### CI 红过两轮，两次都是「本地复现不出来」——记法比结论有用

两轮红的**都不是功能**，是环境漂移，而且两次本地都是绿的：

| 轮次 | 红在哪 | 根因 | 本地为什么看不见 |
|---|---|---|---|
| 1 | `test_project_venv_starts_the_worker_without_installing_tavotto` | 夹具 venv 用 `--system-site-packages`，继承的是**基础解释器**的 site-packages，而 CI 的 backend-fast 正是 `pip install -e ".[dev]"` 装进那里的 → 「项目 venv 里没有 Tavotto」这个**前提**当场失效 | 本地 pytest 跑在 `.venv` 里，**从 venv 建 venv 继承的是基础解释器、不是父 venv** |
| 2 | `test_i18n_dead_keys.py` | `EngineSource` 加了 `managed_project_env` 却没给 `engine.sourceLabel.*` 文案——界面上会显示成键名 | 那条反向门禁是 main 上 #158 **刚落地**的，我的 base 里没有；CI 测的是 merge ref |

两条都按同一条纪律收尾：**把「只有 CI 能发现」变成「本地就能发现」**。
第 1 条现在先断言遮蔽文件在（拆掉遮蔽本地当场红，已反证）；第 2 条靠
rebase 到最新 main 把门禁拿进来。

**第 3 轮（full-ci）红在 Windows 平台档**，同样是**测试断言**不是产品：

`backend-platforms (windows-latest)` 上
`test_managed_environment_end_to_end` 红。根因是 **Windows 的 8.3 短名**：
runner 的 `TEMP` 是 `C:\Users\RUNNER~1\...`，`config.data_dir()` 从它派生，
于是子进程报回来的 `sys.executable` 带短名，而断言另一侧的 `.resolve()`
把它展开成了长名（`runneradmin`）——一边展开一边不展开，`is_relative_to`
按路径段比就永远不等。

只有受管环境那条路会撞上：golden path 用的项目路径来自 pytest 的 `tmp_path`
（长名），两边一致。

修法是**归一父目录、不归一解释器本身**：`Path(executable).parent.resolve()`。
两条约束缺一不可——不 resolve 就展不开短名，resolve 解释器本身则会在 POSIX
上跟着 `venv/bin/python` 的软链接走到基础解释器（projectenv 里同一个坑）。
父目录（`Scripts/` / `bin/`）两个平台上都不是软链接，正好只展开该展开的那半。

**顺带查到一个既有面（本轮不动）**：`managedenv.project_fingerprint()` 走
`config.normalize_path_identity(os.path.abspath(...))`，而那个函数**只做
大小写、不展开 8.3 短名**。所以同一个项目用短名路径与长名路径打开，会拿到
两个不同的受管环境。**这不是本轮引入的**——`app._project_id()` /
`pool._norm_dir()` 共用同一份判据，同样如此；受管环境刻意与它们同源
（写在 `project_fingerprint` 的 docstring 里），单方面在这里改成 resolve
会让环境 key 与项目 id 不同源，那比现在糟。真要修是**改那份共用判据**，
独立一轮、连同 `_project_id` / `_norm_dir` 一起。

**第 4 轮红在 `windows-exe-smoke`，这次是真的产品缺陷**：

Playwright 用例找不到「换渲染环境」的引导。第一反应是「文案变了、改断言」，
**打印面板全文之后发现不是**——新的修复卡在「解析不出包名」那一档只剩
「指定安装包」，而文案还写着「或者换一个已经装好它的 Python 环境」，
那句话**指不出任何控件**。ADR 0019 §五 我自己写的是两个操作并列
（`[选择其他 Python]` / `[指定安装包…]`），实现漏了前一个。

补上了，而且**任何场景都保留**（解析不出 / 没有可用目标 / 轮次用完），
它是兼容层 Layer 4 的入口——最后一条路不能只在某些分支里有。
e2e 断言同时改成盯**控件的无障碍名**而不是散文（文案会改，无障碍名是契约
的一部分），并补了单测（抽掉 `<OtherPython />` 当场变红，已反证）。

### ⚠️ 本地 e2e 会假绿：`src/tavotto/web/` 是陈旧的构建残留

**这一条会反复咬人，务必先读。** 那条 e2e 我本地跑**一直是 `1 passed`**，
CI 却红。根因：

```python
WEB_DIST = PKG_ROOT / "web"          # src/tavotto/web —— 打包产物，被 gitignore
if not WEB_DIST.is_dir():
    WEB_DIST = ... / "web" / "dist"  # 只有前者不存在才回退到 pnpm build 的输出
```

`src/tavotto/web/` 一旦在工作区里存在（以前打过包就会留下），它**优先于**
`web/dist`。于是本地起的后端 serve 的是**那天打包时的前端**，当天所有
`web/src` 改动一个都看不见——`pnpm build` 跑多少次都没用。

**跑 e2e 之前先 `rm -rf src/tavotto/web`**（它被 gitignore，打包时会重新
生成）。删掉之后我本地逐字复现了 CI 的 locator 报错。

定位过程值得照抄，因为它没有一步是猜的：

1. 拦 HTTP 响应 → 后端**确实**发了 `dependency_repair`（排除后端）；
2. grep bundle → 解析代码**在**里面（排除「没 build」）；
3. 打印浏览器实际加载的资源名 → `index-D7n3D3ef.js`，而 `web/dist/assets`
   里**根本没有这个文件**（元凶现形）。

前两步都指向「应该是好的」，只有第三步问了「浏览器到底拿到了哪一份」。
**「各个环节看起来都对」时，去问那个没人验过的环节。**

**下一轮直接用的两条**：
- 夹具 venv 的「前提」要**造出来**，不能指望宿主碰巧没装（`--system-site-packages`
  会把基础解释器的东西带进来）。
- 本地全绿 ≠ CI 会绿：**main 上新落地的门禁不在你的 base 里**。开 PR 前先
  `git fetch && git rebase origin/main` 再跑一遍，比看 CI 红了再查便宜。
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

  **收敛顺序：rebase 在前，格式化在后，而且多半不需要后面那步。**

  ```sh
  git fetch origin && git rebase origin/main     # 就这个
  ruff format --check .                          # 它说红了才补一个格式化提交
  ```

  「先在自己分支上 `ruff format .` 再 rebase，两边形态相同、冲突自己消失」
  这条听着顺理成章，**实测是反的**（`tavotto-d2` 用 `git merge-tree
  --write-tree` 在两条真实在途分支上数冲突文件与 `<<<<<<<` 块）：

  | 分支 | 不格式化 | 预先格式化 |
  |---|---|---|
  | #161 | 3 文件 / 3 块 | **5 文件 / 7 块** |
  | #171 | 3 文件 / 4 块 | **5 文件 / 10 块** |

  机制：分支 base 停在旧 main，在它上面格式化得到的是「**旧内容**的新排版」，
  而 main 上是「**新内容**的新排版」——两边动同一批行，本来能干净带过去的
  文件反而变成冲突。**格式化把陈旧内容烤进了新形状。**

  对照组（内容已跟上 main 的分支）两种做法都是 **0 冲突**。所以决定冲突的
  是**内容陈不陈旧**，不是格没格式化；分支跟上 main，重排根本不构成冲突源。
  这条与本轮自己踩的那两个 CI 红是同一件事的两面：**先 rebase 再干活**。
- **CodeQL 的红不是 required check，但它照样挡合并——走的是另一道门**。
  `CodeQL` 报 1 critical + 7 high，逐条定性写在 PR #177 的
  issuecomment-5438197651。

  **这里我先写错过一版，值得留着**：原文说「所以那 8 条不是队列前置条件」。
  那个结论只查了「`CodeQL` 是不是 required check」（不是），漏掉了**每条
  告警会自动生成一条评审线程**，而 ruleset 21121430 里：

  ```text
  required_review_thread_resolution: true
  ```

  于是 8 条告警 = 8 条未解决线程 = `mergeStateStatus: BLOCKED`，**必须逐条
  处置才能入队**。判「某件事挡不挡合并」不能只数 required contexts——
  `pull_request` 规则里还有线程解决、approval 数、unattributed 改动几条
  独立的门，任一条不满足都是 BLOCKED，而 BLOCKED 只有一个值，看不出是谁挡的。

  查法（比从状态反推可靠）：

  ```sh
  gh api repos/Tavotto/Tavotto/rulesets/21121430 \
    --jq '.rules[]|select(.type=="pull_request")|.parameters'
  ```

  同一份参数里还有 `require_extra_approval_for_unattributed_changes: true`。
  #177 不触发（39 个提交全部归属到 `erwanjun`，`gh api pulls/177/commits
  --jq '.[].author.login'` 逐条验过）——但这条是本轮那次 git identity 污染
  （`q <q@l>`）真正的代价所在：**当时若没修，合并会被这条卡住**。

  **两个名字很像的检查别合成一个**（我第一轮就是这么看错的）：

  ```text
  CodeQL        GitHub 生成的告警汇总 check，报「新增几条告警」——不是 required
  CodeQL gate   required 的是这个。codeql.yml 里 needs: [analyze]，走
                aggregate_gate.py --mode codeql --required analyze，
                聚合的是四个 analyze job 的**结论**，不看告警条数
  ```

  必需上下文只有三个（ruleset 21121430）：`CI fast gate` / `CI integration
  gate` / `CodeQL gate`。现成的对照：#175 的 `CodeQL` 同样 fail（20 条新
  告警），`CodeQL gate` 照样 pass。**但这只证明「`CodeQL` 这个 check 不挡」，
  不证明「这些告警不挡」**——它们从线程那道门挡，见上。

  **我开 PR 时的预判是错的，值得记下来**：我以为红灯来自「本 PR 改写了
  `pool.py::EngineWorker.__init__` 里的一行，把那条 critical dismissal 的
  指纹打散了」。实情不是——那 8 条**全部是新代码第一次被扫到**
  （`projectenv.py` / `depresolve.py` 是本 PR 新增的文件，main 从没扫过），
  与 dismissal 打散无关。`EngineWorker.__init__` 那条**要等本 PR 合进 main
  之后**才会落空。

  处置：**一条都没 dismiss。** main 上那 15 条的先例摆着——维护者
  2026-08-26 逐条手写的理由，那不是 PR 作者该代劳的动作，更不该为了让门禁
  变绿而做。

  **后来确实为此改了代码，但改的是真防线，不是装饰**：`contained_path()` /
  `contained_file()` 那套路径遏制（realpath 后按前缀判）堵住了 `../` 逃逸，
  是真加固，`backend-platforms (windows-latest)` 已验。#119 的守卫（正则
  白名单 + `shell=False` + list argv）本来就是正确形状，没动。

  **然后撞上一件事，下个会话大概率会重蹈**：改完之后我估「再做一轮 inline
  净化能消掉 6 条」。逐条读了告警指的那一行，这个估计是 **0 条**——

  ```text
  projectenv.py:203   _within() 里的 path.resolve()
                      ← 这函数存在的唯一理由就是拒绝越界路径，
                        这次 resolve 就是净化过程本身
  depresolve.py:338   净化已经是 inline 的（往下 3 行同函数内就校验并回退）
                      ← 照报不误
  projectenv.py:140   cand 已经是 contained_file() 的输出，即已净化的值
  app.py:3404         报在净化前一行，下一行就是 contained_file()
  ```

  第一条说明**每加一层净化就多一个 sink**（任何范围判定都得先 resolve 两边
  再比较），第二条说明**inline 化已经试过、无效**。污点分析报的是**数据流
  经过的位置**，不是缺陷位置，而净化器天然坐在数据流上。

  **所以拿到这类告警，先读它指的那一行，再估「能修几条」**——逐条读几分钟，
  估错要赔一轮无用重构，且代码更差（真正的检查被淹在装饰性净化里）。
  剩下的按「产品语义」处置：`app.py:3404` 接受用户指定的解释器路径是 ADR
  0018 明确允许的（项目外的 conda 环境），要消除只能改产品行为。

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

# 全部走 venv 的绝对路径，别用裸命令——本机 PATH 里没有 ruff（直接报错），
# 而 pytest **有**：`/opt/homebrew/bin/pytest`，那是另一个解释器的。
# 前者立刻失败反而安全，后者会跑起来并给出主语错误的结果。
/Volumes/Projects/Tavotto/.venv/bin/ruff check .
/Volumes/Projects/Tavotto/.venv/bin/ruff format --check .   # #176 起也是门禁
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_project_env.py tests/test_dependency_repair.py
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_dependency_repair_e2e.py        # 真装包，约 1–2 分钟
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
