"""`tavotto_health` 对装法的结论：pip / pipx / uvx 的 wheel 没有插件清单与画布，属预期，不是故障；
Codex 插件 / 源码树装法的结论不变。`mcp_launch()` 在 wheel 里也要给出启动命令。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import hostconnect as H  # noqa: E402
from tavotto_mcp import server, widget  # noqa: E402


@pytest.fixture
def as_wheel(tmp_path, monkeypatch):
    """模拟 wheel：包目录里没有 `.codex-plugin`，也没有画布产物。"""
    monkeypatch.delenv(widget.WIDGET_ENV, raising=False)
    monkeypatch.setattr(widget, "_PLUGIN_DIR", tmp_path / "site-packages")
    monkeypatch.setattr(widget, "_DEFAULT", tmp_path / "site-packages" / "widget" / "canvas.html")


def test_install_form_detects_wheel_plugin_and_desktop(as_wheel, monkeypatch):
    assert widget.install_form() == "wheel"
    monkeypatch.setattr(widget, "_PLUGIN_DIR", ROOT / "codex-plugin")
    assert widget.install_form() == "plugin"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert widget.install_form() == "desktop"


def test_wheel_health_reports_canvas_and_package_as_expected(as_wheel):
    out = server._call_health({})["structuredContent"]
    assert out["server"]["install"] == "wheel"
    assert out["canvas"]["available"] is False and out["canvas"]["expected_absent"] is True
    assert "重装" not in out["canvas"]["reason"] and "桌面版" in out["canvas"]["reason"]
    assert out["checks"]["package"] == {"ok": True, "applicable": False, "install": "wheel"}
    assert out["checks"]["canvas_resource"] == {"ok": True, "applicable": False}
    text = server._call_health({})["content"][0]["text"]
    assert "! 画布" not in text and "重装" not in text


def test_plugin_install_with_missing_canvas_is_still_a_fault(tmp_path, monkeypatch):
    monkeypatch.delenv(widget.WIDGET_ENV, raising=False)
    monkeypatch.setattr(widget, "_PLUGIN_DIR", ROOT / "codex-plugin")
    monkeypatch.setattr(widget, "_DEFAULT", tmp_path / "nope" / "canvas.html")
    res = server._call_health({})
    out = res["structuredContent"]
    assert out["server"]["install"] == "plugin"
    assert "expected_absent" not in out["canvas"]
    assert "重装插件" in out["canvas"]["reason"]
    assert out["checks"]["canvas_resource"] == {"ok": False}
    assert out["checks"]["package"]["ok"] is True and "applicable" not in out["checks"]["package"]
    assert "! 画布资源缺失" in res["content"][0]["text"]


def test_wheel_with_explicit_widget_override_missing_is_still_a_fault(
    as_wheel, tmp_path, monkeypatch
):
    monkeypatch.setenv(widget.WIDGET_ENV, str(tmp_path / "x.html"))
    out = server._call_health({})["structuredContent"]
    assert "expected_absent" not in out["canvas"]
    assert out["checks"]["canvas_resource"] == {"ok": False}


def test_mcp_launch_in_wheel_without_source_tree(monkeypatch):
    from tavotto.engine import mcpserve

    monkeypatch.setattr(mcpserve, "_source_tree_mcp_dir", lambda: None)
    monkeypatch.delattr(sys, "frozen", raising=False)
    launch = H.mcp_launch()  # tavotto_mcp 在 sys.path 上（本测试文件加了）
    assert launch == {"command": sys.executable, "args": ["-m", "tavotto", "mcp"]}
    monkeypatch.setattr(H.importlib.util, "find_spec", lambda name: None)
    assert H.mcp_launch() is None  # 真没有服务端才是 None
