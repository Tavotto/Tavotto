"""主窗口屏幕物理 ppi（ADR 0168 第二节）：壳 ↔ 前端同源 + 只给主窗口。

壳（`src-tauri/src/screen_ppi.rs`）在主窗口换屏 / 换缩放时发 `tavotto:screen-ppi`（载荷 `{ppi}`），页面
（`web/src/lib/desktop.ts`）订阅同名事件并可回读 `screen_ppi` 命令；点阵进度条按它选点径。
事件名漂了 = 换屏后点径不跟着变；命令少了 ACL / 注册 = 回读被静默拒绝，一直按缩放倍数取整；
两侧「合理范围」不一致 = 壳当成读不到的数页面却拿去用（或反过来）。

`src-tauri/` 不进 wheel/sdist，这几条在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.rustsrc import (
    allow_permission,
    handler_commands,
    manifest_commands,
    rust_code,
    tauri_commands,
)

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
PPI_RS = TAURI / "src" / "screen_ppi.rs"
MAIN_RS = TAURI / "src" / "main.rs"
CAPS = TAURI / "capabilities"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"

pytestmark = pytest.mark.skipif(not TAURI.is_dir(), reason="src-tauri/ 不在这棵树上")

# 注释里写着的名字 / 常量都不算（判结构，不判子串）：Rust 走 `rust_code`，TS 先抹掉注释
_TS_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _rs(path: Path) -> str:
    return rust_code(path.read_text(encoding="utf-8"), keep_strings=True)


def _ts() -> str:
    return _TS_COMMENT.sub("", DESKTOP_TS.read_text(encoding="utf-8"))


def test_event_name_and_payload_are_one_contract():
    rs = _rs(PPI_RS)
    m = re.search(r'pub const SCREEN_PPI_EVENT: &str = "([^"]+)";', rs)
    ts = _ts()
    t = re.search(r"export const SCREEN_PPI_EVENT = '([^']+)'", ts)
    assert m and t and m.group(1) == t.group(1) == "tavotto:screen-ppi", (m, t)
    main = _rs(MAIN_RS)
    assert 'serde_json::json!({ "ppi": now })' in main
    assert "e.payload?.ppi" in ts


def test_plausible_range_is_the_same_on_both_sides():
    rs = _rs(PPI_RS)
    m = re.search(r"\(([\d.]+)\.\.=([\d.]+)\)\.contains\(&ppi\)", rs)
    ts = _ts()
    t = re.search(r"v >= (\d+) && v <= (\d+) \? v : null", ts)
    assert m and t, (m, t)
    assert (float(m.group(1)), float(m.group(2))) == (float(t.group(1)), float(t.group(2)))


def test_command_is_registered_and_granted_to_the_main_window_only():
    """三处都读结构（`tests/support/rustsrc.py`）：注释掉的条目不算登记（Codex #1694）。"""
    assert "screen_ppi" in tauri_commands()
    assert "screen_ppi" in manifest_commands(), (
        "build.rs 的 AppManifest::commands 里没有 screen_ppi"
    )
    assert "screen_ppi" in handler_commands(), "generate_handler 里没有 screen_ppi"
    caps = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(CAPS.glob("*.json"))}
    allow = allow_permission("screen_ppi")
    assert allow in caps["main.json"]["permissions"]
    for name, cap in caps.items():
        if name != "main.json":
            assert allow not in cap.get("permissions", []), name
    main = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    # 命令只读缓存、并且拒绝非主窗口
    body = main[main.index("fn screen_ppi(") :]
    body = body[: body.index("\n}\n")]
    assert "only_main_window(&window)?" in body
    assert "screen_ppi::read" not in body, (
        "命令不许在命令线程里读屏幕（macOS 的 NSWindow 只在主线程碰）"
    )


def test_reread_on_move_and_scale_change_and_at_startup():
    main = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    hook = main[main.index(".on_window_event(") :]
    hook = hook[: hook.index("announce_screen_ppi(window)") + 1]
    assert "WindowEvent::Moved" in hook and "WindowEvent::ScaleFactorChanged" in hook
    setup = main[main.index(".setup(|app|") :]
    assert "announce_screen_ppi(&win" in setup
