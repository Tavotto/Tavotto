"""Figure-font fallback: preserve the script chain across hot edits and replay."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[1]


def test_family_selection_replays_and_restores_the_original_chain():
    try:
        worker = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("no matplotlib worker")
    probe = r"""
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import overrides

def make():
    fig, ax = plt.subplots()
    text = ax.set_title("Text")
    text.set_fontfamily(["DejaVu Sans", "DejaVu Sans Mono"])
    state = overrides.FigState(fig)
    state.index["title"] = text
    return state, text

def patch(primary, fallback):
    return [{"gid": "title", "prop": "fontfamily",
             "value": {"primary": primary, "fallback": fallback}}]

state, text = make()
baseline = list(text.get_fontfamily())
baseline_math = text.get_math_fontfamily()
assert not overrides.apply(state, patch(None, "DejaVu Serif"))
assert text.get_fontfamily() == ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono"]
assert text.get_math_fontfamily() == baseline_math
assert not overrides.apply(state, patch("DejaVu Sans Mono", "DejaVu Serif"))
hot = list(text.get_fontfamily())
assert hot == ["DejaVu Sans Mono", "DejaVu Serif", "DejaVu Sans Mono"]
fresh, fresh_text = make()
assert not overrides.apply(fresh, patch("DejaVu Sans Mono", "DejaVu Serif"))
assert fresh_text.get_fontfamily() == hot
assert not overrides.apply(state, [])
assert text.get_fontfamily() == baseline
assert text.get_math_fontfamily() == baseline_math
"""
    result = subprocess.run(
        [worker, "-c", probe, str(ROOT / "src" / "tavotto" / "engine")],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=os.environ.copy(),
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_latin_selection_excludes_chinese_punctuation_but_not_fullwidth_numbers(tmp_path):
    """Probe actual cmap faces, including registration invalidation and all styles."""
    try:
        worker = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("no matplotlib worker")
    probe = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fontTools.ttLib import TTFont
from matplotlib import font_manager
import manifest

src = font_manager.findfont('DejaVu Sans')
out = Path(sys.argv[2])
def add_font(family, filename, extra, register=True):
    font = TTFont(src)
    for name in font['name'].names:
        if name.nameID in (1, 4, 6, 16):
            name.string = family.encode(name.getEncoding(), errors='replace')
    for table in font['cmap'].tables:
        if table.isUnicode() and table.format != 14:
            for cp in extra:
                if cp <= 0xFFFF or table.format in (12, 13):
                    table.cmap[cp] = table.cmap[ord('A')]
    path = out / filename
    font.save(path)
    if register:
        font_manager.fontManager.addfont(str(path))

assert manifest._chinese_font_codepoint(0x3001)
assert manifest._chinese_font_codepoint(0xFF0C)
assert manifest._chinese_font_codepoint(0x20000)
assert not manifest._chinese_font_codepoint(0xFF11)
assert not manifest._chinese_font_codepoint(0xFF21)
add_font('TavottoTestLatinOnly', 'latin.ttf', [0xFF11, 0xFF21])
assert 'TavottoTestLatinOnly' in manifest.installed_latin_only_families()
add_font('TavottoTestLatinOnly', 'latin.ttf', [0xFF0C], register=False)
assert 'TavottoTestLatinOnly' not in manifest.installed_latin_only_families()
add_font('TavottoTestWithPunctuation', 'punct.ttf', [0x3001])
assert 'TavottoTestWithPunctuation' not in manifest.installed_latin_only_families()
add_font('TavottoTestLatinOnly', 'samefamily.ttf', [0xFF0C])
assert 'TavottoTestLatinOnly' not in manifest.installed_latin_only_families()
"""
    result = subprocess.run(
        [worker, "-c", probe, str(ROOT / "src" / "tavotto" / "engine"), str(tmp_path)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=os.environ.copy(),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_collection_faces_are_checked_by_registered_index(tmp_path):
    """A CJK face after a Latin-only face must never inherit face 0's verdict."""
    try:
        worker = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("no matplotlib worker")
    probe = r"""
import inspect, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fontTools.ttLib import TTCollection, TTFont
from matplotlib import font_manager
import manifest

src = font_manager.findfont('DejaVu Sans')
def make_face(family, chinese):
    font = TTFont(src)
    for name in font['name'].names:
        if name.nameID in (1, 4, 6, 16):
            name.string = family.encode(name.getEncoding(), errors='replace')
    if chinese:
        for table in font['cmap'].tables:
            if table.isUnicode() and table.format != 14:
                table.cmap[0x4E2D] = table.cmap[ord('A')]
    return font

collection = TTCollection()
collection.fonts = [make_face('TavottoTtcLatin', False), make_face('TavottoTtcChinese', True)]
path = Path(sys.argv[2]) / 'mixed.ttc'
collection.save(path)
font_manager.fontManager.addfont(str(path))
entries = [e for e in font_manager.fontManager.ttflist if str(e.fname) == str(path)]
assert any(e.name == 'TavottoTtcLatin' for e in entries)
assert 'TavottoTtcLatin' in manifest.installed_latin_only_families()
if 'index' in inspect.signature(font_manager.FontEntry).parameters:
    assert any(e.name == 'TavottoTtcChinese' and e.index == 1 for e in entries)
    assert 'TavottoTtcChinese' not in manifest.installed_latin_only_families()
else:
    assert not any(e.name == 'TavottoTtcChinese' for e in entries)
"""
    result = subprocess.run(
        [worker, "-c", probe, str(ROOT / "src" / "tavotto" / "engine"), str(tmp_path)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=os.environ.copy(),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
