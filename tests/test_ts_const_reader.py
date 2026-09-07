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
