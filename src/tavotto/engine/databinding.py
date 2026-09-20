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

判据刻意窄：只认字符串常量（f-string / `%` / `.format` / glob 模式是动态的，一律不算），
只认带**数据类扩展名**或带目录分隔符的相对路径，存图调用（`discover.SAVE_FUNCS`）的
实参归输出不归输入。窄的代价是「少问一次」——那时走默认，与今天一样；宽的代价是
「多问一次」——用户每个项目多点一下。两边都不会把错的数据画出来。

纯标准库；Flask 父进程 import 链上（被 `workdir` / `preparation` 用）。
"""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

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
#: 找不到；`none` = 脚本里没有像相对数据路径的字面量。
VERDICT_DEFAULT_OK = "default_ok"
VERDICT_PROJECT_ROOT = "project_root"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_UNKNOWN = "unknown"
VERDICT_NONE = "none"
VERDICTS = (
    VERDICT_DEFAULT_OK,
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
    if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
        return False  # 绝对路径（含 Windows 盘符）：明确指名的位置，不在本模块的问题里
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


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _lookup(base: Path, literal: str) -> dict | None:
    """字面量在 `base` 下是不是一个**文件**；是就回 `{path, sha1, size}`。"""
    cand = base / literal.replace("\\", "/")
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
            "script.parent": {"found": {literal: {sha1, size}}, "missing": [...]},
            "project.root":  {...}
          },
          "same_dir": bool,          # 脚本就在项目根：两个候选是同一个目录
          "verdict": VERDICTS 之一,
          "conflicts": [literal, ...] # 两处都有且内容不同的字面量
        }
    """
    script = Path(script_path)
    root = Path(project_root)
    try:
        source = script.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        source = ""
    lits = relative_path_literals(source)
    parent = script.parent
    try:
        same_dir = parent.resolve(strict=False) == root.resolve(strict=False)
    except OSError:
        same_dir = str(parent) == str(root)
    candidates: dict[str, dict] = {}
    for name, base in ((CANDIDATE_SCRIPT_PARENT, parent), (CANDIDATE_PROJECT_ROOT, root)):
        found: dict[str, dict] = {}
        missing: list[str] = []
        for lit in lits["reads"]:
            hit = _lookup(base, lit)
            if hit is None:
                missing.append(lit)
            else:
                found[lit] = {"sha1": hit["sha1"], "size": hit["size"]}
        candidates[name] = {"found": found, "missing": missing}
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
    return {
        "reads": list(lits["reads"]),
        "outputs": list(lits["outputs"]),
        "candidates": candidates,
        "same_dir": same_dir,
        "verdict": verdict,
        "conflicts": conflicts,
    }
