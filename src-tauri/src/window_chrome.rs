//! 主窗口的窗框：系统标题栏 / 菜单栏怎么和页面顶栏融成一片（2026-10-13）。
//!
//! - **macOS**：系统标题栏透明地叠在页面上、不写标题（`TitleBarStyle::Overlay` +
//!   `hidden_title`），红绿灯挪进页面顶栏那一行的垂直居中处；页面给它们让出左侧
//!   `TRAFFIC_LIGHTS_INSET`，并把顶栏的空白处标成拖动区（`data-tauri-drag-region`）。
//! - **Windows**：标题栏保留系统的（贴边吸附 / 最大化布局照旧），但菜单栏不再独占一行——
//!   菜单**仍挂在窗口上、只是藏起来**（`hide_menu`）：Tauri 的消息钩子按已登记的菜单
//!   翻译加速键，与可见与否无关，所以 Ctrl+S / Ctrl+O 等照旧先于 webview 截获。页面顶栏的
//!   「菜单」按钮经 `show_app_menu` 在按钮下方弹出同一份菜单；标题栏底色与文字色由页面按
//!   当前主题经 `set_titlebar_colors` 报上来（DWM，Windows 11 起生效，Windows 10 维持系统色）。
//!
//! 页面据 `MARKER_SCRIPT` 注入的全局名知道自己处在哪一种窗框里。**全局名与两个取值与
//! `web/src/lib/desktop.ts` 的 `windowChrome()` 严格同源**（`tests/test_desktop_window_chrome.py`
//! 比两侧）。远程实例窗口（ADR 0105）不注入：它加载的是服务器上那一版前端，未必认得
//! 这个标记，所以那个窗口维持系统标题栏 + 可见菜单栏。

/// 页面读的全局名。
pub const MARKER_GLOBAL: &str = "__TAVOTTO_WINDOW_CHROME__";

/// macOS：系统标题栏叠在页面上，红绿灯在页面顶栏里。
pub const CHROME_OVERLAY: &str = "overlay";
/// Windows：菜单栏藏起来，页面顶栏给一个「菜单」按钮。
pub const CHROME_MENU_BUTTON: &str = "menu-button";

/// 主窗口进 / 出全屏时壳发给页面的事件，载荷 `{fullscreen: bool}`。**事件名与
/// `web/src/lib/desktop.ts` 的 `FULLSCREEN_EVENT` 严格同源**（`tests/test_desktop_window_chrome.py`）。
/// 只有 macOS 发：全屏时红绿灯随系统标题栏一起收起，页面顶栏左侧让给它们的那段就不该再留着。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub const FULLSCREEN_EVENT: &str = "tavotto:fullscreen";

/// 记住上一次告诉页面的全屏状态：`Resized` 一天到晚在发（拖边框、缩放），只有真变了才通知。
#[derive(Debug, Default)]
pub struct FullscreenTracker(std::sync::atomic::AtomicBool);

impl FullscreenTracker {
    /// 记下当前状态；与上次不同返回 `true`（该发事件了）。
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    pub fn update(&self, now: bool) -> bool {
        self.0.swap(now, std::sync::atomic::Ordering::SeqCst) != now
    }

    /// 页面此刻该当作的全屏状态（**按阶段算的**，不是 tao 的切换目标）：原生观察者在位时只有
    /// did-enter 才会把它置 true，进入动画期间仍是 false。
    pub fn get(&self) -> bool {
        self.0.load(std::sync::atomic::Ordering::SeqCst)
    }
}

/// `NSWindow` 的全屏通知（`fullscreen_observer.rs`）。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FullscreenPhase {
    WillEnter,
    DidEnter,
    WillExit,
    DidExit,
}

/// 每条通知该告诉页面什么。判据：**红绿灯在屏上时，绝不收回让位的那段**——
/// 进入动画期间红绿灯还在（不发，等 did-enter 再收）；退出一开始红绿灯就要回来（立刻恢复）。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub const fn phase_verdict(phase: FullscreenPhase) -> Option<bool> {
    match phase {
        FullscreenPhase::WillEnter => None,
        FullscreenPhase::DidEnter => Some(true),
        FullscreenPhase::WillExit | FullscreenPhase::DidExit => Some(false),
    }
}

/// `Resized` + `is_fullscreen()` 这条兜底能不能报这个状态。原生观察者在位时只许报 `false`：
/// tao 退出动画期间仍说「在全屏」，让它报 `true` 会把 will-exit 发出的 `false` 顶回去。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub const fn resize_may_report(observer_live: bool, fullscreen: bool) -> bool {
    !observer_live || !fullscreen
}

/// 这个平台主窗口的窗框形态。
pub const fn chrome() -> &'static str {
    if cfg!(target_os = "macos") {
        CHROME_OVERLAY
    } else {
        CHROME_MENU_BUTTON
    }
}

/// 注进主窗口每一次导航（壳自带页面与 sidecar 页面）的标记脚本。
pub fn marker_script() -> String {
    format!("window.{MARKER_GLOBAL} = '{}';", chrome())
}

/// 红绿灯（关闭钮）在窗口里的位置，逻辑像素：x 是关闭钮左缘，y 决定标题栏容器高
/// （tao：容器高 = 按钮高 + y）。页面顶栏高 44（`TopBar` 的 `h-11`），中线在 22——
/// macOS 26 实测按钮中线 ≈ y − 2.5（y = 30 时落在 27.5），所以 y = 24.5。
/// x 与页面让出的左侧宽度（`desktop.ts` 的 `TRAFFIC_LIGHTS_INSET`）配套：实测三颗按钮
/// 间距 23、宽 14，右缘在 x + 60 = 76，页面从那里再留一段空。
#[cfg(target_os = "macos")]
pub const TRAFFIC_LIGHTS: (f64, f64) = (16.0, 24.5);

/// 主窗口建造链上的一环（`.main_window_chrome()`）：注入窗框标记——每次导航都注入，壳自带
/// 页面与 sidecar 页面一样；macOS 再把系统标题栏叠到页面上、藏起标题、红绿灯挪进顶栏。
/// 写成链上的方法而不是在外面分段改：主窗口的建造链要整条可读（`tests/test_desktop_file_drop.py`
/// 等按链核对 `disable_drag_drop_handler` / `on_navigation`）。
pub trait MainWindowChrome: Sized {
    fn main_window_chrome(self) -> Self;
}

impl<'a, R: tauri::Runtime, M: tauri::Manager<R>> MainWindowChrome
    for tauri::WebviewWindowBuilder<'a, R, M>
{
    fn main_window_chrome(self) -> Self {
        let builder = self.initialization_script(marker_script());
        #[cfg(target_os = "macos")]
        let builder = builder
            .title_bar_style(tauri::TitleBarStyle::Overlay)
            .hidden_title(true)
            .traffic_light_position(tauri::LogicalPosition::new(
                TRAFFIC_LIGHTS.0,
                TRAFFIC_LIGHTS.1,
            ));
        builder
    }
}

/// `#rrggbb` → (r, g, b)。页面报的是 CSS 变量的计算值；认不出来就不动标题栏
/// （维持系统色总好过刷成一个错的颜色）。
pub fn parse_hex_rgb(s: &str) -> Option<(u8, u8, u8)> {
    let hex = s.trim().strip_prefix('#')?;
    if hex.len() != 6 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let byte = |i: usize| u8::from_str_radix(&hex[i..i + 2], 16).ok();
    Some((byte(0)?, byte(2)?, byte(4)?))
}

/// Win32 的 COLORREF：0x00BBGGRR。
#[cfg_attr(not(windows), allow(dead_code))]
pub fn colorref((r, g, b): (u8, u8, u8)) -> u32 {
    u32::from(r) | (u32::from(g) << 8) | (u32::from(b) << 16)
}

/// 给窗口的系统标题栏上色（DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR）。Windows 10 不认这两个
/// 属性，返回错误——照常忽略，标题栏维持系统色。
#[cfg(windows)]
pub fn apply_caption_colors(window: &tauri::WebviewWindow, bg: (u8, u8, u8), fg: (u8, u8, u8)) {
    use windows::Win32::Foundation::COLORREF;
    use windows::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR,
    };
    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    for (attr, rgb) in [(DWMWA_CAPTION_COLOR, bg), (DWMWA_TEXT_COLOR, fg)] {
        let value = COLORREF(colorref(rgb));
        // SAFETY: hwnd 来自活着的窗口；指针指向栈上的 COLORREF，大小如实给出
        let _ = unsafe {
            DwmSetWindowAttribute(
                hwnd,
                attr,
                std::ptr::from_ref(&value).cast(),
                std::mem::size_of::<COLORREF>() as u32,
            )
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marker_names_the_platform_chrome() {
        let script = marker_script();
        assert!(script.starts_with(&format!("window.{MARKER_GLOBAL} = '")));
        if cfg!(target_os = "macos") {
            assert!(script.contains(CHROME_OVERLAY));
        } else {
            assert!(script.contains(CHROME_MENU_BUTTON));
        }
    }

    #[test]
    fn fullscreen_tracker_get_is_what_was_last_recorded() {
        let t = FullscreenTracker::default();
        assert!(!t.get());
        t.update(true);
        assert!(t.get());
        t.update(false);
        assert!(!t.get());
    }

    #[test]
    fn fullscreen_tracker_reports_only_changes() {
        let t = FullscreenTracker::default();
        assert!(!t.update(false), "初值是非全屏：第一次报 false 不算变化");
        assert!(t.update(true));
        assert!(!t.update(true), "Resized 连发：同一状态不重复通知");
        assert!(t.update(false));
    }

    #[test]
    fn exit_restores_the_inset_at_once_and_enter_waits_for_the_traffic_lights_to_go() {
        use FullscreenPhase::*;
        assert_eq!(phase_verdict(WillEnter), None);
        assert_eq!(phase_verdict(DidEnter), Some(true));
        assert_eq!(phase_verdict(WillExit), Some(false));
        assert_eq!(phase_verdict(DidExit), Some(false));
    }

    #[test]
    fn resize_fallback_never_reports_true_while_the_observer_is_live() {
        assert!(resize_may_report(false, true), "没有观察者：兜底全权负责");
        assert!(resize_may_report(false, false));
        assert!(!resize_may_report(true, true));
        assert!(resize_may_report(true, false));
    }

    #[test]
    fn hex_colors_parse_or_are_refused() {
        assert_eq!(parse_hex_rgb("#efefed"), Some((0xef, 0xef, 0xed)));
        assert_eq!(parse_hex_rgb(" #161615 "), Some((0x16, 0x16, 0x15)));
        assert_eq!(parse_hex_rgb("#fff"), None);
        assert_eq!(parse_hex_rgb("rgb(1, 2, 3)"), None);
        assert_eq!(parse_hex_rgb("#gggggg"), None);
        assert_eq!(parse_hex_rgb("efefed"), None);
    }

    #[test]
    fn colorref_is_bgr() {
        assert_eq!(colorref((0x11, 0x22, 0x33)), 0x0033_2211);
    }
}
