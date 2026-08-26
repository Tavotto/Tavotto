//! Tavotto 桌面壳：只做窗口、生命周期、菜单与安全边界，业务全在 Python sidecar。
//! 见 docs/adr/0002-tauri-desktop-shell.md。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod i18n;
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
    /// 原生菜单当前用的语言。前端 i18n 就绪 / 用户切语言时经
    /// `set_menu_locale` 报上来，Rust 据此重建菜单并记住给下次启动。
    menu_locale: Mutex<i18n::Locale>,
    port: Arc<OnceLock<u16>>,
    nonce: String,
    /// 本次启动带进来的交接请求（`Tavotto --open <目录> [--stem <s>]`）。
    /// 首启走这条：项目交给 sidecar 的 `--figures`，stem 拼进落地 URL 的
    /// `?open=`。**第二次启动不走这里**——单实例插件会把 argv 转发给已经在
    /// 跑的窗口，那条路发 `tavotto:open` 事件。
    open: Option<OpenRequest>,
}

/// 交接契约：`Tavotto --open <项目目录> [--stem <stem> | --pick-script <脚本>]`。
///
/// **与 `src/tavotto/engine/handoff.py` 的 `desktop_argv()` 严格同源**——
/// 那边是唯一的生产者，这边是唯一的消费者，改一边必须同步另一边
/// （Python 侧看护 `tests/test_handoff.py::test_desktop_argv_contract`，
/// Rust 侧看护本文件末尾的单测）。
///
/// `pick`（`--pick-script`）是多 Figure 交接的选择信息（脚本的项目相对
/// 路径）：壳不做任何选择，只把它送进落地 URL 的 `?pick=` / `tavotto:open`
/// 事件，Figure 选择器在前端（不静默选第一张，Session 6 契约）。
#[derive(Clone, serde::Serialize)]
struct OpenRequest {
    project: String,
    stem: Option<String>,
    pick: Option<String>,
}

/// 认不出的参数一律忽略：macOS 从 Finder / Dock 启动会塞 `-psn_0_12345`，
/// Windows 的关联启动会塞文件路径，这些都不该让交接解析失败。
fn parse_open_args(args: &[String]) -> Option<OpenRequest> {
    let mut project: Option<String> = None;
    let mut stem: Option<String> = None;
    let mut pick: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--open" => project = it.next().cloned(),
            "--stem" => stem = it.next().cloned(),
            "--pick-script" => pick = it.next().cloned(),
            _ => {}
        }
    }
    let project = project?;
    if project.trim().is_empty() {
        return None;
    }
    let stem = stem.filter(|s| !s.trim().is_empty());
    // stem 定得下来一张就不需要选择器（生产侧本来就互斥，这里兜底同语义）
    let pick = pick.filter(|s| !s.trim().is_empty()).filter(|_| stem.is_none());
    Some(OpenRequest {
        project,
        stem,
        pick,
    })
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
    let base =
        std::fs::canonicalize(PathBuf::from(&dir)).map_err(|_| "导出目录不存在".to_string())?;
    let path = base.join(&name);
    if !path.is_file() {
        return Err("文件不存在".into());
    }
    app.opener()
        .reveal_item_in_dir(&path)
        .map_err(|e| e.to_string())
}

/// 建菜单。**菜单项 id 与加速键在两种语言下完全相同**——只有显示文案换，
/// 事件转发（`tavotto:menu`）与 `CmdOrCtrl+*` 一个字节不动：切语言绝不能
/// 让 ⌘Z 失灵，那种坏法用户根本不会往语言上联想。
fn build_menu_in<R: tauri::Runtime>(
    handle: &tauri::AppHandle<R>,
    locale: i18n::Locale,
) -> tauri::Result<Menu<R>> {
    let m = i18n::text(locale);
    let about = AboutMetadataBuilder::new()
        .name(Some("Tavotto"))
        .version(Some(env!("CARGO_PKG_VERSION")))
        .website(Some("https://github.com/Tavotto/Tavotto"))
        .comments(Some(m.about_comments))
        .build();

    let menu = Menu::new(handle)?;

    #[cfg(target_os = "macos")]
    {
        let app_menu = SubmenuBuilder::new(handle, "Tavotto")
            .about_with_text(m.app_about, Some(about.clone()))
            .separator()
            .hide_with_text(m.app_hide)
            .hide_others_with_text(m.app_hide_others)
            .separator()
            .quit_with_text(m.app_quit)
            .build()?;
        menu.append(&app_menu)?;
    }

    #[cfg_attr(target_os = "macos", allow(unused_mut))]
    let mut file = SubmenuBuilder::new(handle, m.file)
        .item(
            &MenuItemBuilder::with_id("menu-open-project", m.file_open_project)
                .accelerator("CmdOrCtrl+O")
                .build(handle)?,
        )
        .item(
            &MenuItemBuilder::with_id("menu-export", m.file_export)
                .accelerator("CmdOrCtrl+E")
                .build(handle)?,
        );
    #[cfg(not(target_os = "macos"))]
    {
        file = file.separator().quit_with_text(m.quit);
    }
    menu.append(&file.build()?)?;

    // 撤销/重做是自定义项：走事件转发给前端（画布 undo 栈），文本框内的
    // 原生撤销由前端按焦点分派。剪贴板项必须用预定义角色——macOS 的
    // WKWebView 里没有这些菜单角色时 ⌘C/⌘V 在输入框里完全失效。
    let edit = SubmenuBuilder::new(handle, m.edit)
        .item(
            &MenuItemBuilder::with_id("menu-undo", m.edit_undo)
                .accelerator("CmdOrCtrl+Z")
                .build(handle)?,
        )
        .item(
            &MenuItemBuilder::with_id("menu-redo", m.edit_redo)
                .accelerator("CmdOrCtrl+Shift+Z")
                .build(handle)?,
        )
        .separator()
        .cut_with_text(m.edit_cut)
        .copy_with_text(m.edit_copy)
        .paste_with_text(m.edit_paste)
        .select_all_with_text(m.edit_select_all)
        .build()?;
    menu.append(&edit)?;

    let help = SubmenuBuilder::new(handle, m.help)
        .about_with_text(m.app_about, Some(about))
        .build()?;
    menu.append(&help)?;

    Ok(menu)
}

/// `.menu()` 那次：**只能用默认语言建**。
///
/// 这个闭包跑在 `Builder::build()` 里、Tauri 把 `PathResolver` manage 进去
/// **之前**，此时 `handle.path()` 会直接 panic：
///
///     state() called before manage() for tauri::path::desktop::PathResolver<…>
///
/// 而壳是 windows 子系统的可执行文件，panic 写到一个无效的 stderr 句柄上，
/// 用户看到的只是「双击图标什么都没发生」——2026-08-18 起 Windows 桌面版
/// 每次启动都是这么死的（退出码 101），nightly 的壳探针红了一整晚而唯一的
/// 线索是「壳退了」。
///
/// 上次记下的语言在 `setup()` 里读（那时 PathResolver 已经就位），读到不一样
/// 就地重建一次——重建发生在窗口显示之前，用户看不到中间态。
fn build_menu<R: tauri::Runtime>(handle: &tauri::AppHandle<R>) -> tauri::Result<Menu<R>> {
    build_menu_in(handle, i18n::DEFAULT_LOCALE)
}

/// 前端切了界面语言 → 重建原生菜单，并把选择记下来给下次启动用。
///
/// 前端在 i18n 就绪时（还没挂 React）就会调一次，用户在设置里换语言时再调。
/// 语言认不出来一律忽略：菜单保持现状比换成一堆空词条好。
#[tauri::command]
fn set_menu_locale(
    app: tauri::AppHandle,
    locale: String,
    // 用户亲手选的（设置里换语言），还是只是「当前生效」的汇报（i18n 就绪）。
    // 桌面模式下这是**唯一**能把「手动选择 > 系统语言」还给用户的信息：
    // sidecar 绑 `127.0.0.1:0`，端口每次都变，前端 localStorage 的偏好活不
    // 过一次重启（端口是 Web Storage origin 的一部分）。
    explicit: Option<bool>,
) -> Result<(), String> {
    let Some(next) = i18n::normalize(&locale) else {
        return Err(format!("不支持的语言：{locale}"));
    };
    let explicit = explicit.unwrap_or(false);
    let changed = {
        let state = app.state::<AppState>();
        let mut cur = state.menu_locale.lock().unwrap();
        let changed = *cur != next;
        *cur = next;
        changed
    };
    // 显式选择即使「和现在一样」也要落盘：上一次可能只是跟随系统的汇报，
    // 那时文件里没有 explicit 标记，重启后就又退回系统语言了。
    if changed || explicit {
        i18n::write_locale(
            i18n::locale_file(app.path().app_config_dir().ok()),
            next,
            explicit,
        );
    }
    if !changed {
        return Ok(());
    }
    let menu = build_menu_in(&app, next).map_err(|e| e.to_string())?;
    app.set_menu(menu).map_err(|e| e.to_string())?;
    Ok(())
}

fn spawn_sidecar_and_navigate(app: tauri::AppHandle) {
    let state = app.state::<AppState>();
    let nonce = state.nonce.clone();
    let port_cell = state.port.clone();
    let open = state.open.clone();
    // 起 sidecar 这段跑在前端加载之前，语言只能取记下来的那个偏好
    let menu_locale = *state.menu_locale.lock().unwrap();
    // 记下来的那份是不是「用户亲手选的」——只有它才该盖过前端的系统语言探测
    let chosen_locale = i18n::read_stored(i18n::locale_file(app.path().app_config_dir().ok()))
        .filter(|s| s.explicit)
        .map(|s| s.locale);

    std::thread::spawn(move || {
        let resource_dir = app.path().resource_dir().ok();
        let log_dir = app
            .path()
            .app_log_dir()
            .unwrap_or_else(|_| std::env::temp_dir().join("tavotto-logs"));

        let project = open.as_ref().map(|o| o.project.as_str());
        let result = sidecar::Sidecar::start(resource_dir, &log_dir, &nonce, project, menu_locale);
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
                // fragment 携带一次性 nonce：不进 HTTP 请求行，也就不进任何访问日志。
                // `?open=<stem>` 是首启交接的落点（前端 lib/openRequest.ts 消费），
                // 与浏览器模式共用同一份语义——桌面首启不必再多发一次事件。
                // `lang=` 只在用户**亲手选过**语言时带（见 `set_menu_locale`
                // 的 explicit 参数）。桌面模式下 sidecar 绑 `127.0.0.1:0`，
                // 端口每次都变，而端口是 Web Storage origin 的一部分——前端
                // 存在 localStorage 的语言偏好活不过一次重启，`detectLocale()`
                // 会退回系统语言，再把那个退回值报给壳，把用户真正的选择
                // **覆盖掉**。壳记的这份是唯一活得下来的存储，所以由它带过去。
                let mut params: Vec<String> = Vec::new();
                if let Some(stem) = open.as_ref().and_then(|o| o.stem.as_deref()) {
                    params.push(format!(
                        "open={}",
                        utf8_percent_encode(stem, NON_ALPHANUMERIC)
                    ));
                } else if let Some(pick) = open.as_ref().and_then(|o| o.pick.as_deref()) {
                    // 多 Figure 交接：把脚本交给前端的 Figure 选择器
                    // （与 handoff.browser_url 的 `?pick=` 同一份语义）
                    params.push(format!(
                        "pick={}",
                        utf8_percent_encode(pick, NON_ALPHANUMERIC)
                    ));
                }
                if chosen_locale.is_some() {
                    params.push(format!("lang={}", menu_locale.tag()));
                }
                let query = if params.is_empty() {
                    String::new()
                } else {
                    format!("?{}", params.join("&"))
                };
                let url = format!("http://127.0.0.1:{port}/{query}#dnonce={nonce}");
                if win
                    .eval(format!("window.location.replace({})", js_string(&url)))
                    .is_err()
                {
                    show_error(
                        &win,
                        i18n::text(menu_locale).window_init_failed,
                        &log_path.display().to_string(),
                        menu_locale,
                    );
                }
            }
            Err(msg) => {
                let log = log_dir.join("sidecar.log");
                show_error(&win, &msg, &log.display().to_string(), menu_locale);
            }
        }
    });
}

fn js_string(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "''".into())
}

/// 启动失败页。**语言由壳带过去**：这张页面在 `tauri://` 源下，读不到
/// sidecar 那个源的 localStorage，也没有 i18next——不带 lang 的话，选了英文
/// 的用户在最需要看懂的时候看到的是中文。
fn show_error(win: &tauri::WebviewWindow, msg: &str, log_path: &str, locale: i18n::Locale) {
    let q = format!(
        "error.html?msg={}&log={}&lang={}",
        utf8_percent_encode(msg, NON_ALPHANUMERIC),
        utf8_percent_encode(log_path, NON_ALPHANUMERIC),
        locale.tag()
    );
    let _ = win.eval(format!("window.location.replace({})", js_string(&q)));
}

/// 仅测试用的 headless 更新触发口：`TAVOTTO_E2E_RUN_UPDATE=1`（只认字面
/// `"1"`，生产路径不认其它取值）时启动即执行一次 check → download →
/// install——CI 在 Windows runner 上装好 N-1 官方安装包后用它驱动**真实的**
/// 应用内更新（release.yml 的 `n1_update_windows`），不必去自动化 WebView2
/// 里的更新按钮。与 `--insecure-no-auth` 同一套纪律：默认关死、触发时打
/// 警告、有专门用例看护。
///
/// **endpoint / 公钥 / 插件一个字节不改**：走的就是用户按按钮那条链路
/// （`tauri.conf.json` 的 `plugins.updater`），所以它验的是真链路，不是
/// 一条为测试另开的旁门。Windows 上 NSIS passive 装完由插件重启应用，
/// 旧进程 `std::process::exit(0)` 不走 `RunEvent::Exit`（sidecar 靠
/// stdin EOF 自杀链收摊）——验收方要等**新进程出现**，别等旧进程优雅退出。
///
/// 退出码（CI 按它分诊）：40 = updater 不可用；41 = check 失败；
/// 42 = 下载/安装失败。「已是最新」不退出——更新装完重启回来的那个新进程
/// 会再走一次这里，查到没有更新、照常跑下去，正是期望的收敛态。
fn spawn_e2e_update_if_requested(handle: tauri::AppHandle) {
    if std::env::var("TAVOTTO_E2E_RUN_UPDATE").as_deref() != Ok("1") {
        return;
    }
    eprintln!("[e2e-update] ⚠ TAVOTTO_E2E_RUN_UPDATE=1：启动即执行应用内更新（仅测试用，勿在生产设置）");
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_updater::UpdaterExt;
        let updater = match handle.updater() {
            Ok(u) => u,
            Err(e) => {
                eprintln!("[e2e-update] updater 不可用: {e}");
                std::process::exit(40);
            }
        };
        match updater.check().await {
            Ok(Some(update)) => {
                eprintln!("[e2e-update] 发现新版本 {}，开始下载安装", update.version);
                match update.download_and_install(|_, _| {}, || {}).await {
                    Ok(()) => {
                        // Windows 上装到这里进程通常已被插件替换/退出；
                        // 其余平台显式重启到新版本。
                        eprintln!("[e2e-update] 安装完成，重启到新版本");
                        handle.restart();
                    }
                    Err(e) => {
                        eprintln!("[e2e-update] 下载/安装失败: {e}");
                        std::process::exit(42);
                    }
                }
            }
            Ok(None) => {
                eprintln!("[e2e-update] 已是最新版本，照常启动");
            }
            Err(e) => {
                eprintln!("[e2e-update] 检查更新失败: {e}");
                std::process::exit(41);
            }
        }
    });
}

fn main() {
    let state = AppState {
        sidecar: Mutex::new(None),
        port: Arc::new(OnceLock::new()),
        nonce: random_nonce(),
        open: parse_open_args(&std::env::args().skip(1).collect::<Vec<_>>()),
        // 真正的初值在 build_menu 里按配置目录读；这里先摆默认档，
        // 免得 set_menu_locale 把「和现在一样」误判成需要重建。
        menu_locale: Mutex::new(i18n::DEFAULT_LOCALE),
    };

    let app = tauri::Builder::default()
        // 单实例必须最先注册：第二次启动只聚焦已有窗口，绝不再起一套后端。
        // 「已经开着 Tavotto 再交接一张图」走的正是这条——argv 转发过来，
        // 前端换项目 / 定位面板，后端一套进程不动。
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
            if let Some(req) = parse_open_args(argv.get(1..).unwrap_or(&[])) {
                let _ = app.emit_to("main", "tavotto:open", req);
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
        .invoke_handler(tauri::generate_handler![reveal_export, set_menu_locale])
        .menu(build_menu)
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            if id.starts_with("menu-") {
                let _ = app.emit_to("main", "tavotto:menu", id.to_string());
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let port_cell = app.state::<AppState>().port.clone();
            let nav_handle = handle.clone();
            // 上次记下的语言。菜单与启动画面都用它——「装完第一次打开」之外
            // 每一次启动，用户看到的第一屏就已经是他选的语言。
            // 这里才是第一个能安全用 `handle.path()` 的地方（见 build_menu）
            let boot_locale =
                i18n::read_locale(i18n::locale_file(handle.path().app_config_dir().ok()));
            *app.state::<AppState>().menu_locale.lock().unwrap() = boot_locale;
            if boot_locale != i18n::DEFAULT_LOCALE {
                // `.menu()` 那次只能建默认档，这里补上真正的语言。
                // 失败不拦启动：菜单文案不对总好过应用起不来。
                match build_menu_in(&handle, boot_locale) {
                    Ok(menu) => {
                        let _ = app.set_menu(menu);
                    }
                    Err(e) => eprintln!("rebuild menu for {}: {e}", boot_locale.tag()),
                }
            }
            let win = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                // 启动画面同样带上语言：它比前端先出现，读不到 i18next
                tauri::WebviewUrl::App(format!("splash.html?lang={}", boot_locale.tag()).into()),
            )
            .title("Tavotto")
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
            spawn_e2e_update_if_requested(handle.clone());
            spawn_sidecar_and_navigate(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tavotto 桌面壳初始化失败");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            // 无论怎么退出（关窗、⌘Q、系统注销）都同步收掉 sidecar 与其子进程
            if let Some(state) = app_handle.try_state::<AppState>() {
                state.shutdown_sidecar();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    /// `.menu()` 的闭包里**绝不能碰 `handle.path()`**。
    ///
    /// 它跑在 Tauri manage `PathResolver` 之前，一碰就是
    /// `state() called before manage()` 的 panic。壳是 windows 子系统的
    /// 可执行文件，panic 写到无效的 stderr 句柄上——用户看到的只是「双击
    /// 图标什么都没发生」，退出码 101。这条在 2026-08-18 到 08-19 之间让
    /// Windows 桌面版每次启动都当场死掉，而唯一的线索是 nightly 里一句
    /// 「壳退了」。要读配置目录，去 `setup()` 里读。
    #[test]
    fn the_menu_builder_never_touches_the_path_resolver() {
        let src = include_str!("main.rs");
        let start = src
            .find("fn build_menu<R: tauri::Runtime>")
            .expect("build_menu 不见了");
        let body = &src[start..];
        let end = body.find("\n}\n").expect("找不到 build_menu 的结尾");
        let body = &body[..end];
        for line in body.lines() {
            let code = line.split("//").next().unwrap_or("");
            assert!(
                !code.contains(".path()"),
                "build_menu 里碰了 path()：那时 PathResolver 还没被 manage：{code}"
            );
        }
    }

    #[test]
    fn parses_the_handoff_contract() {
        let req = parse_open_args(&args(&["--open", "/p/figures", "--stem", "Fig1"])).unwrap();
        assert_eq!(req.project, "/p/figures");
        assert_eq!(req.stem.as_deref(), Some("Fig1"));
        assert_eq!(req.pick, None);
    }

    #[test]
    fn parses_the_multi_figure_pick() {
        // 多 Figure 交接：`--pick-script` 原样透传给前端选择器
        let req =
            parse_open_args(&args(&["--open", "/p/figures", "--pick-script", "sub/plot.py"]))
                .unwrap();
        assert_eq!(req.stem, None);
        assert_eq!(req.pick.as_deref(), Some("sub/plot.py"));
    }

    #[test]
    fn stem_wins_over_pick() {
        // 生产侧互斥；两个都来了以 stem 为准（定得下来一张就不需要选择器）
        let req = parse_open_args(&args(&[
            "--open",
            "/p",
            "--stem",
            "Fig1",
            "--pick-script",
            "plot.py",
        ]))
        .unwrap();
        assert_eq!(req.stem.as_deref(), Some("Fig1"));
        assert_eq!(req.pick, None);
    }

    #[test]
    fn stem_is_optional() {
        let req = parse_open_args(&args(&["--open", "/p/figures"])).unwrap();
        assert_eq!(req.stem, None);
    }

    #[test]
    fn ignores_unknown_arguments() {
        // macOS 从 Finder / Dock 启动会塞 -psn_0_12345；漏掉这条，
        // 双击图标启动会被当成一次「参数不认识」的失败。
        let req = parse_open_args(&args(&["-psn_0_12345", "--open", "/p", "--verbose"]));
        assert_eq!(req.unwrap().project, "/p");
    }

    #[test]
    fn no_open_flag_means_normal_launch() {
        assert!(parse_open_args(&args(&[])).is_none());
        assert!(parse_open_args(&args(&["--stem", "Fig1"])).is_none());
        assert!(parse_open_args(&args(&["--open"])).is_none()); // 值缺失
        assert!(parse_open_args(&args(&["--open", "  "])).is_none()); // 空白路径
    }

    #[test]
    fn blank_stem_is_dropped_not_forwarded() {
        // 空 stem 拼进 URL 就是 `?open=`，前端会去找一个叫空串的面板。
        let req = parse_open_args(&args(&["--open", "/p", "--stem", " "])).unwrap();
        assert_eq!(req.stem, None);
    }

    #[test]
    fn paths_with_spaces_and_cjk_survive() {
        let req = parse_open_args(&args(&["--open", "/用户/我的 图库", "--stem", "图 1"])).unwrap();
        assert_eq!(req.project, "/用户/我的 图库");
        assert_eq!(req.stem.as_deref(), Some("图 1"));
    }
}
