"""`scripts/ci/proctree.py` 按进程快照找后代时的两道护栏（11-27 Windows 合并组：结束「后代」时把 pytest 自己也结束了）。

快照里的父 pid 只是个号：父进程早退出、号被复用后，快照会说一个陌生进程是我们子进程的「孩子」。
真父子的孩子不早于父亲创建；调用者自己和它的祖先永远不能进名单。纯逻辑，POSIX 上也能验。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import proctree as pt  # noqa: E402


def ctime_of(table):
    return lambda pid: table.get(pid)


def test_a_real_tree_is_found_leaves_first():
    rows = [(10, 1), (11, 10), (12, 11)]
    assert pt._tree(10, rows, ctime_of({10: 100, 11: 110, 12: 120}), set()) == [12, 11]


def test_a_stale_parent_link_from_pid_reuse_is_not_a_child():
    # 20 的真父亲早就退出了；它的旧号 10 现在被我们刚起的子进程用着——20 比 10 还老，不是 10 的孩子
    rows = [(20, 10), (21, 20), (11, 10)]
    got = pt._tree(10, rows, ctime_of({10: 500, 11: 510, 20: 50, 21: 60}), set())
    assert got == [11]


def test_the_caller_and_its_ancestors_are_never_victims():
    # 即便创建时刻看起来合理，调用者（99）也不能被当成后代
    rows = [(99, 10), (11, 10)]
    got = pt._tree(10, rows, ctime_of({10: 100, 11: 110, 99: 120}), protected={99})
    assert got == [11]


def test_an_unreadable_creation_time_means_not_a_child():
    rows = [(11, 10)]
    assert pt._tree(10, rows, ctime_of({10: 100}), set()) == []


def test_lineage_follows_only_verified_parents():
    parent_of = {99: 50, 50: 7, 7: 1}
    # 7 比 50 年轻：50 的父 pid 7 是复用来的号，到 50 为止
    got = pt._lineage(99, parent_of, ctime_of({99: 300, 50: 200, 7: 250, 1: 1}))
    assert got == {99, 50}


def test_descendants_never_returns_the_calling_process(monkeypatch):
    """接线：`descendants` 真的把调用者 + 祖先当成受保护的。快照说调用者是子进程的孩子（复用来的号、时刻也凑巧），照样不杀。"""
    me = 4242
    rows = [(me, 10), (11, 10), (10, 1)]
    monkeypatch.setattr(pt, "IS_WINDOWS", True)
    monkeypatch.setattr(pt.os, "getpid", lambda: me)
    monkeypatch.setattr(pt, "_snapshot", lambda: rows)
    monkeypatch.setattr(pt, "_create_time", ctime_of({1: 1, 10: 100, 11: 110, me: 120}))
    assert pt.descendants(10) == [11]
