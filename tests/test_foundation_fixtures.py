"""U00 合成 fixture 的真值测试 + 原生参考隔离测试（`tests/fixtures/foundation/`）。

两类判据，主语各自说清：

* **输入真值**——夹具文件（CSV / requirements / PDF / PNG）里写的数字与
  `truth.json` 一致，且真值与干扰值分得开。全部用标准库解析，**不 import 任何
  产品模块**：夹具的真值不能由被测产品来背书。
* **原生参考不污染被测环境**——`python <脚本>` 在夹具的**临时副本**里跑，产物落在
  副本里，夹具目录执行前后的文件快照逐字节相同。解释器只借用 `pool.find_worker_python()`
  找一个装了 matplotlib 的（与 `test_worker_roundtrip.py` 同一个借法；它不是被测对象），
  找不到就 skip 并写明——skip 是 `not_run`，不是绿。

这些用例**不是产品测试**：首开链路（Tavotto 打开这些项目并出图）的资格从 U03 起
取得，U00 一律 `not_run`。
"""

from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent / "fixtures" / "foundation"
FIXTURES = (
    "single_file_csv",
    "split_scripts_data",
    "same_name_data",
    "project_venv",
    "dependency_declarations",
    "pdf_png_assets",
)


def _truth(name: str) -> dict:
    return json.loads((ROOT / name / "truth.json").read_text(encoding="utf-8"))


def _csv_column(path: Path, column: str) -> list[float]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [float(row[column]) for row in csv.DictReader(fh)]


def _load_module(name: str, path: Path):
    """按路径装载一个夹具 / 工具模块，**不往它旁边写 `__pycache__`**——夹具目录的
    文件快照是隔离用例的判据，测试自己往里写字节码会把判据弄成随执行顺序漂的东西。"""
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


# --------------------------------------------------------------------------- 目录形状
def test_every_fixture_has_a_truth_file_and_the_readme_lists_it():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in FIXTURES:
        assert (ROOT / name).is_dir(), name
        assert (ROOT / name / "truth.json").is_file(), name
        assert f"`{name}/`" in readme, f"README 没列 {name}"
        assert _truth(name)["fixture"] == name


# --------------------------------------------------------------------------- ① 同目录 CSV
def test_single_file_csv_truth():
    t = _truth("single_file_csv")
    xs = _csv_column(ROOT / "single_file_csv" / t["data_file"], "x")
    ys = _csv_column(ROOT / "single_file_csv" / t["data_file"], "y")
    assert xs == t["x"]
    assert ys == t["y"]
    assert ys == [1.5 * x + 1 for x in xs], "truth.json 的公式与 CSV 不一致"
    assert (ROOT / "single_file_csv" / "figure.py").is_file()


# --------------------------------------------------------------------------- ② scripts/data 分离
def test_split_scripts_data_truth_and_local_package():
    t = _truth("split_scripts_data")
    base = ROOT / "split_scripts_data"
    ts = _csv_column(base / t["data_file"], "t")
    vs = _csv_column(base / t["data_file"], "v")
    assert ts == t["t"]
    assert vs == t["v"]
    assert vs == [2 * x + 1 for x in ts]
    pkg = base / t["local_package"]
    assert (pkg / "__init__.py").is_file()
    # 本地包的公式与数据同一条：按路径装载，不经 sys.path（它不是产品代码）
    mod = _load_module("u00_labhelpers", pkg / "__init__.py")
    assert [mod.predict(x) for x in ts] == vs
    assert set(t["modes"]) == {"file", "cwd"}


# --------------------------------------------------------------------------- ③ 同名不同值
def test_same_name_data_truth_and_decoy_are_distinguishable():
    t = _truth("same_name_data")
    base = ROOT / "same_name_data"
    for key in ("correct", "decoy"):
        xs = _csv_column(base / t[key]["file"], "x")
        assert xs == t[key]["x"], key
        assert [3 * x + 1 for x in xs] == t[key]["y"], key
    assert Path(t["correct"]["file"]).name == Path(t["decoy"]["file"]).name, "两份必须同名"
    assert set(t["correct"]["y"]).isdisjoint(t["decoy"]["y"]), "真值与干扰值必须分得开"


# --------------------------------------------------------------------------- ④ 项目 venv
def test_project_venv_truth_and_ignore_rules():
    t = _truth("project_venv")
    base = ROOT / "project_venv"
    ks = _csv_column(base / t["data_file"], "k")
    ns = _csv_column(base / t["data_file"], "n")
    assert ks == t["k"] and ns == t["n"]
    assert ns == [10 * k for k in ks]
    assert t["harness_preinstalls_packages"] is False
    ignore = (base / ".gitignore").read_text(encoding="utf-8").split()
    assert t["venv_dir"] in ignore and t["receipt"] in ignore
    src = (base / t["constructed_by"]).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 构造脚本里不许出现 pip install：判 AST 里 subprocess 调用的 argv 字面量
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != "install", "make_venv.py 不许 pip install"


def _alternate_python() -> str | None:
    """PATH 上另一个 minor 的 CPython（3.10–3.14），没有就 None。"""
    mine = sys.version_info[:2]
    for minor in (10, 11, 12, 13, 14):
        if (3, minor) == mine:
            continue
        exe = shutil.which(f"python3.{minor}")
        if exe:
            return exe
    return None


def test_make_venv_builds_a_venv_on_a_different_python_without_installing(tmp_path):
    other = _alternate_python()
    if other is None:
        pytest.skip(
            "not_run：PATH 上没有第二个 minor 的 python3.x，无法构造「不同 Python」的项目 venv"
        )
    script = ROOT / "project_venv" / "make_venv.py"
    dest = tmp_path / "proj"
    dest.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--python",
            other,
            "--app-python",
            sys.executable,
            "--dest",
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads((dest / "venv_receipt.json").read_text(encoding="utf-8"))
    assert receipt["venv_version"][:2] != list(sys.version_info[:2])
    assert receipt["packages_installed_by_this_script"] == []
    assert receipt["linked_host_site"] == []
    # 裸 venv：基础解释器的 site-packages 不可见，matplotlib 必然 import 不到
    assert receipt["matplotlib_importable"] is False
    assert (dest / ".venv").is_dir()
    # 同一个 minor 当项目 Python：前提不成立必须拒绝，而不是建一个「不同 Python」的假象
    same = subprocess.run(
        [
            sys.executable,
            str(script),
            "--python",
            sys.executable,
            "--app-python",
            sys.executable,
            "--dest",
            str(tmp_path / "same"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert same.returncode == 2
    assert "同一个 minor" in same.stderr


# --------------------------------------------------------------------------- ⑤ 依赖声明
_REQ = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[^\]]*)\])?(?P<spec>[<>=!~][^;]*)?(?:;\s*(?P<marker>.+))?$"
)


def _parse_requirement_lines(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _REQ.match(line)
        assert m, line
        out.append(
            {
                "name": m["name"],
                "extras": [e.strip() for e in (m["extras"] or "").split(",") if e.strip()],
                "spec": (m["spec"] or "").strip(),
                "marker": (m["marker"] or "").strip() or None,
            }
        )
    return out


def _version_tuple(s: str) -> tuple[int, ...]:
    return tuple(int(p) for p in s.split("."))


def test_dependency_declarations_match_truth():
    t = _truth("dependency_declarations")
    base = ROOT / "dependency_declarations"
    for fname, expected in t["files"].items():
        parsed = _parse_requirement_lines(base / fname)
        assert [(p["name"], p["extras"], p["spec"], p["marker"]) for p in parsed] == [
            (e["name"], e["extras"], e["spec"], e["marker"]) for e in expected
        ], fname
    # 小型非内置依赖：requirements.txt 的名字集合 == 声明的集合
    assert {e["name"] for e in t["files"]["requirements.txt"]} == set(
        t["small_non_builtin_distributions"]
    )
    # marker 为假：这两条 marker 在真实机器上必须求值为假（本机就是一台真实机器）
    for e in t["files"]["requirements-marker-false.txt"]:
        assert e["expected"] == "skip_marker_false"
        marker = e["marker"]
        if marker.startswith("sys_platform"):
            assert sys.platform != "never_os"
        elif marker.startswith("python_version"):
            assert sys.version_info >= (3, 0)
        else:  # pragma: no cover —— 新 marker 形态先来这里登记怎么求值
            raise AssertionError(f"没登记怎么求值的 marker: {marker}")
    # 冲突：同一个名字两条不同的精确版本
    conflict = t["files"]["requirements-conflict.txt"]
    assert len({c["name"] for c in conflict}) == 1
    assert len({c["spec"] for c in conflict}) == 2
    # 约束与 requirements 的 pin 冲突：==0.9.0 不满足 <0.9
    pin = next(e for e in t["files"]["requirements.txt"] if e["name"] == "tabulate")["spec"]
    cap = t["files"]["constraints.txt"][0]["spec"]
    assert pin.startswith("==") and cap.startswith("<")
    assert not (_version_tuple(pin[2:]) < _version_tuple(cap[1:]))
    # extra 真的是 extra
    extra = t["files"]["requirements-extra.txt"][0]
    assert extra["extras"] == ["widechars"] and extra["extra_pulls_in"] == ["wcwidth"]


def test_dependency_script_imports_match_truth():
    t = _truth("dependency_declarations")["script_unknown_local.py"]
    base = ROOT / "dependency_declarations"
    tree = ast.parse((base / "script_unknown_local.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    # 标准库不在真值里：真值只登记「解析器要裁决的那几个名字」
    imported -= set(sys.stdlib_module_names)
    assert imported == set(t["imports"])
    assert (base / "labtools_local.py").is_file(), "本地模块必须真的在旁边"
    assert not (base / "zzz_not_a_real_distribution_u00.py").exists()
    # `--dump` 只碰本地模块，不需要第三方包：任何解释器都能验它的数值真值
    proc = subprocess.run(
        # `-E -s` 而不是 `-I`：3.11 起 `-I` 隐含 `-P`，脚本目录不再进 sys.path，
        # 而「本地模块靠脚本目录 import 到」正是这条夹具要保住的启动形态
        # `-B`：`-E` 会让 PYTHONDONTWRITEBYTECODE 失效，字节码别写进夹具目录
        [sys.executable, "-E", "-s", "-B", "script_unknown_local.py", "--dump"],
        cwd=str(base),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert float(proc.stdout.strip()) == t["dump_truth"]


# --------------------------------------------------------------------------- ⑥ PDF / PNG
def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, out = 8, []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + body), tag
        out.append((tag, body))
        pos += 12 + length
    assert out[-1][0] == b"IEND"
    return out


def test_pdf_asset_matches_truth_when_read_independently():
    t = _truth("pdf_png_assets")["pdf"]
    raw = (ROOT / "pdf_png_assets" / t["file"]).read_bytes()
    assert raw.startswith(b"%PDF-1.") and raw.rstrip().endswith(b"%%EOF")
    text = raw.decode("latin-1")

    def box(key: str) -> list[float]:
        return [float(v) for v in re.search(rf"/{key}\s*\[([^\]]+)\]", text).group(1).split()]

    assert box("MediaBox") == t["media_box"]
    assert box("CropBox") == t["crop_box"]
    mb, cb = t["media_box"], t["crop_box"]
    insets = {
        "left": cb[0] - mb[0],
        "bottom": cb[1] - mb[1],
        "right": mb[2] - cb[2],
        "top": mb[3] - cb[3],
    }
    assert insets == t["crop_insets_pt"]
    assert len(set(insets.values())) > 1, "页盒内缩必须不对称"
    assert [cb[2] - cb[0], cb[3] - cb[1]] == t["visible_size_pt"]
    assert int(re.search(r"/Count\s+(\d+)", text).group(1)) == t["pages"]
    assert re.findall(r"/BaseFont\s*/(\w+)", text) == t["fonts"]
    assert "/FontFile" not in text and t["font_embedded"] is False
    assert f"({t['text']}) Tj" in text
    assert float(re.search(r"/ca\s+([0-9.]+)", text).group(1)) == t["transparency_alpha"]
    assert " re f" in text and " l S" in text, "至少一个填充图形与一条描边路径"
    assert "/Image" not in text and t["vector"] is True


def test_png_assets_match_truth_when_read_independently():
    truth = _truth("pdf_png_assets")
    t = truth["png"]
    chunks = _png_chunks((ROOT / "pdf_png_assets" / t["file"]).read_bytes())
    tags = [tag for tag, _ in chunks]
    assert tags[0] == b"IHDR"
    w, h, depth, ctype = struct.unpack(">IIBB", chunks[0][1][:10])
    assert [w, h, depth, ctype] == [t["width"], t["height"], t["bit_depth"], t["color_type"]]
    assert t["has_alpha"] is True and ctype == 6
    phys = next(body for tag, body in chunks if tag == b"pHYs")
    px, py, unit = struct.unpack(">IIB", phys)
    assert px == py == t["phys_pixels_per_metre"] and unit == 1
    assert round(px * 0.0254) == t["dpi_declared"]
    texts = dict(body.split(b"\x00", 1) for tag, body in chunks if tag == b"tEXt")
    assert {k.decode(): v.decode() for k, v in texts.items()} == t["text"]
    # 像素真值：解码 IDAT，左半不透明、右半 alpha 渐变
    idat = b"".join(body for tag, body in chunks if tag == b"IDAT")
    rows = zlib.decompress(idat)
    stride = 1 + 4 * w
    first = rows[1 : 1 + 4 * w]
    assert first[3] == 255 and first[4 * (w - 1) + 3] == 0
    assert len(rows) == stride * h
    n = truth["png_nophys"]
    chunks2 = _png_chunks((ROOT / "pdf_png_assets" / n["file"]).read_bytes())
    assert b"pHYs" not in [tag for tag, _ in chunks2] and n["phys"] is None
    w2, h2 = struct.unpack(">II", chunks2[0][1][:8])
    assert [w2, h2] == [n["width"], n["height"]]


def test_pdf_png_assets_are_reproducible_from_the_generator():
    base = ROOT / "pdf_png_assets"
    gen = _load_module("u00_make_assets", base / "make_assets.py")
    assert gen.build_pdf() == (base / "page.pdf").read_bytes()
    assert gen.build_png(with_phys=True) == (base / "original.png").read_bytes()
    assert gen.build_png(with_phys=False) == (base / "original_nophys.png").read_bytes()
    assert gen.truth() == _truth("pdf_png_assets")


# --------------------------------------------------------------------------- 原生参考隔离
def _snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            out[str(p.relative_to(root)) + "/"] = ""
    return out


def _scientific_python() -> str:
    """借用产品的解释器发现找一个装了 matplotlib 的 Python（不是被测对象）。"""
    from tavotto.engine import pool

    try:
        return pool.find_worker_python()
    except Exception as exc:  # noqa: BLE001 —— 找不到就 skip，理由写明
        pytest.skip(f"not_run：这台机器上没有装了 matplotlib 的解释器（{exc}）")


@pytest.fixture(scope="module")
def sci_python() -> str:
    return _scientific_python()


def _run_native(python: str, cwd: Path, args: list[str], tmp: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "MPLCONFIGDIR": str(tmp / "mplconfig"), "MPLBACKEND": "Agg"}
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [python, *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180
    )


@pytest.mark.parametrize(
    "name", ["single_file_csv", "split_scripts_data", "same_name_data", "project_venv"]
)
def test_native_reference_runs_in_an_isolated_copy_and_leaves_the_fixture_untouched(
    name, sci_python, tmp_path
):
    src = ROOT / name
    before = _snapshot(src)
    copy = tmp_path / name
    shutil.copytree(src, copy)
    t = _truth(name)
    if name == "split_scripts_data":
        ok = _run_native(sci_python, copy, ["scripts/entry.py", "--data-via", "file"], tmp_path)
        assert ok.returncode == 0, ok.stderr
        ok_cwd = _run_native(
            sci_python,
            copy,
            ["scripts/entry.py", "--data-via", "cwd", "--out", "entry_cwd.pdf"],
            tmp_path,
        )
        assert ok_cwd.returncode == 0, ok_cwd.stderr
        bad = _run_native(
            sci_python,
            copy / "scripts",
            ["entry.py", "--data-via", "cwd", "--out", "never.pdf"],
            tmp_path,
        )
        assert bad.returncode != 0 and "FileNotFoundError" in bad.stderr, (
            "cwd 歧义那一档在 scripts/ 下必须失败"
        )
        assert not (copy / "scripts" / "never.pdf").exists()
        assert (copy / t["expected_output"]).stat().st_size > 0
    elif name == "same_name_data":
        good = _run_native(sci_python, copy, ["plot.py", "--dump"], tmp_path)
        assert good.returncode == 0, good.stderr
        assert [float(v) for v in good.stdout.strip().split(",")] == t["correct"]["y"]
        decoy = _run_native(sci_python, copy / "decoy", ["../plot.py", "--dump"], tmp_path)
        assert decoy.returncode == 0, decoy.stderr
        assert [float(v) for v in decoy.stdout.strip().split(",")] == t["decoy"]["y"]
        assert (copy / t["expected_output"]).stat().st_size > 0
        assert (copy / "decoy" / t["expected_output"]).stat().st_size > 0
    else:
        script = t.get("script", "figure.py")
        proc = _run_native(sci_python, copy, [script], tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert (copy / t["expected_output"]).stat().st_size > 0
        if name == "project_venv":
            identity = json.loads(proc.stdout.strip().splitlines()[-1])
            assert identity["executable"] == sci_python or Path(identity["executable"]).samefile(
                sci_python
            )
    assert _snapshot(src) == before, "原生参考往夹具目录里写了东西"
