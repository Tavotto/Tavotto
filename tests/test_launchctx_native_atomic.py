"""V2 PR2（Codex #1034 r3 P2）：native 成功的启动记录与运行默认值在**同一次**登记写入里提交。

native 成功收口先由 app 的 `record_success(None, update_default=True)` 更新默认并清掉旧记录；之后写 native 记录若是第二次、另一把锁里的
写入，中间插进来的 safe 成功会让登记留下「safe 的默认配置 + 旧 native 的启动记录」，下次自动运行就沿用了 native。
"""

from __future__ import annotations

import pytest

from tavotto.engine import launchctx, runconfig


@pytest.fixture
def writes(monkeypatch):
    calls = []
    real = runconfig._write

    def counting(root, data):
        calls.append({"defaults": dict(data["defaults"]), "launches": dict(data["launches"])})
        return real(root, data)

    monkeypatch.setattr(runconfig, "_write", counting)
    return calls


def _plan(tmp_path):
    from tavotto.engine import scriptargs

    script = tmp_path / "fig.py"
    script.write_text("print(1)\n", encoding="utf-8")

    class P:
        execution = "native"
        interpreter = __import__("sys").executable
        environment = {"source": "system", "generation": "g1"}
        workdir_decision = {"mode": "sandbox"}
        binding = {"revision": "r1"}
        script_revision = scriptargs.source_revision(script)

    return P


def test_the_native_record_and_the_run_default_land_in_one_write(tmp_path, writes):
    rc = runconfig.put(tmp_path, "fig.py", ["--a"], sensitive=False)
    run = runconfig.selection_for(tmp_path, "fig.py", ["--a"], sensitive=False)
    assert run is not None and rc
    writes.clear()
    assert launchctx.record_native_success(tmp_path, "fig.py", _plan(tmp_path), run=run) is True
    assert len(writes) == 1, "默认值与启动记录必须同一次写入"
    assert writes[0]["defaults"]["fig.py"] == run.config_id
    assert writes[0]["launches"]["fig.py"]["profile"] == "native"


def test_a_run_without_arguments_clears_the_default_in_the_same_write(tmp_path, writes):
    runconfig.put(tmp_path, "fig.py", ["--a"], sensitive=False)
    runconfig.commit_success(
        tmp_path, "fig.py", None, default_config_id=_first_id(tmp_path), update_default=True
    )
    writes.clear()
    assert launchctx.record_native_success(tmp_path, "fig.py", _plan(tmp_path), run=None)
    assert len(writes) == 1
    assert "fig.py" not in writes[0]["defaults"]
    assert writes[0]["launches"]["fig.py"]["profile"] == "native"


def _first_id(root):
    return next(iter(runconfig._read(root)["configs"]))


def test_a_stale_script_writes_nothing(tmp_path, writes):
    plan = _plan(tmp_path)
    (tmp_path / "fig.py").write_text("print(2)\n", encoding="utf-8")
    writes.clear()
    assert launchctx.record_native_success(tmp_path, "fig.py", plan, run=None) is False
    assert writes == []
