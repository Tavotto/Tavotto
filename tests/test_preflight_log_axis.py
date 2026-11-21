"""V-001「对数轴上有 ≤ 0 的数据画不出来」（`log-axis-nonpositive`）：真 worker 的事实 + 两侧同一条规则。

手写的 golden 向量（`tests/golden/preflight_vectors.json` 的 `log-axis-*`）只证明两个求值器对同一份
`scale_domain` 结论一致；这里证明**事实本身**是从真实 matplotlib 量出来的：§5.3 表里的每个写法都在真
worker 里渲染一次，读 manifest 里的 `scale_domain`，再交给 `preflight.run`。柱 / 直方图 / 填充 / 混合变换 /
`transAxes` / stem 基线 / 误差棒的帽这些「本来就画到 0」的东西一律不许报。

`tests/golden/scale_domain_real.json` 是真 worker 产出的 `scale_domain` 的一份快照（这里重新渲染并逐字比对），
`web/src/lib/preflight.logAxis.test.ts` 直接消费它——协议两端至少一条用例吃对方的实际输出（错题本 L-042）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, preflight, profiles

ROOT = Path(__file__).resolve().parent.parent
RULE = "log-axis-nonpositive"
REAL_SNAPSHOT = ROOT / "tests" / "golden" / "scale_domain_real.json"
FIGUREBENCH = ROOT / "tests" / "fixtures" / "figurebench"

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ------------------------------ 规则登记（不需要 worker） ------------------------------
def test_rule_is_registered_as_warn_in_all_three_severity_tables():
    """没登记的检查项兜底就是 warn，所以「结果是 warn」测不出漏登记：直接读表。"""
    raw = json.loads((ROOT / "src/tavotto/profiles/publication.json").read_text(encoding="utf-8"))
    tables = {pid: p.get("severity") or {} for pid, p in raw["profiles"].items()}
    for pid in ("lab-publication-v1", "journal-base-v1", "free-form-v1"):
        assert tables[pid].get(RULE) == "warn", pid
        assert profiles.severity_of(profiles.load(pid), RULE) == "warn", pid


# ------------------------------ 真 worker ------------------------------
CASES_SCRIPT = """\
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, axs = plt.subplots(4, 5, figsize=(12, 9), dpi=60)
    a = iter(axs.ravel())

    def nxt(name):
        ax = next(a)
        ax.set_title(name)
        return ax

    ax = nxt("normal"); ax.plot([1, 2, 3], [1, 10, 100]); ax.set_yscale("log")
    ax = nxt("line_nonpositive"); ax.plot([0, 1, 2, 3], [0, -1, 10, 100]); ax.set_yscale("log")
    ax = nxt("errorbar_low"); ax.errorbar([1, 2], [1, 2], yerr=[1.5, 0.5]); ax.set_yscale("log")
    ax = nxt("errorbar_low_capped")
    ax.errorbar([1, 2], [1, 2], yerr=[1.5, 0.5], capsize=3); ax.set_yscale("log")
    ax = nxt("nan_masked")
    ax.plot([1, 2, 3], [1, np.nan, 10]); ax.plot([1, 2], np.ma.masked_less_equal([0, 5], 0))
    ax.set_yscale("log")
    ax = nxt("symlog"); ax.plot([1, 2], [-5, 5]); ax.set_yscale("symlog")
    ax = nxt("hidden")
    ln, = ax.plot([1, 2], [0, 5]); ln.set_visible(False); ax.plot([1, 2], [1, 5]); ax.set_yscale("log")
    ax = nxt("twinx_left"); ax.plot([1, 2], [0, 5])
    tw = ax.twinx(); tw.set_title("twinx_right"); tw.plot([1, 2], [1, 10]); tw.set_yscale("log")
    ax = nxt("scatter"); ax.scatter([1, 2, 3], [-1, 2, 3]); ax.set_yscale("log")
    ax = nxt("bar"); ax.bar([0, 1], [5, 50]); ax.set_yscale("log")
    ax = nxt("hist"); ax.hist([1, 1, 2, 9], bins=5, log=True)
    ax = nxt("axvline_loglog")
    ax.plot([1, 10], [1, 10]); ax.axvline(3); ax.axhline(2); ax.set_xscale("log"); ax.set_yscale("log")
    ax = nxt("axvspan"); ax.plot([1, 10], [1, 10]); ax.axvspan(2, 3); ax.set_yscale("log")
    x = np.arange(1, 5)
    ax = nxt("fill_between"); ax.fill_between(x, 0, x ** 2); ax.plot(x, x ** 2); ax.set_yscale("log")
    ax = nxt("transaxes_line")
    ax.plot([0, 1], [0, 1], transform=ax.transAxes); ax.plot([1, 2], [1, 10]); ax.set_yscale("log")
    ax = nxt("stem"); ax.stem([1, 2], [3, 4]); ax.set_yscale("log")
    ax = nxt("vlines"); ax.vlines([1, 2], 0, [3, 4]); ax.plot([1, 2], [3, 4]); ax.set_yscale("log")
    ax = nxt("errorbar_xerr"); ax.errorbar([1, 2], [1, 2], xerr=[1.5, 0.5]); ax.set_yscale("log")
    ax = nxt("loglog_x_zero"); ax.loglog([0, 1, 2], [1, 2, 3])
    ax = nxt("linear_with_zero"); ax.plot([0, -1, 2])
    fig.savefig("LogCases.pdf")
"""

#: 标题 → 期望的 `scale_domain`（None = 键缺席）。§5.3 每一行 + 几个表外排除项（stem / vlines / 帽 / xerr）
EXPECTED = {
    "normal": {"y": (0, 0)},
    "line_nonpositive": {"y": (2, 0)},
    "errorbar_low": {"y": (0, 1)},
    "errorbar_low_capped": {"y": (0, 1)},  # 帽不算：同一个下端不数两遍
    "nan_masked": {"y": (0, 0)},
    "symlog": None,
    "hidden": {"y": (0, 0)},
    "twinx_left": None,  # 左轴线性，含 0 也与它无关
    "twinx_right": {"y": (0, 0)},
    "scatter": {"y": (1, 0)},
    "bar": {"y": (0, 0)},
    "hist": {"y": (0, 0)},
    "axvline_loglog": {"x": (0, 0), "y": (0, 0)},
    "axvspan": {"y": (0, 0)},
    "fill_between": {"y": (0, 0)},
    "transaxes_line": {"y": (0, 0)},
    "stem": {"y": (0, 0)},
    "vlines": {"y": (0, 0)},
    "errorbar_xerr": {"y": (0, 0)},
    "loglog_x_zero": {"x": (1, 0), "y": (0, 0)},
    "linear_with_zero": None,
}


def _render(tmp: Path, script_name: str, source: str, stem: str, func: str, patches=()):
    (tmp / script_name).write_text(source, encoding="utf-8")
    w = pool.one_shot(script_name, str(tmp), func)
    try:
        w.ensure_built()
        return w, w.override(stem, list(patches))["manifest"]
    except BaseException:
        pool.discard(w)
        raise


def _spec(manifest):
    w, h = manifest.get("size_mm") or (80.0, 60.0)
    rect = [0, 0, float(w), float(h)]
    panel = {
        "id": "p1",
        "name": "p1",
        "kind": "pdf",
        "rect_mm": rect,
        "scale": 1.0,
        "manifest": manifest,
        "px_w": None,
        "missing": False,
        "stale": False,
        "render_error": None,
        "unapplied_overrides": 0,
        "hidden": False,
    }
    return {
        "page": {"w_mm": rect[2], "h_mm": rect[3], "margin_mm": 0.0},
        "panels": [panel],
        "texts": [],
        "objects": [{"id": "p1", "type": "panel", "rect_mm": rect, "hidden": False}],
    }


def _hits(manifest, profile_id="lab-publication-v1"):
    return [i for i in preflight.run(_spec(manifest), profiles.load(profile_id)) if i["id"] == RULE]


def _axes_by_title(manifest) -> dict[str, dict]:
    els = {e["gid"]: e for e in manifest["elements"]}
    out = {}
    for e in manifest["elements"]:
        if e["role"] != "title":
            continue
        text = next((f.get("value") for f in e["editable"] if f.get("prop") == "text"), None)
        host = els.get(e["gid"].split(".", 1)[0])
        if text and host is not None and host["role"] == "axes":
            out[text] = host
    return out


@pytest.fixture(scope="module")
def cases_manifest(tmp_path_factory):
    w, man = _render(
        tmp_path_factory.mktemp("logaxis-cases"),
        "fig_logcases.py",
        CASES_SCRIPT,
        "LogCases",
        "main",
    )
    pool.discard(w)
    return man


@needs_worker
def test_scale_domain_facts_match_the_table_on_a_real_manifest(cases_manifest):
    axes = _axes_by_title(cases_manifest)
    assert set(axes) == set(EXPECTED), sorted(set(axes) ^ set(EXPECTED))
    for title, want in EXPECTED.items():
        got = axes[title].get("scale_domain")
        if want is None:
            assert got is None, (title, got)
            continue
        assert set(got) == set(want), (title, got)
        for dim, (points, low) in want.items():
            assert got[dim] == {
                "status": "ok",
                "nonpositive": points,
                "errorbar_low_nonpositive": low,
            }, (title, dim, got)


@needs_worker
def test_only_the_unplottable_axes_are_reported_on_a_real_manifest(cases_manifest):
    axes = _axes_by_title(cases_manifest)
    hits = _hits(cases_manifest)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["severity"] == "warn"
    want = sorted(
        axes[t]["gid"]
        for t in (
            "line_nonpositive",
            "errorbar_low",
            "errorbar_low_capped",
            "scatter",
            "loglog_x_zero",
        )
    )
    assert sorted(hit["gids"]) == want
    # 最糟的那次（合计 2，第一次出现的是 line_nonpositive 的 y）决定 detail
    assert hit["detail"] == {"axis": "y", "nonpositive": 2, "errorbar_low_nonpositive": 0}
    assert hit["message"] == {"key": "logAxisNonpositive", "params": {"count": 2, "axis": "y"}}


@needs_worker
def test_real_snapshot_for_the_frontend_is_what_the_worker_emits(cases_manifest):
    """`scale_domain_real.json` 不是手写的：这里重新渲染，逐字等于真 worker 的输出才算数。

    改了 `CASES_SCRIPT` 或事实口径后重出：`PYTHONPATH=$PWD/src python tests/test_preflight_log_axis.py --write`，
    不许手改那份 JSON。"""
    assert json.loads(REAL_SNAPSHOT.read_text(encoding="utf-8")) == _snapshot(cases_manifest)


def _snapshot(cases_manifest) -> dict:
    axes = _axes_by_title(cases_manifest)
    return {
        "comment": "真 worker 渲染 tests/test_preflight_log_axis.py 的 CASES_SCRIPT 得到的 axes 元素"
        "（只留 gid / role / scale_domain）。pytest 重渲染逐字比对；web/src/lib/preflight.logAxis.test.ts 消费它。",
        "elements": [
            {
                "title": t,
                "gid": axes[t]["gid"],
                "role": "axes",
                "scale_domain": axes[t]["scale_domain"],
            }
            for t in sorted(axes)
            if "scale_domain" in axes[t]
        ],
        "expected_gids": sorted(hit for i in _hits(cases_manifest) for hit in i["gids"]),
    }


@needs_worker
def test_over_budget_is_not_judged(tmp_path):
    """超出配额写 `over_budget`、不带计数，规则不判（「没量」不是「没有」）。在 worker 解释器里直接调事实函数。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src' / 'tavotto' / 'engine')!r})\n"
        "import matplotlib; matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import manifest as M\n"
        "fig, ax = plt.subplots(); ax.plot([0, 1, 2], [0, 1, 2]); ax.set_yscale('log')\n"
        "fig.canvas.draw()\n"
        "budget = [2]\n"
        "small = M.scale_domain(ax, budget)\n"
        "full = M.scale_domain(ax, [M.FINGERPRINT_BUDGET])\n"
        "print(json.dumps({'small': small, 'left': budget[0], 'full': full,"
        " 'statuses': list(M.SCALE_DOMAIN_STATUSES)}))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(probe)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["small"] == {"y": {"status": "over_budget"}}
    assert out["left"] == 2  # 没量就不扣
    # status 闭集与 TS 侧 `ScaleDomainFact.status` 的字面值一致（同源对，见 same-origin-pairs.md）
    assert out["statuses"] == ["ok", "over_budget", "unavailable"]
    ts = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "status: 'ok' | 'over_budget' | 'unavailable'" in ts
    assert out["full"] == {"y": {"status": "ok", "nonpositive": 1, "errorbar_low_nonpositive": 0}}
    man = {
        "stem": "x",
        "size_mm": [80.0, 60.0],
        "elements": [
            {
                "gid": "axes_0",
                "role": "axes",
                "label": "axes_0",
                "draggable": False,
                "bbox": [0.1, 0.1, 0.8, 0.8],
                "editable": [],
                "scale_domain": out["small"],
            }
        ],
    }
    assert _hits(man) == []


EDIT_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot([0, 1, 2, 3], [0.0, 1.0, 4.0, 9.0], label="y")
    fig.savefig("LogEdit.pdf")
"""


@needs_worker
def test_switching_to_log_in_the_inspector_brings_the_issue_and_undo_clears_it(tmp_path):
    """编辑引入的问题：override `yscale→log` 后事实立刻出现、规则报；撤销（空 override 列表）后消失。"""
    w, before = _render(tmp_path, "fig_logedit.py", EDIT_SCRIPT, "LogEdit", "main")
    try:
        assert "scale_domain" not in next(e for e in before["elements"] if e["gid"] == "axes_0")
        assert _hits(before) == []
        logged = w.override("LogEdit", [{"gid": "axes_0", "prop": "yscale", "value": "log"}])[
            "manifest"
        ]
        ax = next(e for e in logged["elements"] if e["gid"] == "axes_0")
        assert ax["scale_domain"] == {
            "y": {"status": "ok", "nonpositive": 1, "errorbar_low_nonpositive": 0}
        }
        hits = _hits(logged)
        assert len(hits) == 1 and hits[0]["gids"] == ["axes_0"]
        undone = w.override("LogEdit", [])["manifest"]
        assert "scale_domain" not in next(e for e in undone["elements"] if e["gid"] == "axes_0")
        assert _hits(undone) == []
    finally:
        pool.discard(w)


def _figurebench(tmp: Path, name: str):
    """夹具在临时副本里执行（FigureBench 纪律：本目录逐字节不变）。"""
    dst = tmp / name
    shutil.copytree(FIGUREBENCH / name, dst)
    w = pool.one_shot("figure.py", str(dst), "main")
    try:
        w.ensure_built()
    except BaseException:
        pool.discard(w)
        raise
    return w


@needs_worker
def test_figurebench_line_chart_with_zero_x_flags_after_xscale_log_and_clears_on_undo(tmp_path):
    """figurebench 线图 `line_two_series` 的 x 从 0 起：切 `xscale→log` 报，撤销后消失；`yscale→log`（y 全正）不报。"""
    w = _figurebench(tmp_path, "line_two_series")
    try:
        assert _hits(w.override("line_two_series", [])["manifest"]) == []
        xlog = w.override("line_two_series", [{"gid": "axes_0", "prop": "xscale", "value": "log"}])
        hits = _hits(xlog["manifest"])
        assert len(hits) == 1
        assert hits[0]["detail"] == {"axis": "x", "nonpositive": 2, "errorbar_low_nonpositive": 0}
        ylog = w.override("line_two_series", [{"gid": "axes_0", "prop": "yscale", "value": "log"}])
        assert _hits(ylog["manifest"]) == []
        assert _hits(w.override("line_two_series", [])["manifest"]) == []
    finally:
        pool.discard(w)


@needs_worker
def test_figurebench_log_decay_is_not_flagged(tmp_path):
    """真图防误报：y 为 log 且全正，x 含 0 但 x 是线性轴。"""
    w = _figurebench(tmp_path, "log_decay")
    try:
        man = w.override("log_decay", [])["manifest"]
    finally:
        pool.discard(w)
    ax = next(e for e in man["elements"] if e["gid"] == "axes_0")
    assert ax["scale_domain"] == {
        "y": {"status": "ok", "nonpositive": 0, "errorbar_low_nonpositive": 0}
    }
    for pid in ("lab-publication-v1", "journal-base-v1", "free-form-v1"):
        assert _hits(man, pid) == [], pid


if __name__ == "__main__":
    import sys
    import tempfile

    if sys.argv[1:] != ["--write"]:
        raise SystemExit(
            "用法：PYTHONPATH=$PWD/src python tests/test_preflight_log_axis.py --write"
        )
    _w, _man = _render(
        Path(tempfile.mkdtemp()), "fig_logcases.py", CASES_SCRIPT, "LogCases", "main"
    )
    pool.discard(_w)
    REAL_SNAPSHOT.write_text(
        json.dumps(_snapshot(_man), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"已写入 {REAL_SNAPSHOT}")
