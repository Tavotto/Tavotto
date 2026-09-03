"""会话结束线程门禁自己的看护（`tests/conftest.py` 的 `pytest_sessionfinish`）。

那条门禁守的是**解释器退出**那一刻：用例全绿、pytest 打完摘要之后，
`threading._shutdown()` 去 join 泄漏的非 daemon 线程，永久挂住（2026-08-28 那次
8 小时 20 分的 job，根因见 `d9e2b60`）。正常会话里那一刻什么都不会发生——**所以
这条门禁在正常套件里一次都不会被执行**，而从没跑过的门禁不会保持正确。这里把
issue #196 落地时的那次手工反证做成结构：每跑一次套件，就真的让它响一次。

判据全部走**子进程真跑 pytest**：门禁的效果是「进程的退出码 + 日志里的那段话」，
在本进程里 import 一下那两个函数量不到它。子进程用 `-p conftest` 挂的是仓库里
**那一份真的** `tests/conftest.py`，不是复制品——复制品会漂，漂了这里还是绿的。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: 门禁失灵的表现正是**永远不返回**（解释器挂在 `threading._shutdown()` 上）。
#: 没有这个上限，把门禁拿掉做反证时整条套件会跟着一起挂死——那正是我们在防的事。
_SESSION_TIMEOUT_SECONDS = 120.0


def _run_session(
    tmp_path: Path, body: str, *, plugin: str | None = None, **env_overrides: str
) -> subprocess.CompletedProcess:
    """在临时目录里跑一个只有一条用例的 pytest 会话，挂上真的 `tests/conftest.py`。

    `cwd` 在仓库之外，所以子进程捡不到仓库的 `pytest.ini`（那里的 `-q` 会叠成
    `-qq`，摘要行直接消失）；`-p conftest` 靠 `PYTHONPATH` 里的 `tests/` 解析。
    """
    (tmp_path / "test_one.py").write_text(textwrap.dedent(body), encoding="utf-8")
    extra: list[str] = []
    if plugin is not None:
        (tmp_path / "helper_plugin.py").write_text(textwrap.dedent(plugin), encoding="utf-8")
        extra = ["-p", "helper_plugin"]
    env = dict(os.environ, **env_overrides)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(TESTS_DIR), str(tmp_path), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            *extra,
            "test_one.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_SESSION_TIMEOUT_SECONDS,
    )


#: 泄漏一条永远醒不来的非 daemon 线程。`_park_forever` 是**本文件里**的函数：
#: 它出现在报告的栈里，才说明门禁打的是「停在哪一行」而不只是「有几条」。
_LEAKS_A_NON_DAEMON_THREAD = """
    import threading

    _never = threading.Event()


    def _park_forever():
        _never.wait()


    def test_leaks_a_non_daemon_thread():
        threading.Thread(target=_park_forever, name="deliberate-leak").start()
"""


def test_a_leaked_non_daemon_thread_fails_the_session_and_gets_named(tmp_path):
    """用例本身全过，但会话必须红，而且要说得出是谁、停在哪一行。"""
    proc = _run_session(tmp_path, _LEAKS_A_NON_DAEMON_THREAD)
    out = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0, f"线程泄漏了却给了 0：\n{out[-3000:]}"
    assert "非 daemon 线程活着" in out, f"没有那句点名：\n{out[-3000:]}"
    assert "deliberate-leak" in out, f"报告里没有线程名，等于还是零信息：\n{out[-3000:]}"
    assert "_park_forever" in out, f"报告里没有栈，「停在哪一行」还是得靠猜：\n{out[-3000:]}"


def test_the_report_stays_readable_under_a_legacy_code_page(tmp_path):
    """Windows 的 runner 上重定向的 stderr 默认是旧代码页（cp1252 / cp936）。

    那里裸 `print()` 一段中文**不会报错**——`sys.stderr` 天生带
    `backslashreplace`，它会安静地把整段话变成 `\\u7528\\u4f8b`。所以这条判据量的
    不是「有没有抛异常」（那永远不会发生，是条恒真的判据），而是**那段话还是不是
    人话**。`PYTHONIOENCODING` 把子进程的 stdio 压成 cp1252 复现。
    """
    proc = _run_session(tmp_path, _LEAKS_A_NON_DAEMON_THREAD, PYTHONIOENCODING="cp1252")
    out = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0, f"旧代码页下门禁哑了：\n{out[-3000:]}"
    assert "deliberate-leak" in out, f"旧代码页下连线程名都没了：\n{out[-3000:]}"
    assert "非 daemon 线程活着" in out, f"报告退化成了转义序列：\n{out[-3000:]}"


def test_daemon_and_joined_threads_are_not_accused(tmp_path):
    """主语是「非 daemon 且还活着」。

    daemon 线程解释器退出时直接丢下，挂不住谁；join 干净的非 daemon 线程早就不在
    了。把这两种也报出来就是诬告，而被诬告过的门禁下一步就是被人关掉。
    """
    proc = _run_session(
        tmp_path,
        """
        import threading


        def test_leaves_a_daemon_thread_and_joins_a_normal_one():
            threading.Thread(target=threading.Event().wait, daemon=True).start()
            t = threading.Thread(target=lambda: None)
            t.start()
            t.join()
        """,
    )
    out = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"没有泄漏却被判红：\n{out[-3000:]}"
    assert "非 daemon 线程活着" not in out, f"诬告了：\n{out[-3000:]}"


def test_a_thread_closed_by_another_plugins_unconfigure_is_not_accused(tmp_path):
    """判据的主语里有**时刻**，而那个时刻是「就要退出了」，不是「用例刚跑完」。

    别的插件完全可以在自己的 `pytest_unconfigure` 里关掉它的非 daemon worker——
    那样解释器根本不会挂。这里造的正是那种会话：线程在 `pytest_sessionfinish` 那
    一刻确实活着（而且活过 5 秒宽限，所以门禁一定记下了嫌疑），随后被插件收干净。
    要求它**绿**：拿过去那一刻的名单直接判红，就是把干净的会话诬告成泄漏。
    """
    proc = _run_session(
        tmp_path,
        """
        def test_nothing():
            pass
        """,
        plugin="""
        import threading

        _release = threading.Event()
        _worker = threading.Thread(target=_release.wait, name="plugin-owned-worker")


        def pytest_configure(config):
            _worker.start()


        def pytest_unconfigure(config):
            # 门禁挂在 trylast 上，所以这一手一定先跑：走到那里时线程已经没了。
            _release.set()
            _worker.join(30)
        """,
    )
    out = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"插件自己收干净了，却被判红：\n{out[-3000:]}"
    assert "非 daemon 线程活着" not in out, f"诬告了：\n{out[-3000:]}"
