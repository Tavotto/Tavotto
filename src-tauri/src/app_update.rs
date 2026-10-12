//! 应用内更新的来源编排（ADR 0121）：国内 COS 镜像与 GitHub Release 按顺序尝试。
//!
//! 为什么不用插件自带的多 endpoints：它只覆盖 **check**，下载仍直连 check 拿到的那个 url；
//! 镜像能查到、包却下不动（或下到了但签名对不上）时用户只会看到失败。这里把「查 + 下」当作一个
//! 来源内的整体，一个来源失败就换下一个来源**重新查、重新下**。
//!
//! 不变的东西（信任不随来源变）：
//! * 验签始终用 `tauri.conf.json` 里的内置公钥——来源只决定「从哪下」，不决定「信谁」。镜像清单的
//!   `signature` 与上游逐字相同，镜像被篡改或过期都只会让验签失败、回退到下一个来源；
//! * 只在用户按下按钮之后下载（`desktop_update_install`）；启动时只做 check。
//!
//! 「无更新」不回退：第一个**答上来**的来源说没有，就是没有（ADR 0121 §3）。只有答不上来
//! （超时 / 非 2xx / 清单坏 / 缺本平台）才换下一个来源。

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri_plugin_updater::{Error as UpdaterError, Update, Updater, UpdaterBuilder};
use url::Url;

/// 镜像清单地址（单一常量，调整只改这里与 ADR 0121）。镜像关闭 / 过期时该文件不可用，
/// 客户端自然落到 GitHub。
pub const MIRROR_MANIFEST_URL: &str = "https://dl.tavotto.cn/updater/latest.json";

/// 单个来源的 check 总超时（含连接）。
pub const CHECK_TIMEOUT: Duration = Duration::from_secs(10);
/// 连接超时：check 与下载共用。
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// 下载停滞超时：这么久没收到任何字节就放弃这个来源。**不设下载总时长**——慢网下大包本来就久。
pub const STALL_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub check: Duration,
    pub connect: Duration,
    pub stall: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            check: CHECK_TIMEOUT,
            connect: CONNECT_TIMEOUT,
            stall: STALL_TIMEOUT,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Mirror,
    Github,
}

impl Source {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "mirror" => Some(Self::Mirror),
            "github" => Some(Self::Github),
            _ => None,
        }
    }
}

/* -------------------------------------------------------------------------- */
/*  来源顺序                                                                    */
/* -------------------------------------------------------------------------- */

/// 中国大陆的 IANA 时区名（含历史别名；**不含**香港 / 澳门 / 台北——那里直连 GitHub 不受限）。
const MAINLAND_TIMEZONES: &[&str] = &[
    "Asia/Shanghai",
    "Asia/Chongqing",
    "Asia/Chungking",
    "Asia/Harbin",
    "Asia/Urumqi",
    "Asia/Kashgar",
    "PRC",
];

pub fn is_mainland_timezone(tz: &str) -> bool {
    MAINLAND_TIMEZONES.contains(&tz.trim())
}

/// 简体中文（大陆）语言标签：`zh-CN` / `zh-Hans-CN` / 不带地区的 `zh-Hans` / 裸 `zh`。
/// `_` 与 `-` 都认（macOS 给 `zh_CN`），大小写不敏感。`zh-TW` / `zh-HK` / `zh-Hant-*` / `zh-SG` 不算。
pub fn is_mainland_locale(tag: &str) -> bool {
    let norm = tag.trim().replace('_', "-").to_ascii_lowercase();
    // 去掉 `-u-…` / `.UTF-8` / `@…` 一类扩展，只留语言-文字-地区
    let base = norm
        .split(['.', '@'])
        .next()
        .unwrap_or("")
        .split("-u-")
        .next()
        .unwrap_or("");
    let parts: Vec<&str> = base.split('-').filter(|p| !p.is_empty()).collect();
    match parts.as_slice() {
        ["zh"] => true,
        ["zh", rest @ ..] => {
            let hant = rest.contains(&"hant");
            let region = rest
                .iter()
                .find(|p| p.len() == 2 && p.chars().all(|c| c.is_ascii_alphabetic()));
            !hant && matches!(region, None | Some(&"cn"))
        }
        _ => false,
    }
}

/// 来源顺序的**唯一**判据：系统时区在大陆，或系统语言 / 界面语言是简体中文（大陆）→ 镜像优先；
/// 其余 GitHub 优先。两个来源都在列表里——镜像同样是海外用户 GitHub 失败时的后备。
pub fn source_order(
    timezone: Option<&str>,
    system_locale: Option<&str>,
    ui_locale: Option<&str>,
) -> Vec<Source> {
    let cn = timezone.is_some_and(is_mainland_timezone)
        || system_locale.is_some_and(is_mainland_locale)
        || ui_locale.is_some_and(is_mainland_locale);
    if cn {
        vec![Source::Mirror, Source::Github]
    } else {
        vec![Source::Github, Source::Mirror]
    }
}

/// 读本机的时区与系统语言，再套 `source_order`。
pub fn detect_order(ui_locale: Option<&str>) -> Vec<Source> {
    let tz = iana_time_zone::get_timezone().ok();
    let sys = sys_locale::get_locale();
    source_order(tz.as_deref(), sys.as_deref(), ui_locale)
}

pub struct Endpoints {
    pub mirror: Url,
    pub github: Url,
}

impl Endpoints {
    pub fn url(&self, s: Source) -> &Url {
        match s {
            Source::Mirror => &self.mirror,
            Source::Github => &self.github,
        }
    }
}

/* -------------------------------------------------------------------------- */
/*  失败的形状                                                                  */
/* -------------------------------------------------------------------------- */

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Check,
    Download,
    Install,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    /// 连不上 / 连接中断 / 读 body 出错
    Network,
    /// check 总超时或连接超时
    Timeout,
    /// 下载中途一段时间没有字节
    Stalled,
    /// 非 2xx 状态
    Http,
    /// 清单不是合法的更新 JSON / 缺本平台条目 / 版本号坏
    BadManifest,
    /// 验签不过（包被改、清单与包对不上、签名坏）
    Signature,
    /// 回退来源报的版本与用户已看到的那一版不同（含「没有更新」）
    VersionMismatch,
    Other,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Attempt {
    pub source: Source,
    pub stage: Stage,
    pub kind: Kind,
}

/// 命令的错误：稳定 code + 逐来源的失败记录。**不带原始错误文本**（可能含 URL / 路径）；
/// 原文只写 stderr（app.log）。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Failure {
    pub code: &'static str,
    pub attempts: Vec<Attempt>,
}

impl Failure {
    pub fn code(code: &'static str) -> Self {
        Self {
            code,
            attempts: Vec::new(),
        }
    }
}

pub fn classify(e: &UpdaterError) -> Kind {
    match e {
        UpdaterError::Reqwest(r) => {
            if r.is_timeout() {
                Kind::Timeout
            } else if r.is_decode() {
                Kind::BadManifest
            } else if r.is_status() {
                Kind::Http
            } else {
                Kind::Network
            }
        }
        // 插件在下载收到非 2xx 时给 `Network("Download request failed with status: …")`
        UpdaterError::Network(_) | UpdaterError::ReleaseNotFound => Kind::Http,
        UpdaterError::Serialization(_)
        | UpdaterError::Semver(_)
        | UpdaterError::TargetNotFound(_)
        | UpdaterError::TargetsNotFound(_)
        | UpdaterError::UrlParse(_) => Kind::BadManifest,
        UpdaterError::Minisign(_) | UpdaterError::Base64(_) | UpdaterError::SignatureUtf8(_) => {
            Kind::Signature
        }
        _ => Kind::Other,
    }
}

/* -------------------------------------------------------------------------- */
/*  check                                                                      */
/* -------------------------------------------------------------------------- */

/// check 拿到的句柄 + 它来自哪个来源 + 本次的来源顺序。安装时同一个，不重新查。
#[derive(Clone)]
pub struct Pending {
    pub update: Update,
    pub source: Source,
    pub order: Vec<Source>,
}

fn build_updater<F>(make: &F, url: &Url, limits: &Limits) -> Result<Updater, UpdaterError>
where
    F: Fn() -> UpdaterBuilder,
{
    let connect = limits.connect;
    make()
        .endpoints(vec![url.clone()])?
        .timeout(limits.check)
        .configure_client(move |c| c.connect_timeout(connect))
        .build()
}

async fn check_one<F>(make: &F, url: &Url, limits: &Limits) -> Result<Option<Update>, UpdaterError>
where
    F: Fn() -> UpdaterBuilder,
{
    build_updater(make, url, limits)?.check().await
}

/// 按顺序查：第一个**答上来**的来源说了算（有更新 → `Some`，没有 → `None`，都不再问下一个）；
/// 答不上来的记一笔、换下一个；全部答不上来才报错。
pub async fn check<F>(
    make: &F,
    endpoints: &Endpoints,
    order: &[Source],
    limits: &Limits,
) -> Result<Option<Pending>, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let mut attempts = Vec::new();
    for &source in order {
        match check_one(make, endpoints.url(source), limits).await {
            Ok(Some(update)) => {
                return Ok(Some(Pending {
                    update,
                    source,
                    order: order.to_vec(),
                }))
            }
            Ok(None) => return Ok(None),
            Err(e) => {
                eprintln!("[updater] {source:?} 检查失败: {e}");
                attempts.push(Attempt {
                    source,
                    stage: Stage::Check,
                    kind: classify(&e),
                });
            }
        }
    }
    Err(Failure {
        code: "all_sources_failed",
        attempts,
    })
}

/* -------------------------------------------------------------------------- */
/*  下载 + 安装                                                                 */
/* -------------------------------------------------------------------------- */

/// 下载进度事件，形状与插件自己的 `DownloadEvent` 一致（前端的进度换算原样沿用）。
/// 每换一个来源重新下载就会再来一次 `Started`——前端据此把进度归零。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "event", content = "data")]
pub enum DownloadEvent {
    #[serde(rename_all = "camelCase")]
    Started {
        content_length: Option<u64>,
    },
    #[serde(rename_all = "camelCase")]
    Progress {
        chunk_length: usize,
    },
    Finished,
}

pub type Emit<'a> = &'a (dyn Fn(DownloadEvent) + Send + Sync);

async fn stalled(last: Arc<AtomicU64>, start: Instant, limit: Duration) {
    let tick = (limit / 4).clamp(Duration::from_millis(10), Duration::from_secs(1));
    loop {
        tokio::time::sleep(tick).await;
        let now = start.elapsed().as_millis() as u64;
        if now.saturating_sub(last.load(Ordering::Relaxed)) >= limit.as_millis() as u64 {
            return;
        }
    }
}

/// 下载并**验签**（插件的 `download` 在返回前用内置公钥验签），停滞超过 `limits.stall` 放弃。
async fn download_one(update: &Update, limits: &Limits, emit: Emit<'_>) -> Result<Vec<u8>, Kind> {
    let start = Instant::now();
    let last = Arc::new(AtomicU64::new(0));
    let last_in_chunk = last.clone();
    let first = AtomicBool::new(true);
    let download = update.download(
        |n, total| {
            last_in_chunk.store(start.elapsed().as_millis() as u64, Ordering::Relaxed);
            if first.swap(false, Ordering::Relaxed) {
                emit(DownloadEvent::Started {
                    content_length: total,
                });
            }
            emit(DownloadEvent::Progress { chunk_length: n });
        },
        || emit(DownloadEvent::Finished),
    );
    tokio::select! {
        r = download => r.map_err(|e| {
            eprintln!("[updater] 下载失败: {e}");
            classify(&e)
        }),
        _ = stalled(last, start, limits.stall) => {
            eprintln!("[updater] 下载停滞超过 {:?}", limits.stall);
            Err(Kind::Stalled)
        }
    }
}

/// 下载并验签好的安装包，还没动磁盘上的应用。
pub struct Fetched {
    pub source: Source,
    pub update: Update,
    pub bytes: Vec<u8>,
}

/// 安装已下载（且已验签）的包。失败不换来源——那不是来源的错。
pub fn apply(f: Fetched) -> Result<Source, Failure> {
    let source = f.source;
    f.update.install(f.bytes).map(|()| source).map_err(|e| {
        eprintln!("[updater] 安装失败: {e}");
        Failure {
            code: "install_failed",
            attempts: vec![Attempt {
                source,
                stage: Stage::Install,
                kind: classify(&e),
            }],
        }
    })
}

/// 下载 `pending`。先用 check 时拿到的来源；它下载失败（网络 / 非 2xx / 验签 / 停滞）
/// 就换下一个来源**重新 check + 重新下载**，且回退来源必须报出**同一个版本**——用户看到的版本号与
/// 真正装上去的必须是同一版。返回的包已用内置公钥验过签。
pub async fn fetch<F>(
    make: &F,
    endpoints: &Endpoints,
    limits: &Limits,
    pending: &Pending,
    emit: Emit<'_>,
) -> Result<Fetched, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let mut attempts = Vec::new();
    let mut seq = vec![pending.source];
    seq.extend(
        pending
            .order
            .iter()
            .copied()
            .filter(|s| *s != pending.source),
    );
    for (i, &source) in seq.iter().enumerate() {
        let update = if i == 0 {
            pending.update.clone()
        } else {
            match check_one(make, endpoints.url(source), limits).await {
                Ok(Some(u)) if u.version == pending.update.version => u,
                Ok(_) => {
                    attempts.push(Attempt {
                        source,
                        stage: Stage::Check,
                        kind: Kind::VersionMismatch,
                    });
                    continue;
                }
                Err(e) => {
                    eprintln!("[updater] {source:?} 回退检查失败: {e}");
                    attempts.push(Attempt {
                        source,
                        stage: Stage::Check,
                        kind: classify(&e),
                    });
                    continue;
                }
            }
        };
        match download_one(&update, limits, emit).await {
            Ok(bytes) => {
                return Ok(Fetched {
                    source,
                    update,
                    bytes,
                })
            }
            Err(kind) => attempts.push(Attempt {
                source,
                stage: Stage::Download,
                kind,
            }),
        }
    }
    Err(Failure {
        code: "all_sources_failed",
        attempts,
    })
}

/* -------------------------------------------------------------------------- */
/*  与 AppHandle 的接口（命令与 e2e 触发口共用）                                   */
/* -------------------------------------------------------------------------- */

/// check 的结果交给前端的那一份。`date` 是 RFC 3339，原样透出。
#[derive(Debug, Clone, Serialize)]
pub struct CheckedUpdate {
    pub version: String,
    pub notes: Option<String>,
    pub date: Option<String>,
    pub source: Source,
}

/// 上一次 check 留下的句柄。同一时刻只允许一个 check / install 在跑（`try_lock`，撞上回 `busy`）。
#[derive(Default)]
pub struct UpdateState {
    pub pending: tokio::sync::Mutex<Option<Pending>>,
}

/// GitHub 的清单地址取自 `tauri.conf.json` 的 `plugins.updater.endpoints[0]`——它仍是那里
/// 唯一的 endpoint（发布链的 `updater_consumer_check.py` 也读它）；镜像地址只在本文件的常量里。
pub fn endpoints<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<Endpoints, Failure> {
    let github = app
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("endpoints"))
        .and_then(|e| e.get(0))
        .and_then(|e| e.as_str())
        .and_then(|e| Url::parse(e).ok())
        .ok_or_else(|| Failure::code("config_missing"))?;
    let mirror = Url::parse(MIRROR_MANIFEST_URL).map_err(|_| Failure::code("config_missing"))?;
    Ok(Endpoints { mirror, github })
}

pub async fn run_check<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    order: &[Source],
    limits: &Limits,
) -> Result<Option<CheckedUpdate>, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let mut slot = state
        .pending
        .try_lock()
        .map_err(|_| Failure::code("busy"))?;
    // 先清掉旧句柄：这次查不到 / 查失败，就不该还留着上一次的
    *slot = None;
    let found = check(make, endpoints, order, limits).await?;
    let out = found.as_ref().map(|p| CheckedUpdate {
        version: p.update.version.clone(),
        notes: p.update.body.clone(),
        date: p.update.date.and_then(|d| {
            d.format(&time::format_description::well_known::Rfc3339)
                .ok()
        }),
        source: p.source,
    });
    *slot = found;
    Ok(out)
}

pub async fn run_install<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    limits: &Limits,
    emit: Emit<'_>,
) -> Result<Source, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let slot = state
        .pending
        .try_lock()
        .map_err(|_| Failure::code("busy"))?;
    let pending = slot
        .clone()
        .ok_or_else(|| Failure::code("no_pending_update"))?;
    // 句柄保留到成功：失败后用户可以直接再按一次
    apply(fetch(make, endpoints, limits, &pending, emit).await?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn order(tz: Option<&str>, sys: Option<&str>, ui: Option<&str>) -> Vec<Source> {
        source_order(tz, sys, ui)
    }
    const CN: [Source; 2] = [Source::Mirror, Source::Github];
    const WORLD: [Source; 2] = [Source::Github, Source::Mirror];

    #[test]
    fn mainland_timezones_put_the_mirror_first() {
        for tz in ["Asia/Shanghai", "Asia/Urumqi", "Asia/Chongqing", "PRC"] {
            assert_eq!(order(Some(tz), Some("en-US"), Some("en-US")), CN, "{tz}");
        }
    }

    #[test]
    fn other_timezones_with_english_put_github_first() {
        for tz in [
            "America/New_York",
            "Europe/Berlin",
            "Asia/Tokyo",
            "Asia/Hong_Kong",
            "Asia/Macau",
            "Asia/Taipei",
            "Asia/Singapore",
        ] {
            assert_eq!(order(Some(tz), Some("en-US"), Some("en-US")), WORLD, "{tz}");
        }
    }

    #[test]
    fn simplified_chinese_language_puts_the_mirror_first_even_abroad() {
        // 系统语言或界面语言任一为简中（大陆）都算；时区在海外（留学生 / 出差）也一样
        assert_eq!(
            order(Some("America/Los_Angeles"), Some("zh-CN"), Some("en-US")),
            CN
        );
        assert_eq!(
            order(Some("Europe/London"), Some("en-GB"), Some("zh-CN")),
            CN
        );
        assert_eq!(order(None, Some("zh-Hans-CN"), None), CN);
        assert_eq!(order(None, Some("zh_CN"), None), CN);
        assert_eq!(order(None, Some("zh-Hans"), None), CN);
        assert_eq!(order(None, Some("zh_CN.UTF-8"), None), CN);
    }

    #[test]
    fn traditional_or_non_mainland_chinese_is_not_a_mirror_signal() {
        for tag in [
            "zh-TW",
            "zh-HK",
            "zh-Hant",
            "zh-Hant-TW",
            "zh-SG",
            "zh-Hans-SG",
            "ja-JP",
        ] {
            assert_eq!(
                order(Some("Asia/Taipei"), Some(tag), Some(tag)),
                WORLD,
                "{tag}"
            );
        }
    }

    #[test]
    fn nothing_known_defaults_to_github_first_and_both_sources_are_always_listed() {
        assert_eq!(order(None, None, None), WORLD);
        for o in [
            order(None, None, None),
            order(Some("Asia/Shanghai"), None, None),
        ] {
            assert_eq!(o.len(), 2);
            assert!(o.contains(&Source::Mirror) && o.contains(&Source::Github));
        }
    }

    #[test]
    fn the_constants_agree_with_tauri_conf() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let eps = conf["plugins"]["updater"]["endpoints"].as_array().unwrap();
        assert_eq!(
            eps.len(),
            1,
            "GitHub 是 tauri.conf.json 里唯一的 endpoint；镜像只在 app_update.rs"
        );
        assert!(eps[0]
            .as_str()
            .unwrap()
            .starts_with(concat!(env!("TAVOTTO_REPO_URL"), "/")));
        let m = Url::parse(MIRROR_MANIFEST_URL).unwrap();
        assert_eq!(m.scheme(), "https");
        assert_eq!(m.host_str(), Some("dl.tavotto.cn"));
    }
}
