"""`read_run_report` 读的是 `prepsession.SESSIONS.report()` 真实产出的形状：异常在 `result.error`，顶层没有 `error`。

判据的主语：**真实准备会话在 worker 抛出 ModuleNotFoundError 之后的报告**（夹具复用 `test_fixrouter_report`），
不手写一个顶层 error 的假报告。
"""

# ruff: noqa: F811 — 夹具参数名与导入名相同
from __future__ import annotations

import pytest

from tavotto.engine import pool as engine_pool, prepsession
from tavotto.engine.fixtools import REGISTRY, FixContext, run_tool
from test_preparation_api import client, fake_pool  # noqa: F401
from test_preparation_session import sessions  # noqa: F401


@pytest.fixture(autouse=True)
def _no_real_ai_probe(monkeypatch):
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: False)


def test_real_session_report_gets_the_shadow_note_and_the_error_text(
    client, tmp_path, fake_pool, sessions
):
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/p/celerite/__init__.py", line 3, in <module>\n'
        "    from .celerite_version import __version__\n"
        "ModuleNotFoundError: No module named 'celerite.celerite_version'"
    )
    err = engine_pool.WorkerError(
        "脚本执行失败: No module named 'celerite.celerite_version'", tb, code="script_error"
    )
    # 先建会话拿到项目根，再在根里放半截 celerite/，然后跑到失败
    from test_preparation_session import _create_ready

    root, _pj, report = _create_ready(client, tmp_path)
    (root / "celerite").mkdir()
    (root / "celerite" / "__init__.py").write_text(
        "from .celerite_version import __version__\n", encoding="utf-8"
    )
    from test_preparation_session import _act, _action, _settle

    fake_pool["error"] = err
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    final = _settle(client, sid)
    assert "error" not in final and final["result"]["error"]["code"] == "script_error"  # 真实形状

    ctx = FixContext.for_fix_session("s1", root, report=lambda: final)
    r = run_tool(REGISTRY["read_run_report"], ctx, {})
    assert not r.is_error
    assert r.content.lstrip("{\n ").startswith('"project_package_shadow"')
    assert "celerite.celerite_version" in r.content and "Do not install it" in r.content
    assert '"error"' in r.content  # 模型现在也能读到异常本身
