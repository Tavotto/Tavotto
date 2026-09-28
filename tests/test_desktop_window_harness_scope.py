"""真窗口用例（`tests/desktop_windows/`）的输入主语：**只对被测窗口**，绝不注入全局输入。

全局输入（`SendInput` 一族）的主语是「此刻前台的任何应用」——在开发机上那可能是用户的
聊天窗口（2026-09-26 子 Agent 用 System Events 发的按键就落进过微信）。真窗口夹具只许按
窗口句柄投消息、经 CDP 进渲染进程。这条判据放在目录**外面**：那个目录在 Windows 真窗口
腿以外整目录 skip，放进去就只在一台机器上执行（skip 不是绿）。

AST 判名字，不判子串：注释与文档字符串里写着「这里没有 SendInput」不该让它红，
而 `getattr(user32, "Send" + "Input")` 这种拼接也要抓——所以字符串常量同样扫。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "desktop_windows"

#: 向系统输入队列注入的 API（user32 / 各种自动化库的入口）与抢前台的那一个
GLOBAL_INPUT = {
    "SendInput",
    "keybd_event",
    "mouse_event",
    "SetCursorPos",
    "SetForegroundWindow",
    "SendKeys",
    "SendWait",
    "pyautogui",
    "pynput",
    "keyboard",
}


def _identifiers(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.update(node.name.split("."))
    return out


def _string_constants(tree: ast.AST) -> set[str]:
    """非文档字符串的字符串常量（`getattr(user32, "SendInput")` 这种绕法）。"""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docs.add(d)
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docs
    }


def _files() -> list[Path]:
    files = sorted(HARNESS.glob("*.py"))
    assert len(files) >= 5, f"tests/desktop_windows 里的 .py 不够——目录挪了？{files}"
    return files


def test_harness_never_names_a_global_input_api():
    hits = []
    for f in _files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for name in _identifiers(tree) & GLOBAL_INPUT:
            hits.append(f"{f.name}: 标识符 {name}")
        for s in _string_constants(tree):
            for token in re.findall(r"[A-Za-z_]\w*", s):
                if token in GLOBAL_INPUT - {"keyboard"}:  # 「keyboard」作普通词出现在用例文案里无妨
                    hits.append(f"{f.name}: 字符串里的 {token}")
    assert not hits, "真窗口夹具里出现了全局输入 API：" + "；".join(hits)


def test_the_scan_sees_a_planted_call():
    """自看护：判据真的能抓到一次 `user32.SendInput(...)`（不是恒绿）。"""
    tree = ast.parse(
        "import ctypes\nuser32 = ctypes.WinDLL('user32')\nuser32.SendInput(1, None, 0)\n"
    )
    assert "SendInput" in _identifiers(tree)
    tree = ast.parse("getattr(user32, 'Send' 'Input')(1)\n")
    assert "SendInput" in _string_constants(tree)
    # 文档字符串里提到它不算
    tree = ast.parse('"""这里没有 SendInput。"""\n')
    assert "SendInput" not in _identifiers(tree)
    assert not any("SendInput" in s for s in _string_constants(tree))


def test_menu_cases_never_invoke_the_predefined_clipboard_items():
    """muda 在 Windows 上把预定义的剪切 / 复制 / 粘贴 / 全选实现成 `SendInput`：点它们
    就是向前台注入按键。菜单用例只按 `WIRED` 里的加速键认项，那里不许有这四个。"""
    src = (HARNESS / "test_menu_keyboard_focus.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wired = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "WIRED" for t in n.targets)
    ]
    assert len(wired) == 1, "test_menu_keyboard_focus.py 里读不出唯一的 WIRED"
    accels = ast.literal_eval(wired[0])
    assert accels, "WIRED 是空的"
    assert not set(accels) & {"Ctrl+X", "Ctrl+C", "Ctrl+V", "Ctrl+A"}, accels
