# src-tauri/ — Tauri 桌面壳规则

仓库级路由与不变量在根 `AGENTS.md`。架构与安全模型的完整版在
`docs/adr/0002-tauri-desktop-shell.md`，改动前先读。打包与内置 runtime 在
`packaging/AGENTS.md`；sidecar/引擎侧在 `src/tavotto/AGENTS.md`。

## 进程关系与安全边界（2026-08-17，与浏览器模式并行）

- **进程关系**：Tauri 壳（`src-tauri/`）→ spawn `tavotto --desktop-sidecar`
  （PyInstaller onedir，无 matplotlib）→ 现有 worker 协议。前端仍由 sidecar 的
  Flask 提供，**不走 Tauri frontendDist**——桌面与浏览器跑同一份界面。
- 会话认证与桌面/浏览器共用一道边界（ADR 0008），细节见
  `docs/rules/backend/session-auth.md`。
- **桌面模式差异收在 `src/tavotto/desktop.py`**：端口优先壳记住的那个
  （`app_config_dir()/desktop-port`，stdin 首行 `preferred_port`；占不到限时重试后
  退回 `127.0.0.1:0`；连续 3 次落空才改记，策略是 `sidecar/port_memory.rs` 的纯函数；
  字段与 Python 侧严格同源，ADR 0108——端口一变 localStorage 整个换一份）、
  werkzeug 线程 server（可优雅 shutdown）、nonce 走 **stdin 首行**
  （环境变量对同用户进程可见；桌面**不写**完整磁盘凭据，只写 scoped 只读的 handoff-status 文件（ADR 0008 修订），实例复用由壳的
  单实例 argv 转发负责）、握手文件（无密钥、原子写、退出清理）、
  stdin EOF + 父 PID 双路「壳没了就自杀」（`test_desktop_sidecar.py` 看护）。
- **外链（`<a target=_blank>` / `window.open`）全由壳在 Rust 侧交给系统浏览器**：`navigation_allowed` → `OpenExternal`
  与 `on_new_window` 两条原生路径，失败经 `open_external` 记日志。opener 插件必须 `open_js_links_on_click(false)`——
  它默认注入的 click 监听会先 `preventDefault` 再走 ACL 拒绝的 IPC，两条原生路径永远等不到（beta 0.19.0b2「支持 Tavotto」
  点了没反应的真根因，#1288 只修了 `window.open`）。capability 不给页面 `opener:*`；Rust 单测
  `the_opener_plugin_never_swallows_link_clicks` 看护。
- **前端唯一桌面感知点是 `web/src/lib/desktop.ts`**：组件不得直接 import
  `@tauri-apps/*`；每个能力都有浏览器回退（vitest 看护）。菜单事件 id 与
  `src-tauri/src/main.rs` 严格同源（`tavotto:menu`）。
- **Tauri 2 的 ACL 对应用自定义命令同样生效**：新增 `#[tauri::command]` 必须
  三处同步——`build.rs` 的 `AppManifest::commands`、`capabilities/` 里**该拿它的
  那个窗口**加 `allow-<命令名连字符化>`（主窗口 `main.json`，远程实例窗口
  `remote-*.json`）、`main.rs` 的 `generate_handler`。漏掉前两处
  invoke 会被**静默拒绝**（reveal_export「点了没反应」就是这么坏的）；
  失败路径不许吞——回退时把完整文件路径告诉用户。三处齐全的看护读结构不读子串
  （`tests/support/rustsrc.py`，枚举在 `tests/test_desktop_codex_button.py`）。
- **关窗询问闸**（issue #223，ADR 0002 的「关窗询问闸」一节）：
  `WindowEvent::CloseRequested` → `CloseGate` → 事件 `tavotto:close-requested`
  → 前端答 `hold`/`close`/`cancel`。三条别改坏：**默认不拦**（前端 arm 之后才拦，
  splash/error 页没有监听器）、**超时只针对「前端有没有接手」**——而「接手」有
  两种结局，`CloseAsk` 的四档存在正是为了让看门狗分得出「没人接手 / 说继续关 /
  **说取消**」（压成一个 bool 会让点了取消的窗口两秒后自己关掉）、
  **必须留看门狗**（没有它 = 一个关不掉的窗口；拦的唯一入口是 `hold_window()`，
  行为有 Rust 单测钉住，别退回「在源码里搜 token」那种空门禁）。
  ⌘Q 与系统注销不走这条路。两侧同源由 `tests/test_desktop_close_guard.py` 看护。
- **引擎监视、重启与退出标记**（ADR 0129）：握手后监视线程约 1 s `Sidecar::poll_exit()`（shutdown 取走 child 后回 `None` = 正常关闭）；
  意外退出写标记、发 `tavotto:engine-exited`、`current_engine_exit` 回读；`restart_engine`（仅主窗口，远程窗口**无**）与首启共用 `boot_engine`
  （新 nonce，`AppState.port` 可变）。`app_config_dir()/session-state.json` 只含版本 / 时间 / running|clean / 退出类型闭集，不上传；
  `take_previous_exit` 取走即清空。**绕过 `RunEvent::Exit` 的退出路径必须先写 clean**：`std::process::exit` 只许经 `e2e_exit`，
  `UpdaterBuilder` 只许经 `updater_builder()`（`on_before_exit`）——`exit_guard_tests.rs` 用 syn 扫 `src/` 全部生产 `.rs` 的 AST（不做 `use` 别名解析：`use` 里出现 std 的 process 段或导入 exit 即 hit，任何末段是 exit 的路径也是 hit；宏体遍历 TokenTree 只看 Ident；`use` 按叶子逐个 hit；允许名单列到具体叶子,与实际命中按多重集精确相等），`main.rs` 的结构测试钉出口函数内部先写 clean 的顺序；新增退出路径先加看护。
- **原生救援页**（ADR 0130，`src/rescue.rs` + `src/shell_report.rs` + `shell/error.html`）：sidecar 起不来时由壳自己检查修复版本、重新尝试、发送最小启动诊断。
  四条别改坏：① **只在「从未进入编辑态」时可用**——`RescueState.entered` 在 `boot_engine` 导航到应用页**之前**置位、永不复位，置位后七条 `rescue_*` 命令一律拒绝
  （`editing_state_unknown`），不靠超时推断干净；② **只许主窗口的壳自带 `error.html` 调用**（`rescue::decide` 查 label + 当前 URL，ACL 之外再查一道）；
  capability 是独立的 `rescue-page.json`（`local:true`、无 remote、`windows:["main"]`），`main.json` 与 `remote-*.json` 不得出现 `allow-rescue-*`；
  ③ **更新走同一个协调器**（同一个 `UpdateState`，安装带 `expected_version`，对不上 `candidate_changed`）；
  ④ **重新尝试 = 重启整个应用**（收 sidecar → 写 clean → `restart()`），不做同进程重试。shell 报告只含闭集字段（`STAGES` / `CODES`），不收 `msg` / 日志路径 / 环境变量；
  **只有 `rescue_diag_send` 联网**，由用户单独点击触发；端点是编译期常量，环境变量只能关。协议常量、失败码、上传地址向量、包内容以
  `tests/golden/diag_shell_report.json` 为唯一出处（Rust 与 `tests/test_diag_shell_contract.py` 各读）。发行边界：只能救援已带此功能且壳能运行的客户端。
- **macOS 全屏收起顶栏让位**（`window_chrome.rs`）：主信号是 `fullscreen_observer.rs` 听的 NSWindow will/did 全屏通知（will-exit 立刻发 false、did-enter 才发 true：红绿灯在屏上时绝不收回占位；`Resized` 只作兜底且观察者在位时只许报 false）；`FullscreenTracker` 去重，真变了才向主窗口发
  `tavotto:fullscreen`（`{fullscreen}`）；`current_fullscreen` 回读防事件早于监听（仅主窗口，ACL 三处同步）。
  前端 `useWindowFullscreen`（非 `overlay` 窗框恒 false）让 TopBar 撤掉 `TRAFFIC_LIGHTS_INSET`。
  事件名 / 命令 / ACL 由 `tests/test_desktop_window_chrome.py` 看护。
- **Windows 主窗口无系统标题栏**（`window_chrome.rs`，窗框 `menu-button`，2026-10-21）：非 macOS 的主窗口
  `decorations(false)`，与 `chrome()` 同一个 cfg；最小化 / 最大化 / 关闭由页面 `WindowControls`（`main.tsx` 单独一个根，
  崩溃页 / 会话失败页也在）画，壳自带的 splash / error 页各内联一组；关闭走 `close()` 照旧进关窗询问闸。窗口命令只在
  `main.json`，远程实例窗口维持系统标题栏。看护同上。
- **主页拖放拿真实路径**（ADR 0092）：`disable_drag_drop_handler()` 必须留着（装上 Tauri 的处理器
  页面 HTML5 拖放整片失效）；macOS 上 `native_drop.rs` 只旁听 `performDragOperation:` 读路径、发
  `tavotto:file-drop`、再调原实现。路径分派只在 `drop_paths::classify`（绝对 + canonicalize，Rust 单测），
  视图分派在前端（只有主页订阅）；两侧同源由 `tests/test_desktop_file_drop.py` 看护，「什么算脚本」与页面
  `scriptImport.ts` 同源由 `tests/golden/drop_script_rule.json`（两侧各读）看护。
- **远程实例窗口**（ADR 0105）：label `remote`，加载 `ssh -L` 转发过来的服务器引擎。
  `incognito(true)`（cookie 不按端口隔离，共用存储会顶掉本机会话）+ 注入
  `REMOTE_WINDOW_MARKER`（前端 `isDesktop()` 据它为假，本机文件类能力走浏览器回退）+
  不装 native_drop。权限是闭集（`capabilities/remote-*.json`：只听事件不发、关窗询问、
  壳自带页面的 `connect_remote`），**不许往里加本机文件类权限**；关窗闸按窗口各一道，
  菜单发给聚焦窗口。看护 `tests/test_desktop_remote.py`。
- 桌面交接契约 argv `--open <目录> [--stem <stem>]`：生产者唯一
  `handoff.desktop_argv()`，消费者唯一 `src-tauri/src/main.rs::parse_open_args()`，
  两侧各有单测，改一边必须同步另一边（完整交接语义见
  `docs/rules/backend/external-handoff.md`）。
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
  `scripts/make_updater_manifest.py` 在三条 matrix 腿（macOS arm64 / macOS x86_64 / Windows，ADR 0076）都跑完后合成
  （少了它壳永远显示「已是最新」而 CI 全绿）。细节见 ADR 0002 末节。
- 桌面模式下 Python updater 停用（升级归 Tauri 层），`/api/update/*` 回
  禁用响应；浏览器模式照旧。
- **来源编排在 `src/app_update.rs`（ADR 0121）**：国内 COS 镜像 + GitHub 两个来源，顺序由时区 / 系统语言 / 界面语言
  的**唯一**判据 `source_order` 定；前端只调 `desktop_update_check` / `desktop_update_install` 两个命令，
  `capabilities/main.json` **没有** `updater:*`（webview 绕不过编排）。镜像地址是该文件里唯一的常量
  `MIRROR_MANIFEST_URL`，`tauri.conf.json` 的 endpoints 仍只有 GitHub 一条（发布链读它）。三条别改坏：
  ① 第一个**答上来**的来源说「无更新」就是最终答案，不再问另一个；② 下载失败（网络 / 非 2xx / 验签 / 停滞）
  换来源**重新 check + 重新下**，且回退来源必须报同一个版本；③ 验签永远用内置公钥，来源不影响信任。
  e2e 触发口 `TAVOTTO_E2E_UPDATE_SOURCE=mirror|github` 钉死单来源且不回退。
  看护：`app_update.rs` 的顺序单测 + `app_update_flow_tests.rs`（本地假 HTTP 服务 + 测试密钥签名的包，从不调用 `apply`）。

## 构建、验收与安装界面

- 构建：`python scripts/build_desktop.py`；验收：`python scripts/smoke_desktop.py
  --sidecar dist/Tavotto/Tavotto`（真产物全链路：认证/项目/渲染/导出/退出无孤儿）。
  CI 在 `desktop-tauri.yml`——v0.3.0 起是唯一桌面发行链（旧 `desktop.yml`/
  Inno Setup/免安装 zip 已退役删除，git 可找回）；Windows NSIS 自带内置渲染
  runtime，桌面产物一律真窗口、不再有「启动后开浏览器」的形态。
- **安装界面（2026-08-17）**：macOS dmg 带品牌版式——背景图
  `assets/brand/dmg-background.png` 由 `scripts/build_dmg_background.py` 生成
  （维护者脚本用 PyMuPDF 直绘——它不在应用闭包里，要 `pip install -e '.[legacy-pymupdf]'`；图标落点与 `make_dmg.sh` 的 Finder 版式严格同源），
  make_dmg.sh 里 Finder 脚本失败只降级为朴素版式、绝不断发布链。
  Windows NSIS 用 vendored 模板 `src-tauri/windows/installer.nsi`
  （上游 tauri-cli v2.11.4 + `TAVOTTO PATCH` 标注的最小补丁：去欢迎页 /
  极简进度 / 品牌配色；头图侧栏图走 tauri.conf.json 的 nsis.* 配置）。
  **首次安装是三页：选安装位置 → 进度 → 完成**（2026-09-23 起；此前目录页
  恒被跳过）。目录页只在「首次、交互式」安装出现——卸载项里已有安装、
  `/UPDATE`（应用内更新）、`/P` 一律跳过：覆盖安装换位置会在老目录留一份
  没人管的副本。默认值 `%LOCALAPPDATA%\Tavotto`（以前装过就预填老路径）；
  离开页面时先验写权限（currentUser + asInvoker，选 Program Files 当场拦下）。
  自选路径靠安装清单 + HKCU `InstallLocation` 被外部程序发现，不在惯例位置里。
  **@tauri-apps/cli 钉死在 2.11.4**——模板与打包器必须同源，升级 CLI 时
  取新模板重打补丁并同步 build_desktop.py / desktop-tauri.yml / nightly.yml
  （tests/test_nsis_template.py 看护四处版本一致与 BMP 形态）。

- **调试符号与小型转储**：`[profile.release]` 的 `debug = "line-tables-only"` + `split-debuginfo = "packed"` 让 macOS 产出 .dSYM、
  二进制仍剥光；MSVC 上 `strip` 会吞 PDB，Windows 腿由 `desktop-tauri.yml` 用 `CARGO_PROFILE_RELEASE_STRIP=none` 覆盖
  （`workerd/Cargo.toml` 同）。符号另存为 `debug-symbols-*` artifact，**不进安装包、不进更新清单**（`docs/RELEASING.md`「调试符号」）。
  Windows 小型转储由壳自写（`crash_dump.rs`：`main()` 第一句装未处理异常过滤器，过滤器里不分配堆、不加锁；
  落到诊断包 `crash_dirs()` 的 `%LOCALAPPDATA%\CrashDumps\Tavotto.exe.<pid>.dmp`，启动时轮转留 5 份，崩溃后短暂可有 6 份）。
  **不写 WER LocalDumps 注册表**（HKCU 是否生效无权威说法，HKLM 要管理员）。看护：模块内单测 +
  `main_installs_the_filter_as_its_first_statement`；「真崩一次」只在 `nightly.yml` windows-install 跑（PR CI 没有 Windows cargo test）。

## 壳内多语言

**桌面壳自带一份文案**（`src-tauri/src/i18n.rs`）：原生菜单在 webview
起来之前就要建。改菜单文案要**改两处**；切语言只换显示文案，菜单项 id 与
加速键一个字节不动。splash/error 页在 `tauri://` 源下，两份文案内联、
语言由壳经 `?lang=` 带过去。首启（还没有 `menu-locale` 文件）菜单是默认档，
前端起来后重建——已知限制，见 docs/i18n.md。
**壳内文案不手写产品名**：`build.rs` 从 `brand.ts` / `engine/brand.py` 编译期注入
`TAVOTTO_PRODUCT_NAME` / `TAVOTTO_DIST_NAME`，`ShellText` 里写 `concat!(env!(…), …)`，
`connect.html` 由壳经 `?product=` / `?dist=` 带名字（`tests/test_desktop_i18n.py` 看护；
splash / error 页与窗口标题是尚未收口的旧写法）。
