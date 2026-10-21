"""CG-06 explore oracles: pixels, structure, inherited edit / replay / export."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_explore_oracles as OR  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_matrix as cm  # noqa: E402
import compat_triage as TG  # noqa: E402
import pixelcompare  # noqa: E402
from test_compat_explore import fake_parent  # noqa: E402
from test_compat_guardian import native_result  # noqa: E402


def png(path: Path, *, dots: int = 0, level: int = 255, size: int = 64) -> Path:
    from PIL import Image

    im = Image.new("L", (size, size), 255)
    for i in range(dots):
        im.putpixel((i, 0), 255 - level)
    im.save(path)
    return path


class PixelJudgment(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "ev"

    def test_identical_renders_pass_and_leave_no_diff_artifacts(self):
        a, b = png(self.tmp / "a.png"), png(self.tmp / "b.png")
        got = OR.pixel_judgment({"s": a}, {"s": b}, self.out)
        self.assertEqual((got["status"], got["differences"], got["compared"]), ("passed", [], 1))
        self.assertFalse(list(self.out.rglob("*.png")))

    def test_a_visible_change_is_a_pixel_mismatch_with_hashed_evidence(self):
        a, b = png(self.tmp / "a.png"), png(self.tmp / "b.png", dots=64)
        got = OR.pixel_judgment({"s": a}, {"s": b}, self.out)
        self.assertEqual(got["status"], "failed")
        (diff,) = got["differences"]
        self.assertEqual((diff["kind"], diff["category"]), ("pixel_mismatch", "export_or_fidelity"))
        ev = diff["evidence"]
        self.assertEqual(ev["tolerance"], cm.FIDELITY_TOLERANCE)
        self.assertGreater(ev["metrics"]["max_abs_diff"], cm.FIDELITY_TOLERANCE["max_abs_diff"])
        for key in ("control", "tavotto", "diff"):
            path = self.out / ev[key]["path"]
            self.assertTrue(path.is_file(), key)
            self.assertEqual(ev[key]["sha256"], cg.digest(path.read_bytes()), key)
        self.assertEqual((self.out / ev["control"]["path"]).read_bytes(), a.read_bytes())
        self.assertEqual((self.out / ev["tavotto"]["path"]).read_bytes(), b.read_bytes())

    def test_the_tolerance_is_compatbenchs_not_exact_equality(self):
        """3 个像素差 100：低于 CompatBench 容差（max 140、占比 0.004）→ 通过；严格相等会判红。"""
        a, b = png(self.tmp / "a.png"), png(self.tmp / "b.png", dots=3, level=100)
        self.assertGreater(pixelcompare.compare(a, b)["changed_pixels"], 0)
        self.assertEqual(OR.pixel_judgment({"s": a}, {"s": b}, self.out)["status"], "passed")

    def test_size_mismatch_is_a_mismatch(self):
        a, b = png(self.tmp / "a.png"), png(self.tmp / "b.png", size=32)
        got = OR.pixel_judgment({"s": a}, {"s": b}, self.out)
        self.assertEqual(got["differences"][0]["kind"], "pixel_mismatch")

    def test_missing_imaging_dependencies_is_no_conclusion_not_a_difference(self):
        a = png(self.tmp / "a.png")
        with mock.patch.object(
            pixelcompare, "_imaging", side_effect=pixelcompare.MissingImagingDeps("no numpy")
        ):
            got = OR.pixel_judgment({"s": a}, {"s": a}, self.out)
        self.assertEqual((got["status"], got["differences"]), ("not_run", []))
        self.assertIn("no numpy", got["note"])

    def test_no_native_pixels_is_not_covered_not_a_failure_to_run(self):
        a = png(self.tmp / "a.png")
        got = OR.pixel_judgment({}, {"s": a}, self.out)
        self.assertEqual((got["status"], got["differences"]), ("not_covered", []))
        self.assertIn("no native pixel reference", got["note"])

    def test_one_sided_stems_without_any_other_explanation_are_not_run(self):
        a = png(self.tmp / "a.png")
        got = OR.pixel_judgment({"s": a, "t": a}, {"s": a}, self.out)
        self.assertEqual(got["status"], "not_run")
        self.assertIn("['t']", got["note"])

    def test_one_sided_stems_next_to_a_mismatch_are_still_reported(self):
        """Codex #1255 r4334476089：另一张图已 mismatch 时，单边 stem 不能从报告里消失。"""
        a, b = png(self.tmp / "a.png"), png(self.tmp / "b.png", dots=64)
        got = OR.pixel_judgment({"s": a, "t": a}, {"s": b}, self.out)
        self.assertEqual(got["status"], "failed")
        self.assertEqual([d["kind"] for d in got["differences"]], ["pixel_mismatch"])
        self.assertEqual(got["unmatched"], ["t"])
        stages = {
            "fidelity": {**OR.stage("fidelity", got["status"]), "unmatched": got["unmatched"]}
        }
        self.assertEqual(OR.unresolved(stages), ["fidelity: stems not compared: ['t']"])

    def test_only_common_stems_are_compared(self):
        a = png(self.tmp / "a.png")
        self.assertEqual(OR.pixel_judgment({"x": a}, {"y": a}, self.out)["status"], "not_run")


class StructureJudgment(unittest.TestCase):
    PARENT = {"s": {"axes_0.title.text": "T", "axes_0.lines_0.linewidth": 1.5}}

    def test_equal_snapshots_pass_and_extra_candidate_keys_are_allowed(self):
        cand = {"s": {**self.PARENT["s"], "axes_0.extra.color": "#fff"}}
        self.assertEqual(OR.structure_judgment(self.PARENT, cand)["status"], "passed")

    def test_numeric_comparison_uses_compatbenchs_same_value(self):
        cand = {"s": {"axes_0.title.text": "t", "axes_0.lines_0.linewidth": 1.51}}
        self.assertEqual(OR.structure_judgment(self.PARENT, cand)["status"], "passed")

    def test_missing_key_and_changed_value_are_structure_mismatches(self):
        missing = {"s": {"axes_0.title.text": "T"}}
        changed = {"s": {"axes_0.title.text": "T", "axes_0.lines_0.linewidth": 3.0}}
        for cand, key, got in (
            (missing, "axes_0.lines_0.linewidth", "<missing>"),
            (changed, "axes_0.lines_0.linewidth", 3.0),
        ):
            res = OR.structure_judgment(self.PARENT, cand)
            self.assertEqual(res["status"], "failed")
            (diff,) = res["differences"]
            self.assertEqual(
                (diff["kind"], diff["category"]), ("structure_mismatch", "manifest_semantics")
            )
            self.assertEqual(diff["evidence"]["mismatches"][0]["key"], key)
            self.assertEqual(diff["evidence"]["mismatches"][0]["candidate"], got)

    def test_a_stem_the_candidate_lacks_is_a_figure_count_matter_not_structure(self):
        self.assertEqual(OR.structure_judgment(self.PARENT, {})["status"], "passed")


class InheritedCase(unittest.TestCase):
    def parent(self, **spec):
        p = fake_parent()
        p.case_spec = spec
        return p

    def test_shape_preserving_inherits_mutations_and_formats(self):
        mut = [{"gid": "axes_0.title", "prop": "color", "value": "#123456"}]
        got = OR.inherited_case(
            self.parent(stem="s", mutations=mut, export_formats=["png"]),
            {"class": "shape_preserving"},
        )
        self.assertEqual(got, {"stem": "s", "mutations": mut, "export_formats": ["png"]})

    def test_export_formats_default_to_pdf_and_png(self):
        got = OR.inherited_case(self.parent(stem="s"), {"class": "shape_preserving"})
        self.assertEqual(got["export_formats"], ["pdf", "png"])

    def test_axis_changing_candidates_inherit_nothing(self):
        self.assertIsNone(
            OR.inherited_case(self.parent(stem="s", mutations=[1]), {"class": "axis_changing"})
        )

    def test_real_parent_carries_its_registered_case(self):
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        manifest, _ = xt.build(parent, ("import_alias",), 1)
        got = OR.inherited_case(parent, manifest)
        self.assertEqual(got["stem"], "shape_oo")
        self.assertTrue(got["mutations"])


class Layers(unittest.TestCase):
    """`oracle_layers` / `compare` glue with fakes: every new kind lands in a stage and in triage."""

    def test_axis_changing_candidates_report_edit_replay_export_as_not_covered_with_the_criterion(
        self,
    ):
        tav = {"stems": {"s": {"titles": []}}, "shots": {}, "snapshots": {}, "shot_errors": {}}
        got = ex.oracle_layers("axis_changing", tav, {}, None, Path("/nonexistent"), None)
        for name in ("edit", "replay", "export", "structure"):
            self.assertEqual(got["stages"][name]["status"], "not_covered", name)
            self.assertIn("no reviewed edit target", got["stages"][name]["note"])
            self.assertTrue(got["stages"][name]["criterion"])

    def test_no_tavotto_figures_means_every_layer_is_not_run(self):
        got = ex.oracle_layers(
            "shape_preserving", {"stems": {}}, {"s": Path("/n.png")}, None, Path("/x"), None
        )
        self.assertEqual({s["status"] for s in got["stages"].values()}, {"not_run"})
        none = ex.oracle_layers("shape_preserving", {"stems": {}}, {}, None, Path("/x"), None)
        self.assertEqual({s["status"] for s in none["stages"].values()}, {"not_covered"})

    def test_missing_parent_snapshot_is_not_a_structure_difference(self):
        tav = {"stems": {"s": {}}, "shots": {}, "snapshots": {"s": {}}, "shot_errors": {}}
        got = ex.oracle_layers("shape_preserving", tav, {}, None, Path("/x"), None)
        self.assertEqual(got["stages"]["structure"]["status"], "not_run")
        self.assertEqual(got["differences"], [])

    def test_render_failure_is_a_difference_not_a_skip(self):
        tav = {
            "stems": {"s": {}},
            "shots": {},
            "snapshots": {"s": {}},
            "shot_errors": {"s": "boom"},
        }
        got = ex.oracle_layers("axis_changing", tav, {}, None, Path("/x"), None)
        self.assertEqual([d["kind"] for d in got["differences"]], ["render_failed"])
        self.assertEqual(got["stages"]["fidelity"]["status"], "failed")

    def test_oracle_differences_reach_compare_and_never_hide_behind_clean(self):
        nat = native_result(figure_count=1, semantics={"s": {"titles": ["t"]}})
        tav = {"stems": {"s": {"titles": ["t"]}}, "error": None}
        diff = {
            "kind": "pixel_mismatch",
            "summary": "x",
            "category": "export_or_fidelity",
            "evidence": {},
        }
        self.assertEqual(ex.compare("axis_changing", nat, tav, None)["outcome"], "no_difference")
        got = ex.compare("axis_changing", nat, tav, None, [diff])
        self.assertEqual((got["outcome"], got["differences"]), ("difference_found", [diff]))

    def test_every_new_kind_is_attributed_by_triage_and_not_left_unknown(self):
        rules = TG.load_rules()
        expected = {
            "pixel_mismatch": "export_or_fidelity",
            "render_failed": "export_or_fidelity",
            "structure_mismatch": "manifest_semantics",
            "edit_failed": "editing_capability",
            "replay_mismatch": "replay_inconsistency",
            "export_failed": "export_or_fidelity",
        }
        row = {"outcome": "difference_found", "chain": ["rcparams"], "evidence": None}
        for kind, family in expected.items():
            facts = TG.explore_facts(row, {"kind": kind, "evidence": {}})
            self.assertEqual(TG.attribute(facts, "explore", rules)["family"], family, kind)


class NativePixelNote(unittest.TestCase):
    """No native pixel reference is inapplicable only when the script never used savefig."""

    SAVED = {"savefig_last_writer": {"out.png": {"titles": ["t"]}}}

    def test_render_failure_is_a_reason(self):
        note = ex.native_pixel_note(native_result(**self.SAVED), {"ok": False, "code": "x"})
        self.assertIn("native render failed", note)

    def test_saved_but_nothing_rendered_did_not_run(self):
        note = ex.native_pixel_note(native_result(**self.SAVED), {"ok": True, "shots": {}})
        self.assertIn("out.png", note)

    def test_never_saved_is_structurally_absent(self):
        # show() then close(): native rendered nothing because nothing was saved → no reason
        # here, so pixel_judgment reports not_covered instead of not_run
        native = native_result(savefig_last_writer={}, savefig_calls=0)
        self.assertIsNone(ex.native_pixel_note(native, {"ok": True, "shots": {}}))

    def test_other_output_files_do_not_count_as_saved_figures(self):
        native = native_result(savefig_last_writer={})  # artifacts still lists out.png
        self.assertIsNone(ex.native_pixel_note(native, {"ok": True, "shots": {}}))

    def test_rendered_reference_needs_no_reason(self):
        native = native_result(**self.SAVED)
        self.assertIsNone(ex.native_pixel_note(native, {"ok": True, "shots": {"out": "a.png"}}))


class UnresolvedLayers(unittest.TestCase):
    """In-scope layers that did not run never read as clean; inapplicable ones never downgrade."""

    CLEAN = {"outcome": "no_difference", "differences": []}

    def layers(self, tmp, **over):
        a = png(Path(tmp) / "a.png")
        args = dict(
            candidate_class="shape_preserving",
            tavotto={
                "stems": {"s": {}},
                "shots": {"s": a},
                "snapshots": {"s": {"g.p": 1}},
                "shot_errors": {},
                "inherited": {
                    "stages": {n: OR.stage(n, "passed") for n in ("edit", "replay", "export")},
                    "differences": [],
                },
            },
            native_shots={"s": a},
            parent_snapshot={"s": {"g.p": 1}},
            evidence=Path(tmp) / "e",
            native_note=None,
        )
        args.update(over)
        return ex.oracle_layers(**args)

    def verdict(self, layers):
        return ex.apply_unresolved(self.CLEAN, layers["stages"])[0]

    def test_everything_ran_stays_clean(self):
        with tempfile.TemporaryDirectory() as t:
            got = self.layers(t)
            self.assertEqual(self.verdict(got)["outcome"], "no_difference")
            self.assertEqual(OR.unresolved(got["stages"]), [])

    def test_each_reason_a_layer_did_not_run_makes_the_candidate_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            cases = {
                "missing imaging deps": (
                    mock.patch.object(
                        pixelcompare,
                        "_imaging",
                        side_effect=pixelcompare.MissingImagingDeps("no numpy"),
                    ),
                    {},
                    "fidelity",
                ),
                "native pixel render failed": (
                    None,
                    {"native_note": "native render failed: x"},
                    "fidelity",
                ),
                "no common stem": (
                    None,
                    {"native_shots": {"other": Path(t) / "a.png"}},
                    "fidelity",
                ),
                "parent snapshot unavailable": (None, {"parent_snapshot": None}, "structure"),
            }
            for label, (patch, over, stage) in cases.items():
                if patch is None:
                    got = self.layers(t, **over)
                else:
                    with patch:
                        got = self.layers(t, **over)
                self.assertEqual(got["stages"][stage]["status"], "not_run", label)
                verdict = self.verdict(got)
                self.assertEqual(verdict["outcome"], "unknown", label)
                self.assertIn(stage, verdict["note"], label)

    def test_inherited_stage_that_could_not_run_is_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            base = self.layers(t)
            tav = {
                **{k: v for k, v in base.items() if False},
                "stems": {"s": {}},
                "shots": {"s": Path(t) / "a.png"},
                "snapshots": {"s": {"g.p": 1}},
                "shot_errors": {},
                "inherited": {
                    "stages": {},
                    "differences": [],
                    "note": "parent case stem not captured",
                },
            }
            got = self.layers(t, tavotto=tav)
            for name in ("edit", "replay", "export"):
                self.assertEqual(got["stages"][name]["status"], "not_run")
            self.assertEqual(self.verdict(got)["outcome"], "unknown")

    def test_a_found_difference_is_kept_and_the_missing_layer_is_still_reported(self):
        diff = {"outcome": "difference_found", "differences": [{"kind": "k"}]}
        stages = {"structure": OR.stage("structure", "not_run", "why")}
        got, missing = ex.apply_unresolved(diff, stages)
        self.assertIs(got, diff)
        self.assertEqual(missing, ["structure: why"])

    def test_not_covered_layers_never_downgrade(self):
        with tempfile.TemporaryDirectory() as t:
            # axis-changing: structure / edit / replay / export are not_covered
            got = self.layers(t, candidate_class="axis_changing")
            self.assertEqual(self.verdict(got)["outcome"], "no_difference")
            # no native pixel reference (show then close): fidelity is not_covered
            got = self.layers(t, native_shots={}, candidate_class="axis_changing")
            self.assertEqual(got["stages"]["fidelity"]["status"], "not_covered")
            self.assertEqual(self.verdict(got)["outcome"], "no_difference")
            # no figure on either side
            got = self.layers(t, tavotto={"stems": {}, "shots": {}}, native_shots={})
            self.assertEqual(self.verdict(got)["outcome"], "no_difference")


class InheritedStages(unittest.TestCase):
    """The stages are compat_matrix's: a failing stage_* becomes the matching difference kind."""

    CASE = {
        "stem": "s",
        "mutations": [{"gid": "g", "prop": "p", "value": 1}],
        "export_formats": ["png"],
    }

    def run_with(self, **patches):
        defaults = {
            "stage_edit": {"ok": True, "detail": {}, "full_patches": [{"gid": "g"}]},
            "stage_replay": {"ok": True, "detail": {}},
            "stage_export": {"ok": True, "detail": {}},
        }
        defaults.update(patches)
        worker = mock.Mock()
        with (
            mock.patch.object(cm, "stage_edit", return_value=defaults["stage_edit"]),
            mock.patch.object(cm, "stage_replay", return_value=defaults["stage_replay"]),
            mock.patch.object(cm, "stage_export", return_value=defaults["stage_export"]) as exp,
            mock.patch.object(OR, "_discard"),
        ):
            got = OR.inherited_stages(worker, mock.Mock(), "s", self.CASE, {}, Path("/w"))
        return got, exp

    def test_all_pass(self):
        got, exp = self.run_with()
        self.assertEqual(
            {k: v["status"] for k, v in got["stages"].items()},
            dict(edit="passed", replay="passed", export="passed"),
        )
        self.assertEqual(got["differences"], [])
        self.assertEqual(exp.call_args.args[3], ["png"])

    def test_each_failing_stage_is_its_own_difference_kind(self):
        for stage, kind, category in (
            ("stage_edit", "edit_failed", "editing_capability"),
            ("stage_replay", "replay_mismatch", "replay_inconsistency"),
            ("stage_export", "export_failed", "export_or_fidelity"),
        ):
            patch = {"ok": False, "detail": {"why": stage}, "full_patches": [{"gid": "g"}]}
            got, _ = self.run_with(**{stage: patch})
            (diff,) = got["differences"]
            self.assertEqual((diff["kind"], diff["category"]), (kind, category), stage)
            self.assertEqual(diff["evidence"]["detail"], {"why": stage})

    def test_no_declared_mutation_is_not_covered_not_passed(self):
        with mock.patch.object(cm, "stage_edit", side_effect=cm.Skip("no targets")):
            with mock.patch.object(cm, "stage_export", return_value={"ok": True, "detail": {}}):
                got = OR.inherited_stages(mock.Mock(), mock.Mock(), "s", self.CASE, {}, Path("/w"))
        self.assertEqual(got["stages"]["edit"]["status"], "not_covered")
        self.assertEqual(got["stages"]["replay"]["status"], "not_covered")


class ParentSnapshotCache(unittest.TestCase):
    def test_one_adapter_run_per_parent_per_explore_run(self):
        spec = {"suite": "compat_engine", "case": "shape_oo_savefig"}
        record = {"snapshots": {"shape_oo": {"a.b": 1}}, "error": None}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(ex, "_adapter_run", return_value=record) as run:
                a = ex.parent_snapshot(spec, out, 5)
                b = ex.parent_snapshot(spec, out, 5)
                self.assertEqual(run.call_count, 1)
                self.assertIs(a, b)
            other = Path(tmp) / "other"
            (other / "evidence").mkdir(parents=True)
            with mock.patch.object(ex, "_adapter_run", return_value=record) as run:
                ex.parent_snapshot(spec, other, 5)
                self.assertEqual(run.call_count, 1, "a different explore run recomputes")

    def test_a_failed_snapshot_is_data_and_never_a_raise(self):
        spec = {"suite": "compat_engine", "case": "shape_plt_savefig"}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ex, "_adapter_run", side_effect=cg.EvidenceError("boom")):
                got = ex.parent_snapshot(spec, Path(tmp), 5)
        self.assertIsNone(got["snapshots"])
        self.assertEqual(got["error"]["code"], "runner_error")


class DriverNativeLastWriter(unittest.TestCase):
    """compat_driver `native`：同一路径写两张不同的图，stem 的对照 PNG 是后写那张（产品 G2 同义）。"""

    def shots(self, body: str) -> dict:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "p"
            project.mkdir()
            (project / "s.py").write_text(
                "import matplotlib.pyplot as plt\n" + body, encoding="utf-8"
            )
            out = cm.run_driver(
                sys.executable,
                "native",
                {
                    "script": str(project / "s.py"),
                    "project": str(project),
                    "engine_dir": str(cm.ENGINE_DIR),
                    "entry": "__main__",
                    "out_dir": str(Path(tmp) / "shots"),
                    "width": 200,
                },
            )
            self.assertTrue(out["ok"], out)
            with Image.open(out["shots"]["o"]) as im:
                return {"stems": out["stems"], "size": im.size}

    def test_the_later_figure_written_to_one_path_is_the_stems_figure(self):
        two = (
            "a = plt.figure(figsize=(4, 2)); a.savefig('o.png')\n"
            "b = plt.figure(figsize=(4, 4)); b.savefig('o.png')\n"
        )
        got = self.shots(two)
        self.assertEqual(got["size"], (200, 200), "kept the first figure (aspect 2:1)")
        only_first = self.shots("a = plt.figure(figsize=(4, 2)); a.savefig('o.png')\n")
        self.assertEqual(only_first["size"], (200, 100))


class RealExecution(unittest.TestCase):
    """Real native + Tavotto execution (needs the scientific stack)."""

    @classmethod
    def setUpClass(cls):
        cls.parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)

    def run_chain(self, *chain):
        manifest, _ = xt.build(self.parent, chain, 7)
        out = Path(self.tmp.name) / "_".join(chain)
        out.mkdir()
        return manifest, ex.run_one(manifest, 7, out, 300)

    def test_import_alias_passes_every_layer_with_no_difference(self):
        manifest, record = self.run_chain("import_alias")
        self.assertEqual(manifest["class"], "shape_preserving")
        self.assertEqual(record["judgment"]["outcome"], "no_difference", record["judgment"])
        self.assertEqual(
            {k: v["status"] for k, v in record["stages"].items()},
            dict(
                fidelity="passed",
                structure="passed",
                edit="passed",
                replay="passed",
                export="passed",
            ),
        )
        self.assertEqual(sorted(record["stages"]), sorted(OR.STAGES))

    def test_style_candidates_are_now_executed_and_pixel_compared(self):
        for chain in (("rcparams",), ("style_use",)):
            manifest, record = self.run_chain(*chain)
            self.assertEqual(manifest["class"], "axis_changing")
            self.assertNotEqual(record["judgment"]["outcome"], "static_only", chain)
            self.assertIn(record["stages"]["fidelity"]["status"], ("passed", "failed"), chain)
            self.assertEqual(record["stages"]["edit"]["status"], "not_covered", chain)

    def test_same_path_loop_is_pixel_compared_against_the_last_writer(self):
        """同路径多图：原生对照取最后写入者（与产品 G2 同义），像素层真比，且一致。"""
        _, record = self.run_chain("same_path_loop")
        self.assertEqual(record["judgment"]["outcome"], "no_difference", record["judgment"])
        self.assertEqual(record["stages"]["fidelity"]["status"], "passed")
        self.assertEqual(record["executions"]["native"], 1, "pixel run is not a reference run")
        self.assertGreaterEqual(record["executions"]["native_pixels"], 1)

    def test_a_real_render_that_exceeds_the_tolerance_becomes_a_pixel_mismatch_with_evidence(self):
        """真原生 + 真 Tavotto 出图的两张 PNG 走完整条链：容差被压到不可能满足时，必须落成带证据的 pixel_mismatch。

        (本机 24 个父本×变换的真实运行里，样式候选与原生在 CompatBench 容差内一致，没有天然的 pixel_mismatch；
        所以这里用压低容差来证明这条链路不是摆设。)
        """
        manifest, _ = xt.build(self.parent, ("rcparams",), 7)
        out = Path(self.tmp.name) / "forced"
        work, evidence = out / "work", out / "evidence"
        work.mkdir(parents=True)
        evidence.mkdir()
        request = {
            "parent": {"suite": "compat_engine", "case": "shape_oo_savefig"},
            "chain": ["rcparams"],
            "seed": 7,
            "candidate_id": manifest["candidate_id"],
            "work": str(work),
            "evidence": str(evidence),
        }
        impossible = {k: -1 for k in cm.FIDELITY_TOLERANCE}
        env = cg.isolated_env(out / "env")
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(cm, "FIDELITY_TOLERANCE", impossible),
        ):
            record = ex.execute_request(request)
        self.assertEqual(record["judgment"]["outcome"], "difference_found", record["judgment"])
        diff = next(d for d in record["judgment"]["differences"] if d["kind"] == "pixel_mismatch")
        ev = diff["evidence"]
        self.assertEqual(ev["tolerance"], impossible)
        for key in ("control", "tavotto", "diff"):
            path = evidence / ev[key]["path"]
            self.assertEqual(ev[key]["sha256"], cg.digest(path.read_bytes()), key)
        self.assertEqual(record["stages"]["fidelity"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
