"""项目自己的时间线作为恢复证据（ADR 0211 第一期 1–4）。

项目文件夹挪走 / 复制 / 换电脑 / 换 origin 之后，本机按绝对路径作键的会话状态
（`state/layout-sessions.json`）与前端 `tavotto.docIndex` 都认不出它；随文件夹走的只有
`<项目>/tavottofile/versions/<docId>.vx/`。这里只做**只读**的那一半：列出项目里有内容的
时间线、找最近一个非空节点、判断槽位归属记录指向的旧项目路径还在不在。归属的判定与转移
在 `app.py`（要槽位文档锁与会话状态）。

不按文件名 / mtime 猜（ADR 0027 §5）：身份只认 docId（目录名，过 `valid_doc_id`），
新旧只认节点自己记下的时刻与内容。已经存在、只有空节点（例如旧版本每次加载打的空 `open`
节点）的 `.vx` 没有非空节点，不进候选。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import documents, layoutsession, versionstore

#: 候选 `mode` / `owner` 的取值闭集——**唯一出处**（同源对：`web/src/lib/api.ts` 的同名常量与
#: `web/src/test/fakeSessionBackend.ts` 逐字相同，`tests/test_project_history_restore.py::test_vocabulary_mirrors_are_literal` 对拍）
HISTORY_MODES = ("slot", "node", "copy")
HISTORY_OWNERS = ("this", "none", "moved", "other", "unknown")

#: 一个项目最多回这么多份**有内容的**时间线（先筛掉空的再截：挪过的项目恰恰堆着成批只有空节点的 `.vx`）
MAX_STORES = 200
#: 目录里最多认这么多条目（防御：目录被塞进海量条目）；超过就如实报「没列全」
MAX_SCAN_ENTRIES = 5000
#: 每份时间线从新往旧最多读这么多个信封找非空节点（空节点只有几百字节，几十个足够）
MAX_NODE_READS = 40
#: 一份时间线为找非空节点最多读这么多字节
MAX_NODE_BYTES = 64 * 1024 * 1024


def doc_objects(doc: object) -> list[dict]:
    """布局文档 / 节点正文里的全部对象：schema 2 单画布 / schema 3、4 跨全部画布。

    **唯一出处**：`app._doc_objects` 直接转调这里。"""
    if not isinstance(doc, dict):
        return []
    if doc.get("schema") in (documents.SCHEMA_PROJECT, documents.SCHEMA_CHART_PROJECT):
        return [
            o
            for c in doc.get("canvases", [])
            if isinstance(c, dict)
            for o in c.get("objects", [])
            if isinstance(o, dict)
        ]
    objects = doc.get("objects")
    return [o for o in objects if isinstance(o, dict)] if isinstance(objects, list) else []


def store_names(versions_dir: Path) -> tuple[list[str], bool]:
    """`versions_dir` 里形如 `<docId>.vx` 的**真目录**（不跟随符号链接）的 docId（按名字排序），以及
    `truncated`：目录条目超过 `MAX_SCAN_ENTRIES`、没看全。

    只认目录列表给出的名字，不接受调用方给的路径。目录不在 = 没有；其余 `OSError` 往上抛
    （列不出 ≠ 没有）。**不在这里截个数**：有没有内容要读过才知道，截在筛选之前会把真的那份截掉。"""
    out: list[str] = []
    truncated = False
    try:
        with os.scandir(versions_dir) as it:
            for n, entry in enumerate(it):
                if n >= MAX_SCAN_ENTRIES:
                    truncated = True
                    break
                name = entry.name
                if not name.endswith(versionstore.STORE_SUFFIX):
                    continue
                doc_id = name[: -len(versionstore.STORE_SUFFIX)]
                if not layoutsession.valid_doc_id(doc_id):
                    continue
                try:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                out.append(doc_id)
    except (FileNotFoundError, NotADirectoryError):
        return [], False
    out.sort()
    return out, truncated


def latest_content_node(versions_dir: Path, doc_id: str) -> dict | None:
    """这份时间线里**最近一个有对象的节点**：`{vid, ts, canvasId, canvasName, schema, doc, canvases,
    objects}`；没有（空时间线 / 全是空节点 / 读不出）回 `None`。

    只读、不写盘（索引坏了只在内存里重建）。坏信封只赔掉自己；读的个数与字节有上界。"""
    try:
        idx = versionstore.list_index(versions_dir, doc_id)
    except (versionstore.VersionStoreError, OSError):
        return None
    if idx is None:
        return None
    spent = 0
    reads = 0
    for meta in reversed(idx.get("entries", [])):
        if reads >= MAX_NODE_READS or spent >= MAX_NODE_BYTES:
            break
        reads += 1
        spent += int(meta.get("_bytes", 0) or 0)
        vid = meta.get("id")
        if not isinstance(vid, str):
            continue
        try:
            v = versionstore.read(versions_dir, doc_id, vid)
        except (versionstore.VersionStoreError, OSError, ValueError, RecursionError):
            continue
        if v is None:
            continue
        doc = v.get("doc")
        objects = doc_objects(doc)
        if not objects:
            continue
        schema = doc.get("schema") if isinstance(doc, dict) else None
        canvases = doc.get("canvases") if isinstance(doc, dict) else None
        ts = v.get("ts")
        return {
            "vid": vid,
            "ts": ts if isinstance(ts, int) and not isinstance(ts, bool) else 0,
            "canvasId": v.get("canvasId") if isinstance(v.get("canvasId"), str) else None,
            "canvasName": v.get("canvasName") if isinstance(v.get("canvasName"), str) else None,
            "schema": schema if isinstance(schema, int) and not isinstance(schema, bool) else None,
            "doc": doc,
            "canvases": len(canvases) if isinstance(canvases, list) else 1,
            "objects": len(objects),
        }
    return None


def owner_path_gone(key: object) -> bool:
    """槽位归属记录里的项目键（归一化绝对路径）此刻在磁盘上**确定不存在**。

    只有「确定没有」（`FileNotFoundError` / `NotADirectoryError`）才回 True；其余 `OSError`
    （卷没挂载、权限、网络盘抖动）一律当它**还在**——宁可当成复制、新开一份，也不把别人的
    排版归属抢过来。`None`（没开项目那一组）不是路径，回 False。"""
    if not isinstance(key, str) or not key:
        return False
    try:
        os.stat(key)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except (OSError, ValueError):
        return False
    return False


def same_canvas_content(slot_doc: dict, node: dict) -> bool:
    """槽位里与节点同一张画布的对象 / 页面 / 参考线和节点一字不差（只比 schema 2 的单画布节点）。"""
    doc = node.get("doc")
    if not isinstance(doc, dict) or doc.get("schema") != 2:
        return False
    canvases = slot_doc.get("canvases")
    if not isinstance(canvases, list):
        return False
    target = next(
        (c for c in canvases if isinstance(c, dict) and c.get("id") == node.get("canvasId")), None
    )
    if target is None:
        return False

    def norm(v: object) -> str:
        return json.dumps(v, sort_keys=True, ensure_ascii=False)

    return (
        norm(target.get("objects")) == norm(doc.get("objects"))
        and norm(target.get("page")) == norm(doc.get("page"))
        and norm(target.get("guides", [])) == norm(doc.get("guides", []))
    )
