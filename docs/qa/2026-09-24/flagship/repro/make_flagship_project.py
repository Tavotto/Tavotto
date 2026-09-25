"""QA-FLAGSHIP-01 / ACCEPT-1 / ACCEPT-2 的夹具生成器（E1 双环境 + D1 分离目录与同名干扰 + G2 混合场景）。

确定性：无随机数；所有数值写死在本文件的 TRUTH 里。用法：

    python make_flagship_project.py <out_dir> --base-python /opt/homebrew/opt/python@3.13/libexec/bin/python3

产出（<out_dir> 下）：

* ``paper/``                          —— 用户项目（交给 Tavotto 的 ``--figures``）
  * ``scripts/entry.py``             —— 入口脚本：读相对路径 ``data/points.csv``（只有 cwd = 项目根才对）、
                                        import 本地模块 ``labmod``（scripts/ 里）、import 测试包 ``qa_signal``
                                        （只在两个 venv 里有，内置/系统环境没有）、读项目外绝对路径文件
  * ``scripts/labmod.py``            —— 本地模块（不是 PyPI 包）
  * ``scripts/data/points.csv``      —— **同名干扰**：y = [0, 2, 9, 10]
  * ``data/points.csv``              —— **真值**：y = [0, 9, 2, 10]（极值、均值与干扰相同）
  * ``.venv/``                       —— 环境 A（项目 venv）：``qa_signal`` 返回 A 序列
  * ``Fig_flagship.pdf``             —— 用户在终端里用 A 跑过一次留下的原件（素材卡片）
* ``envB/``                           —— 环境 B：同名 ``qa_signal`` 返回 B 序列；驱动脚本把它的 bin 放到 PATH 最前
* ``external/offsets.csv``            —— 项目外有效绝对路径数据
* ``truth.json``                      —— 独立真值 + 每个文件的 sha256

venv 用 ``uv venv --system-site-packages`` 建在给定的基础解释器上（matplotlib 来自基础解释器，
一个字节都不下载）；``qa_signal`` 由本脚本直接写进各自 site-packages（这是 harness 准备
「用户本来就有的环境」，不是替产品装包）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

TRUTH = {
    "t": [0, 1, 2, 3],
    "y_correct": [0, 9, 2, 10],
    "y_decoy": [0, 2, 9, 10],
    "signal_A": [1.0, 3.0, 2.0, 4.0],
    "signal_B": [3.0, 1.0, 4.0, 2.0],
    "y_changed": [1, 8, 3, 9],
    "offsets": [0.5, 1.5, 2.5, 3.5],
    "labmod_scale": 2.0,
    "ident_A": "A",
    "ident_B": "B",
}

ENTRY = '''"""QA 旗舰夹具：G2 混合场景（双子图 + 孪生轴 + 图例 + 注释 + 多文本 + 跨面板同名标题）。"""
import csv
import hashlib

import labmod
import matplotlib
import qa_signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

EXTERNAL = {external!r}

with open("data/points.csv", "rb") as fh:
    RAW = fh.read()
ts, ys = [], []
for row in csv.DictReader(RAW.decode("utf-8").splitlines()):
    ts.append(float(row["t"]))
    ys.append(float(row["y"]))
offs = []
with open(EXTERNAL, encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        offs.append(float(row["offset"]))
sig = qa_signal.values(len(ts))

fig, (ax1, ax3) = plt.subplots(1, 2, figsize=(7.0, 3.2))
ax1.plot(ts, ys, "o-", color="tab:blue", label="points")
ax1.set_title("Signal")
ax1.set_xlabel("t / s")
ax1.set_ylabel("y")
ax1.annotate("peak", xy=(1, ys[1]), xytext=(2.2, 6.0), arrowprops=dict(arrowstyle="->"))
ax2 = ax1.twinx()
ax2.plot(ts, sig, "s--", color="tab:red", label="sig " + qa_signal.IDENT)
ax2.set_ylabel("signal")
ax1.legend(loc="upper left")
ax3.bar(ts, labmod.scale(offs), color="tab:gray")
ax3.set_title("Signal")
ax3.set_xlabel("t / s")
fig.suptitle("QA flagship")
fig.text(0.01, 0.02, "src " + hashlib.sha256(RAW).hexdigest()[:12] + " env " + qa_signal.IDENT)
fig.subplots_adjust(left=0.08, right=0.97, bottom=0.18, top=0.82, wspace=0.45)
fig.savefig("Fig_flagship.pdf")
'''

LABMOD = '''"""本地模块（在 scripts/ 里，与 PyPI 无关）。"""


def scale(values):
    return [v * {scale!r} for v in values]
'''

QA_SIGNAL = '''"""E1 测试包：两个环境同名、返回不同数值。"""
import sys

IDENT = {ident!r}
_VALUES = {values!r}
EXECUTABLE = sys.executable


def values(n):
    return list(_VALUES[:n])
'''


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_venv(dest: Path, base_python: str, ident: str, values: list[float]) -> dict:
    subprocess.run(
        ["uv", "venv", "--quiet", "--python", base_python, "--system-site-packages", str(dest)],
        check=True,
    )
    py = dest / "bin" / "python"
    site = subprocess.run(
        [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pkg = Path(site) / "qa_signal"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(QA_SIGNAL.format(ident=ident, values=values), encoding="utf-8")
    ident_probe = subprocess.run(
        [
            str(py),
            "-c",
            "import json, sys, qa_signal, matplotlib; print(json.dumps({'executable': sys.executable, "
            "'prefix': sys.prefix, 'base_prefix': sys.base_prefix, 'qa_signal_file': qa_signal.__file__, "
            "'ident': qa_signal.IDENT, 'matplotlib': matplotlib.__version__}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(ident_probe.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--base-python", required=True)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    paper = out / "paper"
    (paper / "scripts" / "data").mkdir(parents=True)
    (paper / "data").mkdir(parents=True)
    (out / "external").mkdir(parents=True)

    def csv_rows(ys):
        return "t,y\n" + "".join(f"{t},{y}\n" for t, y in zip(TRUTH["t"], ys, strict=True))

    (paper / "data" / "points.csv").write_text(csv_rows(TRUTH["y_correct"]), encoding="utf-8")
    (paper / "scripts" / "data" / "points.csv").write_text(
        csv_rows(TRUTH["y_decoy"]), encoding="utf-8"
    )
    external = out / "external" / "offsets.csv"
    external.write_text(
        "t,offset\n"
        + "".join(f"{t},{o}\n" for t, o in zip(TRUTH["t"], TRUTH["offsets"], strict=True)),
        encoding="utf-8",
    )
    (paper / "scripts" / "entry.py").write_text(
        ENTRY.format(external=str(external)), encoding="utf-8"
    )
    (paper / "scripts" / "labmod.py").write_text(
        LABMOD.format(scale=TRUTH["labmod_scale"]), encoding="utf-8"
    )

    env_a = make_venv(paper / ".venv", args.base_python, "A", TRUTH["signal_A"])
    env_b = make_venv(out / "envB", args.base_python, "B", TRUTH["signal_B"])
    # 用户在终端里站在项目根、用项目环境跑过一次：原件落在项目根（素材卡片）
    subprocess.run(
        [str(paper / ".venv" / "bin" / "python"), "scripts/entry.py"],
        cwd=paper,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(out / "mplcfg"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    shutil.rmtree(out / "mplcfg", ignore_errors=True)

    files = {}
    for p in sorted(paper.rglob("*")):
        if p.is_file() and ".venv" not in p.relative_to(paper).parts:
            files[str(p.relative_to(out))] = sha256(p)
    files[str(external.relative_to(out))] = sha256(external)
    raw_correct = (paper / "data" / "points.csv").read_bytes()
    truth = {
        **TRUTH,
        "expected_provenance_text": "src "
        + hashlib.sha256(raw_correct).hexdigest()[:12]
        + " env A",
        "decoy_provenance_text": "src "
        + hashlib.sha256((paper / "scripts" / "data" / "points.csv").read_bytes()).hexdigest()[:12]
        + " env A",
        "env_A": env_a,
        "env_B": env_b,
        "files_sha256": files,
        "external_abs": str(external),
        "base_python": args.base_python,
        "generator_python": sys.version.split()[0],
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False), "utf-8")
    print(json.dumps({"out": str(out), "env_A": env_a, "env_B": env_b}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
