"""脚本 stdin 替身与真文本流（`io.StringIO`）逐项一致（QA-WIN-55 / QA-WIN-56）。

两个替身——准备阶段的 `scriptinput.BridgedStdin` 与原生输入桥的 `bridge_runner._BridgeStdin`——同一语义
（同源对，见 docs/rules/repo/same-origin-pairs.md）：同一输入序列、同一串读操作，结果必须与 `StringIO` 逐项相等；
并且缓冲里已有的数据不再提问。
"""

from __future__ import annotations

import builtins
import getpass
import io
import sys

import pytest

from tavotto.engine import bridge_runner, scriptinput


class _FakeChannel:
    """按序给回答、数「问了几次」；准备阶段与原生桥共用（两边 ask 的参数顺序不同，各写一个入口）。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def _next(self, kind):
        # 与真通道一致：到尽头（回答用完）之后不再向用户提问
        if not self.answers:
            return None
        self.asked.append(kind)
        return self.answers.pop(0)

    def ask(self, prompt, kind):  # scriptinput.Channel 形状
        return self._next(kind)


class _FakeBridge(_FakeChannel):  # bridge_runner.InputBridge 形状
    def __init__(self, answers):
        super().__init__(answers)
        self.buffer = ""
        self.eof = False

    def ask(self, kind, prompt=""):
        return self._next(kind)


def _prep(monkeypatch, answers):
    ch = _FakeChannel(answers)
    monkeypatch.setattr(builtins, "input", builtins.input)
    monkeypatch.setattr(sys, "stdin", sys.stdin)
    monkeypatch.setattr(getpass, "getpass", getpass.getpass)
    scriptinput.install(ch)
    return ch, sys.stdin, builtins.input


def _bridge(answers):
    b = _FakeBridge(answers)
    stdin = bridge_runner._BridgeStdin(b)

    def _input(prompt=""):
        line = bridge_runner.InputBridge.take_buffered_line(b)
        if line is not None:
            return line
        answer = b.ask("input")
        if answer is None:
            raise EOFError
        return answer.split("\n", 1)[0].rstrip("\r")

    return b, stdin, _input


def _run(stdin, inp, ops):
    out = []
    for op, *arg in ops:
        try:
            out.append(inp() if op == "input" else getattr(stdin, op)(*arg))
        except EOFError:
            out.append("EOFError")
    return out


def _reference(text, ops):
    ref = io.StringIO(text)

    def _input():
        line = ref.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    return _run(ref, _input, ops)


LINE_ANSWERS = ["abc", "second", "aa", "bb", "cc", "last"]
LINE_TEXT = "".join(a + "\n" for a in LINE_ANSWERS)
LINE_CASES = {
    "readline(1) then input": [("readline", 1), ("input",), ("input",)],
    "readline(0) is a no-op": [("readline", 0), ("readline",), ("readline", 0)],
    "readline(size) bigger than the line": [("readline", 99), ("readline", 2), ("readline",)],
    "read(0) and readline(0)": [("read", 0), ("readline", 0), ("input",)],
    "readlines(3) hint equals the first line": [
        ("readline",),
        ("readline",),
        ("readlines", 3),
        ("input",),
    ],
    "readlines(2) overshoot": [("readline",), ("readline",), ("readlines", 2), ("readline",)],
    "readlines(1) exactly one char": [("readline",), ("readline",), ("readlines", 1), ("input",)],
    "readlines(0) and readlines(-1) read to EOF": [("readline",), ("readlines", 0)],
    "readline/input alternate": [
        ("readline", 2),
        ("input",),
        ("readline", 1),
        ("readline",),
        ("input",),
    ],
    "past EOF": [("readlines",), ("readline",), ("read",), ("input",)],
}


@pytest.mark.parametrize("name", list(LINE_CASES))
@pytest.mark.parametrize("impl", ["prep", "bridge"])
def test_line_mode_matches_a_real_text_stream(name, impl, monkeypatch):
    ops = LINE_CASES[name]
    if impl == "prep":
        ch, stdin, inp = _prep(monkeypatch, LINE_ANSWERS)
    else:
        ch, stdin, inp = _bridge(LINE_ANSWERS)
    assert _run(stdin, inp, ops) == _reference(LINE_TEXT, ops)
    assert len(ch.asked) <= len(LINE_ANSWERS)


READ_TEXT = "abcd\nzz"
READ_CASES = {
    "read(n) then input": [("read", 2), ("input",), ("input",), ("input",)],
    "read(n) then readline(n)": [("read", 1), ("readline", 2), ("readline",), ("readline",)],
    "read(-1)": [("read",), ("read",), ("readline",)],
    "read(n) twice": [("read", 3), ("read", 3), ("read", 3)],
}


@pytest.mark.parametrize("name", list(READ_CASES))
@pytest.mark.parametrize("impl", ["prep", "bridge"])
def test_read_mode_matches_a_real_text_stream_with_one_question(name, impl, monkeypatch):
    ops = READ_CASES[name]
    if impl == "prep":
        ch, stdin, inp = _prep(monkeypatch, [READ_TEXT])
    else:
        ch, stdin, inp = _bridge([READ_TEXT])
    assert _run(stdin, inp, ops) == _reference(READ_TEXT, ops)
    assert ch.asked[0] == "read"


@pytest.mark.parametrize("impl", ["prep", "bridge"])
def test_a_buffer_hit_does_not_ask_again(impl, monkeypatch):
    """QA-WIN-55 的原样：`readline(1)` 之后的两个 `input()`，只问两次，读到 a / bc / last。"""
    if impl == "prep":
        ch, stdin, inp = _prep(monkeypatch, ["abc", "last"])
    else:
        ch, stdin, inp = _bridge(["abc", "last"])
    assert _run(stdin, inp, [("readline", 1), ("input",), ("input",)]) == ["a", "bc", "last"]
    assert ch.asked == ["readline", "input"]


@pytest.mark.parametrize("impl", ["prep", "bridge"])
def test_readlines_hint_equal_to_total_keeps_reading(impl, monkeypatch):
    """CPython 的 `readlines(hint)` 是累计长度「超过」hint 才停（QA-WIN-56）：'aa\\n' 恰好 3 个字符，要继续读。"""
    if impl == "prep":
        ch, stdin, _inp = _prep(monkeypatch, ["aa", "bb", "cc"])
    else:
        ch, stdin, _inp = _bridge(["aa", "bb", "cc"])
    assert stdin.readlines(3) == io.StringIO("aa\nbb\ncc\n").readlines(3) == ["aa\n", "bb\n"]
    assert ch.asked == ["readline", "readline"]
