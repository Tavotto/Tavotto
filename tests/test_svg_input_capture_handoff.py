"""Application handoff of capture-verified bytes into the actual durable store.

Capture/cache/receipt/file IO are real. Figure export and transport are explicit
unit substitutes: these cases make no scientific execution or draw claim.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto import app as m
from tavotto.engine import figcapture, patchspec, receipt, svgstore
from tavotto.rendercore import sources
from test_declared_input_archive_receipt import _from_worker, _frozen

PDF = b"%PDF-admitted-pair-unit-fixture"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="72pt" height="36pt" '
    b'viewBox="0 0 72 36"><path d="M 1 2 L 30 20" stroke="black"/></svg>'
)
PATCH = patchspec.patch_hash([])
OWNER = {"script": "plot.py", "run_config": None}


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    worker, data = _frozen(tmp_path)
    rcpt = _from_worker(worker)
    assert rcpt.captured_input_archive is not None
    root = Path(worker.spec.project_root)
    worker.figures_dir = root
    worker.built = True
    source_id = figcapture.runtime_asset_id("plot.py", "F")
    directory = tmp_path / "paired"
    directory.mkdir()
    pdf, svg = directory / "figure.pdf", directory / "figure.svg"
    pdf.write_bytes(PDF)
    svg.write_bytes(SVG)
    monkeypatch.setattr(m, "_request_ctx", lambda: None)
    monkeypatch.setattr(m, "require_project", lambda: root)
    context = SimpleNamespace(
        worker=worker,
        receipt=rcpt,
        archive=rcpt.captured_input_archive,
        root=root,
        source_id=source_id,
        pdf=pdf,
        svg=svg,
        data=data,
        desc={"asset_id": source_id, "stem": "F", "script": "plot.py"},
    )
    monkeypatch.setattr(m, "_execution_receipt", lambda *a, **k: context.receipt)
    return context


def _pair(case, source_id=None):
    source_id = source_id or case.source_id
    return sources.FrozenSource(
        receipt.source_artifact_for(case.receipt, case.pdf, source_id=source_id, patch_hash=PATCH),
        case.pdf,
        case.receipt.public_facts(),
        sources.FrozenSource(
            receipt.source_artifact_for(
                case.receipt, case.svg, source_id=source_id, patch_hash=PATCH
            ),
            case.svg,
            case.receipt.public_facts(),
        ),
    )


def _capture_worker(case, monkeypatch):
    calls = []

    def export(stem, patches, destination, fmt, dpi, **kwargs):
        calls.append((stem, patches, destination, fmt, dpi, kwargs))
        Path(destination).write_bytes(PDF if fmt == "pdf" else SVG)
        if "svg_path" in kwargs:
            Path(kwargs["svg_path"]).write_bytes(SVG)
            return {
                "svg": {
                    "path": kwargs["svg_path"],
                    "sha256": hashlib.sha256(SVG).hexdigest(),
                    "size_bytes": len(SVG),
                }
            }
        return {}

    monkeypatch.setattr(case.worker, "export", export, raising=False)
    return calls


def _load(case, reference):
    return svgstore.load_snapshot(
        case.root,
        reference,
        source_id=case.source_id,
        patch_hash=PATCH,
        remap_fingerprint=m.engine_inputremap.fingerprint(case.root),
        storage_root=m._svg_storage_root(case.root),
    )


@pytest.mark.parametrize("with_archive", [False, True])
def test_save_handoff_preserves_exact_default_kwargs_and_public_facts(
    case, monkeypatch, with_archive
):
    calls = []
    frozen = _pair(case)
    before = copy.deepcopy(case.receipt.public_facts())
    monkeypatch.setattr(svgstore, "save", lambda *a, **k: calls.append((a, k)))
    optional = {"captured_input_archive": case.archive} if with_archive else {}
    m._save_svg_snapshot(frozen, project_root=case.root, execution_owner=OWNER, **optional)
    expected = {
        "pdf": PDF,
        "svg": SVG,
        "artifact": frozen.artifact.to_payload(),
        "receipt": before,
        "static_sha256": None,
        "execution_owner": OWNER,
        "remap_fingerprint": m.engine_inputremap.fingerprint(case.root),
        "storage_root": m._svg_storage_root(case.root),
        **optional,
    }
    assert calls == [((case.root, case.source_id, PATCH), expected)]
    if with_archive:
        assert calls[0][1]["captured_input_archive"] is case.archive
    assert case.receipt.public_facts() == before
    without = dataclasses.replace(case.receipt, captured_input_archive=None)
    for private in (False, True):
        assert case.receipt.to_payload(include_private=private) == without.to_payload(
            include_private=private
        )
    assert case.receipt.receipt_id == without.receipt_id
    assert case.receipt.public_identity() == without.public_identity()
    assert not hasattr(frozen, "captured_input_archive")


@pytest.mark.parametrize("with_archive", [False, True])
def test_execution_source_forwards_only_the_actual_serialization_receipt(
    case, monkeypatch, with_archive
):
    actual = case.receipt
    if not with_archive:
        actual = dataclasses.replace(actual, captured_input_archive=None)
    serialized = m._FigureSerialization(case.pdf, case.worker, "plot.py", actual)
    calls = []
    monkeypatch.setattr(m, "_serialize_figure_with_worker", lambda *a, **k: serialized)
    monkeypatch.setattr(
        m, "_execution_receipt", lambda *a, **k: pytest.fail("refetched another receipt")
    )
    monkeypatch.setattr(m, "_save_svg_snapshot", lambda *a, **k: calls.append((a, k)))
    frozen = m._execution_source({"id": case.source_id}, 600, None, None, include_svg=True)
    expected = {"execution_owner": OWNER}
    if with_archive:
        expected["captured_input_archive"] = case.archive
    assert calls == [((frozen,), expected)]
    assert frozen.receipt == actual.public_facts()
    assert frozen.svg.receipt == actual.public_facts()
    assert not hasattr(frozen, "captured_input_archive")


@pytest.mark.parametrize("kind", ["runtime", "pdf", "svg"])
@pytest.mark.parametrize("with_archive", [False, True])
def test_best_effort_handoff_for_runtime_and_admitted_static_aliases(
    case, monkeypatch, kind, with_archive
):
    if not with_archive:
        case.receipt = dataclasses.replace(case.receipt, captured_input_archive=None)
    exports = _capture_worker(case, monkeypatch)
    calls = []
    admissions = []
    bindings = []
    if kind == "pdf":
        (case.root / "F.pdf").write_bytes(PDF)
        case.desc["original_artifact"] = "F.pdf"
        monkeypatch.setattr(m, "safe_resolve", lambda name: case.root / name)
        monkeypatch.setattr(
            m.engine_artifactcontext,
            "create_context",
            lambda *a: {"bytes_sha256": hashlib.sha256(PDF).hexdigest()},
        )
        monkeypatch.setattr(
            m.engine_artifactcontext,
            "validate_candidate",
            lambda *a: admissions.append(a),
        )
    if kind == "svg":
        (case.root / "F.svg").write_bytes(SVG)
        original = m._bind_svg_asset

        def bind(*args, **kwargs):
            bindings.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(m, "_bind_svg_asset", bind)
    monkeypatch.setattr(m, "_save_svg_snapshot", lambda *a, **k: calls.append((a, k)))
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)
    wanted_ids = [case.source_id] + ([] if kind == "runtime" else [f"F.{kind}"])
    assert [args[0].artifact.source_id for args, _ in calls] == wanted_ids
    for args, kwargs in calls:
        expected = {"project_root": case.root, "execution_owner": OWNER}
        if args[0].artifact.source_id != case.source_id:
            expected["static_sha256"] = hashlib.sha256(PDF if kind == "pdf" else SVG).hexdigest()
        if with_archive:
            expected["captured_input_archive"] = case.archive
        assert kwargs == expected
        assert args[0].receipt == case.receipt.public_facts()
    assert exports[0][-1]["require_built"] is True
    assert exports[0][-1]["snapshot_only"] is True
    if kind == "pdf":
        assert len(admissions) == 1
    if kind == "svg":
        assert len(bindings) == 1 and len(bindings[0][0]) == 5
        assert bindings[0][1] == ({"captured_input_archive": case.archive} if with_archive else {})


def test_capture_does_not_publish_after_the_receipt_changes(case, monkeypatch):
    _capture_worker(case, monkeypatch)
    changed = dataclasses.replace(case.receipt, instance="new-execution")
    receipts = iter((case.receipt, changed))
    monkeypatch.setattr(m, "_execution_receipt", lambda *a, **k: next(receipts))
    monkeypatch.setattr(
        m, "_save_svg_snapshot", lambda *a, **k: pytest.fail("saved a changed Figure")
    )
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)


def test_capture_keeps_independent_pdf_admission_before_alias_save(case, monkeypatch):
    exports = _capture_worker(case, monkeypatch)
    case.desc["original_artifact"] = "F.pdf"
    original = case.root / "F.pdf"
    original.write_bytes(PDF)
    monkeypatch.setattr(m, "safe_resolve", lambda _: original)
    monkeypatch.setattr(m.engine_artifactcontext, "create_context", lambda *a: {})

    def reject(*args):
        raise ValueError("independent PDF admission rejected")

    monkeypatch.setattr(m.engine_artifactcontext, "validate_candidate", reject)
    calls = []
    monkeypatch.setattr(m, "_save_svg_snapshot", lambda f, **k: calls.append(f.artifact.source_id))
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)
    assert len(exports) == 1
    assert calls == [case.source_id]


@pytest.mark.parametrize("stem", ["F", "../F"])
def test_svg_binding_never_uses_a_mismatched_or_unsafe_candidate(case, monkeypatch, stem):
    (case.root / "F.svg").write_bytes(SVG.replace(b"L 30 20", b"L 31 20"))
    monkeypatch.setattr(
        m, "_save_svg_snapshot", lambda *a, **k: pytest.fail("bound unverified SVG")
    )
    m._bind_svg_asset(
        lambda sid: _pair(case, sid),
        case.root,
        stem,
        case.svg,
        OWNER,
        captured_input_archive=case.archive,
    )


def test_same_receipt_upgrades_v1_to_separate_v2_without_replacing_old_version(case, monkeypatch):
    frozen = _pair(case)
    old = m._save_svg_snapshot(frozen, project_root=case.root, execution_owner=OWNER)
    old_ref = copy.deepcopy(old["snapshot_ref"])
    store = m._svg_storage_root(case.root)
    old_path = store / "snapshots" / (old_ref["snapshotId"] + ".json")
    old_bytes = old_path.read_bytes()
    exports = _capture_worker(case, monkeypatch)
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)
    assert len(exports) == 1
    new = svgstore.load(case.root, case.source_id, PATCH, storage_root=store)
    assert new is not None and new["snapshot_ref"] != old_ref
    assert old_path.read_bytes() == old_bytes
    assert json.loads(old_bytes)["payload"]["version"] == 1
    assert _load(case, old_ref) is not None
    restored = svgstore.load_input_archive(new["snapshot_ref"], storage_root=store)
    assert restored.graph_bytes == case.archive.graph_bytes
    assert {r.sha256: r.path.read_bytes() for r in restored.resources} == {
        r.sha256: r.path.read_bytes() for r in case.archive.resources
    }
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)
    assert len(exports) == 1  # The same actual private graph is now already stored.


def test_same_receipt_without_private_bytes_preserves_original_v1_early_return(case, monkeypatch):
    m._save_svg_snapshot(_pair(case), project_root=case.root, execution_owner=OWNER)
    case.receipt = dataclasses.replace(case.receipt, captured_input_archive=None)
    monkeypatch.setattr(
        case.worker,
        "export",
        lambda *a, **k: pytest.fail("rerendered legacy cached pair"),
        raising=False,
    )
    m._capture_svg_snapshot_best_effort(case.worker, case.desc)


def test_original_input_deletion_does_not_replace_captured_bytes(case):
    expected = {r.sha256: r.path.read_bytes() for r in case.archive.resources}
    case.data.unlink()
    (case.root / "plot.py").unlink()
    saved = m._save_svg_snapshot(
        _pair(case),
        project_root=case.root,
        execution_owner=OWNER,
        captured_input_archive=case.archive,
    )
    restored = svgstore.load_input_archive(
        saved["snapshot_ref"], storage_root=m._svg_storage_root(case.root)
    )
    assert {r.sha256: r.path.read_bytes() for r in restored.resources} == expected


@pytest.mark.parametrize("damage", ["missing", "corrupt", "graph", "owner", "receipt"])
def test_bad_private_attachment_refuses_save_and_preserves_the_old_version(case, damage):
    frozen = _pair(case)
    old = m._save_svg_snapshot(frozen, project_root=case.root, execution_owner=OWNER)
    store = m._svg_storage_root(case.root)
    before = {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()}
    archive = case.archive
    owner = OWNER
    if damage == "missing":
        archive.resources[0].path.unlink()
    elif damage == "corrupt":
        resource = next(r for r in archive.resources if r.size_bytes)
        resource.path.write_bytes(b"X" * resource.size_bytes)
    elif damage == "graph":
        archive = dataclasses.replace(archive, graph_bytes=b"{}")
    elif damage == "owner":
        owner = {**OWNER, "script": "other.py"}
    else:
        frozen = dataclasses.replace(
            frozen,
            receipt={
                **frozen.receipt,
                "captured_inputs": {"version": 1, "revision": "sha256:" + "0" * 64},
            },
        )
    with pytest.raises((ValueError, OSError)):
        m._save_svg_snapshot(
            frozen,
            project_root=case.root,
            execution_owner=owner,
            captured_input_archive=archive,
        )
    assert {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()} == before
    assert _load(case, old["snapshot_ref"]) is not None


@pytest.mark.parametrize("field", ["source_id", "patch_hash", "generation"])
def test_private_bytes_do_not_relax_same_figure_companion_qualification(case, field):
    frozen = _pair(case)
    value = (
        2
        if field == "generation"
        else ("other-source" if field == "source_id" else "sha256:" + "1" * 64)
    )
    bad_svg = dataclasses.replace(
        frozen.svg, artifact=dataclasses.replace(frozen.svg.artifact, **{field: value})
    )
    with pytest.raises(sources.SourceError, match="does not belong"):
        m._save_svg_snapshot(
            dataclasses.replace(frozen, svg=bad_svg),
            project_root=case.root,
            execution_owner=OWNER,
            captured_input_archive=case.archive,
        )
