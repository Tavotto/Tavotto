//! `magplot-workerd`——Magplot 渲染 worker 的 supervisor。
//!
//! Flask（`engine/workerd_client.py`）用一条 stdio JSON 行协议驱动它；它再按
//! worker 协议 v1（ADR 0003）驱动一到多个渲染子进程。契约见
//! `docs/adr/0004-workerd-supervisor.md`。
//!
//! **机制在这里，策略在 Python**：解释器探测、内置 runtime 的 env/args、超时档位、
//! 会话上限，全部由 Flask 算好随请求带过来。这里只负责生命周期与可靠性：
//! 握手、代序隔离、队列合并、有界拒绝、超时强杀、取消、LRU 淘汰。
//!
//! 拆出 lib 是为了让集成测试（golden vectors）能直接调 `patchspec`——
//! 二进制 crate 的模块外面看不见，只能靠 spawn 自己，那样连一条断言都难写清楚。

pub mod patchspec;
pub mod protocol;
pub mod pyfloat;
pub mod session;
pub mod supervisor;
pub mod worker;

use std::io::{BufRead, BufWriter, Write};
use std::sync::mpsc::channel;

use protocol::{Request, Response};
use supervisor::Supervisor;

/// 主循环：stdin 一行一条请求，stdout 一行一条响应。
pub fn serve() {
    let (out_tx, out_rx) = channel::<Response>();

    // 单写线程：Flask 多个线程共用一条管道，响应必须整行原子地出去，
    // 交错半行会让对面的 reader 当场解析失败。
    let writer = std::thread::Builder::new()
        .name("workerd-stdout".into())
        .spawn(move || {
            let stdout = std::io::stdout();
            let mut out = BufWriter::new(stdout.lock());
            for response in out_rx {
                if out.write_all(response.to_line().as_bytes()).is_err()
                    || out.write_all(b"\n").is_err()
                    || out.flush().is_err()
                {
                    break; // 对面没了，再写也没有意义
                }
            }
        })
        .expect("起 stdout 写线程");

    let mut sup = Supervisor::new(out_tx.clone());
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let response = match Request::parse(&line) {
            Ok(req) => sup.dispatch(req),
            Err(failure) => {
                let (rid, err) = *failure;
                Some(Response::err(rid, err))
            }
        };
        if let Some(response) = response {
            if out_tx.send(response).is_err() {
                break;
            }
        }
        if sup.stopping {
            break;
        }
    }

    // stdin EOF = Flask 没了。一个渲染子进程都不许留在用户机器上。
    sup.close_all();
    drop(sup);
    drop(out_tx);

    // 会话线程都被 force close 叫醒了，join 应当立刻返回；真卡住也不许让
    // workerd 自己变成那个不肯退出的进程。
    let _watchdog = std::thread::Builder::new()
        .name("workerd-exit-watchdog".into())
        .spawn(|| {
            std::thread::sleep(std::time::Duration::from_secs(5));
            std::process::exit(0);
        });
    let _ = writer.join();
}
