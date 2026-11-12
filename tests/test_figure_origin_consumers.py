"""历史输入 P 与当前编辑 Q 在应用消费者中保持各自身份。"""

from __future__ import annotations

import copy
import json

import pymupdf
import pytest

import test_export_svg as svg_cases
import test_figure_origin_execution as execution
from tavotto import app as m
from tavotto.engine import exportreq, patchspec, profiles, svgstore

project = execution.project


@pytest.mark.parametrize("backend", execution.BACKENDS)
def test_history_sync_and_specfix_keep_the_admitted_origin(project, monkeypatch, backend):
    with execution._started(project, monkeypatch, backend) as world:
        execution._forbid_execution(world, monkeypatch)
        execution._json_ok(execution._render(world, world.q))
        m.append_baked(execution.STEM, world.p)
        history = execution._json_ok(
            world.client.post(
                "/api/engine/history", json={"id": world.source, "figure_origin": world.origin}
            )
        )
        assert history["figure_origin"] == world.origin
        assert history["versions"][-1]["patches"] == world.p
        synced = execution._json_ok(
            world.client.post(
                "/api/engine/sync_overrides",
                json={
                    "from_id": world.source,
                    "to_id": world.source,
                    "patches": world.q,
                    "target_patches": world.temporary,
                    "source_figure_origin": world.origin,
                    "target_figure_origin": world.origin,
                },
            )
        )
        assert synced["source_figure_origin"] == world.origin
        assert synced["target_figure_origin"] == world.origin
        assert synced["mapped"] == world.q
        fixed = world.client.post(
            "/api/engine/specfix",
            json={
                "id": world.source,
                "figure_origin": world.origin,
                "patches": world.q,
                "scale": 1,
                "profile": profiles.load(),
                "only": [{"rule": "not-a-real-rule", "gid": world.gid}],
            },
        )
        if backend == "native":
            assert fixed.status_code == 409, fixed.get_json()
            assert fixed.get_json()["code"] == "specfix_native_unsupported"
        else:
            body = execution._json_ok(fixed)
            assert body["figure_origin"] == world.origin
            assert body["patches"] == world.q
            assert not body["worker_retired"]
        assert world.counter.read_bytes() == b"run\n"


@pytest.mark.parametrize("backend", ("python", "workerd"))
def test_disk_origin_save_and_writeback_use_q_without_replacing_p(project, monkeypatch, backend):
    # savefig descriptors only claim disk artifacts that existed at capture.
    # Creating a same-name file afterwards cannot add historical provenance.
    with pymupdf.open() as seed:
        seed.new_page(width=144, height=72)
        seed.save(project[1] / "F.pdf")
    with execution._started(project, monkeypatch, backend) as world:
        artifact = world.root / "F.pdf"
        world.worker.export(execution.STEM, world.p, str(artifact), fmt="pdf")
        world.source = "F.pdf"
        saved = execution._json_ok(
            world.client.post(
                "/api/layouts/disk-origin?target=project",
                json=svg_cases.document(world.source, world.p),
            )
        )
        assert not saved.get("svg_snapshot_warnings"), saved
        world.origin = {
            "version": 1,
            "snapshot": saved["figure_snapshots"]["panel"],
            "patches": copy.deepcopy(world.p),
        }
        execution._json_ok(execution._render(world, world.q))
        document = svg_cases.document(world.source, world.q)
        document["canvases"][0]["objects"][0]["figureOrigin"] = copy.deepcopy(world.origin)
        before_count = world.counter.read_bytes()
        saved = execution._json_ok(
            world.client.post("/api/layouts/disk-edited?target=project", json=document)
        )
        assert not saved.get("svg_snapshot_warnings"), saved
        current = svgstore.inspect_portable_snapshot(
            saved["figure_snapshots"]["panel"], storage_root=m._svg_storage_root(world.root)
        )
        assert current["identity"]["patch_hash"] == patchspec.patch_hash(world.q)
        assert current["receipt"]["receipt_id"] == world.origin["snapshot"]["receiptId"]
        assert world.counter.read_bytes() == before_count == b"run\n"
        result = execution._json_ok(
            world.client.post(
                "/api/engine/update_source",
                json={
                    "id": world.source,
                    "figure_origin": world.origin,
                    "patches": world.q,
                    "expected_mtime": artifact.stat().st_mtime,
                },
            )
        )
        assert result["figure_origin"] == world.origin
        assert result["verification"]["pixels"] == "ok", result
        assert world.counter.read_bytes() == b"run\nrun\n"
        assert not list(world.root.glob(".*.updating"))


@pytest.mark.parametrize("scope", ("original", "canvas"))
def test_origin_private_instance_does_not_enter_public_render_plan(project, monkeypatch, scope):
    with execution._started(project, monkeypatch) as world:
        value = svg_cases.spec(world.source, scope=scope, patches=world.q)
        selected = value["original"] if scope == "original" else value["canvas"]["objects"][0]
        selected["figure_origin"] = copy.deepcopy(world.origin)
        resource = {
            "source_id": world.source,
            "origin": "execution",
            "kind": "pdf",
            "bytes_sha256": "a" * 64,
            "receipt_identity": "b" * 64,
            "receipt_id": world.origin["snapshot"]["receiptId"],
            "patch_hash": "c" * 64,
        }
        first = exportreq.render_plan_ref(exportreq.normalize(value), [resource])
        selected["figure_origin"]["snapshot"]["receiptId"] = "sha256:" + "d" * 64
        changed = {**resource, "receipt_id": "sha256:" + "d" * 64}
        second = exportreq.render_plan_ref(exportreq.normalize(value), [changed])
        assert first["plan_identity"] == second["plan_identity"]
        changed["receipt_identity"] = "e" * 64
        assert (
            exportreq.render_plan_ref(exportreq.normalize(value), [changed])["plan_identity"]
            != first["plan_identity"]
        )
        # The private request still carries the actual selected reference.
        parsed = exportreq.normalize(value)
        payload = parsed.to_payload() if scope == "original" else parsed.canvas.objects
        assert "d" * 64 in json.dumps(payload)
