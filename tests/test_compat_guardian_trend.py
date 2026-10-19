"""Trend summary: fixed-set regressions vs scope changes, environment comparability, honest metrics."""

from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_guardian_trend as tr  # noqa: E402
import compat_triage as tg  # noqa: E402
from test_compat_guardian import GuardianBase  # noqa: E402


class Trend(GuardianBase):
    def make_report(self, rows: list[dict], **top) -> dict:
        report = copy.deepcopy(self.report)
        report["cases"] = rows
        report["expected_cases"] = [r["id"] for r in rows]
        report["coverage"] = cg.coverage_table(rows)
        report["verdict"] = cg.verdict(rows)
        report.update(top)
        return report

    def case(self, name: str, status="passed", fixture="a", env=None):
        row = copy.deepcopy(self.row)
        row["id"] = name
        row["status"] = status
        row["fixture"] = {
            "files": {f"tests/x/{name}.py": fixture},
            "source_sha256": "0" * 64,
            "data_sha256": "0" * 64,
        }
        if env:
            row["environment_before"] = row["environment_after"] = env
        if status == "failed":
            row["stages"]["capture"] = cg.stage("failed", "x")
            row["failure_category"] = {
                "category": "figure_capture",
                "source_code": "stage:capture",
                "basis": "x",
            }
        return row

    def write(self, name: str, report: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def summary(self, rows, previous=None, corpus="1" * 64, env=None):
        rows = [self.case(*r) if isinstance(r, tuple) else r for r in rows]
        report = self.make_report(rows, corpus_sha256=corpus)
        path = self.write(f"r{len(list(self.root.iterdir()))}.json", report)
        return tr.build([path], [], previous, [])

    # ---- metrics
    def test_section_12_metrics_are_present_and_honestly_marked(self):
        out = self.summary([("a",), ("b",)])
        metrics = out["metrics"]
        self.assertEqual(
            set(metrics),
            {
                "first_figure_success",
                "execution_attempts",
                "semantic_fidelity",
                "editable_export_success",
                "family_coverage",
                "real_user_success_rate",
            },
        )
        self.assertEqual(metrics["family_coverage"]["status"], "not_computed")
        self.assertIn("no explore report", metrics["family_coverage"]["reason"])
        user = metrics["real_user_success_rate"]
        self.assertEqual(user["status"], "not_computed")
        self.assertIn("not be read as user success", user["reason"])
        ffs = metrics["first_figure_success"]["compat_engine"]
        self.assertEqual((ffs["status"], ffs["numerator"], ffs["denominator"]), ("computed", 2, 2))
        self.assertIn("not real users", ffs["population"])

    def test_adapter_that_does_not_drive_a_layer_is_not_computed_not_zero(self):
        rows = [self.case("a")]
        for name in cg.V2_UNCOVERED:
            rows[0]["stages"][name] = cg.stage("not_covered", "x")
        rows[0]["status"] = "partial_coverage"
        out = self.summary(rows)
        ee = out["metrics"]["editable_export_success"]["compat_engine"]
        self.assertEqual(ee["status"], "not_computed")
        self.assertIn("not_covered", ee["reason"])

    def test_failures_stay_in_the_denominator(self):
        out = self.summary([("a",), ("b", "failed")])
        ffs = out["metrics"]["first_figure_success"]["compat_engine"]
        self.assertEqual((ffs["numerator"], ffs["denominator"]), (1, 2))

    def test_unmeasured_executions_are_unmeasured_not_zero(self):
        rows = [self.case("a")]
        rows[0]["executions"]["tavotto"] = None
        out = self.summary(rows)
        self.assertEqual(
            out["metrics"]["execution_attempts"]["compat_engine"]["status"], "not_computed"
        )

    # ---- comparison
    def baseline(self):
        return self.summary([("a",), ("b",), ("c", "failed")])

    def test_no_previous_summary_means_trend_and_scope_are_not_computed(self):
        self.assertEqual(self.baseline()["comparison"]["status"], "no_previous")

    def test_same_cases_same_environment_regression_is_detected_on_the_fixed_set(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b", "failed"), ("c", "failed")], previous)
        cmp_ = current["comparison"]["fixed_set"]
        self.assertEqual(cmp_["verdict"], "regression")
        self.assertEqual(
            [(t["case"], t["kind"]) for t in cmp_["transitions"]],
            [("compat_engine/b", "regression")],
        )

    def test_removing_a_hard_case_is_a_scope_change_never_an_improvement(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",)], previous)
        comparison = current["comparison"]
        self.assertEqual(comparison["scope_changes"]["removed"], ["compat_engine/c"])
        self.assertEqual(comparison["scope_changes"]["removed_non_passing"], ["compat_engine/c"])
        self.assertEqual(comparison["scope_changes"]["denominator"], {"previous": 3, "current": 2})
        self.assertFalse(comparison["scope_changes"]["aggregate_rates_compared"])
        self.assertEqual(comparison["fixed_set"]["transitions"], [])
        self.assertEqual(
            [x for x in comparison["fixed_set"]["transitions"] if x["kind"] == "improvement"], []
        )

    def test_a_fixed_case_getting_fixed_is_the_only_improvement(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",), ("c", "passed")], previous)
        kinds = [t["kind"] for t in current["comparison"]["fixed_set"]["transitions"]]
        self.assertEqual(kinds, ["improvement"])

    def test_a_rewritten_declaration_is_scope_not_an_improvement(self):
        previous = self.baseline()  # c is an undeclared failure
        declared = self.case("c", "expected_limitation")
        declared["expectation"] = {
            "declared_outcome": "expected_limitation",
            "observed_outcome": "expected_limitation",
            "result": "control_met",
            "reason": "now declared a known limitation",
            "references": [{"kind": "issue", "ref": "#1"}],
            "diff": None,
        }
        current = self.summary([("a",), ("b",), declared], previous)
        comparison = current["comparison"]
        self.assertEqual(comparison["fixed_set"]["transitions"], [])
        self.assertEqual(comparison["fixed_set"]["size"], 2)
        self.assertEqual(comparison["scope_changes"]["declaration_changed"], ["compat_engine/c"])
        self.assertIn("declaration changed", tr.render_markdown(current))
        # The same declaration on both sides is compared as usual.
        again = self.summary([("a",), ("b",), declared], current)
        self.assertEqual(again["comparison"]["scope_changes"]["declaration_changed"], [])
        self.assertEqual(again["comparison"]["fixed_set"]["size"], 3)

    def test_a_missing_expected_suite_is_not_computed_never_read_as_removed(self):
        both = ("compat_engine", "compat_v2")

        def build(suites, previous=None):
            paths = []
            for i, suite in enumerate(suites):
                report = self.make_report([self.case(f"{suite}-a")], suite=suite)
                paths.append(self.write(f"s{len(list(self.root.iterdir()))}-{i}.json", report))
            return tr.build(paths, [], previous, [], both)

        full = build(both)
        self.assertEqual(full["comparison"]["status"], "no_previous")
        partial = build(("compat_engine",), full)
        self.assertEqual(partial["comparison"]["status"], "not_computed")
        self.assertEqual(partial["comparison"]["missing_suites"]["current"], ["compat_v2"])
        self.assertNotIn("scope_changes", partial["comparison"], "nothing is reported as removed")
        self.assertIn("not computed", tr.render_markdown(partial))
        # the incomplete summary must not become the baseline of the next complete run either
        again = build(both, partial)
        self.assertEqual(again["comparison"]["status"], "not_computed")
        self.assertEqual(again["comparison"]["missing_suites"]["previous"], ["compat_v2"])
        self.assertEqual(build(both, full)["comparison"]["status"], "compared")

    def test_different_corpus_version_is_not_compared_as_a_regression(self):
        previous = self.baseline()
        current = self.summary(
            [("a", "passed", "CHANGED"), ("b", "failed", "CHANGED")], previous, corpus="2" * 64
        )
        scope = current["comparison"]["scope_changes"]
        self.assertTrue(scope["corpus_version_changed"]["compat_engine"])
        self.assertEqual(sorted(scope["fixture_changed"]), ["compat_engine/a", "compat_engine/b"])
        fixed = current["comparison"]["fixed_set"]
        self.assertEqual(fixed["size"], 0)
        self.assertEqual(
            fixed["verdict"], "no_regression", "changed fixtures cannot regress; they are scope"
        )
        self.assertEqual(fixed["transitions"], [])

    def test_a_passing_case_becoming_inconclusive_withholds_no_regression(self):
        """passed → unknown 不是「无回归」：本次对这个 case 没有结论（Codex #1139 r4312231434）。"""
        previous = self.baseline()
        for status in ("unknown", "not_run", "environment_unavailable", "invalid_fixture"):
            current = self.summary([("a", status), ("b",)], previous)
            fixed = current["comparison"]["fixed_set"]
            self.assertEqual(fixed["verdict"], "inconclusive", status)
            self.assertEqual(fixed["transitions"][0]["kind"], "inconclusive", status)

    def test_a_different_environment_gives_no_regression_verdict(self):
        previous = self.baseline()
        env = copy.deepcopy(self.row["environment_before"])
        env["facts"] = {**env["facts"], "python": "3.14.0 other", "platform": "Other-OS"}
        env["fingerprint"] = cg.canonical(env["facts"])
        current = self.summary(
            [("a", "passed", "a", env), ("b", "failed", "a", env), ("c", "failed", "a", env)],
            previous,
        )
        fixed = current["comparison"]["fixed_set"]
        self.assertEqual(fixed["verdict"], "incomparable")
        self.assertFalse(fixed["environment_comparable"])
        self.assertIn("platform", fixed["environment_differences"])
        self.assertTrue(fixed["transitions"], "the observations are still listed")

    def test_markdown_separates_fixed_set_from_scope_and_says_what_is_not_covered(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",)], previous)
        text = tr.render_markdown(current)
        self.assertIn("**Fixed set**", text)
        self.assertIn("**Scope changes**", text)
        self.assertIn("(not an improvement)", text)
        self.assertIn("### Not covered", text)
        self.assertIn("not user success rates", text)

    # ---- explore side: family coverage and alerts
    def explore_report(self, rows):
        by_family = {
            "show_close": {
                "axis": "output_call",
                "transforms": ["show_close"],
                "parents_applicable": [],
                "parent_count": 0,
                "candidates": 1,
            },
            "import_alias": {
                "axis": "import_alias",
                "transforms": ["import_alias"],
                "parents_applicable": [],
                "parent_count": 0,
                "candidates": 1,
            },
        }
        return {
            "schema": "tavotto-guardian-explore/1",
            "run_id": "ee" * 16,
            "seed": 42,
            "source": {"commit": "a" * 40},
            "candidates": rows,
            "outcome_counts": {},
            "known_gaps": {"G1": {"found": False}},
            "coverage": {
                "by_family": by_family,
                "parents": {
                    "corpus_by_manifest_category": {"core_artists": 3, "script_shapes": 5},
                    "used_by_manifest_category": {"script_shapes": 2},
                },
            },
        }

    def test_family_coverage_counts_only_families_with_an_executed_candidate(self):
        rows = [
            {"outcome": "no_difference", "chain": ["show_close"]},
            {"outcome": "static_only", "chain": ["import_alias"]},
        ]
        path = self.write("explore.json", self.explore_report(rows))
        out = tr.build([], [path], None, [{"in_corpus": False, "fix_pr": None}])
        cov = out["metrics"]["family_coverage"]
        self.assertEqual((cov["numerator"], cov["denominator"]), (1, 2))
        self.assertEqual(cov["failure_family_records"], {"recorded": 1, "in_corpus": 0, "fixed": 0})
        self.assertTrue(any("core_artists" in u for u in out["uncovered"]))
        self.assertEqual(out["metrics"]["first_figure_success"]["status"], "not_computed")


BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "builtins.open"}
#: os 下唯一允许的调用：读环境变量与纯路径运算（不触盘的 os.path 函数之外一律不许）
OS_ALLOWED = (
    "os.environ.get",
    "os.path.join",
    "os.path.basename",
    "os.path.dirname",
    "os.path.splitext",
)


def confinement_violations(tree: ast.AST) -> tuple[list[str], list[str]]:
    """调用目标按 import **解析后的全名**判（`import os as safe; safe.system()`、`from os import system`
    都还原成 `os.system`），不看拼写（Codex #1139 r4306195794）。返回（违规清单, 文件写入目标）。"""
    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                alias[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                alias[a.asname or a.name] = f"{node.module or ''}.{a.name}"

    def target(func) -> str:
        if isinstance(func, ast.Name):
            return alias.get(func.id, func.id)
        if isinstance(func, ast.Attribute):
            return f"{target(func.value)}.{func.attr}"
        return ""

    violations, writes = [], []
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in PATH_MUTATORS
            and id(node) not in called
        ):
            # 先绑定再调用（`w = p.write_text; w(x)`）、当回调传出去……：不是直接调用的改动方法引用
            # 同样记成写入，精确写入清单因此会变（Codex #1139 r4312500393）
            writes.append(f"unbound:{node.attr}:{ast.unparse(node.value)}")
        if isinstance(node, ast.Attribute):
            # 经白名单模块转手的危险模块（`cg.subprocess.run`、`cg.os.remove`、`GS.shutil`……）：
            # 不管调不调用、拼成什么别名，链上中段出现危险模块就算越界（Codex #1139 r4312274849）
            through = target(node).split(".")[1:]
            hit = [seg for seg in through if seg in REACHABLE_BANNED_MODULES]
            if hit:
                violations.append(target(node))
        if not isinstance(node, ast.Call):
            continue
        name = target(node.func)
        if name in DYNAMIC_ACCESS:
            # `vars(cg)`、`globals()`：拿到命名空间就能取到任何模块（Codex #1139 r4312412189）
            violations.append(name)
        if name == "getattr" and node.args:
            # `getattr(cg, "subprocess")`：取的是已导入模块的属性，或属性名不是字面量——都可能
            # 拿到危险模块；只放行对普通对象（如 args）按字面量名取属性
            obj, attr = node.args[0], node.args[1] if len(node.args) > 1 else None
            on_module = isinstance(obj, ast.Name) and obj.id in alias
            literal = isinstance(attr, ast.Constant) and isinstance(attr.value, str)
            if on_module or not literal or attr.value in REACHABLE_BANNED_MODULES:
                violations.append(f"getattr:{ast.unparse(obj)}")
            elif attr.value in PATH_MUTATORS:
                # `getattr(Path(t), "write_text")(x)` 就是一次写入（Codex #1139 r4312538531）
                writes.append(f"getattr:{attr.value}:{ast.unparse(obj)}")
        if name.split(".")[-1] in INDIRECT_ATTRIBUTE_ACCESS:
            violations.append(name)
        if name in BANNED_CALLS:
            violations.append(name)
        if name.startswith("os.") and not name.startswith(OS_ALLOWED):
            # 白名单而不是黑名单：os 下任何会动文件系统 / 进程的调用（rmdir、chmod、symlink、truncate、write……）
            # 都不用逐个列出（Codex #1139 r4310373945）。triage 只读 os.environ
            violations.append(name)
        if isinstance(node.func, ast.Attribute) and node.func.attr in PATH_MUTATORS:
            # 接收者与（rename / replace / *_to 的）目标都是被改动的路径（Codex #1139 r4306432457）
            writes.append(f"{node.func.attr}:{ast.unparse(node.func.value)}")
            if node.func.attr in PATH_MOVERS and node.args:
                writes.append(f"{node.func.attr}->{ast.unparse(node.args[0])}")
    return violations, writes


#: 绕过 getattr 检查的等价取属性方式
INDIRECT_ATTRIBUTE_ACCESS = {"__getattribute__", "attrgetter", "methodcaller"}

#: 拿到整个命名空间的内建函数：经它们可以取到任何模块
DYNAMIC_ACCESS = {"vars", "globals", "locals", "setattr", "delattr"}

#: 经另一个模块的属性转手也不许碰的模块（进程、文件系统、网络、动态导入、解释器）
REACHABLE_BANNED_MODULES = {
    "os",
    "subprocess",
    "shutil",
    "socket",
    "urllib",
    "http",
    "builtins",
    "importlib",
    "ctypes",
    "sys",
    "signal",
    "multiprocessing",
    "tempfile",
    "io",
}


#: 所有会改文件系统的 `Path` 方法（写、建、删、改名、链接、改权限）
PATH_MUTATORS = {
    "write_text",
    "write_bytes",
    "open",
    "touch",
    "unlink",
    "rmdir",
    "mkdir",
    "rename",
    "replace",
    "symlink_to",
    "hardlink_to",
    "link_to",
    "chmod",
    "lchmod",
}
PATH_MOVERS = {"rename", "replace", "symlink_to", "hardlink_to", "link_to"}


class ConfinementCheckerSelfTest(unittest.TestCase):
    def test_aliases_and_from_imports_are_resolved_to_the_real_target(self):
        for snippet, hit in (
            ("import os as safe\nsafe.system('x')", "os.system"),
            ("from os import system\nsystem('x')", "os.system"),
            ("from os import system as s\ns('x')", "os.system"),
            ("import os\nos.execv('x', [])", "os.execv"),
            ("import builtins as b\nb.open('x')", "builtins.open"),
            ("import os\nos.rmdir('x')", "os.rmdir"),
            ("import os\nos.chmod('x', 0)", "os.chmod"),
            ("from os import symlink\nsymlink('a', 'b')", "os.symlink"),
            ("import os\nos.truncate('x', 0)", "os.truncate"),
            ("import os as o\no.write(1, b'x')", "os.write"),
        ):
            self.assertIn(hit, confinement_violations(ast.parse(snippet))[0], snippet)
        self.assertEqual(confinement_violations(ast.parse("# os.system('x')\n'os.system'"))[0], [])

    def test_side_effects_reached_through_an_allowlisted_module_are_violations(self):
        for snippet in (
            "import compat_guardian as cg\ncg.subprocess.run(['x'])",
            "import compat_guardian as cg\ncg.os.remove('x')",
            "import compat_guardian_summary as GS\nf = GS.shutil.rmtree\nf('x')",
            "from compat_guardian import os as o\no.remove('x')",
        ):
            self.assertTrue(confinement_violations(ast.parse(snippet))[0], snippet)
        ok = "import compat_guardian as cg\ncg.SCHEMA_PATH.read_text(encoding='utf-8')"
        self.assertEqual(confinement_violations(ast.parse(ok))[0], [])

    def test_a_mutator_bound_before_the_call_is_still_a_write(self):
        for snippet in (
            "writer = Path(target).write_text\nwriter(data)",
            "list(map(Path.unlink, paths))",
        ):
            self.assertTrue(
                any(
                    w.startswith("unbound:") for w in confinement_violations(ast.parse(snippet))[1]
                ),
                snippet,
            )

    def test_literal_getattr_of_a_mutator_is_a_write_and_indirect_access_is_banned(self):
        _, writes = confinement_violations(ast.parse("getattr(Path(t), 'write_text')(d)"))
        self.assertIn("getattr:write_text:Path(t)", writes)
        for snippet in ("Path(t).__getattribute__('write_text')(d)", "attrgetter('unlink')(p)()"):
            self.assertTrue(confinement_violations(ast.parse(snippet))[0], snippet)

    def test_dynamically_retrieved_modules_are_violations(self):
        for snippet in (
            "import compat_guardian as cg\ngetattr(cg, 'subprocess').run(['x'])",
            "import compat_guardian as cg\nx = getattr(cg, 'os')\nx.remove('y')",
            "name = 'os'\ngetattr(object(), name)",
            "import compat_guardian as cg\nvars(cg)['os'].remove('y')",
            "globals()['cg']",
        ):
            self.assertTrue(confinement_violations(ast.parse(snippet))[0], snippet)
        ok = "getattr(args, 'markdown_out', None)"
        self.assertEqual(confinement_violations(ast.parse(ok))[0], [])

    def test_every_mutating_path_call_is_tracked_with_its_destination(self):
        _, writes = confinement_violations(
            ast.parse("p.replace(q)\np.rename(r)\np.mkdir()\np.symlink_to(s)\np.chmod(0)")
        )
        for hit in ("replace->q", "rename->r", "mkdir:p", "symlink_to->s", "chmod:p"):
            self.assertIn(hit, writes)


class Alerts(unittest.TestCase):
    def cluster(self, **over):
        base = {
            "cluster_id": "cl-x",
            "key": {
                "family": "figure_capture",
                "stage": "capture",
                "behavior_kind": "zero_figures",
                "call_site": ["figure"],
            },
            "known_family": None,
            "reproducible": True,
            "size": 2,
            "representative": "r",
            "next_action": "look",
        }
        base.update(over)
        return base

    def test_only_new_reproduced_product_families_alert(self):
        self.assertEqual(len(tg.alerts([self.cluster()])), 1)
        self.assertEqual(tg.alerts([self.cluster(known_family="G1-show-close-zero-figures")]), [])
        self.assertEqual(tg.alerts([self.cluster(reproducible=None)]), [], "never re-run: no alert")
        self.assertEqual(tg.alerts([self.cluster(reproducible=False)]), [])
        for family in tg.NOT_PRODUCT_FAMILIES:
            self.assertEqual(
                tg.alerts([self.cluster(key={**self.cluster()["key"], "family": family})]), []
            )

    def test_triage_is_structurally_confined_to_its_report_files_and_annotations(self):
        """Proves, by parsing compat_triage.py with `ast` (comments and strings cannot fool it):
        its own imports stay inside a fixed allowlist (no network, subprocess or dynamic-import
        module), it has no `open`/`eval`/`exec`/`__import__`/`os.system`-style calls, and the only
        file writes are `.write_text` on the `--out` temp file and the `--markdown-out` path.
        It does NOT prove what the imported Guardian modules do, nor what the runner permits;
        the workflow's read-only permissions are checked separately."""
        tree = ast.parse((Path(cg.HERE) / "compat_triage.py").read_text(encoding="utf-8"))
        allowed = {
            "__future__",
            "ast",
            "hashlib",
            "json",
            "os",
            "pathlib",
            "compat_guardian",
            # read-only `load_report` (the summary's structural validation, Codex #1136 r4311781508)
            "compat_guardian_summary",
            "compat_guardian_taxonomy",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertLessEqual(imported, allowed)
        violations, writes = confinement_violations(tree)
        self.assertEqual(violations, [])
        self.assertEqual(
            sorted(writes),
            [
                "mkdir:out.parent",
                "replace->out",
                "replace:tmp",
                "write_text:Path(args.markdown_out)",
                "write_text:tmp",
            ],
            "unexpected file-mutation target",
        )
        # the only stdout channel for alerts is a workflow annotation, emitted by print()
        printed = [
            ast.unparse(n.args[0])
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print" and n.args
        ]
        self.assertTrue(any("::warning title=" in p for p in printed))


class ExploreOptions(unittest.TestCase):
    def test_unknown_family_is_rejected_and_filter_restricts_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=1,
                out=tmp,
                limit=None,
                parent="shape_oo_savefig",
                static_only=True,
                expected_sha=None,
                timeout=5,
                families="nope",
                candidates=None,
                confirm_differences=0,
            )
            with self.assertRaises(cg.EvidenceError):
                ex.explore(args)
            args.families = "import_alias"
            with mock.patch.object(ex, "run_one", side_effect=AssertionError("static only")):
                ex.explore(args)
            (run,) = Path(tmp).iterdir()
            report = json.loads((run / "reports/explore-report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["candidates"])
            self.assertTrue(all(c["chain"] == ["import_alias"] for c in report["candidates"]))

    def test_confirmation_reruns_one_candidate_per_shape_and_records_reproduction(self):
        diff = {
            "kind": "zero_figures",
            "summary": "s",
            "category": "figure_capture",
            "evidence": {},
        }
        rows = [
            {
                "candidate_id": f"c{i}",
                "outcome": "difference_found",
                "chain": ["show_close"],
                "differences": [diff],
            }
            for i in range(3)
        ] + [
            {
                "candidate_id": "ok",
                "outcome": "no_difference",
                "chain": ["rcparams"],
                "differences": [],
            }
        ]
        again = {"judgment": {"outcome": "difference_found", "differences": [diff]}}
        args = Namespace(confirm_differences=2, seed=1, timeout=1)
        with mock.patch.object(ex, "run_one", return_value=again) as rerun:
            ex.confirm_differences(
                rows, {r["candidate_id"]: {} for r in rows}, args, Path("/nonexistent")
            )
        self.assertEqual(rerun.call_count, 1, "one rerun per distinct finding shape")
        self.assertEqual(rows[0]["confirmation"], {"reproduced": True, "runs": 1})
        self.assertIsNone(rows[1]["confirmation"])
        gone = {"judgment": {"outcome": "no_difference", "differences": []}}
        rows2 = [dict(rows[0], confirmation=None)]
        with mock.patch.object(ex, "run_one", return_value=gone):
            ex.confirm_differences(rows2, {"c0": {}}, args, Path("/nonexistent"))
        self.assertFalse(rows2[0]["confirmation"]["reproduced"])

    def test_confirmation_keys_on_triage_behavior_identity_and_compares_it_again(self):
        diff = {
            "kind": "zero_figures",
            "summary": "s",
            "category": "figure_capture",
            "evidence": {},
        }

        def evidence(code):
            return {
                "native": {"exit_status": 0, "figure_count": 1, "figures_shown": 1},
                "tavotto": {"route": "r", "error": {"code": code}, "stems": {}},
            }

        def row(cid, code):
            return {
                "candidate_id": cid,
                "outcome": "difference_found",
                "chain": ["show_close"],  # same first transform and same difference kind
                "differences": [diff],
                "evidence": evidence(code),
            }

        rows = [row("c0", "E_ONE"), row("c1", "E_TWO"), row("c2", "E_ONE")]
        args = Namespace(confirm_differences=5, seed=1, timeout=1)

        def rerun(manifest, seed, out, timeout, reduction, tag):
            code = "E_ONE" if manifest["candidate_id"] == "c0" else "E_OTHER"
            return {
                "judgment": {"outcome": "difference_found", "differences": [diff]},
                "native": {"exit_status": 0, "figure_count": 1, "figures_shown": 1},
                "tavotto": {"route": "r", "error": {"code": code}, "stems": {}},
            }

        with mock.patch.object(ex, "run_one", side_effect=rerun) as run_one:
            ex.confirm_differences(
                rows,
                {r["candidate_id"]: {"candidate_id": r["candidate_id"]} for r in rows},
                args,
                Path("/x"),
            )
        self.assertEqual(run_one.call_count, 2, "two behavior identities, two confirmations")
        self.assertTrue(rows[0]["confirmation"]["reproduced"])
        self.assertFalse(
            rows[1]["confirmation"]["reproduced"],
            "same kind but a different behavior on the rerun is not a reproduction",
        )
        self.assertIsNone(rows[2]["confirmation"], "identical identity to c0 is deduplicated")


if __name__ == "__main__":
    unittest.main()
