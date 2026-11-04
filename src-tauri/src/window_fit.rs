//! 窗口尺寸夹进显示器工作区（QA-WIN-10 / 17）。
//!
//! `min_inner_size(1024, 680)` 与 `inner_size(1280, 860)` 是**逻辑**像素。1024×768 的屏幕在 125%
//! 缩放下只有约 819×614 逻辑像素，窗口最小也比屏幕大，网页以为自己有 680 高、下缘却落在
//! 屏幕 / 任务栏之外：底部按钮被挡住，滚轮也滚不出来（页面根本没有溢出）。
//! 所以创建窗口前先按工作区（扣掉任务栏 / Dock）夹一次；拿不到显示器信息就保持原值。

/// 系统标题栏的保守高度（逻辑像素）。Windows 主窗口无系统窗框、macOS 标题栏叠在页面上，
/// 多扣一点只会让窗口略小，不会让它超出屏幕。
pub const TITLEBAR_RESERVE: f64 = 32.0;

pub type Size = (f64, f64);

/// 把期望尺寸与最小尺寸夹进可用的逻辑尺寸 `avail`，返回 `(inner_size, min_inner_size)`。
/// 最小尺寸先收到 `avail` 以内，期望尺寸再夹到 `[最小, avail]`。
pub fn fit(desired: Size, min: Size, avail: Size) -> (Size, Size) {
    let min = (min.0.min(avail.0), min.1.min(avail.1));
    let size = (
        desired.0.clamp(min.0, avail.0),
        desired.1.clamp(min.1, avail.1),
    );
    (size, min)
}

/// 工作区物理尺寸 → 可用逻辑尺寸（除以缩放，再扣标题栏）。
pub fn avail_logical(work_px: (u32, u32), scale: f64) -> Option<Size> {
    if !(scale.is_finite() && scale > 0.0) || work_px.0 == 0 || work_px.1 == 0 {
        return None;
    }
    Some((
        work_px.0 as f64 / scale,
        (work_px.1 as f64 / scale - TITLEBAR_RESERVE).max(1.0),
    ))
}

/// 主显示器上夹过的 `(inner_size, min_inner_size)`；读不到显示器时原样返回。
pub fn fit_to_primary(app: &tauri::AppHandle, desired: Size, min: Size) -> (Size, Size) {
    let avail = app.primary_monitor().ok().flatten().and_then(|m| {
        let wa = m.work_area().size;
        avail_logical((wa.width, wa.height), m.scale_factor())
    });
    match avail {
        Some(a) => fit(desired, min, a),
        None => (desired, min),
    }
}

/// 恢复上次窗口状态之后的二次夹取：`inner` 是此刻的内尺寸（逻辑像素）。超出可用区才返回要设的新尺寸，
/// 没超出返回 None（不动用户保存的大小）。插件的 `restore_state` 用 `set_size` 原样设回保存过的
/// 物理尺寸、不看工作区，换了显示器 / 缩放 / 任务栏之后旧值可能比屏幕还大。
pub fn clamp_restored(inner: Size, min: Size, avail: Size) -> Option<Size> {
    let (size, _) = fit(inner, min, avail);
    (size != inner).then_some(size)
}

/// 主窗口恢复完状态后调用：非最大化、非全屏、且窗口大于所在显示器工作区时收回去。
/// 读不到显示器信息或任何一步出错都保持原样。
pub fn clamp_restored_window(win: &tauri::WebviewWindow, min: Size) {
    if win.is_maximized().unwrap_or(false) || win.is_fullscreen().unwrap_or(false) {
        return;
    }
    let Some(mon) = win
        .current_monitor()
        .ok()
        .flatten()
        .or_else(|| win.primary_monitor().ok().flatten())
    else {
        return;
    };
    let wa = mon.work_area().size;
    let Some(avail) = avail_logical((wa.width, wa.height), mon.scale_factor()) else {
        return;
    };
    let Ok(inner) = win.inner_size() else { return };
    let scale = win.scale_factor().unwrap_or(1.0);
    if !(scale.is_finite() && scale > 0.0) {
        return;
    }
    let cur = (inner.width as f64 / scale, inner.height as f64 / scale);
    if let Some((w, h)) = clamp_restored(cur, min, avail) {
        let _ = win.set_size(tauri::LogicalSize::new(w, h));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DESIRED: Size = (1280.0, 860.0);
    const MIN: Size = (1024.0, 680.0);

    #[test]
    fn small_screen_with_125_percent_scale_gets_min_below_work_area() {
        // 1024×768 物理、125%、任务栏 40 物理像素
        let avail = avail_logical((1024, 768 - 40), 1.25).unwrap();
        let (size, min) = fit(DESIRED, MIN, avail);
        assert!(min.0 < MIN.0 && min.1 < MIN.1);
        assert!(min.0 <= avail.0 && min.1 <= avail.1);
        assert!(size.0 <= avail.0 && size.1 <= avail.1);
        assert!(size.0 >= min.0 && size.1 >= min.1);
    }

    #[test]
    fn big_screen_keeps_original_values() {
        let avail = avail_logical((2560, 1440 - 48), 1.0).unwrap();
        assert_eq!(fit(DESIRED, MIN, avail), (DESIRED, MIN));
    }

    #[test]
    fn size_between_min_and_avail_is_kept() {
        assert_eq!(fit(DESIRED, MIN, (1200.0, 800.0)), ((1200.0, 800.0), MIN));
    }

    #[test]
    fn bad_monitor_info_is_ignored() {
        assert!(avail_logical((1024, 728), 0.0).is_none());
        assert!(avail_logical((0, 0), 1.0).is_none());
        assert!(avail_logical((1024, 728), f64::NAN).is_none());
    }

    fn avail_for(work: (u32, u32), scale: f64) -> Size {
        avail_logical(work, scale).unwrap()
    }

    #[test]
    fn conversion_for_100_125_150_percent_on_1024x768() {
        // 工作区 1024×728（任务栏 40 物理像素）
        for (scale, w, h) in [
            (1.0, 1024.0, 696.0),
            (1.25, 819.2, 550.4),
            (1.5, 1024.0 / 1.5, 728.0 / 1.5 - 32.0),
        ] {
            let a = avail_for((1024, 728), scale);
            assert!(
                (a.0 - w).abs() < 1e-9 && (a.1 - h).abs() < 1e-9,
                "{scale}: {a:?}"
            );
            let (size, min) = fit(DESIRED, MIN, a);
            assert!(min.0 <= a.0 && min.1 <= a.1, "{scale}: min {min:?} > {a:?}");
            assert!(size.0 <= a.0 && size.1 <= a.1 && size.0 >= min.0 && size.1 >= min.1);
        }
        // 100%：宽度刚好够 1024，高度 696 < 680 以上的期望 860 → 夹到 696
        let (size, min) = fit(DESIRED, MIN, avail_for((1024, 728), 1.0));
        assert_eq!((size, min), ((1024.0, 696.0), (1024.0, 680.0)));
    }

    #[test]
    fn conversion_for_100_125_150_percent_on_1920x1080() {
        // 工作区 1920×1032（任务栏 48）
        let a100 = avail_for((1920, 1032), 1.0);
        assert_eq!(fit(DESIRED, MIN, a100), (DESIRED, MIN));
        let a125 = avail_for((1920, 1032), 1.25); // 1536 × 793.6
        assert_eq!(fit(DESIRED, MIN, a125), ((1280.0, 793.6), MIN));
        let a150 = avail_for((1920, 1032), 1.5); // 1280 × 656
        let (size, min) = fit(DESIRED, MIN, a150);
        assert_eq!(size, (1280.0, 656.0));
        assert_eq!(min, (1024.0, 656.0));
    }

    #[test]
    fn restored_oversize_is_clamped_and_fitting_is_untouched() {
        let avail = avail_for((1024, 728), 1.25); // 819.2 × 550.4
                                                  // 老用户保存过一个 1600×1000 的窗口
        assert_eq!(
            clamp_restored((1600.0, 1000.0), MIN, avail),
            Some((819.2, 550.4))
        );
        // 已经装得下就不动
        assert_eq!(clamp_restored((800.0, 500.0), (640.0, 400.0), avail), None);
        // 大屏上保存的大窗口在大屏上保持
        assert_eq!(
            clamp_restored((1600.0, 1000.0), MIN, (2400.0, 1300.0)),
            None
        );
    }
}
