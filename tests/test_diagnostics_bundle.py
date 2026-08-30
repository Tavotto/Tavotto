"""诊断包 schema 2：前端状态、交互轨迹、manifest 与**服务端第二道校验**。

对应 ADR 0016。本文件覆盖新增的那一层，并守住兼容：老的
`GET /api/diagnostics/bundle` 出的三个文件一个字节都不许变味。

最后一节是**端到端隐私回归**：把几个醒目的秘密串塞满前端载荷与用户配置，
生成真 zip，对包里**每一个文件**全文搜索，断言 0 次出现。这一条不是锦上添花
——它是「allowlist 真的成立」的唯一可验证判据。
"""

import json
import os
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from support import frontend_schema
from tavotto import app as m
from tavotto.engine import diagnostics as engine_diagnostics, diagnostics_frontend as dfe

REPO_ROOT = Path(__file__).resolve().parents[1]

# 这些串必须在诊断包里出现 0 次
SECRET_TITLE = "SUPER_SECRET_PAPER_TITLE_12345"
SECRET_KEY = "SUPER_SECRET_API_KEY_67890"

#: 前端载荷里用的**假**主目录。它走的是新增那条防线（值必须是短技术标识，
#: 带斜杠的一律出局），与这台机器的真实主目录无关。
FAKE_HOME = "/Users/private-user-name/"

#: 既有的 `_redact_text` 抹的是**这台机器实际的**主目录与用户名——它没法也
#: 不该去猜一个虚构的路径。所以验「主目录脱敏」必须拿真的那个来验，
#: 拿假路径验只会验出一条永远为假的结论（而且看起来像通过了）。
REAL_HOME = os.path.expanduser("~")


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()


def _redact(text):
    return engine_diagnostics._redact_text(text)


def event(seq=1, **extra):
    ev = {
        "seq": seq,
        "ts": 1_756_000_000_000 + seq,
        "t_ms": seq * 10,
        "type": "align.blocked",
        "mode": "left",
        "panel": "panel:aaaaaaaaaaaa",
        "reason": "authority_stale",
        "document_variant": "var:111111111111",
        "display_variant": "var:222222222222",
        "authority_variant": "var:222222222222",
    }
    ev.update(extra)
    return ev


def snapshot(**extra):
    snap = {
        "schema_version": 1,
        "session_ms": 12345,
        "document": {
            "document_hash": "doc:aaaaaaaaaaaa",
            "object_count": 6,
            "panel_count": 3,
            "canvas_count": 1,
            "history": {"past": 17, "future": 0, "txn_open": False, "txn_label_key": None},
        },
        "selection": {
            "active_panel": "panel:aaaaaaaaaaaa",
            "selection_kind": "element",
            "element_count": 2,
            "element_gids": ["axes_0.title", "axes_0.xaxis.label"],
            "object_count": 0,
        },
        "preview": {"active_sessions": 0, "settled": None, "history_mode": "gesture"},
        "preview_memory": {
            "resident_svg_bytes": 12_582_912,
            "resident_svg_count": 2,
            "vector_panel_count": 1,
            "hybrid_panel_count": 1,
            "raster_panel_count": 0,
            "evicted_panel_count": 0,
            "budget_per_file": 16 * 1024 * 1024,
            "budget_global": 64 * 1024 * 1024,
        },
        "panels": [
            {
                "panel": "panel:aaaaaaaaaaaa",
                "file": "file:bbbbbbbbbbbb",
                "kind": "matplotlib",
                "override_count": 7,
                "document_variant": "var:111111111111",
                "display_variant": "var:222222222222",
                "authority_variant": None,
                "display_exact": False,
                "exact_manifest_available": False,
                "render_status": "rendering",
                "stale": False,
                "element_count": 12,
                "preview_mode": "hybrid",
                "preview_reason": "complexity_budget",
                "preview_svg_bytes": 1_838_682,
                "estimated_primitives": 662_702,
                "estimated_nodes": 9,
                "rasterized_artist_count": 3,
                "svg_resident": True,
                "svg_evicted": False,
            }
        ],
    }
    snap.update(extra)
    return snap


def open_bundle(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(data))


# ---------------------------------------------------------------------------
# 兼容：老的 GET 一个字节都不许变味
# ---------------------------------------------------------------------------
def test_get_bundle_still_works_and_keeps_the_old_three_files(client):
    res = client.get("/api/diagnostics/bundle")
    assert res.status_code == 200
    assert res.mimetype == "application/zip"
    z = open_bundle(res.data)
    names = set(z.namelist())
    assert {"report.json", "app.log", "README.txt"} <= names
    # 老端点拿不到前端状态：前端状态只活在浏览器内存里
    assert "frontend-state.json" not in names
    assert "interaction-trace.jsonl" not in names
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["contains_frontend_state"] is False
    assert manifest["contains_interaction_trace"] is False
    assert manifest["trace_event_count"] == 0


def test_manifest_declares_its_own_schema(client):
    z = open_bundle(client.get("/api/diagnostics/bundle").data)
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["schema_version"] == 2
    assert manifest["frontend_snapshot_schema"] == 1
    assert manifest["trace_schema"] == 1
    assert manifest["privacy_mode"] == "safe-default"
    # created_at 是带时区的 ISO 串——读包的人要能判断这是什么时候的
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$", manifest["created_at"])


def test_report_and_config_still_redact_home_and_secrets(client, tmp_path, monkeypatch):
    from tavotto.engine import config as engine_config

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps({"api_key": SECRET_KEY, "backup_dir": os.path.join(REAL_HOME, "backups")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_config, "config_path", lambda: cfg)
    z = open_bundle(client.get("/api/diagnostics/bundle").data)
    body = z.read("config.json").decode()
    assert SECRET_KEY not in body
    assert "***" in body
    # 主目录换成了 ~
    assert REAL_HOME not in body
    assert "~" in body


# ---------------------------------------------------------------------------
# POST：带上前端状态与交互轨迹
# ---------------------------------------------------------------------------
def test_post_bundle_includes_frontend_state_and_trace(client):
    payload = {"frontend_state": snapshot(), "interaction_trace": [event(1), event(2), event(3)]}
    res = client.post("/api/diagnostics/bundle", json=payload)
    assert res.status_code == 200
    z = open_bundle(res.data)
    names = set(z.namelist())
    assert {
        "report.json",
        "app.log",
        "README.txt",
        "frontend-state.json",
        "interaction-trace.jsonl",
        "manifest.json",
    } <= names

    manifest = json.loads(z.read("manifest.json"))
    assert manifest["contains_frontend_state"] is True
    assert manifest["contains_interaction_trace"] is True
    assert manifest["trace_event_count"] == 3
    assert manifest["trace_truncated"] is False

    state = json.loads(z.read("frontend-state.json"))
    assert state["document"]["history"]["past"] == 17
    assert state["panels"][0]["exact_manifest_available"] is False
    assert state["selection"]["element_gids"] == ["axes_0.title", "axes_0.xaxis.label"]


def test_trace_is_jsonl_one_valid_json_per_line(client):
    payload = {"frontend_state": snapshot(), "interaction_trace": [event(i) for i in range(1, 6)]}
    z = open_bundle(client.post("/api/diagnostics/bundle", json=payload).data)
    text = z.read("interaction-trace.jsonl").decode()
    lines = text.strip().split("\n")
    assert len(lines) == 5
    # **每一行都必须是单独合法的 JSON**：坏了一行其余照样能读
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [1, 2, 3, 4, 5]


def test_empty_frontend_payload_still_produces_a_bundle(client):
    res = client.post("/api/diagnostics/bundle", json={})
    assert res.status_code == 200
    z = open_bundle(res.data)
    assert "report.json" in z.namelist()
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["contains_frontend_state"] is False


def test_invalid_json_body_degrades_instead_of_failing(client):
    # 用户是来排障的，不该因为载荷畸形而两手空空
    res = client.post(
        "/api/diagnostics/bundle", data="}{ not json", content_type="application/json"
    )
    assert res.status_code == 200
    z = open_bundle(res.data)
    assert "report.json" in z.namelist()
    assert json.loads(z.read("manifest.json"))["contains_frontend_state"] is False


def test_too_many_events_are_truncated_to_the_most_recent(client):
    payload = {
        "frontend_state": snapshot(),
        "interaction_trace": [event(i) for i in range(1, 1001)],
    }
    z = open_bundle(client.post("/api/diagnostics/bundle", json=payload).data)
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["trace_event_count"] == dfe.MAX_EVENTS
    assert manifest["trace_truncated"] is True
    lines = z.read("interaction-trace.jsonl").decode().strip().split("\n")
    # 留下的是**最近的**那些：事故在末尾，开头那些离得最远
    assert json.loads(lines[-1])["seq"] == 1000
    assert json.loads(lines[0])["seq"] == 1000 - dfe.MAX_EVENTS + 1


def test_chunked_body_without_content_length_still_respects_the_limit(client):
    """评审 #139 的 P2：chunked transfer encoding 不带 Content-Length，
    按 0 处理就等于把 512 KB 的硬上限让开了。上限必须卡在读取本身。"""
    big = json.dumps(
        {
            "frontend_state": snapshot(),
            "interaction_trace": [event(i, mode="a" * 8) for i in range(60_000)],
        }
    ).encode()
    assert len(big) > dfe.MAX_REQUEST_BYTES
    # wsgi.input_terminated + 无 CONTENT_LENGTH = 服务器眼里的 chunked 请求：
    # 流是可读的，但没人告诉你它有多长
    res = client.post(
        "/api/diagnostics/bundle",
        data=BytesIO(big),
        content_type="application/json",
        environ_overrides={"wsgi.input_terminated": True, "CONTENT_LENGTH": None},
    )
    assert res.status_code == 200
    manifest = json.loads(open_bundle(res.data).read("manifest.json"))
    assert manifest["contains_interaction_trace"] is False
    assert manifest["trace_truncated"] is True


def test_chunked_body_under_the_limit_is_accepted_in_full(client):
    """上一条的**判别性**在这里：只断言「超大的被拒」是抓不住 bug 的——
    把上限错挂回 content_length（chunked 时是 None）会只读 1 个字节，
    结果同样是「没有 trace + truncated」，那条用例照样绿。

    真正能分辨两种实现的是**合法的 chunked 请求必须被完整收下**：
    错误实现读不到东西，正确实现读得到全部 3 条。
    """
    body = json.dumps(
        {"frontend_state": snapshot(), "interaction_trace": [event(1), event(2), event(3)]}
    ).encode()
    assert len(body) < dfe.MAX_REQUEST_BYTES
    res = client.post(
        "/api/diagnostics/bundle",
        data=BytesIO(body),
        content_type="application/json",
        environ_overrides={"wsgi.input_terminated": True, "CONTENT_LENGTH": None},
    )
    assert res.status_code == 200
    manifest = json.loads(open_bundle(res.data).read("manifest.json"))
    assert manifest["contains_interaction_trace"] is True
    assert manifest["trace_event_count"] == 3
    assert manifest["trace_truncated"] is False


def test_oversize_request_is_dropped_but_the_bundle_still_comes_out(client):
    big = "a" * 8
    payload = {
        "frontend_state": snapshot(),
        "interaction_trace": [event(i, mode=big) for i in range(60_000)],
    }
    res = client.post("/api/diagnostics/bundle", json=payload)
    assert res.status_code == 200
    z = open_bundle(res.data)
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["contains_interaction_trace"] is False
    assert manifest["trace_truncated"] is True


# ---------------------------------------------------------------------------
# 服务端第二道校验：值的形状
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "Experimental results for Fig. 3",  # 有空格的自由文本
        "/Users/private-user-name/paper.py",  # 绝对路径
        "实验结果对比",  # 非 ASCII 的图内文字
        "a" * 200,  # 超长串
    ],
)
def test_free_text_never_survives_the_server_side_check(bad):
    ev = dfe.sanitize_event(event(1, mode=bad), _redact)
    # 整条丢弃，不做部分保留
    assert ev is None


def test_api_key_shaped_value_is_rejected_by_the_redactor():
    # 字符集过得了 _TOKEN_RE，但 _redact_text 会改写它 → 整条丢弃
    ev = dfe.sanitize_event(event(1, mode="sk-live-abcdefgh12345678"), _redact)
    assert ev is None


def test_a_field_legal_on_one_event_is_illegal_on_another():
    """评审 #139 的 P1：`code` 是 render.error 的合法字段，扁平的名字集合于是
    允许 `{"type": "diagnostics.export", "code": "SUPER_SECRET_…"}` 溜过去。

    逐事件的表把它挡在外面。"""
    leaked = dfe.sanitize_event(
        {
            "seq": 1,
            "ts": 1_756_000_000_000,
            "t_ms": 5,
            "type": "diagnostics.export",
            "code": SECRET_TITLE,
        },
        _redact,
    )
    assert leaked is None
    # 同一个字段在它自己的事件上照常通过
    ok = dfe.sanitize_event(
        {
            "seq": 1,
            "ts": 1_756_000_000_000,
            "t_ms": 5,
            "type": "render.error",
            "code": "missing_dependency",
            "file": "file:aaaaaaaaaaaa",
            "variant": "var:bbbbbbbbbbbb",
            "duration_ms": 12,
        },
        _redact,
    )
    assert ok["code"] == "missing_dependency"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("selection_kind", SECRET_TITLE),
        ("history_mode", SECRET_TITLE),
        ("render_status", SECRET_TITLE),
        ("kind", SECRET_TITLE),
        ("preview_mode", SECRET_TITLE),
        ("preview_reason", SECRET_TITLE),
    ],
)
def test_closed_set_fields_reject_content_shaped_tokens(field, bad):
    """评审 #139 的 P1 之二：闭集字段以前走的是通用 token 判据，
    `selection_kind: "SUPER_SECRET_PAPER_TITLE_12345"` 字符集完全合法。"""
    snap = snapshot()
    snap["selection"]["selection_kind"] = bad if field == "selection_kind" else "element"
    snap["preview"]["history_mode"] = bad if field == "history_mode" else "gesture"
    snap["panels"][0]["render_status"] = bad if field == "render_status" else "ready"
    snap["panels"][0]["kind"] = bad if field == "kind" else "matplotlib"
    snap["panels"][0]["preview_mode"] = bad if field == "preview_mode" else "hybrid"
    snap["panels"][0]["preview_reason"] = bad if field == "preview_reason" else "normal"
    out = dfe.sanitize_snapshot(snap, _redact)
    assert SECRET_TITLE not in json.dumps(out, ensure_ascii=False)


def test_preview_representation_survives_the_backend_sanitiser():
    """**「不要只改 TypeScript interface」**（Session 05 §2）。

    后端按 `_SNAPSHOT_SHAPE` / `_PANEL_SHAPE` **拉取**，不遍历输入的键——前端
    加了字段而这两张表没加，字段会被**静默丢掉**，而没有任何地方会报错。
    读包的人看到的是「这个面板没有 preview_mode」，一个指向完全错误方向的线索。
    """
    out = dfe.sanitize_snapshot(snapshot(), _redact)
    panel = out["panels"][0]
    assert panel["preview_mode"] == "hybrid"
    assert panel["preview_reason"] == "complexity_budget"
    assert panel["preview_svg_bytes"] == 1_838_682
    assert panel["estimated_primitives"] == 662_702
    assert panel["estimated_nodes"] == 9
    assert panel["rasterized_artist_count"] == 3
    assert panel["svg_resident"] is True
    assert panel["svg_evicted"] is False

    mem = out["preview_memory"]
    assert mem["resident_svg_bytes"] == 12_582_912
    assert mem["resident_svg_count"] == 2
    assert mem["vector_panel_count"] == 1
    assert mem["hybrid_panel_count"] == 1
    assert mem["budget_per_file"] == 16 * 1024 * 1024


def test_never_estimated_stays_none_instead_of_becoming_zero():
    """**`None` 与 `0` 在这里不是一回事**。

    老后端（没有分析器那一版）不返回 `estimated_*`，前端如实报 `null`。
    `int` 那条 spec 会把它压成 0，于是「没估过」被读成「估出来是零个」
    ——正好是最误导人的那个方向，所以这两个字段走 `int_or_none`。
    """
    snap = snapshot()
    snap["panels"][0]["estimated_primitives"] = None
    snap["panels"][0]["estimated_nodes"] = None
    panel = dfe.sanitize_snapshot(snap, _redact)["panels"][0]
    assert panel["estimated_primitives"] is None
    assert panel["estimated_nodes"] is None
    # 真的是 0 的时候照样报 0——两者必须区分得开
    snap["panels"][0]["estimated_primitives"] = 0
    assert dfe.sanitize_snapshot(snap, _redact)["panels"][0]["estimated_primitives"] == 0


def test_preview_enums_come_from_previewbudget_not_a_second_list():
    """枚举值的唯一出处是 `previewbudget`。

    手写第二份的话，哪天加一档表示法，诊断会把它整条 reject 成 None
    ——而 `previewbudget.MODES` 里已经有它了。
    """
    from tavotto.engine import previewbudget

    assert dfe._ENUM_FIELDS["preview_mode"] == frozenset(previewbudget.MODES)
    assert dfe._ENUM_FIELDS["preview_reason"] == frozenset(previewbudget.REASONS)


def test_unknown_event_type_is_dropped():
    assert dfe.sanitize_event(event(1, type="totally.made.up"), _redact) is None


def test_unknown_container_shapes_are_dropped():
    ev = dfe.sanitize_event(event(1, extra={"unexpected": "shape"}), _redact)
    assert ev is None


def test_patch_and_geometry_shapes_survive():
    ev = dfe.sanitize_event(
        {
            "seq": 1,
            "ts": 1_756_000_000_000,
            "t_ms": 5,
            "type": "document.commit",
            "patches": [
                {"gid": "axes_0.title", "prop": "pos_frac"},
                {"domain": "panel_override", "prop": "fontsize"},
            ],
        },
        _redact,
    )
    assert ev["patches"] == [
        {"gid": "axes_0.title", "prop": "pos_frac"},
        {"domain": "panel_override", "prop": "fontsize"},
    ]

    # input_geometry 只属于 align.request / align.commit——**逐事件的表**说了算，
    # 放在 align.blocked 上会被整条丢掉（这正是它该有的行为）
    ev = dfe.sanitize_event(
        {
            "seq": 1,
            "ts": 1_756_000_000_000,
            "t_ms": 5,
            "type": "align.request",
            "mode": "left",
            "panel": "panel:aaaaaaaaaaaa",
            "selected_count": 2,
            "document_variant": "var:111111111111",
            "display_variant": None,
            "authority_variant": None,
            "exact_authority": False,
            "input_geometry": [
                {"gid": "axes_0.title", "bbox": [0.31, 0.12, 0.18, 0.04], "anchor": [0.40, 0.15]}
            ],
        },
        _redact,
    )
    assert ev["input_geometry"][0]["bbox"] == [0.31, 0.12, 0.18, 0.04]
    assert dfe.sanitize_event(event(1, input_geometry=[{"gid": "axes_0"}]), _redact) is None


def test_patch_value_is_rejected_outright():
    # `value` 不在 _PATCH_KEYS 里——就算前端哪天写错了，这里也不放行
    ev = dfe.sanitize_event(
        event(1, patches=[{"gid": "axes_0.title", "prop": "text", "value": SECRET_TITLE}]), _redact
    )
    assert ev is None


def test_nan_and_inf_geometry_are_rejected():
    ev = dfe.sanitize_event(
        event(1, input_geometry=[{"gid": "axes_0", "bbox": [float("nan"), 0.1, 0.2, 0.3]}]), _redact
    )
    assert ev is None


def test_deeply_nested_payload_is_rejected():
    nested = {"gid": "a"}
    for _ in range(20):
        nested = {"gid": [nested]}
    assert dfe.sanitize_event(event(1, input_geometry=[nested]), _redact) is None


def test_snapshot_falls_back_instead_of_propagating_bad_values():
    bad = snapshot()
    bad["document"]["history"]["txn_label_key"] = "an entire sentence with spaces"
    bad["selection"]["element_gids"] = ["axes_0.title", SECRET_TITLE + " and more"]
    out = dfe.sanitize_snapshot(bad, _redact)
    assert out["document"]["history"]["txn_label_key"] is None
    assert SECRET_TITLE not in json.dumps(out, ensure_ascii=False)


def test_snapshot_of_a_non_dict_is_none():
    assert dfe.sanitize_snapshot(["nope"], _redact) is None


# ---------------------------------------------------------------------------
# 严格同源对：后端认识的事件类型 == 前端可辨识联合里的那些
# ---------------------------------------------------------------------------
def test_event_fields_match_frontend_schema():
    """**逐事件**比对：后端每种事件允许的字段名 == 前端 EVENT_SCHEMA 里那些。

    这是根 AGENTS.md 的严格同源对。后端确实复制了一份逐事件的表——评审指出
    扁平的名字集合不够（`code` 是 render.error 的合法字段，扁平表于是允许
    `{"type": "diagnostics.export", "code": "SUPER_SECRET_…"}`），而
    「后端是独立的结构性隐私边界」这句话必须有代码兑现。复制的代价用这条
    用例对冲：两边任何一个事件少一个字段，它先红。

    读 TS 用的是**大括号配对**而不是正则：正则会把相邻条目串到一起
    （实测 36 个事件只认出 20 个，还互相串味），那种解析出来的「一致」
    是假的。
    """
    table = frontend_schema.extract(str(REPO_ROOT / "web/src/diagnostics/sanitize.ts"))
    assert len(table) >= 30, f"只解析出 {len(table)} 个事件，解析器该跟着 TS 的写法更新"
    assert set(table) == set(dfe.EVENT_FIELDS), "事件类型集合两边不一致"
    for name, fields in sorted(table.items()):
        assert set(fields) == set(dfe.EVENT_FIELDS[name]), name


# ---------------------------------------------------------------------------
# K：端到端隐私回归
# ---------------------------------------------------------------------------
def test_no_secret_string_appears_anywhere_in_the_bundle(client, tmp_path, monkeypatch):
    """把秘密串塞满每一个入口，然后对 zip 里**每个文件**全文搜索。

    这条用例是 allowlist 成立与否的唯一可验证判据。它必须存在，而且必须搜
    **全部文件**——只查 frontend-state.json 的话，README、manifest、report
    任何一处漏出去都抓不到。
    """
    from tavotto.engine import config as engine_config

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "api_key": SECRET_KEY,
                "backup_dir": os.path.join(REAL_HOME, "backups"),
                # 用户的项目清单：每条都带项目名与路径
                "recent_projects": [
                    {
                        "path": os.path.join(REAL_HOME, SECRET_TITLE),
                        "name": SECRET_TITLE,
                        "last_opened": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_config, "config_path", lambda: cfg)

    hostile_events = [
        # ① 顶层字段直接塞
        event(1, mode=SECRET_TITLE),
        # ② 嵌套进几何目标
        event(2, input_geometry=[{"gid": SECRET_TITLE, "bbox": [0.1, 0.2, 0.3, 0.4]}]),
        # ③ 嵌套进 patch 身份，连 value 一起
        {
            "seq": 3,
            "ts": 1_756_000_000_003,
            "t_ms": 30,
            "type": "document.commit",
            "patches": [{"gid": "axes_0.title", "prop": "text", "value": SECRET_TITLE}],
        },
        # ④ 未登记的字段名
        event(4, secret_unexpected_field=SECRET_KEY),
        # ⑤ 路径
        event(5, mode=FAKE_HOME + "fig.py"),
        # ⑥ 一条完全正常的事件，用来证明这次导出确实产出了内容
        event(6),
    ]
    hostile_state = snapshot()
    hostile_state["document"]["document_hash"] = SECRET_TITLE
    hostile_state["selection"]["element_gids"] = ["axes_0.title", SECRET_KEY]
    hostile_state["panels"][0]["file"] = FAKE_HOME + "Fig1.pdf"
    hostile_state["extra_unknown_block"] = {"paper": SECRET_TITLE}

    res = client.post(
        "/api/diagnostics/bundle",
        json={
            "frontend_state": hostile_state,
            "interaction_trace": hostile_events,
        },
    )
    assert res.status_code == 200

    z = open_bundle(res.data)
    for name in z.namelist():
        body = z.read(name).decode("utf-8", errors="replace")
        for secret in (SECRET_TITLE, SECRET_KEY, "private-user-name", REAL_HOME):
            assert secret not in body, f"{secret} 出现在 {name} 里"

    # 反证：这次导出**确实**产出了内容，不是因为整个包是空的才搜不到
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["contains_interaction_trace"] is True
    assert manifest["trace_event_count"] >= 1
    assert manifest["trace_truncated"] is True  # 恶意条目被丢掉了，如实上报


def test_readme_says_what_is_and_is_not_included(client):
    payload = {"frontend_state": snapshot(), "interaction_trace": [event(1)]}
    z = open_bundle(client.post("/api/diagnostics/bundle", json=payload).data)
    readme = z.read("README.txt").decode()
    # 双语
    assert "包含" in readme and "This package contains" in readme
    assert "不包含" in readme and "does NOT intentionally contain" in readme
    # 新增的两个文件在「包含」里被点名
    assert "frontend-state.json" in readme
    assert "interaction-trace.jsonl" in readme
    # 承诺项
    for promise in ("Python", "API", "SVG"):
        assert promise in readme


def test_readme_does_not_promise_files_that_are_not_there(client):
    z = open_bundle(client.get("/api/diagnostics/bundle").data)
    readme = z.read("README.txt").decode()
    assert "interaction-trace.jsonl" not in readme


def test_recent_project_inventory_is_reduced_to_a_count(client, tmp_path, monkeypatch):
    """「用户还有哪些项目」是一份目录清单：项目名与路径逐条列着。

    排障一次都用不到它——要看的是**当前**这个项目，而那个在 report.json 的
    project 段里。清单本身留在用户机器上。
    """
    from tavotto.engine import config as engine_config

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "recent_projects": [
                    {
                        "path": os.path.join(REAL_HOME, "paper-a"),
                        "name": SECRET_TITLE,
                        "last_opened": 1,
                    },
                    {
                        "path": os.path.join(REAL_HOME, "paper-b"),
                        "name": "另一个课题",
                        "last_opened": 2,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_config, "config_path", lambda: cfg)

    z = open_bundle(client.get("/api/diagnostics/bundle").data)
    body = json.loads(z.read("config.json"))
    assert body["recent_projects"] == {"count": 2}
    raw = z.read("config.json").decode()
    assert SECRET_TITLE not in raw
    assert "另一个课题" not in raw
    assert "paper-a" not in raw
