# evidence/ci03c/ · CI03c 的证据

本机部分全部产自 worktree（macOS arm64 Mac mini 12 核，Python 3.13.11 / matplotlib 3.11.2 / Playwright 1.62.1 / Node 26.7.0，与 CI00 / CI03a
同一台机器；跑的期间另外三个会话在同一台机器上做各自的事，CPU 不是独占的），日期 2026-09-16（UTC 09-15 晚），树是本 PR 的工作树
（`d9e8dd72` + 本轮改动）。**没有一条真实的 Windows 分片 run**：不能 push。CI 侧只取了两份**已有** run 的事实（PR #373 的两次 attempt）。
解读在上一级的 [`CI03C_PLAYWRIGHT_SHARDS.md`](../../CI03C_PLAYWRIGHT_SHARDS.md)。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `list_all.txt` / `list_shard1.txt` / `list_shard2.txt` | `npx playwright test --list` 全量（151 条 / 24 文件）、`--project=chromium`（109 / 23）、`--project=webkit --project=chromium-en`（42 / 6）——本机，不运行 | `cd web && npx playwright test --list …` |
| `selfcheck_shard{1,2}.txt` / `.json` | 自验脚本 `--web` 模式（脚本自己起 node 跑两次 `--list`）对两片各跑一次的 stdout + check.json，退出码 0；`--web` 产出的三份清单与上面 npx 的**逐字节相同**（`diff` 空） | `python scripts/ci/playwright_shard_check.py --shard K --projects=… --others=… --web web --out …` |
| `negative_cases.txt` | 12 条负例 N1…N12 的命令、输出与退出码（N1 / N2 是 `--web` 真跑，其余是对存好的清单做变异） | 一次性命令（写在文件里） |
| `mutations.json` | 单元测试的变异反证 19 条（M01…M19）：对 `scripts/ci/playwright_shard_check.py` 逐条拿掉检查，`tests/test_playwright_shard_check.py` 红不红；**19/19 KILLED**，每条的目标串 / 替换串 / 退出码 / 红的条数原样记着 | `mutate_pw.py`（会话 scratchpad，不进仓库；顺序：断言目标串恰好一次 → 变异 → 清 `__pycache__` + `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` → pytest 退出码判 → 还原核 md5） |
| `mutations_ci.json` | ci.yml / playwright.config.ts 合同测试的变异反证 18 条（C01…C18），**18/18 KILLED**，每条记着是哪几条用例红的 | `mutate_ci.py`（同上） |
| `local_runs_timing.json` | 本机四次真跑（A / B1 / B2 / C）的命令、结果、起止时刻、`/usr/bin/time -l` 的 real / user / sys / 峰值 RSS、退出码 | 从 `local_runs/*.time` / `*.start` 算 |
| `local_runs_summary.json` | 四次跑的逐用例 (project, file, line, col, title, status, seconds) + 逐 project 汇总 + **集合比对**：B1 ∪ B2 == `list_shard1.txt`、C == `list_shard2.txt`、B ∪ C == `list_all.txt`、B ∩ C = ∅（主语 (project, file:line:col, title)，标题含 describe 前缀），四条全 true | 从 `--reporter=json` 的报告算（报告原件含本机绝对路径，不入库） |
| `local_runs/runA_shard1.*` | A：片 1 命令原样第一次跑，599.4s，87 passed / 1 failed / 1 did not run / 20 skipped——红的那条是**本机环境**（受限 PATH 下 `python3` 是系统 3.9，fixture 脚本的 `int \| None` 不支持），did not run 是它 serial 后面那条 | `node node_modules/@playwright/test/cli.js test --project=chromium --reporter=list,json`（PATH 只留 node，藏掉本机的 codex / claude） |
| `local_runs/runB_b1.*` / `runB_b2.*` | B：修好 `python3`（wrapper → worktree 的 `.venv/bin/python`）后，片 1 再跑一遍。**本机权宜**：工具单次前台调用上限 10 分钟而 A 已经 599s 贴着上限，所以再按 Playwright 自带 `--shard=1/2` / `2/2` 分成两次前台调用（CI 命令不带 `--shard`）；56 passed + 33 passed / 20 skipped，合计 601.8s，两次的集合并起来 == `list_shard1.txt` | 同上 + `--shard=K/2` |
| `local_runs/runC_shard2.*` | C：片 2 命令原样，320.1s，40 passed / 2 skipped（webkit 23 / 0、chromium-en 17 / 2） | `… test --project=webkit --project=chromium-en --reporter=list,json` |
| `ci_pr373_attempt1_hang.json` | **step 级超时的理由**：PR #373 run 34994534095 attempt 1 的 windows-exe-smoke / posix-e2e 的 job + steps（原样字段）；Playwright 步 16:30:58Z 起 `in_progress`，job 17:31:10Z `cancelled`，后面 3 步 + 6 个 Post 全 `pending`；日志 blob 404，run 的 artifact 里没有 attempt 1 的东西 | `gh api …/actions/runs/34994534095/attempts/1/jobs`、`…/jobs/104468499895/logs`（404）、`…/runs/34994534095/artifacts` |
| `ci_pr373_attempt2_windows_per_project.json` | 同一 run attempt 2 的 windows-exe-smoke（成功那次）的 list reporter 逐条 + 逐 project 汇总：chromium 90 行（89 + 1 重试）474.2s、webkit 23 / 156.2s、chromium-en 16 / 98.1s；1 flaky（asset-library 多-Figure 第一次 x 重试 ok）、23 skipped、127 passed (13.6m)；步 1004s | `gh run view 34994534095 --job 104492544345 --log`，正则解析（Windows 上无 TTY，标记是 `ok` / `x` / `-`，不是 ✓） |

日志里的本机绝对路径已替换成 `<worktree>` / `<tmp>` / `<home>` / `<scratch>`。
