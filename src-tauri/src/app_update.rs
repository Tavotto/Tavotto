//! 应用内更新的来源编排（ADR 0121）：国内 COS 镜像与 GitHub Release 按顺序尝试。
//!
//! 为什么不用插件自带的多 endpoints：它只覆盖 **check**，下载仍直连 check 拿到的那个 url；
//! 镜像能查到、包却下不动（或下到了但签名对不上）时用户只会看到失败。这里把「查 + 下」当作一个
//! 来源内的整体，一个来源失败就换下一个来源**重新查、重新下**。
//!
//! 不变的东西（信任不随来源变）：
//! * 验签始终用 `tauri.conf.json` 里的内置公钥——来源只决定「从哪下」，不决定「信谁」。镜像清单的
//!   `signature` 与上游逐字相同，镜像被篡改或过期都只会让验签失败、回退到下一个来源；
//! * 下载只有两个入口：用户按下按钮（`desktop_update_install`），或「后台自动下载」开着时前端在启动查到新版后
//!   调的 `desktop_update_download`（ADR 0121 修订 2026-11-04；偏好在用户配置里，默认开、可关）。下好的包
//!   只放进 [`UpdateState::ready`]，**绝不在运行中替换**；装上去只发生在用户点「现在重启」或退出应用时。
//!
//! 「无更新」不回退：第一个**答上来**的来源说没有，就是没有（ADR 0121 §3）。只有答不上来
//! （超时 / 非 2xx / 清单坏 / 缺本平台）才换下一个来源。

use std::time::{Duration, Instant};

use serde::Serialize;
use tauri_plugin_updater::{Error as UpdaterError, Update, Updater, UpdaterBuilder};
use url::Url;

/// 镜像清单地址（单一常量，调整只改这里与 ADR 0121）。镜像关闭 / 过期时该文件不可用，
/// 客户端自然落到 GitHub。
pub const MIRROR_MANIFEST_URL: &str = "https://dl.tavotto.cn/updater/latest.json";

/// 镜像根 = 清单地址去掉 `/updater/latest.json`（契约 §6.1：`GET {镜像根}/updater/latest.json`）。
pub fn mirror_root() -> &'static str {
    MIRROR_MANIFEST_URL
        .strip_suffix("/updater/latest.json")
        .expect("MIRROR_MANIFEST_URL 必须是 {镜像根}/updater/latest.json")
}

/// 镜像出过一次问题（check 或下载失败）后，本进程内这么久不再优先镜像（契约 §6.3）。
pub const MIRROR_COOLDOWN: Duration = Duration::from_secs(30 * 60);

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
    /// 下载慢时试探另一个来源的判据（`SLOW_RULE`）
    pub slow: SlowRule,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            check: CHECK_TIMEOUT,
            connect: CONNECT_TIMEOUT,
            stall: STALL_TIMEOUT,
            slow: SLOW_RULE,
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
    /// 镜像根（`{根}/updater/latest.json` 的根）；包地址必须落在 `{根}/releases/<version>/` 下
    pub mirror_root: String,
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
    /// 镜像清单里本机平台的包地址不在 `{镜像根}/releases/<version>/` 下（契约 §6.2 #5）：不跟随
    NotOnMirror,
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

/* -------------------------------------------------------------------------- */
/*  镜像清单的取舍（infra 契约 §6.2，参考实现 consume.py::decide_updater）             */
/* -------------------------------------------------------------------------- */

/// 对镜像清单 + 本机平台的结论。向量 `testdata/updater-vectors.json`（来自 infra a7f1000）逐条过。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decision {
    Mirror {
        version: String,
        url: String,
        signature: String,
    },
    /// 不用镜像，附契约里的 reason
    Github(&'static str),
}

/// `doc` = 清单 JSON（拉取失败 / 非 200 / 非 JSON 由调用方按 `None` / 解析失败表达）。
/// 自上而下第一条命中即停；与 consume.py 逐条对应。
pub fn decide_updater(
    doc: Option<&serde_json::Value>,
    platform: &str,
    mirror_base: &str,
) -> Decision {
    let Some(doc) = doc else {
        return Decision::Github("latest_unavailable");
    };
    let version = doc.get("version").and_then(|v| v.as_str());
    let platforms = doc.get("platforms").and_then(|v| v.as_object());
    let (Some(version), Some(platforms)) = (version, platforms) else {
        return Decision::Github("latest_invalid");
    };
    let semver3 = {
        let parts: Vec<&str> = version.split('.').collect();
        parts.len() == 3
            && parts
                .iter()
                .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
    };
    if !semver3 {
        return Decision::Github("latest_invalid");
    }
    let Some(entry) = platforms.get(platform) else {
        return Decision::Github("platform_missing");
    };
    let sig = entry
        .get("signature")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let url = entry.get("url").and_then(|v| v.as_str());
    let (Some(sig), Some(url)) = (sig, url) else {
        return Decision::Github("entry_invalid");
    };
    let prefix = format!("{}/releases/{version}/", mirror_base.trim_end_matches('/'));
    if !url.starts_with(&prefix) {
        return Decision::Github("url_not_on_mirror");
    }
    Decision::Mirror {
        version: version.to_string(),
        url: url.to_string(),
        signature: sig.to_string(),
    }
}

/// 本机平台键：插件 check 出的 `Update.target` 若已是 `os-arch` 就用它（测试里自定义 target），
/// 否则（只有 os）用插件的 `target()`（`darwin-aarch64` 等）。
fn platform_key(update: &Update) -> String {
    if update.target.contains('-') {
        update.target.clone()
    } else {
        tauri_plugin_updater::target().unwrap_or_default()
    }
}

/// 冷却：镜像在本进程里出过问题后，`MIRROR_COOLDOWN` 内顺序里把镜像排到后面，且同一次更新不再回头试镜像。
#[derive(Default)]
pub struct Cooldown(std::sync::Mutex<Option<Instant>>);

impl Cooldown {
    pub fn trip(&self) {
        *self.0.lock().unwrap() = Some(Instant::now());
    }
    pub fn active(&self) -> bool {
        self.0
            .lock()
            .unwrap()
            .is_some_and(|t| t.elapsed() < MIRROR_COOLDOWN)
    }
    /// 冷却中：镜像挪到最后（仍是 GitHub 也不通时的最后手段）
    pub fn apply(&self, order: &[Source]) -> Vec<Source> {
        let mut o = order.to_vec();
        if self.active() && o.len() > 1 {
            o.sort_by_key(|s| matches!(s, Source::Mirror));
        }
        o
    }
}

fn build_updater<F>(
    make: &F,
    endpoints: &Endpoints,
    source: Source,
    limits: &Limits,
) -> Result<Updater, UpdaterError>
where
    F: Fn() -> UpdaterBuilder,
{
    let connect = limits.connect;
    // 镜像不许把我们重定向到别的主机（契约 §6.2 #5）；check 与下载共用这个 client 配置
    let pin_host = (source == Source::Mirror).then(|| {
        (
            endpoints.mirror.host_str().unwrap_or_default().to_string(),
            endpoints.mirror.port_or_known_default(),
        )
    });
    make()
        .endpoints(vec![endpoints.url(source).clone()])?
        .timeout(limits.check)
        .configure_client(move |c| {
            let c = c.connect_timeout(connect);
            match pin_host.clone() {
                Some((host, port)) => c.redirect(reqwest::redirect::Policy::custom(move |a| {
                    if a.url().host_str() == Some(host.as_str())
                        && a.url().port_or_known_default() == port
                    {
                        a.follow()
                    } else {
                        a.stop()
                    }
                })),
                None => c,
            }
        })
        .build()
}

/// 查一个来源。镜像来源的结果还要过 `decide_updater`（包地址必须在镜像上等）；
/// 失败一律归成 `Kind`（原文写 stderr）。
async fn check_one<F>(
    make: &F,
    endpoints: &Endpoints,
    source: Source,
    limits: &Limits,
) -> Result<Option<Update>, Kind>
where
    F: Fn() -> UpdaterBuilder,
{
    let log = |e: &UpdaterError| eprintln!("[updater] {source:?} 检查失败: {e}");
    let updater = build_updater(make, endpoints, source, limits).map_err(|e| {
        log(&e);
        classify(&e)
    })?;
    let found = updater.check().await.map_err(|e| {
        log(&e);
        classify(&e)
    })?;
    if let (Some(update), Source::Mirror) = (&found, source) {
        match decide_updater(
            Some(&update.raw_json),
            &platform_key(update),
            &endpoints.mirror_root,
        ) {
            Decision::Mirror { .. } => {}
            Decision::Github(reason) => {
                eprintln!("[updater] 镜像清单不可用: {reason}");
                return Err(if reason == "url_not_on_mirror" {
                    Kind::NotOnMirror
                } else {
                    Kind::BadManifest
                });
            }
        }
    }
    Ok(found)
}

/// 按顺序查：第一个**答上来**的来源说了算（有更新 → `Some`，没有 → `None`，都不再问下一个）；
/// 答不上来的记一笔、换下一个；全部答不上来才报错。
pub async fn check<F>(
    make: &F,
    endpoints: &Endpoints,
    order: &[Source],
    limits: &Limits,
    cool: &Cooldown,
) -> Result<Option<Pending>, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let mut attempts = Vec::new();
    for &source in order {
        match check_one(make, endpoints, source, limits).await {
            Ok(Some(update)) => {
                return Ok(Some(Pending {
                    update,
                    source,
                    order: order.to_vec(),
                }))
            }
            Ok(None) => return Ok(None),
            Err(kind) => {
                if source == Source::Mirror {
                    cool.trip();
                }
                attempts.push(Attempt {
                    source,
                    stage: Stage::Check,
                    kind,
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

/// 「下载慢」只决定要不要**试探**另一个来源；换不换由实测比速决定（`should_switch`），
/// 所以换来源永远不会让这次下载比不换更慢——最多多花一段 `probe` 的流量。
#[derive(Debug, Clone, Copy)]
pub struct SlowRule {
    /// 第一个字节到了以后先等这么久再判（连接起速）
    pub grace: Duration,
    /// 量当前速度的滑动窗口
    pub window: Duration,
    /// 总长已知：按当前速度剩余时间超过它算慢
    pub eta: Duration,
    /// 总长未知：低于这个速度（字节/秒）算慢
    pub min_rate: u64,
    /// 另一个来源从它的第一个字节起量这么久，再比速
    pub probe: Duration,
}

pub const SLOW_RULE: SlowRule = SlowRule {
    grace: Duration::from_secs(15),
    window: Duration::from_secs(10),
    eta: Duration::from_secs(3 * 60),
    min_rate: 256 * 1024,
    probe: Duration::from_secs(8),
};

/// 按 `rule` 现在算不算慢。`rate` 字节/秒。
pub fn is_slow(rule: &SlowRule, total: Option<u64>, done: u64, rate: f64) -> bool {
    match total {
        Some(t) => rate <= 0.0 || t.saturating_sub(done) as f64 / rate > rule.eta.as_secs_f64(),
        None => rate < rule.min_rate as f64,
    }
}

/// 比速：当前来源已下 `a_done`、速度 `a_rate`；试探的来源已下 `b_done`、速度 `b_rate`。
/// 只有试探来源**从它现在的进度**下完比当前来源下完剩下的明显更快（少两成以上）才换；
/// 总长未知时要求快一半以上。
pub fn should_switch(
    total: Option<u64>,
    a_done: u64,
    a_rate: f64,
    b_done: u64,
    b_rate: f64,
) -> bool {
    if b_rate <= 0.0 {
        return false;
    }
    if a_rate <= 0.0 {
        return true;
    }
    match total {
        Some(t) => {
            let eta_a = t.saturating_sub(a_done) as f64 / a_rate;
            let eta_b = t.saturating_sub(b_done) as f64 / b_rate;
            eta_b < eta_a * 0.8
        }
        None => b_rate > a_rate * 1.5,
    }
}

/// 一路下载的计数（毫秒都相对 `Relay::start`）。
#[derive(Debug, Clone, Copy, Default)]
struct Lane {
    bytes: u64,
    total: Option<u64>,
    first_ms: Option<u64>,
    /// 第一个 chunk 的字节数：量速从它之后算，不把它当成在窗口里收到的
    first_chunk: u64,
    last_ms: Option<u64>,
    started: bool,
    finished: bool,
}

struct Relay<'a> {
    start: Instant,
    emit: Emit<'a>,
    /// `lanes[0]` 当前来源，`lanes[1]` 试探来源；`.2` 是哪一路的进度交给前端
    inner: std::sync::Mutex<([Lane; 2], usize)>,
}

impl<'a> Relay<'a> {
    fn new(emit: Emit<'a>) -> Self {
        Self {
            start: Instant::now(),
            emit,
            inner: std::sync::Mutex::new(([Lane::default(); 2], 0)),
        }
    }
    fn now(&self) -> u64 {
        self.start.elapsed().as_millis() as u64
    }
    fn lock(&self) -> std::sync::MutexGuard<'_, ([Lane; 2], usize)> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }
    fn chunk(&self, lane: usize, n: usize, total: Option<u64>) {
        let now = self.now();
        let mut g = self.lock();
        let active = g.1;
        let l = &mut g.0[lane];
        l.bytes += n as u64;
        l.total = total.or(l.total);
        if l.first_ms.is_none() {
            l.first_ms = Some(now);
            l.first_chunk = n as u64;
        }
        l.last_ms = Some(now);
        if active == lane {
            if !l.started {
                l.started = true;
                (self.emit)(DownloadEvent::Started {
                    content_length: l.total,
                });
            }
            (self.emit)(DownloadEvent::Progress { chunk_length: n });
        }
    }
    fn finished(&self, lane: usize) {
        let mut g = self.lock();
        g.0[lane].finished = true;
        if g.1 == lane {
            (self.emit)(DownloadEvent::Finished);
        }
    }
    /// 把前端的进度换成 `lane` 那一路：再来一次 `Started`（前端据此归零），补上它已经收到的字节。
    fn promote(&self, lane: usize) {
        let mut g = self.lock();
        if g.1 == lane {
            // 已经是它在交进度（接手后下完时会再调一次）
            return;
        }
        g.1 = lane;
        let l = &mut g.0[lane];
        if l.first_ms.is_none() {
            // 还没收到字节：等它的第一个 chunk 再发 `Started`
            return;
        }
        l.started = true;
        (self.emit)(DownloadEvent::Started {
            content_length: l.total,
        });
        if l.bytes > 0 {
            (self.emit)(DownloadEvent::Progress {
                chunk_length: l.bytes as usize,
            });
        }
        if l.finished {
            (self.emit)(DownloadEvent::Finished);
        }
    }
    fn lane(&self, lane: usize) -> Lane {
        self.lock().0[lane]
    }
}

/// 某一路在 `since_ms` 之后的平均速度（字节/秒）；`samples` 是 `(毫秒, 当时已下字节)`，按时间递增。
fn rate_since(
    samples: &std::collections::VecDeque<(u64, u64)>,
    since_ms: u64,
    now: (u64, u64),
) -> f64 {
    let base = samples
        .iter()
        .rev()
        .find(|(t, _)| *t <= since_ms)
        .or(samples.front())
        .copied()
        .unwrap_or((since_ms, 0));
    let dt = now.0.saturating_sub(base.0);
    if dt == 0 {
        return 0.0;
    }
    now.1.saturating_sub(base.1) as f64 * 1000.0 / dt as f64
}

/// 试探来源：重新 check（必须同一个版本）再下载。自带 `Update`，下完连同它一起交回。
enum Challenge {
    Done(Box<Update>, Result<Vec<u8>, Kind>),
    Refused(Kind),
}

async fn challenge<F>(
    make: &F,
    endpoints: &Endpoints,
    limits: &Limits,
    source: Source,
    version: &str,
    relay: &Relay<'_>,
) -> Challenge
where
    F: Fn() -> UpdaterBuilder,
{
    let mut update = match check_one(make, endpoints, source, limits).await {
        Ok(Some(u)) if u.version == version => u,
        Ok(_) => return Challenge::Refused(Kind::VersionMismatch),
        Err(kind) => return Challenge::Refused(kind),
    };
    update.timeout = None;
    let r = update
        .download(|n, t| relay.chunk(1, n, t), || relay.finished(1))
        .await
        .map_err(|e| {
            eprintln!("[updater] {source:?} 下载失败: {e}");
            classify(&e)
        });
    Challenge::Done(Box::new(update), r)
}

/// 一次下载的结局：赢家（已验签的包），或失败；`attempts` 记下这期间失败的来源，
/// `spent` 是这次已经用掉、外层不该再试的来源。
struct Watched {
    won: Option<(Source, Update, Vec<u8>)>,
    attempts: Vec<Attempt>,
    spent: Vec<Source>,
}

/// 下载 `update`（来自 `source`）并**验签**（插件的 `download` 在返回前用内置公钥验签）。
/// 停滞超过 `limits.stall` 放弃；**慢**（`is_slow`）且有 `challenger` 时，一边继续下，一边重新 check
/// 试探来源并同时下载它，量够 `probe` 后比速：`should_switch` 才换过去（丢掉原来那一路），
/// 否则丢掉试探那一路、原来源继续，这次更新不再试探。原来源在试探期间失败 / 停滞，直接换到试探那一路。
#[allow(clippy::too_many_arguments)] // 来源编排的四件上下文 + 这一路的来源 / 包 / 试探对象 / 进度出口
async fn download_watched<F>(
    make: &F,
    endpoints: &Endpoints,
    limits: &Limits,
    source: Source,
    update: Update,
    challenger: Option<Source>,
    emit: Emit<'_>,
    cool: &Cooldown,
) -> Watched
where
    F: Fn() -> UpdaterBuilder,
{
    use std::collections::VecDeque;

    let rule = limits.slow;
    let relay = Relay::new(emit);
    let version = update.version.clone();
    let tick =
        (limits.stall.min(rule.probe) / 4).clamp(Duration::from_millis(10), Duration::from_secs(1));
    let keep_ms = (rule.window.max(rule.probe) * 3).as_millis() as u64;
    let trip = |s: Source| {
        if s == Source::Mirror {
            cool.trip();
        }
    };

    let mut attempts = Vec::new();
    let mut spent = vec![source];
    let mut a_fut = Some(Box::pin(
        update.download(|n, t| relay.chunk(0, n, t), || relay.finished(0)),
    ));
    let start_b =
        |next: Source| Box::pin(challenge(make, endpoints, limits, next, &version, &relay));
    // 只为让编译器知道试探那一路 future 的类型：async fn 的类型写不出名字
    fn none_of<T>(_: impl FnOnce() -> T) -> Option<T> {
        None
    }
    let mut b_fut = none_of(|| start_b(Source::Github));
    let mut b_source = None;
    let mut probe_from = 0u64;
    let mut probed = false;
    let mut samples: [VecDeque<(u64, u64)>; 2] = Default::default();

    enum End {
        A(Result<Vec<u8>, Kind>),
        B(Option<Box<Update>>, Result<Vec<u8>, Kind>),
    }
    let end = loop {
        // 交给前端的那一路就是「当前」那一路；它的停滞看门狗才算数
        let active = if a_fut.is_some() { 0 } else { 1 };
        tokio::select! {
            r = async { a_fut.as_mut().unwrap().as_mut().await }, if a_fut.is_some() => {
                a_fut = None;
                match r {
                    Ok(bytes) => break End::A(Ok(bytes)),
                    Err(e) => {
                        eprintln!("[updater] {source:?} 下载失败: {e}");
                        let kind = classify(&e);
                        if b_fut.is_none() {
                            break End::A(Err(kind));
                        }
                        trip(source);
                        attempts.push(Attempt { source, stage: Stage::Download, kind });
                        spent.extend(b_source);
                        relay.promote(1);
                    }
                }
            }
            c = async { b_fut.as_mut().unwrap().as_mut().await }, if b_fut.is_some() => {
                b_fut = None;
                let b = b_source.expect("试探来源");
                let (upd, kind) = match c {
                    Challenge::Done(u, Ok(bytes)) => {
                        // 试探期间就下完了：它更快，直接用
                        a_fut = None;
                        spent.push(b);
                        relay.promote(1);
                        break End::B(Some(u), Ok(bytes));
                    }
                    Challenge::Done(u, Err(k)) => (Some(u), k),
                    Challenge::Refused(k) => (None, k),
                };
                trip(b);
                spent.push(b);
                let stage = if upd.is_some() { Stage::Download } else { Stage::Check };
                if a_fut.is_none() {
                    attempts.push(Attempt { source: b, stage, kind });
                    break End::B(upd, Err(kind));
                }
                // 原来源还在下：试探失败不碍事，记一笔（原来源也失败时一起报），继续
                attempts.push(Attempt { source: b, stage, kind });
            }
            _ = tokio::time::sleep(tick) => {
                let now = relay.now();
                for (i, s) in samples.iter_mut().enumerate() {
                    let l = relay.lane(i);
                    // 从第一个字节起量：窗口比已下时长还长时，不拿半截窗口当速度
                    if let (true, Some(first)) = (s.is_empty(), l.first_ms) {
                        s.push_back((first, l.first_chunk));
                    }
                    s.push_back((now, l.bytes));
                    while s.front().is_some_and(|(t, _)| now.saturating_sub(*t) > keep_ms) {
                        s.pop_front();
                    }
                }
                let a = relay.lane(active);
                let quiet_since = a.last_ms.unwrap_or(if active == 0 { 0 } else { probe_from });
                if now.saturating_sub(quiet_since) >= limits.stall.as_millis() as u64 {
                    eprintln!("[updater] 下载停滞超过 {:?}", limits.stall);
                    if active == 0 && b_fut.is_some() {
                        a_fut = None;
                        trip(source);
                        attempts.push(Attempt { source, stage: Stage::Download, kind: Kind::Stalled });
                        spent.extend(b_source);
                        relay.promote(1);
                        continue;
                    }
                    break if active == 0 {
                        End::A(Err(Kind::Stalled))
                    } else {
                        // 试探那一路已经接手又停了：它的 Update 在 future 里，跟着丢掉
                        b_fut = None;
                        let b = b_source.expect("试探来源");
                        trip(b);
                        attempts.push(Attempt { source: b, stage: Stage::Download, kind: Kind::Stalled });
                        End::B(None, Err(Kind::Stalled))
                    };
                }
                if active != 0 {
                    continue;
                }
                if b_fut.is_some() {
                    let b = relay.lane(1);
                    let Some(b_first) = b.first_ms else {
                        // 试探来源迟迟不出字节：check 超时 + 一个 probe 之后放弃它
                        if now.saturating_sub(probe_from)
                            >= (limits.check + limits.connect + rule.probe).as_millis() as u64
                        {
                            b_fut = None;
                        }
                        continue;
                    };
                    if now.saturating_sub(b_first) < rule.probe.as_millis() as u64 {
                        continue;
                    }
                    let a_rate = rate_since(&samples[0], b_first, (now, a.bytes));
                    let b_rate = rate_since(&samples[1], b_first, (now, b.bytes));
                    let total = a.total.or(b.total);
                    if should_switch(total, a.bytes, a_rate, b.bytes, b_rate) {
                        eprintln!(
                            "[updater] {source:?} {a_rate:.0} B/s 太慢，换到 {:?}（{b_rate:.0} B/s）",
                            b_source
                        );
                        a_fut = None;
                        spent.extend(b_source);
                        relay.promote(1);
                    } else {
                        eprintln!(
                            "[updater] 试探 {:?}（{b_rate:.0} B/s）不比 {source:?}（{a_rate:.0} B/s）快，留在原来源",
                            b_source
                        );
                        b_fut = None;
                    }
                    continue;
                }
                let (Some(next), false, Some(a_first)) = (challenger, probed, a.first_ms) else {
                    continue;
                };
                if now.saturating_sub(a_first) < rule.grace.as_millis() as u64 {
                    continue;
                }
                let since = now.saturating_sub(rule.window.as_millis() as u64);
                let rate = rate_since(&samples[0], since, (now, a.bytes));
                if is_slow(&rule, a.total, a.bytes, rate) {
                    eprintln!("[updater] {source:?} 下载慢（{rate:.0} B/s），试探 {next:?}");
                    probed = true;
                    probe_from = now;
                    b_source = Some(next);
                    samples[1].clear();
                    b_fut = Some(start_b(next));
                }
            }
        }
    };
    drop(a_fut);
    drop(b_fut);
    let won = match end {
        End::A(Ok(bytes)) => Some((source, update, bytes)),
        End::B(Some(u), Ok(bytes)) => Some((b_source.expect("试探来源"), *u, bytes)),
        End::A(Err(kind)) => {
            trip(source);
            attempts.push(Attempt {
                source,
                stage: Stage::Download,
                kind,
            });
            None
        }
        End::B(_, _) => None,
    };
    Watched {
        won,
        attempts,
        spent,
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
/// 真正装上去的必须是同一版。下载**慢**时先试探下一个来源、实测更快才换（`download_watched`）。
/// 返回的包已用内置公钥验过签。
pub async fn fetch<F>(
    make: &F,
    endpoints: &Endpoints,
    limits: &Limits,
    pending: &Pending,
    emit: Emit<'_>,
    cool: &Cooldown,
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
    let mut spent: Vec<Source> = Vec::new();
    // 契约 §6.3：镜像已经出过问题，这一次更新不回头再试它；这次已经用掉的来源也不再试
    let skip = |i: usize, s: Source, spent: &[Source]| {
        spent.contains(&s) || (i > 0 && s == Source::Mirror && cool.active())
    };
    for (i, &source) in seq.iter().enumerate() {
        if skip(i, source, &spent) {
            continue;
        }
        let mut update = if i == 0 {
            pending.update.clone()
        } else {
            match check_one(make, endpoints, source, limits).await {
                Ok(Some(u)) if u.version == pending.update.version => u,
                other => {
                    let kind = match other {
                        Err(kind) => kind,
                        Ok(_) => Kind::VersionMismatch,
                    };
                    if source == Source::Mirror {
                        cool.trip();
                    }
                    spent.push(source);
                    attempts.push(Attempt {
                        source,
                        stage: Stage::Check,
                        kind,
                    });
                    continue;
                }
            }
        };
        // check 的 10 s 总超时（`UpdaterBuilder::timeout`）不许带进下载：大包持续在传也会被掐断。
        // 下载只受连接超时与停滞看门狗约束；插件现版本不会把它带进 `Update`，这里显式清掉防升级后回归。
        update.timeout = None;
        let challenger = seq
            .iter()
            .enumerate()
            .skip(i + 1)
            .find(|(j, s)| !skip(*j, **s, &spent))
            .map(|(_, s)| *s);
        let w = download_watched(
            make, endpoints, limits, source, update, challenger, emit, cool,
        )
        .await;
        attempts.extend(w.attempts);
        spent.extend(w.spent);
        if let Some((source, update, bytes)) = w.won {
            return Ok(Fetched {
                source,
                update,
                bytes,
            });
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

/// 上一次 check 留下的句柄。同一时刻只允许一个 check / install / download 在跑（`try_lock`，撞上回 `busy`）。
///
/// `ready` = 后台已下好、验过签、**还没装**的包（字节在内存里，Windows 约 157 MB，ADR 0121 修订 2026-11-04）。
/// 用 std 的锁：退出路径（`RunEvent::Exit`，同步）要取它。装上之前一直留着，装上（或装失败）即被取走。
#[derive(Default)]
pub struct UpdateState {
    pub pending: tokio::sync::Mutex<Option<Pending>>,
    pub ready: std::sync::Mutex<Option<Fetched>>,
    pub cooldown: Cooldown,
}

/// `desktop_update_download` 的返回：下好的是哪一版、从哪个来源。
#[derive(Debug, Clone, Serialize)]
pub struct Downloaded {
    pub version: String,
    pub source: Source,
}

/// 取出已下好的包，**只在版本与 `version` 相同时**（用户看到的版本 == 装上的版本）；不同版本原样留着。
pub fn take_ready(state: &UpdateState, version: &str) -> Option<Fetched> {
    let mut g = state.ready.lock().unwrap_or_else(|e| e.into_inner());
    if g.as_ref().is_some_and(|f| f.update.version == version) {
        g.take()
    } else {
        None
    }
}

/// 退出时用：取出已下好的包（不论版本——它是比当前新的、验过签的一版）。
pub fn take_ready_any(state: &UpdateState) -> Option<Fetched> {
    state.ready.lock().unwrap_or_else(|e| e.into_inner()).take()
}

/// 退出应用时安装后台下好的包。调用方必须已经收掉 sidecar 并写过 `clean`（`RunEvent::Exit` 里的既有顺序），
/// 因为 Windows 上插件的 install 会起安装器并自行退出进程。没有已下好的包 = 什么都不做。失败只记日志——
/// 应用本来就在退出，下次启动会重新查。
pub fn install_ready_on_exit(state: &UpdateState) {
    if let Some(f) = take_ready_any(state) {
        let _ = apply(f);
    }
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
    Ok(Endpoints {
        mirror,
        mirror_root: mirror_root().to_string(),
        github,
    })
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
    let order = state.cooldown.apply(order);
    let found = check(make, endpoints, &order, limits, &state.cooldown).await?;
    let out = found.as_ref().map(|p| CheckedUpdate {
        version: p.update.version.clone(),
        notes: p.update.body.clone(),
        date: p.update.date.and_then(|d| {
            d.format(&time::format_description::well_known::Rfc3339)
                .ok()
        }),
        source: p.source,
    });
    // 已下好的包只对应「现在查到的这一版」：查到的是别的版本 / 没有新版，旧包作废（别白占内存，也别在退出时装旧的）
    {
        let keep = found.as_ref().map(|p| p.update.version.as_str());
        let mut ready = state.ready.lock().unwrap_or_else(|e| e.into_inner());
        if ready.as_ref().map(|f| f.update.version.as_str()) != keep {
            *ready = None;
        }
    }
    *slot = found;
    Ok(out)
}

/// 后台下载 `pending` 并放进 `state.ready`（不安装）。与 [`run_install`] 同一把锁：下载期间别的 check / install
/// 回 `busy`。已经有同一版本的包就直接回，不再请求包地址。失败不动任何状态（用户仍可手动下载）。
pub async fn run_download<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    limits: &Limits,
    emit: Emit<'_>,
) -> Result<Downloaded, Failure>
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
    {
        let ready = state.ready.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(f) = ready
            .as_ref()
            .filter(|f| f.update.version == pending.update.version)
        {
            return Ok(Downloaded {
                version: f.update.version.clone(),
                source: f.source,
            });
        }
    }
    let f = fetch(make, endpoints, limits, &pending, emit, &state.cooldown).await?;
    let out = Downloaded {
        version: f.update.version.clone(),
        source: f.source,
    };
    *state.ready.lock().unwrap_or_else(|e| e.into_inner()) = Some(f);
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
    run_install_expecting(make, endpoints, state, limits, None, emit).await
}

/// 与 [`run_install`] 同一把锁、同一个候选句柄；多一道**候选身份**核对：`expect` 是用户在界面上看到并确认的
/// 版本号，句柄里的版本与它不一致（期间又 check 过、换了候选）就拒绝，回 `candidate_changed`，不下载、不安装。
/// 救援页用它（`rescue.rs`）：确认后装的必须就是检查到的那一个版本。`None` = 不核对（沿用旧入口的行为）。
pub async fn run_install_expecting<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    limits: &Limits,
    expect: Option<&str>,
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
    if let Some(want) = expect {
        if pending.update.version != want {
            return Err(Failure::code("candidate_changed"));
        }
    }
    install_pending(make, endpoints, state, limits, &pending, emit, apply).await
}

/// 装 `pending`：后台已下好**同一版本**的包就直接用它（不再请求包地址），否则现下现装。
/// `install` 是真正动磁盘的那一步（生产里是 [`apply`]），拆出来是为了让测试不碰磁盘上的应用。
pub(crate) async fn install_pending<F, A>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    limits: &Limits,
    pending: &Pending,
    emit: Emit<'_>,
    install: A,
) -> Result<Source, Failure>
where
    F: Fn() -> UpdaterBuilder,
    A: FnOnce(Fetched) -> Result<Source, Failure>,
{
    if let Some(f) = take_ready(state, &pending.update.version) {
        return install(f);
    }
    // 句柄保留到成功：失败后用户可以直接再按一次
    install(fetch(make, endpoints, limits, pending, emit, &state.cooldown).await?)
}

/// 救援页「重新安装」专用的 `UpdaterBuilder` 工厂：在 `make` 之上换一个版本判据——**同版本也算可装**
/// （`>=`，而不是插件默认的 `>`）。只由 [`run_reinstall`] 用；普通更新路径（`run_check` / `desktop_update_*`）
/// 传进来的永远是不带这个判据的 `make`，「只装更新版本」的语义因此一点不变。
/// 清单比当前版本旧（含镜像滞后）仍然判「不可装」——不降级。
pub fn reinstall_builder<F>(make: &F) -> impl Fn() -> UpdaterBuilder + '_
where
    F: Fn() -> UpdaterBuilder,
{
    move || make().version_comparator(|current, remote| remote.version >= current)
}

/// 重新安装的「查」：同 [`run_check`]（同一把锁、同一个候选句柄、同一套来源编排），但允许同版本。
/// 第一个答上来的来源说「没有可装的包」（清单比当前版本旧）即终局，回稳定码 `reinstall_unavailable`。
pub async fn run_reinstall_check<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    order: &[Source],
    limits: &Limits,
) -> Result<CheckedUpdate, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let same_ok = reinstall_builder(make);
    run_check(&same_ok, endpoints, state, order, limits)
        .await?
        .ok_or_else(|| Failure::code("reinstall_unavailable"))
}

/// 重新安装 = 允许同版本的 check，再装刚查到的那一版（`run_install_expecting` 核对版本）。
/// 回退来源重新 check 时用的也是同一个放宽过的工厂，且仍必须报同一个版本。
/// 无论成败，结束时清掉候选句柄：这个「同版本候选」不能留给普通的救援更新命令去装。
pub async fn run_reinstall<F>(
    make: &F,
    endpoints: &Endpoints,
    state: &UpdateState,
    order: &[Source],
    limits: &Limits,
    emit: Emit<'_>,
) -> Result<Source, Failure>
where
    F: Fn() -> UpdaterBuilder,
{
    let same_ok = reinstall_builder(make);
    let result = async {
        let found = run_reinstall_check(make, endpoints, state, order, limits).await?;
        run_install_expecting(
            &same_ok,
            endpoints,
            state,
            limits,
            Some(&found.version),
            emit,
        )
        .await
    }
    .await;
    // 撞上别人在跑（`busy`）时那把锁与句柄不是我们的，不动
    if !matches!(&result, Err(f) if f.code == "busy") {
        if let Ok(mut slot) = state.pending.try_lock() {
            *slot = None;
        }
    }
    result
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
    fn slow_means_a_long_remaining_time_or_a_low_rate_when_the_size_is_unknown() {
        let r = SLOW_RULE;
        // 190 MB 剩 180 MB：1 MB/s = 3 分钟整，不算慢；500 KB/s 算慢
        let (total, done) = (Some(190_000_000), 10_000_000);
        assert!(!is_slow(&r, total, done, 1_000_000.0));
        assert!(is_slow(&r, total, done, 500_000.0));
        assert!(is_slow(&r, total, done, 0.0));
        assert!(!is_slow(&r, total, 189_999_000, 10_000.0), "快下完了不算慢");
        assert!(is_slow(&r, None, done, 100_000.0));
        assert!(!is_slow(&r, None, done, 1_000_000.0));
    }

    #[test]
    fn switching_requires_the_other_source_to_finish_clearly_sooner_from_scratch() {
        let t = Some(100_000_000);
        // 当前 100 KB/s 下了一半（剩 500 s）；对方 1 MB/s 从头（100 s）→ 换
        assert!(should_switch(t, 50_000_000, 100_000.0, 0, 1_000_000.0));
        // 对方快一倍、但要从头下（500 s 对剩下的 500 s）→ 不换
        assert!(!should_switch(t, 50_000_000, 100_000.0, 0, 200_000.0));
        // 只快一点（不到两成）→ 不换
        assert!(!should_switch(t, 0, 100_000.0, 0, 110_000.0));
        assert!(should_switch(t, 0, 100_000.0, 0, 130_000.0));
        // 对方没出字节 → 不换；当前 0 速、对方有速 → 换
        assert!(!should_switch(t, 0, 100_000.0, 0, 0.0));
        assert!(should_switch(t, 0, 0.0, 0, 1.0));
        // 总长未知：要快一半以上
        assert!(!should_switch(None, 0, 100.0, 0, 140.0));
        assert!(should_switch(None, 0, 100.0, 0, 160.0));
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
        assert_eq!(mirror_root(), "https://dl.tavotto.cn");
        assert_eq!(m.host_str(), Some("dl.tavotto.cn"));
    }

    /// infra 契约 §6.2 的向量（`testdata/updater-vectors.json`，复制自 Tavotto/infra 提交 a7f1000
    /// 的 `docs/release-mirror/examples/updater-vectors.json`），逐条过 `decide_updater`。
    #[test]
    fn the_infra_contract_vectors_all_pass() {
        let vectors: serde_json::Value =
            serde_json::from_str(include_str!("../testdata/updater-vectors.json")).unwrap();
        assert_eq!(vectors["schema_version"], 1);
        let list = vectors["vectors"].as_array().unwrap();
        assert!(list.len() >= 11, "向量少了：{}", list.len());
        for v in list {
            let name = v["name"].as_str().unwrap();
            // latest: null = 拉取失败；字符串 = 响应体，解析不了 = 非法 JSON（与参考实现一致）
            let parsed: Option<serde_json::Value> = match &v["latest"] {
                serde_json::Value::Null => None,
                serde_json::Value::String(t) => Some(
                    serde_json::from_str(t)
                        .unwrap_or(serde_json::Value::String("<not json>".into())),
                ),
                other => panic!("{name}: latest 形状不认得 {other}"),
            };
            let got = decide_updater(
                parsed.as_ref(),
                v["platform"].as_str().unwrap(),
                v["mirror_base"].as_str().unwrap(),
            );
            let e = &v["expect"];
            let want = if e["mode"] == "mirror" {
                Decision::Mirror {
                    version: e["version"].as_str().unwrap().into(),
                    url: e["url"].as_str().unwrap().into(),
                    signature: e["signature"].as_str().unwrap().into(),
                }
            } else {
                Decision::Github(match e["reason"].as_str().unwrap() {
                    "latest_unavailable" => "latest_unavailable",
                    "latest_invalid" => "latest_invalid",
                    "platform_missing" => "platform_missing",
                    "entry_invalid" => "entry_invalid",
                    "url_not_on_mirror" => "url_not_on_mirror",
                    r => panic!("{name}: 不认得的 reason {r}"),
                })
            };
            assert_eq!(got, want, "{name}");
        }
    }
}
