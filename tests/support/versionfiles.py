"""版本时间线测试用的读盘辅助（ADR 0140，D2 起写入一律进新存储 `<doc_id>.vx/`）。

测试不该关心历史躺在哪种存储里：`versions()` 给出当前权威存储里的完整版本列表，
`legacy_equivalent_bytes()` 给出「旧单文件此刻会是什么字节」（字节预算量的就是它），
`snapshot()` 给出整个时间线存储的全部文件（拒绝类用例据此断言「磁盘零改动」）。
"""

from __future__ import annotations

from tavotto import app as m
from tavotto.engine import versionstore as vs


def store_root(doc_id: str = "d1"):
    path = m._versions_path(doc_id)
    return vs.store_dir(path.parent, path.stem)


def versions(doc_id: str = "d1") -> list[dict]:
    """完整版本列表（含 `doc`）：新存储在就读它，否则读旧单文件。"""
    path = m._versions_path(doc_id)
    got = vs.read_all(path.parent, path.stem)
    return m._load_versions(doc_id) if got is None else got[1]


def legacy_equivalent_bytes(doc_id: str = "d1") -> bytes:
    return m.engine_atomicio.dumps_json({"versions": versions(doc_id)})


def snapshot(doc_id: str = "d1") -> list[tuple[str, bytes]]:
    """新存储目录下每个文件的 (相对路径, 字节)，排序后可直接比较。"""
    root = store_root(doc_id)
    if not root.exists():
        return []
    return sorted(
        (str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    )
