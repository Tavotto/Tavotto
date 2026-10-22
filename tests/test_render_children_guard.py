"""#1285 的防线本身：用例留下的 render child 要被点名，进程级共享实例由 fixture 代关。"""

import sys

import pytest

from support import render_children
from tavotto.rendercore import renderhost

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)  # guard-marker"]


def test_an_unclosed_render_host_is_reported_with_its_full_command_line():
    render_children.install()
    since = render_children.mark()
    host = renderhost.RenderHost(command=SLEEPER)
    host._ensure()
    pid = host._proc.pid
    try:
        with pytest.raises(AssertionError) as err:
            render_children.reap(since)
    finally:
        host.close()
    text = str(err.value)
    assert f"pid={pid} " in text and "guard-marker" in text, text
    assert host._proc is None or host._proc.poll() is not None


def test_a_closed_render_host_is_not_reported():
    render_children.install()
    since = render_children.mark()
    host = renderhost.RenderHost(command=SLEEPER)
    host._ensure()
    host.close()
    render_children.reap(since)


def test_the_process_wide_shared_host_is_closed_for_the_test_not_reported(monkeypatch):
    render_children.install()
    renderhost.shutdown_shared()
    monkeypatch.setattr(
        renderhost.RenderHost, "__init__", _init_with_sleeper(renderhost.RenderHost)
    )
    since = render_children.mark()
    host = renderhost.shared()
    host._ensure()
    proc = host._proc
    assert proc is not None and proc.poll() is None
    render_children.reap(since)
    assert proc.poll() is not None
    renderhost.shutdown_shared()


def _init_with_sleeper(cls):
    real = cls.__init__

    def init(self, *a, **k):
        k.setdefault("command", SLEEPER)
        real(self, *a, **k)

    return init
