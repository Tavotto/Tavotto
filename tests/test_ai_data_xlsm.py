# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""`.xlsm`（带宏的工作簿）按 `.xlsx` 同一条路读（ADR 0178 修订 2026-11-28）。

判据的主语：**读到的表**（与同内容的 `.xlsx` 逐格相同）、**宏成员从不被打开**、以及所有按「是不是工作簿」分叉的地方（文件夹摘要按工作表列、
生成的脚本用 `read_xlsx`、能给出候选图）对两种后缀一视同仁。防护不因后缀放松：不是 zip 的 `.xlsm` 与 `.xlsx` 同一个码；`.xls` / `.xlsb` 仍拒绝。
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import zipfile

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_template as tpl,
    xlsxread,
)
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_profile import make_xlsx
from tests.test_ai_data_propose import project  # noqa: F401

ROWS = [["time", "signal", "group"], [0, 1.0, "a"], [1, 2.5, "a"], [2, 3.5, "b"], [3, 6.0, "b"]]
#: 宏成员：内容随便（真的 vbaProject.bin 是 OLE 复合文档）。读表不该碰它——下面用「读它就炸」来证
VBA = {"xl/vbaProject.bin": b"\xd0\xcf\x11\xe0" + b"not-a-real-macro" * 8}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")


def test_xlsm_is_a_workbook_suffix_and_the_other_old_formats_are_not():
    assert ".xlsm" in adf.XLSX_SUFFIXES and ".xlsm" in adf.TABLE_SUFFIXES
    assert ".xlsm" not in adf.LEGACY_EXCEL_SUFFIXES
    assert set(adf.LEGACY_EXCEL_SUFFIXES) == {".xls", ".xlsb"}


@pytest.mark.parametrize("name", ["m.xlsm", "UP.XLSM"])
def test_an_xlsm_reads_the_same_table_as_the_same_workbook_saved_as_xlsx(tmp_path, name):
    a = make_xlsx(tmp_path / "plain.xlsx", {"S": ROWS})
    b = make_xlsx(tmp_path / name, {"S": ROWS}, extra=VBA)
    sa = adf.inspect_data_source(a, "S", v2=True)
    sb = adf.inspect_data_source(b, "S", v2=True)
    assert sb.columns == sa.columns == ["time", "signal", "group"]
    assert sb.rows == sa.rows and len(sb.rows) == 4
    assert {k: v.kind for k, v in sb.per_column.items()} == {
        k: v.kind for k, v in sa.per_column.items()
    }


def test_the_macro_member_is_never_opened(tmp_path, monkeypatch):
    p = make_xlsx(tmp_path / "m.xlsm", {"S": ROWS}, extra=VBA)
    opened = []
    real = zipfile.ZipFile.open

    def spy(self, name, *a, **k):
        opened.append(name if isinstance(name, str) else name.filename)
        return real(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, name, *a, **k: spy(self, name).read())
    adf.inspect_data_source(p, "S", v2=True)
    adf.profile_folder(tmp_path)
    assert opened, "读表确实经过了这里"
    assert not [n for n in opened if "vba" in n.lower()], opened


def test_a_broken_xlsm_gets_the_same_code_as_a_broken_xlsx_and_old_formats_stay_refused(tmp_path):
    for name in ("x.xlsm", "x.xlsx"):
        (tmp_path / name).write_bytes(b"not a zip")
        with pytest.raises(adf.DataSourceError) as e:
            adf.inspect_data_source(tmp_path / name, None)
        assert e.value.code == "data_xlsx_invalid"
    for name in ("old.xls", "b.xlsb"):
        (tmp_path / name).write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(adf.DataSourceError) as e:
            adf.inspect_data_source(tmp_path / name, None)
        assert e.value.code == "data_excel_unsupported"


def test_a_folder_lists_an_xlsm_sheet_by_sheet_with_hidden_ones_marked(tmp_path):
    make_xlsx(
        tmp_path / "book.xlsm",
        [("Vis", ROWS), ("Hid", [["p", "q"], [1, 2], [3, 4]])],
        hidden=("Hid",),
        extra=VBA,
    )
    prof = adf.profile_folder(tmp_path)
    assert [(t.path, t.sheet, t.hidden) for t in prof.tables] == [
        ("book.xlsm", "Vis", False),
        ("book.xlsm", "Hid", True),
    ]
    assert not prof.skipped


def test_the_script_runtime_reads_an_xlsm_the_same_as_the_parent(tmp_path, monkeypatch):
    name, data = tpl.runtime_module()
    path = tmp_path / "rt" / name
    path.parent.mkdir()
    path.write_bytes(data)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, path.stem, mod)
    spec.loader.exec_module(mod)
    p = make_xlsx(tmp_path / "m.xlsm", {"S": ROWS}, extra=VBA)
    parent = adf.inspect_data_source(p, "S", v2=True)
    header, rows = mod.read_xlsx(p, "S")
    assert header == parent.columns and rows == [list(r) for r in parent.rows]


def test_the_generated_script_for_an_xlsm_uses_the_workbook_reader_and_draws(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    make_xlsx(folder / "m.xlsm", {"S": ROWS}, extra=VBA)
    (entry,) = adf.profile_folder(folder).tables
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "m.xlsm",
            "family": "trend",
            "layers": [{"mark": "line"}],
            "y": ["signal"],
            "why": "说明",
            "x": "time",
            "sheet": "S",
        }
    )
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="m.xlsm", stem="m")
    assert "read_xlsx(DATA, SHEET)" in rendered.script
    assert "read_delimited" not in rendered.script
    work = tmp_path / "work"
    work.mkdir()
    (work / "m.xlsm").write_bytes((folder / "m.xlsm").read_bytes())
    (work / "m.py").write_text(rendered.script, encoding="utf-8")
    (work / rendered.runtime_name).write_bytes(rendered.runtime)
    out = subprocess.run(
        [sys.executable, "m.py"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"MPLBACKEND": "Agg", "PATH": "", "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert (work / "m.pdf").exists()


def test_propose_offers_candidates_for_an_xlsm(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    make_xlsx(root / "data" / "m.xlsm", {"S": ROWS}, extra=VBA)
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp


def test_the_reader_module_no_longer_lists_xlsm_as_legacy():
    assert xlsxread.LEGACY_SUFFIXES == (".xls", ".xlsb")
