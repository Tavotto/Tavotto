//! 原生救援页（`shell/error.html`）背后的命令与状态（ADR 0130）。
//!
//! 场景只有一个：**Python sidecar 起不来，主窗口从未导航到应用页**。此时页面在 `tauri://` 源下，React 没有挂载，
//! 但壳还能运行——所以检查 / 安装修复版本、重新尝试、发送 / 保存启动诊断都必须由 Rust 完成，不依赖任何前端。
//!
//! 三条边界（`src-tauri/AGENTS.md` 的同名一节）：
//!
//! 1. **只在「从未进入编辑态」时可用，且由可靠状态证明**：[`RescueState::entered`] 在 `boot_engine` 把主窗口
//!    导航到应用页的那一刻置位，之后**永不复位**；每条救援命令先过 [`gate`]，置位后一律拒绝
//!    （`editing_state_unknown`）。不靠超时推断「干净」——引擎启动后再失败（例如重启引擎失败）时，用户可能有
//!    未保存工作，这里没有保护它的手段，就不动手。
//! 2. **只许本机可信 shell 页面调用**：窗口 label 必须是 `main`，且窗口**当前 URL**必须是壳自带的
//!    `error.html`（[`trusted_page`]）。ACL 之外再查一道：capability 配错或将来被放宽，Rust 端仍然拒绝。
//!    远程实例窗口（`remote-*.json`）没有任何一条救援 / 更新 / 报告 / 进程控制权限，结构测试钉着。
//! 3. **更新走同一个协调器**：`rescue_update_check` / `rescue_update_install` 与 `desktop_update_*` 共用
//!    `UpdateState`（同一把 `try_lock`、同一个 `Pending` 候选句柄）。安装要带上用户看到并确认的版本号
//!    （`run_install_expecting`），句柄里的版本对不上就拒绝（`candidate_changed`），不会装成别的版本。
//!
//! 重新尝试选的是**重启整个应用**，不是同进程重试：先收掉 sidecar（`shutdown_sidecar`）、写 `clean`、
//! `restart()`——新进程从零开始，不可能有双 sidecar、孤儿 worker、旧端口或旧 nonce。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::Serialize;
use tauri::Manager;

use crate::app_update::{self, Failure};
use crate::shell_report::{self, Endpoint, Prepared, ReportInput, SendFailure};

/// 失败发生在哪一步（闭集，进 shell 报告）。`unknown` 是兜底。
pub const STAGES: &[&str] = &["locate", "launch", "handshake", "navigate", "unknown"];

/// 稳定错误码（闭集，进 shell 报告；不是 stderr 文本）。
pub mod code {
    pub const SIDECAR_NOT_FOUND: &str = "sidecar_not_found";
    pub const SIDECAR_LOG_UNAVAILABLE: &str = "sidecar_log_unavailable";
    pub const SIDECAR_SPAWN_FAILED: &str = "sidecar_spawn_failed";
    pub const SIDECAR_STDIN_FAILED: &str = "sidecar_stdin_failed";
    pub const SIDECAR_HANDSHAKE_FAILED: &str = "sidecar_handshake_failed";
    pub const SIDECAR_EXITED: &str = "sidecar_exited";
    pub const SIDECAR_TIMEOUT: &str = "sidecar_timeout";
    pub const WINDOW_INIT_FAILED: &str = "window_init_failed";
}

pub const CODES: &[&str] = &[
    code::SIDECAR_NOT_FOUND,
    code::SIDECAR_LOG_UNAVAILABLE,
    code::SIDECAR_SPAWN_FAILED,
    code::SIDECAR_STDIN_FAILED,
    code::SIDECAR_HANDSHAKE_FAILED,
    code::SIDECAR_EXITED,
    code::SIDECAR_TIMEOUT,
    code::WINDOW_INIT_FAILED,
    "unknown",
];

pub mod stage {
    pub const LOCATE: &str = "locate";
    pub const LAUNCH: &str = "launch";
    pub const HANDSHAKE: &str = "handshake";
    pub const NAVIGATE: &str = "navigate";
}

/// 启动失败的结构化事实：阶段 + 稳定码 + 退出码。**不含任何文本**——人读的报错原文走 error.html 的 `msg`，
/// 那是另一条链路，不进 shell 报告。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct FailureInfo {
    pub stage: &'static str,
    pub code: &'static str,
    pub exit_code: Option<i32>,
}

impl FailureInfo {
    pub fn new(stage: &'static str, code: &'static str) -> Self {
        Self {
            stage,
            code,
            exit_code: None,
        }
    }
}

#[derive(Default)]
pub struct RescueState {
    /// 主窗口是否已被导航到应用页（= 可能已进入编辑态）。置位后永不复位。
    entered: AtomicBool,
    failure: Mutex<Option<FailureInfo>>,
    /// 备好的那一份包：预览、保存、上传用同一份字节与同一个幂等键。
    prepared: Mutex<Option<Prepared>>,
    /// 同一时刻只有一次发送（双击不会发两份）。
    send_gate: tokio::sync::Mutex<()>,
    retrying: AtomicBool,
}

impl RescueState {
    pub fn mark_entered(&self) {
        self.entered.store(true, Ordering::SeqCst);
    }

    pub fn entered(&self) -> bool {
        self.entered.load(Ordering::SeqCst)
    }

    pub fn set_failure(&self, info: FailureInfo) {
        *self.failure.lock().unwrap() = Some(info);
        // 失败事实换了，旧包（含旧幂等键）作废
        *self.prepared.lock().unwrap() = None;
    }

    fn failure(&self) -> Option<FailureInfo> {
        *self.failure.lock().unwrap()
    }
}

/* -------------------------------------------------------------------------- */
/*  调用方检查                                                                  */
/* -------------------------------------------------------------------------- */

pub const DENY_NOT_TRUSTED: &str = "not_trusted_caller";
pub const DENY_EDITING_UNKNOWN: &str = "editing_state_unknown";

/// 壳自带的失败页：`tauri://localhost/error.html`（macOS / Linux）或 `http://tauri.localhost/error.html`（Windows）。
/// 主机名**整串相等**（`tauri.localhost.evil.com` 不算），路径只能是 `/error.html`；query / fragment 不看。
pub fn trusted_page(label: &str, url: &url::Url) -> bool {
    if label != crate::MAIN_WINDOW {
        return false;
    }
    let origin_ok = matches!(
        (url.scheme(), url.host_str()),
        ("tauri", Some("localhost")) | ("http" | "https", Some("tauri.localhost"))
    );
    origin_ok && url.path() == "/error.html"
}

/// 纯判据：谁在调、页面在哪、有没有进过编辑态。
pub fn decide(label: &str, url: Option<&url::Url>, entered: bool) -> Result<(), &'static str> {
    match url {
        Some(u) if trusted_page(label, u) => {}
        _ => return Err(DENY_NOT_TRUSTED),
    }
    if entered {
        return Err(DENY_EDITING_UNKNOWN);
    }
    Ok(())
}

fn gate(app: &tauri::AppHandle, window: &tauri::WebviewWindow) -> Result<(), &'static str> {
    let url = window.url().ok();
    decide(
        window.label(),
        url.as_ref(),
        app.state::<RescueState>().entered(),
    )
}

/* -------------------------------------------------------------------------- */
/*  命令                                                                        */
/* -------------------------------------------------------------------------- */

#[derive(Debug, Serialize)]
pub struct Status {
    /// 救援动作是否可用（可信页面 + 从未进入编辑态）。
    eligible: bool,
    /// 不可用的原因（`editing_state_unknown`）；可用时为空。
    reason: Option<&'static str>,
    stage: &'static str,
    code: &'static str,
    exit_code: Option<i32>,
    /// 这个构建配置了更新通道（updater 公钥 + 清单地址）；没配就如实说，不放空按钮。
    update: bool,
    /// 启动诊断可发送（环境变量只能关闭它，不能改地址）。
    upload: bool,
}

fn update_configured(app: &tauri::AppHandle) -> bool {
    let has_key = app
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .is_some_and(|k| !k.trim().is_empty());
    has_key && app_update::endpoints(app).is_ok()
}

#[tauri::command]
pub fn rescue_status(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, RescueState>,
) -> Result<Status, &'static str> {
    // 状态查询只要求「可信页面」；是否进过编辑态是它要回答的内容，不是拒绝的理由
    let url = window.url().ok();
    match url {
        Some(ref u) if trusted_page(window.label(), u) => {}
        _ => return Err(DENY_NOT_TRUSTED),
    }
    let eligible = !state.entered();
    let info = state
        .failure()
        .unwrap_or(FailureInfo::new("unknown", "unknown"));
    Ok(Status {
        eligible,
        reason: (!eligible).then_some(DENY_EDITING_UNKNOWN),
        stage: info.stage,
        code: info.code,
        exit_code: info.exit_code,
        update: eligible && update_configured(&app),
        upload: eligible && shell_report::upload_enabled(),
    })
}

/// 重新尝试 = 重启整个应用。幂等：重复点击只有第一次动手。
#[tauri::command]
pub async fn rescue_retry(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, RescueState>,
) -> Result<(), &'static str> {
    gate(&app, &window)?;
    if state.retrying.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    let worker = app.clone();
    // 收 sidecar 最长等 10 s，不占 async 运行时线程
    let _ = tauri::async_runtime::spawn_blocking(move || {
        worker.state::<crate::AppState>().shutdown_sidecar();
    })
    .await;
    crate::mark_clean_exit(&app);
    app.restart()
}

#[tauri::command]
pub async fn rescue_update_check(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, app_update::UpdateState>,
    ui_lang: Option<String>,
) -> Result<Option<app_update::CheckedUpdate>, Failure> {
    gate(&app, &window).map_err(Failure::code)?;
    crate::update_check(&app, &state, ui_lang.as_deref()).await
}

/// 安装用户确认的那一版。`expected_version` 是页面展示给用户、用户点了确认的版本号。
#[tauri::command]
pub async fn rescue_update_install(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, app_update::UpdateState>,
    expected_version: String,
) -> Result<app_update::Source, Failure> {
    gate(&app, &window).map_err(Failure::code)?;
    crate::update_install(&app, &state, Some(&expected_version), &|_| {}).await
}

#[derive(Debug, Serialize)]
pub struct EntryView {
    name: &'static str,
    bytes: usize,
}

#[derive(Debug, Serialize)]
pub struct Preview {
    entries: Vec<EntryView>,
    /// `report.json` 的全文——用户在点发送之前能看到的就是会发出去的内容。
    report: String,
    bytes: usize,
}

fn now_iso() -> String {
    use time::format_description::well_known::Rfc3339;
    time::OffsetDateTime::now_utc()
        .replace_nanosecond(0)
        .ok()
        .and_then(|t| t.format(&Rfc3339).ok())
        .unwrap_or_else(|| "1970-01-01T00:00:00Z".into())
}

/// 备好（或取回）包。**纯本机**：不联网、不读项目目录、不读环境变量；同一次失败之内反复调用拿到同一份字节。
fn ensure_prepared(app: &tauri::AppHandle, state: &RescueState) -> Prepared {
    let mut slot = state.prepared.lock().unwrap();
    if let Some(p) = slot.as_ref() {
        return p.clone();
    }
    let info = state
        .failure()
        .unwrap_or(FailureInfo::new("unknown", "unknown"));
    let p = shell_report::prepare(ReportInput {
        version: app.package_info().version.to_string(),
        debug_build: cfg!(debug_assertions),
        os: shell_report::host_os(),
        arch: shell_report::host_arch(),
        stage: info.stage.to_string(),
        code: info.code.to_string(),
        exit_code: info.exit_code,
        created_at: now_iso(),
    });
    *slot = Some(p.clone());
    p
}

/// 预览：只备包，**不联网**。
#[tauri::command]
pub fn rescue_diag_preview(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, RescueState>,
) -> Result<Preview, &'static str> {
    gate(&app, &window)?;
    let p = ensure_prepared(&app, &state);
    Ok(Preview {
        entries: p
            .entries()
            .into_iter()
            .map(|(name, bytes)| EntryView { name, bytes })
            .collect(),
        report: p.report_text.clone(),
        bytes: p.zip.len(),
    })
}

#[derive(Debug, Serialize)]
pub struct Receipt {
    report_id: String,
}

/// 发送——**只有这一条命令会联网**，而它只由用户在页面上单独点「发送」触发。统计开关与它无关
/// （没有统计状态可读）。服务端会话已关 / 过期（`expired` / `session_closed` / `not_found`）时丢弃旧包，
/// 下一次点击用新的幂等键重新开始；其余失败保留同一份包与同一个键，重试是幂等的。
#[tauri::command]
pub async fn rescue_diag_send(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, RescueState>,
) -> Result<Receipt, SendFailure> {
    let deny = |c: &'static str| SendFailure {
        code: c,
        retryable: false,
        retry_after: None,
    };
    gate(&app, &window).map_err(deny)?;
    if !shell_report::upload_enabled() {
        return Err(deny("uploads_disabled"));
    }
    let Ok(_one_at_a_time) = state.send_gate.try_lock() else {
        return Err(deny("busy"));
    };
    let p = ensure_prepared(&app, &state);
    match shell_report::send(&Endpoint::production(), &p).await {
        Ok(report_id) => {
            *state.prepared.lock().unwrap() = None;
            Ok(Receipt { report_id })
        }
        Err(e) => {
            if matches!(e.code, "expired" | "session_closed" | "not_found") {
                *state.prepared.lock().unwrap() = None;
            }
            Err(e)
        }
    }
}

/// 保存：把**同一份**包写到用户在系统对话框里选的位置。返回文件名（取消回 `None`）。
#[tauri::command]
pub async fn rescue_diag_save(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, RescueState>,
) -> Result<Option<String>, &'static str> {
    gate(&app, &window)?;
    let p = ensure_prepared(&app, &state);
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        let Some(picked) = handle
            .dialog()
            .file()
            .set_file_name("tavotto-startup-diagnostics.zip")
            .add_filter("ZIP", &["zip"])
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let path = picked.into_path().map_err(|_| "save_failed")?;
        std::fs::write(&path, &p.zip).map_err(|_| "save_failed")?;
        Ok(path.file_name().map(|n| n.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|_| "save_failed")?
}

/* -------------------------------------------------------------------------- */
/*  测试                                                                        */
/* -------------------------------------------------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    fn u(s: &str) -> url::Url {
        url::Url::parse(s).unwrap()
    }

    #[test]
    fn only_the_shells_own_error_page_on_the_main_window_is_trusted() {
        for ok in [
            "tauri://localhost/error.html?msg=x&lang=en-US",
            "tauri://localhost/error.html",
            "http://tauri.localhost/error.html?msg=x",
            "https://tauri.localhost/error.html",
        ] {
            assert!(trusted_page("main", &u(ok)), "{ok}");
        }
        for bad in [
            // 引擎页（应用本体）：恰恰是不能获得救援能力的那一个
            "http://127.0.0.1:5000/",
            "http://127.0.0.1:5000/error.html",
            // 别的壳页面
            "tauri://localhost/connect.html",
            "tauri://localhost/splash.html",
            "tauri://localhost/error.html/../connect.html",
            // 主机名只能整串相等
            "http://tauri.localhost.evil.com/error.html",
            "http://evil.com/tauri.localhost/error.html",
            "tauri://evil/error.html",
            "http://tauri.localhost:8080/../x",
            "file:///error.html",
        ] {
            assert!(!trusted_page("main", &u(bad)), "{bad}");
        }
        // 窗口 label 不对：远程实例窗口里就算有人把壳页面导航进去，也不是主窗口
        assert!(!trusted_page("remote", &u("tauri://localhost/error.html")));
        assert!(!trusted_page("", &u("tauri://localhost/error.html")));
    }

    #[test]
    fn once_the_app_page_was_entered_every_rescue_action_is_refused() {
        let page = u("tauri://localhost/error.html");
        assert_eq!(decide("main", Some(&page), false), Ok(()));
        assert_eq!(
            decide("main", Some(&page), true),
            Err(DENY_EDITING_UNKNOWN),
            "进过编辑态的窗口：工作状态未知，不动手"
        );
        assert_eq!(decide("main", None, false), Err(DENY_NOT_TRUSTED));
        assert_eq!(decide("remote", Some(&page), false), Err(DENY_NOT_TRUSTED));
        // 不可信永远先于「进过编辑态」：不向不可信的调用方泄露状态
        assert_eq!(decide("remote", Some(&page), true), Err(DENY_NOT_TRUSTED));
    }

    #[test]
    fn the_entered_flag_only_ever_goes_one_way() {
        let s = RescueState::default();
        assert!(!s.entered());
        s.mark_entered();
        assert!(s.entered());
        s.set_failure(FailureInfo::new("handshake", "sidecar_timeout"));
        assert!(s.entered(), "换了失败事实也不复位");
    }

    #[test]
    fn a_new_failure_invalidates_the_prepared_bundle_and_its_request_id() {
        let s = RescueState::default();
        *s.prepared.lock().unwrap() = Some(shell_report::prepare(ReportInput {
            version: "1.0.0".into(),
            debug_build: false,
            os: "macos",
            arch: "arm64",
            stage: "handshake".into(),
            code: "sidecar_timeout".into(),
            exit_code: None,
            created_at: "2026-01-01T00:00:00Z".into(),
        }));
        s.set_failure(FailureInfo::new("launch", "sidecar_spawn_failed"));
        assert!(s.prepared.lock().unwrap().is_none());
    }

    #[test]
    fn the_closed_sets_contain_each_other_consistently() {
        for c in CODES {
            assert!(c.chars().all(|ch| ch.is_ascii_lowercase() || ch == '_'));
        }
        for (st, cd) in [
            (stage::LOCATE, code::SIDECAR_NOT_FOUND),
            (stage::LAUNCH, code::SIDECAR_LOG_UNAVAILABLE),
            (stage::LAUNCH, code::SIDECAR_SPAWN_FAILED),
            (stage::LAUNCH, code::SIDECAR_STDIN_FAILED),
            (stage::HANDSHAKE, code::SIDECAR_HANDSHAKE_FAILED),
            (stage::HANDSHAKE, code::SIDECAR_EXITED),
            (stage::HANDSHAKE, code::SIDECAR_TIMEOUT),
            (stage::NAVIGATE, code::WINDOW_INIT_FAILED),
        ] {
            assert!(STAGES.contains(&st) && CODES.contains(&cd), "{st}/{cd}");
        }
    }

    // ---- 结构：命令三处同步 + 每条命令先过闸 + 远程窗口拿不到 ----
    const RESCUE_COMMANDS: [&str; 7] = [
        "rescue_status",
        "rescue_retry",
        "rescue_update_check",
        "rescue_update_install",
        "rescue_diag_preview",
        "rescue_diag_send",
        "rescue_diag_save",
    ];

    fn prod(src: &str) -> &str {
        &src[..src.find("#[cfg(test)]\nmod tests").unwrap_or(src.len())]
    }

    fn command_body<'a>(src: &'a str, name: &str) -> &'a str {
        let at = src
            .find(&format!("fn {name}("))
            .unwrap_or_else(|| panic!("找不到 {name}"));
        let rest = &src[at..];
        &rest[..rest.find("\n}\n").expect("函数没有收尾")]
    }

    #[test]
    fn every_rescue_command_checks_the_caller_before_doing_anything() {
        let src = prod(include_str!("rescue.rs"));
        for name in RESCUE_COMMANDS {
            let body = command_body(src, name);
            if name == "rescue_status" {
                // 状态查询只验「可信页面」，不因进过编辑态而拒绝（它要回答的正是这件事）
                assert!(body.contains("trusted_page("), "{name}");
                continue;
            }
            let g = body
                .find("gate(&app, &window)")
                .unwrap_or_else(|| panic!("{name} 没有先过 gate"));
            for side_effect in [
                "shutdown_sidecar",
                "mark_clean_exit",
                "restart()",
                "crate::update_check(",
                "crate::update_install(",
                "ensure_prepared(",
                "shell_report::send(",
                "blocking_save_file",
            ] {
                if let Some(at) = body.find(side_effect) {
                    assert!(g < at, "{name}: 在过闸之前就 {side_effect}");
                }
            }
        }
    }

    #[test]
    fn only_the_send_command_touches_the_network_and_preview_save_never_do() {
        let src = prod(include_str!("rescue.rs"));
        for name in ["rescue_diag_preview", "rescue_diag_save", "rescue_status"] {
            let body = command_body(src, name);
            assert!(
                !body.contains("shell_report::send") && !body.contains("reqwest"),
                "{name} 不许联网"
            );
        }
        assert_eq!(
            src.matches("shell_report::send(").count(),
            1,
            "上传的唯一入口是 rescue_diag_send"
        );
        assert!(command_body(src, "rescue_diag_send").contains("shell_report::send("));
        // ensure_prepared 本身也不联网、不读环境
        let body = command_body(src, "ensure_prepared");
        assert!(!body.contains("send(") && !body.contains("env::var"));
    }

    #[test]
    fn rescue_commands_are_declared_in_build_manifest_handler_and_the_rescue_capability() {
        let build = include_str!("../build.rs");
        let main = prod(include_str!("main.rs"));
        let cap: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/rescue-page.json")).unwrap();
        let perms: Vec<&str> = cap["permissions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p.as_str().unwrap())
            .collect();
        for name in RESCUE_COMMANDS {
            assert!(build.contains(&format!("\"{name}\"")), "build.rs 缺 {name}");
            assert!(
                main.contains(&format!("rescue::{name}")),
                "generate_handler 缺 {name}"
            );
            let perm = format!("allow-{}", name.replace('_', "-"));
            assert!(perms.contains(&perm.as_str()), "rescue-page.json 缺 {perm}");
        }
        assert_eq!(perms.len(), RESCUE_COMMANDS.len(), "多放行了别的权限");
    }

    #[test]
    fn the_rescue_capability_is_local_only_on_the_main_window() {
        let cap: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/rescue-page.json")).unwrap();
        assert_eq!(cap["windows"], serde_json::json!(["main"]));
        assert_eq!(cap["local"], true);
        assert!(
            cap.get("remote").is_none(),
            "救援能力不许落在任何远程 URL 上（含 127.0.0.1 的引擎页）"
        );
    }

    #[test]
    fn no_remote_capability_and_not_even_the_engine_page_can_reach_rescue_or_update_or_process_control(
    ) {
        let remote_connect = include_str!("../capabilities/remote-connect.json");
        let remote_engine = include_str!("../capabilities/remote-engine.json");
        let main: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
        let banned_prefixes = [
            "allow-rescue",
            "allow-desktop-update",
            "allow-restart-engine",
            "allow-reveal",
            "allow-codex",
            "allow-native-file-drop",
            "allow-set-menu-locale",
            "dialog:",
            "process:",
            "updater:",
            "fs:",
            "shell:",
            "opener:",
        ];
        for (name, text) in [
            ("remote-connect.json", remote_connect),
            ("remote-engine.json", remote_engine),
        ] {
            let cap: serde_json::Value = serde_json::from_str(text).unwrap();
            for p in cap["permissions"].as_array().unwrap() {
                let p = p.as_str().unwrap();
                for b in banned_prefixes {
                    assert!(!p.starts_with(b), "{name} 放行了 {p}");
                }
            }
        }
        // 引擎页（main.json，127.0.0.1）可以更新，但不能碰救援命令
        for p in main["permissions"].as_array().unwrap() {
            assert!(
                !p.as_str().unwrap().starts_with("allow-rescue"),
                "main.json 不许放行救援命令"
            );
        }
    }

    #[test]
    fn the_restart_is_preceded_by_shutting_the_sidecar_and_writing_clean() {
        let src = prod(include_str!("rescue.rs"));
        assert_eq!(src.matches("app.restart()").count(), 1);
        let body = command_body(src, "rescue_retry");
        let down = body.find("shutdown_sidecar()").unwrap();
        let clean = body.find("mark_clean_exit(&app)").unwrap();
        let restart = body.find("app.restart()").unwrap();
        assert!(down < clean && clean < restart);
        assert!(body.contains("retrying.swap(true"), "重复点击要幂等");
    }
}
