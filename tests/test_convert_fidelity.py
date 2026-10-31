"""A captured raster drawing cannot certify an editable conversion."""

import ast

import pytest

from tavotto.engine import ai_convert, convertfidelity


@pytest.mark.parametrize(
    "source",
    [
        'from PIL import ImageDraw\nd = ImageDraw.Draw(image)\nd.text((0,0), "hello")',
        "import PIL.ImageDraw as D\nD.Draw(image).ellipse((0,0,10,10))",
        "from PIL.ImageDraw import Draw as make\ndef main():\n d = make(mask)\n d.polygon(points)",
        "from PIL import ImageDraw as D\nd = D.Draw(mask)\nother = d\nother.line(points)",
    ],
)
def test_proven_raster_paint_is_rejected(source):
    assert convertfidelity.raster_drawing(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        'from PIL import ImageDraw\nd = ImageDraw.Draw(image)\nbox = d.textbbox((0,0), "hello")',
        'from PIL import ImageFont\nfont = ImageFont.truetype("font.ttf", 20)\nfont.getbbox("hi")',
        'from PIL import Image\nphoto = Image.open("photo.png")\nax.imshow(photo)\nax.text(1,2,"hi")',
        'from PIL import ImageDraw\nd = ImageDraw.Draw(image)\nd = ax\nd.text(1,2,"hi")',
        'from PIL import ImageDraw\ndef f(ImageDraw):\n ImageDraw.Draw(x).text(1,2,"hi")',
        'from PIL import ImageDraw\nd = ImageDraw.Draw(image)\nfor d in axes:\n d.text(1,2,"hi")',
        '"ImageDraw.Draw(image).text((0,0), hello)"',
    ],
)
def test_measurement_photographs_and_unproven_receivers_are_not_rejected(source):
    assert not convertfidelity.raster_drawing(ast.parse(source))


def test_raster_draw_is_rejected_before_any_validation_runner(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "plot.py"
    source.write_text("from PIL import Image\n")
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "cache")
    cand = ai_convert.create_candidate("abcdef123456", source, root, "plot.py", library="pillow")
    cand.target.write_text(
        "import matplotlib.pyplot as plt\nfrom PIL import ImageDraw\nImageDraw.Draw(mask).ellipse((0,0,10,10))\n"
    )
    called = []
    cand.extras["runner"] = lambda *_a, **_k: called.append(True)
    ok, reason = ai_convert.agent_check(cand)
    assert not ok and reason.startswith("raster_drawing:")
    assert not called
