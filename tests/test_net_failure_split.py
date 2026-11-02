"""联网失败原因分清（A）、查包版本的镜像回退（B）、自管 pip 子进程 UTF-8（C）。

一次网络请求都不发：`_run_pip` / `_run_lookup` 全部替换成假货。
"""

from __future__ import annotations

import subprocess

import pytest

from tavotto.engine import deprepair, managedenv, runtime

OFFLINE = (
    "WARNING: Retrying (Retry(total=0)) after connection broken by "
    "'NewConnectionError(Failed to establish a new connection: [Errno 61] Connection refused)'\n"
    "ERROR: No matching distribution found for lmfit\n"
)
FOUND = "lmfit (1.3.4)\nAvailable versions: 1.3.4, 1.3.3\n"


# --------------------------------------------------------------------- A
def _install_failing(monkeypatch, user_source, code):
    """`_run_pip` 恒回 code；用户源的真假由 `user_package_source` 决定。回 (发出的 argv 列表, 源通知列表)。"""
    argvs: list = []
    sources: list = []
    monkeypatch.setattr(deprepair, "user_package_source", lambda python: user_source)

    def _fake(argv, cancel_ev, on_log, *, deadline=None, watch=None, on_started=None):
        argvs.append(list(argv))
        if on_started is not None:
            on_started()
        return code, ""

    monkeypatch.setattr(deprepair, "_run_pip", _fake)
    import threading

    got, _ = deprepair._run_pip_install(
        lambda url: ["pip", url or "-"],
        "/py",
        threading.Event(),
        None,
        on_source=sources.append,
    )
    return got, argvs, sources


def _terminal(code, source):
    pid = "a" * 32
    with deprepair._lock:
        deprepair._progress[pid] = {"log": "", "pypi_source": source}
    try:
        return deprepair._emit(pid, deprepair.STATE_FAILED, None, code=code, error="x")
    finally:
        with deprepair._lock:
            deprepair._progress.pop(pid, None)


@pytest.mark.parametrize("code", [deprepair.ERROR_NETWORK, deprepair.ERROR_TIMEOUT])
def test_official_and_mirror_both_failed_is_named(monkeypatch, code):
    got, argvs, sources = _install_failing(monkeypatch, False, code)
    assert got == code and len(argvs) == 2, "没自配源：官方失败后换镜像再试一次（换源条件不变）"
    assert sources[-1] == deprepair.PIP_SOURCE_MIRROR
    assert _terminal(code, sources[-1])["net_cause"] == deprepair.NET_CAUSE_BOTH


@pytest.mark.parametrize("code", [deprepair.ERROR_NETWORK, deprepair.ERROR_TIMEOUT])
def test_a_dead_user_source_is_named_and_never_switched(monkeypatch, code):
    got, argvs, sources = _install_failing(monkeypatch, True, code)
    assert got == code and len(argvs) == 1, "反证：用户自配源不换源"
    assert sources == [deprepair.PIP_SOURCE_USER]
    assert _terminal(code, sources[-1])["net_cause"] == deprepair.NET_CAUSE_USER_SOURCE


def test_unknown_source_and_non_network_codes_carry_no_cause(monkeypatch):
    assert "net_cause" not in _terminal(deprepair.ERROR_NETWORK, deprepair.PIP_SOURCE_UNKNOWN)
    assert "net_cause" not in _terminal(deprepair.ERROR_NETWORK, deprepair.PIP_SOURCE_PYPI)
    assert "net_cause" not in _terminal(deprepair.ERROR_CONFLICT, deprepair.PIP_SOURCE_USER)
    assert set(deprepair.NET_CAUSES) == {"official_and_mirror", "user_source"}


# --------------------------------------------------------------------- B
@pytest.fixture
def env(tmp_path, monkeypatch):
    managedenv.write_manifest(tmp_path, managedenv.new_manifest(tmp_path, "/x/py"))
    py = managedenv.venv_python(tmp_path)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    managedenv.mark_ready(tmp_path)
    monkeypatch.setattr(deprepair, "custom_package_index", lambda python: False)
    return tmp_path


def _lookup_script(monkeypatch, results, user_source=False):
    calls: list = []

    def _fake(argv, timeout=None):
        calls.append((list(argv), timeout))
        return results[min(len(calls), len(results)) - 1]

    monkeypatch.setattr(deprepair, "_run_lookup", _fake)
    monkeypatch.setattr(deprepair, "lookup_package_source", lambda python, timeout=30: user_source)
    monkeypatch.setattr(deprepair, "user_package_source", lambda python: user_source)
    return calls


@pytest.mark.parametrize("first", [(1, OFFLINE, False), (1, "", True)])
def test_lookup_falls_back_to_the_mirror_when_official_is_unreachable(env, monkeypatch, first):
    calls = _lookup_script(monkeypatch, [first, (0, FOUND, False)])
    out = deprepair.lookup_package(env, "lmfit")
    assert out["latest"] == "1.3.4" and len(calls) == 2
    assert "--index-url" not in calls[0][0]
    second, budget = calls[1]
    assert second[second.index("--index-url") + 1] == deprepair.PYPI_MIRROR_URL
    assert second[-2:] == ["--", "lmfit"]
    assert budget <= deprepair.LOOKUP_MIRROR_TIMEOUT_S


def test_lookup_reports_the_mirror_failure_when_both_fail(env, monkeypatch):
    calls = _lookup_script(monkeypatch, [(1, OFFLINE, False), (1, OFFLINE, False)])
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(env, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_OFFLINE and len(calls) == 2
    calls = _lookup_script(monkeypatch, [(1, OFFLINE, False), (1, "", True)])
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(env, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_TIMEOUT


def test_lookup_never_switches_a_user_configured_source(env, monkeypatch):
    for user in (True, None):
        calls = _lookup_script(monkeypatch, [(1, "", True), (0, FOUND, False)], user_source=user)
        with pytest.raises(deprepair.RepairError) as err:
            deprepair.lookup_package(env, "lmfit")
        assert err.value.code == deprepair.ERROR_LOOKUP_TIMEOUT and len(calls) == 1


def test_lookup_does_not_retry_on_not_found_or_parse_failure(env, monkeypatch):
    calls = _lookup_script(
        monkeypatch, [(1, "ERROR: No matching distribution found for lmfit\n", False)]
    )
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(env, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_NOT_FOUND and len(calls) == 1


def test_lookup_mirror_budget_is_bounded_by_the_total(env, monkeypatch):
    ticks = iter([0.0, 13.5, 13.5, 13.5, 13.5])
    monkeypatch.setattr(deprepair.time, "monotonic", lambda: next(ticks, 13.5))
    calls = _lookup_script(monkeypatch, [(1, "", True), (0, FOUND, False)])
    with pytest.raises(deprepair.RepairError):
        deprepair.lookup_package(env, "lmfit")
    assert len(calls) == 1, "总预算只剩不到 1 s：不起镜像那次"


def _probe_json(index_url):
    import json

    return json.dumps(
        {
            "pip_version": "24",
            "index_url": index_url,
            "extra_index_urls": [],
            "no_index": False,
            "find_links": [],
        }
    )


def test_lookup_honours_a_source_configured_only_for_the_index_command(env, monkeypatch):
    """`[index] index-url = 私有仓库`：install 没配、`pip index` 实际生效选项配了——不许换去公共镜像。"""
    calls: list = []

    def _fake_lookup(argv, timeout=None):
        calls.append(list(argv))
        return (1, "", True)

    def _fake_run(argv, timeout):
        private = 'create_command("index")' in argv[2]
        return 0, _probe_json(
            "https://private.example/simple" if private else deprepair.PYPI_DEFAULT_INDEX
        )

    monkeypatch.setattr(deprepair, "_run_lookup", _fake_lookup)
    monkeypatch.setattr(deprepair, "_run", _fake_run)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(env, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_TIMEOUT and len(calls) == 1


def test_lookup_source_probe_cannot_blow_the_total_budget(env, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(deprepair.time, "monotonic", lambda: clock[0])
    calls: list = []

    def _fake_lookup(argv, timeout=None):
        calls.append(list(argv))
        clock[0] += deprepair.LOOKUP_TIMEOUT_S
        return (1, "", True)

    def _slow_probe(argv, timeout):  # 阻塞到自己的超时才返回
        clock[0] += timeout
        return 1, f"超时（{timeout}s）"

    monkeypatch.setattr(deprepair, "_run_lookup", _fake_lookup)
    monkeypatch.setattr(deprepair, "_run", _slow_probe)
    with pytest.raises(deprepair.RepairError):
        deprepair.lookup_package(env, "lmfit")
    assert clock[0] <= deprepair.LOOKUP_TOTAL_S, "整个 lookup 不超总预算"
    assert len(calls) == 1, "探测超时 = 问不出：不起镜像"


def test_index_url_only_accepts_the_builtin_mirror():
    with pytest.raises(deprepair.RepairError):
        deprepair.pip_index_argv("/py", "lmfit", "http://evil/simple")


# --------------------------------------------------------------------- C
def test_pip_child_env_sets_utf8_only_for_owned_pythons(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "is_owned_python", lambda p: str(p) == "/owned/py")
    base = {"A": "1"}
    out = runtime.pip_child_env("/owned/py", base)
    assert out["PYTHONUTF8"] == "1" and out["PYTHONIOENCODING"] == "utf-8" and out["A"] == "1"
    assert "PYTHONUTF8" not in base, "不改传入的字典"
    assert runtime.pip_child_env("/user/venv/py", base) is base, "别人的环境原样"
    assert runtime.pip_child_env("/owned/py", None) is None


def test_owned_env_itself_stays_free_of_utf8_mode(tmp_path, monkeypatch):
    """反证：用户脚本 worker 的环境来源 `owned_env` 不带 UTF-8 模式。"""
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setattr(runtime, "_owned_cache_root", lambda p: str(tmp_path))
    env = runtime.owned_env("/owned/py")
    assert "PYTHONUTF8" not in env and "PYTHONIOENCODING" not in env


def test_pip_subprocesses_get_the_utf8_env(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "is_owned_python", lambda p: True)
    monkeypatch.setattr(runtime, "_owned_cache_root", lambda p: str(tmp_path))
    seen: list = []

    class _Done:
        returncode = 0
        stdout = FOUND
        stderr = ""

    def _run(argv, **kw):
        seen.append(kw["env"])
        return _Done()

    monkeypatch.setattr(subprocess, "run", _run)
    deprepair._run_lookup(deprepair.pip_index_argv("/owned/py", "lmfit"))
    deprepair._run(["/owned/py", "-m", "pip", "--version"], 5)
    assert len(seen) == 2
    assert all(e["PYTHONUTF8"] == "1" and e["PYTHONIOENCODING"] == "utf-8" for e in seen)

    popen_env: list = []

    class _P:
        def __init__(self, argv, **kw):
            popen_env.append(kw["env"])
            raise OSError("stop")

    monkeypatch.setattr(subprocess, "Popen", _P)
    import threading

    deprepair._run_pip(["/owned/py", "-m", "pip", "install", "x"], threading.Event(), None)
    assert popen_env and popen_env[0]["PYTHONUTF8"] == "1"
