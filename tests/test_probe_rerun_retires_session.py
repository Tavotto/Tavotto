"""重新运行（试运行）必须同步退役旧会话，不能让 workerd 把还没关掉的旧会话复用给新的一次。

背景（#815 合并组 windows-exe-smoke 的 `script-input.spec`）：workerd 对同一份 spawn 规格复用活着的会话
（`by_hash`）。试运行原先用异步 `pool.invalidate(...)`，旧会话的关停还没落地新的就开了——拿到旧会话，它的
`build` 命中「已建好」直接回缓存：脚本一行没重跑，`input()` 不再被问，「已用上次的答案」的轻提示也不会出。
Windows 打包产物上关停更慢、窗口更大，所以只红在那里。判据的主语：**重跑**要的是「新的一次执行」。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool, probe


@pytest.fixture
def script_dir(tmp_path):
    (tmp_path / "pick.py").write_text("print('x')\n", encoding="utf-8")
    return tmp_path


def test_rerun_retires_old_session_synchronously_before_building(script_dir, monkeypatch):
    calls: list[tuple] = []

    def fake_invalidate(script, figures_dir=None, run=None, *, only_run=False, force=False):
        calls.append(("invalidate", force, only_run))

    def fake_build(script, figures_dir, entry, **kw):
        calls.append(("build",))
        raise pool.WorkerError("stop here", code="worker_crashed")

    monkeypatch.setattr(pool, "invalidate", fake_invalidate)
    monkeypatch.setattr(pool, "build", fake_build)
    probe.probe(script_dir, "pick.py", entries=["__main__"])
    # 第一次作废（开新会话之前）必须是 force=True：同步关停并确认，之后才 build
    assert calls[0] == ("invalidate", True, True)
    assert calls[1] == ("build",)


def test_unconfirmed_retirement_is_reported_and_never_builds(script_dir, monkeypatch):
    built: list[str] = []

    def refuse(script, figures_dir=None, run=None, *, only_run=False, force=False):
        if force:
            raise pool.WorkerError("无法确认旧渲染会话已关闭，请重试", code="session_dead")

    monkeypatch.setattr(pool, "invalidate", refuse)
    monkeypatch.setattr(pool, "build", lambda *a, **k: built.append("x"))
    result = probe.probe(script_dir, "pick.py", entries=["__main__"])
    assert built == []  # 旧会话没确认关掉就不开新的（否则又是复用旧会话、假装重跑过）
    assert result["stems"] == []
    assert result["error"] is not None
