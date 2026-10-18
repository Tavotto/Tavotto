//! Python sidecar 的解析、启动、握手与退出。
//!
//! 进程关系与协议见 docs/adr/0002-tauri-desktop-shell.md：
//! - 启动 nonce 经 **stdin 首行 JSON** 传入（环境变量对同用户进程可见，管道不可见）；
//!   之后这条管道保持打开，EOF 就是「壳没了」的信号，sidecar 据此自行退出。
//! - sidecar 把 ready/port/pid（或失败原因）原子写进握手文件；文件里没有任何密钥。
//! - 退出：先关 stdin（触发 sidecar 优雅关停，连带 worker/AI 子进程），
//!   限时等不到再 kill —— 任何路径都不留孤儿进程。
//! - 端口：stdin 首行同时带上次记住的 `preferred_port`，sidecar 优先绑它，让窗口的
//!   origin（进而 localStorage）跨重启稳定；记忆策略在 [`port_memory`]（ADR 0108）。

mod port_memory;

use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Deserialize;

/// 握手超时：PyInstaller onedir 冷启动通常 1–3 秒，Windows 上杀软扫描可能拖长。
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(60);
/// 优雅退出等待：sidecar 关 worker（wait=True, 6s）之后才退，给足余量。
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);

#[derive(Deserialize)]
struct Handshake {
    ready: bool,
    port: Option<u16>,
    #[allow(dead_code)]
    pid: Option<u32>,
    error: Option<String>,
}

/// 启动失败：给用户看的原文（走 error.html 的 `msg`，不翻译、不进报告）+ 结构化事实（阶段 / 稳定码 / 退出码，
/// 进 shell 最小报告，见 `rescue.rs`）。两者分开，报告里就没有任何自由文本的入口。
pub struct StartError {
    pub message: String,
    pub info: crate::rescue::FailureInfo,
}

impl StartError {
    fn new(stage: &'static str, code: &'static str, message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            info: crate::rescue::FailureInfo::new(stage, code),
        }
    }
}

pub struct Sidecar {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    pub log_path: PathBuf,
}

/// 解析 sidecar 可执行文件。优先级：
/// 1. `TAVOTTO_SIDECAR_EXE`（开发/排障覆盖，可配 `TAVOTTO_SIDECAR_ARGS`）
/// 2. 打包资源 `resources/sidecar/Tavotto/Tavotto(.exe)`（PyInstaller onedir）
/// 3. 源码树回退：从可执行文件与 cwd 向上找 `pyproject.toml` 旁的 `.venv` 里的 tavotto
fn resolve_command(
    resource_dir: Option<&Path>,
    locale: crate::i18n::Locale,
) -> Result<(PathBuf, Vec<String>), String> {
    let m = crate::i18n::text(locale);
    if let Ok(exe) = std::env::var("TAVOTTO_SIDECAR_EXE") {
        let args = std::env::var("TAVOTTO_SIDECAR_ARGS")
            .map(|s| s.split_whitespace().map(String::from).collect())
            .unwrap_or_default();
        let p = PathBuf::from(exe);
        if !p.is_file() {
            return Err(m
                .sidecar_exe_missing
                .replace("{path}", &p.display().to_string()));
        }
        return Ok((p, args));
    }

    let exe_name = if cfg!(windows) {
        "Tavotto.exe"
    } else {
        "Tavotto"
    };
    if let Some(res) = resource_dir {
        let bundled = res.join("sidecar").join("Tavotto").join(exe_name);
        if bundled.is_file() {
            return Ok((bundled, Vec::new()));
        }
    }

    if let Some(cli) = source_tree_venv_bin() {
        return Ok((cli, Vec::new()));
    }
    Err(m.sidecar_not_found.into())
}

/// 源码树回退：从可执行文件与 cwd 向上找 `pyproject.toml` 旁 `.venv` 里的 `tavotto`。
///
/// **sidecar 与 `tavotto-cli` 在开发形态下是同一个入口**（`.venv/bin/tavotto`
/// 既能 `--desktop-sidecar` 也能 `codex install`），所以这段查找只有一份——
/// 抄第二份的结果是「开发机上按钮能用、sidecar 起不来」这类各修各的的怪状。
fn source_tree_venv_bin() -> Option<PathBuf> {
    let rel = if cfg!(windows) {
        "Scripts\\tavotto.exe"
    } else {
        "bin/tavotto"
    };
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(me) = std::env::current_exe() {
        starts.push(me);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        for dir in start.ancestors() {
            if dir.join("pyproject.toml").is_file() {
                let cli = dir.join(".venv").join(rel);
                if cli.is_file() {
                    return Some(cli);
                }
            }
        }
    }
    None
}

/// 解析安装包里那个 **console 版 `tavotto-cli`**——「安装 Codex 集成」按钮
/// spawn 的就是它（ADR 0012）。
///
/// **壳里没有第二套安装器。** 按钮的全部职责是跑
/// `tavotto-cli codex install --json` 并渲染那一行 JSON；安装步骤（marketplace、
/// 插件、引擎、体检）一条都不在 Rust 里，也不该在前端里。
///
/// 优先级与 sidecar 同源：`TAVOTTO_CLI_EXE` 覆盖 → 打包资源
/// `resources/sidecar/Tavotto/tavotto-cli(.exe)`（与 sidecar 同目录，
/// `packaging/tavotto.spec` 让两个 exe 共用一份 `_internal/`）→ 源码树 `.venv`。
///
/// **GUI 那个 `Tavotto` exe 不能拿来当 CLI 用**：它是 windows 子系统的产物，
/// 没有终端时 stdout 落进日志，我们等的那行 JSON 永远回不来。
pub fn resolve_cli(resource_dir: Option<&Path>) -> Result<PathBuf, String> {
    if let Ok(exe) = std::env::var("TAVOTTO_CLI_EXE") {
        let p = PathBuf::from(exe);
        return if p.is_file() {
            Ok(p)
        } else {
            Err("cli_not_found".into())
        };
    }
    let cli_name = if cfg!(windows) {
        "tavotto-cli.exe"
    } else {
        "tavotto-cli"
    };
    if let Some(res) = resource_dir {
        let bundled = res.join("sidecar").join("Tavotto").join(cli_name);
        if bundled.is_file() {
            return Ok(bundled);
        }
    }
    source_tree_venv_bin().ok_or_else(|| "cli_not_found".to_string())
}

/// `{path}` + `{err}` 两个占位符一起填。文案模板里的占位符名字与
/// `i18n.rs` 里那几条严格对应——两侧都是纯字符串替换，没有格式化宏参与，
/// 所以加一条文案不必再动这里。
fn fill2(template: &str, path: &str, err: &str) -> String {
    template.replace("{path}", path).replace("{err}", err)
}

/// 写 hello 失败后，等子进程退出的上限。
const EXIT_GRACE: Duration = Duration::from_secs(2);

/// 握手阶段发现 sidecar 已退出：带退出码，stderr 尾巴只进 message。
fn exited_error(
    status: std::process::ExitStatus,
    log_path: &Path,
    m: &crate::i18n::ShellText,
) -> StartError {
    use crate::rescue::{code, stage};
    let tail = log_tail(log_path, 2000);
    let mut e = StartError::new(
        stage::HANDSHAKE,
        code::SIDECAR_EXITED,
        m.sidecar_exited
            .replace("{status}", &status.to_string())
            .replace("{tail}", &tail),
    );
    e.info.exit_code = status.code();
    e
}

/// 把 hello 行写进 sidecar 的 stdin。
///
/// 写失败（EPIPE）多半是 sidecar 在父进程写之前就退出了（Python 一启动 import 失败等）：
/// 此时报 `handshake / sidecar_exited` + 退出码，与握手循环里发现退出走同一条路径；
/// 只有子进程仍在运行时才是真正的「stdin 写不进去」。
fn send_hello(
    child: &mut Child,
    stdin: &mut ChildStdin,
    line: &str,
    log_path: &Path,
    m: &crate::i18n::ShellText,
) -> Result<(), StartError> {
    use crate::rescue::{code, stage};
    let err = match stdin
        .write_all(line.as_bytes())
        .and_then(|()| stdin.flush())
    {
        Ok(()) => return Ok(()),
        Err(e) => e,
    };
    // 管道断了但进程还没被回收：短暂轮询，给它一点时间把退出状态交出来
    let deadline = Instant::now() + EXIT_GRACE;
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(exited_error(status, log_path, m));
        }
        if Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = child.kill();
    Err(StartError::new(
        stage::LAUNCH,
        code::SIDECAR_STDIN_FAILED,
        m.sidecar_stdin_write_failed
            .replace("{err}", &err.to_string()),
    ))
}

fn log_tail(path: &Path, max: u64) -> String {
    let Ok(mut f) = std::fs::File::open(path) else {
        return String::new();
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(max)));
    let mut buf = String::new();
    let _ = f.read_to_string(&mut buf);
    buf
}

impl Sidecar {
    /// 启动 sidecar 并等到握手完成，返回 (Sidecar, 端口)。
    /// `project` 是首启交接（`Tavotto --open <目录>`）带来的项目目录：原样转成
    /// sidecar 的 `--figures`。**不做任何存在性判断**——目录有没有、注册表长
    /// 什么样，是 Python 那边（app.open_project）唯一说了算的事，壳里再判一次
    /// 只会制造第二个权威，还会在两边给出不一样的错误。
    pub fn start(
        resource_dir: Option<PathBuf>,
        log_dir: &Path,
        nonce: &str,
        project: Option<&str>,
        // `app_config_dir()`：端口记忆文件放这里（与 `menu-locale` 同一处）；拿不到就不记
        config_dir: Option<&Path>,
        // 起不来时这些话会显示在 error.html 上，得说用户选的那门语言
        locale: crate::i18n::Locale,
    ) -> Result<(Sidecar, u16), StartError> {
        use crate::rescue::{code, stage};
        let m = crate::i18n::text(locale);
        let (exe, extra_args) = resolve_command(resource_dir.as_deref(), locale)
            .map_err(|e| StartError::new(stage::LOCATE, code::SIDECAR_NOT_FOUND, e))?;

        std::fs::create_dir_all(log_dir).map_err(|e| {
            StartError::new(
                stage::LAUNCH,
                code::SIDECAR_LOG_UNAVAILABLE,
                fill2(
                    m.sidecar_log_dir_failed,
                    &log_dir.display().to_string(),
                    &e.to_string(),
                ),
            )
        })?;
        let log_path = log_dir.join("sidecar.log");
        let log_out = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| {
                StartError::new(
                    stage::LAUNCH,
                    code::SIDECAR_LOG_UNAVAILABLE,
                    fill2(
                        m.sidecar_log_open_failed,
                        &log_path.display().to_string(),
                        &e.to_string(),
                    ),
                )
            })?;
        let log_err = log_out.try_clone().map_err(|e| {
            StartError::new(
                stage::LAUNCH,
                code::SIDECAR_LOG_UNAVAILABLE,
                m.sidecar_log_clone_failed.replace("{err}", &e.to_string()),
            )
        })?;

        let handshake = std::env::temp_dir().join(format!(
            "tavotto-handshake-{}-{:016x}.json",
            std::process::id(),
            rand::random::<u64>()
        ));
        let _ = std::fs::remove_file(&handshake);

        let mut cmd = Command::new(&exe);
        cmd.args(&extra_args);
        if let Some(dir) = project {
            cmd.arg("--figures").arg(dir);
        }
        cmd.arg("--desktop-sidecar")
            .env("TAVOTTO_DESKTOP_HANDSHAKE", &handshake)
            .stdin(Stdio::piped())
            .stdout(Stdio::from(log_out))
            .stderr(Stdio::from(log_err));
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd.spawn().map_err(|e| {
            StartError::new(
                stage::LAUNCH,
                code::SIDECAR_SPAWN_FAILED,
                fill2(
                    m.sidecar_spawn_failed,
                    &exe.display().to_string(),
                    &e.to_string(),
                ),
            )
        })?;

        // 凭据走 stdin 首行；这条管道随后保持打开作为「父进程还活着」的信号。
        // 同一行带上建议端口：sidecar 占得到就用它，占不到自己退回系统分配（desktop.py）
        let memory_path = port_memory::path(config_dir);
        let stored = port_memory::load(memory_path.as_deref());
        let preferred = port_memory::preferred(stored, rand::random::<u16>());
        let mut stdin = child.stdin.take().ok_or_else(|| {
            let _ = child.kill();
            StartError::new(
                stage::LAUNCH,
                code::SIDECAR_STDIN_FAILED,
                m.sidecar_stdin_missing,
            )
        })?;
        let mut hello = serde_json::json!({
            "nonce": nonce,
            "parent_pid": std::process::id(),
        });
        hello[port_memory::HELLO_FIELD] = serde_json::json!(preferred);
        send_hello(&mut child, &mut stdin, &format!("{hello}\n"), &log_path, m)?;

        let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
        let port = loop {
            if let Ok(text) = std::fs::read_to_string(&handshake) {
                if let Ok(hs) = serde_json::from_str::<Handshake>(&text) {
                    let _ = std::fs::remove_file(&handshake);
                    if hs.ready {
                        match hs.port {
                            Some(p) => break p,
                            None => {
                                let _ = child.kill();
                                return Err(StartError::new(
                                    stage::HANDSHAKE,
                                    code::SIDECAR_HANDSHAKE_FAILED,
                                    m.sidecar_handshake_no_port,
                                ));
                            }
                        }
                    }
                    let _ = child.kill();
                    return Err(StartError::new(
                        stage::HANDSHAKE,
                        code::SIDECAR_HANDSHAKE_FAILED,
                        hs.error.unwrap_or_else(|| m.sidecar_start_failed.into()),
                    ));
                }
            }
            if let Ok(Some(status)) = child.try_wait() {
                return Err(exited_error(status, &log_path, m));
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                return Err(StartError::new(
                    stage::HANDSHAKE,
                    code::SIDECAR_TIMEOUT,
                    m.sidecar_timeout,
                ));
            }
            std::thread::sleep(Duration::from_millis(100));
        };

        let next = port_memory::after_launch(stored, preferred, port);
        if let Some(mem) = next.filter(|n| Some(*n) != stored) {
            port_memory::store(memory_path.as_deref(), &mem);
        }

        Ok((
            Sidecar {
                child: Mutex::new(Some(child)),
                stdin: Mutex::new(Some(stdin)),
                log_path,
            },
            port,
        ))
    }

    /// 引擎有没有自己（意外）退出了。非阻塞。
    ///
    /// - 还在跑 / 已被 [`Sidecar::shutdown`] 收走（`child` 被 take）→ `None`：
    ///   **shutdown 之后的 `None` 是正常关闭，不是崩溃**；
    /// - 已退出 → 回收子进程（置空，之后 `shutdown` 是空操作）并返回闭集类型，
    ///   不带 status 文本与日志（退出标记的内容见 `exit_marker`）。
    pub fn poll_exit(&self) -> Option<crate::exit_marker::EngineExit> {
        let mut guard = self.child.lock().unwrap();
        let status = guard.as_mut()?.try_wait().ok().flatten()?;
        guard.take();
        drop(self.stdin.lock().unwrap().take());
        Some(crate::exit_marker::EngineExit::classify(status.code()))
    }

    /// 优雅退出：关 stdin（EOF → sidecar 自行收 worker/AI 并退出），
    /// 限时等不到再 kill。幂等。
    pub fn shutdown(&self) {
        drop(self.stdin.lock().unwrap().take());
        let Some(mut child) = self.child.lock().unwrap().take() else {
            return;
        };
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(100));
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::exit_marker::EngineExit;

    fn fake(args: &[&str]) -> Sidecar {
        #[cfg(unix)]
        let mut cmd = Command::new("sh");
        #[cfg(unix)]
        cmd.arg("-c");
        #[cfg(windows)]
        let mut cmd = Command::new("cmd");
        #[cfg(windows)]
        cmd.arg("/C");
        cmd.args(args).stdin(Stdio::piped());
        let mut child = cmd.spawn().unwrap();
        let stdin = child.stdin.take();
        Sidecar {
            child: Mutex::new(Some(child)),
            stdin: Mutex::new(stdin),
            log_path: PathBuf::new(),
        }
    }

    fn wait_exit(sc: &Sidecar) -> Option<EngineExit> {
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if let Some(k) = sc.poll_exit() {
                return Some(k);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        None
    }

    #[test]
    fn poll_exit_reports_a_nonzero_exit_once() {
        let sc = fake(&["exit 3"]);
        assert_eq!(wait_exit(&sc), Some(EngineExit::Nonzero));
        assert_eq!(sc.poll_exit(), None, "reported once");
        sc.shutdown(); // 之后的 shutdown 是空操作
    }

    #[test]
    fn poll_exit_reports_a_zero_exit() {
        let sc = fake(&["exit 0"]);
        assert_eq!(wait_exit(&sc), Some(EngineExit::Zero));
    }

    #[cfg(unix)]
    #[test]
    fn poll_exit_reports_a_signal_death() {
        let sc = fake(&["kill -9 $$"]);
        assert_eq!(wait_exit(&sc), Some(EngineExit::Signal));
    }

    #[test]
    fn a_running_engine_is_not_an_exit() {
        #[cfg(unix)]
        let sc = fake(&["cat >/dev/null"]); // 读 stdin 直到 EOF
        #[cfg(windows)]
        let sc = fake(&["more >nul"]);
        assert_eq!(sc.poll_exit(), None);
        sc.shutdown();
    }

    #[test]
    fn poll_exit_after_shutdown_is_none_not_a_crash() {
        #[cfg(unix)]
        let sc = fake(&["cat >/dev/null"]);
        #[cfg(windows)]
        let sc = fake(&["more >nul"]);
        sc.shutdown(); // 正常关闭：EOF → 子进程以 0 退出
        assert_eq!(sc.poll_exit(), None);
    }
    // ---- 启动失败的结构化事实（阶段 / 稳定码 / 退出码）：shell 最小报告只读这些，不读错误原文 ----
    fn scratch(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "tavotto-sidecar-test-{tag}-{}-{:08x}",
            std::process::id(),
            rand::random::<u32>()
        ));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// 在假资源目录里放一个「打包的 sidecar」。
    fn bundled(res: &Path, body: &[u8]) {
        let exe = res.join("sidecar").join("Tavotto").join(if cfg!(windows) {
            "Tavotto.exe"
        } else {
            "Tavotto"
        });
        std::fs::create_dir_all(exe.parent().unwrap()).unwrap();
        std::fs::write(&exe, body).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
    }

    fn start_in(res: &Path, log_dir: &Path) -> StartError {
        match Sidecar::start(
            Some(res.to_path_buf()),
            log_dir,
            "n",
            None,
            None,
            crate::i18n::DEFAULT_LOCALE,
        ) {
            Ok(_) => panic!("must not start"),
            Err(e) => e,
        }
    }

    #[test]
    fn an_unwritable_log_dir_is_a_launch_failure_with_a_stable_code() {
        let res = scratch("res");
        bundled(&res, b"x");
        let blocker = scratch("blk").join("file");
        std::fs::write(&blocker, b"x").unwrap();
        // 日志目录建在一个普通文件下面：create_dir_all 必然失败
        let e = start_in(&res, &blocker.join("logs"));
        assert_eq!(
            (e.info.stage, e.info.code, e.info.exit_code),
            ("launch", "sidecar_log_unavailable", None)
        );
        assert!(
            !e.message.is_empty(),
            "the human-readable message is still there"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_sidecar_that_dies_during_the_handshake_reports_its_exit_code_not_its_stderr() {
        let res = scratch("res");
        bundled(&res, b"#!/bin/sh\necho SECRET_STDERR_TEXT >&2\nexit 3\n");
        let logs = scratch("logs");
        let e = start_in(&res, &logs);
        assert_eq!(
            (e.info.stage, e.info.code, e.info.exit_code),
            ("handshake", "sidecar_exited", Some(3))
        );
        // 原文（含 stderr 尾巴）只在 message 里；结构化事实里没有任何文本
        assert!(e.message.contains("SECRET_STDERR_TEXT"));
        let facts = serde_json::to_string(&e.info).unwrap();
        assert!(!facts.contains("SECRET_STDERR_TEXT"));
    }

    /// 起一个 `sh -c body` 子进程，返回 (child, stdin)。
    #[cfg(unix)]
    fn spawn_sh(body: &str) -> (Child, ChildStdin) {
        let mut child = Command::new("sh")
            .args(["-c", body])
            .stdin(Stdio::piped())
            .spawn()
            .unwrap();
        let stdin = child.stdin.take().unwrap();
        (child, stdin)
    }

    /// 写 hello 失败且子进程已退出（确定性：先等它退出、读端必已关闭）→ sidecar_exited + 退出码。
    #[cfg(unix)]
    #[test]
    fn a_hello_write_to_an_already_dead_sidecar_reports_its_exit_code() {
        let (mut child, mut stdin) = spawn_sh("echo SECRET_STDERR_TEXT >&2; exit 3");
        while child.try_wait().unwrap().is_none() {
            std::thread::sleep(Duration::from_millis(10));
        }
        let log = scratch("log").join("sidecar.log");
        std::fs::write(&log, b"SECRET_STDERR_TEXT\n").unwrap();
        let m = crate::i18n::text(crate::i18n::DEFAULT_LOCALE);
        let e = send_hello(&mut child, &mut stdin, "{}\n", &log, m).unwrap_err();
        assert_eq!(
            (e.info.stage, e.info.code, e.info.exit_code),
            ("handshake", "sidecar_exited", Some(3))
        );
        assert!(e.message.contains("SECRET_STDERR_TEXT"));
    }

    /// 写失败而子进程仍在跑（自己关了 stdin 读端）→ 仍是 sidecar_stdin_failed，且不留孤儿。
    #[cfg(unix)]
    #[test]
    fn a_hello_write_to_a_live_sidecar_with_closed_stdin_is_a_stdin_failure() {
        let (mut child, mut stdin) = spawn_sh("exec 0<&-; sleep 30");
        let log = scratch("log").join("sidecar.log");
        let m = crate::i18n::text(crate::i18n::DEFAULT_LOCALE);
        // 等 sh 真把 stdin 关掉：写到失败为止（sh 启动前管道缓冲区能吃下小写入）
        let mut last = Ok(());
        for _ in 0..200 {
            last = send_hello(&mut child, &mut stdin, "{}\n", &log, m);
            if last.is_err() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let e = last.unwrap_err();
        assert_eq!(
            (e.info.stage, e.info.code, e.info.exit_code),
            ("launch", "sidecar_stdin_failed", None)
        );
        assert!(child.wait().is_ok(), "killed, no orphan");
    }

    #[cfg(unix)]
    #[test]
    fn a_sidecar_that_cannot_be_executed_is_a_spawn_failure() {
        let res = scratch("res");
        bundled(&res, b"x");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let exe = res.join("sidecar").join("Tavotto").join("Tavotto");
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o644)).unwrap();
        }
        let e = start_in(&res, &scratch("logs"));
        assert_eq!(
            (e.info.stage, e.info.code),
            ("launch", "sidecar_spawn_failed")
        );
    }

    #[test]
    fn every_start_failure_code_in_the_source_is_in_the_closed_set() {
        // 源码里所有 `StartError::new(stage::X, code::Y, …)` 的 Y 都必须在 rescue::CODES 里（编译已保证存在，
        // 这里再保证没有人绕过常量直接写字符串字面量）
        let src = include_str!("sidecar.rs");
        let prod = &src[..src.find("#[cfg(test)]\nmod tests").unwrap()];
        assert!(!prod.contains("StartError::new(\"") && !prod.contains("FailureInfo::new(\""));
        assert!(!prod.contains("code: \""));
    }
}
