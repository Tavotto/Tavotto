"""#1124：native 里「先写临时文件再改名」的 savefig——只有**确认**这次执行把临时文件改名成了对应的产物、
内容就是这次写的，stem 才从 `final.png` 改成 `final`（原件按它找得到、编辑能写回）。

只凭文件名推断会把图关联到一份它没产出的旧原件上（Codex #1126 P1），所以每个反例都是「没有确认」的一种。
真跑 `engine/bridge_runner.py`（native 的 runner，与桌面 / `tavotto run` 同一个入口），读它的 `--report`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import figcapture

RUNNER = Path(figcapture.__file__).resolve().parent / "bridge_runner.py"

HEAD = """\
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [0, 1])
fig.savefig("final.png.tmp", format="png")
"""


def _stems(tmp_path: Path, body: str) -> list[str]:
    (tmp_path / "s.py").write_text(HEAD + body, encoding="utf-8")
    report = tmp_path / "report.json"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(RUNNER),
            "--target-kind",
            "script",
            "--target",
            "s.py",
            "--out-dir",
            str(tmp_path / "out"),
            "--report",
            str(report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return [f["stem"] for f in json.loads(report.read_text("utf-8"))["figures"]]


@pytest.mark.parametrize(
    "rename",
    [
        'os.replace("final.png.tmp", "final.png")\n',
        'os.rename("final.png.tmp", "final.png")\n',
        'Path("final.png.tmp").replace("final.png")\n',
        'import shutil\nshutil.move("final.png.tmp", "final.png")\n',
    ],
)
def test_a_confirmed_rename_takes_the_final_artifact_stem(tmp_path, rename):
    assert _stems(tmp_path, rename) == ["final"]
    assert figcapture.find_original_artifact(str(tmp_path), "final") == "final.png"


def test_no_rename_keeps_the_temp_stem_even_when_an_old_artifact_exists(tmp_path):
    """Codex #1126 P1 的原形：没改名、磁盘上有一份旧的 `final.png`——不许关联到它。"""
    (tmp_path / "final.png").write_bytes(b"old artifact from an earlier run")
    assert _stems(tmp_path, "") == ["final.png"]
    assert (tmp_path / "final.png").read_bytes() == b"old artifact from an earlier run"


def test_a_rename_to_another_name_is_not_a_promotion(tmp_path):
    assert _stems(tmp_path, 'os.replace("final.png.tmp", "final_v2.png")\n') == ["final.png"]


def test_a_final_file_overwritten_after_the_rename_is_not_this_figure(tmp_path):
    body = 'os.replace("final.png.tmp", "final.png")\nPath("final.png").write_bytes(b"other")\n'
    assert _stems(tmp_path, body) == ["final.png"]


def test_a_copy_that_leaves_the_temp_file_is_not_a_promotion(tmp_path):
    body = 'import shutil\nshutil.copyfile("final.png.tmp", "final.png")\n'
    assert _stems(tmp_path, body) == ["final.png"]


def test_copy_then_delete_is_judged_by_the_result_like_a_rename(tmp_path):
    """判据是结果（临时文件没了、最终文件就是这次写的字节），不是用了哪个函数。"""
    body = (
        'import shutil\nshutil.copyfile("final.png.tmp", "final.png")\nos.remove("final.png.tmp")\n'
    )
    assert _stems(tmp_path, body) == ["final"]


def test_a_final_stem_already_taken_by_another_figure_is_left_alone(tmp_path):
    body = (
        'os.replace("final.png.tmp", "final.png")\ng, bx = plt.subplots()\ng.savefig("final.pdf")\n'
    )
    assert _stems(tmp_path, body) == ["final.png", "final"]


def _barrier_stems(tmp_path: Path, body: str) -> list[list[str]]:
    """带控制通道跑（桌面 / `tavotto run` 的形态）：假的父进程握手、记下每个 `barrier` 事件交给界面的 stems，
    然后关掉连接（EOF = 父进程走了 → 屏障放开、脚本照常跑完）。"""
    import socket
    import threading

    (tmp_path / "s.py").write_text(HEAD + body, encoding="utf-8")
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    seen: list[list[str]] = []

    def serve():
        conn, _ = server.accept()
        with conn, conn.makefile("r", encoding="utf-8") as rf:
            assert json.loads(rf.readline())["token"] == "t0k"
            conn.sendall(b'{"ok": true}\n')
            for line in rf:
                msg = json.loads(line)
                if msg.get("bridge_event") == "barrier":
                    seen.append(msg["stems"])
                    return  # 关连接：屏障放开

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(RUNNER),
            "--target-kind",
            "script",
            "--target",
            "s.py",
            "--out-dir",
            str(tmp_path / "out"),
            "--control-port",
            str(port),
        ],
        cwd=tmp_path,
        env={**__import__("os").environ, "TAVOTTO_BRIDGE_TOKEN": "t0k"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    t.join(timeout=30)
    server.close()
    assert proc.returncode == 0, proc.stderr[-2000:]
    return seen


def test_a_rename_before_show_reaches_the_ui_as_the_final_stem(tmp_path):
    """Codex #1126 P1（r4295028867）：改名之后紧接着阻塞的 `plt.show()`——第一个屏障交给界面的就得是 `final`。"""
    body = 'os.replace("final.png.tmp", "final.png")\nplt.show()\n'
    assert _barrier_stems(tmp_path, body)[0] == ["final"]


def test_a_stem_published_before_the_rename_is_not_renamed_afterwards(tmp_path):
    """屏障上已按 `final.png` 交给界面、之后才改名：不再改（界面与会话已按旧名索引）。"""
    body = 'plt.show()\nos.replace("final.png.tmp", "final.png")\n'
    assert _barrier_stems(tmp_path, body)[0] == ["final.png"]


@pytest.mark.parametrize(
    "fname,target",
    [
        ("final.png.tmp", ("final.png", "final")),
        ("out/Fig1.PDF.temp", ("Fig1.PDF", "Fig1")),
        (b"plots/a.svg.part", ("a.svg", "a")),
        ("results.tmp", None),
        ("data.v2.tmp", None),
        ("Fig1.tmp.png", None),
        ("Fig1.pdf", None),
    ],
)
def test_temp_save_target_is_only_a_shape(fname, target):
    assert figcapture.temp_save_target(fname) == target
