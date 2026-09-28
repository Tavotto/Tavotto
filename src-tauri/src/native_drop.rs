//! 主页拖放在 macOS 上拿真实路径（ADR 0092）。
//!
//! ## 为什么不直接打开 Tauri 自己的拖放事件
//!
//! 窗口建的时候 `disable_drag_drop_handler()`（8985b9e9c）：Tauri 的拖放处理器一旦装上，
//! 回调**无条件返回 true**（`tauri-runtime-wry` 的 `with_drag_drop_handler`），wry 就不再把
//! `draggingEntered / draggingUpdated / performDragOperation` 交还给 WKWebView——页面里
//! 所有 HTML5 拖放（素材拖进画布、画布 / 图层 / 工作区列表的拖动排序）整片失效。
//! 这个开关只能在建窗口时定，运行中按视图切不了。
//!
//! ## 这里怎么做
//!
//! 只在 `performDragOperation:` 这一个点上**旁听**：先从拖放粘贴板读出文件路径
//! （Finder 拖来的文件才有 `NSFilenamesPboardType`；页面内部的 HTML5 拖动没有），交给
//! `drop_paths::classify` 分派并发 `tavotto:file-drop` 事件，然后**原样调用原实现**——
//! WKWebView 照常把 drop 交给页面，编辑器里的拖放行为一个字节不变。
//!
//! 视图分派在前端：只有主页在挂着时订阅这个事件（编辑器不订阅，事件落空）；而且
//! WebKit 只在页面的 dragover 接受了这次拖动（`preventDefault`）时才会走到
//! `performDragOperation:`——编辑器画布不接受外部文件，旁听点根本不会被调到。
//!
//! 改的是 wry 的 `WryWebView` 类上这一个方法的实现（整个进程只有主窗口这一个 webview），
//! 装一次、装不上就老实报 `false`，前端退回「选择器」那条降级路。
use std::ffi::{c_char, CStr};
use std::path::PathBuf;
use std::sync::OnceLock;

use objc2::ffi;
use objc2::runtime::{AnyClass, AnyObject, Bool, Imp, Sel};
use objc2::{msg_send, sel};
use tauri::Emitter;

use crate::drop_paths;

type PerformDrag = unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject) -> Bool;

static ORIGINAL: OnceLock<PerformDrag> = OnceLock::new();
static APP: OnceLock<tauri::AppHandle> = OnceLock::new();

/// 旁听装上了没有（`native_file_drop` 命令回的就是它）
pub fn installed() -> bool {
    ORIGINAL.get().is_some()
}

/// 拖放粘贴板里的文件路径（与 wry 自己的 `collect_paths` 同一种读法）
unsafe fn dropped_paths(drag_info: *mut AnyObject) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if drag_info.is_null() {
        return out;
    }
    let pb: *mut AnyObject = msg_send![drag_info, draggingPasteboard];
    let Some(ns_string) = AnyClass::get(c"NSString") else {
        return out;
    };
    let ty: *mut AnyObject =
        msg_send![ns_string, stringWithUTF8String: c"NSFilenamesPboardType".as_ptr()];
    if pb.is_null() || ty.is_null() {
        return out;
    }
    let list: *mut AnyObject = msg_send![pb, propertyListForType: ty];
    if list.is_null() {
        return out;
    }
    let Some(ns_array) = AnyClass::get(c"NSArray") else {
        return out;
    };
    let is_array: Bool = msg_send![list, isKindOfClass: ns_array];
    if !is_array.as_bool() {
        return out;
    }
    let n: usize = msg_send![list, count];
    for i in 0..n {
        let s: *mut AnyObject = msg_send![list, objectAtIndex: i];
        if s.is_null() {
            continue;
        }
        let utf8: *const c_char = msg_send![s, UTF8String];
        if utf8.is_null() {
            continue;
        }
        out.push(PathBuf::from(
            CStr::from_ptr(utf8).to_string_lossy().into_owned(),
        ));
    }
    out
}

unsafe extern "C-unwind" fn perform_drag_operation(
    this: *mut AnyObject,
    cmd: Sel,
    drag_info: *mut AnyObject,
) -> Bool {
    let paths = dropped_paths(drag_info);
    if !paths.is_empty() {
        if let (Some(app), Some(target)) = (APP.get(), drop_paths::classify(&paths)) {
            let _ = app.emit_to("main", drop_paths::EVENT, target);
        }
    }
    // 原实现照常跑：页面拿到它自己的 drop 事件，HTML5 拖放不受影响
    match ORIGINAL.get() {
        Some(original) => original(this, cmd, drag_info),
        None => Bool::NO,
    }
}

/// 在主窗口的 WKWebView 上装旁听。`webview` 是 `PlatformWebview::inner()`。
/// 只装一次；第二次调用什么都不做。
pub fn install(webview: *mut std::ffi::c_void, app: tauri::AppHandle) {
    if installed() || webview.is_null() {
        return;
    }
    unsafe {
        let cls = ffi::object_getClass(webview as *const AnyObject);
        if cls.is_null() {
            return;
        }
        let method = ffi::class_getInstanceMethod(cls, sel!(performDragOperation:));
        if method.is_null() {
            return;
        }
        // 先记下原实现、再换：换上去的那一刻起任何一次 drop 都要能调到原实现
        let Some(previous) = ffi::method_getImplementation(method) else {
            return;
        };
        let _ = APP.set(app);
        let _ = ORIGINAL.set(std::mem::transmute::<Imp, PerformDrag>(previous));
        let ours: PerformDrag = perform_drag_operation;
        ffi::method_setImplementation(method, std::mem::transmute::<PerformDrag, Imp>(ours));
    }
}
