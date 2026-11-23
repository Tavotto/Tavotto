# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「从数据到图」按画布的出版规范出图（ADR 0178 修订 2026-11-23；ADR 0155 的新消费者）。

判据的主语：**请求带的那份规范在每一层的读法是否一致**——
* 不带规范：样式、脚本、响应与从前逐字相同（默认规范）；
* 带规范：栏宽取规范规定的栏位（`widths_mm` 或 `page_columns`，后者经 `profiles.derive_page_columns`）、正文字号夹进规范允许的范围；
  规范没规定的项落到具名默认（期刊没写就是没写），下限 0 不当成数字用；
* 候选预览的脚本与落盘的脚本用同一份样式；
* 规范不合法不让出图失败：退回默认并标出；规范进幂等键。
"""

from __future__ import annotations

import copy
import re
import socket

import pytest

from tavotto.engine import ai_data_propose as P, ai_data_template as tpl, profiles
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import (  # noqa: F401
    commit,
    needs_worker,
    project,
    propose,
    put,
)


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


def journal_spec(**over) -> dict:
    """期刊中性底 + 这本「期刊」写了的项（前端 `resolveDocumentSpec` 交来的就是这种并好的规范）。"""
    spec = copy.deepcopy(profiles.load("journal-base-v1"))
    spec.update(over)
    return spec


NATURE_LIKE = {
    "widths_mm": {"single": 89.0, "double": 183.0, "tolerance_mm": 0.5, "allow_custom": True},
    "min_effective_font_size_pt": 5.0,
    "max_font_size_pt": 7.0,
}

ROWS = [["group", "value"]] + [[g, v] for g in "ABC" for v in (1.0, 1.2, 0.9, 1.1)]


def figsize_single_mm(script: str) -> float:
    m = re.search(r"figsize=\(\(([0-9.]+), ", script)
    assert m, script[:400]
    return round(float(m.group(1)) * 25.4, 3)


def cand_script(body) -> str:
    return (P.CANDIDATE_ROOT / body["session"] / "tavotto_figures" / "cand_1.py").read_text(
        encoding="utf-8"
    )


# =========================================================================== 样式（纯函数）
def test_default_profile_style_is_unchanged():
    d = tpl.style_from_profile()
    assert (d["width_single_mm"], d["width_double_mm"]) == (80.0, 150.0)
    assert (d["font_pt"], d["min_font_pt"]) == (9.0, 8.0)
    assert d == tpl.style_from_profile(profiles.load())


def test_journal_widths_and_font_range_are_used():
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec(**NATURE_LIKE)))
    assert (d["width_single_mm"], d["width_double_mm"]) == (89.0, 183.0)
    # 期刊不规定默认字号：9 pt 夹进 5–7 pt
    assert d["font_pt"] == 7.0 and d["min_font_pt"] == 5.0


def test_journal_that_specifies_nothing_falls_back_to_named_defaults():
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec()))
    base = tpl._STYLE_DEFAULTS
    assert (d["width_single_mm"], d["width_double_mm"]) == (
        base["width_single_mm"],
        base["width_double_mm"],
    )
    # 下限 0 = 没规定：不能让图例字号变成 0
    assert d["font_pt"] == 9.0 and d["min_font_pt"] == base["min_font_pt"]
    assert d["latin_fonts"] == base["latin_fonts"] and d["line_widths_pt"] == base["line_widths_pt"]


def col(cid: str, w: float) -> dict:
    return {"id": cid, "label": {"zh": cid, "en": cid}, "width_mm": w, "qualifier": "exact"}


@pytest.mark.parametrize(
    "cols, want",
    [
        ([col("a", 85.0), col("b", 114.0), col("c", 174.0)], (85.0, 174.0)),
        ([col("only", 120.0)], (120.0, 120.0)),
    ],
)
def test_page_columns_give_narrowest_and_widest(cols, want):
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec(page_columns=cols)))
    assert (d["width_single_mm"], d["width_double_mm"]) == want


def test_small_text_never_exceeds_body_text():
    d = tpl.style_from_profile(
        profiles.validate_spec(journal_spec(min_effective_font_size_pt=10.0, max_font_size_pt=12.0))
    )
    assert d["font_pt"] == 10.0 and d["min_font_pt"] == 10.0


# =========================================================================== 请求里的规范
def test_parse_spec_absent_invalid_and_oversized():
    assert P.parse_spec(None) == (None, None, False)
    for bad in ("nature", [], {"profile_id": "x"}, {"widths_mm": "wide"}):
        assert P.parse_spec(bad) == (None, "invalid", True), bad
    big = journal_spec(source="x" * (P.SPEC_MAX_BYTES + 1))
    assert P.parse_spec(big) == (None, "invalid", True)
    style, digest, bad = P.parse_spec(journal_spec(**NATURE_LIKE))
    assert style["width_single_mm"] == 89.0 and len(digest) == 64 and bad is False


def test_propose_without_spec_is_the_default_and_has_no_new_keys(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy").get_json()
    assert body["kind"] == "candidates" and "spec_fallback" not in body
    assert "figure_style" not in body
    assert figsize_single_mm(cand_script(body)) == 80.0
    assert '"font.size": 9.0' in cand_script(body)


def test_propose_with_spec_draws_at_journal_width_and_font(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy", spec=journal_spec(**NATURE_LIKE)).get_json()
    assert body["kind"] == "candidates" and "spec_fallback" not in body
    assert body["figure_style"] == {
        "width_single_mm": 89.0,
        "width_double_mm": 183.0,
        "font_pt": 7.0,
    }
    assert "tp.fit_fonts(fig)\n" in cand_script(body)  # 没带期刊字号范围：只摆图例，不退字号
    script = cand_script(body)
    assert figsize_single_mm(script) == 89.0
    assert '"font.size": 7.0' in script and '"legend.fontsize": 5.0' in script


def test_invalid_spec_falls_back_and_says_so(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    r = propose(client, render="lazy", spec={"widths_mm": "wide"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["kind"] == "candidates" and body["spec_fallback"] is True
    assert figsize_single_mm(cand_script(body)) == 80.0


def test_spec_is_part_of_the_idempotency_key(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    a = propose(client, render="lazy", client_request_id="rid-spec-0001").get_json()
    again = propose(client, render="lazy", client_request_id="rid-spec-0001").get_json()
    b = propose(
        client, render="lazy", client_request_id="rid-spec-0001", spec=journal_spec(**NATURE_LIKE)
    ).get_json()
    assert again["session"] == a["session"] and b["session"] != a["session"]
    assert figsize_single_mm(cand_script(b)) == 89.0


@needs_worker
def test_committed_script_uses_the_same_style_as_the_preview(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy", spec=journal_spec(**NATURE_LIKE)).get_json()
    first = body["items"][0]
    assert client.get(first["thumbnail_url"]).status_code == 200
    out = commit(client, body["session"], first["id"])
    assert out.status_code == 200, out.get_json()
    done = out.get_json()
    assert done["task"]["outcome"] == "completed", done
    script = (figs / done["script"]).read_text(encoding="utf-8")
    assert figsize_single_mm(script) == 89.0 and '"font.size": 7.0' in script


# =========================================================================== 期刊字号范围（2026-11-23 用户决定：符合范围是首要条件，不干涉时取范围内最大）
@pytest.mark.parametrize(
    "rng, font, floor",
    [
        ((5.0, 7.0), 7.0, 5.0),
        ((6.0, None), 9.0, 6.0),  # 只有下限：默认字号与下限取大
        ((10.0, None), 10.0, 10.0),
        ((None, 12.0), 12.0, 8.0),  # 只有上限：最多退到从前的小字字号
        ((None, 6.0), 6.0, 6.0),
    ],
)
def test_font_range_picks_the_largest_allowed_and_sets_the_floor(rng, font, floor):
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec()), rng)
    assert (d["font_pt"], d["min_font_pt"], d["fit_floor_pt"]) == (font, font, floor)
    assert d["font_range"] == list(rng)


def test_no_font_range_means_no_fitting_and_identical_style():
    assert "fit_floor_pt" not in tpl.style_from_profile()
    assert tpl.style_from_profile(None, (None, None)) == tpl.style_from_profile()


@pytest.mark.parametrize(
    "raw, want",
    [
        ({"min_pt": 5, "max_pt": 7}, (5.0, 7.0)),
        ({"min_pt": 5}, (5.0, None)),
        ({"max_pt": 7.5}, (None, 7.5)),
        ({}, None),
        ({"min_pt": 8, "max_pt": 6}, None),  # 写反了：当作没给
        ({"min_pt": "5"}, None),
        ({"min_pt": True}, None),
        ({"max_pt": 500}, None),
        ([5, 7], None),
        (None, None),
    ],
)
def test_parse_font_range(raw, want):
    assert P.parse_font_range(raw) == want


def test_propose_with_font_range_starts_at_the_maximum_and_fits(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    spec = journal_spec(**NATURE_LIKE)
    body = propose(
        client, render="lazy", spec=spec, font_range={"min_pt": 5, "max_pt": 7}
    ).get_json()
    assert body["figure_style"] == {
        "width_single_mm": 89.0,
        "width_double_mm": 183.0,
        "font_pt": 7.0,
        "font_min_pt": 5.0,
        "font_max_pt": 7.0,
    }
    script = cand_script(body)
    assert '"font.size": 7.0' in script and '"legend.fontsize": 7.0' in script
    assert "tp.fit_fonts(fig, 5.0)" in script
    # 范围进幂等键：同一请求号换了范围要重新出图
    a = propose(
        client,
        render="lazy",
        spec=spec,
        font_range={"min_pt": 5, "max_pt": 7},
        client_request_id="rid-font-0001",
    ).get_json()
    b = propose(
        client,
        render="lazy",
        spec=spec,
        font_range={"min_pt": 6, "max_pt": 8},
        client_request_id="rid-font-0001",
    ).get_json()
    assert a["session"] != b["session"] and '"font.size": 8.0' in cand_script(b)


def test_font_range_without_spec_is_ignored(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy", font_range={"min_pt": 5, "max_pt": 7}).get_json()
    assert "figure_style" not in body and "tp.fit_fonts(fig)\n" in cand_script(body)


def _roles(fig):
    ax = fig.axes[0]
    ticks = {t.get_fontsize() for t in ax.get_xticklabels() + ax.get_yticklabels() if t.get_text()}
    legend = {t.get_fontsize() for t in ax.get_legend().get_texts()}
    return ticks, legend


def test_fit_fonts_shrinks_a_whole_role_only_when_crowded_and_never_below_the_floor():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import warnings

    import matplotlib.pyplot as plt

    rc = {k: 12 for k in ("font.size", "xtick.labelsize", "ytick.labelsize", "axes.labelsize")}
    with plt.rc_context({**rc, "legend.fontsize": 12}), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        roomy, ax = plt.subplots(figsize=(3.5, 2.6))
        ax.plot(["a", "b", "c"], [1, 2, 3], label="x")
        ax.legend()
        roomy.tight_layout()
        tpl.fit_fonts(roomy, 5.0)
        assert _roles(roomy) == ({12.0}, {12.0})  # 不干涉：保持范围内最大

        tight, ax = plt.subplots(figsize=(2.0, 1.5))
        ax.plot([f"condition_{i}" for i in range(8)], range(8), label="a very long legend entry")
        ax.legend()
        tight.tight_layout()
        tpl.fit_fonts(tight, 5.0)
        ticks, legend = _roles(tight)
        # 同一类一个字号；退了，但不低于下限
        assert len(ticks) == 1 and len(legend) == 1
        assert 5.0 <= min(ticks) < 12.0 and 5.0 <= min(legend) < 12.0
        plt.close("all")


# =========================================================================== 图例：图内放得下就图内，压到数据才挪到图外（2026-11-23 用户决定）
def _ecdf(width_mm):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    fig, ax = plt.subplots(figsize=(width_mm / 25.4, width_mm / 25.4 * 0.75))
    for k, start in enumerate((46.0, 58.0, 71.0)):
        xs = [start + 1.2 * i for i in range(8)]
        ax.step(xs, [(i + 1) / 8 for i in range(8)], where="post", color=f"C{k}")
    names = ("control", "low", "high")
    ax.legend(handles=[Patch(facecolor=f"C{k}", label=n) for k, n in enumerate(names)])
    fig.tight_layout()
    return fig, ax


def _outside(ax):
    return ax.get_legend().get_bbox_to_anchor() is not ax.bbox


def test_legend_stays_inside_when_it_fits_and_moves_outside_when_it_hits_data():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import warnings

    import matplotlib.pyplot as plt

    with plt.rc_context({"font.size": 9, "legend.fontsize": 9}), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wide, ax = _ecdf(150)
        before = ax.get_legend()
        tpl.fit_fonts(wide)
        wide.draw_without_rendering()
        assert not _outside(ax) and not tpl.legend_hits_data(ax, ax.get_legend())
        assert ax.get_legend() is before  # 不压数据：原样不动

        narrow, ax = _ecdf(57)
        tpl.fit_fonts(narrow)
        narrow.draw_without_rendering()
        leg = ax.get_legend()
        assert _outside(ax) and not tpl.legend_hits_data(ax, leg)
        assert leg.get_window_extent().y0 >= ax.bbox.y1  # 在坐标区上方
        assert leg.get_window_extent().width <= ax.bbox.width + 1
        assert [t.get_text() for t in leg.get_texts()] == ["control", "low", "high"]
        assert {t.get_fontsize() for t in leg.get_texts()} == {9.0}  # 没给下限：不退字号

        fitted, ax = _ecdf(57)
        tpl.fit_fonts(fitted, 5.0)
        fitted.draw_without_rendering()
        leg = ax.get_legend()
        sizes = {t.get_fontsize() for t in leg.get_texts()}
        assert len(sizes) == 1 and 5.0 <= min(sizes) < 9.0  # 图外的图例把图挤扁了：整类退字号
        assert leg.get_window_extent().height <= 0.25 * ax.bbox.height + 1
        plt.close("all")


def test_only_what_really_covers_the_data_counts_as_a_hit():
    """没有底框：框里的空白压着线不算，只看文字和色块；画了不透明底框：整块框都算（它确实把线盖住了）。"""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def legend_over_line(frameon):
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot([0, 1], [0.5, 0.5], color="k")
        ax.set_ylim(0, 1)
        # 图例居中、内边距很大：线从底框中间穿过，文字 / 色块在线的上方
        leg = ax.legend(
            ["one"], loc="lower center", bbox_to_anchor=(0.5, 0.3), borderpad=5.0, frameon=frameon
        )
        fig.draw_without_rendering()
        frame, line_y = leg.get_window_extent(), ax.transData.transform((0.5, 0.5))[1]
        text = leg.get_texts()[0].get_window_extent()
        assert frame.y0 < line_y < frame.y1 and line_y < text.y0 - 2
        return ax, leg

    ax, leg = legend_over_line(False)
    assert tpl.legend_hits_data(ax, leg) is False
    ax, leg = legend_over_line(True)
    assert tpl.legend_hits_data(ax, leg) is True
    plt.close("all")
