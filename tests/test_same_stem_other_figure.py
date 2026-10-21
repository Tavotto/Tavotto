"""不同的图写同一个 stem 的不同文件：两张都要捕获、原件各归各的、写回不覆盖别人的产物。

`panel.png` 由甲写、`panel.pdf` 由乙写，乙随后 `plt.close` 并出了作用域——原生两个文件都在；以前
捕获只剩甲，乙**整张丢了**（写入者表只握弱引用，乙被回收）。再往下一层：甲的原件按扩展名顺序被找成
`panel.pdf`（乙的文件），写回甲会把甲写进 `panel.pdf`，覆盖乙的产物。

钉住的合同：

1. 先认领者持有 `panel`（既有契约不变），乙以 `panel-2` 补进来，来源 savefig，带它自己的调用表；
   safe worker / native bridge / 浏览器三条入口产出同样的 stem（`figcapture.adopt_displaced_writers`）；
2. 原件只在这张图自己存过的格式里找（`find_original_artifact(formats=…)`）：甲 → `panel.png`，
   乙（`panel-2`）→ `panel.pdf`；
3. 写回不写另一张捕获图的原件（它在这次运行里写的文件）；要写的文件是别的图写的 → 409 `write_back_foreign_file`，原件零改动。
"""

from __future__ import annotations

import pytest

from tavotto.engine import figcapture, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def first():
    fig, ax = plt.subplots(figsize=(2.0, 1.6))
    ax.plot([0, 1], [0, 1])
    ax.set_title("first")
    fig.savefig("panel.png", dpi=100)


def second():
    fig, ax = plt.subplots(figsize=(2.4, 1.6))
    ax.plot([0, 1], [1, 0])
    ax.set_title("second")
    fig.savefig("panel.pdf")
    plt.close(fig)


def main():
    first()
    second()


if __name__ == "__main__":
    main()
"""


# --------------------------------------------------------------------------- 纯函数
def test_original_artifact_only_among_the_figures_own_formats(tmp_path):
    (tmp_path / "panel.pdf").write_bytes(b"%PDF")
    (tmp_path / "panel.png").write_bytes(b"\x89PNG")
    root = str(tmp_path)
    assert figcapture.find_original_artifact(root, "panel") == "panel.pdf"  # 不知道存过什么：旧判据
    assert figcapture.find_original_artifact(root, "panel", formats=["png"]) == "panel.png"
    assert figcapture.find_original_artifact(root, "panel", formats=["jpeg"]) is None
    (tmp_path / "panel.jpg").write_bytes(b"\xff\xd8")
    assert figcapture.find_original_artifact(root, "panel", formats=["jpeg"]) == "panel.jpg"


def test_saved_formats_unknown_is_none():
    assert figcapture.saved_formats(None) is None
    assert figcapture.saved_formats([]) is None
    assert figcapture.saved_formats([{"format": "png"}, {"format": "pdf"}]) == ["png", "pdf"]


# --------------------------------------------------------------------------- safe worker
def _titles(w, stem):
    man = w.override(stem, [])["manifest"]
    return [
        f["value"]
        for e in man["elements"]
        if e["role"] == "title"
        for f in e["editable"]
        if f["prop"] == "text"
    ]


@needs_worker
def test_safe_worker_captures_both_and_binds_each_own_artifact(tmp_path):
    (tmp_path / "panels.py").write_text(SCRIPT, encoding="utf-8")
    # 原件在磁盘上（原生跑过一次的样子）
    (tmp_path / "panel.png").write_bytes(b"\x89PNG")
    (tmp_path / "panel.pdf").write_bytes(b"%PDF")
    w = pool.one_shot("panels.py", str(tmp_path), "main")
    try:
        w.ensure_built()
        desc = {d["stem"]: d for d in w.last_build_descriptors}
        assert list(desc) == ["panel", "panel-2"]
        assert _titles(w, "panel") == ["first"]
        assert _titles(w, "panel-2") == ["second"]
        assert desc["panel-2"]["capture_source"] == "savefig"
        assert [c["format"] for c in desc["panel"]["savefig_calls"]] == ["png"]
        assert [c["format"] for c in desc["panel-2"]["savefig_calls"]] == ["pdf"]
        assert desc["panel"]["original_artifact"] == "panel.png"
        assert desc["panel-2"]["original_artifact"] == "panel.pdf"
        # 乙的图幅按它自己的保存定：2.4 in 宽
        assert round(desc["panel-2"]["size_mm"][0] / 25.4, 2) == 2.4
    finally:
        pool.discard(w)


# --------------------------------------------------------------------------- 浏览器 / native
@needs_worker
def test_browser_produces_the_same_stems(tmp_path):
    from test_compat_capture_parity import browser_load

    out = browser_load(SCRIPT, "panels.py", tmp_path / "ws")
    assert out["ok"], out
    assert [f["stem"] for f in out["figures"]] == ["panel", "panel-2"]


# --------------------------------------------------------------------------- 写回
@pytest.fixture
def client(tmp_path, monkeypatch):
    from tavotto import app as m
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    yield m.app.test_client()
    m.reset_projects()
    project_watch.stop()


def _fake_pair(tmp_path, monkeypatch, other_artifact):
    """全量重放的描述符：Fig1 自己，加上（可选）同 stem 补认领的另一张图 `Fig1-2`，原件是 `other_artifact`。"""
    import test_write_back as wb

    figs = wb._figs(tmp_path)
    hot, fresh = wb._pair(figs, tmp_path)
    fresh.last_build_descriptors = [
        {"stem": "Fig1", "original_artifact": "Fig1.png", "savefig_calls": [{"format": "png"}]}
    ]
    if other_artifact:
        fresh.last_build_descriptors.append(
            {
                "stem": "Fig1-2",
                "original_artifact": other_artifact,
                "savefig_calls": [{"format": "pdf"}],
            }
        )
    discarded = wb._use(monkeypatch, hot, fresh)
    return figs, fresh, discarded, wb


def test_write_back_refuses_a_file_another_figure_wrote(client, tmp_path, monkeypatch):
    figs, fresh, discarded, wb = _fake_pair(tmp_path, monkeypatch, "Fig1.pdf")
    before = {p: (figs / p).read_bytes() for p in ("Fig1.pdf", "Fig1.png")}
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "write_back_foreign_file"
    assert body["params"] == {"file": "Fig1.pdf"}
    assert {p: (figs / p).read_bytes() for p in before} == before
    assert wb._leftovers(figs) == []
    assert discarded == [fresh]
    assert "pdf" not in fresh.calls and "png" not in fresh.calls, "一个临时文件都不该导出"


def test_write_back_writes_only_the_figures_own_formats(client, tmp_path, monkeypatch):
    figs, fresh, _, wb = _fake_pair(tmp_path, monkeypatch, "Fig1.pdf")
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.png", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf, "别的图写的 PDF 不许被覆盖"
    assert "pdf" not in fresh.calls
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"


def test_write_back_without_known_formats_keeps_the_old_targets(client, tmp_path, monkeypatch):
    import test_write_back as wb

    figs = wb._figs(tmp_path)
    hot, fresh = wb._pair(figs, tmp_path)  # 假 worker 没有描述符 = 不知道存过什么
    wb._use(monkeypatch, hot, fresh)
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert sorted(c for c in fresh.calls if c in ("pdf", "png")) == ["pdf", "png"]


def test_a_same_name_file_no_figure_wrote_is_still_written_back(client, tmp_path, monkeypatch):
    """守卫的主语是「别的图写的」：这张图只存了 PNG、磁盘上另有一份没人在这次运行里写过的同名 PDF
    （手工导出 / 旧脚本留下的）——它照旧是写回目标，打开它写回也照常成功（#874 的用例就是这种项目）。"""
    figs, fresh, _, wb = _fake_pair(tmp_path, monkeypatch, None)
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert (figs / "Fig1.pdf").read_bytes() == b"NEW-pdf"
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"
