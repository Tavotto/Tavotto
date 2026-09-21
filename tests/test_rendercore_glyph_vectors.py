"""字形归属的两张生成表在 U10 切默认之后（ADR 0072；D07 迁移见 ADR 0060 §4 / 0067）：

* `src/tavotto/pdfbackend/canvas_coverage.json` —— 前端「这个字导出后是不是方框」读的那张三层覆盖区间表
  （`gen_canvas_coverage.py`；U08 时它是 `rendercore/canvas_coverage.json` 候选表，U10 换到默认落点）；
* `tests/golden/glyph_plan_vectors.json` —— 字形归属向量（`gen_glyph_plan_vectors.py`；pytest 与 vitest 各跑一遍）。

三条判据：两张表与当前后端 + 批准字体一致（生成器 `--check` 退出码 0）；表与向量相对**退役前的旧表**
（批准资产 `evidence/u10/*.pymupdf.json`，PyMuPDF 1.28.2）的差异**只落在批准的那几类**（`fallback` 层消失：
⁵ / ₂ 进 primary（Liberation 自带）、⁻ / 😀 / 𝛼 变 missing；primary / cjk / missing 的字符一个都不许换层，
也不许有新的缺字）；差异的样例名单是闭集（多一个、少一个都红——「批准一次迁移」不是「随便变」）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "golden" / "glyph_plan_vectors.json"
TABLE = ROOT / "src" / "tavotto" / "pdfbackend" / "canvas_coverage.json"
ARCHIVE = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u10"
LEGACY_VECTORS = ARCHIVE / "glyph_plan_vectors.pymupdf.json"
LEGACY_TABLE = ARCHIVE / "canvas_coverage.pymupdf.json"
HAS_CANDIDATE = all(
    importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "fontTools")
)

#: 批准的迁移（ADR 0060 §4，D07）：只有这几条样例（每条 × 三个族）在新旧向量表里不同。
APPROVED_DIFFERENT_CASES = frozenset(
    {
        "multiply-sign",  # ⁵：fallback → primary（Liberation 自带上标 5）
        "subscript",  # ₂：fallback → primary
        "mixed-cjk-latin",  # ⁵：同上
        "unit-negative-exponent",  # ⁻：fallback → missing（靠合成上下标，ADR 0060 §1）
        "mixed-scripts",  # ⁵ 进 primary、⁻ 变 missing
        "emoji-surrogate-pair",  # 😀：fallback → missing（没有隐式回退脸）
        "math-alphanumeric",  # 𝛼：同上
    }
)


def _fonts_ready() -> bool:
    if not HAS_CANDIDATE:
        return False
    from tavotto.rendercore import fonts

    return not fonts.FontRegistry.discover().missing


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_default_tables_name_the_rendercore_backend_and_have_no_fallback_layer():
    table, vectors = _load(TABLE), _load(VECTORS)
    assert table["backend"] == "rendercore" and table["primary_is"]
    assert table["layers"]["fallback"] == []  # 能力边界（ADR 0060 §1），不是漏写
    assert vectors["backend_version"] == table["backend_version"]
    legacy = _load(LEGACY_TABLE)
    assert legacy["backend"] == "pymupdf" and legacy["layers"]["fallback"], "存档的旧表不是旧后端的"
    assert len(vectors["vectors"]) == len(_load(LEGACY_VECTORS)["vectors"])


def test_vectors_differ_from_the_retired_backend_only_in_the_approved_ways():
    old = {v["name"]: v for v in _load(LEGACY_VECTORS)["vectors"]}
    new = {v["name"]: v for v in _load(VECTORS)["vectors"]}
    assert set(old) == set(new)
    different = set()
    for name, a in old.items():
        b = new[name]
        assert a["text"] == b["text"] and a["family"] == b["family"], name
        old_layer = {}
        for run in a["runs"]:
            for ch in run["text"]:
                old_layer[ch] = run["layer"]
        new_layer = {}
        for run in b["runs"]:
            for ch in run["text"]:
                new_layer[ch] = run["layer"]
        assert set(old_layer) == set(new_layer), name
        for ch, was in old_layer.items():
            now = new_layer[ch]
            if was == now:
                continue
            # 只许 fallback → primary / cjk / missing；primary / cjk / missing 的字符一个都不许换层
            assert was == "fallback" and now in ("primary", "cjk", "missing"), (name, ch, was, now)
        if a["runs"] != b["runs"] or a["missing"] != b["missing"]:
            different.add(name.split("/", 1)[0])
        # 新的缺字只能来自原来的 fallback 层
        for ch in set(b["missing"]) - set(a["missing"]):
            assert old_layer.get(ch) == "fallback", (name, ch)
    assert different == APPROVED_DIFFERENT_CASES, sorted(different ^ APPROVED_DIFFERENT_CASES)


def test_the_coverage_table_lost_only_the_fallback_layer_and_gained_only_approved_primary():
    """覆盖表那一侧同一条闭集判据（在切默认那一刻对着两张表量出来的数字，ADR 0072）：旧 primary 653 个码位 →
    新 2305（+1668 / −16；ADR 0060 §1 的 +1684 是 U06 evidence 那张按 serif-regular 出的表，这张是 12 脸交集），
    旧 cjk 34 012 → 30 887（Hangul 等不在），旧 fallback 27 610 → 0。数字变了就是字体集合 / 算法变了，
    先改 ADR 0060 §1 / 0072 再改这里。"""

    def cps(layers: dict, name: str) -> set[int]:
        return {c for lo, hi in layers[name] for c in range(lo, hi + 1)}

    old, new = _load(LEGACY_TABLE)["layers"], _load(TABLE)["layers"]
    assert (len(cps(old, "primary")), len(cps(new, "primary"))) == (653, 2305)
    assert len(cps(new, "primary") - cps(old, "primary")) == 1668
    assert len(cps(old, "primary") - cps(new, "primary")) == 16
    assert (len(cps(old, "cjk")), len(cps(new, "cjk"))) == (34012, 30887)
    assert (len(cps(old, "fallback")), len(cps(new, "fallback"))) == (27610, 0)


@pytest.mark.skipif(not _fonts_ready(), reason="候选包或批准字体未就绪（not_run，不是绿）")
@pytest.mark.parametrize(
    "script", ["scripts/gen_canvas_coverage.py", "scripts/gen_glyph_plan_vectors.py"]
)
def test_the_generators_check_clean_against_the_live_fonts(script: str):
    """生成物与当前后端一致：`--check` 退出码 0（主语是退出码）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not _fonts_ready(), reason="候选包或批准字体未就绪（not_run，不是绿）")
def test_the_primary_layer_is_the_intersection_of_all_twelve_faces():
    """前端读的是一张与族 / 字重无关的表：它说「画得出」就得每一张脸都画得出。Liberation 各脸的 cmap
    差 16 个码位——只取 serif-regular 的表会让粗体 / 等宽上多出方框。"""
    from tavotto.rendercore import facade, typography

    prov = facade.provider()
    live = facade.coverage_ranges()  # 主语是函数本身；进了 git 的表由生成器 --check 那条看护
    primary = {c for lo, hi in live["primary"] for c in range(lo, hi + 1)}
    assert live == _load(TABLE)["layers"]
    faces = [
        prov.face_for(fam, b, i)
        for fam in typography.CANVAS_TEXT_FAMILIES
        for b in (False, True)
        for i in (False, True)
    ]
    assert len(faces) == 12
    only_serif = {cp for cp in range(0x20, 0x3000) if faces[0].covers(cp)}
    everyone = {cp for cp in only_serif if all(f.covers(cp) for f in faces)}
    assert everyone < only_serif, "各脸 cmap 完全相同——交集判据量在空差集上"
    assert primary & only_serif == everyone
