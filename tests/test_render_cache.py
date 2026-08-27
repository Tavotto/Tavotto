"""`/api/render` 的磁盘缓存：身份是内容哈希、写入原子、坏文件自愈。

这三条都不是「优化」，是正确性：

* 曾经缓存键里放的是 `mtime`——它回答的是「什么时候被碰过」，不是「里面是
  什么」。内容没变而 mtime 变了（touch、从备份还原、同步工具、重跑脚本出了
  同一张图）会白丢一张 3200px 的预览；反过来换了渲染后端版本、同一个 PDF
  渲出来的像素已经不一样了，却照旧命中。
* `render_preview_png` 曾经直写最终路径。同一张图被两个面板/两个标签页同时
  请求时，后到的 `send_file` 出去的可能是只写了一半的 PNG。
* 零字节文件（上一次写到一半就被杀）必须当场重建，不能当缓存交出去。
"""
import hashlib
import os
import threading
import time

import pymupdf
import pytest

from tavotto import app as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "cache")
    m._SOURCE_SHA1.clear()
    yield m.app.test_client()
    m.reset_projects()
    m._SOURCE_SHA1.clear()


def _figs(tmp_path, *, text: str = "A") -> "tuple":
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    _write_pdf(figs / "p1.pdf", text)
    return figs


def _write_pdf(path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=50)
    page.insert_text((10, 30), text, fontsize=20)
    doc.save(path)
    doc.close()


def _settle(path, *, age: float) -> None:
    """把 mtime 推回 `age` 秒前，让文件越过 `source_sha1` 的同 tick 窗口。

    memo 只对「安定下来」的文件生效（见 `source_sha1` 的 docstring）。想测
    memo 命中就必须显式把时间推过去，而不是赌两次写入之间机器跑得够慢——
    那正是本文件原先那条隐含时序假设。
    """
    t = time.time() - age
    os.utime(path, (t, t))


def _cached_files(tmp_path):
    """落成的缓存文件（写到一半的 `.part.png` 不算）。"""
    return sorted(p.name for p in (tmp_path / "cache").glob("*.png")
                  if not p.name.endswith(".part.png"))


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
    import os
    os.utime(src, (st.st_atime + 1000, st.st_mtime + 1000))
    m._SOURCE_SHA1.clear()      # 模拟进程重启：memo 里没有旧值可赖

    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    assert _cached_files(tmp_path) == first, "内容没变就不该多出一个缓存文件"


def test_content_change_invalidates(client, tmp_path):
    figs = _figs(tmp_path)
    m.open_project(str(figs))

    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    before = _cached_files(tmp_path)

    _write_pdf(figs / "p1.pdf", "B")        # 内容真的变了
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    after = _cached_files(tmp_path)
    assert len(after) == 2 and before[0] in after, "内容变了必须换一个键"


def test_backend_version_is_part_of_the_key(client, tmp_path, monkeypatch):
    """换渲染后端版本 = 换缓存键（同一个 PDF 渲出来的像素可能已经不一样）。"""
    figs = _figs(tmp_path)
    m.open_project(str(figs))
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    before = _cached_files(tmp_path)

    monkeypatch.setattr(m.pdfbackend, "BACKEND_VERSION", "99.9.9")
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    assert len(_cached_files(tmp_path)) == 2 and before[0] in _cached_files(tmp_path)


def test_source_sha1_memo_avoids_rehashing(client, tmp_path, monkeypatch):
    """安定下来的文件：memo 命中就不再读文件；mtime 一变立刻重算。"""
    figs = _figs(tmp_path)
    src = figs / "p1.pdf"
    _settle(src, age=60)
    calls = []
    real = m._sha1_of
    monkeypatch.setattr(m, "_sha1_of", lambda p: (calls.append(p), real(p))[1])

    a = m.source_sha1(src)
    b = m.source_sha1(src)
    assert a == b and len(calls) == 1, "安定文件的第二次调用应当走 memo"

    _write_pdf(src, "B")
    _settle(src, age=30)        # 显式推进 mtime：真正走到 memo 失效那条路
    c = m.source_sha1(src)
    assert len(calls) == 2 and c != a


def test_same_tick_same_size_rewrite_never_returns_the_old_digest(client, tmp_path):
    """同一个 mtime tick 内改写成同样大小 → 必须拿到新内容的摘要（#143）。

    这是渲染缓存身份的底线：(mtime, size) 在这里一个比特都不变，靠它当身份
    的 memo 会交出上一版的摘要，表现为「文件变了、画布还是旧图」。用
    `os.utime` 把第二次写的 mtime 钉回第一次，等价于粗粒度文件系统上
    两次写入落进同一跳——Windows CI 上撞到的就是它。
    """
    src = tmp_path / "same-tick.bin"
    src.write_bytes(b"A" * 780)
    st = src.stat()
    stamp = (st.st_atime_ns, st.st_mtime_ns)

    assert m.source_sha1(src) == hashlib.sha1(b"A" * 780).hexdigest()

    src.write_bytes(b"B" * 780)             # 同尺寸改写
    os.utime(src, ns=stamp)                 # 同 tick：签名一个比特没动
    now = src.stat()
    assert (now.st_mtime_ns, now.st_size) == (stamp[1], st.st_size), \
        "现场没构造出来：mtime 或 size 变了，本用例就不再是它自称的那个"

    assert m.source_sha1(src) == hashlib.sha1(b"B" * 780).hexdigest(), \
        "同 tick 同尺寸改写拿到了旧摘要——渲染缓存会挂着上一版的图"


def test_a_young_signature_evicts_the_stale_memo_entry(client, tmp_path):
    """同 tick 窗口里的那次调用**不留 memo，也不许把旧条目留在表里**。

    Codex 在 PR #152 上指出的休眠形状：旧条目的签名此刻对不上，所以不会被
    命中——但「对不上」只是此刻。备份还原 / 同步工具把那个 mtime 连同另一份
    同尺寸内容一起写回来，它就又匹配了，交出来的是**更早那一版**的摘要，
    `/api/render` 会一直挂着一张过期的 PNG。
    """
    src = tmp_path / "evict.bin"
    src.write_bytes(b"A" * 640)
    _settle(src, age=60)
    old_stamp = src.stat().st_mtime_ns
    first = m.source_sha1(src)                    # 安定文件：这条进了 memo
    assert m._SOURCE_SHA1.get(str(src), (0, 0, ""))[2] == first

    src.write_bytes(b"B" * 640)                   # 同尺寸，mtime 是「刚刚」
    assert m.source_sha1(src) == hashlib.sha1(b"B" * 640).hexdigest()

    os.utime(src, ns=(old_stamp, old_stamp))      # 把当初那个 mtime 还原回来
    assert m.source_sha1(src) == hashlib.sha1(b"B" * 640).hexdigest(), \
        "旧 memo 条目复活了：mtime 被还原之后它又匹配上，交出了上上版的摘要"


def test_concurrent_requests_never_serve_a_torn_png(client, tmp_path, monkeypatch):
    """同键并发：每个响应都必须是一个完整的 PNG。

    旧的直写路径下，后到的请求会 `send_file` 一个正在被写的文件。这里把
    渲染故意拖慢并让 16 个线程同时打同一个键，读到的字节必须是完整图片。
    """
    figs = _figs(tmp_path)
    m.open_project(str(figs))

    real = m.pdfbackend.render_preview_png

    def partial_then_complete(path, w, out):
        """模拟「正在写」：先落一段不完整的字节，喘口气，再补全。

        真实的 `pix.save()` 就是这样把文件逐渐写出来的。走原子路径时 `out`
        是本线程私有的临时文件，别人根本看不见；直写最终路径时，别的线程
        会看到一个**非零但残缺**的文件并当成缓存交出去。
        """
        import time
        real(path, w, out)
        data = out.read_bytes()
        out.write_bytes(data[: len(data) // 3])
        time.sleep(0.05)
        out.write_bytes(data)

    monkeypatch.setattr(m.pdfbackend, "render_preview_png", partial_then_complete)

    results: list = []
    errors: list = []

    def hit():
        try:
            r = client.get("/api/render?id=p1.pdf&w=400")
            results.append((r.status_code, r.get_data()))
        except Exception as exc:            # noqa: BLE001 — 线程里的异常要带回来
            errors.append(exc)

    threads = [threading.Thread(target=hit) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert not errors, errors
    assert len(results) == 16
    for status, data in results:
        assert status == 200
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), "读到了半个文件"
        # 真解一次：截断的 PNG 头部一样完好，只有解码才认得出来
        pix = pymupdf.Pixmap(data)
        assert pix.width == 400
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


def test_same_key_renders_once_under_concurrency(client, tmp_path, monkeypatch):
    """同键并发只渲染一次，其余线程复用成品。

    16 个线程同时要同一张图时，每个各渲一遍出来的字节完全相同（键含内容
    哈希），多出来的 15 次纯属白烧 CPU；在 Windows 上多个写者还会互相撞
    `os.replace`。写者按键串行 + 锁内复查即可，读路径一点不受影响。
    """
    import time

    figs = _figs(tmp_path)
    m.open_project(str(figs))

    real = m.pdfbackend.render_preview_png
    calls: list = []
    counter_lock = threading.Lock()

    def counted(path, w, out):
        with counter_lock:
            calls.append(w)
        time.sleep(0.05)          # 给别的线程足够时间挤进来
        real(path, w, out)

    monkeypatch.setattr(m.pdfbackend, "render_preview_png", counted)

    results: list = []
    errors: list = []

    def hit():
        try:
            r = client.get("/api/render?id=p1.pdf&w=400")
            results.append((r.status_code, r.get_data()))
        except Exception as exc:            # noqa: BLE001 — 线程里的异常要带回来
            errors.append(exc)

    threads = [threading.Thread(target=hit) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert not errors, errors
    assert len(calls) == 1, f"同键渲染了 {len(calls)} 次，应当只渲一次"
    assert len(results) == 16
    for status, data in results:
        assert status == 200
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
