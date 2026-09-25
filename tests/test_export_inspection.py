"""有限产物验证接进导出管线（统一实施包 U08，ADR 0068，D08）：`exportjob.run` 的 `inspect` 钩子在**提交点之前**重新打开
每个封口的临时文件，拒绝的不发布、合格的带 manifest 发布；发布后的字节就是核过的字节。

任何机器都跑（默认后端；PDF 在没有 pikepdf 的机器上走 `probe` 基本观测——完整性与尺寸照核，其余 unknown 并写明）。
主语是**导出目录里的文件与回执里的 manifest**，不是检查器返回的 dict。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exportreq
from tavotto.rendercore import inspector

SRC_W_PT, SRC_H_PT = 200.0, 150.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=SRC_W_PT, height=SRC_H_PT)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=None, fill=(0.1, 0.2, 0.8))
    page.insert_text((20, 120), "PanelText", fontsize=11)
    doc.save(figs / "p1.pdf")
    doc.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    yield m.app.test_client(), figs
    m.reset_projects()
    exportjob.reset_for_tests()


def _canvas(**over) -> dict:
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 60,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 10, "y_mm": 10, "w_mm": 40, "h_mm": 30},
                {
                    "type": "text",
                    "id": "t1",
                    "text": "Hello",
                    "x_mm": 60,
                    "y_mm": 10,
                    "w_mm": 40,
                    "h_mm": 10,
                    "size_pt": 10,
                },
            ],
        },
    }
    spec.update(over)
    return spec


def _out(body: dict, fmt: str) -> dict:
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 正例：发布的每个文件都带 manifest，且 sha256 就是磁盘上那个文件的
# ---------------------------------------------------------------------------
def test_every_published_output_carries_a_manifest_whose_sha_is_the_published_bytes(env):
    client, _ = env
    body = client.post("/api/export", json=_canvas()).get_json()
    assert body["status"] == "done", body
    out_dir = Path(body["export_dir"])
    for fmt in ("pdf", "png"):
        o = _out(body, fmt)
        mf = o["manifest"]
        assert mf is not None and mf["verdict"] == "accepted" and mf["policy"] == "standard"
        assert mf["checks"]["integrity"] == "verified" and mf["checks"]["size"] == "verified"
        assert mf["sha256"] == _sha(out_dir / o["name"])  # 发布后不改已核验字节
        assert mf["bytes"] == (out_dir / o["name"]).stat().st_size
        for v in mf["checks"].values():
            assert v in inspector.VERDICTS
    png = _out(body, "png")["manifest"]
    assert png["px"] == [709, 354] or abs(png["px"][0] - 709) <= 1
    assert body["request"]["inspection"] == {"mode": "standard", "profile_id": None}
    assert body["files"]  # 旧投影照旧（RC-090）


def test_a_manifest_without_a_plan_never_claims_a_verified_text_layer(env, monkeypatch):
    """D08 / RC-088：量不到的就是 unknown / not_applicable，不显示成绿。生产者没交计划半张（U10 之前旧后端
    走的就是这条路：没有计划里的文字行；退役后同一形状仍存在——MCP 直出路 / 别的生产者），文字层不许 verified。"""
    from tavotto.engine import artifactinspect

    client, _ = env
    real = artifactinspect.plan_half

    def no_plan(job, produced, *, backend):
        half = real(job, produced, backend=backend)
        half.pop("text", None)  # 计划里没有期望文字行
        return half

    monkeypatch.setattr(artifactinspect, "plan_half", no_plan)
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    mf = _out(body, "pdf")["manifest"]
    assert mf["checks"]["text_layer"] in ("unknown", "not_applicable"), mf["checks"]
    assert mf["backend"] == "rendercore"


# ---------------------------------------------------------------------------
# 负例：坏文件 / 错尺寸 / 伪造 proof 在发布之前被拦住
# ---------------------------------------------------------------------------
def _garbage_producer(job, tmp_dir):
    pdf = tmp_dir / "out.pdf"
    pdf.write_bytes(b"%PDF-1.4 garbage, not a document")
    return [
        exportjob.Produced(format="pdf", tmp_path=pdf, width_mm=120.0, height_mm=60.0, vector=True)
    ]


def test_a_broken_artifact_is_rejected_before_publish_and_the_export_dir_stays_clean(
    env, monkeypatch
):
    """RC-073：不合格的 staging 文件不出现在用户的导出目录里；作业报失败，错误码是 `artifact_rejected`。"""
    client, _ = env
    monkeypatch.setattr(m, "_export_produce", _garbage_producer)
    resp = client.post("/api/export", json=_canvas(formats=["pdf"]))
    body = resp.get_json()
    assert resp.status_code == 500 and body["status"] == "failed", body
    o = _out(body, "pdf")
    assert o["status"] == "failed" and o["error"]["code"] == "artifact_rejected"
    assert (
        o["error"]["params"]["failed"] == "integrity"
        and o["error"]["params"]["policy"] == "standard"
    )
    assert (
        o["manifest"]["verdict"] == "rejected" and o["manifest"]["checks"]["integrity"] == "failed"
    )
    out_dir = Path(body["export_dir"])
    assert not (out_dir / "Fig 1.pdf").exists()
    assert not list(out_dir.glob(f"{exportjob.TMP_PREFIX}*"))


def test_a_client_proof_cannot_override_the_server_verdict(env, monkeypatch):
    """RC-074：客户端自报 errors=0 的报告随请求来，服务端照样按文件字节拒绝；报告也不会替坏文件背书。"""
    client, _ = env
    monkeypatch.setattr(m, "_export_produce", _garbage_producer)
    body = client.post(
        "/api/export",
        json=_canvas(
            formats=["pdf"],
            include_style_check_report=True,
            style_check_report={"checks": [], "errors": 0, "ok": True, "verified": True},
        ),
    ).get_json()
    assert body["status"] == "failed"
    assert _out(body, "pdf")["error"]["code"] == "artifact_rejected"
    assert not (Path(body["export_dir"]) / "Fig 1.pdf").exists()


def test_a_page_of_the_wrong_actual_size_is_caught_not_reported_from_the_request(env, monkeypatch):
    """RC-064：请求 120 × 60 mm、文件里却是 100 × 50 pt——量的是文件。"""
    client, _ = env

    def wrong_size(job, tmp_dir):
        doc = pymupdf.open()
        doc.new_page(width=100, height=50)
        doc.save(tmp_dir / "out.pdf")
        doc.close()
        return [
            exportjob.Produced(
                format="pdf",
                tmp_path=tmp_dir / "out.pdf",
                width_mm=120.0,
                height_mm=60.0,
                vector=True,
            )
        ]

    monkeypatch.setattr(m, "_export_produce", wrong_size)
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    o = _out(body, "pdf")
    assert o["status"] == "failed" and o["error"]["code"] == "artifact_rejected"
    assert o["manifest"]["checks"]["size"] == "failed" and o["manifest"]["size_pt"] == [100.0, 50.0]


def test_a_partial_rejection_publishes_the_good_format_and_withholds_the_bad_one(env, monkeypatch):
    """RC-083：PDF 合格、PNG 是坏文件 → partial：PDF 在目录里，PNG 不在，两项都带 manifest。"""
    client, _ = env
    real = m._export_produce

    def produce(job, tmp_dir):
        produced = real(job, tmp_dir)
        for p in produced:
            if p.format == "png":
                p.tmp_path.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        return produced

    monkeypatch.setattr(m, "_export_produce", produce)
    body = client.post("/api/export", json=_canvas()).get_json()
    assert body["status"] == "partial", body
    out_dir = Path(body["export_dir"])
    assert (out_dir / "Fig 1.pdf").exists() and not (out_dir / "Fig 1.png").exists()
    assert _out(body, "pdf")["manifest"]["verdict"] == "accepted"
    png = _out(body, "png")
    assert png["error"]["code"] == "artifact_rejected" and png["manifest"]["verdict"] == "rejected"


# ---------------------------------------------------------------------------
# 政策
# ---------------------------------------------------------------------------
def test_strict_inspection_blocks_on_required_unknown_or_failure_but_standard_only_notes(env):
    """D08：同一次导出，standard 交付并把未核验项写在 manifest 里；strict 下必需项 unknown / failed 都拒。"""
    client, _ = env
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    std = _out(body, "pdf")["manifest"]
    assert std["verdict"] == "accepted"
    assert set(std["checks"]) == set(inspector.CHECKS_PDF)
    body = client.post(
        "/api/export",
        json=_canvas(formats=["pdf"], filename="Strict", inspection={"mode": "strict"}),
    ).get_json()
    assert body["status"] == "failed", body
    o = _out(body, "pdf")
    assert o["error"]["code"] == "artifact_rejected" and o["error"]["params"]["policy"] == "strict"
    mf = o["manifest"]
    assert mf["verdict"] == "rejected" and mf["policy"] == "strict"
    blocked = set(o["error"]["params"]["failed"].split(", "))
    assert blocked and blocked <= set(mf["required"])
    assert all(mf["checks"][k] in ("failed", "unknown") for k in blocked)
    assert not (Path(body["export_dir"]) / "Strict.pdf").exists()
    assert body["request"]["inspection"]["mode"] == "strict"


def test_strict_inspection_takes_its_thresholds_from_the_publication_profile(env, monkeypatch):
    """RC-071：min_raster_dpi 只从 profilestore 解析出来的规范来——改规范就改判据；不认识的 id 是 bad_inspection。"""
    client, _ = env
    seen: list = []
    real_inspect = inspector.inspect

    def spy(path, fmt, **kw):
        seen.append(kw.get("profile"))
        return real_inspect(path, fmt, **kw)

    monkeypatch.setattr(inspector, "inspect", spy)
    client.post(
        "/api/export",
        json=_canvas(formats=["png"], inspection={"mode": "strict", "profile_id": None}),
    ).get_json()
    assert seen and seen[-1] is not None
    from tavotto.engine import profilestore

    default = profilestore.resolve_spec(None)
    assert seen[-1]["min_raster_dpi"] == default["min_raster_dpi"]
    assert seen[-1]["profile_id"] == default["profile_id"]
    body = client.post(
        "/api/export",
        json=_canvas(formats=["png"], inspection={"mode": "strict", "profile_id": "no-such-spec"}),
    ).get_json()
    assert body["status"] == "failed" and body["error"]["code"] == "bad_inspection"


def test_a_raster_export_under_strict_passes_when_the_ppi_meets_the_profile(env):
    """strict 对位图的必需项是完整性 / 尺寸 / 密度（ppi ≥ 规范）：600 ppi 的 PNG 过，150 的不过。"""
    client, _ = env
    body = client.post(
        "/api/export", json=_canvas(formats=["png"], ppi=600, inspection={"mode": "strict"})
    ).get_json()
    assert body["status"] == "done", body
    assert _out(body, "png")["manifest"]["checks"]["raster_density"] == "verified"
    body = client.post(
        "/api/export", json=_canvas(formats=["png"], ppi=150, inspection={"mode": "strict"})
    ).get_json()
    assert body["status"] == "failed"
    assert _out(body, "png")["error"]["params"]["failed"] == "raster_density"


def test_unknown_inspection_mode_is_refused_at_normalize_time(env):
    client, _ = env
    resp = client.post("/api/export", json=_canvas(inspection={"mode": "lenient"}))
    assert resp.status_code == 400 and resp.get_json()["code"] == "bad_inspection"
    resp = client.post("/api/export", json=_canvas(inspection="strict"))
    assert resp.status_code == 400 and resp.get_json()["code"] == "bad_inspection"
    req = exportreq.normalize(
        {"stem": "t", "formats": ["pdf"], "page_w_mm": 10, "page_h_mm": 10, "objects": []}
    )
    assert req.inspection.mode == "standard" and req.inspection.profile_id is None


# ---------------------------------------------------------------------------
# exportjob 层：钩子在提交点之前
# ---------------------------------------------------------------------------
def test_the_inspect_hook_runs_before_the_commit_point_and_its_verdict_enters_the_outputs(tmp_path):
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        {
            "scope": "canvas",
            "filename": "F",
            "formats": ["pdf", "png"],
            "overwrite": "replace",
            "canvas": {"page_w_mm": 10, "page_h_mm": 10, "objects": []},
        },
        export_dir,
    )
    phases: list[str] = []

    def produce(j, tmp_dir):
        (tmp_dir / "a.pdf").write_bytes(b"%PDF-x")
        (tmp_dir / "a.png").write_bytes(b"png")
        return [
            exportjob.Produced(
                format="pdf", tmp_path=tmp_dir / "a.pdf", manifest={"plan": {"k": 1}}
            ),
            exportjob.Produced(format="png", tmp_path=tmp_dir / "a.png"),
        ]

    def inspect(j, produced):
        phases.append(j.phase)
        assert not j._committed and not (export_dir / "F.pdf").exists()  # 还没发布
        out = []
        for p in produced:
            if p.format == "png":
                out.append(
                    exportjob.Produced(
                        format="png",
                        error_code="artifact_rejected",
                        error_params={"failed": "integrity", "policy": "standard"},
                        manifest={"verdict": "rejected"},
                    )
                )
            else:
                p.manifest = {"verdict": "accepted", "plan": p.manifest["plan"]}
                out.append(p)
        return out

    payload = exportjob.run(job, produce, inspect=inspect)
    assert phases == ["inspecting"]
    assert payload["status"] == "partial"
    pdf = next(o for o in payload["outputs"] if o["format"] == "pdf")
    png = next(o for o in payload["outputs"] if o["format"] == "png")
    assert pdf["status"] == "done" and pdf["manifest"] == {"verdict": "accepted", "plan": {"k": 1}}
    assert png["status"] == "failed" and png["error"]["code"] == "artifact_rejected"
    assert png["manifest"] == {"verdict": "rejected"}
    assert (export_dir / "F.pdf").exists() and not (export_dir / "F.png").exists()
    assert "artifact_rejected" in exportjob.ERROR_CODES
    assert json.loads(json.dumps(payload))  # 可序列化


def test_an_inspector_crash_is_unknown_not_verified(env, monkeypatch):
    """检查器自己炸了 = 没检查：standard 交付但每一项都是 unknown 并写明；strict 阻断。绝不当通过。"""
    client, _ = env

    def boom(*a, **kw):
        raise RuntimeError("inspector exploded")

    monkeypatch.setattr(inspector, "inspect", boom)
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    assert body["status"] == "done", body
    mf = _out(body, "pdf")["manifest"]
    assert mf["verdict"] == "accepted" and set(mf["checks"].values()) == {"unknown"}
    assert any("inspector exploded" in n for n in mf["notes"])
    body = client.post(
        "/api/export", json=_canvas(formats=["pdf"], filename="S", inspection={"mode": "strict"})
    ).get_json()
    assert body["status"] == "failed"
    assert _out(body, "pdf")["error"]["code"] == "artifact_rejected"
    assert not (Path(body["export_dir"]) / "S.pdf").exists()


def test_raster_density_is_measured_from_pixels_over_page_not_taken_from_the_request(
    env, monkeypatch
):
    """RC-064 must_fail：请求 600 ppi、文件却只有 150 ppi 那么多像素（生产者如实报了像素数，所以尺寸一项对得上）
    ——密度必须按像素 ÷ 页面量出 150，strict 下按规范拒；拿请求的 600 去判就是「请求 600 就报告实测 600」。"""
    client, _ = env
    real = m._export_produce

    def produce(job, tmp_dir):
        assert job.request.ppi == 600
        lowered = job.request.__class__(**{**job.request.__dict__, "ppi": 150})
        job.request = lowered  # 生产者按 150 画、如实报 150 那么多像素
        try:
            produced = real(job, tmp_dir)
        finally:
            job.request = job.request.__class__(**{**job.request.__dict__, "ppi": 600})
        return produced

    monkeypatch.setattr(m, "_export_produce", produce)
    body = client.post(
        "/api/export", json=_canvas(formats=["png"], ppi=600, inspection={"mode": "strict"})
    ).get_json()
    o = _out(body, "png")
    assert o["manifest"]["checks"]["size"] == "verified"  # 像素数与生产者报的一致
    assert o["manifest"]["checks"]["raster_density"] == "failed"
    assert o["status"] == "failed" and o["error"]["params"]["failed"] == "raster_density"
