"""数据上下文证据：脚本用相对路径读的数据，在哪个 cwd 下才找得到（统一实施包 U03，ADR 0057）。

safe worker 默认把 cwd 切到会话沙盒，只读的 `open` 回退到**脚本目录**（`figcapture`）。
这条回退救得回 `open("data.csv")`（脚本同目录），救不回 `paper/scripts/figure.py` 读
`data/x.csv` 而数据在 `paper/data/`——用户在终端里是站在 `paper/` 敲
`python scripts/figure.py` 的（FO02）。以前这种脚本在 Tavotto 里一张图都画不出来，界面
报「脚本跑完没出图」再给一个「改为在脚本目录里运行」的入口——而脚本目录也不对。

本模块只回答一个**静态**问题：脚本源码里那些像相对文件路径的字面量，在「脚本目录」
与「项目根」两个候选 cwd 下各自能找到哪些。它**不执行脚本、不猜、不搜索同名文件**
（FO08：未知绝对路径不靠同名搜索猜）：

* 找不到的字面量就是找不到（`missing`），不去别的目录里翻同名文件；
* 两个候选目录里都找得到、而且**内容不同**（同名干扰，FO07）→ `ambiguous`，
  由用户选，机器不裁决；
* 一个字面量都没有 / 一个都找不到 → `none` / `unknown`：这里说不出话，按默认走，
  真跑出来的失败仍然看得见（`no_figures_captured` 那条既有路径）。

判据刻意窄：只认字符串常量（f-string / `%` / `.format` 是动态的，一律不算），打开类字面量
只认带**数据类扩展名**或带目录分隔符的相对路径，存图调用（`discover.SAVE_FUNCS`）的
实参归输出不归输入。glob 模式 / `listdir` / `exists` 这类**探路调用**另成一类证据
（`probe_literals`，ADR 0084）：沙盒的只读回退救不回它们，只在脚本目录找得到时结论是
`script_parent`——首开要问。窄的代价是「少问一次」——那时走默认，与今天一样；宽的代价是
「多问一次」——用户每个项目多点一下。两边都不会把错的数据画出来。

纯标准库（只 import 同包的 `projectenv` 取「在不在项目里」那一个判据）；Flask 父进程 import 链上（被 `workdir` / `preparation` 用）。
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import projectenv

#: 「像数据文件」的扩展名（小写、不带点）。表外的名字只有带目录分隔符时才算候选：
#: `os.path` / `matplotlib.pyplot` 这类模块名带点但不带分隔符，不该被当成文件。
DATA_SUFFIXES = frozenset(
    {
        "csv",
        "tsv",
        "txt",
        "dat",
        "data",
        "json",
        "jsonl",
        "npy",
        "npz",
        "h5",
        "hdf",
        "hdf5",
        "mat",
        "xlsx",
        "xls",
        "parquet",
        "feather",
        "pkl",
        "pickle",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "nc",
        "tif",
        "tiff",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "pdf",
        "svg",
        "xyz",
        "lammpstrj",
        "dump",
        "log",
        "out",
        "mtx",
        "fits",
        "db",
        "sqlite",
        "sqlite3",
        "xml",
        "nii",
        "gz",
        "zip",
    }
)

#: 存图调用名——它们的实参是**输出**，不是要找的数据。唯一出处在 `discover.SAVE_FUNCS`；
#: 本模块被 `workdir` import（`workdir` 又被 `pool` import），而 `discover` import
#: `registry` / `atomicio`……为了不把整条静态扫描链拖进 pool 的 import 图，这里镜像一份，
#: `tests/test_databinding.py` 钉住两侧相等。
SAVE_FUNCS = frozenset(
    {
        "save",
        "savefig",
        "imsave",
        "write_image",
        "save_fig",
        "savefigure",
        "save_figure",
        "savefig_pdf",
        "export_fig",
    }
)

CANDIDATE_SCRIPT_PARENT = "script.parent"
CANDIDATE_PROJECT_ROOT = "project.root"

#: 结论（闭集）。`default_ok` = 沙盒默认（回退到脚本目录）就能找到；`project_root` =
#: 只有项目根找得到；`ambiguous` = 两处都有且内容不同；`unknown` = 有候选字面量但一处都
#: 找不到；`none` = 脚本里没有像相对数据路径的字面量。`script_parent`（ADR 0084）= 脚本用
#: `glob` / `listdir` / `exists` 这类**探路调用**找数据、只有脚本目录下找得到——沙盒的只读回退
#: 救不回它们，要在脚本目录里运行。
VERDICT_DEFAULT_OK = "default_ok"
VERDICT_SCRIPT_PARENT = "script_parent"
VERDICT_PROJECT_ROOT = "project_root"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_UNKNOWN = "unknown"
VERDICT_NONE = "none"
VERDICTS = (
    VERDICT_DEFAULT_OK,
    VERDICT_SCRIPT_PARENT,
    VERDICT_PROJECT_ROOT,
    VERDICT_AMBIGUOUS,
    VERDICT_UNKNOWN,
    VERDICT_NONE,
)

#: 只看这么多个字面量：脚本里几百个字符串常量的话，逐个 stat 也是几十毫秒，够了；
#: 而且超过这个数的脚本多半是在拼路径而不是在写路径。
MAX_LITERALS = 64


def _func_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def looks_like_relative_data_path(text: str) -> bool:
    """字符串常量像不像「相对数据文件路径」——判据的全部在这里，别处不许再判一遍。"""
    if not text or len(text) > 240 or text != text.strip():
        return False
    if text.startswith("-") or "://" in text or "\n" in text:
        return False
    if any(ch in text for ch in "{}%*?<>|\"'"):
        return False  # 格式化模板 / glob 模式 / 非法文件名字符：动态或根本不是路径
    # 绝对路径：明确指名的位置，不在本模块的问题里。判据的主语是「脚本作者写的字面量」，
    # 它可能是在另一个 OS 上写的：`/abs/x.csv` 在 Windows 宿主上 `os.path.isabs` 说不是
    # 绝对（3.13 起单斜杠不算），但它在作者的 POSIX 机器上就是；所以 POSIX / Windows
    # 两套规则任一判绝对就算绝对（盘符相对的 `C:x.csv` 也一并出局）。
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        return False
    if len(text) > 1 and text[1] == ":":
        return False
    norm = text.replace("\\", "/")
    if norm.startswith("~"):
        return False
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts or parts[-1] == "..":
        return False  # 空 / 只是「上一级」：不是文件
    last = parts[-1]
    if "." in last and not last.startswith("."):
        suffix = last.rsplit(".", 1)[1].lower()
        if suffix in DATA_SUFFIXES:
            return True
    # 没有数据类扩展名：只有带目录分隔符的才算（`data/run1`）——裸单词太多是 key / 标签
    return "/" in norm and not norm.endswith("/")


def relative_path_literals(source: str) -> dict[str, list[str]]:
    """源码 → `{"reads": [...], "outputs": [...]}`（去重、按出现顺序）。

    解析不了（语法错误）回两个空表：这里不是解析器，说不出话就不说。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {"reads": [], "outputs": []}
    outputs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _func_name(node.func) in SAVE_FUNCS:
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if looks_like_relative_data_path(arg.value) and arg.value not in outputs:
                        outputs.append(arg.value)
    reads: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if text in outputs or text in reads:
                continue
            if looks_like_relative_data_path(text):
                reads.append(text)
            if len(reads) >= MAX_LITERALS:
                break
    return {"reads": reads, "outputs": outputs}


# ---------------------------------------------------------------- 探路调用（2026-09-26）
#
# 上面那张字面量表回答的是「脚本要**打开**哪些文件」——沙盒的只读回退（`figcapture` 的四个
# 打开入口）救得回它们。另一类调用它救不回：脚本**自己先看一眼**数据在不在、有哪些，
# `glob("run-*.traj")` / `os.listdir()` / `os.path.exists("1/x")` / `Path("d").iterdir()`，
# 以及把路径交给 C++ 的读取器（ovito 的 `import_file`）。它们在沙盒 cwd 下看到的是空沙盒：
# 脚本把「数据不存在」当真，打印一句「未在当前目录下找到」就退出，一张图都不画（用户实报，
# 2026-09-26：`glob.glob('run-*-*[Ll]ongrun.traj')`）。回退**不扩**到这些调用
# （ADR 0047 §不做的事：救不回 C++ 读取器，只会让脚本「以为」数据在）——这里只把它们认成
# **证据**：沙盒默认不够用，首开要问一次运行目录（ADR 0084）。纪律与字面量表相同：只认字符串
# 常量、不执行、不搜同名、项目外不看。

#: 第一个位置实参是一条路径、问的是「它在不在 / 是什么」（或交给不经 Python `open` 的读取器）。
#: `import_file` 是 ovito（ADR 0047 背景里的九个脚本与这次实报都是它）；别的库的同名函数若经
#: Python `open` 读，代价是首开多问一次，不会画错数据。
PATH_PROBE_FUNCS = frozenset(
    {
        "exists",
        "lexists",
        "isfile",
        "isdir",
        "getsize",
        "getmtime",
        "stat",
        "lstat",
        "import_file",
    }
)
#: 列目录：不带实参 = 列 cwd。
DIR_PROBE_FUNCS = frozenset({"listdir", "scandir", "walk"})
#: 模块级 glob：第一个实参是模式。
GLOB_FUNCS = frozenset({"glob", "iglob"})
#: `Path(<常量>)` 上的方法：前一组问这条路径本身（`iterdir` 列它），后一组以它为目录做 glob。
PATH_METHOD_PROBES = frozenset({"exists", "is_file", "is_dir", "stat", "lstat", "iterdir"})
PATH_METHOD_GLOBS = frozenset({"glob", "rglob"})
_PATH_CTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath"})
_GLOB_CHARS = "*?["

#: 一次 `evidence()` 展开全部 glob 模式（两个候选、所有探路目标合计）最多**看**这么多个目录条目
#: （不是匹配数）：够回答「有没有」（找到一个项目内的匹配就停），又不至于让没有匹配的 `**` 在大目录
#: 树里把首开的同步判断（Flask 父进程里）拖上几秒——按单个模式计的话 64 个模式 × 2 个候选就又是
#: 几十万条。用完还没答案就是「判不出」（`unjudged`），不说话。
MAX_GLOB_SCAN = 5000


def _relative_probe_target(text: str) -> bool:
    """字符串常量能不能当「相对 cwd 的探路目标」（路径或 glob 模式）。

    与 `looks_like_relative_data_path` 不同：这里**已经知道**它是 `exists` / `glob` / `listdir`
    的实参，所以不要求数据类扩展名（`exists("1")`、`listdir("runs")` 都算），glob 通配符也放行；
    仍然排除绝对路径、`~`、URL、格式化模板（`{}` / `%`）——动态的说不出话。
    """
    if not text or len(text) > 240 or text != text.strip():
        return False
    if "://" in text or "\n" in text or text.startswith("-"):
        return False
    if any(ch in text for ch in "{}%<>|\"'"):
        return False
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        return False
    if len(text) > 1 and text[1] == ":":
        return False
    return not text.replace("\\", "/").startswith("~")


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _path_receiver(node: ast.expr) -> str | None:
    """`Path("d")` / `Path()` / `Path.cwd()` / `pathlib.Path("d")` → 相对 cwd 的目录（`.` 是 cwd 本身）；
    别的接收者（`Path(__file__).parent`、变量）回 None——那是绝对的，或说不出话。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "cwd":
        owner = func.value
        if _func_name(owner) in _PATH_CTORS and not node.args and not node.keywords:
            return "."
        return None
    if _func_name(func) not in _PATH_CTORS or node.keywords:
        return None
    if not node.args:
        return "."
    if len(node.args) != 1:
        return None
    text = _const_str(node.args[0])
    if text is None or not _relative_probe_target(text) or any(ch in text for ch in _GLOB_CHARS):
        return None
    return text


def _is_glob_module_call(func: ast.expr) -> bool:
    """`glob.glob(...)` / `from glob import glob; glob(...)`——不是随便哪个对象的 `.glob()`
    （`Path(__file__).parent.glob` 起算点是绝对的，变量上的起算点说不出话）。"""
    if isinstance(func, ast.Name):
        return True
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "glob"
    )


#: glob 模块认得的关键字（3.10–3.13）：`root_dir` 拼进目标、两个开关进匹配语义；`dir_fd` 等不认。
_GLOB_KWARGS = frozenset({"pathname", "root_dir", "recursive", "include_hidden"})
#: 列目录调用认得的关键字：只有「列哪」的那一个；`topdown` / `onerror` / `followlinks` 不改「列哪」。
_DIR_KWARGS = {
    "listdir": frozenset({"path"}),
    "scandir": frozenset({"path"}),
    "walk": frozenset({"top", "topdown", "onerror", "followlinks"}),
}


def _join_probe(base: str, tail: str) -> str:
    base = base.replace("\\", "/").rstrip("/")
    return tail if base in ("", ".") else f"{base}/{tail}"


def probe_literals(source: str) -> list[dict]:
    """源码 → 探路目标 `[{"kind": "path" | "dir" | "glob", "target": <相对 cwd 的串>}]`（去重、按出现顺序）。

    `path` 问一条路径在不在；`dir` 列一个目录（`listdir()` 不带实参 → `.`）；`glob` 是一个模式
    （`Path("d").glob("*.x")` 拼成 `d/*.x`，`rglob` 拼成 `d/**/*.x`）。解析不了回空表。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[dict] = []

    def add(kind: str, target: str, **flags: bool) -> None:
        item = {"kind": kind, "target": target, **flags}
        if item not in out and len(out) < MAX_LITERALS:
            out.append(item)

    def kw(node: ast.Call, *names: str) -> ast.expr | None:
        return next((k.value for k in node.keywords if k.arg in names), None)

    def only_kw(node: ast.Call, allowed: frozenset[str]) -> bool:
        # 认不出的关键字实参（`dir_fd=`、`case_sensitive=`、`**opts`）会改变真正看的是哪条路径 /
        # 怎么匹配：说不出话就不判，而不是拿另一条路径去判（Codex 评 #673 P2）
        return all(k.arg in allowed for k in node.keywords)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node.func)
        first = node.args[0] if node.args else None
        receiver = _path_receiver(node.func.value) if isinstance(node.func, ast.Attribute) else None
        if receiver is not None and name in PATH_METHOD_PROBES and not node.args:
            if not node.keywords:
                add("dir" if name == "iterdir" else "path", receiver)
        elif receiver is not None and name in PATH_METHOD_GLOBS:
            pattern = _const_str(first if first is not None else kw(node, "pattern"))
            if (
                pattern is not None
                and _relative_probe_target(pattern)
                and only_kw(node, frozenset({"pattern"}))
            ):
                tail = f"**/{pattern}" if name == "rglob" else pattern
                # pathlib 的 `**` 总是递归；隐藏文件照样匹配（与 glob 模块不同）
                add("glob", _join_probe(receiver, tail), recursive=True, hidden=True)
        elif name in GLOB_FUNCS and _is_glob_module_call(node.func):
            if not only_kw(node, _GLOB_KWARGS):
                continue
            pattern = _const_str(first if first is not None else kw(node, "pathname"))
            if pattern is None or not _relative_probe_target(pattern):
                continue
            # `root_dir=` 换了起算点：常量相对的拼进目标；动态的 / 绝对的说不出话，不判
            root_dir = kw(node, "root_dir")
            if root_dir is not None:
                base = _const_str(root_dir)
                if (
                    base is None
                    or not _relative_probe_target(base)
                    or any(ch in base for ch in _GLOB_CHARS)
                ):
                    continue
                pattern = _join_probe(base, pattern)
            flags = {}
            for key in ("recursive", "include_hidden"):
                value = kw(node, key)
                if value is None:
                    flags[key] = False
                elif isinstance(value, ast.Constant) and isinstance(value.value, bool):
                    flags[key] = value.value
                else:
                    flags = None  # 动态的开关：匹配语义说不出，不判
                    break
            if flags is not None:
                add(
                    "glob",
                    pattern,
                    recursive=flags["recursive"],
                    hidden=flags["include_hidden"],
                )
        elif name in DIR_PROBE_FUNCS:
            if not only_kw(node, _DIR_KWARGS[name]):
                continue
            arg = first if first is not None else kw(node, "path", "top")
            if arg is None:
                add("dir", ".")
            else:
                target = _const_str(arg)
                if target is not None and _relative_probe_target(target):
                    add("dir", target)
        elif name in PATH_PROBE_FUNCS:
            if name != "import_file" and not only_kw(node, frozenset({"path"})):
                continue
            target = _const_str(
                first if first is not None or name == "import_file" else kw(node, "path")
            )
            if (
                target is not None
                and _relative_probe_target(target)
                and not any(ch in target for ch in _GLOB_CHARS)
            ):
                add("path", target)
    return out


#: `listdir()` / `listdir(".")` / `Path().iterdir()`：列 cwd 本身。
_LIST_CWD = {"kind": "dir", "target": "."}


def _probe_hit(base: Path, probe: dict, root: Path, budget: list[int]) -> str:
    """一个探路目标在 `base` 下：`found` / `missing` / `outside` / `unjudged`。

    `outside` = 目标（glob 的话是第一个通配段之前的那段目录）落到项目根之外：**不列、不 stat**——
    准备阶段只看用户交给 Tavotto 的那棵树（与 `_lookup` 同一条纪律）。glob 由 `_glob_hit` 自己走：
    进任何目录之前先判它在不在项目里（通配段匹配到一条指向项目外的软链接时不进去列），看过的
    条目数有上限，用完了是 `unjudged`（判不出就不说话）。
    """
    target = probe["target"].replace("\\", "/")
    if probe["kind"] != "glob":
        cand = base / target
        if not projectenv.within(root, cand):
            return "outside"
        try:
            ok = cand.is_dir() if probe["kind"] == "dir" else cand.exists()
        except OSError:
            ok = False
        return "found" if ok else "missing"
    return _glob_hit(
        base,
        target,
        root,
        recursive=bool(probe.get("recursive", True)),
        hidden=bool(probe.get("hidden", False)),
        budget=budget,
    )


class _ScanBudgetExhausted(Exception):
    pass


def _is_magic(segment: str) -> bool:
    return any(ch in segment for ch in _GLOB_CHARS)


def _glob_hit(
    base: Path, target: str, root: Path, *, recursive: bool, hidden: bool, budget: list[int]
) -> str:
    """有没有至少一个**项目内**的条目匹配 `target`（相对 `base`）——按 glob 模块的匹配语义
    （`fnmatch`；非递归时 `**` 等同 `*`；不以 `.` 开头的段不匹配隐藏名，除非 `hidden`），
    但遍历是自己的：

    * 每进一个目录之前按 realpath 判它在不在项目里（`projectenv.within`）——stdlib `glob` 会先把
      软链接指向的项目外目录列完才吐出匹配，事后过滤已经晚了（Codex 评 #673 P1）；
    * 按**看过的目录条目**扣预算（`budget`，调用方整次 `evidence()` 共用一份），不按匹配数：
      没有匹配的 `**` 也走不远；
    * 同一个真实目录只进一次（`**` 碰到软链接环不会打转）。
    """
    parts = [p for p in target.split("/") if p not in ("", ".")]
    if not parts:
        return "found" if projectenv.within(root, base) and base.is_dir() else "missing"
    fixed = list(itertools.takewhile(lambda p: not _is_magic(p), parts))
    start = base.joinpath(*fixed) if fixed else base
    if not projectenv.within(root, start):
        return "outside"
    rest = parts[len(fixed) :]
    if not rest:
        try:
            return "found" if start.exists() else "missing"
        except OSError:
            return "missing"
    listings: dict[str, list[os.DirEntry]] = {}
    expanded: set[tuple[str, int]] = set()

    def entries(directory: Path) -> list[os.DirEntry]:
        # 每个真实目录只列一次（预算只扣一次），不同的段可以反复看同一份列表
        try:
            real = os.path.realpath(directory)
        except (OSError, ValueError):
            return []
        if real in listings:
            return listings[real]
        listings[real] = []
        if not projectenv.within(root, directory):
            return []
        # 先判再取：预算是整次 `evidence()` 合用的，用完之后别的模式连一个目录都不再打开
        if budget[0] <= 0:
            raise _ScanBudgetExhausted
        out: list[os.DirEntry] = []
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if budget[0] <= 0:
                        raise _ScanBudgetExhausted
                    budget[0] -= 1
                    out.append(entry)
        except OSError:
            return []
        listings[real] = out
        return out

    def is_dir(entry: os.DirEntry) -> bool:
        try:
            return entry.is_dir()
        except OSError:
            return False

    def visible(name: str, segment: str) -> bool:
        return hidden or not name.startswith(".") or segment.startswith(".")

    def walk(directory: Path, i: int) -> bool:
        segment = rest[i]
        last = i == len(rest) - 1
        if not _is_magic(segment):
            nxt = directory / segment
            if not projectenv.within(root, nxt):
                return False
            try:
                if last:
                    return nxt.exists()
                return nxt.is_dir() and walk(nxt, i + 1)
            except OSError:
                return False
        if segment == "**" and recursive:
            # 零层：`**` 什么都不吃，直接看下一段（`**` 在末尾时匹配这个目录下的任何东西）
            if last:
                return any(
                    visible(e.name, "") and projectenv.within(root, Path(e.path))
                    for e in entries(directory)
                )
            # 同一个真实目录在同一段上只展开一次：软链接环不会打转
            key = (os.path.realpath(directory), i)
            if key in expanded:
                return False
            expanded.add(key)
            if walk(directory, i + 1):
                return True
            for entry in entries(directory):
                if is_dir(entry) and visible(entry.name, "") and walk(Path(entry.path), i):
                    return True
            return False
        pattern = "*" if segment == "**" else segment
        for entry in entries(directory):
            if not visible(entry.name, pattern) or not fnmatch.fnmatch(entry.name, pattern):
                continue
            path = Path(entry.path)
            if not projectenv.within(root, path):
                continue  # 匹配到的软链接指向项目外：不算，也不进去列
            if last:
                return True
            if is_dir(entry) and walk(path, i + 1):
                return True
        return False

    try:
        return "found" if walk(start, 0) else "missing"
    except _ScanBudgetExhausted:
        return "unjudged"


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _lookup(base: Path, literal: str, root: Path) -> dict | None:
    """字面量在 `base` 下是不是一个**文件**；是就回 `{path, sha1, size}`；候选落在项目根
    之外（`../../secret.csv`、或项目里一条指向别处的软链接）回 `{"outside": True}`——
    **不 stat、不读、不 hash**：准备阶段只许碰用户交给 Tavotto 的那棵目录树，项目外的
    文件连「存不存在」都不替脚本去看（Codex 评 #459 P1）。"""
    cand = base / literal.replace("\\", "/")
    if not projectenv.within(root, cand):
        return {"outside": True}
    try:
        if not cand.is_file():
            return None
        st = cand.stat()
        return {"path": str(cand), "sha1": _sha1_of(cand), "size": int(st.st_size)}
    except OSError:
        return None


def evidence(script_path: str | os.PathLike, project_root: str | os.PathLike) -> dict:
    """脚本 + 项目根 → 证据结构（可 JSON 化，机器路径只有 `found[*].path`）。

    形状：

        {
          "reads": [...], "outputs": [...],
          "candidates": {
            "script.parent": {"found": {literal: {sha1, size}}, "missing": [...],
                              "outside": [...]},   # 落到项目根之外的字面量：没看、不算找到
            "project.root":  {...}
          },                         # 每个候选另有 "probes": {"found", "missing", "outside"}（探路目标）
          "probes": [target, ...],   # 探路调用的目标（glob 模式 / 列的目录 / 问的路径）
          "same_dir": bool,          # 脚本就在项目根：两个候选是同一个目录
          "verdict": VERDICTS 之一,
          "conflicts": [literal, ...] # 两处都有且内容不同的字面量
        }

    探路目标（`probe_literals`）只在「脚本目录 / 项目根」里**找得到**时说话：只有脚本目录找得到
    → `script_parent`（沙盒的只读回退救不回它们）；只有项目根 → `project_root`；两处都有 →
    `ambiguous`。与打开类字面量的结论指向不同的目录时也是 `ambiguous`——机器不挑。一处都找不到
    时它不说话，结论与没有探路调用时逐字相同。
    """
    root = Path(project_root)
    # 脚本本身也钉在项目根之内再读（realpath；`..` / 指到项目外的软链接都出局）：
    # 在外面就当没有字面量——这里不替调用方读项目外的任何文件（CodeQL #143 / #144）。
    real = projectenv.contained_path(root, script_path)
    if real is None:
        return {
            "reads": [],
            "outputs": [],
            "candidates": {},
            "probes": [],
            "same_dir": False,
            "verdict": VERDICT_NONE,
            "conflicts": [],
        }
    script = Path(real)
    try:
        source = script.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        source = ""
    lits = relative_path_literals(source)
    probes = probe_literals(source)
    parent = script.parent
    try:
        same_dir = parent.resolve(strict=False) == root.resolve(strict=False)
    except OSError:
        same_dir = str(parent) == str(root)
    candidates: dict[str, dict] = {}
    scan_budget = [MAX_GLOB_SCAN]
    for name, base in ((CANDIDATE_SCRIPT_PARENT, parent), (CANDIDATE_PROJECT_ROOT, root)):
        found: dict[str, dict] = {}
        missing: list[str] = []
        outside: list[str] = []
        for lit in lits["reads"]:
            hit = _lookup(base, lit, root)
            if hit is None:
                missing.append(lit)
            elif hit.get("outside"):
                outside.append(lit)
            else:
                found[lit] = {"sha1": hit["sha1"], "size": hit["size"]}
        hits: dict[str, list[str]] = {"found": [], "missing": [], "outside": [], "unjudged": []}
        for probe in probes:
            if probe == _LIST_CWD:
                # 列 cwd 本身不指名任何东西：它只说明「cwd 里有什么要紧」，在哪个候选下都「存在」。
                # 位置按产品的默认假设算——脚本目录（与只读回退、终端里 `python fig.py` 同一个
                # 假设）；记在项目根那档的话，任何不在根上的脚本只要 `listdir()` 一下就成了歧义
                if name == CANDIDATE_SCRIPT_PARENT:
                    hits["found"].append(probe["target"])
                continue
            hits[_probe_hit(base, probe, root, scan_budget)].append(probe["target"])
        candidates[name] = {
            "found": found,
            "missing": missing,
            "outside": outside,
            "probes": hits,
        }
    in_parent = candidates[CANDIDATE_SCRIPT_PARENT]["found"]
    in_root = candidates[CANDIDATE_PROJECT_ROOT]["found"]
    conflicts = sorted(
        lit
        for lit in in_parent
        if lit in in_root and in_parent[lit]["sha1"] != in_root[lit]["sha1"]
    )
    if not lits["reads"]:
        verdict = VERDICT_NONE
    elif same_dir:
        verdict = VERDICT_DEFAULT_OK if in_parent else VERDICT_UNKNOWN
    elif conflicts:
        verdict = VERDICT_AMBIGUOUS
    elif in_parent:
        # 脚本目录找得到的那些，沙盒默认（只读回退）就够；项目根**另有**脚本目录里没有
        # 的文件时两边各有一半——那也是歧义，机器不挑
        only_root = [lit for lit in in_root if lit not in in_parent]
        verdict = VERDICT_AMBIGUOUS if only_root else VERDICT_DEFAULT_OK
    elif in_root:
        verdict = VERDICT_PROJECT_ROOT
    else:
        verdict = VERDICT_UNKNOWN
    verdict = _with_probes(
        verdict,
        same_dir=same_dir,
        in_parent=bool(candidates[CANDIDATE_SCRIPT_PARENT]["probes"]["found"]),
        in_root=bool(candidates[CANDIDATE_PROJECT_ROOT]["probes"]["found"]),
    )
    return {
        "reads": list(lits["reads"]),
        "outputs": list(lits["outputs"]),
        "candidates": candidates,
        "probes": [p["target"] for p in probes],
        "same_dir": same_dir,
        "verdict": verdict,
        "conflicts": conflicts,
    }


def _with_probes(verdict: str, *, same_dir: bool, in_parent: bool, in_root: bool) -> str:
    """打开类字面量的结论 + 探路目标找得到的位置 → 最终结论（规则见 `evidence` 的 docstring）。"""
    if same_dir:
        in_root = False  # 同一个目录：「脚本目录」那一档就是它
    if not in_parent and not in_root:
        return verdict
    if in_parent and in_root:
        return VERDICT_AMBIGUOUS
    wanted = VERDICT_SCRIPT_PARENT if in_parent else VERDICT_PROJECT_ROOT
    # 打开类字面量没说话（none / unknown），或说的与探路指向同一个目录（脚本目录的字面量沙盒
    # 回退够用、在脚本目录里跑同样够用）→ 听探路的；指向另一个目录 → 歧义
    agrees = {VERDICT_NONE, VERDICT_UNKNOWN, wanted}
    if wanted == VERDICT_SCRIPT_PARENT:
        agrees.add(VERDICT_DEFAULT_OK)
    return wanted if verdict in agrees else VERDICT_AMBIGUOUS


# ---------------------------------------------------------------- 数据绑定修订（U09，ADR 0070）

BINDING_VERSION = 1


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def binding_for(script_path: str | os.PathLike, project_root: str | os.PathLike, mode: str) -> dict:
    """预检那一刻的**数据绑定**：按已决定 / 默认的 cwd 档，脚本里那些相对路径字面量各自会读到哪一份文件，
    内容是什么（sha256）。回执把它与执行时观察到的输入逐条对（`receipt.ExecutionReceipt.binding_check`），
    准备接口在起会话之前再算一次——不同就是「预检之后数据变了」，旧计划作废（FO30）。

    `mode` 是 `execspec.CWD_MODES` 之一：`sandbox` / `project` 的相对读落在**脚本目录**（沙盒默认的只读回退
    也是回到那里），`project_root` 落在项目根。`expected` 的键是**相对项目根**的 POSIX 路径（与观察器记的
    同一坐标），值是 sha256；找不到 / 项目外的字面量不进 `expected`（列在 `missing` / `outside`，如实）。
    `revision` = 上面这些的规范化摘要（换一个字节就换一个修订）。**只读、不猜、不搜索**（与 `evidence()` 同一
    条纪律）。
    """
    root = Path(project_root)
    real = projectenv.contained_path(root, script_path)
    empty = {
        "binding_version": BINDING_VERSION,
        "mode": mode,
        "base": None,
        "expected": {},
        "missing": [],
        "outside": [],
        "revision": "",
    }
    if real is None:
        return {**empty, "revision": _binding_revision(empty)}
    script = Path(real)
    try:
        source = script.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        source = ""
    lits = relative_path_literals(source)
    base_name = CANDIDATE_PROJECT_ROOT if mode == "project_root" else CANDIDATE_SCRIPT_PARENT
    base = root if base_name == CANDIDATE_PROJECT_ROOT else script.parent
    expected: dict[str, str] = {}
    missing: list[str] = []
    outside: list[str] = []
    try:
        real_root = root.resolve(strict=False)
    except OSError:
        real_root = root
    for lit in lits["reads"]:
        hit = _lookup(base, lit, root)
        if hit is None:
            missing.append(lit)
            continue
        if hit.get("outside"):
            outside.append(lit)
            continue
        cand = Path(hit["path"])
        try:
            rel = PurePosixPath(cand.resolve(strict=False).relative_to(real_root).as_posix())
            expected[str(rel)] = _sha256_of(cand)
        except (OSError, ValueError):
            missing.append(lit)
    out = {
        "binding_version": BINDING_VERSION,
        "mode": mode,
        "base": base_name,
        "expected": dict(sorted(expected.items())),
        "missing": sorted(missing),
        "outside": sorted(outside),
    }
    out["revision"] = _binding_revision(out)
    return out


def _binding_revision(binding: dict) -> str:
    payload = {
        "binding_version": binding.get("binding_version"),
        "mode": binding.get("mode"),
        "base": binding.get("base"),
        "expected": sorted((binding.get("expected") or {}).items()),
        "missing": sorted(binding.get("missing") or []),
        "outside": sorted(binding.get("outside") or []),
    }
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
