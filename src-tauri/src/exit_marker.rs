//! 「上次有没有正常退出」的本机退出标记（ADR 0129）。
//!
//! `app_config_dir()/session-state.json`，内容只有版本 / 启动时刻 / 状态 / 退出类型闭集：
//! 不含项目、路径、脚本名、用户标识、错误文本，**不上传**，启动时读一次就清成新一轮。
//! 它只回答一个问题——「上次是不是没走完 `RunEvent::Exit`」——答案只用来在下次启动时
//! 让用户自己决定要不要发问题反馈（ADR 0118 的确认框），壳不据此补发任何东西。
//!
//! 时序（`main.rs`）：`setup` 里读旧文件 → [`judge`] 出 [`Previous`] 存进内存 → 立刻写
//! `running` → `RunEvent::Exit`（sidecar 收掉之后）写 `clean`。**所有绕过 `RunEvent::Exit`
//! 的退出路径都必须先写 `clean`**（Windows 更新器装包前 `process::exit(0)`、e2e 更新口的
//! `process::exit(4x)`），否则每次升级后都会误报「上次没有正常退出」；
//! `main.rs` 的结构测试逐条钉住。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

pub const FILE_NAME: &str = "session-state.json";
const VERSION: u32 = 1;

/// 引擎（sidecar）意外退出的类型。闭集：不带 status 文本，更不带日志。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineExit {
    /// 被信号杀死（Unix）。
    Signal,
    /// 非零退出码。
    Nonzero,
    /// 退出码 0 但壳没让它退。
    Zero,
}

impl EngineExit {
    pub fn as_str(self) -> &'static str {
        match self {
            EngineExit::Signal => "signal",
            EngineExit::Nonzero => "nonzero",
            EngineExit::Zero => "zero",
        }
    }

    fn parse(s: &str) -> Option<EngineExit> {
        match s {
            "signal" => Some(EngineExit::Signal),
            "nonzero" => Some(EngineExit::Nonzero),
            "zero" => Some(EngineExit::Zero),
            _ => None,
        }
    }

    /// `ExitStatus::code()`：Unix 上被信号杀死时为 `None`。
    pub fn classify(code: Option<i32>) -> EngineExit {
        match code {
            None => EngineExit::Signal,
            Some(0) => EngineExit::Zero,
            Some(_) => EngineExit::Nonzero,
        }
    }
}

/// 上一次会话的结局（只有「不正常」的两种；正常 = `None`）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Previous {
    /// 壳本身没走完退出流程（崩溃 / 被杀 / 断电）。
    AppUnclean,
    /// 退出前引擎已经意外退出过。
    EngineExited,
}

impl Previous {
    pub fn as_str(self) -> &'static str {
        match self {
            Previous::AppUnclean => "app_unclean",
            Previous::EngineExited => "engine_exited",
        }
    }
}

/// 旧文件内容 → 上次的结局。无文件 / `clean` / 损坏 / 版本未知 / 状态未知一律 `None`：
/// 读不懂就当没发生过（宁可漏提示，也不凭一个坏文件吓用户），随后会被覆盖写成新一轮。
pub fn judge(text: Option<&str>) -> Option<Previous> {
    let v: serde_json::Value = serde_json::from_str(text?.trim()).ok()?;
    if v.get("v")?.as_u64()? != u64::from(VERSION) {
        return None;
    }
    let engine = v
        .get("engine_exit")
        .and_then(|e| e.as_str())
        .and_then(EngineExit::parse);
    // `engine_exit` 粘住直到下次启动被取走：引擎崩了、用户没重启就正常退出 App（clean），
    // 下次启动照样提示 engine_exited（取走 = 启动时读进内存，随后写 running 覆盖）
    match v.get("state")?.as_str()? {
        "running" => Some(if engine.is_some() {
            Previous::EngineExited
        } else {
            Previous::AppUnclean
        }),
        "clean" => engine.map(|_| Previous::EngineExited),
        _ => None,
    }
}

fn render(state: &str, started_at: u64, app: &str, engine_exit: Option<EngineExit>) -> String {
    let mut v = serde_json::json!({
        "v": VERSION,
        "state": state,
        "started_at": started_at,
        "app": app,
    });
    if let Some(e) = engine_exit {
        v["engine_exit"] = serde_json::json!(e.as_str());
    }
    format!("{v}\n")
}

/// 本次会话的标记写手。克隆很便宜（共享 `closed`）；`clean` 写过之后再不落盘，
/// 免得退出途中监视线程把 `running` 写回去。
#[derive(Clone)]
pub struct Marker {
    path: Option<PathBuf>,
    started_at: u64,
    app: String,
    closed: Arc<AtomicBool>,
    /// 本次会话里引擎意外退出的类型：之后每次落盘（含 clean）都带着它
    engine: Arc<Mutex<Option<EngineExit>>>,
}

impl Marker {
    pub fn new(config_dir: Option<&Path>, app: &str) -> Marker {
        Marker {
            path: config_dir.map(|d| d.join(FILE_NAME)),
            started_at: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0),
            app: app.to_string(),
            closed: Arc::new(AtomicBool::new(false)),
            engine: Arc::new(Mutex::new(None)),
        }
    }

    /// 读旧文件（只读，不改）。
    pub fn read_previous(&self) -> Option<Previous> {
        judge(std::fs::read_to_string(self.path.as_ref()?).ok().as_deref())
    }

    pub fn mark_running(&self) {
        self.write("running");
    }

    /// 引擎意外退出：状态仍是 `running`（壳还活着），只多一个类型。
    pub fn mark_engine_exit(&self, kind: EngineExit) {
        *self.engine.lock().unwrap() = Some(kind);
        self.write("running");
    }

    /// 正常退出（`engine_exit` 不清，见 [`judge`]）——也是所有绕过 `RunEvent::Exit` 的退出路径必须先调的那一句。幂等。
    pub fn mark_clean(&self) {
        self.write("clean");
        self.closed.store(true, Ordering::SeqCst);
    }

    fn write(&self, state: &str) {
        let engine_exit = *self.engine.lock().unwrap();
        if self.closed.load(Ordering::SeqCst) {
            return;
        }
        let Some(path) = &self.path else { return };
        write_atomic(
            path,
            &render(state, self.started_at, &self.app, engine_exit),
        );
    }
}

/// tmp + rename（与 desktop-port 同写法）。写不进去只意味着下次读不到——不打断退出。
fn write_atomic(path: &Path, text: &str) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = path.with_file_name(format!("{FILE_NAME}.tmp"));
    if std::fs::write(&tmp, text).is_ok() && std::fs::rename(&tmp, path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn j(s: &str) -> Option<Previous> {
        judge(Some(s))
    }

    #[test]
    fn decision_table() {
        assert_eq!(judge(None), None, "无文件");
        assert_eq!(j(r#"{"v":1,"state":"clean"}"#), None);
        assert_eq!(
            j(r#"{"v":1,"state":"running"}"#),
            Some(Previous::AppUnclean)
        );
        for k in ["signal", "nonzero", "zero"] {
            let s = format!(r#"{{"v":1,"state":"running","engine_exit":"{k}"}}"#);
            assert_eq!(j(&s), Some(Previous::EngineExited), "{k}");
        }
        // 闭集之外的类型当没有：仍是 app_unclean，不把任意文本带进判定
        assert_eq!(
            j(r#"{"v":1,"state":"running","engine_exit":"boom"}"#),
            Some(Previous::AppUnclean)
        );
        // engine_exit 粘住：clean 带着合法 engine_exit 仍提示 engine_exited；非法类型的 clean 是正常退出
        assert_eq!(
            j(r#"{"v":1,"state":"clean","engine_exit":"zero"}"#),
            Some(Previous::EngineExited)
        );
        assert_eq!(j(r#"{"v":1,"state":"clean","engine_exit":"boom"}"#), None);
    }

    #[test]
    fn corrupt_or_unknown_files_mean_no_prompt() {
        for s in [
            "",
            "not json",
            "{",
            "[]",
            r#"{"state":"running"}"#,
            r#"{"v":2,"state":"running"}"#,
            r#"{"v":"1","state":"running"}"#,
            r#"{"v":1}"#,
            r#"{"v":1,"state":"weird"}"#,
            r#"{"v":1,"state":7}"#,
        ] {
            assert_eq!(j(s), None, "{s:?}");
        }
    }

    #[test]
    fn engine_exit_classification_is_a_closed_set() {
        assert_eq!(EngineExit::classify(None), EngineExit::Signal);
        assert_eq!(EngineExit::classify(Some(0)), EngineExit::Zero);
        assert_eq!(EngineExit::classify(Some(1)), EngineExit::Nonzero);
        assert_eq!(EngineExit::classify(Some(-1)), EngineExit::Nonzero);
    }

    fn tmp_dir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "tavotto-exit-marker-{tag}-{}-{:016x}",
            std::process::id(),
            rand::random::<u64>()
        ));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn read(dir: &Path) -> String {
        std::fs::read_to_string(dir.join(FILE_NAME)).unwrap()
    }

    #[test]
    fn rendered_file_holds_only_the_closed_fields() {
        let s = render("running", 1700000000, "1.2.3", Some(EngineExit::Signal));
        let v: serde_json::Value = serde_json::from_str(&s).unwrap();
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        keys.sort();
        assert_eq!(keys, ["app", "engine_exit", "started_at", "state", "v"]);
        let s = render("clean", 1, "1.2.3", None);
        assert!(!s.contains("engine_exit"));
    }

    #[test]
    fn lifecycle_running_then_clean() {
        let dir = tmp_dir("life");
        let m = Marker::new(Some(&dir), "1.0.0");
        assert_eq!(m.read_previous(), None, "第一次启动没有旧文件");
        m.mark_running();
        // 这一刻被杀 = 下次启动应提示
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::AppUnclean)
        );
        m.mark_clean();
        assert_eq!(Marker::new(Some(&dir), "1.0.1").read_previous(), None);
        assert!(read(&dir).contains(r#""state":"clean""#));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn engine_exit_is_sticky_through_clean_and_cleared_by_the_next_session() {
        let dir = tmp_dir("engine");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        assert_eq!(m.read_previous(), Some(Previous::EngineExited));
        m.mark_clean();
        // 用户没重启引擎就正常退出 App：clean 不清 engine_exit，下次启动照样提示
        assert_eq!(m.read_previous(), Some(Previous::EngineExited));
        assert!(read(&dir).contains(r#""state":"clean""#));
        // clean 之后监视线程迟到的写入不许把 running 写回去
        m.mark_engine_exit(EngineExit::Zero);
        m.mark_running();
        assert!(read(&dir).contains(r#""state":"clean""#));
        // 下一次会话启动：读到 engine_exited，随后写 running 覆盖（取走即清）
        let next = Marker::new(Some(&dir), "1.0.1");
        assert_eq!(next.read_previous(), Some(Previous::EngineExited));
        next.mark_running();
        next.mark_clean();
        assert_eq!(Marker::new(Some(&dir), "1.0.2").read_previous(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 替换已存在的文件（`write_atomic` 的 tmp + rename 在任何平台都必须能覆盖旧文件）：running → clean 之后
    /// 文件内容是 clean、没有遗留 tmp。Windows 上 `std::fs::rename` 是 MOVEFILE_REPLACE_EXISTING，这条用例防任何平台替换失败。
    #[test]
    fn running_then_clean_replaces_the_existing_file_without_a_leftover_tmp() {
        let dir = tmp_dir("replace");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        assert!(read(&dir).contains(r#""state":"running""#));
        m.mark_clean();
        let text = read(&dir);
        assert!(text.contains(r#""state":"clean""#), "{text}");
        assert!(!text.contains("running"));
        assert!(!dir.join(format!("{FILE_NAME}.tmp")).exists(), "不留 tmp");
        let names: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, [FILE_NAME.to_string()]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_corrupt_file_is_ignored_and_overwritten() {
        let dir = tmp_dir("corrupt");
        std::fs::write(dir.join(FILE_NAME), "\u{0}garbage").unwrap();
        let m = Marker::new(Some(&dir), "1.0.0");
        assert_eq!(m.read_previous(), None);
        m.mark_running();
        assert!(read(&dir).contains(r#""state":"running""#));
        assert!(!dir.join(format!("{FILE_NAME}.tmp")).exists(), "不留 tmp");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_config_dir_is_a_silent_noop() {
        let m = Marker::new(None, "1.0.0");
        m.mark_running();
        m.mark_clean();
        assert_eq!(m.read_previous(), None);
    }
}
