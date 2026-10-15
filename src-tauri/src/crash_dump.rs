//! 壳自写的 Windows 小型转储（.dmp）——崩溃时留一份现场，诊断包「崩溃报告」才有东西可收。
//!
//! 为什么不是 WER 的 `LocalDumps` 注册表键：微软文档只写了 `HKLM` 下的那一处，`HKCU` 是否被读取没有
//! 权威说法（探针在 GitHub runner 上也测不出结论，因为 runner 的 WER 本身不产转储）；而 `HKLM` 要管理员，
//! 与 currentUser 安装冲突。壳自己写就不依赖任何系统配置。
//!
//! - **只在 Windows 生效**：[`install`] 在别的平台是空函数（macOS 系统本来就写 `.ips`，诊断包已收）。
//! - 启动最早期（`main()` 的第一句）装 `SetUnhandledExceptionFilter`；过滤器里
//!   `MiniDumpWriteDump(MiniDumpNormal | MiniDumpWithThreadInfo)` 写
//!   `%LOCALAPPDATA%\CrashDumps\Tavotto.exe.<pid>.<毫秒戳>.dmp`——目录与文件名正好是诊断包
//!   `engine/diagdetail.py::crash_dirs()` 的约定（`windows_crashdumps`，进程名前缀 `tavotto`），那边不用改。
//! - **过滤器里不分配堆、不加锁**：路径前缀在 [`install`] 里预先算好、存进 `OnceLock`；pid 与 `.dmp`
//!   毫秒戳（`GetTickCount64`）与 `.dmp` 后缀在栈上的定长缓冲里拼。写完返回 `EXCEPTION_CONTINUE_SEARCH`，系统照常处理崩溃（WER / 退出码不变）。
//!   重入（过滤器自己又崩）用一个原子旗标挡掉。
//! - **不覆盖旧转储**（#1068 r4280527495）：pid 会被系统复用，所以文件名带毫秒戳，且用 `CREATE_NEW`
//!   打开；撞名最多换 [`OPEN_ATTEMPTS`] 次戳重试。**写失败要清掉半截文件**（r4280527506）：
//!   `MiniDumpWriteDump` 失败时先关句柄再 `DeleteFileW`，免得诊断包收到一份残缺的 .dmp。
//! - 初始化时轮转旧转储：认 `Tavotto.exe.<pid>.dmp`（旧格式）与 `Tavotto.exe.<pid>.<戳>.dmp`（别的程序的 / 用户的文件一个都不碰），
//!   留 [`MAX_DUMPS`] 份——不为「也许会来的」崩溃提前删掉一份（#1068 r4280472040）；崩溃落盘后磁盘上
//!   短暂可有 MAX_DUMPS + 1 份，下次启动再轮回 MAX_DUMPS。
//! - 已知覆盖不到的：Rust `panic!`（走 unwind / abort，不经未处理异常过滤器）、`__fastfail`。
//!   Python sidecar 不覆盖——它是 Python 进程，崩溃信息靠 traceback 与 faulthandler；
//!   `tavotto-workerd` 是另一个 crate，见 `workerd/src/crash_dump.rs`。

// 纯函数（命名 / 轮转）各平台都编译、都有单测；只有 Windows 的 install 真的用它们
#![cfg_attr(not(windows), allow(dead_code))]

use std::path::{Path, PathBuf};

/// 转储文件名里的 exe 名：与 `tauri.conf.json` 的 `mainBinaryName` + `.exe` 同源
/// （[`tests::dump_exe_name_matches_main_binary_name`] 比对）。
pub const DUMP_EXE: &str = "Tavotto.exe";

/// 目录里最多留几份（含这一次崩溃写的那份）。
pub const MAX_DUMPS: usize = 5;

/// 撞名时最多试几个不同的戳（含第一次）。
pub const OPEN_ATTEMPTS: u64 = 4;

/// `<exe>.<pid>.<毫秒戳>.dmp`——WER 自己的命名习惯再加一段戳；诊断包按第一个 `.` 之前的部分判归属。
/// 过滤器里不能用它（要分配堆），那边走 [`push_path`]；两者必须逐字相同（单测对拍）。
#[cfg_attr(not(test), allow(dead_code))]
pub fn dump_file_name(exe: &str, pid: u32, stamp: u64) -> String {
    format!("{exe}.{pid}.{stamp}.dmp")
}

/// 在定长缓冲里拼 `<prefix><pid>.<stamp>.dmp` 并以 NUL 结尾，返回不含 NUL 的长度；放不下返回 `None`。
/// 不分配堆——崩溃过滤器里调用。`prefix` 是 `<dir>\<exe>.` 的 UTF-16。
pub fn push_path(buf: &mut [u16], prefix: &[u16], pid: u32, stamp: u64) -> Option<usize> {
    fn push_num(buf: &mut [u16], n: &mut usize, mut v: u64) -> Option<()> {
        let mut digits = [0u16; 20];
        let mut d = 0;
        loop {
            digits[d] = b'0' as u16 + (v % 10) as u16;
            d += 1;
            v /= 10;
            if v == 0 {
                break;
            }
        }
        while d > 0 {
            d -= 1;
            *buf.get_mut(*n)? = digits[d];
            *n += 1;
        }
        Some(())
    }
    fn push_ascii(buf: &mut [u16], n: &mut usize, s: &[u8]) -> Option<()> {
        for c in s {
            *buf.get_mut(*n)? = *c as u16;
            *n += 1;
        }
        Some(())
    }
    buf.get_mut(..prefix.len())?.copy_from_slice(prefix);
    let mut n = prefix.len();
    push_num(buf, &mut n, u64::from(pid))?;
    push_ascii(buf, &mut n, b".")?;
    push_num(buf, &mut n, stamp)?;
    push_ascii(buf, &mut n, b".dmp")?;
    *buf.get_mut(n)? = 0;
    Some(n)
}

/// 是不是 `<exe>.<pid>.dmp`（旧格式）或 `<exe>.<pid>.<戳>.dmp`，各段都是纯数字。轮转只许删这两种。
pub fn is_own_dump(file_name: &str, exe: &str) -> bool {
    let Some(rest) = file_name.strip_prefix(exe) else {
        return false;
    };
    let Some(rest) = rest.strip_prefix('.') else {
        return false;
    };
    let Some(nums) = rest.strip_suffix(".dmp") else {
        return false;
    };
    let mut parts = nums.split('.');
    let numeric =
        |p: Option<&str>| p.is_some_and(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()));
    let (first, second, more) = (parts.next(), parts.next(), parts.next());
    numeric(first) && (second.is_none() || numeric(second)) && more.is_none()
}

/// 把 `dir` 里属于 `exe` 的转储删到只剩 `keep` 份（删最旧的）。返回删掉了几份；读不了目录当作没有。
pub fn rotate(dir: &Path, exe: &str, keep: usize) -> usize {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return 0;
    };
    let mut own: Vec<(std::time::SystemTime, PathBuf)> = entries
        .flatten()
        .filter(|e| e.file_name().to_str().is_some_and(|n| is_own_dump(n, exe)))
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    if own.len() <= keep {
        return 0;
    }
    own.sort_by_key(|(t, _)| *t);
    let excess = own.len() - keep;
    own.into_iter()
        .take(excess)
        .filter(|(_, p)| std::fs::remove_file(p).is_ok())
        .count()
}

/// 默认落点：`%LOCALAPPDATA%\CrashDumps`。
fn default_dir() -> Option<PathBuf> {
    let local = std::env::var_os("LOCALAPPDATA")?;
    Some(PathBuf::from(local).join("CrashDumps"))
}

/// 装转储过滤器。非 Windows 是空函数。失败（拿不到目录、建不了目录）静默放弃：转储只是诊断加速。
pub fn install() {
    #[cfg(windows)]
    if let Some(dir) = default_dir() {
        imp::install_into(&dir, DUMP_EXE);
    }
}

#[cfg(windows)]
mod imp {
    use super::{push_path, rotate, MAX_DUMPS, OPEN_ATTEMPTS};
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::OnceLock;

    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{CloseHandle, GENERIC_WRITE, INVALID_HANDLE_VALUE};
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, DeleteFileW, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_NONE,
    };
    use windows::Win32::System::Diagnostics::Debug::{
        MiniDumpNormal, MiniDumpWithThreadInfo, MiniDumpWriteDump, SetUnhandledExceptionFilter,
        EXCEPTION_POINTERS, MINIDUMP_EXCEPTION_INFORMATION, MINIDUMP_TYPE,
    };
    use windows::Win32::System::SystemInformation::GetTickCount64;
    use windows::Win32::System::Threading::{
        GetCurrentProcess, GetCurrentProcessId, GetCurrentThreadId,
    };

    /// 路径缓冲上限（UTF-16 单元）：前缀 + 10 位 pid + `.` + 20 位戳 + `.dmp` + NUL。超了就不装（宁可没有转储）。
    const PATH_CAP: usize = 520;
    const EXCEPTION_CONTINUE_SEARCH: i32 = 0;

    /// `<dir>\<exe>.` 的 UTF-16，[`install_into`] 里算好，过滤器只读。
    static PREFIX: OnceLock<Vec<u16>> = OnceLock::new();
    static IN_FILTER: AtomicBool = AtomicBool::new(false);

    pub fn install_into(dir: &Path, exe: &str) {
        if std::fs::create_dir_all(dir).is_err() {
            return;
        }
        rotate(dir, exe, MAX_DUMPS);
        let prefix = format!("{}\\{exe}.", dir.display());
        let wide: Vec<u16> = prefix.encode_utf16().collect();
        // 前缀 + 最长 pid（10 位）+ "." + 最长戳（20 位）+ ".dmp" + NUL 必须放得进栈缓冲
        if wide.len() + 10 + 1 + 20 + 4 + 1 > PATH_CAP || PREFIX.set(wide).is_err() {
            return;
        }
        // 返回的是上一个过滤器：我们是最外层，不链它（WER 走的是系统默认处理，不经这里）
        unsafe {
            SetUnhandledExceptionFilter(Some(filter));
        }
    }

    unsafe extern "system" fn filter(info: *const EXCEPTION_POINTERS) -> i32 {
        if IN_FILTER.swap(true, Ordering::SeqCst) {
            return EXCEPTION_CONTINUE_SEARCH; // 过滤器里又崩：别递归
        }
        write_dump(info);
        EXCEPTION_CONTINUE_SEARCH
    }

    /// 栈上拼路径、写文件。不分配堆、不加锁。
    unsafe fn write_dump(info: *const EXCEPTION_POINTERS) {
        let Some(prefix) = PREFIX.get() else { return };
        let pid = GetCurrentProcessId();
        let mut buf = [0u16; PATH_CAP];
        let tick = GetTickCount64();
        // CREATE_NEW：同名文件已在（pid 被复用且戳相同）就失败而不是覆盖；换个戳最多再试几次
        let mut opened = None;
        for attempt in 0..OPEN_ATTEMPTS {
            if push_path(&mut buf, prefix, pid, tick + attempt).is_none() {
                return;
            }
            if let Ok(h) = CreateFileW(
                PCWSTR(buf.as_ptr()),
                GENERIC_WRITE.0,
                FILE_SHARE_NONE,
                None,
                CREATE_NEW,
                FILE_ATTRIBUTE_NORMAL,
                None,
            ) {
                opened = Some(h);
                break;
            }
        }
        let Some(file) = opened else { return };
        if file == INVALID_HANDLE_VALUE {
            return;
        }
        let exc = MINIDUMP_EXCEPTION_INFORMATION {
            ThreadId: GetCurrentThreadId(),
            ExceptionPointers: info as *mut EXCEPTION_POINTERS,
            ClientPointers: false.into(),
        };
        let kind = MINIDUMP_TYPE(MiniDumpNormal.0 | MiniDumpWithThreadInfo.0);
        let ok = MiniDumpWriteDump(
            GetCurrentProcess(),
            pid,
            file,
            kind,
            if info.is_null() { None } else { Some(&exc) },
            None,
            None,
        )
        .is_ok();
        let _ = CloseHandle(file);
        if !ok {
            // 写失败：别把半截文件留给诊断包（先关句柄才删得掉）
            let _ = DeleteFileW(PCWSTR(buf.as_ptr()));
        }
    }
}

#[cfg(test)]
mod tests {
    /// `include_str!` 读到的是检出时的字节：Windows 上 git 可能把换行转成 CRLF，结构测试按 `\n` 找会落空
    /// （nightly 38701813732：「找不到 fn main」）。一律先归一成 LF。
    fn lf(s: &str) -> String {
        s.replace("\r\n", "\n")
    }

    use super::*;

    fn touch(dir: &Path, name: &str, age_s: u64) {
        let p = dir.join(name);
        std::fs::write(&p, b"x").unwrap();
        let t = std::time::SystemTime::now() - std::time::Duration::from_secs(age_s);
        std::fs::File::options()
            .write(true)
            .open(&p)
            .unwrap()
            .set_modified(t)
            .unwrap();
    }

    #[test]
    fn file_name_is_exe_dot_pid_dot_stamp_dot_dmp() {
        assert_eq!(
            dump_file_name("Tavotto.exe", 4242, 987654321),
            "Tavotto.exe.4242.987654321.dmp"
        );
    }

    /// 栈上拼出来的路径必须与 `dump_file_name` 逐字相同，且 NUL 结尾、放不下就 `None`（不越界）。
    #[test]
    fn push_path_matches_the_heap_formatter() {
        let prefix: Vec<u16> = "C:\\x\\CrashDumps\\Tavotto.exe.".encode_utf16().collect();
        let mut buf = [0xFFFFu16; 520];
        for (pid, stamp) in [(0u32, 0u64), (4242, 987654321), (u32::MAX, u64::MAX)] {
            let n = push_path(&mut buf, &prefix, pid, stamp).unwrap();
            let got = String::from_utf16(&buf[..n]).unwrap();
            let want = format!(
                "C:\\x\\CrashDumps\\{}",
                dump_file_name(DUMP_EXE, pid, stamp)
            );
            assert_eq!(got, want);
            assert_eq!(buf[n], 0, "没有 NUL 结尾");
        }
        let mut tiny = [0u16; 8];
        assert_eq!(push_path(&mut tiny, &prefix, 1, 1), None);
    }

    /// 不覆盖旧转储：生产代码用 CREATE_NEW 且有重试上限；写失败分支里先 CloseHandle 再 DeleteFileW。
    /// （这段只有 Windows 能真跑，所以钉结构；只读生产代码的非注释行。）
    #[test]
    fn writer_never_overwrites_and_cleans_up_a_failed_dump() {
        let me = &lf(include_str!("crash_dump.rs"));
        let prod = me.split("#[cfg(test)]\nmod tests").next().unwrap();
        let code: String = prod
            .lines()
            .filter(|l| !l.trim_start().starts_with("//"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(code.contains("CREATE_NEW,"));
        assert!(!code.contains("CREATE_ALWAYS"));
        assert!(code.contains("for attempt in 0..OPEN_ATTEMPTS"));
        let body = &code[code.find("unsafe fn write_dump").unwrap()..];
        let close = body.find("CloseHandle(file)").unwrap();
        let guard = body.find("if !ok {").unwrap();
        let delete = body.find("DeleteFileW(").unwrap();
        assert!(close < guard && guard < delete, "失败分支顺序不对");
        // DeleteFileW 只许出现在失败分支里
        assert_eq!(body.matches("DeleteFileW(").count(), 1);
    }

    #[test]
    fn only_our_own_numeric_dumps_count_as_ours() {
        assert!(is_own_dump("Tavotto.exe.1.dmp", "Tavotto.exe"));
        assert!(is_own_dump("Tavotto.exe.1234567890.dmp", "Tavotto.exe"));
        // 新格式：<pid>.<戳>
        assert!(is_own_dump("Tavotto.exe.12.34.dmp", "Tavotto.exe"));
        assert!(is_own_dump(
            "Tavotto.exe.4294967295.18446744073709551615.dmp",
            "Tavotto.exe"
        ));
        for other in [
            "Tavotto.exe.dmp",
            "Tavotto.exe..dmp",
            "Tavotto.exe.1..dmp",
            "Tavotto.exe.1.2.3.dmp",
            "Tavotto.exe.1.x.dmp",
            "Tavotto.exe.12a.dmp",
            "Tavotto.exe.12.dmp.bak",
            "tavotto-cli.exe.12.dmp",
            "XTavotto.exe.12.dmp",
            "python.exe.12.dmp",
            "notes.txt",
        ] {
            assert!(!is_own_dump(other, "Tavotto.exe"), "{other}");
        }
    }

    /// 启动时轮转留满 MAX_DUMPS 份（不预先给下一次崩溃腾位置），#1068 r4280472040。
    #[test]
    fn startup_rotation_keeps_all_max_dumps() {
        let src = &lf(include_str!("crash_dump.rs"));
        let prod = &src[..src.find("#[cfg(test)]\nmod tests").unwrap_or(src.len())];
        let calls: Vec<&str> = prod
            .lines()
            .filter(|l| !l.trim_start().starts_with("//") && l.contains("rotate(dir, exe,"))
            .collect();
        assert_eq!(calls.len(), 1, "{calls:?}");
        assert!(
            calls[0].contains("rotate(dir, exe, MAX_DUMPS);"),
            "{calls:?}"
        );
    }

    #[test]
    fn rotate_keeps_the_newest_and_touches_nothing_else() {
        let dir = std::env::temp_dir().join(format!("tavotto-dump-rot-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        for (i, age) in [600u64, 500, 400, 300, 200, 100].iter().enumerate() {
            touch(&dir, &format!("Tavotto.exe.{}.dmp", i + 1), *age);
        }
        touch(&dir, "python.exe.9.dmp", 9000);
        touch(&dir, "readme.txt", 9000);

        assert_eq!(rotate(&dir, DUMP_EXE, 4), 2);
        let mut left: Vec<String> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        left.sort();
        // 最旧的两份（1、2）没了；最新四份、别人的文件都在
        assert_eq!(
            left,
            [
                "Tavotto.exe.3.dmp",
                "Tavotto.exe.4.dmp",
                "Tavotto.exe.5.dmp",
                "Tavotto.exe.6.dmp",
                "python.exe.9.dmp",
                "readme.txt"
            ]
        );
        // 已经不超就不动；目录不存在当作没有
        assert_eq!(rotate(&dir, DUMP_EXE, 4), 0);
        assert_eq!(rotate(&dir.join("nope"), DUMP_EXE, 0), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 转储文件名里的 exe 名必须是真实的主程序名（安装器按 `mainBinaryName` 装 `Tavotto.exe`），
    /// 诊断包才认得出（`_OWN_PROCESS` 以 tavotto 开头）。
    #[test]
    fn dump_exe_name_matches_main_binary_name() {
        let conf: serde_json::Value =
            serde_json::from_str(&lf(include_str!("../tauri.conf.json"))).unwrap();
        let name = conf["mainBinaryName"].as_str().unwrap();
        assert_eq!(DUMP_EXE, format!("{name}.exe"));
        assert!(DUMP_EXE.to_ascii_lowercase().starts_with("tavotto"));
    }

    /// 诊断包收转储的目录与本模块同源：`crash_dirs()` 里 Windows 的 `CrashDumps`。
    #[test]
    fn dump_dir_is_the_one_the_diagnostics_bundle_collects() {
        let py = &lf(include_str!("../../src/tavotto/engine/diagdetail.py"));
        assert!(py.contains(r#"Path(local) / "CrashDumps""#));
        // 只看生产代码的非注释行：测试模块和注释里自己也写着这个字面量，不切掉就是自证
        let me = &lf(include_str!("crash_dump.rs"));
        let prod = me.split("#[cfg(test)]").next().unwrap();
        let code: Vec<&str> = prod
            .lines()
            .filter(|l| !l.trim_start().starts_with("//"))
            .collect();
        assert!(code.iter().any(|l| l.contains(r#".join("CrashDumps")"#)));
    }

    /// `main()` 的第一句必须是装过滤器：晚装一步，启动早期的崩溃就没有转储。
    #[test]
    fn main_installs_the_filter_as_its_first_statement() {
        let src = &lf(include_str!("main.rs"));
        let at = src.find("\nfn main() {\n").expect("找不到 fn main");
        let first = src[at + "\nfn main() {\n".len()..]
            .lines()
            .map(str::trim)
            .find(|l| !l.is_empty() && !l.starts_with("//"))
            .unwrap();
        assert_eq!(first, "crash_dump::install();");
    }

    /// 真崩一次：子进程（本测试二进制自己，带环境变量开关）触发 access violation，
    /// 父进程断言 `.dmp` 落盘且是 `MDMP` 格式。只在 Windows 上编译与运行。
    #[cfg(windows)]
    #[test]
    fn a_real_access_violation_leaves_a_minidump() {
        let dir = std::env::temp_dir().join(format!("tavotto-dump-real-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "crash_dump::tests::crash_helper",
                "--nocapture",
                "--test-threads=1",
            ])
            .env("TAVOTTO_CRASH_DUMP_HELPER_DIR", &dir)
            .status()
            .unwrap();
        assert!(!status.success(), "helper 应当崩溃退出");
        let dumps: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .flatten()
            .filter(|e| {
                e.file_name()
                    .to_str()
                    .is_some_and(|n| is_own_dump(n, DUMP_EXE))
            })
            .collect();
        assert_eq!(dumps.len(), 1, "应有且仅有一份 {DUMP_EXE}.<pid>.dmp");
        let bytes = std::fs::read(dumps[0].path()).unwrap();
        assert!(bytes.len() > 1024, "转储太小: {}", bytes.len());
        assert_eq!(&bytes[..4], b"MDMP", "不是 minidump 格式");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 上一条的子进程入口：没有环境变量就什么都不做（普通 `cargo test` 里是个空用例）。
    #[cfg(windows)]
    #[test]
    fn crash_helper() {
        let Some(dir) = std::env::var_os("TAVOTTO_CRASH_DUMP_HELPER_DIR") else {
            return;
        };
        imp::install_into(Path::new(&dir), DUMP_EXE);
        unsafe { std::ptr::null_mut::<u8>().write_volatile(1) };
    }
}
