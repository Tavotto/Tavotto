"""「AI 会话结束」日志行带出失败事实（诊断包 TVD-BCZ0）：内置执行器 0.1 秒失败却没有任何原因。

只许闭集码进日志：不带自由文本 / 路径 / 密钥。判据的主语是**日志行文本**。
"""

import subprocess
import sys
from types import SimpleNamespace

from tavotto.engine import ai_bridge


def _proc(code):
    return SimpleNamespace(returncode=code)


def test_builtin_done_reason_and_detail_reach_the_line():
    sess = {"status": "failed", "runner_error": "agent_internal", "runner_detail": "bad_target"}
    text = ai_bridge._end_facts(sess, _proc(1), "nosuchsid", True)
    assert "exit=1" in text and "runner=agent_internal" in text
    assert "runner_detail=bad_target" in text


def test_builtin_exit_without_done_event_is_marked(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    (tmp_path / "s1.stderr.log").write_bytes(
        b'Traceback (most recent call last):\n  File "C:\\Users\\alice\\x.py"\nImportError\n'
    )
    text = ai_bridge._end_facts({"status": "failed"}, _proc(3), "s1", True)
    assert "exit=3" in text and "runner=no_done_event" in text
    assert "stderr_bytes=" in text and "stderr_codes=-" in text
    assert "alice" not in text and "Traceback" not in text


def test_runner_stderr_short_codes_are_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    (tmp_path / "s2.stderr.log").write_bytes(b"agent_runner: bad_prompt\n")
    text = ai_bridge._end_facts({"status": "failed"}, _proc(1), "s2", True)
    assert "stderr_codes=bad_prompt" in text


def test_unmet_checks_are_listed_as_closed_codes():
    sess = {
        "status": "failed",
        "task": {
            "unmet": [
                {"constraint_id": "candidate.target", "reason_code": "target_missing"},
                {"constraint_id": "C:\\Users\\alice\\fig.R", "reason_code": "free text here"},
            ]
        },
    }
    text = ai_bridge._end_facts(sess, _proc(0), "x", False)
    assert "unmet=candidate.target:target_missing,other:other" in text
    assert "alice" not in text and "free text" not in text


def test_successful_cli_session_adds_only_the_exit_code():
    assert ai_bridge._end_facts({"status": "done"}, _proc(0), "x", False) == " exit=0"


def test_real_child_that_dies_is_described_without_its_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    with (tmp_path / "s3.stderr.log").open("wb") as log:
        child = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('secret-path'); sys.exit(7)"],
            stderr=log,
            check=False,
            encoding="utf-8",
        )
    text = ai_bridge._end_facts({"status": "failed"}, child, "s3", True)
    assert "exit=7" in text and "runner=no_done_event" in text
    assert "secret-path" not in text
