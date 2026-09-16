# evidence/ci05/ · CI05 前后对照、CI 侧分片完整性、回退演练的证据

全部产出于 2026-09-16，worktree `ci-foundation`，分支 `ci/ci05-rollout-handoff`（叠在 CI04 `a174eb61` 之上）。
解读在 [`../../CI05_COMPARISON.md`](../../CI05_COMPARISON.md)；交接在 [`../../CI_HANDOFF.md`](../../CI_HANDOFF.md)。
本目录没有任何本机跑出来的时长——**所有时长都是 GitHub Actions 的 run / jobs API 原样**（裁剪字段，不裁时刻）。

| 路径 | 内容 | 产出方式 |
|---|---|---|
| `actions/runs/run_<id>[.attempt1].json` + `actions/jobs/jobs_<id>[.attempt1].raw.json` | 17 个 run（10 个 after + 1 个 attempt 1 + 6 个同日 merge_group 对照）的元数据与 jobs+steps（裁剪：`ci_baseline.py trim`） | `python scripts/ci/ci_baseline.py fetch-jobs --run-id … --out <scratchpad>` → `trim --src <scratchpad> --dst actions` |
| `workflows/ci_<sha>.yml` | 六份 ci.yml 快照：`79c5aa38`（CI01）/ `d9e8dd72`（CI03a）/ `35b912a0`（CI03c）/ `1f7f13e8`（CI03b 修复前）/ `162f54c6`（CI03b）/ `bb27bdaf`（CI02；CI04 的 `a174eb61` 同一 blob）。`8b95256c` 那份用 `tests/fixtures/ci_baseline/ci_8b95256c.yml`。每个 PR 的 `refs/pull/N/merge` 上的 ci.yml 与 head 的 blob 相同（核过） | `git show <sha>:.github/workflows/ci.yml` |
| `analyze_all.sh` | 七组 `ci_baseline.py analyze --compact` 的驱动：每组一份 ci.yml 快照 + 对应的 run（用哪份 ci.yml 分解哪些 run 必须对应，CI01 §4 ⑤） | 手写 |
| `timing/timing_ci_<sha>.json` | 上面七组的输出：每 run 每 job 的 dependency_wait / dispatch_gap / runner_wait / job_seconds / execution 按类、关键路径、runner 分钟。**紧凑 JSON（单行），用 `python -m json.tool` 看** | `analyze_all.sh` |
| `compare.py` → `comparison.json`（紧凑单行 JSON，用 `python -m json.tool` 看） | before（CI00 29 个 merge_group 中位）与 after 逐 run 并排；`contended` 标记（关键路径上任一 job runner_wait ≥ 60s）；`qualification_model_zero_runner_wait`（runner_wait := 0 重放 DAG 的口径，不是测量）；感兴趣的 step 秒数（按 ci.yml `name:` 前缀取） | `python evidence/ci05/compare.py` |
| `shards/check_ci_shards.py` → `shards/ci_shard_check.json` | CI 侧分片完整性：run 35007730894 的 10 个 pytest artifact（五条腿 × 两片）的 junit (classname, name) 集合与 manifest 比；run 35011613925 / 35031790918 的 Playwright 自验 artifact 的 `--list` 集合比。解析器与产品侧的 `tests/support/shard.py` / `scripts/ci/playwright_shard_check.py` 刻意不同源 | `gh run download <id> -p 'pytest-*'` / `-p 'playwright-shard-check-*'` → 脚本 |
| `shards/nodeid_union.txt` | 五条腿共同的并集：4686 条 nodeid（与 `../ci03a/collect_full.txt` 逐条相同） | 同上 |
| `shards/manifests_35007730894/*.json` | 10 份 `shard-manifest.json` 原件（`git_head` = `fd2b2164`，那次 run 的 `refs/pull/374/merge`） | artifact 原件 |
| `shards/playwright_35011613925/` / `shards/playwright_35031790918/` | 两片的 `check.json`；35011613925 另存三份 `--list` 清单（两台机器的 `list_all.txt` 逐字节相同，只存一份） | artifact 原件 |
| `shards/negative_cases.txt` | 9 条负例（删一条 / 报告充数 / 重叠 / manifest 总数改小 / 同一 junit 重复 / Playwright 少一条 / ok=false / 清单抄另一片 / 认不出的行），先断言变异落地再看退出码 | 会话 scratchpad 里的 `neg_shards.py`（不进仓库） |
| `rollback/rollback_drill.py` → `rollback/rollback_drill.json` | 四条本机回退演练（CI03a / CI03c / CI01 / CI02）：一次性 `git worktree`（`a174eb61`）上按各 PR 文档的回退方式改 ci.yml → `actionlint` + 六个合同测试文件 → 记录退出码与红掉的用例 → 删 worktree；R0 = 未改的对照 | `python evidence/ci05/rollback/rollback_drill.py <out.json>`（脚本里的路径是本机 worktree 的绝对路径） |

**不进仓库的东西**（会话 scratchpad）：未裁剪的 API JSON、下载的 artifact 原件（junit.xml 每份 ~50 KB × 10）、负例驱动 `neg_shards.py`、
`ci_baseline.py` 分类规则的变异脚本。
