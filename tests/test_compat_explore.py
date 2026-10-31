"""CG-02 explore: deterministic derivation, trust boundary, dedup, and real G1/G2 discovery."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402
from test_compat_guardian import native_result  # noqa: E402

SCRIPTS = Path(cg.HERE)
CORPUS_FILES = ("tests/compat/manifest.json", "tests/compat/baseline.json")


def fake_parent(case="fake_parent", source=None, executable=True) -> xt.Parent:
    text = source or (
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    ax.plot([1, 2, 3])\n    ax.set_title('t')\n"
        "    fig.savefig('out.png')\n"
    )
    return xt.Parent(
        "compat_engine",
        case,
        "script_shapes",
        {"s.py": text.encode()},
        "s.py",
        "main",
        executable,
        {"files": {"s.py": cg.digest(text.encode())}},
    )


class Determinism(unittest.TestCase):
    def test_same_seed_is_byte_identical_and_different_seed_differs(self):
        outputs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                ex.write_candidates(xt.generate(11), out)
                outputs.append(
                    {
                        p.relative_to(out).as_posix(): p.read_bytes()
                        for p in sorted(out.rglob("*"))
                        if p.is_file()
                    }
                )
        self.assertEqual(outputs[0], outputs[1])
        self.assertGreater(len(outputs[0]), 100)
        other = xt.generate(12)["candidates"]
        self.assertNotEqual(
            [c["candidate_id"] for c in xt.generate(11)["candidates"]],
            [c["candidate_id"] for c in other],
        )

    def test_every_candidate_compiles_and_records_its_derivation(self):
        generated = xt.generate(3)
        for manifest in generated["candidates"]:
            for rel, data in generated["_files"][manifest["candidate_id"]].items():
                if rel.endswith(".py"):
                    compile(data.decode(), rel, "exec", dont_inherit=True)
            self.assertEqual(manifest["seed"], 3)
            self.assertIn(manifest["class"], ("shape_preserving", "axis_changing"))
            self.assertRegex(manifest["ast_signature"], r"^[0-9a-f]{16}$")
            self.assertTrue(manifest["chain"] and manifest["parent"]["files"])
            self.assertEqual(manifest["trust"]["level"], "parent")

    def test_transforms_never_use_string_replacement_on_source(self):
        tree = ast.parse((SCRIPTS / "compat_explore_transforms.py").read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("replace", "sub")
        ]
        # The only text operations: identifier normalization for the AST signature and a
        # comparison of the guard expression. Neither rewrites generated source.
        texts = sorted(ast.unparse(c) for c in calls)
        self.assertEqual(len(texts), 3, texts)
        self.assertTrue(
            all(("node.id" in t or "node.asname" in t or "node.test" in t) for t in texts), texts
        )

    def test_one_axis_per_chain_in_canonical_order(self):
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        with self.assertRaises(cg.EvidenceError):
            xt.build(parent, ("style_use", "rcparams"), 1)  # same axis twice
        with self.assertRaises(cg.EvidenceError):
            xt.build(parent, ("import_alias", "rcparams"), 1)  # wrong order
        for manifest in xt.generate(5)["candidates"]:
            self.assertLessEqual(len(manifest["chain"]), 2)

    def test_changing_the_seed_changes_parameters_not_structure_signature(self):
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        a, _ = xt.build(parent, ("same_path_loop",), 1)
        found = {
            xt.build(parent, ("same_path_loop",), s)[0]["chain"][0]["params"]["passes"]
            for s in range(12)
        }
        self.assertGreater(len(found), 1)
        self.assertEqual(
            {xt.build(parent, ("same_path_loop",), s)[0]["ast_signature"] for s in range(12)},
            {a["ast_signature"]},
        )


class TrustBoundary(unittest.TestCase):
    def test_a_candidate_cannot_authorize_itself_identity_is_rederived(self):
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        manifest, _ = xt.build(parent, ("import_alias",), 4)
        spec = {"suite": "compat_engine", "case": "shape_oo_savefig"}
        ex.derive(spec, ["import_alias"], 4, manifest["candidate_id"])
        for bad in (
            dict(chain=["rcparams"], seed=4, cid=manifest["candidate_id"]),
            dict(chain=["import_alias"], seed=5, cid=manifest["candidate_id"]),
            dict(chain=["import_alias"], seed=4, cid="0" * 20),
        ):
            with self.assertRaises(cg.EvidenceError):
                ex.derive(spec, bad["chain"], bad["seed"], bad["cid"])

    def test_executor_has_no_entry_that_takes_files_or_paths(self):
        import inspect

        for fn in (ex.derive, ex.execute_request, ex.run_one):
            names = set(inspect.signature(fn).parameters)
            self.assertFalse(names & {"files", "path", "source", "script"}, fn.__name__)

    def test_nondeterministic_candidates_are_static_only_and_never_run(self):
        risky = (
            "import random\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
            "    fig, ax = plt.subplots()\n    ax.plot([random.random(), 2])\n    fig.savefig('o.png')\n"
        )
        parent = fake_parent("risky", risky)
        manifest, _ = xt.build(parent, ("style_use",), 1)
        self.assertEqual(manifest["execution_plan"]["status"], "static_only")
        self.assertTrue(any("random" in r for r in manifest["execution_plan"]["reasons"]))
        for body in (
            "import time\nt = time.time()\n",
            "import subprocess\n",
            "x = eval('1')\n",
            "import numpy as np\nnp.random.rand()\n",
        ):
            plan = xt.execution_plan({"s.py": body.encode()}, True)
            self.assertEqual(plan["status"], "static_only", body)
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=1,
                out=tmp,
                limit=None,
                parent=None,
                static_only=False,
                expected_sha=None,
                timeout=5,
            )
            with (
                mock.patch.object(xt, "default_parents", return_value=[parent]),
                mock.patch.object(
                    ex, "run_one", side_effect=AssertionError("must not execute")
                ) as runner,
            ):
                ex.explore(args)
            runner.assert_not_called()

    def test_v2_parents_are_static_only(self):
        generated = xt.generate(2)
        v2 = [c for c in generated["candidates"] if c["parent"]["suite"] == "compat_v2"]
        self.assertTrue(v2)
        self.assertTrue(all(c["execution_plan"]["status"] == "static_only" for c in v2))

    def test_no_dependency_authorization_or_install_surface_in_explore_code(self):
        forbidden = (
            "/api/",
            "deprepair",
            "depplan",
            "depresolve",
            "pip",
            "install",
            "authoriz",
            "nativeperm",
        )
        for name in ("compat_explore.py", "compat_explore_transforms.py"):
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            doc_nodes = {
                id(n.body[0].value)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                and n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)
            }
            used = []
            for n in ast.walk(tree):
                if (
                    isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and id(n) not in doc_nodes
                ):
                    used.append(n.value)
                elif isinstance(n, ast.Name):
                    used.append(n.id)
                elif isinstance(n, ast.Attribute):
                    used.append(n.attr)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    used += [a.name for a in n.names] + [getattr(n, "module", None) or ""]
            for text in used:
                for token in forbidden:
                    # the 'pip' token must not match words like 'pipeline'; others are substrings
                    self.assertFalse(token in text.lower(), f"{name}: {token!r} in {text!r}")
            imported = {
                a.name.split(".")[0]
                for n in ast.walk(tree)
                if isinstance(n, ast.Import)
                for a in n.names
            }
            self.assertFalse(
                imported & {"subprocess", "socket", "urllib", "requests", "http"}, name
            )


class Dedup(unittest.TestCase):
    def test_structurally_identical_parents_collapse_by_axis_vector_and_signature(self):
        a, b = fake_parent("twin_a"), fake_parent("twin_b")
        generated = xt.generate(1, [a, b])
        self.assertGreater(len(generated["dropped_duplicates"]), 0)
        self.assertEqual(
            generated["coverage"]["dedup"]["generated"],
            len(generated["candidates"]) + len(generated["dropped_duplicates"]),
        )
        kept = {(tuple(c["axis_vector"]), c["ast_signature"]) for c in generated["candidates"]}
        self.assertEqual(len(kept), len(generated["candidates"]))
        singles = [c for c in generated["candidates"] if len(c["chain"]) == 1]
        self.assertTrue(all(c["parent"]["case"] == "twin_a" for c in singles))

    def spec_parent(self, case, **spec) -> xt.Parent:
        parent = fake_parent(case)
        return xt.Parent(
            parent.suite, case, parent.category, parent.files, parent.script, parent.entry,
            parent.executable, parent.provenance, {"id": case, "notes": case, **spec},
        )  # fmt: skip

    def test_parents_sharing_a_script_but_judged_differently_each_keep_their_candidates(self):
        """一个脚本服务多个 must 用例（ca_basic_series.py）：stem / 期望 / 变更不同就是不同的测试（Codex #1368 P2）。"""
        a = self.spec_parent(
            "case_a", stem="fig_a", mutations=[{"gid": "g", "prop": "p", "value": 1}]
        )
        b = self.spec_parent(
            "case_b", stem="fig_b", mutations=[{"gid": "g", "prop": "p", "value": 2}]
        )
        generated = xt.generate(1, [a, b])
        self.assertEqual(generated["dropped_duplicates"], [])
        by_parent = {"case_a": 0, "case_b": 0}
        for c in generated["candidates"]:
            by_parent[c["parent"]["case"]] += 1
        self.assertGreater(by_parent["case_a"], 0)
        singles = {
            case: {
                c["chain"][0]["name"]
                for c in generated["candidates"]
                if len(c["chain"]) == 1 and c["parent"]["case"] == case
            }
            for case in by_parent
        }
        self.assertEqual(singles["case_a"], singles["case_b"])
        self.assertGreater(len(singles["case_b"]), 0)

    def test_parents_judged_the_same_still_collapse_whatever_their_ids(self):
        a = self.spec_parent("case_a", stem="fig", expected={}, mutations=[])
        b = self.spec_parent("case_b", stem="fig", expected={}, mutations=[])
        generated = xt.generate(1, [a, b])
        self.assertGreater(len(generated["dropped_duplicates"]), 0)
        singles = [c for c in generated["candidates"] if len(c["chain"]) == 1]
        self.assertTrue(all(c["parent"]["case"] == "case_a" for c in singles))

    def test_coverage_exposes_bias_by_family_axis_and_parent_category(self):
        cov = xt.generate(1)["coverage"]
        self.assertGreaterEqual(len(cov["by_family"]), 7)
        for fam in cov["by_family"].values():
            self.assertIn("parent_count", fam)
            self.assertIn("axis", fam)
        self.assertIn("script_shapes", cov["parents"]["used_by_manifest_category"])
        self.assertGreater(cov["parents"]["corpus_by_manifest_category"]["core_artists"], 0)
        # CG-08：父本按规则选（tier=must 且无声明为假的阶段），四类都有；metamorphic 在语料里没有 must 用例
        used = cov["parents"]["used_by_manifest_category"]
        for category in ("script_shapes", "core_artists", "axes_layout", "scientific_stack"):
            self.assertGreater(used[category], 0, category)
        self.assertNotIn("metamorphic", used)
        self.assertEqual(cov["parents"]["categories_without_parent"], ["metamorphic"])
        self.assertEqual(cov["pairwise"]["covered"], cov["pairwise"]["feasible"])
        self.assertIn("bias_note", cov)
        self.assertEqual(set(cov["by_class"]), {"shape_preserving", "axis_changing"})

    def test_limit_selection_is_family_balanced_and_deterministic(self):
        candidates = xt.generate(1)["candidates"]
        chosen = ex.select(candidates, 9)
        self.assertEqual(chosen, ex.select(candidates, 9))
        self.assertGreaterEqual(len({c["axis_vector"][0] for c in chosen}), 7)


class Judgment(unittest.TestCase):
    NATIVE = native_result(
        figure_count=3,
        semantics={f"figure{i}": {"titles": [f"t{i}"]} for i in range(3)},
        savefig_last_writer={"o.png": {"figure": 2, "titles": ["t2"]}},
    )

    def stems(self, **titles):
        return {"stems": {k: {"titles": v} for k, v in titles.items()}, "error": None}

    def test_all_figures_present_is_no_difference_case_15(self):
        tav = self.stems(o=["t2"], a=["t0"], b=["t1"])
        self.assertEqual(
            ex.compare("axis_changing", self.NATIVE, tav, None)["outcome"], "no_difference"
        )

    def test_zero_figures_fewer_figures_and_wrong_figure_are_distinct_findings(self):
        zero = ex.compare(
            "axis_changing", self.NATIVE, {"stems": {}, "error": {"code": "script_no_figure"}}, None
        )
        self.assertEqual(zero["differences"][0]["kind"], "zero_figures")
        self.assertIn("native 3 figure(s) / Tavotto 0 figures", zero["differences"][0]["summary"])
        fewer = ex.compare("axis_changing", self.NATIVE, self.stems(o=["t2"]), None)
        self.assertEqual(fewer["differences"][0]["kind"], "fewer_figures")
        self.assertEqual(fewer["differences"][0]["category"], "partial_capture")
        wrong = ex.compare(
            "axis_changing", self.NATIVE, self.stems(o=["t0"], a=["t1"], b=["t2"]), None
        )
        kinds = [d["kind"] for d in wrong["differences"]]
        self.assertEqual(kinds, ["same_path_content_mismatch"])
        self.assertIn("Tavotto kept ['t0']", wrong["differences"][0]["summary"])

    def test_same_stem_other_format_is_owned_by_the_figure_that_saved_that_format(self):
        """原生 `o.pdf` 第一张、`o.png` 第二张（同 stem 不同格式）：Tavotto 是 `o`（pdf）与 `o-2`（png），
        `o.png` 的归属是 `o-2`，不是 `o`——按 stem 键去比会把它误判成 same_path_content_mismatch。"""
        native = native_result(
            figure_count=2,
            semantics={"f0": {"titles": ["t0"]}, "f1": {"titles": ["t1"]}},
            savefig_last_writer={
                "o.pdf": {"figure": 0, "titles": ["t0"]},
                "o.png": {"figure": 1, "titles": ["t1"]},
            },
        )
        tav = {
            "stems": {"o": {"titles": ["t0"]}, "o-2": {"titles": ["t1"]}},
            "error": None,
            "savefig_calls": {"o": ["pdf"], "o-2": ["png"]},
        }
        self.assertEqual(ex.compare("axis_changing", native, tav, None)["outcome"], "no_difference")
        # 归属错了仍然要报：png 那张其实是第一张的内容
        wrong = dict(tav, stems={"o": {"titles": ["t0"]}, "o-2": {"titles": ["t0"]}})
        found = ex.compare("axis_changing", native, wrong, None)
        self.assertIn("same_path_content_mismatch", [d["kind"] for d in found["differences"]])
        # 没记到格式（描述符里没有 savefig_calls）：退回按 stem 比，原先的读法不变
        blind = {k: v for k, v in tav.items() if k != "savefig_calls"}
        kinds = [d["kind"] for d in ex.compare("axis_changing", native, blind, None)["differences"]]
        self.assertIn("same_path_content_mismatch", kinds)

    def test_an_explicit_numbered_file_does_not_vouch_for_the_base_file(self):
        """脚本自己写了 `s.png` 和 `s-2.png`：`s` 存过 png，`s.png` 就归 `s`；`s-2` 是独立文件，
        它的标题不得替 `s.png` 的错误捕获作保（Codex #1382 r4366222254）。"""
        native = native_result(
            figure_count=2,
            semantics={"f0": {"titles": ["A"]}, "f1": {"titles": ["B"]}},
            savefig_last_writer={
                "s.png": {"figure": 1, "titles": ["B"]},
                "s-2.png": {"figure": 1, "titles": ["B"]},
            },
        )
        tav = {
            "stems": {"s": {"titles": ["A"]}, "s-2": {"titles": ["B"]}},
            "error": None,
            "savefig_calls": {"s": ["png"], "s-2": ["png"]},
        }
        kinds = [d["kind"] for d in ex.compare("axis_changing", native, tav, None)["differences"]]
        self.assertIn("same_path_content_mismatch", kinds)

    def test_tavotto_error_after_partial_observation_is_never_clean(self):
        """部分图观察成功后 worker 报错：已观察部分与原生一致也不得判 no_difference（Codex #1135 r4310366048）。"""
        tav = {
            "stems": {"o": {"titles": ["t2"]}, "a": {"titles": ["t0"]}, "b": {"titles": ["t1"]}},
            "error": {"code": "override_failed"},
        }
        verdict = ex.compare("axis_changing", self.NATIVE, tav, None)
        self.assertEqual(verdict["outcome"], "unknown", verdict)
        self.assertIn("override_failed", verdict["note"])

    def test_extra_tavotto_figures_are_a_difference_even_with_matching_titles(self):
        """原生 3 张、Tavotto 4 张（多出的那张标题与原生重复）：不能判 clean（Codex #1135 r4305973918）。"""
        extra = ex.compare(
            "axis_changing", self.NATIVE, self.stems(o=["t2"], a=["t0"], b=["t1"], c=["t1"]), None
        )
        self.assertEqual(extra["outcome"], "difference_found")
        kinds = [d["kind"] for d in extra["differences"]]
        self.assertEqual(kinds, ["extra_figures"])
        self.assertEqual(extra["differences"][0]["category"], "figure_capture")

    def test_preserving_transform_that_changes_native_output_is_an_invalid_candidate(self):
        parent_native = native_result(figure_count=1, semantics={"figure0": {"titles": ["t0"]}})
        verdict = ex.compare("shape_preserving", self.NATIVE, self.stems(o=["t2"]), parent_native)
        self.assertEqual(verdict["outcome"], "invalid_candidate")
        self.assertEqual(verdict["differences"], [])

    def test_native_failure_is_never_a_tavotto_difference(self):
        failed = native_result(exit_status=1, error={"type": "X", "traceback": "t"})
        self.assertEqual(
            ex.compare("axis_changing", failed, {"stems": {}, "error": {"code": "x"}}, None)[
                "outcome"
            ],
            "invalid_fixture",
        )
        missing = native_result(
            exit_status=1,
            error={"type": "ModuleNotFoundError", "traceback": "ModuleNotFoundError: x"},
        )
        self.assertEqual(
            ex.compare("axis_changing", missing, {"stems": {}}, None)["outcome"],
            "environment_unavailable",
        )


class Cli(unittest.TestCase):
    def explore_args(self, tmp, **over):
        base = dict(
            seed=9,
            out=tmp,
            limit=3,
            parent="shape_oo_savefig",
            static_only=True,
            expected_sha=None,
            timeout=5,
        )
        return Namespace(**{**base, **over})

    def test_every_parent_id_is_validated_typos_empties_and_duplicates_are_refused(self):
        for bad in (
            "shape_oo_savefig,misspelled_case",
            "shape_oo_savefig,",
            ",shape_oo_savefig",
            "shape_oo_savefig,shape_oo_savefig",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(cg.EvidenceError, msg=bad):
                    ex.explore(self.explore_args(tmp, parent=bad))
                self.assertEqual(
                    list(Path(tmp).iterdir()), [], "nothing is written for a bad scope"
                )

    def test_style_axis_candidates_are_executable_now_that_pixels_are_compared(self):
        """CG-06：样式轴候选曾恒为 static_only（compare 观察不到样式）；像素判定落地后可执行。"""
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        for name in ("rcparams", "style_use"):
            manifest, _ = xt.build(parent, (name,), 3)
            self.assertEqual(manifest["execution_plan"]["status"], "executable", name)
            self.assertFalse(hasattr(xt, "STYLE_UNOBSERVED"))
            ex.derive(
                {"suite": "compat_engine", "case": "shape_oo_savefig"},
                [name],
                3,
                manifest["candidate_id"],
            )

    def test_non_positive_limit_is_rejected_by_the_parser_and_by_select(self):
        for bad in ("-1", "0"):
            with self.assertRaises(SystemExit) as caught:
                cg.main(["explore", "--seed", "1", "--out", "/nowhere", "--limit", bad])
            self.assertEqual(caught.exception.code, 2)
        with self.assertRaises(cg.EvidenceError):
            ex.select([{"axis_vector": ["a"]}], 0)
        with self.assertRaises(cg.EvidenceError):
            ex.select([{"axis_vector": ["a"]}], -3)

    def test_an_empty_selection_never_publishes_a_report_or_exits_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=9,
                out=tmp,
                limit=None,
                parent="shape_oo_savefig",
                static_only=True,
                expected_sha=None,
                timeout=5,
            )
            with mock.patch.object(ex, "select", return_value=[]):
                with self.assertRaises(cg.EvidenceError):
                    ex.explore(args)
            for run in Path(tmp).iterdir():
                self.assertFalse((run / "reports").exists())

    def test_every_file_under_scripts_ci_counts_as_execution_code(self):
        with tempfile.NamedTemporaryFile(
            dir=cg.ROOT / ex.EXECUTION_CODE, prefix="untracked_helper_", suffix=".py"
        ) as f:
            self.assertIn(f"{ex.EXECUTION_CODE}/{Path(f.name).name}", ex.dirty_execution_code())

    def test_modified_execution_critical_code_is_refused_before_any_execution(self):
        """脏工作区里改过的变换 / 执行器不得被当成评审过的模板执行（Codex #1135 r4311941518）。"""
        dirty = "scripts/ci/compat_driver.py"
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=9,
                out=tmp,
                limit=1,
                parent="shape_oo_savefig",
                static_only=False,
                expected_sha=None,
                timeout=5,
            )
            with (
                mock.patch.object(ex, "dirty_execution_code", return_value=[dirty]),
                mock.patch.object(ex, "run_one", side_effect=AssertionError("executed")),
            ):
                with self.assertRaisesRegex(cg.EvidenceError, "compat_driver"):
                    ex.explore(args)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_static_only_explore_writes_candidates_executes_nothing_and_touches_no_corpus(self):
        before = {p: hashlib.sha256((cg.ROOT / p).read_bytes()).hexdigest() for p in CORPUS_FILES}
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=9,
                out=tmp,
                limit=12,
                parent="shape_oo_savefig",
                static_only=True,
                expected_sha=None,
                timeout=5,
            )
            with mock.patch.object(ex, "run_one", side_effect=AssertionError("must not execute")):
                # 什么都没比对过不是「无差异」：不得以 clean 退出（AC-11，Codex r4306059452）
                self.assertEqual(ex.explore(args), cg.EXIT_UNKNOWN)
            (run,) = Path(tmp).iterdir()
            report = json.loads((run / "reports/explore-report.json").read_text(encoding="utf-8"))
            self.assertEqual(list(report["outcome_counts"]), ["static_only"])
            self.assertTrue((run / "candidates.json").is_file())
            self.assertTrue(all(c["executions"]["tavotto"] == 0 for c in report["candidates"]))
        self.assertEqual(
            before,
            {p: hashlib.sha256((cg.ROOT / p).read_bytes()).hexdigest() for p in CORPUS_FILES},
        )

    def test_out_inside_the_worktree_is_refused_and_expected_sha_is_pinned(self):
        args = Namespace(
            seed=1,
            out=str(cg.ROOT / "tmp-explore"),
            limit=1,
            parent="shape_oo_savefig",
            static_only=True,
            expected_sha=None,
            timeout=5,
        )
        with self.assertRaises(cg.EvidenceError):
            ex.explore(args)
        self.assertFalse((cg.ROOT / "tmp-explore").exists())
        args.out, args.expected_sha = tempfile.mkdtemp(), "0" * 40
        with self.assertRaises(cg.EvidenceError):
            ex.explore(args)


class AuditedJudgment(unittest.TestCase):
    """观测器没写出执行日志（计数为 None / 0）时，比对一致也只能是 unknown（Codex #1135 r4311521396）。"""

    CLEAN = {"outcome": "no_difference", "differences": []}

    def test_agreement_without_an_audited_tavotto_execution_is_never_clean(self):
        got = ex.audited_judgment("shape_changing", self.CLEAN, {"native": 1, "tavotto": None})
        self.assertEqual(got["outcome"], "unknown")
        self.assertIn("tavotto", got["note"])

    def test_shape_preserving_also_needs_the_parent_native_execution(self):
        counts = {"native": 1, "native_pixels": 1, "tavotto": 1, "parent_native": 0}
        got = ex.audited_judgment("shape_preserving", self.CLEAN, counts)
        self.assertEqual(got["outcome"], "unknown")
        self.assertIn("parent_native", got["note"])

    def test_the_pixel_native_run_needs_its_own_audited_execution(self):
        counts = {"native": 1, "native_pixels": 0, "tavotto": 1, "parent_native": 1}
        got = ex.audited_judgment("shape_preserving", self.CLEAN, counts)
        self.assertEqual(got["outcome"], "unknown")
        self.assertIn("native_pixels", got["note"])

    def test_audited_agreement_and_differences_pass_through(self):
        counts = {"native": 1, "native_pixels": 1, "tavotto": 1, "parent_native": 1}
        self.assertIs(ex.audited_judgment("shape_preserving", self.CLEAN, counts), self.CLEAN)
        diff = {"outcome": "difference_found", "differences": [{}]}
        self.assertIs(ex.audited_judgment("shape_changing", diff, {}), diff)


class RealDiscovery(unittest.TestCase):
    """Real execution against the current product code (needs the scientific stack)."""

    @classmethod
    def setUpClass(cls):
        cls.parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)

    def run_chain(self, *chain):
        manifest, _ = xt.build(self.parent, chain, 7)
        out = Path(self.tmp.name) / "_".join(chain)
        out.mkdir()
        return manifest, ex.run_one(manifest, 7, out, 200)

    def test_g1_show_then_close_no_longer_differs_after_the_fix(self):
        """G1 已由 #1138 修复：同一条 show_close 派生候选现在原生 / Tavotto 一致（曾经是 zero_figures）。"""
        manifest, record = self.run_chain("show_close")
        self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
        self.assertGreaterEqual(record["executions"]["native"], 1)
        row = {"candidate_id": manifest["candidate_id"], "chain": ["show_close"], "differences": []}
        self.assertFalse(ex.known_gap_findings([row])["G1"]["found"])

    def test_g2_same_path_in_a_loop_no_longer_differs_after_the_fix(self):
        """G2 已由 #1141 修复：同一路径循环写多张图的派生候选现在原生 / Tavotto 一致（曾经是 same_path_content_mismatch）。"""
        manifest, record = self.run_chain("same_path_loop")
        self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
        self.assertGreaterEqual(record["executions"]["native"], 1)
        row = {
            "candidate_id": manifest["candidate_id"],
            "chain": ["same_path_loop"],
            "differences": [],
        }
        self.assertFalse(ex.known_gap_findings([row])["G2"]["found"])

    def test_shape_preserving_transforms_on_a_passing_parent_make_no_fake_difference(self):
        for chain in (("import_alias",), ("argparse_out",), ("inline_to_file",)):
            manifest, record = self.run_chain(*chain)
            self.assertEqual(manifest["class"], "shape_preserving")
            self.assertEqual(record["judgment"]["outcome"], "no_difference", (chain, record))
            self.assertEqual(record["judgment"]["differences"], [])


if __name__ == "__main__":
    unittest.main()
