//! 以 tauri-plugin-updater 的身份消费 Windows 更新包（CI 消费者保真检查）。
//!
//! 复刻上游 `updater.rs::extract_zip`（tauri-plugin-updater 2.10.1）的两步：
//! `ZipArchive::extract` 全量解包（zip crate `default-features = false`，
//! 只认 STORED 条目——依赖形态见 Cargo.toml 的注释），随后在**顶层**
//! `read_dir` 找 `.exe`。插件取第一个就执行，所以这里断言**有且仅有一个**：
//! 两个 exe 意味着「装哪个」取决于目录序，零个意味着更新器报
//! `BinaryNotFoundInArchive`。
//!
//! 退出码：0 = 解包成功且顶层恰好一个 exe；1 = 插件会失败的那些
//! （解不开 / 顶层 exe 数不对）；2 = 用法或输入文件本身读不到。

use std::path::PathBuf;
use std::process::exit;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (zip_path, out_dir) = match args.as_slice() {
        [z, o] => (PathBuf::from(z), PathBuf::from(o)),
        _ => {
            eprintln!("usage: updater-extract-probe <update.zip> <out-dir>");
            exit(2);
        }
    };

    let bytes = match std::fs::read(&zip_path) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("read {}: {e}", zip_path.display());
            exit(2);
        }
    };
    if let Err(e) = std::fs::create_dir_all(&out_dir) {
        eprintln!("create {}: {e}", out_dir.display());
        exit(2);
    }

    // 与插件同形：Cursor 上开 archive，整体 extract。deflate 条目会在这里
    // 报 "Compression method not supported"——那正是要量的能力面。
    let cursor = std::io::Cursor::new(bytes);
    let mut archive = match zip::ZipArchive::new(cursor) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("EXTRACT FAILED (open archive): {e}");
            exit(1);
        }
    };
    if let Err(e) = archive.extract(&out_dir) {
        eprintln!("EXTRACT FAILED: {e}");
        exit(1);
    }

    // 插件只扫顶层（不递归），取第一个 .exe。
    let mut exes: Vec<PathBuf> = Vec::new();
    match std::fs::read_dir(&out_dir) {
        Ok(entries) => {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("exe") {
                    exes.push(path);
                }
            }
        }
        Err(e) => {
            eprintln!("read_dir {}: {e}", out_dir.display());
            exit(2);
        }
    }
    if exes.len() != 1 {
        eprintln!(
            "TOPLEVEL EXE COUNT {} (expect exactly 1): {:?}",
            exes.len(),
            exes
        );
        exit(1);
    }
    println!("EXTRACT OK: {}", exes[0].display());
}
