"""Python 版本不合时挑「现成环境」、以及用户显式选过的项目环境不被自动换版本碰（ADR 0148 修订 2026-11-01）。

主语：`SessionService._python_switch_target`（失败那一刻、会话锁内、只读）选出的 `(目标版本, 环境)`，与 `_plan_python_change`
对显式选择的让路。候选来自 `envadvice.recommend` 的公开投影（桩）；范围来自 `pyversionfix.required_span`（真跑，读声明文件）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tavotto.engine import envadvice, prepsession, projectenv, pyversionfix


def _cand(cid, version, *, status="healthy", current=False, name="", sources=("conda",), rel=None):
    return {
        "id": cid,
        "generation": f"gen-{cid}",
        "name": name,
        "sources": list(sources),
        "python_relative": rel,
        "status": status,
        "current": current,
        "health": {"python_version": version, "support": "verified"} if version else None,
    }


def _sess(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.13"\n')
    return SimpleNamespace(
        project_root=str(tmp_path),
        plan=SimpleNamespace(script="fig.py", environment={"adoption": {}}),
    )


def _result():
    return SimpleNamespace(
        error={"code": "project_env_unsupported_python", "params": {"version": "3.9.1"}}
    )


def _pick(monkeypatch, sess, candidates, *, locked=None):
    rec = {"decision": {"locked_by": locked}, "candidates": candidates}
    monkeypatch.setattr(envadvice, "recommend", lambda *a, **k: rec)
    return prepsession.SessionService._python_switch_target(
        None, sess, "project_env_unsupported_python", _result()
    )


def test_picks_a_healthy_environment_inside_the_required_range(tmp_path, monkeypatch):
    sess = _sess(tmp_path)
    to, env = _pick(
        monkeypatch,
        sess,
        [_cand("old", "3.9.5", name="old"), _cand("sci", "3.11.4", name="sci")],
    )
    assert env == {"id": "sci", "generation": "gen-sci", "name": "sci", "version": "3.11.4"}
    assert to == "3.11"


def test_never_offers_builtin_current_unhealthy_or_unchecked(tmp_path, monkeypatch):
    sess = _sess(tmp_path)
    to, env = _pick(
        monkeypatch,
        sess,
        [
            _cand(envadvice.BUILTIN_ID, "3.12.1"),
            _cand("cur", "3.12.2", current=True),
            _cand("sick", "3.12.3", status="no_matplotlib"),
            _cand("unchecked", None, status="unchecked"),
        ],
    )
    assert env is None
    assert to == "3.12"  # 目标版本仍给：前端走选择器


def test_prefers_the_higher_ranked_candidate(tmp_path, monkeypatch):
    sess = _sess(tmp_path)
    _to, env = _pick(
        monkeypatch,
        sess,
        [_cand("a", "3.10.1", name="a"), _cand("b", "3.12.1", name="b")],
    )
    assert env["id"] == "b"  # 同档里 Python 新的优先（userenvs.rank）


def test_a_name_is_always_present_and_has_no_path(tmp_path, monkeypatch):
    sess = _sess(tmp_path)
    _to, env = _pick(monkeypatch, sess, [_cand("x", "3.11.2", name="", rel=".venv")])
    assert env["name"] == ".venv"
    _to, env = _pick(monkeypatch, sess, [_cand("x", "3.11.2", name="", rel=None)])
    assert env["name"] == "Python 3.11"


def test_a_global_explicit_interpreter_means_no_one_click_candidate(tmp_path, monkeypatch):
    """全局显式解释器压着：采用会 409，不摆一个必然失败的按钮，交给选择器"""
    sess = _sess(tmp_path)
    to, env = _pick(monkeypatch, sess, [_cand("sci", "3.11.4")], locked={"source": "configured"})
    assert env is None and to == "3.12"


def test_non_version_failures_have_no_target(tmp_path, monkeypatch):
    sess = _sess(tmp_path)
    got = prepsession.SessionService._python_switch_target(None, sess, "script_error", _result())
    assert got == (None, None)


def test_a_recommend_crash_never_breaks_routing(tmp_path, monkeypatch):
    sess = _sess(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(envadvice, "recommend", boom)
    got = prepsession.SessionService._python_switch_target(
        None, sess, "project_env_unsupported_python", _result()
    )
    assert got == (None, None)


# ---------------------------------------------------------------- 显式选择不被自动换版本碰


def test_user_chose_project_env_is_only_true_for_an_explicit_concrete_choice(tmp_path, monkeypatch):
    def with_record(rec):
        monkeypatch.setattr(projectenv, "remembered_record", lambda *_a, **_k: rec)
        return prepsession._user_chose_project_env(str(tmp_path))

    assert with_record(None) is False
    assert with_record({"mode": projectenv.MODE_DEFAULT_CHAIN, "automatic": False}) is False
    assert with_record({"python": "x", "automatic": True, "trigger": "auto_detected"}) is False
    assert with_record({"python": "x", "automatic": False}) is True


def test_auto_python_change_steps_aside_for_an_explicit_project_choice(tmp_path, monkeypatch):
    sess = SimpleNamespace(
        project_root=str(tmp_path),
        cancel_requested=False,
        python_change_run=-1,
        ledger=SimpleNamespace(run=1),
    )
    plan = SimpleNamespace(target=prepsession.TARGET_SCRIPT, environment={}, script="fig.py")
    result = SimpleNamespace(
        status="error", cancel_requested_at=None, error={"code": "python_out_of_range"}
    )
    asked = []
    monkeypatch.setattr(
        pyversionfix, "plan_python_change", lambda *a, **k: asked.append(1) or "PLAN"
    )
    monkeypatch.setattr(pyversionfix, "failure_from_report", lambda **k: {"code": "x"})
    monkeypatch.setattr(prepsession, "_user_chose_project_env", lambda _r: False)
    svc = prepsession.SessionService.__new__(prepsession.SessionService)
    assert svc._plan_python_change(sess, plan, result, True) == "PLAN"
    monkeypatch.setattr(prepsession, "_user_chose_project_env", lambda _r: True)
    assert svc._plan_python_change(sess, plan, result, True) is None
    assert asked == [1]  # 显式选择时连计划都不去做
