//! 壳的「最小启动诊断」：Python 起不来时，由 Rust 自己备一份极小的诊断包并发给接收服务（ADR 0130）。
//!
//! 与引擎侧 `src/tavotto/engine/diagsend.py` 说的是**同一套外层协议**——`POST /v1/reports/init` →
//! 按服务端给的 `upload{url,fields,file_field}` 做 multipart 上传 → `POST /v1/reports/complete`；
//! `client_request_id` 是幂等键；回执是报告编号 `TVD-…`。两侧不各抄一份 schema：协议常量、失败码归并、
//! 上传地址白名单、init 请求体的键、包里两份文件的内容，全部由 `tests/golden/diag_shell_report.json`
//! 这一份契约样例钉死，Rust 测试（本文件）与 Python 测试（`tests/test_diag_shell_contract.py`）各读一遍。
//!
//! 这份包**只含**：版本 / build、平台 / 架构、失败阶段、稳定错误码、退出码，并明确 `source=shell`、
//! `partial=true`。不含 error.html 的 query 原文、sidecar 的 stderr、日志路径、nonce、token、环境变量、堆栈，
//! 也不读项目目录、不依赖 Python。阶段与错误码都是闭集（[`crate::rescue::STAGES`] / [`crate::rescue::CODES`]），
//! 不在闭集里的值一律写成 `unknown`——自由文本没有任何路径进得了包。
//!
//! 上传地址是编译期常量 [`PRODUCTION_ENDPOINT`]，生产不可改写（环境变量只能把功能**关掉**，不能改地址）。
//! 网络只用已在依赖里的 reqwest（updater 插件带的 rustls），不引入新的云 SDK。

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// 与 `diagsend.PRODUCTION_ENDPOINT` 同一个字面量（契约样例的 `endpoint`，两侧各验）。
pub const PRODUCTION_ENDPOINT: &str = "https://tavotto.cn";
/// 与 `diagnostics.BUNDLE_SCHEMA_VERSION` 同源（契约样例的 `schema_version`，Python 侧验等于它）。
pub const BUNDLE_SCHEMA: u32 = 8;
/// 壳包的故障类别：启动失败属于「安装、启动、更新」。
pub const CATEGORY: &str = "install_update";
/// 壳只存在于桌面发行版。
pub const DISTRIBUTION: &str = "desktop";
pub const API_MAJOR: u32 = 1;
pub const API_VERSION_HEADER: &str = "Tavotto-Diagnostics-Api";
/// 与 `diagsend.MAX_BYTES` 同量级的硬顶；壳包本来只有几百字节，超了就是 bug。
pub const MAX_BYTES: usize = 64 * 1024;
const MAX_RESPONSE_BYTES: usize = 64 * 1024;
const MAX_UPLOAD_FIELDS: usize = 32;
const MAX_FIELD_VALUE: usize = 4096;
const USER_AGENT: &str = "Tavotto-diagnostics-client/1";

pub const ENV_HARD_OFF: &str = "TAVOTTO_NO_DIAG_UPLOAD";
pub const ENV_ENABLE: &str = "TAVOTTO_DIAG_UPLOAD";

/// 环境变量只能关掉上传，不能改地址、不能在被关掉之后重新打开（与 `diagsend.resolve_target` 的关闭判据同源）。
pub fn upload_enabled() -> bool {
    upload_enabled_with(|k| std::env::var(k).ok())
}

fn upload_enabled_with(get: impl Fn(&str) -> Option<String>) -> bool {
    let hard_off = get(ENV_HARD_OFF).is_some_and(|v| v.trim() == "1");
    let off = get(ENV_ENABLE).is_some_and(|v| v.trim() == "0");
    !(hard_off || off)
}

/* -------------------------------------------------------------------------- */
/*  包的内容                                                                    */
/* -------------------------------------------------------------------------- */

/// 进包的全部事实。**每个字段都是闭集或固定形状**，构造时再过一道（[`ReportInput::sanitized`]）。
#[derive(Debug, Clone)]
pub struct ReportInput {
    pub version: String,
    pub debug_build: bool,
    pub os: &'static str,
    pub arch: &'static str,
    pub stage: String,
    pub code: String,
    pub exit_code: Option<i32>,
    pub created_at: String,
}

/// 与 `contract.VERSION_RE` / `diagsend._VERSION_OK` 同一条：`^[0-9A-Za-z.+_-]{1,32}$`。
pub fn version_ok(v: &str) -> bool {
    (1..=32).contains(&v.len())
        && v.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'+' | b'_' | b'-'))
}

impl ReportInput {
    /// 版本不合形状 → `unknown`；阶段 / 错误码不在闭集 → `unknown`。
    fn sanitized(&self) -> ReportInput {
        let mut s = self.clone();
        if !version_ok(&s.version) {
            s.version = "unknown".into();
        }
        if !crate::rescue::STAGES.contains(&s.stage.as_str()) {
            s.stage = "unknown".into();
        }
        if !crate::rescue::CODES.contains(&s.code.as_str()) {
            s.code = "unknown".into();
        }
        s
    }
}

pub fn host_os() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else {
        "other"
    }
}

pub fn host_arch() -> &'static str {
    if cfg!(target_arch = "aarch64") {
        "arm64"
    } else if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else {
        "other"
    }
}

/// 包里两份 JSON 文件（`manifest.json` 与 `report.json`）的内容。
pub fn build_documents(input: &ReportInput) -> (Value, Value) {
    let i = input.sanitized();
    let manifest = json!({
        "schema_version": BUNDLE_SCHEMA,
        "created_at": i.created_at,
        "tavotto_version": i.version,
        "source": "shell",
        "partial": true,
        "privacy_mode": "shell-minimal",
        "contains_frontend_state": false,
        "contains_interaction_trace": false,
        "contains_detail": false,
        "entries": ["manifest.json", "report.json"],
    });
    let report = json!({
        "source": "shell",
        "partial": true,
        "report_version": 1,
        "tavotto": { "version": i.version, "build": if i.debug_build { "debug" } else { "release" } },
        "platform": { "os": i.os, "arch": i.arch, "distribution": DISTRIBUTION },
        "failure": { "stage": i.stage, "code": i.code, "exit_code": i.exit_code },
    });
    (manifest, report)
}

/// 备好的一份包：字节、摘要与这一次发送的幂等键。预览、保存、上传用的是**同一份字节**。
#[derive(Debug, Clone)]
pub struct Prepared {
    pub zip: Vec<u8>,
    pub sha256: String,
    pub request_id: String,
    pub report_text: String,
    pub input: ReportInput,
}

impl Prepared {
    pub fn entries(&self) -> Vec<(&'static str, usize)> {
        // 与 build_zip 的条目顺序一致；字节数是解压后大小
        let (m, r) = documents_bytes(&self.input);
        vec![("manifest.json", m.len()), ("report.json", r.len())]
    }
}

fn documents_bytes(input: &ReportInput) -> (Vec<u8>, Vec<u8>) {
    let (m, r) = build_documents(input);
    (
        serde_json::to_vec_pretty(&m).expect("manifest 可序列化"),
        serde_json::to_vec_pretty(&r).expect("report 可序列化"),
    )
}

pub fn prepare(input: ReportInput) -> Prepared {
    let (m, r) = documents_bytes(&input);
    let zip = build_zip(&[("manifest.json", &m), ("report.json", &r)]);
    Prepared {
        sha256: sha256_hex(&zip),
        request_id: uuid_v4(),
        report_text: String::from_utf8_lossy(&r).into_owned(),
        zip,
        input,
    }
}

pub fn sha256_hex(data: &[u8]) -> String {
    use std::fmt::Write as _;
    Sha256::digest(data)
        .iter()
        .fold(String::with_capacity(64), |mut s, b| {
            let _ = write!(s, "{b:02x}");
            s
        })
}

/// UUIDv4（RFC 4122）：随机 128 位，版本位 4、变体位 10。**不从任何机器信息派生**。
pub fn uuid_v4() -> String {
    let mut b: [u8; 16] = rand::random();
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = |r: &[u8]| r.iter().map(|x| format!("{x:02x}")).collect::<String>();
    format!(
        "{}-{}-{}-{}-{}",
        h(&b[0..4]),
        h(&b[4..6]),
        h(&b[6..8]),
        h(&b[8..10]),
        h(&b[10..16])
    )
}

/* -------------------------------------------------------------------------- */
/*  ZIP（只存不压：几百字节的包没有压缩的必要，也就不引入 zip crate）                   */
/* -------------------------------------------------------------------------- */

fn crc32(data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &b in data {
        crc ^= u32::from(b);
        for _ in 0..8 {
            crc = if crc & 1 == 1 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

/// 1980-01-01 00:00:00（DOS 时间 0、日期 0x0021）：包是确定性的，不泄露本机时间。
const DOS_DATE: u16 = 0x0021;

pub fn build_zip(files: &[(&str, &[u8])]) -> Vec<u8> {
    let mut out: Vec<u8> = Vec::new();
    let mut central: Vec<u8> = Vec::new();
    for (name, data) in files {
        let offset = out.len() as u32;
        let crc = crc32(data);
        let len = data.len() as u32;
        let n = name.as_bytes();
        // local file header
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes()); // version needed
        out.extend_from_slice(&0u16.to_le_bytes()); // flags
        out.extend_from_slice(&0u16.to_le_bytes()); // method: stored
        out.extend_from_slice(&0u16.to_le_bytes()); // time
        out.extend_from_slice(&DOS_DATE.to_le_bytes());
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&(n.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes()); // extra
        out.extend_from_slice(n);
        out.extend_from_slice(data);
        // central directory header
        central.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes()); // made by
        central.extend_from_slice(&20u16.to_le_bytes()); // needed
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&DOS_DATE.to_le_bytes());
        central.extend_from_slice(&crc.to_le_bytes());
        central.extend_from_slice(&len.to_le_bytes());
        central.extend_from_slice(&len.to_le_bytes());
        central.extend_from_slice(&(n.len() as u16).to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes()); // extra
        central.extend_from_slice(&0u16.to_le_bytes()); // comment
        central.extend_from_slice(&0u16.to_le_bytes()); // disk
        central.extend_from_slice(&0u16.to_le_bytes()); // internal attrs
        central.extend_from_slice(&0u32.to_le_bytes()); // external attrs
        central.extend_from_slice(&offset.to_le_bytes());
        central.extend_from_slice(n);
    }
    let cd_offset = out.len() as u32;
    let cd_len = central.len() as u32;
    out.extend_from_slice(&central);
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&cd_len.to_le_bytes());
    out.extend_from_slice(&cd_offset.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out
}

/* -------------------------------------------------------------------------- */
/*  协议：init 请求体、失败归并、上传地址白名单                                       */
/* -------------------------------------------------------------------------- */

/// `POST /v1/reports/init` 的请求体。键集合由契约样例的 `init_keys` 钉住（`note` 壳不发：没有自由文本入口）。
pub fn init_body(p: &Prepared) -> Value {
    let i = p.input.sanitized();
    json!({
        "client_request_id": p.request_id,
        "tavotto_version": i.version,
        "os": i.os,
        "arch": i.arch,
        "distribution": DISTRIBUTION,
        "category": CATEGORY,
        "bundle_schema": BUNDLE_SCHEMA,
        "size": p.zip.len(),
        "sha256": p.sha256,
    })
}

/// 客户端失败码（与 `diagsend.FAILURES` 的键同一份词汇；契约样例的 `failure_codes` 两侧各验）。
#[cfg(test)]
pub const FAILURE_CODES: &[&str] = &[
    "network",
    "timeout",
    "tls",
    "bad_response",
    "api_incompatible",
    "rate_limited",
    "daily_quota_exceeded",
    "uploads_disabled",
    "storage_unavailable",
    "too_large",
    "expired",
    "session_closed",
    "notes_disabled",
    "rejected",
    "client_error",
    "not_found",
    "upload_denied",
    "upload_rejected",
    "upload_unavailable",
    "upload_missing",
    "upload_url_rejected",
    "redirect_blocked",
    "digest_mismatch",
    "unexpected_response",
    "internal",
];

/// 服务端错误 (HTTP 状态, code) → 客户端失败码。与 `diagsend.classify` 逐条同源（契约样例的 `classify_vectors`）。
pub fn classify(status: u16, server_code: &str) -> &'static str {
    match status {
        429 => {
            if server_code == "daily_quota_exceeded" {
                "daily_quota_exceeded"
            } else {
                "rate_limited"
            }
        }
        503 => {
            if server_code == "uploads_disabled" {
                "uploads_disabled"
            } else {
                "storage_unavailable"
            }
        }
        413 => "too_large",
        410 => "expired",
        404 => "not_found",
        409 => match server_code {
            "upload_missing" => "upload_missing",
            "idempotency_conflict" | "session_closed" | "cancelled" => "session_closed",
            _ => "unexpected_response",
        },
        422 => match server_code {
            "notes_disabled" => "notes_disabled",
            "rejected" => "rejected",
            _ => "client_error",
        },
        400 | 415 => "client_error",
        300..=399 => "redirect_blocked",
        _ => "unexpected_response",
    }
}

/// 失败后能不能由用户再点一次（与 `diagsend.FAILURES` 的值同源）。
pub fn retryable(code: &str) -> bool {
    !matches!(
        code,
        "api_incompatible"
            | "too_large"
            | "rejected"
            | "client_error"
            | "upload_rejected"
            | "upload_url_rejected"
            | "redirect_blocked"
            | "digest_mismatch"
            | "internal"
    )
}

/// 允许上传的目的地。生产只有 [`Endpoint::production`]；回环形态只在测试里存在。
#[derive(Debug, Clone)]
pub struct Endpoint {
    base: String,
    loopback: bool,
}

impl Endpoint {
    pub fn production() -> Self {
        Self {
            base: PRODUCTION_ENDPOINT.to_string(),
            loopback: false,
        }
    }

    #[cfg(test)]
    pub fn loopback(port: u16) -> Self {
        Self {
            base: format!("http://127.0.0.1:{port}"),
            loopback: true,
        }
    }
}

/// COS 桶的公网域名 `<名字>-<APPID>.cos.<区域>.myqcloud.com`（与 `diagsend._COS_HOST` 同一条正则，手写等价）。
fn is_cos_host(host: &str) -> bool {
    let Some(rest) = host.strip_suffix(".myqcloud.com") else {
        return false;
    };
    let labels: Vec<&str> = rest.split('.').collect();
    let [bucket, cos, region] = labels[..] else {
        return false;
    };
    let name_chars = |s: &str| {
        s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    };
    if cos != "cos" || !(3..=32).contains(&region.len()) || !name_chars(region) {
        return false;
    }
    let Some((name, appid)) = bucket.rsplit_once('-') else {
        return false;
    };
    (5..=12).contains(&appid.len())
        && appid.bytes().all(|b| b.is_ascii_digit())
        && (1..=63).contains(&name.len())
        && name_chars(name)
        && !name.starts_with('-')
}

/// 服务端给的 `upload.url` → 规范化地址；不在白名单 `None`。与 `diagsend.validate_upload_url` 的判据同源
/// （契约样例的 `upload_url_vectors`）：https、COS 域名、无用户信息 / 查询 / 片段 / 非默认端口、路径只能是根。
pub fn validate_upload_url(raw: &str, endpoint: &Endpoint) -> Option<String> {
    if raw.is_empty() || raw.len() > 512 || !raw.is_ascii() {
        return None;
    }
    // `%` 一律拒：Python 侧不解码主机名，url crate 会——两侧对「看起来像 COS 的主机」不能有分歧
    if raw.chars().any(|c| "\\\r\n\t %".contains(c)) {
        return None;
    }
    let url = url::Url::parse(raw).ok()?;
    if !url.username().is_empty() || url.password().is_some() || url.query().is_some() {
        return None;
    }
    if url.fragment().is_some() {
        return None;
    }
    let host = url.host_str()?.to_ascii_lowercase();
    if endpoint.loopback {
        let base = url::Url::parse(&endpoint.base).ok()?;
        let same = url.scheme() == base.scheme()
            && host == base.host_str()?
            && url.port_or_known_default() == base.port_or_known_default()
            && url.path() == "/v1/local-upload";
        return same.then(|| raw.to_string());
    }
    if url.scheme() != "https" || url.port().is_some() || url.path() != "/" {
        return None;
    }
    is_cos_host(&host).then(|| format!("https://{host}/"))
}

/* -------------------------------------------------------------------------- */
/*  传输                                                                        */
/* -------------------------------------------------------------------------- */

/// 一次发送的失败：稳定码 + 服务端 `Retry-After`。不带任何响应原文 / URL。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SendFailure {
    pub code: &'static str,
    pub retryable: bool,
    pub retry_after: Option<u64>,
}

impl SendFailure {
    fn new(code: &'static str) -> Self {
        Self {
            code,
            retryable: retryable(code),
            retry_after: None,
        }
    }
    fn with_retry(code: &'static str, retry_after: Option<u64>) -> Self {
        Self {
            retry_after,
            ..Self::new(code)
        }
    }
}

struct Resp {
    status: u16,
    api: Option<String>,
    retry_after: Option<u64>,
    body: Vec<u8>,
}

/// reqwest 走 `rustls-no-provider`：没有人装过加密提供者时 `Client::build` 会 panic。updater 插件只在它自己建 client 时才装，
/// 用户可能还没点过「检查修复版本」就点「发送」，所以这里自己确保一次（已装则不动）。
fn ensure_crypto_provider() {
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
}

fn client(timeout_s: u64) -> Result<reqwest::Client, SendFailure> {
    ensure_crypto_provider();
    reqwest::Client::builder()
        // 任何重定向（含 307/308）都不跟：POST 体不会被转交到别处
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(timeout_s))
        .user_agent(USER_AGENT)
        .build()
        .map_err(|_| SendFailure::new("internal"))
}

fn transport_failure(e: &reqwest::Error) -> SendFailure {
    if e.is_timeout() {
        return SendFailure::new("timeout");
    }
    // 证书校验失败没有稳定的类型判据，沿用「错误链里出现 certificate」的保守判断
    let mut src: Option<&dyn std::error::Error> = Some(e);
    while let Some(s) = src {
        let t = s.to_string().to_ascii_lowercase();
        if t.contains("certificate") || t.contains("invalid peer") {
            return SendFailure::new("tls");
        }
        src = s.source();
    }
    SendFailure::new("network")
}

async fn read_response(r: reqwest::Response) -> Result<Resp, SendFailure> {
    let status = r.status().as_u16();
    let api = r
        .headers()
        .get(API_VERSION_HEADER)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);
    let retry_after = r
        .headers()
        .get(reqwest::header::RETRY_AFTER)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok())
        .map(|v| v.min(7 * 86400));
    if r.content_length()
        .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
    {
        return Err(SendFailure::new("bad_response"));
    }
    let body = r.bytes().await.map_err(|e| transport_failure(&e))?;
    if body.len() > MAX_RESPONSE_BYTES {
        return Err(SendFailure::new("bad_response"));
    }
    Ok(Resp {
        status,
        api,
        retry_after,
        body: body.to_vec(),
    })
}

fn check_api_version(r: &Resp) -> Result<(), SendFailure> {
    match &r.api {
        None => Ok(()),
        Some(v) => match v.split('.').next().and_then(|h| h.parse::<u32>().ok()) {
            Some(API_MAJOR) => Ok(()),
            _ => Err(SendFailure::new("api_incompatible")),
        },
    }
}

fn json_body(r: &Resp) -> Result<Value, SendFailure> {
    match serde_json::from_slice::<Value>(&r.body) {
        Ok(v) if v.is_object() => Ok(v),
        _ => Err(SendFailure::new("bad_response")),
    }
}

fn server_code(r: &Resp) -> String {
    serde_json::from_slice::<Value>(&r.body)
        .ok()
        .and_then(|v| {
            v.get("code")
                .and_then(|c| c.as_str().map(|s| s.chars().take(64).collect()))
        })
        .unwrap_or_default()
}

fn server_error(r: &Resp) -> Result<SendFailure, SendFailure> {
    check_api_version(r)?;
    Ok(SendFailure::with_retry(
        classify(r.status, &server_code(r)),
        r.retry_after,
    ))
}

async fn post_json(
    ep: &Endpoint,
    path: &str,
    payload: &Value,
    timeout_s: u64,
) -> Result<Resp, SendFailure> {
    let c = client(timeout_s)?;
    let body = serde_json::to_vec(payload).map_err(|_| SendFailure::new("internal"))?;
    let r = c
        .post(format!("{}{path}", ep.base))
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::ACCEPT, "application/json")
        .body(body)
        .send()
        .await
        .map_err(|e| transport_failure(&e))?;
    read_response(r).await
}

struct UploadSpec {
    url: String,
    fields: Vec<(String, String)>,
    file_field: String,
}

fn field_name_ok(s: &str) -> bool {
    let mut it = s.bytes();
    s.len() <= 64
        && it.next().is_some_and(|b| b.is_ascii_alphanumeric())
        && it.all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// 服务端 `upload.fields` 要**按它给的顺序**写进 multipart（接入要求第 4 条）。`serde_json::Map` 会按键排序，
/// 所以这里自己收：visitor 逐项接，顺序原样留下。
struct OrderedFields(Vec<(String, Value)>);

impl<'de> serde::Deserialize<'de> for OrderedFields {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = OrderedFields;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("an object")
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut m: A,
            ) -> Result<Self::Value, A::Error> {
                let mut out = Vec::new();
                while let Some((k, v)) = m.next_entry::<String, Value>()? {
                    out.push((k, v));
                }
                Ok(OrderedFields(out))
            }
        }
        d.deserialize_map(V)
    }
}

#[derive(serde::Deserialize)]
struct RawUpload {
    method: Option<String>,
    url: Option<String>,
    fields: Option<OrderedFields>,
    file_field: Option<String>,
    content_type: Option<String>,
    max_bytes: Option<u64>,
    expires_at: Option<i64>,
}

#[derive(serde::Deserialize)]
struct RawInit {
    upload: Option<RawUpload>,
}

fn parse_upload_spec(body: &[u8], ep: &Endpoint, size: usize) -> Result<UploadSpec, SendFailure> {
    let bad = || SendFailure::new("bad_response");
    let raw = serde_json::from_slice::<RawInit>(body)
        .map_err(|_| bad())?
        .upload
        .ok_or_else(bad)?;
    if raw.method.as_deref() != Some("POST")
        || raw.content_type.as_deref() != Some("application/zip")
    {
        return Err(bad());
    }
    let url = raw
        .url
        .as_deref()
        .and_then(|u| validate_upload_url(u, ep))
        .ok_or_else(|| SendFailure::new("upload_url_rejected"))?;
    let file_field = raw.file_field.ok_or_else(bad)?;
    let fields_raw = raw.fields.ok_or_else(bad)?.0;
    let max_bytes = raw.max_bytes.ok_or_else(bad)?;
    if raw.expires_at.is_none() {
        return Err(bad());
    }
    if fields_raw.len() > MAX_UPLOAD_FIELDS
        || !field_name_ok(&file_field)
        || fields_raw.iter().any(|(k, _)| *k == file_field)
    {
        return Err(bad());
    }
    let mut fields = Vec::new();
    for (k, v) in fields_raw {
        let v = v.as_str().ok_or_else(bad)?;
        if !field_name_ok(&k) || v.len() > MAX_FIELD_VALUE {
            return Err(bad());
        }
        fields.push((k, v.to_string()));
    }
    if (max_bytes as usize) < size {
        return Err(SendFailure::new("too_large"));
    }
    Ok(UploadSpec {
        url,
        fields,
        file_field,
    })
}

fn multipart(spec: &UploadSpec, data: &[u8]) -> (String, Vec<u8>) {
    let boundary = format!("----tavotto{}", uuid_v4().replace('-', ""));
    let mut body = Vec::with_capacity(data.len() + 1024);
    for (k, v) in &spec.fields {
        body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
        body.extend_from_slice(
            format!("Content-Disposition: form-data; name=\"{k}\"\r\n\r\n").as_bytes(),
        );
        body.extend_from_slice(v.as_bytes());
        body.extend_from_slice(b"\r\n");
    }
    body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
    body.extend_from_slice(
        format!(
            "Content-Disposition: form-data; name=\"{}\"; filename=\"tavotto-diagnostics.zip\"\r\nContent-Type: application/zip\r\n\r\n",
            spec.file_field
        )
        .as_bytes(),
    );
    body.extend_from_slice(data);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    (format!("multipart/form-data; boundary={boundary}"), body)
}

fn report_id_ok(s: &str) -> bool {
    // TVD-XXXX-XXXX-XXXX-XXXX，Crockford base32（无 I L O U）
    let alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
    let parts: Vec<&str> = s.split('-').collect();
    parts.len() == 5
        && parts[0] == "TVD"
        && parts[1..]
            .iter()
            .all(|p| p.len() == 4 && p.chars().all(|c| alphabet.contains(c)))
}

fn token_ok(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// 完整的一次发送：init → 上传 → complete。成功回报告编号。
/// 调用方（`rescue.rs`）保证**只有用户单独点击「发送」之后**才会走到这里；预览 / 保存永远不进这个函数。
pub async fn send(ep: &Endpoint, p: &Prepared) -> Result<String, SendFailure> {
    if p.zip.len() > MAX_BYTES {
        return Err(SendFailure::new("too_large"));
    }
    // ---- init ----
    let r = post_json(ep, "/v1/reports/init", &init_body(p), 15).await?;
    if r.status != 200 && r.status != 201 {
        return Err(server_error(&r)?);
    }
    check_api_version(&r)?;
    let obj = json_body(&r)?;
    let report_id = obj
        .get("report_id")
        .and_then(Value::as_str)
        .filter(|s| report_id_ok(s))
        .ok_or_else(|| SendFailure::new("bad_response"))?
        .to_string();
    if obj.get("ok") != Some(&Value::Bool(true)) {
        return Err(SendFailure::new("bad_response"));
    }
    let state = obj.get("state").and_then(Value::as_str).unwrap_or("");
    if state == "complete" {
        return Ok(report_id); // 幂等重放：这次发送早已成功
    }
    let token = obj
        .get("complete_token")
        .and_then(Value::as_str)
        .filter(|t| token_ok(t))
        .ok_or_else(|| SendFailure::new("bad_response"))?
        .to_string();
    if state != "pending" && state != "verifying" {
        return Err(SendFailure::new("bad_response"));
    }
    let spec = parse_upload_spec(&r.body, ep, p.zip.len())?;
    // ---- upload（服务端已在核实 = 对象早在那里，不重传）----
    if state == "pending" {
        let (ct, body) = multipart(&spec, &p.zip);
        let c = client(60)?;
        let r = c
            .post(&spec.url)
            .header(reqwest::header::CONTENT_TYPE, ct)
            .body(body)
            .send()
            .await
            .map_err(|e| transport_failure(&e))?;
        let status = r.status().as_u16();
        match status {
            200..=299 => {}
            300..=399 => return Err(SendFailure::new("redirect_blocked")),
            401 | 403 => return Err(SendFailure::new("upload_denied")),
            400 | 413 | 415 | 422 => return Err(SendFailure::new("upload_rejected")),
            500..=599 => return Err(SendFailure::new("upload_unavailable")),
            _ => return Err(SendFailure::new("unexpected_response")),
        }
    }
    // ---- complete（丢响应 / 对端 in_progress / 暂未看到对象：同一令牌有界重试）----
    let payload = json!({ "report_id": report_id, "complete_token": token });
    let mut last = SendFailure::new("internal");
    for attempt in 0..COMPLETE_ATTEMPTS {
        if attempt > 0 {
            let wait = last
                .retry_after
                .map_or(COMPLETE_BACKOFF_MS[(attempt - 1).min(2)], |s| {
                    s.min(10) * 1000
                });
            tokio::time::sleep(std::time::Duration::from_millis(
                wait.min(complete_wait_cap()),
            ))
            .await;
        }
        let r = match post_json(ep, "/v1/reports/complete", &payload, 70).await {
            Ok(r) => r,
            Err(f) if f.code == "network" || f.code == "timeout" => {
                last = f;
                continue;
            }
            Err(f) => return Err(f),
        };
        if r.status == 200 {
            check_api_version(&r)?;
            let o = json_body(&r)?;
            let sha = o.get("sha256").and_then(Value::as_str).unwrap_or("");
            let size = o.get("size").and_then(Value::as_u64);
            if o.get("ok") != Some(&Value::Bool(true))
                || o.get("state").and_then(Value::as_str) != Some("complete")
                || o.get("report_id").and_then(Value::as_str) != Some(report_id.as_str())
            {
                return Err(SendFailure::new("bad_response"));
            }
            if sha != p.sha256 || size != Some(p.zip.len() as u64) {
                return Err(SendFailure::new("digest_mismatch"));
            }
            return Ok(report_id);
        }
        let sc = server_code(&r);
        if r.status == 409 && sc == "already_complete" {
            return Ok(report_id);
        }
        if r.status == 409 && sc == "in_progress" {
            last = SendFailure::with_retry("unexpected_response", r.retry_after);
            continue;
        }
        let f = server_error(&r)?;
        if f.code == "upload_missing" {
            last = f;
            continue;
        }
        return Err(f);
    }
    Err(last)
}

const COMPLETE_ATTEMPTS: usize = 4;
const COMPLETE_BACKOFF_MS: [u64; 3] = [1000, 2000, 4000];

#[cfg(not(test))]
fn complete_wait_cap() -> u64 {
    u64::MAX
}
#[cfg(test)]
fn complete_wait_cap() -> u64 {
    5 // 测试里不真等
}

/* -------------------------------------------------------------------------- */
/*  测试                                                                        */
/* -------------------------------------------------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    fn contract() -> Value {
        serde_json::from_str(include_str!("../../tests/golden/diag_shell_report.json"))
            .expect("契约样例是合法 JSON")
    }

    fn strs(v: &Value) -> Vec<String> {
        v.as_array()
            .unwrap()
            .iter()
            .map(|x| x.as_str().unwrap().to_string())
            .collect()
    }

    fn sample_input(c: &Value) -> ReportInput {
        let i = &c["input"];
        ReportInput {
            version: i["version"].as_str().unwrap().into(),
            debug_build: i["debug_build"].as_bool().unwrap(),
            os: match i["os"].as_str().unwrap() {
                "macos" => "macos",
                "windows" => "windows",
                "linux" => "linux",
                _ => "other",
            },
            arch: match i["arch"].as_str().unwrap() {
                "arm64" => "arm64",
                "x86_64" => "x86_64",
                _ => "other",
            },
            stage: i["stage"].as_str().unwrap().into(),
            code: i["code"].as_str().unwrap().into(),
            exit_code: i["exit_code"].as_i64().map(|n| n as i32),
            created_at: i["created_at"].as_str().unwrap().into(),
        }
    }

    #[test]
    fn the_documents_equal_the_cross_language_sample() {
        let c = contract();
        let (m, r) = build_documents(&sample_input(&c));
        assert_eq!(m, c["manifest"], "manifest.json 与契约样例不一致");
        assert_eq!(r, c["report"], "report.json 与契约样例不一致");
        assert_eq!(strs(&c["entries"]), ["manifest.json", "report.json"]);
    }

    #[test]
    fn the_protocol_constants_equal_the_sample() {
        let c = contract();
        assert_eq!(c["endpoint"], PRODUCTION_ENDPOINT);
        assert_eq!(c["schema_version"], BUNDLE_SCHEMA);
        assert_eq!(c["category"], CATEGORY);
        assert_eq!(c["distribution"], DISTRIBUTION);
        assert_eq!(c["api_major"], API_MAJOR);
        assert_eq!(c["api_version_header"], API_VERSION_HEADER);
        assert_eq!(c["off_switch"]["hard_off_env"], ENV_HARD_OFF);
        assert_eq!(c["off_switch"]["enable_env"], ENV_ENABLE);
        let codes: Vec<String> = FAILURE_CODES.iter().map(|s| s.to_string()).collect();
        assert_eq!(strs(&c["failure_codes"]), codes, "失败码词汇两侧不同源");
        let own = |l: &[&str]| l.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(strs(&c["stages"]), own(crate::rescue::STAGES));
        assert_eq!(strs(&c["codes"]), own(crate::rescue::CODES));
    }

    #[test]
    fn the_init_body_has_exactly_the_contract_keys() {
        let c = contract();
        let p = prepare(sample_input(&c));
        let body = init_body(&p);
        let keys: BTreeSet<String> = body.as_object().unwrap().keys().cloned().collect();
        let want: BTreeSet<String> = strs(&c["init_keys"]).into_iter().collect();
        assert_eq!(keys, want);
        // 没有 note（壳没有自由文本入口），也没有遥测标识
        for banned in strs(&c["forbidden_init_keys"]) {
            assert!(!keys.contains(&banned), "{banned}");
        }
        assert_eq!(body["size"], p.zip.len());
        assert_eq!(body["sha256"], sha256_hex(&p.zip));
        assert_eq!(body["category"], c["category"]);
    }

    #[test]
    fn the_request_id_is_a_fresh_uuid_v4_each_time() {
        let c = contract();
        let a = prepare(sample_input(&c)).request_id;
        let b = prepare(sample_input(&c)).request_id;
        assert_ne!(a, b);
        for id in [&a, &b] {
            let p: Vec<&str> = id.split('-').collect();
            assert_eq!(
                p.iter().map(|s| s.len()).collect::<Vec<_>>(),
                [8, 4, 4, 4, 12]
            );
            assert!(p[2].starts_with('4'));
            assert!("89ab".contains(p[3].chars().next().unwrap()));
        }
    }

    #[test]
    fn free_text_never_reaches_the_bundle() {
        let c = contract();
        let mut input = sample_input(&c);
        // 攻击面：stage / code 若被谁塞进自由文本（query 原文、stderr、路径、token），必须落成 unknown
        input.stage = "handshake /Users/alice/secret".into();
        input.code = "stderr: nonce=abcd token=xyz".into();
        input.version = "1.0 with spaces and /paths".into();
        let p = prepare(input);
        let text = String::from_utf8_lossy(&p.zip).to_string();
        for needle in ["alice", "nonce", "token", "stderr", "/Users", "spaces"] {
            assert!(!text.contains(needle), "包里不该出现 {needle}");
        }
        let (_, r) = build_documents(&p.input);
        assert_eq!(r["failure"]["stage"], "unknown");
        assert_eq!(r["failure"]["code"], "unknown");
        assert_eq!(r["tavotto"]["version"], "unknown");
    }

    #[test]
    fn the_report_has_only_the_contract_fields() {
        let c = contract();
        let (m, r) = build_documents(&sample_input(&c));
        fn walk(v: &Value, out: &mut Vec<String>) {
            if let Some(o) = v.as_object() {
                for (k, x) in o {
                    out.push(k.clone());
                    walk(x, out);
                }
            }
        }
        let mut keys = Vec::new();
        walk(&m, &mut keys);
        walk(&r, &mut keys);
        for banned in strs(&c["forbidden_keys"]) {
            assert!(!keys.contains(&banned), "不该有字段 {banned}");
        }
        assert_eq!(r["source"], "shell");
        assert_eq!(r["partial"], true);
        assert_eq!(m["source"], "shell");
        assert_eq!(m["partial"], true);
    }

    /// 自己写的 ZIP 要能被标准实现读出来：用 CRC 与结构自检（Python 侧另有 zipfile + diagsend.zip_problem 的验证）。
    #[test]
    fn the_stored_zip_is_well_formed() {
        let z = build_zip(&[("manifest.json", b"{\"a\":1}"), ("report.json", b"{}")]);
        assert_eq!(&z[0..4], b"PK\x03\x04");
        // EOCD 在最后 22 字节
        let eocd = &z[z.len() - 22..];
        assert_eq!(&eocd[0..4], &0x0605_4b50u32.to_le_bytes());
        assert_eq!(u16::from_le_bytes([eocd[10], eocd[11]]), 2);
        let cd_off = u32::from_le_bytes([eocd[16], eocd[17], eocd[18], eocd[19]]) as usize;
        assert_eq!(&z[cd_off..cd_off + 4], &0x0201_4b50u32.to_le_bytes());
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926, "CRC-32 标准向量");
    }

    /// 样例里存着 Rust 写出的**整包字节**。Python 侧用标准库 `zipfile` + `diagsend.zip_problem`（服务端同一组判据）
    /// 读它——手写的 ZIP 写手是不是真的合法，由站在 Rust 之外的读者说了算，不由 Rust 自己的结构自检说了算。
    #[test]
    fn the_zip_bytes_equal_the_sample_bytes() {
        use base64::Engine as _;
        let c = contract();
        let p = prepare(sample_input(&c));
        let got = base64::engine::general_purpose::STANDARD.encode(&p.zip);
        assert_eq!(
            got,
            c["zip_base64"].as_str().unwrap_or(""),
            "ZIP 字节与样例不一致；确认是有意改动后，把 zip_base64 换成：\n{got}"
        );
        assert_eq!(p.sha256, sha256_hex(&p.zip));
    }

    #[test]
    fn classify_matches_the_sample_vectors() {
        let c = contract();
        let vectors = c["classify_vectors"].as_array().unwrap();
        assert!(vectors.len() >= 15);
        for v in vectors {
            let got = classify(
                v["status"].as_u64().unwrap() as u16,
                v["server_code"].as_str().unwrap(),
            );
            assert_eq!(got, v["client_code"].as_str().unwrap(), "{v}");
            assert!(FAILURE_CODES.contains(&got));
        }
        for code in FAILURE_CODES {
            assert_eq!(
                retryable(code),
                c["retryable"][code].as_bool().unwrap(),
                "{code}"
            );
        }
    }

    #[test]
    fn upload_urls_follow_the_sample_vectors() {
        let c = contract();
        let prod = Endpoint::production();
        let vectors = c["upload_url_vectors"].as_array().unwrap();
        assert!(vectors.len() >= 14);
        for v in vectors {
            let url = v["url"].as_str().unwrap();
            let got = validate_upload_url(url, &prod);
            match v["normalized"].as_str() {
                Some(want) => assert_eq!(got.as_deref(), Some(want), "{url}"),
                None => assert_eq!(got, None, "{url}"),
            }
        }
    }

    #[test]
    fn the_destination_is_a_compile_time_constant_the_environment_cannot_move() {
        // 环境变量只有两个：都只能关。没有任何一个能改地址
        assert!(upload_enabled_with(|_| None));
        assert!(!upload_enabled_with(
            |k| (k == ENV_HARD_OFF).then(|| "1".into())
        ));
        assert!(!upload_enabled_with(
            |k| (k == ENV_ENABLE).then(|| "0".into())
        ));
        // `TAVOTTO_DIAG_UPLOAD=1` 与引擎侧一样无效果；地址类变量被读了也没有任何反应
        assert!(upload_enabled_with(
            |k| (k == ENV_ENABLE).then(|| "1".into())
        ));
        assert!(upload_enabled_with(
            |k| (k == "TAVOTTO_DIAG_ENDPOINT").then(|| "https://evil.example".into())
        ));
        let src = include_str!("shell_report.rs");
        let prod_part = &src[..src.find("#[cfg(test)]\nmod tests").unwrap()];
        assert!(
            !prod_part.contains("DIAG_ENDPOINT"),
            "生产代码不许读任何端点环境变量"
        );
        assert_eq!(Endpoint::production().base, PRODUCTION_ENDPOINT);
    }

    #[test]
    fn the_sample_bundle_is_below_the_hard_cap_and_far_below_the_server_limit() {
        let c = contract();
        let p = prepare(sample_input(&c));
        assert!(p.zip.len() < 4096, "壳包应该只有几百字节：{}", p.zip.len());
    }

    // ---- 传输：本地假接收服务（只有网络是假的，协议步骤、请求体、字段顺序都是真的）----
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    #[derive(Default)]
    struct Fake {
        calls: Vec<(String, Vec<u8>, String)>, // path, body, content-type
        complete_fail_first: bool,
        init_status: u16,
        wrong_digest: bool,
        upload_status: u16,
        complete_hits: usize,
        redirect_init: bool,
    }

    async fn serve(fake: Arc<Mutex<Fake>>) -> u16 {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let Ok((mut s, _)) = l.accept().await else {
                    return;
                };
                let fake = fake.clone();
                tokio::spawn(async move {
                    let mut buf = Vec::new();
                    let mut tmp = [0u8; 8192];
                    let (head_end, len) = loop {
                        let n = s.read(&mut tmp).await.unwrap_or(0);
                        if n == 0 {
                            return;
                        }
                        buf.extend_from_slice(&tmp[..n]);
                        if let Some(p) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                            let head = String::from_utf8_lossy(&buf[..p]).to_ascii_lowercase();
                            let len = head
                                .lines()
                                .find_map(|l| l.strip_prefix("content-length: "))
                                .and_then(|v| v.trim().parse::<usize>().ok())
                                .unwrap_or(0);
                            break (p + 4, len);
                        }
                    };
                    while buf.len() < head_end + len {
                        let n = s.read(&mut tmp).await.unwrap_or(0);
                        if n == 0 {
                            break;
                        }
                        buf.extend_from_slice(&tmp[..n]);
                    }
                    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
                    let path = head.split_whitespace().nth(1).unwrap_or("").to_string();
                    let ct = head
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-type: ")
                                .map(str::to_string)
                        })
                        .unwrap_or_default();
                    let body = buf[head_end..].to_vec();
                    let (status, reply, extra) = {
                        let mut f = fake.lock().unwrap();
                        f.calls.push((path.clone(), body.clone(), ct));
                        match path.as_str() {
                            "/v1/reports/init" if f.redirect_init => (
                                307,
                                String::from("{}"),
                                "Location: https://evil.example/\r\n".to_string(),
                            ),
                            "/v1/reports/init" if f.init_status != 0 => (
                                f.init_status,
                                r#"{"ok":false,"code":"uploads_disabled","error":"x"}"#.into(),
                                "Retry-After: 7\r\n".into(),
                            ),
                            "/v1/reports/init" => {
                                let port = s.local_addr().unwrap().port();
                                (
                                    201,
                                    // 手写 JSON 文本：serde_json 的 Map 会按键排序，会把「服务端给的顺序」抹平
                                    format!(
                                        r#"{{"ok":true,"report_id":"TVD-ABCD-EFGH-JKMN-PQRS","state":"pending","complete_token":"{}","expires_at":1,"upload":{{"method":"POST","url":"http://127.0.0.1:{port}/v1/local-upload","fields":{{"zeta":"z","key":"incoming/x.zip","alpha":"a"}},"file_field":"file","content_type":"application/zip","max_bytes":65536,"expires_at":1}}}}"#,
                                        "a".repeat(64)
                                    ),
                                    String::new(),
                                )
                            }
                            "/v1/local-upload" if f.upload_status != 0 => {
                                (f.upload_status, "{}".into(), String::new())
                            }
                            "/v1/local-upload" => (204, String::new(), String::new()),
                            "/v1/reports/complete" => {
                                f.complete_hits += 1;
                                if f.complete_fail_first && f.complete_hits == 1 {
                                    // 先执行再断线：响应丢在路上
                                    drop(f);
                                    return;
                                }
                                let init = f
                                    .calls
                                    .iter()
                                    .find(|c| c.0 == "/v1/reports/init")
                                    .map(|c| serde_json::from_slice::<Value>(&c.1).unwrap())
                                    .unwrap();
                                let sha = if f.wrong_digest {
                                    "0".repeat(64)
                                } else {
                                    init["sha256"].as_str().unwrap().into()
                                };
                                (
                                    200,
                                    json!({"ok": true, "report_id": "TVD-ABCD-EFGH-JKMN-PQRS", "state": "complete", "replay": false, "size": init["size"], "sha256": sha}).to_string(),
                                    String::new(),
                                )
                            }
                            _ => (404, "{}".into(), String::new()),
                        }
                    };
                    let resp = format!(
                        "HTTP/1.1 {status} X\r\nTavotto-Diagnostics-Api: 1.0.0\r\nContent-Type: application/json\r\n{extra}Content-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                        reply.len()
                    );
                    let _ = s.write_all(resp.as_bytes()).await;
                });
            }
        });
        port
    }

    fn prepared() -> Prepared {
        prepare(sample_input(&contract()))
    }

    fn paths(f: &Arc<Mutex<Fake>>) -> Vec<String> {
        f.lock()
            .unwrap()
            .calls
            .iter()
            .map(|c| c.0.clone())
            .collect()
    }

    #[tokio::test]
    async fn a_full_send_runs_init_upload_complete_in_order_and_uploads_the_prepared_bytes() {
        let fake = Arc::new(Mutex::new(Fake::default()));
        let port = serve(fake.clone()).await;
        let p = prepared();
        let id = send(&Endpoint::loopback(port), &p).await.unwrap();
        assert_eq!(id, "TVD-ABCD-EFGH-JKMN-PQRS");
        assert_eq!(
            paths(&fake),
            [
                "/v1/reports/init",
                "/v1/local-upload",
                "/v1/reports/complete"
            ]
        );
        let f = fake.lock().unwrap();
        let init: Value = serde_json::from_slice(&f.calls[0].1).unwrap();
        assert_eq!(init["client_request_id"], p.request_id);
        // multipart：授权字段在前、文件字段最后；上传的就是备好的那份字节
        let up = &f.calls[1];
        assert!(up.2.starts_with("multipart/form-data; boundary="));
        let text = String::from_utf8_lossy(&up.1).to_string();
        let at = |n: &str| text.find(&format!("name=\"{n}\"")).unwrap();
        assert!(
            at("zeta") < at("key") && at("key") < at("alpha") && at("alpha") < at("file"),
            "字段按服务端给的顺序（不是字母序），文件字段最后"
        );
        assert!(up.1.windows(p.zip.len()).any(|w| w == p.zip.as_slice()));
    }

    #[tokio::test]
    async fn a_lost_complete_response_is_retried_with_the_same_token_and_creates_one_report() {
        let fake = Arc::new(Mutex::new(Fake {
            complete_fail_first: true,
            ..Fake::default()
        }));
        let port = serve(fake.clone()).await;
        let id = send(&Endpoint::loopback(port), &prepared()).await.unwrap();
        assert_eq!(id, "TVD-ABCD-EFGH-JKMN-PQRS");
        let p = paths(&fake);
        assert_eq!(p.iter().filter(|x| *x == "/v1/reports/init").count(), 1);
        assert_eq!(p.iter().filter(|x| *x == "/v1/local-upload").count(), 1);
        assert_eq!(p.iter().filter(|x| *x == "/v1/reports/complete").count(), 2);
        let f = fake.lock().unwrap();
        let bodies: Vec<&Vec<u8>> = f
            .calls
            .iter()
            .filter(|c| c.0 == "/v1/reports/complete")
            .map(|c| &c.1)
            .collect();
        assert_eq!(bodies[0], bodies[1], "重试用同一份令牌");
    }

    #[tokio::test]
    async fn server_errors_are_classified_and_never_echoed() {
        let fake = Arc::new(Mutex::new(Fake {
            init_status: 503,
            ..Fake::default()
        }));
        let port = serve(fake.clone()).await;
        let e = send(&Endpoint::loopback(port), &prepared())
            .await
            .unwrap_err();
        assert_eq!(e.code, "uploads_disabled");
        assert_eq!(e.retry_after, Some(7));
        assert!(e.retryable);
        assert_eq!(paths(&fake), ["/v1/reports/init"], "init 失败就不上传");
    }

    #[tokio::test]
    async fn redirects_are_never_followed() {
        let fake = Arc::new(Mutex::new(Fake {
            redirect_init: true,
            ..Fake::default()
        }));
        let port = serve(fake.clone()).await;
        let e = send(&Endpoint::loopback(port), &prepared())
            .await
            .unwrap_err();
        assert_eq!(e.code, "redirect_blocked");
        assert_eq!(paths(&fake), ["/v1/reports/init"]);
    }

    #[tokio::test]
    async fn a_digest_the_server_disagrees_with_is_a_failure_not_a_receipt() {
        let fake = Arc::new(Mutex::new(Fake {
            wrong_digest: true,
            ..Fake::default()
        }));
        let port = serve(fake.clone()).await;
        let e = send(&Endpoint::loopback(port), &prepared())
            .await
            .unwrap_err();
        assert_eq!(e.code, "digest_mismatch");
        assert!(!e.retryable);
    }

    #[tokio::test]
    async fn an_upload_rejection_stops_before_complete() {
        let fake = Arc::new(Mutex::new(Fake {
            upload_status: 403,
            ..Fake::default()
        }));
        let port = serve(fake.clone()).await;
        let e = send(&Endpoint::loopback(port), &prepared())
            .await
            .unwrap_err();
        assert_eq!(e.code, "upload_denied");
        assert_eq!(paths(&fake), ["/v1/reports/init", "/v1/local-upload"]);
    }

    #[tokio::test]
    async fn unreachable_server_is_a_network_failure_that_can_be_retried() {
        // 端口刚释放，连不上
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        drop(l);
        let e = send(&Endpoint::loopback(port), &prepared())
            .await
            .unwrap_err();
        assert_eq!(e.code, "network");
        assert!(e.retryable);
    }
}
