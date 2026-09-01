"""图内文字的字形覆盖（Prompt 14）：**方框要变成一条问题，不是一块黑**。

改造前这条路上没有任何判据：脚本写死一个 Times New Roman、标签里有 `×10⁵`，
matplotlib 会把 `⁵` `⁻` 画成 .notdef 方框（实测三个字符画出来的暗像素数一模
一样——那就是同一个空心框），链路全通、渲染成功、界面一句话都不说。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_glyphs.py"
ENTRY = "main"
STEM = "GlyphFig"

LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1], [0, 1])
    ax.set_xlabel("Flux (A m\\u207b\\u00b2)")
    ax.set_ylabel("\\u6d53\\u5ea6 (mg/L)")
    ax.set_title("Plain ASCII title")
    fig.savefig("GlyphFig.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("glyph-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _manifest(figs, patches=()):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def test_default_family_draws_the_scientific_characters(library):
    """默认族（DejaVu 那一套）盖得住科学字符——这一条是**对照**。

    没有它的话，下面那条「换成 Times New Roman 就缺字」既可能是字体的问题，
    也可能是判据把所有非 ASCII 都报成缺字，两者分不开。
    """
    man = _manifest(library)
    assert "glyphs_missing" not in _el(man, "axes_0.xlabel")


def test_ascii_only_text_never_reports_glyph_trouble(library):
    man = _manifest(library)
    title = _el(man, "axes_0.title")
    assert "glyphs_missing" not in title
    assert "glyphs_fallback" not in title


def test_cjk_label_reports_the_characters_that_come_out_as_boxes(library):
    """中文轴标题在 DejaVu 上是方框——**逐字列出来**，不是一句「有问题」。

    问题面板要把它们摆给用户看：说得出是哪几个字，用户才知道该换字体还是
    改文案。
    """
    man = _manifest(library)
    gone = _el(man, "axes_0.ylabel").get("glyphs_missing")
    assert gone == ["浓", "度"]


def test_named_family_without_the_glyphs_reports_them(library):
    """脚本/用户选了 Times New Roman 时，`⁻` `²` 这类要么缺、要么退到别的脸。

    两种都必须被报出来，而且**分成两张单子**：一张是方框，一张是「画出来了
    但不是这张脸」。压成一句的话，用户看到红灯却发现图上好好的。
    """
    man = _manifest(
        library, [{"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "Times New Roman"}]
    )
    el = _el(man, "axes_0.xlabel")
    reported = set(el.get("glyphs_missing") or []) | set(el.get("glyphs_fallback") or [])
    if not reported:
        pytest.skip("这台机器上没装 Times New Roman（选项表里也就不会有它）")
    assert "⁻" in reported
    # 用户选的那个族仍然是 manifest 报的那个——回退尾巴不许改变「当前值」
    fam = next(f["value"] for f in el["editable"] if f["prop"] == "fontfamily")
    assert fam == "Times New Roman"


def test_fallback_tail_keeps_the_glyphs_out_of_the_missing_list(library):
    """回退尾巴的**兑现凭据**：换成 Times New Roman 之后 `⁻` 不该是方框。

    只设一个字体名时它就是方框（那正是改造前的行为）；`_family_chain()`
    带上 DejaVu Sans 之后 matplotlib 逐字形退过去。所以这里断言的是
    「它出现在 fallback 那张单子上，而不是 missing 那张」。
    """
    man = _manifest(
        library, [{"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "Times New Roman"}]
    )
    el = _el(man, "axes_0.xlabel")
    if not (el.get("glyphs_fallback") or el.get("glyphs_missing")):
        pytest.skip("这台机器上没装 Times New Roman")
    assert "⁻" not in (el.get("glyphs_missing") or [])
    assert "⁻" in (el.get("glyphs_fallback") or [])
