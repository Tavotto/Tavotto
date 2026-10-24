"""改图助手「厂商目录」（`engine/ai_catalog.json` + `engine/ai_catalog.py`）。

两件事：
* **目录结构**：预设从 JSON 来，加载失败要大声失败；每行字段齐全、id 唯一、地址是 https、agent 在注册表里、
  界面文案键在中英两份 locale 都有。
* **型号实时列表**：`GET {base_url}/v1/models`——成功 / 超时 / 非 JSON / 401 / 重定向各自退回稳定原因码，
  缓存命中不再联网，密钥不进日志也不进缓存文件。
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_catalog, ai_providers

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"
KEY = "sk-super-secret-1234567890"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    ai_bridge.invalidate_capabilities()
    yield
    ai_bridge.invalidate_capabilities()


# ---- 目录结构 ----------------------------------------------------------------


def _locale_value(loc: str, dotted: str):
    node = json.loads((LOCALES / loc / "dialogs.json").read_text(encoding="utf-8"))
    node = node["settings"]["agents"]
    for part in dotted.split("."):
        node = node[part]
    return node


def test_presets_come_from_the_catalog_file():
    rows = ai_catalog.load_vendors()
    # `preset: false` 的行（官方 API Key，只作「连接」视图里某厂商的一种接入方式）不进旧版预设列表
    assert [p["id"] for p in ai_providers.PRESETS] == [
        r["id"] for r in rows if r.get("preset", True) is not False
    ]
    assert {r["id"] for r in rows if r.get("preset") is False} == {"openai-api", "anthropic-api"}
    assert len(rows) >= 13


def test_catalog_rows_are_complete_unique_and_wired():
    rows = ai_catalog.load_vendors()
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "id 重复"
    endpoint_agents = set(ai_agents.endpoint_agents())
    # 只有这四行允许空地址：官方登录态（用 CLI 自己的登录）与两条用户自填
    blank_ok = {"anthropic", "openai", "custom-claude", "custom-codex"}
    for r in rows:
        assert not [k for k in ai_catalog.REQUIRED_FIELDS if k not in r], r["id"]
        assert r["region"] in ai_catalog.REGIONS, r["id"]
        assert r["agent"] in endpoint_agents, r["id"]
        assert r["oauth"] is None, "本期不做 OAuth：预留位必须是 null"
        assert isinstance(r["models_hint"], list) and r["source"].strip(), r["id"]
        if r["id"] in blank_ok:
            assert r["base_url"] == ""
        else:
            assert r["base_url"].startswith("https://"), r["id"]
            assert r["key_url"].startswith("https://"), r["id"]
        if r["agent"] == "codex":
            # Codex 只认 Responses：只有官方文档写明原生支持 Responses 的厂商才许挂在它下面
            assert r["id"] in (
                "openai",
                "openai-api",
                "custom-codex",
                "deepseek-codex",
                "kimi-code-codex",
            ), r["id"]
            assert r.get("wire_api") == "responses", r["id"]
        # 界面文案：label_key / key_hint_key 在中英 locale 都有
        for key in [r["label_key"], r.get("key_hint_key")]:
            if not key:
                continue
            for loc in ("zh-CN", "en-US"):
                text = _locale_value(loc, key)
                assert isinstance(text, str) and text.strip(), (loc, key)


def test_catalog_json_holds_no_chinese_ui_strings():
    """界面名与提示走 i18n 键；JSON 里的中文只允许出现在 `source`（给维护者看的出处注记）。"""
    for r in ai_catalog.load_vendors():
        for k, v in r.items():
            if k == "source":
                continue
            assert not any("一" <= ch <= "鿿" for ch in json.dumps(v, ensure_ascii=False)), (
                r["id"],
                k,
            )


def test_public_preset_shape_stays_compatible():
    for p in ai_providers.PRESETS:
        # 前端 AiEndpointPreset 的老字段不能丢
        for k in ("id", "label", "agent", "base_url", "models"):
            assert k in p, (p["id"], k)
    custom = next(p for p in ai_providers.PRESETS if p["id"] == "custom-codex")
    assert custom["wire_api"] == "responses"


def test_loading_fails_loudly(tmp_path):
    with pytest.raises(ai_catalog.CatalogError):
        ai_catalog.load_vendors(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ai_catalog.CatalogError):
        ai_catalog.load_vendors(bad)
    empty = tmp_path / "empty.json"
    empty.write_text('{"vendors": []}', encoding="utf-8")
    with pytest.raises(ai_catalog.CatalogError):
        ai_catalog.load_vendors(empty)
    short = tmp_path / "short.json"
    short.write_text('{"vendors": [{"id": "x"}]}', encoding="utf-8")
    with pytest.raises(ai_catalog.CatalogError, match="缺字段"):
        ai_catalog.load_vendors(short)


def test_catalog_file_is_packaged():
    """wheel 的 packages = src/tavotto 自然带 JSON；冻结产物靠 packaging/tavotto.spec 的 datas 显式列。"""
    assert ai_catalog.CATALOG_PATH.is_file()
    spec = (ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert "ai_catalog.json" in spec


# ---- 型号实时列表 ------------------------------------------------------------


class _Gateway:
    """本机假网关：记录每次请求，按 mode 回应。"""

    def __init__(self, mode="ok", delay=0.0):
        self.mode = mode
        self.delay = delay
        self.hits: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
                )
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.mode == "401":
                    self.send_response(401)
                    self.end_headers()
                    return
                if outer.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/steal")
                    self.end_headers()
                    return
                body = (
                    b"<html>not json</html>"
                    if outer.mode == "html"
                    else json.dumps(
                        {"data": [{"id": "m-a"}, {"id": "m-b"}, {"id": "m-a"}, {"x": 1}]}
                    ).encode()
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gateway():
    made = []

    def make(**kw):
        g = _Gateway(**kw)
        made.append(g)
        return g

    yield make
    for g in made:
        g.close()


def test_live_models_success_sends_both_auth_headers(gateway):
    g = gateway()
    out = ai_catalog.live_models("e1", g.url, KEY)
    assert out == {"models": ["m-a", "m-b"], "source": "live", "reason": None}
    hit = g.hits[0]
    assert hit["path"] == "/v1/models"
    assert hit["headers"]["x-api-key"] == KEY
    assert hit["headers"]["authorization"] == f"Bearer {KEY}"
    assert hit["headers"]["anthropic-version"] == "2023-06-01"


def test_base_url_already_ending_in_v1_is_not_doubled():
    assert ai_catalog.models_url("https://gw.example/v1/") == "https://gw.example/v1/models"
    assert (
        ai_catalog.models_url("https://gw.example/coding/") == "https://gw.example/coding/v1/models"
    )


@pytest.mark.parametrize(
    "mode,reason",
    [("401", "unauthorized"), ("html", "bad_response"), ("redirect", "bad_response")],
)
def test_live_models_failures_return_a_reason_never_raise(gateway, mode, reason):
    g = gateway(mode=mode)
    out = ai_catalog.live_models("e1", g.url, KEY)
    assert out == {"models": [], "source": "none", "reason": reason}
    assert len(g.hits) == 1, "重定向不许被跟随到别的主机"


def test_live_models_timeout(gateway, monkeypatch):
    monkeypatch.setattr(ai_catalog, "TIMEOUT_S", 0.3)
    g = gateway(delay=2.0)
    out = ai_catalog.live_models("e1", g.url, KEY)
    assert out["source"] == "none" and out["reason"] == "timeout"


def test_live_models_network_error_and_missing_inputs():
    assert ai_catalog.live_models("e1", "http://127.0.0.1:1", KEY)["reason"] == "network"
    assert ai_catalog.live_models("e1", "", KEY)["reason"] == "no_key"
    assert ai_catalog.live_models("e1", "https://gw.example", "")["reason"] == "no_key"
    assert ai_catalog.live_models("e1", "ftp://gw.example", KEY)["reason"] == "bad_response"


def test_failure_is_not_cached(gateway):
    g = gateway(mode="401")
    ai_catalog.live_models("e1", g.url, KEY)
    g.mode = "ok"
    assert ai_catalog.live_models("e1", g.url, KEY)["source"] == "live"


def test_cache_hit_skips_the_network_until_refresh_or_expiry(gateway, monkeypatch):
    g = gateway()
    first = ai_catalog.live_models("e1", g.url, KEY)
    second = ai_catalog.live_models("e1", g.url, KEY)
    assert first["source"] == "live" and second == {
        "models": ["m-a", "m-b"],
        "source": "cache",
        "reason": None,
    }
    assert len(g.hits) == 1
    # 不同密钥 / 不同接口 id 不共用缓存
    assert ai_catalog.live_models("e1", g.url, KEY + "x")["source"] == "live"
    assert ai_catalog.live_models("e2", g.url, KEY)["source"] == "live"
    # 手动刷新绕过缓存
    assert ai_catalog.live_models("e1", g.url, KEY, refresh=True)["source"] == "live"
    # 一天后过期
    n = len(g.hits)
    real = time.time()
    monkeypatch.setattr(ai_catalog.time, "time", lambda: real + ai_catalog.CACHE_TTL_S + 5)
    assert ai_catalog.live_models("e1", g.url, KEY)["source"] == "live"
    assert len(g.hits) == n + 1


def test_key_never_reaches_logs_or_cache_file(gateway, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    for mode in ("ok", "401", "html"):
        g = gateway(mode=mode)
        ai_catalog.live_models(f"e-{mode}", g.url, KEY)
    cache = (tmp_path / "data" / "ai_models_cache.json").read_text(encoding="utf-8")
    assert KEY not in cache and "secret" not in cache
    assert KEY not in caplog.text
    assert "127.0.0.1" not in caplog.text, "日志不写 URL"


def test_cache_lives_under_data_dir_not_the_package(gateway, tmp_path):
    g = gateway()
    ai_catalog.live_models("e1", g.url, KEY)
    assert (tmp_path / "data" / "ai_models_cache.json").is_file()
    assert not list(Path(ai_catalog.__file__).parent.glob("*cache*.json"))


def test_providers_wrapper_fills_url_and_key_from_the_saved_endpoint(gateway):
    g = gateway()
    ai_providers.save(
        {"id": "mine", "agent": "claude", "label": "mine", "base_url": g.url, "api_key": KEY}
    )
    out = ai_providers.live_models("mine", None, None)
    assert out["models"] == ["m-a", "m-b"]
    assert g.hits[0]["headers"]["x-api-key"] == KEY
    # 界面给了就用界面的（新建、还没保存时也能先试）
    assert ai_providers.live_models(None, g.url, "sk-typed")["source"] == "live"
    assert g.hits[-1]["headers"]["x-api-key"] == "sk-typed"
    assert ai_providers.live_models("nope", None, None)["reason"] == "no_key"


class _CountingOpener:
    """假 opener：只数有没有被调用——被拒的请求必须一次都不发。"""

    calls = 0

    def open(self, req, timeout=None):
        type(self).calls += 1
        raise OSError("must not be called")


@pytest.fixture
def counting(monkeypatch):
    _CountingOpener.calls = 0
    monkeypatch.setattr(ai_catalog, "_opener", lambda **_kw: _CountingOpener())
    return _CountingOpener


def test_saved_key_is_not_sent_to_a_different_origin(counting):
    ai_providers.save(
        {
            "id": "mine",
            "agent": "claude",
            "label": "m",
            "base_url": "https://a.example",
            "api_key": KEY,
        }
    )
    out = ai_providers.live_models("mine", "https://b.example", None)
    assert out == {"models": [], "source": "none", "reason": "key_origin_mismatch"}
    assert counting.calls == 0
    # 端口不同 / scheme 不同也是另一个源
    for other in ("https://a.example:8443", "http://a.example"):
        assert ai_providers.live_models("mine", other, None)["reason"] in (
            "key_origin_mismatch",
            "insecure_url",
        )
    assert counting.calls == 0
    # 用户当场重新填了密钥 → 允许（密钥是用户刚给的，不是已存的）
    ai_providers.live_models("mine", "https://b.example", "sk-new")
    assert counting.calls == 1


def test_saved_key_is_usable_on_same_origin_other_path(counting):
    ai_providers.save(
        {
            "id": "mine",
            "agent": "claude",
            "label": "m",
            "base_url": "https://A.example:443",
            "api_key": KEY,
        }
    )
    ai_providers.live_models("mine", "https://a.example/v2", None)
    assert counting.calls == 1  # 大小写 / 默认端口归一，路径不算


@pytest.mark.parametrize(
    "url",
    ["http://evil.example", "http://localhost.evil.example", "http://127.0.0.1.nip.io"],
)
def test_plain_http_to_non_loopback_never_sends_the_key(counting, url):
    out = ai_catalog.live_models("e1", url, KEY)
    assert out["reason"] == "insecure_url"
    assert counting.calls == 0


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://[::1]:1234", "http://localhost"])
def test_plain_http_to_loopback_is_allowed(counting, url):
    ai_catalog.live_models("e1", url, KEY)
    assert counting.calls == 1


def test_loopback_http_never_goes_through_an_env_proxy(gateway, monkeypatch):
    """回环 http 例外核的是 URL；设了 HTTP_PROXY 时请求不许被交给代理（密钥会明文出去）。"""
    proxy = gateway()
    target = gateway()
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, proxy.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    out = ai_catalog.live_models("e1", target.url, KEY, refresh=True)
    assert out["source"] == "live", out
    assert len(target.hits) == 1 and proxy.hits == []


def test_redirects_are_still_not_followed(gateway):
    g = gateway(mode="redirect")
    assert ai_catalog.live_models("e1", g.url, KEY)["reason"] == "bad_response"
    assert len(g.hits) == 1


def test_models_endpoint_http(gateway):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    g = gateway()
    ai_providers.save(
        {"id": "mine", "agent": "claude", "label": "mine", "base_url": g.url, "api_key": KEY}
    )
    resp = client.post("/api/ai/endpoints/models", json={"id": "mine"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["models"] == ["m-a", "m-b"] and body["source"] == "live"
    assert KEY not in resp.get_data(as_text=True)
    # 没有任何输入也是 200 + 原因码，不是 500
    empty = client.post("/api/ai/endpoints/models", json={})
    assert empty.status_code == 200 and empty.get_json()["reason"] == "no_key"
    # 畸形请求体不炸
    assert (
        client.post(
            "/api/ai/endpoints/models", data="[1]", content_type="application/json"
        ).status_code
        == 200
    )


# ---- 厂商图标 ----------------------------------------------------------------

ICON_TABLE = ROOT / "web" / "src" / "components" / "settings" / "vendorIcons.ts"
#: 显式列出「没有可靠图形、用通用图标」的 icon_key：synthetic（lobe-icons 与 simple-icons 都没有）、
#: custom（用户自填的接口）。新增厂商没图形时必须在这里登记，而不是悄悄落到通用图标。
GENERIC_ICON_KEYS = {"synthetic", "custom"}


def _icon_table_keys() -> set[str]:
    import re

    text = ICON_TABLE.read_text(encoding="utf-8")
    body = text[text.index("export const VENDOR_GLYPHS") :]
    return set(re.findall(r"^  '?([a-z-]+)'?: \[$", body, flags=re.M))


def test_every_icon_key_has_a_glyph_or_is_explicitly_generic():
    table = _icon_table_keys()
    assert table, "没解析出图形表（正则与 vendorIcons.ts 的写法对不上了？）"
    for r in ai_catalog.load_vendors():
        key = r["icon_key"]
        assert key in table or key in GENERIC_ICON_KEYS, (r["id"], key)
    # 反向：表里没有人用的图形不留着吃体积
    used = {r["icon_key"] for r in ai_catalog.load_vendors()}
    assert table <= used, table - used
    # 通用清单里的不许同时又在表里（登记过期）
    assert not (GENERIC_ICON_KEYS & table)


def test_same_brand_shares_one_icon_key():
    by_id = {r["id"]: r["icon_key"] for r in ai_catalog.load_vendors()}
    assert by_id["minimax"] == by_id["minimax-global"] == "minimax"
    assert by_id["deepseek"] == by_id["deepseek-codex"] == "deepseek"
    assert by_id["dashscope"] == by_id["dashscope-coding"]


def test_saved_endpoint_carries_icon_key_by_address_not_by_id():
    ai_providers.save(
        {
            "id": "whatever",
            "agent": "claude",
            "label": "x",
            "base_url": "https://API.deepseek.com/anthropic/",
        }
    )
    ai_providers.save(
        {"id": "deepseek", "agent": "claude", "label": "d", "base_url": "https://gw.example/x"}
    )
    ai_providers.save(
        {"id": "cx", "agent": "codex", "label": "c", "base_url": "https://api.deepseek.com"}
    )
    pub = {p["id"]: p for p in map(ai_providers.public, ai_providers.list_providers())}
    assert pub["whatever"]["icon_key"] == "deepseek"
    assert pub["deepseek"]["icon_key"] == "custom", "id 叫 deepseek 不算；地址说了算"
    assert pub["cx"]["icon_key"] == "deepseek"
    # 同一地址换了执行器就不是那一行
    assert ai_catalog.icon_for("claude", "https://api.deepseek.com") == "custom"
    assert ai_catalog.icon_for("claude", "") == "custom"


def test_third_party_notices_cover_the_icon_sources():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "lobehub/lobe-icons" in notices and "MIT License" in notices and "LobeHub" in notices
    assert "Simple Icons" in notices and "CC0" in notices
    for key in _icon_table_keys():
        assert key in notices, f"{key} 的图形来源没在 THIRD_PARTY_NOTICES.md 登记"
