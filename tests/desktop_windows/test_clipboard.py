"""#542 第 7 项：剪贴板跨应用。

「另一个应用」就是本测试进程：它用 Win32 剪贴板 API 读写 `CF_UNICODETEXT`，与记事本、
微信读写的是同一块系统剪贴板。两个方向各一条：

* Tavotto → 别的应用：⌃C 之后系统剪贴板里是对象载荷（魔数来自 `web/src/lib/brand.ts`
  的 `CLIPBOARD_FORMAT`），写它的是**本实例的进程树**（WebView2 的浏览器进程）；
* 别的应用 → Tavotto：本进程改写一份载荷放上剪贴板（本进程成为所有者），⌃V 之后画布上
  多出来的那个对象长成**改写后的样子**——证明读的是系统剪贴板，不是应用内存里的上一份。
  对照：剪贴板里放一段普通文字时 ⌃V 不多出对象（没有内存兜底把判据变成恒真）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from _canvas import object_ids, place_figure, rect
from _webdriver import KEY_CONTROL

ROOT = Path(__file__).resolve().parents[2]


def _clip_magic() -> str:
    """魔数的唯一出处是前端品牌常量；这里读它而不是抄一份。"""
    src = (ROOT / "web" / "src" / "lib" / "brand.ts").read_text(encoding="utf-8")
    m = re.search(r"export const CLIPBOARD_FORMAT = '([^']+)'", src)
    assert m, "web/src/lib/brand.ts 里读不出 CLIPBOARD_FORMAT"
    return m.group(1)


def _select(d, oid: str) -> None:
    r = rect(d, oid)
    d.click_at(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)


def _copy_from_app(d, win32, oid: str) -> str:
    """选中对象、⌃C，等系统剪贴板被换掉。回剪贴板文字。"""
    sentinel = f"desktop-window-sentinel-{os.getpid()}"
    win32.set_clipboard_text(sentinel)
    _select(d, oid)
    d.chord(KEY_CONTROL, "c")
    import time

    deadline = time.monotonic() + 10
    while (text := win32.clipboard_text()) in (None, sentinel):
        assert time.monotonic() < deadline, (
            "⌃C 之后 10s 系统剪贴板还是测试放上去的哨兵：复制没到系统剪贴板"
        )
        time.sleep(0.2)
    return text


def test_copy_lands_on_the_system_clipboard(desktop_app, project_dir, win32):
    d = desktop_app.launch("--open", str(project_dir))
    oid = place_figure(d, "Fig2_yield.pdf")
    text = _copy_from_app(d, win32, oid)

    payload = json.loads(text)
    assert payload.get("magic") == _clip_magic(), f"剪贴板里不是对象载荷：{text[:200]!r}"
    assert [o.get("id") for o in payload.get("objects", [])] == [oid]

    owner = win32.clipboard_owner_pid()
    tree = {p.pid for p in desktop_app.tree} | {desktop_app.proc.pid}  # 句柄持有中：PID 不会被复用
    assert owner != os.getpid(), "剪贴板所有者还是测试进程——写进去的不是应用"
    assert owner in tree, f"剪贴板所有者 {owner} 不在本实例的进程树 {sorted(tree)} 里"


def test_paste_reads_what_another_app_put_there(desktop_app, project_dir, win32):
    d = desktop_app.launch("--open", str(project_dir))
    oid = place_figure(d, "Fig2_yield.pdf")
    original = rect(d, oid)
    payload = json.loads(_copy_from_app(d, win32, oid))

    # 对照：别的应用放上一段普通文字 → ⌃V 不产生对象
    win32.set_clipboard_text("只是一段文字")
    d.chord(KEY_CONTROL, "v")
    import time

    time.sleep(1.5)
    assert object_ids(d) == [oid], (
        "剪贴板里只有普通文字，⌃V 却多出了对象（应用在用内存里的上一份？）"
    )

    # 别的应用改写载荷：宽高减半。⌃V 之后多出来的那个必须是半尺寸
    for o in payload["objects"]:
        o["w"] = round(o["w"] / 2, 3)
        o["h"] = round(o["h"] / 2, 3)
    win32.set_clipboard_text(json.dumps(payload, ensure_ascii=False))
    assert win32.clipboard_owner_pid() != desktop_app.proc.pid
    d.chord(KEY_CONTROL, "v")
    new = d.wait_js(
        "const s = arguments[0]; const n = [...document.querySelectorAll('[data-object-id]')]"
        ".map(e => e.getAttribute('data-object-id')).filter(i => i !== s); return n.length ? n : null",
        oid,
        timeout=10,
        what="⌃V 之后画布上多出一个对象",
    )
    assert len(new) == 1, new
    pasted = rect(d, new[0])
    for k in ("w", "h"):
        assert abs(pasted[k] - original[k] / 2) <= 2, (
            f"粘贴出来的对象 {k}={pasted[k]:.1f}，改写后的载荷要求约 {original[k] / 2:.1f}"
            f"（原对象 {original[k]:.1f}）——粘贴没用系统剪贴板里的内容"
        )
