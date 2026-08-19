"""缓存治理：渲染缓存预算、备份保留、AI 快照保留。"""
import os

from tavotto import app as m
from tavotto.engine import ai_bridge


def _aged(path, size: int, order: int) -> None:
    path.write_bytes(b"x" * size)
    t = 1_700_000_000 + order
    os.utime(path, (t, t))


def test_prune_render_cache_evicts_oldest_within_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)
    for i in range(5):
        _aged(tmp_path / f"f{i}.png", 100, i)
    removed = m.prune_render_cache(max_bytes=250)
    assert removed == 3
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["f3.png", "f4.png"]


def test_prune_render_cache_leaves_engine_subdir_alone(tmp_path, monkeypatch):
    """engine/ 下是 live 会话工件，不归渲染缓存预算管。"""
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)
    _aged(tmp_path / "top.png", 100, 0)
    sub = tmp_path / "engine" / "fig9"
    sub.mkdir(parents=True)
    _aged(sub / "keep.png", 100, 0)
    m.prune_render_cache(max_bytes=0)
    assert not (tmp_path / "top.png").exists()
    assert (sub / "keep.png").exists()


def test_prune_backups_keeps_newest_n(tmp_path):
    for i in range(25):
        d = tmp_path / f"b{i:02d}"
        d.mkdir()
        (d / "x.pdf").write_bytes(b"x")
        t = 1_700_000_000 + i
        os.utime(d, (t, t))
    m.prune_backups(tmp_path, keep=20)
    kept = sorted(p.name for p in tmp_path.iterdir())
    assert len(kept) == 20
    assert kept[0] == "b05" and kept[-1] == "b24"


def test_prune_snapshots_removes_whole_session_triplets(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    for i in range(25):
        sid = f"s{i:03d}xxxxxxxx"  # 生产为定长 12 hex，前缀不会互相包含
        _aged(tmp_path / f"{sid}.json", 10, i)
        _aged(tmp_path / f"{sid}__fig.py", 10, i)
        _aged(tmp_path / f"{sid}.stderr.log", 10, i)
    ai_bridge._prune_snapshots(keep=20)
    sidecars = sorted(p.stem for p in tmp_path.glob("*.json"))
    assert len(sidecars) == 20
    assert sidecars[0] == "s005xxxxxxxx"
    # 老会话的三件套一个不剩
    assert not list(tmp_path.glob("s004*"))
    # 新会话三件套完整
    assert len(list(tmp_path.glob("s024*"))) == 3
