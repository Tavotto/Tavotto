"""`scripts/codesign_macos.py` 的最低系统核对（ADR 0072 §2）。

桌面版声明的 `LSMinimumSystemVersion` 必须不低于打进去的每个 Mach-O 要求的 macOS：RenderCore 切默认时
pikepdf 的 wheel 把下限抬到 14.0 / 15.0，而声明还是 11.0——装得上、打得开、渲染时才出事。这里用手工拼的
Mach-O 钉住解析与判据，再在 macOS 上拿 `otool` 对拍一个真二进制（两把尺子独立：一个坏了另一个不会跟着坏）。
"""

from __future__ import annotations

import importlib.util
import plistlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "codesign_macos", ROOT / "scripts" / "codesign_macos.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = _load()

ARM64, X86_64 = 0x0100000C, 0x01000007


def _packed(v: str) -> int:
    major, minor = (int(x) for x in v.split("."))
    return (major << 16) | (minor << 8)


def _thin(cputype: int, min_os: str | None, *, legacy: bool = False) -> bytes:
    """64 位小端 thin Mach-O：头 + 一条 LC_BUILD_VERSION（或旧的 LC_VERSION_MIN_MACOSX）。"""
    cmds = b""
    if min_os is not None:
        if legacy:
            cmds = struct.pack("<IIII", cs.LC_VERSION_MIN_MACOSX, 16, _packed(min_os), 0)
        else:
            cmds = struct.pack(
                "<IIIIII", cs.LC_BUILD_VERSION, 24, cs.PLATFORM_MACOS, _packed(min_os), 0, 0
            )
    ncmds = 1 if cmds else 0
    head = b"\xcf\xfa\xed\xfe" + struct.pack(
        "<IIIIIII", cputype, 0, cs.MH_DYLIB, ncmds, len(cmds), 0, 0
    )
    return head + cmds


def _fat(*slices: tuple[int, bytes]) -> bytes:
    """fat 头恒为大端；每片 4 KiB 对齐。"""
    head = struct.pack(">II", cs.FAT_MAGIC, len(slices))
    offset = 4096
    table, body = b"", b""
    for cputype, data in slices:
        table += struct.pack(">IIIII", cputype, 0, offset + len(body), len(data), 12)
        body += data + b"\0" * (-len(data) % 4096)
    return head + table + b"\0" * (4096 - len(head) - len(table)) + body


def _app(tmp_path: Path, declared: str, files: dict[str, bytes]) -> Path:
    app = tmp_path / "Tavotto.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"LSMinimumSystemVersion": declared})
    )
    for rel, data in files.items():
        p = app / "Contents" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return app


def test_minos_reads_build_version_legacy_and_the_right_fat_slice(tmp_path):
    thin = tmp_path / "thin.dylib"
    thin.write_bytes(_thin(ARM64, "14.0"))
    legacy = tmp_path / "legacy.dylib"
    legacy.write_bytes(_thin(X86_64, "10.13", legacy=True))
    fat = tmp_path / "fat.dylib"
    fat.write_bytes(_fat((X86_64, _thin(X86_64, "15.0")), (ARM64, _thin(ARM64, "11.0"))))
    none = tmp_path / "none.dylib"
    none.write_bytes(_thin(ARM64, None))
    assert cs.minos(thin, "arm64") == (14, 0)
    assert cs.minos(legacy, "x86_64") == (10, 13)
    # 同一个 fat 文件，按架构取各自那一片——取错片 = 拿 Intel 的 15.0 去判 arm64 的包
    assert cs.minos(fat, "arm64") == (11, 0)
    assert cs.minos(fat, "x86_64") == (15, 0)
    assert cs.minos(none, "arm64") is None
    assert cs.minos(tmp_path / "missing", "arm64") is None


def test_check_passes_when_every_macho_is_at_or_below_the_declared_minimum(tmp_path, capsys):
    app = _app(
        tmp_path,
        "14.0",
        {"Frameworks/a.dylib": _thin(ARM64, "14.0"), "Frameworks/b.so": _thin(ARM64, "11.0")},
    )
    items = cs.scan(app)
    assert len(items) == 2
    cs.check_min_os(app, items, "14.0", "arm64")
    assert "2 个 Mach-O" in capsys.readouterr().out


def test_check_fails_on_a_macho_that_needs_a_newer_macos(tmp_path):
    """RenderCore 切默认那一次的形状：声明 11.0，pikepdf 那片是 14.0。"""
    app = _app(
        tmp_path,
        "11.0",
        {
            "Resources/pikepdf/_core.so": _thin(ARM64, "14.0"),
            "Frameworks/b.so": _thin(ARM64, "11.0"),
        },
    )
    with pytest.raises(cs.SignError, match=r"pikepdf/_core.so → 14.0"):
        cs.check_min_os(app, cs.scan(app), "11.0", "arm64")


def test_check_fails_when_the_plist_does_not_declare_the_expected_minimum(tmp_path):
    app = _app(tmp_path, "11.0", {"Frameworks/a.dylib": _thin(ARM64, "11.0")})
    with pytest.raises(cs.SignError, match="LSMinimumSystemVersion"):
        cs.check_min_os(app, cs.scan(app), "14.0", "arm64")


def test_check_refuses_to_pass_when_no_minimum_could_be_read(tmp_path):
    """一个 minos 都没读到 = 解析坏了；这时「没有超下限的」是空集合上的真，不能算过。"""
    app = _app(tmp_path, "14.0", {"Frameworks/a.dylib": _thin(ARM64, None)})
    with pytest.raises(cs.SignError, match="解析坏了"):
        cs.check_min_os(app, cs.scan(app), "14.0", "arm64")


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("otool") is None,
    reason="需要 macOS 的 otool（对拍用）",
)
def test_minos_agrees_with_otool_on_a_real_binary():
    """独立的第二把尺子：同一个真二进制，自己解析的 minos 与 otool 报的相同。"""
    import platform

    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    target = Path(sys.executable).resolve()
    out = subprocess.run(
        ["otool", "-arch", arch, "-l", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    lines = out.splitlines()
    want = None
    for i, line in enumerate(lines):
        if "LC_BUILD_VERSION" in line or "LC_VERSION_MIN_MACOSX" in line:
            for follow in lines[i : i + 6]:
                parts = follow.split()
                if parts and parts[0] in ("minos", "version"):
                    want = tuple(int(x) for x in parts[1].split(".")[:2])
                    break
            break
    if want is None:
        pytest.skip(f"otool 没从 {target} 读到最低系统（{arch}）")
    assert cs.minos(target, arch) == want
