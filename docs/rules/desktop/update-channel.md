# 更新通道（桌面归壳）

> 原文出自 `src-tauri/AGENTS.md`「更新通道（桌面归壳，2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 60
改到: `src-tauri/src/app_update.rs`、`tauri.conf.json`、`capabilities/main.json`、`scripts/make_updater_manifest.py`、`MIRROR_MANIFEST_URL`
主题: 桌面更新：tauri-plugin-updater 与来源编排（ADR 0121） → `update-channel.md`
必守要点: 桌面关掉 `/api/update/*`、只查一条通道；webview 不拿 `updater:*`；第一个答上来的来源说「无更新」即终局，下载失败换来源重新 check；验签永远用内置公钥；macOS 更新包签名公证后重做
-->

- **桌面版走 `tauri-plugin-updater`**：下载签名过的安装包就地替换，装完
  `relaunch`。两条通道互斥——后端在桌面模式把 `/api/update/*` 整个关掉，
  前端 `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条。
  更新包的 minisign 私钥只在 CI，公钥写死在 tauri.conf.json；**没配私钥时
  构建就地关掉 createUpdaterArtifacts 并打 warning**，安装包照发。
  **macOS 的更新包必须在签名/公证之后重做**——tauri build 顺手打的那份装的是
  没签名的 .app，换上去 Gatekeeper 当场拦。清单 `latest.json` 由
  `scripts/make_updater_manifest.py` 在三条 matrix 腿（macOS arm64 / macOS x86_64 / Windows，ADR 0076）都跑完后合成
  （少了它壳永远显示「已是最新」而 CI 全绿）。细节见 ADR 0002 末节。
- 桌面模式下 Python updater 停用（升级归 Tauri 层），`/api/update/*` 回
  禁用响应；浏览器模式照旧。
- **来源编排在 `src-tauri/src/app_update.rs`（ADR 0121）**：国内 COS 镜像 + GitHub 两个来源，顺序由时区 / 系统语言 / 界面语言
  的**唯一**判据 `source_order` 定；前端只调 `desktop_update_check` / `desktop_update_install` 两个命令，
  `capabilities/main.json` **没有** `updater:*`（webview 绕不过编排）。镜像地址是该文件里唯一的常量
  `MIRROR_MANIFEST_URL`，`tauri.conf.json` 的 endpoints 仍只有 GitHub 一条（发布链读它）。三条别改坏：
  ① 第一个**答上来**的来源说「无更新」就是最终答案，不再问另一个；② 下载失败（网络 / 非 2xx / 验签 / 停滞）
  换来源**重新 check + 重新下**，且回退来源必须报同一个版本；③ 验签永远用内置公钥，来源不影响信任。
  e2e 触发口 `TAVOTTO_E2E_UPDATE_SOURCE=mirror|github` 钉死单来源且不回退。
  看护：`app_update.rs` 的顺序单测 + `app_update_flow_tests.rs`（本地假 HTTP 服务 + 测试密钥签名的包，从不调用 `apply`）。
