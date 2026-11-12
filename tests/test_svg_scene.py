"""计算图表 SVG 场景盘点（SG03 / ADR-XXXX）：清单、摘要、安全拒绝与端点。"""

from __future__ import annotations

import gzip
import hashlib
import tempfile
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import pool, project_watch
from tavotto.rendercore.svgcheck import SVGBudget
from tavotto.svgscene import SceneError, read_scene, scene_inventory

CORPUS = Path(__file__).resolve().parent / "support" / "svg_corpus"


# ---------------------------------------------------------------------------
# 纯函数层
# ---------------------------------------------------------------------------


def test_corpus_scene_inventory_is_stable_and_complete():
    data = (CORPUS / "line_two_series.svg").read_bytes()
    inv = scene_inventory(data)
    assert inv["scene_digest"] == hashlib.sha256(data).hexdigest()
    assert (inv["w_pt"], inv["h_pt"]) == (360.0, 252.0)
    assert inv["view_box"] == [0.0, 0.0, 360.0, 252.0]
    ids = {n["id"] for n in inv["nodes"]}
    # matplotlib 的原有 id 原样保留（SG06 绑定引用它们）
    assert {"axes_1", "line2d_13", "line2d_14", "xtick_1", "text_5"} <= ids
    assert not inv["truncated"]
    # 同一份字节 → 同一张清单（绑定稳定性）
    assert scene_inventory(data) == inv


def test_generated_ids_for_idless_nodes_are_deterministic():
    inv = scene_inventory((CORPUS / "plain_shapes.svg").read_bytes())
    ids = [n["id"] for n in inv["nodes"]]
    assert len(ids) == len(set(ids))
    assert scene_inventory((CORPUS / "plain_shapes.svg").read_bytes())["nodes"] == inv["nodes"]
    tags = {n["id"]: n["tag"] for n in inv["nodes"]}
    # 轴框与数据点都进了清单（可绑定候选）
    assert set(tags.values()) >= {"rect", "circle", "path", "text"}


def test_text_and_style_facts_are_listed_for_binding_ui():
    inv = scene_inventory((CORPUS / "line_two_series.svg").read_bytes())
    title = next(n for n in inv["nodes"] if n.get("text", "").startswith("Two known series"))
    assert title["tag"] == "text"
    # 系列线是 line2d_13 组里无 id 的 path 子节点；描边色来自 style 串
    assert any(n.get("stroke") == "#1f77b4" for n in inv["nodes"])


def test_danger_corpus_is_rejected_with_stable_code():
    with pytest.raises(SceneError) as ei:
        scene_inventory((CORPUS / "external_danger.svg").read_bytes())
    assert ei.value.code == "svg_dtd_forbidden"


def test_gzip_svg_is_not_silently_decompressed():
    gz = gzip.compress(b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
    with pytest.raises(SceneError) as ei:
        scene_inventory(gz)
    assert ei.value.code == "svgz_unsupported"


def test_budget_overflow_is_refused():
    tiny = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect/></svg>'
    assert scene_inventory(tiny)["scene_digest"]
    blob = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><!-- '
    path = Path(tempfile.mkdtemp()) / "big.svg"
    path.write_bytes(blob + b"a" * SVGBudget().max_bytes + b" --></svg>")
    with pytest.raises(SceneError) as ei:
        read_scene(path)
    assert ei.value.code == "svg_resource_limit"


def test_duplicate_ids_are_reported_not_guessed():
    dup = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
        b'<rect id="a"/><rect id="a"/></svg>'
    )
    with pytest.raises(SceneError) as ei:
        scene_inventory(dup)
    # svgcheck 边界先拒绝（稳定码 svg_invalid_id）；场景层的重复 id 判据是第二道
    assert ei.value.code == "svg_invalid_id"


# ---------------------------------------------------------------------------
# 端点（会话认证内的只读盘点）
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "chart.svg").write_bytes((CORPUS / "line_two_series.svg").read_bytes())
    (root / "plain.svg").write_bytes((CORPUS / "plain_shapes.svg").read_bytes())
    (root / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    m.open_project(str(root))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("scene inspection executed a script")
    )
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def test_inspect_endpoint_returns_inventory(project):
    client, root = project
    response = client.get("/api/svg/chart/inspect?id=chart.svg")
    assert response.status_code == 200
    body = response.json
    assert body["scene_digest"] == hashlib.sha256((root / "chart.svg").read_bytes()).hexdigest()
    assert any(n["id"] == "line2d_13" for n in body["nodes"])


def test_inspect_endpoint_refuses_danger_and_outside_paths(project):
    client, root = project
    (root / "evil.svg").write_bytes((CORPUS / "external_danger.svg").read_bytes())
    response = client.get("/api/svg/chart/inspect?id=evil.svg")
    assert response.status_code == 422
    assert response.json["code"] == "svg_dtd_forbidden"
    assert client.get("/api/svg/chart/inspect?id=../plain.svg").status_code == 403
    assert client.get("/api/svg/chart/inspect?id=img.png").status_code == 422


def test_inspect_endpoint_never_modifies_the_source(project):
    client, root = project
    before = (root / "chart.svg").read_bytes()
    mtime = (root / "chart.svg").stat().st_mtime_ns
    client.get("/api/svg/chart/inspect?id=chart.svg")
    client.get("/api/svg/chart/inspect?id=plain.svg")
    client.get("/api/svg/chart/inspect?id=chart.svg")
    assert (root / "chart.svg").read_bytes() == before
    assert (root / "chart.svg").stat().st_mtime_ns == mtime


def test_clip_and_opacity_facts_are_reported_for_region_ownership():
    inv = scene_inventory((CORPUS / "clip_opacity.svg").read_bytes())
    by_tag = {}
    for n in inv["nodes"]:
        by_tag.setdefault(n["tag"], []).append(n)
    clipped = [n for n in inv["nodes"] if n.get("clipped")]
    assert clipped, "the clip-path group must be reported"
    alphas = [n["alpha"] for n in inv["nodes"] if "alpha" in n]
    assert alphas and all(a < 1.0 for a in alphas)


def test_outlined_text_is_paths_not_editable_text():
    inv = scene_inventory((CORPUS / "text_outlined.svg").read_bytes())
    text_nodes = [n for n in inv["nodes"] if n["tag"] == "text" and n.get("text")]
    assert not text_nodes, "转曲文字不得冒充可编辑字符"
    glyph_uses = [n for n in inv["nodes"] if n["tag"] == "use"]
    assert glyph_uses, "轮廓字形以 use/defs 路径存在"
