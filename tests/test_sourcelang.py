"""来源语言识别（`engine/sourcelang.py`，ADR 0143 修订「R / MATLAB 来源」）：.R/.r → r；.m 要区分 MATLAB 与 Objective-C，
判不出就 None（拒绝，不猜）；体积上限与非 UTF-8 都是 None。"""

from __future__ import annotations

import pytest

from tavotto.engine import sourcelang

R_SCRIPT = 'library(ggplot2)\ndf <- read.csv("data.csv")\nggplot(df, aes(x, y)) + geom_point()\n'
M_SCRIPT = "% 画一条曲线\nclear all; clc;\nx = linspace(0, 1, 50);\ny = x.^2;\nfigure;\nplot(x, y);\nxlabel('x');\n"
M_FUNCTION = "function out = f(x)\n  out = x + 1;\nend\n"
OBJC_IMPORT = "#import <Foundation/Foundation.h>\n\nint main(int argc, const char * argv[]) {\n  return 0;\n}\n"
OBJC_INTERFACE = "@interface Foo : NSObject\n- (void)bar;\n@end\n"
OBJC_IMPLEMENTATION = "@implementation Foo\n- (void)bar { }\n@end\n"


@pytest.mark.parametrize("name", ["fig.R", "fig.r", "dir/sub/Plot.R"])
def test_r_scripts_are_recognised_by_suffix(name):
    assert sourcelang.detect(name, R_SCRIPT) == "r"


def test_python_is_recognised_by_suffix():
    assert sourcelang.detect("fig.py", "import matplotlib\n") == "python"


@pytest.mark.parametrize(
    "name", ["a.Rmd", "a.qmd", "a.mlx", "a.fig", "a.rds", "a.txt", "a", "a.RData", "a.mat"]
)
def test_out_of_scope_suffixes_are_refused(name):
    assert sourcelang.detect(name, R_SCRIPT) is None


@pytest.mark.parametrize("text", [M_SCRIPT, M_FUNCTION + "% 注释\n", "% c\nx = [1 2; 3 4];\n"])
def test_matlab_dot_m_with_matlab_features(text):
    assert sourcelang.detect("fig.m", text) == "matlab"


@pytest.mark.parametrize(
    "text", [OBJC_IMPORT, OBJC_INTERFACE, OBJC_IMPLEMENTATION, "#include <stdio.h>\nint x;\n"]
)
def test_objective_c_dot_m_is_refused(text):
    assert sourcelang.detect("main.m", text) is None


def test_objective_c_markers_win_even_when_matlab_features_are_present():
    # 注释里的 `%` 与 plot( 都有，但 `@interface` 在，仍是 Obj-C：宁可拒绝也不猜
    assert sourcelang.detect("x.m", M_SCRIPT + OBJC_INTERFACE) is None


def test_matlab_anonymous_functions_are_not_mistaken_for_objective_c():
    text = "% 匿名函数\nf = @(x) x.^2;\ng = @sin;\nfigure;\nplot(f(0:0.1:1));\n"
    assert sourcelang.detect("anon.m", text) == "matlab"


@pytest.mark.parametrize("text", ["x = 1;\n", "plot(x)\n", "% only a comment\n", "just some words"])
def test_single_weak_feature_is_not_enough_for_matlab(text):
    assert sourcelang.detect("weak.m", text) is None


@pytest.mark.parametrize("text", ["", "   \n\n"])
def test_empty_files_are_refused(text):
    assert sourcelang.detect("a.R", text) is None
    assert sourcelang.detect("a.m", text) is None


def test_non_utf8_bytes_are_refused():
    assert sourcelang.detect("a.R", "x <- 'é'".encode("latin-1")) is None
    assert sourcelang.detect("a.R", R_SCRIPT.encode("utf-8")) == "r"
    assert (
        sourcelang.detect("a.R", b"\xef\xbb\xbf" + R_SCRIPT.encode("utf-8")) == "r"
    )  # UTF-8 BOM 可以


def test_nul_bytes_mean_binary():
    assert sourcelang.detect("a.R", "x <- 1\x00\n") is None


def test_size_limit_is_enforced_for_str_and_bytes():
    ok = "x <- 1\n" + "#" * (sourcelang.MAX_BYTES - 10)
    assert sourcelang.detect("a.R", ok) == "r"
    big = "x <- 1\n" + "#" * sourcelang.MAX_BYTES
    assert sourcelang.detect("a.R", big) is None
    assert sourcelang.detect("a.R", big.encode()) is None
    # 多字节字符按字节数算，不按字符数
    assert sourcelang.detect("a.R", "é" * (sourcelang.MAX_BYTES // 2 + 1)) is None


def test_detect_file_reads_bounded_and_returns_none_for_missing(tmp_path):
    p = tmp_path / "fig.R"
    p.write_bytes(R_SCRIPT.encode())
    assert sourcelang.detect_file(p) == "r"
    assert sourcelang.detect_file(tmp_path / "ghost.R") is None
    big = tmp_path / "big.R"
    big.write_bytes(b"x <- 1\n" + b"#" * sourcelang.MAX_BYTES)
    assert sourcelang.detect_file(big) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.R", "r"),
        ("a.r", "r"),
        ("/Users/me/dir/fig.R", "r"),
        ("a.m", "matlab"),
        ("dir.with.dots/a.m", "matlab"),
        ("a.py", None),
        ("a.Rmd", None),
        ("a.qmd", None),
        ("a.mlx", None),
        ("a.rds", None),
        ("a.M", None),
        (".R", None),
        ("fig", None),
    ],
)
def test_extension_hint_is_suffix_only(name, expected):
    """与 `web/src/lib/foreignScript.test.ts` 同一组用例（严格同源对，docs/rules/repo/same-origin-pairs.md）。"""
    assert sourcelang.extension_hint(name) == expected
