# Compatibility Bridge Session Handoff

> 每个 Session 结束前必须更新本文件。

## 当前状态

- 日期：2026-08-27
- 当前 branch：`compat/bridge-session07-project-env`（worktree
  `.claude/worktrees/compat-bridge-session01`，基于 `origin/main` 的
  `93cca88`，**不再 stacked**——PR 1 的那条链已经合并完了）
- 本 Session Prompt：项目 Python 环境自动发现与无感切换
  （**替代**原计划的 Native Execution / `tavotto run` Session）
- 目标 PR：Session 7 单独一条 PR
- 前一轮：PR 1（#127）Session 1–6 已合入 main（squash `6aeca9e`，
  2026-08-26），交接收尾 `9f48357`（#135）

## 为什么这一轮不是 `tavotto run`

用户在 PR 1 合并后拿一批旧项目做了复测（那是 Session 7 的入场券）。结论
比预期干净得多：**绝大多数脚本现在都能正常发现与打开，剩下的失败几乎只有
一类——内置渲染环境缺第三方依赖。**

真实样本：用户的 `2d 处理` 项目，8 个脚本全部 `import ovito`（另有一个
`import seaborn`，那个内置就有、本来就能跑）。报错是

```text
ModuleNotFoundError: No module named 'ovito'
```

而项目自己就带着能跑通的环境。当时 Tavotto 给用户的唯一出路是「去设置里
手填一条解释器路径」——对科研用户门槛过高。

于是本轮做的是「**把项目自己的 `.venv` 找出来用上**」，而不是「按项目原本
的方式运行」。后者复杂得多、放弃沙盒保证，而数据表明它解决的不是当前的
主要矛盾。`tavotto run` / ADR 0014 继续延期，决策门见下方。

## 本轮唯一目标

内置 runtime 因 `missing_dependency` 失败时，自动发现并整体换用项目本地
虚拟环境（`.venv` / `venv` / `env`），对普通用户尽量无感，但保留清晰、
可诊断、可关闭的环境状态。**不实现 `tavotto run`；不自动 pip install；
不修改用户环境；不混装 site-packages。**

## 已完成

- [x] **`engine/projectenv.py`**（新，纯标准库——被 `pool` import，而 pool
  被 Flask import）：发现（只认带 `pyvenv.cfg` 的真 venv；范围锁在项目根内，
  不上溯、不顺软链接跳出去；优先级 = 离脚本最近 → `.venv`/`venv`/`env` →
  路径字典序）、体检（在候选解释器里真跑一次：版本、matplotlib、缺的那个
  模块、**能不能起 Tavotto worker**）、项目级决策的记/忘/缓存/重试上限。
- [x] **`pool.resolve_worker_python(figures_dir)`**：项目级解释器决策的
  唯一出处。优先级 = 环境变量 > 设置里指定的 > **这个项目记住的** >
  内置/自身/系统。两条显式来源**各自判**，不 `env or configured` 短路。
- [x] **`pool.build()`**：get + ensure_built + **一次**自动 fallback，
  「跑一次用户脚本」的统一入口（`probe.probe()` 已改用它）。
- [x] **`pool.should_try_project_env(exc)`**：「什么错该换环境」的唯一判据
  （只有 `missing_dependency`）。`pool.build` 与 app 端点重试共用它。
- [x] **`pool.get()` 身份守卫加「渲染解释器已变」**，与既有的「入口已变」
  同形，不另起一套 key。
- [x] **端点侧重试**（`app._engine_attempt`）：render / preview_png /
  png / svg 这几条**惰性 build** 的路也覆盖到——只在 probe 那条路上做
  fallback 的话，「素材库能打开、直接点面板打不开」就成立了。
- [x] **产品 API**：`GET /api/engine/environment` 多一段 `project`
  （来源、项目相对解释器路径、是否自动、因为缺哪个包、发现到的候选）；
  `PATCH /api/engine/environment` 支持 `scope="project"`（存下来之前先真
  体检；清空 = 回默认链条）。
- [x] **前端**：环境卡显示「项目环境：.venv/bin/python」+ 一键改回内置；
  `MissingDependencyCard` 重做成恢复引导（先给一键用项目 `.venv`，再给
  手填路径；四种「没接手成」各有各的文案）；自动接手成功时一条轻量 toast
  （`environment_switched` 只在真的切了的那一次响应里出现）。中英各一份。
- [x] **诊断**：`project.environment_resolution`（来源/自动与否/触发原因/
  缺的模块/Python 与 matplotlib 版本/支持等级/**项目相对**解释器路径）。
  版本这些事实在切换当时就存下来了，**生成诊断包时不重新体检**。
- [x] **CompatBench**：报告的 `target.actual` 多一个 `interpreter_source`，
  内置与项目 venv 跑出来的数字不再被当成同一件事。
- [x] **支持口径**：`projectenv` 的 Python/matplotlib 区间是
  `docs/support-matrix.json` 与 pyproject 的运行时镜像，
  `test_support_matrix.py::test_project_env_mirrors_the_matrix` 逐条对拍。
- [x] **ADR 0018** 与 `docs/compatibility/legacy-projects.md`（兼容层分层）。

## 顺手修掉的两个既有缺陷

- **`TAVOTTO_WORKER_PYTHON` 漏给整个 pytest 进程**（`d2b93f4`）：
  `CM._worker_python()` 直接写 `os.environ`（它是 CI 驱动，不是被测函数），
  而那条用例让它在写完之后才抛错。`monkeypatch.delenv(name, raising=False)`
  **在变量本来就没设的时候什么都不记账**，于是逃得掉自动还原。
  现在 `tests/conftest.py` 有一条 autouse fixture 兜住整类问题。
  这条漏出来的环境变量正是 Session 7 的显式优先级用例「单跑绿、全量红」
  的成因。
- **显式来源短路**：`worker_python_env() or config.worker_python()` 让
  一条指向不存在路径的环境变量把设置里那条完全遮住。上面那条泄漏把它撞了
  出来，现在两条各自判，并有专门的用例。

## 负向反证（本轮十一条，全部先红后还原）

| # | 抽掉什么 | 哪条用例变红 |
|---|---|---|
| 1 | 项目 venv 发现 | `test_missing_dependency_falls_back_to_the_project_venv` |
| 2 | 给 worker 注 venv 的 `PYTHONPATH`（混装） | `test_never_mixes_site_packages` |
| 3 | worker 身份里的解释器 | `test_worker_identity_includes_the_interpreter` |
| 4 | 干净重放跟着项目走 | `test_replay_and_export_use_the_same_interpreter` |
| 5 | 显式选择优先 | `test_explicit_configuration_wins_over_automatic_discovery` |
| 6 | 显式来源各自判（改回短路） | `test_a_stale_env_var_does_not_hide_an_explicit_setting` |
| 7 | 项目作用域（改写全局设置） | `test_the_switch_is_remembered_project_scoped_not_globally` |
| 8 | 重试上限 | `test_fallback_is_attempted_at_most_once` |
| 9 | 只认 `missing_dependency` | `test_only_missing_dependency_triggers_a_switch` |
| 10 | 模块名校验 | `test_module_name_must_be_a_bare_identifier` |
| 11 | 端点侧共用同一判据 | `test_the_app_endpoint_retry_shares_the_same_predicate` |

**第一轮跑出两条空门禁，都是真问题**：

- #3 抽掉不红——自动 fallback 那条路顺手 `invalidate()` 过，所以「切过去」
  那一半根本用不到 `get()` 里的比对。补上反向的一半（用户切回内置走
  `forget()`，它不作废任何 worker）才真正看护到那条守卫。
- #9/#11 抽掉不红——判据在 `pool.build` 与 `app` 各写了一份，用例只走
  pool 那条路。收进 `pool.should_try_project_env()` 之后两处共用一份。

变异脚本不用 `git checkout` 还原（工作区里有未提交改动时它会一起吃掉，
这一轮就吃掉过一次），改成写回内存里的原文。

## 真 venv 测试怎么做到不联网

从当前 worker 解释器 `python -m venv --system-site-packages` 建一个：
matplotlib 直接用宿主那份。要「这个环境有而别处没有」的包时，往它自己的
site-packages 写一个纯 Python 的 fixture 模块（`tavotto_probe_fixture`）。

断言必须证明**跑的是那个解释器**（`sys.executable` / `sys.prefix` 在 venv
里），不是「字符串选中了 `/tmp/.venv/bin/python`」。

## 实际运行的测试

```sh
ruff check .                                              # 全绿
PYTHONPATH=src .venv/bin/python -m pytest -q               # 全绿
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_project_env.py   # 29 passed
cd web && pnpm build && pnpm test && pnpm lint && pnpm i18n:check
PYTHONPATH=src .venv/bin/python scripts/ci/compat_matrix.py --smoke      # 通过
```

## 已知失败与限制

| 问题 | 严重度 | 后续 |
|---|---|---|
| 只认 `.venv`/`venv`/`env`；Poetry / Conda / pyenv / pixi 用户走「选择其他 Python」 | 中（如实记账，不是无声失败） | 等真实用户数据 |
| 记住的解释器在 reopen 时只做轻量复检（`import matplotlib`），不重跑完整体检 | 低 | 坏了会在起 worker 时报错，走正常恢复引导 |
| 项目 venv 的 matplotlib 版本可能与视觉基线不同 | 低（标注为 `unverified_but_compatible`） | 不修，如实标注 |
| 自动接手仍是同步阻塞（体检最长 60s×候选数，命中第一个就停） | 低 | SSE 进度流条目沿用 |
| 真机（WebView2/WKWebView 壳内）尚未走过这条路 | 中 | 见「待办」 |

## 不得被下一 Session 破坏的约束

- Session 2–6 的全部约束仍然有效（runtime id 不透明、打开绝不执行、cache
  是派生物、writeback 拒绝在后端、lazy 门、取消端到端、`_PROBES` 并发闸、
  `GET /api/runtime/assets` 零执行、scriptRunStore 代际纪律、不渲染假
  native 入口、素材库两区是普通路径唯一入口、`tavotto open script.py` 的
  执行次数纪律、多 Figure 绝不静默选第一张、`desktop_argv()` ↔
  `parse_open_args()` 同源、CompatBench 产品路由不得旁路、基线只在 target
  bundled 的钉版环境上重生成）。
- **绝不混装 site-packages**：切换的单位永远是完整解释器。给任何 worker 注
  一条指向 venv site-packages 的 `PYTHONPATH` 都是本条的破坏。
- **绝不 pip install**：内置 runtime 与用户 venv 一个字节都不改。
- **绝不写全局 `worker.python`** 来表达项目级决策。
- **用户显式选择 > 自动猜测**，且两条显式来源各自判、不短路。
- **一次 build 最多自动切一次**；只有 `missing_dependency` 触发，判据只有
  `pool.should_try_project_env()` 一份。
- **一个 worker 生命周期一个解释器**：热态 / 干净重放 / 导出 / 写回自检
  必须同源。
- **merge main 之后必须重跑 e2e**（asset-library / golden-paths 至少各一
  遍）：windows-exe-smoke 的 Playwright 套件只在 merge_group 跑，PR CI 绿
  不代表它绿。大改动入队前先打 `full-ci` 标签在 PR SHA 上取证。

## 待办（本 PR 之外）

- [ ] **真机验证这条路**：Windows/macOS 壳内用一个带 `.venv` 且缺包的真实
  项目走一遍（用户的 `2d 处理` 就是现成样本）。CI 侧覆盖不了 WebView2 /
  WKWebView 壳内交互。
- [ ] **网站 playground re-sync**（PR 1 起就挂着）：合并后 main 上
  `python scripts/build_browser_playground.py` → 网站仓库
  `pnpm sync-playground`。
- [ ] 受管产物重建（本 PR 改了 `web/src`）：`build_mcp_widget.py` 与
  `build_browser_playground.py`，各自 `--check`。

## 下一 Session：先出数据，再决定做什么

**不要自动进入 `tavotto run`。** 本轮合并后先回答两个问题：

1. 之前失败的旧项目里，有多少因为项目 `.venv` 自动接手而成功了？
2. 剩余失败按原因分类各占多少？

分类口径：`environment_missing` / `unsupported_python` / `cwd_semantics` /
`argv_semantics` / `env_var_semantics` / `module_invocation` /
`package_layout` / `custom_launcher` / `artist_support` /
`actual_script_bug`。

**只有**当数据表明剩余失败仍大量集中在 `cwd` / `argv` / shell env /
`python -m` / 自定义启动语义上时，才恢复 `tavotto run`（ADR 0014）。
绝大多数项目能正常打开的话，它继续延期——native 执行放弃的是沙盒保证，
那个代价不该为了长尾去付。

本轮的决策输出：**DEFER_TAVOTTO_RUN**（依据：真实用户复测的失败集中在缺
依赖这一类，而本轮正是针对它的；`tavotto run` 的必要性尚无数据支持）。

## 下一 Session 首先阅读

```text
AGENTS.md / CLAUDE.md
docs/compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md
docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md（本文件）
docs/compatibility/legacy-projects.md（兼容层分层）
docs/adr/0018-project-python-environment-resolution.md（本轮）
docs/adr/0014-safe-native-execution-profiles.md（仍是 Proposed）
src/tavotto/engine/projectenv.py
src/tavotto/engine/pool.py 的 resolve_worker_python / build / try_project_env
```

## 建议启动命令

```bash
git status --short && git log -8 --oneline
ruff check .
PYTHONPATH=src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest -q \
    tests/test_project_env.py tests/test_open_script_route.py
/Volumes/Projects/Tavotto/.venv/bin/python scripts/ci/compat_matrix.py --smoke
```
