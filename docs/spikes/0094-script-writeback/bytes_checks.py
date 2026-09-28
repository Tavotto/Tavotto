"""ADR 0094 spike：字节与脱离 Tavotto 运行的检查。

1. 编码 / 换行 / BOM / 缩进变体：插入后编码与换行风格不变、删块逐字节还原、插两次 == 插一次；
2. 「终端里跑」：纯 matplotlib（本进程不 import tavotto）执行写回后的脚本，钩子在 savefig
   那一刻改图；
3. 目标身份守卫：写回之后用户在脚本里重排 / 删掉曲线，块按 label 找对象，找不到只告警、
   脚本照常跑完；无 label 的曲线按位置（ADR 0083 的同一条已知边界）。
"""

from __future__ import annotations

import codecs
import json
import os
import runpy
import shutil
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import emit  # noqa: E402

META = {"version": "spike", "date": "2026-09-26", "backup": "<spike>"}
MAGENTA = "#d6278f"
OUT = HERE / "bytes"


def _block(patches, manifest_elements, **kw):
    b, rep = emit.build_block(
        {"Fig1_kinetics": patches}, {"Fig1_kinetics": {"elements": manifest_elements}}, META, **kw
    )
    return b, rep


FIG1_ELEMENTS = [
    {
        "gid": "axes_0.lines_0",
        "role": "line",
        "editable": [{"prop": "label", "value": "Catalyst (k = 0.125 $\\mathrm{min^{-1}}$)"}],
    },
    {"gid": "axes_0.xlabel", "role": "axis_label"},
]
PATCHES = [
    {"gid": "axes_0.lines_0", "prop": "color", "value": MAGENTA},
    {"gid": "axes_0.xlabel", "prop": "fontsize", "value": 11.0},
]


def run_plain(dirpath: Path, script: str, entry: str = "main"):
    """像终端一样跑脚本（本进程、纯 matplotlib），savefig 只记图。返回 {stem: fig}, 告警。"""
    captured = {}
    real = matplotlib.figure.Figure.savefig

    def capture(self, fname, *a, **k):
        captured[os.path.splitext(os.path.basename(os.fspath(fname)))[0]] = self

    matplotlib.figure.Figure.savefig = capture
    old = os.getcwd()
    os.chdir(dirpath)
    sys.path.insert(0, str(dirpath))
    for m in ("paper_style",):
        sys.modules.pop(m, None)
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ns = runpy.run_path(str(dirpath / script), run_name="spike")
            ns[entry]()
    finally:
        os.chdir(old)
        sys.path.remove(str(dirpath))
        matplotlib.figure.Figure.savefig = real
    return captured, [str(x.message) for x in w if "Tavotto" in str(x.message)]


def _colors(fig):
    from matplotlib.colors import to_hex

    return {ln.get_label(): to_hex(ln.get_color()) for ln in fig.axes[0].lines}


def variants(src: bytes) -> dict[str, bytes]:
    text = src.decode("utf-8")
    zh = "# 中文注释：μ 与 ≤ 这类字符也要原样保留\n"
    return {
        "utf8_lf": src,
        "utf8_crlf": text.replace("\n", "\r\n").encode("utf-8"),
        "utf8_bom_crlf": codecs.BOM_UTF8 + text.replace("\n", "\r\n").encode("utf-8"),
        "gbk_cookie": ("# -*- coding: gbk -*-\n" + zh.replace("μ 与 ≤ ", "") + text).encode("gbk"),
        "tabs": text.replace("    ", "\t").encode("utf-8"),
        "latin1_cookie": (
            "# -*- coding: latin-1 -*-\n# caf\xe9\n" + text.split('"""', 2)[2]
        ).encode("latin-1"),
    }


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir()
    src = (HERE / "examples" / "fig1_kinetics.py").read_bytes()
    report = {"encoding": [], "guard": []}
    for name, raw in variants(src).items():
        d = OUT / name
        d.mkdir()
        shutil.copy(HERE / "examples" / "paper_style.py", d / "paper_style.py")
        info = emit.detect(raw)
        row = {
            "variant": name,
            "encoding": info["encoding"],
            "newline": repr(info["newline"]),
            "indent": repr(info["indent"]),
        }
        try:
            block, _rep = _block(PATCHES, FIG1_ELEMENTS, indent=info["indent"])
            try:
                new = emit.insert_block(raw, block)
                row["ascii_block"] = False
            except UnicodeEncodeError:
                block, _rep = _block(PATCHES, FIG1_ELEMENTS, indent=info["indent"], ascii_only=True)
                new = emit.insert_block(raw, block)
                row["ascii_block"] = True
            row["bom_kept"] = new.startswith(codecs.BOM_UTF8) == raw.startswith(codecs.BOM_UTF8)
            body = new.decode(info["encoding"])
            if info["newline"] == "\r\n":
                row["newline_kept"] = body.count("\n") == body.count("\r\n")
            else:
                row["newline_kept"] = "\r" not in body
            row["roundtrip"] = emit.remove_block(new) == raw
            row["idempotent"] = emit.insert_block(new, block) == new
            (d / "fig1_kinetics.py").write_bytes(new)
            figs, warns = run_plain(d, "fig1_kinetics.py")
            row["terminal_applied"] = (
                _colors(figs["Fig1_kinetics"]).get(FIG1_ELEMENTS[0]["editable"][0]["value"])
                == MAGENTA
            )
            row["terminal_warnings"] = warns
            row["tavotto_imported"] = any(m.startswith("tavotto") for m in sys.modules)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        plt.close("all")
        report["encoding"].append(row)
        print(row)

    # --- 目标身份守卫：写回之后脚本又被用户改了 ---------------------------------
    d = OUT / "guard"
    d.mkdir()
    shutil.copy(HERE / "examples" / "paper_style.py", d / "paper_style.py")
    block, _ = _block(PATCHES, FIG1_ELEMENTS)
    written = emit.insert_block(src, block).decode("utf-8")
    fast = next(ln for ln in written.splitlines() if "ax.plot(t, fast" in ln)
    slow = next(ln for ln in written.splitlines() if "ax.plot(t, slow" in ln)
    cases = {
        "reordered": written.replace(fast + "\n" + slow, slow + "\n" + fast),
        "catalyst_deleted": written.replace(fast + "\n", ""),
        "unchanged": written,
    }
    for name, text in cases.items():
        assert text != written or name == "unchanged", name
        (d / "fig1_kinetics.py").write_text(text, encoding="utf-8")
        figs, warns = run_plain(d, "fig1_kinetics.py")
        colors = _colors(figs["Fig1_kinetics"])
        magenta = sorted(k for k, v in colors.items() if v == MAGENTA)
        row = {"case": name, "magenta_on": magenta, "warnings": warns}
        report["guard"].append(row)
        print(row)
        plt.close("all")

    # 无 label：按位置（ADR 0083 的已知边界，这里如实量出来）
    spec = (HERE / "examples" / "spectrum.py").read_text(encoding="utf-8")
    b, _ = emit.build_block(
        {"spectrum": [{"gid": "axes_0.lines_0", "prop": "color", "value": MAGENTA}]},
        {
            "spectrum": {
                "elements": [
                    {
                        "gid": "axes_0.lines_0",
                        "role": "line",
                        "editable": [{"prop": "label", "value": ""}],
                    }
                ]
            }
        },
        META,
    )
    w = emit.insert_block(spec.encode(), b).decode()
    w = w.replace(
        "ax.plot(w, signal, lw=1.3)",
        "ax.plot(w, 0.5 * signal, lw=0.8, color='gray')\nax.plot(w, signal, lw=1.3)",
    )
    (d / "spectrum.py").write_text(w, encoding="utf-8")
    figs, warns = run_plain_toplevel(d, "spectrum.py")
    from matplotlib.colors import to_hex

    lines = figs["spectrum"].axes[0].lines
    row = {
        "case": "unlabelled_inserted_before",
        "magenta_line_index": [
            i for i, ln in enumerate(lines) if to_hex(ln.get_color()) == MAGENTA
        ],
        "note": "无 label 按位置：新插的灰线被染色（已知边界）",
    }
    report["guard"].append(row)
    print(row)
    (HERE / "bytes_checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def run_plain_toplevel(dirpath: Path, script: str):
    captured = {}
    real = matplotlib.figure.Figure.savefig

    def capture(self, fname, *a, **k):
        captured[os.path.splitext(os.path.basename(os.fspath(fname)))[0]] = self

    matplotlib.figure.Figure.savefig = capture
    old = os.getcwd()
    os.chdir(dirpath)
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            runpy.run_path(str(dirpath / script), run_name="__main__")
    finally:
        os.chdir(old)
        matplotlib.figure.Figure.savefig = real
    return captured, [str(x.message) for x in w]


if __name__ == "__main__":
    main()
