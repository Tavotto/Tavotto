"""脚本自己检查 sys.argv。

worker 的内部参数（--script / --out-dir / --entry）绝不能漏给用户脚本：
按参数命名输出的脚本会存出一堆叫 "--entry" 的图。这里直接断言。
"""
import sys

import matplotlib.pyplot as plt


def main():
    extra = sys.argv[1:]
    assert not extra, f"脚本不该看到额外的命令行参数，却拿到 {extra!r}"
    assert sys.argv[0].endswith("argv_isolation.py"), sys.argv[0]
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 2, 3])
    ax.set_title(f"argc={len(sys.argv)}")
    fig.savefig("shape_argv.pdf")
