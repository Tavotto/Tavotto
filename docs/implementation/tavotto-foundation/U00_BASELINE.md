# U00 · 统一基线：差异清点、基线测量、范围草案

阶段任务书：[`phases/U00_baseline.md`](phases/U00_baseline.md)。前置轨道交接：
[`../ci-foundation/CI_HANDOFF.md`](../ci-foundation/CI_HANDOFF.md)（`ci_hosted_ready: pass`、
`runner_pool_ready: not_run`，本阶段**不重做 CI 调查**，只沿用 §14 列的六条产品问题起点）。

**性质**：只测不拍阈值，只清点不改产品。本文里每个数字都来自真实执行；没跑的写 `not_run`，
本来就红的如实归因。所有未来能力仍 `not_run`（`registry.json` 220 条一条没动）。

## 0. HEAD / 工作区

| 项 | 值 |
|---|---|
| 分支 base（本阶段所有清点与测量的采样点） | `319a506dff5a003d85f02ad7ac55504ec3a9d1f9`（`origin/main` @ 2026-09-20 派工时） |
| 派工后 `origin/main` 又前进到 | `62eb7f7a`（#434：lab 常规套件 4 片并行；只动 lab-ci.yml / conftest / 规则文档，不碰产品源码） |
| 工作区 | `/Volumes/Projects/tavotto-wt/foundation-u00`，分支 `foundation/u00-baseline`，起步 `git status` 干净 |
| 旧审计 SHA（实施包） | `6a1a9dea`（2026-09-15，#358） |
| 最新采样 SHA（实施包） | `8b95256c`（2026-09-15，#359）——与旧审计**相邻**（一个提交之差） |
| 到 base 的距离 | 65 个提交（6a1a → 319a），901 个文件 +114 554 / −13 224 |

## 1. 真实差异清点（6a1a9dea / 8b95256c → 319a506d）

65 个提交逐个读过 `git log`，相关文件逐个看过 diff（`git diff 6a1a9dea..HEAD -- <路径>`）。
两份 SHA 只差 #359（设置页打磨，纯前端），下面按主题以 6a1a9dea 为起点。

### 1.1 `pdfbackend`（RC 主线的替换对象）

**零变化**：`src/tavotto/pdfbackend/__init__.py`、`pymupdf_backend.py`、`canvas_coverage.json`、
`src/tavotto/glyphplan.py`、`src/tavotto/richtext.py`、`src/tavotto/tiffwrite.py` 的 diff 为空。
PyMuPDF 仍是 1.28.2（`.venv`），facade 仍是 19 个导出项（清单见
[`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md)）。

### 1.2 `execspec` / `pool` / `projectenv`（首开主线）

**引擎执行描述零变化**：`engine/execspec.py`、`workdir.py`、`pool.py`、`projectenv.py`、
`envlease.py`、`enginesession.py`、`nativesession.py`、`nativerelay.py`、`runspec.py`、
`runcli.py`、`runtime.py`、`workerd_client.py`、`figcapture.py`、`figsession.py`、`wireproto.py`、
`worker.py` 的 diff 全部为空。有变化的只有 native bridge 的装载清单：

| 文件 | 变化 | 与本轨道的关系 |
|---|---|---|
| `engine/bridge_runner.py` (+5) | `_PHASE2` 加 `axestraversal / spinemodel / tickmodel / colorbarmodel / legendmodel` | 引擎族模块拆分（下）连带；native 侧要还给用户的顶层名字多了五个 |
| `engine/bridgeboot.py` (+23) | `ENGINE_SIBLINGS` 同步加五个；注释改成「引擎里不再有 late import」（`overrides._sibling()` 已删） | FO19（用户 `manifest.py` 重名）在 native 侧的处理前提变了：现在靠模块层平铺 import 一次装完，不再有函数体内的延后 import |
| `src/tavotto/localserver.py`（新，73 行）+ `app.py` main / `desktop.py` | `LocalWSGIServer` 取代 `app.run` / `make_server`：bind 与 listen 之间不再反查主机名（#380） | **CI_HANDOFF §14 第 1 条（getfqdn 停顿）已在产品侧修掉**，不再是 U00 的待查项；`test_localserver.py` 看护 |

### 1.3 `deprepair` / `depresolve` / `managedenv` / `workdir`

**零变化**（四个模块 diff 为空）。但本阶段用合成夹具**实测**了 `depresolve` 的现状
（`tests/fixtures/foundation/dependency_declarations/`，命令与输出见 §4.3）：

* `requirements-marker-false.txt`（`tabulate==0.9.0; sys_platform == "never_os"`）→ 解析成
  `{'tabulate': '==0.9.0', 'six': '==1.17.0'}`——**环境标记被整段丢掉**，标记为假的声明会被当成
  「项目声明了」；
* `requirements-extra.txt`（`tabulate[widechars]==0.9.0`）→ `{'tabulate': '==0.9.0'}`——**extra
  被剥掉**，`wcwidth` 不会进联合安装；
* `requirements-conflict.txt`（`six==1.16.0` / `six==1.17.0`）→ `{'six': '==1.16.0'}`——**第一条
  静默胜出**，冲突不报；
* `constraints.txt` 不在 `REQUIREMENTS_GLOBS` 里，**根本不读**；
* `parse_requirement("tabulate[widechars]==0.9.0")` 与带 `;` 的都回 `None`（第一版刻意窄，docstring
  写明）；`curated_distribution("labtools_local")` / 不存在的名字 → `None`（不猜，FO-034 已满足）。

这些都是 `depresolve.py` docstring 里写明的第一版边界，不是 bug；但它们与 registry
FO-029（保留 marker / extras / constraints）、FO-014 / FO-033（冲突不静默放宽）的假设
**相反**——U04 的 DependencyIntent 要从这里起步，不是从「已经保留」起步。

### 1.4 UI / 协议（`app.py`、HTTP / MCP 端点、前端 api 类型）

| 文件 | 变化 | 备注 |
|---|---|---|
| `src/tavotto/app.py` (160 行) | 写回基线 `_baked_*` / `_sha1_of` 提取到 `engine/bakedbaseline.py`（#401）；main 改用 `localserver`（#380）；教程重置改用 `_baked_store().path_for` | **端点集合零变化**：`/api/engine/*`、`/api/export*`、`/api/runtime/*`、`/api/native/*`、`/api/registry/*` 的 route 装饰器一个没加没删 |
| `src/tavotto/engine/exportjob.py` (+32/−) | 终局字段（`finished_at` / `phase` / `error_*`）先于终局 `status` 写，三条终局路径一致（#389，issue #381） | 04 §2 ArtifactManifest「plan/observed/policy 分开」要接的现状；U01 合同要把「读者看到的快照」这条顺序写进去 |
| `src/tavotto/engine/{axestraversal,spinemodel,tickmodel,colorbarmodel,legendmodel}.py`（新） + `manifest.py` / `overrides.py` 大改（#400/#406/#407/#408） | 引擎 artist 族模块拆分，`fig.axes` 遍历权威只剩 `axestraversal.ordered_axes` | 科学 worker 内部；对 RenderCore / 首开合同无接口影响，但 `packaging/tavotto.spec` 的 datas 与 bridge 装载清单都多了五个文件（1.2 / 1.6） |
| `engine/bakedbaseline.py`（新，194 行） | 写回基线存储 + 三条纯判据（`baseline_matches_file` 等），进 `pyproject.toml` 的 mutmut `only_mutate` | 写回事务的「基线绑定文件身份」现在有独立模块；U01 SourceArtifact 的「文件归属 / 身份」可复用它的 sha1 / mtime_ns / size 三元 |
| `engine/overrides.py`（#412 / #414 / #423 / #427） | 文字 bbox 显隐只归 `bbox_visible`；图例项脱开 = 脚本原样 + 文档里的 handle_*；Patch 边色 / 面色 getter 回模式哨兵；颜色字段 alpha 0 报 `NO_COLOR` | 科学编辑语义；首开 / RenderCore 不消费这些 prop，但 `NO_COLOR` 是新的严格同源对（`tests/test_no_color_pair.py`） |
| `web/src/lib/api.ts`、`web/src/types/*` | **零变化** | 前端 api 类型没动；web/src 的 251 个文件变化全是打磨 / 图标 / 状态提取（#357–#371、#399、#403、#410、#416） |
| `codex-plugin/mcp/tavotto_mcp/*` | **零变化**（插件目录只改了 README / plugin.json 版本号 / 一处文档措辞） | MCP 入口到 `pool.get()` 的路径不变（见 [`U00_CAPABILITY_INVENTORY.md`](U00_CAPABILITY_INVENTORY.md) §2） |
| `src/tavotto/__init__.py` | 0.14.0 → **0.15.0**（v0.15.0 已于 2026-09-18 发出，#419） | 版本号；`docs/support-matrix.json` 零变化 |
| `docs/adr/` | 新增 0052（自绘图标）；0034（图例绑定）与 0016 修订 | 没有与 PDF 后端 / 环境 / 执行相关的新 ADR；ADR 编号下一个可用 **0053**（#302 之后按 `test_docs_references` 唯一性判） |

### 1.5 CI / packaging

| 文件 | 变化 | 备注 |
|---|---|---|
| `.github/workflows/ci.yml` (+607) 等 | CI00–CI05 前置轨道（#372–#383）：verdict-only 边删除、pytest / Playwright 分片、package 冒烟隔离、缓存种子、`full-ci` 语义 | 权威记录是 `CI_HANDOFF.md`，本阶段不复述 |
| `release.yml` / `release-publish.yml` / `lab-ci.yml` / `_lab-qualification.yml` | 发布链切两段、lab runner 迁到私有仓库 `Tavotto/ci-infra`（#384/#386/#388/#392） | **可用 runner**：公开仓库零 self-hosted runner；lab 只剩 ci-infra 的 `tavotto-lab-01`（CI_HANDOFF §8 + §13 ⑦）。U11 的干净机器资格要在 hosted 或 lab 上取得，没有可销毁 PR 池 |
| `packaging/tavotto.spec` | datas 加五个族模块 | 冻结产物的 datas 清单由 `tests/test_runtime_build.py` 从 import 闭包反推校验 |
| `packaging/runtime-lock.json` | **零变化**：CPython 3.13.15；matplotlib 3.11.1 / numpy 2.5.2 / pandas 3.0.5 / scipy 1.18.0 / seaborn 0.13.2 / pillow 12.3.0；`macos-x86_64` 仍 `shipped: false` | §5.3 |
| `pyproject.toml` (+3) | mutmut `only_mutate` 加 `bakedbaseline.py` | 依赖、extras、Python 上下界零变化 |
| `scripts/ci/` | 新增 `ci_baseline.py`、`package_smoke.py`、`playwright_shard_check.py`（CI 前置轨道） | `ci_baseline.py` 是 CI_HANDOFF §14 让 U00 沿用的耗时采集工具（本阶段量的是本机，没用它；U01 量 CI 用它） |
| `tests/` | 59 个文件 +11 536：分片 / CI 合同 / 架构守卫（`test_import_architecture.py` + `import_architecture_baseline.json`）/ 操作序列 harness（`test_override_sequences.py`）/ 族模块用例 | 新增测试若引入 `src/tavotto` 内部新 import 边，`test_import_architecture.py` 会红——U01 加模块时先读 `tests/support/importgraph.py` |

### 1.6 registry 条目里的假设与当前代码不符的地方

| 条目 | 假设 | 当前代码（实测 / 读码） |
|---|---|---|
| FO-029 / FO-014 / FO-033 | marker / extras / constraints 保留；冲突不静默放宽 | §1.3：全部丢弃或静默取第一条（`depresolve.parse_requirements_text`） |
| FO19（场景）/ FO-031 | 用户项目里的 `manifest.py` / `overrides.py` 重名时「用户 import 命中用户模块」 | **safe worker 上不成立**（§4.4 实测）：用户脚本 `import manifest` 拿到的是 Tavotto 引擎的 `engine/manifest.py`。native bridge 侧由 `bridgeboot` 还原用户顶层名（已有用例）。这是一条真实的基线缺陷：**issue #447**（severity:P2 / area:engine），归 U03 |
| FO-036 | 受管 venv 「在最终路径构建，active 指针原子发布」 | `managedenv` 建在 `<data_dir>/environments/<项目指纹>/venv`，状态是 `environment.json` 里的 `ready / incomplete` 标记，**没有 active 指针**这个概念——是 U04 的新目标，不是现状 |
| FO-041 / FO-042 | `script.parent` / `project.root` / `invocation.cwd` 三者准确区分；旧 project 工作目录语义不变 | `execspec.cwd_mode` 只有 `sandbox` / `project` 两档，且 **`project` 档的 cwd 是 `script.parent`**（ADR 0047、`worker.py:160`），不是 project root（04 §2 已写明「旧 project 值仍是 script.parent」）。三分需 U01 在 LaunchContext 里新增字段 |
| FO-047 | 真实 cwd 写入许可在执行前明确授予 | `PATCH /api/engine/workdir` 后端不记「谁授权过」，确认只在前端文案（`docs/rules/backend/figure-capture-and-execution.md`）；grant 是 U01 PreparationPlan 的新字段 |
| FO-054 | `ready_editable` | `engine/readiness.py` 的状态叫 `editable`（六态闭集），没有 `ready_editable`；命名对齐即可 |
| RC-001 must_fail_example | 漏 `original_tiff` / `missing_glyphs` 不能被总通过掩盖 | 现状：`original_tiff` 没有以它为主语的直接单元用例（全经端点），清单已标 |
| CI_HANDOFF §14 ①（getfqdn 停顿） | U00 需重新调查 | 已由 #380 在产品侧修掉（§1.2），不再待查 |
| CI_HANDOFF §14 ②（backend-fast 的 matplotlib 未钉版本） | 意图还是漂移未查明 | 仍未钉：`ci.yml` 的 backend-fast 装的是 `tavotto[worker]` 解析到的最新 matplotlib（3.10 腿 3.10.9、3.13 / 3.14 腿 3.11.2），`support-matrix.json` 只钉 Python。本机开发 worker 是 3.10.8，发行 runtime 是 3.11.1——**三档不同**（§5.3）。U01 的 harness 要把「哪一档 matplotlib」写进实例身份，不能只写 Python |

## 2. 调用图与能力清单

* facade 迁移清单（19 个导出项 → 调用方 → 类别 → 测试分类 → 迁移判据）：
  [`U00_FACADE_LEDGER.json`](U00_FACADE_LEDGER.json)（真值）/ [`U00_FACADE_LEDGER.md`](U00_FACADE_LEDGER.md)（派生），
  门禁 `tests/test_foundation_facade_ledger.py`。
* 首开主线（execspec / projectenv / deprepair / workdir / envlease / managedenv）的入口与能力清单，
  含 HTTP / MCP / CLI / 桌面四类入口各自怎么到达 `ExecutionSpec`：
  [`U00_CAPABILITY_INVENTORY.md`](U00_CAPABILITY_INVENTORY.md)。

## 3. 基线测量（只测不拍阈值）

环境：macOS 27.0（Darwin 27.0.0）arm64，12 核，24 GiB；应用 Python `.venv` 3.13.11（Flask 3.1.3 +
pymupdf 1.28.2，**没有 matplotlib**）；渲染解释器由 `pool.find_worker_python()` 选中
`/opt/homebrew/opt/python@3.13/libexec/bin/python3`（3.13.11，matplotlib **3.10.8**，numpy 2.4.3）；
pnpm 11.0.7 / node 26.7.0；cargo 1.95.0。**全部在 `origin/main` (319a506d) 的干净 detached worktree
上跑**（`git worktree add --detach`），`PYTHONPATH` 指向那棵树（`tavotto.__file__` 已核）；
`/usr/bin/time -l` 量墙钟与峰值 RSS。原始日志在本机 scratchpad（不进 git），摘要在
[`evidence/u00/`](evidence/u00/)。

| # | 命令（在干净 worktree 里） | 退出码 | 墙钟 | 峰值 RSS | 结果 |
|---|---|---|---|---|---|
| 1 | `.venv/bin/python -m ruff check .` | 0 | 0.09 s | 59 MB | All checks passed |
| 2 | `.venv/bin/python -m ruff format --check .` | 0 | 0.1 s | 59 MB | 404 files already formatted |
| 3 | `PYTHONPATH=$WT/src .venv/bin/python -m pytest -p no:cacheprovider -rA --durations=30 --junitxml=…` | **1** | **1260 s（21 分 00 秒）** | 575 MB | **1 failed / 4942 passed / 51 skipped / 2 deselected**（默认档 `-m "not slow"`） |
| 3′ | 同一 worktree 单跑那条红：`pytest tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan` | 0 | ~2 s | — | 绿（见 §3.1） |
| 4 | `cd web && pnpm install --frozen-lockfile` | 0 | 1.5 s（store 已热） | 406 MB | — |
| 5 | `pnpm test`（vitest） | 0 | 31 s | 340 MB | 279 files / **4142 passed** |
| 6 | `pnpm build`（含 tsc -b + tailwind-scan-check） | 0 | 8.5 s | 1157 MB | 主 chunk `index-*.js` 1 899.81 kB / gzip 595.69 kB（已知 #246） |
| 7 | `cd workerd && cargo test` | 0 | 12.9 s | 260 MB | 53 passed（24 + 26 + 3）；顺带产出 `workerd/target/debug/tavotto-workerd` |
| 8 | `cargo clippy --all-targets -- -D warnings` | 0 | 1.5 s | 238 MB | — |
| 9 | `cargo fmt --check` | 0 | <1 s | — | — |
| 10 | `PYTHONPATH=$WT/src .venv/bin/python scripts/smoke_app.py --python .venv/bin/python` | **0** | 16 s | 225 MB | 冒烟通过：认证 deny-by-default ✓、渲染环境 source=**system**（3.13.11 / mpl 3.10.8）、3 面板、首渲染 **12.8 s（冷启动，`worker_get_ms` 12 569）**、热渲染 0.04 s、控制面 **workerd**（自动发现了 #7 刚建的 debug 二进制）、导出 + 覆盖导出、诊断 7 项 0 未通过、干净退出无孤儿 |
| 11 | `docs/implementation/tavotto-foundation`：`python tools/validate_plan.py .` / `python -m unittest discover -s tools -p 'test_*.py'` | 0 / 0 | <1 s | — | 只验任务书结构（`ok: true`、17 tests OK）——**不是产品测试** |

下载成本：本机 pnpm store、cargo registry 都是热的，pnpm install 1.5 s 不代表冷成本；CI 上的
冷 / 暖数字沿用 `CI_HANDOFF.md` §7 / §9（合并组候选恒冷的作用域问题已由 #383 处理）。
内置 runtime 构建（`scripts/build_worker_runtime.py`）本阶段 **not_run**（本机没构建过 runtime，
`tests/test_runtime_build.py` 的 4 条真 import 用例 skip）。

### 3.1 本来就红的检查（归因）

只有一条：`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`
——`os.killpg(SIGINT)` 之后 `proc.communicate(timeout=90)` 超时（stdout 已有 `READY`，CLI 停在
「Waiting for Tavotto desktop…」）。**pre-existing**：issue **#240**（P1，area:ci-harness，2026-09-19
开）记载它自 PR #189 起跨 18 个 Session 在全量里间歇红、窄范围绿；本次形状完全一致——全量红
（90.43 s，最慢的一条），同一 worktree 单跑绿（退出码 0）。本次全量期间本机另有本阶段的小型
pytest / venv 构建在跑，正是 #240 描述的「机器同时有别的重活时复现」条件。**不改 aggregate_gate、
不加豁免**；它不属于本轨道，处置归 #240。

51 条 skip 全部是「产物没建 / 本机形态」类（workerd 二进制 12 条——pytest 由 conftest 钉 `TAVOTTO_WORKERD=0`，
且这次 pytest 跑在 cargo test 之前；MCP 画布 4 条；wheel 产物 3 条；runtime 4 条；插件候选 6 条；本机真装着
Tavotto 桌面版 10 条；联网冒烟 3 条；NSIS 中间脚本 2 条；族模块无 RESTORE 表 2 条；更新链探针二进制 1 条；
`web/node_modules` 1 条；画布真构建 1 条；非 Linux 1 条；序列 harness 空参数集 1 条），逐条见
`evidence/u00/pytest_full_summary.txt`。**skip 不是绿**：workerd / MCP 画布 / 产物那几组在
CI 的 merge_group 上有真实执行位置（`docs/ci/pr-review-tiers.md`、#409），本机没建就不算验过。

### 3.1.1 本分支的全量（提交前，含新用例）

`PYTHONPATH=$WT/src pytest`（本分支 worktree，rebase 到 `62eb7f7a` 之后）：**2 failed / 4972 passed /
51 skipped**，22 分 00 秒。两条红：① 同一条 #240（pre-existing）；② `tests/test_source_hygiene.py::
test_windows_bound_subprocesses_pin_their_decoding`——**抓的是本阶段新加的文件**（`make_venv.py` 与
`test_foundation_fixtures.py` 里 6 处 `subprocess.run(text=True)` 没给 `encoding`），已按仓库纪律补
`encoding="utf-8", errors="replace"` 并单跑该门禁绿；不是基线红。新增 30 条用例全绿。

### 3.2 已知与本机测量相关的不确定性

* 全量 pytest 的墙钟受同时运行的小任务影响（见 3.1），21 分钟是**上界样本**，不是可复现基线；
  CI 上的分片基线以 `CI_HANDOFF.md` §9 为准。
* 渲染解释器是本机 Homebrew Python（source=system），不是发行 runtime；首渲染 12.8 s 含 matplotlib
  冷 import + 字体缓存，与用户桌面版无可比性（`docs/perf-baseline.md` 的口径）。

## 4. 首开旧问题的真实 baseline（FO-002）

| 问题 | 来源 | 本阶段结论 |
|---|---|---|
| Windows 桌面版 + 用户 conda 环境（configured，Python 3.13 / mpl 3.11.0）+ `workdir.mode=project` + 中文路径 `E:\嘿嘿\figure` + cp936：首次渲染「渲染进程崩溃（无响应），会话需要重建」 | issue **#435**（2026-09-20 开，用户诊断包） | **未复现**（需要 Windows + 该 conda 环境；本机 macOS）。诊断包里 `render.worker_error` 为空、`recent_errors` 是四条 traceback，根因未定。它是 U03（已有环境首开）最真实的一条负例，U03 的 Windows 实例集合要能覆盖「configured 源 + project cwd + 非 ASCII 路径 + workerd」这一组合 |
| 用户项目里与引擎同名的 `manifest.py` | registry FO19 / FO-031 | **复现**（§4.4）：safe worker 上用户的 `import manifest` 命中引擎模块。native 侧已处理。已开 **#447**，归 U03 |
| `tavotto run` Ctrl+C 在负载下不退出 | #240 | pre-existing，见 §3.1 |
| CI_HANDOFF §14 ①–⑥ | ci-foundation | ① 已修（#380）；② 未钉（§1.6）；③ Playwright 偶发红——本阶段没跑 e2e，`not_run`；④ Windows 特慢用例——本阶段 macOS 全量里 `InvMix` 三条 29 / 19 / 17 s，是同族最慢，Windows 数字沿用 CI00 §6；⑤ 产物依赖用例的执行位置——#409 已给 package / workerd 两组位置，runtime 4 条仍无；⑥ `WindowsPath` 参数 id——未动 |

### 4.3 依赖声明的实测（§1.3 的证据）

```text
$ PYTHONPATH=src .venv/bin/python -c 'from tavotto.engine import depresolve as d; ...'
requirements.txt              -> {'six': '==1.17.0', 'tabulate': '==0.9.0', 'sortedcontainers': '==2.4.0'}
requirements-marker-false.txt -> {'tabulate': '==0.9.0', 'six': '==1.17.0'}      # marker 丢了
requirements-extra.txt        -> {'tabulate': '==0.9.0'}                          # extra 丢了
requirements-conflict.txt     -> {'six': '==1.16.0'}                              # 第一条静默胜出
constraints.txt               -> {'tabulate': '<0.9'}                             # 但它不在 REQUIREMENTS_GLOBS 里，产品不读
parse_requirement('tabulate[widechars]==0.9.0')            -> None
parse_requirement('six==1.17.0; python_version < "3.0"')   -> None
curated_distribution('labtools_local') -> None ; ('zzz_not_a_real_distribution_u00') -> None
```

### 4.4 FO19 的实测（safe worker，本机）

脚本目录放 `manifest.py`（`WHO = "user-manifest"`）与 `lab_utils.py`，脚本 `import manifest; import lab_utils`
并把 `manifest.WHO`（或引擎模块的 `__file__`）打进 stderr；经 `pool.get(...).ensure_built()`
（Python 池，`TAVOTTO_WORKERD=0`）：

```text
FO19 manifest -> ENGINE:/…/src/tavotto/engine/manifest.py     # 用户的 manifest.py 没被 import 到
FO19 lab_utils slope -> 2.0                                    # 不重名的本地模块正常
```

成因在 `engine/worker.py:48`（引擎目录 `sys.path.insert(0, HERE)` 后模块层 `import manifest` 等）：
用户脚本目录虽在 build 时插到 `sys.path[0]`（`worker.py:164-166`），但 `manifest` 已在 `sys.modules`
里，用户的同名模块永远轮不到。这不是本阶段要修的（U00 不改产品源码）；记录为 `product_failure`，
不挂 required。**issue #447**（归 U03）。

## 5. 范围草案（供 U01 合同 / U02 spike / U06 定稿；U00 不改规则）

### 5.1 字体：最小批准范围

现状（读码 + ADR 0045）：

* **画布文字**（PyMuPDF 侧）：族闭集 `("serif", "sans-serif", "monospace")` × 常规 / 粗 / 斜 / 粗斜，
  全部映射到 PyMuPDF **内建 base-14**（Times / Helvetica / Courier 四款各）；中日韩只有一张脸
  `china-ss`（实测四个别名同为 Droid Sans Fallback）；隐式回退层由 PyMuPDF 自己挑（实测 Noto Serif）。
  四层归属在 `glyphplan.py`，覆盖表 `canvas_coverage.json`（primary 层 0x20–0x7E、Latin-1 补充、
  部分希腊 / 西里尔 / 标点 / 数学符号；上界 0x30000）。**本仓库不分发任何字体**（`test_font_provenance.py`）。
* **图内文字**（matplotlib 侧）：与后端替换无关——DejaVu Sans + 按平台探测的中日韩尾巴
  （ADR 0045），PDF/PS 一律 Type 42。

草案（U06 要用真实候选核许可后定稿，D07 批准一次度量迁移）：

| 项 | 最小批准范围 | 明确不在范围 |
|---|---|---|
| 拉丁 / 希腊 / 基本符号 | 三个通用族 × 四态各**一套**可再分发的开源字体（候选：Liberation / DejaVu / Noto 家族，许可 SIL OFL 或 GPL+FE，U06 逐个核 `docs/legal/` 的 SBOM 要求与 #182 的第三方声明） | 用户磁盘字体的内嵌（另一件事，facade 注释原话） |
| 中日韩 | **一张**回退脸（候选 Noto Sans CJK SC 子集或 Droid Sans Fallback），保持「换族不换 CJK」的现行语义 | 简繁日韩偏好分脸；衬线 CJK |
| 覆盖承诺 | `canvas_coverage.json` 由新字体集合重生成；`tests/test_scientific_text_matrix.py` 的科学文本矩阵三族四态**缺字为空**是硬门 | 「支持所有字体」 |
| 度量 | 位置 / 内容 / 框尺寸保留，换行变化逐例审查（03 §6）；旧 `test_compose_text` 的 base-14 asc/desc 反算期望按实现特定断言迁移 | 与 base-14 逐像素相同 |

### 5.2 应用 Python 支持范围

`docs/support-matrix.json`（唯一权威，零变化）：`requires >=3.10,<3.15`，tested 3.10–3.14；
桌面内置 runtime 3.13.15；本机 `.venv` 3.13.11。CI backend-fast 三腿 3.10 / 3.13 / 3.14 + 平台腿 3.13。
草案：U01 合同里「应用 Python」= 这个范围原样；「项目 Python」（用户环境）另一根轴，下限由
`projectenv.support_status()` 判（现状：按 Python 版本 + matplotlib 版本两维），U03 不放宽。

### 5.3 scientific runtime 范围

`packaging/runtime-lock.json`（schema 2，零变化）：三个目标闭包逐字相同——CPython 3.13.15；
`top_level` = numpy / matplotlib / pandas / scipy / seaborn / pillow；精确版本 matplotlib 3.11.1、
numpy 2.5.2、pandas 3.0.5、scipy 1.18.0、seaborn 0.13.2、pillow 12.3.0（+ 传递闭包 8 个）；
`windows-amd64` / `macos-arm64` shipped，`macos-x86_64` **`shipped: false`**（锁着版本但没构建没冒烟，
CI 无 Intel runner）。

草案：U05 的私有 Python 来源**复用**这份锁（不造第二套安装器，D05）；U04 的联合依赖以「合格已有
base」起步（本机就有三档：3.11 / 3.13 / 3.14 Homebrew）。matplotlib 的三档并存（开发 3.10.8 /
CI 最新 3.11.2 / 发行 3.11.1）要进实例身份（§1.6）。

### 5.4 可用 runner（引 CI_HANDOFF §8 / §13）

* hosted：ubuntu / macos / windows-latest；free 计划并发 20（macOS 5），一个 full-ci run 29 个 job
  自己就超。
* self-hosted：公开仓库 **0 台**；lab `tavotto-lab-01` 只在私有仓库 `Tavotto/ci-infra`
  （`_lab-qualification.yml` 经 `lab-ci.yml` / `release.yml` 派发）。
* 可销毁 PR 池：**不存在**（`runner_pool_ready: not_run`，用户已拍板本轮不建）。
* 含义：U11 的「无系统 Python 干净目标」只能在 hosted 的临时 VM 或 lab 上做；U03/U04 的 PR 档实例
  必须能在 hosted 上跑完（03 §5 的预算起点 6–8 个代表性场景）。

### 5.5 真实安装目标

`support-matrix.json` 的四档：Windows x64 桌面（NSIS，supported）、macOS Apple Silicon 桌面
（dmg 签名公证，supported）、Linux（pip / pipx 浏览器模式，beta）、macOS Intel / Windows ARM
（unsupported）。草案：U11 的精确发行资格只对前三档；Intel 不得写「支持」。

### 5.6 与仓库不变量的冲突（U00 不改规则）

* 根 `AGENTS.md` 不变量「`pdfbackend/pymupdf_backend.py` 是全仓库唯一 import pymupdf 的模块」
  与 1.0 收敛纪律（禁扩能力 / 禁重写稳定模块）——本轨道替换 PyMuPDF 属于 **release blocker**
  （`docs/legal/COMMERCIALIZATION_DEPENDENCY_AUDIT.md`：PyMuPDF 是唯一 BLOCKER；`LICENSING.md`
  Layer 3）。这条不变量在替换期间**继续成立**（新核心不借旧库，D03），到 U10 切默认时由
  **U02 / U06 的 ADR 正式修订**为「发行闭包零 pymupdf」+ 退役扫描；U00 一个字不改。
* `LICENSE`（AGPL-3.0-only）不变；#182（发行物要带第三方声明）是新字体 / 新栅格器进来时的
  前置义务，U06 落地字体时必须同时满足。

## 6. 本阶段交付物索引

| 文件 | 内容 |
|---|---|
| `README.md`（本目录） | 加了 ci-foundation 前置状态段（06 第 1 条） |
| `U00_BASELINE.md` | 本文 |
| `U00_FACADE_LEDGER.json` / `.md` | facade 迁移清单（真值 / 派生） |
| `U00_CAPABILITY_INVENTORY.md` | 首开主线入口与能力清单 |
| `evidence/u00/` | 基线测量摘要 |
| `handoffs/U00_baseline.md` | 交接（07 模板） |
| `tools/generate_facade_ledger.py` | 清单派生器 |
| `../../../tests/fixtures/foundation/` | 六组合成夹具 + README |
| `../../../tests/test_foundation_fixtures.py` | 夹具真值 + 原生参考隔离 |
| `../../../tests/test_foundation_facade_ledger.py` | 清单门禁（`__all__` 逐项、file:line 不腐烂、派生一致） |
| `../../../tests/test_foundation_plan_integrity.py` | 实施包结构自检（**不是产品测试**） |

### 6.1 实施包如收记录（来源 `/Users/jiaqi/Downloads/Tavotto_Unified_Implementation_Pack/`，2026-09-20 复制）

原包 `PACKAGE_CONTENTS.json` 列 45 个文件；入库后它按仓库里的字节重算（54 个）。与如收版本**字节不同**的
只有下面五个，其余 40 个（含 `archive/` 全部、`registry.json`、`sources_manifest.json`、`phase_mapping.json`、
`generated/`、七份编号文档、`phases/`、`extensions/`）逐字节相同：

| 文件 | 如收字节数 | 如收 sha256 | 改动理由 |
|---|---|---|---|
| `README.md` | 4061 | `3069c5e01fb3f169a66f4908afb2ff4060d6cad9e3d29b417b7d26188b880f0d` | 加 ci-foundation 前置段 + U00 已执行段 + 落点句改成事实 |
| `plan.json` | 7408 | `f7836bb8fc7f831734864250fc50c84c8856e12fd3a433d932414e0e2749eca4` | U00 `implementation_status: not_started → done` |
| `tools/generate_views.py` | 4060 | `584e08d78e5464fca25b851552ab339eb288f28c0b078732c524dd8ef0430912` | ruff：import 排序 + 格式化（非 import 语句的 AST 逐节点相同） |
| `tools/test_plan_validation.py` | 3306 | `2b51393ec27bfe5e6ff13056663f6997cd124469481d01929ac81e757746621e` | 同上 |
| `tools/validate_plan.py` | 13023 | `fd20b2f618cc501610c65cda883611e888cb32018645652a4489e2cb8275ca47` | ruff format（AST 完全相同） |
