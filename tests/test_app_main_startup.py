"""`app.main()` 的三条启动路径各自做了哪些启动副作用（#641）。

- **端口复用**（端口上已经有一个 Tavotto）：只换一枚 nonce、把人指过去。这个进程不提供服务，
  PDF 后端不装载、不预热（不起 render child、不起在后台 import 原生扩展的线程）、不开项目、不把 AI
  会话标成中断、不起清缓存线程。py3.10 CI 上它在打印完退出时 SIGSEGV（-11）：daemon 预热线程正在
  import pikepdf，解释器已经开始收尾。
- **浏览器模式起服务** / **桌面 sidecar**：起服务之前 `pdfbackend.warm()` 一次（交给实现的预热）；
  选了装不上 / 退役的后端在起服务之前就抛，不静默回退（ADR 0067 / 0072）。

进程内直接跑 `main()`：`serve_browser` / `desktop.run` / 预热换成记录器，确定性、不起真服务。
"""

from __future__ import annotations

import sys

import pytest

from tavotto import app as appmod, pdfbackend
from tavotto.rendercore import facade, renderhost


@pytest.fixture
def startup(monkeypatch):
    """`main()` 周边的环境副作用换成记录器；回的 `calls` 按发生顺序记下启动副作用。"""
    calls: list[str] = []
    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)
    monkeypatch.delenv("TAVOTTO_INSECURE_NO_AUTH", raising=False)
    monkeypatch.setattr(appmod.engine_cli, "use_utf8_streams", lambda: None)
    monkeypatch.setattr(appmod, "setup_logging", lambda: None)
    monkeypatch.setattr(appmod.engine_locate, "refresh_manifest", lambda: None)
    monkeypatch.setattr(appmod.engine_config, "last_project", lambda: "/nonexistent/figs")
    monkeypatch.setattr(appmod, "open_project", lambda *a, **k: _raise(calls, "open_project"))
    monkeypatch.setattr(appmod, "prune_render_cache", lambda: calls.append("prune"))
    monkeypatch.setattr(appmod.engine_pool, "prune_engine_cache", lambda: calls.append("prune"))
    monkeypatch.setattr(appmod.engine_runtimeasset, "prune_cache", lambda: calls.append("prune"))
    monkeypatch.setattr(
        appmod.engine_ai_history,
        "mark_interrupted_running",
        lambda: calls.append("mark_interrupted") or 0,
    )
    monkeypatch.setattr(appmod.engine_ai_history, "purge", lambda **k: 0)
    monkeypatch.setattr(appmod.engine_updater, "check_in_background", lambda: None)
    monkeypatch.setattr(appmod.engine_telemetry, "note_app_started", lambda *a: None)
    monkeypatch.setattr(facade, "prewarm", lambda: calls.append("prewarm"))
    monkeypatch.setattr(renderhost.RenderHost, "_start", lambda self: calls.append("render_child"))
    monkeypatch.setattr(
        appmod.localserver, "serve_browser", lambda *a, **k: calls.append("serve_browser")
    )
    monkeypatch.setattr(appmod.desktop_mode, "run", lambda app: calls.append("desktop_run") or 0)
    return calls


def _raise(calls: list[str], what: str):
    """记一笔，再像「项目打不开」那样抛——`main()` 照常往下走（接 Project Picker）。"""
    calls.append(what)
    raise RuntimeError("stub")


def run_main(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["tavotto", *argv])
    appmod.main()


def test_second_launch_hands_off_without_warming_the_backend_or_touching_state(
    startup, monkeypatch, capsys
):
    """端口上已经有 Tavotto：打印带新 nonce 的地址就走。预热线程与 render child 都不起（#641 的崩溃就出在
    这个进程退出时预热线程还在 import 原生扩展）；AI 会话不标中断（那是**在跑那个实例**正进行中的会话）；
    项目不开、缓存不清。选了什么后端也与它无关——服务的是在跑的那个实例，它起服务时已经验过。"""
    monkeypatch.setattr(appmod, "resolve_port", lambda preferred: None)
    monkeypatch.setattr(appmod.engine_session_client, "relaunch_nonce", lambda port: "tok123")
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "pymupdf")
    run_main(monkeypatch, "--port", "5999", "--no-browser")
    out = capsys.readouterr().out
    assert "#dnonce=tok123" in out
    assert startup == [], f"端口复用路径不该有任何启动副作用: {startup}"


def test_serving_warms_the_backend_before_it_serves(startup, monkeypatch):
    monkeypatch.setattr(appmod, "resolve_port", lambda preferred: preferred)
    run_main(monkeypatch, "--port", "5999", "--no-browser", "--insecure-no-auth")
    assert "prewarm" in startup and "serve_browser" in startup
    assert startup.index("prewarm") < startup.index("serve_browser")


def test_desktop_sidecar_warms_the_backend_before_it_serves(startup, monkeypatch):
    """sidecar 不走端口复用（端口由桌面壳分配）：照旧起服务前预热。"""
    monkeypatch.setattr(
        appmod, "resolve_port", lambda preferred: pytest.fail("sidecar 不该探端口复用")
    )
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--desktop-sidecar")
    assert exc.value.code == 0
    assert startup.index("prewarm") < startup.index("desktop_run")


@pytest.mark.parametrize("argv", [("--no-browser", "--insecure-no-auth"), ("--desktop-sidecar",)])
def test_an_unusable_backend_fails_before_serving(startup, monkeypatch, argv):
    """要起服务的进程：选了退役的后端在起服务之前就抛，不退默认、不带病起服务（ADR 0067 / 0072）。"""
    monkeypatch.setattr(appmod, "resolve_port", lambda preferred: preferred)
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "pymupdf")
    with pytest.raises(pdfbackend.BackendSelectionError) as exc:
        run_main(monkeypatch, "--port", "5999", *argv)
    assert exc.value.code == "backend_retired"
    assert "serve_browser" not in startup and "desktop_run" not in startup
