"""预览缓存：面板源（PDF）→ 指定像素宽度的 PNG，带磁盘缓存（统一实施包 U07，ADR 0066）。

旧后端那一面的用户合同（`app.py` 的 `/api/render` + `_write_render_cache` + `_publish_render_cache`，
`docs/rules/backend/pdf-backend-boundary.md`「`/api/render` 的磁盘缓存键」）逐条搬到这里，栅格由 render child
出（`renderhost.RenderHost.render(width_px=…)`），编码是本包的 PNG 编码器（`raster.encode_png`）。
U08 起 `app.py` 的 `/api/render` 在候选后端被选中时走本模块（`rendercore.facade.preview_cache()`，ADR 0067）：
`app.py` 那三段（键 / 写 / 发布）在候选路上由这里替掉；`source_sha1` 的 (mtime, size) memo **不收编**——这里的键
与渲染绑在同一份抄出来的字节上（见下）；`render_queue_full` 由端点翻成 503。默认后端那条路一字不变。
ADR 0077 加了**命中快路**：文件指纹与上次抄副本时相同且成品在，就用那次副本上算出的 sha256 直接交出成品（不抄、不读源、从不渲染）。

## 键（RC-061：内容身份，不是 mtime）

`sha1(source_id | 内容 sha256 | 页号 | 宽度 | 背景 | rendercore 名-版本 | PDFium 版本 | 字体政策版本)`：

* **内容 sha256** 而不是 mtime：touch / 从备份还原 / 同步工具改了 mtime 而内容没变 → 键不变、缓存照常命中；
  内容变了 → 键必变；
* hash 与渲染**绑在同一份字节上**：源先一次读成缓存目录里的不可变副本（边抄边算 hash），child 渲染的是副本——
  算键与渲染之间源被换掉（哪怕又换回去）都影响不到它，「渲染后再核一次」挡不住的 A→B→A 也挡住了；
* **后端 build**（`rendercore.BACKEND_VERSION` + child 报的 PDFium 版本）与**字体政策版本**（allowlist 的 sha256）
  进键：换了栅格器 / 换了批准字体集合，像素可能已经不同，旧预览不许再命中——`must_fail_example`「mtime 当唯一
  cache key」与「改 backend / font 但内容路径不变」都在这里挡住；
* **像素 / 颜色参数**（宽度、透明 / 白底）与**页号**进键（多页源的每一页各是一张预览）。

## 写 / 发布（与旧 `_write_render_cache` / `_publish_render_cache` 同一条纪律）

* 同键并发只让一个线程真渲染（每键一把锁，锁表封顶、只丢**登记使用者为零**的——拿到手还没 acquire 的也算在用，
  `lock.locked()` 看不见那一刻）；锁内复查一次，看到成品直接用；
* **同一个源的副本路串行**（每个源一把 staging 锁，与每键锁同一张表，#489 第 4 条）：副本要先抄完才算得出键，
  每键锁管不到抄副本那一步——一份几百 MB 的 PDF 同时来 N 个请求，不串行就先在缓存卷上躺下 N 份副本。拿到
  staging 锁先再走一次指纹快路：排在前面的那个抄完、渲完、记下了身份，后面的直接交出成品、一个字节都不抄；
  指纹不可信（刚写出的源 / 平台开关关着）时后面的仍各抄一次，但**同一时刻只有一份**。锁序固定为 staging → 每键；
* 渲染进临时文件（**后缀仍是 .png**，同一目录）再 `os.replace`：读者要么看到旧的（完整）要么看到新的（完整）；
* **Windows 上 `os.replace` 盖不掉正被读的目标**（`PermissionError`）：目标已在且非空 → **退让**（同键字节
  必然相同）；目标不存在 / 零字节才重试，重试完仍不行如实抛出；
* 零字节缓存当场删掉重建；
* **异常一律抛出**——不返回空白图、不拿旧文件冒充这次成功（`must_fail`：异常返回空白图或旧图作成功）；
* `prune()` 按 mtime 从旧到新删**成品**至预算内（与旧 `prune_render_cache` 同形）：只认 `<sha1>.png`，别人在飞的
  `.part.png` / `stage.*.src.part` 不碰；任何线程的 `get()` 正要交出去的那张（从算出键到 return 都钉着）谁的 prune 都不删，
  pruner 之间串行。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from . import BACKEND_NAME, BACKEND_VERSION
from .identity import fonts_policy_version as _fonts_policy_version
from .raster import SOURCE_MAX_PIXELS, encode_png
from .rasterio import RasterDecodeError, kind_of, preview as raster_preview
from .renderhost import RenderChildError, RenderHost
from .sources import FingerprintMemo

__all__ = ["PreviewCache", "PreviewError", "cache_key", "fonts_policy_version"]

#: 换名撞上 Windows 独占读句柄时的重试次数与间隔（总计 ~0.2s 的退让窗口）。
_REPLACE_TRIES = 5
_REPLACE_BACKOFF_S = 0.05
_LOCKS_MAX = 512
_HASH_CHUNK = 1 << 20


class PreviewError(RuntimeError):
    """渲染 / 落盘失败。调用方拿到的是异常，不是一张空白图。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: 字体政策版本：与 `identity.render_identity` 同一份（U09）——缓存键与 render fingerprint 认的是同一个数
fonts_policy_version = _fonts_policy_version


def cache_key(
    source_id: str,
    content_sha256: str,
    width_px: int,
    *,
    transparent: bool,
    renderer_version: str,
    fonts_version: str,
    page: int = 0,
) -> str:
    parts = (
        source_id,
        content_sha256,
        f"page{int(page)}",  # 多页源：不同页是不同的预览（Codex #471 P2）
        str(int(width_px)),
        "transparent" if transparent else "white",
        f"{BACKEND_NAME}-{BACKEND_VERSION}",
        f"pdfium-{renderer_version}",
        f"fonts-{fonts_version}",
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


class _KeyLock:
    """锁表条目：锁 + 登记在案的使用者数（拿到手还没 acquire 的也算）。"""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


class PreviewCache:
    def __init__(
        self,
        cache_dir: Path,
        host: RenderHost,
        *,
        max_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.host = host
        self.max_bytes = int(max_bytes)
        self._locks: dict[str, _KeyLock] = {}
        self._locks_guard = threading.Lock()
        # 正在交出去的成品：`get()` 从算出键到 return 期间钉住它，任何线程的 `prune()` 都不删（Codex #471 第六轮
        # P2：只护自己这一次 prune 的 `keep`，两个不同键的 get 并发发布时会互删对方刚要交出的那张）
        self._pins: dict[Path, int] = {}
        self._pins_guard = threading.Lock()
        self._prune_lock = threading.Lock()
        self._renderer_version: str | None = None
        self._fonts_version: str | None = None
        self.renders = 0  # 真渲染的次数（同键去重的判据用）
        # 命中快路：文件指纹没变就沿用上次（从抄出的副本上）算出的内容 sha256，不再整份抄一遍——40 MB 的源每次
        # 命中 25 ms + 40 MB 写盘。只决定「能不能直接交出已有的成品」；要渲染的一律走副本那条路（见 get）
        self._identities = FingerprintMemo(maxsize=2048)
        self.fast_hits = 0

    # -- 身份 -------------------------------------------------------------
    def renderer_version(self) -> str:
        if self._renderer_version is None:
            self._renderer_version = str(self.host.ping().get("pdfium", "?"))
        return self._renderer_version

    def fonts_version(self) -> str:
        if self._fonts_version is None:
            self._fonts_version = fonts_policy_version()
        return self._fonts_version

    @staticmethod
    def source_identity(path: Path) -> str:
        """内容 sha256，分块算——一份几百 MB 的源 PDF 不该为了算身份整个读进内存（Codex #471 P2）。"""
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _stage(self, path: Path) -> tuple[Path, str]:
        """把源**一次读**成缓存目录里的一份不可变副本，边抄边算 sha256：键说的是这份字节，child 渲染的也是这份字节
        （Codex #471 第三轮 P2：只在渲染后复核 hash 挡不住 A→B→A 的换回——渲染的是 B，复核时已经换回 A）。
        副本与预览同目录、`.src.part` 后缀，`prune()` 只扫 *.png 不会碰它；用完即删。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        staged = self.cache_dir / f"stage.{os.getpid()}-{threading.get_ident():x}.src.part"
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as src, open(staged, "wb") as dst:
                for chunk in iter(lambda: src.read(_HASH_CHUNK), b""):
                    digest.update(chunk)
                    dst.write(chunk)
        except BaseException:
            # 抄到一半失败（源读不了 / 缓存卷满）：半截副本当场删掉——`prune()` 只扫 *.png，留下就是永久占位
            # （Codex #471 第四轮 P2）
            staged.unlink(missing_ok=True)
            raise
        return staged, digest.hexdigest()

    def key_for(
        self, source_id: str, path: Path, width_px: int, *, transparent: bool, page: int = 0
    ) -> str:
        return cache_key(
            source_id,
            self.source_identity(path),
            width_px,
            transparent=transparent,
            renderer_version=self.renderer_version(),
            fonts_version=self.fonts_version(),
            page=page,
        )

    def path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.png"

    # -- 锁表 -------------------------------------------------------------
    def _reserve(self, key: str) -> _KeyLock:
        """在 `_locks_guard` 里把这个键的锁**登记一个使用者**再交出去：表满时只淘汰 `users == 0` 的条目。
        判「有没有人拿着」不能看 `lock.locked()`——一个线程拿到锁对象、还没来得及 acquire 就被调度走，那一刻它
        是 unlocked 的，被淘汰之后第三个同键请求会另建一把，两边同时渲染同一张预览（Codex #471 第四轮 P2）。"""
        with self._locks_guard:
            entry = self._locks.get(key)
            if entry is None:
                if len(self._locks) >= _LOCKS_MAX:
                    for stale, held in list(self._locks.items()):
                        if held.users == 0:
                            del self._locks[stale]
                entry = _KeyLock()
                self._locks[key] = entry
            entry.users += 1
            return entry

    def _release(self, key: str, entry: _KeyLock) -> None:
        with self._locks_guard:
            entry.users -= 1

    @contextlib.contextmanager
    def _lock_for(self, key: str) -> Iterator[None]:
        entry = self._reserve(key)
        try:
            with entry.lock:
                yield
        finally:
            self._release(key, entry)

    # -- 取 / 渲染 --------------------------------------------------------
    @staticmethod
    def _usable(cached: Path) -> bool:
        try:
            return cached.stat().st_size > 0
        except OSError:
            return False

    def get(
        self,
        source_id: str,
        path: Path,
        width_px: int,
        *,
        transparent: bool = False,
        page: int = 0,
    ) -> Path:
        """命中就回缓存文件；否则渲染、临时发布、回最终文件。任何失败抛 `PreviewError`。

        两条路：**指纹快路**——文件的 `sources.file_fingerprint` 与上次抄副本时相同，就用那次副本上算出的内容
        sha256 算键，成品在就直接交出（不抄、不读源）；**副本路**——其余一切（指纹变了 / 第一次见 / 成品不在），
        照旧先抄副本边抄边算 hash、键与渲染绑在副本上。快路只交出已有的成品，从不渲染，所以 A→B→A 的换回
        在渲染那一侧仍由副本挡住；快路能错的只剩「指纹五元组全等而内容变了」，那时交出的是上一版内容的预览。"""

        def key_of(content: str) -> Path:
            try:
                renderer, fonts = self.renderer_version(), self.fonts_version()
            except RenderChildError as exc:
                # 第一次算键要 ping child 问 PDFium 版本：child 起不来 / 队列满在这里就会炸——同样翻成
                # PreviewError，调用方（/api/render）才能按稳定 code 回 503 / 500（Codex #476 P2）
                raise PreviewError(exc.code, exc.message) from exc
            return self.path_for(
                cache_key(
                    source_id,
                    content,
                    width_px,
                    transparent=transparent,
                    renderer_version=renderer,
                    fonts_version=fonts,
                    page=page,
                )
            )

        def fast_path() -> tuple[tuple | None, Path | None]:
            """`(此刻的指纹, 可直接交出的成品或 None)`。"""
            before, known = self._identities.lookup(path)
            if known is not None:
                cached = key_of(str(known))
                self._pin(cached)
                try:
                    if self._usable(cached):
                        self.fast_hits += 1
                        return before, cached
                finally:
                    self._unpin(cached)
            return before, None

        _, hit = fast_path()
        if hit is not None:
            return hit

        with self._lock_for(f"stage:{os.path.realpath(path)}"):
            # 排在前面的同源请求刚抄完、渲完、记下了身份：这里就命中，不再抄第二份（#489 第 4 条）
            before, hit = fast_path()
            if hit is not None:
                return hit
            staged, content = self._stage(path)
            try:
                # 副本的 hash 就是这份文件此刻的内容身份——抄的过程中文件没动（指纹前后相同）才记下
                self._identities.store(path, before, content)
                cached = key_of(content)
                self._pin(cached)
                try:
                    if self._usable(cached):
                        return cached
                    with self._lock_for(str(cached)):
                        if self._usable(cached):
                            return cached
                        cached.unlink(missing_ok=True)  # 零字节 = 上一次写到一半就断电 / 被杀
                        self._write(
                            staged,
                            width_px,
                            cached,
                            transparent=transparent,
                            page=page,
                            kind=kind_of(path),
                        )
                        self.prune()
                    return cached
                finally:
                    self._unpin(cached)
            finally:
                staged.unlink(missing_ok=True)

    def _pin(self, cached: Path) -> None:
        with self._pins_guard:
            self._pins[cached] = self._pins.get(cached, 0) + 1

    def _unpin(self, cached: Path) -> None:
        with self._pins_guard:
            n = self._pins.get(cached, 0) - 1
            if n > 0:
                self._pins[cached] = n
            else:
                self._pins.pop(cached, None)

    def _write(
        self,
        staged: Path,
        width_px: int,
        cached: Path,
        *,
        transparent: bool,
        page: int,
        kind: str = "pdf",
    ) -> None:
        """`staged` 是 `_stage()` 抄出来的不可变副本：child 渲染的就是键里 hash 过的那份字节，源文件此后怎么改
        都影响不到这张预览。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(f"{cached.stem}.{os.getpid()}-{threading.get_ident():x}.part.png")
        try:
            if kind != "pdf":
                # 位图素材（PNG / JPEG / TIFF）：PDFium 不认，本进程解码缩放（解码前按源像素预算记账）。
                # 副本没有扩展名，类型从源路径来
                try:
                    buf = raster_preview(
                        staged.read_bytes(),
                        kind,
                        width_px,
                        transparent=transparent,
                        max_pixels=SOURCE_MAX_PIXELS,
                    )
                except RasterDecodeError as exc:
                    raise PreviewError(exc.code, str(exc)) from exc
            else:
                try:
                    buf = self.host.render(
                        staged, width_px=width_px, page=page, transparent=transparent
                    )
                except RenderChildError as exc:
                    raise PreviewError(exc.code, exc.message) from exc
            tmp.write_bytes(encode_png(buf))
            self.renders += 1
            self._publish(tmp, cached)
        finally:
            tmp.unlink(missing_ok=True)  # replace 成功后已经不在了，这里是 no-op

    @staticmethod
    def _publish(tmp: Path, cached: Path) -> None:
        for attempt in range(_REPLACE_TRIES):
            try:
                os.replace(tmp, cached)
                return
            except PermissionError:
                try:
                    if cached.stat().st_size > 0:
                        return  # 别人写好的同一张图（同键同字节），让给它
                except OSError:
                    pass
                if attempt == _REPLACE_TRIES - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S)

    @staticmethod
    def _is_final(p: Path) -> bool:
        """成品名 = `<sha1 十六进制 40 位>.png`（`path_for`）；别人正在写的 `<key>.<pid>-<tid>.part.png` 与
        `stage.*.src.part` 都不是成品，`prune()` 不碰——删了在飞的 `.part.png`，那个请求会在 `os.replace`
        上 FileNotFoundError（Codex #471 第五轮 P2）。"""
        stem = p.name[:-4]
        return (
            p.name.endswith(".png")
            and len(stem) == 40
            and all(c in "0123456789abcdef" for c in stem)
        )

    def prune(self, *, keep: Path | None = None) -> int:
        """按 mtime 从旧到新删成品至预算内，返回删除数。**钉住的**（任何线程的 `get()` 正要交出去的，见 `_pin`）
        与 `keep` 预算再小也不删——否则某个 `get()` 会回一个已经被别的 pruner 删掉的路径；pruner 之间串行，
        两个同时算账不会把同一份预算删两遍。"""
        with self._prune_lock:
            return self._prune_locked(keep)

    def _prune_locked(self, keep: Path | None) -> int:
        try:
            files = sorted(
                (p for p in self.cache_dir.glob("*.png") if self._is_final(p)),
                key=lambda p: p.stat().st_mtime,
            )
            total = sum(p.stat().st_size for p in files)
        except OSError:
            return 0
        with self._pins_guard:
            pinned = set(self._pins)
        removed = 0
        for p in files:
            if total <= self.max_bytes:
                break
            if p in pinned or (keep is not None and p == keep):
                continue
            try:
                size = p.stat().st_size
                p.unlink()
                total -= size
                removed += 1
            except OSError:
                continue
        return removed
