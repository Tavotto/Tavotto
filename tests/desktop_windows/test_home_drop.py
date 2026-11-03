"""主页拖入 `.py` 直接打开所在项目、不弹系统文件选择器（ADR 0092 修订 2026-11-03）。

Windows 壳不能装 Tauri 的拖放处理器（装上页面 HTML5 拖放整片失效），路径靠 WebView2：页面在 drop 捕获阶段
经 `chrome.webview.postMessageWithAdditionalObjects` 把 File 交给壳，壳读 `ICoreWebView2File::Path()` 后发
`tavotto:file-drop`，主页按它打开项目并运行这个脚本。

拖放经 CDP `Input.dispatchDragEvent` 派进这个 WebView（带磁盘文件，`_webdriver.Driver.drop_files`），不注入任何
全局输入；它量不到的是 OLE 那一层（真光标的 `DoDragDrop`），盲点照旧在 `docs/rules/ci/verification-chain.md`。
"""

from __future__ import annotations

import json
import os
import time


def _dialogs(win32, pid: int) -> list:
    return [w for w in win32.top_windows({pid}) if w.cls == "#32770" and w.visible]


def test_dropping_a_script_on_home_opens_its_project_without_a_picker(
    desktop_app, project_dir, win32
):
    script = project_dir / "plot.py"
    script.write_text("print('hello from 我的 图库')\n", encoding="utf-8")

    d = desktop_app.launch(expect="home")
    pid = desktop_app.proc.pid
    d.wait_js(
        "return !!document.querySelector('[data-tutorial-open-workspace]')",
        timeout=30,
        what="全新配置目录的第一屏：教程欢迎页的「打开工作区」",
    )
    d.click(d.find("[data-tutorial-open-workspace]"))
    d.wait_js(
        "return !document.querySelector('[data-native-tutorial]')",
        timeout=30,
        what="教程欢迎页收起，主页露出来",
    )
    # 壳报告能拿真实路径（`native_file_drop` = Windows 旁听装上了）：不然下面那次放下会等满 grace 后弹选择器
    native = d.js_async(
        "const done = arguments[arguments.length - 1];"
        " window.__TAURI_INTERNALS__.invoke('native_file_drop').then(done, e => done('ERR ' + e))"
    )
    assert native is True, f"native_file_drop 回 {native!r}：Windows 壳的拖放旁听没装上"

    r = d.js("const b = document.body.getBoundingClientRect(); return [b.width / 2, b.height / 2]")
    d.drop_files(r[0], r[1], [str(script)])

    d.wait_js(
        "const b = document.querySelector('[data-project-switcher]'); return !!b && b.innerText.trim() === arguments[0]",
        project_dir.name,
        timeout=60,
        what=f"拖入 plot.py 后项目「{project_dir.name}」在编辑器里打开",
    )
    # 关键判据：全程没有弹出系统文件对话框（降级路径才会弹）
    end = time.monotonic() + 3
    while time.monotonic() < end:
        assert _dialogs(win32, pid) == [], "拖入后还是弹出了系统文件对话框"
        time.sleep(0.25)
    recent = d.js_async(
        "const done = arguments[arguments.length - 1];"
        " fetch('/api/projects/recent').then(r => r.json()).then(done, e => done({error: String(e)}))"
    )
    paths = [os.path.normcase(os.path.normpath(r["path"])) for r in recent.get("recent", [])]
    assert paths and paths[0] == os.path.normcase(str(project_dir)), json.dumps(
        recent, ensure_ascii=False
    )[:600]
