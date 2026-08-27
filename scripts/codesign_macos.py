#!/usr/bin/env python3
r"""给 macOS 的 Tavotto.app 逐个签名并验收（纯标准库 + 系统 codesign）。

为什么需要这个脚本，而不是一行 `codesign --deep`：

* **`--deep` 不是签名策略**。Apple 自己的文档把它标为「仅用于救急」，它对
  `Contents/Resources` 里那些**不被识别为嵌套代码**的 Mach-O 根本不会去签——
  而我们的内置渲染 runtime（500+ 个 .so/.dylib + 解释器）正好全在那儿。
  漏签的表现是公证被拒（Invalid），或者更坏：公证过了但 Gatekeeper 在用户
  机器上拦下某个 .so，症状是「渲染环境不可用」。
* **顺序是有讲究的**：必须**自内向外**。先签好每个嵌套二进制，最后再签 .app；
  反过来的话，外层签名会被内层的后续改动作废。
* **可执行文件与动态库要区别对待**：hardened runtime 的 entitlements 只对
  可执行文件有意义（内置解释器要 `disable-library-validation` 才敢加载
  numpy/scipy 带的那些 .dylib），给 .dylib 挂 entitlements 是无意义的噪音。

识别 Mach-O 用**读魔数**而不是 `file(1)`：11000 个文件各 fork 一次 `file`
要几分钟，读 4 个字节是秒级；顺带还能把 cputype 解出来做架构核对，
不必再 fork 一次 `lipo`。

用法：
    python scripts/codesign_macos.py sign   --app path/to/Tavotto.app \
        --identity "Developer ID Application: ..." \
        --entitlements packaging/entitlements.plist
    python scripts/codesign_macos.py verify --app path/to/Tavotto.app \
        --expect-arch arm64
    python scripts/codesign_macos.py scan   --app path/to/Tavotto.app
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path

# Mach-O 魔数。fat 头恒为大端（Apple 规定），所以这两个按 int 比就够了；
# thin 头的字节序要看魔数**字节序列**，见下面的 _THIN_MAGIC_BYTES。
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
_FAT = {FAT_MAGIC, FAT_CIGAM}

# filetype（mach_header.filetype）
MH_EXECUTE = 0x2
MH_DYLIB = 0x6
MH_BUNDLE = 0x8

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C
_CPU_NAMES = {CPU_TYPE_X86_64: "x86_64", CPU_TYPE_ARM64: "arm64"}

_ARCH_ALIASES = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


class SignError(RuntimeError):
    pass


class MachO:
    """一个 Mach-O 文件：路径、是不是可执行文件、包含哪些架构。"""

    __slots__ = ("path", "is_executable", "arches")

    def __init__(self, path: Path, is_executable: bool, arches: list[str]):
        self.path = path
        self.is_executable = is_executable
        self.arches = arches

    def __repr__(self) -> str:  # pragma: no cover - 只在排障时用
        kind = "exe" if self.is_executable else "lib"
        return f"<MachO {kind} {'/'.join(self.arches)} {self.path}>"


#: 魔数的**字节序列**唯一决定字节序，不能靠「按大端读出来像不像魔数」去猜:
#: FE ED FA CE 与 CE FA ED FE 互为反转，两边解出来都落在魔数集合里。
_THIN_MAGIC_BYTES = {
    b"\xfe\xed\xfa\xce": ">",  # 32 位大端
    b"\xfe\xed\xfa\xcf": ">",  # 64 位大端
    b"\xce\xfa\xed\xfe": "<",  # 32 位小端
    b"\xcf\xfa\xed\xfe": "<",  # 64 位小端（现代 macOS 全是这个）
}


def _read_header(fh, offset: int) -> tuple[int, int] | None:
    """读一个 thin Mach-O 头，回 (cputype, filetype)。"""
    fh.seek(offset)
    head = fh.read(16)
    if len(head) < 16:
        return None
    endian = _THIN_MAGIC_BYTES.get(head[:4])
    if endian is None:
        return None
    cputype, _cpusub, filetype = struct.unpack(endian + "III", head[4:16])
    return cputype, filetype


def inspect(path: Path) -> MachO | None:
    """是 Mach-O 就回 MachO，不是就回 None（不抛异常——扫描要能跑完）。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
            if len(head) < 8:
                return None
            # fat 头恒为大端（Apple 规定），所以这里按大端读是对的
            magic_be = struct.unpack(">I", head[:4])[0]

            if magic_be in _FAT:
                nfat = struct.unpack(">I", head[4:8])[0]
                if nfat > 64:  # 明显不对，别顺着坏数据读下去
                    return None
                arches, filetype = [], 0
                for i in range(nfat):
                    fh.seek(8 + i * 20)
                    entry = fh.read(20)
                    if len(entry) < 20:
                        break
                    cputype, _sub, off, _size, _align = struct.unpack(">IIIII", entry)
                    arches.append(_CPU_NAMES.get(cputype, hex(cputype)))
                    got = _read_header(fh, off)
                    if got:
                        filetype = got[1]
                if not arches:
                    return None
                return MachO(path, filetype == MH_EXECUTE, arches)

            got = _read_header(fh, 0)
            if got is None:
                return None
            cputype, filetype = got
            if filetype not in (MH_EXECUTE, MH_DYLIB, MH_BUNDLE):
                # .o 目标文件、dSYM 之类：不是要签的东西
                return None
            return MachO(path, filetype == MH_EXECUTE, [_CPU_NAMES.get(cputype, hex(cputype))])
    except OSError:
        return None


def scan(app: Path) -> list[MachO]:
    """把 .app 里所有 Mach-O 找出来，**自内向外**排序（深的在前）。

    跳过符号链接：它们指向的实体会被单独扫到，签两次只是白费时间，
    而且对同一个 inode 并发 codesign 会互相踩。
    """
    found: list[MachO] = []
    for root, _dirs, files in os.walk(app):
        for name in files:
            p = Path(root) / name
            if p.is_symlink():
                continue
            mo = inspect(p)
            if mo is not None:
                found.append(mo)
    # 深度降序 = 自内向外；同深度按路径稳定排序，便于比对两次构建的日志
    found.sort(key=lambda m: (-len(m.path.parts), str(m.path)))
    return found


def _codesign(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["codesign", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def sign(app: Path, identity: str, entitlements: Path | None) -> int:
    """自内向外签完整个 .app。任何一个失败即中止——漏签一个就等于没签。"""
    items = scan(app)
    print(
        f"* 待签 Mach-O：{len(items)} 个"
        f"（可执行 {sum(1 for m in items if m.is_executable)}，"
        f"库/bundle {sum(1 for m in items if not m.is_executable)}）"
    )

    for mo in items:
        args = ["--force", "--timestamp", "--options", "runtime", "--sign", identity]
        # entitlements 只对可执行文件有意义：内置解释器要靠
        # disable-library-validation 才能加载 numpy/scipy 自带的 .dylib
        if entitlements and mo.is_executable:
            args += ["--entitlements", str(entitlements)]
        rc, out = _codesign([*args, str(mo.path)])
        if rc != 0:
            raise SignError(f"签名失败 {mo.path}\n{out}")

    # 最后签 .app 本体（必须在所有嵌套项之后，否则外层签名当场作废）
    args = ["--force", "--timestamp", "--options", "runtime", "--sign", identity]
    if entitlements:
        args += ["--entitlements", str(entitlements)]
    rc, out = _codesign([*args, str(app)])
    if rc != 0:
        raise SignError(f"签名 .app 失败\n{out}")
    print(f"✓ 已签名 {len(items)} 个嵌套 Mach-O + .app 本体")
    return len(items)


def check_arch(app: Path, items: list[MachO], expect_arch: str | None) -> None:
    """.app 里每个 Mach-O 都得含目标架构。

    值得**在签名之前**单独跑一次：一个 x86_64 的 .so 混进 arm64 的包里，
    签名照样能过、公证也可能过，症状要等到用户 import 那个包时才出现
    （"mach-o file, but is an incompatible architecture"）。
    """
    want = _ARCH_ALIASES.get((expect_arch or "").lower(), expect_arch or "")
    if not want:
        return
    bad = [m for m in items if want not in m.arches]
    if bad:
        raise SignError(
            f"这些 Mach-O 不含 {want}（共 {len(bad)} 个，列前 10 个）：\n  "
            + "\n  ".join(f"{m.path.relative_to(app)} → {'/'.join(m.arches)}" for m in bad[:10])
        )
    print(f"✓ 全部 {len(items)} 个 Mach-O 都含 {want}")


def _verify_one(path: Path) -> tuple[Path, bool, str]:
    rc, out = _codesign(["--verify", "--strict", str(path)])
    return path, rc == 0, out.strip()


def bundle_main_executables(app: Path) -> set[Path]:
    """那些**不能单独验**的 Mach-O：某个 bundle 的主可执行文件。

    `codesign --verify <Tavotto.app/Contents/MacOS/Tavotto>` 会报
    "invalid resource directory"——主可执行文件的签名是连同整个 bundle 的资源
    封条一起成立的，脱离 bundle 去验它本身就是问的错问题。它们由前面那次
    `--verify --deep --strict <app>` 覆盖，不是漏验。

    判据是布局而不是文件名：`*/Contents/MacOS/*` 就是 bundle 的主可执行位置。
    onedir 形态（没有 .app 外壳，直接是 `dist/Tavotto/Tavotto`）另算一条。
    """
    skip: set[Path] = set()
    for root, _dirs, files in os.walk(app):
        parent = Path(root)
        if parent.name == "MacOS" and parent.parent.name == "Contents":
            skip.update(parent / f for f in files)
    # PyInstaller onedir：`<dir>/<dir 同名可执行>` 与目录一起构成签名单元
    onedir_main = app / app.name
    if app.suffix != ".app" and onedir_main.is_file():
        skip.add(onedir_main)
    return skip


def verify(app: Path, expect_arch: str | None, expect_identity: str | None, jobs: int = 8) -> None:
    """验收：签名完整 + 一个没漏 + 架构统一。

    `codesign --verify --deep` **验不出**「Resources 里躺着一个没签名的 .so」
    ——那种文件是被当作*资源*封进签名的，封条本身合法。所以这里必须逐个验，
    而这正是内置 runtime 引入的新风险面（500+ 个 .so 全在 Resources 下）。
    """
    rc, out = _codesign(["--verify", "--deep", "--strict", "--verbose=2", str(app)])
    if rc != 0:
        raise SignError(f".app 整体签名校验不过：\n{out}")
    print("✓ codesign --verify --deep --strict 通过")

    if expect_identity:
        rc, out = _codesign(["-dvvv", str(app)])
        if expect_identity not in out:
            raise SignError(f"签名主体不是期望的 {expect_identity!r}（很可能仍是 adhoc）：\n{out}")
        print(f"✓ 签名主体含 {expect_identity}")

    items = scan(app)
    if not items:
        raise SignError(f"{app} 里一个 Mach-O 都没扫到——路径给错了？")
    check_arch(app, items, expect_arch)

    skip = bundle_main_executables(app)
    targets = [m.path for m in items if m.path not in skip]
    unsigned: list[tuple[Path, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for path, ok, out in pool.map(_verify_one, targets):
            if not ok:
                unsigned.append((path, out))
    if unsigned:
        raise SignError(
            f"有 {len(unsigned)} 个 Mach-O 没签名或签名坏了（列前 10 个）：\n  "
            + "\n  ".join(f"{p.relative_to(app)}: {msg[:160]}" for p, msg in unsigned[:10])
        )
    print(
        f"✓ 逐个校验通过：{len(targets)} 个 Mach-O 全部已签名"
        f"（另有 {len(skip)} 个 bundle 主可执行文件随 bundle 一起验）"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sign = sub.add_parser("sign", help="自内向外签整个 .app")
    p_sign.add_argument("--app", required=True)
    p_sign.add_argument("--identity", required=True)
    p_sign.add_argument("--entitlements", default=None)

    p_ver = sub.add_parser("verify", help="验收签名与架构")
    p_ver.add_argument("--app", required=True)
    p_ver.add_argument("--expect-arch", default=None)
    p_ver.add_argument("--expect-identity", default=None)

    p_scan = sub.add_parser("scan", help="列出 Mach-O；可顺带核对架构（签名前用）")
    p_scan.add_argument("--app", required=True)
    p_scan.add_argument(
        "--expect-arch",
        default=None,
        help="断言每个 Mach-O 都含该架构（如 arm64）；不需要签名，因此可以在签名之前跑",
    )
    p_scan.add_argument("--quiet", action="store_true", help="只报统计与核对结果，不逐个列出")

    args = ap.parse_args(argv)
    app = Path(args.app)
    if not app.exists():
        print(f"找不到 {app}", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("::error::这个脚本只在 macOS 上有意义（要用系统的 codesign）", file=sys.stderr)
        return 2

    try:
        if args.cmd == "sign":
            ent = Path(args.entitlements) if args.entitlements else None
            if ent and not ent.is_file():
                raise SignError(f"entitlements 文件不存在: {ent}")
            sign(app, args.identity, ent)
        elif args.cmd == "verify":
            verify(app, args.expect_arch, args.expect_identity)
        else:
            items = scan(app)
            if not args.quiet:
                for mo in items:
                    kind = "exe" if mo.is_executable else "lib"
                    print(f"{kind}  {'/'.join(mo.arches):16} {mo.path.relative_to(app)}")
            print(
                f"共 {len(items)} 个 Mach-O"
                f"（可执行 {sum(1 for m in items if m.is_executable)}），"
                f"本机架构 {_ARCH_ALIASES.get(platform.machine().lower(), '?')}"
            )
            if not items:
                raise SignError(f"{app} 里一个 Mach-O 都没扫到——路径给错了？")
            check_arch(app, items, args.expect_arch)
    except SignError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
