"""#1813 回归：issue 里的合成复现，穿过**真实公共入口**（真服务子进程 + 真用户 Python + 真文件的准备会话）。

场景：项目里有 `cache.csv`（内容 1）与 `data/values.csv`（内容 7）；脚本从一个不存在的目录读 `values.csv`（用户批准过数据改指，
指向 `data/values.csv`），再「写临时文件 → `os.replace` 覆盖 `cache.csv` → 读回」画图。

* 修复前：safe 读到链接的数据，`os.replace` 被守卫跳过、读回判 `safe_file_operation_blocked`，同会话退回 native；native 按 ADR 0106
  不用数据改指，读原本缺失的路径 `FileNotFoundError` → `script_error`（两条尝试：`safe` blocked、`native` script_error）。
* 修复后：safe 的暂存覆盖层接住写 / 改名 / 读回，**一次尝试直接成功**，没有 native 回退；读到的是链接数据，项目里的 `cache.csv`
  逐字节不变（内容与 mtime）。覆盖层够不着的观察（`os.stat`）仍按老规矩判 blocked 并回退——另有一条用例钉住。

独立可观测事实只来自脚本自己写在项目外的文件（`compute.log` 的执行次数、`observed.json` 读到的值），不来自被测接口的回显。
"""

from __future__ import annotations

import json
import time

from support import nativekit
from test_execution_fallback_e2e import ENV, _create, _run, _settled, sidecar_app

pytestmark = nativekit.needs_user_python

SCRIPT = """\
import json
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open({log!r}, "a") as f:
    f.write("x")
source = Path(os.environ["SYNTHETIC_INPUT_DIR"]) / ("values" + ".csv")
data = int(source.read_text())
root = Path(__file__).resolve().parent
tmp, final = root / "cache.tmp", root / "cache.csv"
tmp.write_text("2")
os.replace(tmp, final)
value = int(final.read_text())
{extra}
with open({observed!r}, "w") as f:
    json.dump({{"data": data, "value": value}}, f)
plt.plot([0, data], [0, value])
"""


def _world(tmp_path, extra: str = ""):
    root = tmp_path / "project"
    root.mkdir()
    (root / "cache.csv").write_text("1", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "values.csv").write_text("7", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    missing_dir = tmp_path / "missing"
    (root / "fig.py").write_text(
        SCRIPT.format(
            log=str(out / "compute.log"), observed=str(out / "observed.json"), extra=extra
        ),
        encoding="utf-8",
    )
    return root, out, missing_dir


def _snapshot(root):
    """用户自己的文件（运行前就在的）逐字节 + mtime；Tavotto 自己的登记 / 缓存文件不在其中。"""
    names = ("cache.csv", "data/values.csv")
    return {n: ((root / n).read_bytes(), (root / n).stat().st_mtime_ns) for n in names}


def _link(app, missing_dir, root):
    status, link = app.call(
        "/api/engine/input-remap",
        {
            "requested": str(missing_dir / "values.csv"),
            "chosen": str(root / "data" / "values.csv"),
            "chosen_kind": "file",
        },
    )
    assert status == 200, link


def test_the_issue_script_succeeds_in_one_safe_attempt_and_leaves_the_project_untouched(tmp_path):
    root, out, missing_dir = _world(tmp_path)
    env = {**ENV, "SYNTHETIC_INPUT_DIR": str(missing_dir)}
    before = _snapshot(root)
    with sidecar_app(root, tmp_path / "work", env_overrides=env) as app:
        _link(app, missing_dir, root)
        final = _settled(app, _run(app, _create(app)))
        time.sleep(2)
    assert final["phase"] == "completed", final
    assert [(h["profile"], h["outcome"]) for h in final["attempt_history"]] == [
        ("safe", "succeeded")
    ]
    assert (out / "compute.log").read_text() == "x", "脚本只执行一次：没有 native 回退"
    assert json.loads((out / "observed.json").read_text()) == {"data": 7, "value": 2}
    assert _snapshot(root) == before, "用户项目被改动了（内容或 mtime）"
    assert not (root / "cache.tmp").exists(), "临时文件没有落进项目"


def test_an_uncovered_readback_still_blocks_and_falls_back(tmp_path):
    """覆盖层够不着 `os.stat`：safe 照旧判 blocked，native 里 `os.replace` 真实发生（回退路径不受影响）。"""
    root, out, missing_dir = _world(tmp_path, extra="os.stat(final)")
    env = {**ENV, "SYNTHETIC_INPUT_DIR": str(root / "data")}
    with sidecar_app(root, tmp_path / "work", env_overrides=env) as app:
        final = _settled(app, _run(app, _create(app)))
        time.sleep(2)
    assert [(h["profile"], h["outcome"]) for h in final["attempt_history"]][0] == (
        "safe",
        "blocked",
    )
    assert (root / "cache.csv").read_text() == "2", "native 里真实发生"
