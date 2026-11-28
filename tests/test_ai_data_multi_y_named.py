# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""点名的多条纵轴：上限从 3 放到 8，调色板同时加到 8 种颜色（维护者 2026-11-28 定；ADR 0178 修订）。

判据的主语：**推荐的那张图画了哪几列**。用户 / 模型点名 2–8 个同单位的数值列、且有有序横轴 → 每列一条线，全部画出；
规则**自己**默认选列时的上限仍是 3（宽表超过 3 个数值列照旧先问），不因为这次放宽而往一张图上堆线。
"""

from __future__ import annotations

import os
import socket
import subprocess

import pytest

from tavotto.engine import ai_data_propose as P, ai_data_recipe as R, ai_data_template as tpl, pool
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import project  # noqa: F401

SPECIES = ["H2O", "OH", "O2", "H2", "H2O2", "HO2", "O", "H", "O3"]


def table(n: int) -> str:
    cols = SPECIES[:n]
    rows = ["step," + ",".join(cols)]
    for k in range(12):
        rows.append(f"{k * 1000}," + ",".join(str((k * (j + 2) + j) % 37) for j in range(n)))
    return "\n".join(rows) + "\n"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")


def propose(tmp_path, n, answers=None):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "species.csv").write_text(table(n), encoding="utf-8", newline="")
    kw = {"answers": answers} if answers else {}
    return P.propose(root, project="p", folder="data", render="lazy", understand=False, **kw)


def recommended(resp):
    assert resp["kind"] == "candidates" and resp["items"], resp
    return next(i for i in resp["items"] if i.get("recommended"))["values"]


def test_both_colour_limits_fit_the_palette_and_the_rules_own_default_stays_three():
    assert R.MULTI_Y_MAX == R.MAX_GROUP_LEVELS == 8
    # 每条线 / 每个分组一种颜色：上限不许超过调色板，否则两个同色（8 组时第 8 组曾与第 1 组同色）
    assert len(tpl.PALETTE) >= R.MAX_GROUP_LEVELS and len(set(tpl.PALETTE)) == len(tpl.PALETTE)
    assert R.DEFAULT_MULTI_Y == 3


def test_the_first_seven_colours_keep_their_order_and_grey_is_appended():
    """已有的图重新生成时颜色不变：只在末尾加了一种。"""
    assert tpl.PALETTE[:7] == (
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#E69F00",
        "#56B4E9",
        "#CC79A7",
        "#000000",
    )
    assert tpl.PALETTE[7] == "#7F7F7F"


@pytest.mark.parametrize("n", [4, 6, 8])
def test_named_columns_up_to_eight_are_all_drawn_as_lines(project, tmp_path, n):
    ys = SPECIES[:n]
    v = recommended(propose(tmp_path, 8, {"y": ys, "x": "step", "group": "none"}))
    assert v["x"] == "step" and v["y"] == ys


def test_nine_named_columns_are_refused_outright(project, tmp_path):
    """超过 8 个：请求形状不合法，整个拒绝——不会悄悄画 9 条，也不会只画其中几条冒充。"""
    with pytest.raises(P.ProposeError) as e:
        propose(tmp_path, 9, {"y": SPECIES[:9], "x": "step", "group": "none"})
    assert e.value.code == "propose_input_invalid"


def test_without_a_pick_a_wide_table_of_six_measures_is_handled_as_before(project, tmp_path):
    """对照：没人点名时，规则自己不会把 6 列一起画（超过 3 个数值列照旧先问 / 只挑一列）。"""
    resp = propose(tmp_path, 6)
    if resp["kind"] == "candidates":
        assert len(recommended(resp)["y"]) <= R.DEFAULT_MULTI_Y
    else:
        assert resp["kind"] == "ask", resp


@pytest.mark.parametrize(
    ("n", "group"), [(3, "condition"), (4, "condition"), (8, "condition"), (8, "none")]
)
def test_rendered_lines_and_legend_distinguish_each_named_measure(tmp_path, n, group):
    try:
        worker_python = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("没有装有 matplotlib 的解释器")
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    ys = [f"p{i + 1}" for i in range(n)]
    # p1 与 p4 在每组内部交叉：数据不同，颜色却已被条件分组占用。
    rows = [
        [1.1, 2.6, 4.8, 6.1],
        [3.4, 2.9, 4.6, 3.2],
        [6.2, 3.5, 4.7, 1.2],
        [2.0, 3.6, 5.7, 7.2],
        [4.1, 4.0, 5.5, 4.2],
        [7.1, 4.5, 5.6, 2.1],
    ]
    conditions = ["low", "high"] if group != "none" else []
    lines = ["date,condition," + ",".join(ys)]
    for i, row in enumerate(rows if conditions else rows[:3]):
        row = row + [(i + 1) * (j + 2) + 0.3 for j in range(4)]
        lines.append(
            f"2026-01-0{1 + i % 3},{'low' if i < 3 else 'high'}," + ",".join(map(str, row[:n]))
        )
    (data / "fixture.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    resp = P.propose(
        root,
        project="p",
        folder="data",
        render="lazy",
        understand=False,
        answers={"x": "date", "y": ys, "group": group},
    )
    v = recommended(resp)
    assert v["y"] == ys and v["group"] == (group if conditions else None)
    it = next(i for i in resp["items"] if i.get("recommended"))
    sess, item = P.get_item("p", resp["session"], it["id"])
    script = sess.dir / "tavotto_figures" / f"{item.stem}.py"
    # Matplotlib 的 get_linestyle() 会把自定义节奏都报成 --，必须量实际 dash 序列。
    code = f"""
import runpy
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import to_rgba

ys = {ys!r}
conditions = {conditions!r}
runpy.run_path({str(script)!r}, run_name="__main__")
ax = plt.gcf().axes[0]
by_label = {{line.get_label(): line for line in ax.lines}}
legend = ax.get_legend()
keys = {{text.get_text(): handle for text, handle in zip(legend.get_texts(), legend.legend_handles)}}
assert set(keys) == set(conditions + ys)
assert len(ax.lines) == max(1, len(conditions)) * len(ys)
assert Path({str(script.with_suffix(".pdf"))!r}).read_bytes().startswith(b"%PDF-")

def dashes(line):
    offset, pattern = line._unscaled_dash_pattern
    return (offset, tuple(pattern) if pattern else None)

if conditions:
    for condition in conditions:
        curves = [by_label[f"line | {{condition}} | {{y}}"] for y in ys]
        styles = [dashes(line) for line in curves]
        assert len(set(styles)) == len(ys), (condition, styles)
        assert [dashes(keys[y]) for y in ys] == styles
        assert all(to_rgba(line.get_color()) == keys[condition].get_facecolor() for line in curves)
        assert [line.get_linestyle() for line in curves[:3]] == ["-", "--", ":"]
        if len(curves) >= 4:
            p1, p4 = curves[0].get_ydata(), curves[3].get_ydata()
            assert p1[0] < p4[0] and p1[-1] > p4[-1]
else:
    assert len({{line.get_color() for line in ax.lines}}) == len(ys)
    assert all(keys[y].get_facecolor() == to_rgba(by_label[f"line | {{y}}"].get_color()) for y in ys)
"""
    run = subprocess.run(
        [worker_python, "-c", code],
        cwd=script.parent,
        env={**os.environ, "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=40,
    )
    assert run.returncode == 0, run.stderr
