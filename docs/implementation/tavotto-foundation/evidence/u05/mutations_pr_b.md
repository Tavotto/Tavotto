# U05 PR B · 变异反证（2026-09-21，macOS arm64，应用 `.venv` 3.13.11；事务用例的 worker 解释器 = 本机 3.11.14 / mpl 3.11.0）

脚本：session scratchpad 里同一套 `mut()`（改一处判据 → `pytest -x <用例>` → 退出码 → `git checkout` 还原 + 清 `__pycache__`）。

| # | 变异 | 文件 | 用例 | rc |
|---|---|---|---|---|
| M24 | 供应后不按真解释器重算（替身的 delta 直接装） | deprepair | test_first_plan_uses_standin_facts_then_replans_on_the_real_private_python | 1 |
| M25 | 干净机器不走私有 Python（`private_python_target` 一律 None） | deprepair | TestCleanMachine 四条 | 1 |
| M26 | 干净机器 nothing_needed 不建环境 | deprepair | test_nothing_needed_still_builds_the_environment | 1（用例第一版的脚本 import matplotlib——替身已装为空时任何第三方 import 都是 ready，撞不上那条分支 → 变异绿；改成只用标准库的脚本后红） |
| M27 | 单包修复只在 `creates` 时问私有 Python（Codex #464 第二轮 P2） | deprepair | test_single_package_repair_on_an_existing_environment_still_offers_the_private_base | 1 |
| M28 | provision 进来之前已取消不拒绝（第二轮 P2） | privatepython | test_a_cancellation_set_before_provisioning_starts_is_honoured | 1 |
| M29 | `.part` 打不开归成离线（第二轮 P2） | privatepython | test_an_unwritable_download_dir_is_a_write_error_not_offline | 1 |
| M40 | 重算前不比输入指纹（Codex #475 P1 的形状：下载期间 requirements 多一行、脚本多一个 import → 顶着旧 plan_id 装进去） | deprepair | test_inputs_changed_during_the_download_are_stale_and_install_nothing | 1（变异下作业 done、日志里 pip 装了 tavotto-test-beta——正是要防的那件事） |
| M41 | 指纹不含文件字节（只有声明意图） | depplan | TestInputsDigest（script-import / local-module / 字节改动）三条 | 1 |
| M42 | 指纹不含声明意图（只有文件字节；requirements.txt 本身不在扫描文件里） | depplan | TestInputsDigest（requirements / pyproject）两条 | 1 |
| M43 | 扫描不报跟进过的本地模块文件（只有脚本） | importscan | TestInputsDigest（替身 / 真事实同指纹的 files 断言、local-module）两条 | 1 |
| M44 | 基础解释器探测写回不看世代（reset 之后才结束的旧探测把重置前的答案写回——`test_dependency_repair` 之后跑 TestPrivateBase 的顺序依赖红，先于本 PR 就在） | deprepair | test_a_probe_finished_after_a_reset_does_not_write_the_stale_answer_back | 1 |
| M45 | 取消句柄在起线程之后才登记（U04 C 合同 ① 在 `downloading_python` 段） | deprepair | test_cancel_right_after_the_acknowledgement_never_starts_the_download | 1（`cancel_status` 回 not_found） |
| M46 | `prepare()` 拿锁之前不看事件 + 供应进来之前不看事件 | deprepair + privatepython | 同上 | 1（作业跑过了头，结果里多出 generation） |
| M47 | 只去掉供应进来之前那一处预检 | privatepython | 同上 | **0**——`prepare()` 的预检先拦住了；那一处由 PR A 的 `test_a_cancellation_set_before_provisioning_starts_is_honoured` 单独钉（冗余保证，见 mutations_pr_a M28） |

13/14 红（M47 是预期的冗余保证，另有用例钉着）（M30–M39 编号在 PR A 的 `mutations_pr_a.md`）。三条 HTTP 场景的负例在各自用例里（FO25 是 FO24 的反面：无缓存要求下载；FO26 篡改 → hash 不符 + 旧 active 原样）。
