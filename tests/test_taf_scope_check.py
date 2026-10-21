"""TAF「目标范围以外有没有改动」（`figureverify.changes_outside_target`，`05-verification-design.md` 第七节）。

主语：Tavotto worker 产出的、**不带用户 override** 的 manifest（`override(stem, [])`，与 `app._ai_task_hooks.before` 同口径）。
FigureBench 全部探针场景（dev + holdout）的基线 / 参考解 / 每个反例各 build 一次，然后：

* 目标从场景的 goal 断言推：`figureverify._subject_gids` 收齐全部目标 gid——一个元素 → element；都在同一个 `axes_N` → axes；
  否则 figure（`not_applicable`）；
* **每个参考解**都不是 unknown（verified / not_applicable）——零误判；
* **scope_discipline 的反例**里，凡是 `must_fail` 指名的对象落在目标范围外的，都是 unknown（抓住）；落在目标自己身上的
  （「目标线顺手多改了线宽」）不归这条检查管——那是目标内部的未点名属性，由场景自己的保持断言（`p.*`）判；
* 其它类别的反例只报告抓住与否（打印），不强求。

另有两条合成脚本钉住「派生改动」的排除理由（tight_layout 重排轴位置、柱宽移动自动坐标范围）以及判据的纯逻辑。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from support.figurebench import FIGURE_FIXTURES, probe_scenarios, stage, variants
from tavotto.engine import figureverify, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

_AXES = re.compile(r"axes_\d+")


def _manifest(script, stem: str) -> dict:
    w = pool.one_shot(script.name, str(script.parent), "main")
    try:
        w.ensure_built()
        return w.override(stem, [])["manifest"]
    finally:
        pool.discard(w)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if WORKER_PY is None:
        pytest.skip("not_run：找不到装有 matplotlib 的解释器")
    work = tmp_path_factory.mktemp("scope")
    staged = [
        (job_id, fixture, stage(work, job_id, fixture, edits, sid))
        for job_id, fixture, edits, sid in variants()
    ]

    def build(item):
        job_id, fixture, script = item
        try:
            return job_id, _manifest(script, fixture)
        except Exception as exc:  # noqa: BLE001 —— 构建失败本身要报出来
            return job_id, {"__error__": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(build, staged))


def _ok(manifests: dict, job_id: str) -> dict:
    man = manifests[job_id]
    assert "__error__" not in man, f"{job_id} 构建失败：{man['__error__']}"
    return man


def _target(scenario: dict, base: dict) -> tuple[str, str | None]:
    gids: list[str] = []
    for a in scenario["goal"]:
        for g in figureverify._subject_gids(a, base):
            if g not in gids:
                gids.append(g)
    if len(gids) == 1:
        return "element", gids[0]
    axes = {m.group(0) for g in gids if (m := _AXES.match(g))}
    if gids and len(axes) == 1 and all(_AXES.match(g) for g in gids):
        return "axes", gids[0]
    return "figure", None


def _verdict(manifests, scenario, job_id):
    base = _ok(manifests, f"base__{scenario['fixture']}")
    scope, gid = _target(scenario, base)
    (c,) = figureverify.changes_outside_target(_ok(manifests, job_id), base, scope=scope, gid=gid)
    return c


@needs_worker
@pytest.mark.parametrize("scenario", probe_scenarios(), ids=lambda s: s["id"])
def test_reference_solution_is_never_flagged(built, scenario):
    c = _verdict(built, scenario, f"{scenario['id']}__ref")
    assert c.verdict in ("verified", "not_applicable"), (scenario["id"], c.reason_code, c.observed)


def _outside_the_target(scenario: dict, base: dict, negative: dict) -> bool:
    scope, gid = _target(scenario, base)
    if scope == "figure":
        return False
    by_id = {a["id"]: a for a in scenario["goal"] + scenario["preserve"]}
    subjects = [
        g for aid in negative["must_fail"] for g in figureverify._subject_gids(by_id[aid], base)
    ]
    return any(not figureverify._in_target(g, scope=scope, target=gid) for g in subjects)


@needs_worker
def test_scope_discipline_negatives_that_touch_something_else_are_caught(built):
    caught = total = inside = other_caught = other_total = 0
    lines = []
    for s in probe_scenarios():
        base = _ok(built, f"base__{s['fixture']}")
        for n in s["negative_variants"]:
            c = _verdict(built, s, f"{s['id']}__{n['id']}")
            if s["category"] != "scope_discipline":
                other_total += 1
                other_caught += c.verdict == "unknown"
                lines.append(f"  {s['id']}/{n['id']}: {c.verdict} {c.observed.get('elements', '')}")
                continue
            if _outside_the_target(s, base, n):
                total += 1
                caught += c.verdict == "unknown"
                assert c.verdict == "unknown", (s["id"], n["id"], c.verdict, c.observed)
            else:
                inside += 1
    # 统计只走被捕获的 stdout（`-s` / 失败时可见）：capsys.disabled() 会直写控制台，Windows cp1252 编码不了中文
    print(
        f"\nscope_discipline 范围外反例抓住 {caught}/{total}（另 {inside} 个改的是目标自己未点名的属性，"
        f"不归本检查）；其它类别反例抓住 {other_caught}/{other_total}（信息，不强求）"
    )
    print("\n".join(lines))
    assert total >= 8 and caught == total


# --------------------------------------------------------------------------- 派生改动的排除理由（合成脚本，实测）
TIGHT = """
import matplotlib.pyplot as plt
def main():
    fig, axes = plt.subplots(1, 2, figsize=(5, 3))
    axes[0].plot([0, 1, 2], [0, 1, 4])
    axes[1].plot([0, 1, 2], [3, 2, 1])
    axes[0].set_title("T", fontsize={fs})
    fig.tight_layout()
    fig.savefig("fig.pdf")
if __name__ == "__main__":
    main()
"""
BARS = """
import matplotlib.pyplot as plt
def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar([0, 1, 2], [1, 2, 3], width={w}, label="a")
    ax.set_title("t")
    fig.savefig("fig.pdf")
if __name__ == "__main__":
    main()
"""


def _build(tmp_path, name: str, src: str) -> dict:
    d = tmp_path / name
    d.mkdir()
    (d / "fig.py").write_text(src, encoding="utf-8")
    return _manifest(d / "fig.py", "fig")


def _only(**kw):
    (c,) = figureverify.changes_outside_target(**kw)
    return c


@needs_worker
def test_layout_reflow_of_axes_positions_is_not_an_outside_change(tmp_path):
    before = _build(tmp_path, "a", TIGHT.format(fs=10))
    after = _build(tmp_path, "b", TIGHT.format(fs=22))
    raw = figureverify._changes(after, before)
    assert ("axes_1", "position") in raw, "tight_layout 真的把没被点的子图也挪了位置"
    c = _only(after=after, before=before, scope="element", gid="axes_0.title")
    assert c.verdict == "verified", c.observed
    # 没有起因（目标内没有真改动）时，位置变化照常算范围外
    c = _only(after=after, before=before, scope="element", gid="axes_1.lines_0")
    assert c.verdict == "unknown" and "axes_0.title" in c.observed["elements"]


@needs_worker
def test_bar_width_moves_the_autoscaled_range_and_ticks_with_it(tmp_path):
    before = _build(tmp_path, "a", BARS.format(w=0.8))
    after = _build(tmp_path, "b", BARS.format(w=0.3))
    raw = {g for g, _ in figureverify._changes(after, before)}
    assert "axes_0" in raw and "axes_0.xticks" in raw, "柱宽一变，自动坐标范围与刻度跟着变"
    c = _only(after=after, before=before, scope="element", gid="axes_0.barseries_0")
    assert c.verdict == "verified", c.observed


TWO_AXES = """
import matplotlib.pyplot as plt
def main():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6, 3){share})
    a.plot([0, 1, 2], [0, 1, 4], linewidth={lw}, label="a")
    b.plot([0, 1, 2], [3, 2, 1])
    {extra}
    fig.savefig("fig.pdf")
if __name__ == "__main__":
    main()
"""


def _two(tmp_path, name, *, lw=1, extra="pass", share=""):
    return _build(tmp_path, name, TWO_AXES.format(lw=lw, extra=extra, share=share))


@needs_worker
def test_explicit_limits_on_the_targets_own_axes_are_not_autoscale(tmp_path):
    """Codex #1266 P1：改线宽 + 同一父轴手动收窄 ylim——显式范围不是自动缩放，不能被线宽「起因」免责。"""
    before = _two(tmp_path, "a")
    after = _two(tmp_path, "b", lw=4, extra="a.set_ylim(0, 2)")
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and "axes_0" in c.observed["elements"], c.observed


@needs_worker
def test_explicit_limits_on_another_subplot_are_outside(tmp_path):
    before = _two(tmp_path, "a")
    after = _two(tmp_path, "b", lw=4, extra="b.set_ylim(0, 2)")
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and "axes_1" in c.observed["elements"], c.observed


@needs_worker
def test_line_width_alone_changes_nothing_outside(tmp_path):
    before = _two(tmp_path, "a")
    after = _two(tmp_path, "b", lw=4)
    assert _only(after=after, before=before, scope="element", gid="axes_0.lines_0").verdict == (
        "verified"
    )


def _autoscale_cause_in_other_axes() -> tuple[dict, dict]:
    """合成：目标在 axes_0 的柱组，自动范围变化落在 axes_1（不共享）——起因不在同一个轴。"""

    def man(lim):
        ax1 = {
            **_el("axes_1", "axes", xlim=lim),
            "autoscale": {"x": True, "y": True},
        }
        ax0 = {**_el("axes_0", "axes", xlim=[0, 1]), "autoscale": {"x": True, "y": True}}
        return _m(
            ax0, ax1, _el("axes_0.barseries_0", "bar_series", width=0.8 if lim[1] == 1 else 0.3)
        )

    return man([0, 1]), man([0, 2])


def test_auto_range_change_needs_a_cause_in_the_same_axes():
    before, after = _autoscale_cause_in_other_axes()
    c = _only(after=after, before=before, scope="element", gid="axes_0.barseries_0")
    assert c.verdict == "unknown" and c.observed["elements"] == ["axes_1"]


def test_shared_axes_peers_count_as_the_same_axes():
    def man(w, lim):
        ax = lambda g, p: {  # noqa: E731
            **_el(g, "axes", xlim=lim),
            "autoscale": {"x": True, "y": True, "peers": {"x": [p]}, "expected": {"x": lim}},
        }
        return _m(
            ax("axes_0", "axes_1"),
            ax("axes_1", "axes_0"),
            _el("axes_0.barseries_0", "bar_series", width=w),
        )

    c = _only(
        after=man(0.3, [0, 2]), before=man(0.8, [0, 1]), scope="element", gid="axes_0.barseries_0"
    )
    assert c.verdict == "verified", c.observed


def _line_axes(lw, lim, *, expected, autoscale=True):
    ax = {
        **_el("axes_0", "axes", xlim=lim),
        "autoscale": {
            "x": autoscale,
            "y": True,
            **({"expected": {"x": expected}} if expected else {}),
        },
    }
    return _m(ax, _el("axes_0.lines_0", "line", linewidth=lw))


def test_range_that_differs_from_what_autoscale_would_give_is_not_autoscale():
    """Codex #1266 第二 / 三轮 P1：autoscale 开着、线宽变了、xlim 变了，但 xlim 不是自动缩放会算出的范围——显式写的。"""
    before = _line_axes(1, [0, 1], expected=[0, 1])
    after = _line_axes(4, [0, 5], expected=[0, 4.5])
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and c.observed["elements"] == ["axes_0"], c.observed


def test_range_equal_to_the_expected_autoscale_range_is_autoscale():
    before = _line_axes(1, [0, 1], expected=[0, 1])
    after = _line_axes(4, [0, 4.5], expected=[0, 4.5])
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "verified", c.observed
    # 反向轴（xlim 倒序）按排序后比较
    after = _line_axes(4, [4.5, 0], expected=[0, 4.5])
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "verified", c.observed


def test_missing_expected_is_not_evidence():
    before = _line_axes(1, [0, 1], expected=None)
    after = _line_axes(4, [0, 5], expected=None)
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown", c.observed


BAR_WIDTH = """
import matplotlib.pyplot as plt
def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar([0, 1, 2], [1, 2, 3], width={w}, label="a")
    {extra}
    fig.savefig("fig.pdf")
if __name__ == "__main__":
    main()
"""


@needs_worker
def test_bar_width_change_with_an_explicit_enclosing_xlim_is_not_waived(tmp_path):
    """Codex #1266 第三轮 P1：改柱宽（dataLim 变）+ set_xlim(-3, 9, auto=True)——范围是显式写的，不是自动缩放。"""
    before = _build(tmp_path, "a", BAR_WIDTH.format(w=0.8, extra="pass"))
    after = _build(tmp_path, "b", BAR_WIDTH.format(w=0.3, extra="ax.set_xlim(-3, 9, auto=True)"))
    c = _only(after=after, before=before, scope="element", gid="axes_0.barseries_0")
    assert c.verdict == "unknown" and "axes_0" in c.observed["elements"], c.observed


def test_synthetic_bar_width_with_enclosing_explicit_xlim_is_not_waived():
    def man(w, lim, expected):
        ax = {
            **_el("axes_0", "axes", xlim=lim),
            "autoscale": {"x": True, "y": True, "expected": {"x": expected}},
        }
        return _m(ax, _el("axes_0.barseries_0", "bar_series", width=w))

    c = _only(
        after=man(0.3, [-3, 9], [-0.5, 2.5]),
        before=man(0.8, [-0.6, 2.6], [-0.6, 2.6]),
        scope="element",
        gid="axes_0.barseries_0",
    )
    assert c.verdict == "unknown", c.observed


@needs_worker
def test_probing_autoscale_does_not_touch_the_figure(monkeypatch):
    import tavotto.engine

    # engine/ 里的模块互相用裸名 import（worker 里 engine 目录在 sys.path 上）
    monkeypatch.syspath_prepend(str(__import__("pathlib").Path(tavotto.engine.__file__).parent))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from manifest import _probe_autoscale_expected

    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [0, 1, 4])
    ax.set_xlim(-3, 9, auto=True)
    before = (ax.get_xlim(), ax.get_ylim(), ax.get_autoscalex_on(), ax.get_autoscaley_on())
    tight, d = ax._tight, dict(ax.__dict__)
    got = _probe_autoscale_expected(ax, {"x": True, "y": True})
    assert (ax.get_xlim(), ax.get_ylim(), ax.get_autoscalex_on(), ax.get_autoscaley_on()) == before
    assert ax._tight == tight and ax.__dict__.keys() == d.keys()
    assert "set_xbound" not in ax.__dict__ and "set_ybound" not in ax.__dict__
    assert got["x"] != [-3.0, 9.0] and got["x"][0] < 0 < 2 < got["x"][1] < 3
    plt.close(fig)


@needs_worker
def test_probing_autoscale_restores_instance_level_set_bound(monkeypatch):
    import tavotto.engine

    monkeypatch.syspath_prepend(str(__import__("pathlib").Path(tavotto.engine.__file__).parent))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from manifest import _probe_autoscale_expected

    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [0, 1, 4])

    def custom(*a, **kw):  # 用户实例上自己挂的覆盖
        raise AssertionError("probe must not call the user's override")

    ax.set_xbound = custom
    got = _probe_autoscale_expected(ax, {"x": True, "y": True})
    assert ax.__dict__["set_xbound"] is custom
    assert "set_ybound" not in ax.__dict__
    assert "x" in got and "y" in got
    plt.close(fig)


@needs_worker
def test_set_xlim_auto_true_with_a_line_width_change_is_not_waived(tmp_path):
    before = _two(tmp_path, "a")
    after = _two(tmp_path, "b", lw=4, extra="a.set_xlim(-3, 9, auto=True)")
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and "axes_0" in c.observed["elements"], c.observed


@needs_worker
def test_set_xlim_then_reenabling_autoscale_is_not_waived(tmp_path):
    before = _two(tmp_path, "a")
    after = _two(tmp_path, "b", lw=4, extra="a.set_xlim(-3, 9); a.set_autoscalex_on(True)")
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and "axes_0" in c.observed["elements"], c.observed


def _colorbar_stem(vmax, ylim, *, other_lw):
    cb_ax = {
        **_el("axes_1", "axes", ylim=ylim),
        "is_colorbar": True,
        "autoscale": {"x": False, "y": False},
    }
    return _m(
        {**_el("axes_0", "axes", xlim=[0, 1]), "autoscale": {"x": True, "y": True}},
        cb_ax,
        {**_el("axes_1.colorbar", "colorbar", vmin=0, vmax=vmax), "geom_gid": "axes_1"},
        _el("axes_0.lines_0", "line", linewidth=other_lw),
    )


def test_colorbar_axis_range_needs_a_vmin_vmax_cause():
    """色条轴被 Agent 显式 set_ylim、同时目标线宽变了：范围起因不是色条的 vmin/vmax，不放过。"""
    before = _colorbar_stem(1, [0, 1], other_lw=1)
    after = _colorbar_stem(1, [0, 3], other_lw=4)
    c = _only(after=after, before=before, scope="element", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and "axes_1" in c.observed["elements"], c.observed


def test_colorbar_axis_range_follows_its_own_vmax_change():
    before = _colorbar_stem(1, [0, 1], other_lw=1)
    after = _colorbar_stem(3, [0, 3], other_lw=1)
    c = _only(after=after, before=before, scope="element", gid="axes_1.colorbar")
    assert c.verdict == "verified", c.observed


def test_missing_autoscale_fact_is_not_evidence():
    before = _m(
        _el("axes_0", "axes", xlim=[0, 1]), _el("axes_0.barseries_0", "bar_series", width=0.8)
    )
    after = _m(
        _el("axes_0", "axes", xlim=[0, 2]), _el("axes_0.barseries_0", "bar_series", width=0.3)
    )
    c = _only(after=after, before=before, scope="element", gid="axes_0.barseries_0")
    assert c.verdict == "unknown" and c.observed["elements"] == ["axes_0"]


# --------------------------------------------------------------------------- 判据的纯逻辑（合成 manifest）
def _el(gid, role, **props):
    return {
        "gid": gid,
        "role": role,
        "editable": [{"prop": k, "value": v} for k, v in props.items()],
    }


def _m(*els):
    return {"elements": list(els)}


def test_scope_figure_or_missing_gid_is_not_applicable():
    m = _m(_el("axes_0.title", "title", fontsize=10))
    for scope, gid in (("figure", "axes_0.title"), ("element", None), ("axes", "")):
        c = _only(after=m, before=m, scope=scope, gid=gid)
        assert (c.verdict, c.reason_code) == ("not_applicable", "whole_figure_target")


def test_axes_scope_covers_the_whole_axes_but_not_its_neighbour():
    before = _m(
        _el("axes_0.title", "title", fontsize=10), _el("axes_1.title", "title", fontsize=10)
    )
    after = _m(_el("axes_0.title", "title", fontsize=12), _el("axes_1.title", "title", fontsize=12))
    c = _only(after=after, before=before, scope="axes", gid="axes_0.lines_0")
    assert c.verdict == "unknown" and c.observed == {"elements": ["axes_1.title"], "total": 1}


def test_added_removed_and_identity_swapped_elements_are_changes():
    before = _m({**_el("axes_0.lines_0", "line", color="#111111"), "identity": "l1:a"})
    swapped = _m({**_el("axes_0.lines_0", "line", color="#111111"), "identity": "l1:b"})
    gone = _m()
    for after in (swapped, gone):
        c = _only(after=after, before=before, scope="element", gid="axes_0.title")
        assert c.verdict == "unknown" and c.observed["elements"] == ["axes_0.lines_0"]


def test_outside_list_is_capped_but_total_is_exact():
    n = 30
    before = _m(*[_el(f"axes_0.lines_{i}", "line", color="#000000") for i in range(n)])
    after = _m(*[_el(f"axes_0.lines_{i}", "line", color="#ffffff") for i in range(n)])
    c = _only(after=after, before=before, scope="element", gid="axes_0.title")
    assert len(c.observed["elements"]) == 20 and c.observed["total"] == n


def test_other_stem_has_no_target():
    before = _m(_el("axes_0.title", "title", fontsize=10))
    after = _m(_el("axes_0.title", "title", fontsize=12))
    c = _only(after=after, before=before, scope="none", gid="axes_0.title")
    assert c.verdict == "unknown" and c.observed["total"] == 1


def test_every_probe_fixture_is_covered_by_the_scenarios():
    assert {s["fixture"] for s in probe_scenarios()} <= set(FIGURE_FIXTURES)
