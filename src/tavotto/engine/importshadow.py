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


def _fact(root: Path, parent: str, missing: str, *, relation: str) -> dict:
    parent_dir = root.joinpath(*parent.split("."))
    init = parent_dir / "__init__.py"
    regular = init.is_file()
    leaf = missing.rsplit(".", 1)[-1]
    imports_it = bool(regular and re.search(rf"\b{re.escape(leaf)}\b", _read_init(init)))
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
            f" That folder has no `__init__.py`, so an installed regular `{pkg}` package takes precedence over it: "
            f"installing the distribution that provides `{pkg}` can fix this."
        )
    return head + tail


def detect(text: str | None, roots: list[Path | str | None]) -> dict | None:
    """错误文本 → 遮蔽事实；不是这一类（含读不出来）回 `None`。`roots`：项目根、脚本所在目录（按 `sys.path` 的先后）。"""
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
            for root in candidates:
                pdir = root.joinpath(*parent.split("."))
                if pdir.is_dir() and not _child_exists(pdir, child):
                    return _fact(root, parent, name, relation="missing_submodule")
    m = None
    for m in _CANNOT_IMPORT_RE.finditer(text):
        pass
    if m is not None:
        path = Path(m.group("path").strip())
        for root in candidates:
            if _within(path, root):
                pkg = m.group("pkg")
                if root.joinpath(*pkg.split(".")).is_dir():
                    return _fact(root, pkg, f"{pkg}.{m.group('attr')}", relation="missing_name")
    return None
