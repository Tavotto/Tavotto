# CI03a · pytest 按文件分 2 片（2026-09-16）

- 改动对象：`tests/support/shard.py`（新）、`tests/support/shard_weights.json`（新）、`tests/conftest.py`（两个钩子）、
  `tests/test_pytest_shard.py`（新）、`.github/workflows/ci.yml`（`backend-fast` / `backend-platforms`）、
  `tests/test_merge_queue_workflows.py` / `tests/test_ci_baseline.py` / `tests/test_support_matrix.py`（合同测试按新形状改）。
  实现 commit `064bc541`，收尾 commit 见 §5（叠在 CI01 `79c5aa38` / CI00 `37fb89a1` / 源码基线 `8b95256c` 之上）。
- **改了什么**（ci.yml 非注释行）：两个 job 的 `strategy.matrix` 从 `include` 列表改成轴（`python: ["3.10", "3.13", "3.14"]` × `shard: [1, 2]`；
  `os: [macos-latest, windows-latest]` × `shard: [1, 2]`），不在轴上的那一维写成字面量（`runs-on: ubuntu-latest` / `python-version: "3.13"`）；
  pytest 命令改成 `python -m pytest --shard=${{ matrix.shard }}/2 --shard-manifest="${{ runner.temp }}/shard-manifest.json" --junitxml "${{ runner.temp }}/junit.xml" --durations=50 -rs`；
  每个 job 加一步 `if: always()` 的 `upload-artifact`（`pytest-<job>-<os>-<python>-shard<K>`，`retention-days: 7`）。
- **没改什么**：两个 Gate 的 `needs` / `--required` 闭集、`scripts/ci/aggregate_gate.py`、`timeout-minutes`（40 / 60）、`fail-fast: false`、
  任何 `if:`、顶层 `concurrency`、ruleset / runner / 凭据；`_lab-qualification.yml`（lab 与 release 共用）、`nightly.yml`、
  `desktop-tauri.yml` 的 pytest 命令一个字不动（`test_unsharded_pytest_lanes_stay_unsharded` 钉住）。没有 `-n`，没有 xdist，没动任何产品断言或 skip 条件。
- **本轮没有任何真实 CI run**（不能 push）。下面的数字全部来自本机 macOS；Linux / Windows 的平衡与隔离归 CI 侧的 PR run。
- 回退：matrix 去掉 `shard` 轴 + 命令去掉 `--shard=… --shard-manifest=…`（钩子留着无害：不带 `--shard` 时不动 collection，§4 D⑤）。

## 1. 设计与判据的主语

分片**在同一进程 collection 之后**做（`tests/conftest.py::pytest_collection_modifyitems`，`trylast`——pytest 自己的 `-m "not slow"`
反选是 `tryfirst`，先于它生效），不是按目录切命令行：每个 shard 进程都看见完整的 collection，算出**全部 N 片**，自验之后才把
不属于自己的 deselect 掉（走 `pytest_deselected`，摘要如实报 `N deselected`）。

| 环节 | 主语 | 出处 |
|---|---|---|
| 分组 | `item.path` 相对 rootdir 的 POSIX 路径（`tests/x.py`），**不拆文件**（29 个文件有 module 级 fixture，`CI_BASELINE.md` §7） | `conftest._shard_file_of` |
| 权重 | `tests/support/shard_weights.json`：CI00 的 `evidence/pytest/durations_by_file.csv`（146 个文件，本机 macOS 2026-09-15）；**表里没有的文件按表中各值的第 75 百分位（5.7s）估**——新写的重文件不会被当成零，也不会因为计时表没它而漏掉 | `shard.load_weights` / `shard.default_weight` |
| 分配 | 贪心（LPT）：文件按 (权重 desc, 路径 asc) 排，依次放进当前累计最轻的片，同轻按片号小的；同一 collection 在任何机器上得到同一分配 | `shard.assign` |
| 自验 | **nodeid 集合**：并集 == 全集、两两不交、每片非空、无重复；任一条不成立 → `ShardError` → `pytest.UsageError`（rc 4）。不是计数：两片各漏一条再各多一条，计数照样对得上 | `shard.verify` |
| 证据 | `--shard-manifest=PATH`：`{git_head, shard, shards, files_total, files_selected, nodeids_total, nodeids_selected, weights_source, default_weight, unknown_files, per_shard_estimated_seconds, per_shard_files, per_shard_nodeids, selected_files, platform, python}`；**只作证据，不作判定输入** | `conftest._write_shard_manifest` |

`--shard-manifest` 不带 `--shard` 也是 rc 4：要证据却没分片是半套配置，静默写一份「全集 = 第 1/1 片」的 manifest 会被拿去当分片证据。

**两个选项必须写成 `--opt=值`**（本机实测，`tests/test_pytest_shard.py::test_the_manifest_path_may_already_exist_when_written_with_equals`）：
它们在 `tests/conftest.py` 里注册，而 pytest 预解析时把未知选项的下一个 token 当路径去找 conftest——`--shard-manifest PATH` 在 PATH
**已存在**、命令行又没有测试路径时（正是 CI 命令的形状），只加载 PATH 所在目录的 conftest，`tests/conftest.py` 没加载，整条命令
rc 4「unrecognized arguments」。`=` 形式让整个 token 进 extras，不产生假路径。托管 runner 的 `RUNNER_TEMP` 每次是新的，CI 自己永远
撞不上，所以合同测试钉 `=` 形式（`test_pytest_shards_agree_between_the_matrix_and_the_command`，变异 C11 / C12 打红）。

## 2. 漏片兜底链（谁在哪一层挡）

1. **进程内自验**（每个 shard 进程）：并集 ≠ 全集 / 有交集 / 某片空 / 重复 → rc 4。它能看见「我算出的 N 片」，看不见「别的 job 有没有跑」。
2. **静态合同**（`tests/test_merge_queue_workflows.py::TestGates::test_pytest_shards_agree_between_the_matrix_and_the_command`）：
   matrix.shard 轴必须恰好是 `1..N`，命令里的 `--shard=${{ matrix.shard }}/N` 的 N 与轴长度是同一个数，artifact 名带片号，两个选项是 `=` 形式。
   轴写成 `[1, 1]`、或轴 `[1, 2]` 而命令 `/3`，每个进程都绿、第 2 / 第 3 片却没人跑——只有这一层钉得住。
3. **matrix 语义 + Gate 闭集**（未改动）：两片里任一片不 success，`needs.backend-fast.result` 就不是 success；`ci-fast-gate` 的
   `--required` 闭集里 backend-fast 仍是那一个 job id，`aggregate_gate.py` 对非 success 一律判失败。job id 不变，显示名变成
   `backend-fast (3.10, 1)` / `backend-platforms (windows-latest, 2)`；required context 只有三个 Gate 名字，**仓库设置不需要重新登记任何检查**
   （`tests/test_ci_baseline.py::test_sharded_display_names_map_back_to_the_job_id` 钉住基线采集器仍映射得回 job id）。
4. **五档不变**：`test_backend_split_keeps_all_five_tiers` 改为读轴（os / python 不在轴上时取字面量），仍逐档等于 Linux 3.10 / 3.13 / 3.14 + macOS / Windows 3.13；
   `tests/test_support_matrix.py::test_backend_fast_runs_both_ends_of_the_tested_range` 同步改读 `python` 轴。

## 3. 本地实测（本机 macOS arm64 Mac mini 12 核，HEAD `064bc541`，树在跑的期间没动）

命令、日志与产物在 [`evidence/ci03a/`](evidence/ci03a/README.md)；驱动是 `evidence/ci03a/driver.sh`（A 全量 → B 两片顺序 → C 两片并发，
`nohup … &` 起的——这个起法本身踩了一个坑，见 §3.4）。全集：**4685 条 nodeid / 180 个文件**（CI00 的 4599 = 4557 + 42 是 `8b95256c` 的树；
差值 = CI00/CI01 新增 `tests/test_ci_baseline.py` 23 条 + `test_merge_queue_workflows.py` +4 条 + 本轮 `tests/test_pytest_shard.py` 55 条 + 合同测试 +4 条，
逐文件对得上；收尾 commit 又加 1 条，所以 `collect_full.txt` 是 4686）。

### 3.1 集合级比对（`evidence/ci03a/compare_sets.json`，`compare_sets.py` 退出码 0，14 条 check 全 true）

主语是 junit 的 `(classname, name)` 集合，不是计数：

- s1 ∪ s2 == full（4685 = 2184 + 2501），s1 ∩ s2 = ∅，两片非空；c1 == s1、c2 == s2（集合）；
- passed / skipped / failed 计数逐片相加等于 full（4641 + 42 + 2 = (2156 + 27 + 1) + (2485 + 15 + 1)）；**并发跑的逐条 outcome 与顺序跑完全相同**；
- manifest 的 `selected_files` == junit 里出现的文件集合（89 / 91），`nodeids_selected` == 各片 testcase 数，`nodeids_total` == full 的 testcase 数，
  并发跑的 manifest 与顺序跑的（除 platform / python / git_head 之外）逐字段相同，四份 manifest 的 `git_head` 都是 `064bc541`。

collection 层（`evidence/ci03a/compare_collect.txt`，退出码 0）：`--collect-only` 的 nodeid 集合 4686 = 2184 ∪ 2502，交集 0。

### 3.2 计时与资源（`evidence/ci03a/timing_table.json`，来自 `/usr/bin/time -l` 与 junit）

| run | passed / skipped / failed | 墙钟（real） | user / sys | 峰值 RSS | junit 用例时间合计 | rc |
|---|---|---:|---:|---:|---:|---:|
| A full（不带 `--shard`） | 4641 / 42 / 2 | **1124.3s**（18:44） | 756.7 / 97.8 | 581.6 MiB | 1121.9s | 1 |
| B s1（`--shard=1/2`，顺序） | 2156 / 27 / 1 | 601.9s | 397.3 / 58.4 | 582.3 MiB | 600.1s | 1 |
| B s2（`--shard=2/2`，顺序） | 2485 / 15 / 1 | 540.6s | 370.7 / 41.8 | 440.1 MiB | 536.2s | 1 |
| C c1（`--shard=1/2`，与 c2 同时） | 2156 / 27 / 1 | 619.7s | 412.5 / 62.9 | 582.7 MiB | 617.8s | 1 |
| C c2（`--shard=2/2`，与 c1 同时） | 2485 / 15 / 1 | 538.0s | 374.4 / 39.7 | 455.3 MiB | 536.0s | 1 |

- 并发相对顺序：c1 +17.8s（+3.0%），c2 −2.6s；两片同时跑的峰值 RSS 合计 1038 MiB。并发**没有任何一条因并发而红**（红的两条与顺序跑、全量跑是同两条，见 §3.3）。
- 墙钟：全量 1124s → 两片并发 max(619.7, 538.0) = 620s（本机 −45%）；顺序两片合计 1142s，比全量多 18s（两次进程启动 + collection）。
- rc 全是 1：两条红都不是分片造成的（§3.3），且它们在 full / 顺序 / 并发里落在**同一条 nodeid**上。

### 3.3 两条红的定性

| nodeid | 落点 | 定性 | 处置 |
|---|---|---|---|
| `tests/test_windows_regressions.py::test_support_probes_reconfigure_stdout_to_utf8` | full / s2 / c2 | **本轮真缺陷，仓库门禁抓到的**：`tests/support/shard.py` 有 `__main__` 入口、打 `ensure_ascii=False` 的中文 JSON，却没钉 UTF-8 stdout；Windows 管道下第一行中文就 UnicodeEncodeError | 已修（`main()` 开头 `reconfigure(encoding="utf-8", errors="replace")`，与 `tests/support/` 其它探针同写法）；单跑该用例 + `tests/test_pytest_shard.py` 绿（§5） |
| `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`（90.4s 超时） | full / s1 / c1 | **测试运行环境的坑，不是产品缺陷也不是分片缺陷**（§3.4） | 不改代码；写进已知坑 |

### 3.4 本地实测方法的已知坑：后台驱动会让信号类用例假红

驱动 `driver.sh` 是 `nohup driver.sh &` 从**非交互 shell**起的。POSIX 规定无作业控制时后台命令的 SIGINT / SIGQUIT 置为 `SIG_IGN`，且信号
处置跨 `exec` 继承到 pytest → `tavotto run` → 用户脚本整棵树；Python 启动时 SIGINT 已是 `SIG_IGN` 就不装 `KeyboardInterrupt` 处理器，
于是用例里 `os.killpg(…, SIGINT)` 对谁都不起作用，`communicate(timeout=90)` 到点，用例自己 kill（`returncode -9`）。双向验证（退出码判，
`evidence/ci03a/sigint_*.txt`）：

| 起法 | `python -c "import signal;print(signal.getsignal(signal.SIGINT))"` | 单跑该用例 |
|---|---|---:|
| 前台（普通工具调用） | `<built-in function default_int_handler>` | rc **0**（`sigint_a_foreground.txt`） |
| `nohup zsh -c '…' > x.log 2>&1 &`（与驱动同形） | `1`（= `Handlers.SIG_IGN`，3.11+ 的 IntEnum `str()` 打数值） | rc **1**，同一个 `TimeoutExpired`（`sigint_b_nohup.txt`） |

CI runner 的步骤是前台进程，不受影响；CI00 基线在同一台机器前台跑时这条 0.74s 通过。**以后本机跑全量 / 分片对照时，信号类用例
要么前台跑，要么起驱动前显式 `trap - INT` / 用 `setsid`（macOS 没有 `setsid`，用 `zsh -c 'set -m; …'` 或前台）。**

### 3.5 估计秒数 vs 实测

| 片 | 文件数 | nodeid 数 | 权重表估计 | 顺序实测 junit 合计 | 顺序墙钟 | 并发墙钟 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 89 | 2184 | 661.5s | 600.1s（其中 90.4s 是 §3.4 的假红；去掉 ≈ 509.7s） | 601.9s | 619.7s |
| 2 | 91 | 2501 | 661.5s | 536.2s | 540.6s | 538.0s |

估计与实测同一台机器、权重同源，接近是**预期内**的，不是分片算法准的证据；34 个不在表里的文件按 5.7s 估（清单在 manifest 的 `unknown_files`），
两片实测差 ≈ 27s（去掉假红后 s2 重 5%）。**Linux 3.10 / 3.13 / 3.14 与 Windows 的真实平衡要等 CI 的 junit artifact**——CI00 记录 Windows 腿比
macOS 慢一倍、且慢在不同的用例上（`CI_BASELINE.md` §6），两片在 Windows 上很可能不平衡；重算方法见 §6。

### 3.5 CI 首跑（PR #374，run `35004450721`，2026-09-15 17:57Z，`full-ci`）

第一份 CI 侧真实样本（job 墙钟秒，`gh api …/runs/35004450721/jobs`）：

| leg | 第 1 片 | 第 2 片 | 改前中位（CI00 §4.2） |
|---|---:|---:|---:|
| backend-fast 3.10 | 821 | 1100 | 1865 |
| backend-fast 3.13 | 946 | 1015 | 1745 |
| backend-fast 3.14 | 1058 | 997 | 2090 |
| backend-platforms macOS | 900 | 684 | 1485 |
| backend-platforms Windows | 1169 | 1362 | 2443 |

`CI fast gate` 在 run 创建后 **18.5 分钟**出结论（改前 29 个合并组中位 32.7 分钟）。Windows 第 2 片
比第 1 片重 193s、Linux 3.10 第 2 片重 279s——权重表来自 macOS 本机，各 os 的真实平衡要用这次
上传的 junit artifact 重算（§6）。

这一跑 `backend-platforms (windows-latest, 2)` **红了一条**：`tests/test_pytest_shard.py::
test_more_shards_than_files_is_a_usage_error_not_an_empty_green_run`——不是分片错了，是这条新用例
只钉了父进程的解码器（`encoding="utf-8"`）没钉子进程的编码器：Windows 上子进程 stderr 是 cp1252
管道，pytest 把编不出的 `UsageError` 整行转成 `\uXXXX`，`"片为空" in out.stderr` 恒假。修法是给
子进程钉 `PYTHONIOENCODING=utf-8`（实测它优先于 `PYTHONUTF8`）；本机复现：父进程
`PYTHONIOENCODING=cp1252` 时修复前 1 failed、修复后 56 passed。同族教训：编码要钉两侧。

## 4. 负例与退出码（真实变异，`evidence/ci03a/negative_cli_cases.txt` / `negative_d1_union_check.txt`）

| # | 变异 / 输入 | 期望 | 实测 |
|---|---|---|---|
| D①a | `shard.assign()` 丢掉排序里最后一个文件（并集检查还在） | rc 4 | rc 4，`ERROR: --shard 1/2：分片并集 ≠ 全集：漏掉 46 条 ['tests/test_normalize.py::…` |
| D①b | 同上，再把 `verify()` 的「并集 == 全集」检查拿掉 | 静默漏片（证明是这条检查在挡） | rc 0；两片各报「nodeids …/4639」而 collection 是 4685 条，46 条没人跑——**检查是载荷** |
| D② | `--shard 3/2` | rc 4 | rc 4，`--shard 的 K 必须在 1..N 之内（N ≥ 1）` |
| D③ | `--shard 1/999` | rc 4（某片为空） | rc 4，`第 180/999 片为空（全集 179 个文件、4626 条 nodeid）` |
| D④ | 权重表改成坏 JSON | rc 4（不是静默用默认权重） | rc 4，`权重表 … 不是合法 JSON` |
| D⑤ | 不带 `--shard`：`--collect-only` 输出与装钩子前逐字节比 | 相同 | 相同（4626 条 nodeid；唯一差异是摘要行的墙钟 `in 2.96s` vs `3.22s`，去掉该后缀后 md5 `312fb927…` 两边一致；stderr 逐字节相同：`evidence/ci03a/collect_identity.txt`） |
| D⑥ | `--shard-manifest` 不带 `--shard` | rc 4 | rc 4，`--shard-manifest 只在带 --shard 时有意义` |
| D⑦ / D⑧ | `--shard 0/2` / `--shard abc` | rc 4 | rc 4 |
| D⑨ | `--shard-manifest PATH`（空格形式）、PATH 已存在、命令行无测试路径 | rc 4（§1 的预解析坑） | rc 4，`unrecognized arguments: --shard --shard-manifest`；`=` 形式同条件 rc 0（`test_the_manifest_path_may_already_exist_when_written_with_equals`） |

D②～D④ 的输入形状用的是空格形式（当时 manifest 路径不存在，坑没露头）；结论不受形式影响。

### 单元测试的变异反证（`evidence/ci03a/mutations.json`，22 条）

`tests/test_pytest_shard.py` 的每条负例写完立刻变异一次：先断言目标串恰好出现一次 → 变异 → 清 `__pycache__` +
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` → pytest 退出码判 → 按备份还原并核 md5。**22/22 KILLED**（M01～M21 + M01b）：
parse_spec 的范围 / 正则、assign 的 N ≥ 1、plan 的片号范围、verify 的四条（全集重复 / 片间交集 / 并集 ≠ 全集 / 空片）、
load_weights 的六类（坏 JSON / 负数·NaN·∞ / 布尔·字符串 / 键形状 / 缺出处 / 空表）、default_weight（p75 → 均值、→ 0）、
weights_from_junit 的对不回文件、conftest 的两处 `UsageError`（改成静默 return）、漏片（assign 丢一个文件）、重叠（每片拿全部文件）。

一条值得写下的：M01 单删 `parse_spec` 的范围检查时，子进程用例 `--shard 3/2 → rc 4` **不红**——`plan()` 自己还有一层片号范围检查
（守直接调用的入口）。两层各守各的入口是有意的，但「单删一层不红」正是「冗余的保证杀不死」那一族；所以 M01b 把两层一起删，它必须红
（实测红 7 条）。同族的另一处在写代码时已合掉：`parse_spec` 原来「N ≥ 1」与「1 ≤ K ≤ N」是两条，后者蕴含前者，删掉前者永远不红，现在只有一条。

### ci.yml 合同测试的变异反证（`evidence/ci03a/mutations_ci.json` 10 条 + 收尾 2 条）

**12/12 KILLED**：shard 轴 `[1, 2] → [1, 1]`、命令 `/2 → /3`、删 backend-platforms 的 shard 轴、轴 `[1, 2, 3]` 而命令 `/2`、
`python-version 3.13 → 3.12`、python 轴去掉 3.14（五档 + support-matrix 两处红）、artifact 名去掉片号、删 `--shard-manifest`、
nightly 的 pytest 加上 `--shard 1/2`、`runs-on` 改成 `${{ matrix.os }}` 而没有 os 轴（空档）；收尾：`--shard-manifest=` / `--shard=` 改回空格形式各一条（C11 / C12）。

## 5. 验证命令与退出码（2026-09-16，worktree，收尾 commit 之前的树）

| 命令 | 退出码 |
|---|---:|
| `/opt/homebrew/bin/actionlint .github/workflows/ci.yml` | 0 |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `.venv/bin/python -m pytest tests/test_pytest_shard.py tests/test_merge_queue_workflows.py tests/test_ci_baseline.py tests/test_aggregate_gate.py tests/test_ci_tooling.py tests/test_docs_references.py tests/test_windows_regressions.py tests/test_support_matrix.py` | 0（288 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」） |
| 同上再加 `tests/test_source_hygiene.py tests/test_merge_queue_ruleset.py tests/test_release_workflow_contract.py tests/test_e2e_leg_topology.py tests/test_cla_workflow_contract.py tests/test_ci_qualification.py tests/test_update_chain_gates.py` | 0（534 passed, 2 skipped，另加 tests/test_tracked_paths_are_windows_safe.py tests/test_generated_untracked.py） |
| A / B / C 三组全量（§3.2，commit `064bc541`） | 1 / 1 / 1 / 1 / 1——全部是 §3.3 那两条，集合级比对退出码 0 |
| `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan` 前台单跑 | 0 |
| 实机：PR 上 backend-fast 3 × 2 片、full-ci 下 backend-platforms 2 × 2 片的 manifest artifact 并集 | **not_run**（不能 push） |

## 6. 权重表怎么更新

表只影响两片平不平衡，不影响覆盖（并集自验与表无关；表坏了是 rc 4 而不是错分）。CI 的每一片都把 `junit.xml` + `shard-manifest.json`
上传成 `pytest-<job>-<os>-<python>-shard<K>`（7 天）。把**同一 os、同一次 run** 的两片 junit 一起喂进去：

```sh
python tests/support/shard.py --from-junit junit-shard1.xml junit-shard2.xml \
  --source "run <id> backend-platforms windows-latest shard1+2" \
  --measured "2026-xx-xx windows-latest 3.13 HEAD <sha>" > tests/support/shard_weights.json
```

`classname` → 文件的映射从最长前缀往回试、取 `root` 下真实存在的 `.py`；对不回去的 testcase 直接抛（喂错树的 junit 不会静默变成半张表）。
口径与 CI00 的 CSV 同为 setup+call+teardown（`junit_duration_report=total`），只是 CSV 只累 ≥ 5 ms 的阶段，所以重算出来的数会略大。
一张表只能代表一个 os；现在只有一张（macOS），Linux / Windows 各自不平衡时要么按最慢的那个 os 重算，要么再加一维——**那是下一个 PR 的决定，
不在这里做**。

## 7. 已知边界（如实）

- **CI 侧 Linux / Windows 的平衡未测**：权重是本机 macOS 的样本；Windows 腿的慢用例分布不同（`CI_BASELINE.md` §6），两片可能一重一轻。
  `timeout-minutes` 刻意不动（40 / 60），等 PR run 的实测再调。
- **并发只在本机同机测过一次**（§3.2）：两片是同一台机器上的两个进程，共享 CPU / 内存 / 磁盘 / HOME / 字体缓存；CI 上每片是独立 VM，条件更松。
  同机一次没撞 ≠ 隔离已验证——CIP-018 / 019 / 020 / 021 保持 `not_run`，理由各写在 `acceptance.json`。
- **本机三组跑都不是全绿**（§3.3）：一条是本轮真缺陷（已修，单跑绿，没有重跑三组——修的是 `main()` 入口，不碰 collection / 分配 / 权重表），
  一条是驱动起法的坑（§3.4，前台单跑绿）。集合级比对的结论不依赖这两条是红是绿：它们在三组里落在同一 nodeid 上。
- **没做 xdist**、没有 `-n`、没改任何产品断言、没改任何 skip 条件。
- 分片证据 artifact 的 `if-no-files-found: warn`：pytest 在 collection 之前就死（比如 conftest import 失败）时两份文件都不存在，
  这一步只警告——判定仍由 needs 结论承担，artifact 从来不是判定输入。
- 权重表键是「相对仓库根的 POSIX 路径」；Windows 上 `item.path.relative_to(rootpath).as_posix()` 也是这个形状（静态推断，CI 侧待 PR run 证实）。
- `--shard-manifest` 的 `git_head` 先问 `git rev-parse HEAD`，再退回 `GITHUB_SHA`，都没有就 `unknown`；只是标签，不参与判定。
