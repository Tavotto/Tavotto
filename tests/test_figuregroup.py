"""ADR 0184 §一：同一张图的几份文件——图键与主文件选择。"""

from __future__ import annotations

from tavotto.engine import figuregroup


class _Reg:
    """只实现 `for_artifact` 的注册表替身：`{rel: stem}`。"""

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def for_artifact(self, rel: str):
        stem = self.mapping.get(rel)
        return None if stem is None else {"script": "plot.py", "stem": stem}


def test_same_dir_same_stem_is_one_figure():
    assert figuregroup.figure_key("figs/fig1.pdf") == figuregroup.figure_key("figs/fig1.png")
    assert figuregroup.figure_key("fig1.svg") == figuregroup.figure_key("fig1.pdf") == "dir:fig1"


def test_format_named_subdirs_merge_into_parent():
    k = figuregroup.figure_key("figures/fig1.pdf")
    assert figuregroup.figure_key("figures/png/fig1.png") == k
    assert figuregroup.figure_key("figures/SVG/fig1.svg") == k
    assert figuregroup.figure_key("pdf/fig1.pdf") == figuregroup.figure_key("png/fig1.png")


def test_plain_dirs_never_merge_without_a_script():
    assert figuregroup.figure_key("a/fig1.png") != figuregroup.figure_key("b/fig1.png")
    # 名字像格式但不是格式名的目录不算
    assert figuregroup.figure_key("pngs/fig1.png") != figuregroup.figure_key("fig1.pdf")


def test_windows_separators_are_normalised():
    assert figuregroup.figure_key("figures\\png\\fig1.png") == figuregroup.figure_key(
        "figures/fig1.pdf"
    )


def test_script_backed_files_merge_across_any_dirs():
    reg = _Reg({"out/a/fig1.pdf": "fig1", "elsewhere/fig1.png": "fig1"})
    assert figuregroup.figure_key("out/a/fig1.pdf", reg) == "script:fig1"
    assert figuregroup.figure_key("elsewhere/fig1.png", reg) == "script:fig1"
    # 注册表认不得的退回目录键，不被并进脚本的图
    assert figuregroup.figure_key("other/fig1.png", reg) == "dir:other/fig1"


def test_registry_failure_falls_back_to_dir_key():
    class Broken:
        def for_artifact(self, rel):
            raise OSError("boom")

    assert figuregroup.figure_key("x/fig1.pdf", Broken()) == "dir:x/fig1"


def test_primary_prefers_pdf_then_largest_raster_then_svg():
    e = [
        {"id": "f/png/a.png", "kind": "raster", "px_w": 100, "px_h": 100},
        {"id": "f/a.svg", "kind": "svg"},
        {"id": "f/a.pdf", "kind": "pdf"},
    ]
    assert figuregroup.pick_primary(e) == "f/a.pdf"
    # 没有 PDF 时挑位图，不挑 SVG：没编辑过的 SVG 素材进不了画布合成导出（Codex #1906）
    assert figuregroup.pick_primary(e[:2]) == "f/png/a.png"
    assert figuregroup.pick_primary(e[1:2]) == "f/a.svg"
    rasters = [
        {"id": "a.jpg", "kind": "raster", "px_w": 10, "px_h": 10},
        {"id": "png/a.png", "kind": "raster", "px_w": 300, "px_h": 200},
    ]
    assert figuregroup.pick_primary(rasters) == "png/a.png"
    assert figuregroup.pick_primary([]) is None


def test_annotate_adds_fields_without_touching_others():
    panels = [
        {"id": "f/a.png", "kind": "raster", "px_w": 1, "px_h": 1, "name": "a"},
        {"id": "f/a.svg", "kind": "svg", "name": "a"},
        {"id": "g/a.pdf", "kind": "pdf", "name": "a"},
    ]
    figuregroup.annotate(panels)
    by = {p["id"]: p for p in panels}
    assert by["f/a.png"]["primary"] is True and by["f/a.svg"]["primary"] is False
    assert by["f/a.png"]["formats"] == ["f/a.png", "f/a.svg"]
    assert by["g/a.pdf"]["primary"] is True and by["g/a.pdf"]["formats"] == ["g/a.pdf"]
    assert by["f/a.png"]["name"] == "a"  # 已有字段不动
