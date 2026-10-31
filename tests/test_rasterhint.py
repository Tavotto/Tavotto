"""「跑完没出图」的原因提示（`engine/rasterhint.py`）：Pillow / OpenCV 直接写图片的脚本 vs 正常 Matplotlib 脚本。

判据的主语是**脚本源码（含有界跟进的本地模块）**——不是运行结果：这里没有任何进程，只有 AST。
正例必须出提示；反例（matplotlib、只用 PIL 读图再用 matplotlib 画、`np.save`、存的不是图片）必须沉默。
"""

from __future__ import annotations

import os
import re
import textwrap
import time
from pathlib import Path

import pytest

from tavotto.engine import rasterhint


def _write(root: Path, name: str, body: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")


_LITERAL = re.compile(
    r"""['"]([^'"\n{}]+\.(?:png|jpe?g|bmp|gif|tiff?|webp|ppm|pgm|ico|pdf|svg|eps))['"]""", re.I
)
#: 「本次运行开始」：比现在早一点，测试里即时写的文件都算本次写的
START = time.time() - 30


def _touch_outputs(root: Path, script: str, cwd_mode: str | None, text: str) -> None:
    """模拟「脚本真的跑过并写出了源码里的字面量输出文件」（只写 root 之内的）。"""
    for lit in _LITERAL.findall(text):
        fs = rasterhint._fs_path(lit, root, script, cwd_mode)
        if fs is None or not fs.resolve().is_relative_to(root.resolve()):
            continue
        fs.parent.mkdir(parents=True, exist_ok=True)
        fs.write_bytes(b"x")


def _detect(
    root: Path, script: str = "make.py", cwd_mode: str | None = "project_root"
) -> dict | None:
    """跑过一次之后再判：把源码里的字面量输出文件补出来，再带上本次运行开始时间。"""
    for f in sorted(root.rglob("*.py")):
        _touch_outputs(root, script, cwd_mode, f.read_text(encoding="utf-8", errors="ignore"))
    return rasterhint.detect(root, script, cwd_mode, run_started_at=START)


def _lib(root: Path, script: str = "make.py") -> str | None:
    hint = _detect(root, script)
    return hint["library"] if hint else None


PILLOW_SCRIPT = """
    import csv
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(img).line([(0, 0), (400, 300)], fill="black")
    img.save("out.png")
"""


@pytest.mark.parametrize(
    ("body", "library"),
    [
        (PILLOW_SCRIPT, "pillow"),
        ("import PIL.Image as I\nim = I.new('L', (2, 2))\nim.save('out_1.jpg')\n", "pillow"),
        ("from PIL import Image\nim = Image.open('a.png')\nim.save('copy.png')\n", "pillow"),
        ("import cv2\ncv2.imwrite('o.png', arr)\n", "opencv"),
        ("import cv2 as c\nc.imwrite('o.png', arr)\n", "opencv"),
        ("from cv2 import imwrite\nimwrite('o.png', arr)\n", "opencv"),
        ("import imageio\nimageio.imwrite('o.png', arr)\n", "imageio"),
        ("import imageio.v3 as iio\niio.imwrite('o.png', arr)\n", "imageio"),
        ("from skimage import io\nio.imsave('o.png', arr)\n", "skimage"),
        ("from skimage.io import imsave\nimsave('o.png', arr)\n", "skimage"),
    ],
)
def test_scripts_that_write_bitmaps_are_recognised(tmp_path, body, library):
    _write(tmp_path, "make.py", body)
    assert _lib(tmp_path) == library


def test_a_normal_matplotlib_script_is_not_flagged(tmp_path):
    _write(
        tmp_path,
        "make.py",
        """
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        fig.savefig("out.png")
        """,
    )
    assert _lib(tmp_path) is None


def test_pil_for_reading_then_matplotlib_for_drawing_is_not_flagged(tmp_path):
    _write(
        tmp_path,
        "make.py",
        """
        from PIL import Image
        import matplotlib.pyplot as plt
        im = Image.open("a.png")
        im.save("copy.png")
        plt.imshow(im)
        """,
    )
    assert _lib(tmp_path) is None


def test_pil_read_only_script_is_not_flagged(tmp_path):
    _write(tmp_path, "make.py", "from PIL import Image\nprint(Image.open('a.png').size)\n")
    assert _lib(tmp_path) is None


def test_matplotlib_imported_but_pillow_draws_still_gets_the_hint(tmp_path):
    """ADR 0143 修订（2026-10-23）：detect 只在「没捕获到任何 Figure」之后被问，import 了 matplotlib 不再一刀切沉默；
    Pillow 绘制证据（ImageDraw.Draw + 绘制方法）成立才说。"""
    _write(tmp_path, "make.py", "import matplotlib\n" + textwrap.dedent(PILLOW_SCRIPT))
    assert _lib(tmp_path) == "pillow"


def test_matplotlib_family_import_with_pillow_drawing_still_gets_the_hint(tmp_path):
    _write(tmp_path, "make.py", "import seaborn as sns\n" + textwrap.dedent(PILLOW_SCRIPT))
    assert _lib(tmp_path) == "pillow"


def test_matplotlib_imported_with_only_weak_pillow_evidence_stays_silent(tmp_path):
    """matplotlib + 只有 Image.new + save（无 ImageDraw 绘制）：证据不够强，沉默。"""
    _write(
        tmp_path,
        "make.py",
        "import matplotlib\nfrom PIL import Image\nImage.new('L', (2, 2)).save('o.png')\n",
    )
    assert _lib(tmp_path) is None


def test_non_module_savers_and_non_image_targets_are_not_flagged(tmp_path):
    _write(
        tmp_path,
        "make.py",
        """
        import numpy as np
        from PIL import Image
        np.save("a.npy", arr)
        Image.open("a.png").save("meta.json")
        """,
    )
    assert _lib(tmp_path) is None


def test_save_on_a_foreign_module_is_not_flagged_even_with_pil_imported(tmp_path):
    """路径不是字面量（扩展名帮不上忙）：`np.save(path, a)` 的接收者是 numpy 模块，不是 Pillow 图。"""
    _write(
        tmp_path,
        "make.py",
        "import numpy as np\nimport torch\nfrom PIL import Image\nnp.save(path, a)\ntorch.save(m, path)\n",
    )
    assert _lib(tmp_path) is None


def test_save_without_a_pil_import_is_not_flagged(tmp_path):
    _write(tmp_path, "make.py", "model.save('weights.png')\n")
    assert _lib(tmp_path) is None


def test_a_followed_local_module_that_draws_with_pillow_counts(tmp_path):
    _write(tmp_path, "make.py", "import drawing\ndrawing.render()\n")
    _write(
        tmp_path,
        "drawing.py",
        "from PIL import Image\ndef render():\n    Image.new('L', (1, 1)).save('o.png')\n",
    )
    assert _lib(tmp_path) == "pillow"


def test_a_followed_local_module_importing_matplotlib_silences_weak_evidence(tmp_path):
    _write(
        tmp_path,
        "make.py",
        "import helpers\nfrom PIL import Image\nImage.new('L', (1, 1)).save('o.png')\n",
    )
    _write(tmp_path, "helpers.py", "import matplotlib.pyplot as plt\n")
    assert _lib(tmp_path) is None


def test_unreadable_or_unparsable_scripts_are_silent_not_errors(tmp_path):
    _write(tmp_path, "make.py", "def broken(:\n")
    assert _lib(tmp_path) is None
    assert _lib(tmp_path, "missing.py") is None


def test_when_the_scan_cannot_see_everything_weak_evidence_stays_silent(tmp_path):
    """动态 import 可能装进一个没扫过的 matplotlib——弱证据（Image.new + save）看不全就不说。"""
    _write(
        tmp_path,
        "make.py",
        "from PIL import Image\nImage.new('L', (2, 2)).save('o.png')\n"
        "import importlib\nimportlib.import_module(name)\n",
    )
    assert _lib(tmp_path) is None


def test_strong_pillow_drawing_survives_dynamic_imports(tmp_path):
    """强证据（ImageDraw 绘制）在动态 import 下仍可说：「没出 Figure」已是运行事实。"""
    _write(
        tmp_path,
        "make.py",
        textwrap.dedent(PILLOW_SCRIPT) + "\nimport importlib\nimportlib.import_module(name)\n",
    )
    assert _lib(tmp_path) == "pillow"


# ---- in_project：素材库按钮只在能确定那张图落进素材盘点范围时才给 ----


def _hint(root: Path, body: str, cwd_mode: str | None = "project") -> dict:
    _write(root, "make.py", body)
    hint = _detect(root, "make.py", cwd_mode)
    assert hint is not None
    return hint


@pytest.mark.parametrize("kind", ["absolute", "home", "dotdot", "dotdot2"])
def test_saving_outside_the_project_keeps_the_reason_but_not_the_assets_button(
    tmp_path, monkeypatch, kind
):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    target = {
        "absolute": str(tmp_path / "abs.png"),
        "home": "~/out.png",
        "dotdot": "../out.png",
        "dotdot2": "sub/../../out.png",
    }[kind]
    # 文件确实写出来了（在项目之外）：原因句成立，但素材库看不见
    for f in (tmp_path / "abs.png", tmp_path / "home" / "out.png", tmp_path / "out.png"):
        f.write_bytes(b"x")
    hint = _hint(root, f"from PIL import Image\nImage.new('L', (2, 2)).save({target!r})\n")
    assert hint["library"] == "pillow"
    assert hint["in_project"] is False


@pytest.mark.parametrize("target", ["C:/out.png", "\\\\host\\share\\out.png"])
def test_foreign_system_paths_cannot_be_verified_so_no_hint(tmp_path, target):
    _write(tmp_path, "make.py", f"from PIL import Image\nImage.open('a.png').save({target!r})\n")
    assert _detect(tmp_path, "make.py", "project") is None


def test_a_literal_relative_path_in_project_mode_is_in_project(tmp_path):
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('out/a.png')\n"
    assert _hint(tmp_path, body, "project")["in_project"] is True
    assert _hint(tmp_path, body, "project_root")["in_project"] is True


@pytest.mark.parametrize("mode", ["sandbox", None])
def test_sandbox_without_a_sandbox_dir_or_unknown_mode_has_no_file_evidence(tmp_path, mode):
    """拿不到这次运行实际的 cwd（沙盒目录没给 / 运行目录未知）：项目里恰有同名文件也不能当证据。"""
    _write(tmp_path, "make.py", "from PIL import Image\nImage.open('a.png').save('out.png')\n")
    old = tmp_path / "out.png"
    old.write_bytes(b"x")  # 项目里恰有同名旧文件也不能当证据
    os.utime(old, (START - 3600, START - 3600))
    assert rasterhint.detect(tmp_path, "make.py", mode, run_started_at=START) is None


@pytest.mark.parametrize(
    "body",
    [
        "from PIL import Image\nImage.open(src).save(path)\n",
        "from PIL import Image\nImage.open(src).save(f'{name}.png')\n",
    ],
)
def test_a_dynamic_path_without_drawing_evidence_has_no_hint(tmp_path, body):
    _write(tmp_path, "make.py", body)
    assert _detect(tmp_path, "make.py", "project") is None


@pytest.mark.parametrize(
    "body",
    [
        "from PIL import Image\nImage.new('L', (2, 2)).save(path)\n",
        "from PIL import Image\nImage.new('L', (2, 2)).save(f'{name}.png')\n",
    ],
)
def test_image_new_then_a_dynamic_save_gets_a_hint_without_a_path(tmp_path, body):
    _write(tmp_path, "make.py", body)
    assert _detect(tmp_path, "make.py", "project") == _NO_PATH


@pytest.mark.parametrize("ext", ["bmp", "gif", "webp", "ppm", "ico", "pgm"])
def test_formats_the_asset_inventory_does_not_scan_get_no_assets_button(tmp_path, ext):
    hint = _hint(tmp_path, f"from PIL import Image\nImage.new('L', (2, 2)).save('out.{ext}')\n")
    assert hint["library"] == "pillow"  # 原因句照样说
    assert hint["in_project"] is False


@pytest.mark.parametrize("ext", ["png", "jpg", "jpeg", "tif", "tiff", "PNG"])
def test_formats_the_asset_inventory_scans_get_the_button(tmp_path, ext):
    hint = _hint(tmp_path, f"from PIL import Image\nImage.new('L', (2, 2)).save('out.{ext}')\n")
    assert hint["in_project"] is True


def test_visibility_rule_is_the_inventorys_own_predicate():
    from tavotto.engine import project_refresh

    assert rasterhint.is_inventoried is project_refresh.is_inventoried


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("tavottofile/out.png", False),
        (".hidden/out.png", False),
        ("scripts/out.png", False),
        ("_cache/out.png", False),
        ("a/.hidden/out.png", False),
        (".out.png", False),
        ("plots/out.png", True),
        ("out.png", True),
    ],
)
def test_pruned_or_hidden_locations_get_no_assets_button(tmp_path, target, expected):
    body = f"from PIL import Image\nImage.new('L', (2, 2)).save({target!r})\n"
    for mode in ("project", "project_root"):
        assert _hint(tmp_path, body, mode)["in_project"] is expected


def test_project_mode_resolves_relative_to_the_script_directory(tmp_path):
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('out.png')\n"
    # 脚本在 scripts/ 下：project 模式落在 scripts/out.png（被剪枝），project_root 模式落在项目根（可见）
    _write(tmp_path, "scripts/make.py", body)
    assert _detect(tmp_path, "scripts/make.py", "project")["in_project"] is False
    assert _detect(tmp_path, "scripts/make.py", "project_root")["in_project"] is True
    # 普通子目录里的脚本：两种模式都可见
    _write(tmp_path, "analysis/make.py", body)
    assert _detect(tmp_path, "analysis/make.py", "project")["in_project"] is True
    # 脚本在 .hidden 目录：project 模式相对脚本目录，落进隐藏目录
    _write(tmp_path, ".tools/make.py", body)
    assert _detect(tmp_path, ".tools/make.py", "project")["in_project"] is False


def test_a_same_stem_pdf_on_disk_hides_the_raster(tmp_path):
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('out.png')\n"
    assert _hint(tmp_path, body)["in_project"] is True
    (tmp_path / "out.PDF").write_bytes(b"%PDF-1.4")
    assert _hint(tmp_path, body)["in_project"] is False


def test_is_inventoried_agrees_with_iter_assets(tmp_path):
    from tavotto.engine import project_refresh

    names = [
        "a.png",
        "a.pdf",
        "b.jpg",
        "c.bmp",
        ".d.png",
        "sub/e.tif",
        ".hid/f.png",
        "tavottofile/g.png",
        "scripts/h.png",
        "_cache/i.png",
        "sub/.hid/j.png",
        "sub/k.pdf",
        "sub/k.png",
    ]
    for n in names:
        _write(tmp_path, n, "x")
    listed = {p.relative_to(tmp_path).as_posix() for p, _ in project_refresh.iter_assets(tmp_path)}
    expected = {
        n
        for n in names
        if project_refresh.is_inventoried(
            n, pdf_twin=(tmp_path / n).with_suffix(".pdf").exists() and not n.endswith(".pdf")
        )
    }
    assert listed == expected
    assert listed == {"a.pdf", "b.jpg", "sub/e.tif", "sub/k.pdf"}


def test_a_utf8_bom_script_still_gets_the_hint(tmp_path):
    (tmp_path / "make.py").write_bytes(
        b"\xef\xbb\xbf" + textwrap.dedent(PILLOW_SCRIPT).encode("utf-8")
    )
    assert _lib(tmp_path) == "pillow"


def test_a_pep263_gbk_script_still_gets_the_hint(tmp_path):
    body = "# -*- coding: gbk -*-\n# 生成位图\n" + textwrap.dedent(PILLOW_SCRIPT)
    (tmp_path / "make.py").write_bytes(body.encode("gbk"))
    assert _lib(tmp_path) == "pillow"


def test_a_symlinked_output_directory_is_not_in_project(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / "plots").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    (outside / "a.png").write_bytes(b"x")  # 链接那头的文件确实存在
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('plots/a.png')\n"
    assert _hint(root, body, "project")["in_project"] is False
    assert _hint(root, body, "project_root")["in_project"] is False


# ---- 文件证据：静态上「会存位图」不够，这次运行得真的写出了那个文件 ----

HELPER_ONLY = "from PIL import Image\ndef unused():\n    Image.open('a.png').save('out.png')\n"
_NO_PATH = {"kind": "raster_script", "library": "pillow", "in_project": False, "path": None}


def test_an_uncalled_helper_with_no_file_gets_no_hint(tmp_path):
    _write(tmp_path, "make.py", HELPER_ONLY)
    assert rasterhint.detect(tmp_path, "make.py", "project", run_started_at=START) is None


def test_a_branch_never_taken_with_no_file_gets_no_hint(tmp_path):
    _write(
        tmp_path,
        "make.py",
        "from PIL import Image\nif False:\n    Image.open('a.png').save('out.png')\n",
    )
    assert rasterhint.detect(tmp_path, "make.py", "project", run_started_at=START) is None


def test_a_file_actually_written_by_this_run_gets_hint_and_button(tmp_path):
    _write(tmp_path, "make.py", HELPER_ONLY)
    (tmp_path / "out.png").write_bytes(b"x")  # mtime = 现在，晚于 START
    hint = rasterhint.detect(tmp_path, "make.py", "project", run_started_at=START)
    assert hint == {
        "kind": "raster_script",
        "library": "pillow",
        "in_project": True,
        "path": "out.png",
    }


def test_a_file_older_than_this_run_gets_no_hint(tmp_path):
    _write(tmp_path, "make.py", HELPER_ONLY)
    out = tmp_path / "out.png"
    out.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(out, (old, old))
    assert (
        rasterhint.detect(tmp_path, "make.py", "project", run_started_at=time.time() - 60) is None
    )


def test_without_a_run_start_time_there_is_no_evidence(tmp_path):
    _write(tmp_path, "make.py", HELPER_ONLY)
    (tmp_path / "out.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project") is None


def test_sandbox_mode_checks_the_sandbox_dir_and_never_offers_the_button(tmp_path):
    root = tmp_path / "proj"
    box = tmp_path / "box"
    root.mkdir()
    box.mkdir()
    _write(root, "make.py", HELPER_ONLY)
    assert rasterhint.detect(root, "make.py", "sandbox", START, str(box)) is None  # 沙盒里没写
    (box / "out.png").write_bytes(b"x")
    hint = rasterhint.detect(root, "make.py", "sandbox", START, str(box))
    assert hint == {
        "kind": "raster_script",
        "library": "pillow",
        "in_project": False,
        "path": None,
    }


# ---- 运行时才定输出路径：项目素材盘点里本次运行后新出现 / 被改写的文件 ----

RUNTIME_PILLOW = """
    import argparse
    from PIL import Image, ImageDraw
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="force_reports")
    path = ap.parse_args().out + "/heat.png"
    image = Image.new("RGB", (24, 24), "white")
    draw = ImageDraw.Draw(image)
    image.save(path, format="PNG", dpi=(180, 180))
"""


def _runtime_started() -> float:
    return time.time() - 60


def test_runtime_path_pillow_script_with_a_new_file_in_the_project_gets_hint(tmp_path):
    _write(tmp_path, "make.py", RUNTIME_PILLOW)
    out = tmp_path / "force_reports" / "heat.png"
    out.parent.mkdir()
    out.write_bytes(b"x")  # mtime = 现在，晚于运行开始
    hint = rasterhint.detect(
        tmp_path, "make.py", "sandbox", _runtime_started(), str(tmp_path / "box")
    )
    assert hint == {
        "kind": "raster_script",
        "library": "pillow",
        "in_project": True,
        "path": "force_reports/heat.png",
    }


def test_runtime_path_with_only_an_old_file_gets_no_hint(tmp_path):
    _write(tmp_path, "make.py", RUNTIME_PILLOW)
    out = tmp_path / "force_reports" / "heat.png"
    out.parent.mkdir()
    out.write_bytes(b"x")
    os.utime(out, (time.time() - 7200, time.time() - 7200))
    # 旧文件不是文件证据（不给 path）；但 Image.new + 动态 save 这个 AST 证据仍成立（2026-10-23 修订）
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) == _NO_PATH


def test_runtime_path_without_a_run_start_time_has_no_file_evidence(tmp_path):
    _write(tmp_path, "make.py", RUNTIME_PILLOW)
    (tmp_path / "heat.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project") == _NO_PATH


def test_a_new_file_does_not_count_when_the_script_has_no_save_call(tmp_path):
    _write(tmp_path, "make.py", "from PIL import Image\nImage.open('a.png').load()\n")
    (tmp_path / "heat.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) is None


def test_a_new_file_does_not_count_when_matplotlib_is_imported(tmp_path):
    _write(tmp_path, "make.py", "import matplotlib\n" + textwrap.dedent(RUNTIME_PILLOW))
    (tmp_path / "heat.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) is None


def test_a_new_pdf_is_not_pillow_evidence_but_a_new_png_is(tmp_path):
    _write(tmp_path, "make.py", RUNTIME_PILLOW)
    (tmp_path / "r.pdf").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) == _NO_PATH


def test_inventory_evidence_ignores_pruned_directories(tmp_path):
    _write(tmp_path, "make.py", RUNTIME_PILLOW)
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "heat.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) == _NO_PATH


# ---- plotly / ROOT ----


@pytest.mark.parametrize(
    ("body", "library"),
    [
        (
            "import plotly.graph_objects as go\nfig = go.Figure()\nfig.write_image('o.png')\n",
            "plotly",
        ),
        ("import plotly.io as pio\npio.write_image(fig, 'o.jpg')\n", "plotly"),
        ("import plotly.express as px\npx.line(df).write_image('o.pdf')\n", "plotly"),
        ("import ROOT\nc = ROOT.TCanvas()\nc.SaveAs('o.png')\n", "root"),
        ("import ROOT as rt\nc = rt.TCanvas()\nc.Print('o.svg')\n", "root"),
        ("from ROOT import TCanvas\nTCanvas('c').SaveAs('o.eps')\n", "root"),
    ],
)
def test_plotly_and_root_static_recognition(tmp_path, body, library):
    _write(tmp_path, "make.py", body)
    assert _lib(tmp_path) == library


@pytest.mark.parametrize(
    "body",
    [
        "import plotly.graph_objects as go\ngo.Figure().write_html('o.html')\n",
        "import plotly.graph_objects as go\ngo.Figure().write_image('o.json')\n",
        "import ROOT\nROOT.TFile('o.root').Print('o.txt')\n",
        "import numpy as np\nnp.save('o.png', a)\nfig.write_image('o.png')\n",  # 没 import plotly
        "import matplotlib\nimport plotly\nfig.write_image('o.png')\n",
    ],
)
def test_plotly_and_root_negative_cases(tmp_path, body):
    _write(tmp_path, "make.py", body)
    assert _lib(tmp_path) is None


FIT_MASS_SHAPE = """
    import math
    from importlib import import_module
    import ROOT as rt
    from ROOT import RooFit as rf
    import lhcbStyle

    def fit_mass(isTUNE=False):
        mode = "_tune" if isTUNE else ""
        module = import_module("results.para_data".replace("/", "."))
        can = rt.TCanvas("can", "can", 800, 600)
        frame.Draw()
        can.SaveAs(f"results/mass_data{mode}.pdf")

    fit_mass()
    fit_mass(True)
"""


def test_root_pdf_script_shaped_like_fit_mass_is_recognised_at_runtime(tmp_path):
    """真实样本结构：`rt.TCanvas` + f-string 的 `.pdf` 路径 + 拼出来的 `import_module`；运行后 results/ 下出现新 PDF。"""
    _write(tmp_path, "fit_mass.py", FIT_MASS_SHAPE)
    # 没有任何新文件：不说
    assert rasterhint.detect(tmp_path, "fit_mass.py", "project", _runtime_started()) is None
    out = tmp_path / "results" / "mass_data_tune.pdf"
    out.parent.mkdir()
    out.write_bytes(b"%PDF-")
    hint = rasterhint.detect(tmp_path, "fit_mass.py", "project", _runtime_started())
    assert hint == {
        "kind": "raster_script",
        "library": "root",
        "in_project": True,
        "path": "results/mass_data_tune.pdf",
    }
    # 旧 PDF 不算
    os.utime(out, (time.time() - 7200, time.time() - 7200))
    assert rasterhint.detect(tmp_path, "fit_mass.py", "project", _runtime_started()) is None


# ---- Codex #1271：plotly 函数形式的第二个位置参数是文件；库与输出路径要成对 ----


def test_plotly_function_form_takes_the_file_from_the_second_positional_argument(tmp_path):
    root = tmp_path / "proj"
    box = tmp_path / "box"
    root.mkdir()
    box.mkdir()
    _write(
        root,
        "make.py",
        "import plotly.graph_objects as go\nimport plotly.io as pio\n"
        "fig = go.Figure()\npio.write_image(fig, 'o.png')\n",
    )
    assert rasterhint.detect(root, "make.py", "sandbox", _runtime_started(), str(box)) is None
    (box / "o.png").write_bytes(b"x")  # 沙盒里写出了第二个位置参数指的文件
    hint = rasterhint.detect(root, "make.py", "sandbox", _runtime_started(), str(box))
    assert hint == {"kind": "raster_script", "library": "plotly", "in_project": False, "path": None}


def test_plotly_function_form_literal_ext_is_judged_on_the_file_argument(tmp_path):
    # 第一个实参是 fig（Name），第二个是 .html：不算存图
    _write(
        tmp_path,
        "make.py",
        "import plotly.io as pio\nfig = object()\npio.write_image(fig, 'o.html')\n",
    )
    (tmp_path / "o.html").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) is None


def test_library_stays_paired_with_the_path_of_its_own_fresh_file(tmp_path):
    # 没调用的 Pillow helper 存 unused.png；真正跑到的是 plotly 写 actual.pdf
    _write(
        tmp_path,
        "make.py",
        "from PIL import Image\nimport plotly.io as pio\n"
        "def helper():\n    Image.new('L', (2, 2)).save('unused.png')\n"
        "pio.write_image(object(), 'actual.pdf')\n",
    )
    (tmp_path / "actual.pdf").write_bytes(b"%PDF-")
    hint = rasterhint.detect(tmp_path, "make.py", "project", run_started_at=START)
    assert hint == {
        "kind": "raster_script",
        "library": "plotly",
        "in_project": True,
        "path": "actual.pdf",
    }


def test_runtime_path_inventory_file_is_attributed_by_extension(tmp_path):
    # 运行时才定路径：新出现的是 PDF，只有 plotly 认 PDF，不能算成靠前的 Pillow
    _write(
        tmp_path,
        "make.py",
        "import sys\nfrom PIL import Image\nimport plotly.io as pio\n"
        "def helper():\n    Image.new('L', (2, 2)).save(sys.argv[1] + '/a.png')\n"
        "pio.write_image(object(), sys.argv[1] + '/b.pdf')\n",
    )
    (tmp_path / "b.pdf").write_bytes(b"%PDF-")
    hint = rasterhint.detect(tmp_path, "make.py", "project", _runtime_started())
    assert hint is not None and hint["library"] == "plotly" and hint["path"] == "b.pdf"


# ---- Codex #1271 第二轮：to_image 不写文件；动态路径 PNG 的库归属只在唯一候选时给 ----


def test_plotly_to_image_returns_bytes_and_is_not_a_save_call(tmp_path):
    """`to_image` 返回字节、不写文件：登记它会把运行期间任何新 PNG 安到 plotly 头上。"""
    _write(
        tmp_path,
        "make.py",
        "import plotly.io as pio\nfig = object()\nb = pio.to_image(fig, format='png')\n",
    )
    (tmp_path / "unrelated.png").write_bytes(b"x")  # 运行期间恰好被别的东西写过
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) is None


def test_dynamic_png_with_two_possible_libraries_is_not_attributed(tmp_path):
    """实际写 PNG 的是 plotly（运行时路径），脚本里还有一个没被调用的 Pillow helper（同样动态路径）：
    PNG 两个库都认、静态上分不出 → 沉默，不能按库表顺序冒名 pillow。"""
    _write(
        tmp_path,
        "make.py",
        "import sys\nfrom PIL import Image\nimport plotly.io as pio\n"
        "def helper():\n    Image.new('L', (2, 2)).save(sys.argv[1] + '/a.png')\n"
        "pio.write_image(object(), sys.argv[1] + '/b.png')\n",
    )
    (tmp_path / "b.png").write_bytes(b"x")
    assert rasterhint.detect(tmp_path, "make.py", "project", _runtime_started()) is None


def test_dynamic_png_with_a_single_possible_library_is_still_attributed(tmp_path):
    """正对照：候选唯一（只有 plotly 一处动态保存）→ 照常归属。"""
    _write(
        tmp_path,
        "make.py",
        "import sys\nimport plotly.io as pio\npio.write_image(object(), sys.argv[1] + '/b.png')\n",
    )
    (tmp_path / "b.png").write_bytes(b"x")
    hint = rasterhint.detect(tmp_path, "make.py", "project", _runtime_started())
    assert hint is not None and hint["library"] == "plotly" and hint["path"] == "b.png"


def test_a_stale_literal_save_does_not_claim_an_unrelated_new_inventory_file(tmp_path):
    """字面量路径的保存已逐个核过没写出新文件，它不是候选：另一个库的动态路径才是唯一候选。"""
    _write(
        tmp_path,
        "make.py",
        "import sys\nfrom PIL import Image\nimport plotly.io as pio\n"
        "def helper():\n    Image.new('L', (2, 2)).save('unused.png')\n"
        "pio.write_image(object(), sys.argv[1] + '/b.png')\n",
    )
    (tmp_path / "b.png").write_bytes(b"x")
    hint = rasterhint.detect(tmp_path, "make.py", "project", _runtime_started())
    assert hint is not None and hint["library"] == "plotly"


# ---- Pillow 画图脚本：不依赖落盘文件（ADR 0143 修订 2026-10-23，「先跑再提示」） ----


def _bare(root: Path, body: str, mode: str | None = "project") -> dict | None:
    """不补任何输出文件：只有 AST 证据。"""
    _write(root, "make.py", body)
    return rasterhint.detect(root, "make.py", mode, run_started_at=START)


def test_pillow_draw_with_a_dynamic_output_path_gets_a_hint_without_path(tmp_path):
    body = """
        import sys
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (200, 100), "white")
        d = ImageDraw.Draw(im)
        d.rectangle([10, 10, 90, 90], outline="black")
        im.save(sys.argv[1])
    """
    assert _bare(tmp_path, body) == _NO_PATH


def test_pillow_draw_that_only_shows_gets_a_hint(tmp_path):
    body = """
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (200, 100))
        ImageDraw.Draw(im).ellipse([5, 5, 50, 50], fill="red")
        im.show()
    """
    assert _bare(tmp_path, body) == _NO_PATH


def test_image_new_then_only_show_gets_a_hint(tmp_path):
    assert (
        _bare(tmp_path, "from PIL import Image\nIm = Image.new('L', (4, 4))\nIm.show()\n")
        == _NO_PATH
    )


def test_pillow_draw_whose_output_file_does_not_exist_yet_gets_a_hint(tmp_path):
    body = "from PIL import Image, ImageDraw\nim = Image.open('a.png')\nd = ImageDraw.Draw(im)\nd.text((0, 0), 'x')\nim.save('out.png')\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_a_with_bound_draw_object_counts(tmp_path):
    body = "from PIL import Image, ImageDraw\nim = Image.open('a.png')\nwith ImageDraw.Draw(im) as g:\n    g.line([(0, 0), (5, 5)])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_imagefont_with_a_draw_object_counts_as_drawing_text(tmp_path):
    body = "from PIL import Image, ImageDraw, ImageFont\nim = Image.open('a.png')\nf = ImageFont.truetype('x.ttf', 12)\nd = ImageDraw.Draw(im)\nrender(d, f)\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_pillow_draw_with_matplotlib_imported_and_no_figure_gets_a_hint(tmp_path):
    body = """
        import matplotlib.pyplot as plt
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (50, 50))
        ImageDraw.Draw(im).polygon([(0, 0), (10, 0), (5, 9)], fill="blue")
        im.save(out_path)
    """
    assert _bare(tmp_path, body) == _NO_PATH


@pytest.mark.parametrize(
    "body",
    [
        # 普通图片处理不是画图
        "import sys\nfrom PIL import Image\nImage.open(sys.argv[1]).resize((10, 10)).save(sys.argv[2])\n",
        "from PIL import Image\nImage.open(src).convert('L').show()\n",
        # Image.new 但无 save / show / 绘制
        "from PIL import Image\nim = Image.new('L', (2, 2))\nprint(im.size)\n",
        # 别的库的 .text / .line 不是 Pillow 绘制
        "import matplotlib.pyplot as plt\nfrom PIL import Image\nfig, ax = plt.subplots()\nax.text(0, 0, 'x')\nax.plot([0, 1])\n",
        # plt.show 不是 Pillow 的 show
        "import matplotlib.pyplot as plt\nfrom PIL import Image\nim = Image.new('L', (2, 2))\nplt.show()\n",
        # 没 import PIL
        "d = make()\nd.rectangle([0, 0, 1, 1])\nImage.new('L', (1, 1)).save('o.png')\n",
    ],
)
def test_ordinary_image_processing_or_non_pillow_drawing_gets_no_hint(tmp_path, body):
    assert _bare(tmp_path, body) is None


def test_other_libraries_keep_requiring_file_evidence(tmp_path):
    """只动 Pillow 分支：opencv 画图无文件证据照旧沉默。"""
    assert (
        _bare(
            tmp_path,
            "import cv2\nimg = cv2.imread('a.png')\ncv2.rectangle(img, (0, 0), (5, 5), 1)\ncv2.imwrite(out, img)\n",
        )
        is None
    )


def test_weak_pillow_evidence_defers_to_another_library_that_saves(tmp_path):
    body = "import plotly.io as pio\nfrom PIL import Image\ndef h():\n    Image.new('L', (1, 1)).save(p)\npio.write_image(object(), q)\n"
    assert _bare(tmp_path, body) is None


def test_annotated_draw_binding_counts(tmp_path):
    body = (
        "from PIL import Image, ImageDraw\nim = Image.open('a.png')\n"
        "draw: ImageDraw.ImageDraw = ImageDraw.Draw(im)\ndraw.line([(0, 0), (5, 5)])\n"
    )
    assert _bare(tmp_path, body) == _NO_PATH


def test_annotated_image_new_with_dynamic_save_gets_a_hint(tmp_path):
    body = "import sys\nfrom PIL import Image\nim: Image.Image = Image.new('L', (4, 4))\nim.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_weak_evidence_needs_the_image_new_object_itself_to_be_saved(tmp_path):
    body = "from PIL import Image\nim = Image.new('L', (4, 4))\nmodel.save('weights.bin')\n"
    assert _bare(tmp_path, body) is None


def test_chained_image_new_save_gets_a_hint(tmp_path):
    body = "import sys\nfrom PIL import Image\nImage.new('L', (4, 4)).save(sys.argv[1])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_image_new_name_reassigned_to_something_else_is_not_counted(tmp_path):
    body = "from PIL import Image\nim = Image.new('L', (4, 4))\nim = load()\nim.save(p)\n"
    assert _bare(tmp_path, body) is None


# ---- 名字匹配按作用域 + 先后（Codex #1340 P2：接收者名字不能是全文件、不分先后的匹配） ----

_PIL = "import sys\nfrom PIL import Image, ImageDraw\n"


def test_save_before_the_image_new_binding_in_the_same_function_is_not_counted(tmp_path):
    body = _PIL + "def f(im):\n    im.show()\n    im = Image.new('L', (2, 2))\n"
    assert _bare(tmp_path, body) is None


def test_save_before_the_image_new_binding_at_module_level_is_not_counted(tmp_path):
    body = _PIL + "im.save(sys.argv[1])\nim = Image.new('L', (2, 2))\n"
    assert _bare(tmp_path, body) is None


def test_same_name_in_two_functions_is_not_the_image_new_object(tmp_path):
    body = (
        _PIL
        + "def a():\n    im = Image.new('L', (2, 2))\n    return im\n"
        + "def b(im):\n    im.save(sys.argv[1])\n"
    )
    assert _bare(tmp_path, body) is None


def test_image_new_then_save_in_the_same_function_gets_a_hint(tmp_path):
    body = _PIL + "def f():\n    im = Image.new('L', (2, 2))\n    im.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_image_new_then_save_at_module_level_gets_a_hint(tmp_path):
    body = _PIL + "im = Image.new('L', (2, 2))\nim.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_module_level_binding_is_not_seen_from_inside_a_function(tmp_path):
    body = _PIL + "im = Image.new('L', (2, 2))\ndef f():\n    im.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) is None


def test_function_parameter_shadowing_the_name_is_not_counted(tmp_path):
    body = _PIL + "im = Image.new('L', (2, 2))\ndef f(im):\n    im.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) is None


def test_global_declared_name_is_not_counted(tmp_path):
    body = (
        _PIL
        + "def f():\n    global im\n    im = Image.new('L', (2, 2))\n    im.save(sys.argv[1])\n"
    )
    assert _bare(tmp_path, body) is None


def test_with_as_image_new_then_save_in_the_with_body_gets_a_hint(tmp_path):
    body = _PIL + "with Image.new('L', (2, 2)) as im:\n    im.save(sys.argv[1])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_draw_bound_in_another_function_is_not_the_draw_object(tmp_path):
    body = (
        _PIL
        + "def a(img):\n    d = ImageDraw.Draw(img)\n    return d\n"
        + "def b(d):\n    d.line([(0, 0), (1, 1)])\n"
    )
    assert _bare(tmp_path, body) is None


def test_draw_call_before_the_draw_binding_is_not_counted(tmp_path):
    body = _PIL + "def f(img, d):\n    d.line([(0, 0), (1, 1)])\n    d = ImageDraw.Draw(img)\n"
    assert _bare(tmp_path, body) is None


def test_draw_bound_in_the_same_function_before_the_call_gets_a_hint(tmp_path):
    body = _PIL + "def f(img):\n    d = ImageDraw.Draw(img)\n    d.line([(0, 0), (1, 1)])\n"
    assert _bare(tmp_path, body) == _NO_PATH


def test_draw_and_font_combination_evidence_is_unchanged_by_scoping(tmp_path):
    body = "from PIL import Image, ImageDraw, ImageFont\nim = Image.open('a.png')\nf = ImageFont.truetype('x.ttf', 12)\nd = ImageDraw.Draw(im)\nrender(d, f)\n"
    assert _bare(tmp_path, body) == _NO_PATH
