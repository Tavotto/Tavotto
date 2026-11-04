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
}
