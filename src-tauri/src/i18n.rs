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
    /// 起 sidecar 之前那几步的失败（日志目录/日志文件/句柄/spawn/stdin）。
    /// 这些同样会显示在启动失败页上——最需要看懂的时候，不能是另一门语言。
    pub sidecar_log_dir_failed: &'static str,
    pub sidecar_log_open_failed: &'static str,
    pub sidecar_log_clone_failed: &'static str,
    pub sidecar_spawn_failed: &'static str,
    pub sidecar_stdin_missing: &'static str,
    pub sidecar_stdin_write_failed: &'static str,
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
    sidecar_log_dir_failed: "无法创建日志目录 {path}: {err}",
    sidecar_log_open_failed: "无法打开日志文件 {path}: {err}",
    sidecar_log_clone_failed: "日志句柄复制失败: {err}",
    sidecar_spawn_failed: "无法启动渲染服务 {path}: {err}",
    sidecar_stdin_missing: "拿不到 sidecar stdin",
    sidecar_stdin_write_failed: "写入启动凭据失败: {err}",
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
    sidecar_log_dir_failed: "Could not create the log directory {path}: {err}",
    sidecar_log_open_failed: "Could not open the log file {path}: {err}",
    sidecar_log_clone_failed: "Could not duplicate the log handle: {err}",
    sidecar_spawn_failed: "Could not start the render service {path}: {err}",
    sidecar_stdin_missing: "Could not obtain the sidecar's stdin",
    sidecar_stdin_write_failed: "Could not write the startup credentials: {err}",
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

/// 记下来的语言，外加**它是不是用户亲手选的**。
///
/// 这个区分不是洁癖：前端在 i18n 就绪时会把**当前生效**的语言报上来给菜单
/// 用，那一次可能只是「跟随系统」的结果。桌面模式下 sidecar 绑的是
/// `127.0.0.1:0`，端口每次都变，而端口是 Web Storage origin 的一部分——
/// 前端的 `localStorage` 偏好**根本活不过一次重启**，壳记的这份是唯一的存储。
/// 两种来源混在一起的话，我们没法回答「用户到底选过没有」，也就没法把
/// 「手动选择 > 系统语言」这条优先级还给桌面版。
pub struct StoredLocale {
    pub locale: Locale,
    pub explicit: bool,
}

/// 文件格式：第一行是语言标签，第二行有 `explicit` 就表示是用户亲手选的。
/// 0.7.0 写下的单行文件当成「非显式」——那是安全的一侧（顶多退回系统语言）。
pub fn read_stored(path: Option<PathBuf>) -> Option<StoredLocale> {
    let text = path.and_then(|p| std::fs::read_to_string(p).ok())?;
    let mut lines = text.lines();
    let locale = normalize(lines.next().unwrap_or(""))?;
    let explicit = lines.any(|l| l.trim() == "explicit");
    Some(StoredLocale { locale, explicit })
}

pub fn read_locale(path: Option<PathBuf>) -> Locale {
    read_stored(path)
        .map(|s| s.locale)
        .unwrap_or(DEFAULT_LOCALE)
}

pub fn write_locale(path: Option<PathBuf>, locale: Locale, explicit: bool) {
    let Some(path) = path else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let body = if explicit {
        format!("{}\nexplicit\n", locale.tag())
    } else {
        format!("{}\n", locale.tag())
    };
    // 写不进去只影响下次启动的头一秒（菜单先是默认语言，前端一报就换过来），
    // 不值得打断任何事情。
    let _ = std::fs::write(path, body);
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
                t.sidecar_log_dir_failed,
                t.sidecar_log_open_failed,
                t.sidecar_log_clone_failed,
                t.sidecar_spawn_failed,
                t.sidecar_stdin_missing,
                t.sidecar_stdin_write_failed,
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

    /// `sidecar.rs` 里**一句用户可见的中文都不许有**。
    ///
    /// 逐条往 `ShellText` 里加字段这件事很容易漏：#10 把握手与超时那几条翻
    /// 了，紧挨着的日志目录、日志文件、句柄复制、spawn、stdin 五条却还在
    /// 原地 `format!("无法…")` 拼中文——而它们最终都会作为 `msg` 送到那张
    /// **已经翻成英文**的启动失败页上，于是选了英文的用户在最需要看懂的
    /// 时候读到一句中文（杀毒软件拦了可执行文件、日志目录不可写，正是这类）。
    /// 所以这里不数字段，直接扫源码。
    ///
    /// **只扫 `sidecar.rs`**：`Sidecar::start` 的 `Result<_, String>` 是唯一
    /// 一条「错误原文直接显示给用户」的链路。`main.rs` 里 `reveal_export` /
    /// `set_menu_locale` 的 `Err(String)` 全被前端 `catch { return false }`
    /// 吞掉并走回退（`web/src/lib/desktop.ts`），一个字都不会显示——把它们
    /// 也算进来只会逼人为看不见的字符串编两份文案。这条边界哪天变了
    /// （前端开始显示 invoke 的失败原文），这里要跟着扩。
    #[test]
    fn sidecar_errors_are_never_hardcoded_chinese() {
        let src = include_str!("sidecar.rs");
        for (i, line) in src.lines().enumerate() {
            let code = line.trim_start();
            if code.starts_with("//") {
                continue; // 注释里的中文是写给我们自己看的
            }
            let code = code.split("//").next().unwrap_or("");
            assert!(
                !code.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c)),
                "sidecar.rs:{} 有中文字面量，用户可见文案必须收进 i18n.rs：{code}",
                i + 1
            );
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
            EN.sidecar_log_dir_failed,
            EN.sidecar_log_open_failed,
            EN.sidecar_log_clone_failed,
            EN.sidecar_spawn_failed,
            EN.sidecar_stdin_missing,
            EN.sidecar_stdin_write_failed,
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
