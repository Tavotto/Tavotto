# .github/ — CI、发布与验证链规则

仓库级路由与不变量在根 `AGENTS.md`。

## CI 分层（1.0 稳定化，2026-08-21 起）

CI 按**发生时机**分工（`.github/workflows/ci.yml` 抬头有全图，2026-08-25
Merge Queue 定版）：PR = 快速反馈（python-lint / invariants / backend-fast /
frontend / workerd / **desktop-shell** / compat-smoke / CodeQL）；merge_group = 完整合并资格的唯一常规执行
点（backend-platforms / package ×3 / 两个真产物冒烟，Merge Queue 对「最新
main + 前序 PR + 当前 PR」的组合提交验证）；`full-ci` 标签 = 在 PR 自己的
SHA 上提前跑全套；push main = 轻量落地审计（main-landing-audit，不重复打
包）**+ 缓存种子（cache-seed，非门禁，2026-09-16 起）**——它不产生任何结论、
不在任何 Gate 的闭集里，只是在 main 上把 pnpm store / CPython 归档 / rust-cache
各种一份；为什么 push main 要多这一个 job，见下面「门禁纪律」的缓存那一段；
nightly / lab / release 照旧。**覆盖面一条没减，改的是时机**。ruleset
的 required checks 只有三个稳定 Gate（CI fast gate / CI integration gate /
CodeQL gate），判定收敛在 `scripts/ci/aggregate_gate.py`——普通 PR 上
integration gate 显式 deferred，merge_group 与 full-ci 永远不许 deferred。
迁移顺序与 Ruleset 工具见 `docs/ci/merge-queue-rollout.md`；受管生成物
（canvas.html 等）的冲突域治理与 stack / train 协作见
`docs/ci/parallel-prs.md` + `.github/conflict-domains.json`。ci.yml 与
codeql.yml 的 `cancel-in-progress` **只对 PR 开**：merge_group 候选与 main
的唯一验证记录都不许被取消，tag / release 链路不在分组里。

四条 workflow 的顶层 env 钉 `TAVOTTO_NO_TELEMETRY=1`——**CI 绝不产生真实的
产品事件**（细节见 `src/tavotto/AGENTS.md` 的遥测一节）。

## 门禁纪律

- **反空门禁纪律**：新增的核心不变式测试，提交前必须手工反证一次——把修复
  拿掉，确认它真的红，并把结论写进 PR。「读的人会以为它在挡什么，而它什么
  都没挡」比没有更坏。豁免表要写得出理由，并区分「豁免」（本来就不画）与
  「使能」（画在一个关着的通道上，开了就必须变）。反证是**事后**那一半；
  事前那一半（写判据之前先说出主语：谁的、哪个进程、哪个时刻、哪个维度）
  的唯一出处是根 `AGENTS.md` 的「判据的主语」一节。
- **空转的门禁比没有门禁更坏**：nightly 曾对早已退役删除的 `packaging/tavotto.iss`
  报平安；macOS 冒烟曾借 worker-env 让「runtime 没打进去」全绿。判「最近
  跑过没有」要数**有结论的 run**。
- **Ruff（`python-lint`）是快线里最便宜的一格，不是别的门禁的替代品**：
  它只回答「这份 Python 在语法与名字层面立得住吗」（F821/F401/F841 那一类），
  语义问题仍归不变式 / 等价性矩阵 / CompatBench。它经 `CI fast gate` 参与合并
  资格（`needs` 与 `--required` 闭集里都有它，红了或 skipped 都会让 Gate 红），
  **没有新增 required context**。规则集与豁免的唯一出处是 `pyproject.toml` 的
  `[tool.ruff]`——workflow 命令行上不许再写一份，CI 也不许 `--fix`。
  当前状态：**lint、import 排序（`I`）、formatter 三项均已启用**。
  `python-lint` 这一格里 `ruff check` 与 `ruff format --check` 是**两个独立结论**
  （format 那步带 `if: always()`），一次 push 就把要修的都告诉你；CI 只检查、
  永不写回。**没有因此新增第四个 required context**——两条命令在同一个 job 里。
  first-party 靠 `[tool.ruff]` 的 `src` **按目录**判（那几个目录就是运行时真的
  被注入 sys.path 的），不靠一张会漂的名字清单——**但新增一处 sys.path 源码根
  时必须回来审查 `src`**，在已有源码根下加模块则不用动。
  理由与后续见 `docs/ci/ruff.md`。
- **backend 的 pytest 按文件分 2 片（CI03a，2026-09-16）**：`backend-fast` / `backend-platforms`
  的 matrix 各有一根 `shard: [1, 2]` 轴，命令是 `python -m pytest --shard=K/2 --shard-manifest=… --junitxml … -rs`（两个
  conftest 选项**必须 `=` 形式**：pytest 预解析会把未知选项的下一个 token 当路径去找 conftest，
  空格形式在路径已存在时 rc 4「unrecognized arguments」）。
  分片在**同一进程 collection 之后**做（`tests/conftest.py` → `tests/support/shard.py`）：每个进程
  算出全部两片，自验 **nodeid 集合**的并集 == 全集、两两不交、每片非空、无重复，任一条不成立
  rc 4——不是静默跑全集，也不是静默跑空集。**不带 `--shard` 时钩子是 no-op**，lab / nightly /
  release（`_lab-qualification.yml`）的 pytest 命令没有它，跑的仍是全集，
  `tests/test_merge_queue_workflows.py::TestGates::test_unsharded_pytest_lanes_stay_unsharded` 钉住。
  漏片兜底三层：进程内自验 → 静态合同（轴恰好是 `1..N`、命令里的 N 与轴长度同一个数：
  `::test_pytest_shards_agree_between_the_matrix_and_the_command`）→ matrix 语义（任一片不
  success，`needs.backend-fast.result` 就不是 success，Gate 闭集没动）。**job id 不变**，显示名
  变成 `backend-fast (3.10, 1)`，required contexts 仍只有三个 Gate，仓库设置不用重登记。
  权重表 `tests/support/shard_weights.json` 只影响两片平不平衡、不影响覆盖（表坏了是 rc 4 不是
  错分），从 CI 上传的 junit artifact 重算：`python tests/support/shard.py --from-junit …`。
  不许为了并行放宽任何产品断言、不加 `-n auto`。设计、本机实测、负例与已知边界：
  `docs/implementation/ci-foundation/CI03A_PYTEST_SHARDS.md`。
- **`windows-exe-smoke` 的 Playwright 按 project 分 2 片（CI03c，2026-09-16）**：matrix `include` 两条
  （片 1 `--project=chromium`；片 2 `--project=webkit --project=chromium-en`），每条带 `browsers`（本片要装的引擎）、
  `projects`（本片的 `--project=` 参数，原样进 `pnpm e2e`）与 `others`（其余片的）；值里不能有逗号。两片**各自完整**
  构建产物并都跑三条断言与冒烟①②③——必需步骤一律不加 `if:`。完整性三层：合同测试
  `tests/test_merge_queue_workflows.py::TestPlaywrightShards`（与 `web/playwright.config.ts` 的 project 集比：并集相等、两两不交、
  `others` == 其余片之并、装的引擎 == 本片 project 要的）→ 每片 e2e 之前的自验 `scripts/ci/playwright_shard_check.py`（对着
  Playwright 自己的 `--list`，主语是 (project, file:line:col, title) 集合，不是条数；不完整 rc 1、清单读不懂 rc 2）→ matrix 语义 + Gate
  闭集（不动）。**job id 不变**，显示名 `windows-exe-smoke (1)` / `(2)`（显式 `name:`；不写的话 include 形状会把四个字段全排进显示名），
  required contexts 仍只有三个 Gate。artifact 名一律带 `${{ matrix.shard }}`（upload-artifact v4 同名失败）。加 / 删 / 改名一个 project
  就要回去改那张 matrix，合同测试会红。两条 Playwright 步都有 **step 级** `timeout-minutes`（30 / 20，job 级 60 / 45 不动）：job 级硬杀时 step
  停在 in_progress、`if: failure()` 的收集步骤不跑、日志 blob 与 artifact 都没有（PR #373 attempt 1 实测），step 级超时把挂起变成带日志的失败。
  设计、本机实测、负例与已知边界：`docs/implementation/ci-foundation/CI03C_PLAYWRIGHT_SHARDS.md`。
- **`package` job 的冒烟按实例隔离（CI03b，2026-09-16）**：venv 在 `${{ runner.temp }}/smoke-venv`（两步共用
  `$VENV`，探 `bin` / `Scripts` 那套照旧），起服务那一步是 `python scripts/ci/package_smoke.py --python "$BIN/python"
  --workdir "${{ runner.temp }}/smoke-run"`（step 级 `timeout-minutes: 5`；失败时 `package-smoke-logs-<os>-<python>` 收走
  workdir）。原来的 `/tmp/smoke` + `--port 5199 … &` + `sleep 8` + 两条 curl 三样都不许回来
  （`tests/test_merge_queue_workflows.py::TestPackageSmokeIsolation`）。脚本的判据主语：**端口**是向系统租的
  （产品 `--port` 不接受 0，**不改**——端口冲突用例要的正是「被占用就顺延」），租约在真 bind 之前被抢时产品不会报错退出，
  而是顺延（「端口 P 被占用，改用 Q」）或退 0（「已在 … 运行」），脚本按日志里的占用类文案认出来换号重试；**就绪**
  = `/api/version` 200 + JSON **且**应答者持有本实例 data dir 里那枚凭据（`smoke_app.adopt_session_credentials` +
  `/api/session/ping` 200）——只看公共端点的 200，租约丢失时那个 200 可能来自隔壁实例；**终止** = 进程不存在
  （POSIX 整组 SIGTERM → SIGKILL → `killpg(pgid, 0)` ESRCH，组里剩人再升级；Windows `taskkill /T /F`），
  不是「发了信号」。data / config 由脚本放在 workdir 下（每次尝试一套），yml 里**不再**另设 `TAVOTTO_*_DIR`。
  被起的进程可换（`--launch` 模板，单测用 `tests/support/stub_http_server.py` 的十一种 `--fail-mode`）。
  设计、本机实测（真 wheel 就绪 2.3 s vs 原来盲等 8 s）、负例与已知边界：
  `docs/implementation/ci-foundation/CI03B_PACKAGE_SMOKE_ISOLATION.md`。
- **构建产物不跨 job 抽取、缓存只有两类（CI02，2026-09-16）**：九种 recipe（web 应用 / MCP 画布 / 插件候选 / playground /
  wheel / workerd / 内置 runtime / PyInstaller / .app）逐行量过——0 行值得新抽取，唯一的数据边仍是 `frontend → plugin-candidate`
  （消费者用 **checkout 的 HEAD** + **清单里的 content_digest** 核，不信 artifact 的名字）。ci.yml 里的缓存是**枚举**：
  `actions/cache` 只有 CPython 归档（两处消费者 + `cache-seed` 的种子步，key 含 `runner.os` / `runner.arch` / 锁 hash，恢复在
  `build_worker_runtime.py` 之前）、setup-node 的 pnpm store 按 `web/pnpm-lock.yaml`、rust-cache 各自点名 `workspaces` **并带一个
  以 (workspace, profile) 命名的 `shared-key`**（`workerd` / `desktop-shell` / `workerd-release`，见下一段）；venv / site-packages /
  用户目录 / 测试结果 / **Playwright 浏览器目录**一律不缓存（`tests/test_merge_queue_workflows.py::TestBuildReuseAndCaches`，
  多一条 `actions/cache` 就红——先回文档改数字）。两条派工时的前提被日志推翻，改之前先读：Windows 腿「装浏览器」的 245s 里
  **203–226s 是 `--with-deps` 装 Media Foundation**，浏览器下载只 17–27s——所以 `windows-exe-smoke` 两片的
  `pnpm exec playwright install ${{ matrix.browsers }}` **不带 `--with-deps`**（实验，由 CI02 那个 PR 的 full-ci run 判，红则一行加回；
  `posix-e2e` 的 `--with-deps chromium` 是 apt 真依赖，保留；两条整条命令都被合同钉着）。TypeScript 的类型检查只在
  `frontend` 的 `pnpm build`（第一条命令 `tsc -b`）里，`web/tsconfig.json` 的 references **集合**（app / node / e2e）就是它的
  覆盖面——少一份没有红灯，只是那一类错误从此没有执行位置。数字、反证与下一步：
  `docs/implementation/ci-foundation/CI02_BUILD_REUSE.md`。
- **push main 上的缓存种子（CI02 §4.1 (a)，2026-09-16 用户拍板）**：GitHub 缓存的作用域是「当前 ref + 默认分支」，合并组候选 ref
  （`gh-readonly-queue/main/pr-N-<sha>`）与 PR 首跑都读不到别人的缓存，而 push main 上原先没有任何产缓存的 job——合并资格这条唯一的
  常规执行点上三类缓存 **0% 命中**（合并组 run `35015416419` 的 12 个 job 没有一行 `Cache restored`），**每个候选各 save ≈ 1.8 GB**
  死重（已合入候选的 ref 没了、条目还挂着、没人清），仓库 10 GB 配额被顶穿后按最近访问淘汰，先走的是小而常用的 cpython / pnpm。
  所以 CI01「push main 只跑落地审计、不重复打包」的合同改成「落地审计 **+ 缓存种子**」：`cache-seed` 五条腿，每条 = 一个
  (os, rust-cache `shared-key`)——ubuntu·`workerd`、ubuntu·`desktop-shell`、macos·`desktop-shell`、macos·`workerd-release`、
  windows·`workerd-release`——各做「restore → 让消费者的准备步骤**真跑一次** → save」：pnpm store 每个 os 一次（`pnpm install
  --frozen-lockfile`）、CPython 归档挂在 `workerd-release` 两条腿（`build_worker_runtime.py --clean` 整跑，脚本没有「只下载」开关且
  产品脚本不动）、rust-cache 跑消费者那一组 cargo 命令（dev = `clippy --all-targets` + `test`；release = `build --release`）。
  **它不是门禁**：不在任何 Gate 的 needs / --required 里，红了不影响合并，也不加 `continue-on-error`（红着可见）。
  **key 对齐是成败所在**：pnpm / CPython 的 key 只含 os / arch / 锁 hash，种子与消费者写逐字相同的 with 块即可；rust-cache 的
  自动键含 **job id**，所以四处消费者都加了 `shared-key`（代替 job id 那一段；os / arch / rustc / `CARGO*` `RUST*` 环境变量 /
  Cargo.toml + Cargo.lock 仍由 action 并入），同一把键只对应**一种** cargo profile（dev 与 release 的 target/ 不是一份，
  所以 `workerd` ≠ `workerd-release`），`desktop-shell` 原先冗余的 `key: ${{ matrix.os }}` 一并收掉。一条腿一把键而不是一个 os
  一条腿：rust-cache 的 Post 步会把整机共用的 `~/.cargo/registry` 修剪到自己 workspace 的依赖集再 save，同一个 job 里两个实例会
  互相修剪，先声明的那份缓存里没有自己的 `.crate`。合同 `tests/test_merge_queue_workflows.py::TestCacheSeed` 六条 +
  `TestLandingAudit`（push 上的 job 集合 == {landing audit, cache-seed}）+ `TestBuildReuseAndCaches` 的三张枚举。**验法**：合入后
  第一次 push main 才有种子，之前入队的候选仍冷；看**下一个** merge_group run 的 `workerd` / `desktop-shell` / `windows-exe-smoke` /
  `macos-app-smoke` 日志有没有 `Restored from cache key "v0-rust-…" full match: true`（rust-cache）、`Cache restored from key:
  cpython-…`（actions/cache）、`Cache restored from key: node-cache-…`（setup-node）——不能看 PR 的第二次 run，那本来就暖。
  数字、变异反证与已知边界：`docs/implementation/ci-foundation/CI02_BUILD_REUSE.md` §4.1。
- **runner 信任区的静态守卫（CI04，2026-09-16）**：`tests/test_merge_queue_workflows.py::TestRunnerTrustZones` 四条——
  监听 `pull_request` / `pull_request_target` / `merge_group` 的每个 workflow，全部 job 的 `runs-on`（矩阵展开、经本仓库可复用
  workflow 递归）⊆ `{ubuntu-latest, macos-latest, windows-latest}`；`tavotto-lab` 只在 `_lab-qualification.yml`（只可
  `workflow_call`），调用方只有 `lab-ci.yml` / `release.yml`、事件 ⊆ `{push, schedule, workflow_dispatch}`；每个 workflow 的事件
  ⊆ 闭集 `{push, pull_request, merge_group, schedule, workflow_dispatch, workflow_call}`（`pull_request_target` 不在里面，加任何新事件
  先来登记）；`.github/actionlint.yaml` 的自定义标签集合 **==** 实际用到的自托管标签集合——**未部署的池不进配置**，预留一个标签也红，
  所以那份文件里只有 `tavotto-lab`，新池的标签与第一处 `runs-on` 必须同一个 PR 登记。**主语是 main 上的 workflow 文件**：它让
  「把 job 派到 self-hosted」的 PR 合不进 main，**挡不住** PR 自带的 workflow 在 PR 事件上先执行一次——那一半归 runner group 的
  workflow 限制 / fork PR 审批 / 私有 infra 仓库（本轮读到：仓库级 runner 4 台在 Default 组、org 是 free 计划、fork 审批只挡首次
  贡献者、同仓库分支 PR 不经审批），现状、缺口与管理员交接在 `docs/implementation/ci-foundation/CI04_RUNNER_PILOT.md` §2 与
  `ADMIN_HANDOFF_RUNNER_POOL.md`。`runner_pool_ready: not_run`。
- 每个「只在别人电脑上发生」的 bug 先变成 `tests/test_windows_regressions.py`
  的用例再谈修（cp936 编码、文件占用、盘符/反斜杠/中文路径、端口占用、
  CLI 只有 .cmd、解释器探测）。

## pwsh 步骤的退出码（issue #197，2026-09-14 查清）

- **pwsh / powershell 步骤里，最后一条原生命令故意非零退出（「期望用法错误退 2」
  那类判据）时，脚本必须以显式 `exit 0`（或 `$global:LASTEXITCODE = 0`）结尾。**
  否则断言全过、最后一行 `✓` 都打印了，步骤仍然退 1——不是 PowerShell 抛了什么，
  是两层机制叠在一起：
  * runner 会**改写**每个 pwsh 步骤的脚本：前置 `$ErrorActionPreference = 'stop'`，
    **后置** `if ((Test-Path -LiteralPath variable:\LASTEXITCODE)) { exit $LASTEXITCODE }`
    （actions/runner `src/Runner.Worker/Handlers/ScriptHandlerHelpers.cs` 的
    `FixUpScriptContents`）。你写的最后一行之后还有它这一行在跑，`$LASTEXITCODE`
    是哪条命令留下的它不管。
  * 步骤由 `pwsh -command ". '{0}'"` 起（同一文件的 `_defaultArguments`；Windows
    runner 未指定 `shell` 时默认就是 pwsh），而 `-Command` 会把非 0/1 的退出码折成 1
    （about_pwsh「-Command | -c」一节：「…an exit code other than 0 or 1, that exit
    code is converted to 1 for process exit code」）——所以看见的永远是 1，不是那个 2。
  显式 `exit 0` 在追加的那一行**之前**退出；失败路径全是 `throw`（Stop → 1），
  走不到它，所以不掩盖任何真失败。`ErrorRecord` 转换与
  `$PSNativeCommandUseErrorActionPreference` 都与此无关（2026-08-29 两轮实测证伪）。
  现有两处：ci.yml `windows-exe-smoke` 的「console 版 CLI」步骤、release.yml 的
  更新链验证步骤。

## 验证链（按层）

- **Matplotlib CompatBench**（`tests/compat/` + `scripts/ci/compat_matrix.py`，
  完整说明 `docs/ci/matplotlib-compatibility.md`）：与 `tests/acceptance/`
  **问的不是同一个问题**——那边比「Tavotto 今天 vs 昨天」（抓不到「我们从
  第一版起就一直改错某个 artist」），这边比「**原生 matplotlib** vs Tavotto
  零 override」，并沿九级漏斗（discover → execute → capture → open →
  semantic → edit → replay → export → fidelity）量化「外部 matplotlib 世界
  我们兼容多少」。两套 corpus **不许合并**，合了就再也分不清「我们退步了」
  和「我们本来就不支持」。
  * 结果分六类（`full_support` / `partial_support` / `unsupported_by_design` /
    `environment_dependency` / `product_bug` / `invalid_fixture`），
    **清单里没有声明过的失败一律记成 `product_bug`**；想声明某一级不该过
    要具体到阶段（`expected.<stage>=false` + `expected_false_reasons`），
    而 `execute` / `capture` / `open` **任何档位都不许声明成 false**。
  * 基线 `tests/compat/baseline.json` 与视觉基线同一套纪律（缺失 = FAIL、
    CI 绝不自动更新、`CI=true` 时 `--update-baseline` 被硬拒）；另加两条：
    非 full_support 必须写 reason、`product_bug` 还必须写 follow_up、
    **Tier 1 不许存在 product_bug**（schema 层面挡住）。**基线不是豁免名单。**
  * 判据一律复用产品自己的：重放比对走 `app._compare_manifests`（与写回放行/
    阻断同一把尺），像素比对走 `scripts/ci/pixelcompare.py`（与 golden 视觉
    回归**同一份算法**，从 `visual_regression.py` 提取出来的，不许再写第二份）。
  * artist 普查是**诊断**不是门禁：真正的 pass/fail 一律走生产路径的 worker。
  * 跑法：`--smoke`（PR，2~4 分钟）/ `--all` / `--target bundled|minimum|browser`
    / `--case <id>` / `--gate pr|main|nightly|release`。
- **四路等价性矩阵**（`tests/test_equivalence_matrix.py`，引擎的最终验收物）：
  `hot_apply(patches) == 清空后全量重放 == 全新 worker 重放 == 写回文件后全新
  worker 重放`，六个场景 × 十组 patch，判据直接复用 `app._compare_manifests`。
  四条腿各起独立 worker，核心场景在 workerd 控制面再走一遍。缺 matplotlib /
  缺 CJK 字体各自 skip 并注明理由。
- **五条结构性不变式**（`tests/test_invariants_engine.py` +
  `tests/support/engine_invariant_probe.py`）：能力真实 / 逐字还原 /
  热态==全量重放（含**删除**）/ 不许静默消失 / 单一权威。它们与
  `tests/acceptance/` 和 CompatBench 问的不是同一个问题，**三者不能互相替代**。
  能力真实那条**用像素说话**（`preview_png` 状态中立、6ms 一张、逐字节确定）。
- **端到端冒烟**：`python scripts/smoke_app.py --python .venv/bin/python`
  （或 `--exe dist/Tavotto/Tavotto.exe`）。隔离用户目录 → 渲染环境自检 →
  打开项目 → 渲染 → 导出 → 覆盖导出 → 干净退出（走 `/api/shutdown`，需
  `TAVOTTO_ALLOW_SHUTDOWN`；退出后断言没有残留 worker 子进程）。
  `--expect-source bundled` / `--expect-packages numpy,pandas,…` 是 Windows 桌面版
  的核心验收：少了它，一台碰巧装着 matplotlib 的 CI 机器会让「内置 runtime 根本
  没打进去」全程绿灯。CI 的 windows-exe-smoke 与 nightly 共用它。
  验收项目在 `examples/runtime_check/`（一个把整套内置科学栈都用一遍的脚本）。
  `--expect-control-plane workerd` 同理盯另一件静默失灵：桌面产物必须自带
  Rust supervisor，少了它渲染回退到 Python 池——功能全在、只是慢、零报错。
  两条冒烟腿都**不设 `TAVOTTO_WORKERD`**：要验的正是自动发现。
- **nightly 的安装链路（`nightly.yml`，每晚一次）**：三档代表性环境
  （无 Python / 官方 Python / Conda）× 中文用户名 + 中文区域 + cp936。
  冒烟项目**按档给**——`examples/runtime_check` 要整套科学栈，只有内置 runtime
  满足；指向用户自己解释器的两档用 `examples/figures`（numpy + matplotlib），
  它们验的是解释器优先级与中文路径。「无 Python」那档还会现打一个 NSIS
  安装器，走**装一遍再冒烟**：静默安装 → 断言安装目录里有 sidecar + 内置
  runtime + workerd → 起真壳确认它能拉起 sidecar 且退出不留孤儿 → 对装出来的
  sidecar 冒烟 → 覆盖安装（升级）再冒一次 → 静默卸载。这条链路只有真装一遍
  才知道，而且必须挂在**在发的那个发行形态**上。
- **黄金路径 E2E**：`cd web && pnpm e2e`（Playwright，`TAVOTTO_EXE` 指打包产物、
  缺省用 `python -m tavotto`）。跑之前先 `python scripts/build_frontend.py`——
  包内 `src/tavotto/web/` 优先于 `web/dist`，只跑 `pnpm build` 测的还是旧界面。
- **性能基线**：`python scripts/bench_render.py --python .venv/bin/python`。
  结论与前后对照都写进 `docs/perf-baseline.md`——**改性能前先在那儿指出一个
  数字**。它**默认不隔离 HOME**（重置 HOME 会让每次冷启动多出 9 秒字体缓存
  重建；要量首次体验用 `--fresh-home`）。
- 后端冒烟（示例项目）：`tavotto --figures examples/figures --no-browser
  --insecure-no-auth` 后 `curl -X POST /api/engine/render
  -d '{"id":"Fig1_kinetics.pdf","patches":[]}'`（不带 `--insecure-no-auth` 时
  curl 要加 `X-Tavotto-Auth` 头，见 ADR 0008）。
- 导出保真：导出 PDF 用 pymupdf `get_text()` 验证矢量文字。

## 发布链

- **`desktop-shell`（2026-09-04，issue #275）**：`src-tauri` 的
  `cargo fmt --check` / `clippy -D warnings` / `cargo test`，与 `workerd` 同一条
  纪律（都不做 paths 过滤）。它原先只在 `desktop-tauri.yml` 里跑，而那个工作流
  只在打 tag / dispatch 时跑、`cargo test` 还收在 build 矩阵的 macOS 那条腿上
  ——改了壳的 PR 因此一路全绿，Rust 侧判据合并前一次都不执行。
  **`tauri.conf.json` 的 `bundle.resources` 指向 `../dist/Tavotto`，空目录就够**
  （`mkdir -p dist/Tavotto`），所以这一格不必挂在完整打包之后，几十秒回来。
  **两条腿：`ubuntu-latest` + `macos-latest`（2026-09-07，issue #282）。**
  clippy 只看得见参与编译的那一支，而 `main.rs` 的应用菜单有
  `#[cfg(target_os = "macos")]` 分支——只跑 Linux 腿时，那一支的 lint 在任何
  工作流里都没有执行位置（`desktop-tauri.yml` 的 macOS 腿只跑 `cargo test`，
  不 deny warnings：编译错误抓得到、lint 抓不到），又是一条「登记了但从不执行」
  的判据。这条边界现在**已经关掉**，不再是已知边界。`cargo fmt` 不吃 cfg
  （rustfmt 解析整个文件），本来就两支都看得见。
  matrix 一变（少一类 runner，或把某条 cargo 命令用 `if:` 收窄到一条腿上）
  由 `tests/test_merge_queue_workflows.py::TestGates::test_desktop_shell_lints_both_sides_of_the_macos_cfg`
  与同类 `::test_the_rust_gates_run_on_every_desktop_shell_leg` 当场判红。
  **job 名字变了**：显示名现在是 `desktop-shell (ubuntu-latest)` /
  `desktop-shell (macos-latest)`，但 job **id** 仍是 `desktop-shell`，
  `needs:` 与 `--required` 读的都是 id，required contexts 也只有三个 Gate 名字
  （`scripts/ci/merge_queue_ruleset.py` 的 `GATE_CONTEXTS`）——所以仓库设置里
  **不需要重新登记任何必需检查**。
- **「在 Gate 的闭集里」≠「在 PR 上会跑」**：重型那几档接在 integration gate 里，
  普通 PR 上整体 skipped 而 Gate 判 deferred（绿）。把一个 fast 档的 job 改成
  重型条件，Gate 依旧全绿而它守的东西合并前一次都不验——
  `tests/test_merge_queue_workflows.py::test_every_fast_lane_job_actually_runs_on_a_plain_pull_request`
  逐个比死条件看住这一位。
- **完整 Codex 插件（ADR 0043，2026-09-05）**：ci.yml 的 `frontend` job 从本次 checkout 真构建
  画布 → `scripts/plugin_stage.py` 按 git 清单 + 显式构建物组装、验证、确定性 zip → artifact
  `codex-plugin-candidate`；`plugin-candidate` job 脱离源码树解包、真起 MCP server 读画布、执行
  `tests/test_plugin_candidate.py`（有产物时**不许 skip**）。两者都在 `CI fast gate` 的闭集里。
  候选只作验证，**不向源码分支回写、不发布**。release.yml 的 `build` job 在固定发行 SHA 上
  同样造一次（`--serve` 用发出去的 wheel），三样进 `dist/`（zip / `codex-plugin.json` /
  `codex-plugin-build.json`）与产物清单；`validate_artifacts` 成对验证；`plugin_stable` job 在
  Release 与 PyPI 之后把**同一份** zip 投影到发行分支 `plugin-stable`（publish=false 时对临时
  bare 仓库演练全部发布行为 + 对真实远端只读 plan）。手动入口 `plugin-stable.yml`
  （bootstrap / promote / rollback，从 Release 资产取内容）。手册：`docs/ci/plugin-stable-channel.md`。
  **发行分支不触发任何源码 CI**——没有 workflow 监听它，GITHUB_TOKEN 的推送也不触发。
- release.yml 的插件版本清单（`codex-plugin.json`）由 `build` job 生成（不再在没有 Node 的
  `validate_artifacts` 里从源码目录打包），**不能挪进 desktop-tauri.yml 的 updater-manifest**
  （那个 job 没配 minisign 私钥就整个跳过，插件更新通道会悄悄停而且全绿）。
- 桌面更新清单 `latest.json` 由 `scripts/make_updater_manifest.py` 在两条
  matrix 腿都跑完后合成；macOS 更新包必须在签名/公证之后重做
  （见 `src-tauri/AGENTS.md`）。
- 遥测部署顺序：先发代理 → 验 PostHog 收得到 → 配采集器 → 再发客户端
  （反过来新事件被静默 400 而且全绿）。发行量采集器失败必须让 workflow 红。
