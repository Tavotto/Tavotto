"""设置里的「接入 AI 编辑器」：壳命令 `host_integration` 只 spawn `tavotto-cli hosts … --json`（与 Codex 按钮同一条
纪律，ADR 0012）。

参数校验的**行为**由 `src-tauri/src/main.rs` 的 `host_cli_tests`（cargo test）钉住：去掉闭集校验那几条就红。这里只做
两件 Rust 测试做不到的事，且都先用 `rust_code()` 抹掉注释（Codex #1456 P1：读原文时注释掉整段校验照样绿）：
壳里的宿主闭集与引擎逐字相同；命令真的把 argv 交给 `host_cli_args` 与 `run_cli_json`。ACL 三处同步由
`test_desktop_codex_button.py` 的枚举用例看护。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tavotto.engine import hostconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from support.rustsrc import rust_code  # noqa: E402

MAIN_RS = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
CODE = rust_code(MAIN_RS, keep_strings=True)  # 注释已抹掉，字符串留着（宿主名在字符串里）


def _fn_body(name: str) -> str:
    m = re.search(rf"\bfn {name}\b", CODE)
    assert m, f"main.rs 里没有 fn {name}"
    start = CODE.index("{", m.end())
    depth = 0
    for i in range(start, len(CODE)):
        depth += {"{": 1, "}": -1}.get(CODE[i], 0)
        if depth == 0:
            return CODE[start : i + 1]
    raise AssertionError(name)


def test_the_shell_host_list_is_the_engine_host_list_verbatim():
    m = re.search(r"const HOST_IDS: \[&str; (\d+)\] = \[(.*?)\];", CODE, re.S)
    assert m, "壳里没有（未注释的）HOST_IDS"
    names = tuple(re.findall(r'"([a-z-]+)"', m.group(2)))
    assert names == hostconnect.HOST_IDS and int(m.group(1)) == len(names)


def test_the_command_validates_through_host_cli_args_and_spawns_through_run_cli_json():
    body = _fn_body("host_integration")
    assert re.search(r"host_cli_args\(\s*&action", body), "命令没把参数交给 host_cli_args 校验"
    assert "run_cli_json(app, args)" in body
    args_fn = _fn_body("host_cli_args")
    assert "HOST_IDS.contains(" in args_fn
    assert 'vec!["hosts".into()]' in args_fn and 'args.push("--json".into())' in args_fn


def test_the_behaviour_tests_exist_and_are_not_commented_out():
    mod = re.search(r"#\[cfg\(test\)\]\s*mod host_cli_tests\b", CODE)
    assert mod, "host_cli_tests 不在（或被注释掉了）"
    tests = re.findall(r"#\[test\]\s*fn (\w+)", CODE[mod.end() :])
    assert {
        "list_takes_no_host_or_folder",
        "only_known_hosts_pass",
        "the_folder_is_one_argv_element_and_only_for_connect",
        "unknown_actions_are_refused",
    } <= set(tests)
