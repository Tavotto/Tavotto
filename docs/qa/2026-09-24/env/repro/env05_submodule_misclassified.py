"""最小复现（ENV-05）：已安装的包在它**自己内部** import 一个不存在的子模块时，traceback 是
``No module named '<包>._sub'``；`pool.missing_module()` 取点号前的顶层名，于是把一个**装着的**包
报成「缺包」（`missing_dependency`，文案「脚本用到的 <包> 在当前渲染环境里没有，可以一键装上」）。

真实世界里同形的是 numpy 二进制坏掉时的 ``No module named 'numpy.core._multiarray_umath'``。

    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/env/repro/env05_submodule_misclassified.py

HTTP 层的同一现象见 ``logs/ENV-05.log``（`fig_broken_submodule`：包在 site-packages 里，渲染回
`missing_dependency` + `module=qa_subbroken_pkg`）。
合同：spec §3 ENV-05「"缺包" 与内部错误、二进制加载失败分类准确，不盲目安装」。
退出码：0 = 符合合同（不把已安装包内部的子模块缺失当成缺包）；1 = 复现到偏离。
"""

from __future__ import annotations

import os
import tempfile

# 绝不碰真实用户数据 / 配置目录：import tavotto 之前就钉到临时目录（没显式给的话）
for _var in ("TAVOTTO_DATA_DIR", "TAVOTTO_CONFIG_DIR"):
    os.environ.setdefault(_var, tempfile.mkdtemp(prefix=f"qa-env-{_var.lower()}-"))
os.environ["TAVOTTO_NO_TELEMETRY"] = "1"

import sys  # noqa: E402

from tavotto.engine import pool  # noqa: E402

CASES = {
    # 顶层包本身不在：这才是缺包
    "ModuleNotFoundError: No module named 'rdkit'": "rdkit",
    # 已安装包内部的子模块缺失（包在、坏了）：不该被说成「缺 numpy / 缺 qa_subbroken_pkg」
    "ModuleNotFoundError: No module named 'numpy.core._multiarray_umath'": "",
    "ModuleNotFoundError: No module named 'qa_subbroken_pkg._missing_sub'": "",
}


def main() -> int:
    bad = []
    for text, want in CASES.items():
        got = pool.missing_module(text)
        print(f"{text!r:80} -> missing_module={got!r} (合同期望 {want!r})")
        if got != want:
            bad.append(text)
    if bad:
        print(f"DEVIATION: {len(bad)} 条已安装包的内部子模块缺失被归为缺包")
        return 1
    print("CONTRACT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
