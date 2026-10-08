"""脚本 import 的分类（统一实施包 U04，ADR 0061；Import Origin Resolver PR1 起按真实 Python 的导入
优先级判来源）：stdlib / 本地模块 / 第三方 / 未知，外加每一处 import 的**上下文**（无条件 / 条件 /
延后 / 可选 / 仅类型 / 动态）。

联合准备要回答的问题是「这个脚本**开跑就需要**哪些第三方包」，而不是「项目声明了哪些
包」：声明是约束的来源（`depresolve.declared_intents`），需要不需要看脚本自己。两者交在
`depplan` 里。本模块只做静态分类，**不执行脚本、不 import 任何用户模块、不对用户包调
`importlib.util.find_spec`、不起子进程**——只用 `ast` 与文件系统（`scandir` / `lstat`）；判据：

* **built-in / 启动即在 `sys.modules` 里的 stdlib**（`sys` / `builtins` / `_io` / `os` / `io` …）：
  import 系统先查 `sys.modules` 与内建 / 冻结导入器，**先于**文件系统——脚本目录里再有同名文件也
  遮蔽不了它们（`BUILTIN_NAMES` / `FROZEN_NAMES` / `PRELOADED_STDLIB`；目标解释器的内建名单由调用方
  传 `builtin`，没传时只认这三张恒成立的表）；
* **本地模块**（FO-031 / FO19：`lab_utils` 之类永远不装）：按 `sys.path` 的顺序在**搜索根**里找——
  `python script.py` 是 `[脚本目录, 项目根]`（项目根是 safe worker 的图库根；native 下它不在
  `sys.path` 上，仍按本地判但标 `unverified`），`python -m pkg.mod` 是 `[cwd]`（`Entry`）。同一目录
  里的优先级就是 `FileFinder` 的：**目录包（`__init__`）> 扩展模块 > `.py` > 命名空间目录**，名字按
  目录清单**逐字匹配**（`import Utils` 不会匹配 `utils.py`——大小写不敏感的文件系统上也一样，真实
  Python 的大小写检查）。搜索根在 stdlib 目录**之前**，所以本地 `json.py` 遮蔽标准库 `json`
  （`shadowing="stdlib"`）；
* **命名空间目录**（无 `__init__` 的目录）：常规包 / 模块在整条 `sys.path` 上任何位置都压过命名
  空间——所以它不能被判成「唯一提供者」：`bucket=local`（宁可不装）、`resolution_status=ambiguous`；
* **第三方**：不是上面几种、且能经**可信解析**（项目声明 / curated 表，`depresolve.resolve`
  同一优先级）映射到一个 distribution——这里**只是候选**，本模块不看任何已安装环境；
* **未知**：不是 stdlib、不是本地、也映射不到——**永远不装、不猜同名**（FO-034），只报出来
  让用户指定。

上下文决定「要不要在跑之前就准备」：只有**模块层无条件**的 import 会让脚本一开跑就死
（`needed`）；`try:` 里的（可选依赖）、`if TYPE_CHECKING:` 里的（仅类型）、函数 / 类体内的
（延后）、`if` / `for` / `with` 里的（条件）、`importlib.import_module(变量)` /
`__import__(变量)`（动态）都不在跑前装——它们缺了会以 `missing_dependency` 在运行后报出来，
由有界重计划接手（ADR 0061 §四）。**宁可少装不误装**：改用户环境的默认动作只由确定
会执行的那几行触发。

本地模块**有界跟进**（`MAX_LOCAL_MODULES` / `MAX_DEPTH`）：`lab_utils` 里 import 的第三方包
同样是脚本开跑就需要的；跟进到的 import 记 `via`。跟进按真实的加载语义：`import a.b.c` 加载
`a` / `a.b` / `a.b.c` 各自的 `__init__` / 模块；`from pkg import x` 只在 `pkg/x` 真的是子模块（文件 /
目录）时才跟进它，否则 `x` 是 `pkg` 里的属性，不假定；相对导入按**包上下文**解析（跟进到的文件是以
哪个点分名被 import 的，包就是哪个；`python script.py` 的入口没有包，相对导入记一条 warning 而不是
猜）。默认只读：文件读取走 `scanbudget`（单文件 / 总字节上限、占位文件不读、`no_follow` 时符号链接
不下探）。纯标准库；被 `depplan` import。

结论字段（`ImportClass`）在 `bucket` / `context` / `needed` 之外**只加可选字段**，供观测与后续阶段
消费：`origin_kind` / `resolution_status` / `evidence` / `shadowing` / `warnings`——都是闭集代码或
项目相对路径，不带绝对路径、不带文件内容。
"""

from __future__ import annotations

import ast
import dataclasses
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import depresolve, execspec, figcapture, projectenv, scanbudget

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

# ---- 来源（Import Origin Resolver；只加字段，bucket 语义不变） ---------------------------------
ORIGIN_BUILTIN = "built-in"
ORIGIN_FROZEN = "frozen"
ORIGIN_STDLIB = "stdlib"
ORIGIN_LOCAL = "project-local"
ORIGIN_NAMESPACE = "namespace"
ORIGIN_EXTENSION = "extension"
#: 本阶段没有任何「已安装」证据：第三方候选与未知都落这一档，靠 `resolution_status` 区分。
ORIGIN_UNRESOLVED = "unresolved"
ORIGIN_KINDS = (
    ORIGIN_BUILTIN,
    ORIGIN_FROZEN,
    ORIGIN_STDLIB,
    ORIGIN_LOCAL,
    ORIGIN_NAMESPACE,
    ORIGIN_EXTENSION,
    ORIGIN_UNRESOLVED,
)

STATUS_RESOLVED = "resolved"  # 来源有静态证据、且只有一个答案
STATUS_AMBIGUOUS = "ambiguous"  # 有不止一个合理答案（命名空间目录、目标解释器的内建名单不明）
STATUS_UNVERIFIED = "unverified"  # 有候选，但缺少能确认它的证据（映射表候选、搜索根没确认）
STATUS_UNRESOLVED = "unresolved"  # 找不到任何提供者
STATUS_UNSUPPORTED = "unsupported"  # 认得出但不做（保留位：PR2 起用于 editable 等）
STATUSES = (
    STATUS_RESOLVED,
    STATUS_AMBIGUOUS,
    STATUS_UNVERIFIED,
    STATUS_UNRESOLVED,
    STATUS_UNSUPPORTED,
)

#: `ImportClass.evidence` 的闭集（代码，不是自由文本；不带路径）。
EVIDENCE_CODES = frozenset(
    {
        "builtin_module",
        "frozen_module",
        "preloaded_stdlib",
        "stdlib_name_table",
        "sys_path_script_dir",
        "sys_path_project_root",
        "sys_path_cwd",
        "package_with_init",
        "module_file",
        "extension_file",
        "namespace_dir",
        "exact_name_match",
        "case_mismatch_rejected",
        "mapped_by_project_declared",
        "mapped_by_curated",
        "no_provider_found",
        "sibling_dir_of_importer",
        "link_not_followed",
        "link_outside_project",
    }
)
#: `ImportClass.warnings` / `ScanResult.warnings[].code` 的闭集。
WARNING_CODES = frozenset(
    {
        "local_file_never_imported",
        "may_shadow_builtin",
        "namespace_may_be_overridden",
        "local_namespace_dir_ignored",
        "name_differs_in_case_from_file",
        "path_not_confirmed",
        "implicit_sibling_import",
        "link_not_followed",
        "link_outside_project",
        "relative_import_no_package",
        "relative_import_beyond_top",
        "sys_path_modified",
        "module_not_found",
    }
)

#: 本地模块跟进的上限：文件数与深度。科研项目的本地模块通常两三个；上限只挡住误把整个
#: 源码树扫一遍。
MAX_LOCAL_MODULES = 24
MAX_DEPTH = 3
MAX_SOURCE_BYTES = 1024 * 1024
#: 「脚本的别名会不会被本地模块借走」那一遍（`figcapture.reaches_main`）最多再扫多少个包内文件；
#: 超了按看不全处理（`unused` 作废）。它只为这一个判断扫，不改 needed 的集合。
MAX_MAIN_SCAN_FILES = 256

#: 标准库名字表：宿主的那份是默认；目标解释器的由调用方传入。
HOST_STDLIB: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "__future__",
    "__main__",
    "builtins",
}

#: 每个 CPython 构建上都是内建的模块（导入系统自举要用，`sys.builtin_module_names` 的恒成立子集）。
BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "sys",
        "builtins",
        "_imp",
        "_thread",
        "_warnings",
        "_weakref",
        "_io",
        "marshal",
        "posix",
        "nt",
        "time",
        "_codecs",
        "_abc",
        "_signal",
        "_stat",
    }
)
#: 导入系统自己的冻结模块。
FROZEN_NAMES: frozenset[str] = frozenset(
    {"_frozen_importlib", "_frozen_importlib_external", "zipimport"}
)
#: 解释器启动（`site`）时已经在 `sys.modules` 里的标准库模块：后来的 `import` 直接命中缓存，脚本目录的
#: 同名文件到不了它们。仅 `-S` / `-I -S` 启动时 `site` 链上的几个不在——Tavotto 的 worker 与用户的
#: 解释器都带 `site`。
PRELOADED_STDLIB: frozenset[str] = frozenset(
    {
        "codecs",
        "encodings",
        "abc",
        "io",
        "os",
        "stat",
        "site",
        "posixpath",
        "genericpath",
        "_collections_abc",
        "_sitebuiltins",
    }
)
#: 宿主上是内建、但不在上面恒成立表里的名字（`itertools` / `gc` / `pwd` …）：别的构建上它们可能是
#: 文件，所以没拿到目标解释器的内建名单时，本地同名文件对它们的遮蔽是 `ambiguous`。
HOST_BUILTIN_EXTRA: frozenset[str] = (
    frozenset(sys.builtin_module_names) - BUILTIN_NAMES - FROZEN_NAMES - PRELOADED_STDLIB
)

#: 编译扩展的文件名：`name` + 可选的平台标签 + `.so` / `.pyd`（CPython 的 `EXTENSION_SUFFIXES`：
#: `.cpython-313-darwin.so`、`.abi3.so`、`.cp313-win_amd64.pyd`、`.so`、`.pyd`；`.dylib` / `.dll` 不是
#: 可 import 的扩展后缀）。目标解释器的 ABI 标签不在这里核——静态扫描不知道目标版本。
_EXT_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\.(?:cpython-[0-9a-z]+-[0-9a-z_\-]+|cp[0-9a-z]+-[0-9a-z_]+|abi3|pypy[0-9a-z]+-[0-9a-z_]+))?"
    r"\.(?:so|pyd)$"
)

#: `sys.path` 的就地修改方法（源码里出现就说明搜索路径不是静态可定的）。
_PATH_MUTATORS = frozenset(
    {"insert", "append", "extend", "remove", "pop", "clear", "reverse", "sort", "__setitem__"}
)


@dataclasses.dataclass(frozen=True)
class ImportUse:
    """一处 import：顶级名、写的全名、上下文、行号、经由哪个本地模块（空 = 脚本本身）。

    `names` 是 `from X import a, b` 的名字（`a` 可能是 `X` 的子模块也可能是属性——是哪个由磁盘定，
    不假定）；不进 `to_payload`（协议形状不变）。"""

    module: str
    full: str
    context: str
    lineno: int
    via: str = ""
    names: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        return {
            "module": self.module,
            "full": self.full,
            "context": self.context,
            "lineno": self.lineno,
            "via": self.via,
        }


@dataclasses.dataclass(frozen=True)
class RelativeImport:
    """一条相对导入（`from . import x` / `from ..m import y`）：层数、点后的模块、名字、上下文、行号。"""

    level: int
    module: str
    names: tuple[str, ...]
    context: str
    lineno: int


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
    #: 脚本 import 了它、绑定的名字却从未被读（`figcapture.unused_imports`，只看脚本本身、
    #: 本地模块没有经由它）——跑前不准备；缺的话 worker 给那一行占位（ADR 0061 §二修订）。
    unused: bool = False
    # ---- Import Origin Resolver（可选字段，只观测；见模块文档） ----
    origin_kind: str = ""
    resolution_status: str = ""
    evidence: tuple[str, ...] = ()
    #: 本地文件压过了什么：`stdlib`（本地 `json.py` 遮蔽标准库）/ `third_party`（本地 `numpy.py` 压过映射得到
    #: 的第三方）/ 空。
    shadowing: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def needed(self) -> bool:
        """脚本一开跑就要它、且它是第三方（不管映射得到没有），而且脚本确实用到了它。"""
        return (
            self.bucket == BUCKET_THIRD_PARTY
            and self.context == CONTEXT_UNCONDITIONAL
            and not self.unused
        )

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
            "unused": self.unused,
            "needed": self.needed,
            "origin_kind": self.origin_kind,
            "resolution_status": self.resolution_status,
            "evidence": list(self.evidence),
            "shadowing": self.shadowing,
            "warnings": list(self.warnings),
        }


@dataclasses.dataclass(frozen=True)
class ScanResult:
    """一次扫描的全部结论：分类表 + 逐处 import + 动态 import 的位置 + 读不了的本地模块。"""

    classes: tuple[ImportClass, ...]
    uses: tuple[ImportUse, ...]
    dynamic: tuple[ImportUse, ...]
    problems: tuple[dict, ...]
    truncated: bool = False  # 本地模块跟进碰到上限
    #: 这次扫描读过的文件（脚本 + 跟进过的本地模块），相对项目根的 POSIX 路径、排好序——
    #: 计划的输入指纹按它们的字节算（`depplan.inputs_digest`）。
    files: tuple[str, ...] = ()
    #: 用的搜索根（项目相对路径 + 标签 + 是否确认在 `sys.path` 上）。
    search_roots: tuple[dict, ...] = ()
    #: 搜索路径能不能确认完整：根都确认、源码里没有改 `sys.path`、预算没停、没有读不动的目录、没有被
    #: 链接挡住的命中。`False` = 下面的结论里「找不到」不代表真的没有。
    search_complete: bool = True
    #: 扫描级的 warning：`{code[, path][, line]}`，code 在 `WARNING_CODES` 里，路径项目相对。
    warnings: tuple[dict, ...] = ()
    #: `scanbudget` 账本（项目相对路径；占位文件 / 超大 / 预算用完 / 链接没下探……）。
    issues: tuple[dict, ...] = ()

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
            "search_roots": [dict(r) for r in self.search_roots],
            "search_complete": self.search_complete,
            "warnings": [dict(w) for w in self.warnings],
            "issues": [dict(i) for i in self.issues],
        }


# ---------------------------------------------------------------- 执行入口（sys.path[0] 由它决定）


@dataclasses.dataclass(frozen=True)
class Entry:
    """脚本怎么被跑（决定 `sys.path` 的前几项）。默认 = safe worker 跑一个脚本（历史行为）。

    * 脚本（`python script.py` / safe worker）：`sys.path[0]` 是**脚本目录**；safe worker 另把项目根
      （图库根）放进 `sys.path`，native 下项目根不在上面；
    * 模块（`python -m pkg.mod`）：`sys.path[0]` 是 **cwd**（不是脚本目录），`pkg` 的 `__init__` 先于
      `mod` 执行，`mod` 的 `__package__` 是 `pkg`。cwd 由调用方给（`cwd` 是项目相对目录，空 = 项目根）；
      `cwd_mode=sandbox`（safe worker 的沙盒 cwd）时项目根不在 `sys.path[0]` 上，搜索根标「未确认」。
    """

    kind: str = execspec.TARGET_SCRIPT
    module: str = ""  # kind=module：点分模块名
    profile: str = figcapture.PROFILE_SAFE
    cwd_mode: str = execspec.CWD_SANDBOX
    cwd: str = ""  # kind=module：项目相对 cwd；空 = 项目根

    def __post_init__(self) -> None:
        if self.kind not in execspec.TARGET_KINDS:
            raise ValueError(f"entry.kind 非法: {self.kind!r}")
        if self.profile not in execspec.PROFILES:
            raise ValueError(f"entry.profile 非法: {self.profile!r}")
        if self.cwd_mode not in execspec.CWD_MODES:
            raise ValueError(f"entry.cwd_mode 非法: {self.cwd_mode!r}")
        if self.kind == execspec.TARGET_MODULE and not all(
            depresolve.valid_import_name(p) for p in self.module.split(".")
        ):
            raise ValueError(f"entry.module 不是合法的点分模块名: {self.module!r}")


# ---------------------------------------------------------------- AST


@dataclasses.dataclass
class _Aliases:
    """文件里 `importlib` / `sys` 被绑到了哪些名字（先整份扫一遍，再按名字认调用）。"""

    importlib_mods: set[str]  # 绑定到 importlib 模块的名字（含恒认的 `importlib`）
    import_funcs: set[str]  # 绑定到 import_module / __import__ 的名字（含恒认的两个原名）
    builtins_mods: set[str]  # 绑定到 builtins 的名字
    sys_mods: set[str]
    path_names: set[str]  # `from sys import path [as p]`


def _collect_aliases(tree: ast.AST) -> _Aliases:
    al = _Aliases({"importlib"}, {"import_module", "__import__"}, set(), {"sys"}, set())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname and a.name == "importlib":
                    al.importlib_mods.add(a.asname)
                elif a.asname and a.name == "builtins":
                    al.builtins_mods.add(a.asname)
                elif a.asname and a.name == "sys":
                    al.sys_mods.add(a.asname)
                elif not a.asname and a.name == "builtins":
                    al.builtins_mods.add("builtins")
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for a in node.names:
                bound = a.asname or a.name
                if node.module == "importlib" and a.name in ("import_module", "__import__"):
                    al.import_funcs.add(bound)
                elif node.module == "sys" and a.name == "path":
                    al.path_names.add(bound)
    return al


def _is_sys_path(node: ast.AST, al: _Aliases) -> bool:
    if isinstance(node, ast.Name):
        return node.id in al.path_names
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id in al.sys_mods
    )


class _Visitor(ast.NodeVisitor):
    """收集一份模块里的 import 及其上下文。"""

    def __init__(self, via: str, aliases: _Aliases) -> None:
        self.via = via
        self.al = aliases
        self.uses: list[ImportUse] = []
        self.dynamic: list[ImportUse] = []
        self.relatives: list[RelativeImport] = []
        self.path_mutations: list[int] = []  # 改 sys.path 的行号
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
        names = tuple(a.name for a in node.names)
        if node.level:  # 相对 import：包内的事，不是依赖——按包上下文解析、只为跟进
            self.relatives.append(
                RelativeImport(node.level, node.module or "", names, self._context(), node.lineno)
            )
            return
        mod = node.module or ""
        if not mod:
            return
        self.uses.append(
            ImportUse(mod.split(".", 1)[0], mod, self._context(), node.lineno, self.via, names)
        )

    def visit_Call(self, node):
        target = _dynamic_import_target(node, self.al)
        if target is not None:
            literal, arg, canonical = target
            if literal is not None:
                # 恒认的原形（`importlib.import_module("x")` / `import_module("x")` / `__import__("x")`）按所在
                # 上下文；别名形（`il.import_module("x")` / `im("x")`）名字可能被改绑，只记候选（dynamic）——
                # 不提为无条件依赖
                ctx = self._context() if canonical else CONTEXT_DYNAMIC
                self.uses.append(
                    ImportUse(literal.split(".", 1)[0], literal, ctx, node.lineno, self.via)
                )
            else:
                self.dynamic.append(ImportUse("", arg, CONTEXT_DYNAMIC, node.lineno, self.via))
        fn = node.func
        if isinstance(fn, ast.Attribute) and (
            (fn.attr in _PATH_MUTATORS and _is_sys_path(fn.value, self.al))
            or (
                fn.attr == "addsitedir" and isinstance(fn.value, ast.Name) and fn.value.id == "site"
            )
        ):
            self.path_mutations.append(node.lineno)
        self.generic_visit(node)

    # ---- sys.path 被整体改写 / 就地改写 ----
    def _mutates_path(self, target: ast.AST) -> bool:
        if isinstance(target, ast.Subscript):
            return _is_sys_path(target.value, self.al)
        return _is_sys_path(target, self.al) and isinstance(target, ast.Attribute)

    def visit_Assign(self, node):
        if any(self._mutates_path(t) for t in node.targets):
            self.path_mutations.append(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if self._mutates_path(node.target) or _is_sys_path(node.target, self.al):
            self.path_mutations.append(node.lineno)
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


def _dynamic_import_target(node: ast.Call, al: _Aliases) -> tuple[str | None, str, bool] | None:
    """动态 import 调用 → `(字面量或 None, 参数原文, 是否恒认的原形)`；不是就 None。

    恒认的原形：`importlib.import_module(x)` / 裸 `import_module(x)` / 裸 `__import__(x)`（历史口径，
    字面量按所在上下文算）。别名形：`import importlib as il; il.import_module(x)`、
    `from importlib import import_module as im; im(x)`、`import builtins as b; b.__import__(x)`——名字可能被
    改绑，字面量只是候选（调用方记成 dynamic 上下文），非字面量照旧进 `dynamic`。"""
    fn = node.func
    canonical = False
    hit = False
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        base = fn.value.id
        if fn.attr in ("import_module", "__import__") and base in al.importlib_mods:
            hit = True
            canonical = base == "importlib" and fn.attr == "import_module"
        elif fn.attr == "__import__" and base in al.builtins_mods:
            hit = True
    elif isinstance(fn, ast.Name) and fn.id in al.import_funcs:
        hit = True
        canonical = fn.id in ("import_module", "__import__")
    if not hit or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value, arg.value, canonical
    try:
        text = ast.unparse(arg)
    except Exception:  # noqa: BLE001 — unparse 只为诊断
        text = "<expr>"
    return None, text, canonical


# ---------------------------------------------------------------- 文件系统：按 FileFinder 的规则找模块


@dataclasses.dataclass(frozen=True)
class _Root:
    """`sys.path` 上的一项：目录、标签、是否确认它真的在 `sys.path` 上。"""

    path: Path
    tag: str  # script_dir | project_root | cwd
    confirmed: bool = True


@dataclasses.dataclass(frozen=True)
class _Hit:
    """一个目录里对一个名字的命中：`package`（`path` 是 `__init__`）/ `module` / `extension` / `namespace`
    （`path` 是目录）。`blocked` 非空 = 命中了，但文件被链接重定向 / 在项目外：不读、不下探。"""

    kind: str
    path: Path
    blocked: str = ""
    root: _Root | None = None


@dataclasses.dataclass(frozen=True)
class _Found:
    """沿一串目录找到的结论：第一个常规命中；没有常规命中时是全部命名空间部分。"""

    kind: str
    hit: _Hit
    portions: tuple[_Hit, ...] = ()
    implicit: bool = False  # 只在 importer 自己的目录里找到（不在 sys.path 上）

    @property
    def search_dirs(self) -> list[tuple[Path, _Root | None]]:
        """子模块在哪些目录里找。"""
        if self.kind == "package":
            return [(self.hit.path.parent, self.hit.root)]
        if self.kind == "namespace":
            return [(p.path, p.root) for p in self.portions]
        return []


@dataclasses.dataclass
class _Listing:
    """一个目录的清单（名字逐字，不折叠大小写）。"""

    dirs: frozenset[str]
    py: frozenset[str]  # .py 常规文件的 stem
    ext: dict[str, str]  # 扩展模块 stem → 文件名

    def has_python(self) -> bool:
        return bool(self.py or self.ext)

    def stems(self) -> set[str]:
        return set(self.dirs) | set(self.py) | set(self.ext)


class _Finder:
    """在目录里按 CPython `FileFinder` 的规则找一个名字——只看目录清单，不 import、不 `find_spec`。"""

    def __init__(self, root: Path, *, no_follow: bool, budget: scanbudget.Budget) -> None:
        self.root = root
        self.no_follow = no_follow
        self.budget = budget
        self._lists: dict[str, _Listing | None] = {}
        self.unreadable: set[str] = set()  # 项目相对

    def listing(self, d: Path) -> _Listing | None:
        key = os.path.normcase(str(d))
        if key in self._lists:
            return self._lists[key]
        out: _Listing | None
        try:
            dirs: set[str] = set()
            py: set[str] = set()
            ext: dict[str, str] = {}
            with os.scandir(d) as it:
                for entry in it:
                    if not self.budget.charge_entry():
                        break
                    name = entry.name
                    try:
                        is_dir = entry.is_dir()
                        is_file = entry.is_file()
                    except OSError:
                        continue
                    if is_dir:
                        dirs.add(name)
                    elif is_file:
                        if name.endswith(".py"):
                            py.add(name[:-3])
                        else:
                            m = _EXT_RE.match(name)
                            if m:
                                ext.setdefault(m.group("name"), name)
            out = _Listing(frozenset(dirs), frozenset(py), ext)
        except (FileNotFoundError, NotADirectoryError):
            out = None  # 没有这个目录：不是「读不动」
        except (OSError, ValueError):
            out = None
            rel = _rel(self.root, d)
            self.unreadable.add(rel)
            self.budget.note(scanbudget.ISSUE_UNREADABLE_DIR, scope="import", path=rel)
        self._lists[key] = out
        return out

    def guard(self, p: Path) -> str:
        """命中的路径能不能碰：no_follow 时任何一级是符号链接 / 路径替身就不碰；跟随模式下实体必须在项目内。"""
        if self.no_follow and scanbudget.redirected_component(self.root, p) is not None:
            return "redirect"
        if not projectenv.within(self.root, p):
            return "outside_project"
        return ""

    def find_in(self, d: Path, name: str) -> _Hit | None:
        """`FileFinder.find_spec` 在一个目录里的优先级：带 `__init__` 的目录包 > 扩展模块 > `.py` > 命名空间
        目录（目录里至少有一个 Python 文件才算候选——纯数据目录不是）。名字逐字匹配目录清单。"""
        if not name.isidentifier():  # 拼进路径的每一段必须是合法标识符
            return None
        ls = self.listing(d)
        if ls is None:
            return None
        sub: _Listing | None = None
        if name in ls.dirs:
            sub = self.listing(d / name)
            if sub is not None:
                if "__init__" in sub.ext:
                    return self._hit("package", d / name / sub.ext["__init__"])
                if "__init__" in sub.py:
                    return self._hit("package", d / name / "__init__.py")
        if name in ls.ext:
            return self._hit("extension", d / ls.ext[name])
        if name in ls.py:
            return self._hit("module", d / f"{name}.py")
        if sub is not None and sub.has_python():
            return self._hit("namespace", d / name)
        return None

    def _hit(self, kind: str, path: Path) -> _Hit:
        return _Hit(kind, path, blocked=self.guard(path))

    def near_miss(self, d: Path, name: str) -> bool:
        """目录里有只差大小写的同名条目（`Utils` vs `utils.py`）——真实 Python 不会匹配它。"""
        ls = self.listing(d)
        if ls is None:
            return False
        low = name.lower()
        return any(s != name and s.lower() == low for s in ls.stems())


@dataclasses.dataclass
class _Top:
    """一个顶级名的查找结论。"""

    kind: str  # builtin | frozen | preloaded | found | none
    found: _Found | None = None
    ignored_local: bool = (
        False  # 内建 / 预加载名，磁盘上却有同名本地文件（真实 Python 不会 import 它）
    )
    ns_ignored: bool = False  # 本地命名空间目录败给了标准库的常规包
    near_miss: bool = False  # 有只差大小写的同名文件


# ---------------------------------------------------------------- 本地模块：读与解析


def _pinned(root: Path, path: Path) -> str | None:
    """`path` 钉在 `root` 之内（词法：绝对化 + 归一化 + 前缀）；越界回 None。读文件前的最后一道闸。

    写成静态分析认得的 barrier 形状（同 `projectenv.contained_path`：`startswith` 单独控制通往返回值的
    分支，相等那一支回 `root` 自身）。只做词法判断、不 realpath——no_follow 要靠未解析的路径看出链接，
    链接指向项目外由 `_Finder.guard` 与 `projectenv.within` 另判。"""
    try:
        base = os.path.abspath(os.fspath(root))
        cand = os.path.abspath(os.fspath(path))
    except (OSError, ValueError):
        return None
    if os.path.normcase(cand) == os.path.normcase(base):
        return base
    if not os.path.normcase(cand).startswith(os.path.normcase(base).rstrip(os.sep) + os.sep):
        return None
    return cand


def _rel(root: Path, path: Path) -> str:
    """项目相对 POSIX 路径（纯字符串运算，不 `resolve`——不碰文件系统，也不跟随链接）；不在项目下回文件名。"""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # Windows 跨盘符
        return path.name
    parts = Path(rel).parts
    if not parts or parts[0] == os.pardir:
        return path.name if parts else ""
    return "/".join(parts)


def _parse(text: str, rel: str) -> tuple[ast.Module | None, dict | None]:
    try:
        return ast.parse(text), None
    except (SyntaxError, ValueError) as exc:
        return None, {"path": rel, "kind": "syntax", "detail": str(exc)[:200]}
    except (RecursionError, MemoryError):
        return None, {"path": rel, "kind": scanbudget.ISSUE_PARSE_FAILED}


@dataclasses.dataclass
class _Parsed:
    uses: list[ImportUse]
    dynamic: list[ImportUse]
    relatives: list[RelativeImport]
    path_mutations: list[int]
    tree: ast.Module


@dataclasses.dataclass
class _Job:
    """一个要处理的 Python 文件：它是以哪个点分名被 import 的、包上下文、继承的上下文、深度。"""

    path: Path
    modname: str
    package: str | None  # `__package__`；None / 空 = 没有包（相对导入会 ImportError）
    ctx: str
    depth: int
    via: str  # 项目相对路径（脚本本身为空）
    pkg_dir: Path | None = None  # 是包的 `__init__` 时的包目录


class _Scanner:
    def __init__(
        self,
        root: Path,
        script: str,
        *,
        declared: dict[str, str],
        stdlib: frozenset[str] | None,
        tree: ast.Module | None,
        entry: Entry,
        builtin: frozenset[str] | None,
        no_follow: bool,
        budget: scanbudget.Budget | None,
    ) -> None:
        self.root = root
        self.script = script
        self.script_p = root / script
        self.declared = declared
        self.stdlib_names = (
            HOST_STDLIB if stdlib is None else (frozenset(stdlib) | {"__future__", "builtins"})
        )
        self.tree_given = tree
        self.entry = entry
        self.builtin_names = BUILTIN_NAMES | (frozenset(builtin) if builtin else frozenset())
        self.ambiguous_builtin = HOST_BUILTIN_EXTRA if builtin is None else frozenset()
        self.no_follow = no_follow
        self.budget = budget if budget is not None else scanbudget.Budget()
        self.finder = _Finder(root, no_follow=no_follow, budget=self.budget)

        self.uses: list[ImportUse] = []
        self.dynamic: list[ImportUse] = []
        self.problems: list[dict] = []
        self.warnings: list[dict] = []
        self._warned: set[tuple] = set()
        self.truncated = False
        self.read_files: list[str] = []
        self.parsed: dict[str, _Parsed | None] = {}
        self.file_ctx: dict[str, int] = {}
        self.queue: list[_Job] = []
        self.origins: dict[str, _Top] = {}
        self._tops: dict[str, _Top] = {}
        self._implicit: dict[tuple[str, str], _Found | None] = {}
        self.main_reachable = False
        self.main_queue: list[Path] = []
        self.blocked_hit = False
        self.path_modified = False
        self.cwd_rejected = False
        self.roots = self._search_roots()

    # ------------------------------------------------------------ 搜索根
    def _search_roots(self) -> list[_Root]:
        if self.entry.kind == execspec.TARGET_SCRIPT:
            script_dir = self.script_p.parent
            roots = [_Root(script_dir, "script_dir")]
            if script_dir.resolve(strict=False) != self.root.resolve(strict=False):
                # safe worker 把项目根（图库根）放进 sys.path；native 下它不在上面——仍按本地判（宁可
                # 不误装），但标「未确认」
                roots.append(
                    _Root(self.root, "project_root", self.entry.profile == figcapture.PROFILE_SAFE)
                )
            return roots
        cwd = self._cwd_root()
        if cwd is None:
            # 调用方给的 cwd 出了项目：不拿它当搜索根、不读项目外任何东西；搜索面看不全
            self.warn("cwd_outside_project")
            self.cwd_rejected = True
            return []
        confirmed = bool(self.entry.cwd) or self.entry.cwd_mode == execspec.CWD_PROJECT_ROOT
        return [_Root(cwd, "cwd", confirmed)]

    def _cwd_root(self) -> Path | None:
        """`entry.cwd` 钉在项目内：只收项目相对、规范化后不以 `..` 起头、realpath 之后仍在项目根内的目录。
        绝对路径 / 盘符 / UNC / 反斜杠起头一律拒（不论宿主是 POSIX 还是 Windows：同一份输入两边判同一个结果）。"""
        rel = self.entry.cwd
        if not rel:
            return self.root
        win = PureWindowsPath(rel)
        if (
            PurePosixPath(rel).is_absolute()
            or win.anchor
            or win.drive
            or rel.startswith(("/", "\\"))
            or "\0" in rel
        ):
            return None
        parts = PurePosixPath(rel.replace("\\", "/")).parts
        depth = 0
        for part in parts:  # 规范化后是否越出项目根（`a/../..` 越出，`a/../b` 不越出）
            if part == "..":
                depth -= 1
                if depth < 0:
                    return None
            elif part != ".":
                depth += 1
        pinned = _pinned(self.root, self.root.joinpath(*parts))
        if pinned is None or not projectenv.within(self.root, Path(pinned)):
            return None
        return Path(pinned)

    # ------------------------------------------------------------ 账本
    def warn(self, code: str, path: str = "", line: int = 0) -> None:
        key = (code, path, line)
        if key in self._warned:
            return
        self._warned.add(key)
        row: dict = {"code": code}
        if path:
            row["path"] = path
        if line:
            row["line"] = line
        self.warnings.append(row)

    # ------------------------------------------------------------ 读
    def _load(self, path: Path, rel: str, via: str) -> _Parsed | None:
        """读 + 解析一个文件；读不了 / 解析不了记 problem 并回 None。"""
        text, problem = self._read(path, rel)
        if problem is not None:
            self.problems.append(problem)
            return None
        tree, problem = _parse(text or "", rel)
        if problem is not None:
            self.problems.append(problem)
            return None
        return self._visit(tree, via, rel)

    def _visit(self, tree: ast.Module, via: str, rel: str) -> _Parsed:
        v = _Visitor(via, _collect_aliases(tree))
        v.visit(tree)
        if v.path_mutations:
            self.path_modified = True
            for line in v.path_mutations:
                self.warn("sys_path_modified", rel, line)
        return _Parsed(v.uses, v.dynamic, v.relatives, v.path_mutations, tree)

    def _read(self, path: Path, rel: str) -> tuple[str | None, dict | None]:
        # 读之前把路径钉在项目根内（词法）：越界不 stat、不读。链接指到项目外由 `_Finder.guard` / no_follow 另判
        pinned = _pinned(self.root, path)
        if pinned is None:
            return None, {"path": rel, "kind": "outside_project"}
        path = Path(pinned)
        try:
            st = os.lstat(pinned) if self.no_follow else os.stat(pinned)
        except OSError as exc:
            return None, {"path": rel, "kind": "io", "detail": type(exc).__name__}
        if scanbudget.is_placeholder(st):
            self.budget.note(scanbudget.ISSUE_PLACEHOLDER, scope="import", path=rel)
            return None, {"path": rel, "kind": scanbudget.ISSUE_PLACEHOLDER}
        reason = self.budget.charge_source(st.st_size)
        if reason is not None:
            self.budget.note(reason, scope="import", path=rel)
            kind = "too_large" if reason == scanbudget.ISSUE_TOO_LARGE else reason
            return None, {"path": rel, "kind": kind}
        try:
            base, parts = self._split(path)
            text = scanbudget.read_regular_text(
                base, *parts, no_follow=self.no_follow, max_bytes=self.budget.limits.max_file_bytes
            )
        except OSError as exc:
            return None, {"path": rel, "kind": "io", "detail": type(exc).__name__}
        return text, None

    def _split(self, path: Path) -> tuple[Path, tuple[str, ...]]:
        """`path` 拆成（读文件的根，其下的各级名字）；在项目根下用项目根，否则只钉在它自己的目录。"""
        try:
            rel = os.path.relpath(path, self.root)
        except ValueError:
            rel = os.pardir
        parts = tuple(Path(rel).parts)
        if not parts or parts[0] == os.pardir:
            return path.parent, (path.name,)
        return self.root, parts

    # ------------------------------------------------------------ 查找
    def find_top(self, name: str, importer_dir: Path | None) -> _Top:
        """顶级名的查找：内建 / 冻结 / 启动即加载的 stdlib 先于文件系统；然后沿 `sys.path` 的搜索根
        （常规命中立即胜出，命名空间目录继续往后找）；最后才是 importer 自己目录里的兄弟文件（Python 3
        里并不在 `sys.path` 上，标 implicit）。"""
        if name in self.builtin_names or name in FROZEN_NAMES or name in PRELOADED_STDLIB:
            kind = (
                "builtin"
                if name in self.builtin_names
                else "frozen"
                if name in FROZEN_NAMES
                else "preloaded"
            )
            ignored = any(self.finder.find_in(r.path, name) is not None for r in self.roots)
            return _Top(kind, ignored_local=ignored)
        if name not in self._tops:
            self._tops[name] = self._search_roots_for(name)
        top = self._tops[name]
        if top.kind == "none" and importer_dir is not None and name not in self.stdlib_names:
            key = (name, os.path.normcase(str(importer_dir)))
            if key not in self._implicit:
                hit = (
                    self.finder.find_in(importer_dir, name)
                    if depresolve.valid_import_name(name)
                    else None
                )
                self._implicit[key] = (
                    _Found(hit.kind, hit, (hit,), implicit=True) if hit is not None else None
                )
            found = self._implicit[key]
            if found is not None:
                return _Top("found", found, near_miss=top.near_miss)
        return top

    def _search_roots_for(self, name: str) -> _Top:
        if not depresolve.valid_import_name(name):
            return _Top("none")
        found = self._search([(r.path, r) for r in self.roots], name)
        if found is None:
            near = any(self.finder.near_miss(r.path, name) for r in self.roots)
            return _Top("none", near_miss=near)
        if found.kind == "namespace" and name in self.stdlib_names:
            # 常规包压过命名空间：标准库的 `statistics` 不会败给项目里一个没有 __init__ 的 statistics/ 目录
            return _Top("none", ns_ignored=True)
        return _Top("found", found)

    def _search(self, dirs: list[tuple[Path, _Root | None]], name: str) -> _Found | None:
        portions: list[_Hit] = []
        for d, root in dirs:
            h = self.finder.find_in(d, name)
            if h is None:
                continue
            h = dataclasses.replace(h, root=root)
            if h.kind == "namespace":
                portions.append(h)
                continue
            return _Found(h.kind, h)
        if portions:
            return _Found("namespace", portions[0], tuple(portions))
        return None

    def child(self, parent: _Found, part: str) -> _Found | None:
        if not depresolve.valid_import_name(part):
            return None
        return self._search(parent.search_dirs, part)

    # ------------------------------------------------------------ 入口
    def _module_entry(self) -> tuple[Path, str, str] | None:
        """`python -m pkg.mod` 的入口：回 `(源文件, __package__, 末位模块名)`；父包的 `__init__` 排进队列
        （它们先于入口执行）。找不到 / 是编译扩展 / 被链接挡住回 None。"""
        parts = self.entry.module.split(".")
        top = self.find_top(parts[0], None)
        if top.kind != "found" or top.found is None:
            return None
        cur = top.found
        chain = [(parts[0], cur)]
        for i, part in enumerate(parts[1:], 1):
            nxt = self.child(cur, part)
            if nxt is None:
                return None
            chain.append((".".join(parts[: i + 1]), nxt))
            cur = nxt
        package = ".".join(parts[:-1])
        target = cur
        if cur.kind == "package":  # `python -m pkg` 跑 pkg/__main__.py，__package__ 是 pkg
            target = self.child(cur, "__main__")
            package = ".".join(parts)
            if target is None or target.kind != "module":
                return None
        elif cur.kind != "module":
            return None
        if target.hit.blocked:
            self.blocked_hit = True
            return None
        for dotted, found in chain:
            if found.kind == "package":
                self.enqueue(
                    dotted, found, CONTEXT_UNCONDITIONAL, _Job(Path(), "", None, "", 0, "")
                )
        return target.hit.path, package, parts[-1]

    def run(self) -> ScanResult:
        entry_pkg: str | None = None
        stem = self.script_p.stem
        parsed: _Parsed | None = None
        if self.entry.kind == execspec.TARGET_MODULE:
            got = self._module_entry()
            if got is None:
                self.warn("module_not_found")
                self.problems.append({"path": "", "kind": "module_not_found"})
            else:
                self.script_p, entry_pkg, stem = got
        entry_rel = (
            _rel(self.root, self.script_p)
            if self.entry.kind == execspec.TARGET_MODULE
            else Path(self.script).as_posix()
        )
        self.stem = stem
        if self.entry.kind == execspec.TARGET_SCRIPT or entry_pkg is not None:
            if self.tree_given is not None and self.entry.kind == execspec.TARGET_SCRIPT:
                parsed = self._visit(self.tree_given, "", entry_rel)
            else:
                parsed = self._load(self.script_p, entry_rel, "")
            self.read_files.append(entry_rel)
        unused: frozenset[str] = frozenset()
        if parsed is not None:
            unused = figcapture.unused_imports(parsed.tree)
            targets = _relative_targets(parsed.tree, self.script_p, self.root)
            if targets is None:
                self.main_reachable = True
            else:
                self.main_queue += targets
            self.dynamic += parsed.dynamic
            self.process(
                _Job(self.script_p, "", entry_pkg, CONTEXT_UNCONDITIONAL, 0, "", None), parsed
            )
        while self.queue:
            self.follow(self.queue.pop(0))
        unused = self._main_pass(unused)
        return self._finish(unused)

    # ------------------------------------------------------------ 处理
    def process(self, job: _Job, parsed: _Parsed) -> None:
        """把一个文件里的 import 变成 uses（继承来路的上下文），并把它们指向的本地文件排进队列。"""
        for su in parsed.uses:
            if job.depth == 0:
                use = su
            else:
                merged = max((job.ctx, su.context), key=CONTEXTS.index)
                use = ImportUse(su.module, su.full, merged, su.lineno, job.via, su.names)
            self.uses.append(use)
            self.handle_absolute(use, job)
        for rel in parsed.relatives:
            merged = max((job.ctx, rel.context), key=CONTEXTS.index)
            self.handle_relative(rel, merged, job)

    def follow(self, job: _Job) -> None:
        key = os.path.normcase(str(job.path))
        prior = self.file_ctx.get(key)
        if prior is not None:
            # 已经扫过：只有这次来路的上下文更强（先在 try 里、后又无条件 import）才重放一遍它的 import
            if CONTEXTS.index(job.ctx) < prior:
                self.file_ctx[key] = CONTEXTS.index(job.ctx)
                cached = self.parsed.get(key)
                if cached is not None:
                    self.process(job, cached)
            return
        if job.depth > MAX_DEPTH or len(self.parsed) >= MAX_LOCAL_MODULES:
            self.truncated = True
            return
        if self.budget.stop_reason() is not None:
            self.truncated = True
            return
        self.file_ctx[key] = CONTEXTS.index(job.ctx)
        self.read_files.append(job.via)
        parsed = self._load(job.path, job.via, job.via)
        self.parsed[key] = parsed
        if parsed is None:
            return
        if job.pkg_dir is not None:
            files = _package_files(job.pkg_dir, self.budget)
            if files is None:
                self.main_reachable = True
            else:
                self.main_queue += files
        if figcapture.reaches_main(parsed.tree, self.stem):
            self.main_reachable = True
        targets = _relative_targets(parsed.tree, job.path, self.root)
        if targets is None:
            self.main_reachable = True
        else:
            self.main_queue += targets
        for d in parsed.dynamic:
            self.dynamic.append(ImportUse("", d.full, CONTEXT_DYNAMIC, d.lineno, job.via))
        self.process(job, parsed)

    # ---- 一条 import 指向哪些本地文件 ----
    def handle_absolute(self, use: ImportUse, job: _Job) -> None:
        parts = use.full.split(".")
        if not parts or not all(parts):
            return
        top = self.find_top(parts[0], job.path.parent)
        self.record(parts[0], top)
        self.follow_chain(parts, use.names, use.context, top, job)

    def handle_relative(self, rel: RelativeImport, ctx: str, job: _Job) -> None:
        where = job.via or Path(self.script).as_posix()
        if not job.package:
            # `python script.py` 的入口 / 顶层模块没有包：真实 Python 在这里抛 ImportError，不猜
            self.warn("relative_import_no_package", where, rel.lineno)
            return
        base = job.package.split(".")
        drop = rel.level - 1
        if drop >= len(base):
            self.warn("relative_import_beyond_top", where, rel.lineno)
            return
        if drop:
            base = base[: len(base) - drop]
        parts = base + (rel.module.split(".") if rel.module else [])
        top = self.find_top(parts[0], job.path.parent)
        self.follow_chain(parts, rel.names, ctx, top, job)

    def follow_chain(
        self, parts: list[str], names: tuple[str, ...], ctx: str, top: _Top, job: _Job
    ) -> None:
        if top.kind != "found" or top.found is None:
            return
        cur = top.found
        chain: list[tuple[str, _Found]] = [(parts[0], cur)]
        complete = True
        for i in range(1, len(parts)):
            nxt = self.child(cur, parts[i])
            if nxt is None:
                complete = False
                break
            chain.append((".".join(parts[: i + 1]), nxt))
            cur = nxt
        if complete and cur.kind in ("package", "namespace"):
            for n in names:
                if n == "*":
                    continue
                sub = self.child(cur, n)
                if sub is not None:  # 是子模块；否则 n 是包里的属性——不假定
                    chain.append((f"{'.'.join(parts)}.{n}", sub))
        merged = max((job.ctx, ctx), key=CONTEXTS.index)
        for dotted, found in chain:
            self.enqueue(dotted, found, merged, job)

    def enqueue(self, dotted: str, found: _Found, ctx: str, job: _Job) -> None:
        hit = found.hit
        if found.kind == "namespace":
            return  # 命名空间目录自己不执行任何代码
        if hit.blocked:
            self.blocked_hit = True
            code = "link_not_followed" if hit.blocked == "redirect" else "link_outside_project"
            rel = _rel(self.root, hit.path)
            self.warn(code, rel)
            self.budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="import", path=rel)
            return
        if found.kind == "extension" or hit.path.suffix != ".py":
            self.main_reachable = True  # 编译扩展没有源码可扫：判不清它碰不碰 __main__
            return
        is_pkg = found.kind == "package"
        if is_pkg:
            package = dotted
        else:
            package = "" if found.implicit else dotted.rpartition(".")[0]
        self.queue.append(
            _Job(
                hit.path,
                dotted,
                package,
                ctx,
                job.depth + 1,
                _rel(self.root, hit.path),
                hit.path.parent if is_pkg else None,
            )
        )

    def record(self, name: str, top: _Top) -> None:
        old = self.origins.get(name)
        if old is None or _rank(top) > _rank(old):
            self.origins[name] = top

    # ------------------------------------------------------------ 包内文件与相对导入目标
    def _main_pass(self, unused: frozenset[str]) -> frozenset[str]:
        """包内文件与相对导入的目标：只为 `reaches_main` 再看一遍（不进 needed——那是既有的跟进规则）。
        相对导入解析不到、越出项目根、指到编译扩展，或文件太多，都按看不全处理。"""
        main_scanned = set(self.file_ctx)
        queue = self.main_queue
        while queue and unused and not self.main_reachable:
            f = queue.pop(0)
            key = os.path.normcase(str(f))
            if key in main_scanned:
                continue
            if len(main_scanned) >= MAX_MAIN_SCAN_FILES or not projectenv.within(self.root, f):
                self.main_reachable = True
                break
            main_scanned.add(key)
            rel = _rel(self.root, f)
            self.read_files.append(rel)
            text, problem = self._read(f, rel)
            sub, problem2 = _parse(text or "", rel) if problem is None else (None, problem)
            if sub is None:
                self.problems.append(problem or problem2)
                break
            targets = _relative_targets(sub, f, self.root)
            if targets is None or figcapture.reaches_main(sub, self.stem):
                self.main_reachable = True
                break
            queue += targets
        if (
            self.main_reachable
            or self.truncated
            or self.problems
            or self.dynamic
            or self.blocked_hit
        ):
            # 看不全 = 判不清：跟进被截断、有本地模块读不了、有非字面量的动态 import（可能装进一个没扫过、
            # 借用 __main__ 的本地模块）——都不再承认「脚本的别名没被用到」
            return frozenset()
        return unused

    # ------------------------------------------------------------ 分类
    def _finish(self, unused: frozenset[str]) -> ScanResult:
        by_name: dict[str, list[ImportUse]] = {}
        for u in self.uses:
            if u.module:
                by_name.setdefault(u.module, []).append(u)
        classes = [self._classify(name, by_name[name], unused) for name in sorted(by_name)]
        roots = tuple(
            {"path": _rel(self.root, r.path) or ".", "tag": r.tag, "confirmed": r.confirmed}
            for r in self.roots
        )
        complete = (
            all(r.confirmed for r in self.roots)
            and not self.cwd_rejected
            and not self.finder.unreadable
            and not self.blocked_hit
            and not self.path_modified
            and self.budget.stopped is None
        )
        return ScanResult(
            classes=tuple(classes),
            uses=tuple(self.uses),
            dynamic=tuple(self.dynamic),
            problems=tuple(self.problems),
            truncated=self.truncated,
            files=tuple(sorted(set(self.read_files))),
            search_roots=roots,
            search_complete=complete,
            warnings=tuple(self.warnings),
            issues=tuple(self.budget.issues()),
        )

    def _classify(self, name: str, group: list[ImportUse], unused: frozenset[str]) -> ImportClass:
        context = min((u.context for u in group), key=CONTEXTS.index)
        lines = tuple(sorted({u.lineno for u in group if not u.via}))
        via = tuple(sorted({u.via for u in group if u.via}))
        top = self.origins.get(name) or self.find_top(name, None)
        base = {"module": name, "context": context, "lines": lines, "via": via}
        evidence: list[str] = []
        warns: list[str] = []

        # 1. 内建 / 冻结 / 启动即加载：先于文件系统，本地同名文件到不了
        if top.kind in ("builtin", "frozen", "preloaded"):
            evidence.append(
                {
                    "builtin": "builtin_module",
                    "frozen": "frozen_module",
                    "preloaded": "preloaded_stdlib",
                }[top.kind]
            )
            if top.ignored_local:
                warns.append("local_file_never_imported")
            kind = {
                "builtin": ORIGIN_BUILTIN,
                "frozen": ORIGIN_FROZEN,
                "preloaded": ORIGIN_STDLIB,
            }[top.kind]
            return ImportClass(
                bucket=BUCKET_STDLIB,
                origin_kind=kind,
                resolution_status=STATUS_RESOLVED,
                evidence=tuple(evidence),
                warnings=tuple(warns),
                **base,
            )

        found = top.found if top.kind == "found" else None
        dist, source = map_distribution(name, self.declared)
        in_stdlib = name in self.stdlib_names

        # 2. 本地常规命中（包 / 模块 / 扩展）：在 sys.path 上排在标准库之前
        if found is not None and found.kind in ("package", "module", "extension"):
            hit = found.hit
            if hit.blocked:
                code = "link_not_followed" if hit.blocked == "redirect" else "link_outside_project"
                # 命中了、却不能读：不假装知道它是什么——stdlib 名仍按 stdlib、其余走映射，状态 unverified
                if in_stdlib:
                    bucket, d, s = BUCKET_STDLIB, "", ""
                elif dist:
                    bucket, d, s = BUCKET_THIRD_PARTY, dist, source
                else:
                    bucket, d, s = BUCKET_UNKNOWN, "", ""
                return ImportClass(
                    bucket=bucket,
                    distribution=d,
                    resolution_source=s,
                    origin_kind=ORIGIN_STDLIB if in_stdlib else ORIGIN_UNRESOLVED,
                    resolution_status=STATUS_UNVERIFIED,
                    evidence=(code,),
                    warnings=(code,),
                    **base,
                )
            status = STATUS_RESOLVED
            if found.implicit:
                evidence.append("sibling_dir_of_importer")
                warns.append("implicit_sibling_import")
                status = STATUS_UNVERIFIED
            elif hit.root is not None:
                evidence.append(
                    {
                        "script_dir": "sys_path_script_dir",
                        "project_root": "sys_path_project_root",
                        "cwd": "sys_path_cwd",
                    }[hit.root.tag]
                )
                if not hit.root.confirmed:
                    warns.append("path_not_confirmed")
                    status = STATUS_UNVERIFIED
            evidence.append(
                {
                    "package": "package_with_init",
                    "module": "module_file",
                    "extension": "extension_file",
                }[found.kind]
            )
            evidence.append("exact_name_match")
            shadow = ""
            if in_stdlib:
                shadow = "stdlib"
                if name in self.ambiguous_builtin and status == STATUS_RESOLVED:
                    status = STATUS_AMBIGUOUS
                    warns.append("may_shadow_builtin")
            elif dist:
                shadow = "third_party"
            return ImportClass(
                bucket=BUCKET_LOCAL,
                local_path=_rel(self.root, hit.path),
                origin_kind=ORIGIN_EXTENSION if found.kind == "extension" else ORIGIN_LOCAL,
                resolution_status=status,
                evidence=tuple(evidence),
                shadowing=shadow,
                warnings=tuple(warns),
                **base,
            )

        # 3. 标准库
        if in_stdlib:
            evidence.append("stdlib_name_table")
            if top.ns_ignored:
                warns.append("local_namespace_dir_ignored")
            return ImportClass(
                bucket=BUCKET_STDLIB,
                origin_kind=ORIGIN_STDLIB,
                resolution_status=STATUS_RESOLVED,
                evidence=tuple(evidence),
                warnings=tuple(warns),
                **base,
            )

        # 4. 命名空间目录：常规包在整条 sys.path 上任何位置都压过它——不能当成唯一提供者
        if found is not None and found.kind == "namespace":
            evidence += ["namespace_dir", "exact_name_match"]
            warns.append("namespace_may_be_overridden")
            return ImportClass(
                bucket=BUCKET_LOCAL,
                local_path=_rel(self.root, found.hit.path),
                distribution=dist,
                resolution_source=source,
                origin_kind=ORIGIN_NAMESPACE,
                resolution_status=STATUS_AMBIGUOUS,
                evidence=tuple(evidence),
                warnings=tuple(warns),
                **base,
            )

        # 5. 都不是：映射表候选 / 未知
        if top.near_miss:
            evidence.append("case_mismatch_rejected")
            warns.append("name_differs_in_case_from_file")
        if dist:
            evidence.append(
                "mapped_by_project_declared"
                if source == depresolve.SOURCE_PROJECT_DECLARED
                else "mapped_by_curated"
            )
        else:
            evidence.append("no_provider_found")
        return ImportClass(
            bucket=BUCKET_THIRD_PARTY if dist else BUCKET_UNKNOWN,
            distribution=dist,
            resolution_source=source,
            # 本地模块也 import 了它 = 它的绑定在那边可能被读：照旧按上下文判
            unused=name in unused and not via,
            origin_kind=ORIGIN_UNRESOLVED,
            resolution_status=STATUS_UNVERIFIED if dist else STATUS_UNRESOLVED,
            evidence=tuple(evidence),
            warnings=tuple(warns),
            **base,
        )


def _rank(top: _Top) -> int:
    if top.kind in ("builtin", "frozen", "preloaded"):
        return 4
    if top.kind == "found" and top.found is not None:
        return 2 if top.found.implicit else 3
    return 1


# ---------------------------------------------------------------- 入口


def scan(
    root: str | Path,
    script: str,
    *,
    declared: dict[str, str] | None = None,
    stdlib: frozenset[str] | None = None,
    tree: ast.Module | None = None,
    entry: Entry | None = None,
    builtin: frozenset[str] | None = None,
    no_follow: bool = False,
    budget: scanbudget.Budget | None = None,
) -> ScanResult:
    """扫描脚本（及其本地模块）的 import，逐个顶级名分类。

    `declared` 是项目声明过的 distribution（规范化名 → 版本约束，来自 `depresolve`）——
    第三方名字映射到 distribution 的第一档证据；`stdlib` 是**目标解释器**的标准库名字表
    （没给用宿主的）；`builtin` 是目标解释器的 `sys.builtin_module_names`（没给只认恒成立的那张表，
    宿主上才内建的名字被本地文件遮蔽时标 `ambiguous`）；`tree` 是调用方已经解析好的脚本 AST（`discover`
    那边读过一次就不再读）。`entry` 说脚本怎么被跑（默认 = safe worker 跑脚本：搜索根是脚本目录 + 项目根，
    与 `worker.py` 一致；见 `Entry`；`python -m` 时 `script` 被忽略，入口由 `entry.module` 定）。
    `no_follow=True`（导入即扫描的口径）：符号链接 / 路径替身不下探、不读；默认跟随项目内的链接，指到
    项目外的永远不读。`budget` 是 `scanbudget` 的预算 + 账本（没给就用默认上限）：占位文件不读、单文件 /
    累计字节与墙钟有上限，超了留痕而不是悄悄少读。
    """
    return _Scanner(
        Path(root),
        script,
        declared=dict(declared or {}),
        stdlib=stdlib,
        tree=tree,
        entry=entry if entry is not None else Entry(),
        builtin=builtin,
        no_follow=no_follow,
        budget=budget,
    ).run()


def _package_files(pkg_dir: Path, budget: scanbudget.Budget) -> list[Path] | None:
    """包目录里的全部 .py（有界遍历，不跟随目录链接）；超过 `MAX_MAIN_SCAN_FILES` 或读不了回 None。"""
    out: list[Path] = []
    try:
        for cur, dirs, files in os.walk(pkg_dir, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                if not budget.charge_entry():
                    return None
                if name.endswith(".py"):
                    out.append(Path(cur) / name)
                    if len(out) > MAX_MAIN_SCAN_FILES:
                        return None
    except OSError:
        return None
    return out


def _as_module(target: Path) -> list[Path] | None:
    """一个点分路径在磁盘上对应的源码：`x.py`、`x/__init__.py` 或命名空间目录里的 .py；找不到 / 只有编译
    扩展（没有源码可扫）回 None。"""
    try:
        py = target.with_name(target.name + ".py")
        if py.is_file():
            return [py]
        init = target / "__init__.py"
        if init.is_file():
            return [init]
        if target.is_dir():
            return sorted(p for p in target.glob("*.py") if p.is_file())
    except OSError:
        return None
    return None


def _relative_targets(tree: ast.AST, file: Path, root: Path) -> list[Path] | None:
    """`file` 里每条相对导入（`from . import x` / `from .x import y` / `from .. import z`）解析到的源码文件；
    有一条解析不到、越出项目根、或只能落到编译扩展，就回 None（看不全）。`from . import name` 里的
    name 不是子模块时是包 `__init__.py` 里的属性，包本身找得到就算解析到了。"""
    out: list[Path] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level):
            continue
        base = file.parent
        for _ in range(node.level - 1):
            base = base.parent
        if not projectenv.within(root, base):
            return None
        segs = node.module.split(".") if node.module else []
        if not all(seg.isidentifier() for seg in segs):
            return None
        target = base.joinpath(*segs) if segs else base
        found = _as_module(target)
        if found is None:
            return None
        out += found
        for alias in node.names:
            if alias.name != "*" and alias.name.isidentifier():
                out += _as_module(target / alias.name) or []
    return out


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
