"""项目排版的目录形态（ADR 0211 第二期）：`tavottofile/<名>.tavotto/layout.json` 的**唯一**落盘权威。

名字 ↔ 路径、占位文件、目录自己的清单、当前排版的读取、原子写、迁移状态机都在这里；`app.py`、
`layoutannotate`、`runtimeasset`、`tutorial` 只调这里，不自己拼 `<名>.json` / `<名>.tavotto`。
纯标准库（Flask 父进程 import 链）。

磁盘形态（名字 = `N`，`S` = 项目的 `tavottofile/`）::

    S/N.json                      占位：{"schema": PLACEHOLDER_SCHEMA, "tavotto_layout_dir": "N.tavotto"}
    S/N.tavotto/layout.json       当前权威正文（与旧版 `N.json` 同一种文档）
    S/N.tavotto/package_manifest.json   目录自己的清单（第三期 `.tavotto` 容器骨架，见 `MANIFEST_KEYS`）
    S/N.tavotto/legacy/N.json     迁移时原样留底的旧文件（只是留底：我们不再写它）

旧版（v0.21.3）列出占位、打开它走「版本太新 → 检查更新」；本构建认得占位，**从不把占位当排版**。

**锁与身份永远用逻辑路径 `S/N.json`**（`doclock.lock_stripe` 只看文件名；用 `layout.json` 当锁键，所有排版会
共用一个条带，app 与插件还会选到不同的条带）。会话绑定里的 `file` 也一直是 `tavottofile/N.json`。**修订号永远是
当前正文那份字节的 hash**（目录形态 = `layout.json`；清单里的 `layout_revision` 只是提示，外部工具 / git /
同步盘随时能改 `layout.json` 而不动清单）。

状态（`locate()` → `Located.state`；每一档在 `tests/test_layoutdir.py` 有用例）：

=========  ===================================  ==========================================  =======================
状态       `N.json`                             `N.tavotto/`                                当前正文
=========  ===================================  ==========================================  =======================
NEW        无                                   无                                          无
LEGACY     真排版                               无                                          `N.json`
DIR        占位，或无（占位被删 / 没来得及写）  有，清单有效                                `layout.json`
PENDING    真排版，修订号 = 清单里的留底修订号  有，清单有效（迁移发布了目录、没换占位）   `N.json`（= 留底 = layout.json）
CONFLICT   真排版，修订号 ≠ 留底修订号          有，清单有效（旧版覆盖了占位）              `N.json`（外部改动，见下）
ORPHAN     占位                                 无                                          无
BLOCKED    任意                                 有，但不是有效目录（普通文件 / 无清单）     有真排版则 `N.json`
=========  ===================================  ==========================================  =======================

* **迁移只在写入时发生，且与那次保存分开**：先在同级暂存目录里备齐 `layout.json`（= 旧文件**同一份字节**）、
  留底、清单，**整个目录一次发布**（`atomicio.publish_directory_if_absent`），**最后一步**才原子地把 `N.json`
  换成占位，换完再做这次保存本身。所以迁移中断时两边正文逐字节相同、客户端手里的 `base_revision` 仍然有效，
  下一次写入看到 PENDING 就只补完换占位——不需要任何「选边」。
* **CONFLICT = 旧版在占位上另存**。读它给的是 `N.json`（外部改动是磁盘上的新事实，「重新加载」必须读到它，
  否则用户会在 409 上转圈）；409 的 `revision` 与明确覆盖要带的基线是同一份（`N.json`）。**两边都不丢**：
  明确覆盖时把外来的 `N.json` 与被取代的 `layout.json` 各留一份进 `legacy/`，然后再换回占位。
* 打开 / 列出 / 读取**一个字节都不写**（首开不变式）；只有 `write()` 与迁移会动盘。
* 写入端由 `WRITER_ENABLED` 开关控制，**默认关**：关着时 NEW / LEGACY / ORPHAN 仍写 `N.json`（与改造前逐字节相同）。
  开关只管「创建与迁移」——已经是目录形态的排版（DIR / PENDING / CONFLICT）无论开关都写进目录，否则会把
  `layout.json` 之外的另一份盖在占位上。
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import stat
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import atomicio, documents

#: 写入端总开关：默认关（ADR 0211 第二期 PR 1）；与「自动保存写进项目」同一个 PR 打开。
#: 测试 monkeypatch 这个常量；排查用环境变量 `TAVOTTO_LAYOUT_DIR_WRITER=1` 也能打开。
WRITER_ENABLED = False
WRITER_ENV = "TAVOTTO_LAYOUT_DIR_WRITER"

#: 占位文件的 `schema`：远大于任何现役构建认识的范围，旧版据 `schema > SCHEMA_CURRENT` 判「来自更新的 Tavotto」。
#: **唯一出处**（占位的写出与识别都用它）。
PLACEHOLDER_SCHEMA = 1000
PLACEHOLDER_KEY = "tavotto_layout_dir"
#: 占位只有两个键，几十字节；识别前先看大小，列表时不必把每份排版都读一遍。
PLACEHOLDER_MAX_BYTES = 1024

DIR_SUFFIX = ".tavotto"
LAYOUT_FILE = "layout.json"
MANIFEST_FILE = "package_manifest.json"
LEGACY_DIR = "legacy"

CONTAINER_FORMAT = "tavotto-layout-dir"
#: 容器版本标记（第三期 `package_manifest.json` 骨架）。读到比这更大的清单：只读不写。
CONTAINER_VERSION = 1
#: 清单键（第三期在此基础上加键，不改已有键的含义）。`layout_revision` 只是提示。
MANIFEST_KEYS = (
    "format",
    "container_version",
    "doc_id",
    "layout_revision",
    "placeholder_revision",
    "legacy",
)

NEW, LEGACY, DIR, PENDING, CONFLICT, ORPHAN, BLOCKED = (
    "new",
    "legacy",
    "dir",
    "pending",
    "conflict",
    "orphan",
    "blocked",
)
#: 目录形态的状态：写入一律进目录
_DIR_STATES = (DIR, PENDING, CONFLICT)

#: 文件系统触发的问题（`Located.problem`）
PROBLEM_SYMLINK = "layout_outside_project"  # 目录 / 内部文件是链接：与既有 400 同一个 code
PROBLEM_COLLISION = "layout_name_collision"  # 另一个只差大小写 / Unicode 形式的条目
PROBLEM_TOO_NEW = "layout_dir_too_new"  # 清单的容器版本比本构建新：只读不写

_RESERVED_WINDOWS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_NAME_SANITIZE = re.compile(r"[^\w\-一-鿿]+")
DEFAULT_NEW_NAME = "排版一"

#: 测试注入点：`write()` 的迁移序列在这些位置调它（抛异常 = 模拟进程在这里死掉）。
FAULT_HOOK: Callable[[str], None] | None = None


def _fault(point: str) -> None:
    if FAULT_HOOK is not None:
        FAULT_HOOK(point)


class LayoutDirError(Exception):
    """写入被目录形态的守卫拒绝；`code` 是稳定枚举（HTTP 层直接映射）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def writer_enabled() -> bool:
    return bool(WRITER_ENABLED) or os.environ.get(WRITER_ENV) == "1"


# ------------------------------------------------------------------ 名字


def dir_name(stem: str) -> str:
    return f"{stem}{DIR_SUFFIX}"


def collision_key(name: str) -> str:
    """大小写 / Unicode 形式无关的比较键（与 `doclock.lock_stripe` 同一规则：NFC + casefold）。"""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def unsafe_dir_name(stem: str) -> bool:
    """这个名字做目录名在某个平台上会出事：Windows 保留名 / 末尾点或空格 / 超长。纯函数。

    `app.layout_path` 已把点与空格净化成 `_`，这里是第二道（本模块的帮助函数也直接收画布名）。"""
    if not stem or stem != stem.rstrip(". ") or stem.lstrip() != stem:
        return True
    if stem.split(".", 1)[0].upper() in _RESERVED_WINDOWS:
        return True
    return len((stem + DIR_SUFFIX).encode("utf-8")) > 200


def suggest_name(store: Path, canvas_name: str | None) -> str:
    """从没起过名的新排版：用画布名当名字，重名（含只差大小写的）加数字序号。只读。"""
    base = _NAME_SANITIZE.sub("_", canvas_name or "").strip("_") or DEFAULT_NEW_NAME
    if unsafe_dir_name(base) or not documents.is_user_document_stem(base):
        base = f"{base}_"
    taken = {collision_key(n) for n in _listing(store)}

    def free(stem: str) -> bool:
        return (
            collision_key(f"{stem}.json") not in taken
            and collision_key(dir_name(stem)) not in taken
        )

    if free(base):
        return base
    n = 2
    while not free(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def _listing(store: Path) -> list[str]:
    try:
        return [e.name for e in os.scandir(store)]
    except OSError:
        return []


# ------------------------------------------------------------------ 占位


def placeholder_bytes(stem: str) -> bytes:
    """占位的确定字节（清单里的 `placeholder_revision` 由它算）。"""
    doc = {"schema": PLACEHOLDER_SCHEMA, PLACEHOLDER_KEY: dir_name(stem)}
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")


def placeholder_target(data: bytes) -> str | None:
    """`data` 是占位就回它指向的目录名，否则 None。**按内容认**，不按文件名。大小先过关。"""
    if len(data) > PLACEHOLDER_MAX_BYTES:
        return None
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != PLACEHOLDER_SCHEMA:
        return None
    target = doc.get(PLACEHOLDER_KEY)
    return target if isinstance(target, str) else None


def is_placeholder(path: Path) -> bool:
    """`path` 这个文件是不是占位（读不出 / 太大都不是）。给不经过 `locate()` 的 `*.json` 扫描用。"""
    try:
        if path.stat().st_size > PLACEHOLDER_MAX_BYTES:
            return False
        return placeholder_target(path.read_bytes()) is not None
    except OSError:
        return False


# ------------------------------------------------------------------ 定位


@dataclass(frozen=True)
class Located:
    stem: str
    state: str
    logical: Path  # `S/N.json`：锁键与会话绑定的身份，永远是它
    directory: Path  # `S/N.tavotto`
    #: 当前正文所在的真实文件；没有当前正文是 None
    read_path: Path | None
    problem: str | None = None
    manifest: dict | None = None

    @property
    def layout_file(self) -> Path:
        return self.directory / LAYOUT_FILE

    @property
    def is_dir_form(self) -> bool:
        return self.state in _DIR_STATES

    def current_revision(self) -> str | None:
        """当前正文的修订号：**只有没有当前正文才是 None**；读不出来抛 `OSError`（调用方拒写）。"""
        if self.read_path is None:
            return None
        return atomicio.existing_revision(self.read_path)


def _kind(path: Path) -> str:
    """`absent` / `dir` / `file` / `link`（符号链接 / Windows reparse point）/ `other`。不跟随链接。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "other"
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        return "link"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    return "file" if stat.S_ISREG(info.st_mode) else "other"


def _read_manifest(directory: Path) -> tuple[dict | None, str | None]:
    """→ (清单, problem)。清单不是我们的 / 读不出 = (None, None)；链接 = (None, symlink)。"""
    path = directory / MANIFEST_FILE
    kind = _kind(path)
    if kind == "link":
        return None, PROBLEM_SYMLINK
    if kind != "file":
        return None, None
    try:
        doc = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None, None
    if (
        not isinstance(doc, dict)
        or doc.get("format") != CONTAINER_FORMAT
        or not isinstance(doc.get("container_version"), int)
        or isinstance(doc.get("container_version"), bool)
    ):
        return None, None
    return doc, None


def _legacy_revision(manifest: dict) -> str | None:
    legacy = manifest.get("legacy")
    rev = legacy.get("revision") if isinstance(legacy, dict) else None
    return rev if isinstance(rev, str) else None


def _inside(store: Path, name: str) -> Path:
    """`S/<name>`，且规范化后仍是 `S` 的直接子项：名字里带分隔符 / `..` 一律拒绝（调用方的名字已经净化过，这里是第二道）。"""
    base = os.path.normpath(os.fspath(store))
    joined = os.path.normpath(os.path.join(base, name))
    if not joined.startswith(base.rstrip(os.sep) + os.sep) or os.path.dirname(joined) != base:
        raise LayoutDirError(PROBLEM_SYMLINK)
    return Path(joined)


def locate(store: Path, stem: str, *, names: Iterable[str] | None = None) -> Located:
    """这个名字此刻在盘上是哪一档、当前正文在哪。**只读，从不写。**

    `names`：`S` 的目录列表（批量列出时只扫一次）；不给就扫。"""
    store = Path(store)
    logical = _inside(store, f"{stem}.json")
    directory = _inside(store, dir_name(stem))

    def done(state, read_path, *, problem=None, manifest=None):
        return Located(stem, state, logical, directory, read_path, problem, manifest)

    # 只查目录名的大小写 / Unicode 撞车：`A.json` 与 `a.json` 在区分大小写的盘上本来就是两份排版，不能因此藏起来
    listing = list(names) if names is not None else _listing(store)
    want = collision_key(dir_name(stem))
    if any(n != dir_name(stem) and collision_key(n) == want for n in listing):
        return done(BLOCKED, None, problem=PROBLEM_COLLISION)

    d_kind = _kind(directory)
    p_kind = _kind(logical)
    p_bytes: bytes | None = None
    p_placeholder = False
    if p_kind in ("file", "link"):
        try:
            if logical.stat().st_size <= PLACEHOLDER_MAX_BYTES:
                p_bytes = logical.read_bytes()
                p_placeholder = placeholder_target(p_bytes) == dir_name(stem)
        except OSError:
            p_bytes = None
    p_real = p_kind in ("file", "link") and not p_placeholder

    if d_kind == "link":
        return done(BLOCKED, None, problem=PROBLEM_SYMLINK)
    manifest = None
    if d_kind == "dir":
        manifest, problem = _read_manifest(directory)
        if problem:
            return done(BLOCKED, None, problem=problem)
        layout_kind = _kind(directory / LAYOUT_FILE)
        if manifest is not None and layout_kind == "link":
            return done(BLOCKED, None, problem=PROBLEM_SYMLINK)
        if manifest is not None and layout_kind != "file":
            manifest = None  # 清单在、正文不在：不是完整目录
    d_valid = d_kind == "dir" and manifest is not None
    too_new = d_valid and manifest["container_version"] > CONTAINER_VERSION
    problem = PROBLEM_TOO_NEW if too_new else None

    if d_kind in ("file", "other") or (d_kind == "dir" and not d_valid):
        return done(BLOCKED, logical if p_real else None)
    if not d_valid:  # 目录不在
        if p_real:
            return done(LEGACY, logical)
        return done(ORPHAN if p_placeholder else NEW, None)
    if p_real:
        try:
            same = atomicio.revision_of(logical.read_bytes()) == _legacy_revision(manifest)
        except OSError:
            same = False  # 读不出来就不能说它是留底：按外部改动处理，调用方读修订号时会拒写
        state = PENDING if same else CONFLICT
        return done(state, logical, problem=problem, manifest=manifest)
    return done(DIR, directory / LAYOUT_FILE, problem=problem, manifest=manifest)


def list_layouts(store: Path) -> list[Located]:
    """`S` 里有当前正文的排版（占位不算、孤立占位不算、有问题的不算）。顺序不保证。只读。"""
    store = Path(store)
    names = _listing(store)
    stems: set[str] = set()
    for n in names:
        if n.endswith(".json"):
            stems.add(n[: -len(".json")])
        elif n.endswith(DIR_SUFFIX):
            stems.add(n[: -len(DIR_SUFFIX)])
    out = []
    for stem in sorted(stems):
        if not documents.is_user_document_stem(stem):
            continue
        loc = locate(store, stem, names=names)
        if loc.read_path is not None and loc.problem != PROBLEM_SYMLINK:
            out.append(loc)
    return out


# ------------------------------------------------------------------ 写


def _manifest(
    *, doc_id: str | None, layout_revision: str, stem: str, legacy_revision: str | None
) -> dict:
    return {
        "format": CONTAINER_FORMAT,
        "container_version": CONTAINER_VERSION,
        "doc_id": doc_id,
        "layout_revision": layout_revision,
        "placeholder_revision": atomicio.revision_of(placeholder_bytes(stem)),
        "legacy": None
        if legacy_revision is None
        else {"file": f"{LEGACY_DIR}/{stem}.json", "revision": legacy_revision},
    }


def _manifest_bytes(manifest: dict) -> bytes:
    return atomicio.dumps_json(manifest, indent=1)


def _keepsake_mode() -> int | None:
    # POSIX 上留底设成只读位；Windows 上只读属性会让整夹删除（教程重置 / 用户清理）失败，只靠「我们从不再写」
    return None if os.name == "nt" else 0o444


def _stage_and_publish(
    loc: Located, layout: bytes, *, legacy: bytes | None, doc_id: str | None
) -> None:
    """在同级暂存目录里备齐整个目录，再一次发布。发布失败 / 中途死掉不留下半个目标目录。"""
    store = loc.directory.parent
    stage = store / f".{loc.stem}.{os.getpid()}.{id(loc):x}.stage"
    if stage.exists():
        shutil.rmtree(stage)
    try:
        stage.mkdir()
        atomicio.write_bytes(stage / LAYOUT_FILE, layout)
        legacy_rev = None
        if legacy is not None:
            (stage / LEGACY_DIR).mkdir()
            atomicio.write_bytes(
                stage / LEGACY_DIR / f"{loc.stem}.json", legacy, mode=_keepsake_mode()
            )
            legacy_rev = atomicio.revision_of(legacy)
        manifest = _manifest(
            doc_id=doc_id,
            layout_revision=atomicio.revision_of(layout),
            stem=loc.stem,
            legacy_revision=legacy_rev,
        )
        # 清单最后写：有清单 = 目录是完整的
        atomicio.write_bytes(stage / MANIFEST_FILE, _manifest_bytes(manifest))
        atomicio.publish_directory_if_absent(stage, loc.directory)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _swap_placeholder(loc: Located) -> None:
    atomicio.write_bytes(loc.logical, placeholder_bytes(loc.stem))


def _valid_doc_id(doc_id: object) -> str | None:
    return doc_id if isinstance(doc_id, str) and re.fullmatch(r"[\w\-]{1,128}", doc_id) else None


def _write_dir_form(loc: Located, data: bytes, doc_id: str | None) -> str:
    """目录已经就位、占位已换好：写 `layout.json`，再更新清单里的提示字段。"""
    atomicio.write_bytes(loc.layout_file, data)
    revision = atomicio.revision_of(data)
    manifest = dict(loc.manifest or {})
    want = dict(manifest, layout_revision=revision)
    if doc_id is not None:
        want["doc_id"] = doc_id
    if want != manifest:
        # 清单里的 `layout_revision` / `doc_id` 是提示：正文已落盘，提示没更新不能把这次保存报成失败
        try:
            atomicio.write_bytes(loc.directory / MANIFEST_FILE, _manifest_bytes(want))
        except OSError:
            pass
    return revision


def _archive(loc: Located, tag: str, data: bytes) -> None:
    rev = atomicio.revision_of(data)[:8]
    (loc.directory / LEGACY_DIR).mkdir(exist_ok=True)
    atomicio.write_bytes(loc.directory / LEGACY_DIR / f"{loc.stem}.{tag}-{rev}.json", data)


def write(
    store: Path,
    stem: str,
    data: bytes | dict,
    *,
    doc_id: str | None = None,
    enable_new: bool | None = None,
) -> str:
    """把当前正文写成 `data`，返回它的修订号。**调用方已持逻辑路径 `S/N.json` 的文档锁、并已判过冲突。**

    CONFLICT 档调到这里意味着调用方已确认「明确覆盖」（基线 = `N.json` 的修订号）。守卫不过抛 `LayoutDirError`。
    `data` 可以是已序列化的字节，也可以是文档 dict（`indent=1` 序列化；改造前的 `atomicio.write_json` 写法，
    关着开关时仍走它——保存路径的「只有一份原子写」由 `tests/test_project_layout_save.py` 钉住）。
    `enable_new` 缺省取 `writer_enabled()`。"""
    store = Path(store)
    loc = locate(store, stem)
    if loc.problem in (PROBLEM_SYMLINK, PROBLEM_COLLISION, PROBLEM_TOO_NEW):
        raise LayoutDirError(loc.problem)
    doc_id = _valid_doc_id(doc_id)
    enabled = writer_enabled() if enable_new is None else enable_new
    state = loc.state
    document = data if isinstance(data, dict) else None
    to_dir = state in _DIR_STATES or (
        enabled and state in (NEW, LEGACY, ORPHAN) and not unsafe_dir_name(stem)
    )
    if to_dir and document is not None:
        data = atomicio.dumps_json(document, indent=1)  # 先序列化：载荷有问题时一个字节都不动

    if state in _DIR_STATES:
        if state == CONFLICT:
            # 外来的 N.json 与被取代的 layout.json 都不丢
            _archive(loc, "foreign", loc.logical.read_bytes())
            _archive(loc, "superseded", loc.layout_file.read_bytes())
        if state in (PENDING, CONFLICT) or _kind(loc.logical) == "absent":
            _fault("before_placeholder")
            _swap_placeholder(loc)
        return _write_dir_form(loc, data, doc_id)

    if to_dir:
        if state == LEGACY:
            legacy = loc.logical.read_bytes()
            _stage_and_publish(loc, legacy, legacy=legacy, doc_id=doc_id)
            _fault("dir_published")
            _swap_placeholder(loc)
            _fault("placeholder_swapped")
            loc = locate(store, stem)
            return _write_dir_form(loc, data, doc_id)
        _stage_and_publish(loc, data, legacy=None, doc_id=doc_id)
        _fault("dir_published")
        _swap_placeholder(loc)
        return atomicio.revision_of(data)

    # 关着、或这个名字 / 这个位置做不了目录：就是改造前的那一次原子写
    if document is not None:
        return atomicio.write_json(loc.logical, document, indent=1)
    atomicio.write_bytes(loc.logical, data)
    return atomicio.revision_of(data)


# ------------------------------------------------------------------ 工作副本（自动保存写进项目，ADR 0211 第二期 PR 2）


def find_by_doc_id(store: Path, doc_id: str) -> Located | None:
    """清单里记着这个 docId 的目录形态排版（挪走 / 换电脑后本机绑定没了，靠它把工作副本找回来）。

    **只认唯一的一份**：两份目录记着同一个 docId（整夹被拷贝过）= 不知道哪份是它，回 None，不猜。只读。"""
    store = Path(store)
    names = _listing(store)
    hits: list[Located] = []
    for n in names:
        if not n.endswith(DIR_SUFFIX):
            continue
        stem = n[: -len(DIR_SUFFIX)]
        if not documents.is_user_document_stem(stem):
            continue
        loc = locate(store, stem, names=names)
        if loc.read_path is None or loc.problem == PROBLEM_SYMLINK or not loc.manifest:
            continue
        if loc.manifest.get("doc_id") == doc_id:
            hits.append(loc)
    return hits[0] if len(hits) == 1 else None


def classify_write_failure(exc: BaseException, *, platform: str | None = None) -> str:
    """一次写盘失败是 `read_only`（换位置 / 改权限，重试不会变好）、`transient`（杀毒软件 / 另一个进程占着，稍后会好）
    还是 `other`。**Windows 上 EACCES / 共享冲突是瞬时的**：杀毒软件扫描、同步盘、另一个进程短暂占着文件，
    都会让 `os.replace` 报「拒绝访问」，把它判成只读会让用户的项目被无故降级成本机存储。"""
    plat = platform or sys.platform
    cause = exc.__cause__ if exc.__cause__ is not None else exc
    code = getattr(cause, "errno", None)
    winerror = getattr(cause, "winerror", None)
    if code == errno.EROFS or winerror == 19:  # ERROR_WRITE_PROTECT
        return "read_only"
    if plat == "win32":
        if code in (errno.EACCES, errno.EPERM, errno.EBUSY) or winerror in (5, 32, 33):
            return "transient"
        return "other"
    if code in (errno.EACCES, errno.EPERM):
        return "read_only"
    if code == errno.EBUSY:
        return "transient"
    return "other"


#: 瞬时失败的退避（秒）：总共不到一秒，持着文档锁等，不能更长
RETRY_DELAYS = (0.15, 0.5)


def write_retrying(
    store: Path,
    stem: str,
    data: bytes | dict,
    *,
    doc_id: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """`write()`，瞬时失败退避重试；确实只读 / 其它失败原样抛出（调用方用 `classify_write_failure` 分档）。"""
    for delay in (*RETRY_DELAYS, None):
        try:
            return write(store, stem, data, doc_id=doc_id)
        except (
            OSError
        ) as exc:  # 含 `AtomicWriteError`，也含暂存目录 `mkdir` 直接抛的 PermissionError
            if delay is None or classify_write_failure(exc) != "transient":
                raise
            sleep(delay)
    raise AssertionError("unreachable")
