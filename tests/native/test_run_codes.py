"""稳定错误码表（ADR 0021 §13）。

**码稳定、文案随时可改**——所以判据全挂在"表本身长得对不对"上，一条都不
挂在具体措辞上。

中英同表是刻意的：加一条码却只给中文，等于把英文用户送回 traceback
（issue #30 记的正是这一族）。这批用例让"漏了一种语言"在**加码的那一刻**
就红，而不是等前端 i18n 检查在几周后报一条孤儿 key。
"""

from __future__ import annotations

import re

import pytest

from tavotto.engine import runcodes

PLACEHOLDER = re.compile(r"\{(\w+)\}")

ALL_CODES = sorted(runcodes.MESSAGES)


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_code_has_both_languages(code):
    entry = runcodes.MESSAGES[code]
    assert set(entry) == {"zh", "en"}, f"{code}: 语言不是恰好两种（{sorted(entry)}）"
    for lang, text in entry.items():
        assert text.strip(), f"{code}/{lang} 是空的"


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_two_languages_use_the_same_placeholders(code):
    """占位符两侧必须一致。

    只在一侧带参数的文案会**静默丢掉信息**：中文说"找不到解释器 /x/python"，
    英文只说 "Interpreter not found"——而那个路径正是用户唯一需要的东西。
    """
    entry = runcodes.MESSAGES[code]
    zh = set(PLACEHOLDER.findall(entry["zh"]))
    en = set(PLACEHOLDER.findall(entry["en"]))
    assert zh == en, f"{code}: 占位符不一致 zh={sorted(zh)} en={sorted(en)}"


@pytest.mark.parametrize("code", ALL_CODES)
def test_code_constants_and_table_agree(code):
    """表里的每个码都有一个模块级常量，反过来也成立。

    只有常量没有文案 = `RunError` 一构造就 KeyError；只有文案没有常量 =
    调用方只能手写字符串，而手写的那个迟早会拼错。
    """
    names = {
        getattr(runcodes, n)
        for n in dir(runcodes)
        if n.isupper() and isinstance(getattr(runcodes, n), str)
    }
    assert code in names, f"{code} 没有对应的模块级常量"


def test_every_constant_is_in_the_table():
    codes = {
        name: getattr(runcodes, name)
        for name in dir(runcodes)
        if name.isupper() and isinstance(getattr(runcodes, name), str)
    }
    # 这几个不是错误码
    skip = {"EXIT_OK", "EXIT_USAGE", "EXIT_CANCELLED", "EXIT_ATTACH_FAILED", "EXIT_TERMINATED"}
    for name, value in codes.items():
        if name in skip or not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            continue
        assert value in runcodes.MESSAGES, f"常量 {name}={value!r} 没有文案"


def test_an_unregistered_code_raises_instead_of_saying_unknown_error():
    """未登记的码**当场抛**，不给一句"未知错误"糊过去。

    糊过去的表现是：某条路径上多了一个没人写文案的码，而用户看到的是
    "未知错误"——最没有信息量的那一句，恰恰出现在最需要信息的地方。
    """
    with pytest.raises(KeyError):
        runcodes.message_for("no_such_code_at_all")
    with pytest.raises(KeyError):
        runcodes.RunError("no_such_code_at_all")


def test_message_survives_a_missing_placeholder():
    """少给一个占位符不该让报错本身变成崩溃——错误路径上尤其如此。"""
    text = runcodes.message_for(runcodes.UNSUPPORTED_RUN_COMMAND)
    assert text  # 不抛，回模板原文
    assert runcodes.message_for(runcodes.UNSUPPORTED_RUN_COMMAND, "en", command="make")
    assert "make" in runcodes.message_for(runcodes.UNSUPPORTED_RUN_COMMAND, "zh", command="make")


def test_exit_codes_are_the_four_documented_ones():
    """**不把所有失败都返回 1**（ADR 0021 §10.2）。"""
    assert (runcodes.EXIT_OK, runcodes.EXIT_USAGE) == (0, 2)
    assert (runcodes.EXIT_CANCELLED, runcodes.EXIT_ATTACH_FAILED) == (3, 4)
    assert runcodes.EXIT_TERMINATED == 5
    assert 1 not in set(runcodes.EXIT_FOR_CODE.values()), (
        "有一条失败被折成了 1——那是「脚本自己失败了」的退出码，混在一起就分不开"
    )


def test_invocation_errors_all_exit_two():
    """ "脚本还没启动"那一族**必须**是 usage 档：它们全都可以靠改命令解决。"""
    invocation = [
        runcodes.RUN_COMMAND_MISSING,
        runcodes.UNSUPPORTED_RUN_COMMAND,
        runcodes.UNSUPPORTED_PYTHON_OPTION,
        runcodes.INTERPRETER_NOT_FOUND,
        runcodes.INTERPRETER_NOT_EXECUTABLE,
        runcodes.UNSUPPORTED_PYTHON_VERSION,
        runcodes.UNSUPPORTED_PYTHON_IMPLEMENTATION,
        runcodes.SCRIPT_TARGET_MISSING,
        runcodes.SCRIPT_TARGET_NOT_FILE,
        runcodes.INVALID_MODULE_NAME,
        runcodes.PROJECT_ROOT_INVALID,
        runcodes.PROJECT_UNREADABLE,
    ]
    for code in invocation:
        assert runcodes.RunError(code).exit_code() == runcodes.EXIT_USAGE, code


def test_the_run_error_payload_carries_the_code_not_a_traceback():
    """后端**不得**把整段中文 traceback 当主错误（ADR 0021 §13）。"""
    payload = runcodes.RunError(runcodes.INTERPRETER_NOT_FOUND, interpreter="/x/python").payload()
    assert payload["code"] == runcodes.INTERPRETER_NOT_FOUND
    assert payload["ok"] is False
    assert payload["interpreter"] == "/x/python"
    assert "Traceback" not in payload["error"]


def test_no_code_string_collides():
    values = list(runcodes.MESSAGES)
    assert len(values) == len(set(values))
