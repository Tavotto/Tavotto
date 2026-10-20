"""版本时间线的新存储：每版一个自描述信封文件 + 一份小索引（ADR 0140）。

旧存储（`app._load_versions` / `_save_versions`）把一份文档的全部历史放在一个
`<doc_id>.json` 里，追加 = 读整份 → 追加 → 裁 → 整写（issue #263）。本模块是它的
替身：追加只写**一个信封 + 22 KB 左右的索引**，与历史长度无关。D1b 起 `app.py` 经
`_Timeline` 读写它（读兼容新旧；写入走哪边见 `app.VERSIONS_WRITE_NEW`）。

布局（`versions_dir` 是 `…/versions/`，与旧文件、`thumbs/` 同级）::

    <doc_id>.json            旧文件：迁移后冻结（不删、不改、新程序不再写）
    <doc_id>.vx/             本模块的目录。名字带 `.vx`：doc_id 里没有 "."，撞不上
                             `thumbs/`，也撞不上任何 `*.json`
        index.json           提交点：{"format","gen","updated","entries":[…]}
        migration.json       迁移记录（只在从旧文件迁来的文档里有）
        s/<vid>.json         信封：第 1 行 = 头 JSON，第 2 行 = 文档正文

**新索引绝不与旧文件同名**（`INDEX_NAME` 与 `<doc_id>.json` 永不相等，
`tests/test_versionstore.py::test_index_never_shares_a_name_with_the_legacy_file`）：
旧程序会把 `<doc_id>.json` 里没有 `doc` 的条目当历史，下一次整写就静默毁掉它。

事务语义：所有落盘走 `atomicio.write_bytes`（tmp → fsync → replace → fsync 目录）。
**先写信封，再写索引，索引是唯一的提交点**；删除 / 裁剪**先写索引，后删信封**
（缝里只会留下孤儿信封，孤儿比丢索引好）。

**并发：调用方负责持锁。** 本模块内不取任何锁——一次「读索引 → 写信封 → 写索引」
必须在调用方的跨进程文档锁（`<doc_id>.versions`，D0）里完成。只读函数不要求持锁，
但读到的可能是别人提交前的快照。

只用标准库 + `atomicio` + `documents`（读回旧文件时的非有限数闸）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import atomicio, documents

FORMAT = 1
INDEX_NAME = "index.json"
MIGRATION_NAME = "migration.json"
ENVELOPE_DIR = "s"
STORE_SUFFIX = ".vx"
#: 与 `app._VERSION_ID_RE` 严格同源（`test_constants_mirror_app`）。id 直接做信封文件名，
#: 所以进来的每个 id 都过这一道，不合形状的拒收——绝不拼出越界路径。
VERSION_ID_RE = re.compile(r"^v[0-9a-f]+-[0-9a-f]+$")

# ---- 现行裁剪策略（D1/D2 保持语义逐字不变；换存储与调策略分开评审，ADR 0140 §六）----
#: **这里是唯一出处。** `app.VERSION_KEEP_AUTO` / `VERSION_KEEP_TOTAL` / `VERSION_KEEP_BYTES` /
#: `VERSION_MOMENTS` / `_VERSION_ID_RE` 只是对这些对象的别名（测试按 `is` 钉死，
#: `tests/test_versionstore.py::test_app_constants_are_aliases_of_the_single_source`），
#: 旧单文件与新存储共用同一份策略与判据。
KEEP_AUTO = 60
KEEP_TOTAL = 120
KEEP_BYTES = 24 * 1024 * 1024
MOMENTS = frozenset({"export", "writeback", "open", "close", "before_restore", "save"})
DEFAULT_NAME = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}$")

#: 一次裁剪策略：`(索引条目列表, 受护 id) -> 保留的条目列表`（保持顺序）。
Policy = Callable[[list[dict], "str | None"], list[dict]]


class VersionStoreError(Exception):
    """本模块抛出的错误的基类。"""


class IndexUnreadable(VersionStoreError):
    """索引读不出来（`OSError`）或来自未来格式：**拒写、不重建**——不知道那里是什么。"""


class EnvelopeCorrupt(VersionStoreError):
    """某个信封与索引 / 自带校验对不上。只影响这一条，不拖垮整条时间线。"""


class LegacyUnreadable(VersionStoreError):
    """旧文件解析失败 / 形状不对：不迁移、旧文件原样（#264）。"""


def _checkpoint(name: str) -> None:
    """崩溃点钩子。生产里什么也不做；测试替换它来在确定的位置注入失败 / `os._exit`。"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


# ---------------------------------------------------------------- 路径


def store_dir(versions_dir: Path, doc_id: str) -> Path:
    return Path(versions_dir) / f"{doc_id}{STORE_SUFFIX}"


def _index_path(root: Path) -> Path:
    return root / INDEX_NAME


def _envelope_name(vid: str, gen: int = 0) -> str:
    return f"{vid}.json" if gen == 0 else f"{vid}.{gen}.json"


def _envelope_path(root: Path, vid: str, gen: int = 0) -> Path:
    """信封**不可变**：内容一旦写下就不再原地改。元信息更新写新一代 `<vid>.<gen>.json`
    （第 0 代是 `<vid>.json`），索引的 `_gen` 指向生效的那一代——索引提交是唯一的生效点。"""
    if not VERSION_ID_RE.fullmatch(vid):
        raise VersionStoreError(f"不合法的版本 id: {vid!r}")
    return root / ENVELOPE_DIR / _envelope_name(vid, gen)


def _meta_path(root: Path, meta: dict) -> Path:
    return _envelope_path(root, meta["id"], int(meta.get("_gen", 0)))


_ENVELOPE_NAME = re.compile(r"^(?P<vid>v[0-9a-f]+-[0-9a-f]+)(?:\.(?P<gen>[1-9][0-9]*))?\.json$")


# ---------------------------------------------------------------- 信封


def _envelope(entry: dict, doc: Any, seq: int, gen: int = 0) -> tuple[bytes, int]:
    """信封字节与正文字节数。头不含 `doc`，重建只需读第 1 行。"""
    doc_bytes = atomicio.dumps_json(doc)
    head = atomicio.dumps_json(
        {
            "format": FORMAT,
            "seq": seq,
            "gen": gen,
            "entry": entry,
            "doc_len": len(doc_bytes),
            "doc_sha": _sha(doc_bytes),
        }
    )
    # json 在 indent=None 下把换行转义成 \n，两行格式因此无歧义；断言防的是将来有人改 dumps_json。
    assert b"\n" not in head and b"\n" not in doc_bytes
    return head + b"\n" + doc_bytes, len(doc_bytes)


def _parse_envelope(raw: bytes, *, what: str) -> tuple[dict, bytes]:
    line1, sep, rest = raw.partition(b"\n")
    try:
        head = json.loads(line1)
        ok = (
            sep
            and isinstance(head, dict)
            and head.get("format") == FORMAT
            and isinstance(head.get("entry"), dict)
            and len(rest) == head["doc_len"]
            and _sha(rest) == head["doc_sha"]
        )
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise EnvelopeCorrupt(f"信封损坏: {what}")
    return head, rest


def _read_head(path: Path) -> dict:
    with open(path, "rb") as f:
        head = json.loads(f.readline())
    if not (isinstance(head, dict) and isinstance(head.get("entry"), dict)):
        raise ValueError("head")
    return head


def _legacy_size(entry: dict, doc_len: int) -> int:
    """这一条在**旧文件**里占的字节：`dumps_json({**entry, "doc": doc})` 的长度。

    现行字节预算量的就是它；逐条与 `dumps_json(v)` 相等由 `test_legacy_size_equals_dumps`
    钉住。"""
    return len(atomicio.dumps_json(entry)) + len(b', "doc": ') + doc_len


def _meta(entry: dict, env_len: int, doc_len: int, doc_sha: str, seq: int, gen: int = 0) -> dict:
    """索引条目 = 原条目（不含 `doc`）+ 簿记字段。簿记字段名带下划线，不会撞上版本字段。

    `_seq` 是追加序号（单调、不必连续）：重建靠它还原顺序，不靠 ts（复制出来的版本 ts 可以相同）。"""
    return {
        **entry,
        "_seq": seq,
        "_gen": gen,
        "_bytes": env_len,
        "_legacy_bytes": _legacy_size(entry, doc_len),
        "_doc_sha": doc_sha,
    }


_BOOKKEEPING = ("_seq", "_gen", "_bytes", "_legacy_bytes", "_doc_sha")


def public_entry(meta: dict) -> dict:
    """索引条目去掉簿记字段，得到与旧条目（去掉 `doc`）逐字段相同的 dict。"""
    return {k: v for k, v in meta.items() if k not in _BOOKKEEPING}


# ---------------------------------------------------------------- 索引读写


def has_store(versions_dir: Path, doc_id: str) -> bool:
    """新存储是否已是这份文档的权威（读侧据此决定读哪边）。

    索引在 → 是；索引不在但有信封、且**没有未完成的迁移记录** → 是（索引丢了，重建）；
    其余（含「迁移进行中被打断」）→ 否，旧文件仍是权威。

    **三态：确定没有 / 确定有 / 判不了。** 判不了（权限变化、网络文件系统的瞬时 I/O 错误……
    `.vx` 在而读不了）抛 `IndexUnreadable`——绝不折成「没有」：折成「没有」调用方就会退回
    旧格式写，`.vx` 恢复后新存储重新成为权威，刚确认的检查点就消失了。"""
    root = store_dir(versions_dir, doc_id)
    if _exists(_index_path(root)):
        return True
    if _migration_pending(root):
        return False
    return _has_envelopes(root)


def _exists(path: Path) -> bool:
    """确定有 / 确定没有；其余错误（`PermissionError` 等）= 判不了 → `IndexUnreadable`。"""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise IndexUnreadable(str(exc)) from exc
    return True


def _has_envelopes(root: Path) -> bool:
    try:
        return any(n.endswith(".json") for n in os.listdir(root / ENVELOPE_DIR))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise IndexUnreadable(str(exc)) from exc


def _migration_record(root: Path) -> dict | None:
    try:
        rec = json.loads((root / MIGRATION_NAME).read_bytes())
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return None
    except OSError as exc:  # 记录在而读不了：判不了「迁移是否进行中」，不当作没有
        raise IndexUnreadable(str(exc)) from exc
    return rec if isinstance(rec, dict) else None


def _migration_pending(root: Path) -> bool:
    rec = _migration_record(root)
    return bool(rec) and rec.get("state") == "pending"


def _empty_index(now: int) -> dict:
    return {"format": FORMAT, "gen": 0, "updated": now, "entries": []}


def _write_index(root: Path, idx: dict, now: int) -> dict:
    """写索引（提交点），返回写下的那份。`recovered` 是一次性的读结果标记，不落盘。"""
    idx = {k: v for k, v in idx.items() if k != "recovered"}
    idx = {**idx, "gen": idx.get("gen", 0) + 1, "updated": now}
    atomicio.write_bytes(_index_path(root), atomicio.dumps_json(idx))
    return idx


def _rebuild(root: Path) -> dict:
    """从信封目录重建索引（只读每个信封的第 1 行）。灾难恢复：已被删 / 裁掉但信封
    还在的版本会复活，所以结果带 `recovered: true` 让调用方告诉用户。每个 id 取最高的一代，
    所以最后一次**尝试过**的元数据（哪怕索引没来得及提交）可能被采用——只影响名字 / 标签，
    不影响图内容（ADR 0140 §三）。已提交的改名不会丢。"""
    best: dict[str, tuple[int, int, int, dict]] = {}  # vid -> (gen, seq, ...)
    try:
        names = sorted(os.listdir(root / ENVELOPE_DIR))
    except OSError:
        names = []
    for name in names:
        nm = _ENVELOPE_NAME.match(name)
        if nm is None:
            continue
        vid, gen = nm["vid"], int(nm["gen"] or 0)
        p = root / ENVELOPE_DIR / name
        try:
            head = _read_head(p)
            entry = head["entry"]
            if entry.get("id") != vid:
                continue
            seq = int(head.get("seq", 0))
            meta = _meta(entry, p.stat().st_size, head["doc_len"], head["doc_sha"], seq, gen)
        except (OSError, ValueError, KeyError, TypeError):
            continue  # 坏信封只赔掉自己
        if vid not in best or gen > best[vid][0]:
            best[vid] = (gen, seq, 0, meta)  # 每个 id 取最高的一代
    found = [(seq, vid, meta) for vid, (_, seq, _, meta) in best.items()]
    found.sort(key=lambda t: (t[0], t[1]))
    return {
        "format": FORMAT,
        "gen": 0,
        "updated": 0,
        "recovered": True,
        "entries": [m for _, _, m in found],
    }


def _valid_meta(m: object) -> bool:
    """索引条目的结构自检：任何一条不合格，整份索引当损坏走重建（元数据都在信封头里）。"""
    if not isinstance(m, dict):
        return False
    vid = m.get("id")
    if not isinstance(vid, str) or not VERSION_ID_RE.fullmatch(vid):
        return False
    ints = ("_seq", "_bytes", "_legacy_bytes", "_gen")
    for k in ints:
        v = m.get(k, 0 if k == "_gen" else None)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return False
    return isinstance(m.get("_doc_sha"), str)


def _load_index(root: Path, now: int, *, write: bool) -> dict | None:
    """读索引四档。`None` = 这个目录里还没有存储（既无索引也无信封）。

    * 正常 → 索引；
    * 缺失但有信封 / 字节坏或形状不对 → 重建；`write` 时先把坏字节存成
      `index.corrupt-<sha>.json`（对齐 layoutsession），再写重建结果；只读时仅内存重建；
    * 读不动（`OSError`）/ 未来格式 → `IndexUnreadable`，不重建、不写。"""
    path = _index_path(root)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if _migration_pending(root) or not _has_envelopes(root):
            return None
        raw = None
    except OSError as exc:
        raise IndexUnreadable(str(exc)) from exc
    if raw is not None:
        try:
            idx = documents.loads_document(raw)
        except ValueError:  # 含 DocumentError（非有限数）与 JSON / 编码错误
            idx = None
        if isinstance(idx, dict) and isinstance(idx.get("entries"), list):
            fmt = idx.get("format")
            if isinstance(fmt, int) and fmt > FORMAT:
                raise IndexUnreadable(f"索引来自更新的格式 {fmt}")
            if fmt == FORMAT and all(_valid_meta(m) for m in idx["entries"]):
                return idx
    rebuilt = _rebuild(root)
    if write:
        if raw is not None:
            atomicio.write_bytes(root / f"index.corrupt-{_sha(raw)}.json", raw)
        rebuilt = {**_write_index(root, rebuilt, now), "recovered": True}
    return rebuilt


def rebuild_index(versions_dir: Path, doc_id: str, *, now: int, write: bool = False) -> dict:
    """显式重建（只读时在内存里做）。返回索引 dict，`recovered: true`。"""
    root = store_dir(versions_dir, doc_id)
    idx = _rebuild(root)
    if write:
        if _index_path(root).exists():
            try:
                raw = _index_path(root).read_bytes()
                atomicio.write_bytes(root / f"index.corrupt-{_sha(raw)}.json", raw)
            except OSError as exc:
                raise IndexUnreadable(str(exc)) from exc
        idx = {**_write_index(root, idx, now), "recovered": True}
    return idx


def list_index(
    versions_dir: Path, doc_id: str, *, now: int = 0, write: bool = False
) -> dict | None:
    """索引（含簿记字段）；没有新存储返回 `None`。只读路径别传 `write`。"""
    return _load_index(store_dir(versions_dir, doc_id), now, write=write)


def entries(versions_dir: Path, doc_id: str, *, now: int = 0) -> list[dict] | None:
    """与旧条目（去掉 `doc`）逐字段相同的列表；没有新存储返回 `None`。"""
    idx = list_index(versions_dir, doc_id, now=now)
    return None if idx is None else [public_entry(m) for m in idx["entries"]]


def read(versions_dir: Path, doc_id: str, vid: str) -> dict | None:
    """一条完整版本（`{**entry, "doc": …}`，与旧条目逐字段相同）；索引里没有返回 `None`。

    信封与索引对不上 → `EnvelopeCorrupt`，只影响这一条。"""
    root = store_dir(versions_dir, doc_id)
    idx = _load_index(root, 0, write=False)
    if idx is None:
        return None
    meta = next((m for m in idx["entries"] if m.get("id") == vid), None)
    if meta is None:
        return None
    try:
        raw = _meta_path(root, meta).read_bytes()
    except FileNotFoundError as exc:
        raise EnvelopeCorrupt(f"信封缺失: {vid}") from exc
    head, rest = _parse_envelope(raw, what=vid)
    if meta.get("_doc_sha") != head["doc_sha"]:
        raise EnvelopeCorrupt(f"信封与索引不符: {vid}")
    # 元信息的权威在索引（改名先写信封、后写索引，两步之间被杀时信封头比索引新）
    return {**public_entry(meta), "doc": documents.loads_document(rest)}


def read_all(versions_dir: Path, doc_id: str) -> tuple[dict, list[dict]] | None:
    """整条时间线（含 `doc`）一次读出：`(索引, [旧条目形状…])`；没有新存储返回 `None`。

    只读、不要求持锁。坏信封只赔掉自己：那一条没有 `doc`（`{}`），带 `_damaged: True`，
    其余照常。信封缺失可能只是和别的进程的裁剪赛跑（索引已换、信封刚删），所以缺失时
    重读一次索引再试，仍缺才算坏。"""
    root = store_dir(versions_dir, doc_id)
    for attempt in (0, 1):
        idx = _load_index(root, 0, write=False)
        if idx is None:
            return None
        out: list[dict] = []
        raced = False
        for meta in idx["entries"]:
            vid = meta.get("id")
            entry = public_entry(meta)
            try:
                # 按索引的 _gen 读生效的那一代
                head, rest = _parse_envelope(_meta_path(root, meta).read_bytes(), what=vid)
                if meta.get("_doc_sha") != head["doc_sha"]:
                    raise EnvelopeCorrupt(vid)
                out.append({**entry, "doc": documents.loads_document(rest)})
            except FileNotFoundError:
                raced = True
                out.append({**entry, "doc": {}, "_damaged": True})
            except (EnvelopeCorrupt, ValueError, VersionStoreError):
                out.append({**entry, "doc": {}, "_damaged": True})
        if not raced or attempt:
            return idx, out
    raise AssertionError("unreachable")


def legacy_size_with_entry(meta: dict, new_entry: dict) -> int:
    """`meta` 这一版把条目换成 `new_entry` 之后在旧文件里占的字节（正文不变）。"""
    return (
        meta["_legacy_bytes"]
        - len(atomicio.dumps_json(public_entry(meta)))
        + len(atomicio.dumps_json(new_entry))
    )


def legacy_file_size(metas: list[dict]) -> int:
    """这些条目按旧文件算法拼出来的文件字节数（外壳 + 分隔符 + 每条）。"""
    return 14 + 2 + sum(m["_legacy_bytes"] for m in metas) + 2 * max(len(metas) - 1, 0)


# ---------------------------------------------------------------- 写


def _commit(
    root: Path,
    idx: dict,
    kept: list[dict],
    dropped: Iterable[dict],
    now: int,
    stale: Iterable[Path] = (),
    out_index: dict | None = None,
) -> None:
    """先写索引（提交点），后删被裁的信封与被取代的旧一代。

    `out_index` 给了就把刚提交的索引放进去：调用方不必为了拿到它再读一次盘
    （写已经持久化之后的额外读取失败，不该让请求报错）。"""
    written = _write_index(root, {**idx, "entries": kept}, now)
    if out_index is not None:
        out_index.clear()
        out_index.update(written)
    _checkpoint("after_index")
    for path in [*(_meta_path(root, m) for m in dropped), *stale]:
        try:
            path.unlink()
        except OSError:
            pass  # 孤儿信封由 gc_orphans 收


def _apply_policy(metas: list[dict], policy: Policy | None, keep_id: str | None) -> list[dict]:
    return metas if policy is None else policy(metas, keep_id)


def _next_seq(metas: list[dict]) -> int:
    return max((int(m.get("_seq", 0)) for m in metas), default=0) + 1


def append(
    versions_dir: Path,
    doc_id: str,
    version: dict,
    *,
    now: int,
    policy: Policy | None = None,
    keep_id: str | None = None,
    out_index: dict | None = None,
) -> dict:
    """追加一版（`version` 是旧条目形状，含 `doc`）。返回新条目的公开形状（无 `doc`）。

    写入的文件集合恒为：一个新信封 + 一份索引（+ 被裁版本的信封删除）。
    先确保索引存在（空的），再写信封——于是「首次追加在信封写完、索引提交前被杀」时
    重开看到的是一份空历史而不是被收养的半成品。"""
    root = store_dir(versions_dir, doc_id)
    vid = version.get("id")
    entry = {k: v for k, v in version.items() if k != "doc"}
    _envelope_path(root, str(vid))  # 校验 id 形状
    idx = _load_index(root, now, write=True)
    if idx is None:
        if _migration_pending(root):
            raise VersionStoreError("迁移未完成：先 migrate_legacy，再追加")
        idx = _write_index(root, _empty_index(now), now)
    if any(m.get("id") == vid for m in idx["entries"]):
        raise VersionStoreError(f"版本 id 已存在: {vid}")
    seq = _next_seq(idx["entries"])
    env, doc_len = _envelope(entry, version["doc"], seq)
    atomicio.write_bytes(_envelope_path(root, vid), env)
    _checkpoint("after_envelope")
    meta = _meta(entry, len(env), doc_len, _head_sha(env), seq)
    metas = [*idx["entries"], meta]
    kept = _apply_policy(metas, policy, keep_id)
    kept_ids = {m["id"] for m in kept}
    _commit(
        root, idx, kept, [m for m in metas if m["id"] not in kept_ids], now, out_index=out_index
    )
    return public_entry(meta)


def _head_sha(env: bytes) -> str:
    return json.loads(env.partition(b"\n")[0])["doc_sha"]


def update(
    versions_dir: Path,
    doc_id: str,
    vid: str,
    changes: dict,
    *,
    now: int,
    remove: Iterable[str] = (),
    policy: Policy | None = None,
    out_index: dict | None = None,
) -> dict | None:
    """改名 / 改说明 / 转正等元信息改动：`changes` 并入条目，`remove` 里的键删掉。

    信封不可变：写这一个版本的**新一代**信封（O(单版)），再写索引（唯一生效点），之后才删旧一代；之后过一遍策略（取消命名、转正会让
    某一档越过上限，现行 `_save_versions` 每次写入都裁，这里同样），受护的是这一条。
    索引里没有该版返回 `None`。"""
    root = store_dir(versions_dir, doc_id)
    idx = _load_index(root, now, write=True)
    if idx is None:
        return None
    pos = next((i for i, m in enumerate(idx["entries"]) if m.get("id") == vid), None)
    if pos is None:
        return None
    old = idx["entries"][pos]
    old_path = _meta_path(root, old)
    try:
        raw_old = old_path.read_bytes()
    except FileNotFoundError as exc:  # 索引有、信封没了：与 `read()` 一致，只赔这一条
        raise EnvelopeCorrupt(f"信封缺失: {vid}") from exc
    head, rest = _parse_envelope(raw_old, what=vid)
    gone = set(remove)
    entry = {k: v for k, v in head["entry"].items() if k not in gone}
    entry.update(changes)
    gen = int(old.get("_gen", 0)) + 1
    env, doc_len = _envelope(entry, documents.loads_document(rest), int(old["_seq"]), gen)
    atomicio.write_bytes(_envelope_path(root, vid, gen), env)  # 新一代；旧一代在索引提交前原样不动
    _checkpoint("after_envelope")
    meta = _meta(entry, len(env), doc_len, head["doc_sha"], old["_seq"], gen)
    metas = list(idx["entries"])
    metas[pos] = meta
    kept = _apply_policy(metas, policy, vid)
    kept_ids = {m["id"] for m in kept}
    dropped = [m for m in metas if m["id"] not in kept_ids]
    _commit(
        root,
        idx,
        kept,
        [m for m in dropped if m["id"] != vid],
        now,
        stale=[old_path],
        out_index=out_index,
    )
    if vid in {m["id"] for m in dropped}:  # 自己被裁（策略没护住）：新一代也要收
        _meta_path(root, meta).unlink(missing_ok=True)
    return public_entry(meta)


def delete(
    versions_dir: Path, doc_id: str, vid: str, *, now: int, out_index: dict | None = None
) -> bool:
    """删一版：**先写索引，后删信封**。索引里没有返回 `False`。"""
    root = store_dir(versions_dir, doc_id)
    idx = _load_index(root, now, write=True)
    if idx is None:
        return False
    kept = [m for m in idx["entries"] if m.get("id") != vid]
    if len(kept) == len(idx["entries"]):
        return False
    _commit(
        root, idx, kept, [m for m in idx["entries"] if m.get("id") == vid], now, out_index=out_index
    )
    return True


def prune(
    versions_dir: Path,
    doc_id: str,
    policy: Policy,
    *,
    now: int,
    keep_id: str | None = None,
    out_index: dict | None = None,
) -> list[str]:
    """对现有索引单独过一遍策略，返回被裁的 id。"""
    root = store_dir(versions_dir, doc_id)
    idx = _load_index(root, now, write=True)
    if idx is None:
        return []
    kept = policy(idx["entries"], keep_id)
    kept_ids = {m["id"] for m in kept}
    dropped = [m for m in idx["entries"] if m["id"] not in kept_ids]
    if dropped:
        _commit(root, idx, kept, dropped, now, out_index=out_index)
    return [m["id"] for m in dropped]


# ---------------------------------------------------------------- 孤儿与 tmp


def gc_orphans(
    versions_dir: Path, doc_id: str, *, now: float, min_age_s: float = atomicio.ORPHAN_TMP_MIN_AGE_S
) -> list[str]:
    """清孤儿信封、过期 tmp、已完成迁移的残留标记，返回删掉的名字。持锁调用。

    孤儿信封 = 索引之外的 `s/*.json`，且 mtime 比 `min_age_s` 老（正在写的不碰）。
    **不收养**：它可能是已删 / 已裁的版本，收养会让它复活。索引缺失 / 损坏时一个信封
    也不删（那时它们是重建的原料）。tmp 两处都要扫：索引 tmp 在 `.vx/`，信封 tmp 在 `.vx/s/`。"""
    root = store_dir(versions_dir, doc_id)
    removed: list[str] = []
    try:
        idx = _load_index(root, 0, write=False)
    except IndexUnreadable:
        return removed
    if idx is not None and not idx.get("recovered"):
        live = {_envelope_name(m["id"], int(m.get("_gen", 0))) for m in idx["entries"]}
        try:
            names = os.listdir(root / ENVELOPE_DIR)
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json") or name in live:
                continue
            p = root / ENVELOPE_DIR / name
            try:
                if now - p.stat().st_mtime < min_age_s:
                    continue
                p.unlink()
            except OSError:
                continue
            removed.append(name)
    removed += atomicio.reap_orphan_tmps(root / ENVELOPE_DIR, now=now)
    removed += atomicio.reap_orphan_tmps(root, now=now)
    return removed


# ---------------------------------------------------------------- 迁移


def _read_legacy(legacy_path: Path) -> tuple[bytes, list[dict]]:
    try:
        raw = Path(legacy_path).read_bytes()
        data = documents.loads_document(raw)
    except (OSError, ValueError) as exc:  # DocumentError 是 ValueError 的子类
        raise LegacyUnreadable(str(exc)) from exc
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, list) or not all(
        isinstance(v, dict)
        and isinstance(v.get("id"), str)
        and VERSION_ID_RE.fullmatch(v["id"])
        and "doc" in v
        for v in versions
    ):
        raise LegacyUnreadable("旧文件形状不是 {versions:[{id,…,doc}]}")
    return raw, versions


def _legacy_stat(legacy_path: Path) -> dict:
    st = Path(legacy_path).stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def migrate_legacy(
    versions_dir: Path,
    doc_id: str,
    legacy_path: Path,
    *,
    now: int,
    policy: Policy | None = None,
) -> dict:
    """旧单文件 → 新存储。持锁调用；幂等；**旧文件全程只读，从不删、不改、不改名**。

    返回 `{"status": …, "imported": n}`，status 取：

    * `migrated`：首次迁移完成；
    * `already`：已迁移且旧文件自迁移后没有变化；
    * `imported`：旧文件在迁移后被旧版程序追加过（降级后又升级）——把迁移点**之后**新增
      的条目按 id 去重导入，不覆盖、不丢；迁移时已有的 id（哪怕已在新存储里被删）不再复活；
    * `no_legacy`：没有旧文件，什么也没做。

    旧文件读不出来 / 形状不对 → `LegacyUnreadable`，不建任何东西（#264 的出口见 `init_empty`）。

    崩溃安全：先写 `migration.json`（state=pending，含旧文件 sha 与 id 集合），再写信封，
    读回校验，**最后写索引**。索引之前被杀 → 读侧仍以旧文件为权威（`has_store` 为假），
    重跑复用已一致的信封；索引之后被杀 → 索引已是权威，重跑补记 committed。"""
    root = store_dir(versions_dir, doc_id)
    legacy_path = Path(legacy_path)
    if not legacy_path.is_file():
        return {"status": "no_legacy", "imported": 0}

    idx = _load_index(root, now, write=True)
    rec = _migration_record(root)
    if idx is not None:
        if rec is None:
            return {"status": "already", "imported": 0}  # 不是从旧文件迁来的，没有可比的基线
        if rec.get("state") == "pending":
            rec = {**rec, "state": "committed"}  # 上次死在「索引已提交、记录未收尾」
            atomicio.write_bytes(root / MIGRATION_NAME, atomicio.dumps_json(rec))
        return _import_fork(root, idx, rec, legacy_path, now, policy)

    raw, versions = _read_legacy(legacy_path)
    seen: set[str] = set()
    uniq: list[dict] = []
    dup = 0
    for v in versions:
        if v["id"] in seen:
            dup += 1  # 跨进程撞 id 留下的重复（D0 之前）：保留先出现的一条
            continue
        seen.add(v["id"])
        uniq.append(v)
    record = {
        "format": FORMAT,
        "state": "pending",
        "name": legacy_path.name,
        **_legacy_stat(legacy_path),
        "sha": _sha(raw),
        "sha_full": hashlib.sha256(raw).hexdigest(),
        "count": len(versions),
        "dropped_duplicates": dup,
        "ids": [v["id"] for v in uniq],
    }
    atomicio.write_bytes(root / MIGRATION_NAME, atomicio.dumps_json(record))
    metas: list[dict] = []
    expected: dict[str, bytes] = {}
    for seq, v in enumerate(uniq, start=1):
        entry = {k: x for k, x in v.items() if k != "doc"}
        try:
            env, doc_len = _envelope(entry, v["doc"], seq)
        except atomicio.AtomicWriteError as exc:  # 旧文件里有 1e400 之类读得进、写不出的数
            raise LegacyUnreadable(exc.message) from exc
        p = _envelope_path(root, v["id"])
        try:
            same = p.read_bytes() == env  # 上次迁移留下的、逐字节一致的信封直接复用
        except OSError:
            same = False
        if not same:
            atomicio.write_bytes(p, env)
        expected[v["id"]] = env
        metas.append(_meta(entry, len(env), doc_len, _head_sha(env), seq))
    _checkpoint("mig_after_blobs")
    for vid, env in expected.items():  # 读回校验：磁盘上的就是要写进索引的那份
        if _envelope_path(root, vid).read_bytes() != env:
            raise VersionStoreError(f"迁移读回校验失败: {vid}")
    _checkpoint("mig_before_index")
    kept = _apply_policy(metas, policy, None)
    kept_ids = {m["id"] for m in kept}
    _commit(root, _empty_index(now), kept, [m for m in metas if m["id"] not in kept_ids], now)
    _checkpoint("mig_after_index")
    back = _load_index(root, now, write=False)
    if back is None or [m["id"] for m in back["entries"]] != [m["id"] for m in kept]:
        raise VersionStoreError("迁移后索引读回不一致")
    atomicio.write_bytes(
        root / MIGRATION_NAME, atomicio.dumps_json({**record, "state": "committed"})
    )
    return {"status": "migrated", "imported": 0}


def _import_fork(
    root: Path, idx: dict, rec: dict, legacy_path: Path, now: int, policy: Policy | None
) -> dict:
    """旧文件分叉检测与导入：先比 size + mtime_ns，变了才读全文比 hash。"""
    try:
        st = _legacy_stat(legacy_path)
    except OSError:
        return {"status": "already", "imported": 0}
    if st["size"] == rec.get("size") and st["mtime_ns"] == rec.get("mtime_ns"):
        return {"status": "already", "imported": 0}
    raw, versions = _read_legacy(legacy_path)  # 读不出来 → 抛，新存储不受影响
    if _sha(raw) == rec.get("sha"):
        return {"status": "already", "imported": 0}
    known = set(rec.get("ids", ())) | {m["id"] for m in idx["entries"]}
    fresh: list[dict] = []
    for v in versions:
        if v["id"] in known:
            continue
        known.add(v["id"])
        fresh.append(v)
    metas = list(idx["entries"])
    seq = _next_seq(metas)
    for v in fresh:
        entry = {k: x for k, x in v.items() if k != "doc"}
        try:
            env, doc_len = _envelope(entry, v["doc"], seq)
        except atomicio.AtomicWriteError as exc:
            raise LegacyUnreadable(exc.message) from exc
        atomicio.write_bytes(_envelope_path(root, v["id"]), env)
        metas.append(_meta(entry, len(env), doc_len, _head_sha(env), seq))
        seq += 1
    kept = _apply_policy(metas, policy, None)
    kept_ids = {m["id"] for m in kept}
    _commit(root, idx, kept, [m for m in metas if m["id"] not in kept_ids], now)
    new_rec = {
        **rec,
        **st,
        "sha": _sha(raw),
        "count": len(versions),
        # 这次观察到的**全部**旧文件 id（含重试时已经在索引里的）：崩在提交与记录之间后重试，
        # fresh 为空，只并 fresh 的话这些 id 永远进不了记录，删掉后会再次导入（已删版本复活）
        "ids": sorted(set(rec.get("ids", ())) | {v["id"] for v in versions}),
    }
    atomicio.write_bytes(root / MIGRATION_NAME, atomicio.dumps_json(new_rec))
    return {"status": "imported", "imported": len(fresh)}


def init_empty(
    versions_dir: Path, doc_id: str, *, now: int, legacy_path: Path | None = None
) -> dict:
    """为「旧文件解析失败」的文档开一份空历史（#264，D2 接线时调用）。

    旧文件原样保留（读一次只为记 size / sha 作可恢复的标记，不写它）；索引里带
    `legacy_unreadable`，列表响应据此告诉用户「旧历史还在、可恢复」，不再 409 卡死。
    已有新存储则不动，返回现有索引。"""
    root = store_dir(versions_dir, doc_id)
    if _index_path(root).exists():
        return _load_index(root, now, write=False) or _empty_index(now)
    idx = _empty_index(now)
    if legacy_path is not None:
        try:
            raw = Path(legacy_path).read_bytes()
            idx["legacy_unreadable"] = {
                "name": Path(legacy_path).name,
                "size": len(raw),
                "sha": _sha(raw),
            }
        except OSError:
            idx["legacy_unreadable"] = {"name": Path(legacy_path).name}
    return _write_index(root, idx, now)


# ---------------------------------------------------------------- 现行策略（D1：逐字不变）


def is_named(v: dict) -> bool:
    """镜像 `app._is_named`。"""
    if "named" in v:
        return bool(v["named"])
    if v.get("auto"):
        return False
    name = str(v.get("name") or "").strip()
    return bool(name) and not DEFAULT_NAME.match(name)


def kind(v: dict) -> str:
    """镜像 `app._version_kind`：named / moment / auto / manual。"""
    if is_named(v):
        return "named"
    if v.get("moment") in MOMENTS:
        return "moment"
    return "auto" if v.get("auto") else "manual"


def sacrifice_order(metas: list[dict], keep_id: str | None = None) -> list[int]:
    """镜像 `app._sacrifice_order`：auto → moment → manual；最新一条、受护的一条、命名节点不牺牲。"""
    newest = len(metas) - 1
    tiers: dict[str, list[int]] = {"auto": [], "moment": [], "manual": []}
    for i, v in enumerate(metas):
        k = kind(v)
        if i != newest and (keep_id is None or v.get("id") != keep_id) and k in tiers:
            tiers[k].append(i)
    return tiers["auto"] + tiers["moment"] + tiers["manual"]


def legacy_policy(
    metas: list[dict],
    keep_id: str | None = None,
    *,
    keep_auto: int | None = None,
    keep_total: int | None = None,
    keep_bytes: int | None = None,
) -> list[dict]:
    """现行「两条条数上限 + 24 MB 字节」策略，逐条对拍 `app._save_versions`
    （`tests/test_versionstore.py::test_legacy_policy_matches_save_versions`）。

    字节账按旧文件的算法：外壳 + 分隔符 + 每条 `_legacy_bytes`，所以 D1 的保留结果与
    现行完全相同；换存储不换策略。

    三个上限默认取本模块的常量；调用方（`app`）按调用时刻的 `VERSION_KEEP_*` 显式传入，
    于是 `app` 侧的常量既是别名也仍是可被测试 monkeypatch 的旋钮。"""
    keep_auto = KEEP_AUTO if keep_auto is None else keep_auto
    keep_total = KEEP_TOTAL if keep_total is None else keep_total
    keep_bytes = KEEP_BYTES if keep_bytes is None else keep_bytes
    excess = sum(1 for v in metas if kind(v) == "auto") - keep_auto
    if excess > 0:
        autos = [i for i in sacrifice_order(metas, keep_id) if kind(metas[i]) == "auto"]
        drop = set(autos[:excess])
        metas = [v for i, v in enumerate(metas) if i not in drop]
    unnamed = sum(1 for v in metas if not is_named(v))
    if unnamed > keep_total:
        drop = set(sacrifice_order(metas, keep_id)[: unnamed - keep_total])
        metas = [v for i, v in enumerate(metas) if i not in drop]
    head, sep, tail = 14, 2, 2  # b'{"versions": [' / b", " / b"]}"
    size = head + tail + sum(v["_legacy_bytes"] for v in metas) + sep * max(len(metas) - 1, 0)
    dropped: set[int] = set()
    for i in sacrifice_order(metas, keep_id):
        if size <= keep_bytes:
            break
        dropped.add(i)
        size -= metas[i]["_legacy_bytes"] + sep
    return [v for i, v in enumerate(metas) if i not in dropped]
