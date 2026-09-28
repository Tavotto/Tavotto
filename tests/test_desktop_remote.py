"""桌面壳的「连接远程实例」（ADR 0105）：同源对 + 远程窗口的权限边界。

这些判据站在两侧之外（Python 读 Rust / TypeScript / HTML / JSON），因为它们要证明的是
「两处说的是同一件事」或「这个窗口拿不到那条能力」——那种事在任何一侧的单测里都各自绿：

* **能力标记**：引擎在 `/api/version` 报 `desktop-remote-window`，壳据它拒绝老引擎。
  两侧各写一份字面量；漂了的表现是**所有**远程引擎都被说成「太旧」。
* **窗口标记**：壳往远程窗口注入 `window.__TAVOTTO_REMOTE_ENGINE__`，前端据它把本机
  文件类能力让给浏览器回退。漂了的表现是远程窗口里「打开项目」弹出本机的目录选择器，
  选中的本机路径发给服务器上的引擎。
* **错误 code**：壳回稳定 code，`connect.html` 翻成人话。多一个少一个都是一句
  「连接失败：unreachable」这样的英文 code 直接摆到用户面前。
* **权限按窗口**：远程窗口的页面来自另一台机器。它能调什么，全看 `capabilities/` 里
  给 `remote` 这个 label 的那几份——这里把它钉成一张**闭集**：多一条本机文件类权限、
  或者给了发事件的权限（能给主窗口伪造 `tavotto:open`），当场红。

`src-tauri/` 不进 wheel/sdist，壳相关的几条在没有它的树上跳过。
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
REMOTE_RS = TAURI / "src" / "remote.rs"
CONNECT_HTML = TAURI / "shell" / "connect.html"
CAPS = TAURI / "capabilities"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"

needs_shell = pytest.mark.skipif(
    not MAIN_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)

#: 远程实例窗口的引擎页面**只许**拿这几条（闭集）：收菜单事件、关窗前询问。
REMOTE_ENGINE_PERMISSIONS = {
    "core:event:allow-listen",
    "core:event:allow-unlisten",
    "allow-arm-close-guard",
    "allow-resolve-close-request",
}


def _caps() -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(CAPS.glob("*.json"))}


def _rust_const(src: str, name: str) -> str:
    m = re.search(rf'const {name}: &str = "((?:[^"\\]|\\.)*)";', src)
    assert m, f"找不到 const {name}"
    return m.group(1)


# --------------------------------------------------------------------------- #
# 能力标记：引擎 ↔ 壳
# --------------------------------------------------------------------------- #
def test_the_version_endpoint_advertises_the_remote_window_feature():
    from tavotto import app as tavotto_app

    body = tavotto_app.app.test_client().get("/api/version").get_json()
    assert tavotto_app.DESKTOP_REMOTE_WINDOW_FEATURE in body["features"]
    # 壳的判据还要一个版本号（显示给用户）；老字段不许丢
    assert isinstance(body["version"], str) and "build" in body


@needs_shell
def test_the_feature_string_is_one_literal_on_both_sides():
    from tavotto import app as tavotto_app

    rs = _rust_const(REMOTE_RS.read_text(encoding="utf-8"), "REMOTE_WINDOW_FEATURE")
    assert rs == tavotto_app.DESKTOP_REMOTE_WINDOW_FEATURE == "desktop-remote-window"


# --------------------------------------------------------------------------- #
# 窗口标记：壳 ↔ 前端
# --------------------------------------------------------------------------- #
@needs_shell
def test_the_remote_window_marker_is_the_global_the_frontend_reads():
    marker = _rust_const(MAIN_RS.read_text(encoding="utf-8"), "REMOTE_WINDOW_MARKER")
    m = re.fullmatch(r"window\.(\w+) = true;", marker)
    assert m, f"标记的形状变了：{marker!r}"
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    fn = ts[ts.index("export function isRemoteEngineWindow") :]
    fn = fn[: fn.index("\n}\n")]
    assert f".{m.group(1)} === true" in fn, "前端读的全局名与壳注入的不是同一个"


def test_local_file_capabilities_are_gated_on_the_local_engine():
    """本机文件类能力按 `isDesktop()`（= 壳 + 本机引擎）判，壳本身的能力按 `hasDesktopShell()`。

    反了的两种坏法：本机能力用了 `hasDesktopShell()` → 远程窗口里弹本机对话框；
    菜单 / 关窗询问用了 `isDesktop()` → 远程窗口里 ⌘S 与关窗询问全部失灵。"""
    ts = DESKTOP_TS.read_text(encoding="utf-8")

    def guard(fn: str) -> str:
        start = ts.index(f"export async function {fn}(")
        body = ts[start : ts.index("\n}\n", start)]
        m = re.search(r"if \(!(\w+)\(\)", body)
        assert m, f"{fn} 开头没有桌面判据"
        return m.group(1)

    local = [
        "nativeFileDropAvailable",
        "onNativeFileDrop",
        "pickDirectory",
        "pickScriptFile",
        "revealExportedFile",
        "revealProjectFolder",
        "setDesktopMenuLocale",
        "runCodexIntegration",
        "checkDesktopUpdate",
        "onDesktopOpen",
    ]
    shell = [
        "onDesktopMenu",
        "armDesktopCloseGuard",
        "onDesktopCloseRequested",
        "resolveDesktopCloseRequest",
    ]
    assert {f: guard(f) for f in local} == dict.fromkeys(local, "isDesktop")
    assert {f: guard(f) for f in shell} == dict.fromkeys(shell, "hasDesktopShell")


# --------------------------------------------------------------------------- #
# 错误 code：壳 ↔ connect.html
# --------------------------------------------------------------------------- #
@needs_shell
def test_every_connect_error_code_has_text_in_both_languages():
    rs = REMOTE_RS.read_text(encoding="utf-8")
    impl = rs[rs.index("pub fn code(self)") :]
    impl = impl[: impl.index("\n    }\n")]
    codes = set(re.findall(r'=> "(\w+)"', impl))
    assert len(codes) >= 8, codes

    html = CONNECT_HTML.read_text(encoding="utf-8")
    for locale in ("zh-CN", "en-US"):
        start = html.index(f"'{locale}': {{")
        # 文案里有 `{port}`，按第一个 `}` 截会截在句子中间：截到 errors 自己那行收尾
        block = html[html.index("errors: {", start) + len("errors: {") :]
        block = block[: re.search(r"^\s*\},\s*$", block, re.M).start()]
        keys = set(re.findall(r"^\s*(\w+):", block, re.M))
        assert keys - {"unknown"} == codes, f"{locale}：{sorted(keys ^ codes)}"


# --------------------------------------------------------------------------- #
# 权限按窗口（闭集）
# --------------------------------------------------------------------------- #
@needs_shell
def test_capabilities_only_name_the_two_windows():
    for name, cap in _caps().items():
        assert set(cap["windows"]) <= {"main", "remote"}, f"{name} 给了陌生窗口权限：{cap['windows']}"


@needs_shell
def test_the_remote_window_gets_exactly_the_audited_permissions():
    caps = _caps()
    remote = {n: c for n, c in caps.items() if "remote" in c["windows"]}
    assert set(remote) == {"remote-engine.json", "remote-connect.json"}, sorted(remote)
    for name, cap in remote.items():
        assert cap["windows"] == ["remote"], f"{name} 不该同时给别的窗口"

    engine = remote["remote-engine.json"]
    # 引擎页面：只在回环上、只拿闭集里那几条；壳自带页面不在它的作用域里
    assert engine.get("local") is False
    assert engine["remote"]["urls"] == ["http://127.0.0.1:*"]
    assert set(engine["permissions"]) == REMOTE_ENGINE_PERMISSIONS

    connect = remote["remote-connect.json"]
    # connect.html：只在壳自带页面（tauri://）上，只有这一条命令
    assert connect.get("local") is True and "remote" not in connect
    assert connect["permissions"] == ["allow-connect-remote"]

    # 反向：主窗口拿不到连接命令（它的页面来自本机 sidecar，不需要，也不该能改远程窗口）
    assert "allow-connect-remote" not in caps["main.json"]["permissions"]


# --------------------------------------------------------------------------- #
# 远程窗口的建造链
# --------------------------------------------------------------------------- #
@needs_shell
def test_the_remote_window_is_isolated_and_marked():
    """无痕存储（cookie 按主机不按端口隔离，共用存储会顶掉本机会话、还把本机 token 发去远程）、
    注入标记、导航守卫、不装 native_drop——四条都在**真正建远程窗口的那条链**上。"""
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    site = window_builders(code)["REMOTE_WINDOW"]
    methods = chain_methods(code, site)
    for m in ("incognito", "initialization_script", "on_navigation", "build"):
        assert m in methods, f"远程窗口的建造链没有 {m}：{methods}"
    chain = code[site : code.index(";", code.index(".build", site))]
    assert re.search(r"\.incognito\(\s*true\s*\)", chain), "远程窗口没开无痕存储"
    assert re.search(r"\.initialization_script\(\s*REMOTE_WINDOW_MARKER\s*\)", chain)

    fn = code[code.index("fn open_remote_window(") :]
    fn = fn[: fn.index("\n}\n")]
    assert "native_drop" not in fn, "远程窗口不该旁听拖放：拖进来的是本机路径"
