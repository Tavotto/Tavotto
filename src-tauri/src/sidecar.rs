//! Python sidecar 的解析、启动、握手与退出。
//!
//! 进程关系与协议见 docs/adr/0002-tauri-desktop-shell.md：
//! - 启动 nonce 经 **stdin 首行 JSON** 传入（环境变量对同用户进程可见，管道不可见）；
//!   之后这条管道保持打开，EOF 就是「壳没了」的信号，sidecar 据此自行退出。
//! - sidecar 把 ready/port/pid（或失败原因）原子写进握手文件；文件里没有任何密钥。
//! - 退出：先关 stdin（触发 sidecar 优雅关停，连带 worker/AI 子进程），
//!   限时等不到再 kill —— 任何路径都不留孤儿进程。

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

pub struct Sidecar {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    pub log_path: PathBuf,
}

/// 解析 sidecar 可执行文件。优先级：
/// 1. `MAGPLOT_SIDECAR_EXE`（开发/排障覆盖，可配 `MAGPLOT_SIDECAR_ARGS`）
/// 2. 打包资源 `resources/sidecar/Magplot/Magplot(.exe)`（PyInstaller onedir）
/// 3. 源码树回退：从可执行文件与 cwd 向上找 `pyproject.toml` 旁的 `.venv` 里的 magplot
fn resolve_command(
    resource_dir: Option<&Path>,
    locale: crate::i18n::Locale,
) -> Result<(PathBuf, Vec<String>), String> {
    let m = crate::i18n::text(locale);
    if let Ok(exe) = std::env::var("MAGPLOT_SIDECAR_EXE") {
        let args = std::env::var("MAGPLOT_SIDECAR_ARGS")
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
        "Magplot.exe"
    } else {
        "Magplot"
    };
    if let Some(res) = resource_dir {
        let bundled = res.join("sidecar").join("Magplot").join(exe_name);
        if bundled.is_file() {
            return Ok((bundled, Vec::new()));
        }
    }

    let cli_name = if cfg!(windows) {
        "Scripts\\magplot.exe"
    } else {
        "bin/magplot"
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
                let cli = dir.join(".venv").join(cli_name);
                if cli.is_file() {
                    return Ok((cli, Vec::new()));
                }
            }
        }
    }
    Err(m.sidecar_not_found.into())
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
    /// `project` 是首启交接（`Magplot --open <目录>`）带来的项目目录：原样转成
    /// sidecar 的 `--figures`。**不做任何存在性判断**——目录有没有、注册表长
    /// 什么样，是 Python 那边（app.open_project）唯一说了算的事，壳里再判一次
    /// 只会制造第二个权威，还会在两边给出不一样的错误。
    pub fn start(
        resource_dir: Option<PathBuf>,
        log_dir: &Path,
        nonce: &str,
        project: Option<&str>,
        // 起不来时这些话会显示在 error.html 上，得说用户选的那门语言
        locale: crate::i18n::Locale,
    ) -> Result<(Sidecar, u16), String> {
        let m = crate::i18n::text(locale);
        let (exe, extra_args) = resolve_command(resource_dir.as_deref(), locale)?;

        std::fs::create_dir_all(log_dir)
            .map_err(|e| format!("无法创建日志目录 {}: {e}", log_dir.display()))?;
        let log_path = log_dir.join("sidecar.log");
        let log_out = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| format!("无法打开日志文件 {}: {e}", log_path.display()))?;
        let log_err = log_out
            .try_clone()
            .map_err(|e| format!("日志句柄复制失败: {e}"))?;

        let handshake = std::env::temp_dir().join(format!(
            "magplot-handshake-{}-{:016x}.json",
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
            .env("MAGPLOT_DESKTOP_HANDSHAKE", &handshake)
            .stdin(Stdio::piped())
            .stdout(Stdio::from(log_out))
            .stderr(Stdio::from(log_err));
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("无法启动渲染服务 {}: {e}", exe.display()))?;

        // 凭据走 stdin 首行；这条管道随后保持打开作为「父进程还活着」的信号
        let mut stdin = child.stdin.take().ok_or("拿不到 sidecar stdin")?;
        let hello = serde_json::json!({
            "nonce": nonce,
            "parent_pid": std::process::id(),
        });
        stdin
            .write_all(format!("{hello}\n").as_bytes())
            .and_then(|()| stdin.flush())
            .map_err(|e| {
                let _ = child.kill();
                format!("写入启动凭据失败: {e}")
            })?;

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
                                return Err(m.sidecar_handshake_no_port.into());
                            }
                        }
                    }
                    let _ = child.kill();
                    return Err(hs.error.unwrap_or_else(|| m.sidecar_start_failed.into()));
                }
            }
            if let Ok(Some(status)) = child.try_wait() {
                let tail = log_tail(&log_path, 2000);
                return Err(m
                    .sidecar_exited
                    .replace("{status}", &status.to_string())
                    .replace("{tail}", &tail));
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                return Err(m.sidecar_timeout.into());
            }
            std::thread::sleep(Duration::from_millis(100));
        };

        Ok((
            Sidecar {
                child: Mutex::new(Some(child)),
                stdin: Mutex::new(Some(stdin)),
                log_path,
            },
            port,
        ))
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
