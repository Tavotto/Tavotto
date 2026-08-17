//! supervisor 的行为验收：队列合并、有界拒绝、超时强杀、代序、取消、淘汰。
//!
//! 驱动的是**真的 magplot-workerd 二进制**（`CARGO_BIN_EXE_*`），worker 那一端
//! 换成 `tests/fake_worker.py`（说 v1 协议的小脚本）。这样每条用例验的都是
//! supervisor 自己那部分，不掺 matplotlib 的启动时间与随机慢。

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

fn python() -> String {
    std::env::var("PYTHON").unwrap_or_else(|_| "python3".to_string())
}

fn fake_worker() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fake_worker.py")
}

/// 一个跑着的 workerd + 一条能按 request_id 取响应的收件箱。
///
/// 响应**必然乱序**（被顶掉的那条会先于在飞的那条回来），所以收件箱是个池子，
/// 按 request_id 取，取不到就等——这也正是 Python 客户端要做的事。
struct Workerd {
    child: Child,
    stdin: ChildStdin,
    inbox: Arc<(Mutex<Vec<Value>>, Condvar)>,
    seq: u64,
}

impl Workerd {
    fn start() -> Workerd {
        let mut child = Command::new(env!("CARGO_BIN_EXE_magplot-workerd"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("起 magplot-workerd");
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let inbox = Arc::new((Mutex::new(Vec::new()), Condvar::new()));
        let sink = Arc::clone(&inbox);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Ok(value) = serde_json::from_str::<Value>(&line) {
                    sink.0.lock().unwrap().push(value);
                    sink.1.notify_all();
                }
            }
        });
        let mut wd = Workerd {
            child,
            stdin,
            inbox,
            seq: 0,
        };
        wd.call(
            "hello",
            json!({"max_sessions": 3, "max_queue": 32}),
            None,
            None,
            0,
        );
        wd
    }

    fn next_id(&mut self) -> String {
        self.seq += 1;
        format!("c-{}", self.seq)
    }

    /// 发一条请求，回它的 request_id。
    fn send(
        &mut self,
        op: &str,
        payload: Value,
        session_id: Option<&str>,
        stem: Option<&str>,
        timeout_ms: u64,
    ) -> String {
        let rid = self.next_id();
        let mut req = json!({
            "supervisor_protocol_version": 1,
            "request_id": rid,
            "op": op,
            "payload": payload,
        });
        let map = req.as_object_mut().unwrap();
        if let Some(sid) = session_id {
            map.insert("session_id".into(), json!(sid));
        }
        if let Some(stem) = stem {
            map.insert("stem".into(), json!(stem));
        }
        if timeout_ms > 0 {
            map.insert("timeout_ms".into(), json!(timeout_ms));
        }
        writeln!(self.stdin, "{req}").expect("写 workerd stdin");
        self.stdin.flush().unwrap();
        rid
    }

    /// 发一条请求并等它的响应。
    fn call(
        &mut self,
        op: &str,
        payload: Value,
        session_id: Option<&str>,
        stem: Option<&str>,
        timeout_ms: u64,
    ) -> Value {
        let rid = self.send(op, payload, session_id, stem, timeout_ms);
        self.wait(&rid, Duration::from_secs(30))
    }

    fn wait(&self, request_id: &str, timeout: Duration) -> Value {
        let deadline = Instant::now() + timeout;
        let (lock, cv) = &*self.inbox;
        let mut pool = lock.lock().unwrap();
        loop {
            if let Some(index) = pool
                .iter()
                .position(|v| v["request_id"].as_str() == Some(request_id))
            {
                return pool.remove(index);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            assert!(
                !remaining.is_zero(),
                "等 {request_id} 的响应超时；收件箱: {pool:#?}"
            );
            let (guard, _) = cv.wait_timeout(pool, remaining).unwrap();
            pool = guard;
        }
    }

    /// 开一条用 fake_worker.py 的会话，回 (session_id, 响应)。
    fn open(&mut self, flags: &[&str], handshake_ms: u64) -> (String, Value) {
        let mut argv: Vec<String> = vec![python(), fake_worker().to_string_lossy().into_owned()];
        argv.extend(flags.iter().map(|s| s.to_string()));
        let resp = self.call(
            "open_session",
            json!({"argv": argv, "handshake_timeout_ms": handshake_ms, "label": "fake"}),
            None,
            None,
            0,
        );
        let sid = resp["session_id"].as_str().unwrap_or_default().to_string();
        (sid, resp)
    }
}

impl Drop for Workerd {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn trace_path(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join("magplot-workerd-tests");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("{name}-{}.jsonl", std::process::id()));
    let _ = std::fs::remove_file(&path);
    path
}

fn read_trace(path: &PathBuf) -> Vec<Value> {
    std::fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn err_code(resp: &Value) -> &str {
    resp["error"]["code"].as_str().unwrap_or("<无 code>")
}

// ------------------------------ 握手 ------------------------------

#[test]
fn hello_declares_versions_and_capabilities() {
    let mut wd = Workerd::start();
    let resp = wd.call("hello", json!({"max_sessions": 2}), None, None, 0);
    assert_eq!(resp["ok"], true);
    assert_eq!(resp["supervisor_protocol_version"], 1);
    assert_eq!(resp["worker_protocol_version"], 1);
    assert_eq!(resp["max_sessions"], 2);
    let caps: Vec<&str> = resp["capabilities"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert!(caps.contains(&"generation_fencing") && caps.contains(&"queue_merge_latest"));
}

#[test]
fn open_session_pings_the_worker_before_reporting_ready() {
    let trace = trace_path("handshake");
    let mut wd = Workerd::start();
    let (sid, resp) = wd.open(&["--trace", trace.to_str().unwrap()], 10_000);
    assert_eq!(resp["ok"], true, "{resp:#?}");
    assert!(!sid.is_empty());
    assert_eq!(resp["generation"], 1);
    // 握手就是一条 v1 ping——「起来了但不说 v1」必须在这里就暴露
    let seen = read_trace(&trace);
    assert_eq!(seen.len(), 1);
    assert_eq!(seen[0]["cmd"], "ping");
    assert_eq!(seen[0]["worker_generation"], 1);
}

#[test]
fn a_missing_interpreter_is_spawn_failed_not_a_hang() {
    let mut wd = Workerd::start();
    let resp = wd.call(
        "open_session",
        json!({"argv": ["/nonexistent/python-that-is-not-there", "x.py"]}),
        None,
        None,
        0,
    );
    assert_eq!(resp["ok"], false);
    assert_eq!(err_code(&resp), "spawn_failed");
}

#[test]
fn a_silent_worker_is_handshake_timeout() {
    let mut wd = Workerd::start();
    let (_, resp) = wd.open(&["--hang-handshake"], 700);
    assert_eq!(resp["ok"], false, "{resp:#?}");
    assert_eq!(err_code(&resp), "handshake_timeout");
}

// ------------------------------ 信封 ------------------------------

#[test]
fn worker_requests_carry_generation_and_the_canonical_patch_hash() {
    let trace = trace_path("envelope");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--trace", trace.to_str().unwrap()], 10_000);

    // 乱序 + 重复的写法：hash 必须等于 golden vector 里 order_independent 那一组
    let patches = json!([
        {"gid": "fig.ax1.ylabel", "prop": "fontsize", "value": 9},
        {"gid": "fig.ax0.title", "prop": "text", "value": "T"},
        {"gid": "fig.ax0.title", "prop": "fontsize", "value": 11},
        {"gid": "fig.ax1.ylabel", "prop": "fontsize", "value": 10}
    ]);
    let resp = wd.call(
        "render",
        json!({"patches": patches}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(resp["ok"], true, "{resp:#?}");
    assert_eq!(resp["manifest"]["stem"], "Fig1");

    let seen = read_trace(&trace);
    let render = seen
        .iter()
        .find(|r| r["cmd"] == "render")
        .expect("有一条 render");
    assert_eq!(render["worker_generation"], 1);
    assert_eq!(
        render["canonical_patch_hash"],
        "sha256:fd01355c8b262aa90f2a2cd1e3344fe941ebdb574256118ac6039e81d32116b5"
    );
    // **下发的是原始列表**：规范序只定身份，拿去应用会静静改掉渲染结果
    assert_eq!(render["payload"]["patches"], patches);
}

#[test]
fn a_mismatched_echo_kills_the_session() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--wrong-request-id"], 10_000);
    let resp = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(resp["ok"], false, "{resp:#?}");
    assert_eq!(err_code(&resp), "protocol_mismatch");
}

#[test]
fn a_foreign_protocol_version_kills_the_session() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--bad-protocol-version"], 10_000);
    let resp = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(err_code(&resp), "protocol_mismatch");
}

#[test]
fn garbage_on_the_protocol_pipe_is_protocol_mismatch() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--garbage"], 10_000);
    let resp = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(err_code(&resp), "protocol_mismatch");
}

// ------------------------------ 队列 ------------------------------

#[test]
fn a_newer_render_supersedes_the_queued_one_for_the_same_stem() {
    let trace = trace_path("merge");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(
        &[
            "--trace",
            trace.to_str().unwrap(),
            "--first-sleep-ms",
            "1200",
        ],
        10_000,
    );

    // r1 进在飞（睡 1.2s）；r2 / r3 落在队列里，r3 顶掉 r2
    let r1 = wd.send(
        "render",
        json!({"patches": [], "tag": 1}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    std::thread::sleep(Duration::from_millis(250));
    let r2 = wd.send(
        "render",
        json!({"patches": [], "tag": 2}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    let r3 = wd.send(
        "render",
        json!({"patches": [], "tag": 3}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    // 另一张图不受影响——合并键是 (会话, stem)
    let r4 = wd.send(
        "render",
        json!({"patches": [], "tag": 4}),
        Some(&sid),
        Some("Fig2"),
        20_000,
    );

    let resp2 = wd.wait(&r2, Duration::from_secs(5));
    assert_eq!(resp2["ok"], false, "{resp2:#?}");
    assert_eq!(err_code(&resp2), "queue_superseded");

    assert_eq!(wd.wait(&r1, Duration::from_secs(20))["ok"], true);
    assert_eq!(wd.wait(&r3, Duration::from_secs(20))["ok"], true);
    assert_eq!(wd.wait(&r4, Duration::from_secs(20))["ok"], true);

    let tags: Vec<i64> = read_trace(&trace)
        .iter()
        .filter(|r| r["cmd"] == "render")
        .map(|r| r["payload"]["tag"].as_i64().unwrap())
        .collect();
    // 被顶掉的那条**从来没到过 worker**——这才是合并的意义（省掉一次真渲染）
    assert_eq!(tags, vec![1, 3, 4], "只有 1/3/4 该到 worker");
}

#[test]
fn in_flight_requests_are_never_preempted() {
    let trace = trace_path("no-preempt");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(
        &[
            "--trace",
            trace.to_str().unwrap(),
            "--first-sleep-ms",
            "900",
        ],
        10_000,
    );
    let r1 = wd.send(
        "render",
        json!({"patches": [], "tag": 1}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    std::thread::sleep(Duration::from_millis(200));
    let r2 = wd.send(
        "render",
        json!({"patches": [], "tag": 2}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );

    // 在飞的那条照样跑完并成功——worker 没有协作中断，抢占只能靠杀进程
    assert_eq!(wd.wait(&r1, Duration::from_secs(20))["ok"], true);
    assert_eq!(wd.wait(&r2, Duration::from_secs(20))["ok"], true);
    let tags: Vec<i64> = read_trace(&trace)
        .iter()
        .filter(|r| r["cmd"] == "render")
        .map(|r| r["payload"]["tag"].as_i64().unwrap())
        .collect();
    assert_eq!(tags, vec![1, 2]);
}

#[test]
fn exports_are_executed_in_order_and_never_merged() {
    let trace = trace_path("exports");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(
        &[
            "--trace",
            trace.to_str().unwrap(),
            "--first-sleep-ms",
            "800",
        ],
        10_000,
    );
    let mut ids = Vec::new();
    for i in 0..4 {
        ids.push(wd.send(
            "export",
            json!({"patches": [], "path": format!("/tmp/out-{i}.pdf"), "format": "pdf", "dpi": 300}),
            Some(&sid),
            Some("Fig1"),
            20_000,
        ));
    }
    for id in &ids {
        let resp = wd.wait(id, Duration::from_secs(20));
        assert_eq!(resp["ok"], true, "{resp:#?}");
    }
    let paths: Vec<String> = read_trace(&trace)
        .iter()
        .filter(|r| r["cmd"] == "export")
        .map(|r| r["payload"]["path"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(
        paths,
        vec![
            "/tmp/out-0.pdf".to_string(),
            "/tmp/out-1.pdf".to_string(),
            "/tmp/out-2.pdf".to_string(),
            "/tmp/out-3.pdf".to_string()
        ],
        "导出一条都不能少，顺序也不能乱"
    );
}

#[test]
fn a_full_queue_rejects_immediately_instead_of_blocking() {
    let mut wd = Workerd::start();
    wd.call("hello", json!({"max_queue": 2}), None, None, 0);
    let (sid, _) = wd.open(&["--first-sleep-ms", "1500"], 10_000);

    let _busy = wd.send(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    std::thread::sleep(Duration::from_millis(250));
    // stem 各不相同 → 不触发合并，纯粹堆进队列
    let mut ids = Vec::new();
    for stem in ["Fig2", "Fig3", "Fig4", "Fig5"] {
        ids.push(wd.send(
            "render",
            json!({"patches": []}),
            Some(&sid),
            Some(stem),
            20_000,
        ));
    }
    // 满了的那几条**当场**就回来了，不必等在飞的那 1.5 秒跑完：
    // 这里只给 400ms（远小于剩下的 ~1.2s），能收到就说明没被挂在队列上。
    std::thread::sleep(Duration::from_millis(400));
    let rejected: Vec<Value> = ids
        .iter()
        .filter_map(|rid| {
            let (lock, _) = &*wd.inbox;
            let pool = lock.lock().unwrap();
            pool.iter()
                .find(|v| v["request_id"].as_str() == Some(rid.as_str()))
                .cloned()
        })
        .collect();
    let codes: Vec<String> = rejected.iter().map(|r| err_code(r).to_string()).collect();
    assert!(
        codes.iter().filter(|c| *c == "queue_full").count() >= 2,
        "队列上限 2，第 3、4 条该被当场拒绝: {codes:?}"
    );

    for rid in &ids {
        let resp = wd.wait(rid, Duration::from_secs(20));
        // 排上队的那两条照常跑完（stem 不在 fake worker 的清单里 → unknown_stem）
        if resp["ok"] == false {
            assert!(
                matches!(err_code(&resp), "queue_full" | "unknown_stem"),
                "{resp:#?}"
            );
        }
    }
}

// ------------------------------ 超时 / 取消 / 崩溃 ------------------------------

#[test]
fn a_timeout_kills_the_worker_and_the_next_request_rebuilds_it() {
    let trace = trace_path("timeout");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--trace", trace.to_str().unwrap(), "--hang"], 10_000);

    let resp = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        600,
    );
    assert_eq!(resp["ok"], false, "{resp:#?}");
    assert_eq!(err_code(&resp), "worker_timeout");
    assert!(resp["error"]["message"].as_str().unwrap().contains("重试"));

    // 原地重建：generation +1，握手重做一次
    let resp = wd.call("build", json!({}), Some(&sid), None, 10_000);
    assert_eq!(resp["ok"], true, "{resp:#?}");
    assert_eq!(resp["generation"], 2);
    let pings: Vec<i64> = read_trace(&trace)
        .iter()
        .filter(|r| r["cmd"] == "ping")
        .map(|r| r["worker_generation"].as_i64().unwrap())
        .collect();
    assert_eq!(pings, vec![1, 2], "每 (re)spawn 都要重新握手且代序 +1");
}

#[test]
fn cancelling_a_queued_request_removes_it_without_touching_the_worker() {
    let trace = trace_path("cancel-queued");
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(
        &[
            "--trace",
            trace.to_str().unwrap(),
            "--first-sleep-ms",
            "1200",
        ],
        10_000,
    );
    let busy = wd.send(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    std::thread::sleep(Duration::from_millis(250));
    let queued = wd.send(
        "export",
        json!({"patches": [], "path": "/tmp/cancelled.pdf"}),
        Some(&sid),
        Some("Fig1"),
        20_000,
    );
    let resp = wd.call(
        "cancel",
        json!({"target_request_id": queued}),
        Some(&sid),
        None,
        0,
    );
    assert_eq!(resp["ok"], true, "{resp:#?}");
    assert_eq!(resp["outcome"], "queued_removed");

    let cancelled = wd.wait(&queued, Duration::from_secs(5));
    assert_eq!(err_code(&cancelled), "cancelled");
    // 在飞的那条不受影响
    assert_eq!(wd.wait(&busy, Duration::from_secs(20))["ok"], true);
    assert!(
        !read_trace(&trace).iter().any(|r| r["cmd"] == "export"),
        "取消掉的导出不该到过 worker"
    );
}

#[test]
fn cancelling_an_in_flight_request_kills_the_worker() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--hang"], 10_000);
    let busy = wd.send(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        60_000,
    );
    std::thread::sleep(Duration::from_millis(400));

    let resp = wd.call(
        "cancel",
        json!({"target_request_id": busy}),
        Some(&sid),
        None,
        0,
    );
    assert_eq!(resp["outcome"], "in_flight_killed");
    // ADR 0003 §6：协议层没有协作中断，硬取消就是杀进程——所以那条请求收到的是
    // `cancelled` 而不是「取消成功但还在跑」这种假象
    let cancelled = wd.wait(&busy, Duration::from_secs(10));
    assert_eq!(err_code(&cancelled), "cancelled", "{cancelled:#?}");
}

#[test]
fn cancelling_something_already_finished_is_an_honest_noop() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&[], 10_000);
    let done = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(done["ok"], true);
    let resp = wd.call(
        "cancel",
        json!({"target_request_id": "c-早跑完了"}),
        Some(&sid),
        None,
        0,
    );
    assert_eq!(resp["ok"], true, "取消永远回 ok（幂等的尽力而为）");
    assert_eq!(resp["outcome"], "unknown");
}

#[test]
fn a_crashed_worker_reports_session_dead_and_rebuilds() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&["--die-on-render"], 10_000);
    let resp = wd.call(
        "render",
        json!({"patches": []}),
        Some(&sid),
        Some("Fig1"),
        10_000,
    );
    assert_eq!(err_code(&resp), "session_dead", "{resp:#?}");
    // 会话还在，下一条请求原地重建（generation +1）
    let resp = wd.call("build", json!({}), Some(&sid), None, 10_000);
    assert_eq!(resp["ok"], true, "{resp:#?}");
    assert_eq!(resp["generation"], 2);
}

// ------------------------------ 会话表 ------------------------------

#[test]
fn the_same_spawn_spec_reuses_one_worker() {
    let mut wd = Workerd::start();
    let (a, _) = wd.open(&[], 10_000);
    let (b, resp) = wd.open(&[], 10_000);
    assert_eq!(a, b, "同一份 spawn 规格必须复用同一条会话");
    assert_eq!(resp["reused"], true);

    // 引用计数：第一次 close 只放掉一个句柄，worker 还活着
    let first = wd.call("close_session", json!({}), Some(&a), None, 5_000);
    assert_eq!(first["released"], true);
    assert_eq!(
        wd.call("build", json!({}), Some(&a), None, 10_000)["ok"],
        true
    );
    let second = wd.call("close_session", json!({}), Some(&a), None, 5_000);
    assert_eq!(second["closed"], true);
    let after = wd.call("build", json!({}), Some(&a), None, 10_000);
    assert_eq!(err_code(&after), "unknown_session");
}

#[test]
fn exceeding_the_session_cap_evicts_the_least_recently_used() {
    let mut wd = Workerd::start();
    wd.call("hello", json!({"max_sessions": 1}), None, None, 0);
    let (a, _) = wd.open(&["--stems", "Fig1"], 10_000);
    let (b, _) = wd.open(&["--stems", "FigB"], 10_000);
    assert_ne!(a, b);
    // 淘汰是 kill，不等锁：a 立刻就不认了
    let resp = wd.call("build", json!({}), Some(&a), None, 10_000);
    assert_eq!(err_code(&resp), "unknown_session", "{resp:#?}");
    assert_eq!(
        wd.call("build", json!({}), Some(&b), None, 10_000)["ok"],
        true
    );
}

#[test]
fn closing_an_unknown_session_is_idempotent() {
    let mut wd = Workerd::start();
    let resp = wd.call("close_session", json!({}), Some("s-没有这个"), None, 5_000);
    assert_eq!(resp["ok"], true);
    assert_eq!(resp["known"], false);
}

#[test]
fn unknown_ops_and_bad_envelopes_are_structured_errors() {
    let mut wd = Workerd::start();
    let resp = wd.call("没有这个操作", json!({}), None, None, 0);
    assert_eq!(err_code(&resp), "unknown_op");

    writeln!(
        wd.stdin,
        "{{\"supervisor_protocol_version\":9,\"request_id\":\"c-bad\",\"op\":\"ping\"}}"
    )
    .unwrap();
    wd.stdin.flush().unwrap();
    let resp = wd.wait("c-bad", Duration::from_secs(5));
    assert_eq!(err_code(&resp), "bad_request");
}

#[test]
fn shutdown_leaves_no_orphan_worker_processes() {
    let mut wd = Workerd::start();
    let (_, resp) = wd.open(&[], 10_000);
    let pid = resp["pid"]
        .as_u64()
        .expect("open_session 要回 worker 的 pid") as u32;
    assert!(pid > 0);
    assert!(process_alive(pid), "worker 该活着");

    let resp = wd.call("shutdown", json!({}), None, None, 0);
    assert_eq!(resp["ok"], true);
    let _ = wd.child.wait();

    let deadline = Instant::now() + Duration::from_secs(5);
    while process_alive(pid) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(!process_alive(pid), "workerd 退出后不许留下渲染子进程");
}

#[cfg(unix)]
fn process_alive(pid: u32) -> bool {
    Command::new("ps")
        .arg("-p")
        .arg(pid.to_string())
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[cfg(windows)]
fn process_alive(pid: u32) -> bool {
    let out = Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH"])
        .output();
    match out {
        Ok(out) => String::from_utf8_lossy(&out.stdout).contains(&pid.to_string()),
        Err(_) => false,
    }
}

/// 收件箱里不许出现对不上任何请求的响应（多路复用的基本前提）。
#[test]
fn every_response_pairs_with_a_request() {
    let mut wd = Workerd::start();
    let (sid, _) = wd.open(&[], 10_000);
    let mut sent: BTreeMap<String, ()> = BTreeMap::new();
    for stem in ["Fig1", "Fig2", "Fig1", "Fig2"] {
        sent.insert(
            wd.send(
                "render",
                json!({"patches": []}),
                Some(&sid),
                Some(stem),
                20_000,
            ),
            (),
        );
    }
    for rid in sent.keys() {
        let resp = wd.wait(rid, Duration::from_secs(20));
        assert_eq!(resp["request_id"].as_str().unwrap(), rid);
        assert_eq!(resp["session_id"].as_str().unwrap(), sid);
    }
}
