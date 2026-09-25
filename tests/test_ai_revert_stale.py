"""AI 回滚是一次**带版本的写**（QA STATE-09）：只回滚「这次 AI 改完的那一版」。

判据的主语：**回滚那一刻用户脚本的字节**，对照物是**这次会话结束时 AI 留在磁盘上的
那一版**（`run()` 在会话结束时记下它的 sha256，内存会话与 sidecar 各一份）。脚本此刻
已经不是那一版——AI 之后人工又改过、或第二次 AI 会话又改过、或文件被删了——回滚就是
一次过期写入：拒绝（`AgentError("ai_revert_conflict")`，HTTP 409），用户脚本一个字节
不动。不许静默用快照盖掉后来的改动再回 `{"ok": true}`。

用例全部走真实的 `run()` 记录路径（假 CLI 是一个改写脚本的 Python 子进程），不手捏
会话字典——手捏的会话里没有「AI 改完那一版」的记录，量的就不是产品真实会遇到的形状。

「没有记录」是独立一档（进程在会话结束前就重启了的 `interrupted` 会话、本改动之前写的
老 sidecar）：此时判不出「之后有没有别的改动」，回滚照旧可用——那是用户撤掉一次中断
AI 改动的唯一出口。这条也钉在这里，免得有人把「判不出」改成「一律拒绝」。
"""

from __future__ import annotations

import json
import sys
import threading

import pytest

from tavotto import app as tavotto_app
from tavotto.engine import ai_bridge

BEFORE_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4])\n"
AFTER_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color='red')\n"
HUMAN_LATER = AFTER_AI + b"plt.title('mine')\n"
SECOND_AI = AFTER_AI + b"plt.grid(True)\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "fig1.py"
    script.write_bytes(BEFORE_AI)
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge, "_build_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    for name in ("record_start", "record_end", "update_status"):
        monkeypatch.setattr(ai_bridge.ai_history, name, lambda *a, **k: None)
    started: list[str] = []
    yield {"project": project, "script": script, "started": started}
    for sid in started:
        ai_bridge.SESSIONS.pop(sid, None)


def _ai_session(env, monkeypatch, writes: bytes) -> str:
    """跑一次真实的 `run()`：假 CLI 把脚本改成 `writes`，等 `ai.done` 到了再返回。"""
    code = f"open('fig1.py', 'wb').write({writes!r})"
    monkeypatch.setattr(ai_bridge, "_cmd", lambda *a, **k: ([sys.executable, "-c", code], {}))
    done = threading.Event()
    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "改成红色",
        str(env["project"]),
        on_event=lambda name, data: done.set() if name == "ai.done" else None,
    )
    env["started"].append(sid)
    assert done.wait(30), "假 CLI 没在 30 秒内结束"
    assert env["script"].read_bytes() == writes
    return sid


def test_revert_right_after_the_ai_edit_restores_the_snapshot(env, monkeypatch):
    """对照组：脚本仍是 AI 改完那一版，回滚照常恢复快照。"""
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    assert ai_bridge.revert(sid) == {"ok": True, "script": "fig1.py"}
    assert env["script"].read_bytes() == BEFORE_AI


def test_a_later_human_edit_makes_the_revert_a_conflict(env, monkeypatch):
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    env["script"].write_bytes(HUMAN_LATER)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.revert(sid)
    assert exc.value.code == "ai_revert_conflict"
    assert exc.value.params == {"script": "fig1.py"}
    assert env["script"].read_bytes() == HUMAN_LATER
    assert ai_bridge.SESSIONS[sid]["status"] != "reverted"


def test_a_second_ai_session_makes_reverting_the_first_one_a_conflict(env, monkeypatch):
    first = _ai_session(env, monkeypatch, AFTER_AI)
    second = _ai_session(env, monkeypatch, SECOND_AI)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.revert(first)
    assert exc.value.code == "ai_revert_conflict"
    assert env["script"].read_bytes() == SECOND_AI
    # 最近那一次照常能回滚——回到的是它自己的快照（= 第一次 AI 改完的那一版）
    assert ai_bridge.revert(second)["ok"] is True
    assert env["script"].read_bytes() == AFTER_AI


def test_a_deleted_script_is_a_later_change_too(env, monkeypatch):
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    env["script"].unlink()
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.revert(sid)
    assert exc.value.code == "ai_revert_conflict"
    assert not env["script"].exists()


def test_the_record_survives_a_backend_restart(env, monkeypatch):
    """进程重启后 SESSIONS 没了，sidecar 上的记录照样把过期回滚挡下来。"""
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    ai_bridge.SESSIONS.pop(sid)
    meta = json.loads((ai_bridge.SNAP_DIR / f"{sid}.json").read_text(encoding="utf-8"))
    assert meta.get("after_sha256"), "sidecar 上没有 AI 改完那一版的记录"
    env["script"].write_bytes(HUMAN_LATER)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.revert(sid)
    assert exc.value.code == "ai_revert_conflict"
    assert env["script"].read_bytes() == HUMAN_LATER
    # 对照：回到 AI 那一版之后，同一个重启后的会话照常能回滚
    env["script"].write_bytes(AFTER_AI)
    assert ai_bridge.revert(sid)["ok"] is True
    assert env["script"].read_bytes() == BEFORE_AI


def test_reverting_twice_is_a_harmless_no_op_not_a_conflict(env, monkeypatch):
    """脚本已经等于快照：再点一次回滚没有任何东西会丢，不报冲突。"""
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    ai_bridge.revert(sid)
    assert ai_bridge.revert(sid)["ok"] is True
    assert env["script"].read_bytes() == BEFORE_AI


def test_a_session_without_a_record_still_reverts(env, monkeypatch):
    """「不知道」是独立一档：没有 AI 改完那一版的记录（会话结束前后端就重启了 / 老 sidecar）
    时判不出之后有没有别的改动，回滚照旧可用——不把「判不出」读成「冲突」。"""
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    ai_bridge.SESSIONS.pop(sid)
    side = ai_bridge.SNAP_DIR / f"{sid}.json"
    meta = json.loads(side.read_text(encoding="utf-8"))
    del meta["after_sha256"]
    side.write_text(json.dumps(meta), encoding="utf-8")
    env["script"].write_bytes(HUMAN_LATER)
    assert ai_bridge.revert(sid)["ok"] is True
    assert env["script"].read_bytes() == BEFORE_AI


def test_the_http_endpoint_answers_409_with_the_stable_code(env, monkeypatch):
    sid = _ai_session(env, monkeypatch, AFTER_AI)
    env["script"].write_bytes(HUMAN_LATER)
    tavotto_app.app.config["TESTING"] = True
    resp = tavotto_app.app.test_client().post(f"/api/ai/sessions/{sid}/revert")
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "ai_revert_conflict"
    assert body["params"] == {"script": "fig1.py"}
    assert env["script"].read_bytes() == HUMAN_LATER
