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


def test_check_is_not_hollow():
    gen = _gen()
    built = gen.build()
    on_disk = json.loads(gen.OUT.read_text(encoding="utf-8"))
    assert gen._diff(built, on_disk) == []

    renamed = copy.deepcopy(on_disk)
    renamed["title"]["text"] = "Reaction kinetics at 35 °C"
    assert any("title.text" in p for p in gen._diff(built, renamed))

    moved = copy.deepcopy(on_disk)
    moved["legend"]["box"][0] += gen.TOLERANCE_PT * 2
    assert any("legend.box[0]" in p for p in gen._diff(built, moved))

    nudged = copy.deepcopy(on_disk)
    nudged["legend"]["box"][0] += gen.TOLERANCE_PT / 2
    assert gen._diff(built, nudged) == []

    recolored = copy.deepcopy(on_disk)
    recolored["lines"][1]["color"] = "#c0562b"
    assert any("lines[1].color" in p for p in gen._diff(built, recolored))


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
    """教程讲的三件事落在真实的图上：标题字号是脚本的 9 pt、图例初始压在曲线上。"""
    fig = json.loads(_gen().OUT.read_text(encoding="utf-8"))
    assert fig["title"]["text"] == "Reaction kinetics at 25 °C"
    assert fig["title"]["sizePt"] == 9.0
    assert [e["text"].split(" ")[0] for e in fig["legend"]["entries"]] == ["Catalyst", "Blank"]
    lx, ly, lw, lh = fig["legend"]["box"]
    inside = [
        (x, y)
        for line in fig["lines"]
        for x, y in line["points"]
        if lx <= x <= lx + lw and ly <= y <= ly + lh
    ]
    assert inside, "脚本里 loc='lower right' 的图例应当压着曲线起始段——教程第二步的理由"
