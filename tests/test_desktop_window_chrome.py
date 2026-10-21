"""桌面主窗口的窗框（`src-tauri/src/window_chrome.rs`，2026-10-13）：壳 ↔ 前端同源 + 只给主窗口。

站在两侧之外读 Rust / TypeScript / JSON，因为要证明的是「两处说的是同一件事」：

* **窗框标记**：壳往主窗口注入 `window.__TAVOTTO_WINDOW_CHROME__ = 'overlay' | 'menu-button'`，
  前端 `windowChrome()` 据它让出红绿灯 / 摆出「菜单」按钮。全局名或取值漂了，表现是
  macOS 的红绿灯压在主页按钮上、或 Windows 上菜单栏藏了却没有按钮——菜单整个找不到。
* **只给主窗口**：远程实例窗口加载服务器那一版前端（ADR 0105），未必认得标记；那里
  维持系统标题栏 + 可见菜单栏，壳不注入标记、不藏菜单。
* **ACL**：顶栏拖动 / 双击缩放（`data-tauri-drag-region` 调的两条窗口命令）、Windows 无框窗口的
  三颗窗口按钮（最小化 / 最大化 / 关闭 + 回读是否最大化）与「菜单」按钮只在主窗口的 capability 里；
  少了拖动权限，没有系统标题栏的窗口就**拖不动**；少了按钮权限，点了**没反应**（ACL 静默拒绝）。
* **Windows 无框**：非 macOS 的主窗口去掉系统窗框（`decorations(false)`），与 `chrome()` 取
  `menu-button` 是同一个判据；壳自带的启动 / 出错页内联同一组窗口按钮，用的命令都得在 ACL 里。

`src-tauri/` 不进 wheel/sdist，这几条在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.rustsrc import _bracket_body, chain_methods, rust_code, window_builders

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
WINDOW_CONTROL_PERMISSIONS = {
    "core:window:allow-minimize",
    "core:window:allow-toggle-maximize",
    "core:window:allow-is-maximized",
    "core:window:allow-close",
}
CHROME_COMMANDS = {"allow-show-app-menu"}
SHELL_PAGES = (TAURI / "shell" / "splash.html", TAURI / "shell" / "error.html")


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
    assert DRAG_PERMISSIONS | WINDOW_CONTROL_PERMISSIONS | CHROME_COMMANDS <= granted, sorted(
        granted
    )
    for name, cap in caps.items():
        if name == "main.json":
            continue
        leaked = (DRAG_PERMISSIONS | WINDOW_CONTROL_PERMISSIONS | CHROME_COMMANDS) & set(
            cap["permissions"]
        )
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


@needs_shell
def test_fullscreen_event_command_and_acl_are_one_contract():
    """macOS 全屏时红绿灯收起，顶栏收回让位的宽度：壳发 `tavotto:fullscreen`（载荷 `{fullscreen}`），
    页面订阅同名事件并可回读 `current_fullscreen`。事件名漂了 = 全屏后顶栏左边永远空着 88px；
    命令少了 ACL / 注册 = 回读被静默拒绝，启动时已在全屏的窗口不知道。"""
    rs = CHROME_RS.read_text(encoding="utf-8")
    event = _rust_const(rs, "FULLSCREEN_EVENT")
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    m = re.search(r"export const FULLSCREEN_EVENT = '([^']+)'", ts)
    assert m and m.group(1) == event == "tavotto:fullscreen", (m, event)
    # 载荷字段名两侧一致
    main = rust_code(MAIN_RS.read_text(encoding="utf-8"), keep_strings=True)
    assert 'serde_json::json!({ "fullscreen": now })' in main
    assert "e.payload?.fullscreen" in ts
    # 命令：三处齐全（build.rs / main.json / generate_handler），且只给主窗口
    assert '"current_fullscreen"' in (TAURI / "build.rs").read_text(encoding="utf-8")
    assert (
        "allow-current-fullscreen"
        in json.loads((CAPS / "main.json").read_text(encoding="utf-8"))["permissions"]
    )
    assert "current_fullscreen," in main
    assert "invoke('current_fullscreen')" in ts
    for p in sorted(CAPS.glob("*.json")):
        if p.name != "main.json":
            assert (
                "allow-current-fullscreen"
                not in json.loads(p.read_text(encoding="utf-8"))["permissions"]
            ), p.name
    # 只在 macOS 发、只发给主窗口：取函数体（大括号配对），不是切到文件尾
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    head = code.index("fn announce_fullscreen(")
    assert re.search(r"#\[cfg\(target_os =\s*\)\]\s*$", code[:head]), (
        "announce_fullscreen 必须 cfg(macos)"
    )
    assert 'target_os = "macos"' in MAIN_RS.read_text(encoding="utf-8")
    body = _bracket_body(code, code.index("{", code.index(")", head)))
    assert re.search(r"emit_to\(\s*MAIN_WINDOW\s*,\s*window_chrome::FULLSCREEN_EVENT\b", body), body
    assert ".emit(" not in body, "全屏事件不许全局广播"
    assert "REMOTE_WINDOW" not in body
    # 原生通知观察者：四条通知都听、主窗口销毁时摘掉
    obs = rust_code(
        (TAURI / "src" / "fullscreen_observer.rs").read_text(encoding="utf-8"),
        keep_strings=True,
    )
    for name in ("WillEnter", "DidEnter", "WillExit", "DidExit"):
        assert f"NSWindow{name}FullScreenNotification" in obs, name
    assert "fullscreen_observer::remove()" in code
    assert "fullscreen_observer::install(" in code
    # 回读与事件同一出处（去重底账，按阶段算）：回读若读 `is_fullscreen()`，进入动画期间它已是切换目标
    # true，红绿灯还在就收回占位；底账与页面初值对不上时，第一次退出的 false 还会被当「没变」吞掉。
    head = code.index("fn current_fullscreen(")
    cur = _bracket_body(code, code.index("{", code.index(")", head)))
    assert re.search(r"fullscreen\s*\.get\(\)", cur), "current_fullscreen 回读去重底账"
    assert "is_fullscreen" not in cur, "回读不许读 tao 的切换目标"
    # 启动时按真实状态起步只在观察者没装上时做（观察者在位时等 did-enter）
    install_at = code.index("fullscreen_observer::install(")
    seed = code[install_at : install_at + 900]
    assert re.search(
        r"if\s*!\s*fullscreen_observer::installed\(\)\s*\{[^}]*fullscreen\s*\.update\(", seed
    ), seed


@needs_shell
def test_the_main_window_is_frameless_exactly_where_the_page_draws_window_buttons():
    """`decorations(false)` 只在非 macOS、与 `chrome()` 给 `menu-button` 是同一个 cfg：
    去了窗框却没注入 menu-button = 窗口没有任何关闭按钮；注入了却没去窗框 = 两套按钮。"""
    code = rust_code(CHROME_RS.read_text(encoding="utf-8"))
    head = code.index("fn main_window_chrome(self)")
    body = _bracket_body(code, code.index("{", head))
    m = re.search(r"#\[cfg\(([^\]]*)\)\]\s*let builder = builder\.decorations\(false\);", body)
    assert m, body
    assert re.sub(r"\s", "", m.group(1)) == "not(target_os=)", m.group(1)
    assert body.count("decorations(") == 1
    chrome = code[code.index("pub const fn chrome()") :]
    chrome = _bracket_body(chrome, chrome.index("{"))
    assert re.search(
        r"if cfg!\(target_os =\s*\)\s*\{\s*CHROME_OVERLAY\s*\}\s*else\s*\{\s*CHROME_MENU_BUTTON",
        chrome,
    ), chrome


@needs_shell
def test_shell_pages_window_buttons_use_only_granted_window_commands():
    """启动 / 出错页（`tauri://` 源，主窗口）没有 web 的 `WindowControls`，各自内联一组按钮：
    每颗按钮调的 `plugin:window|<命令>` 都得在 main.json 里授权，且只在 menu-button 窗框露出。"""
    granted = set(json.loads((CAPS / "main.json").read_text(encoding="utf-8"))["permissions"])
    for page in SHELL_PAGES:
        html = page.read_text(encoding="utf-8")
        acts = re.findall(r'data-act="([\w]+)"', html)
        assert sorted(acts) == ["close", "minimize", "toggle_maximize"], (page.name, acts)
        for act in acts:
            perm = "core:window:allow-" + act.replace("_", "-")
            assert perm in granted, (page.name, perm)
        assert "invoke('plugin:window|' + b.dataset.act, { label: 'main' })" in html, page.name
        assert "window.__TAVOTTO_WINDOW_CHROME__ === 'menu-button'" in html, page.name
        assert '<div id="winctl" hidden>' in html, page.name
        # 最大化按钮随窗口状态换「还原」：查询命令 ↔ 授权，两种语言都有 restore 文案
        assert "invoke('plugin:window|is_maximized', { label: 'main' })" in html, page.name
        assert "core:window:allow-is-maximized" in granted, page.name
        assert "addEventListener('resize', sync)" in html, page.name
        assert len(re.findall(r"restore: '[^']+'", html)) == 2, page.name


@needs_shell
def test_web_window_buttons_use_only_granted_window_commands():
    """页面的 `windowControl` / `onWindowMaximizedChange` 调的窗口 API ↔ main.json 授权。"""
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    called = set(re.findall(r"win\.(minimize|toggleMaximize|close|isMaximized)\(", ts))
    assert called == {"minimize", "toggleMaximize", "close", "isMaximized"}, called
    granted = set(json.loads((CAPS / "main.json").read_text(encoding="utf-8"))["permissions"])
    for name in called:
        perm = "core:window:allow-" + re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), name)
        assert perm in granted, perm


@needs_shell
def test_window_state_plugin_never_restores_decorations():
    """`tauri-plugin-window-state` 默认连窗框一起记 / 恢复：从带系统标题栏的旧版本升上来，存着的
    `decorated: true` 会把 `decorations(false)` 顶掉——Windows 上系统标题栏与页面窗口按钮两套并存
    （#1279 Win11 真机实测）。插件的状态位必须把 DECORATIONS 排除在外。"""
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    at = code.index("tauri_plugin_window_state::Builder::default()")
    chain = code[at : code.index(".build()", at)]
    flags = re.search(r"\.with_state_flags\((.*)\)\s*$", chain, re.S)
    assert flags, "window-state 插件没设状态位：默认会恢复窗框"
    expr = re.sub(r"\s", "", flags.group(1)).rstrip(",")
    assert (
        expr
        == "tauri_plugin_window_state::StateFlags::all()-tauri_plugin_window_state::StateFlags::DECORATIONS"
    ), expr


def test_both_windows_install_on_new_window():
    """`target=_blank` / `window.open` 不经 `on_navigation`（macOS 静默无反应、Windows 弹内置小窗）：
    主窗口与远程实例窗口的 builder 都要装 `on_new_window`，外链才会交给系统浏览器。"""
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    sites = window_builders(code)
    for label in ("MAIN_WINDOW", "REMOTE_WINDOW"):
        methods = chain_methods(code, sites[label])
        assert "on_navigation" in methods and "on_new_window" in methods, (label, methods)
