"""同一个 stem 的多个变体（Phase F）。

画布上可以放两个指向同一文件、override 不同的面板——那是两张不同的图。
后端要保证的两件事：

* `/api/engine/render` 能把**这一次**的 SVG 与 manifest 放在同一个响应里
  （`inline_svg`）。分两跳取的话，另一个变体的渲染插进来就会拿到别人的 SVG
  而元素框还是这次的。
* `/api/engine/preview_png` 按给定 patches 出图，与热会话当前是哪个变体无关
  （`/api/engine/png` 从 live figure 直接 savefig，那是「谁最后渲染谁说了算」）。
"""

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import patchspec, pool as engine_pool, previewbudget


@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_pool.stop_watcher()


def _figs(tmp_path, name="variants"):
    figs = tmp_path / name
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    return figs


class _FakeWorker:
    """记下每次调用的参数；不碰科学栈，也不起子进程。"""

    def __init__(self, png_path=None):
        self.built = True
        self.rev = 7
        self.renders: list[dict] = []
        self.previews: list[dict] = []
        self._png = png_path

    #: 这个假 worker 要装成哪一档预览（None = 装成不认识 `preview` 的老 worker）
    preview: dict | None = None

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.renders.append(
            {"patches": patches, "preview_dpi": preview_dpi, "inline_svg": inline_svg}
        )
        resp = {"manifest": {"elements": []}, "warnings": []}
        if self.preview is not None:
            resp["preview"] = self.preview
        raster = (self.preview or {}).get("mode") == previewbudget.MODE_RASTER
        if inline_svg and not raster:
            # worker 把刚写完的那份读回来；这里用 patches 做指纹，好断言配对
            resp["svg"] = f"<svg data-variant='{len(patches)}'/>"
        return resp

    def preview_png(self, stem, patches, width_px, tag):
        self.previews.append({"patches": patches, "width": width_px, "tag": tag})
        return self._png


def _stub_engine(monkeypatch, worker):
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "x.py", "entry": "main", "cost": "light"},
    )
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **kw: worker)


def _open(client, tmp_path, name="variants"):
    figs = _figs(tmp_path, name)
    client.post("/api/projects/open", json={"path": str(figs)})
    return figs


# --------------------------- inline_svg（F1） -------------------------------


def test_render_can_return_the_svg_inline(client, tmp_path, monkeypatch):
    """要了就给，而且是**这一次**那份；不要就一个字段都不多。"""
    _open(client, tmp_path)
    worker = _FakeWorker()
    _stub_engine(monkeypatch, worker)

    plain = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []}).get_json()
    assert "svg" not in plain  # 响应形状对老调用方一字不变
    assert worker.renders[-1]["inline_svg"] is False

    patches = [{"gid": "text_0", "prop": "text", "value": "A"}]
    body = client.post(
        "/api/engine/render", json={"id": "p1.pdf", "patches": patches, "inline_svg": True}
    ).get_json()
    assert worker.renders[-1]["inline_svg"] is True
    assert body["svg"] == "<svg data-variant='1'/>"
    assert body["manifest"] == {"elements": []} and body["rev"] == 7


def test_inline_svg_pairs_with_the_manifest_of_the_same_call(client, tmp_path, monkeypatch):
    """两个变体交替渲染时，各自响应里的 SVG 必须是各自那次的。

    分两跳（render 之后再 GET /api/engine/svg）时这条不成立：第二跳读的是磁盘上
    那一份，另一个变体插进来就把它覆盖了——用户看到的是别人的图，而元素框
    还是自己这次的 manifest。
    """
    _open(client, tmp_path, "pairing")
    _stub_engine(monkeypatch, _FakeWorker())

    one = client.post(
        "/api/engine/render",
        json={
            "id": "p1.pdf",
            "inline_svg": True,
            "patches": [{"gid": "a", "prop": "text", "value": "1"}],
        },
    )
    two = client.post(
        "/api/engine/render",
        json={
            "id": "p1.pdf",
            "inline_svg": True,
            "patches": [
                {"gid": "a", "prop": "text", "value": "1"},
                {"gid": "b", "prop": "text", "value": "2"},
            ],
        },
    )
    assert one.get_json()["svg"] == "<svg data-variant='1'/>"
    assert two.get_json()["svg"] == "<svg data-variant='2'/>"


# --------------------- 预览表示法（ADR 0021 / issue #181） --------------------


def test_render_passes_the_preview_verdict_through(client, tmp_path, monkeypatch):
    """`preview` 必须原样透到前端。

    没有它，前端只看得到「有没有 svg」，而「没有 svg」有两种成因——老后端
    不实现 inline_svg，和引擎按硬闸主动不读。两者要走完全不同的路（前者保留
    上一版画面，后者切位图预览），猜错任何一个都是用户可见的错。
    """
    _open(client, tmp_path, "verdict")
    worker = _FakeWorker()
    worker.preview = previewbudget.metadata(
        svg_bytes=126_132_735,
        mode=previewbudget.MODE_RASTER,
        reason=previewbudget.REASON_SVG_HARD_LIMIT,
    )
    _stub_engine(monkeypatch, worker)

    body = client.post(
        "/api/engine/render", json={"id": "p1.pdf", "patches": [], "inline_svg": True}
    ).get_json()

    assert body["preview"] == worker.preview
    # **要了 inline_svg 却没有 svg 不是错误**：manifest / rev / warnings 都在
    assert "svg" not in body
    assert body["manifest"] == {"elements": []} and body["rev"] == 7
    assert body["warnings"] == []


def test_old_worker_without_preview_keeps_the_old_response_shape(client, tmp_path, monkeypatch):
    """加字段协议的另一半：worker 不返回 `preview` 时响应里一个字段都不多。"""
    _open(client, tmp_path, "legacy-shape")
    worker = _FakeWorker()  # preview 保持 None = 老 worker
    _stub_engine(monkeypatch, worker)

    body = client.post(
        "/api/engine/render", json={"id": "p1.pdf", "patches": [], "inline_svg": True}
    ).get_json()

    assert "preview" not in body
    assert body["svg"] == "<svg data-variant='0'/>"


# ------------------------- preview_png（F3） --------------------------------


def test_preview_png_is_rendered_from_the_given_patches(client, tmp_path, monkeypatch):
    """按 patches 出图（状态中立），并按 bucket 归档宽度。"""
    _open(client, tmp_path, "preview")
    png = tmp_path / "fake.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    worker = _FakeWorker(png_path=png)
    _stub_engine(monkeypatch, worker)

    patches = [{"gid": "text_0", "prop": "text", "value": "A"}]
    resp = client.post(
        "/api/engine/preview_png", json={"id": "p1.pdf", "patches": patches, "w": 500}
    )
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.headers["Cache-Control"] == "no-store"
    assert worker.previews[-1]["patches"] == patches
    assert worker.previews[-1]["width"] == 800  # 500 → 下一档


def test_preview_png_tags_are_per_variant_and_filename_safe(client, tmp_path, monkeypatch):
    """不同变体落不同文件名，同一变体永远同一个——否则并发取图会互相覆盖。

    文件名里**不能有冒号**：`patch_hash` 带 `sha256:` 前缀，直接拿来当文件名
    在 Windows 上会失败（而那台机器上没人跑这套测试）。
    """
    _open(client, tmp_path, "tags")
    png = tmp_path / "fake2.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    worker = _FakeWorker(png_path=png)
    _stub_engine(monkeypatch, worker)

    a = [{"gid": "g", "prop": "text", "value": "A"}]
    b = [{"gid": "g", "prop": "text", "value": "B"}]
    for patches in (a, b, a):
        client.post("/api/engine/preview_png", json={"id": "p1.pdf", "patches": patches, "w": 400})

    tags = [p["tag"] for p in worker.previews]
    assert tags[0] == tags[2] and tags[0] != tags[1]
    assert all(":" not in t and "/" not in t and "\\" not in t for t in tags)
    # 与权威哈希同源，不是另起一套
    assert tags[0] == "v" + patchspec.patch_hash(a).split(":")[-1][:12]


def test_preview_png_rejects_a_bogus_patch_list(client, tmp_path, monkeypatch):
    """patches 写错是调用方的错（400），不能变成一次 500。"""
    _open(client, tmp_path, "bogus")
    _stub_engine(monkeypatch, _FakeWorker())
    resp = client.post("/api/engine/preview_png", json={"id": "p1.pdf", "patches": {"gid": "g"}})
    assert resp.status_code == 400


def test_preview_png_reports_worker_errors_as_such(client, tmp_path, monkeypatch):
    """worker 报错照常带上 code（前端据此给出口，而不是甩 traceback）。"""
    _open(client, tmp_path, "err")

    class _Boom(_FakeWorker):
        def preview_png(self, stem, patches, width_px, tag):
            raise engine_pool.WorkerError("脚本报错", code="script_error")

    _stub_engine(monkeypatch, _Boom())
    resp = client.post("/api/engine/preview_png", json={"id": "p1.pdf", "patches": []})
    assert resp.status_code == 500
    assert resp.get_json()["code"] == "script_error"
