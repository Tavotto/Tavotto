"""计算 SVG 原生编辑的真实 HTTP/worker/RenderCore 闭环。

只在真实 worker 返回后注入外部改源，或记录实际重放次序；不桩造计算结果。
本夹具的轴与系列为同父连续节点，不把尚未闭合的任意 SVG 编辑冒充已完成。
"""

from __future__ import annotations

import base64
import copy
import io
import re
import xml.etree.ElementTree as ET

import pytest

from tavotto import app as m, chartbundle
from tavotto.engine import (
    chartprocess,
    chartprofile,
    envlease,
    exportjob,
    patchspec,
    pool,
    project_watch,
)

NS = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", re.I)
SOURCE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100"><rect width="100" height="100" fill="white"/>'
    b'<g id="old_axis"><path d="M10 10L10 90L90 90" fill="none" stroke="black"/></g>'
    b'<path id="curve" d="M10 90L90 10" fill="none" stroke="#888888"/>'
    b'<text id="keep" x="3" y="7" font-size="5" '
    b'font-family="Liberation Sans">Source keep</text></svg>'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto.rendercore import fonts, renderhost

    # 依赖/字体不全应报告未执行原因并失败，不能把缺 API 或真实渲染转成 skip。
    assert not fonts.FontRegistry.discover().missing, "先运行 scripts/fetch_fonts.py"
    worker_python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_python)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setitem(m.app.config, "TESTING", True)
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "cache")
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "chart.svg").write_bytes(SOURCE)
    exportjob.reset_for_tests()
    m.open_project(str(root))
    try:
        yield m.app.test_client(), root
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects()
        project_watch.stop()
        exportjob.reset_for_tests()
        renderhost.shutdown_shared()


def _bind(client, y=0.25):
    evidence = {
        "p1_user": 0.0,
        "p1_value": 0.0,
        "p2_user": 1.0,
        "p2_value": 1.0,
        "scale": "linear",
    }
    response = client.post(
        "/api/svg/chart/bind",
        json={
            "file": "chart.svg",
            "axes_id": "ax1",
            "rect": [10.0, 10.0, 80.0, 80.0],
            "x": evidence,
            "y": evidence,
            "axes_nodes": ["old_axis"],
            "series": [
                {
                    "node": "curve",
                    "x_col": "x",
                    "y_col": "y",
                    "kind": "line",
                    "style": {"color": "#0000ff", "linewidth": 3.0},
                }
            ],
            "completeness": "self-contained",
            "dataset_text": f"x,y\n0,{y}\n1,{y}\n",
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    return {"model": body["model"], "binding": body["binding"]}


def _color(value):
    return {"gid": "axes_0.lines_0", "prop": "color", "value": value}


def test_chart_http_entries_respect_install_lease_and_recover_without_source_changes(project):
    client, root = project
    chart = _bind(client)
    python, _source = pool.resolve_worker_python(root)
    requests = [
        ("/api/svg/chart/bind", {"file": "chart.svg"}),
        ("/api/svg/chart/compute", {"file": "chart.svg", **chart}),
        ("/api/engine/render", {"id": "chart.svg", "chart": chart, "patches": []}),
        ("/api/engine/preview_png", {"id": "chart.svg", "chart": chart, "patches": []}),
    ]
    with envlease.mutating("chart-http-install", python):
        for endpoint, body in requests:
            response = client.post(endpoint, json=body)
            assert response.status_code == 503, (endpoint, response.get_json())
            assert response.get_json()["code"] == envlease.ENVIRONMENT_MUTATING
        assert chartprocess.active_count() == 0
        assert not envlease.native_sessions_on(python)
    _assert_line(_render(client, chart), y=0.25)
    assert chartprocess.active_count() == 0
    assert envlease.state_of(python) == envlease.STATE_IDLE
    assert (root / "chart.svg").read_bytes() == SOURCE


def test_rebinding_data_keeps_existing_edits_on_the_same_owner(project):
    client, root = project
    previous, current = _bind(client, y=0.25), _bind(client, y=0.6)
    response = client.post(
        "/api/svg/chart/compute",
        json={
            "file": "chart.svg",
            **current,
            "render_profile": chartprofile.NATIVE_PROFILE,
            "overrides": [_color("#ff0000")],
            "previous_chart": previous,
        },
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    line = next(e for e in result["manifest"]["elements"] if e["gid"] == "axes_0.lines_0")
    assert all(p[1] == pytest.approx(0.42) for p in line["geometry"]["paths"][0]["points"])
    assert "stroke: #ff0000" in result["svg"]
    assert (root / "chart.svg").read_bytes() == SOURCE


@pytest.mark.parametrize("missing_previous", [False, True])
def test_rebinding_cannot_apply_old_edits_before_owner_validation(
    project, monkeypatch, missing_previous
):
    client, root = project
    previous = _bind(client)
    current = copy.deepcopy(previous)
    current["model"]["series"][0]["node"] = "keep"
    current["binding"]["entries"][0]["node"] = "keep"

    def no_spawn(*args, **kwargs):
        pytest.fail("changed ownership reached the worker before migration was proven")

    monkeypatch.setattr(m, "_chart_scaffold_via_worker", no_spawn)
    response = client.post(
        "/api/svg/chart/compute",
        json={
            "file": "chart.svg",
            **current,
            "render_profile": chartprofile.NATIVE_PROFILE,
            "overrides": [_color("#ff0000")],
            "previous_chart": None if missing_previous else previous,
        },
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "chart_binding_stale"
    assert (root / "chart.svg").read_bytes() == SOURCE


def _render(client, chart, patches=(), *, file="chart.svg"):
    response = client.post(
        "/api/engine/render",
        json={"id": file, "chart": chart, "patches": list(patches), "inline_svg": True},
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert type(result["rev"]) is int
    assert re.fullmatch(r"[0-9a-f]{64}", result["chart_revision"])
    assert result["preview"]["mode"] == "vector"
    assert result.get("warnings", []) == []
    assert result.get("unrestored", 0) == 0
    assert isinstance(result["manifest"]["elements"], list)
    return result


def _compute(client, chart, *, file="chart.svg", profile=None, patches=()):
    body = {"file": file, **chart}
    if profile is not None:
        body.update(render_profile=profile, overrides=list(patches))
    response = client.post("/api/svg/chart/compute", json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _element(result, gid="axes_0.lines_0"):
    return next(e for e in result["manifest"]["elements"] if e["gid"] == gid)


def _values(element):
    return {field["prop"]: field["value"] for field in element["editable"] if "value" in field}


def _assert_line(result, *, y, color="#0000ff", width=3.0):
    # 独立数据公式：x=10+80*x_data，SVG y=90-80*y_data；manifest 是顶原点分数。
    points = _element(result)["geometry"]["paths"][0]["points"]
    for actual, expected in zip(points, [[0.1, 0.9 - 0.8 * y], [0.9, 0.9 - 0.8 * y]], strict=True):
        assert actual == pytest.approx(expected, abs=1e-7)
    values = _values(_element(result))
    assert values["color"] == color
    assert values["linewidth"] == width
    root = ET.fromstring(result["svg"])
    group = next(e for e in root.iter() if e.get("id") == "axes_0.lines_0")
    paths = list(group.iter(NS + "path"))
    assert len(paths) == 1
    path = paths[0]
    assert [float(n) for n in NUMBER.findall(path.get("d", ""))] == pytest.approx(
        [10, 90 - 80 * y, 90, 90 - 80 * y], abs=1e-6
    )
    assert f"stroke: {color}" in path.get("style", "")
    assert "Source keep" in "".join(root.itertext())


def _png(client, chart, patches=(), *, file="chart.svg"):
    from PIL import Image

    response = client.post(
        "/api/engine/preview_png",
        json={
            "id": file,
            "chart": chart,
            "patches": list(patches),
            "w": 230,
            "with_manifest": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    result = response.get_json()
    raw = base64.b64decode(result["png"], validate=True)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (400, 400), "230px 请求沿用既有400px bucket，并核对真实IHDR"
        pixels = image.convert("RGB")
    return result, pixels


def _assert_pixel(image, *, y, color):
    x, row = 200, round((0.9 - 0.8 * y) * 400)
    expected = {"red": 0, "green": 1, "blue": 2}[color]
    samples = [image.getpixel((x + dx, row + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    assert any(
        rgb[expected] > 160 and all(v < 100 for i, v in enumerate(rgb) if i != expected)
        for rgb in samples
    ), (y, color, samples)


def _portable(root, visible, chart, *, profile, patches=(), name="portable.svg"):
    data = chartbundle.embed_chart(
        visible.encode("utf-8"), SOURCE, chart, render_profile=profile, overrides=list(patches)
    )
    path = root / name
    path.write_bytes(data)
    return path, data


def test_compute_default_contract_stays_legacy_and_native_returns_measured_manifest(project):
    client, root = project
    chart = _bind(client)
    legacy = _compute(client, chart)
    # 本步起 /compute 响应统一带顶层 render_frame（稳定出口）；legacy 与 /1
    # 都没有计算框，值恒为 None——闭集扩一项、同时钉住 /1 不冒充 /2。
    assert set(legacy) == {
        "svg",
        "facts",
        "scene_digest",
        "render_revision",
        "model_digest",
        "binding_digest",
        "engine",
        "render_frame",
    }
    assert legacy["render_frame"] is None
    native = _compute(
        client, chart, profile=chartprofile.NATIVE_PROFILE, patches=[_color("#ff0000")]
    )
    _assert_line(native, y=0.25, color="#ff0000")
    assert native["render_frame"] is None
    assert native["render_revision"] != legacy["render_revision"]
    rendered = _render(client, chart, [_color("#ff0000")])
    assert native["render_revision"] == rendered["chart_revision"]
    assert native["manifest"] == rendered["manifest"]
    assert native["svg"] == rendered["svg"]
    assert (root / "chart.svg").read_bytes() == SOURCE


def test_two_models_and_full_edit_lists_do_not_share_the_last_render(project):
    client, _root = project
    first_chart, second_chart = _bind(client, 0.25), _bind(client, 0.75)
    red = [_color("#ff0000")]
    green = [_color("#00ff00")]
    first = _render(client, first_chart, red)
    second = _render(client, second_chart, green)
    again = _render(client, first_chart, red)
    reset = _render(client, first_chart, [])
    _assert_line(first, y=0.25, color="#ff0000")
    _assert_line(second, y=0.75, color="#00ff00")
    _assert_line(again, y=0.25, color="#ff0000")
    _assert_line(reset, y=0.25)
    assert first["chart_revision"] == again["chart_revision"]
    assert first["svg"] == again["svg"] and first["manifest"] == again["manifest"]
    assert len({first["chart_revision"], second["chart_revision"], reset["chart_revision"]}) == 3


def test_raw_patch_order_is_in_revision_even_when_canonical_patch_hash_matches(project):
    client, _root = project
    chart = _bind(client)
    patches = [_color("#ff0000"), {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5}]
    reverse = list(reversed(patches))
    assert patchspec.patch_hash(patches) == patchspec.patch_hash(reverse)
    first, second = _render(client, chart, patches), _render(client, chart, reverse)
    _assert_line(first, y=0.25, color="#ff0000", width=5)
    _assert_line(second, y=0.25, color="#ff0000", width=5)
    assert first["chart_revision"] != second["chart_revision"]


def test_png_dimensions_pixels_manifest_and_revision_belong_to_the_requested_model(project):
    client, _root = project
    a, b = _bind(client, 0.25), _bind(client, 0.75)
    patches = [_color("#ff0000")]
    rendered = _render(client, a, patches)
    first, image_a = _png(client, a, patches)
    second, image_b = _png(client, b, [_color("#00ff00")])
    again, image_again = _png(client, a, patches)
    assert first["chart_revision"] == rendered["chart_revision"] == again["chart_revision"]
    assert first["manifest"] == rendered["manifest"] == again["manifest"]
    assert first["chart_revision"] != second["chart_revision"]
    _assert_pixel(image_a, y=0.25, color="red")
    _assert_pixel(image_b, y=0.75, color="green")
    _assert_pixel(image_again, y=0.25, color="red")
    assert image_a.tobytes() == image_again.tobytes()


@pytest.mark.parametrize(
    "patch",
    [
        {"gid": "missing.artist", "prop": "color", "value": "red"},
        {"gid": "keep", "prop": "text", "value": "cannot own original text"},
        {"gid": "axes_0.lines_0", "prop": "not_a_setter", "value": 1},
        _color("not-a-real-color"),
        {**_color("#ff0000"), "identity": "l1:0000000000000000"},
    ],
    ids=["missing-gid", "original-unowned", "unknown-setter", "invalid-color", "wrong-identity"],
)
def test_invalid_native_edits_cannot_return_success_with_unapplied_changes(project, patch):
    client, root = project
    chart = _bind(client)
    response = client.post(
        "/api/engine/render",
        json={"id": "chart.svg", "chart": chart, "patches": [patch], "inline_svg": True},
    )
    assert 400 <= response.status_code < 600, response.get_json()
    assert response.get_json()["code"]
    assert "svg" not in response.get_json()
    assert (root / "chart.svg").read_bytes() == SOURCE


def test_v1_visible_replay_is_verified_before_native_upgrade(project, monkeypatch):
    client, root = project
    chart = _bind(client)
    legacy = _compute(client, chart)
    path, original = _portable(root, legacy["svg"], chart, profile=chartprofile.LEGACY_PROFILE)
    assert chartbundle.extract_chart(original).render_profile == chartprofile.LEGACY_PROFILE
    (root / "chart.svg").unlink()
    producer = m._chart_scaffold_via_worker
    profiles = []

    def record(*args, **kwargs):
        profiles.append(kwargs.get("render_profile", chartprofile.LEGACY_PROFILE))
        return producer(*args, **kwargs)

    monkeypatch.setattr(m, "_chart_scaffold_via_worker", record)
    result = _render(client, chart, [_color("#ff0000")], file=path.name)
    _assert_line(result, y=0.25, color="#ff0000")
    assert profiles == [chartprofile.LEGACY_PROFILE, chartprofile.NATIVE_PROFILE]
    assert path.read_bytes() == original


@pytest.mark.parametrize("profile", [chartprofile.LEGACY_PROFILE, chartprofile.NATIVE_PROFILE])
def test_self_consistent_visible_tamper_still_fails_actual_replay_before_native_request(
    project, profile
):
    client, root = project
    chart = _bind(client)
    requested = [_color("#ff0000")]
    if profile == chartprofile.LEGACY_PROFILE:
        visible = _compute(client, chart)["svg"].replace("Source keep", "Altered visible")
        stored_patches = []
    else:
        visible = _render(client, chart, requested)["svg"]
        # 重新签完整包并更新digest，也不能把红色可见图宣称成绿色重放的结果。
        stored_patches = [_color("#00ff00")]
    path, data = _portable(
        root, visible, chart, profile=profile, patches=stored_patches, name="tampered.svg"
    )
    assert chartbundle.extract_chart(data) is not None
    response = client.post(
        "/api/engine/render",
        json={"id": path.name, "chart": chart, "patches": requested, "inline_svg": True},
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "chart_source_mismatch"
    assert "computed SVG differs" in response.get_json()["error"]
    assert path.read_bytes() == data


def test_native_v2_restores_complete_edits_without_the_original_file(project):
    client, root = project
    chart = _bind(client, 0.75)
    patches = [_color("#ff0000"), {"gid": "axes_0", "prop": "xlim", "value": [0, 2]}]
    native = _render(client, chart, patches)
    path, data = _portable(
        root, native["svg"], chart, profile=chartprofile.NATIVE_PROFILE, patches=patches
    )
    bundle = chartbundle.extract_chart(data)
    assert bundle is not None
    assert bundle.render_profile == chartprofile.NATIVE_PROFILE and bundle.overrides == patches
    assert bundle.source_svg == SOURCE
    (root / "chart.svg").unlink()
    inspected = client.get(f"/api/svg/chart/inspect?id={path.name}")
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.get_json()["scene_digest"] == chart["model"]["scene_digest"]
    assert inspected.get_json()["restored_render"] == {
        "render_profile": chartprofile.NATIVE_PROFILE,
        "overrides": patches,
    }
    restored = _render(client, bundle.chart, bundle.overrides, file=path.name)
    assert restored["chart_revision"] == native["chart_revision"]
    assert restored["svg"] == native["svg"] and restored["manifest"] == native["manifest"]
    assert path.read_bytes() == data


@pytest.mark.parametrize("endpoint", ["compute", "render", "preview_png"])
def test_source_change_during_real_native_calculation_is_not_published(
    project, monkeypatch, endpoint
):
    client, root = project
    chart = _bind(client)
    producer = m._chart_scaffold_via_worker
    changed = SOURCE.replace(b"Source keep", b"Changed outside")
    calls = []

    def change_after_actual_worker(*args, **kwargs):
        result = producer(*args, **kwargs)
        calls.append(True)
        (root / "chart.svg").write_bytes(changed)
        return result

    monkeypatch.setattr(m, "_chart_scaffold_via_worker", change_after_actual_worker)
    if endpoint == "compute":
        url = "/api/svg/chart/compute"
        body = {
            "file": "chart.svg",
            **chart,
            "render_profile": chartprofile.NATIVE_PROFILE,
            "overrides": [],
        }
    else:
        url = f"/api/engine/{endpoint}"
        body = {"id": "chart.svg", "chart": chart, "patches": [], "inline_svg": True}
        if endpoint == "preview_png":
            body.update(w=230, with_manifest=True)
    response = client.post(url, json=body)
    assert calls == [True]
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "chart_source_changed"
    assert not ({"svg", "manifest", "png"} & response.get_json().keys())
    assert (root / "chart.svg").read_bytes() == changed


@pytest.mark.parametrize("gid", ["axes_0.lines_0", "axes_0"], ids=["line", "axes"])
def test_native_visibility_hidden_then_full_list_restore_keeps_manifest_and_dom_paired(
    project, gid
):
    client, root = project
    chart = _bind(client)
    edits = [_color("#ff0000")]
    baseline = _render(client, chart, edits)
    _assert_line(baseline, y=0.25, color="#ff0000")
    hidden = _render(client, chart, [*edits, {"gid": gid, "prop": "visible", "value": False}])
    assert hidden["chart_revision"] != baseline["chart_revision"]
    assert _values(_element(hidden, gid))["visible"] is False
    hidden_root = ET.fromstring(hidden["svg"])
    hidden_node = next(e for e in hidden_root.iter() if e.get("id") == gid)
    assert hidden_node.get("display") == "none"
    assert not any(
        e.tag in {NS + tag for tag in ("path", "rect", "text", "image", "use")}
        for e in hidden_node.iter()
    ), "隐藏容器不能仅打标记却保留实际可画实体"
    assert "Source keep" in "".join(hidden_root.itertext())
    if gid == "axes_0":
        child = _element(hidden)
        assert _values(child)["visible"] is True, "父轴隐藏不能改写曲线自己的visible值"
        assert child["geometry"]["paths"] == [], "自身可见的子元素此时仍不可命中"
        assert not any(
            e.tag in {NS + tag for tag in ("path", "rect", "text", "image", "use")}
            for e in next(e for e in hidden_root.iter() if e.get("id") == "axes_0.lines_0").iter()
        )
        for element in hidden["manifest"]["elements"]:
            if element["gid"].startswith("axes_0.") and "geometry" in element:
                assert element["geometry"]["paths"] == [], element["gid"]
    # 全量列表撤掉visible编辑：恢复本来可见的对象，同时保留本次颜色编辑。
    restored = _render(client, chart, edits)
    assert _values(_element(restored, gid))["visible"] is True
    assert restored["chart_revision"] == baseline["chart_revision"]
    assert restored["manifest"] == baseline["manifest"]
    assert restored["svg"] == baseline["svg"]
    _assert_line(restored, y=0.25, color="#ff0000")
    restored_root = ET.fromstring(restored["svg"])
    restored_node = next(e for e in restored_root.iter() if e.get("id") == gid)
    assert restored_node.get("display") is None
    assert any(e.tag == NS + "path" for e in restored_node.iter())
    assert (root / "chart.svg").read_bytes() == SOURCE
