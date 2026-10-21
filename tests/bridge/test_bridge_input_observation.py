"""桌面拥有的 bridge 的输入观察（#1047）：与 safe worker 同一个 `figcapture.InputObserver`、同一份报告形状。

native 成功记录凭它带上 helper / 数据依据、可被沿用（`launchctx.build_native_record`）。这里真起子进程验三件事：

* 观察到的就是脚本读的：`open` / `Path.read_text` / numpy 的 `loadtxt`（numpy 在观察器装好**之后**才被用户 import，
  那条通道靠 import 钩子补）都进 `files`，import 的本地模块进 `local_modules`，各带 sha256；
* 引擎自己读的不算：savefig 簿记给临时文件算摘要（#1124 的提升判据）读的是脚本自己写出的产物，不是输入；
* 不带 `--observe-inputs`（终端拥有的运行）一样都不观察。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

SCRIPT = """\
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import helper
here = Path(__file__).resolve().parent
with open(here / "a.txt") as f:
    a = float(f.read())
b = float((here / "b.txt").read_text())
arr = np.loadtxt("arr.txt")
fig, ax = plt.subplots()
ax.plot(arr * a * b * helper.SCALE)
fig.savefig("final.png.tmp", format="png")
os.replace("final.png.tmp", "final.png")
with open(here / "out.txt", "w") as f:
    f.write("written, not read")
"""


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(user_python, tmp_path, *, observe: bool):
    proj = tmp_path / "proj"
    write(proj / "fig.py", SCRIPT)
    write(proj / "helper.py", "SCALE = 2.0\n")
    write(proj / "a.txt", "1\n")
    write(proj / "b.txt", "3\n")
    write(proj / "arr.txt", "1 2 3\n")
    report = tmp_path / "report.json"
    args = ["--project-root", str(proj)] + (["--observe-inputs"] if observe else [])
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "fig.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        bridge_args=args,
    )
    assert r.returncode == 0, r.stderr
    return proj, json.loads(report.read_text(encoding="utf-8"))


def test_the_bridge_reports_the_files_and_local_modules_the_script_read(user_python, tmp_path):
    proj, data = _run(user_python, tmp_path, observe=True)
    inputs = data["observed_inputs"]
    assert inputs["truncated"] is False
    files = {f["path"]: f["sha256"] for f in inputs["files"]}
    # 三处 Python open + numpy 的 DataSource；写出的 out.txt 与 savefig 产物（临时件 / 改名后的终稿）都不是输入
    assert files == {n: _sha(proj / n) for n in ("a.txt", "arr.txt", "b.txt")}
    assert "numpy_datasource" in inputs["channels"]
    modules = {m["path"]: m["sha256"] for m in inputs["local_modules"]}
    assert modules["helper.py"] == _sha(proj / "helper.py")
    assert not any(p.startswith("..") or p.startswith("/") for p in [*files, *modules])
    assert (proj / "final.png").is_file()


def test_a_terminal_owned_run_observes_nothing(user_python, tmp_path):
    _proj, data = _run(user_python, tmp_path, observe=False)
    assert data["observed_inputs"] is None


SHOW_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import helper
plt.plot([1, 2, helper.SCALE])
plt.show()
{after}
"""


def _run_show(user_python, tmp_path, *, after: str):
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_SCRIPT.format(after=after))
    write(proj / "helper.py", "SCALE = 2.0\n")
    report = tmp_path / "report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "fig.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        bridge_args=["--project-root", str(proj), "--observe-inputs"],
    )
    assert r.returncode == 0, r.stderr
    return json.loads(report.read_text(encoding="utf-8"))


def test_a_helper_changed_while_the_script_sat_on_the_show_barrier_is_not_reported(
    user_python, tmp_path
):
    """脚本停在 `show()` 屏障上时 helper 被改：进程执行的是旧内容，定格却会哈希到新字节——不报观察（Codex #1180 P2）。"""
    data = _run_show(
        user_python,
        tmp_path,
        after='with open("helper.py", "w") as f:\n    f.write("SCALE = 3.0\\n")',
    )
    assert data["observed_inputs"] is None


def test_an_untouched_helper_across_the_show_barrier_is_still_reported(user_python, tmp_path):
    data = _run_show(user_python, tmp_path, after="")
    inputs = data["observed_inputs"]
    assert inputs is not None
    assert "helper.py" in {m["path"] for m in inputs["local_modules"]}
