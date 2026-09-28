"""#542 第 8 项：真 WebView2 里的拖动，与撤销 / 重做。

浏览器 Playwright 早就量过拖动（`web/e2e/drag-gesture-lifecycle.spec.ts`），这里补的是
**同一件事在真壳的 WebView2 里**：指针事件经 CDP 进渲染进程、命中层与手势状态机在
WebView2 的合成器下跑、⌃Z / ⌃⇧Z 在壳里没被菜单加速键表吞掉之前先到 keydown。

主语：对象 `[data-object-id]` 在视口里的矩形。拖动后它移开，撤销回到**逐像素**原位，
重做回到**逐像素**拖后的位置——吸附会让落点与指针位移差几像素，所以拖动那一步只判
「明显移开」，精确的判据放在撤销 / 重做上（它们回放的是记录下来的状态，没有吸附）。
"""

from __future__ import annotations

from _canvas import place_figure, rect
from _webdriver import KEY_CONTROL, KEY_SHIFT


def _same(a: dict, b: dict, tol: float = 1.0) -> bool:
    return all(abs(a[k] - b[k]) <= tol for k in ("x", "y", "w", "h"))


def test_drag_then_undo_and_redo_in_the_real_webview(desktop_app, project_dir):
    d = desktop_app.launch("--open", str(project_dir))
    oid = place_figure(d, "Fig2_yield.pdf")
    r0 = d.wait_js(
        "const e = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " const r = e && e.getBoundingClientRect(); return r && r.width > 20 ? {x:r.x,y:r.y,w:r.width,h:r.height} : null",
        oid,
        what="对象有了尺寸",
    )
    cx, cy = r0["x"] + r0["w"] / 2, r0["y"] + r0["h"] / 2
    d.drag(cx, cy, cx + 140, cy + 90)

    r1 = d.wait_js(
        "const e = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " const r = e.getBoundingClientRect();"
        " return (r.x - arguments[1] > 100 && r.y - arguments[2] > 60) ? {x:r.x,y:r.y,w:r.width,h:r.height} : null",
        oid,
        r0["x"],
        r0["y"],
        timeout=10,
        what="拖动之后对象向右下移开（> 100 / > 60 px）",
    )

    d.chord(KEY_CONTROL, "z")
    d.wait_js(
        "const e = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " const r = e.getBoundingClientRect(); const o = arguments[1];"
        " return Math.abs(r.x-o.x) <= 1 && Math.abs(r.y-o.y) <= 1 && Math.abs(r.width-o.w) <= 1",
        oid,
        r0,
        timeout=10,
        what=f"Ctrl+Z 之后回到原位 {r0}",
    )

    d.chord(KEY_CONTROL, KEY_SHIFT, "z")
    d.wait_js(
        "const e = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " const r = e.getBoundingClientRect(); const o = arguments[1];"
        " return Math.abs(r.x-o.x) <= 1 && Math.abs(r.y-o.y) <= 1 && Math.abs(r.width-o.w) <= 1",
        oid,
        r1,
        timeout=10,
        what=f"Ctrl+Shift+Z 之后回到拖后的位置 {r1}",
    )
    assert _same(rect(d, oid), r1)
