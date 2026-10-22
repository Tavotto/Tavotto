"""CG-07 data_source 家族：读出的值 / 逐字节确定性 / 结构去重 / 缺依赖 / 真实执行。"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/ci"
sys.path.insert(0, str(SCRIPTS))

import compat_explore as ex  # noqa: E402
import compat_explore_data as data  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402

NAMES = [f.name for f in data.FORMATS]
TEXT = [f.name for f in data.FORMATS if f.text]


def fake_parent(values, case="fake_data") -> xt.Parent:
    text = (
        "import matplotlib.pyplot as plt\n\n\ndef main():\n    fig, ax = plt.subplots()\n"
        f"    ax.plot({values!r})\n    ax.set_title('t')\n    fig.savefig('out.png')\n"
    )
    return xt.Parent(
        "compat_engine",
        case,
        "script_shapes",
        {"s.py": text.encode()},
        "s.py",
        "main",
        True,
        {"files": {}},
    )


def build_or_skip(case, parent, name, seed):
    fmt = data.BY_NAME[name]
    if data.missing(fmt):
        case.skipTest(f"{name}: missing {data.missing(fmt)} (installed by the Guardian workflow)")
    return xt.build(parent, (name,), seed)


def lacks(skipped: dict, name: str) -> bool:
    """缺生成 / 读取依赖的格式记下来跳过；其余格式照常断言，最后由 skip_missing 报「缺哪个包」。"""
    absent = data.missing(data.BY_NAME[name])
    if absent:
        skipped[name] = absent
    return bool(absent)


def skip_missing(case, skipped: dict):
    if skipped:
        case.skipTest(
            "missing optional packages (installed by the Guardian workflow): "
            + "; ".join(f"{n}: {', '.join(m)}" for n, m in sorted(skipped.items()))
        )


def evaluate(parent, files, params):
    """在临时目录里把候选的读文件表达式原样求值（cwd = 脚本目录，`__file__` 指向脚本）。"""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, blob in files.items():
            dest = Path(tmp) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        scope = {"__file__": str(Path(tmp) / parent.script)}
        for node in ast.parse(files[parent.script].decode()).body:
            is_import = isinstance(node, (ast.Import, ast.ImportFrom)) and all(
                (a.asname or "").startswith("_cg_") for a in node.names
            )
            if is_import or (isinstance(node, ast.FunctionDef) and node.name.startswith("_cg_")):
                exec(compile(ast.Module([node], []), "cg", "exec"), scope)
        old = os.getcwd()
        os.chdir(tmp)
        try:
            return list(eval(params["read_expr"], scope))
        finally:
            os.chdir(old)


class Registration(unittest.TestCase):
    def test_all_formats_are_preserving_data_source_transforms(self):
        for name in NAMES:
            t = xt.BY_NAME[name]
            self.assertEqual((t.family, t.axis, t.preserving), ("data_source", "data_source", True))
        self.assertEqual(len({t.order for t in xt.TRANSFORMS}), len(xt.TRANSFORMS))

    def test_same_axis_transforms_never_pair(self):
        parent = fake_parent([1, 2, 3])
        for name in NAMES:
            with self.assertRaises(xt.cg.EvidenceError):
                xt.build(parent, ("inline_to_file", name), 1)
        for chain in xt.chains_for(parent, 3):
            axes = [xt.BY_NAME[n].axis for n in chain]
            self.assertEqual(len(axes), len(set(axes)))


class Values(unittest.TestCase):
    def test_values_and_types_read_back_exactly(self):
        skipped: dict = {}
        for values in ([1, 2, 3, 5], [1.0, 2.0, 3.0], [0.1, 2.5e-7, 3.0, 1e20], [1, 2.5, 3]):
            parent = fake_parent(values)
            floats = any(type(v) is float for v in values)
            for name in NAMES:
                if lacks(skipped, name):
                    continue
                for seed in range(6):
                    manifest, files = xt.build(parent, (name,), seed)
                    got = evaluate(parent, files, manifest["chain"][0]["params"])
                    self.assertEqual(got, values, (name, seed))
                    kinds = {type(x).__name__.rstrip("0123456789_") for x in got}
                    self.assertLessEqual(
                        kinds, {"float"} if floats else {"int"}, (name, seed, kinds)
                    )
        skip_missing(self, skipped)

    def test_glob_is_split_in_order_into_data_dir(self):
        parent = fake_parent(list(range(7)))
        manifest, files = build_or_skip(self, parent, "glob_dir", 4)
        parts = sorted(p for p in files if p.startswith("data/"))
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual(parts, manifest["chain"][0]["params"]["data_files"])
        self.assertIn("glob", files["s.py"].decode())

    def test_seeds_cover_every_path_style_and_encoding(self):
        parent = fake_parent([1, 2, 3])
        styles, encodings, names = set(), set(), set()
        for seed in range(60):
            params = xt.build(parent, ("read_tsv_stdlib",), seed)[0]["chain"][0]["params"]
            styles.add(params["path_style"])
            encodings.add(params["encoding"])
            names.add(params["data_files"][0].rsplit("/", 1)[-1])
        self.assertEqual(styles, set(data.PATH_STYLES))
        self.assertEqual(encodings, {e for e, _ in data.ENCODINGS})
        self.assertIn("实验数据.tsv", names)

    def test_text_encodings_really_change_the_bytes(self):
        parent = fake_parent([1, 2, 3])
        seen = {}
        for seed in range(60):
            manifest, files = xt.build(parent, ("read_csv_numpy",), seed)
            params = manifest["chain"][0]["params"]
            seen[params["encoding"]] = files[params["data_files"][0]]
        self.assertTrue(seen["utf-8-sig"].startswith(b"\xef\xbb\xbf"))
        self.assertNotEqual(seen["gbk"], seen["utf-8"])
        self.assertEqual(seen["gbk"].decode("gbk").splitlines()[0], "序号,y")


class Determinism(unittest.TestCase):
    CODE = (
        "import sys, json, hashlib\nsys.path.insert(0, sys.argv[1])\n"
        "import compat_explore_transforms as xt, compat_explore_data as d\n"
        "p = xt.load_parent('compat_engine', 'shape_oo_savefig')\nout = {}\n"
        "for f in d.FORMATS:\n"
        "    if d.missing(f): continue\n"
        "    m, files = xt.build(p, (f.name,), 5)\n"
        "    out[f.name] = [m['candidate_id'], {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}]\n"
        "print(json.dumps(out, sort_keys=True))\n"
    )

    def run_once(self, hashseed):
        env = {
            **os.environ,
            "PYTHONHASHSEED": hashseed,
            "PYTHONPATH": str(SCRIPTS.parents[1] / "src"),
        }
        done = subprocess.run(
            [sys.executable, "-c", self.CODE, str(SCRIPTS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_every_format_is_byte_identical_across_processes(self):
        first = self.run_once("1")
        second = self.run_once("2")
        self.assertEqual(first, second)
        # 可用格式随环境变（backend-fast 不装 openpyxl / pyarrow），但每一个可用的都必须出现；
        # 标准库那几种任何环境都在，所以这个集合不会空
        available = {f.name for f in data.FORMATS if not data.missing(f)}
        self.assertTrue({"read_tsv_stdlib", "glob_dir"} <= available)
        self.assertEqual(set(json.loads(first)), available)

    def test_xlsx_does_not_carry_the_write_time(self):
        if data.missing(data.BY_NAME["read_excel"]):
            self.skipTest("openpyxl not installed")
        import time

        a = data._write_xlsx([1, 2, 3], None)
        time.sleep(1.1)
        self.assertEqual(a, data._write_xlsx([1, 2, 3], None))

    def test_binary_containers_pin_what_varies_between_runs_and_pythons(self):
        import io
        import zipfile

        if not data.missing(data.BY_NAME["read_pickle"]):
            self.assertTrue(data._write_pickle([1, 2], None).startswith(b"\x80\x04"))
        if not data.missing(data.BY_NAME["read_excel"]):
            with zipfile.ZipFile(io.BytesIO(data._write_xlsx([1, 2], None))) as zf:
                self.assertEqual({i.date_time for i in zf.infolist()}, {data.ZIP_EPOCH})
                self.assertEqual({i.compress_type for i in zf.infolist()}, {zipfile.ZIP_STORED})
                self.assertIn(b"2000-01-01T00:00:00Z", zf.read("docProps/core.xml"))

    def test_generator_digest_covers_the_data_module(self):
        """数据家族的字节同样决定候选内容，不进摘要就等于改了模板而 candidate_id 不变。"""
        own = xt.cg.digest(Path(xt.__file__).read_bytes())
        self.assertNotEqual(xt.generator_digest(), own)

    def test_different_seeds_move_parameters(self):
        parent = fake_parent([1, 2, 3])
        ids = {xt.build(parent, ("read_tsv_stdlib",), s)[0]["candidate_id"] for s in range(12)}
        self.assertGreater(len(ids), 1)


class StaticPlan(unittest.TestCase):
    def test_compiles_and_is_executable_without_touching_the_uncertain_table(self):
        before = dict(xt._UNCERTAIN_IMPORTS)
        for parent in xt.default_parents():
            if not parent.executable:
                continue
            for name in NAMES:
                if data.missing(data.BY_NAME[name]) or not xt.BY_NAME[name].applies(
                    xt.Ctx(
                        ast.parse(parent.files[parent.script].decode()),
                        dict(parent.files),
                        parent.script,
                        parent.entry,
                    )
                ):
                    continue
                manifest, files = xt.build(parent, (name,), 2)
                self.assertEqual(
                    manifest["execution_plan"]["status"], "executable", (parent.case, name)
                )
                self.assertEqual(manifest["static_checks"]["compile"], "passed")
                self.assertEqual(manifest["class"], "shape_preserving")
                ast.parse(files[parent.script].decode())
        self.assertEqual(xt._UNCERTAIN_IMPORTS, before)


class Dedup(unittest.TestCase):
    def test_only_path_or_encoding_parameters_collapse_but_a_path_style_is_structure(self):
        parent = fake_parent([1, 2, 3])
        by_style: dict[str, dict[str, str]] = {}
        for seed in range(60):
            manifest = xt.build(parent, ("read_tsv_stdlib",), seed)[0]
            params = manifest["chain"][0]["params"]
            by_style.setdefault(params["path_style"], {})[
                f"{params['encoding']}|{params['data_files'][0]}"
            ] = manifest["ast_signature"]
        for style, found in by_style.items():
            self.assertGreater(len(found), 1, style)
            self.assertEqual(len(set(found.values())), 1, (style, found))
        signatures = {next(iter(f.values())) for f in by_style.values()}
        self.assertEqual(len(signatures), len(data.PATH_STYLES))


class MissingDependency(unittest.TestCase):
    def test_missing_module_makes_the_transform_not_apply_and_says_why(self):
        parent = fake_parent([1, 2, 3])
        real = data.importlib.util.find_spec

        def find(name, *a, **k):
            return None if name == "pyarrow" else real(name, *a, **k)

        with mock.patch.object(data.importlib.util, "find_spec", find):
            ctx = xt.Ctx(
                ast.parse(parent.files["s.py"].decode()), dict(parent.files), "s.py", "main"
            )
            self.assertFalse(xt.BY_NAME["read_parquet"].applies(ctx))
            self.assertIn("pyarrow", data.unavailable()["read_parquet"])
            self.assertNotIn(("read_parquet",), xt.chains_for(parent, 1))
            with self.assertRaises(xt.cg.EvidenceError):
                xt.build(parent, ("read_parquet",), 1)
            self.assertIn("read_parquet", xt.coverage([parent], [], [])["unavailable_transforms"])


#: Tavotto 在 cwd 相对路径下读不到的数据源（沙盒 cwd 不是脚本目录，原生是）。这是**产品**差异，
#: 不是模板 bug：由 Guardian 如实记录，这里只登记「已知、已看到」，不替产品掩盖——别处的 outcome 必须是 no_difference。
#: parquet：`missing_input`（FileNotFoundError: cg_data.parquet）；glob：目录相对 glob 在沙盒 cwd 下为空，
#: 之后 matplotlib 报 `x and y must be the same size` 记成 `script_error`。
KNOWN_TAVOTTO_GAPS = {("read_parquet", "cwd"), ("glob_dir", "cwd")}


class RealExecution(unittest.TestCase):
    """真实执行（需要科学栈；xlsx / parquet 需要 workflow 装的 openpyxl / pyarrow）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)

    def check_format_matches_native_and_the_parent(self, name):
        """每种格式一个用例（两个父本 × 真实执行约 10 次一格，合在一条里会超出 5 分钟单例超时）。"""
        skipped: dict = {}
        if lacks(skipped, name):
            skip_missing(self, skipped)
        for case in ("shape_oo_savefig", "shape_entry_main"):
            parent = xt.load_parent("compat_engine", case)
            with self.subTest(parent=case, transform=name):
                manifest, _ = xt.build(parent, (name,), 7)
                style = manifest["chain"][0]["params"]["path_style"]
                out = Path(self.tmp.name) / f"{case}_{name}"
                out.mkdir()
                record = ex.run_one(manifest, 7, out, 200)
                self.assertGreaterEqual(record["executions"]["parent_native"], 1)
                self.assertEqual(record["native"]["figure_count"], 1, record)
                if (name, style) in KNOWN_TAVOTTO_GAPS:
                    self.assertEqual(record["judgment"]["outcome"], "difference_found", record)
                    kinds = {d["kind"] for d in record["judgment"]["differences"]}
                    self.assertEqual(kinds, {"zero_figures"}, record)
                else:
                    self.assertEqual(record["judgment"]["outcome"], "no_difference", record)
                    # 保型 = 不改用户的图 = 数据指纹也不变：结构层真比了数据，不是空比较
                    structure = record["stages"]["structure"]
                    self.assertEqual(structure["status"], "passed", record["stages"])
                    self.assertGreater(structure["data_compared"], 0, record["stages"])

    def test_a_value_changed_only_on_the_tavotto_side_is_a_data_structure_mismatch(self):
        """原生读到的是对的值，Tavotto 那份项目里数据被改了一个值：图数、标题都对，只有数据指纹能看出来。"""
        import shutil

        parent_spec = {"suite": "compat_engine", "case": "shape_oo_savefig"}
        manifest, _ = xt.build(
            xt.load_parent("compat_engine", "shape_oo_savefig"), ("read_tsv_stdlib",), 7
        )
        out = Path(self.tmp.name) / "tampered"
        work, evidence = out / "work", out / "evidence"
        work.mkdir(parents=True)
        evidence.mkdir()
        snapshot = ex.parent_snapshot(parent_spec, out, 300)["snapshots"]
        self.assertTrue(snapshot, "the parent's Tavotto snapshot is the reference")
        data_file = manifest["chain"][0]["params"]["data_files"][0]
        real_write = ex._write_project

        def tamper_tavotto_copy(root, files):
            project = real_write(root, files)
            if Path(root).name == "tavotto":
                target = project / data_file
                lines = target.read_text(encoding="utf-8").splitlines()
                head, last = lines[-1].rsplit("\t", 1)
                lines[-1] = f"{head}\t{int(last) + 100}"
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return project

        request = {
            "parent": parent_spec,
            "chain": ["read_tsv_stdlib"],
            "seed": 7,
            "candidate_id": manifest["candidate_id"],
            "work": str(work),
            "evidence": str(evidence),
            "parent_snapshot": snapshot,
        }
        env = cg.isolated_env(out / "env")
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(ex, "_write_project", tamper_tavotto_copy),
        ):
            record = ex.execute_request(request)
        shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(record["judgment"]["outcome"], "difference_found", record["judgment"])
        diffs = record["judgment"]["differences"]
        # 数据真的不同，Tavotto 那张图的像素当然也跟着变：结构层的数据指纹是更早、更精确的那个信号
        kinds = {d["kind"] for d in diffs}
        self.assertLessEqual(kinds, {"structure_mismatch", "pixel_mismatch"}, diffs)
        structure = next(d for d in diffs if d["kind"] == "structure_mismatch")
        # 值变了，轴范围之类的可编辑字段也会跟着变：data 那条要在其中，且指纹两侧都带着
        data = [m for m in structure["evidence"]["mismatches"] if m.get("aspect") == "data"]
        self.assertTrue(data, structure["evidence"])
        self.assertNotEqual(data[0]["parent"]["sha256"], data[0]["candidate"]["sha256"])
        self.assertEqual(record["stages"]["structure"]["status"], "failed")


for _name in NAMES:
    setattr(
        RealExecution,
        f"test_{_name}_matches_native_and_the_parent",
        lambda self, name=_name: self.check_format_matches_native_and_the_parent(name),
    )


if __name__ == "__main__":
    unittest.main()
