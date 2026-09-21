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
import os
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
INSTALLABLE_SOURCES = (SOURCE_PROJECT_DECLARED, SOURCE_CURATED, SOURCE_USER_SPECIFIED)

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
SAME_NAME: frozenset[str] = frozenset(
    {
        # 数值 / 科学栈
        "numpy",
        "scipy",
        "pandas",
        "matplotlib",
        "sympy",
        "numba",
        "xarray",
        "statsmodels",
        "h5py",
        "netcdf4",
        "zarr",
        "dask",
        "polars",
        "pyarrow",
        # 领域库（用户复测里真实出现过的那几个就在这儿）
        "astropy",
        "lmfit",
        "uncertainties",
        "emcee",
        "corner",
        "ovito",
        "rdkit",
        "ase",
        "pymatgen",
        "MDAnalysis",
        "mdtraj",
        "nibabel",
        "pydicom",
        "obspy",
        "cartopy",
        "geopandas",
        "shapely",
        "pyproj",
        "rasterio",
        "networkx",
        "igraph",
        "scanpy",
        "anndata",
        "biotite",
        # 绘图 / 输出
        "seaborn",
        "plotly",
        "bokeh",
        "altair",
        "holoviews",
        "datashader",
        "colorcet",
        "cmocean",
        "palettable",
        "adjustText",
        "mplcursors",
        "squarify",
        "joypy",
        "pyvista",
        "trimesh",
        "meshio",
        # 工具
        "tqdm",
        "joblib",
        "openpyxl",
        "xlrd",
        "tabulate",
        "pint",
        "sympy",
        "requests",
        "click",
        "rich",
        "typer",
        "attrs",
        "cattrs",
        "torch",
        "tensorflow",
        "jax",
        "flax",
        "optax",
        "einops",
    }
)


def curated_distribution(import_name: str) -> str | None:
    """curated 两张表的合并查询；查不到回 None（**不猜同名**）。"""
    name = str(import_name or "")
    if name in CURATED:
        return CURATED[name]
    # 大小写：PyPI 名不区分大小写，但 import 名区分。同名表按规范化名比对，
    # 回的是 import 名本身（`MDAnalysis` 的包名就是 `MDAnalysis`）。
    if normalize_distribution(name) in {normalize_distribution(n) for n in SAME_NAME}:
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
    cut = min((raw.find(op) for op in _OPERATORS if raw.find(op) > 0), default=-1)
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
                if not _VERSION_RE.match(chunk[len(op) :]):
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
        return (
            bool(self.distribution)
            and self.resolution_source in INSTALLABLE_SOURCES
            and self.confidence == CONFIDENCE_HIGH
            and parse_requirement(self.requirement()) is not None
        )

    def requirement(self) -> str:
        """交给 pip 的那一个参数（`lmfit>=1.3`）。"""
        return f"{self.distribution}{self.specifier}"

    def to_payload(self) -> dict:
        return {
            "import_name": self.import_name,
            "distribution": self.distribution,
            "specifier": self.specifier,
            "requirement": self.requirement(),
            "resolution_source": self.resolution_source,
            "confidence": self.confidence,
            "installable": self.installable,
        }


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
            continue  # 选项行（-r / --index-url / -e）
        line = line.split(";", 1)[0].strip()  # 环境标记
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
            r"[A-Za-z0-9_.-]*[\"']?\s*=\s*\[(.*?)\]",
            text,
            re.I,
        ):
            items += re.findall(r"""["']([^"'\n]+)["']""", block)
        return items
    project = data.get("project")
    if isinstance(project, dict):
        items += [d for d in (project.get("dependencies") or []) if isinstance(d, str)]
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for group in extras.values():
                items += [d for d in (group or []) if isinstance(d, str)]
    # Poetry 的表是 `{包名: 版本}`，形状不同但同样是「项目声明过」。
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if isinstance(poetry, dict):
        for name, ver in poetry.items():
            if name.lower() == "python":
                continue
            if isinstance(ver, str) and ver and ver[0] in "0123456789":
                items.append(f"{name}=={ver}" if ver[0].isdigit() else name)
            else:
                items.append(str(name))
    return items


def project_declared(figures_dir: str | Path, script: str | None = None) -> dict[str, str]:
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
                    found = parse_requirements_text("\n".join(_pyproject_dependency_strings(text)))
                else:
                    found = parse_requirements_text(text)
            except (ValueError, TypeError, RecursionError) as exc:
                LOG.debug("依赖声明解析失败（忽略）: %s: %s", path, exc)
                continue
            for name, spec in found.items():
                declared.setdefault(name, spec)
    return declared


# ---------------------------------------------------------------------------
# DependencyIntent（统一实施包 U01 → U04，ADR 0053 / 0061）——声明的**无损**读法
#
# 上面那条 `parse_requirements_text` 是安装路径的解析器：它刻意窄（extras 剥掉、
# marker 丢掉、同名取第一条），因为它的输出要交给 pip 的**旧单包路径**。U00 实测
# （`U00_BASELINE.md` §4.3）证明这一层看不见 extras / marker / constraints / 同名冲突——
# 而「项目声明了什么」这个问题需要**原样**的答案。这里是第二个读法，U04 起交给成熟
# 解析器（`packaging.requirements` / `.markers` / `.specifiers`，PEP 508 / 440 的参考
# 实现；pip 与 uv 认的就是它）：
#
#   * 每一行都成为一条 intent，看不懂的行是 `kind=unknown` 并保留原文——
#     **unknown 不是空依赖**（04 §2）；认得出、但 Tavotto 不会替用户安装的构造
#     （直接 URL / VCS / `-e` / 本地路径 / `--index-url` 之类的选项行 / Poetry `^` /
#     pixi 锁）是 `kind=unsupported` + 闭集 `reason`——**它们同样不是空依赖**：
#     一条 unsupported 的声明意味着这个项目的约束 Tavotto 没法完整兑现，联合安装
#     必须明确停下（ADR 0061 §三），不能把 `^` 或 marker 剥掉偷偷继续；
#   * extras / marker 原样保留、marker **不在这里求值**（求值要拿目标解释器的环境，
#     归 `depplan.select`）；
#   * `-r` / `-c` 在**项目根之内**有界跟进（同一份文件上限、循环 / 越界 / 缺失各是一条
#     unsupported，不是忽略）；`constraints.txt` 与 `-c` 引到的都是 `kind=constraint`；
#   * PEP 723 脚本内联元数据、pyproject 的 `[project.dependencies]` /
#     `[project.optional-dependencies]` / `[dependency-groups]`（PEP 735，含
#     `include-group`）按各自元数据范围读；`[tool.poetry.dependencies]` 只读**能无损
#     表达**的那部分（PEP 440 形态的字符串），`^` / `~` / 表结构一律 unsupported；
#   * 同名多条**全部保留**，`conflicts()` 用 `SpecifierSet` 找出**确定**矛盾的那些。
#
# 安装路径一个字节没动：`DependencyRequirement.installable` 仍只认窄语法。联合安装交给
# pip 的字符串由 `requirement_string()` 从**解析后的结构**重新序列化——原文一个字节
# 都不直接进 argv（与 ADR 0038 `argv_package_name` 同一条纪律）。
# ---------------------------------------------------------------------------
INTENT_KIND_REQUIREMENT = "requirement"
INTENT_KIND_CONSTRAINT = "constraint"
INTENT_KIND_UNKNOWN = "unknown"
INTENT_KIND_UNSUPPORTED = "unsupported"
INTENT_KINDS = (
    INTENT_KIND_REQUIREMENT,
    INTENT_KIND_CONSTRAINT,
    INTENT_KIND_UNKNOWN,
    INTENT_KIND_UNSUPPORTED,
)

#: `kind=unsupported` 的原因——**闭集**。加一条就要在细则与前端文案里说清用户该做什么。
UNSUPPORTED_DIRECT_URL = "direct_url"  # `pkg @ https://…` / `git+…`
UNSUPPORTED_EDITABLE = "editable_install"  # `-e .` / `--editable path`
UNSUPPORTED_LOCAL_PATH = "local_path"  # `./vendor/pkg` / `../x` / `*.whl`
UNSUPPORTED_OPTION_LINE = "option_line"  # `--index-url` / `--find-links` / `--pre` …
UNSUPPORTED_PER_REQ_OPTION = "per_requirement_option"  # `--global-option` 之类（`--hash` 认）
UNSUPPORTED_INCLUDE_MISSING = "include_missing"  # `-r x.txt` 指向不存在的文件
UNSUPPORTED_INCLUDE_OUTSIDE = "include_outside_project"  # `-r ../../x.txt`
UNSUPPORTED_INCLUDE_CYCLE = "include_cycle"  # a -r b, b -r a
UNSUPPORTED_INCLUDE_LIMIT = "include_limit"  # 跟进的文件超过上限
UNSUPPORTED_POETRY_CONSTRAINT = "poetry_constraint"  # `^1.2` / `~1.2` / `{version=…}`
UNSUPPORTED_MANAGER_LOCK = "manager_lock"  # pixi / conda / poetry.lock（X01）
UNSUPPORTED_TOML_PARSER = "toml_parser_unavailable"  # 3.10 且没有 tomllib / tomli
UNSUPPORTED_TOML_INVALID = "toml_invalid"  # pyproject / PEP 723 块解析失败
UNSUPPORTED_ENV_VARIABLE = "environment_variable"  # `${TOKEN}` 展开（pip 特性，不做）
UNSUPPORTED_REASONS = (
    UNSUPPORTED_DIRECT_URL,
    UNSUPPORTED_EDITABLE,
    UNSUPPORTED_LOCAL_PATH,
    UNSUPPORTED_OPTION_LINE,
    UNSUPPORTED_PER_REQ_OPTION,
    UNSUPPORTED_INCLUDE_MISSING,
    UNSUPPORTED_INCLUDE_OUTSIDE,
    UNSUPPORTED_INCLUDE_CYCLE,
    UNSUPPORTED_INCLUDE_LIMIT,
    UNSUPPORTED_POETRY_CONSTRAINT,
    UNSUPPORTED_MANAGER_LOCK,
    UNSUPPORTED_TOML_PARSER,
    UNSUPPORTED_TOML_INVALID,
    UNSUPPORTED_ENV_VARIABLE,
)

CONSTRAINTS_NAME = "constraints.txt"
#: 认得出但只会被标成 unsupported 的管理器锁文件（X01 之前不做转换）。它们在项目里
#: 出现本身就是一条信息：这个项目的约束可能不止 requirements / pyproject 里那些。
MANAGER_LOCK_NAMES = ("poetry.lock", "pixi.lock", "pixi.toml", "environment.yml", "conda-lock.yml")

#: 默认选中的组：**只有**这几种（FO20：未选的 optional / dev / 训练组不装）。文件组按
#: 文件名判（任何目录层级的 `requirements.txt`），pyproject 主依赖，脚本自己的 PEP 723。
#: 其余组（`requirements-*.txt` / `requirements/*.txt` / optional-dependencies /
#: dependency-groups）要用户在项目设置里点名。
GROUP_PYPROJECT_MAIN = "project.dependencies"
GROUP_PYPROJECT_OPTIONAL = "optional-dependencies"
GROUP_PYPROJECT_GROUPS = "dependency-groups"
GROUP_PYPROJECT_POETRY = "tool.poetry.dependencies"
GROUP_PEP723 = "pep723"


@dataclasses.dataclass(frozen=True)
class DependencyIntent:
    """一条依赖声明的原样描述（04 §2：name/version/extras/marker/group/source/constraints）。

    `name` 是规范化后的 distribution 名（`unknown` / 没有名字的 `unsupported` 时为空串）；
    `specifier` 是 `SpecifierSet` 的规范串（`>=1.2,<2` → `<2,>=1.2`，只是排序，语义不变）；
    `raw` 永远是那一行的原文。`group` 是它所属的组（`requirements.txt` /
    `scripts/requirements-dev.txt` / `pyproject.toml:project.dependencies` /
    `pyproject.toml:optional-dependencies.<名>` / `pyproject.toml:dependency-groups.<名>` /
    `pep723:<脚本>`），`source` 是声明文件相对项目根的 POSIX 路径。`hashes` 是 pip
    requirements 格式的 `--hash=sha256:…`（有就原样带着，联合安装按它 `--require-hashes`）。
    `reason` 只在 `unsupported` 时有值（闭集 `UNSUPPORTED_REASONS`）。
    """

    name: str
    specifier: str = ""
    extras: tuple[str, ...] = ()
    marker: str = ""
    group: str = ""
    source: str = ""
    kind: str = INTENT_KIND_REQUIREMENT
    raw: str = ""
    hashes: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in INTENT_KINDS:
            raise ValueError(f"kind 非法: {self.kind!r}（可选 {INTENT_KINDS}）")
        if self.kind in (INTENT_KIND_REQUIREMENT, INTENT_KIND_CONSTRAINT) and not self.name:
            raise ValueError("requirement / constraint 的 intent 必须有 name")
        if self.kind == INTENT_KIND_UNSUPPORTED and self.reason not in UNSUPPORTED_REASONS:
            raise ValueError(f"unsupported 的 reason 非法: {self.reason!r}")
        if self.kind != INTENT_KIND_UNSUPPORTED and self.reason:
            raise ValueError("只有 unsupported 才有 reason")

    @property
    def declared(self) -> bool:
        """认得出名字与约束、可以进联合求解的那两档。"""
        return self.kind in (INTENT_KIND_REQUIREMENT, INTENT_KIND_CONSTRAINT)

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "specifier": self.specifier,
            "extras": list(self.extras),
            "marker": self.marker,
            "group": self.group,
            "source": self.source,
            "kind": self.kind,
            "raw": self.raw,
            "hashes": list(self.hashes),
            "reason": self.reason,
        }


def _pkg():
    """`packaging` 的三件套——延后到用到时才 import。

    它是运行时依赖（`pyproject.toml`，ADR 0061），不是科学栈；延后 import 只是让
    `depresolve` 的**旧安装路径**（`parse_requirement` / `resolve`）在没有它的环境里
    照样能被 import——那条路一个字节都不需要它。
    """
    from packaging import markers, requirements, specifiers, utils, version

    return requirements, markers, specifiers, utils, version


#: pip requirements 格式里的**整行**选项（不是依赖）。`-r` / `-c` 单独处理（有界跟进）。
_OPTION_PREFIXES = (
    "-i",
    "--index-url",
    "--extra-index-url",
    "--no-index",
    "-f",
    "--find-links",
    "--no-binary",
    "--only-binary",
    "--prefer-binary",
    "--require-hashes",
    "--pre",
    "--trusted-host",
    "--use-feature",
    "--no-build-isolation",
)
_INCLUDE_RE = re.compile(r"^(-r|--requirement|-c|--constraint)(?:=|\s+)(.+)$")
_EDITABLE_RE = re.compile(r"^(-e|--editable)(?:=|\s+)")
#: pip 的每需求选项：`pkg==1 --hash=sha256:… --hash=sha256:…`。`--hash` 认；其余不认。
_PER_REQ_OPTION_RE = re.compile(r"\s+(--[a-z][a-z-]*)(?:=|\s+)(\S+)")
_HASH_RE = re.compile(r"^(sha256|sha384|sha512):[0-9a-fA-F]{64,128}$")
_ENV_VAR_RE = re.compile(r"\$\{[A-Z0-9_]+\}")
#: 一个 token 看起来像路径 / 归档而不是包名：`./x`、`../x`、`/abs`、`x.whl`、`x.tar.gz`、`.`。
_PATHLIKE_RE = re.compile(r"^(\.{1,2}(/|\\|$)|/|[A-Za-z]:[\\/]|~)")
_ARCHIVE_RE = re.compile(r"\.(whl|zip|tar\.gz|tgz|tar\.bz2)$", re.I)
#: 直接 URL 的形态（`Requirement` 认 `pkg @ url`；这里挡的是裸 URL 与 VCS 前缀）。
_URL_RE = re.compile(r"^([a-z][a-z0-9+.-]*):(//|.*@)", re.I)


def _unsupported(
    raw: str, reason: str, *, group: str, source: str, name: str = ""
) -> DependencyIntent:
    return DependencyIntent(
        name=name,
        group=group,
        source=source,
        kind=INTENT_KIND_UNSUPPORTED,
        raw=raw.strip(),
        reason=reason,
    )


def _unknown(raw: str, *, group: str, source: str) -> DependencyIntent:
    return DependencyIntent(
        name="", group=group, source=source, kind=INTENT_KIND_UNKNOWN, raw=raw.strip()
    )


def intent_from_requirement(
    req,
    *,
    raw: str,
    group: str = "",
    source: str = "",
    kind: str = INTENT_KIND_REQUIREMENT,
    hashes: tuple[str, ...] = (),
) -> DependencyIntent:
    """`packaging.requirements.Requirement` → intent（直接 URL 归 unsupported）。

    extras 按 PEP 685 规范化（`WideChars` → `widechars`）——同一条声明永远得到同一个串。
    """
    _requirements, _markers, _specifiers, _utils, _version = _pkg()
    if req.url:
        return _unsupported(
            raw,
            UNSUPPORTED_DIRECT_URL,
            group=group,
            source=source,
            name=normalize_distribution(req.name),
        )
    return DependencyIntent(
        name=normalize_distribution(req.name),
        specifier=str(req.specifier),
        extras=tuple(sorted(_utils.canonicalize_name(str(e)) for e in req.extras)),
        marker=str(req.marker) if req.marker is not None else "",
        group=group,
        source=source,
        kind=kind,
        raw=raw.strip(),
        hashes=hashes,
    )


def parse_intent(
    line: str, *, group: str = "", source: str = "", kind: str = INTENT_KIND_REQUIREMENT
) -> DependencyIntent | None:
    """一行声明 → intent。空行 / 注释回 None；认不出的回 `kind=unknown`、认得出但不做的回
    `kind=unsupported`（都不丢、都不是空依赖）。

    认得的形状是完整的 PEP 508（`packaging.requirements.Requirement`）：`name`、`name[extra]`、
    `name<op>ver[,<op>ver]`、`name[e]>=1; marker`，外加 pip 的每需求 `--hash=`。`-r` / `-c`
    **不在这里**（它们要读别的文件，归 `declared_intents` 的有界跟进）：脱离文件上下文的
    一行 include 没有可跟进的对象，回 `unknown`。
    """
    raw = str(line or "")
    body = raw.split("#", 1)[0].strip()
    if not body:
        return None
    if _ENV_VAR_RE.search(body):
        return _unsupported(raw, UNSUPPORTED_ENV_VARIABLE, group=group, source=source)
    if _EDITABLE_RE.match(body):
        return _unsupported(raw, UNSUPPORTED_EDITABLE, group=group, source=source)
    if _INCLUDE_RE.match(body):
        return _unknown(raw, group=group, source=source)
    if body.startswith("-"):
        if any(
            body == p or body.startswith(p + "=") or body.startswith(p + " ")
            for p in _OPTION_PREFIXES
        ):
            return _unsupported(raw, UNSUPPORTED_OPTION_LINE, group=group, source=source)
        return _unknown(raw, group=group, source=source)
    # 每需求选项：`pkg==1 --hash=sha256:…`。`--hash` 认下（联合安装按它 require-hashes），
    # 别的（`--global-option` / `--config-settings` / `--install-option`）不认。
    hashes: list[str] = []
    head = body
    for m in _PER_REQ_OPTION_RE.finditer(body):
        opt, val = m.group(1), m.group(2)
        if opt == "--hash" and _HASH_RE.match(val):
            hashes.append(val.lower())
        else:
            return _unsupported(raw, UNSUPPORTED_PER_REQ_OPTION, group=group, source=source)
    if hashes:
        head = body[: _PER_REQ_OPTION_RE.search(body).start()].strip()
    if _URL_RE.match(head):
        # 裸 URL / VCS（`https://…/x.whl`、`git+https://…`）。`pkg @ url` 以包名开头，不在
        # 这里；它由 `Requirement` 认出来、在 `intent_from_requirement` 里归同一档。
        return _unsupported(raw, UNSUPPORTED_DIRECT_URL, group=group, source=source)
    if _ARCHIVE_RE.search(head) and " @ " not in head:
        # `x.whl` 按 PEP 508 是个合法包名，pip 却会按文件系统把它当归档——按归档判
        # （没有人会给包起这种名字，而本地归档正是 pip 认、Tavotto 不做的那一档）。
        return _unsupported(raw, UNSUPPORTED_LOCAL_PATH, group=group, source=source)
    requirements, _markers, _specifiers, _utils, _version = _pkg()
    try:
        req = requirements.Requirement(head)
    except requirements.InvalidRequirement:
        # 成熟解析器认不出：再看它是不是本地路径（`./x`、`../x`、`/abs`）；不是就是 unknown。
        if _PATHLIKE_RE.search(head):
            return _unsupported(raw, UNSUPPORTED_LOCAL_PATH, group=group, source=source)
        return _unknown(raw, group=group, source=source)
    return intent_from_requirement(
        req, raw=raw, group=group, source=source, kind=kind, hashes=tuple(hashes)
    )


def _logical_lines(text: str) -> list[str]:
    """pip requirements 格式：行尾 `\\` 续行；`#` 注释。回逻辑行（保留原文拼接）。"""
    out: list[str] = []
    buf = ""
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if line.endswith("\\") and not line.lstrip().startswith("#"):
            buf += line[:-1]
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


def parse_intents_text(
    text: str, *, group: str = "", source: str = "", kind: str = INTENT_KIND_REQUIREMENT
) -> list[DependencyIntent]:
    """一份 requirements / constraints 文本 → intents（**不跟进 `-r` / `-c`**：跟进要文件
    上下文，归 `declared_intents`；这里遇到 include 行回 unknown）。"""
    out: list[DependencyIntent] = []
    for line in _logical_lines(text):
        intent = parse_intent(line, group=group, source=source, kind=kind)
        if intent is not None:
            out.append(intent)
    return out


# ---- pyproject / PEP 723 ---------------------------------------------------
def _toml_loads(text: str):
    """`tomllib`（3.11+）→ `tomli`（若装了）→ None（3.10 且两者都没有）。

    回 `(data, None)` / `(None, reason)`。**没有解析器不是「没有依赖」**：调用方把整份
    pyproject 记成一条 `unsupported/toml_parser_unavailable`，联合安装据此明确停下。
    """
    loads = None
    try:
        import tomllib  # 3.11+

        loads = tomllib.loads
    except ImportError:
        try:
            import tomli  # type: ignore[import-not-found]

            loads = tomli.loads
        except ImportError:
            return None, UNSUPPORTED_TOML_PARSER
    try:
        return loads(text), None
    except (ValueError, TypeError) as exc:  # tomllib.TOMLDecodeError 是 ValueError 的子类
        LOG.debug("TOML 解析失败: %s", exc)
        return None, UNSUPPORTED_TOML_INVALID


def _poetry_intent(name: str, spec, *, group: str, source: str) -> DependencyIntent:
    """Poetry 表里的一条：只有 PEP 440 形态的字符串能无损表达；`^` / `~` / 表 / 列表一律
    unsupported——**不剥 `^` 偷偷继续**。"""
    raw = f"{name} = {spec!r}"
    if isinstance(spec, str):
        text = spec.strip()
        if text in ("*", ""):
            intent = parse_intent(name, group=group, source=source)
            if intent is None or intent.kind != INTENT_KIND_REQUIREMENT:
                return _unsupported(
                    raw,
                    UNSUPPORTED_POETRY_CONSTRAINT,
                    group=group,
                    source=source,
                    name=normalize_distribution(name),
                )
            return dataclasses.replace(intent, raw=raw)
        if text[0] == "^" or (text[0] == "~" and not text.startswith("~=")):
            # Poetry 的 caret / tilde 与 PEP 440 的 `~=` 不是一回事：不翻译、不剥掉。
            return _unsupported(
                raw,
                UNSUPPORTED_POETRY_CONSTRAINT,
                group=group,
                source=source,
                name=normalize_distribution(name),
            )
        candidate = f"{name}{text}" if text[0] in "<>=!~" else f"{name}=={text}"
        intent = parse_intent(candidate, group=group, source=source)
        if intent is None or intent.kind != INTENT_KIND_REQUIREMENT:
            return _unsupported(
                raw,
                UNSUPPORTED_POETRY_CONSTRAINT,
                group=group,
                source=source,
                name=normalize_distribution(name),
            )
        return dataclasses.replace(intent, raw=raw)
    return _unsupported(
        raw,
        UNSUPPORTED_POETRY_CONSTRAINT,
        group=group,
        source=source,
        name=normalize_distribution(name),
    )


def pyproject_intents(text: str, *, source: str = "pyproject.toml") -> list[DependencyIntent]:
    """pyproject.toml → intents，组名区分主依赖 / 每个 optional 组 / 每个 dependency-group /
    Poetry 表。PEP 735 的 `include-group` 展开成被包含组的条目（组名记**外层**组，
    `raw` 保留 `{include-group = …}` 的出处）；循环 include 记 unsupported。"""
    data, why = _toml_loads(text)
    if data is None:
        return [
            _unsupported(
                "<pyproject.toml>", why, group=f"{source}:{GROUP_PYPROJECT_MAIN}", source=source
            )
        ]
    out: list[DependencyIntent] = []
    project = data.get("project")
    if isinstance(project, dict):
        g = f"{source}:{GROUP_PYPROJECT_MAIN}"
        for d in project.get("dependencies") or []:
            if isinstance(d, str):
                it = parse_intent(d, group=g, source=source)
                if it is not None:
                    out.append(it)
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for gname, group in extras.items():
                g = f"{source}:{GROUP_PYPROJECT_OPTIONAL}.{gname}"
                for d in group or []:
                    if isinstance(d, str):
                        it = parse_intent(d, group=g, source=source)
                        if it is not None:
                            out.append(it)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for gname in groups:
            label = f"{source}:{GROUP_PYPROJECT_GROUPS}.{gname}"
            out += _dependency_group(groups, str(gname), source=source, label=label, chain=())
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if isinstance(poetry, dict):
        g = f"{source}:{GROUP_PYPROJECT_POETRY}"
        for name, spec in poetry.items():
            if str(name).lower() == "python":
                continue
            out.append(_poetry_intent(str(name), spec, group=g, source=source))
    return out


def _dependency_group(
    groups: dict, gname: str, *, source: str, label: str, chain: tuple[str, ...]
) -> list[DependencyIntent]:
    """PEP 735 一组：字符串条目是 PEP 508；`{include-group = "x"}` 展开 x（有界、防环）。

    展开出来的条目归**最外层**那一组（`label`）：用户选的是外层组，被包含的组只是它的
    实现细节；`raw` 保留各自的原文。
    """
    out: list[DependencyIntent] = []
    items = groups.get(gname)
    if not isinstance(items, list):
        return out
    for item in items:
        if isinstance(item, str):
            it = parse_intent(item, group=label, source=source)
            if it is not None:
                out.append(it)
        elif isinstance(item, dict) and "include-group" in item:
            inc = str(item["include-group"])
            raw = f"{{include-group = {inc!r}}}"
            if inc == gname or inc in chain:
                out.append(_unsupported(raw, UNSUPPORTED_INCLUDE_CYCLE, group=label, source=source))
            elif inc not in groups:
                out.append(
                    _unsupported(raw, UNSUPPORTED_INCLUDE_MISSING, group=label, source=source)
                )
            elif len(chain) >= 8:
                out.append(_unsupported(raw, UNSUPPORTED_INCLUDE_LIMIT, group=label, source=source))
            else:
                out += _dependency_group(
                    groups, inc, source=source, label=label, chain=(*chain, gname)
                )
        else:
            out.append(_unknown(repr(item), group=label, source=source))
    return out


#: PEP 723：`# /// script` … `# ///` 块（正则来自 PEP 原文）。
_PEP723_RE = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")


def pep723_intents(script_text: str, *, source: str) -> list[DependencyIntent]:
    """脚本自己的内联元数据（PEP 723）→ intents，组名 `pep723:<脚本>`。没有块回空列表
    ——**这一处的空是真的空**（脚本没写）。块坏了 / 没有 TOML 解析器记 unsupported。"""
    blocks = [m for m in _PEP723_RE.finditer(str(script_text or "")) if m.group("type") == "script"]
    if not blocks:
        return []
    group = f"{GROUP_PEP723}:{source}"
    if len(blocks) > 1:
        return [
            _unsupported(
                "# /// script (multiple)", UNSUPPORTED_TOML_INVALID, group=group, source=source
            )
        ]
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0].group("content").splitlines(keepends=True)
    )
    data, why = _toml_loads(content)
    if data is None:
        return [_unsupported("# /// script", why, group=group, source=source)]
    out: list[DependencyIntent] = []
    for d in data.get("dependencies") or []:
        if isinstance(d, str):
            it = parse_intent(d, group=group, source=source)
            if it is not None:
                out.append(it)
        else:
            out.append(_unknown(repr(d), group=group, source=source))
    return out


# ---- 文件级：有界跟进 -------------------------------------------------------
class _Walk:
    """一次 `declared_intents()` 的账：读过几个文件、正在跟进的链（防环）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root_real = root.resolve(strict=False)
        self.files = 0
        self.seen: set[str] = set()

    def rel(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root_real).as_posix()
        except ValueError:
            return path.name

    def inside(self, path: Path) -> bool:
        try:
            real = path.resolve(strict=False)
        except OSError:
            return False
        return real == self.root_real or self.root_real in real.parents


def _read_declaration_file(
    walk: _Walk, path: Path, *, group: str, kind: str, chain: tuple[str, ...]
) -> list[DependencyIntent]:
    """读一份 requirements / constraints 文件并**有界**跟进 `-r` / `-c`。

    边界（每一条都有用例）：文件必须在项目根内（`_within` 同一判据：resolve 后仍在根下，
    软链接跳出去也算越界）；一次 walk 最多 `MAX_DECL_FILES` 个文件；同一条跟进链里再次
    出现的文件是环；缺失 / 越界 / 环 / 超限各记一条 unsupported **留在引用它的那一行的
    位置**，不是忽略。
    """
    key = os.path.normcase(str(path.resolve(strict=False)))
    source = walk.rel(path)
    if key in walk.seen:
        return []  # 同一次 walk 里同一个文件只读一次（两处 -r 同一份约束是常态，不是环）
    walk.seen.add(key)
    walk.files += 1
    text = _read_text(path)
    out: list[DependencyIntent] = []
    for line in _logical_lines(text):
        body = line.split("#", 1)[0].strip()
        m = _INCLUDE_RE.match(body)
        if not m:
            it = parse_intent(line, group=group, source=source, kind=kind)
            if it is not None:
                out.append(it)
            continue
        opt, target = m.group(1), m.group(2).strip()
        inc_kind = INTENT_KIND_CONSTRAINT if opt in ("-c", "--constraint") else kind
        if _ENV_VAR_RE.search(target) or _URL_RE.match(target):
            out.append(
                _unsupported(
                    line,
                    UNSUPPORTED_ENV_VARIABLE if "${" in target else UNSUPPORTED_DIRECT_URL,
                    group=group,
                    source=source,
                )
            )
            continue
        inc = (path.parent / target) if not os.path.isabs(target) else Path(target)
        if not walk.inside(inc):
            out.append(_unsupported(line, UNSUPPORTED_INCLUDE_OUTSIDE, group=group, source=source))
            continue
        inc_key = os.path.normcase(str(inc.resolve(strict=False)))
        if inc_key == key or inc_key in chain:
            out.append(_unsupported(line, UNSUPPORTED_INCLUDE_CYCLE, group=group, source=source))
            continue
        try:
            is_file = inc.is_file()
        except OSError:
            is_file = False
        if not is_file:
            out.append(_unsupported(line, UNSUPPORTED_INCLUDE_MISSING, group=group, source=source))
            continue
        if walk.files >= MAX_DECL_FILES:
            out.append(_unsupported(line, UNSUPPORTED_INCLUDE_LIMIT, group=group, source=source))
            continue
        # 被 include 的文件的条目归**引用它的组**：`requirements.txt` 里 `-r base.txt`，
        # base.txt 的条目就是 requirements.txt 这一组的一部分（pip 的语义）。
        out += _read_declaration_file(walk, inc, group=group, kind=inc_kind, chain=(*chain, key))
    return out


def declared_intents(figures_dir: str | Path, script: str | None = None) -> list[DependencyIntent]:
    """项目声明过的**全部**依赖意图（无损）：requirements 各文件（含有界跟进的 `-r` / `-c`）
    + constraints.txt + pyproject（PEP 621 / 735 / Poetry 表）+ 脚本的 PEP 723 + 管理器锁文件
    的存在（unsupported）。

    与 `project_declared()` 同一套目录范围与文件上限；只读。返回顺序 = 脚本 PEP 723 →
    目录由近到远、文件名排序、行序——稳定，可进指纹。
    """
    root = Path(figures_dir)
    walk = _Walk(root)
    out: list[DependencyIntent] = []
    if script:
        try:
            script_path = root / script
            if walk.inside(script_path) and script_path.is_file():
                out += pep723_intents(_read_text(script_path), source=Path(script).as_posix())
        except OSError:
            pass
    for directory in _decl_dirs(figures_dir, script):
        candidates: list[tuple[Path, str]] = []
        for pattern in REQUIREMENTS_GLOBS:
            try:
                candidates += [
                    (p, INTENT_KIND_REQUIREMENT) for p in sorted(directory.glob(pattern))
                ]
            except OSError:
                continue
        for name, kind in ((CONSTRAINTS_NAME, INTENT_KIND_CONSTRAINT), (PYPROJECT_NAME, "")):
            path = directory / name
            try:
                if path.is_file():
                    candidates.append((path, kind))
            except OSError:
                pass
        for name in MANAGER_LOCK_NAMES:
            path = directory / name
            try:
                if path.is_file():
                    out.append(
                        _unsupported(
                            name,
                            UNSUPPORTED_MANAGER_LOCK,
                            group=walk.rel(path),
                            source=walk.rel(path),
                        )
                    )
            except OSError:
                pass
        for path, kind in candidates:
            if walk.files >= MAX_DECL_FILES:
                out.append(
                    _unsupported(
                        walk.rel(path),
                        UNSUPPORTED_INCLUDE_LIMIT,
                        group=walk.rel(path),
                        source=walk.rel(path),
                    )
                )
                continue
            try:
                if path.name == PYPROJECT_NAME:
                    walk.files += 1
                    text = _read_text(path)
                    if text:
                        out += pyproject_intents(text, source=walk.rel(path))
                else:
                    out += _read_declaration_file(
                        walk, path, group=walk.rel(path), kind=kind, chain=()
                    )
            except (ValueError, TypeError, RecursionError) as exc:
                LOG.debug("依赖声明解析失败（忽略）: %s: %s", path, exc)
                continue
    return out


def default_group(group: str) -> bool:
    """这个组是不是**默认选中**的：任何层级的 `requirements.txt`、pyproject 主依赖、脚本
    自己的 PEP 723。其余要用户点名（FO20）。"""
    if not group:
        return False
    if group.startswith(f"{GROUP_PEP723}:"):
        return True
    if group.endswith(f":{GROUP_PYPROJECT_MAIN}"):
        return True
    return Path(group).name == "requirements.txt" and ":" not in group


def requirement_string(intent: DependencyIntent, *, with_marker: bool = False) -> str:
    """交给安装器的那一个字符串——**从解析后的结构重新序列化**，原文不进 argv。

    形状：`name[e1,e2]<spec>`（marker 默认不带：它已经按目标环境求过值，带上只是让 pip 再
    求一遍；`with_marker=True` 给诊断 / 约束文件用）。名字按 PEP 503 规范化、extras 排序、
    specifier 是 `SpecifierSet` 的规范串——同一条声明永远得到同一个字符串（可进指纹）。
    只接受 requirement / constraint 两档；别的抛 `ValueError`。
    """
    if not intent.declared:
        raise ValueError(f"不可序列化为安装需求: kind={intent.kind} raw={intent.raw!r}")
    if not _NAME_RE.match(intent.name):
        raise ValueError(f"包名不合形状: {intent.name!r}")
    extras = f"[{','.join(intent.extras)}]" if intent.extras else ""
    text = f"{intent.name}{extras}{intent.specifier}"
    if with_marker and intent.marker:
        text += f"; {intent.marker}"
    return text


def conflicts(intents: list[DependencyIntent]) -> list[dict]:
    """同名声明**确定**互斥的那些 → 逐名列出（不裁决、不放宽）。

    requirement 与 **constraint** 一起分组（`requirements.txt` 的 `tabulate==0.9.0` 与
    `constraints.txt` 的 `tabulate<0.9` 互相矛盾，只看 requirement 会把它漏掉——Codex #451
    P2）。U04 起用 `SpecifierSet` 判：任一条是精确 pin（`==` / `===`，不带通配）而另一条不含
    那个版本、或两条 pin 不同、或下界 ≥ 上界，就是冲突。判不出（`>=1` 与 `<3` 之类）不报
    ——真正的求解交给安装器，它会在安装前把 `ResolutionImpossible` 报回来（FO21 两条路都
    停在切 active 之前）。unknown / unsupported 的行没有可判的约束，不参与。
    """
    _requirements, _markers, specifiers, _utils, version_mod = _pkg()
    by_name: dict[str, list[DependencyIntent]] = {}
    for it in intents:
        if it.declared and it.name:
            by_name.setdefault(it.name, []).append(it)
    out = []
    for name, items in by_name.items():
        specs = sorted({it.specifier for it in items if it.specifier})
        if len(specs) < 2:
            continue
        reasons = _contradictions(specs, specifiers, version_mod)
        if reasons:
            out.append(
                {
                    "name": name,
                    "specifiers": specs,
                    "sources": [it.source for it in items],
                    "kinds": sorted({it.kind for it in items}),
                    "reasons": reasons,
                }
            )
    return out


def _contradictions(specs: list[str], specifiers, version_mod) -> list[str]:
    """两两判确定矛盾；回人读得懂的理由（空 = 判不出矛盾）。"""
    reasons: list[str] = []
    parsed = []
    for s in specs:
        try:
            parsed.append((s, specifiers.SpecifierSet(s)))
        except specifiers.InvalidSpecifier:
            continue

    def _pin(ss):
        for sp in ss:
            if sp.operator in ("==", "===") and "*" not in sp.version:
                return sp.version
        return None

    def _bounds(ss):
        lo = hi = None
        for sp in ss:
            try:
                v = version_mod.Version(sp.version.rstrip(".*"))
            except version_mod.InvalidVersion:
                continue
            if sp.operator in (">=", ">"):
                lo = max(lo, v) if lo else v
            elif sp.operator in ("<=", "<"):
                hi = min(hi, v) if hi else v
        return lo, hi

    for i, (sa, a) in enumerate(parsed):
        for sb, b in parsed[i + 1 :]:
            pa, pb = _pin(a), _pin(b)
            if pa and pb and pa != pb:
                reasons.append(f"{sa} 与 {sb} 是两个不同的精确版本")
            elif pa and not b.contains(pa, prereleases=True):
                reasons.append(f"{sa} 钉住的版本不满足 {sb}")
            elif pb and not a.contains(pb, prereleases=True):
                reasons.append(f"{sb} 钉住的版本不满足 {sa}")
            else:
                lo, hi = _bounds(specifiers.SpecifierSet(f"{sa},{sb}"))
                if lo is not None and hi is not None and lo >= hi:
                    reasons.append(f"{sa} 与 {sb} 的区间为空")
    return reasons


# ---------------------------------------------------------------------------
# 解析入口
# ---------------------------------------------------------------------------
def resolve(
    figures_dir: str | Path, import_name: str, script: str | None = None
) -> DependencyRequirement | None:
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
                import_name=import_name,
                distribution=candidate,
                specifier=declared[key],
                resolution_source=SOURCE_PROJECT_DECLARED,
                confidence=CONFIDENCE_HIGH,
            )
    if curated:
        return DependencyRequirement(
            import_name=import_name,
            distribution=curated,
            specifier="",
            resolution_source=SOURCE_CURATED,
            confidence=CONFIDENCE_HIGH,
        )
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
        distribution=name,
        specifier=spec,
        resolution_source=SOURCE_USER_SPECIFIED,
        confidence=CONFIDENCE_HIGH,
    )
