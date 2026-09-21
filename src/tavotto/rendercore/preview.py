"""预览缓存：面板源（PDF）→ 指定像素宽度的 PNG，带磁盘缓存（统一实施包 U07，ADR 0066）。

旧后端那一面的用户合同（`app.py` 的 `/api/render` + `_write_render_cache` + `_publish_render_cache`，
`docs/rules/backend/pdf-backend-boundary.md`「`/api/render` 的磁盘缓存键」）逐条搬到这里，栅格由 render child
出（`renderhost.RenderHost.render(width_px=…)`），编码是本包的 PNG 编码器（`raster.encode_png`）。
**U07 不接 `app.py`**：`/api/render` 仍走 PyMuPDF；U08 换线时 `app.py` 的那三段（键 / 写 / 发布）由本模块
替掉，`source_sha1` 的 (mtime, size) memo 一并收编到这里的 `source_identity()`（本轮不做 memo：读一遍字节算
sha256，主语只有「内容」，没有 mtime 这一维）。

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
* 渲染进临时文件（**后缀仍是 .png**，同一目录）再 `os.replace`：读者要么看到旧的（完整）要么看到新的（完整）；
* **Windows 上 `os.replace` 盖不掉正被读的目标**（`PermissionError`）：目标已在且非空 → **退让**（同键字节
  必然相同）；目标不存在 / 零字节才重试，重试完仍不行如实抛出；
* 零字节缓存当场删掉重建；
* **异常一律抛出**——不返回空白图、不拿旧文件冒充这次成功（`must_fail`：异常返回空白图或旧图作成功）；
* `prune()` 按 mtime 从旧到新删至预算内（与旧 `prune_render_cache` 同形）。
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
from .fonts import allowlist_path
from .raster import encode_png
from .renderhost import RenderChildError, RenderHost

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


def fonts_policy_version() -> str:
    """字体政策版本 = allowlist 文件的 sha256 前 16 位（加一张脸 / 换一个 hash 都会变）。"""
    return hashlib.sha256(Path(allowlist_path()).read_bytes()).hexdigest()[:16]


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
        self._renderer_version: str | None = None
        self._fonts_version: str | None = None
        self.renders = 0  # 真渲染的次数（同键去重的判据用）

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
    def _lock_for(self, cached: Path) -> Iterator[None]:
        key = str(cached)
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
        """命中就回缓存文件；否则渲染、临时发布、回最终文件。任何失败抛 `PreviewError`。"""
        staged, content = self._stage(path)
        try:
            cached = self.path_for(
                cache_key(
                    source_id,
                    content,
                    width_px,
                    transparent=transparent,
                    renderer_version=self.renderer_version(),
                    fonts_version=self.fonts_version(),
                    page=page,
                )
            )
            if self._usable(cached):
                return cached
            with self._lock_for(cached):
                if self._usable(cached):
                    return cached
                cached.unlink(missing_ok=True)  # 零字节 = 上一次写到一半就断电 / 被杀
                self._write(staged, width_px, cached, transparent=transparent, page=page)
                self.prune()
            return cached
        finally:
            staged.unlink(missing_ok=True)

    def _write(
        self, staged: Path, width_px: int, cached: Path, *, transparent: bool, page: int
    ) -> None:
        """`staged` 是 `_stage()` 抄出来的不可变副本：child 渲染的就是键里 hash 过的那份字节，源文件此后怎么改
        都影响不到这张预览。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(f"{cached.stem}.{os.getpid()}-{threading.get_ident():x}.part.png")
        try:
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

    def prune(self) -> int:
        """按 mtime 从旧到新删至预算内，返回删除数。"""
        try:
            files = sorted(self.cache_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
        except OSError:
            return 0
        removed = 0
        for p in files:
            if total <= self.max_bytes:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                total -= size
                removed += 1
            except OSError:
                continue
        return removed
