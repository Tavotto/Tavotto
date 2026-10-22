"""浏览器模式启动的终端输出（`launchhint`）：没有图形会话不开浏览器、登录地址最后打印。

起因（Ubuntu 20.04 经 SSH，2026-10-22）：`tavotto` 自动开浏览器开出了 lynx，占满终端一片空白；
`tavotto --no-browser` 的登录地址被埋在 werkzeug 的红色警告与不带口令的 `Running on …` 上面。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tavotto import launchhint

REPO = Path(__file__).resolve().parents[1]
SSH = {"SSH_CONNECTION": "10.0.0.7 52114 10.191.198.3 22"}


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({}, "linux", False),  # 纯终端：webbrowser 只剩 lynx / w3m
        ({"DISPLAY": ":0"}, "linux", True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", True),
        ({**SSH, "DISPLAY": "localhost:10.0"}, "linux", False),  # X 转发也不开
        ({"SSH_TTY": "/dev/pts/1"}, "linux", False),
        ({}, "darwin", True),
        ({}, "win32", True),
        (SSH, "darwin", False),  # 开出来的是服务器屏幕上的窗口
        (SSH, "win32", False),
    ],
)
def test_can_open_browser(env, platform, expected):
    assert launchhint.can_open_browser(env, platform) is expected


def test_ssh_forward_command_fills_in_the_server_the_client_connected_to():
    assert (
        launchhint.ssh_forward_command(5089, SSH, user="jiaqi")
        == "ssh -N -L 5089:127.0.0.1:5089 jiaqi@10.191.198.3"
    )
    env = {"SSH_CONNECTION": "10.0.0.7 52114 10.191.198.3 2222"}
    assert launchhint.ssh_forward_command(5190, env, user="u") == (
        "ssh -N -L 5190:127.0.0.1:5190 -p 2222 u@10.191.198.3"
    )
    assert "<这台服务器的地址>" in launchhint.ssh_forward_command(5089, {}, user="u")


def test_server_banner_puts_the_login_url_on_its_own_line_with_the_forward_command():
    url = "http://127.0.0.1:5089/#dnonce=abc_-1"
    text = launchhint.ready_banner(url, 5089, opened=False, env=SSH)
    lines = text.splitlines()
    assert f"    {url}" in lines, "登录地址要单独一行、原样可复制"
    forward = next(i for i, ln in enumerate(lines) if "ssh -N -L 5089:127.0.0.1:5089" in ln)
    assert forward < lines.index(f"    {url}"), "先转发端口、再开地址"
    assert "Ctrl+C" in lines[-1]


def test_server_banner_tells_the_user_to_swap_in_the_address_they_log_in_with(monkeypatch):
    monkeypatch.setattr(launchhint.getpass, "getuser", lambda: "jiaqi")
    text = launchhint.ready_banner("http://127.0.0.1:5089/#dnonce=abc", 5089, opened=False, env=SSH)
    hint = launchhint.ssh_host_hint(5089, SSH, user="jiaqi")
    assert hint in text.splitlines()
    assert "~/.ssh/config" in hint and "jiaqi@10.191.198.3" in hint
    lines = text.splitlines()
    assert next(i for i, ln in enumerate(lines) if "ssh -N -L" in ln) < lines.index(hint)


def test_local_banners_do_not_mention_ssh():
    url = "http://127.0.0.1:5089/#dnonce=abc"
    for opened in (True, False):
        text = launchhint.ready_banner(url, 5089, opened=opened, env={"DISPLAY": ":0"})
        assert f"    {url}" in text.splitlines() and "ssh" not in text
        assert "~/.ssh/config" not in text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(
    sys.platform == "win32", reason="BROWSER 探针是 sh 脚本；判据本身已在上面按平台名单测"
)
def test_real_launch_over_ssh_opens_no_browser_and_ends_with_the_login_block(tmp_path):
    """真进程、不带 `--no-browser`、模拟 SSH 会话：`webbrowser` 一次都不被调（`BROWSER` 指向一个
    留痕脚本——被调就留下文件），输出里没有 werkzeug 的横幅，最后一块是转发命令 + 带口令的登录地址。"""
    marker = tmp_path / "browser-was-launched"
    probe = tmp_path / "fake-browser.sh"
    probe.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n', encoding="utf-8")
    probe.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DISPLAY", "WAYLAND_DISPLAY", "TAVOTTO_INSECURE_NO_AUTH")
    }
    env.update(
        SSH,
        BROWSER=str(probe),
        PYTHONPATH=str(REPO / "src"),
        PYTHONIOENCODING="utf-8",
        TAVOTTO_DATA_DIR=str(tmp_path / "data"),
        TAVOTTO_CONFIG_DIR=str(tmp_path / "config"),
        TAVOTTO_NO_UPDATE_CHECK="1",
        TAVOTTO_NO_TELEMETRY="1",
    )
    port = _free_port()
    out_path = tmp_path / "stdout.log"
    with out_path.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tavotto", "--port", str(port)],
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            ready = False
            while time.monotonic() < deadline and not ready:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1):
                        ready = True
                except (urllib.error.URLError, OSError, TimeoutError):
                    time.sleep(0.2)
            time.sleep(1.5)  # 自动开浏览器的定时器是 0.8 s：给它足够的时间「犯错」
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    text = out_path.read_text(encoding="utf-8")
    assert ready, f"服务没有就绪；输出:\n{text}"
    assert not marker.exists(), f"SSH 会话里仍调了浏览器: {marker.read_text()}"
    assert "Running on" not in text and "development server" not in text, text
    m = re.search(rf"^    (http://127\.0\.0\.1:{port}/#dnonce=[A-Za-z0-9_-]+)$", text, re.M)
    assert m, f"没有单独一行的登录地址；输出:\n{text}"
    assert f"ssh -N -L {port}:127.0.0.1:{port}" in text
    assert text.index(f"ssh -N -L {port}") < m.start()
