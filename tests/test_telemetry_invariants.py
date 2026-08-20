"""几条**比「多送出去一条事件」重要得多**的不变式。

这些用例存在的理由不是覆盖率，而是：埋点是可选的、失败无所谓的功能，
一旦它能影响导出、AI、启动，或者能把用户的内容带出去，那就不是「少了个
指标」，而是产品坏了 / 隐私承诺破了。
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import telemetry

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 没同意 = 零网络
# ---------------------------------------------------------------------------
def test_no_consent_means_zero_network(monkeypatch):
    """同意之前，`urlopen` 一次都不该被调到。"""
    import urllib.request

    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("没同意却发起了网络请求")))
    assert telemetry.settings()["consent"] == "unset"
    for event, props in [
        ("app_started", {"app_mode": "browser"}),
        ("export_completed", {"pdf": True}),
        ("figure_opened", {"asset_kind": "pdf", "editable": True}),
    ]:
        assert telemetry.capture(event, props) is False
    telemetry.note_app_started("browser")
    assert telemetry.flush(5.0)
    assert calls == []
    telemetry.reset_for_tests()


def test_import_time_makes_no_network_request(monkeypatch):
    """import 这个模块本身不许联网，也不许起线程。"""
    import importlib
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("import 期间发起了网络请求")))
    importlib.reload(telemetry)


# ---------------------------------------------------------------------------
# 埋点坏掉 ≠ 产品坏掉
# ---------------------------------------------------------------------------
def test_app_still_starts_when_telemetry_explodes(monkeypatch, telemetry_sent):
    """把埋点整条路径炸掉，`note_app_started` 也必须安静地过去。

    它跑在 `app.main()` 里、`app.run()` 之前——抛出来就是「启动即崩」。
    """
    def boom(*_a, **_kw):
        raise RuntimeError("埋点炸了")

    monkeypatch.setattr(telemetry, "validate", boom)
    monkeypatch.setattr(telemetry, "_enqueue", boom)
    telemetry.note_app_started("desktop")       # 不抛就算过


def test_capture_never_raises_whatever_goes_wrong(monkeypatch, telemetry_sent):
    for target in ("validate", "_auto_props", "_enqueue", "install_id"):
        monkeypatch.setattr(telemetry, target,
                            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
        assert telemetry.capture("export_completed", {"pdf": True}) is False
        monkeypatch.undo()


def test_set_consent_survives_a_broken_transport(monkeypatch):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    monkeypatch.setattr(telemetry, "_post",
                        lambda _p: (_ for _ in ()).throw(OSError("代理挂了")))
    telemetry.set_consent(telemetry.CONSENT_ENABLED, source="first_run")
    assert telemetry.enabled() is True
    assert telemetry.flush(5.0)
    telemetry.reset_for_tests()


# ---------------------------------------------------------------------------
# 内容在结构上就发不出去
# ---------------------------------------------------------------------------
CONTENT_BEARING = [
    "stem", "filename", "file", "path", "dir", "export_dir", "script",
    "source", "prompt", "response", "diff", "label", "gid", "text", "title",
    "project", "canvas", "name", "traceback", "error", "module", "package",
    "user", "email", "host", "hostname", "locale", "ip", "session", "url",
]


@pytest.mark.parametrize("prop", CONTENT_BEARING)
def test_no_event_accepts_a_content_bearing_property(prop):
    """白名单里根本没有这些属性名——发不出去不是因为调用方记得住。

    **闭枚举例外**：`telemetry_enabled.source` 只能是 `first_run` / `settings`，
    值域是我们定死的两个词，装不下任何用户内容。真正危险的是能承载自由文本的
    类型（version 是唯一那种，而它的字符集与长度都卡死了）。
    """
    for event, allowed in telemetry.EVENTS.items():
        spec = allowed.get(prop)
        if spec is None:
            continue
        assert spec["kind"] == "enum", f"{event}.{prop} 能承载自由文本"


def test_no_property_accepts_a_container():
    """没有任何一条属性接受 dict / list：嵌套结构是内容偷渡的经典载体。"""
    for event, allowed in telemetry.EVENTS.items():
        for prop, spec in allowed.items():
            assert spec["kind"] in ("bool", "int", "enum", "version"), (event, prop)


def test_preflight_only_carries_counts():
    """预检事件只有四个计数 + 一个布尔，没有任何一条检查项的文字。"""
    assert set(telemetry.EVENTS["preflight_completed"]) == {
        "errors", "warnings", "not_verifiable", "suggestions", "passed"}
    for prop in ("detail", "message", "id", "font", "object_ids", "gids", "text"):
        with pytest.raises(Exception):
            telemetry.validate("preflight_completed", {prop: "x"})


def test_ai_event_only_carries_the_agent():
    assert set(telemetry.EVENTS["ai_assistant_invoked"]) == {"agent"}


def test_export_event_only_carries_shape():
    assert set(telemetry.EVENTS["export_completed"]) == {
        "pdf", "png", "with_proof", "panel_count"}


def test_enum_values_are_short_and_closed():
    """枚举值都短、都固定——没有任何一个是「调用方给什么算什么」。"""
    for event, allowed in telemetry.EVENTS.items():
        for prop, spec in allowed.items():
            if spec["kind"] != "enum":
                continue
            assert spec["values"], (event, prop)
            for value in spec["values"]:
                assert isinstance(value, str) and 0 < len(value) <= 24
                assert re.fullmatch(r"[a-z0-9_]+", value), (event, prop, value)


# ---------------------------------------------------------------------------
# 服务端埋点挂在成功边界上
# ---------------------------------------------------------------------------
def test_export_is_captured_after_the_files_are_written():
    """源码级看护：`export_completed` 必须出现在 `canvas.save_*` 之后、
    `return jsonify` 之前。挪到函数开头的话失败的导出也会被记成成功。"""
    src = inspect.getsource(m.api_export)
    at_capture = src.index('engine_telemetry.capture("export_completed"')
    assert src.index("canvas.save_pdf") < at_capture
    assert src.index("canvas.save_png") < at_capture
    assert at_capture < src.rindex("return jsonify")


def test_ai_is_captured_after_the_session_exists():
    src = inspect.getsource(m.api_ai_run)
    at_capture = src.index('engine_telemetry.capture("ai_assistant_invoked"')
    assert src.index("sid = engine_ai.run(") < at_capture
    # 失败分支（except → 500）必须在埋点之前就 return 掉
    assert src.index('"code": "ai_start_failed"') < at_capture


def test_app_started_is_only_called_from_main():
    """会话事件必须挂在 `main()` 的启动分支上，不能在模块层。

    模块层的话 `tavotto --help` / `doctor` / 打包脚本 / 单测 import 全都会被
    算成一次产品会话，「有多少人真的在用」从第一天起就是假的。
    """
    src = (ROOT / "src" / "tavotto" / "app.py").read_text(encoding="utf-8")
    at_main = src.index("\ndef main():")
    for match in re.finditer(r"(?m)^(\s*)engine_telemetry\.note_app_started\(", src):
        assert match.group(1), "note_app_started 出现在模块层"
        assert match.start() > at_main, "note_app_started 不在 main() 里"
    # 两条真实启动路径各一次：桌面 sidecar 与浏览器
    assert src.count("engine_telemetry.note_app_started(") == 2


# ---------------------------------------------------------------------------
# CI 绝不产生真实事件
# ---------------------------------------------------------------------------
def test_pytest_runs_with_telemetry_hard_disabled():
    """conftest 把硬开关钉死。这条用例故意**不**摘掉它。"""
    import os
    assert os.environ.get("TAVOTTO_NO_TELEMETRY") == "1"


@pytest.mark.parametrize("workflow", ["ci.yml", "nightly.yml",
                                      "desktop-tauri.yml", "release.yml"])
def test_every_workflow_hard_disables_telemetry(workflow):
    """每条会真的把 Tavotto 跑起来的流水线都要在**工作流级**关掉遥测。

    只靠「CI 上配置目录是新的、同意态是 unset」不行：那是默认值，而默认值
    会变、会被某个步骤改掉、会被缓存的用户目录带进来。
    """
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert re.search(r"(?m)^env:\n(?:.*\n)*?  TAVOTTO_NO_TELEMETRY: \"1\"", text), \
        f"{workflow} 没有在工作流级钉死 TAVOTTO_NO_TELEMETRY"


@pytest.mark.parametrize("script", ["smoke_app.py", "smoke_desktop.py",
                                    "bench_render.py"])
def test_smoke_and_bench_scripts_hard_disable_telemetry(script):
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert '"TAVOTTO_NO_TELEMETRY": "1"' in text, \
        f"{script} 起的进程可能会往生产分析后端发事件"


def test_metrics_workflow_keeps_the_token_out_of_yaml():
    text = (ROOT / ".github" / "workflows" / "telemetry-metrics.yml").read_text(
        encoding="utf-8")
    assert "secrets.TAVOTTO_METRICS_TOKEN" in text
    # 只读权限 + 只调采集器
    assert "contents: read" in text
    assert "collect_distribution_metrics.py" in text
    # 非整点：整点是 Actions 的高峰
    cron = re.search(r'cron:\s*"(\S+) (\S+) ', text)
    assert cron and cron.group(1) != "0", "定时应避开整点"


# ---------------------------------------------------------------------------
# 依赖边界
# ---------------------------------------------------------------------------
def test_telemetry_module_is_pure_stdlib():
    """Flask 父进程 import 的模块必须纯标准库（同 registry / pool / updater）。"""
    src = (ROOT / "src" / "tavotto" / "engine" / "telemetry.py").read_text(
        encoding="utf-8")
    third_party = {"posthog", "requests", "httpx", "urllib3", "flask", "pymupdf",
                   "numpy", "matplotlib", "analytics", "segment", "mixpanel"}
    for line in src.splitlines():
        mod = re.match(r"^(?:import|from)\s+([\w.]+)", line.strip())
        if mod and mod.group(1).split(".")[0] in third_party:
            pytest.fail(f"遥测模块 import 了第三方包: {line.strip()}")


def test_worker_knows_nothing_about_telemetry():
    """渲染 worker 与它周边的模块一行埋点都不该有。"""
    engine = ROOT / "src" / "tavotto" / "engine"
    for name in ("worker.py", "manifest.py", "overrides.py", "pathgeom.py",
                 "patchspec.py"):
        text = (engine / name).read_text(encoding="utf-8")
        assert "telemetry" not in text, f"{name} 里出现了遥测"


def test_proxy_is_not_shipped_in_the_package():
    """代理是独立部署的服务，不该被打进 wheel/sdist。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"services"' in text and '"services/**"' in text
    deps = re.search(r"(?s)dependencies = \[(.*?)\]", text).group(1)
    for banned in ("posthog", "requests", "httpx"):
        assert banned not in deps, f"{banned} 混进了运行时依赖"


def test_client_never_learns_the_analytics_backend():
    """客户端只知道 Tavotto 自己的代理地址，不知道提供商是谁。"""
    assert telemetry.DEFAULT_ENDPOINT.startswith("https://telemetry.tavotto.com/")
    assert "posthog" not in telemetry.DEFAULT_ENDPOINT.lower()


def test_public_settings_never_exposes_the_install_id(telemetry_sent):
    pub = telemetry.public_settings()
    assert "install_id" not in pub
    ident = telemetry.install_id()
    assert ident and ident not in json.dumps(pub)
