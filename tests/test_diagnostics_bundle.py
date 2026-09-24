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
from support.tsconst import exported_number
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
    assert manifest["schema_version"] == 3
    assert manifest["frontend_snapshot_schema"] == 1
    assert manifest["trace_schema"] == 1
    assert manifest["privacy_mode"] == "safe-default"
    # created_at 是带时区的 ISO 串——读包的人要能判断这是什么时候的
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$", manifest["created_at"])


def test_bundle_schema_is_one_number_on_both_sides():
    """`diagnostics.BUNDLE_SCHEMA_VERSION` ↔ `web/src/diagnostics/types.ts` 的同名常量。

    report.json 换形就得升这个号（#524 评审）；只升一侧的话，前端自报的与包里 manifest 写的
    就不是同一个格式。"""
    # 结构性地读（`tests/support/tsconst.py`）：注释掉的 `// export const … = 3` 不算数（#536 评审）
    src = Path(__file__).resolve().parents[1] / "web" / "src" / "diagnostics" / "types.ts"
    ts = exported_number(src.read_text(encoding="utf-8"), "BUNDLE_SCHEMA_VERSION")
    assert ts == engine_diagnostics.BUNDLE_SCHEMA_VERSION


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


# ---------------------------------------------------------------------------
# 项目路径（2026-09-23 beta 诊断包实测）：云盘目录名带邮箱、课题目录带人名，
# 以前只把主目录换成 `~`，其余原样进 report.json / app.log / 复制诊断
# ---------------------------------------------------------------------------

PERSON = "PRIVATE_PERSON_NAME"
PAPER = "SECRET_PAPER_DIR"
EMAIL = "private.person@example.com"
OTHER_EMAIL = "other.person@example.org"
IDN_EMAIL = "张三丰@例子.公司"
PUNY_EMAIL = "zhang.san@mail.example.xn--p1ai"
OTHER_TITLE = "OTHER_RECENT_TITLE"
EXPORT_TITLE = "SECRET_EXPORT_TITLE"


def test_project_paths_names_and_cloud_accounts_never_leave_the_machine(
    client, tmp_path, monkeypatch
):
    """当前项目在云盘里（目录名带邮箱与人名）、另有一个最近项目、导出目录被改到桌面上的题目目录、
    日志里提到这些路径——包里**每个文件**与「复制诊断」的文本里一个都不许出现；同时反证包里确实
    说了「项目在云盘里、路径有非 ASCII」，并且日志与 report 用的是同一个项目记号。"""
    from tavotto.engine import config as engine_config

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cloud = home / "Library" / "CloudStorage" / f"坚果云-{EMAIL}"
    project = cloud / "论文" / PERSON / PAPER
    project.mkdir(parents=True)
    other = home / "Library" / "CloudStorage" / f"GoogleDrive-{OTHER_EMAIL}" / OTHER_TITLE
    export_dir = home / "Desktop" / EXPORT_TITLE

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {"recent_projects": [{"path": str(other), "name": OTHER_TITLE, "last_opened": 1}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_config, "config_path", lambda: cfg)
    log = tmp_path / "app.log"
    log.write_text(
        f"2026-09-23 INFO tavotto: 项目已打开: {project}（0 个脚本）\n"
        f"2026-09-23 ERROR tavotto: 打开失败: {other}/fig.py\n"
        f"2026-09-23 INFO tavotto: 同步账号 {EMAIL}\n"
        f"2026-09-23 INFO tavotto: 联系人 <{IDN_EMAIL}>、{PUNY_EMAIL}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_diagnostics, "_log_path", lambda: log)

    assert client.post("/api/projects/open", json={"path": str(project)}).status_code == 200
    engine_config.set_project_settings(str(project), {"export_dir": str(export_dir)})

    res = client.get("/api/diagnostics/bundle")
    assert res.status_code == 200
    z = open_bundle(res.data)
    texts = {n: z.read(n).decode("utf-8", errors="replace") for n in z.namelist()}
    texts["<复制诊断>"] = client.get("/api/diagnostics/summary").get_data(as_text=True)
    for name, body in texts.items():
        for secret in (
            PERSON,
            PAPER,
            EMAIL,
            OTHER_EMAIL,
            IDN_EMAIL,
            "张三丰",
            PUNY_EMAIL,
            "p1ai",
            OTHER_TITLE,
            EXPORT_TITLE,
            "坚果云-private",
        ):
            assert secret not in body, f"{secret} 出现在 {name} 里"

    # 反证：这次确实打开了项目、确实把有用的事实留下了，不是因为什么都没写才搜不到
    report = json.loads(texts["report.json"])
    proj = report["project"]
    assert proj["open"] is True and "name" not in proj
    token = proj["figures_dir"]
    assert re.fullmatch(r"<project:[0-9a-f]{10}>", token), token
    assert proj["location"] == {"cloud_storage": True, "non_ascii": True, "has_space": False}
    assert proj["document_dir"] == f"{token}/tavottofile"
    assert re.fullmatch(r"~/Desktop/seg:[0-9a-f]{10}", proj["export_dir"]), proj["export_dir"]
    assert token in texts["app.log"], "日志里那一行要与 report 用同一个项目记号"
    assert "坚果云-acct:" in texts["app.log"] or token in texts["app.log"]
    assert "<email>" in texts["app.log"]
    assert texts["app.log"].rstrip().endswith("联系人 <email>"), (
        "国际化与 punycode 地址所在的 token 整个换掉"
    )
    # README 列的 project 字段就是这一份 report 实际写出的键（打开项目时远不止记号与 location）
    fields = _readme_project_fields(texts["README.txt"])
    assert fields == list(proj), (fields, list(proj))
    assert {"id", "exists", "scripts", "settings", "location"} <= set(fields)


def _readme_project_fields(readme: str) -> list[str]:
    head = "Fields in this report's project section:\n"
    assert head in readme, "README 没有列 project 段的字段"
    return readme.split(head, 1)[1].splitlines()[0].strip().split(", ")


def test_readme_describes_the_project_section_it_actually_ships(client):
    """README 说「不含项目名」，就不能几行后又说「文件夹名仍会带上」（#524 评审）；也不能说
    project 段「只剩」记号和三个布尔而漏掉 id / exists / scripts / settings……（#536 评审）。
    字段清单从这一份 report 生成，这里与 report.json 实际的键逐个对拍。没打开项目时只有 open。"""
    z = open_bundle(client.get("/api/diagnostics/bundle").data)
    readme = z.read("README.txt").decode()
    assert "只以 <project:哈希> 记号和 location 的" in readme
    for stale in ("文件夹名", "folder name", "只剩"):
        assert stale not in readme, stale
    project = json.loads(z.read("report.json"))["project"]
    assert _readme_project_fields(readme) == list(project) == ["open"]


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        (f"/x/Library/CloudStorage/GoogleDrive-{EMAIL}/a.pdf", "CloudStorage/GoogleDrive-acct:"),
        ("C:\\Users\\x\\CloudStorage\\OneDrive-Contoso Ltd\\a.pdf", "CloudStorage\\OneDrive-acct:"),
        (f"git config user.email {EMAIL}", "<email>"),
    ],
)
def test_cloud_accounts_and_emails_are_redacted_even_outside_known_projects(raw, expect):
    """没登记成项目的路径（别的目录、日志里的随口一句）也不许带出云盘账号与邮箱。"""
    out = engine_diagnostics._redact_text(raw)
    assert expect in out, out
    assert EMAIL not in out and "Contoso" not in out


# 邮箱：不只认 ASCII（#524 评审 P1）。国际化地址（RFC 6531 / IDNA）一个字都不许剩，punycode 顶级域
# 不许留下 `--p1ai` 尾巴；期望值写死整行——只断言「秘密不在」挡不住把两边的引号、括号一起吃掉。
EMAIL_VECTORS = [
    ("用户@例子.公司", "<email>"),
    ("müller@bücher.de", "<email>"),
    ("उपयोगकर्ता@उदाहरण.कॉम", "<email>"),  # 天城文带组合记号，\w 认不全
    ("123456@qq.com", "<email>"),
    ("user@example.xn--p1ai", "<email>"),
    ("a.b@mail.dept.xn--fiqs8s", "<email>"),
    ("x@xn--80ak6aa92e.xn--p1ai", "<email>"),
    ("张三@mail.cs.例子.中国", "<email>"),
    ("用户＠例子。公司", "<email>"),  # 全角 ＠ 与 IDNA 认的全角句点
    ('{"email": "用户@例子.公司", "n": 1}', '{"email": "<email>", "n": 1}'),
    ('"\\u7528\\u6237@\\u4f8b\\u5b50.\\u516c\\u53f8"', '"<email>"'),  # json.dumps 的转义
    ("联系人 <张三@例子.公司>", "联系人 <email>"),
    ("'user@example.xn--p1ai'", "<email>"),
    ("(用户@例子.公司)", "<email>"),
    ("https://h.example/?to=用户@例子.公司&x=1", "<email>"),
    ("mailto:张三@例子.公司", "<email>"),
    ("写信给 a@b.com。", "写信给 <email>"),
    ("请写信给用户@例子.公司，谢谢", "<email>"),  # 中文不分词，本地部分从哪起分不出：宁可多抹
    ("a@b.com c@例子.中国", "<email> <email>"),
    # json.dumps（ensure_ascii）写出的：全角 ＠ 自己也被转义成 \uff20；非 BMP 字符是一对代理转义，
    # 要合成一个字符再判类别（#536 评审）
    (json.dumps("用户＠例子。公司"), '"<email>"'),
    (json.dumps("𐐀@example.com"), '"<email>"'),
    (json.dumps("a@𐐀𐐀.com"), '"<email>"'),
    (json.dumps({"to": "𐐀用户＠例子。公司"}), '{"to": "<email>"}'),
    # 落单的代理 / 其实是字面反斜杠的 `\\u…`：落单的代理照样算地址的一部分，停在反斜杠这个分隔符上
    (json.dumps("x\\ud801@a.com"), '"<email>"'),
    # 第三轮（#536）：判据换成「只在分隔符处停」之后，正面白名单漏掉的几类
    ("user@l·l.cat", "<email>"),  # IDNA 上下文字符 U+00B7（Po）
    ("a\u200db@x.com", "<email>"),  # ZWJ（Cf）在本地部分里，不许留下 `a\u200d` 残片
    ("a@b\u2010c.com", "<email>"),  # U+2010 连字符（Pd）
    ("a@b\u200cc.xn--p1ai", "<email>"),  # ZWNJ（Cf）在域名里
    ("x^y@例子.公司", "<email>"),  # Sk
    ("user%40example.com", "<email>"),  # URL 编码的 @
    ("ssh://git@github.com/x", "<email>"),  # URL userinfo 照样抹，停在 `/` 上
    ("「用户@例子.公司」", "<email>"),  # 直角引号（Ps / Pe）是分隔符
    ("“用户@例子.公司”", "<email>"),  # 弯引号（Pi / Pf）也是
    ("«a@b.com»", "<email>"),
    ("C:\\Users\\a@b.com\\x", "<email>"),
    # 第五轮（#536）：不在 token 里找边界，含 @ 的 token 整个抹
    ("o'connor@example.com", "<email>"),
    ("user@[192.0.2.1]", "<email>"),
    ('寄给 "quoted local"@x.com 吧', "寄给 <email> 吧"),  # 引号里的空白：并到上一个引号
    ("a@b.c(comment)", "<email>"),
    ("(user@x.com), ok", "<email> ok"),
    ("a@b.com,c@例子.中国", "<email>"),
    (json.dumps({"k": 'x "quoted local"@x.com'}), '{"k": "x <email>"}'),  # JSON 行：结构原样
]
NOT_EMAILS = [
    "matplotlib@3.10",
    "numpy@1.26.4 scipy@1.14",
    "pkg@2.0.0-beta",
    "@app.route('/x')",
    "  @dataclass",
    "a@b",
    "user@localhost",
    "HEAD@{0}",
    "x@例子",
    "@某人 你好",
    "pkg@v1.2.3+build.5",
    "matplotlib@3.10,",
    "/x/.pnpm/jsdom@30.0.1/node_modules/jsdom/lib/api.js",
    "(@某人)",
    "(@app.route)",  # 只有「@ 前只有开括号」这一条放行它：域名两段、不是版本号
    json.dumps({"dep": "numpy@1.26.4"}),
]


def _assert_no_fragment_left(raw: str, out: str) -> None:
    """含 @ 的 token 在输出里一个非空白字符都不剩：输出里不含 `<email>` 的每个 token，都得是原文里
    某个**不含** @ 的 token（JSON 行按字符串内容切，这里把引号与 `{}[],:` 也当成分隔再比）。"""
    split = re.compile(r'[\s{}\[\],:"]+')
    at = re.compile(r"[@＠]|\\u(?:0040|[Ff][Ff]20)|%40")
    clean = {t for t in split.split(raw) if t and not at.search(t)}
    for tok in split.split(out.replace("<email>", " ")):
        assert not tok or tok in clean, (tok, out)


@pytest.mark.parametrize(("raw", "expect"), EMAIL_VECTORS)
def test_internationalized_and_punycode_emails_are_redacted_whole(raw, expect):
    out = engine_diagnostics._redact_text(raw)
    _assert_no_fragment_left(raw, out)
    assert out == expect


@pytest.mark.parametrize("raw", NOT_EMAILS)
def test_things_shaped_like_emails_but_not_addresses_are_left_alone(raw):
    assert engine_diagnostics._redact_text(raw) == raw


# 性质用例（#536 第三 / 五轮）：地址里出现什么字符都不许让它漏出一截。从下面每一类里抽字符（含 ASCII
# 标点：引号、括号、`,;:/\\|?&=#` 都在 Po / Ps / Pe / Sm 里）拼进本地部分与两段域名，断言那个 token
# 整个换成 `<email>`、一个非空白字符都不剩；再 json.dumps 一遍走 JSON 行与转义那条路。
_SAMPLED_CATEGORIES = (
    "Lu Ll Lt Lm Lo Mn Mc Me Nd Nl No Pc Pd Ps Pe Pi Pf Po Sm Sc Sk So Cf Co".split()
)
#: 起点（@ / ＠ / %40）与点号是地址的结构，不当成「任意字符」抽；空白是 token 的边界
_STRUCTURAL = set("@＠%.。．｡")


def _unicode_pools() -> dict[str, list[str]]:
    import unicodedata

    pools: dict[str, list[str]] = {c: [] for c in _SAMPLED_CATEGORIES}
    for cp in range(0x110000):
        ch = chr(cp)
        cat = unicodedata.category(ch)
        if cat not in pools or ch.isspace() or ch in _STRUCTURAL:
            continue
        pools[cat].append(ch)
    return pools


def test_any_unicode_inside_an_address_is_redacted_whole():
    import random
    import unicodedata

    pools = _unicode_pools()
    assert all(pools.values()), "每一类都要抽得到字符，否则这条性质什么都没量"
    assert {"'", '"', "(", ")", "[", "]", ",", ";", ":", "/", "\\"} <= {
        c for cat in ("Po", "Ps", "Pe") for c in pools[cat]
    }, "ASCII 标点要在抽样池里"
    rng = random.Random(536)
    cats = _SAMPLED_CATEGORIES

    def piece(forced: str) -> str:
        chars = [rng.choice(pools[forced])]
        chars += [rng.choice(pools[rng.choice(cats)]) for _ in range(rng.randint(0, 4))]
        rng.shuffle(chars)
        return "".join(chars)

    def only_openers(t: str) -> bool:
        return all(c in "\"'`" or unicodedata.category(c) in ("Ps", "Pi") for c in t)

    for k in range(len(cats) * 20):
        # 负面清单的两种形状要避开，性质才量得到：本地部分只有开括号 / 开引号（= 「@ 前没有账号」）、
        # 域名以 ASCII 数字或 v 开头（可能是版本号）
        local = piece(cats[k % len(cats)])
        while only_openers(local):
            local = piece(cats[k % len(cats)])
        label = piece(cats[(k * 7 + 3) % len(cats)])
        while label[0] in "0123456789v":
            label = piece(cats[(k * 7 + 3) % len(cats)])
        address = f"{local}@{label}.{piece(cats[(k * 5 + 1) % len(cats)])}"
        text = f"前 {address} 后"
        out = engine_diagnostics._redact_text(text)
        assert out == "前 <email> 后", (address, [f"U+{ord(c):04X}" for c in address], out)
        escaped = json.dumps(text)
        expect = json.dumps("前 ")[:-1] + "<email>" + json.dumps(" 后")[1:]
        assert engine_diagnostics._redact_text(escaped) == expect, (escaped, address)
