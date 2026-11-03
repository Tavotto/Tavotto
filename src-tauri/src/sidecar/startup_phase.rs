//! 握手超时时 sidecar 卡在哪一步（TVD-DT76）：从 `sidecar.log` **本次新增**的部分里取最后一个启动阶段标记。
//!
//! sidecar（`src/tavotto/engine/startupphase.py`）在启动的几个关键点往 fd 2 写独占一行的
//! `TAVOTTO_STARTUP_PHASE=<阶段>`，fd 2 就是壳重定向的 `sidecar.log`（追加写）。壳在 spawn 之前记下日志当时的长度，
//! 超时后只读那之后的字节（上限 [`TAIL_MAX`]，取末尾）：
//! - 找到合法标记 → 最后一个（启动是单调往前走的，最后一个就是卡住的那一步）；
//! - 一个都没有 → [`NONE`]：解释器根本没跑起来（多半是杀软在扫 / 拦），或 fd 2 写不进去。
//!
//! **只认完整的一行、且阶段名在闭集 [`PHASES`] 里**：日志里别的任何文本（含路径、用户内容）都不会流进报告。
//! 闭集与标记格式是与 Python 一侧的严格同源对：两侧各读 `tests/golden/desktop_startup_phase.json`。

use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

pub const MARKER_PREFIX: &str = "TAVOTTO_STARTUP_PHASE=";
/// 按启动先后排列（Python 侧 `startupphase.PHASES`）。
pub const PHASES: &[&str] = &[
    "interpreter",
    "imports_done",
    "housekeeping",
    "pdf_backend",
    "project_open",
    "serving",
];
/// 本次新增部分里没有任何合法标记。
pub const NONE: &str = "none";
/// 只读新增部分的末尾这么多字节。
pub const TAIL_MAX: u64 = 64 * 1024;

/// 报告里允许出现的值：[`PHASES`] 加 [`NONE`]。
pub fn is_known(value: &str) -> bool {
    value == NONE || PHASES.contains(&value)
}

/// 日志文本 → 最后一个合法阶段；没有则 [`NONE`]。
pub fn last_phase(text: &str) -> &'static str {
    let mut found = NONE;
    for line in text.lines() {
        if let Some(name) = line.trim_end().strip_prefix(MARKER_PREFIX) {
            if let Some(p) = PHASES.iter().find(|p| **p == name) {
                found = p;
            }
        }
    }
    found
}

/// 日志当前长度：spawn 之前取，作为「本次新增」的起点。拿不到按 0（读整个文件的末尾）。
pub fn log_len(path: &Path) -> u64 {
    std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

/// 读 `path` 里 `start` 之后新增的部分（末尾至多 [`TAIL_MAX`] 字节），取最后一个合法阶段。
/// 文件比 `start` 还短（被截断 / 轮转）→ 整个文件都是新的，从头算。读不了 → [`NONE`]。
pub fn from_log(path: &Path, start: u64) -> &'static str {
    let Ok(mut f) = std::fs::File::open(path) else {
        return NONE;
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let start = if len < start { 0 } else { start };
    let from = start.max(len.saturating_sub(TAIL_MAX));
    if f.seek(SeekFrom::Start(from)).is_err() {
        return NONE;
    }
    let mut bytes = Vec::new();
    if f.take(TAIL_MAX).read_to_end(&mut bytes).is_err() {
        return NONE;
    }
    last_phase(&String::from_utf8_lossy(&bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn marker(p: &str) -> String {
        format!("{MARKER_PREFIX}{p}\n")
    }

    fn tmp(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "tavotto-phase-{tag}-{}-{:08x}",
            std::process::id(),
            rand::random::<u32>()
        ));
        std::fs::create_dir_all(&d).unwrap();
        d.join("sidecar.log")
    }

    #[test]
    fn phases_are_the_golden_pair() {
        let g: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/golden/desktop_startup_phase.json"
        ))
        .unwrap();
        assert_eq!(g["marker_prefix"], MARKER_PREFIX);
        assert_eq!(g["none_value"], NONE);
        assert_eq!(g["tail_max_bytes"], TAIL_MAX);
        let want: Vec<&str> = g["phases"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(PHASES, want.as_slice());
        assert!(!PHASES.contains(&NONE));
        assert!(PHASES
            .iter()
            .all(|p| p.chars().all(|c| c.is_ascii_lowercase() || c == '_')));
    }

    #[test]
    fn no_marker_is_none_and_the_last_valid_marker_wins() {
        assert_eq!(last_phase(""), NONE);
        assert_eq!(last_phase("some traceback\nmore\n"), NONE);
        let text = format!(
            "{}noise\n{}{}",
            marker("interpreter"),
            marker("housekeeping"),
            marker("pdf_backend")
        );
        assert_eq!(last_phase(&text), "pdf_backend");
    }

    #[test]
    fn invalid_or_partial_markers_are_ignored() {
        // 非法阶段名不覆盖前一个合法的；不是独占一行的不算；带路径 / 多余文本的不算
        let text = format!(
            "{}{}x {}{}{}path=/home/u\n{}",
            marker("imports_done"),
            marker("bogus"),
            marker("serving").trim_end(),
            "TAVOTTO_STARTUP_PHASE=serving extra\n",
            "TAVOTTO_STARTUP_PHASE=\n",
            "XTAVOTTO_STARTUP_PHASE=project_open\n"
        );
        assert_eq!(last_phase(&text), "imports_done");
        // CRLF 行尾（Windows 文本）照认
        assert_eq!(last_phase("TAVOTTO_STARTUP_PHASE=serving\r\n"), "serving");
        assert!(is_known("none") && is_known("serving") && !is_known("bogus") && !is_known(""));
    }

    #[test]
    fn only_what_was_appended_after_the_start_offset_counts() {
        let p = tmp("append");
        std::fs::write(&p, marker("serving")).unwrap(); // 上一次启动留下的
        let start = log_len(&p);
        assert_eq!(from_log(&p, start), NONE, "本次一个标记都没有");
        let mut old = std::fs::read(&p).unwrap();
        old.extend_from_slice(marker("imports_done").as_bytes());
        std::fs::write(&p, old).unwrap();
        assert_eq!(from_log(&p, start), "imports_done", "上一次的 serving 不算");
        assert_eq!(from_log(&p, 0), "imports_done", "从头读时仍取最后一个");
    }

    #[test]
    fn a_truncated_log_is_read_from_the_top_and_a_missing_one_is_none() {
        let p = tmp("trunc");
        std::fs::write(&p, marker("housekeeping")).unwrap();
        assert_eq!(from_log(&p, 10_000), "housekeeping");
        assert_eq!(from_log(&p.with_file_name("nope.log"), 0), NONE);
        assert_eq!(log_len(&p.with_file_name("nope.log")), 0);
    }

    #[test]
    fn only_the_tail_of_a_huge_new_part_is_read() {
        let p = tmp("huge");
        let mut data = marker("interpreter").into_bytes();
        data.extend(std::iter::repeat_n(b'x', (TAIL_MAX as usize) * 2));
        data.push(b'\n');
        std::fs::write(&p, &data).unwrap();
        assert_eq!(from_log(&p, 0), NONE, "标记在上限之外，读不到");
        data.extend_from_slice(marker("project_open").as_bytes());
        std::fs::write(&p, &data).unwrap();
        assert_eq!(from_log(&p, 0), "project_open");
    }

    #[test]
    fn non_utf8_noise_does_not_hide_a_marker() {
        let p = tmp("bytes");
        let mut data = vec![0xff, 0xfe, b'\n'];
        data.extend_from_slice(marker("pdf_backend").as_bytes());
        std::fs::write(&p, data).unwrap();
        assert_eq!(from_log(&p, 0), "pdf_backend");
    }
}
