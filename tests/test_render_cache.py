"""`/api/render` 的磁盘缓存：身份是内容哈希、写入原子、坏文件自愈（用户合同）。

这三条都不是「优化」，是正确性：

* 曾经缓存键里放的是 `mtime`——它回答的是「什么时候被碰过」，不是「里面是
  什么」。内容没变而 mtime 变了（touch、从备份还原、同步工具、重跑脚本出了
  同一张图）会白丢一张 3200px 的预览；反过来换了渲染后端版本、同一个 PDF
  渲出来的像素已经不一样了，却照旧命中。
* 渲染曾经直写最终路径。同一张图被两个面板/两个标签页同时请求时，后到的
  `send_file` 出去的可能是只写了一半的 PNG。
* 零字节文件（上一次写到一半就被杀）必须当场重建，不能当缓存交出去。

U10（ADR 0072）起这条路只剩 `rendercore.preview.PreviewCache` 一份实现，这里经**真实端点**验用户合同；
键的构成 / 同键去重 / Windows 退让 / 抄字节再 hash 那些实现细节在 `tests/test_rendercore_preview.py`。
旧路 `source_sha1` 的 (mtime, size) memo 及其三条用例随旧路删除——PreviewCache 的身份从它自己抄出来的
那份字节上算，「同 tick 同尺寸改写」的窗口结构上不存在（`test_the_child_renders_the_hashed_bytes_even_if_the_source_is_swapped_and_restored`）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import artifactbytes  # noqa: E402
import pdfread  # noqa: E402

HAS = all(
    importlib.util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tavotto.rendercore import facade, renderhost

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "cache")
    facade.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    facade.reset_for_tests()
    renderhost.shutdown_shared()


def _figs(tmp_path, *, text: str = "A") -> Path:
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    _write_pdf(figs / "p1.pdf", text)
    return figs


def _write_pdf(path: Path, text: str) -> None:
    """一页 100 × 50 pt 的合法 PDF；`text` 进内容流，所以不同的 text = 不同的字节。"""
    data = artifactbytes.blank_pdf(100, 50)
    path.write_bytes(data.replace(b"endstream", text.encode("ascii") + b"\nendstream", 1))


def _cached_files(tmp_path):
    """落成的缓存文件（写到一半的 `.part.png` 不算）。"""
    return sorted(
        p.name for p in (tmp_path / "cache").glob("*.png") if not p.name.endswith(".part.png")
    )


def _png_width(data: bytes) -> int:
    w, _h, _bpp, _px = pdfread.decode_png_any(data)
    return w


def test_mtime_change_without_content_change_still_hits(client, tmp_path):
    """碰过（touch）但内容没变 → 键不变，缓存照常命中。

    这正是与旧行为可观察的差异：旧的 mtime 键在这里会重渲染一张新缓存。
    """
    figs = _figs(tmp_path)
    m.open_project(str(figs))
    src = figs / "p1.pdf"

    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    first = _cached_files(tmp_path)
    assert len(first) == 1

    st = src.stat()
    os.utime(src, (st.st_atime + 1000, st.st_mtime + 1000))

    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    assert _cached_files(tmp_path) == first, "内容没变就不该多出一个缓存文件"


def test_content_change_invalidates(client, tmp_path):
    figs = _figs(tmp_path)
    m.open_project(str(figs))

    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    before = _cached_files(tmp_path)

    _write_pdf(figs / "p1.pdf", "B")  # 内容真的变了
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    after = _cached_files(tmp_path)
    assert len(after) == 2 and before[0] in after, "内容变了必须换一个键"


def test_concurrent_requests_never_serve_a_torn_png_and_render_once(client, tmp_path, monkeypatch):
    """同键并发：每个响应都必须是一个完整的 PNG，而且只真渲染一次。

    把渲染故意拖慢并让 16 个线程同时打同一个键：读到的字节必须都是完整图片（解得开、宽度就是桶宽），
    渲染计数为 1（其余线程复用成品——各渲一遍出来的字节完全相同，多出来的 15 次纯属白烧 CPU，
    在 Windows 上多个写者还会互相撞 `os.replace`）。
    """
    from tavotto.rendercore import preview as rc_preview

    figs = _figs(tmp_path)
    m.open_project(str(figs))

    real = rc_preview.PreviewCache._write
    calls: list[int] = []
    counter_lock = threading.Lock()

    def slow(self, *args, **kwargs):
        with counter_lock:
            calls.append(1)
        time.sleep(0.05)  # 给别的线程足够时间挤进来
        return real(self, *args, **kwargs)

    monkeypatch.setattr(rc_preview.PreviewCache, "_write", slow)

    results: list = []
    errors: list = []

    def hit():
        try:
            r = client.get("/api/render?id=p1.pdf&w=400")
            results.append((r.status_code, r.get_data()))
        except Exception as exc:  # noqa: BLE001 — 线程里的异常要带回来
            errors.append(exc)

    threads = [threading.Thread(target=hit) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert not errors, errors
    assert len(results) == 16
    for status, data in results:
        assert status == 200
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), "读到了半个文件"
        assert _png_width(data) == 400  # 真解一次：截断的 PNG 头部一样完好，只有解码才认得出来
    assert len(calls) == 1, f"同键渲染了 {len(calls)} 次"
    # 临时文件一个都不许留下
    assert not list((tmp_path / "cache").glob("*.part.png"))


def test_zero_byte_cache_heals_itself(client, tmp_path):
    """零字节缓存（写到一半被杀）当场重建，绝不交给浏览器。"""
    figs = _figs(tmp_path)
    m.open_project(str(figs))
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    cached = (tmp_path / "cache") / _cached_files(tmp_path)[0]

    cached.write_bytes(b"")
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 200
    assert len(resp.get_data()) > 0
    assert cached.stat().st_size > 0
    assert _cached_files(tmp_path) == [cached.name], "自愈应写回同一个键"


def test_the_backend_build_is_part_of_the_key(client, tmp_path, monkeypatch):
    """换渲染 build（同一个 PDF 渲出来的像素可能已经不一样）= 换缓存键。主语是键里那一维的来源
    `preview.BACKEND_VERSION`（与 `rendercore.BACKEND_VERSION` 同一份）；切默认（U10）就是一次这样的
    变化，旧后端留下的缓存自然不命中。"""
    from tavotto.rendercore import facade, preview as rc_preview

    figs = _figs(tmp_path)
    m.open_project(str(figs))
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    before = _cached_files(tmp_path)

    monkeypatch.setattr(rc_preview, "BACKEND_VERSION", "99.9.9")
    facade.reset_for_tests()  # 换 build = 新进程 / 新实例
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    after = _cached_files(tmp_path)
    assert len(after) == 2 and before[0] in after
