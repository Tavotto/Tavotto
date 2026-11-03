//! 「安全启动」：握手超时后的「重新启动」这一次不恢复上次项目（TVD-DT76）。
//!
//! **为什么要有**：sidecar 在写 ready 之前要同步恢复上次项目（扫描、起 watcher……）。上次项目在离线网络盘 /
//! OneDrive 按需下载 / 超大目录上时，这些系统调用可能挂住几分钟，不受扫描预算限制——握手 60 s 超时，
//! 「重新启动」又恢复同一个项目，永远同一个结果。安全启动让那一次启动直接进项目选择页。
//!
//! **为什么是一次性文件而不是内存标志**：救援页的「重新启动」是整个应用重启（`rescue_retry`：收 sidecar →
//! 写 clean → `restart()`），新进程从零开始，内存里什么都带不过去。所以重启前在 `app_config_dir()` 里留一个
//! 一次性标记文件，下一次启动 [`take`] 读到即删。不持久化任何偏好、不碰「最近项目」与 `last_project` 配置。
//!
//! **不误留状态**：标记内容是写入时的 Unix 秒；读到即删，不管用不用得上；超过 [`MAX_AGE_SECS`]（重启没成功、
//! 用户隔了很久才手动打开应用）视为过期、不生效。读写失败一律当「没有标记」——安全启动是兜底，不是启动的前提。
//!
//! stdin 字段名是与 Python 消费方的严格同源对：两侧各读 `tests/golden/desktop_safe_start.json`，不读对方源码。

use std::path::{Path, PathBuf};

/// stdin 首行 JSON 里的字段名（Python 侧 `desktop.SAFE_START_FIELD`）；只发字面 `true`。
pub const HELLO_FIELD: &str = "safe_start";
/// 一次性标记文件名，放在 `app_config_dir()`（与 `desktop-port` 同一处）。
pub const FILE_NAME: &str = "safe-start-once";
/// 标记的有效期：从救援页点下「重新启动」到新进程读到它，正常是几秒。
pub const MAX_AGE_SECS: u64 = 600;

pub fn path(config_dir: Option<&Path>) -> Option<PathBuf> {
    config_dir.map(|d| d.join(FILE_NAME))
}

/// 标记内容 → 写入时刻。不是一个非负整数一律当没有。
pub fn parse(text: &str) -> Option<u64> {
    text.trim().parse().ok()
}

/// 此刻（`now`）读到写入时刻 `written` 的标记是否仍有效。写入时刻在未来（时钟被调回）也算无效。
pub fn fresh(written: u64, now: u64) -> bool {
    now.checked_sub(written)
        .is_some_and(|age| age <= MAX_AGE_SECS)
}

pub fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// 留下标记（重启之前调）。返回是否写成功；失败不阻止重启，只是这一次走普通启动。
pub fn arm(path: Option<&Path>, now: u64) -> bool {
    let Some(path) = path else { return false };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    std::fs::write(path, format!("{now}\n")).is_ok()
}

/// 读并删标记（启动时调）。**无论有效与否都删**——一个标记最多影响一次启动。返回这一次是否安全启动。
pub fn take(path: Option<&Path>, now: u64) -> bool {
    let Some(path) = path else { return false };
    let text = std::fs::read_to_string(path);
    if text.is_ok() || path.exists() {
        let _ = std::fs::remove_file(path);
    }
    text.ok()
        .and_then(|t| parse(&t))
        .is_some_and(|w| fresh(w, now))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir() -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "tavotto-safe-start-{}-{:016x}",
            std::process::id(),
            rand::random::<u64>()
        ));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// 与 Python 消费方（`desktop.parse_safe_start`）的严格同源对：两侧各读这份 golden。
    #[test]
    fn hello_field_is_the_golden_pair() {
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/golden/desktop_safe_start.json"
        ))
        .unwrap();
        assert_eq!(golden["field"], HELLO_FIELD);
        assert_eq!(golden["once_file"], FILE_NAME);
        assert_eq!(golden["once_max_age_seconds"], MAX_AGE_SECS);
        // 壳发出的值（字面 true）在消费方接受的集合里
        assert!(golden["accepted"]
            .as_array()
            .unwrap()
            .contains(&serde_json::json!(true)));
    }

    #[test]
    fn a_marker_is_used_once_and_only_once() {
        let d = dir();
        let p = path(Some(&d)).unwrap();
        assert_eq!(p.file_name().unwrap(), FILE_NAME);
        assert!(!take(Some(&p), 1000), "没有标记 = 普通启动");
        assert!(arm(Some(&p), 1000));
        assert!(take(Some(&p), 1005), "紧接着的这一次安全启动");
        assert!(!p.exists(), "读到即删");
        assert!(!take(Some(&p), 1006), "再下一次是普通启动");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_stale_future_or_corrupt_marker_does_not_apply_but_is_still_removed() {
        let d = dir();
        let p = path(Some(&d)).unwrap();
        for (content, now) in [
            (format!("{}\n", 1000), 1000 + MAX_AGE_SECS + 1), // 过期
            ("5000\n".to_string(), 1000),                     // 写入时刻在未来
            ("not a number".to_string(), 1000),
            (String::new(), 1000),
            ("-3".to_string(), 1000),
        ] {
            std::fs::write(&p, content).unwrap();
            assert!(!take(Some(&p), now));
            assert!(!p.exists(), "无效标记也要清掉，不留到下一次");
        }
        // 边界：刚好 MAX_AGE_SECS 仍有效
        std::fs::write(&p, "1000").unwrap();
        assert!(take(Some(&p), 1000 + MAX_AGE_SECS));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn without_a_config_dir_nothing_is_armed_or_taken() {
        assert_eq!(path(None), None);
        assert!(!arm(None, 1));
        assert!(!take(None, 1));
    }

    #[test]
    fn arming_creates_the_config_dir_and_survives_an_unwritable_one() {
        let d = dir();
        let nested = d.join("a").join("b").join(FILE_NAME);
        assert!(arm(Some(&nested), 7));
        assert!(take(Some(&nested), 8));
        // 目录位置被普通文件占着：写不进去，返回 false，不 panic
        let blocker = d.join("file");
        std::fs::write(&blocker, b"x").unwrap();
        assert!(!arm(Some(&blocker.join(FILE_NAME)), 7));
        let _ = std::fs::remove_dir_all(&d);
    }
}
