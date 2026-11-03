"""项目内的 R / MATLAB 入口所需的后端事实（`engine/foreignsource.py`，ADR 0143 修订「项目内入口」）。

守四件事：

1. 素材同目录下有同主干名、且 `sourcelang.detect` 认得出语言的 `.R` / `.r` / `.m` 时，readiness 的该素材带
   `foreign_source = {script, language}`（`/api/panels` 的 capability 同源）；否则是 `null`；
2. 判不出的一律不给：Obj-C 的 `.m`、超 1 MB、非 UTF-8、`.Rmd`、有歧义（两个文件都认得出）；
3. 只在“量过了、没有候选脚本”这一档给——已连接 / 有 Python 候选的图不带；
4. 脚本库的 `foreign_scripts` 是独立字段：R / MATLAB 脚本不进 `all_scripts`（运行环境探测 / 依赖准备 / 可运行脚本判据）。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import project_watch as engine_watch, sourcelang

R_SCRIPT = 'library(ggplot2)\ndf <- read.csv("data.csv")\nggplot(df, aes(x, y)) + geom_point()\n'
M_SCRIPT = (
    "% 画一条曲线\nclear all; clc;\nx = linspace(0, 1, 50);\ny = x.^2;\nfigure;\nplot(x, y);\n"
)
OBJC = "#import <Foundation/Foundation.h>\n\nint main(int argc, const char * argv[]) {\n  return 0;\n}\n"
PY = "import matplotlib.pyplot as plt\n\ndef main():\n    plt.savefig('{stem}.pdf')\n"


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.clear_with(200)
    pix.save(path)


def _open(client, figs: Path):
    client.post("/api/projects/open", json={"path": str(figs), "default": True})


def _panels(client) -> dict[str, dict]:
    body = client.get("/api/project/readiness").get_json()
    return {p["id"]: p for p in body["panels"]}


def _foreign(client, panel_id: str):
    # 素材 id 是 `str(相对路径)`（`/api/panels` 与 readiness 同一份）：Windows 上子目录用反斜杠
    return _panels(client)[str(Path(panel_id))]["foreign_source"]


def test_sibling_r_script_is_reported_with_language(client, tmp_path):
    (tmp_path / "fig3_yield.R").write_text(R_SCRIPT, encoding="utf-8")
    _png(tmp_path / "fig3_yield.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig3_yield.png") == {"script": "fig3_yield.R", "language": "r"}
    cap = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}["fig3_yield.png"]
    assert cap["capability"]["foreign_source"] == {"script": "fig3_yield.R", "language": "r"}
    # 状态与原因码一字不动：仍是 layout_only / no_source_candidate，只是多一个字段
    assert (cap["capability"]["status"], cap["capability"]["reason_code"]) == (
        "layout_only",
        "no_source_candidate",
    )


def test_lowercase_r_and_matlab_siblings(client, tmp_path):
    (tmp_path / "a.r").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / "b.m").write_text(M_SCRIPT, encoding="utf-8")
    _png(tmp_path / "a.png")
    _png(tmp_path / "b.png")
    _open(client, tmp_path)
    assert _foreign(client, "a.png") == {"script": "a.r", "language": "r"}
    assert _foreign(client, "b.png") == {"script": "b.m", "language": "matlab"}


def test_no_sibling_script_means_null(client, tmp_path):
    _png(tmp_path / "plain.png")
    (tmp_path / "other.R").write_text(R_SCRIPT, encoding="utf-8")  # 主干名不同
    _open(client, tmp_path)
    assert _foreign(client, "plain.png") is None


def test_sibling_must_be_in_the_same_directory(client, tmp_path):
    (tmp_path / "rcode").mkdir()
    (tmp_path / "rcode" / "fig.R").write_text(R_SCRIPT, encoding="utf-8")
    _png(tmp_path / "out" / "fig.png")
    _png(tmp_path / "rcode" / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "out/fig.png") is None
    assert _foreign(client, "rcode/fig.png") == {"script": "rcode/fig.R", "language": "r"}


def test_objc_dot_m_is_not_offered(client, tmp_path):
    (tmp_path / "fig.m").write_text(OBJC, encoding="utf-8")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None


def test_oversized_script_is_not_offered(client, tmp_path):
    big = R_SCRIPT + "# " + "x" * sourcelang.MAX_BYTES + "\n"
    (tmp_path / "fig.R").write_text(big, encoding="utf-8")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None


def test_non_utf8_script_is_not_offered(client, tmp_path):
    (tmp_path / "fig.R").write_bytes(b"x <- 1\n# \xff\xfe\n")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None


@pytest.mark.parametrize("name", ["fig.Rmd", "fig.qmd", "fig.mlx", "fig.rds", "fig.M"])
def test_out_of_scope_suffixes_are_not_siblings(client, tmp_path, name):
    (tmp_path / name).write_text(R_SCRIPT, encoding="utf-8")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None


def test_ambiguous_siblings_are_not_guessed(client, tmp_path):
    (tmp_path / "fig.R").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / "fig.m").write_text(M_SCRIPT, encoding="utf-8")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None


def test_python_connected_figure_does_not_carry_it(client, tmp_path):
    """已有 Python 候选的图说的是别的事：不带这个字段。"""
    (tmp_path / "fig.py").write_text(PY.format(stem="fig"), encoding="utf-8")
    (tmp_path / "fig.R").write_text(R_SCRIPT, encoding="utf-8")
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    panel = _panels(client)["fig.png"]
    assert panel["reason_code"] != "no_source_candidate"
    assert panel["foreign_source"] is None


def test_adding_the_script_later_refreshes_the_cached_report(client, tmp_path):
    _png(tmp_path / "fig.png")
    _open(client, tmp_path)
    assert _foreign(client, "fig.png") is None  # 先把报告算进缓存
    (tmp_path / "fig.R").write_text(R_SCRIPT, encoding="utf-8")
    assert _foreign(client, "fig.png") == {"script": "fig.R", "language": "r"}
    (tmp_path / "fig.R").unlink()
    assert _foreign(client, "fig.png") is None


def test_registry_lists_foreign_scripts_apart_from_python(client, tmp_path):
    (tmp_path / "a.R").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.m").write_text(M_SCRIPT, encoding="utf-8")
    (tmp_path / "objc.m").write_text(OBJC, encoding="utf-8")
    (tmp_path / "notes.Rmd").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "h.R").write_text(R_SCRIPT, encoding="utf-8")
    (tmp_path / "py_plot.py").write_text(PY.format(stem="p"), encoding="utf-8")
    _open(client, tmp_path)
    body = client.get("/api/registry").get_json()
    assert body["foreign_scripts"] == [
        {"script": "a.R", "language": "r"},
        {"script": "sub/b.m", "language": "matlab"},
    ]
    assert [s["script"] for s in body["all_scripts"]] == ["py_plot.py"]


def test_suffix_list_matches_sourcelang_extension_hint():
    """后缀名单是同源对：`discover.FOREIGN_SUFFIXES` 与 `sourcelang.extension_hint` 认的是同一批（区分大小写）。"""
    from tavotto.engine import discover

    for suffix in discover.FOREIGN_SUFFIXES:
        assert sourcelang.extension_hint(f"x{suffix}") in sourcelang.FOREIGN
    for suffix in (".M", ".Rmd", ".qmd", ".mlx", ".fig", ".rds", ".py"):
        assert suffix not in discover.FOREIGN_SUFFIXES
        assert sourcelang.extension_hint(f"x{suffix}") not in sourcelang.FOREIGN
