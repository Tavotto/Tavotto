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
    "G6-bytesio-close-zero-figures": 749,
    # G4 / G5：fix_pr 是诊断修复（#1297 / #1299）；默认档的行为已由 #1846 修好，记录保留为「用户选了沙盒」档的钉
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
                "G6-bytesio-close-zero-figures",
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


def _tavotto(project: Path, workdir_mode: str | None = None) -> dict:
    code = (
        "import json, sys; from pathlib import Path; import compat_explore as ex; "
        "print(json.dumps(ex.tavotto_observe(Path(sys.argv[1]), 'repro.py', "
        "workdir_mode=sys.argv[2] or None)))"
    )
    work = project.parent / "iso"
    work.mkdir()
    evidence = project.parent / "evidence"
    evidence.mkdir()
    # 进程树清理（Windows 用 Job Object）：引擎 worker 可能比直接子进程活得久，还占着沙盒目录，
    # 临时目录就删不掉（WinError 32）
    process = package_smoke.run_isolated_command(
        [sys.executable, "-c", code, str(project), workdir_mode or ""],
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
    G4 / G5: the harness observes through `pool.build`, i.e. the product's own path, so the default
    (undecided project) tier pins "draws, no difference" since #1846; the explicit sandbox tier
    (`workdir_mode="sandbox"`, the user chose it) keeps pinning "the difference is still there".
    G6 was an unfixed gap pinned as "difference is still there"; #749 closed it and the pin is now a
    regression case (like G1 / G2).
    """

    def run_family(self, family_id, workdir_mode=None):
        record = next(r for r in records() if r["id"] == family_id)
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, record)
            native = _native(project, record["repro"]["entry"])
            tavotto = _tavotto(project, workdir_mode)
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

    def test_g6_minimal_repro_is_fixed(self):
        """G6 已由 #749 修复：savefig 到 io.BytesIO 再 close 的那张图被补回来，原生 / Tavotto 无差异。"""
        verdict, native, tavotto = self.run_family("G6-bytesio-close-zero-figures")
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(len(tavotto["stems"]), 1)
        self.assertEqual(verdict["outcome"], "no_difference", verdict)

    # pyarrow 只在 Guardian job 里装（产品依赖不含它）；别的 job 跑不出原生那张图
    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "pyarrow not installed")
    def test_g4_minimal_repro_draws_on_the_product_path(self):
        """G4 在用户真实走到的路径上已经出图（#1846，ADR 0047 修订）：没决定过工作目录的项目，沙盒里
        C++ 读取器读不到同目录数据、错误带 workdir_hint，产品静默改在脚本目录里重跑，第一次就出图。
        观察走 `pool.build`，所以 Guardian 量到的就是这条路径，原生 / Tavotto 无差异。"""
        verdict, native, tavotto = self.run_family("G4-cwd-relative-reader-zero-figures")
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(len(tavotto["stems"]), 1, tavotto)
        self.assertEqual(verdict["outcome"], "no_difference", verdict)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "pyarrow not installed")
    def test_g4_with_the_sandbox_chosen_still_differs_by_design(self):
        """用户自己选了沙盒是一个决定，运行时证据不覆盖它（ADR 0047 护栏）：原生出图、Tavotto 零图并带
        script_error + workdir_hint（「在脚本目录里运行」入口，#1297 / #1299）。这是显式可选档，不是默认。"""
        verdict, native, tavotto = self.run_family(
            "G4-cwd-relative-reader-zero-figures", workdir_mode="sandbox"
        )
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertEqual(tavotto["stems"], {})
        self.assertEqual((tavotto.get("error") or {}).get("code"), "script_error", tavotto)
        self.assertTrue(tavotto["error"]["workdir_hint"], tavotto)
        self.assertNotEqual(verdict["outcome"], "no_difference", verdict)

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "pyarrow not installed")
    def test_g4_data_that_is_really_missing_is_still_reported(self):
        """反证：数据哪儿都没有时，产品不能靠「改目录重跑」假装成功——原生和 Tavotto 都出不了图，
        Tavotto 如实报错，没有凭空出图。"""
        record = next(r for r in records() if r["id"] == "G4-cwd-relative-reader-zero-figures")
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, record)
            for data in project.glob("*.parquet"):
                data.unlink()
            native = _native(project, record["repro"]["entry"])
            tavotto = _tavotto(project)
        self.assertNotEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertEqual(tavotto["stems"], {})
        self.assertIsNotNone(tavotto.get("error"), tavotto)

    def test_g5_minimal_repro_is_rerun_in_the_script_dir_on_the_product_path(self):
        """G5：沙盒里 cwd 相对 glob 扫空、脚本照样画出一张空图——没决定过工作目录的项目里，产品看到
        「glob 回空而脚本目录里有文件」就静默改在脚本目录里重跑（#1846），所以最终那张图是有数据的，
        观察里不再带「在脚本目录里运行」的提示（提示只留给用户选了沙盒的项目，见下一条）。"""
        verdict, native, tavotto = self.run_family("G5-sandbox-empty-glob-empty-figure")
        self.assertEqual((native["exit_status"], native["figure_count"]), (0, 1), native)
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(list(tavotto["stems"]), ["shape_custom_entry"])
        self.assertFalse(tavotto["workdir_hint"], tavotto)
        self.assertEqual(verdict["differences"], [], verdict)

    def test_g5_with_the_sandbox_chosen_keeps_the_empty_figure_and_the_hint(self):
        """用户选了沙盒：图仍是没数据的，产品给的是「在脚本目录里运行」入口（#1299）。显式可选档。"""
        verdict, native, tavotto = self.run_family(
            "G5-sandbox-empty-glob-empty-figure", workdir_mode="sandbox"
        )
        self.assertIsNone(tavotto.get("error"), tavotto)
        self.assertEqual(list(tavotto["stems"]), ["shape_custom_entry"])
        self.assertTrue(tavotto["workdir_hint"], tavotto)

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
