#!/usr/bin/env python3
"""beta 分支专用（不合进 main）：打包后的 sidecar 真的在用 RenderCore，并且它的三条链都通。

smoke_app 会导出一次 PDF，但它不问「是哪个后端导的」，也不导 PNG。冻结产物里 RenderCore
第一次跑，能坏的地方都只在包里暴露：`--render-child` 入口、pypdfium2 / pikepdf 的原生库、
包内字体。所以这里逐条问：

1. 启动日志里的后端是 `rendercore`（默认值在 beta 分支改了，装不上会在启动时抛）；
2. `/api/render` 对**每一类素材**各出一张 PNG 预览：PDF 面板（PDFium 在 render child 里）与 PNG 面板
   （父进程解码缩放）。只测 PDF 素材漏过一次：位图素材被交给 PDFium，素材库缩略图全是 500；
3. `/api/export` 同时导 PDF + PNG + TIFF（文字排版用包内字体、面板是外来页、位图从 Canonical PDF 来）。

用法：python scripts/beta_rendercore_check.py --exe <sidecar> --figures examples/figures
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import struct
import zlib
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _png_bytes(w: int = 240, h: int = 160) -> bytes:
    """标准库拼一张不透明 RGB PNG（左半蓝、右半橙），检查脚本不依赖 Pillow。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    row = b"\x00" + b"\x1f\x77\xb4" * (w // 2) + b"\xff\x7f\x0e" * (w - w // 2)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(row * h)) + chunk(b"IEND", b"")


def _req(url: str, body: dict | None = None, timeout: float = 120) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--figures", required=True)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="beta-rc-"))
    # 导出默认落在项目目录里：拷一份示例图库再跑，别往仓库的 examples/ 里写东西
    figures = work / "figures"
    shutil.copytree(args.figures, figures)
    (figures / "beta-raster-probe.png").write_bytes(_png_bytes())
    port = _free_port()
    log = work / "sidecar.log"
    env = {"TAVOTTO_DATA_DIR": str(work / "data"), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    with log.open("w") as fh:
        proc = subprocess.Popen(
            [args.exe, "--figures", str(figures), "--no-browser", "--insecure-no-auth", "--port", str(port)],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                _req(f"{base}/api/version", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("sidecar 没起来：\n" + log.read_text(errors="replace")[-3000:])

        text = log.read_text(errors="replace")
        if "PDF 后端: rendercore" not in text:
            raise SystemExit("启动日志里的后端不是 rendercore：\n" + text[-2000:])
        print("✓ 后端: rendercore")

        panels = json.loads(_req(f"{base}/api/panels"))
        panels = panels.get("panels", panels) if isinstance(panels, dict) else panels
        ids = [p["id"] for p in panels]
        pid = next((i for i in ids if i.lower().endswith(".pdf")), None)
        raster_id = next((i for i in ids if i.endswith("beta-raster-probe.png")), None)
        if pid is None or raster_id is None:
            raise SystemExit(f"素材库里缺 PDF 或 PNG 面板：{ids[:20]}")

        for rid, what in ((pid, "PDF 素材，PDFium 在 render child 里"), (raster_id, "PNG 素材，父进程解码缩放")):
            t0 = time.time()
            try:
                png = _req(f"{base}/api/render?id={urllib.request.quote(rid)}&w=900")
            except urllib.error.HTTPError as exc:
                raise SystemExit(f"/api/render {rid} → HTTP {exc.code}（{what}）\n" + log.read_text(errors="replace")[-2000:])
            if not png.startswith(b"\x89PNG") or len(png) < 200:
                raise SystemExit(f"/api/render {rid} 回的不是 PNG（{len(png)} 字节）")
            print(f"✓ 预览 {rid} {len(png)} 字节（{time.time() - t0:.2f}s，{what}）")

        spec = {
            "page_w_mm": 90,
            "page_h_mm": 50,
            "formats": ["pdf", "png", "tiff"],
            "stem": "beta-rc",
            "objects": [
                {"type": "text", "text": "RenderCore 中文 cm^{-1}", "x_mm": 5, "y_mm": 4, "w_mm": 80,
                 "h_mm": 8, "size_pt": 10, "bold": False, "color": "#000000", "align": "left"},
                {"type": "panel", "id": pid, "x_mm": 5, "y_mm": 14, "w_mm": 50, "h_mm": 32},
                {"type": "panel", "id": raster_id, "x_mm": 58, "y_mm": 14, "w_mm": 27, "h_mm": 18},
            ],
        }
        t0 = time.time()
        out = json.loads(_req(f"{base}/api/export", spec, timeout=300))
        files = {Path(f["name"]).suffix.lower(): Path(out["export_dir"]) / f["name"] for f in out["files"]}
        for ext, magic in ((".pdf", b"%PDF"), (".png", b"\x89PNG"), (".tif", b"II*\x00"), (".tiff", b"II*\x00")):
            p = files.get(ext)
            if p is None:
                continue
            head = p.read_bytes()[:4]
            if not head.startswith(magic[:4]) and not (ext.startswith(".tif") and head in (b"II*\x00", b"MM\x00*")):
                raise SystemExit(f"导出的 {p.name} 头部不对：{head!r}")
            print(f"✓ 导出 {p.name}（{p.stat().st_size} 字节）")
        if ".pdf" not in files or ".png" not in files:
            raise SystemExit(f"导出缺格式：{sorted(files)}")
        print(f"· 导出墙钟 {time.time() - t0:.2f}s")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
