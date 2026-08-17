"""「写回原始文件」的事务纪律：自检不过不落盘，半成品不留在图库里。

写回是全工具唯一会**覆盖用户原始文件**的一步。这里看护两件事：

  * worker 报了任何 warning（元素不存在 / 属性不支持 / 应用失败）就**阻断**
    ——半对的图比报错糟糕得多：画布上有这条修改、写进 PDF 的没有，而原文件
    已经被换掉了，事后根本对不出来；
  * staging 阶段（导出 / 标注 / 自检）任何一步抛异常，已经生成的
    `.<名字>.updating` 临时文件必须清干净。以前只有「文件被占用」那一条路径
    清理，PDF 导出成功而 PNG 导出失败时垃圾文件就永久留在图库里了。

用 Flask test client + 假 worker（同 test_windows_regressions.py 的做法）：
这一段逻辑与真实渲染无关，不该为了测它去 spawn 一个科学栈解释器。
"""
import json
from pathlib import Path

import pymupdf
import pytest

from magplot import app as m
from magplot.engine import pool as engine_pool


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    yield m.app.test_client()
    m.reset_projects()
    engine_pool.stop_watcher()


def _figs(tmp_path, with_png: bool = True) -> Path:
    figs = tmp_path / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    if with_png:
        (figs / "Fig1.png").write_bytes(b"\x89PNG\r\n\x1a\nORIGINAL")
    (figs / "mm_registry.json").write_text(json.dumps({"version": 1, "scripts": {
        "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]},
    }}), encoding="utf-8")
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    m.open_project(str(figs))
    return figs


class _FakeWorker:
    """按真实协议回应的假 worker：export 回 {"ok", "path", "warnings"}。"""

    def __init__(self, warnings=(), fail_on=None):
        self.warnings = list(warnings)
        self.fail_on = fail_on      # 这个后缀的导出直接抛 WorkerError
        self.calls: list[str] = []
        self.built = True

    def export(self, stem, patches, path, fmt="pdf", dpi=600):
        self.calls.append(fmt)
        Path(path).write_bytes(f"NEW-{fmt}".encode())   # 半成品先落到 .updating
        if self.fail_on == fmt:
            raise engine_pool.WorkerError("导出炸了", "traceback…")
        return {"ok": True, "path": path, "warnings": list(self.warnings)}


def _use(monkeypatch, worker) -> None:
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **k: worker)


def _leftovers(figs: Path) -> list[str]:
    return [p.name for p in figs.iterdir() if p.name.endswith(".updating")]


def test_write_back_blocked_by_worker_warnings(client, tmp_path, monkeypatch):
    """orphan gid 这类 warning 必须阻断写回，且原文件一个字节都不动。"""
    figs = _figs(tmp_path)
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    before_png = (figs / "Fig1.png").read_bytes()
    _use(monkeypatch, _FakeWorker(warnings=["元素不存在（脚本可能已改动）: axes_0.texts_3"]))

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": [
                           {"gid": "axes_0.texts_3", "prop": "text", "value": "x"}]})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "write_back_warnings"
    assert body["warnings"] == ["元素不存在（脚本可能已改动）: axes_0.texts_3"]
    assert "axes_0.texts_3" in body["error"]         # 摘要进 error，界面直接可读

    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert (figs / "Fig1.png").read_bytes() == before_png
    assert _leftovers(figs) == []
    # 阻断的写回不进版本历史（基线仍是空的）
    assert m.load_baked(m.PROJECTS[m._project_id(figs.resolve())]) == {}


def test_write_back_dedupes_warnings_across_targets(client, tmp_path, monkeypatch):
    """PDF / PNG 两次导出报的是同一批 warning，不要在界面上重复两遍。"""
    figs = _figs(tmp_path)
    _use(monkeypatch, _FakeWorker(warnings=["属性不支持: figure.wat"]))
    body = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []}).get_json()
    assert body["warnings"] == ["属性不支持: figure.wat"]


def test_write_back_success_carries_empty_warnings(client, tmp_path, monkeypatch):
    """没有 warning 时正常替换，响应仍带 warnings 字段（前端据此确认全量应用）。"""
    figs = _figs(tmp_path)
    _use(monkeypatch, _FakeWorker())

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf",
                             "patches": [{"gid": "g", "prop": "p", "value": 1}]})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["warnings"] == []
    assert sorted(body["updated"]) == ["Fig1.pdf", "Fig1.png"]
    assert (figs / "Fig1.pdf").read_bytes() == b"NEW-pdf"
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"
    assert _leftovers(figs) == []
    ctx = m.PROJECTS[m._project_id(figs.resolve())]
    assert m._baseline_patches("Fig1", m.load_baked(ctx))[0]["gid"] == "g"


def test_write_back_cleans_updating_when_second_export_fails(client, tmp_path,
                                                             monkeypatch):
    """PDF 导出成功、PNG 导出抛 WorkerError：图库里不许留 `.Fig1.pdf.updating`。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    _use(monkeypatch, _FakeWorker(fail_on="png"))

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 500
    assert _leftovers(figs) == []
    assert (figs / "Fig1.pdf").read_bytes() == before     # 原文件完好


def test_history_restore_blocked_by_worker_warnings(client, tmp_path, monkeypatch):
    """历史恢复走同一条写回路径，同样必须被 warning 阻断。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    _use(monkeypatch, _FakeWorker(warnings=["应用失败 axes_0.title.text: boom"]))

    resp = client.post("/api/engine/history/restore", json={"id": "Fig1.pdf", "n": -1})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "write_back_warnings"
    assert (figs / "Fig1.pdf").read_bytes() == before
    assert _leftovers(figs) == []
