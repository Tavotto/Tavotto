"""桌面主窗口的窗框（`src-tauri/src/window_chrome.rs`，2026-10-13）：壳 ↔ 前端同源 + 只给主窗口。

站在两侧之外读 Rust / TypeScript / JSON，因为要证明的是「两处说的是同一件事」：

* **窗框标记**：壳往主窗口注入 `window.__TAVOTTO_WINDOW_CHROME__ = 'overlay' | 'menu-button'`，
  前端 `windowChrome()` 据它让出红绿灯 / 摆出「菜单」按钮。全局名或取值漂了，表现是
  macOS 的红绿灯压在主页按钮上、或 Windows 上菜单栏藏了却没有按钮——菜单整个找不到。
* **只给主窗口**：远程实例窗口加载服务器那一版前端（ADR 0105），未必认得标记；那里
  维持系统标题栏 + 可见菜单栏，壳不注入标记、不藏菜单。
* **ACL**：顶栏拖动 / 双击缩放（`data-tauri-drag-region` 调的两条窗口命令）与两条自定义命令
  只在主窗口的 capability 里；少了拖动权限，macOS 上叠了标题栏的窗口就**拖不动**。

`src-tauri/` 不进 wheel/sdist，这几条在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.rustsrc import chain_methods, rust_code, window_builders

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
MAIN_RS = TAURI / "src" / "main.rs"
CHROME_RS = TAURI / "src" / "window_chrome.rs"
CAPS = TAURI / "capabilities"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"

needs_shell = pytest.mark.skipif(
    not MAIN_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)

DRAG_PERMISSIONS = {
    "core:window:allow-start-dragging",
    "core:window:allow-internal-toggle-maximize",
}
CHROME_COMMANDS = {"allow-show-app-menu", "allow-set-titlebar-colors"}


def _rust_const(src: str, name: str) -> str:
    m = re.search(rf'pub const {name}: &str = "((?:[^"\\]|\\.)*)";', src)
    assert m, f"找不到 const {name}"
    return m.group(1)


def _ts_function(name: str) -> str:
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    fn = ts[ts.index(f"export function {name}") :]
    return fn[: fn.index("\n}\n")]


@needs_shell
def test_the_chrome_marker_is_the_global_and_values_the_frontend_reads():
    rs = CHROME_RS.read_text(encoding="utf-8")
    global_name = _rust_const(rs, "MARKER_GLOBAL")
    values = {_rust_const(rs, "CHROME_OVERLAY"), _rust_const(rs, "CHROME_MENU_BUTTON")}
    assert values == {"overlay", "menu-button"}, values
    fn = _ts_function("windowChrome")
    assert f").{global_name}\n" in fn, "前端读的全局名与壳注入的不是同一个"
    # 前端认的取值正是壳会注入的那两个（多认一个 = 永远走不到的分支，少认一个 = 那个平台退回 native）
    assert set(re.findall(r"v === '([\w-]+)'", fn)) == values, fn
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    union = re.search(r"export type WindowChrome = ([^\n]+)", ts)
    assert union, "WindowChrome 类型不见了"
    assert set(re.findall(r"'([\w-]+)'", union.group(1))) == values | {"native"}


@needs_shell
def test_only_the_main_window_gets_the_chrome():
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    sites = window_builders(code)
    assert "main_window_chrome" in chain_methods(code, sites["MAIN_WINDOW"])
    assert "main_window_chrome" not in chain_methods(code, sites["REMOTE_WINDOW"])
    # 藏菜单栏只认主窗口的 label：藏到远程窗口上，那里的旧前端就没有菜单可用了
    fn = code[code.index("fn hide_main_menu_bar(") :]
    fn = fn[: fn.index("\n}\n")]
    assert "get_webview_window(MAIN_WINDOW)" in fn and "REMOTE_WINDOW" not in fn


@needs_shell
def test_every_menu_rebuild_hides_the_main_menu_bar_again():
    """`app.set_menu` 会把菜单重新挂到主窗口上**并显示**：每一处换菜单之后都要再藏一次。"""
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    fn = code[code.index("fn set_menu_locale(") :]
    fn = fn[: fn.index("\n}\n")]
    assert fn.index("app.set_menu(") < fn.index("hide_main_menu_bar(")


@needs_shell
def test_drag_and_chrome_commands_are_granted_to_the_main_window_only():
    caps = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(CAPS.glob("*.json"))}
    main = caps["main.json"]
    assert main["windows"] == ["main"]
    granted = set(main["permissions"])
    assert DRAG_PERMISSIONS | CHROME_COMMANDS <= granted, sorted(granted)
    for name, cap in caps.items():
        if name == "main.json":
            continue
        leaked = (DRAG_PERMISSIONS | CHROME_COMMANDS) & set(cap["permissions"])
        assert not leaked, f"{name} 拿到了主窗口的窗框权限：{sorted(leaked)}"


@needs_shell
def test_traffic_lights_fit_inside_the_inset_the_topbar_leaves():
    """红绿灯三颗按钮（关闭钮 x 起，macOS 26 实测间距 23、按钮宽 14）要落在顶栏让出的那段里，
    右侧至少再空 8，主页胶囊的悬停底色才不贴着绿灯。"""
    rs = rust_code(CHROME_RS.read_text(encoding="utf-8"))
    m = re.search(r"TRAFFIC_LIGHTS: \(f64, f64\) = \(([\d.]+), ([\d.]+)\)", rs)
    assert m, "TRAFFIC_LIGHTS 的形状变了"
    x = float(m.group(1))
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    inset = re.search(r"export const TRAFFIC_LIGHTS_INSET = (\d+)", ts)
    assert inset, "TRAFFIC_LIGHTS_INSET 不见了"
    assert x + 2 * 23 + 14 + 8 <= int(inset.group(1)), (x, inset.group(1))
