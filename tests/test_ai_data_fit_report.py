# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""拟合线的系数可追溯（ADR 0178 修订 2026-11-24）。

判据的主语：**图上那条拟合线的截距 / 斜率 / n，在响应和脚本头里各有一份，且就是画出来的那条线**——与脚本运行时用的是同一个
`linear_fit`、同一份数据；对数轴那一维按 log10 报；没有拟合线的图不多出任何东西。
"""

from __future__ import annotations

import json
import socket

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_propose as P, ai_data_template as tpl
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import commit, needs_worker, project, propose, put  # noqa: F401


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


# y = 2 + 3x 精确成立的 12 个点（两个独立测量值：没有哪一列像有序横轴的重复）
EXACT = [["height_cm", "mass_kg"]] + [
    [x, 2 + 3 * x] for x in (1.5, 2.25, 3.0, 4.5, 5.0, 6.5, 7.25, 8.0, 9.5, 10.0, 11.5, 12.0)
]


def fit_items(out):
    return [
        i
        for i in out["items"]
        if any(layer["mark"] == "fit_line" for layer in i["values"]["layers"])
    ]


def test_the_response_reports_the_coefficients_of_the_drawn_fit(project):
    _, client, figs, _ = project
    put(figs, "data/xy.csv", EXACT)
    out = propose(client, render="lazy", answers={"x": "height_cm", "y": "mass_kg"}).get_json()
    assert out["kind"] == "candidates", out
    with_fit = fit_items(out)
    assert with_fit, [i["title_code"] for i in out["items"]]
    (fit,) = with_fit[0]["fits"]
    assert (
        fit["n"] == 12 and fit["y"] == "mass_kg" and fit["group"] is None and fit["panel"] is None
    )
    assert fit["intercept"] == pytest.approx(2.0) and fit["slope"] == pytest.approx(3.0)
    assert (fit["log_x"], fit["log_y"]) == (False, False)
    # 没有拟合线的备选：没有这一项
    assert all("fits" not in i for i in out["items"] if i not in with_fit)


def test_the_script_header_carries_the_same_numbers_as_one_ascii_comment_line(project):
    _, client, figs, _ = project
    rows = [["剂量", "响应"]] + EXACT[1:]
    put(figs, "data/xy.csv", rows)
    out = propose(client, render="lazy", answers={"x": "剂量", "y": "响应"}).get_json()
    item = fit_items(out)[0]
    n = [i["id"] for i in out["items"]].index(item["id"]) + 1
    script = (P.CANDIDATE_ROOT / out["session"] / "tavotto_figures" / f"cand_{n}.py").read_text(
        encoding="utf-8"
    )
    lines = script.split("\n")
    at = next(i for i, ln in enumerate(lines) if ln.startswith(tpl.SOURCE_COMMENT_PREFIX))
    line = lines[at + 1]
    assert line.startswith(tpl.FIT_COMMENT_PREFIX) and line.isascii()
    body = json.loads(line[len(tpl.FIT_COMMENT_PREFIX) :])
    assert body["at"] == "generation" and body["method"] == "least_squares"
    assert body["fits"] == item["fits"] and body["fits"][0]["y"] == "响应"
    # 来源注释照样读得出来（多的那一行不影响它）
    assert tpl.parse_source_comment(script) is not None
    # 没有拟合线的脚本里没有这一行
    other = next(i for i in out["items"] if "fits" not in i)
    m = [i["id"] for i in out["items"]].index(other["id"]) + 1
    plain = (P.CANDIDATE_ROOT / out["session"] / "tavotto_figures" / f"cand_{m}.py").read_text(
        encoding="utf-8"
    )
    assert tpl.FIT_COMMENT_PREFIX not in plain


def test_fit_report_matches_the_segment_the_script_draws_including_log_axes(tmp_path):
    p = tmp_path / "pow.csv"
    xs = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
    p.write_text("conc,signal\n" + "".join(f"{x},{5 * x**0.5}\n" for x in xs), encoding="utf-8")
    values = adf.inspect_data_source(p)
    from tavotto.engine import ai_data_recipe as R

    recipe = R.FigureRecipe.from_dict(
        {
            "table": "pow.csv",
            "family": "relationship",
            "layers": [{"mark": "points"}, {"mark": "fit_line"}],
            "x": "conc",
            "y": ["signal"],
            "scale": "log_xy",
            "why": "x",
        }
    )
    (fit,) = tpl.fit_report(values, recipe)
    # 幂律 y = 5·x^0.5：在 log10 坐标里截距 = log10(5)、斜率 = 0.5
    assert fit["log_x"] and fit["log_y"] and fit["n"] == 10
    assert fit["slope"] == pytest.approx(0.5) and 10 ** fit["intercept"] == pytest.approx(5.0)
    # 与画出来的线段一致：线段两端落在这条直线上
    d = tpl.relationship_data(values.columns, values.rows, "conc", "signal", None, None)
    fx, fy = tpl.fit_segment(tpl.relationship_pairs(d, "", ""), True, True)
    import math

    for x, y in zip(fx, fy):
        assert math.log10(y) == pytest.approx(fit["intercept"] + fit["slope"] * math.log10(x))


def test_no_fit_layer_means_an_empty_report_and_an_unchanged_script(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text(
        "group,value\n" + "".join(f"{g},{v}\n" for g in "ABC" for v in (1, 2, 3, 4)),
        encoding="utf-8",
    )
    values = adf.inspect_data_source(p)
    from tavotto.engine import ai_data_recipe as R

    entry = adf.TableEntry("g.csv", None, False, values)
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "g.csv",
            "family": "comparison",
            "layers": [{"mark": "jitter"}],
            "x": "group",
            "y": ["value"],
            "why": "x",
        }
    )
    assert tpl.fit_report(values, recipe) == []
    a = tpl.render_recipe_script(entry, recipe, data_rel="g.csv", stem="s").script
    b = tpl.render_recipe_script(entry, recipe, data_rel="g.csv", stem="s", fits=[]).script
    assert a == b and tpl.FIT_COMMENT_PREFIX not in a


@needs_worker
def test_the_committed_figure_reports_the_fit_too(project):
    _, client, figs, _ = project
    put(figs, "data/xy.csv", EXACT)
    out = propose(client, render="lazy", answers={"x": "height_cm", "y": "mass_kg"}).get_json()
    item = fit_items(out)[0]
    assert client.get(item["thumbnail_url"]).status_code == 200
    done = commit(client, out["session"], item["id"]).get_json()
    assert done["task"]["outcome"] == "completed", done
    assert done["fits"] == item["fits"]
    script = (figs / done["script"]).read_text(encoding="utf-8")
    assert tpl.FIT_COMMENT_PREFIX in script
