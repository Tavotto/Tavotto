"""Native launch-control tests; no external research code or scientific stack is run."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_driver as driver  # noqa: E402
import compat_guardian as guardian  # noqa: E402


class ReferenceInvocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="guardian-invocation-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.script_dir = self.project / "src"
        self.sandbox = self.project / "sandbox"
        for directory in (self.project, self.script_dir, self.sandbox, self.project / "data"):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "input.txt").write_text(directory.name, encoding="utf-8")
        self.script = self.script_dir / "plot.py"
        self.script.write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "Path('observed.json').write_text(json.dumps({'cwd': os.getcwd(), "
            "'argv': sys.argv, 'data': Path('input.txt').read_text()}))\n",
            encoding="utf-8",
        )
        self.req = {
            "project": str(self.project),
            "script": str(self.script),
            "sandbox": str(self.sandbox),
            "engine_dir": str(self.root / "unused-engine"),
            "out_dir": str(self.root / "unused-renders"),
        }

    def run_control(self, mode, invocation=None):
        """Exercise the real driver entry and runpy, with empty matplotlib observers."""
        req = dict(self.req)
        if invocation is not None:
            req["reference_invocation"] = invocation
        matplotlib = types.ModuleType("matplotlib")
        matplotlib.use = mock.Mock()
        figure = types.ModuleType("matplotlib.figure")
        figure.Figure = type("Figure", (), {"savefig": lambda *_a, **_k: None})
        pyplot = types.ModuleType("matplotlib.pyplot")
        pyplot.get_fignums = lambda: []
        capture = types.ModuleType("figcapture")
        capture.collect_pyplot_figures = lambda *_a: ([], [])
        capture.adopt_displaced_writers = lambda *_a: None
        # run_native 的 PdfPages 认领（#1370）：与产品同名的三个函数，替身里什么都不认领
        capture.PdfPagesLedger = mock.Mock
        capture.is_pdf_pages = lambda _obj: False
        capture.adopt_pdf_pages = lambda *_a: []
        capture.install_relative_read_fallback = mock.Mock()
        modules = {
            "matplotlib": matplotlib,
            "matplotlib.figure": figure,
            "matplotlib.pyplot": pyplot,
            "figcapture": capture,
            "manifest": types.ModuleType("manifest"),
            "overrides": types.ModuleType("overrides"),
        }
        original_cwd = Path.cwd()
        try:
            with (
                mock.patch.dict(sys.modules, modules),
                mock.patch.object(sys, "path", list(sys.path)),
                mock.patch.object(sys, "argv", list(sys.argv)),
                mock.patch.object(driver, "_ok", side_effect=lambda **value: value),
            ):
                result = driver.MODES[mode](req)
        finally:
            os.chdir(original_cwd)
        if mode == "native_ref":
            self.assertEqual(result["exit_status"], 0, result)
        return capture.install_relative_read_fallback

    def test_registered_root_and_nested_cwd_and_literal_argv_reach_script(self):
        argv = ["--label", "空间 a b; $(touch should-not-exist)", "--value=-m"]
        for mode in ("native_ref", "native", "census"):
            for relative in (".", "data"):
                with self.subTest(mode=mode, cwd=relative):
                    fallback = self.run_control(mode, {"cwd": relative, "argv": argv})
                    directory = (self.project / relative).resolve()
                    observed = json.loads((directory / "observed.json").read_text())
                    self.assertEqual(observed["cwd"], str(directory))
                    self.assertEqual(observed["argv"], [str(self.script), *argv])
                    self.assertEqual(observed["data"], directory.name)
                    fallback.assert_not_called()

    def test_missing_metadata_preserves_each_modes_original_launch(self):
        for mode in ("native_ref", "native", "census"):
            with self.subTest(mode=mode):
                fallback = self.run_control(mode)
                directory = self.sandbox if mode == "census" else self.script_dir
                observed = json.loads((directory / "observed.json").read_text())
                self.assertEqual(observed["cwd"], str(directory))
                self.assertEqual(observed["argv"], [str(self.script)])
                if mode == "census":
                    fallback.assert_called_once_with(str(self.script_dir), str(self.project))

    def test_invalid_directory_and_argument_shapes_are_rejected(self):
        (self.root / "outside").mkdir()
        invalid = [
            {"cwd": "../outside", "argv": []},
            {"cwd": str(self.project), "argv": []},
            {"cwd": "C:\\outside", "argv": []},
            {"cwd": "C:outside", "argv": []},
            {"cwd": "\\outside", "argv": []},
            {"cwd": "missing", "argv": []},
            {"cwd": "src/plot.py", "argv": []},
            {"cwd": "", "argv": []},
            {"cwd": ".", "argv": "--flag"},
            {"cwd": ".", "argv": [1]},
            {"cwd": ".", "argv": [], "script": "other.py"},
            None,
        ]
        for invocation in invalid:
            with self.subTest(invocation=invocation):
                req = {**self.req, "reference_invocation": invocation}
                with self.assertRaises(driver.ReferenceInvocationError):
                    driver._reference_invocation(req, str(self.script_dir))

    @unittest.skipIf(os.name == "nt", "Windows symlink creation needs extra privileges")
    def test_symlink_to_directory_outside_project_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "escape").symlink_to(outside, target_is_directory=True)
        req = {**self.req, "reference_invocation": {"cwd": "escape", "argv": []}}
        with self.assertRaises(driver.ReferenceInvocationError):
            driver._reference_invocation(req, str(self.script_dir))

    def test_invalid_request_fails_before_any_scientific_import_or_script_execution(self):
        req = {**self.req, "reference_invocation": {"cwd": "..", "argv": []}}
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(req), encoding="utf-8")
        for mode in ("native_ref", "native", "census"):
            with self.subTest(mode=mode), mock.patch.object(driver, "_fail") as fail:
                driver.main(["--mode", mode, "--request", str(request_file)])
                self.assertEqual(fail.call_args.args[0], "invalid_reference_invocation")
        self.assertFalse(list(self.project.rglob("observed.json")))


class GuardianInvocationForwardingTests(unittest.TestCase):
    def test_only_native_controls_receive_registered_metadata(self):
        for invocation in (None, {"cwd": ".", "argv": ["--dataset", "a b.csv"]}):
            with self.subTest(invocation=invocation), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                project = work / "project"
                project.mkdir()
                case = {"id": "registered", "expected": {}}
                if invocation is not None:
                    case["reference_invocation"] = invocation
                cm = types.ModuleType("compat_matrix")
                cm.materialize = mock.Mock(return_value=(project, "src/plot.py", "__main__"))
                captured = []

                def run_driver(_python, mode, req, _timeout=900):
                    captured.append((mode, dict(req)))
                    return {"ok": True, "figure_count": 1}

                def run_group(*_a, **_k):
                    for mode in ("native", "census", "browser"):
                        cm.run_driver(sys.executable, mode, {"script": "registered-script.py"})
                    raise RuntimeError("stop after native-control request observation")

                cm.run_driver = run_driver
                cm.run_group = run_group
                cm.stage_export = mock.Mock()
                with (
                    mock.patch.dict(sys.modules, {"compat_matrix": cm}),
                    mock.patch.dict(os.environ, {}),
                    mock.patch.object(guardian, "registered", return_value={"registered": case}),
                    mock.patch.object(guardian, "environment", return_value={}),
                    mock.patch.object(guardian, "preflight", return_value=[]),
                    mock.patch.object(guardian, "_count", return_value=1),
                    mock.patch.object(guardian, "_native_status", return_value="passed"),
                    mock.patch.object(guardian, "_trim_native", side_effect=lambda value: value),
                    mock.patch.object(guardian, "finalize_row", side_effect=lambda value: value),
                ):
                    guardian._execute(
                        {
                            "suite": "compat_engine",
                            "case": "registered",
                            "work": str(work),
                            "evidence": str(work / "evidence"),
                        }
                    )
                self.assertEqual(
                    [mode for mode, _req in captured], ["native_ref", "native", "census", "browser"]
                )
                for mode, req in captured:
                    if invocation is not None and mode != "browser":
                        self.assertEqual(req["reference_invocation"], invocation)
                    else:
                        self.assertNotIn("reference_invocation", req)
                self.assertEqual(captured[0][1]["script"], str(project / "src/plot.py"))


if __name__ == "__main__":
    unittest.main()
