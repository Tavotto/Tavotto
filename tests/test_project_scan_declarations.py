"""Import-time dependency evidence reads declarations through the bounded no-follow regular-file reader.

Thread 4210267708: a `requirements.txt` that is a symlink (UNC target -> SMB contact), a FIFO (blocks the scan
thread forever) or oversized must be refused and reported as partial, never read and never "no dependencies".
"""

import os
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import depresolve, projscan, scanbudget

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX link/FIFO semantics")


def _bounded(fn, seconds: float = 5.0):
    """在守护线程里跑 `fn`：旧行为会永久阻塞在 FIFO 上，这里转成断言失败而不是挂住整个测试进程。"""
    box: dict = {}

    def run():
        box["value"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "reader blocked on the FIFO"
    return box["value"]


def _evidence(root: Path):
    budget = scanbudget.Budget()
    return projscan.dependency_evidence(root, None, budget), budget


def _unreadable_paths(budget) -> list[str]:
    return [
        i.get("path")
        for i in budget.issues()
        if i["code"] == scanbudget.ISSUE_UNREADABLE_FILE and i["severity"] == "partial"
    ]


@posix_only
def test_symlinked_requirements_is_refused_and_reported_partial(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("numpy\n", encoding="utf-8")
    (project / "requirements.txt").symlink_to(outside)

    ev, budget = _evidence(project)

    assert ev["requirements"] == 0
    assert _unreadable_paths(budget) == ["requirements.txt"]
    # 非扫描的老路径（依赖门）保持跟随用户自己的链接
    assert [i.name for i in depresolve.declared_intents(project, None)] == ["numpy"]


@posix_only
def test_fifo_requirements_does_not_block_and_is_reported_partial(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    os.mkfifo(project / "requirements.txt")

    ev, budget = _bounded(lambda: _evidence(project))  # 没有写端：普通 open/read 会永远阻塞

    assert ev["requirements"] == 0
    assert _unreadable_paths(budget) == ["requirements.txt"]


@pytest.mark.parametrize("scan", [True, False])
def test_oversized_requirements_is_refused_not_half_read(tmp_path, scan):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text(
        "numpy\n" + "#" * (depresolve.MAX_DECL_BYTES + 1), encoding="utf-8"
    )

    intents = depresolve.declared_intents(project, None, no_follow=scan)

    assert [(i.kind, i.reason) for i in intents] == [
        (depresolve.INTENT_KIND_UNSUPPORTED, depresolve.UNSUPPORTED_UNREADABLE)
    ]
    if scan:
        _, budget = _evidence(project)
        assert _unreadable_paths(budget) == ["requirements.txt"]


@posix_only
def test_symlinked_include_and_requirements_dir_are_not_followed(tmp_path):
    project = tmp_path / "project"
    (project / "real").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("scipy\n", encoding="utf-8")
    (project / "requirements.txt").write_text(
        "-r linked.txt\n-r //srv/share/y.txt\n", encoding="utf-8"
    )
    (project / "linked.txt").symlink_to(outside / "x.txt")
    (project / "requirements").symlink_to(outside, target_is_directory=True)

    intents = depresolve.declared_intents(project, None, no_follow=True)

    assert not [i for i in intents if i.name == "scipy"]
    _, budget = _evidence(project)
    assert {"linked.txt", "requirements"} <= set(_unreadable_paths(budget))


def test_regular_requirements_still_read_on_the_scan_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("numpy>=1\n", encoding="utf-8")

    ev, budget = _evidence(project)

    assert ev["requirements"] == 1
    assert _unreadable_paths(budget) == []
