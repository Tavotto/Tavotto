"""重生成 `tests/golden/script_args_form_vectors.json`（T07）。

脚本与期望（编辑后的 token、真 argparse 的 Namespace / 退出码、缺哪些必填）是**手写的**真值；schema 是
`engine/scriptargs.analyze` 的快照。改了分析器就重跑：`python scripts/dev/gen_script_args_vectors.py`，再逐条核对 diff。
两侧的用例：`tests/test_script_args.py`（Python，跑真 argparse）、`web/src/lib/scriptArgsForm.golden.test.ts`（TS）。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tavotto.engine import scriptargs  # noqa: E402 — 先把仓库的 src 放上 sys.path

DUMP = """
def _dump(ns):
    import json
    out = {}
    for k, v in sorted(vars(ns).items()):
        out[k] = getattr(v, "name", v) if not isinstance(v, (str, int, float, bool, type(None), list)) else v
    print(json.dumps(out, ensure_ascii=False))
"""

SCRIPTS = {
    "short_options": """import argparse
p = argparse.ArgumentParser()
p.add_argument("-k", default="none")
p.add_argument("-k-x", "-kitty", dest="collision", action="store_true")
p.add_argument("-f", action=argparse.BooleanOptionalAction, default=True)
"""
    + DUMP
    + "_dump(p.parse_args())\n",
    "fft6": """import argparse
p = argparse.ArgumentParser(description="FFT")
p.add_argument("--freq", type=float, required=True, help="Hz")
p.add_argument("--amp", type=float, required=True)
p.add_argument("--phase", type=float, required=True)
p.add_argument("--n", type=int, required=True)
p.add_argument("--tag", required=True)
p.add_argument("--mode", choices=["sin", "cos"], required=True)
args = p.parse_args()
"""
    + DUMP
    + "_dump(args)\n",
    "mixed": """import argparse
parser = argparse.ArgumentParser()
parser.add_argument("src", help="input csv")
parser.add_argument("count", type=int)
parser.add_argument("--scale", type=float, default=1.0)
parser.add_argument("-k", "--k-value", default="none")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--color", action=argparse.BooleanOptionalAction)
parser.add_argument("--level", type=int, choices=[1, 2, 3], default=2)
speed = parser.add_mutually_exclusive_group()
speed.add_argument("--fast", action="store_true")
speed.add_argument("--slow", action="store_true")
parser.add_argument("--out", type=argparse.FileType("w"), default=None)
"""
    + DUMP
    + """
if __name__ == "__main__":
    _dump(parser.parse_args())
""",
    "reqdef": """import argparse
p = argparse.ArgumentParser()
p.add_argument("--thr", type=float, required=True, default=0.5)
p.add_argument("--opt", type=int, default=3)
"""
    + DUMP
    + "_dump(p.parse_args())\n",
    "partial": """import argparse
p = argparse.ArgumentParser()
for name in ("alpha",):
    p.add_argument("--" + name)
p.add_argument("--known")
"""
    + DUMP
    + "_dump(p.parse_args())\n",
    "negopt": """import argparse
p = argparse.ArgumentParser()
p.add_argument("-1", dest="one", action="store_true")
p.add_argument("--val")
"""
    + DUMP
    + "_dump(p.parse_args())\n",
    "subcmd": """import argparse
p = argparse.ArgumentParser()
p.add_argument("--g")
sp = p.add_subparsers(dest="cmd", required=True)
t = sp.add_parser("train")
t.add_argument("--lr", type=float)
sp.add_parser("eval")
"""
    + DUMP
    + "_dump(p.parse_args())\n",
}

MIXED_BASE = {
    "color": None,
    "count": 3,
    "fast": False,
    "k_value": "none",
    "level": 2,
    "out": None,
    "scale": 1.0,
    "slow": False,
    "src": "in.csv",
    "verbose": False,
}


def mixed(**kw):
    d = dict(MIXED_BASE)
    d.update(kw)
    return d


CASES = [
    {
        "name": "short_attached_ordinary_value_cannot_select_an_exact_option",
        "script": "short_options",
        "before": ["-kold"],
        "edits": [{"op": "set", "arg": "k", "values": ["itty"]}],
        "after": ["-k=itty"],
        "namespace": {"k": "itty", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_option_edit_preserves_other_exact_option",
        "script": "short_options",
        "before": ["-k-x"],
        "edits": [{"op": "set", "arg": "k", "values": ["-x"]}],
        "after": ["-k-x", "-k=-x"],
        "namespace": {"k": "-x", "collision": True, "f": True},
        "missing": [],
    },
    {
        "name": "short_new_option_like_value",
        "script": "short_options",
        "before": [],
        "edits": [{"op": "set", "arg": "k", "values": ["-x"]}],
        "after": ["-k=-x"],
        "namespace": {"k": "-x", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_replace_separate_option_like_value",
        "script": "short_options",
        "before": ["-k", "old"],
        "edits": [{"op": "set", "arg": "k", "values": ["-x"]}],
        "after": ["-k=-x"],
        "namespace": {"k": "-x", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_replace_attached_option_like_value",
        "script": "short_options",
        "before": ["-kold"],
        "edits": [{"op": "set", "arg": "k", "values": ["-x"]}],
        "after": ["-k=-x"],
        "namespace": {"k": "-x", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_equals_value_is_read_without_separator",
        "script": "short_options",
        "before": ["-k=-x"],
        "edits": [{"op": "set", "arg": "k", "values": ["next"]}],
        "after": ["-k=next"],
        "namespace": {"k": "next", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_replace_attached_empty_value",
        "script": "short_options",
        "before": ["-kold"],
        "edits": [{"op": "set", "arg": "k", "values": [""]}],
        "after": ["-k="],
        "namespace": {"k": "", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_replace_attached_equals_value",
        "script": "short_options",
        "before": ["-kold"],
        "edits": [{"op": "set", "arg": "k", "values": ["=x"]}],
        "after": ["-k==x"],
        "namespace": {"k": "=x", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_boolean_on",
        "script": "short_options",
        "before": [],
        "edits": [{"op": "flag", "arg": "f", "value": "on"}],
        "after": ["-f"],
        "namespace": {"k": "none", "collision": False, "f": True},
        "missing": [],
    },
    {
        "name": "short_boolean_default",
        "script": "short_options",
        "before": ["-f"],
        "edits": [{"op": "flag", "arg": "f", "value": "default"}],
        "after": [],
        "namespace": {"k": "none", "collision": False, "f": True},
        "missing": [],
    },
    # --- fft6：六必填，从空草稿逐项填满（A01）
    {
        "name": "fft6_fill_all",
        "script": "fft6",
        "before": [],
        "edits": [
            {"op": "set", "arg": "freq", "values": ["3"]},
            {"op": "set", "arg": "amp", "values": ["2.5"]},
            {"op": "set", "arg": "phase", "values": ["0.25"]},
            {"op": "set", "arg": "n", "values": ["16"]},
            {"op": "set", "arg": "tag", "values": ["T"]},
            {"op": "set", "arg": "mode", "values": ["sin"]},
        ],
        "after": [
            "--freq",
            "3",
            "--amp",
            "2.5",
            "--phase",
            "0.25",
            "--n",
            "16",
            "--tag",
            "T",
            "--mode",
            "sin",
        ],
        "namespace": {"amp": 2.5, "freq": 3.0, "mode": "sin", "n": 16, "phase": 0.25, "tag": "T"},
        "missing": [],
    },
    {
        "name": "fft6_missing_reported_not_defaulted",
        "script": "fft6",
        "before": ["--freq", "3"],
        "edits": [],
        "after": ["--freq", "3"],
        "exit": 2,
        "missing": ["amp", "phase", "n", "tag", "mode"],
    },
    # --- required + default：default 不是答案
    {
        "name": "required_default_is_not_an_answer",
        "script": "reqdef",
        "before": [],
        "edits": [],
        "after": [],
        "exit": 2,
        "missing": ["thr"],
    },
    {
        "name": "required_default_answered",
        "script": "reqdef",
        "before": [],
        "edits": [{"op": "set", "arg": "thr", "values": ["0.5"]}],
        "after": ["--thr", "0.5"],
        "namespace": {"opt": 3, "thr": 0.5},
        "missing": [],
    },
    # --- mixed：位置 / 选项 / 开关 / 互斥 / 中文 / -- / 重复 / 粘连
    {
        "name": "set_option_appends_after_positionals",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [{"op": "set", "arg": "scale", "values": ["2.5"]}],
        "after": ["in.csv", "3", "--scale", "2.5"],
        "namespace": mixed(scale=2.5),
        "missing": [],
    },
    {
        "name": "option_like_value_is_glued",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [{"op": "set", "arg": "k_value", "values": ["-x"]}],
        "after": ["in.csv", "3", "--k-value=-x"],
        "namespace": mixed(k_value="-x"),
        "missing": [],
    },
    {
        "name": "empty_string_is_a_value",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [{"op": "set", "arg": "k_value", "values": [""]}],
        "after": ["in.csv", "3", "--k-value", ""],
        "namespace": mixed(k_value=""),
        "missing": [],
    },
    {
        "name": "negative_number_stays_separate",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [{"op": "set", "arg": "scale", "values": ["-2"]}],
        "after": ["in.csv", "3", "--scale", "-2"],
        "namespace": mixed(scale=-2.0),
        "missing": [],
    },
    {
        "name": "flag_on_then_bool_off",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [
            {"op": "flag", "arg": "verbose", "value": "on"},
            {"op": "flag", "arg": "color", "value": "off"},
        ],
        "after": ["in.csv", "3", "--verbose", "--no-color"],
        "namespace": mixed(verbose=True, color=False),
        "missing": [],
    },
    {
        "name": "bool_default_removes_it",
        "script": "mixed",
        "before": ["in.csv", "--color", "3", "--verbose"],
        "edits": [
            {"op": "flag", "arg": "color", "value": "default"},
            {"op": "flag", "arg": "verbose", "value": "off"},
        ],
        "after": ["in.csv", "3"],
        "namespace": mixed(),
        "missing": [],
    },
    {
        "name": "repeated_option_edits_the_effective_last_one",
        "script": "mixed",
        "before": ["--scale", "1", "in.csv", "3", "--scale", "3"],
        "edits": [{"op": "set", "arg": "scale", "values": ["4"]}],
        "after": ["--scale", "1", "in.csv", "3", "--scale", "4"],
        "namespace": mixed(scale=4.0),
        "missing": [],
    },
    {
        "name": "glued_forms_keep_their_shape",
        "script": "mixed",
        "before": ["in.csv", "3", "--scale=2", "-kabc"],
        "edits": [
            {"op": "set", "arg": "scale", "values": ["3"]},
            {"op": "set", "arg": "k_value", "values": ["xyz"]},
        ],
        "after": ["in.csv", "3", "--scale=3", "-kxyz"],
        "namespace": mixed(scale=3.0, k_value="xyz"),
        "missing": [],
    },
    {
        "name": "options_go_before_double_dash",
        "script": "mixed",
        "before": ["--", "-in.csv", "3"],
        "edits": [{"op": "set", "arg": "scale", "values": ["2"]}],
        "after": ["--scale", "2", "--", "-in.csv", "3"],
        "namespace": mixed(scale=2.0, src="-in.csv"),
        "missing": [],
    },
    {
        "name": "positional_after_double_dash",
        "script": "mixed",
        "before": ["--", "-in.csv"],
        "edits": [{"op": "set", "arg": "count", "values": ["5"]}],
        "after": ["--", "-in.csv", "5"],
        "namespace": mixed(src="-in.csv", count=5),
        "missing": [],
    },
    {
        "name": "chinese_path_with_space_positional",
        "script": "mixed",
        "before": ["--scale", "2"],
        "edits": [
            {"op": "set", "arg": "src", "values": ["数据/样本 1.csv"]},
            {"op": "set", "arg": "count", "values": ["7"]},
        ],
        "after": ["数据/样本 1.csv", "7", "--scale", "2"],
        "namespace": mixed(src="数据/样本 1.csv", count=7, scale=2.0),
        "missing": [],
    },
    {
        "name": "clear_removes_every_occurrence",
        "script": "mixed",
        "before": ["--scale", "1", "in.csv", "3", "--scale=2"],
        "edits": [{"op": "clear", "arg": "scale"}],
        "after": ["in.csv", "3"],
        "namespace": mixed(),
        "missing": [],
    },
    {
        "name": "exclusive_conflict_is_left_to_the_parser",
        "script": "mixed",
        "before": ["in.csv", "3", "--fast"],
        "edits": [{"op": "flag", "arg": "slow", "value": "on"}],
        "after": ["in.csv", "3", "--fast", "--slow"],
        "exit": 2,
        "missing": [],
        "conflicts": ["g0"],
    },
    {
        "name": "output_file_value_is_only_a_token",
        "script": "mixed",
        "before": ["in.csv", "3"],
        "edits": [{"op": "set", "arg": "out", "values": ["结果.txt"]}],
        "after": ["in.csv", "3", "--out", "结果.txt"],
        "namespace": mixed(out="结果.txt"),
        "missing": [],
        "creates": ["结果.txt"],
    },
    {
        "name": "unknown_option_freezes_positionals_but_keeps_tokens",
        "script": "partial",
        "before": ["--alpha", "1", "--known", "a"],
        "edits": [{"op": "set", "arg": "known", "values": ["b"]}],
        "after": ["--alpha", "1", "--known", "b"],
        "namespace": {"alpha": "1", "known": "b"},
        "missing": [],
        "unattributed": [0, 1],
    },
    {
        "name": "negative_number_options_force_glue",
        "script": "negopt",
        "before": [],
        "edits": [{"op": "set", "arg": "val", "values": ["-5"]}],
        "after": ["--val=-5"],
        "namespace": {"one": False, "val": "-5"},
        "missing": [],
    },
]
# 位置参数 / 只读等错误路径：TS 侧专测（没有 after / namespace）
ERRORS = [
    {
        "name": "short_boolean_cannot_force_off",
        "script": "short_options",
        "before": ["-f"],
        "edit": {"op": "flag", "arg": "f", "value": "off"},
        "error": "not_editable",
    },
    {
        "name": "positional_option_like_needs_dashes",
        "script": "mixed",
        "before": [],
        "edit": {"op": "set", "arg": "src", "values": ["-weird"]},
        "error": "positional_looks_like_option",
    },
    {
        "name": "earlier_positional_missing",
        "script": "mixed",
        "before": [],
        "edit": {"op": "set", "arg": "count", "values": ["1"]},
        "error": "earlier_positional_missing",
    },
    {
        "name": "subcommand_form_is_disabled",
        "script": "subcmd",
        "before": ["train", "--lr", "0.1"],
        "edit": {"op": "set", "arg": "g", "values": ["x"]},
        "error": "form_disabled",
    },
    {
        "name": "partial_positionals_uncertain",
        "script": "mixed",
        "before": ["--weird", "in.csv"],
        "edit": {"op": "set", "arg": "count", "values": ["1"]},
        "error": "positionals_uncertain",
    },
]

schemas = {name: scriptargs.analyze(src) for name, src in SCRIPTS.items()}


# 用 dest 当编辑里的 arg 名更可读；转换成 id
def to_id(script, dest):
    for a in schemas[script]["arguments"]:
        if a["dest"] == dest:
            return a["id"]
    raise KeyError((script, dest))


for c in CASES:
    for e in c["edits"]:
        e["arg"] = to_id(c["script"], e["arg"])
    c["missing"] = [to_id(c["script"], d) for d in c["missing"]]
for c in ERRORS:
    c["edit"]["arg"] = to_id(c["script"], c["edit"]["arg"])
out = {
    "version": 1,
    "note": "T07：表单编辑 → token（TS：web/src/lib/scriptArgsForm.golden.test.ts）与 token → 真 argparse（Python：tests/test_script_args.py）共用；schema 是 engine/scriptargs.analyze 的快照，改分析器后用 scripts/dev/gen_script_args_vectors.py 重生成并逐条核对期望",
    "scripts": SCRIPTS,
    "schemas": schemas,
    "cases": CASES,
    "errors": ERRORS,
}
(ROOT / "tests/golden/script_args_form_vectors.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
)
print("ok", len(CASES), len(ERRORS))
