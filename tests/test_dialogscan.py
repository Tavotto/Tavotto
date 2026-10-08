"""弹窗写法的静态识别（`engine/dialogscan.py`）：正例（含别名形态）、反例（注释 / 字符串 / 无 import 绑定 / 不执行的分支）、文件读取有界。"""

from __future__ import annotations

import textwrap

import pytest

from tavotto.engine import dialogscan


def _apis(src: str) -> list[str]:
    return [c["api"] for c in dialogscan.analyze(textwrap.dedent(src))["calls"]]


@pytest.mark.parametrize(
    ("src", "api"),
    [
        # tkinter 的各种 import 形态
        (
            "from tkinter import filedialog\nfiledialog.askopenfilename()\n",
            "tkinter.filedialog.askopenfilename",
        ),
        (
            "from tkinter import filedialog as fd\nfd.askopenfilenames()\n",
            "tkinter.filedialog.askopenfilenames",
        ),
        (
            "import tkinter.filedialog\ntkinter.filedialog.asksaveasfilename()\n",
            "tkinter.filedialog.asksaveasfilename",
        ),
        (
            "import tkinter.filedialog as fd\nfd.askdirectory()\n",
            "tkinter.filedialog.askdirectory",
        ),
        (
            "import tkinter as tk\nfrom tkinter import filedialog\ntk.filedialog.askopenfile()\n",
            "tkinter.filedialog.askopenfile",
        ),
        (
            "from tkinter.filedialog import askopenfilename as pick\npick()\n",
            "tkinter.filedialog.askopenfilename",
        ),
        ("from tkinter.filedialog import askopenfilename\naskopenfilename()\n", None),
        ("from tkinter import simpledialog\nsimpledialog.askstring('a', 'b')\n", None),
        ("from tkinter import messagebox as mb\nmb.askyesno('a', 'b')\n", None),
        # Qt 四家 + 两种写法
        ("from PyQt5.QtWidgets import QFileDialog\nQFileDialog.getOpenFileName()\n", None),
        ("from PyQt6.QtWidgets import QFileDialog as D\nD.getOpenFileNames()\n", None),
        ("from PySide6 import QtWidgets\nQtWidgets.QFileDialog.getSaveFileName()\n", None),
        ("import PySide2.QtWidgets as W\nW.QFileDialog.getExistingDirectory()\n", None),
        # 其他库
        ("import easygui\neasygui.fileopenbox()\n", "easygui.fileopenbox"),
        ("from easygui import fileopenbox as f\nf()\n", "easygui.fileopenbox"),
        ("import wx\nwx.FileDialog(None)\n", "wx.FileDialog"),
    ],
)
def test_recognizes_dialog_calls_through_every_import_shape(src, api):
    found = _apis(src)
    assert len(found) == 1, found
    if api is not None:
        assert found == [api]


def test_kinds_distinguish_file_pickers_from_prompts():
    out = dialogscan.analyze(
        "from tkinter import filedialog, simpledialog\n"
        "filedialog.askopenfilename()\nsimpledialog.askstring('a', 'b')\n"
    )
    assert [c["kind"] for c in out["calls"]] == ["file", "prompt"]
    assert [c["line"] for c in out["calls"]] == [2, 3]
    assert out["status"] == "found"


@pytest.mark.parametrize(
    "src",
    [
        # 注释 / 字符串 / docstring 里出现不算
        "# from tkinter import filedialog; filedialog.askopenfilename()\nx = 1\n",
        "s = 'filedialog.askopenfilename()'\nprint(s)\n",
        '"""Use tkinter.filedialog.askopenfilename() to pick."""\nx = 1\n',
        # 只 import 不调用
        "from tkinter import filedialog\nx = filedialog\n",
        # 没有 import 绑定的同名函数 / 对象
        "def askopenfilename():\n    return 'a.csv'\naskopenfilename()\n",
        "class filedialog:\n    @staticmethod\n    def askopenfilename():\n        return 'a'\nfiledialog.askopenfilename()\n",
        # 不是询问类 / 不是列入的 API
        "from tkinter import filedialog\nfiledialog.FileDialog\n",
        "from tkinter import messagebox\nmessagebox.showinfo('a', 'b')\n",
        "from PyQt5.QtWidgets import QFileDialog\nQFileDialog.something()\n",
        # 别的包里的同名
        "import pandas as pd\npd.read_csv('a.csv')\n",
        "import os\nos.fileopenbox()\n",
        # 确定不会执行的分支
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from tkinter import filedialog\n    filedialog.askopenfilename()\n",
        "from tkinter import filedialog\nif False:\n    filedialog.askopenfilename()\n",
        "from tkinter import filedialog\nif 0:\n    filedialog.askopenfilename()\n",
    ],
)
def test_does_not_flag_look_alikes_or_dead_branches(src):
    assert dialogscan.analyze(src)["calls"] == []
    assert dialogscan.analyze(src)["status"] == "none"


@pytest.mark.parametrize(
    "src",
    [
        # 常见写法：包在函数里、入口里调用
        "from tkinter import filedialog\ndef pick():\n    return filedialog.askopenfilename()\n",
        "from tkinter import filedialog\nif __name__ == '__main__':\n    filedialog.askopenfilename()\n",
        # 条件 / 异常分支里的也算（取舍见模块 docstring：多提示一次的代价只是点一下「仍然运行」）
        "import sys\nfrom tkinter import filedialog\nif len(sys.argv) < 2:\n    filedialog.askopenfilename()\n",
        "from tkinter import filedialog\ntry:\n    x = 1\nexcept Exception:\n    filedialog.askopenfilename()\n",
        # TYPE_CHECKING 的 else 是运行时那一半
        "from typing import TYPE_CHECKING\nfrom tkinter import filedialog\nif TYPE_CHECKING:\n    pass\nelse:\n    filedialog.askopenfilename()\n",
        # 在表达式里
        "from tkinter import filedialog\nimport pandas as pd\ndf = pd.read_csv(filedialog.askopenfilename())\n",
    ],
)
def test_counts_calls_in_functions_and_branches(src):
    assert len(dialogscan.analyze(src)["calls"]) == 1


def test_unparseable_source_is_unknown_not_a_guess():
    out = dialogscan.analyze("from tkinter import filedialog\nfiledialog.askopenfilename(\n")
    assert out == {"status": "unknown", "calls": [], "truncated": False}


def test_report_is_capped_and_says_so():
    src = "from tkinter import filedialog\n" + "filedialog.askopenfilename()\n" * 20
    out = dialogscan.analyze(src)
    assert len(out["calls"]) == dialogscan.MAX_CALLS and out["truncated"] is True


def test_kinds_cover_all_calls_and_sample_keeps_every_kind_when_capped():
    src = (
        "from tkinter import filedialog, simpledialog\n"
        + "filedialog.askopenfilename()\n" * 9
        + "simpledialog.askstring('a', 'b')\n"
    )
    out = dialogscan.analyze(src)
    assert out["kinds"] == ["file", "prompt"] and out["truncated"] is True
    assert len(out["calls"]) == dialogscan.MAX_CALLS
    assert [c["kind"] for c in out["calls"]].count("prompt") == 1
    assert [c["line"] for c in out["calls"]] == sorted(c["line"] for c in out["calls"])


def test_file_reading_is_bounded_and_cached(tmp_path, monkeypatch):
    p = tmp_path / "a.py"
    p.write_text("from tkinter import filedialog\nfiledialog.askopenfilename()\n", encoding="utf-8")
    assert dialogscan.analyze_file(p)["status"] == "found"
    assert dialogscan.analyze_file(tmp_path / "missing.py")["status"] == "unknown"
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 10, encoding="utf-8")
    monkeypatch.setattr(dialogscan, "MAX_SOURCE_BYTES", 5)
    assert dialogscan.analyze_file(big)["status"] == "unknown"
