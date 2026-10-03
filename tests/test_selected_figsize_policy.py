"""Strict full-figsize PNG uses the existing artifact context and frame vocabulary."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import artifactcontext, exportreq, figcapture, pool
from test_savefig_capture_params import WORKER_PY, needs_worker
from test_selected_artifact_api import api as api, basis, refused

STRICT = figcapture.SELECTED_FIGSIZE_POLICY
FRAME = {"gid": "figure", "prop": "frame", "value": "figsize"}
SAVEFIG = {**FRAME, "value": "savefig"}
COLOR = {"gid": "axes_0.lines_0", "prop": "color", "value": "red"}
EXPECTED = {"bytes_sha256": "a" * 64, "size_bytes": 1}


@pytest.mark.parametrize("patches", [[FRAME], [SAVEFIG, FRAME, COLOR], [COLOR, FRAME]])
def test_strict_request_uses_effective_last_wins_figsize(patches):
    assert figcapture.artifact_request(STRICT, EXPECTED, patches) == EXPECTED


@pytest.mark.parametrize(
    "patches", [[COLOR], [SAVEFIG], [FRAME, SAVEFIG], [{**FRAME, "value": None}]]
)
def test_strict_request_refuses_missing_or_non_figsize_effective_frame(patches):
    with pytest.raises(figcapture.ArtifactContextError) as error:
        figcapture.artifact_request(STRICT, EXPECTED, patches)
    assert error.value.reason == "unsupported_render_state"
    assert not error.value.retryable
    # The less restrictive existing policy and wholly legacy calls keep their contract.
    assert (
        figcapture.artifact_request(figcapture.SELECTED_ARTIFACT_POLICY, EXPECTED, patches)
        == EXPECTED
    )
    assert figcapture.artifact_request(None, None, patches) is None


def test_first_admission_and_static_copy_are_empty_but_edits_need_expected_identity():
    assert figcapture.artifact_request(STRICT, None, []) is None
    assert figcapture.artifact_request(STRICT, EXPECTED, []) == EXPECTED
    with pytest.raises(figcapture.ArtifactContextError) as error:
        figcapture.artifact_request(STRICT, None, [FRAME])
    assert error.value.reason == "source_changed" and not error.value.retryable


def test_strict_context_retains_policy_and_separates_worker_identity(api):
    context = {
        **artifactcontext.create_context(api.root / "plot.png", "plot.png"),
        "render_policy": STRICT,
    }
    assert figcapture.selected_artifact_context(context) == context
    loose = {**context, "render_policy": figcapture.SELECTED_ARTIFACT_POLICY}
    assert pool._worker_key(str(api.root), "plot.py", context) != pool._worker_key(
        str(api.root), "plot.py", loose
    )
    assert pool._worker_base(str(api.root), "plot.py", context) != pool._worker_base(
        str(api.root), "plot.py", loose
    )
    with pytest.raises(ValueError):
        figcapture.selected_artifact_context(
            {k: v for k, v in context.items() if k != "render_policy"}
        )


def test_api_strict_adoption_and_paired_edits_keep_policy(api):
    request = {"id": "plot.png", "source_policy": STRICT, "patches": []}
    response = api.client.post("/api/engine/render", json=request)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["artifact_source"]["render_policy"] == STRICT
    response = api.client.post(
        "/api/engine/preview_png",
        json={
            **request,
            "patches": [FRAME, COLOR],
            "expected_source": basis(api, "plot.png"),
            "with_manifest": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["artifact_source"]["render_policy"] == STRICT
    assert next(iter(api.workers.values())).builds == 1


@pytest.mark.parametrize("route", ["render", "preview_png"])
def test_api_strict_wrong_frame_refuses_before_worker(api, route):
    refused(
        api.client.post(
            "/api/engine/" + route,
            json={
                "id": "plot.png",
                "source_policy": STRICT,
                "expected_source": basis(api, "plot.png"),
                "patches": [FRAME, SAVEFIG],
                "with_manifest": True,
            },
        ),
        "unsupported_render_state",
    )
    assert not api.workers


def _export_spec(*, canvas=False, patches=(), name="plot.png", expected=None):
    source = {
        "figure_id": name,
        "source_policy": STRICT,
        "overrides": list(patches),
        "expected_source": expected,
    }
    spec = {
        "scope": "original",
        "original": source,
        "formats": ["png"],
        "filename": "copy",
        "ppi": 100,
    }
    if canvas:
        spec.pop("original")
        spec.update(
            scope="canvas",
            canvas={
                "page_w_mm": 20,
                "page_h_mm": 20,
                "objects": [
                    {
                        "type": "panel",
                        "id": source.pop("figure_id"),
                        "x_mm": 0,
                        "y_mm": 0,
                        "w_mm": 10,
                        "h_mm": 10,
                        **source,
                    }
                ],
            },
        )
    return spec


@pytest.mark.parametrize("canvas", [False, True])
def test_export_strict_frame_validation_and_policy_identity(api, canvas):
    expected = basis(api, "plot.png")
    spec = _export_spec(canvas=canvas, patches=[SAVEFIG, FRAME], expected=expected)
    first = exportreq.render_plan_ref(exportreq.normalize(spec), [])
    source = spec["canvas"]["objects"][0] if canvas else spec["original"]
    source["source_policy"] = figcapture.SELECTED_ARTIFACT_POLICY
    assert (
        first["plan_identity"]
        != exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    )
    source["source_policy"], source["overrides"] = STRICT, [FRAME, SAVEFIG]
    refused(api.client.post("/api/export", json=spec), "unsupported_render_state")
    assert not api.workers


def test_strict_static_png_copy_stays_worker_free(api, monkeypatch):
    def copy(source, target, *args, **kwargs):
        shutil.copyfile(source, target)
        return {"px_w": 20, "px_h": 10}

    monkeypatch.setattr(m.pdfbackend, "original_png", copy)
    monkeypatch.setattr(m, "_original_page_pt", lambda *args: (20, 10))
    response = api.client.post("/api/export", json=_export_spec(expected=basis(api, "plot.png")))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert (Path(data["export_dir"]) / data["files"][0]["name"]).read_bytes() == (
        api.root / "plot.png"
    ).read_bytes()
    assert not api.workers


@pytest.mark.parametrize("route", ["render", "export"])
def test_strict_pdf_refuses_even_without_edits_or_worker(api, route):
    request = (
        {"id": "plot.pdf", "source_policy": STRICT, "patches": []}
        if route == "render"
        else _export_spec(name="plot.pdf", expected=basis(api))
    )
    refused(
        api.client.post("/api/engine/render" if route == "render" else "/api/export", json=request),
        "unsupported_render_state",
    )
    assert not api.workers


def _project(root, *, dpi=100, one_shot=False, reverse=False, crop=None, transparent=False):
    bbox = (
        "matplotlib.transforms.Bbox.from_bounds(0, 0, 4, 3)"
        if crop == "explicit-full"
        else repr(None if crop == "rc-tight" else crop)
    )
    png = f"fig.savefig('same.png', dpi={dpi}, bbox_inches={bbox}, transparent={transparent})"
    pdf = "fig.savefig('same.pdf', bbox_inches='tight', pad_inches=.02)"
    saves = [png, pdf] if reverse else [pdf, png]
    script = root / "figure.py"
    script.write_text(
        "\n".join(
            [
                "import matplotlib; matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt",
                "fig, ax = plt.subplots(figsize=(4, 3), dpi=100)",
                "ax.plot([0,1,2], [2,0,3])",
                "ax.set(xlabel='Time', ylabel='Response', title='Full figsize PNG')",
                "fig.tight_layout()" if one_shot else "",
                "matplotlib.rcParams['savefig.bbox'] = 'tight'" if crop == "rc-tight" else "",
                *saves,
                "plt.close('all')",
            ]
        )
    )
    result = subprocess.run(
        [WORKER_PY, str(script)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"figure.py": {"entry": "__main__", "stems": ["same"], "cost": "light"}}}
        )
    )
    return {
        **artifactcontext.create_context(root / "same.png", "same.png"),
        "render_policy": STRICT,
    }


@needs_worker
@pytest.mark.parametrize("dpi", [72, 300])
@pytest.mark.parametrize("one_shot", [False, True], ids=["fixed", "one-shot"])
@pytest.mark.parametrize("reverse", [False, True], ids=["pdf-first", "png-first"])
def test_real_api_strict_admission_frame_preview_export_and_fresh_replay(
    tmp_path, monkeypatch, dpi, one_shot, reverse
):
    context = _project(tmp_path, dpi=dpi, one_shot=one_shot, reverse=reverse)
    expected = {k: context[k] for k in EXPECTED}
    original = {name: (tmp_path / name).read_bytes() for name in ("same.png", "same.pdf")}
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    m.open_project(str(tmp_path))
    client = m.app.test_client()
    fresh = None
    try:

        def render(patches, identity=expected):
            response = client.post(
                "/api/engine/render",
                json={
                    "id": "same.png",
                    "source_policy": STRICT,
                    "expected_source": identity,
                    "patches": patches,
                    "inline_svg": True,
                },
            )
            assert response.status_code == 200, response.get_json()
            return response.get_json()

        initial = render([], None)
        assert initial["artifact_source"] == context
        assert initial["manifest"]["size_mm"] == [101.6, 76.2]
        framed = render([FRAME])
        assert framed["manifest"]["size_mm"] == initial["manifest"]["size_mm"]

        def preview(patches):
            response = client.post(
                "/api/engine/preview_png",
                json={
                    "id": "same.png",
                    "source_policy": STRICT,
                    "expected_source": expected,
                    "patches": patches,
                    "with_manifest": True,
                    "w": 400,
                },
            )
            assert response.status_code == 200, response.get_json()
            return response.get_json()["png"]

        assert preview([]) == preview([FRAME])
        red, blue = [FRAME, COLOR], [FRAME, {**COLOR, "value": "blue"}]
        render(red)
        first = preview(red)
        render(blue)
        assert preview(blue) != first
        render(red)
        assert preview(red) == first
        exported = client.post(
            "/api/export", json=_export_spec(patches=red, name="same.png", expected=expected)
        )
        assert exported.status_code == 200, exported.get_json()
        render(red)
        assert preview(red) == first
        # Fresh replay uses the same strict policy and the source's exact initialized state.
        fresh = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
        probe = fresh.ensure_built()["artifact_probe"]
        assert probe["context"] == context
        artifactcontext.validate_candidate(
            tmp_path / "same.png", Path(probe["initialized"]), context
        )
        assert fresh.preview_png_snapshot("same", red, 400)["png"] == first
        # Mutation control: stop sending the bound policy/context. Never fall back in-place.
        saved_context = fresh.artifact_source
        fresh.artifact_source = None
        try:
            with pytest.raises(pool.WorkerError) as error:
                fresh.override("same", [FRAME])
            assert error.value.code == "artifact_source_unavailable"
        finally:
            fresh.artifact_source = saved_context
        assert {name: (tmp_path / name).read_bytes() for name in original} == original
    finally:
        if fresh is not None:
            pool.discard(fresh)
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)


@needs_worker
@pytest.mark.parametrize("crop", ["tight", "rc-tight", "explicit-full"])
def test_real_worker_strict_crop_refusal_and_looser_policy_compatibility(tmp_path, crop):
    context = _project(tmp_path, crop=crop)
    workers = []
    try:
        for policy in (STRICT, figcapture.SELECTED_ARTIFACT_POLICY):
            worker = pool.one_shot(
                "figure.py",
                str(tmp_path),
                "__main__",
                artifact_source={**context, "render_policy": policy},
            )
            workers.append(worker)
            if policy == STRICT:
                with pytest.raises(pool.WorkerError) as error:
                    worker.ensure_built()
                assert error.value.code == "artifact_source_unavailable"
                assert error.value.extra["reason"] == "unsupported_render_state"
            else:
                probe = worker.ensure_built()["artifact_probe"]
                artifactcontext.validate_candidate(
                    tmp_path / "same.png", Path(probe["initialized"]), context
                )
    finally:
        for worker in workers:
            pool.discard(worker)


@needs_worker
@pytest.mark.parametrize("operation", ["render", "preview", "export"])
def test_real_worker_refuses_wrong_frame_before_patches_or_output(tmp_path, operation):
    context = _project(tmp_path)
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
    try:
        worker.ensure_built()
        good = worker.override("same", [FRAME])
        target = tmp_path / "forbidden.png"
        with pytest.raises(pool.WorkerError) as error:
            if operation == "render":
                worker.override("same", [FRAME, SAVEFIG])
            elif operation == "preview":
                worker.preview_png_snapshot("same", [FRAME, SAVEFIG], 400)
            else:
                worker.export("same", [FRAME, SAVEFIG], str(target), "png", 100)
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "unsupported_render_state"
        assert not target.exists()
        assert (
            worker.override("same", [FRAME])["manifest"]["size_mm"] == good["manifest"]["size_mm"]
        )
    finally:
        pool.discard(worker)


@pytest.mark.parametrize("operation", ["render", "export"])
def test_strict_workerd_payload_keeps_policy_and_refuses_transparent_restart(
    tmp_path, monkeypatch, operation
):
    from tavotto.engine import workerd_client
    from test_workerd_pool import _worker

    worker, client = _worker(
        monkeypatch,
        tmp_path,
        [
            {"ok": True, "session_id": "old"},
            workerd_client.WorkerdError("expired", code="unknown_session"),
            {"ok": True, "session_id": "new"},
        ],
    )
    source = tmp_path / "same.png"
    source.write_bytes(b"source identity")
    worker.artifact_source = {
        **artifactcontext.create_context(source, "same.png"),
        "render_policy": STRICT,
    }
    worker.built = True
    with pytest.raises(pool.WorkerError) as error:
        worker._call(operation, 10, stem="same", payload={"patches": [FRAME, COLOR]})
    assert error.value.code == "artifact_source_unavailable"
    assert error.value.extra["reason"] == "source_changed"
    assert not worker.alive()
    assert [op for op, _ in client.calls] == ["open_session", operation]
    assert client.calls[-1][1]["payload"]["artifact_source"]["render_policy"] == STRICT


@needs_worker
def test_real_strict_static_copy_keeps_png_pixels_density_and_alpha_without_worker(
    tmp_path, monkeypatch
):
    context = _project(tmp_path, dpi=72, transparent=True)
    original = (tmp_path / "same.png").read_bytes()
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(
        m.engine_enginesession,
        "resolve",
        lambda **kwargs: pytest.fail("static copy started a worker"),
    )
    m.app.config["TESTING"] = True
    m.open_project(str(tmp_path))
    try:
        response = m.app.test_client().post(
            "/api/export",
            json=_export_spec(name="same.png", expected={k: context[k] for k in EXPECTED}),
        )
        assert response.status_code == 200, response.get_json()
        data = response.get_json()
        assert (Path(data["export_dir"]) / data["files"][0]["name"]).read_bytes() == original
        assert (tmp_path / "same.png").read_bytes() == original
    finally:
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)


@needs_worker
def test_hidden_png_baked_baseline_refuses_strict_adoption_and_publication(tmp_path, monkeypatch):
    """Preferred PDF listing cannot hide even a no-op/stale current PNG edit baseline."""
    context = _project(tmp_path)
    expected = {k: context[k] for k in EXPECTED}
    original = {name: (tmp_path / name).read_bytes() for name in ("same.png", "same.pdf")}
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "baseline-data")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "legacy-baseline.json")
    m.app.config["TESTING"] = True
    m.open_project(str(tmp_path))
    client = m.app.test_client()
    request = {"id": "same.png", "source_policy": STRICT, "patches": []}
    try:
        # Actual initialized source pixels qualify before the latent baseline is recorded.
        initial = client.post("/api/engine/render", json=request)
        assert initial.status_code == 200, initial.get_json()
        assert initial.get_json()["artifact_source"] == context
        m.append_baked("same", [{"gid": "axes_0", "prop": "visible", "value": True}])
        listed = client.get("/api/panels").get_json()["panels"]
        assert {p["id"] for p in listed} == {"same.pdf"}
        assert listed[0]["baked_overrides"]
        spec = _export_spec(name="same.png", expected=expected)
        with monkeypatch.context() as gate:
            gate.setattr(
                m.engine_enginesession,
                "resolve",
                lambda **kw: pytest.fail("nonempty saved baseline reached a worker"),
            )
            refused(client.post("/api/engine/render", json=request), "not_untouched")
            for route in ("/api/export/validate", "/api/export", "/api/export/start"):
                refused(client.post(route, json=spec), "not_untouched")
        assert not list(m.project_export_dir().glob("copy*"))
        # The generic API/legacy paths are unchanged; only the narrow adoption opts in.
        for policy in (figcapture.SELECTED_ARTIFACT_POLICY, None):
            response = client.post("/api/engine/render", json={**request, "source_policy": policy})
            assert response.status_code == 200, response.get_json()
        # Only the current baseline matters, not the entire writeback history.
        m.append_baked("same", [])
        response = client.post("/api/engine/render", json=request)
        assert response.status_code == 200, response.get_json()
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)


def _background_project(root, mode="transparent", *, multiple=False):
    options = {
        "transparent": "transparent=True",
        "opaque": "transparent=False",
        "explicit": "transparent=True, facecolor='yellow'",
        "late-defaults": "transparent=True",
    }[mode]
    script = "\n".join(
        [
            "import matplotlib; matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "fig, ax = plt.subplots(figsize=(4, 3))",
            "ax.plot([0, 1, 2, 3], [.2, .8, .4, .9])",
            "ax.set_title('Guarded source', fontsize=12)",
            "ax.set(xlabel='Time', ylabel='Response')",
            "fig.subplots_adjust(left=.18, bottom=.20, right=.92, top=.82)",
            "fig.add_axes([.62, .48, .2, .2]).plot([0, 1], [1, 0])" if multiple else "",
            "fig.savefig('same.pdf', bbox_inches='tight', pad_inches=.02, facecolor='pink')",
            f"fig.savefig('same.png', bbox_inches=None, dpi=72, {options})",
            "fig.savefig('later.png', dpi=72, facecolor='cyan')" if multiple else "",
            "other, bx = plt.subplots(); bx.plot([0, 1], [1, 0]); other.savefig('other.png')"
            if multiple
            else "",
            "matplotlib.rcParams.update({'savefig.transparent': True, "
            "'savefig.facecolor': 'yellow', 'savefig.edgecolor': 'purple', 'savefig.bbox': 'tight'})"
            if mode == "late-defaults"
            else "",
            "plt.close('all')",
        ]
    )
    (root / "figure.py").write_text(script, encoding="utf-8")
    subprocess.run([WORKER_PY, "figure.py"], cwd=root, check=True, capture_output=True, timeout=60)
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"figure.py": {"entry": "__main__", "cost": "light", "stems": ["same"]}}},
            indent=1,
        )
    )
    return script


def _background_png(data, target):
    from base64 import b64decode

    from PIL import Image

    target.write_bytes(b64decode(data))
    with Image.open(target) as image:
        image = image.convert("RGBA")
        return image.size, image.getpixel((0, 0)), image.getpixel((120, 210))


@needs_worker
@pytest.mark.parametrize("policy", [STRICT, figcapture.SELECTED_ARTIFACT_POLICY])
@pytest.mark.parametrize("mode", ["transparent", "opaque", "explicit", "late-defaults"])
def test_real_selected_background_is_the_editable_baseline(tmp_path, monkeypatch, policy, mode):
    from xml.etree import ElementTree

    script = _background_project(tmp_path, mode)
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    reference = tmp_path.parent / (tmp_path.name + "-native-reference")
    reference.mkdir()
    # Independent native save at the API's minimum 400px width, no adapter in the oracle.
    subprocess.run(
        [WORKER_PY, "-c", script.replace("dpi=72", "dpi=100")],
        cwd=reference,
        check=True,
        capture_output=True,
        timeout=60,
    )
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    m.open_project(str(tmp_path))
    client, fresh = m.app.test_client(), None
    expected = {
        k: artifactcontext.create_context(tmp_path / "same.png", "same.png")[k] for k in EXPECTED
    }
    request = {"id": "same.png", "source_policy": policy}
    try:
        response = client.post(
            "/api/engine/render", json={**request, "patches": [], "inline_svg": True}
        )
        assert response.status_code == 200, response.get_json()
        initial = response.get_json()
        fields = next(
            e["editable"] for e in initial["manifest"]["elements"] if e["gid"] == "figure"
        )
        transparent = mode in ("transparent", "late-defaults")
        assert next(f["value"] for f in fields if f["prop"] == "transparent") is transparent
        assert initial["manifest"]["size_mm"] == [101.6, 76.2]
        svg = ElementTree.fromstring(initial["svg"])
        background = svg.find(
            ".//s:g[@id='figure_1']/s:g[@id='patch_1']/s:path",
            {"s": "http://www.w3.org/2000/svg"},
        )
        assert (background is None) is transparent
        if background is not None:
            assert ("fill: #ffff00" if mode == "explicit" else "fill: #ffffff") in background.get(
                "style", ""
            )

        def preview(patches):
            result = client.post(
                "/api/engine/preview_png",
                json={
                    **request,
                    "expected_source": expected,
                    "patches": patches,
                    "with_manifest": True,
                    "w": 400,
                },
            )
            assert result.status_code == 200, result.get_json()
            return result.get_json()["png"]

        baseline = preview([])
        assert preview([FRAME]) == baseline
        target = tmp_path / "preview-check.png"
        assert _background_png(baseline, target)[0] == (400, 300)
        assert m.pdfbackend.compare_png(reference / "same.png", target)["max_abs_diff"] == 0
        red = [
            FRAME,
            {"gid": "figure", "prop": "transparent", "value": False},
            {"gid": "figure", "prop": "facecolor", "value": "red"},
        ]
        green = [FRAME, {"gid": "axes_0", "prop": "facecolor", "value": "lime"}]

        def render(patches):
            result = client.post(
                "/api/engine/render",
                json={
                    **request,
                    "expected_source": expected,
                    "patches": patches,
                    "inline_svg": True,
                },
            )
            assert result.status_code == 200, result.get_json()
            assert not result.get_json()["warnings"]
            return result.get_json()

        assert "fill: #ff0000" in render(red)["svg"]
        red_png = preview(red)
        render(green)
        green_png = preview(green)
        assert _background_png(red_png, target)[1] == (255, 0, 0, 255)
        assert _background_png(green_png, target)[2] == (0, 255, 0, 255)
        render([FRAME])
        assert preview([FRAME]) == baseline
        render(red)
        assert preview(red) == red_png
        render([FRAME])
        exported = client.post(
            "/api/export",
            json={
                **_export_spec(name="same.png", expected=expected),
                "original": {
                    "figure_id": "same.png",
                    "source_policy": policy,
                    "expected_source": expected,
                    "overrides": [],
                },
            },
        )
        assert exported.status_code == 200, exported.get_json()
        output = exported.get_json()
        assert (Path(output["export_dir"]) / output["files"][0]["name"]).read_bytes() == original[
            "same.png"
        ]
        fresh = pool.one_shot(
            "figure.py", str(tmp_path), "__main__", artifact_source=initial["artifact_source"]
        )
        probe = fresh.ensure_built()["artifact_probe"]
        artifactcontext.validate_candidate(
            tmp_path / "same.png", Path(probe["initialized"]), initial["artifact_source"]
        )
        for patches, pixels in ((red, red_png), (green, green_png), ([FRAME], baseline)):
            fresh.override("same", patches)
            assert fresh.preview_png_snapshot("same", patches, 400)["png"] == pixels
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        if fresh is not None:
            pool.discard(fresh)
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)


@needs_worker
def test_selected_background_keeps_legacy_and_other_figures_isolated(tmp_path):
    _background_project(tmp_path, multiple=True)
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    context = {
        **artifactcontext.create_context(tmp_path / "same.png", "same.png"),
        "render_policy": STRICT,
    }
    try:
        legacy = pool.get("figure.py", str(tmp_path), "__main__")
        legacy.ensure_built()
        before = {
            stem: legacy.preview_png_snapshot(stem, [FRAME], 400)["png"]
            for stem in ("same", "other")
        }
        selected = pool.get("figure.py", str(tmp_path), "__main__", artifact_source=context)
        assert selected is not legacy and selected.out_dir != legacy.out_dir
        result = selected.ensure_built()
        assert [d["stem"] for d in result["descriptors"]] == ["same"]
        artifactcontext.validate_candidate(
            tmp_path / "same.png", Path(result["artifact_probe"]["initialized"]), context
        )
        baseline = selected.preview_png_snapshot("same", [FRAME], 400)["png"]
        assert baseline != before["same"]
        selected.override("same", [FRAME, {"gid": "figure", "prop": "transparent", "value": False}])
        assert all(
            legacy.preview_png_snapshot(stem, [FRAME], 400)["png"] == pixels
            for stem, pixels in before.items()
        )
        assert selected.preview_png_snapshot("same", [FRAME], 400)["png"] == baseline
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        pool.shutdown_all(str(tmp_path))


@needs_worker
@pytest.mark.parametrize("field", ["facecolor", "edgecolor"])
def test_selected_background_invalid_color_is_structured_refusal(tmp_path, field):
    script = _background_project(tmp_path)
    (tmp_path / "figure.py").write_text(
        script.replace(
            "dpi=72, transparent=True)", f"dpi=72, transparent=True, {field}='invalid-color')"
        )
    )
    context = {
        **artifactcontext.create_context(tmp_path / "same.png", "same.png"),
        "render_policy": STRICT,
    }
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.ensure_built()
        assert error.value.code == "artifact_source_unavailable"
        assert error.value.extra["reason"] == "unsupported_render_state"
    finally:
        pool.discard(worker)


@needs_worker
def test_generic_selected_pdf_materializes_its_own_background(tmp_path):
    _background_project(tmp_path)
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    context = {
        **artifactcontext.create_context(tmp_path / "same.pdf", "same.pdf"),
        "render_policy": figcapture.SELECTED_ARTIFACT_POLICY,
    }
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
    try:
        result = worker.ensure_built()
        probe = result["artifact_probe"]
        assert probe["occurrence"] == 1  # Pink PDF first; transparent PNG second.
        artifactcontext.validate_candidate(
            tmp_path / "same.pdf", Path(probe["initialized"]), context
        )
        rendered = worker.override("same", [], inline_svg=True)
        assert "fill: #ffc0cb" in rendered["svg"]
        fields = next(
            e["editable"] for e in rendered["manifest"]["elements"] if e["gid"] == "figure"
        )
        assert next(f["value"] for f in fields if f["prop"] == "facecolor") == "#ffc0cb"
        assert next(f["value"] for f in fields if f["prop"] == "transparent") is False
        baseline = worker.preview_png_snapshot("same", [], 400)["png"]
        path = tmp_path / "pdf-preview.png"
        assert _background_png(baseline, path)[1] == (255, 192, 203, 255)
        edits = [{"gid": "figure", "prop": "facecolor", "value": "cyan"}]
        worker.override("same", edits)
        assert _background_png(worker.preview_png_snapshot("same", edits, 400)["png"], path)[1] == (
            0,
            255,
            255,
            255,
        )
        worker.override("same", [])
        assert worker.preview_png_snapshot("same", [], 400)["png"] == baseline
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        pool.discard(worker)


@needs_worker
@pytest.mark.parametrize("operation", ["render", "preview", "export"])
def test_ambiguous_background_color_refuses_before_edit_or_output(tmp_path, operation):
    _background_project(tmp_path)
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    context = {
        **artifactcontext.create_context(tmp_path / "same.png", "same.png"),
        "render_policy": STRICT,
    }
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
    face = {"gid": "figure", "prop": "facecolor", "value": "red"}
    visibility = {"gid": "figure", "prop": "transparent", "value": False}
    output = tmp_path / "must-not-publish.png"
    try:
        worker.ensure_built()
        worker.override("same", [FRAME, visibility, {**face, "value": "cyan"}])
        before = worker.render_png("same", 400).read_bytes()
        for ambiguous, reason in (
            ([FRAME, face], "background_visibility_required"),
            (
                [FRAME, face, visibility, {**visibility, "value": None}],
                "background_visibility_required",
            ),
            ([FRAME, face, {**visibility, "identity": "l1:stale"}], "unsupported_render_state"),
            (
                [FRAME, face, visibility, {**visibility, "identity": None}],
                "unsupported_render_state",
            ),
        ):
            with pytest.raises(pool.WorkerError) as error:
                if operation == "render":
                    worker.override("same", ambiguous)
                elif operation == "preview":
                    worker.preview_png_snapshot("same", ambiguous, 400)
                else:
                    worker.export("same", ambiguous, str(output), "png", 100)
            assert error.value.code == "artifact_source_unavailable"
            assert error.value.extra["reason"] == reason
            assert worker.render_png("same", 400).read_bytes() == before
            assert not output.exists()
        for transparent in (False, True):
            patches = [
                FRAME,
                face,
                {**visibility, "value": not transparent},
                {**visibility, "value": transparent},
            ]
            rendered = worker.override("same", patches)
            fields = next(
                e["editable"] for e in rendered["manifest"]["elements"] if e["gid"] == "figure"
            )
            assert next(f["value"] for f in fields if f["prop"] == "transparent") is transparent
            png = worker.preview_png_snapshot("same", patches, 400)["png"]
            corner = _background_png(png, tmp_path / "choice.png")[1]
            assert corner == ((255, 255, 255, 0) if transparent else (255, 0, 0, 255))
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        pool.discard(worker)


@needs_worker
def test_already_invisible_script_background_needs_no_new_choice(tmp_path):
    script = _background_project(tmp_path)
    script = script.replace(
        "fig.savefig('same.pdf'", "fig.patch.set_visible(False)\nfig.savefig('same.pdf'"
    )
    (tmp_path / "figure.py").write_text(script, encoding="utf-8")
    subprocess.run(
        [WORKER_PY, "figure.py"], cwd=tmp_path, check=True, capture_output=True, timeout=60
    )
    context = {
        **artifactcontext.create_context(tmp_path / "same.png", "same.png"),
        "render_policy": STRICT,
    }
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__", artifact_source=context)
    try:
        worker.ensure_built()
        result = worker.override(
            "same", [FRAME, {"gid": "figure", "prop": "facecolor", "value": "red"}]
        )
        assert not result["warnings"]
    finally:
        pool.discard(worker)


@needs_worker
@pytest.mark.parametrize("background", [False, True])
def test_background_choice_error_preserves_export_source_owner(tmp_path, monkeypatch, background):
    import time

    _background_project(tmp_path)
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    context = artifactcontext.create_context(tmp_path / "same.png", "same.png")
    expected = {key: context[key] for key in EXPECTED}
    owner = {"file_id": "same.png", **expected, "reason": "background_visibility_required"}
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    m.open_project(str(tmp_path))
    client = m.app.test_client()
    patches = [FRAME, {"gid": "figure", "prop": "facecolor", "value": "red"}]
    try:
        response = client.post(
            "/api/engine/render",
            json={
                "id": "same.png",
                "source_policy": STRICT,
                "expected_source": expected,
                "patches": patches,
            },
        )
        assert response.status_code == 409, response.get_json()
        assert response.get_json()["params"] == owner
        response = client.post(
            "/api/export/start" if background else "/api/export",
            json=_export_spec(name="same.png", patches=patches, expected=expected),
        )
        body = response.get_json()
        if background:
            assert response.status_code == 200, body
            deadline = time.monotonic() + 30
            while body["status"] not in ("failed", "done", "partial", "cancelled"):
                assert time.monotonic() < deadline
                time.sleep(0.02)
                body = client.get(
                    "/api/export/state", query_string={"job_id": body["job_id"]}
                ).get_json()
            assert body["status"] == "failed", body
            error = body["error"]
        else:
            assert response.status_code == 409, body
            error = body
        assert error["code"] == "artifact_source_unavailable"
        assert {key: error["params"][key] for key in owner} == owner
        assert not list(m.project_export_dir().glob("copy*"))
        assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
    finally:
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)
