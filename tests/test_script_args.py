"""T07：脚本参数的静态 schema（`engine/scriptargs.py`）与「表单 → token」的真值对拍。

判据的主语：

* **schema 只读源码**：`analyze` / `analyze_file` 不执行、不 import、不打开 `FileType` 的文件——看护用的是脚本
  顶层的副作用哨兵与已存在输出文件的字节，不是"函数里没写 open"。
* **token 的含义由真 argparse 定**：`tests/golden/script_args_form_vectors.json` 里每条"编辑之后的 token"都在
  子进程里交给**那份脚本自己的 parser**（同一个解释器原生跑），比对 Namespace / 退出码。TS 那边
  （`web/src/lib/scriptArgsForm.golden.test.ts`）证明表单编辑恰好产出这串 token——两边合起来才是
  "表单填的 = 脚本收到的"。
* **粘贴命令**：被接受的那些分词结果必须逐项等于 `shlex.split`（`tests/golden/argv_paste_vectors.json`）。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import scriptargs

GOLDEN = Path(__file__).parent / "golden"
FORM = json.loads((GOLDEN / "script_args_form_vectors.json").read_text("utf-8"))
PASTE = json.loads((GOLDEN / "argv_paste_vectors.json").read_text("utf-8"))


def _by_dest(schema: dict) -> dict:
    return {a["dest"]: a for a in schema["arguments"]}


# ---------------------------------------------------------------- 向量：schema 快照 + 真 argparse


@pytest.mark.parametrize("name", sorted(FORM["scripts"]))
def test_the_vector_schema_is_what_the_analyzer_reads_today(name):
    """TS 用的 schema 就是后端此刻给的那份（两侧不各写一份）。"""
    assert scriptargs.analyze(FORM["scripts"][name]) == FORM["schemas"][name]


def _native(tmp_path: Path, script: str, tokens: list[str]) -> subprocess.CompletedProcess:
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    path = tmp_path / "s.py"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(path), *tokens],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


@pytest.mark.parametrize("case", FORM["cases"], ids=lambda c: c["name"])
def test_the_tokens_the_form_writes_mean_what_the_vector_says_to_the_real_parser(tmp_path, case):
    done = _native(tmp_path, FORM["scripts"][case["script"]], case["after"])
    if "namespace" in case:
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout.strip().splitlines()[-1]) == case["namespace"]
    else:
        assert done.returncode == case["exit"], (done.stdout, done.stderr)
    for created in case.get("creates", ()):
        assert (
            tmp_path / "cwd" / created
        ).exists()  # FileType('w') 是**运行时**打开的：这是原生语义


def test_a_default_is_never_an_answer_to_required():
    """反证主语：`required=True, default=0.5` 的参数不给 token，真 parser 退出 2——默认值不能被表单当答案。"""
    case = next(c for c in FORM["cases"] if c["name"] == "required_default_is_not_an_answer")
    thr = _by_dest(FORM["schemas"]["reqdef"])["thr"]
    assert thr["required"] is True and thr["default"] == {"kind": "literal", "display": "0.5"}
    assert case["after"] == [] and case["exit"] == 2 and case["missing"] == [thr["id"]]


def test_the_vectors_cover_the_semantics_the_stage_promises():
    names = {c["name"] for c in FORM["cases"]}
    for needed in (
        "fft6_fill_all",
        "required_default_is_not_an_answer",
        "repeated_option_edits_the_effective_last_one",
        "empty_string_is_a_value",
        "negative_number_stays_separate",
        "options_go_before_double_dash",
        "chinese_path_with_space_positional",
        "exclusive_conflict_is_left_to_the_parser",
        "unknown_option_freezes_positionals_but_keeps_tokens",
        "output_file_value_is_only_a_token",
    ):
        assert needed in names


# ---------------------------------------------------------------- 只读：不执行、不打开输出


def test_reading_a_schema_runs_nothing_and_never_opens_the_output_file(tmp_path):
    """A05：`FileType('w')` 带默认输出名、脚本顶层还会写哨兵——分析之后哨兵不存在、已有输出逐字节不变、没有新文件。"""
    sentinel = tmp_path / "ran.txt"
    out = tmp_path / "out.txt"
    out.write_bytes(b"precious")
    script = tmp_path / "w.py"
    script.write_text(
        "import argparse\n"
        f"open({str(sentinel)!r}, 'w').write('ran')\n"
        "p = argparse.ArgumentParser()\n"
        f"p.add_argument('--out', type=argparse.FileType('w'), default={str(out)!r})\n"
        "p.add_argument('dst', type=argparse.FileType('wb'))\n"
        "p.add_argument('--src', type=argparse.FileType('r'))\n"
        "args = p.parse_args()\n",
        encoding="utf-8",
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    schema = scriptargs.analyze_file(script)
    assert not sentinel.exists()
    assert out.read_bytes() == b"precious"
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    by = _by_dest(schema)
    assert (by["out"]["role"], by["dst"]["role"], by["src"]["role"]) == (
        "output_file",
        "output_file",
        "input_file",
    )
    assert scriptargs.summary(schema)["output_files"] == 2


def test_names_never_decide_a_role_or_unit():
    """仅凭 `x` / `time` / `range` / `data` 这些名字不给角色——只有 FileType 给。"""
    schema = scriptargs.analyze(
        "import argparse\nfrom pathlib import Path\np = argparse.ArgumentParser()\n"
        "p.add_argument('--x')\np.add_argument('--time', type=float)\n"
        "p.add_argument('--range', nargs=2, type=float)\np.add_argument('--data', type=Path)\n"
        "p.parse_args()\n"
    )
    by = _by_dest(schema)
    assert all(a["role"] is None for a in schema["arguments"])
    assert by["data"]["type"] == "path" and by["range"]["arity"] == 2


# ---------------------------------------------------------------- 识别不全 = partial，照样可运行


@pytest.mark.parametrize(
    ("label", "source", "reason", "form_enabled"),
    [
        (
            "loop_builder",
            "import argparse\np = argparse.ArgumentParser()\n"
            "for n in ('a', 'b'):\n    p.add_argument('--' + n)\np.parse_args()\n",
            "dynamic_add_argument",
            True,
        ),
        (
            # 循环体里的 flags 即使是字面量也可能加 0 次 / 多次：不能当成确定声明
            "loop_literal",
            "import argparse\np = argparse.ArgumentParser()\n"
            "for _ in range(2):\n    p.add_argument('--x')\np.parse_args()\n",
            "dynamic_add_argument",
            True,
        ),
        (
            "kwargs",
            "import argparse\np = argparse.ArgumentParser()\nkw = {}\n"
            "p.add_argument('--a', **kw)\np.parse_args()\n",
            "dynamic_add_argument",
            True,
        ),
        (
            "custom_action",
            "import argparse\nclass A(argparse.Action):\n    pass\n"
            "p = argparse.ArgumentParser()\np.add_argument('--a', action=A)\np.parse_args()\n",
            "custom_action",
            True,
        ),
        (
            "subcommands",
            "import argparse\np = argparse.ArgumentParser()\ns = p.add_subparsers()\n"
            "s.add_parser('go')\np.parse_args()\n",
            "subcommands",
            False,
        ),
        (
            "explicit_list",
            "import argparse\np = argparse.ArgumentParser()\np.add_argument('--a')\n"
            "p.parse_args(['--a', '1'])\n",
            "explicit_parse_args",
            False,
        ),
        (
            "known_args",
            "import argparse\np = argparse.ArgumentParser()\np.add_argument('--a')\n"
            "p.parse_known_args()\n",
            "parse_known_args",
            True,
        ),
        (
            "remainder",
            "import argparse\np = argparse.ArgumentParser()\n"
            "p.add_argument('rest', nargs=argparse.REMAINDER)\np.parse_args()\n",
            "remainder",
            False,
        ),
        (
            "parents",
            "import argparse\nb = argparse.ArgumentParser(add_help=False)\n"
            "p = argparse.ArgumentParser(parents=[b])\np.parse_args()\n",
            "parents",
            False,
        ),
        (
            "conditional",
            "import argparse, os\np = argparse.ArgumentParser()\n"
            "if os.environ.get('X'):\n    p.add_argument('--a')\np.parse_args()\n",
            "conditional_argument",
            True,
        ),
        (
            "reads_argv_too",
            "import argparse, sys\np = argparse.ArgumentParser()\np.add_argument('--a')\n"
            "p.parse_args()\nprint(sys.argv[-1])\n",
            "reads_sys_argv",
            True,
        ),
        (
            "no_parse_call",
            "import argparse\np = argparse.ArgumentParser()\np.add_argument('--a')\n",
            "no_parse_call",
            False,
        ),
    ],
)
def test_what_cannot_be_proven_is_marked_partial_with_its_reason(
    label, source, reason, form_enabled
):
    schema = scriptargs.analyze(source)
    assert schema["status"] == "partial", label
    assert reason in schema["reasons"], (label, schema["reasons"])
    assert schema["form_enabled"] is form_enabled, label
    assert set(schema["reasons"]) <= set(scriptargs.REASONS)


def test_a_custom_action_argument_is_shown_but_not_form_editable():
    schema = scriptargs.analyze(
        "import argparse\nclass A(argparse.Action):\n    pass\np = argparse.ArgumentParser()\n"
        "p.add_argument('--a', action=A)\np.add_argument('--b')\np.parse_args()\n"
    )
    by = _by_dest(schema)
    assert by["a"]["action"] == "custom" and by["a"]["editable"] is False
    assert by["a"]["arity"] == "unknown"
    assert by["b"]["editable"] is True


@pytest.mark.parametrize(
    "source",
    [
        # 模块顶层 / 导入期（A07）、main 守卫、函数里建再 return、parse_args(sys.argv[1:])
        "import argparse\nP = argparse.ArgumentParser()\nP.add_argument('--n', required=True)\n"
        "ARGS = P.parse_args()\n",
        "import argparse\ndef main():\n    p = argparse.ArgumentParser()\n"
        "    p.add_argument('--n', required=True)\n    return p.parse_args()\n"
        "if __name__ == '__main__':\n    main()\n",
        "import argparse\ndef build():\n    p = argparse.ArgumentParser()\n"
        "    p.add_argument('--n', required=True)\n    return p\nargs = build().parse_args()\n",
        "import argparse, sys\np = argparse.ArgumentParser()\np.add_argument('--n', required=True)\n"
        "if __name__ == '__main__':\n    p.add_argument('--m')\n    p.parse_args(sys.argv[1:])\n",
        "from argparse import ArgumentParser as AP\np = AP()\np.add_argument('--n', required=True)\n"
        "p.parse_args(args=None)\n",
    ],
    ids=["import_time", "main_function", "builder_function", "sys_argv_tail", "from_import"],
)
def test_ordinary_shapes_are_complete(source):
    schema = scriptargs.analyze(source)
    assert schema["status"] == "complete", schema["reasons"]
    assert schema["form_enabled"] is True
    assert _by_dest(schema)["n"]["required"] is True


@pytest.mark.parametrize(
    ("source", "status", "reasons"),
    [
        ("import sys\nprint(sys.argv[1])\n", "none", ["reads_sys_argv"]),
        ("import click\n@click.command()\ndef main():\n    pass\n", "none", ["other_cli"]),
        ("print('hi')\n", "none", []),
        ("def broken(:\n", "unknown", ["syntax_error"]),
    ],
)
def test_without_argparse_evidence_nothing_is_claimed(source, status, reasons):
    schema = scriptargs.analyze(source)
    assert (schema["status"], schema["reasons"], schema["arguments"]) == (status, reasons, [])
    assert schema["form_enabled"] is False


def test_a_too_large_file_is_unknown_not_none(tmp_path, monkeypatch):
    monkeypatch.setattr(scriptargs, "MAX_SOURCE_BYTES", 64)
    path = tmp_path / "big.py"
    path.write_text("import argparse\n" + "#" * 200 + "\n", encoding="utf-8")
    assert scriptargs.analyze_file(path)["status"] == "unknown"
    assert scriptargs.analyze_file(path)["reasons"] == ["too_large"]


def test_the_cache_follows_the_file(tmp_path):
    path = tmp_path / "s.py"
    path.write_text("print(1)\n", encoding="utf-8")
    assert scriptargs.analyze_file(path)["status"] == "none"
    path.write_text(
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--a')\np.parse_args()\n",
        encoding="utf-8",
    )
    os.utime(path, ns=(1, 10**18))
    assert scriptargs.analyze_file(path)["status"] == "complete"


def test_the_analyzer_never_evaluates_nodes(monkeypatch):
    """主语：不 eval / literal_eval 任何节点（换成会炸的也照样出 schema）。"""
    import ast as _ast
    import builtins

    monkeypatch.setattr(_ast, "literal_eval", lambda *a, **k: pytest.fail("literal_eval"))
    monkeypatch.setattr(builtins, "eval", lambda *a, **k: pytest.fail("eval"))
    monkeypatch.setattr(builtins, "exec", lambda *a, **k: pytest.fail("exec"))
    schema = scriptargs.analyze(FORM["scripts"]["mixed"])
    assert schema["status"] == "complete"


# ---------------------------------------------------------------- 粘贴命令：shlex 是真值


@pytest.mark.parametrize("vec", PASTE["accepted"], ids=lambda v: v["text"][:40])
def test_accepted_paste_vectors_split_exactly_like_shlex(vec):
    assert shlex.split(vec["text"]) == vec["words"]
    assert vec["words"][len(vec["words"]) - len(vec["argv"]) :] == vec["argv"]


def test_rejected_paste_vectors_use_the_closed_error_set():
    codes = {
        "empty",
        "unsupported_syntax",
        "unbalanced_quote",
        "multiline",
        "env_assignment",
        "interpreter_options",
        "different_script",
        "unrecognized_launcher",
    }
    assert {v["error"] for v in PASTE["rejected"]} <= codes


# ---------------------------------------------------------------- 源码编码（Codex r4220829659）

_CHOICES = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--city', choices=['café'])\np.parse_args()\n"


def _choices_of(schema: dict) -> list:
    return _by_dest(schema)["city"]["choices"]


def test_a_cp1252_coding_declaration_is_honoured(tmp_path):
    path = tmp_path / "s.py"
    path.write_bytes(("# coding: cp1252\n" + _CHOICES).encode("cp1252"))
    schema = scriptargs.analyze_file(path)
    assert schema["status"] == "complete", schema
    assert _choices_of(schema) == ["café"]  # 不是 'caf�'


def test_a_utf8_bom_does_not_break_the_parse(tmp_path):
    path = tmp_path / "s.py"
    path.write_bytes(b"\xef\xbb\xbf" + _CHOICES.encode("utf-8"))
    schema = scriptargs.analyze_file(path)
    assert schema["status"] == "complete", schema
    assert _choices_of(schema) == ["café"]


@pytest.mark.parametrize(
    "raw",
    [
        b"# coding: no-such-codec\n" + _CHOICES.encode("utf-8"),  # 声明了不存在的编码
        b"# coding: ascii\n" + _CHOICES.encode("utf-8"),  # 声明的编码解不开内容
        b"\xef\xbb\xbf# coding: latin-1\n" + _CHOICES.encode("utf-8"),  # BOM 与声明冲突
        b"import argparse\n# \xff\xfe not utf-8\n",  # 默认 UTF-8 却不是 UTF-8
    ],
)
def test_an_undecodable_source_falls_back_to_unknown(tmp_path, raw):
    path = tmp_path / "s.py"
    path.write_bytes(raw)
    schema = scriptargs.analyze_file(path)
    assert schema["status"] == "unknown" and not schema["arguments"], schema
    assert schema["form_enabled"] is False


# ---------------------------------------------------------------- 负数 token 文法（Codex r4220829672）


@pytest.mark.parametrize(
    ("token", "legacy", "extended"),
    [
        ("-1", True, True),
        ("-1.5", True, True),
        ("-.5", True, True),
        ("-1.", False, True),
        ("-1e3", False, True),
        ("-1.5E-3", False, True),
        ("-1_0", False, True),
        ("-1j", False, True),
        ("-1abc", False, True),  # 3.14 的 match 没有结尾锚
        ("-inf", False, False),
        ("-x", False, False),
        ("-", False, False),
    ],
)
def test_the_two_negative_number_grammars_match_cpython(token, legacy, extended):
    assert scriptargs._looks_negative_number(token) is legacy
    assert scriptargs._looks_negative_number_extended(token) is extended


def test_the_grammars_equal_the_running_interpreters_argparse():
    import argparse

    parser = argparse.ArgumentParser()
    pattern = parser._negative_number_matcher.pattern
    expected = (
        scriptargs.NEGATIVE_NUMBER_EXTENDED
        if sys.version_info >= (3, 14)
        else scriptargs.NEGATIVE_NUMBER_LEGACY
    )
    assert pattern == expected.pattern


def test_declared_negative_looking_options_are_flagged_per_grammar():
    src = "import argparse\np = argparse.ArgumentParser()\np.add_argument('-1e3', dest='x')\np.parse_args()\n"
    schema = scriptargs.analyze(src)
    assert schema["negative_number_options"] is False  # 3.13 的文法下 `-1e3` 不像负数
    assert schema["negative_number_options_extended"] is True
