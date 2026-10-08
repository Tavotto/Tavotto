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
