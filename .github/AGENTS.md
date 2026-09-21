# .github/ — CI、发布与验证链规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`；引擎与后端在 `src/tavotto/AGENTS.md`，前端在
`web/AGENTS.md`。本层规则的**全文按主题**在 `docs/rules/ci/`：先在下表按改动路径找到
主题，读那一份细则（各 1–6 KB）与它点名的合同用例，再动手。规则改在细则文件里，
并同步这里那一行；这里不放第二份全文。

## 本层不可破坏

- **CI 按发生时机分工**：PR = 快速反馈；merge_group = 完整合并资格的唯一常规执行点；
  `full-ci` 标签 = 在 PR 自己的 SHA 上提前跑全套；push main = 落地审计 + 缓存种子
  （非门禁）。覆盖面一条不减，改的只是时机。required checks 只有三个稳定 Gate，判定
  收敛在 `scripts/ci/aggregate_gate.py`；merge_group 与 full-ci 永远不许 deferred。
- **四条 workflow 顶层 `TAVOTTO_NO_TELEMETRY=1`**：CI 绝不产生真实的产品事件。
- **新增的核心不变式测试提交前必须手工反证一次**（拿掉修复确认它红），结论写进 PR；
  判「最近跑过没有」要数**有结论的 run**——空转的门禁比没有门禁更坏。
- **「在 Gate 的闭集里」≠「在 PR 上会跑」**：把 fast 档 job 改成重型条件，Gate 依旧绿而
  它守的东西合并前一次都不验；`test_every_fast_lane_job_actually_runs_on_a_plain_pull_request` 看住。
- **job id 不变、显示名可变**：矩阵化 / 分片只改显示名，`needs:` / `--required` / required
  contexts 读的都是 id，仓库设置不用重登记。
- **缓存是枚举、产物不跨 job 抽取**：`actions/cache` 只有 CPython 归档，pnpm store 按锁文件，
  rust-cache 一条腿一把 `shared-key`；venv / site-packages / 测试结果 / Playwright 浏览器一律不缓存。
- **runner 信任区**：监听 PR / merge_group 事件的 workflow 只许 hosted runner；`tavotto-lab`
  只在 `_lab-qualification.yml`；未部署的池不进 `.github/actionlint.yaml`。
- **发布链切两段**：`release.yml` 造产物、派 lab、不等结果；`release-publish.yml` 只有
  `workflow_dispatch`，`trust2` 不信任载荷、按同一规则重算 publish。
- **pwsh 步骤最后一条原生命令故意非零退出时，脚本必须显式 `exit 0` 结尾**。
- 每个「只在别人电脑上发生」的 bug 先变成 `tests/test_windows_regressions.py` 的用例再谈修。

## 按改动路径找细则

| 改到 | 主题（细则在 `docs/rules/ci/`） | 必守要点 | 看护 |
| --- | --- | --- | --- |
| `.github/workflows/ci.yml` 的事件 / `needs` / Gate 闭集、`scripts/ci/aggregate_gate.py` | CI 分层 → `ci-lanes.md` | 时机分工与覆盖面不变；三个稳定 Gate；`cancel-in-progress` 只对 PR 开；顶层 `TAVOTTO_NO_TELEMETRY=1` | `tests/test_merge_queue_workflows.py`、`tests/test_aggregate_gate.py` |
| 任何新门禁 / 豁免表 / 「最近跑过没有」的判据 | 门禁纪律 → `gate-discipline.md` | 手工反证一次并写进 PR；豁免要写得出理由、区分「豁免」与「使能」；数有结论的 run；别人电脑上的 bug 先写用例 | `tests/test_windows_regressions.py` |
| `python-lint` job、`pyproject.toml` 的 `[tool.ruff]` | Ruff 这一格 → `ruff-lint-lane.md` | 规则与豁免只在 `pyproject.toml`，CI 不 `--fix`；check 与 format 两个独立结论；新增 sys.path 源码根要回来审 `src` | `tests/test_merge_queue_workflows.py` |
| `backend-fast` / `backend-platforms` 的 `--shard`、`_lab-qualification.yml` 的常规套件、`tests/support/shard.py`、`shard_weights.json` | pytest 分片（CI03a）→ `pytest-shards.md` | 选项必须 `=` 形式；进程内自验并集 == 全集；不带 `--shard` 是 no-op；lab 常规套件 = 同机 N 片并行、片号是变量、每片退出码进控制流；权重表只影响平衡不影响覆盖；不加 `-n auto` | `tests/test_merge_queue_workflows.py::TestGates`、`tests/test_pytest_shard.py` |
| `windows-exe-smoke` 的 matrix `include`、`web/playwright.config.ts` 的 project 集 | Playwright 分片（CI03c）→ `playwright-shards.md` | 两片各自完整构建；必需步骤不加 `if:`；`others` == 其余片之并；artifact 名带 shard；step 级 `timeout-minutes` | `tests/test_merge_queue_workflows.py::TestPlaywrightShards`、`tests/test_playwright_shard_check.py` |
| 任何装本项目 / 打 wheel / PyInstaller 的 job 的「批准字体」步、`package` 与两条 app smoke 腿的「退役扫描」步、`scripts/ci/retirement_scan.py` | PDF 后端边界（后端侧细则）→ `../docs/rules/backend/pdf-backend-boundary.md` | U10（ADR 0072）：RenderCore 是默认后端、字体不进 git——每条装项目的腿先 `fetch_fonts.py`（闭集在合同用例里；不上 actions/cache），wheel / 冻结产物出来之后扫原生库名单 + METADATA + 字体 13 张；没有一条 `pip install` 再点名 pymupdf | `tests/test_merge_queue_workflows.py::TestApprovedFontsAndRetirementScan`、`tests/test_retirement_scan.py` |
| `package` job 的冒烟步骤、`scripts/ci/package_smoke.py` | package 冒烟隔离（CI03b）→ `package-smoke-isolation.md` | venv / workdir 在 `runner.temp`；端口向系统租、就绪 = 版本端点 + 本实例凭据、终止 = 进程不存在；`/tmp/smoke` + `sleep 8` + curl 不许回来 | `tests/test_merge_queue_workflows.py::TestPackageSmokeIsolation` |
| 任何 `actions/cache` / setup-node / rust-cache、`cache-seed` job、artifact 传递 | 构建复用与缓存（CI02）→ `build-reuse-and-caches.md` | 缓存是枚举（多一条就红）；唯一数据边 `frontend → plugin-candidate` 按 content_digest 核；rust-cache 一条腿一把 `shared-key`；`cache-seed` 不是门禁、`full-ci` PR 上是首验 | `tests/test_merge_queue_workflows.py::TestBuildReuseAndCaches` / `::TestCacheSeed` / `::TestLandingAudit` |
| `runs-on`、workflow 事件、`.github/actionlint.yaml`、可复用 workflow | runner 信任区（CI04）→ `runner-trust-zones.md` | PR / merge_group 只许 hosted；`tavotto-lab` 只在 `_lab-qualification.yml`；事件闭集；标签集合 == 实际用到的 | `tests/test_merge_queue_workflows.py::TestRunnerTrustZones` |
| 任何 `shell: pwsh` 步骤 | pwsh 退出码 → `pwsh-exit-codes.md` | 最后一条原生命令故意非零时显式 `exit 0`；失败路径全是 `throw` | `tests/test_merge_queue_workflows.py` |
| `tests/compat/`、`tests/test_equivalence_matrix.py`、`tests/test_invariants_engine.py`、`scripts/smoke_app.py`、`nightly.yml`、`scripts/bench_render.py` | 验证链（按层）→ `verification-chain.md` | CompatBench 与 acceptance 问的不是同一个问题、corpus 不许合并；未声明的失败一律 `product_bug`、Tier 1 不许有；四路等价性与五条不变式三者不能互相替代；冒烟必须 `--expect-source bundled` / `--expect-control-plane workerd`；改性能前先在 `docs/perf-baseline.md` 指出一个数字 | `scripts/ci/compat_matrix.py`、`tests/test_equivalence_matrix.py`、`tests/test_invariants_engine.py` |
| `release.yml` / `release-publish.yml`、`codex-plugin.json`、`latest.json`、遥测代理部署 | 发布链 → `release-chain.md` | 第一段不等 lab；`trust2` 不信任载荷、publish 只能压成 false、读一次 `lab/release` status；插件清单由 `build` 生成、不进 `desktop-tauri.yml`；先发代理再发客户端 | `tests/test_release_workflow_contract.py`、`tests/test_update_chain_gates.py` |
| `desktop-shell` job、`src-tauri` 的 cargo 三件、fast 档 job 的 `if:` | desktop-shell 与 Gate 闭集 → `desktop-shell-lane.md` | 两条腿（ubuntu + macos）都跑 fmt / clippy / test；`bundle.resources` 空目录就够；改 fast 档条件先看闭集用例 | `tests/test_merge_queue_workflows.py::TestGates` |
| `frontend` / `plugin-candidate` job、`scripts/plugin_stage.py`、`plugin-stable.yml` | Codex 插件候选（ADR 0043）→ `codex-plugin-candidate.md` | 候选只验证不发布；有产物时 `tests/test_plugin_candidate.py` 不许 skip；`plugin_stable` 投影同一份 zip；发行分支不触发源码 CI | `tests/test_plugin_candidate.py`、`docs/ci/plugin-stable-channel.md` |
