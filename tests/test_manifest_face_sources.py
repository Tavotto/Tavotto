"""manifest 报「实际画这段字的那张文件级的脸」（ADR 0164）：名字 + sha256 + 面序号 + 粗斜，**没有路径**。

`manifest.py` 只在 worker 子进程里（flat import），所以这里起子进程跑，主语是它打印出来的 JSON。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src" / "tavotto" / "engine"

SCRIPT = r"""
import json, sys
import matplotlib
matplotlib.use("Agg")
import manifest
from matplotlib import font_manager as fm

out = {}
out["dejavu"] = manifest.face_sources("Hello", ["DejaVu Sans"])
fname = sys.argv[1]
bfile = sys.argv[2]
out["locked"] = manifest.face_sources("Hello", ["DejaVu Sans"], fname)
out["bolditalic"] = manifest.face_sources("Hello", ["DejaVu Sans"], bfile)
out["empty"] = manifest.face_sources("", [])

# 普通文字：只写 fontweight / fontstyle、没有 fname。身份必须是 matplotlib 画它时 findfont 用的那个文件
from matplotlib.figure import Figure
fig = Figure()
live = {}
for name, kw in {
    "bold": {"fontweight": "bold"},
    "italic": {"fontstyle": "italic"},
    "bolditalic2": {"fontweight": "bold", "fontstyle": "italic"},
    "plain": {},
}.items():
    t = fig.text(0.5, 0.5, "Hello", fontfamily="DejaVu Sans", **kw)
    prop = t.get_fontproperties()
    out["live_" + name] = manifest.face_sources(t.get_text(), t.get_fontfamily(), prop.get_file(), prop)
    out["found_" + name] = str(fm.findfont(prop))
    # 老签名（只给族列表）解析到 Regular——这正是修复前 manifest 的写法
    out["familyonly_" + name] = manifest.face_sources(t.get_text(), t.get_fontfamily(), prop.get_file())
print(json.dumps(out))
print(json.dumps(out))
"""


def _run(*args: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ENGINE), "TAVOTTO_NO_TELEMETRY": "1"}
    r = subprocess.run(
        [sys.executable, "-c", SCRIPT, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_reported_identity_is_the_file_the_renderer_resolves_and_carries_no_path(tmp_path):
    import hashlib

    from matplotlib import font_manager as fm

    real = Path(str(fm.findfont(fm.FontProperties(family="DejaVu Sans"))))
    bi = Path(
        str(fm.findfont(fm.FontProperties(family="DejaVu Sans", weight="bold", style="italic")))
    )
    got = _run(str(real), str(bi))
    src = got["dejavu"]["face_src"]
    assert src == {
        "family": "DejaVu Sans",
        "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
        "face": 0,
        "bold": False,
        "italic": False,
    }
    assert "cjk_src" not in got["dejavu"]  # 纯拉丁文字没有中日韩回退
    # 粗斜取自这张脸自己（matplotlib 3.11 的 `style_flags` 是 enum.Flag，不是 int——回归过一次）
    assert got["bolditalic"]["face_src"]["bold"] is True
    assert got["bolditalic"]["face_src"]["italic"] is True
    assert got["bolditalic"]["face_src"]["sha256"] != src["sha256"]
    assert str(real.parent) not in json.dumps(
        {k: v for k, v in got.items() if not k.startswith("found_")}
    )  # 路径不进 manifest
    # 族链为空 = matplotlib 自己的默认（sans-serif）解析：报的仍是它真正会用的脸，不是另编一张
    assert got["empty"]["face_src"]["sha256"] == src["sha256"]


def test_ordinary_bold_or_italic_text_reports_the_face_matplotlib_resolves_for_the_full_properties():
    """P1（Codex #1671）：`fontweight='bold'` / `fontstyle='italic'` 的普通文字（没有 fname）画的是
    DejaVu Sans Bold / Oblique；身份的 sha256 与粗斜标志必须是那个文件的，不是 Regular 的。"""
    import hashlib

    from matplotlib import font_manager as fm

    real = Path(str(fm.findfont(fm.FontProperties(family="DejaVu Sans"))))
    got = _run(str(real), str(real))
    regular = hashlib.sha256(real.read_bytes()).hexdigest()
    expect = {
        "bold": (True, False),
        "italic": (False, True),
        "bolditalic2": (True, True),
        "plain": (False, False),
    }
    for name, (bold, italic) in expect.items():
        file = Path(got["found_" + name])
        src = got["live_" + name]["face_src"]
        assert src["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest(), name
        assert (src["bold"], src["italic"]) == (bold, italic), name
        if name != "plain":
            assert src["sha256"] != regular, name
    # 不带 props 的老写法确实会错（用例本身能复现原问题，而不是恒真）
    assert got["familyonly_bold"]["face_src"]["sha256"] == regular


def test_the_manifest_builder_passes_the_live_texts_full_font_properties():
    text = (ENGINE / "manifest.py").read_text(encoding="utf-8")
    call = text.split("face_sources(\n", 1)[1].split("\n                    )", 1)[0]
    assert "live_text.get_fontproperties()" in call


def test_a_font_file_locked_by_fname_is_identified_by_its_own_bytes(tmp_path):
    """脚本 `fname=` 锁了一个字体文件时，身份就是那个文件的字节（改一个字节 = 另一张脸）。"""
    import hashlib

    from matplotlib import font_manager as fm

    real = Path(str(fm.findfont(fm.FontProperties(family="DejaVu Sans"))))
    data = bytearray(real.read_bytes())
    data[-1] ^= 0xFF  # 同名同大小、字节不同
    copy = tmp_path / "fonts" / "DejaVuSans.ttf"
    copy.parent.mkdir()
    copy.write_bytes(bytes(data))
    got = _run(str(copy), str(copy))
    assert got["locked"]["face_src"]["sha256"] == hashlib.sha256(bytes(data)).hexdigest()
    assert got["locked"]["face_src"]["sha256"] != got["dejavu"]["face_src"]["sha256"]


def test_the_worker_side_cap_and_the_privacy_shape_are_pinned_in_the_source():
    text = (ENGINE / "manifest.py").read_text(encoding="utf-8")
    # _face_source 的返回字典只有这五个键：加路径 / 文件名 / 大小要先改这条
    body = text.split("def _face_source", 1)[1].split("def face_sources", 1)[0]
    keys = set(__import__("re").findall(r'"(\w+)":', body))
    assert keys == {"family", "sha256", "face", "bold", "italic"}
