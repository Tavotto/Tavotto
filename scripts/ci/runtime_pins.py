#!/usr/bin/env python3
"""从 `packaging/runtime-lock.json` 取科学栈的精确版本，输出成 pip 能吃的形式。

**为什么实验室 CI 不能直接 `pip install matplotlib`**：视觉基线是像素级的，
matplotlib 换一个小版本就可能改掉抗锯齿、字体度量或默认样式，于是整片 corpus
在一次与产品毫无关系的依赖升级里同时变红。那种误报会直接摧毁这条门禁的可信度。

用锁文件而不是另写一份版本号，理由和 CLAUDE.md 里那条一样：**版本锁是唯一
输入**。桌面版用户拿到的内置 runtime 就是这些版本，CI 的渲染环境与之一致，
基线才真正代表「用户会看到的那张图」。

只取渲染相关的那几个包——CI 环境不需要复刻整个闭包（那是内置 runtime 的活），
但凡影响像素的都必须钉住。

用法：
    python scripts/ci/runtime_pins.py                 # matplotlib==3.11.1 numpy==2.5.2 …
    python scripts/ci/runtime_pins.py --format lines  # 每行一个
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK = REPO / "packaging" / "runtime-lock.json"

# 影响渲染像素的包。fonttools 与 pillow 看着不起眼，但前者决定字形度量、
# 后者决定 PNG 编码，两者都能让「同一张图」在像素上对不上。
RENDER_CRITICAL = ("matplotlib", "numpy", "pillow", "contourpy", "fonttools",
                   "kiwisolver", "cycler", "pyparsing")


def _packages(target: dict) -> dict[str, str]:
    """锁文件里一个 target 的 包名 → 版本。两种形状都认。"""
    raw = target.get("packages") or target.get("closure") or {}
    if isinstance(raw, dict):
        return {str(k).lower(): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"]).lower()] = str(item.get("version", ""))
    return out


def pins(lock_path: Path = LOCK) -> list[str]:
    """返回 ["matplotlib==3.11.1", ...]。

    取任意一个 target 即可：CLAUDE.md 明确记着**三个目标的闭包刻意逐字相同**
    （同版本的 matplotlib/numpy 才能让同一个脚本在两个平台画出同一张图，
    `test_all_targets_pin_the_same_versions` 看护这一点）。
    """
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    targets = data.get("targets") or {}
    if not targets:
        raise SystemExit(f"{lock_path} 里没有 targets——锁文件格式变了？")
    pkgs = _packages(next(iter(targets.values())))
    out = []
    for name in RENDER_CRITICAL:
        ver = pkgs.get(name)
        if ver:
            out.append(f"{name}=={ver}")
    if not any(p.startswith("matplotlib==") for p in out):
        raise SystemExit("锁文件里没有 matplotlib——视觉基线将失去版本保证，拒绝继续")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="输出与内置 runtime 一致的科学栈版本")
    ap.add_argument("--format", choices=["args", "lines"], default="args")
    args = ap.parse_args(argv)
    got = pins()
    print("\n".join(got) if args.format == "lines" else " ".join(got))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
