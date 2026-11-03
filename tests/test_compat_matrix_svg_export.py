"""SVG export measurement tests; no scientific scripts or real worker are run."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_matrix as matrix  # noqa: E402

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_ROOT = f"{{{SVG_NAMESPACE}}}svg"
VALID_SVG = (
    f'<svg xmlns="{SVG_NAMESPACE}" width="10" height="10" viewBox="0 0 10 10">'
    '<path d="M 1 1 L 9 9" stroke="black"/></svg>'
)


class SvgExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="compat-svg-export-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def write_svg(self, content):
        path = self.directory / "figure.svg"
        path.write_text(content, encoding="utf-8")
        return path

    def worker_for(self, content):
        def export(_stem, _patches, path, *, fmt, dpi):
            Path(path).write_text(content, encoding="utf-8")
            return {"warnings": [], "timings": {"export_ms": 1.25}}

        return mock.Mock(export=mock.Mock(side_effect=export))

    def test_svg_export_stage_accepts_a_small_valid_svg(self):
        worker = self.worker_for(VALID_SVG)
        out = self.directory / "exports"

        result = matrix.stage_export(worker, "figure", out, formats=["svg"])

        self.assertTrue(result["ok"], result)
        detail = result["detail"]["svg"]
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["root"], SVG_ROOT)
        self.assertEqual(detail["bytes"], len(VALID_SVG.encode("utf-8")))
        self.assertLess(detail["bytes"], 900)
        self.assertEqual(detail["warnings"], [])
        self.assertEqual(detail["export_ms"], 1.25)
        worker.export.assert_called_once_with(
            "figure", [], str(out / "figure.svg"), fmt="svg", dpi=600
        )

    def test_svg_namespace_is_parsed_for_default_and_prefixed_roots(self):
        fixtures = [
            VALID_SVG,
            f'<s:svg xmlns:s="{SVG_NAMESPACE}" width="10" height="10">'
            '<s:path d="M 1 1 L 9 9"/></s:svg>',
        ]
        for content in fixtures:
            with self.subTest(content=content):
                result = matrix._decode_check(self.write_svg(content), "svg")
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["root"], SVG_ROOT)

    def test_well_formed_xml_without_an_svg_root_is_rejected(self):
        fixtures = [
            "<report><value>42</value></report>",
            '<svg width="10" height="10"><path d="M 1 1 L 9 9"/></svg>',
            '<svg xmlns="urn:example:other" width="10" height="10"/>',
            f'<report><svg xmlns="{SVG_NAMESPACE}" width="10" height="10"/></report>',
            f'<html xmlns="http://www.w3.org/1999/xhtml"><svg xmlns="{SVG_NAMESPACE}"/></html>',
        ]
        for content in fixtures:
            with self.subTest(content=content):
                result = matrix._decode_check(self.write_svg(content), "svg")
                self.assertFalse(result["ok"], result)
                self.assertTrue(result.get("error"), result)

    def test_malformed_svg_xml_is_rejected(self):
        fixtures = [
            f'<svg xmlns="{SVG_NAMESPACE}"><path></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><path d="M 1 1 L 9 9"/>',
        ]
        for content in fixtures:
            with self.subTest(content=content):
                result = matrix._decode_check(self.write_svg(content), "svg")
                self.assertFalse(result["ok"], result)
                self.assertTrue(result.get("error"), result)

    def test_svg_export_stage_propagates_invalid_xml_failure(self):
        content = '<report description="This well formed document is not an SVG figure."></report>'
        worker = self.worker_for(content)

        result = matrix.stage_export(worker, "figure", self.directory / "exports", formats=["svg"])

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["detail"]["svg"]["ok"], result)
        self.assertTrue(result["detail"]["svg"].get("error"), result)


if __name__ == "__main__":
    unittest.main()
