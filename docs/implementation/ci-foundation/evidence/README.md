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

**不进仓库的东西**（在会话 scratchpad）：未裁剪的原始 API JSON（与裁剪版对拍过：`analyze` 输出逐字节相同）、
完整 pytest 日志（873 KB）、junit.xml（592 KB）、五个 CI job 的完整日志、变异反证脚本。
