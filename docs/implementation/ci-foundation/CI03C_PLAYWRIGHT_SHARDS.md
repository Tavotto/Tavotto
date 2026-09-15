# CI03c · Windows Playwright 按 project 分两台机器 + Playwright 步加 step 级超时（2026-09-16）

> 后记（CI02，同日）：下面写的 `playwright install --with-deps ${{ matrix.browsers }}` 在 CI02 里去掉了 `--with-deps`——那 ~3.2 分钟的
> 「安装」有 204–226s 是 Windows Server 的 Media Foundation，浏览器下载只 17–27s；由 CI02 那个 PR 的 full-ci run 判 Chromium 要不要它。
> 数字与拆分见 [`CI02_BUILD_REUSE.md`](CI02_BUILD_REUSE.md) §2。本文其余内容是 CI03c 当时的事实，不改。

- 改动对象：`.github/workflows/ci.yml`（`windows-exe-smoke` / `posix-e2e`）、`scripts/ci/playwright_shard_check.py`（新）、
  `tests/test_playwright_shard_check.py`（新）、`tests/test_merge_queue_workflows.py`（新增 `TestPlaywrightShards` 十条 + 三个 helper）、
  `scripts/ci/ci_baseline.py` + `tests/test_ci_baseline.py`（显示名带表达式时映射回 job id）、`web/playwright.config.ts`（只改注释：webkit project
  那段「这一腿在哪跑」）。叠在 CI03a `d9e8dd72`（PR #374）之上；栈：#372 CI00 → #373 CI01 → #374 CI03a → 本 PR。
- **改了什么**（ci.yml 非注释行，逐条）：
  1. `windows-exe-smoke` 加 `name: windows-exe-smoke (${{ matrix.shard }})` 与 `strategy.fail-fast: false` + `matrix.include` 两条：
     `{ shard: 1, browsers: "chromium", projects: "--project=chromium", others: "--project=webkit --project=chromium-en" }` /
     `{ shard: 2, browsers: "chromium webkit", projects: "--project=webkit --project=chromium-en", others: "--project=chromium" }`；
  2. 原来一步「Playwright 黄金路径」拆成三步：`装 web 依赖与本片的浏览器`（`pnpm install --frozen-lockfile` + `pnpm exec playwright install --with-deps ${{ matrix.browsers }}`）→
     `Playwright 分片自验`（`python scripts/ci/playwright_shard_check.py --shard ${{ matrix.shard }} --projects="${{ matrix.projects }}" --others="${{ matrix.others }}" --web web --out "${{ runner.temp }}/playwright-shard"`）→
     `Playwright 黄金路径`（`timeout-minutes: 30`；`pnpm e2e ${{ matrix.projects }}`，环境变量两行不变）；
  3. 新增一步 `if: always()` 的 `upload-artifact`：`playwright-shard-check-windows-shard${{ matrix.shard }}`（两份 `--list` + check.json，7 天）；
  4. artifact 改名：`bench-large-preview-windows` → `bench-large-preview-windows-shard${{ matrix.shard }}`、`windows-smoke-logs` → `windows-smoke-logs-shard${{ matrix.shard }}`；
  5. `posix-e2e` 的「Playwright 黄金路径」步加 `timeout-minutes: 20`。
- **没改什么**：job id（`windows-exe-smoke`）、`needs: [frontend]`、`if:`（merge_group / full-ci）、job 级 `timeout-minutes`（60 / 45）、
  `ci-integration-gate` 的 `needs` / `--required` 闭集、`scripts/ci/aggregate_gate.py`、`macos-app-smoke`、`posix-e2e` 的分片形态（不分）、
  两片各自完整的构建链与三条断言 + 冒烟①②③（**没有任何一步加 `if:`**）、`web/playwright.config.ts` 的 `workers: 1` / `fullyParallel: false` /
  `retries` / 三个 project 的 `testMatch` / `testIgnore`、任何用例或 fixture、reporter（CI 仍是 `list` + `html`；不做跨片报告合并——判定由 matrix + Gate 兜，不由报告兜）。
- **本轮没有任何真实的分片 CI run**（不能 push）。本机实测是 macOS 上 `python -m tavotto` 形态（与 posix-e2e 同形），**不是 Windows .exe 形态**——§6。
- 回退：删掉 `name:` 与 `strategy:` 两段、`pnpm e2e` 去掉 `${{ matrix.projects }}`、浏览器安装写回 `chromium webkit`、两个 artifact 名去掉片号、
  删掉自验步骤与它的 artifact；两处 step 级 `timeout-minutes` 留着也无害（它们与分片无关）。`ci_baseline.py` 的表达式映射留着无害。

## 1. 设计与判据的主语

| 环节 | 主语 | 出处 |
|---|---|---|
| 切法 | **Playwright 的 project**（`chromium` / `webkit` / `chromium-en`），不是文件、不是 Playwright 自带的 `--shard` | `ci.yml` matrix include 的 `projects` |
| 完整性 | **三个 project 各恰好出现在一片里**：并集 == `playwright.config.ts` 的 project 集、两两不交、无空片 | ① 合同测试 ② 每片自验 ③ matrix 语义（§3） |
| 每片自验 | **(project, file:line:col, title) 的集合**：本片 `--list` == 全集 `--list` 里 project ∈ 本片的那些；本片 ∪ 另一片的 project 集 == 全集的 project 集。不是条数 | `scripts/ci/playwright_shard_check.py::verify` |
| artifact 名 | **矩阵展开后**同一 job id 下的名字不能相同（upload-artifact v4 同名失败） | `test_every_artifact_of_the_sharded_job_is_named_per_shard` |
| step 超时 | **step** 的 `timeout-minutes`（缩进 8），不是 job 的（缩进 4）；job 级不动 | `test_the_playwright_step_has_a_step_level_timeout` |
| 浏览器 | 本片 project 的设备家族推出的引擎集（`devices['Desktop Chrome']` → chromium、`Safari` → webkit），装的 == 要的 | `test_each_shard_installs_exactly_the_engines_its_projects_need` |
| 显示名 | `name: windows-exe-smoke (${{ matrix.shard }})`——include 形状的 matrix 不写 name 会把四个字段全排进显示名（`windows-exe-smoke (1, chromium, --project=chromium, --project=webkit --project=chromium-en)`）；job id 不变，Gate 读 id | `ci_baseline.display_to_job_id` 按表达式配 |

`others` 是「其余片的 project 集」，与 `projects` 一起放进同一条 include 里：每片的 job 自己就拿得到「另一片声称要跑什么」，不用第二处
（job 级 env / 文件）再写一遍划分。两个字段之间的一致性由合同测试钉（`others` == 其余片 `projects` 之并），运行时又由自验再钉一次
（本片 ∪ others == 全集的 project 集）——改了一片的 `projects` 没改另一片的 `others`，合同测试红；就算合同测试没跑，改过的那一片在 CI 上也红在 e2e 之前。

自验脚本**自己起 node** 跑 `node_modules/@playwright/test/cli.js test --list`（两次：全量 + 本片），按 UTF-8 解码——不经 `pnpm` / `npx` 的
`.cmd` shim，也不经 pwsh 的 `>` 重定向（它按控制台代码页把中文标题转一遍；两份清单被同样地转坏时集合比对照样绿，但坏掉的标题可能把
本该不同的条目粘成同一条）。本机验证过 `--web` 产出的三份清单与 `npx playwright test --list` 的输出**逐字节相同**（`evidence/ci03c/selfcheck_shard*.txt`）。
`--projects` / `--others` 的值以 `--` 开头，**必须写 `--opt=值`**（空格形式 argparse 报「expected one argument」rc 2，本机第一次就踩到）。

## 2. 为什么按 project，而不是按文件或 `--shard`

- Playwright 自带的 `--shard=K/N` 按 test group 的**顺序**等分，不看时长；`fullyParallel: false` 下 group = 文件，三个 project 的同名文件
  （`a11y.spec.ts` 在三个 project 里各一份）会被排在一起，跨 project 的平衡要自写分区器 + 权重表（CI03a 对 pytest 做的那套）。
- 按 project 切，完整性判据是「project 集合的划分」——三个元素的集合，合同测试一眼钉得住；`--list --project=X` 又正是 Playwright 自己算出来的
  「这一片跑什么」，自验不用复现 testMatch / testIgnore 的语义。
- 时长上恰好也平：CI00 的 Windows 实测 chromium 505s vs webkit 267 + chromium-en 111 = 378s；PR #373 attempt 2 是 474 vs 156 + 98 = 254（§6）。
  片 1 略重，但片 2 要多装 webkit（~40s），差距可接受；再平衡的下一步是把 chromium 按文件切，不在这里做。
- 浏览器安装按需（片 1 不装 webkit）也只有按 project 切才成立：文件级切法两片都可能碰到 webkit。

## 3. 完整性三层（谁在哪一层挡）

1. **合同测试**（源码层，PR 快线的 backend-fast 里跑）：`tests/test_merge_queue_workflows.py::TestPlaywrightShards`——
   读 `web/playwright.config.ts` 的 `projects[].name`（只看代码行，切不出来当场抛，不回空集）与 ci.yml include 的 `--project=` 集合比：
   并集相等、两两不交、无空片、`others` == 其余片之并、`browsers` == 本片 project 要的引擎、`pnpm e2e ${{ matrix.projects }}` 与
   `playwright install --with-deps ${{ matrix.browsers }}` 都从 matrix 取、自验步骤在 e2e 之前且拿的是 matrix 的三个字段、
   带 `if:` 的步骤只能是 artifact 上传、artifact 名带片号、两条 Playwright 步的 step 级 timeout、job id / `runs-on` / Gate 闭集不变、
   posix-e2e 不分片且写死的 project 名仍在配置里。变异反证 18/18（§7）。
2. **每片自验**（运行时，每个 shard job 的 e2e 之前）：`scripts/ci/playwright_shard_check.py`——对着 Playwright 自己的 `--list`，
   集合级比对；清单读不懂（空、认不出的行、重复、`Total:` 与条数不符）rc 2，分片不完整 rc 1，都让 job 红在 e2e 之前。
   它看得见「配置里 project 是动态拼的」「合同测试的正则没看见的形状」；看不见「另一片的 job 有没有跑」。
3. **matrix 语义 + Gate 闭集**（未改动）：任一片不 success，`needs.windows-exe-smoke.result` 就不是 success；`ci-integration-gate`
   的 `--required` 闭集里仍是这一个 job id，`aggregate_gate.py` 对非 success 一律判失败。required contexts 只有三个 Gate，仓库设置不用重登记。

「另一片的 job 有没有跑」只有第 3 层答得了；「配置里多了一个 project 而 matrix 没跟上」第 1、2 层都答得了（C14 / N11）。

## 4. 预期时长模型（**是模型，不是实测**；分片后的 Windows run 一次都没跑过）

来源：CI00 的 windows-exe-smoke（job 104397475552，`CI_BASELINE.md` §8）与 PR #373 attempt 2（job 104492544345，`evidence/ci03c/ci_pr373_attempt2_windows_per_project.json`）。

| 段 | attempt 2 实测 | 片 1 模型 | 片 2 模型 |
|---|---:|---:|---:|
| checkout + setup + 前端 / 画布 / workerd / runtime / PyInstaller + 三条断言 + 冒烟①②③ | 4.9 min（17:33:52 → 17:38:46） | 4.9 | 4.9 |
| `pnpm install` + `playwright install --with-deps …` | ~3.2 min（步 1004s − 用例 813s；装 chromium + webkit） | ~2.5（只 chromium） | ~3.2 |
| 分片自验（两次 `--list`，本机各 0.4s；CI 上按几秒算） | — | ~0.1 | ~0.1 |
| 用例（list reporter 逐条之和） | chromium 474 / webkit 156 / chromium-en 98（CI00：505 / 267 / 111） | 7.9–8.4 | 4.2–6.3 |
| 大图基准 + 上传 | ~0.3 | 0.3 | 0.3 |
| **合计** | **22.2 min**（CI00 中位 23.5） | **≈ 16–16.5** | **≈ 13–15** |

链路：frontend（中位 4.2 min）→ 片 1 ≈ 16.5 → **≈ 21 min**；之后合并资格的关键路径不再是这条腿，而是 backend-platforms 的 Windows 片
（CI03a 分片后待实测）。两片各多付一次 4.9 min 的构建 + 一次浏览器安装：runner 分钟从 ~22 涨到 ~30；换来的是关键路径 −6～7 min。
CI02 若抽产物复用（一台构建、两台下载）再回来评估。

## 5. step 级超时：为什么、多少、证据

- **证据**（`evidence/ci03c/ci_pr373_attempt1_hang.json`）：PR #373 run 34994534095 attempt 1，`windows-exe-smoke`（job 104468499895）：
  前 21 步 16:26:11Z → 16:30:58Z 全部 success；第 22 步「Playwright 黄金路径」16:30:58Z 起 `in_progress`，直到 job 级 `timeout-minutes: 60`
  在 17:31:10Z 把整个 job 硬杀（conclusion `cancelled`）。之后的「大图预览内存基准」「上传内存基准」「失败时收集日志与操作轨迹」三步与
  六个 Post 步骤全是 `pending`——**一步都没跑**。`gh api …/jobs/104468499895/logs` → HTTP 404「The specified blob does not exist」；run 的
  artifact 只有 attempt 2 传的 `bench-large-preview-windows` 与 frontend 的 `codex-plugin-candidate`。同一 SHA 的 attempt 2 这一步 16.7 min 成功。
  也就是说这条腿有一种「挂 60 分钟、什么都不留」的失败形状，而它的根因无从查起——因为没有日志。
- **机制**：job 级超时是 runner 取消整个 job，`if: failure()` 的步骤不跑（job 结论是 cancelled 不是 failure），Post 步骤也不跑，
  in_progress 的 step 日志 blob 不落盘。step 级 `timeout-minutes` 到点时是**这一步 failure**，job 继续走后面的步骤：`if: failure()` 的收集步骤
  会跑，`web/playwright-report/**` / `web/test-results/**`（含 trace）传得上来，Post 步骤也跑。挂起变成带日志的失败。
- **数值**：windows-exe-smoke 30 min——CI00 实测这一步 18.7 min（三个 project 全跑），分片后片 1 ≈ 8.4 min、片 2 ≈ 6.3 min，30 是两倍余量以上，
  且低于 job 级 60（step 到点时 job 还有时间做收集）。posix-e2e 20 min——实测 7.4 min。job 级 60 / 45 **不动**。
- 合同：`test_the_playwright_step_has_a_step_level_timeout[windows-exe-smoke-30-60]` / `[posix-e2e-20-45]`——主语是 step 的键，job 级的数字也一起钉。

## 6. 本地实测（本机 macOS，`python -m tavotto` 形态；命令、日志、计时在 `evidence/ci03c/`）

前提复核过：`tavotto.__file__` 在 worktree、`scripts/build_frontend.py` + `scripts/build_mcp_widget.py` 刚建（`src/tavotto/web/index.html` 与
`canvas.html` 的 mtime 在跑之前 1 分钟内）、`TAVOTTO_PYTHON` 指 worktree 的 `.venv/bin/python`（editable 装的是本 worktree，有 matplotlib 3.11.2）、
PATH 只留 node（藏掉本机的 codex / claude，否则 ux-consistency 流程 C 走与 CI 不同的分支）。**全部前台跑**（CI03a §3.4 的坑）。
webkit 本机已装（v2336），所以片 2 是完整的两 project。

### 6.1 `--list` 三份与自验（本机，不运行）

| 清单 | 条数 / 文件数 | 自验 |
|---|---:|---|
| 全量 | 151 / 24（chromium 109、webkit 23、chromium-en 19） | — |
| `--project=chromium` | 109 / 23 | `--shard 1 …` rc 0：109 == 全集里 chromium 的 109；{chromium} ∪ {webkit, chromium-en} == 全集 |
| `--project=webkit --project=chromium-en` | 42 / 6 | `--shard 2 …` rc 0：42 == 23 + 19 |

### 6.2 真跑（逐 project 用例秒数之和；与 CI 的两份 Windows 数字并排）

| project | 本机（条 ran / skipped，秒） | CI00 Windows job 104397475552 | PR #373 attempt 2 Windows job 104492544345 |
|---|---:|---:|---:|
| chromium | 89 / 20，595.6s（B1 + B2） | 89 / 20，505.5s | 89 + 1 重试 / 20，474.2s（1 flaky） |
| webkit | 23 / 0，180.9s | 23 / 0，266.9s | 23 / 0，156.2s |
| chromium-en | 17 / 2，135.3s | 16 / 3，110.6s | 16 / 3，98.1s |
| 墙钟 | 片 1：601.8s（354.9 + 246.8）；片 2：320.1s | 步 1124s（含安装） | 步 1004s（含安装），用例段 813s |

- chromium-en 本机 17 条 vs Windows 16 条：`error-recovery-en` 的两条 POSIX 权限位用例只在非 Windows 上真跑、`file_locked` 只在 Windows 上真跑
  （`tests/test_e2e_leg_topology.py` 钉的那三条），与 posix-e2e 腿的形状一致。20 条 playground* 在本机也 skip（没建 `dist-playground`），与 CI 相同。
- 集合比对（`local_runs_summary.json`，主语 (project, file:line:col, title)，标题含 describe 前缀）：B1 ∪ B2 == `list_shard1.txt`（109）、
  C == `list_shard2.txt`（42）、B ∪ C == `list_all.txt`（151）、B ∩ C = ∅——四条全 true。**本机跑过的集合就是两片声称要跑的集合，一条不多一条不少。**
- 本机比 Windows runner 慢（chromium 596 vs 474–505s）：同一台机器上另外三个会话在并行做事，CPU 不是独占的；数字只用来证明「跑得完、全绿、集合对」，
  不用来估 CI 时长（§4 用的是 CI 自己的数）。

### 6.3 A 那次的红与本机权宜

- **A**（片 1 命令原样第一次跑，599.4s）：87 passed / **1 failed** / 1 did not run / 20 skipped。红的是 `large-figure.spec.ts:217`，原因是**本机环境**：
  为了藏掉 codex / claude 把 PATH 收窄到 `/usr/bin:/bin:…`，于是 fixture 脚本退路里的 `python3` 变成了系统 Python 3.9，`def build(n: int | None = None)`
  在 3.9 上 `TypeError`（`evidence/ci03c/local_runs/runA_shard1.log`）。did not run 的那条是它 serial 后面的 `:266`。CI 上 `python3` 是 setup-python 3.13
  （posix-e2e）或被 `TAVOTTO_WORKER_PYTHON = $null` 清掉后走内置 runtime（windows-exe-smoke），不会踩到。修法：受限 PATH 里放一个 `python3` wrapper
  指向 worktree 的 `.venv/bin/python`（不能用符号链接——venv 的 python 经两层 symlink 解析后会丢掉 `pyvenv.cfg`，`sys.executable` 变成 homebrew 的 3.13，
  matplotlib 变成 3.10.8）。
- **B 的权宜**：工具单次前台调用上限 10 分钟，A 已经 599s 贴着上限，再加两条 large-figure（各十几秒）会被截。所以 B 把片 1 用 Playwright 自带
  `--shard=1/2` / `--shard=2/2` 分成两次前台调用（56 + 33 passed / 20 skipped），并用集合比对证明两次的并集 == `list_shard1.txt`。
  **CI 的命令不带 `--shard`**；这只是本机把一条 10 分钟的命令装进两个 6 分钟的前台窗口。没有用后台起（CI03a §3.4）。

## 7. 负例与变异反证

### 7.1 自验脚本的负例（`evidence/ci03c/negative_cases.txt`，12 条，退出码全部非零）

| # | 输入 | 期望 | 实测 |
|---|---|---|---|
| N1 | 片 2 只写 `--project=webkit`（漏 chromium-en），`--web` 真跑 | rc 1 | rc 1，`配置里的 project ['chromium-en'] 不在任何一片里——漏片` |
| N2 | 两片都含 chromium，`--web` 真跑 | rc 1 | rc 1，`两片都声称要跑 project ['chromium']` |
| N3 | 本片 `--list` 是空文件 | rc 2 | rc 2，`一条用例都没有` |
| N4 | 本片清单里塞一行没有 ` › ` 的 | rc 2 | rc 2，`这一行认不出` |
| N5 | 片 1 的参数配片 2 的清单 | rc 1 | rc 1，`本片少了 109 条` + `本片多了 42 条` |
| N6 | 删一条但 `Total:` 没改 | rc 2（解析层） | rc 2，`Total: 说有 109 条，解析出 108 条` |
| N7 | 删一条并把 `Total:` 改成 108 | rc 1（判定层） | rc 1，`本片少了 1 条` |
| N8 | 一条重复（`Total:` 同步成 110） | rc 2 | rc 2，`重复条目 1 条` |
| N9 | `--projects=`（空片） | rc 2 | rc 2，`一个 --project= 都没有（空片）` |
| N10 | `--projects` 混进 `--grep=a11y` | rc 2 | rc 2，`不是 --project=NAME 的形状` |
| N11 | `others` 里写了配置里没有的 firefox | rc 1 | rc 1，`在全集 --list 里不存在` |
| N12 | 清单没有 `Total:` 行 | rc 2 | rc 2，`没有 Total: 行——输出被截断了？` |

### 7.2 单元测试的变异反证（`evidence/ci03c/mutations.json`，**19/19 KILLED**）

对 `scripts/ci/playwright_shard_check.py` 逐条拿掉检查：parse_list 的六条（空 / 认不出的行静默跳过 / 没有 Total / Total 与条数不符 / 重复 /
两行 Total）、parse_projects 的三条（任何 token 都当 project / 空片 / 重复）、verify 的七条（重叠 / 漏片 / 配置里没有的 project / 本片为空 /
本片有不在全集里的 / 本片少了 / 本片多了）、main 的两条（有问题也退 0 / 只给 `--full` 时本片默认成全集）、集合 vs 计数（少 / 多改按计数判）。
一条值得写下的：第一轮 M03「拿掉没有 Total 行」**存活**——`total != len(entries)` 在 `total is None` 时也抛，两条检查冗余
（「冗余的保证杀不死」）。改成 `total is not None and total != len(entries)` 之后各守各的，19/19。

### 7.3 ci.yml 合同测试的变异反证（`evidence/ci03c/mutations_ci.json`，**18/18 KILLED**，每条记着是哪几条用例红）

片 2 的 projects 漏 chromium-en / 片 1 的 others 漏 chromium-en / 两片都含 chromium / 片 2 只装 chromium / `pnpm e2e` 不带 matrix.projects /
删掉自验步骤 / 冒烟③加 `if: matrix.shard == 1` / 失败日志 artifact 名去掉片号 / Windows Playwright 步去掉 step timeout / posix 同 /
job 级 timeout 60 → 90 / 删掉 `name:` 行（两条用例红：合同 + ci_baseline 的映射）/ shard 写成 1, 1 / `playwright.config.ts` 多一个 firefox project /
自验的 `--others` 写死字面量 / posix-e2e 的 project 名拼错 / 浏览器安装写死 chromium webkit / posix-e2e 加 strategy 分片。
每条：断言目标串恰好一次 → 变异 → 清 `__pycache__` → pytest 退出码判 → 还原核 md5；跑完树的 md5 与跑前一致。

## 8. 验证命令与退出码（2026-09-16，worktree，本 PR 的树）

| 命令 | 退出码 |
|---|---:|
| `/opt/homebrew/bin/actionlint .github/workflows/ci.yml` | 0 |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0（370 files already formatted） |
| `.venv/bin/python -m pytest tests/test_playwright_shard_check.py tests/test_merge_queue_workflows.py tests/test_ci_baseline.py tests/test_aggregate_gate.py tests/test_ci_tooling.py tests/test_docs_references.py tests/test_windows_regressions.py` | 0（264 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」） |
| 同上再加 `tests/test_e2e_leg_topology.py tests/test_source_hygiene.py tests/test_support_matrix.py tests/test_merge_queue_ruleset.py tests/test_release_workflow_contract.py tests/test_ci_qualification.py tests/test_update_chain_gates.py tests/test_cla_workflow_contract.py tests/test_pytest_shard.py tests/test_tracked_paths_are_windows_safe.py tests/test_generated_untracked.py` | 0（316 passed, 1 skipped） |
| `python scripts/ci/playwright_shard_check.py --shard 1/2 … --web web`（§6.1） | 0 / 0 |
| 本机真跑 A / B1 / B2 / C（§6.2、§6.3） | 1（环境）/ 0 / 0 / 0 |
| 实机：PR 上 full-ci 的 `windows-exe-smoke (1)` / `(2)` 各自的自验 artifact 与 e2e 结论 | **not_run**（不能 push） |

`tests/test_windows_regressions.py` 的两条 UTF-8 stdout 门禁扫到了新脚本（`scripts/**` 里带 `__main__` 的入口都要钉 stdout + stderr）——它入口
处 `reconfigure(encoding="utf-8", errors="replace")` 两条流都钉了，与 `aggregate_gate.py` 同写法。

## 9. 已知边界（如实）

- **Windows 真产物形态一次都没跑过分片**：本机是 macOS + `python -m tavotto`，与 posix-e2e 同形；`.exe` + 内置 runtime + 两台 Windows 机器
  各自构建的形态要等本 PR 的 full-ci run（打标签后看 `windows-exe-smoke (1)` / `(2)` 各自的 `playwright-shard-check-windows-shard<K>` artifact
  与 e2e 结论）。CIP-017 因此不写 pass（`acceptance.json` 的理由）。
- **同一份产物两台机器各建一次**（~4.9 min × 2 + 浏览器安装 × 2）：runner 分钟 +~8；CI02 抽产物复用时再评估。
- **片 2 的 chromium-en 与片 1 的 chromium 装的是同一个浏览器**——两片都装 chromium 是必要的（片 2 要跑 chromium-en），不是冗余。
- **自验只能证明「本片声称的 == Playwright 算出来的」**，证不了「另一片的 job 真跑了」——那是第 3 层（matrix 语义 + Gate）的事，未改动。
- `--list` 在 Windows 上打的路径是 `e2e\a11y.spec.ts`（list reporter 实测），本机是 `a11y.spec.ts`；脚本按 `\S+:\d+:\d+` 认位置、两份清单
  同一台机器比，不受影响（`test_parse_accepts_windows_style_paths` 钉住）。
- `include` 的值里**不能有逗号**（`ci_baseline.py::_parse_matrix` 与合同测试的 `_matrix_include` 都按逗号切）；现在四个字段都没有。
- 本机数字（§6.2）不代表 CI：机器被并行会话共用；模型（§4）用的是 CI 自己的两份实测。
- `retries: 1` 在 CI 上保留：attempt 2 那次 chromium 有 1 flaky（asset-library 多-Figure，第一次 x 重试 ok）——**不靠分片消掉它**，
  它在 `coverage_ledger.json` 的 known_limitations 里继续挂着。
