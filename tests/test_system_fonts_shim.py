"""`tests/support/system_fonts_shim.py`：会话内只真跑一次 `system_profiler -xml SPFontsDataType`。"""

import os
import subprocess
import sys

import pytest

from support import system_fonts_shim as shim

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="垫片是 /bin/sh 脚本，只在 macOS 上装"
)

FONTS = ["system_profiler", "-xml", "SPFontsDataType"]


def _fake_real(tmp_path, *, fail_fonts=False):
    """假的 /usr/sbin/system_profiler：每次调用往 calls 追加一行参数。"""
    calls = tmp_path / "calls"
    real = tmp_path / "real_system_profiler"
    fail = 'if [ "$2" = "SPFontsDataType" ]; then exit 3; fi\n' if fail_fonts else ""
    real.write_text(
        f'#!/bin/sh\necho "$*" >> "{calls}"\n{fail}echo "<plist>$*</plist>"\n',
        encoding="utf-8",
    )
    real.chmod(0o755)
    return real, calls


def _install(tmp_path, real):
    env = {"PATH": "/usr/bin:/bin"}
    shim_dir = shim.install(env, real=str(real), platform="darwin")
    assert shim_dir is not None
    return env, shim_dir


def _run(env, args):
    return subprocess.run(
        args, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
    )


def _calls(calls):
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


@posix_only
def test_font_listing_runs_the_real_command_once_per_session(tmp_path):
    real, calls = _fake_real(tmp_path)
    env, shim_dir = _install(tmp_path, real)
    assert env["PATH"].split(os.pathsep)[0] == shim_dir

    first, second = _run(env, FONTS), _run(env, FONTS)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout == "<plist>-xml SPFontsDataType</plist>\n"
    assert _calls(calls) == ["-xml SPFontsDataType"]


@posix_only
def test_other_invocations_pass_through_every_time(tmp_path):
    real, calls = _fake_real(tmp_path)
    env, _ = _install(tmp_path, real)

    for args in (
        ["system_profiler", "SPDisplaysDataType", "-json"],
        ["system_profiler", "-xml", "SPFontsDataType", "-detailLevel", "mini"],
        ["system_profiler", "-json", "SPFontsDataType"],
        ["system_profiler", "-json", "SPFontsDataType"],
        ["system_profiler", "SPDisplaysDataType", "-json"],
    ):
        out = _run(env, args)
        assert out.returncode == 0
        assert out.stdout == f"<plist>{' '.join(args[1:])}</plist>\n"

    assert _calls(calls) == [
        "SPDisplaysDataType -json",
        "-xml SPFontsDataType -detailLevel mini",
        "-json SPFontsDataType",
        "-json SPFontsDataType",
        "SPDisplaysDataType -json",
    ]


@posix_only
def test_failed_listing_is_reported_and_not_cached(tmp_path):
    real, calls = _fake_real(tmp_path, fail_fonts=True)
    env, shim_dir = _install(tmp_path, real)

    assert _run(env, FONTS).returncode == 3
    assert _run(env, FONTS).returncode == 3
    assert _calls(calls) == ["-xml SPFontsDataType"] * 2
    assert sorted(os.listdir(shim_dir)) == ["system_profiler"]


def test_other_platforms_leave_path_alone(tmp_path):
    real, _ = _fake_real(tmp_path)
    for platform in ("linux", "win32"):
        env = {"PATH": "/usr/bin:/bin"}
        assert shim.install(env, real=str(real), platform=platform) is None
        assert env == {"PATH": "/usr/bin:/bin"}


@pytest.mark.skipif(sys.platform != "darwin", reason="只在 macOS 上装")
def test_session_children_resolve_the_shim_first():
    # conftest 在 import 时装好：本进程与之后起的子进程按 PATH 找到的都是垫片。
    found = subprocess.run(
        ["/bin/sh", "-c", "command -v system_profiler"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.strip()
    assert os.path.basename(os.path.dirname(found)).startswith("tavotto-sysfonts-"), found
