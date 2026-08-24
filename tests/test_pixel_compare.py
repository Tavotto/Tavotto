"""`pdfbackend.compare_png` —— 写回像素门的比较器（issue #81）。

这是仓库里像素差异算法的**第二份实现**，存在的理由是环境边界而不是口味：
`scripts/ci/pixelcompare.py` 跑在 CI（numpy + Pillow），而写回像素门跑在
Flask 父进程——它的依赖边界是 flask + pymupdf，wheel 不带科学栈，也 import
不到 scripts/。两份不许悄悄漂开：本文件末尾的对拍用例在同一组图上逐指标
比对两份实现的输出（与 patchspec ↔ Rust、telemetry 客户端 ↔ 代理同一套纪律）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from tavotto import pdfbackend

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"


def _png(path: Path, rows: list[int], width: int = 32) -> Path:
    """每行一个灰度值（写成等值 RGB）的测试图。

    等值 RGB 是有意的：PIL 的 `convert("L")` 与 MuPDF 的 RGB→GRAY 用的 luma
    权重系数不完全相同，但对 r==g==b 的像素两边都精确落回原值——对拍用例
    因此能做逐指标**相等**断言，而不是又造一个容差。
    """
    samples = b"".join(bytes([v, v, v]) * width for v in rows)
    pix = pymupdf.Pixmap(pymupdf.csRGB, width, len(rows), samples, False)
    pix.save(str(path))
    return path


def test_identical_images_report_zero_difference(tmp_path):
    a = _png(tmp_path / "a.png", [40] * 16 + [120] * 16)
    b = _png(tmp_path / "b.png", [40] * 16 + [120] * 16)
    got = pdfbackend.compare_png(a, b)
    assert got["ok"] is True
    assert got["changed_pixel_ratio"] == 0.0
    assert got["mean_abs_diff"] == 0.0
    assert got["max_abs_diff"] == 0
    assert got["total_pixels"] == 32 * 32


def test_sub_noise_jitter_is_not_a_change(tmp_path):
    """±2 的逐像素抖动（抗锯齿 / PNG 量化级）在底噪之内：changed 与 mean 归零，
    max 如实报出——三指标语义与 pixelcompare 相同（底噪只扣前两个）。"""
    a = _png(tmp_path / "a.png", [40] * 32)
    b = _png(tmp_path / "b.png", [42] * 32)
    got = pdfbackend.compare_png(a, b)
    assert got["changed_pixel_ratio"] == 0.0
    assert got["mean_abs_diff"] == 0.0
    assert got["max_abs_diff"] == 2
    assert got["raw_mean_abs_diff"] == 2.0


def test_a_real_change_is_measured(tmp_path):
    """半张图从 40 变成 200：ratio = 0.5，max = 160。"""
    a = _png(tmp_path / "a.png", [40] * 16 + [40] * 16)
    b = _png(tmp_path / "b.png", [40] * 16 + [200] * 16)
    got = pdfbackend.compare_png(a, b)
    assert got["changed_pixel_ratio"] == 0.5
    assert got["max_abs_diff"] == 160
    assert got["changed_pixels"] == 16 * 32
    assert got["mean_abs_diff"] == pytest.approx(160 * 0.5)


def test_size_mismatch_is_the_maximum_difference(tmp_path):
    a = _png(tmp_path / "a.png", [40] * 16)
    b = _png(tmp_path / "b.png", [40] * 32)
    got = pdfbackend.compare_png(a, b)
    assert got["ok"] is False and got["reason"] == "size_mismatch"
    assert got["changed_pixel_ratio"] == 1.0
    assert got["max_abs_diff"] == 255


def _ci_metrics(a: Path, b: Path) -> dict | None:
    """`scripts/ci/pixelcompare.compare` 在同一组图上的输出。

    先试当前解释器（CI 的 backend job 把 matplotlib 装进同一个 venv，numpy /
    Pillow 都在）；不行就借 worker 解释器起子进程；都没有才跳过。
    """
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); import pixelcompare;"
        "print(json.dumps(pixelcompare.compare("
        "__import__('pathlib').Path(sys.argv[2]),"
        "__import__('pathlib').Path(sys.argv[3]))))"
    )
    for exe in (sys.executable, _worker_python()):
        if not exe:
            continue
        proc = subprocess.run([exe, "-c", code, str(CI_DIR), str(a), str(b)],
                              capture_output=True, encoding="utf-8",
                              errors="replace", timeout=120)
        if proc.returncode == 0:
            return json.loads(proc.stdout.strip().splitlines()[-1])
    return None


def _worker_python() -> str | None:
    from tavotto.engine import pool
    try:
        return pool.find_worker_python()
    except Exception:  # noqa: BLE001 - 找不到就跳过对拍
        return None


def test_backend_comparator_matches_the_ci_implementation(tmp_path):
    """对拍：pdfbackend.compare_png 与 scripts/ci/pixelcompare 在同一组图上
    必须给出相同的判据输出——两份实现不许漂开（漂开的那天，写回像素门与
    CI 视觉门禁会对同一张图给出相反结论）。"""
    pairs = [
        ([40] * 16 + [120] * 16, [40] * 16 + [120] * 16),   # 完全相同
        ([40] * 32, [42] * 32),                             # 底噪之内
        ([40] * 16 + [40] * 16, [40] * 16 + [200] * 16),    # 真实差异
    ]
    ran = False
    for i, (rows_a, rows_b) in enumerate(pairs):
        a = _png(tmp_path / f"a{i}.png", rows_a)
        b = _png(tmp_path / f"b{i}.png", rows_b)
        ci = _ci_metrics(a, b)
        if ci is None:
            pytest.skip("numpy / Pillow 不可用（本机与 worker 解释器都没有）")
        ran = True
        ours = pdfbackend.compare_png(a, b)
        for key in ("changed_pixel_ratio", "mean_abs_diff", "max_abs_diff",
                    "changed_pixels", "total_pixels"):
            assert ours[key] == pytest.approx(ci[key]), (i, key, ours, ci)
    assert ran
    # 底噪常量也不许漂：这里断言的是数值本身（import 不到那份模块时也要看住）
    src = (CI_DIR / "pixelcompare.py").read_text(encoding="utf-8")
    assert "NOISE_FLOOR = 3" in src
    from tavotto.pdfbackend import pymupdf_backend
    assert pymupdf_backend.PNG_NOISE_FLOOR == 3
