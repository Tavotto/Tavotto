//! 桌面 sidecar 的端口记忆（issue #715 PR-A，ADR 0108）。
//!
//! **为什么要记端口**：桌面窗口加载的是 `http://127.0.0.1:<端口>/`，而端口是 Web Storage
//! origin 的一部分。端口一变，前端存在 localStorage 里的东西（崩溃兜底副本
//! `tavotto.autosave.<id>` / `tavotto.recovery.<id>`、教程进度、界面偏好……）就全部换了一个
//! 空存储——issue #715 在 Windows 上实测 5 次启动留下 5 个不同端口的 origin。
//!
//! **策略**（全是纯函数，文件读写只在 [`load`] / [`store`] 两处）：
//! - 首次（没有记录，或记录坏了）在 [`FIRST_CHOICE`] 里随机挑一个——这一段落在三个平台的
//!   临时端口段（Linux 32768–60999、Windows / macOS 49152–65535）之外，被别的程序随手占掉的
//!   机会最小；
//! - 有记录就把它作为 stdin 首行的 `preferred_port` 交给 sidecar（sidecar 占不到时自己重试、
//!   再退回系统分配，见 `src/tavotto/desktop.py`）；
//! - 被占时**本次**用 sidecar 实际拿到的端口，记住的端口**不变**，只记一次落空；
//!   **连续 [`MISSES_BEFORE_RELEARN`] 次**落空才改记为实际端口——偶尔被占一次就改记，
//!   等于把 origin 换掉，正是这里要防的事。
//!
//! stdin 字段名与合法范围是与 Python 消费方的严格同源对：两侧各读
//! `tests/golden/desktop_preferred_port.json`，不读对方源码。

use std::ops::RangeInclusive;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// 记忆文件名，放在 `app_config_dir()`，与 `menu-locale` 同一处（这台机器上这个人的设置，
/// 不进项目数据）。
pub const FILE_NAME: &str = "desktop-port";
/// stdin 首行 JSON 里的字段名（Python 侧 `desktop.PREFERRED_PORT_FIELD`）。
pub const HELLO_FIELD: &str = "preferred_port";
/// 首次随机挑选的范围。
pub const FIRST_CHOICE: RangeInclusive<u16> = 20000..=29999;
/// 壳会发出、sidecar 会接受的建议端口范围（非特权端口）。
pub const VALID: RangeInclusive<u16> = 1024..=65535;
/// 连续落空多少次才改记。
pub const MISSES_BEFORE_RELEARN: u32 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Memory {
    /// 记住的端口（每次启动都先建议它）。
    pub port: u16,
    /// 连续落空的次数；拿到一次就清零。
    #[serde(default)]
    pub misses: u32,
}

/// 文件内容 → 记忆。任何不合规（不是 JSON、缺字段、端口越界）一律当「没有记录」：
/// 坏文件的代价只是这次重新随机挑一个，而不是把壳卡在启动上。
pub fn parse(text: &str) -> Option<Memory> {
    let m: Memory = serde_json::from_str(text.trim()).ok()?;
    VALID.contains(&m.port).then_some(m)
}

pub fn render(m: &Memory) -> String {
    format!(
        "{}\n",
        serde_json::json!({ "port": m.port, "misses": m.misses })
    )
}

/// 把一个随机数映射进 [`FIRST_CHOICE`]。
pub fn first_choice(random: u16) -> u16 {
    let span = FIRST_CHOICE.end() - FIRST_CHOICE.start() + 1;
    FIRST_CHOICE.start() + random % span
}

/// 这次启动要建议给 sidecar 的端口。
pub fn preferred(stored: Option<Memory>, random: u16) -> u16 {
    match stored {
        Some(m) => m.port,
        None => first_choice(random),
    }
}

/// 启动完成后该记成什么。返回 `None` = 不写（保持文件原样）。
///
/// - 拿到了建议端口 → 记它、落空计数清零；
/// - 没拿到、而本来就没有记录（首次那个随机端口正好被占）→ 不记：下次再随机挑一个，
///   不把系统分配的临时端口当成「这台机器上的端口」记下来；
/// - 没拿到、有记录 → 计数加一；到 [`MISSES_BEFORE_RELEARN`] 才改记为实际端口。
pub fn after_launch(stored: Option<Memory>, preferred: u16, actual: u16) -> Option<Memory> {
    if actual == preferred {
        return Some(Memory {
            port: preferred,
            misses: 0,
        });
    }
    let m = stored.filter(|m| m.port == preferred)?;
    let misses = m.misses.saturating_add(1);
    if misses >= MISSES_BEFORE_RELEARN && VALID.contains(&actual) {
        Some(Memory {
            port: actual,
            misses: 0,
        })
    } else {
        Some(Memory {
            port: m.port,
            misses,
        })
    }
}

pub fn path(config_dir: Option<&Path>) -> Option<PathBuf> {
    config_dir.map(|d| d.join(FILE_NAME))
}

pub fn load(path: Option<&Path>) -> Option<Memory> {
    parse(&std::fs::read_to_string(path?).ok()?)
}

/// tmp + rename。写不进去只意味着下次还按旧记录（或重新随机）来，不打断启动。
pub fn store(path: Option<&Path>, m: &Memory) {
    let Some(path) = path else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = path.with_file_name(format!("{FILE_NAME}.tmp"));
    if std::fs::write(&tmp, render(m)).is_ok() && std::fs::rename(&tmp, path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mem(port: u16, misses: u32) -> Option<Memory> {
        Some(Memory { port, misses })
    }

    #[test]
    fn first_choice_stays_inside_the_range_for_every_random_value() {
        let mut seen_low = false;
        let mut seen_high = false;
        for r in 0..=u16::MAX {
            let p = first_choice(r);
            assert!(FIRST_CHOICE.contains(&p), "{r} → {p}");
            seen_low |= p == *FIRST_CHOICE.start();
            seen_high |= p == *FIRST_CHOICE.end();
        }
        // 两端都挑得到：范围没被算窄
        assert!(seen_low && seen_high);
        // 与三个平台的临时端口段不相交（Linux 32768 起、Windows / macOS 49152 起）
        assert!(*FIRST_CHOICE.end() < 32768);
    }

    #[test]
    fn preferred_uses_the_memory_and_falls_back_to_a_fresh_pick() {
        assert_eq!(preferred(mem(23456, 2), 7), 23456);
        assert_eq!(preferred(None, 7), 20007);
    }

    #[test]
    fn a_hit_records_the_port_and_clears_the_misses() {
        assert_eq!(after_launch(None, 21000, 21000), mem(21000, 0));
        assert_eq!(after_launch(mem(21000, 2), 21000, 21000), mem(21000, 0));
    }

    #[test]
    fn a_first_launch_miss_records_nothing() {
        assert_eq!(after_launch(None, 21000, 51234), None);
    }

    #[test]
    fn it_takes_three_consecutive_misses_to_relearn() {
        let mut stored = mem(21000, 0);
        let actual = [51001, 51002, 51003];
        for (i, a) in actual.iter().enumerate() {
            let pref = preferred(stored, 0);
            assert_eq!(pref, 21000, "第 {i} 次仍建议记住的端口");
            stored = after_launch(stored, pref, *a);
            if i < 2 {
                assert_eq!(
                    stored,
                    mem(21000, i as u32 + 1),
                    "第 {} 次落空不改记",
                    i + 1
                );
            }
        }
        assert_eq!(stored, mem(51003, 0), "第三次落空改记为实际端口");
    }

    #[test]
    fn a_hit_between_misses_resets_the_count() {
        let s = after_launch(mem(21000, 0), 21000, 50000);
        let s = after_launch(s, 21000, 50001);
        assert_eq!(s, mem(21000, 2));
        let s = after_launch(s, 21000, 21000);
        assert_eq!(s, mem(21000, 0));
        let s = after_launch(s, 21000, 50002);
        assert_eq!(s, mem(21000, 1), "中间拿到过一次：计数从头来");
    }

    #[test]
    fn a_broken_file_is_no_memory() {
        for text in [
            "",
            "garbage",
            "{",
            "23456",
            r#"{"misses":1}"#,
            r#"{"port":"23456"}"#,
            r#"{"port":80}"#,
            r#"{"port":0}"#,
            r#"{"port":70000}"#,
            r#"{"port":-1}"#,
        ] {
            assert_eq!(parse(text), None, "{text:?}");
        }
        assert_eq!(parse(r#"{"port":23456}"#), mem(23456, 0));
        assert_eq!(
            parse(&render(&Memory {
                port: 23456,
                misses: 2
            })),
            mem(23456, 2)
        );
    }

    #[test]
    fn load_and_store_round_trip_and_survive_a_corrupt_file() {
        let dir = std::env::temp_dir().join(format!(
            "tavotto-port-memory-{}-{:016x}",
            std::process::id(),
            rand::random::<u64>()
        ));
        let p = path(Some(&dir)).unwrap();
        assert_eq!(p.file_name().unwrap(), FILE_NAME);
        assert_eq!(load(Some(&p)), None, "没有文件 = 没有记录");
        store(
            Some(&p),
            &Memory {
                port: 24680,
                misses: 1,
            },
        );
        assert_eq!(load(Some(&p)), mem(24680, 1));
        std::fs::write(&p, "{not json").unwrap();
        assert_eq!(load(Some(&p)), None, "坏文件 = 没有记录");
        // 坏文件之后照常能写回
        store(
            Some(&p),
            &Memory {
                port: 24681,
                misses: 0,
            },
        );
        assert_eq!(load(Some(&p)), mem(24681, 0));
        assert!(
            !dir.join(format!("{FILE_NAME}.tmp")).exists(),
            "不留临时文件"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 与 Python 消费方（`desktop.read_launch_credentials`）的严格同源对：两侧各读这份 golden，
    /// 不读对方源码（Python 那侧是 `tests/test_desktop_sidecar.py` 的同名用例）。
    #[test]
    fn hello_field_is_the_golden_pair() {
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/golden/desktop_preferred_port.json"
        ))
        .unwrap();
        assert_eq!(golden["field"], HELLO_FIELD);
        assert_eq!(golden["valid_min"], *VALID.start());
        assert_eq!(golden["valid_max"], *VALID.end());
        assert_eq!(golden["first_choice_min"], *FIRST_CHOICE.start());
        assert_eq!(golden["first_choice_max"], *FIRST_CHOICE.end());
        assert_eq!(golden["misses_before_relearn"], MISSES_BEFORE_RELEARN);
        // 壳能发出的每一个值都在消费方接受的范围里：首次挑选的全部取值 + 能被记住的端口
        for r in [0u16, 1, 9999, 10000, u16::MAX] {
            let p = preferred(None, r);
            assert!(VALID.contains(&p));
        }
        for a in golden["accepted"].as_array().unwrap() {
            let port = u16::try_from(a.as_u64().unwrap()).unwrap();
            assert_eq!(parse(&format!(r#"{{"port":{port}}}"#)), mem(port, 0));
        }
    }
}
