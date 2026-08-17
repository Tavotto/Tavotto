//! supervisor 协议（Flask ↔ magplot-workerd）——**与 worker 协议是两套东西**。
//!
//! worker 协议 v1（ADR 0003）是 workerd ↔ worker 子进程之间那条；这里这套是
//! Flask ↔ workerd。分开是因为两条管道的性质完全不同：worker 那条严格串行、
//! 一次一请求；这条要被 Flask 的多个线程共用，靠 `request_id` 多路复用。
//!
//! 完整契约见 `docs/adr/0004-workerd-supervisor.md`。

use serde_json::{json, Value};

/// 本协议版本。加字段不升版本，改语义 / 删字段才升。
pub const SUPERVISOR_PROTOCOL_VERSION: u64 = 1;

/// workerd 说的 worker 协议版本（ADR 0003）。
pub const WORKER_PROTOCOL_VERSION: u64 = 1;

// ---- 错误码 ---------------------------------------------------------------
// worker 自己那五个（bad_request / unknown_cmd / unknown_stem / script_error /
// internal）**原样透传**，Flask 侧的处置一字不改。下面是 supervisor 层新增的。

/// 队列里被更新的同 (会话, stem) render 顶掉了。重试无意义——新的那条覆盖了它。
pub const CODE_QUEUE_SUPERSEDED: &str = "queue_superseded";
/// 被 `cancel` 显式取消（排队中移除，或在飞时连 worker 一起杀）。
pub const CODE_CANCELLED: &str = "cancelled";
/// 会话已死：worker 进程退出 / 被 LRU 淘汰 / 会话被关掉。
pub const CODE_SESSION_DEAD: &str = "session_dead";
/// 子进程根本没起来（可执行文件不存在、权限不足、cwd 不在）。
pub const CODE_SPAWN_FAILED: &str = "spawn_failed";
/// 起来了但握手（v1 ping）没在期限内回来。
pub const CODE_HANDSHAKE_TIMEOUT: &str = "handshake_timeout";
/// 单次请求超时；worker 已被强杀，下一次请求原地重建。
pub const CODE_WORKER_TIMEOUT: &str = "worker_timeout";
/// 回显对不上或 protocol_version 不符——会话已错位，杀掉不复用。
pub const CODE_PROTOCOL_MISMATCH: &str = "protocol_mismatch";
/// 有界队列满了，**立即拒绝**而不是把调用方挂住。
pub const CODE_QUEUE_FULL: &str = "queue_full";
/// session_id 不存在（workerd 重启过 / 已经关掉了）。
pub const CODE_UNKNOWN_SESSION: &str = "unknown_session";
/// op 不在操作表里。
pub const CODE_UNKNOWN_OP: &str = "unknown_op";
/// supervisor 层的请求形状错误（信封缺字段、类型不对）。
pub const CODE_BAD_REQUEST: &str = "bad_request";

/// 结构化错误。字段与 worker 协议的 `error` 对象同构，方便 Flask 一套代码处理。
#[derive(Debug, Clone)]
pub struct ProtoError {
    pub code: String,
    pub retryable: bool,
    pub message: String,
    pub traceback: String,
    /// worker 错误信封里多带的字段（`known` 之类），原样转交。
    pub extra: Value,
}

impl ProtoError {
    pub fn new(code: &str, retryable: bool, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            retryable,
            message: message.into(),
            traceback: String::new(),
            extra: Value::Null,
        }
    }

    pub fn with_traceback(mut self, traceback: impl Into<String>) -> Self {
        self.traceback = traceback.into();
        self
    }

    pub fn to_json(&self) -> Value {
        let mut out = json!({
            "code": self.code,
            "retryable": self.retryable,
            "message": self.message,
            "traceback": self.traceback,
        });
        if let (Some(map), Some(extra)) = (out.as_object_mut(), self.extra.as_object()) {
            for (key, value) in extra {
                // 不许覆盖上面四个正字段——worker 多带的东西只能是补充
                map.entry(key.clone()).or_insert_with(|| value.clone());
            }
        }
        out
    }
}

/// Flask 发来的一条请求。
#[derive(Debug, Clone)]
pub struct Request {
    pub request_id: String,
    pub op: String,
    pub session_id: Option<String>,
    pub stem: Option<String>,
    pub payload: Value,
    /// 超时**由调用方携带**：档位（BUILD / REQUEST / EXPORT / SHUTDOWN）是
    /// Flask 的策略，workerd 只负责执行。0 或缺省表示用默认档。
    pub timeout_ms: u64,
}

impl Request {
    /// 解析一行；形状不对时回一条能对回 request_id 的错误（对不回就 null）。
    ///
    /// 错误装箱是为了让 `Result` 的两个变体不至于差出一个 `ProtoError` 那么大
    /// （clippy 的 `result_large_err`）——成功路径才是热路径。
    pub fn parse(line: &str) -> Result<Request, Box<(Option<String>, ProtoError)>> {
        let value: Value = serde_json::from_str(line).map_err(|e| {
            Box::new((
                None,
                ProtoError::new(CODE_BAD_REQUEST, false, format!("JSON 解析失败: {e}")),
            ))
        })?;
        let obj = value.as_object().ok_or_else(|| {
            Box::new((
                None,
                ProtoError::new(CODE_BAD_REQUEST, false, "请求必须是 JSON 对象"),
            ))
        })?;

        let request_id = obj
            .get("request_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let rid = if request_id.is_empty() {
            None
        } else {
            Some(request_id.clone())
        };

        let version = obj
            .get("supervisor_protocol_version")
            .and_then(Value::as_u64);
        if version != Some(SUPERVISOR_PROTOCOL_VERSION) {
            return Err(Box::new((
                rid,
                ProtoError::new(
                    CODE_BAD_REQUEST,
                    false,
                    format!(
                        "不支持的 supervisor_protocol_version: {:?}（本 workerd 说 v{}）",
                        version, SUPERVISOR_PROTOCOL_VERSION
                    ),
                ),
            )));
        }
        if request_id.is_empty() {
            return Err(Box::new((
                None,
                ProtoError::new(CODE_BAD_REQUEST, false, "request_id 必须是非空字符串"),
            )));
        }
        let op = obj
            .get("op")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if op.is_empty() {
            return Err(Box::new((
                rid,
                ProtoError::new(CODE_BAD_REQUEST, false, "op 必须是非空字符串"),
            )));
        }
        let payload = obj.get("payload").cloned().unwrap_or_else(|| json!({}));
        if !payload.is_object() && !payload.is_null() {
            return Err(Box::new((
                rid,
                ProtoError::new(CODE_BAD_REQUEST, false, "payload 必须是对象"),
            )));
        }
        Ok(Request {
            request_id,
            op,
            session_id: obj
                .get("session_id")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_string),
            stem: obj
                .get("stem")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_string),
            payload: if payload.is_null() {
                json!({})
            } else {
                payload
            },
            timeout_ms: obj.get("timeout_ms").and_then(Value::as_u64).unwrap_or(0),
        })
    }

    pub fn payload_str(&self, key: &str) -> Option<&str> {
        self.payload.get(key).and_then(Value::as_str)
    }

    pub fn payload_bool(&self, key: &str) -> bool {
        self.payload
            .get(key)
            .and_then(Value::as_bool)
            .unwrap_or(false)
    }

    pub fn payload_u64(&self, key: &str) -> Option<u64> {
        self.payload.get(key).and_then(Value::as_u64)
    }
}

/// 一条待写回 Flask 的响应。
#[derive(Debug, Clone)]
pub struct Response {
    pub request_id: Option<String>,
    pub session_id: Option<String>,
    pub generation: Option<u64>,
    pub body: Result<Value, ProtoError>,
}

impl Response {
    pub fn ok(request_id: &str, result: Value) -> Self {
        Self {
            request_id: Some(request_id.to_string()),
            session_id: None,
            generation: None,
            body: Ok(result),
        }
    }

    pub fn err(request_id: Option<String>, error: ProtoError) -> Self {
        Self {
            request_id,
            session_id: None,
            generation: None,
            body: Err(error),
        }
    }

    pub fn with_session(mut self, session_id: &str, generation: u64) -> Self {
        self.session_id = Some(session_id.to_string());
        self.generation = Some(generation);
        self
    }

    pub fn to_line(&self) -> String {
        let mut out = json!({
            "supervisor_protocol_version": SUPERVISOR_PROTOCOL_VERSION,
            "request_id": self.request_id,
        });
        let map = out.as_object_mut().expect("json! 出来的一定是对象");
        if let Some(sid) = &self.session_id {
            map.insert("session_id".into(), json!(sid));
        }
        if let Some(gen) = self.generation {
            map.insert("generation".into(), json!(gen));
        }
        match &self.body {
            Ok(result) => {
                map.insert("ok".into(), json!(true));
                if let Some(fields) = result.as_object() {
                    for (key, value) in fields {
                        map.insert(key.clone(), value.clone());
                    }
                }
            }
            Err(error) => {
                map.insert("ok".into(), json!(false));
                map.insert("error".into(), error.to_json());
            }
        }
        serde_json::to_string(&out).unwrap_or_else(|_| {
            // 序列化一个纯 JSON 值不可能失败；真失败了也必须回一行合法 JSON，
            // 否则 Flask 侧的 reader 线程会被一行垃圾带崩。
            format!(
                "{{\"supervisor_protocol_version\":{},\"request_id\":null,\"ok\":false,\
                 \"error\":{{\"code\":\"internal\",\"retryable\":true,\
                 \"message\":\"响应序列化失败\",\"traceback\":\"\"}}}}",
                SUPERVISOR_PROTOCOL_VERSION
            )
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_a_foreign_protocol_version() {
        let err =
            Request::parse(r#"{"supervisor_protocol_version":2,"request_id":"c-1","op":"ping"}"#)
                .unwrap_err();
        assert_eq!(err.0.as_deref(), Some("c-1"));
        assert_eq!(err.1.code, CODE_BAD_REQUEST);
    }

    #[test]
    fn unknown_fields_are_tolerated() {
        let req = Request::parse(
            r#"{"supervisor_protocol_version":1,"request_id":"c-1","op":"ping","future":42}"#,
        )
        .unwrap();
        assert_eq!(req.op, "ping");
    }

    #[test]
    fn error_extra_never_overwrites_the_four_real_fields() {
        let mut err = ProtoError::new("unknown_stem", false, "stem 不存在");
        err.extra = json!({"known": ["Fig1"], "code": "冒充的"});
        let out = err.to_json();
        assert_eq!(out["code"], "unknown_stem");
        assert_eq!(out["known"][0], "Fig1");
    }
}
