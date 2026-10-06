//! beta 分支专用（不合进 main）：Tavotto Beta 的配置 / 数据目录与正式版分开。
//!
//! 后端的落点只认 `TAVOTTO_CONFIG_DIR` / `TAVOTTO_DATA_DIR`，没设时按平台取
//! `.../Tavotto`（`src/tavotto/engine/config.py` 的 `config_dir()` / `data_dir()`）。
//! beta 壳在没设时把两者指到同一平台位置的同级目录 `Tavotto Beta`，测试就不会改动正式版的
//! config.json、最近项目与数据目录，也能在干净状态下测首跑。用户已显式设置的值原样尊重；
//! 空串与 Python 侧一样当作没设。
//!
//! 平台规则逐条照 config.py（改一边要同改另一边）：
//! - macOS：两者都是 `~/Library/Application Support/Tavotto Beta`
//! - Windows：配置 `%APPDATA%\Tavotto Beta`；数据 `%LOCALAPPDATA%`（缺省 `%APPDATA%`）`\Tavotto Beta`，再缺省用户主目录
//! - 其它：`$XDG_CONFIG_HOME`（缺省 `~/.config`）/ `$XDG_DATA_HOME`（缺省 `~/.local/share`）下的 `tavotto-beta`

use std::path::PathBuf;

/// 同级目录名（macOS / Windows）。
pub const DIR_NAME: &str = "Tavotto Beta";
/// 同级目录名（Linux 等，照正式版的小写 `tavotto`）。
pub const XDG_DIR_NAME: &str = "tavotto-beta";

/// 要给 sidecar 补上的环境变量。`os` 取 `std::env::consts::OS` 的值，`get` 读环境变量，
/// `home` 是用户主目录（拿不到就不补，交回后端自己的缺省——宁可不隔离也不指到一个相对路径）。
pub fn overrides(
    os: &str,
    get: impl Fn(&str) -> Option<String>,
    home: Option<PathBuf>,
) -> Vec<(&'static str, PathBuf)> {
    let set = |k: &str| get(k).filter(|v| !v.is_empty());
    let mut out = Vec::new();
    let (config, data): (Option<PathBuf>, Option<PathBuf>) = match os {
        "macos" => {
            let d = home.map(|h| h.join("Library").join("Application Support").join(DIR_NAME));
            (d.clone(), d)
        }
        "windows" => {
            let roaming = set("APPDATA").map(PathBuf::from);
            let local = set("LOCALAPPDATA")
                .map(PathBuf::from)
                .or_else(|| roaming.clone());
            (
                roaming.or_else(|| home.clone()).map(|b| b.join(DIR_NAME)),
                local.or(home).map(|b| b.join(DIR_NAME)),
            )
        }
        _ => {
            let cfg = set("XDG_CONFIG_HOME")
                .map(PathBuf::from)
                .or_else(|| home.as_ref().map(|h| h.join(".config")));
            let dat = set("XDG_DATA_HOME")
                .map(PathBuf::from)
                .or_else(|| home.as_ref().map(|h| h.join(".local").join("share")));
            (
                cfg.map(|b| b.join(XDG_DIR_NAME)),
                dat.map(|b| b.join(XDG_DIR_NAME)),
            )
        }
    };
    if set("TAVOTTO_CONFIG_DIR").is_none() {
        if let Some(p) = config {
            out.push(("TAVOTTO_CONFIG_DIR", p));
        }
    }
    if set("TAVOTTO_DATA_DIR").is_none() {
        if let Some(p) = data {
            out.push(("TAVOTTO_DATA_DIR", p));
        }
    }
    out
}

/// 当前进程的环境与主目录（`Path.home()` 同样先看 HOME / USERPROFILE）。
pub fn current() -> Vec<(&'static str, PathBuf)> {
    let get = |k: &str| std::env::var(k).ok();
    let home = get(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(std::env::home_dir);
    overrides(std::env::consts::OS, get, home)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn run(os: &str, env: &[(&str, &str)], home: Option<&str>) -> HashMap<&'static str, PathBuf> {
        let env: HashMap<String, String> = env
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        overrides(os, |k| env.get(k).cloned(), home.map(PathBuf::from))
            .into_iter()
            .collect()
    }

    #[test]
    fn macos_puts_both_next_to_the_release_folder() {
        let got = run("macos", &[], Some("/Users/u"));
        let want = PathBuf::from("/Users/u/Library/Application Support/Tavotto Beta");
        assert_eq!(got["TAVOTTO_CONFIG_DIR"], want);
        assert_eq!(got["TAVOTTO_DATA_DIR"], want);
    }

    #[test]
    fn windows_follows_appdata_and_localappdata() {
        let got = run(
            "windows",
            &[
                ("APPDATA", r"C:\U\AppData\Roaming"),
                ("LOCALAPPDATA", r"C:\U\AppData\Local"),
            ],
            Some(r"C:\U"),
        );
        assert_eq!(
            got["TAVOTTO_CONFIG_DIR"],
            PathBuf::from(r"C:\U\AppData\Roaming").join(DIR_NAME)
        );
        assert_eq!(
            got["TAVOTTO_DATA_DIR"],
            PathBuf::from(r"C:\U\AppData\Local").join(DIR_NAME)
        );
    }

    #[test]
    fn windows_data_falls_back_to_appdata_like_the_backend() {
        let got = run(
            "windows",
            &[("APPDATA", r"C:\R"), ("LOCALAPPDATA", "")],
            Some(r"C:\U"),
        );
        assert_eq!(
            got["TAVOTTO_DATA_DIR"],
            PathBuf::from(r"C:\R").join(DIR_NAME)
        );
    }

    #[test]
    fn linux_uses_xdg_or_home_defaults() {
        let got = run("linux", &[("XDG_DATA_HOME", "/d")], Some("/home/u"));
        assert_eq!(
            got["TAVOTTO_CONFIG_DIR"],
            PathBuf::from("/home/u/.config/tavotto-beta")
        );
        assert_eq!(got["TAVOTTO_DATA_DIR"], PathBuf::from("/d/tavotto-beta"));
    }

    #[test]
    fn explicit_user_values_are_left_alone_and_empty_counts_as_unset() {
        let got = run(
            "macos",
            &[("TAVOTTO_CONFIG_DIR", "/mine"), ("TAVOTTO_DATA_DIR", "")],
            Some("/Users/u"),
        );
        assert!(!got.contains_key("TAVOTTO_CONFIG_DIR"));
        assert!(got["TAVOTTO_DATA_DIR"].ends_with(DIR_NAME));
    }

    #[test]
    fn no_home_means_no_override_rather_than_a_relative_path() {
        assert!(run("macos", &[], None).is_empty());
    }
}
