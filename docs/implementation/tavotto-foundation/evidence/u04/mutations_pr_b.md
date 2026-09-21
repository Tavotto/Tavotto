# U04 PR B · 变异反证（2026-09-21，macOS arm64，应用 `.venv` 3.13.11；worker 解释器 = 本机 3.11.14 / mpl 3.11.1）

脚本：session scratchpad `u04_mutate_b.py`（每条：改一处判据 → `pytest -x tests/test_dependency_transaction.py -k <用例>` → 退出码 → 还原 + 清 `__pycache__`）。
真事务用例真建 venv、真跑 pip（离线 wheelhouse）、真起 worker 自检。

| # | 变异 | 文件 | 用例 | rc |
|---|---|---|---|---|
| B1 | 自检不过也切 active | deprepair | test_selftest_or_consistency_failure_keeps_the_old_generation | 1 |
| B2 | pip check 不过也切 active | deprepair | 同上 | 1 |
| B3 | 关键 import 失败不拦 | deprepair | test_key_import_failure_after_install_is_not_activated | 1 |
| B4 | pip 失败也切 active（不抛） | deprepair | test_resolver_conflict… / test_missing_wheel_and_bad_hash… | 1 |
| B5 | 提交点后仍接受取消 | deprepair | test_three_packages… / test_single_package_repair… | 1 |
| B6 | 换代收掉旧代 worker（shutdown=True） | deprepair | test_old_generation_survives_while_a_worker_uses_it | 1 |
| B7 | 退役不看在不在用 | managedenv | test_retire_unused… / test_old_generation_survives… | 1 |
| B8 | 退役也删 active | managedenv | test_retire_unused_keeps_active_and_in_use | 1 |
| B9 | hash mismatch 归成一般失败 | deprepair | test_hash_mismatch_has_its_own_code / test_missing_wheel_and_bad_hash… | 1 |
| B10 | 需求文件直接写原串 | deprepair | test_plan_files_are_reserialised_and_reject_anything_else | 1 |
| B11 | 需求文件不过形状关 | deprepair | 同上 | 1 |
| B12 | stale 指纹不判 | deprepair | test_stale_plan_is_refused_when_the_environment_changed | 1 |
| B13 | 磁盘不足不拦 | deprepair | test_disk_low_is_refused_before_building | 1 |
| B14 | native 租约不拦 | envlease | test_active_native_session_blocks_the_transaction_and_is_not_killed | 1 |
| B15 | blocked 的计划也能绑定执行 | deprepair | test_declared_conflict_stops_before_any_install / test_second_open… | 1 |
| B16 | 用户 venv 也并入 adapter | deprepair | test_in_place_install_into_the_users_venv_without_adapter | 1 |
| B17 | python_of 不看代的状态 | managedenv | test_mark_incomplete_targets_the_active_generation | 1（脚本第一版 `-k` 指错用例（register/activate 两条量不到这一维）→ 改指 mark_incomplete 那条后红；用例本身早就在） |

17/17 红；还原后 `tests/test_dependency_transaction.py` 30 passed（rc 0）。
