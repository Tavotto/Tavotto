"""N4 声明式 legend 合同：真 worker 脚手架创建图例、条目模型与同步。

看护：scaffold 按 binding.legend 创建真 Legend（follow 条目 handle = 系列实际
artist，custom 条目 = 代理示意线）；`legend:leg1` role 的 gids 是
instrument 的原生命名（axes_0.legend / texts_j）；manifest 的 legend_entry
按**用户指认**给 source_gid，同名同色并列不弃权也不猜；系列改色后 follow
条目示意线同步（sync_legends 语义），custom 条目保持 pristine；legacy
profile 拒绝 legend 合同（防御层，主闸在 app）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import chartmodel, svgchart
from tavotto.engine.chartprofile import NATIVE_PROFILE

ROOT = Path(__file__).resolve().parents[1]


def build_source() -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
        'viewBox="0 0 100 100" preserveAspectRatio="none">'
        '<g id="original"><path id="oldaxis" d="M10 10V90H90"/>'
        '<path id="curve1" d="M10 70H90"/><path id="curve2" d="M10 30H90"/>'
        '<g id="legend_region"><rect id="lgbox" x="70" y="10" width="25" height="14"/></g>'
        "</g></svg>"
    ).encode()


def build_model() -> dict:
    data = {"columns": {"x": [0, 1], "y1": [0.5, 0.5], "y2": [0.5, 0.5]}}
    model = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [10, 10, 80, 80],
                "xlim": [0, 1],
                "ylim": [0, 1],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "curve1",
                "x": "x",
                "y": "y1",
                "style": {"color": "red", "linewidth": 3},
            },
            {
                "id": "s2",
                "kind": "line",
                "axes": "ax1",
                "node": "curve2",
                "x": "x",
                "y": "y2",
                "style": {"color": "blue", "linewidth": 3},
            },
        ],
        "dataset": data,
        "scene_digest": "a" * 64,
        "data_digest": chartmodel.digest_of(data),
        "engine": {"matplotlib": "test", "numpy": "test", "tavotto": "test"},
    }
    return model


def build_binding(model: dict) -> dict:
    return {
        "model_rev": 1,
        "scene_digest": model["scene_digest"],
        "entries": [
            {"object": "axes:ax1", "node": "oldaxis"},
            {"object": "series:s1", "node": "curve1"},
            {"object": "series:s2", "node": "curve2"},
        ],
        "legend": [
            {
                "id": "leg1",
                "node": "legend_region",
                "rect": [70.0, 10.0, 25.0, 14.0],
                "entries": [
                    {"index": 0, "label": "first", "series": "s1"},
                    {"index": 1, "label": "second", "series": "s2"},
                ],
            }
        ],
    }


FRAME = {"view_box": [0, 0, 100, 100], "w_pt": 100, "h_pt": 100, "preserve": "none"}


def run_worker(request: dict) -> dict:
    """真 worker 子进程（``python -m tavotto.engine.svgchart``）：stdin 请求。"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,runpy;sys.path.insert(0,sys.argv[1]);"
            "runpy.run_module('tavotto.engine.svgchart',run_name='__main__')",
            str(ROOT / "src"),
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def scaffold_request(model: dict, patches=(), legend=()) -> dict:
    return {
        "model": model,
        "frame": FRAME,
        "salt": "tavotto",
        "render_profile": NATIVE_PROFILE,
        "patches": patches,
        "legend": legend,
    }


def element(result: dict, gid: str) -> dict:
    return next(e for e in result["manifest"]["elements"] if e["gid"] == gid)


def field(entry: dict, prop: str) -> dict:
    return next(f for f in entry["editable"] if f["prop"] == prop)


def legend_entry_of(result: dict, gid: str) -> dict:
    return element(result, gid)["legend_entry"]


def test_worker_scaffold_creates_legend_group_with_roles_and_entry_facts():
    model, binding = build_model(), build_binding(build_model())
    response = run_worker(scaffold_request(model) | {"legend": binding["legend"]})
    assert response["ok"], response.get("error")
    import base64

    fragment = base64.b64decode(response["fragment_b64"]).decode("utf-8")
    assert "axes_0.legend" in fragment and "axes_0.legend.texts_0" in fragment
    gids = response["roles"]["legend:leg1"]["gids"]
    assert "axes_0.legend" in gids
    assert "axes_0.legend.texts_0" in gids and "axes_0.legend.texts_1" in gids
    # follow 条目按**用户指认**归源：source_gid 各自等于对应系列的 primary gid
    assert response["roles"]["series:s1"]["primary_gid"] == "axes_0.lines_0"
    assert response["roles"]["series:s2"]["primary_gid"] == "axes_0.lines_1"
    entry0 = legend_entry_of(response, "axes_0.legend.texts_0")
    entry1 = legend_entry_of(response, "axes_0.legend.texts_1")
    assert entry0 == {
        "index": 0,
        "source_gid": "axes_0.lines_0",
        "binding_default": "follow_source",
    }
    assert entry1 == {
        "index": 1,
        "source_gid": "axes_0.lines_1",
        "binding_default": "follow_source",
    }
    # follow 条目的示意线颜色跟随系列（scaffold 时即一致）
    assert field(element(response, "axes_0.legend.texts_0"), "handle_color")["value"] != "#000000"
    # 位置模型：图例盒出现在合同 rect 附近（左上角对齐，允许盒高差）——
    # 由组合测试精确断言像素；这里只锁 role 与事实形状。
    assert response["coordinates"]["kind"] == "scene-viewport-figure-fraction"


def test_same_style_series_follow_by_designation_not_fingerprint():
    model = build_model()
    model["series"][1]["style"] = dict(model["series"][0]["style"])  # 同色同款
    binding = build_binding(model)
    binding["legend"][0]["entries"] = [
        {"index": 0, "label": "twin A", "series": "s1"},
        {"index": 1, "label": "twin B", "series": "s2"},
    ]
    response = run_worker(scaffold_request(model) | {"legend": binding["legend"]})
    assert response["ok"], response.get("error")
    entry0 = legend_entry_of(response, "axes_0.legend.texts_0")
    entry1 = legend_entry_of(response, "axes_0.legend.texts_1")
    # 指纹并列不再弃权：两条各归各系列，两两不同
    assert entry0["source_gid"] == "axes_0.lines_0"
    assert entry1["source_gid"] == "axes_0.lines_1"
    assert entry0["binding_default"] == entry1["binding_default"] == "follow_source"


def test_custom_entry_has_no_source_and_keeps_pristine_while_follow_tracks_patches():
    model = build_model()
    binding = build_binding(model)
    binding["legend"][0]["entries"] = [
        {"index": 0, "label": "follows red", "series": "s1"},
        {"index": 1, "label": "note only"},
    ]
    base = run_worker(scaffold_request(model) | {"legend": binding["legend"]})
    assert base["ok"], base.get("error")
    custom = legend_entry_of(base, "axes_0.legend.texts_1")
    # custom 条目无源（_filtered_manifest 口径），示意线 = 代理缺省样式
    assert "source_gid" not in custom and "binding_default" not in custom
    assert field(element(base, "axes_0.legend.texts_1"), "handle_color")["value"] == "#000000"
    follow = legend_entry_of(base, "axes_0.legend.texts_0")
    assert follow["binding_default"] == "follow_source"
    # 系列改色 → follow 条目示意线同步（sync_legends 语义，无 handle_* override）
    patched = run_worker(
        scaffold_request(model, [{"gid": "axes_0.lines_0", "prop": "color", "value": "#00aa00"}])
        | {"legend": binding["legend"]}
    )
    assert patched["ok"] and not patched["warnings"] and patched["unrestored"] == 0
    assert field(element(patched, "axes_0.legend.texts_0"), "handle_color")["value"] == "#00aa00"
    # custom 条目示意线保持 pristine（黑色代理），不从系列色捕获
    assert field(element(patched, "axes_0.legend.texts_1"), "handle_color")["value"] == "#000000"
    assert legend_entry_of(patched, "axes_0.legend.texts_1") == {"index": 1}


def test_worker_refuses_image_series_follow_at_the_contract_gate():
    model = build_model()
    model["series"] = model["series"][:1]
    model["series"][0] = {
        **model["series"][0],
        "kind": "image",
        "x": "-",
        "y": "-",
        "grid": "g1",
        "style": {"cmap": "viridis", "vmin": 0.0, "vmax": 1.0},
    }
    model["dataset"]["grids"] = {"g1": {"values": [[0.1, 0.2], [0.3, 0.4]]}}
    model["data_digest"] = chartmodel.digest_of(model["dataset"])
    legend = [
        {
            "id": "leg1",
            "node": "legend_region",
            "rect": [70.0, 10.0, 25.0, 14.0],
            "entries": [{"index": 0, "label": "heat", "series": "s1"}],
        }
    ]
    response = run_worker(scaffold_request(model) | {"legend": legend})
    assert not response["ok"]
    assert response["code"] == "chart_model_invalid"
    assert "image" in response["error"]


def test_direct_legacy_profile_refuses_legend_defensively():
    model, binding = build_model(), build_binding(build_model())
    with pytest.raises(ValueError, match="legend"):
        svgchart.scaffold_fragment(
            model, FRAME, render_profile="legacy-scaffold/1", legend=binding["legend"]
        )


def test_worker_invalid_legend_contract_is_rejected_cleanly():
    model = build_model()
    binding = build_binding(model)
    binding["legend"][0]["entries"][1]["series"] = "ghost"
    response = run_worker(scaffold_request(model) | {"legend": binding["legend"]})
    assert not response["ok"]
    assert response["code"] == "chart_model_invalid"
