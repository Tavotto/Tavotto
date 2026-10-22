"""Failure-family records (tests/compat_guardian/families) and their minimized reproductions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_guardian_expectations as ex_decl  # noqa: E402
import compat_minimize as cmz  # noqa: E402
import package_smoke  # noqa: E402

FAMILIES = cg.ROOT / "tests/compat_guardian/families"
SCHEMA = cg.ROOT / "tests/compat_guardian/families.schema.json"
DRIVER = Path(cg.HERE) / "compat_driver.py"


def reference_resolves(ref: str, root: Path | None = None) -> bool:
    """`path.py` (the file must exist) or `path.py::Class::test_x[param]` (the whole node must be
    defined, found level by level through the AST: comments, strings and a same-named test in
    another class do not count)."""
    path, _, node = ref.partition("::")
    target = (root or cg.ROOT) / path
    if not target.is_file():
        return False
    node = node.split("[")[0]
    return not node or ex_decl._defines(target, node)


def records() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FAMILIES.glob("*.json"))]


#: 已修复的族 → 修复 PR 号（家族记录的 fix_pr 必须与之一致）
FIXED = {
    "G1-show-close-zero-figures": 1138,
    "G2-same-path-multi-figure": 1141,
    # G4 / G5：行为按 ADR 0047 / 0084 有意保留，fix_pr 是诊断修复（#1297 / #1299），不是行为修复
    "G4-cwd-relative-reader-zero-figures": 1297,
    "G5-sandbox-empty-glob-empty-figure": 1299,
}


class Records(unittest.TestCase):
    def test_there_are_records_for_the_audited_gaps_and_the_weekly_sweep_families(self):
        self.assertEqual(
            [r["id"] for r in records()],
            [
                "G1-show-close-zero-figures",
                "G2-same-path-multi-figure",
                "G4-cwd-relative-reader-zero-figures",
                "G5-sandbox-empty-glob-empty-figure",
            ],
        )

    def test_records_follow_the_schema_and_say_what_is_not_done_yet(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for record in records():
            cg.schema_check(record, schema)
            self.assertFalse(record["in_corpus"], "not in the corpus until a maintainer adds it")
            self.assertFalse(record["promoted"])
            # 修好的族记下修复 PR（G1 → #1138）；没修的仍是 None——两种都不得自动 promote
            self.assertEqual(record["fix_pr"], FIXED.get(record["id"]))
            self.assertTrue(record["environments"]["unverified"])
            self.assertIn(record["category"], __import__("compat_guardian_taxonomy").CATEGORIES)

    def test_repro_scripts_exist_and_match_their_recorded_hash(self):
        for record in records():
            path = cg.ROOT / record["repro"]["script"]
            self.assertTrue(path.is_file(), path)
            # 记录的是仓库里的 LF 字节；Windows checkout（core.autocrlf）会写成 CRLF
            committed = path.read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(committed).hexdigest(), record["repro"]["sha256"])
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            # the recorded `after` metrics are recomputed from the committed bytes, never trusted
            # from the JSON alone (Codex #1136 r4312234096)
            script = Path(record["repro"]["script"]).name
            self.assertEqual(record["repro"]["after"], cmz.measure({script: committed}, script))
            self.assertLess(
                record["repro"]["after"]["statements"], record["repro"]["before"]["statements"]
            )
            self.assertEqual(
                record["repro"]["reproducibility"]["confirmed"],
                record["repro"]["reproducibility"]["total"],
            )

    def test_every_listed_regression_test_exists(self):
        for record in records():
            for ref in record["key_regression_tests"]:
                self.assertTrue(reference_resolves(ref), ref)

    def test_reference_resolution_checks_the_whole_node_not_a_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_x.py").write_text(
                "# test_commented_only is mentioned here\n"
                "NAME = 'test_in_a_string'\n"
                "class Right:\n"
                "    def test_x(self): ...\n"
                "def test_top(): ...\n",
                encoding="utf-8",
            )
            ok = (
                "test_x.py",
                "test_x.py::Right::test_x",
                "test_x.py::test_top",
                "test_x.py::Right",
            )
            for ref in ok:
                self.assertTrue(reference_resolves(ref, root), ref)
            bad = (
                "test_x.py::WrongClass::test_x",  # test_x exists, but not under WrongClass
                "test_x.py::test_x",  # a method, not a module-level function
                "test_x.py::test_commented_only",
                "test_x.py::test_in_a_string",
                "test_x.py::Right::test_missing",
                "missing.py",  # a file-only reference still needs the file
                "missing.py::test_top",
            )
            for ref in bad:
                self.assertFalse(reference_resolves(ref, root), ref)


def _project(tmp: str, record: dict) -> Path:
    project = Path(tmp) / "project"
    project.mkdir()
    script = cg.ROOT / record["repro"]["script"]
    (project / "repro.py").write_bytes(script.read_bytes())
    # 记录里的数据文件放在脚本旁（保持相对子目录），脚本用 cwd 相对路径读它们
    for data in record["repro"].get("data_files", []):
        source = cg.ROOT / data
        target = project / source.relative_to(script.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return project


def _native(project: Path, entry: str) -> dict:
    request = project.parent / "req.json"
    request.write_text(
        json.dumps({"project": str(project), "script": str(project / "repro.py"), "entry": entry}),
        encoding="utf-8",
    )
    done = subprocess.run(
        [sys.executable, str(DRIVER), "--mode", "native_ref", "--request", str(request)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def _tavotto(project: Path) -> dict:
    code = "import json, sys; from pathlib import Path; import compat_explore as ex; print(json.dumps(ex.tavotto_observe(Path(sys.argv[1]), 'repro.py')))"
    work = project.parent / "iso"
    work.mkdir()
    evidence = project.parent / "evidence"
    evidence.mkdir()
    # 进程树清理（Windows 用 Job Object）：引擎 worker 可能比直接子进程活得久，还占着沙盒目录，
    # 临时目录就删不掉（WinError 32）
    process = package_smoke.run_isolated_command(
        [sys.executable, "-c", code, str(project)],
        env=cg.isolated_env(work),
        cwd=project.parent,
        evidence=evidence,
        timeout=180,
    )
    out = (evidence / "stdout.txt").read_text(errors="replace")
    assert process["exit_code"] == 0, (evidence / "stderr.txt").read_text(errors="replace")
    return json.loads(out.strip().splitlines()[-1])


class RealRepro(unittest.TestCase):
    """The minimal scripts against the current product code.

    An unfixed gap must still show; when a gap is fixed its test goes red on purpose and is
    turned into a regression case here (G1 fixed by #1138, G2 by #1141).
    G4 / G5 keep their behavior on purpose (ADR 0047 / 0084; only the diagnosis was fixed), so they
    pin "the difference is still there".
    """

    def run_family(self, family_id):
        record = next(r for r in records() if r["id"] == family_id)
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, record)
            native = _native(project, record["repro"]["entry"])
            tavotto = _tavotto(project)
        return ex.compare("axis_changing", native, tavotto, None), native, tavotto

    def test_g1_minimal_repro_is_fixed(self):
        """G1 已由 #1138 修复：show 后 close 的那张图被捕获，原生 / Tavotto 无差异。"""
        verdict, native, tavotto = self.run_family("G1-show-close-zero-figures")
        self.assertEqual((native["figures_shown"], native["savefig_calls"]), (1, 0))
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(len(tavotto["stems"]), 1)
        self.assertEqual(verdict["outcome"], "no_difference", verdict)

    def test_g2_minimal_repro_is_fixed(self):
        """G2 已由 #1141 修复：同一路径循环写多张图，Tavotto 捕获的是最后写入的那张，原生 / Tavotto 无差异。"""
        verdict, native, tavotto = self.run_family("G2-same-path-multi-figure")
        self.assertEqual(native["same_path_distinct_figures"], ["shape_oo.pdf"])
        self.assertEqual(verdict["outcome"], "no_difference", verdict)

    # pyarrow 只在 Guardian job 里装（产品依赖不含它）；别的 job 跑不出原生那张图
    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "pyarrow not installed")
    def test_g4_minimal_repro_still_differs_by_design(self):
        """G4 的行为按 ADR 0047 / 0084 有意保留，诊断由 #1297 / #1299 修复：沙盒 cwd 下 cwd 相对路径经
        C++ 读取器读不到同目录数据，原生出图、Tavotto 零图并带 script_error。若哪天加了根治开关的默认
        （在脚本目录里运行），这条会红，届时改成回归用例。"""
        verdict, native, tavotto = self.run_family("G4-cwd-relative-reader-zero-figures")
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertEqual(tavotto["stems"], {})
        self.assertEqual((tavotto.get("error") or {}).get("code"), "script_error", tavotto)
        self.assertNotEqual(verdict["outcome"], "no_difference", verdict)

    def test_g5_minimal_repro_still_draws_a_figure_without_data_by_design(self):
        """G5 的行为按 ADR 0047 / 0084 有意保留，诊断由 #1299 修复：沙盒里 cwd 相对 glob 扫空，Tavotto
        出了图但图里没有数据，原生有数据（差异在 fidelity / structure 层，见下一条）。若哪天加了根治开关的默认（在脚本目录里运行），这条会红，
        届时改成回归用例。引导本身（workdir_hint）的断言在 test_runtime_probe_hint.py。"""
        verdict, native, tavotto = self.run_family("G5-sandbox-empty-glob-empty-figure")
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(list(tavotto["stems"]), ["shape_custom_entry"])  # 出了图
        # explore 的 compare 只比图数 / 标题，看不见「图里没数据」（那由 fidelity / structure 层判，记
        # pixel_mismatch）：所以这里不对 verdict 下断言，「图是空的」由下一条的运行时证据证明
        self.assertEqual(verdict["differences"], [], verdict)

    def test_g5_sandbox_run_reports_the_empty_glob_and_the_script_dir_hint(self):
        """G5 的图为什么是空的：沙盒里 `data/cg_data_*.csv` 回空，而脚本目录里找得到——#1299 的运行时
        观察把它记下来，并给出「在脚本目录里运行」引导（推荐 project）。"""
        from tavotto.engine import pool as engine_pool, probe as engine_probe, workdir

        record = next(r for r in records() if r["id"] == "G5-sandbox-empty-glob-empty-figure")
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, record)
            workdir.set_mode(project, workdir.MODE_SANDBOX)
            try:
                result = engine_probe.probe(
                    str(project), "repro.py", entries=[record["repro"]["entry"]]
                )
            finally:
                engine_pool.shutdown_all(str(project), wait=True)
                workdir.forget(project)
        self.assertIsNone(result["error"], result)
        self.assertEqual(result["stems"], ["shape_custom_entry"])
        hint = result["workdir_hint"]
        self.assertEqual(hint["probes"], ["data/cg_data_*.csv"])
        self.assertEqual(hint["reason"], workdir.REASON_SCRIPT_DIR_EVIDENCE)
        self.assertEqual(hint["recommended"], workdir.MODE_PROJECT)


if __name__ == "__main__":
    unittest.main()
