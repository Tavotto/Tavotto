//! worker 子进程的机制层：spawn / 写 / 读 / 强杀。
//!
//! **策略全部留在 Python**：哪个解释器、带什么 env、`-B` 要不要加、日志写哪儿，
//! 都由 Flask 算好装进 [`SpawnSpec`] 交过来。这里一个探测都不做——把解释器优先级
//! （`pool._prioritized_candidates()`）重写一遍就是在制造第二个权威。
//!
//! 两条独立线程是刻意的：
//!
//! * **写线程**——`stdin.write` 在管道满时会阻塞。Python 池里正是这条在会话线程
//!   上同步执行，worker 不读时整个会话（连同它那把锁）就一起挂住。这里写请求只是
//!   往 channel 里塞一条，会话线程永远不会因为写而卡住。
//! * **读线程**——按行读 stdout，每行带上**这一代的序号**发出来。上一代的迟到响应
//!   于是能被认出来直接丢弃，绝不会覆盖新会话的状态。

use std::collections::BTreeMap;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;

use crate::patchspec;

/// spawn 一个 worker 需要知道的**全部**东西（Flask 算好，workerd 照做）。
#[derive(Debug, Clone, Default)]
pub struct SpawnSpec {
    /// 完整命令行：`[解释器, ...解释器参数, worker.py, --script, ...]`
    pub argv: Vec<String>,
    /// 环境**增量**（在继承环境之上覆盖），空表示什么都不改。
    pub env: BTreeMap<String, String>,
    pub cwd: Option<String>,
    /// worker 的 stderr 追加到这里（就是 pool 的 `worker.log`）。
    pub log_path: Option<String>,
    /// 握手（v1 ping）的期限。
    pub handshake_timeout_ms: u64,
    /// 只用于日志与诊断，不参与身份。
    pub label: String,
}

/// 握手期限的兜底：解释器冷启动 + import matplotlib 在慢盘上真能到十几秒。
pub const DEFAULT_HANDSHAKE_TIMEOUT_MS: u64 = 60_000;

impl SpawnSpec {
    pub fn from_payload(payload: &Value) -> Result<SpawnSpec, String> {
        let argv: Vec<String> = payload
            .get("argv")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        if argv.is_empty() {
            return Err("payload.argv 必须是非空的字符串数组".to_string());
        }
        let mut env = BTreeMap::new();
        if let Some(map) = payload.get("env").and_then(Value::as_object) {
            for (key, value) in map {
                match value.as_str() {
                    Some(text) => {
                        env.insert(key.clone(), text.to_string());
                    }
                    None => return Err(format!("payload.env[{key}] 必须是字符串")),
                }
            }
        }
        Ok(SpawnSpec {
            argv,
            env,
            cwd: payload
                .get("cwd")
                .and_then(Value::as_str)
                .map(str::to_string),
            log_path: payload
                .get("log_path")
                .and_then(Value::as_str)
                .map(str::to_string),
            handshake_timeout_ms: payload
                .get("handshake_timeout_ms")
                .and_then(Value::as_u64)
                .filter(|ms| *ms > 0)
                .unwrap_or(DEFAULT_HANDSHAKE_TIMEOUT_MS),
            label: payload
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        })
    }

    /// 会话键：**spawn 规格的内容哈希**。
    ///
    /// argv 里已经带着 (解释器, 脚本, figures-dir, out-dir, sandbox, entry)，
    /// 所以「换了 entry」「换了项目」「换了解释器」都天然是另一个会话——
    /// 不必在 workerd 里重新理解这些概念。label 与 log_path 不参与（它们是
    /// 观测用的附属信息，改它不该让会话重建）。
    pub fn hash(&self) -> String {
        let mut text = String::new();
        let value = serde_json::json!({
            "argv": self.argv,
            "env": self.env.iter().map(|(k, v)| (k.clone(), Value::String(v.clone())))
                .collect::<serde_json::Map<_, _>>(),
            "cwd": self.cwd,
        });
        patchspec::write_value(&value, &mut text);
        patchspec::sha256_hex(text.as_bytes())
    }

    pub fn handshake_timeout(&self) -> Duration {
        Duration::from_millis(self.handshake_timeout_ms)
    }
}

/// 读线程发出来的事件，**一律带这一代的序号**。
#[derive(Debug)]
pub enum WorkerEvent {
    /// 一行合法 JSON 响应。
    Line(u64, Value),
    /// 一行读回来了但不是 JSON——worker 往 stdout 打了脏东西。
    Garbage(u64, String),
    /// stdout 到了 EOF：这一代的 worker 已经没了。
    Eof(u64),
}

/// 管道 EOF 之后子进程的下场——随 `session_dead` 的信封带出去。
///
/// 「渲染进程崩溃」这一句以前是 EOF 的全部描述，而 EOF 背后至少有四种事实，
/// 用户的下一步各不相同：自己以 0 退出（脚本 `sys.exit()` / stdin 被关）、以非零
/// 退出（Python 致命错误、`abort()`）、被信号杀掉（POSIX 的 SIGSEGV / SIGKILL）、
/// 关了管道却还活着（fd 1 被脚本关掉）。退出码是分辨它们的唯一凭据——Windows 上
/// 0xC0000005（access violation）与 0xC0000135（DLL not found）都藏在这个数里。
/// **怎么解释这个数归 Python 侧**（`pool.explain_exit`），这里只如实报。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExitReport {
    /// 进程自己的退出码。Windows 上是 NTSTATUS 按 i32 解释（0xC0000005 → -1073741819）。
    pub code: Option<i32>,
    /// POSIX 上被哪个信号杀掉；Windows 恒 `None`。
    pub signal: Option<i32>,
    /// EOF 之后进程在宽限期内**没有**自己退出，由 workerd 收掉——此时 code / signal
    /// 说的是被收掉的那一下，不是它自己的死因。
    pub lingered: bool,
}

impl ExitReport {
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "code": self.code,
            "signal": self.signal,
            "lingered": self.lingered,
        })
    }

    /// 一句人话（supervisor 层的口径；Python 侧会按退出码再解释一层）。
    pub fn describe(&self) -> String {
        if self.lingered {
            return "关掉了协议管道但没有退出，已被终止".to_string();
        }
        match (self.code, self.signal) {
            (Some(code), _) => format!("退出码 {code}"),
            (None, Some(sig)) => format!("被信号 {sig} 终止"),
            (None, None) => "退出状态未知".to_string(),
        }
    }
}

/// EOF 后等它自己退出的宽限：真实崩溃里「关 stdout → 进程消失」是微秒级，这里
/// 只是给 Windows 上慢半拍的进程回收留余量，不是等脚本跑完。
pub const EXIT_GRACE: Duration = Duration::from_millis(1500);

fn exit_report_of(status: std::process::ExitStatus, lingered: bool) -> ExitReport {
    #[cfg(unix)]
    let signal = {
        use std::os::unix::process::ExitStatusExt;
        status.signal()
    };
    #[cfg(not(unix))]
    let signal: Option<i32> = None;
    ExitReport {
        code: status.code(),
        signal,
        lingered,
    }
}

/// 一个活着的 worker 子进程。
pub struct WorkerProc {
    /// 只在 spawn / kill / try_wait 时短暂持锁——I/O 走另外两条线程的句柄，
    /// 所以任何线程都能随时抢到它把进程杀掉（淘汰与取消都靠这条）。
    child: Arc<Mutex<Option<Child>>>,
    write_tx: Option<Sender<Vec<u8>>>,
    /// 最近一次写是否已经落到管道里。超时报错时据此区分「worker 没回」
    /// 与「连写都没写进去」（后者说明对面根本没在读）。
    wrote: Arc<AtomicBool>,
    pub generation: u64,
    pub pid: u32,
}

impl WorkerProc {
    pub fn spawn(
        spec: &SpawnSpec,
        generation: u64,
        events: Sender<WorkerEvent>,
    ) -> std::io::Result<WorkerProc> {
        let mut cmd = Command::new(&spec.argv[0]);
        cmd.args(&spec.argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped());
        for (key, value) in &spec.env {
            cmd.env(key, value);
        }
        if let Some(cwd) = &spec.cwd {
            cmd.current_dir(cwd);
        }
        match &spec.log_path {
            Some(path) => match OpenOptions::new().create(true).append(true).open(path) {
                Ok(file) => {
                    cmd.stderr(Stdio::from(file));
                }
                // 日志开不出来（只读介质 / 权限）不值得让渲染起不来
                Err(_) => {
                    cmd.stderr(Stdio::null());
                }
            },
            None => {
                cmd.stderr(Stdio::null());
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd.spawn()?;
        let pid = child.id();
        let stdin = child.stdin.take().expect("stdin 是 piped");
        let stdout = child.stdout.take().expect("stdout 是 piped");

        let wrote = Arc::new(AtomicBool::new(true));
        let (write_tx, write_rx) = std::sync::mpsc::channel::<Vec<u8>>();
        let writer_flag = Arc::clone(&wrote);
        std::thread::Builder::new()
            .name(format!("workerd-write-{pid}"))
            .spawn(move || {
                let mut stdin = stdin;
                for line in write_rx {
                    // 写失败（worker 已经退出 / 管道断了）不在这里报错：会话线程
                    // 等不到响应会按超时或 EOF 处置，两条路都比在这里多一个
                    // 错误来源清楚。
                    if stdin.write_all(&line).is_err() || stdin.flush().is_err() {
                        writer_flag.store(true, Ordering::SeqCst);
                        break;
                    }
                    writer_flag.store(true, Ordering::SeqCst);
                }
            })?;

        std::thread::Builder::new()
            .name(format!("workerd-read-{pid}"))
            .spawn(move || {
                let mut reader = BufReader::new(stdout);
                let mut buf: Vec<u8> = Vec::new();
                loop {
                    buf.clear();
                    // **按字节读，不用 `lines()`。** `BufRead::lines()` 把一行非法
                    // UTF-8 当成 `Err` 交回来，上面那个 `for … in lines()` 于是 break、
                    // 报 EOF——一条活得好好的 worker 被判成「进程崩溃」并被杀掉，
                    // 而真正发生的只是 fd 1 上多了几个非 UTF-8 字节（Windows 上
                    // cp936 的子进程输出、C 扩展直接 printf 都是这个形状）。
                    // 判据要对得上事实：字节坏了是「管道上有垃圾」（protocol_mismatch，
                    // 并把那一行原样带出去），只有 read 回 0 / 出错才是 EOF。
                    //
                    // **先判 UTF-8，再解析 JSON**（评审 #443 第九轮）：坏字节落在一个
                    // 合法 JSON 字串**里面**时（C 扩展往 fd 1 写的几个字节恰好夹进
                    // worker 正在输出的响应），lossy 替换成 U+FFFD 之后 serde 照样
                    // 解析成功、request_id 也对得上——一条被篡改过的响应就这么当成
                    // 正常结果收下了。lossy 的形态只用来把那一行带给人看。
                    // 与 Python 池同一口径：那边是 `surrogateescape` + 解析前查残留。
                    match reader.read_until(b'\n', &mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => {}
                    }
                    let event = match std::str::from_utf8(&buf) {
                        Ok(text) => {
                            let line = text.trim_end_matches(['\n', '\r']);
                            if line.trim().is_empty() {
                                continue;
                            }
                            match serde_json::from_str::<Value>(line) {
                                Ok(value) => WorkerEvent::Line(generation, value),
                                Err(_) => WorkerEvent::Garbage(generation, line.to_string()),
                            }
                        }
                        Err(_) => {
                            let shown = String::from_utf8_lossy(&buf);
                            WorkerEvent::Garbage(
                                generation,
                                shown.trim_end_matches(['\n', '\r']).to_string(),
                            )
                        }
                    };
                    if events.send(event).is_err() {
                        return; // 会话没了，读线程跟着退出
                    }
                }
                let _ = events.send(WorkerEvent::Eof(generation));
            })?;

        Ok(WorkerProc {
            child: Arc::new(Mutex::new(Some(child))),
            write_tx: Some(write_tx),
            wrote,
            generation,
            pid,
        })
    }

    /// 把一行请求交给写线程——**永不阻塞调用者**。
    pub fn send_line(&self, line: String) {
        self.wrote.store(false, Ordering::SeqCst);
        if let Some(tx) = &self.write_tx {
            let mut bytes = line.into_bytes();
            bytes.push(b'\n');
            let _ = tx.send(bytes);
        }
    }

    /// 最近一次请求是否真的写进了管道。
    pub fn wrote_last(&self) -> bool {
        self.wrote.load(Ordering::SeqCst)
    }

    pub fn is_alive(&self) -> bool {
        let mut guard = match self.child.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        match guard.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(None)),
            None => false,
        }
    }

    /// 管道 EOF 之后收尸并报告下场：先给它 `grace` 自己退出，没退就 kill。
    ///
    /// 与 `kill()` 的差别只在**先看它自己怎么死的**：EOF 那一刻直接 kill，
    /// 退出码永远是 TerminateProcess / SIGKILL 的那一个，真正的死因就被盖掉了。
    pub fn reap_after_eof(&self, grace: Duration) -> ExitReport {
        let mut guard = match self.child.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let Some(child) = guard.as_mut() else {
            return ExitReport {
                code: None,
                signal: None,
                lingered: false,
            };
        };
        let deadline = std::time::Instant::now() + grace;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => return exit_report_of(status, false),
                Ok(None) if std::time::Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20));
                }
                Ok(None) => break,
                Err(_) => break,
            }
        }
        let _ = child.kill();
        match child.wait() {
            Ok(status) => exit_report_of(status, true),
            Err(_) => ExitReport {
                code: None,
                signal: None,
                lingered: true,
            },
        }
    }

    /// 强杀并回收。**任何线程都能调**（淘汰、取消、超时三条路共用）。
    pub fn kill(&self) {
        let mut guard = match self.child.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// 关掉 stdin（写线程随之退出）——优雅关停时让 worker 读到 EOF。
    pub fn close_stdin(&mut self) {
        self.write_tx = None;
    }
}

impl Drop for WorkerProc {
    fn drop(&mut self) {
        // 绝不在用户机器上留下孤儿 python 进程：会话对象一消失，子进程就得走。
        self.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn spec_hash_ignores_label_and_log_path() {
        let base = json!({"argv": ["python", "worker.py"], "env": {"A": "1"}});
        let a = SpawnSpec::from_payload(&base).unwrap();
        let mut with_label = base.clone();
        with_label["label"] = json!("fig1.py");
        with_label["log_path"] = json!("/tmp/a.log");
        let b = SpawnSpec::from_payload(&with_label).unwrap();
        assert_eq!(a.hash(), b.hash());
    }

    #[test]
    fn spec_hash_changes_with_argv_env_and_cwd() {
        let a = SpawnSpec::from_payload(&json!({"argv": ["python", "worker.py"]})).unwrap();
        let b =
            SpawnSpec::from_payload(&json!({"argv": ["python", "worker.py", "--entry", "draw"]}))
                .unwrap();
        let c =
            SpawnSpec::from_payload(&json!({"argv": ["python", "worker.py"], "env": {"A": "1"}}))
                .unwrap();
        let d = SpawnSpec::from_payload(&json!({"argv": ["python", "worker.py"], "cwd": "/tmp"}))
            .unwrap();
        let hashes = [a.hash(), b.hash(), c.hash(), d.hash()];
        for i in 0..hashes.len() {
            for j in (i + 1)..hashes.len() {
                assert_ne!(hashes[i], hashes[j], "{i} vs {j}");
            }
        }
    }

    #[test]
    fn empty_argv_is_rejected() {
        assert!(SpawnSpec::from_payload(&json!({"argv": []})).is_err());
        assert!(SpawnSpec::from_payload(&json!({})).is_err());
    }
}
