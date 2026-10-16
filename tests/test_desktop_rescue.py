"""原生救援页（ADR 0130）：权限边界与页面文案的结构看护。

Rust 侧另有单测（`rescue.rs`：调用方判据、命令先过闸、restart 之前先收 sidecar 写 clean）；
这里站在 Rust 之外读 JSON / HTML / TypeScript，回答三件事：

* 救援 / 更新 / 报告 / 进程控制的权限**只落在**「主窗口 + 本机壳页面」这一份 capability 上——引擎页（127.0.0.1）
  和任何远程实例窗口一条都拿不到；
* `error.html` 脚本引用的每一条文案两种语言都有（页面不在前端构建里，漏翻没有别的东西会报）；
* 前端（引擎页）的代码里不认识这些命令。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.support.rustsrc import (
    allow_permission,
    handler_commands,
    manifest_commands,
    tauri_commands,
)

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
CAPS = TAURI / "capabilities"
ERROR_HTML = TAURI / "shell" / "error.html"

pytestmark = pytest.mark.skipif(
    not ERROR_HTML.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)

RESCUE = [
    "rescue_status",
    "rescue_retry",
    "rescue_update_check",
    "rescue_update_install",
    "rescue_diag_preview",
    "rescue_diag_send",
    "rescue_diag_save",
]


def _cap(name: str) -> dict:
    return json.loads((CAPS / name).read_text(encoding="utf-8"))


def test_rescue_commands_exist_in_all_three_places_and_only_the_rescue_capability_grants_them():
    cmds, manifest, handler = tauri_commands(), manifest_commands(), handler_commands()
    for name in RESCUE:
        assert name in cmds and name in manifest and name in handler, name
    cap = _cap("rescue-page.json")
    assert sorted(cap["permissions"]) == sorted(allow_permission(n) for n in RESCUE)
    # 只给主窗口的本机壳页面：local，不含任何 remote URL（所以 127.0.0.1 上的引擎页拿不到）
    assert cap["windows"] == ["main"] and cap["local"] is True and "remote" not in cap
    granted_elsewhere = {
        p: f.name
        for f in CAPS.glob("*.json")
        if f.name != "rescue-page.json"
        for p in _cap(f.name)["permissions"]
        if p.startswith("allow-rescue")
    }
    assert not granted_elsewhere, f"别的 capability 放行了救援命令：{granted_elsewhere}"


def test_remote_instance_windows_get_no_update_file_report_or_process_control():
    banned = (
        "allow-rescue",
        "allow-desktop-update",
        "allow-restart-engine",
        "dialog:",
        "process:",
        "updater:",
        "fs:",
        "shell:",
        "opener:",
    )
    for name in ("remote-connect.json", "remote-engine.json"):
        cap = _cap(name)
        assert "remote" in cap["windows"] and "main" not in cap["windows"]
        for p in cap["permissions"]:
            assert not p.startswith(banned), f"{name} 放行了 {p}"


def _inline_script(html: str) -> str:
    """页面里唯一的内联 <script> 正文（用 HTML 解析器取，不用正则匹配标签）。"""

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.depth = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.depth += 1

        def handle_endtag(self, tag):
            if tag == "script":
                self.depth -= 1

        def handle_data(self, data):
            if self.depth:
                self.parts.append(data)

    p = _P()
    p.feed(html)
    p.close()
    assert p.parts, "error.html 里没有内联脚本"
    return "".join(p.parts)


def test_error_page_uses_only_strings_both_languages_define():
    text = ERROR_HTML.read_text(encoding="utf-8")
    script = _inline_script(text)

    def block(tag: str) -> set[str]:
        start = script.index(f"'{tag}': {{")
        end = script.index("\n      },", start)
        return set(re.findall(r"^\s{8}(\w+):", script[start:end], re.M))

    zh, en = block("zh-CN"), block("en-US")
    assert zh == en
    used = set(re.findall(r"\bs\.(\w+)\b", script)) | set(re.findall(r"\['\w+', '(\w+)'\]", script))
    missing = used - zh
    assert not missing, f"脚本引用了没有定义的文案：{missing}"


def test_error_page_never_sends_the_raw_error_text_anywhere():
    """`msg` / `log` 只显示，不进任何 invoke 参数（报告的字段由壳按闭集自己填）。"""
    script = _inline_script(ERROR_HTML.read_text("utf-8"))
    for m in re.finditer(r"call\('(\w+)'(?:,\s*(\{[^}]*\}))?", script):
        args = m.group(2) or ""
        assert "msg" not in args and "log" not in args, m.group(0)


def test_the_engine_pages_code_does_not_know_the_rescue_commands():
    src = (ROOT / "web" / "src" / "lib" / "desktop.ts").read_text(encoding="utf-8")
    assert "rescue_" not in src
