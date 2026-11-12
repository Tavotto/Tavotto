//! 主窗口所在屏幕的物理 ppi（ADR 0168 第二节）：点阵进度条按它选点径，让各块屏幕上点的实际大小接近。
//!
//! - Windows：`GetDpiForMonitor(MDT_RAW_DPI)`——显示器 EDID 报的真实每英寸像素数（不是缩放后的有效 dpi）。
//! - macOS：`CGDisplayScreenSize` 给屏幕毫米宽，像素宽取 Tauri 报的当前显示器物理像素宽（= 后备缓冲像素，
//!   正是 canvas 画的那套像素）。
//! - 其他平台、读不到、读到的数不合理（投影 / 电视 / 远程桌面 / 虚拟机常见 0 或离谱值）：`None`，
//!   前端退回「按缩放倍数取整」。
//!
//! 只在主线程算（窗口建好时、`Moved` / `ScaleFactorChanged`），算完存进 [`PpiCache`]；命令只读缓存，
//! 不在命令线程里碰 `NSWindow`。真变了才发 [`SCREEN_PPI_EVENT`]。

use std::sync::Mutex;

/// 事件名：载荷 `{ppi: number | null}`。与 `web/src/lib/desktop.ts` 的 `SCREEN_PPI_EVENT` 严格同源。
pub const SCREEN_PPI_EVENT: &str = "tavotto:screen-ppi";

/// 合理范围：低于 50 / 高于 1000 的读数当作没读到（EDID 缺失时常见 0、或把英寸当厘米报）。
/// 只有能读 ppi 的平台用得到（其他平台 `read` 恒为 `None`）。
#[cfg(any(target_os = "windows", target_os = "macos", test))]
pub fn plausible(ppi: f64) -> Option<f64> {
    (ppi.is_finite() && (50.0..=1000.0).contains(&ppi)).then_some(ppi)
}

/// 由像素宽与毫米宽算 ppi；毫米宽不正就是没读到。只有 macOS 按毫米宽算（Windows 直接读 dpi）。
#[cfg(any(target_os = "macos", test))]
pub fn ppi_from_mm(pixels_wide: f64, mm_wide: f64) -> Option<f64> {
    if !(mm_wide.is_finite() && mm_wide > 0.0 && pixels_wide.is_finite() && pixels_wide > 0.0) {
        return None;
    }
    plausible(pixels_wide / (mm_wide / 25.4))
}

/// 去重底账：上一次告诉页面的值。
#[derive(Default)]
pub struct PpiCache(Mutex<Option<Option<f64>>>);

impl PpiCache {
    /// 记下新读数；与上次不同（含第一次）才返回 `true`。差不到 0.5 ppi 当作没变（拖动时浮点抖动）。
    pub fn update(&self, now: Option<f64>) -> bool {
        let mut slot = self.0.lock().unwrap_or_else(|e| e.into_inner());
        let changed = match (*slot, now) {
            (Some(Some(a)), Some(b)) => (a - b).abs() >= 0.5,
            (Some(a), b) => a.is_some() != b.is_some(),
            (None, _) => true,
        };
        if changed {
            *slot = Some(now);
        }
        changed
    }

    pub fn get(&self) -> Option<f64> {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).flatten()
    }
}

/// 读主窗口当前所在屏幕的 ppi。只在主线程调。
#[cfg(target_os = "windows")]
pub fn read(window: &tauri::Window) -> Option<f64> {
    use windows::Win32::Graphics::Gdi::{MonitorFromWindow, MONITOR_DEFAULTTONEAREST};
    use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_RAW_DPI};
    let hwnd = window.hwnd().ok()?;
    let (mut x, mut y) = (0u32, 0u32);
    // SAFETY：hwnd 来自 Tauri 的活窗口；两个出参是本栈上的 u32。
    unsafe {
        let mon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
        if mon.is_invalid() {
            return None;
        }
        GetDpiForMonitor(mon, MDT_RAW_DPI, &mut x, &mut y).ok()?;
    }
    plausible(f64::from(x))
}

#[cfg(target_os = "macos")]
pub fn read(window: &tauri::Window) -> Option<f64> {
    use objc2::msg_send;
    use objc2::runtime::AnyObject;

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct CGSize {
        width: f64,
        height: f64,
    }
    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGDisplayScreenSize(display: u32) -> CGSize;
    }

    let ns_window = window.ns_window().ok()?.cast::<AnyObject>();
    if ns_window.is_null() {
        return None;
    }
    // SAFETY：主线程；ns_window 是 Tauri 的活 NSWindow。screen / deviceDescription 都是只读取值，
    // 窗口完全在屏幕外时 screen 是 nil。
    let display: u32 = unsafe {
        let screen: *mut AnyObject = msg_send![&*ns_window, screen];
        if screen.is_null() {
            return None;
        }
        let desc: *mut AnyObject = msg_send![&*screen, deviceDescription];
        if desc.is_null() {
            return None;
        }
        let key: *mut AnyObject =
            msg_send![objc2::class!(NSString), stringWithUTF8String: c"NSScreenNumber".as_ptr()];
        let num: *mut AnyObject = msg_send![&*desc, objectForKey: key];
        if num.is_null() {
            return None;
        }
        msg_send![&*num, unsignedIntValue]
    };
    // SAFETY：纯取值，display 不存在时返回 0×0。
    let mm = unsafe { CGDisplayScreenSize(display) };
    let px = window.current_monitor().ok()??.size().width;
    ppi_from_mm(f64::from(px), mm.width)
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub fn read(_window: &tauri::Window) -> Option<f64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn implausible_readings_are_treated_as_unknown() {
        assert_eq!(plausible(0.0), None);
        assert_eq!(plausible(f64::NAN), None);
        assert_eq!(plausible(5000.0), None);
        assert_eq!(plausible(141.0), Some(141.0));
    }

    #[test]
    fn ppi_from_physical_width() {
        // 14 寸 MacBook Pro：3024 像素 / 302 mm ≈ 254 ppi
        let p = ppi_from_mm(3024.0, 302.4).unwrap();
        assert!((p - 254.0).abs() < 0.5, "{p}");
        // 读不到毫米宽（投影 / 虚拟显示器报 0）
        assert_eq!(ppi_from_mm(1920.0, 0.0), None);
        assert_eq!(ppi_from_mm(0.0, 300.0), None);
    }

    #[test]
    fn cache_reports_only_real_changes() {
        let c = PpiCache::default();
        assert!(c.update(None), "第一次总要告诉页面（哪怕是读不到）");
        assert!(!c.update(None));
        assert!(c.update(Some(141.0)));
        assert!(!c.update(Some(141.3)), "拖动时的浮点抖动不算变");
        assert!(c.update(Some(163.0)), "换到另一块屏幕");
        assert_eq!(c.get(), Some(163.0));
        assert!(c.update(None));
        assert_eq!(c.get(), None);
    }
}
