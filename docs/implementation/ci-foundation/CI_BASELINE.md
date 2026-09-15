# CI00 · 基线测量报告

**只读 / 测量阶段。没有改任何 `.github/workflows/*.yml`、任何测试的覆盖或结论、任何 runner / ruleset / 凭据。**
机器可读版：[`CI_BASELINE.json`](CI_BASELINE.json)（由 `scripts/ci/ci_baseline.py summarize` 从
[`evidence/actions/timing_decomposition.json`](evidence/actions/timing_decomposition.json) 生成，手写段落经 `--extra` 并入）。
每个数字都能回指到 `evidence/` 里的一个文件；本文里的「中位」是 29 个 merge_group success 样本的中位数，
**不给 p95**（样本不够、也不同组）。

## 1. 事实边界

| 项 | 值 |
|---|---|
| 源码 | `8b95256c0d08a14bfcfc4c81358894ef01168933`（worktree `/Volumes/Projects/tavotto-wt/ci-foundation`，分支 `ci/ci00-baseline`，`git rev-parse HEAD` 与 origin/main 一致） |
| Actions 样本窗口 | ci.yml 最近 200 个 run：`2026-09-12T04:37:55Z` → `2026-09-15T13:48:06Z`（`total_count` 1455，只取两页） |
| 分析的 run | 86 个（33 merge_group + 51 pull_request + 2 个 attempt 1 的历史）——见 `CI_BASELINE.json` 的 `sample_selection.runs` |
| 本机 pytest | macOS 26 / Apple Silicon（12 核）/ Python 3.13.11 / matplotlib 3.11.2；**不是 CI 的 ubuntu-latest**，只作分布与分片依据，绝对时长不可比 |
| CI 对照 | run `34970490865` 三条 pytest 腿的日志（Linux 3.10 总时长；macOS / Windows 3.13 的 `--durations=50`）与两条 Playwright 腿的逐用例时长 |
| 没做的事 | 没 SSH、没查 hypervisor、没跑 e2e 全量、没取 nightly / lab / release 的 run、没改 ci.yml 里与实测不符的注释 |

## 2. 采集方法与口径

- 列表：`gh api "repos/Tavotto/Tavotto/actions/workflows/ci.yml/runs?per_page=100&page=N"`（N=1,2）。
- jobs：`gh api --paginate "…/actions/runs/{id}/jobs?filter=all&per_page=100"`；attempt>1 的 run 另取
  `…/attempts/1/jobs` 与 `…/attempts/1`。**`filter=all` 会把两次 attempt 的 job 一起回（44 条而不是 22 条）**，
  分解时按 `run_attempt` 过滤。
- 口径（`04_BUILD_TEST_CACHE.md` §6，实现见 [`scripts/ci/ci_baseline.py`](../../../scripts/ci/ci_baseline.py)）：
  - `dependency_wait` = 该 job 全部 `needs` 里**最晚**的 `completed_at` − 本次 attempt 起点 t0（`run_started_at`；attempt 1 时 = `created_at`）；没有 `needs` 记 0。
  - `runner_wait` = `job.created_at → job.started_at`。GitHub 在 `needs` 满足后才创建下游 job，所以它只含调度与领取。
  - `dispatch_gap` = needs 全完成（或 t0）→ `job.created_at`：GitHub 侧把 job 排进队列前的间隙（正常 0–1s；run 级排队时会变大，见 §4.3）。
  - `execution` = 按 step 分类相加（checkout / setup / install / test / build / artifact / overhead / post）。
  - `feedback` = t0 → `CI fast gate` 的 `completed_at`；`qualification` = t0 → `CI integration gate` 的 `completed_at`。**没有用 `updated_at`。**
  - job 的四种状态：`executed` / `skipped` / `never_started`（取消前没领到 runner：`steps` 为空、无 runner_name——它的「时长」是排队，不算执行）/ `carried_over`（re-run failed jobs 时被抄进新 attempt 的旧结果，`started_at` 早于 t0）。
- 进仓库的 JSON 经 `ci_baseline.py trim` 裁掉 URL / node_id 等字段，**时刻与结论一个不裁**；裁剪前后 `analyze` 输出逐字节相同（本地对拍）。裁剪表就是脚本里的 `RUN_FIELDS / JOB_FIELDS / STEP_FIELDS`。

## 3. 事件与结论分布（`evidence/actions/ci_runs_list.json`）

| 事件 / 结论 | 200 个 | 最近 100 个（09-14 12:45 → 09-15 13:48） |
|---|---:|---:|
| merge_group / success | 29 | 8 |
| merge_group / cancelled | 4 | 0 |
| merge_group / failure | 0 | 0 |
| pull_request / success | 72 | 43 |
| pull_request / failure | 10 | 5 |
| pull_request / cancelled | 57 | 35 |
| pull_request / in_progress | 1 | 1 |
| push / success | 27 | 8 |
| `run_attempt` > 1 | 2（都是 pull_request，`34762723238` / `34750304846`，attempt 2 都成功） | 0 |

pull_request 的 cancelled 主要是同一 PR 新 push 取消旧 SHA（concurrency 组只对 PR 开 cancel）；
2026-09-15 13:46–13:48 一次同时推 6 个 stacked PR 的 batch 全部被后续 push 取消。
4 个 merge_group cancelled 都在 2026-09-13：两次是 PR #336 被队列踢出（codeql.yml L76-78 记的 SARIF 上传失败，
CI run 死在 `pytest` 步上，`34748975776` / `34749514655`）；两次是 PR #340 的 `backend-platforms (windows)` 跑到 45:00
被当时的 `timeout-minutes: 45` 取消（`34759564871` / `34764431359`，ci.yml L422-425 记了这件事，上限后来抬到 60）。

## 4. 时间分解

### 4.1 任务包样本 run `34970490865`（merge_group，attempt 1，24 jobs；`evidence/actions/jobs/jobs_34970490865.raw.json`）

复算与 `evidence/historical_run_sample.json` 的三条 job **逐字一致**：backend-fast 3.10 job 2117s（install 16 / pytest 2096）、
frontend 253s（pnpm install 4 / test 202 / build 22）、windows-exe-smoke 1481s（Playwright 步 1124）。

| job | 结论 | dependency_wait | dispatch_gap | runner_wait | job_seconds | execution（秒，按类） |
|---|---|---:|---:|---:|---:|---|
| Contributor licence (CLA) | success | 0 | 1 | 2 | 3 | test 1 |
| Python quality (Ruff) | success | 0 | 1 | 2 | 14 | checkout 3 / install 4 / test 2 |
| backend-fast (ubuntu-latest, 3.10) | success | 0 | 1 | 2 | 2117 | checkout 2 / install 16 / **test 2096** |
| backend-fast (ubuntu-latest, 3.13) | success | 0 | 1 | 2 | 2049 | install 19 / test 2022 |
| backend-fast (ubuntu-latest, 3.14) | success | 0 | 1 | 4 | 2113 | install 24 / test 2081 |
| backend-platforms (macos-latest, 3.13) | success | 0 | 1 | 9 | 1509 | install 22 / test 1474 |
| backend-platforms (windows-latest, 3.13) | success | 0 | 1 | 2 | **2507** | install 45 / **test 2448** |
| compat-smoke | success | 0 | 1 | 2 | 145 | install 58 / test 80 |
| desktop-shell (ubuntu-latest) | success | 0 | 1 | 2 | 205 | install 34 / test 148 / post 14 |
| desktop-shell (macos-latest) | success | 0 | 1 | 8 | 245 | test 196 / post 38 |
| frontend | success | 0 | 1 | 2 | 253 | install 4 / test 207 / build 27 / artifact 1 |
| invariants | success | 0 | 1 | 2 | 492 | install 23 / test 463 |
| workerd | success | 0 | 1 | 2 | 24 | test 16 |
| main landing audit | skipped | — | — | — | — | push 才跑 |
| plugin-candidate | success | 256 | 0 | 4 | 55 | install 24 / setup 8 / test 9 / artifact 2 |
| CI fast gate | success | **2120** | 1 | 2 | 6 | — |
| package ×4 | success | 2120 | 1 | 2–9 | 64–124 | build 37–67 / test 13–48 |
| macos-app-smoke | success | 2120 | 1 | 9 | 328 | build 157 / test 120 |
| posix-e2e | success | 2120 | 1 | 2 | 491 | build 24 / test 447 |
| windows-exe-smoke | success | **2120** | 1 | 2 | **1481** | setup 37 / build 205 / install 35 / **test 1179**（Playwright 1124）/ post 12 |
| CI integration gate | success | **3604** | 0 | 3 | 8 | — |

feedback = **2129s（35.5 分钟）**，qualification = **3615s（60.3 分钟）**。
关键路径：`backend-fast (3.10)` 2117s → `windows-exe-smoke` 1481s → `CI integration gate`。
runner 分钟：ubuntu 135.2 / windows 68.4 / macos 36.8（`runner_minutes_by_label`）。

**Windows 那 35 分钟的等待是 DAG 等待（`dependency_wait` 2120s），不是 runner 排队（`runner_wait` 2s）。**
样本里托管 runner 领取几乎不排队：merge_group 上 ubuntu 中位 2s / macOS 9s / Windows 3s（§4.3 有例外）。

### 4.2 29 个 merge_group success 的分组统计（`CI_BASELINE.json` → `timing.groups["merge_group/success"]`）

| 指标 | n | min | 中位 | max |
|---|---:|---:|---:|---:|
| feedback（→ CI fast gate） | 29 | 1474 | **1964**（32.7 分钟） | 2710 |
| qualification（→ CI integration gate） | 29 | 2714 | **3412**（56.9 分钟） | 4064（67.7 分钟） |
| 关键路径 = backend-fast → windows-exe-smoke → gate | 28/29 | | | |
| 关键路径 = backend-platforms (windows) → gate | 1/29（`34825627073`） | | | |

每个 job（executed 的）：

| job | n | job_seconds 中位 | min–max | dependency_wait 中位 | runner_wait 中位 | runner_wait max | test 中位 | build 中位 | install 中位 |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| backend-platforms (windows-latest, 3.13) | 29 | **2443** | 1791–2868 | 0 | 3 | 116 | 2378 | — | 48 |
| backend-fast (ubuntu-latest, 3.14) | 9 | 2090 | 1420–2161 | 0 | 3 | 131 | 2057 | — | 23 |
| backend-fast (ubuntu-latest, 3.10) | 29 | **1865** | 1240–2152 | 0 | 2 | 121 | 1841 | — | 19 |
| backend-fast (ubuntu-latest, 3.13) | 29 | 1745 | 1256–2082 | 0 | 3 | 191 | 1720 | — | 20 |
| backend-platforms (macos-latest, 3.13) | 29 | 1485 | 1115–1761 | 0 | 9 | 251 | 1453 | — | 17 |
| windows-exe-smoke | 29 | **1411** | 1193–1605 | **1931** | 2 | 411 | 1168 | 175 | 26 |
| posix-e2e | 29 | 541 | 460–604 | 1931 | 2 | 462 | 487 | 30 | 13 |
| invariants | 29 | 501 | 375–540 | 0 | 2 | 231 | 473 | — | 23 |
| macos-app-smoke | 29 | 325 | 178–380 | 1931 | 8 | 253 | 111 | 157 | 12 |
| frontend | 29 | 252 | 160–272 | 0 | 3 | 157 | 207 | 25 | 5 |
| desktop-shell (ubuntu-latest) | 29 | 238 | 67–269 | 0 | 3 | 267 | 186 | 0 | 30 |
| desktop-shell (macos-latest) | 29 | 212 | 58–280 | 0 | 9 | 238 | 171 | 0 | — |
| compat-smoke | 29 | 132 | 85–145 | 0 | 2 | 260 | 93 | — | 32 |
| package (windows-latest[, 3.13]) | 20 + 9 | 110 / 116 | 101–207 | 1846 / 2099 | 3 | 215 | 13 | 60 | 13–15 |
| package (macos-latest[, 3.13]) | 20 + 9 | 108 / 102 | 90–124 | 1846 / 2099 | 8–9 | 128 | 48 | 39–42 | 5 |
| package (ubuntu-latest[, 3.13 / 3.14]) | 20 + 9 + 9 | 66 / 68 / 67 | 56–76 | 1846 / 2099 | 2 | 202 | 13 | 36–39 | 6–7 |
| plugin-candidate | 29 | 46 | 40–57 | 264 | 3 | 127 | 9 | — | 23 |
| workerd | 29 | 23 | 11–43 | 0 | 3 | 305 | 13 | — | — |
| Python quality (Ruff) | 29 | 12 | 8–19 | 0 | 3 | 148 | 2 | — | 4 |
| CI fast gate | 29 | 7 | 5–9 | 1931 | 2 | 458 | 1 | — | — |
| CI integration gate | 29 | 7 | 5–10 | 3402 | 2 | 4 | 1 | — | — |
| Contributor licence (CLA) | 29 | 4 | 3–7 | 0 | 2 | 271 | 1 | — | — |

（`package` 在 2026-09-14 12:10 之前显示名没有 Python 档位，是同一个 job 的两段命名；3.14 腿 09-14 起才有，n=9。）
runner 分钟（每个合并组）：ubuntu 中位 92.7（max 136.6）/ windows 66.2 / macos 35.3。

### 4.3 pull_request（`timing.groups["pull_request/*"]`）

| 组 | n | feedback 中位 | min | max | 说明 |
|---|---:|---:|---:|---:|---|
| plain PR / success | 36 | **2681（44.7 分钟）** | 1650 | 5018 | integration gate deferred，与 fast gate 同时刻 |
| full-ci PR / success | 7 | 2146 | 2034 | 4517 | qualification 中位 3534 |
| full-ci PR / failure | 5 | 2126 | | | 含两次 windows-exe-smoke 的 Playwright 步红、重跑即过（attempt 1 → 2） |
| plain PR / failure | 2 | | | | `34948407613` backend-fast 3.10 红（2464s 才知道）；`34966709766` frontend 在 `pnpm install --frozen-lockfile` 15s 就红 |

plain PR 的 36 个样本里，最后完成的快线 job **36/36 是某条 `backend-fast` 腿**（3.10 ×13、3.14 ×13、3.13 ×10）；
`backend-fast` 自身中位 ~2000s，feedback 中位却 2681s——差的 ~10 分钟是 **runner 领取等待**：
pull_request 事件上 ubuntu-latest 的 `runner_wait` 中位 331s，max **3050s**（51 分钟，`34949318428`）；
2026-09-15 02:28 / 06:04 / 07:39 / 08:41 / 08:52 / 11:23 / 13:46 六次「几秒内推 5–6 个 stacked PR」都触发了这种排队
（每个 PR 13 个 job，六个就是 ~78 个并发 job）。`34977366199`（13:48 那批）里 CLA 这种没有 needs 的 job 在 run 创建 16 分钟后
才被创建（`dispatch_gap` 961s），`invariants` 领 runner 等了 1681s。**这是容量问题，与 DAG 无关；merge_group 上没有出现
（它被队列串行化了）。** 账户的并发 job 上限 API 不给，是未知项。

### 4.4 attempt 2（`34762723238` / `34750304846`）

re-run failed jobs 只重跑了 `windows-exe-smoke` + `CI integration gate`（其余 19 个 job 是 `carried_over`，`created_at` 是重跑时刻但
`started_at` 还是上一次的）；从 `run_started_at` 到 gate 完成 1447s / 1461s；feedback 报 `carried_over` 而不是一个数。

## 5. DAG（`evidence/dag.json`，分类与证据行在 `evidence/dag_edge_kinds.json`）

17 个 job、23 条 `needs` 边。按事件：快线 9 个 job 在 `pull_request || merge_group`；重型 5 个在 `merge_group || (pull_request && full-ci 标签)`；
两个 Gate `always() && (…)`；`main-landing-audit` 只在 push。`timeout-minutes`：python-lint / cla 10、invariants 20、backend-fast 40、
backend-platforms 60、compat 20、frontend 20、plugin-candidate 20、workerd 15、desktop-shell 20、package 60、windows-exe-smoke 60、
macos-app-smoke 60、posix-e2e 45、Gate 10、landing 15。matrix：backend-fast ubuntu × {3.10, 3.13, 3.14}；backend-platforms {macos, windows} × 3.13；
desktop-shell {ubuntu, macos}；package {ubuntu 3.13, ubuntu 3.14, macos 3.13, windows 3.13}。

| 边 | kind | 证据行（ci.yml @ 8b95256c） |
|---|---|---|
| frontend → plugin-candidate | **artifact/data** | L556-563 上传 `codex-plugin-candidate` → L611-614 `download-artifact` 同名 → L620-623 解包并 `verify --content-digest` |
| backend-fast → package | verdict-only | L784 needs；L799-849 无 download-artifact；前端自建 L806-807（`build_frontend.py` 内部跑 `pnpm install` + `i18n:check` + `pnpm build`） |
| frontend → package | verdict-only | 同上，L806-807 自建前端，不 download frontend 的任何产物 |
| backend-fast → windows-exe-smoke | verdict-only | L874 needs；L878-1103 无 download-artifact；前端 L889-890、画布 L897、workerd L907、runtime L922、PyInstaller L934 全部现建 |
| frontend → windows-exe-smoke | verdict-only | L889-890 自建前端；L1099 自己 `pnpm install` |
| backend-fast → macos-app-smoke | verdict-only | L1190 needs；L1194-1306 无 download-artifact；L1205-1206 / L1214 / L1226 / L1237 现建 |
| frontend → macos-app-smoke | verdict-only | L1205-1206 自建前端 |
| backend-fast → posix-e2e | verdict-only | L1346 needs；L1350-1388 无 download-artifact；L1369-1371 / L1378 现建；L1330-1334 自述「不打包」 |
| frontend → posix-e2e | verdict-only | L1369-1371 自建前端；L1386 `pnpm install` |
| 9 条 * → CI fast gate | verdict-only（Gate 边，必须保留） | L1418 needs 与 L1455 `--required` 同一闭集；L1448 `NEEDS_JSON: ${{ toJSON(needs) }}` 是判定器唯一输入 |
| 5 条 * → CI integration gate | verdict-only（Gate 边，必须保留） | L1461 / L1503 / L1487 |

`plugin-candidate` **真的** download `codex-plugin-candidate`（L611-614），是全图唯一的数据边。
8 条 backend-fast / frontend → 重型 的边**没有一条消费字节**。

**删掉重型 job 对 backend-fast 的边之后，最终 AND 由谁保证：**
`ci-integration-gate` 的 `needs` 本来就不含 `backend-fast`（L1461）。合并资格 = ruleset 三个 required context
（`scripts/ci/merge_queue_ruleset.py` L63 `GATE_CONTEXTS`）∧ `CI fast gate` 的 `--required` 闭集（L1455，含 backend-fast、frontend）
∧ `CI integration gate` 的闭集（L1503）。`aggregate_gate.py` 的**实际输入源是 `--needs-json "$NEEDS_JSON"`，即 `toJSON(needs)`**
（L1448 / L1487），不读 API；判定器本体取自默认分支（L1432-1445）。所以删那 8 条边不会让任何一个 job 掉出 AND——
只要 Gate 的 `needs` / `--required` 闭集不动（`tests/test_merge_queue_workflows.py::TestGates::test_fast_gate_needs_matches_required_closed_set` 看住）。

### 5.1 关键路径与最值得改的三项

模型（`model_without_backend_fast_edges`，**不是测量**）：删掉 backend-fast → {package, windows-exe-smoke, macos-app-smoke, posix-e2e}
四条边、其它一切不变（每个 job 的 `job_seconds` / `runner_wait` / `dispatch_gap` 与实测相同），29 个 merge_group 的
qualification 中位 **3412s → 2476s（Δ 中位 −892s = −15 分钟；范围 −1969…0）**。之后关键路径变成 `backend-platforms (windows)`（中位 2443s）。

1. **删 backend-fast → 重型 的四条 verdict-only 边（CI01）。** 28/29 的关键路径经此；AND 不变；改动是 ci.yml 四行 `needs` +
   `tests/test_merge_queue_workflows.py` 的判据。代价：backend-fast 红时重型 job 白跑一轮（约 66 Windows 分钟 + 35 macOS 分钟）。
2. **`backend-platforms (windows)` 的全量 pytest（CI03）。** 删边后它是关键路径（中位 40.7 分钟，60 上限余量 12 分钟）；
   前 50 条用例占 35%，`test_invariants_engine` 的三条 `InvMix` 合计 232s。
3. **`backend-fast` 三档 Linux 全量 pytest（CI03）+ PR 突发时的领取等待。** plain PR 反馈中位 44.7 分钟 = backend-fast ~33 分钟 +
   排队 ~10 分钟；前者靠分片，后者是账户并发 / 容量（CI04 或调整 stacked PR 的推送节奏）。

## 6. pytest 计时（本机 macOS 样本 + CI 对照）

本机（`evidence/pytest/local_run.json`）：`.venv/bin/python -m pytest --durations=0 -rA --junitxml=… -o junit_logging=no`，
默认档（`pytest.ini` addopts `-q -m "not slow" --strict-config`），无 `-n`，未改 seed，跑的期间树没动。
**4557 passed / 42 skipped / 2 deselected，1153.56s（19:13），退出码 0**。`--durations=0` 打印了 2274 个 ≥5ms 的阶段，
合计 1129.2s（call 1024.8 / setup 53.5 / teardown 51.0）；1839 条用例的单条总时长中位 0.05s、p90 1.47s、p99 6.43s、max 38.42s；
≥5s 的 38 条合计 333.6s；<0.1s 的 992 条合计 18.7s。

| CI 腿（run 34970490865） | passed / skipped | 总时长 | matplotlib |
|---|---|---:|---|
| Linux 3.10 backend-fast | 4540 / 59 | 2095.02s（34:55） | 3.10.9（未钉版本） |
| macOS 3.13 backend-platforms | 4554 / 45 | 1472.57s（24:32） | 3.11.2 |
| Windows 3.13 backend-platforms | 4522 / 77 | 2446.41s（40:46） | 3.11.2 |
| 本机 macOS arm64 | 4557 / 42 | 1153.56s（19:13） | 3.11.2 |

最慢文件（本机，`evidence/pytest/durations_by_file.csv`，setup+call+teardown）：

| 文件 | 秒 | call 条数 |
|---|---:|---:|
| tests/test_invariants_engine.py | 182.2 | 58 |
| tests/test_equivalence_matrix.py | 100.0 | 29 |
| tests/test_manifest_geometry.py | 66.7 | 35 |
| tests/test_project_env.py | 55.6 | 29 |
| tests/test_worker_roundtrip.py | 52.8 | 68 |
| tests/test_workerd_client.py | 46.7（teardown 35.1） | 11 |
| tests/test_plugin_publish.py | 46.6 | 28 |
| tests/test_colorbar_orientation.py | 38.4 | 33 |
| tests/test_dependency_repair_e2e.py | 36.7 | 10 |
| tests/test_mcp_normalize.py | 35.9 | 17 |

前 25 个文件 ≈ 900s / 1129s；146 个文件按文件分 2 片理论上可平衡到 ~565s/片（本机）。
最慢 30 条单阶段见 `evidence/pytest/slowest_30.txt`；CI Windows 的前 50 条见 `evidence/ci_logs/durations_backend-platforms_windows_job104385304695.txt`
（108.41 / 65.09 / 59.04s 三条 InvMix，`test_preview_hybrid` 的 setup 28.13s，`test_dependency_repair` 20.19s……）。
两侧排名一致：`test_invariants_engine[InvMix]` 三条、`test_equivalence_matrix::test_write_back_then_reopen_*`、
`test_dependency_repair_e2e`、`test_document_persistence` 的两条 10s 并发用例、`test_thread_leak_gate`。

本机 42 个 skip 的分类（`evidence/pytest/skips.txt`）：缺 `tavotto-workerd` 产物 12、缺画布 / 插件候选产物 10（CI 在
`plugin-candidate` job 上必跑）、缺内置 runtime 产物 4、缺 wheel 产物 3、缺 NSIS 中间脚本 2、缺 updater 探针 1、
非 Linux 无 /proc 1、本机环境特有 5（装着桌面版 / 解释器带 matplotlib，其中两条在 CI 也 skip）、需网络或真实 CLI 的 opt-in 3、
协议已脱离草案 1。**CI 侧 skip 理由拿不到**（三条腿都没带 `-rs`）。缺 runtime（4）与缺 wheel（3）的用例在 ci.yml 里
没有任何「先建产物再跑 pytest」的 job，它们在 CI 里的执行位置待确认。

## 7. 必须串行 / 隔离的候选（静态扫描，`evidence/pytest/serial_candidates_grep.txt`）

| 类别 | 文件 | 理由 | 定性 |
|---|---|---|---|
| 显式 `os.environ` 写入（不经 monkeypatch） | test_ci_qualification（TAVOTTO_DATA_DIR）、test_equivalence_matrix / test_worker_roundtrip（TAVOTTO_WORKERD）、test_issue181_large_preview（MESH_N）、native/test_run_cli_integration（TEST_MARKER）、conftest（NO_TELEMETRY / DATA_DIR / WORKERD） | 进程级状态；都是 save/restore 形状 | 同进程内串行即可（pytest 本来就串行）；跨 job 分片无影响；xdist 各 worker 独立进程也无影响 |
| `chdir` | test_compat_capture_parity、test_mcp_roots、test_mcp_server、test_merge_queue_ruleset、test_project_env | 进程级 cwd（monkeypatch.chdir） | 同上 |
| module 级 fixture | test_invariants_engine、test_equivalence_matrix 系、test_preview_*、test_colorbar_orientation 等 29 处 | 文件内共享 worker / figure 状态 | 分片时**不要把同一文件拆开**（按文件分或 xdist `--dist loadfile`） |
| 真子进程 | test_windows_regressions（16 处）、test_codex_plugin（13）、test_plugin_stage（6）、bridge/test_bridge_injection_models（6）、test_independent_frontend_prs（4，真跑 vite）、test_project_env / test_package_management / test_dependency_repair_e2e（真建 venv + pip） | 内存与 CPU 叠加；worker 子进程本身也是科学栈 | 有界并行（04 §2：不许 `-n auto`）；分片要记每片的子进程峰值——**待实测** |
| 依赖真实产物 | test_plugin_candidate / test_mcp_stdio / test_mcp_server（画布）、test_worker_roundtrip / test_equivalence_matrix（workerd）、test_runtime_build（runtime）、test_tutorial（wheel）、test_nsis_template、test_update_chain_gates | 干净 checkout 上 skip | 只能进有对应前提的消费者（plugin-candidate 已是）；runtime / wheel 两组的执行位置待确认 |
| 固定端口 | 无真实 bind 到固定端口（全部 `bind(("127.0.0.1", 0))`）；`package` job 的 5199 + `/tmp/smoke` + `sleep 8` 在 workflow 里不在 pytest 里 | | 同一台机器多 runner 时 `package` 要改按实例隔离（04 §4） |
| 字体 | test_cjk_figure_text、test_font_*、test_glyph_*、test_typography_families、compat 的 cjk case | 缺字体时各自 skip / 退回 | 分片不受影响；新 runner 镜像要装字体 |
| git 状态 | test_legal_contribution_policy、test_blame_ignore_revs（浅克隆 skip）、test_generated_untracked、test_source_hygiene 等 | 只读 | 分片不受影响；`plugin-candidate` 要 `fetch-depth: 0` |
| HOME | test_ai_capabilities / test_ai_history / test_diagnostics_bundle / test_telemetry | `expanduser` 只用于断言「不泄漏真实 HOME」 | 分片不受影响 |
| 顺序依赖（靠前一个用例暖缓存 / 建文件才绿） | 静态扫不出 | | **待确认**：CI03 前用随机顺序或真分片跑一次 |

## 8. Playwright 权重

`web/playwright.config.ts`：`workers: 1`、`fullyParallel: false`、`retries: CI ? 1 : 0`、用例超时 180s、expect 15s、
无共享 webServer（每个用例由 `e2e/fixtures.ts` 各自拉起后端，端口与临时目录按用例分配）。
`npx playwright test --list`（本机，不运行，`evidence/playwright/list.txt`）：**151 条 / 24 个 spec 文件**——
chromium 109、webkit 23、chromium-en 19。

CI 逐用例时长（`list` reporter 打印，`evidence/ci_logs/playwright_*.json`）：

| 腿 | 报告 | 每 project（条 / 秒） | 最重的文件（秒） |
|---|---|---|---|
| windows-exe-smoke（job 104397475552） | 128 passed, 23 skipped，**15.0m**；步骤 1124s（多出的 ~3.7 分钟是 `pnpm install` + `playwright install --with-deps chromium webkit`） | chromium 89 / 505.5；webkit 23 / 266.9；chromium-en 16 / 110.6 | webkit/keyboard-golden-path 108.7（含一条 90.0s）、webkit/a11y 89.2、chromium-en/a11y 59.2、chromium/a11y 55.3、webkit/golden-paths 45.0、chromium/tutorial 43.7 |
| posix-e2e（job 104397475709） | 106 passed, 22 skipped，**7.1m**；步骤 447s | chromium 89 / 350.2；chromium-en 17 / 64.6 | chromium/a11y 40.5、chromium-en/a11y 40.0、chromium/i18n 31.7、chromium/tutorial 26.1 |

23 / 22 个 skip：两腿都跳 20 条 `playground*.spec.ts`；`error-recovery-en` 在 Windows 跳 3 条（2 条 POSIX 权限位用例只在 posix 腿真跑 + 1 条「无可用渲染环境」两腿都跳），在 posix 腿跳 2 条（「无可用渲染环境」+ `file_locked`）。
用例时长含后端冷启动，所以 sum(用例) ≈ 报告总时长，文件级分片的收益能按上表直接估。

## 9. 覆盖账摘要（[`coverage_ledger.json`](coverage_ledger.json)，`status` 全部 `not_run`）

28 条：快线 12（python-lint、cla-check、invariants、backend-fast ×3、frontend、plugin-candidate、workerd、desktop-shell ×2、compat-smoke）、
CodeQL 1、重型 9（backend-platforms ×2、package ×4、windows-exe-smoke、macos-app-smoke、posix-e2e）、Gate 2、
合并后 / 发行 3（main-landing-audit、nightly、lab-qualification）+ release 1。每条有旧位置（workflow / job / 事件 / runs-on / 上限 / 行号）、
所需产物、资源类别、串行理由、实测中位与证据、拟新位置（本阶段除「保持不变」外一律「待 CI01/03 决定」）。
**`-m slow` 只在 lab 的 `_lab-qualification.yml` L131-159 执行**；CompatBench 全量 + 保真度只在 lab nightly 档；
真安装链只在 nightly.yml。

## 10. 管理员待填表（[`evidence/admin_inventory.json`](evidence/admin_inventory.json)，`status: not_run`）

没有 SSH / hypervisor 访问，没登录任何机器。已记录的假设（全部来自仓库文档）：标签 `[self-hosted, linux, x64, tavotto-lab]`；
「带该标签的 runner 只有一台」（`_lab-qualification.yml` L70-74）；每 runner 并发 1；state root `/srv/tavotto-ci`（cache / locks / upgrade /
baselines / reports / tmp）；互斥 = concurrency 组 `lab-qualification-${{ github.workflow }}` + runner 端 flock；preflight 门槛 4C / 8G / 20–60G、
FD ≥ 4096；推荐 16 vCPU / 32 GiB / 150 GiB SSD 的独立 VM；`github.com` 443 不可达、checkout 走 `ssh.github.com:443`、tool cache 预置。
需要管理员回答的 11 项：16/32 是 host 还是 guest、hypervisor、其它 VM、可分配 vCPU、真实 RAM、磁盘、网络、runner 注册层级 / runner group、
lab VM 现状、VM 生命周期工具、runner 用户名（文档 `github-runner` vs memory `runner`）。

## 11. H1–H4 裁决

| 假设 | 裁决 | 依据 |
|---|---|---|
| H1 删非必要 DAG 边可重叠 backend 与安装验证而不丢 AND | **证据支持** | 8 条边 verdict-only（§5）；28/29 关键路径经此；模型 Δ 中位 −892s；AND 由 fast gate 闭集 + ruleset 三 context 保证，判定器输入是 `toJSON(needs)` |
| H2 pytest 可平衡分片，不可隔离的留 serial 集 | **证据支持（本机）/ CI 侧证据不足** | 146 文件、最大单文件 16%；无固定端口、不写源码树；CI 只有前 50 条 durations；顺序依赖待实测 |
| H3 Windows E2E 文件级分片比堆 Linux runner 更贴近关键路径 | **部分支持，顺序在 H1 之后** | 关键路径确实经 windows-exe-smoke，但删边后关键路径先变成 backend-platforms (windows)；Linux 容量影响的是 PR 突发时的领取等待，不是 merge_group |
| H4 自托管只有条件齐备才有净收益 | **证据不足** | merge_group 上领取等待中位 2–9s（执行才是瓶颈）；PR 突发时 ubuntu 等待中位 331s / max 3050s 是并发上限问题；lab 网络限制未复测；资源全部待管理员填写；没有任何 A/B 样本 |

## 12. 与源码 / 文档不符的地方（记录，不改）

- ci.yml L388-390「backend-fast 实测 20–25 分钟，40 给余量」：实测中位 29–35 分钟，max 36，余量 4 分钟。
- ci.yml L493「frontend 实测 2.2 分钟」：中位 4.2（vitest 202s）。L732 desktop-shell「冷编译 37 秒」是本机数，CI 3.5–4 分钟。L460 compat 2.4 ✓、L682 workerd 0.3 ✓。
- ci.yml L422「Windows 29~41 分钟，套件 4460 条」：中位 40.7、max 47.8；套件 4601 条。
- `01_AUDIT.md` 与 `evidence/historical_run_sample.json` 的三条 job：复算逐字一致，无差异。
- `docs/ci/self-hosted-runner.md` runner 用户名 `github-runner` vs memory 里的 `runner`：待管理员确认。

## 13. 未知项

见 `CI_BASELINE.json` → `unknowns`：CI 侧逐用例 durations 与 skip 理由；账户并发 job 上限；windows-exe-smoke 两次偶发红的根因；
`test_runtime_build` / `test_tutorial` 依赖产物用例在 CI 的执行位置；lab 资源拓扑；删边后并发对领取等待的影响；backend-fast 未钉 matplotlib 的意图。

## 14. CI01–CI04 建议次序

1. **CI01**：删 backend-fast → 重型 的 4 条边（第一刀）。frontend → 重型 的 4 条二选一：留作短预筛（4 分钟，能挡住前端坏时的 Windows 白跑）或一并删；
   同一 PR 里给 `tests/test_merge_queue_workflows.py` 加「重型 job 不 needs backend-fast」「fast gate 闭集不变」的判据。预期（模型）合并资格中位 ~57 → ~41 分钟，CI05 用真实合并组对照。
2. **CI03**：backend-fast 与 backend-platforms 按文件分 2 片（并集 = `evidence/pytest/junit_testcases.csv` 那样的 nodeid 集合，分片选择器要拒空集 / 漏片）；先不动 Playwright。
3. **CI02**：暂不跨 job 抽取前端产物（build 22–40s，传输校验未必划算）；只把「四个重型 job 各自重跑 `build_frontend.py`」列为观察项。
4. **CI04**：先拿到 `admin_inventory.json` 的答案；PR 池只在证明账户并发上限是瓶颈之后才有净收益；现有 lab 不动。

## 15. 验证命令与退出码（2026-09-15，worktree）

| 命令 | 退出码 |
|---|---:|
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `.venv/bin/python -m pytest tests/test_ci_tooling.py tests/test_merge_queue_workflows.py tests/test_aggregate_gate.py tests/test_ci_baseline.py`（126 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」） | 0 |
| `.venv/bin/python -m pytest tests/test_docs_references.py tests/test_tracked_paths_are_windows_safe.py tests/test_source_hygiene.py tests/test_generated_untracked.py`（33 passed） | 0 |
| 逐个解析 `docs/implementation/ci-foundation/**/*.json`（198 个，含多页首尾相接的 `jobs_*.raw.json`） | 0 |
| `docs/implementation/ci-foundation/**/*.md` 内相对链接存在性检查（13 条，0 缺失） | 0 |
| 负例变异反证（10 条：空 jobs / total_count 不符 / 错 SHA / 缺 completed_at / 边未分类 / 空 evidence / carried_over / never_started / attempt 过滤 / dependency_wait 混成 runner_wait）：每条各让一条用例红 | 逐条退 1；还原后退 0 |

（详细的变异脚本与 pytest 全量日志在会话 scratchpad，不进仓库；全量 pytest 的摘要在 `evidence/pytest/local_run.json`。）

## 16. 证据索引

- `evidence/actions/ci_runs_list.json` / `codeql_runs_list.json`：run 列表（裁剪）
- `evidence/actions/runs/run_<id>[.attempt1].json` + `evidence/actions/jobs/jobs_<id>[.attempt1].raw.json`：86 个 run 的元数据与 jobs+steps（裁剪，`--paginate` 的页结构保留）
- `evidence/actions/codeql/`：3 个 merge_group 的 codeql.yml run
- `evidence/actions/timing_decomposition.json`：`ci_baseline.py analyze --compact` 的全部输出（每 job 四类时间、关键路径、模型）
- `evidence/dag.json` / `evidence/dag_edge_kinds.json`
- `evidence/ci_logs/`：CI 的 durations 段、总时长行、Playwright 逐用例时长
- `evidence/pytest/`：本机 run 元数据、durations、按文件汇总、最慢 30、junit 逐用例、skip、串行候选 grep
- `evidence/playwright/`：`--list` 输出与汇总
- `evidence/admin_inventory.json`
- `evidence/historical_run_sample.json`（任务包原件）

重跑方法：`python scripts/ci/ci_baseline.py fetch-runs --pages 2 --out DIR && … fetch-jobs --run-id … --out DIR && … trim --src DIR --dst evidence/actions && … analyze --compact --workflow .github/workflows/ci.yml --evidence evidence/actions --edge-kinds evidence/dag_edge_kinds.json --out evidence/actions/timing_decomposition.json && … summarize --analysis … --extra <手写段落> --out CI_BASELINE.json`。
