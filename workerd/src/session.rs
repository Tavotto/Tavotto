//! 会话：一个 worker 子进程 + 一条有界队列 + 一代序号。
//!
//! 三条纪律都在这个文件里：
//!
//! * **generation**：每 (re)spawn +1，随每条 worker 请求下发。读线程给每行响应打上
//!   它那一代的号，会话线程只认当前代——上一代的迟到响应直接丢弃，**旧结果绝不
//!   覆盖新状态**。
//! * **最新合并**：per (会话, stem) 的 render 在队列里至多一条，新的顶掉旧的，
//!   被顶掉的**立刻**收到 `queue_superseded`（不是静默丢弃：调用方在等一条响应）。
//!   在飞的那条不抢占——worker 没有协作中断（ADR 0003 §6），抢占只能靠杀进程。
//! * **淘汰 = kill**：关会话从不等锁。Python 池的 `shutdown()` 要抢 `w.lock`，
//!   正在渲染时就一起卡住；这里 kill 是任何线程都能立刻做到的事。

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::patchspec;
use crate::protocol::*;
use crate::worker::{SpawnSpec, WorkerEvent, WorkerProc};

/// 一条排队中的活。
pub struct Job {
    pub client_request_id: String,
    pub kind: JobKind,
    pub stem: Option<String>,
    pub payload: Value,
    pub timeout: Duration,
    /// 静默看门狗（ADR 0050）。`None` = 平坦上限，与看门狗登场之前相同。
    pub idle: Option<Duration>,
}

pub enum JobKind {
    /// spawn + 握手，回的是 `open_session` 那条请求。
    Open,
    /// 转成 worker 协议 v1 的一条命令。
    Worker {
        cmd: &'static str,
        /// `Some(stem)` = 可被同键的新请求顶掉（只有 render 给）。
        merge_key: Option<String>,
    },
}

struct CloseRequest {
    /// 淘汰（LRU）时没有对应的客户请求，所以是 Option。
    client_request_id: Option<String>,
    force: bool,
    timeout: Duration,
}

struct State {
    queue: VecDeque<Job>,
    in_flight: Option<String>,
    /// 在飞的那条被 cancel 了：kill 之后读到 EOF 要回 `cancelled` 而不是 `session_dead`。
    cancel_in_flight: bool,
    close: Option<CloseRequest>,
    accepting: bool,
    last_used: Instant,
}

struct Inner {
    id: String,
    spec: SpawnSpec,
    out: Sender<Response>,
    state: Mutex<State>,
    cv: Condvar,
    max_queue: usize,
    generation: AtomicU64,
    revision: AtomicU64,
    /// 当前子进程。**任何线程**都能锁上它把进程杀掉（取消 / 淘汰 / 关闭三条路）。
    proc: Mutex<Option<WorkerProc>>,
}

pub struct Session {
    pub id: String,
    pub spec_hash: String,
    pub label: String,
    inner: Arc<Inner>,
    /// 打开这条会话的句柄数。同一份 spawn 规格重复 open 时复用同一个 worker，
    /// 引用清零才真关——否则「新 EngineWorker 刚建好、旧的正在异步关停」这个
    /// 常见的交叠窗口会把刚建好的会话关掉。
    refs: Mutex<usize>,
}

impl Session {
    /// 建会话并起线程；`Open` 这条活由会话线程执行（spawn 可能要几秒到几十秒，
    /// 不能占住 workerd 的主循环——那条循环还要服务其他会话的请求）。
    pub fn start(
        id: String,
        spec: SpawnSpec,
        out: Sender<Response>,
        max_queue: usize,
        open_request_id: String,
    ) -> std::io::Result<Arc<Session>> {
        let (events_tx, events_rx) = std::sync::mpsc::channel::<WorkerEvent>();
        let spec_hash = spec.hash();
        let label = spec.label.clone();
        let inner = Arc::new(Inner {
            id: id.clone(),
            spec,
            out,
            state: Mutex::new(State {
                queue: VecDeque::new(),
                in_flight: None,
                cancel_in_flight: false,
                close: None,
                accepting: true,
                last_used: Instant::now(),
            }),
            cv: Condvar::new(),
            max_queue,
            generation: AtomicU64::new(0),
            revision: AtomicU64::new(0),
            proc: Mutex::new(None),
        });
        inner.state.lock().unwrap().queue.push_back(Job {
            client_request_id: open_request_id,
            kind: JobKind::Open,
            stem: None,
            payload: json!({}),
            timeout: inner.spec.handshake_timeout(),
            idle: None,
        });

        let worker_inner = Arc::clone(&inner);
        std::thread::Builder::new()
            .name(format!("workerd-session-{id}"))
            .spawn(move || run(worker_inner, events_tx, events_rx))?;

        Ok(Arc::new(Session {
            id,
            spec_hash,
            label,
            inner,
            refs: Mutex::new(1),
        }))
    }

    pub fn generation(&self) -> u64 {
        self.inner.generation.load(Ordering::SeqCst)
    }

    pub fn last_used(&self) -> Instant {
        self.inner.state.lock().unwrap().last_used
    }

    pub fn retain(&self) -> usize {
        let mut refs = self.refs.lock().unwrap();
        *refs += 1;
        *refs
    }

    /// 放掉一个句柄；回 true 表示已经没人用了，可以关。
    pub fn release(&self) -> bool {
        let mut refs = self.refs.lock().unwrap();
        *refs = refs.saturating_sub(1);
        *refs == 0
    }

    /// 排一条活。合并与有界拒绝都在这里——**两种拒绝都当场回响应**，
    /// 绝不把调用方挂在一条永远排不上的队上。
    pub fn enqueue(&self, job: Job) -> Result<(), ProtoError> {
        let mut state = self.inner.state.lock().unwrap();
        if !state.accepting {
            return Err(ProtoError::new(
                CODE_SESSION_DEAD,
                true,
                format!("渲染会话 {} 已关闭，请重新打开", self.id),
            ));
        }
        state.last_used = Instant::now();

        if let JobKind::Worker {
            merge_key: Some(key),
            ..
        } = &job.kind
        {
            let hit = state.queue.iter().position(|queued| match &queued.kind {
                JobKind::Worker {
                    merge_key: Some(other),
                    ..
                } => other == key,
                _ => false,
            });
            if let Some(index) = hit {
                // 原位替换：新的顶掉旧的，但不插队到已经排在前面的 export 之前。
                let superseded = std::mem::replace(&mut state.queue[index], job);
                let out = self.inner.out.clone();
                let sid = self.id.clone();
                let gen = self.generation();
                drop(state);
                let _ = out.send(
                    Response::err(
                        Some(superseded.client_request_id),
                        ProtoError::new(
                            CODE_QUEUE_SUPERSEDED,
                            false,
                            "同一张图上有更新的渲染请求，这一条已被顶替",
                        ),
                    )
                    .with_session(&sid, gen),
                );
                return Ok(());
            }
        }

        if state.queue.len() >= self.inner.max_queue {
            return Err(ProtoError::new(
                CODE_QUEUE_FULL,
                true,
                format!(
                    "渲染队列已满（{} 条在排队），请稍后重试",
                    self.inner.max_queue
                ),
            ));
        }
        state.queue.push_back(job);
        self.inner.cv.notify_all();
        Ok(())
    }

    /// 取消一条请求。排队中→移除并回 `cancelled`；在飞→**杀 worker**
    /// （ADR 0003 §6：协议层没有协作中断，不许假装有）。
    pub fn cancel(&self, target: &str) -> &'static str {
        let mut state = self.inner.state.lock().unwrap();
        if let Some(index) = state
            .queue
            .iter()
            .position(|job| job.client_request_id == target)
        {
            let job = state.queue.remove(index).expect("position 刚给的下标");
            let out = self.inner.out.clone();
            let sid = self.id.clone();
            let gen = self.generation();
            drop(state);
            let _ = out.send(
                Response::err(
                    Some(job.client_request_id),
                    ProtoError::new(CODE_CANCELLED, false, "请求在排队时被取消"),
                )
                .with_session(&sid, gen),
            );
            return "queued_removed";
        }
        if state.in_flight.as_deref() == Some(target) {
            state.cancel_in_flight = true;
            drop(state);
            self.kill_worker();
            return "in_flight_killed";
        }
        "unknown"
    }

    /// 关会话。`force` = 当场杀进程（淘汰走这条，绝不等在飞的活跑完）。
    pub fn close(&self, client_request_id: Option<String>, force: bool, timeout: Duration) {
        {
            let mut state = self.inner.state.lock().unwrap();
            state.accepting = false;
            if state.close.is_none() {
                state.close = Some(CloseRequest {
                    client_request_id,
                    force,
                    timeout,
                });
            }
        }
        if force {
            // 先杀再叫醒：会话线程正卡在 await_response 里的话，EOF 会立刻把它放出来。
            self.kill_worker();
        }
        self.inner.cv.notify_all();
    }

    pub fn kill_worker(&self) {
        if let Some(proc) = self.inner.proc.lock().unwrap().as_ref() {
            proc.kill();
        }
    }

    pub fn describe(&self) -> Value {
        let state = self.inner.state.lock().unwrap();
        json!({
            "session_id": self.id,
            "spec_hash": self.spec_hash,
            "label": self.label,
            "generation": self.generation(),
            "queued": state.queue.len(),
            "in_flight": state.in_flight,
            "refs": *self.refs.lock().unwrap(),
        })
    }
}

// ------------------------------ 会话线程 ------------------------------

fn run(inner: Arc<Inner>, events_tx: Sender<WorkerEvent>, events_rx: Receiver<WorkerEvent>) {
    loop {
        let job = {
            let mut state = inner.state.lock().unwrap();
            loop {
                if state.close.is_some() {
                    break None;
                }
                match state.queue.pop_front() {
                    Some(job) => {
                        state.in_flight = Some(job.client_request_id.clone());
                        state.cancel_in_flight = false;
                        break Some(job);
                    }
                    None => state = inner.cv.wait(state).unwrap(),
                }
            }
        };
        let Some(job) = job else { break };

        let request_id = job.client_request_id.clone();
        let outcome = execute(&inner, &events_tx, &events_rx, job);
        {
            let mut state = inner.state.lock().unwrap();
            state.in_flight = None;
            state.last_used = Instant::now();
        }
        let response = match outcome {
            Ok(result) => Response::ok(&request_id, result),
            Err(error) => Response::err(Some(request_id), error),
        }
        .with_session(&inner.id, inner.generation.load(Ordering::SeqCst));
        let _ = inner.out.send(response);
    }
    finish(&inner);
}

/// 收尾：把队列里剩下的活一条条回 `session_dead`（**绝不静默丢**——每一条都有
/// 一个调用线程在等），再按 force / 优雅两种方式收掉子进程。
fn finish(inner: &Arc<Inner>) {
    let close = {
        let mut state = inner.state.lock().unwrap();
        state.accepting = false;
        let leftovers: Vec<Job> = state.queue.drain(..).collect();
        let close = state.close.take();
        drop(state);
        for job in leftovers {
            let _ = inner.out.send(
                Response::err(
                    Some(job.client_request_id),
                    ProtoError::new(CODE_SESSION_DEAD, true, "渲染会话已关闭，请重新打开"),
                )
                .with_session(&inner.id, inner.generation.load(Ordering::SeqCst)),
            );
        }
        close
    };

    let force = close.as_ref().map(|c| c.force).unwrap_or(true);
    let timeout = close
        .as_ref()
        .map(|c| c.timeout)
        .unwrap_or_else(|| Duration::from_secs(5));
    if !force {
        graceful_stop(inner, timeout);
    }
    if let Some(proc) = inner.proc.lock().unwrap().take() {
        proc.kill(); // Drop 也会杀一次，这里显式一遍是为了 kill 先于回响应
    }
    if let Some(rid) = close.and_then(|c| c.client_request_id) {
        let _ = inner.out.send(
            Response::ok(&rid, json!({"closed": true}))
                .with_session(&inner.id, inner.generation.load(Ordering::SeqCst)),
        );
    }
}

/// 优雅关停：发一条 v1 `shutdown`，worker 收到就 SystemExit。
/// **读到 EOF 即成功**（ADR 0003：shutdown 没有响应）。
fn graceful_stop(inner: &Arc<Inner>, timeout: Duration) {
    let generation = inner.generation.load(Ordering::SeqCst);
    let guard = inner.proc.lock().unwrap();
    let Some(proc) = guard.as_ref() else { return };
    let env = envelope(
        "shutdown",
        generation,
        0,
        &new_request_id(),
        None,
        &json!({}),
    );
    proc.send_line(env.0);
    drop(guard);
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let alive = inner
            .proc
            .lock()
            .unwrap()
            .as_ref()
            .map(|p| p.is_alive())
            .unwrap_or(false);
        if !alive {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn execute(
    inner: &Arc<Inner>,
    events_tx: &Sender<WorkerEvent>,
    events_rx: &Receiver<WorkerEvent>,
    job: Job,
) -> Result<Value, ProtoError> {
    ensure_worker(inner, events_tx, events_rx)?;
    match job.kind {
        // 握手已经在 ensure_worker 里做过了，这里只是把结果报回去
        JobKind::Open => Ok(json!({
            "session_id": inner.id,
            "spec_hash": inner.spec.hash(),
            "pid": inner.proc.lock().unwrap().as_ref().map(|p| p.pid).unwrap_or(0),
            "worker_protocol_version": WORKER_PROTOCOL_VERSION,
        })),
        JobKind::Worker { cmd, .. } => {
            let result = request(
                inner,
                events_rx,
                cmd,
                job.stem.as_deref(),
                &job.payload,
                job.timeout,
                job.idle,
            )?;
            if cmd == "render" {
                inner.revision.fetch_add(1, Ordering::SeqCst);
            }
            Ok(result)
        }
    }
}

/// 子进程没了就地重建（generation +1）并重新握手。
///
/// 重建是安全的：worker 的每条命令都会 `_ensure_built()`，override 又是全量列表
/// 语义，所以新起的进程只是多跑一次 build，语义与旧会话一致。
fn ensure_worker(
    inner: &Arc<Inner>,
    events_tx: &Sender<WorkerEvent>,
    events_rx: &Receiver<WorkerEvent>,
) -> Result<(), ProtoError> {
    {
        let guard = inner.proc.lock().unwrap();
        if let Some(proc) = guard.as_ref() {
            if proc.is_alive() {
                return Ok(());
            }
        }
    }
    // 旧进程的残余事件全部倒干净，免得新一代的等待被上一代的 EOF 打断
    while events_rx.try_recv().is_ok() {}

    let generation = inner.generation.fetch_add(1, Ordering::SeqCst) + 1;
    inner.revision.store(0, Ordering::SeqCst);
    let proc = WorkerProc::spawn(&inner.spec, generation, events_tx.clone()).map_err(|e| {
        ProtoError::new(
            CODE_SPAWN_FAILED,
            false,
            format!(
                "渲染进程启动失败: {e}（命令 {:?}）",
                inner.spec.argv.first().map(String::as_str).unwrap_or("")
            ),
        )
    })?;
    *inner.proc.lock().unwrap() = Some(proc);

    // 握手：v1 ping。回来了才算这条会话可用——起得来但不说 v1 的进程
    // （用户把解释器指到了别的东西上）必须在这里就被认出来。
    match request(
        inner,
        events_rx,
        "ping",
        None,
        &json!({}),
        inner.spec.handshake_timeout(),
        None, // 握手是一次 ping，没有用户脚本在跑，不用看门狗
    ) {
        Ok(_) => Ok(()),
        Err(mut err) => {
            if err.code == CODE_WORKER_TIMEOUT {
                err = ProtoError::new(
                    CODE_HANDSHAKE_TIMEOUT,
                    true,
                    format!(
                        "渲染进程在 {:.0} 秒内没有应答握手，已终止",
                        inner.spec.handshake_timeout().as_secs_f64()
                    ),
                );
            }
            if let Some(proc) = inner.proc.lock().unwrap().take() {
                proc.kill();
            }
            Err(err)
        }
    }
}

/// 发一条 worker 协议 v1 请求并等它的响应。
fn request(
    inner: &Arc<Inner>,
    events_rx: &Receiver<WorkerEvent>,
    cmd: &str,
    stem: Option<&str>,
    payload: &Value,
    timeout: Duration,
    idle: Option<Duration>,
) -> Result<Value, ProtoError> {
    let generation = inner.generation.load(Ordering::SeqCst);
    let revision = inner.revision.load(Ordering::SeqCst);
    let request_id = new_request_id();
    let (line, patch_hash) = envelope(cmd, generation, revision, &request_id, stem, payload);
    {
        let guard = inner.proc.lock().unwrap();
        let proc = guard.as_ref().ok_or_else(|| {
            ProtoError::new(CODE_SESSION_DEAD, true, "渲染进程已退出，会话需要重建")
        })?;
        // 写只是往写线程的 channel 里塞一条——**永不阻塞**。
        proc.send_line(line);
    }
    let response = await_response(inner, events_rx, generation, &request_id, timeout, idle)?;

    let mut result = serde_json::Map::new();
    if let Some(fields) = response.as_object() {
        for (key, value) in fields {
            // 信封字段是 supervisor 的账本，不往上层漏
            if matches!(
                key.as_str(),
                "ok" | "protocol_version" | "request_id" | "worker_generation" | "render_revision"
            ) {
                continue;
            }
            result.insert(key.clone(), value.clone());
        }
    }
    if let Some(hash) = patch_hash {
        result.insert("canonical_patch_hash".into(), json!(hash));
    }
    Ok(Value::Object(result))
}

/// 看门狗多久看一眼日志。与 Python 池的 `_PROGRESS_POLL` 同一个数。
const PROGRESS_POLL: Duration = Duration::from_secs(2);

/// `worker.log` 现在多大；读不到（还没建、被删）回 0。
///
/// **这就是「它还在动吗」的全部判据**，与 Python 池逐字相同（ADR 0050）：
/// worker 的 stderr 与用户脚本的 stdout 都落进这个文件，长大了就是还在跑。
fn log_size(inner: &Arc<Inner>) -> u64 {
    inner
        .spec
        .log_path
        .as_ref()
        .and_then(|p| std::fs::metadata(p).ok())
        .map(|m| m.len())
        .unwrap_or(0)
}

fn await_response(
    inner: &Arc<Inner>,
    events_rx: &Receiver<WorkerEvent>,
    generation: u64,
    request_id: &str,
    timeout: Duration,
    idle: Option<Duration>,
) -> Result<Value, ProtoError> {
    let started = Instant::now();
    let deadline = started + timeout;
    // 静默看门狗（ADR 0050）：只要日志还在长就把静默计时清零，`timeout` 退化
    // 成兜底上限。`idle` 为 None 时下面那段 `slice` 恒等于 `remaining`，
    // 行为与看门狗登场之前逐字节相同。
    let mut last_progress = started;
    let mut size = if idle.is_some() { log_size(inner) } else { 0 };
    loop {
        let now = Instant::now();
        let remaining = deadline.saturating_duration_since(now);
        if remaining.is_zero() {
            // 兜底上限到点。**判哪一句话不看「是谁到点了」，看静默了多久**——
            // 一直有输出的死循环正是从这里出去的，它绝不能说成「没有任何输出」。
            return Err(timeout_error(
                inner,
                now - started,
                now - last_progress,
                idle,
            ));
        }
        let slice = match idle {
            None => remaining,
            Some(limit) => {
                let silent_for = now - last_progress;
                if silent_for >= limit {
                    return Err(timeout_error(inner, now - started, silent_for, idle));
                }
                remaining.min(PROGRESS_POLL).min(limit - silent_for)
            }
        };
        match events_rx.recv_timeout(slice) {
            // 上一代的迟到响应：直接丢弃，绝不拿去覆盖新会话的状态
            Ok(WorkerEvent::Line(gen, _))
            | Ok(WorkerEvent::Garbage(gen, _))
            | Ok(WorkerEvent::Eof(gen))
                if gen != generation =>
            {
                continue
            }
            Ok(WorkerEvent::Line(_, value)) => {
                let echoed = value.get("request_id").and_then(Value::as_str);
                let version = value.get("protocol_version").and_then(Value::as_u64);
                if echoed != Some(request_id) || version != Some(WORKER_PROTOCOL_VERSION) {
                    // 管道是串行的，回显对不上意味着之后每一条响应都错位——
                    // A 图的 manifest 会落到 B 图上，而且不报错。杀掉重建是唯一安全处置。
                    kill_and_forget(inner);
                    let detail = if echoed == Some(request_id) {
                        format!("protocol_version={version:?}")
                    } else {
                        format!("request_id={echoed:?}，期待 {request_id:?}")
                    };
                    return Err(ProtoError::new(
                        CODE_PROTOCOL_MISMATCH,
                        true,
                        format!("渲染会话协议错乱（{detail}）。会话已重启，可以重试。"),
                    ));
                }
                if value.get("ok").and_then(Value::as_bool) == Some(true) {
                    return Ok(value);
                }
                return Err(worker_error(&value));
            }
            Ok(WorkerEvent::Garbage(_, line)) => {
                kill_and_forget(inner);
                return Err(ProtoError::new(
                    CODE_PROTOCOL_MISMATCH,
                    true,
                    "渲染进程往协议管道里写了非 JSON 的内容，会话已重启。",
                )
                .with_traceback(line.chars().take(400).collect::<String>()));
            }
            Ok(WorkerEvent::Eof(_)) => {
                let cancelled = {
                    let mut state = inner.state.lock().unwrap();
                    let flag = state.cancel_in_flight;
                    state.cancel_in_flight = false;
                    flag
                };
                // **管道 EOF 就是「这个 worker 没了」的判定，就地把进程摘掉。**
                //
                // 不摘的话，「worker 还在不在」这件事就有了**两个判据**：这里按
                // EOF 判，`ensure_worker` 却按 `try_wait()` 判。子进程关掉 stdout
                // 到被回收之间有一个窗口，`try_wait()` 在那期间回 `Ok(None)`
                // ——`ensure_worker` 于是认为「还活着」、**不重建**，把下一条请求
                // 写进一根死管道，等满整个超时才回 `worker_timeout`。
                //
                // 实测就是这么红的（CI 上约 7% 复现，main 上也红过一次）：
                // `a_crashed_worker_reports_session_dead_and_rebuilds` 的第一句
                // 断言（session_dead）过了，第二句（重建后 generation==2）拿到的
                // 是 10 秒后的 worker_timeout 且 generation 仍是 1。
                //
                // 这与 ADR 0004 里「『起来了』= hello 握过手，不是『进程对象还在』」
                // 是同一条纪律的另一半：**「没了」同样不能只看进程对象**。
                if let Some(proc) = inner.proc.lock().unwrap().take() {
                    proc.kill();
                }
                return Err(if cancelled {
                    ProtoError::new(CODE_CANCELLED, false, "请求已取消（渲染进程被终止）")
                } else {
                    ProtoError::new(
                        CODE_SESSION_DEAD,
                        true,
                        "渲染进程崩溃（无响应），会话需要重建",
                    )
                });
            }
            Err(RecvTimeoutError::Timeout) => {
                // 看门狗模式下这只是「这一片没等到回应」，不是判死：先看日志有没有
                // 长——长了就把静默计时清零，继续等。平坦模式下才是真的到点了。
                if idle.is_none() {
                    return Err(timeout_error(
                        inner,
                        started.elapsed(),
                        Duration::ZERO,
                        None,
                    ));
                }
                let grown = log_size(inner);
                if grown != size {
                    size = grown;
                    last_progress = Instant::now();
                }
                continue;
            }
            Err(RecvTimeoutError::Disconnected) => {
                // 读线程整个没了 —— 与 EOF 同一个判定，同样就地摘掉进程。
                if let Some(proc) = inner.proc.lock().unwrap().take() {
                    proc.kill();
                }
                return Err(ProtoError::new(
                    CODE_SESSION_DEAD,
                    true,
                    "渲染进程的读通道已断开，会话需要重建",
                ));
            }
        }
    }
}

/// 超时的用户可读形态。**三种判据，三句话**（ADR 0050）——用户的下一步完全
/// 不同，合成一句等于把两个方向的排查指到同一处：
///
/// * `idle` 为 `None` = 热态操作的平坦上限，说的是「等了多久」；
/// * `silent_for >= idle` = 静默看门狗判的死，说的是「多久没有任何输出」，
///   下一步是展开输出看它停在哪；
/// * 否则 = 兜底上限收掉了一个**一直有输出**的循环，下一步是看它在重复什么。
///   这一条从前被并进了第二条，于是 4 小时的打印死循环会被报成「连着 240
///   分钟没有任何输出」——判据的主语从 `silent_for` 滑到了 `waited`。
///
/// **判据与文案都与 Python 池同源**（`EngineWorker._timeout_error`，
/// `pool.py` 里是 `silent_for >= BUILD_IDLE_TIMEOUT`）：阈值只有下发的这一个，
/// 这边不许再有第二套数。
fn timeout_error(
    inner: &Arc<Inner>,
    waited: Duration,
    silent_for: Duration,
    idle: Option<Duration>,
) -> ProtoError {
    let wrote = inner
        .proc
        .lock()
        .unwrap()
        .as_ref()
        .map(|p| p.wrote_last())
        .unwrap_or(true);
    kill_and_forget(inner);
    if let Some(limit) = idle {
        let message = if silent_for >= limit {
            format!(
                "脚本连着 {} 分钟没有任何输出，当作卡住处理，渲染会话已重启。\
                 展开输出看它最后停在哪一步；如果它本来就要静默算很久，\
                 在那一段里打一行进度输出，Tavotto 就会一直等下去。",
                silent_for.as_secs() / 60
            )
        } else {
            format!(
                "脚本已经跑了 {} 小时还没结束，到了上限，渲染会话已重启。\
                 它一直有输出，所以不是卡死——多半是循环停不下来。\
                 展开输出看它在重复什么。",
                waited.as_secs() / 3600
            )
        };
        return ProtoError::new(CODE_WORKER_TIMEOUT, true, message);
    }
    let hint = if wrote {
        "脚本可能陷入死循环，或这一步本身极慢"
    } else {
        // 连请求都没写进管道 = 对面根本没在读 stdin
        "请求还没写进渲染进程的管道，它很可能已经不在读 stdin 了"
    };
    ProtoError::new(
        CODE_WORKER_TIMEOUT,
        true,
        format!(
            "渲染超时（等了 {} 秒）。{hint}；渲染会话已重启，可以重试。",
            waited.as_secs()
        ),
    )
}

/// 杀掉并丢弃当前子进程——下一条请求会以 generation+1 原地重建。
fn kill_and_forget(inner: &Arc<Inner>) {
    if let Some(proc) = inner.proc.lock().unwrap().take() {
        proc.kill();
    }
}

/// worker 的错误信封 → supervisor 错误。**code / retryable / traceback 原样透传**，
/// Flask 侧对 `missing_dependency` 的识别（按 traceback 正则）因此一字不用改。
fn worker_error(value: &Value) -> ProtoError {
    let err = value.get("error");
    let (code, retryable, message, traceback) = match err.and_then(Value::as_object) {
        Some(obj) => (
            obj.get("code")
                .and_then(Value::as_str)
                .unwrap_or("internal")
                .to_string(),
            obj.get("retryable")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            obj.get("message")
                .and_then(Value::as_str)
                .unwrap_or("worker 错误")
                .to_string(),
            obj.get("traceback")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        ),
        // legacy 的扁平错误形状（`{"ok":false,"error":"..."}`）也兜住
        None => (
            "internal".to_string(),
            false,
            err.and_then(Value::as_str)
                .unwrap_or("worker 错误")
                .to_string(),
            value
                .get("traceback")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        ),
    };
    let mut out = ProtoError::new(&code, retryable, message).with_traceback(traceback);
    if let Some(obj) = err.and_then(Value::as_object) {
        let extra: serde_json::Map<String, Value> = obj
            .iter()
            .filter(|(k, _)| !matches!(k.as_str(), "code" | "retryable" | "message" | "traceback"))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        if !extra.is_empty() {
            out.extra = Value::Object(extra);
        }
    }
    out
}

/// 装 worker 协议 v1 的信封；回 (整行文本, canonical_patch_hash)。
///
/// **发给 worker 的 patches 永远是原始列表**——规范化只用来算身份哈希，
/// 拿规范序去应用会静静改掉渲染结果（几何优先级是 `overrides.apply` 的事）。
fn envelope(
    cmd: &str,
    generation: u64,
    revision: u64,
    request_id: &str,
    stem: Option<&str>,
    payload: &Value,
) -> (String, Option<String>) {
    let mut env = serde_json::Map::new();
    env.insert("protocol_version".into(), json!(WORKER_PROTOCOL_VERSION));
    env.insert("request_id".into(), json!(request_id));
    env.insert("worker_generation".into(), json!(generation));
    env.insert("render_revision".into(), json!(revision));
    env.insert("cmd".into(), json!(cmd));
    if let Some(stem) = stem {
        env.insert("stem".into(), json!(stem));
    }
    let mut hash = None;
    if let Some(patches) = payload.get("patches") {
        let digest = patchspec::patch_hash(patches);
        env.insert("canonical_patch_hash".into(), json!(digest));
        hash = Some(digest);
    }
    env.insert("payload".into(), payload.clone());
    let line = serde_json::to_string(&Value::Object(env)).unwrap_or_default();
    (line, hash)
}

static REQUEST_SEQ: AtomicU64 = AtomicU64::new(0);

/// 进程内唯一的请求号。够用即可——它只需要在**一条会话的管道上**唯一。
pub fn new_request_id() -> String {
    let seq = REQUEST_SEQ.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("r-{nanos:x}-{seq:x}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::channel;

    /// 一个不带子进程的 Inner——`await_response` 只需要 state（取消标记）与
    /// proc（超时/错乱时 kill），两者都允许为空。
    /// 回 (Inner, 响应通道的接收端)——接收端必须被调用方持住，丢掉它
    /// `out.send` 就会失败，本组用例的断言会莫名其妙地变成「什么都没发生」。
    fn test_inner(generation: u64) -> (Arc<Inner>, Receiver<Response>) {
        test_inner_with_log(generation, None)
    }

    fn test_inner_with_log(
        generation: u64,
        log_path: Option<String>,
    ) -> (Arc<Inner>, Receiver<Response>) {
        let (out, rx) = channel();
        let spec = SpawnSpec {
            log_path,
            ..SpawnSpec::default()
        };
        let inner = Arc::new(Inner {
            id: "s-test".into(),
            spec,
            out,
            state: Mutex::new(State {
                queue: VecDeque::new(),
                in_flight: None,
                cancel_in_flight: false,
                close: None,
                accepting: true,
                last_used: Instant::now(),
            }),
            cv: Condvar::new(),
            max_queue: 8,
            generation: AtomicU64::new(generation),
            revision: AtomicU64::new(0),
            proc: Mutex::new(None),
        });
        (inner, rx)
    }

    fn ok_line(request_id: &str) -> Value {
        json!({"ok": true, "protocol_version": 1, "request_id": request_id,
               "manifest": {"elements": []}})
    }

    /// 静默看门狗（ADR 0050）：**日志还在长就一直等**，别把慢当成卡死。
    ///
    /// 这条与 Python 池的 `test_output_resets_the_silence_clock` 是同一件事的
    /// 两侧实现——判据分叉的话，换个控制面同一个脚本一个能跑一个被杀。
    #[test]
    fn output_resets_the_silence_clock() {
        let log = std::env::temp_dir().join(format!("tavotto-wd-{}.log", std::process::id()));
        std::fs::write(&log, b"").unwrap();
        let (inner, _out) = test_inner_with_log(1, Some(log.to_string_lossy().into_owned()));
        let (tx, rx) = channel();

        // 静默阈值 120ms，但每 20ms 就有新输出；400ms 后才回应。
        let writer_log = log.clone();
        let stop = Arc::new(AtomicU64::new(0));
        let stop_writer = Arc::clone(&stop);
        std::thread::spawn(move || {
            while stop_writer.load(Ordering::SeqCst) == 0 {
                use std::io::Write;
                let mut f = std::fs::OpenOptions::new()
                    .append(true)
                    .open(&writer_log)
                    .unwrap();
                let _ = f.write_all(b"[INFO] still working\n");
                std::thread::sleep(Duration::from_millis(20));
            }
        });
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(400));
            let _ = tx.send(WorkerEvent::Line(1, ok_line("r-1")));
        });

        let got = await_response(
            &inner,
            &rx,
            1,
            "r-1",
            Duration::from_secs(30),
            Some(Duration::from_millis(120)),
        );
        stop.store(1, Ordering::SeqCst);
        let _ = std::fs::remove_file(&log);
        assert!(got.is_ok(), "一直有输出时不该判死: {got:?}");
    }

    /// 一个字节都不多出来 → 到点判死，而且说的是「多久没有任何输出」。
    #[test]
    fn silence_ends_it_and_says_so() {
        let log =
            std::env::temp_dir().join(format!("tavotto-wd-silent-{}.log", std::process::id()));
        std::fs::write(&log, b"").unwrap();
        let (inner, _out) = test_inner_with_log(1, Some(log.to_string_lossy().into_owned()));
        let (_tx, rx) = channel::<WorkerEvent>();
        let err = await_response(
            &inner,
            &rx,
            1,
            "r-1",
            Duration::from_secs(30),
            Some(Duration::from_millis(80)),
        )
        .unwrap_err();
        let _ = std::fs::remove_file(&log);
        assert_eq!(err.code, CODE_WORKER_TIMEOUT);
        assert!(
            err.message.contains("没有任何输出"),
            "看门狗判死要说判据本身: {}",
            err.message
        );
    }

    /// 兜底上限收掉的是一个**一直有输出**的循环——那句话不许说成「没有任何输出」。
    ///
    /// 这条以前没有：硬上限那个出口把 `waited` 当成 `silent_for` 传进了同一句
    /// 文案，于是 4 小时的打印死循环会被报成「连着 240 分钟没有任何输出」。
    /// 判据的主语从「静默了多久」滑到了「等了多久」，而上面两条用例都只走
    /// 静默分支，谁也看不见它。
    #[test]
    fn the_hard_ceiling_says_the_script_kept_printing() {
        let log = std::env::temp_dir().join(format!("tavotto-wd-loop-{}.log", std::process::id()));
        std::fs::write(&log, b"").unwrap();
        let (inner, _out) = test_inner_with_log(1, Some(log.to_string_lossy().into_owned()));
        let (_tx, rx) = channel::<WorkerEvent>();

        // 每 20ms 打一行 → 静默计时一直被清零，先到点的必然是 240ms 的兜底上限。
        let writer_log = log.clone();
        let stop = Arc::new(AtomicU64::new(0));
        let stop_writer = Arc::clone(&stop);
        std::thread::spawn(move || {
            while stop_writer.load(Ordering::SeqCst) == 0 {
                use std::io::Write;
                let mut f = std::fs::OpenOptions::new()
                    .append(true)
                    .open(&writer_log)
                    .unwrap();
                let _ = f.write_all(b"[INFO] round\n");
                std::thread::sleep(Duration::from_millis(20));
            }
        });

        let err = await_response(
            &inner,
            &rx,
            1,
            "r-1",
            Duration::from_millis(240),
            Some(Duration::from_millis(150)),
        )
        .unwrap_err();
        stop.store(1, Ordering::SeqCst);
        let _ = std::fs::remove_file(&log);
        assert_eq!(err.code, CODE_WORKER_TIMEOUT);
        assert!(
            err.message.contains("一直有输出"),
            "兜底上限判死要说「它一直有输出」: {}",
            err.message
        );
        assert!(
            !err.message.contains("没有任何输出"),
            "兜底上限判死不许说成静默: {}",
            err.message
        );
    }

    /// **数据损坏级**：会话被超时 kill 后原地重建，上一代的迟到响应必须被认出来
    /// 丢弃。不丢的话新会话会被旧 manifest 污染，而且一声不吭。
    #[test]
    fn late_responses_from_a_previous_generation_are_dropped() {
        let (inner, _out) = test_inner(2);
        let (tx, rx) = channel();
        // 上一代的三种残余：回显对不上的一行、一行垃圾、EOF。放在当代下面
        // 任何一条都会把这次等待搞成 protocol_mismatch / session_dead。
        tx.send(WorkerEvent::Line(1, ok_line("r-上一代的")))
            .unwrap();
        tx.send(WorkerEvent::Garbage(1, "上一代的垃圾".into()))
            .unwrap();
        tx.send(WorkerEvent::Eof(1)).unwrap();
        tx.send(WorkerEvent::Line(2, ok_line("r-当代"))).unwrap();

        let got = await_response(&inner, &rx, 2, "r-当代", Duration::from_secs(2), None)
            .expect("当代的响应必须被采用");
        assert_eq!(got["manifest"]["elements"], json!([]));
    }

    /// 同一代里回显对不上 = 会话真的错位了，必须报出来（并杀掉重建）。
    #[test]
    fn a_mismatched_echo_in_the_current_generation_is_a_protocol_mismatch() {
        let (inner, _out) = test_inner(2);
        let (tx, rx) = channel();
        tx.send(WorkerEvent::Line(2, ok_line("r-别人的"))).unwrap();
        let err =
            await_response(&inner, &rx, 2, "r-当代", Duration::from_secs(2), None).unwrap_err();
        assert_eq!(err.code, CODE_PROTOCOL_MISMATCH);
        assert!(err.message.contains("重试"));
    }

    #[test]
    fn a_foreign_worker_protocol_version_is_a_protocol_mismatch() {
        let (inner, _out) = test_inner(1);
        let (tx, rx) = channel();
        tx.send(WorkerEvent::Line(
            1,
            json!({"ok": true, "protocol_version": 2, "request_id": "r-1"}),
        ))
        .unwrap();
        let err = await_response(&inner, &rx, 1, "r-1", Duration::from_secs(2), None).unwrap_err();
        assert_eq!(err.code, CODE_PROTOCOL_MISMATCH);
    }

    #[test]
    fn eof_is_session_dead_unless_the_request_was_cancelled() {
        let (inner, _out) = test_inner(1);
        let (tx, rx) = channel();
        tx.send(WorkerEvent::Eof(1)).unwrap();
        let err = await_response(&inner, &rx, 1, "r-1", Duration::from_secs(2), None).unwrap_err();
        assert_eq!(err.code, CODE_SESSION_DEAD);

        inner.state.lock().unwrap().cancel_in_flight = true;
        let (tx2, rx2) = channel();
        tx2.send(WorkerEvent::Eof(1)).unwrap();
        let err = await_response(&inner, &rx2, 1, "r-1", Duration::from_secs(2), None).unwrap_err();
        assert_eq!(err.code, CODE_CANCELLED);
        // 标记是一次性的，不许粘在会话上让下一条请求也报「取消」
        assert!(!inner.state.lock().unwrap().cancel_in_flight);
    }

    #[test]
    fn a_silent_worker_times_out_instead_of_waiting_forever() {
        let (inner, _out) = test_inner(1);
        let (_tx, rx) = channel::<WorkerEvent>();
        let err =
            await_response(&inner, &rx, 1, "r-1", Duration::from_millis(120), None).unwrap_err();
        assert_eq!(err.code, CODE_WORKER_TIMEOUT);
    }

    #[test]
    fn the_worker_envelope_matches_adr_0003() {
        let payload =
            json!({"patches": [{"gid": "g", "prop": "text", "value": "x"}], "width": 400});
        let (line, hash) = envelope("render", 3, 17, "r-abc", Some("Fig1"), &payload);
        let env: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(env["protocol_version"], 1);
        assert_eq!(env["request_id"], "r-abc");
        assert_eq!(env["worker_generation"], 3);
        assert_eq!(env["render_revision"], 17);
        assert_eq!(env["cmd"], "render");
        assert_eq!(env["stem"], "Fig1"); // stem 走顶层，不在 payload 里
        assert_eq!(env["payload"], payload);
        assert_eq!(env["canonical_patch_hash"], json!(hash.unwrap()));

        // 不带 patches 的命令不算 hash
        let (line, hash) = envelope("build", 1, 0, "r-b", None, &json!({}));
        assert!(hash.is_none());
        let env: Value = serde_json::from_str(&line).unwrap();
        assert!(env.get("canonical_patch_hash").is_none());
        assert!(env.get("stem").is_none());
    }

    #[test]
    fn worker_errors_pass_through_code_retryable_and_traceback() {
        let value = json!({"ok": false, "protocol_version": 1, "request_id": "r-1",
                           "error": {"code": "unknown_stem", "retryable": false,
                                     "message": "stem 不存在: nope", "traceback": "tb",
                                     "known": ["Fig1"]}});
        let err = worker_error(&value);
        assert_eq!(err.code, "unknown_stem");
        assert!(!err.retryable);
        assert_eq!(err.traceback, "tb");
        assert_eq!(err.extra["known"][0], "Fig1");
        // missing_dependency 的识别仍在 Python 侧（按 traceback 正则），
        // 这里只负责一个字节都不改地把 traceback 交出去
        assert_eq!(err.to_json()["traceback"], "tb");
    }
}
