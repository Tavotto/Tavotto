# evidence/ · CI00 的原始证据

全部由 `scripts/ci/ci_baseline.py` 与本会话的一次性命令产出，日期 2026-09-15，源码 `8b95256c0d08a14bfcfc4c81358894ef01168933`。
数字的解读在上一级的 [`CI_BASELINE.md`](../CI_BASELINE.md)；这里只说每个文件是什么、怎么来的、裁了什么。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `historical_run_sample.json` | 任务包原件：run 34970490865 的三条 job | 任务包自带；CI00 复算一致（`CI_BASELINE.md` §4.1） |
| `actions/ci_runs_list.json` | ci.yml 最近 200 个 run 的列表（裁剪） | `gh api "…/actions/workflows/ci.yml/runs?per_page=100&page=1..2"`；只留 id / attempt / event / status / conclusion / sha / branch / created_at / run_started_at / updated_at / html_url / display_title / PR 号 |
| `actions/codeql_runs_list.json` | codeql.yml 最近 100 个 run（裁剪） | 同上 page=1 |
| `actions/runs/run_<id>[.attempt1].json` | 86 个 run 的元数据（裁剪：`RUN_FIELDS`） | `gh api …/actions/runs/<id>`（attempt 1 另取 `…/attempts/1`）→ `ci_baseline.py trim` |
| `actions/jobs/jobs_<id>[.attempt1].raw.json` | 对应的 jobs+steps（裁剪：`JOB_FIELDS` / `STEP_FIELDS`；`--paginate` 的页结构与 `total_count` 保留） | `gh api --paginate "…/actions/runs/<id>/jobs?filter=all&per_page=100"` → `trim` |
| `actions/codeql/` | 3 个 merge_group 的 codeql.yml run 与 jobs（裁剪） | 同上 |
| `actions/timing_decomposition.json` | 86 个 run 的四类时间分解、关键路径、runner 分钟、删边模型（`--compact`：不含 step 明细）。**紧凑 JSON（单行），用 `python -m json.tool` 看** | `ci_baseline.py analyze --compact --workflow .github/workflows/ci.yml --evidence docs/implementation/ci-foundation/evidence/actions --edge-kinds …/dag_edge_kinds.json`（`evidence_dir` 字段记的是这个相对路径） |
| `dag.json` | ci.yml 的 17 个 job、23 条边（含分类与证据行） | `ci_baseline.py dag --edge-kinds evidence/dag_edge_kinds.json` |
| `dag_edge_kinds.json` | 每条边的 kind + ci.yml 行号 | 人工读 ci.yml @ 8b95256c |
| `ci_baseline_extra_sections.json` | `CI_BASELINE.json` 里手写段落的原件 | 手写 + 从本目录的文件算出的计数；经 `summarize --extra` 并入。上一级的 `CI_BASELINE.json` 本身也是**紧凑 JSON（单行），用 `python -m json.tool` 看** |
| `ci_logs/durations_backend-platforms_{windows,macos}_job<id>.txt` | CI 的 `--durations=50` 段 + 总结行 | `gh api --allow-escape-sequences …/actions/jobs/<id>/logs`，截取 |
| `ci_logs/summary_backend-fast_linux310_job<id>.txt` | Linux 3.10 腿的 pip 安装行与 pytest 总结行 | 同上 |
| `ci_logs/playwright_{windows-exe-smoke,posix-e2e}_job<id>.json` | `list` reporter 的逐用例时长 + 其它行（skip 列表、总结） | 同上，正则解析 |
| `pytest/local_run.json` | 本机全量 pytest 的元数据（环境、命令、起止、总结、退出码） | 本机 `.venv` |
| `pytest/durations.json` | `--durations=0` 的 2274 行（<5ms 的阶段 pytest 不打印） | 解析日志 |
| `pytest/durations_by_file.csv` / `slowest_30.txt` | 按文件汇总 / 最慢 30 个阶段 | 从 durations.json 算 |
| `pytest/junit_testcases.csv` | 4599 条 testcase（classname / name / time / outcome）——本机的 nodeid 集合形状 | `--junitxml` 解析 |
| `pytest/skips.txt` | `-rA` 打印的 41 行 SKIPPED（42 条，一行 `[2]`） | 日志 |
| `pytest/serial_candidates_grep.txt` | 串行 / 隔离候选的静态扫描 | grep（命令写在文件头） |
| `playwright/list.txt` / `list_summary.json` | `npx playwright test --list`（本机，不运行）与按 project / 文件计数 | 本机 |
| `admin_inventory.json` | 管理员待填表（`not_run`）+ 文档里已记录的假设 | 只读文档；没登录任何机器 |
| `ci01/` | **CI01 的静态证据**（改后 DAG、边分类、机器比对、analyze 解析检查），见 [`ci01/README.md`](ci01/README.md)；本目录其余文件仍是 `8b95256c` 的事实，CI01 一个字节没动 | 2026-09-16 |
| `ci03a/` | **CI03a 的本机证据**（全量 vs 两片顺序 vs 两片并发的 junit / time / manifest、集合级比对、负例与变异反证、SIGINT 坑的双向验证），见 [`ci03a/README.md`](ci03a/README.md)；没有真实 CI run | 2026-09-16 |
| `ci03b/` | **CI03b 的本机证据**（真 wheel 装进干净 venv 后 `package_smoke.py` 一次通过 rc 0、`ready_seconds` 2.33；`occupy_then_run.py` 人为抢占端口的 `lease_lost` → 换号 → rc 0；两轮变异反证 24/27 → 28/28 与 ci.yml 合同 18/18，含变异驱动与两份清单），见 [`ci03b/README.md`](ci03b/README.md)；没有真实 CI run | 2026-09-16 |
| `ci03c/` | **CI03c 的证据**（三份 `--list`、自验输出、12 条负例、两组变异反证 19/19 + 18/18、本机四次真跑的日志 / 计时 / 集合比对、PR #373 attempt 1 挂 60 分钟的 job+steps 原样与 attempt 2 的逐 project 时长），见 [`ci03c/README.md`](ci03c/README.md)；没有真实的分片 CI run | 2026-09-16 |
| `ci02/` | **CI02 的证据**（两个已有 run 的逐步秒数、Playwright 安装步的三段拆分、仓库缓存 60 条清单与按作用域汇总、合并组 run 的缓存全 miss、recipe 表、本机五种产物的时长 / 尺寸、tsc 反证 T1–T4、已合入候选 ref 的缓存仍在、变异反证 23/23），见 [`ci02/README.md`](ci02/README.md)；没有新的 CI run（Windows 去 `--with-deps` 的实验由本 PR 的 full-ci run 判） | 2026-09-16 |
| `ci04/` | **CI04 的只读证据**（4 台仓库级 runner 的清单、30 个 lab run 的 runner 归属、`runs-on` 全表与事件 × runner 枚举、仓库 / org 的 Actions 设置、org 级端点 403 原文、变异反证 15/15 + 1 no-op、计划版预算），见 [`ci04/README.md`](ci04/README.md)；没有 SSH、没有改任何设置、没有新 runner | 2026-09-16 |

**不进仓库的东西**（在会话 scratchpad）：未裁剪的原始 API JSON（与裁剪版对拍过：`analyze` 输出逐字节相同）、
完整 pytest 日志（873 KB）、junit.xml（592 KB）、五个 CI job 的完整日志、变异反证脚本。
