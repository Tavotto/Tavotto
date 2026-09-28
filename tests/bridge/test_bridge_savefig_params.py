"""native bridge 也把 savefig 的实效参数记进描述符（`savefig_calls`，tight 图幅的第一步）。

native 的 savefig 是透传（用户的文件照常写），记账与 safe worker / 浏览器是同一条
规则（`figcapture.record_savefig_call`）。它与另外两条入口的差别只在同步时机：钩子
写模块级表，会话在第一个屏障才建——这里钉住屏障处 build 出来的描述符里有它。
"""

from __future__ import annotations

import re

import pytest

from support.bridgekit import write

pytestmark = pytest.mark.usefixtures("clean_env")

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([1, 2, 3])
ax.set_xlabel("Time (s)")
fig.savefig("Fig1.pdf", bbox_inches="tight", pad_inches=0.02)
plt.show()
"""


def test_native_descriptor_carries_the_savefig_calls(tmp_path, bridge_session):
    proj = tmp_path / "proj"
    write(proj / "paper.py", SCRIPT)
    with bridge_session(proj / "paper.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        (desc,) = build["descriptors"]
        assert desc["execution_profile"] == "native"
        assert desc["capture_source"] == "savefig"
        assert desc["savefig_calls"] == [
            {
                "format": "pdf",
                "bbox_inches": "tight",
                "pad_inches": 0.02,
                "dpi": "figure",
                "transparent": False,
                "facecolor": "auto",
                "edgecolor": "auto",
                "bbox_extra_artists": None,
            }
        ]
        assert (proj / "Fig1.pdf").is_file(), "native 的 savefig 是透传：用户的文件照常写出"
        # 图幅（ADR 0098）：native 也按脚本存盘的裁切框——与刚透传写出的那份 PDF 同一页
        m = re.search(
            rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)",
            (proj / "Fig1.pdf").read_bytes(),
        )
        page = [round((float(m.group(i + 2)) - float(m.group(i))) / 72 * 25.4, 2) for i in (1, 2)]
        assert desc["size_mm"] == pytest.approx(page, abs=0.02)
        sess.resume()
        sess.wait_event("barrier")  # 脚本跑完那次
        sess.resume()
        sess.wait_event("exit")


LATE_SAVE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([1, 2, 3])
plt.show()
fig.savefig("paper.pdf", bbox_inches="tight", pad_inches=0.02)
plt.show()
"""


def _page_mm(path) -> list[float]:
    m = re.search(
        rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)",
        path.read_bytes(),
    )
    return [round((float(m.group(i + 2)) - float(m.group(i))) / 72 * 25.4, 2) for i in (1, 2)]


def test_a_figure_shown_first_and_saved_later_is_a_savefig_capture(tmp_path, bridge_session):
    """第一个屏障按 pyplot 兜底捕获（stem = 脚本名），继续之后脚本把同一张图按同一个 stem
    存了盘：第二个屏障里它是 savefig 捕获、调用照记——不许还报「从没存过盘」（`[]`）。

    图幅（ADR 0098）也跟上：它在第一个屏障就有了 FigState，`instrument_all()` 不再管它；
    第二个屏障重放之前按那次 tight 调用挂上图幅，页面 = 脚本写出的原件，不再按 figsize 切边。"""
    proj = tmp_path / "proj"
    write(proj / "paper.py", LATE_SAVE)
    with bridge_session(proj / "paper.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (first,) = sess.ensure_built()["descriptors"]
        assert (first["stem"], first["capture_source"], first["savefig_calls"]) == (
            "paper",
            "pyplot",
            [],
        )
        sess.resume()
        sess.wait_event("barrier")
        (desc,) = sess.ensure_built()["descriptors"]
        assert desc["stem"] == "paper"
        assert desc["capture_source"] == "savefig"
        assert [(c["format"], c["bbox_inches"]) for c in desc["savefig_calls"]] == [
            ("pdf", "tight")
        ]
        page = _page_mm(proj / "paper.pdf")
        assert page != pytest.approx([76.2, 50.8], abs=0.5), "前提：tight 原件确实不是 figsize"
        assert desc["size_mm"] == pytest.approx(page, abs=0.02)
        assert first["size_mm"] == [76.2, 50.8]
        sess.resume()
        sess.wait_event("barrier")  # 脚本跑完那次
        sess.resume()
        sess.wait_event("exit")
