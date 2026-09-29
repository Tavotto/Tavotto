"""#542 第 13 项：应用菜单、快捷键与窗口焦点（含 #37 纯键盘路径的真机部分）。

Windows 上按下 ⌃Z 时，先截走它的是**菜单加速键表**（`TranslateAcceleratorW`），翻成一条
`WM_COMMAND`（`HIWORD(wParam) == 1`）投给壳，壳经 `tavotto:menu` 转给前端——webview 的
keydown 反而在后面（`src-tauri/src/main.rs::menu_spec` 的注释）。这里驱动的正是那条
`WM_COMMAND`：菜单项的命令 id 从**真菜单栏**上读（`GetMenu`），按显示出来的加速键认项
（muda 把加速键文字追加在菜单文案的 `\\t` 之后），不写死 id、不认文案。

只投**自定义项**：muda 在 Windows 上把预定义的剪切 / 复制 / 粘贴 / 全选实现成 `SendInput`
（向前台注入按键），那是全局输入，这里一概不发。

盲点（写在 `docs/rules/ci/verification-chain.md`）：加速键表的**查表**一步（真键盘按下 →
翻成哪条命令）要真按键才驱动得到；菜单栏上显示的加速键文字是它的镜像，下面逐条核对。
"""

from __future__ import annotations

import time

from _canvas import object_ids, place_figure, rect
from _webdriver import KEY_CONTROL, KEY_ENTER, KEY_ESCAPE, KEY_SHIFT, KEY_TAB

#: 前端本来就认的那几组键（`hooks/useKeyboard.ts`），菜单栏上必须各恰好挂在一个项上。
#: 形状照 muda 在 Windows 上的显示：修饰键 `Ctrl+` / `Shift+`，键名是字符本身。
WIRED = ("Ctrl+O", "Ctrl+S", "Ctrl+Shift+S", "Ctrl+E", "Ctrl+Z", "Ctrl+Shift+Z", "Ctrl+D", "Ctrl+,")


def _by_accel(items, accel: str):
    hit = [i for i in items if i.accelerator == accel]
    assert len(hit) == 1, (
        f"菜单栏上挂着加速键 {accel} 的项有 {len(hit)} 个：{[i.path for i in hit]}"
    )
    return hit[0]


def _menu(desktop_app, win32):
    items = win32.menu_items(desktop_app.hwnd())
    assert items, "壳的主窗口没有菜单栏（GetMenu 为空）"
    return items


def test_menu_bar_shows_the_wired_accelerators(desktop_app, project_dir, win32):
    desktop_app.launch("--open", str(project_dir))
    items = _menu(desktop_app, win32)
    for accel in WIRED:
        _by_accel(items, accel)


def test_menu_undo_redo_reach_the_canvas(desktop_app, project_dir, win32):
    """⌃Z / ⌃⇧Z 以加速键翻出来的那条 WM_COMMAND 到达壳 → 转给前端 → 画布撤销 / 重做。"""
    d = desktop_app.launch("--open", str(project_dir))
    items = _menu(desktop_app, win32)
    undo, redo = _by_accel(items, "Ctrl+Z"), _by_accel(items, "Ctrl+Shift+Z")
    oid = place_figure(d, "Fig2_yield.pdf")
    assert object_ids(d) == [oid]

    win32.post_menu_command(desktop_app.hwnd(), undo.command_id, from_accelerator=True)
    d.wait_js(
        "return document.querySelectorAll('[data-object-id]').length === 0",
        timeout=10,
        what="菜单撤销把刚放的图撤掉",
    )
    win32.post_menu_command(desktop_app.hwnd(), redo.command_id, from_accelerator=True)
    d.wait_js(
        "return [...document.querySelectorAll('[data-object-id]')].map(e => e.getAttribute('data-object-id')).join() === arguments[0]",
        oid,
        timeout=10,
        what="菜单重做把它放回来",
    )


def test_menu_duplicate_yields_to_a_focused_text_field(desktop_app, project_dir, win32):
    """加速键先于 keydown 截走按键：输入框聚焦时 ⌃D 不许被菜单劫持成「复制对象」。"""
    d = desktop_app.launch("--open", str(project_dir))
    dup = _by_accel(_menu(desktop_app, win32), "Ctrl+D")
    oid = place_figure(d, "Fig2_yield.pdf")
    r = rect(d, oid)
    d.click_at(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)  # 选中它：⌃D 在画布上有东西可复制

    focused = d.js(
        "const i = [...document.querySelectorAll('[data-inspector-panel] input')]"
        ".find(e => e.type !== 'hidden' && e.getBoundingClientRect().width > 0 && !e.disabled);"
        " if (!i) return null; i.focus(); return document.activeElement === i ? i.outerHTML.slice(0, 120) : null"
    )
    assert focused, "属性栏里找不到一个能聚焦的输入框（这条用例的前提没摆上）"
    win32.post_menu_command(desktop_app.hwnd(), dup.command_id, from_accelerator=True)
    time.sleep(1.5)
    assert object_ids(d) == [oid], "焦点在输入框里，菜单 ⌃D 却在画布上复制出了对象"

    d.click_at(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)  # 焦点回到画布
    win32.post_menu_command(desktop_app.hwnd(), dup.command_id, from_accelerator=True)
    d.wait_js(
        "return document.querySelectorAll('[data-object-id]').length === 2",
        timeout=10,
        what="焦点在画布上时菜单 ⌃D 复制出第二个对象（对照：命令本身是通的）",
    )


def test_menu_settings_opens_and_escape_gives_focus_back(desktop_app, project_dir, win32):
    d = desktop_app.launch("--open", str(project_dir))
    settings = _by_accel(_menu(desktop_app, win32), "Ctrl+,")
    win32.post_menu_command(desktop_app.hwnd(), settings.command_id, from_accelerator=False)
    d.wait_js(
        "const s = document.querySelector('[data-settings-shell]');"
        " return !!s && document.hasFocus() && s.closest('[role=dialog]')?.contains(document.activeElement)",
        timeout=10,
        what="菜单「设置」打开设置窗，且焦点进了对话框（窗口有焦点）",
    )
    d.chord(KEY_ESCAPE)
    d.wait_js(
        "return !document.querySelector('[data-settings-shell]') && document.hasFocus()"
        " && document.contains(document.activeElement)",
        timeout=10,
        what="Esc 关掉设置，焦点留在还在文档里的元素上",
    )


def test_keyboard_only_tab_to_a_figure_place_undo_redo(desktop_app, project_dir):
    """#37 的真机部分：不用指针，Tab 走到素材卡 → Shift+Enter 放上画布 → ⌃Z / ⌃⇧Z。

    完整的纯键盘闭环（图内编辑、元素树、导出）在 `web/e2e/keyboard-golden-path.spec.ts`
    里跑 chromium（与 WebView2 同一个引擎）和 webkit；这里只验「在真壳里 Tab 走得到、
    键盘动作到得了画布」这一段，Tab 的每一步都断言焦点没有掉到不可见的元素上。
    """
    d = desktop_app.launch("--open", str(project_dir))
    d.js("document.activeElement && document.activeElement.blur()")
    deadline = time.monotonic() + 120
    for step in range(1, 121):
        assert time.monotonic() < deadline, f"Tab 走了 {step - 1} 步、120s 还没走到素材卡"
        d.chord(KEY_TAB)
        info = d.js(
            "const e = document.activeElement; if (!e || e === document.body) return {body: true};"
            " const r = e.getBoundingClientRect();"
            " return {card: e.hasAttribute('data-card'), visible: r.width > 0 && r.height > 0,"
            "         desc: e.tagName + ' ' + (e.getAttribute('aria-label') || e.textContent || '').slice(0, 40)}"
        )
        if info.get("body"):
            continue
        assert info["visible"], f"Tab 第 {step} 步焦点掉到了不可见的元素上：{info['desc']}"
        if info["card"]:
            break
    else:
        raise AssertionError("Tab 120 步都没走到任何素材卡（[data-card]）")

    d.chord(KEY_SHIFT, KEY_ENTER)
    placed = d.wait_js(
        "const n = [...document.querySelectorAll('[data-object-id]')]; return n.length === 1 ? n[0].getAttribute('data-object-id') : null",
        timeout=20,
        what="Shift+Enter 把卡片上的图放上画布",
    )
    d.chord(KEY_CONTROL, "z")
    d.wait_js(
        "return document.querySelectorAll('[data-object-id]').length === 0",
        timeout=10,
        what="⌃Z 撤掉",
    )
    d.chord(KEY_CONTROL, KEY_SHIFT, "z")
    d.wait_js(
        "const n = [...document.querySelectorAll('[data-object-id]')]; return n.length === 1 && n[0].getAttribute('data-object-id') === arguments[0]",
        placed,
        timeout=10,
        what="⌃⇧Z 放回来",
    )
