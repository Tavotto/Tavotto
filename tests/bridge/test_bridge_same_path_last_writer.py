"""native bridge 也是「同一路径最后写入者胜」（Compatibility Guardian G2，三条入口同源）。

规则唯一实现 `figcapture.note_savefig_writer`；desktop worker / 浏览器的对应用例在
`tests/test_capture_same_path_last_writer.py`。
"""

from __future__ import annotations

import sys

import pytest

from support.bridgekit import write

pytestmark = pytest.mark.usefixtures("clean_env")

LOOP = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
for i in range(3):
    fig, ax = plt.subplots(figsize=(4 + i, 2.6))
    fig.savefig("shape.pdf"{kw})
    plt.close(fig)
plt.show()
"""


def _last_width(tmp_path, bridge_session, kw=""):
    proj = tmp_path / "proj"
    write(proj / "loop.py", LOOP.format(kw=kw))
    with bridge_session(proj / "loop.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        (desc,) = build["descriptors"]
        assert desc["stem"] == "shape"
        width = round(desc["size_mm"][0] / 25.4, 2)
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")
    return width


def test_native_loop_over_same_path_captures_last(tmp_path, bridge_session):
    assert _last_width(tmp_path, bridge_session) == 6.0  # i=2；先到先得时是 4.0


def test_native_same_file_with_format_kwarg_is_same_path(tmp_path, bridge_session):
    """`savefig("shape.pdf", format="pdf")` 与 `savefig("shape.pdf")` 是同一个文件。"""
    assert _last_width(tmp_path, bridge_session, kw=', format="pdf"') == 6.0


ALTERNATE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("x.pdf")
b.savefig("x.pdf")
a.savefig("x.png", dpi=150)
a.savefig("x.pdf")
plt.show()
"""


def test_native_alternating_writers_keep_all_winner_calls(tmp_path, bridge_session):
    proj = tmp_path / "proj"
    write(proj / "alt.py", ALTERNATE)
    with bridge_session(proj / "alt.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (desc,) = [d for d in sess.ensure_built()["descriptors"] if d["stem"] == "x"]
        assert round(desc["size_mm"][0] / 25.4, 2) == 4.0
        calls = desc["savefig_calls"]
        assert [c["format"] for c in calls] == ["pdf", "png", "pdf"]
        assert calls[1]["dpi"] == 150
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


BAD_SAVES = [
    'format="bogus"',
    'backend="nonexistent"',
    'backend="agg", format="bogus"',
    'dpi="not-a-number"',
]
FAILED_SAVE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("shape.pdf")
try:
    b.savefig("shape.pdf", {bad})
except Exception:
    pass
plt.show()
"""


@pytest.mark.parametrize("bad", BAD_SAVES)
def test_native_failed_save_does_not_displace(tmp_path, bridge_session, bad):
    proj = tmp_path / "proj"
    write(proj / "bad.py", FAILED_SAVE.format(bad=bad))
    with bridge_session(proj / "bad.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (desc,) = [d for d in sess.ensure_built()["descriptors"] if d["stem"] == "shape"]
        assert round(desc["size_mm"][0] / 25.4, 2) == 4.0
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


FAILED_FIRST = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
b, _ = plt.subplots(figsize=(5, 2.6))
a, _ = plt.subplots(figsize=(4, 2.6))
try:
    b.savefig("shape.pdf", {bad})
except Exception:
    pass
a.savefig("shape.pdf")
plt.show()
"""


@pytest.mark.parametrize("bad", BAD_SAVES)
def test_native_failed_first_claimant_is_replaced(tmp_path, bridge_session, bad):
    proj = tmp_path / "proj"
    write(proj / "first.py", FAILED_FIRST.format(bad=bad))
    with bridge_session(proj / "first.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (desc,) = [d for d in sess.ensure_built()["descriptors"] if d["stem"] == "shape"]
        assert round(desc["size_mm"][0] / 25.4, 2) == 4.0
        assert [c["format"] for c in desc["savefig_calls"]] == ["pdf"]
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


RECREATE = """\
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
a.savefig("x.pdf")
keep = open("x.pdf", "rb")  # 旧 inode 仍被打开着
os.unlink("x.pdf")
b.savefig("x.pdf")  # 新建同一路径：inode 变了，磁盘上 x.pdf 是 b
plt.show()
keep.close()
"""


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows 不允许 unlink 仍被打开的文件，场景造不出来"
)
def test_native_recreated_path_is_still_the_same_target(tmp_path, bridge_session):
    """A 写 x.pdf、旧 inode 仍被打开、unlink 后 B 新建 x.pdf：同一路径，捕获 B（Codex #1141 r4305717514）。"""
    proj = tmp_path / "proj"
    write(proj / "link.py", RECREATE)
    with bridge_session(proj / "link.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (desc,) = [d for d in sess.ensure_built()["descriptors"] if d["stem"] == "x"]
        assert round(desc["size_mm"][0] / 25.4, 2) == 5.0
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


LAZY = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
a, _ = plt.subplots(figsize=(4, 2.6))
b, _ = plt.subplots(figsize=(5, 2.6))
try:
    a.savefig("x.pdf", {first})
except Exception:
    pass
b.savefig("x.png")
plt.show()
"""


@pytest.mark.parametrize("first,expect", [('dpi="bad"', 5.0), ("dpi=100", 4.0)])
def test_native_failed_holder_write_does_not_block(tmp_path, bridge_session, first, expect):
    proj = tmp_path / "proj"
    write(proj / "lazy.py", LAZY.format(first=first))
    with bridge_session(proj / "lazy.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (desc,) = [d for d in sess.ensure_built()["descriptors"] if d["stem"] == "x"]
        assert round(desc["size_mm"][0] / 25.4, 2) == expect
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


OTHER_FIGURE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def first():
    fig, ax = plt.subplots(figsize=(2.0, 1.6))
    fig.savefig("panel.png", dpi=100)


def second():
    fig, ax = plt.subplots(figsize=(2.4, 1.6))
    fig.savefig("panel.pdf")
    plt.close(fig)


first()
second()
plt.show()
"""


def test_native_other_figure_on_the_same_stem_is_adopted(tmp_path, bridge_session):
    """`panel.png` 甲 / `panel.pdf` 乙（存完即关）：与 safe worker / 浏览器同样产出 `panel` + `panel-2`
    （`figcapture.adopt_displaced_writers`），show 屏障上就交出来。"""
    proj = tmp_path / "proj"
    write(proj / "other.py", OTHER_FIGURE)
    with bridge_session(proj / "other.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        desc = {d["stem"]: d for d in sess.ensure_built()["descriptors"]}
        assert sorted(desc) == ["panel", "panel-2"]
        assert round(desc["panel"]["size_mm"][0] / 25.4, 2) == 2.0
        assert round(desc["panel-2"]["size_mm"][0] / 25.4, 2) == 2.4
        assert [c["format"] for c in desc["panel-2"]["savefig_calls"]] == ["pdf"]
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


MUTATED_AFTER_SAVE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title("saved")
fig.savefig("mut.png")
ax.set_title("mutated")
keep, kx = plt.subplots(figsize=(2, 1.5))
keep.savefig("keep.png")
plt.show()
"""


def test_native_reports_a_figure_the_script_changed_after_saving(tmp_path, bridge_session):
    """真写盘的 native 入口与 safe worker 同一判据（`figcapture.mark_saved` / `note_modified_after_save`）。"""
    proj = tmp_path / "proj"
    write(proj / "mut.py", MUTATED_AFTER_SAVE)
    with bridge_session(proj / "mut.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        sess.ensure_built()
        assert sess.override("mut", [])["manifest"].get("modified_after_save") is True
        assert "modified_after_save" not in sess.override("keep", [])["manifest"]
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


STALE_PROBE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 1.5))
fig.savefig("probe.png")
print("stale-after-save", fig.stale)
fig.stale = False
ax.set_title("changed")
print("stale-after-change", fig.stale)
fig.savefig("probe.png")
print("stale-after-resave", fig.stale)
"""


def test_native_does_not_change_what_the_script_sees_in_fig_stale(tmp_path, user_python):
    """存盘后读 `fig.stale` 的脚本，在 native 下输出与裸 Python 完全一致（记录只挂回调，不写 `stale`）。"""
    import subprocess

    from support.bridgekit import child_env, run_runner
    from tavotto.engine import bridge

    proj = tmp_path / "proj"
    write(proj / "probe.py", STALE_PROBE)
    plain = subprocess.run(
        [user_python, str(proj / "probe.py")],
        cwd=str(proj),
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert plain.returncode == 0, plain.stderr
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "probe.py",
        cwd=str(proj),
        report=tmp_path / "report.json",
        out_dir=tmp_path / "out",
    )
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("stale-")]
    assert lines == [ln for ln in plain.stdout.splitlines() if ln.startswith("stale-")]
    assert lines == [
        "stale-after-save True",
        "stale-after-change True",
        "stale-after-resave True",
    ]
