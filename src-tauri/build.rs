fn main() {
    // Tauri 2 的 ACL 对**应用自定义命令**同样生效：不在这里声明，build 就不会
    // 生成 `allow-reveal-export` 权限，capability 也就无从允许它——前端 invoke
    // 会被静默拒绝（导出对话框「在文件管理器中显示」点了没反应就是这么来的）。
    // 新增 #[tauri::command] 时必须同步三处：这里、capabilities/main.json（identifier main-window）、
    // main.rs 的 generate_handler。
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "reveal_export",
            "set_menu_locale",
            "codex_integration",
        ]),
    ))
    .expect("failed to run tauri-build");
}
