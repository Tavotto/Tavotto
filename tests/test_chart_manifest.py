"""计算 scaffold 的 SVG 与原生 manifest 同 draw 合同，不代替最终组合验证。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto.engine import pool

REPO = Path(__file__).resolve().parents[1]
NS = "{http://www.w3.org/2000/svg}"

SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("agg")
from matplotlib.figure import Figure
from tavotto.engine.chartmanifest import render_scaffold, ChartManifestError

FRAME = {"view_box":[10,20,360,252], "w_pt":720, "h_pt":504, "preserve":None}
out = {}

def figure(cls=Figure):
    fig = cls(figsize=(5, 3.5), dpi=113)
    ax = fig.add_axes([.1,.15,.8,.7])
    ax.set(xlim=(0,10), ylim=(0,20), xlabel="X unit", ylabel="Y unit")
    ax.set_gid("tv-axes-ax1")
    return fig, ax

def render(name, fig, owned, patches=(), **kw):
    result = render_scaffold(fig, owned, FRAME, list(patches), **kw)
    result["fragment"] = result["fragment"].decode("utf-8")
    for key in ("font_families", "font_latin_only_families", "font_family_names"):
        result["manifest"].pop(key, None)
    out[name] = result
    return result

def line_case(name, patches=()):
    fig, ax = figure()
    line, = ax.plot([2,8],[5,15],gid="tv-series-s1")
    return render(name,fig,{"axes:ax1":ax,"series:s1":line},patches)

line_case("line")
line_case("repeated")
line_case("edited", [
    {"gid":"axes_0.lines_0","prop":"color","value":"#00ff00"},
    {"gid":"axes_0","prop":"xlim","value":[0,20]},
    {"gid":"axes_0.lines_0","prop":"linewidth","value":3},
    {"gid":"axes_0.lines_0","prop":"color","value":"#ff0000"},
])

fig, ax = figure()
scatter = ax.scatter([2,8],[5,15],s=[36,81],gid="tv-series-s1")
render("scatter",fig,{"axes:ax1":ax,"series:s1":scatter})

fig, ax = figure()
bars = ax.bar([2,8],[5,15],width=1,color="#336699")
render("bar",fig,{"axes:ax1":ax,"series:s1":bars},[
    {"gid":"axes_0.barseries_0.bar_0","prop":"facecolor","value":"#ff0000"},
    {"gid":"axes_0.barseries_0","prop":"facecolor","value":"#00ff00"},
])

fig, ax = figure()
errors = ax.errorbar([2,8],[5,15],yerr=[1,2],fmt="o-",capsize=4)
render("errorbar",fig,{"axes:ax1":ax,"series:s1":errors})

fig, ax = figure()
errors = ax.errorbar([2,8],[5,15],yerr=[1,2],fmt="o-",capsize=4)
render("errorbar_members",fig,{"axes:ax1":ax,"series:s1":errors.lines})

fig, ax = figure()
bars = ax.bar([2,8],[5,15],width=1,yerr=[1,2],capsize=4)
render("bar_with_error",fig,{"axes:ax1":ax,"series:s1":bars})

fig, ax = figure()
image = ax.imshow([[0.,1.],[2.,3.]],extent=(0,10,0,20),origin="lower",aspect="auto")
render("image",fig,{"axes:ax1":ax,"series:s1":image})

fig, ax = figure()
image = ax.imshow([[0.,1.],[2.,3.]],extent=(0,10,0,20),origin="lower",aspect="auto")
render("image_without_axes",fig,{"series:s1":image})

def colorbar_case(name,patches=()):
    fig, ax = figure()
    image = ax.imshow([[0.,1.],[2.,3.]],extent=(0,10,0,20),origin="lower",aspect="auto")
    cb = fig.colorbar(image,ax=ax)
    return render(name,fig,{"axes:ax1":ax,"series:s1":image,"axes:cbaxis":cb.ax,"colorbar:cb1":cb},patches)
cb_result = colorbar_case("colorbar")
cb_entry = next(e for e in cb_result["manifest"]["elements"] if e["role"]=="colorbar")
colorbar_case("colorbar_alias",[{"gid":cb_entry["colorbar_key"],"prop":"cmap","value":"plasma"}])

fig, ax = figure()
first, = ax.plot([2,8],[5,15],label="Owned")
ax.plot([2,8],[15,5],label="Other")
ax.set_title("Original title")
ax.legend()
render("unowned",fig,{"axes:ax1":ax,"series:s1":first})

fig, ax = figure()
first, = ax.plot([2,8],[5,15],label="Owned")
legend = ax.legend()
render("legend",fig,{"series:s1":first,"legend:leg1":legend})

fig, ax = figure()
text = fig.text(.2,.8,"Calibrated",family="DejaVu Sans",fontsize=12)
render("text",fig,{"text:note":text},[
    {"gid":"fig.texts_0","prop":"fontsize","value":18},
    {"gid":"fig.texts_0","prop":"pos_frac","value":[.35,.25]},
])

class DrawFigure(Figure):
    history = None
    target = None
    def draw(self, renderer):
        if self.history is None:
            self.history = []
        self.history.append(type(renderer).__name__)
        if self.target is not None:
            self.target.set_ydata([len(self.history),15])
        return super().draw(renderer)
fig, ax = figure(DrawFigure)
line, = ax.plot([2,8],[5,15])
fig.target = line
final = render("last_draw",fig,{"series:s1":line})
final["draw_history"] = fig.history

fig, ax = figure()
line, = ax.plot([2,8],[5,15])
render("failed_patch",fig,{"series:s1":line},[
    {"gid":"axes_0.lines_0","prop":"color","value":"not-a-valid-color"},
])

def refused(name, make, patches=(), frame=FRAME):
    fig, owned = make()
    try:
        render_scaffold(fig,owned,frame,list(patches))
    except ChartManifestError as exc:
        out[name] = {"code":exc.code,"details":exc.details}
    else:
        out[name] = {"code":"NOT_REFUSED"}

def owned_line():
    fig, ax = figure()
    line, = ax.plot([2,8],[5,15])
    return fig, {"series:s1":line}

refused("unowned_patch",owned_line,[{"gid":"axes_0","prop":"xlim","value":[0,20]}])
refused("missing_patch",owned_line,[{"gid":"original_scene.note","prop":"text","value":"changed"}])
refused("bad_patch",owned_line,[{"gid":"axes_0.lines_0","prop":"linewidth","value":float("nan")}])
refused("bad_frame",owned_line,frame=dict(FRAME,view_box=[0,0,0,252]))

def overlap():
    fig, owned = owned_line()
    return fig, dict(owned, **{"series:s2":owned["series:s1"]})
refused("overlap",overlap)

def different_figure():
    fig, _ = figure()
    _, owned = owned_line()
    return fig, owned
refused("different_figure",different_figure)

# 全局 tight 不应悄悄裁掉 scaffold 坐标原点/尺寸。
with matplotlib.rc_context({"savefig.bbox":"tight"}):
    line_case("tight_rc")
print(json.dumps(out,ensure_ascii=True,allow_nan=False))
"""


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    try:
        worker = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("需要安装 matplotlib 的 worker 解释器")
    completed = subprocess.run(
        [worker, "-I", "-c", SCRIPT, str(REPO / "src")],
        cwd=tmp_path_factory.mktemp("chart-manifest"),
        env=dict(os.environ, MPLBACKEND="Agg"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def element(result, gid):
    return next(el for el in result["manifest"]["elements"] if el["gid"] == gid)


def svg_group(result, gid):
    return next(el for el in ET.fromstring(result["fragment"]).iter() if el.get("id") == gid)


def values(el):
    return {field["prop"]: field["value"] for field in el["editable"] if "value" in field}


def numbers(path):
    return [float(n) for n in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?", path)]


def test_import_does_not_load_scientific_stack(tmp_path):
    code = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "from tavotto.engine import chartmanifest; "
        "assert not any(n in sys.modules for n in ('matplotlib','numpy','PIL'))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(REPO / "src")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_native_line_geometry_matches_independent_data_formula_and_svg(report):
    result = report["line"]
    line = element(result, "axes_0.lines_0")
    expected = [
        [0.1 + 0.8 * 2 / 10, 0.85 - 0.7 * 5 / 20],
        [0.1 + 0.8 * 8 / 10, 0.85 - 0.7 * 15 / 20],
    ]
    for actual, want in zip(line["geometry"]["paths"][0]["points"], expected, strict=True):
        assert actual == pytest.approx(want)
    path = next(svg_group(result, "axes_0.lines_0").iter(NS + "path"))
    coords = numbers(path.get("d"))
    assert coords == pytest.approx(
        [expected[0][0] * 360, expected[0][1] * 252, expected[1][0] * 360, expected[1][1] * 252]
    )


def test_style_range_and_last_wins_are_measured_from_final_svg(report):
    result = report["edited"]
    line = element(result, "axes_0.lines_0")
    assert values(element(result, "axes_0"))["xlim"] == [0, 20]
    assert values(line)["color"] == "#ff0000"
    assert values(line)["linewidth"] == 3
    assert line["geometry"]["paths"][0]["points"][0] == pytest.approx([0.18, 0.675])
    path = next(svg_group(result, "axes_0.lines_0").iter(NS + "path"))
    assert "stroke: #ff0000" in path.get("style")
    assert "stroke-width: 3" in path.get("style")
    assert numbers(path.get("d"))[:2] == pytest.approx([0.18 * 360, 0.675 * 252])
    assert result["unrestored"] == 0 and result["warnings"] == []


def test_semantic_roles_and_native_gids_are_separate_and_dom_members_exist(report):
    result = report["line"]
    role = result["roles"]["series:s1"]
    assert role == {
        "primary_gid": "axes_0.lines_0",
        "gids": ["axes_0.lines_0"],
        "svg_ids": ["axes_0.lines_0"],
        "input_gids": ["tv-series-s1"],
    }
    assert "tv-series-s1" not in result["fragment"]
    assert result["gid_roles"]["axes_0.xlabel"] == "axes:ax1"
    assert "axes_0.xaxis" in result["roles"]["axes:ax1"]["svg_ids"]
    ids = {el.get("id") for el in ET.fromstring(result["fragment"]).iter()}
    for role in result["roles"].values():
        assert set(role["svg_ids"]) <= ids


def test_coordinates_explicitly_stop_before_composed_scene(report):
    result = report["line"]
    coords = result["coordinates"]
    assert coords["composed"] is False
    assert coords["fragment_to_scene_user"] == [1, 0, 0, 1, 10, 20]
    assert coords["scene_user_to_pt"] == [2, 0, 0, 2, -20, -40]
    assert coords["scaffold_size_pt"] == [360, 252]
    assert coords["scene_size_pt"] == [720, 504]
    # native manifest 量的是 scaffold Figure，不冒充原场景物理大小。
    assert result["manifest"]["size_mm"] == [127, 88.9]


def test_scatter_centres_and_marker_sizes_have_real_geometry(report):
    el = element(report["scatter"], "axes_0.scatter_0")
    geometry = el["geometry"]
    assert geometry["kind"] == "multi_path"
    for path, centre, diameter in zip(
        geometry["paths"], [[0.26, 0.675], [0.74, 0.325]], [6, 9], strict=True
    ):
        xs, ys = zip(*path["points"], strict=True)
        assert [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2] == pytest.approx(centre)
        assert (max(xs) - min(xs)) * 360 == pytest.approx(diameter, abs=0.004)
        assert path["closed"] is True


def test_bar_proxy_native_hierarchy_members_and_native_narrow_overrides(report):
    result = report["bar"]
    role = result["roles"]["series:s1"]
    assert "axes_0.barseries_0" in role["gids"]
    assert {"axes_0.barseries_0.bar_0", "axes_0.barseries_0.bar_1"} <= set(role["gids"])
    assert "axes_0.barseries_0" not in role["svg_ids"]  # native proxy is not a DOM group
    geometry = element(result, "axes_0.barseries_0")["geometry"]
    assert len(geometry["paths"]) == 2
    assert geometry["paths"][0]["points"][0] == pytest.approx([0.22, 0.85])
    assert geometry["paths"][1]["points"][2] == pytest.approx([0.78, 0.325])
    # 原生应用器保留窄成员覆盖；适配器不能另造一套组/成员 setter 顺序。
    for gid, color in (
        ("axes_0.barseries_0.bar_0", "#ff0000"),
        ("axes_0.barseries_0.bar_1", "#00ff00"),
    ):
        assert values(element(result, gid))["facecolor"] == color
        path = next(svg_group(result, gid).iter(NS + "path"))
        assert f"fill: {color}" in path.get("style")


def test_errorbar_proxy_has_native_geometry_and_actual_svg_members(report):
    result = report["errorbar"]
    role = result["roles"]["series:s1"]
    assert role["primary_gid"] == "axes_0.errorbar_0"
    assert role["svg_ids"] and role["primary_gid"] not in role["svg_ids"]
    assert all(gid.startswith("axes_0.errorbar_0.members_") for gid in role["svg_ids"])
    geom = element(result, role["primary_gid"])["geometry"]
    assert geom["kind"] == "multi_path" and len(geom["paths"]) == 7
    assert geom["paths"][-1]["points"][0] == pytest.approx([0.74, 0.395])
    assert geom["paths"][-1]["points"][1] == pytest.approx([0.74, 0.255])
    assert element(result, role["primary_gid"])["bbox"][3] >= 0.45  # errors extend y=4..17


def test_explicit_members_recover_same_errorbar_ownership_and_svg(report):
    assert report["errorbar_members"]["fragment"] == report["errorbar"]["fragment"]
    assert report["errorbar_members"]["roles"] == report["errorbar"]["roles"]


def test_bar_container_owns_its_real_errorbar_members_without_claiming_neighbours(report):
    result = report["bar_with_error"]
    gids = result["roles"]["series:s1"]["gids"]
    # 原生序号按 ax.containers：bar 的 errorbar 先登记，柱容器占序号 1。
    assert "axes_0.barseries_1" in gids and "axes_0.errorbar_0" in gids
    assert result["roles"]["series:s1"]["primary_gid"] == "axes_0.barseries_1"
    assert result["roles"]["series:s1"]["svg_ids"]
    assert result["unrestored"] == 0


def test_colorbar_uses_native_proxy_geometry_real_svg_members_and_alias_target(report):
    result = report["colorbar_alias"]
    colorbar = element(result, "axes_1.colorbar")
    assert colorbar["geom_gid"] == "axes_1"
    assert values(colorbar)["cmap"] == "plasma"
    assert result["roles"]["colorbar:cb1"]["svg_ids"]
    assert result["unrestored"] == 0 and result["warnings"] == []


def test_image_geometry_uses_real_axes_and_embedded_svg_image(report):
    result = report["image"]
    image = element(result, "axes_0.images_0")
    assert image["geom_gid"] == "axes_0"
    assert image["bbox"] == pytest.approx([0.1, 0.15, 0.8, 0.7])
    node = next(svg_group(result, "axes_0.images_0").iter(NS + "image"))
    assert node.get("{http://www.w3.org/1999/xlink}href").startswith("data:image/png;base64,")
    assert float(node.get("width")) == pytest.approx(0.8 * 360, abs=1)
    assert float(node.get("height")) == pytest.approx(0.7 * 252, abs=1)


def test_unowned_axes_are_not_exposed_as_image_resize_authority(report):
    image = element(report["image_without_axes"], "axes_0.images_0")
    assert "geom_gid" not in image
    assert image["resizable"] is False
    assert "axes_0" in report["image_without_axes"]["unowned_gids"]


def test_axes_furniture_does_not_take_over_other_data_title_or_legend(report):
    result = report["unowned"]
    native = {el["gid"] for el in result["manifest"]["elements"]}
    for gid in ("figure", "axes_0.lines_1", "axes_0.title", "axes_0.legend"):
        assert gid not in native and gid in result["unowned_gids"]
    assert "Original title" in result["fragment"] and "Other" in result["fragment"]
    assert "axes_0.xlabel" in native and "axes_0.xticks" in native


def test_explicit_legend_reuses_native_text_hierarchy(report):
    result = report["legend"]
    gids = result["roles"]["legend:leg1"]["gids"]
    assert "axes_0.legend" in gids and "axes_0.legend.texts_0" in gids
    assert element(result, "axes_0.legend")["bbox"][2] > 0
    assert "fontsize" in values(element(result, "axes_0.legend.texts_0"))


def test_text_uses_vector_font_measurement_and_same_svg_baseline(report):
    result = report["text"]
    text = element(result, "fig.texts_0")
    assert values(text)["fontsize"] == 18
    assert text["face"] == "DejaVu Sans" and not text.get("glyphs_missing")
    assert text["anchor"] == pytest.approx([0.35, 0.25])
    assert text["bbox"][2] > 0.2 and text["bbox"][3] > 0.04
    node = next(svg_group(result, "fig.texts_0").iter(NS + "text"))
    assert float(node.get("x")) == pytest.approx(0.35 * 360)
    assert float(node.get("y")) == pytest.approx(0.25 * 252)
    assert "18px" in node.get("style") and "DejaVu Sans" in node.get("style")


def test_manifest_measures_last_svg_draw_without_extra_geometry_draw(report):
    result = report["last_draw"]
    history = result["draw_history"]
    assert history[-1] == "MixedModeRenderer" and len(history) >= 2
    y = 0.85 - 0.7 * len(history) / 20
    point = element(result, "axes_0.lines_0")["geometry"]["paths"][0]["points"][0]
    assert point == pytest.approx([0.26, y])
    path = next(svg_group(result, "axes_0.lines_0").iter(NS + "path"))
    assert numbers(path.get("d"))[:2] == pytest.approx([0.26 * 360, y * 252])


def test_setter_failure_remains_explicit(report):
    result = report["failed_patch"]
    assert result["unrestored"] == 1 and result["warnings"]
    assert values(element(result, "axes_0.lines_0"))["color"] != "not-a-valid-color"


@pytest.mark.parametrize("case", ["unowned_patch", "missing_patch"])
def test_complete_overrides_never_silently_drop_unowned_scene_targets(report, case):
    assert report[case]["code"] == "chart_manifest_target_unowned"


@pytest.mark.parametrize(
    "case,code",
    [
        ("bad_patch", "chart_manifest_patches_invalid"),
        ("bad_frame", "chart_manifest_frame_invalid"),
        ("overlap", "chart_manifest_ownership_invalid"),
        ("different_figure", "chart_manifest_ownership_invalid"),
    ],
)
def test_invalid_adapter_inputs_fail_closed(report, case, code):
    assert report[case]["code"] == code


def test_deterministic_and_global_tight_rc_does_not_change_scaffold_frame(report):
    assert report["line"]["fragment"] == report["repeated"]["fragment"]
    assert report["line"]["fragment"] == report["tight_rc"]["fragment"]
