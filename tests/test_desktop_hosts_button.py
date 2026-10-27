"""设置里的「接入 AI 编辑器」：壳命令 `host_integration` 只 spawn `tavotto-cli hosts … --json`（与 Codex 按钮同一条
纪律，ADR 0012）。主语是 `src-tauri/src/main.rs` 的源码结构：宿主闭集与引擎逐字相同、action 是闭集、spawn 走同一个
`run_cli_json`。ACL 三处同步由 `test_desktop_codex_button.py` 的枚举用例看护（这里只钉「这个命令存在」）。"""

from __future__ import annotations

import re
from pathlib import Path

from tavotto.engine import hostconnect

ROOT = Path(__file__).resolve().parents[1]
MAIN_RS = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")


def _body(fn: str) -> str:
    return MAIN_RS.split(f"async fn {fn}")[1].split("\n}\n")[0]


def test_the_shell_host_list_is_the_engine_host_list_verbatim():
    block = MAIN_RS.split("const HOST_IDS")[1].split("];")[0]
    assert tuple(re.findall(r'"([a-z-]+)"', block)) == hostconnect.HOST_IDS


def test_the_command_spawns_tavotto_hosts_with_json():
    body = _body("host_integration")
    assert 'vec!["hosts".into()]' in body
    assert 'args.push("--json".into())' in body
    assert "run_cli_json(app, args)" in body


def test_action_and_host_are_closed_sets_and_the_folder_is_one_argv_element():
    body = _body("host_integration")
    assert '"list" =>' in body and '"connect" | "disconnect" =>' in body
    assert '_ => return Err("bad_action".into())' in body
    assert "HOST_IDS.contains(&h.as_str())" in body
    assert 'args.push("--project-root".into());\n                args.push(root);' in body
    assert "sh" not in re.findall(r'Command::new\("(\w+)"\)', MAIN_RS)  # 不过 shell
