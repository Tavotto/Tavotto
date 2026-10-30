"""A surviving preview cannot certify a conversion that lost other figures."""

# ruff: noqa: F811 - imported pytest fixture

from types import SimpleNamespace

import pytest

from tavotto import app as app_module
from tavotto.engine import ai_convert, discover
from test_ai_convert import _assert_only_kept_for_diagnostics, _run, _wait, env  # noqa: F401


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.py"
    source.write_text("# original Pillow source\n")
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "candidates")
    cand = ai_convert.create_candidate("abcdef012345", source, root, source.name, library="pillow")
    cand.new_bytes = b"import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('out.png')\n"
    cand.target.write_bytes(cand.new_bytes)
    return cand


@pytest.mark.parametrize(
    "partial",
    [
        {"partial": True},
        {"dropped_figures": 2},
        {"failed_figures": [{"stem": "page2", "code": "preview_capture_failed"}]},
    ],
)
def test_one_surviving_figure_cannot_pass_agent_check(candidate, partial):
    candidate.extras["runner"] = lambda *_: {"stems": ["page1"], **partial}
    passed, detail = ai_convert.agent_check(candidate)
    assert not passed
    assert "capture_incomplete" in detail


def test_precommit_partial_capture_cannot_be_adopted(env):
    env["runner"] = lambda *_: {
        "stems": ["page1"],
        "failed_figures": [{"stem": "page2", "code": "preview_capture_failed"}],
    }
    sid = _run(env)
    result = _wait(sid)
    assert result["task"]["outcome"] == "failed"
    _assert_only_kept_for_diagnostics(env, sid)


def test_application_adapter_preserves_partial_capture_evidence(tmp_path, monkeypatch):
    failed = [{"stem": "page2", "code": "preview_capture_failed"}]
    monkeypatch.setattr(
        app_module.engine_probe,
        "probe",
        lambda *_a, **_k: {"stems": ["page1"], "partial": True, "failed_figures": failed},
    )
    monkeypatch.setattr(app_module.engine_runconfig, "default_selection", lambda *_: None)
    monkeypatch.setattr(app_module.engine_pool, "peek", lambda *_a, **_k: None)
    retired = []
    monkeypatch.setattr(
        app_module.engine_pool, "invalidate", lambda *_a, **_k: retired.append(True)
    )
    out = app_module._convert_runner(SimpleNamespace(path=tmp_path), "source.py")(
        str(tmp_path), "probe.py"
    )
    assert retired == [True]
    assert out.get("partial") is True
    assert out.get("failed_figures") == failed


def test_validation_probe_cannot_be_discovered_as_a_project_owner(candidate):
    def runner(root, rel, *_):
        # 影子脚本：项目里根本没有这个文件，文件监视器 / 素材发现什么都看不到。
        assert not (candidate.project_root / rel).exists()
        assert rel not in {
            p.relative_to(candidate.project_root).as_posix()
            for p in discover.iter_scripts(candidate.project_root)
        }
        return {"stems": ["out"]}

    assert ai_convert.run_validation(candidate, runner)["status"] == "ok"
    assert not any(
        p.name.startswith(ai_convert.STAGE_PREFIX) for p in candidate.project_root.iterdir()
    )


@pytest.mark.parametrize("code", ["capture_incomplete", "convert_output_identity"])
def test_internal_capture_diagnostics_use_the_existing_ui_reason(code):
    checked = ai_convert._failure_check({"status": "error", "code": code}, "precommit")
    assert checked.reason_code == "run_failed"


def test_application_adapter_carries_the_structured_missing_module(tmp_path, monkeypatch):
    """缺依赖点名的模块走结构化字段（probe 错误的 `params.module`）交给依赖决定，不解析中文文案。"""
    err = {
        "code": "missing_dependency",
        "message": "缺少依赖包：openpyxl（当前渲染环境里没有它）",
        "params": {"module": "openpyxl"},
    }
    monkeypatch.setattr(app_module.engine_probe, "probe", lambda *_a, **_k: {"error": err})
    monkeypatch.setattr(app_module.engine_runconfig, "default_selection", lambda *_: None)
    monkeypatch.setattr(app_module.engine_pool, "invalidate", lambda *_a, **_k: None)
    out = app_module._convert_runner(SimpleNamespace(path=tmp_path), "source.py")(
        str(tmp_path), "probe.py"
    )
    assert out["error"]["code"] == "missing_dependency"
    assert out["error"]["module"] == "openpyxl"
