"""CONSENT_VERSION 2 -> 3（ADR 0134）：从**真实的 v2 已保存状态**升上来的四种人。

`test_telemetry.py` 里那组用例用的是 `CONSENT_VERSION + 1` 的猴子补丁，证明的是机制；这里钉的是这一次
升版本身：磁盘上写着 `consent_version: 2` 的配置，在当前代码下必须是什么样。

* 同意过 v2 的人：升级后一个字节都不发（**包括 `export_result`** 和所有老事件），直到重新同意；
* 重新同意保留原有 `install_id`，也不再补发 `telemetry_enabled`；
* 说过「不」的人：不被再问、保持静默；
* 从没问过的人：仍是 `unset`、没有 `install_id`；
* `TAVOTTO_NO_TELEMETRY=1`：压过一切，不弹框，也不生成 id。
"""

from __future__ import annotations

import pytest

from tavotto.engine import telemetry

EXPORT_RESULT = {
    "outcome": "done",
    "scope": "canvas",
    "pdf": True,
    "png": False,
    "eps": False,
    "tiff": False,
    "svg": False,
    "with_report": False,
    "delivered": 1,
}
V2_ID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def sent(monkeypatch, _isolated_user_config):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("TAVOTTO_TELEMETRY_ENDPOINT", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    yield box
    telemetry.reset_for_tests()


def _flush():
    assert telemetry.flush(5.0)


def _saved_v2(consent: str, *, with_id: bool = True) -> None:
    """把磁盘状态写成 v2 时代的样子（不经 `set_consent`，它会盖上当前版本号）。"""
    patch = {"consent": consent, "consent_version": 2, "ever_enabled": consent == "enabled"}
    if with_id:
        patch["install_id"] = V2_ID
    telemetry._save(patch)


def test_the_version_is_3():
    assert telemetry.CONSENT_VERSION == 3


def test_v2_consent_authorizes_nothing_after_the_upgrade(sent):
    _saved_v2("enabled")
    assert telemetry.enabled() is False
    for name, props in (
        ("export_result", EXPORT_RESULT),
        ("export_completed", {"pdf": True}),
        ("app_started", {"app_mode": "browser"}),
    ):
        assert telemetry.capture(name, props) is False, name
    _flush()
    assert sent == []
    pub = telemetry.public_settings()
    assert pub["needs_reconsent"] is True and pub["saved_consent_version"] == 2
    assert pub["enabled"] is False and pub["consent"] == "enabled"


def test_reconsent_keeps_the_install_id_and_enables_export_result(sent):
    _saved_v2("enabled")
    telemetry.set_consent(telemetry.CONSENT_ENABLED, source="first_run")
    _flush()
    assert telemetry.install_id() == V2_ID, "重新同意不换 install_id"
    assert telemetry.settings()["consent_version"] == 3
    assert telemetry.public_settings()["needs_reconsent"] is False
    assert telemetry.capture("export_result", EXPORT_RESULT) is True
    _flush()
    assert [p["event"] for p in sent if p["event"] == "telemetry_enabled"] == [], "不是新用户"
    assert any(p["event"] == "export_result" for p in sent)
    assert {p["distinct_id"] for p in sent} == {V2_ID}


def test_a_user_who_declined_is_not_reprompted_and_stays_silent(sent):
    _saved_v2("disabled")
    pub = telemetry.public_settings()
    assert pub["needs_reconsent"] is False, "说过不的人，升版不是再问一次的理由"
    assert pub["consent"] == "disabled" and pub["enabled"] is False
    assert telemetry.capture("export_result", EXPORT_RESULT) is False
    _flush()
    assert sent == []


def test_unset_stays_unset_with_no_install_id(sent):
    assert telemetry.settings()["consent"] == "unset"
    assert telemetry.public_settings()["needs_reconsent"] is False
    assert telemetry.capture("export_result", EXPORT_RESULT) is False
    _flush()
    assert sent == []
    assert telemetry.install_id() is None
    assert telemetry.settings()["consent"] == "unset"


def test_no_telemetry_env_wins_and_never_prompts(sent, monkeypatch):
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    # 同意过 v2 的人：不弹重新同意的框，也不发
    _saved_v2("enabled")
    pub = telemetry.public_settings()
    assert pub["hard_disabled"] is True and pub["needs_reconsent"] is False and not pub["enabled"]
    assert telemetry.capture("export_result", EXPORT_RESULT) is False
    # 即便已经是当前版本的同意，硬开关仍然压过
    telemetry._save({"consent_version": telemetry.CONSENT_VERSION})
    assert telemetry.enabled() is False
    assert telemetry.capture("export_result", EXPORT_RESULT) is False
    _flush()
    assert sent == []


@pytest.mark.parametrize("raw", ["v2", "3.0", 2.5, -1, True, None, [3], "²", "3" * 5000])
def test_malformed_saved_version_is_stale_consent_not_a_crash(sent, raw):
    """手改 / 旧版遗留的 `consent_version` 怪形状：不抛、不当成当前版本的同意（Codex #1103 P2）。"""
    telemetry._save({"consent": "enabled", "install_id": V2_ID, "ever_enabled": True})
    with telemetry.config.transaction() as cfg:
        cfg["telemetry"]["consent_version"] = raw
    assert telemetry.settings()["consent_version"] == 0
    assert telemetry.enabled() is False
    assert telemetry.capture("export_result", EXPORT_RESULT) is False
    assert telemetry.public_settings()["needs_reconsent"] is True


def test_numeric_string_version_is_read_as_that_version(sent):
    telemetry._save({"consent": "enabled", "install_id": V2_ID, "ever_enabled": True})
    with telemetry.config.transaction() as cfg:
        cfg["telemetry"]["consent_version"] = "3"
    assert telemetry.settings()["consent_version"] == 3
    assert telemetry.enabled() is True
