#!/usr/bin/env python3
r"""把 Tauri 打包时被「解引用」成真拷贝的符号链接还原回来（纯标准库，只在 macOS 的 .app 上用）。

根因：PyInstaller 的 `dist/Tavotto` 里，凡是同一个文件出现在多处，它都用**相对符号链接**指向唯一一份——
`_internal/Python` → `Python.framework/Versions/3.13/Python`、
`_internal/libqpdf.30.x.dylib` → `pikepdf/.dylibs/libqpdf.30.x.dylib`、`Python.framework/Python` →
`Versions/Current/Python` 等。而 `tauri build` 把 `bundle.resources` 里的 `../dist/Tavotto` 搬进 `.app` 时，
指向文件的链接被当普通文件复制一份内容，指向目录的链接（`Versions/Current`）整个丢掉。于是 `.app` 里同一份字节
出现 2～3 次（Python 解释器库 6.5 MB × 3、libqpdf 3.9 MB × 2 …），更新包也跟着胖。

本脚本在 `tauri build` 之后、签名**之前**运行：对 `src`（PyInstaller 产物）里的每个符号链接，
在 `app` 内的 sidecar 目录里还原成**同样的相对链接**——

* 目的地缺失（被丢掉的目录链接）→ 新建；
* 目的地是真文件、且与链接指向的那份**逐字节相同** → 换回符号链接；
* 目的地是真文件但与指向的那份**不同** → 不动并报出来（宁可不省，不能改内容）；
* 指向 `src` 之外、或在 `.app` 里没有对应目标的链接 → 跳过。

只动符号链接，不删任何唯一内容；链接指向的目标永远还在，所以不会有用户脚本可能用到的模块消失。
签名侧：`codesign_macos.py` 的 `scan()` 本来就跳过符号链接、只签真实文件，所以先还原再签只是少签了
重复的拷贝；Python.framework 也因此恢复成标准的 `Versions/Current` 结构。

**必须在签名之前**：签完名每份拷贝的字节就不同了（各自带签名），`check` 也就无从按内容判重复。

用法：
    python scripts/relink_macos_symlinks.py relink --src dist/Tavotto \
        --app src-tauri/target/release/bundle/macos/Tavotto.app
    python scripts/relink_macos_symlinks.py check --app .../Tavotto.app   # 还有重复真文件就退出 1
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# stdout / stderr 不是真控制台（CI 捕获、管道）时会退回系统区域编码，第一句中文就 UnicodeEncodeError。
# 写法与 build_frontend.py 同源（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: .app 里 sidecar 目录相对 .app 根的位置（tauri.conf.json 的 bundle.resources 映射）
SIDECAR_REL = Path("Contents") / "Resources" / "sidecar" / "Tavotto"

#: 小于它的重复不算体积问题（`check` 只抓大块）
MIN_DUP_BYTES = 256 * 1024

#: `check` 不看的子树：内置渲染 runtime（`_internal/runtime/`）是一棵自洽的、逐字节钉死的 venv
#: （`diff_collected_runtime` 看护），它和 sidecar 自己收的 PIL 等同名库之间的重复是有意的分离，不是 Tauri 复制出来的。
CHECK_EXCLUDE = ("_internal/runtime/",)


@dataclass
class Report:
    relinked: list[str] = field(default_factory=list)  # 真拷贝 → 链接
    created: list[str] = field(default_factory=list)  # 缺失 → 新建链接
    differs: list[str] = field(default_factory=list)  # 内容不同，没动
    skipped: list[str] = field(default_factory=list)  # 指向树外 / 目标缺失
    saved_bytes: int = 0


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def relink(src: Path, sidecar: Path) -> Report:
    """`src` = PyInstaller 的 dist/Tavotto；`sidecar` = .app 里 Resources/sidecar/Tavotto。"""
    rep = Report()
    links: list[Path] = []
    for root, dirs, files in os.walk(src):
        for name in [*dirs, *files]:
            p = Path(root) / name
            if p.is_symlink():
                links.append(p)
    # 目录链接先来（`Versions/Current` 要在 `Python.framework/Python` 之前就位），再按深度
    links.sort(key=lambda p: (not p.is_dir(), len(p.relative_to(src).parts), str(p)))
    for link in links:
        rel = link.relative_to(src)
        text = os.readlink(link)
        if os.path.isabs(text):
            rep.skipped.append(f"{rel}（绝对链接）")
            continue
        if not _inside(Path(os.path.normpath(link.parent / text)), src):
            rep.skipped.append(f"{rel}（指向树外）")
            continue
        dst = sidecar / rel
        dst_target = Path(os.path.normpath(dst.parent / text))
        if dst.is_symlink():
            continue  # 已经是链接（打包器换了行为也无妨）
        if not dst.exists():
            if not dst_target.exists():
                rep.skipped.append(f"{rel}（.app 里没有目标）")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(text, dst)
            rep.created.append(str(rel))
            continue
        if dst.is_dir():
            rep.skipped.append(f"{rel}（.app 里是真目录）")
            continue
        if not dst_target.is_file():
            rep.skipped.append(f"{rel}（.app 里没有目标）")
            continue
        if dst.stat().st_size != dst_target.stat().st_size or not filecmp.cmp(
            dst, dst_target, shallow=False
        ):
            rep.differs.append(str(rel))
            continue
        size = dst.stat().st_size
        dst.unlink()
        os.symlink(text, dst)
        rep.relinked.append(str(rel))
        rep.saved_bytes += size
    return rep


def find_duplicates(sidecar: Path, min_bytes: int = MIN_DUP_BYTES) -> list[tuple[int, list[str]]]:
    """量最终 .app（签名前）里的**真文件**（不含 `CHECK_EXCLUDE`）：同大小、同内容、不是符号链接、≥ min_bytes 的重复组。

    返回 [(单份大小, [相对路径…])]，浪费最多的在前。同 inode 的硬链接只算一份。
    """
    by_size: dict[int, list[Path]] = {}
    for root, _dirs, files in os.walk(sidecar):
        for name in files:
            p = Path(root) / name
            if p.is_symlink() or p.relative_to(sidecar).as_posix().startswith(CHECK_EXCLUDE):
                continue
            size = p.stat().st_size
            if size >= min_bytes:
                by_size.setdefault(size, []).append(p)
    groups: list[tuple[int, list[str]]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: dict[str, dict[int, Path]] = {}
        for p in paths:
            by_hash.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), {})[p.stat().st_ino] = p
        for same in by_hash.values():
            if len(same) > 1:
                groups.append((size, sorted(str(p.relative_to(sidecar)) for p in same.values())))
    return sorted(groups, key=lambda g: -g[0] * (len(g[1]) - 1))


def _sidecar_of(app: Path) -> Path:
    sidecar = app / SIDECAR_REL
    if not sidecar.is_dir():
        raise SystemExit(f"找不到 sidecar 目录: {sidecar}")
    return sidecar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_re = sub.add_parser("relink", help="还原符号链接（签名之前）")
    p_re.add_argument("--src", required=True, type=Path, help="PyInstaller 产物 dist/Tavotto")
    p_re.add_argument("--app", required=True, type=Path)
    p_ck = sub.add_parser("check", help="还原之后检查：sidecar 里还有 ≥256 KB 的重复真文件就退出 1")
    p_ck.add_argument("--app", required=True, type=Path)
    args = ap.parse_args(argv)

    sidecar = _sidecar_of(args.app)
    if args.cmd == "relink":
        if not args.src.is_dir():
            raise SystemExit(f"找不到 PyInstaller 产物: {args.src}")
        rep = relink(args.src, sidecar)
        print(
            f"* 符号链接还原：换回 {len(rep.relinked)} 个、新建 {len(rep.created)} 个，"
            f"省 {rep.saved_bytes / 1e6:.1f} MB；内容不同未动 {len(rep.differs)}、跳过 {len(rep.skipped)}"
        )
        for d in rep.differs:
            print(f"  ! 与链接目标内容不同，保持真文件: {d}")
        return 0
    dups = find_duplicates(sidecar)
    if dups:
        print(
            f"✗ sidecar 里还有 {len(dups)} 组 ≥{MIN_DUP_BYTES // 1024} KB 的重复真文件：",
            file=sys.stderr,
        )
        for size, paths in dups[:20]:
            print(f"  {size / 1e6:.1f} MB × {len(paths)}: " + " | ".join(paths), file=sys.stderr)
        return 1
    print("✓ sidecar 里没有 ≥256 KB 的重复真文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
