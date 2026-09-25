"""QA §4 PATH 的 D1 夹具生成器（确定性、纯标准库、无随机数）。

用法（从 worktree 根目录）::

    python docs/qa/2026-09-24/path/repro/make_d1.py <目标目录> [--decoy] [--external <项目外目录>]

生成::

    <目标>/paper/scripts/entry.py        读相对路径 data/points.csv（只有 cwd = paper/ 才对）+ 本地模块 labmod
    <目标>/paper/scripts/labmod.py       本地模块（名字不是 PyPI 包；给点序列加 0）
    <目标>/paper/data/points.csv         正确 y = [0, 9, 2, 10]
    <目标>/paper/scripts/data/points.csv （--decoy）干扰 y = [0, 2, 9, 10]——极值、均值与正确那份相同
    <外部>/abs_points.csv                （--external）项目外绝对路径文件，y = [1, 8, 3, 7]

真值只看**全点序列**或输入文件 sha256，不看极值 / 均值 / ylim（两份的极值与均值刻意相同）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

X = [0, 1, 2, 3]
TRUE_Y = [0, 9, 2, 10]
DECOY_Y = [0, 2, 9, 10]
EXTERNAL_Y = [1, 8, 3, 7]

ENTRY = '''"""D1 入口：站在项目根（paper/）跑 `python scripts/entry.py` 才读得到 data/points.csv。"""
import csv

import labmod
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

xs, ys = [], []
with open("data/points.csv", encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        xs.append(float(row["x"]))
        ys.append(labmod.passthrough(float(row["y"])))
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, "o-", gid="d1-series")
ax.set_title(labmod.TITLE)
fig.savefig("entry.pdf")
'''

LABMOD = '''"""D1 本地模块：不是 PyPI 包，不许被当成缺失依赖去装。"""
TITLE = "D1 labmod"


def passthrough(v):
    return v + 0.0
'''


def csv_text(ys: list[int]) -> str:
    return "x,y\n" + "".join(f"{x},{y}\n" for x, y in zip(X, ys))


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build(dest: Path, *, decoy: bool = False, external: Path | None = None) -> dict:
    paper = dest / "paper"
    (paper / "scripts").mkdir(parents=True, exist_ok=True)
    (paper / "data").mkdir(parents=True, exist_ok=True)
    (paper / "scripts" / "entry.py").write_text(ENTRY, encoding="utf-8", newline="\n")
    (paper / "scripts" / "labmod.py").write_text(LABMOD, encoding="utf-8", newline="\n")
    true_bytes = csv_text(TRUE_Y).encode("utf-8")
    (paper / "data" / "points.csv").write_bytes(true_bytes)
    truth = {
        "x": X,
        "true_y": TRUE_Y,
        "true_sha256": sha256_bytes(true_bytes),
        "entry_sha256": sha256_bytes(ENTRY.encode("utf-8")),
    }
    if decoy:
        (paper / "scripts" / "data").mkdir(parents=True, exist_ok=True)
        decoy_bytes = csv_text(DECOY_Y).encode("utf-8")
        (paper / "scripts" / "data" / "points.csv").write_bytes(decoy_bytes)
        truth.update(decoy_y=DECOY_Y, decoy_sha256=sha256_bytes(decoy_bytes))
    if external is not None:
        external.mkdir(parents=True, exist_ok=True)
        ext_bytes = csv_text(EXTERNAL_Y).encode("utf-8")
        (external / "abs_points.csv").write_bytes(ext_bytes)
        truth.update(external_y=EXTERNAL_Y, external_sha256=sha256_bytes(ext_bytes))
    return truth


def native_reference(paper: Path, python: str, mplconfig: Path) -> None:
    """用户在终端里站在项目根跑过一次 `python scripts/entry.py`：磁盘上有原件 entry.pdf（面板列表扫磁盘产物）。"""
    import os
    import subprocess

    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(mplconfig), "MPLBACKEND": "Agg"}
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    proc = subprocess.run(
        [python, "scripts/entry.py"],
        cwd=paper,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    for pyc in paper.rglob("__pycache__"):
        import shutil

        shutil.rmtree(pyc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument("--decoy", action="store_true")
    ap.add_argument("--external")
    ap.add_argument(
        "--native-python", help="装了 matplotlib 的解释器：站在 paper/ 原生跑一次，产出原件"
    )
    a = ap.parse_args()
    truth = build(Path(a.dest), decoy=a.decoy, external=Path(a.external) if a.external else None)
    if a.native_python:
        native_reference(Path(a.dest) / "paper", a.native_python, Path(a.dest) / "mpl")
    print(json.dumps(truth, indent=2))


if __name__ == "__main__":
    main()
