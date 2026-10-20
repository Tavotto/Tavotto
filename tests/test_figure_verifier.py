"""TAF Figure Verifier（`engine/figureverify.py`）与 manifest 数据指纹（`engine/manifest.py::data_fingerprint`），ADR 0138 Phase 2。

主语：**Tavotto 自己的 worker 产出的 manifest**——不是原生 matplotlib、不是 Agent 的自述。FigureBench 的每个探针场景都在
临时副本里经 `pool.one_shot()` 真实构建三类变体（基线 / 参考解 / 反例），验证器在「改前 = 基线、改后 = 变体」上判：

* 参考解：全部目标与保持断言 verified（manifest 说不出的那几类是 unknown，并且**只**能是它们）；
* 每个反例：它点名的 `must_fail` 断言被判 failed——验证器不是恒真的；
* manifest 的数据指纹与原生探针从同一份脚本读出的摘要逐字节相同（两条独立的路径量到了同一个东西）。

验证器看不见的反例（manifest 不带的事实）登记在 `KNOWN_BLIND`，**精确匹配**：盲区被补上之后这里会红，提醒删掉登记。
本进程不 import matplotlib：worker 与探针都跑在 `pool.find_worker_python()` 找到的科学栈解释器里，找不到就 skip（not_run）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from support.figurebench import FIGURE_FIXTURES, probe_scenarios, run_probe, stage, variants
from tavotto.engine import figureverify, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

SRC = Path(__file__).resolve().parents[1] / "src"
ENGINE = SRC / "tavotto" / "engine"

#: 验证器量不到、因而抓不住的反例：(场景, 反例) → 原因。精确匹配，见模块说明
KNOWN_BLIND = {
    ("FB-C04", "n.unshared"): "shared_x：manifest 不带 axes 之间的共享关系",
}


def _manifest(script: Path, stem: str) -> dict:
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
    work = tmp_path_factory.mktemp("verifier")
    staged = [
        (job_id, fixture, stage(work, job_id, fixture, edits, sid))
        for job_id, fixture, edits, sid in variants()
    ]

    def build(item):
        job_id, fixture, script = item
        try:
            return job_id, _manifest(script, fixture)
        except Exception as exc:  # noqa: BLE001 —— 构建失败本身就是一条要报出来的事实
            return job_id, {"__error__": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=4) as ex:
        manifests = dict(ex.map(build, staged))
    probe = run_probe(
        WORKER_PY,
        [
            {"id": job_id, "script": str(script)}
            for job_id, _, script in staged
            if job_id.startswith("base__")
        ],
        work,
    )
    return manifests, probe


def _ok(manifests: dict, job_id: str) -> dict:
    man = manifests[job_id]
    assert "__error__" not in man, f"{job_id} 构建失败：{man['__error__']}"
    return man


# --------------------------------------------------------------------------- 指纹 ↔ 探针
@needs_worker
@pytest.mark.parametrize("fixture", FIGURE_FIXTURES)
def test_manifest_fingerprint_matches_the_native_probe(built, fixture):
    manifests, probe = built
    man = _ok(manifests, f"base__{fixture}")
    facts = probe[f"base__{fixture}"]["facts"]
    els = {e["gid"]: e for e in man["elements"]}
    compared = 0
    for i, ax in enumerate(facts["axes"]):
        for line in ax["lines"]:
            (el,) = [
                e
                for g, e in els.items()
                if g.startswith(f"axes_{i}.")
                and e["role"] == "line"
                and figureverify._label_of(e) == line["label"]
            ]
            assert el["data_fingerprint"]["parts"]["xy"]["sha256"] == line["data"]["sha256"], (
                fixture,
                line["label"],
            )
            compared += 1
        for j, eb in enumerate(ax["errorbars"]):
            got = els[f"axes_{i}.errorbar_{j}"]["data_fingerprint"]["parts"]["points"]["sha256"]
            assert got == eb["points"]["sha256"], fixture
            compared += 1
        for j, im in enumerate(ax["images"]):
            got = els[f"axes_{i}.images_{j}"]["data_fingerprint"]["parts"]["array"]["sha256"]
            assert got == im["data"]["sha256"], fixture
            compared += 1
    if (
        fixture != "grouped_bars"
    ):  # 柱组的指纹是（底, 高）对，探针记的是高度，形状不同，不在这里对拍
        assert compared, f"{fixture} 一条都没对拍上"


# --------------------------------------------------------------------------- 场景
def _expected_ref_verdict(a: dict) -> str:
    return "unknown" if a["kind"] in figureverify.NOT_OBSERVABLE else "verified"


@needs_worker
@pytest.mark.parametrize("scenario", probe_scenarios(), ids=lambda s: s["id"])
def test_reference_solution_is_verified(built, scenario):
    manifests, _ = built
    base = _ok(manifests, f"base__{scenario['fixture']}")
    ref = _ok(manifests, f"{scenario['id']}__ref")
    for c in figureverify.verify(scenario["goal"], scenario["preserve"], ref, base):
        want = _expected_ref_verdict(
            {
                "kind": next(
                    a["kind"] for a in scenario["goal"] + scenario["preserve"] if a["id"] == c.id
                )
            }
        )
        assert c.verdict == want, f"{scenario['id']}: {c.id} 判成 {c.verdict}（{c.reason_code}）"


@needs_worker
@pytest.mark.parametrize("scenario", probe_scenarios(), ids=lambda s: s["id"])
def test_baseline_does_not_already_satisfy_the_goal(built, scenario):
    if not scenario["goal"]:
        pytest.skip("主观场景：无目标断言")
    manifests, _ = built
    base = _ok(manifests, f"base__{scenario['fixture']}")
    checks = figureverify.verify(scenario["goal"], [], base, base)
    assert not all(c.verdict == "verified" for c in checks), scenario["id"]


@needs_worker
@pytest.mark.parametrize("scenario", probe_scenarios(), ids=lambda s: s["id"])
def test_every_negative_variant_is_caught_or_registered_blind(built, scenario):
    manifests, _ = built
    base = _ok(manifests, f"base__{scenario['fixture']}")
    by_id = {a["id"]: a for a in scenario["goal"] + scenario["preserve"]}
    for n in scenario["negative_variants"]:
        key = (scenario["id"], n["id"])
        man = _ok(manifests, f"{scenario['id']}__{n['id']}")
        verdicts = {
            aid: figureverify.check(by_id[aid], man, base, role="preserve").verdict
            for aid in n["must_fail"]
        }
        if key in KNOWN_BLIND:
            assert "failed" not in verdicts.values(), (
                f"{key} 已经抓得住了：从 KNOWN_BLIND 删掉这条登记"
            )
            assert set(verdicts.values()) == {"unknown"}, (key, verdicts)
            continue
        assert all(v == "failed" for v in verdicts.values()), f"{key} 没抓住：{verdicts}"


def test_known_blind_list_only_names_real_negatives():
    real = {(s["id"], n["id"]) for s in probe_scenarios() for n in s["negative_variants"]}
    assert set(KNOWN_BLIND) <= real


# --------------------------------------------------------------------------- L3 版式回归
@needs_worker
def test_layout_regressions_only_counts_problems_this_edit_introduced(built, tmp_path):
    manifests, _ = built
    base = _ok(manifests, "base__line_two_series")
    ok = figureverify.layout_regressions(_ok(manifests, "FB-S03__ref"), base)
    assert ok.verdict == "verified", ok.observed
    huge = stage(
        tmp_path,
        "huge_title",
        "line_two_series",
        [
            {
                "file": "figure.py",
                "find": '"Response", fontsize=10',
                "replace": '"Response " * 12, fontsize=30',
                "count": 1,
            }
        ],
        "huge",
    )
    bad = figureverify.layout_regressions(_manifest(huge, "line_two_series"), base)
    assert bad.verdict == "failed" and bad.reason_code == "new_layout_issues", bad.observed
    assert figureverify.layout_regressions(base, base).verdict == "verified", (
        "改前就有的问题不算这次的账"
    )


# --------------------------------------------------------------------------- 判定规则（合成 manifest，不需要 worker）
def _line(label, sha="a" * 64, status="ok", gid="axes_0.lines_0"):
    fp = (
        {"status": status}
        if status != "ok"
        else {"status": "ok", "parts": {"xy": {"sha256": sha, "shape": [3, 2]}}}
    )
    return {
        "gid": gid,
        "role": "line",
        "editable": [{"prop": "label", "value": label}],
        "data_fingerprint": fp,
    }


def _man(*els):
    return {
        "size_mm": [80, 60],
        "elements": [{"gid": "figure", "role": "figure", "editable": []}, *els],
    }


SERIES = {"id": "p", "kind": "series_data_unchanged", "axes": 0, "series": "s"}


def test_removed_subject_is_failed_but_never_seen_subject_is_unknown():
    before = _man(_line("s"))
    assert figureverify.check(SERIES, _man(), before, role="preserve").verdict == "failed"
    assert figureverify.check(SERIES, _man(), _man(), role="preserve").verdict == "unknown"


def _axes(i, xscale="linear"):
    return {
        "gid": f"axes_{i}",
        "role": "axes",
        "editable": [{"prop": "xscale", "value": xscale}, {"prop": "yscale", "value": "linear"}],
    }


def _label(i, which="xlabel"):
    return {
        "gid": f"axes_{i}.{which}",
        "role": "text",
        "editable": [{"prop": "text", "value": "t"}],
        "bbox": [0.1, 0.9, 0.1, 0.05],
    }


_AXIS_KIND_ASSERTIONS = [
    {"id": "a", "kind": "axis_scale", "axes": 1, "axis": "x", "expected": "linear"},
    {"id": "b", "kind": "axis_scale_unchanged", "axes": 1, "axis": "x"},
    {
        "id": "c",
        "kind": "label_alignment",
        "axes": [0, 1],
        "which": "xlabel",
        "edge": "x0",
        "tol": 0.01,
    },
]


@pytest.mark.parametrize("a", _AXIS_KIND_ASSERTIONS, ids=lambda a: a["kind"])
def test_axis_scoped_kinds_register_their_subject(a):
    before = _man(_axes(0), _label(0), _axes(1), _label(1))
    after = _man(_axes(0), _label(0))  # 改动删掉了 axes_1（连同它的标签）
    c = figureverify.check(a, after, before, role="preserve")
    assert (c.verdict, c.reason_code) == ("failed", "subject_removed")
    # 改前就没有这个轴：量不了，不是被删
    c = figureverify.check(a, _man(_axes(0), _label(0)), _man(_axes(0), _label(0)), role="preserve")
    assert (c.verdict, c.reason_code) == ("unknown", "subject_not_found")


def _with_identity(el: dict, ident: str | None) -> dict:
    return {**el, "identity": ident} if ident is not None else dict(el)


@pytest.mark.parametrize("kind", ["axis_scale", "axis_scale_unchanged"])
def test_renumbered_axes_do_not_stand_in_for_a_deleted_axes(kind):
    a = {"id": "a", "kind": kind, "axes": 0, "axis": "x", "expected": "linear"}
    before = _man(_with_identity(_axes(0), "l1:aaa"), _with_identity(_axes(1), "l1:bbb"))
    # 删掉第一个子图：原 axes_1 重编号成 axes_0，身份是 bbb
    after = _man(_with_identity(_axes(0), "l1:bbb"))
    c = figureverify.check(a, after, before, role="preserve")
    assert (c.verdict, c.reason_code) == ("failed", "subject_removed")
    # 同一个对象（身份没变）：照常 verified
    same = _man(_with_identity(_axes(0), "l1:aaa"))
    assert figureverify.check(a, same, before, role="preserve").verdict == "verified"
    # 任一侧缺身份、轴的个数又变了：按位置得到的「成立」不可信 → unknown（默认 plt.subplots 的轴没有 identity）
    for b, af in (
        (_man(_axes(0), _axes(1)), after),
        (before, _man(_axes(0))),
        (_man(_axes(0), _axes(1)), _man(_axes(0))),
    ):
        c = figureverify.check(a, af, b, role="preserve")
        assert (c.verdict, c.reason_code) == ("unknown", "subject_renumbered")
    # 缺身份但轴数没变：没有重编号，照常按位置
    two = _man(_axes(0), _axes(1))
    assert figureverify.check(a, two, two, role="preserve").verdict == "verified"


def test_renumbered_series_do_not_stand_in_for_a_deleted_series():
    # 同 label、同 gid，但身份不同 = 不是同一条曲线（比如被删后另一个子图里的同名曲线顶替）
    before = _man(_with_identity(_line("s"), "l1:aaa"))
    other = _man(_with_identity(_line("s"), "l1:bbb"))
    c = figureverify.check(SERIES, other, before, role="preserve")
    assert (c.verdict, c.reason_code) == ("failed", "subject_removed")
    same = _man(_with_identity(_line("s"), "l1:aaa"))
    assert figureverify.check(SERIES, same, before, role="preserve").verdict == "verified"
    assert (
        figureverify.check(SERIES, _man(_line("s")), before, role="preserve").verdict == "verified"
    )


def test_fig_size_kinds_register_the_figure_as_subject():
    a = {"id": "f", "kind": "fig_size_unchanged", "dim": "width"}
    before = _man()
    before["elements"][0]["editable"] = [{"prop": "size_mm", "value": [80, 60]}]
    after = {"elements": []}
    c = figureverify.check(a, after, before, role="preserve")
    assert (c.verdict, c.reason_code) == ("failed", "subject_removed")


def test_data_change_is_failed_and_same_data_is_verified():
    before = _man(_line("s", sha="a" * 64))
    assert (
        figureverify.check(SERIES, _man(_line("s", sha="a" * 64)), before, role="preserve").verdict
        == "verified"
    )
    assert (
        figureverify.check(SERIES, _man(_line("s", sha="b" * 64)), before, role="preserve").verdict
        == "failed"
    )


@pytest.mark.parametrize("status", ["over_budget", "unavailable"])
def test_unmeasured_fingerprint_is_unknown_not_verified(status):
    before = _man(_line("s"))
    c = figureverify.check(SERIES, _man(_line("s", status=status)), before, role="preserve")
    assert c.verdict == "unknown" and c.reason_code == "not_observable"


def test_unregistered_kind_and_not_observable_kinds_are_unknown():
    m = _man(_line("s"))
    assert (
        figureverify.check({"id": "x", "kind": "made_up"}, m, m, role="goal").verdict == "unknown"
    )
    for kind in figureverify.NOT_OBSERVABLE:
        assert (
            figureverify.check(
                {"id": "x", "kind": kind, "axes": [0, 1]}, m, m, role="preserve"
            ).verdict
            == "unknown"
        )


def test_verifier_kinds_are_exactly_the_figurebench_kinds():
    from test_figurebench_fixtures import ASSERTION_KINDS

    assert set(figureverify.KINDS) == ASSERTION_KINDS


# --------------------------------------------------------------------------- worker 侧指纹的单元判据
_FP_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import manifest

fig, ax = plt.subplots()
a, = ax.plot([0, 1, 2], [1.0, np.nan, 3.0])
im_nan = ax.imshow([[1.0, np.nan], [3.0, 4.0]])
im_masked = ax.imshow(np.ma.masked_array([[1.0, 2.0], [3.0, 4.0]], mask=[[0, 1], [0, 0]]))
big, = ax.plot(range(5), range(5))
c, = ax.plot([0, 1, 2], [1.0, 2.0, 3.0])
fig3 = plt.figure()
ax3 = fig3.add_subplot(projection="3d")
l3, = ax3.plot([0, 1], [0, 1], [5, 6])
from matplotlib.image import AxesImage, NonUniformImage


class MyImage(AxesImage):
    pass


plain = ax.imshow([[1.0, 2.0], [3.0, 4.0]])
sub = MyImage(ax)
sub.set_data(np.array([[1.0, 2.0], [3.0, 4.0]]))
ax.add_image(sub)
nu = NonUniformImage(ax)
nu.set_data([0.0, 1.0], [0.0, 1.0], np.array([[1.0, 2.0], [3.0, 4.0]]))
ax.add_image(nu)
budget = [8]
out = {
    "image_plain": manifest.data_fingerprint(plain, [100]),
    "image_subclass": manifest.data_fingerprint(sub, [100]),
    "image_nonuniform": manifest.data_fingerprint(nu, [100]),
    "nan": manifest.data_fingerprint(a, [100]),
    "image_nan": manifest.data_fingerprint(im_nan, [100]),
    "image_masked": manifest.data_fingerprint(im_masked, [100]),
    "big": manifest.data_fingerprint(big, budget),
    "small": manifest.data_fingerprint(c, budget),
    "budget_left": budget[0],
    "line3d": manifest.data_fingerprint(l3, [100]),
    "text": manifest.data_fingerprint(ax.title, [100]),
}
print(json.dumps(out))
"""


@needs_worker
def test_fingerprint_rules_in_the_worker_interpreter():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "MPL"))}
    proc = subprocess.run(
        [WORKER_PY, "-B", "-c", _FP_PROBE, str(ENGINE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["nan"]["parts"]["xy"]["finite"] == 5
    # 掩码值与 NaN 是同一种「缺失」：同一位置缺失的两张图像指纹相同（掩码下面藏的 2.0 不进指纹）
    assert out["image_masked"]["parts"]["array"] == out["image_nan"]["parts"]["array"]
    assert out["image_nan"]["parts"]["array"]["finite"] == 3
    # 子类（用户自定义 / NonUniformImage）与普通 AxesImage 同样量得到，且同数组摘要相同
    assert out["image_subclass"]["status"] == "ok"
    assert out["image_subclass"]["parts"]["array"] == out["image_plain"]["parts"]["array"]
    assert out["image_nonuniform"]["status"] == "ok"
    assert set(out["image_nonuniform"]["parts"]) == {"array"}
    # 超预算的那一个（10 个数 > 8）不扣配额：排在它后面、装得下的那一个照样量（6 个数）
    assert out["big"] == {"status": "over_budget"} and out["small"]["status"] == "ok"
    assert out["budget_left"] == 2
    assert set(out["line3d"]["parts"]) == {"xyz"} and out["line3d"]["parts"]["xyz"]["shape"] == [
        2,
        3,
    ]
    assert out["text"] is None, "文字不是数据类元素，不出指纹"


def test_verifier_imports_without_the_scientific_stack():
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in {'numpy', 'matplotlib', 'pandas', 'pymupdf', 'fitz'}:\n"
        "            raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import tavotto.engine.figureverify as v\n"
        "print(len(v.KINDS))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


# --------------------------------------------------------------------------- 默认保持断言「数据不变」（figure_edit，Phase 3b）
_DATA_KINDS = {
    "series_data_unchanged",
    "errorbar_data_unchanged",
    "errorbar_points_unchanged",
    "image_data_unchanged",
}


@needs_worker
@pytest.mark.parametrize("scenario", probe_scenarios(), ids=lambda s: s["id"])
def test_data_unchanged_agrees_with_the_benchmark(built, scenario):
    """真 worker manifest 上对拍：参考解（只改样式 / 版式）不触发 `data_changed`；凡是登记了「数据类断言必须失败」的反例，
    `data_unchanged` 至少报一条 failed——默认保持断言抓得住基准里每一个改数据的反例。"""
    manifests, _ = built
    base = _ok(manifests, f"base__{scenario['fixture']}")
    ref = _ok(manifests, f"{scenario['id']}__ref")
    by_id = {a["id"]: a for a in scenario["goal"] + scenario["preserve"]}
    if not any(a["kind"] in _DATA_KINDS for a in scenario["goal"]):
        bad = [c.id for c in figureverify.data_unchanged(ref, base) if c.verdict == "failed"]
        assert bad == [], f"{scenario['id']} 参考解被判改了数据：{bad}"
    for n in scenario["negative_variants"]:
        if not any(by_id[aid]["kind"] in _DATA_KINDS for aid in n["must_fail"]):
            continue
        man = _ok(manifests, f"{scenario['id']}__{n['id']}")
        verdicts = {c.id: c.verdict for c in figureverify.data_unchanged(man, base)}
        assert "failed" in verdicts.values(), (
            f"{scenario['id']}/{n['id']} 改了数据却没抓住：{verdicts}"
        )


def test_data_unchanged_rules_on_synthetic_manifests():
    before = _man(_line("s", sha="a" * 64))
    [same] = figureverify.data_unchanged(_man(_line("s", sha="a" * 64)), before)
    assert (same.id, same.verdict, same.role, same.layer) == (
        "data.axes_0.lines_0",
        "verified",
        "preserve",
        "L2",
    )
    [changed] = figureverify.data_unchanged(_man(_line("s", sha="b" * 64)), before)
    assert (changed.verdict, changed.reason_code, changed.observed) == (
        "failed",
        "data_changed",
        {"parts": ["xy"]},
    )
    [gone] = figureverify.data_unchanged(_man(), before)
    assert (gone.verdict, gone.reason_code) == ("failed", "subject_removed")
    [swapped] = figureverify.data_unchanged(
        _man(_with_identity(_line("s"), "l1:bbb")), _man(_with_identity(_line("s"), "l1:aaa"))
    )
    assert (swapped.verdict, swapped.reason_code) == ("failed", "subject_removed")
    for b, a in (
        (_man(_line("s", status="over_budget")), before),
        (before, _man(_line("s", status="unavailable"))),
    ):
        [c] = figureverify.data_unchanged(a, b)
        assert (c.verdict, c.reason_code) == ("unknown", "not_measured")
    # 改后新出现的数据元素不判（新增一条曲线可以就是目标）；改前没有数据元素 → 空
    assert (
        figureverify.data_unchanged(_man(_line("s"), _line("t", gid="axes_0.lines_1")), before)[
            0
        ].verdict
        == "verified"
    )
    assert (
        len(figureverify.data_unchanged(_man(_line("s"), _line("t", gid="axes_0.lines_1")), before))
        == 1
    )
    assert figureverify.data_unchanged(_man(_line("s")), _man()) == []
