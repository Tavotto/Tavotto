"""缺的 import 名 → 可信的 PyPI 发行包名（Compatibility Bridge Session 7B）。

`ModuleNotFoundError: No module named 'PIL'` 里的 `PIL` **不是**能拿去安装的
东西。import 名与 distribution 名是两套 namespace：

    PIL      → Pillow
    cv2      → opencv-python
    sklearn  → scikit-learn
    skimage  → scikit-image
    yaml     → PyYAML

「一键安装」的本质是**从 package index 下载并执行代码**，所以安装目标必须来自
可信解析，不能来自「traceback 里那个字符串」。本模块是这条解析的唯一出处，
三档可信度，**没有第四档**：

    project_declared  项目自己的 requirements/pyproject 里声明过 → 用它的版本约束
    curated           Tavotto 维护的一张小而高质量的科研包映射
    user_specified    用户自己输入的包名（仍要过严格语法校验）

解析不到就是 `None`：**绝不允许「import 名当包名试试看」**。`import my_lab_tools`
→ `pip install my_lab_tools` 是一条真实的供应链攻击路径（抢注同名包），
由 `tests/test_dependency_repair.py::test_an_unknown_import_is_never_installable`
结构性看护。

纯标准库（被 `engine/deprepair.py` import，那条链一路到 Flask 父进程）。
设计见 `docs/adr/0019-controlled-dependency-repair.md`。
"""
from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path

LOG = logging.getLogger("tavotto.depresolve")

# ---------------------------------------------------------------------------
# 可信度
# ---------------------------------------------------------------------------
#: 项目自己声明过这个包（requirements.txt / pyproject.toml）——最可信的一档：
#: 是**用户自己**写下的依赖，Tavotto 只是照着装。
SOURCE_PROJECT_DECLARED = "project_declared"
#: Tavotto 维护的科研包映射（见 `CURATED` / `SAME_NAME`）。
SOURCE_CURATED = "curated"
#: 用户在界面上手动输入的包名。
SOURCE_USER_SPECIFIED = "user_specified"

#: 允许「一键安装」的来源。**guessed 不在其中，也没有 guessed 这一档**。
INSTALLABLE_SOURCES = (SOURCE_PROJECT_DECLARED, SOURCE_CURATED,
                       SOURCE_USER_SPECIFIED)

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

# ---------------------------------------------------------------------------
# curated 映射
#
# **刻意不维护一个几千项的 PyPI 数据库**：那份东西会过期、要联网校准、没人
# 逐条审得动，而它带来的是「装错包」这一类最难发现的错误。第一版只覆盖高频
# 科研 Python 包，每一条都能一眼看懂、都有用例。
#
# 两张表分开是有意的：
#   * `CURATED`   —— import 名与包名**不同**的，必须查表才知道；
#   * `SAME_NAME` —— import 名与包名相同、且我们确认过 PyPI 上那个名字就是
#                    这个包的。**同名不等于可信**——`pip install <随便一个
#                    import 名>` 正是抢注攻击的入口，所以同名也要显式登记。
# ---------------------------------------------------------------------------
CURATED: dict[str, str] = {
    # 图像 / 视觉
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "skimage": "scikit-image",
    # 机器学习 / 统计
    "sklearn": "scikit-learn",
    # 数据格式
    "yaml": "PyYAML",
    "OpenSSL": "pyOpenSSL",
    "dateutil": "python-dateutil",
    "serial": "pyserial",
    "usb": "pyusb",
    "bs4": "beautifulsoup4",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "PyMuPDF",
    "netCDF4": "netCDF4",
    "mpl_toolkits": "matplotlib",
    # 科研常见的「名字对不上」
    "Bio": "biopython",
    "OCC": "pythonocc-core",
    "vtkmodules": "vtk",
    "gi": "PyGObject",
    "zmq": "pyzmq",
    "wx": "wxPython",
}

#: import 名与发行包名相同、且确认过的高频科研包。
SAME_NAME: frozenset[str] = frozenset({
    # 数值 / 科学栈
    "numpy", "scipy", "pandas", "matplotlib", "sympy", "numba", "xarray",
    "statsmodels", "h5py", "netcdf4", "zarr", "dask", "polars", "pyarrow",
    # 领域库（用户复测里真实出现过的那几个就在这儿）
    "astropy", "lmfit", "uncertainties", "emcee", "corner", "ovito",
    "rdkit", "ase", "pymatgen", "MDAnalysis", "mdtraj", "nibabel", "pydicom",
    "obspy", "cartopy", "geopandas", "shapely", "pyproj", "rasterio",
    "networkx", "igraph", "scanpy", "anndata", "biotite",
    # 绘图 / 输出
    "seaborn", "plotly", "bokeh", "altair", "holoviews", "datashader",
    "colorcet", "cmocean", "palettable", "adjustText", "mplcursors",
    "squarify", "joypy", "pyvista", "trimesh", "meshio",
    # 工具
    "tqdm", "joblib", "openpyxl", "xlrd", "tabulate", "pint", "sympy",
    "requests", "click", "rich", "typer", "attrs", "cattrs",
    "torch", "tensorflow", "jax", "flax", "optax", "einops",
})


def curated_distribution(import_name: str) -> str | None:
    """curated 两张表的合并查询；查不到回 None（**不猜同名**）。"""
    name = str(import_name or "")
    if name in CURATED:
        return CURATED[name]
    # 大小写：PyPI 名不区分大小写，但 import 名区分。同名表按规范化名比对，
    # 回的是 import 名本身（`MDAnalysis` 的包名就是 `MDAnalysis`）。
    if normalize_distribution(name) in {normalize_distribution(n)
                                        for n in SAME_NAME}:
        return name
    return None


# ---------------------------------------------------------------------------
# 包名与版本约束的严格语法
#
# **即使 `shell=False`，pip 自己仍会把 `--index-url` / `-r` / `--target` 解析
# 成选项**——argv 是 list 只挡住了 shell 元字符，挡不住「参数被下游程序当成
# 开关」。所以用户能影响到的那个字符串必须先过一道严格语法，形状不对一律拒绝。
# ---------------------------------------------------------------------------
#: PEP 508 的包名：字母数字开头结尾，中间允许 `.`、`-`、`_`。
#: **开头必须是字母数字**，于是 `-r`、`--index-url`、`../x` 这一族在这里就死了。
_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

#: 允许的比较运算符。刻意**不含** `===`（任意字符串相等）与 `@`（直接 URL）。
_OPERATORS = ("==", ">=", "<=", "~=", "!=", ">", "<")

#: 版本串：数字/字母/点/加号/星号/下划线/连字符。不允许空格、斜杠、冒号。
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.*+!_-]*$")

#: PEP 503 规范化（比较包名身份时的唯一判据）。
_NORMALIZE_RE = re.compile(r"[-_.]+")

#: 只做**顶级** import 名的解析（`missing_module()` 产出的就是顶级名）。
_IMPORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_distribution(name: str) -> str:
    """PEP 503 规范化：`Scikit_Learn` 与 `scikit-learn` 是同一个包。"""
    return _NORMALIZE_RE.sub("-", str(name or "")).lower()


def valid_import_name(name: str) -> bool:
    """能不能安全地拿去 import（与 `projectenv.valid_module_name` 同一形状）。"""
    return bool(name) and bool(_IMPORT_RE.match(str(name)))


def parse_requirement(text: str) -> tuple[str, str] | None:
    """`"lmfit>=1.3"` → `("lmfit", ">=1.3")`；形状不对回 None。

    允许的**全部**形态（第一版刻意窄）：

        package-name
        package-name==1.2.3
        package-name>=1.2
        package-name>=1.2,<2

    明确拒绝，且每一条都有用例：`-r file.txt`、`--index-url …`、`https://…`、
    `git+https://…`、`file://…`、`../local-package`、`pkg @ url`、
    `pkg[extra]`、带 `;` 环境标记的、带空格的、带 shell 元字符的。

    「未来可以做的高级功能」不等于「第一版先放行」——放行了就再也收不回来。
    """
    raw = str(text or "")
    if not raw or raw != raw.strip() or any(c.isspace() for c in raw):
        # 前后空白与内部空白都拒绝：`pkg==1.0 --index-url http://evil` 只有
        # 靠「一个 token」这条判据才挡得住，事后拆词是挡不住的。
        return None
    # **取最早出现的那个运算符**，不是「表里第一个能找到的」：
    # `pkg<2,>=1` 里 `>=` 出现在后面，按表序切会把 `pkg<2,` 当成包名。
    cut = min((raw.find(op) for op in _OPERATORS if raw.find(op) > 0),
              default=-1)
    if cut < 0:
        return (raw, "") if _NAME_RE.match(raw) else None
    name, spec = raw[:cut], raw[cut:]
    return (name, spec) if _valid_name_and_spec(name, spec) else None


def _valid_name_and_spec(name: str, spec: str) -> bool:
    """包名合法 **且** 每一段版本约束都是「运算符 + 版本」。

    逐段判而不是整串正则：`>=1.2,<2` 是两段，其中任何一段不合形状（空段、
    只有运算符、版本里混进路径分隔符）整条都不算数。
    """
    if not _NAME_RE.match(name):
        return False
    chunks = spec.split(",")
    if not chunks:
        return False
    for chunk in chunks:
        for op in _OPERATORS:
            if chunk.startswith(op):
                if not _VERSION_RE.match(chunk[len(op):]):
                    return False
                break
        else:
            return False
    return True


# ---------------------------------------------------------------------------
# 依赖需求（本轮的数据模型）
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class DependencyRequirement:
    """「要装什么」的完整描述——安装计划、UI、诊断三处共读这一份。

    `installable` 是**结构性**的：来源不在 `INSTALLABLE_SOURCES` 或包名不合
    语法时它就是 False，调用方不需要（也不该）自己再判一遍。
    """
    import_name: str
    distribution: str
    specifier: str = ""
    resolution_source: str = SOURCE_CURATED
    confidence: str = CONFIDENCE_HIGH

    @property
    def installable(self) -> bool:
        return (bool(self.distribution)
                and self.resolution_source in INSTALLABLE_SOURCES
                and self.confidence == CONFIDENCE_HIGH
                and parse_requirement(self.requirement()) is not None)

    def requirement(self) -> str:
        """交给 pip 的那一个参数（`lmfit>=1.3`）。"""
        return f"{self.distribution}{self.specifier}"

    def to_payload(self) -> dict:
        return {"import_name": self.import_name,
                "distribution": self.distribution,
                "specifier": self.specifier,
                "requirement": self.requirement(),
                "resolution_source": self.resolution_source,
                "confidence": self.confidence,
                "installable": self.installable}


# ---------------------------------------------------------------------------
# Level 1：项目自己声明的依赖（只读解析，永不改写）
# ---------------------------------------------------------------------------
#: 认哪些声明文件。**只读**——Tavotto 绝不修改用户的 requirements/pyproject。
REQUIREMENTS_GLOBS = ("requirements.txt", "requirements-*.txt", "requirements/*.txt")
PYPROJECT_NAME = "pyproject.toml"

#: 一个项目里最多读多少个声明文件 / 每个文件最多多少行。恶意或手滑的巨大
#: 文件不该让渲染错误响应卡住（这段代码跑在**出错响应**那条路上）。
MAX_DECL_FILES = 12
MAX_DECL_BYTES = 512 * 1024


def _decl_dirs(figures_dir: str | Path, script: str | None) -> list[Path]:
    """从脚本所在目录逐级向上到项目根——与 venv 发现同一套范围纪律。"""
    root = Path(figures_dir)
    try:
        root_real = root.resolve(strict=False)
        start = ((root / script).parent if script else root).resolve(strict=False)
    except OSError:
        return [root]
    if not (start == root_real or root_real in start.parents):
        # 脚本在项目外（更早就该被 `script_path_outside_project` 拦下）
        start = root_real
    dirs: list[Path] = []
    cur = start
    while True:
        dirs.append(cur)
        if cur == root_real or cur.parent == cur:
            break
        cur = cur.parent
    return dirs


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_DECL_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_requirements_text(text: str) -> dict[str, str]:
    """requirements.txt → `{规范化包名: 版本约束}`。

    **不执行、不递归 `-r`、不碰任何选项行**：这里要的只是「这个项目声明过
    哪些包、什么版本」。看不懂的行安静跳过——依赖声明解析失败绝不能阻断
    Tavotto（malformed 只意味着「这一档解析源不可用」）。
    """
    out: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue                       # 选项行（-r / --index-url / -e）
        line = line.split(";", 1)[0].strip()          # 环境标记
        line = re.sub(r"\[[^\]]*\]", "", line, count=1)  # extras
        parsed = parse_requirement(line.replace(" ", ""))
        if parsed is None:
            continue
        name, spec = parsed
        out.setdefault(normalize_distribution(name), spec)
    return out


def _pyproject_dependency_strings(text: str) -> list[str]:
    """从 pyproject.toml 里抠出依赖字符串（tomllib 优先，缺席时退化）。

    Python 3.10 没有 `tomllib`（3.11+ 才进标准库），而支持区间是
    `>=3.10`。退化路径只认 `dependencies = [...]` 这一族数组里的字符串
    字面量——**宁可少认，不可认错**：认错的后果是装了一个项目没声明的包。
    """
    items: list[str] = []
    try:
        import tomllib  # 3.11+
        data = tomllib.loads(text)
    except (ImportError, ValueError):
        # 退化路径只认**名字里带 dependencies / requires** 的数组：
        # 认下 `classifiers = [...]` 那种表只会把无关字符串当成依赖声明。
        for block in re.findall(
                r"(?ms)^\s*[\"']?[A-Za-z0-9_.-]*(?:dependencies|requires)"
                r"[A-Za-z0-9_.-]*[\"']?\s*=\s*\[(.*?)\]", text, re.I):
            items += re.findall(r"""["']([^"'\n]+)["']""", block)
        return items
    project = data.get("project")
    if isinstance(project, dict):
        items += [d for d in (project.get("dependencies") or [])
                  if isinstance(d, str)]
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for group in extras.values():
                items += [d for d in (group or []) if isinstance(d, str)]
    # Poetry 的表是 `{包名: 版本}`，形状不同但同样是「项目声明过」。
    poetry = (((data.get("tool") or {}).get("poetry") or {})
              .get("dependencies") or {})
    if isinstance(poetry, dict):
        for name, ver in poetry.items():
            if name.lower() == "python":
                continue
            if isinstance(ver, str) and ver and ver[0] in "0123456789":
                items.append(f"{name}=={ver}" if ver[0].isdigit() else name)
            else:
                items.append(str(name))
    return items


def project_declared(figures_dir: str | Path,
                     script: str | None = None) -> dict[str, str]:
    """这个项目声明过哪些依赖 → `{规范化包名: 版本约束}`。

    **只读**：不修改、不创建、不 `pip install -r`。解析失败（malformed
    pyproject、编码坏了、权限不够）一律当作「这一档不可用」，绝不冒泡成
    错误——本轮要修的那个脚本可能靠 curated 映射就能修好，不该被一份坏的
    元数据连坐。
    """
    declared: dict[str, str] = {}
    files = 0
    for directory in _decl_dirs(figures_dir, script):
        candidates: list[Path] = []
        for pattern in REQUIREMENTS_GLOBS:
            try:
                candidates += sorted(directory.glob(pattern))
            except OSError:
                continue
        pyproject = directory / PYPROJECT_NAME
        try:
            if pyproject.is_file():
                candidates.append(pyproject)
        except OSError:
            pass
        for path in candidates:
            if files >= MAX_DECL_FILES:
                return declared
            files += 1
            text = _read_text(path)
            if not text:
                continue
            try:
                if path.name == PYPROJECT_NAME:
                    found = parse_requirements_text(
                        "\n".join(_pyproject_dependency_strings(text)))
                else:
                    found = parse_requirements_text(text)
            except (ValueError, TypeError, RecursionError) as exc:
                LOG.debug("依赖声明解析失败（忽略）: %s: %s", path, exc)
                continue
            for name, spec in found.items():
                declared.setdefault(name, spec)
    return declared


# ---------------------------------------------------------------------------
# 解析入口
# ---------------------------------------------------------------------------
def resolve(figures_dir: str | Path, import_name: str,
            script: str | None = None) -> DependencyRequirement | None:
    """缺的 import 名 → 可信的安装目标；**解析不到就是 None**。

    顺序（可信度从高到低，与 ADR 0019 §解析可信度逐条对应）：

    1. **项目自己声明过** —— 用项目声明的包名与版本约束。`import PIL` 遇上
       `Pillow>=10` 时要先经 curated 才知道两者是同一个包，所以这一档同样
       查表，只是**版本约束用项目的那一份**。
    2. **curated** —— Tavotto 维护的科研包映射（含同名白名单）。
    3. 解析不到 —— 回 None。调用方据此给「指定安装包…」的手动出口，
       **绝不**拿 import 名当包名装。
    """
    if not valid_import_name(import_name):
        return None
    declared = project_declared(figures_dir, script)
    curated = curated_distribution(import_name)
    # 候选包名：curated 的那个 + import 名自身（后者**只用于在项目声明里
    # 找证据**，找不到证据绝不会成为安装目标）。
    for candidate in [c for c in (curated, import_name) if c]:
        key = normalize_distribution(candidate)
        if key in declared:
            return DependencyRequirement(
                import_name=import_name, distribution=candidate,
                specifier=declared[key],
                resolution_source=SOURCE_PROJECT_DECLARED,
                confidence=CONFIDENCE_HIGH)
    if curated:
        return DependencyRequirement(
            import_name=import_name, distribution=curated, specifier="",
            resolution_source=SOURCE_CURATED, confidence=CONFIDENCE_HIGH)
    return None


def from_user_input(import_name: str, text: str) -> DependencyRequirement | None:
    """用户手动指定的包名 → 需求；语法不合一律 None。

    `import my_lab_tools` 这类私有包 Tavotto 无从解析，只能问用户。但**问来的
    答案同样要过语法关**：用户可能粘进来一整行 `pip install -r req.txt`，
    而那串东西会被 pip 当成选项解析。
    """
    parsed = parse_requirement(text)
    if parsed is None:
        return None
    name, spec = parsed
    return DependencyRequirement(
        import_name=import_name if valid_import_name(import_name) else "",
        distribution=name, specifier=spec,
        resolution_source=SOURCE_USER_SPECIFIED, confidence=CONFIDENCE_HIGH)
