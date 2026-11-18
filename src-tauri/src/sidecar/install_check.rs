//! 引擎起来了却没握手（提前退出 / 超时）时，安装目录里的文件还在不在、能不能读（TVD-MHQJ）。
//!
//! Windows Lab 实测（0.21.2）：杀软隔离或锁住 `_internal` 里一个 `.pyd`，PyInstaller 在我们的入口之前就导入失败，
//! 日志里没有任何启动阶段标记；[`super::probe_install_files`] 的三个布尔值照样全是真。所以构建时在 sidecar 目录
//! 写一份文件清单（[`MANIFEST_NAME`]），失败后逐项核对：
//! - 有文件不在 → [`MISSING`]（被隔离 / 安装不完整）；
//! - 都在但有打不开读的 → [`UNREADABLE`]（被锁 / 被拒绝访问）；
//! - 都在都能读 → [`INTACT`]；
//! - 清单不在、写坏了、核对超时 → [`UNKNOWN`]。
//!
//! 只核在不在、能不能打开读一个字节，不核大小：签名会改 exe / dll 的字节。只回闭集值，不带路径。
//! 清单格式是与构建脚本的严格同源对：两侧各读 `tests/golden/desktop_install_manifest.json`。

use std::io::Read;
use std::path::{Component, Path};
use std::time::{Duration, Instant};

pub const MANIFEST_NAME: &str = "install-files.txt";
pub const HEADER: &str = "# tavotto-install-files v1";
pub const INTACT: &str = "intact";
pub const MISSING: &str = "missing";
pub const UNREADABLE: &str = "unreadable";
pub const UNKNOWN: &str = "unknown";
/// 报告里允许出现的值。
pub const CHECKS: &[&str] = &[INTACT, MISSING, UNREADABLE, UNKNOWN];
/// 核对的时间上限：Lab 上 5167 个文件全打开读一遍 1.15 s（Defender 开）；超了就不下结论。
pub const BUDGET: Duration = Duration::from_secs(10);

pub fn is_known(value: &str) -> bool {
    CHECKS.contains(&value)
}

/// 清单里的一行是不是一个老老实实的相对路径（不许绝对路径、`..`、盘符、反斜杠）。
fn plain_relative(line: &str) -> bool {
    !line.is_empty()
        && !line.contains('\\')
        && !line.contains(':')
        && Path::new(line)
            .components()
            .all(|c| matches!(c, Component::Normal(_)))
}

/// 核对 `dir`（sidecar 目录）。`budget` 是总时间上限。
pub fn check(dir: &Path, budget: Duration) -> &'static str {
    let started = Instant::now();
    let Ok(text) = std::fs::read_to_string(dir.join(MANIFEST_NAME)) else {
        return UNKNOWN;
    };
    let mut lines = text.lines();
    if lines.next().map(str::trim_end) != Some(HEADER) {
        return UNKNOWN;
    }
    let mut entries = Vec::new();
    for line in lines {
        let line = line.trim_end();
        if line.is_empty() {
            continue;
        }
        if !plain_relative(line) {
            return UNKNOWN;
        }
        entries.push(dir.join(line));
    }
    if entries.is_empty() {
        return UNKNOWN;
    }
    // 先只看在不在（便宜）：有缺就是 missing，不必再逐个打开
    let mut denied = false;
    for p in &entries {
        if started.elapsed() > budget {
            return UNKNOWN;
        }
        match std::fs::metadata(p) {
            Ok(m) if m.is_file() => {}
            Ok(_) => return MISSING,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return MISSING,
            Err(_) => denied = true,
        }
    }
    if denied {
        return UNREADABLE;
    }
    let mut buf = [0u8; 1];
    for p in &entries {
        if started.elapsed() > budget {
            return UNKNOWN;
        }
        let readable = std::fs::File::open(p)
            .and_then(|mut f| f.read(&mut buf))
            .is_ok();
        if !readable {
            return UNREADABLE;
        }
    }
    INTACT
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "tavotto-instcheck-{tag}-{}-{:08x}",
            std::process::id(),
            rand::random::<u32>()
        ));
        std::fs::create_dir_all(d.join("_internal").join("pkg")).unwrap();
        d
    }

    fn install(dir: &Path, files: &[&str]) {
        let mut text = format!("{HEADER}\n");
        for f in files {
            std::fs::write(dir.join(f), b"x").unwrap();
            text.push_str(f);
            text.push('\n');
        }
        std::fs::write(dir.join(MANIFEST_NAME), text).unwrap();
    }

    const FILES: &[&str] = &["Tavotto.exe", "_internal/a.dll", "_internal/pkg/_b.pyd"];

    #[test]
    fn manifest_is_the_golden_pair() {
        let g: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/golden/desktop_install_manifest.json"
        ))
        .unwrap();
        assert_eq!(g["file_name"], MANIFEST_NAME);
        assert_eq!(g["header"], HEADER);
        let want: Vec<&str> = g["checks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(CHECKS, want.as_slice());
    }

    #[test]
    fn a_complete_install_is_intact() {
        let d = scratch("ok");
        install(&d, FILES);
        assert_eq!(check(&d, BUDGET), INTACT);
    }

    #[test]
    fn a_quarantined_file_is_missing() {
        let d = scratch("gone");
        install(&d, FILES);
        std::fs::remove_file(d.join("_internal/pkg/_b.pyd")).unwrap();
        assert_eq!(check(&d, BUDGET), MISSING);
    }

    #[test]
    fn a_directory_where_a_file_should_be_is_missing() {
        let d = scratch("dir");
        install(&d, FILES);
        std::fs::remove_file(d.join("_internal/a.dll")).unwrap();
        std::fs::create_dir(d.join("_internal/a.dll")).unwrap();
        assert_eq!(check(&d, BUDGET), MISSING);
    }

    #[cfg(unix)]
    #[test]
    fn a_locked_file_is_unreadable() {
        use std::os::unix::fs::PermissionsExt;
        let d = scratch("locked");
        install(&d, FILES);
        let p = d.join("_internal/a.dll");
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o000)).unwrap();
        // root 不受权限位约束：那种环境下这条判据测不了
        if std::fs::File::open(&p).is_ok() {
            return;
        }
        assert_eq!(check(&d, BUDGET), UNREADABLE);
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o644)).unwrap();
    }

    #[test]
    fn missing_wins_over_unreadable_and_no_manifest_is_unknown() {
        let d = scratch("nomani");
        assert_eq!(check(&d, BUDGET), UNKNOWN);
        std::fs::write(d.join(MANIFEST_NAME), "not the header\nTavotto.exe\n").unwrap();
        assert_eq!(check(&d, BUDGET), UNKNOWN, "wrong header");
        std::fs::write(d.join(MANIFEST_NAME), format!("{HEADER}\n")).unwrap();
        assert_eq!(check(&d, BUDGET), UNKNOWN, "empty list");
    }

    #[test]
    fn paths_that_leave_the_sidecar_dir_make_the_manifest_unknown() {
        for bad in ["../x", "/etc/passwd", "C:/x", "a\\b", "a/../../b"] {
            let d = scratch("bad");
            std::fs::write(d.join(MANIFEST_NAME), format!("{HEADER}\n{bad}\n")).unwrap();
            assert_eq!(check(&d, BUDGET), UNKNOWN, "{bad}");
        }
    }

    #[test]
    fn running_out_of_budget_is_unknown() {
        let d = scratch("slow");
        install(&d, FILES);
        assert_eq!(check(&d, Duration::ZERO), UNKNOWN);
    }
}
