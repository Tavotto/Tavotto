"""字体来源：**这个仓库不分发任何字体**（Prompt 14 §四 / `00_SHARED_RULES` §10）。

字形回退很容易滑向「把一个覆盖全的字体塞进包里就都解决了」。那条路的代价是
许可证：字体是独立作品，AGPL 的仓库照样不能随手带一份别人的 .ttf 出门。所以
本仓库的每一张脸都必须来自
* PyMuPDF 自带的 base-14 / CJK / 隐式回退（随 PyMuPDF 的许可证走），或
* matplotlib 自带的 DejaVu（随 matplotlib 走），或
* 用户自己机器上装的字体。

这几条不是靠记性维持——**下面每一条都可以被一次提交破坏，所以每一条都要有
判据**。
"""

import re
import subprocess
from pathlib import Path

import pytest

from tavotto import pdfbackend

ROOT = Path(__file__).resolve().parent.parent
FONT_SUFFIXES = (
    ".ttf",
    ".otf",
    ".ttc",
    ".otc",
    ".woff",
    ".woff2",
    ".pfb",
    ".pfa",
    ".eot",
    ".dfont",
)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        # Windows 上不给 encoding 就按系统代码页解码，中文路径会解成乱码
        encoding="utf-8",
        check=True,
    )
    return out.stdout.splitlines()


def test_repository_ships_no_font_binaries():
    """版本库里一个字体文件都没有。

    判据是 **git 登记的文件**，不是磁盘上的文件：node_modules 与构建产物里
    当然有字体，它们不进分发。
    """
    fonts = [f for f in _tracked_files() if f.lower().endswith(FONT_SUFFIXES)]
    assert fonts == [], f"仓库里出现了字体文件，先确认许可证：{fonts}"


def test_no_web_font_is_fetched_or_embedded():
    """前端不下载远程字体，也不内嵌 base64 字体。

    远程字体在离线的桌面壳里就是一次静默降级；内嵌的那种则等于把字体
    分发出去了，只是换了个编码。
    """
    bad: list[str] = []
    face = re.compile(r"@font-face|fonts\.googleapis\.com|fonts\.gstatic\.com|font/woff")
    for rel in _tracked_files():
        if not rel.startswith("web/") or not rel.endswith((".ts", ".tsx", ".css", ".html")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        if face.search(text):
            bad.append(rel)
    assert bad == [], f"这些文件在引入外部/内嵌字体：{bad}"


def test_canvas_faces_all_come_from_the_backend_builtins():
    """画布文字的每一张脸都是 PyMuPDF 的内建名字，没有一个来自文件。

    `pymupdf.Font(fontfile=…)` / `fontbuffer=…` 是「用一份我们自己带的字体」
    的入口——它一旦出现在后端里，上面那条「仓库里没有字体文件」就会被绕过去
    （字体可以从别处下载再喂进来）。
    """
    source = (ROOT / "src" / "tavotto" / "pdfbackend" / "pymupdf_backend.py").read_text(
        encoding="utf-8"
    )
    assert "fontfile" not in source
    assert "fontbuffer" not in source


def test_declared_dependencies_bring_no_font_package():
    """依赖里没有字体包（`pymupdf-fonts` 那一类）。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("pymupdf-fonts", "pymupdf_fonts", "fonts-", "font-roboto"):
        assert name not in text, f"pyproject 里出现了字体包：{name}"


@pytest.mark.parametrize("family", pdfbackend.CANVAS_TEXT_FAMILIES)
def test_every_offered_family_can_actually_be_drawn(family):
    """下拉里摆出来的每一个族，后端都真的画得出（T-78）。

    「摆一个画不出来的选项」的表现是：用户选中了、界面报告成功、导出的字形
    没有变。这条用最平凡的一串 ASCII 量它——族解析不出来时 `latin_font`
    会抛，或者悄悄回默认族，两种都会让这里红。
    """
    from tavotto.pdfbackend import pymupdf_backend as backend

    assert backend.latin_family(family) == family
    assert pdfbackend.text_plan("Sample", family=family) == [("Sample", "primary")]
