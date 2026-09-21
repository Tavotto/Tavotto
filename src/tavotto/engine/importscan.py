"""脚本 import 的分类（统一实施包 U04，ADR 0061）：stdlib / 本地模块 / 第三方 / 未知，
外加每一处 import 的**上下文**（无条件 / 条件 / 延后 / 可选 / 仅类型 / 动态）。

联合准备要回答的问题是「这个脚本**开跑就需要**哪些第三方包」，而不是「项目声明了哪些
包」：声明是约束的来源（`depresolve.declared_intents`），需要不需要看脚本自己。两者交在
`depplan` 里。本模块只做静态分类，**不执行脚本、不 import 任何用户模块**；判据：

* **stdlib**：名字在 `sys.stdlib_module_names`（3.10+ 标准库自报的那张表；目标解释器的
  那份由 `depplan` 传进来——脚本会在**它**里面跑）+ `__future__` / builtins；
* **本地模块**（FO-031 / FO19：`lab_utils` 之类永远不装）：脚本目录或项目根下有同名
  `.py` / 包目录 / 扩展模块——`python script.py` 的 `sys.path[0]` 是脚本目录，
  `project_root` 档还加项目根，两处都按「import 系统会先在这里找到」的顺序判；
* **第三方**：不是上面两种、且能经**可信解析**（项目声明 / curated 表，`depresolve.resolve`
  同一优先级）映射到一个 distribution；
* **未知**：不是 stdlib、不是本地、也映射不到——**永远不装、不猜同名**（FO-034），只报出来
  让用户指定。

上下文决定「要不要在跑之前就准备」：只有**模块层无条件**的 import 会让脚本一开跑就死
（`needed`）；`try:` 里的（可选依赖）、`if TYPE_CHECKING:` 里的（仅类型）、函数 / 类体内的
（延后）、`if` / `for` / `with` 里的（条件）、`importlib.import_module(变量)` /
`__import__(变量)`（动态）都不在跑前装——它们缺了会以 `missing_dependency` 在运行后报出来，
由有界重计划接手（ADR 0061 §四）。**宁可少装不误装**：改用户环境的默认动作只由确定
会执行的那几行触发。

本地模块**有界跟进**（`MAX_LOCAL_MODULES` / `MAX_DEPTH`）：`lab_utils` 里 import 的第三方包
同样是脚本开跑就需要的；跟进到的 import 记 `via`。纯标准库；被 `depplan` import。
"""

from __future__ import annotations

import ast
import dataclasses
import os
import sys
from pathlib import Path

from . import depresolve

BUCKET_STDLIB = "stdlib"
BUCKET_LOCAL = "local"
BUCKET_THIRD_PARTY = "third_party"
BUCKET_UNKNOWN = "unknown"
BUCKETS = (BUCKET_STDLIB, BUCKET_LOCAL, BUCKET_THIRD_PARTY, BUCKET_UNKNOWN)

CONTEXT_UNCONDITIONAL = "unconditional"
CONTEXT_CONDITIONAL = "conditional"
CONTEXT_DEFERRED = "deferred"
CONTEXT_OPTIONAL = "optional"
CONTEXT_TYPE_CHECKING = "type_checking"
CONTEXT_DYNAMIC = "dynamic"
#: 从「一开跑就执行」到「不一定执行」排序；一个名字被 import 多次时取最强的那个。
CONTEXTS = (
    CONTEXT_UNCONDITIONAL,
    CONTEXT_CONDITIONAL,
    CONTEXT_DEFERRED,
    CONTEXT_OPTIONAL,
    CONTEXT_TYPE_CHECKING,
    CONTEXT_DYNAMIC,
)

#: 本地模块跟进的上限：文件数与深度。科研项目的本地模块通常两三个；上限只挡住误把整个
#: 源码树扫一遍。
MAX_LOCAL_MODULES = 24
MAX_DEPTH = 3
MAX_SOURCE_BYTES = 1024 * 1024

#: 本模块认的扩展模块后缀（本地编译扩展：`fastcalc.cpython-313-darwin.so`）。
_EXT_SUFFIXES = (".so", ".pyd", ".dylib")

#: 标准库名字表：宿主的那份是默认；目标解释器的由调用方传入。
HOST_STDLIB: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "__future__",
    "__main__",
    "builtins",
}


@dataclasses.dataclass(frozen=True)
class ImportUse:
    """一处 import：顶级名、写的全名、上下文、行号、经由哪个本地模块（空 = 脚本本身）。"""

    module: str
    full: str
    context: str
    lineno: int
    via: str = ""

    def to_payload(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ImportClass:
    """一个顶级 import 名的分类结论。`distribution` 只在第三方且映射得到时有值。"""

    module: str
    bucket: str
    context: str
    distribution: str = ""
    resolution_source: str = ""
    local_path: str = ""  # 本地模块相对项目根的路径（bucket=local）
    lines: tuple[int, ...] = ()
    via: tuple[str, ...] = ()

    @property
    def needed(self) -> bool:
        """脚本一开跑就要它、且它是第三方（不管映射得到没有）。"""
        return self.bucket == BUCKET_THIRD_PARTY and self.context == CONTEXT_UNCONDITIONAL

    def to_payload(self) -> dict:
        return {
            "module": self.module,
            "bucket": self.bucket,
            "context": self.context,
            "distribution": self.distribution,
            "resolution_source": self.resolution_source,
            "local_path": self.local_path,
            "lines": list(self.lines),
            "via": list(self.via),
            "needed": self.needed,
        }


@dataclasses.dataclass(frozen=True)
class ScanResult:
    """一次扫描的全部结论：分类表 + 逐处 import + 动态 import 的位置 + 读不了的本地模块。"""

    classes: tuple[ImportClass, ...]
    uses: tuple[ImportUse, ...]
    dynamic: tuple[ImportUse, ...]
    problems: tuple[dict, ...]
    truncated: bool = False  # 本地模块跟进碰到上限

    def by_bucket(self, bucket: str) -> list[ImportClass]:
        return [c for c in self.classes if c.bucket == bucket]

    @property
    def needed(self) -> list[ImportClass]:
        return [c for c in self.classes if c.needed]

    def to_payload(self) -> dict:
        return {
            "classes": [c.to_payload() for c in self.classes],
            "dynamic": [u.to_payload() for u in self.dynamic],
            "problems": [dict(p) for p in self.problems],
            "truncated": self.truncated,
            "counts": {b: len(self.by_bucket(b)) for b in BUCKETS},
        }


# ---------------------------------------------------------------- AST


class _Visitor(ast.NodeVisitor):
    """收集一份模块里的 import 及其上下文。"""

    def __init__(self, via: str) -> None:
        self.via = via
        self.uses: list[ImportUse] = []
        self.dynamic: list[ImportUse] = []
        self._stack: list[str] = []  # 进入了哪些会改变上下文的结构

    # ---- 上下文 ----
    def _context(self) -> str:
        if CONTEXT_TYPE_CHECKING in self._stack:
            return CONTEXT_TYPE_CHECKING
        if CONTEXT_OPTIONAL in self._stack:
            return CONTEXT_OPTIONAL
        if CONTEXT_DEFERRED in self._stack:
            return CONTEXT_DEFERRED
        if CONTEXT_CONDITIONAL in self._stack:
            return CONTEXT_CONDITIONAL
        return CONTEXT_UNCONDITIONAL

    def _push(self, ctx: str, body):
        self._stack.append(ctx)
        try:
            for node in body:
                self.visit(node)
        finally:
            self._stack.pop()

    # ---- 结构 ----
    def visit_FunctionDef(self, node):
        self._push(CONTEXT_DEFERRED, node.body)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._push(CONTEXT_DEFERRED, node.body)

    def visit_Try(self, node):
        # `try:` 里的 import 是可选依赖的惯用写法（`except ImportError`）；`else` 与
        # `finally` 不在保护之下，按外层上下文；`except` 分支只在出错时跑——条件。
        self._push(CONTEXT_OPTIONAL, node.body)
        for handler in node.handlers:
            self._push(CONTEXT_CONDITIONAL, handler.body)
        for node_ in node.orelse:
            self.visit(node_)
        for node_ in node.finalbody:
            self.visit(node_)

    visit_TryStar = visit_Try

    def visit_If(self, node):
        if _is_type_checking(node.test):
            self._push(CONTEXT_TYPE_CHECKING, node.body)
            for node_ in node.orelse:  # `else:` 是运行时那一半
                self.visit(node_)
            return
        if _is_main_guard(node.test):
            # `if __name__ == "__main__":`——脚本作为入口跑时它就是无条件执行的那一段
            for node_ in node.body:
                self.visit(node_)
            self._push(CONTEXT_CONDITIONAL, node.orelse)
            return
        self._push(CONTEXT_CONDITIONAL, node.body)
        self._push(CONTEXT_CONDITIONAL, node.orelse)

    def visit_For(self, node):
        self._push(CONTEXT_CONDITIONAL, node.body)
        self._push(CONTEXT_CONDITIONAL, node.orelse)

    visit_AsyncFor = visit_For
    visit_While = visit_For

    def visit_With(self, node):
        # `with` 体一定执行（除非 __enter__ 抛），但 `with suppress(ImportError):` 是可选
        # 依赖的另一种写法——按可选判。
        ctx = CONTEXT_OPTIONAL if _suppresses_import_error(node) else CONTEXT_UNCONDITIONAL
        self._push(ctx, node.body)

    visit_AsyncWith = visit_With

    def visit_Match(self, node):
        for case in node.cases:
            self._push(CONTEXT_CONDITIONAL, case.body)

    # ---- import ----
    def visit_Import(self, node):
        ctx = self._context()
        for alias in node.names:
            self.uses.append(
                ImportUse(alias.name.split(".", 1)[0], alias.name, ctx, node.lineno, self.via)
            )

    def visit_ImportFrom(self, node):
        if node.level:  # 相对 import：包内的事，不是依赖
            return
        mod = node.module or ""
        if not mod:
            return
        self.uses.append(
            ImportUse(mod.split(".", 1)[0], mod, self._context(), node.lineno, self.via)
        )

    def visit_Call(self, node):
        target = _dynamic_import_target(node)
        if target is not None:
            literal, arg = target
            if literal is not None:
                self.uses.append(
                    ImportUse(
                        literal.split(".", 1)[0], literal, self._context(), node.lineno, self.via
                    )
                )
            else:
                self.dynamic.append(ImportUse("", arg, CONTEXT_DYNAMIC, node.lineno, self.via))
        self.generic_visit(node)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _is_main_guard(test: ast.expr) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1):
        return False
    left, right = test.left, test.comparators[0]
    names = {getattr(left, "id", None), getattr(right, "id", None)}
    consts = {
        getattr(left, "value", None) if isinstance(left, ast.Constant) else None,
        getattr(right, "value", None) if isinstance(right, ast.Constant) else None,
    }
    return isinstance(test.ops[0], ast.Eq) and "__name__" in names and "__main__" in consts


def _suppresses_import_error(node) -> bool:
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name == "suppress" and any(
            (
                isinstance(a, ast.Name)
                and a.id in ("ImportError", "ModuleNotFoundError", "Exception")
            )
            or (isinstance(a, ast.Attribute) and a.attr in ("ImportError", "ModuleNotFoundError"))
            for a in call.args
        ):
            return True
    return False


def _dynamic_import_target(node: ast.Call) -> tuple[str | None, str] | None:
    """`importlib.import_module(x)` / `__import__(x)` → `(字面量或 None, 参数原文)`；不是就 None。"""
    fn = node.func
    is_import_module = (
        isinstance(fn, ast.Attribute)
        and fn.attr == "import_module"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "importlib"
    ) or (isinstance(fn, ast.Name) and fn.id == "import_module")
    is_dunder = isinstance(fn, ast.Name) and fn.id == "__import__"
    if not (is_import_module or is_dunder) or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value, arg.value
    try:
        text = ast.unparse(arg)
    except Exception:  # noqa: BLE001 — unparse 只为诊断
        text = "<expr>"
    return None, text


# ---------------------------------------------------------------- 本地模块


def _local_module_path(name: str, search_dirs: list[Path], root: Path) -> Path | None:
    """`name` 在这些目录里是不是一个本地模块 / 包 / 扩展；回它的路径（在项目根内）。"""
    if not depresolve.valid_import_name(name):
        return None
    for base in search_dirs:
        candidates = [base / f"{name}.py", base / name / "__init__.py"]
        try:
            for cand in candidates:
                if cand.is_file() and _within(root, cand):
                    return cand
            pkg = base / name
            if pkg.is_dir() and _within(root, pkg):
                # 没有 __init__.py 的目录：命名空间包，只要里面有 .py 就算本地包
                if any(p.suffix == ".py" for p in pkg.iterdir() if p.is_file()):
                    return pkg
            for ext in base.glob(f"{name}.*"):
                if ext.is_file() and ext.name.endswith(_EXT_SUFFIXES) and _within(root, ext):
                    return ext
        except OSError:
            continue
    return None


def _within(root: Path, path: Path) -> bool:
    try:
        real = path.resolve(strict=False)
        root_real = root.resolve(strict=False)
    except OSError:
        return False
    return real == root_real or root_real in real.parents


def _read(path: Path) -> tuple[str | None, dict | None]:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return None, {"path": str(path), "kind": "too_large"}
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, {"path": str(path), "kind": "io", "detail": str(exc)[:200]}


def _parse(text: str, path: Path) -> tuple[ast.Module | None, dict | None]:
    try:
        return ast.parse(text), None
    except (SyntaxError, ValueError) as exc:
        return None, {"path": str(path), "kind": "syntax", "detail": str(exc)[:200]}


# ---------------------------------------------------------------- 入口


def scan(
    root: str | Path,
    script: str,
    *,
    declared: dict[str, str] | None = None,
    stdlib: frozenset[str] | None = None,
    tree: ast.Module | None = None,
) -> ScanResult:
    """扫描脚本（及其本地模块）的 import，逐个顶级名分类。

    `declared` 是项目声明过的 distribution（规范化名 → 版本约束，来自 `depresolve`）——
    第三方名字映射到 distribution 的第一档证据；`stdlib` 是**目标解释器**的标准库名字表
    （没给用宿主的）；`tree` 是调用方已经解析好的脚本 AST（`discover` 那边读过一次就不再
    读）。本地模块的搜索目录是脚本目录 + 项目根：`python script.py` 的 `sys.path[0]` 是
    前者，`project_root` 档跑时 cwd 是后者——两处的同名模块在任何档下都按本地判
    （宁可判本地不误装）。
    """
    root_p = Path(root)
    script_p = root_p / script
    stdlib_names = (
        HOST_STDLIB if stdlib is None else (frozenset(stdlib) | {"__future__", "builtins"})
    )
    declared = dict(declared or {})
    search_dirs = [script_p.parent]
    if script_p.parent.resolve(strict=False) != root_p.resolve(strict=False):
        search_dirs.append(root_p)

    uses: list[ImportUse] = []
    dynamic: list[ImportUse] = []
    problems: list[dict] = []
    truncated = False

    if tree is None:
        text, problem = _read(script_p)
        if problem is not None:
            problems.append(problem)
            text = ""
        tree, problem = _parse(text, script_p) if text else (None, None)
        if problem is not None:
            problems.append(problem)
    if tree is not None:
        v = _Visitor("")
        v.visit(tree)
        uses += v.uses
        dynamic += v.dynamic

    # 本地模块有界跟进：每发现一个本地模块就扫它的文件，它 import 的东西再排队判本地。
    local_paths: dict[str, Path] = {}
    depth_of: dict[str, int] = {"": 0}  # via → 深度
    scanned: set[str] = set()
    pending = list(uses)
    while pending:
        use = pending.pop(0)
        name = use.module
        if not name or name in stdlib_names or name in local_paths:
            continue
        dirs = ([root_p / Path(use.via).parent] if use.via else []) + search_dirs
        path = _local_module_path(name, dirs, root_p)
        if path is None:
            continue
        local_paths[name] = path
        depth = depth_of.get(use.via, 0) + 1
        if depth > MAX_DEPTH:
            truncated = True
            continue
        via = _rel(root_p, path)
        depth_of[via] = depth
        for f in _module_files(path):
            key = os.path.normcase(str(f))
            if key in scanned:
                continue
            if len(scanned) >= MAX_LOCAL_MODULES:
                truncated = True
                break
            scanned.add(key)
            text, problem = _read(f)
            if problem is not None:
                problems.append(problem)
                continue
            sub, problem = _parse(text, f)
            if problem is not None:
                problems.append(problem)
                continue
            sv = _Visitor(via)
            sv.visit(sub)
            for su in sv.uses:
                # 经由本地模块的 import 继承两处里较弱的上下文：脚本在 try 里 import
                # lab_utils、lab_utils 无条件 import h5py → 对脚本来说 h5py 仍是可选。
                merged = max((use.context, su.context), key=CONTEXTS.index)
                su2 = ImportUse(su.module, su.full, merged, su.lineno, via)
                uses.append(su2)
                pending.append(su2)
            dynamic += [ImportUse("", d.full, CONTEXT_DYNAMIC, d.lineno, via) for d in sv.dynamic]

    by_name: dict[str, list[ImportUse]] = {}
    for u in uses:
        if u.module:
            by_name.setdefault(u.module, []).append(u)
    classes: list[ImportClass] = []
    for name in sorted(by_name):
        group = by_name[name]
        context = min((u.context for u in group), key=CONTEXTS.index)
        lines = tuple(sorted({u.lineno for u in group if not u.via}))
        via = tuple(sorted({u.via for u in group if u.via}))
        if name in stdlib_names:
            classes.append(ImportClass(name, BUCKET_STDLIB, context, lines=lines, via=via))
            continue
        if name in local_paths:
            classes.append(
                ImportClass(
                    name,
                    BUCKET_LOCAL,
                    context,
                    local_path=_rel(root_p, local_paths[name]),
                    lines=lines,
                    via=via,
                )
            )
            continue
        dist, source = map_distribution(name, declared)
        classes.append(
            ImportClass(
                name,
                BUCKET_THIRD_PARTY if dist else BUCKET_UNKNOWN,
                context,
                distribution=dist,
                resolution_source=source,
                lines=lines,
                via=via,
            )
        )
    return ScanResult(
        classes=tuple(classes),
        uses=tuple(uses),
        dynamic=tuple(dynamic),
        problems=tuple(problems),
        truncated=truncated,
    )


def _module_files(path: Path) -> list[Path]:
    """一个本地模块 / 包对应要扫的 .py 文件（扩展模块没有源码可扫）。"""
    try:
        if path.is_file():
            return [path] if path.suffix == ".py" else []
        if path.is_dir():
            return sorted(p for p in path.glob("*.py") if p.is_file())[:MAX_LOCAL_MODULES]
    except OSError:
        pass
    return []


def map_distribution(import_name: str, declared: dict[str, str]) -> tuple[str, str]:
    """import 名 → `(distribution, 来源)`；映射不到回 `("", "")`——**不猜同名**。

    与 `depresolve.resolve` 同一优先级：项目声明过（经 curated 表或同名对上）>
    curated；都没有就是未知。回的 distribution 是规范化名。
    """
    if not depresolve.valid_import_name(import_name):
        return "", ""
    curated = depresolve.curated_distribution(import_name)
    for candidate in [c for c in (curated, import_name) if c]:
        key = depresolve.normalize_distribution(candidate)
        if key in declared:
            return key, depresolve.SOURCE_PROJECT_DECLARED
    if curated:
        return depresolve.normalize_distribution(curated), depresolve.SOURCE_CURATED
    return "", ""


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.name
