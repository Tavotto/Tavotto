"""原图 EPS 公共契约 / 固定 owned action；真实 PS 读取器与进程边界。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tavotto import pdfbackend
from tavotto.engine import chartprocess, chartprofile, envlease, pool
from tavotto.rendercore import epscontainer
from tavotto.rendercore.svgcheck import SVGBudget
from test_native_scene_eps import (
    assert_same_ink,
    pdf_image,
    ps_image,
    svg,
)

# Reuse the existing real process owner/lease/reap and independent reader fixtures.
pytest_plugins = ("test_chart_process", "test_native_scene_eps")


def prepared():
    return pdfbackend.prepare_original_eps(
        svg('<rect id="keep" x="10" y="15" width="20" height="10" fill="green"/>'),
        (100, 100),
    )["payload"]


def test_public_preparer_returns_json_facts_and_binds_exact_full_scene(faces):
    source = svg(
        '<path id="unbound" d="M10 20H80V30H10Z" fill="green"/>'
        '<text x="5" y="50" font-size="10" font-family="sans-serif">Á 原图</text>'
    )
    value = pdfbackend.prepare_original_eps(source, (100, 100))
    assert set(value) == {"payload", "facts", "notes"}
    chartprofile.check_json_budget(value)
    assert json.loads(json.dumps(value)) == value
    assert value["payload"]["source_svg_sha256"] == hashlib.sha256(source).hexdigest()
    assert value["facts"]["paths"] == 1 and value["facts"]["outlined_glyphs"] >= 4
    assert isinstance(value["notes"], list)
    assert "prepare_original_eps" in pdfbackend.__all__


@pytest.mark.parametrize(
    "size", [None, (100,), (True, 100), ("100", 100), (0, 100), (10**1000, 10)]
)
def test_public_invalid_size_has_contract_error_without_loading_fonts(size, monkeypatch):
    from tavotto.rendercore import facade

    monkeypatch.setattr(facade, "provider", lambda: pytest.fail("invalid size loaded fonts"))
    with pytest.raises(pdfbackend.OriginalEPSError) as caught:
        pdfbackend.prepare_original_eps(svg(""), size)
    assert caught.value.code == "native_eps_invalid_payload"


@pytest.mark.parametrize(
    "body,code,operation",
    [
        (
            '<rect width="10" height="10" fill-opacity=".5"/>',
            "unsupported_capability",
            "object_alpha",
        ),
        (
            '<g opacity=".5"><rect width="10" height="10"/></g>',
            "unsupported_capability",
            "group_opacity",
        ),
        (
            '<defs><linearGradient id="g"/></defs><rect width="10" height="10" fill="url(#g)"/>',
            "svgir_gradient",
            None,
        ),
    ],
)
def test_public_errors_preserve_expression_reason_and_params(faces, body, code, operation):
    with pytest.raises(pdfbackend.OriginalEPSError) as caught:
        pdfbackend.prepare_original_eps(svg(body), (100, 100))
    assert caught.value.code == code
    if operation:
        assert caught.value.params["operation"] == operation
        assert caught.value.params["reason"]
    else:
        assert caught.value.params == {}


def test_public_preparation_does_not_import_parent_scientific_stack_or_pdfium(tmp_path):
    script = r"""
import importlib.abc, json, sys
denied={'matplotlib','numpy','pypdfium2','fitz','pymupdf'}
class Missing(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *_args):
        if fullname.split('.')[0] in denied:
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0,Missing())
from tavotto import pdfbackend
source='<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt"><text x="5" y="50">原图 Á</text></svg>'.encode()
value=pdfbackend.prepare_original_eps(source,(100,100))
assert value['facts']['outlined_glyphs']>=4
assert not denied.intersection(sys.modules)
print(json.dumps({'outlined_glyphs':value['facts']['outlined_glyphs'],'denied_loaded':[]}))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", script],
        env=env,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["denied_loaded"] == []


def test_fixed_owned_eps_action_reads_complete_native_scene_with_existing_worker_only(
    child, tmp_path, gs, faces
):
    from tavotto.engine import chartmodel
    from tavotto.svgcompose import compose_native_scene
    from test_chart_native import setup_chart

    source, model, binding, frame = setup_chart(
        '<rect id="keep-green" x="2" y="2" width="7" height="6" fill="#00aa00"/>'
        '<g id="original"><path id="oldaxis"/><path id="curve1"/><path id="curve2"/></g>'
        '<text id="keep-text" x="5" y="98" font-family="sans-serif" font-size="4">原图 Á</text>'
    )
    model["dataset"]["columns"]["y1"] = [0.25, 0.25]
    model["dataset"]["columns"]["y2"] = [0.75, 0.75]
    model["data_digest"] = chartmodel.digest_of(model["dataset"])
    scaffold = child.request(
        {
            "model": model,
            "frame": frame,
            "render_profile": chartprofile.NATIVE_PROFILE,
            "patches": [],
        }
    )
    scaffold["fragment"] = base64.b64decode(scaffold.pop("fragment_b64"), validate=True)
    complete = compose_native_scene(source, binding, scaffold, model)
    assert complete["composition"]["composed"]
    value = pdfbackend.prepare_original_eps(complete["svg"], (100, 100))
    absent = tmp_path / "no-extra-native.json"
    child.startup(
        "import importlib.abc,sys,atexit,json\nfrom pathlib import Path\n"
        "denied={'uharfbuzz','pikepdf','pypdfium2','fitz','pymupdf'}\n"
        "class Missing(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self, fullname, *_args):\n"
        "  if fullname.split('.')[0] in denied: raise ModuleNotFoundError(fullname,name=fullname)\n"
        "sys.meta_path.insert(0,Missing())\n"
        f"atexit.register(lambda:Path({str(absent)!r}).write_text(json.dumps(sorted(denied.intersection(sys.modules)))))\n"
    )
    reply = child.request({"action": "pack_eps", "prepared": value["payload"]})
    assert reply["ok"] is True
    assert json.loads(absent.read_text()) == []
    assert child.owned[-1].confirmed and child.owned[-1].exit_code == 0
    assert (
        chartprocess.active_count() == 0
        and envlease.state_of(sys.executable) == envlease.STATE_IDLE
    )
    assert child.spawned[-1][1] == [sys.executable, "-B", "-m", "tavotto.engine.svgchart"]
    for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt"):
        assert reply[key] == value["payload"][key]
    image = ps_image(tmp_path, gs, base64.b64decode(reply["eps_b64"], validate=True))
    reference, text = pdf_image(tmp_path, complete["svg"], 100, 100, faces)
    assert "原图" in text
    assert image.size == (400, 400) and image.getpixel((4 * 4, 4 * 4)) == (0, 170, 0)
    assert_same_ink(image, reference)


def test_fixed_eps_action_preserves_shared_environment_lease(child, faces):
    payload = {"action": "pack_eps", "prepared": prepared()}
    with envlease.mutating("fixed-eps-test", sys.executable):
        with pytest.raises(pool.WorkerError) as caught:
            child.request(payload)
        assert caught.value.code == envlease.ENVIRONMENT_MUTATING
    assert not child.spawned and chartprocess.active_count() == 0


@pytest.mark.parametrize("kind", ["digest", "schema", "extra_action_field", "unknown_action"])
def test_fixed_eps_action_rejects_bad_payload_in_real_child(child, faces, kind):
    request = {"action": "pack_eps", "prepared": prepared()}
    if kind == "digest":
        request["prepared"]["ps_sha256"] = "0" * 64
    elif kind == "schema":
        request["prepared"]["schema"] = "scene-ps/2"
    elif kind == "extra_action_field":
        request["argv"] = ["unused"]
    else:
        request["action"] = "arbitrary-command"
    reply = child.request(request)
    assert reply["ok"] is False and reply["code"] == "native_eps_invalid_payload"
    assert "eps_b64" not in reply and child.owned[-1].confirmed


def response(payload):
    return {
        "ok": True,
        "engine": {"matplotlib": "m", "numpy": "n", "tavotto": "t"},
        "eps_b64": base64.b64encode(
            b"%!PS-Adobe-3.0 EPSF-3.0\n%%EndComments\n%%EndProlog\nshowpage\n"
        ).decode(),
        **{
            key: payload[key] for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt")
        },
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(eps_b64="???"),
        lambda p: p.update(eps_b64=base64.b64encode(b"%PDF-1.7\n%%EOF\n").decode()),
        lambda p: p.update(eps_b64=base64.b64encode(b"%!PS-Adobe-3.0 EPSF-3.0\n").decode()),
        lambda p: p.update(source_svg_sha256="0" * 64),
        lambda p: p.update(ps_sha256="0" * 64),
        lambda p: p.update(width_pt=99),
        lambda p: p.update(fragment_b64="PHN2Zy8+"),
    ],
)
def test_owned_eps_response_rejects_wrong_bytes_or_frozen_identity(faces, mutate):
    payload = prepared()
    reply = response(payload)
    mutate(reply)
    with pytest.raises(pool.WorkerError) as caught:
        chartprocess._response(json.dumps(reply).encode(), False, prepared=payload)
    assert caught.value.code == "svg_scaffold_invalid"


def test_owned_eps_reply_compares_with_serialized_request_not_later_mutated_dict(child, faces):
    payload = prepared()
    expected = deepcopy(payload)
    child.startup(child.barrier)
    thread, box = child.background({"action": "pack_eps", "prepared": payload})
    from test_chart_process import wait_until

    wait_until(child.marker.exists)
    payload["source_svg_sha256"] = "0" * 64
    payload["width_pt"] = 999
    child.release.touch()
    thread.join(15)
    assert not thread.is_alive() and "error" not in box, box
    assert box["value"]["source_svg_sha256"] == expected["source_svg_sha256"]
    assert box["value"]["width_pt"] == expected["width_pt"]


def test_actual_request_cap_includes_action_envelope_and_json_escaping(child, faces, monkeypatch):
    payload = prepared()
    request = {"action": "pack_eps", "prepared": payload}
    bare = len(chartprocess._json_bytes(payload, chartprocess.MAX_REQUEST_BYTES))
    wrapped = len(chartprocess._json_bytes(request, chartprocess.MAX_REQUEST_BYTES))
    assert bare < wrapped and len(payload["ps_ascii"]) < bare
    # A valid leaf payload can exactly fill the input cap while its action
    # envelope cannot. Reject honestly; don't claim every 32MiB PS traverses it.
    monkeypatch.setattr(chartprocess, "MAX_REQUEST_BYTES", bare)
    with pytest.raises(pool.WorkerError) as caught:
        child.request(request)
    assert caught.value.code == "svg_resource_limit" and not child.spawned


def test_eps_response_uses_shared_leaf_byte_cap(faces, monkeypatch):
    payload = prepared()
    reply = response(payload)
    monkeypatch.setattr(chartprocess, "MAX_EPS_BYTES", 4)
    with pytest.raises(pool.WorkerError) as caught:
        chartprocess._response(json.dumps(reply).encode(), False, prepared=payload)
    assert caught.value.code == "svg_resource_limit"
    assert epscontainer.MAX_BYTES == SVGBudget().max_bytes
