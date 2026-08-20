"""匿名遥测：同意模型、白名单、传输隔离与「绝不影响产品行为」的那几条不变式。

**没有一个用例发出真实网络请求**：所有用例都把 `telemetry._post` 换成一个
收集器。conftest 把 `TAVOTTO_NO_TELEMETRY` 钉成 1，需要真的走投递路径的
用例自己在 fixture 里摘掉它。
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from tavotto.engine import config as engine_config
from tavotto.engine import diagnostics, telemetry


@pytest.fixture
def sent(monkeypatch):
    """允许遥测 + 拦下传输层。返回收集到的 payload 列表。"""
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("TAVOTTO_TELEMETRY_ENDPOINT", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    yield box
    telemetry.reset_for_tests()


def _flush():
    assert telemetry.flush(5.0), "发送队列没有在超时内排空"


def _enable(source: str = "settings") -> None:
    telemetry.set_consent(telemetry.CONSENT_ENABLED, source=source)
    _flush()


# ---------------------------------------------------------------------------
# 同意模型
# ---------------------------------------------------------------------------
def test_default_consent_is_unset_and_sends_nothing(sent):
    assert telemetry.settings()["consent"] == "unset"
    assert telemetry.enabled() is False
    assert telemetry.capture("export_completed", {"pdf": True}) is False
    _flush()
    assert sent == []


def test_unset_never_generates_an_install_id(sent):
    """同意之前连标识都不该存在——「反正生成了也没发」不是隐私承诺。"""
    assert telemetry.install_id() is None
    telemetry.capture("app_started", {"app_mode": "browser"})
    assert telemetry.install_id() is None


def test_disabled_sends_nothing(sent):
    telemetry.set_consent(telemetry.CONSENT_DISABLED)
    assert telemetry.enabled() is False
    assert telemetry.capture("export_completed", {"pdf": True}) is False
    _flush()
    assert sent == []


def test_enabled_queues_and_sends(sent):
    _enable()
    assert telemetry.capture("export_completed",
                             {"pdf": True, "png": False, "with_proof": False,
                              "panel_count": 3}) is True
    _flush()
    events = [p["event"] for p in sent]
    assert "telemetry_enabled" in events
    assert "export_completed" in events
    payload = next(p for p in sent if p["event"] == "export_completed")
    assert payload["properties"]["panel_count"] == 3
    assert payload["schema_version"] == telemetry.SCHEMA_VERSION


def test_hard_env_switch_beats_saved_consent(sent, monkeypatch):
    _enable()
    sent.clear()
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    assert telemetry.hard_disabled() is True
    assert telemetry.enabled() is False
    assert telemetry.capture("export_completed", {"pdf": True}) is False
    _flush()
    assert sent == []
    # 「管理员关掉了」要能在界面上说清楚，而不是显示成用户自己关的
    pub = telemetry.public_settings()
    assert pub["hard_disabled"] is True and pub["consent"] == "enabled"


@pytest.mark.parametrize("raw", ["", "0", "false", "no"])
def test_empty_or_zero_env_is_not_a_kill_switch(sent, monkeypatch, raw):
    """`TAVOTTO_NO_TELEMETRY=` 是「取消设置」的常见写法，不该被当成开着。"""
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", raw)
    assert telemetry.hard_disabled() is False


def test_disabling_takes_effect_immediately(sent):
    _enable()
    sent.clear()
    telemetry.set_consent(telemetry.CONSENT_DISABLED)
    assert telemetry.capture("canvas_created", {"creation_kind": "blank"}) is False
    _flush()
    assert sent == []


# ---------------------------------------------------------------------------
# 匿名标识
# ---------------------------------------------------------------------------
def test_install_id_is_a_random_uuid4_and_persists(sent):
    _enable()
    ident = telemetry.install_id()
    parsed = uuid.UUID(ident)
    assert parsed.version == 4
    # 重新读一遍配置（模拟重启）：同一个 id
    assert telemetry.settings()["install_id"] == ident
    raw = json.loads(engine_config.config_path().read_text(encoding="utf-8"))
    assert raw["telemetry"]["install_id"] == ident


def test_install_id_is_not_derived_from_machine_information(sent, monkeypatch):
    """同一台机器上「关掉→清空→重新同意」必须得到一个新的随机 id。

    从机器信息推导出来的标识会在这里复现原值——那正是设备指纹的定义。
    """
    _enable()
    first = telemetry.install_id()
    cfg = engine_config.load()
    cfg["telemetry"] = {}
    engine_config.save(cfg)
    telemetry.set_consent(telemetry.CONSENT_ENABLED)
    assert telemetry.install_id() != first


def test_reenabling_does_not_mint_a_second_new_user(sent):
    _enable()
    ident = telemetry.install_id()
    sent.clear()
    telemetry.set_consent(telemetry.CONSENT_DISABLED)
    telemetry.set_consent(telemetry.CONSENT_ENABLED)
    _flush()
    assert telemetry.install_id() == ident, "重新打开不该换标识"
    assert [p["event"] for p in sent if p["event"] == "telemetry_enabled"] == [], \
        "关掉再打开不是一个新用户，不该再发一条 telemetry_enabled"


def test_distinct_id_is_the_install_id(sent):
    _enable()
    assert all(p["distinct_id"] == telemetry.install_id() for p in sent)


# ---------------------------------------------------------------------------
# 同意书版本：**同意的是哪一版**
# ---------------------------------------------------------------------------
def test_stale_consent_stops_sending_until_the_user_is_asked_again(sent, monkeypatch):
    """采集范围实质性扩大（CONSENT_VERSION +1）之后，旧的同意**不再算数**。

    用户当初同意的是 v1 那一版的采集范围。把范围扩大了还接着按老同意发，
    等于替用户做了他没做过的决定——模块顶部的注释一直是这么承诺的，
    但代码里 `enabled()` 只比 consent 不比版本，那句承诺是空的。
    """
    _enable()
    sent.clear()

    monkeypatch.setattr(telemetry, "CONSENT_VERSION", telemetry.CONSENT_VERSION + 1)

    assert telemetry.enabled() is False
    assert telemetry.capture("export_completed", {"pdf": True}) is False
    _flush()
    assert sent == []


def test_stale_consent_is_surfaced_to_the_ui(sent, monkeypatch):
    """界面要分得清「从没问过」和「问过了但那是上一版」——两种都要再问一次，
    但后者不能把用户当成新用户。"""
    _enable()
    monkeypatch.setattr(telemetry, "CONSENT_VERSION", telemetry.CONSENT_VERSION + 1)
    pub = telemetry.public_settings()
    assert pub["needs_reconsent"] is True
    assert pub["enabled"] is False
    # consent 本身仍然是 enabled：用户确实同意过，只是同意的是上一版
    assert pub["consent"] == "enabled"


def test_reconsent_keeps_the_same_install_id(sent, monkeypatch):
    """重新征求同意**不换 UUID**。

    换一个等于在升级那天凭空造出一批「新安装」：留存曲线断掉，
    活跃数虚高一轮，而实际上一个新用户都没有。
    """
    _enable()
    ident = telemetry.install_id()
    monkeypatch.setattr(telemetry, "CONSENT_VERSION", telemetry.CONSENT_VERSION + 1)
    sent.clear()

    telemetry.set_consent(telemetry.CONSENT_ENABLED, source="first_run")
    _flush()

    assert telemetry.install_id() == ident
    assert telemetry.enabled() is True
    assert telemetry.settings()["consent_version"] == telemetry.CONSENT_VERSION
    # ever_enabled 早就是 true，不该再发一条「新用户」
    assert [p for p in sent if p["event"] == "telemetry_enabled"] == []


def test_hard_switch_still_wins_over_a_stale_consent(sent, monkeypatch):
    _enable()
    monkeypatch.setattr(telemetry, "CONSENT_VERSION", telemetry.CONSENT_VERSION + 1)
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    pub = telemetry.public_settings()
    assert pub["enabled"] is False
    # 硬开关关着时不该再去骚扰用户重新同意——那个框点了也没用
    assert pub["needs_reconsent"] is False


def test_a_fresh_consent_is_current(sent):
    _enable()
    pub = telemetry.public_settings()
    assert pub["needs_reconsent"] is False
    assert pub["saved_consent_version"] == telemetry.CONSENT_VERSION


def test_declining_is_not_stale_consent(sent, monkeypatch):
    """说过「不」的人，升版之后也不该被再问一次——那是骚扰，不是征求同意。"""
    telemetry.set_consent(telemetry.CONSENT_DISABLED)
    monkeypatch.setattr(telemetry, "CONSENT_VERSION", telemetry.CONSENT_VERSION + 1)
    assert telemetry.public_settings()["needs_reconsent"] is False


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------
def test_unknown_event_is_dropped(sent):
    _enable()
    sent.clear()
    assert telemetry.capture("figure_contents_uploaded", {"path": "/x"}) is False
    _flush()
    assert sent == []


def test_unknown_property_is_dropped(sent):
    _enable()
    sent.clear()
    assert telemetry.capture("export_completed",
                             {"pdf": True, "stem": "Fig1_kinetics"}) is False
    _flush()
    assert sent == []


@pytest.mark.parametrize("props", [
    {"panel_count": "3"},                       # 类型不符
    {"panel_count": -1},                        # 越界
    {"panel_count": 10 ** 9},                   # 越界
    {"panel_count": True},                      # bool 不是 int
    {"pdf": "yes"},                             # 字符串不是 bool
])
def test_bad_property_values_are_rejected(props):
    with pytest.raises(Exception):
        telemetry.validate("export_completed", props)


def test_enum_properties_reject_free_text():
    with pytest.raises(Exception):
        telemetry.validate("ai_assistant_invoked", {"agent": "把透明度调到 50%"})
    assert telemetry.validate("ai_assistant_invoked", {"agent": "codex"}) == {
        "agent": "codex"}


def test_every_event_carries_the_controlled_auto_properties(sent):
    _enable()
    telemetry.capture("canvas_created", {"creation_kind": "blank"})
    _flush()
    for payload in sent:
        props = payload["properties"]
        for key in ("app_version", "platform", "arch", "distribution"):
            assert key in props, f"{payload['event']} 少了 {key}"
        assert props["platform"] in ("macos", "windows", "linux", "other")
        assert props["arch"] in ("arm64", "x86_64", "other")


def test_no_fingerprinting_surface_in_the_payload(sent):
    """整条 payload 里不许出现主机名 / 内核串 / 可执行文件路径。"""
    import os
    import platform
    import socket
    import sys
    _enable()
    telemetry.capture("app_started", {"app_mode": "browser"})
    _flush()
    blob = json.dumps(sent, ensure_ascii=False)
    for forbidden in (socket.gethostname(), platform.platform(), sys.executable,
                      os.path.expanduser("~")):
        if forbidden and len(forbidden) > 3:
            assert forbidden not in blob


def test_distribution_reuses_the_single_install_kind_authority(sent):
    _enable()
    telemetry.capture("canvas_created", {"creation_kind": "blank"})
    _flush()
    expected = diagnostics.install_kind()
    props = sent[-1]["properties"]
    assert props["distribution"] in ("desktop", "pipx", "pip", "source", "unknown")
    if expected in ("desktop", "pipx", "pip", "source"):
        assert props["distribution"] == expected


# ---------------------------------------------------------------------------
# 队列与失败路径
# ---------------------------------------------------------------------------
def test_queue_is_bounded_and_never_blocks(monkeypatch):
    """代理长时间不可达时，队列必须有上限、且入队永不阻塞。"""
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    import threading
    hold = threading.Event()
    monkeypatch.setattr(telemetry, "_post", lambda payload: hold.wait(30))
    telemetry.set_consent(telemetry.CONSENT_ENABLED)
    accepted = sum(telemetry.capture("canvas_created", {"creation_kind": "blank"})
                   for _ in range(telemetry.QUEUE_MAX * 3))
    hold.set()
    # 队列上限 + 正在被发送的那条；无论如何不能全收
    assert accepted <= telemetry.QUEUE_MAX + 2
    telemetry.reset_for_tests()


def test_transport_failure_never_propagates(monkeypatch):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()

    def boom(_payload):
        raise OSError("代理挂了")
    monkeypatch.setattr(telemetry, "_post", boom)
    telemetry.set_consent(telemetry.CONSENT_ENABLED)
    assert telemetry.capture("export_completed", {"pdf": True}) is True
    assert telemetry.flush(5.0)                 # 发送线程没被异常带走
    assert telemetry.capture("export_completed", {"png": True}) is True
    telemetry.reset_for_tests()


def test_real_post_swallows_network_errors(monkeypatch):
    """`_post` 自己也不许抛：它跑在发送线程里，抛出去就没人接。"""
    import urllib.request
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", "http://127.0.0.1:1/v1/events")

    def refuse(*_a, **_kw):
        raise OSError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    telemetry._post({"event": "app_started", "properties": {}})


# ---------------------------------------------------------------------------
# 会话边界
# ---------------------------------------------------------------------------
def test_import_alone_sends_nothing(sent):
    """光是 import 应用模块不该产生任何事件（--help / doctor / 打包 / 单测）。"""
    _enable()
    sent.clear()
    import importlib
    importlib.import_module("tavotto.app")
    _flush()
    assert [p for p in sent if p["event"] == "app_started"] == []


def test_app_started_fires_once_per_real_session(sent):
    _enable()
    sent.clear()
    telemetry.note_app_started("browser")
    telemetry.note_app_started("browser")
    _flush()
    started = [p for p in sent if p["event"] == "app_started"]
    assert len(started) == 1
    assert started[0]["properties"]["app_mode"] == "browser"


def test_consent_during_a_running_session_backfills_app_started(sent):
    """会话已经在跑了才同意：这台机器不该等到下次启动才被观测到。"""
    telemetry.note_app_started("desktop")
    _flush()
    assert sent == []
    _enable(source="first_run")
    events = [p["event"] for p in sent]
    assert events.count("app_started") == 1
    assert "telemetry_enabled" in events
    first = next(p for p in sent if p["event"] == "telemetry_enabled")
    assert first["properties"]["source"] == "first_run"


def test_unknown_app_mode_is_ignored(sent):
    _enable()
    sent.clear()
    telemetry.note_app_started("cli")
    _flush()
    assert [p for p in sent if p["event"] == "app_started"] == []


# ---------------------------------------------------------------------------
# 日志与诊断
# ---------------------------------------------------------------------------
def test_install_id_is_never_logged(sent, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    _enable()
    telemetry.capture("export_completed", {"pdf": True})
    _flush()
    ident = telemetry.install_id()
    assert ident and ident not in caplog.text


def test_diagnostics_report_redacts_the_install_id(sent):
    _enable()
    ident = telemetry.install_id()
    report = diagnostics.build_report()
    blob = json.dumps(report, ensure_ascii=False)
    assert ident not in blob
    # 但「开没开」要留着：那对排障有用
    assert report["telemetry"]["enabled"] is True
    assert report["telemetry"]["consent"] == "enabled"


def test_diagnostics_bundle_redacts_the_install_id(sent, tmp_path):
    import io
    import zipfile
    _enable()
    ident = telemetry.install_id()
    # 日志里塞一条含标识的行，验证按**值**的那道脱敏也在
    log = engine_config.data_dir() / "cache" / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"2026-08-20 INFO 假装有人把 {ident} 写进了日志\n",
                   encoding="utf-8")
    data = diagnostics.build_bundle()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            text = z.read(name).decode("utf-8", "replace")
            assert ident not in text, f"{name} 里泄漏了匿名标识"
    log.unlink()


# ---------------------------------------------------------------------------
# 端点形状
# ---------------------------------------------------------------------------
def test_payload_shape_has_no_free_form_containers(sent):
    _enable()
    telemetry.capture("figure_edit_completed",
                      {"edit_kind": "layout", "patch_count": 4})
    _flush()
    payload = sent[-1]
    assert set(payload) == {"schema_version", "distinct_id", "event", "properties"}
    for value in payload["properties"].values():
        assert isinstance(value, (bool, int, str)), "属性只能是标量"
        if isinstance(value, str):
            assert len(value) <= 32


def test_endpoint_default_and_override(monkeypatch):
    monkeypatch.delenv("TAVOTTO_TELEMETRY_ENDPOINT", raising=False)
    assert telemetry.endpoint() == "https://telemetry.tavotto.com/v1/events"
    assert re.match(r"^https://", telemetry.endpoint())
    monkeypatch.setenv("TAVOTTO_TELEMETRY_ENDPOINT", "http://127.0.0.1:8123/v1/events")
    assert telemetry.endpoint() == "http://127.0.0.1:8123/v1/events"


def test_no_posthog_key_or_direct_host_anywhere_in_the_client():
    """应用里不许出现 PostHog 项目密钥或直连地址——那是代理的事。

    开源桌面应用里嵌的任何「密钥」都是公开的，所以客户端只认 Tavotto 自己的
    代理；提供商是谁、密钥是什么、发往哪个区域，全部收在代理那一侧。
    （文档与注释里出现 PostHog 这个词是正常的，这里查的是密钥与主机名。）
    """
    from pathlib import Path
    src = Path(telemetry.__file__).resolve().parent.parent
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "phc_" not in text, f"{path} 里出现了疑似 PostHog 项目密钥"
        assert "posthog.com" not in text.lower(), f"{path} 里出现了 PostHog 主机名"
