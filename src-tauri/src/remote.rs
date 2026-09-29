//! 「连接远程实例」（ADR 0105）：壳在一个单独的窗口里加载经 `ssh -L` 转发过来的
//! 服务器上的 Tavotto 引擎。
//!
//! 这里只放两件纯逻辑（窗口与 IPC 的接线在 `main.rs`）：
//!
//! 1. **登录地址的解析**。用户粘进来的是服务器上 `tavotto --no-browser` 打印的那一行
//!    `http://127.0.0.1:<端口>/#dnonce=<一次性口令>`。只认 `127.0.0.1`——引擎的 Host
//!    校验本来就只认这一种写法（ADR 0008），而且壳的导航守卫只许窗口停在本机回环上：
//!    转发由 ssh 负责，壳绝不自己去连一个外部主机。
//! 2. **远程引擎够不够新**。页面是远程引擎自己供的，壳管不了它的前端是哪一版；
//!    老前端不知道自己在远程窗口里，会去调本机的对话框与 Finder（ACL 会拒，但用户
//!    看到的是一串失败）。所以连之前先问一次公开的 `/api/version`，要它报出
//!    `REMOTE_WINDOW_FEATURE`——**问机器要能力标记，不按版本号猜**。
//!
//! 失败一律回**稳定 code**（`ConnectError::code`），由壳自带的 `connect.html` 翻成人话；
//! 英文 code 不进界面，与 `codex_integration` 同一条纪律。

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;

/// 远程引擎在 `/api/version` 的 `features` 里报的能力标记。
///
/// **与 `src/tavotto/app.py` 的 `DESKTOP_REMOTE_WINDOW_FEATURE` 严格同源**
/// （`tests/test_desktop_remote.py` 两侧比字面值）。它说的是「这一版前端认得出
/// 远程实例窗口、会把本机文件类能力全部让给浏览器回退」。
pub const REMOTE_WINDOW_FEATURE: &str = "desktop-remote-window";

/// 探测的连接 / 读写时限。转发断了时 `connect` 会立即被拒；这两个数只防
/// 「端口有人听、却一个字都不回」的挂死。
const PROBE_CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
const PROBE_IO_TIMEOUT: Duration = Duration::from_secs(5);
/// `/api/version` 的回应只有几十个字节；读满这么多还没完就不是它。
const PROBE_MAX_BYTES: u64 = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoginUrl {
    pub port: u16,
    pub nonce: String,
}

impl LoginUrl {
    /// 真正拿去导航的地址：由解析出的两段**重新拼**，不把用户粘进来的原文交给 webview
    /// （原文里的路径、查询串、别的 fragment 一概丢掉）。`lang` 与主窗口的落地 URL 同一语义。
    pub fn landing(&self, lang: Option<&str>) -> String {
        let query = lang.map(|t| format!("?lang={t}")).unwrap_or_default();
        format!(
            "http://127.0.0.1:{}/{query}#dnonce={}",
            self.port, self.nonce
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectError {
    /// 不是一个 http 地址
    BadUrl,
    /// 主机不是 `127.0.0.1`（包括 `localhost`：引擎的 Host 校验会 403）
    NotLoopback,
    /// 没有 `#dnonce=`，或口令里有不该有的字符
    NoNonce,
    /// 端口就是壳自己的本机 sidecar
    IsLocalEngine,
    /// 端口上没人听（转发没开 / 服务器上的 Tavotto 没在跑）
    Unreachable,
    /// 有人听，但不是 Tavotto
    NotTavotto,
    /// 是 Tavotto，但前端太旧，不认得远程实例窗口
    TooOld,
    /// 调用方不是远程实例窗口
    BadWindow,
}

impl ConnectError {
    /// 与 `src-tauri/shell/connect.html` 的 `STRINGS.<locale>.errors` 键严格同源
    /// （`tests/test_desktop_remote.py` 比两侧集合）。
    pub fn code(self) -> &'static str {
        match self {
            Self::BadUrl => "bad_url",
            Self::NotLoopback => "not_loopback",
            Self::NoNonce => "no_nonce",
            Self::IsLocalEngine => "is_local_engine",
            Self::Unreachable => "unreachable",
            Self::NotTavotto => "not_tavotto",
            Self::TooOld => "too_old",
            Self::BadWindow => "bad_window",
        }
    }

    #[cfg(test)]
    pub const ALL: [ConnectError; 8] = [
        Self::BadUrl,
        Self::NotLoopback,
        Self::NoNonce,
        Self::IsLocalEngine,
        Self::Unreachable,
        Self::NotTavotto,
        Self::TooOld,
        Self::BadWindow,
    ];
}

/// 解析用户粘进来的登录地址。口令的字符集与前端 `bootstrapDesktopSession` 的
/// `dnonce=([A-Za-z0-9_-]+)` 同一集合——它下一步要被拼回 URL，含 `&`/`#` 的「口令」
/// 会把 fragment 改掉。
pub fn parse_login_url(raw: &str) -> Result<LoginUrl, ConnectError> {
    let url = url::Url::parse(raw.trim()).map_err(|_| ConnectError::BadUrl)?;
    if url.scheme() != "http" {
        return Err(ConnectError::BadUrl);
    }
    if url.host() != Some(url::Host::Ipv4(Ipv4Addr::LOCALHOST)) {
        return Err(ConnectError::NotLoopback);
    }
    // `http://127.0.0.1/` 的端口是 80：引擎从不开在那里，但它是一个合法端口，
    // 交给探测去说「连不上」，不在这里另编一条错误。
    let port = url.port_or_known_default().ok_or(ConnectError::BadUrl)?;
    let nonce = url
        .fragment()
        .unwrap_or("")
        .split('&')
        .find_map(|kv| kv.strip_prefix("dnonce="))
        .filter(|n| {
            !n.is_empty()
                && n.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        })
        .ok_or(ConnectError::NoNonce)?;
    Ok(LoginUrl {
        port,
        nonce: nonce.to_string(),
    })
}

/// 判一份 `GET /api/version` 的原始 HTTP 回应。返回远程引擎的版本号。
pub fn judge_version_response(raw: &[u8]) -> Result<String, ConnectError> {
    let text = String::from_utf8_lossy(raw);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or(ConnectError::NotTavotto)?;
    let status_ok = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        == Some("200");
    if !status_ok {
        return Err(ConnectError::NotTavotto);
    }
    let json: serde_json::Value =
        serde_json::from_str(body.trim()).map_err(|_| ConnectError::NotTavotto)?;
    let version = json
        .get("version")
        .and_then(|v| v.as_str())
        .ok_or(ConnectError::NotTavotto)?
        .to_string();
    let has_feature = json
        .get("features")
        .and_then(|f| f.as_array())
        .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(REMOTE_WINDOW_FEATURE)));
    if !has_feature {
        return Err(ConnectError::TooOld);
    }
    Ok(version)
}

/// 问一次 `http://127.0.0.1:<port>/api/version`（公开端点，ADR 0008）。阻塞，调用方放
/// 到 `spawn_blocking` 里。HTTP/1.0 + `Connection: close`：读到对端关闭即是全文，
/// 不必为这一次请求引一个 HTTP 客户端。
pub fn probe(port: u16) -> Result<String, ConnectError> {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&addr, PROBE_CONNECT_TIMEOUT)
        .map_err(|_| ConnectError::Unreachable)?;
    let _ = stream.set_read_timeout(Some(PROBE_IO_TIMEOUT));
    let _ = stream.set_write_timeout(Some(PROBE_IO_TIMEOUT));
    // Host 必须是 `127.0.0.1:<port>` 这一种写法，否则引擎回 403（ADR 0008）
    let req = format!(
        "GET /api/version HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(req.as_bytes())
        .map_err(|_| ConnectError::Unreachable)?;
    let mut raw = Vec::new();
    // 读超时 / 对端中途断开：手里有多少算多少，交给判据去说「不是 Tavotto」
    let _ = stream.take(PROBE_MAX_BYTES).read_to_end(&mut raw);
    judge_version_response(&raw)
}

/// 远程实例窗口的「这一轮」：窗口代次 + 此刻放行的本机端口。
///
/// label `remote` 是可复用的：关掉再开是同名的**另一个**窗口。探测要等网络，途中用户
/// 可能关窗重开——等回来的结果若按 label 去找「当前」窗口，放弃了的那次连接就会把新开的
/// 连接页换成旧的远程会话（Codex #702）。所以每次开窗、每次窗口没了都换一代；探测发起时
/// 记下代次，回来时代次不符就整个丢掉：不放行端口、不导航。
#[derive(Debug, Default)]
pub struct RemoteSlot {
    generation: u64,
    port: Option<u16>,
}

impl RemoteSlot {
    /// 新开一个远程实例窗口：新的一代，什么端口都不放。
    pub fn open_window(&mut self) -> u64 {
        self.generation += 1;
        self.port = None;
        self.generation
    }

    /// 远程实例窗口没了：这一代作废，途中的探测回来一律丢弃。
    pub fn window_gone(&mut self) {
        self.generation += 1;
        self.port = None;
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// 某一代窗口的导航守卫此刻放行的端口。别的代（已经关掉的窗口）一个都不放。
    pub fn allowed_port(&self, generation: u64) -> Option<u16> {
        if generation == self.generation {
            self.port
        } else {
            None
        }
    }

    /// 探测结果回来：还是发起它的那一代才放行端口。返回 false = 结果作废。
    fn admit(&mut self, generation: u64, port: u16) -> bool {
        if generation != self.generation {
            return false;
        }
        self.port = Some(port);
        true
    }
}

/// 连接成功后对窗口做的事（改标题、导航）。收成 trait 是为了让「作废的结果不碰窗口」
/// 成为能跑的断言；生产实现握的是**发起调用的那个窗口实例**，不按 label 重新找。
pub trait Navigate {
    fn navigate(&self) -> Result<(), ConnectError>;
}

/// 探测回来之后的唯一出口：先在锁里核代次并放行端口（导航守卫要先认得这个端口，
/// 否则这一跳会被自己拦掉），再放开锁去导航——握着锁调窗口 API 可能与主线程上的
/// 窗口事件（`window_gone`）互等。代次不符 = 窗口已关 / 已重开，回 `BadWindow`。
pub fn finish_connect<N: Navigate + ?Sized>(
    slot: &std::sync::Mutex<RemoteSlot>,
    generation: u64,
    port: u16,
    nav: &N,
) -> Result<(), ConnectError> {
    if !slot.lock().unwrap().admit(generation, port) {
        return Err(ConnectError::BadWindow);
    }
    nav.navigate()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_the_line_the_server_prints() {
        let got = parse_login_url("http://127.0.0.1:5089/#dnonce=AbC_12-x").unwrap();
        assert_eq!(
            got,
            LoginUrl {
                port: 5089,
                nonce: "AbC_12-x".into()
            }
        );
        // 前后空白（从终端复制常带换行）与 fragment 里别的键都不碍事
        let got = parse_login_url("  http://127.0.0.1:5189/?open=a#x=1&dnonce=zz\n").unwrap();
        assert_eq!(got.port, 5189);
        assert_eq!(got.nonce, "zz");
    }

    #[test]
    fn rejects_everything_that_is_not_the_loopback_login_line() {
        let cases: &[(&str, ConnectError)] = &[
            ("", ConnectError::BadUrl),
            ("127.0.0.1:5089/#dnonce=a", ConnectError::BadUrl),
            ("https://127.0.0.1:5089/#dnonce=a", ConnectError::BadUrl),
            ("file:///etc/passwd#dnonce=a", ConnectError::BadUrl),
            // 引擎的 Host 校验只认 127.0.0.1，这几种写法到了那边都是 403
            ("http://localhost:5089/#dnonce=a", ConnectError::NotLoopback),
            ("http://[::1]:5089/#dnonce=a", ConnectError::NotLoopback),
            ("http://127.0.0.2:5089/#dnonce=a", ConnectError::NotLoopback),
            (
                "http://example.com:5089/#dnonce=a",
                ConnectError::NotLoopback,
            ),
            ("http://10.0.0.5:5089/#dnonce=a", ConnectError::NotLoopback),
            ("http://127.0.0.1:5089/", ConnectError::NoNonce),
            ("http://127.0.0.1:5089/#dnonce=", ConnectError::NoNonce),
            ("http://127.0.0.1:5089/#nonce=abc", ConnectError::NoNonce),
            // 口令要被拼回 URL：带着 URL 语法字符的一律不收
            ("http://127.0.0.1:5089/#dnonce=a%20b", ConnectError::NoNonce),
            ("http://127.0.0.1:5089/#dnonce=a/b", ConnectError::NoNonce),
        ];
        for (raw, want) in cases {
            assert_eq!(parse_login_url(raw), Err(*want), "{raw:?}");
        }
    }

    #[test]
    fn the_landing_url_is_rebuilt_from_the_parsed_parts_only() {
        let u = parse_login_url("http://127.0.0.1:5089/somewhere?x=1#dnonce=abc&evil=1").unwrap();
        assert_eq!(u.landing(None), "http://127.0.0.1:5089/#dnonce=abc");
        assert_eq!(
            u.landing(Some("en-US")),
            "http://127.0.0.1:5089/?lang=en-US#dnonce=abc"
        );
    }

    fn http(status: &str, body: &str) -> Vec<u8> {
        format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\r\n{body}").into_bytes()
    }

    #[test]
    fn a_new_enough_engine_reports_the_feature() {
        let raw = http(
            "200 OK",
            r#"{"build":"index-abc","version":"0.18.0","features":["desktop-remote-window"]}"#,
        );
        assert_eq!(judge_version_response(&raw), Ok("0.18.0".into()));
    }

    #[test]
    fn an_old_engine_is_too_old_and_a_stranger_is_not_tavotto() {
        // 0.17.0 的 /api/version 就长这样：有版本号，没有 features
        let old = http("200 OK", r#"{"build":"index-abc","version":"0.17.0"}"#);
        assert_eq!(judge_version_response(&old), Err(ConnectError::TooOld));
        let other_feature = http("200 OK", r#"{"version":"0.18.0","features":["x"]}"#);
        assert_eq!(
            judge_version_response(&other_feature),
            Err(ConnectError::TooOld)
        );
        for raw in [
            http(
                "404 NOT FOUND",
                r#"{"version":"0.18.0","features":["desktop-remote-window"]}"#,
            ),
            http("403 FORBIDDEN", "bad_host"),
            http("200 OK", "<html>jupyter</html>"),
            http("200 OK", r#"{"features":["desktop-remote-window"]}"#),
            b"SSH-2.0-OpenSSH_9.6\r\n".to_vec(),
            Vec::new(),
        ] {
            assert_eq!(
                judge_version_response(&raw),
                Err(ConnectError::NotTavotto),
                "{}",
                String::from_utf8_lossy(&raw)
            );
        }
    }

    #[test]
    fn probing_a_closed_port_says_unreachable() {
        // 绑一个端口再放掉：拿到的是此刻没人听的端口
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert_eq!(probe(port), Err(ConnectError::Unreachable));
    }

    #[test]
    fn probing_a_live_server_reads_the_whole_reply() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut buf = [0u8; 1024];
            let n = s.read(&mut buf).unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            let body = r#"{"version":"0.18.0","features":["desktop-remote-window"]}"#;
            let _ = write!(s, "HTTP/1.0 200 OK\r\n\r\n{body}");
            req
        });
        assert_eq!(probe(port), Ok("0.18.0".into()));
        let req = server.join().unwrap();
        assert!(req.starts_with("GET /api/version HTTP/1.0\r\n"), "{req}");
        assert!(
            req.contains(&format!("\r\nHost: 127.0.0.1:{port}\r\n")),
            "Host 必须是 127.0.0.1:<port>，否则引擎 403：{req}"
        );
    }

    #[test]
    fn error_codes_are_distinct() {
        let mut codes: Vec<_> = ConnectError::ALL.iter().map(|e| e.code()).collect();
        codes.sort_unstable();
        codes.dedup();
        assert_eq!(codes.len(), ConnectError::ALL.len());
    }

    struct Recorder(std::cell::Cell<u32>);

    impl Navigate for Recorder {
        fn navigate(&self) -> Result<(), ConnectError> {
            self.0.set(self.0.get() + 1);
            Ok(())
        }
    }

    #[test]
    fn a_probe_that_outlives_its_window_is_discarded() {
        let slot = std::sync::Mutex::new(RemoteSlot::default());
        // 第一代窗口发起探测……
        let first = slot.lock().unwrap().open_window();
        // ……探测途中用户关窗、重开
        slot.lock().unwrap().window_gone();
        let second = slot.lock().unwrap().open_window();
        assert_ne!(first, second);

        let nav = Recorder(std::cell::Cell::new(0));
        assert_eq!(
            finish_connect(&slot, first, 5091, &nav),
            Err(ConnectError::BadWindow)
        );
        assert_eq!(nav.0.get(), 0, "作废的结果导航了窗口");
        assert_eq!(
            slot.lock().unwrap().allowed_port(second),
            None,
            "作废的结果放行了端口"
        );

        // 新窗口自己的连接照常
        assert_eq!(finish_connect(&slot, second, 5092, &nav), Ok(()));
        assert_eq!(nav.0.get(), 1);
        assert_eq!(slot.lock().unwrap().allowed_port(second), Some(5092));
        // 旧窗口的导航守卫（若还没销毁）不借新窗口的端口
        assert_eq!(slot.lock().unwrap().allowed_port(first), None);
    }

    #[test]
    fn closing_the_window_revokes_its_port() {
        let slot = std::sync::Mutex::new(RemoteSlot::default());
        let g = slot.lock().unwrap().open_window();
        let nav = Recorder(std::cell::Cell::new(0));
        assert_eq!(finish_connect(&slot, g, 5091, &nav), Ok(()));
        slot.lock().unwrap().window_gone();
        assert_eq!(slot.lock().unwrap().allowed_port(g), None);
        // 关窗之后、重开之前回来的探测：也作废
        assert_eq!(
            finish_connect(&slot, g, 5093, &nav),
            Err(ConnectError::BadWindow)
        );
        assert_eq!(nav.0.get(), 1);
        assert_eq!(slot.lock().unwrap().port, None);
        let next = slot.lock().unwrap().open_window();
        assert_eq!(slot.lock().unwrap().allowed_port(next), None);
    }
}
