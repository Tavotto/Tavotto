"""CG-09: explore sharding, shard-set validation, and the weekly report's listing order.

Sharding is a pure slice of the deduplicated pool; the set check is the only door between N
shard reports and `summarize` / `triage`. Every refusal here is a case the weekly sweep could hit
on GitHub (a shard that crashed, a re-run attempt, a sweep that straddled midnight).
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import random
import sys
import tempfile
import unittest
from argparse import ArgumentTypeError, Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_explore_shards as sh  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_guardian_summary as sm  # noqa: E402
import compat_guardian_trend as tr  # noqa: E402
import compat_triage as tg  # noqa: E402

N = 4


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cg.main(list(argv))
        except SystemExit as exc:  # argparse usage errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class Slicing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = xt.generate(42, xt.default_parents())["candidates"]

    def ids(self, rows):
        return [c["candidate_id"] for c in rows]

    def test_shards_union_to_the_whole_pool_are_disjoint_and_balanced(self):
        for n in (1, 2, 3, 4, 7, 16):
            slices = [sh.slice_pool(self.pool, k, n) for k in range(1, n + 1)]
            flat = [i for s in slices for i in self.ids(s)]
            self.assertEqual(sorted(flat), sorted(self.ids(self.pool)), n)
            self.assertEqual(len(flat), len(set(flat)), f"shards overlap for n={n}")
            sizes = [len(s) for s in slices]
            self.assertLessEqual(max(sizes) - min(sizes), 1, (n, sizes))

    def test_slice_is_deterministic_and_independent_of_pool_order(self):
        shuffled = list(self.pool)
        random.Random(5).shuffle(shuffled)
        for k in range(1, N + 1):
            self.assertEqual(
                sorted(self.ids(sh.slice_pool(self.pool, k, N))),
                sorted(self.ids(sh.slice_pool(shuffled, k, N))),
            )
        self.assertEqual(
            self.ids(sh.slice_pool(self.pool, 2, N)), self.ids(sh.slice_pool(self.pool, 2, N))
        )

    def test_slice_keeps_the_pools_own_order(self):
        order = {cid: i for i, cid in enumerate(self.ids(self.pool))}
        picked = [order[i] for i in self.ids(sh.slice_pool(self.pool, 3, N))]
        self.assertEqual(picked, sorted(picked))

    def test_duplicate_ids_refuse_to_shard(self):
        with self.assertRaises(cg.EvidenceError):
            sh.slice_pool([self.pool[0], self.pool[0]], 1, 2)

    def test_parse_shard_is_strict(self):
        self.assertEqual(sh.parse_shard("3/4"), (3, 4))
        self.assertEqual(sh.parse_shard("16/16"), (16, 16))
        for bad in (
            "0/4",
            "5/4",
            "1/17",
            "a/b",
            "1/0",
            "-1/4",
            "1",
            "1/4/2",
            "01/4",
            " 1/4",
            "1/4 ",
            "1.0/4",
            "",
        ):
            with self.assertRaises(ArgumentTypeError, msg=bad):
                sh.parse_shard(bad)


class ExploreShardCli(unittest.TestCase):
    """`explore --shard` through the real CLI; --static-only keeps these off the executor."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.n = 0

    def explore(self, *extra: str) -> tuple[int, dict | None]:
        type(self).n += 1
        out = Path(self.tmp.name) / f"run{self.n}"
        code, _, _ = run_cli("explore", "--seed", "42", "--static-only", "--out", str(out), *extra)
        reports = list(out.glob("*/reports/explore-report.json"))
        return code, (json.loads(reports[0].read_text(encoding="utf-8")) if reports else None)

    def test_invalid_shard_values_are_usage_errors_exit_2(self):
        for bad in ("0/4", "5/4", "1/17", "x", "1/0", "2/1"):
            code, report = self.explore("--shard", bad)
            self.assertEqual(code, 2, bad)
            self.assertIsNone(report, bad)

    def test_shard_cannot_be_combined_with_explicit_candidates(self):
        code, report = self.explore("--shard", "1/2", "--candidates", "deadbeefdeadbeefdead")
        self.assertEqual(code, 2)
        self.assertIsNone(report)

    def test_the_four_reports_cover_the_unsharded_pool_exactly_once(self):
        _, whole = self.explore()
        shards = [self.explore("--shard", f"{k}/{N}")[1] for k in range(1, N + 1)]
        self.assertNotIn("shard", whole)
        ids = [[r["candidate_id"] for r in s["candidates"]] for s in shards]
        flat = [i for part in ids for i in part]
        self.assertEqual(sorted(flat), sorted(r["candidate_id"] for r in whole["candidates"]))
        self.assertEqual(len(flat), len(set(flat)))
        self.assertLessEqual(max(map(len, ids)) - min(map(len, ids)), 1)
        for k, s in enumerate(shards, 1):
            self.assertEqual(s["shard"]["k"], k)
            self.assertEqual(s["shard"]["n"], N)
            self.assertEqual(s["shard"]["candidates_in_shard"], len(ids[k - 1]))
            self.assertEqual(s["shard"]["candidates_total"], len(whole["candidates"]))
            self.assertEqual(s["generator"], whole["generator"])
        self.assertIsNotNone(sh.check_set(shards))

    def test_limit_applies_to_the_shard_not_the_pool(self):
        _, report = self.explore("--shard", "2/4", "--limit", "3")
        self.assertEqual(len(report["candidates"]), 3)
        self.assertGreater(report["shard"]["candidates_in_shard"], 3)
        self.assertEqual(report["shard"]["filters"]["limit"], 3)

    def test_filters_run_before_the_slice_and_are_recorded(self):
        _, report = self.explore("--shard", "1/2", "--families", "entry_form")
        self.assertEqual(report["shard"]["candidates_total"], 2)
        self.assertEqual(report["shard"]["candidates_in_shard"], 1)
        self.assertEqual(report["shard"]["filters"]["families"], ["entry_form"])
        _, parent = self.explore("--shard", "1/2", "--parent", "shape_oo_savefig")
        self.assertEqual(parent["shard"]["filters"]["parent"], ["shape_oo_savefig"])
        self.assertTrue(all(r["parent"] == "shape_oo_savefig" for r in parent["candidates"]))

    def test_a_shard_with_nothing_in_it_publishes_no_report(self):
        code, report = self.explore("--shard", "3/3", "--families", "entry_form")
        self.assertEqual(code, 2)
        self.assertIsNone(report)


def shard_reports(n: int = N, seed: int = 42) -> list[dict]:
    """Synthetic but structurally real shard reports (schema fields the checks and consumers read)."""
    pool = xt.generate(seed, xt.default_parents())
    ids = sorted(c["candidate_id"] for c in pool["candidates"])
    by_id = {c["candidate_id"]: c for c in pool["candidates"]}
    reports = []
    for k in range(1, n + 1):
        mine = [by_id[i] for j, i in enumerate(ids) if j % n == k - 1]
        rows = [
            {
                "candidate_id": c["candidate_id"],
                "parent": c["parent"]["case"],
                "chain": [s["name"] for s in c["chain"]],
                "axis_vector": c["axis_vector"],
                "outcome": "no_difference"
                if c["execution_plan"]["status"] == "executable"
                else "static_only",
                "differences": [],
            }
            for c in mine
        ]
        outcomes: dict[str, int] = {}
        for r in rows:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        reports.append(
            {
                "schema": "tavotto-guardian-explore/1",
                "run_id": f"{k:02d}" + "a" * 30,
                "seed": seed,
                "source": {"commit": "c" * 40, "dirty": False},
                "expected_sha": "c" * 40,
                "generator": copy.deepcopy(pool["generator"]),
                "candidates": rows,
                "coverage": copy.deepcopy(pool["coverage"]),
                "known_gaps": {
                    "G1": {
                        "transform": "show_close",
                        "kind": "zero_figures",
                        "meaning": "m",
                        "found": False,
                        "candidates": [],
                    }
                },
                "outcome_counts": dict(sorted(outcomes.items())),
                "shard": sh.shard_block(
                    k,
                    n,
                    len(rows),
                    len(ids),
                    {"parent": None, "families": None, "limit": None, "static_only": False},
                ),
            }
        )
    return reports


class SetCheck(unittest.TestCase):
    def test_a_complete_set_merges_counts_equal_to_the_sum_of_shards(self):
        reports = shard_reports()
        info = sh.check_set(reports)
        self.assertTrue(info["complete"])
        self.assertEqual(info["label"], "4 of 4 shards completed")
        self.assertEqual(info["candidates_reported"], sum(len(r["candidates"]) for r in reports))
        self.assertEqual(info["candidates_reported"], info["candidates_total"])
        expected: dict[str, int] = {}
        for r in reports:
            for k, v in r["outcome_counts"].items():
                expected[k] = expected.get(k, 0) + v
        self.assertEqual(info["outcome_counts"], dict(sorted(expected.items())))
        merged = sh.merge_reports(reports)
        self.assertEqual(len(merged["candidates"]), info["candidates_total"])
        self.assertEqual(merged["outcome_counts"], info["outcome_counts"])

    def test_unsharded_reports_pass_through_untouched(self):
        plain = shard_reports()[0]
        del plain["shard"]
        self.assertIsNone(sh.check_set([plain]))
        self.assertIsNone(sh.check_set([]))

    def refuses(self, reports, text, **kw):
        with self.assertRaises(cg.EvidenceError) as caught:
            sh.check_set(reports, **kw)
        self.assertIn(text, str(caught.exception))

    def test_missing_shard_fails_unless_partial_is_explicitly_allowed(self):
        reports = shard_reports()
        del reports[2]
        self.refuses(reports, "3 of 4 completed, missing [3]")
        info = sh.check_set(reports, allow_partial=True)
        self.assertFalse(info["complete"])
        self.assertEqual(info["missing"], [3])
        self.assertEqual(info["label"], "3 of 4 shards completed")

    def test_duplicate_shard_fails_even_when_partial_is_allowed(self):
        reports = shard_reports()
        reports.append(copy.deepcopy(reports[1]))
        self.refuses(reports, "duplicate explore shard", allow_partial=True)

    def test_mixed_seed_head_generator_filters_or_total_fail_even_when_partial(self):
        for what, mutate in (
            ("seed", lambda r: r.update(seed=43)),
            ("HEAD", lambda r: r["source"].update(commit="d" * 40)),
            ("generator digest", lambda r: r["generator"].update(sha256="0" * 64)),
            ("selection filters", lambda r: r["shard"]["filters"].update(limit=5)),
            ("candidates_total", lambda r: r["shard"].update(candidates_total=1)),
            ("shard count", lambda r: r["shard"].update(n=8)),
        ):
            reports = shard_reports()
            mutate(reports[1])
            self.refuses(reports, "disagree", allow_partial=True)

    def test_unsharded_report_mixed_into_a_sweep_fails(self):
        reports = shard_reports()
        del reports[3]["shard"]
        self.refuses(reports, "unsharded", allow_partial=True)

    def test_shard_number_outside_the_range_fails(self):
        reports = shard_reports()
        reports[0]["shard"]["k"] = 9
        self.refuses(reports, "outside 1..4", allow_partial=True)

    def test_a_candidate_in_two_shards_fails(self):
        reports = shard_reports()
        reports[1]["candidates"].append(copy.deepcopy(reports[0]["candidates"][0]))
        reports[1]["shard"]["candidates_in_shard"] += 1
        self.refuses(reports, "appears in shards", allow_partial=True)

    def test_slices_that_do_not_add_up_to_the_pool_fail_when_complete(self):
        reports = shard_reports()
        reports[0]["shard"]["candidates_in_shard"] -= 1
        reports[0]["candidates"].pop()
        self.refuses(reports, "add up to")


class ConsumersEatShards(unittest.TestCase):
    """summarize and triage over report files, through the CLI."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.dir = Path(cls.tmp.name)
        cls.paths = []
        for report in shard_reports():
            d = cls.dir / report["run_id"] / "reports"
            d.mkdir(parents=True)
            p = d / "explore-report.json"
            p.write_text(json.dumps(report), encoding="utf-8")
            cls.paths.append(str(p))

    def summarize(self, paths, *extra):
        out = self.dir / "out.json"
        md = self.dir / "out.md"
        code, stdout, stderr = run_cli(
            "summarize", *paths, "--json-out", str(out), "--markdown-out", str(md), *extra
        )
        return code, stderr, (json.loads(out.read_text()) if code == 0 else None), md

    def test_summarize_four_shards_reports_the_whole_sweep(self):
        code, _, summary, md = self.summarize(self.paths)
        self.assertEqual(code, 0)
        shards = summary["trend"]["explore_shards"]
        self.assertEqual(shards["label"], "4 of 4 shards completed")
        sample = summary["trend"]["sample"]
        self.assertEqual(sample["explore_candidates_total"], shards["candidates_total"])
        self.assertIn("4 of 4 shards completed", md.read_text(encoding="utf-8"))

    def test_weekly_report_says_the_corpus_rate_is_not_a_user_rate_and_empty_comparison_is_not_green(
        self,
    ):
        _, _, first, md = self.summarize(self.paths)
        self.assertIn("语料通过率 ≠ 用户首次成功率", md.read_text(encoding="utf-8"))
        previous = self.dir / "previous.json"
        previous.write_text(json.dumps(first), encoding="utf-8")
        code, _, second, md = self.summarize(self.paths, "--previous", str(previous))
        self.assertEqual(code, 0)
        fixed = second["trend"]["comparison"]["fixed_set"]
        self.assertEqual(fixed["size"], 0)
        self.assertEqual(
            fixed["verdict"], "inconclusive", "nothing was compared, so not no_regression"
        )

    def test_summarize_refuses_a_missing_shard_but_labels_it_when_allowed(self):
        code, stderr, _, _ = self.summarize(self.paths[:3])
        self.assertEqual(code, 2)
        self.assertIn("incomplete", stderr)
        code, _, summary, md = self.summarize(self.paths[:3], "--allow-partial-shards")
        self.assertEqual(code, 0)
        self.assertFalse(summary["trend"]["explore_shards"]["complete"])
        text = md.read_text(encoding="utf-8")
        self.assertIn("3 of 4 shards completed", text)
        self.assertIn("NOT the whole pool", text)

    def test_summarize_refuses_a_mixed_seed_set(self):
        other = shard_reports(seed=43)[0]
        p = self.dir / "mixed" / "reports" / "explore-report.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(other), encoding="utf-8")
        code, stderr, _, _ = self.summarize([*self.paths[1:], str(p)], "--allow-partial-shards")
        self.assertEqual(code, 2)
        self.assertIn("disagree", stderr)

    def test_triage_checks_the_set_before_reading_findings(self):
        out = self.dir / "triage.json"
        code, _, stderr = run_cli("triage", *self.paths[:3], "--out", str(out))
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())
        code, stdout, _ = run_cli(
            "triage", *self.paths, "--out", str(out), "--markdown-out", str(self.dir / "t.md")
        )
        self.assertEqual(code, 0, stdout)
        self.assertEqual(
            json.loads(out.read_text())["explore_shards"]["label"], "4 of 4 shards completed"
        )
        code, _, _ = run_cli(
            "triage",
            *self.paths[:3],
            "--out",
            str(out),
            "--allow-partial-shards",
            "--markdown-out",
            str(self.dir / "t.md"),
        )
        self.assertEqual(code, 0)
        self.assertIn("3 of 4 shards completed", (self.dir / "t.md").read_text(encoding="utf-8"))


class AxisWeightsOnlyReorder(unittest.TestCase):
    def test_default_table_is_the_agreed_one(self):
        self.assertEqual(
            {
                a: sm.axis_weight(a)
                for a in (
                    "data_source",
                    "output_call",
                    "entry",
                    "style",
                    "import_alias",
                    "args",
                    "output_name",
                )
            },
            {
                "data_source": 3,
                "output_call": 3,
                "entry": 2,
                "style": 1,
                "import_alias": 1,
                "args": 1,
                "output_name": 1,
            },
        )
        self.assertEqual(sm.axis_weight("data_source:read_csv_numpy"), 3)

    def weekly(self, tmp):
        paths = []
        for report in shard_reports():
            report["candidates"][0]["outcome"] = "difference_found"
            report["outcome_counts"] = {"difference_found": 1}
            d = Path(tmp) / report["run_id"] / "reports"
            d.mkdir(parents=True)
            (d / "explore-report.json").write_text(json.dumps(report), encoding="utf-8")
            paths.append(str(d / "explore-report.json"))
        out, md = Path(tmp) / "s.json", Path(tmp) / "s.md"
        self.assertEqual(
            run_cli(
                "summarize",
                *paths,
                "--allow-partial-shards",
                "--json-out",
                str(out),
                "--markdown-out",
                str(md),
            )[0],
            0,
        )
        return out.read_bytes(), md.read_text(encoding="utf-8")

    def test_changing_weights_changes_listing_order_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            json_a, md_a = self.weekly(a)
            flipped = {
                "data_source": 1,
                "output_call": 1,
                "entry": 1,
                "style": 9,
                "import_alias": 8,
            }
            with mock.patch.object(sm, "AXIS_WEIGHTS", flipped):
                json_b, md_b = self.weekly(b)
        self.assertEqual(json_a, json_b, "metrics / verdict JSON must be byte-identical")
        self.assertNotEqual(md_a, md_b, "the listing order must actually follow the weights")
        self.assertEqual(sorted(md_a.splitlines()), sorted(md_b.splitlines()))

        def axes(md):
            rows = md.split("### Explore by axis", 1)[1].split("### Not covered", 1)[0].splitlines()
            return [
                ln.split("|")[1].strip() for ln in rows if ln.startswith("| ") and "---" not in ln
            ][1:]

        self.assertEqual(axes(md_a)[:2], ["data_source", "output_call"])
        self.assertEqual(axes(md_b)[:2], ["style", "import_alias"])
        self.assertEqual(sorted(axes(md_a)), sorted(axes(md_b)))

    def test_triage_render_orders_clusters_by_weight_without_touching_the_data(self):
        def cluster(cid, axes):
            return {
                "cluster_id": cid,
                "key": {"family": "f", "stage": "s", "behavior_kind": "b", "call_site": []},
                "known_family": None,
                "size": 1,
                "reproducible": True,
                "axes": axes,
            }

        result = {
            "summary": {"findings": 2, "unknown": 0},
            "clusters": [cluster("cl-style", ["style"]), cluster("cl-data", ["data_source"])],
            "alerts": [
                {
                    "cluster_id": "cl-style",
                    "family": "f",
                    "stage": "s",
                    "behavior_kind": "b",
                    "size": 1,
                    "representative": "r",
                    "next_action": "n",
                },
                {
                    "cluster_id": "cl-data",
                    "family": "f",
                    "stage": "s",
                    "behavior_kind": "b",
                    "size": 1,
                    "representative": "r",
                    "next_action": "n",
                },
            ],
        }
        before = json.dumps(result, sort_keys=True)
        default = tg.render_markdown(result)
        self.assertLess(default.index("cl-data"), default.index("cl-style"))
        with mock.patch.object(sm, "AXIS_WEIGHTS", {"style": 5, "data_source": 1}):
            flipped = tg.render_markdown(result)
        self.assertLess(flipped.index("cl-style"), flipped.index("cl-data"))
        self.assertEqual(json.dumps(result, sort_keys=True), before)
        self.assertEqual(sorted(default.splitlines()), sorted(flipped.splitlines()))

    def test_trend_numbers_do_not_read_the_weights(self):
        reports = shard_reports()
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for r in reports:
                p = Path(tmp) / r["run_id"] / "reports" / "explore-report.json"
                p.parent.mkdir(parents=True)
                p.write_text(json.dumps(r), encoding="utf-8")
                paths.append(p)
            info = sh.check_set(reports)
            one = tr.build([], paths, None, [], shards=info)
            with mock.patch.object(sm, "AXIS_WEIGHTS", {"style": 99}):
                two = tr.build([], paths, None, [], shards=info)
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))


class RealShardedExplore(unittest.TestCase):
    """Two real shards of a two-candidate pool, executed through the shared isolation."""

    def test_two_shards_end_to_end_then_merged(self):
        dirty = ex.dirty_execution_code()
        if dirty:
            self.skipTest(f"scripts/ci differs from HEAD ({dirty}); explore refuses to execute it")
        with tempfile.TemporaryDirectory() as tmp:
            reports = []
            for k in (1, 2):
                args = Namespace(
                    seed=7,
                    out=str(Path(tmp) / f"s{k}"),
                    limit=2,
                    parent=None,
                    static_only=False,
                    candidates=None,
                    families="entry_form",
                    confirm_differences=0,
                    expected_sha=None,
                    timeout=200,
                    shard=(k, 2),
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    ex.explore(args)
                (path,) = Path(tmp, f"s{k}").glob("*/reports/explore-report.json")
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            info = sh.check_set(reports)
            self.assertTrue(info["complete"])
            self.assertEqual(info["candidates_reported"], 2)
            outcomes = {r["outcome"] for rep in reports for r in rep["candidates"]}
            self.assertEqual(outcomes, {"no_difference", "static_only"})


class FakeClock:
    """Deterministic `time.monotonic`: only the stubs move it, so the budget arithmetic is exact."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def hang_until_cut(clock: FakeClock, calls: list, then=None):
    """Stub for `_adapter_run`: "sleeps" (moves the fake clock) for the timeout it was given, then
    times out like a hung child. `then=(seconds, record)` makes the parent snapshot succeed slowly."""

    def stub(request, hashes, evidence, timeout):
        mode = request.get("mode", "candidate")
        calls.append((mode, timeout))
        if then and mode == "parent_snapshot":
            clock.now += then[0]
            return then[1]
        clock.now += timeout
        raise cg.AdapterTimeout("stub: hung until the timeout")

    return stub


def with_budget_spent(report: dict, how_many: int = 2) -> dict:
    """The same shard report, as if its time budget ran out before `how_many` executable candidates."""
    rows = [r for r in report["candidates"] if r["outcome"] == "no_difference"][:how_many]
    for row in rows:
        row["outcome"] = "not_run"
    report["time_budget"] = {"max_seconds": 2700.0, "exhausted": True, "not_run": len(rows)}
    counts: dict[str, int] = {}
    for r in report["candidates"]:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    report["outcome_counts"] = dict(sorted(counts.items()))
    return report


class TimeBudget(unittest.TestCase):
    """`explore --max-seconds`: a spent budget publishes a partial report instead of being killed."""

    def run_explore(self, tmp, max_seconds, fake=None, **over):
        args = Namespace(
            seed=7,
            out=str(tmp),
            limit=None,
            parent=None,
            static_only=False,
            candidates=None,
            families="show_close",
            confirm_differences=0,
            expected_sha=None,
            timeout=200,
            shard=None,
            max_seconds=max_seconds,
            **over,
        )
        with (
            mock.patch.object(ex, "dirty_execution_code", return_value=[]),
            mock.patch.object(
                ex, "run_one", side_effect=fake or AssertionError("must not execute")
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = ex.explore(args)
        (path,) = Path(tmp).glob("*/reports/explore-report.json")
        return code, json.loads(path.read_text(encoding="utf-8"))

    def test_nothing_runs_after_the_budget_and_the_report_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, report = self.run_explore(tmp, 1e-9)
        rows = report["candidates"]
        self.assertTrue(rows)
        self.assertEqual(
            {r["outcome"] for r in rows},
            {"not_run"} | ({"static_only"} & {r["outcome"] for r in rows}),
        )
        for row in rows:
            if row["outcome"] == "not_run":
                self.assertIn("time budget exhausted", row["note"])
                self.assertEqual(row["executions"]["basis"], "not_executed")
                self.assertEqual(row["differences"], [])
                self.assertEqual({c["status"] for c in row["stages"].values()}, {"not_run"})
        self.assertEqual(code, cg.EXIT_PARTIAL, "partial coverage: neither a pass nor a failure")
        budget = report["time_budget"]
        self.assertTrue(budget["exhausted"])
        self.assertEqual(budget["not_run"], report["outcome_counts"]["not_run"])
        self.assertIn("NOT", ex.render_markdown(report).upper())

    def test_candidates_before_the_budget_ran_and_later_ones_did_not(self):
        clock = FakeClock()

        def slow(manifest, *a, **k):
            clock.now += 10.0
            return {
                "judgment": {"outcome": "no_difference", "differences": []},
                "executions": {
                    "native": 1,
                    "parent_native": 0,
                    "native_pixels": 0,
                    "tavotto": 1,
                    "basis": "x",
                },
            }

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ex.time, "monotonic", clock),
            mock.patch.object(ex, "MIN_ATTEMPT_SECONDS", 0.0),
        ):
            code, report = self.run_explore(tmp, 25.0, fake=slow)
        outcomes = [r["outcome"] for r in report["candidates"] if r["outcome"] != "static_only"]
        self.assertEqual(
            outcomes.count("no_difference"), 3, "t=0, 10, 20 start inside the 25 s budget"
        )
        self.assertEqual(set(outcomes[3:]), {"not_run"}, "once spent, it stays spent")
        self.assertEqual(code, cg.EXIT_PARTIAL)

    def test_no_budget_means_no_budget_block_and_no_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=7,
                out=str(tmp),
                limit=None,
                parent=None,
                static_only=True,
                candidates=None,
                families="entry_form",
                confirm_differences=0,
                expected_sha=None,
                timeout=200,
                shard=None,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                ex.explore(args)
            (path,) = Path(tmp).glob("*/reports/explore-report.json")
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("time_budget", report)

    def test_confirmation_reruns_stop_when_the_deadline_has_passed(self):
        import time

        row = {"candidate_id": "x", "outcome": "difference_found", "differences": [{"kind": "k"}]}
        with mock.patch.object(
            ex, "run_one", side_effect=AssertionError("no rerun after the deadline")
        ):
            ex.confirm_differences(
                [row], {}, Namespace(confirm_differences=3), Path("."), time.monotonic() - 1
            )
        self.assertIsNone(row["confirmation"], "unconfirmed, so it can never raise an alert")

    def test_max_seconds_must_be_a_finite_positive_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("0", "-1", "nan", "inf", "x", ""):
                code, _, err = run_cli(
                    "explore", "--seed", "1", "--static-only", "--out", tmp, "--max-seconds", bad
                )
                self.assertEqual(code, 2, bad)
                self.assertIn(
                    "--max-seconds", err, f"must be rejected by the argument parser: {bad!r}"
                )

    def test_a_spent_budget_is_visible_in_the_merged_sweep_and_never_a_finding(self):
        reports = shard_reports()
        with_budget_spent(reports[1])
        info = sh.check_set(reports)
        self.assertTrue(info["complete"])
        self.assertFalse(
            info["fully_executed"], "all shards present is not all candidates executed"
        )
        self.assertEqual(info["not_run"], 2)
        self.assertEqual(info["budget_exhausted_shards"], [2])
        self.assertTrue(sh.check_set(shard_reports())["fully_executed"])
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for r in reports:
                p = Path(tmp) / r["run_id"] / "reports" / "explore-report.json"
                p.parent.mkdir(parents=True)
                p.write_text(json.dumps(r), encoding="utf-8")
                paths.append(str(p))
            md, js, tj = Path(tmp) / "w.md", Path(tmp) / "w.json", Path(tmp) / "t.json"
            tmd = Path(tmp) / "t.md"
            self.assertEqual(
                run_cli("summarize", *paths, "--json-out", str(js), "--markdown-out", str(md))[0], 0
            )
            self.assertEqual(
                run_cli("triage", *paths, "--out", str(tj), "--markdown-out", str(tmd))[0], 0
            )
            text = md.read_text(encoding="utf-8")
            self.assertIn("time budget exhausted in shard(s) [2]", text)
            self.assertIn("NOT RUN", text)
            self.assertIn("not run", tmd.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(tj.read_text())["summary"]["findings"], 0, "not_run is not a finding"
            )
            sample = json.loads(js.read_text())["trend"]["sample"]
        total = sum(len(r["candidates"]) for r in reports)
        static = sum(1 for r in reports for c in r["candidates"] if c["outcome"] == "static_only")
        self.assertEqual(
            sample["explore_candidates_executed"], total - static - 2, "not_run is not executed"
        )

    def test_report_and_rows_must_agree_about_the_budget(self):
        hidden = shard_reports()
        with_budget_spent(hidden[0])
        del hidden[0]["time_budget"]
        with self.assertRaises(cg.EvidenceError):
            sh.check_set(hidden)
        lying = shard_reports()
        with_budget_spent(lying[0])
        lying[0]["time_budget"]["not_run"] = 1
        with self.assertRaises(cg.EvidenceError):
            sh.check_set(lying)
        claims = shard_reports()
        claims[0]["time_budget"] = {"max_seconds": 1.0, "exhausted": True, "not_run": 0}
        with self.assertRaises(cg.EvidenceError):
            sh.check_set(claims)


class BudgetCapsEverySubprocess(unittest.TestCase):
    """Codex P1: with `--max-seconds`, no adapter process may outlive the budget by its own timeout.

    `_adapter_run` (the only place a child is launched) is stubbed and time is a fake clock, so the
    workflow's real numbers are used: budget 2700 s, `--timeout` 240 s, floor 30 s.
    """

    PARENT = "shape_oo_savefig"
    BUDGET = 2700.0

    def explore(self, tmp, budget, adapter, clock, setup_seconds=0.0):
        args = Namespace(
            seed=7,
            out=str(tmp),
            limit=None,
            parent=self.PARENT,
            static_only=False,
            candidates=None,
            families="import_alias",
            confirm_differences=0,
            expected_sha=None,
            timeout=240.0,
            shard=None,
            max_seconds=budget,
        )
        real_generate = xt.generate

        def slow_generate(*a, **k):  # "the candidate starts setup_seconds into the budget"
            clock.now += setup_seconds
            return real_generate(*a, **k)

        start = clock.now
        with (
            mock.patch.object(ex.time, "monotonic", clock),
            mock.patch.object(ex.XT, "generate", slow_generate),
            mock.patch.object(ex, "dirty_execution_code", return_value=[]),
            mock.patch.object(ex, "_adapter_run", side_effect=adapter),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = ex.explore(args)
        (path,) = Path(tmp).glob("*/reports/explore-report.json")
        return code, json.loads(path.read_text(encoding="utf-8")), clock.now - start

    def cached(self, tmp):
        return [k for k in ex._PARENT_SNAPSHOTS if k[0].startswith(str(Path(tmp).resolve()))]

    def test_near_the_deadline_with_an_uncached_parent_both_calls_are_capped(self):
        clock, calls = FakeClock(), []
        with tempfile.TemporaryDirectory() as tmp:
            code, report, elapsed = self.explore(
                tmp, self.BUDGET, hang_until_cut(clock, calls), clock, setup_seconds=2650.0
            )
            cached = self.cached(tmp)
        self.assertEqual(
            calls, [("parent_snapshot", 50.0)], "50 s left: that is all the child gets, not 240"
        )
        self.assertLessEqual(elapsed, self.BUDGET + ex.OVERRUN_BOUND_SECONDS)
        self.assertLessEqual(
            elapsed, self.BUDGET, "with a simulated instant kill the clock stops at the deadline"
        )
        self.assertEqual(cached, [], "a snapshot cut off by the budget is never cached")
        (row,) = [r for r in report["candidates"] if r["outcome"] != "static_only"]
        self.assertEqual(row["outcome"], "not_run")
        self.assertIn("time budget exhausted", row["note"])
        self.assertEqual(row["differences"], [])
        self.assertEqual(report["time_budget"]["not_run"], 1)
        self.assertEqual(code, cg.EXIT_PARTIAL)

    def test_a_slow_snapshot_leaves_the_candidate_only_what_is_left(self):
        clock, calls = FakeClock(), []
        good = {"snapshots": {"s": {"a.b": 1}}}
        with tempfile.TemporaryDirectory() as tmp:
            code, report, elapsed = self.explore(
                tmp,
                self.BUDGET,
                hang_until_cut(clock, calls, then=(60.0, good)),
                clock,
                setup_seconds=2600.0,
            )
            cached = self.cached(tmp)
        self.assertEqual(calls, [("parent_snapshot", 100.0), ("candidate", 40.0)])
        self.assertLessEqual(elapsed, self.BUDGET)
        self.assertEqual(len(cached), 1, "a completed snapshot is kept")
        (row,) = [r for r in report["candidates"] if r["outcome"] != "static_only"]
        self.assertEqual(
            row["outcome"], "not_run", "cut by the budget: not unknown, not a difference"
        )
        self.assertEqual(code, cg.EXIT_PARTIAL)

    def test_the_candidates_own_timeout_is_still_a_candidate_verdict(self):
        """Budget plentiful, so nothing is capped: a hang stays `unknown` / adapter_timeout."""
        clock, calls = FakeClock(), []
        good = {"snapshots": {"s": {"a.b": 1}}}

        def own_timeout(request, hashes, evidence, timeout):
            calls.append(timeout)
            clock.now += 1.0
            if request.get("mode") == "parent_snapshot":
                return good
            raise cg.AdapterTimeout("stub: hung")

        with tempfile.TemporaryDirectory() as tmp:
            _, report, _ = self.explore(tmp, self.BUDGET, own_timeout, clock)
        self.assertEqual(calls, [240.0, 240.0], "the budget did not shorten anything")
        (row,) = [r for r in report["candidates"] if r["outcome"] != "static_only"]
        self.assertEqual(row["outcome"], "unknown")
        self.assertIn("adapter_timeout", row["note"])
        self.assertFalse(report["time_budget"]["exhausted"])

    def test_without_a_budget_the_timeout_is_passed_through_untouched(self):
        clock, calls = FakeClock(), []

        def seen(request, hashes, evidence, timeout):
            calls.append(timeout)
            raise cg.AdapterTimeout("stub")

        with tempfile.TemporaryDirectory() as tmp:
            _, report, _ = self.explore(tmp, None, seen, clock)
        self.assertEqual(calls, [240.0, 240.0])
        self.assertNotIn("time_budget", report)

    def test_below_the_floor_the_candidate_is_not_started_at_all(self):
        clock, calls = FakeClock(), []
        with tempfile.TemporaryDirectory() as tmp:
            code, report, _ = self.explore(
                tmp, self.BUDGET, hang_until_cut(clock, calls), clock, setup_seconds=2700.0 - 20.0
            )
        self.assertEqual(calls, [], "20 s < 30 s floor: no child is started")
        self.assertEqual(report["time_budget"]["not_run"], 1)
        self.assertEqual(code, cg.EXIT_PARTIAL)

    def test_each_layer_enforces_the_floor_on_its_own(self):
        """explore's loop, `run_one` and the confirmation loop each refuse to start under the floor."""
        parent = xt.load_parent("compat_engine", self.PARENT)
        manifest, _ = xt.build(parent, ("import_alias",), 7)
        clock = FakeClock()
        low = clock.now + 20.0  # 20 s left < 30 s floor
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ex.time, "monotonic", clock),
            mock.patch.object(ex, "_adapter_run", side_effect=AssertionError("no child may start")),
        ):
            record = ex.run_one(manifest, 7, Path(tmp), 240.0, deadline=low)
            self.assertEqual(record["judgment"]["outcome"], "not_run")
            self.assertEqual(record["executions"]["basis"], "not_executed")
            row = {
                "candidate_id": manifest["candidate_id"],
                "outcome": "difference_found",
                "chain": ["import_alias"],
                "differences": [{"kind": "k"}],
            }
            with mock.patch.object(
                ex, "run_one", side_effect=AssertionError("no rerun under the floor")
            ):
                ex.confirm_differences(
                    [row],
                    {manifest["candidate_id"]: manifest},
                    Namespace(confirm_differences=3, seed=7, timeout=240.0),
                    Path(tmp),
                    low,
                )
            self.assertIsNone(row["confirmation"])
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=7,
                out=tmp,
                limit=None,
                parent=self.PARENT,
                static_only=False,
                candidates=None,
                families="import_alias",
                confirm_differences=0,
                expected_sha=None,
                timeout=240.0,
                shard=None,
                max_seconds=self.BUDGET,
            )
            real = xt.generate

            def late(*a, **k):
                clock.now += self.BUDGET - 20.0
                return real(*a, **k)

            with (
                mock.patch.object(ex.time, "monotonic", clock),
                mock.patch.object(ex.XT, "generate", late),
                mock.patch.object(ex, "dirty_execution_code", return_value=[]),
                mock.patch.object(
                    ex, "run_one", side_effect=AssertionError("loop must not call run_one")
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(ex.explore(args), cg.EXIT_PARTIAL)

    def test_a_confirmation_that_starts_before_the_deadline_is_cut_at_it(self):
        parent = xt.load_parent("compat_engine", self.PARENT)
        manifest, _ = xt.build(parent, ("import_alias",), 7)
        cid = manifest["candidate_id"]
        row = {
            "candidate_id": cid,
            "outcome": "difference_found",
            "chain": ["import_alias"],
            "differences": [ex._diff("zero_figures", "s", next(iter(ex.TX.CATEGORIES)), {})],
        }
        clock, calls = FakeClock(), []
        stub = hang_until_cut(clock, calls, then=(0.0, {"snapshots": {"s": {"a.b": 1}}}))
        deadline = clock.now + 50.0
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(ex.time, "monotonic", clock),
            mock.patch.object(ex, "_adapter_run", side_effect=stub),
        ):
            ex.confirm_differences(
                [row],
                {cid: manifest},
                Namespace(confirm_differences=3, seed=7, timeout=240.0),
                Path(tmp),
                deadline,
            )
        self.assertTrue(calls and all(t <= 50.0 for _, t in calls), calls)
        self.assertLessEqual(clock.now, deadline)
        self.assertIsNone(row["confirmation"], "cut by the budget: unconfirmed, so never an alert")

    def test_the_overrun_bound_is_derived_and_fits_the_weekly_step(self):
        smoke = cg.package_smoke
        self.assertEqual(
            ex.CLEANUP_GRACE_SECONDS, smoke.TERMINATE_GRACE_S + 2 * smoke.GROUP_GONE_WAIT_S
        )
        self.assertEqual(
            ex.OVERRUN_BOUND_SECONDS, ex.CLEANUP_GRACE_SECONDS + ex.FIXED_OVERHEAD_SECONDS
        )
        self.assertGreaterEqual(ex.MIN_ATTEMPT_SECONDS, 30.0)

    def test_a_child_that_ignores_sigterm_is_gone_within_the_cleanup_grace(self):
        """The 16 s in the bound is not a guess: SIGTERM-proof child, 0.5 s timeout, real kill."""
        import os
        import time

        if sys.platform == "win32":
            self.skipTest("POSIX process-group semantics")
        command = [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            t0 = time.monotonic()
            result = cg.package_smoke.run_isolated_command(
                command, env=dict(os.environ), cwd=work, evidence=work, timeout=0.5
            )
            wall = time.monotonic() - t0
        self.assertTrue(result["timed_out"])
        self.assertIsNot(result["cleanup"].get("group_gone"), False)
        self.assertLess(wall, 0.5 + ex.CLEANUP_GRACE_SECONDS)


if __name__ == "__main__":
    unittest.main()
