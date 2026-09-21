//! 同源对：`worker::EXIT_GRACE` ↔ `engine/pool.EXIT_GRACE`（管道 EOF 后等子进程自己退出的宽限）。
//!
//! 两侧各自钉在**同一份** `tests/golden/exit_grace_ms.txt` 上（Python 侧是
//! `tests/test_worker_exit_report.py::test_the_exit_grace_is_one_number_on_both_control_planes`）：
//! 改哪一边而不改这份文件，那一边自己的测试就红。以前 Python 侧是拿正则扫 worker.rs 的
//! 源码找那条 `pub const`，而没有 Rust 解析器就分不清注释 / 普通字符串 / 原始字符串 /
//! 字符字面量（评审 #443 第四、十三轮）——现在两边都不读对方的源码。

use std::path::PathBuf;

use tavotto_workerd::worker::EXIT_GRACE;

fn golden_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("tests")
        .join("golden")
        .join("exit_grace_ms.txt")
}

#[test]
fn exit_grace_matches_the_golden_number_shared_with_the_python_pool() {
    let path = golden_path();
    let text =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("读不到 {}: {e}", path.display()));
    let golden: u128 = text
        .trim()
        .parse()
        .unwrap_or_else(|e| panic!("{} 不是一个毫秒数: {e}", path.display()));
    assert_eq!(
        EXIT_GRACE.as_millis(),
        golden,
        "worker.rs 的 EXIT_GRACE 与 tests/golden/exit_grace_ms.txt 不一致：两侧要一起改"
    );
}
