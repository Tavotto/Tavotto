"""运行前路由（V2 PR2，ADR 0129）：用户在桌面「打开并运行」一份脚本时，**执行之前**凭静态证据挑 `safe` 还是 `native`。

只回答一个问题：这份脚本第一次就该用保留文件语义的 native 跑吗？默认答案是 `safe`（普通绘图）。只有**高置信度**才直达
native；其余一律 safe，由运行期账本（`BlockedFileOps`）观察，真被拦下再由 PR1 的同会话回退接手（`execpolicy`）。
代价不对称：误判 native = 丢掉 safe 的沙盒守卫；漏判 = safe 白跑一遍，但守卫拦住了所以无副作用。所以低置信度**永不升级**。

零执行：只做 `ast.parse` 与纯字符串 / 路径文本运算，不 import 用户代码、不起进程、不 `find_spec`、不读脚本以外的文件内容
（`realpath` 只用于项目根自己）。**只在用户发起的运行 / 准备里被调用**；被动扫描（`projscan` 一系）不得 import 本模块
（`tests/test_runroute_boundary.py` 钉住）。纯标准库（路径 / 读写调用表是本模块自带的、按 import 绑定解析的限定名，
与别处按尾名匹配的 `READ_FUNCS` / `SAVE_FUNCS` 不是同一类东西；包含关系由用例钉住）。

规则（审计 B §5）：

* R1 `savefig(P)` 之后在同一函数体里**读回** P（`open` / `Image.open` / `imread` / `Path.read_*` / `np.load` / `pd.read_*`）→ high。
  safe 的 savefig 拦截不落盘，读回读到的不是脚本刚存的。与 cwd 档无关（拦截对任何路径都生效）。
* R2 `savefig(P)` 之后只对 P 做 `exists` / `getsize`（多是「存好了」的校验打印；运行期也刻意不算观察）→ medium，仍 safe。
* R3 写 T → `os.replace` / `os.rename` / `shutil.move` / `Path.replace|rename`（T→F）→ 再读（或探 `exists`）F，**且 T 在真实图库内**
  （safe 的删除 / 改名守卫只看源）→ high。T 在沙盒 / 系统临时目录里 safe 本来就真执行，不升格。链不全（看不见写或看不见读）→ medium。
* R4 动态路径（形参 / argv / `input()` / 时间戳命名 / 循环变量）→ low，只记计数，safe。

静态分析做不到的（blind spots，诚实写进 `Route.blind_spots`）：跨函数 / 跨文件的路径传递、本地 helper 模块里的 savefig / replace、
别名反射、`subprocess` 搬文件、运行时才定的分支。覆盖不到的由运行期账本兜底。
"""

from __future__ import annotations

import ast
import dataclasses
import ntpath
import os
import posixpath
import threading
import time
from collections import OrderedDict
from pathlib import Path

# ---------------------------------------------------------------- 词汇（闭集）
EXECUTION_SAFE = "safe"
EXECUTION_NATIVE = "native"
EXECUTIONS = (EXECUTION_SAFE, EXECUTION_NATIVE)
#: 调用方（准备会话）不指定执行语义时的取值：让路由来挑
EXECUTION_AUTO = "auto"

REASON_ORDINARY_PLOT = "ordinary_plot"
REASON_SAVEFIG_READBACK = "savefig_readback"
REASON_NATIVE_FILE_SEMANTICS = "native_file_semantics_required"
REASON_PREVIOUS_SUCCESS = "previous_successful_profile"
REASON_USER_EXPLICIT = "user_explicit_profile"
#: 运维开关 `TAVOTTO_EXECUTION_FALLBACK=0` 关着：整个 V2 的自动选语义都停（直达 native 与回退同一个开关）
REASON_OPERATOR_DISABLED = "operator_disabled"
REASONS = (
    REASON_ORDINARY_PLOT,
    REASON_SAVEFIG_READBACK,
    REASON_NATIVE_FILE_SEMANTICS,
    REASON_PREVIOUS_SUCCESS,
    REASON_USER_EXPLICIT,
    REASON_OPERATOR_DISABLED,
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCES = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

RULE_R1, RULE_R2, RULE_R3, RULE_R3_PARTIAL, RULE_R4 = "R1", "R2", "R3", "R3_partial", "R4"
RULE_R1_CROSS_SCOPE = "R1_cross_scope"
#: 证据成立的前提没证明：`savefig` 的接收者解析不到 matplotlib，或证据只在模块从没调用到的函数体里（只记，不直达）
RULE_R1_UNPROVEN = "R1_unproven"
RULES = (
    RULE_R1,
    RULE_R1_CROSS_SCOPE,
    RULE_R1_UNPROVEN,
    RULE_R2,
    RULE_R3,
    RULE_R3_PARTIAL,
    RULE_R4,
)

#: 与 `worker.REQUIRED_EXECUTION_FILE_SEMANTICS`（ADR 0003 的 `error.required_execution`）同一个词：路由原因与运行期错误
#: 是一套话术。worker 不在 Flask 进程里 import，这里镜像字符串，`tests/test_runroute.py` 钉相等。
REQUIRED_EXECUTION_FILE_SEMANTICS = "preserve_file_semantics"
#: 与 `figcapture.BlockedFileOps.KIND_*` 同值（同上，用例钉相等）：静态证据与运行期观察用同一个 `kind`
KIND_RENAME = "rename"
KIND_WRITE = "write"
KIND_SAVEFIG = "savefig"
#: 与 `execpolicy.MODE_*` 同值（同上）
POLICY_COMPATIBLE = "compatible"
POLICY_SAFE_ONLY = "safe_only"
#: 与 `workdir.MODE_*` / `execspec.CWD_*` 同值（同上）
CWD_SANDBOX = "sandbox"
CWD_PROJECT = "project"
CWD_PROJECT_ROOT = "project_root"

BLIND_SPOTS = (
    "cross_function_path_flow",
    "local_helper_modules",
    "aliasing_and_reflection",
    "subprocess_file_moves",
    "runtime_branches",
)
BLIND_UNPARSED = "unparsed"
BLIND_TOO_LARGE = "too_large"

#: 与 `scriptargs` / `dialogscan` 同值：超过就不分析（不是路由的理由，只是不为巨型文件付解析成本）
MAX_SOURCE_BYTES = 1 << 20
CACHE_SIZE = 64
_MAX_DEPTH = 8  # 名字追踪 / 表达式折叠的递归深度


# ---------------------------------------------------------------- 调用表（限定名 = import 绑定解析之后的点号名）
_BUILTIN_OPEN = frozenset({"open", "io.open", "codecs.open"})
_MOVE_FUNCS = {"os.replace": "os.replace", "os.rename": "os.rename", "shutil.move": "shutil.move"}
_STAT_FUNCS = frozenset(
    {
        "os.path.exists",
        "os.path.isfile",
        "os.path.getsize",
        "os.path.getmtime",
        "os.stat",
    }
)
_IMREAD_ROOTS = frozenset({"matplotlib", "imageio", "skimage", "cv2", "scipy"})
_NUMPY_READ = frozenset({"load", "loadtxt", "genfromtxt", "fromfile"})
_NUMPY_WRITE = frozenset({"save", "savez", "savez_compressed", "savetxt"})
#: 实参是路径的写出方法（`df.to_csv(P)` 之类，接收者不是路径）
_DATA_WRITE_TAILS = frozenset(
    {"to_csv", "to_excel", "to_parquet", "to_json", "to_pickle", "to_hdf", "to_feather", "tofile"}
)
_PATH_CTORS = frozenset(
    {
        "pathlib.Path",
        "pathlib.PurePath",
        "pathlib.PosixPath",
        "pathlib.WindowsPath",
        "pathlib.PurePosixPath",
        "pathlib.PureWindowsPath",
    }
)
_FILE_LIKE_CTORS = frozenset({"io.BytesIO", "io.StringIO"})
_STR_PASS = frozenset(
    {"str", "os.fspath", "os.path.abspath", "os.path.realpath", "os.path.normpath"}
)

# 事件种类
_E_SAVEFIG, _E_WRITE, _E_MOVE, _E_READ, _E_STAT = "savefig", "write", "move", "read", "stat"


# ---------------------------------------------------------------- 路径值
@dataclasses.dataclass(frozen=True)
class PathVal:
    """静态折叠出来的路径：`base` 说明相对什么，`text` 是规范化后的 POSIX 文本。

    base：`cwd`（相对运行目录）· `script`（相对脚本所在目录，`<file>` 是脚本自己）· `abs`（字面绝对路径）· `tmp`
    （系统临时目录之下）。`pathobj` 区分 `pathlib` 对象与普通字符串（只有路径对象的 `/` 才是拼接、`.replace` 才是改名）。"""

    base: str
    text: str
    pathobj: bool = False


def _is_abs_text(text: str) -> bool:
    """POSIX 绝对、Windows 盘符 / UNC / **单分隔符根**（`\\foo`，当前盘符的根）/ 盘符相对（`C:foo`）都算「不是项目内相对路径」。
    用 `ntpath` 判，不依赖当前平台：路由在哪个平台算，脚本都可能在 Windows 上跑。"""
    return bool(text.startswith(("/", "\\", "~")) or ntpath.splitdrive(text)[0])


def _norm(text: str) -> str:
    out = posixpath.normpath(text.replace("\\", "/")) if text else "."
    return out


def _from_text(text: str, *, pathobj: bool = False) -> PathVal | None:
    if text.startswith("~"):
        return None  # 用户目录：运行时才定
    if _is_abs_text(text):
        return PathVal("abs", _norm(text), pathobj)
    return PathVal("cwd", _norm(text), pathobj)


def _join(left: PathVal, right: PathVal, *, pathobj: bool) -> PathVal:
    if right.base == "abs":
        return dataclasses.replace(right, pathobj=pathobj)
    if right.base != "cwd":
        return dataclasses.replace(left, text="<mixed>", pathobj=pathobj)
    if left.text == "<mixed>":
        return left
    return PathVal(left.base, _norm(posixpath.join(left.text, right.text)), pathobj)


def _parent(v: PathVal) -> PathVal | None:
    if v.text == "<mixed>":
        return None
    if v.base == "script" and v.text == "<file>":
        return PathVal("script", ".", v.pathobj)
    if v.base == "abs":
        parent = posixpath.dirname(v.text) or v.text
        return PathVal("abs", parent, v.pathobj)
    return PathVal(v.base, _norm(posixpath.join(v.text, "..")), v.pathobj)


# ---------------------------------------------------------------- 事实
@dataclasses.dataclass(frozen=True)
class Event:
    kind: str  # _E_*
    op: str  # 公开的操作名（不含路径）
    line: int
    col: int
    scope: int
    branches: tuple[tuple[int, int], ...]
    path: PathVal | None  # 主路径（move 的源）；折叠不出 = None（动态）
    dest: PathVal | None = None  # move 的目的
    #: savefig 的接收者确实解析到 matplotlib（`plt.savefig` / 来自 `plt.figure()` 等的 Figure）；其它事件恒 True
    certain: bool = True


@dataclasses.dataclass(frozen=True)
class ScriptFacts:
    """一份脚本的静态事实（一次 `ast.parse` 的产物，与 cwd 档 / 项目根无关，可缓存）。不含源码与路径文本的公开投影。"""

    parsed: bool
    too_large: bool = False
    events: tuple[Event, ...] = ()
    parse_ms: float = 0.0
    #: 模块执行时会真走到的作用域（模块顶层、类体，以及被可达代码按名字调用到的函数体）。其余作用域里的证据不够直达 native
    live_scopes: frozenset[int] = frozenset()


_EMPTY = ScriptFacts(parsed=False)


class _Scope:
    __slots__ = ("node", "parent", "assigns", "poisoned", "ident", "poison_n", "unpacks", "defs")

    def __init__(self, node, parent, ident: int) -> None:
        self.node, self.parent, self.ident = node, parent, ident
        self.assigns: dict[str, list[ast.expr]] = {}
        self.poisoned: set[str] = set()
        self.poison_n: dict[str, int] = {}
        #: `fig, ax = call(...)` 的元组解包：名字 → (调用表达式, 下标)
        self.unpacks: dict[str, tuple[ast.expr, int]] = {}
        #: 本作用域里 `def` 出来的函数：名字 → 函数作用域（同名定义多次 = None）
        self.defs: dict[str, "_Scope | None"] = {}


def _target_names(target: ast.AST) -> list[str]:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


class _Collector:
    """单遍 AST：作用域与赋值表、import 绑定表，然后产出事件。"""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.scopes: dict[int, _Scope] = {}
        self.module = _Scope(tree, None, 0)
        self.scopes[id(tree)] = self.module
        self.aliases: dict[str, str | None] = {}
        self.events: list[Event] = []
        #: 名字在**整个模块任何作用域**被非 import 方式绑定的次数（赋值 / 形参 / def / class / for·with 目标 / global·nonlocal…）。
        #: 保守起见不做作用域精析：别名（`plt`）被重绑过、或接收者名（`fig`）不是恰好绑定一次，一律当解析不了
        self.bind_total: dict[str, int] = {}
        #: 每个作用域里按名字发生的调用：(调用方作用域, 名字)，供可达性推导
        self.calls: list[tuple[_Scope, str]] = []
        #: `with open(...) as fh` 绑出来的文件句柄名：把它交给 `savefig` 不是动态路径
        self.handles: set[str] = set()
        self._next = 1
        self._declare(tree, self.module)

    # ---- 第一遍：绑定
    def _declare(self, node: ast.AST, scope: _Scope) -> None:
        for child in ast.iter_child_nodes(node):
            self._bind(child, scope)

    def _poison(self, scope: _Scope, names, *, imported: bool = False) -> None:
        for n in names:
            scope.poison_n[n] = scope.poison_n.get(n, 0) + 1
            if not imported:
                self.bind_total[n] = self.bind_total.get(n, 0) + 1
        scope.poisoned.update(names)

    def _assign(self, scope: _Scope, name: str, value: ast.expr) -> None:
        scope.assigns.setdefault(name, []).append(value)
        self.bind_total[name] = self.bind_total.get(name, 0) + 1

    def _bind(self, node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._poison(scope, [node.name])
            inner = _Scope(node, scope, self._next)
            self._next += 1
            self.scopes[id(node)] = inner
            scope.defs[node.name] = None if node.name in scope.defs else inner
            args = node.args
            names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
            self._poison(inner, names)
            for dec in node.decorator_list:
                self._bind(dec, scope)
            for default in (*args.defaults, *(d for d in args.kw_defaults if d is not None)):
                self._bind(default, scope)
            for stmt in node.body:
                self._bind(stmt, inner)
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.ClassDef):
            self._poison(scope, [node.name])
            # 类体不是名字解析的作用域：里面的赋值不进任何表，方法按函数处理
            inner = _Scope(node, scope, self._next)
            self._next += 1
            self.scopes[id(node)] = inner
            for stmt in node.body:
                self._bind(stmt, inner)
            return
        if isinstance(node, ast.Assign):
            tgt = node.targets[0] if len(node.targets) == 1 else None
            if isinstance(tgt, ast.Name):
                self._assign(scope, tgt.id, node.value)
            elif (
                isinstance(tgt, (ast.Tuple, ast.List))
                and isinstance(node.value, (ast.Tuple, ast.List))
                and len(tgt.elts) == len(node.value.elts)
                and all(isinstance(t, ast.Name) for t in tgt.elts)
                and not any(isinstance(v, ast.Starred) for v in node.value.elts)
            ):
                # `tmp, final = a, b`：逐个按单赋值记（元素个数对得上、没有 `*`）
                for t, v in zip(tgt.elts, node.value.elts, strict=True):
                    self._assign(scope, t.id, v)
            else:
                if (
                    isinstance(tgt, (ast.Tuple, ast.List))
                    and isinstance(node.value, ast.Call)
                    and not any(isinstance(t, ast.Starred) for t in tgt.elts)
                ):
                    for i, t in enumerate(tgt.elts):
                        if isinstance(t, ast.Name):
                            scope.unpacks[t.id] = (node.value, i)
                for t in node.targets:
                    self._poison(scope, _target_names(t))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                self._assign(scope, node.target.id, node.value)
            else:
                self._poison(scope, _target_names(node.target))
        elif isinstance(node, ast.AugAssign):
            self._poison(scope, _target_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._poison(scope, _target_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._poison(scope, _target_names(item.optional_vars))
                    ctx = item.context_expr
                    if (
                        isinstance(item.optional_vars, ast.Name)
                        and isinstance(ctx, ast.Call)
                        and getattr(ctx.func, "id", getattr(ctx.func, "attr", "")) == "open"
                    ):
                        self.handles.add(item.optional_vars.id)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                self._poison(scope, [node.name])
        elif isinstance(node, ast.NamedExpr):
            self._poison(scope, _target_names(node.target))
        elif isinstance(node, ast.comprehension):
            self._poison(scope, _target_names(node.target))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            self._poison(scope, node.names)
            if isinstance(node, ast.Global):
                self.module.poisoned.update(node.names)
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                self._poison(scope, _target_names(t))
        elif isinstance(node, ast.Import):
            for a in node.names:
                bound = a.asname or a.name.split(".")[0]
                self._alias(bound, a.name if a.asname else a.name.split(".")[0])
                self._poison(scope, [bound], imported=True)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                bound = a.asname or a.name
                self._alias(bound, None if node.level else f"{node.module}.{a.name}")
                self._poison(scope, [bound], imported=True)
        elif hasattr(ast, "MatchAs") and isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if getattr(node, "name", None):
                self._poison(scope, [node.name])
        elif hasattr(ast, "MatchMapping") and isinstance(node, ast.MatchMapping):
            if node.rest:
                self._poison(scope, [node.rest])
        for child in ast.iter_child_nodes(node):
            self._bind(child, scope)

    def _alias(self, name: str, qualified: str | None) -> None:
        if name in self.aliases and self.aliases[name] != qualified:
            self.aliases[name] = None  # 同一个名字绑过两个不同的东西：认不准，不解析
        else:
            self.aliases[name] = qualified

    # ---- 解析
    def qual(self, node: ast.AST) -> str | None:
        """`Name` / `Attribute` 链 → 点号限定名（头必须是 import 绑定的名字）。"""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        if self.bind_total.get(node.id):
            return None  # 这个名字在模块里的某处被重新绑定过（形参 / 赋值 / def…）：不信它还是那个 import
        head = self.aliases.get(node.id)
        if head is None:
            return None
        return ".".join([head, *reversed(parts)])

    def _is_builtin(self, node: ast.AST, name: str, scope: _Scope) -> bool:
        if not (isinstance(node, ast.Name) and node.id == name):
            return False
        s: _Scope | None = scope
        while s is not None:
            if name in s.assigns or name in s.poisoned:
                return name in self.aliases and self.aliases[name] in _BUILTIN_OPEN
            s = s.parent
        return True

    def _lookup(self, name: str, scope: _Scope) -> list[ast.expr] | None:
        """名字 → 它**恰好一次**的赋值右值；赋值多次 / 被别的方式绑定 / 找不到 = None。"""
        s: _Scope | None = scope
        while s is not None:
            if s.node is not self.tree and isinstance(s.node, ast.ClassDef):
                s = s.parent
                continue
            if name in s.poisoned:
                return None
            if name in s.assigns:
                vals = s.assigns[name]
                return vals if len(vals) == 1 else None
            s = s.parent
        return None

    def string(self, node: ast.AST | None, scope: _Scope, depth: int = 0) -> str | None:
        """能折叠成一个字符串常量就返回它（f-string / `+` / `%` / `.format` / 单赋值的 Name）。"""
        if node is None or depth > _MAX_DEPTH:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            vals = self._lookup(node.id, scope)
            return self.string(vals[0], scope, depth + 1) if vals else None
        if isinstance(node, ast.JoinedStr):
            out = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    out.append(part.value)
                elif isinstance(part, ast.FormattedValue) and part.format_spec is None:
                    got = self.string(part.value, scope, depth + 1)
                    if got is None:
                        got = self._text_of(part.value, scope, depth + 1)
                    if got is None:
                        return None
                    out.append(got)
                else:
                    return None
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = (
                self.string(node.left, scope, depth + 1),
                self.string(node.right, scope, depth + 1),
            )
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            fmt = self.string(node.left, scope, depth + 1)
            if fmt is None:
                return None
            args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            folded = [self.string(a, scope, depth + 1) for a in args]
            if any(f is None for f in folded):
                return None
            try:
                return fmt % tuple(folded)
            except (TypeError, ValueError):
                return None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and not node.keywords
        ):
            fmt = self.string(node.func.value, scope, depth + 1)
            args = [self.string(a, scope, depth + 1) for a in node.args]
            if fmt is None or any(a is None for a in args):
                return None
            try:
                return fmt.format(*args)
            except (IndexError, KeyError, ValueError):
                return None
        return None

    def _text_of(self, node: ast.AST, scope: _Scope, depth: int) -> str | None:
        """f-string 里 `{p}`：p 是 cwd / 绝对路径值时取它的文本（脚本目录相对的值不当字符串用）。"""
        v = self.value(node, scope, depth)
        return v.text if v is not None and v.base in ("cwd", "abs") else None

    def value(self, node: ast.AST | None, scope: _Scope, depth: int = 0) -> PathVal | None:
        """表达式 → 路径值；折叠不出（动态）= None。"""
        if node is None or depth > _MAX_DEPTH:
            return None
        text = self.string(node, scope, depth)
        if text is not None:
            return _from_text(text)
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return PathVal("script", "<file>")
            vals = self._lookup(node.id, scope)
            return self.value(vals[0], scope, depth + 1) if vals else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = (
                self.value(node.left, scope, depth + 1),
                self.value(node.right, scope, depth + 1),
            )
            if left is None or right is None or not (left.pathobj or right.pathobj):
                return None
            return _join(left, right, pathobj=True)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            inner = self.value(node.value, scope, depth + 1)
            return _parent(inner) if inner is not None and inner.pathobj else None
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
            and 0 <= node.slice.value < 8
        ):
            cur = self.value(node.value.value, scope, depth + 1)
            for _ in range(node.slice.value + 1):
                cur = _parent(cur) if cur is not None and cur.pathobj else None
            return cur
        if isinstance(node, ast.Call):
            return self._call_value(node, scope, depth)
        return None

    def _call_value(self, node: ast.Call, scope: _Scope, depth: int) -> PathVal | None:
        func = node.func
        q = self.qual(func)
        args = [self.value(a, scope, depth + 1) for a in node.args]
        if q in _PATH_CTORS:
            if not node.args:
                return PathVal("cwd", ".", True)
            if any(a is None for a in args):
                return None
            cur = args[0]
            for a in args[1:]:
                cur = _join(cur, a, pathobj=True)
            return dataclasses.replace(cur, pathobj=True)
        if q in ("pathlib.Path.cwd", "os.getcwd"):
            return PathVal("cwd", ".", q == "pathlib.Path.cwd")
        if q == "tempfile.gettempdir":
            return PathVal("tmp", ".")
        if q == "os.path.join":
            if not args or any(a is None for a in args):
                return None
            cur = args[0]
            for a in args[1:]:
                cur = _join(cur, a, pathobj=False)
            return cur
        if q in _STR_PASS:
            if len(args) == 1 and args[0] is not None:
                return dataclasses.replace(args[0], pathobj=False)
            return None
        if q == "os.path.dirname":
            return _parent(args[0]) if len(args) == 1 and args[0] is not None else None
        if q == "os.path.basename":
            if len(args) == 1 and args[0] is not None and args[0].text not in ("<file>", "<mixed>"):
                return PathVal("cwd", posixpath.basename(args[0].text) or ".")
            return None
        if isinstance(func, ast.Attribute):
            recv = self.value(func.value, scope, depth + 1)
            if recv is None or not recv.pathobj:
                return None
            name = func.attr
            if name in ("resolve", "absolute"):
                return recv
            if name == "joinpath":
                if any(a is None for a in args) or node.keywords:
                    return None
                cur = recv
                for a in args:
                    cur = _join(cur, a, pathobj=True)
                return cur
            if name in ("with_suffix", "with_name") and len(node.args) == 1:
                tail = self.string(node.args[0], scope, depth + 1)
                if tail is None or recv.text in ("<file>", "<mixed>"):
                    return None
                base = posixpath.dirname(recv.text) if name == "with_name" else recv.text
                root, _ext = posixpath.splitext(recv.text)
                new = (
                    posixpath.join(base, tail)
                    if name == "with_name"
                    else (root if tail else recv.text) + tail
                )
                return PathVal(recv.base, _norm(new), True)
        return None

    # ---- 第二遍：事件
    def collect(self) -> None:
        self._walk(self.tree, self.module, ())

    def _walk(self, node: ast.AST, scope: _Scope, branches: tuple) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = self.scopes.get(id(node), scope)
            for dec in node.decorator_list:
                self._walk(dec, scope, branches)
            for stmt in node.body:
                self._walk(stmt, inner, branches)
            return
        if isinstance(node, (ast.If, ast.While)) and self._constant_false(node.test):
            # 条件是常量 False 的 `if` / `while`：body 永远不执行，里面的调用与证据都不算（`else` 照常）。只认这一种形状
            self._walk(node.test, scope, branches)
            for stmt in node.orelse:
                self._walk(stmt, scope, (*branches, (id(node), 1)))
            return
        if isinstance(node, ast.If):
            self._walk(node.test, scope, branches)
            for stmt in node.body:
                self._walk(stmt, scope, (*branches, (id(node), 0)))
            for stmt in node.orelse:
                self._walk(stmt, scope, (*branches, (id(node), 1)))
            return
        if isinstance(node, ast.Match):
            self._walk(node.subject, scope, branches)
            for i, case in enumerate(node.cases):
                for stmt in case.body:
                    self._walk(stmt, scope, (*branches, (id(node), i)))
            return
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                self.calls.append((scope, node.func.id))
            self._call(node, scope, branches)
        for child in ast.iter_child_nodes(node):
            self._walk(child, scope, branches)

    def live_scopes(self) -> frozenset[int]:
        """模块执行真会走到的作用域：模块、类体（定义时执行）、被可达作用域按名字调用到的函数。"""
        live: set[int] = {self.module.ident}
        for sc in self.scopes.values():
            if isinstance(sc.node, ast.ClassDef) and sc.parent is not None:
                pass  # 类体随其父作用域是否可达，下面迭代里处理
        changed = True
        while changed:
            changed = False
            for sc in self.scopes.values():
                if sc.ident not in live and isinstance(sc.node, ast.ClassDef):
                    if sc.parent is not None and sc.parent.ident in live:
                        live.add(sc.ident)
                        changed = True
            for caller, name in self.calls:
                if caller.ident not in live:
                    continue
                s: _Scope | None = caller
                while s is not None:
                    if name in s.defs:
                        target = s.defs[name]
                        if target is not None and target.ident not in live:
                            live.add(target.ident)
                            changed = True
                        break
                    s = s.parent
        return frozenset(live)

    @staticmethod
    def _constant_false(test: ast.expr) -> bool:
        if isinstance(test, ast.Constant):
            return not test.value
        if isinstance(test, ast.Name):
            return test.id == "TYPE_CHECKING"
        return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"

    def _add(self, kind, op, node, scope, branches, path, dest=None, certain=True) -> None:
        self.events.append(
            Event(
                kind=kind,
                op=op,
                line=node.lineno,
                col=node.col_offset,
                scope=scope.ident,
                branches=branches,
                path=path,
                dest=dest,
                certain=certain,
            )
        )

    @staticmethod
    def _arg(node: ast.Call, index: int, *names: str) -> ast.expr | None:
        if len(node.args) > index and not isinstance(node.args[index], ast.Starred):
            return node.args[index]
        for kw in node.keywords:
            if kw.arg in names:
                return kw.value
        return None

    def _mode(self, node: ast.Call, scope: _Scope, index: int) -> str | None:
        arg = self._arg(node, index, "mode")
        if arg is None:
            return "r"
        return self.string(arg, scope)

    def _file_like(self, node: ast.AST | None, scope: _Scope) -> bool:
        """第一个参数明显是文件对象（`BytesIO()` / `open(...)` 句柄）：不是路径、也不是动态路径。"""
        if isinstance(node, ast.Call):
            q = self.qual(node.func)
            return (
                q in _FILE_LIKE_CTORS
                or q in _BUILTIN_OPEN
                or self._is_builtin(node.func, "open", scope)
            )
        if isinstance(node, ast.Name):
            if node.id in self.handles:
                return True
            vals = self._lookup(node.id, scope)
            return bool(vals) and self._file_like(vals[0], scope)
        return False

    _FIGURE_MAKERS = frozenset(
        {"matplotlib.pyplot.figure", "matplotlib.pyplot.gcf", "matplotlib.figure.Figure"}
    )

    def _is_figure(self, node: ast.AST | None, scope: _Scope, depth: int = 0) -> bool:
        """表达式确实是一个 matplotlib Figure：`plt.figure()` / `plt.gcf()` / `Figure()`，或由它们（单次）赋值 /
        `fig, ax = plt.subplots()` 解包得来的名字。解析不到 = False（不猜）。"""
        if node is None or depth > _MAX_DEPTH:
            return False
        if isinstance(node, ast.Call):
            return self.qual(node.func) in self._FIGURE_MAKERS
        if isinstance(node, ast.Name):
            if self.bind_total.get(node.id) != 1:
                return False  # 不是恰好绑定一次（别的作用域里同名的形参 / 赋值也算）：不猜
            vals = self._lookup(node.id, scope)
            if vals:
                return self._is_figure(vals[0], scope, depth + 1)
            s: _Scope | None = scope
            while s is not None:
                if node.id in s.poisoned or node.id in s.assigns:
                    pair = s.unpacks.get(node.id)
                    return (
                        pair is not None
                        and s.poison_n.get(node.id) == 1
                        and node.id not in s.assigns
                        and pair[1] == 0
                        and isinstance(pair[0], ast.Call)
                        and self.qual(pair[0].func) == "matplotlib.pyplot.subplots"
                    )
                s = s.parent
        return False

    def _savefig_is_matplotlib(self, func: ast.AST, scope: _Scope) -> bool:
        if self.qual(func) == "matplotlib.pyplot.savefig":
            return True
        return isinstance(func, ast.Attribute) and self._is_figure(func.value, scope)

    def _call(self, node: ast.Call, scope: _Scope, branches: tuple) -> None:
        func = node.func
        q = self.qual(func)
        tail = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        recv = None
        if isinstance(func, ast.Attribute):
            recv = self.value(func.value, scope)

        def at(kind, op, path, dest=None):
            self._add(kind, op, node, scope, branches, path, dest)

        # savefig：任何接收者（plt / fig / Figure）；实参是文件对象的不算
        if tail == "savefig":
            target = self._arg(node, 0, "fname")
            if target is not None and not self._file_like(target, scope):
                self._add(
                    _E_SAVEFIG,
                    "savefig",
                    node,
                    scope,
                    branches,
                    self.value(target, scope),
                    certain=self._savefig_is_matplotlib(func, scope),
                )
            return
        if q in _MOVE_FUNCS:
            at(
                _E_MOVE,
                _MOVE_FUNCS[q],
                self.value(self._arg(node, 0, "src", "path"), scope),
                self.value(self._arg(node, 1, "dst"), scope),
            )
            return
        if recv is not None and recv.pathobj:
            if tail in ("replace", "rename"):
                at(_E_MOVE, f"Path.{tail}", recv, self.value(self._arg(node, 0, "target"), scope))
            elif tail in ("write_text", "write_bytes"):
                at(_E_WRITE, f"Path.{tail}", recv)
            elif tail in ("read_text", "read_bytes"):
                at(_E_READ, f"Path.{tail}", recv)
            elif tail in ("exists", "is_file", "stat", "is_dir"):
                at(_E_STAT, f"Path.{tail}", recv)
            elif tail == "open":
                mode = self._mode(node, scope, 0)
                if mode is not None and any(c in mode for c in "wxa"):
                    at(_E_WRITE, "Path.open", recv)
                else:
                    at(_E_READ, "Path.open", recv)
            return
        if q in _BUILTIN_OPEN or self._is_builtin(func, "open", scope):
            target = self._arg(node, 0, "file")
            mode = self._mode(node, scope, 1)
            if target is None or self._file_like(target, scope):
                return
            if mode is None:
                return  # 模式是动态的：不判
            path = self.value(target, scope)
            if any(c in mode for c in "wxa"):
                at(_E_WRITE, "open", path)
            else:
                at(_E_READ, "open", path)
            return
        if q in _STAT_FUNCS:
            at(_E_STAT, q, self.value(self._arg(node, 0, "path"), scope))
            return
        if q is not None:
            root = q.split(".", 1)[0]
            last = q.rsplit(".", 1)[-1]
            read = (
                q == "PIL.Image.open"
                or (last == "imread" and root in _IMREAD_ROOTS)
                or (root == "numpy" and last in _NUMPY_READ)
                or (root == "pandas" and last.startswith("read_"))
            )
            if read:
                at(
                    _E_READ,
                    q.rsplit(".", 1)[-1],
                    self.value(self._arg(node, 0, "fname", "fp"), scope),
                )
                return
            if (root == "numpy" and last in _NUMPY_WRITE) or (
                root == "matplotlib" and last == "imsave"
            ):
                at(_E_WRITE, last, self.value(self._arg(node, 0, "file", "fname"), scope))
                return
        if tail in _DATA_WRITE_TAILS and recv is None:
            target = self._arg(node, 0, "path_or_buf", "path", "fname")
            if target is not None and not self._file_like(target, scope):
                at(_E_WRITE, tail, self.value(target, scope))


def _collect(source: str) -> ScriptFacts:
    started = time.perf_counter()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _EMPTY
    try:
        col = _Collector(tree)
        col.collect()
    except RecursionError:
        return _EMPTY
    return ScriptFacts(
        parsed=True,
        events=tuple(sorted(col.events, key=lambda e: (e.line, e.col))),
        live_scopes=col.live_scopes(),
        parse_ms=(time.perf_counter() - started) * 1000.0,
    )


def facts_of(source: str) -> ScriptFacts:
    """源码 → 静态事实。一次 `ast.parse`；语法错误 / 过大 = 未解析（路由按普通绘图走，并如实标注）。"""
    if len(source.encode("utf-8", "replace")) > MAX_SOURCE_BYTES:
        return ScriptFacts(parsed=False, too_large=True)
    return _collect(source)


_cache: OrderedDict[tuple, ScriptFacts] = OrderedDict()
_cache_lock = threading.Lock()


def facts_of_file(path: str | os.PathLike) -> ScriptFacts:
    """脚本文件 → 静态事实，64 项 LRU，键 `(realpath, mtime_ns, size)`（与 `dialogscan` 同款）。读不到 = 未解析。"""
    try:
        real = os.path.realpath(path)
        st = os.stat(real)
    except OSError:
        return _EMPTY
    key = (real, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    if st.st_size > MAX_SOURCE_BYTES:
        facts = ScriptFacts(parsed=False, too_large=True)
    else:
        try:
            source = Path(real).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return _EMPTY
        facts = _collect(source)
    with _cache_lock:
        _cache[key] = facts
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return facts


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- 路由结果
@dataclasses.dataclass(frozen=True)
class Route:
    """路由决定 + 原因 + 证据。不含路径 / 源码：`public()` 可进本地诊断。"""

    execution: str
    reason: str
    confidence: str
    evidence: tuple[dict, ...] = ()
    dynamic_paths: int = 0
    blind_spots: tuple[str, ...] = ()
    #: 想直达 native 却没做成的原因（如没有解释器）；空 = 无
    declined: str = ""
    elapsed_ms: float = 0.0

    @property
    def required_execution(self) -> str:
        return (
            REQUIRED_EXECUTION_FILE_SEMANTICS
            if self.reason in (REASON_SAVEFIG_READBACK, REASON_NATIVE_FILE_SEMANTICS)
            else ""
        )

    def public(self) -> dict:
        return {
            "execution": self.execution,
            "reason": self.reason,
            "confidence": self.confidence,
            "required_execution": self.required_execution,
            "rules": sorted({e["rule"] for e in self.evidence}),
            "evidence": [dict(e) for e in self.evidence],
            "dynamic_paths": self.dynamic_paths,
            "blind_spots": list(self.blind_spots),
            "declined": self.declined,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }

    def downgraded(self, why: str) -> "Route":
        """想直达 native 但做不到（没有解释器）：回 safe，原因与证据保留，记下为什么没成。"""
        return dataclasses.replace(
            self, execution=EXECUTION_SAFE, declined=why, confidence=CONFIDENCE_LOW
        )


# ---------------------------------------------------------------- 规范化与「在不在真实图库内」
@dataclasses.dataclass(frozen=True)
class _Where:
    root: str | None  # 项目根
    roots_abs: tuple[str, ...]
    cwd_mode: str | None
    script_dir: str  # 项目相对，"." = 项目根


def _where(project_root: str | os.PathLike, script: str, cwd_mode: str | None) -> _Where:
    raw = os.path.abspath(str(project_root))
    roots = {_norm(raw)}
    try:
        roots.add(_norm(os.path.realpath(raw)))
    except OSError:
        pass
    sd = posixpath.dirname(str(script).replace("\\", "/")) or "."
    return _Where(root=_norm(raw), roots_abs=tuple(sorted(roots)), cwd_mode=cwd_mode, script_dir=sd)


def _canon(v: PathVal | None, w: _Where) -> tuple[str, str] | None:
    """路径值 → 可比较的规范键 `(区域, 文本)`。`root` 区 = 项目根之内（文本相对项目根）；其余区域各自独立。"""
    if v is None or v.text in ("<mixed>", "<file>"):
        return None
    if v.base == "tmp":
        return ("tmp", v.text)
    if v.base == "abs":
        for r in w.roots_abs:
            if v.text == r:
                return ("root", ".")
            if v.text.startswith(r.rstrip("/") + "/"):
                return ("root", _norm(v.text[len(r.rstrip("/")) + 1 :]))
        return ("abs", v.text)
    if v.base == "script":
        rel = _norm(posixpath.join(w.script_dir, v.text))
    else:  # cwd
        if w.cwd_mode == CWD_PROJECT_ROOT:
            rel = v.text
        elif w.cwd_mode == CWD_PROJECT:
            rel = _norm(posixpath.join(w.script_dir, v.text))
        else:
            return ("sandbox", v.text)
    if rel == ".." or rel.startswith("../"):
        return ("outside", rel)  # 逃出项目根：是否仍在图库内算不准，按「不在」处理（不升格）
    return ("root", rel)


def _exclusive(a: Event, b: Event) -> bool:
    arms = dict(a.branches)
    return any(node in arms and arms[node] != arm for node, arm in b.branches)


def _after(later: Event, earlier: Event) -> bool:
    return (later.line, later.col) > (earlier.line, earlier.col) and not _exclusive(earlier, later)


# ---------------------------------------------------------------- 决定
def _static(facts: ScriptFacts, w: _Where) -> tuple[list[dict], int, bool, bool]:
    """跑 R1–R4 → `(证据, 动态路径数, 有 R1 高置信, 有 R3 高置信)`。"""
    evidence: list[dict] = []
    dynamic = 0
    high_r1 = high_r3 = False
    events = facts.events
    keyed = [(e, _canon(e.path, w)) for e in events]
    for e, k in keyed:
        if e.kind in (_E_SAVEFIG, _E_WRITE, _E_MOVE) and k is None:
            dynamic += 1
        if e.kind == _E_MOVE and _canon(e.dest, w) is None and e.path is not None:
            dynamic += 1

    def ev(rule, kind, op, line, other=None):
        item = {"rule": rule, "kind": kind, "op": op, "line": line, "source": "static"}
        if other is not None:
            item["read_line"] = other
        evidence.append(item)

    seen: set[tuple] = set()

    for s, sk in keyed:
        if s.kind != _E_SAVEFIG or sk is None:
            continue
        # R1 / R2：同一规范路径、之后出现的读 / 探
        for r, rk in keyed:
            if rk != sk or r.kind not in (_E_READ, _E_STAT) or not _after(r, s):
                continue
            same_scope = r.scope == s.scope
            if r.kind == _E_READ:
                if not same_scope:
                    rule = RULE_R1_CROSS_SCOPE
                elif s.certain and s.scope in facts.live_scopes:
                    rule = RULE_R1
                    high_r1 = True
                else:
                    # 接收者解析不到 matplotlib（用户对象 / 同名函数 / 参数），或证据在模块没调用到的函数体里：只记，不直达
                    rule = RULE_R1_UNPROVEN
            else:
                rule = RULE_R2
            if (rule, s.line, r.line) not in seen:
                seen.add((rule, s.line, r.line))
                ev(rule, KIND_SAVEFIG, "savefig", s.line, r.line)
        # 不同作用域里的读回也在上面按 R1_cross_scope 记了（_after 只看行序）；这里不再重复

    for m, mk in keyed:
        if m.kind != _E_MOVE or mk is None:
            continue
        dk = _canon(m.dest, w)
        inside = mk[0] == "root"  # safe 的删除 / 改名守卫只看源是否在真实图库内
        if not inside:
            continue
        wrote = any(
            wk == mk
            and x.kind in (_E_WRITE, _E_SAVEFIG)
            and x.certain
            and x.scope == m.scope
            and _after(m, x)
            for x, wk in keyed
        )
        read_line = None
        if dk is not None:
            for r, rk in keyed:
                if (
                    rk == dk
                    and r.kind in (_E_READ, _E_STAT)
                    and r.scope == m.scope
                    and _after(r, m)
                ):
                    read_line = r.line
                    break
        if wrote and read_line is not None and m.scope in facts.live_scopes:
            ev(RULE_R3, KIND_RENAME, m.op, m.line, read_line)
            high_r3 = True
        else:
            ev(RULE_R3_PARTIAL, KIND_RENAME, m.op, m.line, read_line)
    if dynamic:
        evidence.append(
            {
                "rule": RULE_R4,
                "kind": KIND_WRITE,
                "op": "dynamic_path",
                "line": 0,
                "source": "static",
            }
        )
    return evidence, dynamic, high_r1, high_r3


def decide(
    facts: ScriptFacts | None,
    *,
    cwd_mode: str | None,
    project_root: str | os.PathLike,
    script: str,
    policy: str = POLICY_COMPATIBLE,
    last_success: str | None = None,
    operator_enabled: bool = True,
) -> Route:
    """脚本事实 + 运行目录档 + 项目根 + 策略 + 上次成功的执行语义 → 路由。纯函数（`realpath(project_root)` 之外无 IO）。

    优先级：用户显式（`safe_only`）· 运维开关 > 上次成功沿用 > 静态高置信 > 默认 safe。
    `last_success` 由调用方核过（`launchctx.verify`：脚本 / 解释器 / 运行目录没变）后才传，本模块不判其真假。"""
    if policy == POLICY_SAFE_ONLY:
        return Route(EXECUTION_SAFE, REASON_USER_EXPLICIT, CONFIDENCE_HIGH)
    if not operator_enabled:
        return Route(EXECUTION_SAFE, REASON_OPERATOR_DISABLED, CONFIDENCE_HIGH)
    if last_success in EXECUTIONS:
        return Route(last_success, REASON_PREVIOUS_SUCCESS, CONFIDENCE_HIGH)
    if facts is None or not facts.parsed:
        blind = BLIND_TOO_LARGE if facts is not None and facts.too_large else BLIND_UNPARSED
        return Route(EXECUTION_SAFE, REASON_ORDINARY_PLOT, CONFIDENCE_LOW, blind_spots=(blind,))
    evidence, dynamic, high_r1, high_r3 = _static(facts, _where(project_root, script, cwd_mode))
    blind = BLIND_SPOTS
    if high_r1:
        return Route(
            EXECUTION_NATIVE,
            REASON_SAVEFIG_READBACK,
            CONFIDENCE_HIGH,
            tuple(evidence),
            dynamic,
            blind,
        )
    if high_r3:
        return Route(
            EXECUTION_NATIVE,
            REASON_NATIVE_FILE_SEMANTICS,
            CONFIDENCE_HIGH,
            tuple(evidence),
            dynamic,
            blind,
        )
    if evidence and any(e["rule"] != RULE_R4 for e in evidence):
        confidence = CONFIDENCE_MEDIUM
    elif dynamic:
        confidence = CONFIDENCE_LOW
    else:
        confidence = CONFIDENCE_HIGH
    return Route(EXECUTION_SAFE, REASON_ORDINARY_PLOT, confidence, tuple(evidence), dynamic, blind)


def route_for_file(
    script_path: str | os.PathLike,
    *,
    cwd_mode: str | None,
    project_root: str | os.PathLike,
    script: str,
    policy: str = POLICY_COMPATIBLE,
    last_success: str | None = None,
    operator_enabled: bool = True,
) -> Route:
    """`facts_of_file` + `decide`，并量总耗时（`Route.elapsed_ms`，本地诊断 / 基准读；不进遥测）。
    用户显式 / 运维关闭 / 沿用上次成功这三条不需要读脚本，直接决定。"""
    started = time.perf_counter()
    needs_facts = policy != POLICY_SAFE_ONLY and operator_enabled and last_success not in EXECUTIONS
    facts = facts_of_file(script_path) if needs_facts else None
    route = decide(
        facts,
        cwd_mode=cwd_mode,
        project_root=project_root,
        script=script,
        policy=policy,
        last_success=last_success,
        operator_enabled=operator_enabled,
    )
    return dataclasses.replace(route, elapsed_ms=(time.perf_counter() - started) * 1000.0)
