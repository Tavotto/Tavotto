"""Agent（MCP 桥）读远程期刊目录（ADR 0155 §10.1）：列表读「种子 + 缓存索引」不计数；
open / preflight / export / normalize 碰到本地没有的 journal_id 先 `ensure_journal`；
超限 / 离线 / 验签失败回**明确的错误码**，不静默退回。服务端用 `test_journal_remote.FakeServer`（夹具库 fx-*）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from journal_catalog_signing import TEST_SEED, b64, public_key
from tavotto.engine import journalremote as jr, journals
from test_journal_remote import BASE, FakeServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge  # noqa: E402


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TAVOTTO_NO_JOURNAL_SYNC", raising=False)
    monkeypatch.setattr(jr, "_CLIENT_OVERRIDE", None)  # 桥会写它；monkeypatch 让 teardown 还原
    monkeypatch.delenv("TAVOTTO_NETWORK_REGION", raising=False)
    monkeypatch.setattr(jr, "CATALOG_URLS", (BASE,))
    monkeypatch.setattr(jr, "TRUSTED_KEYS", {"test-1": b64(public_key(TEST_SEED))})
    monkeypatch.setattr(jr, "seed_catalog", lambda: {"publishers": {}, "journals": {}})
    monkeypatch.setattr(jr, "seed_rev", lambda: 1)
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    monkeypatch.setattr(bridge, "_MCP_CLIENT_NAME", "codex")
    server = FakeServer()
    monkeypatch.setattr(jr, "_request", server)
    return server


def _code(fn, *a, **k) -> bridge.BridgeError:
    with pytest.raises(bridge.BridgeError) as exc:
        fn(*a, **k)
    return exc.value


def test_new_agent_error_codes_are_registered_next_to_the_existing_ones():
    for code in (
        "journal_catalog_disabled",
        "journal_id_conflict",
        "journal_quota_exceeded",
        "journal_offline",
        "journal_verification_failed",
    ):
        assert code in journals.AGENT_ERROR_CODES
    assert not set(journals.AGENT_ERROR_CODES) & set(journals.ERROR_CODES)


# ------------------------------- 列表：不计数 -------------------------------
def test_list_reads_cached_index_without_counting_and_marks_download_state(srv):
    out = bridge.list_journals(None)
    assert out["catalog_rev"] == 5
    ids = {j["id"]: j["downloaded"] for j in out["journals"]}
    assert "fx-beta" in ids and not any(ids.values())
    assert srv.count("journals/") == 0 and srv.count("device") == 0  # 读索引既不领令牌也不计数
    # 离线时照样能列（缓存里的索引）
    srv.offline = True
    again = bridge.list_journals("beta")
    assert [j["id"] for j in again["journals"]] == ["fx-beta"]


def test_list_falls_back_to_seed_when_remote_is_unreachable(srv, monkeypatch):
    srv.offline = True
    out = bridge.list_journals(None)  # 没有缓存：agent_catalog 回 None，用引擎的内置种子
    assert out["journals"] == [] or "downloaded" not in out["journals"][0]


# ------------------------------- 选期刊：按需下载 -------------------------------
def test_selection_downloads_once_and_reports_client_type(srv):
    pid, jov, ref = bridge.journal_selection(None, None, "fx-beta")
    assert pid == "journal-base-v1" and jov and ref["id"] == "fx-beta" and ref["catalog_rev"] == 5
    reads = srv.count("journals/")
    assert reads == 1
    bridge.journal_selection(None, None, "fx-beta")  # 本地命中：不再请求（服务端也就不计数）
    assert srv.count("journals/") == reads
    clients = {h["X-Tavotto-Client"] for _, _, h in srv.calls}
    assert clients == {"agent-codex"}
    listed = {j["id"]: j["downloaded"] for j in bridge.list_journals(None)["journals"]}
    assert listed["fx-beta"] is True and listed["fx-gamma"] is False


def test_selection_without_journal_id_touches_nothing(srv):
    assert bridge.journal_selection("lab-publication-v1", None, None) == (
        "lab-publication-v1",
        None,
        None,
    )
    assert srv.calls == []


def test_conflict_with_handwritten_profile_does_not_spend_quota(srv):
    err = _code(bridge.journal_selection, "lab-publication-v1", None, "fx-beta")
    assert err.code == "journal_id_conflict"
    assert srv.count("journals/") == 0


def test_unknown_journal_id_is_journal_unknown(srv):
    err = _code(bridge.journal_selection, None, None, "no-such-journal")
    assert err.code == "journal_unknown"
    assert srv.count("journals/") == 0


# ------------------------------- 明确的错误码，不静默退回 -------------------------------
def test_quota_exceeded_is_an_explicit_error_with_wait_time(srv):
    srv.quota = {"code": "quota_day", "retry_after_s": 7200}
    err = _code(bridge.journal_selection, None, None, "fx-beta")
    assert err.code == "journal_quota_exceeded"
    assert err.extra["retry_after_s"] == 7200 and "120 分钟" in str(err)
    assert "不要换设备令牌" in str(err)


def test_offline_without_the_bundle_is_journal_offline(srv):
    bridge.list_journals(None)  # 先把索引缓存下来
    srv.offline = True
    err = _code(bridge.journal_selection, None, None, "fx-beta")
    assert err.code == "journal_offline"


def test_sync_switch_off_means_seed_only(srv, monkeypatch):
    """同步关着：能用的目录只有种子。种子里没有的 id 是「不认识」；索引已缓存时，没下载的刊是「离线」。"""
    monkeypatch.setenv("TAVOTTO_NO_JOURNAL_SYNC", "1")
    err = _code(bridge.journal_selection, None, None, "fx-beta")
    assert err.code == "journal_unknown" and srv.calls == []
    monkeypatch.delenv("TAVOTTO_NO_JOURNAL_SYNC")
    bridge.list_journals(None)  # 联网把索引缓存下来
    monkeypatch.setenv("TAVOTTO_NO_JOURNAL_SYNC", "1")
    n = len(srv.calls)
    err = _code(bridge.journal_selection, None, None, "fx-beta")
    assert err.code == "journal_offline" and len(srv.calls) == n


def test_bundle_failing_verification_is_an_explicit_error(srv):
    bridge.list_journals(None)
    path = "r5/journals/fx-beta.json"
    srv.files[path] = srv.files[path].replace(b"Fixture", b"Fixturf", 1)
    err = _code(bridge.journal_selection, None, None, "fx-beta")
    assert err.code == "journal_verification_failed"
    assert "不要采用" in str(err)


def test_seed_journals_need_no_download_and_no_token(srv, monkeypatch):
    import copy
    import json

    cat = copy.deepcopy(
        json.loads((ROOT / "tests" / "golden" / "journal_vectors.json").read_text("utf-8"))[
            "catalog"
        ]
    )
    monkeypatch.setattr(
        jr, "seed_catalog", lambda: {"publishers": cat["publishers"], "journals": cat["journals"]}
    )
    monkeypatch.setattr(
        journals, "load_catalog", lambda c=None: journals.validate_catalog(c or cat)
    )
    _, _, ref = bridge.journal_selection(None, None, "fx-beta")
    assert ref["id"] == "fx-beta"
    assert srv.count("journals/") == 0 and srv.count("device") == 0


def test_client_name_is_slugged_and_defaults_to_mcp(srv, monkeypatch):
    bridge._remember_mcp_client_name({"name": "Claude Code!! v2"})
    assert bridge._MCP_CLIENT_NAME == "claude-code-v2"
    monkeypatch.setattr(bridge, "_MCP_CLIENT_NAME", None)
    bridge._journal_remote()
    assert jr.client_type() == "agent-mcp"
