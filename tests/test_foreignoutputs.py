"""ADR 0184 修订：R / MATLAB 脚本与 Python 一样认作图的来源——只读源码里写死的输出路径，不运行。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import figuregroup, foreignoutputs, sourcelang

R = sourcelang.R
M = sourcelang.MATLAB


@pytest.mark.parametrize(
    "src, want",
    [
        ('ggsave("fig1.pdf", p, width = 4)', ["fig1.pdf"]),
        ("ggplot2::ggsave(filename = 'out/fig1.png', plot = p)", ["out/fig1.png"]),
        (
            'ggsave(plot = p, filename = file.path("figs", "png", "fig1.png"))',
            ["figs/png/fig1.png"],
        ),
        ('ggsave(here::here("figs", "fig1.svg"), p)', ["figs/fig1.svg"]),
        ('pdf("fig2.pdf", width = 5)\nplot(x)\ndev.off()', ["fig2.pdf"]),
        ('png(filename = "fig3.png", res = 300)', ["fig3.png"]),
        ('cairo_pdf("a.pdf"); svg(file = "a.svg")', ["a.pdf", "a.svg"]),
        ('dev.copy(pdf, "copy.pdf")', ["copy.pdf"]),
        ('dev.print(png, file = "p.png", width = 600)', ["p.png"]),
        # ggsave 的 path（关键字 / 第 4 个位置参数）拼在前面；path 不是字面量就整条不认（Codex #1906）
        ('ggsave("fig.pdf", path = "plots")', ["plots/fig.pdf"]),
        ('ggsave("fig.pdf", p, "pdf", "plots")', ["plots/fig.pdf"]),
        ('ggsave(filename = "fig.png", path = file.path("out", "png"))', ["out/png/fig.png"]),
        ('ggsave("fig.pdf", path = out_dir)', []),
        # path = NULL 是 ggsave 的缺省值：文件名原样（Codex #1906 复核）
        ('ggsave("fig.pdf", path = NULL)', ["fig.pdf"]),
        ('ggsave("fig.pdf", p, "pdf", NULL)', ["fig.pdf"]),
        # file.path 的第一段是根：保留，不变成相对路径（Codex #1906）
        ('ggsave(file.path("/tmp", "fig.pdf"))', ["/tmp/fig.pdf"]),
        ('ggsave(file.path("/", "fig.pdf"))', ["/fig.pdf"]),
        # 不认：拼出来的 / 变量 / 不是素材格式 / 注释里的
        ('ggsave(paste0("fig", i, ".pdf"))', []),
        ("ggsave(out_path)", []),
        ('postscript("x.eps")', []),
        ('# ggsave("commented.pdf")\nx <- "ggsave(\\"in_string.pdf\\")"', []),
        ('write.csv(df, "data.csv")', []),
    ],
)
def test_r_outputs(src, want):
    assert foreignoutputs.outputs_of(src, R) == want


@pytest.mark.parametrize(
    "src, want",
    [
        ("saveas(gcf, 'fig1.png')", ["fig1.png"]),
        ("saveas(h, 'fig1', 'pdf')", ["fig1.pdf"]),
        ("exportgraphics(ax, \"figs/fig1.pdf\", 'ContentType', 'vector')", ["figs/fig1.pdf"]),
        ("print(gcf, '-dpng', '-r300', 'fig2')", ["fig2.png"]),
        ("print('-dsvg', fullfile('figs', 'svg', 'fig2.svg'))", ["figs/svg/fig2.svg"]),
        ("exportgraphics(gcf, fullfile('/data/out', 'f.pdf'))", ["/data/out/f.pdf"]),
        ("exportgraphics(gcf, fullfile('C:\\', 'f.pdf'))", ["C:\\f.pdf"]),
        ("print -dpdf fig3.pdf", ["fig3.pdf"]),
        ("print -depsc fig4", []),
        # 转置不是字符串开头；注释不算
        ("y = x';\n% saveas(gcf, 'commented.png')\nsaveas(gcf, 'real.png')", ["real.png"]),
        ("saveas(gcf, name)", []),
        ("saveas(gcf, ['fig', num2str(i), '.png'])", []),
        ("title('it''s fine'); exportgraphics(gcf, 'q.png')", ["q.png"]),
    ],
)
def test_matlab_outputs(src, want):
    assert foreignoutputs.outputs_of(src, M) == want


R_SCRIPT = (
    "library(ggplot2)\n"
    "p <- ggplot(df, aes(x, y)) + geom_point()\n"
    'ggsave("fig1.pdf", p)\n'
    'ggsave(file.path("png", "fig1.png"), p)\n'
    'ggsave("../outside.pdf", p)\n'
)
M_SCRIPT = (
    "% 画一条曲线\nclear all; clc;\nx = linspace(0, 1, 50);\ny = x.^2;\nfigure;\nplot(x, y);\n"
    "xlabel('x');\nsaveas(gcf, 'svg/fig2.svg');\nexportgraphics(gcf, 'fig2.pdf');\n"
)


def test_output_map_resolves_relative_to_script_dir(tmp_path: Path):
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "plot.R").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / "fig2.m").write_text(M_SCRIPT, encoding="utf-8")
    got = foreignoutputs.output_map(tmp_path)
    assert got == {
        "analysis/fig1.pdf": "analysis/plot.R",
        "analysis/png/fig1.png": "analysis/plot.R",
        "outside.pdf": "analysis/plot.R",  # ../ 回到项目根仍在项目里
        "svg/fig2.svg": "fig2.m",
        "fig2.pdf": "fig2.m",
    }


def test_output_claimed_by_two_scripts_is_nobodys(tmp_path: Path):
    (tmp_path / "a.R").write_text('library(ggplot2)\nggsave("same.pdf")\n', encoding="utf-8")
    (tmp_path / "b.R").write_text('library(ggplot2)\nggsave("same.pdf")\n', encoding="utf-8")
    assert "same.pdf" not in foreignoutputs.output_map(tmp_path)


def test_absolute_join_outside_project_never_claims_a_local_file(tmp_path: Path):
    # file.path("/tmp", "fig.pdf") 写到项目外：不许被当成脚本旁边的 tmp/fig.pdf（Codex #1906）
    (tmp_path / "tmp").mkdir()
    (tmp_path / "a.R").write_text(
        'library(ggplot2)\nggsave(file.path("/tmp", "fig.pdf"))\n', encoding="utf-8"
    )
    assert "tmp/fig.pdf" not in foreignoutputs.output_map(tmp_path)


def test_absolute_join_inside_project_is_claimed(tmp_path: Path):
    out = (tmp_path / "plots").as_posix()
    (tmp_path / "a.R").write_text(
        f'library(ggplot2)\nggsave(file.path("{out}", "fig.pdf"))\n', encoding="utf-8"
    )
    assert foreignoutputs.output_map(tmp_path) == {"plots/fig.pdf": "a.R"}


def test_paths_leaving_the_project_are_dropped(tmp_path: Path):
    (tmp_path / "a.R").write_text(
        'library(ggplot2)\nggsave("../../x.pdf")\nggsave("~/y.pdf")\nggsave("/tmp/z.pdf")\n',
        encoding="utf-8",
    )
    assert foreignoutputs.output_map(tmp_path) == {}


def test_r_and_matlab_figures_group_like_python(tmp_path: Path):
    foreign = {"fig1.pdf": "plot.R", "png/fig1.png": "plot.R", "elsewhere/fig1.svg": "plot.R"}
    panels = [
        {"id": "fig1.pdf", "kind": "pdf"},
        {"id": "png/fig1.png", "kind": "raster", "px_w": 10, "px_h": 10},
        {"id": "elsewhere/fig1.svg", "kind": "svg"},
        {"id": "fig9.png", "kind": "raster"},
        {"id": "fig9.pdf", "kind": "pdf"},
    ]
    figuregroup.annotate(
        panels, None, foreign, sibling=lambda rel: "fig9.m" if rel.startswith("fig9") else None
    )
    by = {p["id"]: p for p in panels}
    # 同一个 R 脚本存的同名文件跨目录合成一张图，PDF 是主文件
    assert {by[i]["figure_key"] for i in foreign} == {"foreign:plot.R|fig1"}
    assert by["fig1.pdf"]["primary"] and by["fig1.pdf"]["source_script"] == "plot.R"
    assert not by["elsewhere/fig1.svg"]["primary"]
    # 同目录同名的 MATLAB 脚本也是来源
    assert by["fig9.pdf"]["source_script"] == "fig9.m" and by["fig9.pdf"]["primary"]
    # 来源不进 `script`（那个字段只表示 Python 注册表、可进图内编辑）
    assert "script" not in by["fig1.pdf"]
