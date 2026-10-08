"""「跑完没出图」的原因提示：脚本是用位图库直接画成图片的，不是 Matplotlib 图。

用户把 Pillow / OpenCV 画图脚本交给 Tavotto 时，捕获不到任何 Matplotlib figure，卡片原来只说
「没有捕获到图」。本模块只回答一个问题——**这个脚本是不是自己把图片写成了文件**——让卡片能说出
原因、给出路（图片文件在素材库里，想逐元素编辑要改成 Matplotlib）。它不改捕获逻辑、不改任何结果，
只在 `prepsession` 已经判出 `execution_finished_no_figure` 之后被问一次。

判据（AST，纯静态，不执行脚本、不 import 用户模块）：

* **主语**：脚本本身 + `importscan.scan` 有界跟进到的本地模块（同一份 `files`，同一组上限）；
  绑定的名字按每个文件自己的 import 解析（`import PIL.Image as I` / `from PIL import Image` /
  `from skimage import io` / `import imageio.v3 as iio`）。
* **正面**：这些文件里有一处调用是「保存位图」——`cv2.imwrite`、`imageio.imwrite/imsave/mimsave/...`、
  `skimage.io.imsave`，或者在导入了 `PIL` 的文件里对非模块对象调用 `.save(...)`（Pillow 的 `Image.save`）。
  路径实参若是字面量且扩展名不是图片格式（`.npy` / `.pkl` …）则不算；不是字面量（变量 / f-string 的
  动态部分）按「可能是图片」算。
* **反面（宁可不说，不说错）**：脚本或它跟进到的本地模块 import 了 matplotlib 家族（matplotlib /
  pylab / seaborn / plotnine / mpl_toolkits / scienceplots / proplot）——哪怕没建 figure 也不提示：
  那种脚本没出图多半是别的原因（没调 plt、`Agg` 之外的怪后端……），说「是 Pillow 画的」会误导。
  只 import `PIL` 读图、再用 matplotlib 画的脚本同理不提示。
* **盲点（写在明处）**：跟进被截断 / 有读不了的本地模块 / 有动态 import 时，反面判据看不全，
  一律不提示；`.save` 的接收者只按「不是已 import 的非 PIL 模块」判（`np.save` / `torch.save` 排除），
  不做类型推断，所以一个导入了 PIL 又对自家对象调 `.save("x.png")` 的脚本会被算上。
  本模块不知道脚本**实际**写出了哪个文件（引擎没有「脚本写了哪些文件」的记录），因此不指认文件名。

纯标准库；被 `prepsession` import（Flask 父进程 import 链）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from . import discover, importscan

# 素材盘点的可见性判据：单一出处（`iter_assets` 同一份），不在这里抄第二份
from .project_refresh import PDF_EXT as _PDF_EXT, is_inventoried
from .workdir import MODE_PROJECT, MODE_PROJECT_ROOT

#: 会让 Tavotto 能捕获到 figure 的家族；脚本或其本地模块 import 了其中任何一个都不提示。
MATPLOTLIB_FAMILY = frozenset(
    {"matplotlib", "pylab", "seaborn", "plotnine", "mpl_toolkits", "scienceplots", "proplot"}
)

LIB_PILLOW = "pillow"
LIB_OPENCV = "opencv"
LIB_IMAGEIO = "imageio"
LIB_SKIMAGE = "skimage"
LIBRARIES = (LIB_PILLOW, LIB_OPENCV, LIB_IMAGEIO, LIB_SKIMAGE)

#: 保存位图的函数（解析后的点分全名）→ 库。
_SAVE_FUNCS: dict[str, str] = {
    "cv2.imwrite": LIB_OPENCV,
    "imageio.imwrite": LIB_IMAGEIO,
    "imageio.imsave": LIB_IMAGEIO,
    "imageio.mimsave": LIB_IMAGEIO,
    "imageio.mimwrite": LIB_IMAGEIO,
    "imageio.v2.imwrite": LIB_IMAGEIO,
    "imageio.v2.imsave": LIB_IMAGEIO,
    "imageio.v2.mimsave": LIB_IMAGEIO,
    "imageio.v2.mimwrite": LIB_IMAGEIO,
    "imageio.v3.imwrite": LIB_IMAGEIO,
    "skimage.io.imsave": LIB_SKIMAGE,
}

#: 路径字面量的扩展名：这些是位图（含 GIF 动图）；字面量扩展名不在表里就不算「存图」。
_IMAGE_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
        ".tif",
        ".tiff",
        ".webp",
        ".ppm",
        ".pgm",
        ".pbm",
        ".ico",
    }
)


def _top(name: str) -> str:
    return name.split(".", 1)[0]


class _FileScan(ast.NodeVisitor):
    """一个文件里的 import 绑定 + 保存位图的调用 + 是否 import 了 matplotlib 家族。"""

    def __init__(self) -> None:
        #: 本地名 → 它绑定的点分全名（`I` → `PIL.Image`；`imwrite` → `cv2.imwrite`）
        self.bound: dict[str, str] = {}
        self.imports_pil = False
        self.imports_mpl = False
        self.libs: list[str] = []
        #: 每处「保存位图」调用的路径实参：字面量字符串原文；不是纯字符串字面量为 None
        self.paths: list[str | None] = []
        self._save_calls: list[ast.Call] = []

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            top = _top(a.name)
            self._note(top)
            if a.asname:
                self.bound[a.asname] = a.name
            else:
                self.bound[top] = top  # `import a.b.c` 绑定的是 `a`
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._note(_top(node.module))
            for a in node.names:
                if a.name != "*":
                    self.bound[a.asname or a.name] = f"{node.module}.{a.name}"
        self.generic_visit(node)

    def _note(self, top: str) -> None:
        if top == "PIL":
            self.imports_pil = True
        elif top in MATPLOTLIB_FAMILY:
            self.imports_mpl = True

    def visit_Call(self, node: ast.Call) -> None:
        self._save_calls.append(node)
        self.generic_visit(node)

    def _resolve(self, func: ast.expr) -> str | None:
        """`a.b.c` / `name` → 按 import 绑定展开的点分全名；不是纯属性链返回 None。"""
        parts: list[str] = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name) or cur.id not in self.bound:
            return None
        return ".".join([self.bound[cur.id], *reversed(parts)])

    def finish(self) -> None:
        """import 全部收齐之后再判调用（调用可能写在 import 之前的函数体里）。"""
        for call in self._save_calls:
            func = call.func
            full = self._resolve(func)
            if full is not None and full in _SAVE_FUNCS:
                if _path_arg_is_image(call):
                    self.libs.append(_SAVE_FUNCS[full])
                    self.paths.append(_literal_path(call))
                continue
            if (
                self.imports_pil
                and isinstance(func, ast.Attribute)
                and func.attr == "save"
                and not self._receiver_is_foreign_module(func.value)
                and _path_arg_is_image(call)
            ):
                self.libs.append(LIB_PILLOW)
                self.paths.append(_literal_path(call))

    def _receiver_is_foreign_module(self, value: ast.expr) -> bool:
        """`np.save` / `torch.save` / `joblib.dump` 之类：接收者是 import 进来的、不是 PIL 的名字。"""
        cur = value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self.bound:
            return _top(self.bound[cur.id]) != "PIL"
        return False


def _path_arg(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg in ("fp", "fname", "filename", "uri", "path", "im_path"):
            return kw.value
    return None


def _literal_path(call: ast.Call) -> str | None:
    node = _path_arg(call)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _has_pdf_twin(root: Path, rel: str) -> bool:
    """磁盘上此刻同目录是否有同 stem 的 PDF（`iter_assets` 里矢量版压掉位图那条）。读不了按没有。"""
    target = root / rel
    try:
        return any(
            p.stem == target.stem and p.suffix.lower() in _PDF_EXT for p in target.parent.iterdir()
        )
    except OSError:
        return False


def _lands_in_inventory(path: str | None, root: Path, script: str, cwd_mode: str | None) -> bool:
    """字面量相对路径（不是绝对 / `~` / 带 `..` 往上跳 / Windows 盘符）按运行目录解析成**项目内相对路径**后，
    会被素材盘点（`project_refresh.is_inventoried`，与 `iter_assets` 同一判据）列出。

    运行目录：`project_root` 相对项目根，`project` 相对脚本所在目录（沙盒 / 未知：写进会话沙盒，看不见）。
    盲点（写在明处）：同 stem PDF 压位图只看**分析时磁盘当前状态**——脚本之后才生成 / 删除同名 PDF 时可能偏差。"""
    if not path or path.startswith(("/", "\\", "~")) or (len(path) > 1 and path[1] == ":"):
        return False
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts or ".." in parts:
        return False
    if cwd_mode == MODE_PROJECT_ROOT:
        base: list[str] = []
    elif cwd_mode == MODE_PROJECT:
        base = [p for p in script.replace("\\", "/").split("/") if p and p != "."][:-1]
    else:
        return False
    rel = "/".join([*base, *parts])
    return is_inventoried(rel, pdf_twin=_has_pdf_twin(root, rel))


def _path_arg_is_image(call: ast.Call) -> bool:
    """保存目标（这几个 API 的第一个实参）的字面量扩展名不是位图就 False；不是字面量按「可能是」True。"""
    node = _path_arg(call)
    ext = _literal_ext(node)
    return ext is None or ext in _IMAGE_EXTS


def _literal_ext(node: ast.expr | None) -> str | None:
    """字符串字面量 / f-string 末尾常量段的扩展名（小写，带点）；动态的返回 None。"""
    text: str | None = None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            text = last.value
    if text is None:
        return None
    suffix = Path(text).suffix.lower()
    return suffix or None


def detect(root: str | Path, script: str, cwd_mode: str | None = None) -> dict | None:
    """脚本（含有界跟进的本地模块）是不是用位图库自己写了图片文件；是则回 `{"kind": "raster_script",
    "library": <LIBRARIES 之一>, "in_project": bool}`，否则 None（含「看不全」）。读不了 / 解析不了一律 None，不抛。

    `in_project`：**能确定**那张图落在素材库盘点得到的地方才 True——运行目录模式是 `project` / `project_root`
    （沙盒模式下相对路径写进会话沙盒，素材库看不见；模式未知也算不确定），且至少有一处保存的路径是字面量相对
    路径、扩展名属于素材盘点集（`project_refresh.IMG_EXT`）。绝对路径 / `~` / `..` / 动态路径 / bmp·gif·webp 等
    盘点不认的格式一律 False——原因句照样成立，只是不指向素材库。"""
    root_p = Path(root)
    try:
        scan = importscan.scan(root_p, script)
    except (OSError, ValueError, RecursionError):
        return None
    if scan.truncated or scan.problems or scan.dynamic:
        return None  # 反面判据（有没有 matplotlib）看不全：不说
    libs: list[str] = []
    paths: list[str | None] = []
    for rel in scan.files:
        text, problem = discover.read_source(root_p / rel)  # PEP 263 / BOM；解不出就不说
        if problem is not None or text is None:
            return None
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return None
        fs = _FileScan()
        fs.visit(tree)
        if fs.imports_mpl:
            return None
        fs.finish()
        libs += fs.libs
        paths += fs.paths
    # 扫描只读 .py；编译扩展 / 命名空间包的内部看不见，已在 scan 里记入 problems 或不进 files，这里不再猜
    if not libs:
        return None
    # 多个库时取先出现的；顺序按 LIBRARIES 稳定化，保证同一份脚本总给同一个答案
    in_project = any(_lands_in_inventory(p, root_p, script, cwd_mode) for p in paths)
    for lib in LIBRARIES:
        if lib in libs:
            return {"kind": "raster_script", "library": lib, "in_project": in_project}
    return None
