"""savefig 的参数记进捕获描述符（`savefig_calls`，tight 图幅的第一步）。

拦截只取 stem 的年代，`savefig(bbox_inches="tight", pad_inches=0.02)` 的脚本在
Tavotto 里按 figsize 出图、紧贴图幅的轴标题被切掉半截（审计 T14 / T33），而那些
参数一个都没留下——将来怎么用它们都无从谈起。这里钉住的是**记**这一步：

* 记的是**实效值**：显式参数 > 调用那一刻的 `rcParams["savefig.*"]`；
* 三档：`None` = 没观察到、`[]` = pyplot 捕获从没存过盘、非空 = 调用列表——「不知道」
  不许被压成「没有」（`paper_style.save` 捷径自 ADR 0098 §四起执行用户那份 `save`，看得见了）；
* safe worker 与浏览器 playground 逐字段一致（同一份 `figcapture` 实现）；
* 怎么用这些参数（图幅）在 `tests/test_savefig_frame.py`（ADR 0098）。

记账规则是纯函数，单测直接驱动；实效值要真 matplotlib，经真 worker / 真浏览器
驱动跑（本进程不 import matplotlib，与 `test_compat_capture_parity.py` 同一条纪律）。
"""

from __future__ import annotations

import sys
import types

import pytest

from tavotto.engine import figcapture, pool
from test_compat_capture_parity import browser_load, desktop_build, write

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ===========================================================================
# 纯函数：实效值的解析顺序与记账规则
# ===========================================================================
@pytest.fixture
def fake_mpl(monkeypatch):
    """`savefig_call` 只经 `sys.modules` 取 matplotlib：塞一个假的进去，rc 可控。"""
    rc = {
        "savefig.bbox": None,
        "savefig.pad_inches": 0.1,
        "savefig.dpi": "figure",
        "savefig.transparent": False,
        "savefig.facecolor": "auto",
        "savefig.edgecolor": "auto",
        "savefig.format": "png",
    }
    mpl = types.SimpleNamespace(rcParams=rc)
    colors = types.SimpleNamespace(
        to_hex=lambda c, keep_alpha=False: {"white": "#ffffffff", (1, 0, 0): "#ff0000ff"}[c]
    )
    monkeypatch.setitem(sys.modules, "matplotlib", mpl)
    monkeypatch.setitem(sys.modules, "matplotlib.colors", colors)
    return rc


class _Bbox:
    extents = (-0.25, -0.5, 3.0, 2.25)


def test_explicit_arguments_win(fake_mpl):
    call = figcapture.savefig_call(
        "out/Fig1.PDF",
        {
            "bbox_inches": "tight",
            "pad_inches": 0.02,
            "dpi": 600,
            "transparent": True,
            "facecolor": "white",
            "edgecolor": (1, 0, 0),
            "bbox_extra_artists": ["a", "b"],
        },
    )
    assert call == {
        "format": "pdf",
        "bbox_inches": "tight",
        "pad_inches": 0.02,
        "dpi": 600.0,
        "transparent": True,
        "facecolor": "#ffffffff",
        "edgecolor": "#ff0000ff",
        "bbox_extra_artists": 2,
    }


def test_missing_arguments_fall_back_to_rcparams_at_call_time(fake_mpl):
    """`savefig.bbox: tight` 写在 matplotlibrc / rc_context 里的图，参数表上一个字都没有。"""
    fake_mpl["savefig.bbox"] = "tight"
    fake_mpl["savefig.pad_inches"] = "layout"
    call = figcapture.savefig_call("Fig1", {"bbox_inches": None})
    assert call["bbox_inches"] == "tight"
    assert call["pad_inches"] == "layout"
    assert call["dpi"] == "figure"
    assert call["format"] == "png", "没有后缀、没有 format= 时取 rcParams['savefig.format']"
    assert call["facecolor"] == "auto"
    assert call["bbox_extra_artists"] is None


def test_explicit_format_beats_the_suffix(fake_mpl):
    assert figcapture.savefig_call("a.pdf", {"format": "SVG"})["format"] == "svg"


def test_an_explicit_bbox_is_recorded_in_inches(fake_mpl):
    call = figcapture.savefig_call("a.pdf", {"bbox_inches": _Bbox()})
    assert call["bbox_inches"] == [-0.25, -0.5, 3.0, 2.25]


def test_unrecognised_values_are_recorded_not_guessed(fake_mpl):
    call = figcapture.savefig_call("a.pdf", {"facecolor": "no-such-colour", "bbox_inches": 7})
    assert call["facecolor"] == "no-such-colour"
    assert call["bbox_inches"] == "7"


class TestRecordingRule:
    def test_calls_are_recorded_in_order(self):
        calls, fig = {}, object()
        capture = {"Fig1": fig}
        figcapture.record_savefig_call(calls, capture, "Fig1", fig, {"format": "pdf"})
        figcapture.record_savefig_call(calls, capture, "Fig1", fig, {"format": "png"})
        assert calls == {"Fig1": [{"format": "pdf"}, {"format": "png"}]}

    def test_a_call_from_another_figure_under_the_same_stem_is_not_ours(self):
        """同名 stem 已被另一张图认领：那次调用的参数不属于捕获表里这张。"""
        calls, first, second = {}, object(), object()
        capture = {"Fig1": first}
        figcapture.record_savefig_call(calls, capture, "Fig1", second, {"format": "pdf"})
        assert calls == {}

    def test_an_unobserved_call_makes_the_whole_record_unknown(self):
        """只记到一部分的列表会被当成全貌——一次没观察到，整份就是「不知道」。"""
        calls, fig = {}, object()
        capture = {"Fig1": fig}
        figcapture.record_savefig_call(calls, capture, "Fig1", fig, {"format": "pdf"})
        figcapture.record_savefig_call(calls, capture, "Fig1", fig, None)
        figcapture.record_savefig_call(calls, capture, "Fig1", fig, {"format": "png"})
        assert calls == {"Fig1": None}

    def test_the_record_is_capped(self):
        calls, fig = {}, object()
        capture = {"Fig1": fig}
        for i in range(figcapture.MAX_SAVEFIG_CALLS + 3):
            figcapture.record_savefig_call(calls, capture, "Fig1", fig, {"i": i})
        assert [c["i"] for c in calls["Fig1"]] == list(range(figcapture.MAX_SAVEFIG_CALLS))

    def test_the_descriptor_value_keeps_unknown_apart_from_none(self):
        calls = {"a": [{"format": "pdf"}], "b": None}
        assert figcapture.savefig_calls_of(calls, "a", figcapture.SOURCE_SAVEFIG) == [
            {"format": "pdf"}
        ]
        assert figcapture.savefig_calls_of(calls, "b", figcapture.SOURCE_SAVEFIG) is None
        assert figcapture.savefig_calls_of(calls, "c", figcapture.SOURCE_SAVEFIG) is None
        assert figcapture.savefig_calls_of(calls, "c", figcapture.SOURCE_PYPLOT) == []


def _descriptor(**kw):
    base = dict(
        script="fig.py",
        entry="main",
        stem="Fig1",
        capture_source=figcapture.SOURCE_SAVEFIG,
        execution_profile=figcapture.PROFILE_SAFE,
        size_mm=(80.0, 60.0),
        source_fingerprint="sha256:feed",
    )
    base.update(kw)
    return figcapture.build_descriptor(**base)


class TestDescriptorField:
    def test_a_pyplot_capture_cannot_claim_savefig_calls(self):
        with pytest.raises(ValueError, match="savefig_calls"):
            _descriptor(capture_source=figcapture.SOURCE_PYPLOT, savefig_calls=[{"format": "pdf"}])

    def test_payload_roundtrip_keeps_all_three_states(self):
        for value in (None, [], [{"format": "pdf", "bbox_inches": "tight"}]):
            d = _descriptor(savefig_calls=value)
            payload = d.to_payload()
            assert payload["savefig_calls"] == value
            assert figcapture.descriptor_from_payload(payload) == d

    def test_an_old_payload_without_the_key_reads_as_unknown(self):
        payload = _descriptor(savefig_calls=[{"format": "pdf"}]).to_payload()
        del payload["savefig_calls"]
        assert figcapture.descriptor_from_payload(payload).savefig_calls is None

    def test_the_field_does_not_enter_the_fingerprint(self):
        """指纹是 stale hint（脚本 + 执行描述）；savefig 参数由脚本决定，重复算进去
        只会让「只多了一个字段」的升级把全部旧 fingerprint 判成过时。"""
        import inspect

        assert "savefig_calls" not in inspect.signature(figcapture.source_fingerprint).parameters


# ===========================================================================
# 真 worker / 真浏览器驱动
# ===========================================================================
TIGHT = """\
import matplotlib
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.15, 2.27))
ax.plot([1, 2, 3], [2, 1, 3])
ax.set_xlabel("Time (s)")
fig.savefig("tight.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("tight.png", dpi=300, transparent=True, facecolor="white")
other = plt.figure()
other.savefig("tight.svg")   # 同名 stem、另一张图：不是它的调用
with matplotlib.rc_context({"savefig.bbox": "tight", "savefig.pad_inches": 0.3}):
    fig.savefig("tight.eps")
plt.close(other)
"""

TIGHT_EXPECTED = [
    {
        "format": "pdf",
        "bbox_inches": "tight",
        "pad_inches": 0.02,
        "dpi": "figure",
        "transparent": False,
        "facecolor": "auto",
        "edgecolor": "auto",
        "bbox_extra_artists": None,
    },
    {
        "format": "png",
        "bbox_inches": None,
        "pad_inches": 0.1,
        "dpi": 300.0,
        "transparent": True,
        "facecolor": "#ffffffff",
        "edgecolor": "auto",
        "bbox_extra_artists": None,
    },
    {
        "format": "eps",
        "bbox_inches": "tight",
        "pad_inches": 0.3,
        "dpi": "figure",
        "transparent": False,
        "facecolor": "auto",
        "edgecolor": "auto",
        "bbox_extra_artists": None,
    },
]

SHOW_ONLY = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()\n"

PAPER_STYLE = (
    "def save(fig, stem, outdir=None):\n    fig.savefig(f'{stem}.pdf', bbox_inches='tight')\n"
)
USES_PAPER_STYLE = """\
import matplotlib.pyplot as plt
from paper_style import save

fig, ax = plt.subplots()
ax.plot([1, 2])
save(fig, "Fig9")
fig.savefig("Fig9.png", dpi=300)  # 同一张图、同一个 stem 又存一次：看得见的这次不是全貌
"""


@needs_worker
class TestRecordedOnEveryEntry:
    def test_desktop_records_the_effective_arguments(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "tight.py", TIGHT)
        (d,) = desktop_build(figs, "tight.py")["descriptors"]
        assert d["savefig_calls"] == TIGHT_EXPECTED

    def test_browser_records_the_same(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "tight.py", TIGHT)
        desktop = desktop_build(figs, "tight.py")["descriptors"]
        browser = browser_load(TIGHT, "tight.py", tmp_path / "ws")["descriptors"]
        assert browser == desktop

    def test_a_pyplot_capture_says_it_was_never_saved(self, tmp_path):
        figs = tmp_path / "figs"
        write(figs, "show_only.py", SHOW_ONLY)
        (d,) = desktop_build(figs, "show_only.py")["descriptors"]
        assert d["capture_source"] == "pyplot"
        assert d["savefig_calls"] == []

    def test_the_paper_style_shortcut_is_observed(self, tmp_path):
        """ADR 0098 §四起捷径执行用户那份 `save`：它里面那句 savefig 与之后原生 savefig 存的
        同一个 stem 都看得见（以前捷径整个被替换，这里记的是「没观察到」）。"""
        figs = tmp_path / "figs"
        write(figs, "paper_style.py", PAPER_STYLE)
        write(figs, "uses.py", USES_PAPER_STYLE)
        (d,) = desktop_build(figs, "uses.py")["descriptors"]
        assert d["stem"] == "Fig9"
        assert d["capture_source"] == "savefig"
        assert [(c["format"], c["bbox_inches"], c["dpi"]) for c in d["savefig_calls"]] == [
            ("pdf", "tight", "figure"),
            ("png", None, 300.0),
        ]
