"""桌面壳的关窗询问闸（issue #223）：两侧同源 + 兜底不许被摘掉。

为什么这几条要用 Python 测（而不是留给任一侧的单测）：

* **同源对站在两侧之外。** 壳发 `tavotto:close-requested`、前端听同一个名字；
  答复的词汇表（`hold`/`close`/`cancel`）壳认一份、前端写一份。名字或词汇漂了，
  Rust 的 `cargo test` 与前端的 vitest **各自都是绿的**——坏法只在真机上现形，
  表现是「点关闭按钮之后什么都不发生，两秒后窗口自己关了」。
* **`#[tauri::command]` 的 ACL 漏配是静默失败**（`src-tauri/AGENTS.md`：三处
  少一处 invoke 被直接拒）。这里对本闸的两个命令各查三处，与
  `test_desktop_i18n.py::test_set_menu_locale_is_declared_in_all_three_places`
  同一形状。（PR #255 用**枚举** `main.rs` 里所有命令的方式取代这类白名单；
  它落地之后本文件这两条就是冗余的，删掉即可。）
* **看门狗是这条路上唯一的兜底。** 拦下窗口却没人应答时若不强关，用户得到的
  是一个**关不掉的窗口**——那比「关窗不提示」坏得多，他只能去杀进程，而杀进程
  连防抖窗口内的最后一次编辑都保不住。所以「拦」与「起看门狗」必须同时在。

`src-tauri/` 不进 wheel/sdist，因此整个文件在没有它的树上跳过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
MAIN_RS = TAURI / "src" / "main.rs"
DESKTOP_TS = ROOT / "web" / "src" / "lib" / "desktop.ts"
CLOSE_GUARD_TS = ROOT / "web" / "src" / "lib" / "closeGuard.ts"

pytestmark = pytest.mark.skipif(
    not MAIN_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)

#: 壳 → 前端的关窗询问事件。两侧各自写死，这里是它们唯一的比对处。
CLOSE_EVENT = "tavotto:close-requested"

#: 本闸新增的命令。ACL 三处齐全才 invoke 得动。
CLOSE_COMMANDS = ("arm_close_guard", "resolve_close_request")


def _main_rs() -> str:
    return MAIN_RS.read_text(encoding="utf-8")


def _rust_fn_body(src: str, signature: str) -> str:
    """抠出一个自由函数的函数体，并剥掉行注释。

    剥注释是必须的：这个文件里的说明性注释会提到 `prevent_close`、
    `spawn_close_watchdog` 这些名字，不剥的话判据匹配到的是散文，
    把实现整个删掉它照样绿。
    """
    start = src.index(signature)
    body = src[start:]
    body = body[: body.index("\n}\n")]
    return "\n".join(line.split("//")[0] for line in body.splitlines())


def test_the_close_event_name_is_one_string_on_both_sides():
    """壳发的与前端听的必须逐字相同，且各自只出现一次。"""
    rs = _main_rs()
    ts = DESKTOP_TS.read_text(encoding="utf-8")
    assert rs.count(f'"{CLOSE_EVENT}"') == 1, "壳里这个事件名不是恰好一处"
    assert ts.count(f"'{CLOSE_EVENT}'") == 1, "前端里这个事件名不是恰好一处"
    assert f'emit_to("main", "{CLOSE_EVENT}"' in rs, "壳没有把它发给主窗口"
    assert f"listen('{CLOSE_EVENT}'" in ts, "前端没有订阅它"


def test_the_decision_vocabulary_is_the_same_closed_set_on_both_sides():
    """`hold` / `close` / `cancel` 三个词，壳认一份、前端写一份。

    前端多写一个壳不认的词，壳会把它当错误退回去——而 `resolveDesktopCloseRequest`
    吞掉异常返回 false，于是那次答复**静默丢失**，窗口挂到看门狗超时。
    """
    rs = _main_rs()
    impl = rs[rs.index("impl CloseDecision {") :]
    impl = impl[: impl.index("\n}\n")]
    rust_words = set(re.findall(r'"(\w+)" => Some\(', impl))

    ts = DESKTOP_TS.read_text(encoding="utf-8")
    union = re.search(r"export type CloseDecision =([^\n]+)", ts)
    assert union, "前端没有 CloseDecision 联合类型"
    ts_words = set(re.findall(r"'(\w+)'", union.group(1)))

    assert rust_words == ts_words == {"hold", "close", "cancel"}, (
        f"关窗答复的词汇表两侧不一致：rust={sorted(rust_words)} ts={sorted(ts_words)}"
    )


@pytest.mark.parametrize("command", CLOSE_COMMANDS)
def test_the_close_guard_commands_are_declared_in_all_three_places(command: str):
    build_rs = (TAURI / "build.rs").read_text(encoding="utf-8")
    cap = json.loads((TAURI / "capabilities" / "main.json").read_text(encoding="utf-8"))
    handler = _main_rs().split("generate_handler![")[1].split("]")[0]

    assert f'"{command}"' in build_rs, "build.rs 的 AppManifest::commands 里没有它"
    assert f"allow-{command.replace('_', '-')}" in cap["permissions"], "capability 没放行它"
    assert command in handler, "generate_handler 里没有它"


def test_holding_the_window_always_arms_the_watchdog():
    """拦一次窗口就必须起一条看门狗——两件事在同一个函数里，不许只留前一半。

    只留 `prevent_close()` 的表现是：webview 卡死时窗口**关不掉**。那不是
    「保护得更严」，那是把用户逼去杀进程。
    """
    body = _rust_fn_body(
        _main_rs(),
        "fn on_close_requested(window: &tauri::Window, api: &tauri::CloseRequestApi)",
    )
    assert "api.prevent_close()" in body, "这个函数已经不拦窗口了？判据的前提变了，先读它"
    assert "spawn_close_watchdog(" in body, "拦下了窗口却没起看门狗：这是一个关不掉的窗口"


def test_the_close_guard_reuses_the_one_unsaved_predicate():
    """「有没有未落盘的工作」全产品只有一份判据（ADR 0024 的关闭保护）。

    在这里另写一份的表现是**刷新会拦、关窗不拦**（或者反过来），而两侧的单测
    各自都绿——因为它们各自问的是自己那一份。
    """
    src = CLOSE_GUARD_TS.read_text(encoding="utf-8")
    assert re.search(
        r"import \{[^}]*\bhasUnsavedWork\b[^}]*\} from '@/store/documentStore'", src
    ), "closeGuard.ts 没有从 documentStore 取那份唯一的判据"
