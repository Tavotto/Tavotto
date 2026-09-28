"""#542 第 5 项：经**系统文件选择器**打开一个 `.py`（主页「导入我的脚本」）。

对话框是壳经 `tauri-plugin-dialog` 弹出的原生 `IFileOpenDialog`（窗口类 `#32770`，属主是壳
进程）。驱动它不靠按键：按控件 id 找到文件名输入框写 `WM_SETTEXT`、给「打开」按钮发
`BM_CLICK`——两条消息都只投给这个对话框的控件句柄。路径是中文 + 空格的目录，
那是 Windows 上路径被拆开的典型位置。

**拖入 `.py` 不在这里**：真实的 OLE 拖放要系统级的指针输入（`DoDragDrop` 的模态循环跟的是
真光标），注入它就是全局输入；Windows 壳也不旁听拖放（ADR 0092 §五，`native_file_drop`
回 false），主页拖入之后同样走到这个选择器。盲点写在 `docs/rules/ci/verification-chain.md`。
"""

from __future__ import annotations

import json
import os
import time

#: 主页两种形态各自的「选择文件」入口：选哪个由页面自己报的 `data-home-variant` 决定，
#: 不是「哪个找得到点哪个」
IMPORT_BUTTON = {"newcomer": "[data-home-import]", "returning": "[data-home-dropzone]"}


def _dialogs(win32, pid: int) -> list:
    return [w for w in win32.top_windows({pid}) if w.cls == "#32770" and w.visible]


def test_open_a_script_through_the_system_file_dialog(desktop_app, project_dir, win32):
    script = project_dir / "plot.py"
    script.write_text("print('hello from 我的 图库')\n", encoding="utf-8")

    d = desktop_app.launch(expect="home")  # 全新配置目录：没有上次的项目，落在主页
    pid = desktop_app.proc.pid
    assert _dialogs(win32, pid) == [], "点按钮之前就已经有系统对话框了"
    variant = d.js(
        "return document.querySelector('[data-home-variant]').getAttribute('data-home-variant')"
    )
    assert variant in IMPORT_BUTTON, f"主页形态 {variant!r} 不认识——IMPORT_BUTTON 要跟着改"
    d.click(d.find(IMPORT_BUTTON[variant]))

    deadline = time.monotonic() + 20
    while not (dlgs := _dialogs(win32, pid)):
        assert time.monotonic() < deadline, "点了「导入我的脚本」20s 还没出现系统文件对话框"
        time.sleep(0.25)
    assert len(dlgs) == 1, dlgs
    dlg = dlgs[0].hwnd
    assert win32.user32.GetWindow(dlg, win32.GW_OWNER) == desktop_app.hwnd(), (
        "对话框的属主不是 Tavotto 主窗口"
    )

    # 输入框在对话框显示之后才逐个建出来：等它出现且唯一
    deadline = time.monotonic() + 10
    while True:
        try:
            edit = win32.dialog_path_edit(dlg)
            break
        except AssertionError:
            assert time.monotonic() < deadline, "系统文件对话框里一直找不到文件名输入框"
            time.sleep(0.25)
    win32.set_text(edit, str(script))
    assert win32.get_text(edit) == str(script)
    win32.click(win32.dialog_button(dlg, win32.IDOK))

    deadline = time.monotonic() + 15
    while win32.is_window(dlg):
        assert time.monotonic() < deadline, "点了「打开」15s 对话框还在（路径被拒？）"
        time.sleep(0.25)

    d.wait_js(
        "const b = document.querySelector('[data-project-switcher]'); return !!b && b.innerText.trim() === arguments[0]",
        project_dir.name,
        timeout=60,
        what=f"项目「{project_dir.name}」在编辑器里打开",
    )
    # 后端那一侧：打开的正是这个目录（不是被拆开 / 转码过的路径）
    recent = d.js_async(
        "const done = arguments[arguments.length - 1];"
        " fetch('/api/projects/recent').then(r => r.json()).then(done, e => done({error: String(e)}))"
    )
    paths = [os.path.normcase(os.path.normpath(r["path"])) for r in recent.get("recent", [])]
    assert paths and paths[0] == os.path.normcase(str(project_dir)), json.dumps(
        recent, ensure_ascii=False
    )[:600]
