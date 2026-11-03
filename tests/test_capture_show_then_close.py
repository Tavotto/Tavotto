"""G1：`plt.show()` 之后 `plt.close()` 的图也要捕获（Compatibility Guardian 家族 G1）。

兜底只看脚本跑完仍活着的 Figure；`plt.show(); plt.close('all')` 的脚本跑完一张不剩，safe worker
以前返回 ok 却捕获 0 张。现在 show 那一刻开着的图记账（`figcapture.ShownFigures`），兜底时补进来：
同一套 stem 命名 / 来源（pyplot）/ 上限 8 张 + 提示，不与 savefig 认领或跑完仍活着的图重复。
真起 safe worker（`pool.one_shot`），本进程不 import matplotlib。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool

FIXTURE = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 2.6))
    plt.show()
    plt.close('all')


main()
"""


def build(tmp_path, source: str, name: str = "g1.py") -> dict:
    figs = tmp_path / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    (figs / name).write_text(source, encoding="utf-8")
    w = pool.one_shot(name, str(figs), "__main__")
    try:
        return w.ensure_built()
    finally:
        pool.discard(w)


def stems_of(tmp_path, source: str, name: str = "g1.py") -> dict:
    return build(tmp_path, source, name).get("stems") or {}


def test_show_then_close_all_is_captured(tmp_path):
    stems = stems_of(tmp_path, FIXTURE)
    assert list(stems) == ["g1"]
    assert stems["g1"]["source"] == "pyplot"


def test_loop_show_close_each_figure_is_captured_in_order(tmp_path):
    src = """\
import matplotlib.pyplot as plt

for i in range(3):
    fig, ax = plt.subplots(figsize=(3.0, 2.0))
    ax.plot([0, 1, 2], [0, i, 2])
    plt.show()
    plt.close(fig)
"""
    stems = stems_of(tmp_path, src)
    assert list(stems) == ["g1", "g1-2", "g1-3"]
    assert {s["source"] for s in stems.values()} == {"pyplot"}


def test_show_inside_functions_and_from_import_show(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.pyplot import show


def draw(k):
    fig, ax = plt.subplots(figsize=(3.0, 2.0))
    ax.plot([0, k])
    show()
    plt.close(fig)


draw(1)
draw(2)
"""
    assert list(stems_of(tmp_path, src)) == ["g1", "g1-2"]


def test_repeated_show_of_the_same_figure_is_one_stem(tmp_path):
    src = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([1, 2])
plt.show()
plt.show()
plt.close(fig)
"""
    assert list(stems_of(tmp_path, src)) == ["g1"]


def test_shown_and_still_open_at_end_is_not_captured_twice(tmp_path):
    src = """\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3])
plt.show()
"""
    assert list(stems_of(tmp_path, src)) == ["g1"]


def test_shown_then_closed_plus_open_tail_keeps_creation_order(tmp_path):
    src = """\
import matplotlib.pyplot as plt

a = plt.figure(figsize=(3.0, 2.0))
plt.plot([1, 2])
plt.show()
plt.close(a)
plt.figure(figsize=(3.0, 2.0))
plt.plot([2, 1])
"""
    assert list(stems_of(tmp_path, src)) == ["g1", "g1-2"]


def test_savefig_then_show_then_close_has_no_duplicate(tmp_path):
    src = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([1, 2])
fig.savefig("out.png")
plt.show()
plt.close(fig)
"""
    stems = stems_of(tmp_path, src)
    assert list(stems) == ["out"]
    assert stems["out"]["source"] == "savefig"


def test_close_without_show_is_still_not_captured(tmp_path):
    """反面：没 show 过就关掉的图，交互用户也没见过——判定不变（仍是 0 张，仍如实报）。"""
    src = """\
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([1, 2])
plt.close(fig)
"""
    assert stems_of(tmp_path, src) == {}


def test_show_does_not_change_the_current_figure(tmp_path):
    """记账不许碰 pyplot 的活动图：脚本在 show 之后的 `plt.gca()` 仍作用于原来那张。"""
    src = """\
import matplotlib.pyplot as plt

a = plt.figure(figsize=(3.0, 2.0))
b = plt.figure(figsize=(3.0, 2.0))
plt.show()
assert plt.gcf() is b
plt.plot([1, 2])
"""
    assert list(stems_of(tmp_path, src)) == ["g1", "g1-2"]


def test_empty_figure_shown_then_closed_is_captured_like_an_open_one(tmp_path):
    shown = (
        "import matplotlib.pyplot as plt\nplt.figure(figsize=(3.0, 2.0))\nplt.show()\nplt.close()\n"
    )
    left_open = "import matplotlib.pyplot as plt\nplt.figure(figsize=(3.0, 2.0))\nplt.show()\n"
    assert (
        list(stems_of(tmp_path / "a", shown)) == list(stems_of(tmp_path / "b", left_open)) == ["g1"]
    )


def test_over_the_cap_keeps_eight_and_reports_the_rest(tmp_path):
    src = """\
import matplotlib.pyplot as plt

for i in range(10):
    plt.figure(figsize=(2.0, 1.5))
    plt.plot([0, i])
    plt.show()
    if i != 9:
        plt.close()
"""
    resp = build(tmp_path, src)
    assert len(resp["stems"]) == 8
    assert resp.get("dropped_figures") == 2


@pytest.mark.parametrize("n", [8])
def test_exactly_at_the_cap_drops_nothing(tmp_path, n):
    src = f"""\
import matplotlib.pyplot as plt

for i in range({n}):
    plt.figure(figsize=(2.0, 1.5))
    plt.show()
    plt.close()
"""
    resp = build(tmp_path, src)
    assert len(resp["stems"]) == n
    assert not resp.get("dropped_figures")


def test_repeated_shows_do_not_eat_the_cap(tmp_path):
    """同一张图 show 多次只占一个名额：5 张各 show 两次，不能被当成 10 张而丢图。"""
    src = """\
import matplotlib.pyplot as plt

for i in range(5):
    plt.figure(figsize=(2.0, 1.5))
    plt.show()
    plt.show()
    plt.close()
"""
    resp = build(tmp_path, src)
    assert len(resp["stems"]) == 5
    assert not resp.get("dropped_figures")


RETRY_SRC = """\
import builtins

import matplotlib.pyplot as plt

run = getattr(builtins, "_g1_run", 0) + 1
builtins._g1_run = run
n = 3 if run == 1 else 1
for i in range(n):
    plt.figure(figsize=(2.0 + run, 1.5))
    plt.show()
    plt.close()
if run == 1:
    raise RuntimeError("第一次 build 在 show+close 之后失败")
"""


def test_failed_build_retry_in_same_worker_starts_a_fresh_ledger(tmp_path):
    """build 失败的会话会被复用、重跑脚本：第二次捕获只来自第二次（Codex #1138 P1）。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "g1.py").write_text(RETRY_SRC, encoding="utf-8")
    w = pool.one_shot("g1.py", str(figs), "__main__")
    try:
        with pytest.raises(Exception):
            w.ensure_built()
        assert w.build_failed
        resp = w.ensure_built()
    finally:
        pool.discard(w)
    stems = resp["stems"]
    assert list(stems) == ["g1"]


def test_savefig_claimed_figures_do_not_eat_the_fallback_cap(tmp_path):
    """10 张 show+close，前两张显式 savefig：8 张兜底 + 2 张 savefig，不丢（Codex #1138 P2）。"""
    src = """\
import matplotlib.pyplot as plt

for i in range(10):
    fig = plt.figure(figsize=(2.0, 1.5))
    if i < 2:
        fig.savefig(f"saved{i}.png")
    plt.show()
    plt.close()
"""
    resp = build(tmp_path, src)
    sources = [s["source"] for s in resp["stems"].values()]
    assert sources.count("savefig") == 2 and sources.count("pyplot") == 8
    assert not resp.get("dropped_figures")


def test_cap_boundary_with_savefig_claims_still_reports_the_excess(tmp_path):
    """savefig 认领 2 张 + 11 张兜底：兜底仍只留 8 张，丢 3 张。"""
    src = """\
import matplotlib.pyplot as plt

for i in range(13):
    fig = plt.figure(figsize=(2.0, 1.5))
    if i < 2:
        fig.savefig(f"saved{i}.png")
    plt.show()
    plt.close()
"""
    resp = build(tmp_path, src)
    sources = [s["source"] for s in resp["stems"].values()]
    assert sources.count("savefig") == 2 and sources.count("pyplot") == 8
    assert resp.get("dropped_figures") == 3
