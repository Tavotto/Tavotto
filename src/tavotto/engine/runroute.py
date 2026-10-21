"""运行前路由（V2 PR2，ADR 0133）：用户在桌面「打开并运行」一份脚本时，**执行之前**凭静态证据挑 `safe` 还是 `native`。

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
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

# figcapture：守卫操作表的唯一出处（BlockedFileOps.OPS），见 _guarded_call；scriptargs：按 PEP 263 解码源码的唯一实现
from tavotto.engine import figcapture, scriptargs

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
#: 伤害闸门：脚本里出现 safe 守卫会拦的文件操作形状，运行前不直达 native（ADR 0133「伤害闸门」）
REASON_GUARDED_FILE_OPS = "guarded_file_ops_present"
#: 不可证明门槛：脚本里出现静态分析无法证明调用目标的动态分派（动态名的 `getattr`、`importlib` / `__import__` 取模块、
#: `exec` / `eval` / `compile`、`globals()` / `__dict__` / `sys.modules` 取名），运行前不直达 native（ADR 0133「不可证明门槛」）
REASON_UNPROVABLE_DYNAMIC = "unprovable_dynamic_dispatch"
REASONS = (
    REASON_ORDINARY_PLOT,
    REASON_SAVEFIG_READBACK,
    REASON_NATIVE_FILE_SEMANTICS,
    REASON_PREVIOUS_SUCCESS,
    REASON_USER_EXPLICIT,
    REASON_OPERATOR_DISABLED,
    REASON_GUARDED_FILE_OPS,
    REASON_UNPROVABLE_DYNAMIC,
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
#: 规则配对的比较预算：超出 = 不给高置信（回 safe），不为病态的生成脚本付 n² 的代价
_PAIR_BUDGET = 200_000


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
#: 只认与本机路径语义一致的构造器：另一种风格的纯路径（POSIX 上的 `PureWindowsPath` 序列化成 `a\\b.png`）
#: 与按字面折叠的文本不是同一个文件，不认（Codex #1034 r4284099949）
_PATH_CTORS = frozenset({"pathlib.Path", "pathlib.PurePath"}) | (
    frozenset({"pathlib.WindowsPath", "pathlib.PureWindowsPath"})
    if os.sep == "\\"
    else frozenset({"pathlib.PosixPath", "pathlib.PurePosixPath"})
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
    """规范化路径文本。证明不了文件系统语义的一律变成 `<mixed>`（比较时 = 不可比，不升格；Codex #1034 r4283860490）：
    POSIX 上反斜杠是文件名字符而不是分隔符；任何 `..` 段经符号链接可能指向别处，不能按字面折叠。"""
    if not text:
        return "."
    if "\\" in text and os.sep != "\\":  # 只有 Windows 的分隔符是反斜杠
        return "<mixed>"
    t = text.replace("\\", "/")
    if ".." in t.split("/"):
        return "<mixed>"
    return posixpath.normpath(t)


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
    # 字面取上级（`Path.parent` 的语义），不经 `..` 段：`_norm` 把含 `..` 的路径视为证明不了
    parent = posixpath.dirname(v.text.rstrip("/"))
    if v.text in (".", ""):
        return PathVal(v.base, "<mixed>", v.pathobj)
    return PathVal(v.base, parent or ".", v.pathobj)


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
    #: 在会无条件执行的位置（模块 / 函数体顶层语句序列；不在条件 / 循环 / try / with 体 / lambda / 推导式里）
    top: bool = True
    #: 调用表达式的结束坐标：嵌在它实参里的调用先于它求值（`savefig(p, metadata={"T": open(p).read()})`）
    end_line: int = 0
    end_col: int = 0


@dataclasses.dataclass(frozen=True)
class ScriptFacts:
    """一份脚本的静态事实（一次 `ast.parse` 的产物，与 cwd 档 / 项目根无关，可缓存）。不含源码与路径文本的公开投影。"""

    parsed: bool
    too_large: bool = False
    events: tuple[Event, ...] = ()
    parse_ms: float = 0.0
    #: 模块执行时会真走到的作用域（模块顶层、类体，以及被可达代码按名字调用到的函数体）。其余作用域里的证据不够直达 native
    live_scopes: frozenset[int] = frozenset()
    #: 全文件任何位置（含函数体 / 条件 / 类体 / lambda，不做可达性推导）出现 safe 守卫会拦的文件操作调用形状
    guarded_ops: bool = False
    #: 脚本里任何位置改了当前目录（`os.chdir` / `os.fchdir` / `contextlib.chdir`…）：cwd 相对的证据前后可能不是同一个目录
    cwd_changes: bool = False
    #: 脚本里任何位置出现静态无法证明调用目标的动态分派：此时「没看到守卫操作」不等于「没有守卫操作」（失败开放），不允许直达 native
    dynamic_dispatch: bool = False


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
                elif (
                    isinstance(part, ast.FormattedValue)
                    and part.format_spec is None
                    and part.conversion
                    in (-1, ord("s"))  # `!r` / `!a` 会加引号 / 转义，不按原值折叠
                ):
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
                    else root + tail  # `with_suffix("")` 去掉后缀（pathlib 语义），不是保留原名
                )
                return PathVal(recv.base, _norm(new), True)
        return None

    # ---- 伤害闸门
    #: 守卫表同源：`BlockedFileOps.OPS` 减去 savefig（safe worker 守卫记账的操作名）
    _GUARDED = tuple(op for op in figcapture.BlockedFileOps.OPS if op != "savefig")
    #: 带模块前缀的限定名（`os.remove` / `shutil.rmtree`…）
    _GUARDED_QUALIFIED = frozenset(op for op in _GUARDED if op.split(".")[0] in ("os", "shutil"))
    #: 方法名太泛（`list.remove` / `str.replace` / `obj.move`）的，只认限定名（`replace` 另按单实参判）
    _GENERIC_TAILS = frozenset(
        {"remove", "replace", "move", "rename"}
    )  # `df.rename(columns=…)` 另按单实参判
    _GUARDED_TAILS = frozenset(op.rsplit(".", 1)[1] for op in _GUARDED) - _GENERIC_TAILS

    _MODE_LIKE = re.compile(r"[rwxabtU+]+")
    _READONLY_OPEN_ROOTS = ("PIL.",)
    #: 方法名 / 限定名末段即可判定为写出的调用（savefig 不在内：它是证据）
    _WRITER_TAILS = (
        _NUMPY_WRITE
        | _DATA_WRITE_TAILS
        | frozenset(
            {
                "save",
                "imsave",
                "imwrite",
                "savemat",
                "to_netcdf",
                "to_zarr",
                "to_stata",
                "to_sql",
                "write_image",
                "write_html",
                "symlink_to",
                "hardlink_to",
                "chmod",
                "rmdir",
                "unlink",
                "write_text",
                "write_bytes",
                "mkdir",
                "touch",
            }
        )
    )
    #: 只认限定名的写 / 改元数据调用
    _WRITER_QUALIFIED = frozenset(
        {
            "os.write",
            "os.truncate",
            "os.ftruncate",
            "os.symlink",
            "os.link",
            "os.chmod",
            "os.chown",
            "os.utime",
            "os.mkfifo",
            "os.renames",
            "os.removedirs",
            "os.mkdir",
            "os.makedirs",
            "sqlite3.connect",
            "dbm.open",
            "shelve.open",
        }
    )
    _WRITER_PREFIXES = ("shutil.copy", "shutil.make_archive", "shutil.unpack_archive")
    #: `to_*` / `write_*` 这一族（pandas `to_xml` / `to_latex` / `to_markdown`、polars `write_csv`、xarray `to_netcdf`……）按**形态**
    #: 收口而不是逐个列名（Codex #1034 r4286232612）：带了实参就算写出；只排除常见的、从不写文件的转换方法。多判只会回 safe
    _CONVERT_TAILS = frozenset(
        {
            "to_numpy",
            "to_dict",
            "to_list",
            "to_frame",
            "to_series",
            "to_datetime",
            "to_numeric",
            "to_timedelta",
            "to_period",
            "to_timestamp",
            "to_records",
            "to_dense",
            "to_flat_index",
            "to_pydatetime",
            "to_offset",
            "to_rgba",
            "to_rgba_array",
            "to_rgb",
            "to_hex",
            "to_tuple",
            "to_coo",
            "to_array",
            "to_dataset",
            "to_dataframe",
            "to_pandas",
            "to_xarray",
            "to_masked_array",
            "to_device",
            "to_bytes",
            "to_value",
            "to_unit",
            "to_polygons",
            "to_path",
            "to_mask",
        }
    )
    #: 第二个实参是 mode 时这些字符串方法会撞形（`s.replace("x", "a")`），不按 mode 判
    _STR_METHODS = frozenset(
        {
            "replace",
            "split",
            "rsplit",
            "strip",
            "join",
            "count",
            "find",
            "index",
            "get",
            "partition",
        }
    )

    def _mode_arg_writes(self, node: ast.Call, func: ast.expr) -> bool:
        """任意调用的 `mode=` 实参，或第二个位置实参是 mode 样字面量（`ZipFile(p, "w")` / `h5py.File(p, "a")` /
        `tarfile.open(p, "w:gz")` / `gzip.open(p, "wb")`），且可能写：按写出算。"""
        for k in node.keywords:
            if (
                k.arg == "mode"
                and isinstance(k.value, ast.Constant)
                and isinstance(k.value.value, str)
            ):
                if self._mode_writes(k.value):
                    return True
        if isinstance(func, ast.Attribute) and func.attr in self._STR_METHODS:
            return False
        if len(node.args) > 1:
            a = node.args[1]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                head = a.value.split(":", 1)[0]
                if head and self._MODE_LIKE.fullmatch(head) and any(c in head for c in "wax+"):
                    return True
        return False

    @staticmethod
    def _mode_writes(arg: ast.expr | None) -> bool:
        """mode 实参是否可能写：字面量含 `w` / `a` / `x` / `+`；不是字面量（判不了）按「可能写」算。"""
        if arg is None:
            return False
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return any(c in arg.value for c in "wax+")
        return True

    def _write_open(self, node: ast.Call, q: str | None) -> bool:
        func = node.func
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
            k.arg is None for k in node.keywords
        ):
            return (
                (isinstance(func, ast.Attribute) and func.attr == "open")
                or (isinstance(func, ast.Name) and func.id == "open")
                or q in _BUILTIN_OPEN
                or q == "os.open"
            )
        kw = {k.arg: k.value for k in node.keywords}
        if q == "os.open":
            flags = node.args[1] if len(node.args) > 1 else kw.get("flags")
            return not (isinstance(flags, ast.Attribute) and flags.attr == "O_RDONLY")
        if q in _BUILTIN_OPEN or (isinstance(func, ast.Name) and func.id == "open"):
            return self._mode_writes(node.args[1] if len(node.args) > 1 else kw.get("mode"))
        if isinstance(func, ast.Attribute) and func.attr == "open":
            if q is not None and q.startswith(self._READONLY_OPEN_ROOTS):
                return False  # Image.open 只读
            if "mode" in kw:
                return self._mode_writes(kw["mode"])
            if node.args:  # Path.open(mode) / obj.open(path)：首个实参是 mode 样的字面量才按 mode 判，路径样字面量是只读
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    return bool(self._MODE_LIKE.fullmatch(first.value)) and self._mode_writes(first)
                return True
        return False

    def _guarded_call(self, node: ast.Call) -> bool:
        func = node.func
        names = {self.qual(func)}
        parts: list[str] = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            names.add(".".join([cur.id, *reversed(parts)]))  # 头名被重绑（qual 不认）也按字面名算
        if names & self._GUARDED_QUALIFIED:
            return True
        # 写入：safe 模式落到沙箱、native 落到真实目录，这个差异本身就是伤害面（ADR 0133「伤害闸门」）；savefig 不算（它是证据）
        q = self.qual(func)
        if names & {"os.mkdir", "os.makedirs"} or any(
            n.startswith("shutil.copy") for n in names if n
        ):
            return True
        if isinstance(func, ast.Attribute) and func.attr in ("write_bytes", "mkdir", "touch"):
            return True
        if self._write_open(node, q):
            return True
        # 已知写出器（与事件收集里 `_NUMPY_WRITE` / `_DATA_WRITE_TAILS` 同一份集合，外加常见科学 / 数据库写出口），按方法名或
        # 限定名末段认，不看接收者是谁——宁宽（Codex #1034 r4283254482）
        last = (q or "").rsplit(".", 1)[-1] if q else None
        tails = {func.attr} if isinstance(func, ast.Attribute) else set()
        if last:
            tails.add(last)
        if tails & self._WRITER_TAILS:
            return True
        # 日志写文件（Codex #1034 r4286453690）：`logging.basicConfig(filename=…)`、`*FileHandler(…)`、按配置建处理器的 `fileConfig` /
        # `dictConfig`——safe 里落沙箱、native 里截断真实文件
        if any(t.endswith("FileHandler") or t in ("fileConfig", "dictConfig") for t in tails):
            return True
        # 写出器对象（Codex #1034 r4286515577）：名字里带 writer 的构造 / 工厂（`pd.ExcelWriter`、`imageio.get_writer`、
        # `cv2.VideoWriter`、`csv.writer`……）与建档即落盘的容器（`pd.HDFStore`、`PdfPages`）——按形态认，不逐个列名
        if (node.args or node.keywords) and any(
            "writer" in t.lower() or t in ("HDFStore", "PdfPages") for t in tails
        ):
            return True
        if any(k.arg in ("filename", "filemode") for k in node.keywords) and (
            (q or "").startswith("logging") or "basicConfig" in tails
        ):
            return True
        if (node.args or node.keywords) and any(
            (t.startswith("to_") and t not in self._CONVERT_TAILS)
            or t.startswith("write_")
            or t == "writeto"
            for t in tails
        ):
            return True
        if names & self._WRITER_QUALIFIED or any(
            n.startswith(self._WRITER_PREFIXES) for n in names if n
        ):
            return True
        if self._mode_arg_writes(node, func):
            return True
        # `Path.rename(target)`：恰一个位置实参、无关键字；`df.rename(columns=…)` 不算
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "rename"
            and len(node.args) == 1
            and not node.keywords
        ):
            return True
        if isinstance(func, ast.Attribute):
            if func.attr in self._GUARDED_TAILS:
                return True
            # `Path.replace(target)`：恰一个实参；`str.replace(a, b)` 至少两个
            if (
                func.attr == "replace"
                and "os.replace" in self._GUARDED_QUALIFIED
                and len(node.args) + len(node.keywords) == 1
            ):
                return True
        return False

    def has_cwd_changes(self) -> bool:
        """宁宽：任何名为 `chdir` / `fchdir` 的调用（方法名、限定名末段或裸名）都算改了当前目录（Codex #1034 r4283860478）。"""
        for n in ast.walk(self.tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = (
                f.attr
                if isinstance(f, ast.Attribute)
                else f.id
                if isinstance(f, ast.Name)
                else None
            )
            q = self.qual(f) or ""
            if name in ("chdir", "fchdir") or q.rsplit(".", 1)[-1] in ("chdir", "fchdir"):
                return True
        return False

    #: 动态取属性 / 方法的内建与 operator 入口：名字实参必须是字面字符串才算证明得了
    _ATTR_DISPATCH = frozenset({"getattr", "setattr", "delattr", "methodcaller", "attrgetter"})
    #: 只当裸名（或 `builtins.X`）认：`re.compile` / `df.eval` 是另一回事
    _CODE_EXEC = frozenset({"exec", "eval", "compile", "__import__"})
    #: 按名字取模块 / 跑文件 / 解析点号名：属性末段或 `from … import` 的名字
    _MODULE_LOADERS = frozenset(
        {
            "import_module",
            "__import__",
            "run_path",
            "run_module",
            "resolve_name",
            "spec_from_file_location",
            "exec_module",
            "load_module",
        }
    )
    #: 能按字符串名取到任何全局 / 模块成员的反射面
    _REFLECTION_ATTRS = frozenset(
        {
            "__dict__",
            "__builtins__",
            "__globals__",
            "__getattribute__",
            "__subclasses__",
            "f_globals",
            "f_locals",
            "f_builtins",
        }
    )
    #: 按字符串名枚举 / 取成员的标准库入口（`inspect.getmembers`、`pydoc.locate`、`gc.get_objects`…）
    _MEMBER_LOOKUP = frozenset(
        {
            "getmembers",
            "getmembers_static",
            "getattr_static",
            "locate",
            "get_objects",
            "get_referrers",
        }
    )

    @staticmethod
    def _literal_str(node: ast.AST | None) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def _dispatch_names_provable(self, name: str, call: ast.Call) -> bool:
        """属性分派入口的**全部**名字实参都是字面字符串（`*args` / 关键字传名 / 缺实参都算证明不了）。"""
        if name == "methodcaller":
            # 选名的只有第 1 个位置实参；其后的位置 / 关键字实参属于最终方法调用，不参与（`methodcaller("set_title", "X", loc="left")`）
            return bool(call.args) and self._literal_str(call.args[0])
        if call.keywords or any(isinstance(a, ast.Starred) for a in call.args):
            return False
        if name in ("getattr", "setattr", "delattr"):
            return len(call.args) >= 2 and self._literal_str(call.args[1])
        return bool(call.args) and all(self._literal_str(a) for a in call.args)  # attrgetter

    def has_dynamic_dispatch(self) -> bool:
        """静态分析**证明不了调用目标**的形状（一条结构规则，不枚举危险函数名）：

        * `getattr` / `setattr` / `delattr` / `methodcaller` / `attrgetter` 的名字实参不全是字面字符串（含 `"re" + "move"`、变量、
          `*args`、关键字传名），或这些入口被当值传走（含 `functools.partial(getattr, os)`）；
        * `exec` / `eval` / `compile` / `__import__`（裸名或 `builtins.X`），`importlib.import_module` 等按名取模块 / 跑文件的入口；
        * `globals()` / `locals()` / `vars(x)`（任何形式，`vars(x)` 即 `x.__dict__`）、`inspect.getmembers` 一类按名枚举入口、`X.__dict__` 等反射面、`__builtins__`、`sys.modules`；
        * **从任何模块 import** 上述原语（不看别名）：导入原语本身就让之后的调用点只露别名，宁宽。

        命中即「没看到守卫操作」不再等于「没有守卫操作」。字面属性名（`getattr(obj, "foo")`）不算。"""
        primitives = (
            self._ATTR_DISPATCH
            | self._CODE_EXEC
            | self._MODULE_LOADERS
            | self._MEMBER_LOOKUP
            | {"globals", "locals", "vars", "modules", "__builtins__"}
        )
        called = {id(n.func) for n in ast.walk(self.tree) if isinstance(n, ast.Call)}
        for n in ast.walk(self.tree):
            if isinstance(n, ast.ImportFrom):
                if any(a.name in primitives for a in n.names):
                    return True
                if n.module in ("importlib", "runpy", "pkgutil") and any(
                    a.name == "*" for a in n.names
                ):
                    return True
            elif isinstance(n, ast.Attribute):
                if (
                    n.attr in self._MODULE_LOADERS
                    or n.attr in self._MEMBER_LOOKUP
                    or n.attr in self._REFLECTION_ATTRS
                ):
                    return True
                q = self.qual(n) or ""
                if q == "sys.modules" or (
                    q.startswith("builtins.") and q.rsplit(".", 1)[1] in primitives
                ):
                    return True
                if n.attr in self._ATTR_DISPATCH and id(n) not in called:
                    return True  # `operator.attrgetter` 被当值传走
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                if (
                    n.id in self._CODE_EXEC
                    or n.id in self._MODULE_LOADERS
                    or n.id in self._MEMBER_LOOKUP
                    or n.id in ("globals", "locals", "vars", "__builtins__")
                ):
                    return True
                if n.id in self._ATTR_DISPATCH and id(n) not in called:
                    return True
            elif isinstance(n, ast.Call):
                f = n.func
                name = (
                    f.id
                    if isinstance(f, ast.Name)
                    else f.attr
                    if isinstance(f, ast.Attribute)
                    else None
                )
                if name in self._ATTR_DISPATCH and not self._dispatch_names_provable(name, n):
                    return True
        return False

    def has_guarded_ops(self) -> bool:
        if any(isinstance(n, ast.Call) and self._guarded_call(n) for n in ast.walk(self.tree)):
            return True
        return self._writer_escapes()

    def _writer_tail(self, attr: str) -> bool:
        return (
            "writer" in attr.lower()
            or attr in ("HDFStore", "PdfPages")
            or attr.endswith("FileHandler")
            or attr in self._WRITER_TAILS
            or attr in self._GUARDED_TAILS
            or (attr.startswith("to_") and attr not in self._CONVERT_TAILS)
            or attr.startswith("write_")
            or attr == "writeto"
        )

    def _writer_escapes(self) -> bool:
        """已知写出器 / 守卫操作**不经直接调用**地出现：绑定成别名（`w = Path("v").write_text; w("x")`、`rm = os.remove`、
        `from os import remove` 之后把 `remove` 传走）或按字面名取属性（`getattr(p, "write_text")`）。调用点看不出是谁，
        所以只要它在脚本里被引用就宁宽：按含守卫操作处理（Codex #1034 r4286291290）。"""
        called = {id(n.func) for n in ast.walk(self.tree) if isinstance(n, ast.Call)}
        qualified = self._GUARDED_QUALIFIED | self._WRITER_QUALIFIED
        for n in ast.walk(self.tree):
            if (
                isinstance(n, ast.Call)
                and (
                    n.func.id
                    if isinstance(n.func, ast.Name)
                    else n.func.attr
                    if isinstance(n.func, ast.Attribute)
                    else None
                )
                in ("getattr", "methodcaller", "attrgetter")
                and any(
                    isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and (
                        self._writer_tail(a.value)
                        or a.value in self._GENERIC_TAILS
                        or a.value == "open"
                    )
                    for a in n.args
                )
            ):
                return True
            if id(n) in called:
                continue
            if isinstance(n, ast.Attribute):
                q = self.qual(n) or ""
                # 不经直接调用的 `.open`（`o = Path("v").open; o("w")`）：调用点的 mode 看不到，宁宽（Codex #1034 r4286575586）；
                # 只读的 `PIL.Image.open` 除外
                if n.attr == "open" and not q.startswith(self._READONLY_OPEN_ROOTS):
                    return True
                if (
                    self._writer_tail(n.attr)
                    or q in qualified
                    or q.startswith(self._WRITER_PREFIXES)
                ):
                    return True
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                q = self.qual(n) or ""
                if q and (
                    q in qualified
                    or q.startswith(self._WRITER_PREFIXES)
                    or ("." in q and self._writer_tail(q.rsplit(".", 1)[1]))
                ):
                    return True
        return False

    # ---- 第二遍：事件
    def collect(self) -> None:
        self._walk(self.tree, self.module, ())

    #: 子节点是否仍是「无条件执行」：这些节点的子树按条件 / 循环 / 异常 / 表达式级分支处理，一律不算
    _CONDITIONAL_EXPR = (
        ast.Lambda,
        ast.IfExp,
        ast.BoolOp,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    def _name_rebound(self) -> bool:
        """模块任何位置对 `__name__` 有绑定（赋值 / augassign / del / walrus / for·with·except 目标 / 形参 / def·class /
        import as / global·nonlocal / match 捕获），或碰了 `globals()`（可写 `__name__`）：宁宽。"""
        cached = getattr(self, "_name_rebound_cache", None)
        if cached is not None:
            return cached
        hit = False
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Name) and (
                (n.id == "__name__" and not isinstance(n.ctx, ast.Load))
                or n.id in ("globals", "vars")
            ):
                hit = True
            elif isinstance(n, (ast.Global, ast.Nonlocal)) and "__name__" in n.names:
                hit = True
            elif isinstance(n, ast.arg) and n.arg == "__name__":
                hit = True
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                n.name == "__name__"
            ):
                hit = True
            elif isinstance(n, ast.ExceptHandler) and n.name == "__name__":
                hit = True
            elif isinstance(n, ast.alias) and (n.asname or n.name.split(".")[0]) == "__name__":
                hit = True
            elif (
                isinstance(n, (ast.MatchAs, ast.MatchStar))
                and getattr(n, "name", None) == "__name__"
            ):
                hit = True
            elif isinstance(n, ast.MatchMapping) and n.rest == "__name__":
                hit = True
            if hit:
                break
        self._name_rebound_cache = hit
        return hit

    def _main_guard(self, test: ast.expr) -> bool:
        """`if __name__ == "__main__":`——作为脚本运行时恒真，它的 body 按无条件处理（几乎所有脚本都这么写入口）。
        前提是 `__name__` 在模块里从没被绑定过（改写之后守卫体可能永远走不到，不再给例外）。"""
        if self._name_rebound():
            return False
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        )

    def _walk(self, node: ast.AST, scope: _Scope, branches: tuple, top: bool = True) -> None:
        """`top`：这个节点是否在「会无条件执行」的位置（模块 / 函数体的顶层语句序列；不在任何条件分支、循环、try、with 体、
        lambda / 推导式 / 三元里）。只有 `top` 的事件才够格升 high（ADR 0133「保守边界」）。"""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = self.scopes.get(id(node), scope)
            for dec in node.decorator_list:
                self._walk(dec, scope, branches, top)
            # 函数体是它自己的语句序列（能不能走到由可达性判）；类体在定义时执行，跟随外层
            body_top = True if not isinstance(node, ast.ClassDef) else top
            self._walk_seq(node.body, inner, branches, body_top)
            return
        if isinstance(node, (ast.If, ast.While)) and self._constant_false(node.test):
            # 条件是常量 False 的 `if` / `while`：body 永远不执行，里面的调用与证据都不算（`else` 照常）
            self._walk(node.test, scope, branches, top)
            for stmt in node.orelse:
                self._walk(stmt, scope, (*branches, (id(node), 1)), top)
            return
        if isinstance(node, ast.If):
            self._walk(node.test, scope, branches, top)
            body_top = top and self._main_guard(node.test)
            self._walk_seq(node.body, scope, (*branches, (id(node), 0)), body_top)
            for stmt in node.orelse:
                self._walk(stmt, scope, (*branches, (id(node), 1)), False)
            return
        if isinstance(node, ast.Match):
            self._walk(node.subject, scope, branches, top)
            for i, case in enumerate(node.cases):
                for stmt in case.body:
                    self._walk(stmt, scope, (*branches, (id(node), i)), False)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._walk(node.iter, scope, branches, top)
            for stmt in (*node.body, *node.orelse):
                self._walk(stmt, scope, branches, False)
            return
        if isinstance(node, ast.While):
            self._walk(node.test, scope, branches, top)
            for stmt in (*node.body, *node.orelse):
                self._walk(stmt, scope, branches, False)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:  # `with open(p, "w") as f:` 的 open 在进入 body 之前无条件执行
                self._walk(item.context_expr, scope, branches, top)
            for stmt in node.body:
                self._walk(stmt, scope, branches, False)
            return
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            for stmt in (*node.body, *node.orelse, *node.finalbody):
                self._walk(stmt, scope, branches, False)
            for handler in node.handlers:
                for stmt in handler.body:
                    self._walk(stmt, scope, branches, False)
            return
        if isinstance(node, ast.Module):
            self._walk_seq(node.body, scope, branches, top)
            return
        if isinstance(node, ast.Call):
            if top and isinstance(node.func, ast.Name):
                self.calls.append((scope, node.func.id))
            self._top = top
            self._call(node, scope, branches)
        child_top = top and not isinstance(node, self._CONDITIONAL_EXPR)
        for child in ast.iter_child_nodes(node):
            self._walk(child, scope, branches, child_top)

    def _walk_seq(self, stmts: list, scope: _Scope, branches: tuple, top: bool) -> bool:
        """一段语句序列。遇到无条件终止（`return` / `raise` / `sys.exit()` 一类 / 无 `break` 的 `while True`）后，
        同一序列里后面的语句永远走不到：照常走一遍（保留 medium 的线索），但不再算「无条件执行」，不升 high。
        返回这段序列是否无条件终止（`__main__` 守卫的 body 终止，外层后面的语句同样走不到）。"""
        dead = False
        for stmt in stmts:
            self._walk(stmt, scope, branches, top and not dead)
            if not dead and self._terminates(stmt, scope):
                dead = True
        return dead

    def _terminates(
        self, stmt: ast.stmt, scope: _Scope, ret: bool = True, seen: frozenset = frozenset()
    ) -> bool:
        """这条语句执行后，同一序列里后面的语句是否一定走不到。`ret=False`：`return` 不算（函数体里的无条件 return
        只是回到调用处，调用处后面还会执行）。拿不准一律按「终止」处理——后面的语句因此不出证据（保守方向）。"""
        if isinstance(stmt, ast.Raise):
            return True
        if isinstance(stmt, (ast.Return, ast.Continue, ast.Break)):
            return ret
        if isinstance(stmt, ast.Assert):
            return isinstance(stmt.test, ast.Constant) and not stmt.test.value
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            func = stmt.value.func
            if self.qual(func) in ("sys.exit", "os._exit", "os.abort"):
                return True
            if any(self._is_exit_builtin(func, n, scope) for n in ("exit", "quit")):
                return True
            return self._call_always_raises(func, scope, seen)
        if isinstance(stmt, ast.If):
            const_true = isinstance(stmt.test, ast.Constant) and bool(stmt.test.value)
            if const_true or self._main_guard(stmt.test):
                return any(self._terminates(s, scope, ret, seen) for s in stmt.body)
            return False
        if isinstance(stmt, ast.While):
            t = stmt.test
            if isinstance(t, ast.Constant) and t.value and not stmt.orelse:
                return not any(isinstance(n, ast.Break) for b in stmt.body for n in ast.walk(b))
            return False
        if isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try), ast.With, ast.AsyncWith)):
            # try / with 里的终止可能被 except / 上下文管理器吞掉，证明不了：块内出现任何无条件终止，后面都不算
            inner = [*stmt.body]
            if not isinstance(stmt, (ast.With, ast.AsyncWith)):
                inner += [*stmt.orelse, *stmt.finalbody]
                for h in stmt.handlers:
                    inner += h.body
            return any(self._terminates(s, scope, ret, seen) for s in inner)
        if isinstance(stmt, ast.ClassDef):  # 类体在定义时执行
            inner_scope = self.scopes.get(id(stmt), scope)
            return any(self._terminates(s, inner_scope, False, seen) for s in stmt.body)
        return False

    @staticmethod
    def _runs_on_call(node: ast.AST) -> bool:
        """裸调用 `f()` 会立刻执行函数体：普通 `def`（不是 `async def`，体内——不含嵌套 def / lambda / 类——没有 `yield`）。"""
        if not isinstance(node, ast.FunctionDef):
            return not isinstance(node, ast.AsyncFunctionDef)  # 类体等照旧
        stack = list(node.body)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.Yield, ast.YieldFrom)):
                return False
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            stack.extend(ast.iter_child_nodes(n))
        return True

    def _call_always_raises(self, func: ast.AST, scope: _Scope, seen: frozenset) -> bool:
        """`f()`：f 是本模块唯一绑定的 `def`，函数体顶层序列在任何 `return` 之前无条件 raise / exit。"""
        if not isinstance(func, ast.Name) or self.bind_total.get(func.id) != 1:
            return False
        if func.id in self.aliases:
            return False
        s: _Scope | None = scope
        while s is not None:
            if func.id in s.defs:
                target = s.defs[func.id]
                if target is None:
                    return False
                node = target.node
                if (
                    not isinstance(node, ast.FunctionDef)
                    or node.decorator_list
                    or id(node) in seen
                    or not self._runs_on_call(node)
                ):
                    return False
                seen = seen | {id(node)}
                for stmt in node.body:
                    if self._terminates(stmt, target, False, seen):
                        return True
                    if any(isinstance(n, ast.Return) for n in ast.walk(stmt)):
                        return False
                return False
            s = s.parent
        return False

    def _is_exit_builtin(self, func: ast.AST, name: str, scope: _Scope) -> bool:
        return (
            isinstance(func, ast.Name)
            and func.id == name
            and self.bind_total.get(name, 0) == 0
            and (name not in self.aliases)
            and self._is_builtin(func, name, scope)
        )

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
                        # 名字在模块任何地方被重新绑定过（赋值 / 再次 def / 形参 / import 同名）：调用的未必是这个 def，不证明可达
                        if self.bind_total.get(name) != 1 or name in self.aliases:
                            target = None
                        # `async def` / 生成器：裸调用只造出协程 / 生成器对象，不执行函数体；await / 迭代是否发生不推导
                        if target is not None and not self._runs_on_call(target.node):
                            target = None
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

    _top = True

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
                top=self._top,
                end_line=getattr(node, "end_lineno", None) or node.lineno,
                end_col=getattr(node, "end_col_offset", None) or node.col_offset,
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

    def _savefig_name_literal(self, node: ast.Call, path: PathVal | None) -> bool:
        """这次 `savefig` 写出的文件名就是字面目标本身，不依赖运行期的 `rcParams["savefig.format"]`。

        matplotlib：显式 `format=`（非 None）时文件名原样；否则无后缀会补 `.<savefig.format>`（`figcapture.savefig_effective_names`
        同一规则）。静态侧不复刻 rcParams，所以「无后缀且没有字面非 None 的 `format=`」一律不算确定（不升 high）。
        """
        if path is None or len(node.args) > 1:
            return False
        for kw in node.keywords:
            if kw.arg is None:
                return False  # **kwargs：可能带 format
            if kw.arg == "format":
                return isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
        return bool(posixpath.splitext(path.text)[1][1:])

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
                spath = self.value(target, scope)
                self._add(
                    _E_SAVEFIG,
                    "savefig",
                    node,
                    scope,
                    branches,
                    spath,
                    certain=self._savefig_is_matplotlib(func, scope)
                    and self._savefig_name_literal(node, spath),
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


def _collect_bytes(raw: bytes) -> ScriptFacts:
    """按 Python 自己的源码编码规则（PEP 263 声明 / BOM / 默认 UTF-8）解码再分析；解不开 = 看不懂 = 不路由（未解析，走 safe）。
    不用 replacement 字符糊过去：那会把 Latin-1 的 `café` / `cañ` 折成同一个路径，伪造出读回（Codex #1034 r4283569553）。"""
    text = scriptargs.decode_source(raw)
    if text is None:
        return ScriptFacts(parsed=False)
    return _collect(text)


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
        guarded_ops=col.has_guarded_ops(),
        cwd_changes=col.has_cwd_changes(),
        dynamic_dispatch=col.has_dynamic_dispatch(),
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
            raw = Path(real).read_bytes()
        except OSError:
            return _EMPTY
        facts = _collect_bytes(raw)
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
            # Windows 路径（带盘符）大小写不敏感；POSIX 区分大小写
            fold = (lambda t: t.lower()) if ntpath.splitdrive(r)[0] else (lambda t: t)
            if fold(v.text) == fold(r):
                return ("root", ".")
            if fold(v.text).startswith(fold(r.rstrip("/")) + "/"):
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


def _really_inside(w: _Where, rel: str) -> bool:
    """项目根内的相对路径 `rel`，经 `realpath`（只 lstat，不读内容）解析后是否仍在项目根之内。解析不了 = 证明不了 = False。"""
    try:
        real = os.path.normcase(os.path.realpath(os.path.join(w.root or "", rel)))
        roots = {os.path.normcase(os.path.realpath(r)) for r in w.roots_abs}
    except (OSError, ValueError):
        return False
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _exclusive(a: Event, b: Event) -> bool:
    arms = dict(a.branches)
    return any(node in arms and arms[node] != arm for node, arm in b.branches)


def _after(later: Event, earlier: Event) -> bool:
    """`later` 在 `earlier` 之后求值。嵌在 `earlier` 调用表达式里的（它的实参）先于它求值，不算之后（Codex #1034 r4283403948）。"""
    start = (later.line, later.col)
    if start <= (earlier.end_line, earlier.end_col):
        return False
    return start > (earlier.line, earlier.col) and not _exclusive(earlier, later)


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

    # 每个（规则, 规范路径）只出一条证据；配对比较有总预算，超了就不再给高置信（回 safe，Codex #1034 r4286453702）——
    # 生成出来的脚本重复成千上万次同一对 savefig / 读回时，证据与耗时都不随 n² 增长
    seen: set[tuple] = set()
    budget = _PAIR_BUDGET

    for s, sk in keyed:
        if s.kind != _E_SAVEFIG or sk is None:
            continue
        # R1 / R2：同一规范路径、之后出现的读 / 探
        for r, rk in keyed:
            budget -= 1
            if budget < 0:
                return evidence, dynamic, False, False
            if rk != sk or r.kind not in (_E_READ, _E_STAT) or not _after(r, s):
                continue
            same_scope = r.scope == s.scope
            if r.kind == _E_READ:
                if not same_scope:
                    rule = RULE_R1_CROSS_SCOPE
                elif s.certain and s.top and r.top and s.scope in facts.live_scopes:
                    rule = RULE_R1
                    high_r1 = True
                else:
                    # 接收者解析不到 matplotlib（用户对象 / 同名函数 / 参数），或证据在模块没调用到的函数体里：只记，不直达
                    rule = RULE_R1_UNPROVEN
            else:
                rule = RULE_R2
            if (rule, sk) not in seen:
                seen.add((rule, sk))
                ev(rule, KIND_SAVEFIG, "savefig", s.line, r.line)
        # 不同作用域里的读回也在上面按 R1_cross_scope 记了（_after 只看行序）；这里不再重复

    for m, mk in keyed:
        if m.kind != _E_MOVE or mk is None:
            continue
        dk = _canon(m.dest, w)
        # safe 的删除 / 改名守卫先 resolve 再看源是否在真实图库内：词法上在项目里、但经符号链接指到项目外的，safe 照常真执行
        inside = mk[0] == "root" and _really_inside(w, mk[1])
        if not inside:
            continue
        budget -= 2 * len(keyed)
        if budget < 0:
            return evidence, dynamic, False, False
        wrote = any(
            wk == mk
            and x.kind in (_E_WRITE, _E_SAVEFIG)
            and x.certain
            and x.top
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
                    and r.top
                    and r.scope == m.scope
                    and _after(r, m)
                ):
                    read_line = r.line
                    break
        rule = (
            RULE_R3
            if wrote and read_line is not None and m.top and m.scope in facts.live_scopes
            else RULE_R3_PARTIAL
        )
        high_r3 = high_r3 or rule == RULE_R3
        if (rule, mk) not in seen:
            seen.add((rule, mk))
            ev(rule, KIND_RENAME, m.op, m.line, read_line)
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
    if (high_r1 or high_r3) and facts.guarded_ops:
        # 伤害闸门：存在 safe 守卫会拦的操作时，不为「可达性证明」赌——直达 native 会让它们真实执行。
        # 静默回退（ADR 0127）兜住真需要 native 的脚本；证据照实带出
        return Route(
            EXECUTION_SAFE,
            REASON_GUARDED_FILE_OPS,
            CONFIDENCE_MEDIUM,
            tuple(evidence),
            dynamic,
            blind,
        )
    if (high_r1 or high_r3) and facts.dynamic_dispatch:
        # 不可证明门槛：动态分派让「没看到守卫操作」不等于「没有」。证明不了就不提升——不枚举危险名字，
        # 同样走 safe + 静默回退（ADR 0127）。与伤害闸门互补：闸门管看得见的操作形状，这里管看不见的调用目标
        return Route(
            EXECUTION_SAFE,
            REASON_UNPROVABLE_DYNAMIC,
            CONFIDENCE_MEDIUM,
            tuple(evidence),
            dynamic,
            blind,
        )
    if (high_r1 or high_r3) and facts.cwd_changes:
        # 脚本改了当前目录：cwd 相对的「存」与「读」可能不在同一个目录，证明不了是同一个文件——不直达，走 safe + 回退
        return Route(
            EXECUTION_SAFE, REASON_ORDINARY_PLOT, CONFIDENCE_MEDIUM, tuple(evidence), dynamic, blind
        )
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
    source: bytes | None = None,
) -> Route:
    """`facts_of_file` + `decide`，并量总耗时（`Route.elapsed_ms`，本地诊断 / 基准读；不进遥测）。
    用户显式 / 运维关闭 / 沿用上次成功这三条不需要读脚本，直接决定。"""
    started = time.perf_counter()
    needs_facts = policy != POLICY_SAFE_ONLY and operator_enabled and last_success not in EXECUTIONS
    if not needs_facts:
        facts = None
    elif source is not None:
        # 调用方已读好的字节（与计划的 revision 核对同一份，见 preparation.plan_for）：分析它，不再读第二遍
        facts = (
            ScriptFacts(parsed=False, too_large=True)
            if len(source) > MAX_SOURCE_BYTES
            else _collect_bytes(source)
        )
    else:
        facts = facts_of_file(script_path)
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
