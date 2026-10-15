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
    /// 并带一个代次：每次 `mark_engine_exit` 加一，重启成功时只清「重启开始时看到的那一代」
    engine: Arc<Mutex<(u64, Option<EngineExit>)>>,
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
            engine: Arc::new(Mutex::new((0, None))),
        }
    }

    /// 读旧文件（只读，不改）。
    pub fn read_previous(&self) -> Option<Previous> {
        judge(std::fs::read_to_string(self.path.as_ref()?).ok().as_deref())
    }

    pub fn mark_running(&self) {
        let g = self.engine.lock().unwrap();
        self.write_locked("running", g.1);
    }

    /// 引擎意外退出：状态仍是 `running`（壳还活着），只多一个类型；代次加一。
    pub fn mark_engine_exit(&self, kind: EngineExit) {
        let mut g = self.engine.lock().unwrap();
        g.0 += 1;
        g.1 = Some(kind);
        self.write_locked("running", g.1);
    }

    /// 当前的引擎退出代次。重启开始时记下它（**不清除**）：重启期间用户正常退出，文件里仍带着旧类型，下次启动照样提示。
    pub fn engine_exit_generation(&self) -> u64 {
        self.engine.lock().unwrap().0
    }

    /// 重启成功之后：只有文件里的类型仍是 `generation` 那一代才清（立即重写，状态仍是 `running`）。
    /// 这期间新一代若又崩了（代次已变），它的类型保留。返回是否清了。
    pub fn clear_engine_exit_if(&self, generation: u64) -> bool {
        let mut g = self.engine.lock().unwrap();
        if g.0 != generation || g.1.is_none() {
            return false;
        }
        g.1 = None;
        self.write_locked("running", None);
        true
    }

    /// 正常退出（`engine_exit` 不清，见 [`judge`]）——也是所有绕过 `RunEvent::Exit` 的退出路径必须先调的那一句。幂等。
    pub fn mark_clean(&self) {
        let g = self.engine.lock().unwrap();
        self.write_locked("clean", g.1);
        self.closed.store(true, Ordering::SeqCst);
    }

    /// 只在**持有 `engine` 锁**时调用：改内存状态与落盘在同一把锁里完成，并发的两次写不会让旧内容
    /// 后落盘、盖掉新内容（#1070 r4280411389）。`closed` 也在锁内读写，clean 之后不会再被 running 覆盖。
    fn write_locked(&self, state: &str, engine_exit: Option<EngineExit>) {
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

    #[test]
    fn a_successful_restart_clears_the_generation_it_started_from() {
        let dir = tmp_dir("restart");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        let g = m.engine_exit_generation(); // 重启开始：只记代次，不清
        assert!(read(&dir).contains(r#""engine_exit":"nonzero""#));
        assert!(m.clear_engine_exit_if(g));
        assert!(!read(&dir).contains("engine_exit"));
        // 崩 → 重启成功 → 正常退出：下次启动无提示
        m.mark_clean();
        assert_eq!(Marker::new(Some(&dir), "1.0.1").read_previous(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn quitting_during_a_restart_still_prompts_for_the_old_crash() {
        let dir = tmp_dir("quit-mid-restart");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        let _g = m.engine_exit_generation(); // 重启开始，尚未成功
        m.mark_clean(); // 用户此刻正常退出
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::EngineExited)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_new_generation_crash_before_the_success_branch_survives_clean() {
        let dir = tmp_dir("newgen");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        let g = m.engine_exit_generation();
        m.mark_engine_exit(EngineExit::Signal); // 新 sidecar / 监视线程已起，成功分支还没跑完时就崩了
        assert!(!m.clear_engine_exit_if(g), "代次已变：不清");
        m.mark_clean();
        assert!(read(&dir).contains(r#""engine_exit":"signal""#));
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::EngineExited)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_failed_restart_keeps_the_old_kind() {
        let dir = tmp_dir("failed");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        let _g = m.engine_exit_generation(); // 失败分支不调 clear
        m.mark_clean();
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::EngineExited)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_restart_keeps_the_engine_exit_through_clean() {
        let dir = tmp_dir("norestart");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Signal);
        m.mark_clean();
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::EngineExited)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn crashing_again_after_a_restart_sticks_the_new_kind() {
        let dir = tmp_dir("recrash");
        let m = Marker::new(Some(&dir), "1.0.0");
        m.mark_running();
        m.mark_engine_exit(EngineExit::Nonzero);
        let g = m.engine_exit_generation();
        assert!(m.clear_engine_exit_if(g));
        m.mark_engine_exit(EngineExit::Signal);
        assert!(read(&dir).contains(r#""engine_exit":"signal""#));
        m.mark_clean();
        assert_eq!(m.read_previous(), Some(Previous::EngineExited));
        // 崩 → 重启成功 → 再崩 → 不重启就退出：仍提示
        assert_eq!(
            Marker::new(Some(&dir), "1.0.1").read_previous(),
            Some(Previous::EngineExited)
        );
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

    /// 改状态与落盘同锁（#1070 r4280411389）：并发的崩溃 / 按代次清 / running 写完之后，文件里的类型必须等于
    /// 内存里最后的类型——不会有一次旧渲染后落盘、盖掉新内容。
    #[test]
    fn concurrent_writes_leave_the_file_matching_the_final_state() {
        for round in 0..20 {
            let dir = tmp_dir(&format!("race{round}"));
            let m = Marker::new(Some(&dir), "1.0.0");
            m.mark_running();
            let workers: Vec<_> = (0..4)
                .map(|t| {
                    let m = m.clone();
                    std::thread::spawn(move || {
                        for i in 0..50 {
                            match (t + i) % 3 {
                                0 => m.mark_engine_exit(EngineExit::Signal),
                                1 => {
                                    let g = m.engine_exit_generation();
                                    m.clear_engine_exit_if(g);
                                }
                                _ => m.mark_running(),
                            }
                        }
                    })
                })
                .collect();
            for w in workers {
                w.join().unwrap();
            }
            let want = m.engine.lock().unwrap().1;
            let v: serde_json::Value = serde_json::from_str(&read(&dir)).unwrap();
            let got = v
                .get("engine_exit")
                .and_then(|x| x.as_str())
                .map(str::to_owned);
            assert_eq!(got.as_deref(), want.map(|k| k.as_str()), "round {round}");
        }
    }

    /// 结构看护：`write_locked` 的每个调用点都在同一函数里先拿了 `self.engine.lock()`。
    #[test]
    fn every_write_happens_under_the_engine_lock() {
        let src = include_str!("exit_marker.rs");
        let prod = &src[..src.find("#[cfg(test)]").unwrap()];
        let mut calls = 0;
        for (at, _) in prod.match_indices("self.write_locked(") {
            calls += 1;
            let fn_start = prod[..at].rfind("fn ").unwrap();
            let lock = prod[fn_start..at].find("self.engine.lock()");
            assert!(
                lock.is_some(),
                "write_locked 调用点（偏移 {at}）之前没拿 engine 锁"
            );
        }
        assert!(calls >= 4, "write_locked 调用点少于 4 处：{calls}");
        assert!(!prod.contains("fn write("), "不许留不拿锁的 write");
    }
}
