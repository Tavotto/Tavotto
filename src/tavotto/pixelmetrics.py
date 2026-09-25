"""像素差异指标 —— 写回像素门的**比较器**，与后端无关（统一实施包 U08，ADR 0067）。

`pdfbackend.compare_png` 的实现（U10 之前是 PyMuPDF 解码 / RenderCore 经 `rasterio` 解码两份，现在只剩后者）只负责
把 PNG 变成 RGBA 字节，**指标的算法只有这一份**：逐 RGBA 通道、每像素取通道最大差、底噪
`PNG_NOISE_FLOOR`，三指标 `changed_pixel_ratio / mean_abs_diff / max_abs_diff`。两个后端换的是
解码器，不是尺子——尺子在两边各写一份，等亮度换色这类分歧就可能只在一边被看见。

判据结构与 `scripts/ci/pixelcompare.py` 同构（底噪 3、三指标同名），刻意比它更严（那份比灰度）；
两份在灰度等值图上逐指标一致，`tests/test_pixel_compare.py` 的对拍用例钉住交集。

纯标准库。
"""

from __future__ import annotations

#: 噪声底噪：抗锯齿与 PNG 量化会让**完全相同的图形**出现 ±1~2 的逐像素抖动，
#: 不先扣掉它 changed_ratio 恒非零。取值与 `scripts/ci/pixelcompare.py` 相同。
PNG_NOISE_FLOOR = 3


def rgba_metrics(a: bytes, b: bytes, size_a: tuple[int, int], size_b: tuple[int, int]) -> dict:
    """两幅 RGBA 交错字节序列的差异指标；尺寸不同直接判为最大差异。

    阈值不在这里——这里只出指标，判定归调用方（app.py 的写回像素门）。
    """
    if size_a != size_b:
        return {
            "ok": False,
            "reason": "size_mismatch",
            "baseline_size": list(size_a),
            "candidate_size": list(size_b),
            "changed_pixel_ratio": 1.0,
            "mean_abs_diff": 255.0,
            "max_abs_diff": 255,
        }
    total = size_a[0] * size_a[1]
    if a == b or not total:  # 快路径：逐字节相同
        return {
            "ok": True,
            "changed_pixel_ratio": 0.0,
            "mean_abs_diff": 0.0,
            "max_abs_diff": 0,
            "changed_pixels": 0,
            "total_pixels": total,
            "raw_mean_abs_diff": 0.0,
        }
    changed = 0
    signal_sum = 0
    raw_sum = 0
    max_d = 0
    va, vb = memoryview(a), memoryview(b)
    for off in range(0, total * 4, 4):
        d = 0
        for c in range(4):  # 每像素取 RGBA 四通道最大差
            x, y = va[off + c], vb[off + c]
            dc = x - y if x >= y else y - x
            if dc > d:
                d = dc
        if d:
            raw_sum += d
            if d > max_d:
                max_d = d
            if d > PNG_NOISE_FLOOR:
                changed += 1
                signal_sum += d
    return {
        "ok": True,
        "changed_pixel_ratio": round(changed / total, 6),
        "mean_abs_diff": round(signal_sum / total, 4),
        "max_abs_diff": max_d,
        "changed_pixels": changed,
        "total_pixels": total,
        # 原始均值只作记录，不参与判定——排查时能看出「是不是整体偏了一点」
        "raw_mean_abs_diff": round(raw_sum / total, 4),
    }


__all__ = ["PNG_NOISE_FLOOR", "rgba_metrics"]
