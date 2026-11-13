"""计算面板的派生产物身份独立于 Figure 回执，旧来源合同保持不变。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from tavotto.engine import figcapture
from tavotto.rendercore import ir, plan, sources

REVISION = "1" * 64
OTHER_REVISION = "2" * 64


def artifact(kind="pdf", *, origin="declarative", **changes):
    values = {
        "source_id": "figures/chart.svg",
        "origin": origin,
        "kind": kind,
        "bytes_sha256": "a" * 64,
        "size_bytes": 10,
    }
    if origin == "declarative":
        values["render_revision"] = REVISION
    elif origin == "execution":
        values.update(receipt_id="run-1", receipt_identity="public-1")
    return figcapture.SourceArtifact(**{**values, **changes})


def frozen(kind="pdf", *, origin="declarative", **changes):
    return sources.FrozenSource(artifact(kind, origin=origin, **changes), Path("unused." + kind))


@pytest.mark.parametrize("revision", [None, "", "a" * 63, "a" * 65, "A" * 64, 1, True])
def test_declarative_sources_require_a_closed_render_revision(revision):
    with pytest.raises(ValueError, match="render_revision"):
        artifact(render_revision=revision)


@pytest.mark.parametrize("origin", ["static", "execution"])
def test_old_source_origins_cannot_borrow_a_declarative_revision(origin):
    with pytest.raises(ValueError, match="只属于 declarative"):
        artifact(origin=origin, render_revision=REVISION)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("receipt_id", "run-1"),
        ("receipt_id", ""),
        ("receipt_identity", "public-1"),
        ("generation", 0),
        ("generation", 1),
        ("patch_hash", "sha1:edits"),
    ],
)
def test_declarative_sources_cannot_claim_figure_execution(field, value):
    with pytest.raises(ValueError, match="没有 Figure 执行身份"):
        artifact(**{field: value})


@pytest.mark.parametrize("origin", ["static", "execution"])
def test_old_payload_bytes_and_semantic_identity_keep_the_existing_contract(origin):
    source = artifact(origin=origin)
    expected = {
        "source_id": "figures/chart.svg",
        "origin": origin,
        "kind": "pdf",
        "bytes_sha256": "a" * 64,
        "size_bytes": 10,
        "receipt_id": "run-1" if origin == "execution" else None,
        "generation": None,
        "patch_hash": None,
        "receipt_identity": "public-1" if origin == "execution" else None,
        "source_artifact_version": 1,
    }
    # 不仅键值相等，默认序列化的字段顺序与缺键行为也必须保持。
    assert json.dumps(source.to_payload()) == json.dumps(expected)
    semantic = {
        "source_id": expected["source_id"],
        "origin": origin,
        "kind": "pdf",
        "receipt_identity": expected["receipt_identity"],
        "patch_hash": None,
    }
    old_digest = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert source.semantic_identity() == "sha256:" + old_digest


def test_declarative_semantics_include_the_calculation_revision_but_not_output_bytes():
    first = artifact()
    assert first.to_payload()["render_revision"] == REVISION
    assert first.semantic_identity() != artifact(render_revision=OTHER_REVISION).semantic_identity()
    assert first.semantic_identity() != artifact(source_id="other.svg").semantic_identity()
    assert first.semantic_identity() != artifact("svg").semantic_identity()
    assert (
        first.semantic_identity()
        == artifact(bytes_sha256="b" * 64, size_bytes=20).semantic_identity()
    )


def test_declarative_file_factory_freezes_actual_bytes_and_detects_later_mutation(tmp_path):
    path = tmp_path / "canonical.pdf"
    raw = b"%PDF frozen computation"
    path.write_bytes(raw)
    source = figcapture.source_artifact_from_file(
        path,
        source_id="figures/chart.svg",
        origin=figcapture.ORIGIN_DECLARATIVE,
        render_revision=REVISION,
    )
    fs = sources.FrozenSource(source, path)
    assert source.bytes_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.size_bytes == len(raw)
    assert sources.read_frozen(fs) == raw
    path.write_bytes(b"%PDF changed computation")
    with pytest.raises(sources.SourceError) as exc:
        sources.read_frozen(fs)
    assert exc.value.code == "source_changed"


def test_declarative_pdf_and_svg_pair_only_with_the_same_source_and_revision():
    companion = frozen("svg")
    source = dataclasses.replace(frozen(), svg=companion)
    assert source.svg_representation() is companion
    assert source.svg_text_representation() is None


@pytest.mark.parametrize(
    "companion",
    [
        frozen("svg", source_id="other.svg"),
        frozen("svg", render_revision=OTHER_REVISION),
        frozen("svg", origin="static"),
        frozen("svg", origin="execution"),
        frozen("pdf"),
        dataclasses.replace(frozen("svg"), svg=frozen("svg")),
        dataclasses.replace(frozen("svg"), svg_text=frozen("svg")),
    ],
)
def test_declarative_pairing_rejects_foreign_versions_origins_kinds_and_nested_companions(
    companion,
):
    with pytest.raises(sources.SourceError) as exc:
        dataclasses.replace(frozen(), svg=companion).svg_representation()
    assert exc.value.code == "source_needs_execution"


@pytest.mark.parametrize("origin", ["static", "execution"])
def test_declarative_companions_cannot_authorize_an_old_source_origin(origin):
    with pytest.raises(sources.SourceError) as exc:
        dataclasses.replace(frozen(origin=origin), svg=frozen("svg")).svg_representation()
    assert exc.value.code == "source_needs_execution"


def test_static_pairs_stay_untrusted_and_existing_figure_pairing_stays_strict():
    static = dataclasses.replace(frozen(origin="static"), svg=frozen("svg", origin="static"))
    with pytest.raises(sources.SourceError):
        static.svg_representation()
    companion = frozen("svg", origin="execution")
    executed = dataclasses.replace(frozen(origin="execution"), svg=companion)
    assert executed.svg_representation() is companion
    wrong_receipt = dataclasses.replace(
        companion, artifact=dataclasses.replace(companion.artifact, receipt_identity="public-2")
    )
    with pytest.raises(sources.SourceError):
        dataclasses.replace(executed, svg=wrong_receipt).svg_representation()
    admitted = dataclasses.replace(
        frozen(origin="static"), svg=companion, svg_source_sha256="a" * 64
    )
    assert admitted.svg_representation() is companion


def test_the_figure_execution_resolver_does_not_accept_a_declarative_substitute(tmp_path):
    resolver = sources.ExecutionSourceResolver(
        sources.StaticSourceResolver(tmp_path), lambda _obj: frozen()
    )
    with pytest.raises(sources.SourceError) as exc:
        resolver.resolve({"id": "figures/chart.svg", "overrides": [{"gid": "line"}]})
    assert exc.value.code == "source_needs_execution"


def test_two_calculation_versions_have_separate_resources_even_with_identical_pdf_bytes():
    versions = iter([frozen(), frozen(render_revision=OTHER_REVISION)])

    class Resolver:
        def resolve(self, _obj):
            return next(versions)

    panel = {
        "type": "panel",
        "id": "figures/chart.svg",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 10,
        "h_mm": 10,
    }
    compiled = plan.compile_page(
        100, 100, [panel, {**panel, "x_mm": 20}], sources=Resolver(), faces=None
    )
    resources = compiled.page.resources
    assert len(resources) == 2
    assert compiled.page.children[0].resource != compiled.page.children[1].resource
    assert {fs.artifact.render_revision for fs in compiled.sources.values()} == {
        REVISION,
        OTHER_REVISION,
    }


@pytest.mark.parametrize("origin", ["static", "execution", "declarative"])
def test_ir_accepts_native_svg_pages_without_admitting_unpaired_execution_or_images(origin):
    resource = ir.FileResource("chart.svg", "pdf", "a" * 64, origin, "sha256:" + "b" * 64)
    placed = ir.ImportedPage("source", (10, 20, 30, 40))
    page = ir.Page(100, 100, (placed,), {"source": resource})
    assert ir.validate(page) is page
    svg_page = dataclasses.replace(
        page, resources={"source": dataclasses.replace(resource, kind="svg")}
    )
    if origin in ("static", "declarative"):
        assert ir.validate(svg_page) is svg_page
        # Native SVG is an imported page, not a general bitmap file kind.
        assert "svg" not in ir.FILE_KINDS
        with pytest.raises(ir.IRError) as exc:
            ir.validate(dataclasses.replace(svg_page, children=(ir.Image("source", placed.rect),)))
        assert exc.value.code == "resource_kind_mismatch"
    else:
        with pytest.raises(ir.IRError) as exc:
            ir.validate(svg_page)
        assert exc.value.code == "resource_kind_mismatch"
    for kind in ("eps", "svgz", "SVG"):
        with pytest.raises(ir.IRError) as exc:
            ir.validate(
                dataclasses.replace(
                    page, resources={"source": dataclasses.replace(resource, kind=kind)}
                )
            )
        assert exc.value.code == "resource_kind_mismatch"


@pytest.mark.parametrize("origin", ["static", "declarative"])
def test_native_svg_is_its_own_representation_without_forging_a_pdf_or_receipt(tmp_path, origin):
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="10pt" height="5pt"><rect width="10" height="5" fill="red"/></svg>'
    path = tmp_path / "source.svg"
    path.write_bytes(raw)
    source = figcapture.source_artifact_from_file(
        path,
        source_id="figures/chart.svg",
        origin=origin,
        **({"render_revision": REVISION} if origin == "declarative" else {}),
    )
    svg = sources.FrozenSource(source, path)
    assert svg.svg_representation() is svg
    assert sources.read_frozen(svg.svg_representation()) == raw
    assert svg.path is path and svg.artifact is source
    assert svg.artifact.kind == "svg" and svg.artifact.origin == origin
    assert svg.artifact.bytes_sha256 == hashlib.sha256(raw).hexdigest()
    assert svg.artifact.size_bytes == len(raw)
    assert svg.receipt is svg.svg is svg.svg_text is None
    for field, value in (
        ("receipt_id", "run-1"),
        ("receipt_identity", "public-1"),
        ("generation", 0),
        ("patch_hash", "sha1:edits"),
    ):
        assert getattr(svg.artifact, field) is None
        with pytest.raises(ValueError, match="没有.*执行身份"):
            dataclasses.replace(svg.artifact, **{field: value})
    for invalid in (
        dataclasses.replace(svg, svg=frozen("svg", origin=origin)),
        dataclasses.replace(svg, svg_text=frozen("svg", origin=origin)),
    ):
        with pytest.raises(sources.SourceError) as exc:
            invalid.svg_representation()
        assert exc.value.code == "source_needs_execution"
    assert frozen("svg", origin="execution").svg_representation() is None


def test_ir_still_rejects_unknown_origins_and_bad_byte_identity():
    source = ir.FileResource("chart.svg", "pdf", "a" * 64, "declarative", "sha256:" + "b" * 64)
    for invalid in (
        dataclasses.replace(source, origin="generated"),
        dataclasses.replace(source, sha256="not-a-sha256"),
    ):
        with pytest.raises(ir.IRError) as exc:
            ir.validate(ir.Page(100, 100, (), {"source": invalid}))
        assert exc.value.code == "unknown_resource"
