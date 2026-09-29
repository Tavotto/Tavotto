//! Tavotto 桌面壳：只做窗口、生命周期、菜单与安全边界，业务全在 Python sidecar。
//! 见 docs/adr/0002-tauri-desktop-shell.md。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// 分派只有 macOS 的 native_drop 在用；别的平台上它只剩单测（分派规则各平台一样，照样跑）
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
mod drop_paths;
mod i18n;
#[cfg(target_os = "macos")]
mod native_drop;
mod remote;
mod sidecar;

use std::collections::HashMap;
use std::fmt::Write as _;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use percent_encoding::{utf8_percent_encode, NON_ALPHANUMERIC};
use tauri::menu::{
    AboutMetadata, AboutMetadataBuilder, Menu, MenuItemBuilder, PredefinedMenuItem, Submenu,
};
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
    /// 关窗询问闸（issue #223），**每个窗口一道**（键是窗口 label）：主窗口与远程实例
    /// 窗口各自的前端各自 arm、各自答复。窗口关闭按钮 / Alt+F4 / 任务栏关闭都先经过它。
    close_gates: Mutex<HashMap<String, CloseGate>>,
    /// 远程实例窗口此刻停在哪个本机端口上（ADR 0105）。没连上 / 窗口不在时是 None，
    /// 那时远程窗口的导航守卫只放行壳自带页面。
    remote_port: Arc<Mutex<Option<u16>>>,
}

/// 主窗口：内容来自壳自己拉起的本机 sidecar。
const MAIN_WINDOW: &str = "main";
/// 远程实例窗口（ADR 0105）：内容来自经 `ssh -L` 转发过来的服务器上的引擎。
/// **ACL 按这个 label 给权限**（`capabilities/remote.json`）——它拿不到任何本机文件类命令。
const REMOTE_WINDOW: &str = "remote";
/// 注进远程实例窗口的标记。**与 `web/src/lib/desktop.ts` 的 `isRemoteEngineWindow`
/// 读的全局名严格同源**（`tests/test_desktop_remote.py` 比两侧）：前端据它把本机文件类
/// 能力全部让给浏览器模式的回退。
const REMOTE_WINDOW_MARKER: &str = "window.__TAVOTTO_REMOTE_ENGINE__ = true;";

/// 有关窗询问闸的窗口。别的 label（将来可能有的对话框窗口）一律不拦。
fn guarded_window(label: &str) -> bool {
    label == MAIN_WINDOW || label == REMOTE_WINDOW
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

/// 导航守卫（每个窗口一份，`port` 是**这个窗口**的引擎端口）：
/// - 壳自带页面（splash/error/connect，tauri://localhost 或 http://tauri.localhost）放行；
/// - 引擎源上只放行 SPA 根路径（应用是单页的，任何其它整页导航都不对——
///   /exports 等由前端走原生「在文件夹中显示」，不允许把主窗口导航成 PDF 视图）；
///   主窗口的引擎是本机 sidecar，远程实例窗口的是转发端口（ADR 0105）——
///   两者都只可能在 127.0.0.1 上，别的回环端口一律不许停；
/// - 其余 http(s)/mailto 一律交给系统默认程序打开，绝不在 WebView 里加载外部网页。
fn navigation_allowed(url: &url::Url, port: Option<u16>) -> NavDecision {
    if url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost") {
        return NavDecision::Allow;
    }
    if url.scheme() == "http" && url.host_str() == Some("127.0.0.1") {
        if let Some(p) = port {
            if url.port() == Some(p) {
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
/// 文件名不得含路径分隔符。暴露给（本机 sidecar 页面的）IPC 的文件类能力只有两条，
/// 都只 reveal 不 open：这一条与下面的 `reveal_project_dir`。
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

/// 主页拖放能不能拿到真实路径（ADR 0092）：macOS 上旁听装好了才是 true，其余平台
/// 一律 false，前端据此走「提示文件名 + 选择器」的降级。只读、无参数。
#[tauri::command]
fn native_file_drop() -> bool {
    #[cfg(target_os = "macos")]
    {
        native_drop::installed()
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// 左栏工作区右键「在 Finder 中打开」：在文件管理器里打开项目文件夹。
///
/// 项目里有脚本就选中（按名字排第一个的）顶层 `.py`——文件管理器开的正是项目文件夹；
/// 一个都没有就选中项目文件夹本身（开的是它的上一级）。
///
/// 只 reveal、从不 open：macOS 上 `.app` 也是目录，`open_path` 一个目录会把它当应用
/// 启动——webview 递进来的路径只配「在文件夹中显示」这一种效果。路径必须是绝对路径、
/// 此刻真实存在的目录（先 canonicalize，符号链接落到它指向的地方）。失败回稳定 code，
/// 前端不显示原文，只说「没能打开」并给出完整路径。
#[tauri::command]
fn reveal_project_dir(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let dir = checked_project_dir(&path)?;
    app.opener()
        .reveal_item_in_dir(reveal_target(&dir))
        .map_err(|e| e.to_string())
}

/// 要选中的那一项：顶层第一个（按文件名）非隐藏的 `.py` 文件，没有就是目录本身。
fn reveal_target(dir: &std::path::Path) -> PathBuf {
    let mut scripts: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.is_file()
                && p.extension().is_some_and(|x| x.eq_ignore_ascii_case("py"))
                && !p
                    .file_name()
                    .is_some_and(|n| n.to_string_lossy().starts_with('.'))
        })
        .collect();
    scripts.sort();
    scripts
        .into_iter()
        .next()
        .unwrap_or_else(|| dir.to_path_buf())
}

fn checked_project_dir(path: &str) -> Result<PathBuf, String> {
    let raw = PathBuf::from(path);
    if path.is_empty() || !raw.is_absolute() {
        return Err("not_absolute".into());
    }
    let dir = std::fs::canonicalize(&raw).map_err(|_| "not_found".to_string())?;
    if !dir.is_dir() {
        return Err("not_a_directory".into());
    }
    Ok(dir)
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
///
/// 闸按**发起调用的那个窗口**算：主窗口与远程实例窗口各一道（ADR 0105）。
#[tauri::command]
fn arm_close_guard(app: tauri::AppHandle, window: tauri::Window) {
    let label = window.label();
    if !guarded_window(label) {
        return;
    }
    app.state::<AppState>()
        .close_gates
        .lock()
        .unwrap()
        .entry(label.to_string())
        .or_default()
        .armed = true;
}

/// 前端对 `tavotto:close-requested` 的答复。只作用于发起调用的那个窗口。
#[tauri::command]
fn resolve_close_request(
    app: tauri::AppHandle,
    window: tauri::Window,
    decision: String,
) -> Result<(), String> {
    let Some(decision) = CloseDecision::parse(&decision) else {
        return Err(format!("未知的关窗答复：{decision}"));
    };
    let label = window.label().to_string();
    // 锁在这条语句结束就还回去：`close()` 会同步走一遍窗口事件，握着锁进去
    // 等于自己和自己抢。
    let close_now = app
        .state::<AppState>()
        .close_gates
        .lock()
        .unwrap()
        .get_mut(&label)
        .is_some_and(|gate| gate.resolve(decision));
    if close_now {
        if let Some(win) = app.get_webview_window(&label) {
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
    /// 被拦的是哪个窗口：询问只发给它，看门狗也只关它。
    label: String,
}

impl CloseHold for TauriCloseHold<'_> {
    fn prevent_close(&self) {
        self.api.prevent_close();
    }

    fn ask_frontend(&self) {
        // 发不出去也不特殊处理：看门狗是这条路上唯一的兜底，让它只有一条。
        let _ = self
            .app
            .emit_to(self.label.as_str(), "tavotto:close-requested", ());
    }

    fn arm_watchdog(&self, generation: u64) {
        spawn_close_watchdog(self.app.clone(), self.label.clone(), generation);
    }
}

/// 起一条看门狗：`CLOSE_ACK_TIMEOUT` 之后这一代还**停在 `Waiting` 上**就强关。
///
/// 没有它，一个卡死的 webview 就是一个**关不掉的窗口**——那比「关窗不提示」
/// 坏得多，用户只能去杀进程，而杀进程连自动保存的防抖窗口都保不住。
fn spawn_close_watchdog(app: tauri::AppHandle, label: String, generation: u64) {
    std::thread::spawn(move || {
        std::thread::sleep(CLOSE_ACK_TIMEOUT);
        let force = app.try_state::<AppState>().is_some_and(|s| {
            s.close_gates
                .lock()
                .unwrap()
                .get_mut(&label)
                .is_some_and(|gate| gate.watchdog_fires(generation))
        });
        if force {
            eprintln!("[close-guard] {label} 的前端未在 {CLOSE_ACK_TIMEOUT:?} 内应答，放行关闭");
            if let Some(win) = app.get_webview_window(&label) {
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
    let label = window.label().to_string();
    let verdict = match app.try_state::<AppState>() {
        // 这个窗口的前端还没 arm 过（splash / connect 页、老前端）就没有闸：放行
        Some(state) => state
            .close_gates
            .lock()
            .unwrap()
            .get_mut(&label)
            .map_or(CloseVerdict::Close, CloseGate::on_close_requested),
        None => CloseVerdict::Close,
    };
    let CloseVerdict::Ask(generation) = verdict else {
        return;
    };
    hold_window(&TauriCloseHold { api, app, label }, generation);
}

/// 仓库地址：帮助菜单的两条链接与「关于」里的网站都从它派生。
///
/// **不在这里手写**：`build.rs` 编译时从品牌常量的唯一出处
/// `web/src/lib/brand.ts`（与 `engine/brand.py` 同源）读出来注入——壳在 webview
/// 起来之前就要建菜单，运行时读不到前端的常量。
const REPO_URL: &str = env!("TAVOTTO_REPO_URL");

/// 由壳自己处理的帮助链接（不转发给前端）：这两条在前端还没起来、sidecar 起不来
/// 的时候最有用，而前端也没有开外部链接的权限（capability 不开放任意 opener）。
/// 其余 `menu-*` 一律经 `tavotto:menu` 交给前端。
fn help_link(id: &str) -> Option<String> {
    match id {
        "help-docs" => Some(format!("{REPO_URL}#readme")),
        "help-report-issue" => Some(format!("{REPO_URL}/issues/new/choose")),
        _ => None,
    }
}

/// 「连接远程实例…」：壳自己开远程实例窗口（ADR 0105），不转发给前端——主窗口的前端
/// 起不来时它照样要能用，而且开窗口本来就是壳的事。
const CONNECT_REMOTE_ID: &str = "shell-connect-remote";

/// 壳自己处理的菜单项。**`menu_spec` 里每个非 `menu-*` 的 id 都必须在这里有着落**
/// （单测 `menu_ids_match_the_golden_pair_on_both_platforms` 逐个查），否则就是一个
/// 点了没反应的菜单项。
#[derive(Debug, PartialEq, Eq)]
enum ShellAction {
    OpenUrl(String),
    ConnectRemote,
}

fn shell_action(id: &str) -> Option<ShellAction> {
    if id == CONNECT_REMOTE_ID {
        return Some(ShellAction::ConnectRemote);
    }
    help_link(id).map(ShellAction::OpenUrl)
}

/// 菜单动作发给哪个窗口：**当前聚焦的那个**（远程实例窗口在前台时 ⌘S 存的是远程
/// 的排版）。谁都没聚焦（菜单栏在 macOS 上可以脱离窗口被点）就给主窗口。
fn menu_target(focused: &[&str]) -> &'static str {
    if focused.contains(&REMOTE_WINDOW) {
        REMOTE_WINDOW
    } else {
        MAIN_WINDOW
    }
}

fn on_menu_event(app: &tauri::AppHandle, id: &str) {
    match shell_action(id) {
        Some(ShellAction::OpenUrl(url)) => {
            if let Err(e) = app.opener().open_url(url, None::<&str>) {
                eprintln!("open help link {id}: {e}");
            }
            return;
        }
        Some(ShellAction::ConnectRemote) => {
            if let Err(e) = open_remote_window(app) {
                eprintln!("open remote window: {e}");
            }
            return;
        }
        None => {}
    }
    if id.starts_with("menu-") {
        let windows = app.webview_windows();
        let focused: Vec<&str> = windows
            .iter()
            .filter(|(_, w)| w.is_focused().unwrap_or(false))
            .map(|(label, _)| label.as_str())
            .collect();
        let _ = app.emit_to(menu_target(&focused), "tavotto:menu", id.to_string());
    }
}

/* -------------------------------------------------------------------------- */
/*  远程实例窗口（ADR 0105）                                                    */
/* -------------------------------------------------------------------------- */

/// 远程实例窗口的起始页。`connect.html` 在 `tauri://` 源下，读不到品牌常量：产品名与
/// 命令名（PyPI 包名）由这里带过去，值是 build.rs 从 `brand.ts` / `engine/brand.py`
/// 注入的——页面里不手写产品名（`tests/test_desktop_i18n.py` 看护）。
fn connect_page_url(locale: i18n::Locale) -> String {
    format!(
        "connect.html?lang={}&product={}&dist={}",
        locale.tag(),
        utf8_percent_encode(env!("TAVOTTO_PRODUCT_NAME"), NON_ALPHANUMERIC),
        utf8_percent_encode(env!("TAVOTTO_DIST_NAME"), NON_ALPHANUMERIC),
    )
}

/// 开（或聚焦已有的）远程实例窗口。先停在壳自带的 `connect.html` 上，用户粘进
/// 服务器打印的登录地址，`connect_remote` 验过之后再导航过去。
///
/// 三处与主窗口不同，都是刻意的：
/// - **`incognito`**：cookie 按主机不按端口隔离，两个引擎都往 `127.0.0.1` 写同名的
///   `tavotto_session`——共用一个存储，连上远程就把本机 sidecar 的会话顶掉，而且
///   本机的会话 token 会随每个请求发到远程引擎去。无痕存储各窗口各一份、关窗即清。
/// - **注入 `REMOTE_WINDOW_MARKER`**：前端据它把本机文件类能力让给浏览器回退。
/// - **不装 native_drop**：拖进来的是本机路径，远程引擎用不了。
fn open_remote_window(app: &tauri::AppHandle) -> tauri::Result<()> {
    if let Some(win) = app.get_webview_window(REMOTE_WINDOW) {
        let _ = win.unminimize();
        let _ = win.show();
        return win.set_focus();
    }
    let state = app.state::<AppState>();
    *state.remote_port.lock().unwrap() = None;
    let locale = *state.menu_locale.lock().unwrap();
    let port_cell = state.remote_port.clone();
    let nav_handle = app.clone();
    let win = tauri::WebviewWindowBuilder::new(
        app,
        REMOTE_WINDOW,
        tauri::WebviewUrl::App(connect_page_url(locale).into()),
    )
    .title(i18n::text(locale).remote_window_title)
    .inner_size(1280.0, 860.0)
    .min_inner_size(1024.0, 680.0)
    .incognito(true)
    .initialization_script(REMOTE_WINDOW_MARKER)
    // 与主窗口同理：把 HTML5 拖放还给页面（素材拖进画布）
    .disable_drag_drop_handler()
    .on_navigation(move |url| {
        let port = *port_cell.lock().unwrap();
        match navigation_allowed(url, port) {
            NavDecision::Allow => true,
            NavDecision::Deny => false,
            NavDecision::OpenExternal => {
                let _ = nav_handle.opener().open_url(url.as_str(), None::<&str>);
                false
            }
        }
    })
    .build()?;
    win.set_focus()
}

/// `connect.html` 的「连接」：验登录地址 → 问远程引擎够不够新 → 放行这个端口 → 导航。
///
/// 只收远程实例窗口的调用（ACL 也只给那个窗口的壳自带页面开了这条命令）。
/// 成功回远程引擎的版本号；失败回 `remote::ConnectError::code()` 的稳定 code。
#[tauri::command]
async fn connect_remote(
    app: tauri::AppHandle,
    window: tauri::Window,
    url: String,
) -> Result<String, String> {
    if window.label() != REMOTE_WINDOW {
        return Err(remote::ConnectError::BadWindow.code().into());
    }
    let login = remote::parse_login_url(&url).map_err(|e| e.code().to_string())?;
    let state = app.state::<AppState>();
    // 粘成了壳自己那个 sidecar 的地址：那是本机引擎，用主窗口就好
    if state.port.get() == Some(&login.port) {
        return Err(remote::ConnectError::IsLocalEngine.code().into());
    }
    let port = login.port;
    let version = tauri::async_runtime::spawn_blocking(move || remote::probe(port))
        .await
        .map_err(|_| remote::ConnectError::Unreachable.code().to_string())?
        .map_err(|e| e.code().to_string())?;
    // 语言与主窗口的落地 URL 同一条规则：只有用户亲手选过才带
    let chosen = i18n::read_stored(i18n::locale_file(app.path().app_config_dir().ok()))
        .filter(|s| s.explicit)
        .map(|s| s.locale.tag());
    let landing = login.landing(chosen);
    // 先放行端口，再导航——反过来的话导航守卫会把这一跳拦掉
    *state.remote_port.lock().unwrap() = Some(port);
    let win = app
        .get_webview_window(REMOTE_WINDOW)
        .ok_or_else(|| remote::ConnectError::BadWindow.code().to_string())?;
    let locale = *state.menu_locale.lock().unwrap();
    let _ = win.set_title(
        &i18n::text(locale)
            .remote_window_connected_title
            .replace("{port}", &port.to_string()),
    );
    win.eval(format!("window.location.replace({})", js_string(&landing)))
        .map_err(|_| remote::ConnectError::BadWindow.code().to_string())?;
    Ok(version)
}

/// 系统预定义角色（行为归操作系统：剪贴板、隐藏、关窗……）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Native {
    About,
    Hide,
    HideOthers,
    ShowAll,
    Quit,
    CloseWindow,
    Cut,
    Copy,
    Paste,
    SelectAll,
    Fullscreen,
    Minimize,
    Maximize,
    BringAllToFront,
}

/// 菜单的声明式结构。**`build_menu_in` 只照着它建，单测也只量它**——量的是真正
/// 会挂到菜单上的项，而不是源码里出现过的字符串（注释、死代码里的 id 不算数）。
#[derive(Debug)]
enum Entry {
    /// 自定义项：`menu-*` 经 `tavotto:menu` 转发给前端，`help-*` 由壳开链接
    Item {
        id: &'static str,
        text: &'static str,
        accel: Option<&'static str>,
    },
    Native(Native, &'static str),
    Separator,
    Submenu(&'static str, Vec<Entry>),
}

fn item(id: &'static str, text: &'static str, accel: Option<&'static str>) -> Entry {
    Entry::Item { id, text, accel }
}

/// 整棵菜单。**菜单项 id 与加速键在两种语言下完全相同**——只有显示文案换：
/// 切语言绝不能让 ⌘Z 失灵，那种坏法用户根本不会往语言上联想（单测逐项比两种语言）。
///
/// `macos` 是参数而不是 `cfg`：两个平台的形状在任何一台机器上的单测里都量得到。
///
/// **菜单只接前端已有的命令**，动作全部落到前端既有的 action 上
/// （`web/src/hooks/menuActions.ts`）。加速键只挂在前端**本来就认**的键上
/// （`hooks/useKeyboard.ts`），并且前端对菜单转发做与 keydown 相同的让位判断——
/// 菜单加速键可能先于 webview 的 keydown 截获按键（Windows 一定如此），
/// 输入框聚焦时 ⌘D / ⌘0 / ⌘± 不能被菜单劫持成画布动作。
/// Delete / ? 这类不带修饰键的前端键位**不挂**加速键：挂上等于在输入框里打不了字。
fn menu_spec(m: &'static i18n::ShellText, macos: bool) -> Vec<Entry> {
    use Entry::{Native as N, Separator as Sep, Submenu as Sub};
    use Native::*;

    // 设置 / 检查更新：macOS 放应用菜单，别的平台（没有应用菜单）分别放「文件」与「帮助」
    let settings = || item("menu-settings", m.app_settings, Some("CmdOrCtrl+Comma"));
    let check_updates = || item("menu-check-updates", m.app_check_updates, None);

    let mut menus = Vec::new();
    if macos {
        menus.push(Sub(
            "Tavotto",
            vec![
                N(About, m.app_about),
                Sep,
                settings(),
                check_updates(),
                Sep,
                N(Hide, m.app_hide),
                N(HideOthers, m.app_hide_others),
                N(ShowAll, m.app_show_all),
                Sep,
                N(Quit, m.app_quit),
            ],
        ));
    }

    let mut file = vec![
        item(
            "menu-open-project",
            m.file_open_project,
            Some("CmdOrCtrl+O"),
        ),
        item(CONNECT_REMOTE_ID, m.file_connect_remote, None),
        Sep,
        item("menu-save", m.file_save, Some("CmdOrCtrl+S")),
        item(
            "menu-save-layout",
            m.file_save_layout,
            Some("CmdOrCtrl+Shift+S"),
        ),
        Sep,
        item("menu-export", m.file_export, Some("CmdOrCtrl+E")),
        Sep,
        // 预定义角色：macOS 走 performClose:、Windows 发 WM_CLOSE，两条都会进
        // `WindowEvent::CloseRequested` → 关窗询问闸，与点红灯同一条路。
        N(CloseWindow, m.file_close_window),
    ];
    if !macos {
        file.extend([Sep, settings(), Sep, N(Quit, m.quit)]);
    }
    menus.push(Sub(m.file, file));

    // 撤销/重做是自定义项：走事件转发给前端（画布 undo 栈），文本框内的
    // 原生撤销由前端按焦点分派。剪贴板项必须用预定义角色——macOS 的
    // WKWebView 里没有这些菜单角色时 ⌘C/⌘V 在输入框里完全失效。
    menus.push(Sub(
        m.edit,
        vec![
            item("menu-undo", m.edit_undo, Some("CmdOrCtrl+Z")),
            item("menu-redo", m.edit_redo, Some("CmdOrCtrl+Shift+Z")),
            Sep,
            N(Cut, m.edit_cut),
            N(Copy, m.edit_copy),
            N(Paste, m.edit_paste),
            item("menu-duplicate", m.edit_duplicate, Some("CmdOrCtrl+D")),
            // 不挂 Delete / Backspace：那是输入框里删字的键
            item("menu-delete", m.edit_delete, None),
            N(SelectAll, m.edit_select_all),
            Sep,
            // 对齐与分布：id 的后缀就是前端 `AlignMode`，参照与属性页、多选浮动栏
            // 同一套（`arrangeStore.alignRefFor`）。菜单感知不到选区，选得不够时由前端说出口。
            Sub(
                m.edit_arrange,
                vec![
                    item("menu-align-left", m.align_left, None),
                    item("menu-align-hcenter", m.align_hcenter, None),
                    item("menu-align-right", m.align_right, None),
                    Sep,
                    item("menu-align-top", m.align_top, None),
                    item("menu-align-vcenter", m.align_vcenter, None),
                    item("menu-align-bottom", m.align_bottom, None),
                    Sep,
                    item("menu-align-hdist", m.align_hdist, None),
                    item("menu-align-vdist", m.align_vdist, None),
                ],
            ),
        ],
    ));

    let mut view = vec![
        item("menu-zoom-in", m.view_zoom_in, Some("CmdOrCtrl+Equal")),
        item("menu-zoom-out", m.view_zoom_out, Some("CmdOrCtrl+Minus")),
        item("menu-zoom-actual", m.view_actual_size, Some("CmdOrCtrl+0")),
        item("menu-zoom-fit", m.view_fit, Some("CmdOrCtrl+1")),
        Sep,
        item("menu-toggle-left", m.view_toggle_left, None),
        item("menu-toggle-right", m.view_toggle_right, None),
    ];
    if macos {
        // 全屏的预定义角色只有 macOS 有（muda：Windows / Linux 不支持）
        view.extend([Sep, N(Fullscreen, m.view_fullscreen)]);
    }
    menus.push(Sub(m.view, view));

    // 「窗口」是 macOS 的标准菜单；Windows 的最小化 / 最大化在标题栏上，不另开一栏。
    if macos {
        menus.push(Sub(
            m.window,
            vec![
                N(Minimize, m.window_minimize),
                N(Maximize, m.window_zoom),
                Sep,
                N(BringAllToFront, m.window_bring_all),
            ],
        ));
    }

    let mut help = vec![
        item("menu-shortcut-help", m.help_shortcuts, None),
        item("help-docs", m.help_docs, None),
        item("help-report-issue", m.help_report_issue, None),
        Sep,
        item("menu-diagnostics", m.help_diagnostics, None),
    ];
    // macOS 的「关于」与「检查更新」在应用菜单里，帮助菜单不再重复一份
    if !macos {
        help.extend([Sep, check_updates(), N(About, m.app_about)]);
    }
    menus.push(Sub(m.help, help));
    menus
}

fn build_native<R: tauri::Runtime>(
    handle: &tauri::AppHandle<R>,
    kind: Native,
    text: &str,
    about: &AboutMetadata<'static>,
) -> tauri::Result<PredefinedMenuItem<R>> {
    let t = Some(text);
    match kind {
        Native::About => PredefinedMenuItem::about(handle, t, Some(about.clone())),
        Native::Hide => PredefinedMenuItem::hide(handle, t),
        Native::HideOthers => PredefinedMenuItem::hide_others(handle, t),
        Native::ShowAll => PredefinedMenuItem::show_all(handle, t),
        Native::Quit => PredefinedMenuItem::quit(handle, t),
        Native::CloseWindow => PredefinedMenuItem::close_window(handle, t),
        Native::Cut => PredefinedMenuItem::cut(handle, t),
        Native::Copy => PredefinedMenuItem::copy(handle, t),
        Native::Paste => PredefinedMenuItem::paste(handle, t),
        Native::SelectAll => PredefinedMenuItem::select_all(handle, t),
        Native::Fullscreen => PredefinedMenuItem::fullscreen(handle, t),
        Native::Minimize => PredefinedMenuItem::minimize(handle, t),
        Native::Maximize => PredefinedMenuItem::maximize(handle, t),
        Native::BringAllToFront => PredefinedMenuItem::bring_all_to_front(handle, t),
    }
}

fn build_submenu<R: tauri::Runtime>(
    handle: &tauri::AppHandle<R>,
    text: &str,
    entries: &[Entry],
    about: &AboutMetadata<'static>,
) -> tauri::Result<Submenu<R>> {
    let sub = Submenu::new(handle, text, true)?;
    for entry in entries {
        match entry {
            Entry::Item { id, text, accel } => {
                let b = MenuItemBuilder::with_id(*id, *text);
                let b = match accel {
                    Some(a) => b.accelerator(*a),
                    None => b,
                };
                sub.append(&b.build(handle)?)?;
            }
            Entry::Native(kind, text) => sub.append(&build_native(handle, *kind, text, about)?)?,
            Entry::Separator => sub.append(&PredefinedMenuItem::separator(handle)?)?,
            Entry::Submenu(text, children) => {
                sub.append(&build_submenu(handle, text, children, about)?)?
            }
        }
    }
    Ok(sub)
}

/// 照 `menu_spec` 建菜单；结构与 id / 加速键的判据全在 `menu_spec` 与它的单测里。
fn build_menu_in<R: tauri::Runtime>(
    handle: &tauri::AppHandle<R>,
    locale: i18n::Locale,
) -> tauri::Result<Menu<R>> {
    let m = i18n::text(locale);
    let about = AboutMetadataBuilder::new()
        .name(Some("Tavotto"))
        .version(Some(env!("CARGO_PKG_VERSION")))
        .website(Some(REPO_URL))
        .comments(Some(m.about_comments))
        .build();

    let menu = Menu::new(handle)?;
    for top in menu_spec(m, cfg!(target_os = "macos")) {
        let Entry::Submenu(text, entries) = top else {
            unreachable!("菜单栏顶层只放子菜单");
        };
        menu.append(&build_submenu(handle, text, &entries, &about)?)?;
    }
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
        // 默认**不拦**：前端注册好监听器后自己来 arm（那时才为那个窗口建闸）。
        close_gates: Mutex::new(HashMap::new()),
        remote_port: Arc::new(Mutex::new(None)),
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
            reveal_project_dir,
            set_menu_locale,
            codex_integration,
            arm_close_guard,
            resolve_close_request,
            native_file_drop,
            connect_remote
        ])
        .on_window_event(|window, event| {
            // 事件回调是全局的：只看有关窗询问闸的那两个窗口。
            if !guarded_window(window.label()) {
                return;
            }
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => on_close_requested(window, api),
                // 远程实例窗口没了：它的闸与放行的端口一起收掉，下次再开是新的一轮
                tauri::WindowEvent::Destroyed if window.label() == REMOTE_WINDOW => {
                    if let Some(state) = window.app_handle().try_state::<AppState>() {
                        state.close_gates.lock().unwrap().remove(REMOTE_WINDOW);
                        *state.remote_port.lock().unwrap() = None;
                    }
                }
                _ => {}
            }
        })
        .menu(build_menu)
        .on_menu_event(|app, event| on_menu_event(app, event.id().as_ref()))
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
                MAIN_WINDOW,
                // 启动画面同样带上语言：它比前端先出现，读不到 i18next
                tauri::WebviewUrl::App(format!("splash.html?lang={}", boot_locale.tag()).into()),
            )
            .title("Tavotto")
            .inner_size(1280.0, 860.0)
            // 三栏工作台的断点下限：再窄左右栏会互相挤压（见 CLAUDE.md 视觉纪律）
            .min_inner_size(1024.0, 680.0)
            // Tauri 默认接管窗口的拖放事件（tauri://drag-drop），代价是 webview 里
            // 的 HTML5 drag&drop 整个失效——「素材拖入画布」在桌面壳里就是这么坏的。
            // 关掉它把 DnD 还给页面。主页要的 OS 文件路径不靠它：macOS 上由 native_drop
            // 只旁听 performDragOperation 拿（ADR 0092），页面的 HTML5 拖放一个字节不变。
            .disable_drag_drop_handler()
            .on_navigation(
                move |url| match navigation_allowed(url, port_cell.get().copied()) {
                    NavDecision::Allow => true,
                    NavDecision::Deny => false,
                    NavDecision::OpenExternal => {
                        let _ = nav_handle.opener().open_url(url.as_str(), None::<&str>);
                        false
                    }
                },
            )
            .build()?;
            // 主页拖放拿真实路径：只旁听 performDragOperation，不装 Tauri 的拖放处理器
            // （上面那条注释说的 HTML5 拖放照旧归页面）。见 native_drop.rs 与 ADR 0092。
            #[cfg(target_os = "macos")]
            {
                let drop_handle = handle.clone();
                let _ = win.with_webview(move |pw| native_drop::install(pw.inner(), drop_handle));
            }
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

    /// `menu_spec` 里**真正会挂上去**的自定义项（id, 加速键），按出现顺序。
    fn spec_items(entries: &[Entry], out: &mut Vec<(&'static str, Option<&'static str>)>) {
        for e in entries {
            match e {
                Entry::Item { id, accel, .. } => out.push((id, *accel)),
                Entry::Submenu(_, children) => spec_items(children, out),
                Entry::Native(..) | Entry::Separator => {}
            }
        }
    }

    fn items_of(locale: i18n::Locale, macos: bool) -> Vec<(&'static str, Option<&'static str>)> {
        let mut out = Vec::new();
        spec_items(&menu_spec(i18n::text(locale), macos), &mut out);
        out
    }

    const LOCALES: [i18n::Locale; 2] = [i18n::Locale::ZhCn, i18n::Locale::EnUs];

    /// 与前端 `MENU_ACTIONS` 的严格同源对：两侧各自与这份 golden 比，不读对方源码
    /// （前端那一侧是 `web/src/lib/desktop.golden.test.ts`）。壳多一条 = 点了没反应，
    /// 前端多一条 = 死分支；`help-*` 由壳开链接，不进前端。
    #[test]
    fn menu_ids_match_the_golden_pair_on_both_platforms() {
        let golden: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/golden/menu_actions.json")).unwrap();
        let set = |k: &str| -> std::collections::BTreeSet<String> {
            golden[k]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_string())
                .collect()
        };
        let (forwarded, shell) = (set("forwarded"), set("shell"));
        assert!(!forwarded.is_empty() && !shell.is_empty());
        for macos in [true, false] {
            for locale in LOCALES {
                let mut seen = std::collections::BTreeSet::new();
                for (id, _) in items_of(locale, macos) {
                    assert!(
                        seen.insert(id.to_string()),
                        "{id} 挂了不止一次（macos={macos}）"
                    );
                }
                let got_fwd = seen
                    .iter()
                    .filter(|i| i.starts_with("menu-"))
                    .cloned()
                    .collect();
                let got_shell = seen
                    .iter()
                    .filter(|i| !i.starts_with("menu-"))
                    .cloned()
                    .collect();
                assert_eq!(
                    forwarded, got_fwd,
                    "转发给前端的 id 与 golden 不同（macos={macos}）"
                );
                assert_eq!(
                    shell, got_shell,
                    "壳自己处理的 id 与 golden 不同（macos={macos}）"
                );
                for id in &shell {
                    assert!(shell_action(id).is_some(), "{id} 挂在菜单上壳却不处理");
                }
            }
        }
    }

    /// 切语言只换显示文案：id 与加速键逐项相同。改坏了的表现是「切成英文之后 ⌘Z
    /// 没反应」——用户不会往语言上联想。
    #[test]
    fn language_switch_does_not_touch_ids_or_accelerators() {
        for macos in [true, false] {
            assert_eq!(
                items_of(i18n::Locale::ZhCn, macos),
                items_of(i18n::Locale::EnUs, macos),
                "macos={macos}"
            );
        }
    }

    /// 自定义项的加速键全集，每条都是前端 `hooks/useKeyboard.ts` 本来就认的键
    /// （⌘, 除外：前端没有这个键），菜单转发与 keydown 的让位判据同一条
    /// （`web/src/hooks/menuActions.test.tsx` 逐键比）。**加一条就要回答它在输入框里
    /// 会不会被菜单劫持**——Delete / ? 这类不带修饰键的键因此一律不挂。
    #[test]
    fn accelerators_are_the_audited_set_each_once() {
        let expected: std::collections::BTreeSet<&str> = [
            "CmdOrCtrl+Comma",
            "CmdOrCtrl+O",
            "CmdOrCtrl+S",
            "CmdOrCtrl+Shift+S",
            "CmdOrCtrl+E",
            "CmdOrCtrl+Z",
            "CmdOrCtrl+Shift+Z",
            "CmdOrCtrl+D",
            "CmdOrCtrl+Equal",
            "CmdOrCtrl+Minus",
            "CmdOrCtrl+0",
            "CmdOrCtrl+1",
        ]
        .into();
        for macos in [true, false] {
            let accels: Vec<&str> = items_of(i18n::DEFAULT_LOCALE, macos)
                .into_iter()
                .filter_map(|(_, a)| a)
                .collect();
            let set: std::collections::BTreeSet<&str> = accels.iter().copied().collect();
            assert_eq!(set.len(), accels.len(), "有加速键挂了两次（macos={macos}）");
            assert_eq!(set, expected, "加速键集合变了（macos={macos}）");
        }
    }

    /// 仓库地址来自品牌常量（build.rs 注入），不是空串、不是手写的第二份。
    #[test]
    fn repo_url_is_injected_from_the_brand_source() {
        let brand = include_str!("../../web/src/lib/brand.ts");
        assert!(brand.contains(&format!("export const REPO_URL = '{REPO_URL}'")));
    }

    /// 帮助菜单的两条链接由壳自己打开，而且只从 `REPO_URL` 派生；
    /// 别的 id 一律不当链接（否则 `menu-*` 会被吞掉、前端收不到）。
    #[test]
    fn help_links_derive_from_the_repo_url_and_nothing_else_opens() {
        let docs = help_link("help-docs").expect("使用文档没有链接");
        let issue = help_link("help-report-issue").expect("报告问题没有链接");
        for url in [&docs, &issue] {
            assert!(url.starts_with(REPO_URL), "{url} 不是从 REPO_URL 派生的");
        }
        assert_eq!(issue, format!("{REPO_URL}/issues/new/choose"));
        for id in ["menu-export", "menu-settings", "help", "", "help-docs-x"] {
            assert_eq!(help_link(id), None, "{id} 不该被当成帮助链接");
        }
    }

    /// 「连接远程实例…」由壳自己开窗口，不当链接、也不转发给前端。
    #[test]
    fn connect_remote_is_a_shell_action_not_a_link() {
        assert_eq!(
            shell_action(CONNECT_REMOTE_ID),
            Some(ShellAction::ConnectRemote)
        );
        assert_eq!(help_link(CONNECT_REMOTE_ID), None);
        assert!(!CONNECT_REMOTE_ID.starts_with("menu-"));
        assert_eq!(shell_action("menu-open-project"), None);
    }

    /// 菜单动作跟着焦点走：远程实例窗口在前台时 ⌘S 存的是远程的排版，
    /// 不是躲在后面的主窗口。
    #[test]
    fn menu_actions_go_to_the_focused_window() {
        assert_eq!(menu_target(&[REMOTE_WINDOW]), REMOTE_WINDOW);
        assert_eq!(menu_target(&[MAIN_WINDOW]), MAIN_WINDOW);
        // 菜单栏脱离窗口被点（macOS）或焦点在别处：给主窗口
        assert_eq!(menu_target(&[]), MAIN_WINDOW);
        assert_eq!(menu_target(&["something-else"]), MAIN_WINDOW);
    }

    /// 每个窗口只许停在**自己的**引擎端口的根路径上；别的回环端口、非根路径一律拒。
    #[test]
    fn navigation_is_pinned_to_the_windows_own_engine_port() {
        let u = |s: &str| url::Url::parse(s).unwrap();
        let allow =
            |s: &str, p: Option<u16>| matches!(navigation_allowed(&u(s), p), NavDecision::Allow);
        assert!(allow("http://127.0.0.1:5089/", Some(5089)));
        assert!(allow("http://127.0.0.1:5089/?lang=en-US", Some(5089)));
        assert!(allow("tauri://localhost/connect.html", None));
        // 远程窗口还没连上：引擎源一个都不放
        assert!(!allow("http://127.0.0.1:5089/", None));
        // 别人的端口（例如主窗口的 sidecar）不放
        assert!(!allow("http://127.0.0.1:5089/", Some(5189)));
        assert!(!allow("http://127.0.0.1:5089/exports/a.pdf", Some(5089)));
        assert!(matches!(
            navigation_allowed(&u("https://example.com/"), Some(5089)),
            NavDecision::OpenExternal
        ));
    }

    /// 远程实例窗口的标记与前端读的全局名是同一个（Python 侧再比一次两侧源码）。
    #[test]
    fn the_remote_marker_sets_the_global_the_frontend_reads() {
        assert!(REMOTE_WINDOW_MARKER.contains("__TAVOTTO_REMOTE_ENGINE__"));
        assert!(REMOTE_WINDOW_MARKER.contains("= true"));
        assert!(guarded_window(MAIN_WINDOW) && guarded_window(REMOTE_WINDOW));
        assert!(!guarded_window("splash"));
    }

    /// 工作区右键 reveal 只收「此刻存在的绝对目录」：文件、相对路径、不存在的路径一律拒。
    #[test]
    fn reveal_project_dir_accepts_only_existing_absolute_dirs() {
        let tmp = std::env::temp_dir().join(format!("tavotto-reveal-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let file = tmp.join("plot.py");
        std::fs::write(&file, b"").unwrap();

        let ok = checked_project_dir(tmp.to_str().unwrap()).unwrap();
        assert_eq!(ok, std::fs::canonicalize(&tmp).unwrap());
        assert_eq!(
            checked_project_dir(file.to_str().unwrap()).unwrap_err(),
            "not_a_directory"
        );
        assert_eq!(
            checked_project_dir(tmp.join("gone").to_str().unwrap()).unwrap_err(),
            "not_found"
        );
        assert_eq!(checked_project_dir("figs").unwrap_err(), "not_absolute");
        assert_eq!(checked_project_dir("").unwrap_err(), "not_absolute");

        std::fs::remove_dir_all(&tmp).unwrap();
    }

    /// 有脚本选中按名字排第一的顶层 `.py`（隐藏文件、子目录里的、别的扩展名都不算）；
    /// 没有就选中项目文件夹本身。
    #[test]
    fn reveal_target_prefers_the_first_top_level_script() {
        let tmp = std::env::temp_dir().join(format!("tavotto-reveal-t-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("aaa")).unwrap();
        std::fs::write(tmp.join("aaa").join("0.py"), b"").unwrap();
        std::fs::write(tmp.join(".hidden.py"), b"").unwrap();
        std::fs::write(tmp.join("a.txt"), b"").unwrap();
        assert_eq!(reveal_target(&tmp), tmp);

        std::fs::write(tmp.join("plot_b.py"), b"").unwrap();
        std::fs::write(tmp.join("Fig1.PY"), b"").unwrap();
        assert_eq!(reveal_target(&tmp), tmp.join("Fig1.PY"));

        std::fs::remove_dir_all(&tmp).unwrap();
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
    fn the_connect_page_carries_the_brand_from_the_injected_constants() {
        // 页面里没有手写的产品名，全靠这两个参数：丢了就是满屏空白的「在服务器上运行 ，…」
        for locale in LOCALES {
            let url = connect_page_url(locale);
            let q = url.strip_prefix("connect.html?").expect(&url);
            let pairs: Vec<(String, String)> = q
                .split('&')
                .map(|kv| {
                    let (k, v) = kv.split_once('=').expect(kv);
                    let v = percent_encoding::percent_decode_str(v)
                        .decode_utf8()
                        .unwrap();
                    (k.to_string(), v.into_owned())
                })
                .collect();
            let want = [
                ("lang", locale.tag()),
                ("product", env!("TAVOTTO_PRODUCT_NAME")),
                ("dist", env!("TAVOTTO_DIST_NAME")),
            ]
            .map(|(k, v)| (k.to_string(), v.to_string()));
            assert_eq!(pairs, want);
        }
        assert!(!env!("TAVOTTO_PRODUCT_NAME").is_empty() && !env!("TAVOTTO_DIST_NAME").is_empty());
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
