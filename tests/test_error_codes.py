"""用户可见的后端错误：稳定 code + 参数，文案归前端。

约定写在 `app.py` 的 API 段首。这批用例守两件事：

1. 这些端点**真的**给了 code（漏一个的表现不是报错，而是英文界面里冷不丁
   蹦出一句中文——只有装了英文界面的用户会遇到，本地永远复现不了）；
2. 每个 code 在**两种语言**里都有文案，且占位符与后端给的 params 对得上
   （`{{path}}` 写成 `{{dir}}` 的话，界面上就是一个原样的 `{{path}}`）。

新增用户可见的失败时：app.py 给 code + params → 两份 errors.json 加文案 →
这张表里加一行。
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "tavotto" / "app.py"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"


#: 会返回用户可见 JSON 错误的模块。`engine/ai_bridge.py` 在列，是因为编码
#: Agent 的失败以 `AgentError("<code>")` 抛出、由 app.py 的一个漏斗转成 JSON
#: ——只扫 app.py 的话这批 code 一个都看不见，而看不见的门禁 = 没有门禁。
#: `engine/project_refresh.py` 与 `engine/atomicio.py` 在列，是因为它们的失败
#: 以 `RefreshError("<code>")` / `AtomicWriteError("<code>")` 抛出、由 app.py
#: 的漏斗转成 JSON——只扫 app.py 的话这批 code 一个都看不见，而看不见的门禁
#: = 没有门禁。atomicio 是 2026-08-29 补进来的：它的 `write_failed` /
#: `replace_failed` / `non_finite_number` 早就会落到用户界面上，却一直没人
#: 要求它们有英文文案。
_SOURCE_FILES = (
    "app.py",
    "security.py",
    "desktop.py",
    "engine/ai_bridge.py",
    # 「连接 AI」：`ConnectionRefused("<code>")` 由 app 的 `_connection_error` 漏斗转成 JSON
    "engine/ai_connections.py",
    # 「生成可编辑版本」（ADR 0143）：`ConvertError("<code>")` 由 app 的 `_convert_error` 漏斗转成 JSON
    "engine/ai_convert.py",
    "engine/project_refresh.py",
    "engine/atomicio.py",
    "engine/profilestore.py",
    "engine/exportreq.py",
    "engine/exportjob.py",
    # 离线教程（ADR 0039）：`TutorialError("<code>")` 由 app._tutorial_error 转成 JSON
    "engine/tutorial.py",
)

#: 「自己报得出全部 code」的模块（ADR 0031 的导出管线）。
#:
#: 正则扫源码在这两个模块上**看不见东西**：`exportreq._one_of()` 收的 code 是
#: 一个变量，`exportjob` 是 `job.error_code = …` 的赋值。所以它们各自导出一个
#: `ERROR_CODES` 元组，门禁直接读那个元组——比正则强，因为它不可能与实现漂移。
_CODE_REGISTRIES = (
    "tavotto.engine.exportreq",
    "tavotto.engine.exportjob",
    "tavotto.engine.workdir",
    "tavotto.engine.pool",
    "tavotto.engine.preparation",
    "tavotto.engine.deprepair",
    "tavotto.engine.envadvice",
    "tavotto.engine.inputremap",
    "tavotto.engine.ai_data_figure",
    "tavotto.engine.xlsxread",
    "tavotto.engine.ai_data_recipe",
    "tavotto.engine.scriptbackup",
    "tavotto.engine.scriptedit",
    "tavotto.engine.runconfig",
    "tavotto.tiffprobe",
    "tavotto.engine.texdiag",
    "tavotto.engine.projectcopy",
    "tavotto.engine.folderrepair",
    "tavotto.engine.pathignore",
    "tavotto.engine.assetimport",
    "tavotto.engine.notebookscript",
)


def _all_error_sources() -> str:
    root = ROOT / "src" / "tavotto"
    return "\n".join(
        (root / n).read_text(encoding="utf-8") for n in _SOURCE_FILES if (root / n).is_file()
    )


def _registry_codes() -> set[str]:
    import importlib

    out: set[str] = set()
    for name in _CODE_REGISTRIES:
        out.update(importlib.import_module(name).ERROR_CODES)
    return out


#: 沿既有异常漏斗识别构造调用；码仍从实际字面量取，不手工列新增码。
_ERROR_CLASSES = frozenset(
    {
        "AgentError",
        "ConnectionRefused",
        "RefreshError",
        "AtomicWriteError",
        "ConvertError",
        "ProfileStoreError",
        "ExportRequestError",
        "TutorialError",
        "DocumentError",
    }
)


@cache
def _source_tree(text: str) -> ast.Module:
    return ast.parse(text)


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_code(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if re.fullmatch(r"[a-z0-9_]+", node.value) else None
    return None


def _dict_fields(node: ast.Dict) -> dict[str, ast.AST]:
    return {
        key.value: value
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _raised_codes(tree: ast.Module) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, index = _name(node.func), 0
        if name == "__init__" and isinstance(node.func, ast.Attribute):
            name, index = _name(node.func.value), 1
        if name in _ERROR_CLASSES:
            argument = (
                node.args[index]
                if len(node.args) > index
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "code"), None
                )
            )
        elif name == "_fail" and len(node.args) > 1:
            argument = node.args[1]
        else:
            continue
        if code := _literal_code(argument):
            out.add(code)
    return out


@cache
def _declared_codes(text: str) -> set[str]:
    tree = _source_tree(text)
    out = _raised_codes(tree)
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Dict):
            value = _dict_fields(node).get("code")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_name(target) == "error_code" for target in targets):
                value = node.value
        elif isinstance(node, ast.Call):
            # Job results carry the code as a keyword (`Produced(error_code=...)`),
            # not as a response dict; it is still an emitted code.
            for keyword in node.keywords:
                if keyword.arg in {"error_code", "code"} and (code := _literal_code(keyword.value)):
                    out.add(code)
        if code := _literal_code(value):
            out.add(code)
    return out


def test_declared_codes_are_actual_ast_nodes_not_comments_or_strings():
    source = '''
# jsonify({"code": "comment_code"})
# raise DocumentError("comment_exception", "not executed")
example = 'AgentError("string_exception")'
description = """{"code": "string_code"}"""
raise engine_documents.DocumentError("actual_code", "fallback")
'''
    assert _declared_codes(source) == {"actual_code"}


@pytest.mark.parametrize("exception", sorted(_ERROR_CLASSES))
def test_declared_codes_preserve_each_exception_funnel(exception):
    assert _declared_codes(f'raise engine.{exception}("actual_code", "fallback")') == {
        "actual_code"
    }


@pytest.mark.parametrize(
    "source,expected",
    [
        ('ProfileStoreError.__init__(self, "profile_code", "fallback")', "profile_code"),
        ('DocumentError(code="document_code", message="fallback")', "document_code"),
        ('job.error_code = "assigned_code"', "assigned_code"),
        ('error_code: str = "annotated_code"', "annotated_code"),
        ('_fail(job, "job_code", "fallback")', "job_code"),
        ("jsonify({'code': 'response_code', 'error': 'fallback'})", "response_code"),
    ],
)
def test_declared_codes_keep_literal_response_and_legacy_assignment_shapes(source, expected):
    assert _declared_codes(source) == {expected}


# code → 后端会塞进 params 的键（2026-08-21 起覆盖 app.py 的**全部**字面量
# code——审计 P1-02：错误尾部不许泄漏中文，所以每个 code 都要有两种语言的
# 文案，且占位符与 params 对得上）
USER_VISIBLE_CODES = {
    "attachment_too_large": set(),
    "attachment_empty": set(),
    "attachment_invalid": set(),
    "asset_import_too_large": {"limit_mb"},
    "asset_import_unsupported_type": set(),
    "asset_import_unreadable": set(),
    "asset_import_empty": set(),
    "asset_import_not_writable": set(),
    "asset_import_disk_full": set(),
    "asset_import_write_failed": set(),
    "asset_import_not_visible": set(),
    "asset_import_name_exhausted": set(),
    "svg_asset_unsupported": set(),
    "svg_asset_export_unsupported": set(),
    "chart_engine_mismatch": set(),
    "chart_engine_unavailable": set(),
    "chart_model_invalid": set(),
    "chart_model_unsupported": set(),
    "chart_render_unsupported": set(),
    "chart_source_changed": set(),
    "svg_resource_limit": set(),
    "svg_scene_invalid": set(),
    "no_project": set(),
    "missing_path": set(),
    "mkdir_failed": {"reason"},
    "unsafe_project_name": {"name"},
    "open_project_failed": {"reason"},
    "invalid_path": set(),
    "dir_missing": {"path"},
    "pinned_full": {"max"},
    "permission_denied": {"path"},
    "read_failed": {"reason"},
    "internal_error": {"reason"},
    "backend_unavailable": {"reason"},
    "export_render_failed": {"id", "reason"},
    "invalid_document": set(),
    "package_file_missing": set(),
    "package_invalid": {"reason"},
    "package_schema_unsupported": set(),
    "figure_snapshot_unavailable": set(),
    "scan_failed": {"reason"},
    "perf_report_rejected": {"reason"},
    "perf_report_write_failed": set(),
    "user_environment_gone": set(),
    "environment_changed": set(),
    "environment_generation_required": set(),
    "environment_candidate_gone": set(),
    "environment_save_failed": set(),
    "environment_locked": set(),
    "environment_check_running": set(),
    "user_environment_incomplete": {"packages"},
    "user_environment_unverifiable": set(),
    "engine_files_incomplete": set(),
    "write_outside_roots": set(),
    "import_folder_unreadable": {"folder"},
    "import_folder_abi_mismatch": {"abi", "current", "folder"},
    "path_ignore_token_invalid": set(),
    "path_ignore_rejected": set(),
    "repair_unsupported": set(),
    "repair_token_invalid": set(),
    "repair_target_rejected": set(),
    "repair_in_progress": set(),
    "repair_not_confirmed": set(),
    "repair_cancelled": set(),
    "repair_not_elevated": set(),
    "repair_failed": set(),
    "repair_has_links": set(),
    "repair_too_large": set(),
    "export_raster_too_large": {"height", "max_pixels", "width"},
    "tex_command_missing": {"command"},
    "tex_package_missing": {"file"},
    "tex_compile_failed": {"line"},
    "tex_failed": {"error"},
    "script_not_found": {"script"},
    "script_path_outside_project": {"script"},
    "unsupported_script_type": {"script"},
    "runtime_asset_unknown": {"id"},
    "runtime_asset_has_no_original_artifact": set(),
    "runtime_cache_missing": set(),
    "runtime_asset_source_present": set(),
    "runtime_asset_in_use": set(),
    "probe_in_progress": {"script"},
    "script_name_missing": set(),
    "invalid_entry": {"entry"},
    "registry_update_failed": {"reason"},
    "settings_dir_unusable": {"key", "reason"},
    "invalid_preview_dpi": {"value"},
    "invalid_scale": {"value"},
    "invalid_profile": set(),
    "invalid_only": set(),
    "specfix_native_unsupported": {"product"},
    "invalid_patches": set(),
    "invalid_purpose": {"value"},
    "invalid_width": {"value"},
    "invalid_preview_options": set(),
    "not_parameterizable": set(),
    "sync_different_scripts": set(),
    "python_missing": set(),
    "interpreter_not_found": {"path"},
    "workdir_mode_invalid": {"mode"},
    "input_remap_chosen_invalid": {"path"},
    "input_remap_not_found_in_dir": {"name", "path"},
    "input_remap_requested_invalid": set(),
    "input_remap_rule_unknown": {"source"},
    "input_remap_changed": set(),
    "input_declaration_conflict": set(),
    "input_declaration_invalid": set(),
    "input_declaration_unavailable": set(),
    "script_is_symlink": {"script"},
    "script_hardlinked": {"script"},
    "script_readonly": {"script"},
    "script_changed_since_preview": {"script"},
    "script_backup_failed": {"script"},
    "script_replace_failed": {"script"},
    "script_backup_unknown": {"id"},
    "script_busy": {"script"},
    "script_backup_unsafe": {"script"},
    "script_edit_nothing_to_change": set(),
    "script_edit_unreadable": set(),
    "script_edit_self_check_failed": set(),
    "script_edit_token_invalid": set(),
    "script_edit_preview_stale": set(),
    "script_edit_needs_ui": set(),
    "script_restore_conflict": set(),
    "script_restore_stale": {"script"},
    "workdir_confirmation_required": set(),
    "explicit_python_unusable": set(),
    "project_python_unusable": set(),
    "preparation_plan_stale": set(),
    "no_figures_captured": set(),
    "invalid_argv": set(),
    "run_config_missing": set(),
    "run_config_secret_missing": set(),
    "run_config_unsupported": set(),
    "run_config_unreadable": set(),
    "project_scan_not_started": set(),
    "dependency_preparation_required": set(),
    "preparation_not_found": {"id"},
    "interpreter_no_matplotlib": {"path"},
    "interpreter_unsupported_python": {"path", "python_version"},
    "interpreter_worker_import_failed": {"detail", "path"},
    "interpreter_unusable": {"detail", "path"},
    "invalid_consent": set(),
    "name_missing": set(),
    "endpoint_save_failed": {"reason"},
    "endpoint_invalid": {"reason"},
    "ai_endpoint_incompatible": {"endpoint"},
    "ai_endpoint_needs_login": {"endpoint"},
    "oauth_unavailable": set(),
    "oauth_start_failed": set(),
    "oauth_flow_not_found": set(),
    "connection_not_found": set(),
    "connection_not_deletable": set(),
    "ai_start_failed": {"reason"},
    "ai_revert_failed": {"reason"},
    "ai_revert_conflict": {"script"},
    "ai_script_busy": {"script"},
    "ai_candidate_changed": set(),
    "ai_candidate_unavailable": set(),
    "ai_request_not_found": set(),
    "ai_revert_not_applicable": set(),
    "convert_disabled": set(),
    "convert_tool_forbidden": set(),
    "convert_unsandboxed_windows_only": set(),
    "convert_not_found": set(),
    "convert_not_ready": set(),
    "convert_source_changed": set(),
    "convert_context_changed": set(),
    "convert_reference_invalid": set(),
    "workspace_path_too_long": set(),
    "convert_project_changed": set(),
    "convert_candidate_changed": set(),
    "convert_outside_project": set(),
    "convert_write_failed": set(),
    "convert_name_exhausted": set(),
    "convert_no_preview": set(),
    "convert_source_language_mismatch": {"script"},
    "convert_not_awaiting_dependencies": set(),
    "convert_resume_busy": set(),
    "convert_nothing_to_realign": set(),
    "convert_realigning": set(),
    "ai_request_superseded": set(),
    "ai_supersede_conflict": set(),
    "ai_pending_unresolved": set(),
    "taf_disabled": set(),
    "data_figure_input_missing": set(),
    "data_path_outside_project": set(),
    "data_not_found": set(),
    "data_not_csv": set(),
    "data_too_large": set(),
    "data_too_long": set(),
    "data_too_wide": set(),
    "data_unreadable": set(),
    "data_decode_error": set(),
    "data_malformed": set(),
    "data_empty": set(),
    "data_header_invalid": set(),
    "data_ragged": set(),
    "data_format_unsupported": set(),
    "data_excel_unsupported": set(),
    "data_xlsx_invalid": set(),
    "data_xlsx_too_large": set(),
    "data_xlsx_unsafe": set(),
    "data_sheet_not_found": set(),
    "table_not_found": set(),
    "column_not_found": set(),
    "column_type_mismatch": set(),
    "roles_invalid": set(),
    "no_usable_columns": set(),
    "mark_not_allowed": set(),
    "no_primary_mark": set(),
    "layer_role_invalid": set(),
    "summary_missing": set(),
    "summary_misplaced": set(),
    "unit_unconfirmed": set(),
    "unit_required": set(),
    "paired_unconfirmed": set(),
    "box_violin_n_small": set(),
    "hist_density_n_small": set(),
    "fit_line_n_small": set(),
    "bar_baseline_invalid": set(),
    "bar_needs_summary": set(),
    "line_duplicate_x_unconfirmed": set(),
    "log_nonpositive": set(),
    "dual_axis_unsupported": set(),
    "facet_limit_exceeded": set(),
    "group_limit_exceeded": set(),
    "coord_not_allowed": set(),
    "bar_before_raw_points": set(),
    "duplicate_candidate": set(),
    "too_many_candidates": set(),
    "trend_too_few_x": set(),
    "paired_incomplete": set(),
    "matrix_too_large": set(),
    "composition_negative": set(),
    "composition_total_not_positive": set(),
    "composition_too_many_parts": set(),
    "ranking_too_many_items": set(),
    "ranking_needs_order": set(),
    "interval_bounds_invalid": set(),
    "trajectories_limit_exceeded": set(),
    "dose_nonpositive": set(),
    "recipe_malformed": set(),
    "recipe_unknown_key": set(),
    "recipe_unknown_value": set(),
    "recipe_layer_count": set(),
    "recipe_why_invalid": set(),
    "figure_name_exhausted": set(),
    "ai_request_unresolved": set(),
    "ai_panel_file_missing": set(),
    "ai_draft_too_large": {"limit_kb"},
    "ai_pending_limit": {"count"},
    "ai_agent_unknown": {"agent"},
    "ai_agent_disabled": {"agent"},
    "ai_agent_not_installed": {"agent"},
    "ai_agent_needs_auth": {"agent"},
    "ai_agent_not_usable": {"agent"},
    "ai_agent_install_unsupported": {"agent"},
    "ai_cli_update_unavailable": {"agent"},
    "ai_agent_login_unsupported": {"agent"},
    "ai_agent_login_failed": {"agent"},
    "ai_agent_executable_invalid": {"path"},
    "ai_agent_probe_timeout": {"path"},
    "session_auth_required": set(),
    "annotations_need_pdf": set(),
    "desktop_updater_disabled": set(),
    "file_locked": set(),
    "replay_divergence": set(),
    "artifact_source_unavailable": set(),
    "script_changed": set(),
    "source_changed": set(),
    "stale_write": set(),
    "external_change": set(),
    "layout_outside_project": set(),
    "layout_read_only": set(),
    "revision_unreadable": set(),
    "registry_reload_failed": {"reason"},
    "project_closed": set(),
    "non_finite_number": {"reason"},
    "write_failed": {"reason"},
    "replace_failed": {"reason"},
    "dir_fsync_failed": {"reason"},
    "write_back_disabled": set(),
    "write_back_warnings": set(),
    "write_back_persist_failed": {"reason"},
    "profile_bad_kind": set(),
    "profile_bad_payload": set(),
    "profile_bad_data": set(),
    "profile_bad_spec": set(),
    "profile_bad_style": set(),
    "profile_bad_json": set(),
    "profile_bad_format": set(),
    "profile_bad_schema": set(),
    "profile_bad_journal": set(),
    "profile_not_found": set(),
    "profile_read_only": set(),
    "profile_no_origin": set(),
    "profile_limit_reached": set(),
    "profile_too_large": set(),
    "profile_revision_missing": set(),
    "profile_revision_conflict": set(),
    "profile_store_unsupported_schema": set(),
    "bad_request": set(),
    "bad_scope": {"value"},
    "bad_overwrite": {"value"},
    "bad_background": {"value"},
    "bad_policy": {"value"},
    "bad_formats": set(),
    "no_format": set(),
    "unsupported_format": {"format"},
    "bad_ppi": {"value"},
    "ppi_out_of_range": {"max", "min", "value"},
    "bad_geometry": {"field"},
    "bad_filename": {"reason"},
    "bad_validation": set(),
    "bad_objects": set(),
    "bad_merge": set(),
    "bad_pad": set(),
    "missing_original": set(),
    "missing_figure": set(),
    "bad_overrides": set(),
    "name_exhausted": set(),
    "export_dir_unwritable": {"error"},
    "tmp_dir_failed": {"error"},
    "export_failed": {"error"},
    "format_failed": {"error"},
    "no_output": set(),
    "publish_failed": {"error"},
    "report_failed": {"error"},
    "report_write_failed": {"error"},
    "report_missing_payload": set(),
    "source_missing": {"figure"},
    "eps_not_for_canvas": set(),
    "script_export_diverged": set(),
    "script_export_native": set(),
    "script_export_no_savefig": set(),
    "script_export_no_script": set(),
    "script_export_not_for_canvas": set(),
    "script_export_not_python": set(),
    "script_export_run_failed": set(),
    "script_export_script_changed": set(),
    "script_export_same_file": set(),
    "script_export_unreadable": set(),
    "eps_needs_script": {"figure"},
    "eps_source_unverified": {"figure"},
    "eps_needs_live_figure": {"figure"},
    "eps_annotations_unsupported": {"operation"},
    "export_basis_mismatch": {"format"},
    "svg_source_unsupported": {"id"},
    "svg_figure_unavailable": {"figure"},
    "svg_snapshot_missing": {"id"},
    "tutorial_resources_missing": {"reason"},
    "tutorial_resources_invalid": {"reason"},
    "tutorial_copy_failed": {"reason"},
    "tutorial_locked": {"reason"},
    "tutorial_reset_incomplete": set(),
    "tutorial_layouts_changed": set(),
    "versions_unreadable": set(),
    "named_budget_exceeded": {"limit", "used"},
    "version_name_too_long": {"max"},
    "version_moment_invalid": {"moment"},
    "version_thumb_invalid": set(),
    "version_thumb_write_failed": set(),
    "tiff_unreadable": {"file"},
    "tiff_bigtiff": {"file"},
    "tiff_compression": {"file"},
    "tiff_color_space": {"file"},
    "tiff_bit_depth": {"file"},
    "tiff_sample_format": {"file"},
    "tiff_planar": {"file"},
    "project_copy_too_large": {"limit_mb"},
    "project_copy_dest_invalid": set(),
    "project_copy_dest_unwritable": set(),
    "project_copy_failed": {"reason"},
    "output_folder_read_only": {"product"},
    "interpreter_cache_denied": {"product"},
    "write_back_format_unsupported": {"format"},
    "write_back_foreign_file": {"file"},
    "write_back_multipage_pdf": {"file"},
    "artifact_rejected": {"failed", "policy"},
    "bad_inspection": {"value"},
    "script_input_not_pending": set(),
    "script_input_not_found": set(),
    "script_input_invalid": set(),
    "script_input_stream_gone": set(),
    "script_input_transcript_failed": set(),
}

pytestmark = pytest.mark.skipif(
    not (LOCALES / "zh-CN" / "errors.json").is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


def _errors(locale: str) -> dict:
    return json.loads((LOCALES / locale / "errors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", sorted(USER_VISIBLE_CODES))
def test_backend_emits_the_code(code: str):
    declared = _declared_codes(_all_error_sources()) | _registry_codes()
    assert code in declared, f"后端源码里没有发出 {code}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_code_has_text_in_both_languages(locale: str):
    table = _errors(locale).get("backend", {})
    missing = sorted(set(USER_VISIBLE_CODES) - set(table))
    assert not missing, f"{locale} 的 errors.json 缺这些 code 的文案：{missing}"
    empty = sorted(k for k, v in table.items() if not str(v).strip())
    assert not empty, f"{locale} 里这些 code 的文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_placeholders_match_the_params_the_backend_sends(locale: str):
    table = _errors(locale)["backend"]
    for code, params in USER_VISIBLE_CODES.items():
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
        params = params - DATA_ONLY_PARAMS.get(code, set())
        assert used == params, f"{locale} {code}：文案用了 {used}，后端给的是 {params}"


def test_english_texts_have_no_chinese():
    bad = {k: v for k, v in _errors("en-US")["backend"].items() if re.search(r"[一-鿿]", v)}
    assert not bad, f"英文文案里还留着中文：{bad}"


def test_error_field_is_still_there_as_the_fallback():
    """
    `error` 里的中文原文是回退，不能因为有了 code 就删掉：装着旧前端的用户、
    以及 curl 调试的人只看得到它。
    """
    failures = _fallback_failures(_all_error_sources(), USER_VISIBLE_CODES, _registry_codes())
    assert not failures, "\n".join(failures)


def test_the_export_funnel_still_carries_the_original_text():
    """导出管线（ADR 0031）的两个漏斗：请求层与作业层各一个，都要带 error 原文。

    只断言"有个 code"是不够的——两个漏斗都少写一个 `"error"`，装着旧前端的
    用户就会看到一个空白的失败提示，而 code 只在开发者工具里躺着。
    """
    app = APP.read_text(encoding="utf-8")
    # ① 请求层：`_prepare_export_job()` 把 ExportRequestError 转成 JSON
    start = app.index("def _prepare_export_job(")
    block = app[start : start + 900]
    for key in ('"error": exc.message', '"code": exc.code', '"params": exc.params'):
        assert key in block, f"_prepare_export_job() 少了 {key}"
    # ② 作业层：`_legacy_export_response()` 把作业失败转成 JSON
    start = app.index("def _legacy_export_response(")
    block = app[start : start + 1200]
    assert '"error": job.error_params.get("error")' in block, (
        "_legacy_export_response() 少了 error 原文"
    )
    assert '"code": code' in block, "_legacy_export_response() 少了 code"


def test_the_atomic_write_funnel_still_carries_the_original_text():
    """`AtomicWriteError` 那批同样只有一个漏斗（`_atomic_write_error`）。"""
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _atomic_write_error(")
    assert "exc.as_payload()" in app[start : start + 800]
    atomicio = (SRC_DIR / "engine" / "atomicio.py").read_text(encoding="utf-8")
    start = atomicio.index("    def as_payload(")
    block = atomicio[start : start + 300]
    for key in ('"error"', '"code"'):
        assert key in block, f"AtomicWriteError.as_payload() 少了 {key}"


def test_the_document_error_funnel_still_carries_the_original_text():
    from tavotto.engine.documents import DocumentError

    handler = next(
        node
        for node in _source_tree(APP.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == "_document_error"
    )
    returned_payloads = (
        node.value.elts[0]
        for node in ast.walk(handler)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Tuple)
        and len(node.value.elts) == 2
    )
    assert any(
        isinstance(node, ast.Call)
        and _name(node.func) == "jsonify"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Attribute)
        and node.args[0].func.attr == "as_payload"
        and _name(node.args[0].func.value) == "exc"
        for node in returned_payloads
    ), "DocumentError 漏斗没有交付异常的完整 payload"
    payload = DocumentError("document_test", "original fallback").as_payload()
    assert payload == {"error": "original fallback", "code": "document_test"}


def test_the_refresh_error_funnel_still_carries_the_original_text():
    """`RefreshError` 那批（统一项目刷新）同样只有一个漏斗，单独钉死它。

    漏斗给的是 `exc.as_payload()`，所以真正要守的是 `as_payload()` 里那三样
    ——只断言 app.py 那一行的话，把 `as_payload` 改成只回 `{"code": ...}`
    照样绿，而英文界面上会冒出一个原样的 code。
    """
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _refresh_error(")
    assert "exc.as_payload()" in app[start : start + 600]
    refresh = (SRC_DIR / "engine" / "project_refresh.py").read_text(encoding="utf-8")
    start = refresh.index("    def as_payload(")
    block = refresh[start : start + 300]
    for key in ('"error"', '"code"', '"params"'):
        assert key in block, f"RefreshError.as_payload() 少了 {key}"


def test_the_agent_error_funnel_still_carries_the_original_text():
    """`AgentError` 那批的 `error` 原文全靠 app.py 里那一个漏斗。

    漏斗只有一处，所以单独钉死它——上面那条对它们只能确认「异常存在」。"""
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _agent_error(")
    block = app[start : start + 600]
    assert '"error"' in block and '"code": exc.code' in block
    assert '"params": exc.params' in block


# ---------------------------------------------------------------------------
# 结构扫描（2026-08-21，审计 P1-02）：上面那张表靠人记得补；这两条不靠。
# ---------------------------------------------------------------------------
#: 明知没有 code 的位置（"<文件名>:<行号>"）。新增前先想清楚——每一条都意味着
#: 英文用户会在那儿看到中文。desktop.py:98 的「非桌面模式」404 由 ADR 0008
#: 分支整块搬进 security.py 并带上 no_session_mode，合并后这条自然消失；
#: 不在这里改它是为了不与那个分支冲突。
NO_CODE_ALLOWLIST: set[str] = {"desktop.py:98"}

#: 会返回用户可见 JSON 错误的模块（不存在的跳过：security.py 随 ADR 0008
#: 的分支进来，两个分支各自独立绿、合并后自动都在扫描范围内）
SRC_DIR = ROOT / "src" / "tavotto"
SCANNED = [n for n in _SOURCE_FILES if (SRC_DIR / n).is_file()]

#: 刻意没有文案的 code：不是用户可见的失败（前端把这类调用整个吞掉或只做
#: 分诊）。会话 guard 那几个的拒绝对象是攻击页面/畸形请求，正常界面路径由
#: main.tsx 的专用启动页兜住；401 的 session_auth_required 另有 backend 文案。
NON_UI_CODES = {
    # Conversion-run diagnostics are mapped by ai_convert._failure_check to the
    # existing UI reason run_failed; they are not HTTP response codes.
    "capture_incomplete",
    "convert_output_identity",
    "preview_render_failed",
    "redirect_area_not_clearable",
    "telemetry_rejected",
    "invalid_telemetry_event",
    "bad_nonce",
    "bad_host",
    "bad_origin",
    "bad_secret",
    "no_session_mode",
    "desktop_auth_required",
    # `GET /api/handoff/render-status` 的参数错误：只有 `tavotto open` 的轮询客户端会调，界面不展示（ADR 0131 §六）
    "bad_handoff_query",
    # `POST /api/handoff/display-ack`（重复交接的显示确认）的参数错误：调用方是前端自己的静默 fire-and-forget，不展示（ADR 0131）
    "bad_handoff_ack",
    # `POST /api/engine/render` 的 `include_figure_execution` 必须是真布尔：前端只发 true，
    # 写错是调用方/协议错误，界面不会展示（响应仍带 `error` 原文）。
    "invalid_include_figure_execution",
    # 导出作业 / 图表子进程的**状态**字面量，不是错误响应码。
    "cancelled",
    "timeout",
    # `GET /api/journal-catalog/assets/<file>`：出处卡的 `<img src>`，失败时前端只显示无图占位、不读 code（ADR 0155 §10）
    "snapshot_unavailable",
    # 一键安装的**进度**状态码，不是错误响应：它们的文案在
    # dialogs:settings.agents.install.error.*（由 pnpm i18n:check
    # 守双语齐全），不该也不能进 errors.backend 那张表。
    "npm_missing",
    "npm_failed",
    "installed_but_not_found",
    "spawn_failed",
    # #715 验收 P1：「上次开着的排版」记到了别的项目的槽位上。只在后台推送里出现，前端认它作废
    # 本机缓存（`lib/projectDocs.LAYOUT_FOREIGN_CODE`），从不显示给用户
    "layout_foreign",
    # ADR 0120：会话状态此刻读不到 / 跨进程锁拿不到（503）。同样只在后台「上次开着的」推送里出现，前端当成
    # 可重试的失败、留着待确认标记下次补推（`lib/projectDocs.pushRemote`），从不显示给用户
    "session_unavailable",
    # 候选发现（只读）：槽位目录在、却列不出来（权限 / 网络盘）→ 503。前端当成 unavailable 走重试，不折成空项目，
    # 从不把这个 code 显示给用户
    "candidates_unavailable",
    # 执行追踪（ADR 0151）Span 的错误码：只进本地 Trace 的 `error_code`，不是 HTTP 响应、不作为错误文案显示
    # （PR3 的 Waterfall 若展示它们，届时随界面一起登记双语文案）
    "agent_exit_nonzero",
    "agent_spawn_failed",
}


#: 只给前端程序用、不进文案的 params（不要求占位符）。`session`：占着脚本的那次会话 id，前端据此接上它。
DATA_ONLY_PARAMS = {"ai_script_busy": {"session"}, "eps_annotations_unsupported": {"operation"}}


def _response_dicts(text: str):
    """直接响应 dict 或同词法作用域唯一前置 dict 赋值；复杂数据流不冒充已判。"""
    tree = _source_tree(text)
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}

    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return node
        return tree

    assignments = {}
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault((scope(node), target.id), []).append(node)
    for node in nodes:
        if not isinstance(node, ast.Call) or _name(node.func) != "jsonify" or not node.args:
            continue
        payload = node.args[0]
        if isinstance(payload, ast.Name):
            preceding = [
                assignment
                for assignment in assignments.get((scope(node), payload.id), ())
                if assignment.lineno < node.lineno
            ]
            if len(preceding) != 1:
                continue
            payload = preceding[0].value
        if isinstance(payload, ast.Dict):
            yield node.lineno, _dict_fields(payload)


def _fallback_failures(text: str, codes, registry_codes) -> list[str]:
    responses: dict[str, list[tuple[int, dict]]] = {}
    for line, fields in _response_dicts(text):
        if code := _literal_code(fields.get("code")):
            responses.setdefault(code, []).append((line, fields))
    declared = _declared_codes(text)
    failures = []
    for code in codes:
        if code in responses:
            for line, fields in responses[code]:
                if "error" not in fields:
                    failures.append(f"{code} 第 {line} 行的响应里没有 error 原文")
        elif code not in declared and code not in registry_codes:
            failures.append(f"{code} 既没有响应也没有异常")
    return failures


def _error_blocks(text: str):
    """每个 `jsonify({"error": ...})` 调用的文本块（到第一个 `})` 为止——
    本仓库的错误响应都是字面量 dict，这个粗粒度够用且改坏会立刻可见。"""
    for m in re.finditer(r'jsonify\(\{"error"', text):
        window = text[m.start() : m.start() + 600]
        end = window.find("})")
        yield text[: m.start()].count("\n") + 1, window[: end if end != -1 else 600]


@pytest.mark.parametrize(
    "payload",
    [
        '{"code": "sample_code", "error": "fallback"}',
        '{"error": "fallback", "code": "sample_code"}',
        '{"params": {"nested": {"value": "})"}}, "code": "sample_code", "error": "fallback"}',
    ],
)
def test_fallback_accepts_either_field_order_in_the_same_actual_response(payload):
    assert _fallback_failures(f"jsonify(\n{payload}\n)", {"sample_code"}, set()) == []


@pytest.mark.parametrize(
    "source",
    [
        'jsonify({"code": "sample_code"}); other = {"code": "sample_code", "error": "elsewhere"}',
        'jsonify({"code": "sample_code"}); jsonify({"code": "sample_code", "error": "elsewhere"})',
        'jsonify({"code": "sample_code", "params": {"error": "nested"}})',
        '# jsonify({"code": "sample_code", "error": "comment"})\njsonify({"code": "sample_code"})',
    ],
)
def test_fallback_cannot_borrow_an_error_from_another_dict_or_comment(source):
    failures = _fallback_failures(source, {"sample_code"}, {"sample_code"})
    assert len(failures) == 1 and "sample_code" in failures[0]


def test_fallback_keeps_exception_and_registry_paths_without_literal_responses():
    source = 'raise documents.DocumentError("document_code", "fallback")'
    assert _fallback_failures(source, {"document_code", "registry_code"}, {"registry_code"}) == []
    assert _fallback_failures('# DocumentError("missing_code", "comment")', {"missing_code"}, set())


def test_response_dict_scanner_uses_actual_dict_fields_in_any_order():
    source = """
# jsonify({"error": "comment"})
description = 'jsonify({"error": "string"})'
jsonify({"code": "sample_code", "error": "fallback"})
jsonify({"params": {"error": "nested"}})
jsonify({"error": "missing stable code"})
"""
    blocks = list(_response_dicts(source))
    assert len(blocks) == 3
    assert set(blocks[0][1]) == {"code", "error"}
    assert set(blocks[1][1]) == {"params"}
    assert set(blocks[2][1]) == {"error"}


def test_fallback_resolves_the_same_function_unique_preceding_payload_dict():
    source = """
def response():
    body = {"code": "sample_code", "error": "fallback"}
    return jsonify(body)
"""
    assert _fallback_failures(source, {"sample_code"}, set()) == []
    assert len(list(_response_dicts(source))) == 1


def test_fallback_does_not_borrow_a_payload_assignment_from_another_function():
    source = """
def other():
    body = {"code": "sample_code", "error": "elsewhere"}
def response():
    body = {"code": "sample_code"}
    return jsonify(body)
"""
    assert len(_fallback_failures(source, {"sample_code"}, set())) == 1


def test_response_scanner_does_not_claim_to_resolve_ambiguous_assignments():
    source = """
def response(choice):
    body = {"code": "sample_code", "error": "first"}
    if choice:
        body = {"code": "sample_code"}
    return jsonify(body)
"""
    assert list(_response_dicts(source)) == []


def test_nested_capability_and_registered_status_codes_do_not_imply_http_errors():
    source = """
capabilities.append({"id": "figure.svg", "code": "capability_code"})
def registration():
    return {"registered": False, "code": "registration_code"}
"""
    assert _declared_codes(source) == {"capability_code", "registration_code"}
    assert list(_response_dicts(source)) == []


def test_every_error_response_carries_a_stable_code():
    missing = []
    for name in SCANNED:
        text = (SRC_DIR / name).read_text(encoding="utf-8")
        for line, block in _error_blocks(text):
            if '"code"' in block:
                continue
            key = f"{name}:{line}"
            if key not in NO_CODE_ALLOWLIST:
                missing.append((key, block.splitlines()[0]))
    assert not missing, "以下错误响应没有稳定 code（英文界面会在这里冒中文）：\n" + "\n".join(
        f"  {k}  {frag}" for k, frag in missing
    )


def test_visible_codes_table_covers_every_literal_code():
    """上面那张 USER_VISIBLE_CODES 表必须覆盖源码里的每个字面量 code：
    新增 code 忘了登记的话，「两种语言都有文案」那条就守不到它。"""
    declared: set[str] = set()
    for name in SCANNED:
        declared |= _declared_codes((SRC_DIR / name).read_text(encoding="utf-8"))
    unlisted = sorted(declared - set(USER_VISIBLE_CODES) - NON_UI_CODES)
    assert not unlisted, f"这些 code 没进 USER_VISIBLE_CODES 表：{unlisted}"


# ---------------------------------------------------------------------------
# Tavotto Run 的稳定码（ADR 0021）——**上面那张表按形状守不到它们**
# ---------------------------------------------------------------------------
# `_declared_codes` 认的是**字面量**：`{"code": "no_project"}`。而 native 那条
# 面返回的是 `engine_runcodes.NATIVE_*` 常量，一个字面量都没有。于是
# `test_visible_codes_table_covers_every_literal_code` 对它们**结构性地看不见**
# ——不是漏登记，是判据的前提（"用户可见的 code 都以字面量出现在源码里"）在
# runcodes 引入常量式码表的那一刻就不再成立了。
#
# 解法不是给 native 开一张平行的手工表（手工表下一次照样忘），而是**枚举**：
# `app.py` 的 `_NATIVE_STATUS` 就是"这条 HTTP 面会返回哪些码"的闭集出处，
# 逐条要求两种语言都有文案。往那张表里加一行却不加文案 → 当场红。
#
# 反方向同样量：`errors.json` 里不许有这个闭集之外的 `native_*` 键。
# `test_i18n_dead_keys.py` 在这里帮不上忙——它按"源码里出现过这个串"判活，
# 而 `runcodes.py` 的码表让**每一个** native 码看起来都有发射点，包括那些
# 只有 CLI 会打印、界面永远见不到的（`native_desktop_required`、
# `native_attach_timeout`、`no_figure_captured`）。

_NATIVE_STATUS_RE = re.compile(r"engine_runcodes\.([A-Z0-9_]+):\s*\d{3}")


def _native_http_codes() -> set[str]:
    """`app.py` 的 `_NATIVE_STATUS` 键 —— 这条面能返回的码的闭集出处。"""
    from tavotto.engine import runcodes

    text = APP.read_text(encoding="utf-8")
    block = text[text.index("_NATIVE_STATUS = {") : text.index("def _native_error")]
    names = _NATIVE_STATUS_RE.findall(block)
    assert len(names) >= 10, f"没解析到 _NATIVE_STATUS（只拿到 {names}）——判据本身坏了"
    return {getattr(runcodes, n) for n in names}


#: 不在 `_NATIVE_STATUS` 里、但确实会经别的端点到达界面的码。
#: 只有一条：装依赖时撞上活跃 native 会话（envlease 抛 EnvironmentBusy，
#: 依赖修复那条端点转成 JSON）。
_EXTRA_NATIVE_UI_CODES = {"environment_in_use_by_native_session"}


def _ui_native_codes() -> set[str]:
    return _native_http_codes() | _EXTRA_NATIVE_UI_CODES


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_native_code_has_text_in_both_languages(locale: str):
    table = _errors(locale).get("backend", {})
    missing = sorted(_ui_native_codes() - set(table))
    assert not missing, (
        f"{locale} 的 errors.json 缺这些 Tavotto Run 码的文案：{missing}\n"
        "  （界面会原样显示 `backend.<code>`，而这几条恰好都出现在"
        "用户等着看一句解释的时刻）"
    )
    empty = sorted(c for c in _ui_native_codes() if not str(table[c]).strip())
    assert not empty, f"{locale} 里这些码的文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_native_placeholders_match_the_backend_message(locale: str):
    """文案里的 `{{x}}` 必须与后端那条消息的 `{x}` 逐个对上。

    对不上的表现不是报错，是界面上出现一个**原样的** `{{seconds}}`——一句
    本地化过、看着很正常、却把占位符泄漏给用户的话。
    """
    from tavotto.engine import runcodes

    table = _errors(locale)["backend"]
    lang = "zh" if locale == "zh-CN" else "en"
    for code in sorted(_ui_native_codes()):
        entry = runcodes.MESSAGES[code]
        backend = set(re.findall(r"\{(\w+)\}", entry.get(lang) or entry["zh"]))
        front = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
        assert front == backend, f"{locale} {code}：文案用了 {front}，后端消息给的是 {backend}"


def test_no_dead_native_keys_in_errors_json():
    """反方向：`errors.json` 里不许有闭集之外的 `native_*` 键。

    只有 CLI 会打印的那几条（`native_desktop_required` / `native_attach_timeout`
    / `no_figure_captured`）放进来就是死键——而 `test_i18n_dead_keys.py` 抓不到
    它们：它按"源码里出现过这个串"判活，`runcodes.py` 的码表让每一条都像活的。
    """
    allowed = _ui_native_codes()
    for locale in ("zh-CN", "en-US"):
        table = _errors(locale).get("backend", {})
        stray = sorted(k for k in table if k.startswith("native_") and k not in allowed)
        assert not stray, (
            f"{locale} 的 errors.json 里有界面到不了的 native 码：{stray}\n"
            "  要么它真的会到界面（那就把它加进 app.py 的 _NATIVE_STATUS），"
            "要么删掉这条文案。"
        )


def test_run_error_payload_carries_params_for_the_frontend():
    """`RunError.payload()` 必须同时给**两套约定**。

    * 顶层平铺字段 = CLI 的 JSON 契约（`tests/native/test_run_codes.py` 钉着）；
    * `params` = 前端错误文案的约定（`lib/api.ts` 的 `backendCodeMsg` 从
      `body.params` 取插值）。

    少了 `params` 的表现很隐蔽：界面照常显示那条错误，只是把 `{{seconds}}`
    原样印出来。
    """
    from tavotto.engine import runcodes

    payload = runcodes.RunError(runcodes.NATIVE_ATTACH_TIMEOUT, seconds=300).payload()
    assert payload["params"] == {"seconds": 300}
    assert payload["seconds"] == 300, "顶层平铺字段是 CLI 的契约，不能因为加了 params 就撤掉"
    assert payload["code"] == runcodes.NATIVE_ATTACH_TIMEOUT
    # params 是副本：调用方改它不该改到异常对象自己的 fields
    payload["params"]["seconds"] = 1
    assert runcodes.RunError(runcodes.NATIVE_ATTACH_TIMEOUT, seconds=300).fields == {"seconds": 300}


# --------------------------------------------------------------------------
# worker 错误必须带状态码
# --------------------------------------------------------------------------
def _worker_error_call_sites(src: str) -> list[str]:
    return [
        f"app.py:{index}"
        for index, line in enumerate(src.splitlines(), start=1)
        if "_worker_error_payload(" in line and not line.lstrip().startswith(("#", "*", "def "))
    ]


def _worker_error_status_failures(src: str) -> list[str]:
    """Calls which would return a worker error body without a failure status.

    The status may be a literal 4xx/5xx, or an `http_status` expression
    (`exc.http_status` / `getattr(exc, "http_status", 500)`) which the worker
    error contract defines as 4xx/5xx. Wrapped `return` statements are read as
    one window; the predicate measures the status expression, not formatting.
    """
    lines = src.splitlines()
    bad: list[str] = []
    for index, line in enumerate(lines):
        if "_worker_error_payload(" not in line:
            continue
        if line.lstrip().startswith(("#", "*", "def ")):
            continue  # 定义处与注释不算调用
        window = "\n".join(lines[index : index + 3])
        tail = window[window.index("_worker_error_payload(") :]
        if re.search(
            r",\s*(?:[45]\d\d\b|getattr\([^\n]*[\"']http_status[\"'][^\n]*\)"
            r"|[A-Za-z_][\w.]*\.http_status\b)",
            tail,
        ):
            continue
        bad.append(f"app.py:{index + 1}: {line.strip()}")
    return bad


def test_every_worker_error_response_carries_a_failure_status():
    """`_worker_error_payload()` 回的是**裸 dict**——Flask 会把它序列化成
    **HTTP 200**。

    调用方（前端 `jsonFetch`）按状态码判成败，于是一次 bridge / worker 失败
    被当成成功，代码接着去读一个不存在的 `session`。用户看到的是**第二个**
    错误，真正的原因被盖掉了——这正是 "silent wrong" 的标准形状：不是没报，
    是**报错了却说成功**。

    2026-08-28 native 那两处（`build` / `continue`·`detach`·`terminate`）正是
    这么漏的：同一个文件里另外 7 处全是 `, 500`，只有这两处忘了（issue #191）。
    所以这条判据是**枚举**式的：每一处调用都要在同一段里带上一个非 2xx 的
    状态码，加第 10 处时忘了会当场红。

    判据只看源码文本，因为它量的是**响应的形状**，不是某一条端点的行为——
    行为那一半由各端点自己的用例覆盖，而"忘了带状态码"恰恰是那些用例
    看不见的（它们断言的是 body 里的 code，200 与 500 都读得到）。
    """
    src = APP.read_text(encoding="utf-8")
    seen = _worker_error_call_sites(src)
    bad = _worker_error_status_failures(src)
    assert not bad, (
        "这些 worker 错误响应没带失败状态码，会以 HTTP 200 发出去：\n  "
        + "\n  ".join(bad)
        + "\n  前端按状态码判成败——200 的失败会被当成成功。"
    )
    # **扫到的条数也要钉**（2026-08-28 实测 11 处）。写成 `>=` 不行：那样它会
    # 跟着系统一起长、却不会跟着系统一起收紧，"悄悄少两处"永远不会响（仓库里
    # 那条"下限判据会向上腐烂"的同族）。
    #
    # **它兑现的是什么，说准**——这一格挡的是"调用点集合被动过了，来个人看
    # 一眼"这条**绊线**：新增一处出口、删掉一处、或者把它挪到别的文件里，
    # 都会红。
    #
    # **它挡不住什么**：在 `app.py` 里包一层（`_native_worker_error()` 内部
    # 再调 `_worker_error_payload`）。那处调用没有消失，只是从路由搬进了
    # wrapper——总数不变，这条照样绿，而上面那条逐行判据也看不见 wrapper
    # 的调用方。真要挡它，判据得改成**数出口**：枚举每条路由的
    # `except pool.WorkerError` 分支、断言它的 `return` 带非 2xx。没这么写是
    # 因为 `app.py` 里的 `except pool.WorkerError` 形态很杂（有的往 checks 里
    # 追加、有的置状态字段、有的 `return None`、有的直接 re-raise），逐条分辨
    # "哪些是响应路径"会引入一堆假红。
    #
    # 写在这里是因为**理由写得比兑现的强，比没有理由更坏**：下一个人会以为
    # 这一格有人守着。
    # 2026-09-25（QA SCI-04-B1）：update_source / history/restore 两处 `, 500` 收进
    # `_write_back_error_response` 的一处 `, 409`（写回 verify 段挂了，原件零改动），11 → 10；
    # ADR 0080 的 /api/engine/specfix 渲染半路死了那一处 `, 500`，10 → 11。
    # #779: paired PNG snapshot adds one explicit HTTP 500 error response, 11 → 12.
    # ADR 0181: the SVG scene export path adds one explicit failure response, 12 → 13.
    assert len(seen) == 13, (
        f"`_worker_error_payload` 的调用点从 13 变成了 {len(seen)}：{seen}\n"
        "  新增出口 → 把这个数改成新的实测值，并确认它带了状态码；\n"
        "  变少了 → 确认那处是真的删了，而不是搬到了别的文件里。"
    )


def test_worker_error_status_gate_rejects_a_missing_or_success_status():
    assert _worker_error_status_failures("return jsonify(_worker_error_payload(exc)), 409") == []
    assert (
        _worker_error_status_failures("return jsonify(_worker_error_payload(exc)), exc.http_status")
        == []
    )
    assert (
        _worker_error_status_failures(
            'return jsonify(_worker_error_payload(exc)), getattr(exc, "http_status", 500)'
        )
        == []
    )
    # A wrapped return still counts: the status is on the next line.
    assert (
        _worker_error_status_failures(
            "return jsonify(_worker_error_payload(exc, x=fact)), getattr(\n"
            '    exc, "http_status", 500\n)'
        )
        == []
    )
    assert _worker_error_status_failures("return jsonify(_worker_error_payload(exc))") != []
    assert _worker_error_status_failures("return jsonify(_worker_error_payload(exc)), 200") != []
