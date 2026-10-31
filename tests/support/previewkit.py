"""预览产物的读写夹具：读方一律经账本指针（P08a），测试也不例外。

引擎不再把 `<stem>.svg` / `<stem>.json` 写在产物目录顶层——它们在不可变的代目录里，账本里的
指针说哪一代是当前的（`figcapture.publish_preview` / `usable_preview_generation`）。测试要读某个
stem 的预览就经 `preview_file`；要伪造一个"已发布"的 stem（假 worker 夹具）就经 `publish_fixture`，
走与引擎相同的发布函数，而不是手写顶层文件。
"""

from __future__ import annotations

import json
from pathlib import Path

from tavotto.engine import figcapture

_EMPTY_SVG = b"<svg xmlns='http://www.w3.org/2000/svg'/>"


def preview_file(out_dir, stem: str, suffix: str) -> Path:
    """`stem` 当前发布那一代的 `.svg` / `.json` 路径（没有可用预览时是一个必不存在的路径）。"""
    return Path(figcapture.usable_preview_path(out_dir, stem, suffix))


def publish_fixture(
    out_dir, stem: str, manifest: dict | None = None, svg: bytes | None = None
) -> int:
    """把 `stem` 发布成新一代（假 worker 用）；`manifest` 缺省是 `{"stem": stem}`，`svg` 缺省是空 SVG。

    只给 manifest 时沿用上一代的 SVG（假 worker 每次 override 只重写 manifest）。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if svg is None:
        previous = preview_file(out, stem, ".svg")
        svg = previous.read_bytes() if previous.exists() else _EMPTY_SVG
    svg_part, man_part = out / f".{stem}.svg.part", out / f".{stem}.json.part"
    svg_part.write_bytes(svg)
    man_part.write_text(
        json.dumps(manifest if manifest is not None else {"stem": stem}, ensure_ascii=False),
        encoding="utf-8",
    )
    return figcapture.publish_preview(out, stem, svg_part, man_part, len(svg))
