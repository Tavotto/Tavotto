"""render child —— 应用自己的 PDFium 子进程（统一实施包 U07，ADR 0066）。

PDFium 不是线程安全的（pypdfium2 文档），U02 已用 spike 证明「一个串行 child + 排队」这条形状可用
（ADR 0055 §2.3）；本模块把它收编进产品：**所有 native（PDFium）调用都在这个进程的主线程里**——
probe（探尺寸）、render（栅格成 `RasterBuffer`）、inspect（对象普查 / 文字层），父进程经
`renderhost.RenderHost` 一把锁串行地跟它说话（RC-047）。

## 协议

stdin / stdout 上的**行分隔 JSON**（一行一条）。请求 `{"id", "op", ...}`，响应 `{"id", "ok", "seq", ...}` 或
`{"id", "ok": false, "seq", "error": {"code", "message"}}`。op 闭集 `ping / probe / size / render / inspect / close`
（`size` 只回可见尺寸、不加载页，ADR 0077）。
像素**不走管道**：`render` 把 RGB（白底）/ RGBA（透明底，straight alpha；都经 `FPDF_REVERSE_BYTE_ORDER`）原样
写进父进程指定的文件，响应里只带 `width / height / stride / channels`；父进程读完就删
（`renderhost.RenderHost.render`）。条带请求（`band_y0 / band_rows / band_overlap`，ADR 0077 P2）只交出那一带，
响应另带 `full_height / band_y0`。

## 四条纪律（RC-050）

* **native 对象显式释放**：每个请求 `doc` / `page` / `bitmap` 在 `finally` 里 `close()`，像素在关之前
  **直接从 native 缓冲写进文件**（ADR 0077 P1，不再先复制成一份 `bytes`）——父进程读回的是它自己的字节，
  `RasterBuffer` 不共享已关闭的 handle。
* **像素预算两侧都判**：父进程按 probe 的尺寸先拒；child 打开页面后按真实尺寸再判一次（父侧被绕过时仍挡）。
* **内存上限**：POSIX 上 `RLIMIT_AS`（Linux 生效；macOS 内核不强制、Windows 没有 resource 模块——
  ADR 0055 §2.3 实测，像素预算是那两处唯一有效的护栏）。
* **任何渲染失败都是结构化错误**，child 不带着半个响应退出；坏 PDF 报 `render_failed`，child 继续活。

pypdfium2 / pikepdf **只在 `child_main()` 里 import**（本模块被父进程 import 时不拉起 native 库：父侧只用
`ERROR_CODES` 与 `child_argv()`）。冻结产物里同一个 exe 以 `--render-child` 再起自己（`child_argv()`）。

Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码，第一句中文就 UnicodeEncodeError：
两条流都钉成 UTF-8（`tests/test_windows_regressions.py` 看护的同一条纪律）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

#: 默认像素预算：约 8000×8000 RGBA = 256 MiB。超过的请求父进程直接拒绝，child 再判一次。
DEFAULT_MAX_PIXELS = 64_000_000
#: 条带栅格（ADR 0077 P2）：整页像素超过 `max_pixels` 时按行带分几次渲染，**每一带**仍受 `max_pixels` 约束；
#: 整页的上限是这个数（A3 @ 1200 ppi ≈ 278 M；RGBA 最坏 2 GB 原始像素，仍在经典 TIFF 的 4 GiB 偏移之内）。
DEFAULT_MAX_TOTAL_PIXELS = 512_000_000
#: 每一带的目标像素数。行带高度 = `band_rows_for(宽)`——**只由宽度定**：PDFium 按带渲染与整页渲染不逐字节相同
#: （抗锯齿随位图原点 / 尺寸差 1–5 级，ADR 0077 实测），所以带的切法必须固定，同一输入才同一份像素。
BAND_PIXELS = 16_000_000
#: 每一带上下各多渲染这么多行、只交出中间那几行：笔画跨过带界时，PDFium 在位图边缘算覆盖率与整页不同（u06 @ 150 ppi
#: 实测接缝那一行差到 88 级）；多渲 1 行就回到抗锯齿噪声，取 4 行给比细线更宽的效果（软遮罩 / 渐变）留余量。
BAND_OVERLAP = 4


def band_rows_for(width_px: int) -> int:
    return max(1, BAND_PIXELS // max(1, int(width_px)))


#: child 地址空间上限（字节）；只在支持 RLIMIT_AS 的 POSIX 内核上生效。
DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024

#: 父子两侧共用的错误码闭集。
ERROR_CODES = (
    "render_child_timeout",  # deadline 到，child 被 kill
    "render_child_died",  # child 自己退出 / 被外力杀死（读线程见 EOF）
    "render_child_spawn_failed",  # child 起不来（exe 不在 / 没权限 / 冻结产物的命令不对）
    "render_child_protocol",  # 回来的不是 JSON / 对不上 id
    "render_queue_full",  # 等锁的请求超过有界队列（背压，RC-062）
    "pixel_budget_exceeded",  # 超像素预算（父侧或 child 侧）
    "render_failed",  # PDFium 打不开 / 渲染失败（child 继续活）
    "bad_request",  # 请求形状不对 / 未知 op
)

OPS = ("ping", "probe", "size", "render", "inspect", "close")


class RenderChildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        assert code in ERROR_CODES, code
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def child_argv() -> list[str]:
    """起 child 的 argv：源码树 / wheel 里是本解释器跑本模块；冻结产物里是同一个 exe 加 `--render-child`
    （PyInstaller 的 `sys.executable` 就是那个 exe，`-m` 在冻结产物里不存在——RC-049）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--render-child"]
    return [sys.executable, "-m", "tavotto.rendercore.renderchild"]


# ---------------------------------------------------------------------------
# 子进程侧
# ---------------------------------------------------------------------------
def _apply_memory_limit(limit: int) -> str:
    try:
        import resource
    except ImportError:  # Windows
        return "unsupported"
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError) as exc:
        return f"failed: {exc}"
    return "set" if sys.platform != "darwin" else "set-but-not-enforced-by-kernel"


def _user_unit(pdf_path: Path, page_index: int) -> float:
    """`/UserUnit`（PDFium 的 get_size() 忽略它——本机实测；RC-039 要求它被应用一次，这里就是那一次）。"""
    import pikepdf

    with pikepdf.open(str(pdf_path)) as src:
        page = src.pages[page_index]
        uu = page.obj.get("/UserUnit")
        return float(uu) if uu is not None else 1.0


def _probe(pdfium, req: dict) -> dict:
    pdf_path = Path(req["pdf"])
    page_index = int(req.get("page", 0))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(doc)
        if page_index >= n:
            raise RenderChildError("render_failed", f"只有 {n} 页，没有第 {page_index} 页")
        page = doc[page_index]
        try:
            w_pt, h_pt = page.get_size()
            rotation = page.get_rotation()
            media = page.get_mediabox()
            crop = page.get_cropbox()
        finally:
            page.close()
    finally:
        doc.close()
    uu = _user_unit(pdf_path, page_index)
    return {
        "pages": n,
        "page": page_index,
        "width_pt": w_pt * uu,
        "height_pt": h_pt * uu,
        "raw_width_pt": w_pt,
        "raw_height_pt": h_pt,
        "user_unit": uu,
        "rotation": rotation,
        "media_box": list(media),
        "crop_box": list(crop),
    }


def _size(pdfium, req: dict) -> dict:
    """只要可见尺寸（`probe_asset` / 原图导出 / 标注用的就只有这一维）：`FPDF_GetPageSizeByIndexF` **不加载页**。
    `doc[i]` 走的 `FPDF_LoadPage` 会解析整页内容流——CAD 图纸 8 ms、海报 23 ms，而尺寸只取决于页盒与 /Rotate；
    这里 0.06–1 ms。两条路读的是同一个页字典的页盒与 /Rotate（解析内容流是 LoadPage 另外做的事），
    `/UserUnit` 与 `_probe` 同一次乘；要页盒 / 旋转细节的调用方仍用 `probe`。"""
    pdf_path = Path(req["pdf"])
    page_index = int(req.get("page", 0))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(doc)
        if page_index >= n:
            raise RenderChildError("render_failed", f"只有 {n} 页，没有第 {page_index} 页")
        w_pt, h_pt = doc.get_page_size(page_index)
    finally:
        doc.close()
    uu = _user_unit(pdf_path, page_index)
    return {"pages": n, "page": page_index, "width_pt": w_pt * uu, "height_pt": h_pt * uu}


def _render(pdfium, req: dict, default_max_pixels: int) -> dict:
    """页 → RGBA 原样字节写进 `out`。尺寸规则只有一条：`width_px` 给了就按宽定比例（画布预览的桶），
    否则按 `dpi`（导出）——两者都是 `round()`，产物尺寸就是响应里报的尺寸。"""
    pdf_path = Path(req["pdf"])
    out_path = Path(req["out"])
    page_index = int(req.get("page", 0))
    transparent = bool(req.get("transparent", False))
    max_pixels = int(req.get("max_pixels", default_max_pixels))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index >= len(doc):
            raise RenderChildError("render_failed", f"只有 {len(doc)} 页，没有第 {page_index} 页")
        page = doc[page_index]
        try:
            w_pt, h_pt = page.get_size()
            if req.get("width_px") is not None:
                width_px = int(req["width_px"])
                scale = width_px / w_pt
                height_px = max(1, int(round(h_pt * scale)))
            else:
                # dpi 是**物理**密度：页的物理尺寸 = PDFium 的尺寸 × /UserUnit（PDFium 自己忽略它，与 probe 同一次乘；
                # Codex #471 第三轮 P2），位图按物理尺寸定，PDFium 把页拉伸到这块位图上
                dpi = float(req["dpi"])
                uu = _user_unit(pdf_path, page_index)
                width_px = max(1, int(round(w_pt * uu * dpi / 72.0)))
                height_px = max(1, int(round(h_pt * uu * dpi / 72.0)))
            if width_px <= 0 or height_px <= 0:
                raise RenderChildError("bad_request", "尺寸必须为正")
            # 条带（ADR 0077 P2）：整页尺寸照上面算，位图只有 [band_y0, band_y0 + band_rows) 这几行；预算按**这一带**判，
            # 整页另有 `max_total_pixels`。不带条带参数的请求与从前一字不差（整页 ≤ max_pixels）
            band_y0, band_rows = req.get("band_y0"), req.get("band_rows")
            full_height = height_px
            top = 0  # 条带的上重叠行数（位图从 band_y0 - top 开始）
            if band_rows is None:
                band_y0 = 0
                if width_px * height_px > max_pixels:
                    raise RenderChildError(
                        "pixel_budget_exceeded", f"{width_px}×{height_px} > {max_pixels}"
                    )
            else:
                band_y0, band_rows = int(band_y0), int(band_rows)
                if band_y0 < 0 or band_rows <= 0 or band_y0 + band_rows > height_px:
                    raise RenderChildError(
                        "bad_request",
                        f"行带 [{band_y0}, {band_y0 + band_rows}) 不在 0..{height_px} 内",
                    )
                max_total = int(req.get("max_total_pixels", DEFAULT_MAX_TOTAL_PIXELS))
                if width_px * height_px > max_total:
                    raise RenderChildError(
                        "pixel_budget_exceeded", f"整页 {width_px}×{height_px} > {max_total}"
                    )
                overlap = max(0, int(req.get("band_overlap", BAND_OVERLAP)))
                top = min(overlap, band_y0)
                bottom = min(overlap, full_height - band_y0 - band_rows)
                if width_px * (band_rows + top + bottom) > max_pixels:
                    raise RenderChildError(
                        "pixel_budget_exceeded",
                        f"行带 {width_px}×{band_rows + top + bottom}（含上下重叠）> {max_pixels}",
                    )
                height_px = (
                    band_rows + top + bottom
                )  # 位图带上下重叠；交出去的只有中间 band_rows 行
            t0 = time.perf_counter()
            raw = pdfium.raw
            # 透明底 → BGRA（REVERSE_BYTE_ORDER 之后是 RGBA，straight alpha，不请求 Premul 格式）；
            # 白底 → BGR（→ RGB，3 字节 / 像素）：本机实测两条路的 RGB 逐字节相同，白底不带一条全 255 的 alpha
            fmt = raw.FPDFBitmap_BGRA if transparent else raw.FPDFBitmap_BGR
            channels = 4 if transparent else 3
            bitmap = pdfium.PdfBitmap.new_native(
                width_px, height_px, format=fmt, rev_byteorder=True
            )
            try:
                bitmap.fill_rect(
                    (0, 0, 0, 0) if transparent else (255, 255, 255, 255),
                    0,
                    0,
                    width_px,
                    height_px,
                )
                # 不画注释（导出 / 预览不认注释）；REVERSE_BYTE_ORDER 让缓冲成 RGBA。条带：页按**整页**尺寸铺开、
                # 向上挪 band_y0 - top 行，位图接住这一带连同上下重叠（整数像素平移，PDFium 在位图边界外裁掉）
                raw.FPDF_RenderPageBitmap(
                    bitmap,
                    page,
                    0,
                    -(band_y0 - top),
                    width_px,
                    full_height,
                    0,
                    raw.FPDF_REVERSE_BYTE_ORDER,
                )
                stride = bitmap.stride
                # 像素在关位图**之前**直接从 native 缓冲写进文件，不再先复制成一份 bytes——600 ppi A4 的 RGB 就是
                # 104 MB，child 峰值因此少一整份（ADR 0077 P1）。父进程读回的是文件里它自己的字节：RasterBuffer 仍
                # 不共享 native 句柄（RC-050）
                tmp = out_path.with_name(out_path.name + ".part")
                pixels = memoryview(bitmap.buffer).cast("B")
                if band_rows is not None:  # 条带：只交出中间那几行，上下重叠丢掉
                    pixels = pixels[top * stride : (top + band_rows) * stride]
                    height_px = band_rows
                with open(tmp, "wb") as fh:
                    nbytes = fh.write(pixels)
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()
    os.replace(tmp, out_path)
    return {
        "width": width_px,
        "height": height_px,
        "full_height": full_height,
        "band_y0": band_y0,
        "stride": stride,
        "channels": channels,
        "bytes": nbytes,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _inspect(pdfium, req: dict) -> dict:
    """对象普查 + 文字层（测试 / 证据 / 产物检查用；PDFium 对象类型：1 text、2 path、3 image、4 shading、5 form）。"""
    pdf_path = Path(req["pdf"])
    page_index = int(req.get("page", 0))
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index >= len(doc):
            raise RenderChildError("render_failed", f"只有 {len(doc)} 页，没有第 {page_index} 页")
        page = doc[page_index]
        try:
            kinds = [o.type for o in page.get_objects()]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
        finally:
            page.close()
        pages = len(doc)
    finally:
        doc.close()
    census = {"text": 0, "path": 0, "image": 0, "shading": 0, "form": 0, "other": 0}
    names = {1: "text", 2: "path", 3: "image", 4: "shading", 5: "form"}
    for k in kinds:
        census[names.get(k, "other")] += 1
    return {"pages": pages, "page": page_index, "objects": census, "text": text}


def child_main(argv: list[str] | None = None) -> int:
    """child 主循环。pypdfium2 在这里才 import，且**只在这个线程**用。"""
    import argparse

    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # 只在 child 进程里钉 UTF-8：父进程 import 本模块不许有副作用
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    ap.add_argument("--memory-limit", type=int, default=DEFAULT_MEMORY_LIMIT_BYTES)
    args = ap.parse_args(argv)
    mem = _apply_memory_limit(args.memory_limit)

    import pypdfium2 as pdfium

    out = sys.stdout.buffer
    seq = 0

    def reply(obj: dict) -> None:
        out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()

    for line in sys.stdin.buffer:
        try:
            req = json.loads(line.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("not an object")
        except ValueError:
            reply(
                {"id": None, "ok": False, "error": {"code": "bad_request", "message": "not JSON"}}
            )
            continue
        rid, op = req.get("id"), req.get("op")
        seq += 1
        try:
            if op == "ping":
                body = {
                    "pid": os.getpid(),
                    "memory_limit": mem,
                    "pdfium": pdfium.PDFIUM_INFO.version,
                    "python": sys.version.split()[0],
                    "frozen": bool(getattr(sys, "frozen", False)),
                }
            elif op == "close":
                reply({"id": rid, "ok": True, "seq": seq})
                return 0
            elif op == "probe":
                body = _probe(pdfium, req)
            elif op == "size":
                body = _size(pdfium, req)
            elif op == "render":
                body = _render(pdfium, req, args.max_pixels)
            elif op == "inspect":
                body = _inspect(pdfium, req)
            else:
                raise RenderChildError("bad_request", f"unknown op {op!r}")
            reply({"id": rid, "ok": True, "seq": seq, **body})
        except RenderChildError as exc:
            reply(
                {
                    "id": rid,
                    "ok": False,
                    "seq": seq,
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
        except Exception as exc:  # noqa: BLE001 —— 任何渲染失败都是结构化错误，child 不带着半个响应退出
            reply(
                {
                    "id": rid,
                    "ok": False,
                    "seq": seq,
                    "error": {"code": "render_failed", "message": f"{type(exc).__name__}: {exc}"},
                }
            )
    return 0


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--render-child"]
    return child_main(argv)


if __name__ == "__main__":
    sys.exit(main())
