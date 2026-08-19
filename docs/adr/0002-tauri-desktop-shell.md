# ADR 0002：Tauri 2 桌面壳 + Python sidecar

日期：2026-08-17 ｜ 状态：已采纳（实验分支先行，与现有发行链并行验证）

## 背景

此前桌面形态 = PyInstaller 打包 Flask，启动后调 `webbrowser.open` 打开系统
浏览器。问题：不是真正的应用窗口（没有菜单/生命周期/单实例）、5089 固定端口、
`0.0.0.0`-adjacent 的「任何本地页面都能打 localhost API」攻击面、退出靠用户
自己关进程。

## 决定

### 为什么 Tauri 2（而不是 Electron / pywebview）

- 壳只做窗口 + 生命周期 + 菜单，业务全在现有 Flask + React——需要的是最薄的
  系统 WebView 壳。Tauri 2 用系统 WebView（WKWebView / WebView2），壳本体
  ~15MB；Electron 要再背一个 Chromium（+150MB）且与「sidecar 里已经有一个
  Python 运行时」叠加到不可接受。
- pywebview 把窗口进程和 Python 绑死，失去「壳崩了 sidecar 也能被收掉」的
  进程隔离，且菜单/单实例/更新器生态远弱于 Tauri。
- Tauri 自带 updater、单实例、窗口状态插件与 NSIS/dmg bundler，正好替掉
  我们手搓的那部分（Inno Setup 脚本、make_dmg.sh）。

### 为什么保留 Python sidecar（而不是把引擎移进 Rust）

渲染引擎的本体是「在常驻 matplotlib Figure 上做 override」，必须活在 Python
里；PyMuPDF 合成、AI 桥、注册表也全是既有 Python 资产。桌面化的目标是换壳，
不是重写引擎。前端继续由 sidecar 的 Flask 提供（`http://127.0.0.1:<port>`），
**不走 Tauri 的 frontendDist**——保证浏览器模式（`magplot` CLI/PyPI 安装）与
桌面模式跑的是同一份界面、同一套 API 语义。

## 进程模型与生命周期

```text
Tauri 壳（Magplot.app / Magplot.exe）
  │ spawn，stdin 管道保持打开
  ▼
magplot --desktop-sidecar（PyInstaller onedir，无 matplotlib）
  │ 现有 worker 协议（pool.py）
  ▼
matplotlib worker（用户/内置 Python，独立子进程）
```

- **启动**：壳生成 128-bit 随机 nonce → spawn sidecar（`--desktop-sidecar`）→
  **stdin 首行** JSON `{nonce, parent_pid}` → sidecar 绑 `127.0.0.1:0`（端口 0 =
  OS 分配，天然无「先查再绑」竞态，5089 被占也无感）→ 原子写握手文件
  （`MAGPLOT_DESKTOP_HANDSHAKE` 路径，内容仅 ready/port/pid 或 error，**无密钥**）
  → 壳读到 ready 后把窗口从 splash 导航到 `http://127.0.0.1:<port>/#dnonce=<nonce>`。
  失败则导航到内置 error.html（含日志路径）。
- **退出（正常）**：窗口关闭 / ⌘Q → Tauri `RunEvent::Exit` → 关 sidecar stdin →
  sidecar 收到 EOF：停 watcher → `pool.shutdown_all(wait=True)` 同步等 worker 退 →
  中断 AI 子进程 → 删握手文件 → 退出。壳限时（10s）等不到则 kill 兜底。
- **退出（壳崩溃/被 kill）**：stdin EOF 同样触发（这是选 stdin 而不是显式
  shutdown API 的原因——它把正常退出与异常退出合并成同一条可靠信号）；另有
  父 PID 监视（POSIX `getppid` 变化 / Windows `WaitForSingleObject`）兜底。
  实测 `kill -TERM` 壳后 sidecar 5 秒内自退，无孤儿。
- **单实例**：tauri-plugin-single-instance，第二次启动聚焦已有窗口，绝不再起
  一套后端。
- **窗口状态**：tauri-plugin-window-state 记忆大小/位置；最小 1024×680
  （三栏工作台断点下限）。

## 认证模型（一次性 bootstrap）

威胁模型：本机其他进程/浏览器页面对 `127.0.0.1:<port>` 的任意访问
（drive-by localhost 攻击、DNS rebinding）。

1. nonce 经 **stdin** 传入而不是环境变量——macOS/Linux 上同用户进程可读他进程
   env（`ps eww`），管道不可见。`MAGPLOT_DESKTOP_NONCE` env 仅作调试回退，
   读到立即 `os.environ.pop`（不让 worker/AI 子进程继承）。任务书原型写的是
   env 传递；实现改为 stdin-first 正是为满足其中「不暴露给其他进程」这条更硬
   的约束。
2. 壳把 nonce 放在首个 URL 的 **fragment**（不进 HTTP 请求行 → 不进任何日志）。
3. 前端启动代码（`web/src/main.tsx` → `lib/desktop.ts`）先清 fragment，再 POST
   `/api/desktop/bootstrap`；后端核对（constant-time）后**当场作废** nonce，
   签发进程内随机 token，落 `HttpOnly + SameSite=Strict` 会话 cookie。
   错误猜测不作废 nonce（否则任何本地进程可在真页面 bootstrap 前用错误值 DoS）。
4. 此后 `/api/*`、`/exports/*`、`/api/render`、SSE 一律凭 cookie（401 否则）；
   `/`、`/assets/*`、bootstrap 本身公开（不含用户数据，页面得先加载起来）。
   同时校验 Host（仅 `127.0.0.1:<port>` 一种写法）与 Origin。
5. **浏览器/CLI 模式完全不变**：钩子在 `MAGPLOT_DESKTOP_STATE` 缺席时直接放行，
   bootstrap 端点 404。

## 桌面/浏览器模式边界

| | 浏览器模式（`magplot`） | 桌面模式（`--desktop-sidecar`） |
|---|---|---|
| 端口 | 5089 顺延探测 | `127.0.0.1:0` OS 分配 |
| server | `Flask.app.run` | werkzeug `make_server`（可优雅 shutdown） |
| 打开方式 | `webbrowser.open` | 壳窗口导航，绝不开系统浏览器 |
| 认证 | 无（保持现状） | nonce → HttpOnly cookie + Host/Origin |
| updater | Python updater（GitHub Releases） | 完全停用，升级归 Tauri 层 |
| 退出 | 用户关进程 | 壳退出 → EOF → 全链路同步收尾 |

前端唯一的桌面感知点是 `web/src/lib/desktop.ts`（Tauri 检测、bootstrap、菜单
事件、原生目录选择、导出文件 reveal），组件不得直接 import `@tauri-apps/*`；
每个能力都有浏览器回退（vitest 看护）。

## WebView 安全边界

- 导航守卫：壳内页面只允许 shell 自带页（splash/error）与 sidecar 源的根路径
  `/`；同源其他路径（如 `/exports/x.pdf`）拒绝——导出文件走原生「在文件夹中
  显示」（`reveal_export` 命令，仅接受「目录 + 纯文件名」）；外部 http(s)/mailto
  一律交系统默认程序，WebView 永不加载外部网页。
- capability 最小化（`src-tauri/capabilities/main.json`）：远程上下文
  （`http://127.0.0.1:*`）只拿 `core:event:default` + `dialog:allow-open`，
  不开放 shell/fs/任意 opener。
- 菜单剪贴板项用预定义角色（macOS WKWebView 没有这些菜单角色时输入框里
  ⌘C/⌘V 完全失效）；撤销/重做是自定义项，事件转发给前端按焦点分派
  （文本框 → 原生 execCommand，画布 → 文档 undo 栈）。

## 打包（PyInstaller onedir，不用 onefile）

onefile 每次启动要把整个运行时解压到临时目录——科学栈体量下是数秒到数十秒的
冷启动税，且杀软最爱盯着它。保留 onedir（`packaging/magplot.spec` 原封不动），
Tauri 把整个 `dist/Magplot/` 目录作为资源打进壳
（`bundle.resources: {"../dist/Magplot": "sidecar/Magplot"}`）。既有边界全部
维持：sidecar 不含 matplotlib；`worker.py`/`manifest.py`/`overrides.py` 仍是
磁盘上的真 .py（外部解释器要按路径读）；wheel/sdist 不含 `src-tauri/`（hatchling
白名单本来就不收）；可写数据一律 `engine/config.data_dir()`。

sidecar 可执行解析顺序（`src-tauri/src/sidecar.rs`）：`MAGPLOT_SIDECAR_EXE`
（开发/排障）→ 打包资源 → 源码树 `.venv`（向上找 `pyproject.toml`）。

构建入口：`python scripts/build_desktop.py`（版本同步 → 前端 → PyInstaller →
Tauri bundler）。

## 平台范围

- **macOS**：`.app` + `.dmg`。签名/公证沿用已趟通的经验（全量 Mach-O 自内向外
  签、hardened runtime、notarytool + staple）；PyInstaller onedir 作为嵌套资源
  意味着签名必须继续「签**所有** Mach-O」，Tauri 的 `signingIdentity`
  只签壳本体。

  ~~**macOS 尚无内置科学运行时**——worker 仍按现有优先级找用户 Python，
  这是下一阶段的产品化缺口。~~
  **2026-08-18 更新：这个缺口已补上。** macOS `.app` 现在与 Windows 一样自带
  内置渲染 runtime（上游是 python-build-standalone 的 `install_only` 发行版，
  可重定位、逐个可签名），装完即可渲染，不依赖 Homebrew / Conda / 系统 Python。
  两个后果要记住：
  * 嵌套 Mach-O 从几十个变成五百多个，且全在 `Contents/Resources` 下，
    **`codesign --deep` 既签不到也验不出**（它们被当作*资源*封进签名）。
    签名与验收统一走 `scripts/codesign_macos.py`（读魔数、深度降序、逐个 verify
    并核对架构）。
  * `.app` 是签过名的，运行时**一个字节都不能往里写**，否则代码签名当场作废、
    下次启动报「应用已损坏」——worker 一律带 `-B` 起，字节码与 Matplotlib
    字体缓存改道到数据目录（`engine/runtime.child_args/child_env`）。

  仍然只发 **arm64**；Intel 目标在锁文件里标着 `shipped: false`，
  **没有构建过也没有冒烟过**，不得对外称「支持 Intel」。详见 docs/RELEASING.md。
- **Windows**：NSIS 安装包（替代 Inno Setup 的候选，旧链路暂不删）。内置
  CPython runtime（`packaging/runtime-lock.json` 那套）继续作为独立资源目录
  随包分发（在另一条在途改动里，见「与主工作树的边界」）。
- **Linux**：只保架构兼容（代码零平台假设、bundler 天然支持 deb/AppImage），
  本轮不构建不承诺。
- 不做 iOS/Android。

## 已知限制与回退路径

- 旧 PyInstaller 直发链路（`desktop.yml`：Inno Setup / make_dmg.sh）**原样保留**，
  Tauri 链路（`desktop-tauri.yml`）完成等价验证（含 Windows 真 exe 门禁）之前
  不删；任何时刻可回退到旧安装包。
  ——**2026-08-17 更新**：等价验证完成，Windows NSIS 已带内置渲染 runtime 并过
  `--expect-source bundled` 门禁；旧链于 v0.3.0 退役删除（含 Inno Setup 与
  免安装 zip），回退路径改为检出历史 tag 走旧链构建。
- ~~桌面壳的 Tauri updater 本轮只留了位置~~ ——**2026-08-18 已接**：
  `tauri-plugin-updater` + `tauri-plugin-process`，界面在「设置 → 检查更新」
  （桌面模式整段换成壳的更新器，Python updater 仍然停用）。清单
  `latest.json` 由 `scripts/make_updater_manifest.py` 在两条 matrix 腿都跑完
  之后合成。要点见下节。
- 画布级 ⌘C/⌘V 在桌面菜单预定义角色下的行为需人工回归一轮（文本框内已保证）；
  发现异常的回退方案是把剪贴板项换成自定义转发（同撤销/重做路径）。
- 双击 .magplot 项目包 / 文件关联未做。

## 应用内更新（2026-08-18）

桌面版的升级**全程留在软件里**：检查 → 下载（带进度）→ 安装 → 重启。
用户不再需要去 Releases 页面手动下载覆盖安装。

- **两条升级通道互斥**。浏览器 / pip / pipx 走 Python updater
  （`/api/update/*`，pip 装 wheel）；桌面壳走 Tauri updater（下载签名过的
  安装包就地替换）。后端在桌面模式把 `/api/update/*` 整个关掉，
  `checkUpdateOnStartup()` 按 `isDesktop()` 只查一条——两条同时插手会出现
  「一边说有新版、一边说不支持」。
- **前端唯一桌面感知点仍是 `web/src/lib/desktop.ts`**：
  `checkDesktopUpdate` / `installDesktopUpdate` / `relaunchDesktop`，
  每个都有浏览器回退（vitest 看护）。组件不 import `@tauri-apps/*`。
- **check 拿到的句柄要留住**。`installDesktopUpdate` 用的是上一次 check 的
  那个 `Update` 对象，没有句柄直接抛——不在安装那一刻偷偷补一次 check，
  否则用户看到的版本号与真正装上去的可能不是同一个。
- **签名**。更新包用 minisign 签名（与代码签名/公证是两回事）：私钥只在 CI
  的 `TAURI_SIGNING_PRIVATE_KEY` secret 里，公钥写死在 tauri.conf.json 的
  `plugins.updater.pubkey`。校验不过 `downloadAndInstall` 当场抛错。
  **没有配私钥时构建就地关掉 `createUpdaterArtifacts`**（否则打包器直接失败），
  并打一条 warning——安装包照发，只是这一版进不了自动更新。
- **macOS 的更新包必须在签名/公证之后重做**。`tauri build` 顺手打的那个
  `.app.tar.gz` 里装的是**还没签名**的 .app，更新器换上去之后 Gatekeeper 当场
  拦下，用户拿到一个打不开的应用。发行链里那一份是签完 + `stapler staple`
  之后重新 tar、重新 `tauri signer sign` 的。
- **macOS 只发 arm64**（sidecar 由 arm64 runner 上的 PyInstaller 打出来）。
  清单里因此**没有** `darwin-x86_64`——给 Intel 挂上同一个包，等于把一个装不上
  的更新推给他们，比「查不到更新」糟糕得多。
- **清单是单独一个 job**：两条 matrix 腿各只知道自己那一半，`latest.json`
  必须等两条都跑完才拼得出来。拼接逻辑在 `scripts/make_updater_manifest.py`
  （有包没签名、一个包都没有，都是硬错误——宁可不发清单，也不发一份装到
  一半才发现对不上的），看护 `tests/test_updater_manifest.py`。
