"""「写回原始文件」的事务纪律：prepare → verify → commit，任一环不过零改动。

写回是全工具唯一会**覆盖用户原始文件**的一步。这里看护整条事务：

  * **prepare**：素材在用户按下确认之后被外部改过（`expected_mtime` 对不上）
    → 409 `source_changed`；生成脚本在会话 spawn 之后被改过（sha1 对不上）
    → 409 `script_changed`。后者的窗口来自 mtime watcher 的 2 秒轮询——
    那一小段里热会话跑的还是旧代码，写进去的图与下次渲染出来的对不上。
  * **verify**：staging 一律由**全新的一次性 worker** 全量重放产出，再与热态
    manifest 逐元素比几何。热会话是增量的，它「现在的样子」未必等于「重开项目
    按这组 patches 重放一次的样子」（FigS3 事故就是这个差）——对不上就 409
    `replay_divergence`，并把分歧清单交出来。
    worker 报了任何 warning（元素不存在 / 属性不支持 / 应用失败）同样**阻断**
    ——半对的图比报错糟糕得多：画布上有这条修改、写进 PDF 的没有，而原文件
    已经被换掉了，事后根本对不出来。
    staging 阶段任何一步抛异常，已经生成的 `.<名字>.updating` 临时文件必须
    清干净，一次性 worker 也必须被回收。
  * **commit**：第 2 个目标撞上独占锁时，已经替换掉的要从本次备份**回滚**。
    一张图的 PDF 是新的、PNG 还是旧的，比整件事失败糟糕得多。

用 Flask test client + 假 worker（同 test_windows_regressions.py 的做法）：
这一段逻辑与真实渲染无关，不该为了测它去 spawn 一个科学栈解释器。真实链路
（真 matplotlib + 真重放）由 test_worker_roundtrip.py 的写回一节看护。
"""
import json
from pathlib import Path

import pymupdf
import pytest

from magplot import app as m
from magplot.engine import patchspec
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


def _manifest(*, bbox=(0.10, 0.10, 0.20, 0.20), size_mm=(35.28, 17.64)) -> dict:
    """写回事务只比几何，所以假 manifest 只带 gid / bbox / anchor / size_mm。

    size_mm 默认与 `_figs()` 造的 100×50pt 页面同尺寸，落盘后的尺寸自检才算
    真的走过一遍（100pt = 35.28mm）。
    """
    return {
        "stem": "Fig1",
        "size_mm": list(size_mm),
        "elements": [
            {"gid": "axes_0", "bbox": list(bbox)},
            {"gid": "axes_0.title", "bbox": [0.30, 0.05, 0.40, 0.05],
             "anchor": [0.50, 0.07]},
        ],
    }


class _FakeWorker:
    """按真实协议回应的假 worker（热会话与一次性重放共用一套接口）。

    `override` 把 manifest 落到 out_dir（真 worker 也是这么做的，写回事务正是
    从那儿读），`export` 回 {"ok", "path", "warnings"}。
    """

    def __init__(self, figs: Path, out_dir: Path, *, warnings=(), fail_on=None,
                 manifest=None, applied=()):
        self.script_name = "fig1.py"
        self.figures_dir = str(figs)
        self.entry = "main"
        self.base = out_dir
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.script_sha1 = engine_pool.script_sha1(str(figs), "fig1.py")
        self.warnings = list(warnings)
        self.fail_on = fail_on      # 这个后缀的导出直接抛 WorkerError
        self.manifest = manifest if manifest is not None else _manifest()
        self.calls: list[str] = []
        self.built = True
        self.shutdowns = 0
        # 热会话「最后应用的是哪一组 patches」：与写回请求一致时两份 manifest 才可比
        self.last_patch_hash = patchspec.patch_hash(list(applied))
        self._write_manifest()

    def _write_manifest(self) -> None:
        (self.out_dir / f"{self.manifest['stem']}.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.calls.append("override")
        self.last_patch_hash = patchspec.patch_hash(patches)
        self._write_manifest()
        return {"ok": True, "manifest": self.manifest, "warnings": list(self.warnings)}

    def export(self, stem, patches, path, fmt="pdf", dpi=600):
        self.calls.append(fmt)
        Path(path).write_bytes(f"NEW-{fmt}".encode())   # 半成品先落到 .updating
        if self.fail_on == fmt:
            raise engine_pool.WorkerError("导出炸了", "traceback…")
        return {"ok": True, "path": path, "warnings": list(self.warnings)}

    def shutdown(self):
        self.shutdowns += 1


def _use(monkeypatch, hot, fresh=None) -> list:
    """接上假 worker；返回被 `discard()` 回收的一次性 worker 清单。"""
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **k: hot)
    monkeypatch.setattr(m.engine_pool, "one_shot",
                        lambda *a, **k: fresh if fresh is not None else hot)
    discarded: list = []
    monkeypatch.setattr(m.engine_pool, "discard",
                        lambda w: (discarded.append(w), w.shutdown()) and None)
    return discarded


def _pair(figs: Path, tmp_path: Path, **kw) -> tuple[_FakeWorker, _FakeWorker]:
    """(热会话, 一次性重放) 两个假 worker，各自独立的 out_dir。

    `hot_*` 开头的关键字给热会话，其余给重放那个——写回事务里 staging 全部
    出自后者，所以「导出报 warning」「导出炸了」这类都设在重放侧。
    """
    hot_kw = {k[4:]: v for k, v in kw.items() if k.startswith("hot_")}
    fresh_kw = {k: v for k, v in kw.items() if not k.startswith("hot_")}
    hot = _FakeWorker(figs, tmp_path / "_hot", **hot_kw)
    fresh = _FakeWorker(figs, tmp_path / "_fresh", **fresh_kw)
    return hot, fresh


def _leftovers(figs: Path) -> list[str]:
    return [p.name for p in figs.iterdir() if p.name.endswith(".updating")]


def _mtime(p: Path) -> int:
    return int(p.stat().st_mtime)


# --------------------------- verify：worker warnings -------------------------
def test_write_back_blocked_by_worker_warnings(client, tmp_path, monkeypatch):
    """orphan gid 这类 warning 必须阻断写回，且原文件一个字节都不动。"""
    figs = _figs(tmp_path)
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    before_png = (figs / "Fig1.png").read_bytes()
    hot, fresh = _pair(figs, tmp_path,
                       warnings=["元素不存在（脚本可能已改动）: axes_0.texts_3"])
    discarded = _use(monkeypatch, hot, fresh)

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
    assert discarded == [fresh], "被阻断的写回也必须回收一次性 worker"
    # 阻断的写回不进版本历史（基线仍是空的）
    assert m.load_baked(m.PROJECTS[m._project_id(figs.resolve())]) == {}


def test_write_back_dedupes_warnings_across_targets(client, tmp_path, monkeypatch):
    """PDF / PNG 两次导出报的是同一批 warning，不要在界面上重复两遍。"""
    figs = _figs(tmp_path)
    hot, fresh = _pair(figs, tmp_path, warnings=["属性不支持: figure.wat"])
    _use(monkeypatch, hot, fresh)
    body = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []}).get_json()
    assert body["warnings"] == ["属性不支持: figure.wat"]


def test_write_back_cleans_updating_when_second_export_fails(client, tmp_path,
                                                             monkeypatch):
    """PDF 导出成功、PNG 导出抛 WorkerError：图库里不许留 `.Fig1.pdf.updating`。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    hot, fresh = _pair(figs, tmp_path, fail_on="png")
    discarded = _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 500
    assert _leftovers(figs) == []
    assert (figs / "Fig1.pdf").read_bytes() == before     # 原文件完好
    assert discarded == [fresh]


# ------------------------------ verify：干净重放 ------------------------------
def test_staging_comes_from_a_fresh_worker_not_the_hot_session(client, tmp_path,
                                                               monkeypatch):
    """写进用户原件的必须是**可复现的那一版**：热会话一次导出都不该被调用。"""
    figs = _figs(tmp_path)
    hot, fresh = _pair(figs, tmp_path)
    discarded = _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert hot.calls == [], "热会话被拿去出 staging 了"
    assert fresh.calls == ["override", "pdf", "png"], "重放只该 build/导出各一次"
    assert discarded == [fresh] and fresh.shutdowns == 1


def test_write_back_blocked_by_replay_divergence(client, tmp_path, monkeypatch):
    """热态 manifest 与干净重放对不上 = FigS3 级问题，阻断并交出分歧清单。"""
    figs = _figs(tmp_path)
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    before_png = (figs / "Fig1.png").read_bytes()
    patches = [{"gid": "axes_0.title", "prop": "pos_frac", "value": [0.5, 0.9]}]
    # 热会话最后应用的正是这组 patches（两份 manifest 因此可比），但重放出来的
    # 标题位置差了一大截——正是「热态所见 ≠ 重开后重放」的形状
    hot = _FakeWorker(figs, tmp_path / "_hot", applied=patches)
    fresh = _FakeWorker(figs, tmp_path / "_fresh",
                        manifest=_manifest(bbox=(0.10, 0.42, 0.20, 0.20)))
    discarded = _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": patches})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "replay_divergence"
    assert [(d["gid"], d["field"]) for d in body["diffs"]] == [("axes_0", "bbox")]
    assert body["diffs"][0]["hot"] == [0.10, 0.10, 0.20, 0.20]
    assert body["diffs"][0]["fresh"] == [0.10, 0.42, 0.20, 0.20]
    assert "报告给开发者" in body["error"]

    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert (figs / "Fig1.png").read_bytes() == before_png
    assert _leftovers(figs) == []
    assert discarded == [fresh]
    assert m.load_baked(m.PROJECTS[m._project_id(figs.resolve())]) == {}


def test_replay_comparison_tolerates_float_noise_and_stringified_numbers(
        client, tmp_path, monkeypatch):
    """manifest 经 JSON 落盘，numpy 标量可能被 default= 写成字符串。

    不统一 float() 化的话「"0.1"」与 0.1 会被判成分歧——一条真防线会变成
    天天误报的噪音，用户学到的是「这个提示可以无视」。
    """
    figs = _figs(tmp_path)
    hot = _FakeWorker(figs, tmp_path / "_hot")
    noisy = _manifest(bbox=("0.1", 0.1 + 1e-9, 0.2, 0.2))
    fresh = _FakeWorker(figs, tmp_path / "_fresh", manifest=noisy)
    _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["verification"] == {"replay": "ok", "elements": 2}


def test_a_hot_session_holding_other_patches_is_not_compared(client, tmp_path,
                                                             monkeypatch):
    """热态压根不是这组 patches 时不许拿去比——比出来的差异全是假的。

    跨面板同步、历史恢复都走这条：写回的是会话里没应用过的 patches。假报一次
    `replay_divergence`，用户学到的就是「这个提示可以无视」。
    """
    figs = _figs(tmp_path)
    hot = _FakeWorker(figs, tmp_path / "_hot",
                      applied=[{"gid": "axes_0.title", "prop": "text", "value": "热态"}])
    fresh = _FakeWorker(figs, tmp_path / "_fresh",
                        manifest=_manifest(bbox=(0.9, 0.9, 0.05, 0.05)))
    _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    v = resp.get_json()["verification"]
    assert v["replay"] == "fresh_only" and v["elements"] == 0
    assert v["reason"] == "hot_state_differs"


# --------------------------- prepare：素材 / 脚本变更 -------------------------
def test_write_back_rejects_a_stale_expected_mtime(client, tmp_path, monkeypatch):
    """素材在用户按下确认之后被外部改过：按旧状态覆盖 = 悄悄吃掉别人的改动。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)

    stale = _mtime(figs / "Fig1.pdf") - 100
    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": [], "expected_mtime": stale})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "source_changed"
    assert body["file"] == "Fig1.pdf"
    assert body["expected"] == stale and body["actual"] == _mtime(figs / "Fig1.pdf")
    assert "刷新素材" in body["error"]
    assert (figs / "Fig1.pdf").read_bytes() == before
    assert _leftovers(figs) == []
    assert fresh.calls == [], "前置校验没过就不该起一次性 worker"


def test_a_matching_expected_mtime_lets_the_write_back_through(client, tmp_path,
                                                               monkeypatch):
    figs = _figs(tmp_path)
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)
    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": [],
                             "expected_mtime": _mtime(figs / "Fig1.pdf")})
    assert resp.status_code == 200, resp.get_json()


def test_write_back_blocked_when_the_script_changed_under_the_session(
        client, tmp_path, monkeypatch):
    """watcher 有 2 秒轮询窗口：那一小段里热会话跑的还是旧代码，必须关死。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)
    (figs / "fig1.py").write_text("def main():\n    pass  # 改过了\n", encoding="utf-8")

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "script_changed" and body["script"] == "fig1.py"
    assert "重新渲染" in body["error"]
    assert (figs / "Fig1.pdf").read_bytes() == before
    assert _leftovers(figs) == []
    assert fresh.calls == []


# ------------------------------ commit：成功与回滚 ----------------------------
def test_write_back_success_carries_the_full_transaction_receipt(client, tmp_path,
                                                                 monkeypatch):
    """成功响应要能把「磁盘上这张图」与「哪一版 patches / 哪一份 manifest」对上。"""
    figs = _figs(tmp_path)
    patches = [{"gid": "g", "prop": "p", "value": 1}]
    hot, fresh = _pair(figs, tmp_path, hot_applied=patches)
    _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": patches})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["warnings"] == []
    assert sorted(body["updated"]) == ["Fig1.pdf", "Fig1.png"]
    assert body["patch_hash"] == patchspec.patch_hash(patches)
    assert body["manifest_hash"] == m._manifest_hash(fresh.manifest)
    assert set(body["source_sha1"]) == {"Fig1.pdf", "Fig1.png"}
    assert body["source_sha1"]["Fig1.pdf"] == m._sha1_of(figs / "Fig1.pdf")
    assert body["verification"] == {"replay": "ok", "elements": 2}
    assert "post_check" not in body        # 尺寸对得上就不该出现这个字段

    assert (figs / "Fig1.pdf").read_bytes() == b"NEW-pdf"
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"
    assert _leftovers(figs) == []
    ctx = m.PROJECTS[m._project_id(figs.resolve())]
    baked = m.load_baked(ctx)
    assert m._baseline_patches("Fig1", baked)[0]["gid"] == "g"
    # 版本条目带上权威 patch_hash，与响应里的是同一个值
    assert baked["Fig1"]["versions"][-1]["patch_hash"] == body["patch_hash"]


def test_post_check_reports_a_size_mismatch_without_rolling_back(client, tmp_path,
                                                                 monkeypatch):
    """落盘后尺寸对不上：如实报告（文件已换、备份仍在），不再自动回滚。"""
    figs = _figs(tmp_path)
    big = _manifest(size_mm=(200.0, 100.0))
    hot, fresh = _pair(figs, tmp_path, manifest=big, hot_manifest=big)
    # 导出的 PDF 得是真 PDF，post-check 才读得出页面尺寸
    real = pymupdf.open()
    real.new_page(width=100, height=50)
    pdf_bytes = real.tobytes()
    real.close()
    fresh.export = lambda stem, patches, path, fmt="pdf", dpi=600: (  # noqa: ARG005
        Path(path).write_bytes(pdf_bytes if fmt == "pdf" else b"NEW-png"),
        {"ok": True, "path": path, "warnings": []})[1]
    _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["post_check"] == "size_mismatch"
    assert sorted(body["updated"]) == ["Fig1.pdf", "Fig1.png"]   # 文件确实换了


def _lock_replace(monkeypatch, name: str) -> None:
    """让某个目标文件的原子替换抛 PermissionError（Windows 独占锁的形状）。"""
    real = Path.replace

    def guarded(self, target):
        if Path(target).name == name:
            raise PermissionError(f"[WinError 32] 另一个程序正在使用 {name}")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", guarded)


def test_a_locked_second_target_rolls_the_first_one_back(client, tmp_path,
                                                         monkeypatch):
    """PNG 撞锁时把已经换掉的 PDF 从备份恢复回去——绝不留下 PDF 新 / PNG 旧。"""
    figs = _figs(tmp_path)
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    before_png = (figs / "Fig1.png").read_bytes()
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)
    _lock_replace(monkeypatch, "Fig1.png")

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "file_locked" and body["file"] == "Fig1.png"
    assert body["rolled_back"] == ["Fig1.pdf"]
    assert body["rollback_failed"] == []
    assert body["updated"] == [], "回滚成功后没有任何文件处于「已被换掉」状态"
    assert "已回滚" in body["error"]

    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert (figs / "Fig1.png").read_bytes() == before_png
    assert _leftovers(figs) == []


def test_a_locked_first_target_reports_nothing_updated(client, tmp_path, monkeypatch):
    """第一个就撞锁：没有可回滚的，照旧如实报告。"""
    figs = _figs(tmp_path)
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)
    _lock_replace(monkeypatch, "Fig1.pdf")

    body = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []}).get_json()
    assert body["code"] == "file_locked" and body["file"] == "Fig1.pdf"
    assert body["updated"] == [] and body["rolled_back"] == []
    assert _leftovers(figs) == []


# ------------------------------ 历史恢复走同一条路 ----------------------------
def test_history_restore_blocked_by_worker_warnings(client, tmp_path, monkeypatch):
    """历史恢复走同一条写回路径，同样必须被 warning 阻断。"""
    figs = _figs(tmp_path)
    before = (figs / "Fig1.pdf").read_bytes()
    hot, fresh = _pair(figs, tmp_path, warnings=["应用失败 axes_0.title.text: boom"])
    _use(monkeypatch, hot, fresh)

    resp = client.post("/api/engine/history/restore", json={"id": "Fig1.pdf", "n": -1})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "write_back_warnings"
    assert (figs / "Fig1.pdf").read_bytes() == before
    assert _leftovers(figs) == []


def test_history_restore_returns_the_same_receipt_shape(client, tmp_path, monkeypatch):
    """恢复的响应与写回同构；恢复的是热态没应用过的一版，不谎称比对过。"""
    figs = _figs(tmp_path)
    hot = _FakeWorker(figs, tmp_path / "_hot",
                      applied=[{"gid": "axes_0.title", "prop": "text", "value": "热态"}])
    fresh = _FakeWorker(figs, tmp_path / "_fresh")
    _use(monkeypatch, hot, fresh)

    body = client.post("/api/engine/history/restore",
                       json={"id": "Fig1.pdf", "n": -1}).get_json()
    assert body["patches"] == []
    assert body["patch_hash"] == patchspec.patch_hash([])
    assert body["manifest_hash"] == m._manifest_hash(fresh.manifest)
    assert body["verification"]["replay"] == "fresh_only"
    assert sorted(body["source_sha1"]) == ["Fig1.pdf", "Fig1.png"]


def test_history_restore_honours_expected_mtime(client, tmp_path, monkeypatch):
    figs = _figs(tmp_path)
    hot, fresh = _pair(figs, tmp_path)
    _use(monkeypatch, hot, fresh)
    resp = client.post("/api/engine/history/restore",
                       json={"id": "Fig1.pdf", "n": -1,
                             "expected_mtime": _mtime(figs / "Fig1.pdf") - 100})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "source_changed"


def test_hot_manifest_is_matched_per_stem_not_per_worker(tmp_path):
    """池键是 (figures_dir, script_name)、**不含 stem**：一个脚本登记多个
    stem 时（examples 里的 fig2.py 就有两个）它们共用同一条会话。

    只看 worker 级的 `last_patch_hash`，就会出现这种事：先编辑 A 并渲染，
    再对 **B** 点「更新原图」，而两次的 patches 恰好同一组（最常见的就是
    两边都是空列表，或用户在两张图上做了同样的改动）——写回自检于是认为
    「热态就是这组 patches」，拿 B 那份**从没被 override 过**的热态
    manifest 去和 B 的全量重放结果比，比出一堆假分歧，一次合法的写回被
    409 `replay_divergence` 拦下。反过来（该比的没比）同样成立。

    判据必须按 stem 问：B 没被 override 过，它的热态是「脚本原样」，
    与请求里的这组 patches 不是一回事，直接判为不可比（回 None，
    响应据实写 `replay: fresh_only`）。
    """
    out = tmp_path / "out"
    out.mkdir()
    patches = [{"gid": "axes_0.title", "prop": "fontsize", "value": 7}]
    for stem in ("Fig2_yield", "Fig2_correlation"):
        man = {**_manifest(), "stem": stem}
        (out / f"{stem}.json").write_text(json.dumps(man), encoding="utf-8")

    class Hot:
        """热会话：只在 Fig2_yield 上应用过 patches。"""
        built = True
        out_dir = out
        last_patch_hash = patchspec.patch_hash(patches)     # worker 级：像是「就是它」
        last_patch_hash_by_stem = {"Fig2_yield": patchspec.patch_hash(patches)}

        def override(self, *a, **k):                        # pragma: no cover
            raise AssertionError("不可比的 stem 不该触发重渲染")

    hot = Hot()
    assert m._hot_manifest(hot, "Fig2_yield", patches) is not None       # 这个才可比
    assert m._hot_manifest(hot, "Fig2_correlation", patches) is None     # 另一个不可比
