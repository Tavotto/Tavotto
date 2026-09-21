"""预览缓存的用户合同（统一实施包 U07，ADR 0066；旧后端那一面来自 `tests/test_render_cache.py` /
`test_windows_regressions.py`）。

主语是**缓存目录里的文件与「真渲染了几次」**：键含内容身份（不是 mtime）、后端 build、字体政策版本、像素 / 颜色参数；
同键并发只渲染一次；零字节重建；临时发布 + Windows 退让；异常抛出而不是空白图 / 旧图。

用一个**假 host**（进程内、纯标准库）驱动，任何机器都能跑；真 child 的一条在 rc-venv 里跑。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto.rendercore import preview, raster

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"


class FakeHost:
    """假 render child：`render()` 回一张 width×1 的纯色 RGBA；记调用次数；可注入失败 / 延迟；pdfium 版本可改。"""

    def __init__(self) -> None:
        self.renders = 0
        self.pdfium = "153.0.7999.0"
        self.fail: Exception | None = None
        self.delay = 0.0
        self.lock = threading.Lock()
        self.on_render = None  # 渲染那一刻的钩子（模拟「文件在算键与渲染之间被换掉」）

    def ping(self) -> dict:
        return {"ok": True, "pdfium": self.pdfium}

    def render(self, pdf, *, width_px, page=0, transparent=False, **_):
        with self.lock:
            self.renders += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        if self.on_render is not None:
            self.on_render(pdf)
        color = (
            (0, 0, 0, 0) if transparent else (10, 20, 30 + int(page), 255)
        )  # 页号进颜色：哪一页看得出来
        return raster.RasterBuffer(width_px, 1, 4, bytes(color) * width_px, width_px * 4)


@pytest.fixture
def cache(tmp_path):
    host = FakeHost()
    c = preview.PreviewCache(tmp_path / "cache", host, max_bytes=10_000_000)
    return c, host


def _pdf(tmp_path, name="a.pdf", body=b"%PDF-1.4 a") -> Path:
    p = tmp_path / name
    p.write_bytes(body)
    return p


def test_a_hit_is_served_without_rendering_and_the_file_is_a_png_of_the_requested_width(
    cache, tmp_path
):
    c, host = cache
    src = _pdf(tmp_path)
    a = c.get("figs/a.pdf", src, 400)
    assert a.suffix == ".png" and a.parent == tmp_path / "cache"
    w, h, rgba = pdfread.decode_png(a.read_bytes())
    assert (w, h) == (400, 1) and rgba[:4] == bytes((10, 20, 30, 255))
    b = c.get("figs/a.pdf", src, 400)
    assert b == a and host.renders == 1


def test_touching_the_file_keeps_the_key_but_changing_its_bytes_changes_it(cache, tmp_path):
    """RC-061 must_fail：mtime 当唯一 cache key。"""
    c, host = cache
    src = _pdf(tmp_path)
    a = c.get("figs/a.pdf", src, 400)
    os.utime(src, (time.time() + 100, time.time() + 100))
    assert c.get("figs/a.pdf", src, 400) == a and host.renders == 1
    src.write_bytes(b"%PDF-1.4 b")
    b = c.get("figs/a.pdf", src, 400)
    assert b != a and host.renders == 2


def test_the_key_carries_width_background_renderer_build_and_fonts_policy(cache, tmp_path):
    c, host = cache
    src = _pdf(tmp_path)
    base = c.get("figs/a.pdf", src, 400)
    assert c.get("figs/a.pdf", src, 800) != base
    assert c.get("figs/a.pdf", src, 400, transparent=True) != base
    assert c.get("figs/b.pdf", src, 400) != base  # 面板 id 也是身份的一维
    assert host.renders == 4
    # 换了栅格器 build：旧预览不许再命中
    host.pdfium = "154.0.0.0"
    c._renderer_version = None
    assert c.get("figs/a.pdf", src, 400) != base and host.renders == 5
    # 换了字体政策：同上（allowlist 的 sha256 进键）
    c._fonts_version = "deadbeefdeadbeef"
    assert c.get("figs/a.pdf", src, 400) not in (base,) and host.renders == 6
    # 键函数本身：六个维度任一变，键必变
    k = preview.cache_key(
        "a", "x" * 64, 400, transparent=False, renderer_version="1", fonts_version="f"
    )
    for kw in (
        dict(source_id="b"),
        dict(content_sha256="y" * 64),
        dict(width_px=401),
        dict(transparent=True),
        dict(renderer_version="2"),
        dict(fonts_version="g"),
    ):
        args = dict(
            source_id="a",
            content_sha256="x" * 64,
            width_px=400,
            transparent=False,
            renderer_version="1",
            fonts_version="f",
        )
        args.update(kw)
        assert (
            preview.cache_key(
                args.pop("source_id"), args.pop("content_sha256"), args.pop("width_px"), **args
            )
            != k
        ), kw


def test_different_pages_of_one_source_are_different_previews(cache, tmp_path):
    """Codex #471 P2：多页源的第 1 页不许拿第 0 页的缓存冒充——页号进键。"""
    c, host = cache
    src = _pdf(tmp_path)
    p0 = c.get("figs/a.pdf", src, 400, page=0)
    p1 = c.get("figs/a.pdf", src, 400, page=1)
    assert p0 != p1 and host.renders == 2
    _, _, rgba0 = pdfread.decode_png(p0.read_bytes())
    _, _, rgba1 = pdfread.decode_png(p1.read_bytes())
    assert rgba0[:4] == bytes((10, 20, 30, 255)) and rgba1[:4] == bytes((10, 20, 31, 255))
    assert c.get("figs/a.pdf", src, 400, page=1) == p1 and host.renders == 2


def test_the_child_renders_the_hashed_bytes_even_if_the_source_is_swapped_and_restored(
    cache, tmp_path
):
    """Codex #471 第三轮 P2：算键之后、child 打开文件之前源被换成 B 再换回 A——「渲染后再核一次 hash」看到的是 A，
    挡不住（第二轮的修法）。现在 child 渲染的是抄出来的不可变副本：它打开的路径不是源文件，源在渲染期间
    换成什么、换回来没有，它看到的字节都是键里 hash 过的那份；副本用完即删；下一次源真的变了 → 新键、再渲。"""
    c, host = cache
    src = _pdf(tmp_path, body=b"%PDF-1.4 A")
    seen: dict[str, object] = {}

    def swap_and_restore(pdf):
        seen["path"] = Path(pdf)
        seen["before"] = Path(pdf).read_bytes()
        src.write_bytes(b"%PDF-1.4 B")  # A → B
        seen["during"] = Path(pdf).read_bytes()
        src.write_bytes(b"%PDF-1.4 A")  # B → A：渲染后复核 hash 会说「没变」

    host.on_render = swap_and_restore
    p = c.get("figs/a.pdf", src, 400)
    assert seen["path"] != src and seen["path"].parent == tmp_path / "cache"
    assert seen["before"] == seen["during"] == b"%PDF-1.4 A"
    assert p.exists() and host.renders == 1
    assert not list((tmp_path / "cache").glob("*.src.part"))  # 副本用完即删
    host.on_render = None
    src.write_bytes(b"%PDF-1.4 B")
    p2 = c.get("figs/a.pdf", src, 400)  # 现在磁盘上是 B：按它的 hash 建键、渲染、发布
    assert p2 != p and p2.exists() and host.renders == 2


def test_source_identity_hashes_in_chunks_without_read_bytes(cache, tmp_path, monkeypatch):
    """Codex #471 P2：几百 MB 的源不该为了算身份整个读进内存——`Path.read_bytes` 一次都不许叫。"""
    import pathlib

    c, host = cache
    body = b"%PDF-1.4 " + b"z" * (3 * preview._HASH_CHUNK + 17)
    src = _pdf(tmp_path, body=body)
    expected = hashlib.sha256(body).hexdigest()

    def boom(self):
        raise AssertionError("不该整个读进内存")

    monkeypatch.setattr(pathlib.Path, "read_bytes", boom)
    assert c.source_identity(src) == expected


def test_fonts_policy_version_is_the_allowlist_hash_prefix():
    from tavotto.rendercore.fonts import allowlist_path

    assert (
        preview.fonts_policy_version()
        == hashlib.sha256(allowlist_path().read_bytes()).hexdigest()[:16]
    )


def test_a_handed_out_but_not_yet_acquired_lock_survives_table_eviction(cache, tmp_path):
    """Codex #471 第四轮 P2：表满时按 `lock.locked()` 淘汰——线程 A 拿到锁对象、还没 acquire 就被调度走，那一刻它
    是 unlocked 的，会被淘汰；第三个同键请求另建一把，A 与它同时渲染同一张预览。现在淘汰看**登记使用者数**：
    A 在 `_locks_guard` 里已登记为 1 个使用者，表满淘汰 512 个别的键也轮不到它，C 拿到的仍是 A 那把。"""
    c, _host = cache
    key_a = str(tmp_path / "cache" / "a.png")
    entry_a = c._reserve(key_a)  # A：拿到手，还没 acquire（模拟被调度走）
    assert entry_a.users == 1 and not entry_a.lock.locked()
    for i in range(preview._LOCKS_MAX + 5):  # 别的键把表灌满、触发淘汰
        k = str(tmp_path / "cache" / f"other-{i}.png")
        c._release(k, c._reserve(k))
    assert c._locks[key_a] is entry_a, "登记在案的锁被淘汰了"
    entry_c = c._reserve(key_a)  # C：同键，必须拿到同一把
    assert entry_c is entry_a and entry_a.users == 2
    c._release(key_a, entry_c)
    c._release(key_a, entry_a)
    assert entry_a.users == 0
    # 没人用了才可淘汰：再灌一轮，它才消失
    for i in range(preview._LOCKS_MAX + 5):
        k = str(tmp_path / "cache" / f"later-{i}.png")
        c._release(k, c._reserve(k))
    assert key_a not in c._locks


def test_a_half_written_staging_copy_is_removed_when_copying_fails(cache, tmp_path, monkeypatch):
    """Codex #471 第四轮 P2：抄源到一半失败（源读不了 / 缓存卷满）——`_stage()` 在 `get()` 的 try/finally 之前
    抛出，半截 `.src.part` 就永远留在缓存目录里（`prune()` 只扫 *.png，名字还按 pid/线程各不相同）。现在 `_stage()`
    自己在任何异常下删掉副本再抛。"""
    c, host = cache
    body = b"%PDF-1.4 " + b"z" * (3 * preview._HASH_CHUNK + 17)
    src = _pdf(tmp_path, body=body)
    calls = {"n": 0}
    real_sha256 = hashlib.sha256

    class Boom:
        def __init__(self):
            self.inner = real_sha256()

        def update(self, chunk):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(28, "No space left on device")  # 第二块：副本已经写了一块
            self.inner.update(chunk)

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr(preview.hashlib, "sha256", Boom)
    with pytest.raises(OSError):
        c.get("figs/a.pdf", src, 400)
    assert not list((tmp_path / "cache").glob("*.src.part")), list((tmp_path / "cache").iterdir())
    assert host.renders == 0


def test_concurrent_requests_for_the_same_key_render_exactly_once(cache, tmp_path):
    c, host = cache
    host.delay = 0.2
    src = _pdf(tmp_path)
    results: list[Path] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work() -> None:
        try:
            p = c.get("figs/a.pdf", src, 400)
            with lock:
                results.append(p)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(results)) == 1 and host.renders == 1


def test_a_zero_byte_cache_file_is_rebuilt_not_served(cache, tmp_path):
    c, host = cache
    src = _pdf(tmp_path)
    a = c.get("figs/a.pdf", src, 400)
    a.write_bytes(b"")
    b = c.get("figs/a.pdf", src, 400)
    assert b == a and b.stat().st_size > 0 and host.renders == 2


def test_a_render_failure_raises_and_leaves_no_blank_or_stale_file(cache, tmp_path):
    """must_fail：异常返回空白图或旧图作成功。第一次失败 → 抛且目录里没有这个键；旧内容的缓存文件不被当成新内容交出。"""
    c, host = cache
    src = _pdf(tmp_path)
    old = c.get("figs/a.pdf", src, 400)
    src.write_bytes(b"%PDF-1.4 changed")
    from tavotto.rendercore.renderhost import RenderChildError

    host.fail = RenderChildError("render_child_timeout", "slow")
    with pytest.raises(preview.PreviewError) as ei:
        c.get("figs/a.pdf", src, 400)
    assert ei.value.code == "render_child_timeout"
    new_key = c.path_for(c.key_for("figs/a.pdf", src, 400, transparent=False))
    assert not new_key.exists() and not list((tmp_path / "cache").glob("*.part.png"))
    assert old.exists()  # 旧键的文件还在，但没有被冒充成新内容
    host.fail = None
    assert c.get("figs/a.pdf", src, 400) == new_key and new_key.stat().st_size > 0


def test_publish_yields_to_a_reader_holding_the_target_on_windows(cache, tmp_path, monkeypatch):
    """Windows 上 `os.replace` 盖不掉正被读的目标：目标已在且非空 → 退让（同键同字节）；目标不存在 → 重试后如实抛。"""
    c, host = cache
    src = _pdf(tmp_path)
    a = c.get("figs/a.pdf", src, 400)
    real_replace = os.replace
    calls = {"n": 0}

    def locked_replace(s, d):
        calls["n"] += 1
        raise PermissionError(5, "被读者拿着")

    monkeypatch.setattr(preview.os, "replace", locked_replace)
    a.write_bytes(b"")  # 零字节 → 重建 → replace 撞锁 → 目标零字节 → 重试 → 仍不行 → 抛出
    with pytest.raises(PermissionError):
        c.get("figs/a.pdf", src, 400)
    assert calls["n"] == preview._REPLACE_TRIES
    monkeypatch.setattr(preview.os, "replace", real_replace)
    a = c.get("figs/a.pdf", src, 400)
    assert a.stat().st_size > 0
    # 目标已在且非空：退让不抛
    calls["n"] = 0
    monkeypatch.setattr(preview.os, "replace", locked_replace)
    tmp = a.with_name("x.part.png")
    tmp.write_bytes(b"x")
    preview.PreviewCache._publish(tmp, a)
    assert calls["n"] == 1 and a.stat().st_size > 1


def test_prune_deletes_oldest_first_down_to_the_budget(cache, tmp_path):
    c, host = cache
    src = _pdf(tmp_path)
    paths = [c.get("figs/a.pdf", src, w) for w in (100, 200, 300)]
    for i, p in enumerate(paths):
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    c.max_bytes = paths[2].stat().st_size + paths[1].stat().st_size
    assert c.prune() == 1
    assert not paths[0].exists() and paths[1].exists() and paths[2].exists()


def test_prune_leaves_in_flight_part_files_and_the_just_published_one_alone(cache, tmp_path):
    """Codex #471 第五轮 P2：`prune()` 的 glob 会把别人正在写的 `<key>.<pid>-<tid>.part.png` 当缓存删掉——那个
    请求随后在 `os.replace` 上 FileNotFoundError；预算够小时连刚发布、马上要交出去的那张也删。现在只认成品名
    `<sha1>.png`，且 `keep=` 那张预算再小也留着。"""
    c, host = cache
    src = _pdf(tmp_path)
    published = c.get("figs/a.pdf", src, 100)
    os.utime(published, (1_000_000, 1_000_000))
    cache_dir = tmp_path / "cache"
    part = cache_dir / f"{published.stem}.{os.getpid()}-abc.part.png"  # 别人在飞的临时文件，最旧
    part.write_bytes(b"x" * 4096)
    os.utime(part, (900_000, 900_000))
    staged = cache_dir / "stage.1-2.src.part"
    staged.write_bytes(b"y" * 4096)
    os.utime(staged, (900_000, 900_000))
    c.max_bytes = 1  # 预算小到什么成品都留不下
    assert c.prune(keep=published) == 0
    assert published.exists() and part.exists() and staged.exists()
    assert c.prune() == 1  # 不带 keep：成品可删，在飞的仍不碰
    assert not published.exists() and part.exists() and staged.exists()
    # 端到端：预算 1 字节时 get() 交出来的路径必须存在
    host.renders = 0
    p = c.get("figs/a.pdf", src, 200)
    assert p.exists() and host.renders == 1


needs = pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None or importlib.util.find_spec("pikepdf") is None,
    reason="候选包未装（not_run）",
)


@needs
def test_a_real_pdf_preview_is_rendered_by_the_child_at_the_bucket_width(tmp_path):
    from tavotto.rendercore import renderhost

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    host = renderhost.RenderHost(env=env, default_timeout=60, scratch_dir=tmp_path)
    try:
        c = preview.PreviewCache(tmp_path / "cache", host)
        p = c.get("figs/page.pdf", FIXTURE / "page.pdf", 800)
        w, h, bpp, rgb = pdfread.decode_png_any(p.read_bytes())
        assert (w, h, bpp) == (800, round(160 * 800 / 270), 3)  # 白底预览是 RGB
        # 源蓝矩形 (40..140, 40..100) → CropBox 空间 (25..125, 30..90) → 像素 x=60·800/270, y=h−60·(800/270)
        s = 800 / 270
        x, y = int(60 * s), int(h - 60 * s)
        px = rgb[(y * w + x) * 3 : (y * w + x) * 3 + 3]
        assert tuple(px) == (0, 0, 255), tuple(px)
        assert c.get("figs/page.pdf", FIXTURE / "page.pdf", 800) == p and c.renders == 1
    finally:
        host.close()
