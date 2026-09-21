"""候选后端的两张生成表（统一实施包 U08，ADR 0067 / ADR 0060 §4 的 D07 迁移）：

* `src/tavotto/rendercore/canvas_coverage.json` —— 候选的三层覆盖区间表（`gen_canvas_coverage.py --backend rendercore`）；
* `tests/golden/glyph_plan_vectors.rendercore.json` —— 候选的字形归属向量（`gen_glyph_plan_vectors.py --backend rendercore`）。

三条判据：两张表与当前候选后端一致（生成器 `--check` 退出码 0）；候选向量与默认向量的差异**只落在批准的
那几类**（`fallback` 层消失：⁵ / ₂ 进 primary（Liberation 自带）、⁻ / 😀 / 𝛼 变 missing；primary / cjk / missing
的字符一个都不许换层，也不许有新的缺字）；差异的样例名单是闭集（多一个、少一个都红——「批准一次迁移」
不是「随便变」）。

默认那两张表由 `tests/test_glyph_plan.py` 看护（旧后端、主 `.venv`）；这里只管候选，rc-venv 里真跑。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VECTORS = ROOT / "tests" / "golden" / "glyph_plan_vectors.json"
CANDIDATE_VECTORS = ROOT / "tests" / "golden" / "glyph_plan_vectors.rendercore.json"
CANDIDATE_TABLE = ROOT / "src" / "tavotto" / "rendercore" / "canvas_coverage.json"
HAS_CANDIDATE = all(
    importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "fontTools")
)

#: 批准的迁移（ADR 0060 §4，D07）：只有这几条样例（每条 × 三个族）在两张向量表里不同。
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


def test_the_candidate_tables_exist_and_name_the_candidate_backend():
    table, vectors = _load(CANDIDATE_TABLE), _load(CANDIDATE_VECTORS)
    assert table["backend"] == "rendercore" and table["primary_is"]
    assert table["layers"]["fallback"] == []  # 能力边界（ADR 0060 §1），不是漏写
    assert vectors["backend_version"] == _load(CANDIDATE_TABLE)["backend_version"]
    assert len(vectors["vectors"]) == len(_load(DEFAULT_VECTORS)["vectors"])


def test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways():
    old = {v["name"]: v for v in _load(DEFAULT_VECTORS)["vectors"]}
    new = {v["name"]: v for v in _load(CANDIDATE_VECTORS)["vectors"]}
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


@pytest.mark.skipif(not _fonts_ready(), reason="候选包或批准字体未就绪（not_run，不是绿）")
@pytest.mark.parametrize(
    "script", ["scripts/gen_canvas_coverage.py", "scripts/gen_glyph_plan_vectors.py"]
)
def test_the_candidate_generators_check_clean_against_the_live_fonts(script: str):
    """生成物与当前候选后端一致：`--backend rendercore` 的 `--check` 退出码 0（主语是退出码）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), "--backend", "rendercore"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not _fonts_ready(), reason="候选包或批准字体未就绪（not_run，不是绿）")
def test_the_candidate_primary_layer_is_the_intersection_of_all_twelve_faces():
    """前端读的是一张与族 / 字重无关的表：它说「画得出」就得每一张脸都画得出。Liberation 各脸的 cmap
    差 16 个码位——只取 serif-regular 的表会让粗体 / 等宽上多出方框。"""
    from tavotto.rendercore import facade, typography

    prov = facade.provider()
    live = facade.coverage_ranges()  # 主语是函数本身；进了 git 的表由生成器 --check 那条看护
    primary = {c for lo, hi in live["primary"] for c in range(lo, hi + 1)}
    assert live == _load(CANDIDATE_TABLE)["layers"]
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
