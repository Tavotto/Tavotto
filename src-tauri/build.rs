/// 仓库地址的唯一出处是 `web/src/lib/brand.ts`（与 `engine/brand.py` 同源）。壳要在
/// webview 起来之前建菜单，运行时读不到它，于是在这里编译期读出来注入
/// `TAVOTTO_REPO_URL`——壳里不再有第二处手写的地址。读不到就让构建失败：
/// 一个空地址的「报告问题」比编译不过更难发现。
fn inject_repo_url() {
    const BRAND: &str = "../web/src/lib/brand.ts";
    const DECL: &str = "export const REPO_URL = '";
    println!("cargo:rerun-if-changed={BRAND}");
    let src = std::fs::read_to_string(BRAND).unwrap_or_else(|e| panic!("读不到 {BRAND}：{e}"));
    let url = src
        .lines()
        .find_map(|l| l.strip_prefix(DECL)?.strip_suffix('\''))
        .unwrap_or_else(|| panic!("{BRAND} 里没有 `{DECL}…'` 这一行"));
    assert!(
        url.starts_with("https://"),
        "{BRAND} 的 REPO_URL 不像地址：{url}"
    );
    println!("cargo:rustc-env=TAVOTTO_REPO_URL={url}");
}

fn main() {
    inject_repo_url();
    // Tauri 2 的 ACL 对**应用自定义命令**同样生效：不在这里声明，build 就不会
    // 生成 `allow-reveal-export` 权限，capability 也就无从允许它——前端 invoke
    // 会被静默拒绝（导出对话框「在文件管理器中显示」点了没反应就是这么来的）。
    // 新增 #[tauri::command] 时必须同步三处：这里、capabilities/ 里给它的那个窗口（main.json = 主窗口，remote*.json = 远程实例窗口）、
    // main.rs 的 generate_handler。
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "reveal_export",
            "reveal_project_dir",
            "set_menu_locale",
            "codex_integration",
            "arm_close_guard",
            "resolve_close_request",
            "native_file_drop",
            "connect_remote",
        ]),
    ))
    .expect("failed to run tauri-build");
}
