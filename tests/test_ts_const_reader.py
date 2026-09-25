"""读 TS 闭集的那把尺子自己得是对的（评审 #300-5）。

`tests/support/tsconst.py` 现在是三道同源门禁共同的读法
（遥测披露 / legend 模型 / 画布字体族）。它**自己就是判据的一部分**——尺子
量不准的话，三道门禁一起变成空门禁，而且是那种「跑了、绿了、什么都没量」的
空门禁。所以这里用手捏的源码把它钉住：注释里的、字符串里的、没导出的、
有两份的，一条都不许混进答案。
"""

from __future__ import annotations

import pytest

from tests.support.tsconst import (
    blank_comments_and_strings,
    exported_number,
    exported_string,
    exported_string_array,
)

LIVE = "export const E = ['a', 'b', 'c'] as const\n"


def test_reads_the_live_declaration():
    assert exported_string_array(LIVE, "E") == ["a", "b", "c"]


def test_a_commented_out_old_array_above_it_is_not_the_answer():
    """真实的翻车形状：改过活声明，把旧的注释掉留在上面。

    `re.search` 取第一处匹配，读到的就是那段注释——界面已经与后端漂开了，
    门禁却照样绿。
    """
    src = "// export const E = ['old1', 'old2'] as const\n" + LIVE
    assert exported_string_array(src, "E") == ["a", "b", "c"]


def test_a_block_comment_holding_a_whole_declaration_is_not_the_answer():
    src = "/*\nexport const E = ['old1'] as const\n*/\n" + LIVE
    assert exported_string_array(src, "E") == ["a", "b", "c"]


def test_a_doc_comment_inside_the_array_does_not_add_an_entry():
    src = "export const E = [\n  'a',\n  // 'ghost' 这条已经删了\n  'b',\n] as const\n"
    assert exported_string_array(src, "E") == ["a", "b"]


def test_an_unrelated_string_literal_elsewhere_does_not_add_an_entry():
    src = LIVE + "const other = 'ghost'\nconst tpl = `also ${'ghost2'} here`\n"
    assert exported_string_array(src, "E") == ["a", "b", "c"]


def test_a_declaration_that_is_not_exported_is_not_the_answer():
    """界面消费的是**导出的**那个绑定；同名局部量不算数。"""
    with pytest.raises(AssertionError, match="找到 0 处"):
        exported_string_array("const E = ['a'] as const\n", "E")


def test_two_live_declarations_are_a_red_not_a_coin_flip():
    with pytest.raises(AssertionError, match="找到 2 处"):
        exported_string_array(LIVE + "export const E = ['x'] as const\n", "E")


def test_a_value_it_cannot_read_exactly_is_a_red_not_a_guess():
    """展开 / 变量读不出确切取值——报错，别猜。

    一个语义错的精确值比一个诚实的失败更坏：门禁会拿它去比，然后绿。
    """
    with pytest.raises(AssertionError, match="别的东西"):
        exported_string_array("export const E = ['a', ...OTHER] as const\n", "E")


def test_type_annotated_declaration_still_reads():
    src = "export const E: readonly string[] = ['a', 'b'] as const\n"
    assert exported_string_array(src, "E") == ["a", "b"]


def test_blanking_keeps_offsets_and_line_numbers():
    """抹掉注释与字符串不许改变长度与行数——否则回原文切片会切错位置。"""
    src = "const a = 1 // 注释\nconst b = 'x'\n/* 块\n注释 */\n"
    code, spans = blank_comments_and_strings(src)
    assert len(code) == len(src)
    assert code.count("\n") == src.count("\n")
    assert [src[a:b] for a, b in spans] == ["x"]
    assert "注释" not in code


def test_an_unterminated_string_is_a_red():
    with pytest.raises(AssertionError, match="收尾引号"):
        blank_comments_and_strings("const a = 'oops\n")


def test_single_string_const_reads_the_live_one():
    src = "// export const D = 'old'\nexport const D: Fam = 'sans' as const\n"
    assert exported_string(src, "D") == "sans"


def test_single_string_const_with_two_live_declarations_is_a_red():
    with pytest.raises(AssertionError, match="找到 2 处"):
        exported_string("export const D = 'a'\nexport const D = 'b'\n", "D")


# ---- 整数常量（诊断包 bundle schema 那一对，#536 评审）----


def test_number_const_reads_the_live_one():
    src = "/** 说明 */\nexport const V = 3\nexport const W = 4;\n"
    assert exported_number(src, "V") == 3
    assert exported_number(src, "W") == 4


def test_number_const_with_only_a_commented_out_declaration_is_a_red():
    """#536 评审的原话：真的那行改掉 / 删掉、只剩注释里的 `= 3`，裸正则照样读出 3。"""
    for src in (
        "// export const V = 3\nexport const V2 = 9\n",
        "/* export const V = 3 */\n",
        "const doc = 'export const V = 3'\n",
    ):
        with pytest.raises(AssertionError):
            exported_number(src, "V")


def test_number_const_ignores_a_stale_comment_next_to_the_live_one():
    src = "// export const V = 2\nexport const V = 3\n"
    assert exported_number(src, "V") == 3


@pytest.mark.parametrize(
    "src",
    [
        "const V = 3\n",  # 没导出
        "export const V = 3\nexport const V = 4\n",  # 两份
        "export const V = 3 + 1\n",
        "export const V = OTHER\n",
        "export const V = 3\n  + 1\n",  # 换行续写
        "export const V = '3'\n",
        # #536 第四轮：换行后接单词形式的运算符 / 成员访问 / 调用 / 索引 / 一元续写，运行时都不是 3
        "export const V = 3\n  in {3: true}\n",
        "export const V = 3\n  instanceof Number\n",
        "export const V = 3\n  ? 1 : 2\n",
        "export const V = 3\n  .toString()\n",
        "export const V = 3\n  (x)\n",
        "export const V = 3\n  [0]\n",
        "export const V = 3\n  - 1\n",
        "export const V = 3\n  as unknown\n",
        "export const V = 3\n  foo\n",  # 不在声明关键字闭集里的单词一律不认
        "export const V = 3\n`tag`\nexport const W = 1\n",  # 带标签的模板
        "export const V = 3 as number\n",
        "export const V = 3 satisfies number\n",
        "export const V = 0x3\n",
        "export const V = 3n\n",
        "export const V = 3.0\n",
        "export const V = 3e0\n",
        "export const V = 03\n",
        "export const V = -3\n",
    ],
)
def test_number_const_it_cannot_read_exactly_is_a_red(src):
    with pytest.raises(AssertionError):
        exported_number(src, "V")


@pytest.mark.parametrize(
    ("src", "value"),
    [
        ("export const V = 3", 3),  # 文件结束
        ("export const V = 3;\nfoo()\n", 3),  # 分号之后随便
        ("export const V = 3 // 注释\n/** 下一个 */\nexport const W = 4\n", 3),
        ("export const V = 1_000\nconst x = 1\n", 1000),
        ("export const V: number = 3\ninterface X {}\n", 3),
    ],
)
def test_number_const_ends_at_a_semicolon_or_a_declaration_keyword(src, value):
    assert exported_number(src, "V") == value


# ---- 正则字面量（#540：`/export const V = 3;/` 曾被读成 3）----


@pytest.mark.parametrize(
    "src",
    [
        "const re = /export const V = 3;/\n",
        "check(/export const V = 3;/g)\n",
        "const re = /[/]export const V = 3;/\n",  # 字符类里的 `/` 不收尾
        "const re = /\\/export const V = 3;/\n",  # 转义的 `/` 不收尾
    ],
)
def test_number_const_inside_a_regex_literal_is_a_red(src):
    with pytest.raises(AssertionError):
        exported_number(src, "V")


@pytest.mark.parametrize(
    "regex",
    [
        "/'/",
        "/[/]'/",  # 字符类里的 `/` 不收尾
        "/\\/'/",  # 转义的 `/` 不收尾
        "/'/gi",
    ],
)
def test_regex_literal_with_a_quote_does_not_derail_the_scan(regex):
    src = f"const re = {regex}\nexport const V = 3\n"
    assert exported_number(src, "V") == 3


@pytest.mark.parametrize(
    "src",
    [
        # 除号之后故意不再出现 `/`：否则两个除号之间恰好凑成一条正则，量不出误判
        "const x = a / 2\nexport const V = 3\n",  # 标识符之后是除号
        "const x = 6 / 2\nexport const V = 3\n",  # 数字之后是除号
        "const x = f(a) / 2\nexport const V = 3\n",  # `)` 之后是除号
        "const x = a[0] / 2\nexport const V = 3\n",  # `]` 之后是除号
        "const x = '4' / 2\nexport const V = 3\n",  # 字符串之后是除号
        "const x = <A>b</A>\nexport const V = 3\n",  # TSX 闭合标签
        "const x = <A b={1} />\nexport const V = 3\n",  # TSX 自闭合（`}` 之后）
    ],
)
def test_division_and_tsx_tags_are_not_regex_literals(src):
    assert exported_number(src, "V") == 3


def test_regex_literal_is_blanked_but_not_a_string_span():
    src = "const re = /ab'c/gi; const s = 'd'\n"
    code, spans = blank_comments_and_strings(src)
    assert len(code) == len(src)
    assert "ab" not in code and "gi" not in code
    assert [src[a:b] for a, b in spans] == ["d"]


def test_interface_member_with_a_multiline_union_is_one_member():
    """跨行的联合类型（`type:` 下面一行一个 `| '…'`）是**一个**成员，不是一串读不出形状的行。"""
    from tests.support.tsconst import exported_interface_members

    src = """export interface F {
  prop: string
  type:
    | 'text'
    | 'number'
  value: unknown
}
"""
    got = exported_interface_members(src, "F")
    assert list(got) == ["prop", "type", "value"]
    assert "'number'" in got["type"]
