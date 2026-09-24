# ADR 0056：U02 runtime_spike——uv + python-build-standalone 的私有 Python 准备路径技术证明

日期：2026-09-20 · 状态：**Accepted（技术证明；不启用任何能力）**；2026-09-22 起 spike 代码**已退役**——结论由 [0063 私有 Python 供应](0063-private-python-provisioning.md) 接进产品（`engine/privatepython.py`，PR #475），`scripts/dev/u02_spikes/` 的 runtime 半边、`tests/test_foundation_u02_runtime.py` 与证据 workflow `foundation-u02-spikes.yml` 随 `chore/retire-u02-runtime-spike` 删除；`evidence/u02/runtime/` 保留，本文所述的文件路径是历史记录
相关：[0019 受控依赖修复](0019-controlled-dependency-repair.md)、[0044 系统解释器候选](0044-system-interpreter-as-repair-candidate.md)、
[0053 U01 合同](0053-foundation-contracts-and-preparation.md)、[0055 render_spike](0055-render-spike.md)；
`packaging/runtime-lock.json`（schema 2）、`packaging/AGENTS.md`「内置渲染 runtime」；实施包 D05（U05 不造第二套安装器）、
`phases/U05_private_python.md`、`SOURCES.md` [W3]。

## 裁决摘要

| 问题 | 裁决 | 证据 |
|---|---|---|
| provisioner（只选一条） | **uv 0.12.17**，取自 PyPI 的 wheel（按平台钉 sha256，从 wheel 里取出 `uv` 二进制），MIT OR Apache-2.0。只用 `uv venv --python <私有解释器>` 与 `uv pip install --offline --no-index --find-links --require-hashes`；**不用** `uv python install`（那会引入 uv 自己的解释器来源与目录约定，与锁文件成第二套真相） | `runtime_spike.UV_WHEELS`、report step `provisioner.uv_from_pinned_wheel` |
| 私有完整 Python 来源 | **python-build-standalone 20260814 · CPython 3.13.15 · install_only**——macOS 目标**直接读 `packaging/runtime-lock.json`**（版本 / build / 三元组 / URL / sha256 单一出处）；锁里没有的 Linux 与 Windows-pbs 在 spike 里补一张同形状的表（`PBS_EXTRA`，sha256 来自该 release 的 SHA256SUMS），只服务 spike | `python_source()`、`tests/test_foundation_u02_runtime.py::test_macos_python_source_is_read_from_the_runtime_lock_not_duplicated` |
| 安装位置 | 全部在 `engine.config.data_dir()` 之下：`runtimes/<name>-<sha256 前 12 位>`（**按内容命名、不可变**：staging 目录解包 → 真起一次 → `os.replace` 原子改名；同一份字节永远同一个目录、已在就复用；换版本是新目录就位后才切 `active.json`，**已在用的 runtime 永远不被 rmtree**；指针文件 tmp + `os.replace`，从不半写）、`envs/<venv>`、`tools/uv-<ver>`、`downloads/`、`wheelhouse/`、`uv-cache/` | step `isolation.everything_under_data_dir`、`verify.imports_and_everything_under_private_dir`；快用例 `test_replacing_a_runtime_never_deletes_the_one_in_use_and_only_switches_the_pointer` / `test_active_pointer_is_replaced_atomically` |
| 不改系统 | `HOME` / `USERPROFILE` 指到空目录整个跑完后仍为空；`PATH` 前后相同；`UV_NO_CONFIG=1` / `UV_NO_ENV_FILE=1` / `UV_CACHE_DIR=<data_dir>/uv-cache` / `PYTHONNOUSERSITE=1`；不碰系统 Python、不写 shell 配置；Windows 注册表本机无法量（见 §4） | step `isolation.home_untouched_and_path_unchanged` |
| 无下载授权不联网 | 安装步骤在**死代理**（`HTTP(S)_PROXY=http://127.0.0.1:9`）+ `--offline --no-index` 下成功；**对照两条**：空 wheelhouse 必失败（证明来源是 wheelhouse），不带 `--offline` 装一个不在 wheelhouse 的包必被代理挡住（证明代理真在挡网） | steps `install.*` |
| 坏 hash 不执行 | 篡改归档 / 登记错的期望值 → `HashMismatch`，**解释器执行计数不增、runtimes 目录不变、不存在半个 staging** | steps `negative.*`、`tests/test_foundation_u02_runtime.py` |
| embeddable vs 完整 Python | 锁文件的 windows-amd64 embeddable zip **静态检查**：有 `python313._pth`、`python313.zip` 里 541 条**没有** `venv/` 与 `ensurepip/`（也没有 tkinter）→ `python.exe -m venv` 在它上面注定失败；**运行时证据要 Windows 目标**（CI dispatch 的 windows-latest 腿在 embeddable 上真跑 `-m venv` 看它失败，并用 pbs 的 Windows install_only 走完整条链） | step `embeddable.static_inspection` |
| 目标 | 本机 macOS arm64 15/15；Linux / Windows 由 `foundation-u02-spikes.yml`（dispatch + spike 文件 paths 过滤的 pull_request，非 required）提供，run 号见交接文件 | `evidence/u02/runtime/report-macos-arm64.json` |
| 启用 | **不启用**任何能力（`plan.json` `new_default_capabilities_enabled: []`）；U05 接入产品时复用 U04 的环境计划 / 事务 / 验证（D05） | — |

## 1. 实测（macOS 14+ arm64，本机 2026-09-20；纯标准库脚本，用主仓库 `.venv` 的解释器跑，写入全部在临时 `TAVOTTO_DATA_DIR`）

```sh
PYTHONPATH=scripts:src .venv/bin/python -m dev.u02_spikes.runtime_spike \
    --out docs/implementation/tavotto-foundation/evidence/u02/runtime --keep     # 退出 0，ALL OK 15/15
```

（进 git 的 `report-macos-arm64.json` 是评审处置后按同一条命令重跑的那份，只多了 `--data-dir` 指向复用了首轮
 hash 校验过的下载缓存的临时目录——`target.resolved.via` 记着这一点；首轮走 `engine.config.data_dir()` 的落点
判据同样在 CI 三腿的 report 里。）

| step | 结果 | 关键数字 |
|---|---|---|
| `target.resolved` | macos-arm64；来源 = 锁文件 | pbs `cpython-3.13.15+20260814-aarch64-apple-darwin-install_only.tar.gz`，sha256 `7d50bb42…c5c6a8c5` |
| `provisioner.uv_from_pinned_wheel` | `uv 0.12.17 (635500036 2026-09-18 aarch64-apple-darwin)` | wheel 17 MB，二进制 37 MB，wheel 内含 LICENSE-APACHE / LICENSE-MIT |
| `python.provisioned_atomically` | `runtimes/cpython-3.13.15/bin/python3`；`active.json` 指向它 | 归档 25 MB → 解开 68 MB |
| `wheelhouse.downloaded_with_pinned_hashes` | six 1.17.0 / tabulate 0.9.0 / sortedcontainers 2.4.0（与 `dependency_declarations` 夹具同一组） | 80 KB |
| `venv.created_from_private_python` | `envs/spike-venv`（uv 建，不需要 `venv` 模块） | — |
| `install.offline_from_wheelhouse_with_dead_proxy` | 退出 0 | 0.09 s，`--require-hashes` |
| `install.negative_empty_wheelhouse_fails` | 退出 1：`six was not found in the provided package locations` | — |
| `install.network_blocked_control` | 退出 2：`Connection refused (os error 61)`（uv 重试 3 次共 9.8 s） | `requests` 没被装上 |
| `verify.imports_and_everything_under_private_dir` | `six/tabulate/sortedcontainers` 版本对；`sys.prefix` / `base_prefix` / `executable` / 全部 `sys.path` 都在 data_dir 下；`3.13.15` | — |
| `verify.base_prefix_is_the_private_runtime` | `base_prefix == runtimes/cpython-3.13.15`（realpath） | — |
| `negative.tampered_archive_refused_before_any_execution` | HashMismatch；执行计数 1 → 1；runtimes 目录不变 | — |
| `negative.wrong_expected_hash_refused_before_any_execution` | 同上 | — |
| `embeddable.static_inspection` | `has_pth=true, has_venv_module=false, has_ensurepip=false, has_tkinter=false` | zip 11 MB |
| `isolation.home_untouched_and_path_unchanged` | 新文件 0；PATH 不变 | — |
| `isolation.everything_under_data_dir` | 顶层：downloads / empty-wheelhouse / envs / runtimes / tools / uv-cache / uv-cache-empty / wheelhouse | 合计 ≈ 180 MB（含 77 MB 下载缓存） |

快用例（`tests/test_foundation_u02_runtime.py`，不联网）：`hashcheck` 的四条（篡改 / 错期望 / `.part` 只在校验后改名 / 软链接逃逸）；钉法单一出处；用**本地假归档**（会打印 JSON 的 `python3` 脚本）走 staging → 真起 → 原子改名 → `active.json`；坏 hash 那条路上执行计数为 0、不存在最终目录；起不来的解释器不发布、不留 staging、不写 `active.json`。

## 2. 为什么这样选

* **只有一条 resolver**：uv 同时承担 venv 创建与 wheel 安装；不自制 resolver、不并行保留 pip 那一条（产品里 `deprepair` 用 pip 的那部分是 ADR 0019 / 0038 的既有事实，U04 / U05 决定怎么接，本 ADR 不动它）。
* **uv 走 PyPI wheel 而不是 GitHub release 二进制**：PyPI 的 digest 与我们钉其它 wheel 的方式同源，`pip download` / 校验 / 解 zip 三步全是标准库；GitHub release 的 `.tar.gz` 也可以，但多一种归档形状。
* **pbs 与锁文件同源**：D05 说 U05 不造第二套安装器，所以 macOS 的来源就是 `runtime-lock.json` 里那一份，spike 不另抄一遍；Linux / Windows-pbs 目标锁文件里没有，spike 表只是补位，U05 接入时要么把它们抬进锁（schema 加目标），要么明确 Linux 不做私有 Python。
* **Windows 的私有 Python 应是 pbs 的 install_only，不是 embeddable**：embeddable 没有 `venv` / `ensurepip`（§静态检查），而 uv 虽然能在它上面建 venv（uv 自己写 `pyvenv.cfg`），但那条路的「完整性」建立在 uv 的行为上，与「完整 Python」的承诺不是一回事——U05 的 Windows 产物若继续用 embeddable 当 worker runtime（`packaging/AGENTS.md` 的现状）、用 pbs 当私有准备的 base，两者分工要写清。这一条在 §4 里标 **待 Windows 目标**。
* **staging + 按内容命名的不可变目录 + 原子切指针**：[W3] 说 venv 不可移动，所以最终目录在创建前就固定，只切指针；半个解包永远不会叫最终名字。第一版在 `os.replace` 前 `rmtree(final)`——Codex（#455）指出那会让正在用它的消费者看到解释器消失、且 rmtree 与 replace 之间失败就永久丢掉可用 runtime；改成目录名带 sha256 前缀：同字节复用、异字节另建，旧目录不动，只有指针换（指针本身也 tmp + replace）。

## 3. 反证

* 篡改归档 / 错期望值：`HashMismatch` 在解包之前抛出（`verify_sha256` 在 `provision_python` 的第一行，拿不到返回值就没有下一步），执行计数不动、目录不动（spike 与快用例各证一次）。
* 空 wheelhouse：离线安装必失败——「刚才装成功」不能来自别处。
* 死代理对照：不带 `--offline` 的联网尝试被拒（`os error 61`），证明死代理不是摆设。
* 起不来的解释器（`exit 7` 的假 python）：不发布、不留 staging、不写指针。
* 快用例的变异：去掉 `verify_sha256` → 两条坏 hash 用例红；去掉原子改名（直接解到最终目录）→ `.staging` / 最终目录存在性断言红；退回「同名 rmtree 再 replace」→ 换版本用例红（旧目录与标记文件消失）；指针直接 `write_text` → 原子指针用例红（`os.replace` 失败时磁盘上剩半个）。

## 4. 明确没做 / 仍缺的目标

* **Windows / Linux 运行时证据已由 CI 腿取得**（run 35507598899，交接文件「其它目标」表）：windows-latest 上 pbs `x86_64-pc-windows-msvc` install_only 起得来、uv 建 venv + 离线装 + 四条负例全过，**embeddable 真跑 `python.exe -m venv` 退出 1 `No module named venv`**；ubuntu-latest 上 pbs `x86_64-unknown-linux-gnu` 15/15。仍没量的：Windows **注册表**前后快照（spike 不读不写注册表，但没有这一步的证据）、`py.exe` launcher 配置；U05 接产品时补。这仍是 spike 证据，不是启用资格（06 §2）。
* 下载去重 / 消费者租约 / GC / 取消 / 磁盘配额 / 断点续传 / 代理与凭据配置 / 应用重开后的半环境处理（FO-025 ~ FO-028）——U05 的产品实现，本 spike 不做。
* 签名 / 公证（pbs 的 Mach-O 逐个 codesign）、最终安装器——不在此 gate（D04）。
* 科学栈 wheel（numpy / matplotlib）的离线安装：本 spike 用三个纯 Python 小包证明路径；大 wheel 的体积 / 时间在 U05 量。
* `install.network_blocked_control` 让 uv 重试了 3 次（9.8 s）——产品里判 offline 要更快（`--retries 1` 之类），ADR 0038 已有同一条教训。

## 5. 后果

* 加了：`scripts/dev/u02_spikes/runtime_spike.py`（纯标准库）、`hashcheck.py`（与 render_spike 共用）、`tests/test_foundation_u02_runtime.py`、`evidence/u02/runtime/report-macos-arm64.json`。
* 没加：产品代码、锁文件改动、新的运行时依赖；`packaging/runtime-lock.json` 一字未动（spike 读它、不改它）。
* U05 拿走：uv 的钉法与三条子命令、staging / 原子发布 / active 指针的形状、四条负例、embeddable 的静态事实与「Windows base 用 pbs」的建议、Linux / Windows-pbs 的 sha256（进锁文件时要重新从 SHA256SUMS 核一次）。
