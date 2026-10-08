"""「跑完没出图」的原因提示（`engine/rasterhint.py`）：Pillow / OpenCV 直接写图片的脚本 vs 正常 Matplotlib 脚本。

判据的主语是**脚本源码（含有界跟进的本地模块）**——不是运行结果：这里没有任何进程，只有 AST。
正例必须出提示；反例（matplotlib、只用 PIL 读图再用 matplotlib 画、`np.save`、存的不是图片）必须沉默。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tavotto.engine import rasterhint


def _write(root: Path, name: str, body: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")


def _lib(root: Path, script: str = "make.py") -> str | None:
    hint = rasterhint.detect(root, script)
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
        ("import PIL.Image as I\nim = I.new('L', (2, 2))\nim.save(f'out_{1}.jpg')\n", "pillow"),
        ("from PIL import Image\nim = Image.open('a.png')\nim.save(path)\n", "pillow"),
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


def test_matplotlib_imported_but_never_used_is_still_silent(tmp_path):
    """取舍：import 了 matplotlib 就不提示——没出图多半是别的原因，说「是 Pillow 画的」会误导。"""
    _write(tmp_path, "make.py", "import matplotlib\n" + textwrap.dedent(PILLOW_SCRIPT))
    assert _lib(tmp_path) is None


def test_matplotlib_family_without_a_direct_import_is_silent(tmp_path):
    _write(tmp_path, "make.py", "import seaborn as sns\n" + textwrap.dedent(PILLOW_SCRIPT))
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


def test_a_followed_local_module_importing_matplotlib_silences_it(tmp_path):
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


def test_when_the_scan_cannot_see_everything_it_stays_silent(tmp_path):
    """动态 import 可能装进一个没扫过的 matplotlib——看不全就不说。"""
    _write(
        tmp_path,
        "make.py",
        textwrap.dedent(PILLOW_SCRIPT) + "\nimport importlib\nimportlib.import_module(name)\n",
    )
    assert _lib(tmp_path) is None


# ---- in_project：素材库按钮只在能确定那张图落进素材盘点范围时才给 ----


def _hint(root: Path, body: str, cwd_mode: str | None = "project") -> dict:
    _write(root, "make.py", body)
    hint = rasterhint.detect(root, "make.py", cwd_mode)
    assert hint is not None
    return hint


@pytest.mark.parametrize(
    "target",
    [
        "/tmp/out.png",
        "~/out.png",
        "../out.png",
        "sub/../../out.png",
        "C:/out.png",
        "\\\\host\\share\\out.png",
    ],
)
def test_saving_outside_the_project_keeps_the_reason_but_not_the_assets_button(tmp_path, target):
    hint = _hint(tmp_path, f"from PIL import Image\nImage.new('L', (2, 2)).save({target!r})\n")
    assert hint["library"] == "pillow"
    assert hint["in_project"] is False


def test_a_literal_relative_path_in_project_mode_is_in_project(tmp_path):
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('out/a.png')\n"
    assert _hint(tmp_path, body, "project")["in_project"] is True
    assert _hint(tmp_path, body, "project_root")["in_project"] is True


@pytest.mark.parametrize("mode", ["sandbox", None])
def test_sandbox_or_unknown_workdir_mode_is_never_in_project(tmp_path, mode):
    body = "from PIL import Image\nImage.new('L', (2, 2)).save('out.png')\n"
    assert _hint(tmp_path, body, mode)["in_project"] is False


@pytest.mark.parametrize(
    "body",
    [
        "from PIL import Image\nImage.new('L', (2, 2)).save(path)\n",
        "from PIL import Image\nImage.new('L', (2, 2)).save(f'{name}.png')\n",
    ],
)
def test_a_dynamic_path_cannot_be_confirmed_in_project(tmp_path, body):
    assert _hint(tmp_path, body)["in_project"] is False


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
    assert rasterhint.detect(tmp_path, "scripts/make.py", "project")["in_project"] is False
    assert rasterhint.detect(tmp_path, "scripts/make.py", "project_root")["in_project"] is True
    # 普通子目录里的脚本：两种模式都可见
    _write(tmp_path, "analysis/make.py", body)
    assert rasterhint.detect(tmp_path, "analysis/make.py", "project")["in_project"] is True
    # 脚本在 .hidden 目录：project 模式相对脚本目录，落进隐藏目录
    _write(tmp_path, ".tools/make.py", body)
    assert rasterhint.detect(tmp_path, ".tools/make.py", "project")["in_project"] is False


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
