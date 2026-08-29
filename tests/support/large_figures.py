#!/usr/bin/env python3
"""把 issue #181 的合成 fixture 摊成一个**可用的图库目录**。

Tavotto 的素材扫描（`app.scan_panels`）认的是**磁盘上的产物**：有 PDF 才有
面板，有面板 + 注册表才有「可参数化面板」。所以基准脚本要跑 #181 的复现，
光有脚本和注册表不够，还得有一份同名 PDF。

那份 PDF **不进仓库**（默认规模下它自己就是上百 MB）。这里现摊：脚本 +
注册表复制过去，再用一个**很小的 n** 跑一次产出占位 PDF——图幅（figsize）
与 n 无关，所以占位产物的页面尺寸与真实规模下逐位相同，而它只有几十 KB。
真正的规模由环境变量 `TAVOTTO_ISSUE181_MESH_N` 在**渲染时**决定。

用法：

    # 基准前先摊一份（用装了 matplotlib 的解释器）
    python tests/support/large_figures.py /tmp/issue181-lib \\
        --python /opt/homebrew/bin/python3

    # 然后按 #181 的量级跑基线
    TAVOTTO_ISSUE181_MESH_N=470 python scripts/bench_render.py \\
        --python .venv/bin/python --figures /tmp/issue181-lib \\
        --repeat 3 --plane python
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），而这个
# 探针把**带中文的 JSON** 打给父进程——第一次 print 就 UnicodeEncodeError，
# 退出码变成 1，于是所有用例只看得见「returned non-zero exit status 1」。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "large_figures"
SCRIPT_NAME = "issue_181_large_pcolormesh.py"
REGISTRY_NAME = "tavotto_registry.json"
STEM = "Issue181_large_pcolormesh"

#: 占位产物的 mesh 边长。只为让素材扫描认出这张图，**不参与任何测量**——
#: 图幅与 n 无关，所以页面尺寸与真实规模下的一样。
ARTIFACT_N = 4


def materialize(dest: str | Path, *, python: str | None = None) -> Path:
    """在 `dest` 里摊出「脚本 + 注册表 + 占位 PDF」，返回图库目录。

    `python` 是**用来生成占位 PDF 的解释器**（需要 matplotlib）；不给就用当前
    解释器——测试进程本身没有 matplotlib，调用方要传 worker 解释器进来。
    """
    figures = Path(dest)
    figures.mkdir(parents=True, exist_ok=True)
    for name in (SCRIPT_NAME, REGISTRY_NAME):
        shutil.copy2(FIXTURE_DIR / name, figures / name)

    # 占位产物由**子进程**产出：本进程（pytest / 基准脚本）与 Flask 父进程
    # 一样不 import matplotlib，这条边界不为了图方便破一次。
    subprocess.run(
        [
            python or sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import matplotlib; matplotlib.use('Agg');"
            "import issue_181_large_pcolormesh as fx;"
            "fx.build(int(sys.argv[2])).savefig(sys.argv[3])",
            str(figures),
            str(ARTIFACT_N),
            str(figures / f"{STEM}.pdf"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return figures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dest", help="图库目录（会被创建）")
    ap.add_argument("--python", default=None, help="生成占位产物的解释器（需要 matplotlib）")
    args = ap.parse_args(argv)
    out = materialize(args.dest, python=args.python)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
