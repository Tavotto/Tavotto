//! 会话表与操作分派——workerd 的「控制面的机制层」。
//!
//! 这里**不做任何策略判断**：解释器怎么挑、env 怎么给、超时给多少、最多留几个
//! 会话，全部由 Flask 在请求里带过来（`pool._prioritized_candidates()` 之类仍是
//! Python 的唯一权威）。把那些搬到 Rust 里就等于制造第二个权威，两边迟早分叉。

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::Sender;
use std::sync::Arc;
use std::time::Duration;

use serde_json::json;

use crate::protocol::*;
use crate::session::{Job, JobKind, Session};
use crate::worker::SpawnSpec;

/// 同时存活的会话数上限的兜底（Flask 在 hello 里给真值，对齐 `pool.MAX_ALIVE`）。
const DEFAULT_MAX_SESSIONS: usize = 3;
/// 单会话队列上限的兜底。有界是刻意的——排队无上限时一次卡顿会攒出几百条
/// 早就没人要的渲染，之后逐条跑完，用户看着界面「越用越慢」。
const DEFAULT_MAX_QUEUE: usize = 32;
/// 请求没带 timeout_ms 时的兜底档（正常路径上 Flask 一定带）。
const DEFAULT_TIMEOUT_MS: u64 = 300_000;
/// 优雅关停等 EOF 的兜底。
const DEFAULT_CLOSE_TIMEOUT_MS: u64 = 5_000;

/// op → worker 协议 v1 的命令名，以及这条 op 是否可被同 stem 的新请求顶掉。
///
/// **只有 render 合并**：export / 写回类请求各自有独立产物，合并等于悄悄少写
/// 一个文件；preview_png 按 tag 分文件，顶掉会让某个直方图永远不出现。
fn worker_cmd(op: &str) -> Option<(&'static str, bool)> {
    match op {
        "build" => Some(("build", false)),
        "render" => Some(("render", true)),
        "render_png" => Some(("render_png", false)),
        "preview_png" => Some(("preview_png", false)),
        "export" => Some(("export", false)),
        _ => None,
    }
}

pub struct Supervisor {
    sessions: BTreeMap<String, Arc<Session>>,
    by_hash: BTreeMap<String, String>,
    out: Sender<Response>,
    seq: AtomicU64,
    max_sessions: usize,
    max_queue: usize,
    /// 收到 `shutdown` 后置位，主循环据此退出。
    pub stopping: bool,
}

impl Supervisor {
    pub fn new(out: Sender<Response>) -> Supervisor {
        Supervisor {
            sessions: BTreeMap::new(),
            by_hash: BTreeMap::new(),
            out,
            seq: AtomicU64::new(0),
            max_sessions: DEFAULT_MAX_SESSIONS,
            max_queue: DEFAULT_MAX_QUEUE,
            stopping: false,
        }
    }

    /// 分派一条请求。回 `Some(resp)` 表示当场就有结论；回 `None` 表示已经排进
    /// 某条会话的队列，响应稍后由会话线程从同一个通道发出。
    pub fn dispatch(&mut self, req: Request) -> Option<Response> {
        match req.op.as_str() {
            "hello" => Some(self.hello(&req)),
            "ping" if req.session_id.is_none() => Some(Response::ok(
                &req.request_id,
                json!({"pong": true, "sessions": self.sessions.len()}),
            )),
            "sessions" => Some(Response::ok(
                &req.request_id,
                json!({"sessions": self.sessions.values().map(|s| s.describe()).collect::<Vec<_>>()}),
            )),
            "open_session" => self.open_session(&req),
            "close_session" => self.close_session(&req),
            "cancel" => Some(self.cancel(&req)),
            "shutdown" => Some(self.shutdown(&req)),
            "ping" => self.enqueue_worker(&req, "ping", false),
            other => match worker_cmd(other) {
                Some((cmd, mergeable)) => self.enqueue_worker(&req, cmd, mergeable),
                None => Some(Response::err(
                    Some(req.request_id.clone()),
                    ProtoError::new(CODE_UNKNOWN_OP, false, format!("未知操作: {other}")),
                )),
            },
        }
    }

    fn hello(&mut self, req: &Request) -> Response {
        if let Some(n) = req.payload_u64("max_sessions") {
            self.max_sessions = (n as usize).max(1);
        }
        if let Some(n) = req.payload_u64("max_queue") {
            self.max_queue = (n as usize).max(1);
        }
        Response::ok(
            &req.request_id,
            json!({
                "workerd_version": env!("CARGO_PKG_VERSION"),
                "worker_protocol_version": WORKER_PROTOCOL_VERSION,
                "pid": std::process::id(),
                "max_sessions": self.max_sessions,
                "max_queue": self.max_queue,
                "capabilities": [
                    "queue_merge_latest",   // per (会话, stem) 的 render 只留最新一条
                    "cancel_kill",          // 在飞的取消 = 杀进程（没有协作中断）
                    "generation_fencing",   // 上一代的迟到响应一律丢弃
                    "patch_hash",           // canonical_patch_hash 由 workerd 算
                    "lru_evict",            // 超出上限按最久未用淘汰（kill，不等锁）
                ],
            }),
        )
    }

    /// 回 `None` 表示会话线程已经接手——spawn + 握手可能要几十秒，
    /// 占住主循环的话别的会话连一条 ping 都发不出去。
    fn open_session(&mut self, req: &Request) -> Option<Response> {
        let spec = match SpawnSpec::from_payload(&req.payload) {
            Ok(spec) => spec,
            Err(message) => {
                return Some(Response::err(
                    Some(req.request_id.clone()),
                    ProtoError::new(CODE_BAD_REQUEST, false, message),
                ))
            }
        };
        let hash = spec.hash();

        // 同一份 spawn 规格已经有活着的会话就复用（引用 +1）。
        // 「旧 EngineWorker 正在异步关停 / 新的已经建好」这个交叠窗口很常见，
        // 不复用的话会多起一个端着整套 Figure 内存的进程。
        if let Some(existing) = self.by_hash.get(&hash).and_then(|id| self.sessions.get(id)) {
            let existing = Arc::clone(existing);
            existing.retain();
            return Some(
                Response::ok(
                    &req.request_id,
                    json!({
                        "session_id": existing.id,
                        "spec_hash": hash,
                        "reused": true,
                        "worker_protocol_version": WORKER_PROTOCOL_VERSION,
                    }),
                )
                .with_session(&existing.id, existing.generation()),
            );
        }

        let id = format!("s-{}", self.seq.fetch_add(1, Ordering::Relaxed) + 1);
        let session = match Session::start(
            id.clone(),
            spec,
            self.out.clone(),
            self.max_queue,
            req.request_id.clone(),
        ) {
            Ok(session) => session,
            Err(e) => {
                return Some(Response::err(
                    Some(req.request_id.clone()),
                    ProtoError::new(CODE_SPAWN_FAILED, false, format!("会话线程起不来: {e}")),
                ))
            }
        };
        self.by_hash.insert(hash, id.clone());
        self.sessions.insert(id.clone(), session);
        self.evict_if_needed(&id);
        None // 响应（含 spawn/握手的成败）由会话线程发
    }

    /// 超出上限就淘汰最久未用的一条。**淘汰 = kill**：正在渲染也照杀，
    /// 绝不等它把手上的活跑完（等锁正是 Python 池里那条把界面拖住的路径）。
    fn evict_if_needed(&mut self, protect: &str) {
        while self.sessions.len() > self.max_sessions {
            let victim = self
                .sessions
                .values()
                .filter(|s| s.id != protect)
                .min_by_key(|s| s.last_used())
                .map(|s| s.id.clone());
            let Some(victim) = victim else { break };
            if let Some(session) = self.sessions.remove(&victim) {
                self.by_hash.retain(|_, id| id != &victim);
                session.close(None, true, Duration::from_millis(0));
            }
        }
    }

    fn close_session(&mut self, req: &Request) -> Option<Response> {
        let Some(sid) = req.session_id.clone() else {
            return Some(Response::err(
                Some(req.request_id.clone()),
                ProtoError::new(CODE_BAD_REQUEST, false, "close_session 需要 session_id"),
            ));
        };
        let Some(session) = self.sessions.get(&sid).cloned() else {
            // 已经关掉的会话再关一次是幂等成功——Flask 那边 invalidate 与
            // shutdown_all 完全可能对同一条会话各发一次。
            return Some(Response::ok(
                &req.request_id,
                json!({"closed": false, "known": false}),
            ));
        };
        let force = req.payload_bool("force");
        if !force && !session.release() {
            // 还有别的句柄在用同一个 worker，这次 close 只是放掉一个引用
            return Some(Response::ok(
                &req.request_id,
                json!({"closed": false, "released": true}),
            ));
        }
        self.sessions.remove(&sid);
        self.by_hash.retain(|_, id| id != &sid);
        let timeout = Duration::from_millis(if req.timeout_ms > 0 {
            req.timeout_ms
        } else {
            DEFAULT_CLOSE_TIMEOUT_MS
        });
        session.close(Some(req.request_id.clone()), force, timeout);
        None
    }

    fn cancel(&mut self, req: &Request) -> Response {
        let Some(target) = req.payload_str("target_request_id").map(str::to_string) else {
            return Response::err(
                Some(req.request_id.clone()),
                ProtoError::new(
                    CODE_BAD_REQUEST,
                    false,
                    "cancel 需要 payload.target_request_id",
                ),
            );
        };
        let candidates: Vec<Arc<Session>> = match &req.session_id {
            Some(sid) => self.sessions.get(sid).cloned().into_iter().collect(),
            None => self.sessions.values().cloned().collect(),
        };
        let mut outcome = "unknown";
        for session in candidates {
            let result = session.cancel(&target);
            if result != "unknown" {
                outcome = result;
                break;
            }
        }
        // **永远回 ok**：取消是幂等的尽力而为（对齐 ADR 0003 §6 的诚实边界）。
        // 找不到目标通常只是它刚好跑完了，那不是错误。
        Response::ok(
            &req.request_id,
            json!({"outcome": outcome, "target_request_id": target}),
        )
    }

    fn shutdown(&mut self, req: &Request) -> Response {
        let sessions: Vec<Arc<Session>> = self.sessions.values().cloned().collect();
        let count = sessions.len();
        self.sessions.clear();
        self.by_hash.clear();
        for session in sessions {
            // 退出路径一律硬杀：优雅关停要等在飞的活跑完，而死循环脚本正是
            // 「等不到」的那一类，等下去就会留下孤儿渲染进程。
            session.close(None, true, Duration::from_millis(0));
        }
        self.stopping = true;
        Response::ok(&req.request_id, json!({"closed_sessions": count}))
    }

    fn enqueue_worker(
        &mut self,
        req: &Request,
        cmd: &'static str,
        mergeable: bool,
    ) -> Option<Response> {
        let Some(sid) = req.session_id.clone() else {
            return Some(Response::err(
                Some(req.request_id.clone()),
                ProtoError::new(
                    CODE_BAD_REQUEST,
                    false,
                    format!("{} 需要 session_id", req.op),
                ),
            ));
        };
        let Some(session) = self.sessions.get(&sid).cloned() else {
            return Some(Response::err(
                Some(req.request_id.clone()),
                ProtoError::new(
                    CODE_UNKNOWN_SESSION,
                    true,
                    format!("会话 {sid} 不存在（workerd 可能重启过），请重新打开"),
                ),
            ));
        };
        let merge_key = if mergeable {
            req.stem.clone().or_else(|| Some(String::new()))
        } else {
            None
        };
        let job = Job {
            client_request_id: req.request_id.clone(),
            kind: JobKind::Worker { cmd, merge_key },
            stem: req.stem.clone(),
            payload: req.payload.clone(),
            timeout: Duration::from_millis(if req.timeout_ms > 0 {
                req.timeout_ms
            } else {
                DEFAULT_TIMEOUT_MS
            }),
        };
        match session.enqueue(job) {
            Ok(()) => None,
            Err(error) => Some(
                Response::err(Some(req.request_id.clone()), error)
                    .with_session(&sid, session.generation()),
            ),
        }
    }

    /// 进程退出前把所有会话收掉（主循环读到 EOF 时走这条）。
    pub fn close_all(&mut self) {
        for session in std::mem::take(&mut self.sessions).into_values() {
            session.close(None, true, Duration::from_millis(0));
        }
        self.by_hash.clear();
    }
}
