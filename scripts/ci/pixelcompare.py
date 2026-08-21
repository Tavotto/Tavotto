#!/usr/bin/env python3
"""像素比较：**全仓库唯一**的一份位图差异算法与判据。

原本长在 `visual_regression.py` 里（golden 视觉回归）。CompatBench 的
「零 patch 原生保真度」要问的是另一个问题——**原生 matplotlib vs Tavotto
零 override**——但「两张 PNG 差多少算差」这件事必须只有一个答案。各写各的
最直接的后果是两条门禁对同一张图给出相反结论，而看报告的人无从判断该信谁；
更隐蔽的是阈值悄悄漂开，某一侧变成永远不会红的摆设。

三个指标同时看（缺一不可）：

* `changed_pixel_ratio` —— 抓「大片变了」；
* `mean_abs_diff` —— 抓「整体偏移」；
* `max_abs_diff` —— 抓「一小块彻底变了」。

底噪 3 在三个指标里都先扣掉：抗锯齿与 PNG 量化会让**完全相同的图形**出现
±1~2 的逐像素抖动，不扣的话 changed_ratio 恒非零、mean_abs 被整幅图的噪声
抬起来（实测每像素 ±2 的均匀噪声就贡献 1.2 的均值，顶穿任何合理阈值，而
画面其实一模一样）。

比灰度不比 RGB 也是有意的：色相的细微变化几乎总是抗锯齿造成的，而真正的
回归（元素挪位、消失、字号变了）在灰度上同样显眼——这让阈值好定，diff 图
也好读。
"""
from __future__ import annotations

from pathlib import Path

#: 噪声底噪。三个指标都先扣掉它再算（见模块头）。
NOISE_FLOOR = 3


class MissingImagingDeps(RuntimeError):
    """numpy / Pillow 不在。调用方决定这是「跳过」还是「失败」。"""


def _imaging():
    """numpy + Pillow，缺了就抛 `MissingImagingDeps`。

    **所有**用到这两个包的地方都必须经过它。`compare()` 里曾经另有一句裸的
    `import numpy as np`——缺依赖时那句抛的是 ModuleNotFoundError，
    穿透调用方为 MissingImagingDeps 准备的「跳过」分支，一路变成
    「runner 崩了」。缺依赖是环境问题，不该表现成产品缺陷。
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:                       # pragma: no cover - 环境相关
        raise MissingImagingDeps(
            "像素比较需要 numpy 与 Pillow：pip install -e '.[ci]'") from exc
    return np, Image


def load_pixels(path: Path):
    """PNG → (numpy 灰度数组, (宽, 高))。"""
    np, Image = _imaging()
    with Image.open(path) as im:
        gray = im.convert("L")
        return np.asarray(gray, dtype="int16"), gray.size


def compare(baseline: Path, candidate: Path, diff_out: Path | None = None) -> dict:
    """两张 PNG 的差异指标。尺寸不同直接判为最大差异——那本身就值得看。"""
    np, _Image = _imaging()
    a, size_a = load_pixels(baseline)
    b, size_b = load_pixels(candidate)
    if size_a != size_b:
        return {"ok": False, "reason": "size_mismatch",
                "baseline_size": list(size_a), "candidate_size": list(size_b),
                "changed_pixel_ratio": 1.0, "mean_abs_diff": 255.0,
                "max_abs_diff": 255}

    delta = np.abs(a - b)
    signal = np.where(delta > NOISE_FLOOR, delta, 0)
    changed = int((delta > NOISE_FLOOR).sum())
    total = int(delta.size)
    metrics = {
        "ok": True,
        "changed_pixel_ratio": round(changed / total, 6),
        "mean_abs_diff": round(float(signal.mean()), 4),
        "max_abs_diff": int(delta.max()),
        "changed_pixels": changed,
        "total_pixels": total,
        # 原始均值只作记录，不参与判定——排查时能看出「是不是整体偏了一点」。
        "raw_mean_abs_diff": round(float(delta.mean()), 4),
    }
    if diff_out is not None:
        try:
            _np, Image = _imaging()
            # 差异放大 4 倍再反相：肉眼看得清「哪里变了」，而不是一片近黑。
            vis = np.clip(delta.astype("int32") * 4, 0, 255).astype("uint8")
            Image.fromarray(255 - vis, mode="L").save(diff_out)
        except Exception:                            # noqa: BLE001 - diff 图是辅助产物
            pass
    return metrics


def verdict(metrics: dict, tol: dict) -> tuple[bool, list[str]]:
    """三个指标任一越界即回归。返回 (通过?, 人话理由列表)。"""
    if not metrics.get("ok", True):
        return False, [f"尺寸不一致：基线 {metrics['baseline_size']} "
                       f"vs 候选 {metrics['candidate_size']}"]
    bad: list[str] = []
    for key, label in (("changed_pixel_ratio", "变化像素占比"),
                       ("mean_abs_diff", "平均绝对差"),
                       ("max_abs_diff", "最大绝对差")):
        if key in tol and metrics[key] > tol[key]:
            bad.append(f"{label} {metrics[key]} > 阈值 {tol[key]}")
    return (not bad), bad
