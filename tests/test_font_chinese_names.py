"""字体的中文名：下拉里显示中文名，只有中文名的字体选得中、画得出。

两件事同一个根：matplotlib 登记字体用的是 FreeType 的 `family_name`，而 FreeType
把 name 表记录转成 ASCII，非 ASCII 码元一律 `?`。

* 有英文名的中文字体（Songti SC）登记成英文名：能用，下拉里认不出——manifest 顶层
  `font_family_names` 给它配中文显示名（`overrides.font_display_names`），值不变；
* 只有中文名的（狮尾、方正、汉仪……）登记成 `??????SC`：下拉不列、按名字分不清——
  `overrides.register_font_name_aliases` 用 FreeType 当初读的那条记录**正确解码**出
  的真名补登记同一张脸。

用例不依赖本机装了什么：现场用 fontTools（matplotlib 的依赖）把随 matplotlib 分发的
DejaVu Serif 改写 name 表，造出「只有繁体中文名」与「英文名 + 简体中文名」两种字体。
全部跑在 worker 解释器里（本进程不 import matplotlib）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"

#: 造字体：DejaVu Serif 换一张 name 表。`names` 是 [(nameID, 平台, 编码, 语言, 文本)]。
MAKE_FONTS = """\
import os
import matplotlib
from fontTools.ttLib import TTFont


def make_font(out, names):
    src = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSerif.ttf")
    f = TTFont(src)
    table = f["name"]
    table.names = []
    for nid, plat, enc, lang, text in names:
        table.setName(text, nid, plat, enc, lang)
    f.save(out)
    return out
"""

_DRIVER = (
    """\
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
"""
    + MAKE_FONTS
    + """
import overrides

tmp = tempfile.mkdtemp()
ONLY_ZH = "測試宋體甲"
only_zh = make_font(os.path.join(tmp, "only-zh.ttf"), [
    (1, 3, 1, 0x0404, ONLY_ZH), (2, 3, 1, 0x0404, "Regular"),
    (4, 3, 1, 0x0404, ONLY_ZH), (6, 3, 1, 0x0409, "TavottoOnlyZh"),
])
EN = "Tavotto Name Test"
both = make_font(os.path.join(tmp, "both.ttf"), [
    (1, 3, 1, 0x0409, EN), (1, 3, 1, 0x0804, "塔沃名测试"), (1, 3, 1, 0x0404, "塔沃名測試"),
    (2, 3, 1, 0x0409, "Regular"), (4, 3, 1, 0x0409, EN), (6, 3, 1, 0x0409, "TavottoNameTest"),
])
overrides.register_font_name_aliases()  # 先把本机已有的补完，下面只量新加的
fm.fontManager.addfont(only_zh)
fm.fontManager.addfont(both)

# 前提：matplotlib 自己登记的名字确实读坏了（否则这条用例量不到任何东西）
reg = [str(e.name) for e in fm.fontManager.ttflist if e.fname == only_zh]
assert reg and all("?" in n for n in reg), reg

# 1) 补登记：按真名找得到，而且找到的是那一个文件
assert overrides.register_font_name_aliases() >= 1
assert overrides.register_font_name_aliases() == 0  # 幂等
found = fm.findfont(fm.FontProperties(family=[ONLY_ZH]), fallback_to_default=False)
assert os.path.samefile(found, only_zh), found
assert overrides.font_installed(ONLY_ZH)
names = {str(e.name) for e in fm.fontManager.ttflist if e.fname == only_zh}
# 同一个文件只多出一个名字，就是真名
assert {n for n in names if "?" not in n} == {ONLY_ZH}, names

# 1b) 本机实例（【肆柒】忍冬藤）：typographic family（nameID 16）与 family（nameID 1）是
#     两个中文名，FreeType 读的是 16、读坏了；matplotlib 3.11 自己按英文语言 ID 的
#     nameID 1 记录补登记了一个正确的 Unicode 名。这张脸已经有真名，不再补第二个
#     （3.10 没有那条补登记，这里补的就是唯一的那个）
two = make_font(os.path.join(tmp, "two-names.ttf"), [
    (16, 3, 1, 0x0404, "中药合集乙"), (1, 3, 1, 0x0409, "忍冬藤乙"),
    (2, 3, 1, 0x0409, "Regular"), (6, 3, 1, 0x0409, "TavottoTwoNames"),
])
fm.fontManager.addfont(two)
overrides.register_font_name_aliases()
two_names = {str(e.name) for e in fm.fontManager.ttflist if e.fname == two}
assert len({n for n in two_names if "?" not in n}) == 1, two_names

# 2) 增量：之后才 addfont 的字体照样补上
late = make_font(os.path.join(tmp, "late.ttf"), [
    (1, 3, 1, 0x0804, "后加测试体"), (2, 3, 1, 0x0804, "Regular"),
    (6, 3, 1, 0x0409, "TavottoLate"),
])
fm.fontManager.addfont(late)
assert overrides.register_font_name_aliases() >= 1
assert os.path.samefile(
    fm.findfont(fm.FontProperties(family=["后加测试体"]), fallback_to_default=False), late
)

# 3) 显示名：英文名配简体中文名（简体优先于繁体）；没有中文名的不出现；值还是英文名
shown = overrides.font_display_names([EN, "DejaVu Sans", ONLY_ZH])
assert shown.get(EN) == "塔沃名测试", shown
assert "DejaVu Sans" not in shown and ONLY_ZH not in shown, shown

# 4) manifest 顶层的显示名表随字体注册表的代次失效：先发过一份 manifest，之后脚本
#    （或 fname 放开）才 addfont 的字体，下一份就要有它的中文显示名
import manifest

LATER = "Tavotto Later Name"
before = manifest.installed_font_display_names()
assert LATER not in before
later = make_font(os.path.join(tmp, "later-name.ttf"), [
    (1, 3, 1, 0x0409, LATER), (1, 3, 1, 0x0804, "塔沃后加名"),
    (2, 3, 1, 0x0409, "Regular"), (6, 3, 1, 0x0409, "TavottoLaterName"),
])
fm.fontManager.addfont(later)
after = manifest.installed_font_display_names()
assert after.get(LATER) == "塔沃后加名", after.get(LATER)
assert LATER in manifest.installed_font_families()
# 注册表没变：不重读（同一个对象）
assert manifest.installed_font_display_names() is after
print("OK")
"""
)


def _run(code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [WORKER_PY, "-c", code, str(ENGINE_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_chinese_only_fonts_get_their_true_name_and_english_ones_a_display_name():
    out = _run(_DRIVER)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("OK")


_FT_ASCII = """\
import sys
sys.path.insert(0, sys.argv[1])
import overrides
# FreeType 的 ASCII 化：Unicode 平台按 UTF-16 码元（代理对算两个 `?`），其它平台按字节
assert overrides._freetype_ascii(3, "獅尾SC".encode("utf-16-be")) == "??SC"
assert overrides._freetype_ascii(3, "\\U00020000A".encode("utf-16-be")) == "??A"
assert overrides._freetype_ascii(1, "宋".encode("gb2312")) == "??"
assert overrides._decode_sfnt_name(1, 0, b"\\xc1\\xe8\\xe7") is None
assert overrides._decode_sfnt_name(1, 25, "宋体".encode("gb2312")) == "宋体"
print("OK")
"""


def test_freetype_ascii_rule_matches_how_matplotlib_mangles_names():
    out = _run(_FT_ASCII)
    assert out.returncode == 0, out.stderr


#: 端到端：脚本注册了一个只有中文名的字体，用户在下拉里按真名选它。
SCRIPT = (
    MAKE_FONTS
    + """
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    fm.fontManager.addfont(make_font(os.path.join(HERE, "only-zh.ttf"), [
        (1, 3, 1, 0x0404, "測試宋體乙"), (2, 3, 1, 0x0404, "Regular"),
        (6, 3, 1, 0x0409, "TavottoOnlyZhB"),
    ]))
    fm.fontManager.addfont(make_font(os.path.join(HERE, "both.ttf"), [
        (1, 3, 1, 0x0409, "Tavotto Both B"), (1, 3, 1, 0x0804, "塔沃双名乙"),
        (2, 3, 1, 0x0409, "Regular"), (6, 3, 1, 0x0409, "TavottoBothB"),
    ]))
    fig, ax = plt.subplots(figsize=(3.0, 2.0))
    ax.plot([0, 1], [0, 1])
    ax.set_title("Wg Title")
    fig.savefig("NameFig.pdf")
"""
)


def test_worker_lists_true_names_and_display_names_and_the_choice_replays(tmp_path):
    (tmp_path / "fig_names.py").write_text(SCRIPT, encoding="utf-8")
    pick = [{"gid": "axes_0.title", "prop": "fontfamily", "value": "測試宋體乙"}]
    for attempt in ("hot", "replay"):  # 热会话与写回用的一次性重放各起一次
        w = pool.one_shot("fig_names.py", str(tmp_path), "main")
        try:
            w.ensure_built()
            base = w.override("NameFig", [])
            man = base["manifest"]
            assert "測試宋體乙" in man["font_families"], attempt
            assert not any("?" in f for f in man["font_families"])
            assert man["font_family_names"].get("Tavotto Both B") == "塔沃双名乙"
            resp = w.override("NameFig", pick)
            assert not resp.get("warnings"), resp["warnings"]
            title = next(e for e in resp["manifest"]["elements"] if e["gid"] == "axes_0.title")
            fam = next(f for f in title["editable"] if f["prop"] == "fontfamily")
            assert fam["value"] == "測試宋體乙"
            # 选得中不算数，要画得出：没装上的名字会被标进 options_unavailable
            assert "測試宋體乙" not in (fam.get("options_unavailable") or []), fam
            pngs = []
            for patches in ([], pick):
                png = tmp_path / f"{attempt}-{len(patches)}.png"
                w.export("NameFig", patches, str(png), "png", dpi=100)
                pngs.append(png.read_bytes())
            # 测试字体是 DejaVu Serif 的字形，默认是 DejaVu Sans：换上了就不同
            assert pngs[0] != pngs[1], attempt
        finally:
            pool.discard(w)
