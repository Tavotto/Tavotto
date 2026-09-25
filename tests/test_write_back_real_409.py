"""写回事务在**真 matplotlib worker** 上的失败分支：每一环不过都 409，且 PDF + PNG 两份原件逐字节不变。

`tests/test_write_back.py` 用假 worker 把 prepare / verify / commit 的分支全走了一遍（manifest 是
测试自己造的）；`tests/test_worker_roundtrip.py` 末节在真链路上只钉了 `script_changed` 与「纯属性
像素分歧」两条。这里补齐真链路上其余几条（QA 2026-09-24 §5 SCI-04）：

  * prepare —— `expected_mtime` 过期 → `source_changed`；
  * verify  —— 引用一个脚本里不存在的 gid → worker 自己报 warning → `write_back_warnings`；
  * verify  —— 热会话的**几何**被绕过 pool 记账改过（FigS3 形状）→ `replay_divergence`，分歧落在
                 bbox 上（不是像素门那一道）；
  * commit  —— 第一个目标撞锁 → `file_locked`、`updated == []`；第二个目标撞锁 → 回滚第一个。

判据的主语：**用户图库里那两份原件的字节**（写回前读一次，409 之后再读一次），外加图库里没有
`.updating` 半成品、版本基线（baked）没有前进。每个用例的前提（两个目标都存在、热态确实是这组
patches）都在用例里先断言一次，免得前提没成立时「字节没变」恒真。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.set_title("Original Title")
    ax.legend()
    fig.savefig("Fig1.pdf")
    fig.savefig("Fig1.png", dpi=50)
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)

# 一张合法的 1×1 PNG（原件只需存在且可比字节；写回的 staging 由一次性 worker 现画）
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201"
    "a5f6d2a30000000049454e44ae426082"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    import pikepdf

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Fig1.pdf")
    (figs / "Fig1.png").write_bytes(_PNG_1X1)
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _snapshot(figs: Path) -> dict[str, bytes]:
    snap = {n: (figs / n).read_bytes() for n in ("Fig1.pdf", "Fig1.png")}
    assert all(snap.values()), "前提：两个写回目标都在盘上"
    return snap


def _assert_untouched(m, figs: Path, before: dict[str, bytes]) -> None:
    for name, data in before.items():
        assert (figs / name).read_bytes() == data, f"{name} 被 409 的写回改动了"
    assert not [p.name for p in figs.iterdir() if p.name.endswith(".updating")]
    assert m.load_baked(m.PROJECTS[m._project_id(figs.resolve())]) == {}, "409 不许推进基线"


def _render(client, patches):
    r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


def _title_gid(manifest) -> str:
    return next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f.get("value") == "Original Title"
    )


def test_prepare_stale_mtime_is_409_and_both_originals_keep_their_bytes(project):
    m, client, figs = project
    man = _render(client, [])
    patches = [{"gid": _title_gid(man), "prop": "text", "value": "Edited"}]
    _render(client, patches)
    before = _snapshot(figs)
    stale = int((figs / "Fig1.pdf").stat().st_mtime) - 3600

    r = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.pdf", "patches": patches, "expected_mtime": stale},
    )
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["code"] == "source_changed"
    _assert_untouched(m, figs, before)


def test_verify_worker_warning_on_a_missing_gid_is_409_and_nothing_is_replaced(project):
    m, client, figs = project
    _render(client, [])
    patches = [{"gid": "axes_0.lines_7", "prop": "color", "value": "#ff00ff"}]
    before = _snapshot(figs)

    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["code"] == "write_back_warnings"
    assert any("axes_0.lines_7" in w for w in body["warnings"]), body["warnings"]
    _assert_untouched(m, figs, before)


def test_verify_geometry_drift_in_the_hot_session_is_409_replay_divergence(project):
    """热会话几何被绕过 pool 记账改过：账本以为热态是 `[]`，实际子图挪了。"""
    m, client, figs = project
    _render(client, [])
    before = _snapshot(figs)
    worker = pool.get("fig1.py", str(figs), "main")
    resp = worker.request(
        {
            "cmd": "override",
            "stem": "Fig1",
            "patches": [{"gid": "axes_0", "prop": "position", "value": [0.30, 0.30, 0.40, 0.40]}],
        }
    )
    assert resp.get("ok") and not resp.get("warnings"), resp

    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["code"] == "replay_divergence"
    fields = {(d["gid"], d["field"]) for d in body["diffs"]}
    assert ("axes_0", "bbox") in fields, body["diffs"]
    _assert_untouched(m, figs, before)


@pytest.mark.parametrize("locked_index", [0, 1], ids=["first-target", "second-target"])
def test_commit_lock_is_409_file_locked_and_the_other_target_is_rolled_back(
    project, monkeypatch, locked_index
):
    m, client, figs = project
    man = _render(client, [])
    patches = [{"gid": _title_gid(man), "prop": "text", "value": "Edited"}]
    _render(client, patches)
    before = _snapshot(figs)

    real_replace = Path.replace
    seen: list[str] = []

    def replace(self, target):
        if str(self.name).endswith(".updating"):
            seen.append(Path(target).name)
            if len(seen) - 1 == locked_index:
                raise PermissionError(13, "locked by a PDF viewer", str(target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
    monkeypatch.setattr(Path, "replace", real_replace)

    assert len(seen) == locked_index + 1, seen
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["code"] == "file_locked"
    assert body["updated"] == []
    assert body["rollback_failed"] == []
    assert body["rolled_back"] == seen[:locked_index]
    _assert_untouched(m, figs, before)
