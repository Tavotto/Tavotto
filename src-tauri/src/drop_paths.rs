//! 主页拖放：系统原生拖放交来的路径 → 前端该做什么（ADR 0092）。
//!
//! 纯函数、跨平台、不碰窗口：取路径的那一半在 `native_drop.rs`（macOS），这里只做分派。
//! 规则：
//!
//! * 只认**真实存在的本地绝对路径**：先看原串是不是绝对路径，再 `canonicalize`（解开
//!   符号链接与 `..`）；拿不到规范路径的一律丢掉，不猜。
//! * 放下的东西里**第一个 `.py`** 赢：打开它所在的文件夹（项目 = 脚本所在的文件夹），
//!   脚本本身一起交给前端（说出口 + 关联）；没有 `.py` 时**第一个文件夹**当项目打开；
//!   都没有就是「不支持的类型」，把第一个的名字交给前端提示。
//! * `ignored` = 这次放下来、但没被采用的条目数（多拖了几个时前端要说一句）。
//!
//! 产出只是一个建议：前端拿到路径后仍走 `/api/projects/open`——会话认证（ADR 0008）与
//! 后端对路径的校验一步不少，壳这里不开第二条打开项目的通道。

use serde::Serialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DropTarget {
    /// 放下的是 `.py`：打开 `folder`，并告诉用户是哪个脚本
    Script {
        folder: String,
        script: String,
        name: String,
        ignored: usize,
    },
    /// 放下的是文件夹：它自己就是项目
    Folder {
        folder: String,
        name: String,
        ignored: usize,
    },
    /// 放下的东西存在，但既不是 `.py` 也不是文件夹
    Unsupported { name: String },
}

/// 前端事件名（与 `web/src/lib/desktop.ts` 的 `onNativeFileDrop` 严格同源）
pub const EVENT: &str = "tavotto:file-drop";

/// Windows 的 `canonicalize` 会带上 `\\?\` 前缀（verbatim 路径）：交给前端 / 后端之前去掉，
/// 否则同一个目录在最近项目里会出现两种写法。UNC 的 `\\?\UNC\` 还原成 `\\`。
fn display_path(p: &Path) -> Option<String> {
    let s = p.to_str()?;
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        return Some(format!(r"\\{rest}"));
    }
    Some(s.strip_prefix(r"\\?\").unwrap_or(s).to_string())
}

fn name_of(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| p.to_string_lossy().into_owned())
}

fn is_script(p: &Path) -> bool {
    p.is_file()
        && p.extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| e.eq_ignore_ascii_case("py"))
}

/// 一次放下 → 分派结果；一个能用的路径都没有时 `None`（什么都不发）。
pub fn classify(paths: &[PathBuf]) -> Option<DropTarget> {
    let real: Vec<PathBuf> = paths
        .iter()
        .filter(|p| p.is_absolute())
        .filter_map(|p| std::fs::canonicalize(p).ok())
        .filter(|p| display_path(p).is_some())
        .collect();
    let ignored = paths.len().saturating_sub(1);
    if let Some(script) = real.iter().find(|p| is_script(p)) {
        let folder = script.parent()?;
        return Some(DropTarget::Script {
            folder: display_path(folder)?,
            script: display_path(script)?,
            name: name_of(script),
            ignored,
        });
    }
    if let Some(dir) = real.iter().find(|p| p.is_dir()) {
        return Some(DropTarget::Folder {
            folder: display_path(dir)?,
            name: name_of(dir),
            ignored,
        });
    }
    real.first()
        .map(|p| DropTarget::Unsupported { name: name_of(p) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// 每条用例一个独立目录：`temp_dir/tavotto-drop-<名字>-<pid>`
    fn scratch(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("tavotto-drop-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        fs::canonicalize(d).unwrap()
    }

    fn touch(p: &Path) {
        fs::write(p, b"import matplotlib\n").unwrap();
    }

    #[test]
    fn a_script_opens_its_folder_and_names_itself() {
        let d = scratch("script");
        let py = d.join("plot.PY");
        touch(&py);
        match classify(std::slice::from_ref(&py)).unwrap() {
            DropTarget::Script {
                folder,
                script,
                name,
                ignored,
            } => {
                assert_eq!(folder, display_path(&d).unwrap());
                assert_eq!(script, display_path(&py).unwrap());
                assert_eq!(name, "plot.PY");
                assert_eq!(ignored, 0);
            }
            other => panic!("expected Script, got {other:?}"),
        }
    }

    #[test]
    fn the_first_script_wins_over_earlier_folders_and_files() {
        let d = scratch("many");
        let sub = d.join("sub");
        fs::create_dir(&sub).unwrap();
        let pdf = d.join("fig.pdf");
        touch(&pdf);
        let a = d.join("a.py");
        let b = d.join("b.py");
        touch(&a);
        touch(&b);
        match classify(&[pdf, sub, a, b]).unwrap() {
            DropTarget::Script { name, ignored, .. } => {
                assert_eq!(name, "a.py");
                assert_eq!(ignored, 3);
            }
            other => panic!("expected Script, got {other:?}"),
        }
    }

    #[test]
    fn a_folder_is_the_project_itself() {
        let d = scratch("folder");
        let sub = d.join("paper figs");
        fs::create_dir(&sub).unwrap();
        assert_eq!(
            classify(std::slice::from_ref(&sub)),
            Some(DropTarget::Folder {
                folder: display_path(&sub).unwrap(),
                name: "paper figs".into(),
                ignored: 0
            })
        );
    }

    #[test]
    fn other_types_are_named_not_opened() {
        let d = scratch("other");
        let pdf = d.join("fig.pdf");
        touch(&pdf);
        assert_eq!(
            classify(&[pdf]),
            Some(DropTarget::Unsupported {
                name: "fig.pdf".into()
            })
        );
    }

    #[test]
    fn only_real_absolute_paths_count() {
        let d = scratch("real");
        // 不存在的 .py、相对路径：都不算；一个能用的都没有就什么都不发
        assert_eq!(classify(&[d.join("ghost.py")]), None);
        assert_eq!(classify(&[PathBuf::from("relative/plot.py")]), None);
        assert_eq!(classify(&[]), None);
        // `..` 与符号链接被规范化：交出去的是真实位置
        let real = d.join("real");
        fs::create_dir(&real).unwrap();
        let py = real.join("plot.py");
        touch(&py);
        let dotted = d.join("real").join("..").join("real").join("plot.py");
        match classify(&[dotted]).unwrap() {
            DropTarget::Script { folder, .. } => assert_eq!(folder, display_path(&real).unwrap()),
            other => panic!("expected Script, got {other:?}"),
        }
        #[cfg(unix)]
        {
            let link = d.join("link.py");
            std::os::unix::fs::symlink(&py, &link).unwrap();
            match classify(&[link]).unwrap() {
                DropTarget::Script { folder, name, .. } => {
                    assert_eq!(folder, display_path(&real).unwrap());
                    assert_eq!(name, "plot.py");
                }
                other => panic!("expected Script, got {other:?}"),
            }
        }
    }

    #[test]
    fn verbatim_windows_prefixes_are_stripped() {
        assert_eq!(
            display_path(Path::new(r"\\?\C:\work\figs")).unwrap(),
            r"C:\work\figs"
        );
        assert_eq!(
            display_path(Path::new(r"\\?\UNC\srv\share\figs")).unwrap(),
            r"\\srv\share\figs"
        );
        assert_eq!(
            display_path(Path::new("/Users/me/figs")).unwrap(),
            "/Users/me/figs"
        );
    }

    #[test]
    fn the_payload_shape_is_what_the_frontend_reads() {
        let v = serde_json::to_value(DropTarget::Folder {
            folder: "/x".into(),
            name: "x".into(),
            ignored: 2,
        })
        .unwrap();
        assert_eq!(
            v,
            serde_json::json!({"kind": "folder", "folder": "/x", "name": "x", "ignored": 2})
        );
    }
}
