"""排版与按规范修**不改数据**：一键排版会写的那几类键应用之后，每个数据元素的 `data_fingerprint` 逐个相等。

科学可视化理论体系方案 §4 的 E2（`docs/sciviz/principles.md` P4「缺的是一条测试不变量」）里，一键排版（ADR 0199）
用得到的那一部分：`figure.size_mm`、axes 的 `position`，以及按规范修会动的字号 / 线宽。主语是**数据数组**，不是
画面——改图幅后像素全变了，数据一个数都不该变。

夹具是一张有真实科研图结构的图（不等宽网格、twin 轴、inset、色条、对数轴、误差线、柱、填充带、NaN、超长标签、
数学上下标），刻意做小：`over_budget` / `unavailable` 的元素算「没量」，本用例要求一个都不出现——没量不能算没变。

反证写在用例里（`test_the_comparison_notices_one_changed_value`）：同一张图只改一个数据点，比较函数必须说得出是哪个元素。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_layout_data.py"
ENTRY = "main"
STEM = "LayoutData"

#: `{peak}` 是反证用的那一个数据点
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    rng = np.random.RandomState(7)
    x = np.linspace(0.5, 8.0, 16)
    fig = plt.figure(figsize=(7.0, 4.6))
    gs = fig.add_gridspec(2, 3, width_ratios=[2.0, 1.0, 1.3], height_ratios=[1.0, 1.2])

    # a：跨两列的主图——线（含 NaN）、误差线、填充带、twin 轴、inset
    a = fig.add_subplot(gs[0, :2])
    y = np.sin(x) + 2.0
    y[5] = np.nan
    y[9] = {peak}
    a.plot(x, y, marker="o", label="signal")
    a.errorbar(x, np.cos(x) + 3.5, yerr=0.2 + 0.05 * x, fmt="s", capsize=2, label="mean ± s.d.")
    a.fill_between(x, 0.4, 0.9 + 0.1 * x, alpha=0.3, label="band")
    a.set_xlabel(r"Concentration of the very long reagent name ($\\mu$mol L$^{{-1}}$)")
    a.set_ylabel(r"$R^2$ of $x_{{i}}$")
    a.legend(loc="upper left", fontsize=6)
    t = a.twinx()
    t.plot(x, 40.0 + 3.0 * x, color="C3", linestyle="--")
    t.set_ylabel("Temperature (K)")
    inset = a.inset_axes([0.62, 0.08, 0.3, 0.32])
    inset.scatter(x[:6], y[:6] * 0.5, s=8)

    # b：对数轴 + 散点（颜色映射）
    b = fig.add_subplot(gs[0, 2])
    b.set_yscale("log")
    b.scatter(x, 10.0 ** (x / 2.0), c=x, s=14)
    b.set_xlabel("dose")

    # c：分组柱 + 误差线，类别很多、标签很长
    c = fig.add_subplot(gs[1, 0])
    names = ["control", "treatment A (10 nM)", "treatment B (100 nM)", "washout"]
    c.bar(np.arange(4) - 0.2, [3.1, 4.4, 5.2, 3.3], 0.4, yerr=[0.3, 0.4, 0.2, 0.5], label="day 1")
    c.bar(np.arange(4) + 0.2, [2.9, 4.9, 6.0, 3.0], 0.4, yerr=[0.2, 0.3, 0.6, 0.4], label="day 7")
    c.set_xticks(np.arange(4))
    c.set_xticklabels(names, rotation=30, ha="right")

    # d：热图 + 色条
    d = fig.add_subplot(gs[1, 1])
    im = d.imshow(rng.rand(6, 5), aspect="auto")
    fig.colorbar(im, ax=d, label="z-score")

    # e：直方图 + 一个离群值
    e = fig.add_subplot(gs[1, 2])
    vals = np.concatenate([rng.normal(0.0, 1.0, 60), [9.5]])
    e.hist(vals, bins=12)
    e.set_xlabel("residual")

    fig.savefig("LayoutData.pdf")
"""


def _library(tmp_path_factory, name: str, peak: str):
    figs = tmp_path_factory.mktemp(name)
    (figs / SCRIPT_NAME).write_text(LIBRARY.format(peak=peak), encoding="utf-8")
    return figs


def _manifest(worker, patches=()):
    resp = worker.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _fingerprints(manifest: dict) -> dict[str, dict]:
    return {
        e["gid"]: e["data_fingerprint"]
        for e in manifest.get("elements") or []
        if "data_fingerprint" in e
    }


def _differences(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    """哪些元素的数据不一样了。「没量」（over_budget / unavailable）单独报——没量不能算没变。"""
    out = []
    for gid in sorted(set(before) | set(after)):
        a, b = before.get(gid), after.get(gid)
        if a is None or b is None:
            out.append(f"{gid}: 元素{'消失' if b is None else '新增'}")
        elif a.get("status") != "ok" or b.get("status") != "ok":
            out.append(f"{gid}: 没量（{a.get('status')} → {b.get('status')}）")
        elif a["parts"] != b["parts"]:
            changed = sorted(
                k
                for k in set(a["parts"]) | set(b["parts"])
                if a["parts"].get(k) != b["parts"].get(k)
            )
            out.append(f"{gid}: 数据变了（{', '.join(changed)}）")
    return out


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    w = pool.one_shot(SCRIPT_NAME, str(_library(tmp_path_factory, "layout-data", "2.75")), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


@pytest.fixture(scope="module")
def base(worker):
    return _manifest(worker)


def _editable(manifest: dict, prop: str) -> list[tuple[str, object]]:
    return [
        (e["gid"], ed.get("value"))
        for e in manifest.get("elements") or []
        for ed in e.get("editable") or []
        if ed.get("prop") == prop
    ]


def test_the_fixture_is_fully_measured_and_covers_the_data_families(base):
    """夹具自检：数据元素都量到了，而且真的有线 / 误差线 / 柱 / 填充 / 散点 / 图像这几族——否则下面的「不变」是空话。"""
    fps = _fingerprints(base)
    assert fps and all(fp.get("status") == "ok" for fp in fps.values()), fps
    parts = {name for fp in fps.values() for name in fp["parts"]}
    assert {"xy", "points", "bars", "offsets", "array", "paths"} <= parts, parts
    assert len(fps) >= 12, sorted(fps)


def test_figure_size_and_axes_position_keep_every_fingerprint(worker, base):
    """一键排版写的两类键（ADR 0199 §五）：改图幅、挪坐标轴。数据一个数都不变。"""
    before = _fingerprints(base)
    positions = _editable(base, "position")
    assert len(positions) >= 4, "夹具里应当有多个可挪的坐标轴"
    w, h = base["size_mm"]
    patches = [
        {"gid": "figure", "prop": "size_mm", "value": [round(w * 0.62, 3), round(h * 0.8, 3)]}
    ]
    for gid, value in positions:
        x, y, pw, ph = value
        patches.append(
            {
                "gid": gid,
                "prop": "position",
                "value": [
                    round(x + 0.013, 4),
                    round(y + 0.021, 4),
                    round(pw * 0.9, 4),
                    round(ph * 0.88, 4),
                ],
            }
        )
    after_manifest = _manifest(worker, patches)
    assert after_manifest["size_mm"] != base["size_mm"]  # 落点：图幅真的改了，不是空操作
    moved = dict(_editable(after_manifest, "position"))
    assert all(moved[gid] != value for gid, value in positions)  # 落点：轴真的挪了
    assert _differences(before, _fingerprints(after_manifest)) == []
    # 撤回之后同样不变
    assert _differences(before, _fingerprints(_manifest(worker, []))) == []


@pytest.mark.parametrize("prop", ["fontsize", "linewidth"])
def test_spec_style_keys_keep_every_fingerprint(worker, base, prop):
    """按规范修会动的字号与线宽（specfix 的改动落在这两类键上）：整张图每个可改的元素都改一遍。"""
    before = _fingerprints(base)
    targets = [
        (gid, v)
        for gid, v in _editable(base, prop)
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert len(targets) >= 5, (prop, targets)
    patches = [
        {"gid": gid, "prop": prop, "value": round(float(v) * 1.6 + 0.5, 2)} for gid, v in targets
    ]
    after = _manifest(worker, patches)
    changed = dict(_editable(after, prop))
    assert sum(1 for gid, v in targets if changed.get(gid) != v) >= 5  # 落点：值真的写进去了
    assert _differences(before, _fingerprints(after)) == []
    _manifest(worker, [])


def test_the_comparison_notices_one_changed_value(tmp_path_factory, base):
    """反证：同一个脚本只改一个数据点（2.75 → 2.76），比较函数必须指出是哪条线——它不是一个恒真的断言。"""
    other = pool.one_shot(
        SCRIPT_NAME, str(_library(tmp_path_factory, "layout-data-mutated", "2.76")), ENTRY
    )
    try:
        other.ensure_built()
        diff = _differences(_fingerprints(base), _fingerprints(_manifest(other)))
    finally:
        pool.discard(other)
    assert len(diff) == 1 and "数据变了（xy）" in diff[0], diff


def test_unmeasured_elements_are_reported_not_treated_as_unchanged():
    ok = {"status": "ok", "parts": {"xy": {"sha256": "a"}}}
    assert _differences({"l": ok}, {"l": {"status": "over_budget"}}) == [
        "l: 没量（ok → over_budget）"
    ]
    assert _differences({"l": {"status": "unavailable"}}, {"l": {"status": "unavailable"}}) != []
    assert _differences({"l": ok}, {}) == ["l: 元素消失"]
