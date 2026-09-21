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

6/6 红。三条 HTTP 场景的负例在各自用例里（FO25 是 FO24 的反面：无缓存要求下载；FO26 篡改 → hash 不符 + 旧 active 原样）。
