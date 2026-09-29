/// 品牌常量的唯一出处是 `web/src/lib/brand.ts`（与 `engine/brand.py` 同源）。壳要在
/// webview 起来之前建菜单、给窗口起标题，运行时读不到它，于是在这里编译期读出来注入
/// `TAVOTTO_REPO_URL` / `TAVOTTO_PRODUCT_NAME`——壳里不再有第二处手写的地址与产品名。
/// `TAVOTTO_DIST_NAME`（PyPI 包名 = 命令行名，`pip install -U …` / `… --no-browser`）
/// 只在 `engine/brand.py` 里有，从那里读。读不到就让构建失败：一个空的地址或产品名比
/// 编译不过更难发现。
fn brand_const(path: &str, decl: &str, quote: char) -> String {
    println!("cargo:rerun-if-changed={path}");
    let src = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("读不到 {path}：{e}"));
    let value = src
        .lines()
        .find_map(|l| l.strip_prefix(decl)?.split(quote).next())
        .unwrap_or_else(|| panic!("{path} 里没有 `{decl}…{quote}` 这一行"));
    assert!(!value.trim().is_empty(), "{path} 的 `{decl}` 是空的");
    value.to_string()
}

fn inject_brand() {
    const BRAND_TS: &str = "../web/src/lib/brand.ts";
    const BRAND_PY: &str = "../src/tavotto/engine/brand.py";
    let url = brand_const(BRAND_TS, "export const REPO_URL = '", '\'');
    assert!(
        url.starts_with("https://"),
        "{BRAND_TS} 的 REPO_URL 不像地址：{url}"
    );
    println!("cargo:rustc-env=TAVOTTO_REPO_URL={url}");
    let product = brand_const(BRAND_TS, "export const PRODUCT_NAME = '", '\'');
    println!("cargo:rustc-env=TAVOTTO_PRODUCT_NAME={product}");
    let dist = brand_const(BRAND_PY, "DIST_NAME = \"", '"');
    println!("cargo:rustc-env=TAVOTTO_DIST_NAME={dist}");
}

fn main() {
    inject_brand();
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
