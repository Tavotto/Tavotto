# src-tauri/ — Tauri 桌面壳规则

仓库级路由与不变量在根 `AGENTS.md`。架构与安全模型的完整版在
`docs/adr/0002-tauri-desktop-shell.md`，改动前先读。打包与内置 runtime 在
`packaging/AGENTS.md`；sidecar/引擎侧在 `src/tavotto/AGENTS.md`。

## 进程关系与安全边界（2026-08-17，与浏览器模式并行）

- **进程关系**：Tauri 壳（`src-tauri/`）→ spawn `tavotto --desktop-sidecar`
  （PyInstaller onedir，无 matplotlib）→ 现有 worker 协议。前端仍由 sidecar 的
  Flask 提供，**不走 Tauri frontendDist**——桌面与浏览器跑同一份界面。
- 会话认证与桌面/浏览器共用一道边界（ADR 0008），细节见
  `src/tavotto/AGENTS.md` 的「会话认证」。
- **桌面模式差异收在 `src/tavotto/desktop.py`**：`127.0.0.1:0` 动态端口
  （werkzeug `make_server`，可优雅 shutdown）、nonce 走 **stdin 首行**
  （环境变量对同用户进程可见；桌面**不写**磁盘凭据文件，实例复用由壳的
  单实例 argv 转发负责）、握手文件（无密钥、原子写、退出清理）、
  stdin EOF + 父 PID 双路「壳没了就自杀」（`test_desktop_sidecar.py` 看护）。
- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- **Tauri 2 的 ACL 对应用自定义命令同样生效**：新增 `#[tauri::command]` 必须
  三处同步——`build.rs` 的 `AppManifest::commands`、`capabilities/main.json`
  加 `allow-<命令名连字符化>`、`main.rs` 的 `generate_handler`。漏掉前两处
  invoke 会被**静默拒绝**（reveal_export「点了没反应」就是这么坏的）；
  失败路径不许吞——回退时把完整文件路径告诉用户。
- **关窗询问闸**（issue #223，ADR 0002 的「关窗询问闸」一节）：
  `WindowEvent::CloseRequested` → `CloseGate` → 事件 `tavotto:close-requested`
  → 前端答 `hold`/`close`/`cancel`。三条别改坏：**默认不拦**（前端 arm 之后才拦，
  splash/error 页没有监听器）、**超时只针对「前端有没有接手」**（用户想多久都行）、
  **必须留看门狗**（没有它 = 一个关不掉的窗口）。⌘Q 与系统注销不走这条路。
- 桌面交接契约 argv `--open <目录> [--stem <stem>]`：生产者唯一
  `handoff.desktop_argv()`，消费者唯一 `src-tauri/src/main.rs::parse_open_args()`，
  两侧各有单测，改一边必须同步另一边（完整交接语义见
  `src/tavotto/AGENTS.md` 的「外部交接」）。
- wheel/sdist 不含 `src-tauri/`（hatchling 白名单）；`src-tauri/target/`、
  `src-tauri/gen/` 进 .gitignore。

## 更新通道（桌面归壳，2026-08-18）

- **桌面版走 `tauri-plugin-updater`**：下载签名过的安装包就地替换，装完
  `relaunch`。两条通道互斥——后端在桌面模式把 `/api/update/*` 整个关掉，
  前端 `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条。
  更新包的 minisign 私钥只在 CI，公钥写死在 tauri.conf.json；**没配私钥时
  构建就地关掉 createUpdaterArtifacts 并打 warning**，安装包照发。
  **macOS 的更新包必须在签名/公证之后重做**——tauri build 顺手打的那份装的是
  没签名的 .app，换上去 Gatekeeper 当场拦。清单 `latest.json` 由
  `scripts/make_updater_manifest.py` 在两条 matrix 腿都跑完后合成
  （少了它壳永远显示「已是最新」而 CI 全绿）。细节见 ADR 0002 末节。
- 桌面模式下 Python updater 停用（升级归 Tauri 层），`/api/update/*` 回
  禁用响应；浏览器模式照旧。

## 构建、验收与安装界面

- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py
  --sidecar dist/Tavotto/Tavotto`（真产物全链路：认证/项目/渲染/导出/退出无孤儿）。
  CI 在 `desktop-tauri.yml`——v0.3.0 起是唯一桌面发行链（旧 `desktop.yml`/
  Inno Setup/免安装 zip 已退役删除，git 可找回）；Windows NSIS 自带内置渲染
  runtime，桌面产物一律真窗口、不再有「启动后开浏览器」的形态。
- **安装界面（2026-08-17）**：macOS dmg 带品牌版式——背景图
  `assets/brand/dmg-background.png` 由 `scripts/build_dmg_background.py` 生成
  （PyMuPDF 直绘，图标落点与 `make_dmg.sh` 的 Finder 版式严格同源），
  make_dmg.sh 里 Finder 脚本失败只降级为朴素版式、绝不断发布链。
  Windows NSIS 用 vendored 模板 `src-tauri/windows/installer.nsi`
  （上游 tauri-cli v2.11.4 + `TAVOTTO PATCH` 标注的最小补丁：去欢迎页 /
  极简进度 / 品牌配色；头图侧栏图走 tauri.conf.json 的 nsis.* 配置）。
  **@tauri-apps/cli 钉死在 2.11.4**——模板与打包器必须同源，升级 CLI 时
  取新模板重打补丁并同步 build_desktop.py / desktop-tauri.yml / nightly.yml
  （tests/test_nsis_template.py 看护四处版本一致与 BMP 形态）。

## 壳内多语言

**桌面壳自带一份文案**（`src-tauri/src/i18n.rs`）：原生菜单在 webview
起来之前就要建。改菜单文案要**改两处**；切语言只换显示文案，菜单项 id 与
加速键一个字节不动。splash/error 页在 `tauri://` 源下，两份文案内联、
语言由壳经 `?lang=` 带过去。首启（还没有 `menu-locale` 文件）菜单是默认档，
前端起来后重建——已知限制，见 docs/i18n.md。
