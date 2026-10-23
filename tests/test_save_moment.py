"""保存瞬间快照（ADR 0139 §九）：脚本 `savefig` 之后又改了 Figure，可编辑基础用**保存那一刻**的复制品。

三件要钉死的事：
1. 还原出的 Figure 与「脚本到保存那一行就结束」的参照在像素上逐字节一致（才配叫「保存瞬间」）；
2. 拍不了 / 还原不了一律退回最终态，原因落在闭集里（`save_moment_unavailable`），行为与 #1250 相同；
3. 热会话与写回的一次性重放（同一份脚本、同一个 worker 代码路径）看到同一个 Figure——写回事务不变式。
"""

from __future__ import annotations

import ast
import filecmp
import io
import json
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from tavotto.engine import figcapture, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"
)

ENGINE = Path(figcapture.__file__).resolve().parent


def test_manifest_reads_the_same_marker_name_as_figcapture():
    """manifest 不 import figcapture，两边各写一个字面量：钉住相等（同源对）。"""
    tree = ast.parse((ENGINE / "manifest.py").read_text(encoding="utf-8"))
    value = next(
        n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SAVE_MOMENT_ATTR" for t in n.targets)
    )
    assert value == figcapture.SAVE_MOMENT


HEAD = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main():
    fig, ax = plt.subplots(figsize=(2.4, 1.8))
    line, = ax.plot([0, 1, 2], [0, 1, 4], label="a")
    ax.legend()
    ax.set_title("saved")
"""

#: 参照：脚本在保存那一行就结束——它跑完的最终态就是保存瞬间
REFERENCE = HEAD + '    fig.savefig("fig.png")\n'

MUTATIONS = {
    "title_and_color": '    ax.set_title("mutated")\n    line.set_color("red")\n',
    "add_line": '    ax.plot([0, 1, 2], [4, 1, 0], color="g")\n',
    "figsize": "    fig.set_size_inches(3.0, 2.0)\n",
    "text": '    fig.text(0.5, 0.5, "late")\n',
}


def _build(tmp_path, source, name, env=None):
    d = tmp_path / name
    d.mkdir()
    (d / "s.py").write_text(source, encoding="utf-8")
    with mock.patch.dict(os.environ, env or {}):
        return pool.one_shot("s.py", str(d), "main")


def _run(tmp_path, source, name, patches=(), stem="fig", env=None, width=300):
    w = _build(tmp_path, source, name, env)
    try:
        w.ensure_built()
        man = w.override(stem, list(patches))["manifest"]
        png = Path(tmp_path / f"{name}.png")
        png.write_bytes(Path(w.render_png(stem, width)).read_bytes())
        return man, png
    finally:
        pool.discard(w)


@needs_worker
@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_editing_base_is_the_figure_at_the_moment_of_the_save(tmp_path, case):
    ref_man, ref_png = _run(tmp_path, REFERENCE, "ref")
    man, png = _run(tmp_path, REFERENCE + MUTATIONS[case], "mut")
    assert man["modified_after_save"] is True
    assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
    assert "save_moment_unavailable" not in man
    assert "modified_after_save" not in ref_man
    assert filecmp.cmp(ref_png, png, shallow=False), "保存瞬间的复制品必须与参照逐字节一致"
    assert man["size_mm"] == ref_man["size_mm"]


@needs_worker
def test_without_the_snapshot_the_base_would_be_the_final_state(tmp_path):
    """反证：关掉快照，同一个脚本的编辑基础是最终态——与参照不同（证明上一条不是假绿）。"""
    ref_man, ref_png = _run(tmp_path, REFERENCE, "ref")
    man, png = _run(
        tmp_path,
        REFERENCE + MUTATIONS["title_and_color"],
        "off",
        env={figcapture.SAVE_MOMENT_ENV: "0"},
    )
    assert man["modified_after_save"] is True
    assert "save_moment" not in man and "save_moment_unavailable" not in man
    assert not filecmp.cmp(ref_png, png, shallow=False)


@needs_worker
def test_the_last_save_of_the_same_figure_is_the_one_restored(tmp_path):
    """同一张图多次保存：取最后一次（与「改动判据是最近一次保存之后」同一时刻）。"""
    twice = (
        HEAD
        + '    fig.savefig("fig.png")\n    ax.set_title("second")\n    fig.savefig("fig.png")\n'
        + MUTATIONS["add_line"]
    )
    ref = (
        HEAD
        + '    fig.savefig("fig.png")\n    ax.set_title("second")\n    fig.savefig("fig.png")\n'
    )
    _, ref_png = _run(tmp_path, ref, "ref")
    man, png = _run(tmp_path, twice, "twice")
    assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
    assert filecmp.cmp(ref_png, png, shallow=False)


@needs_worker
def test_unpicklable_figure_falls_back_to_the_final_state_and_says_why(tmp_path):
    lam = HEAD.replace(
        '    ax.set_title("saved")\n',
        '    ax.set_title("saved")\n'
        '    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f"{x}u"))\n',
    )
    man, _ = _run(tmp_path, lam + '    fig.savefig("fig.png")\n' + MUTATIONS["add_line"], "lam")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] == "unpicklable"
    assert "save_moment" not in man
    assert len([e for e in man["elements"] if e["gid"].startswith("axes_0.line")]) == 2


@needs_worker
def test_user_classes_are_never_restored(tmp_path):
    """用户自己的类能被 pickle（按名字引用）但还原时被白名单拒绝：不执行用户代码，退回最终态。"""
    src = (
        HEAD.replace(
            "def main():",
            "class MyFmt(matplotlib.ticker.ScalarFormatter):\n    pass\n\n\ndef main():",
        ).replace(
            '    ax.set_title("saved")\n',
            '    ax.set_title("saved")\n    ax.xaxis.set_major_formatter(MyFmt())\n',
        )
        + '    fig.savefig("fig.png")\n'
        + MUTATIONS["add_line"]
    )
    man, _ = _run(tmp_path, src, "cls")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] in ("unpicklable", "restore_failed")


@needs_worker
def test_bbox_extra_artists_are_not_snapshotted(tmp_path):
    src = (
        HEAD
        + '    t = fig.text(0.5, 0.01, "x")\n    fig.savefig("fig.png", bbox_extra_artists=[t])\n'
        + MUTATIONS["add_line"]
    )
    man, _ = _run(tmp_path, src, "extra")
    assert man["save_moment_unavailable"] == "bbox_extra_artists"


@needs_worker
def test_one_figure_under_two_stems_is_not_guessed(tmp_path):
    src = (
        HEAD
        + '    fig.savefig("a.png")\n    ax.set_title("b")\n    fig.savefig("b.png")\n'
        + MUTATIONS["add_line"]
    )
    w = _build(tmp_path, src, "two")
    try:
        stems = list(w.ensure_built()["stems"])
        assert stems == ["a", "b"]
        for stem in stems:
            man = w.override(stem, [])["manifest"]
            assert man["modified_after_save"] is True
            assert man["save_moment_unavailable"] == "multi_stem"
    finally:
        pool.discard(w)


@needs_worker
def test_unchanged_after_save_figure_is_untouched(tmp_path):
    man, _ = _run(tmp_path, REFERENCE, "same")
    for key in ("modified_after_save", "save_moment", "save_moment_unavailable"):
        assert key not in man


@needs_worker
def test_hot_session_and_fresh_replay_see_the_same_snapshot_figure(tmp_path):
    """写回事务不变式：热态所见 == 一次性 worker 从零重放所得——快照在重放里同样被拍、同样被采用。"""
    patches = [{"gid": "axes_0.title", "prop": "text", "value": "edited"}]
    src = REFERENCE + MUTATIONS["title_and_color"]
    man_a, png_a = _run(tmp_path, src, "hot", patches)
    man_b, png_b = _run(tmp_path, src, "replay", patches)
    assert man_a["save_moment"] == man_b["save_moment"] == "restored"
    assert filecmp.cmp(png_a, png_b, shallow=False)
    # 编辑落在保存瞬间的图上：线的颜色是保存时的，不是后来改的红色
    ref_man, _ = _run(tmp_path, REFERENCE, "ref", patches)
    assert [e.get("color") for e in man_a["elements"]] == [
        e.get("color") for e in ref_man["elements"]
    ]


# ---- 同 worker 重试：快照账绑「一次脚本执行」（Codex #1314 P1-a） ----

FLAKY_PUBLISH_PREFIX = """\
import sys
import matplotlib.pyplot as plt

def _flaky_once():
    # 模拟 instrument / 预览发布阶段的临时失败（Windows 上账本瞬时被锁一类）：第一次 build 在这里失败，
    # pool 在同一个 worker 里重跑脚本。标记放在 builtins 上，跨「第二次执行脚本」存活。
    import builtins
    for mod in list(sys.modules.values()):
        cls = getattr(mod, "LiveFigureSession", None)
        if cls is not None and not getattr(cls, "_flaky_installed", False):
            real = cls.instrument_all

            def flaky(self, *a, __real=real, **k):
                if not getattr(builtins, "_tav_flaky_done", False):
                    builtins._tav_flaky_done = True
                    raise OSError("transient publish failure")
                return __real(self, *a, **k)

            cls.instrument_all = flaky
            cls._flaky_installed = True
            break

_flaky_once()

"""


@needs_worker
def test_retry_in_the_same_worker_still_snapshots(tmp_path):
    """第一次 build 在 instrument 阶段临时失败、同 worker 重试成功：存后改图的图仍按保存瞬间采用，且与一次性重放一致。"""
    src = FLAKY_PUBLISH_PREFIX + REFERENCE + MUTATIONS["title_and_color"]
    d = tmp_path / "retry"
    d.mkdir()
    (d / "s.py").write_text(src, encoding="utf-8")
    w = pool.one_shot("s.py", str(d), "main")
    try:
        with pytest.raises(Exception):  # noqa: B017, PT011 —— 第一次 build 的临时失败
            w.ensure_built()
        w.ensure_built()  # 同一个 worker 里重跑脚本
        man = w.override("fig", [])["manifest"]
        png = tmp_path / "retry.png"
        png.write_bytes(Path(w.render_png("fig", 300)).read_bytes())
    finally:
        pool.discard(w)
    assert man["modified_after_save"] is True
    assert man.get("save_moment") == "restored", man.get("save_moment_unavailable")
    ref_man, ref_png = _run(tmp_path, REFERENCE, "ref_retry")
    assert filecmp.cmp(ref_png, png, shallow=False), (
        "重试后的编辑基础必须与一次性重放（保存瞬间）逐字节一致"
    )
    replay_man, replay_png = _run(
        tmp_path, REFERENCE + MUTATIONS["title_and_color"], "replay_retry"
    )
    assert replay_man["save_moment"] == "restored"
    assert filecmp.cmp(replay_png, png, shallow=False)


# ---- 单元：账本本身（不起 worker） ----


def _figure():
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.plot([0, 1], [0, 1])
    return fig


def test_taking_a_snapshot_does_not_touch_the_figure_or_its_stale_state():
    fig = _figure()
    figcapture.mark_saved(fig)
    before = (fig.stale, getattr(fig, figcapture.DIRTY_SINCE_SAVE), type(fig.canvas.manager))
    mgr = fig.canvas.manager
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert (
        fig.stale,
        getattr(fig, figcapture.DIRTY_SINCE_SAVE),
        type(fig.canvas.manager),
    ) == before
    assert fig.canvas.manager is mgr, "临时摘掉的 canvas.manager 必须放回"
    restored, reason = ledger.restore(fig)
    assert reason is None and restored is not fig
    import matplotlib.pyplot as plt

    assert restored.number not in plt.get_fignums() or restored is not plt.figure(fig.number)
    assert not hasattr(restored, figcapture.DIRTY_SINCE_SAVE)
    plt.close("all")


def test_budget_is_bytes_not_wall_clock(monkeypatch):
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_MAX_BYTES", 10)
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "too_large")
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_MAX_BYTES", 10**9)
    monkeypatch.setattr(figcapture, "SAVE_MOMENT_TOTAL_BYTES", 10)
    ledger.take(fig, 2)
    assert ledger.restore(fig) == (None, "budget_exceeded")


def test_selected_occurrence_must_be_the_snapshotted_save():
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 3)
    assert ledger.restore(fig, 2) == (None, "not_selected_save")
    assert ledger.restore(fig, 3)[1] is None


class _UserThing:
    pass


def test_user_classes_are_refused_at_both_ends():
    """拍摄侧在序列化之前就拒（unpicklable）；还原侧对手工拼出来的含用户类的字节流照样拒（restore_failed）。两侧共用一张白名单。"""
    import pickle

    fig = _figure()
    fig._user_thing = _UserThing()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "unpicklable")
    with pytest.raises(pickle.UnpicklingError):
        figcapture._RestrictedUnpickler(io.BytesIO(pickle.dumps(_UserThing()))).load()


class _Booby:
    """序列化钩子会改活 Figure 再抛：拍快照时它一次都不许被调用。"""

    calls = 0

    def __init__(self, fig):
        self._fig = fig

    def __reduce__(self):
        _Booby.calls += 1
        self._fig.axes[0].set_title("MUTATED BY REDUCE")
        raise RuntimeError("boom")


@pytest.mark.parametrize("where", ["attr", "gid", "url", "subclass", "instance_hook"])
def test_taking_a_snapshot_never_runs_user_serialisation_code(where):
    import matplotlib.lines as mlines

    fig = _figure()
    ax = fig.axes[0]
    ax.set_title("kept")
    _Booby.calls = 0
    line = ax.lines[0]
    if where == "attr":
        line.custom_attr = _Booby(fig)
    elif where == "gid":
        line.set_gid(_Booby(fig))
    elif where == "url":
        line.set_url(_Booby(fig))
    elif where == "subclass":

        class MyLine(mlines.Line2D):
            def __reduce__(self):
                _Booby.calls += 1
                ax.set_title("MUTATED BY REDUCE")
                raise RuntimeError("boom")

        ax.add_line(MyLine([0, 1], [1, 0]))
    else:
        booby = _Booby(fig)

        def hook(proto):
            _Booby.calls += 1
            ax.set_title("MUTATED BY REDUCE")
            raise RuntimeError("boom")

        booby.__dict__["__reduce_ex__"] = hook
        line.custom_attr = booby
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert _Booby.calls == 0, "用户的 __reduce__ 被执行了"
    assert ax.get_title() == "kept", "拍快照改了活 Figure"
    assert ledger.restore(fig) == (None, "unpicklable")
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False


def _patch_serialiser(monkeypatch, fig, where, *, raise_after=True):
    """脚本 monkey-patch 白名单类上的序列化方法：钩子改标题（可再抛）。返回调用计数器。"""
    import matplotlib.figure as mfigure
    import matplotlib.lines as mlines

    calls = []
    ax = fig.axes[0]

    def hook(self, *a, **k):
        calls.append(1)
        ax.set_title("MUTATED BY PATCHED HOOK")
        if raise_after:
            raise RuntimeError("boom")
        return NotImplemented

    cls, name = (
        (mlines.Line2D, "__reduce__") if where == "line" else (mfigure.Figure, "__getstate__")
    )
    monkeypatch.setattr(cls, name, hook)
    return calls


@pytest.mark.parametrize("where", ["line", "figure"])
def test_patched_class_level_serialisers_are_never_called(monkeypatch, where):
    """纵深层：类级钩子被 patch 成用户函数（类型名仍是 matplotlib）——拍快照一次都不调它。"""
    monkeypatch.setattr(figcapture, "trusted_surface_intact", lambda: True)  # 只验这一层
    fig = _figure()
    fig.axes[0].set_title("kept")
    figcapture.mark_saved(fig)
    calls = _patch_serialiser(monkeypatch, fig, where)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert calls == [], "被 patch 的序列化钩子被执行了"
    assert fig.axes[0].get_title() == "kept"
    assert ledger.restore(fig) == (None, "unpicklable")
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False


@pytest.mark.parametrize("raise_after", [True, False])
@pytest.mark.parametrize("where", ["line", "figure"])
def test_a_mutation_during_the_snapshot_stays_reported_as_modified_after_save(
    monkeypatch, where, raise_after
):
    """结构层：即使纵深层被绕过（这里直接让它放行），拍摄期间的任何改动都留在脏标记里、快照作废——
    不被清掉，最终如实报 `modified_after_save`。成功（不抛）的钩子同样作废。"""
    monkeypatch.setattr(figcapture, "_class_hooks_trusted", lambda t: True)
    monkeypatch.setattr(figcapture, "trusted_surface_intact", lambda: True)  # 只验结构层
    fig = _figure()
    figcapture.mark_saved(fig)
    calls = _patch_serialiser(monkeypatch, fig, where, raise_after=raise_after)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert calls, "前提：被 patch 的钩子确实在拍摄中跑了（否则这条用例没有验证结构层）"
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is True, "拍摄期间的改动被清掉了"
    assert ledger.restore(fig) == (None, "unpicklable")


def test_a_clean_snapshot_leaves_the_dirty_flag_false():
    fig = _figure()
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert getattr(fig, figcapture.DIRTY_SINCE_SAVE) is False
    assert ledger.restore(fig)[1] is None


# ---- 可信方法面指纹（Codex #1314 P1-d）：检测到 matplotlib 被 patch 就放弃快照 ----


@pytest.fixture
def fresh_surface():
    figcapture._reset_trusted_surface_for_tests()
    figcapture.freeze_trusted_surface()
    yield
    figcapture._reset_trusted_surface_for_tests()


def test_surface_patch_after_the_snapshot_blocks_the_restore_and_the_hook_never_runs(
    monkeypatch, fresh_surface
):
    """拍摄时一切正常，脚本随后 patch `Figure.__setstate__`：还原前的那次比对放弃快照，钩子调用 0 次。"""
    import matplotlib.figure as mfigure

    fig = _figure()
    fig.axes[0].set_title("kept")
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    calls = []
    orig = mfigure.Figure.__setstate__

    def hook(self, state):
        calls.append(1)
        orig(self, state)
        self.axes[0].set_title("HOOK")

    monkeypatch.setattr(mfigure.Figure, "__setstate__", hook)
    assert ledger.restore(fig) == (None, "matplotlib_patched")
    assert calls == []


@pytest.mark.parametrize("where", ["Text.set_text", "Axes.draw", "Line2D.__reduce__"])
def test_any_patched_matplotlib_method_blocks_taking_the_snapshot(
    monkeypatch, fresh_surface, where
):
    import matplotlib.axes as maxes
    import matplotlib.lines as mlines
    import matplotlib.text as mtext

    cls = {"Text": mtext.Text, "Axes": maxes.Axes, "Line2D": mlines.Line2D}[where.split(".")[0]]
    name = where.split(".")[1]
    orig = getattr(cls, name)
    monkeypatch.setattr(cls, name, lambda self, *a, **k: orig(self, *a, **k))
    fig = _figure()
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "matplotlib_patched")


def test_unpatched_surface_stays_intact_and_restores(fresh_surface):
    fig = _figure()
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert figcapture.trusted_surface_intact()
    assert ledger.restore(fig)[1] is None


def test_first_seen_module_with_a_user_function_is_refused():
    """脚本运行期间才导入、没有启动基线的模块：从严，类里出现来源在库目录之外的 Python 函数就判被改过。"""
    import types as _t

    figcapture._reset_trusted_surface_for_tests()
    figcapture.freeze_trusted_surface()
    mod = _t.ModuleType("mpl_toolkits.fake_late")

    class Late:
        def __setstate__(
            self, state
        ):  # 定义在本测试文件里：来源不在 matplotlib / 标准库 / numpy 目录
            pass

    Late.__module__ = mod.__name__
    mod.Late = Late
    assert figcapture._module_origin_ok(mod, mod.__name__, figcapture._surface_roots) is False
    figcapture._reset_trusted_surface_for_tests()


def test_structure_signature_covers_inset_and_secondary_axes(fresh_surface):
    """`fig.axes` 看不到 `inset_axes` 建出来的子轴；签名走 `ordered_axes`，它们的结构也在签名里。"""
    fig = _figure()
    ax = fig.axes[0]
    inset = ax.inset_axes([0.5, 0.5, 0.4, 0.4])
    inset.plot([0, 1], [1, 0])
    assert len(fig.axes) == 1, "前提：fig.axes 里没有子轴"
    sig = figcapture._structure_signature(fig)
    assert (
        len(sig[-1]) == 2
    )  # 主轴 + inset（`secondary_xaxis` 本身 matplotlib 就 pickle 不了，不在快照范围）
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    inset.plot([0, 1], [0, 1])  # 存后在 inset 里加了一条线
    restored, reason = ledger.restore(fig)
    assert reason is None
    assert figcapture._structure_signature(restored) == sig
    assert figcapture._structure_signature(restored) != figcapture._structure_signature(fig)


def test_library_origin_is_decided_by_location_not_by_one_platform_layout(
    tmp_path, monkeypatch, fresh_surface
):
    """Windows 打包 runtime 的误判（Codex #1314 CI）：标准库在 `python3xx.zip` 里、冻结模块的文件名是 `<frozen …>`、依赖库装在
    site-packages——这些都是库来源；用户项目里的文件不是。边界按目录分隔符算（`/rt` 不是 `/rtX` 的父目录）。"""
    root = str(tmp_path / "rt")
    roots = (os.path.normcase(root),)
    under = figcapture._path_under_roots
    assert under(os.path.join(root, "python313.zip", "enum.pyc"), roots)
    assert under(os.path.join(root, "Lib", "site-packages", "cycler", "__init__.py"), roots)
    assert not under("<frozen os>", roots) and not under("<string>", roots)  # 合成文件名不按路径认
    assert not under(str(tmp_path / "rtX" / "evil.py"), roots)
    assert not under(str(tmp_path / "project" / "figure.py"), roots)

    # 装在库目录里的第三方依赖的类（如 pyparsing / cycler）是库来源；用户目录里的模块不是
    dep_dir = tmp_path / "rt" / "site-packages"
    dep_dir.mkdir(parents=True)
    monkeypatch.setattr(figcapture, "_surface_roots", roots)
    for modname, where, expected in (
        ("fakedep_ok", dep_dir / "fakedep_ok.py", True),
        ("fakedep_user", tmp_path / "project" / "fakedep_user.py", False),
    ):
        mod = types.ModuleType(modname)
        mod.__file__ = str(where)
        cls = type("Dep", (), {})
        cls.__module__ = modname
        monkeypatch.setitem(sys.modules, modname, mod)
        assert figcapture._owner_module_ok(cls) is expected


def test_only_matplotlibs_own_function_rebinding_is_accepted(monkeypatch, fresh_surface):
    """复核只接受 matplotlib 自己的函数重绑定（它的惰性状态）；其余——用户函数、partial、绑定方法、第三方包、标准库——一律是被改过。"""
    import matplotlib.lines as mlines
    import matplotlib.text as mtext

    monkeypatch.setattr(mlines.Line2D, "set_label", mtext.Text.set_text)  # matplotlib 自己的函数
    assert figcapture.trusted_surface_intact() is True
    monkeypatch.setattr(
        mlines.Line2D, "set_label", lambda self, s: None
    )  # 用户（测试文件）里的函数
    assert figcapture.trusted_surface_intact() is False


@pytest.mark.parametrize("wrapper", ["partial", "bound_method", "stdlib_function"])
def test_wrappers_around_user_code_are_patches_even_if_their_type_is_a_library_type(
    monkeypatch, fresh_surface, wrapper
):
    import functools

    import matplotlib.text as mtext

    calls = []

    def hook(*a, **k):
        calls.append(1)

    class Owner:
        def method(self, *a, **k):
            calls.append(1)

    if wrapper == "partial":
        replacement = functools.partial(hook)
    elif wrapper == "bound_method":
        replacement = Owner().method
    else:
        replacement = functools.wraps  # 标准库的函数
    fig = _figure()  # 先建图再 patch：matplotlib 自己建图时也会用到 set_text
    figcapture.mark_saved(fig)
    monkeypatch.setattr(mtext.Text, "set_text", replacement)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "matplotlib_patched")
    assert calls == []


def test_a_third_party_package_patching_matplotlib_is_a_patch(tmp_path, monkeypatch, fresh_surface):
    """scienceplots / seaborn / 用户自己装的包去 patch matplotlib：它们在 site-packages 里（算「库来源」），但仍是被改过。"""
    import matplotlib.axes as maxes

    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(
        figcapture, "_surface_roots", (*figcapture._surface_roots, os.path.normcase(str(site)))
    )
    ns = {"__name__": "fake_scienceplots"}
    exec(
        compile(
            "def draw(self, renderer):\n    return None\n",
            str(site / "fake_scienceplots.py"),
            "exec",
        ),
        ns,
    )  # noqa: S102
    monkeypatch.setattr(maxes.Axes, "draw", ns["draw"])
    assert figcapture.trusted_surface_intact() is False


def test_pyplot_switching_backend_is_not_a_patch(fresh_surface):
    import matplotlib.pyplot as plt

    plt.switch_backend("agg")
    assert figcapture.trusted_surface_intact() is True
    plt.close("all")


def _function_from(source: str, filename: str, module: str):
    ns = {"__name__": module}
    exec(compile(source, filename, "exec"), ns)  # noqa: S102
    return ns["f"]


def test_synthetic_filenames_are_not_library_origin_unless_frozen_stdlib(fresh_surface):
    """`<string>`（用户 `exec` 生成的函数就是它）不算库来源；`<frozen X>` 只在 X 精确等于函数的 `__module__` 且是标准库时才认。"""
    roots = figcapture._surface_roots
    ok = figcapture._function_origin_ok
    src = "def f():\n    pass\n"
    assert ok(_function_from(src, "<frozen os>", "os"), roots)  # Windows 冻结的标准库
    assert not ok(_function_from(src, "<frozen os>", "evil_lib"), roots)  # X 对不上 `__module__`
    assert not ok(_function_from(src, "<frozen evil_lib>", "evil_lib"), roots)  # 不是标准库
    assert not ok(_function_from(src, "<string>", "matplotlib.text"), roots)  # 即便借了库的模块名
    assert not ok(_function_from(src, "<whatever>", "matplotlib.text"), roots)


def test_exec_generated_method_in_a_first_seen_module_is_a_patch(monkeypatch, fresh_surface):
    calls = []
    fn = _function_from(
        "def f(self, *a):\n    CALLS.append(1)\n", "<string>", "mpl_toolkits.fake_exec"
    )
    fn.__globals__["CALLS"] = calls
    mod = types.ModuleType("mpl_toolkits.fake_exec")
    cls = type("C", (), {"__setstate__": fn, "__module__": mod.__name__})
    mod.C = cls
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    fig = _figure()
    figcapture.mark_saved(fig)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger.restore(fig) == (None, "matplotlib_patched")
    assert calls == []
    # 被判过的模块不能被记成基线：再比一次仍然是被改过
    assert figcapture.trusted_surface_intact() is False


def test_a_foreign_packages_custom_path_is_never_iterated(monkeypatch, fresh_surface):
    """外来类所在顶层包的 `__path__` 可以是任意对象（命名空间包的 `_NamespacePath` 迭代会走 meta_path / path_hooks）：
    只认精确的 list / tuple，其它当非库来源，且绝不迭代。"""
    iterated = []

    class HostilePath:
        def __iter__(self):
            iterated.append(1)
            raise RuntimeError("hostile __iter__")

    pkg = types.ModuleType("hostile_ns_pkg")
    pkg.__path__ = HostilePath()
    monkeypatch.setitem(sys.modules, "hostile_ns_pkg", pkg)
    foreign = type("Foreign", (), {"__module__": "hostile_ns_pkg.sub"})
    assert figcapture._owner_module_ok(foreign) is False
    mod = types.ModuleType("mpl_toolkits.fake_foreign")
    mod.Foreign = foreign
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    assert figcapture.trusted_surface_intact() is False
    assert iterated == []


def _hostile(calls, fig):
    """每一种「被检查就会跑起来」的写法：属性钩子、比较 / 哈希 / 布尔钩子、元类的 `__instancecheck__` / `__getattr__`。"""

    def boom(name):
        calls.append(name)
        fig.axes[0].set_title("HOOK RAN")
        raise RuntimeError("hostile hook ran: " + name)

    class Evil:
        def __getattr__(self, n):
            boom("__getattr__:" + n)

        def __getattribute__(self, n):
            boom("__getattribute__:" + n)

        def __eq__(self, other):
            boom("__eq__")

        def __hash__(self):
            boom("__hash__")

        def __bool__(self):
            boom("__bool__")

        def __repr__(self):
            boom("__repr__")

    class Meta(type):
        def __getattr__(cls, n):
            boom("meta.__getattr__:" + n)

        def __instancecheck__(cls, inst):
            boom("meta.__instancecheck__")

        def __eq__(cls, other):
            boom("meta.__eq__")

        def __hash__(cls):
            boom("meta.__hash__")

    class WithMeta(metaclass=Meta):
        pass

    class EvilModule(types.ModuleType):
        def __getattribute__(self, n):
            boom("module.__getattribute__:" + n)

    return Evil, WithMeta, EvilModule


@pytest.mark.parametrize("where", ["late_module", "late_module_meta", "evil_module", "known_class"])
def test_fingerprint_never_runs_code_from_the_objects_it_inspects(
    monkeypatch, fresh_surface, where
):
    """脚本导入一个没见过的 mpl_toolkits 模块并往类里塞带自定义属性 / 比较 / 元类钩子的对象，或塞进已知的 matplotlib 类：
    指纹与比对只做静态读，钩子调用 0 次，没有异常漏出，结论是「被改过」（用户类型的对象不是库来源）。"""
    import matplotlib.lines as mlines

    fig = _figure()
    fig.axes[0].set_title("kept")
    figcapture.mark_saved(fig)
    calls = []
    Evil, WithMeta, EvilModule = _hostile(calls, fig)
    if where == "evil_module":
        monkeypatch.setitem(
            sys.modules, "mpl_toolkits.fake_evil", EvilModule("mpl_toolkits.fake_evil")
        )
    elif where == "known_class":
        monkeypatch.setattr(mlines.Line2D, "evil_hook", Evil(), raising=False)
    else:
        mod = types.ModuleType("mpl_toolkits.fake_evil")

        class C:
            pass

        C.__module__ = mod.__name__
        C.__setstate__ = Evil() if where == "late_module" else WithMeta
        mod.C = C
        mod.hostile = Evil()
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    assert figcapture.trusted_surface_intact() is False
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)  # 不抛
    assert ledger.restore(fig) == (None, "matplotlib_patched")
    assert calls == [], f"比对期间跑了用户代码: {calls}"
    assert fig.axes[0].get_title() == "kept"


PATCH_AFTER_SAVE = {
    "setstate": (
        "import matplotlib.figure as _mf\n"
        "_orig = _mf.Figure.__setstate__\n"
        "def _hook(self, state):\n"
        "    _orig(self, state)\n"
        "    self.axes[0].set_title('HOOK RAN')\n"
        "_mf.Figure.__setstate__ = _hook\n"
    ),
    "set_text": (
        "import matplotlib.text as _mt\n"
        "_o = _mt.Text.set_text\n"
        "_mt.Text.set_text = lambda self, s: _o(self, s)\n"
    ),
    "axes_draw": (
        "import matplotlib.axes as _ma\n_o = _ma.Axes.draw\n"
        "_ma.Axes.draw = lambda self, r: _o(self, r)\n"
    ),
}


@needs_worker
@pytest.mark.parametrize("which", sorted(PATCH_AFTER_SAVE))
def test_script_that_patches_matplotlib_after_the_save_keeps_the_final_state_and_says_so(
    tmp_path, which
):
    """存盘之后脚本 patch 了 matplotlib：快照放弃，编辑基础是最终态，`modified_after_save` 照常报，被 patch 的钩子没跑过。"""
    patch = "".join("    " + line + "\n" for line in PATCH_AFTER_SAVE[which].splitlines())
    src = REFERENCE + patch + MUTATIONS["title_and_color"]
    man, _ = _run(tmp_path, src, f"patched_{which}")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] == "matplotlib_patched"
    assert "save_moment" not in man
    assert "HOOK RAN" not in json.dumps(man, ensure_ascii=False)


@needs_worker
def test_hostile_late_module_does_not_break_the_build_and_runs_no_code(tmp_path):
    """端到端：脚本在保存前往一个新模块 `mpl_toolkits.fake_evil` 的类里塞带 `__getattr__` 的对象。
    savefig 里的 take 不得把它的钩子跑起来（否则这条钩子会改标题再抛、脚本当场崩），build 成功，退回只告知。"""
    inject = (
        "    import sys, types\n"
        "    class Evil:\n"
        "        calls = 0\n"
        "        def __getattr__(self, n):\n"
        "            Evil.calls += 1\n"
        "            plt.gcf().axes[0].set_title('HOOK RAN')\n"
        "            raise RuntimeError('boom')\n"
        "    _m = types.ModuleType('mpl_toolkits.fake_evil')\n"
        "    class _C: pass\n"
        "    _C.__module__ = 'mpl_toolkits.fake_evil'\n"
        "    _C.__setstate__ = Evil()\n"
        "    _m._C = _C\n"
        "    sys.modules['mpl_toolkits.fake_evil'] = _m\n"
    )
    src = (
        HEAD
        + inject
        + '    fig.savefig("fig.png")\n    assert Evil.calls == 0\n'
        + MUTATIONS["title_and_color"]
    )
    man, _ = _run(tmp_path, src, "hostile")
    assert man["modified_after_save"] is True
    assert man["save_moment_unavailable"] == "matplotlib_patched"
    assert "HOOK RAN" not in json.dumps(man, ensure_ascii=False)


@needs_worker
def test_unpatched_script_still_restores_with_the_fingerprint_on(tmp_path):
    man, _ = _run(tmp_path, REFERENCE + MUTATIONS["title_and_color"], "unpatched")
    assert man.get("save_moment") == "restored"


@needs_worker
def test_unpicklable_user_hook_does_not_hide_modified_after_save(tmp_path):
    """端到端：存一次、之后没改图——有用户对象挂在 Figure 上也不得被快照搅出 modified_after_save，且钩子从未被调用。"""
    src = HEAD.replace(
        "def main():",
        "class Booby:\n    n = 0\n    def __reduce__(self):\n        Booby.n += 1\n"
        "        plt.gcf().axes[0].set_title('MUTATED')\n        raise RuntimeError\n\n\ndef main():",
    ).replace(
        '    ax.set_title("saved")\n',
        '    ax.set_title("saved")\n    line.custom_attr = Booby()\n',
    )
    man, _ = _run(tmp_path, src + '    fig.savefig("fig.png")\n    assert Booby.n == 0\n', "booby")
    assert "modified_after_save" not in man
    assert all(e.get("value") != "MUTATED" for e in man["elements"])


def test_reasons_are_a_closed_set_and_release_frees_bytes():
    fig = _figure()
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    assert ledger._total > 0
    ledger.release()
    assert ledger._total == 0
    assert figcapture.SAVE_MOMENT_RESTORED not in figcapture.SAVE_MOMENT_REASONS


def _builders():
    import numpy as np

    def basic(plt):
        fig, ax = plt.subplots(figsize=(3, 2))
        ax.plot([1, 2, 3], [1, 4, 9], label="a")
        ax.legend()
        ax.set_title("t")
        return fig

    def rich(plt):
        fig, ax = plt.subplots(figsize=(4, 3))
        sc = ax.scatter(np.arange(50), np.arange(50) ** 0.5, c=np.arange(50))
        fig.colorbar(sc, ax=ax)
        ax.errorbar([1, 2], [1, 2], yerr=0.2)
        ax.fill_between([0, 1, 2], [0, 1, 0], [1, 2, 1], alpha=0.3)
        ax.annotate("x", (1, 1), (10, 10), arrowprops={"arrowstyle": "->"})
        return fig

    def twin_tight(plt):
        fig, ax = plt.subplots(figsize=(4, 3))
        ax.plot([1, 2])
        ax.twinx().plot([3, 1])
        fig.tight_layout()
        return fig

    def constrained(plt):
        fig, axs = plt.subplots(2, 2, figsize=(5, 4), layout="constrained")
        for a in axs.flat:
            a.imshow(np.random.RandomState(0).rand(5, 5))
        return fig

    def three_d(plt):
        fig = plt.figure()
        fig.add_subplot(projection="3d").plot([1, 2], [1, 2], [1, 2])
        return fig

    return [basic, rich, twin_tight, constrained, three_d]


@pytest.mark.parametrize("build", _builders(), ids=lambda f: f.__name__)
def test_restored_figure_renders_pixel_identical_to_the_original(build):
    """还原保真度：同一时刻、同一 dpi，复制品与原件逐字节同一张 PNG（多种 artist 家族）。"""
    import io

    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    def png(fig):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        return buf.getvalue()

    fig = build(plt)
    ledger = figcapture.SaveMomentLedger()
    ledger.take(fig, 1)
    restored, reason = ledger.restore(fig)
    assert reason is None
    FigureCanvasAgg(restored)
    assert png(restored) == png(fig)
    plt.close("all")


WRITE_BACK_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [0, 1, 4], label="saved-series")
    ax.legend()
    ax.set_title("saved title")
    fig.savefig("Snap.pdf")
    ax.set_title("LATE TITLE")
    fig.text(0.5, 0.5, "LATE TEXT")
"""


@needs_worker
def test_write_back_writes_the_saved_moment_plus_edits_and_replay_verifies(tmp_path, monkeypatch):
    """全链路（真 matplotlib + Flask + 一次性重放）：写回的 PDF = 保存瞬间 + 用户的编辑，后来的改动不在其中；
    热态 == 干净重放（`verification.replay == ok`、像素门过）。"""
    import json

    import pymupdf

    from tavotto import app as m
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_snap.py").write_text(WRITE_BACK_SCRIPT, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig_snap.py": {
                        "entry": "main",
                        "cost": "light",
                        "notes": "",
                        "stems": ["Snap"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    doc = pymupdf.open()
    doc.new_page(width=216, height=144)
    doc.save(figs / "Snap.pdf")
    doc.close()
    m.open_project(str(figs))
    client = m.app.test_client()
    try:
        patches = [{"gid": "axes_0.title", "prop": "text", "value": "edited title"}]
        r = client.post("/api/engine/render", json={"id": "Snap.pdf", "patches": patches})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["manifest"]["save_moment"] == "restored"
        resp = client.post(
            "/api/engine/update_source",
            json={
                "id": "Snap.pdf",
                "patches": patches,
                "expected_mtime": int((figs / "Snap.pdf").stat().st_mtime),
            },
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["verification"]["replay"] == "ok", body["verification"]
        assert body["verification"]["pixels"] == "ok", body["verification"]
        with pymupdf.open(figs / "Snap.pdf") as written:
            text = written[0].get_text()
        assert "edited title" in text and "saved-series" in text
        assert "LATE TEXT" not in text and "LATE TITLE" not in text
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()
