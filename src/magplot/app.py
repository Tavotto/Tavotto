#!/usr/bin/env python3
"""
Magplot — 论文多面板图可视化排版工具

扫描 figures 目录中的 PDF（矢量）与 PNG/JPG 面板，在浏览器画布上自由
拖拽 / 缩放 / 对齐 / 加标注，最终导出出版级 PNG（可选 DPI）和真矢量 PDF。

用法:
    magplot [--figures 图目录] [--port 5089]      # 装成包后
    ./run.sh [同上]                                # 源码树
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import queue
import shutil
import socket
import sys

from flask import (Flask, Response, abort, has_request_context, jsonify,
                   request, send_file, send_from_directory)
from werkzeug.exceptions import HTTPException

from . import pdfbackend
from .pdfbackend import hex2rgb, mm2pt
from .engine import ai_bridge as engine_ai
from .engine import bootstrap as engine_bootstrap
from .engine import ai_history as engine_ai_history
from .engine import brand as engine_brand
from .engine import config as engine_config
from .engine import diagnostics as engine_diagnostics
from .engine import discover as engine_discover
from .engine import patchspec as engine_patchspec
from .engine import pool as engine_pool
from .engine import probe as engine_probe
from .engine import ai_providers as engine_ai_providers
from .engine import registry as engine_registry
from .engine import runtime as engine_runtime
from .engine import updater as engine_updater

PKG_ROOT = Path(__file__).resolve().parent   # 只读：包自带资源（前端构建产物）
DATA_ROOT = engine_config.data_dir()         # 可写：运行时产物（装成包后 site-packages 不可写）

# magplotfile/ 是项目内的 Magplot 数据收纳目录（画布/导出/版本历史）：
# 导出的 PDF/PNG 落在里面，素材扫描必须剪掉，否则导出一次素材面板就多一堆成图
EXCLUDE_DIRS = {"__pycache__", "_cache", "_palette_ref", "scripts", ".git",
                "magplotfile"}
PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg"}

MM_PER_PT = 25.4 / 72.0
RENDER_BUCKETS = [200, 400, 800, 1600, 3200]

app = Flask(__name__, static_folder=None)

# 桌面 sidecar 的认证钩子必须在首个请求前注册；浏览器模式下全部旁路（零行为差异）
from . import desktop as desktop_mode  # noqa: E402 — 需要 app 实例存在后立即挂钩

desktop_mode.install(app)

# 打开着的项目：id → ProjectCtx。**一个进程可以同时端着多个项目**——
# 不同浏览器标签页各开各的图库（标签页把自己的 pj 带在请求上，见
# `_request_ctx`）。没有任何项目时前端显示 Project Picker。
# 不再内置任何默认路径——项目由 --figures、最近项目或 Picker 决定。
PROJECTS: dict[str, "ProjectCtx"] = {}
DEFAULT_PROJECT: str | None = None       # 不带 pj 的请求落到这里
_PROJECT_LOCK = threading.Lock()
CACHE_DIR = DATA_ROOT / "cache"
EXPORT_DIR = DATA_ROOT / "exports"
LAYOUT_DIR = DATA_ROOT / "layouts"
# 前端构建产物：装成包后随 wheel 落在 magplot/web/；源码树里还没打包，
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

LOG = logging.getLogger("mm")

# ---- 缓存增长治理（三处无限增长点各给上限） --------------------------------
RENDER_CACHE_MAX_BYTES = 500 * 1024 * 1024  # cache/*.png 渲染缓存总预算
BACKUP_KEEP = 20                            # 「更新原图」备份保留份数


def setup_logging() -> None:
    """stderr + cache/app.log（1MB×3 轮转）。重复调用无副作用。"""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    file = RotatingFileHandler(CACHE_DIR / "app.log", maxBytes=1_000_000,
                               backupCount=3, encoding="utf-8")
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
        LOG.info("渲染缓存清理: 删除 %d 个文件（预算 %dMB）",
                 removed, max_bytes // (1024 * 1024))
    return removed


def prune_backups(root: Path, keep: int = BACKUP_KEEP) -> int:
    """original_backups 只保留最近 keep 个时间戳目录。"""
    if not root.is_dir():
        return 0
    dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime)
    for old in dirs[:-keep] if keep else dirs:
        shutil.rmtree(old, ignore_errors=True)
    return max(0, len(dirs) - keep)


def _baked_path(ctx: "ProjectCtx") -> Path:
    return BAKED_DIR / f"{ctx.id}.json"


def _write_baked(path: Path, data: dict) -> None:
    """临时文件 + replace 原子落盘，读者不会撞见半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


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
    mine = {stem: v for stem, v in legacy.items()
            if isinstance(v, dict) and ctx.registry.for_stem(stem) is not None}
    try:
        _write_baked(path, mine)
    except OSError:      # 写不进去（只读介质）：这次照旧读旧文件，不拦渲染
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
            data[stem] = {"versions": [{"ts": v.get("updated_at", ""),
                                        "patches": v["patches"]}]}
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
        entry["versions"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "patches": patches,
                                  "patch_hash": engine_patchspec.patch_hash(patches)})
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
    key = str(path).lower() if os.name == "nt" else str(path)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _request_ctx() -> "ProjectCtx | None":
    """本次请求作用于哪个项目：显式 pj > 默认项目。

    pj 走查询参数或请求头两条路：`fetch` 统一加请求头，但 `<img src>` 和
    EventSource 加不了头，只能用查询参数——两条都认才不会有一半 API 串项目。
    """
    # 后台线程（watcher 回调、启动流程）没有请求上下文，落到默认项目
    pid = ((request.args.get("pj") or request.headers.get("X-Magplot-Project")
            or "").strip() if has_request_context() else "")
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
    baked = load_baked(ctx)      # 本项目的写回基线，局部变量（绝不跨项目共享）
    panels = []
    root = ctx.path.resolve()
    # os.walk 而不是 rglob：隐藏目录当场剪枝，不下探。图库里常有 .venv、
    # .git、工具留下的 .rendered/.qa_* 快照——它们既是噪音（素材库里塞满
    # page-1.png），爬进去还很慢。以 . 开头的文件同理（.DS_Store）。
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.startswith(".")]
        files += [Path(dirpath) / fn for fn in filenames if not fn.startswith(".")]
    files.sort()
    LOG.info("素材扫描: %s → %d 个文件", root, len(files))

    pdf_stems = {(p.parent, p.stem) for p in files if p.suffix.lower() in PDF_EXT}

    for p in files:
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
            if ext in PDF_EXT:
                probe = pdfbackend.probe_asset(p, "pdf")
                entry.update(
                    kind="pdf",
                    native_w_mm=round(probe["w_pt"] * MM_PER_PT, 3),
                    native_h_mm=round(probe["h_pt"] * MM_PER_PT, 3),
                )
                info = current_registry().for_stem(p.stem)
                if info is not None:  # 可参数化面板：有产出它的 matplotlib 脚本
                    entry.update(script=info["script"], cost=info["cost"])
                    baseline = _baseline_patches(p.stem, baked)
                    if baseline:
                        entry["baked_overrides"] = baseline
            elif ext in IMG_EXT:
                if (p.parent, p.stem) in pdf_stems:
                    continue  # 有矢量版就不重复列出位图
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
                if info is not None:  # fig1 等纯 PNG 素材脚本
                    entry.update(script=info["script"], cost=info["cost"])
                    baseline = _baseline_patches(p.stem, baked)
                    if baseline:
                        entry["baked_overrides"] = baseline
            else:
                continue
        except Exception:
            # 单个素材坏了不拖垮整个列表，但绝不静默——用户丢面板时
            # app.log 里要能看到是哪个文件、为什么
            LOG.warning("素材扫描跳过 %s（probe 失败）", p, exc_info=True)
            continue
        panels.append(entry)
    return panels


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.errorhandler(NoProjectError)
def _no_project(_exc):
    return jsonify({"error": "尚未打开项目", "code": "no_project"}), 409


def _worker_error_payload(exc) -> dict:
    """worker 错误的统一响应体。

    `module` 只在 code == "missing_dependency" 时有值：用户脚本 import 了当前
    渲染环境里没有的包（内置 runtime 只带常用科学栈）。前端据此给「换成你自己
    的环境」这个可执行出口，而不是甩一段 ModuleNotFoundError。
    """
    body = {"error": str(exc), "traceback": exc.traceback_text, "code": exc.code}
    if getattr(exc, "module", ""):
        body["module"] = exc.module
    return body


@app.errorhandler(engine_pool.WorkerError)
def _worker_error(exc):
    """worker 类错误统一带上 code。

    多数端点自己 catch 了，但 `_engine_worker()` 这类调用常落在 try 之外——
    没有这个处理器时它们会掉进通用 Exception 处理器，`code` 全丢，前端就分不出
    「缺渲染环境」（该给引导）和「脚本报错」（该给 traceback）。
    """
    LOG.error("worker 错误: %s %s: %s", request.method, request.path, exc)
    return jsonify(_worker_error_payload(exc)), 500


@app.errorhandler(Exception)
def _unhandled(exc):
    """未处理异常：记日志并回 JSON（前端各处都按 JSON 解析错误）。
    abort() 的 HTTPException 原样放行，不动 403/404 语义。"""
    if isinstance(exc, HTTPException):
        return exc
    LOG.exception("未处理异常: %s %s", request.method, request.path)
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.get("/")
def index():
    """工作台（Vite 构建产物）。index.html 必须每次验证，
    否则前端部署新 bundle 后旧标签页/启发式缓存会继续跑旧代码。"""
    if not (WEB_DIST / "index.html").is_file():
        # 源码检出直接跑起来时最常见的一步没做——给出确切命令而不是白屏 404
        return (f"<h1>{engine_brand.PRODUCT_NAME}: 前端尚未构建</h1>"
                "<p>请先执行：<code>python scripts/build_frontend.py</code>"
                "（需要 node + pnpm），或改用发行版："
                "<code>pipx install magplot</code>。</p>"), 503
    resp = send_from_directory(WEB_DIST, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/assets/<path:name>")
def web_assets(name):
    """vite 输出的 hash 资源，index.html 里是绝对路径 /assets/…"""
    resp = send_from_directory(WEB_DIST / "assets", name)
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
    key = hashlib.sha1(f"{rel_id}|{path.stat().st_mtime}|{w}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.png"
    if not cached.exists():
        pdfbackend.render_preview_png(path, w, cached)
        prune_render_cache()
    # no-cache = 每次向服务器验证（304 极快）；文件一变（mtime 进 key）立即失效。
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
    """
    path = safe_resolve(o["id"])
    overrides = o.get("overrides") or []
    if overrides:
        info = current_registry().for_stem(path.stem)
        if info is not None:
            worker = engine_pool.get(info["script"], str(require_project()), info["entry"])
            tmp = worker.export_dir / f"{path.stem}.pdf"
            resp = worker.export(path.stem, overrides, str(tmp), "pdf", dpi)
            if sink is not None:
                for w in (resp.get("warnings") or []):
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
        objects = ([{"type": "panel", **it} for it in spec.get("items", [])]
                   + [{"type": "text", **t} for t in spec.get("texts", [])])

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
            return jsonify({"error": f"{o.get('id', kind)} 重渲染失败: {exc}",
                            "traceback": exc.traceback_text}), 500

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
        proof = {**proof, "files": [f["name"] for f in out_files],
                 "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        (out_dir / name).write_text(
            json.dumps(proof, ensure_ascii=False, indent=1), encoding="utf-8")
        out_files.append({"name": name, "url": f"/exports/{name}"})
    LOG.info("导出: %s（%d 对象, %s, %.0fms）%s",
             [f["name"] for f in out_files], len(objects), formats,
             (time.time() - t0) * 1000,
             f"，{len(warnings)} 条警告" if warnings else "")
    if warnings:
        LOG.warning("导出警告: %s", warnings)
    return jsonify({"files": out_files, "export_dir": str(out_dir),
                    "warnings": warnings})


@app.get("/exports/<path:name>")
def api_exports(name):
    return send_from_directory(project_export_dir(), name, as_attachment=False)


# ------------------------- 可复现项目包 -------------------------------------
def _doc_objects(doc: dict) -> list[dict]:
    """布局文档里的全部对象：schema 2 单画布 / schema 3 跨全部画布。"""
    if doc.get("schema") == 3:
        return [o for c in doc.get("canvases", []) if isinstance(c, dict)
                for o in c.get("objects", []) if isinstance(o, dict)]
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
        return jsonify({"error": "无效的布局文档"}), 400
    default_stem = (doc.get("project") or {}).get("name") if doc.get("schema") == 3 \
        else doc.get("name")
    stem = re.sub(r"[^\w\-一-鿿]+", "_", body.get("stem") or default_stem or "package")

    panel_ids = sorted({o.get("fileId") for o in _doc_objects(doc)
                        if o.get("type") == "panel" and o.get("fileId")})
    assets, missing_now, scripts = [], [], {}
    for rel_id in panel_ids:
        p = (root / rel_id).resolve()
        entry = {"id": rel_id}
        if p.is_relative_to(root.resolve()) and p.is_file():
            entry.update(sha1=_sha1_of(p), mtime=int(p.stat().st_mtime),
                         bytes=p.stat().st_size)
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
    }

    out_dir = project_export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    # 新包用 .magplot 单文件扩展（zip 容器）；读取端继续兼容 .mmpack.zip
    name = f"{stem}_{time.strftime('%m%d_%H%M%S')}{engine_brand.PACKAGE_EXT}"
    out = out_dir / name
    import zipfile  # noqa: PLC0415 — 仅此端点用
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("layout.json", json.dumps(doc, ensure_ascii=False, indent=1))
        z.writestr("package_manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=1))
        for entry in assets:
            z.write(root / entry["id"], f"assets/{entry['id']}")
        for rel, path in scripts.items():
            if path.is_file():
                z.write(path, f"scripts/{rel}")
    LOG.info("项目包: %s（%d 素材, %d 脚本）", name, len(assets), len(scripts))
    return jsonify({"name": name, "url": f"/exports/{name}",
                    "assets": len(assets), "missing": missing_now})


@app.post("/api/package/open")
def api_package_open():
    """检视上传的项目包：取出布局，并对照当前图库列出缺失/内容漂移的素材。
    只读不写——素材永远不会被自动安装进图库。"""
    file = request.files.get("package")
    if file is None:
        return jsonify({"error": "缺少上传文件（multipart 字段 package）"}), 400
    import io
    import zipfile  # noqa: PLC0415
    try:
        z = zipfile.ZipFile(io.BytesIO(file.read()))
        doc = json.loads(z.read("layout.json"))
        manifest = json.loads(z.read("package_manifest.json"))
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        return jsonify({"error": f"不是有效的项目包: {exc}"}), 400
    if doc.get("schema") not in (2, 3):
        return jsonify({"error": "项目包里的布局既不是 schema 2 也不是 schema 3"}), 400

    root = require_project()
    missing, drifted = [], []
    for entry in manifest.get("assets", []):
        rel_id = entry.get("id", "")
        p = (root / rel_id).resolve()
        if not (p.is_relative_to(root.resolve()) and p.is_file()):
            missing.append(rel_id)
        elif entry.get("sha1") and _sha1_of(p) != entry["sha1"]:
            drifted.append(rel_id)
    return jsonify({
        "doc": doc,
        "manifest": {k: manifest.get(k) for k in
                     ("created_at", "figures_dir", "page", "export_settings",
                      "scripts")},
        "missing": missing,
        "drifted": drifted,
    })


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

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ------------------------- 项目（Project）管理 -------------------------------
# 对象层级见 docs/adr/0001-project-canvas-tab-object.md：Project = 图库路径 +
# 素材根 + 导出/备份位置 + 设置。用户级配置（最近项目等）存 engine_config。
def _script_change_handler(ctx: "ProjectCtx"):
    """watcher 回调必须绑定到具体项目——事件里带上 pj，别的标签页才不会
    因为另一个图库的脚本变动去重渲染自己的面板。"""
    def _on_change(changed: list[str]) -> None:
        stems = [s for sc in changed for s in ctx.registry.stems_of(sc)]
        sse_publish("panel.file_changed",
                    {"scripts": changed, "stems": stems, "pj": ctx.id})
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
        return {**project_status(existing), "drafted": False, "conflicts": [],
                "reused": True}

    drafted, conflicts = False, []
    try:
        reg = engine_registry.open_registry(path)
    except FileNotFoundError:
        cfg, rep = engine_discover.build_draft(path)
        engine_discover.write_config(path, cfg)
        reg = engine_registry.open_registry(path)
        drafted, conflicts = True, sorted(rep["conflicts"])
    ctx = ProjectCtx(path, pid, reg)
    with _PROJECT_LOCK:
        PROJECTS[pid] = ctx
        if make_default or DEFAULT_PROJECT is None:
            DEFAULT_PROJECT = pid
    engine_pool.start_watcher(str(path), reg.all_scripts(),
                              _script_change_handler(ctx))
    engine_config.touch_recent(str(path))
    LOG.info("项目已打开: %s（%d 个脚本%s）", path, len(reg.all_scripts()),
             "，注册表为静态扫描草稿" if drafted else "")
    return {**project_status(ctx), "drafted": drafted, "conflicts": conflicts,
            "reused": False}


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
    if wait:
        engine_pool.shutdown_all(wait=True)   # 兜底：不属于任何项目的残留


def reload_registry(ctx: "ProjectCtx") -> None:
    """注册表改过之后重装并重挂 watcher（新脚本要被盯上才会自动作废会话）。"""
    try:
        ctx.registry.load(ctx.path)
    except (FileNotFoundError, RuntimeError):
        return
    engine_pool.start_watcher(str(ctx.path), ctx.registry.all_scripts(),
                              _script_change_handler(ctx))


def project_store_dir(ctx: "ProjectCtx | None" = None) -> Path | None:
    """项目文件夹内的 `magplotfile/`：与该项目相关的 Magplot 文件统一收纳处
    ——命名画布布局直接放里面，导出在 `export/`，布局版本历史在 `versions/`。
    用户在自己的项目目录里就能看到、随项目一起备份/同步/迁移。
    未打开项目时返回 None（调用方各自退回数据目录）。"""
    ctx = ctx if ctx is not None else _request_ctx()
    return None if ctx is None else ctx.path / "magplotfile"


def project_export_dir(ctx: "ProjectCtx | None" = None) -> Path:
    """项目的导出目录（项目设置可覆盖）。

    缺省 `<项目>/magplotfile/export/`——导出的成图要交给投稿/合作者，
    跟着项目走才找得到（旧版的 `<项目名>-exports/` 同级目录不再新建，
    已有的留在原地不动）。项目目录建不出来（只读、网络盘等）退回数据目录
    exports/。未打开项目时（纯文字/形状导出不依赖项目）直接用数据目录。"""
    ctx = ctx if ctx is not None else _request_ctx()
    if ctx is None:
        return EXPORT_DIR
    d = engine_config.project_settings(str(ctx.path)).get("export_dir")
    if d:
        return Path(d).expanduser()
    store = project_store_dir(ctx)
    assert store is not None
    try:
        (store / "export").mkdir(parents=True, exist_ok=True)
        return store / "export"
    except OSError:
        return EXPORT_DIR


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
            out = sp.run([py, "-c", "import matplotlib; print(matplotlib.__version__)"],
                         capture_output=True, text=True,
                         # 显式 UTF-8：text=True 默认跟随系统区域编码（cp936），
                         # 解释器路径带中文时一解码就炸。creationflags 见
                         # engine/runtime.py——GUI 子系统进程不该弹控制台黑框。
                         encoding="utf-8", errors="replace", timeout=30,
                         stdin=sp.DEVNULL,
                         creationflags=engine_runtime.CREATE_NO_WINDOW)
            mpl = out.stdout.strip() or None
        except (OSError, sp.TimeoutExpired):
            mpl = None
        src = engine_pool.source_of(py)
        checks.append({"id": "worker_python", "ok": True,
                       "label": "渲染引擎 Python",
                       "detail": f"{py}（{engine_pool.SOURCE_LABELS.get(src, src)}）"})
        checks.append({"id": "matplotlib", "ok": mpl is not None,
                       "label": "matplotlib",
                       "detail": mpl or "无法导入（渲染将不可用）"})
    except engine_pool.WorkerError as exc:
        checks.append({"id": "worker_python", "ok": False,
                       "label": "渲染引擎 Python", "detail": str(exc)})

    rt = engine_runtime.status()
    # 只在「本该有」或「确实有一套好的」时才报这一项。开发机上放着一份交叉
    # 构建出来的 Windows runtime（在 macOS 上当然跑不起来）不该被算成故障。
    if rt["valid"] or engine_runtime.ships_bundled_runtime():
        info = rt.get("manifest") or {}
        pkgs = info.get("packages") or {}
        checks.append({
            "id": "bundled_runtime", "ok": rt["valid"],
            "label": "内置渲染环境",
            "detail": (f"Python {(info.get('python') or {}).get('version')}"
                       f" + {len(pkgs)} 个包" if rt["valid"]
                       else rt.get("error") or "缺失"),
        })

    caps = engine_ai.capabilities()
    for name in ("codex", "claude"):
        p = caps["providers"][name]
        checks.append({"id": f"cli_{name}", "ok": p["installed"],
                       "label": f"{name.capitalize()} CLI",
                       "detail": p["version"] or "未安装（改图助手对应选项不可用）"})

    ctx = _request_ctx()
    if ctx is not None:
        root = ctx.path
        checks.append({"id": "project_readable", "ok": root.is_dir(),
                       "label": "项目目录可读", "detail": str(root)})
        checks.append({"id": "project_writable",
                       "ok": os.access(root, os.W_OK),
                       "label": "项目目录可写（写回原始文件需要）",
                       "detail": str(root)})
        try:
            _cfg, rep = engine_discover.build_draft(root)
            n = len(rep.get("conflicts") or [])
            checks.append({"id": "registry_conflicts", "ok": n == 0,
                           "label": "注册表 stem 归属",
                           "detail": "无冲突" if n == 0 else
                           f"{n} 个 stem 归属冲突: {', '.join(sorted(rep['conflicts']))}"})
        except Exception as exc:  # noqa: BLE001 — 诊断本身不能炸
            checks.append({"id": "registry_conflicts", "ok": False,
                           "label": "注册表 stem 归属", "detail": str(exc)})
    else:
        checks.append({"id": "project_open", "ok": False,
                       "label": "项目", "detail": "尚未打开项目"})

    resp = jsonify({"checks": checks})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/diagnostics/bundle")
def api_diagnostics_bundle():
    """一键诊断包（zip）。密钥与个人路径已脱敏，见 engine/diagnostics.py。

    剩下那些没法提前覆盖的 bug，来回问十次才能定位一次；有了这个包，
    用户点一下发过来就够了。
    """
    ctx = _request_ctx()
    data = engine_diagnostics.build_bundle(
        project=project_status(ctx), port=request.host.rsplit(":", 1)[-1])
    name = f"magplot-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(data, mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store",
    })


@app.get("/api/project")
def api_project():
    """本标签页当前指向的项目（?pj= 决定；不带就是默认项目）。"""
    resp = jsonify(project_status(_request_ctx()))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/shutdown")
def api_shutdown():
    """受控退出（仅当环境变量 MAGPLOT_ALLOW_SHUTDOWN 打开时可用）。

    存在的理由只有一个：端到端冒烟要验证**干净退出**——关掉窗口之后
    worker 子进程必须一起收掉，不能在用户机器上留一堆僵尸 python.exe。
    默认关闭，免得本地应用平白多一个「任何网页都能把它关掉」的入口。
    """
    if not os.environ.get("MAGPLOT_ALLOW_SHUTDOWN"):
        abort(404)
    LOG.info("收到关闭请求，正在收尾")
    reset_projects(wait=True)  # 停 watcher + 关 worker（等它们真的收完）
    engine_ai.interrupt_all()  # AI 任务终止，快照保留

    def _bye():
        time.sleep(0.3)        # 先把响应送出去
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
        entries.append({**e, "exists": p.is_dir(),
                        "id": open_paths.get(str(p)),
                        "opened": str(p) in open_paths,
                        "current": current is not None and p == current.path})
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
        return jsonify({"error": "缺少项目路径"}), 400
    p = Path(raw).expanduser()
    if body.get("create"):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return jsonify({"error": f"无法创建目录: {exc}"}), 400
    try:
        return jsonify(open_project(str(p), make_default=body.get("default", True)))
    except (RuntimeError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


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
    for label, name in (("桌面", "Desktop"), ("文档", "Documents"),
                        ("下载", "Downloads")):
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
        return jsonify({"path": "@roots", "parent": None, "is_roots": True,
                        "dirs": roots, "roots": roots,
                        "shortcuts": _browse_shortcuts()})
    if not raw:
        raw = str(Path.home())
    try:
        p = Path(raw).expanduser()
        p = p.resolve() if p.exists() else Path(os.path.abspath(str(p)))
    except (OSError, ValueError):
        return jsonify({"error": "路径无效"}), 400
    if not p.is_dir():
        # 找一个还存在的祖先，前端可以一键跳过去继续找
        near = p
        while near != near.parent and not near.is_dir():
            near = near.parent
        return jsonify({"error": f"目录不存在: {p}",
                        "nearest": str(near) if near.is_dir() else None}), 400
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if not child.is_dir():
                    continue
            except OSError:        # 断开的网络驱动器 / 权限受限的符号链接
                continue
            dirs.append({"name": child.name, "path": str(child)})
    except PermissionError:
        return jsonify({"error": f"无权限读取: {p}"}), 403
    except OSError as exc:
        return jsonify({"error": f"无法读取: {exc}"}), 400
    # 盘符根的上一级是「此电脑」那一层虚拟根，不是它自己
    parent = str(p.parent) if p != p.parent else ("@roots" if os.name == "nt" else None)
    return jsonify({"path": str(p), "parent": parent, "is_roots": False,
                    "dirs": dirs, "roots": roots,
                    "shortcuts": _browse_shortcuts(),
                    "writable": os.access(p, os.W_OK)})


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
        return jsonify({"error": f"扫描失败: {exc}"}), 400
    registered_stems = {s for c in reg.values() for s in c["stems"]}
    candidates = []
    for script, info in sorted(rep["scripts"].items()):
        fresh = [s for s in info["stems"] if s not in registered_stems]
        # 已登记且没有新产物就不再列为「未登记」——包括那些静态解不出文件名的
        # 脚本（它们已经靠试运行登记过了，再列一遍只会自相矛盾）。需要重新
        # 探测时从「已登记」那一栏走。
        if script in reg and not fresh:
            continue
        candidates.append({"script": script, **info, "new_stems": fresh,
                           "registered": script in reg})
    return jsonify({"source": ctx.registry.source(), "scripts": reg,
                    "candidates": candidates,
                    "conflicts": rep["conflicts"]})


@app.post("/api/registry/scan")
def api_registry_scan():
    """重跑静态扫描并合并进 mm_registry.json（现有条目永远优先）。"""
    ctx = current_ctx()
    try:
        cfg, rep, changes = engine_discover.merge(ctx.path)
        engine_discover.write_config(ctx.path, cfg)
    except (OSError, ValueError, RuntimeError) as exc:
        return jsonify({"error": f"扫描失败: {exc}"}), 400
    reload_registry(ctx)
    return jsonify({"changes": changes, "conflicts": rep["conflicts"],
                    "scripts": ctx.registry.entries()})


@app.post("/api/registry/probe")
def api_registry_probe():
    """试运行一个脚本，按**真实产出**的文件名登记 stem。

    静态解不出文件名的脚本（stem 来自数据目录 / 命令行）只有这条路。
    脚本跑得起来 = 能参数化，不用再让用户手改 JSON 猜自己该写什么。
    同步阻塞：冷启动秒级到分钟级，前端逐个脚本调用即可看到进度。
    """
    ctx = current_ctx()
    body = request.get_json(force=True)
    script = str(body.get("script") or "").strip()
    target = (ctx.path / script).resolve() if script else ctx.path
    # 只允许跑图库目录内的 .py：这个端点会真的执行代码，越权必须挡死
    if (not script or target.suffix != ".py" or not target.is_file()
            or not target.is_relative_to(ctx.path.resolve())):
        return jsonify({"error": "脚本不存在或不在项目目录内"}), 404
    result = engine_probe.probe_and_register(
        ctx.path, script, cost=str(body.get("cost") or "medium"))
    if result.get("registered"):
        reload_registry(ctx)
        sse_publish("registry.changed", {"pj": ctx.id, "script": script,
                                         "stems": result["stems"]})
    return jsonify(result)


@app.put("/api/registry")
def api_registry_write():
    """手工裁决：直接写一条脚本的 stem 归属（冲突仲裁、改 entry/cost）。"""
    ctx = current_ctx()
    body = request.get_json(force=True)
    script = str(body.get("script") or "").strip()
    if not script:
        return jsonify({"error": "缺少脚本名"}), 400
    entry = str(body.get("entry") or "main")
    if not engine_registry.valid_entry(entry):
        return jsonify({"error": f"entry 非法: {entry}"}), 400
    stems = [str(s).strip() for s in (body.get("stems") or []) if str(s).strip()]
    try:
        engine_discover.register(ctx.path, script, stems, entry=entry,
                                 cost=str(body.get("cost") or "medium"),
                                 notes=str(body.get("notes") or ""))
        reload_registry(ctx)
    except (OSError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    sse_publish("registry.changed", {"pj": ctx.id, "script": script, "stems": stems})
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
                    return jsonify({"error": f"{key} 不可用: {exc}"}), 400
                patch[key] = str(d)
            else:
                patch[key] = None
    if "allow_write_back" in body:
        patch["allow_write_back"] = bool(body["allow_write_back"])
    merged = engine_config.set_project_settings(str(root), patch)
    return jsonify({"settings": merged, **{
        "export_dir": str(project_export_dir()),
        "backup_dir": str(project_backup_dir()),
    }})


# ------------------------- 参数化渲染引擎 ----------------------------------
def _engine_worker(rel_id: str):
    """面板 id → (worker, stem)；非脚本面板 404。"""
    path = safe_resolve(rel_id)
    info = current_registry().for_stem(path.stem)
    if info is None:
        abort(404)
    return engine_pool.get(info["script"], str(require_project()), info["entry"]), path.stem


@app.post("/api/engine/render")
def api_engine_render():
    """应用全量 override 列表并重渲染，返回新 manifest 与版本号。

    首次调用会触发脚本 build（fig9 数秒；heavy 脚本 Phase 1 处理异步化）。
    """
    body = request.get_json(force=True)
    rel_id = body.get("id", "")
    worker, stem = _engine_worker(rel_id)
    info = current_registry().for_stem(Path(stem).stem) or {}
    cold = not worker.built
    # 三个事件都得带 pj：前端 renderStore 按 fileId 索引且不分项目，不带的话
    # 另一个标签页里同名的面板（到处都是的 Fig1.pdf）会跟着显示「正在构建…」
    pj = current_ctx().id
    sse_publish("render.started",
                {"pj": pj, "id": rel_id, "cost": info.get("cost", ""), "cold": cold})
    t0 = time.time()
    try:
        resp = worker.override(stem, body.get("patches", []))
    except engine_pool.WorkerError as exc:
        LOG.error("引擎渲染失败: %s: %s", stem, exc)
        sse_publish("render.failed", {"pj": pj, "id": rel_id, "error": str(exc)})
        return jsonify(_worker_error_payload(exc)), 500
    LOG.info("引擎渲染: %s %.0fms%s", stem, (time.time() - t0) * 1000,
             "（冷启动）" if cold else "")
    sse_publish("render.done", {"pj": pj, "id": rel_id, "rev": worker.rev})
    return jsonify({
        "rev": worker.rev,
        "manifest": resp["manifest"],
        "warnings": resp.get("warnings", []),
    })


@app.get("/api/engine/png")
def api_engine_png():
    """当前 override 状态下的高清位图（bucket 宽度）——含 imshow 的面板显示不糊。"""
    worker, stem = _engine_worker(request.args.get("id", ""))
    want_w = int(request.args.get("w", 800))
    w = next((b for b in RENDER_BUCKETS if b >= want_w), RENDER_BUCKETS[-1])
    try:
        path = worker.render_png(stem, w)
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
    annotations = [a for a in (body.get("annotations") or [])
                   if isinstance(a, dict)
                   and a.get("type") in ("text", "arrow", "shape")]
    src = safe_resolve(rel_id)
    if annotations and not src.with_suffix(".pdf").exists():
        return jsonify({"error": "该素材只有位图、没有矢量 PDF，"
                                 "暂不支持把标注写回原图",
                        "code": "annotations_need_pdf"}), 400
    info = current_registry().for_stem(src.stem)
    if info is None:
        return jsonify({"error": "该面板不可参数化（没有对应脚本）"}), 404
    worker = engine_pool.get(info["script"], str(require_project()), info["entry"])
    try:
        result = _write_source_files(src, patches, worker,
                                     annotations=annotations,
                                     expected_mtime=body.get("expected_mtime"))
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    except (SourceChangedError, ScriptChangedError, ReplayDivergenceError,
            WriteBackVerifyError, FileLockedError) as exc:
        return _write_back_error_response(exc)
    # 把这组修改追加为该图的版本历史，末位即当前基线：
    # 新拖入的同名面板自动继承，双击进编辑态能接着改
    append_baked(src.stem, patches)
    return jsonify(_write_back_response(result, baked=bool(patches)))


def _write_back_forbidden():
    """项目被设为只读时拒绝一切「写回原始文件」类操作；返回错误响应或 None。"""
    st = engine_config.project_settings(str(require_project()))
    if st.get("allow_write_back") is False:
        return jsonify({"error": "该项目已设为只读：不允许写回原始文件"
                                 "（可在项目设置中恢复可写）",
                        "code": "write_back_disabled"}), 403
    return None


class FileLockedError(RuntimeError):
    """目标文件被别的程序占用，替换不了（Windows 上的独占锁）。

    第 2 个目标撞锁时**先把已经换掉的从备份恢复回去**：一张图的 PDF 与 PNG
    分岔（矢量是新的、位图还是旧的）比整件事失败糟糕得多——用户在画布上看到
    的是位图，投出去的是矢量。恢复成功 = 原文件一个字节都没变；恢复也失败
    （备份也被锁）才退回「部分完成」的如实报告。
    """

    def __init__(self, name: str, detail: str, updated: list[str],
                 rolled_back: list[str] | None = None,
                 rollback_failed: list[str] | None = None):
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
    return (f"写回前自检未通过，原文件未做任何修改：{exc}。"
            "这些元素/属性没能应用到图上——通常是脚本改过了（元素的 gid 变了或"
            "已删除）。请重新渲染确认当前效果，或撤销对应的修改后再写回。")


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
    text = json.dumps(man, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compare_manifests(hot: dict, fresh: dict) -> tuple[list[dict], int]:
    """热态 manifest vs 干净重放 manifest → (分歧清单, 逐项比过的元素数)。

    比 gid 集合、每个元素的 bbox/anchor、以及 figure 的 size_mm。只比几何：
    文本内容之类的属性差异会被 worker 自己的 warning 抓到，而**位置**恰恰是
    热会话增量应用与全量重放最容易分岔的地方（FigS3 事故就是这条）。
    """
    diffs: list[dict] = []
    hot_size, fresh_size = _vec(hot.get("size_mm"), 2), _vec(fresh.get("size_mm"), 2)
    if hot_size is None or fresh_size is None:
        if hot.get("size_mm") != fresh.get("size_mm"):
            diffs.append({"gid": "", "field": "size_mm",
                          "hot": hot.get("size_mm"), "fresh": fresh.get("size_mm")})
    elif any(abs(a - b) > REPLAY_SIZE_TOL for a, b in zip(hot_size, fresh_size)):
        diffs.append({"gid": "", "field": "size_mm",
                      "hot": hot_size, "fresh": fresh_size})

    hot_els = {el.get("gid"): el for el in hot.get("elements", []) if el.get("gid")}
    fresh_els = {el.get("gid"): el for el in fresh.get("elements", []) if el.get("gid")}
    for gid in sorted(set(hot_els) - set(fresh_els)):
        diffs.append({"gid": gid, "field": "missing_in_replay",
                      "hot": "存在", "fresh": None})
    for gid in sorted(set(fresh_els) - set(hot_els)):
        diffs.append({"gid": gid, "field": "missing_in_hot",
                      "hot": None, "fresh": "存在"})

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
        return          # 会话没记指纹（读不到脚本）：没有可比的基准，不臆断
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
    if getattr(worker, "last_patch_hash", "") != want:
        return None
    path = Path(worker.out_dir) / f"{stem}.json"
    if not path.exists():
        try:
            worker.override(stem, patches)   # 同一组 patches，幂等
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
            LOG.error("写回回滚失败: %s（备份在 %s）", target.name, backup_dir,
                      exc_info=True)
            failed.append(target.name)
    return rolled, failed


def _write_source_files(src: Path, patches: list, worker,
                        annotations: list | None = None,
                        expected_mtime=None) -> dict:
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
        for w in (resp.get("warnings") or []):
            if w not in warnings:
                warnings.append(str(w))
        man_fresh = json.loads(
            (Path(fresh.out_dir) / f"{stem}.json").read_text(encoding="utf-8"))
        if man_hot is None:
            diffs, compared = [], 0
        else:
            diffs, compared = _compare_manifests(man_hot, man_fresh)

        for target in targets:
            tmp = target.with_name(f".{target.name}.updating")
            tmps.append((target, tmp))   # 先登记再导出：中途抛了也要清得掉
            eresp = fresh.export(stem, patches, str(tmp),
                                 fmt=target.suffix.lstrip("."), dpi=600)
            for w in (eresp.get("warnings") or []):
                if w not in warnings:    # PDF/PNG 两次导出报的是同一批
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
                leftover.unlink(missing_ok=True)   # 不给图库留下半成品
            LOG.warning("写回原图失败（文件被占用？）: %s: %s", target.name, exc)
            rolled, failed = _rollback(done, backup_dir)
            raise FileLockedError(target.name, str(exc), failed, rolled, failed) from exc
        done.append(target)
        updated.append(target.name)
    prune_backups(backup_dir.parent)
    LOG.info("更新原图: %s → %s（备份 %s，标注 %d 条）",
             stem, updated, backup_dir.name, len(annotations or []))

    verification = {
        "replay": "ok" if man_hot is not None else "fresh_only",
        "elements": compared,
    }
    if man_hot is None:
        # 没比 ≠ 没验：staging 本来就出自干净重放，只是没有可对照的热态基准
        verification["reason"] = "hot_state_differs"
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
    except Exception:                        # noqa: BLE001 — 自检读不动不算失败
        LOG.warning("写回后尺寸自检读不出 PDF: %s", pdf, exc_info=True)
        return ""
    if any(abs(a - b) > POST_CHECK_SIZE_TOL for a, b in zip(got, want)):
        LOG.error("写回后尺寸自检不符: %s 实际 %.2f×%.2fmm，manifest %.2f×%.2fmm",
                  pdf.name, got[0], got[1], want[0], want[1])
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
        return jsonify({
            "error": f"{exc.name} 已被外部修改（本工具之外），写回已取消，"
                     "原文件未做任何改动。请刷新素材面板后重新确认再写回。",
            "code": "source_changed", "file": exc.name,
            "expected": exc.expected, "actual": exc.actual}), 409
    if isinstance(exc, ScriptChangedError):
        return jsonify({
            "error": f"生成这张图的脚本 {exc.script} 在本次会话开始后被改动过，"
                     "当前渲染的仍是旧代码，写回已取消（原文件未做任何改动）。"
                     "请重新渲染该面板确认效果后再写回。",
            "code": "script_changed", "script": exc.script}), 409
    if isinstance(exc, ReplayDivergenceError):
        return jsonify({
            "error": "热编辑状态与全新重放不一致，写回已阻断，原文件未做任何改动。"
                     f"分歧：{exc}。这属于引擎级问题，请把此信息报告给开发者。",
            "code": "replay_divergence", "diffs": exc.diffs}), 409
    if isinstance(exc, WriteBackVerifyError):
        return jsonify({"error": _write_back_warning_error(exc),
                        "code": "write_back_warnings",
                        "warnings": exc.warnings}), 409
    if isinstance(exc, FileLockedError):
        # 可操作的错误：告诉用户是哪个文件、该去关掉谁；回滚结果一并报出来，
        # 免得用户以为「什么都没发生」或者反过来以为「已经写进去了」
        return jsonify({"error": f"{exc}。请关闭正在打开它的程序"
                                 "（PDF 阅读器 / 看图工具）后重试。",
                        "code": "file_locked", "file": exc.name,
                        "updated": exc.updated,
                        "rolled_back": exc.rolled_back,
                        "rollback_failed": exc.rollback_failed}), 409
    return None


# ---- 组图 ↔ 子图 override 同步 ----------------------------------------------
_SYNC_SKIP = {"position", "size_mm"}      # 版面几何不跨图搬
_SYNC_POINT = {"pos_frac", "loc_frac"}    # 点位经 axes 框换算后可搬


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
        return jsonify({"error": "两张图不属于同一个脚本，无法同步"}), 400
    worker = engine_pool.get(info_s["script"], str(require_project()), info_s["entry"])
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
    return jsonify({"versions": [
        {"n": i, "ts": v.get("ts", ""), "count": len(v["patches"]),
         "patches": v["patches"]}
        for i, v in enumerate(versions)
    ]})


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
    worker, stem = _engine_worker(body.get("id", ""))
    n = int(body.get("n", -1))
    versions = load_baked().get(stem, {}).get("versions") or []
    patches = [] if n < 0 or n >= len(versions) else versions[n]["patches"]
    src = safe_resolve(body.get("id", ""))
    try:
        result = _write_source_files(src, patches, worker,
                                     expected_mtime=body.get("expected_mtime"))
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    except (SourceChangedError, ScriptChangedError, ReplayDivergenceError,
            WriteBackVerifyError, FileLockedError) as exc:
        return _write_back_error_response(exc)
    append_baked(stem, patches)
    return jsonify(_write_back_response(result, patches=patches))


@app.get("/api/engine/svg")
def api_engine_svg():
    """当前 override 状态下的预览 SVG（元素带 gid）。"""
    worker, stem = _engine_worker(request.args.get("id", ""))
    try:
        if not worker.built:
            worker.ensure_built()
    except engine_pool.WorkerError as exc:
        return jsonify(_worker_error_payload(exc)), 500
    svg = worker.svg_path(stem)
    if not svg.exists():
        abort(404)
    resp = send_file(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------- AI 桥 -------------------------------------------
@app.get("/api/ai/capabilities")
def api_ai_capabilities():
    """实测本机 codex / claude 的安装、版本、可用参数，以及已配置的第三方接口。"""
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    resp = jsonify(engine_ai.capabilities(refresh=refresh))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/ai/install")
def api_ai_install():
    """一键 `npm install -g` 装 CLI（后台线程）；agent 只认 codex/claude。"""
    agent = str((request.get_json(silent=True) or {}).get("agent") or "")
    if agent not in engine_ai.NPM_PACKAGES:
        return jsonify({"error": f"未知 agent: {agent}"}), 400
    return jsonify(engine_ai.start_install(agent))


@app.get("/api/ai/install/status")
def api_ai_install_status():
    agent = str(request.args.get("agent") or "")
    if agent not in engine_ai.NPM_PACKAGES:
        return jsonify({"error": f"未知 agent: {agent}"}), 400
    resp = jsonify(engine_ai.install_status(agent))
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
        names = ([n.strip() for n in probe.split(",") if n.strip()]
                 if probe not in ("1", "true", "yes") else None)
        py = st.get("python")
        st["imports"] = engine_runtime.probe_packages(py, names) if py else {}
    resp = jsonify(st)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/engine/environment/install")
def api_engine_environment_install():
    """在 Magplot 自己的数据目录里建一个 venv 并装 matplotlib。

    绝不动用户已有的环境——那是他做研究用的。进度经 SSE `engine.bootstrap` 推送。
    """
    st = engine_bootstrap.status()
    if st["ok"]:
        return jsonify({"ok": True, **st})
    if st.get("runtime", {}).get("expected"):
        # 桌面版自带渲染环境，缺了就是安装文件不完整——现场联网建 venv 只会
        # 把一个包装问题伪装成用户的环境问题
        return jsonify({"error": engine_runtime.repair_hint(),
                        "code": st.get("code")}), 400
    if not st.get("can_install"):
        return jsonify({"error": "这台机器上没找到可用的 Python，"
                                 "请先安装 Python 3.10 以上再重试。"}), 400
    engine_bootstrap.install_async(
        lambda p: sse_publish("engine.bootstrap", p))
    return jsonify({"started": True, **engine_bootstrap.progress()})


@app.patch("/api/engine/environment")
def api_engine_environment_set():
    """手动指定渲染解释器；path 为空 = 清除，回到自动探测。"""
    body = request.get_json(force=True)
    raw = str(body.get("python") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_file():
            return jsonify({"error": f"找不到该文件: {p}"}), 400
        ver = engine_bootstrap.matplotlib_version(str(p))
        if not ver:
            return jsonify({"error": f"{p} 里 import 不到 matplotlib"}), 400
        engine_config.set_worker_python(str(p))
    else:
        engine_config.set_worker_python(None)
    engine_pool.reset_worker_python()
    return jsonify(engine_bootstrap.status())


# ------------------------- 检查更新 -----------------------------------------
def _updater_disabled_in_desktop():
    """桌面模式下 Python updater 整个停用（升级归 Tauri 层，避免两套升级机制）。
    回禁用响应或 None（浏览器 / CLI 模式照旧）。"""
    if app.config.get("MAGPLOT_DESKTOP_MODE"):
        # 带上 Releases 地址：界面据此显示「去下载新安装包」，
        # 而不是留一个永远没有结果的「立即检查」死按钮
        return jsonify({"desktop": True, "auto_check": False,
                        "update_available": False,
                        "current": engine_updater.current_version(),
                        "repo_url": engine_brand.REPO_URL,
                        "releases_url": engine_brand.RELEASES_URL})
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
    if app.config.get("MAGPLOT_DESKTOP_MODE"):
        return jsonify({"error": "桌面版内不支持 pip 自升级，请更新桌面应用",
                        "code": "desktop_updater_disabled"}), 409
    result = engine_updater.apply_upgrade()
    LOG.info("升级 %s: %s", "成功" if result["ok"] else "失败", result["command"])
    return jsonify(result), (200 if result["ok"] else 500)


@app.patch("/api/ai/settings")
def api_ai_settings():
    """AI CLI 的用户级设置（自定义可执行路径）；改完立即重探测。"""
    body = request.get_json(force=True)
    patch = {}
    for key in ("codex_path", "claude_path"):
        if key in body:
            val = str(body[key] or "").strip()
            patch[key] = val or None
    merged = engine_config.set_ai_settings(patch)
    return jsonify({"settings": {k: v for k, v in merged.items()
                                 if k not in ("providers",)},
                    **engine_ai.capabilities(refresh=True)})


@app.put("/api/ai/endpoints")
def api_ai_endpoint_save():
    """新增/更新一个第三方接口。api_key 留空 = 保留原值（界面不回显密钥）。"""
    body = request.get_json(force=True)
    if not str(body.get("label") or body.get("id") or "").strip():
        return jsonify({"error": "缺少名称"}), 400
    try:
        engine_ai_providers.save(body)
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
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
        engine_ai_providers.set_active(str(body.get("agent") or ""),
                                       body.get("id") or None)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
        return jsonify({"error": "该面板不可参数化（没有对应脚本）"}), 404
    context = {"stem": path.stem, "gid": body.get("gid"),
               "label": body.get("label"), "overrides": body.get("overrides"),
               "scope": body.get("scope"), "target": body.get("target"),
               "canvas": body.get("canvas")}
    try:
        sid = engine_ai.run(agent, info["script"], prompt, str(require_project()),
                            context=context, on_event=sse_publish,
                            model=body.get("model") or None,
                            effort=body.get("effort") or None,
                            endpoint_id=body.get("endpoint"))
    except RuntimeError as exc:
        LOG.error("AI 任务启动失败: %s %s: %s", agent, info["script"], exc)
        return jsonify({"error": str(exc)}), 500
    LOG.info("AI 任务启动: %s %s（session %s）", agent, info["script"], sid)
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
        offset=max(int(request.args.get("offset", 0)), 0))
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
        return jsonify({"error": str(exc)}), 400


@app.post("/api/ai/sessions/<sid>/cancel")
def api_ai_cancel(sid):
    return jsonify({"ok": engine_ai.cancel(sid)})


# ------------------------- 布局的保存 / 读取 -------------------------------
def project_layout_dir(ctx: "ProjectCtx | None" = None) -> Path:
    """命名画布文件的目录：项目内 `magplotfile/`（项目文件的统一收纳处）。

    成品画布要跟图库一起被找到 / 备份 / 同步，藏在应用数据目录里对用户
    等于不存在。未打开项目时退回数据目录 layouts/（纯文字/形状排版不依赖
    项目）；旧位置（项目 `canvases/`、数据目录 layouts/）只读兼容
    （api_layouts 合并列出，保存后以 magplotfile/ 里的为准）。"""
    ctx = ctx if ctx is not None else _request_ctx()
    store = project_store_dir(ctx)
    return LAYOUT_DIR if store is None else store


def _layout_read_dirs() -> list[Path]:
    """读画布的查找顺序：magplotfile/ → 旧项目 canvases/ → 数据目录 layouts/。"""
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
    return (base if base is not None else LAYOUT_DIR) / f"{name}.json"


@app.get("/api/layouts")
def api_layouts():
    # 主位置 = 项目 magplotfile/；旧位置只读兼容，重名以主位置为准
    seen: dict[str, float] = {}
    for d in _layout_read_dirs():
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
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
    d = project_layout_dir()
    d.mkdir(parents=True, exist_ok=True)
    layout_path(name, d).write_text(
        json.dumps(request.get_json(force=True), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return jsonify({"ok": True})


# ------------------------- 文档自动保存（磁盘） ------------------------------
# 文档主体的可靠落盘：localStorage 只留轻量索引与崩溃兜底副本。
# 原子写（tmp + replace），按前端 documentId 一档。
AUTOSAVE_DIR = LAYOUT_DIR / "_autosave"


def _autosave_path(doc_id: str) -> Path:
    doc_id = re.sub(r"[^\w\-]+", "_", doc_id)
    if not doc_id:
        abort(400)
    return AUTOSAVE_DIR / f"{doc_id}.json"


@app.get("/api/autosave/<doc_id>")
def api_autosave_get(doc_id):
    p = _autosave_path(doc_id)
    if not p.is_file():
        abort(404)
    resp = send_file(p, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
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


@app.put("/api/autosave/<doc_id>")
def api_autosave_put(doc_id):
    body = request.get_json(force=True)
    if not isinstance(body, dict) or body.get("schema") not in (2, 3):
        return jsonify({"error": "无效的文档（需要 schema 2 或 3）"}), 400
    p = _autosave_path(doc_id)
    theirs = _autosave_newer_than(p, request.args.get("base"))
    if theirs is not None:
        return jsonify({
            "code": "stale_write",
            "theirs": theirs,
            "error": "该文档已在其他窗口保存了更新的版本",
        }), 409
    AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return jsonify({"ok": True, "saved_at": int(time.time() * 1000)})


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
VERSIONS_DIR = LAYOUT_DIR / "_versions"   # 旧位置：只读兼容（新写入进项目 magplotfile/versions/）
_VERSIONS_LOCK = threading.Lock()
VERSION_KEEP_AUTO = 40    # 自动检查点保留数
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
    p = _versions_path(doc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"versions": versions}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(p)


def _prune_versions(versions: list[dict]) -> list[dict]:
    autos = [v for v in versions if v.get("auto")]
    if len(autos) > VERSION_KEEP_AUTO:
        drop = {id(v) for v in autos[: len(autos) - VERSION_KEEP_AUTO]}
        versions = [v for v in versions if id(v) not in drop]
    return versions[-VERSION_KEEP_TOTAL:]


def _version_meta(v: dict) -> dict:
    doc = v.get("doc") or {}
    return {
        "id": v["id"], "name": v.get("name", ""), "ts": v.get("ts", 0),
        "auto": bool(v.get("auto")), "description": v.get("description", ""),
        "objects": len(_doc_objects(doc)),
        "page": doc.get("page"),
    }


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
    doc = body.get("doc")
    if not isinstance(doc, dict) or doc.get("schema") not in (2, 3):
        return jsonify({"error": "无效的文档快照（需要 schema 2 或 3）"}), 400
    ver = {
        "id": _new_version_id(),
        "name": str(body.get("name") or "").strip()
                or time.strftime("%m-%d %H:%M"),
        "ts": int(time.time() * 1000),
        "auto": bool(body.get("auto")),
        "description": str(body.get("description") or ""),
        "doc": doc,
    }
    with _VERSIONS_LOCK:
        versions = _load_versions(doc_id)
        # 自动检查点若与最近一版内容相同则跳过（刷新/空转不该刷版本）
        if ver["auto"] and versions:
            last = versions[-1]
            if json.dumps(last.get("doc"), sort_keys=True) == \
                    json.dumps(doc, sort_keys=True):
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
                copy = {**v, "id": _new_version_id(),
                        "name": f"{v.get('name', '')} 副本",
                        "ts": int(time.time() * 1000), "auto": False}
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
STYLES_PATH = LAYOUT_DIR / "_styles.json"
_STYLES_LOCK = threading.Lock()


def _load_styles() -> list[dict]:
    try:
        data = json.loads(STYLES_PATH.read_text(encoding="utf-8"))
        return data.get("styles", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save_styles(styles: list[dict]) -> None:
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STYLES_PATH.with_name(STYLES_PATH.name + ".tmp")
    tmp.write_text(json.dumps({"styles": styles}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(STYLES_PATH)


@app.get("/api/styles")
def api_styles_list():
    return jsonify({"styles": _load_styles()})


@app.post("/api/styles")
def api_styles_save():
    body = request.get_json(force=True)
    if not isinstance(body, dict) or not str(body.get("name") or "").strip():
        return jsonify({"error": "样式需要一个名称"}), 400
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


def magplot_is_serving(port: int) -> bool:
    """占着这个端口的是不是另一个 Magplot（而不是别的程序）。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/version", timeout=1.5) as resp:
            return "build" in json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def resolve_port(preferred: int, tries: int = 20) -> int | None:
    """要用的端口；None = 该端口上已经有一个 Magplot 在跑，不必再起。

    被别的程序占用时顺延找下一个空闲端口——双击启动的应用不能因为端口冲突就
    一声不响地退出（窗口化打包下用户连 traceback 都看不到）。
    """
    if port_is_free(preferred):
        return preferred
    if magplot_is_serving(preferred):
        return None
    for p in range(preferred + 1, preferred + 1 + tries):
        if port_is_free(p):
            return p
    return preferred          # 全占满了：交给 app.run 报错，至少日志里有据可查


def main():
    # 启动信息里有中文。Windows 上 stdout 一旦不是真控制台（被重定向到文件、
    # 由启动器接管管道）就退回系统区域编码，print 会 UnicodeEncodeError 直接
    # 打死进程——用户看到的是「启动即崩」，却查不出原因。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--figures", default=None,
                    help="面板图所在目录（缺省恢复最近打开的项目）")
    ap.add_argument("--port", type=int, default=5089)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--desktop-sidecar", action="store_true",
                    help="作为 Magplot 桌面应用的后端运行：127.0.0.1 动态端口 + "
                         "桌面认证 + 父进程跟随退出（由桌面壳启动，不建议手动使用）")
    args = ap.parse_args()

    setup_logging()
    threading.Thread(target=prune_render_cache, daemon=True,
                     name="mm-cache-prune").start()  # 启动清一次历史存量
    # 引擎会话缓存同理：get() 里的触发点只在新建会话时走，长开不新建的实例靠这次
    threading.Thread(target=engine_pool.prune_engine_cache, daemon=True,
                     name="mm-engine-cache-prune").start()
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
                print("* 未找到注册表，已静态扫描生成草稿"
                      "（cost 默认 medium，请按需修正）")
            if st.get("conflicts"):
                print(f"  ⚠ {len(st['conflicts'])} 个 stem 归属冲突未分配，"
                      f"请在注册表中手工裁决: {', '.join(st['conflicts'])}")
        except (RuntimeError, OSError) as exc:
            print(f"* 无法打开项目 {candidate}: {exc}")
            print(f"* 请在{where}中选择或新建项目")
    else:
        print(f"* 尚未选择项目：请在{where}中新建或打开一个项目")

    if args.desktop_sidecar:
        sys.exit(desktop_mode.run(app))

    port = resolve_port(args.port)
    if port is None:
        # 端口上已经有一个 Magplot 在跑：把浏览器指过去就够了，别再起一个。
        # 双击应用图标的用户没有终端可看，这里必须自己把事办圆。
        url = f"http://127.0.0.1:{args.port}"
        print(f"* Magplot 已在 {url} 运行，打开现有窗口")
        if not args.no_browser:
            webbrowser.open(url)
        return

    url = f"http://127.0.0.1:{port}"
    if port != args.port:
        print(f"* 端口 {args.port} 被占用，改用 {port}")
    print(f"* 打开 {url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
