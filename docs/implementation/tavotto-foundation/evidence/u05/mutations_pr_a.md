# U05 PR A · 变异反证（2026-09-21，macOS arm64，应用 `.venv` 3.13.11；事务用例的 worker 解释器 = 本机 3.11.14 / mpl 3.11.0）

脚本：session scratchpad `u05/mutate_a.py`（每条：改一处判据 → `pytest -x <用例>` → 退出码 → `git checkout` 还原 + 清 `__pycache__`；结论用退出码）。
`test_private_python.py` 经本地供应服务 + 假归档跑真实状态机；`test_private_python_transaction.py` 真建 venv、真跑 pip（离线 wheelhouse）、真起 worker 自检。

| # | 变异 | 文件 | 用例 | rc |
|---|---|---|---|---|
| M01 | 下载后不比对 sha256（照单全收） | privatepython | test_corrupted_bytes_are_refused_before_any_execution / test_wrong_expected_hash_is_refused | 1 |
| M02 | 归档成员不校验（zip-slip 放行） | privatepython | test_archive_members_outside_the_root_are_refused | 1 |
| M03 | 起来的解释器自报版本不比对 | privatepython | test_an_interpreter_reporting_another_version_is_not_published | 1 |
| M04 | 传输循环里不看中止信号 | privatepython | test_the_last_consumer_cancelling_aborts_and_leaves_nothing | 1 |
| M05 | 最后一个消费者走了也不中止下载 | privatepython | test_the_last_consumer_cancelling_aborts_and_leaves_nothing | 1 |
| M06 | 提交前不再检查中止（起完就改名） | privatepython | test_an_abort_arriving_before_the_commit_point_is_honoured | 1（第一轮绿：中止只在传输循环里被抓，起完到改名之间那一次检查没有用例单独量——补 test_an_abort_arriving_before_the_commit_point_is_honoured 后红） |
| M07 | 退役不问 in_use（旧 runtime 一律删） | privatepython | test_retire_keeps_the_current_and_anything_a_generation_records / test_an_old_private_runtime_survives_while_a_generation_records_it | 1 |
| M08 | 退役连当前那份也删 | privatepython | test_retire_keeps_the_current_and_anything_a_generation_records | 1 |
| M09 | 磁盘配额不查 | privatepython | test_disk_quota_is_checked_before_any_download | 1 |
| M10 | 不真起就发布 | privatepython | test_an_interpreter_that_fails_to_launch_is_not_published / test_an_interpreter_reporting_another_version_is_not_published | 1 |
| M11 | 逃生门 0 不压过锁文件 | privatepython | test_offered_follows_the_lock_then_the_engineering_override | 1 |
| M12 | 缓存里对不上的归档照样复用 | privatepython | test_cached_archive_with_wrong_bytes_is_not_reused | 1 |
| M13 | python_of 连 staging 里的也认 | privatepython | test_orphan_staging_from_a_dead_process_is_never_used_and_gets_reaped | 1 |
| M14 | 有基础解释器也照样要求下载 | deprepair | test_one_authorization_provisions_python_then_builds_the_generation | 1 |
| M15 | 没明示过下载的路（重建 / 首装）也去供应 | deprepair | test_rebuild_and_package_first_install_never_download | 1 |
| M16 | 这一代不记 base_runtime | managedenv | test_one_authorization_provisions_python_then_builds_the_generation / test_retire_keeps_the_current_and_anything_a_generation_records | 1 |
| M17 | 探测链找不到 base 时不看私有 Python | managedenv | test_one_authorization_provisions_python_then_builds_the_generation | 1（第一轮绿：事务里供应完直接刷新了 base 缓存，探测链末级在同一进程里从没被问到——补「应用重开（reset_state）后 base_python 仍回私有 Python」后红） |
| M18 | 供应失败照样登记一代 | deprepair | test_offline_prepare_is_a_safe_stop_and_registers_no_generation | 1 |
| M19 | 不提供私有 Python 时也不再拒绝（静默放行） | deprepair | test_without_the_offer_no_base_is_still_managed_env_unavailable | 1 |
| M20 | 软链接目标不校验 | privatepython | test_member_validation_is_our_own_first_line | 1（第一轮绿：软链接越界被 tarfile 的 data 过滤器（第二道）挡住，第一道被删了也绿——补 test_member_validation_is_our_own_first_line 直接量第一道后红） |
| M21 | 代号不折私有 base 的 id（Codex #464 P1 的形状：锁换版本、意图没变 → 同号覆盖 active 并 rmtree） | deprepair | test_a_new_private_runtime_makes_a_new_generation_and_never_touches_the_active_one | 1（用例第一版经 create_joint_plan 建第一代 → 与重建的代号公式不同、根本不同号 → 变异绿；改成两次重建后红：`python_of` 回 None） |
| M22 | 探测链末级不看 `offered()`（能力关掉仍用磁盘上那份） | managedenv | test_a_provisioned_runtime_is_not_used_once_the_capability_is_off | 1 |
| M23 | `.part` 用固定名（两个进程互相搬走对方的半成品） | privatepython | test_two_processes_provisioning_the_same_runtime_both_succeed | 1 |

20/20 变异被抓住（17 条第一轮红；M06 / M17 / M20 第一轮绿，各补一条用例后红——三条用例已进提交 `U05（A）：三条变异反证补钉`）；
Codex 第一轮处置后再加 M21–M23，三条红。

## Codex #464 第二 / 三轮处置后的变异（M24–M29 编号归 PR B 的 `mutations_pr_b.md`）

第二轮（提交 `U05（A）Codex #464 第二轮处置`）三条修复各钉一条用例：`test_an_unwritable_download_dir_is_a_write_error_not_offline`（`.part` 打不开归 write_failed 不归 offline）、`test_a_cancellation_set_before_provisioning_starts_is_honoured`（进来之前已取消：不起线程、不挂到别人的下载上）、`test_single_package_repair_on_an_existing_environment_still_offers_the_private_base`（有 active 代、系统 Python 没了：计划仍带 `private_python`）——三条都先红后绿。

| # | 变异 | 模块 | 抓住它的用例 | 退出码 |
|---|---|---|---|---|
| M30 | 包作业 / 单包修复把代事务自己的 done 原样外露（两次 emit 之间轮询者看到没有 version 的 done；#464 backend-fast 红的形状） | deprepair | test_the_job_never_shows_done_without_its_result | 1 |
| M31 | offer 里受管目标的「可用」退回 `True if exists else managed_available()`（有 active 代就不问 base，载荷丢了） | deprepair | test_single_package_repair_on_an_existing_environment_still_offers_the_private_base（offer 侧断言） | 1（`private_python` 是 None） |
| M32 | 软链接目标不拒反斜杠 / 盘符 | privatepython | test_member_validation_is_our_own_first_line[win1/win2/win3] | 1（三条参数各红） |
| M33 | 解包时的 OSError 归 invalid_archive | privatepython | test_an_unwritable_staging_dir_is_a_write_error_not_an_invalid_archive | 1 |
| M34 | 提交点不在锁下（`_check_abort` + `os.replace` 与消费者的取消判断交错） | privatepython | test_a_cancellation_racing_the_commit_never_publishes_after_being_accepted | 1（「报了取消，目录却在」） |
| M35 | 真归档用例只摘大写代理（夹具改为两种拼法都设之后） | 测试夹具 | TestRealArchive（`TAVOTTO_PRIVATE_PYTHON_REAL=1`、无缓存） | 1（`Connection refused`；修后有缓存 / 无缓存各跑一次都 0） |
| M36 | `.part → 正式名` 被拒后不复用正式名上那份（#467 Windows 腿确定性红的形状） | privatepython | test_archive_rename_refused_while_another_process_holds_it_reuses_that_copy | 1 |
| M37 | 复用不校 hash | privatepython | test_archive_rename_refused_with_wrong_bytes_on_the_name_is_a_write_error | 1 |

M36 / M37 的用例按 Windows 语义摆场景（本机没有 Windows）：replace 被拒之前先把同一份字节落到正式名上。Windows 上的真实判据是 #467 目标腿里的 `test_two_processes_provisioning_the_same_runtime_both_succeed`——修前两次运行都红（退出 [1, 0]）。

## 真归档的工程验证（不是资格）

`tests/test_private_python.py::TestRealArchive`（`TAVOTTO_PRIVATE_PYTHON_REAL=1`，归档来自 scratchpad 缓存，hash 校验过）：真 pbs 3.13.15 供应 → `-I -c` 自报版本 3.13.15、prefix 在 `runtimes/<id>` 下 → `-m venv` → venv 里 `pip --version` 退出 0、`sys.base_prefix` == runtime 目录。2.66 s。

`engineering-chain-macos-arm64.json`（scratchpad `u05/engineering_chain.py`）：缓存齐备 + 死代理（零请求）→ 计划明示 `private_python`（cached=true、download_bytes=0）→ U04 代事务：`downloading_python` → 建 venv（base = 私有 Python）→ pip 离线装 matplotlib 3.11.1 / numpy 2.5.2 / six / fixture 包 → pip check → 关键 import → worker 自检 → 切 active；这一代 `base_runtime` = 私有 runtime 的 id、`sys.base_prefix` == runtime 目录；再独立用该 venv 跑脚本出 10.6 KB 的 PDF。19.2 s。HOME 里只有两条**已归因、非本模块**的写入（`.matplotlib` 来自既有的 `probe_environment`，`.rustup/settings.toml` 来自本机 `python -m venv` 的 ensurepip 子进程——Homebrew Python 一样）；PATH 前后不变；数据目录顶层只有 cache / environments / private-python。体积：runtime 106 MB、归档 25 MB、受管环境 187 MB。

「没有合格基础解释器」在两处都用 `bootstrap.find_base_python → None` 表达——发现链末端的输入；这是工程验证，NO_SYSTEM_PYTHON 的资格要在真实目标上取（ADR 0064）。
