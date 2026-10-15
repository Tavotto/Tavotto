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
    mirror: Fake,
    github: Fake,
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
        Self {
            app: app(&keys),
            mirror,
            github,
            cool: Cooldown::default(),
        }
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
        let log: Arc<Mutex<Vec<DownloadEvent>>> = Default::default();
        let l2 = log.clone();
        let emit = move |e: DownloadEvent| l2.lock().unwrap().push(e);
        let r = fetch(
            &self.make(),
            &self.endpoints(),
            &limits(),
            pending,
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
