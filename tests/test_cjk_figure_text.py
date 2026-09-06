"""图内中文（ADR 0044）：标题 / 轴标签 / 图例 / 标注里的汉字不再是方框。

改造前的实测（macOS，matplotlib 3.10.8，用户反馈第 3 条）：`plt.title("中文标题")`
走完引擎，manifest 把四个字全列进 `glyphs_missing`，PDF 里只嵌了 DejaVuSans，
PNG 上「中」与「文」是两张**逐像素相同**的位图——那就是同一个 .notdef 空心框。
matplotlib 自己 warn 了（`Glyph 20013 … missing from font(s) DejaVu Sans.`），
但那条只落在 worker 的 stderr 日志里，界面看不到。

四种产物各有各的尺子，**互不能当代理**：
* manifest：没有 `glyphs_missing`，且 `cjk_family` 说得出是哪张脸画的；
* PDF：文本层读回**同一串字符**，且嵌了 DejaVu 之外的字体；
* PNG：「中」与「文」两次导出**不是**同一张位图（两张一样 ⇔ 都是方框——
  这把尺子的反向由 `test_without_the_tail_cjk_is_boxes_again` 证明它是活的）；
* 预检：`glyph-missing` 不响；`cjk-fallback-missing` 只在那张脸不在白名单里
  时响，而且说的是那张脸。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
机器上一张中日韩字体都没有时（Pyodide、没装 Noto CJK 的 Linux），回退链是空的，
行为退回改造前的「逐字报方框」——那一档在这里只断言诊断仍然完整，然后 skip
兑现类断言并说明原因。**skip 不是绿**：CI 上看到 skip 要去查 runner 装没装字体。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import pool, preflight, profiles

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"

#: 「这台机器的 worker 解释器装没装中日韩候选字体」——**独立的尺子**：直接问
#: matplotlib 的 findfont，不经 `cjk_fallback_tail()`。要是拿被测函数自己的结论
#: 决定 skip 不 skip，把尾巴整个拿掉也只会得到一片 skip，而 skip 看起来像绿。
_PROBE = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
import overrides
have = []
for name in overrides.cjk_fallback_candidates():
    try:
        font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
        have.append(name)
    except (ValueError, RuntimeError):
        pass
print(json.dumps(have))
"""


def _installed_cjk_candidates() -> list[str]:
    out = subprocess.run(
        [WORKER_PY, "-c", _PROBE, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def installed_cjk():
    """worker 解释器上装了的中日韩候选；空表示这台机器退回「逐字报方框」那一档。"""
    return _installed_cjk_candidates() if WORKER_PY else []


SCRIPT_NAME = "fig_cn.py"
ENTRY = "main"
STEM = "CnFig"
TITLE = "中文标题"
XLABEL = "时间 (s)"
LEGEND = ("实验组", "对照组")
NOTE = "峰值"
LIBRARY = f"""\
import matplotlib.pyplot as plt


def main():
    # constrained：让 X 轴标题整个落在页面内。PyMuPDF 的 get_text() 默认按页面
    # 矩形裁剪，默认边距下 xlabel 的下缘探出页面，CJK 脸的字框更高，整段会被
    # 裁掉——那是抽取窗口的事，不是文本层缺字。
    fig, ax = plt.subplots(figsize=(4.0, 3.0), layout="constrained")
    ax.plot([0, 1], [0, 1], label={LEGEND[0]!r})
    ax.plot([0, 1], [1, 0], label={LEGEND[1]!r})
    ax.set_title({TITLE!r})
    ax.set_xlabel({XLABEL!r})
    ax.set_ylabel("Y")
    ax.annotate({NOTE!r}, xy=(0.5, 0.5), xytext=(0.2, 0.8))
    ax.legend()
    fig.savefig("{STEM}.pdf")
"""
LATIN_LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1], [0, 1], label="μm ×10⁵")
    ax.set_title("Plain title ± ≤ 25 °C")
    ax.set_xlabel("Flux (A m⁻²)")
    ax.legend()
    fig.savefig("LatinFig.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cn-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    (figs / "fig_latin.py").write_text(LATIN_LIBRARY, encoding="utf-8")
    return figs


def _title_png_pair(w, out: Path, tag: str) -> tuple[bytes, bytes]:
    """同一张图、标题分别改成「中」与「文」的两张 PNG 的像素。"""
    pixels = []
    for ch in ("中", "文"):
        png = out / f"{tag}-{ch}.png"
        w.export(
            STEM, [{"gid": "axes_0.title", "prop": "text", "value": ch}], str(png), "png", dpi=150
        )
        with pymupdf.open(png) as doc:
            pix = doc[0].get_pixmap()
            pixels.append(bytes(pix.samples))
    return pixels[0], pixels[1]


def _render(library, out: Path, tag: str) -> dict:
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, [])
        assert not resp.get("warnings"), resp["warnings"]
        pdf = out / f"{tag}.pdf"
        w.export(STEM, [], str(pdf), "pdf")
        zhong, wen = _title_png_pair(w, out, tag)
        return {"manifest": resp["manifest"], "pdf": pdf, "png_zhong": zhong, "png_wen": wen}
    finally:
        pool.discard(w)


@pytest.fixture(scope="module")
def rendered(library, tmp_path_factory):
    """默认世界：回退链按本机探测。"""
    return _render(library, tmp_path_factory.mktemp("cn-out"), "chain")


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _text_of(e) -> str:
    return next((str(f["value"]) for f in e.get("editable", []) if f["prop"] == "text"), "")


def _cjk_texts(man):
    # 判据的主语是元素的**文字**，不是 label：label 里的「轴」「图例项」是引擎
    # 自己发的中文角色名，拿它筛会把 ylabel "Y" 也筛进来
    return [
        e
        for e in man["elements"]
        if e["role"] in ("title", "axis_label", "legend_text", "text")
        and re.search("[一-鿿]", _text_of(e))
    ]


def _require_chain(man, installed_cjk) -> None:
    """装了候选字体就**必须**接住；一张都没装时诊断必须仍然完整，然后 skip。"""
    title = _el(man, "axes_0.title")
    if installed_cjk:
        assert title.get("cjk_family") in installed_cjk, (title, installed_cjk)
        return
    assert title.get("glyphs_missing") == list(TITLE), title
    assert "cjk_family" not in title
    pytest.skip("worker 的解释器找不到任何中日韩候选字体：回退链为空，退回逐字报方框")


def test_manifest_reports_the_face_that_draws_the_chinese_not_missing_glyphs(
    rendered, installed_cjk
):
    man = rendered["manifest"]
    _require_chain(man, installed_cjk)
    texts = _cjk_texts(man)
    assert len(texts) >= 5, [e["label"] for e in texts]  # 标题 / X 轴 / 两条图例 / 标注
    for e in texts:
        assert "glyphs_missing" not in e, e
        # 汉字由回退链接手**不算**「换了脸」（与画布侧 glyphplan 同一个裁决）
        assert "glyphs_fallback" not in e, e
        assert e.get("cjk_family"), e
        # 用户 / 脚本设的族仍然是「当前值」，尾巴不改它
        fam = next(f["value"] for f in e["editable"] if f["prop"] == "fontfamily")
        assert fam == "sans-serif", fam
    # 接手的那张脸必须是下拉里钉得住的选项之一：自动回退到谁，用户就能显式选谁
    title = _el(man, "axes_0.title")
    opts = next(f["options"] for f in title["editable"] if f["prop"] == "fontfamily")
    assert title["cjk_family"] in opts, (title["cjk_family"], opts)


def test_pdf_text_layer_reads_back_the_chinese_and_embeds_a_cjk_face(rendered, installed_cjk):
    _require_chain(rendered["manifest"], installed_cjk)
    with pymupdf.open(rendered["pdf"]) as doc:
        text = " ".join(" ".join(p.get_text().split()) for p in doc)
        fonts = {f[3] for p in doc for f in p.get_fonts()}
    for s in (TITLE, XLABEL, *LEGEND, NOTE):
        assert s in text, f"PDF 文本层里找不到 {s!r}\n{text}"
    assert any("DejaVu" not in f for f in fonts), fonts


def test_png_draws_two_different_chinese_glyphs_not_the_same_box(rendered, installed_cjk):
    _require_chain(rendered["manifest"], installed_cjk)
    assert rendered["png_zhong"] != rendered["png_wen"]


def test_preflight_does_not_call_the_rendered_chinese_a_violation(rendered, installed_cjk):
    man = rendered["manifest"]
    _require_chain(man, installed_cjk)
    p = profiles.load("lab-publication-v1")
    issues = preflight.run(preflight.spec_from_manifest(man), p)
    ids = [i["id"] for i in issues]
    assert "glyph-missing" not in ids, issues
    face = _el(man, "axes_0.title")["cjk_family"]
    accepted = {s.lower() for s in p["cjk_fallback"]["accepted"]}
    cjk = [i for i in issues if i["id"] == "cjk-fallback-missing"]
    if face.lower() in accepted:
        assert cjk == [], cjk
    else:
        # 这台机器接手的脸不在默认白名单里：可以响，但主语必须是那张脸
        assert cjk and all(i["detail"]["face"] == face for i in cjk), cjk


def test_without_the_tail_cjk_is_boxes_again(library, tmp_path, monkeypatch):
    """反向对照：关掉尾巴，PNG 那把尺子必须量到「中 == 文」。

    没有这条，上面「两张 PNG 不同」既可能是字画出来了，也可能是别的什么在
    两次导出之间变了；有了它，「相同 ⇔ 方框」这把尺子才算被证明是活的。
    """
    monkeypatch.setenv("TAVOTTO_CJK_FALLBACK", "0")
    r = _render(library, tmp_path, "notail")
    title = _el(r["manifest"], "axes_0.title")
    assert title.get("glyphs_missing") == list(TITLE)
    assert "cjk_family" not in title
    assert r["png_zhong"] == r["png_wen"]


def test_latin_only_figures_render_identically_with_and_without_the_tail(
    library, tmp_path, monkeypatch
):
    """尾巴只在正文那张脸缺字形时接手：没有汉字的图**一个像素都不许变**。

    CompatBench 拿原生 matplotlib 当参照；这条是「加了链不影响既有图」的凭据。
    """
    pixels = []
    for flag in ("0", ""):
        if flag:
            monkeypatch.setenv("TAVOTTO_CJK_FALLBACK", flag)
        else:
            monkeypatch.delenv("TAVOTTO_CJK_FALLBACK", raising=False)
        w = pool.one_shot("fig_latin.py", str(library), ENTRY)
        try:
            w.ensure_built()
            png = tmp_path / f"latin-{flag or 'chain'}.png"
            w.export("LatinFig", [], str(png), "png", dpi=150)
            with pymupdf.open(png) as doc:
                pixels.append(bytes(doc[0].get_pixmap().samples))
        finally:
            pool.discard(w)
    assert pixels[0] == pixels[1]


_ENSURE_DRIVER = """\
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.text import Text
import overrides

tail = overrides.cjk_fallback_tail()
assert tail, "探测器说装了、这里却是空的"
cjk = tail[0]

# a) 脚本整个覆盖了 font.family（中文用户最常见的写法）；补 rcParams 之后
#    **之后才创建**的 Text 带着尾巴，而且用户写的名字仍在最前
matplotlib.rcParams["font.family"] = "Some Font The User Typed"
overrides.ensure_rcparams_fallback()
later = Text(0, 0, "x")
fams = list(later.get_fontfamily())
assert fams[0] == "Some Font The User Typed", fams
assert "DejaVu Sans" in fams and cjk in fams, fams
# 幂等：再补一次一个都不多
before = list(matplotlib.rcParams["font.family"])
overrides.ensure_rcparams_fallback()
assert list(matplotlib.rcParams["font.family"]) == before

# b) 图上已有的 Text：脚本给单个 Text 传了 fontfamily=，走的是逐个补那条
matplotlib.rcdefaults()
fig, ax = plt.subplots()
ax.set_title("中文", fontfamily="Times New Roman")
ax.set_xlabel("普通")
n = overrides.ensure_figure_fallback(fig)
assert n >= 2, n
t = list(ax.title.get_fontfamily())
assert t[0] == "Times New Roman" and cjk in t, t
x = list(ax.xaxis.label.get_fontfamily())
assert x[0] == "sans-serif" and cjk in x, x
# 刻度文字也在 findobj 的遍历里
tick = ax.xaxis.get_major_ticks()[0].label1
assert cjk in list(tick.get_fontfamily()), tick.get_fontfamily()
# 幂等
assert overrides.ensure_figure_fallback(fig) == 0
print("OK")
"""


def test_ensure_helpers_cover_texts_created_after_build_and_explicit_families(installed_cjk):
    """`ensure_rcparams_fallback` / `ensure_figure_fallback` 的两条路各自兑现：

    * 脚本整个覆盖 `font.family` 之后才创建的文字（懒建刻度、override 新加的
      标注）读的是补过的 rcParams；
    * 脚本给单个 Text 传 `fontfamily=` 的，只有逐个补才管用。

    两条都要求用户写的名字留在最前、补第二遍一个都不多。跑在 worker 解释器里。
    """
    if not installed_cjk:
        pytest.skip("worker 的解释器找不到任何中日韩候选字体")
    out = subprocess.run(
        [WORKER_PY, "-c", _ENSURE_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("OK")


def test_cjk_character_class_is_the_same_on_both_processes():
    """manifest（worker 侧）与 preflight（Flask 侧）各持一份「哪些字算中日韩」；
    两份必须字面相同，否则 worker 报成 `cjk_family` 的字，预检那边不认它是中文。"""
    man = (ROOT / "src/tavotto/engine/manifest.py").read_text(encoding="utf-8")
    pf = (ROOT / "src/tavotto/engine/preflight.py").read_text(encoding="utf-8")
    a = re.search(r'_CJK_CHAR = re\.compile\("(\[[^"]+\])"\)', man)
    b = re.search(r'_CJK = re\.compile\("(\[[^"]+\])"\)', pf)
    assert a and b, (a, b)
    assert a.group(1) == b.group(1)
