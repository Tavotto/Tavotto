"""native bridge 入口的「跳过读不了的 import 位置」端到端（ADR 0153）：真进程、真 bridge 会话。

单元与 safe worker 那一半在 `tests/test_unreadable_import_skip.py`（同一份脚本模板与不可读目录助手）。
"""

from __future__ import annotations

import os

import pytest

from support import bridgekit
from test_unreadable_import_skip import SCRIPT, _dep_dirs, _name, _unlock

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="chmod 000 在 Windows 上不构成不可读；ACL 腿见 ADR 0153"
)
bridge_needs = bridgekit.needs_user_python


@bridge_needs
def test_native_bridge_end_to_end_skips_and_leaves_a_trace(tmp_path, monkeypatch, bridge_session):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    mod = _name()
    bad, good = _dep_dirs(tmp_path, mod, with_good=True)
    script = bridgekit.write(
        tmp_path / "paper.py",
        SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show="plt.show()"),
    )
    try:
        with bridge_session(script, cwd=str(tmp_path)) as sess:
            barrier = sess.wait_event("barrier")
            assert barrier["script_error"] is None
            resp = sess.ensure_built()
            skips = resp["runtime"]["import_skips"]
            assert [(e["module"], e["error"]) for e in skips] == [(mod, "PermissionError")]
    finally:
        _unlock(bad, good)


@bridge_needs
def test_native_bridge_end_to_end_reports_the_unreadable_folder(
    tmp_path, monkeypatch, bridge_session
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    mod = _name()
    bad, good = _dep_dirs(tmp_path, mod, with_good=False)
    proj = tmp_path / "proj"  # 依赖夹在项目外面（tmp_path 下），与真实现场同形
    script = bridgekit.write(
        proj / "paper.py", SCRIPT.format(bad=str(bad), good=str(good), mod=mod, show="plt.show()")
    )
    try:
        with bridge_session(script, cwd=str(proj)) as sess:
            err = sess.wait_event("barrier")["script_error"]
            assert err["unreadable_folder"] == {"folder": ".work_deps_bad"}
            assert "PermissionError" in err["traceback"]
    finally:
        _unlock(bad, good)
