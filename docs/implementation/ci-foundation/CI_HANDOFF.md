# CI 前置交接（CI05，2026-09-16）

按 [`templates/CI_HANDOFF.md`](templates/CI_HANDOFF.md) 与 [`06_UNIFIED_HANDOFF.md`](06_UNIFIED_HANDOFF.md)「必填」逐项填；每项指到文件 / run / commit。
四种状态**分开写**（05 §6）：**源测试通过**（本文大部分条目）/ **服务器部署**（没有：CI04 `not_run`）/ **代码合并**（没有：七个 PR 全部 open）/
**产品发布**（没有）。本任务不做后三种写操作。本文件校验成功 ≠ 产品 / runner 资格通过。

## 0. 两个状态（独立判）

- **`ci_hosted_ready`: pass**——按 05 §1 的定义逐条对：
  - 合理的 DAG：CI01 删四条 verdict-only 边，重型 job 的 `dependency_wait` 在 5 个真实 run 上从 1931s（before 中位）降到 191–254s（[`CI05_COMPARISON.md`](CI05_COMPARISON.md) §4.1）；
  - 分片 / 隔离：pytest 五条腿 × 2 片与 Playwright 2 片都在 CI 上跑过且 CI 侧集合级完整性成立（§9）；`package` 冒烟按实例隔离在 Linux / macOS / Windows 三种 runner 上各跑过（§4.5）；
  - Gate 与事件：三个 Gate 未改，且分片红 / 硬杀 → Gate 红在 CI 上真发生过三次（§10 第 1 / 9 条）；同 PR 新事件取消旧 run、别的 PR 不受影响有两对样本（§6.1）；
  - 合并前覆盖不缩小：[`coverage_ledger.json`](coverage_ledger.json) 28 条——24 条在 after run 上以新形状执行并 success（逐条引 run + job），4 条（push main / nightly / lab / release）本轮未动；
  - 真实前后样本：有，且样本不足处写明（每档 n = 1–2、争抢样本单列、不算 p95；§2）。

  **pass 的主语是「hosted 上的验证」**：全部证据来自七个 PR 的 `full-ci` run（`pull_request` 事件）。它们与 merge_group 同一套 job，差别是缓存作用域与争抢。
  **七个 PR 一个都没合入 main**——合入后用第一个 merge_group run 复核（§12 的命令），复核不过就把这一行改回 `fail`。
- **`runner_pool_ready`: not_run**——没有 VM、没有 runner、没有部署权限；CI04 只交付了只读清点 + 静态守卫 + 管理员操作表
  （[`CI04_RUNNER_PILOT.md`](CI04_RUNNER_PILOT.md) §5、[`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md)）。它不是 U00 的前置（06 末段）。

## 1. source_sha / workflow_policy_revision（当前源码 SHA / 策略版本）

- 源码基线 `8b95256c0d08a14bfcfc4c81358894ef01168933`（CI00 读的那份 main）。
- 本栈：#372 CI00 `37fb89a1` → #373 CI01 `79c5aa38` → #374 CI03a `e952fe14` → #375 CI03c `35b912a0` → #376 CI03b `162f54c6` → #377 CI02 `bb27bdaf` → #378 CI04 `a174eb61` → 本 PR（`ci/ci05-rollout-handoff`，叠在 `a174eb61` 上；第一个提交 `62cd5d8f`）。
- **策略版本 = ci.yml 的 blob**：本栈 HEAD 的 `.github/workflows/ci.yml` 与 `bb27bdaf` / `a174eb61` 同一 blob `2a58e856`（CI04 / CI05 都没动 yml）。
  逐版快照在 [`evidence/ci05/workflows/`](evidence/ci05/workflows/)；`coverage_ledger.json` 的 `policy_source_sha` = `a174eb6142f75bc40c94dfa5804f4b247a7ec45a`。
- 版本上 main 已前进（合并组对照 run 的 head 到 `07c9e634`），但那些 PR 没碰 ci.yml（blob 仍是 `b653fce5`）——本栈 rebase 到 main 时 yml 无冲突可预期，其余文件由用户合并时判。

## 2. CI 实际改动范围（实际读取、修改与未修改范围）

| 范围 | 改了什么 | 没改什么 |
|---|---|---|
| `.github/workflows/ci.yml` | CI01 四行 `needs`；CI03a 两个 job 的 matrix `shard` 轴 + pytest 命令 + 证据 artifact；CI03c `windows-exe-smoke` 的 `name:` / `strategy` / 三步拆分 / 自验 / step timeout ×2 / artifact 改名；CI03b `package` 两步 + 失败 artifact；CI02 一行（Windows 去 `--with-deps`） | 两个 Gate 的 `needs` / `--required` 闭集、`scripts/ci/aggregate_gate.py`、顶层 `concurrency`、所有 `if:`、job 级 `timeout-minutes`、job id |
| 其它 workflow | 无 | `codeql.yml` / `nightly.yml` / `lab-ci.yml` / `_lab-qualification.yml` / `release.yml` / `desktop-tauri.yml` / `plugin-stable.yml` 一个字节没动（CI04 §7 的 `git diff --stat 8b95256c..HEAD` 对 lab 五文件为空） |
| `.github/actionlint.yaml` | CI04 删掉预留标签 `tavotto-trusted` | — |
| 脚本 | 新：`scripts/ci/ci_baseline.py`（CI00；CI03c / CI05 补显示名映射与 step 分类）、`scripts/ci/playwright_shard_check.py`（CI03c）、`scripts/ci/package_smoke.py`（CI03b）；`tests/support/shard.py` + `shard_weights.json` + `tests/conftest.py` 两个钩子（CI03a） | 任何产品源码（`src/`、`web/src`、`workerd/`、`src-tauri/`）、`web/tsconfig*.json`、构建脚本、`pyproject.toml` |
| 测试 | `tests/test_merge_queue_workflows.py` 新增五组合同（HeavyLaneDependencies / PlaywrightShards / PackageSmokeIsolation / BuildReuseAndCaches / RunnerTrustZones）；新文件 `test_ci_baseline.py` / `test_pytest_shard.py` / `test_playwright_shard_check.py` / `test_package_smoke.py` | 没有放宽任何产品断言，没有改任何 skip 条件，没有 `-n auto` |
| 仓库设置 | 无（ruleset / runner / 凭据 / 标签都没动） | required contexts 仍只有三个 Gate 名字（`scripts/ci/merge_queue_ruleset.py::GATE_CONTEXTS`） |
| 文档 | `docs/implementation/ci-foundation/`（本包）、`.github/AGENTS.md` 加五段（CI03a / CI03c / CI03b / CI02 / CI04） | — |

## 3. 稳定 Gate 与其输入来源（工作流事件与三 Gate 输入）

- 三个 required context：`CI fast gate` / `CI integration gate` / `CodeQL gate`，名字与 workflow 逐字相同（`tests/test_merge_queue_ruleset.py::…::test_gate_names_match_the_workflow_files`）。
- 判定器 `scripts/ci/aggregate_gate.py` **未改**；输入只有 `--needs-json "$NEEDS_JSON"`（`toJSON(needs)`），判定器本体取默认分支副本以 `python3 -I` 执行（`TestGates::test_gates_run_the_trusted_copy_of_the_verdict`）。
- 闭集：fast gate 9 个 job id（含 backend-fast、frontend）；integration gate 5 个（package / windows-exe-smoke / macos-app-smoke / posix-e2e / backend-platforms）——分片后 job id 不变，显示名变成 `backend-fast (3.10, 1)` / `windows-exe-smoke (1)`，**仓库设置不用重登记**。
- 事件 × 层级表：[`CI01_EVENTS_AND_DAG.md`](CI01_EVENTS_AND_DAG.md) §2（PR / ready_for_review / labeled full-ci / 其它 labeled / merge_group / push main / schedule / release 八行，每行写了看住它的测试或「无测试」）。
  §4 的七条现存问题本轮**没改**（拍板清单 ⑤）。

## 4. 覆盖迁移（旧集合 → 新执行位置）

[`coverage_ledger.json`](coverage_ledger.json)：28 条，`status` 24 pass / 4 not_run。每条 `new_location` 写了旧位置 → 新位置（同 job 加 shard 轴 / needs 改 / 步骤改 / 不变），
`promotion_evidence` 逐条引至少一个 after run 的 job 显示名与结论。摘要：

| 组 | 条数 | 新位置 | 状态与证据 |
|---|---:|---|---|
| 快线 9 个未改形状的 job（Ruff / CLA / invariants / frontend / plugin-candidate / workerd / desktop-shell ×2 / compat-smoke） | 9 | 不变 | pass：run 35031790918 与 35031461863 各 executed / success |
| backend-fast ×3 档 | 3 | 同 job，matrix 加 `shard: [1, 2]`，命令 `--shard=K/2 --shard-manifest=…` | pass：35031790918 / 35007730894 两片各 success；CI 侧并集证据 `evidence/ci05/shards/` |
| CodeQL | 1 | 不变 | pass：codeql.yml run 35031788040 等四个 PR 的 run success |
| backend-platforms ×2 | 2 | 同 job，matrix 加 `shard` 轴 | pass：同上 |
| package ×4 | 4 | needs `[frontend]`；冒烟两步按实例隔离（`package_smoke.py --timeout 120`） | pass：35031461863 / 35031790918（macOS 腿就绪 36s） |
| windows-exe-smoke | 1 | needs `[frontend]`；Playwright 按 project 2 片；不带 `--with-deps` | pass：35031790918 两片 success；分片形态五个 run 各跑过 |
| macos-app-smoke / posix-e2e | 2 | needs `[frontend]` | pass |
| 两个 Gate | 2 | 不变 | pass（含 full-ci 模式下的真实 failure 记录） |
| main-landing-audit / nightly / lab-qualification / release | 4 | 不变 | **not_run，本轮未动**：没有合入 main 就没有 push main run；nightly / lab / release 的 yml 没改、本轮没采集它们的 run（`ci05_note` 逐条写明） |

**没有任何 required 领域被搬到合并后**：`-m slow` 仍只在 lab、CompatBench 全量 + 保真度仍只在 lab nightly 档、真安装链仍只在 nightly.yml（CI00 §9 的三条不变）。

## 5. 构建 producer / consumer 与 artifact 身份

- 全图唯一的数据边仍是 `frontend ══▶ plugin-candidate`（`codex-plugin-candidate` artifact）：消费者用 **checkout 的 HEAD SHA** + **清单里的 content_digest** 核，不信 artifact 名字（CI02 §5 / CIP-011；`TestBuildReuseAndCaches::test_the_plugin_candidate_consumer_verifies_head_sha_and_manifest_digest`）。
- CI02 决定 **0 行跨 job 抽取**（九种 recipe 逐行有数字，`evidence/ci02/recipe_table.json`）；重型 job 各自现建产物（每片各建一次，§4.3 的代价）。
- pytest 分片的 `pytest-*-shard<K>` artifact（manifest + junit，7 天）与 Playwright 的 `playwright-shard-check-windows-shard<K>` **只作证据、不作判定输入**；manifest 带 `git_head`（那次 run 的 merge ref，如 `fd2b2164`）。
- 生成物不回索引：`scripts/ci/check_generated_untracked.py` 在 frontend 与 main-landing-audit 各查一次（CIP-015）；plugin-stable 通道未动。

## 6. pytest / Playwright 分片及 serial 集合

- **pytest**：按文件分 2 片（不拆文件——29 个文件有 module 级 fixture，CI00 §7），同进程 collection 之后做，每个进程算全部片并自验 nodeid 集合（并集 == 全集、两两不交、每片非空、无重复，否则 rc 4）；
  权重表 `tests/support/shard_weights.json`（本机 macOS 样本；Windows 上两片最多差 399s，重算方法 CI03A §6，CI 的 junit 在 `evidence/ci05/shards/`）。
  不带 `--shard` 是 no-op：lab / nightly / release 的命令没有它，仍跑全集（`test_unsharded_pytest_lanes_stay_unsharded`）。**serial 集合**：没有把任何用例标成「只能单进程」——CI00 §7 的候选（进程级 env / chdir / module fixture / 真子进程）在「每片一台机器、不拆文件」下都不需要 serial；同机多进程（xdist）没做、也没有为它建 serial 集。
- **Playwright**：`windows-exe-smoke` 按 project 分 2 片（片 1 chromium；片 2 webkit + chromium-en），每片先跑自验（`--list` 的 (project, file:line:col, title) 集合）再 e2e；`posix-e2e` 不分片。`workers: 1` / `fullyParallel: false` / `retries: 1` 未改。
- CI 侧完整性：[`CI05_COMPARISON.md`](CI05_COMPARISON.md) §9（五条腿 × 两片并集 == 4686 且与本机 collection 逐条相同；Playwright 并集 == 151）。

## 7. 已有 runtime / toolchain / 缓存策略

- runtime / toolchain：setup-python（3.10 / 3.13 / 3.14 + 3.13 平台腿）、pnpm 11 + setup-node、rust-toolchain + rust-cache（workerd / src-tauri 各自 workspace）、内置 CPython 归档由 `build_worker_runtime.py` 按 `packaging/runtime-lock.json` 下载校验。matplotlib 在 backend-fast **未钉版本**（3.10 腿 3.10.9、3.13/3.14 腿 3.11.2）——是意图还是漂移未查明（`coverage_ledger.json` known_limitations；U00 的产品问题）。
- 缓存只有两类、枚举钉住（CI02 §4）：`actions/cache` 两处 CPython 归档（key 含 os / arch / 锁 hash）、setup-node pnpm store（按 `web/pnpm-lock.yaml`）、rust-cache（各自 workspace，无 `shared-key`）；venv / site-packages / 用户目录 / 测试结果 / Playwright 浏览器目录一律不缓存。
- **作用域是坏的**（CI02 §4.1）：push main 上没有任何产缓存的 job → 合并组候选 ref 永远冷（run 35015416419 上 12 个 job 0 行 `Cache restored`）、每个候选各写 ≈ 1.8 GB、仓库缓存 10.3–10.6 GB 已超 10 GB 上限、已合入候选的条目没人清。after 样本里看得见：desktop-shell 冷 233s vs 暖 41s（同一 PR 第二次 run）。修法 (a)（push main 种子 job + rust-cache `shared-key` 对齐）归拍板清单 ③。
- Playwright 浏览器缓存决定**不加**（下载只 17–27s；Windows 那 200 多秒是 `--with-deps` 装 Media Foundation，CI02 已去掉并由 run 35031790918 证实不需要）。

## 8. runner 各信任区、实际资源与未部署项

- 信任区 A（hosted）：ci.yml / codeql.yml / pr-conflict-domains.yml 的全部 job ⊆ {ubuntu-latest, macos-latest, windows-latest}（`TestRunnerTrustZones` ①）。信任区 B（可销毁 PR 池）：**不存在**。信任区 C（lab）：`tavotto-lab` 只在 `_lab-qualification.yml`，调用方只有 `lab-ci.yml` / `release.yml`，事件 ⊆ {push, schedule, workflow_dispatch}（②）。
- 实际资源（只读，2026-09-15 22:03Z，`evidence/ci04/`）：仓库级 self-hosted runner 4 台在线（`tavotto-ci-01` 带 `tavotto-lab`，busy；`-01-2/-3/-4` 只带 `tavotto-ci`，**idle 且没有任何 workflow 用它们**），runner 2.336.0（当前发行 2.337.0），`runner_group=Default`；org free 计划；fork PR 审批 `first_time_contributors`；org 级 runner group / 策略 **403 读不到**。
- 容量：托管账户并发上限已查明——free 计划 **20 个并发 job（macOS 5）**（`evidence/ci04/org_plan.json` + GitHub 文档「Usage limits」；API / 账单页不显示）；一个 full-ci run 29 个 job 自己就超，多 PR 同时进队时 ubuntu 领取等待 105–1380s（本轮 §5），是 after 状态下的第一瓶颈；处置是改推送习惯（§13 ⑥）。lab 网络 / hypervisor / VM 模板：一条没测（`evidence/admin_inventory.json` 18 项待管理员）。
- **未部署项**：新池的 VM / JIT 注册 / runner group 限制 / 路由开关（ADMIN_HANDOFF B / C / D）全部 `not_run`；PR 触到可信 lab 的动态半边（同仓库分支 PR 加一行 `runs-on`）读不到、没实测（CIP-024 not_run）。

## 9. 真实性能样本及局限（实际计时样本、冷暖、版本与资源）

[`CI05_COMPARISON.md`](CI05_COMPARISON.md) §1–§8。关键行：

| | before（29 个 merge_group 中位） | after 无争抢样本 #374（CI01 + CI03a） | after #375（+ CI03c，Windows 等 205s） | after 争抢样本 #376 / #377 的 q₀（口径） |
|---|---:|---:|---:|---:|
| feedback | 1964s（32.7 min） | **1127s（18.8 min）** | 1229s | — |
| qualification | 3412s（56.9 min） | **1695s（28.3 min）** | **1485s（24.8 min）** | 1412 / 1498s（23.5 / 25.0 min，runner_wait := 0 的口径，不是测量） |
| 关键路径 | backend-fast → windows-exe-smoke → gate | frontend → windows-exe-smoke（未分片）→ gate | frontend → windows-exe-smoke (1) → gate | backend-platforms (windows) → gate |
| runner 分钟（ubuntu / windows / macos） | 92.7 / 66.2 / 35.3（同日对照 120–135 / 58–75 / 32–38） | 127.7 / 70.6 / 37.8 | 124.8 / 69.1 / 39.0 | 128 / 69–73 / 37–38 |

局限（全部写在 §2 / §5）：每档 n = 1–2；全是 `pull_request` 事件（缓存作用域与 merge_group 不同：PR 第二次 run 暖、合并组永远冷）；
托管 ubuntu 执行时长自身漂 ±30%（同一套 backend-fast 在 PR 上 1293–1963s）；含全部改动的两个样本都落在三 PR 并行的争抢窗口里，只有扣除 runner_wait 的口径；
`--with-deps` 那 200s 只在 job 级看得见。**不承诺 2×；无争抢样本上资格 −50%、反馈 −43%**。

## 10. 负例和取消 / 掉线 / 回退演练（取消、掉线、坏 artifact、缺片与清理负例）

- 九条负例逐条的证据类型（CI 真红 / 本机反证 / not_run 与要开什么坏 PR 才能证）：[`CI05_COMPARISON.md`](CI05_COMPARISON.md) §10。CI 上真红过的三个 run：35004450721（Windows 片 2 红一条 → Gate failure）、35024490379 / 35028309531（macOS 片 2 红 → Gate failure）、34994534095 attempt 1（windows-exe-smoke 挂 60 min 硬杀 → Gate failure）。
- 取消：同 PR 新事件取消旧 run、别的 PR 不受影响（两对样本，§6.1）；PR ↔ merge_group / push main / release 互不取消**没有同时刻样本**（CIP-008 / 010 not_run）。
- 掉线：托管 runner 没有掉线样本；PR #373 attempt 1 的「挂 60 分钟无日志」是唯一的异常形状，CI03c 用 step 级 timeout 把它变成带日志的失败（那之后没再出现）。
- 回退演练（本机，一次性 worktree）：§11——CI03a / CI03c / CI01 / CI02 四条各改几行，actionlint 全 0，合同测试按预期红（红的用例逐条列出，回退 PR 必须连测试一起改）；CI03b 的回退与**实机回退（回退后同 SHA 重跑、旧报告不复用）not_run**。

## 11. 必需证据路径 / 命令 / exit code

| 命令（worktree `ci-foundation`，本 PR 的树） | 退出码 |
|---|---:|
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `.venv/bin/python -m pytest tests/test_ci_baseline.py tests/test_docs_references.py tests/test_merge_queue_workflows.py` | 0（112 passed） |
| `docs/implementation/ci-foundation/evidence/ci05/analyze_all.sh` | 0 ×7 |
| `.venv/bin/python docs/implementation/ci-foundation/evidence/ci05/compare.py` | 0 |
| `.venv/bin/python docs/implementation/ci-foundation/evidence/ci05/shards/check_ci_shards.py --pytest-dir <artifacts> --playwright-dir <artifacts> … --out …` | 0（9 条负例各 1 / 2） |
| `.venv/bin/python docs/implementation/ci-foundation/evidence/ci05/rollback/rollback_drill.py <out>` | R0 0；R1–R4 预期红 |
| 逐个解析 `docs/implementation/ci-foundation/**/*.json`（305 个）；`**/*.md` 相对链接 | 0 / 0 |
| 各阶段自己的验证表 | CI00 §15 / CI01 §6 / CI03A §5 / CI03C §8 / CI03B §7 / CI02 §7 / CI04 §7 |

证据根：[`evidence/README.md`](evidence/README.md)（每个子目录一行）；本轮：[`evidence/ci05/README.md`](evidence/ci05/README.md)。

## 12. 合入后的复核（谁来做：用户 / 合并者；本轮 not_run）

七个 PR 合入 main 之后，用**第一个** merge_group success run（不是 PR 的第二次 run——那本来就暖）：

```sh
python scripts/ci/ci_baseline.py fetch-jobs --run-id <merge_group run id> --out /tmp/ci05-verify
python scripts/ci/ci_baseline.py trim --src /tmp/ci05-verify --dst /tmp/ci05-verify/trimmed
python scripts/ci/ci_baseline.py analyze --compact --workflow .github/workflows/ci.yml \
  --evidence /tmp/ci05-verify/trimmed \
  --edge-kinds docs/implementation/ci-foundation/evidence/ci01/dag_edge_kinds_after.json \
  --out /tmp/ci05-verify/timing.json
python -c "import json; r=json.load(open('/tmp/ci05-verify/timing.json'))['runs'][0]; print(r['feedback_seconds'], r['qualification_seconds'], [x['name'] for x in r['critical_path']])"
gh run download <run id> -p 'pytest-*' -D /tmp/ci05-verify/art && \
python docs/implementation/ci-foundation/evidence/ci05/shards/check_ci_shards.py \
  --pytest-dir /tmp/ci05-verify/art --pytest-run-id <run id> --out /tmp/ci05-verify/shards.json
```

判据：`qualification_seconds` 与 §9 的 1485–1695s 同一量级（合并组上 runner_wait 中位 2–9s，应接近 q₀）；`check_ci_shards.py` 退出码 0；
四个 job 日志里各自的缓存行仍是 miss（作用域没修之前应如此——修好后应看到 `Cache restored from key`）。任一条不成立，回到 §0 把 `ci_hosted_ready` 改成 `fail` 并写原因。

## 13. 需要管理员 / 用户拍板的最小下一步——**2026-09-16 用户已逐条拍板**

| # | 事项 | 拍板结果 | 状态 | 落点 |
|---|---|---|---|---|
| ① | **合并顺序**：#372 → #373 → #374 → #375 → #376 → #377 → #378 → 本 PR（stacked，进合并队列，每合一个把下一个的 base 改成 `main` 并核 `baseRefName`）；**#373 之前不合 #374**；合完做 §12 的复核 | **已授权**：lead 按此顺序经合并队列逐个合入 | **执行中** | [`README.md`](README.md) 实施状态；`docs/ci/parallel-prs.md` |
| ② | **lab 暴露**：同仓库分支 PR 加一行 `runs-on: [self-hosted, tavotto-lab]` 今天很可能会派到 `tavotto-ci-01`（CI04 §2.2） | **把 lab runner 迁到私有 ci-infra 仓库**（`docs/ci/self-hosted-runner.md` §1 的「备用形态」）；不走 runner group | 待管理员 + 两个后续 PR | [`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md) **F 组**（操作表 F-1…F-12、发布链切两段的设计 F.2、要改主语的合同测试 F.3、切换顺序与回退 F.4）；CI04 §2.3 那一行已标 |
| ③ | **缓存种子 job**（CI02 §4.1 (a)：push main 上三条腿 restore → 真跑一次 → save，rust-cache 两边同一个 `shared-key`；验法看合并组日志的 `Cache restored from key`） | **做** | **已拍板，待栈合入后开 PR** | [`CI02_BUILD_REUSE.md`](CI02_BUILD_REUSE.md) §4.1 (a) 已标 |
| ④ | **产品侧 getfqdn**：werkzeug 继承的 `server_bind` 反查主机名让首开在反向 DNS 无回音的机器上多等 ~36s | **现在就修产品，单开分支，不进 CI 栈** | **已拍板，修复 PR 另开** | [`CI03B_PACKAGE_SMOKE_ISOLATION.md`](CI03B_PACKAGE_SMOKE_ISOLATION.md) §9 已加一句；修好后 `--timeout 120` 回默认值 |
| ⑤ | **CI01 事件表的七条现存问题**（[`CI01_EVENTS_AND_DAG.md`](CI01_EVENTS_AND_DAG.md) §4） | **修 ①③⑥，接受 ②④⑤⑦**：① 任意 `labeled` / `unlabeled` 重跑快线、去掉 `full-ci` 产出 deferred Gate → 修；③ `ready_for_review` 无测试 → 修（与 ① 同一个 PR）；⑥ ci.yml 抬头与时长注释陈旧 → 修；② push main 三连推中间一次的 landing audit / SARIF 被替换 → 接受（不是资格，并发只有 20）；④ codeql 无 `types` → 接受（结论按 SHA）；⑤ `analyze` 不校验 workflow 版本 → 接受（流程已按 run 记快照）；⑦ 草稿与非草稿同一套 → 接受（快线已 19 min） | 修的三条：后续 PR | CI01 §4 每条后面已写「拍板：修 / 已接受（理由）」 |
| ⑥ | **账户并发上限** | **已查明：org `Tavotto` 是 free 计划 → 托管 runner 总 20 个并发 job、macOS 最多 5**（`gh api orgs/Tavotto --jq .plan.name` = free；GitHub 文档「Usage limits」表；账单页与 API 都不显示这个数）。处置 = **改习惯 + 记录**：stacked PR 一次只让一个在跑（直接进合并队列串行）、`full-ci` 只给真要探平台腿的 PR；一个 full-ci run 自己就 29 个 job > 20 | **已拍板，已记录** | [`CI05_COMPARISON.md`](CI05_COMPARISON.md) §5（争抢样本按已知数重述）；`evidence/admin_inventory.json`（新 observed 项）；`docs/ci/parallel-prs.md`「并发上限与推送节奏」 |
| ⑦ | **闲置 runner** `tavotto-ci-01-2/-3/-4`（在线、idle、没有任何 workflow 用） | **注销，不建池**（ADMIN_HANDOFF B / C / D 组留档不执行） | 待管理员执行 | [`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md) A-2 已改成操作项（`svc.sh stop && svc.sh uninstall && config.sh remove` ×3；验收 `actions/runners` 只剩 -01）；`runner_pool_ready` 仍 `not_run`，按拍板本轮不会变 pass；CI04 §4 结尾已记 |

不在清单里但已记录、不需要拍板的下一刀（都在 hosted 上）：Windows junit 重算权重表（CI03A §6）、backend-fast 3 片或 Linux 重平衡、`package` 开 pnpm 缓存（③ 之后）。

## 14. U00 应沿用的内容与需重新调查的产品问题（U00 应复用与需要重新审计的内容）

**沿用（06 第 2–5 条）**：

- 执行位置与合同：三个 Gate + `aggregate_gate.py` 不变；新增测试按 `runner_class`（hosted 三种）/ 阶段（快线 vs 重型 vs lab）/ timeout / 资源前提接进现有 job，不新造调度器、结果汇总器或 case-enrollment 数据库（U01 若需要产品用例管理，自己实现；本前置没交付空接口）。
- 分片机制：pytest 的 `--shard K/N` + manifest、Playwright 的按 project 分片 + 自验——U02–U11 的真实包 / FirstOpen / RenderBench 用例进现有 job 就自动被分片覆盖；新增 Playwright project 要回去改 `windows-exe-smoke` 的 matrix（合同会红）。
- 产物身份：`plugin_stage.py` 的 source_sha + content_digest 是唯一跨 job 数据边的样板；新的 producer / consumer 照它做，不信 artifact 名字。
- 基线采集：`scripts/ci/ci_baseline.py`（四类时间口径、按 attempt 过滤、用 run 执行时那份 ci.yml 分解）+ `evidence/ci05/compare.py`（争抢标记、q₀ 口径）——U00 量新用例的耗时时直接用。
- 两种语料（CompatBench vs acceptance）仍分开，不合成一个百分比（06 第 5 条）。

**需重新调查的产品问题（本前置不碰产品代码）**：

1. werkzeug `server_bind` 的 getfqdn 停顿（拍板清单 ④）——用户首开体验，不只是 CI。
2. backend-fast 的 matplotlib **未钉版本**（3.10 腿 3.10.9 / 3.13、3.14 腿 3.11.2）：tested-latest 的意图还是漂移，`coverage_ledger.json` 的 known_limitations 记着；与 `docs/support-matrix.json` 的口径要对一次。
3. `windows-exe-smoke` 的 Playwright 偶发红（CI00 §4.3 两次 attempt 1→2 即过；attempt 2 那次 asset-library 多-Figure 1 flaky）与 #373 attempt 1 的「挂 60 分钟无日志」——step 级 timeout 之后再出现时会有 trace，那时再查根因。
4. CI00 §6 列的 Windows 特慢用例（`test_invariants_engine` 三条 `InvMix` 合计 232s、`test_preview_hybrid` setup 28s、`test_dependency_repair` 20s）：是产品在 Windows 上慢还是夹具慢，分片之后它们决定 Windows 片的长短。
5. `test_runtime_build` / `test_tutorial` 依赖产物的用例在 CI 里没有「先建产物再跑 pytest」的执行位置（CI00 §13 未知项，本轮没解）。
6. 参数 id 里带 `WindowsPath` 的两条用例在 Windows 腿上名字不同（`evidence/ci05/shards/ci_shard_check.json` 的 `raw_union_differences_vs_reference`）——只是 id 渲染，但跨 os 比对 junit 的工具要认得它。

## 15. 待统一计划入库时粘贴的文字（06 第 1 条）

`docs/implementation/tavotto-foundation/` **今天不在仓库里**，06 第 1 条要加到它的 README / Start Here 的那段，原样放在这里，入库时粘贴：

> 先执行 ci-foundation 的 CI00；`ci_hosted_ready` 后进入 U00，`runner_pool_ready` 可以随后完成。CI00 与 U00 只读清点可并行。
> ——2026-09-16 状态：`ci_hosted_ready: pass`（基于七个 PR 的 full-ci 实测，合入 main 后用第一个 merge_group run 复核，见 `docs/implementation/ci-foundation/CI_HANDOFF.md` §12）；`runner_pool_ready: not_run`（无部署权限，管理员操作表见 `ADMIN_HANDOFF_RUNNER_POOL.md`）。U00 引用 `CI_HANDOFF.md`，不重做 CI 调查，不从别的 SHA 借产品资格。

06 第 2 条（U00 引用 `CI_HANDOFF.md`、补本轮产品调用图 / 旧行为）：产品调用图本前置没画——U00 自己做，§14 的六条是它的起点。
