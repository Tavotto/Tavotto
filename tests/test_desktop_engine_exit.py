"""引擎意外退出的检测 / 重启与退出标记（ADR 0129）里**跨语言、跨文件**的同源关系。

Rust 侧的判定表、损坏文件、时序、`poll_exit` 与 `shutdown` 互斥、「绕过 `RunEvent::Exit` 的退出路径先写 clean」
由 `src-tauri` 的 `cargo test` 看护；前端的回退与卡片由 vitest 看护。这里只钉 Python 测试能看到、cargo 与 vitest
各看不全的东西：

* 三条新命令只给主窗口，**远程实例窗口一条都没有**（ADR 0105）；命令体里都先核窗口 label；
* 事件名、引擎退出类型闭集、上次结局闭集：Rust ↔ `web/src/lib/desktop.ts` 严格同源；
* 退出标记文件名在 ADR / 隐私声明里被如实声明，且声明了「不上传」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.rustsrc import TAURI, rust_code

ROOT = Path(__file__).resolve().parent.parent
MAIN_RS = TAURI / "src" / "main.rs"
MARKER_RS = TAURI / "src" / "exit_marker.rs"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"
COMMANDS = ("take_previous_exit", "current_engine_exit", "restart_engine")


def _caps() -> dict[str, dict]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((TAURI / "capabilities").glob("*.json"))
    }


def test_main_window_has_the_three_commands_and_no_remote_window_does():
    caps = _caps()
    main = set(caps["main.json"]["permissions"])
    for name in COMMANDS:
        perm = "allow-" + name.replace("_", "-")
        assert perm in main, perm
        for cap_name, cap in caps.items():
            if "remote" in cap["windows"]:
                assert perm not in cap["permissions"], (cap_name, perm)
    # 重启引擎没有走 webview 自带的 process 插件命令，也没有给 exit 一类的权限
    assert not [p for p in main if p.startswith("process:") and p != "process:allow-restart"]


def test_every_new_command_checks_the_window_label_first():
    code = rust_code(MAIN_RS.read_text(encoding="utf-8"))
    for name in COMMANDS:
        m = re.search(rf"fn {name}\(", code)
        assert m, name
        body = code[m.end() : m.end() + 600]
        assert "only_main_window(&window)?" in body, f"{name} 没先核窗口"
        # 参数里带 WebviewWindow，命令才拿得到调用方窗口
        assert "tauri::WebviewWindow" in body, name
    helper = code[code.index("fn only_main_window(") :][:300]
    assert "MAIN_WINDOW" in helper


def _rust_str_const(src: str, name: str) -> str:
    code = rust_code(src, keep_strings=True)
    m = re.findall(rf'const {name}: &str = "([^"]*)";', code)
    assert len(m) == 1, (name, m)
    return m[0]


def _ts_str_const(name: str) -> str:
    m = re.findall(rf"export const {name} = '([^']*)'", DESKTOP_TS.read_text(encoding="utf-8"))
    assert len(m) == 1, (name, m)
    return m[0]


def _ts_tuple(name: str) -> list[str]:
    m = re.search(rf"export const {name} = \[(.*?)\] as const", DESKTOP_TS.read_text("utf-8"), re.S)
    assert m, name
    return re.findall(r"'([^']+)'", m.group(1))


def _rust_as_str(enum: str) -> list[str]:
    code = rust_code(MARKER_RS.read_text(encoding="utf-8"), keep_strings=True)
    impl = code[code.index(f"impl {enum} {{") :]
    body = impl[impl.index("pub fn as_str") :][:500]
    return re.findall(r'=> "([^"]+)"', body)


def test_event_name_is_one_literal_on_both_sides():
    rs = _rust_str_const(MAIN_RS.read_text(encoding="utf-8"), "ENGINE_EXITED_EVENT")
    assert rs == _ts_str_const("ENGINE_EXITED_EVENT") == "tavotto:engine-exited"


def test_closed_sets_match_between_shell_and_frontend():
    assert (
        _rust_as_str("EngineExit")
        == _ts_tuple("ENGINE_EXIT_KINDS")
        == ["signal", "nonzero", "zero"]
    )
    assert (
        _rust_as_str("Previous") == _ts_tuple("PREVIOUS_EXITS") == ["app_unclean", "engine_exited"]
    )


def test_marker_file_is_declared_to_users_and_documented_as_not_uploaded():
    name = _rust_str_const(MARKER_RS.read_text(encoding="utf-8"), "FILE_NAME")
    assert name == "session-state.json"
    privacy = (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")
    assert name in privacy, "隐私声明没有如实声明本机退出标记"
    adr = next((ROOT / "docs" / "adr").glob("0129-abnormal-exit-marker.md")).read_text(
        encoding="utf-8"
    )
    assert name in adr
    assert "不上传" in adr
