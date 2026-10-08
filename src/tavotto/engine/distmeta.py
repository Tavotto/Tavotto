"""已安装发行包的**静态**元数据读取与 import 名 → 发行包反查（Import Origin Resolver PR2，ADR 0019 / 0061 / 0114）。

回答的问题：「这个环境的 site-packages 里，谁提供了顶级模块 `docx`？」——**不起这个环境的解释器**。
`depplan._FACTS_SRC` / `deprepair.inventory` 在目标解释器子进程里用 `importlib.metadata` 读「发行包名 → 版本」，
那是**用户发起的检查**（会执行，授权路径）；本模块是它的**静态对偶**：只读 site-packages 里的文本文件，
默认扫描可以用，子进程数恒为 0。两者的发行包身份键同为 PEP 503 规范化名（`depresolve.normalize_distribution`），
本模块只补 `_FACTS_SRC` 不读的 `top_level.txt` / `RECORD` / `direct_url.json` / editable finder。

## 零执行边界（`tests/test_import_origin_metadata.py` 钉着，不是约定）

* 只用 `os.scandir` / `os.lstat` / `scanbudget.read_regular_text`（普通文件、字节上限、`no_follow`、占位文件不读）；
  不 import / exec / eval 任何站点里的东西，不调 `importlib.metadata` / `find_spec`，不起子进程，不联网；
* `.pth` 只当**文本**解析：`import …` 行一律不执行，只从 `__editable___*_finder` 这个名字去读**同一个
  site-packages 里**的 finder 源码，且用 `ast.parse` + `ast.literal_eval` 取 `MAPPING` 字面量（不是 exec）；
  路径行（指向 site-packages 之外）**不跟进、不 stat**，只计数——数量非零时「没查到」不再等于「没装」；
* 读的范围：环境前缀下的 `pyvenv.cfg`、`lib/pythonX.Y/site-packages`（Windows `Lib/site-packages`）、`conda-meta/*.json`
  （Conda 环境，只读 `name` / `version` / `files`）。可选的基础解释器 site-packages（`include_base`，默认关）
  只在调用方明确要求且 `pyvenv.cfg` 声明 `include-system-site-packages = true` 时才列；
* 环境前缀在项目内时，调用方传 `project_root`：前缀的任何一级是符号链接 / junction 就整个拒绝（先 `lstat`，不碰目标）；
* 预算走 `scanbudget`（目录项数、累计字节、墙钟、占位文件、符号链接）；超了留痕在 `Index.issues`，
  `Index.complete=False`——**「没读全」不许被当成「没装」**（`environment_not_checked`）。

## 证据优先级（`resolve_module`，计划 P2.3）

已检查环境里**能对应到具体模块位置**的已安装证据（`top_level.txt` / `RECORD` / editable finder 的 `MAPPING` /
`conda-meta` 的 `files`）> 项目声明 > curated（`depresolve`）。`user_specified` 来自用户在确认界面的显式输入，
扫描阶段没有这个输入，PR4/PR5 才会出现。**已安装元数据只是证据，不是安装授权**：本模块不 import `deprepair`，不产出
`DependencyRequirement`，也不往 `depresolve.INSTALLABLE_SOURCES` 加东西；editable / 本地路径 / 本地 wheel / VCS / URL /
Conda 专有的提供者一律 `reproducible=False`，状态说明白「不可重现」，**不转成 `pip install 包名`**，
本地 editable 包也**不**映射到同名 PyPI 包。声明与已装版本冲突只报告（`distribution_version_conflict`），不覆盖任何一边。

纯标准库 + `depresolve`（名字规范化 / curated 表 / 版本约束判断）+ `scanbudget`（叶子）。
"""

from __future__ import annotations

import ast
import csv
import io
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from . import depresolve, scanbudget

# ---- 来源（direct_url.json / INSTALLER 推出；PEP 610） ----------------------------------------
PROV_INDEX = "index"  # 没有 direct_url.json（PEP 610：从索引装的没有它）；不能证明是 PyPI，只是没有相反的记录
PROV_EDITABLE = "editable"
PROV_LOCAL_PATH = "local_path"
PROV_LOCAL_ARCHIVE = "local_archive"  # 本地 wheel / sdist（file: URL 的 archive_info）
PROV_VCS = "vcs"
PROV_URL = "url"  # 远程 URL 的归档，或 direct_url.json 读不懂
PROV_CONDA = "conda"
PROVENANCES = (
    PROV_INDEX,
    PROV_EDITABLE,
    PROV_LOCAL_PATH,
    PROV_LOCAL_ARCHIVE,
    PROV_VCS,
    PROV_URL,
    PROV_CONDA,
)
#: 只有这一种来源可以由「PyPI 名 + 版本」重现；其余都不许被转成 requirement。
REPRODUCIBLE_PROVENANCES = frozenset({PROV_INDEX})

ECO_PYPI = "pypi"
ECO_CONDA = "conda"

# ---- 候选的证据码（闭集；不带路径） -------------------------------------------------------------
EV_TOP_LEVEL = "installed_top_level_txt"
EV_RECORD = "installed_record_files"
EV_FINDER = "installed_editable_finder"
EV_CONDA_META = "installed_conda_meta_files"
EV_NAME_ONLY = (
    "installed_metadata_name_only"  # 元数据没有模块证据，只有发行包名与 import 名相同：未确认
)
EV_ORPHAN = "module_present_no_metadata"  # site-packages 里有这个模块，却没有任何发行包认领它
EV_MULTIPLE = "multiple_distribution_candidates"
EVIDENCE_CODES = frozenset(
    {EV_TOP_LEVEL, EV_RECORD, EV_FINDER, EV_CONDA_META, EV_NAME_ONLY, EV_ORPHAN, EV_MULTIPLE}
)

# ---- 候选来源 ---------------------------------------------------------------------------------
SRC_INSTALLED = "installed"
SRC_DECLARED = "project_declared"
SRC_CURATED = "curated"

# ---- 结论（`Resolution.status`；稳定码，计划「错误状态区分」的子集） --------------------------------
ST_NOT_CHECKED = ""  # 没给索引：什么都没量
ST_CONFIRMED = "installed_confirmed"
ST_AMBIGUOUS = "module_origin_ambiguous"
ST_EDITABLE = "editable_dependency_not_reproducible"
ST_NOT_REPRODUCIBLE = "installed_source_not_reproducible"  # 本地路径 / 本地 wheel / VCS / URL
ST_CONDA = "conda_package_not_pypi"
ST_UNVERIFIED = "unverified"
ST_VERSION_CONFLICT = "distribution_version_conflict"
ST_NOT_INSTALLED = "not_installed"
ST_ENV_NOT_CHECKED = "environment_not_checked"
STATUSES = (
    ST_CONFIRMED,
    ST_AMBIGUOUS,
    ST_EDITABLE,
    ST_NOT_REPRODUCIBLE,
    ST_CONDA,
    ST_UNVERIFIED,
    ST_VERSION_CONFLICT,
    ST_NOT_INSTALLED,
    ST_ENV_NOT_CHECKED,
)

# ---- 结论的种类（给 importscan 映射到 origin_kind / resolution_status，避免两边各写一份字符串） ------
KIND_NONE = "none"  # 没有已安装证据（含「没检查」）
KIND_CONFIRMED = "confirmed"
KIND_EDITABLE = "editable"
KIND_UNSUPPORTED = "unsupported"
KIND_AMBIGUOUS = "ambiguous"
KIND_UNVERIFIED = "unverified"

# ---- 兼容性码（闭集） -------------------------------------------------------------------------
COMPAT_CODES = frozenset(
    {
        "declared_constraint_satisfied",
        "declared_version_conflict",
        "declared_constraint_unchecked",
        "declared_dist_differs_from_observed",
        "curated_dist_differs_from_observed",
        "declared_matches_installed_candidate",
        "declared_matches_candidate",
        "installed_by_conda_not_pip",
        "shadowed_by_higher_priority_layer",
        "multiple_versions_installed",
        "metadata_incomplete",
        "path_entries_not_followed",
        "metadata_scan_incomplete",
    }
)

# ---- 上限 -------------------------------------------------------------------------------------
MAX_METADATA_BYTES = 256 * 1024  # METADATA 常带长描述；超了退回目录名，留痕
MAX_SMALL_BYTES = 64 * 1024  # top_level.txt / direct_url.json / .pth / pyvenv.cfg / INSTALLER
MAX_FINDER_BYTES = 512 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_RECORD_ROWS = 200_000
MAX_CONDA_META_BYTES = 8 * 1024 * 1024
MAX_CONDA_META_FILES = 4000
#: 默认预算（调用方没给 `Budget` 时）：比源码扫描宽——一个科研环境几百个发行包、每个读五个小文件。
DEFAULT_LIMITS = scanbudget.Limits(
    max_entries=400_000,
    max_file_bytes=MAX_RECORD_BYTES,
    max_source_bytes=192 * 1024 * 1024,
    max_seconds=15.0,
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_*-]{0,63}$")
_PYDIR_RE = re.compile(r"^python3\.\d{1,2}t?$")
_EXT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z0-9_-]+)*\.(?:so|pyd)$")
_FINDER_RE = re.compile(r"\b(__editable___[A-Za-z0-9_]+_finder)\b")
_SKIP_ENTRIES = ("__pycache__",)


# ---------------------------------------------------------------- 数据


@dataclass(frozen=True)
class SiteRoot:
    """一个 site-packages 目录。`path` 只在内存里用；`rel` 是相对环境前缀的 POSIX 路径（公开、不带绝对路径）。"""

    path: str
    rel: str
    layer: str  # "env" | "base"
    order: int  # sys.path 上的先后（小的先）


@dataclass(eq=False)
class _Dist:
    """一个已安装的发行包（内部，可变：editable finder 之后补模块证据）。"""

    name: str
    key: str
    version: str
    kind: str  # "dist-info" | "egg-info" | "egg-link" | "pth" | "conda-meta"
    ecosystem: str
    provenance: str
    root: int
    entry: (
        str  # site-packages 之下的条目名（dist-info 目录名等）；conda 为 "conda-meta/<name>.json"
    )
    modules: dict[str, set[str]] = field(default_factory=dict)  # 顶级模块 → 证据码
    regular: set[str] = field(default_factory=set)  # RECORD 里有 `<top>/__init__.py` 的顶级包
    metadata_ok: bool = True


@dataclass(frozen=True)
class Candidate:
    """一个提供者候选（公开形状见 `to_payload`）。"""

    distribution: str
    version: str
    ecosystem: str
    provenance: str
    evidence: tuple[str, ...]
    evidence_file: str
    reproducible: bool
    shadowed: bool
    namespace: bool = False
    duplicate_versions: bool = False
    metadata_ok: bool = True
    order: int = 0

    @property
    def key(self) -> str:
        return depresolve.normalize_distribution(self.distribution)

    def to_payload(self, source: str = SRC_INSTALLED) -> dict:
        return {
            "distribution": self.distribution,
            "version": self.version,
            "source": source,
            "ecosystem": self.ecosystem,
            "provenance": self.provenance,
            "evidence": list(self.evidence),
            "evidence_file": self.evidence_file,
            "reproducible": self.reproducible,
            "shadowed": self.shadowed,
        }


@dataclass(frozen=True)
class Lookup:
    candidates: tuple[Candidate, ...]
    module_present: bool  # site-packages 目录清单里有这个名字的包 / 模块 / 扩展
    complete: bool
    uncovered_paths: int


class Index:
    """一个环境的 site-packages 元数据索引（构建后只读）。"""

    def __init__(
        self, roots: Sequence[SiteRoot], dists: list[_Dist], names: list[set[str]]
    ) -> None:
        self.roots = tuple(roots)
        self._dists = dists
        self._names = names  # 每个 root 的目录清单里的顶级名
        self.issues: tuple[dict, ...] = ()
        self.complete = True
        self.uncovered_paths = 0  # 没跟进的 `.pth` 路径行（指向 site-packages 之外）
        self.checked = True
        self._by_module: dict[str, list[_Dist]] = {}
        self._name_only: dict[str, list[_Dist]] = {}
        for d in dists:
            for mod in d.modules:
                self._by_module.setdefault(mod, []).append(d)
            if not d.modules:
                self._name_only.setdefault(d.key, []).append(d)

    @property
    def distribution_count(self) -> int:
        return len(self._dists)

    def to_payload(self) -> dict:
        """公开投影：只有计数与账本（项目 / 环境相对路径），没有绝对路径、没有元数据内容。"""
        return {
            "site_roots": len(self.roots),
            "distributions": len(self._dists),
            "complete": self.complete,
            "uncovered_path_entries": self.uncovered_paths,
            "issues": [dict(i) for i in self.issues],
        }

    def lookup(self, module: str) -> Lookup:
        found: dict[tuple[int, str, str], list[_Dist]] = {}
        for d in self._by_module.get(module, ()):
            found.setdefault((d.root, d.ecosystem, d.key), []).append(d)
        # 没有模块证据的发行包：只有名字对得上、且模块真在目录清单里（或它是 editable，模块本来就在 site-packages 之外）
        norm = depresolve.normalize_distribution(module)
        for d in self._name_only.get(norm, ()):
            on_disk = module in self._names[d.root]
            if on_disk or d.provenance == PROV_EDITABLE:
                found.setdefault((d.root, d.ecosystem, d.key), []).append(d)
        # Conda 的 conda-meta 记录与同一 root 里别的（有 dist-info 的）提供者重叠时让位
        covered_roots = {r for (r, eco, _k) in found if eco != ECO_CONDA}
        groups = {
            k: v for k, v in found.items() if not (k[1] == ECO_CONDA and k[0] in covered_roots)
        }
        if groups:
            best = min(k[0] for k in groups)
        else:
            best = 0
        cands: list[Candidate] = []
        for (root, _eco, key), ds in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][2])):
            cands.append(self._merge(ds, module, shadowed=root > best, order=root))
        present = any(module in names for names in self._names)
        return Lookup(tuple(cands), present, self.complete, self.uncovered_paths)

    def _merge(self, ds: list[_Dist], module: str, *, shadowed: bool, order: int) -> Candidate:
        first = ds[0]
        versions = sorted({d.version for d in ds if d.version})
        evidence: set[str] = set()
        for d in ds:
            evidence |= d.modules.get(module, set())
        if not evidence:
            evidence = {EV_NAME_ONLY}
        # 本模块在这个发行包里的所有提供者都没有 `<top>/__init__.py` 时，才说它是命名空间式的
        namespace = EV_RECORD in evidence and module not in set().union(*(d.regular for d in ds))
        provenance = first.provenance
        for d in ds:
            if d.provenance != PROV_INDEX:
                provenance = d.provenance
        root_rel = self.roots[first.root].rel
        file_rel = "/".join(p for p in (root_rel, first.entry) if p)
        return Candidate(
            distribution=first.name,
            version=versions[0] if len(versions) == 1 else "",
            ecosystem=first.ecosystem,
            provenance=provenance,
            evidence=tuple(sorted(evidence)),
            evidence_file=file_rel,
            reproducible=provenance in REPRODUCIBLE_PROVENANCES,
            shadowed=shadowed,
            namespace=namespace,
            duplicate_versions=len(versions) > 1,
            metadata_ok=all(d.metadata_ok for d in ds),
            order=order,
        )


# ---------------------------------------------------------------- 布局：从环境前缀静态推 site-packages


def _parse_cfg(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith(("#", ";")):
            continue
        k, _, v = line.partition("=")
        out[k.strip().lower()] = v.strip()
    return out


def _is_real_dir(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not scanbudget.is_redirect(st)


def _layout_dirs(prefix: str, version_hint: str) -> list[tuple[str, str]]:
    """`prefix` 之下按已知布局推出的 site-packages：[(绝对路径, 相对前缀的 POSIX 路径)]。"""
    out: list[tuple[str, str]] = []
    win = os.path.join(prefix, "Lib", "site-packages")
    if _is_real_dir(os.path.join(prefix, "Lib")) and _is_real_dir(win):
        out.append((win, "Lib/site-packages"))
    lib = os.path.join(prefix, "lib")
    if _is_real_dir(lib):
        try:
            names = sorted(n for n in os.listdir(lib) if _PYDIR_RE.match(n))
        except OSError:
            names = []
        # pyvenv.cfg 的 version 在前
        names.sort(key=lambda n: (not (version_hint and n.startswith(f"python{version_hint}")), n))
        for n in names:
            sp = os.path.join(lib, n, "site-packages")
            if _is_real_dir(os.path.join(lib, n)) and _is_real_dir(sp):
                seen = {os.path.normcase(p) for p, _ in out}
                if os.path.normcase(sp) not in seen:
                    out.append((sp, f"lib/{n}/site-packages"))
    return out


def site_packages(
    prefix: str | os.PathLike,
    *,
    project_root: str | os.PathLike | None = None,
    include_base: bool = False,
    budget: scanbudget.Budget | None = None,
) -> list[SiteRoot]:
    """环境前缀（venv 目录 / Conda 前缀）→ 它的 site-packages 目录列表，**只读磁盘布局，不起解释器**。

    `project_root`：前缀在项目里时传它——前缀的任何一级是符号链接 / junction 就整个拒绝（留痕，不碰目标）。
    `include_base`：为真且 `pyvenv.cfg` 写了 `include-system-site-packages = true` 时，再列 `home` 指向的基础
    解释器的 site-packages（层 "base"，排在环境层之后）；默认关——`home` 是 `pyvenv.cfg` 里的字符串，
    来自项目的环境不应该被它牵着去读项目之外的目录。
    """
    budget = budget if budget is not None else scanbudget.Budget(limits=DEFAULT_LIMITS)
    pre = os.path.normpath(os.fspath(prefix))
    if project_root is not None:
        redirected = scanbudget.redirected_component(project_root, pre)
        if redirected is not None:
            budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="env", path=redirected)
            return []
    cfg: dict[str, str] = {}
    try:
        cfg = _parse_cfg(
            scanbudget.read_regular_text(
                pre, "pyvenv.cfg", no_follow=True, max_bytes=MAX_SMALL_BYTES
            )
        )
    except FileNotFoundError:
        pass
    except OSError:
        budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="env", path="pyvenv.cfg")
    version = cfg.get("version_info") or cfg.get("version") or ""
    hint = ".".join(version.split(".")[:2]) if version else ""
    roots = [
        SiteRoot(path=p, rel=rel, layer="env", order=i)
        for i, (p, rel) in enumerate(_layout_dirs(pre, hint))
    ]
    if include_base and cfg.get("include-system-site-packages", "").lower() == "true":
        home = cfg.get("home", "")
        if home and os.path.isabs(home):
            for base in (os.path.dirname(os.path.normpath(home)), os.path.normpath(home)):
                layout = _layout_dirs(base, hint)
                if layout:
                    for p, _rel in layout:
                        roots.append(SiteRoot(path=p, rel="base", layer="base", order=len(roots)))
                    break
    return roots


# ---------------------------------------------------------------- 读


class _Reader:
    def __init__(self, roots: Sequence[SiteRoot], budget: scanbudget.Budget) -> None:
        self.roots = list(roots)
        self.budget = budget
        self.complete = True
        self.uncovered = 0

    def note(self, code: str, root: SiteRoot, rel: str, *, scope: str = "file") -> None:
        self.budget.note(code, scope=scope, path="/".join(p for p in (root.rel, rel) if p))

    def read(self, root: SiteRoot, *parts: str, max_bytes: int) -> str | None:
        """读 site-packages 之下的一个文件。不存在回 None（不留痕）；读不了 / 超限 / 占位 / 链接留痕回 None。"""
        full = os.path.join(root.path, *parts)
        try:
            st = os.lstat(full)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            self.note(scanbudget.ISSUE_UNREADABLE_FILE, root, "/".join(parts))
            return None
        rel = "/".join(parts)
        if scanbudget.is_redirect(st):
            self.note(scanbudget.ISSUE_SYMLINK_DIR, root, rel)
            return None
        if not stat.S_ISREG(st.st_mode):
            self.note(scanbudget.ISSUE_UNREADABLE_FILE, root, rel)
            return None
        if scanbudget.is_placeholder(st):
            self.note(scanbudget.ISSUE_PLACEHOLDER, root, rel)
            return None
        if st.st_size > max_bytes:
            self.note(scanbudget.ISSUE_TOO_LARGE, root, rel)
            return None
        if self.budget.stop_reason() is not None:
            self.complete = False
            return None
        refused = self.budget.charge_source(st.st_size)
        if refused is not None:
            self.note(refused, root, rel)
            self.complete = False
            return None
        try:
            return scanbudget.read_regular_text(
                root.path, *parts, no_follow=True, max_bytes=max_bytes
            )
        except OSError:
            self.note(scanbudget.ISSUE_UNREADABLE_FILE, root, rel)
            return None


def _safe_name(text: str) -> str:
    text = (text or "").strip()
    return text if _NAME_RE.match(text) else ""


def _safe_version(text: str) -> str:
    text = (text or "").strip()
    return text if _VERSION_RE.match(text) else ""


def _headers(text: str) -> tuple[str, str]:
    """METADATA / PKG-INFO 头部的 Name 与 Version（读到第一个空行为止）。"""
    name = version = ""
    for line in text.splitlines():
        if not line.strip():
            break
        k, sep, v = line.partition(":")
        if not sep:
            continue
        k = k.strip().lower()
        if k == "name" and not name:
            name = _safe_name(v)
        elif k == "version" and not version:
            version = _safe_version(v)
    return name, version


def _top_level_names(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        first = line.strip().replace("\\", "/").split("/")[0]
        if _IDENT_RE.match(first):
            out.add(first)
    return out


def _record_tops(text: str) -> tuple[set[str], set[str]] | None:
    """RECORD → (顶级模块名, 其中有 `__init__.py` 的顶级包)。读不懂回 None。"""
    tops: set[str] = set()
    regular: set[str] = set()
    try:
        for i, row in enumerate(csv.reader(io.StringIO(text))):
            if i >= MAX_RECORD_ROWS:
                break
            if not row:
                continue
            path = row[0].replace("\\", "/")
            parts = path.split("/")
            # `../../bin/x`、`/abs/x`、`C:/x` 这类装到 site-packages 之外的（脚本、数据）首段不是标识符，
            # 下面的 `_IDENT_RE` 自然把它们挡掉——不另写一条会漂移的前缀判据
            first = parts[0]
            if first in _SKIP_ENTRIES or first.endswith(
                (".dist-info", ".data", ".egg-info", ".pth")
            ):
                continue
            if len(parts) > 1:
                if _IDENT_RE.match(first):
                    tops.add(first)
                    if parts[1] == "__init__.py":
                        regular.add(first)
                continue
            if first.endswith(".py") and _IDENT_RE.match(first[:-3]):
                tops.add(first[:-3])
                continue
            m = _EXT_RE.match(first)
            if m:
                tops.add(m.group(1))
    except csv.Error:
        return None
    return tops, regular


def _direct_url(text: str | None) -> str:
    """direct_url.json → 来源码（PEP 610）。没有这个文件 = `PROV_INDEX`；读不懂 = `PROV_URL`（保守：不可重现）。"""
    if text is None:
        return PROV_INDEX
    try:
        data = json.loads(text)
    except ValueError:
        return PROV_URL
    if not isinstance(data, dict):
        return PROV_URL
    if "vcs_info" in data:
        return PROV_VCS
    info = data.get("dir_info")
    if isinstance(info, dict):
        return PROV_EDITABLE if info.get("editable") is True else PROV_LOCAL_PATH
    if "archive_info" in data:
        url = str(data.get("url", ""))
        return PROV_LOCAL_ARCHIVE if url.startswith("file:") else PROV_URL
    return PROV_URL


def _finder_mapping(text: str) -> set[str]:
    """setuptools editable finder 源码里 `MAPPING` 字面量的键（顶级模块名）。`ast.parse` + `literal_eval`，不执行。"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return set()
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MAPPING" for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "MAPPING"
        ):
            value = node.value
        if value is None:
            continue
        try:
            mapping = ast.literal_eval(value)
        except (ValueError, SyntaxError, RecursionError, MemoryError):
            return set()
        if isinstance(mapping, dict):
            out = set()
            for k in mapping:
                first = str(k).split(".")[0]
                if _IDENT_RE.match(first):
                    out.add(first)
            return out
    return set()


def _list_root(
    rd: _Reader, root: SiteRoot
) -> tuple[list[str], list[str], list[str], list[str], set[str]]:
    """列 site-packages 一层：dist-info、egg-info、egg-link、pth、顶级模块名。"""
    dist_infos: list[str] = []
    egg_infos: list[str] = []
    egg_links: list[str] = []
    pths: list[str] = []
    names: set[str] = set()
    try:
        it = os.scandir(root.path)
    except OSError:
        rd.note(scanbudget.ISSUE_UNREADABLE_DIR, root, "", scope="dir")
        return dist_infos, egg_infos, egg_links, pths, names
    with it:
        for e in it:
            if not rd.budget.charge_entry():
                rd.complete = False
                break
            name = e.name
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                rd.note(scanbudget.ISSUE_UNREADABLE_FILE, root, name)
                continue
            redirect = scanbudget.is_redirect(st)
            if name.endswith((".dist-info", ".egg-info")):
                if redirect:
                    rd.note(scanbudget.ISSUE_SYMLINK_DIR, root, name)
                    continue
                (dist_infos if name.endswith(".dist-info") else egg_infos).append(name)
            elif name.endswith(".egg-link"):
                if not redirect:
                    egg_links.append(name)
            elif name.endswith(".pth"):
                if not redirect:
                    pths.append(name)
            elif redirect or name in _SKIP_ENTRIES or name.endswith(".data"):
                continue
            elif stat.S_ISDIR(st.st_mode):
                if _IDENT_RE.match(name):
                    names.add(name)
            elif name.endswith(".py") and _IDENT_RE.match(name[:-3]):
                names.add(name[:-3])
            else:
                m = _EXT_RE.match(name)
                if m:
                    names.add(m.group(1))
    return sorted(dist_infos), sorted(egg_infos), sorted(egg_links), sorted(pths), names


def _read_dist_info(rd: _Reader, root: SiteRoot, entry: str, kind: str) -> _Dist | None:
    stem = entry[: -len(".dist-info" if kind == "dist-info" else ".egg-info")]
    dir_name, _, dir_version = stem.rpartition("-")
    meta_file = "METADATA" if kind == "dist-info" else "PKG-INFO"
    meta = rd.read(root, entry, meta_file, max_bytes=MAX_METADATA_BYTES)
    name = version = ""
    ok = True
    if meta is not None:
        name, version = _headers(meta)
    if not name:
        # 元数据读不到 / 没有 Name：退回目录名，并标出「元数据不完整」（不装作读过）
        name = _safe_name(dir_name or stem)
        version = version or _safe_version(dir_version)
        ok = False
    if not name:
        rd.note(scanbudget.ISSUE_PARSE_FAILED, root, entry)
        return None
    d = _Dist(
        name=name,
        key=depresolve.normalize_distribution(name),
        version=version,
        kind=kind,
        ecosystem=ECO_PYPI,
        provenance=PROV_INDEX,
        root=root.order,
        entry=entry,
        metadata_ok=ok,
    )
    top = rd.read(root, entry, "top_level.txt", max_bytes=MAX_SMALL_BYTES)
    if top is not None:
        for m in _top_level_names(top):
            d.modules.setdefault(m, set()).add(EV_TOP_LEVEL)
    if kind == "dist-info":
        rec = rd.read(root, entry, "RECORD", max_bytes=MAX_RECORD_BYTES)
        if rec is not None:
            parsed = _record_tops(rec)
            if parsed is None:
                rd.note(scanbudget.ISSUE_PARSE_FAILED, root, f"{entry}/RECORD")
            else:
                for m in parsed[0]:
                    d.modules.setdefault(m, set()).add(EV_RECORD)
                d.regular |= parsed[1]
        d.provenance = _direct_url(
            rd.read(root, entry, "direct_url.json", max_bytes=MAX_SMALL_BYTES)
        )
        installer = rd.read(root, entry, "INSTALLER", max_bytes=1024)
        if (
            installer is not None
            and installer.strip().lower() == "conda"
            and d.provenance == PROV_INDEX
        ):
            d.provenance = PROV_CONDA
    return d


def _read_conda_meta(rd: _Reader, prefix: str, roots: Sequence[SiteRoot]) -> list[_Dist]:
    """Conda 前缀的 `conda-meta/*.json`：只取 name / version / files 里落在 site-packages 之下的顶级名。
    名字是 **Conda 包名**，不是 PyPI 名（`ecosystem=conda`）——永远不转成 requirement。"""
    meta_dir = os.path.join(prefix, "conda-meta")
    if not _is_real_dir(meta_dir):
        return []
    try:
        names = sorted(n for n in os.listdir(meta_dir) if n.endswith(".json"))
    except OSError:
        rd.budget.note(scanbudget.ISSUE_UNREADABLE_DIR, scope="env", path="conda-meta")
        return []
    out: list[_Dist] = []
    fake_root = SiteRoot(path=prefix, rel="", layer="env", order=0)
    prefixes = [(r, r.rel + "/") for r in roots if r.layer == "env" and r.rel != "base"]
    for n in names[:MAX_CONDA_META_FILES]:
        if not rd.budget.charge_entry():
            rd.complete = False
            break
        text = rd.read(fake_root, "conda-meta", n, max_bytes=MAX_CONDA_META_BYTES)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            rd.note(scanbudget.ISSUE_PARSE_FAILED, fake_root, f"conda-meta/{n}")
            continue
        if not isinstance(data, dict):
            continue
        name = _safe_name(str(data.get("name", "")))
        files = data.get("files")
        if not name or not isinstance(files, list):
            continue
        by_root: dict[int, _Dist] = {}
        for f in files:
            if not isinstance(f, str):
                continue
            p = f.replace("\\", "/")
            for root, pre in prefixes:
                if not p.startswith(pre):
                    continue
                parts = p[len(pre) :].split("/")
                first = parts[0]
                top = ""
                if len(parts) > 1 and _IDENT_RE.match(first):
                    top = first
                elif first.endswith(".py") and _IDENT_RE.match(first[:-3]):
                    top = first[:-3]
                else:
                    m = _EXT_RE.match(first)
                    top = m.group(1) if m else ""
                if not top:
                    break
                d = by_root.get(root.order)
                if d is None:
                    d = by_root[root.order] = _Dist(
                        name=name,
                        key=depresolve.normalize_distribution(name),
                        version=_safe_version(str(data.get("version", ""))),
                        kind="conda-meta",
                        ecosystem=ECO_CONDA,
                        provenance=PROV_CONDA,
                        root=root.order,
                        entry=f"conda-meta/{n}",
                    )
                d.modules.setdefault(top, set()).add(EV_CONDA_META)
                break
        out.extend(by_root.values())
    return out


def _apply_pth(rd: _Reader, root: SiteRoot, pths: list[str], dists: list[_Dist]) -> None:
    """`.pth` 当文本：`import` 行不执行；`__editable___*_finder` 读同一目录里的 finder 源码取 MAPPING；
    路径行不跟进（计数）。"""
    by_key = {
        d.key: d
        for d in dists
        if d.root == root.order and d.ecosystem == ECO_PYPI and d.kind != "pth"
    }
    for pth in pths:
        text = rd.read(root, pth, max_bytes=MAX_SMALL_BYTES)
        if text is None:
            continue
        finders: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("import ", "import\t")):
                finders += _FINDER_RE.findall(line)
            else:
                rd.uncovered += 1
        if not pth.startswith("__editable__."):
            continue
        stem = pth[len("__editable__.") : -len(".pth")]
        dist_name = stem.rpartition("-")[0] or stem
        key = depresolve.normalize_distribution(dist_name)
        mods: set[str] = set()
        for f in dict.fromkeys(finders):
            src = rd.read(root, f + ".py", max_bytes=MAX_FINDER_BYTES)
            if src is not None:
                mods |= _finder_mapping(src)
        d = by_key.get(key)
        if d is None:
            safe = _safe_name(dist_name)
            if not safe:
                continue
            d = _Dist(
                name=safe,
                key=key,
                version=_safe_version(stem.rpartition("-")[2]),
                kind="pth",
                ecosystem=ECO_PYPI,
                provenance=PROV_EDITABLE,
                root=root.order,
                entry=pth,
                metadata_ok=False,
            )
            dists.append(d)
        # `__editable__.*.pth` 本身就是 editable 的证据——即使 direct_url.json 缺失
        d.provenance = PROV_EDITABLE
        for m in mods:
            d.modules.setdefault(m, set()).add(EV_FINDER)


def build_index(
    roots: Sequence[SiteRoot],
    *,
    prefix: str | os.PathLike | None = None,
    budget: scanbudget.Budget | None = None,
) -> Index:
    """读一组 site-packages 的元数据，建 import 名 → 发行包的反查。`prefix` 给了且有 `conda-meta/` 就再读它。

    预算用完 / 读不动的目录 / 没跟进的 `.pth` 路径行都会让 `Index.complete` 或 `uncovered_paths` 说实话。
    """
    budget = budget if budget is not None else scanbudget.Budget(limits=DEFAULT_LIMITS)
    rd = _Reader(roots, budget)
    dists: list[_Dist] = []
    names: list[set[str]] = []
    for root in roots:
        dist_infos, egg_infos, egg_links, pths, mods = _list_root(rd, root)
        names.append(mods)
        for entry in dist_infos:
            d = _read_dist_info(rd, root, entry, "dist-info")
            if d is not None:
                dists.append(d)
        for entry in egg_infos:
            d = _read_dist_info(rd, root, entry, "egg-info")
            if d is not None:
                dists.append(d)
        for link in egg_links:
            # 老式 develop 安装：只用文件名（不读内容——它指向 site-packages 之外）
            name = _safe_name(link[: -len(".egg-link")])
            if name:
                dists.append(
                    _Dist(
                        name=name,
                        key=depresolve.normalize_distribution(name),
                        version="",
                        kind="egg-link",
                        ecosystem=ECO_PYPI,
                        provenance=PROV_EDITABLE,
                        root=root.order,
                        entry=link,
                        metadata_ok=False,
                    )
                )
        _apply_pth(rd, root, pths, dists)
    if prefix is not None:
        dists += _read_conda_meta(rd, os.path.normpath(os.fspath(prefix)), roots)
    index = Index(roots, dists, names)
    index.complete = rd.complete and budget.stopped is None
    index.uncovered_paths = rd.uncovered
    index.issues = tuple(budget.issues())
    return index


def index_environment(
    prefix: str | os.PathLike,
    *,
    project_root: str | os.PathLike | None = None,
    include_base: bool = False,
    budget: scanbudget.Budget | None = None,
) -> Index:
    """便捷入口：环境前缀 → 布局 → 索引。前缀不存在 / 没有 site-packages = `checked=False`（没量，不是「没装」）。"""
    budget = budget if budget is not None else scanbudget.Budget(limits=DEFAULT_LIMITS)
    roots = site_packages(
        prefix, project_root=project_root, include_base=include_base, budget=budget
    )
    index = build_index(roots, prefix=prefix, budget=budget) if roots else Index((), [], [])
    if not roots:
        index.checked = False
        index.complete = False
        index.issues = tuple(budget.issues())
    return index


# ---------------------------------------------------------------- 反查 + 优先级


@dataclass(frozen=True)
class Resolution:
    """一个顶级 import 名的发行包结论。字段与 `importscan.ImportClass` 的可选字段一一对应。"""

    kind: str = KIND_NONE
    status: str = ST_NOT_CHECKED
    candidates: tuple[dict, ...] = ()
    selected: str = ""  # 可由 PyPI 名重现的唯一提供者；其余为空（身份看 observed_distribution）
    observed_distribution: str = ""
    observed_version: str = ""
    declared_requirement: str = ""
    declared_constraint: str = ""
    provenance: str = ""
    compatibility: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    editable: bool = False


def _dedupe(seq):
    return list(dict.fromkeys(seq))


def resolve_module(
    index: Index | None,
    module: str,
    *,
    declared: Mapping[str, str],
    curated: str = "",
    alternatives: Sequence[str] = (),
) -> Resolution:
    """已安装证据 > 项目声明 > curated 的综合结论；**只观测**。

    `declared`：规范化发行包名 → 版本约束（`depresolve.project_declared`）；`curated`：`depresolve` 的单一映射
    （没有回 ""）；`alternatives`：`depresolve.distribution_alternatives`（`cv2` 这类多发行包）。
    """
    alt_keys = [depresolve.normalize_distribution(a) for a in alternatives]
    cur_key = depresolve.normalize_distribution(curated) if curated else ""
    mod_key = depresolve.normalize_distribution(module)

    look = index.lookup(module) if index is not None and index.checked else None
    installed = [c for c in look.candidates if not c.shadowed] if look else []
    solid = [c for c in installed if EV_NAME_ONLY not in c.evidence]
    name_only = [c for c in installed if EV_NAME_ONLY in c.evidence]
    inst_keys = _dedupe(c.key for c in installed)

    compat: list[str] = []
    evidence: list[str] = []

    # ---- 声明：只和「可能的发行包」比；不凭名字相近去挑 ----
    dec_req = dec_con = ""
    for k in _dedupe([*inst_keys, cur_key, *alt_keys, mod_key]):
        if k and k in declared:
            dec_req, dec_con = k, declared[k]
            break

    # ---- 候选表：已安装 > 声明 > curated；同一发行包只出现一次（最强来源） ----
    rows: list[dict] = [c.to_payload(SRC_INSTALLED) for c in look.candidates] if look else []
    seen = {depresolve.normalize_distribution(r["distribution"]) for r in rows}

    def add_static(key: str, source: str) -> None:
        if not key or key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "distribution": key,
                "version": "",
                "source": source,
                "ecosystem": ECO_PYPI,
                "provenance": "",
                "evidence": [],
                "evidence_file": "",
                "reproducible": False,
                "shadowed": False,
            }
        )

    if dec_req:
        add_static(dec_req, SRC_DECLARED)
    for a in alternatives or ([curated] if curated else []):
        add_static(depresolve.normalize_distribution(a), SRC_CURATED)

    if look is not None and any(c.shadowed for c in look.candidates):
        compat.append("shadowed_by_higher_priority_layer")

    def finish(res_kwargs: dict) -> Resolution:
        return Resolution(
            candidates=tuple(rows),
            declared_requirement=dec_req,
            declared_constraint=dec_con,
            compatibility=tuple(_dedupe(compat)),
            **res_kwargs,
        )

    # ============ A. 有指向具体模块位置的已安装证据 ============
    if solid:
        keys = _dedupe(c.key for c in solid)
        evidence += _dedupe(e for c in solid for e in c.evidence)
        if dec_req and dec_req in inst_keys:
            compat.append("declared_matches_installed_candidate")
        if len(keys) > 1:
            evidence.append(EV_MULTIPLE)
            return finish(
                {
                    "kind": KIND_AMBIGUOUS,
                    "status": ST_AMBIGUOUS,
                    "evidence": tuple(_dedupe(evidence)),
                }
            )
        c = solid[0]
        if c.duplicate_versions:
            compat.append("multiple_versions_installed")
        if not c.metadata_ok:
            compat.append("metadata_incomplete")
        if dec_req and dec_req != c.key:
            compat.append("declared_dist_differs_from_observed")
        elif cur_key and cur_key != c.key and not dec_req:
            compat.append("curated_dist_differs_from_observed")
        common = {
            "provenance": c.provenance,
            "evidence": tuple(_dedupe(evidence)),
        }
        if c.ecosystem == ECO_CONDA:
            return finish({**common, "kind": KIND_UNSUPPORTED, "status": ST_CONDA})
        observed = {"observed_distribution": c.distribution, "observed_version": c.version}
        if c.provenance == PROV_EDITABLE:
            return finish(
                {
                    **common,
                    **observed,
                    "kind": KIND_EDITABLE,
                    "status": ST_EDITABLE,
                    "editable": True,
                }
            )
        if c.provenance in (PROV_LOCAL_PATH, PROV_LOCAL_ARCHIVE, PROV_VCS, PROV_URL):
            return finish(
                {**common, **observed, "kind": KIND_UNSUPPORTED, "status": ST_NOT_REPRODUCIBLE}
            )
        if c.provenance == PROV_CONDA:
            compat.append("installed_by_conda_not_pip")
            return finish({**common, **observed, "kind": KIND_CONFIRMED, "status": ST_CONFIRMED})
        status = ST_CONFIRMED
        if dec_req == c.key and dec_con:
            ok = depresolve.version_satisfies(dec_con, c.version)
            if ok is None:
                compat.append("declared_constraint_unchecked")
            elif ok:
                compat.append("declared_constraint_satisfied")
            else:
                compat.append("declared_version_conflict")
                status = ST_VERSION_CONFLICT
        return finish(
            {
                **common,
                **observed,
                "selected": c.distribution,
                "kind": KIND_CONFIRMED,
                "status": status,
            }
        )

    # ============ B. 只有「名字对得上」的元数据（没有模块证据）：未确认 ============
    if name_only:
        c = name_only[0]
        evidence.append(EV_NAME_ONLY)
        if not c.metadata_ok:
            compat.append("metadata_incomplete")
        editable = c.provenance == PROV_EDITABLE
        return finish(
            {
                "kind": KIND_EDITABLE if editable else KIND_UNVERIFIED,
                "status": ST_EDITABLE if editable else ST_UNVERIFIED,
                "provenance": c.provenance,
                "evidence": tuple(evidence),
                "editable": editable,
            }
        )

    # ============ C. 没有任何认领它的发行包 ============
    if look is not None and look.module_present:
        evidence.append(EV_ORPHAN)
        return finish(
            {"kind": KIND_UNVERIFIED, "status": ST_UNVERIFIED, "evidence": tuple(evidence)}
        )
    kind, status = KIND_NONE, ST_NOT_CHECKED
    if index is not None:
        if look is not None and look.complete and look.uncovered_paths == 0:
            status = ST_NOT_INSTALLED
        else:
            status = ST_ENV_NOT_CHECKED
            compat.append(
                "path_entries_not_followed"
                if look is not None and look.complete
                else "metadata_scan_incomplete"
            )
    if len(alt_keys) > 1:  # 多发行包提供同一个 import 名（cv2）：全留着，不替用户挑
        evidence.append(EV_MULTIPLE)
        kind, status = KIND_AMBIGUOUS, ST_AMBIGUOUS
        if dec_req in alt_keys:
            compat.append("declared_matches_candidate")
    return finish({"kind": kind, "status": status, "evidence": tuple(evidence)})
