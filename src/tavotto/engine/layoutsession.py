"""会话状态以后端为准（#715 PR-B）：「这个项目上次开着哪份排版」与「这个槽位属于哪个项目」。

为什么要放在后端：桌面壳每次启动 sidecar 都可能换端口，而 localStorage 按 origin（协议 + 主机 +
端口）隔离——换了端口就是一份全新的空存储，前端记在本机的「上次打开的排版」随之丢失，用户每次
启动都落在一份空白排版上（#715，Windows 实测 5 次启动 5 个 origin）。稳定端口（PR-A #718，ADR
0108 §一—§二）能让 origin 尽量不变，但端口被占时仍然会变；所以这类**丢了用户就找不回工作**的状态
以数据目录里的这份文件为准，前端的 localStorage 只当缓存（ADR 0108 §三）。

文件 `data_dir()/state/layout-sessions.json`，只由本模块读写：

```json
{
  "version": 1,
  "projects": {"<normalize_path_identity(项目路径)>": {"last": {"doc_id", "name", "at"}}},
  "no_project": {"last": {...}},
  "owners": {"<槽位 doc_id>": "<项目键>" | null}
}
```

* 键用 `config.normalize_path_identity(项目路径)`——与 `app._project_id()`、`pool._norm_dir()`
  同一份判断；短 id 是它的 sha1 前缀、反解不回路径，所以落盘存的是路径本身。
* `no_project` 是「没开项目」那一组（后端没有打开的项目时的排版）；`owners` 里值为 `null`
  表示「确认过：写它的时候没开项目」，与「没有这条记录（不知道）」是两档。
* **不能放的位置**：`layouts/_autosave/`（槽位目录，清理按文件名扫）、`layouts/`（命名画布）、
  项目的 `tavottofile/`（随项目包、网盘流动，而这是本机的会话状态）。

纯标准库。写入一律 `atomicio`（ADR 0023）；读坏了（不是 JSON、形状不对、含非有限数）当作
什么都没记——这是「上次停在哪」的提示，丢了只是回到旧行为，不值得让任何一次保存失败。
本模块的锁只管本进程里的读改写；别的进程共用同一个数据目录时后写者赢（与 `config.json` 同一档）。

导出默认值（`tavotto.export.defaults`，丢了会静默从 1200 ppi 退回 600 ppi，#715 证据）同理
迁到数据目录，按用户一份：`state/export-defaults.json`。内容由前端定义（`lib/exportDefaults.ts`
是它的语义唯一出处），这里只守「是个不大的 JSON 对象」。
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from . import atomicio, config, documents

LOG = logging.getLogger(__name__)

STATE_DIRNAME = "state"
FILENAME = "layout-sessions.json"
EXPORT_DEFAULTS_FILENAME = "export-defaults.json"
FORMAT_VERSION = 1

#: 前端 documentId 的形状：`newId('d')` 出来的都在这个字符集里。与 `app._autosave_path`
#: 的净化同一个字符集——owners 的键就是槽位文件名去掉 `.json`。
_DOC_ID_RE = re.compile(r"^[\w\-]{1,128}$")
#: 排版名是用户内容，只用来在「找不到上次排版」那句话里指名；截一个上限防止粘错。
MAX_NAME_CHARS = 500
#: 导出默认值的序列化上限（字节）。现在的形状不到 200 字节。
MAX_EXPORT_DEFAULTS_BYTES = 16 * 1024

_LOCK = threading.Lock()
_PREFS_LOCK = threading.Lock()


def state_path() -> Path:
    """会话状态文件在哪。**唯一出处**。"""
    return config.data_path(STATE_DIRNAME, FILENAME)


def export_defaults_path() -> Path:
    return config.data_path(STATE_DIRNAME, EXPORT_DEFAULTS_FILENAME)


def project_key(path: str | Path) -> str:
    """项目在这份文件里的键。"""
    return config.normalize_path_identity(path)


def valid_doc_id(doc_id: object) -> bool:
    return isinstance(doc_id, str) and bool(_DOC_ID_RE.match(doc_id))


# ------------------------------- 读 / 写 ------------------------------------


def _clean_last(raw: object) -> dict | None:
    if not isinstance(raw, dict) or not valid_doc_id(raw.get("doc_id")):
        return None
    name = raw.get("name")
    at = raw.get("at")
    return {
        "doc_id": raw["doc_id"],
        "name": name[:MAX_NAME_CHARS] if isinstance(name, str) else "",
        # 非有限数（`1e400` 解析成 inf）按坏值处理：int(inf) 会抛 OverflowError，让每一次读写都失败，
        # 违背「读坏了当成空的、下次写整份替换」（#719 Codex P2）
        "at": int(at)
        if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)
        else 0,
    }


def _empty() -> dict:
    return {"projects": {}, "no_project": {}, "owners": {}}


def _read() -> dict:
    """读整份状态；读不动 / 坏了一律当成空的（见模块文档）。"""
    path = state_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _empty()
    except OSError as exc:
        LOG.warning("会话状态读不动，按空处理（%s）", exc)
        return _empty()
    try:
        data = documents.loads_document(raw)
    except ValueError:
        # 坏文件不单独备份：里面只有「上次停在哪」的提示，下一次写入整份换掉它
        LOG.warning("会话状态文件损坏，按空处理；下一次写入会整份替换它")
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, group in projects.items():
            if not isinstance(key, str) or not key or not isinstance(group, dict):
                continue
            last = _clean_last(group.get("last"))
            if last is not None:
                out["projects"][key] = {"last": last}
    none_group = data.get("no_project")
    if isinstance(none_group, dict):
        last = _clean_last(none_group.get("last"))
        if last is not None:
            out["no_project"] = {"last": last}
    owners = data.get("owners")
    if isinstance(owners, dict):
        out["owners"] = {
            k: v
            for k, v in owners.items()
            if valid_doc_id(k) and (v is None or (isinstance(v, str) and v))
        }
    return out


def _write(state: dict) -> None:
    payload = {
        "version": FORMAT_VERSION,
        "projects": dict(sorted(state["projects"].items())),
        "no_project": state["no_project"],
        "owners": dict(sorted(state["owners"].items())),
    }
    atomicio.write_json(state_path(), payload, indent=1)


def _group(state: dict, key: str | None) -> dict:
    if key is None:
        return state["no_project"]
    return state["projects"].setdefault(key, {})


def snapshot() -> dict:
    """整份状态的一份拷贝（测试与诊断用；不回给前端——里面有项目路径）。"""
    with _LOCK:
        return _read()


# ------------------------------- 归属判据 ----------------------------------


class ForeignLayoutError(Exception):
    """这份排版的槽位记在**别的项目**名下，不能记成这个项目「上次开着的」。"""


def owner_conflict(state: dict, doc_id: str, key: str | None) -> bool:
    """槽位 `doc_id` **确知**属于别的项目（`key` 为 `None` = 没开项目那一组）。

    「确知」= owners 里有这条记录且不是 `key`；没有记录是「不知道」，不算冲突（第一次自动保存
    之前就会先记 last）。前端的同一条判据是 `web/src/lib/docOwnership.isForeignDocument`（按本机
    「最近文档」索引），两侧互为纵深（#715 验收 P1：稳定端口之后两个项目共用一个 origin，前端把
    项目 F 的排版当成新项目 G 的「上次开着的」装进来并记下，而这里的 owners 明明记着它属于 F）。
    """
    owners_ = state["owners"]
    return doc_id in owners_ and owners_[doc_id] != key


def owner_verdict(doc_id: str, key: str | None) -> str | None:
    """owners 对「槽位 `doc_id` 是不是 `key` 的」怎么说：`"this"` / `"other"` / `None`（没有记录 = 不知道）。"""
    with _LOCK:
        owners_ = _read()["owners"]
    if doc_id not in owners_:
        return None
    return "this" if owners_[doc_id] == key else "other"


# ------------------------------- last --------------------------------------


def last_for(key: str | None) -> dict | None:
    """这个项目（`None` = 没开项目）上次开着的排版 `{doc_id, name, at}`；没记过回 `None`。

    记着的那份槽位**确知属于别的项目**时同样回 `None`：修复之前的版本可能已经把别的项目的
    排版记到这个项目名下（#715 验收 P1），这类旧记录读出来就不认，不等它被覆盖。
    """
    with _LOCK:
        state = _read()
        group = state["no_project"] if key is None else state["projects"].get(key, {})
        last = group.get("last")
        if not last or owner_conflict(state, last["doc_id"], key):
            return None
        return dict(last)


def set_last(key: str | None, doc_id: str, name: str) -> dict:
    """记「这个项目现在开着这份排版」。同一份 (doc_id, name) 不重写文件。

    `ValueError`：doc_id 形状不对 / name 不是字符串。`ForeignLayoutError`：槽位确知属于别的项目
    （`owner_conflict`），一个字节都不写。`atomicio.AtomicWriteError`：写不进去。
    """
    if not valid_doc_id(doc_id):
        raise ValueError("doc_id 形状不对")
    if not isinstance(name, str):
        raise ValueError("name 必须是字符串")
    name = name[:MAX_NAME_CHARS]
    with _LOCK:
        state = _read()
        if owner_conflict(state, doc_id, key):
            raise ForeignLayoutError(doc_id)
        group = _group(state, key)
        cur = group.get("last")
        if cur and cur["doc_id"] == doc_id and cur["name"] == name:
            return dict(cur)
        last = {"doc_id": doc_id, "name": name, "at": int(time.time() * 1000)}
        group["last"] = last
        _write(state)
        return dict(last)


# ------------------------------- owners ------------------------------------


def record_owner(doc_id: str, key: str | None) -> bool:
    """记下自动保存槽位 `doc_id` 属于哪个项目（`None` = 没开项目）。已经是它就不写，回 False。

    调用方是 `PUT /api/autosave/<id>`：那条路每秒都可能来一次，所以「同值不写」是必须的——
    否则每一次自动保存都会多 fsync 一个文件。
    """
    if not valid_doc_id(doc_id):
        return False
    with _LOCK:
        state = _read()
        owners = state["owners"]
        if doc_id in owners and owners[doc_id] == key:
            return False
        owners[doc_id] = key
        _write(state)
        return True


def owners() -> dict[str, str | None]:
    with _LOCK:
        return dict(_read()["owners"])


def _protected(state: dict) -> set[str]:
    ids = {g["last"]["doc_id"] for g in state["projects"].values() if g.get("last")}
    if state["no_project"].get("last"):
        ids.add(state["no_project"]["last"]["doc_id"])
    return ids


def protected_doc_ids() -> set[str]:
    """各组「上次开着的」排版：槽位清理永不删它们（删了就是下次启动的「找不到上次排版」）。

    这只是**一张快照**：拿到之后别的请求随时可能把某个旧槽位记成新的 last。真正删槽位的
    那一下要走 `remove_slot_unless_protected`，在同一把锁里重判。"""
    with _LOCK:
        return _protected(_read())


def _forget_locked(state: dict, gone: set[str]) -> tuple[list[str], bool]:
    """在 `_LOCK` 里改 `state`：删掉这些槽位的归属与指向它们的 last。回（删掉的归属，改没改）。"""
    removed = sorted(d for d in gone if d in state["owners"])
    for d in removed:
        del state["owners"][d]
    changed = bool(removed)
    for group in [*state["projects"].values(), state["no_project"]]:
        last = group.get("last")
        if last and last["doc_id"] in gone:
            del group["last"]
            changed = True
    state["projects"] = {k: g for k, g in state["projects"].items() if g}
    return removed, changed


def forget_documents(doc_ids: Iterable[str]) -> list[str]:
    """这些槽位没了（被删 / 被清理）：删掉它们的归属，以及指向它们的「上次开着的」记录。"""
    gone = {d for d in doc_ids if isinstance(d, str)}
    if not gone:
        return []
    with _LOCK:
        state = _read()
        removed, changed = _forget_locked(state, gone)
        if changed:
            _write(state)
        return removed


def remove_slot_unless_protected(doc_id: str, remove: Callable[[], bool]) -> bool:
    """清理删一个槽位：**同一把锁里**重判「它此刻是不是某组的 last」、删文件、忘掉它的记录。

    `protected_doc_ids()` 的快照拿到之后，并发的 `set_last` 可能正把这个旧槽位记成某个项目
    的新 last；快照判、事后删，就会删掉那份并在 `forget_documents` 里把刚写的 last 一起抹掉
    （#719 Codex P1）。在这里判与删之间 `set_last` 插不进来：要么它先写（这里看到、不删），
    要么它后写（槽位已删、记录已忘，它记下的 last 由那个标签页下一次自动保存重新落盘）。

    `remove()` 真删文件，回是否删了（调用方在文件自己的锁里、带 mtime 重判）。回是否删了。
    会话状态写不进去不影响「文件已删」这个事实：只记日志，孤儿记录由下一次清理的
    `drop_owners_not_in` 收拾。
    """
    with _LOCK:
        state = _read()
        if doc_id in _protected(state):
            return False
        if not remove():
            return False
        _removed, changed = _forget_locked(state, {doc_id})
        if changed:
            try:
                _write(state)
            except OSError as exc:
                LOG.warning("清理后会话状态没跟上（%s）", exc)
        return True


def drop_owners_not_in(existing: set[str], still_exists: Callable[[str], bool]) -> int:
    """把磁盘上已经不在的槽位从 owners 里拿掉（槽位被外部删掉时 owners 不会自己缩）。

    `existing` 是调用方扫描目录时的快照，只用来挑**候选**；真正删之前在会话状态锁里用
    `still_exists(doc_id)` 当场再看一眼文件：快照之后并发的自动保存可能刚建出那个槽位并记下
    归属，按快照删会让它永远没有归属、从此不进配额（#719 Codex P2）。`record_owner` 持同一把锁，
    所以「重看 → 删」之间插不进新的归属。
    """
    with _LOCK:
        state = _read()
        stale = [d for d in state["owners"] if d not in existing and not still_exists(d)]
        if not stale:
            return 0
        for d in stale:
            del state["owners"][d]
        _write(state)
        return len(stale)


def forget_project(key: str | None, doc_ids: Iterable[str] = ()) -> bool:
    """清掉一个项目的「上次开着的」记录与给定槽位的归属（教程重置用，ADR 0039 §5）。"""
    gone = {d for d in doc_ids if isinstance(d, str)}
    with _LOCK:
        state = _read()
        changed = False
        if key is None:
            if state["no_project"]:
                state["no_project"] = {}
                changed = True
        elif key in state["projects"]:
            del state["projects"][key]
            changed = True
        for d in gone:
            if d in state["owners"]:
                del state["owners"][d]
                changed = True
        if changed:
            _write(state)
        return changed


# ------------------------------- 导出默认值 ----------------------------------


def read_export_defaults() -> dict | None:
    """数据目录里的导出默认值；没存过 / 坏了回 `None`（前端退回它自己的默认值）。"""
    with _PREFS_LOCK:
        try:
            raw = export_defaults_path().read_bytes()
        except OSError:
            return None
        try:
            data = documents.loads_document(raw)
        except ValueError:
            return None
    value = data.get("defaults") if isinstance(data, dict) else None
    return value if isinstance(value, dict) else None


def write_export_defaults(value: object) -> dict:
    """整份替换导出默认值。`ValueError`：不是对象 / 太大 / 含非有限数。"""
    if not isinstance(value, dict):
        raise ValueError("导出默认值必须是 JSON 对象")
    try:
        size = len(atomicio.dumps_json(value))
    except atomicio.AtomicWriteError as exc:
        raise ValueError(str(exc)) from exc
    if size > MAX_EXPORT_DEFAULTS_BYTES:
        raise ValueError("导出默认值太大")
    with _PREFS_LOCK:
        atomicio.write_json(
            export_defaults_path(), {"version": FORMAT_VERSION, "defaults": value}, indent=1
        )
    return value
