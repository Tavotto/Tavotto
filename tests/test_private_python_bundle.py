"""桌面安装包附带私有 Python 归档（ADR 0111）的打包侧：备料脚本的判据、spec / 构建脚本 / 发行工作流的接线、
wheel / sdist 绝不带它。运行时一侧（包内归档命中不联网、sha 不符回退下载）在 `tests/test_private_python.py::
TestBundledArchive`。"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import stage_private_python as spp  # noqa: E402
from tavotto.engine import privatepython, runtime  # noqa: E402

SPEC = REPO / "packaging" / "tavotto.spec"


def _fake_source(tmp_path, data: bytes) -> privatepython.PythonSource:
    import hashlib

    real = privatepython.source_for("macos-arm64")
    return privatepython.PythonSource(
        **{
            **real.__dict__,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    )


@pytest.fixture
def fake(tmp_path, monkeypatch):
    data = b"pretend this is a pbs install_only archive" * 100
    src = _fake_source(tmp_path, data)
    monkeypatch.setattr(spp, "source", lambda target=None: src)
    return src, data


class TestCheckBundle:
    def test_missing_dir_is_none_not_an_error(self, tmp_path, fake):
        assert spp.check_bundle(tmp_path / "nope") is None

    def test_the_one_matching_archive_passes(self, tmp_path, fake):
        src, data = fake
        (tmp_path / src.archive_name).write_bytes(data)
        assert spp.check_bundle(tmp_path) == tmp_path / src.archive_name

    @pytest.mark.parametrize("damage", ["bytes", "extra-file", "other-name"])
    def test_anything_else_refuses_to_package(self, tmp_path, fake, damage):
        src, data = fake
        if damage == "bytes":
            (tmp_path / src.archive_name).write_bytes(data[:-1] + b"!")
        elif damage == "extra-file":
            (tmp_path / src.archive_name).write_bytes(data)
            (tmp_path / "cpython-other-target.tar.gz").write_bytes(data)
        else:
            (tmp_path / "cpython-other-target.tar.gz").write_bytes(data)
        with pytest.raises(spp.BuildError):
            spp.check_bundle(tmp_path)

    def test_linux_has_no_desktop_bundle(self):
        with pytest.raises(spp.BuildError):
            spp.source("linux-x86_64")


def test_desktop_targets_are_the_shipped_runtime_targets():
    """附带归档的目标 == 发桌面安装包的目标（`packaging/runtime-lock.json` 的 shipped，架构名归一后）。"""
    lock = json.loads((REPO / "packaging" / "runtime-lock.json").read_text(encoding="utf-8"))
    shipped = {
        f"{t['os']}-{runtime.normalize_arch(t['arch'])}"
        for t in lock["targets"].values()
        if t.get("shipped")
    }
    assert set(spp.DESKTOP_TARGETS) == shipped
    for name in spp.DESKTOP_TARGETS:
        assert privatepython.source_for(name) is not None, name


def test_spec_ships_the_archive_where_the_runtime_looks_for_it():
    """同源对：spec 的 datas 目的地 == `runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME`（运行时在 `_internal/<它>/` 找），
    且经 `check_bundle` 收、`TAVOTTO_REQUIRE_PRIVATE_PYTHON` 可把「必须带」打开。判 AST 不判子串。"""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    appended = [
        n.args[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "datas"
        and n.args
        and isinstance(n.args[0], ast.Tuple)
    ]
    dests = {
        t.elts[1].value
        for t in appended
        if len(t.elts) == 2 and isinstance(t.elts[1], ast.Constant)
    }
    assert runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME in dests
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "check_bundle" in names
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert "TAVOTTO_REQUIRE_PRIVATE_PYTHON" in consts


def test_build_desktop_stages_the_archive_before_pyinstaller():
    """`main()` 里备料那一步（`stage_private_python(...)` 的调用）在 PyInstaller 之前。判 AST。"""
    src = (REPO / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    stage_calls = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "stage_private_python"
    ]
    pyinstaller = [
        n.lineno for n in ast.walk(main) if isinstance(n, ast.Constant) and n.value == "PyInstaller"
    ]
    assert len(stage_calls) == 1 and len(pyinstaller) == 1, (stage_calls, pyinstaller)
    assert stage_calls[0] < pyinstaller[0]


def _build_desktop(monkeypatch, bundle: Path):
    import build_desktop as bd

    runs: list[list[str]] = []
    monkeypatch.setattr(bd, "PRIVATE_PYTHON_BUNDLE", bundle)
    monkeypatch.setattr(bd, "run", lambda cmd, **kw: runs.append(cmd))
    return bd, runs


def test_build_desktop_bundle_dir_is_the_staging_dir():
    import build_desktop as bd

    assert bd.PRIVATE_PYTHON_BUNDLE == spp.BUNDLE_DIR


def test_skipping_private_python_removes_a_previously_staged_archive(tmp_path, monkeypatch):
    """Codex #743 P2：spec 只看目录在不在——`--skip-private-python` 光不调备料脚本的话，上一次备好的（甚至
    另一个目标的）归档照样被收进包里。跳过就得把目录删掉，且不起备料脚本。"""
    bundle = tmp_path / "private-python-bundle"
    bundle.mkdir()
    (bundle / "cpython-old-target.tar.gz").write_bytes(b"stale")
    bd, runs = _build_desktop(monkeypatch, bundle)
    bd.stage_private_python(True)
    assert not bundle.exists()
    assert runs == []
    assert spp.check_bundle(bundle) is None  # spec 那把尺此后看到的是「没备」
    bd.stage_private_python(True)  # 目录本来就不在：照样不出错


def test_not_skipping_runs_the_staging_script(tmp_path, monkeypatch):
    bd, runs = _build_desktop(monkeypatch, tmp_path / "private-python-bundle")
    bd.stage_private_python(False)
    assert len(runs) == 1 and Path(runs[0][-1]).name == "stage_private_python.py"


def test_the_release_workflow_requires_the_archive():
    wf = (REPO / ".github" / "workflows" / "desktop-tauri.yml").read_text(encoding="utf-8")
    assert 'TAVOTTO_REQUIRE_PRIVATE_PYTHON: "1"' in wf


@pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")
def test_wheel_and_sdist_never_pick_up_the_archive():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = cfg["tool"]["hatch"]["build"]
    rel = spp.BUNDLE_DIR.relative_to(REPO).as_posix()
    assert f"{rel}/**" in build["exclude"]
    assert not any("private-python" in a or a.startswith("build") for a in build["artifacts"])
    sdist = build["targets"]["sdist"]["include"]
    assert not any(p.strip("/").startswith("build") for p in sdist)
    assert spp.BUNDLE_DIR.parent.name == "build"
    assert "/build/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
