"""署名文件 `thanks.signed.json`（ADR 0213 三期）：只是装饰，任何问题都当没有、不影响事实读取。

判据的主语：「不显示」= `thanks_for` 回 `{}` 且事实读取（`get_journal`）仍照常返回；「验过签」= 篡改一字节就回 `{}`；
「没联网」= `_request` 的调用次数，不是函数自己说没联网。
"""

from __future__ import annotations

import json

import pytest

from tavotto import app as appmod, security
from tavotto.engine import journalfacts_remote as facts, journalremote, journals
from test_journalfacts_remote import fact_bundle, index_for, signed

pytest_plugins = ("support.journalfacts_fixture",)

REV = 4


def payload(rev=REV, thanks=None, **over):
    body = {
        "schema": 1,
        "kind": "journal_facts_thanks",
        "catalog_rev": rev,
        "thanks": {"fx-journal": {"line": "李明"}} if thanks is None else thanks,
    }
    return {**body, **over}


class Server:
    """脚本化的 `_request`：索引 / 刊包 / 署名三个路由，记下每次请求。"""

    def __init__(self, key, monkeypatch, thanks_resp):
        self.bundle = json.dumps(fact_bundle()).encode()
        self.index = signed(key, index_for(self.bundle, REV))
        self.thanks_resp = thanks_resp  # (status, body) 或 callable
        self.calls: list[str] = []
        monkeypatch.setattr(facts, "_request", self)
        monkeypatch.setattr(facts, "_thanks_miss", {})

    def __call__(self, method, url, cap, headers):
        self.calls.append(url)
        if url.endswith("index.signed.json"):
            return 200, self.index
        if url.endswith("thanks.signed.json"):
            r = self.thanks_resp
            return r() if callable(r) else r
        if "/journals/" in url:
            return 200, self.bundle
        raise AssertionError(url)

    @property
    def thanks_calls(self):
        return [u for u in self.calls if u.endswith("thanks.signed.json")]


def _ready(key, monkeypatch, thanks_resp):
    srv = Server(key, monkeypatch, thanks_resp)
    facts.get_journal("fx-journal")  # 先同步索引 + 取刊包：署名只认本机已验签索引的版本
    return srv


def test_valid_signed_thanks_is_returned_and_cached_without_a_second_request(isolated, monkeypatch):
    key, _ = isolated
    srv = _ready(key, monkeypatch, (200, signed(key, payload())))
    assert facts.thanks_for("fx-journal") == {"line": "李明"}
    assert facts.thanks_for("fx-journal") == {"line": "李明"}
    assert len(srv.thanks_calls) == 1  # 第二次读本机缓存（同一版本的署名文件不变）
    assert facts.thanks_for("other-journal") == {}
    # 缓存的是原始信封：重新验签仍过
    assert (facts.cache_dir() / "thanks.signed.json").is_file()


@pytest.mark.parametrize(
    "resp",
    [
        (404, b"{}"),  # 老服务端没有这个文件：常态
        (500, b"boom"),
        (200, b"not json"),
    ],
)
def test_missing_or_garbled_file_shows_nothing_and_facts_still_read(isolated, monkeypatch, resp):
    key, _ = isolated
    _ready(key, monkeypatch, resp)
    assert facts.thanks_for("fx-journal") == {}
    assert facts.get_journal("fx-journal")["bundle"]["facts"][0]["id"] == "line"


def test_network_failure_shows_nothing(isolated, monkeypatch):
    key, _ = isolated

    def boom():
        raise journalremote.RemoteCatalogError("network", "OSError")

    _ready(key, monkeypatch, boom)
    assert facts.thanks_for("fx-journal") == {}


def test_tampered_or_wrongly_signed_file_is_not_shown_and_not_cached(isolated, monkeypatch):
    key, _ = isolated
    from nacl.signing import SigningKey

    good = signed(key, payload())
    forged = signed(SigningKey.generate(), payload())  # 别人的钥匙签的
    flipped = json.loads(good)
    flipped["payload"] = flipped["payload"][:-4] + (
        "AAAA" if not flipped["payload"].endswith("AAAA") else "BBBB"
    )
    for resp in (forged, json.dumps(flipped).encode()):
        _ready(key, monkeypatch, (200, resp))
        assert facts.thanks_for("fx-journal") == {}
        assert not (facts.cache_dir() / "thanks.signed.json").exists()


def test_catalog_rev_mismatch_is_not_shown(isolated, monkeypatch):
    key, _ = isolated
    _ready(key, monkeypatch, (200, signed(key, payload(rev=REV + 1))))
    assert facts.thanks_for("fx-journal") == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"extra": 1},  # 多键
        {"schema": 2},
        {"schema": True},
        {"kind": "other"},
        {"catalog_rev": True},
        {"thanks": []},
        {"thanks": {"fx-journal": []}},
        {"thanks": {"Bad Id": {"line": "x"}}},
        {"thanks": {"fx-journal": {"BAD": "x"}}},
    ],
)
def test_wrong_shape_is_rejected_whole(isolated, monkeypatch, bad):
    key, _ = isolated
    p = payload()
    p.update(bad)
    _ready(key, monkeypatch, (200, signed(key, p)))
    assert facts.thanks_for("fx-journal") == {}


def test_one_bad_name_drops_only_that_entry(isolated, monkeypatch):
    key, _ = isolated
    names = {
        "fx-journal": {
            "line": "李明",
            "b-ok": "  Ｌi  ",
            "c": "a@b",
            "d": "x" * 33,
            "e": "https://x",
            "f": "a‮b",
            "g": 7,
        }
    }
    _ready(key, monkeypatch, (200, signed(key, payload(thanks=names))))
    assert facts.thanks_for("fx-journal") == {"line": "李明", "b-ok": "Li"}


def test_no_journal_sync_switch_means_no_request(isolated, monkeypatch):
    key, _ = isolated
    srv = _ready(key, monkeypatch, (200, signed(key, payload())))
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    assert facts.thanks_for("fx-journal") == {}
    assert srv.thanks_calls == []


def test_a_miss_is_not_retried_on_every_call(isolated, monkeypatch):
    key, _ = isolated
    srv = _ready(key, monkeypatch, (404, b"{}"))
    for _ in range(3):
        assert facts.thanks_for("fx-journal") == {}
    assert len(srv.thanks_calls) == 1


def test_preflight_endpoint_carries_thanks_only_for_facts_in_the_bundle(isolated, monkeypatch):
    key, _ = isolated
    names = {"fx-journal": {"line": "李明", "ghost": "不存在的事实"}}
    _ready(key, monkeypatch, (200, signed(key, payload(thanks=names))))
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    state = security.SessionState("test-nonce", api_secret="test-secret")
    state.port = 54321
    monkeypatch.setitem(appmod.app.config, security.STATE_KEY, state)
    client = appmod.app.test_client()
    headers = {"Host": "127.0.0.1:54321", "X-Tavotto-Auth": "test-secret"}
    res = client.post(
        "/api/journal-facts/preflight", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert res.status_code == 200 and res.json["thanks"] == {"line": "李明"}
    # 署名取不到：照常 200，thanks 为空
    _ready(key, monkeypatch, (404, b"{}"))
    (facts.cache_dir() / "thanks.signed.json").unlink(missing_ok=True)
    res = client.post(
        "/api/journal-facts/preflight", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert res.status_code == 200 and res.json["thanks"] == {}
