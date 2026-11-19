"""AI 修复的小实验 `run_snippet`（ADR 0197）：真子进程 + 真守卫。

能读项目数据、能 import 项目模块；写不进项目、连不了网、读不到凭据位置、起不了进程；不算「改动」。
没有操作系统沙盒时进程内守卫照样拦；不带严格段的调用（试跑 / 探 import）守卫前导与沙盒参数不变。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


# ---------------------------------------------------------------- Codex #2064 三条 P1（都在没有操作系统沙盒时复现）
def _no_os_sandbox(monkeypatch):
    monkeypatch.setattr(trial, "_find_tool", lambda _kind: None)


def test_project_modules_named_like_stdlib_never_run_before_the_guard(box, monkeypatch, tmp_path):
    """项目里的 `json.py` / `tempfile.py` 不能在守卫装好之前被前导 import 执行（P1 ①）。试跑的诊断副本同一条前导，一并看护。"""
    _no_os_sandbox(monkeypatch)
    marker = tmp_path / "PWNED"
    for name in ("json.py", "tempfile.py"):
        put(box.proj / name, f"open({str(marker)!r}, 'w').write('x')\n")
    put(box.proj / "fig.py", "print('fig ran')\n")
    ctx = make_ctx(box, environments={"e": sys.executable}, script_path=box.proj / "fig.py")
    r = call(ctx, "run_snippet", {"env": "e", "code": "print('hello')"})
    assert not r.is_error and "hello" in r.content, r.content
    assert not marker.exists()
    call(ctx, "try_run", {"env": "e", "mode": "diagnostic"})
    assert not marker.exists()


def test_credentials_cannot_be_read_through_a_directory_descriptor(box, monkeypatch):
    """`os.open(home)` 再 `os.open('.ssh/id_rsa', dir_fd=…)`：审计事件看不到 dir_fd，所以不许把文件夹当文件打开（P1 ②）。"""
    _no_os_sandbox(monkeypatch)
    put(box.home / ".ssh" / "id_rsa", "SECRET-KEY-MATERIAL")
    code = (
        "import os\n"
        f"d = os.open({str(box.home)!r}, os.O_RDONLY)\n"
        "fd = os.open('.ssh/id_rsa', os.O_RDONLY, dir_fd=d)\n"
        "print(os.read(fd, 100))\n"
    )
    r = call(_ctx(box), "run_snippet", {"env": "e", "code": code})
    assert "SECRET-KEY-MATERIAL" not in r.content and "blocked_reads" in r.content


@pytest.mark.parametrize(
    "attack",
    [
        # 沿调用栈找到前导，把拒读清单清空
        "import sys\nf = sys._getframe()\nwhile f is not None:\n    for k in list(f.f_globals):\n        if 'deny' in k.lower():\n            f.f_globals[k] = []\n    f = f.f_back\n",
        # 经 gc 找到装着拒读清单的对象，清空它
        "import gc\nfor o in gc.get_objects():\n    if isinstance(o, list) and any('.ssh' in str(x) for x in o):\n        o.clear()\n",
    ],
)
def test_the_code_under_test_cannot_switch_the_guard_off(box, monkeypatch, attack):
    """判据状态不在可改的全局里、函数对象拿不到（P1 ③）：改完照样读不到凭据。"""
    _no_os_sandbox(monkeypatch)
    put(box.home / ".ssh" / "id_rsa", "SECRET-KEY-MATERIAL")
    code = (
        attack
        + f"try:\n    print(open({str(box.home / '.ssh' / 'id_rsa')!r}).read())\nexcept Exception as e:\n    print('blocked', type(e).__name__)\n"
    )
    r = call(_ctx(box), "run_snippet", {"env": "e", "code": code})
    assert "SECRET-KEY-MATERIAL" not in r.content, r.content


def test_a_pythonpath_entry_under_the_interpreter_prefix_never_runs_before_the_guard(
    box, monkeypatch, tmp_path
):
    """Codex #2064 复核 P1：PYTHONPATH 指向解释器安装前缀下的目录（不是标准库），那里的 `json.py` 也不能先于守卫执行。
    判据按 PYTHONPATH 的精确条目去，不按前缀猜。
    注：venv 里守卫前导运行时 `sys.prefix` 还是基础解释器（site 之后才换成 venv），所以本地 venv 上旧判据碰巧也挡住了；
    CI 与 Codex 的复现用的是不带 venv 的解释器（前缀就是它自己的目录），旧判据在那里放行。"""
    import shutil
    import uuid

    _no_os_sandbox(monkeypatch)
    marker = tmp_path / "PWNED"
    evil = Path(sys.prefix) / f"_tavotto_test_{uuid.uuid4().hex[:8]}"
    evil.mkdir()
    try:
        (evil / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", str(evil))
        r = call(_ctx(box), "run_snippet", {"env": "e", "code": "print('hello')"})
        assert not marker.exists()
        assert not r.is_error and "hello" in r.content, r.content
    finally:
        shutil.rmtree(evil, ignore_errors=True)


def test_a_diagnostic_copy_still_imports_the_scripts_sibling_modules(box):
    """Codex #2064 复核 P2：去掉 '' 之后，脚本所在目录由前导显式放回最前（与 `python fig.py` 一致），同目录的辅助模块照常 import。"""
    put(box.proj / "helper.py", "VALUE = 42\n")
    put(box.proj / "fig.py", "import helper\nprint('value', helper.VALUE)\n")
    ctx = make_ctx(box, environments={"e": sys.executable}, script_path=box.proj / "fig.py")
    r = call(ctx, "try_run", {"env": "e", "mode": "diagnostic"})
    assert "ModuleNotFoundError" not in r.content, r.content


@pytest.mark.parametrize("shape", ["{d}/./evil", "{d}/evil/", "{d}/x/../evil"])
def test_no_pythonpath_spelling_runs_before_the_guard(box, monkeypatch, tmp_path, shape):
    """Codex #2064 第三轮 P1：解释器会把 PYTHONPATH 条目展开、规范化（`/x/./evil` → `/x/evil`），按条目猜总会漏。
    结构性修法：解释器启动时根本看不到 PYTHONPATH，守卫装好后才放回——任何写法都进不了守卫前的路径。"""
    _no_os_sandbox(monkeypatch)
    marker = tmp_path / "PWNED"
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    (tmp_path / "x").mkdir()
    monkeypatch.setenv("PYTHONPATH", shape.format(d=tmp_path))
    r = call(_ctx(box), "run_snippet", {"env": "e", "code": "print('hello')"})
    assert not marker.exists(), shape
    assert not r.is_error and "hello" in r.content, r.content


def test_pythonpath_modules_still_import_once_the_guard_is_up(box, monkeypatch, tmp_path):
    """放回之后与原来一致：用户 PYTHONPATH 里的模块照常能 import，环境里也看得到 PYTHONPATH。"""
    lib = tmp_path / "userlib"
    lib.mkdir()
    (lib / "labtools.py").write_text("ANSWER = 7\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib))
    r = call(
        _ctx(box),
        "run_snippet",
        {
            "env": "e",
            "code": "import os, labtools\nprint('answer', labtools.ANSWER)\nprint('pp', bool(os.environ.get('PYTHONPATH')))\nprint('private', 'TAVOTTO_TRIAL_PYTHONPATH' in os.environ)\n",
        },
    )
    assert "answer 7" in r.content and "pp True" in r.content and "private False" in r.content, (
        r.content
    )


def test_the_bytecode_cache_prefix_is_not_read_before_the_guard(box, monkeypatch, tmp_path):
    """Codex #2064 P1：`PYTHONPYCACHEPREFIX` 下的 .pyc 也会在守卫前被读；启动时挪开，守卫装好后放回（`sys.pycache_prefix` 与环境都恢复）。"""
    prefix = tmp_path / "pyc"
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(prefix))
    r = call(
        _ctx(box),
        "run_snippet",
        {
            "env": "e",
            "code": "import os, sys\nprint('prefix', sys.pycache_prefix)\nprint('env', os.environ.get('PYTHONPYCACHEPREFIX'))\nprint('private', 'TAVOTTO_TRIAL_PYCACHEPREFIX' in os.environ)\n",
        },
    )
    # 输出过了脱敏（用户名换成 <user>），只比路径尾部
    lines = dict(
        line.split(" ", 1)
        for line in r.content.splitlines()
        if line.startswith(("prefix ", "env ", "private "))
    )
    assert lines["prefix"].endswith("/pyc") and lines["env"].endswith("/pyc"), r.content
    assert lines["private"] == "False"


def test_the_interpreter_starts_without_code_location_variables(box, monkeypatch):
    """子进程启动时环境里没有 PYTHONPATH / PYTHONPYCACHEPREFIX（它们在私有变量里，守卫装好后才放回）。"""
    monkeypatch.setenv("PYTHONPATH", "/somewhere/lib")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "/somewhere/pyc")
    seen = {}
    real = trial.subprocess.Popen

    def spy(*a, **kw):
        seen.update(kw.get("env") or {})
        return real(*a, **kw)

    monkeypatch.setattr(trial.subprocess, "Popen", spy)
    call(_ctx(box), "run_snippet", {"env": "e", "code": "print(1)"})
    assert "PYTHONPATH" not in seen and "PYTHONPYCACHEPREFIX" not in seen
    assert seen["TAVOTTO_TRIAL_PYTHONPATH"] == "/somewhere/lib"
    assert seen["TAVOTTO_TRIAL_PYCACHEPREFIX"] == "/somewhere/pyc"
