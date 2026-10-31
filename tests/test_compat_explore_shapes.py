"""CG-08 脚本写法 / 输出写法变换、父本按规则选取、两两覆盖、按家族轮转的 --limit。"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/ci"
sys.path.insert(0, str(SCRIPTS))

import compat_explore as ex  # noqa: E402
import compat_explore_shapes as shapes  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402

ENTRY_FORMS = ("class_wrapper", "return_fig", "sys_argv_out", "cell_markers")
OUTPUT_CALLS = ("multi_format", "pdfpages", "bytesio_close", "same_stem_two_figs", "bbox_tight_dpi")
NEW = ENTRY_FORMS + OUTPUT_CALLS

SOURCE = (
    '"""doc"""\nimport matplotlib.pyplot as plt\n\n\ndef main():\n'
    "    fig, ax = plt.subplots()\n    ax.plot([1, 2, 3])\n    ax.set_title('t')\n"
    "    fig.savefig('out.png')\n"
)
TOPLEVEL = (
    "import matplotlib.pyplot as plt\n\n"
    "fig, ax = plt.subplots()\nax.plot([1, 2, 3])\nax.set_title('t')\nfig.savefig('out.png')\n"
)
NO_SAVEFIG = (
    "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nax.plot([1, 2, 3])\nplt.show()\n"
)


def fake_parent(source=SOURCE, entry="main", case="fake_shapes") -> xt.Parent:
    return xt.Parent(
        "compat_engine",
        case,
        "script_shapes",
        {"s.py": source.encode()},
        "s.py",
        entry,
        True,
        {"files": {"s.py": cg.digest(source.encode())}},
    )


def ctx_of(parent: xt.Parent) -> xt.Ctx:
    return xt.Ctx(
        ast.parse(parent.files[parent.script].decode()),
        dict(parent.files),
        parent.script,
        parent.entry,
    )


def built(parent, chain, seed=3) -> str:
    manifest, files = xt.build(parent, chain, seed)
    return files[parent.script].decode()


def savefig_calls(source: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "savefig"
    ]


class Registration(unittest.TestCase):
    def test_families_axes_and_classes(self):
        for name in ENTRY_FORMS:
            t = xt.BY_NAME[name]
            self.assertEqual((t.family, t.preserving), ("entry_form", True), name)
        for name in OUTPUT_CALLS:
            t = xt.BY_NAME[name]
            self.assertEqual(
                (t.family, t.axis, t.preserving), ("output_call", "output_call", False)
            )
        self.assertEqual(len({t.order for t in xt.TRANSFORMS}), len(xt.TRANSFORMS))

    def test_output_calls_are_mutually_exclusive_with_each_other_and_with_show(self):
        parent = fake_parent()
        for a in OUTPUT_CALLS + ("show_only", "show_close"):
            for b in OUTPUT_CALLS:
                if a != b:
                    with self.assertRaises(cg.EvidenceError, msg=(a, b)):
                        xt.build(
                            parent, tuple(sorted((a, b), key=lambda n: xt.BY_NAME[n].order)), 1
                        )

    def test_entry_axis_is_shared_with_wrap_main_and_args_with_argparse(self):
        self.assertEqual(
            {xt.BY_NAME[n].axis for n in ("wrap_main", "class_wrapper", "return_fig")}, {"entry"}
        )
        self.assertEqual(xt.BY_NAME["sys_argv_out"].axis, xt.BY_NAME["argparse_out"].axis)
        with self.assertRaises(cg.EvidenceError):
            xt.build(fake_parent(TOPLEVEL, "__main__"), ("class_wrapper", "return_fig"), 1)

    def test_new_imports_do_not_touch_the_uncertain_table(self):
        before = dict(xt._UNCERTAIN_IMPORTS)
        for name in NEW:
            manifest, _ = xt.build(fake_parent(), (name,), 2)
            self.assertEqual(manifest["execution_plan"]["status"], "executable", name)
            self.assertEqual(manifest["static_checks"]["compile"], "passed")
        self.assertEqual(xt._UNCERTAIN_IMPORTS, before)

    def test_generator_digest_covers_the_shapes_module(self):
        own = xt.cg.digest(
            Path(xt.__file__).read_bytes() + Path(xt.data_source.__file__).read_bytes()
        )
        self.assertNotEqual(xt.generator_digest(), own)


class Shapes(unittest.TestCase):
    """每个变换写出的 AST 形状（不执行）。"""

    def test_class_wrapper(self):
        tree = ast.parse(built(fake_parent(), ("class_wrapper",)))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        cls = next(n for n in main.body if isinstance(n, ast.ClassDef))
        self.assertEqual((cls.name, cls.body[0].name), ("Plotter", "run"))
        self.assertEqual(ast.unparse(main.body[-1]), "Plotter().run()")
        self.assertTrue(savefig_calls(ast.unparse(cls)))

    def test_class_wrapper_at_module_level(self):
        tree = ast.parse(built(fake_parent(TOPLEVEL, "__main__"), ("class_wrapper",)))
        self.assertEqual([type(n).__name__ for n in tree.body[-2:]], ["ClassDef", "Expr"])

    def test_return_fig_draws_in_a_function_and_the_caller_saves(self):
        tree = ast.parse(built(fake_parent(), ("return_fig",)))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        draw = next(n for n in main.body if isinstance(n, ast.FunctionDef))
        self.assertEqual(draw.name, "_cg_draw")
        self.assertEqual(ast.unparse(draw.body[-1]), "return fig")
        self.assertFalse(savefig_calls(ast.unparse(draw)))
        self.assertEqual(ast.unparse(main.body[-2]), "fig = _cg_draw()")
        self.assertEqual(len(savefig_calls(ast.unparse(main))), 1)

    def test_return_fig_for_pyplot_savefig_returns_gcf(self):
        src = "import matplotlib.pyplot as plt\n\nplt.plot([1, 2, 3])\nplt.title('t')\nplt.savefig('o.png')\n"
        out = built(fake_parent(src, "__main__"), ("return_fig",))
        self.assertIn("return plt.gcf()", out)
        self.assertIn("_cg_fig.savefig('o.png')", out)

    def test_sys_argv_out_defaults_to_the_original_path(self):
        out = built(fake_parent(), ("sys_argv_out",))
        self.assertIn("fig.savefig(_cg_sys.argv[1] if len(_cg_sys.argv) > 1 else 'out.png')", out)
        self.assertIn("import sys as _cg_sys", out)

    def test_cell_markers_are_comments_only(self):
        parent = fake_parent()
        out = built(parent, ("cell_markers",))
        lines = out.splitlines()
        self.assertIn("# %%", lines)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(SOURCE)))
        for line in lines:
            if line.startswith("#"):
                self.assertTrue(line.startswith("# %%") or line.startswith("# ## Step "), line)
        self.assertNotIn(shapes.CODE_MARK, out)
        self.assertNotIn(shapes.MD_MARK, out)
        # 注释不是结构：签名与父本相同，所以与父本同一结构
        manifest, _ = xt.build(parent, ("cell_markers",), 3)
        self.assertEqual(manifest["ast_signature"], xt.ast_signature(SOURCE))

    def test_cell_markers_cover_both_kinds_and_stay_off_the_docstring(self):
        outs = [built(fake_parent(), ("cell_markers",), seed) for seed in range(30)]
        self.assertTrue(any("[markdown]" in out for out in outs))
        self.assertTrue(any("[markdown]" not in out and "# %%" in out for out in outs))
        self.assertEqual({out.splitlines()[0] for out in outs}, {'"""doc"""'})

    def test_finalize_only_rewrites_whole_sentinel_lines(self):
        src = f"x = 1\n{shapes.CODE_MARK}\ny = '{shapes.CODE_MARK}'\n  {shapes.CODE_MARK}\n{shapes.MD_MARK}2\n{shapes.MD_MARK}x\n{shapes.CODE_MARK}_x = 1\n"
        self.assertEqual(
            shapes.finalize_source(src),
            "x = 1\n# %%\ny = 'CG_CELL_CODE'\n  CG_CELL_CODE\n# %% [markdown]\n# ## Step 2\n# %%\nCG_CELL_MD_x\nCG_CELL_CODE_x = 1\n",
        )

    def test_multi_format_saves_one_stem_in_three_formats(self):
        calls = savefig_calls(built(fake_parent(), ("multi_format",)))
        paths = [c.args[0].value for c in calls]
        self.assertEqual(sorted(paths), ["out.pdf", "out.png", "out.svg"])
        orders = {
            tuple(savefig_paths(built(fake_parent(), ("multi_format",), s))) for s in range(20)
        }
        self.assertGreater(len(orders), 1)

    def test_pdfpages_writes_several_pages_through_pdfpages(self):
        out = built(fake_parent(), ("pdfpages",))
        self.assertIn("from matplotlib.backends.backend_pdf import PdfPages as _cg_PdfPages", out)
        self.assertIn("with _cg_PdfPages('out.pdf') as _cg_pdf:", out)
        pages = out.count("_cg_pdf.savefig(fig)")
        self.assertIn(pages, (2, 3))
        self.assertNotIn("fig.savefig('out.png')", out)

    def test_bytesio_close_saves_into_a_buffer_then_closes_the_figure(self):
        out = built(fake_parent(), ("bytesio_close",))
        self.assertIn("_cg_buf = _cg_io.BytesIO()", out)
        self.assertRegex(out, r"fig\.savefig\(_cg_buf, format='(png|svg|pdf)'\)")
        self.assertTrue(out.rstrip().endswith("plt.close(fig)"))

    def test_same_stem_two_figs_writes_a_second_figure_to_the_same_stem_then_closes_it(self):
        out = built(fake_parent(), ("same_stem_two_figs",))
        self.assertIn("_cg_fig2.savefig('out.", out)
        self.assertNotIn("_cg_fig2.savefig('out.png')", out)
        self.assertTrue(out.rstrip().endswith("plt.close(_cg_fig2)"))
        (first, second) = savefig_paths(out)
        self.assertEqual((first, second.rsplit(".", 1)[0]), ("out.png", "out"))

    def test_bbox_tight_dpi(self):
        out = built(fake_parent(), ("bbox_tight_dpi",))
        self.assertIn("fig.savefig('out.png', bbox_inches='tight', dpi=600)", out)

    def test_parameters_come_from_seed_and_structure_not_the_case_id(self):
        a = xt.build(fake_parent(case="twin_a"), ("pdfpages",), 5)[0]
        b = xt.build(fake_parent(case="twin_b"), ("pdfpages",), 5)[0]
        self.assertEqual(a["chain"], b["chain"])
        pages = {
            xt.build(fake_parent(), ("pdfpages",), s)[0]["chain"][0]["params"]["pages"]
            for s in range(20)
        }
        self.assertEqual(pages, {2, 3})


def savefig_paths(source: str) -> list[str]:
    return [c.args[0].value for c in savefig_calls(source) if isinstance(c.args[0], ast.Constant)]


def _native(artifacts):
    return {
        "ok": True,
        "exit_status": 0,
        "figure_count": 1,
        "figures_shown": 0,
        "semantics": {"f": {"titles": ["t"]}},
        "savefig_last_writer": {},
        "artifacts": artifacts,
    }


def _tav(calls):
    return {"stems": {"s": {"titles": ["t"]}}, "error": None, "savefig_calls": calls}


def _art(path, pages=None):
    return {"path": path, **({} if pages is None else {"pdf_pages": pages})}


class OutputFacts(unittest.TestCase):
    """多输出变换比的是输出本身：格式集合与页数（Codex #1368 P1）。"""

    THREE = [_art("s.pdf", 1), _art("s.png"), _art("s.svg")]

    def kinds(self, chain, native, tavotto):
        return [
            d["kind"]
            for d in ex.compare("axis_changing", native, tavotto, None, None, chain)["differences"]
        ]

    def test_every_written_format_recorded_is_no_difference(self):
        verdict = ex.compare(
            "axis_changing",
            _native(self.THREE),
            _tav({"s": ["svg", "png", "pdf"]}),
            None,
            None,
            ["multi_format"],
        )
        self.assertEqual(verdict["outcome"], "no_difference")

    def test_a_missing_format_is_a_format_set_mismatch(self):
        for calls in (["png"], ["png", "pdf"], ["png", "pdf", "svg", "eps"]):
            with self.subTest(calls=calls):
                self.assertEqual(
                    self.kinds(["multi_format"], _native(self.THREE), _tav({"s": calls})),
                    ["format_set_mismatch"],
                )

    def test_unobserved_calls_are_not_a_format_mismatch(self):
        self.assertEqual(self.kinds(["multi_format"], _native(self.THREE), _tav({})), [])

    def test_formats_are_only_compared_for_the_multi_format_transform(self):
        self.assertEqual(self.kinds(["show_only"], _native(self.THREE), _tav({"s": ["png"]})), [])

    def test_pages_recorded_equal_to_pages_written_is_no_difference(self):
        native = _native([_art("s.pdf", 3)])
        self.assertEqual(self.kinds(["pdfpages"], native, _tav({"s": ["pdf", "pdf", "pdf"]})), [])

    def test_a_missing_page_is_a_page_count_mismatch(self):
        native = _native([_art("s.pdf", 3)])
        for calls in ([], ["pdf"], ["pdf", "pdf"], ["pdf"] * 4):
            with self.subTest(calls=calls):
                self.assertEqual(
                    self.kinds(["pdfpages"], native, _tav({"s": calls})), ["page_count_mismatch"]
                )

    def test_pages_are_only_compared_for_the_pdfpages_transform(self):
        native = _native([_art("s.pdf", 3)])
        self.assertEqual(self.kinds(["multi_format"], native, _tav({"s": []})), [])


class Applicability(unittest.TestCase):
    def applies(self, name, parent):
        return xt.BY_NAME[name].applies(ctx_of(parent))

    def test_output_calls_need_a_direct_savefig(self):
        parent = fake_parent(NO_SAVEFIG, "__main__")
        for name in OUTPUT_CALLS + ("return_fig", "sys_argv_out"):
            self.assertFalse(self.applies(name, parent), name)
        for name in ("class_wrapper", "cell_markers"):
            self.assertTrue(self.applies(name, parent), name)

    def test_savefig_inside_a_loop_is_not_a_direct_site(self):
        src = "import matplotlib.pyplot as plt\n\nfor i in range(2):\n    fig, ax = plt.subplots()\n    fig.savefig('o.png')\n"
        parent = fake_parent(src, "__main__")
        for name in OUTPUT_CALLS + ("return_fig",):
            self.assertFalse(self.applies(name, parent), name)

    def test_a_non_literal_or_extensionless_path_only_serves_the_variants_that_do_not_need_a_stem(
        self,
    ):
        src = "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nout = 'a' + '.png'\nfig.savefig(out)\n"
        parent = fake_parent(src, "__main__")
        for name in ("multi_format", "pdfpages", "same_stem_two_figs"):
            self.assertFalse(self.applies(name, parent), name)
        self.assertTrue(self.applies("bytesio_close", parent))

    def test_a_format_keyword_blocks_the_variants_that_would_clash_with_it(self):
        src = "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nfig.savefig('a.png', format='png')\n"
        parent = fake_parent(src, "__main__")
        for name in ("multi_format", "pdfpages", "bytesio_close", "same_stem_two_figs"):
            self.assertFalse(self.applies(name, parent), name)

    def test_bbox_tight_dpi_is_a_noop_on_a_script_that_already_has_them(self):
        src = "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nfig.savefig('a.png', bbox_inches='tight', dpi=600)\n"
        self.assertFalse(self.applies("bbox_tight_dpi", fake_parent(src, "__main__")))

    def test_return_fig_refuses_when_the_tail_needs_something_the_head_defined(self):
        src = (
            "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nname = 'a.png'\n"
            "fig.savefig(name)\n"
        )
        self.assertFalse(self.applies("return_fig", fake_parent(src, "__main__")))

    def test_names_the_rest_of_the_module_reads_cannot_move_into_a_function(self):
        src = (
            "import matplotlib.pyplot as plt\n\n\ndef helper():\n    return LIMIT\n\n\n"
            "LIMIT = 3\nfig, ax = plt.subplots()\nax.plot([1, 2, 3])\nfig.savefig('a.png')\nhelper()\n"
        )
        parent = fake_parent(src, "__main__")
        self.assertFalse(self.applies("class_wrapper", parent))
        ok = "import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\nax.plot([1])\nfig.savefig('a.png')\n"
        self.assertTrue(self.applies("class_wrapper", fake_parent(ok, "__main__")))

    def test_statements_that_cannot_live_in_a_function_body_are_refused(self):
        for line in ("global G", "from math import *"):
            src = f"import matplotlib.pyplot as plt\n\nfig, ax = plt.subplots()\n{line}\nfig.savefig('a.png')\n"
            self.assertFalse(self.applies("class_wrapper", fake_parent(src, "__main__")), line)


class Determinism(unittest.TestCase):
    CODE = (
        "import sys, json, hashlib\nsys.path.insert(0, sys.argv[1])\n"
        "import compat_explore_transforms as xt\nout = {}\n"
        "for case in ('shape_oo_savefig', 'shape_toplevel_imperative'):\n"
        "    p = xt.load_parent('compat_engine', case)\n"
        "    for name in sys.argv[2:]:\n"
        "        m, files = xt.build(p, (name,), 5)\n"
        "        out[case + ':' + name] = [m['candidate_id'], {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}]\n"
        "print(json.dumps(out, sort_keys=True))\n"
    )

    def run_once(self, hashseed):
        env = {
            **os.environ,
            "PYTHONHASHSEED": hashseed,
            "PYTHONPATH": str(SCRIPTS.parents[1] / "src"),
        }
        done = subprocess.run(
            [sys.executable, "-c", self.CODE, str(SCRIPTS), *NEW],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_every_new_transform_is_byte_identical_across_processes(self):
        first, second = self.run_once("1"), self.run_once("2")
        self.assertEqual(first, second)
        found = json.loads(first)
        # 两个父本 × 九个变换：每个都真的构建出来了，不是因为不适用而静默缺席
        expected = {
            f"{c}:{n}" for c in ("shape_oo_savefig", "shape_toplevel_imperative") for n in NEW
        }
        self.assertEqual(set(found), expected)


class ParentSelection(unittest.TestCase):
    def test_rule_is_tier_must_and_no_stage_declared_false(self):
        cases = {
            "a": {"tier": "must", "expected": {}},
            "b": {"tier": "must", "expected": {"fidelity": False}},
            "c": {"tier": "expected", "expected": {}},
            "d": {"tier": "exploratory", "expected": {}},
            "e": {"tier": "must"},
            "f": {"tier": "must", "expected": {"fidelity": True}},
            "g": {"tier": "must", "expected": {"export": 0, "edit": None}},
            "h": {"tier": "must", "expected": {"fidelity": True, "replay": False}},
        }
        self.assertEqual(xt.select_engine_parents(cases), ("a", "e", "f", "g"))

    def test_result_is_sorted_and_independent_of_input_order(self):
        cases = {k: {"tier": "must", "expected": {}} for k in ("z", "a", "m")}
        self.assertEqual(xt.select_engine_parents(cases), ("a", "m", "z"))
        self.assertEqual(
            xt.select_engine_parents(dict(reversed(list(cases.items())))), ("a", "m", "z")
        )

    def test_the_real_corpus_selection(self):
        cases = cg.registered("compat_engine")
        ids = xt.engine_parent_ids()
        self.assertTrue(set(ids) <= {c for c, v in cases.items() if v["tier"] == "must"})
        self.assertNotIn("ax_constrained_layout", ids)  # 声明 fidelity 为假
        self.assertEqual(len(ids), 38)
        categories = {cases[i]["category"] for i in ids}
        self.assertEqual(
            categories, {"script_shapes", "core_artists", "axes_layout", "scientific_stack"}
        )
        # 语料里 metamorphic 没有任何 must 用例，所以按规则它没有父本；覆盖报告要明说
        self.assertFalse(
            [c for c in cases.values() if c["category"] == "metamorphic" and c["tier"] == "must"]
        )

    def test_default_parents_are_the_rule_plus_the_static_v2_parents(self):
        parents = xt.default_parents()
        engine = [p.case for p in parents if p.suite == "compat_engine"]
        self.assertEqual(tuple(engine), xt.engine_parent_ids())
        self.assertEqual(
            [p.case for p in parents if p.suite == "compat_v2"], list(xt.V2_STATIC_PARENTS)
        )
        self.assertTrue(all(p.executable for p in parents if p.suite == "compat_engine"))


class Pairwise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parents = [
            xt.load_parent("compat_engine", c)
            for c in ("shape_oo_savefig", "shape_toplevel_imperative", "shape_entry_main")
        ]
        cls.plan = xt.pairwise_plan(cls.parents, 42)

    def test_every_feasible_combination_is_covered_exactly_once(self):
        chosen = [pair for pairs in self.plan["chosen"].values() for pair in pairs]
        self.assertEqual(len(chosen), len(set(chosen)))  # 一个对只在一个父本上出现：每次都新增覆盖
        self.assertEqual(set(chosen), set(self.plan["feasible"]))
        self.assertGreater(len(chosen), 30)

    def test_combinations_are_ordered_and_on_different_axes(self):
        for a, b in self.plan["feasible"]:
            self.assertLess(xt.BY_NAME[a].order, xt.BY_NAME[b].order)
            self.assertNotEqual(xt.BY_NAME[a].axis, xt.BY_NAME[b].axis)

    def test_a_parent_only_takes_pairs_it_can_apply(self):
        for parent in self.parents:
            own = set(xt.feasible_pairs(parent))
            for pair in self.plan["chosen"][(parent.suite, parent.case)]:
                self.assertIn(pair, own)
                xt.build(parent, pair, 1)

    def test_same_seed_same_plan_and_the_seed_only_breaks_ties(self):
        self.assertEqual(xt.pairwise_plan(self.parents, 42), self.plan)
        shares = {
            json.dumps(
                {k[1]: v for k, v in xt.pairwise_plan(self.parents, seed)["chosen"].items()},
                sort_keys=True,
            )
            for seed in range(6)
        }
        self.assertGreater(len(shares), 1)
        for seed in range(6):
            other = xt.pairwise_plan(self.parents, seed)
            self.assertEqual(other["feasible"], self.plan["feasible"])

    def test_the_cover_is_spread_over_parents_not_piled_on_one(self):
        sizes = [len(v) for v in self.plan["chosen"].values()]
        self.assertGreater(min(sizes), 0)
        self.assertLess(max(sizes), sum(sizes))

    def test_chains_for_returns_singles_plus_the_parents_share(self):
        parent = self.parents[0]
        chains = xt.chains_for(parent, 42, self.plan)
        pairs = [c for c in chains if len(c) == 2]
        self.assertEqual(
            pairs, [tuple(p) for p in self.plan["chosen"][(parent.suite, parent.case)]]
        )
        self.assertTrue(all(len(c) == 1 for c in chains if c not in pairs))

    def test_generate_candidates_carry_the_whole_cover(self):
        generated = xt.generate(42, self.parents)
        cov = generated["coverage"]["pairwise"]
        self.assertEqual(cov["covered"], cov["feasible"])
        self.assertEqual(cov["uncovered"], [])
        self.assertEqual(cov["feasible"], len(self.plan["feasible"]))
        self.assertEqual(generated, xt.generate(42, self.parents) | {"_files": generated["_files"]})

    def test_coverage_says_when_a_combination_is_missing(self):
        generated = xt.generate(42, self.parents)
        kept = [
            c
            for c in generated["candidates"]
            if len(c["chain"]) == 1 or c["axis_vector"][1] != "style:rcparams"
        ]
        cov = xt.coverage(self.parents, kept, [], self.plan)["pairwise"]
        self.assertLess(cov["covered"], cov["feasible"])
        self.assertTrue(cov["uncovered"])
        self.assertTrue(all(pair[1] == "rcparams" for pair in cov["uncovered"]))


class SelectByFamily(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parents = [
            xt.load_parent("compat_engine", c)
            for c in (
                "shape_oo_savefig",
                "shape_toplevel_imperative",
                "shape_entry_main",
                "shape_plt_savefig",
            )
        ]
        cls.candidates = xt.generate(42, cls.parents)["candidates"]

    def test_a_small_limit_still_spans_every_family(self):
        families = {c["chain"][0]["family"] for c in self.candidates}
        self.assertGreaterEqual(len(families), 8)
        chosen = ex.select(self.candidates, len(families))
        self.assertEqual({c["chain"][0]["family"] for c in chosen}, families)

    def test_the_two_audited_gaps_are_always_within_a_limit_of_24(self):
        chosen = ex.select(self.candidates, 24)
        names = {c["chain"][0]["name"] for c in chosen}
        self.assertLessEqual({"show_close", "same_path_loop"}, names)
        self.assertEqual(len(chosen), 24)
        self.assertEqual(chosen, ex.select(self.candidates, 24))

    def test_transforms_of_one_family_take_turns(self):
        chosen = ex.select(self.candidates, 40)
        out = {c["chain"][0]["name"] for c in chosen if c["chain"][0]["family"] == "output_call"}
        self.assertGreaterEqual(len(out), 4)


class SeedRotation(unittest.TestCase):
    def test_which_transforms_of_a_big_family_run_depends_on_the_seed(self):
        parents = [xt.load_parent("compat_engine", c) for c in ("shape_oo_savefig",)]
        runs = set()
        for seed in range(1, 7):
            pool = [c for c in xt.generate(seed, parents)["candidates"] if len(c["chain"]) == 1]
            chosen = ex.select(pool, 12)
            runs.add(
                tuple(
                    sorted(
                        c["chain"][0]["name"]
                        for c in chosen
                        if c["chain"][0]["family"] == "data_source"
                    )
                )
            )
        self.assertGreater(len(runs), 1)


class ShardsAndSelect(unittest.TestCase):
    """CG-09 的 --shard 在 --limit 之前切池：每片自己轮转，片与片不重不漏。"""

    def test_shards_partition_the_pool_and_select_stays_inside_its_shard(self):
        import compat_explore_shards as sh

        parents = [
            xt.load_parent("compat_engine", c)
            for c in ("shape_oo_savefig", "shape_toplevel_imperative")
        ]
        pool = xt.generate(42, parents)["candidates"]
        seen: list[str] = []
        for k in (1, 2, 3):
            part = sh.slice_pool(pool, k, 3)
            chosen = ex.select(part, 8)
            self.assertLessEqual(
                {c["candidate_id"] for c in chosen}, {c["candidate_id"] for c in part}
            )
            self.assertEqual(len(chosen), 8)
            self.assertEqual(chosen, ex.select(part, 8))
            seen += [c["candidate_id"] for c in part]
        self.assertEqual(sorted(seen), sorted(c["candidate_id"] for c in pool))


class RealExecution(unittest.TestCase):
    """真实执行（需要科学栈）。每条用例只跑一个变换在 2 个父本上——合在一起会超出 5 分钟单例超时。"""

    PARENTS = ("shape_oo_savefig", "shape_entry_main")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)

    def run_transform(self, parent_case, name):
        parent = xt.load_parent("compat_engine", parent_case)
        manifest, _ = xt.build(parent, (name,), 7)
        out = Path(self.tmp.name) / f"{parent_case}_{name}"
        out.mkdir()
        return manifest, ex.run_one(manifest, 7, out, 200)

    def check_preserving(self, name):
        for case in self.PARENTS:
            with self.subTest(parent=case, transform=name):
                manifest, record = self.run_transform(case, name)
                self.assertEqual(manifest["class"], "shape_preserving")
                self.assertGreaterEqual(record["executions"]["parent_native"], 1)
                self.assertEqual(record["native"]["figure_count"], 1, record)
                self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
                structure = record["stages"]["structure"]
                self.assertEqual(structure["status"], "passed", record["stages"])
                self.assertGreater(structure["data_compared"], 0, record["stages"])

    def check_axis_changing(self, name, case="shape_oo_savefig"):
        manifest, record = self.run_transform(case, name)
        self.assertEqual(manifest["class"], "axis_changing")
        self.assertGreaterEqual(record["native"]["figure_count"], 1, record)
        return record

    def kinds(self, record):
        return {d["kind"] for d in record["judgment"]["differences"]}

    def test_multi_format_one_stem_three_formats_matches_native(self):
        record = self.check_axis_changing("multi_format")
        self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
        self.assertEqual(len(record["native"]["artifacts"]), 3, record["native"])
        (stem,) = {Path(a["path"]).stem for a in record["native"]["artifacts"]}
        self.assertEqual(
            sorted(record["tavotto"]["savefig_calls"][stem]),
            ["pdf", "png", "svg"],
            record["tavotto"],
        )

    def test_pdfpages_pages_are_known_since_pr2(self):
        """多页 PDF（#1370 PR2 之后的口径）：产品现在**知道页数**——每一页记成那份 PDF 路径的一次 occurrence，
        build 响应的 `pdf_pages` 摘要带页数；`page_count_mismatch` 消失（PR1 时它是唯一的差异）。图数仍等于原生的图数
        （模板把**同一张**图写 N 页，所以是 1 张），stem 取 PDF 文件名、来源 pyplot（多页 PDF 不是原件）。

        原生对照（`compat_driver.run_native`）也按产品的方式认领 PdfPages 的页：保存成功之后记进同一个
        `PdfPagesLedger`、脚本结束先于 pyplot 兜底 `adopt_pdf_pages`，所以两边 stem 都是 PDF 文件名，保真层能比，
        判定是 `no_difference`。把编辑导回多页 PDF（A）**不做**：写回对它一律拒绝、引导去导出。"""
        for case in self.PARENTS:
            with self.subTest(parent=case):
                record = self.check_axis_changing("pdfpages", case)
                self.assertEqual(record["judgment"]["differences"], [], record["judgment"])
                self.assertNotIn("page_count_mismatch", self.kinds(record))
                self.assertEqual(record["judgment"]["outcome"], "no_difference", record["judgment"])
                self.assertEqual(record["unresolved_layers"], [])
                # 图数：认领了（不是 script_no_figure / 0 张），数量等于原生的图数
                self.assertEqual(len(record["tavotto"]["stems"]), record["native"]["figure_count"])
                (stem,) = record["tavotto"]["stems"]
                (artifact,) = record["native"]["artifacts"]
                self.assertEqual(stem, Path(artifact["path"]).stem)  # stem 取 PDF 文件名
                # 页数：产品记的页数 == 原生 PDF 的页数（≥ 2），路径就是那份 PDF
                native_pages = ex.native_facts(record["native"])["pdf_pages"]
                self.assertGreaterEqual(native_pages, 2)
                self.assertEqual(
                    record["tavotto"]["pdf_pages"],
                    [{"path": artifact["path"], "pages": native_pages}],
                )

    def test_bytesio_close_is_the_known_gap_749(self):
        """savefig 到 BytesIO 再 close：原生有一张图，Tavotto 一张都没捕获（script_no_figure）。登记已知、已看到（家族 G6-bytesio-close-zero-figures）。"""
        for case in self.PARENTS:
            with self.subTest(parent=case):
                record = self.check_axis_changing("bytesio_close", case)
                self.assertEqual(record["judgment"]["outcome"], "difference_found", record)
                self.assertEqual(self.kinds(record), BYTESIO_KINDS, record["judgment"])
                zero = record["judgment"]["differences"][0]
                self.assertEqual(zero["evidence"]["tavotto_error"], "script_no_figure")

    def test_same_stem_two_figs_matches_native_file_by_file(self):
        """同一 stem 两张图、后写的被 close。#1250 起第二张另起 `<stem>-2`，#1309 起每个落盘文件按「谁写的」
        归属：原生 `<stem>.pdf` 是第一张、`<stem>.png` 是第二张，Tavotto 的 `<stem>` 对 pdf、`<stem>-2` 对 png，
        逐个对得上。曾被判成 same_path_content_mismatch，那是 compare 把路径硬按 stem 键去比的误报。"""
        for case in self.PARENTS:
            with self.subTest(parent=case):
                record = self.check_axis_changing("same_stem_two_figs", case)
                self.assertEqual(record["native"]["figure_count"], 2, record)
                self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
                # 第二张图被 Tavotto 另存了一个 stem（不是丢了），并只绑它自己存过的格式
                titles = [t for v in record["tavotto"]["stems"].values() for t in v["titles"]]
                self.assertIn("cg_second", titles)
                formats = sorted(
                    f for fs in record["tavotto"]["savefig_calls"].values() for f in fs
                )
                self.assertEqual(len(formats), 2, record["tavotto"])
                self.assertEqual(len(set(formats)), 2, record["tavotto"])

    def test_bbox_tight_dpi_native_and_tavotto_pixels_agree(self):
        """tight 裁切下原生与 Tavotto 的像素必须一致（ADR 0098：frame 就是图幅）。曾观察到的
        `pixel_mismatch`（原生 [640, 416] / [640, 426]、Tavotto [640, 494] / [640, 502]）成因是 #1321 之前
        原生参考图没有回放脚本原 savefig 的 tight 裁切，是比对口径问题，不是产品缺口；回退 #1321 这条会红。"""
        for case in self.PARENTS:
            with self.subTest(parent=case):
                record = self.check_axis_changing("bbox_tight_dpi", case)
                self.assertEqual(record["judgment"]["outcome"], "no_difference", record["judgment"])
                self.assertEqual(record["stages"]["fidelity"]["status"], "passed", record["stages"])


#: 已知缺口的差异种类（Tavotto 产品侧，不是模板 bug；这里只登记「已知、已看到」，不替产品掩盖）
BYTESIO_KINDS = {"zero_figures"}

for _name in ENTRY_FORMS:
    setattr(
        RealExecution,
        f"test_{_name}_keeps_the_parent_output",
        lambda self, name=_name: self.check_preserving(name),
    )


if __name__ == "__main__":
    unittest.main()
