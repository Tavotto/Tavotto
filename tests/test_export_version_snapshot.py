"""导出的是哪一版：编辑后立即导出 / 慢渲染 + 第二次编辑 / 数据改动后的快照与明确重算（真 matplotlib worker）。

QA 2026-09-24 §5 SCI-07 / SCI-08。`tests/test_export_pipeline.py` 用假 worker 钉住了「中间 PDF 作业私有」
「带 override 的面板会被重画」；这里在**真链路**上量产物本身：

  * SCI-07：作业 1 带版本 A 开始后被人为放慢，期间热会话改成版本 B 并完成作业 2。两份产物各自只含
    自己那一版的标题，且各自回显自己的 `document_revision`——慢的旧作业不许把 B 当成自己的结果，
    也不许在事后覆盖 B 的文件。
  * SCI-08：出图后改磁盘数据。普通导出按**明确旧快照**继续（编辑保留、数据仍是旧的），回执的数据
    绑定核对如实写 `matched: False`；用户明确「重新构建」（`/api/engine/invalidate`）后再导出才用新数据，
    编辑照样重放，绑定核对回到 `matched: True`。（合同：docs/rules/backend/preparation-and-receipts.md
    「复用热态会话时不一致不是错误……不自动重算、不清编辑，用户要重算走既有 /api/engine/invalidate」。）

独立真值：脚本把它读到的数据序列原样写进图里一行文字（`DATA 0_9_2_10`），标题由 override 改成
`Version A` / `Version B`；产物用 PyMuPDF（与 RenderCore 写入器不同源）抽文字判定。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import exportjob, pool, project_watch

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
    with open("data.csv", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split(",")]
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot(range(len(ys)), ys, color="#1f77b4", label="alpha")
    ax.set_title("Original Title")
    ax.text(0.02, 0.9, "DATA " + "_".join(str(int(v)) for v in ys), transform=ax.transAxes)
    fig.savefig("Fig1.pdf")
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)

OLD_DATA, NEW_DATA = "0,9,2,10", "0,2,9,10"


@pytest.fixture
def project(tmp_path, monkeypatch):
    import pikepdf

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "data.csv").write_text(OLD_DATA, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Fig1.pdf")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _render(client, patches):
    r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


def _title_patch(manifest, text: str) -> list:
    gid = next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f.get("value") == "Original Title"
    )
    return [{"gid": gid, "prop": "text", "value": text}]


def _spec(name: str, patches: list, revision: str) -> dict:
    return {
        "scope": "canvas",
        "filename": name,
        "formats": ["pdf"],
        "document_revision": revision,
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 90,
            "objects": [
                {
                    "type": "panel",
                    "id": "Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 81.28,
                    "h_mm": 60.96,
                    "overrides": patches,
                }
            ],
        },
    }


def _text_of(body) -> str:
    out = next(o for o in body["outputs"] if o["format"] == "pdf")
    assert out["status"] == "done", body
    with pymupdf.open(Path(body["export_dir"]) / out["name"]) as doc:
        return doc[0].get_text()


def _wait(client, job_id: str) -> dict:
    for _ in range(1500):
        body = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled", "conflict"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"作业 {job_id} 30 秒没结束：{body}")


def _binding_matched(body) -> list:
    """产物 manifest 里回执公开事实的 `binding.matched` 取值（`receipt.public_facts()` 的那一段；
    递归找，不依赖它嵌在 provenance 的哪一层）。"""
    found: list = []

    def walk(v):
        if isinstance(v, dict):
            bc = v.get("binding")
            if isinstance(bc, dict) and "matched" in bc:
                found.append(bc["matched"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(body)
    return found


def test_a_slow_export_of_version_a_never_ships_version_b(project, monkeypatch):
    m, client, _figs = project
    base = _render(client, [])
    a, b = _title_patch(base, "Version A"), _title_patch(base, "Version B")
    _render(client, a)

    real = m._serialize_figure_with_worker
    gate = threading.Event()

    def slow(rel_id, overrides, *args, **kw):
        if any(p.get("value") == "Version A" for p in overrides):
            assert gate.wait(30), "第二次编辑与作业 2 没在时限内完成"
        return real(rel_id, overrides, *args, **kw)

    monkeypatch.setattr(m, "_serialize_figure_with_worker", slow)
    started = client.post("/api/export/start", json=_spec("Job A", a, "rev-A")).get_json()
    assert started.get("job_id"), started

    # 作业 1 还卡着：用户做了第二次编辑，并立即导出版本 B
    _render(client, b)
    r = client.post("/api/export", json=_spec("Job B", b, "rev-B"))
    body_b = r.get_json()
    assert r.status_code == 200 and body_b["status"] == "done", body_b
    gate.set()
    body_a = _wait(client, started["job_id"])
    assert body_a["status"] == "done", body_a

    text_a, text_b = _text_of(body_a), _text_of(body_b)
    assert "Version A" in text_a and "Version B" not in text_a, text_a
    assert "Version B" in text_b and "Version A" not in text_b, text_b
    assert body_a["document_revision"] == "rev-A" and body_b["document_revision"] == "rev-B"
    # 慢作业事后没有覆盖 B 的文件
    assert "Version B" in _text_of(body_b)


def test_edit_then_immediate_export_ships_the_edit_not_the_disk_file(project):
    m, client, _figs = project
    base = _render(client, [])
    a = _title_patch(base, "Version A")
    _render(client, a)
    r = client.post("/api/export", json=_spec("Now", a, "rev-now"))
    body = r.get_json()
    assert r.status_code == 200, body
    text = _text_of(body)
    assert "Version A" in text and "Original Title" not in text, text
    assert f"DATA {OLD_DATA.replace(',', '_')}" in text, text


def test_data_change_exports_the_explicit_snapshot_until_the_user_rebuilds(project):
    m, client, figs = project
    base = _render(client, [])
    a = _title_patch(base, "Version A")
    _render(client, a)

    (figs / "data.csv").write_text(NEW_DATA, encoding="utf-8")
    time.sleep(2.6)  # 越过 watcher 一个轮询周期：数据文件不是脚本，不该让会话失效

    snap = client.post("/api/export", json=_spec("Snapshot", a, "rev-snap")).get_json()
    text = _text_of(snap)
    assert "Version A" in text, "快照导出丢了编辑"
    assert f"DATA {OLD_DATA.replace(',', '_')}" in text, f"快照导出擅自用了新数据：{text}"
    assert False in _binding_matched(snap), f"回执没有如实写出数据已变：{_binding_matched(snap)}"

    r = client.post("/api/engine/invalidate", json={"id": "Fig1.pdf"})
    assert r.status_code == 200 and r.get_json()["invalidated"] is True
    _render(client, a)

    fresh = client.post("/api/export", json=_spec("Rebuilt", a, "rev-rebuilt")).get_json()
    text = _text_of(fresh)
    assert "Version A" in text, "重算后编辑没有重放"
    assert f"DATA {NEW_DATA.replace(',', '_')}" in text, f"重算后仍是旧数据：{text}"
    assert _binding_matched(fresh) and False not in _binding_matched(fresh), _binding_matched(fresh)
