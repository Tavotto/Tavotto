"""「只定义了画图函数」的库模块：运行之前认出来，并反查是哪个脚本 import 了它（反馈包 TVD-Y5JF）。

用户把代码仓库里的**函数库模块**（一堆 `def plot_xxx(df, ...)`，数据准备与调用在另一个入口脚本里）当成画图脚本
拖进来运行：跑完一张图都没有（`no_figures_captured_silent`），在那之前还可能先为它装了几分钟依赖。本模块只回答
两个问题，**不执行脚本、不 import 任何用户模块**：

1. **这个脚本是不是库模块**（`is_library`）。主语是「产品此刻会怎么跑它」：safe worker 要么跑顶层代码（`__main__`），
   要么按 `discover.entry_candidates_of` 调一个零参入口函数。判据写成正面形式，全部成立才算：
   * 注册表登记的 entry 不是函数（是函数 = 产品会调它，不是库）；
   * 没有 `if __name__ == ...` 守卫，`discover._toplevel_reaches_plot` 为假，`entry_candidates_of` 只剩兜底的
     `__main__`（没有零参的 main / render / 自定义画图函数）——与产品挑入口**同一份**算法，不另写；
   * 至少有一个顶层函数够得着画图调用（`discover.PLOT_FUNCS`）——不画图的工具模块不是这里要说的事；
   * 顶层语句只有：docstring、import（`import *` 不算）、def / class、赋值、表达式调用，以及只包着这些的
     `try` 和 `if TYPE_CHECKING` / `if sys.version_info ...`；顶层的每一个调用都**不**指向本文件定义的函数、
     不指向从本地模块导入的名字、最后一段不在 `PLOT_FUNCS` 里（`matplotlib.use("Agg")`、
     `Cmap = LinearSegmentedColormap.from_list(...)` 这类配置与常量构造照样算库）。
   其余一律不算（`for` / `with` / 普通 `if` / 顶层调本地函数 / 读不了 / 语法错误……）：**判不出就不说**。
   结果只是一句不阻塞的提示，主按钮之外永远留着「仍然运行」——说错的代价是多点一下，但仍以不说错为先。

2. **谁 import 了它**（`find_entries`）。搜索范围：从脚本目录往上找最近的仓库标记（`.git` / `pyproject.toml` /
   `setup.py` / `setup.cfg`），标记目录在项目根**之外**且不是主目录（及其祖先）、不是盘符根时，用它；否则只在
   项目根里找。遍历只经 `discover._iter_py`（同一套剪枝：隐藏目录、虚拟环境、缓存、`MAX_DEPTH`；符号链接 /
   junction 不跟进），读文件只经 `scanbudget.read_regular_text(no_follow=True)`，云盘占位文件不读；墙钟与文件数
   有上限，超了如实标 `complete=False`。
   「import 了它」不按名字猜：先用源码里出现模块名这个便宜条件筛一遍，再对候选做 `importscan.scan` 的**正向**解析
   （包、相对导入、`from pkg import mod` 都由它按真实搜索根解），目标文件在它读到的本地文件里才算。候选自己也是
   库模块时不是入口，再往上追一层（有界）。入口按「直接 import 的 → 在项目里的 → 有 `__main__` 守卫的 → 路径短的」排序。

结果是本机报告的一部分：项目外的入口带绝对路径（前端要用它打开那个文件夹），只进 requirement 载荷，
**不进检查项 detail、诊断包、日志与遥测**。纯标准库；被 `prepsession` import（Flask 父进程 import 链）。
"""

from __future__ import annotations

import ast
import builtins
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from . import discover, execspec, importscan, scanbudget

#: 仓库标记：从脚本目录往上找到的第一个带它们之一的目录就是「这个仓库」。
REPO_MARKERS = (".git", "pyproject.toml", "setup.py", "setup.cfg")
#: 往上最多找几层（从脚本目录算）。
MAX_UP_LEVELS = 8
#: 反查的墙钟预算（秒）与最多列多少个 .py、最多对多少个候选做正向解析。
MAX_SECONDS = 2.0
MAX_FILES = 2000
MAX_SCANS = 40
#: 往上追几层（入口 → 中间库模块 → … → 目标）。
MAX_HOPS = 3
#: 报告里最多给几个入口。
MAX_ENTRIES = 5
#: 结果缓存：按 (目标文件身份, 项目根, 搜索根目录的 mtime) 记，有效期内不重扫（报告每秒都会被轮询）。
#: 在搜索根里新建 / 删掉文件会改它的 mtime：在仓库根新写的入口脚本下一次检查就看得见。
CACHE_TTL_S = 600.0
_CACHE_MAX = 32

#: 不当入口的文件名：测试文件 import 库模块是常态，但它不是「真正画图的那个脚本」。
_NOT_ENTRY_PREFIXES = ("test_", "conftest")
_NOT_ENTRY_SUFFIXES = ("_test.py",)

#: 画图库家族：顶层调用只认 `CONFIG_CALLS` 里的配置与常量构造（`rasterhint.MATPLOTLIB_FAMILY` 加上别的画图库）。
PLOT_FAMILY = frozenset(
    {
        "matplotlib",
        "pylab",
        "seaborn",
        "plotnine",
        "mpl_toolkits",
        "scienceplots",
        "proplot",
        "plotly",
        "bokeh",
        "altair",
        "pygal",
    }
)
#: 画图库里 import 期常见、不会建出 Figure 的配置 / 常量构造（按调用的最后一段 / 改名导入的原名判）。
CONFIG_CALLS = frozenset(
    {
        "use",
        "rc",
        "rcdefaults",
        "update",
        "set_theme",
        "set_style",
        "set_context",
        "set_palette",
        "set_color_codes",
        "from_list",
        "ListedColormap",
        "LinearSegmentedColormap",
        "Normalize",
        "TwoSlopeNorm",
        "LogNorm",
        "BoundaryNorm",
        "PowerNorm",
        "CenteredNorm",
        "register",
        "register_cmap",
        "get_cmap",
        "color_palette",
        "light_palette",
        "dark_palette",
        "diverging_palette",
        "cubehelix_palette",
        "blend_palette",
        "to_rgba",
        "to_rgb",
        "to_hex",
        "addfont",
        "FontProperties",
        "findfont",
        "reversed",
        "resampled",
        "with_extremes",
        "MaxNLocator",
        "MultipleLocator",
        "AutoMinorLocator",
        "FuncFormatter",
        "FormatStrFormatter",
        "ScalarFormatter",
        "PercentFormatter",
    }
)
#: 不画图的常见数据 / 工具包：顶层调用它们（读常量、建数组、设随机种子）不会出图。
SAFE_PACKAGES = frozenset(
    {
        "numpy",
        "pandas",
        "scipy",
        "sklearn",
        "statsmodels",
        "sympy",
        "typing_extensions",
        "attr",
        "attrs",
        "yaml",
        "joblib",
        "tqdm",
    }
)

_BUILTINS = frozenset(dir(builtins))

_cache: OrderedDict[tuple, tuple[float, dict | None]] = OrderedDict()
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- 1. 是不是库模块


def _root_name(node: ast.expr) -> str:
    """调用目标的最左一段：`a.b.c()` → `a`；`f()` → `f`；`x[0].y()` → `x`；判不出 → 空串。"""
    while True:
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Name):
            return node.id
        else:
            return ""


def _import_bindings(tree: ast.Module) -> tuple[dict[str, tuple[str, int, str]], bool]:
    """顶层（含 try / if 里）的 import 绑定：名字 → (顶层模块名, 相对层数, `from X import 原名` 的原名)；第二项 = 有没有 `import *`。"""
    out: dict[str, tuple[str, int, str]] = {}
    star = False

    def visit(body: list[ast.stmt]) -> None:
        nonlocal star
        for st in body:
            if isinstance(st, ast.Import):
                for a in st.names:
                    top = a.name.split(".")[0]
                    out[a.asname or top] = (top, 0, "")
            elif isinstance(st, ast.ImportFrom):
                top = (st.module or "").split(".")[0]
                for a in st.names:
                    if a.name == "*":
                        star = True
                        continue
                    out[a.asname or a.name] = (top, st.level, a.name)
            elif isinstance(st, ast.Try):
                visit(st.body)
                for h in st.handlers:
                    visit(h.body)
                visit(st.orelse)
                visit(st.finalbody)
            elif isinstance(st, ast.If):
                visit(st.body)
                visit(st.orelse)

    visit(tree.body)
    return out, star


def _is_guard_test(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` / `if sys.version_info >= (...)` / `if sys.platform == ...`。"""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Compare):
        left = test.left
        if isinstance(left, ast.Subscript):
            left = left.value
        return (
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "sys"
            and left.attr in ("version_info", "platform")
        )
    return False


def _calls_quiet(
    node: ast.AST,
    funcs: frozenset[str],
    bindings: dict[str, tuple[str, int, str]],
    is_local: Callable[[str], bool],
) -> bool:
    """`node` 里的每个调用都不会在 import 期画图 / 跑用户逻辑：不调本文件函数、不调本地模块的名字、不是画图调用。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and not _callee_quiet(sub.func, funcs, bindings, is_local):
            return False
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            # 本文件的函数 / 类、本地模块导入的名字**被提到**就算：`map(plot_one, FILES)`、`fire.Fire(plot)`、
            # `DF.groupby("k").apply(plot_group)`、`Plotter().run()` 都是把它交出去跑，不是只定义
            if sub.id in funcs:
                return False
            bound = bindings.get(sub.id)
            if bound is not None and (bound[1] > 0 or (bound[0] and is_local(bound[0]))):
                return False
    return True


def _callee_quiet(
    func: ast.expr,
    funcs: frozenset[str],
    bindings: dict[str, tuple[str, int, str]],
    is_local: Callable[[str], bool],
) -> bool:
    """被调用的那个东西（调用的 `func`，或装饰器表达式本身）不会在 import 期画图 / 跑用户逻辑。

    正面清单，判不出就不算：标准库与常见数据库（`SAFE_PACKAGES`）的调用；画图库家族（`PLOT_FAMILY`）只认配置与常量构造
    （`CONFIG_CALLS`：`matplotlib.use`、`rcParams.update`、`LinearSegmentedColormap.from_list`……，改名导入按原名判）；
    其余第三方一律当判不出。根是先前赋值得到的值（`CMAP.reversed()`）或内置名时按配置算。"""
    if discover._func_name(func) in discover.PLOT_FUNCS:
        return False
    root = _root_name(func)
    if not root or root in funcs:
        return False
    bound = bindings.get(root)
    if bound is None:
        return True  # 内置名 / 先前赋值得到的值
    top, level, orig = bound
    if level > 0 or not top or is_local(top):
        return False
    if top in PLOT_FAMILY:
        name = discover._func_name(func) if isinstance(func, ast.Attribute) else (orig or root)
        return name in CONFIG_CALLS
    return top in importscan.HOST_STDLIB or top in SAFE_PACKAGES


def _quiet_body(
    body: list[ast.stmt],
    funcs: frozenset[str],
    bindings: dict[str, tuple[str, int, str]],
    is_local: Callable[[str], bool],
) -> bool:
    for st in body:
        if isinstance(st, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # 装饰器、默认值、基类在 import 期求值
            # 装饰器本身就是一次调用（`@register` 不带括号也是）
            if not all(_callee_quiet(d, funcs, bindings, is_local) for d in st.decorator_list):
                return False
            parts: list[ast.AST] = list(st.decorator_list)
            if isinstance(st, ast.ClassDef):
                parts += list(st.bases) + [k.value for k in st.keywords]
                if not _quiet_body(st.body, funcs, bindings, is_local):
                    return False  # 类体在定义时就执行（`class P: fig = plt.figure()`）
            else:
                parts += list(st.args.defaults) + [d for d in st.args.kw_defaults if d is not None]
                # 没有延迟注解时，注解在定义时求值：只看里面的调用（提到本文件的类做类型注解不算）
                annotations = [
                    a.annotation
                    for a in (
                        *st.args.posonlyargs,
                        *st.args.args,
                        *st.args.kwonlyargs,
                        *(x for x in (st.args.vararg, st.args.kwarg) if x is not None),
                    )
                    if a.annotation is not None
                ] + ([st.returns] if st.returns is not None else [])
                for ann in annotations:
                    for sub in ast.walk(ann):
                        if isinstance(sub, ast.Call) and not _callee_quiet(
                            sub.func, funcs, bindings, is_local
                        ):
                            return False
            if not all(_calls_quiet(p, funcs, bindings, is_local) for p in parts):
                return False
            continue
        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Constant):
                continue  # docstring / 裸字面量
            if isinstance(st.value, ast.Call) and _calls_quiet(st.value, funcs, bindings, is_local):
                continue
            return False
        if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(st, ast.AnnAssign) and any(
                isinstance(sub, ast.Call) and not _callee_quiet(sub.func, funcs, bindings, is_local)
                for sub in ast.walk(st.annotation)
            ):
                return False
            if st.value is None or _calls_quiet(st.value, funcs, bindings, is_local):
                continue
            return False
        if isinstance(st, ast.Try):
            blocks = [st.body, st.orelse, st.finalbody] + [h.body for h in st.handlers]
            if all(_quiet_body(b, funcs, bindings, is_local) for b in blocks):
                continue
            return False
        if isinstance(st, ast.If) and _is_guard_test(st.test):
            if _quiet_body(st.body, funcs, bindings, is_local) and _quiet_body(
                st.orelse, funcs, bindings, is_local
            ):
                continue
            return False
        return False  # for / while / with / 普通 if / match / del / assert …：判不出就不说
    return True


def _local_checker(dirs: tuple[Path, ...]) -> Callable[[str], bool]:
    """顶层模块名 `top` 是不是本地模块：在这些目录里有 `top.py` 或 `top/` 包。读不动当作是（宁可不说）。"""

    def is_local(top: str) -> bool:
        for d in dirs:
            try:
                if (d / f"{top}.py").is_file() or (d / top).is_dir():
                    return True
            except OSError:
                return True
        return False

    return is_local


def is_library(
    tree: ast.Module, *, local_dirs: tuple[Path, ...] = (), registered_entry: str | None = None
) -> bool:
    """见模块文档第 1 条。`local_dirs`：判「本地模块」的目录（脚本目录、项目根、仓库根）。"""
    if registered_entry and registered_entry != "__main__":
        return False
    if discover._has_inline_main(tree):
        return False
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not funcs:
        return False
    if discover._toplevel_reaches_plot(tree, funcs):
        return False
    if discover.entry_candidates_of(tree) != ["__main__"]:
        return False  # 有零参入口：产品会调它
    bindings, star = _import_bindings(tree)
    if star:
        return False
    is_local = _local_checker(local_dirs)
    if not any(
        discover._reaches_calls(fn, funcs, discover.PLOT_FUNCS)
        or _delegates_to_local(fn, bindings, is_local)
        for fn in funcs.values()
    ):
        return False
    defined = frozenset(funcs) | frozenset(n.name for n in tree.body if isinstance(n, ast.ClassDef))
    return _quiet_body(tree.body, defined, bindings, is_local)


def _delegates_to_local(
    fn: ast.AST, bindings: dict[str, tuple[str, int, str]], is_local: Callable[[str], bool]
) -> bool:
    """函数体里调了本地模块（相对导入 / 本地文件）导入的名字：画图交给了同一个包里的另一个模块。"""
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call):
            bound = bindings.get(_root_name(sub.func))
            if bound is not None and (bound[1] > 0 or (bound[0] and is_local(bound[0]))):
                return True
    return False


def runs_at_import(tree: ast.Module, *, local_dirs: tuple[Path, ...] = ()) -> bool:
    """顶层会做事（有 `__main__` 守卫，或顶层语句不全是 import / 定义 / 配置调用）：这样的脚本才可能是入口。"""
    if discover._has_inline_main(tree):
        return True
    defined = frozenset(
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    bindings, star = _import_bindings(tree)
    return star or not _quiet_body(tree.body, defined, bindings, _local_checker(local_dirs))


# ---------------------------------------------------------------- 2. 谁 import 了它


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def search_root(project_root: Path, script_path: Path) -> Path:
    """反查的范围：项目根之外最近的仓库标记目录（不是主目录或其祖先、不是盘符根）；没有就是项目根。"""
    project_root = Path(os.path.realpath(project_root))
    try:
        home = Path(os.path.realpath(Path.home()))
    except (OSError, RuntimeError):
        home = None
    d = Path(os.path.realpath(script_path)).parent
    for _ in range(MAX_UP_LEVELS):
        if d.parent == d:
            break  # 盘符根 / 文件系统根：不扫
        if home is not None and _is_within(home, d):
            break  # 主目录本身或它的祖先：不扫（主目录下的 dotfiles 仓库不是这个项目的仓库）
        try:
            marked = any((d / m).exists() for m in REPO_MARKERS)
        except OSError:
            marked = False
        if marked:
            # 标记在项目里面（子目录自带 pyproject）：项目根更大，用项目根
            return d if _is_within(project_root, d) and d != project_root else project_root
        d = d.parent
    return project_root


def _stem_of(path: Path) -> str:
    return path.parent.name if path.name == "__init__.py" else path.stem


def _not_entry_name(name: str) -> bool:
    return name.startswith(_NOT_ENTRY_PREFIXES) or name.endswith(_NOT_ENTRY_SUFFIXES)


def _read(root: Path, rel: str) -> str | None:
    try:
        st = (root / rel).lstat()
        if not stat.S_ISREG(st.st_mode) or scanbudget.is_placeholder(st):
            return None
        return scanbudget.read_regular_text(
            root, *rel.split("/"), no_follow=True, python_source=True
        )
    except OSError:
        return None


def _module_name(root: Path, rel: str) -> str | None:
    """`rel` 作为包里的模块时的点分名（每一级目录都有 `__init__.py`，一直到 `root`）；不在包里 → None。"""
    parts = rel[:-3].split("/") if rel.endswith(".py") else []
    if len(parts) < 2:
        return None
    for i in range(1, len(parts)):
        if not (root.joinpath(*parts[:i]) / "__init__.py").is_file():
            return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if all(p.isidentifier() for p in parts) else None


def _reaches(
    root: Path, rel: str, tree: ast.Module, target_rel: str, deadline: float
) -> str | None:
    """`rel` 的 import（含跟进到的本地模块）读不读到 `target_rel`：`importscan` 的正向解析说了算。
    回 `"script"`（按脚本跑就读得到——产品的 safe worker 正是这么跑的）/ `"module"`（只有按 `python -m 包.模块` 才读得到：
    包里用相对导入的模块）/ `""`（读不到）/ None（读不动 / 预算停了：「没读到」不代表没有）。
    每次解析都只给到 `deadline` 为止的墙钟预算。"""

    def budget() -> scanbudget.Budget | None:
        left = deadline - time.monotonic()
        return scanbudget.Budget(scanbudget.Limits(max_seconds=left)) if left > 0 else None

    try:
        b = budget()
        if b is None:
            return None
        if target_rel in importscan.scan(root, rel, tree=tree, no_follow=True, budget=b).files:
            return "script"
        relative = any(isinstance(n, ast.ImportFrom) and n.level > 0 for n in ast.walk(tree))
        module = _module_name(root, rel) if relative else None
        if module is None:
            return "" if b.stop_reason() is None else None
        b = budget()
        if b is None:
            return None
        entry = importscan.Entry(
            kind=execspec.TARGET_MODULE, module=module, cwd_mode=execspec.CWD_PROJECT_ROOT
        )
        if (
            target_rel
            in importscan.scan(root, rel, tree=tree, entry=entry, no_follow=True, budget=b).files
        ):
            return "module"
        return "" if b.stop_reason() is None else None
    except (OSError, ValueError, RecursionError):
        return None


def find_entries(project_root: Path, script: str) -> dict:
    """`script`（项目相对）是库模块时，找 import 了它的入口脚本。回 `{entries, complete, scope}`：

    * `entries`：`[{path, in_project, script?, open_root, open_script, direct}]`——`path` 绝对路径；在项目里的带项目相对
      `script`；`open_root` / `open_script` 是「打开哪个文件夹、运行其中哪个脚本」（项目里的就是项目根）；
    * `complete`：预算没停、没有读不动的候选（False = 「没找到」不代表真的没有）；
    * `scope`：`project`（只在项目里找）/ `repo`（找到了项目外的仓库根）。
    """
    project_root = Path(os.path.realpath(project_root))
    target = Path(os.path.realpath(project_root / script))
    root = search_root(project_root, target)
    deadline = time.monotonic() + MAX_SECONDS
    budget = scanbudget.Budget(scanbudget.Limits(max_scripts=MAX_FILES, max_seconds=MAX_SECONDS))
    files = discover._iter_py(root, include_infrastructure=True, budget=budget)
    complete = budget.stop_reason() is None and not any(
        i.get("severity") == scanbudget.SEVERITY_PARTIAL and i.get("code") != scanbudget.ISSUE_DEPTH
        for i in budget.issues()
    )
    rels = []
    for f in files:
        try:
            rels.append(Path(os.path.realpath(f)).relative_to(root).as_posix())
        except ValueError:
            continue
    texts: dict[str, str | None] = {}

    def text_of(rel: str) -> str | None:
        if rel not in texts:
            texts[rel] = _read(root, rel)
        return texts[rel]

    try:
        target_rel = target.relative_to(root).as_posix()
    except ValueError:
        return {"entries": [], "complete": False, "scope": "project"}
    found: dict[str, dict] = {}
    frontier = [(target_rel, 1)]
    seen = {target_rel}
    scans = 0
    while frontier:
        mod_rel, hop = frontier.pop(0)
        pattern = re.compile(r"\b" + re.escape(_stem_of(root / mod_rel)) + r"\b")
        for rel in rels:
            if rel in seen:
                continue
            if time.monotonic() > deadline or scans >= MAX_SCANS:
                complete = False
                frontier = []
                break
            text = text_of(rel)
            if text is None:
                complete = False  # 读不动 / 太大 / 解不了码 / 云盘占位：没看过它，不算「没有」
                continue
            if not pattern.search(text):
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                complete = False
                continue
            scans += 1
            reached = _reaches(root, rel, tree, mod_rel, deadline)
            if reached is None:
                complete = False
                continue
            if not reached:
                continue
            seen.add(rel)
            cand = root / rel
            dirs = (cand.parent, root, project_root)
            if not runs_at_import(tree, local_dirs=dirs):
                # 自己也只是模块（库 / 工具 / 包的 `__init__`）：不是入口，沿它再往上找一层
                if hop < MAX_HOPS:
                    frontier.append((rel, hop + 1))
                continue
            if _not_entry_name(cand.name) or reached != "script":
                # 只有按 `python -m` 才解析得到的（包里用相对导入）不推荐成脚本：safe worker 按脚本路径跑它会 ImportError
                continue
            found.setdefault(
                rel,
                {
                    "rel": rel,
                    "direct": hop == 1,
                    "main": discover._has_inline_main(tree),
                    "tree": tree,
                },
            )
    entries = []
    for row in found.values():
        path = root / row["rel"]
        in_project = _is_within(path, project_root)
        if in_project and root != project_root:
            # 在项目里、但只有以仓库根为搜索根才解析得到（项目开在 `pkg/` 上、入口写 `from pkg.x import …`）：
            # 在当前项目里跑会找不到模块，按「打开仓库再运行」给
            try:
                rel_p = path.relative_to(project_root).as_posix()
                tgt_p = target.relative_to(project_root).as_posix()
            except ValueError:
                rel_p = tgt_p = ""
            # 与前面共用同一个截止时间与解析次数；预算不够就按「打开仓库再运行」给（在仓库根跑两种情况都成立）
            ok = None
            if rel_p and scans < MAX_SCANS:
                scans += 1
                ok = _reaches(project_root, rel_p, row["tree"], tgt_p, deadline)
            in_project = ok == "script"
        entry = {
            "path": str(path),
            "in_project": in_project,
            "direct": row["direct"],
            "open_root": str(project_root if in_project else root),
            "open_script": (
                path.relative_to(project_root).as_posix() if in_project else row["rel"]
            ),
        }
        if in_project:
            entry["script"] = entry["open_script"]
        entry["_rank"] = (
            not row["direct"],
            not in_project,
            not row["main"],
            row["rel"].count("/"),
            row["rel"],
        )
        entries.append(entry)
    entries.sort(key=lambda e: e["_rank"])
    for e in entries:
        del e["_rank"]
    return {
        "entries": entries[:MAX_ENTRIES],
        "complete": complete,
        "scope": "project" if root == project_root else "repo",
    }


# ---------------------------------------------------------------- 3. 准备会话用的入口


def analyze(
    project_root: str | Path, script: str, *, registered_entry: str | None = None
) -> dict | None:
    """准备会话的提示：不是库模块（或判不出）→ None；是 → `find_entries` 的结果。按目标文件身份缓存。"""
    project_root = Path(project_root)
    path = project_root / script
    try:
        st = path.stat()
    except OSError:
        return None
    try:
        root_mtime = search_root(project_root, path).stat().st_mtime_ns
    except OSError:
        root_mtime = 0
    key = (
        os.path.realpath(path),
        st.st_mtime_ns,
        st.st_size,
        os.path.realpath(project_root),
        root_mtime,
        registered_entry,
    )
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(key)
            return dict(hit[1]) if hit[1] is not None else None
    result = _analyze(project_root, script, registered_entry)
    with _cache_lock:
        _cache[key] = (now, result)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return dict(result) if result is not None else None


def _analyze(project_root: Path, script: str, registered_entry: str | None) -> dict | None:
    text, problem = discover.read_source(project_root / script)
    if problem is not None or text is None or len(text) > scanbudget.MAX_FILE_BYTES:
        return None
    tree, problem = discover.parse_source(text)
    if tree is None:
        return None
    path = Path(os.path.realpath(project_root / script))
    root = search_root(project_root, path)
    dirs = (path.parent, Path(os.path.realpath(project_root)), root)
    if not is_library(tree, local_dirs=dirs, registered_entry=registered_entry):
        return None
    return find_entries(project_root, script)


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
