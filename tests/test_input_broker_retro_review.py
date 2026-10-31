"""输入回放的补审回归（Codex #909 r4236696403）：

* 回放回的明文答案与界面作答同一条隐私规则：敏感运行 / 口令 = 0600，自动回填事件不带明文。
"""

from __future__ import annotations

import os
import stat
import sys
from types import SimpleNamespace

import pytest

from tavotto.engine import inputbroker, scriptinput


def _rec(**kw):
    base = {"index": 1, "kind": "input", "prompt": "pick: ", "context": "ctx1:a", "answer": "2"}
    base.update(kw)
    return base


@pytest.fixture
def events(monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(inputbroker, "_publish", lambda e, p, d: seen.append((e, dict(d))))
    monkeypatch.setattr(inputbroker, "_has_answerer", lambda p: False)
    return seen


def _worker(tmp_path, *, sensitive):
    return SimpleNamespace(
        figures_dir=str(tmp_path), script_name="s.py", run=SimpleNamespace(sensitive=sensitive)
    )


def _decide(tmp_path, policy, *, sensitive, kind="input"):
    d = tmp_path / "rendezvous"
    d.mkdir(exist_ok=True)
    request = {"index": 1, "prompt": "pick: ", "kind": kind, "context": "ctx1:a"}
    inputbroker._decide(_worker(tmp_path, sensitive=sensitive), d, request, policy)
    return d / scriptinput.reply_name(1)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限位")
@pytest.mark.parametrize(
    ("sensitive", "kind", "private"),
    [(False, "input", False), (True, "input", True), (False, "getpass", True)],
)
def test_replayed_answers_follow_the_interactive_privacy_rule(
    tmp_path, events, sensitive, kind, private
):
    rec = _rec(kind=kind)
    path = _decide(tmp_path, inputbroker.ReplayAnswers.of([rec]), sensitive=sensitive, kind=kind)
    assert path.read_text(encoding="utf-8")
    if private:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.parametrize(
    ("sensitive", "kind", "expect"),
    [(False, "input", "2"), (True, "input", None), (False, "getpass", None)],
)
def test_cold_replay_autofill_event_carries_no_plaintext_for_private_runs(
    tmp_path, events, sensitive, kind, expect
):
    policy = inputbroker.ReplayAnswers.transcript(
        SimpleNamespace(id="t1", entries=[_rec(kind=kind)])
    )
    _decide(tmp_path, policy, sensitive=sensitive, kind=kind)
    filled = [d for e, d in events if e == "script.input_autofilled"]
    assert len(filled) == 1
    assert filled[0]["answer"] == expect
    assert filled[0]["index"] == 1 and filled[0]["prompt"] == "pick: "


def test_private_run_mismatch_never_leaks_the_old_answer_as_a_suggestion(tmp_path, monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(inputbroker, "_publish", lambda e, p, d: seen.append((e, dict(d))))
    monkeypatch.setattr(inputbroker, "_has_answerer", lambda p: True)
    policy = inputbroker.ReplayAnswers.transcript(
        SimpleNamespace(id="t1", entries=[_rec(context="ctx1:other")])
    )
    d = tmp_path / "rv"
    d.mkdir()
    request = {"index": 1, "prompt": "pick: ", "kind": "input", "context": "ctx1:a"}
    try:
        inputbroker._decide(_worker(tmp_path, sensitive=True), d, request, policy)
        asked = [x for e, x in seen if e == "script.input_requested"]
        assert len(asked) == 1 and asked[0]["suggestion"] is None
    finally:
        for p in inputbroker.pending():
            inputbroker.discard(p.id, "stopped")
