"""Tests the plan validator, NOT Tavotto implementation behavior."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path

import validate_plan as v

# Windows 上 stdout / stderr 一被重定向就退回系统区域编码（cp1252 / cp936），第一句
# 中文输出就 UnicodeEncodeError；这些工具都会被 subprocess 捕获着调用，两条流一起钉。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]


class PlanIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.plan = v.read_json(ROOT / "plan.json")
        self.registry = v.read_json(ROOT / "registry.json")
        self.sources = v.read_json(ROOT / "sources_manifest.json")
        self.mapping = v.read_json(ROOT / "phase_mapping.json")

    def errors(self):
        return v.validate_data(ROOT, self.plan, self.registry, self.sources, self.mapping)

    def rejects(self, fragment):
        self.assertTrue(any(fragment in e for e in self.errors()), self.errors())

    def test_01_valid_plan(self):
        self.assertEqual(self.errors(), [])

    def test_02_dropped_requirement(self):
        self.registry["entries"].pop(0)
        self.rejects("traceability set")

    def test_03_duplicate_case(self):
        self.registry["entries"].append(deepcopy(self.registry["entries"][-1]))
        self.rejects("duplicated")

    def test_04_original_requirement_cannot_be_silently_rewritten(self):
        self.registry["entries"][0]["original"]["requirement"] = "No tests needed"
        self.rejects("original source text")

    def test_05_source_hash_failure(self):
        self.sources[0]["sha256"] = "0" * 64
        self.rejects("integrity mismatch")

    def test_06_unknown_dependency(self):
        self.plan["stages"][1]["dependencies"] = ["U98.not_real"]
        self.rejects("unknown dependency")

    def test_07_cyclic_dependency(self):
        self.plan["stages"][0]["dependencies"] = ["U11.release_qualification"]
        self.rejects("cyclic")

    def test_08_enforced_without_qualification(self):
        self.registry["entries"][0]["enrollment"] = "enforced"
        self.rejects("promotion contract")

    def test_09_premature_global_legacy_ban(self):
        self.plan["policy"]["global_pymupdf_runtime_ban_begins"] = "U00"
        self.rejects("policy mismatch")

    def test_10_changed_stable_gates(self):
        self.plan["stable_gate_contexts"] = ["Always green"]
        self.rejects("stable gate")

    def test_11_unsubstantiated_product_pass(self):
        self.registry["entries"][0]["execution_status"] = "pass"
        self.rejects("unsupported product pass")

    def test_12_missing_phase_document(self):
        self.plan["stages"][0]["document"] = "phases/DOES_NOT_EXIST.md"
        self.rejects("missing stage document")

    def test_13_unexecuted_plan_cannot_enable_defaults(self):
        self.plan["new_default_capabilities_enabled"] = ["rendercore"]
        self.rejects("cannot enable product defaults")

    def test_14_missing_old_phase(self):
        del self.mapping["CP08-D"]
        self.rejects("legacy phase map")

    def test_15_joint_dependency_phase_is_independent_of_private_base(self):
        self.plan["stages"][4]["dependencies"].append("U05.private_python")
        self.rejects("must not wait")

    def test_16_archive_does_not_override_active_spec(self):
        self.sources[0]["authority"] = "also_mandatory"
        self.rejects("parallel authority")

    def test_17_markdown_links_and_fences(self):
        self.assertEqual(v.document_errors(ROOT), [])


if __name__ == "__main__":
    unittest.main()
