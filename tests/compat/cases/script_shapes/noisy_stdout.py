"""往 stdout 狂打日志的脚本。worker 的 JSON 协议必须不被污染。"""
import sys

import matplotlib.pyplot as plt


def main():
    for i in range(200):
        print(f"[progress] step {i} of 200")
    print("warning: this goes to stderr", file=sys.stderr)
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(range(10))
    ax.set_title("Noisy")
    fig.savefig("shape_noisy.pdf")
