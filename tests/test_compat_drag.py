"""CompatBench「拖动」阶段的判据（`scripts/ci/compat_drag.py`）：每一条裁决各一个用例。

用一个假 worker 按 override 列表回 manifest，不起真进程：判据是纯函数加一个 worker 句柄，
这里钉住的是**怎么判**；真图上的行为由 corpus 的 `art_layout_annotations` 与产品用例
（`test_axes_layout_*_drag.py`）看着。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ci"))

import compat_drag  # noqa: E402

AX = {
    "gid": "axes_0",
    "role": "axes",
    "bbox": [0.1, 0.1, 0.8, 0.8],
    "editable": [{"prop": "xlim", "value": [0.0, 10.0]}, {"prop": "ylim", "value": [0.0, 5.0]}],
}


def _el(gid, role, bbox, **kw):
    return {"gid": gid, "role": role, "bbox": list(bbox), "draggable": False, **kw}


class FakeWorker:
    """`moving` 里的 gid 随 xlim 平移；`drift` 为真时撤销后子图框差一截（回不到基线）。"""

    def __init__(self, elements, moving=(), drift=False):
        self.elements = elements
        self.moving = set(moving)
        self.drift = drift
        self.calls = []

    def override(self, stem, patches):
        self.calls.append(list(patches))
        els = [dict(e) for e in self.elements]
        if patches:
            for e in els:
                if e["gid"] in self.moving:
                    b = e["bbox"]
                    e["bbox"] = [b[0] + 0.05, b[1], b[2], b[3]]
        elif self.drift and len(self.calls) > 1:
            for e in els:
                if e["gid"] == "axes_0":
                    e["bbox"] = [0.11, 0.1, 0.8, 0.8]
        return {"manifest": {"elements": els, "size_mm": [100.0, 80.0]}, "warnings": []}


def _audit(elements, **kw):
    return compat_drag.audit(FakeWorker([AX, *elements], **kw), "S")


def _gids(rows):
    return [r["gid"] for r in rows]


def test_static_placed_element_that_cannot_drag_is_a_finding():
    res = _audit([_el("axes_0.lines_0", "line", [0.3, 0.3, 0.1, 0.1])])
    assert _gids(res["findings"]) == ["axes_0.lines_0"]
    assert not compat_drag.verdict(res)


def test_element_that_moves_with_the_data_is_not_judged():
    res = _audit([_el("axes_0.lines_0", "line", [0.3, 0.3, 0.1, 0.1])], moving=["axes_0.lines_0"])
    assert res["findings"] == [] and res["excused"] == []
    assert compat_drag.verdict(res)


def test_draggable_or_delegated_or_reasoned_elements_are_excused():
    res = _audit(
        [
            _el("axes_0.lines_0", "line", [0.3, 0.3, 0.1, 0.1], draggable=True),
            _el("axes_0.texts_0.t", "text", [0.3, 0.3, 0.1, 0.1], drag_host="axes_0.artists_0"),
            _el(
                "axes_0.arrows_0",
                "arrow_patch",
                [0.3, 0.3, 0.1, 0.1],
                arrow_endpoints=[[0, 0], [1, 1]],
            ),
            _el(
                "axes_0.patches_0",
                "patch",
                [0.3, 0.3, 0.1, 0.1],
                unsupported_props=[{"prop": "pos_frac", "reason": "pixel_offset"}],
            ),
        ]
    )
    assert res["findings"] == []
    assert {r["gid"]: r["why"] for r in res["excused"]} == {
        "axes_0.lines_0": "draggable",
        "axes_0.texts_0.t": "drag_host",
        "axes_0.arrows_0": "arrow_endpoints",
        "axes_0.patches_0": "reason:pixel_offset",
    }


def test_structural_roles_and_empty_data_are_not_judged():
    empty = {"status": "ok", "parts": {"xy": {"shape": [0, 2], "finite": 0}}}
    res = _audit(
        [
            _el("axes_0.spine_left", "spine", [0.1, 0.1, 0.0, 0.8]),
            _el("axes_0.lines_13", "line", [-0.01, 0.98, 0.02, 0.03], data_fingerprint=empty),
        ]
    )
    assert res["findings"] == [] and res["excused"] == []


def test_figure_level_elements_are_judged_without_perturbation():
    res = _audit([_el("fig.artists_0", "linecoll", [0.1, 0.05, 0.8, 0.0])])
    assert _gids(res["findings"]) == ["fig.artists_0"]


def test_both_axes_are_perturbed_and_every_perturbation_is_undone():
    w = FakeWorker([AX, _el("axes_0.lines_0", "line", [0.3, 0.3, 0.1, 0.1])])
    compat_drag.audit(w, "S")
    perturbations = [c for c in w.calls if c]
    assert [p[0]["prop"] for p in perturbations] == ["xlim", "ylim"]
    assert perturbations[0][0]["value"] == [2.5, 12.5]
    # 每次扰动之后紧跟一次撤销
    for i, c in enumerate(w.calls):
        if c:
            assert w.calls[i + 1] == []


def test_undo_that_does_not_return_to_baseline_fails_the_stage():
    res = _audit([_el("axes_0.lines_0", "line", [0.3, 0.3, 0.1, 0.1], draggable=True)], drift=True)
    assert res["findings"] == []
    assert res["restore_errors"] and "axes_0" in res["restore_errors"][0]
    assert not compat_drag.verdict(res)


def test_stage_is_registered_with_corpus_and_taxonomy():
    import compat_corpus as CC
    import compat_guardian_taxonomy as tax

    assert "drag" in CC.STAGES and CC.STAGES.index("drag") > CC.STAGES.index("export")
    assert "drag" in CC.STAGE_LABELS
    assert tax.STAGE_TO_CATEGORY["drag"] == "editing_capability"
