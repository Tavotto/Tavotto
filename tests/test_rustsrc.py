"""`tests/support/rustsrc.py` 自己的反证：注释里、无关字符串里的命令名不许算登记。

这几条是那几道「三处登记」门禁（`test_desktop_*`）的尺子本身——尺子恒真，它们就一起空了。
"""

from __future__ import annotations

import pytest

from tests.support.rustsrc import handler_commands, manifest_commands, rust_code, tauri_commands

BUILD = """
fn main() {
    let _hint = "arm_close_guard"; // 无关字符串里的名字
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "reveal_export",
            // "arm_close_guard",
            /* "native_file_drop", /* 嵌套 */ */
            "a//b",
        ]),
    ))
    .expect("failed");
}
"""

MAIN = """
#[tauri::command]
fn reveal_export() {}
// #[tauri::command]
// fn ghost() {}
fn helper() { let _ = "generate_handler![ghost]"; }
fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            reveal_export,
            // arm_close_guard,
            crate::shell::native_file_drop
        ]);
}
"""


def test_manifest_reads_the_array_not_comments_or_other_strings():
    assert manifest_commands(BUILD) == ["reveal_export", "a//b"]


def test_handler_reads_the_macro_entries_not_comments():
    assert handler_commands(MAIN) == ["reveal_export", "native_file_drop"]


def test_commented_out_commands_are_not_declared():
    assert tauri_commands(MAIN) == ["reveal_export"]


def test_unreadable_entries_fail_instead_of_guessing():
    with pytest.raises(AssertionError, match="字符串以外"):
        manifest_commands('fn main() { x.commands(&[NAMES, "a"]); }')
    with pytest.raises(AssertionError, match="恰好一处"):
        manifest_commands("fn main() {}")


def test_blanking_keeps_offsets():
    src = 'let a = "x // y"; // c\nlet b = 1;'
    assert len(rust_code(src)) == len(src)
    assert rust_code(src, keep_strings=True).startswith('let a = "x // y";')
    assert "c" not in rust_code(src, keep_strings=True).split("\n")[0][18:]
