#!/usr/bin/env python3
"""桌面构建：把本目标的私有 Python 归档备进 `build/private-python-bundle/`，供 `packaging/tavotto.spec`
收进 `_internal/private-python/`（ADR 0111）。

    python scripts/stage_private_python.py            # 按本机目标取（下载或复用缓存）并校验
    python scripts/stage_private_python.py --check    # 只校验已备好的那份（spec 用同一个函数）

**唯一输入是 `src/tavotto/resources/private_python_lock.json`**（产品运行时读的同一份锁）：下什么、多大、
sha256 是什么都从 `privatepython.source_for()` 来，这里不另记一遍。下载复用 `build_worker_runtime.download`
（同一个函数、同一个缓存目录 `build/runtime-cache/`）：macOS 两个目标与内置渲染 runtime 是**同一份**
归档（同源对），构建 runtime 时已经下过、这里缓存命中；Windows 的内置 runtime 是 embeddable，
pbs 归档（~47 MB）在这里另下一次。sha256 对不上当场失败，绝不「重下一次算了」。

产物只在 `build/` 下（`.gitignore` 挡、pyproject 的 exclude 再挡一道）：**绝不进 wheel / sdist**。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = ROOT / "build" / "private-python-bundle"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from build_worker_runtime import DEFAULT_CACHE, BuildError, download, sha256_file  # noqa: E402
from tavotto.engine import privatepython  # noqa: E402

#: 发桌面安装包的目标（与 `packaging/runtime-lock.json` 的 shipped 腿对应）；Linux 没有桌面产物。
DESKTOP_TARGETS = ("macos-arm64", "macos-x86_64", "windows-x86_64")


def source(target: str | None = None) -> privatepython.PythonSource:
    name = target or privatepython.host_target()
    if name not in DESKTOP_TARGETS:
        raise BuildError(
            f"目标 {name!r} 没有桌面安装包，不附带私有 Python（认得: {DESKTOP_TARGETS}）"
        )
    src = privatepython.source_for(name)
    if src is None:
        raise BuildError(f"私有 Python 锁文件里没有目标 {name}")
    return src


def stage(
    target: str | None = None, *, cache: Path = DEFAULT_CACHE, out: Path = BUNDLE_DIR
) -> Path:
    """取到（或复用缓存）并校验本目标的归档，放进 `out/`（先清空：目录里只许有这一份）。回归档路径。"""
    src = source(target)
    archive = download(src.url, cache / src.archive_name, src.sha256)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    dest = out / src.archive_name
    shutil.copy2(archive, dest)
    return check_bundle(out, target) or dest


def check_bundle(out: Path = BUNDLE_DIR, target: str | None = None) -> Path | None:
    """已备好的那份配不配得上这次构建（spec 与 `stage` 共用这一把尺）：

    * 目录不存在 → None（没备，是否必须由调用方按 `TAVOTTO_REQUIRE_PRIVATE_PYTHON` 定）；
    * 目录里**恰好一个文件**，名字是本目标的归档名，整份 sha256 等于锁 → 回路径；
    * 其余（多了文件 / 别的目标的归档 / 字节对不上）→ `BuildError`：装进包里的那份运行时会被判不可用、
      退回联网下载，而这件事在构建机上就该拦下。
    """
    if not out.is_dir():
        return None
    src = source(target)
    files = sorted(p.name for p in out.iterdir())
    if files != [src.archive_name]:
        raise BuildError(f"{out} 里应当只有 {src.archive_name}（{src.target}），实际是 {files}")
    path = out / src.archive_name
    got = sha256_file(path)
    if got != src.sha256:
        raise BuildError(f"{path} 的 SHA-256 与锁不符\n  期望 {src.sha256}\n  实得 {got}")
    if path.stat().st_size != src.size:
        raise BuildError(f"{path} 的大小与锁不符（{path.stat().st_size} ≠ {src.size}）")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", help="目标名（缺省为本机，如 macos-arm64 / windows-x86_64）")
    ap.add_argument("--check", action="store_true", help="只校验已备好的那份，不下载")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="下载缓存目录")
    args = ap.parse_args(argv)
    try:
        if args.check:
            path = check_bundle(BUNDLE_DIR, args.target)
            if path is None:
                raise BuildError(
                    f"{BUNDLE_DIR} 不存在——先跑 python scripts/stage_private_python.py"
                )
        else:
            path = stage(args.target, cache=Path(args.cache))
    except BuildError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ 私有 Python 归档: {path}（{path.stat().st_size} 字节，sha256 与锁一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
