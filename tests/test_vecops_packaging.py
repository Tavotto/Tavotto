"""矢量几何内核的依赖与打包（ADR 0212 第 3 期）：wasmtime 是运行时依赖；冻结产物要显式收它的原生库；vecops.wasm 的找法。

判据的主语：不是「spec 文本里出现 wasmtime」，而是 spec 的**语法树**里 ① 有 `collect_dynamic_libs("wasmtime")` 的结果进了 `binaries`、
② 整包子模块进了 `Analysis(hiddenimports=…)`。本机真跑过 PyInstaller（有 / 没有这一收）的对照结果记在 PR 里。"""

from __future__ import annotations

import ast
from pathlib import Path

import tomllib

from tavotto.engine import vecops

ROOT = Path(__file__).resolve().parent.parent


def _spec_tree() -> ast.Module:
    return ast.parse((ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8"))


def test_wasmtime_is_a_pinned_runtime_dependency_mirrored_in_requirements():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    spec = next(d for d in deps if d.startswith("wasmtime"))
    assert ">=" in spec and "<" in spec  # 上下界都钉
    req = [
        ln
        for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if ln.startswith("wasmtime")
    ]
    assert len(req) == 1 and req[0].startswith("wasmtime==")


def test_spec_collects_the_wasmtime_native_library_and_submodules():
    tree = _spec_tree()
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "collect_dynamic_libs"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "wasmtime"
    ]
    assert calls, (
        "spec 没有 collect_dynamic_libs('wasmtime')：冻结产物里 _libwasmtime 会缺，内核不可用"
    )
    # 收到的库确实进了 binaries（而不是只算了一下）
    holder = next(
        n.targets[0].id
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and any(c in ast.walk(n.value) for c in calls)
    )
    assert any(
        isinstance(n, ast.AugAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "binaries"
        and isinstance(n.value, ast.Name)
        and n.value.id == holder
        for n in tree.body
    )
    analysis = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Analysis"
    )
    hidden = next(k.value for k in analysis.keywords if k.arg == "hiddenimports")
    assert any(
        isinstance(e, ast.Starred)
        and isinstance(e.value, ast.Name)
        and e.value.id == "WASMTIME_MODULES"
        for e in hidden.elts
    )


def test_wasmtime_loads_its_native_library_from_a_platform_dir_next_to_the_package():
    """`collect_dynamic_libs` 保留相对包目录的路径；冻结产物能用，前提是库就在 `<包>/<平台>-<架构>/` 里（`_ffi.py` 的相对寻址）。"""
    import wasmtime

    pkg = Path(wasmtime.__file__).parent
    libs = [p for p in pkg.rglob("*") if p.suffix in (".dylib", ".so", ".dll") and p.parent != pkg]
    assert len(libs) == 1 and libs[0].parent.parent == pkg and "-" in libs[0].parent.name


def test_wasm_path_prefers_the_copy_the_frontend_build_puts_in_the_package(tmp_path, monkeypatch):
    pkg = tmp_path / "tavotto"
    (pkg / "engine").mkdir(parents=True)
    (pkg / "web" / "assets").mkdir(parents=True)
    built = pkg / "web" / "assets" / "vecops-AbC123.wasm"
    built.write_bytes(b"\0asm")
    monkeypatch.setattr(vecops, "__file__", str(pkg / "engine" / "vecops.py"))
    assert vecops.wasm_path() == built.resolve()


def test_wasm_path_falls_back_to_the_source_tree_then_to_nothing(tmp_path, monkeypatch):
    # 源码树：<根>/src/tavotto/engine/vecops.py → <根>/web/src/lib/vecops/vecops.wasm
    root = tmp_path / "repo"
    (root / "src" / "tavotto" / "engine").mkdir(parents=True)
    monkeypatch.setattr(vecops, "__file__", str(root / "src" / "tavotto" / "engine" / "vecops.py"))
    assert vecops.wasm_path() is None
    src = root / "web" / "src" / "lib" / "vecops"
    src.mkdir(parents=True)
    (src / "vecops.wasm").write_bytes(b"\0asm")
    assert vecops.wasm_path() == src / "vecops.wasm"
    # 真实仓库里这一份存在，且与前端 kernel.ts 用的是同一个文件
    monkeypatch.undo()
    assert (ROOT / "web" / "src" / "lib" / "vecops" / "vecops.wasm").is_file()


def test_the_abi_version_matches_the_frontend_loader():
    ts = (ROOT / "web" / "src" / "lib" / "vecops" / "kernel.ts").read_text(encoding="utf-8")
    assert f"export const VECOPS_ABI = {vecops.VECOPS_ABI}\n" in ts


def test_the_abi_version_matches_the_rust_crate():
    rs = (ROOT / "vecops" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert f"pub const ABI_VERSION: u32 = {vecops.VECOPS_ABI};" in rs


def test_spec_ships_the_wasmtime_license_with_its_native_library():
    """安装包里带着 wasmtime 的原生库，就得带着它的许可证原文（Apache-2.0 WITH LLVM-exception）：
    `copy_metadata("wasmtime")` 把 dist-info（含 `licenses/LICENSE`）收进 datas；第三方声明里也列着它。"""
    tree = _spec_tree()
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "copy_metadata"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == "wasmtime"
    ]
    assert any(
        isinstance(n, ast.AugAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "datas"
        and any(c in ast.walk(n.value) for c in calls)
        for n in ast.walk(tree)
    ), "spec 没有把 copy_metadata('wasmtime') 收进 datas：安装包里缺它的许可证原文"
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "wasmtime" in notices and "LLVM-exception" in notices
