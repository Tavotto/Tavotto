"""项目内的 R / MATLAB 脚本事实（ADR 0143 修订「项目内入口」）。

「这张图旁边有同名的 R / MATLAB 脚本」和「项目里有哪些 R / MATLAB 脚本」是**后端的事实**，前端只翻译：

* `sibling_of(root, asset_id)`：素材同目录下、与素材同主干名的 `.R` / `.r` / `.m`，且 `sourcelang.detect_file`
  认得出语言（≤ 1 MB、UTF-8、`.m` 的 Obj-C 一票否决）。多个文件都认得出（Linux 上 `fig.R` 与 `fig.r` 共存、
  或 `fig.R` 与 `fig.m` 并列）就**不给**——不猜；`.Rmd` / `.qmd` / `.mlx` 因后缀不符本来就不是候选；
* `inventory(root)`：项目内全部认得出语言的 R / MATLAB 脚本，给脚本库列出（walk 规则与 `.py` 同一份，
  见 `discover.iter_foreign_scripts`）。

边界：只读（`scandir` / `stat` / 读 ≤ 1 MB 内容），不执行任何东西，不进注册表 / 探测 / 依赖准备 / readiness 的“可运行脚本”判据；
不运行 R / MATLAB。纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from . import discover, sourcelang

#: 与素材同主干名的候选后缀（区分大小写，与 `discover.FOREIGN_SUFFIXES` 同一份）
SUFFIXES = discover.FOREIGN_SUFFIXES


class DirCache:
    """一次计算内共用的“目录 → 其中的文件名集合”，同一目录只列一次。"""

    def __init__(self, root: Path):
        self.root = root
        self._names: dict[str, frozenset[str]] = {}

    def names(self, rel_dir: str) -> frozenset[str]:
        cached = self._names.get(rel_dir)
        if cached is None:
            found: set[str] = set()
            try:
                with os.scandir(self.root / rel_dir if rel_dir else self.root) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                found.add(entry.name)
                        except OSError:
                            continue
            except OSError:
                pass
            cached = self._names[rel_dir] = frozenset(found)
        return cached


def sibling_names(cache: DirCache, asset_id: str) -> list[str]:
    """素材同目录下与它同主干名的 R / MATLAB 候选文件的项目相对路径（只看文件名，不读内容；顺序固定）。

    名字必须逐字匹配（`scandir` 的真实文件名，不是 `stat` 的结果）：不区分大小写的文件系统上，
    `fig.r` 的 `stat` 会命中 `fig.R`，那会把同一个文件报成两个。"""
    rel = PurePosixPath(asset_id.replace("\\", "/"))
    parent = rel.parent.as_posix()
    parent = "" if parent == "." else parent
    present = cache.names(parent)
    out: list[str] = []
    for suffix in SUFFIXES:
        name = rel.stem + suffix
        if name in present:
            out.append(f"{parent}/{name}" if parent else name)
    return out


def signature_of(root: Path, cache: DirCache, asset_ids: list[str]) -> list[list]:
    """候选文件的 `(路径, 大小, mtime_ns)`：readiness 报告缓存键的一部分（候选变了，报告要重算）。"""
    parts: list[list] = []
    for asset_id in asset_ids:
        for rel in sibling_names(cache, asset_id):
            try:
                st = (root / rel).stat()
            except OSError:
                continue
            parts.append([rel, st.st_size, st.st_mtime_ns])
    return parts


def sibling_of(root: Path, cache: DirCache, asset_id: str) -> dict | None:
    """素材的同名外语脚本 `{"script": <项目相对路径>, "language": "r" | "matlab"}`；没有 / 判不出 / 有歧义回 None。"""
    found: list[dict] = []
    for rel in sibling_names(cache, asset_id):
        language = sourcelang.detect_file(root / rel)
        if language in sourcelang.FOREIGN:
            found.append({"script": rel, "language": language})
    return found[0] if len(found) == 1 else None


def inventory(root: Path) -> list[dict]:
    """项目内认得出语言的 R / MATLAB 脚本，按路径排序：`[{"script", "language"}]`。判不出语言的不列。"""
    out: list[dict] = []
    for path in discover.iter_foreign_scripts(root):
        language = sourcelang.detect_file(path)
        if language in sourcelang.FOREIGN:
            out.append({"script": discover.rel_key(path, root), "language": language})
    return sorted(out, key=lambda item: item["script"])
