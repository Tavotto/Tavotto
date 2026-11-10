"""TAF 数据出图 v2 的「读表 + 整文件夹摘要」层：TSV、XLSX（标准库现场拼最小 xlsx，不提交二进制、不依赖 openpyxl）、
结构线索（layout / groups / id_like / repeats）与 `profile_folder`。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, xlsxread

SRC = Path(__file__).resolve().parent.parent / "src"

# --------------------------------------------------------------------------- 最小 xlsx 拼装
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class C:
    """一个格子：`kind` = s（共享字符串）/ i（内联）/ n / b / str（公式字符串）/ e / f（公式带缓存数值）/ f0（公式无缓存）。"""

    def __init__(self, kind: str, value=None, style: int = 0, ref: str | None = None):
        self.kind, self.value, self.style, self.ref = kind, value, style, ref


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sheet_xml(rows: list[list], sst: list[str]) -> str:
    """`rows` 的每格：None（空）/ str（共享字符串）/ int|float（数值）/ bool / `C`。"""
    out = [f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_NS}"><sheetData>']
    for ri, row in enumerate(rows, start=1):
        out.append(f'<row r="{ri}">')
        for ci, v in enumerate(row):
            if v is None:
                continue
            c = v if isinstance(v, C) else None
            if c is None:
                if isinstance(v, bool):
                    c = C("b", v)
                elif isinstance(v, str):
                    c = C("s", v)
                else:
                    c = C("n", v)
            ref = c.ref or f"{_col(ci)}{ri}"
            st = f' s="{c.style}"' if c.style else ""
            if c.kind == "s":
                if c.value not in sst:
                    sst.append(c.value)
                out.append(f'<c r="{ref}"{st} t="s"><v>{sst.index(c.value)}</v></c>')
            elif c.kind == "i":
                out.append(f'<c r="{ref}"{st} t="inlineStr"><is><t>{_esc(c.value)}</t></is></c>')
            elif c.kind == "n":
                out.append(f'<c r="{ref}"{st}><v>{c.value}</v></c>')
            elif c.kind == "b":
                out.append(f'<c r="{ref}"{st} t="b"><v>{int(c.value)}</v></c>')
            elif c.kind == "str":
                out.append(f'<c r="{ref}"{st} t="str"><f>A1&amp;"x"</f><v>{_esc(c.value)}</v></c>')
            elif c.kind == "e":
                out.append(f'<c r="{ref}"{st} t="e"><v>{c.value}</v></c>')
            elif c.kind == "f":
                out.append(f'<c r="{ref}"{st}><f>SUM(A1:A2)</f><v>{c.value}</v></c>')
            elif c.kind == "f0":
                out.append(f'<c r="{ref}"{st}><f>SUM(A1:A2)</f></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


STYLES = (
    f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{_NS}">'
    '<numFmts count="3"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/>'
    '<numFmt numFmtId="165" formatCode="0.00"/><numFmt numFmtId="166" formatCode="[h]:mm:ss"/></numFmts>'
    '<cellXfs count="5"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="165"/>'
    '<xf numFmtId="166"/></cellXfs></styleSheet>'
)
S_DATE14, S_DATE_CUSTOM, S_NUM, S_DURATION = 1, 2, 3, 4


def make_xlsx(
    path: Path,
    sheets: dict[str, list[list]] | list[tuple[str, list[list]]],
    *,
    hidden: tuple[str, ...] = (),
    date1904: bool = False,
    extra: dict[str, bytes] | None = None,
    workbook_override: str | None = None,
    sheet_xml_override: dict[str, str] | None = None,
) -> Path:
    items = list(sheets.items()) if isinstance(sheets, dict) else list(sheets)
    sst: list[str] = []
    sheet_files = {}
    for i, (name, rows) in enumerate(items, start=1):
        sheet_files[i] = (name, (sheet_xml_override or {}).get(name) or sheet_xml(rows, sst))
    wb = [f'<?xml version="1.0"?><workbook xmlns="{_NS}" xmlns:r="{_RNS}">']
    wb.append(f'<workbookPr date1904="{int(date1904)}"/><sheets>')
    rel = [
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    ]
    for i, (name, _) in sheet_files.items():
        st = ' state="hidden"' if name in hidden else ""
        wb.append(f'<sheet name="{_esc(name)}" sheetId="{i}"{st} r:id="rId{i}"/>')
        rel.append(
            f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
        )
    wb.append("</sheets></workbook>")
    rel.append("</Relationships>")
    sst_xml = (
        f'<?xml version="1.0"?><sst xmlns="{_NS}">'
        + "".join(f"<si><t>{_esc(s)}</t></si>" for s in sst)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", workbook_override or "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rel))
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/sharedStrings.xml", sst_xml)
        for i, (_, xml) in sheet_files.items():
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


def values(tmp_path, rows, **kw):
    p = make_xlsx(tmp_path / "t.xlsx", {"S": rows}, **kw)
    return xlsxread.read_xlsx_values(p, "S")


# --------------------------------------------------------------------------- TSV
def test_tsv_and_tab_share_the_csv_pipeline(tmp_path):
    csv_text = "t,v\n1,2.5\n2,3.5\n"
    tsv_text = "t\tv\n1\t2.5\n2\t3.5\n"
    (tmp_path / "a.csv").write_text(csv_text)
    (tmp_path / "a.tsv").write_text(tsv_text)
    (tmp_path / "a.tab").write_text(tsv_text)
    base = adf.inspect_data_source(tmp_path / "a.csv")
    for n in ("a.tsv", "a.tab"):
        s = adf.inspect_data_source(tmp_path / n)
        assert s.columns == base.columns and s.rows == base.rows
        assert s.per_column["v"].to_dict() == base.per_column["v"].to_dict()


def test_tsv_does_not_split_on_commas_and_uppercase_suffix(tmp_path):
    (tmp_path / "A.TSV").write_text("name\tv\na,b\t1\n")
    s = adf.inspect_data_source(tmp_path / "A.TSV")
    assert s.rows == [["a,b", "1"]]


def test_tsv_uses_the_same_limits_and_codes(tmp_path):
    (tmp_path / "w.tsv").write_text(
        "\t".join(f"c{i}" for i in range(adf.MAX_COLUMNS + 1)) + "\n1\n"
    )
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(tmp_path / "w.tsv")
    assert e.value.code == "data_too_wide"
    (tmp_path / "r.tsv").write_text("a\tb\n1\n")
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(tmp_path / "r.tsv")
    assert e.value.code == "data_ragged"
    (tmp_path / "big.tab").write_bytes(b"a\tb\n" + b"1\t2\n" * (adf.MAX_BYTES // 4 + 10))
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(tmp_path / "big.tab")
    assert e.value.code == "data_too_large"


def test_v1_csv_behaviour_unchanged_for_unknown_suffix(tmp_path):
    (tmp_path / "x.txt").write_text("a,b\n1,2\n")
    assert adf.inspect_data_source(tmp_path / "x.txt").columns == ["a", "b"]


def test_limits_are_one_number_in_both_modules():
    assert (xlsxread.MAX_BYTES, xlsxread.MAX_ROWS) == (adf.MAX_BYTES, adf.MAX_ROWS)


# --------------------------------------------------------------------------- XLSX 取值
def test_cell_kinds(tmp_path):
    rows = [
        ["name", "num", "flag", "f", "fs", "err", "inl", "nocache"],
        [
            "a&<b>",
            0.1,
            True,
            C("f", "7"),
            C("str", "hello"),
            C("e", "#N/A"),
            C("i", "in line"),
            C("f0"),
        ],
        ["b", 3, False, None, None, None, None, None],
    ]
    t = values(tmp_path, rows)
    assert t[0] == rows[0]
    assert t[1] == ["a&<b>", "0.1", "TRUE", "7", "hello", "#N/A", "in line", ""]
    assert t[2] == ["b", "3", "FALSE", "", "", "", "", ""]


def test_rich_text_shared_string_joins_runs_and_skips_phonetic(tmp_path):
    p = make_xlsx(tmp_path / "r.xlsx", {"S": [["h"], ["x"]]})
    sst = (
        f'<?xml version="1.0"?><sst xmlns="{_NS}"><si><t>h</t></si>'
        "<si><r><t>ab</t></r><r><t>cd</t></r><rPh><t>PHONETIC</t></rPh></si></sst>"
    )
    rewrite(p, "xl/sharedStrings.xml", sst)
    # 第二行第一格改成引用 si #1
    xml = sheet_xml([["h"], ["x"]], ["h", "x"]).replace("<v>1</v>", "<v>1</v>")
    rewrite(p, "xl/worksheets/sheet1.xml", xml)
    assert xlsxread.read_xlsx_values(p, "S") == [["h"], ["abcd"]]


def rewrite(path: Path, member: str, text: str) -> None:
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items[member] = text.encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)


@pytest.mark.parametrize(
    "serial,style,want",
    [
        (44927, S_DATE14, "2023-01-01"),
        (44927, S_DATE_CUSTOM, "2023-01-01"),
        (1, S_DATE14, "1900-01-01"),
        (59, S_DATE14, "1900-02-28"),
        (60, S_DATE14, "60"),  # Excel 的假 1900-02-29：不存在，保持原数
        (61, S_DATE14, "1900-03-01"),
        (36526, S_DATE14, "2000-01-01"),
        (44927.5, S_DATE14, "2023-01-01T12:00:00"),
        (0, S_DATE14, "0"),
        (-5, S_DATE14, "-5"),
        (44927, S_NUM, "44927"),  # 非日期格式的数不转
        (44927, 0, "44927"),
        (44927, S_DURATION, "44927"),  # `[h]:mm:ss` 时长不是日期
    ],
)
def test_dates_1900_epoch(tmp_path, serial, style, want):
    t = values(tmp_path, [["d"], [C("n", serial, style)]])
    assert t[1] == [want]


@pytest.mark.parametrize(
    "serial,want",
    [
        (43465, "2023-01-01"),
        (0, "0"),
        (1, "1904-01-02"),
        (1462, "1908-01-02"),
        (34064, "1997-04-06"),
    ],
)
def test_dates_1904_epoch(tmp_path, serial, want):
    t = values(tmp_path, [["d"], [C("n", serial, S_DATE14)]], date1904=True)
    assert t[1] == [want]


def test_1904_serial_zero_is_left_alone_but_1904_epoch_differs_from_1900(tmp_path):
    same = [["d"], [C("n", 43465, S_DATE14)]]
    assert values(tmp_path, same, date1904=True)[1] == ["2023-01-01"]
    assert values(tmp_path, same, date1904=False)[1] != ["2023-01-01"]


def test_xlsx_date_column_is_a_date_column_in_the_summary(tmp_path):
    p = make_xlsx(
        tmp_path / "d.xlsx",
        {"S": [["day", "v"], [C("n", 44927, S_DATE14), 1], [C("n", 44928, S_DATE14), 2]]},
    )
    s = adf.inspect_data_source(p)
    assert s.per_column["day"].kind == "date" and s.rows[0][0] == "2023-01-01"
    m = adf.plan_mapping(s, "v 随 day 的变化")
    assert isinstance(m, adf.Mapping) and m.x_is_date


def test_sparse_cells_and_blank_rows(tmp_path):
    rows = [
        ["a", None, "c"],
        [None, None, None],
        [1, None, 3],
        [None, C("n", 2, ref="B4"), None],
    ]
    # 行 2 全空被丢；行 4 的 B4 单格；每行补齐到最右非空格的宽度
    t = values(tmp_path, rows)
    assert t == [["a", "", "c"], ["1", "", "3"], ["", "2", ""]]


def test_cells_without_refs_are_read_in_order(tmp_path):
    xml = (
        f'<worksheet xmlns="{_NS}"><sheetData><row><c t="inlineStr"><is><t>a</t></is></c>'
        '<c t="inlineStr"><is><t>b</t></is></c></row><row><c><v>1</v></c><c><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )
    p = make_xlsx(tmp_path / "n.xlsx", {"S": []}, sheet_xml_override={"S": xml})
    assert xlsxread.read_xlsx_values(p, "S") == [["a", "b"], ["1", "2"]]


def test_sheet_selection_by_name_index_default_and_hidden(tmp_path):
    p = make_xlsx(
        tmp_path / "m.xlsx",
        [("H", [["h"], [1]]), ("V", [["v"], [2]]), ("W", [["w"], [3]])],
        hidden=("H",),
    )
    sheets = xlsxread.list_xlsx_sheets(p)
    assert [(s.name, s.index, s.hidden) for s in sheets] == [
        ("H", 0, True),
        ("V", 1, False),
        ("W", 2, False),
    ]
    assert xlsxread.read_xlsx_values(p)[0] == ["v"], "默认是第一张可见表"
    assert xlsxread.read_xlsx_values(p, "H")[0] == ["h"], "隐藏表也能按名取"
    assert xlsxread.read_xlsx_values(p, 2)[0] == ["w"]
    assert adf.inspect_data_source(p, "W").sheet == "W"
    assert adf.inspect_data_source(p).sheet == "V"
    for bad in ("nope", 9, -1, True):
        with pytest.raises(xlsxread.XlsxError) as e:
            xlsxread.read_xlsx_values(p, bad)
        assert e.value.code == "data_sheet_not_found"


def test_all_hidden_falls_back_to_first_sheet(tmp_path):
    p = make_xlsx(
        tmp_path / "h.xlsx", [("A", [["a"], [1]]), ("B", [["b"], [2]])], hidden=("A", "B")
    )
    assert xlsxread.read_xlsx_values(p)[0] == ["a"]


def test_chartsheet_is_not_listed(tmp_path):
    p = make_xlsx(tmp_path / "c.xlsx", {"S": [["a"], [1]]})
    wb = (
        f'<workbook xmlns="{_NS}" xmlns:r="{_RNS}"><sheets><sheet name="S" sheetId="1" r:id="rId1"/>'
        '<sheet name="Chart" sheetId="2" r:id="rId9"/></sheets></workbook>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId9" Type="{_REL}/chartsheet" Target="chartsheets/sheet1.xml"/></Relationships>'
    )
    rewrite(p, "xl/workbook.xml", wb)
    rewrite(p, "xl/_rels/workbook.xml.rels", rels)
    assert [s.name for s in xlsxread.list_xlsx_sheets(p)] == ["S"]


def test_merged_cells_and_multirow_header_are_read_as_laid_out(tmp_path):
    # 合并表头：只有左上角有值 → 右边的格子是空表头 → 与 CSV 同一条 `data_header_invalid`，不猜
    p = make_xlsx(tmp_path / "mg.xlsx", {"S": [["A", None], ["x", "y"], [1, 2]]})
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)
    assert e.value.code == "data_header_invalid"
    # 两行表头（第二行是单位）：表头合法，但第二行把数值列变成混合列 → layout unknown
    p = make_xlsx(tmp_path / "mh.xlsx", {"S": [["w", "h"], ["kg", "cm"], [1, 2], [3, 4]]})
    s = adf.inspect_data_source(p)
    assert s.layout == "unknown" and s.per_column["w"].kind == "mixed"


def test_xlsx_and_csv_give_the_same_summary(tmp_path):
    (tmp_path / "a.csv").write_text("time_s,temp_C,note\n0,20.5,a\n1,,b\n2,21.0,a\n3,nan,c\n")
    p = make_xlsx(
        tmp_path / "a.xlsx",
        {
            "S": [
                ["time_s", "temp_C", "note"],
                [0, 20.5, "a"],
                [1, None, "b"],
                [2, 21.0, "a"],
                [3, C("i", "nan"), "c"],
            ]
        },
    )
    a, b = adf.inspect_data_source(tmp_path / "a.csv"), adf.inspect_data_source(p)
    assert a.columns == b.columns
    assert {k: v.to_dict() for k, v in a.per_column.items()} == {
        k: v.to_dict() for k, v in b.per_column.items()
    }
    assert adf.plan_mapping(a, "") == adf.plan_mapping(b, "")
    assert b.sha256 == hashlib.sha256(p.read_bytes()).hexdigest() and b.size == p.stat().st_size


def test_xlsx_row_and_size_limits_reuse_codes(tmp_path, monkeypatch):
    p = make_xlsx(tmp_path / "big.xlsx", {"S": [["a"]] + [[i] for i in range(10)]})
    monkeypatch.setattr(xlsxread, "MAX_ROWS", 5)
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)
    assert e.value.code == "data_too_long"
    monkeypatch.setattr(xlsxread, "MAX_ROWS", 100_000)
    monkeypatch.setattr(xlsxread, "MAX_BYTES", 100)
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(p)
    assert e.value.code == "data_too_large"
    wide = make_xlsx(
        tmp_path / "w.xlsx", {"S": [[f"c{i}" for i in range(adf.MAX_COLUMNS + 1)], list(range(65))]}
    )
    monkeypatch.setattr(xlsxread, "MAX_BYTES", 5 * 1024 * 1024)
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(wide)
    assert e.value.code == "data_too_wide"


# --------------------------------------------------------------------------- XLSX 防护
def _code(path, sheet=None):
    with pytest.raises(adf.DataSourceError) as e:
        adf.inspect_data_source(path, sheet)
    return e.value.code


@pytest.mark.parametrize("name", ["old.xls", "m.xlsm", "b.xlsb", "UP.XLS"])
def test_legacy_excel_is_refused_with_a_stable_code(tmp_path, name):
    (tmp_path / name).write_bytes(b"\xd0\xcf\x11\xe0")
    assert _code(tmp_path / name) == "data_excel_unsupported"


def test_not_a_zip_or_missing_parts(tmp_path):
    (tmp_path / "x.xlsx").write_bytes(b"not a zip")
    assert _code(tmp_path / "x.xlsx") == "data_xlsx_invalid"
    with zipfile.ZipFile(tmp_path / "e.xlsx", "w") as z:
        z.writestr("hello.txt", "x")
    assert _code(tmp_path / "e.xlsx") == "data_xlsx_invalid"
    assert _code(tmp_path / "gone.xlsx") == "data_unreadable"


def test_broken_xml(tmp_path):
    p = make_xlsx(
        tmp_path / "b.xlsx", {"S": [["a"], [1]]}, sheet_xml_override={"S": "<worksheet><oops"}
    )
    assert _code(p) == "data_xlsx_invalid"


_BILLION = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'


def test_doctype_and_entities_are_refused_everywhere(tmp_path):
    sheet = (
        _BILLION
        + f'<worksheet xmlns="{_NS}"><sheetData><row><c t="inlineStr"><is><t>&lol2;</t></is></c></row></sheetData></worksheet>'
    )
    p = make_xlsx(tmp_path / "s.xlsx", {"S": []}, sheet_xml_override={"S": sheet})
    assert _code(p) == "data_xlsx_unsafe"
    # 外部实体同理（任何 DOCTYPE 都拒绝，不区分内外部）
    ext = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    for member in ("xl/workbook.xml", "xl/sharedStrings.xml", "xl/styles.xml"):
        q = make_xlsx(tmp_path / "q.xlsx", {"S": [["a"], [1]]})
        rewrite(q, member, ext)
        assert _code(q) == "data_xlsx_unsafe", member
    # 小写 / 空格写法
    q = make_xlsx(tmp_path / "q2.xlsx", {"S": [["a"], [1]]})
    rewrite(
        q, "xl/sharedStrings.xml", '<?xml version="1.0"?><!doctype sst [<!entity a "b">]><sst/>'
    )
    assert _code(q) == "data_xlsx_unsafe"


def test_utf16_xml_cannot_hide_a_doctype(tmp_path):
    p = make_xlsx(tmp_path / "u.xlsx", {"S": [["a"], [1]]})
    body = (_BILLION + f'<worksheet xmlns="{_NS}"/>').encode("utf-16")
    with zipfile.ZipFile(p) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["xl/worksheets/sheet1.xml"] = body
    with zipfile.ZipFile(p, "w") as z:
        for n, b in items.items():
            z.writestr(n, b)
    assert _code(p) == "data_xlsx_unsafe"
    # 声明成别的编码也拒绝
    rewrite(p, "xl/worksheets/sheet1.xml", '<?xml version="1.0" encoding="UTF-16"?><worksheet/>')
    assert _code(p) == "data_xlsx_unsafe"


def test_zip_bomb_by_declared_size(tmp_path):
    p = make_xlsx(tmp_path / "bomb.xlsx", {"S": [["a"], [1]]})
    with zipfile.ZipFile(p, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/media/pad.bin", b"\0" * (xlsxread.MAX_UNZIPPED + 1024))
    assert p.stat().st_size < adf.MAX_BYTES, "压缩后很小才是炸弹"
    assert _code(p) == "data_xlsx_too_large"


def test_too_many_members(tmp_path):
    p = make_xlsx(tmp_path / "many.xlsx", {"S": [["a"], [1]]})
    with zipfile.ZipFile(p, "a") as z:
        for i in range(xlsxread.MAX_MEMBERS + 1):
            z.writestr(f"xl/m/{i}.txt", "")
    assert _code(p) == "data_xlsx_too_large"


def test_duplicate_members_are_refused(tmp_path):
    p = make_xlsx(tmp_path / "dup.xlsx", {"S": [["a"], [1]]})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    assert _code(p) == "data_xlsx_invalid"


def test_encrypted_members_are_refused(tmp_path):
    p = tmp_path / "enc.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        zi = zipfile.ZipInfo("xl/workbook.xml")
        zi.flag_bits |= 0x1
        z.writestr(zi, b"x")
    assert _code(p) == "data_xlsx_invalid"


def test_sheet_target_cannot_escape_the_package(tmp_path):
    p = make_xlsx(tmp_path / "esc.xlsx", {"S": [["a"], [1]]})
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="../../etc/passwd"/></Relationships>'
    )
    rewrite(p, "xl/_rels/workbook.xml.rels", rels)
    assert _code(p) == "data_xlsx_invalid"


def test_xlsx_reading_never_writes_anything(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    make_xlsx(d / "a.xlsx", {"S": [["a", "b"], [1, 2]]})
    before = sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d.rglob("*"))
    adf.inspect_data_source(d / "a.xlsx")
    adf.profile_folder(d)
    assert before == sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d.rglob("*"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ro"]


def test_parent_side_stays_standard_library():
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import tempfile, pathlib\n"
        "from tavotto.engine import ai_data_figure as adf, xlsxread\n"
        "d = pathlib.Path(tempfile.mkdtemp()); (d/'a.csv').write_text('a,b\\n1,2\\n')\n"
        "adf.profile_folder(d)\n"
        "bad = [m for m in ('openpyxl','pandas','numpy','matplotlib','lxml','defusedxml') if m in sys.modules]\n"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr


# --------------------------------------------------------------------------- 结构线索
def _csv(tmp_path, text, name="t.csv"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return adf.inspect_data_source(tmp_path / name)


def test_long_layout_with_groups(tmp_path):
    s = _csv(tmp_path, "time,group,value\n0,A,1\n1,A,2\n0,B,3\n1,B,4\n")
    assert s.layout == "long" and s.groups == {"group": 2}


def test_wide_layout(tmp_path):
    s = _csv(tmp_path, "time,ctrl,treat\n0,1,2\n1,2,3\n2,3,4\n")
    assert s.layout == "wide" and s.groups == {}
    s = _csv(tmp_path, "gene,ctrl,treat\ng1,1,2\ng2,2,3\n", "l.csv")
    assert s.layout == "wide", "第一列是互不相同的文字标签也算"


def test_matrix_layout_is_only_the_symmetric_case(tmp_path):
    s = _csv(tmp_path, "gene,a,b,c\na,1,0.2,0.3\nb,0.2,1,0.4\nc,0.3,0.4,1\n")
    assert s.layout == "matrix"
    s = _csv(tmp_path, "gene,ctrl,treat\ng1,1,2\ng2,2,3\n", "w.csv")
    assert s.layout != "matrix"


@pytest.mark.parametrize(
    "text",
    [
        "x,y\n1,2\n2,3\n",  # 单个 y 的两列表
        "group,w0,w1,w2\nA,1,2,3\nA,2,3,4\nB,3,4,5\nB,4,5,6\n",  # 分组 + 多个数值列
        "time,ctrl,note\n0,1,x\n1,2,y\n2,3,z\n",  # 带备注文字列
        "a,b\n1,2\nx,3\n",  # 混合列
    ],
)
def test_ambiguous_tables_stay_unknown(tmp_path, text):
    assert _csv(tmp_path, text).layout == "unknown"


def test_group_columns_need_2_to_12_levels_with_repeats(tmp_path):
    rows13 = "".join(f"g{i % 13},{i}\n" for i in range(26))
    assert _csv(tmp_path, "grp,v\n" + rows13).groups == {}, "13 个取值不是分组"
    rows12 = "".join(f"g{i % 12},{i}\n" for i in range(24))
    assert _csv(tmp_path, "grp,v\n" + rows12, "b.csv").groups == {"grp": 12}
    assert _csv(tmp_path, "grp,v\nA,1\nB,2\nC,3\n", "c.csv").groups == {}, "取值全不同 = 标签"
    assert _csv(tmp_path, "grp,v\nA,1\nA,2\nA,3\n", "d.csv").groups == {}, "只有一个取值"
    assert _csv(tmp_path, "grp,v\n1,1\n1,2\n2,3\n2,4\n", "e.csv").groups == {}, "数字编码的分组不猜"
    # 日期列不是分组
    assert (
        _csv(
            tmp_path, "d,v\n2020-01-01,1\n2020-01-01,2\n2020-01-02,3\n2020-01-02,4\n", "f.csv"
        ).groups
        == {}
    )


@pytest.mark.parametrize(
    "name,ids",
    [
        ("subject", True),
        ("Subject_ID", True),
        ("SubjectID", True),
        ("patient_id", True),
        ("sample_no", True),
        ("animal", True),
        ("编号", True),
        ("小鼠编号", True),
        ("id", True),
        ("sample_mean", False),
        ("valid", False),
        ("rating", False),
        ("grid", False),
        ("value", False),
    ],
)
def test_id_like_by_column_name(tmp_path, name, ids):
    s = _csv(tmp_path, f"{name},v\ns1,1\ns2,2\ns1,3\n")
    assert (name in s.id_like) is ids


def test_id_like_value_conditions_and_repeats(tmp_path):
    s = _csv(tmp_path, "subject,time,v\ns1,0,1\ns1,1,2\ns2,0,3\ns2,1,4\n")
    assert s.id_like == ["subject"] and s.repeats is True
    s = _csv(tmp_path, "subject,v\ns1,1\ns2,2\ns3,3\n", "u.csv")
    assert s.id_like == ["subject"] and s.repeats is False
    s = _csv(tmp_path, "time,v\n0,1\n1,2\n", "n.csv")
    assert s.id_like == [] and s.repeats is None
    s = _csv(tmp_path, "id,v\n1,1\n2,2\n2,3\n", "i.csv")
    assert s.id_like == ["id"] and s.repeats is True, "整数编号"
    s = _csv(tmp_path, "id,v\n1.5,1\n2.5,2\n", "f.csv")
    assert s.id_like == [], "有小数的数不当编号"
    s = _csv(tmp_path, "id,v\n1,1\n1,2\n", "o.csv")
    assert s.id_like == [], "只有一种取值不算"


def test_summary_dict_keeps_old_keys_and_adds_new_without_cell_values(tmp_path):
    s = _csv(
        tmp_path,
        "subject,group,value\nsecret_s1,A,0.4242\nsecret_s1,B,2\nsecret_s2,A,3\nsecret_s2,B,4\n",
    )
    d = s.to_dict()
    assert {"file", "sha256", "size", "columns", "rows", "per_column"} <= set(d)
    assert {"sheet", "layout", "groups", "id_like", "repeats"} <= set(d)
    assert d["rows"] == 4 and d["groups"] == {"subject": 2, "group": 2}
    # per_column.text 本来就带文字样本（v1 行为，给人 / 模型看）；新字段不新增任何单元格值
    for k in ("sheet", "layout", "groups", "id_like", "repeats"):
        assert "secret_s1" not in json.dumps(d[k]) and "0.4242" not in json.dumps(d[k])


# --------------------------------------------------------------------------- 整文件夹
def _tree(root: Path) -> Path:
    (root / "sub").mkdir(parents=True)
    (root / "a.csv").write_text("t,v\n1,2\n2,3\n")
    (root / "sub" / "b.TSV").write_text("t\tv\n1\t2\n")
    make_xlsx(
        root / "book.xlsx",
        [
            ("Vis", [["x", "y"], [1, 2]]),
            ("Hid", [["p", "q"], [3, 4]]),
            ("Bad", [["a", None], [1, 2]]),
        ],
        hidden=("Hid",),
    )
    (root / "old.xls").write_bytes(b"x")
    (root / "m.xlsm").write_bytes(b"x")
    (root / "~$book.xlsx").write_bytes(b"lock")
    (root / ".hidden.csv").write_text("a\n1\n")
    (root / "ragged.csv").write_text("a,b\n1\n")
    (root / "broken.xlsx").write_bytes(b"nope")
    (root / "empty.csv").write_text("")
    for d in ("node_modules", ".git", "__pycache__", ".hiddendir"):
        (root / d).mkdir()
        (root / d / "z.csv").write_text("a\n1\n")
    return root


def test_profile_folder_lists_tables_skips_and_relative_posix_paths(tmp_path):
    root = _tree(tmp_path / "data")
    prof = adf.profile_folder(root)
    got = [(t.path, t.sheet, t.hidden) for t in prof.tables]
    assert got == [
        ("a.csv", None, False),
        ("book.xlsx", "Vis", False),
        ("book.xlsx", "Hid", True),
        ("sub/b.TSV", None, False),
    ]
    assert [(s.path, s.sheet, s.code) for s in prof.skipped] == [
        ("book.xlsx", "Bad", "data_header_invalid"),
        ("broken.xlsx", None, "data_xlsx_invalid"),
        ("empty.csv", None, "data_empty"),
        ("m.xlsm", None, "data_excel_unsupported"),
        ("old.xls", None, "data_excel_unsupported"),
        ("ragged.csv", None, "data_ragged"),
    ]
    assert not prof.truncated and prof.truncated_reason is None
    for t in prof.tables:
        assert not os.path.isabs(t.path) and "\\" not in t.path
        assert t.summary.rows == [] and t.summary.row_count is not None
    assert prof.tables[0].summary.to_dict()["rows"] == 2


def test_profile_folder_does_not_follow_links(tmp_path):
    root = _tree(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.csv").write_text("a\n1\n")
    (outside / "o.csv").write_text("a\n1\n")
    try:
        (root / "linkdir").symlink_to(outside, target_is_directory=True)
        (root / "link.csv").symlink_to(outside / "o.csv")
    except OSError:
        pytest.skip("no symlink support")
    paths = {t.path for t in adf.profile_folder(root).tables}
    assert not any("link" in p or "leak" in p or p == "o.csv" for p in paths)
    assert not any("link" in s.path for s in adf.profile_folder(root).skipped)


def test_profile_folder_truncates_loudly_at_20_tables(tmp_path):
    for i in range(25):
        (tmp_path / f"f{i:02d}.csv").write_text("a,b\n1,2\n")
    prof = adf.profile_folder(tmp_path)
    assert len(prof.tables) == adf.MAX_PROFILE_TABLES == 20
    assert prof.truncated and prof.truncated_reason == "tables"
    assert [t.path for t in prof.tables] == [f"f{i:02d}.csv" for i in range(20)]
    # 恰好 20 张不算截断
    for i in range(20, 25):
        (tmp_path / f"f{i:02d}.csv").unlink()
    assert not adf.profile_folder(tmp_path).truncated


def test_profile_folder_counts_each_sheet_toward_the_cap(tmp_path):
    make_xlsx(tmp_path / "w.xlsx", [(f"S{i}", [["a"], [i]]) for i in range(25)])
    prof = adf.profile_folder(tmp_path)
    assert len(prof.tables) == 20 and prof.truncated and prof.truncated_reason == "tables"
    assert prof.tables[-1].sheet == "S19"


def test_profile_folder_truncates_on_skipped_and_scan_budget(tmp_path, monkeypatch):
    for i in range(adf.MAX_PROFILE_SKIPPED + 5):
        (tmp_path / f"e{i:03d}.csv").write_text("")
    prof = adf.profile_folder(tmp_path)
    assert len(prof.skipped) == adf.MAX_PROFILE_SKIPPED
    assert prof.truncated and prof.truncated_reason == "skipped"
    d2 = tmp_path / "scan"
    d2.mkdir()
    for i in range(10):
        (d2 / f"f{i}.csv").write_text("a\n1\n")
    monkeypatch.setattr(adf, "PROFILE_SCAN_MAX", 3)
    prof = adf.profile_folder(d2)
    assert prof.truncated and prof.truncated_reason == "scan" and len(prof.tables) < 10


def test_profile_folder_one_bad_file_does_not_fail_the_folder(tmp_path, monkeypatch):
    (tmp_path / "a.csv").write_text("a,b\n1,2\n")
    (tmp_path / "b.csv").write_text("a,b\n1,2\n")
    real = adf._summarize

    def boom(table, **kw):
        if kw["path_name"] == "a.csv":
            raise RuntimeError("unexpected")
        return real(table, **kw)

    monkeypatch.setattr(adf, "_summarize", boom)
    prof = adf.profile_folder(tmp_path)
    assert [t.path for t in prof.tables] == ["b.csv"]
    assert [(s.path, s.code) for s in prof.skipped] == [("a.csv", "data_unreadable")]


def test_profile_folder_root_must_be_a_directory(tmp_path):
    (tmp_path / "f.csv").write_text("a\n1\n")
    for bad in (tmp_path / "nope", tmp_path / "f.csv"):
        with pytest.raises(adf.DataSourceError) as e:
            adf.profile_folder(bad)
        assert e.value.code == "data_not_found"


def test_profile_to_dict_is_json_and_carries_no_row_values(tmp_path):
    (tmp_path / "a.csv").write_text("subject,group,value\ns1,A,0.4242\ns1,B,2\ns2,A,3\ns2,B,4\n")
    d = adf.profile_folder(tmp_path).to_dict()
    text = json.dumps(d)
    assert "0.4242" not in text
    t = d["tables"][0]
    assert (
        t["path"] == "a.csv" and t["layout"] == "long" and t["rows"] == 4 and t["repeats"] is True
    )
    assert set(d) == {"tables", "skipped", "truncated", "truncated_reason"}


def test_all_new_codes_are_registered():
    for c in xlsxread.ERROR_CODES + ("data_format_unsupported",):
        assert c in adf.ERROR_CODES
        adf.DataSourceError(c)
