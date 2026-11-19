"""包里的脚本用相对导入时，按包里的模块来运行（ADR 0196，维护者 2026-11-19 裁决）。

`plots/__init__.py` + `plots/helpers.py` + `plots/fig.py`（`from .helpers import tidy`）：直接 `python plots/fig.py` 必然
`ImportError: attempted relative import with no known parent package`——**原样跑必然失败**，所以这里改跑法不违背
「与 `python fig.py` 一致」。只在这个形状上生效：本模块回答一个问题——**这份脚本该不该按 `python -m 包.模块` 的方式跑**，
是则给出完整模块名（`plots.fig`）；`sys.path[0]`（包根的上一级）由执行侧从脚本路径往上数层得出
（`bridgeboot.package_path_root`，safe worker 与 native bridge 同一个），子进程只收模块名。

## 判据（AST，零执行；判不出就不改，一律回 None）

全部成立才算：

1. 脚本的登记入口是 `__main__`（`entry` 为 `None` = native 整份脚本运行，没有登记入口，同样算）——函数入口走 `import_module`，
   不在这里；
2. 脚本顶层的**直接语句**里有相对导入（`ImportFrom.level > 0`）。嵌在 `try` / `if` / `def` 里的不算：
   `try: from .h import x / except ImportError: from h import x` 今天能跑，按模块跑会丢掉脚本目录这个 `sys.path[0]`；
3. 所有顶层相对导入的 `level` 都不超过包的层数（`plots.fig` 只有一层包，`from .. import x` 在 `python -m` 下同样失败）；
4. 从脚本所在目录往上，每一级都有 `__init__.py`（普通文件，不是符号链接），包根 = 最上面那个带 `__init__.py` 的目录；
   命名空间包（没有 `__init__.py`）不算；
5. 包根最多到项目文件夹为止：项目文件夹本身就是包（项目根里有 `__init__.py` 且整条链都有）时，包链把项目文件夹名算进去、
   `sys.path[0]` 是项目的上一级（维护者 2026-11-19 裁决：也支持）；项目上一级再有 `__init__.py` 也不往外爬；
6. 路径上没有符号链接 / junction（脚本、每一级目录、每个 `__init__.py` 都不跟随），读脚本走有界的普通文件读法；
7. 每一级包名与脚本 stem 都是合法标识符（`my-plots/`、`fig.v2.py` 不改），stem 不是 `__init__` / `__main__`。

纯标准库；Flask 父进程 import（`execspec` 的装配点调它）。判据只在父进程跑，子进程不 import 本模块。
"""

from __future__ import annotations

import ast
import dataclasses
import os
import stat

from . import figcapture, scanbudget

ENTRY_MAIN = "__main__"


@dataclasses.dataclass(frozen=True)
class PackageEntry:
    """判据成立时的结论。"""

    #: 完整点分模块名（`plots.fig`）。
    module: str
    #: 包根的上一级目录（绝对、未解析符号链接）= `python -m` 下的 `sys.path[0]`。信息性：执行侧自己从脚本路径推。
    root: str


def _is_plain_file(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not scanbudget.is_redirect(st)


def _is_plain_dir(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not scanbudget.is_redirect(st)


def _sets_own_package_context(tree: ast.Module) -> bool:
    """脚本顶层代码（不进函数 / 类体）自己给 `__package__` / `__spec__` 赋值：常见的自举写法
    （`if __name__ == "__main__" and __package__ is None: __package__ = "plots"` + 改 `sys.path`）。这种脚本按脚本跑本来就行，
    改成按模块跑反而把脚本目录挪出 `sys.path`、它的绝对同级 import 会断（Codex #2055 P2）——判据对它一律不改。"""
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(st):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, (ast.AugAssign, ast.AnnAssign))
                else []
            )
            if any(
                isinstance(t, ast.Name) and t.id in ("__package__", "__spec__") for t in targets
            ):
                return True
    return False


def _relative_levels(tree: ast.Module) -> list[int]:
    """脚本顶层**直接语句**里的相对导入层数（`from . import x` = 1）。"""
    return [n.level for n in tree.body if isinstance(n, ast.ImportFrom) and (n.level or 0) > 0]


def resolve(project_root, script: str, entry: str | None = ENTRY_MAIN) -> PackageEntry | None:
    """判据（见模块头）。`script` 是项目相对路径；任何读不了 / 解析不了 / 不确定都回 None。"""
    if entry not in (None, ENTRY_MAIN):
        return None
    try:
        rel = figcapture.normalize_relative_script(script)
    except ValueError:
        return None
    parts = rel.split("/")
    if not rel.endswith(".py") or any(p in ("", ".", "..") for p in parts):
        return None
    dirs, stem = parts[:-1], parts[-1][:-3]
    if stem in ("__init__", "__main__"):
        return None
    root = os.path.abspath(os.fspath(project_root))

    def dir_path(n: int) -> str:
        return os.path.join(root, *dirs[:n])

    # 便宜的先行判断：脚本所在目录必须直接带 `__init__.py`——普通脚本只付这一次 stat
    if not _is_plain_file(os.path.join(dir_path(len(dirs)), "__init__.py")):
        return None
    # 路径上的每一级目录都不能是符号链接 / junction（包根越界的主要途径）
    if not all(_is_plain_dir(dir_path(n)) for n in range(1, len(dirs) + 1)):
        return None
    # 往上放宽：`n` 是「dirs[:n] 这一级带 `__init__.py`」的最小 n，包链 = dirs[n - 1:]。
    # 脚本就在项目根（`dirs` 为空）时，上面那次检查看的是项目根自己的 `__init__.py`：链从项目文件夹开始（下面那一支）
    n = max(len(dirs), 1)
    while n > 1 and _is_plain_file(os.path.join(dir_path(n - 1), "__init__.py")):
        n -= 1
    chain_start = n - 1 if dirs else 0
    names = [*dirs[chain_start:], stem]
    path_root = dir_path(chain_start)
    if chain_start == 0 and _is_plain_file(os.path.join(root, "__init__.py")):
        # 项目文件夹本身也是包（维护者 2026-11-19 裁决：也支持）：包链把项目文件夹名算进去，`sys.path[0]` 是项目的上一级，
        # 与在项目上一级 `python -m <项目名>.….<脚本>` 一致。**只到项目文件夹为止**：它的上一级再有 `__init__.py` 也不往外爬
        top = os.path.basename(root)
        parent = os.path.dirname(root)
        if (
            not top
            or not parent
            or parent == root
            or os.path.islink(root)
            or not _is_plain_dir(root)
        ):
            return None
        names = [top, *names]
        path_root = parent
    if not all(part.isidentifier() for part in names):
        return None
    try:
        text = scanbudget.read_regular_text(
            os.path.realpath(root), *parts, no_follow=True, python_source=True
        )
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    levels = _relative_levels(tree)
    if not levels or max(levels) > len(names) - 1:
        return None
    if _sets_own_package_context(tree):
        return None
    return PackageEntry(module=".".join(names), root=path_root)
