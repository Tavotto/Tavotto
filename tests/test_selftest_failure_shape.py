"""worker 自检没过时，诊断记录里只有闭集形状（TVD-JXMR）。

一份用户反馈里受管环境连续六次「自检未通过」，诊断包只有错误码：失败在哪一步、哪个异常、引擎哪一行都看不到。
这里钉住：`worker_self_test` 每条失败路径都给出 `failure` 形状；形状里只有阶段 / 退出码 / 稳定码 /
`[structure]` 摘要 / 引擎源码位置，报错文字与路径一个字都不出；`envdiag` 读回本地记录时逐字段重投影。
"""

import json
import subprocess

import pytest

from tavotto.engine import deprepair, envdiag

PRIVATE = ("alice", "secret_lab", "patient_123", "C:\\Users")

_TB = (
    "Traceback (most recent call last):\n"
    '  File "C:\\Users\\alice\\secret_lab\\run.py", line 3, in <module>\n'
    '  File "C:\\Program Files\\Tavotto\\_internal\\tavotto\\engine\\worker.py", line 752, in build\n'
    '  File "C:\\Program Files\\Tavotto\\_internal\\tavotto\\engine\\figcapture.py", line 41, in grab\n'
    "PermissionError: [WinError 5] 拒绝访问: 'C:\\Users\\alice\\secret_lab\\patient_123'\n"
)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, timeout=False):
        self._out, self._err, self.returncode, self._timeout = stdout, stderr, returncode, timeout

    def communicate(self, _input=None, timeout=None):
        if self._timeout:
            raise subprocess.TimeoutExpired("worker", timeout)
        return self._out, self._err

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


@pytest.fixture
def fake_worker(monkeypatch):
    """`worker_self_test` 的子进程换成假进程；argv / 激活照走真实现之外的最小桩。"""
    monkeypatch.setattr(
        deprepair.launcher, "activate_within", lambda python, env, budget: (env, budget)
    )
    holder = {}

    def _popen(argv, **kwargs):
        factory = holder["factory"]
        if isinstance(factory, Exception):
            raise factory
        return factory(kwargs)

    monkeypatch.setattr(deprepair.subprocess, "Popen", _popen)
    monkeypatch.setattr(deprepair, "_kill", lambda proc: None)
    return holder


def _no_private(obj):
    text = json.dumps(obj, ensure_ascii=False)
    for word in PRIVATE:
        assert word not in text, word


def test_worker_died_before_replying_keeps_exception_type_and_engine_line(fake_worker):
    fake_worker["factory"] = lambda kw: _FakeProc(stderr=_TB, returncode=1)
    out = deprepair.worker_self_test("python3")
    assert out["ok"] is False
    failure = out["failure"]
    assert failure["stage"] == deprepair.SELFTEST_STAGE_NO_REPLY
    assert failure["exit_code"] == 1
    assert failure["worker_code"] is None
    assert failure["structure"] == ["[structure] PermissionError frames=3 (user=1, engine=2)"]
    assert failure["engine_frame"] == "figcapture.py:41"
    _no_private(failure)
    assert envdiag._selftest(failure) == failure


def test_error_envelope_keeps_the_stable_code(fake_worker, monkeypatch):
    # 信封要回显 request_id：截住 build_envelope 拿到它
    real = deprepair.pool.build_envelope
    seen = {}

    def _envelope(body):
        env = real(body)
        seen.setdefault("build", env)
        return env

    monkeypatch.setattr(deprepair.pool, "build_envelope", _envelope)

    def _reply(kw):
        resp = {
            "protocol_version": deprepair.pool.PROTOCOL_VERSION,
            "request_id": seen["build"]["request_id"],
            "ok": False,
            "error": {"code": "script_error", "message": "patient_123 broke", "traceback": _TB},
        }
        return _FakeProc(stdout=json.dumps(resp) + "\n", returncode=0)

    fake_worker["factory"] = _reply
    failure = deprepair.worker_self_test("python3")["failure"]
    assert failure["stage"] == deprepair.SELFTEST_STAGE_ERROR_REPLY
    assert failure["worker_code"] == "script_error"
    assert failure["structure"] == ["[structure] PermissionError frames=3 (user=1, engine=2)"]
    _no_private(failure)


def test_timeout_and_spawn_failure_have_their_own_stage(fake_worker):
    fake_worker["factory"] = lambda kw: _FakeProc(timeout=True)
    assert deprepair.worker_self_test("python3")["failure"]["stage"] == "timeout"
    fake_worker["factory"] = FileNotFoundError(2, "No such file", "C:\\Users\\alice\\py.exe")
    failure = deprepair.worker_self_test("python3")["failure"]
    assert failure["stage"] == "spawn_failed"
    assert failure["structure"] == ["[structure] FileNotFoundError"]
    _no_private(failure)


def test_stage_tables_are_the_same_on_both_sides():
    assert envdiag._SELFTEST_STAGES == deprepair.SELFTEST_STAGES


def test_reloaded_record_is_reprojected_field_by_field():
    tampered = {
        "stage": "no_reply",
        "exit_code": True,
        "worker_code": "Has Spaces / patient_123",
        "structure": [
            "[structure] PermissionError frames=1 (user=1)",
            "[structure] PatientDataError frames=1 (user=1)",
            "C:\\Users\\alice\\secret_lab",
            42,
        ],
        "engine_frame": "C:\\Users\\alice\\worker.py:1",
        "detail": "patient_123",
    }
    out = envdiag._selftest(tampered)
    assert out == {
        "stage": "no_reply",
        "exit_code": None,
        "worker_code": None,
        "structure": ["[structure] PermissionError frames=1 (user=1)"],
        "engine_frame": None,
    }
    assert envdiag._selftest({"stage": "something_else"}) is None
    assert envdiag._selftest("no_reply") is None
