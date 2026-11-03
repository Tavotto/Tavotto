"""Research staging contracts use only temporary bytes; no scientific script is run."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_guardian as guardian  # noqa: E402
import compat_matrix as matrix  # noqa: E402


class ResearchStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="guardian-staging-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.compat = self.root / "tests" / "compat"
        self.script = "cases/research/contract/upstream/src/plot.py.source"
        self.source = self.compat / self.script
        self.source.parent.mkdir(parents=True)
        self.source_bytes = b"# Original CRLF bytes\r\nraise AssertionError('do not execute')\r\n"
        self.source.write_bytes(self.source_bytes)
        self.case = {
            "script": self.script,
            "script_dest": "src/plot.py",
            "entry": "__main__",
        }
        patch = mock.patch.object(matrix.CC, "COMPAT_DIR", self.compat)
        patch.start()
        self.addCleanup(patch.stop)

    def test_archived_source_hash_matches_restored_execution_bytes(self):
        source_relative = self.source.relative_to(self.root).as_posix()
        head_digest = hashlib.sha256(self.source_bytes.replace(b"\r\n", b"\n")).hexdigest()
        fixture = {source_relative: head_digest}

        project, script, entry = matrix.materialize([self.case], self.root / "work")
        restored = project / script
        observed_hashes = guardian.staged_source_hashes(self.root, fixture, self.script)

        self.assertEqual(script, "src/plot.py")
        self.assertEqual(entry, "__main__")
        self.assertEqual(restored.read_bytes(), self.source_bytes)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertTrue(observed_hashes)
        self.assertEqual(observed_hashes, [hashlib.sha256(restored.read_bytes()).hexdigest()])
        self.assertNotIn(head_digest, observed_hashes)

    def test_registered_output_directories_are_created_inside_project(self):
        case = {**self.case, "project_directories": ["figures", "results/nested"]}

        project, script, _entry = matrix.materialize([case], self.root / "work")

        self.assertTrue((project / script).is_file())
        for relative in case["project_directories"]:
            directory = project / relative
            self.assertTrue(directory.is_dir())
            self.assertTrue(directory.resolve().is_relative_to(project.resolve()))
            self.assertEqual(list(directory.iterdir()), [])

    def test_project_directories_cannot_escape_by_parent_absolute_or_symlink(self):
        outside = self.root / "outside"
        for index, relative in enumerate(("../outside", str(outside))):
            with self.subTest(relative=relative):
                case = {**self.case, "project_directories": [relative]}
                with self.assertRaisesRegex(ValueError, "inside the materialized project"):
                    matrix.materialize([case], self.root / f"work-{index}")
                self.assertFalse(outside.exists())

        outside.mkdir()
        work = self.root / "work-symlink"
        project = work / "project"
        project.mkdir(parents=True)
        try:
            (project / "linked").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")
        case = {**self.case, "project_directories": ["linked/escaped"]}
        with self.assertRaisesRegex(ValueError, "inside the materialized project"):
            matrix.materialize([case], work)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
