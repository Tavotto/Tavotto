"""把若干张单页 PDF 按给定顺序拼成一个多页 PDF（#1370，PdfPages 导出）。

只做一件事：**不重画**——每个输入文件的那一页被 pikepdf 原样搬进新文件（`pages.extend`，
页面尺寸 / 路径 / 文字一个字节不动）。「每一页怎么导出」不在这里：调用方先让每张图各走现有的
单页 PDF 导出路径（`app._export_produce_original`），再把得到的单页文件交给这里。

已知代价：字体按页各嵌一份（每个单页文件自带子集字体，pikepdf 搬运时不会跨页去重）。
文件会比脚本里 `PdfPages` 直接写出的偏大，内容不变；去重需要重写字体资源，那是另一个有风险的改动，
本模块不做。

同一个输入文件在 `singles` 里出现多次（同一张图写了多页）= 这一页按页序重复放多次。
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path


class PdfMergeError(ValueError):
    """输入不是「恰好一页的可读 PDF」。"""


def merge_pdf_pages(singles: Sequence[Path], out: Path) -> int:
    """按 `singles` 的顺序把每个单页 PDF 的那一页拼进 `out`，回总页数。

    每个输入必须恰好一页（单页导出路径的契约：一张图 = 一页），否则 `PdfMergeError`——
    悄悄只取第一页会让页数与页序对不上请求。`out` 的父目录不存在时创建。
    """
    import pikepdf

    if not singles:
        raise PdfMergeError("no pages to merge")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        dest = stack.enter_context(pikepdf.new())
        opened: dict[str, pikepdf.Pdf] = {}
        for path in singles:
            key = str(path)
            src = opened.get(key)
            if src is None:
                try:
                    src = stack.enter_context(pikepdf.open(key))
                except Exception as exc:  # noqa: BLE001 —— 打不开 = 这一页没有，不是空页
                    raise PdfMergeError(f"unreadable single-page PDF: {path.name}") from exc
                if len(src.pages) != 1:
                    raise PdfMergeError(f"{path.name} has {len(src.pages)} pages, expected 1")
                opened[key] = src
            dest.pages.extend(src.pages)  # 源文件保持打开到 save 之后：pikepdf 要求
        count = len(dest.pages)
        dest.save(
            str(out),
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            deterministic_id=True,
        )
    return count
