# 更新通道（桌面归壳）

> 原文出自 `src-tauri/AGENTS.md`「更新通道（桌面归壳，2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src-tauri/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `src-tauri/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：桌面关掉 `/api/update/*`、只查一条通道；webview 不拿 `updater:*`；第一个答上来的来源说「无更新」即终局，下载失败换来源重新 check；验签永远用内置公钥；macOS 更新包签名公证后重做

<!-- agents-route
sheet: src-tauri/AGENTS.md
order: 60
改到: `src-tauri/src/app_update.rs`、`tauri.conf.json`、`capabilities/main.json`、`scripts/make_updater_manifest.py`、`MIRROR_MANIFEST_URL`
主题: 桌面更新：tauri-plugin-updater 与来源编排（ADR 0121） → `update-channel.md`
必守要点: 桌面关掉 `/api/update/*`；webview 不拿 `updater:*`；验签用内置公钥；首个来源说「无更新」即终局；大陆下载先 GitHub
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
  换来源**重新 check + 重新下**，且回退来源必须报同一个版本；下载**慢**（`SLOW_RULE`）时先边下边试探下一个来源（同版本），
  实测更快（`should_switch`）才换、否则丢掉试探那一路留在原来源，每次更新只试探一次；③ 验签永远用内置公钥，来源不影响信任。
  （②的慢速试探只适用于非大陆环境，大陆见⑤。）
  e2e 触发口 `TAVOTTO_E2E_UPDATE_SOURCE=mirror|github` 钉死单来源且不回退。
  ④ **后台下载（ADR 0121 §八，2026-11-04）**：偏好「后台下载新版本」默认开（后端用户配置 `updates.auto_download`，不放 localStorage）。
  开着时前端启动查到新版即调 `desktop_update_download`（走同一个 `fetch`，只下载 + 验签，包进 `UpdateState.ready` 槽，**不安装**）；
  **安装只发生在「现在重启」（`desktop_update_install` 取槽里同版本的包、不再请求包地址）或退出时（`RunEvent::Exit` 里排在
  `shutdown_sidecar()` + `mark_clean()` 之后的 `install_ready_on_exit`）**——两个平台一样，macOS 也不即时替换 .app（sidecar / worker 会按需
  从包里载入代码，运行中换包会混用新旧版本）。后台失败静默；`run_check` 查到别的版本 / 无新版就作废旧包；下载期间 `pending` 锁被持有，
  前端用 `desktopPhase` 挡住再次检查，不报 `busy`。新命令只许主窗口（三处 ACL 同步）。
  ⑤ **检查与下载的来源拆开（ADR 0121 §九，2026-11-05）**：检查不变（大陆镜像先答）；**大陆环境**（`Pending::mainland`＝原始顺序镜像在前，冷却不改它，单来源钉死不算）
  下载先走 GitHub，唯一判据 `plan_download` / `plan_for`，`fetch` 按 `Plan { seq, pace }` 执行：后台 `Steady`（慢不换来源，只有失败 / 停滞才换）；
  手动 `Trial`（`TRIAL_RULE`：GitHub 8 s 没字节、或出字节后估算超过 2 分钟 → 转镜像，镜像一出字节就换，镜像不行 GitHub 继续）。
  后台改走镜像的条件＝同一版本后台走 GitHub 连续失败 3 次，或新版发布超过 3 天且试过至少一次，跨重启记在 `app_config_dir()/update-source.json`
  （`update_source_memory.rs`；发布时间取清单 `pub_date`，取不到用本机首次见到的时刻；读写失败静默）。与 check 不同来源的来源一律重新 check 且必须同版本，
  别的版本 / 没有该版本＝`version_mismatch` 直接用镜像，不悄悄换版本、也不计入失败。非大陆环境一个字不变。
  ⑥ **第三个下载来源：国内自建边缘节点（ADR 0121 §十，2026-11-11）**：`Source::Edge`，清单 `EDGE_MANIFEST_URL`（`up.tavotto.cn`）；**只在大陆·后台**插进 `plan_for`
  （`[GitHub, 节点, 镜像]`；改道后 `[节点, 镜像, GitHub]`，仍 `Steady`），check 顺序 / 手动下载 / 非大陆一个字不变。节点同样过 `decide_updater`（根换成节点根）+ pin host；
  404 或连不上 = 来源不可用、换下一个（服务器删清单即关停，客户端不发版）；失败不触发镜像 `Cooldown`；`outcome_of` 把节点赢与镜像赢同样对待。界面文案与镜像共用，不出现「节点」。
  看护：`app_update.rs` 的顺序单测 + `update_source_memory.rs` 单测、`app_update_flow_tests.rs`（本地假 HTTP 服务 + 测试密钥签名的包，从不调用 `apply`；后台下载用 `install_pending` 注入「装」的替身）、
  `main.rs` 的 `run_event_exit_writes_clean_after_the_sidecar_is_gone`（退出时装包排在 clean 之后、只有一处）。
