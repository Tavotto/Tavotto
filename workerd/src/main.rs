//! `tavotto-workerd` 的命令行入口——只解析几个自述参数，实现全在 lib 里。

use tavotto_workerd::protocol::SUPERVISOR_PROTOCOL_VERSION;

const USAGE: &str = "\
tavotto-workerd —— Tavotto 渲染 worker 的 supervisor

用法: tavotto-workerd [选项]
  --version            打印版本
  --protocol-version   打印 supervisor 协议版本
  -h, --help           打印这段

不带选项时从 stdin 读 JSON 行协议（一行一条请求），响应写 stdout。
";

fn main() {
    // 参数只有几个自述开关，互斥且都不带值——多给一个就是调用方写错了。
    match std::env::args().nth(1).as_deref() {
        None => tavotto_workerd::serve(),
        Some("--version") => println!("{}", env!("CARGO_PKG_VERSION")),
        Some("--protocol-version") => println!("{SUPERVISOR_PROTOCOL_VERSION}"),
        Some("-h") | Some("--help") => print!("{USAGE}"),
        Some(other) => {
            eprintln!("未知参数: {other}\n{USAGE}");
            std::process::exit(2);
        }
    }
}
