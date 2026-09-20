"""Codex source-bake: Tavotto target -> modified source + overrides=[] parity."""

import json
from pathlib import Path

from tavotto import app as m


def _manifest() -> dict:
    return {
        "stem": "Fig1",
        "size_mm": [80.0, 60.0],
        "elements": [
            {"gid": "axes_0", "bbox": [0.1, 0.1, 0.8, 0.8]},
            {"gid": "axes_0.title", "bbox": [0.3, 0.02, 0.4, 0.05], "anchor": [0.5, 0.04]},
        ],
    }


class _FreshSourceWorker:
    def __init__(self, tmp_path: Path, png_bytes: bytes, manifest: dict | None = None):
        self.out_dir = tmp_path / "fresh"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.png_bytes = png_bytes
        self.manifest = manifest or _manifest()
        self.override_patches = None

    def override(self, stem, patches):
        self.override_patches = list(patches)
        (self.out_dir / f"{stem}.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )
        return {"ok": True, "warnings": []}

    def render_png(self, stem, width_px):
        path = self.out_dir / f"{stem}_{width_px}.png"
        path.write_bytes(self.png_bytes)
        return path


def _target(tmp_path: Path, png_bytes: bytes) -> dict:
    target_png = tmp_path / "target.png"
    target_png.write_bytes(png_bytes)
    return {
        "schema": "tavotto.codex_source_bake.v1",
        "stem": "Fig1",
        "script": "fig1.py",
        "entry": "main",
        "patch_hash": "sha256:test",
        "manifest": _manifest(),
        "target_png": str(target_png),
    }


def test_source_bake_verifies_fresh_source_without_runtime_overrides(tmp_path, monkeypatch):
    fresh = _FreshSourceWorker(tmp_path, b"same-png")
    discarded = []
    monkeypatch.setattr(m.engine_pool, "one_shot", lambda *a, **k: fresh)
    monkeypatch.setattr(m.engine_pool, "discard", lambda w: discarded.append(w))

    target = _target(tmp_path, b"same-png")
    result = m._verify_ai_source_bake(target, True, str(tmp_path))

    assert result["status"] == "verified"
    assert result["reason"] == "verified"
    assert fresh.override_patches == [], "最终验证必须是 source-only，不能偷偷重放 Tavotto patches"
    assert result["differences"] == []
    assert result["pixels"]["status"] == "ok"
    assert discarded == [fresh]
    assert not Path(target["target_png"]).exists(), "冻结 target 是临时验证材料，验证后应清理"


def test_source_bake_pixel_gate_catches_geometry_neutral_style_mismatch(tmp_path, monkeypatch):
    fresh = _FreshSourceWorker(tmp_path, b"source-png")
    monkeypatch.setattr(m.engine_pool, "one_shot", lambda *a, **k: fresh)
    monkeypatch.setattr(m.engine_pool, "discard", lambda w: None)
    monkeypatch.setattr(
        m.pdfbackend,
        "compare_png",
        lambda a, b: {
            "ok": False,
            "changed_pixel_ratio": 0.01,
            "mean_abs_diff": 2.0,
            "max_abs_diff": 120,
        },
    )

    result = m._verify_ai_source_bake(_target(tmp_path, b"target-png"), True, str(tmp_path))

    assert result["status"] == "mismatch"
    assert result["reason"] == "visual_mismatch"
    assert result["elements_compared"] == 2, "几何可以完全一致，像素仍必须独立把关"
    pixel_diffs = [d for d in result["differences"] if d["field"] == "pixels"]
    assert len(pixel_diffs) == 1
    assert "changed_pixel_ratio" in pixel_diffs[0]["exceeded"]
    assert fresh.override_patches == []


def test_source_bake_without_source_change_is_not_reported_as_verified(tmp_path, monkeypatch):
    called = False

    def one_shot(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("source 没变时不应浪费一次 fresh run")

    monkeypatch.setattr(m.engine_pool, "one_shot", one_shot)
    target = _target(tmp_path, b"target")
    result = m._verify_ai_source_bake(target, False, str(tmp_path))

    assert result["status"] == "mismatch"
    assert result["reason"] == "no_source_change"
    assert called is False
    assert not Path(target["target_png"]).exists()
