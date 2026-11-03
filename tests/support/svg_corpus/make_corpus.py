"""Generate the SVG corpus fixtures with independently known data.

Every generated chart ships a companion ``*.data.csv`` holding the exact
arrays the chart was drawn from, so tests can verify computed geometry
against independent numbers instead of trusting any parser.  Hand-authored
SVGs (plain shapes, outlined text, use/groups, clip/opacity, gradient and
image, and a deliberately unsafe sample) live beside the generated ones;
per-file provenance and chart meaning are documented in ``README.md``.

Run with the implementation venv:

    .venv/Scripts/python.exe tests/support/svg_corpus/make_corpus.py

The script only writes inside this directory and is deterministic apart
from matplotlib's metadata timestamp, which is stripped after saving.
"""

from __future__ import annotations

import base64
import csv
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# 生成器约定：除 text_outlined（转曲样本）外，图表一律保留 <text>（ADR 0144 的
# 默认方向），让语料同时覆盖「可编辑文字」与「转曲路径」两类。
matplotlib.rcParams["svg.fonttype"] = "none"

HERE = Path(__file__).resolve().parent


def _save(fig: plt.Figure, name: str) -> None:
    """Save deterministically: hash keys for ids, metadata date stripped."""
    fig.savefig(HERE / name, format="svg")
    plt.close(fig)
    path = HERE / name
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<dc:date>[^<]*</dc:date>\s*", "", text)
    text = re.sub(r"<dc:creator>.*?</dc:creator>\s*", "", text, flags=re.S)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_csv(name: str, header: list[str], rows) -> None:
    with (HERE / name).open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def make_line_two_series() -> None:
    """Two curves over the same x: y1 = x^2, y2 = 100 - 5x, x = 0..10.

    Chart meaning: 'growth' (parabola) vs 'decline' (line) on linear axes,
    with a legend; ticks are matplotlib defaults.  Every visible point is
    recoverable from the CSV by the axes transform alone.
    """
    x = np.arange(0, 11, dtype=float)
    y1 = x**2
    y2 = 100.0 - 5.0 * x
    fig, ax = plt.subplots(figsize=(5, 3.5), dpi=100)
    ax.plot(x, y1, color="#1f77b4", linewidth=2.0, marker="o", label="growth")
    ax.plot(x, y2, color="#d62728", linewidth=2.0, linestyle="--", label="decline")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("signal (a.u.)")
    ax.set_title("Two known series")
    ax.legend(loc="upper left")
    fig.tight_layout()
    _save(fig, "line_two_series.svg")
    _write_csv(
        "line_two_series.data.csv",
        ["x", "growth", "decline"],
        zip(x.tolist(), y1.tolist(), y2.tolist()),
    )


def make_bar_error() -> None:
    """Four bars with symmetric error bars; legend labels one series.

    Chart meaning: measured means with standard deviation; the error
    arrays are model facts (not derived statistics of hidden samples).
    """
    cats = ["A", "B", "C", "D"]
    values = [12.0, 25.0, 18.0, 31.0]
    err = [1.5, 3.0, 2.0, 2.5]
    fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
    ax.bar(cats, values, yerr=err, capsize=4, color="#2ca02c", label="mean ± sd")
    ax.set_ylabel("yield (mg)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    _save(fig, "bar_error.svg")
    _write_csv(
        "bar_error.data.csv",
        ["category", "value", "error"],
        zip(cats, values, err),
    )


def make_heatmap() -> None:
    """5x7 matrix with vmin/vmax known and a vertical colorbar.

    Chart meaning: scalar field sample; colormap is viridis with fixed
    vmin=0 vmax=1 so cell colours are checkable against the norm.
    """
    rng = np.random.default_rng(42)
    grid = np.round(rng.random((5, 7)), 3)
    fig, ax = plt.subplots(figsize=(4.5, 3), dpi=100)
    im = ax.imshow(grid, cmap="viridis", vmin=0.0, vmax=1.0, origin="upper")
    fig.colorbar(im, ax=ax)
    ax.set_title("Scalar field")
    fig.tight_layout()
    _save(fig, "heatmap_colorbar.svg")
    _write_csv(
        "heatmap_colorbar.data.csv",
        [f"col{j}" for j in range(grid.shape[1])],
        grid.tolist(),
    )


def make_text_outlined() -> None:
    """Same two-series chart with text converted to glyph outlines.

    Chart meaning: identical data to ``line_two_series``; used to assert
    that outlined labels are paths, not editable characters.
    """
    x = np.arange(0, 11, dtype=float)
    fig, ax = plt.subplots(figsize=(5, 3.5), dpi=100)
    ax.plot(x, x**2, color="#1f77b4", label="growth")
    ax.set_title("Outlined text")
    ax.legend()
    matplotlib.rcParams["svg.fonttype"] = "path"
    try:
        _save(fig, "text_outlined.svg")
    finally:
        matplotlib.rcParams["svg.fonttype"] = "none"


def _tiny_png_data_uri() -> str:
    """A 2x2 PNG (blue/red rows) as a data URI for the image sample."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"\x00\x00\x00\xff\xff\x00\x00" + b"\x00\xff\x00\x00\x00\x00\xff"
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def write_hand_authored() -> None:
    """Plain, static SVGs written by hand (no matplotlib involvement)."""
    (HERE / "plain_shapes.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"
     viewBox="0 0 400 300">
  <rect x="0" y="0" width="400" height="300" fill="white"/>
  <!-- axis frame: plot area is (60,40)-(360,260); data x in [0,10] ->
       60+30x px, y in [0,100] -> 260-2.2y px (known binding for tests) -->
  <rect x="60" y="40" width="300" height="220" fill="none" stroke="black"/>
  <circle cx="120" cy="216" r="3" fill="#1f77b4"/>
  <circle cx="180" cy="172" r="3" fill="#1f77b4"/>
  <circle cx="240" cy="106" r="3" fill="#1f77b4"/>
  <path d="M 60 260 L 360 260" stroke="black" stroke-width="1"/>
  <path d="M 60 260 L 60 40" stroke="black" stroke-width="1"/>
  <text x="200" y="290" font-family="sans-serif" font-size="12"
        text-anchor="middle">time (s)</text>
  <text x="20" y="150" font-family="sans-serif" font-size="12"
        transform="rotate(-90 20 150)">signal (a.u.)</text>
</svg>
""",
        encoding="utf-8",
    )
    (HERE / "use_and_groups.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"
     viewBox="0 0 200 120">
  <defs>
    <rect id="chip" x="0" y="0" width="40" height="20" fill="#dde"/>
  </defs>
  <g id="outer" transform="translate(10,10)">
    <g id="inner" transform="translate(20,30)">
      <use href="#chip"/>
      <use href="#chip" x="60" y="40" transform="rotate(15)"/>
    </g>
    <rect x="100" y="10" width="30" height="30" fill="orange"/>
  </g>
</svg>
""",
        encoding="utf-8",
    )
    (HERE / "clip_opacity.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"
     viewBox="0 0 200 200">
  <defs>
    <clipPath id="half">
      <rect x="0" y="0" width="100" height="200"/>
    </clipPath>
  </defs>
  <rect x="0" y="0" width="200" height="200" fill="white"/>
  <g clip-path="url(#half)">
    <rect x="20" y="20" width="160" height="160" fill="#1f77b4"
          fill-opacity="0.5"/>
  </g>
  <g opacity="0.5">
    <rect x="60" y="60" width="100" height="100" fill="#d62728"/>
  </g>
</svg>
""",
        encoding="utf-8",
    )
    (HERE / "gradient_image.svg").write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"
     viewBox="0 0 160 160">
  <defs>
    <linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#1f77b4"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="160" height="160" fill="url(#fade)"/>
  <image x="20" y="20" width="40" height="40" href="{_tiny_png_data_uri()}"/>
</svg>
""",
        encoding="utf-8",
    )
    # Deliberately unsafe: used only by negative tests that assert rejection.
    (HERE / "external_danger.svg").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
     viewBox="0 0 100 100">
  <rect width="100" height="100" fill="white"/>
  <script type="text/javascript">alert(1)</script>
  <a href="https://example.invalid"><text x="10" y="50">link</text></a>
  <image href="https://example.invalid/tracker.png" width="10" height="10"/>
  <text x="10" y="80">&xxe;</text>
</svg>
""",
        encoding="utf-8",
    )


def main() -> None:
    make_line_two_series()
    make_bar_error()
    make_heatmap()
    make_text_outlined()
    write_hand_authored()
    print(f"corpus written to {HERE}")


if __name__ == "__main__":
    main()
