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
* **文件证据（硬条件）**：静态上看到「会存位图」不等于这次真的存了（只定义没调用的 helper、没走到的分支）。
  所以只有**字面量输出路径解析出的文件在运行后确实存在，且 mtime 不早于本次运行开始**（`run_started_at`，
  `prepsession` 传入本次尝试的创建时间）才出提示。相对路径按本次运行**实际的 cwd** 解析：`project` /
  `project_root` 模式是脚本目录 / 项目根，沙盒模式（默认）是这次尝试的会话沙盒目录（`sandbox_dir`，取自
  执行结果 `captured["sandbox"]`，即 worker 的 `spec.sandbox`；沙盒随 worker 缓存目录存在，不在每次运行后清掉）。
  沙盒里有新文件 → 给原因句，但 `in_project` 恒为 false（素材库看不见沙盒）。没有任何文件证据——动态路径、
  未知运行目录、拿不到沙盒目录、没传开始时间、文件不存在或是旧文件——一律不出位图提示，回到普通「没出图」。
  取舍：宁可漏说一个真 Pillow 脚本（动态路径写出的图），也不对「根本没写图」的脚本说「它画成图片了」。
* **盲点（写在明处）**：跟进被截断 / 有读不了的本地模块 / 有动态 import 时，反面判据看不全，
  一律不提示；`.save` 的接收者只按「不是已 import 的非 PIL 模块」判（`np.save` / `torch.save` 排除），
  不做类型推断，所以一个导入了 PIL 又对自家对象调 `.save("x.png")` 的脚本会被算上。
  第二种文件证据（路径运行时才定）：本次运行开始后项目素材盘点里新出现 / 被改写的文件（`_new_inventory_file`），
  给出时 hint 带项目相对路径 `path`。plotly（`write_image`）与 ROOT（`SaveAs` / `Print`）同样覆盖，另认 pdf / svg / eps；
  这两个库在动态 import 下仍可提示（ADR 0139 修订 2026-10-21）。

纯标准库；被 `prepsession` import（Flask 父进程 import 链）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from . import discover, importscan, scanbudget

# 素材盘点的可见性判据：单一出处（`iter_assets` 同一份），不在这里抄第二份
from .project_refresh import PDF_EXT as _PDF_EXT, is_inventoried, iter_assets
from .workdir import MODE_PROJECT, MODE_PROJECT_ROOT, MODE_SANDBOX

#: 会让 Tavotto 能捕获到 figure 的家族；脚本或其本地模块 import 了其中任何一个都不提示。
MATPLOTLIB_FAMILY = frozenset(
    {"matplotlib", "pylab", "seaborn", "plotnine", "mpl_toolkits", "scienceplots", "proplot"}
)

LIB_PILLOW = "pillow"
LIB_OPENCV = "opencv"
LIB_IMAGEIO = "imageio"
LIB_SKIMAGE = "skimage"
LIB_PLOTLY = "plotly"
LIB_ROOT = "root"
LIBRARIES = (LIB_PILLOW, LIB_OPENCV, LIB_IMAGEIO, LIB_SKIMAGE, LIB_PLOTLY, LIB_ROOT)

#: 「对象方法」式保存：方法名 → (库, 文件里要 import 的顶层包, 接收者若是已 import 的模块则必须属于这些顶层包)。
#: `write_html` 不在表里——它写的是网页不是图片；plotly 的 `write_image` 才算（svg / pdf 由扩展名判据排除）。
_SAVE_METHODS: dict[str, tuple[str, str, frozenset[str]]] = {
    "save": (LIB_PILLOW, "PIL", frozenset({"PIL"})),
    "write_image": (LIB_PLOTLY, "plotly", frozenset({"plotly"})),
    "SaveAs": (LIB_ROOT, "ROOT", frozenset({"ROOT"})),
    "Print": (LIB_ROOT, "ROOT", frozenset({"ROOT"})),
}

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
    "plotly.io.write_image": LIB_PLOTLY,
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


#: plotly / ROOT 的输出还常是矢量（PDF / SVG / EPS）：它们同样是「非 Matplotlib 产出」，字面量扩展名在这里就算存图。
_VECTOR_EXTS = frozenset({".pdf", ".svg", ".eps"})
_EXTS_BY_LIB: dict[str, frozenset[str]] = {
    LIB_PLOTLY: _IMAGE_EXTS | _VECTOR_EXTS,
    LIB_ROOT: _IMAGE_EXTS | _VECTOR_EXTS,
}


def _exts_of(lib: str) -> frozenset[str]:
    return _EXTS_BY_LIB.get(lib, _IMAGE_EXTS)


def _top(name: str) -> str:
    return name.split(".", 1)[0]


class _FileScan(ast.NodeVisitor):
    """一个文件里的 import 绑定 + 保存位图的调用 + 是否 import 了 matplotlib 家族。"""

    def __init__(self) -> None:
        #: 本地名 → 它绑定的点分全名（`I` → `PIL.Image`；`imwrite` → `cv2.imwrite`）
        self.bound: dict[str, str] = {}
        self.imports_pil = False
        self.imports_tops: set[str] = set()
        self.imports_mpl = False
        #: 每处「保存位图」调用的 (库, 路径实参)：路径是字面量字符串原文；不是纯字符串字面量为 None。
        #: 库与路径成对记，后面挑「新写出的文件」时不能把 A 库的路径安到 B 库头上
        self.saves: list[tuple[str, str | None]] = []
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
        self.imports_tops.add(top)
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
                pos = _FUNC_FILE_POS.get(full, 0)
                if _path_arg_is_image(call, _exts_of(_SAVE_FUNCS[full]), pos):
                    self.saves.append((_SAVE_FUNCS[full], _literal_path(call, pos)))
                continue
            if not isinstance(func, ast.Attribute) or func.attr not in _SAVE_METHODS:
                continue
            lib, needs, tops = _SAVE_METHODS[func.attr]
            if (
                needs in self.imports_tops
                and not self._receiver_is_foreign_module(func.value, tops)
                and _path_arg_is_image(call, _exts_of(lib))
            ):
                self.saves.append((lib, _literal_path(call)))

    def _receiver_is_foreign_module(self, value: ast.expr, own_tops: frozenset[str]) -> bool:
        """`np.save` / `torch.save` / `joblib.dump` 之类：接收者是 import 进来的、不属于该库（`own_tops`）的名字。"""
        cur = value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self.bound:
            return _top(self.bound[cur.id]) not in own_tops
        return False


#: 函数形式里「输出文件」不是第一个位置参数的：`plotly.io.write_image(fig, file, ...)` 的 `file` 是第二个；
#: `to_image` 不在表里：它返回字节、不写文件，登记它会让任何新图被安到 plotly 头上（Codex #1271）。表外一律第一个。
_FUNC_FILE_POS: dict[str, int | None] = {
    "plotly.io.write_image": 1,
}


def _path_arg(call: ast.Call, pos: int | None = 0) -> ast.expr | None:
    if pos is not None and len(call.args) > pos:
        return call.args[pos]
    for kw in call.keywords:
        if kw.arg in ("fp", "fname", "filename", "uri", "path", "im_path", "file"):
            return kw.value
    return None


def _literal_path(call: ast.Call, pos: int | None = 0) -> str | None:
    node = _path_arg(call, pos)
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


def _inventory_rel(path: str | None, root: Path, script: str, cwd_mode: str | None) -> str | None:
    """字面量相对路径（不是绝对 / `~` / 带 `..` 往上跳 / Windows 盘符）按运行目录解析成**项目内相对路径**后，
    会被素材盘点（`project_refresh.is_inventoried`，与 `iter_assets` 同一判据）列出；是则返回那个项目相对路径（posix），否则 None。

    运行目录：`project_root` 相对项目根，`project` 相对脚本所在目录（沙盒 / 未知：写进会话沙盒，看不见）。
    盲点（写在明处）：同 stem PDF 压位图只看**分析时磁盘当前状态**——脚本之后才生成 / 删除同名 PDF 时可能偏差。"""
    if not path or path.startswith(("/", "\\", "~")) or (len(path) > 1 and path[1] == ":"):
        return None
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts or ".." in parts:
        return None
    if cwd_mode == MODE_PROJECT_ROOT:
        base: list[str] = []
    elif cwd_mode == MODE_PROJECT:
        base = [p for p in script.replace("\\", "/").split("/") if p and p != "."][:-1]
    else:
        return None
    rel = "/".join([*base, *parts])
    if not is_inventoried(rel, pdf_twin=_has_pdf_twin(root, rel)):
        return None
    return None if _crosses_symlink_dir(root, rel) else rel


#: 文件系统时间戳粗糙（FAT / HFS+ 整秒）：mtime 允许比运行开始早这么多秒仍算「本次写的」
_MTIME_SLACK_S = 1.0
_WIN_ABS = re.compile(r"^([A-Za-z]:|[\\/]{2})")


def _fs_path(
    path: str | None,
    root: Path,
    script: str,
    cwd_mode: str | None,
    sandbox_dir: str | None = None,
) -> Path | None:
    """字面量输出路径 → 磁盘上的绝对路径；解析不了（动态 / 沙盒或未知运行目录下的相对路径 / 别的系统的路径）None。"""
    if not path:
        return None
    if path.startswith("~") or Path(path).is_absolute():
        return Path(path).expanduser()
    if _WIN_ABS.match(path):
        return None  # Windows 盘符 / UNC：在本机上无从核实
    if cwd_mode == MODE_PROJECT_ROOT:
        return root / path
    if cwd_mode == MODE_PROJECT:
        script_dir = [p for p in script.replace("\\", "/").split("/") if p and p != "."][:-1]
        return root.joinpath(*script_dir, path)
    if cwd_mode == MODE_SANDBOX and sandbox_dir:
        return Path(sandbox_dir) / path  # 沙盒模式：脚本的 cwd 就是这次尝试的会话沙盒目录
    return None  # 未知运行目录 / 沙盒目录拿不到：无从核实


def _written_since(fs: Path | None, run_started_at: float | None) -> bool:
    """文件此刻存在，且 mtime 不早于本次运行开始（旧文件不算本次写的）。"""
    if fs is None or run_started_at is None:
        return False
    try:
        st = fs.stat()
        return fs.is_file() and st.st_mtime >= run_started_at - _MTIME_SLACK_S
    except (OSError, ValueError):
        return False


def _crosses_symlink_dir(root: Path, rel: str) -> bool:
    """`rel` 的任一级目录分量按**磁盘当前状态**是符号链接（或解析后跑出项目根）→ True。
    `iter_assets` 的 os.walk 不跟链接，`plots -> /outside` 下的图素材库列不出，不能指向素材库。"""
    dirs = rel.replace("\\", "/").split("/")[:-1]
    cur = root
    try:
        for d in dirs:
            cur = cur / d
            if cur.is_symlink():
                return True
        cur.resolve().relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return True
    return False


#: 动态 import（`import_module(拼出来的名字)`）下仍可提示的库：ROOT / RooFit 脚本常按拟合结果动态加载自己生成的参数模块
#: （真实样本 fit_mass.py），这类脚本几乎不会在动态模块里再装 matplotlib；Pillow 等位图库保持「看不全就不说」。
_DYNAMIC_TOLERANT = frozenset({LIB_ROOT, LIB_PLOTLY})

#: 运行时文件证据的盘点预算：只是「找本次新写的文件」，别让大项目拖住 `report`
_INVENTORY_SECONDS = 3.0


def _allowed_exts(libs: list[str]) -> frozenset[str]:
    """静态上出现过的库所认的输出扩展名并集（只有位图库的脚本，新出现的 PDF 不算它写的）。"""
    out: frozenset[str] = frozenset()
    for lib in set(libs):
        out |= _exts_of(lib)
    return out


def _new_inventory_file(
    root: Path, run_started_at: float | None, exts: frozenset[str]
) -> str | None:
    """素材盘点（`iter_assets`，带时间预算，不新开遍历）里 mtime 不早于本次运行开始的文件里最新的那个的项目相对路径；
    没有 / 没传开始时间 / 读不了 → None。盘点只认 png / jpg / tiff / pdf，svg / eps 等盘点外的格式只能走字面量路径证据。"""
    if run_started_at is None:
        return None
    best: tuple[float, str] | None = None
    try:
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_seconds=_INVENTORY_SECONDS))
        for path, _kind in iter_assets(root, budget=budget):
            if path.suffix.lower() not in exts:
                continue
            try:
                mtime = path.stat().st_mtime
                rel = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            if mtime >= run_started_at - _MTIME_SLACK_S and (best is None or mtime > best[0]):
                best = (mtime, rel)
    except (OSError, ValueError):
        return None
    return best[1] if best else None


def _path_arg_is_image(
    call: ast.Call, exts: frozenset[str] = _IMAGE_EXTS, pos: int | None = 0
) -> bool:
    """保存目标（按该 API 的签名取位置，多数是第一个实参）的字面量扩展名不在该库的图片 / 图形格式里就 False；不是字面量按「可能是」True。"""
    node = _path_arg(call, pos)
    ext = _literal_ext(node)
    return ext is None or ext in exts


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


def detect(
    root: str | Path,
    script: str,
    cwd_mode: str | None = None,
    run_started_at: float | None = None,
    sandbox_dir: str | None = None,
) -> dict | None:
    """脚本（含有界跟进的本地模块）是不是用位图库自己写了图片文件；是则回 `{"kind": "raster_script",
    "library": <LIBRARIES 之一>, "in_project": bool, "path": 项目相对路径 | None}`，否则 None（含「看不全」）。读不了 / 解析不了一律 None，不抛。

    **文件证据**：至少一处字面量输出路径解析出的文件此刻存在且 mtime 不早于 `run_started_at`（本次运行开始，
    epoch 秒）才回提示；没传 `run_started_at` 也按没有证据（None）。沙盒模式（默认）下相对路径按
    `sandbox_dir`（这次尝试实际使用的会话沙盒目录，脚本的 cwd）解析：沙盒里有新文件 → 给原因句，但
    `in_project` 恒为 false（素材库看不见沙盒）；沙盒目录拿不到才没有证据。

    `in_project`：**能确定**那张图落在素材库盘点得到的地方才 True——运行目录模式是 `project` / `project_root`
    （沙盒模式下相对路径写进会话沙盒，素材库看不见；模式未知也算不确定），且至少有一处保存的路径是字面量相对
    路径、扩展名属于素材盘点集（`project_refresh.IMG_EXT`）。绝对路径 / `~` / `..` / 动态路径 / bmp·gif·webp 等
    盘点不认的格式一律 False——原因句照样成立，只是不指向素材库。"""
    root_p = Path(root)
    try:
        scan = importscan.scan(root_p, script)
    except (OSError, ValueError, RecursionError):
        return None
    if scan.truncated or scan.problems:
        return None  # 反面判据（有没有 matplotlib）看不全：不说
    saves: list[tuple[str, str | None]] = []
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
        saves += fs.saves
    # 扫描只读 .py；编译扩展 / 命名空间包的内部看不见，已在 scan 里记入 problems 或不进 files，这里不再猜
    libs = [lib for lib, _p in saves]
    if not libs:
        return None
    if scan.dynamic and not set(libs) <= _DYNAMIC_TOLERANT:
        return None  # 动态 import 可能装进没扫过的 matplotlib：位图库脚本看不全就不说
    # 多个库时取先出现的；顺序按 LIBRARIES 稳定化，保证同一份脚本总给同一个答案
    fresh = [
        (lib, p)
        for lib, p in saves
        if _written_since(_fs_path(p, root_p, script, cwd_mode, sandbox_dir), run_started_at)
    ]
    if not fresh:
        # 第二种证据（运行时才定的输出路径）：本次运行开始后，项目素材盘点里新出现 / 被改写的图片文件
        found = _new_inventory_file(root_p, run_started_at, _allowed_exts(libs))
        if found is None:
            return (
                None  # 没有这次运行写出文件的证据：不说（未调用的 helper / 没走到的分支 / 旧文件）
            )
        # 新文件是哪个库写的（ADR 0139 证据规则：证据缺失一律沉默，宁可少说不说假话）：候选只有「输出路径运行时才定」
        # 的那几处保存，加上字面量路径按项目根 / 脚本目录解析后**正是**这个新文件的那几处（沙盒模式下字面量是对沙盒核的，
        # 文件却写进了项目，这条字面量就是最直接的证据），且扩展名得是该库认的。其余字面量上面已核过不是本次写的。
        # 候选库唯一才归属；PNG 这类两个库都认、而静态上分不出哪个真被调用（未调用的 helper 也算候选）→ 不说
        suffix = Path(found).suffix.lower()
        target = root_p / found
        candidates = {
            lib
            for lib, p in saves
            if suffix in _exts_of(lib)
            and (
                p is None
                or target
                in (
                    _fs_path(p, root_p, script, MODE_PROJECT_ROOT),
                    _fs_path(p, root_p, script, MODE_PROJECT),
                )
            )
        }
        if len(candidates) != 1:
            return None
        return {
            "kind": "raster_script",
            "library": next(iter(candidates)),
            "in_project": True,
            "path": found,
        }
    # 库与路径成对：只在「确有新文件」的那几处保存里挑库，路径也取自同一库的新文件
    fresh_libs = {lib for lib, _p in fresh}
    for lib in LIBRARIES:
        if lib not in fresh_libs:
            continue
        rel: str | None = None
        for fl, p in fresh:
            if fl != lib:
                continue
            rel = _inventory_rel(p, root_p, script, cwd_mode)
            if rel is not None:
                break
        return {"kind": "raster_script", "library": lib, "in_project": rel is not None, "path": rel}
    return None
