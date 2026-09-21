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

## 第二轮：Codex #461 五条处置（B18–B27）

脚本：session scratchpad `u04_mutate_b2.py`（每条：改一处判据 → 针对性用例 → 退出码 → 还原 + 清 `__pycache__`；
用例文件 `test_dependency_transaction.py` + `test_dependency_plan.py`）。

| # | 变异（对应评审） | 文件 | 用例 | rc |
|---|---|---|---|---|
| B18 | `fresh_generation` 撞在册的代也用原名（P1 重建两次删 active） | managedenv | …::test_fresh_generation_never_reuses_a_registered_name / …::test_rebuild_twice_never_touches_the_active_directory | 1 |
| B19 | `register_generation` 不拒绝重新登记 active / ready 的代 | managedenv | …::test_fresh_generation_never_reuses_a_registered_name | 1 |
| B20 | hash 模式也把 adapter + 账上的写进需求文件（P1 hash + adapter） | deprepair | …::test_generation_requirements_in_hash_mode_are_the_lock_only / …::test_hash_locked_managed_generation_installs_only_the_lock（真 pip `--require-hashes`） | 1 |
| B21 | hash 模式下锁没钉 adapter 也不 blocked | depplan | …::test_hash_mode_on_the_managed_target_requires_the_lock_to_pin_the_adapter / …::test_hash_locked_… | 1 |
| B22 | 锁钉在 adapter 范围外不算冲突 | depplan | …::test_hash_mode_on_the_managed_target_requires_the_lock_to_pin_the_adapter | 1 |
| B23 | 集合仍按选中解释器量（`install_facts` 不生效；P1 事实按目标量） | depplan | …::test_install_facts_measure_the_set_against_the_target_not_the_current_interpreter / …::test_managed_target_from_a_project_venv_installs_the_full_needed_set | 1 |
| B24 | 受管目标没有环境时不给 fresh 事实（旧行为） | deprepair | …::test_managed_target_from_a_project_venv_installs_the_full_needed_set | 1 |
| B25 | 执行前不重算事实 digest（P2） | deprepair | …::test_stale_plan_is_refused_when_the_target_packages_changed | 1 |
| B26 | 受管：自检后不再看取消（P2） | deprepair | …::test_cancel_accepted_during_the_selftest_is_honored | 1 |
| B27 | 用户 venv 原地：自检后不再看取消 | deprepair | …::test_cancel_accepted_during_the_selftest_is_honored_in_place | 1 |

10/10 红（B24 第一次「目标串不在」：处置里又把那一行拆成了多行，改准落点后红）；还原后针对性用例 12 passed；
PR B 相关套件（transaction / repair / e2e / package_management / package_lookup / project_env / first_open_environment /
preparation_api / import_architecture / error_codes / dependency_plan / execution_receipt）EXIT 0。

顺带两条**事实**：① 第一版 `test_rebuild_twice…` 用异步 `rebuild_managed_async` + `wait_for(REBUILD_PROGRESS_ID)`，
第二次轮询立刻拿到的是**上一次**的终态（进度 id 固定）——`seen == []` 而状态 done，用例什么都没量到；改成同步
`rebuild_managed()`。② 「新的一代已装为空」第一版把 matplotlib 也列进要装的集合（脚本 import 了它、项目没声明），
离线 CI 里 pip 装不了——adapter 必然带上的 distribution 要算作「已有」（`fresh_venv_facts(provided=adapter_distributions())`），
离线夹具照样把这层放宽说出来（嫁接的宿主栈 = 生产上的 adapter）。
