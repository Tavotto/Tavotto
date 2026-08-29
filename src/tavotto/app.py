#!/usr/bin/env python3
"""
Tavotto — 论文多面板图可视化排版工具

扫描 figures 目录中的 PDF（矢量）与 PNG/JPG 面板，在浏览器画布上自由
拖拽 / 缩放 / 对齐 / 加标注，最终导出出版级 PNG（可选 DPI）和真矢量 PDF。

用法:
    tavotto [--figures 图目录] [--port 5089]      # 装成包后
    ./run.sh [同上]                                # 源码树
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    g,
    has_request_context,
    jsonify,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.exceptions import HTTPException

from . import pdfbackend
from .engine import (
    ai_bridge as engine_ai,
    ai_history as engine_ai_history,
    ai_providers as engine_ai_providers,
    atomicio as engine_atomicio,
    bootstrap as engine_bootstrap,
    brand as engine_brand,
    cli as engine_cli,
    config as engine_config,
    deprepair as engine_deprepair,
    diagnostics as engine_diagnostics,
    diagnostics_frontend as engine_diagnostics_frontend,
    discover as engine_discover,
    documents as engine_documents,
    enginesession as engine_enginesession,
    handoff as engine_handoff,
    locate as engine_locate,
    managedenv as engine_managedenv,
    nativehandoff as engine_nativehandoff,
    nativeperm as engine_nativeperm,
    nativesession as engine_nativesession,
    patchspec as engine_patchspec,
    pool as engine_pool,
    probe as engine_probe,
    project_refresh as engine_refresh,
    projectenv as engine_projectenv,
    registry as engine_registry,
    runcodes as engine_runcodes,
    runtime as engine_runtime,
    runtimeasset as engine_runtimeasset,
    session_client as engine_session_client,
    telemetry as engine_telemetry,
    updater as engine_updater,
)

PKG_ROOT = Path(__file__).resolve().parent  # 只读：包自带资源（前端构建产物）
DATA_ROOT = engine_config.data_dir()  # 可写：运行时产物（装成包后 site-packages 不可写）

# 素材边界的**唯一出处**在 `engine/project_refresh.py`：列给用户的素材
# （`/api/panels`）与"素材变了没有"（统一刷新的 inventory）必须是同一把尺。
# 这里只是别名，别在本文件里另写一份集合。
EXCLUDE_DIRS = engine_refresh.EXCLUDE_DIRS
PDF_EXT = engine_refresh.PDF_EXT
IMG_EXT = engine_refresh.IMG_EXT

MM_PER_PT = 25.4 / 72.0
RENDER_BUCKETS = [200, 400, 800, 1600, 3200]

app = Flask(__name__, static_folder=None)

# 会话认证钩子（浏览器与桌面模式共用，见 security.py / ADR 0008）必须在
# 首个请求前注册；测试的 test_client 与 --insecure-no-auth 下全部旁路
from . import (  # noqa: E402 —— 必须在 app 实例创建之后
    desktop as desktop_mode,
    security,  # 需要 app 实例存在后立即挂钩
)

security.install(app)

# 打开着的项目：id → ProjectCtx。**一个进程可以同时端着多个项目**——
# 不同浏览器标签页各开各的图库（标签页把自己的 pj 带在请求上，见
# `_request_ctx`）。没有任何项目时前端显示 Project Picker。
# 不再内置任何默认路径——项目由 --figures、最近项目或 Picker 决定。
PROJECTS: dict[str, "ProjectCtx"] = {}
DEFAULT_PROJECT: str | None = None  # 不带 pj 的请求落到这里
_PROJECT_LOCK = threading.Lock()
CACHE_DIR = DATA_ROOT / "cache"
EXPORT_DIR = DATA_ROOT / "exports"
LAYOUT_DIR = DATA_ROOT / "layouts"
# 前端构建产物：装成包后随 wheel 落在 tavotto/web/；源码树里还没打包，
# 回退到仓库的 web/dist（pnpm build 的默认输出），否则开发态首页 404。
WEB_DIST = PKG_ROOT / "web"
if not WEB_DIST.is_dir():
    WEB_DIST = PKG_ROOT.parent.parent / "web" / "dist"
# 「更新原图」时烙进文件的 override 基线：**每个项目一份**（文件名 = 项目 id）。
# 曾经是一个全局文件、按 stem 索引——两个图库里都有的 Fig1 会互相覆盖基线，
# 新拖入的面板于是继承了另一个项目的 override（写回时直接把别人的改动烙进图）。
BAKED_DIR = DATA_ROOT / "baked_overrides"
BAKED_PATH = DATA_ROOT / "baked_overrides.json"  # 旧全局文件：只读迁移源，不再写
# 读-改-写与「旧文件迁移」都持它。可重入：append_baked 持锁后还会调 load_baked。
_BAKED_LOCK = threading.RLock()

LOG = logging.getLogger("tavotto")

# ---- 缓存增长治理（三处无限增长点各给上限） --------------------------------
RENDER_CACHE_MAX_BYTES = 500 * 1024 * 1024  # cache/*.png 渲染缓存总预算
BACKUP_KEEP = 20  # 「更新原图」备份保留份数


def setup_logging() -> None:
    """stderr + cache/app.log（1MB×3 轮转）。重复调用无副作用。"""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    file = RotatingFileHandler(
        CACHE_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file.setFormatter(fmt)
    root.setLevel(logging.INFO)
    root.addHandler(stream)
    root.addHandler(file)


def prune_render_cache(max_bytes: int = RENDER_CACHE_MAX_BYTES) -> int:
    """渲染缓存按 mtime（≈生成时间）从旧到新删至预算内，返回删除数。"""
    try:
        files = sorted(CACHE_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
    except OSError:
        return 0
    removed = 0
    for p in files:
        if total <= max_bytes:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    if removed:
        LOG.info("渲染缓存清理: 删除 %d 个文件（预算 %dMB）", removed, max_bytes // (1024 * 1024))
    return removed


# ---- 渲染缓存的身份：内容哈希，不是 mtime ----------------------------------
#: `{路径: (mtime, size, sha1)}` 的进程内 memo。**身份永远是内容 sha1**，
#: mtime/size 只当「要不要重算」的信号：
#:   * 内容没变而 mtime 变了（touch、从备份还原、同步工具、重跑脚本出同一张图）
#:     → 重算一次哈希，值不变，**缓存照常命中**；
#:   * 内容变了 → mtime/size 必然也变，memo 失效重算，键跟着变，缓存必然失效。
#: 反过来拿 mtime 当身份就只有第二条成立，第一条会白丢一张 3200px 的预览。
#:
#: 第二条有一个例外，见 `source_sha1` 的「同 tick 改写」窗口。
_SOURCE_SHA1: dict[str, tuple[float, int, str]] = {}
_SOURCE_SHA1_LOCK = threading.Lock()
#: memo 条目上限（素材数量由用户的图库决定，不设上限就是慢性泄漏）。
#: 满了整表清掉：LRU 只为省几次哈希，不值得多维护一个数据结构。
_SOURCE_SHA1_MAX = 4096
#: mtime 粒度的保守上界（秒）。各文件系统差得很远——APFS/ext4 是纳秒、
#: HFS+/NTFS 到秒、FAT 到两秒——运行时问不出确切值，就按最粗的算。
#: 这个常量只决定「多久之内写过的文件不进 memo」，估大了只是少省几次哈希。
_SOURCE_SHA1_TICK = 2.0


def source_sha1(path: Path) -> str:
    """素材文件内容的 sha1（按 (mtime, size) memo，命中就不读文件）。

    **(mtime, size) 是「要不要重算」的信号，不是身份。** 它漏掉的是一种
    改写：文件在**同一个 mtime tick 内被改写成同样大小**时两者一个比特都
    不变，memo 会把上一版内容的摘要当成这一版交出去——而它正是渲染缓存的
    键，用户看到的就是「脚本重跑了、文件变了，画布还是旧图」。粗粒度
    文件系统（Windows/HFS+，一秒一跳）上这个窗口宽得能被日常操作撞到。

    堵法用 git 判 "racily clean" 的同一招：**只有能证明「以后不会再有写入
    落进同一个 tick」的条目才可信**。哈希算完时墙上时间已经越过
    `mtime + _SOURCE_SHA1_TICK` → 之后任何写入都只能落进下一个 tick →
    (mtime, size) 必然变 → 记 memo；还在窗口里就不记，下次老实重算。
    刚写完的文件因此在两秒内每次都真算一遍哈希，**这正是需要它算的时候**。

    时钟不一致（网络盘的 mtime 来自服务端）朝安全方向倒：服务端时钟偏快
    → 窗口判定永远成立 → 退化成「不 memo」，只是慢，不会错。
    """
    st = path.stat()
    key = str(path)
    sig = (st.st_mtime, st.st_size)
    with _SOURCE_SHA1_LOCK:
        hit = _SOURCE_SHA1.get(key)
    if hit is not None and hit[0] == sig[0] and hit[1] == sig[1]:
        return hit[2]
    digest = _sha1_of(path)
    if time.time() < sig[0] + _SOURCE_SHA1_TICK:
        # 还在同 tick 窗口里：这条 memo 不可信，不留。**旧条目也要一并清掉**
        # ——签名对不上只是「此刻对不上」，留着它就是一颗休眠的地雷：日后备份
        # 还原/同步工具把那个 mtime 连同另一份同尺寸内容一起写回来，它就又匹配
        # 了，而它挂的是更早那一版的摘要。改这条判据之前，每一次签名不匹配都会
        # 覆盖掉旧条目，这个形状不存在。
        with _SOURCE_SHA1_LOCK:
            _SOURCE_SHA1.pop(key, None)
        return digest
    with _SOURCE_SHA1_LOCK:
        if len(_SOURCE_SHA1) >= _SOURCE_SHA1_MAX:
            _SOURCE_SHA1.clear()
        _SOURCE_SHA1[key] = (sig[0], sig[1], digest)
    return digest


#: 同键写者串行用的锁表。键随内容/宽度/后端版本变，**必须封顶**——与
#: `_SOURCE_SHA1` 同一条纪律，不封顶就是慢性泄漏。
_RENDER_CACHE_LOCKS: dict[str, threading.Lock] = {}
_RENDER_CACHE_LOCKS_GUARD = threading.Lock()
_RENDER_CACHE_LOCKS_MAX = 512


def _cache_write_lock(cached: Path) -> threading.Lock:
    """同一个缓存键共用一把写锁。"""
    key = str(cached)
    with _RENDER_CACHE_LOCKS_GUARD:
        lock = _RENDER_CACHE_LOCKS.get(key)
        if lock is None:
            if len(_RENDER_CACHE_LOCKS) >= _RENDER_CACHE_LOCKS_MAX:
                # 只丢没人拿着的那些：正被持有的锁一旦从表里消失，下一个线程
                # 会为同一个键另建一把，互斥当场失效
                for stale, held in list(_RENDER_CACHE_LOCKS.items()):
                    if not held.locked():
                        del _RENDER_CACHE_LOCKS[stale]
            lock = threading.Lock()
            _RENDER_CACHE_LOCKS[key] = lock
        return lock


def _write_render_cache(src: Path, width_px: int, cached: Path) -> None:
    """渲染进临时文件再 `os.replace` 落盘（同键并发不会读到半个 PNG）。

    直写最终路径的话，同一张图被两个面板/两个标签页同时请求时，后到的那个
    `send_file` 出去的可能是只写了一半的文件——浏览器画半张图，而且**下次
    还命中那个坏文件**。replace 是原子的：读者要么看到旧的（完整），要么看到
    新的（完整）。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # **后缀必须仍是 .png**：后端按扩展名决定图片格式（`pix.save()` 见了 .tmp
    # 直接抛 ValueError）。留在同一个目录里也是有意的——进程被杀留下的半成品
    # 会被 `prune_render_cache()`（按 *.png 扫）当成最久未用的缓存正常回收，
    # 不需要另写一套清理；它删的是最旧的，正在写的那个永远是最新的。
    tmp = cached.with_name(f"{cached.stem}.{os.getpid()}-{threading.get_ident():x}.part.png")
    try:
        pdfbackend.render_preview_png(src, width_px, tmp)
        _publish_render_cache(tmp, cached)
    finally:
        tmp.unlink(missing_ok=True)  # replace 成功后已经不在了，这里是 no-op


#: 换名撞上 Windows 独占读句柄时的重试次数与间隔（总计 ~0.2s 的退让窗口）。
_REPLACE_TRIES = 5
_REPLACE_BACKOFF_S = 0.05


def _publish_render_cache(tmp: Path, cached: Path) -> None:
    """把临时文件换成最终缓存；目标正被人读着就**退让**，不是出错。

    POSIX 的 rename 可以盖掉一个正被读的文件，**Windows 不行**：werkzeug 的
    `send_file` 用 `open(path, "rb")` 拿着句柄（没带 `FILE_SHARE_DELETE`），
    这一刻 `os.replace` 直接 `PermissionError`（WinError 5 / 32）。上面那句
    「读者要么看到旧的、要么看到新的」在 Windows 上因此不成立——16 个并发
    请求撞一次，用户拿到的是 500，而图其实好好地在磁盘上
    （tests/test_windows_regressions.py 看护）。

    退让是安全的：缓存键 = `sha1(id|内容 sha1|宽度|后端-版本)`，同键的字节
    逐字节相同——目标已经在那儿且非空，就说明别人刚写完的正是同一张图。
    只有目标不存在（或是零字节的半成品）时才重试，重试完仍不行就如实抛出：
    那是真出了事，不该伪装成成功。
    """
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, cached)
            return
        except PermissionError:
            try:
                if cached.stat().st_size > 0:
                    return  # 别人写好的同一张图，让给它
            except OSError:
                pass
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_S)


def prune_backups(root: Path, keep: int = BACKUP_KEEP) -> int:
    """original_backups 只保留最近 keep 个时间戳目录。"""
    if not root.is_dir():
        return 0
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in dirs[:-keep] if keep else dirs:
        shutil.rmtree(old, ignore_errors=True)
    return max(0, len(dirs) - keep)


def _baked_path(ctx: "ProjectCtx") -> Path:
    return BAKED_DIR / f"{ctx.id}.json"


def _write_baked(path: Path, data: dict) -> None:
    """写回基线的原子落盘（唯一实现见 engine/atomicio.py）。"""
    engine_atomicio.write_json(path, data, indent=1)


def _migrate_global_baked(ctx: "ProjectCtx", path: Path) -> None:
    """旧的全局 baked_overrides.json → 本项目的分键文件（只读迁移，一次性）。

    按注册表过滤：只有本项目认得的 stem 才搬过来，别的项目的同名 Fig1 留在
    旧文件里等它自己迁移（所以**不删旧文件**）。迁完就写盘——哪怕一条都没搬
    也要写出空 dict，否则每次读都要再翻一遍旧文件，而且「本项目确实没有基线」
    与「还没迁移」这两种状态分不开。
    """
    try:
        legacy = json.loads(BAKED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        legacy = {}
    mine = {
        stem: v
        for stem, v in legacy.items()
        if isinstance(v, dict) and ctx.registry.for_stem(stem) is not None
    }
    try:
        _write_baked(path, mine)
    except OSError:  # 写不进去（只读介质）：这次照旧读旧文件，不拦渲染
        LOG.warning("baked 基线迁移写盘失败: %s", path, exc_info=True)
        return
    if mine:
        LOG.info("baked 基线迁移: %d 个 stem → %s", len(mine), path.name)


def load_baked(ctx: "ProjectCtx | None" = None) -> dict:
    """本项目的 {stem: {"versions": [{"ts", "patches"}...]}}；末位 = 当前基线。"""
    ctx = ctx if ctx is not None else current_ctx()
    with _BAKED_LOCK:
        path = _baked_path(ctx)
        if not path.exists():
            _migrate_global_baked(ctx, path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    for stem, v in list(data.items()):  # 迁移单版本旧格式
        if "patches" in v:
            data[stem] = {"versions": [{"ts": v.get("updated_at", ""), "patches": v["patches"]}]}
    return data


def append_baked(stem: str, patches: list, ctx: "ProjectCtx | None" = None) -> None:
    """读-改-写全程持锁（同一项目内），落盘走 `_write_baked` 的原子替换。

    每条版本都带 `patch_hash`（`patchspec` 的权威实现）：写回响应里回的是同一个
    值，用户/排障时能把「磁盘上这张图」与「哪一版 patches」对上。旧条目没有这个
    键，读取端一律按缺失兼容。
    """
    ctx = ctx if ctx is not None else current_ctx()
    with _BAKED_LOCK:
        data = load_baked(ctx)
        entry = data.setdefault(stem, {"versions": []})
        entry["versions"].append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "patches": patches,
                "patch_hash": engine_patchspec.patch_hash(patches),
            }
        )
        entry["versions"] = entry["versions"][-50:]
        _write_baked(_baked_path(ctx), data)


def _baseline_patches(stem: str, baked: dict) -> list:
    """baked 表由调用方传进来——它曾经是个模块级缓存，多项目下那就是
    「A 项目扫一遍素材，B 项目的基线全被换掉」。"""
    versions = (baked.get(stem) or {}).get("versions") or []
    return versions[-1]["patches"] if versions else []


class NoProjectError(Exception):
    """当前没有打开的项目；API 层转成 409，前端据此显示 Project Picker。"""


class ProjectCtx:
    """一个打开着的项目：图库路径 + 它自己的脚本注册表。

    每个标签页可以指向不同的 ctx，所以注册表**不能**再是模块全局的——
    两个图库里同名的 Fig1.pdf 必须各自映射到各自的脚本。
    """

    def __init__(self, path: Path, pid: str, registry: engine_registry.Registry):
        self.path = path
        self.id = pid
        self.registry = registry

    def __repr__(self) -> str:  # 日志用
        return f"<Project {self.id} {self.path}>"


def _project_id(path: Path) -> str:
    """项目短 id：路径的稳定哈希。

    URL 参数（`?pj=`）与 `<img src>` 都要带上它，用完整路径既难看又会把
    用户的目录结构塞进浏览器历史；短 id 还天然对大小写/分隔符差异免疫。
    """
    # 归一化按**卷**判而不是按 os.name（macOS 的 APFS 同样大小写不敏感）：
    # 与 `pool._norm_dir()` 共用 `config.normalize_path_identity`，两边分头
    # 判断的话，一个认为是同一个项目、另一个认为是两个。
    key = engine_config.normalize_path_identity(path)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _request_ctx() -> "ProjectCtx | None":
    """本次请求作用于哪个项目：显式 pj > 默认项目。

    pj 走查询参数或请求头两条路：`fetch` 统一加请求头，但 `<img src>` 和
    EventSource 加不了头，只能用查询参数——两条都认才不会有一半 API 串项目。
    """
    # 后台线程（watcher 回调、启动流程）没有请求上下文，落到默认项目
    pid = (
        (request.args.get("pj") or request.headers.get("X-Tavotto-Project") or "").strip()
        if has_request_context()
        else ""
    )
    if pid:
        ctx = PROJECTS.get(pid)
        if ctx is not None:
            return ctx
        # 指名了一个不存在的项目（后端重启 / 项目已关闭）：不能悄悄落到别的
        # 项目上——那会让标签页对着另一个图库继续编辑。
        raise NoProjectError()
    return PROJECTS.get(DEFAULT_PROJECT or "")


def current_ctx() -> ProjectCtx:
    ctx = _request_ctx()
    if ctx is None:
        raise NoProjectError()
    return ctx


def require_project() -> Path:
    return current_ctx().path


def current_registry() -> engine_registry.Registry:
    return current_ctx().registry


def safe_resolve(rel_id: str) -> Path:
    """把面板 id（相对路径）解析回 figures 目录内的真实文件，禁止越权访问。"""
    root = require_project()
    p = (root / rel_id).resolve()
    if not p.is_relative_to(root.resolve()):
        abort(403)
    if not p.is_file():
        abort(404)
    if p.suffix.lower() not in PDF_EXT | IMG_EXT:
        abort(403)
    return p


def scan_panels() -> list[dict]:
    """扫描 figures 目录：PDF 是首选（矢量）；无同名 PDF 的图片按位图收录。"""
    ctx = current_ctx()
    baked = load_baked(ctx)  # 本项目的写回基线，局部变量（绝不跨项目共享）
    panels = []
    root = ctx.path.resolve()
    # 「哪些文件算素材」的判据在 `engine/project_refresh.iter_assets()`（剪枝、
    # 隐藏文件、同名 PDF 盖过位图都在那儿）——统一刷新的 inventory 用的是同一
    # 个函数。这里只负责 probe 出尺寸并挂上注册表信息。
    assets = engine_refresh.iter_assets(root)
    LOG.info("素材扫描: %s → %d 个素材", root, len(assets))

    for p, kind in assets:
        ext = p.suffix.lower()
        rel = str(p.relative_to(root))
        folder = str(p.parent.relative_to(root)) or "."
        entry = {
            "id": rel,
            "name": p.stem,
            "folder": folder,
            "mtime": int(p.stat().st_mtime),
        }
        try:
            if kind == "pdf":
                probe = pdfbackend.probe_asset(p, "pdf")
                entry.update(
                    kind="pdf",
                    native_w_mm=round(probe["w_pt"] * MM_PER_PT, 3),
                    native_h_mm=round(probe["h_pt"] * MM_PER_PT, 3),
                )
            else:
                probe = pdfbackend.probe_asset(p, "raster")
                # matplotlib 输出 PNG 为 600ppi；照片等按 300ppi 给个初始物理尺寸
                ppi = 600 if ext == ".png" else 300
                entry.update(
                    kind="raster",
                    px_w=probe["px_w"],
                    px_h=probe["px_h"],
                    native_w_mm=round(probe["px_w"] / ppi * 25.4, 3),
                    native_h_mm=round(probe["px_h"] / ppi * 25.4, 3),
                )
            info = current_registry().for_stem(p.stem)
            if info is not None:  # 可参数化面板：有产出它的 matplotlib 脚本
                entry.update(script=info["script"], cost=info["cost"])
                baseline = _baseline_patches(p.stem, baked)
                if baseline:
                    entry["baked_overrides"] = baseline
        except Exception:
            # 单个素材坏了不拖垮整个列表，但绝不静默——用户丢面板时
            # app.log 里要能看到是哪个文件、为什么
            LOG.warning("素材扫描跳过 %s（probe 失败）", p, exc_info=True)
            continue
        panels.append(entry)
    return panels


# ---------------------------------------------------------------------------
# API
#
# **错误响应的语言归前端管**（界面支持中英文，见 docs/i18n.md）。后端不知道、
# 也不该知道用户选了哪门语言——它没有 Accept-Language 之外的线索，而语言偏好
# 存在浏览器/桌面壳这一侧。
#
# 于是约定：**每一个** `{"error": ...}` 响应都带稳定的 `code`（多数还带
# `params`），前端按 code 查自己的文案、把 params 插进去；`error` 字段保留
# 人可读的中文原文，作为 ① 老前端与 curl 调试的回退，② 前端没有对应文案时
# 的兜底。**code 一旦发布就不能改名**——改了等于让所有装着旧前端的用户看到
# 一句英文 key。「请求畸形类」的校验错误也要 code（2026-08-21 起，审计
# P1-02）：tests/test_error_codes.py 逐行扫本文件，没有 code 的 error 响应
# 直接红——诊断材料（traceback / 日志原文）照旧原样附带，不翻译。
# ---------------------------------------------------------------------------
@app.errorhandler(NoProjectError)
def _no_project(_exc):
    return jsonify({"error": "尚未打开项目", "code": "no_project"}), 409


@app.errorhandler(engine_documents.DocumentError)
def _document_error(exc):
    """文档结构不合法（含「来自更新版本的 Tavotto」「名称已被占用」）。"""
    return jsonify(exc.as_payload()), exc.status


@app.errorhandler(engine_atomicio.AtomicWriteError)
def _atomic_write_error(exc):
    """原子写失败。

    载荷本身有问题（非有限数）是 **400**——重试一百次也是同样的结果，
    前端该提示用户而不是静默重排队；磁盘/权限类是 **500**，那是可重试的。
    两类都保留 code，`tests/test_error_codes.py` 逐行扫这个文件。
    """
    if exc.code == "non_finite_number":
        return jsonify(exc.as_payload()), 400
    LOG.error("原子写失败: %s %s: %s", request.method, request.path, exc.message)
    return jsonify(exc.as_payload()), 500


@app.errorhandler(engine_refresh.RefreshError)
def _refresh_error(exc):
    """项目刷新失败。**400 而不是 500**：这一族的成因是用户图库里的东西
    （注册表被手改坏了、目录读不动），重试一百次也是同样的结果，用户要看到
    的是"你的注册表怎么了"，不是一句"服务器错误"。内存里的注册表原封不动，
    已打开的项目照常能用（`engine/project_refresh.py` 的失败语义）。"""
    LOG.warning("项目刷新失败: %s %s: %s", request.method, request.path, exc.message)
    return jsonify(exc.as_payload()), 400


def _worker_error_payload(exc) -> dict:
    """worker 错误的统一响应体。

    `module` 只在 code == "missing_dependency" 时有值：用户脚本 import 了当前
    渲染环境里没有的包（内置 runtime 只带常用科学栈）。前端据此给「换成你自己
    的环境」这个可执行出口，而不是甩一段 ModuleNotFoundError。
    """
    body = {"error": str(exc), "traceback": exc.traceback_text, "code": exc.code}
    if getattr(exc, "module", ""):
        body["module"] = exc.module
    # 项目环境自动接手失败时的结构化原因（ADR 0018）：找不到 venv / venv 里
    # 也没这个包 / 那个环境没有 matplotlib / Python 版本不支持。前端据此给出
    # 各自不同的恢复引导——四种情况用户要做的事完全不同，混成一句话等于没说。
    detail = getattr(exc, "project_env", None)
    if isinstance(detail, dict) and detail.get("code"):
        body["project_env"] = {
            "code": detail.get("code", ""),
            "module": detail.get("module", ""),
            "venv": _project_relative(detail.get("venv", "")),
            "candidates": [_project_relative(c) for c in (detail.get("candidates") or [])],
            "python_version": (detail.get("health") or {}).get("python_version", ""),
        }
    if exc.code == "missing_dependency" and getattr(exc, "module", ""):
        repair = _dependency_repair_offer(exc, detail)
        if repair is not None:
            body["dependency_repair"] = repair
    return body


def _dependency_repair_offer(exc, project_env: dict | None) -> dict | None:
    """「这个缺的包能不能一键装上」——挂在 missing_dependency 的错误响应里。

    **只读判断**（ADR 0019 §UX）：解析 import 名 → distribution、看项目里那个
    `.venv` 是不是「除了这个包之外都健康」、看能不能建受管环境。一个子进程都
    不起，也**绝不**在这里安装任何东西——安装要用户先看到「装什么、装到哪、
    会不会改你的环境」再点一次。

    没有打开项目（CLI 单文件、素材库之外的路径）时回 None：修复的作用域是
    项目，没有项目就没有可信的依赖声明，也没有地方记账。
    """
    try:
        root = str(require_project())
    except NoProjectError:
        return None
    script = getattr(exc, "script_name", "") or ""
    try:
        return engine_deprepair.offer(root, script, exc.module, project_env)
    except (OSError, ValueError) as err:  # 修复建议失败不该盖掉原始错误
        LOG.warning("依赖修复建议生成失败: %s", err)
        return None


def _project_relative(path: str) -> str:
    """项目内的路径显示成项目相对（`.venv`），项目外的原样交出。

    界面上「找到了 `/Users/张三/paper/.venv`」远不如「找到了 `.venv`」好读，
    诊断包也不该无谓地带上用户主目录名。
    """
    if not path:
        return ""
    try:
        root = require_project()
    except NoProjectError:  # 没有打开项目时不该影响错误响应
        return str(path)
    # **走 projectenv 那一份**：它刻意不 `resolve()` 解释器本身。
    # `.venv/bin/python` 在 POSIX 上是指向基础解释器的软链接，跟着它走的话
    # 每一个项目 venv 都会被判成「在项目外」，界面上于是显示一条用户主目录
    # 打头的绝对路径，而我们本想显示的是 `.venv`。
    return engine_projectenv.project_relative(root, str(path)) or str(path)


@app.errorhandler(engine_pool.WorkerError)
def _worker_error(exc):
    """worker 类错误统一带上 code。

    多数端点自己 catch 了，但 `_engine_worker()` 这类调用常落在 try 之外——
    没有这个处理器时它们会掉进通用 Exception 处理器，`code` 全丢，前端就分不出
    「缺渲染环境」（该给引导）和「脚本报错」（该给 traceback）。
    """
    LOG.error("worker 错误: %s %s: %s", request.method, request.path, exc)
    return jsonify(_worker_error_payload(exc)), 500


@app.errorhandler(engine_runcodes.RunError)
def _run_error(exc):
    """`tavotto run` 家族的结构化失败（ADR 0021 §13）。

    多数端点自己 catch 了，但 `_engine_worker()` → `enginesession.resolve()`
    这条路常落在 try 之外：native 面板的会话结束之后它会抛
    `native_session_offline`，而没有这个处理器时它会掉进通用 Exception，
    `code` 全丢——前端就分不出「会话结束了，重跑那条命令即可」和「后端崩了」。
    """
    return _native_error(exc)


@app.errorhandler(Exception)
def _unhandled(exc):
    """未处理异常：记日志并回 JSON（前端各处都按 JSON 解析错误）。
    abort() 的 HTTPException 原样放行，不动 403/404 语义。"""
    if isinstance(exc, HTTPException):
        return exc
    LOG.exception("未处理异常: %s %s", request.method, request.path)
    return jsonify(
        {
            "error": f"{type(exc).__name__}: {exc}",
            "code": "internal_error",
            "params": {"reason": f"{type(exc).__name__}: {exc}"},
        }
    ), 500


@app.get("/")
def index():
    """工作台（Vite 构建产物）。index.html 必须每次验证，
    否则前端部署新 bundle 后旧标签页/启发式缓存会继续跑旧代码。"""
    if not (WEB_DIST / "index.html").is_file():
        # 源码检出直接跑起来时最常见的一步没做——给出确切命令而不是白屏 404
        return (
            f"<h1>{engine_brand.PRODUCT_NAME}: 前端尚未构建</h1>"
            "<p>请先执行：<code>python scripts/build_frontend.py</code>"
            "（需要 node + pnpm），或改用发行版："
            "<code>pipx install tavotto</code>。</p>"
        ), 503
    resp = send_from_directory(WEB_DIST, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    # 已中毒的资产缓存只有这条路够得着（issue #115 评审）：0.10.x 在注册表
    # 改坏的机器上把 text/plain 的 .js 按 immutable 缓存了一年，bundle 内容
    # 哈希不变时升级后浏览器根本不再发请求，服务端改什么都到不了。"cache"
    # 只清本 origin 的 HTTP 缓存（sessionStorage 的 pj、localStorage 的
    # autosave 兜底都不碰）；127.0.0.1 是 trustworthy origin，无 HTTPS 也生效。
    resp.headers["Clear-Site-Data"] = '"cache"'
    return resp


# 打包资产的 Content-Type 不许由机器级文件关联决定（issue #115）：Windows 上
# mimetypes 读注册表，HKCR\.js 被改成 text/plain 时 send_from_directory 会照猜，
# 而 <script type="module"> 受严格 MIME 检查——WebView2 拒绝执行，整窗白屏。
# 这里只列浏览器做严格校验、猜错即拒载的几类；图片等可嗅探类型照旧交给猜测。
ASSET_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
}


@app.get("/assets/<path:name>")
def web_assets(name):
    """vite 输出的 hash 资源，index.html 里是绝对路径 /assets/…"""
    resp = send_from_directory(WEB_DIST / "assets", name)
    forced = ASSET_MIME.get(os.path.splitext(name)[1].lower())
    if forced:
        resp.mimetype = forced
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.get("/api/version")
def api_version():
    """当前前端构建 id：旧标签页据此发现自己过期并提示刷新。"""
    try:
        html = (WEB_DIST / "index.html").read_text(encoding="utf-8")
        m = re.search(r"assets/(index-[\w-]+)\.js", html)
        build = m.group(1) if m else "unknown"
    except OSError:
        build = "dev"
    resp = jsonify({"build": build, "version": engine_updater.current_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/panels")
def api_panels():
    resp = jsonify({"figures_dir": str(require_project()), "panels": scan_panels()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/render")
def api_render():
    """把面板渲染为指定像素宽度的 PNG（带磁盘缓存），用于画布显示与缩略图。"""
    rel_id = request.args.get("id", "")
    want_w = int(request.args.get("w", 400))
    w = next((b for b in RENDER_BUCKETS if b >= want_w), RENDER_BUCKETS[-1])

    path = safe_resolve(rel_id)
    # 缓存身份 =（面板 id, **内容哈希**, 宽度, 渲染后端与版本）。曾经这里是
    # `path.stat().st_mtime`：mtime 是「什么时候被碰过」，不是「里面是什么」，
    # 拿它当身份两头都错——内容没变而 mtime 变了白丢缓存，换了渲染后端版本
    # （出来的像素可能不一样）却照旧命中。
    key = hashlib.sha1(
        f"{rel_id}|{source_sha1(path)}|{w}|"
        f"{pdfbackend.BACKEND_NAME}-{pdfbackend.BACKEND_VERSION}".encode()
    ).hexdigest()
    cached = CACHE_DIR / f"{key}.png"
    try:
        usable = cached.stat().st_size > 0
    except OSError:
        usable = False
    if not usable:
        # 同键并发只让一个线程真渲染：同一素材在画布里放几份、缩略图与主图同时
        # 上，是常态。其余线程渲出来的字节完全一样（键含内容哈希），多渲一次
        # 就是白烧一次 CPU。锁内复查一次，看到成品直接用。
        # 这把锁**只保本进程**——杀毒软件、别的进程不听它的，所以失败点上的
        # 退让（`_publish_render_cache`）仍然是必须的，两者不互相替代。
        with _cache_write_lock(cached):
            try:
                usable = cached.stat().st_size > 0
            except OSError:
                usable = False
            if not usable:
                # 零字节 = 上一次写到一半就断电/被杀（旧的直写路径留下的产物）。
                # 把空文件当缓存交出去，用户看到的是一个永远画不出来的面板。
                cached.unlink(missing_ok=True)
                _write_render_cache(path, w, cached)
                prune_render_cache()
    # no-cache = 每次向服务器验证（304 极快）；内容一变（sha1 进 key）立即失效。
    # 不用长 max-age——「更新原图」后旧 URL 也不能再吃浏览器缓存。
    resp = send_file(cached, mimetype="image/png", conditional=True)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/file")
def api_file():
    """位图面板直接回传原文件（浏览器自行缩放显示）。"""
    path = safe_resolve(request.args.get("id", ""))
    resp = send_file(path, conditional=True)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _resolve_panel_source(o: dict, dpi: int, sink: list | None = None) -> Path:
    """面板对象 → 待嵌入的源文件路径。带 override 的 ⚡ 面板先由引擎按全质量
    重渲染成临时 PDF，导出的永远是矢量而不是画布上的预览位图。

    `sink` 收集 worker 的 warnings（哪些 override 没写进去）。导出**不因此
    中断**——用户要的成图已经出来了，但不能像以前那样把它们直接扔掉：
    「导出的图和画布上不一样」必须有个说法。

    runtime 面板（ADR 0013）**永远由当次权威 worker 渲染**：没有磁盘原件可
    嵌，也**绝不拿 materialized cache 的旧文件冒充最新结果**——worker 起不
    来就让错误如实抛出（先重新运行，而不是拿旧图交差）。
    """
    rel_id = str(o.get("id", ""))
    if engine_runtimeasset.is_runtime_id(rel_id):
        # **走同一扇门**（`_engine_worker` → `enginesession.resolve`）：这里曾经
        # 直接 `pool.get()`，于是同一张 native 图「预览是 native 的、画布导出
        # 是 safe 的」——两张不一样的图，而界面上什么都没说。
        worker, stem = _engine_worker(rel_id)
        tmp = worker.export_dir / f"{stem}.pdf"
        resp = worker.export(stem, o.get("overrides") or [], str(tmp), "pdf", dpi)
        if sink is not None:
            for w in resp.get("warnings") or []:
                msg = f"{rel_id}: {w}"
                if msg not in sink:
                    sink.append(msg)
        return tmp
    path = safe_resolve(o["id"])
    overrides = o.get("overrides") or []
    if overrides:
        info = current_registry().for_stem(path.stem)
        if info is not None:
            worker = _safe_worker(info["script"], info["entry"], path.stem)
            tmp = worker.export_dir / f"{path.stem}.pdf"
            resp = worker.export(path.stem, overrides, str(tmp), "pdf", dpi)
            if sink is not None:
                for w in resp.get("warnings") or []:
                    msg = f"{o.get('id', path.name)}: {w}"
                    if msg not in sink:
                        sink.append(msg)
            path = tmp
    return path


@app.post("/api/export")
def api_export():
    """按布局合成最终图：PDF 走 show_pdf_page 保持真矢量，PNG 由该 PDF 渲染。

    新契约：objects[] 为统一 z 序列表（panel/text/arrow/shape，从底到顶），
    hidden 对象后端再过滤一道；旧契约 items[]+texts[] 保留兼容（texts 恒在最上）。
    """
    spec = request.get_json(force=True)
    page_w = float(spec["page_w_mm"])
    page_h = float(spec["page_h_mm"])
    dpi = int(spec.get("dpi", 600))
    formats = spec.get("formats", ["png", "pdf"])
    stem = re.sub(r"[^\w\-一-鿿]+", "_", spec.get("stem") or "composed") or "composed"

    objects = spec.get("objects")
    if objects is None:  # 旧契约（老 bundle 标签页）
        objects = [{"type": "panel", **it} for it in spec.get("items", [])] + [
            {"type": "text", **t} for t in spec.get("texts", [])
        ]

    t0 = time.time()
    canvas = pdfbackend.compose(page_w, page_h)

    # 面板重渲染时 worker 报的 warning（元素不存在 / 属性不支持 / 应用失败）：
    # 随响应透出，不阻断导出
    warnings: list[str] = []

    def resolve(obj: dict, out_dpi: int) -> Path:
        return _resolve_panel_source(obj, out_dpi, warnings)

    for o in objects:
        if o.get("hidden"):
            continue
        try:
            canvas.place(o, dpi, resolve)
        except engine_pool.WorkerError as exc:
            canvas.close()
            kind = o.get("type")
            LOG.error("导出失败: %s 重渲染出错: %s", o.get("id", kind), exc)
            return jsonify(
                {
                    "error": f"{o.get('id', kind)} 重渲染失败: {exc}",
                    "code": "export_render_failed",
                    "params": {"id": str(o.get("id", kind)), "reason": str(exc)},
                    "traceback": exc.traceback_text,
                }
            ), 500

    out_dir = project_export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files = []
    ts = time.strftime("%m%d_%H%M%S")
    if "pdf" in formats:
        name = f"{stem}_{ts}.pdf"
        canvas.save_pdf(out_dir / name)
        out_files.append({"name": name, "url": f"/exports/{name}"})
    if "png" in formats:
        name = f"{stem}_{ts}.png"
        canvas.save_png(out_dir / name, dpi)
        out_files.append({"name": name, "url": f"/exports/{name}"})
    canvas.close()
    # 可选的导出 proof report：预检结果与设置随成图落盘，作为投稿留档
    proof = spec.get("proof")
    if isinstance(proof, dict):
        name = f"{stem}_{ts}_proof.json"
        proof = {
            **proof,
            "files": [f["name"] for f in out_files],
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (out_dir / name).write_text(
            json.dumps(proof, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        out_files.append({"name": name, "url": f"/exports/{name}"})
    LOG.info(
        "导出: %s（%d 对象, %s, %.0fms）%s",
        [f["name"] for f in out_files],
        len(objects),
        formats,
        (time.time() - t0) * 1000,
        f"，{len(warnings)} 条警告" if warnings else "",
    )
    if warnings:
        LOG.warning("导出警告: %s", warnings)
    # 激活事件：**文件真的写完之后**才记，且只记形状不记内容——没有 stem、
    # 没有导出目录、没有画布名、没有项目信息。埋点在服务端而不是前端，是因为
    # 前端记的是「用户点了导出」，点了之后还可能失败；这里记的是「导出成功了」。
    # capture() 自己吞掉一切失败，这一行不可能影响上面这个响应。
    engine_telemetry.capture(
        "export_completed",
        {
            "pdf": "pdf" in formats,
            "png": "png" in formats,
            "with_proof": isinstance(spec.get("proof"), dict),
            "panel_count": min(
                sum(1 for o in objects if o.get("type") == "panel" and not o.get("hidden")), 1000
            ),
        },
    )
    return jsonify({"files": out_files, "export_dir": str(out_dir), "warnings": warnings})


@app.get("/exports/<path:name>")
def api_exports(name):
    return send_from_directory(project_export_dir(), name, as_attachment=False)


# ------------------------- 可复现项目包 -------------------------------------
def _doc_objects(doc: dict) -> list[dict]:
    """布局文档里的全部对象：schema 2 单画布 / schema 3 跨全部画布。"""
    if doc.get("schema") == 3:
        return [
            o
            for c in doc.get("canvases", [])
            if isinstance(c, dict)
            for o in c.get("objects", [])
            if isinstance(o, dict)
        ]
    return [o for o in doc.get("objects", []) if isinstance(o, dict)]


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@app.post("/api/package")
def api_package():
    """把布局打成可复现项目包（zip）：布局 + 引用素材 + 源脚本 + 清单。

    清单记录每个素材的 sha1，换机器打开时据此发现缺失/内容漂移；
    打包只读 figures 目录，不写入任何源文件。
    """
    root = require_project()
    body = request.get_json(force=True)
    doc = body.get("doc")
    if not isinstance(doc, dict) or doc.get("schema") not in (2, 3):
        return jsonify({"error": "无效的布局文档", "code": "invalid_document"}), 400
    default_stem = (
        (doc.get("project") or {}).get("name") if doc.get("schema") == 3 else doc.get("name")
    )
    stem = re.sub(r"[^\w\-一-鿿]+", "_", body.get("stem") or default_stem or "package")

    panel_ids = sorted(
        {o.get("fileId") for o in _doc_objects(doc) if o.get("type") == "panel" and o.get("fileId")}
    )
    assets, missing_now, scripts, runtime_assets = [], [], {}, []
    for rel_id in panel_ids:
        # runtime 素材（ADR 0013）：包里带**描述符 + 源脚本**，不带 cache
        # 副本——cache 是本机派生物不是原件，接收方跑一次脚本即可重建。
        # 它没有磁盘文件这件事是设计而非缺失，绝不进 missing 清单。
        if engine_runtimeasset.is_runtime_id(rel_id):
            info = engine_runtimeasset.resolve(rel_id, current_registry())
            entry = {"id": rel_id, "kind": "runtime"}
            if info is not None:
                entry.update(script=info["script"], stem=info["stem"])
                scripts[info["script"]] = root / info["script"]
            runtime_assets.append(entry)
            continue
        p = (root / rel_id).resolve()
        entry = {"id": rel_id}
        if p.is_relative_to(root.resolve()) and p.is_file():
            entry.update(sha1=_sha1_of(p), mtime=int(p.stat().st_mtime), bytes=p.stat().st_size)
            info = current_registry().for_stem(p.stem)
            if info is not None:
                entry["script"] = info["script"]
                scripts[info["script"]] = root / info["script"]
            assets.append(entry)
        else:
            missing_now.append(rel_id)

    manifest = {
        "kind": engine_brand.PACKAGE_KIND,
        "version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "figures_dir": str(root),
        "page": doc.get("page"),
        "export_settings": body.get("settings") or {},
        "assets": assets,
        "missing_at_pack_time": missing_now,
        "scripts": sorted(scripts),
        # 旧读取端不认识这个键会原样忽略（package/open 本来就不校验 kind）
        "runtime_assets": runtime_assets,
    }

    out_dir = project_export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    # 包是 .tavotto 单文件扩展（zip 容器）。**写出端只有这一个名字**——改名到
    # Tavotto 时旧扩展降级的做法被否掉了（见 engine/brand.py 的干净断裂说明）。
    # 读取端（api_package_open）不校验 kind、也不看扩展名，只认 zip 里的结构，
    # 所以更早的包实际上仍打得开——那是**不校验的副作用，不是我们许下的承诺**。
    name = f"{stem}_{time.strftime('%m%d_%H%M%S')}{engine_brand.PACKAGE_EXT}"
    out = out_dir / name
    import zipfile  # noqa: PLC0415 — 仅此端点用

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps(doc, ensure_ascii=False, indent=1))
        z.writestr("package_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for entry in assets:
            z.write(root / entry["id"], f"assets/{entry['id']}")
        for rel, path in scripts.items():
            if path.is_file():
                z.write(path, f"scripts/{rel}")
    LOG.info("项目包: %s（%d 素材, %d 脚本）", name, len(assets), len(scripts))
    return jsonify(
        {"name": name, "url": f"/exports/{name}", "assets": len(assets), "missing": missing_now}
    )


@app.post("/api/package/open")
def api_package_open():
    """检视上传的项目包：取出布局，并对照当前图库列出缺失/内容漂移的素材。
    只读不写——素材永远不会被自动安装进图库。"""
    file = request.files.get("package")
    if file is None:
        return jsonify(
            {"error": "缺少上传文件（multipart 字段 package）", "code": "package_file_missing"}
        ), 400
    import io
    import zipfile  # noqa: PLC0415

    try:
        z = zipfile.ZipFile(io.BytesIO(file.read()))
        doc = json.loads(z.read("layout.json"))
        manifest = json.loads(z.read("package_manifest.json"))
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        return jsonify(
            {
                "error": f"不是有效的项目包: {exc}",
                "code": "package_invalid",
                "params": {"reason": str(exc)},
            }
        ), 400
    if doc.get("schema") not in (2, 3):
        return jsonify(
            {
                "error": "项目包里的布局既不是 schema 2 也不是 schema 3",
                "code": "package_schema_unsupported",
            }
        ), 400

    root = require_project()
    missing, drifted = [], []
    for entry in manifest.get("assets", []):
        rel_id = entry.get("id", "")
        # 老包里不会有 runtime 条目；防的是手改包/未来版本混入——runtime
        # 素材没有磁盘文件是设计，不按缺失报
        if engine_runtimeasset.is_runtime_id(rel_id):
            continue
        p = (root / rel_id).resolve()
        if not (p.is_relative_to(root.resolve()) and p.is_file()):
            missing.append(rel_id)
        elif entry.get("sha1") and _sha1_of(p) != entry["sha1"]:
            drifted.append(rel_id)
    return jsonify(
        {
            "doc": doc,
            "manifest": {
                k: manifest.get(k)
                for k in ("created_at", "figures_dir", "page", "export_settings", "scripts")
            },
            "missing": missing,
            "drifted": drifted,
        }
    )


# ------------------------- SSE 事件流 --------------------------------------
_sse_subs: list[queue.Queue] = []


def sse_publish(event: str, data: dict) -> None:
    for q in list(_sse_subs):
        try:
            q.put_nowait((event, data))
        except queue.Full:
            pass


@app.get("/api/events")
def api_events():
    q: queue.Queue = queue.Queue(maxsize=200)
    _sse_subs.append(q)

    def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev, data = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            _sse_subs.remove(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ------------------------- 项目（Project）管理 -------------------------------
# 对象层级见 docs/adr/0001-project-canvas-tab-object.md：Project = 图库路径 +
# 素材根 + 导出/备份位置 + 设置。用户级配置（最近项目等）存 engine_config。
def _script_change_handler(ctx: "ProjectCtx"):
    """watcher 回调必须绑定到具体项目——事件里带上 pj，别的标签页才不会
    因为另一个图库的脚本变动去重渲染自己的面板。"""

    def _on_change(changed: list[str]) -> None:
        stems = [s for sc in changed for s in ctx.registry.stems_of(sc)]
        sse_publish("panel.file_changed", {"scripts": changed, "stems": stems, "pj": ctx.id})

    return _on_change


def project_status(ctx: "ProjectCtx | None") -> dict:
    if ctx is None:
        return {"open": False}
    p = ctx.path
    return {
        "open": True,
        "id": ctx.id,
        "figures_dir": str(p),
        "name": p.name,
        "exists": p.is_dir(),
        "writable": os.access(p, os.W_OK),
        "scripts": len(ctx.registry.all_scripts()),
        "settings": engine_config.project_settings(str(p)),
        "export_dir": str(project_export_dir(ctx)),
        "backup_dir": str(project_backup_dir(ctx)),
    }


def open_project(path_str: str, make_default: bool = True) -> dict:
    """打开一个项目（已打开就直接复用），可选把它设为默认项目。

    多项目并存后这里**不再拆别的项目的台**：以前每次切换都
    stop_watcher + shutdown_all + interrupt_all，那会把另一个标签页正在用的
    渲染会话和 AI 任务一起打掉。worker 池自带 LRU 上限，内存不会失控。

    失败（目录不存在 / 注册表损坏）抛 RuntimeError，已打开的项目不受影响。
    """
    global DEFAULT_PROJECT
    path = Path(path_str).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"目录不存在: {path}")
    pid = _project_id(path)

    with _PROJECT_LOCK:
        existing = PROJECTS.get(pid)
    if existing is not None:
        if make_default:
            DEFAULT_PROJECT = pid
        engine_config.touch_recent(str(path))
        return {**project_status(existing), "drafted": False, "conflicts": [], "reused": True}

    drafted, conflicts = False, []
    try:
        reg = engine_registry.open_registry(path)
    except FileNotFoundError:
        cfg, rep = engine_discover.build_draft(path)
        engine_discover.write_config(path, cfg)
        reg = engine_registry.open_registry(path)
        drafted, conflicts = True, sorted(rep["conflicts"])
    ctx = ProjectCtx(path, pid, reg)
    # 刷新基线：现在的素材长什么样、注册表是哪一份。没有它，打开项目后的
    # 第一次刷新只能报「什么都没变」——而用户按刷新正是因为他刚在外面加了
    # 一张图（`engine/project_refresh.seed_state`）。
    engine_refresh.seed_state(ctx)
    with _PROJECT_LOCK:
        PROJECTS[pid] = ctx
        if make_default or DEFAULT_PROJECT is None:
            DEFAULT_PROJECT = pid
    engine_pool.start_watcher(str(path), reg.all_scripts(), _script_change_handler(ctx))
    engine_config.touch_recent(str(path))
    LOG.info(
        "项目已打开: %s（%d 个脚本%s）",
        path,
        len(reg.all_scripts()),
        "，注册表为静态扫描草稿" if drafted else "",
    )
    return {**project_status(ctx), "drafted": drafted, "conflicts": conflicts, "reused": False}


def close_project(pid: str, wait: bool = False) -> bool:
    """关闭一个项目：停它的 watcher、收它的 worker。别的项目一概不动。

    `wait=True` 用于进程即将退出的场合——关停跑在 daemon 线程里，父进程先走
    的话 worker 子进程会留在用户机器上。**必须在这里等**：等到 close_project
    返回时 worker 已经从池里摘走了，外层再调 shutdown_all(wait=True) 等的是
    一个空池子，等于没等（冒烟脚本的「残留 worker」断言当场抓到过）。
    """
    global DEFAULT_PROJECT
    with _PROJECT_LOCK:
        ctx = PROJECTS.pop(pid, None)
        if ctx is not None and DEFAULT_PROJECT == pid:
            DEFAULT_PROJECT = next(iter(PROJECTS), None)
    if ctx is None:
        return False
    engine_pool.stop_watcher(str(ctx.path))
    engine_pool.shutdown_all(str(ctx.path), wait=wait)
    LOG.info("项目已关闭: %s", ctx.path)
    return True


def default_project_path() -> Path | None:
    ctx = PROJECTS.get(DEFAULT_PROJECT or "")
    return ctx.path if ctx else None


def reset_projects(wait: bool = False) -> None:
    """关掉所有项目（进程收尾 / 测试隔离）：watcher 与 worker 一起收。

    `wait=True` 用于进程即将退出的场合——关停线程是 daemon，父进程先走的话
    worker 子进程会留在用户机器上。
    """
    global DEFAULT_PROJECT
    with _PROJECT_LOCK:
        ids = list(PROJECTS)
        DEFAULT_PROJECT = None
    for pid in ids:
        close_project(pid, wait=wait)
    # native 会话：**只放手，不杀**（ADR 0021 §8.1）。用户的脚本是他自己的
    # 进程，关掉 Tavotto 不该把它一起带走——runner 看到控制通道 EOF 会先把
    # Figure 恢复成脚本原样，再放开屏障让脚本跑完。这就是「关掉 App 默认
    # detach and continue」。
    engine_nativesession.REGISTRY.shutdown_all()
    if wait:
        engine_pool.shutdown_all(wait=True)  # 兜底：不属于任何项目的残留


def _refresh_sink(ctx: "ProjectCtx") -> engine_refresh.RefreshSink:
    """统一刷新服务的两个副作用出口：发 SSE、重挂 watcher。

    引擎侧不知道 SSE 长什么样，也不知道 watcher 回调要带哪个 pj——这两件事
    都是 app 层的。注入而不是 import 回来，是为了让 `engine/project_refresh.py`
    保持"能被 Flask 父进程安全 import 的纯标准库模块"。
    """
    return engine_refresh.RefreshSink(
        publish=sse_publish,
        watch=lambda scripts: engine_pool.start_watcher(
            str(ctx.path), list(scripts), _script_change_handler(ctx)
        ),
    )


def refresh_project(
    ctx: "ProjectCtx",
    *,
    reason: str,
    allow_static_merge: bool = True,
    changed_paths: list[str] | None = None,
    publish: bool = True,
) -> dict:
    """**app 层唯一的刷新入口**（手动刷新 / scan / probe / 手工登记 / 以后的
    watcher / Codex / AI 都走这里）。

    改造前这条链路在本文件里有三份：各自 reload、各自 `start_watcher`、各自
    发一条措辞不同的 `registry.changed`，而"哪些 worker 该作废"根本没人管。
    真正的编排在 `engine/project_refresh.refresh_project_index()`，这里只负责
    把 app 层的两个出口接上去——**别在别处再写第二条**（Prompt 05 的项目
    watcher 也调这个函数，不许自己 merge、自己发事件）。
    """
    return engine_refresh.refresh_project_index(
        ctx,
        reason=reason,
        changed_paths=changed_paths,
        allow_static_merge=allow_static_merge,
        publish=publish,
        sink=_refresh_sink(ctx),
    )


def project_store_dir(ctx: "ProjectCtx | None" = None) -> Path | None:
    """项目文件夹内的 `tavottofile/`：与该项目相关的 Tavotto 文件统一收纳处
    ——命名画布布局直接放里面，导出在 `export/`，布局版本历史在 `versions/`。
    用户在自己的项目目录里就能看到、随项目一起备份/同步/迁移。
    未打开项目时返回 None（调用方各自退回数据目录）。"""
    ctx = ctx if ctx is not None else _request_ctx()
    return None if ctx is None else engine_config.project_store_dir(ctx.path)


def project_export_dir(ctx: "ProjectCtx | None" = None) -> Path:
    """项目的导出目录（项目设置可覆盖）。

    规则本身在 `engine/config.project_export_dir()`——**Codex 插件的 MCP server
    也导出成图**，两条入口各写一份的话，用户会在两个地方找同一张图。
    这里只负责把请求上下文翻译成项目路径。"""
    ctx = ctx if ctx is not None else _request_ctx()
    return engine_config.project_export_dir(None if ctx is None else ctx.path, fallback=EXPORT_DIR)


def project_backup_dir(ctx: "ProjectCtx | None" = None) -> Path:
    """「写回原始文件」的备份根目录（项目设置可覆盖）。"""
    default = CACHE_DIR / "original_backups"
    ctx = ctx if ctx is not None else _request_ctx()
    if ctx is None:
        return default
    d = engine_config.project_settings(str(ctx.path)).get("backup_dir")
    return Path(d).expanduser() if d else default


@app.get("/api/diagnostics")
def api_diagnostics():
    """首次运行 / 排障诊断：worker Python、matplotlib、AI CLI、项目权限、
    注册表冲突。全部实测，不猜。"""
    import subprocess as sp

    checks: list[dict] = []

    try:
        py = engine_pool.find_worker_python()
        try:
            out = sp.run(
                [py, "-c", "import matplotlib; print(matplotlib.__version__)"],
                capture_output=True,
                text=True,
                # 显式 UTF-8：text=True 默认跟随系统区域编码（cp936），
                # 解释器路径带中文时一解码就炸。creationflags 见
                # engine/runtime.py——GUI 子系统进程不该弹控制台黑框。
                encoding="utf-8",
                errors="replace",
                timeout=30,
                stdin=sp.DEVNULL,
                creationflags=engine_runtime.CREATE_NO_WINDOW,
            )
            mpl = out.stdout.strip() or None
        except (OSError, sp.TimeoutExpired):
            mpl = None
        src = engine_pool.source_of(py)
        checks.append(
            {
                "id": "worker_python",
                "ok": True,
                "label": "渲染引擎 Python",
                "detail": f"{py}（{engine_pool.SOURCE_LABELS.get(src, src)}）",
            }
        )
        checks.append(
            {
                "id": "matplotlib",
                "ok": mpl is not None,
                "label": "matplotlib",
                "detail": mpl or "无法导入（渲染将不可用）",
            }
        )
    except engine_pool.WorkerError as exc:
        checks.append(
            {"id": "worker_python", "ok": False, "label": "渲染引擎 Python", "detail": str(exc)}
        )

    rt = engine_runtime.status()
    # 只在「本该有」或「确实有一套好的」时才报这一项。开发机上放着一份交叉
    # 构建出来的 Windows runtime（在 macOS 上当然跑不起来）不该被算成故障。
    if rt["valid"] or engine_runtime.ships_bundled_runtime():
        info = rt.get("manifest") or {}
        pkgs = info.get("packages") or {}
        checks.append(
            {
                "id": "bundled_runtime",
                "ok": rt["valid"],
                "label": "内置渲染环境",
                "detail": (
                    f"Python {(info.get('python') or {}).get('version')} + {len(pkgs)} 个包"
                    if rt["valid"]
                    else rt.get("error") or "缺失"
                ),
            }
        )

    caps = engine_ai.capabilities()
    for entry in caps["agents"]:
        checks.append(
            {
                "id": f"cli_{entry['id']}",
                "ok": entry["installed"],
                "label": f"{entry['display_name']} CLI",
                "detail": entry["version"] or "未安装（改图助手对应选项不可用）",
            }
        )

    ctx = _request_ctx()
    if ctx is not None:
        root = ctx.path
        checks.append(
            {
                "id": "project_readable",
                "ok": root.is_dir(),
                "label": "项目目录可读",
                "detail": str(root),
            }
        )
        checks.append(
            {
                "id": "project_writable",
                "ok": os.access(root, os.W_OK),
                "label": "项目目录可写（写回原始文件需要）",
                "detail": str(root),
            }
        )
        try:
            _cfg, rep = engine_discover.build_draft(root)
            n = len(rep.get("conflicts") or [])
            checks.append(
                {
                    "id": "registry_conflicts",
                    "ok": n == 0,
                    "label": "注册表 stem 归属",
                    "detail": "无冲突"
                    if n == 0
                    else f"{n} 个 stem 归属冲突: {', '.join(sorted(rep['conflicts']))}",
                }
            )
        except Exception as exc:  # noqa: BLE001 — 诊断本身不能炸
            checks.append(
                {
                    "id": "registry_conflicts",
                    "ok": False,
                    "label": "注册表 stem 归属",
                    "detail": str(exc),
                }
            )
    else:
        checks.append(
            {"id": "project_open", "ok": False, "label": "项目", "detail": "尚未打开项目"}
        )

    resp = jsonify({"checks": checks})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/diagnostics/bundle")
def api_diagnostics_bundle():
    """一键诊断包（zip）。密钥与个人路径已脱敏，见 engine/diagnostics.py。

    剩下那些没法提前覆盖的 bug，来回问十次才能定位一次；有了这个包，
    用户点一下发过来就够了。

    **GET 保持原样**（ADR 0016 §8 的兼容承诺）：出的包 schema 是 2，但
    `contains_frontend_state: false`——它拿不到前端状态，前端状态只活在
    浏览器内存里，得由前端在 POST 里现交上来。
    """
    return _diagnostics_bundle_response()


@app.post("/api/diagnostics/bundle")
def api_diagnostics_bundle_post():
    """带前端状态与交互轨迹的诊断包（ADR 0016）。

    请求体 `{frontend_state, interaction_trace}` 是前端在用户点「导出诊断包」
    那一刻现采的，**只存在于内存里**——Tavotto 不把交互轨迹写磁盘、不自动
    上传，它只在这一刻、因为用户按了那个按钮，才进一个 zip。

    前端已经按字段 allowlist 脱敏过一遍，这里**再校验一遍**
    （`engine/diagnostics_frontend`）。理由与 `/api/telemetry/event` 一致：
    这个端点接受的是请求体，而「结构性防线」的意思就是「就算调用方把整条
    路径塞进来也走不出这一步」。

    **任何形式的坏载荷都不该让用户拿不到诊断包**：超限、畸形 JSON、类型不对
    一律退化成「不带前端那两个文件的包」，并在 manifest 里记 truncated。
    用户是来排障的，不是来看 400 的。
    """
    frontend, dropped = _read_frontend_payload()
    return _diagnostics_bundle_response(frontend=frontend, frontend_dropped=dropped)


def _read_frontend_payload() -> tuple[dict | None, bool]:
    """请求体 → (载荷, 是不是被整份丢掉了)。**不抛异常**。

    **上限卡在读取本身，不卡 `Content-Length`**：chunked transfer encoding 的
    请求根本没有那个头，`request.content_length` 是 None，按 0 处理就等于把
    512 KB 的硬上限让开了——`get_json()` 会把任意大的 body 先缓冲再解析。
    所以这里自己按上限 +1 读流：多出来的那一个字节就是「超了」的判据，
    而且无论有没有 Content-Length 都成立。
    """
    limit = engine_diagnostics_frontend.MAX_REQUEST_BYTES
    try:
        raw = request.stream.read(limit + 1)
    except Exception:  # noqa: BLE001 — 读流失败不该 500
        return None, False
    if not raw:
        return None, False
    if len(raw) > limit:
        # 超限时**不解析**：前端环最多 240 条，走到这儿说明载荷不是我们发的，
        # 或者出了别的问题——不该为了它把整个请求的内存吃满
        app.logger.warning("诊断载荷超出上限（>%d 字节），本次只出环境诊断包", limit)
        return None, True
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:  # 畸形 JSON：退化，不是 400
        return None, True
    if not isinstance(body, dict):
        return None, True
    return body, False


def _diagnostics_bundle_response(frontend: dict | None = None, frontend_dropped: bool = False):
    ctx = _request_ctx()
    status = project_status(ctx)
    if ctx is not None:
        # 「为什么用了这个 Python」必须能在诊断包里读出来（ADR 0018 §诊断）：
        # 是自动接手还是用户挑的、因为缺哪个包、那个环境的版本是多少。
        # **这里不体检**——事实在切换当时就存下来了。
        st = engine_projectenv.state(str(ctx.path))
        try:
            # **按真正生效的那条判**，不是「记住过就算数」：用户在设置里显式
            # 指了别的解释器时，项目记住的那条并不生效，诊断包写成 project_venv
            # 就是在骗人。这一步与报告里既有的 `find_worker_python()` 同档
            # 开销（都可能探测一次），不额外拖慢什么。
            effective = engine_pool.resolve_worker_python(str(ctx.path))[1]
        except engine_pool.WorkerError:
            effective = ""
        status["environment_resolution"] = {
            "source": effective,
            "automatic": st.get("automatic", False),
            "trigger": st.get("trigger", ""),
            "module": st.get("module", ""),
            "python_version": st.get("python_version", ""),
            "matplotlib_version": st.get("matplotlib_version", ""),
            "support": st.get("support", ""),
            # 项目内的解释器只出**项目相对**路径：用户主目录名不该无谓地
            # 进诊断包，而相对路径已经足够定位问题。
            "executable": st.get("python_relative", ""),
        }
        # 受控依赖修复的账（ADR 0019 §诊断）：修过几轮、受管环境里装了什么。
        # **不含** index 地址、pip 配置、绝对路径——那三样是这一族功能里
        # 最容易顺手泄漏凭据的地方。
        status["dependency_repair"] = engine_deprepair.diagnostics_state(str(ctx.path))
    data = engine_diagnostics.build_bundle(
        project=status,
        port=request.host.rsplit(":", 1)[-1],
        frontend=frontend,
        frontend_dropped=frontend_dropped,
    )
    name = f"tavotto-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        data,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/project")
def api_project():
    """本标签页当前指向的项目（?pj= 决定；不带就是默认项目）。"""
    resp = jsonify(project_status(_request_ctx()))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/shutdown")
def api_shutdown():
    """受控退出（仅当环境变量 TAVOTTO_ALLOW_SHUTDOWN 打开时可用）。

    存在的理由只有一个：端到端冒烟要验证**干净退出**——关掉窗口之后
    worker 子进程必须一起收掉，不能在用户机器上留一堆僵尸 python.exe。
    默认关闭，免得本地应用平白多一个「任何网页都能把它关掉」的入口。
    """
    if not os.environ.get("TAVOTTO_ALLOW_SHUTDOWN"):
        abort(404)
    LOG.info("收到关闭请求，正在收尾")
    reset_projects(wait=True)  # 停 watcher + 关 worker（等它们真的收完）
    engine_ai.interrupt_all()  # AI 任务终止，快照保留

    def _bye():
        time.sleep(0.3)  # 先把响应送出去
        os._exit(0)

    threading.Thread(target=_bye, daemon=True, name="mm-shutdown").start()
    return jsonify({"ok": True})


@app.get("/api/projects")
def api_projects_open_list():
    """进程里打开着的全部项目——快速切换菜单据此标出「已打开」。"""
    with _PROJECT_LOCK:
        items = [project_status(c) for c in PROJECTS.values()]
    resp = jsonify({"projects": items, "default": DEFAULT_PROJECT})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/projects/recent")
def api_projects_recent():
    open_paths = {str(c.path): c.id for c in PROJECTS.values()}
    current = _request_ctx()
    entries = []
    for e in engine_config.recent_projects():
        p = Path(e["path"])
        entries.append(
            {
                **e,
                "exists": p.is_dir(),
                "id": open_paths.get(str(p)),
                "opened": str(p) in open_paths,
                "current": current is not None and p == current.path,
            }
        )
    resp = jsonify({"recent": entries})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/projects/open")
def api_projects_open():
    """打开（或 create=true 时先创建）一个项目目录。

    已经打开的项目直接复用，不会打断其它标签页；`default=false` 表示
    「只给本标签页用」，不改动新标签页的默认落点。
    """
    body = request.get_json(force=True)
    raw = str(body.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "缺少项目路径", "code": "missing_path"}), 400
    p = Path(raw).expanduser()
    if body.get("create"):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return jsonify(
                {
                    "error": f"无法创建目录: {exc}",
                    "code": "mkdir_failed",
                    "params": {"reason": str(exc)},
                }
            ), 400
    try:
        return jsonify(open_project(str(p), make_default=body.get("default", True)))
    except (RuntimeError, OSError) as exc:
        return jsonify(
            {"error": str(exc), "code": "open_project_failed", "params": {"reason": str(exc)}}
        ), 400


@app.post("/api/projects/close")
def api_projects_close():
    """关闭一个打开着的项目（收 watcher 与 worker）；不动磁盘内容。"""
    body = request.get_json(force=True)
    return jsonify({"ok": close_project(str(body.get("id") or ""))})


@app.post("/api/projects/remove")
def api_projects_remove():
    """从最近列表移除；绝不删除磁盘内容。"""
    body = request.get_json(force=True)
    return jsonify({"ok": engine_config.remove_recent(str(body.get("path") or ""))})


@app.post("/api/project/refresh")
def api_project_refresh():
    """显式刷新当前项目的派生事实：registry 合并重载 + 素材快照 + 结构化 diff。

    **不执行任何用户脚本**（共享规则 §4）：静态 merge 只读 AST，素材 inventory
    只 `stat()`。要跑脚本请走显式的 `/api/registry/probe`。

    请求体只有一个 `reason`，且只认闭集里的值（未知归成 `manual`）——它进
    日志、进事件、以后还会进遥测维度，透传客户端字符串等于让外面往我们的
    指标里写自由文本。**项目由现有的 pj 认证决定**（`current_ctx()`），
    不接受客户端传路径。
    """
    body = request.get_json(force=True, silent=True) or {}
    ctx = current_ctx()
    return jsonify(refresh_project(ctx, reason=str(body.get("reason") or "")))


def _drive_roots() -> list[dict]:
    """Windows 的盘符根。

    以前浏览器从 home 起步、只能往下钻，`C:\\` 的 parent 又是它自己——
    等于**永远走不到 D 盘**。把盘符做成一层虚拟根目录就通了。
    """
    if os.name != "nt":
        return [{"name": "/", "path": "/"}]
    roots = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d = f"{letter}:\\"
        try:
            if os.path.isdir(d):
                roots.append({"name": f"{letter}:", "path": d})
        except OSError:
            continue
    return roots


def _browse_shortcuts() -> list[dict]:
    """常用起点。桌面/文档只在真实存在时给出（非英文系统上未必叫这个名字）。"""
    home = Path.home()
    out = [{"name": "主目录", "path": str(home)}]
    for label, name in (("桌面", "Desktop"), ("文档", "Documents"), ("下载", "Downloads")):
        p = home / name
        if p.is_dir():
            out.append({"name": label, "path": str(p)})
    return out


@app.get("/api/projects/browse")
def api_projects_browse():
    """服务器端目录列举（本地单用户应用的目录选择器；只列目录）。

    `path=@roots` 列驱动器/根，让 Windows 能横跨盘符；路径可由用户直接输入，
    所以不存在时要给出**最近的存在祖先**，别让人对着一句报错干瞪眼。
    """
    raw = (request.args.get("path") or "").strip()
    roots = _drive_roots()
    if raw in ("@roots", "@drives"):
        return jsonify(
            {
                "path": "@roots",
                "parent": None,
                "is_roots": True,
                "dirs": roots,
                "roots": roots,
                "shortcuts": _browse_shortcuts(),
            }
        )
    if not raw:
        raw = str(Path.home())
    try:
        p = Path(raw).expanduser()
        p = p.resolve() if p.exists() else Path(os.path.abspath(str(p)))
    except (OSError, ValueError):
        return jsonify({"error": "路径无效", "code": "invalid_path"}), 400
    if not p.is_dir():
        # 找一个还存在的祖先，前端可以一键跳过去继续找
        near = p
        while near != near.parent and not near.is_dir():
            near = near.parent
        return jsonify(
            {
                "error": f"目录不存在: {p}",
                "code": "dir_missing",
                "params": {"path": str(p)},
                "nearest": str(near) if near.is_dir() else None,
            }
        ), 400
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if not child.is_dir():
                    continue
            except OSError:  # 断开的网络驱动器 / 权限受限的符号链接
                continue
            dirs.append({"name": child.name, "path": str(child)})
    except PermissionError:
        return jsonify(
            {"error": f"无权限读取: {p}", "code": "permission_denied", "params": {"path": str(p)}}
        ), 403
    except OSError as exc:
        return jsonify(
            {"error": f"无法读取: {exc}", "code": "read_failed", "params": {"reason": str(exc)}}
        ), 400
    # 盘符根的上一级是「此电脑」那一层虚拟根，不是它自己
    parent = str(p.parent) if p != p.parent else ("@roots" if os.name == "nt" else None)
    return jsonify(
        {
            "path": str(p),
            "parent": parent,
            "is_roots": False,
            "dirs": dirs,
            "roots": roots,
            "shortcuts": _browse_shortcuts(),
            "writable": os.access(p, os.W_OK),
        }
    )


# ------------------------- 脚本注册表 ---------------------------------------
# 「面板上没有 ⚡」几乎总是注册表的问题，而以前它只能靠手改 JSON 解决。
# 这三个端点把整条链路搬到界面上：看现状 → 重扫 → 跑一遍认领。
@app.get("/api/registry")
def api_registry():
    """当前注册表 + 静态扫描报告（谁已登记、谁在存图却没登记、有无冲突）。"""
    ctx = current_ctx()
    reg = ctx.registry.entries()
    try:
        rep = engine_discover.discover(ctx.path)
    except OSError as exc:
        return jsonify(
            {"error": f"扫描失败: {exc}", "code": "scan_failed", "params": {"reason": str(exc)}}
        ), 400
    registered_stems = {s for c in reg.values() for s in c["stems"]}
    candidates = []
    for script, info in sorted(rep["scripts"].items()):
        fresh = [s for s in info["stems"] if s not in registered_stems]
        # 已登记且没有新产物就不再列为「未登记」——包括那些静态解不出文件名的
        # 脚本（它们已经靠试运行登记过了，再列一遍只会自相矛盾）。需要重新
        # 探测时从「已登记」那一栏走。
        if script in reg and not fresh:
            continue
        candidates.append(
            {"script": script, **info, "new_stems": fresh, "registered": script in reg}
        )
    return jsonify(
        {
            "source": ctx.registry.source(),
            "scripts": reg,
            "candidates": candidates,
            "conflicts": rep["conflicts"],
            # 项目内**全部**合理 .py（含 show-only 与基础设施脚本，
            # 各带稳定 reason code）：普通脚本不因静态分析解不出
            # 产物就从产品里消失，任意一条都可试运行。
            "all_scripts": engine_probe.script_inventory(ctx.path, registered=set(reg)),
        }
    )


@app.post("/api/registry/scan")
def api_registry_scan():
    """重跑静态扫描并合并进 tavotto_registry.json（现有条目永远优先）。

    内核就是统一刷新服务（`reason="registry"`）：这个端点只负责把结果翻译回
    它一直以来的那三个字段。**旧响应逐字保留**——RegistryDialog 读的是
    `changes.added_scripts.length`，换个形状等于让存量前端当场坏掉；新的
    结构化 diff 挂在 `refresh` 里另给。
    """
    ctx = current_ctx()
    result = refresh_project(ctx, reason="registry")
    return jsonify(
        {
            "changes": result["merge"],
            "conflicts": result["registry"]["conflicts"] or {},
            "scripts": ctx.registry.entries(),
            "refresh": result,
        }
    )


# 在跑的试运行：同一 (项目, 脚本) 同时只允许一个（素材库与 RegistryDialog
# 双入口并发点同一脚本时后端兜底）。value 是取消 Event——cancel 端点置位并
# 硬杀 worker，probe 循环据此把失败归类为 execution_cancelled 且不再试下一个
# entry。key 用 ctx.id（项目身份），与 SSE 的 pj 同一口径。
_PROBES: dict[tuple[str, str], threading.Event] = {}
_PROBES_LOCK = threading.Lock()


@app.post("/api/registry/probe")
def api_registry_probe():
    """试运行一个脚本，按**真实产出**的文件名登记 stem。

    静态解不出文件名的脚本（stem 来自数据目录 / 命令行）只有这条路。
    脚本跑得起来 = 能参数化，不用再让用户手改 JSON 猜自己该写什么。
    同步阻塞：冷启动秒级到分钟级；取消走 POST /api/registry/probe/cancel
    （置取消标志 + 硬杀该脚本的 worker 会话，本请求随即以
    execution_cancelled 返回）。SSE `probe.started` 在执行真正开始前发出
    （前端状态机的 starting_runtime → running 边界）。
    """
    ctx = current_ctx()
    body = request.get_json(force=True)
    raw = str(body.get("script") or "").strip()
    # 只允许跑项目目录内的 .py：这个端点会真的执行代码，越权必须挡死。
    # 三种拒绝各有稳定 code（前端按码换文案）；判据一律在 **realpath 之后**
    # ——`..` 回溯、symlink/junction 指到项目外、项目外绝对路径都在 resolve
    # 那一步现出原形，逐条模式匹配防不完。
    if not raw:
        return jsonify(
            {"error": f"脚本不存在: {raw}", "code": "script_not_found", "params": {"script": raw}}
        ), 404
    root = ctx.path.resolve()
    try:
        target = (Path(raw) if Path(raw).is_absolute() else ctx.path / raw).resolve()
    except OSError:
        return jsonify(
            {"error": f"脚本不存在: {raw}", "code": "script_not_found", "params": {"script": raw}}
        ), 404
    if not target.is_relative_to(root):
        return jsonify(
            {
                "error": f"脚本路径在项目目录之外: {raw}",
                "code": "script_path_outside_project",
                "params": {"script": raw},
            }
        ), 400
    if target.suffix.lower() != ".py" or target.is_dir():
        return jsonify(
            {
                "error": f"不是可试运行的 .py 脚本: {raw}",
                "code": "unsupported_script_type",
                "params": {"script": raw},
            }
        ), 400
    if not target.is_file():
        return jsonify(
            {"error": f"脚本不存在: {raw}", "code": "script_not_found", "params": {"script": raw}}
        ), 404
    # 注册表键 = 项目相对路径（POSIX）——与清单 / 静态起草同一种写法
    script = target.relative_to(root).as_posix()
    key = (ctx.id, script)
    cancel_ev = threading.Event()
    with _PROBES_LOCK:
        if key in _PROBES:
            return jsonify(
                {
                    "error": f"该脚本已有一次试运行在进行中: {script}",
                    "code": "probe_in_progress",
                    "params": {"script": script},
                }
            ), 409
        _PROBES[key] = cancel_ev
    sse_publish("probe.started", {"pj": ctx.id, "script": script})
    try:
        result = engine_probe.probe_and_register(
            ctx.path, script, cost=str(body.get("cost") or "medium"), should_cancel=cancel_ev.is_set
        )
    finally:
        with _PROBES_LOCK:
            _PROBES.pop(key, None)
    if result.get("registered"):
        # 每张捕获图当场物化进 runtime cache（复制热 worker 已写好的预览
        # SVG + 描述符——不触发第二次执行）。重开文档时的首帧占位靠它。
        # **在刷新之前**：刷新会发 `registry.changed`，前端收到就去取 runtime
        # 清单与预览，那时 cache 里得已经有东西。
        _materialize_runtime(script, result.get("entry") or "", result.get("descriptors") or [])
        # probe 已经把结果写进注册表了（`probe.probe_and_register` → `discover.register`），
        # 这里只要重装 + 发事件：再跑一遍静态扫描既慢，又会把刚刚按**真实产出**
        # 裁决好的归属重新掀一遍。
        refresh_project(ctx, reason="probe", allow_static_merge=False)
    return jsonify(result)


@app.post("/api/registry/probe/cancel")
def api_registry_probe_cancel():
    """取消一个在跑的试运行：置取消标志并**当场硬杀**该脚本的 worker 会话。

    「取消」必须真正终止工作（Session 5 反证 #3）：只置标志的话，阻塞在
    build 里的慢脚本会一直跑到超时。`pool.force_cancel` 直接 kill 子进程，
    阻塞中的 probe 请求随即拿到 EOF → execution_cancelled。幂等：没有在跑
    的返回 `{cancelling: false}`——「取消」与「跑完」天然赛跑，输了不是错误
    （跑完的照常登记，probe_and_register 的注释写明了这条语义）。
    """
    ctx = current_ctx()
    body = request.get_json(force=True)
    script = str(body.get("script") or "").strip()
    with _PROBES_LOCK:
        ev = _PROBES.get((ctx.id, script))
    if ev is None:
        return jsonify({"cancelling": False})
    ev.set()  # 先置标志再杀：probe 醒来时答案已经在了
    engine_pool.force_cancel(script, str(ctx.path))
    return jsonify({"cancelling": True})


# ------------------------- Runtime Figure 素材（ADR 0013） -------------------
@app.get("/api/runtime/assets")
def api_runtime_assets():
    """素材库「图」区的 RuntimeFigureAsset 清单（**只读，绝不执行脚本**）。

    清单语义在 `runtimeasset.list_assets`（唯一实现）：注册表里磁盘无原件
    的 (script, stem) 各成一条；有原件的是 FileAsset，归 /api/panels。
    """
    ctx = current_ctx()
    return jsonify(
        {
            "assets": engine_runtimeasset.list_assets(
                ctx.path, ctx.registry, worker_python=engine_pool.find_worker_python
            )
        }
    )


@app.post("/api/runtime/status")
def api_runtime_status():
    """一个 runtime 素材的 stale 状态与 cache 可用性。

    请求体：`{id, source?}`。`source` 是文档里持久化的描述块（{script, stem,
    …}），注册表条目已被删除时用它兜底——但 fail closed：重算出的 asset id
    必须与请求的一致，对不上按未知处理（绝不把 override 套到猜出来的脚本）。
    **这个端点只读磁盘与注册表，绝不执行脚本**（lazy 生命周期的看护点）。
    """
    ctx = current_ctx()
    body = request.get_json(force=True)
    rel_id = str(body.get("id") or "")
    if not engine_runtimeasset.is_runtime_id(rel_id):
        return jsonify(
            {
                "error": f"不是 runtime 素材 id: {rel_id}",
                "code": "runtime_asset_unknown",
                "params": {"id": rel_id},
            }
        ), 400
    source = body.get("source") if isinstance(body.get("source"), dict) else None
    status = engine_runtimeasset.stale_status(
        ctx.path, rel_id, ctx.registry, source=source, worker_python=engine_pool.find_worker_python
    )
    if status["status"] is None:
        return _runtime_asset_unknown(rel_id)
    # **上一次这张图是怎么产生的**（ADR 0021 §9）。界面靠它在重开文档时就说得出
    # 「这张图来自一条 native 会话」——而不是等用户点进图内编辑、撞上一条 409
    # 才知道。判据与渲染路由同一个出处（`enginesession.profile_of`），不另立
    # 一份：两份判据迟早会在某个边角上分叉，而分叉的那一侧会显示成"能编辑"。
    return jsonify(
        {
            "id": rel_id,
            "execution_profile": engine_enginesession.profile_of(ctx.path, rel_id),
            **status,
        }
    )


@app.get("/api/runtime/preview")
def api_runtime_preview():
    """materialized cache 里的预览 SVG（重开文档的首帧占位）。

    cache 是可删除可重建的派生物：404 只表示「还没物化 / 已被清理」，
    前端据此显示占位并等 lazy build——不是错误路径，别当错误弹出来。
    """
    ctx = current_ctx()
    rel_id = request.args.get("id", "")
    if not engine_runtimeasset.is_runtime_id(rel_id):
        return jsonify(
            {
                "error": f"不是 runtime 素材 id: {rel_id}",
                "code": "runtime_asset_unknown",
                "params": {"id": rel_id},
            }
        ), 400
    path = engine_runtimeasset.preview_path(ctx.path, rel_id)
    if path is None:
        return jsonify(
            {
                "error": "该运行时素材尚未物化预览（重新运行后生成）",
                "code": "runtime_cache_missing",
                "params": {"id": rel_id},
            }
        ), 404
    resp = send_file(path, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.put("/api/registry")
def api_registry_write():
    """手工裁决：直接写一条脚本的 stem 归属（冲突仲裁、改 entry/cost）。"""
    ctx = current_ctx()
    body = request.get_json(force=True)
    script = str(body.get("script") or "").strip()
    if not script:
        return jsonify({"error": "缺少脚本名", "code": "script_name_missing"}), 400
    entry = str(body.get("entry") or "main")
    if not engine_registry.valid_entry(entry):
        return jsonify(
            {"error": f"entry 非法: {entry}", "code": "invalid_entry", "params": {"entry": entry}}
        ), 400
    stems = [str(s).strip() for s in (body.get("stems") or []) if str(s).strip()]
    try:
        engine_discover.register(
            ctx.path,
            script,
            stems,
            entry=entry,
            cost=str(body.get("cost") or "medium"),
            notes=str(body.get("notes") or ""),
        )
    except (OSError, RuntimeError) as exc:
        return jsonify(
            {"error": str(exc), "code": "registry_update_failed", "params": {"reason": str(exc)}}
        ), 400
    # 手工裁决就是权威，写完只要重装 + 发事件；再跑一遍静态扫描会把用户刚
    # 裁决完的归属重新掀一遍（那正是他点这个按钮要解决的事）。
    refresh_project(ctx, reason="registry", allow_static_merge=False)
    return jsonify({"scripts": ctx.registry.entries()})


@app.patch("/api/project/settings")
def api_project_settings():
    """项目设置：导出目录 / 备份目录 / 写回权限。空字符串 = 恢复默认。"""
    root = require_project()
    body = request.get_json(force=True)
    patch: dict = {}
    for key in ("export_dir", "backup_dir"):
        if key in body:
            val = str(body[key] or "").strip()
            if val:
                d = Path(val).expanduser()
                try:
                    d.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    return jsonify(
                        {
                            "error": f"{key} 不可用: {exc}",
                            "code": "settings_dir_unusable",
                            "params": {"key": key, "reason": str(exc)},
                        }
                    ), 400
                patch[key] = str(d)
            else:
                patch[key] = None
    if "allow_write_back" in body:
        patch["allow_write_back"] = bool(body["allow_write_back"])
    merged = engine_config.set_project_settings(str(root), patch)
    return jsonify(
        {
            "settings": merged,
            **{
                "export_dir": str(project_export_dir()),
                "backup_dir": str(project_backup_dir()),
            },
        }
    )


# ------------------------- 参数化渲染引擎 ----------------------------------
def _runtime_asset_unknown(rel_id: str):
    """runtime fileId 在注册表里解析不到时的统一 404 响应体。"""
    resp = jsonify(
        {
            "error": f"运行时素材未登记（脚本注册表里找不到它）: {rel_id}",
            "code": "runtime_asset_unknown",
            "params": {"id": rel_id},
        }
    )
    resp.status_code = 404
    return resp


# --------------------- Tavotto Run · Beta（native 会话）----------------------
# ADR 0021。进程关系是**倒过来**的：用户的 Python 是 `tavotto run` CLI 的子进程，
# sidecar 只是通过一条认证 relay 连上去。这里全部是**控制面**，一个字节的
# Figure 都不经过（Figure 永远留在用户那个进程里）。
#
# 前端能提交的只有 `native_id`——host / port / token / interpreter / 完整命令
# 一律来自那份 0600 的 descriptor 文件，不来自请求体（ADR 0021 §4.1）。

#: 稳定码 → HTTP 状态。**不把所有失败都 400**：调用方靠状态码分诊
#: "没有这个东西"（404）与"有，但现在不能"（409）。
_NATIVE_STATUS = {
    engine_runcodes.NATIVE_HANDOFF_INVALID: 404,
    engine_runcodes.NATIVE_SESSION_UNKNOWN: 404,
    engine_runcodes.NATIVE_HANDOFF_EXPIRED: 409,
    engine_runcodes.NATIVE_HANDOFF_CONSUMED: 409,
    engine_runcodes.NATIVE_ATTACH_CANCELLED: 409,
    engine_runcodes.NATIVE_SESSION_CONFLICT: 409,
    engine_runcodes.NATIVE_ASSET_CONFLICT: 409,
    engine_runcodes.NATIVE_SESSION_NOT_AT_BARRIER: 409,
    engine_runcodes.NATIVE_SESSION_OFFLINE: 409,
    engine_runcodes.NATIVE_SESSION_ENDED: 409,
    engine_runcodes.NATIVE_SESSION_DISCONNECTED: 409,
    engine_runcodes.NATIVE_AUTH_FAILED: 403,
    engine_runcodes.NATIVE_ATTACH_FAILED: 502,
    engine_runcodes.NATIVE_RELAY_FAILED: 502,
}


def _native_error(exc: "engine_runcodes.RunError"):
    resp = jsonify(exc.payload())
    resp.status_code = _NATIVE_STATUS.get(exc.code, 400)
    return resp


def _native_session_event(session, entry: dict) -> None:
    """会话状态变了 → SSE。**在 reader 线程里跑**，所以只 put 一条消息。

    带上 `pj`（项目短 id）与 `sequence`：另一个项目的标签页要能把不属于自己
    的事件丢掉，而迟到的事件要能按序号判出"这条比我手里的旧"（前端代际
    纪律，与 `panel.file_changed` 同一条）。
    """
    try:
        pj = _project_id(Path(session.project_root))
    except (OSError, ValueError):  # 项目目录已经不在了：事件照发，pj 留空
        pj = ""
    sse_publish(
        "native.session",
        {"pj": pj, "session": session.public_state(), "event": entry},
    )


engine_nativesession.REGISTRY.on_change = _native_session_event


@app.get("/api/native/pending/<native_id>")
def api_native_pending(native_id: str):
    """待确认的一条 native 交接。**返回的 metadata 里没有 token、没有端口。**"""
    try:
        meta = engine_nativehandoff.sanitized(native_id)
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    meta["remembered"] = engine_nativeperm.is_remembered(
        meta.get("project_root", ""), meta.get("permission_key", "")
    )
    return jsonify({"ok": True, "pending": meta})


@app.post("/api/native/pending/<native_id>/approve")
def api_native_approve(native_id: str):
    """用户点了"运行并连接"。

    **这一步之后 CLI 才会 spawn 用户的 Python**（ADR 0021 §7）：attach 成功
    是 CLI 那边"可以开跑了"的信号。所以确认之前一行用户代码都没跑。

    请求体里只认 `remember`（布尔）。interpreter / target / host / port 一律
    从 descriptor 文件读——界面确认的是哪条 invocation，执行端就只能执行那条。
    """
    body = request.get_json(silent=True) or {}
    try:
        descriptor = engine_nativehandoff.consume(native_id)
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    meta = descriptor.get("metadata") or {}
    try:
        session = engine_nativesession.REGISTRY.attach(descriptor)
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    except engine_pool.EnvironmentBusy as exc:
        resp = jsonify({"ok": False, "code": exc.code, "error": str(exc)})
        resp.status_code = 409
        return resp
    if body.get("remember") is True:
        engine_nativeperm.remember(
            meta.get("project_root", ""),
            meta.get("permission_key", ""),
            interpreter=meta.get("interpreter", ""),
        )
    return jsonify({"ok": True, "session": session.public_state()})


@app.post("/api/native/pending/<native_id>/cancel")
def api_native_cancel(native_id: str):
    """用户点了"取消"。CLI 正盯着这份 descriptor，会当场收摊并退出 3。"""
    try:
        engine_nativehandoff.cancel(native_id)
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    return jsonify({"ok": True, "cancelled": True})


@app.get("/api/native/sessions")
def api_native_sessions():
    project = request.args.get("project_root", "")
    sessions = engine_nativesession.REGISTRY.list(project)
    return jsonify({"ok": True, "sessions": [s.public_state() for s in sessions]})


@app.get("/api/native/sessions/<session_id>")
def api_native_session(session_id: str):
    try:
        session = engine_nativesession.REGISTRY.get(session_id)
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    return jsonify({"ok": True, "session": session.public_state()})


@app.post("/api/native/sessions/<session_id>/build")
def api_native_session_build(session_id: str):
    """在屏障处 build 一次：拿到 stems / descriptors，并绑定 live route。

    **由界面显式调**，不在收到 barrier 事件时自动发：那条事件是在 reader
    线程里收到的，而 build 的响应要由**同一个** reader 读回来——在那里发请求
    就是自己等自己（ADR 0021 §5.2）。
    """
    try:
        session = engine_nativesession.REGISTRY.get(session_id)
        resp = session.ensure_built()
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    except engine_pool.WorkerError as exc:
        # **必须带状态码**：`_worker_error_payload()` 回的是裸 dict，Flask 会把它
        # 序列化成 **HTTP 200**——而调用方（前端 `jsonFetch`）按状态码判成败，
        # 于是一次 bridge 失败会被当成成功，然后去读一个不存在的 `session`。
        # 用户看到的是**第二个**错误，真正的原因被盖掉了。同一个文件里另外 7 处
        # `_worker_error_payload` 全是 `, 500`；这两处是漏的（issue #191）。
        return jsonify(_worker_error_payload(exc)), 500
    rejected = engine_nativesession.REGISTRY.bind_assets(session)
    _materialize_native(session)
    out = {
        "ok": True,
        "session": session.public_state(),
        "stems": resp.get("stems", {}),
        "descriptors": session.descriptors,
    }
    if rejected:
        # **如实说**：这些 stem 已经被另一条还活着的会话占着（用户在两个终端
        # 跑了同一个脚本）。静默抢过来的表现是他在界面上看到的图突然换成了
        # 另一次运行的，而界面什么都没说（ADR 0021 §9.2）。
        out["conflicts"] = {"code": engine_runcodes.NATIVE_ASSET_CONFLICT, "stems": rejected}
    return jsonify(out)


def _native_action(session_id: str, action: str):
    try:
        session = engine_nativesession.REGISTRY.get(session_id)
        result = getattr(session, action)()
    except engine_runcodes.RunError as exc:
        return _native_error(exc)
    except engine_pool.WorkerError as exc:
        # **必须带状态码**：`_worker_error_payload()` 回的是裸 dict，Flask 会把它
        # 序列化成 **HTTP 200**——而调用方（前端 `jsonFetch`）按状态码判成败，
        # 于是一次 bridge 失败会被当成成功，然后去读一个不存在的 `session`。
        # 用户看到的是**第二个**错误，真正的原因被盖掉了。同一个文件里另外 7 处
        # `_worker_error_payload` 全是 `, 500`；这两处是漏的（issue #191）。
        return jsonify(_worker_error_payload(exc)), 500
    return jsonify({"ok": True, "result": result, "session": session.public_state()})


@app.post("/api/native/sessions/<session_id>/continue")
def api_native_continue(session_id: str):
    """继续运行脚本。**runner 会先把 Figure 恢复成脚本原样**（ADR 0021 §8）。"""
    return _native_action(session_id, "resume")


@app.post("/api/native/sessions/<session_id>/detach")
def api_native_detach(session_id: str):
    """放手：脚本继续正常跑完，Tavotto 不再控制它。**不杀进程。**"""
    return _native_action(session_id, "detach")


@app.post("/api/native/sessions/<session_id>/terminate")
def api_native_terminate(session_id: str):
    """结束用户脚本——**明确的危险操作**，退出码固定 5，不伪装成 continue。

    只在屏障处可用。脚本正在跑的时候没有人读控制通道，而那时真正该做的是
    用户在自己的终端里按 Ctrl+C：那个进程是他的，信号也是他的。
    """
    return _native_action(session_id, "terminate")


@app.get("/api/native/permissions")
def api_native_permissions():
    root = request.args.get("project_root", "") or str(require_project())
    return jsonify({"ok": True, "permissions": engine_nativeperm.listing(root)})


@app.delete("/api/native/permissions")
def api_native_forget_permission():
    body = request.get_json(silent=True) or {}
    root = body.get("project_root") or str(require_project())
    removed = engine_nativeperm.forget(root, body.get("permission_key", "") or "")
    return jsonify({"ok": True, "removed": removed})


def _materialize_native(session) -> None:
    """把 native 会话这一轮的预览物化进 runtime cache（**last known preview**）。

    它不是 live Figure，也不是原始产物：会话结束之后这份 cache 仍然看得见，
    但对象级编辑与权威导出一律不可用（ADR 0021 §9.4）。cache 里有东西
    **不等于** live session 还在——那条判据只有 `route_for()` 说了算。
    """
    root = session.project_root
    for desc in session.descriptors or []:
        if not isinstance(desc, dict):
            continue
        stem = desc.get("stem")
        if not stem:
            continue
        engine_runtimeasset.materialize(root, desc, session.svg_path(stem))


def _materialize_runtime(script: str, entry: str, descriptors: list) -> None:
    """把一次成功 build 捕获的每张图物化进 runtime cache（失败只记日志）。

    SVG 从热 worker 的 out 目录拿——build 阶段本来就写好了，这里只是复制，
    **绝不触发第二次执行**（probe 的 execution-count 纪律，Session 3 约束）。
    """
    if not script or not entry:
        return
    try:
        worker = _safe_worker(script, entry)
    except engine_pool.WorkerError:
        return
    root = require_project()
    for desc in descriptors or []:
        if not isinstance(desc, dict):
            continue
        stem = desc.get("stem")
        if not stem:
            continue
        engine_runtimeasset.materialize(root, desc, worker.svg_path(stem))


def _switched_to_project_env(worker, exc) -> bool:
    """内置环境缺依赖时替**这个项目**切到它自己的 `.venv`；切成了回 True。

    切成之后**调用方必须重新取一次 worker**：旧会话是内置解释器起的，
    `pool.get()` 会因为「渲染解释器已变」把它换掉（ADR 0018 的 worker 身份
    纪律）。切不成时把结构化原因挂回异常，`_worker_error_payload` 会带给前端。

    「值不值得为这个错误换环境」的判据只有一份
    （`pool.should_try_project_env`）——这里绝不再写一遍 `exc.code == …`。
    """
    if not engine_pool.should_try_project_env(exc):
        return False
    try:
        root = str(require_project())
    except NoProjectError:
        return False
    outcome = engine_pool.try_project_env(root, worker.script_name, getattr(exc, "module", ""))
    if outcome.get("ok"):
        # 记在**请求作用域**里：渲染端点据此在响应里带一句「已自动使用这个
        # 项目的 Python 环境」，前端给一条轻量 toast。用户不该被一个阻断式
        # 对话框拦住去读一段他没要求的技术说明——但也不能完全看不见。
        g.environment_switched = {
            "source": engine_pool.SOURCE_PROJECT_VENV,
            "python": _project_relative(outcome.get("python", "")),
            "module": outcome.get("module", ""),
        }
        return True
    exc.project_env = outcome
    return False


def _engine_attempt(rel_id: str, worker, stem: str, action):
    """`action(worker, stem)`；缺依赖时切项目环境**重试一次**。

    回 `(worker, stem, 结果)`——重试后 worker 换成了新解释器起的那个，调用方
    后续要拿 `rev` / `last_build_descriptors` 的话必须用回传的这一个。
    """
    try:
        return worker, stem, action(worker, stem)
    except engine_pool.WorkerError as exc:
        if not _switched_to_project_env(worker, exc):
            raise
    worker, stem = _engine_worker(rel_id)
    return worker, stem, action(worker, stem)


def _safe_worker(script: str, entry: str, stem: str = ""):
    """**磁盘面板永远是 safe**——它有自己的原始产物，那是 safe worker 产出的
    世界（写回、画布合成、两图同步走的都是这条）。

    仍然经 `enginesession.resolve()` 而不是直接 `pool.get()`：不是为了在这里
    产生分支，而是为了让"谁来渲染"这个判断在整份 `app.py` 里**只有一扇门**。
    绕过去的那几处正是"共享判据修了一处、第二个消费点还是老样子"的形状，
    仓库里同形状的缺陷出现过三次。
    """
    return engine_enginesession.resolve(
        project_root=str(require_project()),
        script=script,
        entry=entry,
        stem=stem,
        execution_profile=engine_enginesession.PROFILE_SAFE,
    )


def _engine_worker(rel_id: str):
    """面板 id → (worker-like, stem)；非脚本面板 404。

    runtime 素材（`runtime:` 前缀，ADR 0013）不经 safe_resolve——它没有磁盘
    原件。解析走注册表正向重算（`runtimeasset.resolve`，不反解 id），解析
    不到回 404 + 稳定 code。冷启动的 build 由后续的 override/export 惰性
    触发（与磁盘面板同一 lazy 语义），这里不主动 build。

    **"谁来渲染"的判据只有一处**：`enginesession.resolve()`（ADR 0021 §5）。
    这里绝不写 `if 有 native 会话 … else pool.get(…)`——那个形状会在下一个
    端点上被漏掉一次，表现是"预览是 native 的、导出是 safe 的"。
    """
    root = str(require_project())
    if engine_runtimeasset.is_runtime_id(rel_id):
        info = engine_runtimeasset.resolve(rel_id, current_registry())
        if info is None:
            abort(_runtime_asset_unknown(rel_id))
        return (
            engine_enginesession.resolve(
                project_root=root,
                script=info["script"],
                entry=info["entry"],
                stem=info["stem"],
                execution_profile=engine_enginesession.profile_of(root, rel_id),
            ),
            info["stem"],
        )
    path = safe_resolve(rel_id)
    info = current_registry().for_stem(path.stem)
    if info is None:
        abort(404)
    # 磁盘面板永远是 safe：它有自己的原始产物，那是 safe worker 产出的世界。
    return (
        engine_enginesession.resolve(
            project_root=root,
            script=info["script"],
            entry=info["entry"],
            stem=path.stem,
            execution_profile=engine_enginesession.PROFILE_SAFE,
        ),
        path.stem,
    )


@app.post("/api/engine/render")
def api_engine_render():
    """应用全量 override 列表并重渲染，返回新 manifest 与版本号。

    首次调用会触发脚本 build（fig9 数秒；heavy 脚本 Phase 1 处理异步化）。

    可选 `preview_dpi`：本次预览 SVG 里**嵌入位图**的分辨率（缺省是 worker 的
    `--preview-dpi`）。纯矢量图上它毫无影响（实测 72→300 耗时与体积完全相同），
    含 imshow 的面板上 200→100 能把这条 render 的 canvas_draw 砍掉近一半、
    SVG 体积降到四分之一。前端只在**含图像元素的面板**的连续调整期间发它
    （松手即回默认 dpi），见 docs/perf-baseline.md 与 web 侧 useEngineSync。

    可选 `inline_svg`：响应里一并带上本次的预览 SVG 文本。前端一律发它——
    `/api/engine/svg` 是第二跳 GET，读的是磁盘上那一份，另一个变体/标签页的
    渲染插进来就会与本次 manifest 错配（元素框对不上图）。端点保留兼容，
    但新代码不要再用两跳。

    响应恒带 `preview`（ADR 0022）：这一版该用哪种预览表示法。**要了
    `inline_svg` 却没有 `svg`** 不是错误——`preview.mode == "raster"` 时
    worker 按硬闸决定不把那份 SVG 读进内存，manifest / warnings / timings /
    rev 一样不少。老 worker 不返回它时字段整个不出现。
    """
    body = request.get_json(force=True)
    rel_id = body.get("id", "")
    inline_svg = bool(body.get("inline_svg"))
    # 参数校验先做完再进渲染：混在下面那个 try 里的话，worker 响应的
    # JSONDecodeError（也是 ValueError）会被当成「preview_dpi 写错了」
    raw_dpi = body.get("preview_dpi")
    try:
        # `is not None` 而不是真值判断：显式发了 0 是写错了，不是「没给」
        preview_dpi = int(raw_dpi) if raw_dpi is not None else None
    except (TypeError, ValueError):
        return jsonify(
            {
                "error": f"preview_dpi 必须是整数: {raw_dpi!r}",
                "code": "invalid_preview_dpi",
                "params": {"value": repr(raw_dpi)},
            }
        ), 400
    if preview_dpi is not None and preview_dpi <= 0:
        return jsonify(
            {
                "error": f"preview_dpi 必须为正: {preview_dpi}",
                "code": "invalid_preview_dpi",
                "params": {"value": str(preview_dpi)},
            }
        ), 400
    t0 = time.time()
    t_get = time.perf_counter()
    worker, stem = _engine_worker(rel_id)
    # 取会话可能当场 spawn 一个解释器并 import matplotlib——冷启动的大头常常
    # 在这里，而它**既不在 worker 的 timings 里也不在 build 里**。不单独量出来，
    # 用户等的那十几秒在数据里就凭空消失了（第一版计时管道就是这么骗了自己）。
    get_ms = round((time.perf_counter() - t_get) * 1000, 3)
    info = current_registry().for_stem(Path(stem).stem) or {}
    cold = not worker.built
    # 三个事件都得带 pj：前端 renderStore 按 fileId 索引且不分项目，不带的话
    # 另一个标签页里同名的面板（到处都是的 Fig1.pdf）会跟着显示「正在构建…」
    pj = current_ctx().id
    sse_publish(
        "render.started", {"pj": pj, "id": rel_id, "cost": info.get("cost", ""), "cold": cold}
    )
    try:
        # 冷启动的第一次 build 就发生在这里（lazy 语义：打开面板不预跑脚本）。
        # 内置环境缺依赖时自动改用项目自己的 .venv 重试一次（ADR 0018）。
        worker, stem, resp = _engine_attempt(
            rel_id,
            worker,
            stem,
            lambda wk, st: wk.override(
                st, body.get("patches", []), preview_dpi, inline_svg=inline_svg
            ),
        )
    except engine_pool.WorkerError as exc:
        LOG.error("引擎渲染失败: %s: %s", stem, exc)
        sse_publish("render.failed", {"pj": pj, "id": rel_id, "error": str(exc)})
        return jsonify(_worker_error_payload(exc)), 500
    # 阶段计时：worker 的 script_build/patch_apply/manifest/canvas_draw +
    # 控制面的 queue_wait/total。日志里一行结构化（可 grep 可喂脚本），响应里
    # 原样交给前端——「慢」这件事必须能指到具体某一段上，不能靠猜。
    timings = {**(resp.get("timings") or {}), "worker_get_ms": get_ms}
    LOG.info(
        "引擎渲染: %s %.0fms%s timings=%s",
        stem,
        (time.time() - t0) * 1000,
        "（冷启动）" if cold else "",
        json.dumps(timings, sort_keys=True),
    )
    sse_publish("render.done", {"pj": pj, "id": rel_id, "rev": worker.rev})
    if engine_runtimeasset.is_runtime_id(rel_id):
        # 重开文档时的首帧占位从这里来：刷新 materialized cache 的预览与
        # metadata（描述符取自本会话 build 响应，只复制文件、不二次执行）。
        _materialize_runtime(
            info.get("script", ""), info.get("entry", ""), worker.last_build_descriptors
        )
    out = {
        "rev": worker.rev,
        "manifest": resp["manifest"],
        "warnings": resp.get("warnings", []),
        "timings": timings,
    }
    # 没要就不加这个字段（响应形状对老调用方一字不变）
    if inline_svg and "svg" in resp:
        out["svg"] = resp["svg"]
    # 这一版预览该用什么画法（ADR 0022）。**要了 inline_svg 却没拿到 svg**
    # 的正常形态就在这里：`preview.mode == "raster"` 表示 worker 按硬闸决定
    # 不读那份 SVG（不是渲染失败），前端据此走位图编辑预览。
    # 老 worker 不返回 `preview` 时这个字段整个不出现，前端维持既有行为。
    if "preview" in resp:
        out["preview"] = resp["preview"]
    switched = g.pop("environment_switched", None)
    if switched:
        # 同上：加字段不改老形状。只在**真的发生了自动切换**的那一次响应里出现。
        out["environment_switched"] = switched
    return jsonify(out)


@app.post("/api/engine/preview_png")
def api_engine_preview_png():
    """**按给定 patches** 出高清位图（bucket 宽度），不依赖热会话当前是哪个变体。

    与 `/api/engine/png` 的区别就是这一条：那个端点从 live figure 直接 savefig，
    而 live 状态永远只是「最后渲染的那个变体」。画布上放两个同文件不同 override
    的面板时，后渲染的那个会把像素喂给前一个——用户看到的是「一个面板显示了
    另一个面板的图」。这里走 worker 的 `preview_png`（应用 patches → 出图 →
    还原，状态中立），每个变体各拿各的。

    落盘文件名带 patches 哈希前 12 位：同一 stem 的多个变体、多个标签页并发
    取图时不会互相覆盖对方的临时文件。哈希前缀（`sha256:`）**必须去掉**——
    冒号在 Windows 上不是合法文件名字符。
    """
    body = request.get_json(force=True)
    worker, stem = _engine_worker(body.get("id", ""))
    patches = body.get("patches", [])
    if not isinstance(patches, list):
        return jsonify({"error": "patches 必须是数组", "code": "invalid_patches"}), 400
    try:
        want_w = int(body.get("w", 800))
    except (TypeError, ValueError):
        return jsonify(
            {
                "error": f"w 必须是整数: {body.get('w')!r}",
                "code": "invalid_width",
                "params": {"value": repr(body.get("w"))},
            }
        ), 400
    w = next((b for b in RENDER_BUCKETS if b >= want_w), RENDER_BUCKETS[-1])
    tag = "v" + engine_patchspec.patch_hash(patches).split(":")[-1][:12]
    try:
        worker, stem, path = _engine_attempt(
            body.get("id", ""), worker, stem, lambda wk, st: wk.preview_png(st, patches, w, tag=tag)
        )
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    resp = send_file(path, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/engine/png")
def api_engine_png():
    """当前 override 状态下的高清位图（bucket 宽度）——含 imshow 的面板显示不糊。

    **状态相关**：从 live figure 直接出图，拿到的永远是最后渲染的那个变体。
    同文件多变体的场景请改用 `/api/engine/preview_png`（前端已全部改过去），
    这个端点只为兼容保留。
    """
    worker, stem = _engine_worker(request.args.get("id", ""))
    want_w = int(request.args.get("w", 800))
    w = next((b for b in RENDER_BUCKETS if b >= want_w), RENDER_BUCKETS[-1])
    try:
        worker, stem, path = _engine_attempt(
            request.args.get("id", ""), worker, stem, lambda wk, st: wk.render_png(st, w)
        )
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    resp = send_file(path, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"  # rev 参数负责客户端缓存节流
    return resp


@app.post("/api/engine/update_source")
def api_engine_update_source():
    """把当前图内修改**写回原始单图文件**（figures 目录里的 stem.pdf / stem.png）。

    原文件先备份到 cache/original_backups/<时间戳>/；替换为原子操作。
    只覆盖图片文件，脚本不动——脚本与文件从此不再一一对应，但 override
    仍是文档里的真相（工具内渲染始终走脚本 + overrides）。
    """
    if err := _write_back_forbidden():
        return err
    body = request.get_json(force=True)
    rel_id = body.get("id", "")
    patches = body.get("patches", [])
    # 可选：随写回把画布标注烙进原图（坐标已由前端换算成该图自身的 mm）
    annotations = [
        a
        for a in (body.get("annotations") or [])
        if isinstance(a, dict) and a.get("type") in ("text", "arrow", "shape")
    ]
    if engine_runtimeasset.is_runtime_id(rel_id):
        # runtime 素材没有可写回的原件——后端**硬拒绝**，不是藏按钮
        # （ADR 0013 §7；code 与 runtimeasset.writeback_rejection 对拍看护）。
        # savefig 来源且磁盘上确有产物的那些，写回走它的 FileAsset 身份，
        # 那条路的事务防线一条不少。
        return jsonify(
            {
                "error": "运行时素材没有原始图文件，无法写回"
                "（磁盘上有同名产物时请从素材库的那一份写回）",
                "code": "runtime_asset_has_no_original_artifact",
                "params": {"id": rel_id},
            }
        ), 400
    src = safe_resolve(rel_id)
    if annotations and not src.with_suffix(".pdf").exists():
        return jsonify(
            {
                "error": "该素材只有位图、没有矢量 PDF，暂不支持把标注写回原图",
                "code": "annotations_need_pdf",
            }
        ), 400
    info = current_registry().for_stem(src.stem)
    if info is None:
        return jsonify(
            {"error": "该面板不可参数化（没有对应脚本）", "code": "not_parameterizable"}
        ), 404
    worker = _safe_worker(info["script"], info["entry"], src.stem)
    try:
        result = _write_source_files(
            src, patches, worker, annotations=annotations, expected_mtime=body.get("expected_mtime")
        )
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    except (
        SourceChangedError,
        ScriptChangedError,
        ReplayDivergenceError,
        WriteBackVerifyError,
        FileLockedError,
    ) as exc:
        return _write_back_error_response(exc)
    # 把这组修改追加为该图的版本历史，末位即当前基线：
    # 新拖入的同名面板自动继承，双击进编辑态能接着改
    append_baked(src.stem, patches)
    return jsonify(_write_back_response(result, baked=bool(patches)))


def _write_back_forbidden():
    """项目被设为只读时拒绝一切「写回原始文件」类操作；返回错误响应或 None。"""
    st = engine_config.project_settings(str(require_project()))
    if st.get("allow_write_back") is False:
        return jsonify(
            {
                "error": "该项目已设为只读：不允许写回原始文件（可在项目设置中恢复可写）",
                "code": "write_back_disabled",
            }
        ), 403
    return None


class FileLockedError(RuntimeError):
    """目标文件被别的程序占用，替换不了（Windows 上的独占锁）。

    第 2 个目标撞锁时**先把已经换掉的从备份恢复回去**：一张图的 PDF 与 PNG
    分岔（矢量是新的、位图还是旧的）比整件事失败糟糕得多——用户在画布上看到
    的是位图，投出去的是矢量。恢复成功 = 原文件一个字节都没变；恢复也失败
    （备份也被锁）才退回「部分完成」的如实报告。
    """

    def __init__(
        self,
        name: str,
        detail: str,
        updated: list[str],
        rolled_back: list[str] | None = None,
        rollback_failed: list[str] | None = None,
    ):
        self.rolled_back = list(rolled_back or [])
        self.rollback_failed = list(rollback_failed or [])
        tail = ""
        if self.rolled_back and not self.rollback_failed:
            tail = f"（已回滚 {'、'.join(self.rolled_back)}，原文件未变）"
        elif self.rollback_failed:
            tail = f"（{'、'.join(self.rollback_failed)} 回滚失败，仍是新内容）"
        super().__init__(f"{name} 正被其它程序占用，无法覆盖：{detail}{tail}")
        self.name = name
        #: 仍处于「已被换掉」状态的文件（回滚成功后为空）
        self.updated = updated


class SourceChangedError(RuntimeError):
    """写回目标在用户按下确认之后被外部改过（mtime 与前端手里的对不上）。

    典型场景：AI 桥改了脚本重出、另一个标签页刚写回过同一张图、用户自己在
    别的工具里存了一次。这时候按旧状态覆盖 = 悄悄吃掉别人的改动。
    """

    def __init__(self, name: str, expected: int, actual: int):
        super().__init__(f"{name} 已被外部修改")
        self.name = name
        self.expected = expected
        self.actual = actual


class ScriptChangedError(RuntimeError):
    """生成这张图的脚本，在当前会话 spawn 之后被改过。

    热会话里跑的还是旧代码，mtime watcher 有 2 秒轮询窗口——那个窗口里写回，
    落盘的是旧脚本的产物，而下一次渲染就会变成新脚本的样子，两者再也对不上。
    """

    def __init__(self, script: str, expected: str, actual: str):
        super().__init__(f"脚本 {script} 已改动")
        self.script = script
        self.expected = expected
        self.actual = actual


class ReplayDivergenceError(RuntimeError):
    """热会话的 manifest 与「全新 worker 全量重放」的对不上。

    这是 FigS3 那一类问题（热态所见 ≠ 重开后重放）的最后一道防线：写回把热态
    的样子刻进用户原件，而重开项目后引擎会按 patches 全量重放——两者不一致，
    用户下次打开就会看到一张与文件里不同的图，且无从判断哪一份才是对的。
    """

    def __init__(self, diffs: list[dict]):
        head = "；".join(f"{d['gid'] or 'figure'}.{d['field']}" for d in diffs[:3])
        more = f"（另有 {len(diffs) - 3} 处）" if len(diffs) > 3 else ""
        super().__init__(f"{head}{more}")
        self.diffs = diffs


class WriteBackVerifyError(RuntimeError):
    """写回前自检不通过：worker 报了 warning，这组 patches 没有全部落到图上。

    warning 的来源是 `overrides.apply`——「元素不存在（脚本可能已改动）」、
    「属性不支持」、「应用失败 / 还原失败」。写回是**覆盖用户原始文件**的一步，
    这时候半对的图比报错糟糕得多：用户看到的画布是带这条修改的，写进 PDF 的
    却不是，而原文件已经被换掉了，事后根本对不出来。所以一条 warning 就阻断。
    """

    def __init__(self, warnings: list[str]):
        head = "；".join(warnings[:3])
        more = f"（另有 {len(warnings) - 3} 条）" if len(warnings) > 3 else ""
        super().__init__(f"{head}{more}")
        self.warnings = warnings


def _write_back_warning_error(exc: "WriteBackVerifyError") -> str:
    return (
        f"写回前自检未通过，原文件未做任何修改：{exc}。"
        "这些元素/属性没能应用到图上——通常是脚本改过了（元素的 gid 变了或"
        "已删除）。请重新渲染确认当前效果，或撤销对应的修改后再写回。"
    )


# ---- 写回事务：prepare → verify → commit ------------------------------------
#: 干净重放与热态 manifest 的几何容差（figure 分数坐标，bbox/anchor 逐项）。
#: 0.5% 是「同一张图两次独立渲染的浮点噪声」与「真的挪位了」之间的分界：
#: 文字错位那类事故的偏移量是百分之几到几十，噪声在 1e-6 量级。
REPLAY_GEOM_TOL = 0.005
#: 画布尺寸容差（mm）。size_mm 由 figsize 直接算出，两次重放该逐位相同。
REPLAY_SIZE_TOL = 0.01
#: 落盘后页面尺寸自检的容差（mm）——这一档只看「有没有整体错档」。
POST_CHECK_SIZE_TOL = 0.5
#: 分歧清单最多回多少条（够定位问题，又不至于把响应撑成一页 JSON）。
REPLAY_DIFF_LIMIT = 20
#: 像素门探针的目标像素宽（热态与重放各出一张，逐 RGBA 通道比差异）。
#: 3×2 英寸的图在这一档约 66 万像素——线型 / 字形级的差异足够显影，
#: 两张探针图合计也只要几十毫秒，相对整个写回（重跑一遍脚本）可以忽略。
REPLAY_PIXEL_WIDTH = 1000
#: 像素门阈值（三指标任一越界即分歧，语义见 scripts/ci/pixelcompare.py）。
#: 通过态的基线是**逐字节相同**——热态与重放出自同一台机器、同一个解释器、
#: 同一版 matplotlib，同一 dpi 的两次 savefig 本就该逐位一致（不变式套件
#: 每天在拿字节相等断言 preview_png）。这里的余量只为万一的解码 / 抗锯齿
#: 抖动兜底，并刻意远低于最小的真实信号（虚线化一条曲线 ≈ 0.2% 变化像素，
#: 改色 / 改透明度更大）。比 CompatBench 的跨版本保真阈值（0.004 / 1.2 /
#: 140）严一个量级是有意的：那边比的是两个 matplotlib 世界，这边比的是
#: 同一个世界里的同一张图。
REPLAY_PIXEL_TOL = {
    "changed_pixel_ratio": 0.001,
    "mean_abs_diff": 0.5,
    "max_abs_diff": 64,
}


def _f(value) -> float | None:
    """manifest 里的数值统一成 float。

    manifest 是经 JSON 落盘的，worker 的 `default=` 兜底可能把 numpy 标量写成
    字符串（`float()` 失败时它回 `str(o)`）——不统一化的话「0.5」与 0.5 会被
    判成分歧，把一条真防线变成天天误报的噪音。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vec(value, n: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != n:
        return None
    out = [_f(v) for v in value]
    return None if any(v is None for v in out) else out  # type: ignore[return-value]


def _manifest_hash(man: dict) -> str:
    """manifest 的内容指纹（canonical JSON 的 sha256，带算法前缀）。

    与 `patchspec.canonical_json` 同一口径（sort_keys + 紧凑分隔符 +
    ensure_ascii=False），这样「哪一版 patches」与「重放出的哪一份 manifest」
    是两个能对上的、可复现的值。
    """
    text = json.dumps(man, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compare_manifests(hot: dict, fresh: dict) -> tuple[list[dict], int]:
    """热态 manifest vs 干净重放 manifest → (分歧清单, 逐项比过的元素数)。

    比 gid 集合、每个元素的 bbox/anchor、以及 figure 的 size_mm。只比几何：
    **位置**是热会话增量应用与全量重放最容易分岔的地方（FigS3 事故就是这条）。
    颜色 / 线型 / 字体样式 / 透明度这类几何不变的纯属性差异这把尺子量不到
    （PR #49 的 bar_series.facecolor 就逃过它了）——那一层归
    `_replay_pixel_diff` 的像素门（issue #81），两道一起才是完整的 verify。
    """
    diffs: list[dict] = []
    hot_size, fresh_size = _vec(hot.get("size_mm"), 2), _vec(fresh.get("size_mm"), 2)
    if hot_size is None or fresh_size is None:
        if hot.get("size_mm") != fresh.get("size_mm"):
            diffs.append(
                {
                    "gid": "",
                    "field": "size_mm",
                    "hot": hot.get("size_mm"),
                    "fresh": fresh.get("size_mm"),
                }
            )
    elif any(abs(a - b) > REPLAY_SIZE_TOL for a, b in zip(hot_size, fresh_size)):
        diffs.append({"gid": "", "field": "size_mm", "hot": hot_size, "fresh": fresh_size})

    hot_els = {el.get("gid"): el for el in hot.get("elements", []) if el.get("gid")}
    fresh_els = {el.get("gid"): el for el in fresh.get("elements", []) if el.get("gid")}
    for gid in sorted(set(hot_els) - set(fresh_els)):
        diffs.append({"gid": gid, "field": "missing_in_replay", "hot": "存在", "fresh": None})
    for gid in sorted(set(fresh_els) - set(hot_els)):
        diffs.append({"gid": gid, "field": "missing_in_hot", "hot": None, "fresh": "存在"})

    common = sorted(set(hot_els) & set(fresh_els))
    for gid in common:
        for field, n in (("bbox", 4), ("anchor", 2)):
            a, b = hot_els[gid].get(field), fresh_els[gid].get(field)
            if a is None and b is None:
                continue
            va, vb = _vec(a, n), _vec(b, n)
            if va is None or vb is None:
                if a != b:
                    diffs.append({"gid": gid, "field": field, "hot": a, "fresh": b})
            elif any(abs(x - y) > REPLAY_GEOM_TOL for x, y in zip(va, vb)):
                diffs.append({"gid": gid, "field": field, "hot": va, "fresh": vb})
    return diffs, len(common)


def _replay_pixel_diff(worker, fresh, stem: str) -> dict | None:
    """写回像素门：热态当前的样子 vs 干净重放，逐像素比对（issue #81）。

    `_compare_manifests` 只量几何——颜色 / 线型 / 字体样式 / 透明度 / hatch /
    marker 这类**几何不变的纯属性差异**它一项都量不到，而它们同样会把「用户
    看到的」与「写进原件的」变成两张图（PR #49 的 facecolor 恢复顺序 bug 报了
    0 处分歧）。像素是这些属性最终兑现的地方，也是唯一不用逐属性枚举、连
    manifest 没暴露的属性都逃不掉的一把尺。

    两侧都用协议既有的 `render_png`（画热会话 / 重放会话**当前**的 live
    figure，不重新 apply）：热侧渲染的正是「用户此刻所见」，重放侧在
    `fresh.override(stem, patches)` 之后就是「重开项目后重放出来的」。同一台
    机器、同一个解释器、同一版 matplotlib、同一 dpi——通过态本就该逐字节
    相同，字节相同直接放行；不同才解码按 `REPLAY_PIXEL_TOL` 三指标裁决，
    底噪与容差只为解码 / 抗锯齿抖动兜底，不给真实属性差异留活口。

    只在 `_hot_manifest` 判定「热态最后应用的正是这组 patches」之后调用——
    与 manifest 比对同一条前提：热态压根不是这组 patches 时（历史恢复、跨面板
    同步），比出来的差异全是假的，那时如实回 `fresh_only`，不装比过。
    探针渲染失败一律**让异常冒出去**（写回失败、原件零改动）：查不了 ≠ 查过，
    静默降级会把这道门慢慢变成空转的门禁。

    返回 (分歧项 | None, 状态)：状态回填进 `verification["pixels"]`——
    `"ok"` = 比过且一致；`"hot_rebuilt"` = 热会话在探针中途被 workerd 透明
    重开（`unknown_session` → `_open()` → 重试），它此刻画的是脚本原样、
    不再是「用户所见」的热态基准，本次像素比对**作废但如实报告**。误报一次
    分歧，用户学到的就是「这个提示可以无视」。
    """
    hot_png = Path(worker.render_png(stem, REPLAY_PIXEL_WIDTH))
    if not getattr(worker, "built", True):
        # 透明重开把 built 置回了 False（且不重放 patches）：探针画的是原样
        LOG.warning("写回像素门：热会话在探针中途被重开，本次像素比对作废: %s", stem)
        return None, "hot_rebuilt"
    fresh_png = Path(fresh.render_png(stem, REPLAY_PIXEL_WIDTH))
    if hot_png.read_bytes() == fresh_png.read_bytes():
        return None, "ok"
    metrics = pdfbackend.compare_png(hot_png, fresh_png)
    exceeded = {
        k: metrics.get(k)
        for k, tol in REPLAY_PIXEL_TOL.items()
        if _f(metrics.get(k)) is not None and _f(metrics.get(k)) > tol
    }
    if metrics.get("ok", False) and not exceeded:
        return None, "ok"  # 抖动在底噪 / 容差之内：不算分歧
    LOG.warning("写回像素门发现分歧: %s %s（阈值 %s）", stem, metrics, REPLAY_PIXEL_TOL)
    return (
        {
            "gid": "",
            "field": "pixels",
            "hot": "热态渲染",
            "fresh": "全量重放",
            "metrics": metrics,
            "exceeded": exceeded,
            "tolerance": dict(REPLAY_PIXEL_TOL),
        },
        "diverged",
    )


def _write_back_prepare(src: Path, worker, expected_mtime) -> None:
    """写回前置校验：目标文件没被外部改过、脚本没在会话背后换过。

    `expected_mtime` 比对的是**请求点名的那个素材**（`src`）：它是前端 assetStore
    里唯一有 mtime 的那一份；同 stem 的另一载体（PDF↔PNG）客户端并不持有它的
    mtime，服务端也就无从判定「用户看到的是哪一版」，硬拿同一个数去比只会必然
    误报。缺省（旧前端）跳过整条检查。
    """
    if expected_mtime is not None:
        try:
            actual = int(src.stat().st_mtime)
        except OSError:
            actual = 0
        if actual != int(expected_mtime):
            raise SourceChangedError(src.name, int(expected_mtime), actual)

    figures_dir = getattr(worker, "figures_dir", "")
    script_name = getattr(worker, "script_name", "")
    spawned = getattr(worker, "script_sha1", "")
    if not (figures_dir and script_name and spawned):
        return  # 会话没记指纹（读不到脚本）：没有可比的基准，不臆断
    now = engine_pool.script_sha1(figures_dir, script_name)
    if now and now != spawned:
        raise ScriptChangedError(script_name, spawned, now)


def _hot_manifest(worker, stem: str, patches: list) -> dict | None:
    """热会话当前的 manifest；拿不到（或热态压根不是这组 patches）回 None。

    只有热会话最后应用的正是**这一组** patches 时，两份 manifest 才可比。
    历史版本恢复、跨面板同步这类入口写回的是会话里没应用过的 patches，那时候
    比出来的差异全是假的——报一次假的 `replay_divergence`，用户学到的是
    「这个提示可以无视」，真出事那天它就不再是防线了。
    """
    want = engine_patchspec.patch_hash(patches)
    # 按 **stem** 问，不是按 worker 问：一个脚本可以登记多个 stem，它们共用
    # 同一条会话（见 `pool.stem_patch_hash` 的说明）。
    if engine_pool.stem_patch_hash(worker, stem) != want:
        return None
    path = Path(worker.out_dir) / f"{stem}.json"
    if not path.exists():
        try:
            worker.override(stem, patches)  # 同一组 patches，幂等
        except engine_pool.WorkerError:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rollback(done: list[Path], backup_dir: Path) -> tuple[list[str], list[str]]:
    """把已经替换掉的目标从本次备份恢复回去，返回 (成功, 失败) 两份文件名。"""
    rolled, failed = [], []
    for target in done:
        try:
            shutil.copy2(backup_dir / target.name, target)
            rolled.append(target.name)
        except OSError:
            LOG.error("写回回滚失败: %s（备份在 %s）", target.name, backup_dir, exc_info=True)
            failed.append(target.name)
    return rolled, failed


def _write_source_files(
    src: Path, patches: list, worker, annotations: list | None = None, expected_mtime=None
) -> dict:
    """写回事务：prepare → verify → commit，任一环不过就保持原文件零改动。

    **staging 的 PDF/PNG 由一个全新的一次性 worker 产出，不用热会话。**
    热会话是增量的：build 之后经历过任意多次 override 与还原，它「现在的样子」
    未必等于「按这组 patches 从零重放一次的样子」（FigS3 事故就是这个差）。
    写回是把某一版刻进用户原件，那就必须刻**可复现的那一版**——重开项目后引擎
    重放出来的，正是干净重放这一版。代价是每次写回都要重跑一遍脚本（heavy 的
    分钟级），这是正确性优先的自觉取舍，不许为省时间跳过。同一次写回只 build
    一次：override + PDF + PNG 全共用这一个 worker。

    annotations 非空时（写回携带画布标注，坐标为该图自身的 mm），先在导出的
    PDF 上矢量绘制标注，PNG 再由注好的 PDF 重新栅格化——两种载体逐像素同源。

    替换失败要当成一等公民处理：Windows 上文件被 Acrobat / 看图工具打开时
    是**独占锁**，`replace` 直接抛 PermissionError。不接住的话用户会拿到一个
    500 加一串 traceback，图库里还留下一个 `.Fig1.pdf.updating` 垃圾文件，
    下次再看见它完全不知道是什么。

    **staging 阶段（导出 + 标注 + 自检）任何异常都要清干净临时文件**：
    以前只有「文件被占用」那一条路径清理，PDF 导出成功而 PNG 导出抛
    WorkerError 时，`.Fig1.pdf.updating` 就永久留在图库里了。
    """
    stem = src.stem
    _write_back_prepare(src, worker, expected_mtime)

    targets = [p for p in (src.with_suffix(".pdf"), src.with_suffix(".png")) if p.exists()]
    man_hot = _hot_manifest(worker, stem, patches)

    # ---- verify：全新 worker 全量重放，staging 也从它出 ----------------------
    fresh = engine_pool.one_shot(worker.script_name, worker.figures_dir, worker.entry)
    tmps: list[tuple[Path, Path]] = []
    warnings: list[str] = []
    try:
        resp = fresh.override(stem, patches)
        for w in resp.get("warnings") or []:
            if w not in warnings:
                warnings.append(str(w))
        man_fresh = json.loads((Path(fresh.out_dir) / f"{stem}.json").read_text(encoding="utf-8"))
        pixel_state = None
        if man_hot is None:
            diffs, compared = [], 0
        else:
            diffs, compared = _compare_manifests(man_hot, man_fresh)
            if not diffs:
                # 几何这把尺子过了，再过像素门：颜色 / 线型 / 字体 / 透明度
                # 这类几何不变的纯属性分歧只有像素量得到（issue #81）
                pixel_diff, pixel_state = _replay_pixel_diff(worker, fresh, stem)
                if pixel_diff is not None:
                    diffs.append(pixel_diff)

        for target in targets:
            tmp = target.with_name(f".{target.name}.updating")
            tmps.append((target, tmp))  # 先登记再导出：中途抛了也要清得掉
            eresp = fresh.export(stem, patches, str(tmp), fmt=target.suffix.lstrip("."), dpi=600)
            for w in eresp.get("warnings") or []:
                if w not in warnings:  # PDF/PNG 两次导出报的是同一批
                    warnings.append(str(w))
        if annotations:
            pdf_tmp = next((t for tg, t in tmps if tg.suffix == ".pdf"), None)
            png_tmp = next((t for tg, t in tmps if tg.suffix == ".png"), None)
            assert pdf_tmp is not None  # 端点已拦过「只有 PNG」的素材
            pdfbackend.annotate_asset(pdf_tmp, png_tmp, annotations, dpi=600)
        if warnings:
            raise WriteBackVerifyError(warnings)
        if diffs:
            raise ReplayDivergenceError(diffs[:REPLAY_DIFF_LIMIT])
    except BaseException:
        for _t, leftover in tmps:
            leftover.unlink(missing_ok=True)
        raise
    finally:
        engine_pool.discard(fresh)

    # ---- commit：备份 → 逐个原子替换（中途撞锁则回滚） ----------------------
    backup_dir = project_backup_dir() / time.strftime("%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    updated: list[str] = []
    done: list[Path] = []
    for target, tmp in tmps:
        shutil.copy2(target, backup_dir / target.name)
        try:
            tmp.replace(target)
        except OSError as exc:
            for _t, leftover in tmps:
                leftover.unlink(missing_ok=True)  # 不给图库留下半成品
            LOG.warning("写回原图失败（文件被占用？）: %s: %s", target.name, exc)
            rolled, failed = _rollback(done, backup_dir)
            raise FileLockedError(target.name, str(exc), failed, rolled, failed) from exc
        done.append(target)
        updated.append(target.name)
    prune_backups(backup_dir.parent)
    LOG.info(
        "更新原图: %s → %s（备份 %s，标注 %d 条）",
        stem,
        updated,
        backup_dir.name,
        len(annotations or []),
    )

    verification = {
        "replay": "ok" if man_hot is not None else "fresh_only",
        "elements": compared,
    }
    if man_hot is None:
        # 没比 ≠ 没验：staging 本来就出自干净重放，只是没有可对照的热态基准
        verification["reason"] = "hot_state_differs"
    elif pixel_state is not None:
        # 能走到这儿 = 几何与像素两道门都过了（任一分歧早已抛出）；
        # "hot_rebuilt" = 像素门没比成（热会话中途被重开），如实报告
        verification["pixels"] = pixel_state
    return {
        "updated": updated,
        "backup_dir": backup_dir,
        "patch_hash": engine_patchspec.patch_hash(patches),
        "source_sha1": {t.name: _sha1_of(t) for t in done},
        "manifest_hash": _manifest_hash(man_fresh),
        "verification": verification,
        "post_check": _post_check_size(done, man_fresh),
    }


def _post_check_size(done: list[Path], man_fresh: dict) -> str:
    """落盘后自检：写回的 PDF 页面尺寸对不对得上重放 manifest 的 size_mm。

    对不上**不回滚**——文件已经换掉了，再动一次只会让状态更难解释；备份还在，
    如实报告（响应 + ERROR 日志）就是这里能给的最有用的东西。
    """
    pdf = next((t for t in done if t.suffix.lower() == ".pdf"), None)
    want = _vec(man_fresh.get("size_mm"), 2)
    if pdf is None or want is None:
        return ""
    try:
        info = pdfbackend.probe_asset(pdf, "pdf")
        got = [info["w_pt"] * 25.4 / 72.0, info["h_pt"] * 25.4 / 72.0]
    except Exception:  # noqa: BLE001 — 自检读不动不算失败
        LOG.warning("写回后尺寸自检读不出 PDF: %s", pdf, exc_info=True)
        return ""
    if any(abs(a - b) > POST_CHECK_SIZE_TOL for a, b in zip(got, want)):
        LOG.error(
            "写回后尺寸自检不符: %s 实际 %.2f×%.2fmm，manifest %.2f×%.2fmm",
            pdf.name,
            got[0],
            got[1],
            want[0],
            want[1],
        )
        return "size_mismatch"
    return ""


def _write_back_response(result: dict, **extra) -> dict:
    """写回成功响应（update_source 与 history/restore 同构）。"""
    body = {
        "updated": result["updated"],
        "backup_dir": str(result["backup_dir"]),
        # warnings 恒为空（有一条就走 409），但字段必须在：前端据此确认
        # 「这次写回确实是全量应用的」，而不是靠「没报错」推断
        "warnings": [],
        "patch_hash": result["patch_hash"],
        "source_sha1": result["source_sha1"],
        "manifest_hash": result["manifest_hash"],
        "verification": result["verification"],
        **extra,
    }
    if result.get("post_check"):
        body["post_check"] = result["post_check"]
    return body


def _write_back_error_response(exc):
    """三种 prepare/verify 失败 → 409 + 专属 code；不认识的回 None。"""
    if isinstance(exc, SourceChangedError):
        return jsonify(
            {
                "error": f"{exc.name} 已被外部修改（本工具之外），写回已取消，"
                "原文件未做任何改动。请刷新素材面板后重新确认再写回。",
                "code": "source_changed",
                "file": exc.name,
                "expected": exc.expected,
                "actual": exc.actual,
            }
        ), 409
    if isinstance(exc, ScriptChangedError):
        return jsonify(
            {
                "error": f"生成这张图的脚本 {exc.script} 在本次会话开始后被改动过，"
                "当前渲染的仍是旧代码，写回已取消（原文件未做任何改动）。"
                "请重新渲染该面板确认效果后再写回。",
                "code": "script_changed",
                "script": exc.script,
            }
        ), 409
    if isinstance(exc, ReplayDivergenceError):
        return jsonify(
            {
                "error": "热编辑状态与全新重放不一致，写回已阻断，原文件未做任何改动。"
                f"分歧：{exc}。这属于引擎级问题，请把此信息报告给开发者。",
                "code": "replay_divergence",
                "diffs": exc.diffs,
            }
        ), 409
    if isinstance(exc, WriteBackVerifyError):
        return jsonify(
            {
                "error": _write_back_warning_error(exc),
                "code": "write_back_warnings",
                "warnings": exc.warnings,
            }
        ), 409
    if isinstance(exc, FileLockedError):
        # 可操作的错误：告诉用户是哪个文件、该去关掉谁；回滚结果一并报出来，
        # 免得用户以为「什么都没发生」或者反过来以为「已经写进去了」
        return jsonify(
            {
                "error": f"{exc}。请关闭正在打开它的程序（PDF 阅读器 / 看图工具）后重试。",
                "code": "file_locked",
                "file": exc.name,
                "updated": exc.updated,
                "rolled_back": exc.rolled_back,
                "rollback_failed": exc.rollback_failed,
            }
        ), 409
    return None


# ---- 组图 ↔ 子图 override 同步 ----------------------------------------------
_SYNC_SKIP = {"position", "size_mm"}  # 版面几何不跨图搬
_SYNC_POINT = {"pos_frac", "loc_frac"}  # 点位经 axes 框换算后可搬


def _manifest_of(worker, stem: str) -> dict:
    if not worker.built:
        worker.ensure_built()
    return json.loads((worker.out_dir / f"{stem}.json").read_text(encoding="utf-8"))


def _axes_info(man: dict) -> list[dict]:
    """按序号排列的 axes 概要：bbox + 文字签名 + 子元素数（用于组↔子对位）。"""
    info: dict[int, dict] = {}
    for el in man["elements"]:
        m = re.match(r"axes_(\d+)$", el["gid"])
        if m:
            info[int(m.group(1))] = {"bbox": el.get("bbox"), "texts": set(), "n": 0}
    for el in man["elements"]:
        m = re.match(r"axes_(\d+)\.", el["gid"])
        if not m:
            continue
        i = int(m.group(1))
        ax = info.setdefault(i, {"bbox": None, "texts": set(), "n": 0})
        ax["n"] += 1
        for f in el.get("editable", []):
            if f["prop"] == "text" and f.get("value"):
                ax["texts"].add(str(f["value"]))
    return [info[i] for i in sorted(info)]


def _best_offset(big: list, small: list) -> int:
    def score(a, b):
        return len(a["texts"] & b["texts"]) * 10 - abs(a["n"] - b["n"])

    best, best_s = 0, None
    for o in range(len(big) - len(small) + 1):
        s = sum(score(big[o + j], small[j]) for j in range(len(small)))
        if best_s is None or s > best_s:
            best, best_s = o, s
    return best


def _remap_point(pt, src_bbox, dst_bbox):
    """figure 分数点位：先转源 axes 相对坐标，再落到目标 axes 框。
    目标图边距不同可能越界——钳制进画布并标记，用户可再手动微调。"""
    sx, sy, sw, sh = src_bbox
    dx, dy, dw, dh = dst_bbox
    rx = (float(pt[0]) - sx) / sw if sw else 0.5
    ry = (float(pt[1]) - sy) / sh if sh else 0.5
    x, y = dx + rx * dw, dy + ry * dh
    clamped = not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0)
    return [min(max(x, 0.02), 0.98), min(max(y, 0.02), 0.98)], clamped


@app.post("/api/engine/sync_overrides")
def api_engine_sync_overrides():
    """把一张图的 overrides 映射到同脚本的另一张图（组图 ↔ 子图）。

    返回 mapped（gid 已换、点位已按 axes 框换算）+ skipped（版面几何）
    + unmatched（目标图没有对应元素）。不落任何状态，由前端决定怎么用。
    """
    body = request.get_json(force=True)
    src_path = safe_resolve(body.get("from_id", ""))
    dst_path = safe_resolve(body.get("to_id", ""))
    patches = body.get("patches", [])
    info_s = current_registry().for_stem(src_path.stem)
    info_d = current_registry().for_stem(dst_path.stem)
    if info_s is None or info_d is None or info_s["script"] != info_d["script"]:
        return jsonify(
            {"error": "两张图不属于同一个脚本，无法同步", "code": "sync_different_scripts"}
        ), 400
    worker = _safe_worker(info_s["script"], info_s["entry"], src_path.stem)
    try:
        man_s = _manifest_of(worker, src_path.stem)
        man_d = _manifest_of(worker, dst_path.stem)
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500

    ax_s, ax_d = _axes_info(man_s), _axes_info(man_d)
    if len(ax_s) >= len(ax_d):  # 组 → 子：源 axes 区间 [o, o+K) → 目标 0..K
        off = _best_offset(ax_s, ax_d)
        conv = lambda i: i - off if off <= i < off + len(ax_d) else None  # noqa: E731
        bbox_of = lambda i: (ax_s[i]["bbox"], ax_d[i - off]["bbox"])  # noqa: E731
    else:  # 子 → 组
        off = _best_offset(ax_d, ax_s)
        conv = lambda i: i + off if i < len(ax_s) else None  # noqa: E731
        bbox_of = lambda i: (ax_s[i]["bbox"], ax_d[i + off]["bbox"])  # noqa: E731

    dst_gids = {el["gid"] for el in man_d["elements"]}
    mapped, skipped, unmatched = [], [], []
    for p in patches:
        gid, prop = str(p["gid"]), str(p["prop"])
        if prop in _SYNC_SKIP:
            skipped.append(p)
            continue
        m = re.match(r"axes_(\d+)(\..+)?$", gid)
        if not m:
            skipped.append(p)  # figure / fig.texts 级不映射
            continue
        i, rest = int(m.group(1)), m.group(2) or ""
        ni = conv(i)
        if ni is None:
            unmatched.append(p)
            continue
        ngid = f"axes_{ni}{rest}"
        if ngid not in dst_gids:
            unmatched.append(p)
            continue
        np_ = {**p, "gid": ngid}
        if prop in _SYNC_POINT:
            src_bb, dst_bb = bbox_of(i)
            if src_bb and dst_bb:
                np_["value"], was_clamped = _remap_point(p["value"], src_bb, dst_bb)
                if was_clamped:
                    np_["clamped"] = True  # 越界钳回画布，提示用户可能需微调
        mapped.append(np_)
    return jsonify({"mapped": mapped, "skipped": skipped, "unmatched": unmatched})


@app.get("/api/engine/history")
def api_engine_history():
    """某张图的「更新原图」版本足迹（末位 = 当前基线）。"""
    worker, stem = _engine_worker(request.args.get("id", ""))
    versions = load_baked().get(stem, {}).get("versions") or []
    return jsonify(
        {
            "versions": [
                {"n": i, "ts": v.get("ts", ""), "count": len(v["patches"]), "patches": v["patches"]}
                for i, v in enumerate(versions)
            ]
        }
    )


@app.get("/api/engine/history/preview")
def api_engine_history_preview():
    """历史版本缩略图：临时应用该版本 patches 渲染，不影响当前编辑状态。
    n=-1 表示脚本原始状态。"""
    worker, stem = _engine_worker(request.args.get("id", ""))
    n = int(request.args.get("n", -1))
    w = int(request.args.get("w", 400))
    versions = load_baked().get(stem, {}).get("versions") or []
    patches = [] if n < 0 or n >= len(versions) else versions[n]["patches"]
    try:
        path = worker.preview_png(stem, patches, w, tag=f"hist{n}")
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    resp = send_file(path, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/engine/history/restore")
def api_engine_history_restore():
    """一键恢复到某个历史版本：重放该版本的 patches 重出文件，
    并把它追加为最新版本（历史不回卷、只前进）。n=-1 恢复脚本原始。"""
    if err := _write_back_forbidden():
        return err
    body = request.get_json(force=True)
    if engine_runtimeasset.is_runtime_id(str(body.get("id", ""))):
        # 版本恢复写的是磁盘原件——runtime 素材没有原件，同一条硬拒绝
        return jsonify(
            {
                "error": "运行时素材没有原始图文件，无法恢复写回",
                "code": "runtime_asset_has_no_original_artifact",
                "params": {"id": body.get("id", "")},
            }
        ), 400
    worker, stem = _engine_worker(body.get("id", ""))
    n = int(body.get("n", -1))
    versions = load_baked().get(stem, {}).get("versions") or []
    patches = [] if n < 0 or n >= len(versions) else versions[n]["patches"]
    src = safe_resolve(body.get("id", ""))
    try:
        result = _write_source_files(
            src, patches, worker, expected_mtime=body.get("expected_mtime")
        )
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    except (
        SourceChangedError,
        ScriptChangedError,
        ReplayDivergenceError,
        WriteBackVerifyError,
        FileLockedError,
    ) as exc:
        return _write_back_error_response(exc)
    append_baked(stem, patches)
    return jsonify(_write_back_response(result, patches=patches))


@app.get("/api/engine/svg")
def api_engine_svg():
    """当前 override 状态下的预览 SVG（元素带 gid）。"""
    rel_id = request.args.get("id", "")
    worker, stem = _engine_worker(rel_id)
    try:
        if not worker.built:
            worker, stem, _ = _engine_attempt(
                rel_id, worker, stem, lambda wk, st: wk.ensure_built()
            )
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    svg = worker.svg_path(stem)
    if not svg.exists():
        abort(404)
    resp = send_file(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------- 编码 Agent 桥 -----------------------------------
#
# 端点一律按 **agent id** 收敛（`/api/ai/agents/<agent_id>/…`）：id 必须命中
# `engine/ai_agents.py` 的注册表，否则当场 400。加第三个 Agent 不需要新增
# 任何一条路由分支。全部端点照旧走 ADR 0008 的会话认证，没有旁路。
def _agent_error(exc: "engine_ai.AgentError"):
    """AgentError → 稳定 code 的 JSON。未知 id 是「请求畸形」，其余是状态冲突。"""
    status = 400 if exc.code in ("ai_agent_unknown", "ai_agent_install_unsupported") else 409
    return jsonify({"error": str(exc), "code": exc.code, "params": exc.params}), status


@app.get("/api/ai/capabilities")
def api_ai_capabilities():
    """实测本机每个已注册编码 Agent（安装/版本/就绪/模型/推理强度）+ 第三方接口。"""
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    resp = jsonify(engine_ai.capabilities(refresh=refresh))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.patch("/api/ai/agents/<agent_id>")
def api_ai_agent_settings(agent_id):
    """通用 Agent 设置：`enabled` 与 `path_override`（两个字段都可选）。

    `path_override` 为空 = 显式恢复自动检测；非空值要过与自动探测同一套
    验证，验不过就报错、**不覆盖用户原来有效的设置**。
    """
    # 畸形请求体按「一个字段都没给」处理：返回当前能力，不新造一个通用错误码
    body = request.get_json(force=True, silent=True)
    body = body if isinstance(body, dict) else {}
    try:
        caps = engine_ai.capabilities()
        if "path_override" in body:
            value = body["path_override"]
            caps = engine_ai.set_agent_path_override(
                agent_id, None if value is None else str(value)
            )
        if "enabled" in body:
            caps = engine_ai.set_agent_enabled(agent_id, bool(body["enabled"]))
    except engine_ai.AgentError as exc:
        return _agent_error(exc)
    return jsonify(caps)


@app.post("/api/ai/agents/<agent_id>/install")
def api_ai_agent_install(agent_id):
    """一键 `npm install -g <适配器写死的包名>`（后台线程）。

    包名**只从注册表取**，请求体里没有、也不接受任何包名字段。
    """
    try:
        return jsonify(engine_ai.start_install(agent_id))
    except engine_ai.AgentError as exc:
        return _agent_error(exc)


@app.get("/api/ai/agents/<agent_id>/install")
def api_ai_agent_install_status(agent_id):
    try:
        engine_ai.require_agent(agent_id)
    except engine_ai.AgentError as exc:
        return _agent_error(exc)
    resp = jsonify(engine_ai.install_status(agent_id))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------- 渲染环境（缺 matplotlib 时的自助安装）--------------
@app.get("/api/engine/environment")
def api_engine_environment():
    """渲染环境现状；ok=False 且 can_install=True 时前端给「自动安装」按钮。

    `?probe=1` 会**真去 import** 一遍内置科学栈并报各自版本（最长几十秒）。
    平时不做：普通刷新不该为了贴个版本号卡住界面；排障与冒烟才用得上。
    """
    st = engine_bootstrap.status()
    probe = request.args.get("probe")
    if probe:
        # `?probe=1` 用 manifest 里声明的那批；`?probe=numpy,scipy` 指定要问哪些
        # （用户自己的环境没有 manifest，只能由调用方点名）
        names = (
            [n.strip() for n in probe.split(",") if n.strip()]
            if probe not in ("1", "true", "yes")
            else None
        )
        py = st.get("python")
        st["imports"] = engine_runtime.probe_packages(py, names) if py else {}
    st["project"] = _project_environment_state()
    resp = jsonify(st)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _project_environment_state() -> dict:
    """当前项目的渲染环境（ADR 0018）——**不做任何体检**。

    界面刷新不该为了贴个版本号去起一个 Python（体检最长 60s）。这里只把
    已经记住的决策原样交出去；真要现场核实走 `?probe=1` 那条路。

    `source` 用 `pool` 的那套字符串（`project_venv` / `bundled` / …），
    前端据此显示「项目 .venv · Python 3.12」而不是含糊的一句「Python」——
    同一条路径，「内置」和「你自己的 conda」在排障时含义天差地别。
    """
    try:
        root = str(require_project())
    except NoProjectError:
        return {"open": False}
    state = engine_projectenv.state(root)
    try:
        python, source = engine_pool.resolve_worker_python(root)
    except engine_pool.WorkerError:
        python, source = "", ""
    out = {
        "open": True,
        "source": source,
        "source_label": engine_pool.SOURCE_LABELS.get(source, source),
        "python": _project_relative(python) or python,
        "automatic": state.get("automatic", False),
        "trigger": state.get("trigger", ""),
        "module": state.get("module", ""),
        "can_use_project_venv": [_project_relative(v) for v in engine_projectenv.discover(root)],
        # Tavotto 替这个项目建过的隔离环境（ADR 0019）。**不做任何体检**，
        # 只把 `environment.json` 里记的事实交出去：界面据此显示「Tavotto
        # 环境 · 装了什么」与「重建」入口。
        "managed": engine_managedenv.state(root),
    }
    return out


@app.post("/api/engine/environment/install")
def api_engine_environment_install():
    """在 Tavotto 自己的数据目录里建一个 venv 并装 matplotlib。

    绝不动用户已有的环境——那是他做研究用的。进度经 SSE `engine.bootstrap` 推送。
    """
    st = engine_bootstrap.status()
    if st["ok"]:
        return jsonify({"ok": True, **st})
    if st.get("runtime", {}).get("expected"):
        # 桌面版自带渲染环境，缺了就是安装文件不完整——现场联网建 venv 只会
        # 把一个包装问题伪装成用户的环境问题
        return jsonify({"error": engine_runtime.repair_hint(), "code": st.get("code")}), 400
    if not st.get("can_install"):
        return jsonify(
            {
                "error": "这台机器上没找到可用的 Python，请先安装 Python 3.10 以上再重试。",
                "code": "python_missing",
            }
        ), 400
    engine_bootstrap.install_async(lambda p: sse_publish("engine.bootstrap", p))
    return jsonify({"started": True, **engine_bootstrap.progress()})


@app.patch("/api/engine/environment")
def api_engine_environment_set():
    """手动指定渲染解释器；path 为空 = 清除，回到自动探测。

    `scope="project"` 时改的是**当前项目**那一条（ADR 0018）：项目自己的
    `.venv`、或用户为这个项目挑的别的环境。绝不写全局设置——A 项目挑的环境
    变成 B 项目的渲染环境是本轮明确要避免的事。
    """
    body = request.get_json(force=True)
    raw = str(body.get("python") or "").strip()
    if str(body.get("scope") or "global") == "project":
        return _set_project_environment(raw)
    if raw:
        p = Path(raw).expanduser()
        if not p.is_file():
            return jsonify(
                {
                    "error": f"找不到该文件: {p}",
                    "code": "interpreter_not_found",
                    "params": {"path": str(p)},
                }
            ), 400
        ver = engine_bootstrap.matplotlib_version(str(p))
        if not ver:
            return jsonify(
                {
                    "error": f"{p} 里 import 不到 matplotlib",
                    "code": "interpreter_no_matplotlib",
                    "params": {"path": str(p)},
                }
            ), 400
        engine_config.set_worker_python(str(p))
    else:
        engine_config.set_worker_python(None)
    engine_pool.reset_worker_python()
    return jsonify(engine_bootstrap.status())


def _set_project_environment(raw: str):
    """设定/清除**当前项目**的渲染解释器。

    路径为空 = 回到默认链条（内置 runtime 优先）。给了路径就先真体检一遍：
    「选了但用不了」比「没选」更难查——用户以为设好了，实际每次打开都在报
    另一个错。体检不过一律 400 + 稳定 code，绝不先存下来再说。
    """
    root = str(require_project())
    if not raw:
        engine_projectenv.forget(root)
        engine_pool.reset_worker_python()
        return jsonify({"ok": True, "project": _project_environment_state()})
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        # 前端交回来的是项目相对路径（`.venv/bin/python`）——它才是能跟着
        # 项目走的那种形态。**但相对不等于安全**：`../../../etc/x` 也是相对
        # 路径，拼完就逃出项目了，而这条路径下游是要被当解释器 spawn 的。
        # 所以拼完必须钉回项目内（realpath 之后按前缀判，软链接一并落地）。
        contained = engine_projectenv.contained_file(root, candidate)
        if contained is None:
            return jsonify(
                {
                    "error": f"这条相对路径落在项目之外: {raw}",
                    "code": "script_path_outside_project",
                    "params": {"script": raw},
                }
            ), 400
        candidate = Path(contained)
    if not candidate.is_file():
        return jsonify(
            {
                "error": f"找不到该文件: {candidate}",
                "code": "interpreter_not_found",
                "params": {"path": str(candidate)},
            }
        ), 400
    health = engine_projectenv.probe_environment(str(candidate))
    if not health.get("ok"):
        return jsonify(
            {
                "error": _project_env_message(health),
                "code": health.get("code", ""),
                "params": {
                    "path": _project_relative(str(candidate)),
                    "python_version": health.get("python_version", ""),
                },
            }
        ), 400
    engine_projectenv.remember(
        root, str(candidate), automatic=False, trigger="user_selected", health=health
    )
    engine_pool.reset_worker_python()
    engine_pool.shutdown_all(root)
    return jsonify({"ok": True, "health": health, "project": _project_environment_state()})


def _project_env_message(health: dict) -> str:
    """体检失败的中文回退文案（前端有自己的 i18n，这里是后端兜底）。"""
    code = health.get("code", "")
    if code == engine_projectenv.ERROR_UNSUPPORTED_PYTHON:
        return f"这个环境的 Python {health.get('python_version', '')} 不在 Tavotto 当前支持的范围内"
    if code == engine_projectenv.ERROR_NO_MATPLOTLIB:
        return "这个环境里 import 不到 matplotlib，它不是一个可用的绘图环境"
    if code == engine_projectenv.ERROR_MODULE_MISSING:
        return f"这个环境里也没有 {health.get('requested_module', '')}"
    return f"这个 Python 起不来：{health.get('detail', '')}"


# ------------------------- 受控依赖修复（ADR 0019）--------------------------
#
# 三个端点，职责刻意分开：
#
#   POST /api/engine/dependency/plan     「装什么、装到哪」定下来，发一个短期
#                                        计划 id。**这一步不装任何东西。**
#   POST /api/engine/dependency/install   执行**那个计划**。请求体里只有
#                                        plan_id——装什么不由这次请求说了算
#                                        （防 TOCTOU）。
#   POST /api/engine/dependency/cancel    取消。
#
# 「没有确认就不许改用户环境」是**后端的**能力边界，不是「按钮理论上不会调
# 这个接口」：没有计划 id 一律 `dependency_install_not_allowed`。
def _repair_error(exc: "engine_deprepair.RepairError", status: int = 400):
    body = {"error": str(exc), "code": exc.code}
    health = (exc.extra or {}).get("health")
    if isinstance(health, dict):
        body["params"] = {"python_version": health.get("python_version", "")}
    return jsonify(body), status


@app.post("/api/engine/dependency/plan")
def api_dependency_plan():
    """为「缺 X」形成一个安装计划（不安装）。

    `target` = `project_venv`（改用户环境，需要用户在界面上明确确认）或
    `tavotto_managed`（Tavotto 自己的隔离环境，改的是我们的东西）。
    `distribution` 只在解析不出来时由用户给，仍要过严格语法校验。
    """
    root = str(require_project())
    body = request.get_json(force=True)
    module = str(body.get("module") or "").strip()
    script = str(body.get("script") or "").strip()
    target = str(body.get("target") or "").strip()
    # 空 module / 不合形状的 module 一律交给 `create_plan`：「什么样的模块名
    # 能拿去 import」只有 `projectenv.valid_module_name` 一份判据，端点这里
    # 再写一遍迟早与它分叉。
    try:
        plan = engine_deprepair.create_plan(
            root,
            script,
            module,
            target_kind=target,
            user_distribution=str(body.get("distribution") or "").strip(),
        )
    except engine_deprepair.RepairError as exc:
        return _repair_error(exc)
    return jsonify({"plan": plan.to_payload()})


@app.post("/api/engine/dependency/install")
def api_dependency_install():
    """执行一个已经形成的计划。进度经 SSE `engine.dependency` 推送。

    **请求体里只有 plan_id**：解释器、包名、版本、目标环境全部来自计划本身。
    用户看到的是「把 lmfit 装进 项目 .venv」，点下去执行的就必须是那一件事。
    """
    require_project()
    body = request.get_json(force=True)
    plan_id = str(body.get("plan_id") or "")
    plan = engine_deprepair.get_plan(plan_id)
    if plan is None:
        return jsonify(
            {
                "error": "没有这个修复计划（或已过期），请重新开始。",
                "code": engine_deprepair.ERROR_NOT_ALLOWED,
            }
        ), 409
    if plan.project != str(require_project()):
        # 计划绑定项目：A 项目的计划不能拿到 B 项目来执行
        return jsonify(
            {"error": "这个修复计划不属于当前项目。", "code": engine_deprepair.ERROR_NOT_ALLOWED}
        ), 409
    engine_deprepair.install_async(plan_id, lambda p: sse_publish("engine.dependency", p))
    return jsonify({"started": True, **engine_deprepair.progress(plan_id)})


@app.post("/api/engine/dependency/cancel")
def api_dependency_cancel():
    """取消安装。**不承诺完整 rollback**（用户环境上做不到，见 ADR 0019）。"""
    require_project()
    body = request.get_json(force=True)
    ok = engine_deprepair.cancel(str(body.get("plan_id") or ""))
    return jsonify({"cancelling": bool(ok)})


@app.get("/api/engine/dependency/state")
def api_dependency_state():
    """某个计划的当前进度（SSE 断了之后的补拉）。"""
    resp = jsonify(engine_deprepair.progress(request.args.get("plan_id", "")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/engine/environment/managed/rebuild")
def api_managed_environment_rebuild():
    """删掉并重建当前项目的 Tavotto 受管环境。

    这是受管环境相对「改用户 `.venv`」的**唯一优势**：坏了可以整个扔掉重来。
    重建会按 `environment.json` 里记的把我们装过的包装回去——但**不声称
    lockfile 级复现**：某个版本从 index 上消失时如实报错。
    """
    root = str(require_project())
    # **端点自己不删任何东西**（Codex 评审 P1）：拆旧与重建必须在同一把环境
    # 锁之内。以前是这里先查一下 `is_mutating()`、再在锁外把 venv 删掉、
    # 然后异步去重建——那个窗口里一个已经形成的 plan 可以开始往这个解释器
    # 里 pip install，而它的 venv 正在被删；而且两边拿的还是不同的 key
    # （install 用解释器路径，重建当时用合成 key），根本不互斥。
    engine_deprepair.reset_state(root)
    engine_deprepair.rebuild_managed_async(root, lambda p: sse_publish("engine.dependency", p))
    return jsonify({"started": True})


# ------------------------- 检查更新 -----------------------------------------
def _updater_disabled_in_desktop():
    """桌面模式下 Python updater 整个停用（升级归 Tauri 层，避免两套升级机制）。
    回禁用响应或 None（浏览器 / CLI 模式照旧）。"""
    if app.config.get("TAVOTTO_DESKTOP_MODE"):
        # 带上 Releases 地址：界面据此显示「去下载新安装包」，
        # 而不是留一个永远没有结果的「立即检查」死按钮
        return jsonify(
            {
                "desktop": True,
                "auto_check": False,
                "update_available": False,
                "current": engine_updater.current_version(),
                "repo_url": engine_brand.REPO_URL,
                "releases_url": engine_brand.RELEASES_URL,
            }
        )
    return None


@app.get("/api/update/check")
def api_update_check():
    """?force=1 = 用户手动点「立即检查」，无视 24h 节流与自动检查开关。"""
    if resp := _updater_disabled_in_desktop():
        return resp
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(engine_updater.check(force=force))


@app.patch("/api/update/settings")
def api_update_settings():
    if resp := _updater_disabled_in_desktop():
        return resp
    body = request.get_json(force=True)
    patch = {}
    if "auto_check" in body:
        patch["auto_check"] = bool(body["auto_check"])
    return jsonify(engine_updater.set_settings(patch))


@app.post("/api/update/apply")
def api_update_apply():
    """执行升级。成功后进程仍跑着旧代码，restart_required 由界面提示重启。"""
    if app.config.get("TAVOTTO_DESKTOP_MODE"):
        return jsonify(
            {
                "error": "桌面版内不支持 pip 自升级，请更新桌面应用",
                "code": "desktop_updater_disabled",
            }
        ), 409
    result = engine_updater.apply_upgrade()
    LOG.info("升级 %s: %s", "成功" if result["ok"] else "失败", result["command"])
    return jsonify(result), (200 if result["ok"] else 500)


# ------------------------- 匿名用量统计 -------------------------------------
# 与 /api/update/* 同构的一小组端点。**刻意不回 install_id**：界面只需要知道
# 「现在发不发」，把假名标识交给前端只会让它出现在截图、localStorage 与前端
# 日志里，而界面拿它没有任何用处（engine/telemetry.public_settings 是唯一出口）。
@app.get("/api/telemetry/settings")
def api_telemetry_settings():
    return jsonify(engine_telemetry.public_settings())


@app.patch("/api/telemetry/settings")
def api_telemetry_settings_patch():
    body = request.get_json(force=True)
    consent = body.get("consent")
    if consent not in ("unset", "enabled", "disabled"):
        return jsonify(
            {"error": "consent 必须是 unset / enabled / disabled", "code": "invalid_consent"}
        ), 400
    # source 只影响 telemetry_enabled 的那一条属性，取值受白名单约束
    source = body.get("source") if body.get("source") in ("first_run", "settings") else "settings"
    engine_telemetry.set_consent(consent, source=source)
    return jsonify(engine_telemetry.public_settings())


@app.post("/api/telemetry/event")
def api_telemetry_event():
    """前端语义事件的入口（服务端推断不出来的那几个：进图内编辑、一次编辑
    落进历史、新建画布、预检完成、桌面版更新装完）。

    校验用的是**和后端调用同一份白名单**（engine/telemetry.validate）：
    这个端点在桌面模式下同样被 sidecar 的认证挡着，但即便如此也不该有一条
    「前端说什么就发什么」的通路——白名单是结构性的防线，不是礼貌性的检查。
    """
    body = request.get_json(force=True)
    event = body.get("event")
    props = body.get("properties") or {}
    if not isinstance(event, str) or not isinstance(props, dict):
        return jsonify(
            {
                "error": "event 必须是字符串、properties 必须是对象",
                "code": "invalid_telemetry_event",
            }
        ), 400
    try:
        engine_telemetry.validate(event, props)
    except Exception:  # noqa: BLE001 — 白名单外一律拒绝
        # 不回显收到了什么：那正是不该被记录、也不该被回声出去的东西。
        # 这个 code **不是**用户可见的失败（前端的 captureTelemetry 把一切吞掉，
        # 界面上永远看不到它），所以按 API 段首的约定它没有、也不需要 i18n 文案；
        # 它的用处是让开发者和用例分清「被白名单拒了」与「请求本身畸形」。
        return jsonify({"error": "事件或属性不在白名单内", "code": "telemetry_rejected"}), 400
    accepted = engine_telemetry.capture(event, props)
    return jsonify({"accepted": accepted})


@app.put("/api/ai/endpoints")
def api_ai_endpoint_save():
    """新增/更新一个第三方接口。api_key 留空 = 保留原值（界面不回显密钥）。"""
    body = request.get_json(force=True)
    if not str(body.get("label") or body.get("id") or "").strip():
        return jsonify({"error": "缺少名称", "code": "name_missing"}), 400
    try:
        engine_ai_providers.save(body)
    except (ValueError, OSError) as exc:
        return jsonify(
            {"error": str(exc), "code": "endpoint_save_failed", "params": {"reason": str(exc)}}
        ), 400
    return jsonify(engine_ai.capabilities(refresh=True))


@app.delete("/api/ai/endpoints/<pid>")
def api_ai_endpoint_delete(pid):
    engine_ai_providers.delete(pid)
    return jsonify(engine_ai.capabilities(refresh=True))


@app.post("/api/ai/endpoints/active")
def api_ai_endpoint_active():
    """选中某个 agent 当前使用的接口；id 为空字符串 = 回到 CLI 自带登录态。"""
    body = request.get_json(force=True)
    try:
        engine_ai_providers.set_active(str(body.get("agent") or ""), body.get("id") or None)
    except ValueError as exc:
        return jsonify(
            {"error": str(exc), "code": "endpoint_invalid", "params": {"reason": str(exc)}}
        ), 400
    return jsonify(engine_ai.capabilities(refresh=True))


@app.post("/api/ai/run")
def api_ai_run():
    """启动 codex / claude 对某脚本面板的深度修改。"""
    body = request.get_json(force=True)
    agent = body.get("agent", "codex")
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        abort(400)
    path = safe_resolve(body.get("id", ""))
    info = current_registry().for_stem(path.stem)
    if info is None:
        return jsonify(
            {"error": "该面板不可参数化（没有对应脚本）", "code": "not_parameterizable"}
        ), 404
    context = {
        "stem": path.stem,
        "gid": body.get("gid"),
        "label": body.get("label"),
        "overrides": body.get("overrides"),
        "scope": body.get("scope"),
        "target": body.get("target"),
        "canvas": body.get("canvas"),
    }
    try:
        sid = engine_ai.run(
            agent,
            info["script"],
            prompt,
            str(require_project()),
            context=context,
            on_event=sse_publish,
            model=body.get("model") or None,
            effort=body.get("effort") or None,
            endpoint_id=body.get("endpoint"),
        )
    except engine_ai.AgentError as exc:
        # 未知 / 未安装 / 被用户在 Tavotto 里关掉——前端本该已经过滤掉，
        # 但这个端点可以被直接调，判据只有一份、在后端
        LOG.warning("AI 任务被拒: %s (%s)", agent, exc.code)
        return _agent_error(exc)
    except RuntimeError as exc:
        LOG.error("AI 任务启动失败: %s %s: %s", agent, info["script"], exc)
        return jsonify(
            {"error": str(exc), "code": "ai_start_failed", "params": {"reason": str(exc)}}
        ), 500
    LOG.info("AI 任务启动: %s %s（session %s）", agent, info["script"], sid)
    # **只在会话真的起来之后**记一条，且只记用了哪个 agent。
    # 提示词、脚本、stem、gid、label、target、画布名、会话 id ——一个都不发；
    # agent 走枚举白名单，用户自定义的名字落成 "other" 而不是原样透出。
    # **白名单取自遥测自己的 EVENTS 表**，不是注册表：注册表一加第三个
    # Agent，「在注册表里」就恒真，那个 id 会被原样透出，而 capture() 只收
    # 表里那几个值——结果是那个 Agent 的调用被静默丢弃，「加个适配器就完事」
    # 当场破功。取表里的枚举，不认识的一律落成 "other"。
    allowed = engine_telemetry.EVENTS["ai_assistant_invoked"]["agent"]["values"]
    engine_telemetry.capture(
        "ai_assistant_invoked", {"agent": agent if agent in allowed else "other"}
    )
    return jsonify({"session": sid, "script": info["script"]})


@app.get("/api/ai/history")
def api_ai_history():
    """当前项目的 AI 会话历史：分页 / 搜索 / 状态筛选 / 只看固定。"""
    project = str(require_project().resolve())
    data = engine_ai_history.list_sessions(
        project,
        query=request.args.get("q", ""),
        status=request.args.get("status", ""),
        pinned_only=request.args.get("pinned") == "1",
        limit=min(int(request.args.get("limit", 20)), 100),
        offset=max(int(request.args.get("offset", 0)), 0),
    )
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.delete("/api/ai/history/<sid>")
def api_ai_history_delete(sid):
    return jsonify({"ok": engine_ai_history.delete(sid)})


@app.post("/api/ai/history/<sid>/pin")
def api_ai_history_pin(sid):
    body = request.get_json(force=True)
    return jsonify({"ok": engine_ai_history.set_pinned(sid, bool(body.get("pinned")))})


@app.get("/api/ai/sessions/<sid>")
def api_ai_session(sid):
    sess = engine_ai.get(sid)
    if sess is None:
        abort(404)
    return jsonify(sess)


@app.post("/api/ai/sessions/<sid>/revert")
def api_ai_revert(sid):
    try:
        return jsonify(engine_ai.revert(sid))
    except RuntimeError as exc:
        return jsonify(
            {"error": str(exc), "code": "ai_revert_failed", "params": {"reason": str(exc)}}
        ), 400


@app.post("/api/ai/sessions/<sid>/cancel")
def api_ai_cancel(sid):
    return jsonify({"ok": engine_ai.cancel(sid)})


# ------------------------- 布局的保存 / 读取 -------------------------------
def project_layout_dir(ctx: "ProjectCtx | None" = None) -> Path:
    """命名画布文件的目录：项目内 `tavottofile/`（项目文件的统一收纳处）。

    成品画布要跟图库一起被找到 / 备份 / 同步，藏在应用数据目录里对用户
    等于不存在。未打开项目时退回数据目录 layouts/（纯文字/形状排版不依赖
    项目）；旧位置（项目 `canvases/`、数据目录 layouts/）只读兼容
    （api_layouts 合并列出，保存后以 tavottofile/ 里的为准）。"""
    ctx = ctx if ctx is not None else _request_ctx()
    store = project_store_dir(ctx)
    return LAYOUT_DIR if store is None else store


def _layout_read_dirs() -> list[Path]:
    """读画布的查找顺序：tavottofile/ → 旧项目 canvases/ → 数据目录 layouts/。"""
    ctx = _request_ctx()
    dirs = [project_layout_dir(ctx)]
    if ctx is not None:
        dirs.append(ctx.path / "canvases")
    dirs.append(LAYOUT_DIR)
    return dirs


def layout_path(name: str, base: Path | None = None) -> Path:
    name = re.sub(r"[^\w\-一-鿿]+", "_", name)
    if not name:
        abort(400)
    # 净化之后仍可能撞上收纳目录里 Tavotto 自己的文件名——那条路既能读出
    # 样式表，也能用一份画布把它盖掉。
    engine_documents.require_user_document_stem(name)
    return (base if base is not None else LAYOUT_DIR) / f"{name}.json"


@app.get("/api/layouts")
def api_layouts():
    # 主位置 = 项目 tavottofile/；旧位置只读兼容，重名以主位置为准。
    # 收纳目录里还躺着 Tavotto 自己的文件（`_styles.json`），它们不是用户
    # 文档——不剔掉的话「打开画布」列表里会多出一条叫 `_styles` 的东西，
    # 点开是一份读不成画布的样式表。
    seen: dict[str, float] = {}
    for d in _layout_read_dirs():
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            if not engine_documents.is_user_document_stem(p.stem):
                continue
            if p.stem not in seen:
                seen[p.stem] = p.stat().st_mtime
    names = sorted(seen, key=lambda n: seen[n], reverse=True)
    return jsonify({"layouts": names})


@app.get("/api/layouts/<name>")
def api_layout_get(name):
    for d in _layout_read_dirs():
        p = layout_path(name, d)
        if p.exists():
            return send_file(p, mimetype="application/json")
    abort(404)


@app.post("/api/layouts/<name>")
def api_layout_save(name):
    """用户的「另存为」。

    2026-08-29 之前这里是 `write_text` 直接盖：写到一半失败（磁盘满、断电、
    进程被杀）留下的是一个**截断的文件**，而它已经把上一份好文件顶掉了——
    产品里最显眼的一次保存，恰恰是唯一一处不原子的写入。

    **载荷这里不做 schema 校验。** 已经在用这条路的调用方不止前端：
    `scripts/ci/upgrade_acceptance.py` 发的是 `{"doc": ...}` 包一层的形状。
    在这个 PR 里收紧会让 N-1 升级验收的两个检查悄悄换一种坏法（见
    docs/implementation/product-ux-reliability/STATUS.md 的 R-18），
    那属于修调用方，不属于修落盘。非有限数仍然挡（`atomicio` 里），
    因为那种文档写出去谁都读不回来。
    """
    engine_atomicio.write_json(
        layout_path(name, project_layout_dir()), request.get_json(force=True), indent=1
    )
    return jsonify({"ok": True})


# ------------------------- 文档自动保存（磁盘） ------------------------------
# 文档主体的可靠落盘：localStorage 只留轻量索引与崩溃兜底副本。
# 原子写（tmp + replace），按前端 documentId 一档。
AUTOSAVE_DIR = LAYOUT_DIR / engine_documents.AUTOSAVE_DIRNAME


def _autosave_path(doc_id: str) -> Path:
    doc_id = re.sub(r"[^\w\-]+", "_", doc_id)
    if not doc_id:
        abort(400)
    return AUTOSAVE_DIR / f"{doc_id}.json"


#: 自动保存的「读修订号 → 判冲突 → 写」必须是一个原子段。
#:
#: 判据本身是对的（`_revision_conflict` 两条边都钉住了），但**判完到写完之间
#: 没有互斥**，于是它只在请求串行时成立：两个标签页同时保存同一份文档时，
#: 双方都能在对方落盘之前读到旧的修订号、双方都判「没冲突」，后写的那个把先写
#: 的整份盖掉——**而两边都收到 200**。这正是 `absent` 哨兵要挡的那个场景
#: （两个标签页同时新建同一份文档），只是被交错执行绕了过去。
#:
#: 用**固定条数的锁带**而不是「doc_id → 锁」的表：锁表要自己治理生命周期
#: （什么时候能删掉一把锁没有可靠信号，而它会随打开过的文档数一直长），
#: 锁带的内存是常数。不同文档偶尔共用一把锁只是多串行一点点，正确性不受影响。
_AUTOSAVE_LOCKS = [threading.Lock() for _ in range(64)]


def _autosave_lock(path: Path) -> threading.Lock:
    """按**落盘路径**取锁，不按 doc_id：`_autosave_path` 会把非法字符归一成
    `_`，于是两个不同的 id 可能指向同一个文件——按 id 分锁的话，那两个请求
    以为自己各写各的。"""
    return _AUTOSAVE_LOCKS[hash(str(path)) % len(_AUTOSAVE_LOCKS)]


@app.get("/api/autosave/<doc_id>")
def api_autosave_get(doc_id):
    p = _autosave_path(doc_id)
    if not p.is_file():
        abort(404)
    # 读失败（权限、坏扇区）**照旧往上抛成 500**，不折成 404：「读不动」不是
    # 「没有」——报成 404 的话前端会当成这份自动保存不存在，下一次写就以
    # `absent` 为基线，把一份确实存在、只是此刻读不出来的文件整份盖掉。
    data = p.read_bytes()
    # **读一次，发这一份，也 hash 这一份。** 以前是 `send_file(p)` 再单独
    # `content_revision(p)` 读第二遍，两个毛病：
    #
    # * 两次读之间文件可能被改过 —— 前端拿这个 header 当**后续写入的基线**，
    #   它描述的却是另一份内容，于是外部修改检测会放行一次真正的覆盖；
    # * `send_file` 把文件句柄留在响应里，直到响应被消费完才关。Windows 上
    #   `os.replace` 撞见一个还开着的目标文件就是 `[WinError 5] Access is
    #   denied` —— 用户读过一次这份自动保存之后，下一次保存就写不进去了
    #   （POSIX 上换掉一个开着的文件完全合法，所以这条只在 Windows 上现形）。
    resp = app.response_class(data, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    # 内容 hash 放 header 而不是塞进 body——body 就是文档本身，多一个字段会
    # 跟着一路进 localStorage 兜底副本和版本快照。
    resp.headers["X-Tavotto-Revision"] = engine_atomicio.revision_of(data)
    return resp


def _autosave_newer_than(p: Path, base) -> int | None:
    """磁盘上这一份是否比调用方的基线更新？是则返回它的 updatedAt。

    乐观并发：`base` 是前端标签页最后一次成功落盘时的 updatedAt。磁盘上比它
    更新 = 另一个标签页在这中间存过一份，此时整份覆盖就是静默丢数据。
    没带基线（首次写、旧前端）一律放行；磁盘无文件、读不出来或没有 updatedAt
    也放行——自动保存是用户数据的最后一道，不能因为一个坏掉的旧槽位卡死。
    比较的是文档里的 updatedAt 而不是文件 mtime：同机多标签页共享同一个时钟。
    """
    if base is None:
        return None
    try:
        mine = int(base)
    except (TypeError, ValueError):
        return None
    try:
        theirs = json.loads(p.read_text(encoding="utf-8")).get("updatedAt")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(theirs, (int, float)) and not isinstance(theirs, bool) and theirs > mine:
        return int(theirs)
    return None


def document_summary(path: Path) -> dict | None:
    """磁盘上这一份的结构化摘要 —— 冲突时回答「那边现在是什么」。

    **不做文本 diff**（Prompt 03 §七 明说不需要）：用户在冲突面板上要判断的是
    「磁盘上那份是不是我想要的」，对象数、画布数、schema、两个时间就够了。
    逐行 diff 对一份布局 JSON 也没有意义——里面全是坐标。

    两个时间是**两个维度**，都给：`updatedAt` 是文档自报的编辑时刻（外部工具
    改完可能一动不动），`mtime` 是文件系统记的最后写入时刻（`touch` 一下就变）。
    只报一个，另一类外部修改就在摘要里隐形。

    读不出来（文件不在 / 不是 JSON / 不是对象）返回 `None`：那是「磁盘上没有
    可比较的东西」，不是「摘要里各项为 0」——后者会让前端把一个空壳画成
    「对方把文档清空了」。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        mtime = int(path.stat().st_mtime * 1000)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    canvases = raw.get("canvases")
    return {
        "schema": raw.get("schema"),
        "canvases": len(canvases) if isinstance(canvases, list) else 1,
        "objects": len(_doc_objects(raw)),
        "updatedAt": raw.get("updatedAt"),
        "mtime": mtime,
        "name": (raw.get("project") or {}).get("name")
        if isinstance(raw.get("project"), dict)
        else None,
        "revision": engine_atomicio.content_revision(path),
    }


#: `base_revision` 的哨兵：调用方**读过**，磁盘上当时没有这份文件。
#: 没有它，判据就只钉住了一条边——两个标签页同时新建同一份文档时双方都没有
#: hash 可带，后写的那个把先写的那份整份盖掉，而这正是这条判据要挡的事。
REVISION_ABSENT = "absent"


def _revision_conflict(base_revision: str, current: str | None) -> bool:
    """带着这个基线来写，会不会盖掉别人的内容？

    两侧**故意不对称**：

    - 基线是 `absent`（我以为没有文件）而磁盘上有 → 冲突。那份内容不是我的，
      整份 PUT 就是把它删掉。
    - 基线是某个 hash 而磁盘上没有文件（被外部删了）→ **放行**。这条判据挡的
      是「覆盖别人的内容」，不是「重建一个被删掉的文件」；此时磁盘上没有任何
      内容会因为这次写入而消失，而内存里那份是用户真实的工作。
    - 基线是某个 hash 而磁盘上是另一个 hash → 冲突。
    """
    if base_revision == REVISION_ABSENT:
        return current is not None
    return current is not None and current != base_revision


@app.put("/api/autosave/<doc_id>")
def api_autosave_put(doc_id):
    body = engine_documents.validate_document(request.get_json(force=True))
    p = _autosave_path(doc_id)
    # 外部修改检测（R-08）：`base_revision` 是调用方最后一次**读到或写成功**的
    # 那一份的内容 hash。磁盘上现在的 hash 与它不同 = 这中间有人动过这个文件，
    # 而那个人不一定是另一个 Tavotto 标签页——脚本、同步盘、编辑器都算。
    #
    # 它**强于** `base`（updatedAt 比较）：外部工具改完文档往往一个字节的
    # updatedAt 都不动，甚至写回一个更小的值，那种改动 `base` 一律放行。
    # 所以带了 `base_revision` 就以它为准，`base` 只服务不发修订号的旧前端。
    base_revision = request.args.get("base_revision")
    # 判据与写入之间不能有缝：中间放开一瞬，两个标签页就能双双判「没冲突」
    # 然后一前一后落盘，后写的把先写的整份盖掉。锁按落盘路径取（见
    # `_autosave_lock`），不同文档互不阻塞。
    with _autosave_lock(p):
        if base_revision:
            current = engine_atomicio.content_revision(p)
            if _revision_conflict(base_revision, current):
                return jsonify(
                    {
                        "error": "磁盘上的这份文档已被 Tavotto 之外的改动覆盖过",
                        "code": "external_change",
                        "revision": current,
                        "summary": document_summary(p),
                    }
                ), 409
        else:
            theirs = _autosave_newer_than(p, request.args.get("base"))
            if theirs is not None:
                return jsonify(
                    {
                        "error": "该文档已在其他窗口保存了更新的版本",
                        "code": "stale_write",
                        "theirs": theirs,
                    }
                ), 409
        engine_atomicio.write_json(p, body)
        # revision = 落盘后的内容 hash。外部修改检测拿它当下一次写入的基线：
        # 「我上次写的就是这一份」比「文件的 mtime 是多少」结实得多。
        # **在锁里读**：放到锁外读的话，返回给 A 的可能是 B 刚写下的那份内容的
        # hash，于是 A 的下一次写会带着一个「不是我写的」基线过来。
        revision = engine_atomicio.content_revision(p)
    return jsonify({"ok": True, "saved_at": int(time.time() * 1000), "revision": revision})


@app.get("/api/autosave/<doc_id>/summary")
def api_autosave_summary(doc_id):
    """磁盘上那一份的结构化摘要（冲突面板用）。文件不在就是 404。"""
    summary = document_summary(_autosave_path(doc_id))
    if summary is None:
        abort(404)
    return jsonify(summary)


@app.delete("/api/autosave/<doc_id>")
def api_autosave_delete(doc_id):
    p = _autosave_path(doc_id)
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass
    return jsonify({"ok": True})


# ------------------------- 布局版本时间线 -----------------------------------
# 与「写回原始文件」的版本历史（baked_overrides/<项目>.json，作用于单张图的源文件）
# 是两件事：这里保存的是**整份布局文档**的快照，按前端 documentId 分文件存放，
# 恢复只改前端文档内容，绝不触碰 figures 里的任何文件。
VERSIONS_DIR = (
    LAYOUT_DIR / engine_documents.VERSIONS_DIRNAME
)  # 旧位置：只读兼容（新写入进项目 tavottofile/versions/）
_VERSIONS_LOCK = threading.Lock()
VERSION_KEEP_AUTO = 40  # 自动检查点保留数
VERSION_KEEP_TOTAL = 120  # 单文档版本总数上限（先裁自动、再裁最旧）


_version_seq = 0


def _new_version_id() -> str:
    """毫秒时间戳 + 进程内序号：同毫秒内连续创建（复制紧跟保存）也不碰撞。"""
    global _version_seq
    _version_seq += 1
    return f"v{int(time.time() * 1000):x}-{_version_seq:x}"


def _versions_path(doc_id: str) -> Path:
    doc_id = re.sub(r"[^\w\-]+", "_", doc_id)
    if not doc_id:
        abort(400)
    store = project_store_dir()
    base = (store / "versions") if store is not None else VERSIONS_DIR
    return base / f"{doc_id}.json"


def _load_versions(doc_id: str) -> list[dict]:
    p = _versions_path(doc_id)
    if not p.is_file():
        # 升级前的历史在数据目录 layouts/_versions/：项目里还没有这份文档的
        # 版本文件时继续可见；一旦保存过新版本，就以项目里的为准
        p = VERSIONS_DIR / p.name
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("versions", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save_versions(doc_id: str, versions: list[dict]) -> None:
    engine_atomicio.write_json(_versions_path(doc_id), {"versions": versions})


def _prune_versions(versions: list[dict]) -> list[dict]:
    autos = [v for v in versions if v.get("auto")]
    if len(autos) > VERSION_KEEP_AUTO:
        drop = {id(v) for v in autos[: len(autos) - VERSION_KEEP_AUTO]}
        versions = [v for v in versions if id(v) not in drop]
    return versions[-VERSION_KEEP_TOTAL:]


def _version_meta(v: dict) -> dict:
    doc = v.get("doc") or {}
    meta = {
        "id": v["id"],
        "name": v.get("name", ""),
        "ts": v.get("ts", 0),
        "auto": bool(v.get("auto")),
        "description": v.get("description", ""),
        "objects": len(_doc_objects(doc)),
        "page": doc.get("page"),
    }
    # 检查点存的是**某一张画布**的内容，却按 documentId（= 整个项目）归档。
    # 不记下是哪一张，恢复时就只能往「当前激活的那张」上盖——在画布 B 上产生
    # 的检查点会把 B 的内容和名字盖到 A 头上（R-03）。
    # 旧检查点没有这两个字段：**不填默认值**。`canvasId` 缺席的含义是
    # 「不知道它来自哪张画布」，填一个 "" 或当前画布 id 都是在替它编一个身份。
    if v.get("canvasId"):
        meta["canvasId"] = v["canvasId"]
    if v.get("canvasName"):
        meta["canvasName"] = v["canvasName"]
    return meta


@app.get("/api/versions/<doc_id>")
def api_versions_list(doc_id):
    return jsonify({"versions": [_version_meta(v) for v in _load_versions(doc_id)]})


@app.get("/api/versions/<doc_id>/<vid>")
def api_versions_get(doc_id, vid):
    for v in _load_versions(doc_id):
        if v["id"] == vid:
            return jsonify(v)
    abort(404)


@app.post("/api/versions/<doc_id>")
def api_versions_create(doc_id):
    body = request.get_json(force=True)
    doc = engine_documents.validate_document(body.get("doc"))
    ver = {
        "id": _new_version_id(),
        "name": str(body.get("name") or "").strip() or time.strftime("%m-%d %H:%M"),
        "ts": int(time.time() * 1000),
        "auto": bool(body.get("auto")),
        "description": str(body.get("description") or ""),
        "doc": doc,
    }
    # 画布身份（R-03）：只在调用方真的给了的时候记；给了空串等于没给。
    if body.get("canvasId"):
        ver["canvasId"] = str(body["canvasId"])
    if body.get("canvasName"):
        ver["canvasName"] = str(body["canvasName"])
    with _VERSIONS_LOCK:
        versions = _load_versions(doc_id)
        # 自动检查点若与最近一版内容相同则跳过（刷新/空转不该刷版本）
        if ver["auto"] and versions:
            last = versions[-1]
            # 画布身份也参与去重判据：两张画布内容恰好相同（复制一张画布之后
            # 很常见）时，只比 doc 会把**另一张画布**的检查点判成重复而跳过，
            # 于是那张画布在时间线上一个检查点都没有。
            same_canvas = last.get("canvasId") == ver.get("canvasId")
            if same_canvas and json.dumps(last.get("doc"), sort_keys=True) == json.dumps(
                doc, sort_keys=True
            ):
                return jsonify({"skipped": True, "version": _version_meta(last)})
        versions.append(ver)
        versions = _prune_versions(versions)
        _save_versions(doc_id, versions)
    return jsonify({"version": _version_meta(ver)})


@app.patch("/api/versions/<doc_id>/<vid>")
def api_versions_rename(doc_id, vid):
    body = request.get_json(force=True)
    with _VERSIONS_LOCK:
        versions = _load_versions(doc_id)
        for v in versions:
            if v["id"] == vid:
                if "name" in body:
                    v["name"] = str(body["name"]).strip() or v["name"]
                if "description" in body:
                    v["description"] = str(body["description"])
                if "auto" in body:  # 「保留此检查点」= 转正为手动版本
                    v["auto"] = bool(body["auto"])
                _save_versions(doc_id, versions)
                return jsonify({"version": _version_meta(v)})
    abort(404)


@app.post("/api/versions/<doc_id>/<vid>/duplicate")
def api_versions_duplicate(doc_id, vid):
    with _VERSIONS_LOCK:
        versions = _load_versions(doc_id)
        for v in versions:
            if v["id"] == vid:
                copy = {
                    **v,
                    "id": _new_version_id(),
                    "name": f"{v.get('name', '')} 副本",
                    "ts": int(time.time() * 1000),
                    "auto": False,
                }
                versions.append(copy)
                _save_versions(doc_id, _prune_versions(versions))
                return jsonify({"version": _version_meta(copy)})
    abort(404)


@app.delete("/api/versions/<doc_id>/<vid>")
def api_versions_delete(doc_id, vid):
    with _VERSIONS_LOCK:
        versions = _load_versions(doc_id)
        kept = [v for v in versions if v["id"] != vid]
        if len(kept) == len(versions):
            abort(404)
        _save_versions(doc_id, kept)
    return jsonify({"ok": True})


# ------------------------- 论文样式预设 -------------------------------------
# 命名样式（字体/字号/线宽/刻度/图例/页面预设），跨文档共享，存在 layouts 下。
# 样式只在前端映射成 override / 标注属性，应用是文档操作（可撤销），
# 不经它写回任何源文件。
STYLES_PATH = LAYOUT_DIR / engine_documents.STYLES_FILENAME
_STYLES_LOCK = threading.Lock()


def _load_styles() -> list[dict]:
    try:
        data = json.loads(STYLES_PATH.read_text(encoding="utf-8"))
        return data.get("styles", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save_styles(styles: list[dict]) -> None:
    engine_atomicio.write_json(STYLES_PATH, {"styles": styles}, indent=1)


@app.get("/api/styles")
def api_styles_list():
    return jsonify({"styles": _load_styles()})


@app.post("/api/styles")
def api_styles_save():
    body = request.get_json(force=True)
    if not isinstance(body, dict) or not str(body.get("name") or "").strip():
        return jsonify({"error": "样式需要一个名称", "code": "name_missing"}), 400
    with _STYLES_LOCK:
        styles = _load_styles()
        sid = body.get("id") or f"s{int(time.time() * 1000):x}"
        body = {**body, "id": sid, "name": str(body["name"]).strip()}
        idx = next((i for i, s in enumerate(styles) if s.get("id") == sid), None)
        if idx is None:
            styles.append(body)
        else:
            styles[idx] = body
        _save_styles(styles[-100:])
    return jsonify({"style": body})


@app.delete("/api/styles/<sid>")
def api_styles_delete(sid):
    with _STYLES_LOCK:
        styles = _load_styles()
        kept = [s for s in styles if s.get("id") != sid]
        if len(kept) == len(styles):
            abort(404)
        _save_styles(kept)
    return jsonify({"ok": True})


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def tavotto_is_serving(port: int) -> bool:
    """占着这个端口的是不是另一个 Tavotto（而不是别的程序）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1.5) as resp:
            return "build" in json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


#: TCP 端口号的上限。**不是风格常量**：`bind()` 收到 65535 以上的号抛的是
#: `OverflowError` 而不是 `OSError`，而 `port_is_free()` 只 catch 后者。
MAX_PORT = 65535


def resolve_port(preferred: int, tries: int = 20) -> int | None:
    """要用的端口；None = 该端口上已经有一个 Tavotto 在跑，不必再起。

    被别的程序占用时顺延找下一个空闲端口——双击启动的应用不能因为端口冲突就
    一声不响地退出（窗口化打包下用户连 traceback 都看不到）。

    **顺延绝不越过 `MAX_PORT`。** 越过去的表现恰恰是这个函数自己承诺不许发生
    的那件事：`bind(65536)` 抛的是 `OverflowError`，而 `port_is_free()` 只
    catch `OSError`——于是 `preferred` 落在范围顶端 `tries` 个之内、且那几个
    都被占着时，这里**当场崩掉**。默认参数下窗口是 65516–65535；调用方把
    `tries` 调大，窗口就跟着变大。

    扫不动了就退回 `preferred`（与"全占满了"同一条出口）：交给 `app.run`
    报一个说得清的错，而不是从一个探测函数里抛 OverflowError。
    """
    if port_is_free(preferred):
        return preferred
    if tavotto_is_serving(preferred):
        return None
    for p in range(preferred + 1, min(preferred + 1 + tries, MAX_PORT + 1)):
        if port_is_free(p):
            return p
    return preferred  # 全占满了：交给 app.run 报错，至少日志里有据可查


def main():
    # 启动信息里有中文。Windows 上 stdout 一旦不是真控制台（被重定向到文件、
    # 由启动器接管管道）就退回系统区域编码，print 会 UnicodeEncodeError 直接
    # 打死进程——用户看到的是「启动即崩」，却查不出原因。
    # 实现收在 engine/cli.py（纯标准库，三个入口共用同一份）。
    engine_cli.use_utf8_streams()

    # 子命令（`tavotto open` / `tavotto doctor`）：外部程序（Codex 插件、安装器、
    # 编辑器、别的 Agent、用户自己）的入口。**必须在 argparse 之前拦**——主入口
    # 是纯 flag 形态（`tavotto --figures …`），改成 subparsers 会把既有命令行
    # 整个换掉。分派本身在 engine/cli.py（纯标准库），打包出来的 tavotto-cli
    # 走的是同一份，不必为一次交接付整个 Flask 的冷启动。
    rc = engine_cli.dispatch(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)

    ap = argparse.ArgumentParser(
        description=__doc__,
        # 子命令在上面就分派掉了，argparse 看不见它——不在这儿写一句，
        # `tavotto --help` 里就查无此命令
        epilog="另有子命令（详见各自的 --help）:\n"
        "  tavotto open <图|脚本|目录>  把一张刚画好的图交给 Tavotto 打开\n"
        "  tavotto doctor               检查本机安装并维护交接用的安装清单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--figures", default=None, help="面板图所在目录（缺省恢复最近打开的项目）")
    ap.add_argument("--port", type=int, default=5089)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument(
        "--open-stem",
        default=None,
        help="启动后在界面里定位这个面板（stem）；由 `tavotto open` 传入",
    )
    ap.add_argument(
        "--open-pick",
        default=None,
        help="启动后打开这个脚本的 Figure 选择器（多图交接）；由 `tavotto open` 传入",
    )
    ap.add_argument(
        "--desktop-sidecar",
        action="store_true",
        help="作为 Tavotto 桌面应用的后端运行：127.0.0.1 动态端口 + "
        "桌面认证 + 父进程跟随退出（由桌面壳启动，不建议手动使用）",
    )
    ap.add_argument(
        "--insecure-no-auth",
        action="store_true",
        help="禁用本地会话认证（任何本机页面/进程都能调用全部 API）。"
        "仅供开发调试（vite dev proxy / 手工 curl），生产环境不要用",
    )
    args = ap.parse_args()

    setup_logging()
    # 安装清单刷成「这套 Tavotto 现在在这儿」。安装器只写得了装的那一刻，
    # 而用户会把 .app 拖到别处、会用免安装形态、macOS 压根没有安装后钩子——
    # 每次启动刷一遍，外部程序（Codex 插件）查到的就永远是最后真跑起来过的
    # 那一套。**失败一律不打扰用户**：清单只是快路径，已知安装位置那条腿还在。
    if engine_locate.refresh_manifest() is None:
        LOG.debug("安装清单未能刷新（不影响使用）")
    threading.Thread(
        target=prune_render_cache, daemon=True, name="mm-cache-prune"
    ).start()  # 启动清一次历史存量
    # 引擎会话缓存同理：get() 里的触发点只在新建会话时走，长开不新建的实例靠这次
    threading.Thread(
        target=engine_pool.prune_engine_cache, daemon=True, name="mm-engine-cache-prune"
    ).start()
    # runtime 素材的 materialized cache（可删除可重建的派生物）同一治理
    threading.Thread(
        target=engine_runtimeasset.prune_cache, daemon=True, name="mm-runtime-cache-prune"
    ).start()
    # 上个进程留下的 running AI 会话一律标为已中断（绝不显示为空/unknown）
    n = engine_ai_history.mark_interrupted_running()
    if n:
        LOG.info("上次运行遗留的 %d 个 AI 会话已标记为中断", n)
    engine_ai_history.purge(keep_days=180)
    if not args.desktop_sidecar:
        # 桌面模式的升级由 Tauri 层负责，Python updater 连后台检查都不跑
        engine_updater.check_in_background()  # 默认每天一次；设置里可关，关了不联网

    # --figures 最高优先；否则恢复最近项目；都没有（或无效）→ 界面里的
    # Project Picker 接手，进程不再直接退出。
    where = "窗口" if args.desktop_sidecar else "浏览器"
    candidate = args.figures or engine_config.last_project()
    if candidate:
        try:
            st = open_project(candidate)
            print(f"* 项目: {st['figures_dir']}（{st['scripts']} 个脚本）")
            if st.get("drafted"):
                print("* 未找到注册表，已静态扫描生成草稿（cost 默认 medium，请按需修正）")
            if st.get("conflicts"):
                print(
                    f"  ⚠ {len(st['conflicts'])} 个 stem 归属冲突未分配，"
                    f"请在注册表中手工裁决: {', '.join(st['conflicts'])}"
                )
        except (RuntimeError, OSError) as exc:
            print(f"* 无法打开项目 {candidate}: {exc}")
            print(f"* 请在{where}中选择或新建项目")
    else:
        print(f"* 尚未选择项目：请在{where}中新建或打开一个项目")

    if args.desktop_sidecar:
        # 一次**真实的应用会话**开始了。放在这里而不是 import 时：
        # `tavotto --help` / `tavotto doctor` / 打包脚本 / 单测都会 import
        # 到 app 模块，把它们算进 DAU 会让这个数字从第一天起就是假的。
        # 没同意时这一行什么都不做；用户在本次会话里同意之后由
        # telemetry.set_consent 补发（同一次会话只发一条）。
        engine_telemetry.note_app_started("desktop")
        sys.exit(desktop_mode.run(app))

    # 落地地址的形状（含 `?open=<stem>`）只有 handoff.browser_url 一个出处：
    # 前端 lib/openRequest.ts 认的就是它，两边别各写一份。
    def landing(p: int) -> str:
        return engine_handoff.browser_url(
            p, engine_handoff.Target("", args.open_stem, args.open_pick)
        )

    insecure = args.insecure_no_auth or os.environ.get("TAVOTTO_INSECURE_NO_AUTH") == "1"

    port = resolve_port(args.port)
    if port is None:
        # 端口上已经有一个 Tavotto 在跑：把浏览器指过去就够了，别再起一个。
        # 双击应用图标的用户没有终端可看，这里必须自己把事办圆。
        # 复用是一次**安全的 token 交接**：凭本机凭据文件向在跑的实例换一枚
        # 一次性 nonce（session_client.relaunch_nonce）；对面是老版本或
        # --insecure-no-auth 的实例时换不到，裸地址也照样能用。
        url = landing(args.port)
        nonce = engine_session_client.relaunch_nonce(args.port)
        if nonce:
            url += "#dnonce=" + nonce
        print(f"* Tavotto 已在 {landing(args.port)} 运行，打开现有窗口")
        if not args.no_browser:
            webbrowser.open(url)
        return

    url = landing(port)
    if insecure:
        print(
            "* ⚠ 已禁用本地会话认证（--insecure-no-auth）：任何本机页面/进程"
            "都能调用全部 API，仅供开发调试"
        )
    else:
        # 浏览器模式与桌面模式共用同一道会话边界（security.py / ADR 0008）：
        # 一次性 nonce 走落地 URL 的 fragment（不进 HTTP 请求行与访问日志），
        # 本机 CLI/脚本凭 0600 凭据文件的 X-Tavotto-Auth 头直连。
        state, nonce = security.new_browser_state(port)
        app.config[security.STATE_KEY] = state
        url += "#dnonce=" + nonce
        import atexit

        atexit.register(engine_session_client.remove_secret, port)
    if port != args.port:
        print(f"* 端口 {args.port} 被占用，改用 {port}")
    print(f"* 打开 {url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # 同上：真的要开始服务了才算一次会话。**上面「已有实例在跑，把浏览器
    # 指过去就完事」那条分支刻意不记**——那个进程没有提供任何服务。
    engine_telemetry.note_app_started("browser")
    try:
        app.run(host="127.0.0.1", port=port, threaded=True)
    finally:
        if not insecure:
            engine_session_client.remove_secret(port)


if __name__ == "__main__":
    main()
