"""AI 修复的小实验 `run_snippet`（ADR 0197）：真子进程 + 真守卫。

能读项目数据、能 import 项目模块；写不进项目、连不了网、读不到凭据位置、起不了进程；不算「改动」。
没有操作系统沙盒时进程内守卫照样拦；不带严格段的调用（试跑 / 探 import）守卫前导与沙盒参数不变。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tavotto.engine.fixtools import trial
from test_fixtools import box, call, make_ctx, put  # noqa: F401


def _ctx(box):
    return make_ctx(box, environments={"e": sys.executable})


def test_reads_project_data_and_imports_project_modules(box):
    put(box.proj / "data.csv", "x,y\n1,2\n")
    put(box.proj / "helper.py", "def f():\n    return 41\n")
    r = call(
        _ctx(box),
        "run_snippet",
        {
            "env": "e",
            "code": "import helper\nprint(helper.f() + 1)\nprint(open('data.csv').read())",
        },
    )
    assert not r.is_error, r.content
    assert "exit 0" in r.content and "42" in r.content and "x,y" in r.content


def test_tracebacks_point_at_the_models_own_lines(box):
    """项目根经守卫前导进 `sys.path`，不往模型代码前面拼行：报错行号就是模型写的行号。"""
    r = call(
        _ctx(box), "run_snippet", {"env": "e", "code": "x = 1\ny = 2\nraise ValueError('boom')\n"}
    )
    assert not r.is_error and "exit 1" in r.content
    assert "line 3" in r.content and "ValueError: boom" in r.content


def test_cannot_write_into_the_project(box):
    r = call(_ctx(box), "run_snippet", {"env": "e", "code": "open('out.txt', 'w').write('x')"})
    assert not r.is_error and "blocked_writes" in r.content
    assert not (box.proj / "out.txt").exists()


def test_network_and_credential_reads_are_blocked(box):
    put(box.home / ".ssh" / "id_rsa", "SECRET-KEY-MATERIAL")
    ctx = _ctx(box)
    net = call(
        ctx,
        "run_snippet",
        {"env": "e", "code": "import socket\nsocket.socket().connect(('127.0.0.1', 9))\n"},
    )
    assert not net.is_error and "blocked_network" in net.content
    cred = call(
        ctx,
        "run_snippet",
        {"env": "e", "code": f"print(open({str(box.home / '.ssh' / 'id_rsa')!r}).read())"},
    )
    assert not cred.is_error and "blocked_reads" in cred.content
    assert "SECRET-KEY-MATERIAL" not in cred.content
    listing = call(
        ctx,
        "run_snippet",
        {"env": "e", "code": f"import os\nprint(os.listdir({str(box.home / '.ssh')!r}))"},
    )
    assert "id_rsa" not in listing.content


def test_without_an_os_sandbox_the_in_process_guard_still_blocks(box, monkeypatch):
    monkeypatch.setattr(trial, "_find_tool", lambda _kind: None)
    put(box.home / ".aws" / "credentials", "AKIA-NOT-REAL")
    ctx = _ctx(box)
    r = call(
        ctx,
        "run_snippet",
        {"env": "e", "code": f"print(open({str(box.home / '.aws' / 'credentials')!r}).read())"},
    )
    assert "os_sandbox: none" in r.content and "blocked_reads" in r.content
    assert "AKIA-NOT-REAL" not in r.content
    r = call(
        ctx,
        "run_snippet",
        {"env": "e", "code": "import socket\nsocket.getaddrinfo('localhost', 80)\n"},
    )
    assert "blocked_network" in r.content


def test_cannot_start_processes(box):
    r = call(
        _ctx(box),
        "run_snippet",
        {"env": "e", "code": "import subprocess\nsubprocess.run(['true'])\n"},
    )
    assert not r.is_error and "blocked_spawns" in r.content


def test_an_experiment_is_not_a_change(box):
    """`run` 不在 CHANGE_TIERS：实验不解开「先动手才能起草缺陷」的闸（ADR 0149 修订 #2053）。"""
    ctx = _ctx(box)
    call(ctx, "run_snippet", {"env": "e", "code": "print(1)"})
    call(ctx, "run_snippet", {"env": "e", "code": "raise SystemExit(3)"})
    assert ctx.changes_attempted == 0


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"env": "zzz", "code": "print(1)"}, "env_unknown"),
        ({"env": "e", "code": ""}, "bad_arguments"),
        ({"env": "e", "code": None}, "bad_arguments"),
        ({"env": "e", "code": "a\x00b"}, "bad_arguments"),
        ({"env": "e", "code": "def (:"}, "snippet_syntax_error"),
        ({"env": "e", "code": "x = 1\n" * 5000}, "snippet_too_large"),
    ],
)
def test_rejects_bad_input_without_running(box, monkeypatch, args, code):
    monkeypatch.setattr(trial, "run_python", lambda *_a, **_k: pytest.fail("must not run"))
    assert call(_ctx(box), "run_snippet", args).code == code


def test_timeout_is_clamped_and_strict_mode_is_requested(box, monkeypatch):
    seen = {}

    def fake(python, **kw):
        seen.update(kw)
        return {"status": "ok", "returncode": 0, "stdout": "hi", "stderr": "", "os_sandbox": "none"}

    monkeypatch.setattr(trial, "run_python", fake)
    ctx = _ctx(box)
    assert not call(ctx, "run_snippet", {"env": "e", "code": "print(1)", "timeout_s": 999}).is_error
    assert seen["timeout"] == trial.SNIPPET_TIMEOUT_MAX_S
    assert seen["cwd"] == str(box.proj)
    assert str(box.home / ".ssh") in seen["strict_deny"]


def test_long_output_keeps_the_tail(box):
    r = call(
        _ctx(box),
        "run_snippet",
        {"env": "e", "code": "for i in range(5000):\n    print('line', i)\n"},
    )
    assert "line 4999" in r.content and "earlier output cut" in r.content
    assert len(r.content) < 3 * trial.SNIPPET_OUTPUT_CHARS


# ---------------------------------------------------------------- 不带严格段的调用不变
def test_plain_trial_profile_and_bwrap_args_are_unchanged(tmp_path):
    prof = trial.macos_profile(str(tmp_path))
    assert "network" not in prof and "file-read" not in prof
    args = trial._sandbox_args("bwrap", "/usr/bin/bwrap", str(tmp_path))
    assert "--unshare-net" not in args
    strict = trial._sandbox_args("bwrap", "/usr/bin/bwrap", str(tmp_path), [str(tmp_path)])
    real = os.path.realpath(tmp_path)
    assert "--unshare-net" in strict
    assert any(strict[i : i + 2] == ["--tmpfs", real] for i in range(len(strict) - 1))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec")
def test_the_macos_sandbox_layer_blocks_on_its_own(tmp_path):
    """纵深：不靠进程内守卫，单是 macOS profile 就断网、拒读清单里的位置。"""
    secret = tmp_path / "creds"
    secret.mkdir()
    (secret / "k").write_text("SECRET")
    work = tmp_path / "work"
    work.mkdir()
    prof = trial.macos_profile(str(work), [str(secret)])
    for code in (
        "import socket; socket.create_connection(('127.0.0.1', 9), timeout=2)",
        f"print(open({str(secret / 'k')!r}).read())",
    ):
        r = subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", prof, sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert r.returncode != 0 and "Operation not permitted" in r.stderr, (code, r.stderr)
        assert "SECRET" not in r.stdout


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="不分大小写的默认文件系统")
def test_a_differently_cased_credential_path_is_still_blocked_without_an_os_sandbox(
    box, monkeypatch
):
    """`~/.SSH/id_rsa` 在 macOS / Windows 上打开的就是 `~/.ssh/id_rsa`：进程内守卫比较前 casefold（自审发现的绕过）。"""
    monkeypatch.setattr(trial, "_find_tool", lambda _kind: None)
    put(box.home / ".ssh" / "id_rsa", "SECRET-KEY-MATERIAL")
    variant = str(box.home / ".SSH" / "ID_RSA")
    r = call(_ctx(box), "run_snippet", {"env": "e", "code": f"print(open({variant!r}).read())"})
    assert "blocked_reads" in r.content and "SECRET-KEY-MATERIAL" not in r.content
