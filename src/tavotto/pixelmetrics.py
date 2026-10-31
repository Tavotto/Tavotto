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


def diff_extent(a: bytes, b: bytes, size: tuple[int, int]) -> dict:
    """两幅同尺寸 RGBA 的**逐字节**差异范围：差异像素数、比例、包围框。

    与 `rgba_metrics` 的区别：这里**没有底噪**——任何一个通道差 1 也算差异像素，因为它回答的是
    「重画与原件是不是同一个像素」，不是「差得多不多」；也没有任何阈值，判定归调用方。
    纯标准库、整行大整数运算（C 速度，16 MP 的图也是毫秒~秒级，不逐像素循环）。
    包围框 `[x0, y0, x1, y1]`，x1/y1 开区间；没有差异时为 `None`。
    """
    width, height = size
    total = width * height
    if len(a) != total * 4 or len(b) != total * 4:
        raise ValueError("buffer length does not match the declared size")
    if a == b or not total:
        return {"differing_pixels": 0, "total_pixels": total, "differing_ratio": 0.0, "bbox": None}
    row = width * 4
    # 每个像素最低字节处放 0x7F / 0x80，其余字节为 0：用「(y & L) + L | y) & H」把非零字节折成最高位
    low = int.from_bytes(b"\x7f\x00\x00\x00" * width, "little")
    high = int.from_bytes(b"\x80\x00\x00\x00" * width, "little")
    count = 0
    x0, x1, y0, y1 = width, -1, height, -1
    for y in range(height):
        ra, rb = a[y * row : (y + 1) * row], b[y * row : (y + 1) * row]
        if ra == rb:
            continue
        x = int.from_bytes(ra, "little") ^ int.from_bytes(rb, "little")
        x |= (x >> 8) | (x >> 16) | (x >> 24)  # 四个通道折到每像素的最低字节
        x &= low | (low << 7)  # 只留每像素最低字节（0x7F|0x80 = 0xFF）
        flagged = (((x & low) + low) | x) & high
        count += bin(flagged).count("1")
        first = (flagged & -flagged).bit_length() - 1
        last = flagged.bit_length() - 1
        x0, x1 = min(x0, first // 32), max(x1, last // 32)
        y0, y1 = min(y0, y), y
    return {
        "differing_pixels": count,
        "total_pixels": total,
        "differing_ratio": count / total,
        "bbox": [x0, y0, x1 + 1, y1 + 1],
    }


__all__ = ["PNG_NOISE_FLOOR", "diff_extent", "rgba_metrics"]
