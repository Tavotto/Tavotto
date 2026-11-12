//! 来源回退状态机的端到端用例（ADR 0121）：本地假 HTTP 服务提供 latest.json 与假包，包用测试密钥
//! 签名；`UpdaterBuilder` 是真的（mock_app 里装了真插件，公钥来自测试密钥），验签、超时、非 2xx、
//! 清单解析走的都是插件原样的代码，只有网络是假的。
//!
//! 判据的主语：断言的是「**哪个来源被问到了几次**」与「**最终拿到的包是哪个来源的、验过签的**」，
//! 不是某个内部函数返回了什么——所以每条都数服务端的命中。**这里从不调用 `apply`**（真安装会动
//! 磁盘上的应用 / 在 Windows 上起安装器）。

use std::collections::HashMap;
use std::io::Cursor;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use base64::Engine as _;
use serde_json::json;
use tauri::test::{mock_builder, mock_context, noop_assets, MockRuntime};
use tauri_plugin_updater::UpdaterExt;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use url::Url;

use crate::app_update::*;

const TARGET: &str = "test-x86_64";
/// 镜像契约 §6.2 #5：包必须在 `{镜像根}/releases/<version>/` 下
const PKG_PATH: &str = "/releases/9.9.9/pkg";
const PKG: &[u8] = b"the-genuine-installer-bytes-0123456789";
const CN: [Source; 2] = [Source::Mirror, Source::Github];
const WORLD: [Source; 2] = [Source::Github, Source::Mirror];

/// 假服务的一个路由该怎么答。
#[derive(Clone)]
enum Route {
    Ok(Vec<u8>),
    Status(u16),
    /// 收到请求后永不回应（模拟被丢包的来源）
    Hang,
    /// 声明 `declared` 字节，只发 `prefix` 就不再发（模拟下到一半断流）
    Stall {
        prefix: Vec<u8>,
        declared: usize,
    },
    /// 分 `chunks` 段、每段间隔 `gap` 发完 `body`（持续有字节，但总时长远超 check 超时）
    Drip {
        body: Vec<u8>,
        chunks: usize,
        gap: Duration,
    },
    /// 首字节被真实进度回调确认后再发主体；尾字节由测试在试探计时分支跑过后放行。
    TrialStay {
        body: Vec<u8>,
        first_seen: Arc<tokio::sync::Notify>,
        finish: Arc<tokio::sync::Notify>,
        middle_after: Duration,
    },
    /// 立刻关连接
    Reset,
    /// 302 到别处
    Redirect(String),
}

struct Fake {
    base: String,
    hits: Arc<Mutex<HashMap<String, usize>>>,
}

impl Fake {
    fn url(&self, path: &str) -> Url {
        Url::parse(&format!("{}{path}", self.base)).unwrap()
    }
    fn hits(&self, path: &str) -> usize {
        *self.hits.lock().unwrap().get(path).unwrap_or(&0)
    }
}

/// 先 bind 拿到自己的地址，再按地址造路由（清单里的包 url 要指回自己）。
async fn serve(build: impl FnOnce(&str) -> Vec<(&'static str, Route)>) -> Fake {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let routes: HashMap<String, Route> = build(&base)
        .into_iter()
        .map(|(p, r)| (p.to_string(), r))
        .collect();
    let hits: Arc<Mutex<HashMap<String, usize>>> = Default::default();
    let hits2 = hits.clone();
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else {
                return;
            };
            let routes = routes.clone();
            let hits = hits2.clone();
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 1024];
                while !buf.windows(4).any(|w| w == b"\r\n\r\n") {
                    match sock.read(&mut chunk).await {
                        Ok(0) | Err(_) => return,
                        Ok(n) => buf.extend_from_slice(&chunk[..n]),
                    }
                }
                let head = String::from_utf8_lossy(&buf).to_string();
                let path = head
                    .split_whitespace()
                    .nth(1)
                    .unwrap_or("/")
                    .split('?')
                    .next()
                    .unwrap()
                    .to_string();
                *hits.lock().unwrap().entry(path.clone()).or_default() += 1;
                match routes.get(&path).cloned().unwrap_or(Route::Status(404)) {
                    Route::Ok(body) => {
                        let h = format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                        let _ = sock.write_all(&body).await;
                    }
                    Route::Status(code) => {
                        let h = format!(
                            "HTTP/1.1 {code} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                    }
                    Route::Hang => tokio::time::sleep(Duration::from_secs(3600)).await,
                    Route::Stall { prefix, declared } => {
                        let h = format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {declared}\r\nConnection: close\r\n\r\n"
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                        let _ = sock.write_all(&prefix).await;
                        let _ = sock.flush().await;
                        tokio::time::sleep(Duration::from_secs(3600)).await;
                    }
                    Route::Drip { body, chunks, gap } => {
                        let h = format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                        let step = body.len().div_ceil(chunks);
                        for part in body.chunks(step) {
                            let _ = sock.write_all(part).await;
                            let _ = sock.flush().await;
                            tokio::time::sleep(gap).await;
                        }
                    }
                    Route::TrialStay {
                        body,
                        first_seen,
                        finish,
                        middle_after,
                    } => {
                        let h = format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                        let _ = sock.write_all(&body[..1]).await;
                        let _ = sock.flush().await;
                        // 不能让 TCP 合并首块与主体：生产量速会扣掉整个 first_chunk。
                        first_seen.notified().await;
                        tokio::time::sleep(middle_after).await;
                        let _ = sock.write_all(&body[1..body.len() - 1]).await;
                        let _ = sock.flush().await;
                        finish.notified().await;
                        let _ = sock.write_all(&body[body.len() - 1..]).await;
                    }
                    Route::Reset => drop(sock),
                    Route::Redirect(to) => {
                        let h = format!(
                            "HTTP/1.1 302 Found\r\nLocation: {to}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        );
                        let _ = sock.write_all(h.as_bytes()).await;
                    }
                }
            });
        }
    });
    Fake { base, hits }
}

struct Keys {
    kp: minisign::KeyPair,
}

impl Keys {
    fn new() -> Self {
        Self {
            kp: minisign::KeyPair::generate_unencrypted_keypair().unwrap(),
        }
    }
    fn pubkey_b64(&self) -> String {
        let s = self.kp.pk.to_box().unwrap().to_string();
        base64::engine::general_purpose::STANDARD.encode(s)
    }
    fn sign_b64(&self, data: &[u8]) -> String {
        let sig = minisign::sign(
            Some(&self.kp.pk),
            &self.kp.sk,
            Cursor::new(data),
            Some("trusted"),
            Some("untrusted"),
        )
        .unwrap();
        base64::engine::general_purpose::STANDARD.encode(sig.to_string())
    }
}

/// 一份 latest.json：`pkg_url` 指向某个包，`sig` 是该包的签名。
fn manifest(version: &str, pkg_url: &str, sig: &str) -> Route {
    Route::Ok(
        json!({
            "version": version,
            "notes": "n",
            "pub_date": "2026-10-12T00:00:00Z",
            "platforms": { TARGET: { "url": pkg_url, "signature": sig } },
        })
        .to_string()
        .into_bytes(),
    )
}

/// 健康来源：清单（版本 `version`、包签名 `sig`）+ 自己的 /pkg。
fn healthy(base: &str, version: &str, sig: &str, pkg: &[u8]) -> Vec<(&'static str, Route)> {
    vec![
        (
            "/latest.json",
            manifest(version, &format!("{base}/releases/{version}/pkg"), sig),
        ),
        (
            Box::leak(format!("/releases/{version}/pkg").into_boxed_str()),
            Route::Ok(pkg.to_vec()),
        ),
    ]
}

fn limits() -> Limits {
    Limits {
        check: Duration::from_millis(600),
        connect: Duration::from_millis(600),
        stall: Duration::from_millis(500),
        // 用例里的包只有几十字节：默认判不出「慢」，专测慢的用例自己换一套
        slow: SlowRule {
            // 比 Drip 用例的段间隔（250 ms）长：第二段到之前量不出速度
            grace: Duration::from_millis(600),
            // 比 Drip 用例的段间隔（250 ms）宽，不把两段之间的空档量成 0 速
            window: Duration::from_millis(600),
            eta: Duration::from_secs(5),
            min_rate: 1,
            probe: Duration::from_millis(300),
        },
        trial: TrialRule {
            wait: Duration::from_millis(400),
            eta: Duration::from_secs(5),
            min_rate: 1,
        },
    }
}

fn app(keys: &Keys) -> tauri::App<MockRuntime> {
    let mut ctx = mock_context(noop_assets());
    ctx.config_mut().plugins.0.insert(
        "updater".into(),
        json!({
            "endpoints": ["https://example.invalid/latest.json"],
            "pubkey": keys.pubkey_b64(),
            "dangerousInsecureTransportProtocol": true,
        }),
    );
    mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(ctx)
        .unwrap()
}

struct Rig {
    keys: Keys,
    mirror: Fake,
    github: Fake,
    /// 边缘节点：默认是个什么都没有的服务（清单 404 = 节点关闭）；用例用 `with_edge` 开张
    edge: Fake,
    app: tauri::App<MockRuntime>,
    cool: Cooldown,
}

impl Rig {
    async fn new(
        keys: Keys,
        mirror: impl FnOnce(&str, &Keys) -> Vec<(&'static str, Route)>,
        github: impl FnOnce(&str, &Keys) -> Vec<(&'static str, Route)>,
    ) -> Self {
        let mirror = serve(|b| mirror(b, &keys)).await;
        let github = serve(|b| github(b, &keys)).await;
        let edge = serve(|_| Vec::new()).await;
        Self {
            app: app(&keys),
            keys,
            mirror,
            github,
            edge,
            cool: Cooldown::default(),
        }
    }
    async fn with_edge(
        mut self,
        edge: impl FnOnce(&str, &Keys) -> Vec<(&'static str, Route)>,
    ) -> Self {
        self.edge = serve(|b| edge(b, &self.keys)).await;
        self
    }
    /// 两边都健康，版本相同
    async fn both_healthy() -> Self {
        Self::new(
            Keys::new(),
            |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
            |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        )
        .await
    }
    fn endpoints(&self) -> Endpoints {
        Endpoints {
            mirror: self.mirror.url("/latest.json"),
            mirror_root: self.mirror.base.clone(),
            github: self.github.url("/latest.json"),
            edge: self.edge.url("/latest.json"),
            edge_root: self.edge.base.clone(),
        }
    }
    fn make(&self) -> impl Fn() -> tauri_plugin_updater::UpdaterBuilder {
        let h = self.app.handle().clone();
        move || h.updater_builder().target(TARGET)
    }
    async fn check(&self, order: &[Source]) -> Result<Option<Pending>, Failure> {
        check(
            &self.make(),
            &self.endpoints(),
            order,
            &limits(),
            &self.cool,
        )
        .await
    }
    async fn fetch(&self, pending: &Pending) -> (Result<Fetched, Failure>, Vec<DownloadEvent>) {
        self.fetch_with(pending, &limits()).await
    }
    async fn fetch_with(
        &self,
        pending: &Pending,
        limits: &Limits,
    ) -> (Result<Fetched, Failure>, Vec<DownloadEvent>) {
        let log: Arc<Mutex<Vec<DownloadEvent>>> = Default::default();
        let l2 = log.clone();
        let emit = move |e: DownloadEvent| l2.lock().unwrap().push(e);
        let r = fetch(
            &self.make(),
            &self.endpoints(),
            limits,
            pending,
            &Plan::from_pending(pending),
            &emit,
            &self.cool,
        )
        .await;
        let events = log.lock().unwrap().clone();
        (r, events)
    }
}

fn kinds(f: &Failure) -> Vec<(Source, Stage, Kind)> {
    f.attempts
        .iter()
        .map(|a| (a.source, a.stage, a.kind))
        .collect()
}

/* ------------------------------- check ----------------------------------- */

#[tokio::test(flavor = "multi_thread")]
async fn the_first_source_in_order_answers_and_the_other_is_never_asked() {
    for (order, want, other_is_mirror) in
        [(CN, Source::Mirror, false), (WORLD, Source::Github, true)]
    {
        let rig = Rig::both_healthy().await;
        let p = rig.check(&order).await.unwrap().expect("有更新");
        assert_eq!(p.source, want);
        let (asked, untouched) = if other_is_mirror {
            (&rig.github, &rig.mirror)
        } else {
            (&rig.mirror, &rig.github)
        };
        assert_eq!(asked.hits("/latest.json"), 1);
        assert_eq!(
            untouched.hits("/latest.json"),
            0,
            "第一个来源答上来就不问另一个"
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn no_update_from_the_first_answering_source_is_final() {
    // 镜像滞后：它说「没有更新」（版本不高于当前），GitHub 其实有新的 9.9.9。权衡见 ADR 0121 §3：
    // 不再问 GitHub——否则大陆用户每次启动都要多等一个必超时的 GitHub，且多一次出网。
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "0.0.0", &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await;
    assert!(rig.check(&CN).await.unwrap().is_none());
    assert_eq!(rig.mirror.hits("/latest.json"), 1);
    assert_eq!(rig.github.hits("/latest.json"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_source_that_cannot_answer_falls_through_to_the_next_with_a_classified_attempt() {
    let broken: Vec<(&str, Route, Kind)> = vec![
        ("5xx", Route::Status(503), Kind::Http),
        ("404", Route::Status(404), Kind::Http),
        ("坏 JSON", Route::Ok(b"<html>not json</html>".to_vec()), Kind::BadManifest),
        ("丢包", Route::Hang, Kind::Timeout),
        ("断连", Route::Reset, Kind::Network),
        (
            "缺本平台",
            Route::Ok(
                json!({"version":"9.9.9","platforms":{"other-os":{"url":"http://x/p","signature":"s"}}})
                    .to_string()
                    .into_bytes(),
            ),
            Kind::BadManifest,
        ),
    ];
    for (name, route, kind) in broken {
        // 1) 后备健康：回退到 GitHub，镜像恰好被问一次
        let r2 = route.clone();
        let rig = Rig::new(
            Keys::new(),
            move |_, _| vec![("/latest.json", r2)],
            |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        )
        .await;
        let p = rig
            .check(&CN)
            .await
            .unwrap()
            .unwrap_or_else(|| panic!("{name}: 应回退到有更新"));
        assert_eq!(p.source, Source::Github, "{name}");
        assert_eq!(rig.mirror.hits("/latest.json"), 1, "{name}");
        assert_eq!(rig.github.hits("/latest.json"), 1, "{name}");

        // 2) 后备也坏：全部失败，逐来源记录原因，顺序 = 尝试顺序
        let r3 = route.clone();
        let rig = Rig::new(
            Keys::new(),
            move |_, _| vec![("/latest.json", r3)],
            |_, _| vec![("/latest.json", Route::Status(500))],
        )
        .await;
        let f = rig
            .check(&CN)
            .await
            .err()
            .unwrap_or_else(|| panic!("{name}: 应失败"));
        assert_eq!(f.code, "all_sources_failed", "{name}");
        assert_eq!(
            kinds(&f),
            vec![
                (Source::Mirror, Stage::Check, kind),
                (Source::Github, Stage::Check, Kind::Http)
            ],
            "{name}"
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pinned_single_source_never_falls_back() {
    // e2e-update 的 TAVOTTO_E2E_UPDATE_SOURCE：钉死单来源，坏了就是坏了
    let rig = Rig::new(
        Keys::new(),
        |_, _| vec![("/latest.json", Route::Status(500))],
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await;
    let f = rig.check(&[Source::Mirror]).await.err().unwrap();
    assert_eq!(kinds(&f), vec![(Source::Mirror, Stage::Check, Kind::Http)]);
    assert_eq!(rig.github.hits("/latest.json"), 0);
}

/* ------------------------------ download --------------------------------- */

#[tokio::test(flavor = "multi_thread")]
async fn a_healthy_download_streams_events_and_returns_the_verified_bytes() {
    let rig = Rig::both_healthy().await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let (r, events) = rig.fetch(&p).await;
    let f = r.unwrap();
    assert_eq!(f.source, Source::Mirror);
    assert_eq!(f.bytes, PKG);
    assert_eq!(f.update.version, "9.9.9");
    assert_eq!(rig.github.hits(PKG_PATH), 0);
    assert!(
        matches!(events.first(), Some(DownloadEvent::Started { content_length: Some(n) }) if *n as usize == PKG.len())
    );
    let got: usize = events
        .iter()
        .filter_map(|e| match e {
            DownloadEvent::Progress { chunk_length } => Some(*chunk_length),
            _ => None,
        })
        .sum();
    assert_eq!(got, PKG.len());
    assert!(matches!(events.last(), Some(DownloadEvent::Finished)));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_download_that_fails_falls_back_and_re_checks_the_other_source() {
    let tampered = b"tampered-by-a-bad-mirror-XXXXXXXXXXXXXXXXX".to_vec();
    let cases: Vec<(&str, Route, Kind)> = vec![
        ("非 2xx", Route::Status(502), Kind::Http),
        ("断连", Route::Reset, Kind::Network),
        ("验签不过", Route::Ok(tampered), Kind::Signature),
        (
            "停滞",
            Route::Stall {
                prefix: PKG[..5].to_vec(),
                declared: PKG.len(),
            },
            Kind::Stalled,
        ),
    ];
    for (name, bad_pkg, kind) in cases {
        let rig = Rig::new(
            Keys::new(),
            move |b, k| {
                vec![
                    (
                        "/latest.json",
                        manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                    ),
                    (PKG_PATH, bad_pkg),
                ]
            },
            |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        )
        .await;
        let p = rig.check(&CN).await.unwrap().unwrap();
        assert_eq!(p.source, Source::Mirror, "{name}");
        assert_eq!(
            rig.github.hits("/latest.json"),
            0,
            "{name}: check 阶段没碰 GitHub"
        );

        let t = Instant::now();
        let (r, events) = rig.fetch(&p).await;
        let f = r.unwrap_or_else(|e| panic!("{name}: 应回退成功，实得 {e:?}"));
        assert_eq!(f.source, Source::Github, "{name}");
        assert_eq!(f.bytes, PKG, "{name}: 拿到的是验过签的真包");
        assert_eq!(rig.mirror.hits(PKG_PATH), 1, "{name}");
        assert_eq!(
            rig.github.hits("/latest.json"),
            1,
            "{name}: 回退来源重新 check"
        );
        assert_eq!(rig.github.hits(PKG_PATH), 1, "{name}");
        assert!(
            t.elapsed() < Duration::from_secs(10),
            "{name}: 不得卡在坏来源上"
        );
        let starts = events
            .iter()
            .filter(|e| matches!(e, DownloadEvent::Started { .. }))
            .count();
        assert!(starts >= 1, "{name}");
        assert!(
            matches!(events.last(), Some(DownloadEvent::Finished)),
            "{name}"
        );
        let _ = kind;
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn download_failure_kinds_are_reported_per_source_when_everything_fails() {
    let tampered = b"tampered-by-a-bad-mirror-XXXXXXXXXXXXXXXXX".to_vec();
    let cases: Vec<(&str, Route, Kind)> = vec![
        ("非 2xx", Route::Status(502), Kind::Http),
        ("断连", Route::Reset, Kind::Network),
        ("验签不过", Route::Ok(tampered), Kind::Signature),
        (
            "停滞",
            Route::Stall {
                prefix: PKG[..5].to_vec(),
                declared: PKG.len(),
            },
            Kind::Stalled,
        ),
    ];
    for (name, bad, kind) in cases {
        let bad2 = bad.clone();
        let rig = Rig::new(
            Keys::new(),
            move |b, k| {
                vec![
                    (
                        "/latest.json",
                        manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                    ),
                    (PKG_PATH, bad),
                ]
            },
            move |b, k| {
                vec![
                    (
                        "/latest.json",
                        manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                    ),
                    (PKG_PATH, bad2),
                ]
            },
        )
        .await;
        let p = rig.check(&CN).await.unwrap().unwrap();
        let (r, _) = rig.fetch(&p).await;
        let f = r.err().unwrap_or_else(|| panic!("{name}: 应失败"));
        assert_eq!(f.code, "all_sources_failed", "{name}");
        assert_eq!(
            kinds(&f),
            vec![
                (Source::Mirror, Stage::Download, kind),
                (Source::Github, Stage::Download, kind)
            ],
            "{name}"
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_manifest_signed_by_another_key_is_rejected_whatever_the_source() {
    // 信任不随来源变：镜像清单里的签名若不是内置公钥签的，包再「真」也不收
    let other = Keys::new();
    let rig = Rig::new(
        Keys::new(),
        move |b, _| healthy(b, "9.9.9", &other.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let (r, _) = rig.fetch(&p).await;
    let f = r.unwrap();
    assert_eq!(
        f.source,
        Source::Github,
        "镜像的包验不过，落到用内置公钥验得过的 GitHub"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn the_fallback_source_must_offer_the_same_version_the_user_saw() {
    // 用户看到并确认的是镜像报的 9.9.9；镜像包下不动，GitHub 此刻是 9.9.10（或已无更新）→ 不悄悄装别的版本
    for (gh_version, name) in [("9.9.10", "更新的版本"), ("0.0.0", "无更新")] {
        let rig = Rig::new(
            Keys::new(),
            |b, k| {
                vec![
                    (
                        "/latest.json",
                        manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                    ),
                    (PKG_PATH, Route::Status(502)),
                ]
            },
            move |b, k| healthy(b, gh_version, &k.sign_b64(PKG), PKG),
        )
        .await;
        let p = rig.check(&CN).await.unwrap().unwrap();
        let (r, _) = rig.fetch(&p).await;
        let f = r.err().unwrap_or_else(|| panic!("{name}: 应拒绝"));
        assert_eq!(
            kinds(&f),
            vec![
                (Source::Mirror, Stage::Download, Kind::Http),
                (Source::Github, Stage::Check, Kind::VersionMismatch)
            ],
            "{name}"
        );
        assert_eq!(rig.github.hits(PKG_PATH), 0, "{name}: 版本不符就不下载");
    }
}

/* ------------------------------- 命令层 ---------------------------------- */

#[tokio::test(flavor = "multi_thread")]
async fn run_check_and_run_install_guard_their_state() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();

    // 没查过就装：稳定 code，不偷偷补一次 check
    let e = run_install(&make, &ep, &state, &limits(), &|_| {})
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "no_pending_update");
    assert_eq!(
        rig.mirror.hits("/latest.json") + rig.github.hits("/latest.json"),
        0
    );

    // 查到：交出版本 / 说明 / RFC 3339 日期 / 来源，并留下句柄
    let c = run_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .unwrap();
    assert_eq!((c.version.as_str(), c.source), ("9.9.9", Source::Mirror));
    assert_eq!(c.notes.as_deref(), Some("n"));
    assert_eq!(c.date.as_deref(), Some("2026-10-12T00:00:00Z"));
    assert!(state.pending.try_lock().unwrap().is_some());

    // 再查一次但这次所有来源都挂了：旧句柄不能留着（否则界面说失败、按钮却还能装旧的）
    let dead = Endpoints {
        mirror: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        mirror_root: "http://127.0.0.1:9".into(),
        github: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge_root: "http://127.0.0.1:9".into(),
    };
    assert!(run_check(&make, &dead, &state, &CN, &limits())
        .await
        .is_err());
    assert!(state.pending.try_lock().unwrap().is_none());

    // 同一时刻只许一个在跑
    let _held = state.pending.try_lock().unwrap();
    let e = run_check(&make, &ep, &state, &CN, &limits())
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "busy");
}

#[tokio::test(flavor = "multi_thread")]
async fn an_install_that_names_another_version_than_the_candidate_is_refused_before_any_download() {
    // 救援页（rescue.rs）：用户确认的是检查到的 9.9.9；句柄里若已是别的版本（期间又 check 过），
    // 绝不能装成另一个。核对在下载之前、且不动句柄（用户可以重新确认）。从不走到 `apply`。
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    let c = run_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(c.version, "9.9.9");
    for wrong in ["9.9.8", "9.9.10", "", "9.9.9 "] {
        let e = run_install_expecting(&make, &ep, &state, &limits(), Some(wrong), &|_| {})
            .await
            .err()
            .unwrap_or_else(|| panic!("{wrong:?} 应被拒绝"));
        assert_eq!(e.code, "candidate_changed", "{wrong:?}");
    }
    assert_eq!(rig.mirror.hits(PKG_PATH) + rig.github.hits(PKG_PATH), 0);
    assert!(
        state.pending.try_lock().unwrap().is_some(),
        "拒绝不清句柄：用户可以对着同一个候选重新确认"
    );
    // 与旧入口共用同一把锁：有人持有时一样回 busy，而不是各自并发安装
    let _held = state.pending.try_lock().unwrap();
    let e = run_install_expecting(&make, &ep, &state, &limits(), Some("9.9.9"), &|_| {})
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "busy");
}

/* --------------------- 救援页「重新安装」：允许同版本 ------------------- */

/// 当前版本 ± 1 的补丁号（mock 应用是 0.x.y 形态，补丁号一定能加减）
fn bump(v: &str, delta: i64) -> String {
    let mut p: Vec<i64> = v.split('.').map(|x| x.parse().unwrap()).collect();
    p[2] += delta;
    format!("{}.{}.{}", p[0], p[1], p[2])
}

#[tokio::test(flavor = "multi_thread")]
async fn the_plain_check_refuses_the_installed_version_but_the_reinstall_check_takes_it() {
    let keys = Keys::new();
    let probe = app(&keys);
    let cur = probe.package_info().version.to_string();
    drop(probe);
    let pkg_path: &'static str = Box::leak(format!("/releases/{cur}/pkg").into_boxed_str());
    let mk = |cur: String| move |b: &str, k: &Keys| healthy(b, &cur, &k.sign_b64(PKG), PKG);
    let rig = Rig::new(keys, mk(cur.clone()), mk(cur.clone())).await;
    let make = rig.make();
    let ep = rig.endpoints();

    // 普通更新路径：同版本 = 没有更新（语义不变）
    let state = UpdateState::default();
    assert!(run_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .is_none());
    assert!(state.pending.try_lock().unwrap().is_none());

    // 重新安装：同版本可装，留下候选句柄，且能下载并验签（不 apply）
    let c = run_reinstall_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap();
    assert_eq!(
        (c.version.as_str(), c.source),
        (cur.as_str(), Source::Mirror)
    );
    let pending = state.pending.try_lock().unwrap().clone().unwrap();
    let reinstall_make = reinstall_builder(&make);
    let bytes = fetch(
        &reinstall_make,
        &ep,
        &limits(),
        &pending,
        &Plan::from_pending(&pending),
        &|_| {},
        &Cooldown::default(),
    )
    .await
    .unwrap_or_else(|e| panic!("同版本包应能下载并验签：{e:?}"));
    assert_eq!(bytes.bytes, PKG);
    assert_eq!(rig.mirror.hits(pkg_path), 1);

    // 同版本放行不外溢：普通 check 之后仍然是「无更新」
    assert!(run_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_newer_manifest_is_fine_for_reinstall_and_an_older_one_is_an_honest_failure() {
    let keys = Keys::new();
    let probe = app(&keys);
    let cur = probe.package_info().version.to_string();
    drop(probe);

    // 清单比当前新：装更新版本同样能修好
    let newer = bump(&cur, 1);
    let n2 = newer.clone();
    let rig = Rig::new(
        keys,
        move |b, k| healthy(b, &n2, &k.sign_b64(PKG), PKG),
        |_, _| vec![],
    )
    .await;
    let state = UpdateState::default();
    let c = run_reinstall_check(&rig.make(), &rig.endpoints(), &state, &CN, &limits())
        .await
        .unwrap();
    assert_eq!(c.version, newer);

    // 清单比当前旧：不降级，稳定码 reinstall_unavailable；第一个答上来的来源即终局，GitHub 不被问
    let keys = Keys::new();
    let older = bump(&cur, -1);
    if older.split('.').any(|p| p.starts_with('-')) {
        return; // mock 版本是 0.0.0 一类时无法造出更旧的版本
    }
    let o2 = older.clone();
    let rig = Rig::new(
        keys,
        move |b, k| healthy(b, &o2, &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "99.0.0", &k.sign_b64(PKG), PKG),
    )
    .await;
    let state = UpdateState::default();
    let e = run_reinstall_check(&rig.make(), &rig.endpoints(), &state, &CN, &limits())
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "reinstall_unavailable");
    assert_eq!(rig.github.hits("/latest.json"), 0, "首个来源说无可装即终局");

    // 来源全答不上来：如实失败（all_sources_failed），不是 reinstall_unavailable
    let dead = Endpoints {
        mirror: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        mirror_root: "http://127.0.0.1:9".into(),
        github: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge_root: "http://127.0.0.1:9".into(),
    };
    let e = run_reinstall_check(&rig.make(), &dead, &state, &CN, &limits())
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "all_sources_failed");
}

/* --------------------- infra 契约 §6.2（镜像清单的取舍） ------------------- */

#[tokio::test(flavor = "multi_thread")]
async fn a_mirror_package_outside_the_mirror_release_dir_is_not_followed() {
    // 清单把包放在别的主机 / 别的版本目录：签名能挡住篡改，但不该用镜像清单去访问非镜像地址
    for (name, url_of) in [
        (
            "别的主机",
            Box::new(|_b: &str| "http://127.0.0.1:9/releases/9.9.9/pkg".to_string())
                as Box<dyn Fn(&str) -> String>,
        ),
        (
            "别的版本目录",
            Box::new(|b: &str| format!("{b}/releases/9.9.8/pkg")),
        ),
    ] {
        let rig = Rig::new(
            Keys::new(),
            move |b, k| {
                vec![
                    (
                        "/latest.json",
                        manifest("9.9.9", &url_of(b), &k.sign_b64(PKG)),
                    ),
                    ("/releases/9.9.8/pkg", Route::Ok(PKG.to_vec())),
                ]
            },
            |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        )
        .await;
        let p = rig.check(&CN).await.unwrap().unwrap();
        assert_eq!(p.source, Source::Github, "{name}");
        assert_eq!(rig.mirror.hits("/releases/9.9.8/pkg"), 0, "{name}");
        assert!(rig.cool.active(), "{name}: 镜像出过问题 → 进入冷却");
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_mirror_redirect_to_another_host_is_not_followed() {
    let github = serve(|b| healthy(b, "9.9.9", &Keys::new().sign_b64(PKG), PKG)).await;
    let target = github.url("/latest.json").to_string();
    let rig = Rig::new(
        Keys::new(),
        move |_, _| vec![("/latest.json", Route::Redirect(target))],
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await;
    let f = rig.check(&[Source::Mirror]).await.err().unwrap();
    assert_eq!(kinds(&f), vec![(Source::Mirror, Stage::Check, Kind::Http)]);
    assert_eq!(github.hits("/latest.json"), 0, "重定向没有被跟随");
}

#[tokio::test(flavor = "multi_thread")]
async fn after_the_mirror_fails_the_cooldown_demotes_it_and_the_same_update_never_retries_it() {
    // 镜像 check 就挂了 → 冷却；GitHub 查到更新，但 GitHub 的包下不动：不回头再试镜像（契约 §6.3）
    let rig = Rig::new(
        Keys::new(),
        |_, _| vec![("/latest.json", Route::Status(503))],
        |b, k| {
            vec![
                (
                    "/latest.json",
                    manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                ),
                (PKG_PATH, Route::Status(502)),
            ]
        },
    )
    .await;
    assert!(!rig.cool.active());
    let p = rig.check(&CN).await.unwrap().unwrap();
    assert_eq!(p.source, Source::Github);
    assert!(rig.cool.active());
    let hits_before = rig.mirror.hits("/latest.json");
    let (r, _) = rig.fetch(&p).await;
    assert_eq!(
        kinds(&r.err().unwrap()),
        vec![(Source::Github, Stage::Download, Kind::Http)]
    );
    assert_eq!(
        rig.mirror.hits("/latest.json"),
        hits_before,
        "同一次更新不再回头试镜像"
    );
    // 冷却中，下一次的顺序里镜像被挪到最后
    assert_eq!(rig.cool.apply(&CN), vec![Source::Github, Source::Mirror]);
    assert_eq!(rig.cool.apply(&WORLD), vec![Source::Github, Source::Mirror]);
    assert_eq!(
        rig.cool.apply(&[Source::Mirror]),
        vec![Source::Mirror],
        "钉死的单来源不动"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_download_longer_than_the_check_timeout_still_succeeds_while_bytes_keep_flowing() {
    // check 超时 600 ms、停滞超时 500 ms；包分 6 段、每段隔 250 ms 发完（≈1.5 s）：总时长是 check 超时的 2.5 倍，
    // 但任意两段之间都没有停滞。下载不得被 check 的总超时掐断（Codex #948 P1）。
    let rig = Rig::new(
        Keys::new(),
        |b, k| {
            vec![
                (
                    "/latest.json",
                    manifest("9.9.9", &format!("{b}{PKG_PATH}"), &k.sign_b64(PKG)),
                ),
                (
                    PKG_PATH,
                    Route::Drip {
                        body: PKG.to_vec(),
                        chunks: 6,
                        gap: Duration::from_millis(250),
                    },
                ),
            ]
        },
        |_, _| vec![("/latest.json", Route::Status(500))],
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let t = Instant::now();
    let (r, _) = rig.fetch(&p).await;
    let f = r.unwrap_or_else(|e| panic!("持续有字节的慢下载不该失败：{e:?}"));
    assert_eq!((f.source, f.bytes.as_slice()), (Source::Mirror, PKG));
    assert!(
        t.elapsed() > limits().check,
        "用例本身要比 check 超时长才有意义：{:?}",
        t.elapsed()
    );
    assert_eq!(rig.github.hits("/latest.json"), 0);
}

/* ---------------------------- 下载慢：试探另一个来源 ---------------------------- */

/// 慢用例的包：几 KB，按 `Drip` 发要好几秒
fn big_pkg() -> Vec<u8> {
    (0..6000u32).map(|i| (i % 251) as u8).collect()
}

/// 3 s 发完（150 段 × 20 ms）：按 1 s 的 `eta` 判是慢的；段要细，比速才不被段的颗粒度带偏
fn slow_drip(body: &[u8]) -> Route {
    Route::Drip {
        body: body.to_vec(),
        chunks: 150,
        gap: Duration::from_millis(20),
    }
}

fn slow_limits() -> Limits {
    Limits {
        slow: SlowRule {
            eta: Duration::from_secs(1),
            probe: Duration::from_millis(800),
            ..limits().slow
        },
        ..limits()
    }
}

fn source_with(base: &str, version: &str, sig: &str, pkg: Route) -> Vec<(&'static str, Route)> {
    vec![
        (
            "/latest.json",
            manifest(version, &format!("{base}/releases/{version}/pkg"), sig),
        ),
        (
            Box::leak(format!("/releases/{version}/pkg").into_boxed_str()),
            pkg,
        ),
    ]
}

fn starts(events: &[DownloadEvent]) -> usize {
    events
        .iter()
        .filter(|e| matches!(e, DownloadEvent::Started { .. }))
        .count()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_download_switches_to_the_other_source_when_it_is_measurably_faster() {
    // 镜像在发、只是慢（3 s）；GitHub 快三倍多（0.9 s，比 0.8 s 的试探窗口长）：判慢后试探 GitHub，
    // 量满一个窗口比速 → 换过去，镜像那一路丢掉
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), slow_drip(&p1)),
        move |b, k| {
            let fast = Route::Drip {
                body: p2.clone(),
                chunks: 60,
                gap: Duration::from_millis(15),
            };
            source_with(b, "9.9.9", &k.sign_b64(&p2), fast)
        },
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    assert_eq!(p.source, Source::Mirror);
    let t = Instant::now();
    let (r, events) = rig.fetch_with(&p, &slow_limits()).await;
    let f = r.unwrap_or_else(|e| panic!("该换到 GitHub 下完：{e:?}"));
    assert_eq!(
        (f.source, f.bytes.as_slice()),
        (Source::Github, pkg.as_slice())
    );
    assert!(
        // 镜像自己要 3 s 才下完
        t.elapsed() < Duration::from_millis(2600),
        "没等慢来源下完：{:?}",
        t.elapsed()
    );
    assert_eq!(
        rig.github.hits("/latest.json"),
        1,
        "换之前重新 check 一次（同版本）"
    );
    assert_eq!(starts(&events), 2, "换来源时前端进度归零一次");
    assert!(
        matches!(events.last(), Some(DownloadEvent::Finished)),
        "最后交给前端的是 Finished：{events:?}"
    );
    assert!(!rig.cool.active(), "慢不是故障，不让镜像进冷却");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_download_takes_the_other_source_if_it_finishes_within_the_probe() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), slow_drip(&p1)),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), Route::Ok(p2.clone())),
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let (r, events) = rig.fetch_with(&p, &slow_limits()).await;
    let f = r.unwrap_or_else(|e| panic!("该用 GitHub 下完的包：{e:?}"));
    assert_eq!(
        (f.source, f.bytes.as_slice()),
        (Source::Github, pkg.as_slice())
    );
    assert_eq!(starts(&events), 2);
    assert!(
        matches!(events.last(), Some(DownloadEvent::Finished)),
        "{events:?}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_download_stays_put_when_the_other_source_is_no_faster() {
    // 两边一样慢：试探过 GitHub，但从头下不比镜像剩下的快 → 丢掉试探那一路，镜像下完
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), slow_drip(&p1)),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let (r, events) = rig.fetch_with(&p, &slow_limits()).await;
    let f = r.unwrap_or_else(|e| panic!("该留在镜像下完：{e:?}"));
    assert_eq!(
        (f.source, f.bytes.as_slice()),
        (Source::Mirror, pkg.as_slice())
    );
    assert_eq!(
        rig.github.hits("/releases/9.9.9/pkg"),
        1,
        "试探过 GitHub 一次，之后不再试探"
    );
    assert_eq!(starts(&events), 1, "没换来源，进度不归零");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_download_never_switches_to_a_source_offering_another_version() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), slow_drip(&p1)),
        move |b, k| source_with(b, "9.9.10", &k.sign_b64(&p2), Route::Ok(p2.clone())),
    )
    .await;
    let p = rig.check(&CN).await.unwrap().unwrap();
    let (r, _) = rig.fetch_with(&p, &slow_limits()).await;
    let f = r.unwrap_or_else(|e| panic!("该留在镜像下完：{e:?}"));
    assert_eq!(
        (f.source, f.update.version.as_str()),
        (Source::Mirror, "9.9.9")
    );
    assert_eq!(rig.github.hits("/releases/9.9.10/pkg"), 0, "别的版本不下载");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pinned_single_source_is_never_probed_even_when_slow() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), slow_drip(&p1)),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), Route::Ok(p2.clone())),
    )
    .await;
    let p = rig.check(&[Source::Mirror]).await.unwrap().unwrap();
    let (r, _) = rig.fetch_with(&p, &slow_limits()).await;
    assert_eq!(r.unwrap().source, Source::Mirror);
    assert_eq!(rig.github.hits("/latest.json"), 0);
}

/* ------------- 后台下载（ADR 0121 修订 2026-11-04）：下好 → 槽 → 装 ------------- */

/// 一个「装」的替身：记下收到的包与来源，**不碰磁盘上的应用**。
type Installed = Arc<Mutex<Vec<(Source, Vec<u8>)>>>;

fn recorder() -> (Installed, impl FnOnce(Fetched) -> Result<Source, Failure>) {
    let seen: Installed = Default::default();
    let s2 = seen.clone();
    (seen, move |f: Fetched| {
        s2.lock().unwrap().push((f.source, f.bytes));
        Ok(f.source)
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn a_background_download_fills_the_slot_and_install_uses_it_without_asking_for_the_package_again(
) {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .unwrap();
    assert!(state.ready.lock().unwrap().is_none());

    let d = run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!((d.version.as_str(), d.source), ("9.9.9", Source::Github));
    assert_eq!(rig.github.hits(PKG_PATH), 1);
    {
        let ready = state.ready.lock().unwrap();
        let f = ready.as_ref().expect("下好的包进槽");
        assert_eq!(
            (f.update.version.as_str(), f.bytes.as_slice()),
            ("9.9.9", PKG)
        );
    }

    // 再下一次同一版本：直接回，不再请求包地址
    run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(rig.github.hits(PKG_PATH), 1, "槽里已有同版本的包，不重下");

    // 装：取槽里的包交给「装」，包地址一次都不再被请求，槽被取空
    let pending = state.pending.try_lock().unwrap().clone().unwrap();
    let (seen, install) = recorder();
    let events: Arc<Mutex<Vec<DownloadEvent>>> = Default::default();
    let e2 = events.clone();
    let emit = move |e: DownloadEvent| e2.lock().unwrap().push(e);
    let src = install_pending(&make, &ep, &state, &limits(), &pending, &emit, install)
        .await
        .unwrap();
    assert_eq!(src, Source::Github);
    assert_eq!(
        seen.lock().unwrap().as_slice(),
        [(Source::Github, PKG.to_vec())]
    );
    assert_eq!(rig.github.hits(PKG_PATH), 1, "装的时候没有再下载");
    assert_eq!(rig.mirror.hits(PKG_PATH), 0);
    assert!(events.lock().unwrap().is_empty(), "槽里的包没有下载事件");
    assert!(state.ready.lock().unwrap().is_none(), "装过的包被取走");
}

#[tokio::test(flavor = "multi_thread")]
async fn install_downloads_normally_when_the_slot_is_empty() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    let pending = state.pending.try_lock().unwrap().clone().unwrap();
    let (seen, install) = recorder();
    install_pending(&make, &ep, &state, &limits(), &pending, &|_| {}, install)
        .await
        .unwrap();
    assert_eq!(seen.lock().unwrap().len(), 1);
    assert_eq!(
        rig.github.hits(PKG_PATH),
        1,
        "槽空时照旧现下现装（大陆环境手动也先试 GitHub）"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slot_holding_another_version_than_the_candidate_is_never_installed() {
    // 槽里是 9.9.9，用户看到并要装的是 9.9.10：装的必须是 9.9.10（现下），槽里那份原样不动
    let old = Rig::both_healthy().await;
    let state = UpdateState::default();
    run_check(&old.make(), &old.endpoints(), &state, &CN, &limits())
        .await
        .unwrap();
    run_download(&old.make(), &old.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();

    const PKG2: &[u8] = b"the-newer-installer";
    let newer = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.10", &k.sign_b64(PKG2), PKG2),
        |b, k| healthy(b, "9.9.10", &k.sign_b64(PKG2), PKG2),
    )
    .await;
    let pending = newer.check(&CN).await.unwrap().unwrap();
    assert_eq!(pending.update.version, "9.9.10");
    let (seen, install) = recorder();
    install_pending(
        &newer.make(),
        &newer.endpoints(),
        &state,
        &limits(),
        &pending,
        &|_| {},
        install,
    )
    .await
    .unwrap();
    assert_eq!(
        seen.lock().unwrap().as_slice(),
        [(Source::Mirror, PKG2.to_vec())]
    );
    assert_eq!(newer.mirror.hits("/releases/9.9.10/pkg"), 1);
    assert_eq!(
        state.ready.lock().unwrap().as_ref().unwrap().update.version,
        "9.9.9",
        "不同版本的包不被取走"
    );
    assert!(take_ready(&state, "9.9.10").is_none());
    assert!(take_ready(&state, "9.9.9").is_some());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_new_check_that_finds_another_version_or_none_drops_the_stale_package() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .unwrap();
    // 再查到同一版本：包留着
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    assert!(state.ready.lock().unwrap().is_some());
    // 查到「没有新版」（更宽的版本判据换成永不更新）：旧包作废
    let never = || make().version_comparator(|_, _| false);
    assert!(run_check(&never, &ep, &state, &CN, &limits())
        .await
        .unwrap()
        .is_none());
    assert!(state.ready.lock().unwrap().is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_failed_check_keeps_the_downloaded_package_installable() {
    // 后台下好之后用户离线再查一次：查失败不能把候选清掉——否则「现在重启」回 no_pending_update，
    // 而那个验过签的包明明还在、退出时照样会装（Codex #1730）
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .unwrap();
    let dead = Endpoints {
        mirror: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        mirror_root: "http://127.0.0.1:9".into(),
        github: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge_root: "http://127.0.0.1:9".into(),
    };
    assert!(run_check(&make, &dead, &state, &CN, &limits())
        .await
        .is_err());
    let pending = state
        .pending
        .try_lock()
        .unwrap()
        .clone()
        .expect("查失败后候选仍是已下好的那一版");
    assert_eq!(pending.update.version, "9.9.9");
    let hits = rig.mirror.hits(PKG_PATH);
    let (seen, install) = recorder();
    install_pending(&make, &ep, &state, &limits(), &pending, &|_| {}, install)
        .await
        .unwrap();
    assert_eq!(seen.lock().unwrap().len(), 1);
    assert_eq!(rig.mirror.hits(PKG_PATH), hits, "用的是已下好的包，不重下");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_failed_check_without_a_downloaded_package_still_clears_the_candidate() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let state = UpdateState::default();
    run_check(&make, &rig.endpoints(), &state, &CN, &limits())
        .await
        .unwrap();
    let dead = Endpoints {
        mirror: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        mirror_root: "http://127.0.0.1:9".into(),
        github: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge: Url::parse("http://127.0.0.1:9/latest.json").unwrap(),
        edge_root: "http://127.0.0.1:9".into(),
    };
    assert!(run_check(&make, &dead, &state, &CN, &limits())
        .await
        .is_err());
    assert!(state.pending.try_lock().unwrap().is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_failed_background_download_leaves_no_package_and_the_manual_install_still_works() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Status(500);
            r
        },
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Status(500);
            r
        },
    )
    .await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    let e = run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "all_sources_failed");
    assert!(state.ready.lock().unwrap().is_none());
    assert!(
        state.pending.try_lock().unwrap().is_some(),
        "候选句柄还在：用户仍可手动再下"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn while_a_background_download_runs_check_and_install_report_busy() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    let _held = state.pending.try_lock().unwrap();
    let e = run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .err()
        .unwrap();
    assert_eq!(e.code, "busy");
}

#[tokio::test(flavor = "multi_thread")]
async fn exit_takes_whatever_is_ready_once() {
    let rig = Rig::both_healthy().await;
    let make = rig.make();
    let ep = rig.endpoints();
    let state = UpdateState::default();
    assert!(take_ready_any(&state).is_none());
    run_check(&make, &ep, &state, &CN, &limits()).await.unwrap();
    run_download(&make, &ep, &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(take_ready_any(&state).unwrap().update.version, "9.9.9");
    assert!(take_ready_any(&state).is_none(), "取走后槽空：不会装两次");
}

/* ---- 检查与下载的来源拆开（ADR 0121 修订 2026-11-05）：大陆环境下载先走 GitHub ---- */

/// 发布时间固定在清单里的 `2026-10-12T00:00:00Z`（`manifest()`）
fn published() -> i64 {
    time::OffsetDateTime::parse(
        "2026-10-12T00:00:00Z",
        &time::format_description::well_known::Rfc3339,
    )
    .unwrap()
    .unix_timestamp()
}

const DAY: i64 = 24 * 3600;

fn trial_limits(eta: Duration) -> Limits {
    Limits {
        trial: TrialRule {
            eta,
            ..limits().trial
        },
        ..limits()
    }
}

/// 大陆环境：查（镜像优先）→ 拿到候选。
async fn mainland(rig: &Rig, state: &UpdateState, limits: &Limits) -> Pending {
    run_check(&rig.make(), &rig.endpoints(), state, &CN, limits)
        .await
        .unwrap()
        .unwrap();
    let p = state.pending.try_lock().unwrap().clone().unwrap();
    assert_eq!(p.source, Source::Mirror, "检查没变：大陆仍是镜像先答");
    assert!(p.mainland);
    p
}

fn seeded(tries: u32, failures: u32, first_seen: i64) -> crate::update_source_memory::Memory {
    crate::update_source_memory::Memory {
        version: "9.9.9".into(),
        first_seen,
        tries,
        failures,
    }
}

async fn manual_install(
    rig: &Rig,
    state: &UpdateState,
    p: &Pending,
    limits: &Limits,
) -> (Result<Source, Failure>, Vec<DownloadEvent>, Installed) {
    let (seen, install) = recorder();
    let log: Arc<Mutex<Vec<DownloadEvent>>> = Default::default();
    let l2 = log.clone();
    let emit = move |e: DownloadEvent| l2.lock().unwrap().push(e);
    let r = install_pending(
        &rig.make(),
        &rig.endpoints(),
        state,
        limits,
        p,
        &emit,
        install,
    )
    .await;
    let events = log.lock().unwrap().clone();
    (r, events, seen)
}

#[tokio::test(flavor = "multi_thread")]
async fn mainland_background_downloads_from_github_though_the_check_came_from_the_mirror() {
    let rig = Rig::both_healthy().await;
    let state = UpdateState::default();
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!((d.version.as_str(), d.source), ("9.9.9", Source::Github));
    assert_eq!(rig.github.hits(PKG_PATH), 1);
    assert_eq!(rig.mirror.hits(PKG_PATH), 0, "镜像只回答了检查，包没从它下");
    assert_eq!(
        rig.github.hits("/latest.json"),
        1,
        "下载来源与检查不同：GitHub 重新 check 一次"
    );
    assert_eq!(rig.mirror.hits("/latest.json"), 1, "检查没有多问镜像");
    assert!(
        state.memory.snapshot().is_none(),
        "GitHub 下成功 = 记录清空"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn mainland_background_never_probes_the_mirror_when_github_is_merely_slow() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), Route::Ok(p1.clone())),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    let l = slow_limits();
    mainland(&rig, &state, &l).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &l, &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Github, "慢不换来源");
    assert_eq!(rig.mirror.hits(PKG_PATH), 0);
    assert_eq!(rig.mirror.hits("/releases/9.9.9/pkg"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn mainland_background_falls_back_in_the_same_run_and_counts_each_github_failure() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Status(500);
            r
        },
    )
    .await;
    let state = UpdateState::default();
    state.memory.set_now(published());
    for run in 1..=3u32 {
        mainland(&rig, &state, &limits()).await;
        let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
            .await
            .unwrap();
        assert_eq!(
            d.source,
            Source::Mirror,
            "第 {run} 次：GitHub 失败，同一轮里落到镜像"
        );
        assert_eq!(
            rig.github.hits(PKG_PATH),
            run as usize,
            "第 {run} 次仍先试 GitHub（失败不到 3 次不改道）"
        );
        assert_eq!(state.memory.snapshot().unwrap().failures, run);
        take_ready_any(&state); // 模拟重启：内存里的包没了，记忆还在
    }
    // 第 4 次：连续失败满 3 次 → 直接镜像，不再碰 GitHub 的包
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Mirror);
    assert_eq!(
        rig.github.hits(PKG_PATH),
        3,
        "改走镜像后不再请求 GitHub 的包"
    );
    assert_eq!(
        state.memory.snapshot().unwrap().failures,
        3,
        "镜像下成功不清记录：重启后仍走镜像"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_github_success_resets_the_failure_streak() {
    let rig = Rig::both_healthy().await;
    let state = UpdateState::default();
    state.memory.set_now(published());
    state.memory.put(Some(seeded(2, 2, published())));
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Github, "2 次失败还不改道");
    assert!(state.memory.snapshot().is_none(), "成功后从头算");
}

#[tokio::test(flavor = "multi_thread")]
async fn mainland_background_goes_to_the_mirror_once_the_release_is_three_days_old_and_was_tried() {
    for (age, want, hits) in [
        (3 * DAY - 1, Source::Github, 1),
        (3 * DAY, Source::Mirror, 0),
    ] {
        let rig = Rig::both_healthy().await;
        let state = UpdateState::default();
        state.memory.set_now(published() + age);
        state.memory.put(Some(seeded(1, 0, published())));
        mainland(&rig, &state, &limits()).await;
        let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
            .await
            .unwrap();
        assert_eq!(d.source, want, "发布后 {age} 秒");
        assert_eq!(rig.github.hits(PKG_PATH), hits);
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn an_interrupted_background_run_still_counts_as_a_try_for_the_overdue_rule() {
    // 第一次后台下载（没有记录）：进入时就记下「试过一次」，哪怕之后被退出打断
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Hang;
            r
        },
    )
    .await;
    let state = UpdateState::default();
    state.memory.set_now(published() + 10 * DAY);
    mainland(&rig, &state, &limits()).await;
    let (make, ep, l) = (rig.make(), rig.endpoints(), limits());
    let fut = run_download(&make, &ep, &state, &l, &|_| {});
    // 超过 stall 之前就放弃等待（= 被打断）
    let _ = tokio::time::timeout(Duration::from_millis(250), fut).await;
    let m = state.memory.snapshot().expect("进入时已记下");
    assert_eq!((m.tries, m.failures), (1, 0));
    // 下一次启动：发布 10 天了、且试过 → 直接镜像
    take_ready_any(&state);
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Mirror);
}

#[tokio::test(flavor = "multi_thread")]
async fn without_a_publish_date_the_overdue_rule_counts_from_the_first_time_this_machine_saw_it() {
    let no_date = |b: &str, k: &Keys| {
        let sig = k.sign_b64(PKG);
        vec![
            (
                "/latest.json",
                Route::Ok(
                    json!({
                        "version": "9.9.9",
                        "platforms": { TARGET: { "url": format!("{b}/releases/9.9.9/pkg"), "signature": sig } },
                    })
                    .to_string()
                    .into_bytes(),
                ),
            ),
            ("/releases/9.9.9/pkg", Route::Ok(PKG.to_vec())),
        ]
    };
    for (first_seen_ago, want) in [(2 * DAY, Source::Github), (4 * DAY, Source::Mirror)] {
        let rig = Rig::new(Keys::new(), no_date, no_date).await;
        let state = UpdateState::default();
        let now = published() + 100 * DAY;
        state.memory.set_now(now);
        state.memory.put(Some(seeded(1, 0, now - first_seen_ago)));
        mainland(&rig, &state, &limits()).await;
        let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
            .await
            .unwrap();
        assert_eq!(d.source, want, "{first_seen_ago} 秒前首次见到");
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_new_version_starts_the_streak_over() {
    let rig = Rig::both_healthy().await;
    let state = UpdateState::default();
    state.memory.set_now(published());
    let mut old = seeded(5, 5, published() - 30 * DAY);
    old.version = "9.9.8".into();
    state.memory.put(Some(old));
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Github, "上一个版本的失败不带到这个版本");
}

#[tokio::test(flavor = "multi_thread")]
async fn background_with_a_different_github_version_uses_the_mirror_without_counting_a_failure() {
    const PKG2: &[u8] = b"the-newer-installer";
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.10", &k.sign_b64(PKG2), PKG2),
    )
    .await;
    let state = UpdateState::default();
    state.memory.set_now(published());
    mainland(&rig, &state, &limits()).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!((d.version.as_str(), d.source), ("9.9.9", Source::Mirror));
    assert_eq!(
        rig.github.hits("/releases/9.9.10/pkg"),
        0,
        "不悄悄下 GitHub 的更新版本"
    );
    assert_eq!(rig.github.hits("/releases/9.9.9/pkg"), 0);
    let m = state.memory.snapshot().unwrap();
    assert_eq!((m.tries, m.failures), (1, 0), "镜像滞后不是 GitHub 坏了");
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_stays_on_github_when_it_will_finish_soon_enough() {
    trial_stay(false).await;
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_stays_on_github_with_frequent_download_polls() {
    trial_stay(true).await;
}

async fn trial_stay(frequent_polls: bool) {
    use std::future::{poll_fn, Future};

    let pkg = big_pkg();
    let l = trial_limits(Duration::from_secs(5));
    let first_seen = Arc::new(tokio::sync::Notify::new());
    let finish = Arc::new(tokio::sync::Notify::new());
    let route = Route::TrialStay {
        body: pkg.clone(),
        first_seen: first_seen.clone(),
        finish: finish.clone(),
        middle_after: l.trial.wait / 2,
    };
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), Route::Ok(p1.clone())),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), route),
    )
    .await;
    let state = UpdateState::default();
    let p = mainland(&rig, &state, &l).await;
    let (seen, install) = recorder();
    let events = Mutex::new(Vec::new());
    let progress = Mutex::new(Vec::new());
    let emit = |e: DownloadEvent| {
        if let DownloadEvent::Progress { chunk_length } = &e {
            progress
                .lock()
                .unwrap()
                .push((Instant::now(), *chunk_length));
            first_seen.notify_one();
        }
        events.lock().unwrap().push(e);
    };
    let (make, ep) = (rig.make(), rig.endpoints());
    let mut downloading = Box::pin(install_pending(&make, &ep, &state, &l, &p, &emit, install));
    // 与生产看门狗同一个 tick；不改 wait / eta / stall，只控制服务器何时发尾字节。
    let tick = (l.stall.min(l.slow.probe).min(l.trial.wait) / 4)
        .clamp(Duration::from_millis(10), Duration::from_secs(1));
    let mut quiet_pending = None::<(usize, Instant)>;
    let mut trial_polled = None::<Instant>;
    let mut released = None;
    // Future 可以在没有新网络进度时被反复 poll；定时唤醒只用来重现这种调度。
    let mut repoll = Box::pin(tokio::time::sleep(tick / 4));
    let mut quiet_repolls = 0;
    let r = poll_fn(|cx| {
        let repolled = frequent_polls && repoll.as_mut().poll(cx).is_ready();
        if repolled {
            repoll
                .as_mut()
                .reset(tokio::time::Instant::now() + tick / 4);
            cx.waker().wake_by_ref();
        }
        let before = Instant::now();
        let result = downloading.as_mut().poll(cx);
        if result.is_pending() {
            let samples = progress.lock().unwrap();
            let done: usize = samples.iter().map(|(_, n)| n).sum();
            if repolled && done == pkg.len() - 1 && released.is_none() {
                quiet_repolls += 1;
            }
            if let (Some(&(first, _)), Some(&(last, _)), Some((previous_done, previous))) =
                (samples.first(), samples.last(), quiet_pending)
            {
                // 检查点的 Pending 已收齐主体，期间没有网络输入；隔了完整 tick 再 poll，
                // select 的计时分支必定就绪。窗口已过且仍缺尾字节，不能靠提前下完假绿。
                if done == pkg.len() - 1
                    && done == previous_done
                    && last <= previous
                    && before.duration_since(previous) >= tick
                    && before.duration_since(first) >= l.trial.wait + tick
                {
                    if trial_polled.is_none() {
                        trial_polled = Some(before);
                        quiet_pending = Some((done, Instant::now()));
                    } else if released.is_none()
                        && rig.mirror.hits("/latest.json") == 1
                        && rig.mirror.hits(PKG_PATH) == 0
                    {
                        // 再让生产路径跑一个 tick：若错误地 Leave，健康镜像有机会接手，
                        // 不让尾字节与刚启动的镜像请求竞速，掩盖来源决策回归。
                        released = Some(before);
                        finish.notify_one();
                    }
                }
            }
            // 只有新进度才重建检查点；无关 wake / 更频繁的 poll 不能无限扣住尾字节，
            // 否则夹具自己会把健康下载拖过 stall。
            if quiet_pending.is_none_or(|(previous_done, _)| done != previous_done) {
                quiet_pending = Some((done, Instant::now()));
            }
        }
        result
    })
    .await;
    assert_eq!(r.unwrap(), Source::Github);
    assert_eq!(seen.lock().unwrap()[0].1, pkg);
    assert_eq!(rig.mirror.hits(PKG_PATH), 0, "够快就不碰镜像的包");
    assert_eq!(starts(&events.lock().unwrap()), 1);
    assert!(
        !frequent_polls || quiet_repolls >= 2,
        "主体收齐、尾字节未放行时确实有额外轮询：{quiet_repolls}"
    );

    let samples = progress.lock().unwrap();
    let (first, first_chunk) = samples[0];
    let (tail, tail_chunk) = *samples.last().unwrap();
    let released = released.expect("试探计时分支跑过后才允许完成下载");
    let judged = trial_polled.unwrap();
    let elapsed = judged.duration_since(first);
    let done: usize = samples[..samples.len() - 1].iter().map(|(_, n)| n).sum();
    let rate = (done - first_chunk) as f64 / elapsed.as_secs_f64();
    let eta = (pkg.len() - done) as f64 / rate;
    assert_eq!((first_chunk, tail_chunk, done), (1, 1, pkg.len() - 1));
    assert!(elapsed >= l.trial.wait);
    assert!(tail >= released && released > judged);
    let (last_body, _) = samples[samples.len() - 2];
    let body_quiet = judged.duration_since(last_body);
    let decision_wait = released.duration_since(judged);
    assert!(
        body_quiet >= tick,
        "body_quiet={body_quiet:?}, tick={tick:?}"
    );
    assert!(
        decision_wait >= tick,
        "decision_wait={decision_wait:?}, tick={tick:?}"
    );
    assert!(rate > 0.0 && eta < l.trial.eta.as_secs_f64());
    assert!(events.lock().unwrap().iter().any(|e| matches!(
        e,
        DownloadEvent::Started { content_length: Some(n) } if *n == pkg.len() as u64
    )));
    eprintln!(
        "trial fixture: window={elapsed:?} >= {:?}, done={done}/{}, first_chunk={first_chunk}, rate={rate:.1} B/s, ETA={eta:.6} s, quiet_repolls={quiet_repolls}, body_quiet={body_quiet:?}, decision_wait={decision_wait:?}, tail_after={:?}",
        l.trial.wait,
        pkg.len(),
        tail.duration_since(first),
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_leaves_a_slow_github_for_the_mirror() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| {
            // 镜像也不是瞬间下完（1.5 s）：这样「一出字节就换」与「等够试探窗口再换」在结果上分得开
            let slow_mirror = Route::Drip {
                body: p1.clone(),
                chunks: 100,
                gap: Duration::from_millis(15),
            };
            source_with(b, "9.9.9", &k.sign_b64(&p1), slow_mirror)
        },
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    // 预计要 2 s+ > 1 s。试探窗口放长到 5 s：若还要等它量满才换，GitHub 早在 3 s 下完了——
    // 这条同时看护「试用判慢后镜像一出字节就换，不再比速」
    let l = Limits {
        slow: SlowRule {
            probe: Duration::from_secs(5),
            ..limits().slow
        },
        ..trial_limits(Duration::from_secs(1))
    };
    let p = mainland(&rig, &state, &l).await;
    let (r, events, seen) = manual_install(&rig, &state, &p, &l).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert_eq!(seen.lock().unwrap()[0].1, pkg, "镜像的包同样验过签");
    assert_eq!(rig.github.hits("/releases/9.9.9/pkg"), 1, "先试了 GitHub");
    assert_eq!(starts(&events), 2, "换来源：进度归零（第二次 Started）");
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_leaves_github_when_it_sends_no_byte_within_the_trial() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Hang;
            r
        },
    )
    .await;
    let state = UpdateState::default();
    // 停滞看门狗放到 10 s：换来源只能是试用期（400 ms）的功劳
    let l = Limits {
        stall: Duration::from_secs(10),
        ..limits()
    };
    let p = mainland(&rig, &state, &l).await;
    let t = Instant::now();
    let (r, _, _) = manual_install(&rig, &state, &p, &l).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert!(t.elapsed() < Duration::from_secs(5), "{:?}", t.elapsed());
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_keeps_a_slow_github_when_the_mirror_cannot_deliver() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), Route::Status(500)),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    let l = trial_limits(Duration::from_secs(1));
    let p = mainland(&rig, &state, &l).await;
    let (r, _, seen) = manual_install(&rig, &state, &p, &l).await;
    assert_eq!(
        r.unwrap(),
        Source::Github,
        "镜像也给不出包时，慢的 GitHub 继续下完"
    );
    assert_eq!(seen.lock().unwrap()[0].1, pkg);
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_never_swaps_versions_when_github_reports_another_one() {
    const PKG2: &[u8] = b"the-newer-installer";
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.10", &k.sign_b64(PKG2), PKG2),
    )
    .await;
    let state = UpdateState::default();
    let p = mainland(&rig, &state, &limits()).await;
    let (r, _, seen) = manual_install(&rig, &state, &p, &limits()).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert_eq!(seen.lock().unwrap()[0].1, PKG, "装的就是用户看到的 9.9.9");
    assert_eq!(rig.github.hits("/releases/9.9.10/pkg"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_does_not_touch_the_background_memory() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| {
            let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
            r[1].1 = Route::Status(500);
            r
        },
    )
    .await;
    let state = UpdateState::default();
    let p = mainland(&rig, &state, &limits()).await;
    let (r, _, _) = manual_install(&rig, &state, &p, &limits()).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert!(
        state.memory.snapshot().is_none(),
        "手动下载不计入后台的失败次数"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn outside_the_mainland_nothing_changes_check_source_downloads_and_the_memory_stays_empty() {
    let rig = Rig::both_healthy().await;
    let state = UpdateState::default();
    run_check(&rig.make(), &rig.endpoints(), &state, &WORLD, &limits())
        .await
        .unwrap();
    let p = state.pending.try_lock().unwrap().clone().unwrap();
    assert!(!p.mainland);
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Github);
    assert_eq!(rig.mirror.hits("/latest.json"), 0, "海外：镜像一次都没被问");
    assert_eq!(rig.mirror.hits(PKG_PATH), 0);
    assert_eq!(
        rig.github.hits("/latest.json"),
        1,
        "check 来源就是下载来源：不重新 check"
    );
    assert!(state.memory.snapshot().is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn outside_the_mainland_a_slow_download_is_still_probed_and_a_failing_one_still_falls_back() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), Route::Ok(p1.clone())),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    let l = slow_limits();
    run_check(&rig.make(), &rig.endpoints(), &state, &WORLD, &l)
        .await
        .unwrap();
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &l, &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Mirror, "海外慢下载照旧试探、更快才换");
    assert!(state.memory.snapshot().is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pinned_single_source_is_not_mainland_and_never_trials_or_probes() {
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p1), Route::Ok(p1.clone())),
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    let l = trial_limits(Duration::from_secs(1));
    run_check(&rig.make(), &rig.endpoints(), &state, &[Source::Github], &l)
        .await
        .unwrap();
    let p = state.pending.try_lock().unwrap().clone().unwrap();
    assert!(!p.mainland);
    let (r, _, _) = manual_install(&rig, &state, &p, &l).await;
    assert_eq!(r.unwrap(), Source::Github);
    assert_eq!(rig.mirror.hits("/latest.json"), 0);
    assert_eq!(rig.mirror.hits("/releases/9.9.9/pkg"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_cooled_down_mirror_does_not_make_the_machine_look_overseas() {
    // 镜像刚出过问题 → 检查顺序里它被挪到后面；但下载仍按「大陆」先 GitHub（冷却只改 order）
    let rig = Rig::both_healthy().await;
    let state = UpdateState::default();
    state.cooldown.trip();
    run_check(&rig.make(), &rig.endpoints(), &state, &CN, &limits())
        .await
        .unwrap();
    let p = state.pending.try_lock().unwrap().clone().unwrap();
    assert_eq!(p.source, Source::Github, "冷却中：GitHub 先答");
    assert!(p.mainland, "但本机仍是大陆环境");
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Github);
    assert_eq!(rig.mirror.hits(PKG_PATH), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn the_mirror_that_just_answered_the_check_is_usable_even_while_cooling_down() {
    // 镜像冷却中；GitHub 的检查答不上来（404），于是镜像答了检查。手动下载时 GitHub 再 check 仍答不上来，
    // 这时手里这份刚从镜像拿到的、验过的候选不能被冷却拦掉（冷却只拦「回头去试」）
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |_, _| vec![],
    )
    .await;
    let state = UpdateState::default();
    state.cooldown.trip();
    run_check(&rig.make(), &rig.endpoints(), &state, &CN, &limits())
        .await
        .unwrap();
    let p = state.pending.try_lock().unwrap().clone().unwrap();
    assert_eq!((p.source, p.mainland), (Source::Mirror, true));
    let (r, _, seen) = manual_install(&rig, &state, &p, &limits()).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert_eq!(seen.lock().unwrap()[0].1, PKG);
}

#[tokio::test(flavor = "multi_thread")]
async fn manual_mainland_moves_to_the_mirror_on_its_first_byte_without_comparing_speeds() {
    // 方案是「预计超过 2 分钟就换镜像」，不是「镜像更快才换」：镜像这里更慢（4 s 对 GitHub 的 3 s）也照换。
    // （试探窗口放到 10 s：若还要等它量满，GitHub 早下完了）
    let pkg = big_pkg();
    let (p1, p2) = (pkg.clone(), pkg.clone());
    let rig = Rig::new(
        Keys::new(),
        move |b, k| {
            let slower = Route::Drip {
                body: p1.clone(),
                chunks: 200,
                gap: Duration::from_millis(20),
            };
            source_with(b, "9.9.9", &k.sign_b64(&p1), slower)
        },
        move |b, k| source_with(b, "9.9.9", &k.sign_b64(&p2), slow_drip(&p2)),
    )
    .await;
    let state = UpdateState::default();
    let l = Limits {
        slow: SlowRule {
            probe: Duration::from_secs(10),
            ..limits().slow
        },
        ..trial_limits(Duration::from_secs(1))
    };
    let p = mainland(&rig, &state, &l).await;
    let (r, events, _) = manual_install(&rig, &state, &p, &l).await;
    assert_eq!(r.unwrap(), Source::Mirror);
    assert_eq!(starts(&events), 2);
}

/* ------------- 边缘节点（ADR 0121 修订 2026-11-11）：只分担大陆·后台下载 ------------- */

/// GitHub 清单健康、但包地址 500（后台下载在 GitHub 一环失败）。
fn github_pkg_500(b: &str, k: &Keys) -> Vec<(&'static str, Route)> {
    let mut r = healthy(b, "9.9.9", &k.sign_b64(PKG), PKG);
    r[1].1 = Route::Status(500);
    r
}

/// 大陆·后台下载的前置：check 由镜像答（CN 顺序），随后 `run_download` 走 `[GitHub, 节点, 镜像]`。
async fn mainland_pending(rig: &Rig) -> UpdateState {
    let state = UpdateState::default();
    run_check(&rig.make(), &rig.endpoints(), &state, &CN, &limits())
        .await
        .unwrap()
        .unwrap();
    state
}

#[tokio::test(flavor = "multi_thread")]
async fn a_mainland_background_download_falls_to_the_edge_before_the_mirror() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        github_pkg_500,
    )
    .await
    .with_edge(|b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG))
    .await;
    let state = mainland_pending(&rig).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Edge);
    assert_eq!(rig.edge.hits(PKG_PATH), 1);
    assert_eq!(rig.mirror.hits(PKG_PATH), 0, "节点下成了，镜像不用碰");
    assert!(!state.cooldown.active(), "节点不是镜像，不触发冷却");
    // 记账：GitHub 失败一次、节点兜住 = GitHub 失败 +1（与镜像兜住一样）
    let m = state.memory.snapshot().expect("大陆后台下载会留记忆");
    assert_eq!((m.tries, m.failures), (1, 1));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_closed_or_lagging_edge_is_skipped_and_the_mirror_finishes_the_job() {
    // 节点清单 404（关闭）：GitHub 失败后直接落到镜像，且不进冷却
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        github_pkg_500,
    )
    .await;
    let state = mainland_pending(&rig).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Mirror);
    assert_eq!(rig.edge.hits("/latest.json"), 1, "问过节点，404");
    assert_eq!(rig.edge.hits(PKG_PATH), 0);
    assert!(!state.cooldown.active(), "节点失败不触发镜像冷却");

    // 节点落后一个版本：VersionMismatch，同样跳过，绝不悄悄换版本
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        github_pkg_500,
    )
    .await
    .with_edge(|b, k| healthy(b, "9.9.8", &k.sign_b64(PKG), PKG))
    .await;
    let state = mainland_pending(&rig).await;
    let d = run_download(&rig.make(), &rig.endpoints(), &state, &limits(), &|_| {})
        .await
        .unwrap();
    assert_eq!(d.source, Source::Mirror);
    assert_eq!(
        rig.edge.hits("/releases/9.9.8/pkg"),
        0,
        "落后的版本一个字节都不下"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn the_edge_is_pinned_to_its_host_and_its_package_must_live_under_its_root() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await;
    // 节点清单把包指到镜像地址（不在节点根下）：不跟随，当不可用
    let mirror_pkg = format!("{}{PKG_PATH}", rig.mirror.base);
    let rig = rig
        .with_edge(move |_, k| {
            vec![(
                "/latest.json",
                manifest("9.9.9", &mirror_pkg, &k.sign_b64(PKG)),
            )]
        })
        .await;
    let f = rig.check(&[Source::Edge]).await.err().unwrap();
    assert_eq!(
        kinds(&f),
        vec![(Source::Edge, Stage::Check, Kind::NotOnMirror)]
    );

    // 节点清单重定向到别的主机：不跟随
    let github_url = format!("{}/latest.json", rig.github.base);
    let rig = rig
        .with_edge(move |_, _| vec![("/latest.json", Route::Redirect(github_url))])
        .await;
    let f = rig.check(&[Source::Edge]).await.err().unwrap();
    assert_eq!(kinds(&f)[0].0, Source::Edge);
    assert_eq!(rig.github.hits("/latest.json"), 0, "重定向没被跟随");
}

#[tokio::test(flavor = "multi_thread")]
async fn the_edge_never_joins_a_check_or_a_manual_download() {
    let rig = Rig::new(
        Keys::new(),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
        |b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG),
    )
    .await
    .with_edge(|b, k| healthy(b, "9.9.9", &k.sign_b64(PKG), PKG))
    .await;
    // check：只问 [镜像, GitHub]
    let state = mainland_pending(&rig).await;
    assert_eq!(rig.edge.hits("/latest.json"), 0);
    // 手动下载：[GitHub, 镜像]，GitHub 健康就用它，节点依旧没被碰
    let pending = state.pending.try_lock().unwrap().clone().unwrap();
    let (_seen, install) = recorder();
    let src = install_pending(
        &rig.make(),
        &rig.endpoints(),
        &state,
        &limits(),
        &pending,
        &|_| {},
        install,
    )
    .await
    .unwrap();
    assert_eq!(src, Source::Github);
    assert_eq!(rig.edge.hits("/latest.json"), 0);
    assert_eq!(rig.edge.hits(PKG_PATH), 0);
}
