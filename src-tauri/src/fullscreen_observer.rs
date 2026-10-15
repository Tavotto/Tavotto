//! macOS 主窗口进 / 出全屏的原生信号（`NSWindow` 的四条通知）。
//!
//! ## 为什么不只看 `Resized` + `is_fullscreen()`
//!
//! tao 的 `is_fullscreen()` 要到 `windowDidExitFullScreen` 才变 false：退出动画期间 `Resized`
//! 一直报「还在全屏」，false 事件到页面时红绿灯早已回到屏上，再叠上 CSS 过渡，顶栏最左的
//! 控件就会在每次退出全屏时压在红绿灯下面（tauri-apps/tauri#7162）。所以改听原生的
//! will / did 通知，时机按一条判据选：**红绿灯在屏上时，绝不收回让位的那段**。
//!
//! | 通知 | 发什么 | 理由 |
//! |---|---|---|
//! | `WillEnterFullScreen` | 不发 | 进入动画期间红绿灯还在，此刻收回占位会压上去 |
//! | `DidEnterFullScreen` | `true` | 红绿灯已随标题栏收起 |
//! | `WillExitFullScreen` | `false` | 红绿灯即将回来：占位立刻恢复（前端退出方向不做过渡） |
//! | `DidExitFullScreen` | `false` | 对账（`FullscreenTracker` 去重，已发过就不重发） |
//!
//! 决策表是 `window_chrome::phase_verdict`（纯函数，Rust 单测钉着）。`Resized` +
//! `is_fullscreen()` 仍留作兜底：观察者装上后它只许报 `false`
//! （`window_chrome::resize_may_report`），否则退出动画里那次「还是全屏」会把 will-exit 发出的
//! `false` 又顶回 `true`。
//!
//! ## 写法
//!
//! 与 `native_drop.rs` 同一套：只用已有的 objc2 0.6（不引入新 crate、不要 block2），
//! 用 `ClassBuilder` 定义一个 `NSObject` 子类当观察者、selector 接通知。观察者随主窗口：
//! `Destroyed` 时 `remove()` 摘掉并释放。
use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicPtr, Ordering};
use std::sync::OnceLock;

use objc2::runtime::{AnyClass, AnyObject, ClassBuilder, Sel};
use objc2::{msg_send, sel};

use crate::window_chrome::FullscreenPhase;

static APP: OnceLock<tauri::AppHandle> = OnceLock::new();
static OBSERVER: AtomicPtr<AnyObject> = AtomicPtr::new(std::ptr::null_mut());
static LIVE: AtomicBool = AtomicBool::new(false);

/// 观察者装上了没有（`Resized` 兜底据此收紧成「只报 false」）。
pub fn installed() -> bool {
    LIVE.load(Ordering::SeqCst)
}

fn forward(phase: FullscreenPhase) {
    if let Some(app) = APP.get() {
        crate::on_fullscreen_phase(app, phase);
    }
}

extern "C-unwind" fn will_enter(_this: &AnyObject, _cmd: Sel, _n: *mut AnyObject) {
    forward(FullscreenPhase::WillEnter);
}
extern "C-unwind" fn did_enter(_this: &AnyObject, _cmd: Sel, _n: *mut AnyObject) {
    forward(FullscreenPhase::DidEnter);
}
extern "C-unwind" fn will_exit(_this: &AnyObject, _cmd: Sel, _n: *mut AnyObject) {
    forward(FullscreenPhase::WillExit);
}
extern "C-unwind" fn did_exit(_this: &AnyObject, _cmd: Sel, _n: *mut AnyObject) {
    forward(FullscreenPhase::DidExit);
}

fn observer_class() -> Option<&'static AnyClass> {
    if let Some(cls) = AnyClass::get(c"TavottoFullscreenObserver") {
        return Some(cls);
    }
    let mut builder = ClassBuilder::new(c"TavottoFullscreenObserver", AnyClass::get(c"NSObject")?)?;
    // SAFETY: 方法签名 (id, SEL, id) -> void 与 selector `xxx:`（一个对象参数、无返回）一致
    unsafe {
        builder.add_method(
            sel!(tavottoWillEnter:),
            will_enter as extern "C-unwind" fn(_, _, _),
        );
        builder.add_method(
            sel!(tavottoDidEnter:),
            did_enter as extern "C-unwind" fn(_, _, _),
        );
        builder.add_method(
            sel!(tavottoWillExit:),
            will_exit as extern "C-unwind" fn(_, _, _),
        );
        builder.add_method(
            sel!(tavottoDidExit:),
            did_exit as extern "C-unwind" fn(_, _, _),
        );
    }
    Some(builder.register())
}

/// 给主窗口的 `NSWindow` 装观察者。`ns_window` 是 `WebviewWindow::ns_window()`；只在主线程调
/// （`setup` 里）。只装一次；装不上老实不报 `installed()`，`Resized` 兜底照旧全权负责。
pub fn install(ns_window: *mut c_void, app: tauri::AppHandle) {
    if installed() || ns_window.is_null() {
        return;
    }
    let Some(cls) = observer_class() else {
        return;
    };
    let Some(center_cls) = AnyClass::get(c"NSNotificationCenter") else {
        return;
    };
    let Some(string_cls) = AnyClass::get(c"NSString") else {
        return;
    };
    let _ = APP.set(app);
    let window = ns_window.cast::<AnyObject>();
    // SAFETY: cls 是刚注册的 NSObject 子类；center / NSString 是系统单例与类方法；window 来自活着的
    // 主窗口（调用方在主线程、窗口刚建好）。通知中心不保留观察者，所以观察者由 OBSERVER 持有（+1），
    // `remove()` 里先摘再释放。
    unsafe {
        let observer: *mut AnyObject = msg_send![cls, new];
        if observer.is_null() {
            return;
        }
        let center: *mut AnyObject = msg_send![center_cls, defaultCenter];
        let pairs: [(Sel, &std::ffi::CStr); 4] = [
            (
                sel!(tavottoWillEnter:),
                c"NSWindowWillEnterFullScreenNotification",
            ),
            (
                sel!(tavottoDidEnter:),
                c"NSWindowDidEnterFullScreenNotification",
            ),
            (
                sel!(tavottoWillExit:),
                c"NSWindowWillExitFullScreenNotification",
            ),
            (
                sel!(tavottoDidExit:),
                c"NSWindowDidExitFullScreenNotification",
            ),
        ];
        for (selector, name) in pairs {
            let name: *mut AnyObject = msg_send![string_cls, stringWithUTF8String: name.as_ptr()];
            let _: () = msg_send![
                center,
                addObserver: observer,
                selector: selector,
                name: name,
                object: window
            ];
        }
        OBSERVER.store(observer, Ordering::SeqCst);
        LIVE.store(true, Ordering::SeqCst);
    }
}

/// 主窗口销毁时摘掉观察者并释放（之后通知不会再打到已释放的对象上）。幂等。
pub fn remove() {
    LIVE.store(false, Ordering::SeqCst);
    let observer = OBSERVER.swap(std::ptr::null_mut(), Ordering::SeqCst);
    if observer.is_null() {
        return;
    }
    let Some(center_cls) = AnyClass::get(c"NSNotificationCenter") else {
        return;
    };
    // SAFETY: observer 是 `install` 里 new 出来、只由 OBSERVER 持有的那一个（swap 保证只释放一次）
    unsafe {
        let center: *mut AnyObject = msg_send![center_cls, defaultCenter];
        let _: () = msg_send![center, removeObserver: observer];
        let _: () = msg_send![observer, release];
    }
}
