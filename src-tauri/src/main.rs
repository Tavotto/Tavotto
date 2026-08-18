//! Magplot 桌面壳：只做窗口、生命周期、菜单与安全边界，业务全在 Python sidecar。
//! 见 docs/adr/0002-tauri-desktop-shell.md。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

use percent_encoding::{utf8_percent_encode, NON_ALPHANUMERIC};
use tauri::menu::{AboutMetadataBuilder, Menu, MenuItemBuilder, SubmenuBuilder};
use tauri::{Emitter, Manager};
use tauri_plugin_opener::OpenerExt;

struct AppState {
    sidecar: Mutex<Option<sidecar::Sidecar>>,
    port: Arc<OnceLock<u16>>,
    nonce: String,
}

impl AppState {
    fn shutdown_sidecar(&self) {
        if let Some(sc) = self.sidecar.lock().unwrap().take() {
            sc.shutdown();
        }
    }
}

fn random_nonce() -> String {
    let bytes: [u8; 32] = rand::random();
    bytes.iter().fold(String::with_capacity(64), |mut s, b| {
        let _ = write!(s, "{b:02x}");
        s
    })
}

/// 导航守卫：
/// - 壳自带页面（splash/error，tauri://localhost 或 http://tauri.localhost）放行；
/// - sidecar 源上只放行 SPA 根路径（应用是单页的，任何其它整页导航都不对——
///   /exports 等由前端走原生「在文件夹中显示」，不允许把主窗口导航成 PDF 视图）；
/// - 其余 http(s)/mailto 一律交给系统默认程序打开，绝不在 WebView 里加载外部网页。
fn navigation_allowed(url: &url::Url, port: &OnceLock<u16>) -> NavDecision {
    if url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost") {
        return NavDecision::Allow;
    }
    if url.scheme() == "http" && url.host_str() == Some("127.0.0.1") {
        if let Some(p) = port.get() {
            if url.port() == Some(*p) {
                if url.path() == "/" {
                    return NavDecision::Allow;
                }
                return NavDecision::Deny; // 同源非根路径：前端应走原生路径
            }
        }
        return NavDecision::Deny;
    }
    match url.scheme() {
        "http" | "https" | "mailto" => NavDecision::OpenExternal,
        _ => NavDecision::Deny,
    }
}

enum NavDecision {
    Allow,
    Deny,
    OpenExternal,
}

/// 在导出目录里定位文件并在文件管理器中显示。前端只能传「目录 + 纯文件名」，
/// 文件名不得含路径分隔符——这是暴露给（本机 sidecar 页面的）IPC 的唯一文件类能力。
#[tauri::command]
fn reveal_export(app: tauri::AppHandle, dir: String, name: String) -> Result<(), String> {
    if name.contains('/') || name.contains('\\') || name.contains("..") || name.is_empty() {
        return Err("非法文件名".into());
    }
    let base = std::fs::canonicalize(PathBuf::from(&dir))
        .map_err(|_| "导出目录不存在".to_string())?;
    let path = base.join(&name);
    if !path.is_file() {
        return Err("文件不存在".into());
    }
    app.opener()
        .reveal_item_in_dir(&path)
        .map_err(|e| e.to_string())
}

fn build_menu<R: tauri::Runtime>(handle: &tauri::AppHandle<R>) -> tauri::Result<Menu<R>> {
    let about = AboutMetadataBuilder::new()
        .name(Some("Magplot"))
        .version(Some(env!("CARGO_PKG_VERSION")))
        .website(Some("https://github.com/erwanjun/magplot"))
        .comments(Some("论文 Figure 排版 + 参数化图表编辑"))
        .build();

    let menu = Menu::new(handle)?;

    #[cfg(target_os = "macos")]
    {
        let app_menu = SubmenuBuilder::new(handle, "Magplot")
            .about_with_text("关于 Magplot", Some(about.clone()))
            .separator()
            .hide_with_text("隐藏 Magplot")
            .hide_others_with_text("隐藏其他")
            .separator()
            .quit_with_text("退出 Magplot")
            .build()?;
        menu.append(&app_menu)?;
    }

    #[cfg_attr(target_os = "macos", allow(unused_mut))]
    let mut file = SubmenuBuilder::new(handle, "文件")
        .item(
            &MenuItemBuilder::with_id("menu-open-project", "打开项目…")
                .accelerator("CmdOrCtrl+O")
                .build(handle)?,
        )
        .item(
            &MenuItemBuilder::with_id("menu-export", "导出…")
                .accelerator("CmdOrCtrl+E")
                .build(handle)?,
        );
    #[cfg(not(target_os = "macos"))]
    {
        file = file.separator().quit_with_text("退出");
    }
    menu.append(&file.build()?)?;

    // 撤销/重做是自定义项：走事件转发给前端（画布 undo 栈），文本框内的
    // 原生撤销由前端按焦点分派。剪贴板项必须用预定义角色——macOS 的
    // WKWebView 里没有这些菜单角色时 ⌘C/⌘V 在输入框里完全失效。
    let edit = SubmenuBuilder::new(handle, "编辑")
        .item(
            &MenuItemBuilder::with_id("menu-undo", "撤销")
                .accelerator("CmdOrCtrl+Z")
                .build(handle)?,
        )
        .item(
            &MenuItemBuilder::with_id("menu-redo", "重做")
                .accelerator("CmdOrCtrl+Shift+Z")
                .build(handle)?,
        )
        .separator()
        .cut_with_text("剪切")
        .copy_with_text("复制")
        .paste_with_text("粘贴")
        .select_all_with_text("全选")
        .build()?;
    menu.append(&edit)?;

    let help = SubmenuBuilder::new(handle, "帮助")
        .about_with_text("关于 Magplot", Some(about))
        .build()?;
    menu.append(&help)?;

    Ok(menu)
}

fn spawn_sidecar_and_navigate(app: tauri::AppHandle) {
    let state = app.state::<AppState>();
    let nonce = state.nonce.clone();
    let port_cell = state.port.clone();

    std::thread::spawn(move || {
        let resource_dir = app.path().resource_dir().ok();
        let log_dir = app
            .path()
            .app_log_dir()
            .unwrap_or_else(|_| std::env::temp_dir().join("magplot-logs"));

        let result = sidecar::Sidecar::start(resource_dir, &log_dir, &nonce);
        let Some(win) = app.get_webview_window("main") else {
            if let Ok((sc, _)) = result {
                sc.shutdown();
            }
            return;
        };
        match result {
            Ok((sc, port)) => {
                let log_path = sc.log_path.clone();
                *app.state::<AppState>().sidecar.lock().unwrap() = Some(sc);
                let _ = port_cell.set(port);
                // fragment 携带一次性 nonce：不进 HTTP 请求行，也就不进任何访问日志
                let url = format!("http://127.0.0.1:{port}/#dnonce={nonce}");
                if win
                    .eval(&format!("window.location.replace({})", js_string(&url)))
                    .is_err()
                {
                    show_error(&win, "窗口初始化失败", &log_path.display().to_string());
                }
            }
            Err(msg) => {
                let log = log_dir.join("sidecar.log");
                show_error(&win, &msg, &log.display().to_string());
            }
        }
    });
}

fn js_string(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "''".into())
}

fn show_error(win: &tauri::WebviewWindow, msg: &str, log_path: &str) {
    let q = format!(
        "error.html?msg={}&log={}",
        utf8_percent_encode(msg, NON_ALPHANUMERIC),
        utf8_percent_encode(log_path, NON_ALPHANUMERIC)
    );
    let _ = win.eval(&format!("window.location.replace({})", js_string(&q)));
}

fn main() {
    let state = AppState {
        sidecar: Mutex::new(None),
        port: Arc::new(OnceLock::new()),
        nonce: random_nonce(),
    };

    let app = tauri::Builder::default()
        // 单实例必须最先注册：第二次启动只聚焦已有窗口，绝不再起一套后端
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        // 应用内更新：前端经 @tauri-apps/plugin-updater 检查/下载/安装，
        // 装完用 process 的 restart 重启。升级永不静默进行——什么时候换版本
        // 是用户按下按钮的结果（与 Python updater 同一条纪律）。
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![reveal_export])
        .menu(build_menu)
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            if id.starts_with("menu-") {
                let _ = app.emit_to("main", "magplot:menu", id.to_string());
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let port_cell = app.state::<AppState>().port.clone();
            let nav_handle = handle.clone();
            let win = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("splash.html".into()),
            )
            .title("Magplot")
            .inner_size(1280.0, 860.0)
            // 三栏工作台的断点下限：再窄左右栏会互相挤压（见 CLAUDE.md 视觉纪律）
            .min_inner_size(1024.0, 680.0)
            // Tauri 默认接管窗口的拖放事件（tauri://drag-drop），代价是 webview 里
            // 的 HTML5 drag&drop 整个失效——「素材拖入画布」在桌面壳里就是这么坏的。
            // 我们不消费 OS 文件拖放（素材来自图库目录扫描），关掉它把 DnD 还给页面。
            .disable_drag_drop_handler()
            .on_navigation(move |url| match navigation_allowed(url, &port_cell) {
                NavDecision::Allow => true,
                NavDecision::Deny => false,
                NavDecision::OpenExternal => {
                    let _ = nav_handle.opener().open_url(url.as_str(), None::<&str>);
                    false
                }
            })
            .build()?;
            let _ = win.set_focus();
            spawn_sidecar_and_navigate(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Magplot 桌面壳初始化失败");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            // 无论怎么退出（关窗、⌘Q、系统注销）都同步收掉 sidecar 与其子进程
            if let Some(state) = app_handle.try_state::<AppState>() {
                state.shutdown_sidecar();
            }
        }
    });
}
