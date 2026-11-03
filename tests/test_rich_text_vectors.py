"""标注两种写法（旧标记 / LaTeX 子集，ADR 0152）的跨语言看护：Python 侧跑同一份向量。

vitest 那侧是 `web/src/lib/richText.golden.test.ts`。导出上真的换了斜体 / 粗体脸，
看护在 `test_compose_text.py` 的 `test_latex_*`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto import richtext

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "tests" / "golden" / "rich_text_vectors.json"
VECTORS = json.loads(GOLDEN.read_text(encoding="utf-8"))["vectors"]


def _run(r: richtext.TextRun) -> dict:
    d: dict = {"text": r.text, "script": r.script}
    for key in ("italic", "bold", "math", "raw"):
        if getattr(r, key):
            d[key] = True
    return d


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vectors_match_python_side(vec):
    runs = richtext.parse_runs(vec["text"], vec["format"])
    assert [_run(r) for r in runs] == vec["runs"]
    assert richtext.serialize_latex(runs) == vec["latex"]
    assert richtext.plain_text(vec["text"], vec["format"]) == vec["plain"]
    assert richtext.glance_text(vec["text"], vec["format"]) == vec["glance"]


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_canonical_form_reads_back_to_the_same_runs(vec):
    """规范形再读一遍，除 raw 标记外片段不变（编辑框写回后画出来的就是写回前看到的）。"""

    def strip(rs):
        return [r._replace(raw=False) for r in rs]

    runs = richtext.parse_runs(vec["text"], vec["format"])
    assert strip(richtext.parse_runs(vec["latex"], "latex")) == strip(runs)


def test_legacy_reading_is_unchanged_without_format():
    """没有 text_format 的标注一字不差按旧标记读：LaTeX 命令与 `$` 都是字。"""
    assert richtext.parse_runs(r"\textit{T} $x$") == [richtext.TextRun(r"\textit{T} $x$", "")]


def test_generator_is_up_to_date():
    """向量文件是生成物：改了算法却没重跑生成器时，这里红。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_rich_text_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
