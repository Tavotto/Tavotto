"""按改动文件取错题本里命中的条目：评审或动手前只看这几条，不必通读各层。

用法::

    python3 -I scripts/dev/lessons_for_diff.py                  # git diff origin/main...HEAD 的改动文件
    python3 -I scripts/dev/lessons_for_diff.py --paths-file f   # 每行一个路径（- 读 stdin）
    python3 -I scripts/dev/lessons_for_diff.py --all            # 另外列出通用条目（触发: *）
    python3 -I scripts/dev/lessons_for_diff.py --why            # 每条附「为什么」

输出每行：`L-xxx<TAB>层文件<TAB>标题`。命中规则（`/` 结尾 = 目录前缀、`*` 结尾 = 同目录文件名前缀、
否则精确路径）与格式见 `docs/rules/repo/lessons/README.md`，解析与匹配在 `lessons.py`。
没有命中也退出 0（空输出）；取不到改动文件（没有 origin/main 等）退出 2。纯标准库。
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_RANGE = "origin/main...HEAD"


def _lessons():
    """`python3 -I` 不把脚本目录放进 sys.path：按文件名加载同目录的解析器。"""
    spec = importlib.util.spec_from_file_location("tavotto_lessons", HERE / "lessons.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 解析注解时要在 sys.modules 里找到模块
    spec.loader.exec_module(mod)
    return mod


L = _lessons()


def changed_paths(rng: str = DEFAULT_RANGE, root: Path = ROOT) -> list[str]:
    """`git diff --name-only --no-renames <范围>`：改名按「删旧 + 加新」各算一个路径。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "-z", "--name-only", "--no-renames", rng],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError) as e:
        detail = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace").strip()
        raise L.LessonsError(f"取不到 git diff {rng}：{detail or e}") from None
    return [p for p in out.split("\0") if p]


def read_paths(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return [ln.strip().replace("\\", "/") for ln in text.splitlines() if ln.strip()]


def select(lessons, paths: list[str], *, include_universal: bool = False):
    """命中的未升级条目（编号序）；通用条目只在 include_universal 时附上。"""
    out = []
    for x in lessons:
        if x.status != "active":
            continue
        if x.universal:
            if include_universal:
                out.append(x)
        elif x.triggered_by(paths):
            out.append(x)
    return sorted(out, key=lambda x: x.number)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths-file", help="改动文件清单，每行一个；- 读标准输入")
    ap.add_argument("--range", default=DEFAULT_RANGE, help=f"git 范围（默认 {DEFAULT_RANGE}）")
    ap.add_argument("--all", action="store_true", help="另外列出通用条目（触发: *）")
    ap.add_argument("--why", action="store_true", help="每条后附「为什么」")
    ap.add_argument("--dir", type=Path, default=L.LESSONS)
    args = ap.parse_args(argv)
    try:
        lessons = L.load(args.dir)
        paths = read_paths(args.paths_file) if args.paths_file else changed_paths(args.range)
    except (L.LessonsError, OSError) as e:
        print(e, file=sys.stderr)
        return 2
    hits = select(lessons, paths, include_universal=args.all)
    for x in hits:
        mark = "（通用）" if x.universal else ""
        print(f"{x.id}\tdocs/rules/repo/lessons/{x.layer}.md\t{mark}{x.rule}")
        if args.why:
            print(f"\t为什么: {x.why}")
    if not args.all:
        n = sum(1 for x in lessons if x.status == "active" and x.universal)
        if n:
            print(f"另有 {n} 条通用条目未列，--all 列出", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
