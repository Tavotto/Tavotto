"""Real controller routes, isolated fake workers: byte authority before edits/publication."""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto import app as m
from tavotto.engine import artifactcontext as ac, exportreq, figcapture

POLICY = figcapture.SELECTED_ARTIFACT_POLICY
PATCHES = [{"gid": "axes_1", "prop": "color", "value": "red"}]


@pytest.fixture
def api(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    for name in ("plot.pdf", "plot.png"):
        (root / name).write_bytes(name.encode())
    workers, resolutions, validations = {}, [], []
    ctx = SimpleNamespace(
        id="selected-project",
        path=root,
        registry=SimpleNamespace(for_stem=lambda stem: {"script": "plot.py", "entry": "main"}),
    )
    monkeypatch.setattr(m, "PROJECTS", {ctx.id: ctx})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", ctx.id)
    monkeypatch.setattr(m, "_resync_registration", lambda *args: None)
    monkeypatch.setattr(m, "sse_publish", lambda *args: None)
    monkeypatch.setattr(m, "project_export_dir", lambda: tmp_path / "exports")
    monkeypatch.setattr(m, "_export_inspect", lambda job, produced: produced)
    monkeypatch.setattr(m, "_export_telemetry", lambda *args: None)
    # These isolated fake workers are not registered in the real process pool.
    monkeypatch.setattr(m.engine_pool, "force_cancel", lambda *args, **kwargs: False)

    class Worker:
        def __init__(self, context, index):
            self.context = context
            self.artifact_admission_lock = threading.Lock()
            self.script_name = "plot.py"
            self.built, self.rev, self.builds = False, 0, 0
            self.out_dir = tmp_path / f"worker-{index}"
            self.export_dir = self.out_dir / "exports"
            self.out_dir.mkdir()
            self.actions = []
            self.after = lambda: None
            self.last_build_artifact_probe = None

        def ensure_built(self):
            self.builds += 1
            # Re-ensuring a warm worker erases its bookkeeping: tests must catch that.
            if self.built:
                self.last_build_artifact_probe = None
                return {}
            self.built = True
            if self.context:
                candidate = self.out_dir / ("initialized." + self.context["kind"])
                candidate.write_bytes((root / self.context["source_id"]).read_bytes())
                self.last_build_artifact_probe = dict(
                    initialized=str(candidate),
                    stem="plot",
                    context=self.context,
                    occurrence=1,
                )
            (self.out_dir / "plot.svg").write_text("<svg/>")
            return {}

        def override(self, stem, patches, *args, **kwargs):
            if not self.built:
                self.ensure_built()
            self.actions.append(("override", patches))
            self.rev += 1
            self.after()
            return {"manifest": {"size_mm": [10, 10]}, "svg": "<svg/>"}

        def preview_png_snapshot(self, stem, patches, width):
            self.actions.append(("paired", patches))
            self.after()
            return {"png": "cG5n", "manifest": {"size_mm": [10, 10]}}

        def preview_png(self, stem, patches, width, *, tag):
            self.actions.append(("preview", patches))
            return self.render_png(stem, width)

        def render_png(self, stem, width):
            self.actions.append(("png", []))
            path = self.out_dir / "preview.png"
            path.write_bytes(b"png")
            self.after()
            return path

        def svg_path(self, stem):
            return self.out_dir / (stem + ".svg")

        def export(self, stem, patches, path, fmt, dpi):
            self.actions.append(("export", patches))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"export")
            self.after()
            return {}

    def resolve(**kwargs):
        resolutions.append(kwargs)
        context = kwargs.get("artifact_source")
        key = json.dumps(context, sort_keys=True)
        if key not in workers:
            workers[key] = Worker(context, len(workers))
        return workers[key]

    def validate(source, candidate, context):
        validations.append((source, candidate, context))
        if source.read_bytes() != candidate.read_bytes():
            raise ac.ArtifactContextError("source_mismatch", "Different source pixels.")
        return {"pixels_equal": True}

    monkeypatch.setattr(m.engine_enginesession, "resolve", resolve)
    monkeypatch.setattr(ac, "validate_candidate", validate)
    m.app.config["TESTING"] = True
    return SimpleNamespace(
        client=m.app.test_client(),
        root=root,
        workers=workers,
        resolutions=resolutions,
        validations=validations,
        worker_type=Worker,
        ctx=ctx,
    )


def basis(api, name="plot.pdf"):
    context = ac.create_context(api.root / name, name)
    return {key: context[key] for key in ("bytes_sha256", "size_bytes")}


def render(api, name="plot.pdf", **extra):
    return api.client.post(
        "/api/engine/render", json={"id": name, "source_policy": POLICY, **extra}
    )


def refused(response, reason):
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["code"] == "artifact_source_unavailable"
    assert body["params"]["reason"] == reason
    return body


def test_pdf_png_a_b_a_admits_once_and_preserves_legacy(api):
    for name in ("plot.pdf", "plot.png", "plot.pdf"):
        response = render(api, name, patches=PATCHES, expected_source=basis(api, name))
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["artifact_source"]["source_id"] == name
    assert len(api.workers) == 2 and len(api.validations) == 2
    assert all(w.builds == 1 for w in api.workers.values())
    response = api.client.post("/api/engine/render", json={"id": "plot.pdf", "patches": PATCHES})
    assert response.status_code == 200 and "artifact_source" not in response.get_json()
    assert "artifact_source" not in api.resolutions[-1]


@pytest.mark.parametrize("route", ["render", "preview_png"])
def test_nonempty_selected_edits_require_exact_baseline(api, route):
    body = {"id": "plot.pdf", "source_policy": POLICY, "patches": PATCHES, "with_manifest": True}
    refused(api.client.post("/api/engine/" + route, json=body), "source_changed")
    old = basis(api)
    (api.root / "plot.pdf").write_bytes(b"new source")
    refused(
        api.client.post("/api/engine/" + route, json={**body, "expected_source": old}),
        "source_changed",
    )
    assert not api.resolutions


@pytest.mark.parametrize("mode", ["mismatch", "escape", "context", "missing"])
def test_bad_initial_admission_never_applies_overrides(api, mode, monkeypatch):
    ensure = api.worker_type.ensure_built

    def bad(worker):
        ensure(worker)
        probe = worker.last_build_artifact_probe
        if mode == "mismatch":
            Path(probe["initialized"]).write_bytes(b"wrong")
        elif mode == "escape":
            probe["initialized"] = str(api.root / "plot.pdf")
        elif mode == "context":
            probe["context"] = {**probe["context"], "bytes_sha256": "0" * 64}
        else:
            del probe["initialized"]

    monkeypatch.setattr(api.worker_type, "ensure_built", bad)
    refused(render(api), "source_mismatch" if mode == "mismatch" else "capture_incomplete")
    assert not next(iter(api.workers.values())).actions


def test_paired_preview_uses_selected_context_without_rebuilding(api):
    assert render(api).status_code == 200
    query = {
        "id": "plot.pdf",
        "source_policy": POLICY,
        "expected_source": basis(api),
        "with_manifest": True,
    }
    response = api.client.post("/api/engine/preview_png", json=query)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["artifact_source"]["source_id"] == "plot.pdf"
    worker = next(iter(api.workers.values()))
    assert worker.builds == 1 and len(api.validations) == 1
    (api.root / "plot.pdf").write_bytes(b"changed")
    refused(api.client.post("/api/engine/preview_png", json=query), "source_changed")
    assert len(api.workers) == 1


@pytest.mark.parametrize("route", ["svg", "png"])
def test_legacy_current_state_gets_refuse_selected_policy(api, route):
    refused(
        api.client.get(
            "/api/engine/" + route, query_string={"id": "plot.pdf", "source_policy": POLICY}
        ),
        "unsupported_render_state",
    )
    assert not api.resolutions


def test_source_changed_during_render_is_not_published(api):
    assert render(api).status_code == 200
    worker = next(iter(api.workers.values()))
    worker.after = lambda: (api.root / "plot.pdf").write_bytes(b"changed")
    refused(render(api), "source_changed")


def test_retry_keeps_exact_context_and_admits_replacement(api, monkeypatch):
    assert render(api).status_code == 200
    old = next(iter(api.workers.values()))
    replacement = api.worker_type(old.context, 99)

    def failed(*args, **kwargs):
        raise m.engine_pool.WorkerError("missing", code="missing_dependency")

    monkeypatch.setattr(old, "override", failed)
    monkeypatch.setattr(m, "_switched_to_project_env", lambda *args: True)

    def resolver(**kwargs):
        assert kwargs["artifact_source"] == old.context
        return replacement

    monkeypatch.setattr(m.engine_enginesession, "resolve", resolver)
    # First call uses the failing worker; the retry resolves its replacement.
    with m.bound_project(api.ctx):
        result = m._engine_attempt(
            "plot.pdf", old, "plot", lambda wk, st: wk.override(st, []), artifact_source=old.context
        )
    assert result[0] is replacement and replacement.builds == 1
    assert len(api.validations) == 2


@pytest.mark.parametrize(
    "route,method,reason",
    [
        ("update_source", "post", "writeback_not_supported"),
        ("history/restore", "post", "writeback_not_supported"),
        ("specfix", "post", "unsupported_render_state"),
        ("sync_overrides", "post", "unsupported_render_state"),
        ("history/preview", "get", "unsupported_render_state"),
        ("history", "get", "unsupported_render_state"),
    ],
)
def test_unsupported_policy_routes_refuse_before_worker_or_write(
    api, monkeypatch, route, method, reason
):
    monkeypatch.setattr(m, "_write_back_forbidden", lambda: pytest.fail("writeback began"))
    values = {"id": "plot.pdf", "source_policy": POLICY}
    response = getattr(api.client, method)(
        "/api/engine/" + route,
        **({"json": values} if method == "post" else {"query_string": values}),
    )
    refused(response, reason)
    assert not api.resolutions


def test_unknown_policy_runtime_and_missing_source_refuse(api):
    refused(render(api, source_policy="future"), "unsupported_render_state")
    refused(render(api, "runtime:any"), "unsupported_render_state")
    refused(render(api, "missing.pdf"), "source_unreadable")
    assert not api.resolutions


def export_spec(api, *, edited=False, canvas=False):
    source = {
        "figure_id": "plot.png",
        "overrides": PATCHES if edited else [],
        "source_policy": POLICY,
    }
    if edited:
        source["expected_source"] = basis(api, "plot.png")
    spec = {"scope": "original", "filename": "result", "formats": ["png"], "original": source}
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


def test_original_empty_overrides_preserves_source_raster_bytes(api, monkeypatch):
    seen = []

    def original_png(source, out, dpi, **kwargs):
        seen.append(source)
        shutil.copyfile(source, out)
        return {"px_w": 73, "px_h": 47}

    monkeypatch.setattr(m.pdfbackend, "original_png", original_png)
    monkeypatch.setattr(m, "_original_page_pt", lambda *args: (20, 10))
    response = api.client.post("/api/export", json=export_spec(api))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    output = Path(data["export_dir"]) / data["files"][0]["name"]
    assert output.read_bytes() == (api.root / "plot.png").read_bytes()
    assert seen == [api.root / "plot.png"]
    assert not api.workers and not api.validations


@pytest.mark.parametrize("canvas", [False, True])
def test_export_rejects_edited_source_without_expected_before_work(api, canvas):
    spec = export_spec(api, edited=True, canvas=canvas)
    source = spec["canvas"]["objects"][0] if canvas else spec["original"]
    source.pop("expected_source")
    refused(api.client.post("/api/export", json=spec), "source_changed")
    assert not api.resolutions


def test_background_export_freezes_context_before_dispatch_and_guards_publication(api, monkeypatch):
    captured = {}

    def run_async(job, produce, **kwargs):
        captured.update(job=job, produce=produce, **kwargs)

    monkeypatch.setattr(m.engine_exportjob, "run_async", run_async)
    response = api.client.post("/api/export/start", json=export_spec(api))
    assert response.status_code == 200
    (api.root / "plot.png").write_bytes(b"changed")
    with pytest.raises(ac.ArtifactContextError, match="changed"):
        with captured["commit_guard"]():
            pytest.fail("published changed source")
    with pytest.raises(ac.ArtifactContextError, match="changed"):
        captured["produce"](captured["job"], api.root / "out")


def test_original_normalization_and_plan_identity_preserve_policy_and_expected(api):
    spec = export_spec(api, edited=True)
    selected = exportreq.normalize(spec)
    assert selected.original.expected_source == basis(api, "plot.png")
    assert selected.to_payload()["source_policy"] == POLICY
    first = exportreq.render_plan_ref(selected, [])
    spec["original"].pop("source_policy")
    spec["original"].pop("expected_source")
    legacy = exportreq.normalize(spec)
    assert "source_policy" not in legacy.to_payload()
    assert first["plan_identity"] != exportreq.render_plan_ref(legacy, [])["plan_identity"]


def test_failed_admission_retires_only_its_selected_context(api, monkeypatch):
    retired = []
    monkeypatch.setattr(
        m.engine_pool, "force_cancel", lambda *args, **kwargs: retired.append((args, kwargs))
    )

    def refuse(*args):
        raise ac.ArtifactContextError("source_mismatch", "Different pixels.")

    monkeypatch.setattr(ac, "validate_candidate", refuse)
    refused(render(api), "source_mismatch")
    assert len(retired) == 1
    args, kwargs = retired[0]
    assert args == ("plot.py", str(api.root))
    assert kwargs["artifact_source"]["source_id"] == "plot.pdf"
    assert kwargs["expected_worker"] is next(iter(api.workers.values()))


def test_worker_refusal_preserves_reason_and_retires_before_edits(api, monkeypatch):
    def refuse(worker):
        exc = m.engine_pool.WorkerError("private scene details", code="artifact_source_unavailable")
        exc.extra = {"reason": "layout_history_required"}
        raise exc

    monkeypatch.setattr(api.worker_type, "ensure_built", refuse)
    response = render(api)
    refused(response, "layout_history_required")
    assert b"private scene" not in response.data
    assert not next(iter(api.workers.values())).actions


def test_mutation_after_export_commit_does_not_break_cleanup_or_terminal_status(api, monkeypatch):
    with m.bound_project(api.ctx):
        context = m._artifact_source("plot.png", POLICY)
        guard = m._export_commit_guard(api.root, {"plot.png": context})
    job = m.engine_exportjob.prepare(export_spec(api), api.root / "deliveries")

    def produce(job, directory):
        output = directory / "out.png"
        output.write_bytes(b"frozen-output")
        return [m.engine_exportjob.Produced(format="png", tmp_path=output)]

    publish = m.engine_atomicio.publish_file

    def publish_then_change(source, target):
        publish(source, target)
        (api.root / "plot.png").write_bytes(b"subsequently changed")

    monkeypatch.setattr(m.engine_atomicio, "publish_file", publish_then_change)
    result = m.engine_exportjob.run(
        job, produce, commit_guard=guard, classify_error=m._classify_export_error
    )
    assert result["status"] == "done"
    assert (job.export_dir / "result.png").read_bytes() == b"frozen-output"
    assert not any(job.export_dir.glob(m.engine_exportjob.TMP_PREFIX + "*"))
    assert str(job.export_dir / "result.png") not in m.engine_exportjob._RESERVED


def test_canvas_resource_patch_hash_keeps_its_literal_authority(api, monkeypatch):
    path = api.root / "intermediate.pdf"
    path.write_bytes(b"same-rendered-bytes")
    worker = SimpleNamespace()
    monkeypatch.setattr(
        m, "_serialize_figure_with_worker", lambda *args, **kwargs: (path, worker, "plot.py")
    )
    monkeypatch.setattr(
        m, "_execution_receipt", lambda *args: SimpleNamespace(public_facts=lambda: {})
    )

    def artifact(receipt, path, *, source_id, patch_hash):
        return figcapture.source_artifact_from_file(
            path,
            source_id=source_id,
            origin="execution",
            receipt_id="instance",
            receipt_identity="sha256:" + "a" * 64,
            patch_hash=patch_hash,
        )

    monkeypatch.setattr(m.engine_receipt, "source_artifact_for", artifact)
    with m.bound_project(api.ctx):
        context = m._artifact_source("plot.png", POLICY)
        obj = {"id": "plot.png", "overrides": PATCHES}
        selected = m._execution_source(obj, 100, [], api.root, artifact_source=context)
        legacy = m._execution_source(obj, 100, [], api.root)
    assert selected.artifact.bytes_sha256 == legacy.artifact.bytes_sha256
    assert selected.artifact.patch_hash == m.engine_patchspec.patch_hash(PATCHES)
    assert selected.artifact.semantic_identity() == legacy.artifact.semantic_identity()
    spec = export_spec(api, edited=True, canvas=True)
    first = exportreq.render_plan_ref(exportreq.normalize(spec), [])
    spec["canvas"]["objects"][0].pop("source_policy")
    spec["canvas"]["objects"][0].pop("expected_source")
    assert (
        first["plan_identity"]
        != exportreq.render_plan_ref(exportreq.normalize(spec), [])["plan_identity"]
    )


def test_selected_binary_preview_refuses_before_worker(api):
    response = api.client.post(
        "/api/engine/preview_png", json={"id": "plot.pdf", "source_policy": POLICY}
    )
    refused(response, "unsupported_render_state")
    assert not api.resolutions


def test_repeated_canvas_source_cannot_replace_an_earlier_frozen_basis(api, monkeypatch):
    spec = export_spec(api, edited=True, canvas=True)
    first = spec["canvas"]["objects"][0]
    changed = b"a different persisted source"
    new_basis = {"bytes_sha256": m.hashlib.sha256(changed).hexdigest(), "size_bytes": len(changed)}
    spec["canvas"]["objects"].append({**first, "expected_source": new_basis})
    artifact_source = m._artifact_source
    calls = []

    def mutate_after_first(*args):
        context = artifact_source(*args)
        calls.append(context)
        if len(calls) == 1:
            (api.root / "plot.png").write_bytes(changed)
        return context

    monkeypatch.setattr(m, "_artifact_source", mutate_after_first)
    refused(api.client.post("/api/export", json=spec), "source_changed")
    assert len(calls) == 2 and not api.resolutions


def test_malformed_missing_and_stale_saved_bases_are_not_retryable(api):
    for expected in (
        None,
        {"bytes_sha256": "bad", "size_bytes": 1},
        {"bytes_sha256": "0" * 64, "size_bytes": 1},
    ):
        response = render(api, patches=PATCHES, expected_source=expected)
        assert refused(response, "source_changed")["retryable"] is False


def test_concurrent_first_requests_admit_once_without_resetting_render_bookkeeping(
    api, monkeypatch
):
    build_entered, release_build, second_admission = (threading.Event() for _ in range(3))
    ensure = api.worker_type.ensure_built
    override = api.worker_type.override
    build_calls = []

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.count_lock = threading.Lock()
            self.attempts = 0

        def __enter__(self):
            with self.count_lock:
                self.attempts += 1
                if self.attempts == 2:
                    second_admission.set()
            self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    def blocked_build(worker):
        build_calls.append(worker.built)
        build_entered.set()
        assert release_build.wait(5), "test did not release the first build"
        result = ensure(worker)
        worker.last_patch_hash = None  # The real ensure_built resets this bookkeeping.
        return result

    def record_render(worker, stem, patches, *args, **kwargs):
        result = override(worker, stem, patches, *args, **kwargs)
        worker.last_patch_hash = m.engine_patchspec.patch_hash(patches)
        return result

    with m.bound_project(api.ctx):
        context = m._artifact_source("plot.pdf", POLICY)
        worker, _ = m._engine_worker("plot.pdf", artifact_source=context)
    worker.artifact_admission_lock = ObservedLock()
    monkeypatch.setattr(api.worker_type, "ensure_built", blocked_build)
    monkeypatch.setattr(api.worker_type, "override", record_render)
    payload = {
        "id": "plot.pdf",
        "source_policy": POLICY,
        "expected_source": basis(api),
        "patches": PATCHES,
    }

    def request():
        with m.app.test_client() as client:
            response = client.post("/api/engine/render", json=payload)
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(request)
        try:
            assert build_entered.wait(5)
            second = executor.submit(request)
            assert second_admission.wait(5), "second request never attempted admission"
            assert build_calls == [False]
        finally:
            release_build.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert all(status == 200 for status, _ in results), results
    assert worker.builds == 1 and len(api.validations) == 1
    assert worker.last_build_artifact_probe["context"] == context
    assert worker.last_patch_hash == m.engine_patchspec.patch_hash(PATCHES)
    assert [action for action, _ in worker.actions] == ["override", "override"]


@pytest.mark.parametrize("occurrence", [0, -1, True])
def test_probe_occurrence_requires_a_positive_integer(api, monkeypatch, occurrence):
    ensure = api.worker_type.ensure_built

    def invalid(worker):
        ensure(worker)
        worker.last_build_artifact_probe["occurrence"] = occurrence

    monkeypatch.setattr(api.worker_type, "ensure_built", invalid)
    refused(render(api), "capture_incomplete")
    assert not next(iter(api.workers.values())).actions


@pytest.mark.parametrize("route", ["update_source", "history/restore"])
def test_partial_selected_basis_never_falls_through_to_legacy_writeback(api, monkeypatch, route):
    monkeypatch.setattr(m, "_write_back_forbidden", lambda: pytest.fail("legacy writeback began"))
    response = api.client.post(
        "/api/engine/" + route, json={"id": "plot.pdf", "expected_source": basis(api)}
    )
    refused(response, "unsupported_render_state")
    assert not api.resolutions


@pytest.mark.parametrize("alias", ["./plot.png", "sub/../plot.png", "link.png"])
def test_selected_source_aliases_share_canonical_identity(api, alias):
    (api.root / "sub").mkdir()
    if alias == "link.png":
        try:
            (api.root / alias).symlink_to(api.root / "plot.png")
        except OSError as exc:
            pytest.skip(f"Symlinks unavailable: {exc}")
    canonical = render(api, "plot.png").get_json()["artifact_source"]
    response = render(api, alias)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["artifact_source"] == canonical
    assert len(api.workers) == 1 and len(api.validations) == 1
