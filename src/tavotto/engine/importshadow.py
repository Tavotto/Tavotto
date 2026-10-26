"""项目里的同名包遮蔽了真正要 import 的包：把「缺的到底是什么」说准。

典型形状（真实科研语料 celerite）：项目根里躺着半截 `celerite/__init__.py`，它 `from .celerite_version import …`，
而 `celerite_version.py` 是**构建时才生成**的。运行时 Python 先找到项目里的 `celerite/`（项目根在 `sys.path` 前面），
于是报 `No module named 'celerite.celerite_version'`。看到的人（和模型）容易读成「缺 `celerite` 这个包」，去 pip 装同名包——
装了也没用：项目里的文件夹仍然先被找到。

本模块只做**判据**：输入是一段错误文本（异常消息 / traceback 尾巴）与候选根目录（项目根、脚本所在目录），
对着**真实文件系统**回一个事实字典或 `None`。纯函数、只读、不执行任何用户代码、不联网。

* 主语：「失败的那一级 import 的**父包**，是从项目目录里解析到的」——父包目录在候选根下存在，而缺的子模块不在里面。
  （`ModuleNotFoundError` 里 `No module named 'a.b'` 说明 `a` 已经导入成功；顶层名缺失时没有父包，不属于这一类。）
* 两种形状：`regular`（有 `__init__.py`，常规包，pip 装同名包**不能**改变解析结果）与 `namespace`（只是个没有
  `__init__.py` 的目录，已安装的常规包优先于它，所以装真包通常能修）。
* `ImportError: cannot import name 'x' from 'pkg' (/项目内/pkg/__init__.py)`：括号里的路径落在候选根内也算。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NO_MODULE_RE = re.compile(r"No module named ['\"](?P<name>[A-Za-z_][\w.]*)['\"]")
_CANNOT_IMPORT_RE = re.compile(
    r"cannot import name ['\"](?P<attr>[^'\"]+)['\"] from ['\"](?P<pkg>[A-Za-z_][\w.]*)['\"]"
    r" \((?P<path>[^)\n]+)\)"
)
_EXT_SUFFIXES = (".py", ".pyc", ".so", ".pyd", ".dylib")
_INIT_READ_BYTES = 64 * 1024
_EXTENDS_PATH_RE = re.compile(r"\b(?:extend_path|declare_namespace)\s*\(")
_BUILD_FILES = ("setup.py", "pyproject.toml", "setup.cfg", "CMakeLists.txt", "meson.build")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _child_exists(parent_dir: Path, child: str) -> bool:
    """`parent_dir` 里有没有叫 `child` 的子包 / 子模块（目录、`.py`、编译扩展）。读不了目录时当作有：不下「不存在」的结论。"""
    if (parent_dir / child).is_dir():
        return True
    try:
        names = os.listdir(parent_dir)
    except OSError:
        return True
    return any(n.split(".", 1)[0] == child and n.endswith(_EXT_SUFFIXES) for n in names)


def _read_init(init: Path) -> str:
    try:
        with open(init, "rb") as f:
            return f.read(_INIT_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def select_package(parent: str, roots: list[Path]) -> tuple[Path, list[Path]] | None:
    """Python 会用项目 `roots`（按 `sys.path` 先后）里的哪一份 `parent` 包？回 `(代表根, 全部相关根)`；项目里没有回 `None`。

    对应 `importlib.machinery.PathFinder` 与 PEP 420 的规则：沿 `sys.path` 逐项找，**第一个**含 `parent/__init__.py`
    的位置胜出（regular 包），哪怕更前面有没有 `__init__.py` 的同名目录（那只是 namespace 的一个 portion，
    记下来继续找）；只有所有位置都没有 regular 包时，才由全部 portion 合成 namespace 包。项目根都排在
    site-packages 之前，所以项目里没有 regular 包时，已安装的 regular 包仍会胜出过 namespace portion——这时
    安装能修，代表根取第一个 portion。胜出的 regular 包的 `__init__.py` 若用 `extend_path` / `declare_namespace`，
    它的 `__path__` 会并入其它同名目录（`_fact` 据此把它按 namespace 说）：这里把其余含该目录的根也算作相关根。"""
    rel = parent.split(".")
    portions: list[Path] = []
    for root in roots:
        pdir = root.joinpath(*rel)
        if not pdir.is_dir():
            continue
        if (pdir / "__init__.py").is_file():
            others = [r for r in roots if r.joinpath(*rel).is_dir()]
            return root, (
                others if _EXTENDS_PATH_RE.search(_read_init(pdir / "__init__.py")) else [root]
            )
        portions.append(root)
    return (portions[0], portions) if portions else None


def _fact(root: Path, parent: str, missing: str, *, relation: str) -> dict:
    parent_dir = root.joinpath(*parent.split("."))
    init = parent_dir / "__init__.py"
    init_text = _read_init(init) if init.is_file() else ""
    # `pkgutil.extend_path` / `declare_namespace` 的 `__init__.py` 会把 sys.path 上其它同名目录并进来：
    # 装上的发行版里的子模块照样能导入，行为上等同 namespace，不能说「装了也没用」
    regular = init.is_file() and not _EXTENDS_PATH_RE.search(init_text)
    leaf = missing.rsplit(".", 1)[-1]
    imports_it = bool(regular and re.search(rf"\b{re.escape(leaf)}\b", init_text))
    try:
        rel = parent_dir.relative_to(root).as_posix() + "/"
    except ValueError:
        rel = parent_dir.name + "/"
    fact: dict = {
        "kind": "regular" if regular else "namespace",
        "package": parent,
        "missing": missing,
        "project_path": rel,
        "relation": relation,  # missing_submodule | missing_name
        "init_imports_it": imports_it,
        "project_has_build_files": any((root / b).exists() for b in _BUILD_FILES),
    }
    fact["note"] = describe(fact)
    return fact


def describe(fact: dict) -> str:
    """给人 / 模型看的一段话（英文，修复会话的语言）。事实全部来自 `fact`，不另加推测。"""
    pkg, missing, path = fact["package"], fact["missing"], fact["project_path"]
    if fact["relation"] == "missing_name":
        head = (
            f"`{missing}` is not defined in `{pkg}`, and `{pkg}` is the copy inside this project (`{path}`), "
            f"not an installed package."
        )
    else:
        head = (
            f"The failing import is `{missing}`, not `{pkg}`. Python found `{pkg}` in the project's own folder `{path}` "
            f"and that folder has no `{missing.rsplit('.', 1)[-1]}`."
        )
    if fact["kind"] == "regular":
        tail = (
            " That folder is a regular package, so it is found before anything installed: installing a package named "
            f"`{pkg}` with pip will NOT change this. Do not install it."
        )
        if fact["init_imports_it"]:
            tail += (
                f" Its `__init__.py` imports it, which usually means `{missing.rsplit('.', 1)[-1]}` is generated "
                "when the project is built or installed"
                + (" (the project has build files)" if fact["project_has_build_files"] else "")
                + "."
            )
        tail += (
            " Tell the user the project's `"
            + path
            + "` folder is incomplete and needs the project's own build/install step; never edit the script."
        )
    else:
        tail = (
            f" That folder is not a regular package (it has no `__init__.py`, or its `__init__.py` extends the package path), so the installed `{pkg}` package can still be found: "
            f"installing the distribution that provides `{pkg}` can fix this."
        )
    return head + tail


_FRAME_PATH_RE = re.compile(r'File "(?P<path>[^"\n]+)"')


def _frame_in(text: str, pkg_dir: Path) -> bool:
    """traceback 里是否有一帧的文件落在 `pkg_dir` 之内——失败的进程真的从项目里加载过这个包的证据。"""
    for m in _FRAME_PATH_RE.finditer(text):
        if _within(Path(m.group("path")), pkg_dir):
            return True
    return False


def detect(
    text: str | None, roots: list[Path | str | None], *, project_on_path: bool = True
) -> dict | None:
    """错误文本 → 遮蔽事实；不是这一类（含读不出来）回 `None`。`roots`：按 `sys.path` 的先后（脚本目录在前，再项目根）。

    `project_on_path`：失败的那个进程是否把项目放在 `sys.path` 上（真实运行 / 试运行副本：是；`try_import` 在全新
    scratch 目录里跑：否）。**不在路径上时，项目里有同名目录说明不了什么**——失败可能完全来自 site-packages——
    所以只在 traceback 里确有一帧落在项目的那个包目录内（来源证据）时才下结论。"""
    if not text:
        return None
    candidates = [Path(r) for r in roots if r]
    if not candidates:
        return None
    # 取最后一处：错误尾巴里最靠后的才是这次失败的那个异常
    hits = list(_NO_MODULE_RE.finditer(text))
    if hits:
        name = hits[-1].group("name")
        if "." in name:
            parent, child = name.rsplit(".", 1)
            winner = select_package(parent, candidates)
            if winner is not None:
                root, portions = winner
                # 没有来源证据就不下结论：项目不在 sys.path 上时，失败可能完全来自 site-packages
                if project_on_path or any(
                    _frame_in(text, root.joinpath(*parent.split("."))) for root in portions
                ):
                    if not any(
                        _child_exists(r.joinpath(*parent.split(".")), child) for r in portions
                    ):
                        return _fact(root, parent, name, relation="missing_submodule")
    m = None
    for m in _CANNOT_IMPORT_RE.finditer(text):
        pass
    if m is not None:
        path = Path(m.group("path").strip())
        for root in candidates:
            if _within(path, root):  # 括号里的路径本身就是来源证据
                pkg = m.group("pkg")
                if root.joinpath(*pkg.split(".")).is_dir():
                    return _fact(root, pkg, f"{pkg}.{m.group('attr')}", relation="missing_name")
    return None
