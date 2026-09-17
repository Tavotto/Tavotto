"""`engine/bakedbaseline.py`：写回基线存储与有效性判据，**不起 Flask**。

app 侧的包装（`load_baked` / `append_baked` 默认取当前项目）由 `test_paths_and_baked.py`
与 `test_write_back.py` 继续看护；这里量的是提出来的那份纯逻辑——根目录、项目 id、
「哪些 stem 是本项目的」全部显式递进来。判据的主语：**这一个项目的那一份文件**。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import bakedbaseline as bb

ALL = lambda stem: True  # noqa: E731
NONE = lambda stem: False  # noqa: E731


@pytest.fixture
def store(tmp_path):
    return bb.BakedBaselineStore(tmp_path / "baked", tmp_path / "legacy.json")


# ---------------------------------------------------------------- load / migrate


def test_missing_file_reads_as_empty_and_leaves_no_legacy_dependency(store, tmp_path):
    assert store.load("p1", stem_known=ALL) == {}
    # 迁移哪怕一条没搬也写出空 dict：「本项目确实没有基线」与「还没迁移」要分得开
    assert store.path_for("p1").is_file()
    assert json.loads(store.path_for("p1").read_text(encoding="utf-8")) == {}


def test_legacy_global_file_migrates_only_the_stems_this_project_knows(store, tmp_path):
    legacy = {
        "Fig1": {"versions": [{"ts": "2026-01-01 00:00:00", "patches": [{"gid": "g"}]}]},
        "Other": {"versions": [{"ts": "2026-01-01 00:00:00", "patches": [{"gid": "o"}]}]},
        "junk": "not a dict",
    }
    (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    data = store.load("p1", stem_known=lambda s: s == "Fig1")
    assert set(data) == {"Fig1"}
    # 旧文件不删：别的项目的 Other 还要等它自己迁移
    assert json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8")) == legacy
    # 第二次读走分键文件，不再翻旧文件（改了旧文件也不影响）
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")
    assert set(store.load("p1", stem_known=ALL)) == {"Fig1"}


def test_single_version_legacy_shape_is_upgraded_on_read(store):
    store.path_for("p1").parent.mkdir(parents=True)
    store.path_for("p1").write_text(
        json.dumps({"Fig1": {"patches": [{"gid": "g"}], "updated_at": "2026-01-02 03:04:05"}}),
        encoding="utf-8",
    )
    data = store.load("p1", stem_known=ALL)
    assert data["Fig1"] == {"versions": [{"ts": "2026-01-02 03:04:05", "patches": [{"gid": "g"}]}]}


def test_corrupt_file_reads_as_empty(store):
    store.path_for("p1").parent.mkdir(parents=True)
    store.path_for("p1").write_text("{not json", encoding="utf-8")
    assert store.load("p1", stem_known=ALL) == {}


def test_no_legacy_path_means_no_migration_attempt(tmp_path):
    s = bb.BakedBaselineStore(tmp_path / "baked")
    assert s.load("p1", stem_known=ALL) == {}
    assert s.path_for("p1").is_file()


# ---------------------------------------------------------------- append


def test_append_records_hash_files_and_trims(store):
    for i in range(bb.KEEP_VERSIONS + 5):
        store.append(
            "p1",
            "Fig1",
            [{"gid": "g", "prop": "p", "value": i}],
            stem_known=ALL,
            files={"Fig1.pdf": {"sha1": "x", "mtime_ns": 1, "size": 2}} if i == 0 else None,
        )
    versions = store.load("p1", stem_known=ALL)["Fig1"]["versions"]
    assert len(versions) == bb.KEEP_VERSIONS
    assert versions[-1]["patches"][0]["value"] == bb.KEEP_VERSIONS + 4
    assert versions[0]["patches"][0]["value"] == 5  # 最老的 5 条被裁掉
    assert versions[-1]["patch_hash"] == bb.patchspec.patch_hash(versions[-1]["patches"])
    assert "files" not in versions[-1]


def test_append_keeps_files_identity_when_given(store):
    ident = {"Fig1.pdf": {"sha1": "abc", "mtime_ns": 7, "size": 3}}
    store.append("p1", "Fig1", [{"gid": "g"}], stem_known=ALL, files=ident)
    assert store.load("p1", stem_known=ALL)["Fig1"]["versions"][-1]["files"] == ident


def test_projects_do_not_share_baselines(store):
    store.append("a", "Fig1", [{"gid": "g", "value": "A"}], stem_known=ALL)
    assert store.load("b", stem_known=ALL) == {}
    store.append("b", "Fig1", [{"gid": "g", "value": "B"}], stem_known=ALL)
    assert bb.baseline_patches("Fig1", store.load("a", stem_known=ALL))[0]["value"] == "A"
    assert bb.baseline_patches("Fig1", store.load("b", stem_known=ALL))[0]["value"] == "B"


def test_concurrent_appends_lose_nothing(store):
    def worker(i):
        store.append("p1", "Fig1", [{"gid": "g", "value": i}], stem_known=ALL)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    versions = store.load("p1", stem_known=ALL)["Fig1"]["versions"]
    assert sorted(v["patches"][0]["value"] for v in versions) == list(range(12))


def test_a_shared_lock_is_honoured(tmp_path):
    lock = threading.RLock()
    s = bb.BakedBaselineStore(tmp_path / "baked", lock=lock)
    with lock:  # 可重入：持锁的线程自己 append 不会死锁
        s.append("p1", "Fig1", [{"gid": "g"}], stem_known=ALL)
    assert bb.baseline_patches("Fig1", s.load("p1", stem_known=ALL)) == [{"gid": "g"}]


# ---------------------------------------------------------------- 纯判据


def test_baseline_version_and_patches():
    baked = {"Fig1": {"versions": [{"patches": [1]}, {"patches": [2]}]}, "Empty": {"versions": []}}
    assert bb.baseline_version("Fig1", baked) == {"patches": [2]}
    assert bb.baseline_patches("Fig1", baked) == [2]
    assert bb.baseline_version("Empty", baked) is None
    assert bb.baseline_patches("Nope", baked) == []


def _pdf(tmp_path: Path, content: bytes = b"%PDF-1.4 x") -> Path:
    p = tmp_path / "Fig1.pdf"
    p.write_bytes(content)
    return p


def _ident(p: Path) -> dict:
    st = p.stat()
    return {p.name: {"sha1": bb.sha1_of(p), "mtime_ns": st.st_mtime_ns, "size": st.st_size}}


def _forbid_reading(monkeypatch):
    """常态路径与「尺寸变了」都不许读内容——sha1 是最后一道、最贵的一道。"""

    def boom(_path):
        raise AssertionError("这条路不该读文件内容")

    monkeypatch.setattr(bb, "sha1_of", boom)


def test_matches_when_identity_unchanged_without_reading(tmp_path, monkeypatch):
    p = _pdf(tmp_path)
    version = {"files": _ident(p)}
    _forbid_reading(monkeypatch)
    assert bb.baseline_matches_file(version, p) is True


def test_size_change_invalidates_without_reading(tmp_path, monkeypatch):
    p = _pdf(tmp_path)
    version = {"files": _ident(p)}
    p.write_bytes(b"%PDF-1.4 rewritten by the user's build")
    _forbid_reading(monkeypatch)
    assert bb.baseline_matches_file(version, p) is False


def test_touch_keeps_baseline_because_sha1_agrees(tmp_path):
    p = _pdf(tmp_path)
    version = {"files": _ident(p)}
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000_000))
    assert bb.baseline_matches_file(version, p) is True


def test_same_size_different_content_invalidates(tmp_path):
    p = _pdf(tmp_path, b"AAAA")
    version = {"files": _ident(p)}
    p.write_bytes(b"BBBB")  # 同尺寸
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000_000))
    assert bb.baseline_matches_file(version, p) is False


def test_missing_file_never_matches(tmp_path):
    assert bb.baseline_matches_file({"files": {}}, tmp_path / "gone.pdf") is False


def test_legacy_entry_without_files_uses_ts_with_grace(tmp_path):
    p = _pdf(tmp_path)
    mtime = p.stat().st_mtime
    fresh = time.strftime(bb.TS_FORMAT, time.localtime(mtime))  # 写回时刻 ≈ 文件时刻
    assert bb.baseline_matches_file({"ts": fresh}, p) is True
    stale = time.strftime(bb.TS_FORMAT, time.localtime(mtime - bb.TS_GRACE_S - 60))
    assert bb.baseline_matches_file({"ts": stale}, p) is False
    # ts 解析不动：维持旧行为（当作有效），不拿猜出来的结论触发 heavy 重渲染
    assert bb.baseline_matches_file({"ts": "not-a-time"}, p) is True
    assert bb.baseline_matches_file({}, p) is True
