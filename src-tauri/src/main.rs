//! Tavotto 桌面壳：只做窗口、生命周期、菜单与安全边界，业务全在 Python sidecar。
//! 见 docs/adr/0002-tauri-desktop-shell.md。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod i18n;
mod sidecar;

use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

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
    /// 关窗询问闸（issue #223）。窗口关闭按钮 / Alt+F4 / 任务栏关闭都先经过它。
    close_gate: Mutex<CloseGate>,
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
    /// `tavotto run` 的一次性交接 ID（ADR 0021 §4）。**不透明串，不是凭据**
    /// ——token、端口、完整命令都在那份 0600 的 descriptor 文件里，argv 上
    /// 只有这个 ID（同机上 `ps` 对别的用户可见）。壳一个字都不解释，原样
    /// 送进落地 URL / `tavotto:open` 事件，确认界面在前端。
    ///
    /// 与 stem / pick **不互斥**：那两个说的是"打开哪张图"，这个说的是
    /// "有一条 native 会话在等你确认"。
    native: Option<String>,
}

/// 认不出的参数一律忽略：macOS 从 Finder / Dock 启动会塞 `-psn_0_12345`，
/// Windows 的关联启动会塞文件路径，这些都不该让交接解析失败。
fn parse_open_args(args: &[String]) -> Option<OpenRequest> {
    let mut project: Option<String> = None;
    let mut stem: Option<String> = None;
    let mut pick: Option<String> = None;
    let mut native: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--open" => project = it.next().cloned(),
            "--stem" => stem = it.next().cloned(),
            "--pick-script" => pick = it.next().cloned(),
            "--native-session" => native = it.next().cloned(),
            _ => {}
        }
    }
    let project = project?;
    if project.trim().is_empty() {
        return None;
    }
    let stem = stem.filter(|s| !s.trim().is_empty());
    // stem 定得下来一张就不需要选择器（生产侧本来就互斥，这里兜底同语义）
    let pick = pick
        .filter(|s| !s.trim().is_empty())
        .filter(|_| stem.is_none());
    // ID 的格式判据与 Python 侧（`nativehandoff._ID_RE`）同源：32 个小写
    // 十六进制字符。壳在这里挡一道，是因为它下一步要把这个串拼进落地 URL
    // ——一个含 `&` 或 `#` 的"ID"会把后面的查询参数整个改掉。
    let native = native
        .filter(|s| s.len() == 32 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')));
    Some(OpenRequest {
        project,
        stem,
        pick,
        native,
    })
}

/// 首启的落地 URL 查询串（不含前导 `?` 时为空串）。
///
/// **抽成函数是为了能被量到。** 这段以前长在 `setup` 的闭包里，谁也测不着
/// ——于是 `--native-session` 在这里被漏掉了整整一轮：壳把它解析出来了、
/// `tavotto:open` 事件也带着它，只有**首启**这一条路把它丢了。表现是
/// `tavotto run` 在 Tavotto 还没开着的时候唤起界面，窗口起来了、确认界面
/// 永远不出现，CLI 一直挂在 "Waiting for Tavotto desktop…" 上直到 attach
/// 超时，而两边都不报错。
///
/// 三个参数的语义与 `handoff.browser_url()` / `tavotto:open` 事件同源：
/// * `open=<stem>` 与 `pick=<脚本>` 互斥（定得下来一张就不需要选择器）；
/// * `native=<ID>` 与那两个**不互斥**——它说的是"有一条 native 会话在等
///   你确认"，不是"打开哪张图"；
/// * `lang=` 只在用户亲手选过语言时带。
fn landing_query(open: Option<&OpenRequest>, lang: Option<&str>) -> String {
    let mut params: Vec<String> = Vec::new();
    if let Some(stem) = open.and_then(|o| o.stem.as_deref()) {
        params.push(format!(
            "open={}",
            utf8_percent_encode(stem, NON_ALPHANUMERIC)
        ));
    } else if let Some(pick) = open.and_then(|o| o.pick.as_deref()) {
        // 多 Figure 交接：把脚本交给前端的 Figure 选择器
        // （与 handoff.browser_url 的 `?pick=` 同一份语义）
        params.push(format!(
            "pick={}",
            utf8_percent_encode(pick, NON_ALPHANUMERIC)
        ));
    }
    if let Some(native) = open.and_then(|o| o.native.as_deref()) {
        // `parse_open_args` 已经把它限成 32 位小写十六进制，编码在这里是
        // 恒等的——留着是因为那道格式判据将来一旦放宽，这里不该跟着变成
        // 一个注入点。
        params.push(format!(
            "native={}",
            utf8_percent_encode(native, NON_ALPHANUMERIC)
        ));
    }
    if let Some(tag) = lang {
        params.push(format!("lang={tag}"));
    }
    if params.is_empty() {
        String::new()
    } else {
        format!("?{}", params.join("&"))
    }
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

/// 「安装 Codex 集成」/「重新诊断」——**壳里没有第二套安装器**（ADR 0012）。
///
/// 这个命令的全部职责是 spawn `tavotto-cli codex <action> --json`，把它打出来的
/// 那一行 JSON 原样交给前端渲染。marketplace / 插件 / 引擎 / 体检四步一条都不在
/// 这里：安装器只有 `engine/codexinstall.py` 那一份，按钮与终端命令永远走同一条
/// 实现（看护 `tests/test_desktop_codex_button.py`）。
///
/// `action` 是**闭集**（`install` / `doctor`）——webview 递不进任意 argv，也递不进
/// `uninstall`：卸载不该是一个按得动的按钮。
///
/// **失败也是一行 JSON**（引擎的 `--json` 纪律），所以这里不看退出码，只找 stdout
/// 里最后那行 JSON。真的一行都没有（CLI 没找到 / spawn 不起来 / 输出被截断）才回
/// `Err`，回的是**稳定 code**，由前端翻成人话——英文 code 不进界面。
#[tauri::command]
async fn codex_integration(app: tauri::AppHandle, action: String) -> Result<String, String> {
    if action != "install" && action != "doctor" {
        return Err("bad_action".into());
    }
    let resource_dir = app.path().resource_dir().ok();
    tauri::async_runtime::spawn_blocking(move || {
        let cli = sidecar::resolve_cli(resource_dir.as_deref())?;
        let mut cmd = std::process::Command::new(&cli);
        cmd.arg("codex")
            .arg(&action)
            .arg("--json")
            // 安装要拉一次稀疏检出，可能跑上几分钟。stdin 给 null：这条命令
            // 刻意不是交互向导，等在一个没人接的提示上等于挂死。
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let out = cmd.output().map_err(|_| "spawn_failed".to_string())?;
        let stdout = String::from_utf8_lossy(&out.stdout);
        stdout
            .lines()
            .rev()
            .map(str::trim)
            .find(|l| l.starts_with('{'))
            .map(str::to_string)
            .ok_or_else(|| "bad_output".to_string())
    })
    .await
    .map_err(|_| "spawn_failed".to_string())?
}

/* -------------------------------------------------------------------------- */
/*  关窗询问闸（issue #223）                                                    */
/* -------------------------------------------------------------------------- */

/// 前端**确认收到**这一次询问的时限。超过它就当 webview 已经答不上话
/// （JS 崩了、主线程卡死、页面是 splash/error 那种没有监听器的壳内页），
/// 放行关闭——退回改造前的行为（磁盘自动保存 + 本机崩溃恢复副本兜底）。
///
/// **这不是用户思考的时限**：前端一收到事件就先答一句 `hold` 表示「我接手了，
/// 正在问用户」，此后用户想多久都行。把两件事分成两步，正是为了让这个超时
/// 可以短到用户察觉不到，同时又不会在用户读对话框时把窗口关掉。
const CLOSE_ACK_TIMEOUT: Duration = Duration::from_millis(2000);

/// 前端对一次关窗询问的答复。**闭集**，与 `web/src/lib/desktop.ts` 的
/// `CloseDecision` 严格同源（`tests/test_desktop_close_guard.py` 逐个比）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CloseDecision {
    /// 我接手了，正在问用户——别再等我，也别强关。
    Hold,
    /// 关吧。
    Close,
    /// 用户改主意了，窗口留着。
    Cancel,
}

impl CloseDecision {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "hold" => Some(Self::Hold),
            "close" => Some(Self::Close),
            "cancel" => Some(Self::Cancel),
            _ => None,
        }
    }
}

/// 壳对一次 `CloseRequested` 的裁决。
#[derive(Debug, PartialEq, Eq)]
enum CloseVerdict {
    /// 放行。
    Close,
    /// 拦住并问前端；带上这一次的代号，看门狗只对自己那一代负责。
    Ask(u64),
}

/// 待决询问的状态。
///
/// **「没人接手」「接手了说继续关」「接手了说取消」是三件不同的事**，看门狗
/// 必须分得出来。把它压成一个 `acknowledged: bool` 就分不出第三种：用户在 2 秒内
/// 点了「取消」会把那一位重置成 false，而**那一次请求的看门狗还在睡**——它醒来
/// 看到 false 就按「前端没接手」放行，于是用户明明点了取消、窗口照样被关掉。
/// 取消也是一种接手：用户表了态。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
enum CloseAsk {
    /// 没有待决的询问（开机态，也是「取消」之后的落点）。
    #[default]
    Idle,
    /// 第 n 代正在等前端说第一句话。**只有这一档的看门狗会开火。**
    Waiting(u64),
    /// 第 n 代已被前端接手（正在问用户）。用户想多久都行。
    Held(u64),
    /// 已经答复「关」。`window.close()` 触发的第二次 `CloseRequested` 靠它放行。
    Confirmed,
}

/// 「关窗前问一句」的闸。
///
/// **默认不拦**（`armed == false`）：只有前端亲口说过「我在，我能答」
/// （`arm_close_guard`）之后才拦。壳自带的 splash / error 页在 `tauri://` 源下，
/// 既没有 i18next 也没有这个监听器——那时候点关闭必须当场关掉，等两秒看门狗
/// 的「按了没反应」比不问更坏。
#[derive(Default)]
struct CloseGate {
    armed: bool,
    /// 每一次 `CloseRequested` 取一个新代号。取消后再点一次关闭是**新的一代**，
    /// 上一代的看门狗醒来时会发现自己那一代已经不在 `Waiting` 上了。
    next_generation: u64,
    ask: CloseAsk,
}

impl CloseGate {
    fn on_close_requested(&mut self) -> CloseVerdict {
        if !self.armed || self.ask == CloseAsk::Confirmed {
            return CloseVerdict::Close;
        }
        self.next_generation += 1;
        self.ask = CloseAsk::Waiting(self.next_generation);
        CloseVerdict::Ask(self.next_generation)
    }

    /// 前端的答复。返回 true = 现在就关。
    fn resolve(&mut self, decision: CloseDecision) -> bool {
        match decision {
            // 接手：看门狗从此不对这一代负责。答复迟到（此刻已经 Idle）就丢掉,
            // 别把一次已经被取消的询问重新挂起来。
            CloseDecision::Hold => {
                if let CloseAsk::Waiting(g) | CloseAsk::Held(g) = self.ask {
                    self.ask = CloseAsk::Held(g);
                }
                false
            }
            CloseDecision::Close => {
                self.ask = CloseAsk::Confirmed;
                true
            }
            // **取消把这一代结掉。** 回到 Idle 之后没有任何代号还在 `Waiting`,
            // 睡着的那条看门狗醒来什么都不会做——这正是「用户点了取消、窗口
            // 却在两秒后自己关掉」那个缺陷的修法。
            CloseDecision::Cancel => {
                self.ask = CloseAsk::Idle;
                false
            }
        }
    }

    /// 看门狗到点。返回 true = 这一代确实没人接手，强关。
    fn watchdog_fires(&mut self, generation: u64) -> bool {
        if self.ask != CloseAsk::Waiting(generation) {
            return false;
        }
        self.ask = CloseAsk::Confirmed;
        true
    }
}

/// 前端就绪：从现在起关窗先问它。
///
/// **必须在监听器注册之后才调**——反过来的话，两者之间的那次关闭会拦下一个
/// 没人听的问题，白等一个看门狗。
#[tauri::command]
fn arm_close_guard(app: tauri::AppHandle) {
    app.state::<AppState>().close_gate.lock().unwrap().armed = true;
}

/// 前端对 `tavotto:close-requested` 的答复。
#[tauri::command]
fn resolve_close_request(app: tauri::AppHandle, decision: String) -> Result<(), String> {
    let Some(decision) = CloseDecision::parse(&decision) else {
        return Err(format!("未知的关窗答复：{decision}"));
    };
    // 锁在这条语句结束就还回去：`close()` 会同步走一遍窗口事件，握着锁进去
    // 等于自己和自己抢。
    let close_now = app
        .state::<AppState>()
        .close_gate
        .lock()
        .unwrap()
        .resolve(decision);
    if close_now {
        if let Some(win) = app.get_webview_window("main") {
            win.close().map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

/// 拦一次窗口要做的三件事。**收进一个 trait，是为了让「拦了就一定起了看门狗」
/// 成为一条能跑的断言，而不是在源码文本里搜一个 token。**
///
/// 文本判据的天花板在这里：`if false { spawn_close_watchdog(…) }` 也含那个
/// token，剥掉注释也照样通过——而它守的正是「没有看门狗 = 关不掉的窗口」。
/// 换成行为判据之后，假的实现在录到的动作里当场缺一项（见本文件末尾的
/// `holding_a_window_always_arms_a_watchdog_for_the_same_generation`）。
trait CloseHold {
    /// 别关，我还没问完。
    fn prevent_close(&self);
    /// 问前端：有没有没落盘的工作？
    fn ask_frontend(&self);
    /// 没人应答时的兜底。
    fn arm_watchdog(&self, generation: u64);
}

/// **拦窗口的唯一入口。** 三件事在同一个函数里按同一个代号发生；
/// `api.prevent_close()` 在整个壳里只出现在这个 trait 的实现里
/// （`tests/test_desktop_close_guard.py` 数它出现几次）。
fn hold_window<H: CloseHold + ?Sized>(hold: &H, generation: u64) {
    hold.prevent_close();
    hold.ask_frontend();
    hold.arm_watchdog(generation);
}

/// 生产实现。这层适配器（把三件事接到真的 Tauri 对象上）是这条路上唯一没有
/// 单测覆盖的一小段——它没有分支，全部逻辑在 `CloseGate` 与 `hold_window` 里。
struct TauriCloseHold<'a> {
    api: &'a tauri::CloseRequestApi,
    app: tauri::AppHandle,
}

impl CloseHold for TauriCloseHold<'_> {
    fn prevent_close(&self) {
        self.api.prevent_close();
    }

    fn ask_frontend(&self) {
        // 发不出去也不特殊处理：看门狗是这条路上唯一的兜底，让它只有一条。
        let _ = self.app.emit_to("main", "tavotto:close-requested", ());
    }

    fn arm_watchdog(&self, generation: u64) {
        spawn_close_watchdog(self.app.clone(), generation);
    }
}

/// 起一条看门狗：`CLOSE_ACK_TIMEOUT` 之后这一代还**停在 `Waiting` 上**就强关。
///
/// 没有它，一个卡死的 webview 就是一个**关不掉的窗口**——那比「关窗不提示」
/// 坏得多，用户只能去杀进程，而杀进程连自动保存的防抖窗口都保不住。
fn spawn_close_watchdog(app: tauri::AppHandle, generation: u64) {
    std::thread::spawn(move || {
        std::thread::sleep(CLOSE_ACK_TIMEOUT);
        let force = app
            .try_state::<AppState>()
            .is_some_and(|s| s.close_gate.lock().unwrap().watchdog_fires(generation));
        if force {
            eprintln!("[close-guard] 前端未在 {CLOSE_ACK_TIMEOUT:?} 内应答，放行关闭");
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.close();
            }
        }
    });
}

/// 窗口关闭按钮 / Alt+F4 / 任务栏关闭 → 先问前端有没有没落盘的工作。
///
/// **⌘Q 与系统注销不走这里**（那是 `RunEvent::ExitRequested`），仍然只有
/// 自动保存 + 崩溃恢复副本兜底——见 ADR 0002 的「关窗询问闸」一节。
fn on_close_requested(window: &tauri::Window, api: &tauri::CloseRequestApi) {
    let app = window.app_handle().clone();
    let verdict = match app.try_state::<AppState>() {
        Some(state) => state.close_gate.lock().unwrap().on_close_requested(),
        None => CloseVerdict::Close,
    };
    let CloseVerdict::Ask(generation) = verdict else {
        return;
    };
    hold_window(&TauriCloseHold { api, app }, generation);
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
                let query = landing_query(
                    open.as_ref(),
                    chosen_locale.is_some().then(|| menu_locale.tag()),
                );
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
    eprintln!(
        "[e2e-update] ⚠ TAVOTTO_E2E_RUN_UPDATE=1：启动即执行应用内更新（仅测试用，勿在生产设置）"
    );
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
        // 默认**不拦**：前端注册好监听器后自己来 arm。
        close_gate: Mutex::new(CloseGate::default()),
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
        .invoke_handler(tauri::generate_handler![
            reveal_export,
            set_menu_locale,
            codex_integration,
            arm_close_guard,
            resolve_close_request
        ])
        .on_window_event(|window, event| {
            // 只看主窗口：壳只有这一个，但事件回调是全局的。
            if window.label() != "main" {
                return;
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                on_close_requested(window, api);
            }
        })
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
        let req = parse_open_args(&args(&[
            "--open",
            "/p/figures",
            "--pick-script",
            "sub/plot.py",
        ]))
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
    fn parses_the_native_session_handoff() {
        // ADR 0021：`tavotto run` 的交接。**与 handoff.desktop_argv() 严格同源**
        // （两侧各有一条用例，改一处必须改两处）。
        let id = "0123456789abcdef0123456789abcdef";
        let req = parse_open_args(&args(&["--open", "/p", "--native-session", id])).unwrap();
        assert_eq!(req.native.as_deref(), Some(id));
        assert_eq!(req.stem, None);
    }

    #[test]
    fn native_session_coexists_with_stem() {
        // 这两个**不互斥**：一次交接完全可以既打开某张图、又带一条待确认的
        // native 会话。把它写成互斥（照抄 stem/pick 那条）会让 UI 二选一。
        let id = "0123456789abcdef0123456789abcdef";
        let req = parse_open_args(&args(&[
            "--open",
            "/p",
            "--stem",
            "Fig1",
            "--native-session",
            id,
        ]))
        .unwrap();
        assert_eq!(req.stem.as_deref(), Some("Fig1"));
        assert_eq!(req.native.as_deref(), Some(id));
    }

    #[test]
    fn a_malformed_native_session_id_is_dropped_not_forwarded() {
        // 这个串下一步会被拼进落地 URL。含 `&` / `#` 的"ID"会把后面的查询
        // 参数整个改掉，而它来自 argv——任何人都能往一个正在跑的实例转发。
        for bad in [
            "",
            "  ",
            "0123456789abcdef0123456789abcde",   // 31 位
            "0123456789abcdef0123456789abcdefa", // 33 位
            "0123456789ABCDEF0123456789abcdef",  // 大写
            "0123456789abcdef0123456789abcde&",  // 有 `&`
            "../../etc/passwd0000000000000000",
        ] {
            let req = parse_open_args(&args(&["--open", "/p", "--native-session", bad])).unwrap();
            assert_eq!(req.native, None, "不该被转发: {bad:?}");
        }
    }

    #[test]
    fn stem_is_optional() {
        let req = parse_open_args(&args(&["--open", "/p/figures"])).unwrap();
        assert_eq!(req.stem, None);
    }

    /// 首启这条路曾经把 `native` 丢掉：解析出来了、事件里也有，只有落地 URL
    /// 没带——`tavotto run` 在 Tavotto 没开着时唤起界面，窗口起来了但确认
    /// 界面永远不出现，CLI 挂到 attach 超时，两边都不报错。
    #[test]
    fn the_landing_url_carries_the_native_session() {
        let id = "0123456789abcdef0123456789abcdef";
        let req = parse_open_args(&args(&["--open", "/p", "--native-session", id])).unwrap();
        let q = landing_query(Some(&req), None);
        assert_eq!(q, format!("?native={id}"));
    }

    #[test]
    fn the_landing_url_carries_a_figure_and_a_native_session_together() {
        // 两者不互斥：这一次交接既要打开 Fig1，又有一条会话在等确认。
        let id = "0123456789abcdef0123456789abcdef";
        let req = parse_open_args(&args(&[
            "--open",
            "/p",
            "--stem",
            "Fig1",
            "--native-session",
            id,
        ]))
        .unwrap();
        let q = landing_query(Some(&req), Some("zh-CN"));
        assert_eq!(q, format!("?open=Fig1&native={id}&lang=zh-CN"));
    }

    #[test]
    fn the_landing_url_percent_encodes_the_pick_script() {
        // 脚本相对路径里有 `/`，原样拼进查询串会被前端解成另一个参数边界。
        let req =
            parse_open_args(&args(&["--open", "/p", "--pick-script", "sub/plot.py"])).unwrap();
        assert_eq!(landing_query(Some(&req), None), "?pick=sub%2Fplot%2Epy");
    }

    #[test]
    fn a_plain_launch_has_no_query_at_all() {
        // 没有交接、也没亲手选过语言时不该冒出一个空的 `?`——那会让
        // 「地址栏里有没有参数」这个判据在正常启动上就已经是真的。
        assert_eq!(landing_query(None, None), "");
        let req = parse_open_args(&args(&["--open", "/p"])).unwrap();
        assert_eq!(landing_query(Some(&req), None), "");
        assert_eq!(landing_query(Some(&req), Some("en-US")), "?lang=en-US");
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

    /* ---------------------------------------------------------------- */
    /*  关窗询问闸（issue #223）                                          */
    /* ---------------------------------------------------------------- */

    fn armed_gate() -> CloseGate {
        CloseGate {
            armed: true,
            ..Default::default()
        }
    }

    /// 起一次询问，返回它的代号。
    fn ask(gate: &mut CloseGate) -> u64 {
        match gate.on_close_requested() {
            CloseVerdict::Ask(g) => g,
            CloseVerdict::Close => panic!("armed 的闸该问一句"),
        }
    }

    #[test]
    fn an_unarmed_gate_never_holds_the_window() {
        // splash / error 页在 `tauri://` 源下，没有那个监听器。在它们上面
        // 拦一下等于让关闭按钮「按了没反应」两秒——那比不问更坏。
        let mut gate = CloseGate::default();
        assert_eq!(gate.on_close_requested(), CloseVerdict::Close);
        // 而且不该留下待决的一代：看门狗没起，代号也就不该往前走。
        assert_eq!(gate.ask, CloseAsk::Idle);
        assert_eq!(gate.next_generation, 0);
    }

    #[test]
    fn an_armed_gate_asks_the_frontend_first() {
        let mut gate = armed_gate();
        assert_eq!(gate.on_close_requested(), CloseVerdict::Ask(1));
        assert_eq!(gate.ask, CloseAsk::Waiting(1));
    }

    #[test]
    fn the_confirmed_close_is_let_through_on_the_second_pass() {
        // `resolve("close")` 之后壳自己调 `window.close()`，那一下会**再**触发
        // 一次 CloseRequested。这一次必须放行，否则就是一个关不掉的窗口。
        let mut gate = armed_gate();
        ask(&mut gate);
        assert!(gate.resolve(CloseDecision::Close));
        assert_eq!(gate.on_close_requested(), CloseVerdict::Close);
    }

    #[test]
    fn cancel_keeps_the_window_and_the_next_press_asks_again() {
        let mut gate = armed_gate();
        assert_eq!(ask(&mut gate), 1);
        assert!(!gate.resolve(CloseDecision::Hold));
        assert!(!gate.resolve(CloseDecision::Cancel));
        // 取消不是「以后都别问了」
        assert_eq!(ask(&mut gate), 2);
    }

    /* --- 看门狗必须分得出三件事：没人接手 / 说继续关 / 说取消 --- */

    #[test]
    fn the_watchdog_forces_a_close_when_nobody_answers() {
        // webview 卡死 / JS 崩了：没人会 hold，也没人会 close。兜底必须存在，
        // 否则窗口关不掉，用户只能杀进程——那连防抖窗口内的编辑都保不住。
        let mut gate = armed_gate();
        let g = ask(&mut gate);
        assert!(gate.watchdog_fires(g));
        // 强关也走「已确认」那条路：随后的 close() 会再触发一次 CloseRequested。
        assert_eq!(gate.on_close_requested(), CloseVerdict::Close);
    }

    #[test]
    fn the_watchdog_does_not_close_a_window_the_user_is_still_deciding_on() {
        // 超时的主语是**「前端有没有接手」**，不是「用户有没有回答」。量错了
        // 主语，用户读对话框读到第三秒，窗口就在他面前关掉了。
        let mut gate = armed_gate();
        let g = ask(&mut gate);
        assert!(!gate.resolve(CloseDecision::Hold));
        assert!(!gate.watchdog_fires(g));
    }

    #[test]
    fn a_cancelled_request_is_never_closed_by_its_own_sleeping_watchdog() {
        // **取消也是一种接手：用户表了态。** 这一位曾经是错的——`acknowledged`
        // 被 Cancel 重置成 false，而那次请求的看门狗还在睡，醒来看到 false 就
        // 按「没人接手」放行：用户明明点了取消，窗口两秒后自己关掉。
        let mut gate = armed_gate();
        let g = ask(&mut gate);
        assert!(!gate.resolve(CloseDecision::Hold));
        assert!(!gate.resolve(CloseDecision::Cancel));
        assert!(
            !gate.watchdog_fires(g),
            "取消掉的那一代不该被自己的看门狗关掉"
        );
        assert_eq!(gate.ask, CloseAsk::Idle);
    }

    #[test]
    fn cancelling_before_the_hold_also_settles_the_generation() {
        // 前端有可能一步到位（没有 hold 直接 cancel）。那一代同样该结掉。
        let mut gate = armed_gate();
        let g = ask(&mut gate);
        assert!(!gate.resolve(CloseDecision::Cancel));
        assert!(!gate.watchdog_fires(g));
    }

    #[test]
    fn a_late_hold_after_a_cancel_does_not_revive_the_request() {
        // 迟到的答复不该把一次已经取消的询问重新挂起来——挂起来之后
        // 那一代又变成「有人在问用户」，而其实没有任何对话框在。
        let mut gate = armed_gate();
        ask(&mut gate);
        assert!(!gate.resolve(CloseDecision::Cancel));
        assert!(!gate.resolve(CloseDecision::Hold));
        assert_eq!(gate.ask, CloseAsk::Idle);
    }

    #[test]
    fn a_stale_watchdog_never_closes_a_later_generation() {
        // 取消之后再点一次关闭：上一代的看门狗还在睡，醒来时不该把
        // 这一代（用户可能正在读对话框）的窗口关掉。
        let mut gate = armed_gate();
        let first = ask(&mut gate);
        assert!(!gate.resolve(CloseDecision::Cancel));
        let second = ask(&mut gate);
        assert_ne!(first, second);
        assert!(!gate.watchdog_fires(first));
        assert_eq!(gate.ask, CloseAsk::Waiting(second));
    }

    #[test]
    fn the_decision_vocabulary_is_a_closed_set() {
        // 前端拼错一个词不该被当成「关吧」。
        assert_eq!(CloseDecision::parse("hold"), Some(CloseDecision::Hold));
        assert_eq!(CloseDecision::parse("close"), Some(CloseDecision::Close));
        assert_eq!(CloseDecision::parse("cancel"), Some(CloseDecision::Cancel));
        for bad in ["", "Close", "closed", "ok", "true", "discard"] {
            assert_eq!(CloseDecision::parse(bad), None, "{bad} 不该被认下来");
        }
    }

    /* --- 拦窗口这件事本身：**行为**判据，不是源码里搜 token --- */

    #[derive(Default)]
    struct RecordingHold {
        acts: std::cell::RefCell<Vec<String>>,
    }

    impl CloseHold for RecordingHold {
        fn prevent_close(&self) {
            self.acts.borrow_mut().push("prevent".into());
        }
        fn ask_frontend(&self) {
            self.acts.borrow_mut().push("ask".into());
        }
        fn arm_watchdog(&self, generation: u64) {
            self.acts
                .borrow_mut()
                .push(format!("watchdog:{generation}"));
        }
    }

    #[test]
    fn holding_a_window_always_arms_a_watchdog_for_the_same_generation() {
        // 这条替掉了原先「在 main.rs 文本里搜 `spawn_close_watchdog(`」那条判据。
        // 那是个空门禁：`if false { spawn_close_watchdog(…) }` 照样含那个 token，
        // 剥掉注释也拦不住——而它守的正是「没有看门狗 = 关不掉的窗口」。
        // 现在录的是**真的发生了什么**：少一项、或者代号对不上，当场红。
        let hold = RecordingHold::default();
        hold_window(&hold, 7);
        assert_eq!(
            *hold.acts.borrow(),
            vec![
                "prevent".to_string(),
                "ask".to_string(),
                "watchdog:7".to_string()
            ],
            "拦窗口必须是「拦 + 问 + 起看门狗」三件事，且看门狗认的是同一代"
        );
    }

    #[test]
    fn the_watchdog_is_armed_for_the_generation_the_gate_handed_out() {
        // 代号错位的表现最隐蔽：看门狗永远开不了火（守着一个不存在的代），
        // 于是「有兜底」这件事在真机上是假的，而上面那条 vec 断言里的
        // 数字若被写死成常量也发现不了——所以这里的代号取自闸本身。
        let mut gate = armed_gate();
        let g = ask(&mut gate);
        let hold = RecordingHold::default();
        hold_window(&hold, g);
        assert!(hold.acts.borrow().contains(&format!("watchdog:{g}")));
        // 而这个代号确实是看门狗开得了火的那一个
        assert!(gate.watchdog_fires(g));
    }
}
