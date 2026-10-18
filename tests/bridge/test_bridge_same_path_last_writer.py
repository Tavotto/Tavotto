"""native bridge 也是「同一路径最后写入者胜」（Compatibility Guardian G2，三条入口同源）。

规则唯一实现 `figcapture.note_savefig_writer`；desktop worker / 浏览器的对应用例在
`tests/test_capture_same_path_last_writer.py`。
"""

from __future__ import annotations

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
