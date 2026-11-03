//! MSIX 包身份兜底（issue #1574）：壳自己带着包身份启动时，用 `DESKTOP_APP_POLICY = BREAKAWAY_ENABLE_PROCESS_TREE`
//! （0x01）把自己重新拉到包外，然后退出。
//!
//! 为什么要有：Codex 桌面版是 MSIX 包。从它拉起的进程继承包身份，`%LOCALAPPDATA%` / `%APPDATA%` 下**新建**的文件
//! 被重定向到 `Packages\<pfn>\LocalCache\...`——最近项目、设置、受管环境、缓存和「直接启动」各是一套。
//! Windows Lab 实测：父树带 `DISABLE_PROCESS_TREE` 时普通 spawn / `cmd /c start` / `Start-Process` 都脱离不了，
//! **只有** `CreateProcessW` + `STARTUPINFOEX` + 该属性 0x01 能脱离，且整棵子树（sidecar、worker）都在包外。
//! 主路径是交接层（`engine/handoff.py` → `engine/msixbreakaway.py`）拉起桌面版时就带这个策略；本模块是
//! 兜底：不管谁拉起的（任何 MSIX 宿主、用户脚本、旧版插件），壳启动时发现自己带包身份就自救。
//!
//! 放在壳而不是 sidecar：壳是整棵进程树的根（sidecar、worker 都是它的后代），在根上脱离一次，
//! 整棵树都在包外；壳在 Tauri 建窗口前就写 `app_config_dir()`（safe_start / session-state / 窗口状态），
//! 放到 sidecar 层这些已经写进包私有区了。
//!
//! 防循环：用环境变量 [`GUARD_ENV`] 标记。重新拉起前置为 `relaunched`；若新进程仍带包身份（策略不被
//! 系统认 / 宿主另有限制），置 `still_packaged`、不再重试、照常启动；`CreateProcessW` 失败置 `failed`、
//! 照常启动。这个值会被 sidecar 继承，诊断包的 `system.msix_breakaway` 如实报出（闭集三值）。
//!
//! 启动结局可观察：替身起来后本进程**不立刻 `exit(0)`**，而是最多等 [`HANDOFF_WAIT_MS`]：替身在窗口内退出就
//! 原样透传它的退出码（崩溃 = 非零，单实例转发完成 = 0），仍活着则以 0 退出。这样 Python 启动器
//! （`handoff._launch_desktop_via_spawn`，观察窗口 `LAUNCH_CRASH_WINDOW`）看到的仍是「这个进程」的结局：
//! 替身崩了报 launch_failed，而不是把任何 0 退出都当成转发完成。
//!
//! 与 Python 侧 `engine/msixbreakaway.py` 是同一份契约的两种语言实现（常量、策略值一致）。

#![cfg_attr(not(windows), allow(dead_code))]

/// 防循环 / 结果标记的环境变量名。与 `engine/msixbreakaway.py::GUARD_ENV` 同源。
pub const GUARD_ENV: &str = "TAVOTTO_MSIX_BREAKAWAY";
/// 已经重新拉起过一次（新进程应当没有包身份）。
pub const RELAUNCHED: &str = "relaunched";
/// 重新拉起后仍带包身份：不再重试。
pub const STILL_PACKAGED: &str = "still_packaged";
/// 重新拉起本身失败：照常启动。
pub const FAILED: &str = "failed";

/// `PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY`
pub const ATTR_DESKTOP_APP_POLICY: usize = 0x0002_0012;
/// `PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE`
pub const BREAKAWAY_ENABLE_PROCESS_TREE: u32 = 0x01;

/// 替身起来后本进程最多等多久（毫秒）。必须大于启动器的观察窗口（`handoff.LAUNCH_CRASH_WINDOW` = 6s，
/// `tests/test_msix_breakaway.py` 对拍），否则窗口内替身崩溃看不到。
pub const HANDOFF_WAIT_MS: u32 = 8000;

/// 本进程该以什么退出码结束：`replacement_exit` 是等待期内替身的退出码（`None` = 等到期替身还活着）。
/// 替身还活着 = 启动成功，0；替身已退出就原样透传（崩溃非零；转发给已在跑的实例后正常退出为 0）。
pub fn handoff_exit_code(replacement_exit: Option<u32>) -> i32 {
    match replacement_exit {
        None => 0,
        Some(code) => code as i32,
    }
}

/// 启动时该做什么。
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Decision {
    /// 没有包身份：照常启动。
    Proceed,
    /// 有包身份、还没试过：脱离包重新拉起自己后退出。
    Relaunch,
    /// 有包身份、已经重新拉起过一次还在包里：不再重试，照常启动（并把事实交给诊断）。
    ProceedStillPackaged,
}

/// 纯判据（各平台都编译、都有单测）：`has_identity` 来自 `GetCurrentPackageFullName`，
/// `guard` 是 [`GUARD_ENV`] 此刻的值。
pub fn decide(has_identity: bool, guard: Option<&str>) -> Decision {
    if !has_identity {
        return Decision::Proceed;
    }
    match guard {
        None | Some("") => Decision::Relaunch,
        Some(_) => Decision::ProceedStillPackaged,
    }
}

/// 按 MSVCRT 规则给一个参数加引号，拼成 `CreateProcessW` 的命令行片段。
pub fn quote_arg(arg: &str) -> String {
    if !arg.is_empty()
        && !arg
            .chars()
            .any(|c| matches!(c, ' ' | '\t' | '\n' | '\x0b' | '"'))
    {
        return arg.to_string();
    }
    let mut out = String::from("\"");
    let mut backslashes = 0usize;
    for c in arg.chars() {
        match c {
            '\\' => backslashes += 1,
            '"' => {
                out.extend(std::iter::repeat_n('\\', backslashes * 2 + 1));
                out.push('"');
                backslashes = 0;
            }
            _ => {
                out.extend(std::iter::repeat_n('\\', backslashes));
                out.push(c);
                backslashes = 0;
            }
        }
    }
    out.extend(std::iter::repeat_n('\\', backslashes * 2));
    out.push('"');
    out
}

/// 命令行：`"<exe>" <args...>`（argv[0] 用真实可执行路径，其余原样转发）。
pub fn command_line(exe: &str, args: &[String]) -> String {
    let mut parts = vec![quote_arg(exe)];
    parts.extend(args.iter().map(|a| quote_arg(a)));
    parts.join(" ")
}

/// 壳启动最早期调用（崩溃过滤器之后、Tauri 建 Builder 之前）：带包身份就脱离包重新拉起自己并退出进程。
/// 非 Windows 是空函数。任何失败都只是「照常启动」，绝不拦死启动。
/// 替身拉起成功后等它至多 [`HANDOFF_WAIT_MS`] 并透传其退出码（见模块文档）。
#[cfg(not(windows))]
pub fn relaunch_outside_package_if_needed() {}

#[cfg(windows)]
pub fn relaunch_outside_package_if_needed() {
    let guard = std::env::var(GUARD_ENV).ok();
    match decide(has_package_identity(), guard.as_deref()) {
        Decision::Proceed => {}
        Decision::ProceedStillPackaged => std::env::set_var(GUARD_ENV, STILL_PACKAGED),
        Decision::Relaunch => {
            // 先置标记再拉起：新进程继承它；拉起失败再改成 failed。
            std::env::set_var(GUARD_ENV, RELAUNCHED);
            match spawn_self_outside_package() {
                Ok(replacement_exit) => std::process::exit(handoff_exit_code(replacement_exit)),
                Err(_) => std::env::set_var(GUARD_ENV, FAILED),
            }
        }
    }
}

#[cfg(windows)]
fn has_package_identity() -> bool {
    use windows::Win32::Foundation::{ERROR_INSUFFICIENT_BUFFER, WIN32_ERROR};
    use windows::Win32::Storage::Packaging::Appx::GetCurrentPackageFullName;
    let mut len = 0u32;
    // SAFETY: 只传长度指针与空缓冲，API 定义的「询问所需长度」用法。
    let rc: WIN32_ERROR = unsafe { GetCurrentPackageFullName(&mut len, None) };
    // 有包身份：缓冲不足（要长度）或成功；没有：APPMODEL_ERROR_NO_PACKAGE（15700）。
    rc == ERROR_INSUFFICIENT_BUFFER || rc.0 == 0
}

#[cfg(windows)]
fn spawn_self_outside_package() -> Result<Option<u32>, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::core::{PCWSTR, PWSTR};
    use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
        InitializeProcThreadAttributeList, UpdateProcThreadAttribute, WaitForSingleObject,
        EXTENDED_STARTUPINFO_PRESENT, LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION,
        STARTUPINFOEXW,
    };

    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cmd = command_line(&exe.to_string_lossy(), &args);
    let mut cmd_w: Vec<u16> = std::ffi::OsStr::new(&cmd)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    // 属性列表：先问长度（按文档这一步返回失败是正常的），再初始化。
    let mut size = 0usize;
    // SAFETY: 空列表 + 长度指针 = 询问所需字节数。
    let _ = unsafe { InitializeProcThreadAttributeList(None, 1, None, &mut size) };
    if size == 0 {
        return Err("attribute list size".into());
    }
    let mut buf = vec![0u8; size];
    let list = LPPROC_THREAD_ATTRIBUTE_LIST(buf.as_mut_ptr().cast());
    // SAFETY: buf 活到本函数结束，大小正是上面询问到的。
    unsafe { InitializeProcThreadAttributeList(Some(list), 1, None, &mut size) }
        .map_err(|e| e.to_string())?;

    let policy: u32 = BREAKAWAY_ENABLE_PROCESS_TREE;
    let result = (|| {
        // SAFETY: `policy` 活到 CreateProcessW 返回之后（属性值按指针引用，不复制）。
        unsafe {
            UpdateProcThreadAttribute(
                list,
                0,
                ATTR_DESKTOP_APP_POLICY,
                Some((&policy as *const u32).cast()),
                std::mem::size_of::<u32>(),
                None,
                None,
            )
        }
        .map_err(|e| e.to_string())?;

        let mut si = STARTUPINFOEXW::default();
        si.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
        si.lpAttributeList = list;
        let mut pi = PROCESS_INFORMATION::default();
        // SAFETY: 命令行缓冲可写且以 NUL 结尾；si/pi 都是本栈上的有效结构。
        unsafe {
            CreateProcessW(
                PCWSTR::null(),
                Some(PWSTR(cmd_w.as_mut_ptr())),
                None,
                None,
                false,
                EXTENDED_STARTUPINFO_PRESENT,
                None, // 环境照旧继承（含刚置的 GUARD_ENV）
                PCWSTR::null(),
                &si.StartupInfo,
                &mut pi,
            )
        }
        .map_err(|e| e.to_string())?;
        // 等替身至多 HANDOFF_WAIT_MS：期间退出就取它的退出码，否则 None（还活着 = 启动成功）。
        // SAFETY: 两个句柄刚由 CreateProcessW 交给我们，等完才关；code 是本栈上的有效 u32。
        let replacement_exit = unsafe {
            let _ = CloseHandle(pi.hThread);
            let exited = WaitForSingleObject(pi.hProcess, HANDOFF_WAIT_MS) == WAIT_OBJECT_0;
            let mut code = 0u32;
            let got = exited && GetExitCodeProcess(pi.hProcess, &mut code).is_ok();
            let _ = CloseHandle(pi.hProcess);
            if got {
                Some(code)
            } else {
                None
            }
        };
        Ok(replacement_exit)
    })();
    // SAFETY: list 已初始化。
    unsafe { DeleteProcThreadAttributeList(list) };
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_identity_always_proceeds() {
        assert_eq!(decide(false, None), Decision::Proceed);
        assert_eq!(decide(false, Some(RELAUNCHED)), Decision::Proceed);
    }

    #[test]
    fn identity_without_guard_relaunches_once() {
        assert_eq!(decide(true, None), Decision::Relaunch);
        assert_eq!(decide(true, Some("")), Decision::Relaunch);
    }

    /// 防循环：重新拉起后仍带包身份（guard 已置位）不再重试。
    #[test]
    fn identity_with_guard_never_relaunches_again() {
        for g in [RELAUNCHED, STILL_PACKAGED, FAILED, "anything"] {
            assert_eq!(decide(true, Some(g)), Decision::ProceedStillPackaged, "{g}");
        }
    }

    #[test]
    fn policy_constants_match_the_documented_values() {
        assert_eq!(ATTR_DESKTOP_APP_POLICY, 0x00020012);
        assert_eq!(BREAKAWAY_ENABLE_PROCESS_TREE, 0x01);
    }

    #[test]
    fn command_line_quotes_like_msvcrt() {
        assert_eq!(quote_arg("plain"), "plain");
        assert_eq!(quote_arg(""), "\"\"");
        assert_eq!(quote_arg("a b"), "\"a b\"");
        assert_eq!(quote_arg("say \"hi\""), "\"say \\\"hi\\\"\"");
        assert_eq!(
            quote_arg("C:\\dir with space\\"),
            "\"C:\\dir with space\\\\\""
        );
        assert_eq!(
            command_line(
                "C:\\Program Files\\Tavotto\\Tavotto.exe",
                &["--open".into(), "D:\\my fig".into()]
            ),
            "\"C:\\Program Files\\Tavotto\\Tavotto.exe\" --open \"D:\\my fig\""
        );
    }

    #[test]
    fn handoff_exit_code_passes_the_replacements_fate_through() {
        assert_eq!(handoff_exit_code(None), 0, "替身还活着 = 启动成功");
        assert_eq!(
            handoff_exit_code(Some(0)),
            0,
            "转发给已在跑的实例后正常退出"
        );
        assert_eq!(handoff_exit_code(Some(1)), 1);
        // 崩溃类 NTSTATUS（0xC0000005）按位透传，不能被截成 0
        assert_ne!(handoff_exit_code(Some(0xC000_0005)), 0);
    }

    /// 本进程的等待必须长于启动器的观察窗口，否则窗口内的替身崩溃观察不到。
    #[test]
    fn handoff_wait_outlasts_the_launchers_observation_window() {
        let py = include_str!("../../src/tavotto/engine/handoff.py");
        let line = py
            .lines()
            .find(|l| l.starts_with("LAUNCH_CRASH_WINDOW ="))
            .expect("handoff.py 里找不到 LAUNCH_CRASH_WINDOW");
        let secs: f64 = line.split('=').nth(1).unwrap().trim().parse().unwrap();
        assert!(f64::from(HANDOFF_WAIT_MS) / 1000.0 > secs, "{secs}");
    }

    // ---- main() 里「脱包在崩溃过滤器之后、Tauri Builder 之前」的结构看护（AST，不按子串）----

    /// 顶层语句里「可执行调用」的路径（如 `msix_breakaway::relaunch_outside_package_if_needed`）。
    struct CallPaths(Vec<String>);

    impl<'ast> syn::visit::Visit<'ast> for CallPaths {
        fn visit_expr_call(&mut self, call: &'ast syn::ExprCall) {
            if let syn::Expr::Path(p) = &*call.func {
                let segs: Vec<String> = p
                    .path
                    .segments
                    .iter()
                    .map(|s| s.ident.to_string())
                    .collect();
                self.0.push(segs.join("::"));
            }
            syn::visit::visit_expr_call(self, call);
        }
    }

    /// 解析源码，取 `fn main` 的顶层语句，返回三者所在的顶层语句序号并校验顺序。
    /// 注释被解析器丢掉、字符串字面量不是调用，所以注释掉调用必然失败。
    fn check_main_order(src: &str) -> Result<(), String> {
        use syn::visit::Visit;
        let file = syn::parse_file(src).map_err(|e| e.to_string())?;
        let main = file
            .items
            .iter()
            .find_map(|i| match i {
                syn::Item::Fn(f) if f.sig.ident == "main" => Some(f),
                _ => None,
            })
            .ok_or("找不到 fn main")?;
        let position = |want: &str| -> Option<usize> {
            main.block.stmts.iter().position(|stmt| {
                let mut c = CallPaths(Vec::new());
                c.visit_stmt(stmt);
                c.0.iter().any(|p| p == want)
            })
        };
        let crash = position("crash_dump::install").ok_or("main 没有调用 crash_dump::install")?;
        let relaunch = position("msix_breakaway::relaunch_outside_package_if_needed")
            .ok_or("main 没有调用 msix_breakaway::relaunch_outside_package_if_needed")?;
        let builder = position("tauri::Builder::default").ok_or("main 没有 Tauri Builder")?;
        if crash < relaunch && relaunch < builder {
            Ok(())
        } else {
            Err(format!(
                "顺序不对：crash={crash} relaunch={relaunch} builder={builder}"
            ))
        }
    }

    /// 兜底必须发生在 Tauri Builder 之前（单实例插件之前），且在崩溃过滤器之后。
    #[test]
    fn main_relaunches_after_the_crash_filter_and_before_the_builder() {
        let src = include_str!("main.rs");
        check_main_order(src).unwrap();
    }

    /// 自测：看护本身不能被注释 / 字符串 / 调换顺序骗过。
    #[test]
    fn main_order_check_rejects_commented_out_or_reordered_calls() {
        let src = include_str!("main.rs");
        let call = "msix_breakaway::relaunch_outside_package_if_needed();";
        assert!(src.contains(call), "main.rs 里的调用写法变了，同步这个自测");

        let line_commented = src.replace(call, &format!("// {call}"));
        assert!(check_main_order(&line_commented).is_err(), "行注释");
        let block_commented = src.replace(call, &format!("/* {call} */"));
        assert!(check_main_order(&block_commented).is_err(), "块注释");
        let in_string = src.replace(call, &format!("let _doc = \"{call}\";"));
        assert!(check_main_order(&in_string).is_err(), "字符串字面量");

        // 调换顺序：调用挪到崩溃过滤器之前
        let moved = src.replace(call, "").replacen(
            "crash_dump::install();",
            &format!("{call}\n    crash_dump::install();"),
            1,
        );
        assert!(check_main_order(&moved).is_err(), "先于崩溃过滤器");
    }
}
