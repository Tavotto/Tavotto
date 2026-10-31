"""卸载后重装 → 新手态：记号文件的读 / 一次性消费 / 平台闸（ADR 0136 修订 2026-10-21）。"""

from __future__ import annotations

import pytest

from tavotto import app as m
from tavotto.engine import reinstall_marker as rm


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(rm, "_supported", lambda: True)


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_marker_lives_in_config_dir_under_the_name_the_installer_writes(cfg):
    assert rm.marker_path() == cfg / "reinstall-marker"


def test_no_marker_means_not_a_reinstall(cfg, windows):
    assert rm.is_reinstall() is False
    assert rm.consume() is False


def test_marker_is_read_without_deleting_then_consumed_once(cfg, windows):
    (cfg / "reinstall-marker").write_text("1760000000", encoding="utf-8")
    assert rm.is_reinstall() is True
    assert rm.is_reinstall() is True  # 读不删：前端没处理完下次还在
    assert rm.consume() is True
    assert rm.is_reinstall() is False
    assert rm.consume() is False


def test_non_windows_ignores_a_stray_marker(cfg, monkeypatch):
    monkeypatch.setattr(rm, "_supported", lambda: False)
    (cfg / "reinstall-marker").write_text("x", encoding="utf-8")
    assert rm.is_reinstall() is False
    assert rm.consume() is False
    assert (cfg / "reinstall-marker").exists()  # 也不删


def test_api_returns_only_a_boolean_and_ack_consumes(cfg, windows, client):
    (cfg / "reinstall-marker").write_text("secret-ts", encoding="utf-8")
    r = client.get("/api/onboarding/reinstall")
    assert r.get_json() == {"reinstalled": True}
    assert "secret-ts" not in r.get_data(as_text=True)
    assert client.get("/api/onboarding/reinstall").get_json() == {"reinstalled": True}
    assert client.post("/api/onboarding/reinstall/ack").get_json() == {"consumed": True}
    assert client.get("/api/onboarding/reinstall").get_json() == {"reinstalled": False}


def test_api_without_marker_is_false(cfg, windows, client):
    assert client.get("/api/onboarding/reinstall").get_json() == {"reinstalled": False}
    assert client.post("/api/onboarding/reinstall/ack").get_json() == {"consumed": False}
