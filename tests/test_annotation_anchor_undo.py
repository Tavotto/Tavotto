"""Annotation text/arrow shared-anchor undo through real workers (issue #568)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tavotto.engine import pool  # noqa: E402

FIXTURE = Path(__file__).parent / "compat/cases/metamorphic/mm_annotated_oo.py"
STEM = "mm_annotated_oo"
TEXT = "axes_0.texts_0"
ARROW = TEXT + ".arrow"
POSITION = {"gid": TEXT, "prop": "pos_frac", "value": [0.65, 0.35]}
CLEAR = {"gid": TEXT, "prop": "text", "value": ""}
ENDPOINTS = {"gid": ARROW, "prop": "endpoints_frac", "value": [0.30, 0.65, 0.55, 0.40]}


class AnnotationAnchorUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="annotation-undo-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        shutil.copy2(FIXTURE, self.project / "script.py")

    @contextmanager
    def worker(self):
        worker = pool.one_shot("script.py", str(self.project), "main")
        try:
            worker.ensure_built()
            yield worker
        finally:
            pool.discard(worker)

    def observe(self, worker, patches):
        response = worker.override(STEM, patches)
        self.assertEqual(response.get("warnings") or [], [])
        elements = {element["gid"]: element for element in response["manifest"]["elements"]}
        text = elements.get(TEXT) or {}
        path = worker.preview_png(STEM, patches, 380, "annotation-undo")
        with Image.open(path) as image:
            pixels = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        return {
            "anchor": text.get("anchor"),
            "arrow": elements[ARROW]["bbox"],
            "endpoints": elements[ARROW].get("arrow_endpoints"),
            "rgba": pixels,
        }

    def assert_same(self, actual, expected):
        self.assertEqual(actual["rgba"], expected["rgba"])
        for field in ("anchor", "arrow"):
            if actual[field] is None or expected[field] is None:
                self.assertEqual(actual[field], expected[field])
            else:
                for got, want in zip(actual[field], expected[field], strict=True):
                    self.assertAlmostEqual(got, want, delta=1e-12)

    def test_full_undo_matches_untouched_and_fresh(self):
        with self.worker() as hot, self.worker() as fresh:
            baseline = self.observe(hot, [])
            for patches in ([POSITION], [POSITION, CLEAR], [POSITION, CLEAR, ENDPOINTS]):
                self.observe(hot, patches)
            undone = self.observe(hot, [])
            self.assert_same(undone, baseline)
            self.assert_same(undone, self.observe(fresh, []))

    def test_shared_original_and_survivors_match_fresh_in_both_orders(self):
        starts = ([POSITION, CLEAR, ENDPOINTS], [CLEAR, ENDPOINTS, POSITION])
        survivors = ([POSITION], [CLEAR, ENDPOINTS], [POSITION, ENDPOINTS], [ENDPOINTS, POSITION])
        for start in starts:
            for survivor in survivors:
                with (
                    self.subTest(start=start, survivor=survivor),
                    self.worker() as hot,
                    self.worker() as fresh,
                ):
                    self.observe(hot, start)
                    observed = self.observe(hot, survivor)
                    self.assert_same(observed, self.observe(fresh, survivor))
                    if POSITION in survivor and CLEAR not in survivor:
                        for got, want in zip(observed["anchor"], POSITION["value"], strict=True):
                            self.assertAlmostEqual(got, want, delta=1e-6)
                    if CLEAR in survivor and ENDPOINTS in survivor:
                        points = [v for point in observed["endpoints"] for v in point]
                        for got, want in zip(points, ENDPOINTS["value"], strict=True):
                            self.assertAlmostEqual(got, want, delta=1e-6)
                    self.assert_same(self.observe(hot, survivor), self.observe(fresh, survivor))
                    self.assert_same(self.observe(hot, []), self.observe(fresh, []))

    def test_changed_position_replays_unchanged_endpoints(self):
        moved = {"gid": TEXT, "prop": "pos_frac", "value": [0.20, 0.30]}
        for start, followup in (
            ([CLEAR, POSITION, ENDPOINTS], [CLEAR, moved, ENDPOINTS]),
            ([CLEAR, ENDPOINTS, POSITION], [CLEAR, ENDPOINTS, moved]),
            ([CLEAR, ENDPOINTS], [CLEAR, moved, ENDPOINTS]),
        ):
            with self.subTest(start=start), self.worker() as hot, self.worker() as fresh:
                self.observe(hot, start)
                self.assert_same(self.observe(hot, followup), self.observe(fresh, followup))

    def test_layouts_keep_the_native_pin_after_arrow_then_text(self):
        source = FIXTURE.read_text(encoding="utf-8")
        original = "plt.subplots(figsize=(3.8, 2.4))"
        self.assertEqual(source.count(original), 1)
        for layout in ("constrained", "tight"):
            with self.subTest(layout=layout):
                (self.project / "script.py").write_text(
                    source.replace(
                        original, f"plt.subplots(figsize=(3.8, 2.4), layout={layout!r})"
                    ),
                    encoding="utf-8",
                )
                with self.worker() as hot, self.worker() as fresh:
                    for patches in ([CLEAR], [CLEAR, ENDPOINTS], [CLEAR, ENDPOINTS, POSITION]):
                        self.observe(hot, patches)
                    for patches in ([ENDPOINTS, POSITION], []):
                        self.assert_same(self.observe(hot, patches), self.observe(fresh, patches))

    def test_pin_original_and_post_clear_native_changes_are_preserved(self):
        # Inspect the layout input directly, in the scientific interpreter only.
        code = """
import json, runpy, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import manifest as M
import overrides as O
runpy.run_path('script.py')['main']()
fig = plt.gcf()
fig.canvas.draw()
ann = fig.axes[0].texts[0]
state = O.FigState(fig)
M.instrument(state)
position, clear, endpoints = json.loads(sys.argv[2])
for patches in ([clear, endpoints], [clear, endpoints, position]):
    assert O.apply(state, patches) == []
assert O._text_pins(fig)[ann][1][0] == (3.4, 0.8), 'layout pin original is contaminated'
assert O.apply(state, []) == []
ann.xy, ann.xyann = (1.25, 0.55), (4.2, 0.65)
ann.set_annotation_clip(False)
ann.set_text('changed by native code')
M.instrument(state)
for patches in ([position], [position, clear, endpoints], []):
    assert O.apply(state, patches) == []
assert ann.xy == (1.25, 0.55) and ann.xyann == (4.2, 0.65)
assert ann.get_annotation_clip() is False and ann.get_text() == 'changed by native code'
assert state.applied == {} and state.originals == {} and ann not in O._text_pins(fig)
"""
        import json

        result = subprocess.run(
            [
                pool.find_worker_python(),
                "-c",
                code,
                str(Path(__file__).resolve().parents[1] / "src/tavotto/engine"),
                json.dumps([POSITION, CLEAR, ENDPOINTS]),
            ],
            cwd=self.project,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
