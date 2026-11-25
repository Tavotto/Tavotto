# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「读懂数据」用哪个 AI：auto / 关闭 / 指定连接（`ai_data_aichoice`，ADR 0178 修订 2026-11-25）。

判据的主语：**数据发给谁**——用假传输记下每次请求的主机：
* 指定了连接 B（而 auto 会挑 A）：请求只能到 B；B 此刻用不了 = 一个请求都不发、绝不退到 A；
* 关闭：`ai_data_roles` 与 `ai_data_model` 两个出站入口都零请求，也不读磁盘里旧连接的判断；
* `auto`：传给 `call_structured` 的参数与改动前一致（Key 连接不带 `conn`，由它自己挑 / 刷新）；
* 换连接后不命中上一个连接的缓存 / 在途任务 / 负缓存；
* 清单与接口不含任何密钥 / 地址 / 令牌，GET 不联网（`isolate` 里任何 socket 访问即失败）。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import (
    ai_bridge,
    ai_connections,
    ai_data_aichoice as C,
    ai_data_figure as adf,
    ai_data_model as M,
    ai_data_roles as R,
    ai_providers,
    ai_structured,
    config,
)
from tests.test_ai_data_propose import propose, put  # noqa: F401
from tests.test_ai_data_roles import D, isolate, project  # noqa: F401

SECRET_A, SECRET_B = "sk-secret-AAAA1111", "sk-secret-BBBB2222"


def add_key(cid, host, secret, label=None):
    ai_providers.save(
        {
            "id": cid,
            "label": label or cid.upper(),
            "agent": "claude",
            "api_key": secret,
            "base_url": f"https://{host}/anthropic",
            "models": ["fake-model"],
        }
    )


@pytest.fixture(autouse=True)
def no_cached_capabilities(monkeypatch):
    """进程里别的用例可能留下能力缓存（订阅连接）：本文件默认没有订阅连接，要的用 `with_sub`。"""
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: None)


@pytest.fixture
def two_keys():
    add_key("a", "gw-a.invalid", SECRET_A)
    add_key("b", "gw-b.invalid", SECRET_B)


SUB_CAPS = {
    "connections": [
        {
            "id": "sub:codex",
            "vendor": "openai",
            "label": "ChatGPT",
            "logo": "openai",
            "state": "ready",
            "models": ["gpt-x"],
            "default_model": "gpt-x",
        }
    ]
}


@pytest.fixture
def with_sub(monkeypatch):
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: SUB_CAPS)


@pytest.fixture
def real_settings(project, monkeypatch):
    """`project` 夹具把 `config.ai_settings` 换成只有实验开关的假设置；这里换成「真配置 + 按夹具开关并入 `taf.enabled`」，选择与连接才能真的存取。"""
    state = project[3]

    def settings():
        ai = dict(config.load()["ai"])
        taf = dict(ai.get("taf") or {})
        if state["taf"]:
            taf["enabled"] = True
        else:
            taf.pop("enabled", None)
        ai["taf"] = taf
        return ai

    monkeypatch.setattr(config, "ai_settings", settings)


def table(tmp_path):
    folder = tmp_path / "tbl"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "t.csv").write_text(
        "group,value\n" + "\n".join(f"{g},{i}" for i, g in enumerate("aabbcc")) + "\n"
    )
    prof = adf.profile_folder(folder)
    return prof.tables[0], adf.inspect_data_source(
        folder / "t.csv", None, v2=True, allow_large=True
    )


def go(tmp_path, post=None, project="p"):
    """真走 `start`（不注入连接）：同步跑完，返回 `start` 的结果。"""
    entry, values = table(tmp_path)
    extra = {"post": post} if post is not None else {}
    old = R.set_overrides(sync=True, **extra)
    try:
        return R.start(project=project, entry=entry, values=values, hint=None, data_path=None)
    finally:
        R.restore_overrides(old)


def hosts(fake):
    return [c["url"].split("/")[2] for c in fake.calls]


# =========================================================================== 存取
@pytest.mark.parametrize(
    "stored, want",
    [
        (None, ("auto", None)),
        ({"mode": "auto"}, ("auto", None)),
        ({"mode": "off"}, ("off", None)),
        ({"mode": "connection", "connection": "b"}, ("connection", "b")),
        # 畸形 / 未知一律当关闭：宁可不发数据
        ({"mode": "connection"}, ("off", None)),
        ({"mode": "connection", "connection": ""}, ("off", None)),
        ({"mode": "connection", "connection": 3}, ("off", None)),
        ({"mode": "banana"}, ("off", None)),
        ({}, ("off", None)),
        ("auto", ("off", None)),
        (["auto"], ("off", None)),
    ],
)
def test_read_choice_is_conservative(stored, want):
    config.set_ai_settings(
        {"taf": {"enabled": True, **({} if stored is None else {"understanding": stored})}}
    )
    assert C.read_choice() == want


def test_write_keeps_the_other_taf_keys_and_auto_clears():
    config.set_ai_settings({"taf": {"enabled": True}})
    C.write_choice("off")
    assert config.ai_settings()["taf"] == {"enabled": True, "understanding": {"mode": "off"}}
    C.write_choice("connection", "b")
    assert config.ai_settings()["taf"]["understanding"] == {"mode": "connection", "connection": "b"}
    C.write_choice("auto")
    assert config.ai_settings()["taf"] == {"enabled": True}
    with pytest.raises(ValueError):
        C.write_choice("connection", "")


# =========================================================================== 清单
def test_options_list_usable_connections_without_secrets(two_keys, with_sub):
    opts = C.options()
    assert [o["id"] for o in opts] == ["sub:codex", "a", "b"]
    assert opts[0] == {
        "id": "sub:codex",
        "label": "ChatGPT",
        "logo": "openai",
        "vendor": "openai",
        "kind": "subscription",
        "model": "gpt-x",
    }
    assert all(set(o) == {"id", "label", "logo", "vendor", "kind", "model"} for o in opts)
    blob = json.dumps(opts)
    assert (
        SECRET_A not in blob and SECRET_B not in blob and "gw-a" not in blob and "https" not in blob
    )


def test_options_skip_unusable_connections(two_keys, with_sub, monkeypatch):
    ai_providers.save(
        {
            "id": "bad",
            "label": "x",
            "agent": "claude",
            "api_key": "k",
            "base_url": "https://x.invalid/anthropic",
            "models": ["m"],
            "last_error": "unauthorized",
        }
    )
    assert [o["id"] for o in C.options()] == ["sub:codex", "a", "b"]
    monkeypatch.setattr(
        ai_bridge, "capabilities_if_cached", lambda: None
    )  # 能力缓存还没有：没有订阅项
    assert [o["id"] for o in C.options()] == ["a", "b"]
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: False)  # 运维关掉内置：一个都没有
    assert C.options() == []


# =========================================================================== 判据
def test_resolve_per_mode(two_keys, with_sub):
    auto = lambda: {"provider_id": "auto-pick"}  # noqa: E731
    C.write_choice("off")
    assert C.resolve(auto) is None
    C.write_choice("connection", "b")
    assert C.resolve(auto)["provider_id"] == "b"
    C.write_choice("connection", "sub:codex")
    assert C.resolve(auto)["conn_id"] == "sub:codex"
    C.write_choice("auto")
    assert C.resolve(auto) == {"provider_id": "auto-pick"}


def test_a_chosen_connection_that_is_gone_never_falls_back_to_another(two_keys, monkeypatch):
    C.write_choice("connection", "b")
    ai_providers.delete("b")
    assert R._available_connection() is None  # a 还在，但用户没选它
    C.write_choice("connection", "sub:claude")  # 订阅项不在清单里
    assert R._available_connection() is None
    st = R.understanding_state()
    assert st["state"] == "unavailable" and st["mode"] == "connection" and st["connection"] is None
    assert st["chosen"] == "sub:claude"  # 界面靠它指认「你选的是哪一个」
    C.write_choice("auto")
    assert "chosen" not in R.understanding_state()
    C.write_choice("off")
    assert "chosen" not in R.understanding_state()


def test_state_object(two_keys, with_sub):
    st = R.understanding_state()
    assert st["state"] == "on" and st["mode"] == "auto" and st["connection"] == "a"
    assert [o["id"] for o in st["options"]] == ["sub:codex", "a", "b"]
    C.write_choice("off")
    st = R.understanding_state()
    assert st["state"] == "off" and st["connection"] is None and len(st["options"]) == 3
    C.write_choice("connection", "b")
    assert R.understanding_state()["connection"] == "b"


def test_state_none_without_any_connection(monkeypatch):
    monkeypatch.setattr(ai_bridge, "capabilities_if_cached", lambda: None)
    st = R.understanding_state()
    assert st == {"state": "none", "mode": "auto", "connection": None, "options": []}


# =========================================================================== 数据发给谁
def test_chosen_key_connection_is_the_only_one_that_receives_requests(tmp_path, two_keys):
    # 对照：auto 挑 a
    auto_fake = D.FakeTransport([])
    go(tmp_path / "x1", post=auto_fake)
    assert set(hosts(auto_fake)) == {"gw-a.invalid"}
    R.reset_for_tests()
    C.write_choice("connection", "b")
    fake = D.FakeTransport([])
    go(tmp_path / "x2", post=fake)
    assert fake.calls and set(hosts(fake)) == {"gw-b.invalid"}


def test_chosen_connection_unavailable_sends_nothing(tmp_path, two_keys):
    C.write_choice("connection", "b")
    ai_providers.delete("b")
    fake = D.FakeTransport([])
    assert go(tmp_path, post=fake) is None
    assert fake.calls == []


def test_off_sends_nothing_from_either_entry_point(tmp_path, two_keys, with_sub, monkeypatch):
    C.write_choice("off")
    fake = D.FakeTransport([])
    assert go(tmp_path, post=fake) is None  # ai_data_roles
    prof = adf.profile_folder(tmp_path / "tbl")
    out = M.propose_recipes(prof, post=fake)  # ai_data_model
    assert out.call.ok is False and out.call.reason == "no_connection" and out.recipes == ()
    boom = lambda **k: (_ for _ in ()).throw(AssertionError("outbound call"))  # noqa: E731
    monkeypatch.setattr(ai_structured, "call_structured", boom)
    assert go(tmp_path, post=fake) is None
    assert M.propose_recipes(prof).call.reason == "no_connection"
    assert fake.calls == []


def test_model_entry_uses_the_chosen_connection(tmp_path, two_keys):
    go(tmp_path, post=D.FakeTransport([]))  # 建好 tbl
    prof = adf.profile_folder(tmp_path / "tbl")
    C.write_choice("connection", "b")
    fake = D.FakeTransport([])
    M.propose_recipes(prof, post=fake)
    assert fake.calls and set(hosts(fake)) == {"gw-b.invalid"}
    C.write_choice("connection", "zzz")
    fake2 = D.FakeTransport([])
    assert M.propose_recipes(prof, post=fake2).call.reason == "no_connection" and fake2.calls == []


def test_off_does_not_read_a_cached_judgement(tmp_path, two_keys):
    entry, values = table(tmp_path)
    key = R.cache_key(
        data_sha256=values.sha256,
        header_line=values.header_line,
        hint=None,
        identity=R._identity(R._available_connection()),
        table=f"{entry.path}|{entry.sheet}",
    )
    assert key
    C.write_choice("off")
    assert (
        go(tmp_path, post=D.FakeTransport([])) is None
    )  # 缓存里有没有都不会被用：`start` 在查缓存之前就返回


def test_auto_keeps_the_arguments_passed_to_call_structured(tmp_path, two_keys):
    """`auto` 逐字不变：Key 连接不显式传 `conn`（`call_structured` 自己挑 / 刷新令牌）；CLI 连接传。"""
    seen: list[dict] = []
    orig = ai_structured.call_structured

    def spy(**kw):
        seen.append(kw)
        return orig(**kw)

    ai_structured.call_structured = spy
    try:
        go(tmp_path, post=D.FakeTransport([]))
        C.write_choice("connection", "b")
        R.reset_for_tests()
        go(tmp_path / "again", post=D.FakeTransport([]))
    finally:
        ai_structured.call_structured = orig
    auto_kw, pinned_kw = seen[0], seen[-1]
    assert "conn" not in auto_kw and auto_kw["auto_small"] is True
    assert pinned_kw["conn"]["provider_id"] == "b" and pinned_kw["auto_small"] is True
    assert set(auto_kw) | {"conn"} == set(pinned_kw) | {"conn"}


# =========================================================================== 缓存 / 单飞 / 负缓存按连接隔离
def test_cache_key_and_flight_differ_per_connection(tmp_path, two_keys):
    entry, values = table(tmp_path)

    def key_for(cid):
        C.write_choice("connection", cid)
        return R.cache_key(
            data_sha256=values.sha256,
            header_line=values.header_line,
            hint=None,
            identity=R._identity(R._available_connection()),
            table=f"{entry.path}|{entry.sheet}",
        )

    assert key_for("a") != key_for("b")


def test_negative_cache_of_one_connection_does_not_block_another(tmp_path, two_keys):
    C.write_choice("connection", "a")
    first = go(tmp_path, post=D.FakeTransport([]))  # 假传输脚本用完 = 失败 → 记负缓存
    assert first["state"] == "failed"
    assert go(tmp_path, post=D.FakeTransport([])) is None  # 同连接：被负缓存挡住
    C.write_choice("connection", "b")
    fake = D.FakeTransport([])
    again = go(tmp_path, post=fake)
    assert again is not None and set(hosts(fake)) == {"gw-b.invalid"}  # 另一个连接不受影响


def test_switching_clears_only_jobs_of_other_identities(tmp_path, two_keys):
    C.write_choice("connection", "a")
    ja = go(tmp_path, post=D.FakeTransport([]))
    assert R.get("p", ja["id"]) is not None
    st = R.apply_choice("connection", "a")  # 选同一个：保留
    assert st["connection"] == "a" and R.get("p", ja["id"]) is not None
    R.apply_choice("connection", "b")
    assert R.get("p", ja["id"]) is None  # 旧连接的任务不再被轮询到（回 off）
    R.apply_choice("off")
    assert not R._JOBS and not R._BY_KEY


def test_apply_choice_rejects_what_is_not_offered(two_keys):
    assert R.apply_choice("connection", "nope") is None
    assert R.apply_choice("connection", None) is None
    assert R.apply_choice("weird") is None
    assert C.read_choice() == ("auto", None)  # 什么都没写
    st = R.apply_choice("off")
    assert st["state"] == "off" and C.read_choice() == ("off", None)


# =========================================================================== 接口
def test_endpoints_are_behind_the_experiment_switch(project, real_settings):
    _m, client, _figs, state = project
    state["taf"] = False
    assert client.get("/api/ai/data-understanding").status_code == 404
    r = client.post("/api/ai/data-understanding", json={"mode": "off"})
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"


def test_get_and_post_round_trip(project, real_settings, two_keys, with_sub):
    _m, client, _figs, _state = project
    body = client.get("/api/ai/data-understanding").get_json()["understanding"]
    assert body["state"] == "on" and body["mode"] == "auto" and body["connection"] == "a"
    r = client.post("/api/ai/data-understanding", json={"mode": "connection", "connection": "b"})
    assert r.status_code == 200
    st = r.get_json()["understanding"]
    assert st["mode"] == "connection" and st["connection"] == "b" and st["state"] == "on"
    blob = json.dumps(r.get_json())
    assert SECRET_A not in blob and SECRET_B not in blob and "gw-" not in blob
    assert client.get("/api/ai/data-understanding").get_json()["understanding"]["connection"] == "b"
    r = client.post("/api/ai/data-understanding", json={"mode": "off"})
    assert r.get_json()["understanding"]["state"] == "off"
    r = client.post("/api/ai/data-understanding", json={"mode": "auto"})
    assert r.get_json()["understanding"]["mode"] == "auto"


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"mode": "weird"},
        {"mode": "off", "connection": "a"},
        {"mode": "auto", "connection": "a"},
        {"mode": "connection"},
        {"mode": "connection", "connection": 3},
        {"mode": "connection", "connection": ""},
        {"mode": "connection", "connection": "x" * 300},
        {"mode": "off", "api_key": "sk-evil"},
    ],
)
def test_post_rejects_malformed_bodies_and_echoes_nothing(project, real_settings, two_keys, body):
    _m, client, _figs, _state = project
    r = client.post("/api/ai/data-understanding", json=body)
    assert r.status_code == 400 and r.get_json()["code"] == "propose_input_invalid"
    assert "sk-evil" not in r.get_data(as_text=True)
    assert C.read_choice() == ("auto", None)


def test_post_unknown_connection_is_a_closed_set_error_and_writes_nothing(
    project, real_settings, two_keys
):
    _m, client, _figs, _state = project
    r = client.post("/api/ai/data-understanding", json={"mode": "connection", "connection": "nope"})
    assert r.status_code == 400 and r.get_json()["code"] == "understanding_connection_unavailable"
    assert C.read_choice() == ("auto", None)


def test_data_files_carries_the_understanding_object(project, real_settings, two_keys):
    _m, client, figs, _state = project
    (figs / "d.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    body = client.get("/api/ai/data-files?formats=all").get_json()
    assert body["understanding_available"] is True
    assert body["understanding"]["state"] == "on" and body["understanding"]["connection"] == "a"
    C.write_choice("off")
    body = client.get("/api/ai/data-files?formats=all").get_json()
    assert body["understanding_available"] is False and body["understanding"]["state"] == "off"
    C.write_choice("connection", "zzz")
    body = client.get("/api/ai/data-files?formats=all").get_json()
    assert (
        body["understanding_available"] is False and body["understanding"]["state"] == "unavailable"
    )


def test_propose_has_no_understanding_key_when_off_or_unavailable(project, real_settings, two_keys):
    _m, client, figs, _state = project
    put(
        figs, "data/g.csv", [["group", "value_mV"]] + [[g, 1.0 + i] for i, g in enumerate("aabbcc")]
    )
    for mode, cid in (("off", None), ("connection", "zzz")):
        C.write_choice(mode, cid)
        R.reset_for_tests()
        out = propose(client, render="lazy").get_json()
        assert out["kind"] == "candidates" and "understanding" not in out
