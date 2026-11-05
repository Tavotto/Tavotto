"""MCP 路径上的「有新版本」提醒（`tavotto_mcp/updatenotice.py`）。

四条底线（不阻塞、不污染 stdout、吞异常、不执行）沿用 `update_check.py`；这里验的是
MCP 这一侧新增的接线：`tavotto_health` 带字段、`tavotto_export` 每进程一次、调用线程从不等网络、
桌面包（frozen）布局下能定位模块。
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

from tavotto_mcp import server, updatenotice  # noqa: E402

URL = "http://127.0.0.1:9/codex-plugin.json"  # 不会被连接：缓存新鲜时根本不请求


def _manifest(latest="99.0.0"):
    return {
        "schema": 1,
        "latest_version": latest,
        "release_notes_url": "https://example.invalid/notes",
    }


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """隔离配置目录 + 清空模块级状态。返回写缓存的函数。"""
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TAVOTTO_UPDATE_URL", URL)
    monkeypatch.delenv("TAVOTTO_DISABLE_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("TAVOTTO_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updatenotice, "_latest", None)
    monkeypatch.setattr(updatenotice, "_started_at", None)
    monkeypatch.setattr(updatenotice, "_thread", None)
    monkeypatch.setattr(updatenotice, "_export_delivered", False)
    monkeypatch.setattr(server, "_version", lambda: "1.0.0")
    # 默认按桌面版口径（比引擎版本）；插件口径的用例自己改 how / current_version
    monkeypatch.setattr(updatenotice, "how", lambda: "desktop")

    def seed(manifest=None, age=0.0, url=URL):
        folder = tmp_path / "cfg"
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time() - age
        (folder / "codex-plugin-update.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "url": url,
                    "checked_at": now,
                    "attempted_at": now,
                    "manifest": manifest or _manifest(),
                }
            ),
            encoding="utf-8",
        )

    return seed


@pytest.fixture
def fake_export(monkeypatch):
    out = {
        "files": [{"status": "done", "path": "/tmp/a.pdf", "format": "pdf"}],
        "forced": False,
        "warnings": [],
    }
    monkeypatch.setattr(
        server.bridge, "export", lambda *a, **k: dict(out, files=list(out["files"]))
    )


def _health():
    return server.call_tool("tavotto_health", {})["structuredContent"]


def _export():
    return server.call_tool("tavotto_export", {"session_id": "s"})


# ------------------------------ health ----------------------------------
def test_health_carries_update_when_cache_has_newer(cfg, monkeypatch):
    cfg()
    monkeypatch.setattr(updatenotice, "how", lambda: "desktop")
    res = server.call_tool("tavotto_health", {})
    up = res["structuredContent"]["update"]
    assert up["status"] == "available"
    assert up["current_version"] == "1.0.0" and up["latest_version"] == "99.0.0"
    assert up["how"] == "desktop"
    assert "桌面版" in up["message"] and "新开" in up["message"]
    assert "upgrade_command" not in up
    assert up["message"] in res["content"][0]["text"]


def test_no_update_field_when_current(cfg):
    cfg(_manifest("1.0.0"))
    assert "update" not in _health()


def test_no_update_field_when_engine_version_unknown(cfg, monkeypatch):
    """`_version()` 读不到会回 "0"——绝不能把它当成「落后了」去吓用户。"""
    cfg()
    monkeypatch.setattr(server, "_version", lambda: "0")
    assert "update" not in _health()


@pytest.mark.parametrize("kind", ["desktop", "codex_plugin", None])
def test_how_variants(cfg, monkeypatch, kind):
    cfg()
    monkeypatch.setattr(updatenotice, "how", lambda: kind)
    up = _health()["update"]
    assert up["how"] == kind
    if kind == "codex_plugin":
        assert up["upgrade_command"] == "codex plugin marketplace upgrade tavotto"
        assert "codex plugin marketplace upgrade tavotto" in up["message"]
    else:
        assert "upgrade_command" not in up
        assert "codex plugin" not in up["message"]


def test_how_detection_frozen_and_source(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert updatenotice.how() == "desktop"
    monkeypatch.delattr(sys, "frozen", raising=False)
    # 源码树里插件根有 .codex-plugin/plugin.json
    assert updatenotice.how() == "codex_plugin"
    monkeypatch.setattr(updatenotice, "plugin_dir", lambda: "/nonexistent")
    assert updatenotice.how() is None


def test_disable_switch(cfg, monkeypatch):
    cfg()
    monkeypatch.setenv("TAVOTTO_DISABLE_UPDATE_CHECK", "1")
    assert "update" not in _health()


@pytest.mark.parametrize("switch", ["env", "auto_check"])
def test_user_switches_stop_the_background_request(cfg, monkeypatch, switch):
    """更新检查的规则是「关了一个包都不发」：过期缓存也不许起后台线程，缓存里已有的提醒照常。"""
    cfg(age=10 * 24 * 3600)
    if switch == "env":
        monkeypatch.setenv("TAVOTTO_NO_UPDATE_CHECK", "1")
    else:
        from tavotto.engine import updater

        monkeypatch.setattr(updater, "settings", lambda: {"auto_check": False})
    assert updatenotice.network_allowed() is False
    assert _health()["update"]["status"] == "available"
    assert updatenotice._thread is None


def test_the_background_request_starts_when_nothing_is_switched_off(cfg, monkeypatch):
    """上一条的对照：开关都开着时这条路真的会起线程，否则上一条恒真。"""
    cfg(age=10 * 24 * 3600)
    monkeypatch.setattr(updatenotice, "_refresh", lambda mod, mine: None)
    _health()
    assert updatenotice._thread is not None


# ------------------------------ export ----------------------------------
def test_export_carries_update_only_once_per_process(cfg, fake_export):
    cfg()
    first = _export()
    assert first["structuredContent"]["update"]["status"] == "available"
    assert first["structuredContent"]["update"]["message"] in first["content"][0]["text"]
    second = _export()
    assert "update" not in second["structuredContent"]
    assert "新版本" not in second["content"][0]["text"]
    # health 不受「export 已提醒过」影响
    assert "update" in _health()


def test_export_without_newer_adds_nothing(cfg, fake_export):
    cfg(_manifest("1.0.0"))
    res = _export()
    assert "update" not in res["structuredContent"]


def test_failed_export_does_not_spend_the_one_shot(cfg, monkeypatch, fake_export):
    cfg()
    monkeypatch.setattr(
        server.bridge,
        "export",
        lambda *a, **k: {
            "files": [{"status": "failed", "format": "pdf", "error": {"code": "x"}}],
            "forced": False,
            "warnings": [],
        },
    )
    assert "update" not in _export()["structuredContent"]
    assert updatenotice._export_delivered is False


# ---------------------------- 不阻塞 / 不污染 -----------------------------
class _Hang(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        time.sleep(4)

    def log_message(self, *a):  # noqa: D401
        pass


def test_hung_network_never_slows_tool_calls(cfg, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hang)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/m.json"
        monkeypatch.setenv("TAVOTTO_UPDATE_URL", url)
        # 缓存过期（25 小时前）：必须触发后台刷新，而且调用线程不等它
        cfg(url=url, age=25 * 3600)
        t0 = time.monotonic()
        out = _health()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, elapsed  # 网络挂死 4 秒；调用线程只读缓存
        assert out["update"]["latest_version"] == "99.0.0"  # 过期缓存照样能提醒
        assert updatenotice._thread is not None  # 后台线程确实起了
        assert updatenotice._thread.daemon is True
        t1 = time.monotonic()
        _export if False else _health()
        assert time.monotonic() - t1 < 0.5
    finally:
        srv.shutdown()


def test_stale_cache_read_does_not_burn_the_retry_window(cfg):
    """调用线程的只读不许写缓存（否则 attempted_at 被刷新，后台那次真请求会被判成「刚试过」）。"""
    cfg(age=25 * 3600)
    path = Path(os.environ["TAVOTTO_CONFIG_DIR"]) / "codex-plugin-update.json"
    before = path.read_text(encoding="utf-8")
    mod = updatenotice._load()
    mod.check(version="1.0.0", allow_network=False)
    assert path.read_text(encoding="utf-8") == before


def test_exceptions_are_swallowed(cfg, monkeypatch):
    cfg()
    monkeypatch.setattr(updatenotice, "_current", lambda mine: 1 / 0)
    assert updatenotice.notice("1.0.0") is None
    assert updatenotice.notice_once("1.0.0") is None
    assert "update" not in _health()


def test_stdout_gets_no_extra_bytes(cfg, fake_export, capfd):
    cfg()
    _health()
    _export()
    time.sleep(0.1)
    cap = capfd.readouterr()
    assert cap.out == ""


def test_no_stdout_bytes_in_a_real_process(tmp_path):
    """真进程：只读缓存路径下，加载 + 调用 notice 不往 stdout 写任何字节。"""
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    (cfgdir / "codex-plugin-update.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "url": URL,
                "checked_at": time.time(),
                "attempted_at": time.time(),
                "manifest": _manifest(),
            }
        ),
        encoding="utf-8",
    )
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from tavotto_mcp import updatenotice\n"
        "n = updatenotice.notice('1.0.0')\n"
        "sys.stderr.write('OK' if n and n['status']=='available' else 'NO')\n"
    ) % str(PLUGIN / "mcp")
    env = dict(
        os.environ,
        TAVOTTO_CONFIG_DIR=str(cfgdir),
        TAVOTTO_UPDATE_URL=URL,
        PYTHONPATH=str(ROOT / "src"),
    )
    env.pop("TAVOTTO_DISABLE_UPDATE_CHECK", None)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b""
    assert r.stderr.endswith(b"OK"), r.stderr


# --------------------------- 模块定位（frozen） ----------------------------
def test_frozen_layout_locates_module(tmp_path, monkeypatch):
    """桌面包布局：`<_MEIPASS>/skills/tavotto-figure/scripts/update_check.py`，插件根的那份不存在也行。"""
    meipass = tmp_path / "meipass"
    dst = meipass / "skills" / "tavotto-figure"
    import shutil

    shutil.copytree(
        PLUGIN / "skills" / "tavotto-figure", dst, ignore=shutil.ignore_patterns("__pycache__")
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(updatenotice, "plugin_dir", lambda: str(tmp_path / "nowhere"))
    monkeypatch.setattr(updatenotice, "_module", None)
    assert updatenotice.scripts_dirs()[0] == str(dst / "scripts")
    mod = updatenotice._load()
    assert mod is not None and mod.__file__ == str(dst / "scripts" / "update_check.py")
    assert updatenotice.how() == "desktop"


def test_missing_module_is_silent(cfg, monkeypatch):
    cfg()
    monkeypatch.setattr(updatenotice, "scripts_dirs", lambda: ["/nonexistent"])
    monkeypatch.setattr(updatenotice, "_module", None)
    assert "update" not in _health()
    monkeypatch.setattr(updatenotice, "_module", None)  # 清掉「找过了没有」的记忆，别污染后面


def test_spec_bundles_the_skill_directory():
    spec = (ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert 'SKILL_SRC = ROOT / "codex-plugin" / "skills" / "tavotto-figure"' in spec
    assert 'Path("skills") / "tavotto-figure"' in spec


# ------------------- 插件版本 ≠ 引擎版本（codex_plugin） -------------------
def _plugin_case(monkeypatch, plugin_version):
    monkeypatch.setattr(updatenotice, "how", lambda: "codex_plugin")
    mod = updatenotice._load()
    monkeypatch.setattr(mod, "current_version", lambda path=None: plugin_version)


def test_old_plugin_with_new_engine_still_reminds(cfg, monkeypatch):
    cfg(_manifest("2.0.0"))
    monkeypatch.setattr(server, "_version", lambda: "2.0.0")  # 引擎已是新的
    _plugin_case(monkeypatch, "1.0.0")  # 插件是旧的
    up = _health()["update"]
    assert up["current_version"] == "1.0.0" and "当前 1.0.0" in up["message"]


def test_new_plugin_with_old_engine_does_not_false_alarm(cfg, monkeypatch):
    cfg(_manifest("2.0.0"))
    monkeypatch.setattr(server, "_version", lambda: "1.0.0")  # 引擎旧
    _plugin_case(monkeypatch, "2.0.0")  # 插件已是最新
    assert "update" not in _health()


def test_unreadable_plugin_version_falls_back_to_engine(cfg, monkeypatch):
    cfg(_manifest("2.0.0"))
    _plugin_case(monkeypatch, None)
    assert _health()["update"]["current_version"] == "1.0.0"


def test_desktop_compares_engine_version_not_plugin_json(cfg, monkeypatch):
    cfg(_manifest("2.0.0"))
    mod = updatenotice._load()
    monkeypatch.setattr(mod, "current_version", lambda path=None: "2.0.0")
    assert _health()["update"]["current_version"] == "1.0.0"  # how=desktop（fixture）


# --------------------------- 刷新时机交给 _due -----------------------------
class _Spy:
    """记录后台线程被起了几次（不真联网）。"""

    def __init__(self, monkeypatch):
        self.n = 0
        monkeypatch.setattr(updatenotice, "_refresh", self._run)

    def _run(self, mod, ver):
        self.n += 1

    def settle(self):
        t = updatenotice._thread
        if t is not None:
            t.join(2)


def test_fresh_cache_starts_no_thread(cfg, monkeypatch):
    cfg()
    spy = _Spy(monkeypatch)
    _health()
    spy.settle()
    assert spy.n == 0 and updatenotice._thread is None


def test_failed_first_attempt_is_retried_after_an_hour(cfg, monkeypatch):
    """缓存里只有一次失败的尝试（没有 checked_at / manifest）：1 小时内不再起线程，过了就再试。"""
    spy = _Spy(monkeypatch)
    folder = Path(os.environ["TAVOTTO_CONFIG_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / "codex-plugin-update.json"

    def write(attempted_ago):
        cache.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "url": URL,
                    "checked_at": None,
                    "attempted_at": time.time() - attempted_ago,
                    "manifest": None,
                }
            ),
            encoding="utf-8",
        )

    write(60)  # 刚失败
    _health()
    spy.settle()
    assert spy.n == 0
    write(3700)  # 一小时多以前
    _health()
    spy.settle()
    assert spy.n == 1


def test_unwritable_cache_does_not_spawn_a_thread_per_call(cfg, monkeypatch):
    """缓存写不了 → `_due` 永远为真；进程内的最小间隔要拦住「每次调用起一个线程」。"""
    spy = _Spy(monkeypatch)  # 既不联网也不写缓存，等价于写不了；缓存目录里始终没有文件
    for _ in range(5):
        _health()
        spy.settle()
    assert spy.n == 1


def test_cache_wins_over_in_memory_result(cfg, monkeypatch):
    cfg(_manifest("1.0.0"))  # 缓存说已是最新
    monkeypatch.setattr(
        updatenotice,
        "_latest",
        {"status": "available", "latest_version": "50.0.0", "upgrade_command": "x"},
    )
    assert "update" not in _health()
