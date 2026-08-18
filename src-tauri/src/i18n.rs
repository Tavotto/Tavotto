//! 桌面壳自己那点用户可见文案的两套翻译（原生菜单 + 启动失败）。
//!
//! 为什么这些字符串不复用前端的 JSON：原生菜单由 Rust 在 webview 起来**之前**
//! 就建好了（macOS 的菜单栏一直在那儿），那时既没有 i18next 实例也没有页面。
//! 把翻译文件读进来解析属于把整套 i18n 运行时搬进壳里，而这里一共十几条词。
//! 代价写在这儿：**改菜单文案要改两处**——本文件与 `web/src/i18n/locales/`。
//! `tests/test_desktop_i18n.py` 看护两侧对得上：撤销/重做/导出这几条菜单与
//! 界面必须说同一个词，英文表里不许残留中文。
//!
//! 语言从哪儿来（见 `menu_locale`）：
//!   ① 上次前端报上来的那个，落在应用配置目录里的 `menu-locale`；
//!   ② 读不到就用 zh-CN——与前端 `DEFAULT_LOCALE` 同一档。
//! 前端每次 i18n 就绪或用户切语言都会 invoke `set_menu_locale`，Rust 重建菜单
//! 并把新值写回文件，所以「装完第一次打开」之外的每一次启动都是对的。

use std::path::PathBuf;

/// 与 `web/src/i18n/locale.ts` 的 `SUPPORTED_LOCALES` 同一集合。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Locale {
    ZhCn,
    EnUs,
}

pub const DEFAULT_LOCALE: Locale = Locale::ZhCn;

impl Locale {
    pub fn tag(self) -> &'static str {
        match self {
            Locale::ZhCn => "zh-CN",
            Locale::EnUs => "en-US",
        }
    }
}

/// BCP-47 → 支持的语言，规则与 `web/src/i18n/locale.ts` 的 `normalizeLocale`
/// 严格同源：只看主子标签，`zh*` → zh-CN、`en*` → en-US，其余认不出（None）。
pub fn normalize(tag: &str) -> Option<Locale> {
    let lower = tag.trim().to_ascii_lowercase().replace('_', "-");
    let primary = lower.split('-').next().unwrap_or("");
    match primary {
        "zh" => Some(Locale::ZhCn),
        "en" => Some(Locale::EnUs),
        _ => None,
    }
}

/// 壳自己要说的话：原生菜单 + 起不来时那张页面。
/// 界面里的一切文案都在前端，这里只有「前端还没起来 / 根本起不来」的部分。
pub struct ShellText {
    pub app_about: &'static str,
    pub app_hide: &'static str,
    pub app_hide_others: &'static str,
    pub app_quit: &'static str,
    pub file: &'static str,
    pub file_open_project: &'static str,
    pub file_export: &'static str,
    /// 非 macOS 才有的「文件 → 退出」；macOS 上退出在应用菜单里，这条用不上。
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    pub quit: &'static str,
    pub edit: &'static str,
    pub edit_undo: &'static str,
    pub edit_redo: &'static str,
    pub edit_cut: &'static str,
    pub edit_copy: &'static str,
    pub edit_paste: &'static str,
    pub edit_select_all: &'static str,
    pub help: &'static str,
    pub about_comments: &'static str,
    /// 起窗口本身就失败时的那行字（比 sidecar 的报错更早）
    pub window_init_failed: &'static str,

    /* --- sidecar 起不来的几种说法。都会显示在 error.html 的报错框里 --- */
    /// `{path}` = 环境变量指到的路径
    pub sidecar_exe_missing: &'static str,
    pub sidecar_not_found: &'static str,
    pub sidecar_handshake_no_port: &'static str,
    pub sidecar_start_failed: &'static str,
    /// `{status}` = 退出码，`{tail}` = 日志末尾（两者都是诊断信息，不翻译）
    pub sidecar_exited: &'static str,
    pub sidecar_timeout: &'static str,
}

const ZH: ShellText = ShellText {
    app_about: "关于 Magplot",
    app_hide: "隐藏 Magplot",
    app_hide_others: "隐藏其他",
    app_quit: "退出 Magplot",
    file: "文件",
    file_open_project: "打开项目…",
    file_export: "导出…",
    quit: "退出",
    edit: "编辑",
    edit_undo: "撤销",
    edit_redo: "重做",
    edit_cut: "剪切",
    edit_copy: "复制",
    edit_paste: "粘贴",
    edit_select_all: "全选",
    help: "帮助",
    about_comments: "论文 Figure 排版 + 参数化图表编辑",
    window_init_failed: "窗口初始化失败",
    sidecar_exe_missing: "MAGPLOT_SIDECAR_EXE 指向的文件不存在: {path}",
    sidecar_not_found: "找不到 Magplot 渲染服务：安装文件可能不完整，请重新安装",
    sidecar_handshake_no_port: "握手数据缺少端口",
    sidecar_start_failed: "渲染服务启动失败",
    sidecar_exited: "渲染服务提前退出（{status}）。日志末尾：\n{tail}",
    sidecar_timeout: "等待渲染服务就绪超时（60 秒）",
};

const EN: ShellText = ShellText {
    app_about: "About Magplot",
    app_hide: "Hide Magplot",
    app_hide_others: "Hide Others",
    app_quit: "Quit Magplot",
    file: "File",
    file_open_project: "Open Project…",
    file_export: "Export…",
    quit: "Quit",
    edit: "Edit",
    edit_undo: "Undo",
    edit_redo: "Redo",
    edit_cut: "Cut",
    edit_copy: "Copy",
    edit_paste: "Paste",
    edit_select_all: "Select All",
    help: "Help",
    about_comments: "Figure layout and parametric plot editing for papers",
    window_init_failed: "Window initialization failed",
    sidecar_exe_missing: "MAGPLOT_SIDECAR_EXE points at a file that does not exist: {path}",
    sidecar_not_found:
        "Cannot find the Magplot render service — the installation may be incomplete. Please reinstall.",
    sidecar_handshake_no_port: "The handshake data has no port",
    sidecar_start_failed: "The render service failed to start",
    sidecar_exited: "The render service exited early ({status}). End of the log:\n{tail}",
    sidecar_timeout: "Timed out waiting for the render service (60s)",
};

pub fn text(locale: Locale) -> &'static ShellText {
    match locale {
        Locale::ZhCn => &ZH,
        Locale::EnUs => &EN,
    }
}

/// 记住上次语言的小文件。放应用配置目录（与 window-state 插件同一处），
/// **不进项目数据**：它是这台机器上这个人的偏好。
pub fn locale_file(config_dir: Option<PathBuf>) -> Option<PathBuf> {
    config_dir.map(|d| d.join("menu-locale"))
}

pub fn read_locale(path: Option<PathBuf>) -> Locale {
    path.and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| normalize(&s))
        .unwrap_or(DEFAULT_LOCALE)
}

pub fn write_locale(path: Option<PathBuf>, locale: Locale) {
    let Some(path) = path else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // 写不进去只影响下次启动的头一秒（菜单先是默认语言，前端一报就换过来），
    // 不值得打断任何事情。
    let _ = std::fs::write(path, locale.tag());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_matches_the_frontend_rules() {
        for tag in ["zh", "zh-CN", "zh-Hans", "zh-Hans-CN", "zh_TW", " ZH-cn "] {
            assert_eq!(normalize(tag), Some(Locale::ZhCn), "{tag}");
        }
        for tag in ["en", "en-US", "en-GB", "EN_us"] {
            assert_eq!(normalize(tag), Some(Locale::EnUs), "{tag}");
        }
        for tag in ["ja", "fr-FR", "", "   ", "klingon"] {
            assert_eq!(normalize(tag), None, "{tag}");
        }
    }

    #[test]
    fn unknown_or_missing_preference_falls_back_to_zh() {
        assert_eq!(read_locale(None), Locale::ZhCn);
        assert_eq!(DEFAULT_LOCALE, Locale::ZhCn);
    }

    /// 两套文案的字段必须都填了——漏一条的表现是菜单里出现一个空词条。
    #[test]
    fn every_menu_string_is_present_in_both_languages() {
        for t in [&ZH, &EN] {
            for s in [
                t.app_about,
                t.app_hide,
                t.app_hide_others,
                t.app_quit,
                t.file,
                t.file_open_project,
                t.file_export,
                t.quit,
                t.edit,
                t.edit_undo,
                t.edit_redo,
                t.edit_cut,
                t.edit_copy,
                t.edit_paste,
                t.edit_select_all,
                t.help,
                t.about_comments,
                t.window_init_failed,
                t.sidecar_exe_missing,
                t.sidecar_not_found,
                t.sidecar_handshake_no_port,
                t.sidecar_start_failed,
                t.sidecar_exited,
                t.sidecar_timeout,
            ] {
                assert!(!s.trim().is_empty());
            }
        }
    }

    /// 英文菜单里不该残留汉字（漏改一条时最典型的样子）。
    #[test]
    fn english_menu_has_no_cjk() {
        for s in [
            EN.app_about,
            EN.app_hide,
            EN.app_hide_others,
            EN.app_quit,
            EN.file,
            EN.file_open_project,
            EN.file_export,
            EN.quit,
            EN.edit,
            EN.edit_undo,
            EN.edit_redo,
            EN.edit_cut,
            EN.edit_copy,
            EN.edit_paste,
            EN.edit_select_all,
            EN.help,
            EN.about_comments,
            EN.window_init_failed,
            EN.sidecar_exe_missing,
            EN.sidecar_not_found,
            EN.sidecar_handshake_no_port,
            EN.sidecar_start_failed,
            EN.sidecar_exited,
            EN.sidecar_timeout,
        ] {
            assert!(
                !s.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c)),
                "英文菜单里还留着中文：{s}"
            );
        }
    }
}
