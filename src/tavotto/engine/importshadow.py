"""项目里的同名包遮蔽了真正要 import 的包：把「缺的到底是什么」说准。

典型形状（真实科研语料 celerite）：项目根里躺着半截 `celerite/__init__.py`，它 `from .celerite_version import …`，
而 `celerite_version.py` 是**构建时才生成**的。环境里**没装** `celerite` 时，Python 只能找到项目里的 `celerite/`
（项目根在 `sys.path` 的**末尾**，site-packages 之后），于是报 `No module named 'celerite.celerite_version'`。看到的人（和模型）容易读成「缺 `celerite` 这个包」，去 pip 装同名包——
「装了也没用」只对**脚本所在目录**里的同名包成立（`sys.path[0]`，先于一切）；对**项目根**里的同名包不成立：
safe worker 把项目根追加在末尾（`worker._put_project_on_path`），已安装的同名包排在它前面，装上就能修。所以：

* 脚本目录里的同名包（`roots`）：先于 site-packages，只看文件系统就能认定；
* 项目根里的同名包（`late_roots`）：排在 site-packages 之后，**只有 traceback 里确有一帧落在这个包目录内**
  （进程真的加载过它）才认——没装同名包时才会走到它，装了的话失败来自别处。

本模块只做**判据**：输入是一段错误文本（异常消息 / traceback 尾巴）与候选根目录（项目根、脚本所在目录），
对着**真实文件系统**回一个事实字典或 `None`。纯函数、只读、不执行任何用户代码、不联网。

* 主语：「失败的那一级 import 的**父包**，是从项目目录里解析到的」——父包目录在候选根下存在，而缺的子模块不在里面。
  （`ModuleNotFoundError` 里 `No module named 'a.b'` 说明 `a` 已经导入成功；顶层名缺失时没有父包，不属于这一类。）
* 两种形状：`regular`（有 `__init__.py`，常规包，pip 装同名包**不能**改变解析结果）与 `namespace`（只是个没有
  `__init__.py` 的目录，已安装的常规包优先于它，所以装真包通常能修）。
* `ImportError: cannot import name 'x' from 'pkg' (/项目内/pkg/__init__.py)`：括号里的路径落在候选根内也算。
"""

from __future__ import annotations

import ast
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
_PATH_EXTENDERS = frozenset({"extend_path", "declare_namespace"})


def _extends_path(init_text: str) -> bool:
    """`__init__.py` 里真的**调用**了 `pkgutil.extend_path` / `pkg_resources.declare_namespace`（按 AST 认调用，
    注释与文档字符串里提到不算）。解析不了的文件按没有调用处理——宁可说「别装」也不误导去装。"""
    try:
        tree = ast.parse(init_text)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = (
                f.attr
                if isinstance(f, ast.Attribute)
                else f.id
                if isinstance(f, ast.Name)
                else None
            )
            if name in _PATH_EXTENDERS:
                return True
    return False


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


def select_package(parent: str, roots: list[Path]) -> tuple[Path, list[Path], str] | None:
    """Python 会用项目 `roots`（按 `sys.path` 先后）里的哪一份 `parent` 包？回 `(代表根, 全部相关根, kind)`；
    项目里没有回 `None`。`kind`：`regular`（项目自有，装同名包不管用）或 `namespace`（装上能修）。

    对应 `importlib.machinery.PathFinder` 与 PEP 420 的规则，**从顶层包开始逐级解析**：

    1. 沿 `sys.path` 逐项找顶层名，**第一个**含 `top/__init__.py` 的位置胜出（regular 包），哪怕更前面有没有
       `__init__.py` 的同名目录（那只是 namespace 的一个 portion，记下继续找）。传进来的 `roots` 都排在
       site-packages 之前（脚本目录）；排在它之后的项目根由 `detect` 单独处理（`late_roots`）。
    2. 胜出的 regular 包把 `top.__path__` 限定为它自己的目录：它下面**所有**子包（含没有 `__init__.py` 的
       `top/sub/`）都只在这个 `__path__` 里找，装上的 `top/sub/extra.py` 看不见 → 结论是项目自有（`regular`）。
    3. 例外：它的 `__init__.py` 用 `extend_path` / `declare_namespace` 时，`__path__` 会并入其它同名目录，
       等同 namespace，继续往下一级找。
    4. 项目里所有位置都没有 regular 包时，由全部 portion 合成 namespace 包；已安装的 regular 包仍会胜出过
       namespace portion，所以安装能修。再往下一级，在这些 portion 里重复本规则。"""
    top, _, rest = parent.partition(".")
    portions: list[Path] = []
    for root in roots:
        tdir = root / top
        if not tdir.is_dir():
            continue
        init = tdir / "__init__.py"
        if init.is_file():
            if _extends_path(_read_init(init)):
                # 第一个 regular 包胜出，但它把 `__path__` 并入其它同名目录：等同 namespace，其余位置也算相关根
                portions = [r for r in roots if (r / top).is_dir()]
                break
            # regular 且不并路径：`__path__` 只有它自己，下面的一切都是它的
            return (
                (root, [root], "regular")
                if (not rest or (tdir / rest.replace(".", "/")).is_dir())
                else None
            )
        portions.append(
            root
        )  # namespace portion，或 extend_path 的 regular 包（__path__ 会并入其它同名目录）
    if not portions:
        return None
    if not rest:
        return portions[0], portions, "namespace"
    deeper = select_package(rest, [r / top for r in portions])
    if deeper is None:
        return None
    root, found, kind = deeper
    return root.parent, [r.parent for r in found], kind


def _fact(
    root: Path,
    parent: str,
    missing: str,
    *,
    relation: str,
    kind: str | None = None,
    after_site: bool = False,
) -> dict:
    parent_dir = root.joinpath(*parent.split("."))
    init = parent_dir / "__init__.py"
    init_text = _read_init(init) if init.is_file() else ""
    # `pkgutil.extend_path` / `declare_namespace` 的 `__init__.py` 会把 sys.path 上其它同名目录并进来：
    # 装上的发行版里的子模块照样能导入，行为上等同 namespace，不能说「装了也没用」
    regular = (kind == "regular") if kind else (init.is_file() and not _extends_path(init_text))
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
        #: 这份项目内的包在 `sys.path` 的哪一段：`before_site`（脚本目录，先于已装的包）| `after_site`（项目根，已装的同名包优先）
        "search_order": "after_site" if after_site else "before_site",
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
    if fact.get("search_order") == "after_site":
        # 项目根排在 site-packages 之后：走到项目里这一份，说明环境里没有别的同名包能用
        tail = (
            f" The project root is searched after installed packages, so Python only used this copy because no usable installed `{pkg}` "
            f"was found. Installing the distribution that provides `{pkg}` can fix this (the installed one is found first). "
            f"If the folder is a library source tree that was never built, building it is the alternative. Never edit the script."
        )
    elif fact["kind"] == "regular":
        tail = (
            " That folder belongs to a regular package in the project, so it is found before anything installed: installing a package named "
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


_IMPORT_ERRORS = ("ModuleNotFoundError", "ImportError")
#: traceback 的终止异常行：行首（可带缩进）是异常类名（可带模块前缀），后跟冒号或行尾
_EXC_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Exit|Interrupt|Warning)\b(?::|$)"
)


def _terminal_exception(text: str) -> str | None:
    """traceback 文本里最后一个异常行（去掉前导空白），认不出返回 `None`。"""
    last = None
    for line in text.splitlines():
        if _EXC_LINE_RE.match(line):
            last = line.strip()
    return last


def detect(
    text: str | None,
    roots: list[Path | str | None],
    *,
    project_on_path: bool = True,
    late_roots: list[Path | str | None] | tuple[Path | str | None, ...] = (),
) -> dict | None:
    """错误文本 → 遮蔽事实；不是这一类（含读不出来）回 `None`。`roots`：排在 site-packages **之前**的目录，按
    `sys.path` 先后（脚本目录）。`late_roots`：排在 site-packages **之后**的目录（safe worker 追加在末尾的项目根）：
    那里的同名包只有环境里没装同名包时才会被 import，所以必须有 traceback 帧落在它的包目录内才认。

    `project_on_path`：失败的那个进程是否把项目放在 `sys.path` 上（真实运行 / 试运行副本：是；`try_import` 在全新
    scratch 目录里跑：否）。**不在路径上时，项目里有同名目录说明不了什么**——失败可能完全来自 site-packages——
    所以只在 traceback 里确有一帧落在项目的那个包目录内（来源证据）时才下结论。"""
    if not text:
        return None
    candidates = [Path(r) for r in roots if r]
    late = [Path(r) for r in late_roots if r and Path(r) not in candidates]
    if not candidates and not late:
        return None
    # 只看**最终抛出**的那个异常：脚本 `except ModuleNotFoundError:` 接住可选子模块、再抛别的错时，前面那次 import
    # 失败不是这次的原因。traceback 里认得出终止异常行时，它不是 import 类就不下结论，且只在那一行里匹配；
    # 认不出（只给了一句消息）就照旧看整段文本。来源证据（`_frame_in`）仍按整段 traceback 找。
    terminal = _terminal_exception(text)
    if terminal is not None and not terminal.startswith(_IMPORT_ERRORS):
        return None
    scope = terminal if terminal is not None else text
    hits = list(_NO_MODULE_RE.finditer(scope))
    if hits:
        name = hits[-1].group("name")
        if "." in name:
            parent, child = name.rsplit(".", 1)
            # 整条 sys.path 一起解析（脚本目录在前、项目根在后）；winner 落在项目根（`late`）里 = 在 site-packages 之后
            winner = select_package(parent, [*candidates, *late])
            after_site = winner is not None and winner[0] in late and winner[0] not in candidates
            needs_evidence = not project_on_path or after_site
            if winner is not None:
                root, portions, kind = winner
                # 没有来源证据就不下结论：项目不在 sys.path 上时，失败可能完全来自 site-packages
                if not needs_evidence or any(
                    _frame_in(text, root.joinpath(*parent.split("."))) for root in portions
                ):
                    if not any(
                        _child_exists(r.joinpath(*parent.split(".")), child) for r in portions
                    ):
                        return _fact(
                            root,
                            parent,
                            name,
                            relation="missing_submodule",
                            kind=kind,
                            after_site=after_site,
                        )
    m = None
    for m in _CANNOT_IMPORT_RE.finditer(scope):
        pass
    if m is not None:
        path = Path(m.group("path").strip())
        for root in [*candidates, *late]:
            if _within(path, root):  # 括号里的路径本身就是来源证据
                pkg = m.group("pkg")
                if root.joinpath(*pkg.split(".")).is_dir():
                    return _fact(
                        root,
                        pkg,
                        f"{pkg}.{m.group('attr')}",
                        relation="missing_name",
                        after_site=root in late and root not in candidates,
                    )
    return None
