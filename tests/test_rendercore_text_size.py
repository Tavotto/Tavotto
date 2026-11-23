"""成品 PDF 里的**实际字号**（`inspector.observed.text_size` 与 `text_size_verdict`，ADR 0068 的观测面）。

主语是**封口文件的内容流**，不是脚本里的 `fontsize`、也不是引擎 manifest：预检按 manifest 的 `fontsize × scale` 判
（`engine/preflight._iter_font_elements`），它看不到一段文字**内部**更小的字——matplotlib 的 mathtext 把上下标排成
约 0.7 倍的另一段 `Tf`，7 pt 的 `$R^2$` 里有一个 4.9 pt 的「2」。这里量的就是那一段。

用例分四类（`docs/rules/repo/scientific-figure-review.md` 第 7 问）：正常 / 边界 / 错误 / 判不了，外加真 matplotlib
产物的防误报与反证。阈值全部由用例交进去——检查器自己不认识任何 pt 数。
"""

from __future__ import annotations

import importlib.util
import subprocess

import pytest

from tavotto.engine import pool
from tavotto.rendercore import inspector as I

HAS_PIKEPDF = importlib.util.find_spec("pikepdf") is not None
pytestmark = pytest.mark.skipif(not HAS_PIKEPDF, reason="pikepdf 未装（not_run，不是绿）")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
needs_matplotlib = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（not_run，不是绿）"
)


# ---------------------------------------------------------------------------
# 手工 PDF：一页、一个简单字体，内容流由用例给
# ---------------------------------------------------------------------------
def _pdf(
    tmp_path,
    content: bytes,
    *,
    name="a.pdf",
    forms: dict | None = None,
    type3=None,
    user_unit: float | None = None,
):
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont="/Body")
    )
    fonts = pikepdf.Dictionary(F1=f1)
    if type3 is not None:
        fonts["/T3"] = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type3,
                FontBBox=[0, 0, 1000, 1000],
                FontMatrix=type3,
                CharProcs=pikepdf.Dictionary(),
                Encoding=pikepdf.Dictionary(Type=pikepdf.Name.Encoding, Differences=[]),
                FirstChar=0,
                LastChar=0,
                Widths=[0],
            )
        )
    xobjects = pikepdf.Dictionary()
    for key, (body, matrix) in (forms or {}).items():
        form = pikepdf.Stream(pdf, body)
        form["/Type"], form["/Subtype"] = pikepdf.Name.XObject, pikepdf.Name.Form
        form["/BBox"] = [0, 0, 400, 400]
        if matrix is not None:
            form["/Matrix"] = matrix
        form["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=f1))
        xobjects[f"/{key}"] = pdf.make_indirect(form)
    page.obj["/Resources"] = pikepdf.Dictionary(Font=fonts, XObject=xobjects)
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
    if user_unit is not None:
        page.obj["/UserUnit"] = user_unit
    out = tmp_path / name
    pdf.save(str(out))
    return out


def _facts(path, **kw) -> dict:
    return I.observe_pdf(path, **kw)["text_size"]


# ---------------------------------------------------------------------------
# 正常
# ---------------------------------------------------------------------------
def test_plain_text_reports_the_tf_size_and_counts_runs(tmp_path):
    p = _pdf(
        tmp_path, b"BT /F1 8 Tf 10 10 Td (axis) Tj ET BT /F1 6 Tf 10 30 Td (tick) Tj (s) Tj ET"
    )
    f = _facts(p)
    assert f["status"] == "measured" and f["min_pt"] == 6.0 and f["runs"] == 3
    assert f["smallest"] == [[6.0, 2], [8.0, 1]]
    assert f["hidden_runs"] == 0 and f["unsized_runs"] == 0
    assert I.text_size_verdict({"text_size": f}, 6.0) == ("verified", "ok")
    assert I.text_size_verdict({"text_size": f}, 7.0) == ("failed", "below_min")


def test_text_matrix_scale_is_part_of_the_size(tmp_path):
    """Cairo / R 的写法：`1 Tf` + 把字号写进 `Tm`。只看 Tf 会量出 1 pt。"""
    p = _pdf(tmp_path, b"BT /F1 1 Tf 7 0 0 7 20 20 Tm (label) Tj ET")
    assert _facts(p)["min_pt"] == 7.0


def test_a_scaled_panel_shrinks_its_text_through_cm_and_form_matrix(tmp_path):
    """面板缩到 60% 摆上版面：字在 Form 里是 10 pt，读者量到的是 10 × 0.6 × Form 自带的 0.5 = 3 pt。"""
    p = _pdf(
        tmp_path,
        b"q 0.6 0 0 0.6 10 10 cm /P0 Do Q BT /F1 9 Tf 5 5 Td (canvas) Tj ET",
        forms={"P0": (b"BT /F1 10 Tf 5 5 Td (inside) Tj ET", [0.5, 0, 0, 0.5, 0, 0])},
    )
    f = _facts(p)
    assert f["smallest"] == [[3.0, 1], [9.0, 1]]


def test_user_unit_scales_the_size_into_physical_points(tmp_path):
    """`/UserUnit 2`：默认空间一个单位是 2 pt，`4 Tf` 的字读者看到的是 8 pt；页面尺寸照旧只乘一次。"""
    p = _pdf(tmp_path, b"BT /F1 4 Tf (a) Tj ET", user_unit=2.0)
    assert _facts(p)["min_pt"] == 8.0
    assert I.observe_pdf(p)["size_pt"] == [400.0, 400.0]  # 200 × 2，不是 200 × 2 × 2
    # 缺省按 1 计
    assert _facts(_pdf(tmp_path, b"BT /F1 4 Tf (a) Tj ET", name="u1.pdf"))["min_pt"] == 4.0


def test_a_form_inherits_the_callers_render_mode(tmp_path):
    """Form XObject 继承调用处的文字状态（PDF 32000 §8.10.1）：页面上 `3 Tr` 之后画的 Form，里面那段 2 pt 的字
    同样不画——算进 hidden_runs、结论 unknown，不是 failed。"""
    p = _pdf(
        tmp_path,
        b"BT 3 Tr ET /P0 Do",
        forms={"P0": (b"BT /F1 2 Tf (invisible) Tj ET", None)},
    )
    f = _facts(p)
    assert f["hidden_runs"] == 1 and f["min_pt"] is None and f["runs"] == 0
    assert I.text_size_verdict({"text_size": f}, 5.0) == ("unknown", "unmeasured_runs")


def test_a_form_without_its_own_tf_inherits_font_and_size(tmp_path):
    """Form 里没有自己的 `Tf`：用调用处的字体与字号（9 pt × Form 的 0.5 = 4.5 pt），不是「量不了」；
    Form 画完以后调用处的状态不被 Form 里的改动带走（Form 里 `3 Tr` 之后，页面上的下一段仍看得见）。"""
    p = _pdf(
        tmp_path,
        b"BT /F1 9 Tf ET /P0 Do BT (after) Tj ET",
        forms={"P0": (b"BT (inside) Tj 3 Tr (gone) Tj ET", [0.5, 0, 0, 0.5, 0, 0])},
    )
    f = _facts(p)
    assert f["unsized_runs"] == 0, f
    assert f["smallest"] == [[4.5, 1], [9.0, 1]] and f["hidden_runs"] == 1


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_rotated_text_keeps_its_size(tmp_path):
    """纵轴标题转了 90°：矩阵是纯旋转，字号不变（按行列式或只看 d 都会在这里出错的写法要被这条拦住）。"""
    p = _pdf(tmp_path, b"BT /F1 7 Tf 0 1 -1 0 30 30 Tm (ylabel) Tj ET")
    assert _facts(p)["min_pt"] == 7.0


def test_font_size_is_graphics_state_and_comes_back_on_restore(tmp_path):
    """`Tf` 属于图形状态：q…Q 里换的小字号在 Q 之后不该留着。"""
    p = _pdf(tmp_path, b"BT /F1 10 Tf q /F1 4 Tf (small) Tj Q (big) Tj ET")
    assert _facts(p)["smallest"] == [[4.0, 1], [10.0, 1]]


def test_a_new_text_object_resets_the_text_matrix(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 2 Tf 5 0 0 5 0 0 Tm (ten) Tj ET BT (two) Tj ET")
    assert _facts(p)["smallest"] == [[2.0, 1], [10.0, 1]]


def test_the_floor_is_inclusive_and_the_tolerance_is_the_callers(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 5 Tf (a) Tj ET")
    o = {"text_size": _facts(p)}
    assert I.text_size_verdict(o, 5.0)[0] == "verified"
    assert I.text_size_verdict(o, 5.004)[0] == "failed"
    assert I.text_size_verdict(o, 5.004, tol_pt=0.005)[0] == "verified"


def test_only_the_smallest_buckets_are_kept(tmp_path):
    body = b"".join(b"BT /F1 %d Tf (x) Tj ET " % n for n in range(20, 4, -1))
    f = _facts(_pdf(tmp_path, body))
    assert f["runs"] == 16 and len(f["smallest"]) == I.TEXT_SIZE_BUCKETS
    assert [s for s, _n in f["smallest"]] == [float(n) for n in range(5, 5 + I.TEXT_SIZE_BUCKETS)]


# ---------------------------------------------------------------------------
# 错误：找到一段太小的字就是 failed，别的段量没量到都不改变这一点
# ---------------------------------------------------------------------------
def test_one_small_run_fails_even_when_other_runs_are_unmeasurable(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 4 Tf (tiny) Tj 3 Tr /F1 9 Tf (layer) Tj ET")
    f = _facts(p)
    assert f["min_pt"] == 4.0 and f["hidden_runs"] == 1
    assert I.text_size_verdict({"text_size": f}, 5.0) == ("failed", "below_min")


# ---------------------------------------------------------------------------
# 判不了：unknown，不是通过
# ---------------------------------------------------------------------------
def test_text_that_is_not_painted_is_counted_but_not_measured(tmp_path):
    """渲染模式 3：不画、只进文本层（Tavotto 自己的公式就是这样写的，ADR 0152）。读者看到的是轮廓，这一段的 Tf 不算数。"""
    p = _pdf(tmp_path, b"BT /F1 9 Tf (seen) Tj 3 Tr /F1 2 Tf (layer) Tj 0 Tr ET")
    f = _facts(p)
    assert f["min_pt"] == 9.0 and f["runs"] == 1 and f["hidden_runs"] == 1
    # 量到的那一段够大，但还有一段没量：不许说 verified
    assert I.text_size_verdict({"text_size": f}, 5.0) == ("unknown", "unmeasured_runs")


def test_type3_with_a_nonstandard_font_matrix_is_unsized(tmp_path):
    body = b"BT /T3 10 Tf (\000) Tj ET"
    odd = _facts(_pdf(tmp_path, body, name="odd.pdf", type3=[0.01, 0, 0, 0.01, 0, 0]))
    assert odd["status"] == "none" and odd["unsized_runs"] == 1 and odd["min_pt"] is None
    assert I.text_size_verdict({"text_size": odd}, 5.0) == ("unknown", "unmeasured_runs")
    std = _facts(_pdf(tmp_path, body, name="std.pdf", type3=[0.001, 0, 0, 0.001, 0, 0]))
    assert std["min_pt"] == 10.0 and std["unsized_runs"] == 0


def test_degenerate_matrix_and_missing_font_are_unsized(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 9 Tf 0 0 0 0 5 5 Tm (flat) Tj ET BT /Nope 9 Tf (ghost) Tj ET")
    f = _facts(p)
    assert f["status"] == "none" and f["unsized_runs"] == 2


def test_exhausted_budget_is_unknown_not_a_partial_minimum(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 9 Tf (a) Tj (b) Tj (c) Tj ET")
    f = _facts(p, budget=I.Budget(max_ops=3))
    assert f == {"status": "unknown", "reason": "budget"}
    assert I.text_size_verdict({"text_size": f}, 5.0) == ("unknown", "budget")


def test_no_reader_no_threshold_and_no_text_are_each_their_own_answer(tmp_path):
    assert I.text_size_verdict({}, 5.0) == ("unknown", "not_observed")
    probe_only = I.observe_pdf_basic(
        _pdf(tmp_path, b"BT /F1 9 Tf (a) Tj ET"), lambda _p, _k: {"w_pt": 200, "h_pt": 200}
    )
    assert I.text_size_verdict(probe_only, 5.0) == ("unknown", "no_reader")
    seen = {"text_size": _facts(_pdf(tmp_path, b"BT /F1 9 Tf (a) Tj ET", name="b.pdf"))}
    for no_floor in (None, 0, 0.0, float("nan"), "x"):
        assert I.text_size_verdict(seen, no_floor) == ("unknown", "no_threshold"), no_floor
    empty = {"text_size": _facts(_pdf(tmp_path, b"0 0 10 10 re f", name="c.pdf"))}
    assert empty["text_size"]["status"] == "none"
    assert I.text_size_verdict(empty, 5.0) == ("not_applicable", "no_text")


def _strict_ok_pdf(tmp_path, content: bytes, *, name="ok.pdf"):
    """strict 的必需项（完整性 / 尺寸 / 载体 / 字体嵌入 / 文字层 / 位图）都能 verified / n/a 的手工 PDF：
    一个嵌入的 TrueType + ToUnicode，期望文字行给齐——拿它看 `text_size` 自己的判定会不会拦导出。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"1 begincodespacerange <00> <FF> endcodespacerange\n"
        b"2 beginbfchar <61> <0061> <62> <0062> endbfchar\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.TrueType,
            BaseFont="/ABCDEF+Body",
            FontDescriptor=pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.FontDescriptor,
                    FontName="/ABCDEF+Body",
                    FontFile2=pdf.make_indirect(pikepdf.Stream(pdf, b"\0" * 16)),
                )
            ),
            ToUnicode=pdf.make_indirect(pikepdf.Stream(pdf, cmap)),
        )
    )
    page.obj["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.obj["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
    out = tmp_path / name
    pdf.save(str(out))
    return out


_OK_PLAN = {"page_pt": [200, 200], "vector": True, "text": ["a", "b"], "object_boxes": []}


@pytest.mark.parametrize("policy", ["standard", "strict"])
def test_text_size_is_a_check_but_never_a_required_one(tmp_path, policy):
    """ADR 0201 §3：`text_size` 进 `checks`，failed 写进说明，两档政策都不拦导出（不在 `required` 里）。"""
    p = _strict_ok_pdf(tmp_path, b"BT /F1 9 Tf 10 10 Td (a) Tj /F1 2 Tf (b) Tj ET")
    m = I.inspect(
        p,
        "pdf",
        plan=_OK_PLAN,
        policy=policy,
        profile={"profile_id": "x", "min_effective_font_size_pt": 8.0},
        text_size_tol_pt=0.005,
    )
    assert set(m["checks"]) == set(I.CHECKS_PDF) and "text_size" in I.CHECKS_PDF
    assert m["checks"]["text_size"] == "failed"
    assert "text_size" not in m["policy"]["required"]
    assert m["policy"]["verdict"] == "accepted", m["checks"]
    assert m["policy"]["optional_failed"] == ["text_size"]
    assert any("2.0 pt" in n and "8 pt" in n for n in m["notes"])
    s = I.summary(m)
    assert s["checks"]["text_size"] == "failed"
    assert s["text_size"] == {"reason": "below_min", "min_pt": 2.0, "floor_pt": 8.0}
    pub = I.public_projection(m)
    assert pub["checks"]["text_size"] == "failed" and "text_size" not in pub


def test_required_sets_never_list_text_size():
    for policy in I.POLICIES:
        assert "text_size" not in I.REQUIRED[policy]["pdf"]
    assert "text_size" not in I.CHECKS_SVG  # ADR 0201 §5：SVG 不做


def test_the_four_verdicts_through_inspect(tmp_path):
    """四值各走一遍真 `inspect()`：阈值只从 `profile` 来，没有下限就是 unknown。"""
    floor = {"min_effective_font_size_pt": 6.0}

    def verdict(content, profile, name):
        m = I.inspect(
            _strict_ok_pdf(tmp_path, content, name=name),
            "pdf",
            plan=_OK_PLAN,
            profile=profile,
            text_size_tol_pt=0.005,
        )
        return m["checks"]["text_size"], I.summary(m)["text_size"]

    assert verdict(b"BT /F1 7 Tf (a) Tj ET", floor, "v.pdf") == (
        "verified",
        {"reason": "ok", "min_pt": 7.0, "floor_pt": 6.0},
    )
    assert verdict(b"BT /F1 7 Tf (a) Tj /F1 4.9 Tf (b) Tj ET", floor, "f.pdf")[0] == "failed"
    # 下限是 0（中性底 journal-base-v1）/ 没给规范（standard）：unknown，不是 verified
    for i, prof in enumerate(({"min_effective_font_size_pt": 0.0}, None, {})):
        got = verdict(b"BT /F1 7 Tf (a) Tj ET", prof, f"n{i}.pdf")
        assert got == ("unknown", {"reason": "no_threshold", "min_pt": 7.0, "floor_pt": None})
    assert verdict(b"0 0 10 10 re f", floor, "e.pdf") == (
        "not_applicable",
        {"reason": "no_text", "min_pt": None, "floor_pt": None},
    )
    # 容差是调用方的：5.996 对 6 pt 在 0.005 的容差里
    assert verdict(b"BT /F1 5.996 Tf (a) Tj ET", floor, "t.pdf")[0] == "verified"


def test_tavotto_formula_outlines_with_a_hidden_text_layer_are_unknown(tmp_path):
    """Tavotto 自己的公式（ADR 0152）= 轮廓路径 + 渲染模式 3 的文本层：可见的字都够大，也不许说 verified。"""
    p = _strict_ok_pdf(
        tmp_path, b"BT /F1 9 Tf (a) Tj ET 0 0 m 5 5 l f BT 3 Tr /F1 9 Tf (b) Tj 0 Tr ET"
    )
    m = I.inspect(p, "pdf", plan=_OK_PLAN, profile={"min_effective_font_size_pt": 6.0})
    assert m["checks"]["text_size"] == "unknown"
    assert I.summary(m)["text_size"]["reason"] == "unmeasured_runs"


def test_without_a_reader_or_with_a_broken_file_text_size_is_unknown(tmp_path):
    p = _pdf(tmp_path, b"BT /F1 9 Tf (a) Tj ET")
    m = I.inspect(
        p,
        "pdf",
        plan={"page_pt": [200, 200]},
        profile={"min_effective_font_size_pt": 6.0},
        probe=lambda _p, _k: {"w_pt": 200, "h_pt": 200},
        budget=I.Budget(max_ops=1),
    )
    assert m["checks"]["text_size"] == "unknown"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7 garbage")
    m = I.inspect(bad, "pdf", plan={}, profile={"min_effective_font_size_pt": 6.0})
    assert m["checks"]["text_size"] == "unknown"
    u = I.uninspected(bad, "pdf", plan={}, reason="boom")
    assert u["checks"]["text_size"] == "unknown"


# ---------------------------------------------------------------------------
# 真 matplotlib 产物：防误报 + 反证
# ---------------------------------------------------------------------------
_MPL = """
import sys
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = int(sys.argv[2])
import matplotlib.pyplot as plt
fig, (a, b) = plt.subplots(1, 2, figsize=(4, 2), gridspec_kw={"width_ratios": [2, 1]})
a.plot([1, 2, 3], [1, 4, 9])
a.set_xlabel(sys.argv[3], fontsize=7)
a.tick_params(labelsize=7)
b.set_yscale("log"); b.plot([1, 2], [1, 1e4]); b.tick_params(labelsize=7)
b.set_xlabel("dose", fontsize=7)
fig.savefig(sys.argv[1])
"""


def _mpl_pdf(tmp_path, fonttype: int, xlabel: str):
    out = tmp_path / f"mpl-{fonttype}-{abs(hash(xlabel))}.pdf"
    subprocess.run(
        [WORKER_PY, "-I", "-c", _MPL, str(out), str(fonttype), xlabel],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return out


@needs_matplotlib
@pytest.mark.parametrize("fonttype", [3, 42])
def test_mathtext_scripts_are_smaller_than_the_fontsize_the_script_asked_for(tmp_path, fonttype):
    """7 pt 的标签，上下标是 4.9 pt；对数轴的 `10^4` 同理。manifest 的 `fontsize` 都是 7。"""
    f = _facts(_mpl_pdf(tmp_path, fonttype, r"$R^2$ of $x_{i}$"))
    assert f["status"] == "measured" and f["unsized_runs"] == 0 and f["hidden_runs"] == 0
    assert f["min_pt"] == pytest.approx(4.9, abs=0.011)
    assert any(abs(s - 7.0) < 0.011 for s, _n in f["smallest"])
    o = {"text_size": f}
    assert I.text_size_verdict(o, 5.0) == ("failed", "below_min")  # 某些期刊的 5 pt 下限
    assert (
        I.text_size_verdict(o, 4.5)[0] == "verified"
    )  # 换一份下限更低的规范就过：阈值不在检查器里


@needs_matplotlib
def test_a_figure_without_scripts_is_not_flagged(tmp_path):
    """防误报：同一张图不带上下标、对数轴换成线性——最小的字就是脚本要的 7 pt。"""
    script = _MPL.replace('b.set_yscale("log"); ', "")
    out = tmp_path / "plain.pdf"
    subprocess.run(
        [WORKER_PY, "-I", "-c", script, str(out), "42", "time (s)"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    f = _facts(out)
    assert f["min_pt"] == pytest.approx(7.0, abs=0.011)
    assert I.text_size_verdict({"text_size": f}, 5.0) == ("verified", "ok")
