# U00 · 统一基线 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U00.baseline（单一 milestone；一个 PR）。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `319a506d`（`origin/main`，工作区干净）；
结束 = 本 PR 的 head（合并后以 `git log origin/main` 里 PR 号为准）；用户主工作区
`/Volumes/Projects/Tavotto` 一个字节没碰（全部在 worktree `tavotto-wt/foundation-u00` 里做）。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 后端、safe worker、旧依赖解析）；
候选路径**无**；本阶段拟启用能力**无**（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`.github/AGENTS.md`、
`packaging/AGENTS.md`；`docs/rules/backend/` 的 pdf-backend-boundary / export-pipeline /
execution-entries / dependency-repair-and-packages / project-system / writeback-transaction /
worker-protocol-and-lifecycle / figure-capture-and-execution / process-boundaries /
profiles-and-preflight / runtime-figure-assets；`docs/rules/repo/same-origin-pairs.md`、
`predicate-subject.md`；`docs/rules/ci/verification-chain.md`；ADR 0045；`docs/legal/LICENSING.md` 与
`COMMERCIALIZATION_DEPENDENCY_AUDIT.md`；ci-foundation 的 `CI_HANDOFF.md` / `06_UNIFIED_HANDOFF.md`。
复用（只读，不改）：`pdfbackend/__init__.py` 的 `__all__`、`engine/execspec.py`、`pool.py`、
`projectenv.py`、`workdir.py`、`envlease.py`、`deprepair.py`、`depresolve.py`、`managedenv.py`、
`pool.find_worker_python()`（夹具测试借它找科学解释器）。

**实际代码与 API / 数据结构变更**：**产品源码零变更**（`src/`、`scripts/`、`web/src`、`.github/`、
`packaging/`、`pyproject.toml` 都没动）。新增：

* `docs/implementation/tavotto-foundation/`（实施包整包入库；`tools/*.py` 只过了 ruff 的 import
  排序与格式化，AST 除 import 顺序外逐节点相同；`generated/` 重生成后逐字节相同）；
  `README.md` 加 ci-foundation 前置段；`plan.json` U00 `implementation_status: done`；
  `PACKAGE_CONTENTS.json` 按入库后的字节重算（五个字节有变的文件的如收 sha256 记在
  `U00_BASELINE.md` §6.1；`sources_manifest.json` 与 `archive/` 一字未动，校验仍过）。
* `U00_BASELINE.md`、`U00_FACADE_LEDGER.json`（真值）+ `.md`（派生，`tools/generate_facade_ledger.py`）、
  `U00_CAPABILITY_INVENTORY.md`、`evidence/u00/`、本文件。
* `tests/fixtures/foundation/`（六组合成夹具 + README）；`tests/test_foundation_fixtures.py`、
  `tests/test_foundation_facade_ledger.py`、`tests/test_foundation_plan_integrity.py`。

**关联旧要求 ID / 场景 ID**：FO-001（调用图）→ `U00_FACADE_LEDGER` + `U00_CAPABILITY_INVENTORY`；
FO-002（首开旧问题 baseline）→ `U00_BASELINE.md` §4（#435 未复现 / FO19 已复现 / #240）；
FO-003（原生参考与数据身份）→ `tests/test_foundation_fixtures.py` 的原生参考隔离用例 + 六组 `truth.json`；
FO-004（现有测试覆盖 ↔ 新 gap）→ 清单里每个导出项的 tests 分「用户合同 / 实现特定」+ §1.6 的假设差异表；
R00 / RC-001…006 的清点部分（迁移判据已写，验证归 U08）；CP08-A 的基线命令 / 时长（§3）。
所有条目 `execution_status` 仍 `not_run`。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，origin/main 干净 worktree | 0 / 0 | 全绿（`U00_BASELINE.md` §3） |
| `PYTHONPATH=$WT/src pytest`（全量，默认档） | 同上，worker = Homebrew 3.13.11 + mpl 3.10.8 | **1** | 1 failed / 4942 passed / 51 skipped，21 分 00 秒，峰值 575 MB；红的是 #240（pre-existing），单跑绿（退出码 0）；`evidence/u00/pytest_full_summary.txt` |
| `cd web && pnpm install && pnpm test && pnpm build` | 同上 | 0 / 0 / 0 | 4142 vitest 用例；构建主 chunk 1.9 MB（#246 已知）；`evidence/u00/frontend_workerd_summary.txt` |
| `cd workerd && cargo test / clippy -D warnings / fmt --check` | 同上 | 0 / 0 / 0 | 53 tests |
| `PYTHONPATH=$WT/src python scripts/smoke_app.py --python .venv/bin/python` | 同上；web/dist 与 workerd debug 二进制现建 | 0 | 冒烟通过：source=system、控制面 workerd、首渲染 12.8 s 冷 / 0.04 s 热、导出 + 覆盖导出、干净退出；`evidence/u00/smoke_steps.txt` |
| `PYTHONPATH=$WT/src pytest tests/test_foundation_fixtures.py tests/test_foundation_facade_ledger.py tests/test_foundation_plan_integrity.py` | 本 PR 的 worktree | 0 | 见 PR 正文（含变异反证） |
| `python tools/validate_plan.py .` / `python -m unittest discover -s tools -p 'test_*.py'` | 入库后的 `docs/implementation/tavotto-foundation` | 0 / 0 | `ok: true` / 17 tests OK——**任务书结构自检，不是产品测试** |
| 内置 runtime 构建、e2e（Playwright）、lab 腿、Windows / Linux 任何测量 | — | — | **not_run**（本机没构建 runtime；e2e 未跑；只有 macOS） |

**本切片的正例、负例、旧行为回归**：正例 = 夹具真值用例（CSV / 依赖声明 / PDF / PNG 逐字段对真值）、
原生参考在临时副本里跑通并留产物、`make_venv.py` 在 3.11 上建出与应用 3.13 不同 minor 的 venv；
负例 = 同名数据在 `decoy/` 下算出 [601, 1201, 2401]（分得开）、`--data-via cwd` 在 `scripts/` 下
FileNotFoundError、`make_venv.py` 同 minor 时退 2、清单漏一项 / 多一项 / 行漂移各自红（变异反证在
PR 正文）；旧行为回归 = 产品源码没动，全量套件与 origin/main 同形（同一条 #240 红）。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**没有**。
`registry.json` 220 条 `enrollment` / `execution_status` 一条没动（validate_plan 的
「registry aggregate execution status contradicts entries」也要求全 `not_run` 时聚合仍 `not_run`）。
新增的三个测试文件是**切片自检**（夹具真值 / 清单不腐烂 / 任务书结构），随代码进既有 backend job，
不是 FO / RC 场景的 enrollment。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：内置 runtime 构建与其 4 条真 import 用例；Playwright e2e；lab / Windows / Linux
  任何腿；`-m slow` 档；registry 的全部 220 条产品实例。
* 基础设施问题：无（本机工具链齐全；pnpm / cargo 缓存是热的，冷成本以 `CI_HANDOFF.md` §7/§9 为准）。
* 真正产品失败（都不是本阶段修的）：
  1. #240 `tavotto run` Ctrl+C 在负载下 90 s 不退出（全量红、单跑绿，形状与 issue 一致）；
  2. **FO19 在 safe worker 上不成立**：用户项目里的 `manifest.py` 被引擎的 `engine/manifest.py`
     遮住（`U00_BASELINE.md` §4.4 实测）——待主对话定处置（建议开 issue，归 U03 或独立小切片）；
  3. #435（Windows + conda configured + project cwd + 中文路径首渲染崩溃）——本机不可复现，记为 U03 的负例来源。
  4. 依赖声明解析丢 marker / extras / 冲突静默取首条（§4.3 实测）——是 `depresolve` 第一版写明的边界，
     不是回归；但它是 U04 DependencyIntent 的起点事实。

**批准的字体 / 视觉差异，及未授权变更检查**：无字体 / 视觉改动；`git diff --stat origin/main` 只含
`docs/implementation/tavotto-foundation/**`、`tests/fixtures/foundation/**`、`tests/test_foundation_*.py`。
用户文件、`LICENSE`、branch protection、`aggregate_gate.py`、默认后端、生产依赖一个都没动。

**当前可合并依据（不等于可以默认启用 / 发行）**：纯 docs + tests + fixtures（低风险档）；
ruff 两条 0；针对性 pytest 0；变异反证逐条红过（PR 正文）；实施包自检 0；`test_docs_references` /
`test_agents_rules_index` 通过（新文档的相对链接都解析得开）。

**仍缺哪些默认启用 / 精确安装物资格**：全部。本阶段不产生任何产品资格。

**下一个无阻塞阶段 / 子切片**：**U01**（共同合同与增量测试骨架），输入：
* `U00_CAPABILITY_INVENTORY.md` 每节末尾的「差距」行——LaunchContext 的 cwd 三分 / grant / 写入模式，
  DependencyIntent 的 extras / marker / constraints / group 四维，ExecutionReceipt 的 worker 自报
  `sys.prefix`（加字段不升协议版）；
* `U00_FACADE_LEDGER.json` 的 `test_classes` 分类——U01 挑独立读取栈（不是 PyMuPDF）时，先用
  `tests/fixtures/foundation/pdf_png_assets/` 的真值验读取栈自己；
* `U00_BASELINE.md` §1.6 的 matplotlib 三档（3.10.8 / 3.11.2 / 3.11.1）要进实例身份；
* 新增 `src/tavotto` 模块前读 `tests/support/importgraph.py`（架构守卫会红）；
* ADR 编号从 **0053** 起（派工时预分配，别「最大号 + 1」）。
U02 的两个 spike 与 U03 可并行起步（02_ROADMAP）。

**回退方式、不能假装可回滚的外部副作用**：revert 本 PR 即回退（纯文档 / 测试 / 夹具，没有产物、没有
发布、没有设置写入）。夹具的 `make_venv.py` 只在调用方指定的目录建 `.venv`（默认本目录，已 gitignore），
测试用 `tmp_path`；原生参考只写临时副本，夹具目录快照前后逐字节相同——没有外部副作用。
