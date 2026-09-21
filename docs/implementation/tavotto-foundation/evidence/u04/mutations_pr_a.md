# U04 PR A · 变异反证（2026-09-21，macOS arm64，应用 `.venv` 3.13.11）

脚本：session scratchpad `u04_mutate_a.py`（每条：改一处判据 → `pytest -x tests/test_dependency_plan.py -k <用例>` → 退出码 → 还原 + 清 `__pycache__`）。
结论按退出码；还原后整份 `tests/test_dependency_plan.py` 退出 0。

| # | 变异 | 文件 | 用例 | rc |
|---|---|---|---|---|
| M1 | `-e` 不再 unsupported（变 unknown） | depresolve | test_recognised_but_unsupported_constructs_are_named_not_dropped | 1 |
| M2 | include 环不判 | depresolve | test_cycle_missing_and_outside… / test_self_include_is_a_cycle | 1 |
| M3 | include 越界不判 | depresolve | test_cycle_missing_and_outside… / test_symlink_escaping_the_project_is_outside | 1 |
| M4 | marker 不求值（全部当真） | depplan | test_markers_are_evaluated_against_the_target… / test_marker_false_declarations… | 1 |
| M5 | 所有组默认选中 | depplan | test_only_default_and_named_groups… / test_unsupported_in_an_unselected_group_does_not_block | 1 |
| M6 | `try:` 体内的 import 当无条件 | importscan | test_every_context_is_classified / test_possible_imports_are_listed_but_never_installed | 1 |
| M7 | 本地模块不识别 | importscan | test_local_modules_in_script_dir… / test_ready_plan_installs_only_what_is_needed… | 1 |
| M8 | adapter 也并进用户 venv | depplan | test_project_venv_target_does_not_carry_adapter_constraints | 1 |
| M9 | unsupported 不再 blocked | depplan | test_blocked_by_unsupported_declaration_in_a_selected_group | 1 |
| M10 | hash 模式缺 hash 不判 | depplan | test_hash_mode_installs_the_whole_closure_and_requires_every_hash | 1 |
| M11 | requirement_string 直接用原文 | depresolve | test_requirement_string_is_reserialised_from_the_parsed_structure | 1 |
| M12 | 没有 TOML 解析器时静默当空 | depresolve | test_without_a_toml_parser_pyproject_and_pep723_are_unsupported | 1 |
| M13 | 冲突判据关掉 | depresolve | test_only_definite_contradictions_are_reported / test_blocked_by_conflict_keeps_the_conflict_visible | 1 |
| M14 | unknown import 也进 requirements（猜同名） | importscan | test_mapping_priority… / test_ready_plan_installs_only_what_is_needed… | 1 |
| M15 | stdlib 表用宿主而不是目标 | depplan | test_plan_classifies_stdlib_by_the_target_interpreter_not_the_host | 1（第一次跑绿：原用例只钉 importscan 层的参数，补 plan 层用例后红） |

`ALL 15 MUTATIONS RED`，还原后 rc=0。

## Codex #459 处置后追加（2026-09-21）

| # | 变异 | 用例 | rc |
|---|---|---|---|
| M16 | include 的 seen 只按文件（换组 / 换成约束再 include 被吞掉） | test_the_same_file_included_as_another_group_or_as_a_constraint_counts_again | 1 |
| M17 | 读不了 / 超上限的声明文件当空 | test_unreadable_or_oversized_declaration_files_are_unsupported_not_empty | 1 |
| M18 | 闭区间单点交集当空（`>=1` + `<=1`） | test_only_definite_contradictions_are_reported | 1 |
| M19 | `nothing_needed` 压过 `blocked` | test_blocked_wins_even_when_nothing_is_missing | 1 |

4/4 红；还原后 rc 0。
