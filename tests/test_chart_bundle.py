"""C 路径核心合同：只靠导出字节冷重放；不以 metadata 摘要冒充可见等价。"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto import chartbundle
from tavotto.chartbundle import ChartBundleError, embed_chart, extract_chart, verify_replay
from tavotto.engine import chartmodel, chartprofile
from tavotto.rendercore.svgcheck import parse_svg

REPO = Path(__file__).resolve().parents[1]
SOURCE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100">'
    b'<g id="old_axis"><text x="2" y="98">old tick</text></g>'
    b'<path id="curve" d="M10 90L90 10" fill="none" stroke="blue"/>'
    b'<text x="30" y="8">unbound title</text></svg>'
)
RENDERED = SOURCE.replace(b"M10 90L90 10", b"M10 90L50 70L90 10")


def chart(source=SOURCE):
    dataset = {"columns": {"x": [0.0, 5.0, 10.0], "y": [0.0, 25.0, 100.0]}}
    model = {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [10.0, 10.0, 80.0, 80.0],
                "xlim": [0.0, 10.0],
                "ylim": [0.0, 100.0],
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
                "node": "curve",
                "x": "x",
                "y": "y",
                "style": {"color": "#064cff"},
            }
        ],
        "dataset": dataset,
        "scene_digest": hashlib.sha256(source).hexdigest(),
        "data_digest": chartmodel.digest_of(dataset),
        "engine": {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"},
    }
    binding = {
        "model_rev": 1,
        "scene_digest": model["scene_digest"],
        "entries": [
            {"object": "axes:ax1", "node": "old_axis"},
            {"object": "series:s1", "node": "curve"},
        ],
    }
    return {"model": model, "binding": binding}


def _payload(svg):
    root = ET.fromstring(svg)
    return json.loads(base64.b64decode(root.attrib[chartbundle.BUNDLE_ATTRIBUTE]))


def _replace_payload(svg, payload=None, *, raw=None):
    root = ET.fromstring(svg)
    if raw is None:
        raw = json.dumps(payload, separators=(",", ":"), allow_nan=True).encode("utf-8")
    root.set(chartbundle.BUNDLE_ATTRIBUTE, base64.b64encode(raw).decode("ascii"))
    return ET.tostring(root, encoding="utf-8")


def test_bundle_contains_exact_source_and_validated_chart_without_mutating_inputs():
    state = chart()
    before = copy.deepcopy(state)
    bundled = embed_chart(RENDERED, SOURCE, state)
    restored = extract_chart(bundled)
    assert restored is not None
    assert restored.source_svg == SOURCE
    assert restored.chart == before
    assert state == before
    assert restored.model is not state["model"]
    assert restored.binding is not state["binding"]
    assert len(restored.rendered_svg_digest) == 64
    assert parse_svg(bundled).tag.endswith("svg")
    assert chartbundle.MAX_ATTRIBUTE_CHARS < chartbundle._SVG_BUDGET.max_attribute_chars
    assert _payload(bundled)["source"]["encoding"] == "base64"


def test_plain_svg_and_old_model_only_payload_do_not_claim_cold_recovery():
    assert extract_chart(SOURCE) is None
    legacy = _replace_payload(SOURCE, chart())
    assert extract_chart(legacy) is None
    # 即使旧版不具备原场景，它也不是任意非 JSON 内容的容器。
    with pytest.raises(ChartBundleError, match="chart_model_invalid"):
        extract_chart(_replace_payload(SOURCE, raw=b"{broken"))


def test_embedding_is_idempotent_and_does_not_nest_previous_export():
    first = embed_chart(RENDERED, SOURCE, chart())
    second = embed_chart(first, SOURCE, chart())
    assert first == second
    with pytest.raises(ChartBundleError, match="nested chart"):
        embed_chart(RENDERED, first, chart(first))


def test_visible_changes_with_retained_metadata_are_rejected():
    exported = embed_chart(RENDERED, SOURCE, chart())
    changed = exported.replace(b"M10 90L50 70L90 10", b"M10 90L50 10L90 10")
    assert changed != exported
    with pytest.raises(ChartBundleError, match="chart_source_mismatch"):
        extract_chart(changed)


def test_attribute_order_namespace_prefix_and_inert_metadata_do_not_change_identity():
    exported = embed_chart(RENDERED, SOURCE, chart())
    root = ET.fromstring(exported)
    root.attrib = dict(reversed(list(root.attrib.items())))
    ET.SubElement(root, "{http://www.w3.org/2000/svg}metadata").text = "inert note"
    # 不通过修改进程级 ElementTree namespace registry 来制造副作用。
    reserialized = ET.tostring(root, encoding="utf-8").replace(b"ns0:", b"other:")
    reserialized = reserialized.replace(b"xmlns:ns0=", b"xmlns:other=")
    restored = extract_chart(reserialized)
    assert restored is not None
    assert restored.source_svg == SOURCE


def test_digest_is_consistency_only_and_does_not_claim_replayed_appearance():
    # 故意嵌入不匹配模型的可见图：本层仍只证明包内部一致性。
    exported = embed_chart(SOURCE, SOURCE, chart())
    assert extract_chart(exported) is not None
    # 实际 replay 对照是接线层必须另做的门，不能把此成功当成等价证人。
    assert b"M10 90L50 70L90 10" not in exported


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p.update(schema="tavotto.chart-bundle/3"), "chart_model_unsupported"),
        (lambda p: p.update(schema="other/1"), "chart_model_invalid"),
        (lambda p: p.update(script="run.py"), "chart_model_invalid"),
        (lambda p: p["source"].update(path="C:/private/figure.svg"), "chart_model_invalid"),
        (lambda p: p["source"].update(encoding="zlib+base64"), "chart_model_invalid"),
        (lambda p: p["source"].update(sha256="0" * 64), "chart_source_mismatch"),
        (lambda p: p["source"].update(bytes=len(SOURCE) - 1), "chart_source_mismatch"),
        (lambda p: p["source"].update(svg="%%"), "chart_model_invalid"),
        (lambda p: p["model"].update(script="evil.py"), "chart_model_invalid"),
        (lambda p: p["model"]["dataset"]["columns"]["y"].__setitem__(0, 12), "chart_data_changed"),
        (lambda p: p["model"].update(scene_digest="0" * 64), "chart_source_mismatch"),
        (lambda p: p["binding"].update(model_rev=2), "chart_binding_stale"),
        (lambda p: p["binding"]["entries"][1].update(node="missing"), "chart_node_missing"),
        (lambda p: p["rendered"].update(canonical_sha256="0" * 64), "chart_source_mismatch"),
    ],
)
def test_closed_payload_and_identity_checks(mutation, code):
    exported = embed_chart(RENDERED, SOURCE, chart())
    payload = _payload(exported)
    mutation(payload)
    with pytest.raises(ChartBundleError) as caught:
        extract_chart(_replace_payload(exported, payload))
    assert caught.value.code == code


def test_legacy_default_artifact_keeps_the_pre_v2_exact_bytes(monkeypatch):
    # 该摘要在引入v2之前采自原embed_chart；隔离进程级 namespace registry。
    monkeypatch.setattr(ET, "_namespace_map", {})
    exported = embed_chart(RENDERED, SOURCE, chart())
    assert len(exported) == 2240
    assert hashlib.sha256(exported).hexdigest() == (
        "0c3316ab6778340bfd83fb17c5062284f7e3876bcb2182df79df660155941069"
    )
    assert (
        embed_chart(
            RENDERED, SOURCE, chart(), render_profile=chartprofile.LEGACY_PROFILE, overrides=[]
        )
        == exported
    )
    restored = extract_chart(exported)
    assert restored.render_profile == chartprofile.LEGACY_PROFILE
    assert restored.overrides == []
    assert set(restored.chart) == {"model", "binding"}
    assert _payload(exported)["schema"] == chartbundle.BUNDLE_SCHEMA
    assert set(_payload(exported)) == {"schema", "model", "binding", "source", "rendered"}


def native_bundle(edits=()):
    return embed_chart(
        RENDERED, SOURCE, chart(), render_profile=chartprofile.NATIVE_PROFILE, overrides=edits
    )


def test_native_v2_roundtrip_retains_explicit_profile_and_complete_original_edits():
    edits = [
        {"gid": "axes_0.lines_0", "prop": "color", "value": "red", "identity": "object:one"},
        {"gid": "axes_0", "prop": "xlim", "value": [0, 20]},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "blue"},
    ]
    before = copy.deepcopy(edits)
    exported = native_bundle(edits)
    payload = _payload(exported)
    assert payload["schema"] == chartbundle.BUNDLE_SCHEMA_V2
    assert payload["render_profile"] == chartprofile.NATIVE_PROFILE
    assert payload["overrides"] == before
    restored = extract_chart(exported)
    assert restored.source_svg == SOURCE
    assert restored.overrides == before
    assert restored.render_profile == chartprofile.NATIVE_PROFILE
    assert restored.chart == chart()
    assert edits == before
    restored.overrides[0]["value"] = "green"
    assert edits == before


def test_native_v2_complete_profile_roundtrip_is_accepted():
    """native-manifest/2 自本步起是受支持 profile：包层接受并原样保留身份。"""
    edits = [{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}]
    exported = embed_chart(
        RENDERED, SOURCE, chart(), render_profile=chartprofile.COMPLETE_PROFILE, overrides=edits
    )
    payload = _payload(exported)
    assert payload["schema"] == chartbundle.BUNDLE_SCHEMA_V2
    assert payload["render_profile"] == chartprofile.COMPLETE_PROFILE
    restored = extract_chart(exported)
    assert restored.render_profile == chartprofile.COMPLETE_PROFILE
    assert restored.overrides == edits
    assert restored.chart == chart()


@pytest.mark.parametrize("missing", ["render_profile", "overrides"])
def test_v2_cannot_silently_default_a_missing_render_contract(missing):
    exported = native_bundle()
    payload = _payload(exported)
    del payload[missing]
    with pytest.raises(ChartBundleError, match="chart_model_invalid"):
        extract_chart(_replace_payload(exported, payload))


@pytest.mark.parametrize(
    "updates,code",
    [
        # native-manifest/2 自本步起是受支持 profile（NATIVE_PROFILES），换成
        # 仍不存在的 /3 继续钉「闭集外拒收」这条不变量。
        ({"render_profile": "native-manifest/3"}, "chart_model_unsupported"),
        ({"render_profile": {"module": "evil"}}, "chart_model_unsupported"),
        ({"overrides": None}, "chart_model_invalid"),
        (
            {"overrides": [{"gid": "a", "prop": "text", "value": "a", "script": "evil.py"}]},
            "chart_model_invalid",
        ),
        (
            {"overrides": [{"gid": "a", "prop": "text", "value": float("nan")}]},
            "chart_model_invalid",
        ),
        ({"script": "evil.py"}, "chart_model_invalid"),
    ],
)
def test_v2_profile_edits_and_top_level_fields_are_closed(updates, code):
    exported = native_bundle()
    payload = _payload(exported)
    payload.update(updates)
    with pytest.raises(ChartBundleError) as raised:
        extract_chart(_replace_payload(exported, payload))
    assert raised.value.code == code


def test_v1_cannot_smuggle_native_fields_or_edits_into_legacy_rendering():
    edits = [{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}]
    with pytest.raises(ChartBundleError, match="legacy"):
        embed_chart(RENDERED, SOURCE, chart(), overrides=edits)
    exported = embed_chart(RENDERED, SOURCE, chart())
    payload = _payload(exported)
    payload.update(render_profile=chartprofile.NATIVE_PROFILE, overrides=edits)
    with pytest.raises(ChartBundleError, match="chart_model_invalid"):
        extract_chart(_replace_payload(exported, payload))
    native = native_bundle(edits)
    payload = _payload(native)
    payload["render_profile"] = chartprofile.LEGACY_PROFILE
    with pytest.raises(ChartBundleError, match="legacy"):
        extract_chart(_replace_payload(native, payload))


def test_native_model_binding_and_edits_share_the_existing_total_budget(monkeypatch):
    from tavotto.engine import patchspec

    edits = [{"gid": "axes_0.lines_0", "prop": "label", "value": "x" * 100}]
    exported = native_bundle(edits)
    limit = len(chartbundle._json_bytes(chart())) + 16
    assert len(chartbundle._json_bytes(edits)) < limit
    monkeypatch.setattr(chartbundle, "MAX_CHART_BYTES", limit)
    monkeypatch.setattr(
        patchspec,
        "canonicalize_with_diagnostics",
        lambda _: pytest.fail("超出总状态预算后进入patch校验"),
    )
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        native_bundle(edits)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)


@pytest.mark.parametrize("raw", [b'{"model":{},"model":{},"binding":{}}', b"NaN", b"[]"])
def test_duplicate_keys_nonfinite_and_invalid_json_shape_are_rejected(raw):
    with pytest.raises(ChartBundleError, match="chart_model_invalid"):
        extract_chart(_replace_payload(SOURCE, raw=raw))


def test_payload_and_model_budgets_are_enforced_before_import(monkeypatch):
    exported = embed_chart(RENDERED, SOURCE, chart())
    monkeypatch.setattr(chartbundle, "MAX_ATTRIBUTE_CHARS", 32)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)
    monkeypatch.undo()
    monkeypatch.setattr(chartbundle, "MAX_PAYLOAD_BYTES", 20)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)
    monkeypatch.undo()
    monkeypatch.setattr(chartbundle, "MAX_CHART_BYTES", 50)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        embed_chart(RENDERED, SOURCE, chart())
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)


def test_source_budget_is_enforced_for_embed_and_extract(monkeypatch):
    exported = embed_chart(RENDERED, SOURCE, chart())
    monkeypatch.setattr(chartbundle, "MAX_SOURCE_BYTES", len(SOURCE) - 1)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        embed_chart(RENDERED, SOURCE, chart())
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)


def test_json_depth_and_nodes_are_bounded(monkeypatch):
    exported = embed_chart(RENDERED, SOURCE, chart())
    monkeypatch.setattr(chartbundle, "MAX_JSON_DEPTH", 2)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)
    monkeypatch.undo()
    monkeypatch.setattr(chartbundle, "MAX_JSON_NODES", 8)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        extract_chart(exported)


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (SOURCE.replace(b"</svg>", b"<script>raise()</script></svg>"), "svg_unsafe_element"),
        (
            SOURCE.replace(b"</svg>", b'<image href="file:///secret.png"/></svg>'),
            "svg_external_resource",
        ),
        (b'<!DOCTYPE svg [<!ENTITY boom "bad">]>' + SOURCE, "svg_dtd_forbidden"),
        (
            SOURCE.replace(b'<g id="old_axis">', b'<g onclick="evil()" id="old_axis">'),
            "svg_unsafe_attribute",
        ),
    ],
)
def test_embedded_source_uses_same_static_security_boundary(source, code):
    # 恶意源的摘要和模型都由攻击者同步更新，仍须在实际源校验处失败。
    with pytest.raises(ChartBundleError) as caught:
        embed_chart(RENDERED, source, chart(source))
    assert caught.value.code == code


def test_node_and_resource_budgets_remain_active(monkeypatch):
    from dataclasses import replace

    original_parse = chartbundle.parse_svg

    def constrained_parse(data):
        return original_parse(data, budget=replace(chartbundle._SVG_BUDGET, max_elements=3))

    monkeypatch.setattr(chartbundle, "parse_svg", constrained_parse)
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        embed_chart(RENDERED, SOURCE, chart())


def test_embedded_image_pixel_budget_is_checked_without_native_image_decoder():
    png_header = (
        b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
        + struct.pack(">II", 100_000, 100_000)
        + b"\x08\x06\0\0\0\0\0\0\0"
    )
    data_url = b"data:image/png;base64," + base64.b64encode(png_header)
    source = SOURCE.replace(b"</svg>", b'<image href="' + data_url + b'"/></svg>')
    with pytest.raises(ChartBundleError, match="svg_resource_limit"):
        embed_chart(RENDERED, source, chart(source))


def test_extract_rechecks_embedded_source_safety_after_all_attacker_hashes_match():
    exported = embed_chart(RENDERED, SOURCE, chart())
    malicious = SOURCE.replace(b"</svg>", b"<script>doBadThings()</script></svg>")
    payload = _payload(exported)
    payload["source"].update(
        svg=base64.b64encode(malicious).decode("ascii"),
        sha256=hashlib.sha256(malicious).hexdigest(),
        bytes=len(malicious),
    )
    state = chart(malicious)
    payload["model"], payload["binding"] = state["model"], state["binding"]
    with pytest.raises(ChartBundleError, match="svg_unsafe_element"):
        extract_chart(_replace_payload(exported, payload))


def test_truncation_and_nested_markers_do_not_become_plain_svg():
    exported = embed_chart(RENDERED, SOURCE, chart())
    with pytest.raises(ChartBundleError):
        extract_chart(exported[:-20])
    root = ET.fromstring(exported)
    list(root)[0].set(chartbundle.BUNDLE_ATTRIBUTE, "e30=")
    with pytest.raises(ChartBundleError, match="nested chart"):
        extract_chart(ET.tostring(root, encoding="utf-8"))


def _run_child(script, request, cwd):
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=json.dumps(request),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
        cwd=cwd,
        env={**os.environ, "TAVOTTO_NO_TELEMETRY": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_export_bytes_alone_replay_after_deleting_source_in_fresh_process(tmp_path):
    """主语是新 python -I 进程，只拿导出 bytes，不存在原 SVG/项目文件。"""
    prelude = f"""
import base64, json, sys
sys.path.insert(0, {str(REPO / "src")!r})
from tavotto.chartbundle import extract_chart, verify_replay
from tavotto.engine import svgchart
from tavotto.svgscene import scene_inventory
from tavotto.svgcompose import compose_scene
req = json.load(sys.stdin)
"""
    replay = """
inventory = scene_inventory(source)
frame = {'view_box': inventory['view_box'], 'w_pt': inventory['w_pt'],
         'h_pt': inventory['h_pt'], 'preserve': None}
out = svgchart.scaffold_fragment(state['model'], frame)
visible = compose_scene(source, state['binding'], out['fragment'], state['model'])
print(json.dumps({'svg': base64.b64encode(visible).decode('ascii'), 'facts': out['facts']}))
"""
    path = tmp_path / "original.svg"
    path.write_bytes(SOURCE)
    state = chart()
    hot = _run_child(
        prelude + "source = base64.b64decode(req['source'])\nstate = req['chart']\n" + replay,
        {"source": base64.b64encode(path.read_bytes()).decode("ascii"), "chart": state},
        tmp_path,
    )
    exported = embed_chart(base64.b64decode(hot["svg"]), path.read_bytes(), state)
    path.unlink()
    cold_dir = tmp_path / "new-project"
    cold_dir.mkdir()
    cold = _run_child(
        prelude
        + "bundle = extract_chart(base64.b64decode(req['exported']))\n"
        + "assert bundle is not None\nsource = bundle.source_svg\nstate = bundle.chart\n"
        + replay
        + "\nverify_replay(bundle, visible)\n",
        {"exported": base64.b64encode(exported).decode("ascii")},
        cold_dir,
    )
    assert not path.exists()
    assert not list(cold_dir.iterdir())
    assert cold["svg"] == hot["svg"]
    assert cold["facts"] == hot["facts"]
    points = cold["facts"]["series_points"]["s1"]
    assert points[1]["x"] == pytest.approx(0.5)
    assert points[1]["y"] == pytest.approx(0.25)
    assert b"unbound title" in base64.b64decode(cold["svg"])


def test_replay_rejects_attacker_resigned_model_source_and_hashes(tmp_path):
    """一致性可被重签，实际重算的曲线则必须与导出可见图相同。"""
    script = f"""
import base64, json, sys
sys.path.insert(0, {str(REPO / "src")!r})
from tavotto.engine import svgchart
from tavotto.svgscene import scene_inventory
from tavotto.svgcompose import compose_scene
req = json.load(sys.stdin)
source = base64.b64decode(req['source'])
state = req['chart']
scene = scene_inventory(source)
frame = {{'view_box': scene['view_box'], 'w_pt': scene['w_pt'],
          'h_pt': scene['h_pt'], 'preserve': None}}
computed = svgchart.scaffold_fragment(state['model'], frame)
rendered = compose_scene(source, state['binding'], computed['fragment'], state['model'])
print(json.dumps({{'svg': base64.b64encode(rendered).decode('ascii'), 'facts': computed['facts']}}))
"""
    original = _run_child(
        script, {"source": base64.b64encode(SOURCE).decode("ascii"), "chart": chart()}, tmp_path
    )
    visible = base64.b64decode(original["svg"])
    normal_bundle = extract_chart(embed_chart(visible, SOURCE, chart()))
    assert normal_bundle is not None
    verify_replay(normal_bundle, visible)

    # 原场景也改字节、绑定身份同步换成新摘要，外观仍携带旧的可见曲线。
    resigned_source = SOURCE.replace(b"</svg>", b"<metadata>source revision 2</metadata></svg>")
    modified = chart(resigned_source)
    modified["model"]["dataset"]["columns"]["y"][1] = 75.0
    modified["model"]["data_digest"] = chartmodel.digest_of(modified["model"]["dataset"])
    bundle = extract_chart(embed_chart(visible, resigned_source, modified))
    assert bundle is not None  # 所有元数据字段、来源和摘要自身一致。
    replay = _run_child(
        script,
        {"source": base64.b64encode(bundle.source_svg).decode("ascii"), "chart": bundle.chart},
        tmp_path,
    )
    actual_replay = base64.b64decode(replay["svg"])
    assert actual_replay != visible
    assert original["facts"]["series_points"]["s1"][1]["y"] == pytest.approx(0.25)
    assert replay["facts"]["series_points"]["s1"][1]["y"] == pytest.approx(0.75)
    with pytest.raises(ChartBundleError, match="chart_source_mismatch"):
        verify_replay(bundle, actual_replay)


def test_replay_cannot_bypass_static_svg_security():
    bundle = extract_chart(embed_chart(RENDERED, SOURCE, chart()))
    assert bundle is not None
    malicious = RENDERED.replace(b"</svg>", b"<script>doBadThings()</script></svg>")
    with pytest.raises(ChartBundleError, match="svg_unsafe_element"):
        verify_replay(bundle, malicious)


def test_v2_native_edits_and_profile_are_verified_by_actual_fresh_replay(tmp_path):
    """库级原生实际绘制，不把元数据摘要当完整app组合/导出资格。"""
    script = f"""
import base64,json,sys
sys.path.insert(0,{str(REPO / "src")!r})
import matplotlib as mpl
mpl.use('Agg')
from matplotlib.figure import Figure
from tavotto.chartbundle import extract_chart
from tavotto.engine import chartprofile,svgchart
from tavotto.engine.chartmanifest import render_scaffold
from tavotto.svgscene import scene_inventory
from tavotto.svgcompose import compose_scene
req=json.load(sys.stdin)
bundle=extract_chart(base64.b64decode(req['bundle'])) if 'bundle' in req else None
source=bundle.source_svg if bundle else base64.b64decode(req['source'])
state=bundle.chart if bundle else req['chart']
profile=bundle.render_profile if bundle else chartprofile.NATIVE_PROFILE
edits=bundle.overrides if bundle else req['overrides']
scene=scene_inventory(source)
frame={{'view_box':scene['view_box'],'w_pt':scene['w_pt'],'h_pt':scene['h_pt'],'preserve':None}}
with mpl.rc_context({{'text.usetex':False}}):
    if profile==chartprofile.NATIVE_PROFILE:
        model=state['model']
        cfg=model['axes'][0]
        fig=Figure(figsize=(frame['w_pt']/72,frame['h_pt']/72),dpi=72)
        x,y,w,h=cfg['rect']
        ax=fig.add_axes([x/100,1-(y+h)/100,w/100,h/100])
        ax.set(xlim=cfg['xlim'],ylim=cfg['ylim'])
        sr=model['series'][0]
        columns=model['dataset']['columns']
        line,=ax.plot(columns[sr['x']],columns[sr['y']],color=sr['style']['color'])
        result=render_scaffold(fig,{{'axes:ax1':ax,'series:s1':line}},frame,edits)
        assert not result['warnings'] and not result['unrestored']
        visible=result['fragment']
    else:
        assert profile==chartprofile.LEGACY_PROFILE and not edits
        result=svgchart.scaffold_fragment(state['model'],frame)
        visible=compose_scene(source,state['binding'],result['fragment'],state['model'])
print(json.dumps({{'svg':base64.b64encode(visible).decode('ascii')}}))
"""
    edits = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#e32636"}]
    hot = _run_child(
        script,
        {"source": base64.b64encode(SOURCE).decode("ascii"), "chart": chart(), "overrides": edits},
        tmp_path,
    )
    visible = base64.b64decode(hot["svg"])
    assert b"#e32636" in visible
    exported = embed_chart(
        visible, SOURCE, chart(), render_profile=chartprofile.NATIVE_PROFILE, overrides=edits
    )
    bundle = extract_chart(exported)
    cold = _run_child(script, {"bundle": base64.b64encode(exported).decode("ascii")}, tmp_path)
    actual = base64.b64decode(cold["svg"])
    assert actual == visible
    verify_replay(bundle, actual)

    for change in ("edits", "profile"):
        payload = _payload(exported)
        if change == "edits":
            payload["overrides"][0]["value"] = "#008000"
        else:
            payload.update(render_profile=chartprofile.LEGACY_PROFILE, overrides=[])
        modified_bytes = _replace_payload(exported, payload)
        modified = extract_chart(modified_bytes)
        assert modified is not None  # 攻击者可保持所有可见摘要不变；这不是重放证人。
        replay = _run_child(
            script, {"bundle": base64.b64encode(modified_bytes).decode("ascii")}, tmp_path
        )
        with pytest.raises(ChartBundleError, match="chart_source_mismatch"):
            verify_replay(modified, base64.b64decode(replay["svg"]))
