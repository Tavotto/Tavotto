"""脚本文件被移动 / 删除（`script_source_missing`）时，界面那句话要填上脚本名。

改前 `pool.recovery_params` 对这个码回空，文案里的 `{{script}}` 原样露在界面上
（2026-11-25 项目搬家复现实测）。参数只给文件名，不给路径。
"""

from __future__ import annotations

from tavotto.engine import pool


def _err(extra: dict | None = None, script_name: str = "") -> pool.WorkerError:
    exc = pool.WorkerError(
        "脚本文件不存在: plot.py（可能被移动或删除）", code=pool.SCRIPT_SOURCE_MISSING_CODE
    )
    exc.extra = extra or {}
    exc.script_name = script_name
    return exc


def test_worker_supplied_name_reaches_the_ui_params():
    exc = _err({"script": "plot.py", "missing_source": True})
    assert pool.recovery_params(exc) == {"script": "plot.py"}


def test_falls_back_to_the_session_script_and_keeps_only_the_file_name():
    assert pool.recovery_params(_err(script_name="scripts/sub/plot.py")) == {"script": "plot.py"}
    assert pool.recovery_params(_err(script_name="scripts\\sub\\plot.py")) == {"script": "plot.py"}


def test_extra_path_is_reduced_to_a_file_name():
    exc = _err({"script": "/Users/a/proj/scripts/plot.py"})
    assert pool.recovery_params(exc) == {"script": "plot.py"}


def test_no_name_anywhere_is_not_invented():
    assert pool.recovery_params(_err()) == {}


def test_render_error_response_carries_the_script_name():
    """渲染端点的响应体（`app._worker_error_payload`）真的带上 `params.script`——不是只有 `recovery_params` 会算（Codex #2215）。"""
    from tavotto import app as tavotto_app

    body = tavotto_app._worker_error_payload(_err({"script": "plot.py"}))
    assert body["code"] == pool.SCRIPT_SOURCE_MISSING_CODE
    assert body["params"] == {"script": "plot.py"}


def test_probe_error_carries_the_script_name():
    """试运行（`probe._error_from_worker`）那条出口同样带上脚本名，码原样带出。"""
    from tavotto.engine import probe

    out = probe._error_from_worker(_err({"script": "plot.py"}), "")
    assert out["code"] == pool.SCRIPT_SOURCE_MISSING_CODE
    assert out["params"] == {"script": "plot.py"}
