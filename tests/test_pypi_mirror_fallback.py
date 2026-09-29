"""PyPI 镜像回退（ADR 0111）：先用官方 / 用户自己的配置装；**仅当**这次 pip 是网络类失败、且这个环境
没有用户自配的包源时，改用固定镜像重试**一次**。

判据的主语：

* 「网络类失败」= **这一次** pip 进程的结局：退出码非零 + 输出带网络特征（`_run_pip` → `classify_pip_failure`
  给出 `ERROR_NETWORK`）；退出码 0 哪怕输出里有 Retrying 也是成功。用真子进程量（`python -c` 按剧本打印 +
  以给定退出码退出），不桩 `_run_pip`。
* 「用户自配了源」= 这个环境的 pip 配置（环境变量四个 + `pip config list` 的四个键），与诊断里只问 index 的
  `custom_package_index` 是超集关系。
* 「重试了几次、用的什么源」= 剧本进程每次被起来时写下的 argv（`runs.jsonl`）。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import deprepair

MIRROR = deprepair.PYPI_MIRROR_URL
NETWORK_OUT = (
    "WARNING: Retrying (Retry(total=4, connect=None, read=None)) after connection broken by "
    "'NewConnectionError(...: Failed to establish a new connection: [Errno 8] nodename nor servname "
    "provided, or not known')': /simple/lmfit/\n"
    "ERROR: Could not find a version that satisfies the requirement lmfit (from versions: none)\n"
)


@pytest.fixture(autouse=True)
def _no_user_source_env(monkeypatch):
    for name in deprepair._PIP_SOURCE_ENV:
        monkeypatch.delenv(name, raising=False)


def _script_argv(runs: Path, *, default: tuple[int, str], mirror: tuple[int, str]):
    """`build_argv(index_url)`：起一个真子进程，把自己拿到的 argv 记进 `runs`，按「用没用镜像」打印剧本并以
    给定退出码退出（与真 pip 的 argv 同形：`--index-url <url>` 出现在参数里）。"""

    def build(index_url):
        rc, out = mirror if index_url else default
        code = (
            "import json, sys; "
            f"open({str(runs)!r}, 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n'); "
            f"sys.stdout.write({out!r}); sys.exit({rc})"
        )
        return [sys.executable, "-c", code, *(["--index-url", index_url] if index_url else [])]

    return build


def _runs(path: Path) -> list[list[str]]:
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    except OSError:
        return []


# ---------------------------------------------------------------- 纯函数：要不要换源
@pytest.mark.parametrize(
    "code,user_source,expected",
    [
        (deprepair.ERROR_NETWORK, False, True),
        (deprepair.ERROR_NETWORK, True, False),  # 用户配过源：一律不动
        (deprepair.ERROR_NETWORK, None, False),  # 问不出来：宁可不换
        ("", False, False),  # 成功
        (deprepair.ERROR_CANCELLED, False, False),
        (deprepair.ERROR_TIMEOUT, False, False),
        (deprepair.ERROR_CONFLICT, False, False),
        (deprepair.ERROR_NOT_FOUND, False, False),
        (deprepair.ERROR_REQUIRES_BUILD, False, False),
        (deprepair.ERROR_HASH_MISMATCH, False, False),
        (deprepair.ERROR_FAILED, False, False),
    ],
)
def test_mirror_retry_is_warranted_only_for_network_failures_without_a_user_source(
    code, user_source, expected
):
    assert deprepair.mirror_retry_warranted(code, user_source) is expected


@pytest.mark.parametrize("rc,expected", [(1, deprepair.ERROR_NETWORK), (0, "")])
def test_network_code_needs_a_nonzero_exit_not_just_the_markers(rc, expected):
    """同一段带 Retrying / 连不上 的输出：退出码非零才是网络类失败；pip 重试后装成了（退出码 0）就是成功。"""
    argv = [sys.executable, "-c", f"import sys; sys.stdout.write({NETWORK_OUT!r}); sys.exit({rc})"]
    code, out = deprepair._run_pip(argv, threading.Event(), None)
    assert "Retrying" in out
    assert code == expected


# ---------------------------------------------------------------- 执行器：至多一次、写明镜像
def test_network_failure_without_a_user_source_retries_once_on_the_mirror(tmp_path, monkeypatch):
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(deprepair, "user_package_source", lambda python: False)
    logs: list[str] = []
    mirrors: list[str] = []
    code, out = deprepair._run_pip_install(
        _script_argv(runs, default=(1, NETWORK_OUT), mirror=(0, "Successfully installed lmfit\n")),
        sys.executable,
        threading.Event(),
        logs.append,
        on_mirror=mirrors.append,
    )
    assert code == ""
    assert [("--index-url" in r) for r in _runs(runs)] == [False, True]
    assert _runs(runs)[1][-2:] == ["--index-url", MIRROR]
    assert mirrors == [MIRROR]
    assert any(MIRROR in line for line in logs) and MIRROR in out


def test_the_mirror_is_tried_at_most_once(tmp_path, monkeypatch):
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(deprepair, "user_package_source", lambda python: False)
    code, _out = deprepair._run_pip_install(
        _script_argv(runs, default=(1, NETWORK_OUT), mirror=(1, NETWORK_OUT)),
        sys.executable,
        threading.Event(),
        None,
    )
    assert code == deprepair.ERROR_NETWORK
    assert len(_runs(runs)) == 2


@pytest.mark.parametrize("env", deprepair._PIP_SOURCE_ENV)
def test_a_user_configured_source_is_never_bypassed(tmp_path, monkeypatch, env):
    """用户配过源（index / extra-index / 离线 wheelhouse）：网络失败也如实报失败，不去镜像。"""
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setenv(env, "1" if env == "PIP_NO_INDEX" else "https://pypi.corp/simple")
    mirrors: list[str] = []
    code, _out = deprepair._run_pip_install(
        _script_argv(runs, default=(1, NETWORK_OUT), mirror=(0, "")),
        sys.executable,
        threading.Event(),
        None,
        on_mirror=mirrors.append,
    )
    assert code == deprepair.ERROR_NETWORK
    assert len(_runs(runs)) == 1 and mirrors == []


def test_a_non_network_failure_is_not_retried_and_does_not_probe_the_config(tmp_path, monkeypatch):
    runs = tmp_path / "runs.jsonl"
    monkeypatch.setattr(
        deprepair, "user_package_source", lambda python: pytest.fail("非网络失败不该去问配置")
    )
    code, _out = deprepair._run_pip_install(
        _script_argv(runs, default=(1, "ERROR: ResolutionImpossible\n"), mirror=(0, "")),
        sys.executable,
        threading.Event(),
        None,
    )
    assert code == deprepair.ERROR_CONFLICT and len(_runs(runs)) == 1


@pytest.mark.parametrize(
    "rc,out,expected",
    [
        (0, "global.index-url='https://pypi.corp/simple'\n", True),
        (0, "global.find-links='/wheels'\n", True),
        (0, "global.no-index='true'\n", True),
        (0, "global.timeout='60'\n", False),
        (0, "", False),
        (1, "pip: error", None),
    ],
)
def test_user_package_source_reads_the_pip_config(monkeypatch, rc, out, expected):
    monkeypatch.setattr(deprepair, "_run", lambda argv, timeout: (rc, out))
    assert deprepair.user_package_source("/env/bin/python") is expected


# ---------------------------------------------------------------- 两条 argv 出处
def test_both_argv_sources_take_the_mirror_only_when_asked():
    r, c = Path("r.txt"), Path("c.txt")
    single = deprepair.pip_install_argv("py", "lmfit>=1.3", index_url=MIRROR)
    joint = deprepair.pip_install_joint_argv("py", r, c, index_url=MIRROR)
    for argv in (single, joint):
        i = argv.index("--index-url")
        assert argv[i + 1] == MIRROR and argv[:4] == ["py", "-m", "pip", "install"]
    assert single[-1] == "lmfit>=1.3"  # 需求仍在最后
    assert "--index-url" not in deprepair.pip_install_argv("py", "lmfit>=1.3")
    assert "--index-url" not in deprepair.pip_install_joint_argv("py", r, c)


def test_the_single_package_install_goes_through_the_mirror_fallback(monkeypatch):
    """`_pip_install`（单包修复 / 包管理）走 `_run_pip_install`：第二次的 argv 出自 `pip_install_argv(index_url=镜像)`。"""
    seen: list[list[str]] = []

    def _fake_run_pip(argv, ev, log):
        seen.append(argv)
        return (deprepair.ERROR_NETWORK, NETWORK_OUT) if len(seen) == 1 else ("", "ok")

    monkeypatch.setattr(deprepair, "_run_pip", _fake_run_pip)
    monkeypatch.setattr(deprepair, "user_package_source", lambda python: False)
    mirrors: list[str] = []
    code, _ = deprepair._pip_install(
        "/env/bin/python", "lmfit>=1.3", threading.Event(), None, on_mirror=mirrors.append
    )
    assert code == ""
    assert seen == [
        deprepair.pip_install_argv("/env/bin/python", "lmfit>=1.3"),
        deprepair.pip_install_argv("/env/bin/python", "lmfit>=1.3", index_url=MIRROR),
    ]
    assert mirrors == [MIRROR]
