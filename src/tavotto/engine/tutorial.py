"""离线教程项目：随安装包分发的资源、用户数据目录里的版本化副本、完整性与重置。

教程是一个**真正的项目**（图库目录 + 注册表 + 画布文档），不是界面上的一套
假数据：用户在项目选择器里点「用示例了解 Tavotto」之后，走的是与打开自己
项目完全相同的 `open_project()`、面板扫描、图内编辑、画布与导出。区别只在
「项目目录从哪来」——这里。

### 三个位置，各自的角色

* **包内资源** `tavotto/resources/tutorial_project/`：只读、随 wheel / sdist /
  桌面包分发，经 `importlib.resources` 访问（装成 wheel 之后 `__file__` 的上级
  是 site-packages；源码树的相对路径不存在）。**绝不在这里写任何东西**——
  site-packages 不可写，macOS 上往 `.app` 里写一个字节当场破坏代码签名。
* **可写副本** `<data_dir>/tutorial/v<版本>-<资源指纹>/Tutorial/`：首次打开时
  从包内复制过来，之后用户随便改（override、写回、另存画布、改脚本）。
  目录名里同时带 tutorial_version 与资源内容指纹：**改了资源就换目录**，不靠
  「记得升版本号」，旧目录原样留着（那是用户的东西，本模块不删用户目录）。
* **用户自己的项目**：本模块碰都不碰。`is_tutorial_path()` 只认数据目录下的
  `tutorial/` 这一棵树。

### 复制与重置的原子性

新副本一律先在同一父目录下的临时目录里建完整，再 rename 到位；重置时旧副本
先 rename 成 `.Tutorial-*.old` 再 rename 新的进去，任一步失败都把能放回去的
放回去——用户看到的要么是完整的旧副本、要么是完整的新副本，绝不是半个。
Windows 上文件被别的程序占着（打开着的 PDF、资源管理器预览）时 rename 会
PermissionError，这里报 `tutorial_locked` 并说清楚，而不是留下一个残缺目录。

### 打开教程不执行脚本

`ensure_tutorial_copy()` 只做文件复制；打开项目走 `open_project()`，它只读
注册表 JSON（教程自带注册表，连静态起草都不需要）。脚本只在用户进入图内
编辑、明确要求渲染那一刻才由 worker 跑（共享规则 §4）。
`validate_tutorial_resources()` 同样只做静态检查：读 JSON、`compile()`、
读 PDF 尺寸，一行用户代码都不执行。

纯标准库（PDF 尺寸那一条在函数内部延迟 import `tavotto.pdfbackend` 契约层），
Flask 父进程可安全 import。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import atomicio, config, documents, registry

#: 包内资源目录（相对 `tavotto` 包根）。
RESOURCE_PARTS = ("resources", "tutorial_project")
META_FILENAME = "tutorial_meta.json"
#: `tutorial_meta.json` 的 schema。**改字段语义必须升它**——前端按 schema 判
#: 「这份元数据我认不认识」。
META_SCHEMA = 1
#: 数据目录下的教程根目录名。
TUTORIAL_DIRNAME = "tutorial"
#: 版本目录里的项目子目录名（与 `state.json` 并列）。它就是项目在最近列表 /
#: 标题栏里的名字（`project_status()["name"]` 取目录名），所以不叫 `project`。
PROJECT_DIRNAME = "Tutorial"
STATE_FILENAME = "state.json"
#: 教程资源总大小上限：它随每一份 wheel 走，不该悄悄长成几 MB。
MAX_TOTAL_BYTES = 512 * 1024
MAX_FILE_BYTES = 256 * 1024

#: 资源清单里不算数的东西：源码树里跑过脚本会留下 `__pycache__`，
#: 打包器可能留 `.pyc`。它们既不该进 wheel 里的清单，更不该进副本。
_SKIP_DIRS = {"__pycache__"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}

#: 「引用了教程目录之外的数据」的静态判据：这些调用在教程脚本里一律不许出现。
#: 教程必须自包含（共享规则：不依赖网络、不依赖用户工程），而「脚本读了一个
#: 别处的文件」在源码树里跑得通、装到用户机器上第一次打开就 FileNotFoundError。
_FORBIDDEN_CALLS = frozenset(
    {
        "open",
        "load",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "read_csv",
        "read_excel",
        "read_json",
        "read_parquet",
        "read_table",
        "urlopen",
        "urlretrieve",
        "get",
        "post",
    }
)
_FORBIDDEN_MODULES = frozenset({"urllib", "requests", "socket", "http", "ftplib"})
_DATA_SUFFIXES = (
    ".csv",
    ".tsv",
    ".npy",
    ".npz",
    ".txt",
    ".xlsx",
    ".xls",
    ".h5",
    ".hdf5",
    ".dat",
    ".pkl",
    ".pickle",
    ".parquet",
)
#: 绝对路径的样子：POSIX 根、Windows 盘符、家目录。只在字符串 / 文本开头处
#: 认——`8 / 2.54` 这种除法与 `PDF/PNG` 这种并列不算。
_ABS_PATH_RE = re.compile(r"""(?:^|["'\s=(])(?:/[A-Za-z_]|[A-Za-z]:\\|~/)""", re.M)
_DOC_ID_RE = re.compile(r"^[\w\-]+$")


class TutorialError(RuntimeError):
    """教程资源或副本出了问题。`code` 是稳定枚举，HTTP 层直接映射。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 包内资源
# ---------------------------------------------------------------------------
def resource_root() -> Path:
    """包内教程资源目录：`importlib.resources` → 开发态源码树兜底。

    与 `engine/profiles.profiles_path()` 同一条纪律：先问包，再退到
    `engine/` 的上一级。冻结（PyInstaller）产物里 `files("tavotto")` 落在
    `_MEIPASS/tavotto`，资源经 `packaging/tavotto.spec` 的 datas 放到同一处。
    """
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath(*RESOURCE_PARTS)))
        if cand.is_dir():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return Path(__file__).resolve().parent.parent.joinpath(*RESOURCE_PARTS)


def resource_files(root: Path | None = None) -> dict[str, Path]:
    """资源清单：`相对路径（POSIX）→ 绝对路径`，按相对路径排序。

    **这就是「教程由哪些文件组成」的唯一出处**：复制、完整性、打包验证都读它，
    不另维护一张手写的文件表——手写的表在加一张图时必然漏一行。
    """
    base = Path(root) if root is not None else resource_root()
    out: dict[str, Path] = {}
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or Path(name).suffix in _SKIP_SUFFIXES:
                continue
            p = Path(dirpath) / name
            out[p.relative_to(base).as_posix()] = p
    return dict(sorted(out.items()))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resource_manifest(root: Path | None = None) -> dict[str, str]:
    """`相对路径 → sha256`。几十 KB 的文件，每次现算，不缓存。"""
    return {rel: _sha256(p) for rel, p in resource_files(root).items()}


def resource_digest(manifest: dict[str, str] | None = None) -> str:
    """整套资源的内容指纹（清单逐行哈希）。目录名的一部分，见模块文档。"""
    man = manifest if manifest is not None else resource_manifest()
    h = hashlib.sha256()
    for rel, digest in sorted(man.items()):
        h.update(f"{rel}\0{digest}\n".encode())
    return h.hexdigest()[:12]


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_metadata(meta: object) -> list[str]:
    """元数据骨架校验；返回问题列表（空 = 合法）。"""
    problems: list[str] = []
    if not isinstance(meta, dict):
        return ["tutorial_meta.json 不是 JSON 对象"]
    if meta.get("schema") != META_SCHEMA:
        problems.append(
            f"tutorial_meta.json schema 应为 {META_SCHEMA}，实际 {meta.get('schema')!r}"
        )
    ver = meta.get("tutorial_version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        problems.append(f"tutorial_version 应为 ≥1 的整数，实际 {ver!r}")
    for key in ("project_name", "document_name"):
        if not isinstance(meta.get(key), str) or not meta.get(key):
            problems.append(f"{key} 缺失或不是非空字符串")
    doc_id = meta.get("document_id")
    if not isinstance(doc_id, str) or not _DOC_ID_RE.match(doc_id):
        problems.append(f"document_id 必须只含字母 / 数字 / 下划线 / 连字符，实际 {doc_id!r}")
    stems = meta.get("expected_stems")
    if not isinstance(stems, list) or not all(isinstance(s, str) and s for s in stems):
        problems.append("expected_stems 必须是非空字符串列表")
    roles = meta.get("editable_role_preferences")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        problems.append("editable_role_preferences 必须是字符串列表")
    panels = meta.get("panels")
    if not isinstance(panels, list) or len(panels) < 2:
        problems.append("panels 至少要有两张（教程要演示多选排列）")
    else:
        for i, panel in enumerate(panels):
            if not isinstance(panel, dict):
                problems.append(f"panels[{i}] 不是对象")
                continue
            for key in ("key", "file", "stem", "script"):
                if not isinstance(panel.get(key), str) or not panel.get(key):
                    problems.append(f"panels[{i}].{key} 缺失")
            if not isinstance(panel.get("editable_roles"), list):
                problems.append(f"panels[{i}].editable_roles 必须是列表")
    return problems


def tutorial_metadata() -> dict:
    """读并校验包内 `tutorial_meta.json`；不合法抛 `TutorialError`。"""
    path = resource_root() / META_FILENAME
    try:
        meta = _read_json(path)
    except OSError as exc:
        raise TutorialError("tutorial_resources_missing", f"教程资源读不到：{exc}") from exc
    except ValueError as exc:
        raise TutorialError(
            "tutorial_resources_invalid", f"教程元数据不是合法 JSON：{exc}"
        ) from exc
    problems = _check_metadata(meta)
    if problems:
        raise TutorialError("tutorial_resources_invalid", "；".join(problems))
    return meta


# ---------------------------------------------------------------------------
# 数据目录里的副本
# ---------------------------------------------------------------------------
def tutorial_root() -> Path:
    """数据目录下教程的根：`<data_dir>/tutorial/`。**唯一出处**。"""
    return config.data_path(TUTORIAL_DIRNAME)


def version_dirname(version: int, digest: str) -> str:
    return f"v{version}-{digest}"


def tutorial_version_dir(meta: dict | None = None, digest: str | None = None) -> Path:
    meta = meta if meta is not None else tutorial_metadata()
    digest = digest if digest is not None else resource_digest()
    return tutorial_root() / version_dirname(int(meta["tutorial_version"]), digest)


def tutorial_destination(meta: dict | None = None, digest: str | None = None) -> Path:
    """可写副本的项目目录（此刻资源版本对应的那一份）。**不保证存在。**"""
    return tutorial_version_dir(meta, digest) / PROJECT_DIRNAME


def is_tutorial_path(path: str | Path) -> bool:
    """这个路径在教程树（`<data_dir>/tutorial/`）之下吗？

    用 `normalize_path_identity`（与项目 id 同一把尺）而不是 `resolve()` 后
    直接比：大小写不敏感的卷上两条只差大小写的路径是同一个目录。
    """
    try:
        root = config.normalize_path_identity(tutorial_root().resolve())
        target = config.normalize_path_identity(Path(path).resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    return target == root or target.startswith(root.rstrip(os.sep) + os.sep)


def _state_path(version_dir: Path) -> Path:
    return version_dir / STATE_FILENAME


def _write_state(version_dir: Path, meta: dict, digest: str, manifest: dict[str, str]) -> None:
    atomicio.write_json(
        _state_path(version_dir),
        {
            "tutorial_version": int(meta["tutorial_version"]),
            "resource_digest": digest,
            "copied_at": int(time.time() * 1000),
            "files": manifest,
        },
        indent=1,
    )


def copy_status(meta: dict | None = None) -> dict:
    """副本此刻的状态：在不在、全不全、缺什么。只读。"""
    meta = meta if meta is not None else tutorial_metadata()
    manifest = resource_manifest()
    digest = resource_digest(manifest)
    dest = tutorial_destination(meta, digest)
    if not dest.is_dir():
        return {
            "exists": False,
            "complete": False,
            "missing": sorted(manifest),
            "registry_ok": False,
            "version": int(meta["tutorial_version"]),
            "resource_digest": digest,
        }
    missing = [rel for rel in manifest if not (dest / rel).is_file()]
    registry_ok = _registry_readable(dest)
    return {
        "exists": True,
        "complete": not missing and registry_ok,
        "missing": missing,
        "registry_ok": registry_ok,
        "version": int(meta["tutorial_version"]),
        "resource_digest": digest,
    }


def _registry_readable(project: Path) -> bool:
    try:
        registry.Registry().load(project)
    except (FileNotFoundError, RuntimeError):
        return False
    return True


@dataclass
class TutorialProject:
    """`ensure_tutorial_copy()` 的结果：副本在哪、这次做了什么。"""

    path: Path
    version: int
    resource_digest: str
    created: bool = False
    reset: bool = False
    repaired: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _build_fresh_copy(version_dir: Path, files: dict[str, Path]) -> Path:
    """在版本目录下建一个**完整的**临时副本；失败清掉自己、不留半个。"""
    version_dir.mkdir(parents=True, exist_ok=True)
    tmp = version_dir / f".{PROJECT_DIRNAME}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.mkdir()
        for rel, src in files.items():
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            # `copyfile` 只拷内容不拷权限位：包内文件就算是只读的（site-packages /
            # .app），副本也按 umask 建成可写的，不需要再 chmod。
            shutil.copyfile(src, dst)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise TutorialError("tutorial_copy_failed", f"复制教程项目失败：{exc}") from exc
    return tmp


def _is_locked(exc: OSError) -> bool:
    """Windows 上「被占用」的两种脸：PermissionError（WinError 5）与
    WinError 32（sharing violation）。POSIX 上只有 EACCES / EPERM。"""
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32)


def _sweep_leftovers(version_dir: Path) -> None:
    """上一次 reset 没删干净的 `.Tutorial-*.old` / `.tmp`：尽力清，清不掉不算失败。"""
    if not version_dir.is_dir():
        return
    for p in version_dir.iterdir():
        if p.is_dir() and p.name.startswith(f".{PROJECT_DIRNAME}-"):
            shutil.rmtree(p, ignore_errors=True)


def ensure_tutorial_copy(*, reset: bool = False) -> TutorialProject:
    """确保数据目录里有一份可用的教程副本，返回它的位置。

    * 没有 → 复制一份（`created=True`）；
    * 有且完整 → 原样复用，用户的改动一个字节不动；
    * 有但缺文件 / 注册表读不了 → **只补缺的那几个文件**（`repaired=[...]`），
      其余仍是用户的；
    * `reset=True` → 原子换成干净副本（`reset=True`），旧副本整个丢掉。

    从不删除版本目录之外的任何东西；旧版本目录（资源升级之后）留着不动。
    """
    meta = tutorial_metadata()
    files = resource_files()
    if not files:
        raise TutorialError("tutorial_resources_missing", "包内没有教程资源目录")
    manifest = {rel: _sha256(p) for rel, p in files.items()}
    digest = resource_digest(manifest)
    version = int(meta["tutorial_version"])
    version_dir = tutorial_version_dir(meta, digest)
    dest = version_dir / PROJECT_DIRNAME
    _sweep_leftovers(version_dir)

    result = TutorialProject(path=dest, version=version, resource_digest=digest, metadata=meta)

    if dest.is_dir() and not reset:
        missing = [rel for rel in files if not (dest / rel).is_file()]
        reg_rel = registry.REGISTRY_NAME
        if reg_rel in files and reg_rel not in missing and not _registry_readable(dest):
            missing.append(reg_rel)
        for rel in missing:
            dst = dest / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.tmp")
                shutil.copyfile(files[rel], tmp)
                os.replace(tmp, dst)
            except OSError as exc:
                code = "tutorial_locked" if _is_locked(exc) else "tutorial_copy_failed"
                raise TutorialError(code, f"修复教程文件 {rel} 失败：{exc}") from exc
        result.repaired = missing
        if missing or not _state_path(version_dir).is_file():
            _write_state(version_dir, meta, digest, manifest)
        return result

    tmp = _build_fresh_copy(version_dir, files)
    old: Path | None = None
    if dest.exists():
        old = version_dir / f".{PROJECT_DIRNAME}-{uuid.uuid4().hex[:8]}.old"
        try:
            os.rename(dest, old)
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            if _is_locked(exc):
                raise TutorialError(
                    "tutorial_locked",
                    "教程项目里有文件正被其他程序占用（通常是打开着的 PDF 或文件夹预览），"
                    f"关掉之后再试：{exc}",
                ) from exc
            raise TutorialError("tutorial_copy_failed", f"无法替换旧的教程副本：{exc}") from exc
    try:
        os.rename(tmp, dest)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if old is not None:
            try:
                os.rename(old, dest)  # 把旧副本放回去：用户不该因为一次失败失去教程
            except OSError:
                pass
        code = "tutorial_locked" if _is_locked(exc) else "tutorial_copy_failed"
        raise TutorialError(code, f"放置新的教程副本失败：{exc}") from exc
    _write_state(version_dir, meta, digest, manifest)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)  # 删不掉留着，下次 _sweep_leftovers 再清
    result.created = old is None
    result.reset = old is not None
    return result


# ---------------------------------------------------------------------------
# 资源静态验证（不执行任何脚本）
# ---------------------------------------------------------------------------
def _script_problems(rel: str, source: str) -> list[str]:
    problems: list[str] = []
    try:
        compile(source, rel, "exec")
    except SyntaxError as exc:
        return [f"{rel} 编译失败：{exc}"]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                if name.split(".")[0] in _FORBIDDEN_MODULES:
                    problems.append(f"{rel} import 了网络模块 {name}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in _FORBIDDEN_CALLS:
                problems.append(f"{rel} 第 {node.lineno} 行调用了 {name}()：教程脚本不许读外部数据")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if s.lower().endswith(_DATA_SUFFIXES) or s.startswith("../") or "/../" in s:
                problems.append(f"{rel} 第 {node.lineno} 行引用了外部数据 {s!r}")
    return problems


def validate_tutorial_resources(root: Path | None = None) -> list[str]:
    """包内教程资源的静态验证；返回问题列表，空表示合法。

    所有检查都不执行用户代码：读 JSON、`compile()`、读 PDF 首页尺寸。
    `root` 只给测试用（指到一份故意弄坏的资源）。
    """
    base = Path(root) if root is not None else resource_root()
    problems: list[str] = []
    files = resource_files(base)
    if not files:
        return [f"教程资源目录不存在或为空：{base}"]

    # 1. 元数据
    meta_path = base / META_FILENAME
    if META_FILENAME not in files:
        return [f"缺少 {META_FILENAME}"]
    try:
        meta = _read_json(meta_path)
    except ValueError as exc:
        return [f"{META_FILENAME} 不是合法 JSON：{exc}"]
    problems += _check_metadata(meta)
    if problems:
        return problems
    assert isinstance(meta, dict)

    # 2. 注册表
    reg_rel = registry.REGISTRY_NAME
    reg = registry.Registry()
    if reg_rel not in files:
        problems.append(f"缺少 {reg_rel}")
    else:
        try:
            reg.load(base)
        except (RuntimeError, FileNotFoundError) as exc:
            problems.append(f"注册表不合法：{exc}")
    if problems:
        return problems

    # 3. 脚本：存在、能编译、不读外部数据、不联网
    for script in reg.all_scripts():
        if script not in files:
            problems.append(f"注册表声明的脚本不存在：{script}")
            continue
        problems += _script_problems(script, files[script].read_text(encoding="utf-8"))
    for rel, path in files.items():
        if rel.endswith(".py") and rel not in reg.all_scripts():
            problems += _script_problems(rel, path.read_text(encoding="utf-8"))

    # 4. stems：注册表 ↔ 元数据 ↔ 磁盘上的 PDF
    reg_stems = {s for sc in reg.all_scripts() for s in reg.stems_of(sc)}
    meta_stems = set(meta["expected_stems"])
    if reg_stems != meta_stems:
        problems.append(f"expected_stems {sorted(meta_stems)} 与注册表 {sorted(reg_stems)} 不一致")
    panel_stems = {p["stem"] for p in meta["panels"]}
    if not panel_stems <= meta_stems:
        problems.append(
            f"panels 里的 stem 不在 expected_stems 内：{sorted(panel_stems - meta_stems)}"
        )
    for panel in meta["panels"]:
        if panel["file"] != f"{panel['stem']}.pdf":
            problems.append(f"panels[{panel['key']}].file 应为 {panel['stem']}.pdf")
        if panel["script"] not in reg.all_scripts():
            problems.append(f"panels[{panel['key']}].script {panel['script']} 不在注册表里")
        elif panel["stem"] not in reg.stems_of(panel["script"]):
            problems.append(
                f"panels[{panel['key']}].stem {panel['stem']} 不是 {panel['script']} 的产物"
            )
    for stem in sorted(meta_stems):
        rel = f"{stem}.pdf"
        if rel not in files:
            problems.append(f"缺少 {rel}")
            continue
        try:
            from .. import pdfbackend  # 契约层；PyMuPDF 只在它背后

            probe = pdfbackend.probe_asset(files[rel], "pdf")
            if not (probe.get("w_pt", 0) > 0 and probe.get("h_pt", 0) > 0):
                problems.append(f"{rel} 读不出页面尺寸")
        except Exception as exc:  # noqa: BLE001 —— 坏 PDF 的异常类型由后端决定
            problems.append(f"{rel} 不是可读的 PDF：{exc}")

    # 5. 教程画布文档：当前 schema、只引用教程自己的素材
    doc_rel = f"{config.PROJECT_STORE_DIRNAME}/{meta['document_name']}.json"
    if doc_rel not in files:
        problems.append(f"缺少教程画布文档 {doc_rel}")
    else:
        try:
            doc = documents.validate_document(_read_json(files[doc_rel]))
        except ValueError as exc:
            problems.append(f"{doc_rel} 不是合法的项目文档：{exc}")
        else:
            if doc.get("schema") != documents.SCHEMA_CURRENT:
                problems.append(
                    f"{doc_rel} schema {doc.get('schema')} 不是当前的 {documents.SCHEMA_CURRENT}"
                )
            if (
                isinstance(doc.get("project"), dict)
                and doc["project"].get("id") != meta["document_id"]
            ):
                problems.append(f"{doc_rel} 的 project.id 应与 document_id 一致")
            for canvas in doc.get("canvases") or []:
                for obj in canvas.get("objects") or []:
                    if obj.get("type") == "panel" and obj.get("fileId") not in files:
                        problems.append(f"{doc_rel} 引用了不存在的素材 {obj.get('fileId')!r}")

    # 6. 绝对路径与体积
    total = 0
    for rel, path in files.items():
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES:
            problems.append(f"{rel} 太大（{size} 字节 > {MAX_FILE_BYTES}）")
        if rel.endswith((".py", ".json", ".md", ".txt")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if _ABS_PATH_RE.search(text):
                problems.append(f"{rel} 里出现了绝对路径")
    if total > MAX_TOTAL_BYTES:
        problems.append(f"教程资源总大小 {total} 字节超过上限 {MAX_TOTAL_BYTES}")
    return problems
