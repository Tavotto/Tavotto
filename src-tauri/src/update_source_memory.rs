//! 大陆环境「后台静默下载」该先走 GitHub 还是改走镜像的跨重启记忆（ADR 0121 修订 2026-11-05）。
//!
//! 后台下载在大陆环境先走 GitHub、不因为慢而换来源；只有两种情况才改走镜像：
//! * **失败累计**：同一个新版本上，后台走 GitHub 连续失败 [`FAILURES_BEFORE_MIRROR`] 次；
//! * **超期**：新版本发布已超过 [`OVERDUE_AFTER`] 仍没下好（启动一次、被退出打断一次也算「试过」，
//!   所以「慢到下不完」不会因为从不报错而永远卡在 GitHub）。
//!
//! 这两个数要跨重启才有意义（一次启动里后台只下一次），所以落一个小文件
//! `app_config_dir()/update-source.json`（与 `desktop-port`、`session-state.json` 同一处：这台机器上这个人的
//! 状态，不进项目数据）。内容只有版本号、首次见到的时刻与两个计数，**不含任何路径或标识符**。
//! 读写失败一律静默：读不到 = 没有记录（等于从头来），写不进 = 这次不记——最坏只是多试几次 GitHub。
//!
//! 策略全是纯函数（[`observe`] / [`prefer_mirror`] / [`outcome_of`] / [`record`]），文件读写只在 [`SourceMemory`]。

use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

/// 记忆文件名，放在 `app_config_dir()`。
pub const FILE_NAME: &str = "update-source.json";
/// 同一版本上后台走 GitHub 连续失败这么多次，就改走镜像。
pub const FAILURES_BEFORE_MIRROR: u32 = 3;
/// 新版本发布超过这么久（且后台已试过至少一次）仍没下好，就改走镜像。
pub const OVERDUE_AFTER: Duration = Duration::from_secs(3 * 24 * 3600);

pub fn path(config_dir: &Path) -> PathBuf {
    config_dir.join(FILE_NAME)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Memory {
    /// 记的是哪个版本；换了新版本，计数从头来
    pub version: String,
    /// 本机第一次见到这个版本的时刻（Unix 秒）——清单里取不到发布时间时的保守回退
    pub first_seen: i64,
    /// 后台已开始过几次下载（含被退出打断的）
    #[serde(default)]
    pub tries: u32,
    /// 其中判定为 GitHub 失败的次数；一次 GitHub 下成功就清零（整条记录删掉）
    #[serde(default)]
    pub failures: u32,
}

/// 为什么改走镜像（日志用）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reason {
    Failures,
    Overdue,
}

/// 一次后台下载对「GitHub 好不好用」的结论。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// GitHub 下成功了
    GithubOk,
    /// GitHub 没给出这个包（check / 下载失败、停滞、验签不过）
    GithubFailed,
    /// 与 GitHub 无关：这次没走 GitHub（镜像优先）或它只是报了别的版本——不加也不清
    Neutral,
}

/// 文件内容 → 记忆。任何不合规一律当「没有记录」。
pub fn parse(text: &str) -> Option<Memory> {
    serde_json::from_str::<Memory>(text.trim()).ok()
}

pub fn render(m: &Memory) -> String {
    format!("{}\n", serde_json::to_string(m).unwrap_or_default())
}

/// 本次后台下载的是 `version`：记忆里是别的版本（或没有）就换成一条新记录。
pub fn observe(prev: Option<Memory>, version: &str, now: i64) -> Memory {
    match prev {
        Some(m) if m.version == version => m,
        _ => Memory {
            version: version.to_string(),
            first_seen: now,
            tries: 0,
            failures: 0,
        },
    }
}

/// 要不要改走镜像。`published` = 清单里的发布时间（Unix 秒），取不到就用 `first_seen`。
/// 「超期」要求之前后台至少试过一次：隔了一周才打开应用的人，新版在 GitHub 上早已超过三天，
/// 但这台机器还没试过，不该一上来就绕过 GitHub。
pub fn prefer_mirror(m: &Memory, published: Option<i64>, now: i64) -> Option<Reason> {
    if m.failures >= FAILURES_BEFORE_MIRROR {
        return Some(Reason::Failures);
    }
    let since = published.unwrap_or(m.first_seen);
    let age = now.saturating_sub(since);
    if m.tries >= 1 && age >= OVERDUE_AFTER.as_secs() as i64 {
        return Some(Reason::Overdue);
    }
    None
}

/// 这一次后台下载的结论：`won` = 最后包是哪个来源给的（没下成 = `None`；边缘节点 / 镜像赢都不是 GitHub 赢，同样对待），`github_failed` =
/// 过程中 GitHub 那一环出过问题（check / 下载失败、停滞、验签不过；**报了别的版本不算**，那是镜像滞后，不是 GitHub 坏）。
pub fn outcome_of(won: Option<crate::app_update::Source>, github_failed: bool) -> Outcome {
    use crate::app_update::Source;
    if won == Some(Source::Github) {
        return Outcome::GithubOk;
    }
    if github_failed {
        return Outcome::GithubFailed;
    }
    Outcome::Neutral
}

/// 记下一次结论。`None` = 把这条记录删掉（GitHub 通了，从头来）。
pub fn record(mut m: Memory, outcome: Outcome) -> Option<Memory> {
    match outcome {
        Outcome::GithubOk => None,
        Outcome::GithubFailed => {
            m.failures = m.failures.saturating_add(1);
            Some(m)
        }
        Outcome::Neutral => Some(m),
    }
}

#[derive(Default)]
struct Inner {
    path: Option<PathBuf>,
    loaded: bool,
    data: Option<Memory>,
}

/// 文件读写所在；`Default` = 纯内存（测试、e2e 触发口），不碰磁盘。
#[derive(Default)]
pub struct SourceMemory {
    inner: std::sync::Mutex<Inner>,
    /// 测试里钉住「现在」；`None` = 真实时钟
    now_override: std::sync::Mutex<Option<i64>>,
}

impl SourceMemory {
    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// 绑定文件位置（幂等；第一次绑定时读入）。`None` = 保持纯内存。
    pub fn bind(&self, path: Option<PathBuf>) {
        let mut g = self.lock();
        if g.path.is_none() {
            if let Some(p) = path {
                if !g.loaded {
                    g.data = std::fs::read_to_string(&p).ok().and_then(|t| parse(&t));
                    g.loaded = true;
                }
                g.path = Some(p);
            }
        }
    }

    pub fn now(&self) -> i64 {
        if let Some(t) = *self.now_override.lock().unwrap_or_else(|e| e.into_inner()) {
            return t;
        }
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0)
    }

    #[cfg(test)]
    pub fn set_now(&self, t: i64) {
        *self.now_override.lock().unwrap() = Some(t);
    }

    pub fn snapshot(&self) -> Option<Memory> {
        self.lock().data.clone()
    }

    pub fn put(&self, m: Option<Memory>) {
        let mut g = self.lock();
        g.data = m;
        g.loaded = true;
        if let Some(p) = g.path.clone() {
            match &g.data {
                Some(m) => {
                    if let Some(dir) = p.parent() {
                        let _ = std::fs::create_dir_all(dir);
                    }
                    let _ = std::fs::write(&p, render(m));
                }
                None => {
                    let _ = std::fs::remove_file(&p);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app_update::Source;

    const DAY: i64 = 24 * 3600;

    fn mem(tries: u32, failures: u32, first_seen: i64) -> Memory {
        Memory {
            version: "1.2.3".into(),
            first_seen,
            tries,
            failures,
        }
    }

    #[test]
    fn a_new_version_starts_a_fresh_record_and_the_same_version_keeps_its_counts() {
        let old = mem(2, 2, 100);
        let same = observe(Some(old.clone()), "1.2.3", 999);
        assert_eq!(same, old);
        let fresh = observe(Some(old), "1.2.4", 999);
        assert_eq!(
            (
                fresh.version.as_str(),
                fresh.first_seen,
                fresh.tries,
                fresh.failures
            ),
            ("1.2.4", 999, 0, 0)
        );
        assert_eq!(observe(None, "1.2.3", 7).first_seen, 7);
    }

    #[test]
    fn three_github_failures_switch_to_the_mirror_two_do_not() {
        let now = 1_000_000;
        assert_eq!(prefer_mirror(&mem(2, 2, now), Some(now), now), None);
        assert_eq!(
            prefer_mirror(&mem(3, 3, now), Some(now), now),
            Some(Reason::Failures)
        );
        assert_eq!(
            prefer_mirror(&mem(0, 4, now), Some(now), now),
            Some(Reason::Failures),
            "超过阈值也算"
        );
    }

    #[test]
    fn an_update_older_than_three_days_that_was_tried_switches_to_the_mirror() {
        let now = 10 * DAY;
        // 发布 3 天前整，试过一次 → 超期
        assert_eq!(
            prefer_mirror(&mem(1, 0, now), Some(now - 3 * DAY), now),
            Some(Reason::Overdue)
        );
        // 差一秒不到 3 天 → 还不算
        assert_eq!(
            prefer_mirror(&mem(1, 0, now), Some(now - 3 * DAY + 1), now),
            None
        );
    }

    #[test]
    fn overdue_needs_a_previous_try_so_a_long_absent_user_still_tries_github_first() {
        let now = 30 * DAY;
        assert_eq!(
            prefer_mirror(&mem(0, 0, now), Some(now - 20 * DAY), now),
            None
        );
    }

    #[test]
    fn without_a_publish_time_the_first_seen_time_is_the_conservative_fallback() {
        let now = 10 * DAY;
        // 没有发布时间：从本机第一次见到算起，4 天前见到 + 试过 → 超期；1 天前见到 → 不超期
        assert_eq!(
            prefer_mirror(&mem(1, 0, now - 4 * DAY), None, now),
            Some(Reason::Overdue)
        );
        assert_eq!(prefer_mirror(&mem(1, 0, now - DAY), None, now), None);
        // 有发布时间就以它为准（哪怕首次见到很近）
        assert_eq!(
            prefer_mirror(&mem(1, 0, now), Some(now - 4 * DAY), now),
            Some(Reason::Overdue)
        );
    }

    #[test]
    fn outcomes_count_only_real_github_failures_and_a_github_success_clears_everything() {
        let m = mem(2, 2, 0);
        assert_eq!(record(m.clone(), Outcome::GithubOk), None);
        assert_eq!(
            record(m.clone(), Outcome::GithubFailed).unwrap().failures,
            3
        );
        assert_eq!(record(m.clone(), Outcome::Neutral).unwrap(), m);
        assert_eq!(outcome_of(Some(Source::Github), false), Outcome::GithubOk);
        assert_eq!(
            outcome_of(Some(Source::Github), true),
            Outcome::GithubOk,
            "最后还是 GitHub 下成的（比如第一次失败又自己重试）"
        );
        assert_eq!(
            outcome_of(Some(Source::Mirror), true),
            Outcome::GithubFailed
        );
        assert_eq!(outcome_of(None, true), Outcome::GithubFailed);
        assert_eq!(
            outcome_of(Some(Source::Mirror), false),
            Outcome::Neutral,
            "没走 GitHub，或它只是报了别的版本：不算它坏"
        );
    }

    #[test]
    fn an_edge_win_is_booked_like_a_mirror_win() {
        // GitHub 先失败、节点兜住：算 GitHub 失败（计数 +1），与镜像兜住完全一样
        assert_eq!(outcome_of(Some(Source::Edge), true), Outcome::GithubFailed);
        // 节点优先（改道后）直接赢、没碰 GitHub / GitHub 只是报了别的版本：不加也不清
        assert_eq!(outcome_of(Some(Source::Edge), false), Outcome::Neutral);
        let m = mem(2, 2, 0);
        // 连续失败的判据不被节点破坏：节点兜住的第三次仍把计数推到 3 → 改道（节点优先）
        let m3 = record(m.clone(), outcome_of(Some(Source::Edge), true)).unwrap();
        assert_eq!(prefer_mirror(&m3, Some(0), 0), Some(Reason::Failures));
        // 改道后节点赢（GitHub 没试）不清零、不再加
        assert_eq!(
            record(m3.clone(), outcome_of(Some(Source::Edge), false)),
            Some(m3)
        );
    }

    #[test]
    fn the_file_round_trips_and_garbage_reads_as_no_record() {
        let m = mem(2, 1, 42);
        assert_eq!(parse(&render(&m)), Some(m));
        assert_eq!(parse("not json"), None);
        assert_eq!(parse(""), None);
        // 旧文件缺计数字段 → 补 0
        let p = parse(r#"{"version":"1.0.0","first_seen":5}"#).unwrap();
        assert_eq!((p.tries, p.failures), (0, 0));
    }

    #[test]
    fn the_store_survives_a_restart_and_silently_ignores_unwritable_paths() {
        let dir = std::env::temp_dir().join(format!("tavotto-usm-{}", std::process::id()));
        let file = path(&dir);
        let a = SourceMemory::default();
        a.bind(Some(file.clone()));
        a.put(Some(mem(1, 2, 3)));
        let b = SourceMemory::default();
        b.bind(Some(file.clone()));
        assert_eq!(b.snapshot(), Some(mem(1, 2, 3)), "重启后读回来");
        b.put(None);
        assert!(!file.exists(), "清掉 = 删文件");
        let _ = std::fs::remove_dir_all(&dir);

        // 路径写不进（父目录是个文件）：不 panic，内存里照常有
        let blocker = std::env::temp_dir().join(format!("tavotto-usm-f-{}", std::process::id()));
        std::fs::write(&blocker, b"x").unwrap();
        let c = SourceMemory::default();
        c.bind(Some(blocker.join("update-source.json")));
        c.put(Some(mem(0, 1, 1)));
        assert_eq!(c.snapshot(), Some(mem(0, 1, 1)));
        let _ = std::fs::remove_file(&blocker);
    }
}
