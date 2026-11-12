//! 主页拖放在 Windows 上拿真实路径（ADR 0092 修订 2026-11-03）。
//!
//! ## 为什么不直接打开 Tauri 自己的拖放事件
//!
//! 与 macOS 同一个原因（见 `native_drop.rs`）：窗口建的时候 `disable_drag_drop_handler()`。
//! 在 Windows 上 Tauri 的处理器是 wry 往窗口注册的一个 OLE `IDropTarget`，它把 WebView2
//! 自己的那个顶掉，页面里所有 HTML5 拖放（素材拖进画布、列表排序）整片失效。
//!
//! ## 这里怎么做
//!
//! WebView2 不给页面路径，但给了一条官方的路：页面在 `drop` 里调
//! `chrome.webview.postMessageWithAdditionalObjects(消息, dataTransfer.files)`，宿主在
//! `WebMessageReceived` 里经 `ICoreWebView2WebMessageReceivedEventArgs2::AdditionalObjects`
//! 拿到 `ICoreWebView2File`，`Path()` 就是磁盘上的真实路径。拿到之后与 macOS 走同一条：
//! `drop_paths::classify` 分派、发 `tavotto:file-drop`。
//!
//! 页面那半在 `web/src/lib/desktop.ts` 的 `ensureDropForwarder`：只在 window 的捕获阶段听
//! `drop`——浏览器只在页面的 dragover 接受了这次拖动时才派发 `drop`，语义与 macOS 的
//! `performDragOperation:` 相同（编辑器里只有画布接受系统文件）。页面自己的 drop 处理一个字不变。
//!
//! 消息必须是 JSON **字符串**：wry 的 IPC 处理器先 `TryGetWebMessageAsString`，对象消息让
//! 它返回 E_INVALIDARG，后面的旁听无法收到。这里也读字符串，再按 `{"tavotto":"file-drop"}`
//! 认标记；`WebMessageAsJson` 会对字符串再编码一层，认不出来。Tauri 会把这条非 invoke 消息
//! 记成控制台解析错误，但处理器正常返回，不影响后续旁听；不伪造 invoke 或绕过其认证。
//!
//! 需要 WebView2 运行时 ≥ 1.0.1518（2023 年起的常青版本都满足）；拿不到
//! `ICoreWebView2WebMessageReceivedEventArgs2` 时本次不发事件，前端等满 grace 后照旧降级到选择器。
use std::path::PathBuf;
use std::sync::OnceLock;

use tauri::Emitter;
use webview2_com::Microsoft::Web::WebView2::Win32::{
    ICoreWebView2Controller, ICoreWebView2File, ICoreWebView2WebMessageReceivedEventArgs,
    ICoreWebView2WebMessageReceivedEventArgs2,
};
use webview2_com::{take_pwstr, WebMessageReceivedEventHandler};
use windows::core::{Interface, PWSTR};

use crate::drop_paths;

/// 页面发来的消息 `{"tavotto": MESSAGE_KIND}`（与 `web/src/lib/desktop.ts` 的 `DROP_MESSAGE` 严格同源，
/// `tests/test_desktop_file_drop.py` 看护）
pub const MESSAGE_KIND: &str = "file-drop";

static INSTALLED: OnceLock<()> = OnceLock::new();

/// 旁听装上了没有（`native_file_drop` 命令回的就是它）
pub fn installed() -> bool {
    INSTALLED.get().is_some()
}

/// 这条消息是不是页面转来的那一次放下
fn is_drop_message(json: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(json)
        .ok()
        .and_then(|v| {
            v.get("tavotto")
                .and_then(|k| k.as_str())
                .map(|k| k == MESSAGE_KIND)
        })
        .unwrap_or(false)
}

/// 随消息带来的 File 对象在磁盘上的路径（页面里凭空 `new File()` 出来的没有 `ICoreWebView2File`，跳过）
unsafe fn dropped_paths(args: &ICoreWebView2WebMessageReceivedEventArgs) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(args2) = args.cast::<ICoreWebView2WebMessageReceivedEventArgs2>() else {
        return out;
    };
    let Ok(objects) = args2.AdditionalObjects() else {
        return out;
    };
    let mut n = 0u32;
    if objects.Count(&mut n).is_err() {
        return out;
    }
    for i in 0..n {
        let Ok(obj) = objects.GetValueAtIndex(i) else {
            continue;
        };
        let Ok(file) = obj.cast::<ICoreWebView2File>() else {
            continue;
        };
        let mut p = PWSTR::null();
        if file.Path(&mut p).is_ok() {
            let s = take_pwstr(p);
            if !s.is_empty() {
                out.push(PathBuf::from(s));
            }
        }
    }
    out
}

/// 在主窗口的 WebView2 上装旁听。`controller` 是 `PlatformWebview::controller()`。
/// 只装一次；第二次调用什么都不做。装不上（拿不到 CoreWebView2、注册失败）就不装，命令回 `false`。
pub fn install(controller: ICoreWebView2Controller, app: tauri::AppHandle) {
    if installed() {
        return;
    }
    unsafe {
        let Ok(webview) = controller.CoreWebView2() else {
            return;
        };
        let handler = WebMessageReceivedEventHandler::create(Box::new(move |_, args| {
            let Some(args) = args else {
                return Ok(());
            };
            let mut json = PWSTR::null();
            if args.TryGetWebMessageAsString(&mut json).is_err()
                || !is_drop_message(&take_pwstr(json))
            {
                return Ok(());
            }
            let paths = dropped_paths(&args);
            if !paths.is_empty() {
                if let Some(target) = drop_paths::classify(&paths) {
                    let _ = app.emit_to("main", drop_paths::EVENT, target);
                }
            }
            Ok(())
        }));
        let mut token = 0i64;
        if webview.add_WebMessageReceived(&handler, &mut token).is_ok() {
            let _ = INSTALLED.set(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_tagged_json_text_counts() {
        assert!(is_drop_message(r#"{"tavotto":"file-drop"}"#));
        // WebMessageAsJson 对字符串再次编码后的结果不是约定的消息。
        let double_encoded = serde_json::to_string(r#"{"tavotto":"file-drop"}"#).unwrap();
        assert!(!is_drop_message(&double_encoded));
        assert!(!is_drop_message(r#""file-drop""#));
        assert!(!is_drop_message(r#"{"tavotto":"other"}"#));
        assert!(!is_drop_message(r#"{"cmd":"plugin:event|listen"}"#));
        assert!(!is_drop_message("not json"));
    }
}
