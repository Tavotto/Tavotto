"""主页拖放拿真实路径（ADR 0092）：壳与前端两侧同源 + 命令 ACL 三处齐全。

与 `test_desktop_close_guard.py` 同一个理由站在两侧之外：事件名、载荷的 kind 词汇表
壳写一份、前端写一份，漂了之后 `cargo test` 与 vitest 各自都是绿的，只在真机上
表现为「拖进来什么都不发生」。`native_file_drop` 命令漏登记也是静默失败
（invoke 被拒 → 前端读成「不支持」→ 永远走降级）。

`src-tauri/` 不进 wheel/sdist，因此整个文件在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

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
    build_rs = (TAURI / "build.rs").read_text(encoding="utf-8")
    cap = json.loads((TAURI / "capabilities" / "main.json").read_text(encoding="utf-8"))
    handler = MAIN_RS.read_text(encoding="utf-8").split("generate_handler![")[1].split("]")[0]
    assert '"native_file_drop"' in build_rs, "build.rs 的 AppManifest::commands 里没有它"
    assert "allow-native-file-drop" in cap["permissions"], "capability 没放行它"
    assert "native_file_drop" in handler, "generate_handler 里没有它"
    assert "'native_file_drop'" in DESKTOP_TS.read_text(encoding="utf-8")


def test_the_tauri_drag_drop_handler_stays_disabled():
    """真实路径靠旁听拿，不是把 Tauri 的拖放处理器打开——打开它页面里的 HTML5 拖放整片失效。"""
    assert ".disable_drag_drop_handler()" in MAIN_RS.read_text(encoding="utf-8")
