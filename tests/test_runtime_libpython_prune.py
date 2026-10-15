"""#1074：macOS 内置 runtime 剪掉**没有任何 Mach-O 链接它**的 `lib/libpython3*.dylib`。

判据是加载命令里的引用，不是文件名：`codesign_macos.dylib_dependencies()` 读 LC_LOAD_DYLIB 一族，
`build_worker_runtime.prune_unreferenced_libpython()` 只在没人链接时才剪。这里用手工拼的 Mach-O 钉住
解析与判据，再在 macOS 上拿 `otool -L` 对拍一个真二进制（两把尺子独立）。
"""

from __future__ import annotations

import ast
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import build_worker_runtime as brt  # noqa: E402
import codesign_macos as cs  # noqa: E402

ARM64 = 0x0100000C
LC_LOAD_DYLIB, LC_ID_DYLIB, LC_LOAD_WEAK_DYLIB = 0xC, 0xD, 0x80000018
MACOS = {"kind": brt.KIND_MACOS}


def _dylib_cmd(cmd: int, name: str) -> bytes:
    raw = name.encode() + b"\0"
    size = (24 + len(raw) + 7) // 8 * 8
    return struct.pack("<IIIIII", cmd, size, 24, 2, 0x10000, 0x10000) + raw.ljust(size - 24, b"\0")


def _macho(*cmds: tuple[int, str], filetype: int = 0x6) -> bytes:
    body = b"".join(_dylib_cmd(c, n) for c, n in cmds)
    head = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", ARM64, 0, filetype, len(cmds), len(body), 0, 0
    )
    return head + body


def _fat(*slices: bytes) -> bytes:
    head = struct.pack(">II", cs.FAT_MAGIC, len(slices))
    offset = 8 + 20 * len(slices)
    table, blobs = b"", b""
    for s in slices:
        table += struct.pack(">IIIII", ARM64, 0, offset + len(blobs), len(s), 0)
        blobs += s
    return head + table + blobs


# ---------------- 解析 ----------------------------------------------------------
def test_dylib_dependencies_reads_load_commands_but_not_the_own_install_name(tmp_path):
    p = tmp_path / "x.dylib"
    p.write_bytes(
        _macho(
            (LC_ID_DYLIB, "@rpath/x.dylib"),
            (LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib"),
            (LC_LOAD_WEAK_DYLIB, "@rpath/libpython3.13.dylib"),
        )
    )
    assert cs.dylib_dependencies(p) == {"/usr/lib/libSystem.B.dylib", "@rpath/libpython3.13.dylib"}


def test_dylib_dependencies_unions_fat_slices_and_ignores_non_macho(tmp_path):
    fat = tmp_path / "fat.so"
    fat.write_bytes(
        _fat(
            _macho((LC_LOAD_DYLIB, "/usr/lib/a.dylib")), _macho((LC_LOAD_DYLIB, "/usr/lib/b.dylib"))
        )
    )
    assert cs.dylib_dependencies(fat) == {"/usr/lib/a.dylib", "/usr/lib/b.dylib"}
    text = tmp_path / "python3.13-config"
    text.write_text("#!/bin/sh\necho -lpython3.13\n", encoding="utf-8")
    assert cs.dylib_dependencies(text) is None


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("otool"), reason="要 macOS 的 otool 对拍"
)
@pytest.mark.parametrize(
    "binary",
    [
        pytest.param(lambda: Path(sys.executable).resolve(), id="current-python"),
        # 系统自带的 fat 文件（x86_64 + arm64e）：CI 上 python.org 的 universal2 解释器就是这种形态
        pytest.param(lambda: Path("/usr/bin/true"), id="fat-system-binary"),
    ],
)
def test_dylib_dependencies_agrees_with_otool_on_a_real_binary(binary):
    exe = binary()
    # -arch all：fat 文件每一片都列（不带它只列本机那一片），与 dylib_dependencies 取各片并集同一口径
    out = subprocess.run(
        ["otool", "-arch", "all", "-L", str(exe)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    # 每一片前有一行「<路径> (architecture …):」/「<路径>:」表头，不是依赖
    want = {
        line.strip().split(" (")[0]
        for line in out.splitlines()
        if line.strip() and not line.rstrip().endswith(":")
    }
    got = cs.dylib_dependencies(exe)
    assert got  # 真二进制至少链接 libSystem
    # otool -L 对 dylib 会把自己的 install name 也列出来；可执行文件没有，两边应完全一致
    assert got == want


# ---------------- 精简判据 --------------------------------------------------------
def _runtime(root: Path, *, ext_links: str | None = None) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python3.13").write_bytes(
        _macho((LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib"), filetype=0x2)
    )
    lib = root / "lib"
    lib.mkdir()
    (lib / "libpython3.13.dylib").write_bytes(_macho((LC_ID_DYLIB, "@rpath/libpython3.13.dylib")))
    (lib / "libpython3.dylib").symlink_to("libpython3.13.dylib")
    ext = root / "lib" / "python3.13" / "site-packages" / "numpy" / "_core" / "_umath.so"
    ext.parent.mkdir(parents=True)
    deps = [(LC_LOAD_DYLIB, "/usr/lib/libSystem.B.dylib")]
    if ext_links:
        deps.append((LC_LOAD_DYLIB, ext_links))
    ext.write_bytes(_macho(*deps, filetype=0x8))
    return lib


@pytest.mark.skipif(sys.platform == "win32", reason="建符号链接要特权")
def test_unreferenced_libpython_and_its_alias_are_pruned(tmp_path):
    lib = _runtime(tmp_path)
    removed, freed = brt.prune_unreferenced_libpython(tmp_path, MACOS)
    assert removed == 2 and freed > 0
    assert sorted(p.name for p in lib.iterdir()) == ["python3.13"]
    assert (tmp_path / "bin" / "python3.13").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="建符号链接要特权")
@pytest.mark.parametrize(
    "link", ["@rpath/libpython3.13.dylib", "@loader_path/../../../../libpython3.dylib"]
)
def test_libpython_is_kept_when_any_macho_links_it_even_via_the_alias(tmp_path, link):
    """判引用不判名字：哪怕只有一个扩展模块经别名链接它，也一个都不剪。"""
    lib = _runtime(tmp_path, ext_links=link)
    assert brt.prune_unreferenced_libpython(tmp_path, MACOS) == (0, 0)
    assert (lib / "libpython3.13.dylib").is_file() and (lib / "libpython3.dylib").is_symlink()


def test_non_macos_targets_are_untouched(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libpython3.13.dylib").write_bytes(b"x")
    assert brt.prune_unreferenced_libpython(tmp_path, {"kind": "windows-embeddable"}) == (0, 0)
    assert (tmp_path / "lib" / "libpython3.13.dylib").is_file()


def test_build_prunes_libpython_before_the_smoke_that_validates_it():
    """剪完再冒烟：import + 真绘图验的必须是用户拿到的那份布局（与 prune_aliases 同一顺序）。"""
    tree = ast.parse((REPO / "scripts" / "build_worker_runtime.py").read_text(encoding="utf-8"))
    build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build")
    first = {}
    for n in ast.walk(build):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            first.setdefault(n.func.id, n.lineno)
            first[n.func.id] = min(first[n.func.id], n.lineno)
    assert first["prune_unreferenced_libpython"] < first["precompile"] < first["smoke_imports"]
