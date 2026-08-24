"""坐标轴与刻度：范围 / 缩放类型 / spine / 刻度定位模型 / 单条刻度文字。

两条纪律在这里被钉住：

1. **界面上出现的每一个 scale 选项，`set_[xy]scale` 都必须真吃得下**。
   manifest 的 options 是从 `matplotlib.scale.get_scale_names()` 现取的，
   所以这里就照着 options 挨个跑一遍——列一个会失败的假选项，用户点了
   只会得到一次渲染失败。
2. **刻度定位走 Locator / Formatter，不是改已经生成出来的 Text**。
   改 xlim、换 scale 之后 matplotlib 会把刻度整组重来；只改 Text 的实现
   会在下一帧被静默打回原样，这里用「改完 xlim 之后刻度仍然是配置的样子」
   来看住它。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""
import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

SCRIPT_NAME = "fig_axes.py"
ENTRY = "main"
STEM = "AxesFig"

LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.05, 0.95, 40)
    ax.plot(x, x * 8.0 + 0.5, label="ramp")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.legend()
    fig.savefig("AxesFig.pdf")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("axes-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _render(figs, patches=()):
    w = _worker(figs)
    try:
        resp = w.override(STEM, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _field(man, gid, prop):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == prop)


def _options(man, gid, prop):
    return next(f.get("options") for f in _el(man, gid)["editable"] if f["prop"] == prop)


def _tick_texts(man, which="x"):
    return [e["label"] for e in man["elements"]
            if e["role"] == "ticklabel" and f".{which}ticklabels_" in e["gid"]]


def _pdf_text(figs, patches, out) -> str:
    """把这组 patch 导成 PDF 再读它的文字层。

    次刻度的数字不是独立元素（单条编辑只对主刻度开放），manifest 里看不到它。
    要证明「真的标出来了」而不是「字段能回显」，只能看真产物——导出的 PDF
    里文字仍是矢量，`get_text()` 拿到的就是画在纸上的那些字。
    """
    import pymupdf
    w = _worker(figs)
    try:
        w.export(STEM, list(patches), str(out), "pdf", 200)
    finally:
        pool.discard(w)
    with pymupdf.open(out) as doc:
        return doc[0].get_text()


# ---------------------------------------------------------------------------
# 范围、缩放类型、spine
# ---------------------------------------------------------------------------
def test_xlim_and_ylim_roundtrip(library):
    man = _render(library, [{"gid": "axes_0", "prop": "xlim", "value": [0.1, 0.9]},
                            {"gid": "axes_0", "prop": "ylim", "value": [1.0, 7.0]}])
    assert _field(man, "axes_0", "xlim") == pytest.approx([0.1, 0.9])
    assert _field(man, "axes_0", "ylim") == pytest.approx([1.0, 7.0])


def test_scale_options_are_exactly_what_the_handler_accepts(library):
    """options 里的每一项都真跑一遍。这条就是「不许显示一个会失败的选项」。"""
    base = _render(library)
    opts = _options(base, "axes_0", "yscale")
    assert "linear" in opts and "log" in opts
    for name in opts:
        man = _render(library, [{"gid": "axes_0", "prop": "yscale", "value": name}])
        assert _field(man, "axes_0", "yscale") == name, name
        # 换完 scale 图还画得出来（manifest 有元素 = 那一次 draw 没炸）
        assert len(man["elements"]) > 3


@pytest.mark.parametrize("name", ["log", "symlog", "logit"])
def test_nonlinear_scales_round_trip_and_restore(library, name):
    """换成非线性刻度再撤销，回到原样（scale 会把 locator/formatter 整套换掉，
    「自动」必须跟着重新采集脚本原样，否则撤销回来的是线性轴的 AutoLocator）。"""
    base = _render(library)
    if name not in _options(base, "axes_0", "xscale"):
        pytest.skip(f"当前 matplotlib 没有注册 {name} 刻度")
    w = _worker(library)
    try:
        man = w.override(STEM, [{"gid": "axes_0", "prop": "xscale", "value": name}])
        assert not man["warnings"], man["warnings"]
        assert _field(man["manifest"], "axes_0", "xscale") == name
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
        assert _field(back["manifest"], "axes_0", "xscale") == "linear"
        assert _tick_texts(back["manifest"]) == _tick_texts(base)
    finally:
        pool.discard(w)


#: 次刻度落在 0.05 的奇数倍上（主刻度是 0.2 的倍数），所以 "0.15" 这个字
#: 只可能来自次刻度——拿它当「真的标出来了」的判据不会与主刻度混淆。
_MINOR_ONLY = [
    {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
    {"gid": "axes_0.xticks", "prop": "minor_visible", "value": True},
    {"gid": "axes_0.xticks", "prop": "minor_mode", "value": "step"},
    {"gid": "axes_0.xticks", "prop": "minor_step", "value": 0.05},
]


def test_minor_format_really_labels_the_minor_ticks(library, tmp_path):
    """次刻度默认**不标数字**；开了格式之后纸上真的多出那一排数字。

    判据取自导出 PDF 的文字层——字段能回显不等于图上有字，那正是「假支持」。
    """
    base = _render(library)
    assert _field(base, "axes_0.xticks", "minor_format") == "none"
    assert "0.15" not in _pdf_text(library, _MINOR_ONLY, tmp_path / "off.pdf")

    on = [*_MINOR_ONLY,
          {"gid": "axes_0.xticks", "prop": "minor_format", "value": "%.2f"}]
    text = _pdf_text(library, on, tmp_path / "on.pdf")
    assert "0.15" in text, text[:300]
    man = _render(library, on)
    assert _field(man, "axes_0.xticks", "minor_format") == "%.2f"
    # 主刻度是另一条：它的格式没被动过
    assert _field(man, "axes_0.xticks", "format") == "auto"


def test_minor_format_undo_goes_back_to_unlabelled(library, tmp_path):
    w = _worker(library)
    try:
        w.override(STEM, [*_MINOR_ONLY,
                          {"gid": "axes_0.xticks", "prop": "minor_format", "value": "%.2f"}])
        back = w.override(STEM, _MINOR_ONLY)
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    assert _field(back["manifest"], "axes_0.xticks", "minor_format") == "none"
    assert "0.15" not in _pdf_text(library, _MINOR_ONLY, tmp_path / "undo.pdf")


def test_minor_labels_count_into_the_tick_group_box(library):
    """次刻度标出数字之后，「X 刻度文字」这一组的包围盒必须把它们圈进去
    ——不然那一排点不中，对齐也对不准。"""
    off = _render(library, _MINOR_ONLY)
    on = _render(library, [*_MINOR_ONLY,
                           {"gid": "axes_0.xticks", "prop": "minor_format", "value": "%.2f"}])
    assert _el(on, "axes_0.xticks")["bbox"][3] > _el(off, "axes_0.xticks")["bbox"][3]


# ---------------------------------------------------------------------------
# 边框：一档「全部」+ 四条各自可覆盖
# ---------------------------------------------------------------------------
_SIDES = ("top", "right", "bottom", "left")


def test_each_spine_can_be_styled_on_its_own(library):
    """「只把左边和下边加粗」是论文图的常见做法——四条边必须能各改各的。"""
    man = _render(library, [
        {"gid": "axes_0", "prop": "spine_left_linewidth", "value": 1.8},
        {"gid": "axes_0", "prop": "spine_bottom_linewidth", "value": 1.8},
        {"gid": "axes_0", "prop": "spine_top_color", "value": "#B34700"},
    ])
    assert _field(man, "axes_0", "spine_left_linewidth") == pytest.approx(1.8)
    assert _field(man, "axes_0", "spine_bottom_linewidth") == pytest.approx(1.8)
    assert _field(man, "axes_0", "spine_top_color").lower() == "#b34700"
    # 没点名的那些原样不动
    assert _field(man, "axes_0", "spine_right_linewidth") == pytest.approx(0.8, abs=0.01)
    assert _field(man, "axes_0", "spine_left_color") == _field(
        _render(library), "axes_0", "spine_left_color")


def test_per_spine_wins_over_all_regardless_of_order(library):
    """「全部灰色」+「上边红色」：谁先谁后都必须是同一张图。

    两条会互相盖写的 setter 是最容易在热会话与全量重放之间分岔的形状——
    模型化（写 cfg 再整体重建）就是为了让顺序不再有意义。
    """
    a = [{"gid": "axes_0", "prop": "spine_color", "value": "#888888"},
         {"gid": "axes_0", "prop": "spine_top_color", "value": "#B34700"}]
    man_a = _render(library, a)
    man_b = _render(library, list(reversed(a)))
    for prop in ("spine_top_color", "spine_left_color", "spine_color"):
        assert _field(man_a, "axes_0", prop) == _field(man_b, "axes_0", prop), prop
    assert _field(man_a, "axes_0", "spine_top_color").lower() == "#b34700"
    assert _field(man_a, "axes_0", "spine_left_color").lower() == "#888888"


def test_undo_one_spine_falls_back_to_the_all_setting(library):
    """撤销「上边红色」= 退回未表态，于是落到「全部」那一档，不是钉死成红色。"""
    w = _worker(library)
    try:
        w.override(STEM, [{"gid": "axes_0", "prop": "spine_color", "value": "#888888"},
                          {"gid": "axes_0", "prop": "spine_top_color", "value": "#B34700"}])
        back = w.override(STEM, [{"gid": "axes_0", "prop": "spine_color", "value": "#888888"}])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    assert _field(back["manifest"], "axes_0", "spine_top_color").lower() == "#888888"


def test_undo_all_spine_settings_returns_to_the_script(library):
    base = _render(library)
    w = _worker(library)
    try:
        w.override(STEM, [{"gid": "axes_0", "prop": "spine_color", "value": "#888888"},
                          {"gid": "axes_0", "prop": "spine_left_linewidth", "value": 2.5}])
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    for side in _SIDES:
        for prop in (f"spine_{side}_color", f"spine_{side}_linewidth"):
            assert _field(back["manifest"], "axes_0", prop) == _field(base, "axes_0", prop), prop


def test_invert_and_spines(library):
    man = _render(library, [
        {"gid": "axes_0", "prop": "invert_y", "value": True},
        {"gid": "axes_0", "prop": "spine_top", "value": False},
        {"gid": "axes_0", "prop": "spine_right", "value": False},
        {"gid": "axes_0", "prop": "spine_color", "value": "#B34700"},
        {"gid": "axes_0", "prop": "spine_linewidth", "value": 1.6},
    ])
    assert _field(man, "axes_0", "invert_y") is True
    assert _field(man, "axes_0", "spine_top") is False
    assert _field(man, "axes_0", "spine_right") is False
    assert _field(man, "axes_0", "spine_color").lower() == "#b34700"
    assert _field(man, "axes_0", "spine_linewidth") == pytest.approx(1.6)


# ---------------------------------------------------------------------------
# 刻度线四边开关（issue #92）
# ---------------------------------------------------------------------------
def test_tick_sides_default_and_toggle(library):
    """入口在子图元素上：上/右两边没有刻度数字、画布上点不到，只能从这儿开。"""
    man = _render(library)
    assert _field(man, "axes_0", "ticks_bottom") is True
    assert _field(man, "axes_0", "ticks_top") is False
    assert _field(man, "axes_0", "ticks_left") is True
    assert _field(man, "axes_0", "ticks_right") is False

    man = _render(library, [
        {"gid": "axes_0", "prop": "ticks_top", "value": True},
        {"gid": "axes_0", "prop": "ticks_right", "value": True},
        {"gid": "axes_0", "prop": "ticks_bottom", "value": False},
    ])
    assert _field(man, "axes_0", "ticks_top") is True
    assert _field(man, "axes_0", "ticks_right") is True
    assert _field(man, "axes_0", "ticks_bottom") is False
    assert _field(man, "axes_0", "ticks_left") is True   # 没动的不受牵连


def test_tick_sides_really_change_pixels_and_restore(library):
    """能力真实用像素说话：开上边刻度线必须真的画出来，撤销必须逐字节回去。
    axes 角色被不变式套件的自动扫描跳过（各有专用用例），这条就是那份专用用例。"""
    w = _worker(library)
    try:
        base = w.preview_png(STEM, [], 380, "ticks-base").read_bytes()
        on = w.preview_png(STEM, [{"gid": "axes_0", "prop": "ticks_top", "value": True}],
                           380, "ticks-on").read_bytes()
        back = w.preview_png(STEM, [], 380, "ticks-back").read_bytes()
    finally:
        pool.discard(w)
    assert on != base       # 画面真的变了
    assert back == base     # preview 状态中立 + 逐字还原


def test_tick_sides_undo_returns_to_the_script(library):
    base = _render(library)
    w = _worker(library)
    try:
        w.override(STEM, [{"gid": "axes_0", "prop": "ticks_top", "value": True},
                          {"gid": "axes_0", "prop": "ticks_bottom", "value": False}])
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    for prop in ("ticks_bottom", "ticks_top", "ticks_left", "ticks_right"):
        assert _field(back["manifest"], "axes_0", prop) == _field(base, "axes_0", prop), prop


def test_tick_sides_survive_a_scale_change(library):
    """开关与换 scale 组合仍成立（两种列表序）。注意：这条只看护端到端
    组合——规范化应用顺序里 scale 永远先于其余档，所以它**判别不了**
    「写在轴上还是逐个改现有 Tick」；那条机制主张由下面的 reset_ticks
    用例看护（手工变异确认过：天真实现在这条上是绿的、在那条上是红的）。"""
    for patches in (
        [{"gid": "axes_0", "prop": "ticks_top", "value": True},
         {"gid": "axes_0", "prop": "xscale", "value": "log"}],
        [{"gid": "axes_0", "prop": "xscale", "value": "log"},
         {"gid": "axes_0", "prop": "ticks_top", "value": True}],
    ):
        man = _render(library, patches)
        assert _field(man, "axes_0", "ticks_top") is True
        assert _field(man, "axes_0", "xscale") == "log"


_TICK_SIDE_MECHANISM_DRIVER = '''
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import overrides

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.canvas.draw()
get, set_ = overrides.HANDLERS[("axes", "ticks_top")]
set_(ax, True)
ax.xaxis.reset_ticks()          # 换 scale / set_ticks 冻结共同的底层一步
fig.canvas.draw()
assert all(t.tick2line.get_visible() for t in ax.xaxis.get_major_ticks()), (
    "reset_ticks 之后顶边刻度线丢了：开关写在了现有 Tick 上，不在轴上")
assert get(ax) is True
print("OK")
'''


def test_tick_side_setter_marks_the_axis_not_the_current_ticks():
    """机制级看护：开关必须经 tick_params 写进轴的 `_major_tick_kw`，让之后
    **新建**的刻度也继承。逐个改现有 Tick 的实现，在 matplotlib 重建刻度
    （`reset_ticks`——换 scale、`set_ticks` 冻结都会走到）之后就全丢了，
    而上面那条端到端用例恰好被规范化顺序掩护、抓不到它。"""
    import subprocess
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"
    out = subprocess.run([WORKER_PY, "-c", _TICK_SIDE_MECHANISM_DRIVER,
                          str(engine_dir)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "OK"


# ---------------------------------------------------------------------------
# 刻度定位模型
# ---------------------------------------------------------------------------
def test_major_step_produces_evenly_spaced_ticks(library):
    man = _render(library, [
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "major_step", "value": 0.25},
    ])
    assert _field(man, "axes_0.xticks", "major_mode") == "step"
    vals = _field(man, "axes_0.xticks", "major_values")
    steps = {round(b - a, 6) for a, b in zip(vals, vals[1:])}
    assert steps == {0.25}, vals


def test_fixed_values_place_exactly_those_ticks(library):
    man = _render(library, [
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0.xticks", "prop": "major_values", "value": [0.0, 0.33, 0.9]},
    ])
    assert _field(man, "axes_0.xticks", "major_mode") == "fixed"
    assert _field(man, "axes_0.xticks", "major_values") == pytest.approx([0.0, 0.33, 0.9])
    assert len(_tick_texts(man)) == 3


def test_clearing_fixed_values_replays_the_same_as_a_fresh_worker(library):
    """「固定刻度值」清空之后，热会话与全量重放必须落到同一组刻度。

    界面上这一档写着「留空 = 用当前刻度」，而「当前」曾经取的是
    `axis.get_majorticklocs()`——热会话里那是上一次 `FixedLocator` 留下的
    痕迹，全新 worker 重放同一份 patch 列表时却是脚本自己的刻度。于是
    「所见 == 全量重放 == 写回 == 重开」这条不变式在这一档上破了：用户
    重开工程会发现刻度自己变了，applied 表里却一个字节没动。
    """
    final = [
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0.xticks", "prop": "major_values", "value": []},
    ]
    hot = _worker(library)
    try:
        first = hot.override(STEM, final[:2] + [
            {"gid": "axes_0.xticks", "prop": "major_values", "value": [0.2, 0.5, 0.8]}])
        assert _field(first["manifest"], "axes_0.xticks", "major_values") \
            == pytest.approx([0.2, 0.5, 0.8])
        hot_man = hot.override(STEM, final)["manifest"]      # 清空（仍是全量列表）
    finally:
        pool.discard(hot)

    replay_man = _render(library, final)                     # 全新 worker 一次性重放
    assert _field(hot_man, "axes_0.xticks", "major_values") == pytest.approx(
        _field(replay_man, "axes_0.xticks", "major_values"))
    assert _tick_texts(hot_man) == _tick_texts(replay_man)


def test_format_changes_the_major_formatter(library):
    man = _render(library, [{"gid": "axes_0.xticks", "prop": "format", "value": "%.3f"}])
    assert _field(man, "axes_0.xticks", "format") == "%.3f"
    assert all(lbl.count(".") == 1 and lbl.rstrip("”").split(".")[-1].rstrip("”")
               for lbl in _tick_texts(man))


def test_minor_ticks_can_be_turned_on_and_stepped(library):
    on = _render(library, [{"gid": "axes_0.xticks", "prop": "minor_visible", "value": True}])
    assert _field(on, "axes_0.xticks", "minor_visible") is True
    stepped = _render(library, [
        {"gid": "axes_0.xticks", "prop": "minor_visible", "value": True},
        {"gid": "axes_0.xticks", "prop": "minor_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "minor_step", "value": 0.05},
    ])
    assert _field(stepped, "axes_0.xticks", "minor_mode") == "step"
    assert _field(stepped, "axes_0.xticks", "minor_step") == pytest.approx(0.05)


def test_tick_model_survives_a_later_xlim_change(library):
    """先定间隔再改范围：刻度必须仍按那个间隔重算。

    只改「已经生成出来的 Text」的实现在这一步会被静默打回原样——用户看到的是
    「设的间隔自己没了」。走 Locator 才留得住。
    """
    man = _render(library, [
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "major_step", "value": 0.2},
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 2.0]},
    ])
    vals = _field(man, "axes_0.xticks", "major_values")
    assert {round(b - a, 6) for a, b in zip(vals, vals[1:])} == {0.2}
    assert max(vals) > 1.0, "范围扩大之后应当多出刻度"


def test_tick_model_undo_returns_to_the_script_original(library):
    """撤销一条刻度模型 prop = **退回未表态**，不是把当前推断值钉死成配置。"""
    base = _render(library)
    w = _worker(library)
    try:
        w.override(STEM, [{"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
                          {"gid": "axes_0.xticks", "prop": "major_step", "value": 0.07}])
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
        assert _field(back["manifest"], "axes_0.xticks", "major_mode") == "auto"
        assert _tick_texts(back["manifest"]) == _tick_texts(base)
    finally:
        pool.discard(w)


def test_scale_change_does_not_leave_the_linear_locator_behind(library):
    """换成对数刻度时「自动」必须跟着变成对数轴自己的 locator。

    刻度模型缓存的「脚本原样」是在**换 scale 那一刻重新采集**的；不重采的话
    「自动」会把线性轴的 AutoLocator 按到对数轴上，一个刻度都出不来。
    """
    man = _render(library, [
        {"gid": "axes_0", "prop": "xscale", "value": "log"},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "auto"},
    ])
    assert _field(man, "axes_0", "xscale") == "log"
    assert _tick_texts(man), "对数轴上一个刻度都没有 = locator 用错了"


# ---------------------------------------------------------------------------
# 单条刻度文字
# ---------------------------------------------------------------------------
def _xtick_gids(man):
    return [e["gid"] for e in man["elements"]
            if e["role"] == "ticklabel" and ".xticklabels_" in e["gid"]]


def test_single_tick_label_edit_and_undo(library):
    base = _render(library)
    gids = _xtick_gids(base)
    assert len(gids) >= 3
    w = _worker(library)
    try:
        man = w.override(STEM, [{"gid": gids[1], "prop": "text", "value": "低"}])
        assert not man["warnings"], man["warnings"]
        assert "低" in _el(man["manifest"], gids[1])["label"]
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
        assert _tick_texts(back["manifest"]) == _tick_texts(base)
    finally:
        pool.discard(w)


def test_two_tick_labels_on_one_axis_do_not_clobber_each_other(library):
    """冻结是整条轴的动作：只冻自己那一条的话，第二条会把第一条顶掉。"""
    gids = _xtick_gids(_render(library))
    man = _render(library, [{"gid": gids[1], "prop": "text", "value": "AA"},
                            {"gid": gids[2], "prop": "text", "value": "BB"}])
    assert "AA" in _el(man, gids[1])["label"]
    assert "BB" in _el(man, gids[2])["label"]


def test_tick_label_edit_survives_a_formatter_change(library):
    """改数值格式之后，手动改过的那一条仍然是手动的内容（其余跟着格式走）。"""
    gids = _xtick_gids(_render(library))
    man = _render(library, [{"gid": gids[1], "prop": "text", "value": "★"},
                            {"gid": "axes_0.xticks", "prop": "format", "value": "%.2f"}])
    assert "★" in _el(man, gids[1])["label"]
    others = [e["label"] for e in man["elements"]
              if e["role"] == "ticklabel" and ".xticklabels_" in e["gid"]
              and e["gid"] != gids[1]]
    assert any("." in lbl for lbl in others), others


def test_tick_label_pointing_at_a_vanished_tick_reports_it(library):
    """刻度被换掉之后那条编辑落空了——必须**报出来**（→ 写回阻断 + 界面孤儿），
    绝不静默吞掉。静默吞掉的表现是「改了字，下一帧自己变回去，毫无提示」。"""
    gids = _xtick_gids(_render(library))
    w = _worker(library)
    try:
        resp = w.override(STEM, [
            {"gid": gids[-1], "prop": "text", "value": "尾"},
            {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
            {"gid": "axes_0.xticks", "prop": "major_values", "value": [0.2, 0.6]},
        ])
        assert resp["warnings"], "落空的刻度文字编辑必须报 warning"
        assert gids[-1] in " ".join(resp["warnings"])
        # 而且它已经从 manifest 里消失 → 前端把它列成可清理的孤儿
        assert gids[-1] not in [e["gid"] for e in resp["manifest"]["elements"]]
    finally:
        pool.discard(w)


def test_new_ticks_become_editable_after_the_locator_changes(library):
    """刻度变多之后，**新出现**的那些也要能选中能改——刻度伪元素每次渲染
    按当前状态重登记，不是 build 那一刻定死的。"""
    before = len(_xtick_gids(_render(library)))
    man = _render(library, [
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "major_step", "value": 0.05},
    ])
    after = _xtick_gids(man)
    assert len(after) > before, (before, len(after))
    # 新出现的最后一条能改
    man2 = _render(library, [
        {"gid": "axes_0", "prop": "xlim", "value": [0.0, 1.0]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "major_step", "value": 0.05},
        {"gid": after[-1], "prop": "text", "value": "末"},
    ])
    assert "末" in _el(man2, after[-1])["label"]
