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

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from . import pdfbackend
from .pdfbackend import hex2rgb, mm2pt
from .engine import ai_bridge as engine_ai
from .engine import bootstrap as engine_bootstrap
from .engine import ai_history as engine_ai_history
from .engine import brand as engine_brand
from .engine import config as engine_config
from .engine import discover as engine_discover
from .engine import pool as engine_pool
from .engine import registry as engine_registry
from .engine import updater as engine_updater

PKG_ROOT = Path(__file__).resolve().parent   # 只读：包自带资源（前端构建产物）
DATA_ROOT = engine_config.data_dir()         # 可写：运行时产物（装成包后 site-packages 不可写）

EXCLUDE_DIRS = {"__pycache__", "_cache", "_palette_ref", "scripts", ".git"}
PDF_EXT = {".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg"}

MM_PER_PT = 25.4 / 72.0
RENDER_BUCKETS = [200, 400, 800, 1600, 3200]

app = Flask(__name__, static_folder=None)

# 当前项目的图库目录；None = 尚未打开项目（前端显示 Project Picker）。
# 不再内置任何默认路径——项目由 --figures、最近项目或 Picker 决定。
FIGURES_DIR: Path | None = None
CACHE_DIR = DATA_ROOT / "cache"
EXPORT_DIR = DATA_ROOT / "exports"
LAYOUT_DIR = DATA_ROOT / "layouts"
# 前端构建产物：装成包后随 wheel 落在 magplot/web/；源码树里还没打包，
# 回退到仓库的 web/dist（pnpm build 的默认输出），否则开发态首页 404。
WEB_DIST = PKG_ROOT / "web"
if not WEB_DIST.is_dir():
    WEB_DIST = PKG_ROOT.parent.parent / "web" / "dist"
BAKED_PATH = DATA_ROOT / "baked_overrides.json"  # stem → 「更新原图」时烙进文件的 override 基线
_BAKED_CACHE: dict = {}
_BAKED_LOCK = threading.Lock()  # append 是读-改-写；Flask threaded 下必须互斥

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


def load_baked() -> dict:
    """{stem: {"versions": [{"ts", "patches"}...]}}；末位 = 当前基线。"""
    try:
        data = json.loads(BAKED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for stem, v in list(data.items()):  # 迁移单版本旧格式
        if "patches" in v:
            data[stem] = {"versions": [{"ts": v.get("updated_at", ""),
                                        "patches": v["patches"]}]}
    return data


def append_baked(stem: str, patches: list) -> None:
    """读-改-写全程持锁；临时文件 + replace 原子落盘，读者不会撞见半个文件。"""
    with _BAKED_LOCK:
        data = load_baked()
        entry = data.setdefault(stem, {"versions": []})
        entry["versions"].append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "patches": patches})
        entry["versions"] = entry["versions"][-50:]
        tmp = BAKED_PATH.with_name(BAKED_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(BAKED_PATH)


def _baseline_patches(stem: str) -> list:
    versions = (_BAKED_CACHE.get(stem) or {}).get("versions") or []
    return versions[-1]["patches"] if versions else []


class NoProjectError(Exception):
    """当前没有打开的项目；API 层转成 409，前端据此显示 Project Picker。"""


def require_project() -> Path:
    if FIGURES_DIR is None:
        raise NoProjectError()
    return FIGURES_DIR


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
    global _BAKED_CACHE
    _BAKED_CACHE = load_baked()
    panels = []
    root = require_project().resolve()
    # os.walk 而不是 rglob：隐藏目录当场剪枝，不下探。图库里常有 .venv、
    # .git、工具留下的 .rendered/.qa_* 快照——它们既是噪音（素材库里塞满
    # page-1.png），爬进去还很慢。以 . 开头的文件同理（.DS_Store）。
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.startswith(".")]
        files += [Path(dirpath) / fn for fn in filenames if not fn.startswith(".")]
    files.sort()

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
                info = engine_registry.for_stem(p.stem)
                if info is not None:  # 可参数化面板：有产出它的 matplotlib 脚本
                    entry.update(script=info["script"], cost=info["cost"])
                    baseline = _baseline_patches(p.stem)
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
                info = engine_registry.for_stem(p.stem)
                if info is not None:  # fig1 等纯 PNG 素材脚本
                    entry.update(script=info["script"], cost=info["cost"])
                    baseline = _baseline_patches(p.stem)
                    if baseline:
                        entry["baked_overrides"] = baseline
            else:
                continue
        except Exception:
            continue
        panels.append(entry)
    return panels


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.errorhandler(NoProjectError)
def _no_project(_exc):
    return jsonify({"error": "尚未打开项目", "code": "no_project"}), 409


@app.errorhandler(engine_pool.WorkerError)
def _worker_error(exc):
    """worker 类错误统一带上 code。

    多数端点自己 catch 了，但 `_engine_worker()` 这类调用常落在 try 之外——
    没有这个处理器时它们会掉进通用 Exception 处理器，`code` 全丢，前端就分不出
    「缺渲染环境」（该给引导）和「脚本报错」（该给 traceback）。
    """
    LOG.error("worker 错误: %s %s: %s", request.method, request.path, exc)
    return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                    "code": exc.code}), 500


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


def _resolve_panel_source(o: dict, dpi: int) -> Path:
    """面板对象 → 待嵌入的源文件路径。带 override 的 ⚡ 面板先由引擎按全质量
    重渲染成临时 PDF，导出的永远是矢量而不是画布上的预览位图。"""
    path = safe_resolve(o["id"])
    overrides = o.get("overrides") or []
    if overrides:
        info = engine_registry.for_stem(path.stem)
        if info is not None:
            worker = engine_pool.get(info["script"], str(FIGURES_DIR), info["entry"])
            tmp = (engine_pool.ENGINE_CACHE / Path(info["script"]).stem
                   / "export" / f"{path.stem}.pdf")
            worker.export(path.stem, overrides, str(tmp), "pdf", dpi)
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

    for o in objects:
        if o.get("hidden"):
            continue
        try:
            canvas.place(o, dpi, _resolve_panel_source)
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
    LOG.info("导出: %s（%d 对象, %s, %.0fms）",
             [f["name"] for f in out_files], len(objects), formats,
             (time.time() - t0) * 1000)
    return jsonify({"files": out_files, "export_dir": str(out_dir)})


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
            info = engine_registry.for_stem(p.stem)
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
def _on_script_change(changed: list[str]) -> None:
    stems = [s for sc in changed for s in engine_registry.stems_of(sc)]
    sse_publish("panel.file_changed", {"scripts": changed, "stems": stems})


def project_status() -> dict:
    if FIGURES_DIR is None:
        return {"open": False}
    p = FIGURES_DIR
    return {
        "open": True,
        "figures_dir": str(p),
        "name": p.name,
        "exists": p.is_dir(),
        "writable": os.access(p, os.W_OK),
        "scripts": len(engine_registry.all_scripts()),
        "settings": engine_config.project_settings(str(p)),
        "export_dir": str(project_export_dir()),
        "backup_dir": str(project_backup_dir()),
    }


def open_project(path_str: str) -> dict:
    """校验并切换当前项目：停旧 watcher、关旧 worker、中断 AI 任务、换 registry。

    失败（目录不存在 / 注册表损坏）抛 RuntimeError，当前项目保持不变。
    """
    global FIGURES_DIR
    path = Path(path_str).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"目录不存在: {path}")
    drafted, conflicts = False, []
    try:
        engine_registry.load(path)
    except FileNotFoundError:
        cfg, rep = engine_discover.build_draft(path)
        engine_discover.write_config(path, cfg)
        engine_registry.load(path)
        drafted, conflicts = True, sorted(rep["conflicts"])
    # 旧项目运行态全部收掉：先停 watcher（不再轮询旧目录），再关 worker，
    # 再中断 AI 任务（快照保留，revert 仍可用）
    engine_pool.stop_watcher()
    engine_pool.shutdown_all()
    interrupted = engine_ai.interrupt_all()
    FIGURES_DIR = path
    engine_pool.start_watcher(str(path), engine_registry.all_scripts(),
                              _on_script_change)
    engine_config.touch_recent(str(path))
    LOG.info("项目已打开: %s（%d 个脚本%s）", path,
             len(engine_registry.all_scripts()),
             "，注册表为静态扫描草稿" if drafted else "")
    return {**project_status(), "drafted": drafted, "conflicts": conflicts,
            "ai_interrupted": interrupted}


def project_export_dir() -> Path:
    """当前项目的导出目录（项目设置可覆盖；缺省数据目录 exports/）。
    未打开项目时（纯文字/形状导出不依赖项目）直接用默认目录。"""
    if FIGURES_DIR is None:
        return EXPORT_DIR
    d = engine_config.project_settings(str(FIGURES_DIR)).get("export_dir")
    return Path(d).expanduser() if d else EXPORT_DIR


def project_backup_dir() -> Path:
    """「写回原始文件」的备份根目录（项目设置可覆盖）。"""
    default = CACHE_DIR / "original_backups"
    if FIGURES_DIR is None:
        return default
    d = engine_config.project_settings(str(FIGURES_DIR)).get("backup_dir")
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
                         capture_output=True, text=True, timeout=30)
            mpl = out.stdout.strip() or None
        except (OSError, sp.TimeoutExpired):
            mpl = None
        checks.append({"id": "worker_python", "ok": True,
                       "label": "渲染引擎 Python", "detail": py})
        checks.append({"id": "matplotlib", "ok": mpl is not None,
                       "label": "matplotlib",
                       "detail": mpl or "无法导入（渲染将不可用）"})
    except engine_pool.WorkerError as exc:
        checks.append({"id": "worker_python", "ok": False,
                       "label": "渲染引擎 Python", "detail": str(exc)})

    caps = engine_ai.capabilities()
    for name in ("codex", "claude"):
        p = caps["providers"][name]
        checks.append({"id": f"cli_{name}", "ok": p["installed"],
                       "label": f"{name.capitalize()} CLI",
                       "detail": p["version"] or "未安装（改图助手对应选项不可用）"})

    if FIGURES_DIR is not None:
        checks.append({"id": "project_readable", "ok": FIGURES_DIR.is_dir(),
                       "label": "项目目录可读", "detail": str(FIGURES_DIR)})
        checks.append({"id": "project_writable",
                       "ok": os.access(FIGURES_DIR, os.W_OK),
                       "label": "项目目录可写（写回原始文件需要）",
                       "detail": str(FIGURES_DIR)})
        try:
            _cfg, rep = engine_discover.build_draft(FIGURES_DIR)
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


@app.get("/api/project")
def api_project():
    resp = jsonify(project_status())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/projects/recent")
def api_projects_recent():
    entries = []
    for e in engine_config.recent_projects():
        p = Path(e["path"])
        entries.append({**e, "exists": p.is_dir(),
                        "current": FIGURES_DIR is not None
                        and p == FIGURES_DIR})
    resp = jsonify({"recent": entries})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/projects/open")
def api_projects_open():
    """打开（或 create=true 时先创建）一个项目目录并切换过去。"""
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
        return jsonify(open_project(str(p)))
    except (RuntimeError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/projects/remove")
def api_projects_remove():
    """从最近列表移除；绝不删除磁盘内容。"""
    body = request.get_json(force=True)
    return jsonify({"ok": engine_config.remove_recent(str(body.get("path") or ""))})


@app.get("/api/projects/browse")
def api_projects_browse():
    """服务器端目录列举（本地单用户应用的目录选择器；只列目录）。"""
    raw = request.args.get("path") or str(Path.home())
    try:
        p = Path(raw).expanduser().resolve()
    except OSError:
        return jsonify({"error": "路径无效"}), 400
    if not p.is_dir():
        return jsonify({"error": f"目录不存在: {p}"}), 400
    dirs = []
    try:
        for child in sorted(p.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            dirs.append({"name": child.name, "path": str(child)})
    except PermissionError:
        return jsonify({"error": f"无权限读取: {p}"}), 403
    return jsonify({"path": str(p),
                    "parent": str(p.parent) if p != p.parent else None,
                    "dirs": dirs})


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
    info = engine_registry.for_stem(path.stem)
    if info is None:
        abort(404)
    return engine_pool.get(info["script"], str(FIGURES_DIR), info["entry"]), path.stem


@app.post("/api/engine/render")
def api_engine_render():
    """应用全量 override 列表并重渲染，返回新 manifest 与版本号。

    首次调用会触发脚本 build（fig9 数秒；heavy 脚本 Phase 1 处理异步化）。
    """
    body = request.get_json(force=True)
    rel_id = body.get("id", "")
    worker, stem = _engine_worker(rel_id)
    info = engine_registry.for_stem(Path(stem).stem) or {}
    cold = not worker.built
    sse_publish("render.started",
                {"id": rel_id, "cost": info.get("cost", ""), "cold": cold})
    t0 = time.time()
    try:
        resp = worker.override(stem, body.get("patches", []))
    except engine_pool.WorkerError as exc:
        LOG.error("引擎渲染失败: %s: %s", stem, exc)
        sse_publish("render.failed", {"id": rel_id, "error": str(exc)})
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
    LOG.info("引擎渲染: %s %.0fms%s", stem, (time.time() - t0) * 1000,
             "（冷启动）" if cold else "")
    sse_publish("render.done", {"id": rel_id, "rev": worker.rev})
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
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
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
    src = safe_resolve(rel_id)
    info = engine_registry.for_stem(src.stem)
    if info is None:
        return jsonify({"error": "该面板不可参数化（没有对应脚本）"}), 404
    worker = engine_pool.get(info["script"], str(FIGURES_DIR), info["entry"])
    try:
        updated, backup_dir = _write_source_files(src, patches, worker)
    except engine_pool.WorkerError as exc:
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
    # 把这组修改追加为该图的版本历史，末位即当前基线：
    # 新拖入的同名面板自动继承，双击进编辑态能接着改
    append_baked(src.stem, patches)
    return jsonify({"updated": updated, "backup_dir": str(backup_dir),
                    "baked": bool(patches)})


def _write_back_forbidden():
    """项目被设为只读时拒绝一切「写回原始文件」类操作；返回错误响应或 None。"""
    st = engine_config.project_settings(str(require_project()))
    if st.get("allow_write_back") is False:
        return jsonify({"error": "该项目已设为只读：不允许写回原始文件"
                                 "（可在项目设置中恢复可写）",
                        "code": "write_back_disabled"}), 403
    return None


def _write_source_files(src: Path, patches: list, worker) -> tuple[list, Path]:
    """按 patches 全质量重出 stem 的 PDF/PNG 并原子替换原文件（先备份）。"""
    targets = [p for p in (src.with_suffix(".pdf"), src.with_suffix(".png")) if p.exists()]
    backup_dir = project_backup_dir() / time.strftime("%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    updated = []
    for target in targets:
        shutil.copy2(target, backup_dir / target.name)
        tmp = target.with_name(f".{target.name}.updating")
        worker.export(src.stem, patches, str(tmp),
                      fmt=target.suffix.lstrip("."), dpi=600)
        tmp.replace(target)
        updated.append(target.name)
    prune_backups(backup_dir.parent)
    LOG.info("更新原图: %s → %s（备份 %s）", src.stem, updated, backup_dir.name)
    return updated, backup_dir


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
    info_s = engine_registry.for_stem(src_path.stem)
    info_d = engine_registry.for_stem(dst_path.stem)
    if info_s is None or info_d is None or info_s["script"] != info_d["script"]:
        return jsonify({"error": "两张图不属于同一个脚本，无法同步"}), 400
    worker = engine_pool.get(info_s["script"], str(FIGURES_DIR), info_s["entry"])
    try:
        man_s = _manifest_of(worker, src_path.stem)
        man_d = _manifest_of(worker, dst_path.stem)
    except engine_pool.WorkerError as exc:
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500

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
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
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
        updated, backup_dir = _write_source_files(src, patches, worker)
    except engine_pool.WorkerError as exc:
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
    append_baked(stem, patches)
    return jsonify({"updated": updated, "backup_dir": str(backup_dir),
                    "patches": patches})


@app.get("/api/engine/svg")
def api_engine_svg():
    """当前 override 状态下的预览 SVG（元素带 gid）。"""
    worker, stem = _engine_worker(request.args.get("id", ""))
    try:
        if not worker.built:
            worker.ensure_built()
    except engine_pool.WorkerError as exc:
        return jsonify({"error": str(exc), "traceback": exc.traceback_text,
                        "code": exc.code}), 500
    svg = worker.svg_path(stem)
    if not svg.exists():
        abort(404)
    resp = send_file(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------- AI 桥 -------------------------------------------
@app.get("/api/ai/capabilities")
def api_ai_capabilities():
    """实际探测本机 codex / claude 的安装、版本与可用参数。"""
    refresh = request.args.get("refresh") == "1"
    resp = jsonify(engine_ai.capabilities(refresh=refresh))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------- 渲染环境（缺 matplotlib 时的自助安装）--------------
@app.get("/api/engine/environment")
def api_engine_environment():
    """渲染环境现状；ok=False 且 can_install=True 时前端给「自动安装」按钮。"""
    return jsonify(engine_bootstrap.status())


@app.post("/api/engine/environment/install")
def api_engine_environment_install():
    """在 Magplot 自己的数据目录里建一个 venv 并装 matplotlib。

    绝不动用户已有的环境——那是他做研究用的。进度经 SSE `engine.bootstrap` 推送。
    """
    st = engine_bootstrap.status()
    if st["ok"]:
        return jsonify({"ok": True, **st})
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
@app.get("/api/update/check")
def api_update_check():
    """?force=1 = 用户手动点「立即检查」，无视 24h 节流与自动检查开关。"""
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(engine_updater.check(force=force))


@app.patch("/api/update/settings")
def api_update_settings():
    body = request.get_json(force=True)
    patch = {}
    if "auto_check" in body:
        patch["auto_check"] = bool(body["auto_check"])
    return jsonify(engine_updater.set_settings(patch))


@app.post("/api/update/apply")
def api_update_apply():
    """执行升级。成功后进程仍跑着旧代码，restart_required 由界面提示重启。"""
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
    return jsonify({"settings": merged,
                    **engine_ai.capabilities(refresh=True)})


@app.post("/api/ai/run")
def api_ai_run():
    """启动 codex / claude 对某脚本面板的深度修改。"""
    body = request.get_json(force=True)
    agent = body.get("agent", "codex")
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        abort(400)
    path = safe_resolve(body.get("id", ""))
    info = engine_registry.for_stem(path.stem)
    if info is None:
        return jsonify({"error": "该面板不可参数化（没有对应脚本）"}), 404
    context = {"stem": path.stem, "gid": body.get("gid"),
               "label": body.get("label"), "overrides": body.get("overrides"),
               "scope": body.get("scope"), "target": body.get("target"),
               "canvas": body.get("canvas")}
    try:
        sid = engine_ai.run(agent, info["script"], prompt, str(FIGURES_DIR),
                            context=context, on_event=sse_publish,
                            model=body.get("model") or None,
                            effort=body.get("effort") or None)
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
def layout_path(name: str) -> Path:
    name = re.sub(r"[^\w\-一-鿿]+", "_", name)
    if not name:
        abort(400)
    return LAYOUT_DIR / f"{name}.json"


@app.get("/api/layouts")
def api_layouts():
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(
        (p.stem for p in LAYOUT_DIR.glob("*.json")),
        key=lambda n: layout_path(n).stat().st_mtime,
        reverse=True,
    )
    return jsonify({"layouts": list(names)})


@app.get("/api/layouts/<name>")
def api_layout_get(name):
    p = layout_path(name)
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="application/json")


@app.post("/api/layouts/<name>")
def api_layout_save(name):
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    layout_path(name).write_text(
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


@app.put("/api/autosave/<doc_id>")
def api_autosave_put(doc_id):
    body = request.get_json(force=True)
    if not isinstance(body, dict) or body.get("schema") not in (2, 3):
        return jsonify({"error": "无效的文档（需要 schema 2 或 3）"}), 400
    AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    p = _autosave_path(doc_id)
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
# 与「写回原始文件」的版本历史（baked_overrides.json，作用于单张图的源文件）
# 是两件事：这里保存的是**整份布局文档**的快照，按前端 documentId 分文件存放，
# 恢复只改前端文档内容，绝不触碰 figures 里的任何文件。
VERSIONS_DIR = LAYOUT_DIR / "_versions"
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
    return VERSIONS_DIR / f"{doc_id}.json"


def _load_versions(doc_id: str) -> list[dict]:
    try:
        data = json.loads(_versions_path(doc_id).read_text(encoding="utf-8"))
        return data.get("versions", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save_versions(doc_id: str, versions: list[dict]) -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    p = _versions_path(doc_id)
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
    args = ap.parse_args()

    setup_logging()
    threading.Thread(target=prune_render_cache, daemon=True,
                     name="mm-cache-prune").start()  # 启动清一次历史存量
    # 上个进程留下的 running AI 会话一律标为已中断（绝不显示为空/unknown）
    n = engine_ai_history.mark_interrupted_running()
    if n:
        LOG.info("上次运行遗留的 %d 个 AI 会话已标记为中断", n)
    engine_ai_history.purge(keep_days=180)
    engine_updater.check_in_background()  # 默认每天一次；设置里可关，关了不联网

    # --figures 最高优先；否则恢复最近项目；都没有（或无效）→ 浏览器里的
    # Project Picker 接手，进程不再直接退出。
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
            print("* 请在浏览器中选择或新建项目")
    else:
        print("* 尚未选择项目：请在浏览器中新建或打开一个项目")

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
