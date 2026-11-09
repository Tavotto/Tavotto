//! 提权复制助手（ADR 0173）：`tavotto-desktop --elevated-copy <源> <目标>`。
//!
//! 缺数据时 Tavotto 把用户指认过的那份文件复制到脚本要读的原路径；目标目录没有写权限、用户点了「授权后复制」，
//! Python 引擎用 `ShellExecuteExW("runas")` 拉起**本可执行文件自己**带这个固定子命令。助手**只做这一次复制**，
//! 做完就退出，不开窗口、不起 sidecar、不读写任何配置。参数只有两个路径，不经 shell；助手拿到之后**自己再校验一次**
//! （引擎那一侧的检查不当作信任来源）：
//!
//! * 两个都是绝对路径、不含 `..`；源是普通文件（跟随符号链接，不是目录）；目标的父目录存在、目标本身一个字节都还没有
//!   （含悬空符号链接——不穿过它去写）；
//! * 目标不在 Windows 目录里、扩展名不是可执行 / 脚本 / 快捷方式一类——数据文件不该落在那里，提权写入更不该；
//! * 同目录临时名写满、`sync_all`，再以**不覆盖**的方式发布（Windows `MoveFileExW` 不带 `REPLACE_EXISTING`，其余平台
//!   `hard_link` + 删临时名）。
//!
//! 退出码与 `src/tavotto/engine/inputcopy.py` 的 `HELPER_*` 严格同源（`tests/test_input_copy.py` 看护）。
//! `main()` 里在崩溃过滤器与 MSIX 脱离之后、建任何 Builder 之前调用 [`run_if_requested`]。

use std::fs;
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};

/// 固定子命令；引擎侧同名常量 `HELPER_FLAG`
pub const FLAG: &str = "--elevated-copy";
pub const EXIT_OK: i32 = 0;
pub const EXIT_EXISTS: i32 = 10;
pub const EXIT_INVALID: i32 = 11;
pub const EXIT_DENIED: i32 = 12;
pub const EXIT_FAILED: i32 = 13;

/// 目标扩展名黑名单（小写）：数据文件不是这些
const REFUSED_EXTENSIONS: &[&str] = &[
    "exe", "dll", "sys", "scr", "com", "cpl", "msi", "bat", "cmd", "ps1", "psm1", "vbs", "vbe",
    "js", "jse", "wsf", "wsh", "hta", "lnk", "reg", "pif", "url", "inf", "gadget", "msc",
];

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Outcome {
    Copied,
    Exists,
    Invalid,
    Denied,
    Failed,
}

impl Outcome {
    pub fn exit_code(self) -> i32 {
        match self {
            Outcome::Copied => EXIT_OK,
            Outcome::Exists => EXIT_EXISTS,
            Outcome::Invalid => EXIT_INVALID,
            Outcome::Denied => EXIT_DENIED,
            Outcome::Failed => EXIT_FAILED,
        }
    }
}

/// 子命令之后的参数恰好是 `<源> <目标>` 两项才算数；多一项少一项都不执行
pub fn parse(args: &[String]) -> Option<(PathBuf, PathBuf)> {
    match args {
        [flag, src, dst] if flag == FLAG && !src.is_empty() && !dst.is_empty() => {
            Some((PathBuf::from(src), PathBuf::from(dst)))
        }
        _ => None,
    }
}

fn has_parent_dir(p: &Path) -> bool {
    p.components().any(|c| matches!(c, Component::ParentDir))
}

/// Windows 目录（`%SystemRoot%`）：只在 Windows 上有意义，别处恒为 None
fn system_root() -> Option<PathBuf> {
    if cfg!(windows) {
        std::env::var_os("SystemRoot").map(PathBuf::from)
    } else {
        None
    }
}

fn starts_with_ci(path: &Path, base: &Path) -> bool {
    let norm = |p: &Path| p.to_string_lossy().replace('/', "\\").to_lowercase();
    let (p, b) = (norm(path), norm(base));
    let b = b.trim_end_matches('\\');
    p == b || p.starts_with(&format!("{b}\\"))
}

/// 目标位置 / 类型是否被拒绝（提权写入不该去的地方）
pub fn refused_target(dst: &Path, system_root: Option<&Path>) -> bool {
    if let Some(ext) = dst.extension().and_then(|e| e.to_str()) {
        if REFUSED_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()) {
            return true;
        }
    }
    match system_root {
        Some(root) => starts_with_ci(dst, root),
        None => false,
    }
}

/// 助手自己的前置校验。返回 `Err(Outcome)` 就是不执行；目标已存在回 `Err(Exists)`。
pub fn validate(src: &Path, dst: &Path, system_root: Option<&Path>) -> Result<(), Outcome> {
    if !src.is_absolute() || !dst.is_absolute() || has_parent_dir(src) || has_parent_dir(dst) {
        return Err(Outcome::Invalid);
    }
    if dst.file_name().is_none() || refused_target(dst, system_root) {
        return Err(Outcome::Invalid);
    }
    // 源：普通文件（跟随符号链接；目录、设备、管道都不是）
    match fs::metadata(src) {
        Ok(m) if m.is_file() => {}
        Ok(_) => return Err(Outcome::Invalid),
        Err(e) => return Err(classify(&e)),
    }
    match dst.parent() {
        Some(parent) if parent.is_dir() => {}
        _ => return Err(Outcome::Invalid),
    }
    // 目标：`symlink_metadata` 不穿过符号链接——悬空的也算「已有东西」，不写
    match fs::symlink_metadata(dst) {
        Ok(_) => Err(Outcome::Exists),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(classify(&e)),
    }
}

fn classify(e: &io::Error) -> Outcome {
    match e.kind() {
        io::ErrorKind::PermissionDenied => Outcome::Denied,
        io::ErrorKind::AlreadyExists => Outcome::Exists,
        _ => Outcome::Failed,
    }
}

fn temp_name(dst: &Path) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let name = dst
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let tag = format!(
        ".{name}.{}.{nanos:x}.{}.tavotto-copy",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    );
    dst.with_file_name(tag)
}

/// 把临时文件放到 `dst`，**不覆盖**；目标此刻存在 → `AlreadyExists`。
#[cfg(windows)]
fn publish_no_overwrite(tmp: &Path, dst: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::{MoveFileExW, MOVE_FILE_FLAGS};
    let wide = |p: &Path| -> Vec<u16> {
        p.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    };
    let (from, to) = (wide(tmp), wide(dst));
    // flags = 0：不带 MOVEFILE_REPLACE_EXISTING，目标存在即失败（ERROR_ALREADY_EXISTS / ERROR_FILE_EXISTS）
    unsafe {
        MoveFileExW(
            PCWSTR(from.as_ptr()),
            PCWSTR(to.as_ptr()),
            MOVE_FILE_FLAGS(0),
        )
    }
    .map_err(|e| io::Error::from_raw_os_error((e.code().0 as u32 & 0xFFFF) as i32))
}

#[cfg(not(windows))]
fn publish_no_overwrite(tmp: &Path, dst: &Path) -> io::Result<()> {
    fs::hard_link(tmp, dst)?;
    fs::remove_file(tmp)
}

/// 校验 + 复制。`Copied` 之外的每一档都没有在目标位置留下任何东西（临时文件也清掉）。
pub fn perform(src: &Path, dst: &Path) -> Outcome {
    perform_with(src, dst, system_root().as_deref())
}

pub fn perform_with(src: &Path, dst: &Path, system_root: Option<&Path>) -> Outcome {
    if let Err(outcome) = validate(src, dst, system_root) {
        return outcome;
    }
    let tmp = temp_name(dst);
    let result = (|| -> io::Result<()> {
        let mut input = fs::File::open(src)?;
        let mut out = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        let mut buf = vec![0u8; 1 << 20];
        loop {
            let n = input.read(&mut buf)?;
            if n == 0 {
                break;
            }
            out.write_all(&buf[..n])?;
        }
        out.sync_all()?;
        drop(out);
        publish_no_overwrite(&tmp, dst)
    })();
    match result {
        Ok(()) => Outcome::Copied,
        Err(e) => {
            let _ = fs::remove_file(&tmp);
            // Windows 的 ERROR_ALREADY_EXISTS(183) / ERROR_FILE_EXISTS(80) 不一定映射成 AlreadyExists
            if matches!(e.raw_os_error(), Some(183) | Some(80)) && cfg!(windows) {
                return Outcome::Exists;
            }
            classify(&e)
        }
    }
}

/// 子命令入口：命令行是 `--elevated-copy …` 就执行并退出进程，否则什么都不做。
/// 不开窗口、不起 sidecar、不碰配置：被调用时（经 UAC 提权）本进程只为这一次复制而存在。
pub fn run_if_requested() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) != Some(FLAG) {
        return;
    }
    let code = match parse(&args) {
        Some((src, dst)) => perform(&src, &dst).exit_code(),
        None => EXIT_INVALID,
    };
    std::process::exit(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(tag: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("tavotto-elevcopy-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn s(v: &str) -> String {
        v.to_string()
    }

    #[test]
    fn only_the_fixed_subcommand_with_exactly_two_paths_is_accepted() {
        assert!(parse(&[s(FLAG), s("/a"), s("/b")]).is_some());
        assert!(parse(&[s(FLAG), s("/a")]).is_none());
        assert!(parse(&[s(FLAG), s("/a"), s("/b"), s("/c")]).is_none());
        assert!(parse(&[s("--open"), s("/a"), s("/b")]).is_none());
        assert!(parse(&[s(FLAG), s(""), s("/b")]).is_none());
        assert!(parse(&[]).is_none());
    }

    #[test]
    fn exit_codes_match_the_engine_side() {
        // `src/tavotto/engine/inputcopy.py` 的 HELPER_* 同值（那边有读本文件的看护）
        assert_eq!(
            [EXIT_OK, EXIT_EXISTS, EXIT_INVALID, EXIT_DENIED, EXIT_FAILED],
            [0, 10, 11, 12, 13]
        );
        assert_eq!(Outcome::Copied.exit_code(), 0);
        assert_eq!(Outcome::Exists.exit_code(), 10);
    }

    #[test]
    fn copies_a_regular_file_without_leaving_temp_files() {
        let dir = tmpdir("ok");
        let src = dir.join("src.csv");
        fs::write(&src, b"7\n").unwrap();
        let out = dir.join("out");
        fs::create_dir(&out).unwrap();
        let dst = out.join("values.csv");
        assert_eq!(perform_with(&src, &dst, None), Outcome::Copied);
        assert_eq!(fs::read(&dst).unwrap(), b"7\n");
        assert_eq!(fs::read(&src).unwrap(), b"7\n");
        let names: Vec<_> = fs::read_dir(&out)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(names, vec![std::ffi::OsString::from("values.csv")]);
    }

    #[test]
    fn never_overwrites_an_existing_target() {
        let dir = tmpdir("exists");
        let src = dir.join("src.csv");
        fs::write(&src, b"new").unwrap();
        let dst = dir.join("dst.csv");
        fs::write(&dst, b"mine").unwrap();
        assert_eq!(perform_with(&src, &dst, None), Outcome::Exists);
        assert_eq!(fs::read(&dst).unwrap(), b"mine");
    }

    #[test]
    fn refuses_a_directory_source_a_relative_path_and_dotdot() {
        let dir = tmpdir("invalid");
        let folder = dir.join("folder");
        fs::create_dir(&folder).unwrap();
        let file = dir.join("f.csv");
        fs::write(&file, b"x").unwrap();
        assert_eq!(
            perform_with(&folder, &dir.join("o.csv"), None),
            Outcome::Invalid
        );
        assert_eq!(
            perform_with(Path::new("rel.csv"), &dir.join("o.csv"), None),
            Outcome::Invalid
        );
        assert_eq!(
            perform_with(&file, Path::new("rel-out.csv"), None),
            Outcome::Invalid
        );
        let sneaky = dir.join("sub").join("..").join("o.csv");
        assert_eq!(perform_with(&file, &sneaky, None), Outcome::Invalid);
        assert!(!dir.join("o.csv").exists());
    }

    #[test]
    fn does_not_create_a_missing_parent_directory() {
        let dir = tmpdir("noparent");
        let file = dir.join("f.csv");
        fs::write(&file, b"x").unwrap();
        let dst = dir.join("missing").join("f.csv");
        assert_eq!(perform_with(&file, &dst, None), Outcome::Invalid);
        assert!(!dir.join("missing").exists());
    }

    #[test]
    fn refuses_executable_looking_targets_and_the_windows_directory() {
        let dir = tmpdir("refused");
        let file = dir.join("f.csv");
        fs::write(&file, b"x").unwrap();
        for name in ["a.exe", "b.DLL", "c.bat", "d.ps1", "e.lnk"] {
            assert_eq!(
                perform_with(&file, &dir.join(name), None),
                Outcome::Invalid,
                "{name}"
            );
            assert!(!dir.join(name).exists());
        }
        assert!(refused_target(&dir.join("x.csv"), Some(&dir)));
        assert!(refused_target(&dir.join("sub").join("x.csv"), Some(&dir)));
        assert!(!refused_target(
            &dir.join("x.csv"),
            Some(&dir.join("other"))
        ));
        assert!(!refused_target(&dir.join("x.csv"), None));
    }

    #[cfg(unix)]
    #[test]
    fn a_dangling_symlink_target_counts_as_existing_and_is_not_written_through() {
        let dir = tmpdir("dangling");
        let file = dir.join("f.csv");
        fs::write(&file, b"x").unwrap();
        let dst = dir.join("link.csv");
        std::os::unix::fs::symlink(dir.join("nowhere.csv"), &dst).unwrap();
        assert_eq!(perform_with(&file, &dst, None), Outcome::Exists);
        assert!(!dir.join("nowhere.csv").exists());
    }

    #[cfg(unix)]
    #[test]
    fn a_read_only_directory_is_denied_and_leaves_nothing() {
        use std::os::unix::fs::PermissionsExt;
        // root 无视权限位：此时这条断言没有意义，跳过
        if is_root() {
            return;
        }
        let dir = tmpdir("denied");
        let file = dir.join("f.csv");
        fs::write(&file, b"x").unwrap();
        let out = dir.join("ro");
        fs::create_dir(&out).unwrap();
        fs::set_permissions(&out, fs::Permissions::from_mode(0o555)).unwrap();
        let outcome = perform_with(&file, &out.join("f.csv"), None);
        fs::set_permissions(&out, fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(outcome, Outcome::Denied);
        assert_eq!(fs::read_dir(&out).unwrap().count(), 0);
    }

    #[cfg(unix)]
    fn is_root() -> bool {
        std::process::Command::new("id")
            .arg("-u")
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim() == "0")
            .unwrap_or(false)
    }
}
