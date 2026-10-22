"""新手教程演示图（ADR 0136）与示例脚本同源。

新手教程不执行 Python：那张反应动力学图由前端按 `web/src/lib/onboarding/demoFigure.json`
画出来。这份 JSON 是 `resources/tutorial_project/fig1_kinetics.py` 的镜像，唯一产生者
`scripts/gen_tutorial_demo_figure.py`。这里看护三件事：

* 生成物与「现在用真 matplotlib 跑一遍脚本量出来的」一致（改了脚本没重新生成就红）；
* 校对本身不是空门禁：改一个字、挪半个 pt 以上都会被它说出来；
* 欢迎页展示的每一行代码都逐字是脚本里的一行（顺序也一致）。
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "scripts" / "gen_tutorial_demo_figure.py"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_tutorial_demo_figure", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_figure_matches_the_shipped_script():
    gen = _gen()
    assert gen.check() == []


def test_cross_version_legend_box_is_pinned_at_the_tuple_loc_corner():
    """跨 Matplotlib 小版本：`loc=(x, y)` 钉的是图例左下角，字宽变了右边会动、左边与下边不动。"""
    gen = _gen()
    want = [100.0, 200.0, 104.0, 26.0]
    wider = [100.0, 200.0, 110.0, 26.0]  # 字宽变宽：右边外扩 6pt，左下角不动
    assert gen._diff(want, wider, "$.legend.box", loose=True) == []
    moved = [106.0, 200.0, 104.0, 26.0]  # 左边真的挪了：脚本变了
    assert gen._diff(want, moved, "$.legend.box", loose=True) != []


def test_measurement_ignores_rcparams_left_by_earlier_code():
    """同一进程里别的代码改过的 rcParams 不漏进测量，测完调用方的 rcParams 原样还回去。

    CI 上这条曾只在某一片红：同片前面的用例改了 `text.hinting` / `legend.labelspacing`。
    """
    import matplotlib as mpl

    gen = _gen()
    polluted = {"text.hinting": "none", "legend.labelspacing": 0.8, "font.weight": "bold"}
    with mpl.rc_context(polluted):
        assert gen.check() == []
        assert {k: mpl.rcParams[k] for k in polluted} == polluted


def test_check_is_not_hollow():
    """校对本身不是空门禁。拿「此刻量出来的」自己当基准做变异，不依赖磁盘那份是哪个版本生成的。"""
    gen = _gen()
    built = gen.build()
    assert gen._diff(built, copy.deepcopy(built)) == []

    renamed = copy.deepcopy(built)
    renamed["title"]["text"] = "Reaction kinetics at 35 °C"
    assert any("title.text" in p for p in gen._diff(built, renamed))

    moved = copy.deepcopy(built)
    moved["legend"]["box"][0] += gen.TOLERANCE_PT * 2
    assert any("legend.box[0]" in p for p in gen._diff(built, moved))

    nudged = copy.deepcopy(built)
    nudged["legend"]["box"][0] += gen.TOLERANCE_PT / 2
    assert gen._diff(built, nudged) == []

    recolored = copy.deepcopy(built)
    recolored["lines"][1]["color"] = "#c0562b"
    assert any("lines[1].color" in p for p in gen._diff(built, recolored))


def test_other_matplotlib_versions_only_loosen_glyph_metrics():
    """生成物来自另一版 matplotlib 时：只放过字形度量带来的变化，位置照样比。

    CI 的 Python 3.10 腿只装得上 matplotlib 3.10：文字外框的宽高、图例行高与本机 3.11 差几个 pt，
    但文字外框的中心、图例的左下角（loc=(x, y) 元组）两版一致——那才是「脚本有没有变」的证据。
    """
    gen = _gen()
    built = gen.build()
    other = copy.deepcopy(built)
    other["matplotlib"] = "0.0"
    # 版本带来的变化：文字外框绕中心变高、图例往上长（右下角不动）、条目行距略变
    other["xlabel"]["box"][1] -= 1.5
    other["xlabel"]["box"][3] += 3
    other["legend"]["box"][1] -= 3
    other["legend"]["box"][3] += 3
    other["legend"]["entries"][0]["handle"][0][1] += 2
    assert gen.compare(built, other) == []
    # 同版本时同样的偏差要红
    same = copy.deepcopy(other)
    same["matplotlib"] = built["matplotlib"]
    assert gen.compare(built, same)

    def shift(path, i, by):
        def f(d):
            node = d
            for k in path:
                node = node[k]
            node[i] += by

        return f

    # 版本不同也不放过：位置（轴标签 / 刻度标签的对齐锚点挪了、图例挪了）、条目超出有界容差、
    # 曲线、坐标轴、文字、颜色。锚点哪怕只挪半个字宽也要红（不能靠「起点 / 中心 / 终点任一对上」蒙混）
    half_width = built["xlabel"]["box"][2] / 2
    for mutate, needle in (
        (shift(["xlabel", "anchor"], 0, half_width), "xlabel.anchor"),
        (shift(["ylabel", "anchor"], 1, 3), "ylabel.anchor"),
        (
            lambda d: d["ticks"]["x"][1]["label"]["anchor"].__setitem__(
                0, d["ticks"]["x"][1]["label"]["anchor"][0] + 2
            ),
            "ticks.x[1].label.anchor",
        ),
        (shift(["legend", "box"], 1, 3), "legend.box"),
        (shift(["legend", "box"], 0, -3), "legend.box"),
        (
            shift(["legend", "entries", 1, "box"], 1, gen.METRIC_TOLERANCE_PT * 2),
            "legend.entries[1].box",
        ),
        (shift(["lines", 0, "points", 10], 1, 3), "lines[0].points"),
        (shift(["axes", "box"], 0, 3), "axes.box"),
        (lambda d: d["title"].__setitem__("text", "x"), "title.text"),
        (lambda d: d["lines"][1].__setitem__("color", "#000000"), "lines[1].color"),
    ):
        broken = copy.deepcopy(other)
        mutate(broken)
        assert any(needle in p for p in gen.compare(built, broken)), needle


def test_snippet_lines_are_verbatim_script_lines_in_order():
    gen = _gen()
    script_lines = [line.strip() for line in gen.SCRIPT.read_text(encoding="utf-8").splitlines()]
    on_disk = json.loads(gen.OUT.read_text(encoding="utf-8"))
    assert on_disk["snippet"] == gen.SNIPPET
    cursor = 0
    for group in on_disk["snippet"]:
        for line in group:
            assert line in script_lines[cursor:], f"欢迎页这一行不在脚本里（或顺序不对）：{line!r}"
            cursor = script_lines.index(line, cursor) + 1


def test_figure_describes_the_tutorial_story():
    """教程讲的三件事落在真实的图上：标题字号是脚本的 9 pt、图例比正文小、压住 Blank 曲线。"""
    fig = json.loads(_gen().OUT.read_text(encoding="utf-8"))
    assert fig["title"]["text"] == "Reaction kinetics at 25 °C"
    assert fig["title"]["sizePt"] == 9.0
    assert [e["text"].split(" ")[0] for e in fig["legend"]["entries"]] == ["Catalyst", "Blank"]
    # 图例比正文小一号（2026-10-22 起 7.2 pt = 9 pt × 0.8），放在轴框中部偏上并压住 Blank 虚线
    # （用户 10-22 裁决：教程要把「有问题的图」调成「正确的图」，图例不压线就没有可调的理由）；
    # 下方留出空地给后续操作
    assert all(e["sizePt"] == 7.2 for e in fig["legend"]["entries"])
    lx, ly, lw, lh = fig["legend"]["box"]
    ax, ay, aw, ah = fig["axes"]["box"]
    assert ax <= lx and lx + lw <= ax + aw and ay <= ly and ly + lh <= ay + ah, "图例应在坐标轴框内"
    # 坐标轴坐标（y 向上）：图例下沿 ≥ 0.4（下方留空）、上沿 ≤ 0.8（中部偏上，不顶到轴框上缘）
    bottom, top = 1 - (ly + lh - ay) / ah, 1 - (ly - ay) / ah
    assert 0.4 <= bottom and top <= 0.8, f"图例应在轴框中部偏上，实际 {bottom:.2f}–{top:.2f}"
    by_label = {line["label"].split(" ")[0]: line for line in fig["lines"]}
    inside = [
        (x, y) for x, y in by_label["Blank"]["points"] if lx <= x <= lx + lw and ly <= y <= ly + lh
    ]
    assert len(inside) >= 40, "脚本里的图例应当压住 Blank 虚线一大段——教程第二步的理由"
