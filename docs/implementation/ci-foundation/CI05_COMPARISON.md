# CI05 · 前后对照：同等覆盖下的提速证据（2026-09-16）

- **本轮没有任何 PR 合入 main**。所有 after 样本都是七个 stacked PR（#372 → #378）上的 `full-ci` run（`pull_request` 事件）：
  job 集合与 merge_group 同一套，差别是事件、缓存作用域（PR 的第二次 run 能命中自己的缓存，合并组永远冷——CI02 §4.1）、
  以及是否与别的 run 争抢托管 runner。**合入后要用第一个 merge_group run 复核**（`CI_HANDOFF.md` 的复核命令）。
- before = CI00 的 29 个 merge_group success（源码 `8b95256c`，[`evidence/actions/timing_decomposition.json`](evidence/actions/timing_decomposition.json)）；
  另取 2026-09-15 当天 6 个别人的 merge_group run 作「同一天的托管噪声对照」（ci.yml 仍是 `8b95256c` 那份）。
- **每个 after run 用它自己那个分支的 ci.yml 分解**（CI01 §4 ⑤：用哪份 ci.yml 分解哪些 run 必须对应）：
  快照在 [`evidence/ci05/workflows/`](evidence/ci05/workflows/)（`git show <head>:.github/workflows/ci.yml`），
  每个 PR 的 `refs/pull/N/merge` 上那份 ci.yml 与 head 的 **blob 相同**（`git rev-parse <ref>:.github/workflows/ci.yml` 逐个比过，
  下表「ci.yml」列）；`8b95256c` 那份用 `tests/fixtures/ci_baseline/ci_8b95256c.yml`。边分类：CI00 那份 DAG 用 `evidence/dag_edge_kinds.json`（23 条），
  CI01 之后的全部用 `evidence/ci01/dag_edge_kinds_after.json`（19 条；CI03 之后边没再变，只是 matrix 变了）。
- 分解命令与输出：[`evidence/ci05/analyze_all.sh`](evidence/ci05/analyze_all.sh) → [`evidence/ci05/timing/`](evidence/ci05/timing/)（7 份，按 ci.yml 快照分组）；
  并排表由 [`evidence/ci05/compare.py`](evidence/ci05/compare.py) 生成 → [`evidence/ci05/comparison.json`](evidence/ci05/comparison.json)。
  原始 run / jobs JSON（裁剪，`ci_baseline.py trim`）在 [`evidence/ci05/actions/`](evidence/ci05/actions/)。
- **样本量：每档 n = 1–2，不算 p95**，只给单值；before 给中位（n = 29）。「争抢 runner」的样本单列（§5），不混进可比列。
- 一处工具修补：after run 的 `分片证据` 上传步（CI03a）在 `ci_baseline.py` 的 step 分类表里没登记、静默落进 `other`；
  补进 `artifact` 类并加用例 `tests/test_ci_baseline.py::test_every_named_step_in_the_live_workflow_has_an_execution_category`
  （HEAD 的 ci.yml 里每个带 `name:` 的步骤都必须落进一个真实类别；变异：把规则去掉 → 红）。没有放宽任何判据。

## 1. 样本一览

| run | attempt | PR / 分支 | ci.yml（blob） | 含哪些改动 | 事件 | 结论 | 备注 |
|---|---|---|---|---|---|---|---|
| 34993304390 | 1 | #372 ci/ci00-baseline | `8b95256c`（b653fce5） | 无 CI 行为改动 | PR full-ci | success | **after 对照**：同 DAG，量 PR 事件本身的差别 |
| 34994534095 | 1 | #373 ci/ci01-dag-edges | `79c5aa38`（3b901351） | CI01 | PR full-ci | cancelled | windows-exe-smoke 的 Playwright 步挂到 job 级 60 min 被硬杀（无日志，CI03c §5）→ Gate failure；**其余 22 个 job 全 success**，重型 job 的 dependency_wait 是 CI01 的第一份实测 |
| 34994534095 | 2 | 同上 | 同上 | CI01 | re-run failed jobs | success | 只重跑 windows-exe-smoke + integration gate（21 个 job carried_over）——给那一个 job 的时长（1334s），**不是资格样本** |
| 35004450721 | 1 | #374 ci/ci03-pytest-shards | `d9e8dd72`（61fd2dc7） | CI01 + CI03a | PR full-ci | failure | `backend-platforms (windows-latest, 2)` 红一条（新用例只钉了父进程解码器，CI03A §3.5）→ Gate failure；分片红 → Gate 红的实机证据 |
| **35007730894** | 1 | #374 | `d9e8dd72` | CI01 + CI03a | PR full-ci | success | **无争抢**（全部 job runner_wait ≤ 50s） |
| **35011613925** | 1 | #375 ci/ci03c-playwright-shards | `35b912a0`（381d2c6b） | + CI03c | PR full-ci | success | Windows 领 runner 等 131–372s（本 run 自己同时要 5 台 Windows：pytest 2 片 + Playwright 2 片 + package），关键路径上 205s |
| 35024490379 | 1 | #376 ci/ci03b-package-smoke-isolation | `1f7f13e8`（6be29eda） | + CI03b | PR full-ci | failure | `backend-platforms (macos-latest, 2)` 红 10 条（getfqdn，CI03B §9）→ Gate failure |
| 35028309531 | 1 | #376 | `1f7f13e8` | + CI03b（诊断） | PR full-ci | failure | 同上红 5 |
| **35031461863** | 1 | #376 | `162f54c6`（860f77a3） | + CI03b（修复） | PR full-ci | success | 22:32 起跑；22:36 起 #377 / #378 同时跑——两个 Gate 各等 runner 176s / 447s，重型 job 69–394s |
| **35031790918** | 1 | #377 ci/ci02-build-reuse | `bb27bdaf`（2a58e856） | + CI02 | PR full-ci | success | **争抢样本**：frontend 等 654s、Ruff 1380s、Windows 片 2 等 503s |
| 35031800904 | 1 | #378 ci/ci04-runner-pilot | `bb27bdaf` | + CI04（无 yml 行为改动） | PR（无 full-ci） | success | 重型 5 个 skipped、integration gate deferred；**不是资格样本**，只有快线（gate 等 runner 1129s） |
| 34957615294 / 34970490865 / 34990070055 / 35002647792 / 35015416419 / 35027644355 | 1 | #358 / #359 / #360 / #361 / #362 / #357 的合并组 | `8b95256c` | 无 | merge_group | success ×6 | 同一天的 before 形状对照；35027644355 与 lab nightly 35028176892 同时在跑 |

## 2. 两个总时长：before 中位 vs after 各样本

`feedback` = t0 → `CI fast gate` 完成；`qualification` = t0 → `CI integration gate` 完成（口径同 CI00 §2）。
`q₀` = **runner_wait 全部记 0、其余照实测重放 DAG** 的资格时长（`compare.py::zero_runner_wait_model`）——
它只是把「等 runner」从数字里扣掉的**口径**，不是测量：它回答「runner 秒领时这条 DAG 会在几秒出结论」。

| 样本 | feedback | qualification | q₀ | 关键路径（job_seconds / runner_wait） | 争抢 |
|---|---:|---:|---:|---|---|
| **before**：29 个 merge_group 中位 | **1964**（32.7 min） | **3412**（56.9 min；min 2714 / max 4064） | ≈ q（runner_wait 中位 2–9s） | backend-fast → windows-exe-smoke → gate（28/29） | — |
| 同日 6 个 merge_group 对照 | 1983–2157 | 3284–3615 | 3278–3608 | 同上 6/6 | 35027644355 的 gate 等 135s |
| #372 34993304390（after 对照，同 DAG） | 1539 | 2957 | 2950 | backend-fast (3.14) 1440 → windows-exe-smoke 1413 → gate | 否 |
| #373 34994534095 a1（CI01） | 2029 | 4160（failure） | — | frontend 180 → windows-exe-smoke **3900（硬杀）** → gate | 否 |
| **#374 35007730894（CI01 + CI03a）** | **1127**（18.8 min） | **1695**（28.3 min） | 1688 | frontend 251 → windows-exe-smoke 1428 / 2 → gate | **否** |
| **#375 35011613925（+ CI03c）** | 1229（20.5 min） | **1485**（24.8 min） | 1275 | frontend 175 → windows-exe-smoke (1) 1076 / **205** → gate | 是（Windows） |
| #376 35031461863（+ CI03b） | 1363（fast gate 等 176s） | 1873 | 1412 | backend-platforms (windows, 1) 1401 → gate **/ 447** | 是（Gate） |
| #377 35031790918（+ CI02） | 2467 | 2221 | 1498 | frontend 250 / **654** → windows-exe-smoke (2) 670 / **503** → gate | 是（全面） |
| #378 35031800904（无 full-ci） | 2430 | deferred | — | —（重型 skipped） | 是（gate 等 1129s） |

**能说的事实**：

- 唯一一个无争抢、且含 DAG + pytest 分片的完整样本（#374）：反馈 32.7 → **18.8 min**，资格 56.9 → **28.3 min**（−50%，n = 1）。
- 加上 Playwright 分片的样本（#375）：资格 **24.8 min**，且它的关键路径上还带着 205s 的 Windows 领取等待——扣掉后的口径 q₀ = 1275s（21.3 min）。
- 全部改动（含去 `--with-deps`）的两个样本（#376 / #377）都落在争抢窗口里，实测 31.2 / 37.0 min；扣除等待的口径 q₀ = 1412 / 1498s（23.5 / 25.0 min），
  与 #375 的 q₀ 同一量级——**说明 CI02 那 200 秒在关键路径上的贡献被争抢淹没了，只能在 job 级看见（§4.4）**。
- feedback 现在由 backend-fast 最慢的那一片决定（3.10 的第 2 片 1112 / 1171 / 902 / 839s）+ 领 runner；before 由 3.10 / 3.14 整档决定（1865 / 2090）。
- #372 这个「同 DAG 的 PR 对照」资格 2957s 比 before 中位快 455s：它的三条 backend-fast 是 1293 / 1408 / 1440s（merge_group 中位 1865 / 1745 / 2090，
  min 1240）——**托管 ubuntu 的执行时长本身漂 ±30%**（同一套用例 #373 a1 又跑出 1963 / 1989 / 1532）。所以下面「每片一半」的结论要看同一 run 内两片之和，
  不要拿单个 after 数字减 before 中位当作分片的收益。
- **不要求 2×，也没达到 2×**（05 §1）：无争抢样本上资格 −50%，争抢样本上 −35…−45%（实测）。

## 3. 每个 job 的四类时间（before 中位 vs after 各 run）

数字 = `job_seconds`；括号里 `dw` = dependency_wait、`rw` = runner_wait（≥ 60s 才标，粗体）；`—` = 这个 run 里没有这个显示名（矩阵形状不同）。
完整的 dispatch_gap / execution 按类（checkout / setup / install / test / build / artifact / post）在 `comparison.json` 的 `after[].jobs`。

| job | before 中位（n） | #372 34993304390 | #373 a1 34994534095 | #374 35007730894 | #375 35011613925 | #376 35031461863 | #377 35031790918 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Python quality (Ruff) | 12（n=29） | 13 | 13 | 10 | 12 | 10 | 13（**rw 1380**） |
| Contributor licence (CLA) | 4（n=29） | 7 | 5 | 6 | 9（**rw 120**） | 7 | 4（**rw 105**） |
| invariants | 501（n=29） | 374 | 375 | 509 | 523 | 531 | 469（**rw 432**） |
| backend-fast (3.10, 1) | 1865（整档，n=29） | — | — | 1045 | 1017 | 1045 | 1007（**rw 1301**） |
| backend-fast (3.10, 2) | 〃 | — | — | 1112 | 902 | 1171 | 839（**rw 429**） |
| backend-fast (3.13, 1) | 1745（整档，n=29） | — | — | 1003 | 975 | 989 | 947（**rw 704**） |
| backend-fast (3.13, 2) | 〃 | — | — | 1028 | 983 | 1063 | 1054（**rw 1270**） |
| backend-fast (3.14, 1) | 2090（整档，n=9） | — | — | 784 | 592（**rw 171**） | 799 | 933（**rw 331**） |
| backend-fast (3.14, 2) | 〃 | — | — | 856 | 1123（**rw 83**） | 861 | 1125（**rw 934**） |
| backend-fast (ubuntu-latest, 3.10) | 1865 | 1293 | 1963 | — | — | — | — |
| backend-fast (ubuntu-latest, 3.13) | 1745 | 1408 | 1989 | — | — | — | — |
| backend-fast (ubuntu-latest, 3.14) | 2090 | 1440 | 1532 | — | — | — | — |
| backend-platforms (macos-latest, 1) | 1485（整档，n=29） | — | — | 850 | 847 | 814 | 882（**rw 612**） |
| backend-platforms (macos-latest, 2) | 〃 | — | — | 910 | 787 | 927 | 741（**rw 411**） |
| backend-platforms (macos-latest, 3.13) | 1485 | 1526 | 1629 | — | — | — | — |
| backend-platforms (windows-latest, 1) | 2443（整档，n=29） | — | — | 1438 | 823（**rw 131**） | 1401 | 1223（**rw 304**） |
| backend-platforms (windows-latest, 2) | 〃 | — | — | 1269 | 1172 | 1002 | 1357（**rw 277**） |
| backend-platforms (windows-latest, 3.13) | 2443 | 2166 | 2676 | — | — | — | — |
| compat-smoke | 132（n=29） | 134 | 133 | 144 | 146 | 136 | 136（**rw 756**） |
| desktop-shell (macos-latest) | 212（n=29） | 141 | 226 | 41 | 237 | 39 | 167（**rw 209**） |
| desktop-shell (ubuntu-latest) | 238（n=29） | 193 | 176 | 91 | 233 | 77 | 184（**rw 1000**） |
| frontend | 252（n=29） | 175 | 180 | 251 | 175 | 248 | 250（**rw 654**） |
| plugin-candidate | 46（dw 264，n=29） | 42（dw 264） | 49（dw 195） | 44（dw 254） | 69（dw 191，**rw 133**） | 50（dw 251，**rw 394**） | 56（dw 1036，**rw 604**） |
| workerd | 23（n=29） | 26 | 24 | 54 | 20（**rw 149**） | 25 | 28（**rw 917**） |
| CI fast gate | 7（dw 1931，n=29） | 8（dw 1529） | 5（dw 2021） | 8（dw 1117） | 8（dw 1219） | 6（dw 1181，**rw 176**） | 9（dw 2456） |
| package (macos-latest, 3.13) | 102（dw 2099，n=9） | 116（dw 1529） | 109（dw 195，**rw 66**） | 116（dw 254） | 111（dw 191，**rw 209**） | 80（dw 251，**rw 223**） | 87（dw 1036，**rw 260**） |
| package (ubuntu-latest, 3.13) | 68（dw 2099，n=9） | 70（dw 1529） | 60（dw 195） | 72（dw 254） | 66（dw 191，**rw 65**） | 52（dw 251，**rw 339**） | 55（dw 1036，**rw 721**） |
| package (ubuntu-latest, 3.14) | 67（dw 2099，n=9） | 69（dw 1529） | 64（dw 195） | 71（dw 254） | 70（dw 191，**rw 348**） | 55（dw 251，**rw 69**） | 56（dw 1036，**rw 724**） |
| package (windows-latest, 3.13) | 116（dw 2099，n=9） | 121（dw 1529） | 163（dw 195） | 102（dw 254） | 128（dw 191，**rw 372**） | 101（dw 251，**rw 117**） | 83（dw 1036，**rw 636**） |
| windows-exe-smoke（不分片） | 1411（dw 1931，n=29） | 1413（dw 1529） | 3900（dw 195，cancelled）；a2：1334 | 1428（dw 254） | — | — | — |
| windows-exe-smoke (1) | 〃 | — | — | — | 1076（dw 191，**rw 205**） | 1008（dw 251，**rw 96**） | 796（dw 1036，**rw 147**） |
| windows-exe-smoke (2) | 〃 | — | — | — | 945（dw 191，**rw 321**） | 861（dw 251，**rw 172**） | 670（dw 1036，**rw 503**） |
| macos-app-smoke | 325（dw 1931，n=29） | 290（dw 1529） | 319（dw 195） | 348（dw 254） | 356（dw 191） | 354（dw 251，**rw 132**） | 386（dw 1036，**rw 356**） |
| posix-e2e | 541（dw 1931，n=29） | 490（dw 1529） | 520（dw 195） | 564（dw 254） | 556（dw 191，**rw 72**） | 558（dw 251，**rw 147**） | 501（dw 1036，**rw 661**） |
| CI integration gate | 7（dw 3402，n=29） | 10（dw 2944） | 8（dw 4149，failure） | 8（dw 1684） | 10（dw 1472） | 10（dw 1416，**rw 447**） | 9（dw 2209） |

## 4. 分项贡献（每项引它自己的 job / step）

### 4.1 DAG 删边（CI01）：模型 −892s（中位）→ 实测重型 job 的 dependency_wait 1931 → 191–254s

- before：`package` / `windows-exe-smoke` / `macos-app-smoke` / `posix-e2e` 的 `dependency_wait` 中位 **1931s**（等 backend-fast）；after 对照 #372 是 1529s。
- after（只含 CI01 的 #373 attempt 1）：四类重型 job 的 `dependency_wait` 全部 **195s**（= frontend 180s + dispatch）；#374 254s、#375 191s、#376 251s。
  争抢的 #377 是 1036s——那是 frontend 自己等了 654s runner 再跑 250s，不是 DAG。
- 资格时长上的贡献只能与分片一起看（#373 attempt 1 因硬杀没有资格时刻）：CI00 的模型说删边后关键路径会变成 `backend-platforms (windows)`
  整档 2443s → 资格 ≈ 2476s；实测 #374（删边 + pytest 分片）关键路径是 frontend 251 → windows-exe-smoke 1428 → gate = 1695s，
  `backend-platforms (windows, 1)` 1438s 与它几乎并列。**删边把 backend-fast 从关键路径上拿掉了，这一条是实测；−892s 那个数是模型，不再引用。**

### 4.2 pytest 按文件分 2 片（CI03a）：每档 −40…−50%（按同 run 内最慢片算）

| 腿 | before 整档中位 | after 两片（同 run 内，取 #374 / #375 / #376，无争抢或争抢不在这条腿上） | 两片之和 vs 整档 |
|---|---:|---|---|
| backend-fast 3.10 | 1865 | 1045 / 1112 · 1017 / 902 · 1045 / 1171 → 最慢片 **1112 / 1017 / 1171** | 2157 / 1919 / 2216（≈ 整档 + 1 次 install/collection ≈ +5%） |
| backend-fast 3.13 | 1745 | 1003 / 1028 · 975 / 983 · 989 / 1063 → **1028 / 983 / 1063** | 2031 / 1958 / 2052 |
| backend-fast 3.14 | 2090 | 784 / 856 · 592 / 1123 · 799 / 861 → **856 / 1123 / 861** | 1640 / 1715 / 1660 |
| backend-platforms macOS | 1485 | 850 / 910 · 847 / 787 · 814 / 927 → **910 / 847 / 927** | 1760 / 1634 / 1741 |
| backend-platforms Windows | 2443 | 1438 / 1269 · 823 / 1172 · 1401 / 1002 → **1438 / 1172 / 1401** | 2707 / 1995 / 2403 |

- 权重表来自本机 macOS（CI03A §6），Windows 上两片不平衡：#374 片 1 比片 2 重 169s，#376 重 399s；Linux 3.10 片 2 比片 1 重 67–126s。
  重算方法在 CI03A §6，喂 `evidence/ci05/shards/` 里那次 run 的 junit 即可——本轮没有改权重表（覆盖不受影响，只影响平衡）。
- **feedback 从 1964 → 1127–1229s（无争抢）**：由 backend-fast 最慢片 + fast gate 决定。
- `backend-platforms (windows)` 分片后 1002–1438s，**仍是资格的关键路径候选**（#376 上就是它：1401 → gate）；它的 60 分钟上限现在余量充足。

### 4.3 Playwright 按 project 分 2 片（CI03c）：windows-exe-smoke 1411–1428 → 1008–1076（片 1）

- before：`windows-exe-smoke` 中位 1411s（Playwright 步 1124–1137s，三个 project 一台机器串行）。
- after（#375，仍带 `--with-deps`）：片 1 1076s（Playwright 步 525s + 安装 245s）、片 2 945s（377s + 231s）；#376：1008 / 861。
  CI03c §4 的模型说片 1 ≈ 16–16.5 min（960–990s）：实测 1008–1076s，模型偏乐观 2–5%（构建链 4.9 min 那段实测 5.3 min）。
- 关键路径上的贡献：#374（未分片）frontend 251 + 1428 = 1679 → #375 frontend 175 + 205（等 runner）+ 1076 = 1456；扣掉等待 1251。
- 代价：两片各建一次产物（build 142–188s ×2）+ 各装一次浏览器；Windows runner 分钟 64–75 → 69–73（§8，几乎没涨——`--with-deps` 那 200s 抵掉了）。

### 4.4 Windows 去 `--with-deps`（CI02）：安装步 245 / 231 → 17 / 24s，两片 job −212 / −191s

| step「装 web 依赖与本片的浏览器」 | #375 35011613925 | #376 35031461863 | **#377 35031790918（不带 --with-deps）** |
|---|---:|---:|---:|
| 片 1（chromium） | 245 | 205 | **17** |
| 片 2（chromium webkit） | 231 | 211 | **24** |
| Playwright 黄金路径 片 1 / 片 2 | 525 / 377 | 545 / 376 | 493 / 359（128 条全过：Chromium / WebKit 没有 Media Foundation 也起得来） |
| job 合计 片 1 / 片 2 | 1076 / 945 | 1008 / 861 | **796 / 670** |

实验成立（CI02 §2.1 写的判法：两片 e2e 全绿 = 不需要 Media Foundation）。它在关键路径上的贡献被 #377 的争抢（frontend 等 654s、片 2 等 503s）盖住，
只能在 job 级看：片 1 −212s、片 2 −191s（相对 #376）。

### 4.5 `package` 冒烟按实例隔离（CI03b）：起服务步 13 / 48 / 13 → 1 / 36 / 1s（附带）

| step「起服务并请求首页」 | before（`sleep 8` + curl） | after（`package_smoke.py`，#376 35031461863 / #377 35031790918） |
|---|---:|---:|
| ubuntu 3.13 / 3.14 | 13 / 13 | 1 / 1 · 1 / 0 |
| windows 3.13 | 13 | 1 · 1 |
| macos 3.13 | 48 | 36 · 36（werkzeug 继承的 `server_bind` 反查主机名 ~36s，CI03B §9；`--timeout 120` 之内） |

不在关键路径上；CI03b 的目的是隔离与就绪判据，秒数只是附带。

### 4.6 没变的

invariants（501 → 469–531）、compat-smoke（132 → 134–146）、frontend（252 → 175–251）、posix-e2e（541 → 490–564）、macos-app-smoke（325 → 290–386）、
package 各腿、workerd、Ruff、CLA：job_seconds 都在 before 的 min–max 之内。desktop-shell 在 #374 / #376 是 41 / 91s、在 #375 / #377 是 237 / 233s——
**同一 PR 的第二次 run 命中了自己的 rust-cache**（CI02 §4「发现一」），合并组上不会有这个数。

## 5. 争抢 runner 的样本（单列，不进可比列）

| run | 谁在等 | runner_wait | 与谁同时 |
|---|---|---:|---|
| 35011613925（#375） | backend-platforms (windows, 1) 131 · windows-exe-smoke (1)/(2) 205 / 321 · package (windows) 372 · package (ubuntu 3.14) 348 · CLA 120 · workerd 149 · backend-fast (3.14, 1) 171 | 65–372 | 本 run 自己：Windows 同时要 5 台（pytest ×2 + Playwright ×2 + package）；ubuntu 同时要 13 台 |
| 35031461863（#376） | CI fast gate 176 · CI integration gate **447** · plugin-candidate 394 · package (ubuntu 3.13) 339 · 其余重型 69–223 | 69–447 | 22:36 起 #377（29 job）+ #378（17 job）同时进队 |
| 35031790918（#377） | Ruff **1380** · backend-fast (3.10,1) 1301 · (3.13,2) 1270 · desktop-shell (ubuntu) 1000 · workerd 917 · compat 756 · frontend 654 · windows-exe-smoke (2) 503 | 105–1380 | 与 #376（已在跑）+ #378（同时进队）三个 run 共 75 个 job |
| 35031800904（#378，无 full-ci） | CI integration gate 1129（deferred 的那个）· 快线各 job | — | 同上 |
| 35027644355（merge_group #357） | CI integration gate 135 | 135 | 22:32 起 #376 的 run（不是 lab：lab 跑在 tavotto-ci-01，与托管池无关） |

**扣除等待后的口径**（§2 的 q₀）：#375 1275、#376 1412、#377 1498——三个数落在 21–25 min。**这是「如果 runner 秒领」的口径，不是测量**；
merge_group 上 runner_wait 中位 2–9s（CI00 §4.2），所以合并组上的实测大概率落在这个口径附近，但本轮没有一个合并组样本能证明。

## 6. 三种演练（真实材料，不是合成）

1. **同 PR 连续事件**：每个 PR 的 `opened` run 都被几秒后的 `labeled`（加 `full-ci`）run 取消——#372：34993254893（16:10:04 created，16:11:58 cancelled）
   → 34993304390（16:10:31）；#377：35031788042（22:36:14，22:38:28 cancelled）→ 35031790918（22:36:16）。**别的 PR 不受影响**：#376 的
   35031461863（22:32:21 → 23:03:35 success）横跨了 #377 / #378 的 opened → cancelled → labeled 三个事件，一个 job 都没被取消。
   这与 CI01 §3 真值表的前两行一致（组名带 PR 号）。**没做的**：PR run 与 merge_group / push main 之间的互不取消，本轮没有「同一时刻两者都在跑」的样本
   （CIP-008 保持 not_run）；`ready_for_review` / 去掉标签 的行为（CIP-009）没有样本。
2. **多 PR 并行**：22:32–23:20 三个 run 同时跑（#376 / #377 / #378，75 个 job）→ 争抢数字见 §5。**新的瓶颈就是账户并发上限**（API 不给数，
   CI00 §4.3 / CI04 §4 已记）：三个 PR 同时进队时快线反馈从 19 min 退到 41 min（#377 fb 2467s），而 job 自身时长没变。
3. **合并组与 lab nightly 同时**：35027644355（merge_group，21:48:17 → 22:45:54，success）与 35028176892（Lab Qualification，schedule，
   21:54:17 → 22:48:55，success；`qualify` 跑在 `tavotto-ci-01` 21:54:35 → 22:48:54）同时在跑，互不影响——合并组的 job 全在托管 runner 上，
   lab 只占 -01。合并组那次 gate 等了 135s 的 runner，时间点是 22:45，对应 #376 的 run 在 22:32 进队，不是 lab。

## 7. 剩余瓶颈（按贡献排）

1. **账户并发上限 / 托管 runner 领取等待**（§5、§6.2）：多 PR 同时进队时是第一瓶颈，job 自身已经不是。停 stacked PR 的「同时推」习惯、或量到上限再决定扩容量（CI04 §4 第 3 步）。
2. **`backend-platforms (windows)` 的两片 1002–1438s**：资格关键路径的候选；两片不平衡（最多差 399s），按 Windows 的 junit 重算权重表可以把最慢片压到 ~1200s（CI03A §6）；再往下要么 3 片、要么处理 CI00 §6 列的 Windows 特慢用例（三条 `InvMix` 合计 232s）。
3. **Windows 并发**：一个 full-ci run 自己同时要 5 台 Windows（#375 上 Windows 领取 131–372s）。合并组被队列串行化时问题小（CI00 §4.2 Windows 领取中位 3s），PR 上会撞。
4. **缓存作用域**（CI02 §4.1）：合并组上三类缓存 0% 命中、每候选写 1.8 GB；desktop-shell 冷 233 vs 暖 41s、workerd 冷 54 vs 20s 都在 after 样本里看得见（#374 vs #375）。修法 (a) 归用户拍板。
5. **backend-fast 最慢片 ~1100s 决定反馈**：3 片或按 Linux junit 重平衡是下一刀；不在本轮。

## 8. runner 分钟（每个 run 真正执行的 job_seconds 之和，不含排队）

| | ubuntu | windows | macos |
|---|---:|---:|---:|
| before 29 个合并组中位（CI00 §4.2） | 92.7 | 66.2 | 35.3 |
| 同日 6 个合并组对照 | 119.6–135.2 | 57.8–74.6 | 31.9–38.1 |
| #374 / #375 / #376 / #377 | 127.7 / 124.8 / 128.2 / 127.9 | 70.6 / 69.1 / 72.9 / 68.8 | 37.8 / 39.0 / 36.9 / 37.7 |

after 与同日对照在同一区间：pytest 分片多付的一次 install + collection（每档 +50…+100s）、Playwright 两片各建一次产物（+150s）与去 `--with-deps`（−400s）互相抵掉。
**没有用更多 runner 分钟换时间**；换来的是并行度。

## 9. CI 侧分片完整性（CIP-016 / CIP-017 的 CI 半边）

[`evidence/ci05/shards/check_ci_shards.py`](evidence/ci05/shards/check_ci_shards.py) → [`ci_shard_check.json`](evidence/ci05/shards/ci_shard_check.json)，
退出码 0。解析器与 `tests/support/shard.py` / `scripts/ci/playwright_shard_check.py` **刻意不同源**（第二把尺子）。

- **pytest**：run 35007730894 的 10 个 `pytest-*-shard{1,2}` artifact（manifest 里 `git_head` = `fd2b2164`，即那次 run 的 `refs/pull/374/merge`）。
  五条腿（Linux 3.10 / 3.13 / 3.14、macOS、Windows）逐腿：junit 的 (classname, name) 集合两片**不交**、并集 **4686 == manifest `nodeids_total`**、
  每片 junit 条数 == `nodeids_selected`（2184 / 2502）、每片 junit 出现的文件集合 == manifest `selected_files`（89 / 91）、两片 manifest 对全集的描述一致。
  五条腿的并集按路径分隔符归一后**逐条相同**（Windows 腿有 2 条参数 id 里的 `WindowsPath` 打成反斜杠，原样列在 `raw_union_differences_vs_reference`），
  且与本机 CI03a 的 `--collect-only`（`evidence/ci03a/collect_full.txt`，4686 条）**逐条相同**——CI 与本机两侧独立。
  skip 数按腿不同（Linux 35+24 / 32+23、macOS 28+17、Windows 51+26），failure 0。manifest 原件在 [`shards/manifests_35007730894/`](evidence/ci05/shards/manifests_35007730894/)，
  并集清单 [`nodeid_union.txt`](evidence/ci05/shards/nodeid_union.txt)。
- **Playwright**：run 35011613925（CI03c 首个 CI run）与 35031790918（CI02，不带 `--with-deps`）的 `playwright-shard-check-windows-shard{1,2}`：
  两片 `--list` 不交、并集 == 全集 151 条（chromium 109 / webkit 23 / chromium-en 19）、两台机器各自打的 `list_all.txt` **逐字节相同**（两个 run 之间也相同）、
  两片 `check.json` 的 `ok` 都 true 且计数与独立解析一致。**「消费真实产物」半边**：这两个 run 的 `windows-exe-smoke (1)` / `(2)` 各自跑完
  PyInstaller → 三条断言 → 冒烟①②③ → 自验 → e2e，全 success（§3 表；job 时长 1076 / 945 与 796 / 670）。
- **负例**（[`shards/negative_cases.txt`](evidence/ci05/shards/negative_cases.txt)，9 条，先断言变异落地再看退出码）：删一条 testcase → rc 1；
  shard2 的 junit 抄 shard1 的（一份报告充数）→ rc 1；一条用例两片都有 → rc 1；manifest `nodeids_total` −1 → rc 1；同一 junit 里一条出现两次 → rc 2；
  Playwright 片 1 少一条（Total 同步改）→ rc 1；`check.json` ok=false → rc 1；片 2 清单抄片 1 的 → rc 1；清单里一行认不出 → rc 2。

## 10. 负例清单（05 §3 的九条：每条「怎么证明会红」）

证据类型三种：**CI 真红过**（引 run / job）、**本机反证过**（引文档 §）、**not_run**（写清要开什么样的一次性坏 PR 才能证——本轮不做）。

| # | 05 §3 | 证据类型 | 证据 |
|---|---|---|---|
| 1 | 缺一个 pytest / Playwright shard，或重复一份报告充数 | CI 真红 + 本机 | **CI**：35004450721 `backend-platforms (windows-latest, 2)` failure → `CI integration gate` failure（分片红 = 整个 job id 非 success，Gate 闭集没动）；34994534095 a1 windows-exe-smoke cancelled → Gate failure。**本机**：漏片 rc 4（CI03A §4 D①a；拿掉并集检查后同一变异静默漏 46 条——D①b 证明检查是载荷）；Playwright 漏片 / 两片重叠 rc 1（CI03C §7.1 N1 / N2）；本文 §9 的 9 条（报告充数 rc 1）。**not_run**：在 CI 上把 matrix 轴改成 `[1, 1]` 看两个 job 都绿而 Gate 也绿——只有静态合同（`test_pytest_shards_agree_between_the_matrix_and_the_command`，CI03A 变异 C01）挡这一层；要证得开一个坏 PR 看它的 backend-fast 红 |
| 2 | 构建 artifact 来自另一个 SHA / target / recipe 或被篡改 | 本机 | CI02 §5 / CIP-011：`plugin_stage.py verify --source-sha` 用 checkout 的 HEAD、`--content-digest` 用清单 digest；`tests/test_plugin_stage.py`（错 SHA / 改一字节 / 少多文件 / 改过的清单各红）。**not_run**：CI 上没有制造过坏 artifact（要开一个 PR 让 frontend 上传错 SHA 的 zip，看 plugin-candidate 红） |
| 3 | 真类型错误却用空 tsc 报绿 | 本机 | CI02 §3 / `evidence/ci02/tsc_mutations.json`：src / e2e 植入类型错误 `pnpm build` rc 2；references 去掉 e2e 后 rc 0（T3——所以合同钉 references 集合）。**not_run**：CI 上没有推过一个带类型错误的 PR 看 frontend 红 |
| 4 | 多 job 同端口 / 目录串图，cleanup 误杀别的 job | 本机 | CI03B §5 / §6：桩的 11 种 fail-mode + 人为抢占端口的真产品复现（lease_lost → 换号 → rc 0）；终止只对自己 setsid 的组。**not_run**：同机双实例真并发要 CI04 的多 runner 主机（CIP-018 / 019） |
| 5 | 分片使需要真实资源的测试全部 skip | CI + 本机 | **CI**：§9 五条腿的 skip 数按 os 不同（35+24 / 51+26…）与 CI00 记录的形状一致，没有一片 skip 数异常；`plugin-candidate` 在每个 after run 都 success（有产物时 `tests/test_plugin_candidate.py` 不许 skip）。**本机**：CI03a `compare_sets.json` skipped 27+15 == 42 == 全量。**没有**做「删掉产物看它红」的 CI 演练 |
| 6 | PR 运行要求可信 lab 标签，或 PR 模板带入长期发布凭据 | 本机（静态） | CI04 §2.4：`TestRunnerTrustZones` 变异 15/15（PR 事件的 workflow 派到 `tavotto-lab` / `self-hosted` → 红；挡合并不挡执行）。**not_run**：动态半边（同仓库分支 PR 加一行 runs-on 会不会真派到 -01）要管理员（ADMIN_HANDOFF A-1 / A-3），本轮不开这种 PR |
| 7 | PR 新 push 取消了 merge / main / release，或旧 pending 挤掉必须保留的候选 | 部分 CI | **CI**：§6.1——同 PR 的 opened run 被 labeled run 取消，别的 PR 的 run 不受影响（两对样本）。**not_run**：PR push 与 merge_group / push main / release 同时刻的样本本轮没有（CIP-008 / 010） |
| 8 | 字体 / 科学栈 / 浏览器版本漂移被当产品回归 | 本机 | 本轮没动这一层：CompatBench 的六类结论（`environment_dependency` 单列）与基线纪律未改（`.github/AGENTS.md` 验证链）；backend-fast 未钉 matplotlib 的事实记在 `coverage_ledger.json` 的 known_limitations。**没有**新增负例 |
| 9 | 新 gate 缺输入 / 错状态 / 错事件组合却返回成功 | CI + 本机 | **CI**：35004450721 / 35024490379 / 35028309531 三个 run 的 `CI integration gate` 在一个分片红时 failure；34994534095 a1 在 windows-exe-smoke cancelled 时 failure；35031800904（无标签）deferred 而不是 success-with-heavy。**本机**：`tests/test_aggregate_gate.py`（merge_group / full-ci 下 deferred 是 ConfigError；skipped / cancelled / missing 一律拒）、`TestHeavyLaneDependencies::test_a_red_backend_fast_still_blocks_the_merge_even_when_every_heavy_job_is_green`（真判定器）。本轮没有改判定器 |

## 11. 回退演练（本机，一次性 worktree）

[`evidence/ci05/rollback/rollback_drill.py`](evidence/ci05/rollback/rollback_drill.py) → [`rollback_drill.json`](evidence/ci05/rollback/rollback_drill.json)。
每条回退在一个 `git worktree add --detach <tmp> a174eb61` 上按该 PR 文档写的方式改 ci.yml，跑 `actionlint` + 六个合同测试文件
（`test_merge_queue_workflows / test_ci_baseline / test_support_matrix / test_e2e_leg_topology / test_pytest_shard / test_playwright_shard_check`），
记录退出码与红掉的用例，然后 `git worktree remove --force`；跑完 `git worktree list` 里没有残留。对照 R0（未改）217 passed。

| 回退 | 改动 | actionlint | 合同测试 | 红掉的用例（回退时要一起改的） |
|---|---|---:|---|---|
| R1 CI03a | 两个 job 去掉 `shard` 轴；pytest 命令去掉 `--shard=…` / `--shard-manifest=…`；分片证据 artifact 名去掉片号（conftest 钩子留着） | 0 | 3 failed / 214 passed | `TestGates::test_pytest_shards_agree_between_the_matrix_and_the_command[backend-fast]` / `[backend-platforms]`；`test_ci_baseline.py::test_the_real_ci_yml_yields_the_two_gates_and_their_closed_sets`（它断言 backend-platforms 的 matrix 有 shard 轴） |
| R2 CI03c | 删 `name:` 与 `strategy:`；`pnpm e2e` 去掉 `${{ matrix.projects }}`；浏览器安装写回 `chromium webkit`；artifact 名去掉片号；删自验步骤与其 artifact（step 级 timeout 留着） | 0 | 8 failed / 209 passed | `TestPlaywrightShards` 六条（partition / engines / e2e 从 matrix 取参 / 自验在 e2e 前 / artifact 按片 / job id 与 Gate 闭集）；`TestBuildReuseAndCaches::test_windows_installs_browsers_without_with_deps_and_posix_keeps_it`（整条命令被钉着）；`test_ci_baseline.py::test_expression_display_names_map_back_to_the_job_id` |
| R3 CI01 | 四个重型 job 的 `needs` 加回 `backend-fast`（plugin-candidate 不动） | 0 | 4 failed / 213 passed | `TestHeavyLaneDependencies::test_heavy_jobs_do_not_wait_for_the_backend_fast_verdict` / `test_heavy_jobs_keep_the_frontend_prescreen`；`TestPackageSmokeIsolation::test_the_job_shape_and_the_gate_closed_set_are_unchanged`（CI03b 钉了 `package` 的 needs）；`test_ci_baseline.py::test_the_snapshot_is_the_workflow_the_fixture_runs_executed_under`（它以「HEAD 已没有那四条边」为前提） |
| R4 CI02 | Windows 两片的 `playwright install` 加回 `--with-deps` | 0 | 1 failed / 216 passed | `TestBuildReuseAndCaches::test_windows_installs_browsers_without_with_deps_and_posix_keeps_it` |
| CI03b | 恢复 `/tmp/smoke` + `--port 5199 … &` + `sleep 8` + 两条 curl + 两行 env，删上传步骤（CI03B 抬头写的回退） | **not_run** | — | 预期红：`TestPackageSmokeIsolation` 七条（它们钉的正是新形状）；本轮没有演练 |

三条结论：yml 层面每条回退都是几行、actionlint 都过；**回退时合同测试必红**——它们钉的就是新形状，回退 PR 必须连测试一起改，红的清单在上表；
**Gate 闭集与判定器在任何一条回退里都不用动**。**实机回退（回退后在 CI 上重跑同一候选、旧报告不复用）not_run**：不能 push。

## 12. 验证命令与退出码（2026-09-16，worktree `ci-foundation`，本 PR 的树）

| 命令 | 退出码 |
|---|---:|
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `.venv/bin/python -m pytest tests/test_ci_baseline.py tests/test_docs_references.py tests/test_merge_queue_workflows.py` | 0（见 CI_HANDOFF.md §验证 的条数） |
| `evidence/ci05/analyze_all.sh`（7 组 `ci_baseline.py analyze`） | 0 ×7 |
| `evidence/ci05/compare.py` | 0 |
| `evidence/ci05/shards/check_ci_shards.py …`（§9） | 0；9 条负例各 1 / 2 |
| `evidence/ci05/rollback/rollback_drill.py`（§11） | R0 0；R1–R4 pytest 1（预期红）、actionlint 0 |
| 变异：`ci_baseline.py` 的 `^分片证据` 规则去掉 → `test_every_named_step_in_the_live_workflow_has_an_execution_category` | 1（还原后 0） |
| 逐个解析 `docs/implementation/ci-foundation/**/*.json`；`**/*.md` 相对链接存在性 | 0 / 0 |
| 实机：合入后第一个 merge_group run 的分解与 §2 对照 | **not_run**（没有合入） |
