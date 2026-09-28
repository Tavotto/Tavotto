"""主页拖放拿真实路径（ADR 0092）：壳与前端两侧同源 + 命令 ACL 三处齐全。

与 `test_desktop_close_guard.py` 同一个理由站在两侧之外：事件名、载荷的 kind 词汇表
壳写一份、前端写一份，漂了之后 `cargo test` 与 vitest 各自都是绿的，只在真机上
表现为「拖进来什么都不发生」。`native_file_drop` 命令漏登记也是静默失败
（invoke 被拒 → 前端读成「不支持」→ 永远走降级）。

`src-tauri/` 不进 wheel/sdist，因此整个文件在没有它的树上跳过。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.rustsrc import (
    allow_permission,
    assert_unconditionally_in,
    capability_permissions,
    chain_methods,
    handler_commands,
    manifest_commands,
    rust_code,
    tauri_commands,
    window_builders,
)

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
MAIN_RS = TAURI / "src" / "main.rs"
DROP_RS = TAURI / "src" / "drop_paths.rs"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"

pytestmark = pytest.mark.skipif(
    not MAIN_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)


def _rust_event() -> str:
    m = re.search(r'pub const EVENT: &str = "([^"]+)";', DROP_RS.read_text(encoding="utf-8"))
    assert m, "drop_paths.rs 里找不到 EVENT 常量"
    return m.group(1)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _rust_kinds() -> set[str]:
    src = DROP_RS.read_text(encoding="utf-8")
    body = src.split("pub enum DropTarget {", 1)[1].split("\n}\n", 1)[0]
    # 变体名：行首缩进 4 格、大写开头、后面跟 `{`（带字段的变体）
    return {_snake(v) for v in re.findall(r"^    ([A-Z][A-Za-z]+) \{", body, re.M)}


def _ts_kinds() -> set[str]:
    src = DESKTOP_TS.read_text(encoding="utf-8")
    body = src.split("export type NativeFileDrop =", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"kind: '([a-z_]+)'", body))


def test_the_event_name_is_one_string_on_both_sides():
    event = _rust_event()
    assert event == "tavotto:file-drop"
    assert f"'{event}'" in DESKTOP_TS.read_text(encoding="utf-8")


def test_the_payload_kinds_are_the_same_closed_set_on_both_sides():
    rust, ts = _rust_kinds(), _ts_kinds()
    assert rust == {"script", "folder", "unsupported"}, rust
    assert ts == rust


def test_native_file_drop_is_declared_in_all_three_places():
    # 三处都读结构（`tests/support/rustsrc.py`）：注释里、无关字符串里写着命令名不算登记
    assert "native_file_drop" in tauri_commands(), "main.rs 里没有这个 #[tauri::command]"
    assert "native_file_drop" in manifest_commands(), "build.rs 的 AppManifest::commands 里没有它"
    assert allow_permission("native_file_drop") in capability_permissions(), "capability 没放行它"
    assert "native_file_drop" in handler_commands(), "generate_handler 里没有它"
    assert "'native_file_drop'" in DESKTOP_TS.read_text(encoding="utf-8")


def test_the_tauri_drag_drop_handler_stays_disabled():
    """真实路径靠旁听拿，不是把 Tauri 的拖放处理器打开——打开它页面里的 HTML5 拖放整片失效。

    判据的主语是**真正建窗口的那几条建造链**，不是 main.rs 里某个子串（Codex #665 P1：
    写进注释、字符串、`if false` 或一个没人调的函数里，子串照样在）。所以：剥掉注释与
    字面量后，`WebviewWindowBuilder::new(` 全壳恰好两处——主窗口（`fn main`）与远程实例
    窗口（`fn open_remote_window`，ADR 0105）；每条链都在第 0 层调了
    `disable_drag_drop_handler()`、且在 `.build()` 之前；它们所在的每一层块都不是条件 /
    循环 / `#[cfg]` 块。远程窗口不装 native_drop（拖进来的是本机路径，远程引擎用不了），
    但页面里的 HTML5 拖放（素材拖进画布）同样要还给页面。"""
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    sites = window_builders(code)
    assert set(sites) == {"MAIN_WINDOW", "REMOTE_WINDOW"}, sorted(sites)
    for label, outer in (("MAIN_WINDOW", "main"), ("REMOTE_WINDOW", "open_remote_window")):
        methods = chain_methods(code, sites[label])
        assert "disable_drag_drop_handler" in methods, (
            f"{label} 的建造链没关 Tauri 拖放处理器：{methods}"
        )
        assert "build" in methods, f"建造链没有 build()：{methods}"
        assert methods.index("disable_drag_drop_handler") < methods.index("build"), methods
        assert_unconditionally_in(code, sites[label], outer)
