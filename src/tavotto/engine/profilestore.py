"""全局 Style / Spec profile 的**唯一服务**：用户数据目录、原子写、乐观并发。

三层边界（`docs/adr/0029-style-spec-profiles.md`）：

    Style   图实际长什么样    —— 应用到图 = 用户文档修改，进 undo / dirty
    Spec    图必须满足什么    —— 只用于检查，不改图
    Export  文件如何生成      —— 既不进 Spec，也不写死在导出组件里

本模块只管**全局清单**：内置 + 用户自定义、增删改查、导入导出、损坏回退。
它不做检查（那在 `preflight.py`）、不做应用（那在前端 `lib/stylePresets.ts`）、
也不碰任何项目文档——项目里存的是**当时生效的快照**，快照一旦写下就与这里
无关（「全局改了不能悄悄改旧项目」的实现方式就是这个：不是靠版本号比对，
是靠项目自己带着一份规则）。

落盘位置是**用户数据目录**（`engine/config.data_dir()`，三平台各自的惯例），
不是安装目录——装成 wheel 之后 site-packages 不可写，写在那里的东西升级即失。

**损坏一律回退内置，不让应用起不来**：读不出来 / 不是合法结构时当作「用户
一条都没建」，并把坏文件挪进 `backup/`（**不删**：那是用户的东西，只是我们
读不懂它）。
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import atomicio, config, profiles as profiles_mod

#: 两种 profile。**枚举而不是布尔**：以后加第三种（比如 Export preset）时
#: 这里加一行，而布尔值会逼出 `is_spec` 这类只对两种取值成立的判断。
KIND_STYLE = "style"
KIND_SPEC = "spec"
KINDS = (KIND_STYLE, KIND_SPEC)

#: 数据目录下的子目录名
STORE_DIRNAME = "profiles"
#: 坏文件与迁移前原件的收容处（相对 STORE_DIRNAME）
BACKUP_DIRNAME = "backup"
#: 每种 kind 一个文件
FILENAMES = {KIND_STYLE: "styles.json", KIND_SPEC: "specs.json"}
#: 本模块能读能写的清单格式版本。更高的版本一律不动（旧构建不懂新语义，
#: 「尽力打开」等于用旧规则重写用户的新数据——与文档 schema 同一条纪律）。
STORE_SCHEMA = 1
#: 单条 profile 的格式版本（迁移判据）
PROFILE_SCHEMA = 1

#: 用户自定义的条数上限（每种 kind 各算）。超过就拒绝新建，**不静默丢最旧的**。
MAX_USER_PROFILES = 200
#: 导入载荷的字节上限：解析前先卡，避免拿一个 GB 级 JSON 把父进程撑爆。
MAX_IMPORT_BYTES = 1 << 20

#: 内置样式的 id（从默认规范派生，不是第二份数字）
BUILTIN_STYLE_ID = "builtin-default-style"

_LOCK = threading.RLock()

#: 显示名允许的长度（超出截断；空名拒绝）
MAX_NAME_LEN = 80

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProfileStoreError(ValueError):
    """载荷不合法 / 目标不存在 / 内置只读。`code` 是稳定枚举，HTTP 层直接映射。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class RevisionConflict(ProfileStoreError):
    """乐观并发：拿在手里的 revision 不是磁盘上的那一版。

    **不静默覆盖**——两个窗口同时编辑同一条 profile 时，后写的那个必须先看到
    对方写了什么。`current` 是磁盘上的现值，调用方直接回给界面。
    """

    def __init__(self, current: dict) -> None:
        # 刻意写成 `ProfileStoreError.__init__(self, "…")` 而不是 `super()`：
        # 「后端真的发得出这个 code 吗」那条门禁靠**源码里的字面量**判定
        # （tests/test_error_codes.py），而 super() 那种写法它看不见。
        ProfileStoreError.__init__(
            self,
            "profile_revision_conflict",
            f"这条配置已被改过（磁盘上是第 {current.get('revision')} 版）",
            status=409,
        )
        self.current = current


# ------------------------------- 路径 --------------------------------------
def store_dir() -> Path:
    """`<用户数据目录>/profiles/`。只拼路径，不建目录。"""
    return config.data_path(STORE_DIRNAME)


def store_path(kind: str) -> Path:
    _require_kind(kind)
    return store_dir() / FILENAMES[kind]


def backup_dir() -> Path:
    return store_dir() / BACKUP_DIRNAME


def _require_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ProfileStoreError("profile_bad_kind", f"未知的 profile 类型: {kind!r}")


# ------------------------------ 内置 ---------------------------------------
def _builtin_spec_records() -> list[dict]:
    """内置规范 = `profiles/publication.json` 里的每一条，只读。

    **不复制数字**：`data` 直接来自那份 canonical JSON，改规范仍然只改一处。
    """
    out: list[dict] = []
    try:
        summaries = profiles_mod.list_profiles()
        default_id = profiles_mod.default_profile_id()
    except profiles_mod.ProfileError:
        return out
    for summary in summaries:
        pid = summary["profile_id"]
        try:
            data = profiles_mod.load(pid)
        except profiles_mod.ProfileError:
            continue
        out.append(
            _record(
                pid,
                KIND_SPEC,
                data,
                display_name=summary.get("label") or pid,
                built_in=True,
                version=summary.get("version", ""),
                name_key=f"builtin.spec.{pid}",
                is_default=pid == default_id,
            )
        )
    return out


def _builtin_style_record() -> dict:
    """内置样式 = **从默认规范派生**的一份「默认样式」。

    规范说「正文 9 pt、拉丁字体 Times New Roman、线宽用这几档」，样式就照它
    生成一份可直接应用的默认值——**不在这里再写一遍数字**。规范改了，内置
    样式跟着变；两者从此不可能互相矛盾。

    派生不出来（规范文件坏了）时给一份**空样式**：宁可什么都不改，也不拿
    一组凭空的数字去改用户的图。
    """
    element: dict[str, dict[str, Any]] = {}
    palette: list[str] = []
    try:
        spec = profiles_mod.load()
    except profiles_mod.ProfileError:
        spec = {}
    base = spec.get("default_font_size_pt")
    fam = (spec.get("font_family") or {}).get("latin")
    widths = spec.get("line_widths_pt") or []
    legend = spec.get("legend_policy") or {}
    axis = spec.get("axis_policy") or {}
    weights = spec.get("text_weight_policy") or {}
    if isinstance(base, (int, float)):
        for role in ("text", "title", "axis_label", "ticks"):
            element.setdefault(role, {})["fontsize"] = float(base)
        legend_size = legend.get("max_font_size_pt")
        element.setdefault("legend", {})["fontsize"] = float(
            legend_size if isinstance(legend_size, (int, float)) else base
        )
    if isinstance(fam, str) and fam:
        for role in ("text", "title", "axis_label"):
            element.setdefault(role, {})["fontfamily"] = fam
    for role in ("title", "axis_label", "ticklabel", "legend_text", "annotation"):
        want = weights.get(role)
        target = {"ticklabel": "ticks", "legend_text": "legend"}.get(role, role)
        if want in ("normal", "bold") and target in ("title", "axis_label"):
            element.setdefault(target, {})["weight"] = want
    if widths:
        element.setdefault("line", {})["linewidth"] = float(widths[0])
    frame = axis.get("frame_linewidth_pt") or []
    if frame:
        element.setdefault("axes", {})["spine_linewidth"] = float(frame[0])
    if axis.get("tick_direction") in ("in", "out", "inout"):
        element.setdefault("ticks", {})["direction"] = axis["tick_direction"]
    if legend.get("frame") is False:
        element.setdefault("legend", {})["frameon"] = False
    data = {
        "element": element,
        "palette": palette,
        "annotation": ({"sizePt": float(base)} if isinstance(base, (int, float)) else {}),
        "background": "#ffffff",
        "derived_from_spec": spec.get("profile_id") or "",
    }
    return _record(
        BUILTIN_STYLE_ID,
        KIND_STYLE,
        data,
        display_name="默认样式",
        built_in=True,
        version=str(spec.get("version") or ""),
        name_key="builtin.style.default",
        is_default=True,
    )


def builtins(kind: str) -> list[dict]:
    _require_kind(kind)
    return _builtin_spec_records() if kind == KIND_SPEC else [_builtin_style_record()]


# ------------------------------ 记录形状 ------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _record(
    pid: str,
    kind: str,
    data: dict,
    *,
    display_name: str,
    built_in: bool,
    version: str = "",
    name_key: str = "",
    is_default: bool = False,
    revision: int = 1,
    created_at: int | None = None,
    updated_at: int | None = None,
    derived_from: str = "",
    warnings: list[str] | None = None,
) -> dict:
    """一条 profile 的完整形状（内置与用户自定义**同形**，界面只看字段不看来源）。

    `name_key` 只有内置才有：内置的显示名要跟界面语言走（「默认规范」/
    "Default spec"），而用户起的名字不翻译。`display_name` 是 name_key 翻不出来
    时的兜底，**不是**给用户看的技术 id。
    """
    stamp = _now_ms()
    return {
        "id": pid,
        "kind": kind,
        "schema_version": PROFILE_SCHEMA,
        "revision": revision,
        "display_name": display_name,
        "name_key": name_key,
        "version": version,
        "created_at": created_at if created_at is not None else stamp,
        "updated_at": updated_at if updated_at is not None else stamp,
        "built_in": built_in,
        "read_only": built_in,
        "is_default": is_default,
        "derived_from": derived_from,
        "warnings": list(warnings or []),
        "data": copy.deepcopy(data),
    }


# ------------------------------ 磁盘 ---------------------------------------
def _quarantine(path: Path, why: str) -> None:
    """坏文件挪进 backup/，**不删**。挪不动就算了（下次照样回退内置）。"""
    try:
        dest = backup_dir()
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{path.stem}-{why}-{_now_ms()}{path.suffix}"
        path.replace(target)
    except OSError:
        pass


def _schema_unsupported(kind: str) -> bool:
    """磁盘上那份用的是**本构建读不懂的版本**吗。

    「读不懂」与「一条都没有」在 `_read_user()` 的返回值里长得一模一样（都是
    空清单），而两者的正确动作正相反：后者可以随便写，前者**一个字都不许
    写回去**——用户在新版 Tavotto 里建的每一条 profile 都在那个文件里。
    这一维只能单独问，合并进相邻取值就是静默删除。
    """
    try:
        doc = json.loads(store_path(kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict):
        return False
    schema = doc.get("schema")
    return isinstance(schema, int) and schema > STORE_SCHEMA


def _read_user(kind: str) -> list[dict]:
    """用户自定义清单；读不出来 = 一条都没有（并把坏文件收容）。

    **它答不了「为什么是空的」**：磁盘上没有文件、文件坏了、文件是更高版本
    ——三种都回空清单。要写盘之前必须再问一次 `_schema_unsupported()`
    （`_write_user` 已经替所有调用方问了）。
    """
    path = store_path(kind)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        doc = json.loads(raw)
    except ValueError:
        _quarantine(path, "unparsable")
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("profiles"), list):
        _quarantine(path, "malformed")
        return []
    schema = doc.get("schema")
    if not isinstance(schema, int) or schema > STORE_SCHEMA:
        # 更高版本：**原样留着别动**，本构建当作没有用户 profile。
        # 挪走它等于把用户在新版里建的东西藏起来。
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in doc["profiles"]:
        rec = _coerce(entry, kind)
        if rec is None or rec["id"] in seen:
            continue
        seen.add(rec["id"])
        out.append(rec)
    return out


def _coerce(entry: object, kind: str) -> dict | None:
    """磁盘上的一条 → 记录形状。缺字段补默认，形状不对整条丢掉（不是整份丢掉）。"""
    if not isinstance(entry, dict):
        return None
    pid = entry.get("id")
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        return None
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    name = entry.get("display_name")
    revision = entry.get("revision")
    created = entry.get("created_at")
    updated = entry.get("updated_at")
    warnings = entry.get("warnings")
    return _record(
        pid,
        kind,
        data,
        display_name=(
            name.strip()[:MAX_NAME_LEN] if isinstance(name, str) and name.strip() else pid
        ),
        built_in=False,
        version=str(entry.get("version") or ""),
        revision=revision if isinstance(revision, int) and revision > 0 else 1,
        created_at=created if isinstance(created, int) else None,
        updated_at=updated if isinstance(updated, int) else None,
        derived_from=str(entry.get("derived_from") or ""),
        warnings=[str(w) for w in warnings] if isinstance(warnings, list) else None,
    )


def _write_user(kind: str, records: list[dict]) -> None:
    """落盘。**全部写路径的唯一出口**，所以「不许写」的判据只钉在这里一处。

    磁盘上那份是更高版本时一律拒绝：`_read_user()` 那时回的是空清单，任何
    新建 / 复制 / 导入 / 旧版迁移都会拿着这份空清单走到这里，把一份 schema 1
    的文件盖上去——用户在新版 Tavotto 里建的每一条都没了，而他只是在旧版里
    点了一下「新建样式」。
    """
    if _schema_unsupported(kind):
        raise ProfileStoreError(
            "profile_store_unsupported_schema",
            "这份配置是更高版本的 Tavotto 写的，本版本只读不写",
            status=409,
        )
    path = store_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STORE_SCHEMA,
        "kind": kind,
        "profiles": [
            {
                "id": r["id"],
                "schema_version": r["schema_version"],
                "revision": r["revision"],
                "display_name": r["display_name"],
                "version": r["version"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "derived_from": r["derived_from"],
                "warnings": r["warnings"],
                "data": r["data"],
            }
            for r in records
        ],
    }
    atomicio.write_json(path, payload, indent=1)


# ------------------------------ 查询 ---------------------------------------
def list_profiles(kind: str) -> list[dict]:
    """内置在前、用户自定义按更新时间倒序在后。"""
    _require_kind(kind)
    with _LOCK:
        return builtins(kind) + sorted(
            _read_user(kind), key=lambda r: r["updated_at"], reverse=True
        )


def get_profile(kind: str, pid: str) -> dict | None:
    for rec in list_profiles(kind):
        if rec["id"] == pid:
            return rec
    return None


def require_profile(kind: str, pid: str) -> dict:
    rec = get_profile(kind, pid)
    if rec is None:
        raise ProfileStoreError("profile_not_found", f"没有这条配置: {pid}", status=404)
    return rec


# ------------------------------ 写 -----------------------------------------
def _new_id(kind: str, existing: set[str]) -> str:
    base = f"{kind}-{_now_ms():x}"
    pid = base
    n = 1
    while pid in existing:
        pid = f"{base}-{n}"
        n += 1
    return pid


def _unique_name(name: str, taken: set[str]) -> str:
    """重名策略：加「(2)」直到不撞。**不静默合并、也不拒绝**——名字是给人看的，
    两个「投稿用」并存不是错误，但列表里两行一模一样是。"""
    name = name.strip()[:MAX_NAME_LEN] or "未命名"
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def _validate_data(kind: str, data: object) -> dict:
    if not isinstance(data, dict):
        raise ProfileStoreError("profile_bad_data", "配置内容必须是对象")
    if kind == KIND_SPEC:
        cleaned = copy.deepcopy(data)
        try:
            profiles_mod.validate_spec(cleaned)
        except profiles_mod.ProfileError as exc:
            raise ProfileStoreError("profile_bad_spec", str(exc)) from exc
        return cleaned
    return _validate_style(data)


#: StyleProfile 的顶层键白名单。**不在表里的键不是错误**，它们进 `extra`
#: 并记一条 warning——旧版本读新版本写的样式时，不该把用户的东西丢掉。
#:
#: **这里刻意没有「默认输出 PPI」。** PPI 归 Export 层
#: （`web/src/lib/exportDefaults.ts`，一台机器一份偏好），而"规范推荐多少"
#: 已经在 Spec 里（`preferred_formats.export_dpi_default`）。再在 Style 里放
#: 一份就是同一个数的第三个出处——正是本阶段要消掉的那种东西。
_STYLE_KEYS = (
    "element",
    "palette",
    "annotation",
    "subLabel",
    "page",
    "background",
    "derived_from_spec",
)


def _validate_style(data: dict) -> dict:
    out: dict[str, Any] = {}
    element = data.get("element")
    if element is not None and not isinstance(element, dict):
        raise ProfileStoreError("profile_bad_style", "样式的 element 必须是对象")
    out["element"] = {
        str(role): {str(p): v for p, v in props.items()}
        for role, props in (element or {}).items()
        if isinstance(props, dict)
    }
    palette = data.get("palette")
    out["palette"] = [str(c) for c in palette][:32] if isinstance(palette, list) else []
    for key in ("annotation", "subLabel", "page"):
        value = data.get(key)
        if isinstance(value, dict):
            out[key] = copy.deepcopy(value)
    bg = data.get("background")
    if isinstance(bg, str) and bg.strip():
        out["background"] = bg.strip()[:32]
    src = data.get("derived_from_spec")
    if isinstance(src, str):
        out["derived_from_spec"] = src[:64]
    # `extra` **本身不是一个未知字段**：它是上一次收容未知字段的那只桶。
    # 不单独认出来的话，界面把读到的样式原样存回来（`extra` 也在里面）就会
    # 变成 `{extra: {extra: {...}}}`——每存一次多包一层，警告也从
    # `unmapped_field:futureKey` 变成 `unmapped_field:extra`，而用户什么都没改。
    known = (*_STYLE_KEYS, "extra")
    extra: dict[str, Any] = {}
    prev = data.get("extra")
    if isinstance(prev, dict):
        extra.update(copy.deepcopy(prev))
    extra.update({k: copy.deepcopy(v) for k, v in data.items() if k not in known})
    if extra:
        out["extra"] = extra
    return out


def _extra_warnings(data: dict) -> list[str]:
    extra = data.get("extra")
    return [f"unmapped_field:{k}" for k in sorted(extra)] if isinstance(extra, dict) else []


def create_profile(kind: str, data: dict, display_name: str, *, derived_from: str = "") -> dict:
    _require_kind(kind)
    with _LOCK:
        users = _read_user(kind)
        if len(users) >= MAX_USER_PROFILES:
            raise ProfileStoreError(
                "profile_limit_reached", f"自定义配置最多 {MAX_USER_PROFILES} 条", status=409
            )
        taken_ids = {r["id"] for r in users} | {r["id"] for r in builtins(kind)}
        taken_names = {r["display_name"] for r in list_profiles(kind)}
        pid = _new_id(kind, taken_ids)
        # **规范的身份跟着记录走**：从内置复制一份时，`data.profile_id` 必须
        # 改成新 id。留着源 id 的话，proof report 与 MCP 响应上写的是
        # `lab-publication-v1`，而实际用的是用户改过的规则——「这张图按哪套
        # 规矩过的检」就此说不清了。
        clean = _validate_data(kind, _with_identity(kind, data, pid))
        rec = _record(
            pid,
            kind,
            clean,
            display_name=_unique_name(display_name, taken_names),
            built_in=False,
            derived_from=derived_from,
            warnings=_extra_warnings(clean),
        )
        _write_user(kind, users + [rec])
        return rec


def _with_identity(kind: str, data: object, pid: str) -> dict:
    """规范的 `profile_id` 恒等于记录 id；样式不带身份字段，原样返回。"""
    if kind != KIND_SPEC or not isinstance(data, dict):
        return data if isinstance(data, dict) else {}
    return {**data, "profile_id": pid}


def duplicate_profile(kind: str, pid: str, display_name: str | None = None) -> dict:
    """复制成用户自定义的一份（内置也能复制——那正是「改内置」的正确出口）。"""
    src = require_profile(kind, pid)
    name = display_name or f"{src['display_name']} 副本"
    return create_profile(kind, src["data"], name, derived_from=src["id"])


def update_profile(kind: str, pid: str, patch: dict, expected_revision: int | None) -> dict:
    """改一条用户自定义。`expected_revision` 对不上抛 `RevisionConflict`。

    `expected_revision` 允许 `None`（脚本/迁移这类没有并发的调用方），但
    **界面必须传**：不传等于放弃了「别人改过我就该知道」这条保证。
    """
    _require_kind(kind)
    with _LOCK:
        users = _read_user(kind)
        idx = next((i for i, r in enumerate(users) if r["id"] == pid), None)
        if idx is None:
            if any(r["id"] == pid for r in builtins(kind)):
                raise ProfileStoreError(
                    "profile_read_only", "内置配置不能直接改，请先复制一份", status=409
                )
            raise ProfileStoreError("profile_not_found", f"没有这条配置: {pid}", status=404)
        current = users[idx]
        if expected_revision is not None and expected_revision != current["revision"]:
            raise RevisionConflict(current)
        nxt = copy.deepcopy(current)
        if "display_name" in patch:
            name = patch["display_name"]
            if not isinstance(name, str) or not name.strip():
                raise ProfileStoreError("name_missing", "名称不能为空")
            taken = {r["display_name"] for r in list_profiles(kind) if r["id"] != pid}
            nxt["display_name"] = _unique_name(name, taken)
        if "data" in patch:
            nxt["data"] = _validate_data(kind, _with_identity(kind, patch["data"], pid))
            nxt["warnings"] = _extra_warnings(nxt["data"])
        nxt["revision"] = current["revision"] + 1
        nxt["updated_at"] = _now_ms()
        users[idx] = nxt
        _write_user(kind, users)
        return nxt


def delete_profile(kind: str, pid: str) -> None:
    """删一条用户自定义。**内置删不掉**；已被项目引用的也照删——项目里带着
    自己的 snapshot，删掉全局那份不会让任何一个项目失去规则。"""
    _require_kind(kind)
    with _LOCK:
        users = _read_user(kind)
        kept = [r for r in users if r["id"] != pid]
        if len(kept) == len(users):
            if any(r["id"] == pid for r in builtins(kind)):
                raise ProfileStoreError("profile_read_only", "内置配置不能删除", status=409)
            raise ProfileStoreError("profile_not_found", f"没有这条配置: {pid}", status=404)
        _write_user(kind, kept)


def reset_profile(kind: str, pid: str) -> dict:
    """把一条派生自内置的用户配置**还原成内置那一份**（revision 照常前进）。"""
    rec = require_profile(kind, pid)
    if rec["built_in"]:
        raise ProfileStoreError("profile_read_only", "内置配置本来就是默认值", status=409)
    source = rec["derived_from"] or (
        profiles_mod.default_profile_id() if kind == KIND_SPEC else BUILTIN_STYLE_ID
    )
    origin = next((b for b in builtins(kind) if b["id"] == source), None)
    if origin is None:
        raise ProfileStoreError(
            "profile_no_origin", "这条配置不是从内置复制来的，没有可恢复的默认值"
        )
    return update_profile(kind, pid, {"data": origin["data"]}, rec["revision"])


# ------------------------------ 导入 / 导出 ----------------------------------
#: 导出文件的信封。带 kind 与 schema，导入时据此拒绝张冠李戴。
EXPORT_FORMAT = "tavotto.profile"


def export_profile(kind: str, pid: str) -> dict:
    rec = require_profile(kind, pid)
    return {
        "format": EXPORT_FORMAT,
        "schema": PROFILE_SCHEMA,
        "kind": kind,
        "display_name": rec["display_name"],
        "version": rec["version"],
        "exported_at": _now_ms(),
        "data": copy.deepcopy(rec["data"]),
    }


def import_profile(payload: object, *, kind: str | None = None) -> dict:
    """严格校验后建成一条**新的用户自定义**。

    * 只认自己导出的信封（`format` / `schema` / `kind` 三样都对）；
    * 只取 `display_name` 与 `data`，**id 一律重新分配**——导入不覆盖任何
      既有配置，也就不存在「导入把我的改动冲掉了」；
    * 载荷里除了白名单什么都不执行、不 import、不 eval。
    """
    if isinstance(payload, (str, bytes)):
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(raw) > MAX_IMPORT_BYTES:
            raise ProfileStoreError("profile_too_large", "导入文件过大")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProfileStoreError("profile_bad_json", f"不是合法的 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileStoreError("profile_bad_payload", "导入内容必须是对象")
    if payload.get("format") != EXPORT_FORMAT:
        raise ProfileStoreError("profile_bad_format", "这不是 Tavotto 导出的配置文件")
    schema = payload.get("schema")
    if not isinstance(schema, int) or schema > PROFILE_SCHEMA:
        raise ProfileStoreError("profile_bad_schema", "这份配置来自更新的版本，当前版本读不了")
    got = payload.get("kind")
    if got not in KINDS or (kind is not None and got != kind):
        raise ProfileStoreError("profile_bad_kind", "配置类型对不上")
    name = payload.get("display_name")
    if not isinstance(name, str) or not name.strip():
        raise ProfileStoreError("name_missing", "配置缺少名称")
    return create_profile(got, payload.get("data") or {}, name)


# ------------------------------ 迁移 ---------------------------------------
#: 旧样式预设的位置（数据目录 `layouts/_styles.json`）。**只读迁移源**。
LEGACY_STYLES_REL = ("layouts", "_styles.json")


def legacy_styles_path() -> Path:
    return config.data_path(*LEGACY_STYLES_REL)


def migrate_legacy_styles() -> dict:
    """把 `layouts/_styles.json` 一次性迁进本 store。

    与 `config._migrate_ai_agents()` 同一条纪律：**迁完就把旧位置腾空**，
    两份权威并存的话，一边改样式另一边不知道，下次读哪份全看读取顺序。
    腾空前先把原件整份复制进 `backup/`——「不因迁移删除用户配置」说的是
    内容不能没，不是旧文件必须留在原地当第二个权威。

    幂等：旧文件不在了就什么都不做（动作数 0）。
    """
    src = legacy_styles_path()
    report = {"migrated": 0, "skipped": 0, "warnings": [], "backup": ""}
    with _LOCK:
        try:
            raw = src.read_text(encoding="utf-8")
        except OSError:
            return report
        try:
            doc = json.loads(raw)
        except ValueError:
            report["warnings"].append("legacy_unparsable")
            _quarantine(src, "legacy-unparsable")
            return report
        entries = doc.get("styles") if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            report["warnings"].append("legacy_malformed")
            _quarantine(src, "legacy-malformed")
            return report
        backup = backup_dir() / f"legacy-styles-{_now_ms()}.json"
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            atomicio.write_bytes(backup, raw.encode("utf-8"))
            report["backup"] = str(backup)
        except OSError as exc:
            # 备份都写不出来就不迁：宁可保持现状，也不在没有退路时动用户的东西
            report["warnings"].append(f"backup_failed:{exc.__class__.__name__}")
            return report
        users = _read_user(KIND_STYLE)
        taken_ids = {r["id"] for r in users} | {r["id"] for r in builtins(KIND_STYLE)}
        taken_names = {r["display_name"] for r in users} | {
            r["display_name"] for r in builtins(KIND_STYLE)
        }
        for entry in entries:
            if not isinstance(entry, dict):
                report["skipped"] += 1
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                report["skipped"] += 1
                report["warnings"].append("legacy_unnamed")
                continue
            body = {k: v for k, v in entry.items() if k not in ("id", "name")}
            clean = _validate_style(body)
            warnings = _extra_warnings(clean)
            report["warnings"].extend(warnings)
            pid = entry.get("id")
            if not (isinstance(pid, str) and _ID_RE.match(pid)) or pid in taken_ids:
                pid = _new_id(KIND_STYLE, taken_ids)
            taken_ids.add(pid)
            display = _unique_name(name, taken_names)
            taken_names.add(display)
            users.append(
                _record(
                    pid,
                    KIND_STYLE,
                    clean,
                    display_name=display,
                    built_in=False,
                    warnings=warnings,
                )
            )
            report["migrated"] += 1
        _write_user(KIND_STYLE, users)
        try:
            src.unlink()
        except OSError as exc:
            # 删不掉旧文件：下次启动会再迁一次，而重名策略会造出「副本」。
            # 如实记着，别假装迁干净了。
            report["warnings"].append(f"legacy_not_removed:{exc.__class__.__name__}")
    return report


# ------------------------------ 取一份可直接检查的规范 ------------------------
def resolve_spec(profile_id: str | None = None, journal: dict | None = None) -> dict:
    """按 id 取一份可直接喂给 `preflight.run()` 的规范（内置 + 用户自定义）。

    `engine/profiles.load()` 只认内置那份 canonical JSON——它是**规则文件**的
    读取器，不该知道用户数据目录的存在（那会让 profiles ↔ profilestore 循环
    import）。所以「任意 id → 规范」这条路只有本函数一份，MCP 与 HTTP 都走它。

    不认识的 id **抛错，不静默退默认**：调用方拿着一个不存在的规范 id 时，
    「按默认规范放行了」是最坏的答案。前端那份 `loadProfile()` 刻意相反
    （旧文档里可能存着已删掉的 id，导出对话框不能整个崩掉），差别写在
    ADR 0029 里——**两边的取舍不同是有意的，不是漂移**。
    """
    pid = profile_id or profiles_mod.default_profile_id()
    # **先按 id 分流，不靠捕获异常分流。** `load()` 抛 ProfileError 有两个成因
    # （id 不认识 / journal 覆盖不合法），吞掉它去查用户清单会把后者也说成
    # 「没有这个出版规范」——用户改了一个字段，收到的是一句指错方向的话。
    if any(b["id"] == pid for b in builtins(KIND_SPEC)):
        try:
            return profiles_mod.load(pid, journal)
        except profiles_mod.ProfileError as exc:
            raise ProfileStoreError("profile_bad_journal", str(exc)) from exc
    rec = get_profile(KIND_SPEC, pid)
    if rec is None or rec["built_in"]:
        raise ProfileStoreError("profile_not_found", f"没有这个出版规范: {pid}", status=404)
    base = profiles_mod.validate_spec(copy.deepcopy(rec["data"]), pid)
    if not journal:
        return base
    if not isinstance(journal, dict):
        raise ProfileStoreError("profile_bad_journal", "journal 覆盖必须是对象")
    merged = profiles_mod.merge_journal(base, journal)
    merged["profile_id"] = base["profile_id"]
    merged["version"] = base.get("version", "")
    merged["derived_from"] = pid
    merged["journal"] = copy.deepcopy(journal)
    return profiles_mod.validate_spec(merged, pid)
