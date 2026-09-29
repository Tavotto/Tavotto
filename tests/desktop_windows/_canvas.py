"""画布上的几件共用事：放一张图、读对象、等它真的落盘。

选择器只认稳定的 `data-*`（`web/AGENTS.md`：不认文案 / aria-label / class）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from _webdriver import KEY_ENTER, KEY_SHIFT, Driver

OBJECT_IDS = "return [...document.querySelectorAll('[data-object-id]')].map(e => e.getAttribute('data-object-id'))"
RECT_OF = """
const el = document.querySelector('[data-object-id="' + CSS.escape(arguments[0]) + '"]');
if (!el) return null;
const r = el.getBoundingClientRect();
return {x: r.x, y: r.y, w: r.width, h: r.height};
"""


def object_ids(d: Driver) -> list[str]:
    return d.js(OBJECT_IDS)


def rect(d: Driver, oid: str) -> dict:
    r = d.js(RECT_OF, oid)
    assert r is not None, f"画布上没有对象 {oid}"
    return r


def place_figure(d: Driver, card: str) -> str:
    """素材卡获得焦点后按 Shift+Enter = 「添加到画布」（卡片自己声明的键位，
    `aria-keyshortcuts`）。回新对象的 id。"""
    before = set(object_ids(d))
    d.wait_js(
        "return !!document.querySelector('[data-card=\"' + CSS.escape(arguments[0]) + '\"]')",
        card,
        timeout=60,
        what=f"素材卡 {card}",
    )
    d.js("document.querySelector('[data-card=\"' + CSS.escape(arguments[0]) + '\"]').focus()", card)
    d.chord(KEY_SHIFT, KEY_ENTER)
    new = d.wait_js(
        "const s = new Set(arguments[0]);"
        " const n = [...document.querySelectorAll('[data-object-id]')].map(e => e.getAttribute('data-object-id'))"
        "   .filter(i => !s.has(i)); return n.length ? n : null",
        sorted(before),
        timeout=30,
        what=f"{card} 出现在画布上",
    )
    assert len(new) == 1, f"Shift+Enter 放上来的对象不是一个：{new}"
    return new[0]


def autosave_dir(data_dir: Path) -> Path:
    from tavotto.engine.documents import AUTOSAVE_DIRNAME  # 目录名的唯一出处

    return data_dir / "layouts" / AUTOSAVE_DIRNAME


def wait_on_disk(data_dir: Path, oid: str, timeout: float = 30.0) -> Path:
    """等后端真的把含这个对象的文档写进磁盘（`PUT /api/autosave/<id>` 落地）。

    主语是**磁盘**，不是顶栏那句「已保存」：那句是前端状态，而重开之后读的是磁盘。
    """
    root = autosave_dir(data_dir)
    deadline = time.monotonic() + timeout
    while True:
        for f in sorted(root.glob("*.json")) if root.is_dir() else []:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 原子写的 tmp + replace 之间读到一半：下一轮再看
            if oid in json.dumps(doc):
                return f
        if time.monotonic() > deadline:
            listing = sorted(p.name for p in root.glob("*")) if root.is_dir() else "（目录不存在）"
            raise TimeoutError(f"{timeout:.0f}s 内 {root} 里没有含对象 {oid} 的文档：{listing}")
        time.sleep(0.25)
